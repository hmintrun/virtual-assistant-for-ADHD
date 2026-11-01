"""Bộ Unit Test toàn diện cho lõi thuật toán lập lịch và thích ứng (Scheduler Engine).

Kiểm thử các bài toán:
- A02.2: Kiểm điều kiện sang UF2 (Validation, TimeConstraint, Availability).
- A05: Tính thời gian dự phòng Buffer Time (Bảo toàn ước lượng gốc, nhân 1 lần).
- A08: Phân rã cấu trúc Session và SessionSegments (work/rest xen kẽ).
- A06: Tính khoảng giờ khả dụng (Two-pointer interval subtraction, own-event protection).
- A07, A09, A10: Xếp lịch First-Fit, kiểm tra gap 15 phút (900s), Rolling Horizon và FeasibilityReport.
- A17: Tái lập lịch thích ứng UF6 (tịnh tiến ô lịch trôi qua, cô lập allowed_task_ids).
- A20: Học hệ số dự phòng thích ứng (lọc mẫu sạch cấp Step, trung vị median, kẹp policy).
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.schemas.availability import Availability, WeeklyRule
from app.schemas.calendar import BusyInterval, BusyIntervalSource, CalendarContext
from app.schemas.common import SessionSegment, TimeConstraint, TimeInterval
from app.schemas.draft import AvailabilityRef, TaskSummary
from app.schemas.planning import CandidatePart, CandidateStep
from app.schemas.settings import UserPreference
from app.schemas.task import Session
from app.services.scheduler_engine import (
    build_session_segments,
    calculate_buffered_work,
    compute_effective_free_intervals,
    evaluate_buffer_adaptation,
    schedule_candidate_sessions,
    shift_elapsed_sessions,
    validate_planning_prerequisites,
)

# ==============================================================================
# 1. Test A05 & A08: Buffer Time & Cấu trúc Quãng làm/nghỉ
# ==============================================================================


def test_calculate_buffered_work_basic():
    """Kiểm tra công thức W_buffered = int(ceil(base * k)) (A05)."""
    # 50 phút (3000s) nhân 1.2 -> 3600s
    assert calculate_buffered_work(3000, 1.2) == 3600

    # Làm tròn lên số nguyên trần (ceiling)
    assert calculate_buffered_work(333, 1.2) == 400
    assert calculate_buffered_work(100, 1.05) == 105

    # Lỗi khi base <= 0 hoặc k <= 0
    with pytest.raises(ValueError):
        calculate_buffered_work(0, 1.2)
    with pytest.raises(ValueError):
        calculate_buffered_work(1000, 0.0)


def test_build_session_segments_k03():
    """Ca kiểm chứng K03: 3600s work, max work 1500s, rest 300s -> 5 segments."""
    segments, work_sec, rest_sec, duration_sec = build_session_segments(
        buffered_work_seconds=3600,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
    )
    assert work_sec == 3600
    assert rest_sec == 600
    assert duration_sec == 4200
    assert len(segments) == 5

    # Thứ tự: work(1500) -> rest(300) -> work(1500) -> rest(300) -> work(600)
    assert segments[0].kind == "work" and segments[0].duration_seconds == 1500
    assert segments[1].kind == "rest" and segments[1].duration_seconds == 300
    assert segments[2].kind == "work" and segments[2].duration_seconds == 1500
    assert segments[3].kind == "rest" and segments[3].duration_seconds == 300
    assert segments[4].kind == "work" and segments[4].duration_seconds == 600
    # Quãng cuối cùng luôn là work (không có rest thừa)
    assert segments[-1].kind == "work"


def test_build_session_segments_zero_rest():
    """Khi cấu hình nghỉ nội bộ = 0, chỉ sinh các quãng work."""
    segments, work_sec, rest_sec, duration_sec = build_session_segments(
        buffered_work_seconds=2000,
        work_interval_seconds=1000,
        rest_interval_seconds=0,
    )
    assert work_sec == 2000
    assert rest_sec == 0
    assert duration_sec == 2000
    assert len(segments) == 2
    assert all(s.kind == "work" for s in segments)


# ==============================================================================
# 2. Test A02.2: Kiểm tra điều kiện sang UF2
# ==============================================================================


@pytest.fixture
def sample_valid_availability():
    return Availability(
        availability_id="avail_1",
        revision=1,
        source="user_confirmed",
        timezone="Asia/Ho_Chi_Minh",
        confirmed_at=datetime(2026, 10, 30, 8, 0, tzinfo=UTC),
        weekly_rules=[
            WeeklyRule(weekday=w, start_local="08:00", end_local="18:00")
            for w in range(1, 8)
        ],
        date_overrides=[],
        task_limits=[],
        effective_intervals=None,
        calendar_context=None,
    )


@pytest.fixture
def sample_valid_summary():
    return TaskSummary(
        title="Làm báo cáo môn Phân tích Dữ liệu",
        description="Nghiên cứu tài liệu và hoàn thành báo cáo",
        goal="Nộp đúng hạn cho giảng viên",
        scope="Tài liệu báo cáo định dạng PDF hoàn chỉnh",
        time_constraint=TimeConstraint(
            kind="deadline",
            start_at=datetime(2026, 10, 30, 8, 0, tzinfo=timezone(timedelta(hours=7))),
            deadline_at=datetime(2026, 11, 2, 17, 0, tzinfo=timezone(timedelta(hours=7))),
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        availability_ref=AvailabilityRef(availability_id="avail_1", revision=1),
        importance=3,
        can_plan=True,
        field_sources={},
    )


def test_validation_success(sample_valid_summary, sample_valid_availability):
    can_plan, issues = validate_planning_prerequisites(
        sample_valid_summary, sample_valid_availability
    )
    assert can_plan is True
    assert len(issues) == 0


def test_validation_missing_fields(sample_valid_summary, sample_valid_availability):
    invalid_summary = sample_valid_summary.model_copy(
        update={"title": "", "scope": None, "time_constraint": None}
    )
    can_plan, issues = validate_planning_prerequisites(
        invalid_summary, sample_valid_availability
    )
    assert can_plan is False
    codes = {iss.code for iss in issues}
    assert "MISSING_TITLE" in codes
    assert "MISSING_SCOPE" in codes
    assert "MISSING_TIME_CONSTRAINT" in codes


def test_validation_unconfirmed_availability(sample_valid_summary, sample_valid_availability):
    unconfirmed_avail = sample_valid_availability.model_copy(update={"confirmed_at": None})
    can_plan, issues = validate_planning_prerequisites(
        sample_valid_summary, unconfirmed_avail
    )
    assert can_plan is False
    assert any(iss.code == "AVAILABILITY_NOT_CONFIRMED" for iss in issues)


# ==============================================================================
# 3. Test A06: Tính khoảng giờ khả dụng & Two-pointer Subtraction
# ==============================================================================


def test_compute_effective_free_intervals_basic(sample_valid_availability):
    # Khung giờ ngày 2026-10-30: 08:00 - 12:00 (Asia/Ho_Chi_Minh, tức 01:00 - 05:00 UTC)
    # Rảnh 08:00 - 18:00 theo weekly_rule.
    w_start = datetime(2026, 10, 30, 8, 0, tzinfo=timezone(timedelta(hours=7)))
    w_end = datetime(2026, 10, 30, 12, 0, tzinfo=timezone(timedelta(hours=7)))

    # Khoảng bận từ 09:00 - 10:00
    b_start = datetime(2026, 10, 30, 9, 0, tzinfo=timezone(timedelta(hours=7)))
    b_end = datetime(2026, 10, 30, 10, 0, tzinfo=timezone(timedelta(hours=7)))

    cal_ctx = CalendarContext(
        context_id="ctx_1",
        revision=1,
        availability_ref=AvailabilityRef(availability_id="avail_1", revision=1),
        sync_state="synced",
        selected_calendar_ids=["cal_primary"],
        selected_calendar_sources=[],
        query_start_end=TimeInterval(start_at=w_start, end_at=w_end),
        busy_intervals=[
            BusyInterval(
                start_at=b_start,
                end_at=b_end,
                source=BusyIntervalSource(kind="calendar_event"),
            )
        ],
        coverage_state="complete",
        fetched_at=datetime.now(UTC),
    )

    free_intervals = compute_effective_free_intervals(
        availability=sample_valid_availability,
        window_start=w_start,
        window_end=w_end,
        calendar_context=cal_ctx,
    )

    # Mong đợi 2 khoảng rảnh: 08:00-09:00 và 10:00-12:00
    assert len(free_intervals) == 2
    int1 = free_intervals[0]
    int2 = free_intervals[1]
    assert int1.start_at.astimezone(timezone(timedelta(hours=7))).hour == 8
    assert int1.end_at.astimezone(timezone(timedelta(hours=7))).hour == 9
    assert int2.start_at.astimezone(timezone(timedelta(hours=7))).hour == 10
    assert int2.end_at.astimezone(timezone(timedelta(hours=7))).hour == 12


def test_own_event_protection(sample_valid_availability):
    """Ca kiểm chứng K20: Loại bỏ own-event của session đang dời nhưng giữ sự kiện ngoại vi."""
    w_start = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
    w_end = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)

    # Sự kiện 1: own-event của session-1
    # Sự kiện 2: sự kiện bên ngoài trùng giờ 09:00 - 10:00
    b1_start = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    b1_end = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    b2_start = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
    b2_end = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

    cal_ctx = CalendarContext(
        context_id="ctx_1",
        revision=1,
        availability_ref=AvailabilityRef(availability_id="avail_1", revision=1),
        sync_state="synced",
        selected_calendar_ids=["cal_primary"],
        selected_calendar_sources=[],
        query_start_end=TimeInterval(start_at=w_start, end_at=w_end),
        busy_intervals=[
            BusyInterval(
                start_at=b1_start,
                end_at=b1_end,
                source=BusyIntervalSource(kind="confirmed_session", session_id="session-1"),
            ),
            BusyInterval(
                start_at=b2_start,
                end_at=b2_end,
                source=BusyIntervalSource(kind="calendar_event"),
            ),
        ],
        coverage_state="complete",
        fetched_at=datetime.now(UTC),
    )

    free_intervals = compute_effective_free_intervals(
        availability=sample_valid_availability,
        window_start=w_start,
        window_end=w_end,
        calendar_context=cal_ctx,
        rescheduled_session_ids={"session-1"},  # Dời session-1 -> b1 được loại bỏ
    )

    # b1 được loại bỏ nên 09:00-10:00 vẫn là giờ rảnh; b2 vẫn chặn 11:00-12:00
    assert not any(
        inv.start_at <= b2_start and inv.end_at >= b2_end for inv in free_intervals
    )


# ==============================================================================
# 4. Test A07, A09, A10: Xếp lịch, Gap 900s & FeasibilityReport
# ==============================================================================


@pytest.fixture
def sample_pref():
    return UserPreference(
        revision=1,
        effective_revision=1,
        work_interval_seconds=1500,  # 25p
        rest_interval_seconds=300,   # 5p
        buffer_multiplier=1.2,
        reminder_offsets=[900],
        reminder_style="gentle",
    )


def test_schedule_sessions_feasible(sample_pref):
    """Xếp thành công 2 bước khi đủ giờ rảnh và cách nhau tối thiểu 15 phút."""
    t_start = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)

    steps = [
        CandidateStep(
            client_step_key="step-1",
            client_part_key="part-1",
            title="Nghiên cứu tài liệu",
            expected_output="Ghi chú",
            order_index=0,
            estimated_base_seconds=1500,  # 25p * 1.2 = 30p work + 0 rest = 1800s
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
            status="pending",
        ),
        CandidateStep(
            client_step_key="step-2",
            client_part_key="part-1",
            title="Viết phác thảo",
            expected_output="Bản thảo",
            order_index=1,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
            status="pending",
        ),
    ]

    parts = [
        CandidatePart(
            client_part_key="part-1",
            scope_description="Phần 1",
            order_index=0,
            decomposition_state="complete",
            scheduling_state="complete",
        )
    ]

    # Khoảng rảnh cả ngày 08:00 - 18:00 (10 giờ = 36000s)
    effective_intervals = [TimeInterval(start_at=t_start, end_at=t_end)]

    tc = TimeConstraint(
        kind="deadline",
        start_at=t_start,
        deadline_at=t_end,
        input_timezone="UTC",
    )

    sessions, report = schedule_candidate_sessions(
        steps=steps,
        effective_intervals=effective_intervals,
        fixed_sessions=[],
        time_constraint=tc,
        pref=sample_pref,
        planning_parts=parts,
    )

    assert report.status == "feasible"
    assert report.fully_scheduled is True
    assert report.deficit_seconds == 0
    assert len(sessions) == 2

    # Kiểm tra khoảng cách giữa Phiên 1 và Phiên 2 >= 900 giây (15 phút)
    s1_end = sessions[0].scheduled_end
    s2_start = sessions[1].scheduled_start
    gap = (s2_start - s1_end).total_seconds()
    assert gap >= 900


def test_schedule_sessions_deficit(sample_pref):
    """Trường hợp thiếu thời gian -> báo infeasible và tính chính xác deficit_seconds."""
    t_start = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)  # Chỉ có 1 giờ rảnh (3600s)

    steps = [
        CandidateStep(
            client_step_key="step-1",
            client_part_key="part-1",
            title="Viết báo cáo dài",
            expected_output="Báo cáo",
            order_index=0,
            estimated_base_seconds=6000,  # 6000 * 1.2 = 7200s work -> vượt quá 3600s
            remaining_base_seconds=6000,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
            status="pending",
        )
    ]

    parts = [
        CandidatePart(
            client_part_key="part-1",
            scope_description="P1",
            order_index=0,
            decomposition_state="complete",
            scheduling_state="not_scheduled",
        )
    ]
    effective_intervals = [TimeInterval(start_at=t_start, end_at=t_end)]
    tc = TimeConstraint(kind="deadline", start_at=t_start, deadline_at=t_end, input_timezone="UTC")

    sessions, report = schedule_candidate_sessions(
        steps=steps,
        effective_intervals=effective_intervals,
        fixed_sessions=[],
        time_constraint=tc,
        pref=sample_pref,
        planning_parts=parts,
    )

    assert report.status == "infeasible"
    assert report.fully_scheduled is False
    assert report.deficit_seconds > 0
    assert any(b.code == "TOTAL_DEFICIT" for b in report.blocking_reasons)


# ==============================================================================
# 5. Test A17: Tái lập lịch thích ứng UF6
# ==============================================================================


def test_shift_elapsed_sessions():
    now = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)

    # Session 1: Đã trôi qua (bắt đầu 09:00, kết thúc 10:00)
    # Session 2: Thuộc Task khác không nằm trong allowed_task_ids (13:00 - 14:00) -> cố định
    s1 = Session(
        session_id="s1",
        step_id="step_a",
        scheduled_start=datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
        scheduled_end=datetime(2026, 10, 30, 10, 0, tzinfo=UTC),
        duration_seconds=3600,
        work_seconds=3600,
        rest_seconds=0,
        segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
        micro_task_text="Khởi động 2 phút",
        schedule_revision=1,
        status="incomplete",
        start_state="waiting",
        start_decision_due_at=datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
    )
    s2_fixed = Session(
        session_id="s2",
        step_id="step_b",
        scheduled_start=datetime(2026, 10, 30, 13, 0, tzinfo=UTC),
        scheduled_end=datetime(2026, 10, 30, 14, 0, tzinfo=UTC),
        duration_seconds=3600,
        work_seconds=3600,
        rest_seconds=0,
        segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
        micro_task_text="Khởi động 2 phút",
        schedule_revision=1,
        status="scheduled",
        start_state="waiting",
        start_decision_due_at=datetime(2026, 10, 30, 13, 0, tzinfo=UTC),
    )

    free_intervals = [
        TimeInterval(
            start_at=datetime(2026, 10, 30, 11, 30, tzinfo=UTC),
            end_at=datetime(2026, 10, 30, 18, 0, tzinfo=UTC),
        )
    ]

    moved, affected, all_shifted, deficit = shift_elapsed_sessions(
        sessions=[s1, s2_fixed],
        allowed_task_ids=["task_allowed"],
        effective_intervals=free_intervals,
        real_time_anchor=now,
        step_to_task_map={"step_a": "task_allowed", "step_b": "task_fixed"},
    )

    assert all_shifted is True
    assert affected == ["task_allowed"]
    assert len(moved) == 1
    # s1 được dời sang sau 11:30
    assert moved[0]["new_start"] >= datetime(2026, 10, 30, 11, 30, tzinfo=UTC)


# ==============================================================================
# 6. Test A20: Học hệ số đệm thích ứng từ trung vị mẫu sạch
# ==============================================================================


def test_evaluate_buffer_adaptation_median():
    # 5 bước hoàn tất có tỷ số: [1.3, 1.4, 1.3, 1.35, 1.3] -> median = 1.30
    samples = [
        {"step_id": "st1", "status": "completed", "estimated_base_seconds": 3000, "actual_work_seconds": 3900, "scope_modified": False},
        {"step_id": "st2", "status": "completed", "estimated_base_seconds": 2000, "actual_work_seconds": 2800, "scope_modified": False},
        {"step_id": "st3", "status": "completed", "estimated_base_seconds": 3000, "actual_work_seconds": 3900, "scope_modified": False},
        {"step_id": "st4", "status": "completed", "estimated_base_seconds": 4000, "actual_work_seconds": 5400, "scope_modified": False},
        {"step_id": "st5", "status": "completed", "estimated_base_seconds": 3000, "actual_work_seconds": 3900, "scope_modified": False},
        # Bước chưa hoàn thành hoặc đổi scope -> phải bị lọc bỏ
        {"step_id": "st6_incomplete", "status": "incomplete", "estimated_base_seconds": 3000, "actual_work_seconds": 3900},
        {"step_id": "st7_scope_mod", "status": "completed", "estimated_base_seconds": 3000, "actual_work_seconds": 3900, "scope_modified": True},
    ]

    # Buffer hiện tại là 1.05. Độ lệch |1.30 - 1.05| = 0.25 >= 0.20 -> sinh gợi ý
    suggestion = evaluate_buffer_adaptation(
        step_samples=samples,
        current_buffer_multiplier=1.05,
        min_sample_count=5,
        delta_threshold=0.2,
    )

    assert suggestion is not None
    assert suggestion["kind"] == "buffer_multiplier"
    assert suggestion["suggested_value"] == 1.30
    assert suggestion["sample_count"] == 5
    assert suggestion["model_revision"] is None  # Thuần toán học thống kê
