"""Dịch vụ giọng nói Backend (Role Voice — V01 & A21): chuyển audio người dùng chủ động gửi thành chữ.

Nhà cung cấp chính: Gemini 3.5 Flash-Lite inline audio (chế độ ``smart``) với định tuyến theo
mục đích (riêng ``uf5_checkin`` ưu tiên Groq Whisper cho câu trả lời ngắn) và dự phòng hai chiều.
Dự phòng khi Backend lỗi/mất mạng nằm ở Mobile (``speech_to_text`` on-device), không nằm trong file này.

Quy tắc bắt buộc (AGENTS.md §2 UF4, doc Backend UF4/A21):
- Chỉ xử lý audio do người dùng CHỦ ĐỘNG gửi. Không gọi STT cho bản ghi UF4 trong phiên.
- KHÔNG log nội dung audio hoặc transcript — log chỉ có mã lỗi, nhà cung cấp, thời gian xử lý.
- File upload lên Google Files API (khi audio > 15MB hoặc chế độ legacy) phải được xóa ngay sau khi chép (kể cả khi lỗi).
- Nhà cung cấp, model, transport, chế độ, ngôn ngữ, timeout là tham số cấu hình (``app.core.config``).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import logging
import math
import re
import struct
import sys
import time
import unicodedata
from array import array
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Any, Literal, Protocol

import edge_tts
from google import genai
from google.genai import types
from google.oauth2 import service_account
from groq import AsyncGroq

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)

_CLOUD_PLATFORM_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_GEMINI_INLINE_MAX_BYTES = 15 * 1024 * 1024

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
_MIME_TO_EXTENSION: dict[str, str] = {
    "audio/m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/webm": ".webm",
}


def _iso_language_code(language_code: str) -> str:
    """Chuyển BCP-47 (vd ``vi-VN``) sang ISO-639-1 (``vi``) cho Groq Whisper."""
    cleaned = (language_code or "").strip().lower()
    if not cleaned:
        return "vi"
    return cleaned.split("-", 1)[0].split("_", 1)[0]


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
    low_confidence: bool = False


@dataclass(frozen=True, slots=True)
class SttProviderDetails:
    """Chi tiết kết quả từ provider STT (kèm chỉ số âm học từ verbose_json nếu có)."""

    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    all_segments_silent: bool | None = None


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


_GEMINI_STT_PROMPT_SMART = (
    "Hãy chép lời (transcribe) chính xác từng câu từ đoạn ghi âm tiếng Việt dưới đây.\n"
    "Quy tắc:\n"
    "1. Giữ nguyên văn ý nói của người dùng, sửa nhẹ dấu câu cho tự nhiên nhưng KHÔNG tự thêm bớt ý. "
    "Bỏ các từ đệm ngập ngừng vô nghĩa (ờ, ừm), nhưng giữ nguyên các cụm đính chính "
    "('à nhầm', 'sửa lại', 'thay vì').\n"
    "2. Chú ý người Việt nói nhanh hoặc giọng vùng miền, và thường xen lẫn từ/thuật ngữ hoặc tên sách tiếng Anh "
    "(ví dụ: tên framework, thuật ngữ lập trình, từ khóa tiếng Anh chuyên ngành). Hãy viết đúng chính tả tiếng Anh "
    "của các từ đó theo âm thanh nghe được.\n"
    "3. Viết số lượng, ngày, giờ bằng chữ số khi người nói đọc số (vd: 5 trang, 19:00).\n"
    "4. Nếu không có lời nói rõ ràng (im lặng, chỉ có nhiễu nền) thì CHỈ trả về [NO_SPEECH].\n"
    "5. Không giải thích, không thêm tiền tố như 'Transcript:', chỉ trả về duy nhất văn bản đã chép lời."
)

_GEMINI_STT_PROMPT_VERBATIM = (
    "Hãy chép lời nguyên văn (verbatim) chính xác từng từ từ đoạn ghi âm tiếng Việt dưới đây.\n"
    "Quy tắc:\n"
    "1. Giữ nguyên văn mọi từ người dùng nói, kể cả từ đệm, ngập ngừng hay cụm đính chính; "
    "KHÔNG tự thêm bớt hay diễn giải lại.\n"
    "2. Viết đúng chính tả tiếng Việt và các từ/thuật ngữ hoặc tên sách tiếng Anh xen lẫn theo âm thanh nghe được.\n"
    "3. Nếu không có lời nói rõ ràng (im lặng, chỉ có nhiễu nền) thì CHỈ trả về [NO_SPEECH].\n"
    "4. Không giải thích, không thêm tiền tố như 'Transcript:', chỉ trả về duy nhất văn bản đã chép lời."
)

_GEMINI_STT_USER_PROMPT = "Chép lời đoạn ghi âm này."

_CODE_FENCE_START_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?")
_CODE_FENCE_END_RE = re.compile(r"\n?\s*```$")
_NO_SPEECH_SENTINEL_RE = re.compile(
    r"^[\s\"'“”`.,!?;:\-–—…()]*\[\s*(?:no[_\s-]*speech|silence|inaudible)\s*\][\s\"'“”`.,!?;:\-–—…()]*$",
    re.IGNORECASE,
)
_ECHO_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_WAV_DIGITAL_SILENCE_PEAK_THRESHOLD = 32


def _normalize_for_echo_check(text: str) -> str:
    """Chuẩn hóa văn bản (NFC, lowercase, bỏ dấu câu, gộp khoảng trắng) để phát hiện echo prompt."""
    normalized = unicodedata.normalize("NFC", text or "").lower()
    without_punct = _ECHO_PUNCT_RE.sub(" ", normalized)
    return " ".join(without_punct.split())


_NORMALIZED_GEMINI_PROMPTS: tuple[str, ...] = tuple(
    f" {_normalize_for_echo_check(p)} "
    for p in (
        _GEMINI_STT_PROMPT_SMART,
        _GEMINI_STT_PROMPT_VERBATIM,
        _GEMINI_STT_USER_PROMPT,
    )
)

_PROMPT_META_ANCHORS: tuple[str, ...] = tuple(
    f" {_normalize_for_echo_check(anchor)} "
    for anchor in (
        "quy tắc",
        "chép lời",
        "transcribe",
        "transcript",
        "đoạn ghi âm",
        "không giải thích",
        "tiền tố",
        "lời nói rõ ràng",
        "chỉ trả về",
        "văn bản đã chép lời",
    )
)


def _is_gemini_prompt_echo(text: str) -> bool:
    """Kiểm tra đầu ra ngắn (<= 40 ký tự sau chuẩn hóa) có lặp lại meta-instruction trong prompt Gemini không."""
    normalized = _normalize_for_echo_check(text)
    if not normalized or len(normalized) > 40:
        return False
    padded = f" {normalized} "
    if not any(padded in prompt_padded for prompt_padded in _NORMALIZED_GEMINI_PROMPTS):
        return False
    if len(normalized.split()) >= 4:
        return True
    return any(anchor in padded for anchor in _PROMPT_META_ANCHORS)


def _is_wav_pcm16_digital_silence(
    audio: bytes, *, peak_threshold: int = _WAV_DIGITAL_SILENCE_PEAK_THRESHOLD
) -> bool:
    """Tiền kiểm im lặng số (digital silence) cho WAV PCM 16-bit.

    Parse các subchunk RIFF/WAVE (``fmt `` và ``data``) bằng ``struct``. Nếu header hỏng,
    thiếu chunk hoặc không phải PCM 16-bit (``audio_format == 1``, ``bits_per_sample == 16``),
    trả về ``False`` để bỏ qua precheck và vẫn cho gọi provider bình thường.
    """
    try:
        if len(audio) < 12 or audio[0:4] != b"RIFF" or audio[8:12] != b"WAVE":
            return False

        audio_format: int | None = None
        bits_per_sample: int | None = None
        data_slice: memoryview | None = None
        view = memoryview(audio)
        total_len = len(audio)
        offset = 12

        while offset + 8 <= total_len:
            chunk_id = audio[offset : offset + 4]
            (chunk_size,) = struct.unpack_from("<I", audio, offset + 4)
            data_start = offset + 8
            data_end = data_start + chunk_size
            if data_end > total_len:
                return False

            if chunk_id == b"fmt ":
                if chunk_size < 16:
                    return False
                (
                    audio_format,
                    _channels,
                    _sample_rate,
                    _byte_rate,
                    _block_align,
                    bits_per_sample,
                ) = struct.unpack_from("<HHIIHH", audio, data_start)
            elif chunk_id == b"data":
                data_slice = view[data_start:data_end]

            if audio_format is not None and data_slice is not None:
                break

            offset = data_end + (chunk_size % 2)

        if audio_format != 1 or bits_per_sample != 16 or data_slice is None:
            return False

        n_bytes = len(data_slice)
        if n_bytes == 0:
            return True
        if n_bytes % 2 != 0:
            return False

        samples = array("h")
        samples.frombytes(data_slice)
        if sys.byteorder != "little":
            samples.byteswap()
        peak = max(abs(min(samples)), abs(max(samples)))
        return peak <= peak_threshold
    except (struct.error, ValueError, TypeError):
        return False


def _clean_gemini_transcript(raw_text: str) -> str:
    """Làm sạch code fence ``` và dấu ngoặc kép bao quanh đầu ra văn bản từ Gemini."""
    text = (raw_text or "").strip()
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        text = _CODE_FENCE_START_RE.sub("", text, count=1)
        text = _CODE_FENCE_END_RE.sub("", text, count=1).strip()
    while len(text) >= 2:
        if (text[0] == '"' and text[-1] == '"' and '"' not in text[1:-1]) or (
            text[0] == "“" and text[-1] == "”" and "“" not in text[1:-1] and "”" not in text[1:-1]
        ):
            text = text[1:-1].strip()
        else:
            break
    return text


def _is_no_speech_sentinel(text: str) -> bool:
    """Kiểm tra đầu ra Gemini có phải sentinel báo im lặng/không có lời nói hay không."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return bool(_NO_SPEECH_SENTINEL_RE.match(cleaned))


def _accepts_purpose_kwarg(func: Any) -> bool:
    """Kiểm tra callable có nhận tham số ``purpose`` hoặc ``**kwargs`` hay không."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "purpose" and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


class GeminiSttProvider:
    """Gemini STT hỗ trợ inline audio (``generate_content``) và luồng legacy ``transcribe_api``.

    - ``transport="auto"``: nếu ``"transcribe"`` nằm trong tên model -> dùng Files API + ``interactions.create``;
      ngược lại dùng inline ``Part.from_bytes`` với ``models.generate_content`` (fallback Files API nếu > 15MB).
    - Hỗ trợ xác thực kép: ưu tiên ``GEMINI_API_KEY``, dự phòng Service Account (``enterprise=True``).
    """

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        mode: Literal["smart", "verbatim"],
        language_code: str,
        transport: Literal["auto", "inline", "transcribe_api"] = "auto",
        credentials_file: Path | None = None,
        project: str = "",
        location: str = "global",
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model
        self._mode = mode
        self._language_code = language_code
        self._transport = transport
        self._credentials_file = credentials_file
        self._project = project
        self._location = location
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client

        http_opts = types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1),
        )

        if self._api_key:
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=http_opts,
            )
            return self._client

        if self._credentials_file is not None and self._credentials_file.is_file():
            try:
                raw_meta = json.loads(self._credentials_file.read_text(encoding="utf-8"))
                project_id = self._project.strip() or str(raw_meta.get("project_id", "")).strip()
                if project_id:
                    sa_creds = service_account.Credentials.from_service_account_file(
                        str(self._credentials_file),
                        scopes=_CLOUD_PLATFORM_SCOPES,
                    )
                    self._client = genai.Client(
                        enterprise=True,
                        credentials=sa_creds,
                        project=project_id,
                        location=self._location,
                        http_options=http_opts,
                    )
                    return self._client
            except Exception as exc:
                logger.warning(
                    "Không thể nạp Service Account cho Gemini STT (%s).",
                    type(exc).__name__,
                )

        raise SttError(SttErrorCode.PROVIDER_UNAVAILABLE, "missing_gemini_credentials")

    def _resolved_transport(self) -> Literal["inline", "transcribe_api"]:
        if self._transport == "auto":
            return "transcribe_api" if "transcribe" in self._model.lower() else "inline"
        return self._transport

    def _transcription_config(self) -> dict[str, Any]:
        # Docs: verbatim cấu hình dạng object, smart cấu hình dạng chuỗi.
        mode: str | dict[str, str] = "smart" if self._mode == "smart" else {"type": "verbatim"}
        return {"language_codes": [self._language_code], "mode": mode}

    async def _transcribe_via_transcribe_api(
        self, client: genai.Client, audio: bytes, mime_type: str
    ) -> str:
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
        finally:
            if uploaded is not None and uploaded.name:
                await self._delete_quietly(client, uploaded.name)

    async def _transcribe_via_inline(
        self, client: genai.Client, audio: bytes, mime_type: str
    ) -> str:
        prompt = (
            _GEMINI_STT_PROMPT_SMART if self._mode == "smart" else _GEMINI_STT_PROMPT_VERBATIM
        )
        config = types.GenerateContentConfig(
            temperature=0.0,
            candidate_count=1,
            system_instruction=prompt,
        )
        uploaded = None
        try:
            if len(audio) > _GEMINI_INLINE_MAX_BYTES:
                uploaded = await client.aio.files.upload(
                    file=io.BytesIO(audio),
                    config={"mime_type": mime_type},
                )
                uploaded_uri = uploaded.uri
                if not uploaded_uri:
                    raise SttError(SttErrorCode.PROVIDER_ERROR, "missing_uploaded_file_uri")
                audio_part = types.Part.from_uri(
                    file_uri=uploaded_uri,
                    mime_type=uploaded.mime_type or mime_type,
                )
            else:
                audio_part = types.Part.from_bytes(data=audio, mime_type=mime_type)

            resp = await client.aio.models.generate_content(
                model=self._model,
                contents=[audio_part, _GEMINI_STT_USER_PROMPT],
                config=config,
            )
            raw_text = (getattr(resp, "text", None) or "").strip()
        finally:
            if uploaded is not None and uploaded.name:
                await self._delete_quietly(client, uploaded.name)

        cleaned = _clean_gemini_transcript(raw_text)
        if _is_no_speech_sentinel(cleaned):
            raise SttError(SttErrorCode.NO_SPEECH, "no_speech_sentinel")
        if not cleaned:
            raise SttError(SttErrorCode.NO_SPEECH, "empty_transcript")
        if _is_gemini_prompt_echo(cleaned):
            raise SttError(SttErrorCode.NO_SPEECH, "prompt_echo_filtered")
        return cleaned

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        client = self._get_client()
        try:
            if self._resolved_transport() == "transcribe_api":
                return await self._transcribe_via_transcribe_api(client, audio, mime_type)
            return await self._transcribe_via_inline(client, audio, mime_type)
        except Exception as exc:
            raise map_provider_exception(exc) from exc

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


_DEFAULT_STT_PROMPT = (
    "ProPlan VA, ADHD, Pomodoro, Focus Session, Micro-task, deadline, "
    "lịch bận, Google Calendar, cất suy nghĩ, phiên tập trung, "
    "Thứ 2, Thứ 3, Thứ 4, Thứ 5, Thứ 6, Thứ 7, Chủ nhật, hằng ngày, cuối tuần, "
    "19h30, 20h45, tiếng rưỡi, 90 phút, rảnh, bận, trừ, riêng, ngoại lệ, báo cáo, slide."
)

_WHISPER_HALLUCINATION_SUBSTRINGS: tuple[str, ...] = (
    "hãy subscribe",
    "đăng ký kênh",
    "cảm ơn các bạn đã xem",
    "hẹn gặp lại các bạn",
    "hẹn gặp lại ở video",
    "like và subscribe",
    "nhấn chuông thông báo",
    "subtitles by",
    "amara.org",
    "transcribed by",
)
_HALLUCINATION_PUNCT_RE = re.compile(r"[,\.!?;:\"'()\[\]…\-/\\]+")


def _is_whisper_hallucination(text: str) -> bool:
    """Phát hiện ảo giác đặc trưng của Whisper tiếng Việt khi audio im lặng hoặc nhiễu."""
    raw_cleaned = (text or "").strip()
    if not raw_cleaned:
        return False
    normalized = " ".join(raw_cleaned.lower().split()).strip(" ,.!?;:\"'")
    if not normalized:
        return True
    if any(sub in normalized for sub in _WHISPER_HALLUCINATION_SUBSTRINGS):
        return True

    stripped = " ".join(_HALLUCINATION_PUNCT_RE.sub(" ", normalized).split())
    if not stripped:
        return True
    if any(sub in stripped for sub in _WHISPER_HALLUCINATION_SUBSTRINGS if "." not in sub):
        return True

    words = stripped.split()
    n_words = len(words)
    for phrase_len in range(1, 6):
        if n_words < phrase_len * 4:
            continue
        if n_words % phrase_len == 0:
            unit = words[:phrase_len]
            if all(words[i : i + phrase_len] == unit for i in range(0, n_words, phrase_len)):
                return True
        max_start = n_words - phrase_len * 4
        for start in range(max_start + 1):
            unit = words[start : start + phrase_len]
            repeats = 1
            cursor = start + phrase_len
            while cursor + phrase_len <= n_words and words[cursor : cursor + phrase_len] == unit:
                repeats += 1
                cursor += phrase_len
            if repeats >= 4 and (repeats * phrase_len) / n_words >= 0.8:
                return True

    return False


def _to_finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _is_segment_silent(no_speech_prob: float | None, avg_logprob: float | None) -> bool:
    return (
        no_speech_prob is not None
        and avg_logprob is not None
        and no_speech_prob > 0.85
        and avg_logprob < -1.2
    )


def _extract_segment_metrics(
    response: Any,
) -> tuple[float | None, float | None, bool | None]:
    """Rút trích chỉ số âm học theo từng segment từ verbose_json.

    - Đánh giá im lặng trên từng segment (``no_speech_prob > 0.85`` và ``avg_logprob < -1.2``).
    - ``all_segments_silent = True`` chỉ khi TẤT CẢ segment có chỉ số đều im lặng.
    - Khi có ít nhất 1 segment không im lặng, ``(max_no_speech_prob, min_avg_logprob)`` chỉ
      tính trên các segment không im lặng để khoảng lặng cuối câu không làm hỏng câu nói rõ.
    """
    segments = (
        response.get("segments")
        if isinstance(response, dict)
        else getattr(response, "segments", None)
    )
    all_no_speech_probs: list[float] = []
    all_avg_logprobs: list[float] = []
    non_silent_no_speech_probs: list[float] = []
    non_silent_avg_logprobs: list[float] = []
    metric_segment_count = 0
    silent_segment_count = 0

    if isinstance(segments, (list, tuple)):
        for seg in segments:
            raw_no_speech = (
                seg.get("no_speech_prob")
                if isinstance(seg, dict)
                else getattr(seg, "no_speech_prob", None)
            )
            raw_avg_logprob = (
                seg.get("avg_logprob")
                if isinstance(seg, dict)
                else getattr(seg, "avg_logprob", None)
            )
            val_no_speech = _to_finite_float(raw_no_speech)
            val_avg_logprob = _to_finite_float(raw_avg_logprob)
            if val_no_speech is None and val_avg_logprob is None:
                continue

            metric_segment_count += 1
            if val_no_speech is not None:
                all_no_speech_probs.append(val_no_speech)
            if val_avg_logprob is not None:
                all_avg_logprobs.append(val_avg_logprob)

            if _is_segment_silent(val_no_speech, val_avg_logprob):
                silent_segment_count += 1
            else:
                if val_no_speech is not None:
                    non_silent_no_speech_probs.append(val_no_speech)
                if val_avg_logprob is not None:
                    non_silent_avg_logprobs.append(val_avg_logprob)

    if metric_segment_count > 0:
        all_segments_silent = silent_segment_count == metric_segment_count
        if all_segments_silent:
            max_no_speech = max(all_no_speech_probs) if all_no_speech_probs else None
            min_avg_logprob = min(all_avg_logprobs) if all_avg_logprobs else None
        else:
            max_no_speech = (
                max(non_silent_no_speech_probs) if non_silent_no_speech_probs else None
            )
            min_avg_logprob = (
                min(non_silent_avg_logprobs) if non_silent_avg_logprobs else None
            )
        return max_no_speech, min_avg_logprob, all_segments_silent

    top_no_speech = (
        response.get("no_speech_prob")
        if isinstance(response, dict)
        else getattr(response, "no_speech_prob", None)
    )
    top_avg_logprob = (
        response.get("avg_logprob")
        if isinstance(response, dict)
        else getattr(response, "avg_logprob", None)
    )
    return (
        _to_finite_float(top_no_speech),
        _to_finite_float(top_avg_logprob),
        None,
    )


async def _call_provider_details(
    provider: Any,
    audio: bytes,
    mime_type: str,
    *,
    purpose: str | None = None,
) -> SttProviderDetails:
    """Gọi provider ưu tiên transcribe_verbose, nhưng tôn trọng mock patch.object(..., 'transcribe')."""
    inst_dict = getattr(provider, "__dict__", {}) or {}
    transcribe_verbose = getattr(provider, "transcribe_verbose", None)
    if (
        "transcribe" in inst_dict and "transcribe_verbose" not in inst_dict
    ) or not callable(transcribe_verbose):
        transcribe_fn = provider.transcribe
        if purpose is not None and _accepts_purpose_kwarg(transcribe_fn):
            raw = await transcribe_fn(audio, mime_type, purpose=purpose)
        else:
            raw = await transcribe_fn(audio, mime_type)
        if isinstance(raw, SttProviderDetails):
            return raw
        return SttProviderDetails(text=raw)
    if purpose is not None and _accepts_purpose_kwarg(transcribe_verbose):
        raw_details = await transcribe_verbose(audio, mime_type, purpose=purpose)
    else:
        raw_details = await transcribe_verbose(audio, mime_type)
    if isinstance(raw_details, SttProviderDetails):
        return raw_details
    return SttProviderDetails(text=str(raw_details))


class GroqWhisperSttProvider:
    """Groq Whisper (``whisper-large-v3-turbo``) dùng làm nhánh STT tốc độ cao / dự phòng."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        language_code: str,
        timeout_seconds: float = 20.0,
        prompt: str | None = _DEFAULT_STT_PROMPT,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._language = _iso_language_code(language_code)
        self._timeout_seconds = timeout_seconds
        self._prompt: str | None = (prompt or "").strip() or None
        self._client: AsyncGroq | None = None

    def _get_client(self) -> AsyncGroq:
        if not self._api_key:
            raise SttError(SttErrorCode.PROVIDER_UNAVAILABLE, "missing_groq_api_key")
        if self._client is None:
            self._client = AsyncGroq(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                max_retries=0,
            )
        return self._client

    async def transcribe_verbose(self, audio: bytes, mime_type: str) -> SttProviderDetails:
        client = self._get_client()
        ext = _MIME_TO_EXTENSION.get(mime_type, ".m4a")
        try:
            if self._prompt is not None:
                response = await client.audio.transcriptions.create(
                    model=self._model,
                    file=(f"audio{ext}", audio, mime_type),
                    language=self._language,
                    prompt=self._prompt,
                    response_format="verbose_json",
                    temperature=0.0,
                )
            else:
                response = await client.audio.transcriptions.create(
                    model=self._model,
                    file=(f"audio{ext}", audio, mime_type),
                    language=self._language,
                    response_format="verbose_json",
                    temperature=0.0,
                )
            raw_text = (
                response.get("text")
                if isinstance(response, dict)
                else getattr(response, "text", None)
            )
            text = (raw_text or "").strip()
            no_speech_prob, avg_logprob, all_segments_silent = _extract_segment_metrics(response)
            return SttProviderDetails(
                text=text,
                no_speech_prob=no_speech_prob,
                avg_logprob=avg_logprob,
                all_segments_silent=all_segments_silent,
            )
        except Exception as exc:
            raise map_provider_exception(exc) from exc

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        details = await self.transcribe_verbose(audio, mime_type)
        return details.text


class FallbackSttProvider:
    """Điều phối STT 2 nhánh có định tuyến theo ``purpose`` và ngân sách timeout riêng cho nhánh chính."""

    def __init__(
        self,
        *,
        primary: SttProvider,
        fallback: SttProvider,
        prefer_fallback_purposes: frozenset[str] = frozenset(),
        primary_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._prefer_fallback_purposes = prefer_fallback_purposes
        self._primary_timeout_seconds = primary_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self.name = f"{primary.name}->{fallback.name}"

    async def transcribe_verbose_with_provider(
        self, audio: bytes, mime_type: str, purpose: str | None = None
    ) -> tuple[SttProviderDetails, str]:
        if purpose is not None and purpose in self._prefer_fallback_purposes:
            first, second = self._fallback, self._primary
        else:
            first, second = self._primary, self._fallback

        started = time.perf_counter()
        first_timeout: float | None = self._primary_timeout_seconds
        if first_timeout is not None and self._total_timeout_seconds is not None:
            first_timeout = min(first_timeout, self._total_timeout_seconds)
        elif first_timeout is None and self._total_timeout_seconds is not None:
            first_timeout = self._total_timeout_seconds

        try:
            first_coro = _call_provider_details(first, audio, mime_type, purpose=purpose)
            if first_timeout is not None:
                try:
                    details = await asyncio.wait_for(first_coro, timeout=first_timeout)
                except TimeoutError as exc:
                    raise SttError(SttErrorCode.TIMEOUT, "primary_timeout") from exc
            else:
                details = await first_coro
            return details, first.name
        except SttError as exc:
            if not (exc.code == SttErrorCode.PROVIDER_UNAVAILABLE or exc.retryable):
                raise
            logger.warning(
                "STT nhánh chính (%s) lỗi %s; chuyển sang dự phòng (%s).",
                first.name,
                exc.code,
                second.name,
            )
        except Exception as exc:
            mapped = map_provider_exception(exc)
            if not (mapped.code == SttErrorCode.PROVIDER_UNAVAILABLE or mapped.retryable):
                raise mapped from exc
            logger.warning(
                "STT nhánh chính (%s) gặp sự cố %s; chuyển sang dự phòng (%s).",
                first.name,
                mapped.code,
                second.name,
            )

        second_coro = _call_provider_details(second, audio, mime_type, purpose=purpose)
        if self._total_timeout_seconds is not None:
            elapsed = time.perf_counter() - started
            remaining = max(1.0, self._total_timeout_seconds - elapsed)
            try:
                details = await asyncio.wait_for(second_coro, timeout=remaining)
            except TimeoutError as exc:
                raise SttError(SttErrorCode.TIMEOUT, "fallback_timeout") from exc
        else:
            details = await second_coro
        return details, second.name

    async def transcribe_with_provider(
        self, audio: bytes, mime_type: str, purpose: str | None = None
    ) -> tuple[str, str]:
        details, provider_name = await self.transcribe_verbose_with_provider(
            audio, mime_type, purpose=purpose
        )
        return details.text, provider_name

    async def transcribe(
        self, audio: bytes, mime_type: str, purpose: str | None = None
    ) -> str:
        text, _ = await self.transcribe_with_provider(audio, mime_type, purpose=purpose)
        return text


class VoiceService:
    """Điều phối STT: kiểm đầu vào, áp timeout, chuẩn hóa lỗi và kết quả."""

    def __init__(
        self,
        provider: SttProvider,
        *,
        timeout_seconds: float,
        max_audio_bytes: int,
        fallback_provider: SttProvider | None = None,
        primary_timeout_seconds: float | None = None,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_audio_bytes = max_audio_bytes
        self._fallback_provider = fallback_provider
        self._primary_timeout_seconds = primary_timeout_seconds

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str | None = None,
        filename: str | None = None,
        purpose: str | None = None,
    ) -> SttResult:
        """Chuyển audio thành chữ. Ném ``SttError`` nếu không có chữ hợp lệ để đưa sang A01."""
        if not audio:
            raise SttError(SttErrorCode.UNSUPPORTED_AUDIO, "empty_audio")
        if len(audio) > self._max_audio_bytes:
            raise SttError(SttErrorCode.AUDIO_TOO_LARGE, f"bytes={len(audio)}")
        mime_type = resolve_mime_type(content_type, filename)

        started = time.perf_counter()
        if mime_type == "audio/wav" and _is_wav_pcm16_digital_silence(audio):
            digital_silent = SttError(SttErrorCode.NO_SPEECH, "digital_silence")
            self._log_failure(digital_silent, started)
            raise digital_silent

        use_fallback_headroom = isinstance(self._provider, FallbackSttProvider) and (
            self._provider._total_timeout_seconds is not None
            or self._provider._primary_timeout_seconds is not None
        )
        outer_timeout = (
            self._timeout_seconds + 0.5 if use_fallback_headroom else self._timeout_seconds
        )
        try:
            inst_dict = getattr(self._provider, "__dict__", {}) or {}
            transcribe_verbose_with_provider = getattr(
                self._provider, "transcribe_verbose_with_provider", None
            )
            transcribe_with_provider = getattr(self._provider, "transcribe_with_provider", None)
            if (
                callable(transcribe_verbose_with_provider)
                and "transcribe_with_provider" not in inst_dict
                and "transcribe" not in inst_dict
            ):
                if purpose is not None and _accepts_purpose_kwarg(
                    transcribe_verbose_with_provider
                ):
                    coro = transcribe_verbose_with_provider(audio, mime_type, purpose=purpose)
                else:
                    coro = transcribe_verbose_with_provider(audio, mime_type)
                details, active_provider = await asyncio.wait_for(
                    coro,
                    timeout=outer_timeout,
                )
            elif callable(transcribe_with_provider) and "transcribe" not in inst_dict:
                if purpose is not None and _accepts_purpose_kwarg(transcribe_with_provider):
                    coro = transcribe_with_provider(audio, mime_type, purpose=purpose)
                else:
                    coro = transcribe_with_provider(audio, mime_type)
                raw_text, active_provider = await asyncio.wait_for(
                    coro,
                    timeout=outer_timeout,
                )
                details = (
                    raw_text
                    if isinstance(raw_text, SttProviderDetails)
                    else SttProviderDetails(text=raw_text)
                )
            else:
                primary_timeout = (
                    min(self._primary_timeout_seconds, self._timeout_seconds)
                    if (
                        self._fallback_provider is not None
                        and self._primary_timeout_seconds is not None
                    )
                    else self._timeout_seconds
                )
                try:
                    details = await asyncio.wait_for(
                        _call_provider_details(
                            self._provider, audio, mime_type, purpose=purpose
                        ),
                        timeout=primary_timeout,
                    )
                    active_provider = self._provider.name
                except Exception as primary_exc:
                    if isinstance(primary_exc, SttError):
                        mapped_primary = primary_exc
                    elif isinstance(primary_exc, TimeoutError):
                        mapped_primary = SttError(SttErrorCode.TIMEOUT, "primary_timeout")
                    else:
                        mapped_primary = map_provider_exception(primary_exc)
                    if self._fallback_provider is not None and (
                        mapped_primary.retryable
                        or mapped_primary.code == SttErrorCode.PROVIDER_UNAVAILABLE
                    ):
                        logger.warning(
                            "STT nhánh chính (%s) gặp sự cố %s; chuyển sang dự phòng (%s).",
                            self._provider.name,
                            mapped_primary.code,
                            self._fallback_provider.name,
                        )
                        elapsed = time.perf_counter() - started
                        fallback_timeout = max(1.0, self._timeout_seconds - elapsed)
                        details = await asyncio.wait_for(
                            _call_provider_details(
                                self._fallback_provider, audio, mime_type, purpose=purpose
                            ),
                            timeout=fallback_timeout,
                        )
                        active_provider = self._fallback_provider.name
                    else:
                        raise mapped_primary from primary_exc
        except SttError as exc:
            self._log_failure(exc, started)
            raise
        except Exception as exc:
            mapped = map_provider_exception(exc)
            self._log_failure(mapped, started)
            raise mapped from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        cleaned = (details.text or "").strip()
        if _is_no_speech_sentinel(cleaned):
            sentinel_err = SttError(SttErrorCode.NO_SPEECH, "no_speech_sentinel")
            self._log_failure(sentinel_err, started)
            raise sentinel_err

        if not cleaned or not _HALLUCINATION_PUNCT_RE.sub("", cleaned).strip():
            no_speech = SttError(SttErrorCode.NO_SPEECH, "empty_transcript")
            self._log_failure(no_speech, started)
            raise no_speech

        if _is_whisper_hallucination(cleaned):
            hallucinated = SttError(SttErrorCode.NO_SPEECH, "hallucination_filtered")
            self._log_failure(hallucinated, started)
            raise hallucinated

        is_acoustically_silent = (
            details.all_segments_silent
            if details.all_segments_silent is not None
            else _is_segment_silent(details.no_speech_prob, details.avg_logprob)
        )
        if is_acoustically_silent:
            silent = SttError(SttErrorCode.NO_SPEECH, "high_no_speech_prob")
            self._log_failure(silent, started)
            raise silent

        if active_provider == "gemini":
            low_confidence = False
        else:
            low_confidence = bool(
                (details.no_speech_prob is not None and details.no_speech_prob > 0.7)
                or (details.avg_logprob is not None and details.avg_logprob < -1.0)
            )

        logger.info(
            "STT thành công: provider=%s latency_ms=%d low_confidence=%s",
            active_provider,
            latency_ms,
            low_confidence,
        )
        return SttResult(
            text=cleaned,
            provider=active_provider,
            latency_ms=latency_ms,
            low_confidence=low_confidence,
        )

    def _log_failure(self, exc: SttError, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "STT thất bại: provider=%s code=%s detail=%s elapsed_ms=%d",
            self._provider.name,
            exc.code,
            exc.detail,
            elapsed_ms,
        )


def _build_gemini_stt_provider(cfg: Settings) -> GeminiSttProvider:
    return GeminiSttProvider(
        api_key=cfg.GEMINI_API_KEY.strip() if cfg.has_gemini_key else "",
        model=cfg.GEMINI_STT_MODEL,
        mode=cfg.GEMINI_STT_MODE,
        language_code=cfg.VOICE_STT_LANGUAGE,
        transport=cfg.GEMINI_STT_TRANSPORT,
        credentials_file=cfg.resolved_google_credentials_file,
        project=cfg.GOOGLE_CLOUD_PROJECT,
        location=cfg.GOOGLE_CLOUD_LOCATION,
    )


def _build_groq_stt_provider(cfg: Settings) -> GroqWhisperSttProvider:
    return GroqWhisperSttProvider(
        api_key=cfg.GROQ_API_KEY.strip() if cfg.has_groq_key else "",
        model=cfg.GROQ_STT_MODEL,
        language_code=cfg.VOICE_STT_LANGUAGE,
        timeout_seconds=cfg.VOICE_STT_TIMEOUT_SECONDS,
    )


def build_voice_service(app_settings: Settings | None = None) -> VoiceService:
    """Tạo VoiceService theo cấu hình ``VOICE_STT_PROVIDER`` (``gemini``, ``groq``, ``auto``, ``fake``)."""
    cfg = app_settings or settings
    provider: SttProvider
    fallback: SttProvider | None = None
    if cfg.VOICE_STT_PROVIDER == "fake":
        provider = FakeSttProvider()
    elif cfg.VOICE_STT_PROVIDER == "groq":
        provider = _build_groq_stt_provider(cfg)
    elif cfg.VOICE_STT_PROVIDER == "auto":
        provider = FallbackSttProvider(
            primary=_build_gemini_stt_provider(cfg),
            fallback=_build_groq_stt_provider(cfg),
            prefer_fallback_purposes=(
                frozenset({"uf5_checkin"}) if cfg.VOICE_STT_UF5_PREFER_GROQ else frozenset()
            ),
            primary_timeout_seconds=cfg.VOICE_STT_PRIMARY_TIMEOUT_SECONDS,
            total_timeout_seconds=cfg.VOICE_STT_TIMEOUT_SECONDS,
        )
    else:
        provider = _build_gemini_stt_provider(cfg)
        fallback = _build_groq_stt_provider(cfg) if cfg.has_groq_key else None
    return VoiceService(
        provider,
        timeout_seconds=cfg.VOICE_STT_TIMEOUT_SECONDS,
        max_audio_bytes=cfg.VOICE_MAX_AUDIO_BYTES,
        fallback_provider=fallback,
        primary_timeout_seconds=cfg.VOICE_STT_PRIMARY_TIMEOUT_SECONDS,
    )


@lru_cache(maxsize=1)
def get_voice_service() -> VoiceService:
    """Singleton VoiceService dùng chung cho Router (giữ một client STT dùng lại)."""
    return build_voice_service()


# ==============================================================================
# TTS PIPELINE (W3-09): EDGE-TTS + CACHE + ON-DEVICE FALLBACK SIGNALS
# ==============================================================================


class TtsErrorCode(StrEnum):
    """Mã lỗi chuẩn của dịch vụ TTS."""

    INVALID_INPUT = "INVALID_INPUT"
    TEXT_TOO_LONG = "TEXT_TOO_LONG"
    EMPTY_AUDIO = "EMPTY_AUDIO"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class TtsError(Exception):
    """Lỗi tổng hợp giọng nói. Không lưu/làm lộ toàn văn nội dung nhạy cảm."""

    def __init__(self, code: TtsErrorCode, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else str(code))
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in {
            TtsErrorCode.TIMEOUT,
            TtsErrorCode.PROVIDER_ERROR,
            TtsErrorCode.PROVIDER_UNAVAILABLE,
        }


@dataclass(frozen=True, slots=True)
class TtsResult:
    """Kết quả tổng hợp âm thanh TTS."""

    audio_bytes: bytes
    mime_type: str
    provider: str
    voice: str
    cached: bool
    latency_ms: int


class TtsProvider(Protocol):
    """Giao diện chung cho các nhà cung cấp TTS (Edge-TTS hoặc giả lập cho test)."""

    name: str

    async def synthesize(self, text: str, voice: str) -> tuple[bytes, str]: ...


def _silent_wav(duration_ms: int = 160, sample_rate: int = 16000) -> bytes:
    """Sinh file WAV PCM mono 16-bit im lặng hợp lệ cho FakeTtsProvider."""
    num_samples = max(1, int(sample_rate * duration_ms / 1000))
    data_size = num_samples * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + (b"\x00\x00" * num_samples)


class EdgeTtsProvider:
    """Tổng hợp giọng nói tiếng Việt qua ``edge-tts`` (mặc định ``vi-VN-HoaiMyNeural``)."""

    name = "edge_tts"

    def __init__(
        self,
        *,
        receive_timeout: int = 15,
        retry_delay_seconds: float = 0.15,
    ) -> None:
        self._receive_timeout = receive_timeout
        self._retry_delay_seconds = retry_delay_seconds

    async def _synthesize_once(self, cleaned: str, voice: str) -> bytes:
        communicate = edge_tts.Communicate(
            text=cleaned,
            voice=voice,
            receive_timeout=self._receive_timeout,
        )
        audio_buf = io.BytesIO()
        try:
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    data = chunk.get("data")
                    if data:
                        audio_buf.write(data)
        except TtsError:
            raise
        except TimeoutError as exc:
            raise TtsError(TtsErrorCode.TIMEOUT, type(exc).__name__) from exc
        except Exception as exc:
            exc_name = type(exc).__name__
            if "Timeout" in exc_name:
                raise TtsError(TtsErrorCode.TIMEOUT, exc_name) from exc
            if "NoAudio" in exc_name:
                raise TtsError(TtsErrorCode.EMPTY_AUDIO, exc_name) from exc
            raise TtsError(TtsErrorCode.PROVIDER_ERROR, exc_name) from exc

        audio_bytes = audio_buf.getvalue()
        if not audio_bytes:
            raise TtsError(TtsErrorCode.EMPTY_AUDIO, "no_audio_chunks")
        return audio_bytes

    async def synthesize(self, text: str, voice: str) -> tuple[bytes, str]:
        cleaned = (text or "").strip()
        if not cleaned:
            raise TtsError(TtsErrorCode.INVALID_INPUT, "empty_text")
        for attempt in range(2):
            try:
                audio_bytes = await self._synthesize_once(cleaned, voice)
                return audio_bytes, "audio/mpeg"
            except TtsError as exc:
                if attempt == 0 and exc.code in (
                    TtsErrorCode.EMPTY_AUDIO,
                    TtsErrorCode.PROVIDER_ERROR,
                ):
                    logger.warning(
                        "EdgeTTS lỗi %s ở lần thử 1; chờ %.2fs và thử lại lần 2.",
                        exc.code,
                        self._retry_delay_seconds,
                    )
                    await asyncio.sleep(self._retry_delay_seconds)
                    continue
                raise
        raise TtsError(TtsErrorCode.EMPTY_AUDIO, "no_audio_chunks")


class FakeTtsProvider:
    """Nhà cung cấp TTS giả lập không gọi mạng cho unit/integration test."""

    name = "fake"

    def __init__(self, *, audio_bytes: bytes | None = None, mime_type: str = "audio/wav") -> None:
        self._audio_bytes = audio_bytes if audio_bytes is not None else _silent_wav()
        self._mime_type = mime_type

    async def synthesize(self, text: str, voice: str) -> tuple[bytes, str]:
        return self._audio_bytes, self._mime_type


class TtsService:
    """Điều phối tổng hợp giọng nói: chuẩn hóa văn bản, cache LRU theo hash, áp timeout."""

    def __init__(
        self,
        provider: TtsProvider,
        *,
        default_voice: str = "vi-VN-HoaiMyNeural",
        timeout_seconds: float = 10.0,
        max_chars: int = 600,
        cache_size: int = 64,
    ) -> None:
        self._provider = provider
        self._default_voice = default_voice
        self._timeout_seconds = timeout_seconds
        self._max_chars = max_chars
        self._cache_size = max(0, cache_size)
        self._cache: OrderedDict[str, tuple[bytes, str, str]] = OrderedDict()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
    ) -> TtsResult:
        normalized = " ".join((text or "").split())
        if not normalized:
            raise TtsError(TtsErrorCode.INVALID_INPUT, "empty_text")
        if len(normalized) > self._max_chars:
            raise TtsError(TtsErrorCode.TEXT_TOO_LONG, f"chars={len(normalized)}")

        target_voice = (voice or self._default_voice).strip() or self._default_voice
        cache_key = hashlib.sha256(
            f"{self._provider.name}|{target_voice}|{normalized}".encode()
        ).hexdigest()

        if self._cache_size > 0 and cache_key in self._cache:
            cached_bytes, cached_mime, cached_voice = self._cache.pop(cache_key)
            self._cache[cache_key] = (cached_bytes, cached_mime, cached_voice)
            return TtsResult(
                audio_bytes=cached_bytes,
                mime_type=cached_mime,
                provider=self._provider.name,
                voice=cached_voice,
                cached=True,
                latency_ms=0,
            )

        started = time.perf_counter()
        try:
            audio_bytes, mime_type = await asyncio.wait_for(
                self._provider.synthesize(normalized, target_voice),
                timeout=self._timeout_seconds,
            )
        except TtsError as exc:
            self._log_tts_failure(exc, started, target_voice)
            raise
        except TimeoutError as exc:
            mapped = TtsError(TtsErrorCode.TIMEOUT, type(exc).__name__)
            self._log_tts_failure(mapped, started, target_voice)
            raise mapped from exc
        except Exception as exc:
            mapped = TtsError(TtsErrorCode.PROVIDER_ERROR, type(exc).__name__)
            self._log_tts_failure(mapped, started, target_voice)
            raise mapped from exc

        if not audio_bytes:
            empty_err = TtsError(TtsErrorCode.EMPTY_AUDIO, "empty_audio")
            self._log_tts_failure(empty_err, started, target_voice)
            raise empty_err

        latency_ms = int((time.perf_counter() - started) * 1000)
        if self._cache_size > 0:
            self._cache[cache_key] = (audio_bytes, mime_type, target_voice)
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

        logger.info(
            "TTS thành công: provider=%s voice=%s latency_ms=%d bytes=%d",
            self._provider.name,
            target_voice,
            latency_ms,
            len(audio_bytes),
        )
        return TtsResult(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            provider=self._provider.name,
            voice=target_voice,
            cached=False,
            latency_ms=latency_ms,
        )

    def _log_tts_failure(self, exc: TtsError, started: float, voice: str) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "TTS thất bại: provider=%s voice=%s code=%s detail=%s elapsed_ms=%d",
            self._provider.name,
            voice,
            exc.code,
            exc.detail,
            elapsed_ms,
        )


def build_tts_service(app_settings: Settings | None = None) -> TtsService:
    """Tạo TtsService theo cấu hình ``VOICE_TTS_PROVIDER``."""
    cfg = app_settings or settings
    provider: TtsProvider
    if cfg.VOICE_TTS_PROVIDER == "fake":
        provider = FakeTtsProvider()
    else:
        receive_timeout = max(5, int(cfg.VOICE_TTS_TIMEOUT_SECONDS))
        provider = EdgeTtsProvider(receive_timeout=receive_timeout)
    return TtsService(
        provider,
        default_voice=cfg.VOICE_TTS_DEFAULT_VOICE,
        timeout_seconds=cfg.VOICE_TTS_TIMEOUT_SECONDS,
        max_chars=cfg.VOICE_TTS_MAX_CHARS,
        cache_size=cfg.VOICE_TTS_CACHE_SIZE,
    )


@lru_cache(maxsize=1)
def get_tts_service() -> TtsService:
    """Singleton TtsService dùng chung cho Router."""
    return build_tts_service()
