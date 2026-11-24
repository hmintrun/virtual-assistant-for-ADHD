"""Kiểm thử toàn diện Phase 2 (P1b + P3 + P6 — Zero Overfitting):
1. UserVocabularyService: cache LRU + TTL + invalidate, async SQLite (Task.title,
   PlanningDraft.summary['title'], PlanningContext.event_snapshots), lọc PII & stopword, fail-open.
2. VoiceService & POST /api/v1/voice/transcriptions: truyền contextual_hints an toàn (<= 400 ký tự),
   không sửa AudioTranscriptionConfig của transcribe_inline, tương thích ngược 100% fake/mock STT cũ.
3. ReALM Self-Repair Schema: turn_intent & abandoned_phrases defaults, lọc TemporalAnchor bị tự hủy
   ở _enforce_uf1_temporal_guardrails (bảo vệ khi trùng hoàn toàn toàn bộ câu nói).
4. ReALM Multi-Turn Slot Preservation (T10 -> T11): Turn 1 phân vân Scope vẫn lưu time_constraint
   & availability, chặn khi còn 'hay'/'hoặc', Turn 2 chốt 5 trang -> can_plan=True; fail-safe blocking=True.
5. Zone 2 Medium Confidence Check (AI_DEADLINE_CONFIRMATION_NEEDED): kích hoạt cho 'hẹn tối thứ Bảy nhé'
   khi thiếu deadline, KHÔNG kích hoạt cho 'hẹn tối thứ Sáu gặp khách hàng' hay 'sau buổi hẹn tối mai với bác sĩ'.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from google.genai import types
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.ai_service as ai_service_module
import app.services.draft_service as draft_service_module
from app.api.deps import get_current_user, get_db
from app.api.v1 import voice as voice_router_mod
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import Base, Thought, User, UserPreference
from app.models.calendar import PlanningContext as PlanningContextModel
from app.models.planning import PlanningDraft as PlanningDraftModel
from app.models.task import Task as TaskModel
from app.schemas.common import ClarificationIssue
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMTaskExtractionPayload,
    TaskExtractionResult,
)
from app.schemas.user import CurrentUser
from app.services.ai_service import UF1_EXTRACTION_SYSTEM_PROMPT, AIService, ai_service
from app.services.user_vocabulary_service import (
    UserVocabularyService,
    extract_salient_vocabulary_hints,
    get_user_vocabulary_service,
)
from app.services.voice_service import (
    _DEFAULT_STT_PROMPT,
    _GEMINI_STT_PROMPT_SMART,
    _GEMINI_STT_USER_PROMPT,
    FallbackSttProvider,
    GeminiSttProvider,
    GroqWhisperSttProvider,
    VoiceService,
    _format_contextual_hints,
    get_voice_service,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Thứ Tư 2026-11-18 09:00+07:00 (Thứ Năm=19/11, Thứ Sáu=20/11, Thứ Bảy=21/11, Chủ nhật=22/11)
FROZEN_NOW = datetime(2026, 11, 18, 9, 0, tzinfo=VN_TZ)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> Any:  # type: ignore[override]
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


@pytest.fixture
async def phase2_env(monkeypatch: pytest.MonkeyPatch):
    """Môi trường HTTP + Async SQLite cô lập cho kiểm thử Phase 2."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_MODE", "dev_user")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    monkeypatch.setattr(draft_service_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(ai_service_module, "datetime", _FrozenDatetime)

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory: async_sessionmaker[AsyncSession] = session_factory(engine)

    user_id = uuid4()
    other_user_id = uuid4()
    user_email = f"p2_{user_id.hex[:8]}@example.com"

    async with factory() as db:
        db.add_all(
            [
                User(id=user_id, email=user_email, display_name="P2 User", is_active=True),
                User(
                    id=other_user_id,
                    email=f"other_{other_user_id.hex[:8]}@example.com",
                    display_name="Other User",
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
    get_user_vocabulary_service.cache_clear()

    async def override_db():
        async with factory() as db:
            yield db

    async def override_user():
        return CurrentUser(user_id=str(user_id), email=user_email, display_name="P2 User")

    prev_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield {
                "client": client,
                "factory": factory,
                "user_id": user_id,
                "other_user_id": other_user_id,
            }
    finally:
        voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
        voice_router_mod._INFLIGHT_VOICE_LOCKS.clear()
        draft_service_module._INFLIGHT_ANALYZE_LOCKS.clear()
        get_voice_service.cache_clear()
        get_user_vocabulary_service.cache_clear()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev_overrides)
        await engine.dispose()


# ==============================================================================
# 1. TRỤ CỘT 1b — UserVocabularyService (DB, Cache LRU/TTL/Invalidate, Zero PII, Fail-Open)
# ==============================================================================


@pytest.mark.asyncio
async def test_user_vocabulary_service_sqlite_sources_cache_ttl_lru_and_invalidate(
    phase2_env: dict[str, Any],
) -> None:
    factory = phase2_env["factory"]
    user_id: UUID = phase2_env["user_id"]
    other_user_id: UUID = phase2_env["other_user_id"]

    draft_id = uuid4()
    other_draft_id = uuid4()
    now_utc = FROZEN_NOW.astimezone(UTC)

    async with factory() as db:
        db.add_all(
            [
                TaskModel(
                    user_id=user_id,
                    title="Tối ưu dịch vụ FastAPI và PostgreSQL",
                    time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
                ),
                TaskModel(
                    user_id=other_user_id,
                    title="Bí mật nội bộ ProjectZephyr",
                    time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
                ),
                PlanningDraftModel(
                    id=draft_id,
                    user_id=user_id,
                    client_draft_id="vocab-draft-1",
                    input_source="text",
                    summary={"title": "Ôn tập giải thuật Karatsuba và Quy hoạch động"},
                    summary_revision=1,
                    planning_input_revision=1,
                ),
                PlanningDraftModel(
                    id=other_draft_id,
                    user_id=other_user_id,
                    client_draft_id="vocab-draft-other",
                    input_source="text",
                    summary={"title": "Tài liệu mật ShadowProtocol"},
                    summary_revision=1,
                    planning_input_revision=1,
                ),
            ]
        )
        await db.flush()
        db.add(
            PlanningContextModel(
                user_id=user_id,
                source_draft_id=draft_id,
                source_summary_revision=1,
                availability_snapshot={"weekly_rules": [], "date_overrides": []},
                query_start=now_utc,
                query_end=now_utc + timedelta(days=7),
                fetched_at=now_utc,
                sync_state="synced",
                coverage_state="complete",
                selected_calendar_ids=["primary"],
                selected_calendar_sources=[],
                busy_intervals=[],
                calendar_errors=[],
                event_snapshots=[
                    {"summary": "Họp Sprint Planning dự án Kubernetes"},
                    {"title": "Seminar Transformer Architecture"},
                ],
                manual_busy_intervals=[],
                excluded_sources=[],
            )
        )
        await db.commit()

    fake_now = [100.0]
    vocab_svc = UserVocabularyService(
        max_entries=2,
        ttl_seconds=600.0,
        max_hints=30,
        clock=lambda: fake_now[0],
    )

    async with factory() as db:
        hints = await vocab_svc.get_hints(db, str(user_id))
        assert not db.new and not db.dirty and not db.deleted

    joined = " | ".join(hints)
    assert "FastAPI" in joined
    assert "PostgreSQL" in joined
    assert "Karatsuba" in joined
    assert "Kubernetes" in joined
    assert "Transformer Architecture" in joined
    # Cô lập tuyệt đối giữa các user
    assert "ProjectZephyr" not in joined
    assert "ShadowProtocol" not in joined
    assert len(hints) <= 30

    # Cache hit trong TTL 600s: thêm Task mới nhưng chưa invalidate -> vẫn trả bản cache
    async with factory() as db:
        db.add(
            TaskModel(
                user_id=user_id,
                title="Nghiên cứu thuật toán BellmanFord",
                time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
            )
        )
        await db.commit()

    fake_now[0] = 699.0  # < 100 + 600
    async with factory() as db:
        cached_hints = await vocab_svc.get_hints(db, user_id)
    assert cached_hints == hints
    assert "BellmanFord" not in " | ".join(cached_hints)

    # Hết hạn TTL (>= 700.0) -> tự động truy vấn lại lấy BellmanFord
    fake_now[0] = 701.0
    async with factory() as db:
        refreshed_hints = await vocab_svc.get_hints(db, user_id)
    assert "BellmanFord" in " | ".join(refreshed_hints)

    # Invalidate tường minh xóa ngay cache không cần chờ TTL
    async with factory() as db:
        db.add(
            TaskModel(
                user_id=user_id,
                title="Triển khai hệ thống OpenTelemetry",
                time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
            )
        )
        await db.commit()
    vocab_svc.invalidate(str(user_id))
    async with factory() as db:
        after_invalidate = await vocab_svc.get_hints(db, user_id)
    assert "OpenTelemetry" in " | ".join(after_invalidate)

    # Bounded LRU (max_entries=2): nạp thêm 2 user mới đẩy mục cũ nhất ra khỏi cache
    third_user = uuid4()
    async with factory() as db:
        await vocab_svc.get_hints(db, other_user_id)
        # Chạm lại user_id để biến other_user_id thành LRU
        await vocab_svc.get_hints(db, user_id)
        await vocab_svc.get_hints(db, third_user)
    assert len(vocab_svc._cache) == 2
    assert user_id in vocab_svc._cache
    assert third_user in vocab_svc._cache
    assert other_user_id not in vocab_svc._cache


@pytest.mark.asyncio
async def test_user_vocabulary_service_zero_pii_stopwords_cap_and_fail_open() -> None:
    raw_samples = [
        "Gửi báo cáo GraphQL cho minh.nguyen@hcmus.edu.vn hoặc số +84 912 345 678 trước tối mai",
        "Họp tại https://meet.google.com/abc-defg-hij liên hệ 028-3835-4266 mã 550e8400-e29b-41d4-a716-446655440000",
        "Làm việc và ôn tập buổi tối the meeting task deadline daily",
        *[f"Chuyên đề Kỹ thuật TermCode{i:02d}" for i in range(40)],
    ]
    hints = extract_salient_vocabulary_hints(raw_samples, max_hints=30)
    assert len(hints) == 30
    assert "GraphQL" in hints
    for h in hints:
        assert "@" not in h
        assert "http" not in h.lower()
        assert "meet.google.com" not in h.lower()
        assert sum(ch.isdigit() for ch in h) < 8
        assert h.casefold() not in {"làm", "viết", "buổi", "tối", "the", "meeting", "deadline"}

    svc = UserVocabularyService()
    assert await svc.get_hints(None, uuid4()) == []

    class _BrokenSession:
        async def scalars(self, *_args: Any, **_kwargs: Any) -> Any:
            raise SQLAlchemyError("simulated db failure with secret_title_do_not_leak")

    broken_db = cast(AsyncSession, _BrokenSession())
    assert await svc.get_hints(broken_db, "invalid-not-a-uuid") == []
    assert await svc.get_hints(broken_db, "") == []
    assert await svc.get_hints(broken_db, uuid4()) == []


# ==============================================================================
# 2. VoiceService & Router POST /api/v1/voice/transcriptions (contextual_hints & Backward Compat)
# ==============================================================================


@pytest.mark.asyncio
async def test_voice_providers_format_contextual_hints_and_preserve_transcribe_inline_schema() -> None:
    long_hints = [f"ThuậtNgữChuyênNgành_{idx:02d}" for idx in range(40)]
    formatted = _format_contextual_hints(long_hints)
    assert 0 < len(formatted) <= 400

    # 1. GroqWhisperSttProvider nối contextual_hints vào prompt khi khác rỗng, giữ nguyên khi rỗng
    groq_create = AsyncMock(
        return_value={
            "text": "Ôn tập giải thuật Karatsuba",
            "segments": [{"no_speech_prob": 0.05, "avg_logprob": -0.2}],
        }
    )
    groq = GroqWhisperSttProvider(
        api_key="test-groq-key",
        model="whisper-large-v3",
        language_code="vi-VN",
    )
    cast(Any, groq)._client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=groq_create))
    )
    await groq.transcribe(b"RIFF-audio", "audio/wav", contextual_hints=["Karatsuba", "FastAPI"])
    assert groq_create.await_args is not None
    sent_prompt = groq_create.await_args.kwargs["prompt"]
    assert sent_prompt.startswith(_DEFAULT_STT_PROMPT)
    assert "Karatsuba, FastAPI" in sent_prompt

    groq_create.reset_mock()
    await groq.transcribe(b"RIFF-audio", "audio/wav", contextual_hints=[])
    assert groq_create.await_args is not None
    assert groq_create.await_args.kwargs["prompt"] == _DEFAULT_STT_PROMPT

    # 2. GeminiSttProvider inline (Flash-Lite) nối vào system_instruction; transcribe_inline giữ nguyên AudioTranscriptionConfig
    gen_inline = AsyncMock(return_value=SimpleNamespace(text="Ôn tập Karatsuba"))
    gemini_inline = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-flash-lite",
        mode="smart",
        language_code="vi-VN",
        transport="inline",
    )
    cast(Any, gemini_inline)._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=gen_inline))
    )
    await gemini_inline.transcribe(
        b"M4A-bytes",
        "audio/m4a",
        contextual_hints=["Karatsuba", "Dijkstra"],
    )
    assert gen_inline.await_args is not None
    inline_cfg = gen_inline.await_args.kwargs["config"]
    assert inline_cfg.system_instruction.startswith(_GEMINI_STT_PROMPT_SMART)
    assert "Karatsuba, Dijkstra" in inline_cfg.system_instruction
    assert gen_inline.await_args.kwargs["contents"][1] == _GEMINI_STT_USER_PROMPT

    gen_transcribe = AsyncMock(
        return_value=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text="Ôn tập Karatsuba", audio_transcription=None)]
                    )
                )
            ]
        )
    )
    gemini_transcribe = GeminiSttProvider(
        api_key="test-gemini-key",
        model="gemini-3.5-transcribe",
        mode="verbatim",
        language_code="vi-VN",
        transport="auto",
    )
    cast(Any, gemini_transcribe)._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=gen_transcribe))
    )
    await gemini_transcribe.transcribe(
        b"M4A-bytes",
        "audio/m4a",
        contextual_hints=["Karatsuba"],
    )
    assert gen_transcribe.await_args is not None
    transcribe_cfg = gen_transcribe.await_args.kwargs["config"]
    assert transcribe_cfg.system_instruction is None
    assert (
        transcribe_cfg.audio_transcription_config.mode
        == types.AudioTranscriptionConfigMode.VERBATIM
    )


@pytest.mark.asyncio
async def test_voice_router_passes_contextual_hints_and_keeps_legacy_fake_providers_compatible(
    phase2_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]
    factory = phase2_env["factory"]
    user_id: UUID = phase2_env["user_id"]
    now_utc = FROZEN_NOW.astimezone(UTC)

    thought_id = uuid4()
    async with factory() as db:
        db.add_all(
            [
                TaskModel(
                    user_id=user_id,
                    title="Triển khai kiến trúc EventSourcing với FastAPI",
                    time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
                ),
                Thought(
                    id=thought_id,
                    user_id=user_id,
                    client_thought_id="thought-vocab-1",
                    input_type="audio",
                    capture_state="saved",
                    attempt_count=1,
                ),
            ]
        )
        await db.commit()

    # 1. Provider nhận đủ contextual_hints cho uf1_input & thought_to_draft, nhưng uf5_checkin không cần lấy hints
    captured_calls: list[dict[str, Any]] = []

    class _HintAwareProvider:
        name = "hint_aware"

        async def transcribe(
            self,
            audio: bytes,
            mime_type: str,
            *,
            purpose: str | None = None,
            contextual_hints: list[str] | None = None,
        ) -> str:
            captured_calls.append({"purpose": purpose, "hints": contextual_hints})
            return f"ok:{purpose}"

    hint_service = VoiceService(
        FallbackSttProvider(primary=_HintAwareProvider(), fallback=_HintAwareProvider()),
        timeout_seconds=5.0,
        max_audio_bytes=1024 * 1024,
    )
    with patch.object(voice_router_mod, "get_voice_service", return_value=hint_service):
        r_uf1 = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": "p2-stt-uf1"},
            data={"purpose": "uf1_input", "device_id": "pixel_8"},
            files={"file": ("uf1.m4a", b"M4A-uf1", "audio/m4a")},
        )
        assert r_uf1.status_code == 200, r_uf1.text

        r_thought = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": "p2-stt-thought"},
            data={
                "purpose": "thought_to_draft",
                "device_id": "pixel_8",
                "thought_id": str(thought_id),
            },
            files={"file": ("thought.m4a", b"M4A-thought", "audio/m4a")},
        )
        assert r_thought.status_code == 200, r_thought.text

        r_uf5 = await client.post(
            "/api/v1/voice/transcriptions",
            headers={"Idempotency-Key": "p2-stt-uf5"},
            data={"purpose": "uf5_checkin", "device_id": "pixel_8"},
            files={"file": ("uf5.m4a", b"M4A-uf5", "audio/m4a")},
        )
        assert r_uf5.status_code == 200, r_uf5.text

    assert len(captured_calls) == 3
    assert captured_calls[0]["purpose"] == "uf1_input"
    assert "EventSourcing" in (captured_calls[0]["hints"] or [])
    assert captured_calls[1]["purpose"] == "thought_to_draft"
    assert "FastAPI" in (captured_calls[1]["hints"] or [])
    assert captured_calls[2]["purpose"] == "uf5_checkin"
    assert captured_calls[2]["hints"] is None

    # 2. Fake STT provider kiểu cũ chỉ nhận (audio, mime_type) hoặc chỉ nhận purpose vẫn chạy 100% an toàn
    class _MinimalLegacyProvider:
        name = "legacy_two_args"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            return f"legacy:{mime_type}:{len(audio)}"

    class _PurposeOnlyProvider:
        name = "legacy_purpose_only"

        async def transcribe(
            self, audio: bytes, mime_type: str, purpose: str | None = None
        ) -> str:
            return f"purpose_only:{purpose}"

    for idx, legacy_provider in enumerate((_MinimalLegacyProvider(), _PurposeOnlyProvider())):
        legacy_svc = VoiceService(
            legacy_provider,
            timeout_seconds=5.0,
            max_audio_bytes=1024 * 1024,
        )
        with patch.object(voice_router_mod, "get_voice_service", return_value=legacy_svc):
            resp = await client.post(
                "/api/v1/voice/transcriptions",
                headers={"Idempotency-Key": f"p2-legacy-prov-{idx}"},
                data={"purpose": "uf1_input", "device_id": "pixel_8"},
                files={"file": ("legacy.m4a", b"M4A-legacy", "audio/m4a")},
            )
        assert resp.status_code == 200, resp.text


# ==============================================================================
# 3. TRỤ CỘT 3 — ReALM Self-Repair Schema & Abandoned Anchor Filtering
# ==============================================================================


def test_realm_self_repair_schema_defaults_and_abandoned_anchor_filtering() -> None:
    default_payload = LLMTaskExtractionPayload(summary="Bản nháp mặc định")
    assert default_payload.turn_intent == "create_task"
    assert default_payload.abandoned_phrases == []

    coerced_payload = LLMTaskExtractionPayload.model_validate(
        {
            "summary": "Kiểm tra null safety",
            "turn_intent": None,
            "abandoned_phrases": ["  hạn 20/11  ", "", "   "],
        }
    )
    assert coerced_payload.turn_intent == "create_task"
    assert coerced_payload.abandoned_phrases == ["hạn 20/11"]
    assert "abandoned_phrases" in UF1_EXTRACTION_SYSTEM_PROMPT
    assert "turn_intent" in UF1_EXTRACTION_SYSTEM_PROMPT

    # Case A: User tự sửa cả khung rảnh bị lệch thứ-ngày lẫn hạn chót -> lọc sạch anchor bị tự hủy
    self_repair_input = (
        "Làm báo cáo 5 trang, rảnh Thứ Sáu 21/11 19:00-21:00, "
        "à nhầm Thứ Bảy 21/11 20:00-22:00, hạn 22/11, à quên đổi hạn sang 25/11 lúc 22:00"
    )
    raw_result = TaskExtractionResult(
        title="Làm báo cáo",
        content="Làm báo cáo 5 trang",
        scope="5 trang",
        turn_intent="create_task",
        abandoned_phrases=[
            "rảnh Thứ Sáu 21/11 19:00-21:00",
            "hạn 22/11",
        ],
        free_time_slots=["Thứ Bảy 21/11 20:00-22:00"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                weekday=6,
                date="2026-11-21",
                start_local="20:00",
                end_local="22:00",
                raw_phrase="Thứ Bảy 21/11 20:00-22:00",
            )
        ],
        # Giả lập LLM nhầm ngày hạn cũ 22/11 để kiểm chứng I1 sửa sang 25/11 nhờ đã lọc bỏ 'hạn 22/11'
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=datetime(2026, 11, 22, 22, 0, tzinfo=VN_TZ),
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=datetime(2026, 11, 22, 22, 0, tzinfo=VN_TZ),
        is_complete=True,
        summary="Làm báo cáo 5 trang, rảnh T7 21/11 20:00-22:00, hạn 25/11.",
    )
    verified = AIService._enforce_uf1_temporal_guardrails(
        raw_result,
        current_dt=FROZEN_NOW,
        timezone="Asia/Ho_Chi_Minh",
        user_input=self_repair_input,
    )
    assert verified.is_complete is True
    assert verified.structured_free_windows[0].is_ambiguous is False
    assert verified.deadline is not None
    assert verified.deadline.astimezone(VN_TZ).date().isoformat() == "2026-11-25"

    # Case B: Chốt an toàn — nếu abandoned_phrases trùng hoàn toàn toàn bộ câu nói thì KHÔNG lọc mất anchor thật
    whole_input = "Làm báo cáo 5 trang, rảnh 19:00-21:00 hằng ngày, hạn 25/11/2026 22:00"
    whole_abandoned = TaskExtractionResult(
        title="Làm báo cáo",
        content="Làm báo cáo 5 trang",
        scope="5 trang",
        abandoned_phrases=[whole_input],
        free_time_slots=["19:00-21:00 hằng ngày"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="rảnh 19:00-21:00 hằng ngày",
            )
        ],
        deadline=datetime(2026, 11, 25, 22, 0, tzinfo=VN_TZ),
        is_complete=True,
        summary="Báo cáo 5 trang.",
    )
    kept_whole = AIService._enforce_uf1_temporal_guardrails(
        whole_abandoned,
        current_dt=FROZEN_NOW,
        timezone="Asia/Ho_Chi_Minh",
        user_input=whole_input,
    )
    assert kept_whole.is_complete is True
    assert kept_whole.structured_free_windows[0].is_ambiguous is False


# ==============================================================================
# 4. TRỤ CỘT 3 — Multi-Turn ReALM Preservation (T10 -> T11) & I2/I4 Fail-Safe Blocking
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn2_selection_msg",
    [
        "Thôi chốt làm bản tóm tắt năm trang thôi nhé",
        "chốt phương án 5 trang",
    ],
)
async def test_multi_turn_realm_scope_undecided_to_selected_preserves_slots(
    phase2_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    turn2_selection_msg: str,
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]
    deadline_sun = datetime(2026, 11, 22, 22, 0, tzinfo=VN_TZ)

    turn1_raw = (
        "Làm báo cáo môn Trí tuệ nhân tạo, đang phân vân làm bản tóm tắt năm trang "
        "hay bài chi tiết mười lăm trang, rảnh tối thứ Năm từ 19:00 đến 21:00, "
        "hạn chót Chủ nhật 22/11 lúc 22:00"
    )
    create_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": f"t10-create-{uuid4()}"},
        json={
            "client_draft_id": f"client-t10-{uuid4().hex[:8]}",
            "input_source": "voice",
            "raw_input": turn1_raw,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]
    draft_id = draft["draft_id"]

    t10_payload = LLMTaskExtractionPayload(
        title="Báo cáo môn Trí tuệ nhân tạo",
        content="Làm báo cáo môn Trí tuệ nhân tạo",
        scope="Bản tóm tắt 5 trang hoặc bài chi tiết 15 trang",
        turn_intent="create_task",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=deadline_sun,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=deadline_sun,
        free_time_slots=["2026-11-19 19:00-21:00"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                weekday=4,
                date="2026-11-19",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="tối thứ Năm từ 19:00 đến 21:00",
            )
        ],
        is_complete=False,
        summary="Báo cáo môn Trí tuệ nhân tạo, chưa chốt 5 trang hay 15 trang.",
    )
    t_still_undecided_payload = LLMTaskExtractionPayload(
        title="Báo cáo môn Trí tuệ nhân tạo",
        content="Làm báo cáo môn Trí tuệ nhân tạo",
        scope="5 trang hay 10 trang",
        turn_intent="clarify_only",
        is_complete=False,
        summary="Vẫn đang phân vân giữa 5 trang hay 10 trang.",
    )
    # Turn chốt Scope (T11): giả lập LLM chỉ trả delta scope và để trống windows/time_constraint
    # (thậm chí trả nhầm availability_update_mode='replace') -> ReALM vẫn bảo toàn 100% lịch & hạn từ T10!
    t11_payload = LLMTaskExtractionPayload(
        title="Báo cáo môn Trí tuệ nhân tạo",
        content="Làm báo cáo môn Trí tuệ nhân tạo",
        scope="Bản tóm tắt 5 trang",
        turn_intent="update_draft",
        availability_update_mode="replace",
        structured_free_windows=[],
        time_constraint=None,
        deadline=None,
        is_complete=True,
        summary="Chốt làm bản tóm tắt 5 trang.",
    )

    queue = [t10_payload, t_still_undecided_payload, t11_payload]

    async def _fake_hedged(**_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return queue.pop(0), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged)

    # Lượt 1 (T10): Phân vân scope -> can_plan=False, nhưng time_constraint & availability đã được lưu
    r1 = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": f"t10-a1-{uuid4()}"},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": turn1_raw,
        },
    )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()["data"]
    assert d1["summary"]["can_plan"] is False
    assert d1["summary"]["scope"] is None
    assert d1["summary"]["time_constraint"] is not None
    assert (
        datetime.fromisoformat(d1["summary"]["time_constraint"]["deadline_at"]) == deadline_sun
    )
    assert d1["summary"]["availability"]["date_overrides"][0]["date"] == "2026-11-19"
    assert any(
        i["code"] == "AI_SCOPE_CHOICE_REQUIRED" and i["blocking"] is True
        for i in d1["summary"]["clarification_issues"]
    )

    # Lượt trung gian còn chữ 'hay' dù có chữ 'chốt' -> vẫn phải chặn!
    r_mid = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": f"t10-amid-{uuid4()}"},
        json={
            "expected_summary_revision": d1["summary_revision"],
            "expected_planning_input_revision": d1["planning_input_revision"],
            "message": "Chốt làm 5 trang hay 10 trang nhỉ?",
        },
    )
    assert r_mid.status_code == 200, r_mid.text
    d_mid = r_mid.json()["data"]
    assert d_mid["summary"]["can_plan"] is False
    assert d_mid["summary"]["scope"] is None
    assert any(
        i["code"] == "AI_SCOPE_CHOICE_REQUIRED" and i["blocking"] is True
        for i in d_mid["summary"]["clarification_issues"]
    )

    # Lượt 2 (T11): Chốt 5 trang -> giữ nguyên time_constraint + availability từ T10, mở can_plan=True!
    r2 = await client.post(
        f"/api/v1/drafts/{draft_id}/analyze",
        headers={"Idempotency-Key": f"t11-a2-{uuid4()}"},
        json={
            "expected_summary_revision": d_mid["summary_revision"],
            "expected_planning_input_revision": d_mid["planning_input_revision"],
            "message": turn2_selection_msg,
        },
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()["data"]
    assert d2["summary"]["scope"] == "Bản tóm tắt 5 trang"
    assert d2["summary"]["time_constraint"] == d1["summary"]["time_constraint"]
    assert d2["summary"]["availability"] == d1["summary"]["availability"]
    assert d2["summary"]["can_plan"] is True
    assert not any(i["blocking"] for i in d2["summary"]["clarification_issues"])


@pytest.mark.asyncio
async def test_i2_i4_failsafe_forces_blocking_true_and_syncs_is_initial_turn(
    phase2_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]
    deadline_sun = datetime(2026, 11, 22, 22, 0, tzinfo=VN_TZ)

    # Tạo draft chỉ có câu ngắn rồi gọi /analyze lần đầu qua message -> is_initial_turn=True kích hoạt I4
    c_resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": f"i4-init-{uuid4()}"},
        json={
            "client_draft_id": f"i4-{uuid4().hex[:8]}",
            "input_source": "text",
            "raw_input": "Soạn 5 slide thuyết trình Sinh học",
        },
    )
    draft = c_resp.json()["data"]

    hallucinated_payload = LLMTaskExtractionPayload(
        title="Đầu tư chứng khoán phái sinh",
        content="Soạn 5 slide thuyết trình Sinh học",
        scope="5 slide",
        clarification_issues=[
            ClarificationIssue(
                code="TITLE_CONFIRMATION_REQUIRED",
                field_path="title",
                message="Cần xác nhận tiêu đề.",
                question="Bạn xác nhận lại tiêu đề nhé?",
                blocking=False,  # LLM cố tình gửi blocking=False -> fail-safe phải ép lên True!
            ),
            ClarificationIssue(
                code="AVAILABILITY_INCOMPLETE",
                field_path="availability",
                message="Thiếu giờ kết thúc.",
                question="Bạn làm đến mấy giờ?",
                blocking=False,
            ),
        ],
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=deadline_sun,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=deadline_sun,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local=None,
                raw_phrase="tối hằng ngày từ 19:00",
            )
        ],
        is_complete=True,
        summary="Soạn 5 slide.",
    )

    seen_is_initial_turn: list[bool | None] = []
    real_extract = AIService.extract_task_intent

    async def _spy_extract(user_input: str, **kwargs: Any):
        seen_is_initial_turn.append(kwargs.get("is_initial_turn"))
        return await real_extract(ai_service, user_input, **kwargs)

    async def _fake_hedged(**_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return hallucinated_payload, "tier_1_gemini"

    monkeypatch.setattr(ai_service, "extract_task_intent", _spy_extract)
    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged)

    a_resp = await client.post(
        f"/api/v1/drafts/{draft['draft_id']}/analyze",
        headers={"Idempotency-Key": f"i4-analyze-{uuid4()}"},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": "Soạn 5 slide thuyết trình Sinh học, tối hằng ngày từ 19:00, hạn 22/11 lúc 22:00",
        },
    )
    assert a_resp.status_code == 200, a_resp.text
    analyzed = a_resp.json()["data"]
    assert seen_is_initial_turn == [True]
    assert analyzed["summary"]["can_plan"] is False
    issues_by_code = {i["code"]: i for i in analyzed["summary"]["clarification_issues"]}
    assert issues_by_code["AI_TITLE_CONFIRMATION_REQUIRED"]["blocking"] is True
    assert issues_by_code["AI_AVAILABILITY_INCOMPLETE"]["blocking"] is True


# ==============================================================================
# 5. TRỤ CỘT 6 — Zone 2 Medium Confidence Dialog (AI_DEADLINE_CONFIRMATION_NEEDED)
# ==============================================================================


@pytest.mark.asyncio
async def test_zone2_deadline_confirmation_needed_positive_and_negative_contexts(
    phase2_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]

    def _missing_deadline_payload(title: str = "Viết báo cáo môn học") -> LLMTaskExtractionPayload:
        return LLMTaskExtractionPayload(
            title=title,
            content="Viết báo cáo môn học 5 trang",
            scope="5 trang Word",
            time_constraint=None,
            deadline=None,
            free_time_slots=["19:00 đến 21:00 hằng ngày"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="daily",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="rảnh 19:00 đến 21:00 hằng ngày",
                )
            ],
            is_complete=False,
            summary="Viết báo cáo 5 trang, chưa rõ hạn chót.",
        )

    # --- Case 1 (Positive): "hẹn tối thứ Bảy nhé" khi thiếu deadline -> AI_DEADLINE_CONFIRMATION_NEEDED ---
    c1 = (
        await client.post(
            "/api/v1/drafts",
            headers={"Idempotency-Key": "z2-pos-create"},
            json={
                "client_draft_id": "z2-pos-1",
                "input_source": "voice",
                "raw_input": "Viết báo cáo môn học 5 trang, rảnh 19:00 đến 21:00 hằng ngày, hẹn tối thứ Bảy nhé",
            },
        )
    ).json()["data"]

    sat_deadline = datetime(2026, 11, 21, 22, 0, tzinfo=VN_TZ)
    confirmed_payload = LLMTaskExtractionPayload(
        title="Viết báo cáo môn học",
        content="Viết báo cáo môn học 5 trang",
        scope="5 trang Word",
        turn_intent="clarify_only",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=sat_deadline,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=sat_deadline,
        is_complete=True,
        summary="Xác nhận hạn chót tối thứ Bảy 21/11 lúc 22:00.",
    )
    pos_queue = [_missing_deadline_payload(), confirmed_payload]

    async def _pos_hedged(**_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return pos_queue.pop(0), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _pos_hedged)

    r_pos = await client.post(
        f"/api/v1/drafts/{c1['draft_id']}/analyze",
        headers={"Idempotency-Key": "z2-pos-analyze-1"},
        json={
            "expected_summary_revision": c1["summary_revision"],
            "expected_planning_input_revision": c1["planning_input_revision"],
            "raw_input": c1["raw_input"],
        },
    )
    assert r_pos.status_code == 200, r_pos.text
    d_pos = r_pos.json()["data"]
    assert d_pos["summary"]["can_plan"] is False
    pos_issues = d_pos["summary"]["clarification_issues"]
    pos_codes = {i["code"] for i in pos_issues}
    assert "AI_DEADLINE_CONFIRMATION_NEEDED" in pos_codes
    assert "MISSING_TIME_CONSTRAINT" not in pos_codes
    assert "TIME_CONSTRAINT_REQUIRED" not in pos_codes
    z2_issue = next(i for i in pos_issues if i["code"] == "AI_DEADLINE_CONFIRMATION_NEEDED")
    assert z2_issue["field_path"] == "time_constraint"
    assert z2_issue["blocking"] is True
    assert "21/11/2026" in z2_issue["question"]
    assert "hạn chót hoàn thành hay một buổi hẹn cụ thể ạ?" in z2_issue["question"]

    # Turn 2 xác nhận là hạn chót -> xóa sạch AI_DEADLINE_CONFIRMATION_NEEDED, mở can_plan=True
    r_pos_t2 = await client.post(
        f"/api/v1/drafts/{c1['draft_id']}/analyze",
        headers={"Idempotency-Key": "z2-pos-analyze-2"},
        json={
            "expected_summary_revision": d_pos["summary_revision"],
            "expected_planning_input_revision": d_pos["planning_input_revision"],
            "message": "Đúng là hạn chót tối thứ Bảy 21/11 lúc 22:00 nhé",
        },
    )
    assert r_pos_t2.status_code == 200, r_pos_t2.text
    d_pos_t2 = r_pos_t2.json()["data"]
    assert d_pos_t2["summary"]["can_plan"] is True
    assert not any(
        i["code"] == "AI_DEADLINE_CONFIRMATION_NEEDED"
        for i in d_pos_t2["summary"]["clarification_issues"]
    )

    # --- Case 2 & 3 (Negative): Cuộc gặp thật với khách hàng / bác sĩ -> KHÔNG sinh AI_DEADLINE_CONFIRMATION_NEEDED ---
    for idx, neg_sentence in enumerate(
        (
            "Viết báo cáo môn học 5 trang, rảnh 19:00 đến 21:00 hằng ngày, hẹn tối thứ Sáu gặp khách hàng",
            "Viết báo cáo môn học 5 trang, rảnh 19:00 đến 21:00 hằng ngày, sau buổi hẹn tối mai với bác sĩ",
        )
    ):
        async def _neg_hedged(**_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
            return _missing_deadline_payload(), "tier_1_gemini"

        monkeypatch.setattr(ai_service, "_execute_hedged_structured", _neg_hedged)
        c_neg = (
            await client.post(
                "/api/v1/drafts",
                headers={"Idempotency-Key": f"z2-neg-create-{idx}"},
                json={
                    "client_draft_id": f"z2-neg-{idx}",
                    "input_source": "voice",
                    "raw_input": neg_sentence,
                },
            )
        ).json()["data"]
        r_neg = await client.post(
            f"/api/v1/drafts/{c_neg['draft_id']}/analyze",
            headers={"Idempotency-Key": f"z2-neg-analyze-{idx}"},
            json={
                "expected_summary_revision": c_neg["summary_revision"],
                "expected_planning_input_revision": c_neg["planning_input_revision"],
                "raw_input": neg_sentence,
            },
        )
        assert r_neg.status_code == 200, r_neg.text
        neg_codes = {
            i["code"] for i in r_neg.json()["data"]["summary"]["clarification_issues"]
        }
        assert "AI_DEADLINE_CONFIRMATION_NEEDED" not in neg_codes


# ==============================================================================
# 6. CODE REVIEW PHASE 2 REGRESSIONS (#1..#5)
# ==============================================================================


def test_review_f1_abandoned_anchor_boundary_and_repair_pivot_guards() -> None:
    # 1a. Boundary guard: "2/11" KHÔNG được cắt hỏng "22/11" và "7h" KHÔNG cắt hỏng "17h"
    slash_input = (
        "Làm bài tập 5 bài, rảnh 7h-9h, à nhầm 17h-19h hằng ngày, "
        "hạn 2/11, à nhầm 22/11 lúc 20:00"
    )
    slash_res = TaskExtractionResult(
        title="Làm bài tập",
        content="Làm bài tập 5 bài",
        scope="5 bài",
        abandoned_phrases=["2/11", "7h"],
        free_time_slots=["17:00-19:00 hằng ngày"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="17:00",
                end_local="19:00",
                raw_phrase="17h-19h hằng ngày",
            )
        ],
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=datetime(2026, 11, 20, 20, 0, tzinfo=VN_TZ),
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=datetime(2026, 11, 20, 20, 0, tzinfo=VN_TZ),
        is_complete=True,
        summary="Làm 5 bài tập.",
    )
    verified_slash = AIService._enforce_uf1_temporal_guardrails(
        slash_res,
        current_dt=FROZEN_NOW,
        timezone="Asia/Ho_Chi_Minh",
        user_input=slash_input,
    )
    assert verified_slash.is_complete is True
    assert verified_slash.structured_free_windows[0].is_ambiguous is False
    assert verified_slash.deadline is not None
    assert verified_slash.deadline.astimezone(VN_TZ).date().isoformat() == "2026-11-22"

    # 1b. Duplicate match trước/sau từ khóa đính chính: "rảnh thứ 2, à nhầm thứ 2 tuần sau" với abandoned=["thứ 2"]
    # Đặt mốc Thứ Hai 2026-11-16 để "thứ 2" = 2026-11-16 và "thứ 2 tuần sau" = 2026-11-23 khác nhau rõ ràng
    ref_mon = datetime(2026, 11, 16, 9, 0, tzinfo=VN_TZ)
    dup_input = (
        "Ôn bài 3 chương, rảnh thứ 2 từ 19:00 đến 21:00, "
        "à nhầm thứ 2 tuần sau từ 19:00 đến 21:00, hạn 25/11/2026 22:00"
    )
    dup_res = TaskExtractionResult(
        title="Ôn bài",
        content="Ôn bài 3 chương",
        scope="3 chương",
        abandoned_phrases=["thứ 2"],
        free_time_slots=["thứ 2 tuần sau từ 19:00 đến 21:00"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                weekday=1,
                date="2026-11-23",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="thứ 2 tuần sau từ 19:00 đến 21:00",
            )
        ],
        deadline=datetime(2026, 11, 25, 22, 0, tzinfo=VN_TZ),
        is_complete=True,
        summary="Ôn bài thứ 2 tuần sau.",
    )
    verified_dup = AIService._enforce_uf1_temporal_guardrails(
        dup_res,
        current_dt=ref_mon,
        timezone="Asia/Ho_Chi_Minh",
        user_input=dup_input,
    )
    assert verified_dup.is_complete is True
    assert verified_dup.structured_free_windows[0].date == "2026-11-23"
    assert verified_dup.structured_free_windows[0].is_ambiguous is False


@pytest.mark.asyncio
async def test_review_f2_title_confirmation_persists_across_unrelated_turn2_until_confirmed(
    phase2_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]
    deadline_sun = datetime(2026, 11, 22, 22, 0, tzinfo=VN_TZ)

    draft = (
        await client.post(
            "/api/v1/drafts",
            headers={"Idempotency-Key": "f2-create"},
            json={
                "client_draft_id": "f2-draft",
                "input_source": "text",
                "raw_input": "Soạn 5 slide thuyết trình Sinh học, rảnh 19:00-21:00 hằng ngày",
            },
        )
    ).json()["data"]

    # Turn 1: AI bịa title "Đầu tư bất động sản" và còn thiếu deadline
    t1_payload = LLMTaskExtractionPayload(
        title="Đầu tư bất động sản",
        content="Soạn 5 slide thuyết trình Sinh học",
        scope="5 slide",
        time_constraint=None,
        deadline=None,
        free_time_slots=["19:00-21:00 hằng ngày"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="rảnh 19:00-21:00 hằng ngày",
            )
        ],
        is_complete=False,
        summary="Soạn 5 slide.",
    )
    # Turn 2: User CHỈ bổ sung deadline, AI vẫn giữ nguyên title cũ chưa xác nhận
    t2_only_deadline = LLMTaskExtractionPayload(
        title="Đầu tư bất động sản",
        content="Soạn 5 slide thuyết trình Sinh học",
        scope="5 slide",
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=deadline_sun,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        deadline=deadline_sun,
        is_complete=True,
        summary="Đã có hạn chót.",
    )
    # Turn 3: User xác nhận/đổi tiêu đề sang đúng nội dung
    t3_fix_title = LLMTaskExtractionPayload(
        title="Thuyết trình Sinh học",
        content="Soạn 5 slide thuyết trình Sinh học",
        scope="5 slide",
        is_complete=True,
        summary="Đã chốt tiêu đề Thuyết trình Sinh học.",
    )
    seq = [t1_payload, t2_only_deadline, t3_fix_title]

    async def _fake_hedged(**_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return seq.pop(0), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged)

    d1 = (
        await client.post(
            f"/api/v1/drafts/{draft['draft_id']}/analyze",
            headers={"Idempotency-Key": "f2-t1"},
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "raw_input": draft["raw_input"],
            },
        )
    ).json()["data"]
    assert d1["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_TITLE_CONFIRMATION_REQUIRED" and i["blocking"] is True
        for i in d1["summary"]["clarification_issues"]
    )

    # Turn 2 chỉ bổ sung deadline -> AI_TITLE_CONFIRMATION_REQUIRED KHÔNG được rơi ngầm!
    d2 = (
        await client.post(
            f"/api/v1/drafts/{draft['draft_id']}/analyze",
            headers={"Idempotency-Key": "f2-t2"},
            json={
                "expected_summary_revision": d1["summary_revision"],
                "expected_planning_input_revision": d1["planning_input_revision"],
                "message": "Hạn chót 22/11 lúc 22:00 nhé",
            },
        )
    ).json()["data"]
    assert d2["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_TITLE_CONFIRMATION_REQUIRED" and i["blocking"] is True
        for i in d2["summary"]["clarification_issues"]
    )

    # Turn 3 sửa tiêu đề -> xóa AI_TITLE_CONFIRMATION_REQUIRED, can_plan=True
    d3 = (
        await client.post(
            f"/api/v1/drafts/{draft['draft_id']}/analyze",
            headers={"Idempotency-Key": "f2-t3"},
            json={
                "expected_summary_revision": d2["summary_revision"],
                "expected_planning_input_revision": d2["planning_input_revision"],
                "message": "Đổi tiêu đề thành Thuyết trình Sinh học nhé",
            },
        )
    ).json()["data"]
    assert d3["summary"]["title"] == "Thuyết trình Sinh học"
    assert d3["summary"]["can_plan"] is True
    assert not any(
        i["code"] == "AI_TITLE_CONFIRMATION_REQUIRED"
        for i in d3["summary"]["clarification_issues"]
    )


@pytest.mark.asyncio
async def test_review_f3_dev_user_string_resolves_uuid_and_skips_cancelled_tasks(
    phase2_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]
    factory = phase2_env["factory"]
    now_utc = FROZEN_NOW.astimezone(UTC)

    # Bỏ override get_current_user để đi qua AUTH_MODE=dev_user thật ("user_a")
    saved_user_override = app.dependency_overrides.pop(get_current_user, None)
    try:
        dev_uid = draft_service_module.resolve_user_uuid("user_a")
        # Kích hoạt seed user_a trước rồi thêm 1 Task active + 1 Task cancelled
        await client.get("/api/v1/preferences")
        async with factory() as db:
            db.add_all(
                [
                    TaskModel(
                        user_id=dev_uid,
                        title="Xây dựng pipeline LangGraph",
                        status="pending",
                        time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
                    ),
                    TaskModel(
                        user_id=dev_uid,
                        title="Dự án CancelledLegacyX",
                        status="cancelled",
                        time_constraint={"kind": "deadline", "deadline_at": now_utc.isoformat()},
                    ),
                ]
            )
            await db.commit()
        get_user_vocabulary_service().invalidate()

        captured_hints: list[list[str] | None] = []

        class _CaptureService:
            async def transcribe(
                self,
                audio: bytes,
                *,
                content_type: str | None = None,
                filename: str | None = None,
                purpose: str | None = None,
                contextual_hints: list[str] | None = None,
            ) -> Any:
                captured_hints.append(contextual_hints)
                return SimpleNamespace(text="ok", provider="gemini", low_confidence=False)

        with patch.object(voice_router_mod, "get_voice_service", return_value=_CaptureService()):
            resp = await client.post(
                "/api/v1/voice/transcriptions",
                headers={"Idempotency-Key": "f3-dev-user-a"},
                data={"purpose": "uf1_input", "device_id": "dev_mobile"},
                files={"file": ("dev.m4a", b"M4A-dev", "audio/m4a")},
            )
        assert resp.status_code == 200, resp.text
        assert len(captured_hints) == 1
        joined_hints = " | ".join(captured_hints[0] or [])
        assert "LangGraph" in joined_hints
        assert "CancelledLegacyX" not in joined_hints
    finally:
        if saved_user_override is not None:
            app.dependency_overrides[get_current_user] = saved_user_override


@pytest.mark.asyncio
async def test_review_f4_f5_zone2_pronoun_exclusions_and_qualitative_compound_scope(
    phase2_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: httpx.AsyncClient = phase2_env["client"]
    deadline_sun = datetime(2026, 11, 22, 22, 0, tzinfo=VN_TZ)

    # F4a: "Bạn nhắc giúp mình hẹn tối thứ Bảy nhé" -> đại từ 'Bạn' đầu câu KHÔNG chặn Zone 2!
    base_no_dl = LLMTaskExtractionPayload(
        title="Ôn tập Giải tích",
        content="Ôn tập Giải tích 4 bài",
        scope="4 bài",
        time_constraint=None,
        deadline=None,
        free_time_slots=["19:00-21:00 hằng ngày"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="rảnh 19:00-21:00 hằng ngày",
            )
        ],
        is_complete=False,
        summary="Ôn 4 bài Giải tích.",
    )

    async def _ret_no_dl(**_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return base_no_dl, "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _ret_no_dl)
    c_pronoun = (
        await client.post(
            "/api/v1/drafts",
            headers={"Idempotency-Key": "f4-pronoun-c"},
            json={
                "client_draft_id": "f4-pronoun",
                "input_source": "voice",
                "raw_input": "Ôn tập Giải tích 4 bài, rảnh 19:00-21:00 hằng ngày. Bạn nhắc giúp mình hẹn tối thứ Bảy nhé",
            },
        )
    ).json()["data"]
    d_pronoun = (
        await client.post(
            f"/api/v1/drafts/{c_pronoun['draft_id']}/analyze",
            headers={"Idempotency-Key": "f4-pronoun-a"},
            json={
                "expected_summary_revision": c_pronoun["summary_revision"],
                "expected_planning_input_revision": c_pronoun["planning_input_revision"],
                "raw_input": c_pronoun["raw_input"],
            },
        )
    ).json()["data"]
    assert any(
        i["code"] == "AI_DEADLINE_CONFIRMATION_NEEDED" and i["blocking"] is True
        for i in d_pronoun["summary"]["clarification_issues"]
    )

    # F4b: Negative checks cho "đạt hạng 2 vào thứ Bảy", "hẹn khám tối mai", "lỡ hẹn tối thứ Sáu", "hẹn bạn tối mai"
    for idx, neg_txt in enumerate(
        (
            "Ôn tập Giải tích 4 bài, rảnh 19:00-21:00 hằng ngày, mục tiêu đạt hạng 2 vào thứ Bảy",
            "Ôn tập Giải tích 4 bài, rảnh 19:00-21:00 hằng ngày, nhớ hẹn khám tối mai",
            "Ôn tập Giải tích 4 bài, rảnh 19:00-21:00 hằng ngày, sợ lỡ hẹn tối thứ Sáu",
            "Ôn tập Giải tích 4 bài, rảnh 19:00-21:00 hằng ngày, có hẹn bạn tối mai",
        )
    ):
        c_neg = (
            await client.post(
                "/api/v1/drafts",
                headers={"Idempotency-Key": f"f4-neg-c-{idx}"},
                json={
                    "client_draft_id": f"f4-neg-{idx}",
                    "input_source": "voice",
                    "raw_input": neg_txt,
                },
            )
        ).json()["data"]
        d_neg = (
            await client.post(
                f"/api/v1/drafts/{c_neg['draft_id']}/analyze",
                headers={"Idempotency-Key": f"f4-neg-a-{idx}"},
                json={
                    "expected_summary_revision": c_neg["summary_revision"],
                    "expected_planning_input_revision": c_neg["planning_input_revision"],
                    "raw_input": neg_txt,
                },
            )
        ).json()["data"]
        assert not any(
            i["code"] == "AI_DEADLINE_CONFIRMATION_NEEDED"
            for i in d_neg["summary"]["clarification_issues"]
        )

    # F5: Turn 2 chốt tổ hợp "Làm 5 trang và 10 slide nhé" HOẶC định tính "Làm bản chi tiết đi"
    for s_idx, (reply_msg, expected_scope) in enumerate(
        (
            ("Làm 5 trang và 10 slide nhé", "5 trang báo cáo và 10 slide"),
            ("Làm bản chi tiết đi", "Bài báo cáo chi tiết 15 trang"),
        )
    ):
        t1_undecided = LLMTaskExtractionPayload(
            title="Báo cáo đồ án",
            content="Làm báo cáo đồ án",
            scope="Bản tóm tắt 5 trang hoặc bài chi tiết 15 trang",
            time_constraint=ExtractedTimeConstraint(
                kind="deadline",
                start_at=FROZEN_NOW,
                deadline_at=deadline_sun,
                input_timezone="Asia/Ho_Chi_Minh",
            ),
            deadline=deadline_sun,
            free_time_slots=["19:00-21:00 hằng ngày"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="daily",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="rảnh 19:00-21:00 hằng ngày",
                )
            ],
            is_complete=False,
            summary="Chưa chốt scope.",
        )
        t2_selected = LLMTaskExtractionPayload(
            title="Báo cáo đồ án",
            content="Làm báo cáo đồ án",
            scope=expected_scope,
            turn_intent="update_draft",
            is_complete=True,
            summary=f"Đã chốt {expected_scope}.",
        )
        pair_q = [t1_undecided, t2_selected]

        async def _pair_hedged(_q=pair_q, **_kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
            return _q.pop(0), "tier_1_gemini"

        monkeypatch.setattr(ai_service, "_execute_hedged_structured", _pair_hedged)
        raw_t1 = (
            "Làm báo cáo đồ án, đang phân vân làm bản tóm tắt 5 trang hay bài chi tiết 15 trang, "
            "rảnh 19:00-21:00 hằng ngày, hạn 22/11 lúc 22:00"
        )
        c_s = (
            await client.post(
                "/api/v1/drafts",
                headers={"Idempotency-Key": f"f5-c-{s_idx}"},
                json={
                    "client_draft_id": f"f5-{s_idx}",
                    "input_source": "voice",
                    "raw_input": raw_t1,
                },
            )
        ).json()["data"]
        d_s1 = (
            await client.post(
                f"/api/v1/drafts/{c_s['draft_id']}/analyze",
                headers={"Idempotency-Key": f"f5-a1-{s_idx}"},
                json={
                    "expected_summary_revision": c_s["summary_revision"],
                    "expected_planning_input_revision": c_s["planning_input_revision"],
                    "raw_input": raw_t1,
                },
            )
        ).json()["data"]
        assert d_s1["summary"]["can_plan"] is False
        d_s2 = (
            await client.post(
                f"/api/v1/drafts/{c_s['draft_id']}/analyze",
                headers={"Idempotency-Key": f"f5-a2-{s_idx}"},
                json={
                    "expected_summary_revision": d_s1["summary_revision"],
                    "expected_planning_input_revision": d_s1["planning_input_revision"],
                    "message": reply_msg,
                },
            )
        ).json()["data"]
        assert d_s2["summary"]["can_plan"] is True
        assert d_s2["summary"]["scope"] == expected_scope
        assert not any(
            i["code"] == "AI_SCOPE_CHOICE_REQUIRED"
            for i in d_s2["summary"]["clarification_issues"]
        )

