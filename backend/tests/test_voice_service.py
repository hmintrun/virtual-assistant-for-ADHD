"""Kiểm thử adapter STT (V01/A21) — dùng nhà cung cấp giả, không gọi mạng, không tốn quota."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.voice_service import (
    FakeSttProvider,
    GeminiSttProvider,
    SttError,
    SttErrorCode,
    VoiceService,
    map_provider_exception,
    resolve_mime_type,
)

AUDIO = b"\x00\x01fake-m4a-bytes"


def _service(provider, *, timeout: float = 1.0, max_bytes: int = 1024) -> VoiceService:
    return VoiceService(provider, timeout_seconds=timeout, max_audio_bytes=max_bytes)


class _StaticProvider:
    name = "static"

    def __init__(self, result: str = "", error: Exception | None = None, delay: float = 0.0):
        self._result = result
        self._error = error
        self._delay = delay

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


class _HttpLikeError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


# ---------- resolve_mime_type ----------


@pytest.mark.parametrize(
    ("content_type", "filename", "expected"),
    [
        ("audio/m4a", None, "audio/m4a"),
        ("audio/x-m4a", None, "audio/m4a"),
        ("audio/mp4", "clip.m4a", "audio/m4a"),
        ("application/octet-stream", "c01.m4a", "audio/m4a"),
        (None, "note.WAV", "audio/wav"),
        ("audio/mpeg; charset=binary", None, "audio/mp3"),
    ],
)
def test_resolve_mime_type(content_type, filename, expected):
    assert resolve_mime_type(content_type, filename) == expected


def test_resolve_mime_type_rejects_unknown():
    with pytest.raises(SttError) as info:
        resolve_mime_type("application/pdf", "doc.pdf")
    assert info.value.code is SttErrorCode.UNSUPPORTED_AUDIO


# ---------- VoiceService ----------


async def test_transcribe_success_strips_text():
    result = await _service(_StaticProvider("  Thứ sáu tuần sau nộp slide  ")).transcribe(
        AUDIO, filename="c01.m4a"
    )
    assert result.text == "Thứ sáu tuần sau nộp slide"
    assert result.provider == "static"
    assert result.latency_ms >= 0


async def test_fake_provider_for_dev():
    result = await _service(FakeSttProvider("xin chào")).transcribe(AUDIO, filename="a.m4a")
    assert result.text == "xin chào"
    assert result.provider == "fake"


async def test_empty_transcript_is_no_speech():
    """Gemini trả chuỗi rỗng với clip im lặng (khảo sát c05) -> không được đưa sang A01."""
    with pytest.raises(SttError) as info:
        await _service(_StaticProvider("   ")).transcribe(AUDIO, filename="silent.m4a")
    assert info.value.code is SttErrorCode.NO_SPEECH
    assert info.value.retryable is False


async def test_empty_audio_rejected_before_provider():
    provider = _StaticProvider(error=AssertionError("không được gọi provider"))
    with pytest.raises(SttError) as info:
        await _service(provider).transcribe(b"", filename="a.m4a")
    assert info.value.code is SttErrorCode.UNSUPPORTED_AUDIO


async def test_audio_too_large():
    with pytest.raises(SttError) as info:
        await _service(_StaticProvider("x"), max_bytes=4).transcribe(AUDIO, filename="a.m4a")
    assert info.value.code is SttErrorCode.AUDIO_TOO_LARGE


async def test_timeout_is_retryable():
    with pytest.raises(SttError) as info:
        await _service(_StaticProvider("x", delay=0.5), timeout=0.05).transcribe(
            AUDIO, filename="a.m4a"
        )
    assert info.value.code is SttErrorCode.TIMEOUT
    assert info.value.retryable is True


async def test_unknown_provider_exception_is_mapped():
    with pytest.raises(SttError) as info:
        await _service(_StaticProvider(error=_HttpLikeError(429))).transcribe(
            AUDIO, filename="a.m4a"
        )
    assert info.value.code is SttErrorCode.RATE_LIMITED


# ---------- map_provider_exception ----------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_HttpLikeError(429), SttErrorCode.RATE_LIMITED),
        (_HttpLikeError(401), SttErrorCode.PROVIDER_UNAVAILABLE),
        (_HttpLikeError(403), SttErrorCode.PROVIDER_UNAVAILABLE),
        (_HttpLikeError(400), SttErrorCode.UNSUPPORTED_AUDIO),
        (_HttpLikeError(500), SttErrorCode.PROVIDER_ERROR),
        (TimeoutError(), SttErrorCode.TIMEOUT),
        (ConnectionError(), SttErrorCode.PROVIDER_ERROR),
    ],
)
def test_map_provider_exception(exc, expected):
    assert map_provider_exception(exc).code is expected


def test_map_provider_exception_reads_code_attribute():
    """Files API của google-genai đặt HTTP status ở thuộc tính ``code``."""
    exc = Exception("files api")
    exc.code = 429  # type: ignore[attr-defined]
    assert map_provider_exception(exc).code is SttErrorCode.RATE_LIMITED


# ---------- GeminiSttProvider (client giả lập) ----------


class _FakeGeminiClient:
    def __init__(self, *, output_text: str | None = "Mình cần ôn thi giữa kỳ", fail_create=None):
        self.deleted: list[str] = []
        self.create_kwargs: dict = {}
        self._output_text = output_text
        self._fail_create = fail_create
        self.aio = SimpleNamespace(
            files=SimpleNamespace(upload=self._upload, delete=self._delete),
            interactions=SimpleNamespace(create=self._create),
        )

    async def _upload(self, *, file, config):
        return SimpleNamespace(name="files/abc", uri="https://gen/files/abc", mime_type=None)

    async def _create(self, **kwargs):
        self.create_kwargs = kwargs
        if self._fail_create is not None:
            raise self._fail_create
        return SimpleNamespace(output_text=self._output_text)

    async def _delete(self, *, name):
        self.deleted.append(name)


def _gemini(client: _FakeGeminiClient, mode="smart") -> GeminiSttProvider:
    provider = GeminiSttProvider(
        api_key="test-key", model="gemini-3.5-transcribe", mode=mode, language_code="vi-VN"
    )
    provider._client = client  # type: ignore[assignment]
    return provider


async def test_gemini_success_sends_config_and_deletes_file():
    client = _FakeGeminiClient()
    text = await _gemini(client).transcribe(AUDIO, "audio/m4a")

    assert text == "Mình cần ôn thi giữa kỳ"
    assert client.deleted == ["files/abc"]
    cfg = client.create_kwargs["generation_config"]["transcription_config"]
    assert cfg == {"language_codes": ["vi-VN"], "mode": "smart"}
    assert client.create_kwargs["input"][0]["mime_type"] == "audio/m4a"


async def test_gemini_verbatim_mode_config():
    client = _FakeGeminiClient()
    await _gemini(client, mode="verbatim").transcribe(AUDIO, "audio/m4a")
    cfg = client.create_kwargs["generation_config"]["transcription_config"]
    assert cfg["mode"] == {"type": "verbatim"}


async def test_gemini_deletes_file_even_on_error():
    """Quy tắc riêng tư: file trên Google phải bị xóa kể cả khi chép lời lỗi."""
    client = _FakeGeminiClient(fail_create=_HttpLikeError(429))
    with pytest.raises(SttError) as info:
        await _gemini(client).transcribe(AUDIO, "audio/m4a")
    assert info.value.code is SttErrorCode.RATE_LIMITED
    assert client.deleted == ["files/abc"]


async def test_gemini_none_output_returns_empty_string():
    client = _FakeGeminiClient(output_text=None)
    assert await _gemini(client).transcribe(AUDIO, "audio/m4a") == ""


async def test_gemini_without_key_is_unavailable():
    provider = GeminiSttProvider(
        api_key="", model="gemini-3.5-transcribe", mode="smart", language_code="vi-VN"
    )
    with pytest.raises(SttError) as info:
        await provider.transcribe(AUDIO, "audio/m4a")
    assert info.value.code is SttErrorCode.PROVIDER_UNAVAILABLE
