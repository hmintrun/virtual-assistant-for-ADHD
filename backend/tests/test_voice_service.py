"""Kiểm thử Phase 3 STT (P3-C1, P3-C2, P3-C3 & RISK-03):
- Mặc định VOICE_STT_PROVIDER='auto' -> build_voice_service trả 'gemini->groq' với GEMINI_STT_MODEL='gemini-3.5-transcribe'.
- Contextual biasing prompt (_DEFAULT_STT_PROMPT) chứa từ khóa ngày/giờ/ngoại lệ tiếng Việt.
- Groq verbose_json: phát hiện hallucination & lọc im lặng âm học -> SttError(NO_SPEECH) / HTTP 422 NO_SPEECH_DETECTED,
  KHÔNG kích hoạt Gemini fallback trong mode auto.
- Câu hợp lệ không bị lọc nhầm (false positive).
- Ngưỡng low_confidence (no_speech_prob > 0.7 hoặc avg_logprob < -1.0) -> HTTP 200 + low_confidence=True,
  bảo toàn qua cả in-memory _VOICE_IDEMPOTENCY_STORE hit và DB command_receipts hit.
- Segment dạng dict & attribute-style với chỉ số sạch -> HTTP 200 + low_confidence=False.
- Failover tự động Groq (RATE_LIMITED / TIMEOUT / PROVIDER_ERROR) -> Gemini -> HTTP 200 + low_confidence=False.
- Tương thích ngược RISK-03: TranscriptionData.model_validate khi thiếu trường low_confidence.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_current_user, get_db
from app.api.v1 import voice as voice_router_mod
from app.core.config import Settings
from app.core.database import make_engine
from app.main import app
from app.models import Base, CommandReceipt, User, UserPreference
from app.schemas.user import CurrentUser
from app.schemas.voice import TranscriptionData
from app.services import command_service
from app.services.voice_service import (
    _DEFAULT_STT_PROMPT,
    FallbackSttProvider,
    GeminiSttProvider,
    GroqWhisperSttProvider,
    SttError,
    SttErrorCode,
    VoiceService,
    _ensure_groq_compatible_audio,
    _is_adts_aac,
    _is_whisper_hallucination,
    _remux_adts_aac_to_m4a,
    build_voice_service,
    get_voice_service,
)


class _StatusCodeError(Exception):
    """Lỗi giả lập từ SDK Groq mang thuộc tính status_code."""

    def __init__(self, status_code: int, message: str = "provider error") -> None:
        super().__init__(message)
        self.status_code = status_code


def _build_auto_voice_service(
    groq_create_mock: AsyncMock,
    gemini_transcribe_mock: AsyncMock | None = None,
) -> tuple[VoiceService, AsyncMock]:
    """Tạo VoiceService chế độ auto (Groq -> Gemini) dùng mock ở lớp SDK/provider."""
    groq_provider = GroqWhisperSttProvider(
        api_key="test-groq-key",
        model="whisper-large-v3-turbo",
        language_code="vi-VN",
        timeout_seconds=5.0,
    )
    groq_provider._client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=groq_create_mock),
        )
    )
    gemini_provider = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-transcribe",
        mode="smart",
        language_code="vi-VN",
    )
    gemini_mock = gemini_transcribe_mock or AsyncMock(return_value="Fallback Gemini thành công")
    gemini_provider.transcribe = gemini_mock  # type: ignore[method-assign]

    fallback = FallbackSttProvider(primary=groq_provider, fallback=gemini_provider)
    service = VoiceService(
        fallback,
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )
    return service, gemini_mock


@pytest.fixture
async def voice_api_client():
    """Client HTTP cô lập DB SQLite in-memory, user và cache idempotency cho từng test."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    user_id = uuid4()
    user_str = str(user_id)
    user_email = f"voice_test_{user_id.hex[:10]}@example.com"

    voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
    voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
    get_voice_service.cache_clear()

    async with factory() as db:
        db.add_all(
            [
                User(
                    id=user_id,
                    email=user_email,
                    display_name="Voice Tester",
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
        return CurrentUser(user_id=user_str, email=user_email, display_name="Voice Tester")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client, factory, user_id
    finally:
        voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
        voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
        get_voice_service.cache_clear()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


def test_default_settings_auto_provider_and_vietnamese_prompt_keywords() -> None:
    """Kiểm tra mặc định VOICE_STT_PROVIDER='auto', provider_name='gemini->groq' và từ khóa prompt."""
    cfg = Settings(_env_file=None)
    assert cfg.VOICE_STT_PROVIDER == "auto"
    assert cfg.GEMINI_STT_MODEL == "gemini-3.5-transcribe"

    configured = Settings(
        _env_file=None,
        GEMINI_API_KEY="test-gemini-key",
        GROQ_API_KEY="test-groq-key",
    )
    service = build_voice_service(configured)
    assert service.provider_name == "gemini->groq"

    expected_keywords = (
        "Thứ 2",
        "Thứ 3",
        "Thứ 4",
        "Thứ 5",
        "Thứ 6",
        "Thứ 7",
        "Chủ nhật",
        "19h30",
        "tiếng rưỡi",
        "rảnh",
        "bận",
        "trừ",
        "ngoại lệ",
    )
    for keyword in expected_keywords:
        assert keyword in _DEFAULT_STT_PROMPT


@pytest.mark.asyncio
async def test_groq_verbose_json_sends_prompt_and_temperature_zero() -> None:
    """GroqWhisperSttProvider.transcribe_verbose gửi response_format='verbose_json', temperature=0.0 và prompt."""
    groq_create = AsyncMock(
        return_value={
            "text": "Họp nhóm thứ 5 lúc 19h30",
            "segments": [{"no_speech_prob": 0.05, "avg_logprob": -0.2}],
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    result = await service.transcribe(
        b"RIFF-audio-bytes",
        content_type="audio/wav",
        filename="sample.wav",
    )

    assert result.text == "Họp nhóm thứ 5 lúc 19h30"
    assert result.provider == "groq"
    assert result.low_confidence is False
    gemini_mock.assert_not_awaited()

    groq_create.assert_awaited_once()
    kwargs = groq_create.await_args.kwargs
    assert kwargs["response_format"] == "verbose_json"
    assert kwargs["temperature"] == 0.0
    assert kwargs["language"] == "vi"
    assert kwargs["prompt"] == _DEFAULT_STT_PROMPT


@pytest.mark.parametrize(
    "hallucinated_text",
    [
        "Hãy subscribe cho kênh ghiền mì gõ",
        "Cảm ơn các bạn đã xem",
        "Subtitles by Amara.org",
        "cảm ơn cảm ơn cảm ơn cảm ơn",
        "ừ ừ ừ ừ",
        "Hẹn gặp lại các bạn ở video sau nhé!",
        "cảm ơn các bạn, cảm ơn các bạn, cảm ơn các bạn, cảm ơn các bạn",
    ],
)
@pytest.mark.asyncio
async def test_groq_hallucinations_raise_no_speech_and_return_422_without_gemini_fallback(
    voice_api_client, hallucinated_text: str
) -> None:
    """Các câu ảo giác Whisper -> SttError(NO_SPEECH) và HTTP 422 NO_SPEECH_DETECTED, không gọi Gemini."""
    assert _is_whisper_hallucination(hallucinated_text) is True
    client, _, _ = voice_api_client

    groq_create = AsyncMock(
        return_value={
            "text": hallucinated_text,
            "segments": [{"no_speech_prob": 0.1, "avg_logprob": -0.25}],
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    with pytest.raises(SttError) as exc_info:
        await service.transcribe(b"dummy-audio", content_type="audio/wav", filename="h.wav")
    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    assert exc_info.value.detail == "hallucination_filtered"
    gemini_mock.assert_not_awaited()

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"hallucination-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("silence.wav", b"RIFF-silence", "audio/wav")},
        )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "NO_SPEECH_DETECTED"
    gemini_mock.assert_not_awaited()


@pytest.mark.parametrize(
    "valid_text",
    [
        "Mình cảm ơn bạn và hẹn gặp thứ 5 từ 19h30 đến 21h",
        "Làm 2 bài báo cáo và slide",
        "ừ ừ ừ",
        "Thứ 2 đến thứ 6 mình bận giờ hành chính, trừ thứ 4 rảnh buổi tối",
    ],
)
@pytest.mark.asyncio
async def test_non_hallucination_sentences_pass_through(
    voice_api_client, valid_text: str
) -> None:
    """Câu nói bình thường chứa 'cảm ơn' hoặc lặp < 4 lần không bị lọc nhầm hallucination."""
    assert _is_whisper_hallucination(valid_text) is False
    client, _, _ = voice_api_client

    groq_create = AsyncMock(
        return_value={
            "text": valid_text,
            "segments": [{"no_speech_prob": 0.12, "avg_logprob": -0.3}],
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"valid-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("speech.wav", b"RIFF-speech", "audio/wav")},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["text"] == valid_text
    assert data["low_confidence"] is False
    gemini_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_groq_acoustic_rejection_returns_422_without_gemini_fallback(
    voice_api_client,
) -> None:
    """no_speech_prob=0.92 & avg_logprob=-1.4 -> HTTP 422 NO_SPEECH_DETECTED, không fallback Gemini."""
    client, _, _ = voice_api_client

    groq_create = AsyncMock(
        return_value={
            "text": "alo",
            "segments": [{"no_speech_prob": 0.92, "avg_logprob": -1.4}],
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    with pytest.raises(SttError) as exc_info:
        await service.transcribe(b"RIFF-noise", content_type="audio/wav", filename="noise.wav")
    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    assert exc_info.value.detail == "high_no_speech_prob"
    gemini_mock.assert_not_awaited()

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"acoustic-reject-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("noise.wav", b"RIFF-noise", "audio/wav")},
        )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "NO_SPEECH_DETECTED"
    gemini_mock.assert_not_awaited()


@pytest.mark.parametrize(
    ("no_speech_prob", "avg_logprob"),
    [
        (0.75, -0.5),
        (0.3, -1.05),
    ],
)
@pytest.mark.asyncio
async def test_groq_low_confidence_and_idempotent_replay_preserves_flag(
    voice_api_client, no_speech_prob: float, avg_logprob: float
) -> None:
    """low_confidence=True trả 200 OK và giữ nguyên qua cả in-memory cache lẫn DB command_receipts."""
    client, factory, user_id = voice_api_client

    groq_create = AsyncMock(
        return_value={
            "text": "Làm đề cương khóa luận trước tối thứ 6",
            "segments": [{"no_speech_prob": no_speech_prob, "avg_logprob": avg_logprob}],
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    audio_bytes = b"RIFF-low-conf-audio"
    key_mem = f"low-conf-mem-{uuid4()}"

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp1 = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": key_mem},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("low.wav", audio_bytes, "audio/wav")},
        )
        assert resp1.status_code == 200, resp1.text
        data1 = resp1.json()["data"]
        assert data1["text"] == "Làm đề cương khóa luận trước tối thứ 6"
        assert data1["low_confidence"] is True
        assert groq_create.await_count == 1

        # Xóa DB CommandReceipt nhưng giữ _VOICE_IDEMPOTENCY_STORE để kiểm tra nhánh in-memory hit.
        async with factory() as db:
            await db.execute(delete(CommandReceipt).where(CommandReceipt.user_id == user_id))
            await db.commit()
        assert (str(user_id), key_mem) in voice_router_mod._VOICE_IDEMPOTENCY_STORE

        resp_mem_replay = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": key_mem},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("low.wav", audio_bytes, "audio/wav")},
        )
        assert resp_mem_replay.status_code == 200, resp_mem_replay.text
        assert resp_mem_replay.json()["data"] == data1
        assert resp_mem_replay.json()["data"]["low_confidence"] is True
        assert groq_create.await_count == 1

        # Bây giờ gọi với key mới để ghi DB CommandReceipt, sau đó xóa RAM cache -> kiểm tra DB hit.
        key_db = f"low-conf-db-{uuid4()}"
        resp2 = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": key_db},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("low.wav", audio_bytes, "audio/wav")},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["data"]["low_confidence"] is True
        assert groq_create.await_count == 2

        voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
        resp_db_replay = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": key_db},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("low.wav", audio_bytes, "audio/wav")},
        )
        assert resp_db_replay.status_code == 200, resp_db_replay.text
        assert resp_db_replay.json()["data"] == resp2.json()["data"]
        assert resp_db_replay.json()["data"]["low_confidence"] is True
        assert groq_create.await_count == 2

    gemini_mock.assert_not_awaited()


@pytest.mark.parametrize("segment_style", ["dict", "attr"])
@pytest.mark.asyncio
async def test_groq_clean_metrics_dict_and_attr_segments_low_confidence_false(
    voice_api_client, segment_style: str
) -> None:
    """Segment dạng dict lẫn attribute-style với chỉ số sạch -> 200 OK và low_confidence=False."""
    client, _, _ = voice_api_client

    if segment_style == "dict":
        payload = {
            "text": "Ôn thi môn học máy 2 tiếng mỗi tối trừ chủ nhật",
            "segments": [
                {"no_speech_prob": 0.05, "avg_logprob": -0.2},
                {"no_speech_prob": 0.1, "avg_logprob": -0.3},
            ],
        }
    else:
        payload = SimpleNamespace(
            text="Ôn thi môn học máy 2 tiếng mỗi tối trừ chủ nhật",
            segments=[
                SimpleNamespace(no_speech_prob=0.05, avg_logprob=-0.2),
                SimpleNamespace(no_speech_prob=0.1, avg_logprob=-0.3),
            ],
        )

    groq_create = AsyncMock(return_value=payload)
    service, gemini_mock = _build_auto_voice_service(groq_create)

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"clean-{segment_style}-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("clean.wav", b"RIFF-clean", "audio/wav")},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["text"] == "Ôn thi môn học máy 2 tiếng mỗi tối trừ chủ nhật"
    assert data["low_confidence"] is False
    gemini_mock.assert_not_awaited()


@pytest.mark.parametrize(
    "groq_exception",
    [
        SttError(SttErrorCode.PROVIDER_UNAVAILABLE, "missing_groq_api_key"),
        _StatusCodeError(401, "unauthorized"),
        SttError(SttErrorCode.RATE_LIMITED, "http_429"),
        _StatusCodeError(429, "rate_limited"),
        SttError(SttErrorCode.TIMEOUT, "ReadTimeout"),
        TimeoutError("groq timeout"),
        SttError(SttErrorCode.PROVIDER_ERROR, "http_503"),
        _StatusCodeError(503, "upstream_503"),
    ],
)
@pytest.mark.asyncio
async def test_auto_failover_groq_retryable_errors_to_gemini(
    voice_api_client, groq_exception: Exception
) -> None:
    """Khi Groq gặp PROVIDER_UNAVAILABLE / RATE_LIMITED / TIMEOUT / PROVIDER_ERROR, auto failover sang Gemini 200 OK."""
    client, _, _ = voice_api_client

    groq_create = AsyncMock(side_effect=groq_exception)
    gemini_mock = AsyncMock(return_value="Soạn slide báo cáo trước thứ 6 lúc 20h")
    service, _ = _build_auto_voice_service(groq_create, gemini_transcribe_mock=gemini_mock)

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"failover-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("failover.wav", b"RIFF-failover", "audio/wav")},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["text"] == "Soạn slide báo cáo trước thứ 6 lúc 20h"
    assert data["low_confidence"] is False
    groq_create.assert_awaited_once()
    gemini_mock.assert_awaited_once()


@pytest.mark.parametrize(
    ("segments", "expected_status", "expected_low_confidence"),
    [
        (
            [
                {"no_speech_prob": 0.1, "avg_logprob": -0.2},
                {"no_speech_prob": 0.9, "avg_logprob": -1.5},
            ],
            200,
            False,
        ),
        (
            [
                {"no_speech_prob": 0.1, "avg_logprob": -0.2},
                {"no_speech_prob": 0.75, "avg_logprob": -0.5},
            ],
            200,
            True,
        ),
        (
            [
                {"no_speech_prob": 0.9, "avg_logprob": -0.5},
                {"no_speech_prob": 0.1, "avg_logprob": -1.3},
            ],
            200,
            True,
        ),
        (
            [
                {"no_speech_prob": 0.9, "avg_logprob": -1.3},
                {"no_speech_prob": 0.95, "avg_logprob": -1.6},
            ],
            422,
            None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_groq_multisegment_silence_and_confidence_aggregation(
    voice_api_client,
    segments: list[dict[str, float]],
    expected_status: int,
    expected_low_confidence: bool | None,
) -> None:
    """Đánh giá im lặng theo từng segment và tính low_confidence trên các segment không im lặng."""
    client, _, _ = voice_api_client
    groq_create = AsyncMock(
        return_value={
            "text": "Làm báo cáo môn học máy trước thứ 6",
            "segments": segments,
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"multiseg-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("multiseg.wav", b"RIFF-multiseg", "audio/wav")},
        )

    assert resp.status_code == expected_status, resp.text
    if expected_status == 200:
        data = resp.json()["data"]
        assert data["text"] == "Làm báo cáo môn học máy trước thứ 6"
        assert data["low_confidence"] is expected_low_confidence
    else:
        assert resp.json()["error"]["code"] == "NO_SPEECH_DETECTED"
    gemini_mock.assert_not_awaited()


@pytest.mark.parametrize("punct_text", ["...", ", . ! ?", "…"])
@pytest.mark.asyncio
async def test_groq_punctuation_only_artifact_returns_422_without_gemini_fallback(
    voice_api_client, punct_text: str
) -> None:
    """Chuỗi chỉ có dấu câu (vd '...') -> SttError(NO_SPEECH) và HTTP 422 NO_SPEECH_DETECTED, không fallback Gemini."""
    assert _is_whisper_hallucination(punct_text) is True
    client, _, _ = voice_api_client

    groq_create = AsyncMock(
        return_value={
            "text": punct_text,
            "segments": [{"no_speech_prob": 0.1, "avg_logprob": -0.2}],
        }
    )
    service, gemini_mock = _build_auto_voice_service(groq_create)

    with pytest.raises(SttError) as exc_info:
        await service.transcribe(b"RIFF-punct", content_type="audio/wav", filename="punct.wav")
    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    assert exc_info.value.detail == "empty_transcript"
    gemini_mock.assert_not_awaited()

    with patch.object(voice_router_mod, "get_voice_service", return_value=service):
        resp = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": f"punct-{uuid4()}"},
            data={"purpose": "uf1_input", "device_id": "dev_p3"},
            files={"file": ("punct.wav", b"RIFF-punct", "audio/wav")},
        )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "NO_SPEECH_DETECTED"
    gemini_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_risk03_legacy_receipt_without_low_confidence_backward_compatible(
    voice_api_client,
) -> None:
    """RISK-03: Schema và DB replay chấp nhận payload cũ không có low_confidence -> mặc định False."""
    legacy = TranscriptionData.model_validate(
        {"text": "abc", "input_source": "voice", "thought_id": None}
    )
    assert legacy.text == "abc"
    assert legacy.input_source == "voice"
    assert legacy.thought_id is None
    assert legacy.low_confidence is False

    client, factory, user_id = voice_api_client
    audio_bytes = b"RIFF-legacy-receipt"
    idem_key = f"legacy-receipt-{uuid4()}"
    fingerprint = json.dumps(
        {
            "purpose": "uf1_input",
            "device_id": "dev_p3",
            "thought_id": None,
            "filename": "legacy.wav",
            "content_type": "audio/wav",
            "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
        },
        sort_keys=True,
    )
    digest = command_service.command_hash("voice_transcription", "", json.loads(fingerprint))

    async with factory() as db:
        db.add(
            CommandReceipt(
                user_id=user_id,
                command_id=idem_key,
                command_type="voice_transcription",
                payload_hash=digest,
                state="applied",
                result_reference={
                    "_response": {
                        "text": "abc",
                        "input_source": "voice",
                        "thought_id": None,
                    }
                },
            )
        )
        await db.commit()

    resp = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_key},
        data={"purpose": "uf1_input", "device_id": "dev_p3"},
        files={"file": ("legacy.wav", audio_bytes, "audio/wav")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {
        "text": "abc",
        "input_source": "voice",
        "thought_id": None,
        "low_confidence": False,
    }


# ==============================================================================
# Trụ cột 1a — Pure-Python ADTS (.aac -> .m4a) Remuxer
# ==============================================================================


def _build_synthetic_adts_stream(
    *,
    profile_minus_1: int = 1,
    freq_idx: int = 4,
    chan_cfg: int = 1,
    payloads: tuple[bytes, ...] = (b"\x21\x10\x04\x60\x8c\x1c", b"\x21\x10\x04\x60\x8c\x1d"),
    include_id3v2: bool = False,
) -> bytes:
    """Tạo luồng ADTS AAC hợp lệ thuần byte cho unit test (không phụ thuộc file ngoài)."""
    frames = bytearray()
    for raw_frame in payloads:
        frame_len = 7 + len(raw_frame)
        header = bytearray(7)
        header[0] = 0xFF
        header[1] = 0xF1  # MPEG-4 ADTS, protection_absent=1
        header[2] = ((profile_minus_1 & 0x03) << 6) | ((freq_idx & 0x0F) << 2) | ((chan_cfg >> 2) & 0x01)
        header[3] = ((chan_cfg & 0x03) << 6) | ((frame_len >> 11) & 0x03)
        header[4] = (frame_len >> 3) & 0xFF
        header[5] = ((frame_len & 0x07) << 5) | 0x1F
        header[6] = 0xFC
        frames.extend(header)
        frames.extend(raw_frame)

    if include_id3v2:
        tag_body = b"TIT2\x00\x00\x00\x04\x00\x00\x00abc"
        size = len(tag_body)
        syncsafe = bytes(
            [
                (size >> 21) & 0x7F,
                (size >> 14) & 0x7F,
                (size >> 7) & 0x7F,
                size & 0x7F,
            ]
        )
        return b"ID3\x03\x00\x00" + syncsafe + tag_body + bytes(frames)
    return bytes(frames)


def _find_mp4_box(data: bytes, target_type: bytes) -> tuple[int, int] | None:
    """Tìm atom ISO BMFF cấp trên trong buffer, trả về (start_offset, box_size)."""
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos : pos + 4], "big")
        box_type = data[pos + 4 : pos + 8]
        if size < 8:
            break
        if box_type == target_type:
            return pos, size
        pos += size
    return None


def test_adts_aac_detection_and_pure_python_m4a_remuxer() -> None:
    aac_44k = _build_synthetic_adts_stream(freq_idx=4, chan_cfg=1, include_id3v2=True)
    aac_16k = _build_synthetic_adts_stream(freq_idx=8, chan_cfg=1, include_id3v2=False)

    assert _is_adts_aac(aac_44k) is True
    assert _is_adts_aac(aac_16k) is True
    assert _is_adts_aac(b"RIFF\x24\x00\x00\x00WAVE") is False

    for freq_idx, raw_aac, expected_asc in (
        (4, aac_44k, bytes([0x12, 0x08])),  # AAC-LC (2), 44100Hz (4), mono (1)
        (8, aac_16k, bytes([0x14, 0x08])),  # AAC-LC (2), 16000Hz (8), mono (1)
    ):
        remuxed, mime = _ensure_groq_compatible_audio(raw_aac, "audio/aac")
        assert mime == "audio/m4a"
        assert remuxed[:8] == int.to_bytes(_find_mp4_box(remuxed, b"ftyp")[1], 4, "big") + b"ftyp"
        assert b"M4A " in remuxed[:24]
        for box_name in (b"moov", b"mvhd", b"trak", b"mdia", b"hdlr", b"soun", b"stsd", b"mp4a", b"esds", b"stco", b"mdat"):
            assert box_name in remuxed, (freq_idx, box_name)
        assert expected_asc in remuxed

        # Kiểm tra offset stco trỏ chính xác vào đầu payload của mdat
        mdat_pos, _ = _find_mp4_box(remuxed, b"mdat")
        stco_idx = remuxed.index(b"stco")
        stco_offset = int.from_bytes(remuxed[stco_idx + 12 : stco_idx + 16], "big")
        assert stco_offset == mdat_pos + 8

        # Kiểm tra không còn header ADTS lọt vào bên trong payload mdat
        mdat_payload = remuxed[stco_offset:]
        assert mdat_payload == b"\x21\x10\x04\x60\x8c\x1c\x21\x10\x04\x60\x8c\x1d"


def test_adts_remuxer_high_sample_rates_96khz_88_2khz_and_7_1_channel_mapping() -> None:
    # freq_idx=0 -> 96000Hz, chan_cfg=7 -> 7.1 (8 channels)
    aac_96k_7_1 = _build_synthetic_adts_stream(freq_idx=0, chan_cfg=7)
    # freq_idx=1 -> 88200Hz, chan_cfg=0 -> fallback 2 channels in mp4a
    aac_88k_cfg0 = _build_synthetic_adts_stream(freq_idx=1, chan_cfg=0)

    for raw_aac, expected_sr, expected_ch, expected_asc in (
        (aac_96k_7_1, 96000, 8, bytes([0x10, 0x38])),
        (aac_88k_cfg0, 88200, 2, bytes([0x10, 0x80])),
    ):
        remuxed, mime = _ensure_groq_compatible_audio(raw_aac, "audio/aac")
        assert mime == "audio/m4a"
        assert expected_asc in remuxed

        # mp4a: offset +8 (header) +6 (reserved) +2 (dref_idx) +8 (reserved) = +24 -> channels (uint16)
        # offset +32 -> samplerate 16.16 fixed-point capped at 65535 << 16
        mp4a_idx = remuxed.index(b"mp4a") - 4
        channels_written = int.from_bytes(remuxed[mp4a_idx + 24 : mp4a_idx + 26], "big")
        sr_fixed_written = int.from_bytes(remuxed[mp4a_idx + 32 : mp4a_idx + 36], "big")
        assert channels_written == expected_ch
        assert sr_fixed_written == (65535 << 16)

        # mdhd (FullBox v0): box_start + 12 (header+ver/flags) + 8 (ctime+mtime) = +20 -> timescale (uint32)
        mdhd_idx = remuxed.index(b"mdhd") - 4
        mdhd_timescale = int.from_bytes(remuxed[mdhd_idx + 20 : mdhd_idx + 24], "big")
        assert mdhd_timescale == expected_sr


def test_adts_remuxer_malformed_bytes_fallback_safely() -> None:
    malformed = b"NOT-VALID-ADTS-BYTES"
    out_bytes, out_mime = _ensure_groq_compatible_audio(malformed, "audio/aac")
    assert out_bytes == malformed
    assert out_mime == "audio/aac"

    with pytest.raises(ValueError):
        _remux_adts_aac_to_m4a(b"\xFF\xF1\x50")


@pytest.mark.asyncio
async def test_groq_whisper_transcribe_verbose_remuxes_adts_aac_and_safely_passes_malformed() -> None:
    observed_files: list[tuple[str, bytes, str]] = []

    async def _fake_create(**kwargs: Any) -> dict[str, Any]:
        file_arg = kwargs["file"]
        observed_files.append((file_arg[0], file_arg[1], file_arg[2]))
        return {
            "text": "Họp nhóm lúc 19:00 tối mai",
            "segments": [{"no_speech_prob": 0.05, "avg_logprob": -0.2}],
        }

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=AsyncMock(side_effect=_fake_create)))
    )
    provider = GroqWhisperSttProvider(
        api_key="gsk_test",
        model="whisper-large-v3",
        language_code="vi-VN",
        timeout_seconds=5.0,
    )
    provider._client = fake_client

    adts_audio = _build_synthetic_adts_stream(freq_idx=8, chan_cfg=1)
    res_ok = await provider.transcribe_verbose(adts_audio, "audio/aac")
    assert res_ok.text == "Họp nhóm lúc 19:00 tối mai"
    assert observed_files[0][0] == "audio.m4a"
    assert observed_files[0][2] == "audio/m4a"
    assert _find_mp4_box(observed_files[0][1], b"ftyp") is not None

    # Nếu byte audio giả/lỗi không đúng ADTS -> không crash, truyền nguyên gốc
    bad_audio = b"synthetic-invalid-aac-bytes"
    await provider.transcribe_verbose(bad_audio, "audio/aac")
    assert observed_files[1][0] == "audio.aac"
    assert observed_files[1][1] == bad_audio
    assert observed_files[1][2] == "audio/aac"
