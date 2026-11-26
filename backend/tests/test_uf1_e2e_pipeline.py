"""Kiểm thử tích hợp E2E HTTP 4 tầng cho luồng UF1 -> UF2 (Phase 5 — T5.1):
Voice Upload -> STT (Gemini 3.5 Transcribe / ElevenLabs Scribe v2 / Deepgram Nova-3 / Auto Failover / Noise Gate) -> Vietnamese ITN
-> UF1 Draft Create & Single-Round Analyze -> Confirm Summary -> UF2 Plan Candidate.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import delete

import app.services.ai_service as ai_service_module
import app.services.draft_service as draft_service_module
import app.services.plan_service as plan_service_module
from app.api.deps import get_current_user, get_db
from app.api.v1 import voice as voice_router_mod
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import Base, CommandReceipt, Thought, User, UserPreference
from app.models.planning import PlanCandidate as PlanCandidateModel
from app.models.planning import PlanningDraft as PlanningDraftModel
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMTaskDecompositionPayload,
    LLMTaskExtractionPayload,
    StepPromptItem,
)
from app.schemas.user import CurrentUser
from app.services.ai_service import AIService, ai_service
from app.services.voice_service import (
    FallbackSttProvider,
    GeminiSttProvider,
    SttError,
    SttErrorCode,
    SttProviderDetails,
    VoiceService,
    get_voice_service,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Thứ Tư 2026-11-18 09:00+07:00 để toàn bộ mốc "tối nay" (18/11) và hạn chót trong ngày tất định
FROZEN_NOW = datetime(2026, 11, 18, 9, 0, tzinfo=VN_TZ)
TONIGHT_DEADLINE = datetime(2026, 11, 18, 22, 0, tzinfo=VN_TZ)
TONIGHT_LATE_DEADLINE = datetime(2026, 11, 18, 23, 59, tzinfo=VN_TZ)


class _FrozenDatetime(datetime):
    """Đồng hồ giả lập cố định tại FROZEN_NOW cho toàn bộ tầng dịch vụ UF1/UF2."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


class _StatusCodeError(Exception):
    """Lỗi giả lập từ provider STT mang thuộc tính status_code (ví dụ 429 RATE_LIMITED)."""

    def __init__(self, status_code: int, message: str = "provider error") -> None:
        super().__init__(message)
        self.status_code = status_code


class _MockDetailsPrimaryProvider:
    """Adapter kiểm thử E2E chuyển payload giả lập thành SttProviderDetails cho VoiceService."""

    name = "elevenlabs"

    def __init__(self, create_mock: AsyncMock) -> None:
        self._create_mock = create_mock

    async def transcribe_verbose(
        self, audio: bytes, mime_type: str, *, contextual_hints: list[str] | None = None
    ) -> SttProviderDetails:
        _ = (audio, mime_type, contextual_hints)
        try:
            payload = await self._create_mock()
        except Exception as exc:
            if isinstance(exc, SttError):
                raise
            code = getattr(exc, "status_code", None)
            if code in (401, 403):
                raise SttError(SttErrorCode.PROVIDER_UNAVAILABLE, f"http_{code}") from exc
            if code == 429:
                raise SttError(SttErrorCode.RATE_LIMITED, "http_429") from exc
            if isinstance(code, int) and code >= 400:
                raise SttError(SttErrorCode.PROVIDER_ERROR, f"http_{code}") from exc
            if isinstance(exc, TimeoutError):
                raise SttError(SttErrorCode.TIMEOUT, "timeout") from exc
            raise SttError(SttErrorCode.PROVIDER_ERROR, type(exc).__name__) from exc

        segs = payload.get("segments") if isinstance(payload, dict) else getattr(payload, "segments", None)
        raw_text = payload.get("text") if isinstance(payload, dict) else getattr(payload, "text", None)
        text = str(raw_text or "").strip()
        if isinstance(segs, (list, tuple)) and segs:
            parsed_segs = [
                (
                    seg.get("no_speech_prob") if isinstance(seg, dict) else getattr(seg, "no_speech_prob", None),
                    seg.get("avg_logprob") if isinstance(seg, dict) else getattr(seg, "avg_logprob", None),
                )
                for seg in segs
            ]
            non_silent = [
                (nsp, alp)
                for nsp, alp in parsed_segs
                if not (nsp is not None and alp is not None and nsp > 0.85 and alp < -1.2)
            ]
            all_silent = len(non_silent) == 0
            target = parsed_segs if all_silent else non_silent
            nsps = [nsp for nsp, _ in target if nsp is not None]
            alps = [alp for _, alp in target if alp is not None]
            return SttProviderDetails(
                text=text,
                no_speech_prob=max(nsps) if nsps else None,
                avg_logprob=min(alps) if alps else None,
                all_segments_silent=all_silent,
            )
        return SttProviderDetails(text=text)

    async def transcribe(self, audio: bytes, mime_type: str, *, contextual_hints: list[str] | None = None) -> str:
        return (await self.transcribe_verbose(audio, mime_type, contextual_hints=contextual_hints)).text


def _build_auto_voice_service(
    primary_create_mock: AsyncMock,
    gemini_transcribe_mock: AsyncMock | None = None,
) -> tuple[VoiceService, AsyncMock]:
    """Khởi tạo VoiceService chế độ auto với mock provider details -> Gemini fallback."""
    primary_provider = _MockDetailsPrimaryProvider(primary_create_mock)
    gemini_provider = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-transcribe",
        mode="smart",
        language_code="vi-VN",
    )
    gemini_mock = gemini_transcribe_mock or AsyncMock(
        return_value="Fallback Gemini thành công"
    )
    gemini_provider.transcribe = gemini_mock  # type: ignore[method-assign]

    fallback = FallbackSttProvider(primary=primary_provider, fallback=gemini_provider)
    service = VoiceService(
        fallback,
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )
    return service, gemini_mock


@pytest.fixture
async def uf1_e2e_env(monkeypatch: pytest.MonkeyPatch):
    """Fixture E2E HTTP cô lập SQLite in-memory, user, đồng hồ và dependency overrides."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_MODE", "dev_user")
    monkeypatch.setenv("CALENDAR_PROVIDER_MODE", "fake")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    monkeypatch.setattr(settings, "CALENDAR_PROVIDER_MODE", "fake")

    monkeypatch.setattr(draft_service_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(ai_service_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(plan_service_module, "datetime", _FrozenDatetime)

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    user_id = uuid4()
    user_str = str(user_id)
    user_email = f"uf1_e2e_{user_id.hex[:10]}@example.com"

    async with factory() as db:
        db.add_all(
            [
                User(
                    id=user_id,
                    email=user_email,
                    display_name="UF1 E2E Tester",
                    is_active=True,
                ),
                UserPreference(
                    user_id=user_id,
                    work_interval_seconds=1500,
                    rest_interval_seconds=300,
                    buffer_multiplier=1.2,
                    effective_revision=1,
                    micro_task_seconds=120,
                    reminder_offsets=[900, 600, 300],
                    reminder_style="gentle",
                ),
            ]
        )
        await db.commit()

    voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
    voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
    draft_service_module._INFLIGHT_ANALYZE_LOCKS.clear()
    get_voice_service.cache_clear()

    active_voice_service: dict[str, VoiceService | None] = {"service": None}

    def set_voice_service(service: VoiceService) -> None:
        active_voice_service["service"] = service

    def _resolve_voice_service() -> VoiceService:
        assert active_voice_service["service"] is not None, "VoiceService chưa được gán trong test"
        return active_voice_service["service"]

    monkeypatch.setattr(voice_router_mod, "get_voice_service", _resolve_voice_service)

    async def override_db():
        async with factory() as db:
            yield db

    async def override_user():
        return CurrentUser(
            user_id=user_str,
            email=user_email,
            display_name="UF1 E2E Tester",
        )

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_voice_service] = _resolve_voice_service

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield {
                "client": client,
                "factory": factory,
                "user_id": user_id,
                "set_voice_service": set_voice_service,
            }
    finally:
        voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
        voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
        draft_service_module._INFLIGHT_ANALYZE_LOCKS.clear()
        get_voice_service.cache_clear()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await engine.dispose()


@pytest.mark.asyncio
async def test_case1_happy_path_4tier_e2e_voice_stt_itn_uf1_to_uf2_candidates(
    uf1_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 1: Happy Path 4-Tier E2E:
    Voice Upload -> STT auto verbose_json -> ITN -> UF1 /drafts + /analyze (RISK-02)
    -> /confirm-summary -> UF2 /candidates.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]
    factory = uf1_e2e_env["factory"]
    set_voice_service = uf1_e2e_env["set_voice_service"]

    raw_stt_transcript = (
        "Ờm tối nay viết báo cáo tuần 3 trang trước mười giờ tối, "
        "làm khoảng một tiếng rưỡi từ bảy rưỡi tối đến chín giờ tối"
    )
    primary_create = AsyncMock(
        return_value={
            "text": raw_stt_transcript,
            "segments": [{"no_speech_prob": 0.04, "avg_logprob": -0.18}],
        }
    )
    voice_service, gemini_mock = _build_auto_voice_service(primary_create)
    set_voice_service(voice_service)

    # 1. Tầng Voice / STT: Upload multipart audio lên POST /api/v1/voice/transcriptions
    stt_resp = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c1-stt-1"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("uf1_case1.wav", b"RIFF-case1-audio-payload", "audio/wav")},
    )
    assert stt_resp.status_code == 200, stt_resp.text
    stt_data = stt_resp.json()["data"]
    assert stt_data["text"] == raw_stt_transcript
    assert stt_data["input_source"] == "voice"
    assert stt_data["low_confidence"] is False
    primary_create.assert_awaited_once()
    gemini_mock.assert_not_awaited()

    # 2. Tầng UF1 Draft Creation: Tạo bản nháp từ kết quả STT
    create_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-c1-draft-create"},
        json={
            "client_draft_id": "client-draft-c1",
            "input_source": stt_data["input_source"],
            "raw_input": stt_data["text"],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]
    draft_id = draft["draft_id"]
    assert draft["raw_input"] == raw_stt_transcript
    assert draft["input_source"] == "voice"

    # 3. Tầng ITN + UF1 Analyze + UF2 Decomposition mocks
    captured_extract_inputs: list[dict[str, Any]] = []
    captured_uf1_prompts: list[dict[str, Any]] = []
    real_extract_task_intent = AIService.extract_task_intent

    async def _spy_extract_task_intent(user_input: str, **kwargs: Any):
        captured_extract_inputs.append({"user_input": user_input, **kwargs})
        return await real_extract_task_intent(ai_service, user_input, **kwargs)

    uf1_extraction_payload = LLMTaskExtractionPayload(
        title="Viết báo cáo tuần",
        content="Viết báo cáo tuần 3 trang",
        scope="3 trang báo cáo hoàn chỉnh",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=TONIGHT_DEADLINE,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=TONIGHT_DEADLINE,
        free_time_slots=["2026-11-18 19:30-21:00"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local="19:30",
                end_local="21:00",
                raw_phrase="tối nay từ 19:30 đến 21:00",
            )
        ],
        is_complete=True,
        summary="Viết báo cáo tuần 3 trang, rảnh 19:30–21:00 tối nay, hạn 22:00.",
    )
    uf2_decomposition_payload = LLMTaskDecompositionPayload(
        task_title="Viết báo cáo tuần",
        steps=[
            StepPromptItem(
                order_index=1,
                title="Tổng hợp số liệu và lập dàn ý báo cáo tuần",
                expected_output="Dàn ý chi tiết và bảng số liệu cho 3 trang báo cáo",
                estimated_minutes=25,
                micro_task_prompt="Mở tài liệu báo cáo tuần và ghi 3 tiêu đề mục chính (2 phút).",
            ),
            StepPromptItem(
                order_index=2,
                title="Viết nội dung hoàn chỉnh 3 trang báo cáo tuần",
                expected_output="Bản báo cáo tuần 3 trang đã rà soát lỗi chính tả",
                estimated_minutes=25,
                micro_task_prompt="Viết câu mở đầu cho mục kết quả tuần này (2 phút).",
            ),
        ],
        requires_clarification=False,
        reasoning="Chia làm 2 bước gọn trong 90 phút tối nay giúp bạn khởi động nhẹ nhàng.",
    )

    async def _fake_hedged_structured(**kwargs: Any) -> tuple[Any, str]:
        flow_name = kwargs.get("flow_name")
        if flow_name == "UF1":
            captured_uf1_prompts.append(json.loads(kwargs["user_prompt"]))
            return uf1_extraction_payload, "tier_1_gemini"
        if flow_name == "UF2":
            return uf2_decomposition_payload, "tier_1_gemini"
        raise AssertionError(f"Unexpected flow_name: {flow_name}")

    monkeypatch.setattr(ai_service, "extract_task_intent", _spy_extract_task_intent)
    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged_structured)

    analyze_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": "e2e-c1-analyze-1"},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": stt_data["text"],
        },
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    analyzed = analyze_resp.json()["data"]

    # Kiểm chứng ITN đã chuẩn hóa văn bản đầu vào cho AI extractor
    assert len(captured_extract_inputs) == 1
    normalized_text = captured_extract_inputs[0]["user_input"]
    assert not normalized_text.lower().startswith("ờm")
    assert "19:30" in normalized_text
    assert "21:00" in normalized_text
    assert "22:00" in normalized_text
    assert "90 phút" in normalized_text
    assert captured_uf1_prompts[0]["user_raw_input"] == normalized_text

    # Kiểm chứng RISK-02: draft.raw_input trả về qua API và lưu trong DB giữ nguyên 100% bản gốc STT
    assert analyzed["raw_input"] == raw_stt_transcript
    async with factory() as db:
        draft_row = await db.get(
            PlanningDraftModel,
            draft_service_module.resolve_draft_uuid(draft_id),
        )
        assert draft_row is not None
        assert draft_row.raw_input == raw_stt_transcript

    assert analyzed["summary"]["can_plan"] is True
    assert not [i for i in analyzed["summary"]["clarification_issues"] if i["blocking"]]

    # 4. Tầng UF1 Confirm Summary -> Chuyển sang UF2
    confirm_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/confirm-summary",
        headers={"Idempotency-Key": "e2e-c1-confirm-1"},
        json={"expected_summary_revision": analyzed["summary_revision"]},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    confirm_data = confirm_resp.json()["data"]
    assert confirm_data["stage"] == "summary_confirmed"
    assert confirm_data["summary_confirmed_revision"] == analyzed["summary_revision"]

    # 5. Tầng UF2 Generate Candidate: Chia bước & xếp phiên tập trung
    candidate_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "e2e-c1-candidate-1"},
        json={
            "source_summary_revision": analyzed["summary_revision"],
            "source_planning_input_revision": analyzed["planning_input_revision"],
        },
    )
    assert candidate_resp.status_code == 201, candidate_resp.text
    candidate = candidate_resp.json()["data"]
    assert candidate["draft_id"] == draft_id
    assert len(candidate["steps"]) == 2
    assert len(candidate["sessions"]) >= 2
    assert candidate["feasibility"]["status"] == "feasible"
    assert candidate["feasibility"]["fully_scheduled"] is True

    async with factory() as db:
        cand_row = await db.get(PlanCandidateModel, UUID(candidate["candidate_id"]))
        assert cand_row is not None
        assert cand_row.generation_tier == "tier_1_gemini"


@pytest.mark.asyncio
async def test_case2_turn2_duration_synthesis_and_long_title_over_100_chars(
    uf1_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 2: E2E Turn 2 Duration Synthesis + Long Title > 100 chars (P1-C1 + P1-C2 + RISK-01 + ITN):
    - Turn 1: Câu nói dài > 100 ký tự có khung giờ mở 'tối nay từ bảy giờ rưỡi' và title ban đầu > 100 ký tự.
      Không lỗi 422 string_too_long, title sau phân tích <= 100 ký tự, yêu cầu làm rõ thời lượng/giờ kết thúc.
    - Turn 2: Trả lời ngắn 'tầm tiếng rưỡi' -> ITN chuẩn hóa thành 'tầm 90 phút' -> tổng hợp thành 19:30–21:00
      và can_plan=True.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]
    set_voice_service = uf1_e2e_env["set_voice_service"]

    long_spoken_turn1 = (
        "Ờm tối nay mình cần hoàn thành báo cáo phân tích kiến trúc hệ thống phân tán "
        "và tổng hợp số liệu kiểm thử hiệu năng chi tiết dài 4 trang Word trước 23h59, "
        "mình rảnh tối nay từ bảy giờ rưỡi tối"
    )
    assert len(long_spoken_turn1) > 100

    primary_create = AsyncMock(
        return_value={
            "text": long_spoken_turn1,
            "segments": [{"no_speech_prob": 0.08, "avg_logprob": -0.22}],
        }
    )
    voice_service, _ = _build_auto_voice_service(primary_create)
    set_voice_service(voice_service)

    stt_resp = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c2-stt-1"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("uf1_case2.wav", b"RIFF-case2-audio", "audio/wav")},
    )
    assert stt_resp.status_code == 200, stt_resp.text
    transcript_turn1 = stt_resp.json()["data"]["text"]
    assert len(transcript_turn1) > 100

    # Tạo draft với summary.title dài > 100 ký tự để kiểm chứng P1-C2 / RISK-01
    create_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-c2-draft-create"},
        json={
            "client_draft_id": "client-draft-c2",
            "input_source": "voice",
            "raw_input": transcript_turn1,
            "summary": {
                "title": transcript_turn1,
            },
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]
    draft_id = draft["draft_id"]
    assert len(draft["summary"]["title"]) > 100

    turn1_payload = LLMTaskExtractionPayload(
        title="Báo cáo phân tích kiến trúc hệ thống phân tán và kiểm thử hiệu năng",
        content="Hoàn thành báo cáo phân tích kiến trúc hệ thống phân tán và tổng hợp số liệu kiểm thử hiệu năng",
        scope="4 trang Word",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=TONIGHT_LATE_DEADLINE,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=TONIGHT_LATE_DEADLINE,
        free_time_slots=["tối nay từ 19:30"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local="19:30",
                end_local=None,
                raw_phrase="tối nay từ 19:30",
            )
        ],
        is_complete=False,
        summary="Báo cáo 4 trang Word hạn 23:59 tối nay, rảnh từ 19:30 nhưng chưa rõ kết thúc lúc mấy giờ.",
    )
    assert turn1_payload.structured_free_windows[0].is_ambiguous is True

    # Lượt 2: Giả lập LLM chỉ trả lại thông tin cũ và bỏ sót structured_free_windows=[]
    # để kiểm chứng P1-C1 tự tổng hợp thời lượng từ câu trả lời ngắn đã qua ITN ("tầm tiếng rưỡi" -> "tầm 90 phút")
    turn2_payload = LLMTaskExtractionPayload(
        title="Báo cáo phân tích kiến trúc hệ thống phân tán và kiểm thử hiệu năng",
        content="Hoàn thành báo cáo phân tích kiến trúc hệ thống phân tán và tổng hợp số liệu kiểm thử hiệu năng",
        scope="4 trang Word",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=TONIGHT_LATE_DEADLINE,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=TONIGHT_LATE_DEADLINE,
        availability_update_mode="merge",
        structured_free_windows=[],
        is_complete=False,
        summary="Báo cáo 4 trang Word hạn 23:59 tối nay.",
    )

    queue = [turn1_payload, turn2_payload]
    captured_calls: list[dict[str, Any]] = []
    real_extract = AIService.extract_task_intent

    async def _spy_extract(user_input: str, **kwargs: Any):
        captured_calls.append({"user_input": user_input, **kwargs})
        return await real_extract(ai_service, user_input, **kwargs)

    async def _fake_hedged(**kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return queue.pop(0), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "extract_task_intent", _spy_extract)
    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged)

    # Lượt 1: POST /api/v1/drafts/{id}/analyze
    analyze1_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": "e2e-c2-analyze-turn1"},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": transcript_turn1,
        },
    )
    assert analyze1_resp.status_code == 200, analyze1_resp.text
    analyzed1 = analyze1_resp.json()["data"]
    assert "19:30" in captured_calls[0]["user_input"]
    assert analyzed1["summary"]["title"] is not None
    assert len(analyzed1["summary"]["title"]) <= 100
    assert (
        analyzed1["summary"]["title"]
        == "Báo cáo phân tích kiến trúc hệ thống phân tán và kiểm thử hiệu năng"
    )
    assert analyzed1["summary"]["can_plan"] is False
    avail_issues = [
        i
        for i in analyzed1["summary"]["clarification_issues"]
        if i["field_path"] == "availability" and i["blocking"]
    ]
    assert len(avail_issues) == 1

    # Lượt 2: POST /api/v1/drafts/{id}/analyze với message="tầm tiếng rưỡi"
    analyze2_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": "e2e-c2-analyze-turn2"},
        json={
            "expected_summary_revision": analyzed1["summary_revision"],
            "expected_planning_input_revision": analyzed1["planning_input_revision"],
            "message": "tầm tiếng rưỡi",
        },
    )
    assert analyze2_resp.status_code == 200, analyze2_resp.text
    analyzed2 = analyze2_resp.json()["data"]

    # Kiểm chứng ITN đã đổi "tầm tiếng rưỡi" -> "tầm 90 phút" và ghép thành 19:30–21:00
    assert captured_calls[1]["followup_message"] == "tầm 90 phút"
    assert analyzed2["summary"]["can_plan"] is True
    assert not [i for i in analyzed2["summary"]["clarification_issues"] if i["blocking"]]

    overrides = {
        item["date"]: [
            (iv["start_local"][:5], iv["end_local"][:5]) for iv in item["intervals"]
        ]
        for item in analyzed2["summary"]["availability"]["date_overrides"]
    }
    assert overrides.get("2026-11-18") == [("19:30", "21:00")]


@pytest.mark.asyncio
async def test_case3_low_confidence_replay_auto_failover_and_noise_gate(
    uf1_e2e_env: dict[str, Any],
) -> None:
    """Case 3: E2E low_confidence=True, Auto Failover & Noise Gate:
    - 3A: Audio nhiễu (no_speech_prob=0.78, avg_logprob=-0.6) -> HTTP 200, low_confidence=True,
          bảo toàn qua Idempotency-Key replay (cả in-memory cache và DB CommandReceipt).
    - 3B: Nhánh chính 429 RATE_LIMITED tự động failover sang Gemini -> HTTP 200, low_confidence=False,
          tiếp tục tạo bản nháp POST /api/v1/drafts thành công.
    - 3C: Audio im lặng / ảo giác (NO_SPEECH) -> HTTP 422 NO_SPEECH_DETECTED và KHÔNG gọi Gemini fallback.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]
    factory = uf1_e2e_env["factory"]
    user_id: UUID = uf1_e2e_env["user_id"]
    set_voice_service = uf1_e2e_env["set_voice_service"]

    # --- 3A: Noisy audio -> low_confidence=True & Idempotency replay (in-memory + DB) ---
    noisy_primary = AsyncMock(
        return_value={
            "text": "Ôn tập giải thuật quy hoạch động tối thứ 5",
            "segments": [{"no_speech_prob": 0.78, "avg_logprob": -0.6}],
        }
    )
    noisy_service, noisy_gemini = _build_auto_voice_service(noisy_primary)
    set_voice_service(noisy_service)

    noisy_audio = b"RIFF-noisy-audio-case3a"
    idem_mem = "e2e-c3-low-conf-mem"
    resp_noisy = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_mem},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("noisy.wav", noisy_audio, "audio/wav")},
    )
    assert resp_noisy.status_code == 200, resp_noisy.text
    data_noisy = resp_noisy.json()["data"]
    assert data_noisy["low_confidence"] is True
    assert data_noisy["text"] == "Ôn tập giải thuật quy hoạch động tối thứ 5"
    assert noisy_primary.await_count == 1
    noisy_gemini.assert_not_awaited()

    # Xóa DB CommandReceipt để ép replay đi qua nhánh _VOICE_IDEMPOTENCY_STORE in-memory
    async with factory() as db:
        await db.execute(delete(CommandReceipt).where(CommandReceipt.user_id == user_id))
        await db.commit()

    resp_mem_replay = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_mem},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("noisy.wav", noisy_audio, "audio/wav")},
    )
    assert resp_mem_replay.status_code == 200, resp_mem_replay.text
    assert resp_mem_replay.json()["data"] == data_noisy
    assert resp_mem_replay.json()["data"]["low_confidence"] is True
    assert noisy_primary.await_count == 1

    # Tạo lượt mới để lưu DB CommandReceipt rồi xóa _VOICE_IDEMPOTENCY_STORE -> ép replay từ DB
    idem_db = "e2e-c3-low-conf-db"
    resp_db_init = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_db},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("noisy.wav", noisy_audio, "audio/wav")},
    )
    assert resp_db_init.status_code == 200, resp_db_init.text
    assert resp_db_init.json()["data"]["low_confidence"] is True
    assert noisy_primary.await_count == 2

    voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
    resp_db_replay = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_db},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("noisy.wav", noisy_audio, "audio/wav")},
    )
    assert resp_db_replay.status_code == 200, resp_db_replay.text
    assert resp_db_replay.json()["data"] == resp_db_init.json()["data"]
    assert resp_db_replay.json()["data"]["low_confidence"] is True
    assert noisy_primary.await_count == 2

    # --- 3B: Nhánh chính 429 RATE_LIMITED -> Auto Failover sang Gemini -> POST /api/v1/drafts ---
    rate_limited_primary = AsyncMock(side_effect=_StatusCodeError(429, "rate_limited"))
    gemini_fallback_mock = AsyncMock(
        return_value="Soạn slide thuyết trình đồ án 10 trang trước tối thứ 6"
    )
    failover_service, _ = _build_auto_voice_service(
        rate_limited_primary,
        gemini_transcribe_mock=gemini_fallback_mock,
    )
    set_voice_service(failover_service)

    resp_failover = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c3-failover-429"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("failover.wav", b"RIFF-failover-429", "audio/wav")},
    )
    assert resp_failover.status_code == 200, resp_failover.text
    failover_data = resp_failover.json()["data"]
    assert failover_data["text"] == "Soạn slide thuyết trình đồ án 10 trang trước tối thứ 6"
    assert failover_data["low_confidence"] is False
    assert failover_data["input_source"] == "voice"
    rate_limited_primary.assert_awaited_once()
    gemini_fallback_mock.assert_awaited_once()

    draft_from_failover = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-c3-draft-from-failover"},
        json={
            "client_draft_id": "client-draft-c3-failover",
            "input_source": failover_data["input_source"],
            "raw_input": failover_data["text"],
        },
    )
    assert draft_from_failover.status_code == 201, draft_from_failover.text
    assert (
        draft_from_failover.json()["data"]["raw_input"]
        == "Soạn slide thuyết trình đồ án 10 trang trước tối thứ 6"
    )

    # --- 3C: Hallucination / Silence audio (NO_SPEECH) -> HTTP 422, KHÔNG gọi Gemini ---
    hallucination_primary = AsyncMock(
        return_value={
            "text": "Hãy subscribe cho kênh ghiền mì gõ",
            "segments": [{"no_speech_prob": 0.12, "avg_logprob": -0.25}],
        }
    )
    hallucination_service, hallucination_gemini = _build_auto_voice_service(hallucination_primary)
    set_voice_service(hallucination_service)

    resp_hallucination = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c3-no-speech-hallucination"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("silence.wav", b"RIFF-silence-hallucination", "audio/wav")},
    )
    assert resp_hallucination.status_code == 422, resp_hallucination.text
    assert resp_hallucination.json()["error"]["code"] == "NO_SPEECH_DETECTED"
    hallucination_primary.assert_awaited_once()
    hallucination_gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_case4_concurrent_analyze_draft_single_flight_lock_over_http(
    uf1_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 4: E2E Concurrent analyze_draft Single-Flight Lock (P1-C3) over HTTP:
    Hai request HTTP đồng thời (asyncio.gather) tới POST /api/v1/drafts/{id}/analyze
    cùng Idempotency-Key đều trả về HTTP 200 với payload giống hệt nhau,
    trong khi ai_service.extract_task_intent chỉ được gọi đúng 1 lần duy nhất.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]

    create_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-c4-create"},
        json={
            "client_draft_id": "client-draft-c4",
            "input_source": "voice",
            "raw_input": "Ờm tối nay viết báo cáo 3 trang từ bảy giờ tối đến chín giờ tối",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]
    draft_id = draft["draft_id"]

    extract_call_count = 0
    real_extract = AIService.extract_task_intent

    extraction_payload = LLMTaskExtractionPayload(
        title="Viết báo cáo 3 trang",
        content="Viết báo cáo 3 trang",
        scope="3 trang báo cáo",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=TONIGHT_DEADLINE,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=TONIGHT_DEADLINE,
        free_time_slots=["2026-11-18 19:00-21:00"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="tối nay từ 19:00 đến 21:00",
            )
        ],
        is_complete=True,
        summary="Viết báo cáo 3 trang tối nay 19:00–21:00.",
    )

    async def _slow_hedged(**kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        await asyncio.sleep(0.05)
        return extraction_payload, "tier_1_gemini"

    async def _counting_extract(user_input: str, **kwargs: Any):
        nonlocal extract_call_count
        extract_call_count += 1
        return await real_extract(ai_service, user_input, **kwargs)

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _slow_hedged)
    monkeypatch.setattr(ai_service, "extract_task_intent", _counting_extract)

    analyze_body = {
        "expected_summary_revision": draft["summary_revision"],
        "expected_planning_input_revision": draft["planning_input_revision"],
        "message": "Ờm tối nay viết báo cáo 3 trang từ bảy giờ tối đến chín giờ tối, hạn mười giờ tối nay",
    }
    shared_headers = {"Idempotency-Key": "e2e-c4-concurrent-analyze-key"}

    resp1, resp2 = await asyncio.gather(
        client.post(
            f"/api/v1/drafts/{draft_id}/analyze",
            headers=shared_headers,
            json=analyze_body,
        ),
        client.post(
            f"/api/v1/drafts/{draft_id}/analyze",
            headers=shared_headers,
            json=analyze_body,
        ),
    )

    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text
    assert resp1.json()["data"] == resp2.json()["data"]
    assert resp1.json()["data"]["summary"]["can_plan"] is True
    assert extract_call_count == 1
    assert len(draft_service_module._INFLIGHT_ANALYZE_LOCKS) == 0


@pytest.mark.asyncio
async def test_case5_protected_discourse_and_adverb_integrity_e2e(
    uf1_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 5: Protected Discourse & Adverb Integrity E2E:
    ITN chuẩn hóa 'bảy rưỡi tối'/'chín rưỡi tối' -> '19:30'/'21:30' trước extract_task_intent,
    đồng thời bảo toàn nguyên vẹn 'À nhầm', 'tiếng Anh', 'từ từ', 'ngày ngày' trong đầu vào LLM
    và giữ 100% raw_input gốc trong DB.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]
    factory = uf1_e2e_env["factory"]
    set_voice_service = uf1_e2e_env["set_voice_service"]

    raw_transcript = (
        "À nhầm, tối nay ôn tiếng Anh từ từ thôi "
        "từ bảy rưỡi tối đến chín rưỡi tối, ngày ngày ôn tập đều đặn"
    )
    primary_create = AsyncMock(
        return_value={
            "text": raw_transcript,
            "segments": [{"no_speech_prob": 0.05, "avg_logprob": -0.19}],
        }
    )
    voice_service, gemini_mock = _build_auto_voice_service(primary_create)
    set_voice_service(voice_service)

    stt_resp = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c5-stt-1"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("uf1_case5.wav", b"RIFF-case5-audio", "audio/wav")},
    )
    assert stt_resp.status_code == 200, stt_resp.text
    stt_data = stt_resp.json()["data"]
    assert stt_data["text"] == raw_transcript
    assert stt_data["low_confidence"] is False
    assert stt_data["input_source"] == "voice"
    primary_create.assert_awaited_once()
    gemini_mock.assert_not_awaited()

    create_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-c5-draft-create"},
        json={
            "client_draft_id": "client-draft-c5",
            "input_source": stt_data["input_source"],
            "raw_input": stt_data["text"],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]
    draft_id = draft["draft_id"]

    captured_extract_inputs: list[dict[str, Any]] = []
    captured_uf1_prompts: list[dict[str, Any]] = []
    real_extract_task_intent = AIService.extract_task_intent

    async def _spy_extract_task_intent(user_input: str, **kwargs: Any):
        captured_extract_inputs.append({"user_input": user_input, **kwargs})
        return await real_extract_task_intent(ai_service, user_input, **kwargs)

    extraction_payload = LLMTaskExtractionPayload(
        title="Ôn tiếng Anh từ từ tối nay",
        content="Ôn tiếng Anh từ từ, ngày ngày ôn tập đều đặn",
        scope="Ôn tập tiếng Anh đều đặn trong buổi tối",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=TONIGHT_DEADLINE,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=TONIGHT_DEADLINE,
        free_time_slots=["2026-11-18 19:30-21:30"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local="19:30",
                end_local="21:30",
                raw_phrase="tối nay từ 19:30 đến 21:30",
            )
        ],
        is_complete=True,
        summary="Ôn tiếng Anh tối nay 19:30–21:30, hạn 22:00.",
    )

    async def _fake_hedged_structured(**kwargs: Any) -> tuple[Any, str]:
        assert kwargs.get("flow_name") == "UF1"
        captured_uf1_prompts.append(json.loads(kwargs["user_prompt"]))
        return extraction_payload, "tier_1_gemini"

    monkeypatch.setattr(ai_service, "extract_task_intent", _spy_extract_task_intent)
    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged_structured)

    analyze_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": "e2e-c5-analyze-1"},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": stt_data["text"],
        },
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    analyzed = analyze_resp.json()["data"]

    assert len(captured_extract_inputs) == 1
    assert len(captured_uf1_prompts) == 1
    normalized_text = captured_extract_inputs[0]["user_input"]
    llm_raw_input = captured_uf1_prompts[0]["user_raw_input"]
    assert normalized_text == llm_raw_input

    assert "19:30" in normalized_text
    assert "21:30" in normalized_text
    assert "bảy rưỡi tối" not in normalized_text.lower()
    assert "chín rưỡi tối" not in normalized_text.lower()

    for protected in ("À nhầm", "tiếng Anh", "từ từ", "ngày ngày"):
        assert protected in normalized_text
        assert protected in llm_raw_input

    assert analyzed["raw_input"] == raw_transcript
    assert analyzed["summary"]["can_plan"] is True

    async with factory() as db:
        draft_row = await db.get(
            PlanningDraftModel,
            draft_service_module.resolve_draft_uuid(draft_id),
        )
        assert draft_row is not None
        assert draft_row.raw_input == raw_transcript
        for protected in ("À nhầm", "tiếng Anh", "từ từ", "ngày ngày"):
            assert protected in draft_row.raw_input


@pytest.mark.asyncio
async def test_case6_multi_segment_speech_plus_trailing_silence_e2e(
    uf1_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 6: Multi-Segment Speech + Trailing Silence E2E:
    Segment 1 có lời nói rõ (no_speech_prob=0.1, avg_logprob=-0.2) + Segment 2 im lặng cuối câu
    (no_speech_prob=0.92, avg_logprob=-1.5) -> HTTP 200, low_confidence=False, chạy trọn vẹn
    /drafts -> /analyze -> /confirm-summary -> /candidates.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]
    factory = uf1_e2e_env["factory"]
    set_voice_service = uf1_e2e_env["set_voice_service"]

    spoken_transcript = (
        "Tối nay hoàn thành 5 bài tập giải tích trước mười giờ tối, "
        "làm khoảng một tiếng rưỡi từ bảy rưỡi tối đến chín giờ tối"
    )
    primary_create = AsyncMock(
        return_value={
            "text": spoken_transcript,
            "segments": [
                {"no_speech_prob": 0.1, "avg_logprob": -0.2},
                {"no_speech_prob": 0.92, "avg_logprob": -1.5},
            ],
        }
    )
    voice_service, gemini_mock = _build_auto_voice_service(primary_create)
    set_voice_service(voice_service)

    stt_resp = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c6-stt-1"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("uf1_case6.wav", b"RIFF-case6-multi-segment", "audio/wav")},
    )
    assert stt_resp.status_code == 200, stt_resp.text
    stt_data = stt_resp.json()["data"]
    assert stt_data["text"] == spoken_transcript
    assert stt_data["low_confidence"] is False
    assert stt_data["input_source"] == "voice"
    primary_create.assert_awaited_once()
    gemini_mock.assert_not_awaited()

    create_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-c6-draft-create"},
        json={
            "client_draft_id": "client-draft-c6",
            "input_source": stt_data["input_source"],
            "raw_input": stt_data["text"],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]
    draft_id = draft["draft_id"]

    uf1_payload = LLMTaskExtractionPayload(
        title="Làm 5 bài tập giải tích",
        content="Hoàn thành 5 bài tập giải tích",
        scope="5 bài tập giải tích",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=TONIGHT_DEADLINE,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=TONIGHT_DEADLINE,
        free_time_slots=["2026-11-18 19:30-21:00"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local="19:30",
                end_local="21:00",
                raw_phrase="tối nay từ 19:30 đến 21:00",
            )
        ],
        is_complete=True,
        summary="Làm 5 bài tập giải tích tối nay 19:30–21:00, hạn 22:00.",
    )
    uf2_payload = LLMTaskDecompositionPayload(
        task_title="Làm 5 bài tập giải tích",
        steps=[
            StepPromptItem(
                order_index=1,
                title="Giải 3 bài tập tích phân đầu tiên",
                expected_output="Lời giải hoàn chỉnh bài 1–3",
                estimated_minutes=25,
                micro_task_prompt="Đọc đề bài 1 và viết công thức áp dụng (2 phút).",
            ),
            StepPromptItem(
                order_index=2,
                title="Giải 2 bài tập còn lại và kiểm tra đáp án",
                expected_output="Lời giải hoàn chỉnh đủ 5 bài giải tích",
                estimated_minutes=25,
                micro_task_prompt="Chép đề bài 4 ra giấy nháp (2 phút).",
            ),
        ],
        requires_clarification=False,
        reasoning="Chia 5 bài thành 2 chặng ngắn giúp bạn giữ nhịp tập trung đều trong tối nay.",
    )

    async def _fake_hedged_structured(**kwargs: Any) -> tuple[Any, str]:
        flow_name = kwargs.get("flow_name")
        if flow_name == "UF1":
            return uf1_payload, "tier_1_gemini"
        if flow_name == "UF2":
            return uf2_payload, "tier_1_gemini"
        raise AssertionError(f"Unexpected flow_name: {flow_name}")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged_structured)

    analyze_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": "e2e-c6-analyze-1"},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": stt_data["text"],
        },
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    analyzed = analyze_resp.json()["data"]
    assert analyzed["summary"]["can_plan"] is True
    assert not [i for i in analyzed["summary"]["clarification_issues"] if i["blocking"]]

    confirm_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/confirm-summary",
        headers={"Idempotency-Key": "e2e-c6-confirm-1"},
        json={"expected_summary_revision": analyzed["summary_revision"]},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["data"]["stage"] == "summary_confirmed"

    candidate_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "e2e-c6-candidate-1"},
        json={
            "source_summary_revision": analyzed["summary_revision"],
            "source_planning_input_revision": analyzed["planning_input_revision"],
        },
    )
    assert candidate_resp.status_code == 201, candidate_resp.text
    candidate = candidate_resp.json()["data"]
    assert candidate["draft_id"] == draft_id
    assert len(candidate["steps"]) == 2
    assert len(candidate["sessions"]) >= 2
    assert candidate["feasibility"]["status"] == "feasible"
    assert candidate["feasibility"]["fully_scheduled"] is True

    async with factory() as db:
        cand_row = await db.get(PlanCandidateModel, UUID(candidate["candidate_id"]))
        assert cand_row is not None
        assert cand_row.generation_tier == "tier_1_gemini"


@pytest.mark.asyncio
async def test_case7_thought_to_draft_vs_uf1_input_purpose_isolation_e2e(
    uf1_e2e_env: dict[str, Any],
) -> None:
    """Case 7: E2E Purpose Isolation (`thought_to_draft` vs `uf1_input`):
    - `purpose="thought_to_draft"` thiếu `thought_id` -> 422 THOUGHT_ID_REQUIRED.
    - Có `Thought` hợp lệ trong DB + `thought_id` -> 200, `input_source="thought"`, `thought_id=...`.
    - `purpose="uf1_input"` -> 200, `input_source="voice"`, `thought_id=None`.
    """
    client: httpx.AsyncClient = uf1_e2e_env["client"]
    factory = uf1_e2e_env["factory"]
    user_id: UUID = uf1_e2e_env["user_id"]
    set_voice_service = uf1_e2e_env["set_voice_service"]

    transcript_text = "Ghi chú ý tưởng làm slide thuyết trình cuối kỳ"
    primary_create = AsyncMock(
        return_value={
            "text": transcript_text,
            "segments": [{"no_speech_prob": 0.05, "avg_logprob": -0.15}],
        }
    )
    voice_service, gemini_mock = _build_auto_voice_service(primary_create)
    set_voice_service(voice_service)

    # 7A. thought_to_draft thiếu thought_id -> 422 THOUGHT_ID_REQUIRED
    resp_missing_thought = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c7-missing-thought"},
        data={"purpose": "thought_to_draft", "device_id": "mobile_uf4"},
        files={"file": ("thought_missing.wav", b"RIFF-case7-missing", "audio/wav")},
    )
    assert resp_missing_thought.status_code == 422, resp_missing_thought.text
    err_payload = resp_missing_thought.json()["error"]
    assert err_payload["code"] == "VALIDATION_ERROR"
    assert any(
        fe["field_path"] == "thought_id" and fe["code"] == "THOUGHT_ID_REQUIRED"
        for fe in err_payload["field_errors"]
    )
    assert primary_create.await_count == 0
    gemini_mock.assert_not_awaited()

    # 7B. Tạo bản ghi Thought thật trong DB -> thought_to_draft trả về input_source="thought"
    thought_uuid = uuid4()
    async with factory() as db:
        db.add(
            Thought(
                id=thought_uuid,
                user_id=user_id,
                client_thought_id="client-thought-e2e-c7",
                source_run_id=None,
                input_type="audio",
                local_audio_path="/tmp/thought_c7.m4a",
                text_content=None,
                attempt_count=1,
                capture_state="saved",
            )
        )
        await db.commit()

    resp_thought = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c7-thought-ok"},
        data={
            "purpose": "thought_to_draft",
            "thought_id": str(thought_uuid),
            "device_id": "mobile_uf4",
        },
        files={"file": ("thought_ok.wav", b"RIFF-case7-thought-ok", "audio/wav")},
    )
    assert resp_thought.status_code == 200, resp_thought.text
    thought_data = resp_thought.json()["data"]
    assert thought_data["text"] == transcript_text
    assert thought_data["input_source"] == "thought"
    assert thought_data["thought_id"] == str(thought_uuid)
    assert thought_data["low_confidence"] is False
    assert primary_create.await_count == 1

    # 7C. purpose="uf1_input" -> input_source="voice", thought_id=None
    resp_uf1 = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": "e2e-c7-uf1-ok"},
        data={"purpose": "uf1_input", "device_id": "mobile_uf1"},
        files={"file": ("uf1_ok.wav", b"RIFF-case7-uf1-ok", "audio/wav")},
    )
    assert resp_uf1.status_code == 200, resp_uf1.text
    uf1_data = resp_uf1.json()["data"]
    assert uf1_data["text"] == transcript_text
    assert uf1_data["input_source"] == "voice"
    assert uf1_data["thought_id"] is None
    assert uf1_data["low_confidence"] is False
    assert primary_create.await_count == 2
    gemini_mock.assert_not_awaited()
