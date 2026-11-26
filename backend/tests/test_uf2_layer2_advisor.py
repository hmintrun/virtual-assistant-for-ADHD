"""Kiểm thử Layer 2 (Deterministic Advisor Layer) cho UF2: U2-BE-04, U2-BE-05, U2-BE-06."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.deps import get_db
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import Base
from app.schemas.availability import DateOverride, DraftAvailability, LocalInterval, WeeklyRule
from app.schemas.calendar import (
    BusyInterval,
    BusyIntervalSource,
    CalendarContext,
    ManualBusyInterval,
)
from app.schemas.common import SessionSegment, TimeConstraint, TimeInterval
from app.schemas.planning import (
    AppliedExtraConstraints,
    CandidateStep,
    CandidateVerificationScope,
    FeasibilityReport,
    PlanCandidate,
)
from app.schemas.settings import UserPreference
from app.schemas.task import Session, StepPromptItem, TaskDecompositionResult
from app.services import plan_service
from app.services.ai_service import ai_service
from app.services.planning.scheduling import _diagnose_fragmented_reason
from app.services.scheduler_engine import (
    ExtraSchedulingConstraints,
    compute_effective_free_intervals,
    compute_feasibility_recovery,
    estimate_extra_free_seconds_per_day,
    find_earliest_feasible_end_at,
    parse_extra_requirements,
    schedule_candidate_sessions,
    serialize_extra_constraints,
)


def _pref(
    *,
    work_interval_seconds: int = 1500,
    rest_interval_seconds: int = 300,
    buffer_multiplier: float = 1.0,
) -> UserPreference:
    return UserPreference(
        revision=1,
        effective_revision=1,
        work_interval_seconds=work_interval_seconds,
        rest_interval_seconds=rest_interval_seconds,
        micro_task_seconds=120,
        reminder_offsets=[900, 600, 300],
        buffer_multiplier=buffer_multiplier,
        reminder_style="gentle",
    )


def _step(
    key: str = "step-1",
    *,
    order_index: int = 0,
    base_seconds: int = 1800,
    buffer_multiplier: float = 1.0,
    title: str = "Thực hiện bước",
) -> CandidateStep:
    return CandidateStep(
        client_step_key=key,
        title=title,
        expected_output=f"Kết quả {key}",
        order_index=order_index,
        estimated_base_seconds=base_seconds,
        remaining_base_seconds=base_seconds,
        buffer_multiplier=buffer_multiplier,
        estimate_source="user_confirmed",
        status="pending",
    )


def _fixed_session(session_id: str, start: datetime, duration_seconds: int = 1800) -> Session:
    return Session(
        session_id=session_id,
        step_id="other-step",
        scheduled_start=start,
        scheduled_end=start + timedelta(seconds=duration_seconds),
        micro_task_seconds=120,
        micro_task_text="Chuẩn bị phiên cố định",
        work_seconds=duration_seconds,
        rest_seconds=0,
        segments=[SessionSegment(order_index=0, kind="work", duration_seconds=duration_seconds)],
        duration_seconds=duration_seconds,
        schedule_revision=1,
        status="scheduled",
        start_state="waiting",
        start_decision_due_at=start + timedelta(seconds=900),
    )


def _daily_availability(
    start_local: str,
    end_local: str,
    *,
    tz: str = "UTC",
) -> DraftAvailability:
    return DraftAvailability(
        timezone=tz,
        weekly_rules=[
            WeeklyRule(weekday=wd, start_local=start_local, end_local=end_local)  # type: ignore[arg-type]
            for wd in range(1, 8)
        ],
        date_overrides=[],
    )


@pytest.fixture
async def api(monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    async def unavailable(**kwargs):
        raise ConnectionError("Providers unavailable in Layer 2 test")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", unavailable)
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def _post(client: AsyncClient, path: str, key: str, payload: dict):
    return await client.post("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _patch(client: AsyncClient, path: str, key: str, payload: dict):
    return await client.patch("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


def _make_summary(
    *,
    start_at: datetime,
    deadline_at: datetime,
    weekly_rules: list[dict[str, object]],
    extra_requirements: list[str] | None = None,
    timezone_name: str = "UTC",
) -> dict[str, object]:
    return {
        "title": "Ôn tập kiến trúc hệ thống",
        "description": "Tổng hợp và luyện tập các chủ đề trọng tâm",
        "scope": "Hoàn thành toàn bộ 3 chương",
        "extra_requirements": extra_requirements or [],
        "time_constraint": {
            "kind": "deadline",
            "start_at": start_at.isoformat(),
            "deadline_at": deadline_at.isoformat(),
            "input_timezone": timezone_name,
        },
        "availability": {
            "timezone": timezone_name,
            "weekly_rules": weekly_rules,
            "date_overrides": [],
        },
    }


async def _confirmed_draft(client: AsyncClient, key: str, task_summary: dict[str, object]) -> dict:
    created = await _post(
        client,
        "/drafts",
        f"{key}-create",
        {"client_draft_id": key, "input_source": "text", "summary": task_summary},
    )
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    confirmed = await _post(
        client,
        f"/drafts/{draft['draft_id']}/confirm-summary",
        f"{key}-confirm",
        {"expected_summary_revision": draft["summary_revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return (await client.get(f"/api/v1/drafts/{draft['draft_id']}")).json()["data"]


# =============================================================================
# U2-BE-04: AppliedExtraConstraints & FeasibilityReport schema serialization
# =============================================================================


def test_u2_be_04_applied_extra_constraints_schema_and_serialization() -> None:
    """Schema giữ tương thích ngược và serialize đúng các ràng buộc đã áp dụng."""
    start = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    end = datetime(2026, 11, 22, 8, 0, tzinfo=UTC)

    legacy_report = FeasibilityReport.model_validate(
        {
            "status": "feasible",
            "required_seconds": 1500,
            "available_seconds": 3600,
            "unscheduled_seconds": 0,
            "verification_scope": {
                "scope_type": "candidate_steps",
                "client_step_keys": ["step-1"],
                "planning_horizon": {"start_at": start, "end_at": end},
            },
            "fully_scheduled": True,
            "blocking_reasons": [],
            "deficit_seconds": 0,
            "unscheduled_step_keys": [],
            "extension_candidate_at": None,
        }
    )
    assert legacy_report.earliest_feasible_end_at is None
    assert legacy_report.suggested_extra_free_seconds_per_day is None
    assert legacy_report.applied_extra_constraints is None

    with pytest.raises(ValidationError):
        AppliedExtraConstraints(max_daily_work_seconds=0)
    with pytest.raises(ValidationError):
        FeasibilityReport.model_validate(
            {
                **legacy_report.model_dump(),
                "suggested_extra_free_seconds_per_day": 0,
            }
        )

    assert serialize_extra_constraints(ExtraSchedulingConstraints()) is None

    parsed = parse_extra_requirements(
        [
            "Mỗi ngày học tối đa 90 phút",
            "Ít nhất 45 phút trống liền",
            "Ưu tiên buổi sáng",
            "Ưu tiên sau 20h",
        ]
    )
    serialized = serialize_extra_constraints(parsed)
    assert serialized is not None
    assert serialized.max_daily_work_seconds == 5400
    assert serialized.min_contiguous_slot_seconds == 2700
    assert serialized.preferred_time_windows == ["05:00-12:00", "20:00-24:00"]

    avail = _daily_availability("08:00", "12:00")
    free = compute_effective_free_intervals(avail, start, end)
    tc = TimeConstraint(kind="deadline", start_at=start, deadline_at=end, input_timezone="UTC")
    _, report = schedule_candidate_sessions(
        [_step(base_seconds=1800)],
        free,
        [],
        tc,
        _pref(work_interval_seconds=1800, rest_interval_seconds=0),
        extra_requirements=["Mỗi ngày học tối đa 90 phút", "Ưu tiên sau 20h"],
    )
    assert report.applied_extra_constraints is not None
    assert report.applied_extra_constraints.max_daily_work_seconds == 5400
    assert report.applied_extra_constraints.min_contiguous_slot_seconds is None
    assert report.applied_extra_constraints.preferred_time_windows == ["20:00-24:00"]

    _, no_constraint_report = schedule_candidate_sessions(
        [_step(base_seconds=1800)],
        free,
        [],
        tc,
        _pref(work_interval_seconds=1800, rest_interval_seconds=0),
        extra_requirements=["Ghi chú tự do dễ hiểu"],
    )
    assert no_constraint_report.applied_extra_constraints is None


# =============================================================================
# U2-BE-05: Pure-Python Recovery Functions & Proof-of-Feasibility Loop
# =============================================================================


def test_u2_be_05_proof_of_feasibility_loop_with_earliest_end_at() -> None:
    """Nới deadline đúng tới earliest_feasible_end_at giúp schedule_candidate_sessions đạt feasible."""
    start = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    tight_deadline = start + timedelta(days=2)  # Chỉ có 2h rảnh (1h/ngày), cần 5h
    avail = _daily_availability("09:00", "10:00", tz="UTC")
    pref = _pref(work_interval_seconds=1800, rest_interval_seconds=0)
    steps = [
        _step("step-1", order_index=0, base_seconds=9000),  # 2.5h
        _step("step-2", order_index=1, base_seconds=9000),  # 2.5h
    ]

    initial_free = compute_effective_free_intervals(avail, start, tight_deadline)
    initial_tc = TimeConstraint(
        kind="deadline", start_at=start, deadline_at=tight_deadline, input_timezone="UTC"
    )
    _, initial_report = schedule_candidate_sessions(steps, initial_free, [], initial_tc, pref)
    assert initial_report.status == "infeasible"
    assert initial_report.fully_scheduled is False

    earliest_end, extra_per_day = compute_feasibility_recovery(
        initial_report,
        steps,
        avail,
        [],
        start,
        tight_deadline,
        pref,
    )
    assert earliest_end is not None
    assert earliest_end > tight_deadline
    assert extra_per_day is not None and extra_per_day > 0

    # Vòng lặp chứng minh tính khả thi: đưa earliest_end quay lại schedule_candidate_sessions
    horizon_days = max(1, math.ceil((earliest_end - start).total_seconds() / 86400))
    extended_free = compute_effective_free_intervals(avail, start, earliest_end)
    extended_tc = TimeConstraint(
        kind="deadline", start_at=start, deadline_at=earliest_end, input_timezone="UTC"
    )
    sessions, verified_report = schedule_candidate_sessions(
        steps,
        extended_free,
        [],
        extended_tc,
        pref,
        horizon_days=horizon_days,
    )
    assert verified_report.status == "feasible"
    assert verified_report.fully_scheduled is True
    assert sessions[-1].scheduled_end == earliest_end

    # Kiểm chứng biên tối thiểu: rút lại 1 phút trước earliest_end sẽ không còn khả thi
    too_early = earliest_end - timedelta(minutes=1)
    too_early_free = compute_effective_free_intervals(avail, start, too_early)
    too_early_tc = TimeConstraint(
        kind="deadline", start_at=start, deadline_at=too_early, input_timezone="UTC"
    )
    _, still_bad = schedule_candidate_sessions(
        steps, too_early_free, [], too_early_tc, pref, horizon_days=horizon_days
    )
    assert still_bad.fully_scheduled is False


def test_u2_be_05_earliest_end_respects_fixed_gaps_calendar_manual_and_extra_constraints() -> None:
    """find_earliest_feasible_end_at tôn trọng fixed ±900s, calendar busy, manual busy và extra_requirements."""
    start = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    avail = DraftAvailability(
        timezone="UTC",
        weekly_rules=[
            WeeklyRule(weekday=wd, start_local="09:00", end_local="09:40")  # 40p sáng
            for wd in range(1, 8)
        ]
        + [
            WeeklyRule(weekday=wd, start_local="10:00", end_local="12:00")  # 120p trưa
            for wd in range(1, 8)
        ]
        + [
            WeeklyRule(weekday=wd, start_local="20:00", end_local="21:00")  # 60p tối
            for wd in range(1, 8)
        ],
        date_overrides=[],
    )
    pref = _pref(work_interval_seconds=1800, rest_interval_seconds=0)
    steps = [_step("step-1", base_seconds=3600)]  # Cần 60p

    # Ngày 1 (2026-11-23): calendar busy chặn 10:00-12:00, fixed session ở 19:45-20:15 (gap ±900s -> tới 20:30, còn 30p < 60p tối thiểu)
    cal_ctx = CalendarContext(
        context_id="ctx-1",
        revision=1,
        query_start_end=TimeInterval(start_at=start, end_at=start + timedelta(days=10)),
        busy_intervals=[
            BusyInterval(
                start_at=datetime(2026, 11, 23, 10, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
                source=BusyIntervalSource(
                    kind="calendar_event",
                    connection_id="conn-1",
                    google_calendar_id="primary",
                    google_event_id="evt-1",
                ),
            )
        ],
        fetched_at=start,
        sync_state="synced",
        coverage_state="complete",
    )
    fixed = [_fixed_session("fix-1", datetime(2026, 11, 23, 19, 45, tzinfo=UTC), 1800)]
    # Ngày 2 (2026-11-24): manual busy chặn 10:00-12:00 và 20:00-20:20 (chỉ còn 40p < 60p)
    manual = [
        ManualBusyInterval(
            client_interval_id="man-1",
            start_at=datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 11, 24, 12, 0, tzinfo=UTC),
        ),
        ManualBusyInterval(
            client_interval_id="man-2",
            start_at=datetime(2026, 11, 24, 20, 0, tzinfo=UTC),
            end_at=datetime(2026, 11, 24, 20, 20, tzinfo=UTC),
        ),
    ]
    # Ngày 3 (2026-11-25): 09:00-09:40 bị lọc bởi min_contiguous=60p; Pass 1 ưu tiên sau 20h chọn 20:00-21:00 ngày 3!
    earliest = find_earliest_feasible_end_at(
        steps,
        avail,
        fixed,
        start,
        pref,
        calendar_context=cal_ctx,
        manual_busy=manual,
        extra_requirements=["Ít nhất 60 phút trống liền", "Ưu tiên sau 20h"],
        input_timezone="UTC",
    )
    assert earliest == datetime(2026, 11, 25, 21, 0, tzinfo=UTC)

    # Kiểm tra max_daily_work_seconds: 120p work với giới hạn tối đa 60p/ngày phải trải ít nhất qua 2 ngày
    earliest_capped = find_earliest_feasible_end_at(
        [_step("step-cap", base_seconds=7200)],
        _daily_availability("09:00", "13:00", tz="Asia/Ho_Chi_Minh"),
        [],
        datetime(2026, 11, 23, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        pref,
        extra_requirements=["Mỗi ngày làm tối đa 60 phút"],
        input_timezone="Asia/Ho_Chi_Minh",
    )
    assert earliest_capped is not None
    assert earliest_capped.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat() == "2026-11-24"


def test_u2_be_05_earliest_end_edge_cases_and_validation() -> None:
    """Kiểm tra trường hợp rỗng, không đủ slot trong date_overrides và lỗi tham số."""
    start = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    avail = _daily_availability("09:00", "10:00")
    pref = _pref()

    assert find_earliest_feasible_end_at([], avail, [], start, pref) is None

    with pytest.raises(ValueError):
        find_earliest_feasible_end_at(
            [_step()], avail, [], datetime(2026, 11, 23, 0, 0), pref
        )
    with pytest.raises(ValueError):
        find_earliest_feasible_end_at([_step()], avail, [], start, pref, min_gap_seconds=600)

    # Chỉ có 1 ngày override 1h nhưng việc cần 3h -> không thể xếp dù search 365 ngày -> None
    finite_avail = DraftAvailability(
        timezone="UTC",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=start.date(),
                intervals=[LocalInterval(start_local="09:00", end_local="10:00")],
            )
        ],
    )
    assert (
        find_earliest_feasible_end_at(
            [_step(base_seconds=10800)], finite_avail, [], start, pref, max_search_days=500
        )
        is None
    )


def test_u2_be_05_estimate_extra_free_seconds_dst_and_midnight_boundaries() -> None:
    """estimate_extra_free_seconds_per_day tính cả nghỉ nội bộ và đếm đúng ngày địa phương qua nửa đêm/DST."""
    pref = _pref(work_interval_seconds=1500, rest_interval_seconds=300)

    # Invalid/zero cases -> None
    w_start = datetime(2026, 11, 20, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    w_end_1d = datetime(2026, 11, 21, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert estimate_extra_free_seconds_per_day(0, w_start, w_end_1d, pref, "Asia/Ho_Chi_Minh") is None
    assert (
        estimate_extra_free_seconds_per_day(1800, w_end_1d, w_start, pref, "Asia/Ho_Chi_Minh")
        is None
    )

    # 3000s work với 25p work / 5p rest = 1500 + 300 + 1500 = 3300s gross trong đúng 1 ngày (00:00 -> 00:00 hôm sau)
    assert (
        estimate_extra_free_seconds_per_day(3000, w_start, w_end_1d, pref, "Asia/Ho_Chi_Minh")
        == 3300
    )

    # Qua nửa đêm theo giờ VN (23:00 -> 01:00 hôm sau = 2 ngày lịch VN, nhưng chỉ 1 ngày theo UTC)
    cross_start = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)  # 23:00+07
    cross_end = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)  # 01:00+07 ngày 21
    assert (
        estimate_extra_free_seconds_per_day(3000, cross_start, cross_end, pref, "Asia/Ho_Chi_Minh")
        == 1650
    )
    assert estimate_extra_free_seconds_per_day(3000, cross_start, cross_end, pref, "UTC") == 3300

    # DST fall-back 25 giờ tại America/New_York (2026-11-01 00:00-04:00 -> 2026-11-02 00:00-05:00) vẫn là 1 ngày
    ny = ZoneInfo("America/New_York")
    dst_fall_start = datetime(2026, 11, 1, 0, 0, tzinfo=ny)
    dst_fall_end = datetime(2026, 11, 2, 0, 0, tzinfo=ny)
    assert int((dst_fall_end.astimezone(UTC) - dst_fall_start.astimezone(UTC)).total_seconds()) == 25 * 3600
    assert (
        estimate_extra_free_seconds_per_day(3000, dst_fall_start, dst_fall_end, pref, "America/New_York")
        == 3300
    )

    # DST spring-forward 23 giờ tại America/New_York (2026-03-08 00:00-05:00 -> 2026-03-09 00:00-04:00) vẫn là 1 ngày
    dst_spring_start = datetime(2026, 3, 8, 0, 0, tzinfo=ny)
    dst_spring_end = datetime(2026, 3, 9, 0, 0, tzinfo=ny)
    assert int((dst_spring_end.astimezone(UTC) - dst_spring_start.astimezone(UTC)).total_seconds()) == 23 * 3600
    assert (
        estimate_extra_free_seconds_per_day(
            3000, dst_spring_start, dst_spring_end, pref, "America/New_York"
        )
        == 3300
    )


def test_u2_be_05_compute_feasibility_recovery_controls() -> None:
    """compute_feasibility_recovery trả (None, None) khi feasible và bỏ qua nới hạn khi skip_deadline_extension=True."""
    start = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=2)
    avail = _daily_availability("09:00", "11:00")
    pref = _pref(work_interval_seconds=1800, rest_interval_seconds=0)
    steps = [_step(base_seconds=7200)]

    feasible_report = FeasibilityReport(
        status="feasible",
        required_seconds=7200,
        available_seconds=14400,
        unscheduled_seconds=0,
        deficit_seconds=0,
        verification_scope=CandidateVerificationScope(
            client_step_keys=["step-1"],
            planning_horizon=TimeInterval(start_at=start, end_at=end),
        ),
        fully_scheduled=True,
    )
    assert compute_feasibility_recovery(feasible_report, steps, avail, [], start, end, pref) == (
        None,
        None,
    )

    infeasible_report = feasible_report.model_copy(
        update={
            "status": "search_inconclusive",
            "fully_scheduled": False,
            "required_seconds": None,
            "unscheduled_seconds": 3600,
            "deficit_seconds": 0,
        }
    )
    assert compute_feasibility_recovery(infeasible_report, [], avail, [], start, end, pref) == (
        None,
        None,
    )

    earliest_skipped, extra_only = compute_feasibility_recovery(
        infeasible_report,
        steps,
        avail,
        [],
        start,
        end,
        pref,
        skip_deadline_extension=True,
    )
    assert earliest_skipped is None
    assert extra_only == 1800


# =============================================================================
# U2-BE-06: Diagnostic FRAGMENTED_FREE_SLOTS & Step-1 Horizon Overflow
# =============================================================================


def test_u2_be_06_fragmented_free_slots_diagnostics() -> None:
    """FRAGMENTED_FREE_SLOTS giải thích rõ nút thắt daily cap, min contiguous slot và giữ fallback mặc định."""
    start = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=2)  # 2 ngày
    pref = _pref(work_interval_seconds=1500, rest_interval_seconds=0)
    tc = TimeConstraint(kind="deadline", start_at=start, deadline_at=end, input_timezone="UTC")

    # Case A: Tổng rảnh 6h >= 3h việc, nhưng giới hạn tối đa 60p/ngày trên 2 ngày -> chỉ làm được 2h < 3h
    avail_a = _daily_availability("09:00", "12:00")
    free_a = compute_effective_free_intervals(avail_a, start, end)
    _, rep_a = schedule_candidate_sessions(
        [_step(base_seconds=10800)],
        free_a,
        [],
        tc,
        pref,
        extra_requirements=["Mỗi ngày học tối đa 60 phút"],
    )
    assert rep_a.status == "search_inconclusive"
    assert rep_a.deficit_seconds == 0
    assert rep_a.blocking_reasons[0].code == "FRAGMENTED_FREE_SLOTS"
    assert "tối đa 60 phút/ngày" in rep_a.blocking_reasons[0].message

    # Case B: Tổng rảnh đủ (4 khe 30p + 1 khe 60p = 180p >= 120p việc), nhưng yêu cầu khe rảnh liên tục >= 60p
    avail_b = DraftAvailability(
        timezone="UTC",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=start.date(),
                intervals=[
                    LocalInterval(start_local="09:00", end_local="09:30"),
                    LocalInterval(start_local="10:00", end_local="10:30"),
                    LocalInterval(start_local="11:00", end_local="11:30"),
                    LocalInterval(start_local="12:00", end_local="12:30"),
                    LocalInterval(start_local="14:00", end_local="15:00"),
                ],
            )
        ],
    )
    free_b = compute_effective_free_intervals(avail_b, start, end)
    _, rep_b = schedule_candidate_sessions(
        [_step(base_seconds=7200)],
        free_b,
        [],
        tc,
        pref,
        extra_requirements=["Chỉ xếp khi có ít nhất 60 phút trống liền"],
    )
    assert rep_b.status == "search_inconclusive"
    assert rep_b.deficit_seconds == 0
    assert rep_b.blocking_reasons[0].code == "FRAGMENTED_FREE_SLOTS"
    assert "khe rảnh liên tục tối thiểu 60 phút" in rep_b.blocking_reasons[0].message

    # Case C: Không có extra_requirements -> fallback message mặc định
    avail_c = DraftAvailability(
        timezone="UTC",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=start.date(),
                intervals=[
                    LocalInterval(start_local="09:00", end_local="09:04"),  # < min_chunk 5p -> bị bỏ
                    LocalInterval(start_local="10:00", end_local="10:25"),  # 25p
                ],
            )
        ],
    )
    free_c = compute_effective_free_intervals(avail_c, start, end)
    _, rep_c = schedule_candidate_sessions([_step(base_seconds=1680)], free_c, [], tc, pref)
    assert rep_c.status == "search_inconclusive"
    assert rep_c.deficit_seconds == 0
    assert rep_c.blocking_reasons[0].code == "FRAGMENTED_FREE_SLOTS"
    assert (
        rep_c.blocking_reasons[0].message
        == "Chưa xếp đủ Steps theo thứ tự, giờ rảnh, nghỉ nội bộ và gap; chưa chứng minh vô nghiệm."
    )


async def test_u2_be_06_step1_horizon_slice_overflow_sets_recovery_fields(api, monkeypatch) -> None:
    """Khi Step 1 vượt chặng 14 ngày đầu nhưng đủ trong toàn dự án: earliest_feasible_end_at=None và suggested_extra_free_seconds_per_day > 0."""
    client, _ = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = (now + timedelta(days=2)).replace(hour=0)
    deadline_at = start_at + timedelta(days=30)

    # 35p/ngày -> tối đa 1800s work/slot -> 14d = 25,200s work, 30d = 54,000s work.
    # Step 1 = 480p * 1.2 = 34,560s (> 25,200s ở 14d nhưng cả 2 bước = 38,880s <= 54,000s ở 30d).
    summary_30d = _make_summary(
        start_at=start_at,
        deadline_at=deadline_at,
        weekly_rules=[{"weekday": wd, "start_local": "09:00", "end_local": "09:35"} for wd in range(1, 8)],
        timezone_name="UTC",
    )
    draft = await _confirmed_draft(client, "step1-overflow-draft", summary_30d)

    monkeypatch.setattr(
        ai_service,
        "decompose_task",
        AsyncMock(
            return_value=TaskDecompositionResult(
                task_title="Dự án dài",
                steps=[
                    StepPromptItem(
                        order_index=1,
                        title="Bước 1 rất lớn",
                        expected_output="Hoàn tất phần lõi",
                        estimated_minutes=480,
                        micro_task_prompt="Mở tài liệu phần lõi.",
                    ),
                    StepPromptItem(
                        order_index=2,
                        title="Bước 2 nhẹ",
                        expected_output="Hoàn tất kiểm tra",
                        estimated_minutes=60,
                        micro_task_prompt="Mở checklist.",
                    ),
                ],
                reasoning="Chia 2 giai đoạn.",
                generation_tier="tier_1_gemini",
            )
        ),
    )

    cand_resp = await _post(
        client,
        f"/drafts/{draft['draft_id']}/candidates",
        "step1-overflow-cand",
        {
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
        },
    )
    assert cand_resp.status_code == 201, cand_resp.text
    feas = cand_resp.json()["data"]["feasibility"]
    assert feas["status"] == "search_inconclusive"
    assert feas["fully_scheduled"] is False
    assert feas["blocking_reasons"][0]["code"] == "FRAGMENTED_FREE_SLOTS"
    assert feas["earliest_feasible_end_at"] is None
    assert feas["suggested_extra_free_seconds_per_day"] is not None
    assert feas["suggested_extra_free_seconds_per_day"] > 0


# =============================================================================
# E2E API Integration: POST /drafts/{id}/candidates & PATCH /candidates/{id}
# =============================================================================


async def test_u2_be_04_05_06_e2e_candidates_infeasible_then_patch_feasible(api) -> None:
    """POST infeasible trả đầy đủ recovery fields; PATCH sửa bước về feasible xóa recovery và giữ applied_extra_constraints."""
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = (now + timedelta(days=2)).replace(hour=0)
    deadline_at = start_at + timedelta(days=2)  # 2 ngày, 1h/ngày = 2h rảnh

    task_summary = _make_summary(
        start_at=start_at,
        deadline_at=deadline_at,
        weekly_rules=[{"weekday": wd, "start_local": "19:00", "end_local": "20:00"} for wd in range(1, 8)],
        extra_requirements=["Mỗi ngày học tối đa 60 phút", "Ưu tiên buổi tối"],
        timezone_name="UTC",
    )
    draft = await _confirmed_draft(client, "e2e-layer2-draft", task_summary)

    # 3 giờ gốc * 1.2 buffer = 3.6 giờ > 2 giờ rảnh -> infeasible
    create_resp = await _post(
        client,
        f"/drafts/{draft['draft_id']}/candidates",
        "e2e-layer2-cand-1",
        {
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
            "manual_steps": [
                {
                    "order_index": 0,
                    "title": "Nghiên cứu và tổng hợp",
                    "expected_output": "Tài liệu tóm tắt",
                    "estimated_base_seconds": 10800,
                }
            ],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cand1 = create_resp.json()["data"]
    feas1 = cand1["feasibility"]
    assert feas1["status"] == "infeasible"
    assert feas1["fully_scheduled"] is False
    assert feas1["deficit_seconds"] > 0
    assert feas1["earliest_feasible_end_at"] is not None
    earliest_dt = datetime.fromisoformat(feas1["earliest_feasible_end_at"].replace("Z", "+00:00"))
    assert earliest_dt > deadline_at
    assert feas1["suggested_extra_free_seconds_per_day"] is not None
    assert feas1["suggested_extra_free_seconds_per_day"] > 0
    assert feas1["applied_extra_constraints"] == {
        "max_daily_work_seconds": 3600,
        "min_contiguous_slot_seconds": None,
        "preferred_time_windows": ["18:00-24:00"],
    }

    # PATCH giảm khối lượng bước 1 về 1800s (buffered 2160s <= 3600s/ngày) -> feasible, xóa recovery fields
    patch_resp = await _patch(
        client,
        f"/candidates/{cand1['candidate_id']}",
        "e2e-layer2-patch-feasible",
        {
            "expected_review_revision": cand1["review_revision"],
            "edits": {
                "steps": [
                    {
                        "client_step_key": cand1["steps"][0]["client_step_key"],
                        "estimated_base_seconds": 1800,
                    }
                ]
            },
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand2 = patch_resp.json()["data"]
    feas2 = cand2["feasibility"]
    assert feas2["status"] == "feasible"
    assert feas2["fully_scheduled"] is True
    assert feas2["deficit_seconds"] == 0
    assert feas2["earliest_feasible_end_at"] is None
    assert feas2["suggested_extra_free_seconds_per_day"] is None
    assert feas2["applied_extra_constraints"] == {
        "max_daily_work_seconds": 3600,
        "min_contiguous_slot_seconds": None,
        "preferred_time_windows": ["18:00-24:00"],
    }

    # Kiểm chứng thêm nhánh _review_feasibility(feasible) xóa recovery fields khi chỉnh thẳng giờ phiên (changed_timing=False)
    async with factory() as db:
        _, parsed_draft, _ = await plan_service._read_candidate(db, "user_a", cand2["candidate_id"])
        source = await plan_service._sources(
            db, "user_a", parsed_draft, PlanCandidate.model_validate(cand2).planning_horizon, read_only=True
        )
    stale_recovery = PlanCandidate.model_validate(cand2).model_copy(
        update={
            "feasibility": PlanCandidate.model_validate(cand2).feasibility.model_copy(
                update={
                    "status": "infeasible",
                    "fully_scheduled": False,
                    "earliest_feasible_end_at": earliest_dt,
                    "suggested_extra_free_seconds_per_day": 1800,
                }
            )
        }
    )
    reviewed = plan_service._review_feasibility(stale_recovery, source)
    assert reviewed.feasibility.status == "feasible"
    assert reviewed.feasibility.earliest_feasible_end_at is None
    assert reviewed.feasibility.suggested_extra_free_seconds_per_day is None
    assert reviewed.feasibility.applied_extra_constraints is not None
    assert reviewed.feasibility.applied_extra_constraints.max_daily_work_seconds == 3600


def test_u2_be_05_earliest_end_honors_min_contiguous_slot_for_proof_of_feasibility() -> None:
    """Fix 1: earliest_feasible_end_at phải bao trùm đủ min_contiguous_slot_seconds để round-trip đạt feasible."""
    start = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    short_deadline = datetime(2026, 11, 23, 9, 45, tzinfo=UTC)
    avail = _daily_availability("09:00", "12:00", tz="UTC")
    pref = _pref(work_interval_seconds=1800, rest_interval_seconds=0)
    steps = [_step("step-1", base_seconds=1800)]
    extra = ["Chỉ xếp khi có ít nhất 60 phút trống liền"]

    initial_free = compute_effective_free_intervals(avail, start, short_deadline)
    initial_tc = TimeConstraint(
        kind="deadline", start_at=start, deadline_at=short_deadline, input_timezone="UTC"
    )
    _, initial_report = schedule_candidate_sessions(
        steps, initial_free, [], initial_tc, pref, extra_requirements=extra
    )
    assert initial_report.status == "search_inconclusive"
    assert initial_report.blocking_reasons[0].code == "FRAGMENTED_FREE_SLOTS"

    earliest_end = find_earliest_feasible_end_at(
        steps,
        avail,
        [],
        start,
        pref,
        extra_requirements=extra,
        input_timezone="UTC",
    )
    assert earliest_end is not None
    assert earliest_end == datetime(2026, 11, 23, 10, 0, tzinfo=UTC)

    verified_free = compute_effective_free_intervals(avail, start, earliest_end)
    verified_tc = TimeConstraint(
        kind="deadline", start_at=start, deadline_at=earliest_end, input_timezone="UTC"
    )
    sessions, verified_report = schedule_candidate_sessions(
        steps, verified_free, [], verified_tc, pref, extra_requirements=extra
    )
    assert verified_report.status == "feasible"
    assert verified_report.fully_scheduled is True
    assert len(sessions) == 1
    assert sessions[0].scheduled_start == datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    assert sessions[0].scheduled_end == datetime(2026, 11, 23, 9, 30, tzinfo=UTC)


async def test_u2_be_06_unexpanded_scope_horizon_only_overflow_scopes_recovery_to_14d(
    api,
) -> None:
    """Fix 2: khi allow_horizon_slice=False (có unexpanded_scope) và horizon < full_end, recovery chỉ tính trên 14d."""
    client, _ = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = (now + timedelta(days=2)).replace(hour=0)
    deadline_at = start_at + timedelta(days=30)

    summary_30d = _make_summary(
        start_at=start_at,
        deadline_at=deadline_at,
        weekly_rules=[
            {"weekday": wd, "start_local": "09:00", "end_local": "09:35"} for wd in range(1, 8)
        ],
        timezone_name="UTC",
    )
    draft = await _confirmed_draft(client, "unexpanded-14d-draft", summary_30d)

    # 30,000s * 1.2 = 36,000s work > 14 * 1800s = 25,200s trong 14d đầu (nhưng 30d có 54,000s).
    resp = await _post(
        client,
        f"/drafts/{draft['draft_id']}/candidates",
        "unexpanded-14d-cand",
        {
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
            "manual_steps": [
                {
                    "order_index": 0,
                    "title": "Giai đoạn 1 mở rộng",
                    "expected_output": "Hoàn thành khung đầu",
                    "estimated_base_seconds": 30000,
                }
            ],
            "unexpanded_scope": "Phần còn lại từ tuần 3 trở đi",
        },
    )
    assert resp.status_code == 201, resp.text
    cand = resp.json()["data"]
    feas = cand["feasibility"]
    assert feas["fully_scheduled"] is False
    assert feas["earliest_feasible_end_at"] is None

    horizon_start = datetime.fromisoformat(cand["planning_horizon"]["start_at"].replace("Z", "+00:00"))
    horizon_end = datetime.fromisoformat(cand["planning_horizon"]["end_at"].replace("Z", "+00:00"))
    pref = _pref()
    expected_14d = estimate_extra_free_seconds_per_day(
        feas["unscheduled_seconds"], horizon_start, horizon_end, pref, "UTC"
    )
    wrong_30d = estimate_extra_free_seconds_per_day(
        feas["unscheduled_seconds"], horizon_start, deadline_at, pref, "UTC"
    )
    assert expected_14d is not None and wrong_30d is not None
    assert expected_14d > wrong_30d
    assert feas["suggested_extra_free_seconds_per_day"] == expected_14d


def test_u2_be_06_asymmetric_days_capped_total_and_subminute_formatting() -> None:
    """Fix 3: tổng rảnh lệch ngày (4h + 15m) bị giới hạn 60m/ngày vẫn chẩn đoán đúng và không in '0 phút'."""
    day1 = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    day2 = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)
    avail = DraftAvailability(
        timezone="UTC",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=day1.date(),
                intervals=[LocalInterval(start_local="09:00", end_local="13:00")],  # 4h
            ),
            DateOverride(
                date=day2.date(),
                intervals=[LocalInterval(start_local="09:00", end_local="09:15")],  # 15p
            ),
        ],
    )
    free = compute_effective_free_intervals(avail, day1, end)
    pref = _pref(work_interval_seconds=900, rest_interval_seconds=0)
    tc = TimeConstraint(kind="deadline", start_at=day1, deadline_at=end, input_timezone="UTC")

    # 90p (5400s) <= 255p tổng rảnh, nhưng capped_total = min(240, 60) + min(15, 60) = 75p < 90p!
    _, report = schedule_candidate_sessions(
        [_step(base_seconds=5400)],
        free,
        [],
        tc,
        pref,
        extra_requirements=["Mỗi ngày học tối đa 60 phút"],
    )
    assert report.status == "search_inconclusive"
    assert report.deficit_seconds == 0
    assert report.blocking_reasons[0].code == "FRAGMENTED_FREE_SLOTS"
    assert "tối đa 60 phút/ngày" in report.blocking_reasons[0].message

    subminute_msg = _diagnose_fragmented_reason(
        [(day1.replace(hour=9), day1.replace(hour=9, second=30))],
        ExtraSchedulingConstraints(max_daily_work_seconds=45, min_contiguous_slot_seconds=45),
        total_work=120,
        local_tz=ZoneInfo("UTC"),
    )
    assert "0 phút" not in subminute_msg
    assert "tối đa 45 giây/ngày" in subminute_msg
    assert "khe rảnh liên tục tối thiểu 45 giây" in subminute_msg
