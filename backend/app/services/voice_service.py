"""Dịch vụ giọng nói Backend (Role Voice).

STT (V01 & A21): chuyển audio người dùng chủ động gửi thành chữ.
Nhà cung cấp chính: Gemini 3.5 Transcribe (chế độ ``smart``) — chọn sau khảo sát STT tiếng Việt
ngày 01/11/2026 (đúng ngày/giờ/thực thể tốt nhất, không bịa câu khi audio im lặng). Dự phòng khi
Backend lỗi/mất mạng nằm ở Mobile (``speech_to_text`` on-device), không nằm trong file này.

TTS (check-in UF5, lời nhắc UF3, động viên UF6): đọc câu của trợ lý thành audio WAV.
Nhà cung cấp chính: Gemini 3.8 Flash(-Lite) TTS; dự phòng ở Mobile (``flutter_tts`` on-device).
Free tier chỉ ~10 lượt/ngày -> có bộ nhớ đệm (cache) để câu lặp lại không tốn lượt gọi.
Chưa có endpoint: API Sheet chưa có dòng cho TTS, chờ Backend Lead chốt hợp đồng.

Quy tắc bắt buộc (AGENTS.md §2 UF4, doc Backend UF4/A21):
- Chỉ xử lý audio do người dùng CHỦ ĐỘNG gửi. Không gọi STT cho bản ghi UF4 trong phiên.
- KHÔNG log nội dung audio, transcript hay câu TTS — log chỉ có mã lỗi, nhà cung cấp, thời gian.
- File upload lên Google Files API phải được xóa ngay sau khi chép (kể cả khi lỗi).
- Nhà cung cấp, model, chế độ, ngôn ngữ, giọng, timeout là tham số cấu hình (``app.core.config``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import time
import wave
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import PurePath
from typing import Any, Literal, Protocol

from google import genai

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)

# Định dạng audio Gemini Transcribe chấp nhận (docs ai.google.dev/gemini-api/docs/transcribe).
_EXTENSION_TO_MIME: dict[str, str] = {
    ".m4a": "audio/m4a",
    ".mp4": "audio/m4a",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}
_MIME_ALIASES: dict[str, str] = {
    "audio/x-m4a": "audio/m4a",
    "audio/mp4": "audio/m4a",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/mpeg": "audio/mp3",
}
SUPPORTED_MIME_TYPES: frozenset[str] = frozenset(_EXTENSION_TO_MIME.values())


class SttErrorCode(StrEnum):
    """Mã lỗi chuẩn của adapter STT — Router đổi sang Error Envelope của Sheet."""

    NO_SPEECH = "NO_SPEECH"  # Audio không có tiếng nói / nhà cung cấp trả chuỗi rỗng
    UNSUPPORTED_AUDIO = "UNSUPPORTED_AUDIO"  # Định dạng không hỗ trợ hoặc file hỏng
    AUDIO_TOO_LARGE = "AUDIO_TOO_LARGE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"  # Chưa cấu hình key / key sai / bị từ chối quyền
    RATE_LIMITED = "RATE_LIMITED"  # HTTP 429
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"  # Lỗi mạng / 5xx / lỗi không xác định


_RETRYABLE_CODES = frozenset(
    {
        SttErrorCode.RATE_LIMITED,
        SttErrorCode.TIMEOUT,
        SttErrorCode.PROVIDER_ERROR,
    }
)


class SttError(Exception):
    """Lỗi chuyển giọng nói thành chữ. Không chứa nội dung audio/transcript."""

    def __init__(self, code: SttErrorCode, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else str(code))
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES


@dataclass(frozen=True, slots=True)
class SttResult:
    """Kết quả chép lời. ``text`` là bản do nhà cung cấp trả về (chế độ smart đã bỏ từ đệm)."""

    text: str
    provider: str
    latency_ms: int


class SttProvider(Protocol):
    """Giao diện chung cho mọi nhà cung cấp STT (thật hoặc giả lập cho test)."""

    name: str

    async def transcribe(self, audio: bytes, mime_type: str) -> str: ...


def resolve_mime_type(content_type: str | None, filename: str | None) -> str:
    """Xác định MIME type audio từ header của file upload, ưu tiên đuôi file nếu header chung chung."""
    normalized = (content_type or "").split(";")[0].strip().lower()
    normalized = _MIME_ALIASES.get(normalized, normalized)
    if normalized in SUPPORTED_MIME_TYPES:
        return normalized

    suffix = PurePath(filename or "").suffix.lower()
    if suffix in _EXTENSION_TO_MIME:
        return _EXTENSION_TO_MIME[suffix]

    raise SttError(SttErrorCode.UNSUPPORTED_AUDIO, f"content_type={normalized or 'none'}")


def _status_code_of(exc: Exception) -> int | None:
    """Lấy HTTP status từ lỗi SDK (Files API dùng ``code``, Interactions API dùng ``status_code``)."""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def map_provider_exception(exc: Exception) -> SttError:
    """Đổi lỗi của SDK nhà cung cấp thành ``SttError`` chuẩn (không lộ nội dung request)."""
    if isinstance(exc, SttError):
        return exc
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return SttError(SttErrorCode.TIMEOUT, type(exc).__name__)

    status = _status_code_of(exc)
    if status == 429:
        return SttError(SttErrorCode.RATE_LIMITED, "http_429")
    if status in (401, 403):
        return SttError(SttErrorCode.PROVIDER_UNAVAILABLE, f"http_{status}")
    if status in (400, 413, 415, 422):
        return SttError(SttErrorCode.UNSUPPORTED_AUDIO, f"http_{status}")
    if status is not None:
        return SttError(SttErrorCode.PROVIDER_ERROR, f"http_{status}")
    return SttError(SttErrorCode.PROVIDER_ERROR, type(exc).__name__)


class GeminiSttProvider:
    """Gemini 3.5 Transcribe qua Gemini Developer API (API key AI Studio).

    Luồng: upload audio lên Files API -> ``interactions.create`` -> đọc ``output_text`` -> xóa file.
    Docs chỉ mô tả cách gửi qua Files API nên chưa dùng inline bytes (cần đo lại nếu muốn giảm trễ).
    """

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        mode: Literal["smart", "verbatim"],
        language_code: str,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._mode = mode
        self._language_code = language_code
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if not self._api_key:
            raise SttError(SttErrorCode.PROVIDER_UNAVAILABLE, "missing_gemini_api_key")
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _transcription_config(self) -> dict[str, Any]:
        # Docs: verbatim cấu hình dạng object, smart cấu hình dạng chuỗi.
        mode: str | dict[str, str] = "smart" if self._mode == "smart" else {"type": "verbatim"}
        return {"language_codes": [self._language_code], "mode": mode}

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        client = self._get_client()
        uploaded = None
        try:
            uploaded = await client.aio.files.upload(
                file=io.BytesIO(audio),
                config={"mime_type": mime_type},
            )
            interaction = await client.aio.interactions.create(
                model=self._model,
                input=[
                    {
                        "type": "audio",
                        "uri": uploaded.uri,
                        "mime_type": uploaded.mime_type or mime_type,
                    }
                ],
                generation_config={"transcription_config": self._transcription_config()},
            )
            return (getattr(interaction, "output_text", None) or "").strip()
        except Exception as exc:
            raise map_provider_exception(exc) from exc
        finally:
            if uploaded is not None and uploaded.name:
                await self._delete_quietly(client, uploaded.name)

    @staticmethod
    async def _delete_quietly(client: genai.Client, file_name: str) -> None:
        """Xóa file đã upload; lỗi xóa chỉ ghi cảnh báo (Google tự hết hạn file sau một thời gian)."""
        try:
            await client.aio.files.delete(name=file_name)
        except Exception as exc:
            logger.warning("Không xóa được file STT tạm trên Gemini (%s).", type(exc).__name__)


class FakeSttProvider:
    """Nhà cung cấp giả lập cho test/dev: không gọi mạng, không tốn quota."""

    name = "fake"

    def __init__(self, text: str = "Viết bản nháp báo cáo môn học") -> None:
        self._text = text

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        return self._text


class VoiceService:
    """Điều phối STT: kiểm đầu vào, áp timeout, chuẩn hóa lỗi và kết quả."""

    def __init__(
        self,
        provider: SttProvider,
        *,
        timeout_seconds: float,
        max_audio_bytes: int,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_audio_bytes = max_audio_bytes

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> SttResult:
        """Chuyển audio thành chữ. Ném ``SttError`` nếu không có chữ hợp lệ để đưa sang A01."""
        if not audio:
            raise SttError(SttErrorCode.UNSUPPORTED_AUDIO, "empty_audio")
        if len(audio) > self._max_audio_bytes:
            raise SttError(SttErrorCode.AUDIO_TOO_LARGE, f"bytes={len(audio)}")
        mime_type = resolve_mime_type(content_type, filename)

        started = time.perf_counter()
        try:
            text = await asyncio.wait_for(
                self._provider.transcribe(audio, mime_type),
                timeout=self._timeout_seconds,
            )
        except SttError as exc:
            self._log_failure(exc, started)
            raise
        except Exception as exc:
            mapped = map_provider_exception(exc)
            self._log_failure(mapped, started)
            raise mapped from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        cleaned = (text or "").strip()
        if not cleaned:
            no_speech = SttError(SttErrorCode.NO_SPEECH, "empty_transcript")
            self._log_failure(no_speech, started)
            raise no_speech

        logger.info("STT thành công: provider=%s latency_ms=%d", self._provider.name, latency_ms)
        return SttResult(text=cleaned, provider=self._provider.name, latency_ms=latency_ms)

    def _log_failure(self, exc: SttError, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "STT thất bại: provider=%s code=%s detail=%s elapsed_ms=%d",
            self._provider.name,
            exc.code,
            exc.detail,
            elapsed_ms,
        )


def build_voice_service(app_settings: Settings | None = None) -> VoiceService:
    """Tạo VoiceService theo cấu hình ``VOICE_STT_PROVIDER``."""
    cfg = app_settings or settings
    provider: SttProvider
    if cfg.VOICE_STT_PROVIDER == "fake":
        provider = FakeSttProvider()
    else:
        provider = GeminiSttProvider(
            api_key=cfg.GEMINI_API_KEY.strip() if cfg.has_gemini_key else "",
            model=cfg.GEMINI_STT_MODEL,
            mode=cfg.GEMINI_STT_MODE,
            language_code=cfg.VOICE_STT_LANGUAGE,
        )
    return VoiceService(
        provider,
        timeout_seconds=cfg.VOICE_STT_TIMEOUT_SECONDS,
        max_audio_bytes=cfg.VOICE_MAX_AUDIO_BYTES,
    )


@lru_cache(maxsize=1)
def get_voice_service() -> VoiceService:
    """Singleton VoiceService dùng chung cho Router (giữ một Gemini client duy nhất)."""
    return build_voice_service()


# =============================================================================
# TTS — đọc câu của trợ lý thành audio
# =============================================================================


class TtsErrorCode(StrEnum):
    """Mã lỗi chuẩn của TTS — Mobile dựa vào đây để chuyển sang ``flutter_tts`` hoặc chỉ hiện chữ."""

    EMPTY_TEXT = "EMPTY_TEXT"
    TEXT_TOO_LONG = "TEXT_TOO_LONG"
    INVALID_REQUEST = "INVALID_REQUEST"  # Nhà cung cấp từ chối nội dung/tham số (HTTP 400/422)
    EMPTY_AUDIO = "EMPTY_AUDIO"  # Gọi thành công nhưng không có audio trả về
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


_TTS_RETRYABLE = frozenset(
    {TtsErrorCode.RATE_LIMITED, TtsErrorCode.TIMEOUT, TtsErrorCode.PROVIDER_ERROR}
)

_STT_TO_TTS_CODE: dict[SttErrorCode, TtsErrorCode] = {
    SttErrorCode.RATE_LIMITED: TtsErrorCode.RATE_LIMITED,
    SttErrorCode.TIMEOUT: TtsErrorCode.TIMEOUT,
    SttErrorCode.PROVIDER_UNAVAILABLE: TtsErrorCode.PROVIDER_UNAVAILABLE,
    SttErrorCode.UNSUPPORTED_AUDIO: TtsErrorCode.INVALID_REQUEST,
}


class TtsError(Exception):
    """Lỗi đọc câu thành audio. Không chứa nội dung câu."""

    def __init__(self, code: TtsErrorCode, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else str(code))
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in _TTS_RETRYABLE


def map_tts_exception(exc: Exception) -> TtsError:
    """Đổi lỗi SDK sang ``TtsError`` (dùng chung cách phân loại HTTP status với STT)."""
    if isinstance(exc, TtsError):
        return exc
    stt_error = map_provider_exception(exc)
    return TtsError(
        _STT_TO_TTS_CODE.get(stt_error.code, TtsErrorCode.PROVIDER_ERROR), stt_error.detail
    )


@dataclass(frozen=True, slots=True)
class TtsResult:
    """Audio đã tổng hợp. ``cached=True`` nghĩa là lấy từ bộ nhớ đệm, không tốn lượt gọi."""

    audio: bytes
    mime_type: str
    provider: str
    latency_ms: int
    cached: bool


class TtsProvider(Protocol):
    """Giao diện chung cho mọi nhà cung cấp TTS."""

    name: str

    @property
    def cache_key(self) -> str:
        """Định danh cấu hình (model/giọng/phong cách) — đổi cấu hình thì cache cũ không dùng lại."""
        ...

    async def synthesize(self, text: str) -> bytes: ...


class GeminiTtsProvider:
    """Gemini 3.8 Flash(-Lite) TTS qua Gemini Developer API. Trả WAV 24 kHz mono 16-bit."""

    name = "gemini"

    def __init__(self, *, api_key: str, model: str, voice: str, style: str) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._style = style.strip()
        self._client: genai.Client | None = None

    @property
    def cache_key(self) -> str:
        return f"{self.name}|{self._model}|{self._voice}|{self._style}"

    def _get_client(self) -> genai.Client:
        if not self._api_key:
            raise TtsError(TtsErrorCode.PROVIDER_UNAVAILABLE, "missing_gemini_api_key")
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _text_content(self, text: str) -> dict[str, Any]:
        content: dict[str, Any] = {"type": "text", "text": text}
        if self._style:
            # Docs: phong cách đọc đặt ở annotation speech_metadata, KHÔNG viết chỉ dẫn vào text.
            content["annotations"] = [{"type": "speech_metadata", "style": self._style}]
        return content

    async def synthesize(self, text: str) -> bytes:
        client = self._get_client()
        try:
            interaction = await client.aio.interactions.create(
                model=self._model,
                input=[{"type": "user_input", "content": [self._text_content(text)]}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": self._voice}]},
            )
        except Exception as exc:
            raise map_tts_exception(exc) from exc

        data = getattr(getattr(interaction, "output_audio", None), "data", None)
        if not data:
            raise TtsError(TtsErrorCode.EMPTY_AUDIO, "no_output_audio")
        return base64.b64decode(data)


def _silent_wav(duration_ms: int = 200, sample_rate: int = 24_000) -> bytes:
    """WAV im lặng hợp lệ (mono 16-bit) cho FakeTtsProvider."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * (sample_rate * duration_ms // 1000))
    return buffer.getvalue()


class FakeTtsProvider:
    """Nhà cung cấp giả lập cho test/dev: trả WAV im lặng, không gọi mạng, không tốn quota."""

    name = "fake"
    cache_key = "fake"

    async def synthesize(self, text: str) -> bytes:
        return _silent_wav()


class TtsService:
    """Điều phối TTS: kiểm câu, tra bộ nhớ đệm, áp timeout, chuẩn hóa lỗi."""

    MIME_TYPE = "audio/wav"

    def __init__(
        self,
        provider: TtsProvider,
        *,
        timeout_seconds: float,
        max_chars: int,
        cache_size: int,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_chars = max_chars
        self._cache_size = cache_size
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def _cache_key(self, text: str) -> str:
        raw = f"{self._provider.cache_key}\n{text}".encode()
        return hashlib.sha256(raw).hexdigest()

    def _remember(self, key: str, audio: bytes) -> None:
        if self._cache_size <= 0:
            return
        self._cache[key] = audio
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def synthesize(self, text: str) -> TtsResult:
        """Đọc câu thành WAV. Ném ``TtsError`` để Mobile chuyển sang ``flutter_tts``/chỉ hiện chữ."""
        cleaned = " ".join((text or "").split())
        if not cleaned:
            raise TtsError(TtsErrorCode.EMPTY_TEXT)
        if len(cleaned) > self._max_chars:
            raise TtsError(TtsErrorCode.TEXT_TOO_LONG, f"chars={len(cleaned)}")

        key = self._cache_key(cleaned)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return TtsResult(cached, self.MIME_TYPE, self._provider.name, 0, cached=True)

        started = time.perf_counter()
        try:
            # Lưu ý: khi hết quota, google-genai có thể tự thử lại trong im lặng -> chỉ thấy TIMEOUT.
            audio = await asyncio.wait_for(
                self._provider.synthesize(cleaned), timeout=self._timeout_seconds
            )
        except TtsError as exc:
            self._log_failure(exc, started)
            raise
        except Exception as exc:
            mapped = map_tts_exception(exc)
            self._log_failure(mapped, started)
            raise mapped from exc

        if not audio:
            empty = TtsError(TtsErrorCode.EMPTY_AUDIO, "empty_bytes")
            self._log_failure(empty, started)
            raise empty

        latency_ms = int((time.perf_counter() - started) * 1000)
        self._remember(key, audio)
        logger.info(
            "TTS thành công: provider=%s latency_ms=%d bytes=%d",
            self._provider.name,
            latency_ms,
            len(audio),
        )
        return TtsResult(audio, self.MIME_TYPE, self._provider.name, latency_ms, cached=False)

    def _log_failure(self, exc: TtsError, started: float) -> None:
        logger.warning(
            "TTS thất bại: provider=%s code=%s detail=%s elapsed_ms=%d",
            self._provider.name,
            exc.code,
            exc.detail,
            int((time.perf_counter() - started) * 1000),
        )


def build_tts_service(app_settings: Settings | None = None) -> TtsService:
    """Tạo TtsService theo cấu hình ``VOICE_TTS_PROVIDER``."""
    cfg = app_settings or settings
    provider: TtsProvider
    if cfg.VOICE_TTS_PROVIDER == "fake":
        provider = FakeTtsProvider()
    else:
        provider = GeminiTtsProvider(
            api_key=cfg.GEMINI_API_KEY.strip() if cfg.has_gemini_key else "",
            model=cfg.GEMINI_TTS_MODEL,
            voice=cfg.GEMINI_TTS_VOICE,
            style=cfg.GEMINI_TTS_STYLE,
        )
    return TtsService(
        provider,
        timeout_seconds=cfg.VOICE_TTS_TIMEOUT_SECONDS,
        max_chars=cfg.VOICE_TTS_MAX_CHARS,
        cache_size=cfg.VOICE_TTS_CACHE_SIZE,
    )


@lru_cache(maxsize=1)
def get_tts_service() -> TtsService:
    """Singleton TtsService (giữ cache và một Gemini client dùng chung)."""
    return build_tts_service()
