"""Kiểm thử chuẩn hóa văn bản giọng nói tiếng Việt (ITN & Disfluency Removal) và tích hợp UF1 (T3.1–T3.3, RISK-02)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.services.ai_service as ai_service_module
import app.services.draft_service as draft_service_module
from app.core.database import make_engine, session_factory
from app.models import Base
from app.models.planning import PlanningDraft as PlanningDraftModel
from app.models.user import User, UserPreference
from app.schemas.draft import CreateDraftRequest, DraftAnalyzeRequest
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMTaskExtractionPayload,
)
from app.services import draft_service
from app.services.ai_service import ai_service
from app.utils.vietnamese_itn import (
    PROTECTED_DISCOURSE_WHITELIST,
    normalize_vietnamese_transcript,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
FROZEN_NOW = datetime(2026, 11, 18, 9, 0, tzinfo=VN_TZ)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


# ==============================================================================
# 1. POSITIVE CASES (>= 35): Spoken Hours, Ranges, Weekdays, Durations, Fillers
# ==============================================================================

POSITIVE_CASES: list[tuple[str, str]] = [
    # Spoken hours across sáng / trưa / chiều / tối / đêm / khuya
    ("bảy giờ sáng", "07:00"),
    ("tám giờ mười lăm sáng", "08:15"),
    ("mười hai giờ sáng", "00:00"),
    ("mười một giờ trưa", "11:00"),
    ("mười hai giờ trưa", "12:00"),
    ("một giờ trưa", "13:00"),
    ("hai giờ chiều", "14:00"),
    ("hai giờ mười lăm chiều", "14:15"),
    ("ba giờ rưỡi chiều", "15:30"),
    ("5 giờ 5 chiều", "17:05"),
    ("bảy giờ tối", "19:00"),
    ("bảy rưỡi tối", "19:30"),
    ("bảy giờ ba mươi tối", "19:30"),
    ("7h tối", "19:00"),
    ("6h30 tối", "18:30"),
    ("chín kém mười lăm tối", "20:45"),
    ("chín giờ kém mười lăm tối", "20:45"),
    ("bảy giờ kém mười sáng", "06:50"),
    ("12 giờ kém 15 trưa", "11:45"),
    ("12 giờ kém 15 đêm", "23:45"),
    ("mười một giờ đêm", "23:00"),
    ("mười hai giờ đêm", "24:00"),
    ("hai giờ đêm", "02:00"),
    ("mười giờ khuya", "22:00"),
    ("12g30 trưa", "12:30"),
    ("hai mươi ba giờ rưỡi", "23:30"),
    ("19 giờ 45", "19:45"),
    # Shared trailing period ranges & mixed-period ranges
    ("từ bảy giờ đến chín giờ tối", "từ 19:00 đến 21:00"),
    ("từ bảy rưỡi đến chín rưỡi tối", "từ 19:30 đến 21:30"),
    ("từ bảy giờ sáng đến hai giờ chiều", "từ 07:00 đến 14:00"),
    ("từ 8 giờ đến 5 giờ chiều", "từ 08:00 đến 17:00"),
    ("từ bảy giờ tối đến hai giờ sáng", "từ 19:00 đến 02:00"),
    # Forward period inheritance ranges
    ("từ 7 giờ sáng đến 12 giờ", "từ 07:00 đến 12:00"),
    ("từ bảy giờ sáng đến hai giờ", "từ 07:00 đến 14:00"),
    ("từ 10 giờ sáng đến 1 giờ", "từ 10:00 đến 13:00"),
    ("từ 11 giờ tối đến 1 giờ", "từ 23:00 đến 01:00"),
    # Spoken weekdays
    (
        "thứ hai, thứ ba, thứ tư, thứ năm, thứ sáu, thứ bảy",
        "Thứ 2, Thứ 3, Thứ 4, Thứ 5, Thứ 6, Thứ 7",
    ),
    ("thứ Tư và thứ Bẩy", "Thứ 4 và Thứ 7"),
    # Spoken durations
    ("một tiếng rưỡi", "90 phút"),
    ("tiếng rưỡi", "90 phút"),
    ("2 tiếng rưỡi", "150 phút"),
    ("hai tiếng rưỡi", "150 phút"),
    ("nửa tiếng", "30 phút"),
    ("nửa giờ", "30 phút"),
    ("hai tiếng", "120 phút"),
    ("hai tiếng mười lăm phút", "135 phút"),
    ("chín mươi phút", "90 phút"),
    ("mười lăm phút", "15 phút"),
    # Decimal hour durations
    ("làm được 1.5 tiếng", "làm được 90 phút"),
    ("1,5 tiếng", "90 phút"),
    ("2.5 tiếng đồng hồ", "150 phút"),
    ("0.5 tiếng", "30 phút"),
    # Disfluency removal + stuttered function-word collapse
    ("ờm mình cần làm báo cáo", "mình cần làm báo cáo"),
    ("ừm, à thì tối mai rảnh", "tối mai rảnh"),
    ("ơ kìa, thứ, thứ 5 rảnh", "thứ 5 rảnh"),
    ("từ, từ 7h tối đến 9h tối", "từ 19:00 đến 21:00"),
    ("từ từ 7h tối đến 9h tối", "từ 19:00 đến 21:00"),
    ("từ từ bảy giờ tối đến chín giờ tối", "từ 19:00 đến 21:00"),
    ("ngày ngày mai rảnh buổi tối", "ngày mai rảnh buổi tối"),
    ("à thì mình định làm từ từ thôi", "mình định làm từ từ thôi"),
    # 100% preservation of self-correction & discourse tokens
    ("7h tối à nhầm 8h tối", "19:00 à nhầm 20:00"),
    ("thứ hai, à quên thứ ba mới đúng", "Thứ 2, à quên Thứ 3 mới đúng"),
    ("học cả tuần, à trừ thứ tư", "học cả tuần, à trừ Thứ 4"),
    ("thôi đổi sang thứ Năm", "thôi đổi sang Thứ 5"),
    ("làm 2 trang thay vì 6 slide", "làm 2 trang thay vì 6 slide"),
    ("sửa lại thành 8h tối", "sửa lại thành 20:00"),
    ("cũng giờ đó nhé", "cũng giờ đó nhé"),
    ("sau họp làm 1 tiếng", "sau họp làm 60 phút"),
    ("sau cuộc họp tối", "sau cuộc họp tối"),
    ("đang phân vân chưa chốt", "đang phân vân chưa chốt"),
]


@pytest.mark.parametrize(("raw_text", "expected"), POSITIVE_CASES)
def test_normalize_vietnamese_transcript_positive(raw_text: str, expected: str) -> None:
    assert normalize_vietnamese_transcript(raw_text) == expected


def test_protected_discourse_whitelist_preserved_verbatim() -> None:
    for token in PROTECTED_DISCOURSE_WHITELIST:
        sentence = f"Mình định làm 2 trang, {token} nhé"
        normalized = normalize_vietnamese_transcript(sentence)
        assert token in normalized.lower(), f"Mất cụm từ bảo vệ: {token!r} -> {normalized!r}"


# ==============================================================================
# 2. NEGATIVE CASES (>= 15): Anti-False-Positive Guards
# ==============================================================================

NEGATIVE_CASES: list[str] = [
    "thứ tự ưu tiên của 4 bài",
    "đầu tư 2 phần",
    "tư duy phản biện",
    "tư vấn khách hàng",
    "kém hiệu quả",
    "rưỡi trang giấy",
    "tài liệu tiếng Anh",
    "học tiếng Việt",
    "làm 2 trang hoặc 6 slide chưa chốt",
    "thứ ba mươi",
    "bảy giờ",
    "bảy rưỡi",
    "bảy giờ kém năm",
    "danh tiếng",
    "nổi tiếng",
    "lên tiếng phản đối",
    "sáng tạo nội dung",
    "tối đa 2 giờ",
    "chiều dài báo cáo",
    "bước thứ hai",
    "làm việc từ từ thôi",
    "ngày ngày ôn tập",
    "version 2.5 giờ sáng",
    "1.5 giờ sáng",
]


@pytest.mark.parametrize("raw_text", NEGATIVE_CASES)
def test_normalize_vietnamese_transcript_negative_no_false_positive(raw_text: str) -> None:
    assert normalize_vietnamese_transcript(raw_text) == raw_text


@pytest.mark.parametrize(
    ("raw_text", "expected_minutes"),
    [
        ("làm được 1.5 tiếng", 90),
        ("1,5 tiếng", 90),
        ("2.5 tiếng đồng hồ", 150),
        ("0.5 tiếng", 30),
    ],
)
def test_decimal_tieng_extract_explicit_duration_minutes(
    raw_text: str, expected_minutes: int
) -> None:
    normalized = normalize_vietnamese_transcript(raw_text)
    assert draft_service._extract_explicit_duration_minutes(normalized) == expected_minutes


# ==============================================================================
# 3. LATENCY BENCHMARK CHECK (< 5ms per call)
# ==============================================================================


def test_normalize_vietnamese_transcript_latency_under_5ms() -> None:
    sample = (
        "Ờm, mình cần viết báo cáo môn hệ thống thông minh, phạm vi 5 trang Word, "
        "mỗi ngày rảnh từ bảy rưỡi đến chín rưỡi tối từ thứ hai đến thứ sáu, "
        "à trừ thứ tư ra nhé, làm được khoảng một tiếng rưỡi mỗi buổi, "
        "à nhầm chín mươi phút, phân vân chưa chốt phần biểu đồ, hạn 25/11/2026 22:00.\n"
        "Cũng giờ đó nếu tối thứ bảy rảnh từ sáu giờ rưỡi tối đến tám giờ tối."
    )
    # Warm-up
    for _ in range(10):
        normalize_vietnamese_transcript(sample)

    iterations = 200
    started = time.perf_counter()
    for _ in range(iterations):
        out = normalize_vietnamese_transcript(sample)
    avg_ms = ((time.perf_counter() - started) * 1000.0) / iterations

    assert "19:30 đến 21:30" in out
    assert "90 phút" in out
    assert "à nhầm" in out
    assert "à trừ Thứ 4" in out
    assert avg_ms < 5.0, f"Độ trễ ITN vượt ngưỡng 5ms: {avg_ms:.3f}ms"


# ==============================================================================
# 4. TIER-3 FALLBACK & draft_service.analyze_draft DB INTEGRATION TESTS (RISK-02)
# ==============================================================================


@pytest.fixture
async def itn_db_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'itn.sqlite3').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    owner = uuid4()
    async with factory() as db:
        db.add(User(id=owner, email="itn-owner@example.test", display_name="ITN Owner"))
        await db.flush()
        db.add(
            UserPreference(
                user_id=owner,
                work_interval_seconds=1500,
                rest_interval_seconds=300,
                buffer_multiplier=1.2,
                effective_revision=1,
                micro_task_seconds=120,
                reminder_offsets=[900],
                reminder_style="gentle",
            )
        )
        await db.commit()

    monkeypatch.setattr(draft_service_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(ai_service_module, "datetime", _FrozenDatetime)
    try:
        yield {"db": factory, "owner": str(owner)}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_itn_tier3_fallback_extracts_daily_window_and_preserves_db_raw_input(
    itn_db_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RISK-02: Tier 3 fallback nhận input đã ITN -> trích daily 19:30–21:30, DB giữ nguyên raw_input gốc."""
    raw_spoken = (
        "Ờm mình cần làm báo cáo, mỗi ngày rảnh từ bảy rưỡi tối đến chín rưỡi tối, "
        "hạn 25/11/2026 22:00"
    )
    user_id = itn_db_env["owner"]

    async with itn_db_env["db"]() as db:
        created = await draft_service.create_draft(
            user_id=user_id,
            idempotency_key=f"itn-t3-create-{uuid4().hex}",
            payload=CreateDraftRequest(
                client_draft_id=f"itn-t3-{uuid4().hex}",
                input_source="voice",
                raw_input=raw_spoken,
            ),
            db=db,
        )

    async def _force_provider_timeout(**kwargs: Any):
        raise TimeoutError("Simulated provider timeout to force Tier 3 fallback")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _force_provider_timeout)

    async with itn_db_env["db"]() as db:
        updated = await draft_service.analyze_draft(
            user_id=user_id,
            draft_id=created.draft_id,
            idempotency_key=f"itn-t3-analyze-{uuid4().hex}",
            payload=DraftAnalyzeRequest(
                expected_summary_revision=created.summary_revision,
                expected_planning_input_revision=created.planning_input_revision,
                raw_input=raw_spoken,
            ),
            db=db,
        )

    assert updated.summary.availability is not None
    assert len(updated.summary.availability.weekly_rules) == 7
    assert {
        (rule.weekday, rule.start_local.strftime("%H:%M"), rule.end_local.strftime("%H:%M"))
        for rule in updated.summary.availability.weekly_rules
    } == {(wd, "19:30", "21:30") for wd in range(1, 8)}

    assert updated.summary.time_constraint is not None
    assert updated.summary.time_constraint.deadline_at is not None
    assert updated.summary.time_constraint.deadline_at.astimezone(VN_TZ) == datetime(
        2026, 11, 25, 22, 0, tzinfo=VN_TZ
    )

    # DB raw_input phải giữ nguyên văn lời nói gốc của người dùng (không bị ghi đè bản ITN)
    assert updated.raw_input == raw_spoken
    async with itn_db_env["db"]() as db:
        row = await db.scalar(
            select(PlanningDraftModel).where(
                PlanningDraftModel.id == draft_service.resolve_draft_uuid(created.draft_id)
            )
        )
        assert row is not None
        assert row.raw_input == raw_spoken
        assert "Ờm" in row.raw_input
        assert "bảy rưỡi tối" in row.raw_input

    # Các regex nhận diện sửa/chọn phạm vi ở draft_service vẫn chạy trực tiếp trên bản gốc
    assert "availability" in draft_service._uf1_requested_edits(
        "đổi giờ rảnh sang tối thứ 4", updated.summary
    )
    assert "time_constraint" in draft_service._uf1_requested_edits(
        "lùi deadline sang ngày mai", updated.summary
    )
    assert draft_service._UF1_SCOPE_ALT_DELIVERABLE_RE.search("làm 2 trang hoặc 6 slide chưa chốt")
    assert draft_service._UF1_SCOPE_UNDECIDED_RE.search("làm 2 trang hoặc 6 slide chưa chốt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn2_spoken", "preserved_phrase"),
    [
        ("làm được chín mươi phút", "chín mươi phút"),
        ("làm được tiếng rưỡi", "tiếng rưỡi"),
    ],
)
async def test_itn_turn2_spoken_duration_completes_open_ended_window_and_preserves_raw_input(
    itn_db_env,
    monkeypatch: pytest.MonkeyPatch,
    turn2_spoken: str,
    preserved_phrase: str,
) -> None:
    """RISK-02 + P1-C1: Turn 2 nói thời lượng bằng chữ hoàn tất cửa sổ #8 -> 19:30–21:00, DB giữ nguyên văn."""
    user_id = itn_db_env["owner"]
    tomorrow = (FROZEN_NOW + timedelta(days=1)).date()
    deadline = datetime(
        (tomorrow + timedelta(days=2)).year,
        (tomorrow + timedelta(days=2)).month,
        (tomorrow + timedelta(days=2)).day,
        23,
        59,
        tzinfo=VN_TZ,
    )
    tc = ExtractedTimeConstraint(
        kind="deadline",
        start_at=FROZEN_NOW,
        deadline_at=deadline,
        input_timezone="Asia/Ho_Chi_Minh",
    )
    turn1_raw = "tối mai viết báo cáo rảnh sau bảy rưỡi tối"

    async with itn_db_env["db"]() as db:
        created = await draft_service.create_draft(
            user_id=user_id,
            idempotency_key=f"itn-t2-create-{uuid4().hex}",
            payload=CreateDraftRequest(
                client_draft_id=f"itn-t2-{uuid4().hex}",
                input_source="voice",
                raw_input=turn1_raw,
            ),
            db=db,
        )

    turns = iter(
        [
            LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo tiến độ",
                scope="Báo cáo hai trang",
                time_constraint=tc,
                deadline=deadline,
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="specific_date",
                        date=tomorrow.isoformat(),
                        start_local="19:30",
                        end_local=None,
                        raw_phrase=turn1_raw,
                    )
                ],
                is_complete=False,
                summary="Chờ giờ kết thúc từ 19:30.",
            ),
            LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo tiến độ",
                scope="Báo cáo hai trang",
                time_constraint=tc,
                deadline=deadline,
                structured_free_windows=[],
                is_complete=True,
                summary="Đã bổ sung thời lượng.",
            ),
        ]
    )

    async def _fake_provider(**kwargs: Any):
        return next(turns), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_provider)

    async with itn_db_env["db"]() as db:
        after_t1 = await draft_service.analyze_draft(
            user_id=user_id,
            draft_id=created.draft_id,
            idempotency_key=f"itn-t2-a1-{uuid4().hex}",
            payload=DraftAnalyzeRequest(
                expected_summary_revision=created.summary_revision,
                expected_planning_input_revision=created.planning_input_revision,
                raw_input=turn1_raw,
            ),
            db=db,
        )
    assert after_t1.summary.can_plan is False

    async with itn_db_env["db"]() as db:
        after_t2 = await draft_service.analyze_draft(
            user_id=user_id,
            draft_id=created.draft_id,
            idempotency_key=f"itn-t2-a2-{uuid4().hex}",
            payload=DraftAnalyzeRequest(
                expected_summary_revision=after_t1.summary_revision,
                expected_planning_input_revision=after_t1.planning_input_revision,
                message=turn2_spoken,
            ),
            db=db,
        )

    assert after_t2.summary.can_plan is True
    assert not any(issue.blocking for issue in after_t2.summary.clarification_issues)
    assert after_t2.summary.availability is not None
    assert {
        item.date.isoformat(): [
            (iv.start_local.strftime("%H:%M"), iv.end_local.strftime("%H:%M"))
            for iv in item.intervals
        ]
        for item in after_t2.summary.availability.date_overrides
    } == {tomorrow.isoformat(): [("19:30", "21:00")]}

    async with itn_db_env["db"]() as db:
        row = await db.scalar(
            select(PlanningDraftModel).where(
                PlanningDraftModel.id == draft_service.resolve_draft_uuid(created.draft_id)
            )
        )
        assert row is not None
        assert preserved_phrase in row.raw_input
        assert turn1_raw in row.raw_input
