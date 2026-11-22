"""Kiểm thử Phase 3 STT (P3-C1, P3-C2, P3-C3 & RISK-03):
- Mặc định VOICE_STT_PROVIDER='auto' -> build_voice_service trả 'gemini->groq' với GEMINI_STT_MODEL='gemini-3.5-flash-lite'.
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
    _is_whisper_hallucination,
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
    assert cfg.GEMINI_STT_MODEL == "gemini-3.5-flash-lite"

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
