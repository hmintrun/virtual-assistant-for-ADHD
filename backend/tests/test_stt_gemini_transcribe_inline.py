"""Kiểm thử transport ``transcribe_inline``: Gemini Transcribe qua ``generate_content`` + audio inline.

Lý do & số đo: docs/backend/VOICE_STT_BENCHMARK.md (Files API + interactions ~10s -> inline generate_content ~2-2,9s).
Mock SDK hoàn toàn offline:
1. transport='auto' + model Transcribe -> generate_content 1 lần, KHÔNG upload / interactions / delete.
2. Gửi audio_transcription_config (vi-VN, VERBATIM/SMART theo mode), không có system prompt.
3. Transcript nằm trong part ``audio_transcription`` (không có part text) vẫn đọc được.
4. Kết quả rỗng -> SttError(NO_SPEECH).
5. Audio > 15MB -> quay về luồng Files API (upload + interactions + delete).
6. Model Flash-Lite với transport='auto' vẫn đi luồng inline + prompt cũ.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import types

from app.services import voice_service as voice_service_mod
from app.services.voice_service import GeminiSttProvider, SttError, SttErrorCode


def _client(gen_return: object) -> tuple[SimpleNamespace, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    gen = AsyncMock(return_value=gen_return)
    up = AsyncMock(return_value=SimpleNamespace(uri="files/u", name="files/temp-1", mime_type="audio/m4a"))
    dele = AsyncMock(return_value=None)
    inter = AsyncMock(return_value=SimpleNamespace(output_text="legacy"))
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=gen),
            files=SimpleNamespace(upload=up, delete=dele),
            interactions=SimpleNamespace(create=inter),
        )
    )
    return client, gen, up, dele, inter


def _resp_with_parts(*parts: object) -> SimpleNamespace:
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=list(parts)))])


def _provider(client: object, *, model: str = "gemini-3.5-transcribe", mode: str = "verbatim") -> GeminiSttProvider:
    p = GeminiSttProvider(api_key="k", model=model, mode=mode, language_code="vi-VN", transport="auto")  # type: ignore[arg-type]
    p._client = client  # type: ignore[assignment]
    return p


@pytest.mark.asyncio
async def test_auto_transport_transcribe_model_uses_single_generate_content_call() -> None:
    client, gen, up, dele, inter = _client(
        _resp_with_parts(SimpleNamespace(text="Hạn là thứ năm, à không, thứ sáu chứ.", audio_transcription=None))
    )
    text = await _provider(client).transcribe(b"m4a-bytes", "audio/m4a")

    assert text == "Hạn là thứ năm, à không, thứ sáu chứ."
    gen.assert_awaited_once()
    up.assert_not_awaited()
    inter.assert_not_awaited()
    dele.assert_not_awaited()

    kwargs = gen.await_args.kwargs
    assert kwargs["model"] == "gemini-3.5-transcribe"
    (part,) = kwargs["contents"]
    assert part.inline_data.data == b"m4a-bytes"
    assert part.inline_data.mime_type == "audio/m4a"
    cfg = kwargs["config"]
    assert cfg.system_instruction is None
    assert cfg.audio_transcription_config.language_codes == ["vi-VN"]
    assert cfg.audio_transcription_config.mode == types.AudioTranscriptionConfigMode.VERBATIM


@pytest.mark.asyncio
async def test_smart_mode_is_forwarded() -> None:
    client, gen, *_ = _client(SimpleNamespace(text="- Ôn thi giữa kỳ"))
    await _provider(client, mode="smart").transcribe(b"x", "audio/m4a")
    cfg = gen.await_args.kwargs["config"]
    assert cfg.audio_transcription_config.mode == types.AudioTranscriptionConfigMode.SMART


@pytest.mark.asyncio
async def test_reads_audio_transcription_part_when_no_text_part() -> None:
    client, *_ = _client(
        _resp_with_parts(SimpleNamespace(text=None, audio_transcription=SimpleNamespace(text="Làm slide môn Tư duy tính toán")))
    )
    assert await _provider(client).transcribe(b"x", "audio/m4a") == "Làm slide môn Tư duy tính toán"


@pytest.mark.asyncio
async def test_empty_transcript_raises_no_speech() -> None:
    client, *_ = _client(_resp_with_parts(SimpleNamespace(text="  ", audio_transcription=None)))
    with pytest.raises(SttError) as exc_info:
        await _provider(client).transcribe(b"silence", "audio/m4a")
    assert exc_info.value.code == SttErrorCode.NO_SPEECH


@pytest.mark.asyncio
async def test_large_audio_falls_back_to_files_api() -> None:
    client, gen, up, dele, inter = _client(SimpleNamespace(text="không dùng"))
    with patch.object(voice_service_mod, "_GEMINI_INLINE_MAX_BYTES", 4):
        text = await _provider(client).transcribe(b"12345", "audio/m4a")
    assert text == "legacy"
    gen.assert_not_awaited()
    up.assert_awaited_once()
    inter.assert_awaited_once()
    dele.assert_awaited_once_with(name="files/temp-1")


@pytest.mark.asyncio
async def test_flash_lite_auto_transport_still_uses_prompt_inline() -> None:
    client, gen, *_ = _client(SimpleNamespace(text="Làm bài tập"))
    await _provider(client, model="gemini-3.5-flash-lite").transcribe(b"x", "audio/m4a")
    cfg = gen.await_args.kwargs["config"]
    assert cfg.system_instruction  # luồng inline cũ vẫn dùng prompt chép lời
    assert cfg.audio_transcription_config is None
