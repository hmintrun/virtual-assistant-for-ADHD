"""Kiểm thử chuyển đổi Backend Voice STT sang Gemini 3.5 Flash-Lite inline audio & purpose-aware routing.

Bao phủ toàn bộ 9 nhóm kịch bản (mock SDK, hoàn toàn offline):
1. Inline audio gọi models.generate_content với Part.from_bytes & temperature=0.0, không gọi files.upload
   (kèm kiểm tra > 15MB tự upload Files API + xóa file trong finally, và làm sạch code fence / ngoặc kép).
2. transport='transcribe_api' (legacy, chọn tường minh) -> files.upload + interactions.create + files.delete.
   (transport='auto' với model Transcribe giờ đi transcribe_inline — xem test_stt_gemini_transcribe_inline.py.)
3. Không có GEMINI_API_KEY nhưng có file Service Account -> dùng genai.Client(enterprise=True, ...).
4. Sentinel [NO_SPEECH] (kể cả [NO_SPEECH]., chữ thường, [silence], [inaudible], chuỗi rỗng) -> SttError(NO_SPEECH),
   Groq KHÔNG bị gọi; qua HTTP POST /api/v1/voice/transcriptions -> 422 NO_SPEECH_DETECTED.
5. Gemini lỗi 429 / 503 hoặc chậm hơn primary_timeout_seconds (0.05s) -> fallback Groq trả 200,
   provider='groq', giữ nguyên low_confidence của Groq.
6. Purpose-aware routing: purpose='uf5_checkin' -> Groq đi trước; 'uf1_input' & 'thought_to_draft' -> Gemini đi trước;
   khi VOICE_STT_UF5_PREFER_GROQ=False thì 'uf5_checkin' đi Gemini trước.
7. Custom SttProvider chỉ có chữ ký (audio, mime_type) vẫn chạy an toàn khi truyền purpose.
8. Router POST /api/v1/voice/transcriptions truyền đúng purpose vào service (và vẫn tương thích mock cũ không nhận purpose).
9. Settings defaults: VOICE_STT_TIMEOUT_SECONDS==12.0, VOICE_STT_PRIMARY_TIMEOUT_SECONDS==8.0,
   VOICE_STT_UF5_PREFER_GROQ is True, VOICE_STT_PROVIDER=='auto', GEMINI_STT_MODEL=='gemini-3.5-transcribe'.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_current_user, get_db
from app.api.v1 import voice as voice_router_mod
from app.core.config import Settings
from app.core.database import make_engine
from app.main import app
from app.models import Base, User, UserPreference
from app.schemas.user import CurrentUser
from app.services import voice_service as voice_service_mod
from app.services.voice_service import (
    _GEMINI_INLINE_MAX_BYTES,
    _GEMINI_STT_PROMPT_SMART,
    _GEMINI_STT_PROMPT_VERBATIM,
    _GEMINI_STT_USER_PROMPT,
    FallbackSttProvider,
    GeminiSttProvider,
    GroqWhisperSttProvider,
    SttError,
    SttErrorCode,
    SttResult,
    VoiceService,
    build_voice_service,
    get_voice_service,
)


class _SdkStatusError(Exception):
    """Lỗi giả lập từ SDK mang status_code (429/503...)."""

    def __init__(self, status_code: int, message: str = "upstream error") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture
async def stt_http_client():
    """HTTP client cô lập DB SQLite in-memory cho các test router /api/v1/voice/transcriptions."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    user_id = uuid4()
    user_str = str(user_id)
    user_email = f"stt_inline_{user_id.hex[:10]}@example.com"

    voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
    voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
    get_voice_service.cache_clear()

    async with factory() as db:
        db.add_all(
            [
                User(
                    id=user_id,
                    email=user_email,
                    display_name="STT Inline Tester",
                    is_active=True,
                ),
                UserPreference(
                    user_id=user_id,
                    reminder_offsets=[900, 600, 300],
                    work_interval_seconds=1500,
                    rest_interval_seconds=300,
                    buffer_multiplier=1.2,
                ),
            ]
        )
        await db.commit()

    async def override_db():
        async with factory() as db:
            yield db

    async def override_user():
        return CurrentUser(user_id=user_str, email=user_email, display_name="STT Inline Tester")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
        voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
        get_voice_service.cache_clear()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


def _make_mock_gemini_client(
    *,
    generate_content_mock: AsyncMock | None = None,
    upload_mock: AsyncMock | None = None,
    delete_mock: AsyncMock | None = None,
    interactions_create_mock: AsyncMock | None = None,
) -> tuple[Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    gen_mock = generate_content_mock or AsyncMock(
        return_value=SimpleNamespace(text="Làm bài tập 5 trang trước 19:00")
    )
    up_mock = upload_mock or AsyncMock(
        return_value=SimpleNamespace(
            uri="https://generativelanguage.googleapis.com/v1beta/files/temp-1",
            name="files/temp-1",
            mime_type="audio/m4a",
        )
    )
    del_mock = delete_mock or AsyncMock(return_value=None)
    inter_mock = interactions_create_mock or AsyncMock(
        return_value=SimpleNamespace(output_text="Kết quả legacy transcribe")
    )
    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=gen_mock),
            files=SimpleNamespace(upload=up_mock, delete=del_mock),
            interactions=SimpleNamespace(create=inter_mock),
        )
    )
    return fake_client, gen_mock, up_mock, del_mock, inter_mock


# ------------------------------------------------------------------------------
# Case 1: Inline audio -> models.generate_content + Part.from_bytes + temp=0.0
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_inline_uses_generate_content_part_from_bytes_without_upload() -> None:
    fake_client, gen_mock, up_mock, del_mock, inter_mock = _make_mock_gemini_client(
        generate_content_mock=AsyncMock(
            return_value=SimpleNamespace(text='```text\n"Làm báo cáo 5 trang trước 19:00"\n```')
        )
    )
    provider = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
        transport="auto",
    )
    provider._client = fake_client

    audio_bytes = b"M4A-inline-payload"
    text = await provider.transcribe(audio_bytes, "audio/m4a")

    assert text == "Làm báo cáo 5 trang trước 19:00"
    up_mock.assert_not_awaited()
    del_mock.assert_not_awaited()
    inter_mock.assert_not_awaited()

    gen_mock.assert_awaited_once()
    assert gen_mock.await_args is not None
    kwargs = gen_mock.await_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    contents = kwargs["contents"]
    assert len(contents) == 2
    assert isinstance(contents[0], types.Part)
    assert contents[0].inline_data is not None
    assert contents[0].inline_data.data == audio_bytes
    assert contents[0].inline_data.mime_type == "audio/m4a"
    assert contents[1] == _GEMINI_STT_USER_PROMPT

    config = kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.temperature == 0.0
    assert config.candidate_count == 1
    assert config.system_instruction == _GEMINI_STT_PROMPT_SMART
    assert getattr(config, "thinking_config", None) is None


@pytest.mark.asyncio
async def test_gemini_inline_verbatim_mode_and_large_audio_uploads_and_deletes_in_finally() -> None:
    fake_client, gen_mock, up_mock, del_mock, _ = _make_mock_gemini_client(
        generate_content_mock=AsyncMock(
            return_value=SimpleNamespace(text="Ờ ừm làm báo cáo môn học")
        )
    )
    provider = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="verbatim",
        language_code="vi-VN",
        transport="inline",
    )
    provider._client = fake_client

    large_audio = b"x" * (_GEMINI_INLINE_MAX_BYTES + 1)
    text = await provider.transcribe(large_audio, "audio/m4a")

    assert text == "Ờ ừm làm báo cáo môn học"
    up_mock.assert_awaited_once()
    del_mock.assert_awaited_once_with(name="files/temp-1")
    gen_mock.assert_awaited_once()
    assert gen_mock.await_args is not None
    contents = gen_mock.await_args.kwargs["contents"]
    config = gen_mock.await_args.kwargs["config"]
    assert isinstance(contents[0], types.Part)
    assert contents[0].file_data is not None
    assert contents[0].file_data.file_uri == (
        "https://generativelanguage.googleapis.com/v1beta/files/temp-1"
    )
    assert contents[1] == _GEMINI_STT_USER_PROMPT
    assert isinstance(config, types.GenerateContentConfig)
    assert config.system_instruction == _GEMINI_STT_PROMPT_VERBATIM
    assert getattr(config, "thinking_config", None) is None


# ------------------------------------------------------------------------------
# Case 2: Model chứa 'transcribe' -> legacy files.upload + interactions.create + delete
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_transcribe_model_routes_to_legacy_interactions_api() -> None:
    fake_client, gen_mock, up_mock, del_mock, inter_mock = _make_mock_gemini_client()
    provider = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-transcribe",
        mode="smart",
        language_code="vi-VN",
        transport="transcribe_api",
    )
    provider._client = fake_client

    text = await provider.transcribe(b"RIFF-legacy", "audio/wav")

    assert text == "Kết quả legacy transcribe"
    gen_mock.assert_not_awaited()
    up_mock.assert_awaited_once()
    inter_mock.assert_awaited_once()
    del_mock.assert_awaited_once_with(name="files/temp-1")


# ------------------------------------------------------------------------------
# Case 3: Không có API key nhưng có Service Account file -> enterprise=True
# ------------------------------------------------------------------------------


def test_gemini_get_client_uses_service_account_when_api_key_missing(tmp_path: Path) -> None:
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(json.dumps({"project_id": "proj-from-json"}), encoding="utf-8")

    fake_creds = MagicMock(name="sa_credentials")
    fake_client = MagicMock(name="enterprise_genai_client")

    provider = GeminiSttProvider(
        api_key="",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
        credentials_file=sa_file,
        project="",
        location="global",
    )

    with (
        patch.object(
            voice_service_mod.service_account.Credentials,
            "from_service_account_file",
            return_value=fake_creds,
        ) as mock_from_sa,
        patch.object(
            voice_service_mod.genai,
            "Client",
            return_value=fake_client,
        ) as mock_client_cls,
    ):
        client = provider._get_client()

    assert client is fake_client
    mock_from_sa.assert_called_once_with(
        str(sa_file),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    mock_client_cls.assert_called_once()
    call_kwargs = mock_client_cls.call_args.kwargs
    assert call_kwargs["enterprise"] is True
    assert call_kwargs["credentials"] is fake_creds
    assert call_kwargs["project"] == "proj-from-json"
    assert call_kwargs["location"] == "global"
    assert call_kwargs["http_options"].retry_options.attempts == 1


def test_gemini_get_client_missing_both_credentials_raises_provider_unavailable() -> None:
    provider = GeminiSttProvider(
        api_key="",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
        credentials_file=None,
    )
    with pytest.raises(SttError) as exc_info:
        provider._get_client()
    assert exc_info.value.code == SttErrorCode.PROVIDER_UNAVAILABLE
    assert exc_info.value.detail == "missing_gemini_credentials"


# ------------------------------------------------------------------------------
# Case 4: Sentinel [NO_SPEECH] / [silence] / rỗng -> NO_SPEECH, không gọi Groq, HTTP 422
# ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_output",
    [
        "[NO_SPEECH]",
        "[NO_SPEECH].",
        "  [no_speech]  ",
        "[silence]",
        "[Inaudible]!",
        '"[NO_SPEECH]"',
        "```\n[NO_SPEECH]\n```",
        "",
        "   ",
    ],
)
@pytest.mark.asyncio
async def test_gemini_no_speech_sentinel_and_empty_raise_no_speech_without_groq_fallback(
    stt_http_client: httpx.AsyncClient, raw_output: str
) -> None:
    fake_client, _, _, _, _ = _make_mock_gemini_client(
        generate_content_mock=AsyncMock(return_value=SimpleNamespace(text=raw_output))
    )
    gemini = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
    )
    gemini._client = fake_client

    groq_create = AsyncMock(return_value={"text": "Không được gọi tới Groq"})
    groq = GroqWhisperSttProvider(
        api_key="test-groq-key",
        model="whisper-large-v3-turbo",
        language_code="vi-VN",
        timeout_seconds=5.0,
    )
    cast(Any, groq)._client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=groq_create))
    )

    service = VoiceService(
        FallbackSttProvider(
            primary=gemini,
            fallback=groq,
            prefer_fallback_purposes=frozenset({"uf5_checkin"}),
            primary_timeout_seconds=8.0,
            total_timeout_seconds=12.0,
        ),
        timeout_seconds=12.0,
        max_audio_bytes=1024 * 1024,
    )

    with pytest.raises(SttError) as exc_info:
        await service.transcribe(
            b"M4A-silence",
            content_type="audio/m4a",
            filename="silence.m4a",
            purpose="uf1_input",
        )
    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    groq_create.assert_not_awaited()

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await stt_http_client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"sentinel-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_inline"},
            files={"file": ("silence.m4a", b"M4A-silence", "audio/m4a")},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "NO_SPEECH_DETECTED"
    groq_create.assert_not_awaited()


# ------------------------------------------------------------------------------
# Case 5: Gemini lỗi 429 / 503 hoặc chậm > primary_timeout_seconds -> Groq 200
# ------------------------------------------------------------------------------


@pytest.mark.parametrize("failure_mode", ["429", "503", "slow_timeout"])
@pytest.mark.asyncio
async def test_gemini_transient_or_primary_timeout_falls_back_to_groq_preserving_low_confidence(
    stt_http_client: httpx.AsyncClient, failure_mode: str
) -> None:
    if failure_mode == "429":
        gen_mock = AsyncMock(side_effect=_SdkStatusError(429, "quota"))
    elif failure_mode == "503":
        gen_mock = AsyncMock(side_effect=_SdkStatusError(503, "unavailable"))
    else:

        async def _slow_generate(**_kwargs: Any) -> Any:
            await asyncio.sleep(0.3)
            return SimpleNamespace(text="Quá chậm")

        gen_mock = AsyncMock(side_effect=_slow_generate)

    fake_client, _, _, _, _ = _make_mock_gemini_client(generate_content_mock=gen_mock)
    gemini = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
    )
    gemini._client = fake_client

    groq_create = AsyncMock(
        return_value={
            "text": "Fallback Groq thành công",
            "segments": [{"no_speech_prob": 0.78, "avg_logprob": -0.4}],
        }
    )
    groq = GroqWhisperSttProvider(
        api_key="test-groq-key",
        model="whisper-large-v3-turbo",
        language_code="vi-VN",
        timeout_seconds=5.0,
    )
    cast(Any, groq)._client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=groq_create))
    )

    service = VoiceService(
        FallbackSttProvider(
            primary=gemini,
            fallback=groq,
            prefer_fallback_purposes=frozenset({"uf5_checkin"}),
            primary_timeout_seconds=0.05,
            total_timeout_seconds=2.0,
        ),
        timeout_seconds=2.0,
        max_audio_bytes=1024 * 1024,
    )

    res = await service.transcribe(
        b"M4A-audio",
        content_type="audio/m4a",
        filename="sample.m4a",
        purpose="uf1_input",
    )
    assert res.provider == "groq"
    assert res.text == "Fallback Groq thành công"
    assert res.low_confidence is True
    assert gen_mock.await_count == 1
    assert groq_create.await_count == 1

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await stt_http_client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"fallback-{failure_mode}-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_inline"},
            files={"file": ("sample.m4a", b"M4A-audio", "audio/m4a")},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["text"] == "Fallback Groq thành công"
    assert data["low_confidence"] is True


# ------------------------------------------------------------------------------
# Case 6: Purpose-aware routing (uf5_checkin vs uf1_input vs thought_to_draft)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purpose_aware_routing_default_and_disabled_flag() -> None:
    def _build_configured_service(prefer_groq_for_uf5: bool) -> tuple[VoiceService, AsyncMock, AsyncMock]:
        cfg = Settings(
            _env_file=None,
            VOICE_STT_PROVIDER="auto",
            GEMINI_API_KEY="test-gemini-key",
            GROQ_API_KEY="test-groq-key",
            VOICE_STT_UF5_PREFER_GROQ=prefer_groq_for_uf5,
        )
        svc = build_voice_service(cfg)
        assert isinstance(svc._provider, FallbackSttProvider)

        gemini_mock = AsyncMock(return_value="Gemini đã chép lời")
        groq_mock = AsyncMock(return_value="Groq đã chép lời")
        cast(Any, svc._provider._primary).transcribe = gemini_mock
        cast(Any, svc._provider._fallback).transcribe = groq_mock
        return svc, gemini_mock, groq_mock

    # Mặc định VOICE_STT_UF5_PREFER_GROQ=True: uf5_checkin -> Groq trước
    svc_default, gemini_mock, groq_mock = _build_configured_service(True)
    res_uf5 = await svc_default.transcribe(b"RIFF-uf5", content_type="audio/wav", purpose="uf5_checkin")
    assert res_uf5.provider == "groq"
    assert res_uf5.text == "Groq đã chép lời"
    groq_mock.assert_awaited_once()
    gemini_mock.assert_not_awaited()

    # uf1_input & thought_to_draft -> Gemini trước
    for purpose in ("uf1_input", "thought_to_draft"):
        svc_i, g_mock, q_mock = _build_configured_service(True)
        res = await svc_i.transcribe(b"RIFF-audio", content_type="audio/wav", purpose=purpose)
        assert res.provider == "gemini"
        assert res.text == "Gemini đã chép lời"
        assert res.low_confidence is False
        g_mock.assert_awaited_once()
        q_mock.assert_not_awaited()

    # VOICE_STT_UF5_PREFER_GROQ=False: uf5_checkin -> Gemini trước
    svc_disabled, gemini_mock_off, groq_mock_off = _build_configured_service(False)
    res_uf5_off = await svc_disabled.transcribe(
        b"RIFF-uf5", content_type="audio/wav", purpose="uf5_checkin"
    )
    assert res_uf5_off.provider == "gemini"
    assert res_uf5_off.text == "Gemini đã chép lời"
    gemini_mock_off.assert_awaited_once()
    groq_mock_off.assert_not_awaited()


# ------------------------------------------------------------------------------
# Case 7: Custom SttProvider chỉ có (audio, mime_type) chạy an toàn khi truyền purpose
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_stt_provider_without_purpose_param_works_safely() -> None:
    class _MinimalCustomProvider:
        name = "custom_minimal"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            assert audio == b"RIFF-custom"
            assert mime_type == "audio/wav"
            return "Kết quả từ custom provider"

    service = VoiceService(
        _MinimalCustomProvider(),
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )
    res = await service.transcribe(
        b"RIFF-custom",
        content_type="audio/wav",
        filename="custom.wav",
        purpose="uf5_checkin",
    )
    assert res.text == "Kết quả từ custom provider"
    assert res.provider == "custom_minimal"
    assert res.low_confidence is False


# ------------------------------------------------------------------------------
# Case 8: Router POST /api/v1/voice/transcriptions truyền đúng purpose vào service
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_passes_purpose_to_voice_service_and_supports_legacy_mock(
    stt_http_client: httpx.AsyncClient,
) -> None:
    captured_purposes: list[str | None] = []

    class _PurposeAwareService:
        async def transcribe(
            self,
            audio: bytes,
            *,
            content_type: str | None = None,
            filename: str | None = None,
            purpose: str | None = None,
        ) -> SttResult:
            captured_purposes.append(purpose)
            return SttResult(text=f"ok:{purpose}", provider="gemini", latency_ms=10)

    with patch.object(
        voice_router_mod, "get_voice_service", return_value=_PurposeAwareService()
    ):
        for expected_purpose in ("uf1_input", "uf5_checkin"):
            resp = await stt_http_client.post(
                "/api/v1/voice/transcriptions",
                headers={"Idempotency-Key": f"purpose-{expected_purpose}-{uuid4()}"},
                data={"purpose": expected_purpose, "device_id": "dev_inline"},
                files={"file": ("clip.wav", b"RIFF-clip", "audio/wav")},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["text"] == f"ok:{expected_purpose}"

    assert captured_purposes == ["uf1_input", "uf5_checkin"]

    # Mock cũ không có tham số purpose và không có **kwargs vẫn chạy an toàn
    class _LegacyMockService:
        async def transcribe(
            self,
            audio: bytes,
            *,
            content_type: str | None = None,
            filename: str | None = None,
        ) -> SttResult:
            return SttResult(text="legacy-mock-ok", provider="fake", latency_ms=1)

    with patch.object(voice_router_mod, "get_voice_service", return_value=_LegacyMockService()):
        resp_legacy = await stt_http_client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"legacy-mock-{uuid4()}"},
            data={"purpose": "uf5_checkin", "device_id": "dev_inline"},
            files={"file": ("clip.wav", b"RIFF-clip", "audio/wav")},
        )
    assert resp_legacy.status_code == 200, resp_legacy.text
    assert resp_legacy.json()["data"]["text"] == "legacy-mock-ok"


# ------------------------------------------------------------------------------
# Case 9: Settings defaults
# ------------------------------------------------------------------------------


def test_voice_stt_settings_defaults() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.VOICE_STT_TIMEOUT_SECONDS == 12.0
    assert cfg.VOICE_STT_PRIMARY_TIMEOUT_SECONDS == 8.0
    assert cfg.VOICE_STT_UF5_PREFER_GROQ is True
    assert cfg.VOICE_STT_PROVIDER == "auto"
    assert cfg.GEMINI_STT_MODEL == "gemini-3.5-transcribe"
    assert cfg.GEMINI_STT_TRANSPORT == "auto"
    assert cfg.GEMINI_STT_MODE == "verbatim"
    assert cfg.GROQ_STT_MODEL == "whisper-large-v3"


# ------------------------------------------------------------------------------
# Step 3: Echo guard & WAV PCM16 digital silence precheck
# ------------------------------------------------------------------------------


def _make_pcm16_wav(samples: list[int], *, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if samples:
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        else:
            wf.writeframes(b"")
    return buf.getvalue()


@pytest.mark.parametrize(
    "echo_text",
    [
        "Quy tắc",
        "Quy tắc:",
        "  quy tắc! ",
        "Chép lời đoạn ghi âm này",
        "Chép lời đoạn ghi âm này.",
        "Transcript:",
    ],
)
@pytest.mark.asyncio
async def test_gemini_prompt_echo_filtered_without_groq_fallback(echo_text: str) -> None:
    fake_client, _, _, _, _ = _make_mock_gemini_client(
        generate_content_mock=AsyncMock(return_value=SimpleNamespace(text=echo_text))
    )
    gemini = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
    )
    gemini._client = fake_client

    groq_create = AsyncMock(return_value={"text": "Không được gọi tới Groq"})
    groq = GroqWhisperSttProvider(
        api_key="test-groq-key",
        model="whisper-large-v3-turbo",
        language_code="vi-VN",
        timeout_seconds=5.0,
    )
    cast(Any, groq)._client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=groq_create))
    )

    service = VoiceService(
        FallbackSttProvider(
            primary=gemini,
            fallback=groq,
            prefer_fallback_purposes=frozenset({"uf5_checkin"}),
            primary_timeout_seconds=8.0,
            total_timeout_seconds=12.0,
        ),
        timeout_seconds=12.0,
        max_audio_bytes=1024 * 1024,
    )

    with pytest.raises(SttError) as exc_info:
        await service.transcribe(
            b"M4A-echo-test",
            content_type="audio/m4a",
            filename="echo.m4a",
            purpose="uf1_input",
        )
    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    assert exc_info.value.detail == "prompt_echo_filtered"
    groq_create.assert_not_awaited()


@pytest.mark.parametrize(
    "valid_short_text",
    [
        "5 trang",
        "19:00",
        "Sửa lại",
        "Thay vì",
        "À nhầm",
        "Tiếng Anh",
        "Tiếng Việt",
        "Không",
    ],
)
@pytest.mark.asyncio
async def test_gemini_short_real_transcripts_not_filtered_by_echo_guard(
    stt_http_client: httpx.AsyncClient, valid_short_text: str
) -> None:
    fake_client, _, _, _, _ = _make_mock_gemini_client(
        generate_content_mock=AsyncMock(return_value=SimpleNamespace(text=valid_short_text))
    )
    gemini = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
    )
    gemini._client = fake_client

    service = VoiceService(
        gemini,
        timeout_seconds=12.0,
        max_audio_bytes=1024 * 1024,
    )

    res = await service.transcribe(
        b"M4A-short-valid",
        content_type="audio/m4a",
        filename="short.m4a",
        purpose="uf1_input",
    )
    assert res.text == valid_short_text
    assert res.provider == "gemini"
    assert res.low_confidence is False

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await stt_http_client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"short-valid-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_inline"},
            files={"file": ("short.m4a", b"M4A-short-valid", "audio/m4a")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["text"] == valid_short_text
    assert resp.json()["data"]["low_confidence"] is False


@pytest.mark.parametrize(
    "silent_samples",
    [
        [0] * 1600,
        [0, 12, -32, 32, -5, 0],
        [],
    ],
)
@pytest.mark.asyncio
async def test_wav_pcm16_digital_silence_precheck_blocks_provider_call(
    silent_samples: list[int],
) -> None:
    wav_bytes = _make_pcm16_wav(silent_samples)
    provider_mock = AsyncMock(return_value="Không được gọi provider")

    class _SpyProvider:
        name = "gemini"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            return await provider_mock(audio, mime_type)

    service = VoiceService(
        _SpyProvider(),
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )

    with pytest.raises(SttError) as exc_info:
        await service.transcribe(
            wav_bytes,
            content_type="audio/wav",
            filename="silence.wav",
            purpose="uf1_input",
        )
    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    assert exc_info.value.detail == "digital_silence"
    provider_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_wav_pcm16_with_signal_above_threshold_calls_provider() -> None:
    wav_bytes = _make_pcm16_wav([0, 10, -33, 500, -1200, 0])
    provider_mock = AsyncMock(return_value="Làm bài tập môn học máy")

    class _SpyProvider:
        name = "gemini"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            return await provider_mock(audio, mime_type)

    service = VoiceService(
        _SpyProvider(),
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )

    res = await service.transcribe(
        wav_bytes,
        content_type="audio/wav",
        filename="speech.wav",
        purpose="uf1_input",
    )
    assert res.text == "Làm bài tập môn học máy"
    assert res.provider == "gemini"
    provider_mock.assert_awaited_once()


@pytest.mark.parametrize(
    "corrupt_wav",
    [
        b"RIFF\xff\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00",  # truncated fmt chunk
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\xff\x00\x00\x00\x00\x00",  # declared data > buffer
        b"NOT-A-WAV-HEADER-BYTES",
    ],
)
@pytest.mark.asyncio
async def test_wav_corrupt_header_skips_precheck_and_calls_provider(
    corrupt_wav: bytes,
) -> None:
    provider_mock = AsyncMock(return_value="Provider vẫn được gọi")

    class _SpyProvider:
        name = "gemini"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            return await provider_mock(audio, mime_type)

    service = VoiceService(
        _SpyProvider(),
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )

    res = await service.transcribe(
        corrupt_wav,
        content_type="audio/wav",
        filename="broken.wav",
        purpose="uf1_input",
    )
    assert res.text == "Provider vẫn được gọi"
    provider_mock.assert_awaited_once()
