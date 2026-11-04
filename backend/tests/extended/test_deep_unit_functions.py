"""Bộ kiểm thử đơn vị chuyên sâu (Deep Unit Function Tests) với các kịch bản biên,
dữ liệu dị biệt (weird/edge cases), và hành vi bất thường của người dùng ADHD.

Kiểm tra từng hàm tác vụ độc lập:
1. durations: calculate_buffered_work, build_session_segments, fit_session_to_slot
2. availability: _parse_time_str, _merge_intervals, _subtract_intervals, compute_effective_free_intervals
3. scheduling: schedule_candidate_sessions
4. adaptation: evaluate_buffer_adaptation
5. rescheduling: shift_elapsed_sessions
6. ai_service: extract_task_intent (Tier 3), decompose_task (Tier 3), adjust_plan_by_nl, empathy notes
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, time, timedelta, timezone
import pytest

from app.core.config import Settings
from app.schemas.availability import Availability, DateOverride, WeeklyRule
from app.schemas.calendar import (
    BusyInterval,
    BusyIntervalSource,
    CalendarContext,
    ManualBusyInterval,
)
from app.schemas.common import AvailabilityRef, SessionSegment, TimeConstraint, TimeInterval
from app.schemas.planning import CandidatePart, CandidateStep
from app.schemas.settings import UserPreference
from app.schemas.task import Session, StepPromptItem
from app.services.ai_service import AIService
from app.services.planning.adaptation import evaluate_buffer_adaptation
from app.services.planning.availability import (
    _merge_intervals,
    _parse_time_str,
    _subtract_intervals,
    compute_effective_free_intervals,
)
from app.services.planning.durations import (
    build_session_segments,
    calculate_buffered_work,
    fit_session_to_slot,
)
from app.services.planning.rescheduling import shift_elapsed_sessions
from app.services.planning.scheduling import schedule_candidate_sessions


# ==============================================================================
# PHẦN 1: DURATIONS - HỆ SỐ ĐỆM & CẤU TRÚC PHIÊN LÀM/NGHỈ
# ==============================================================================


class TestDurationsDeepScenarios:
    """Kiểm tra chuyên sâu các hàm tính toán thời lượng với dữ liệu lạ."""

    def test_calculate_buffered_work_edge_multipliers(self):
        """Hệ số đệm cận biên: k=1.0, k rất nhỏ cận 1, k=1.99999, k cực lớn."""
        # k = 1.0 (không đổi)
        assert calculate_buffered_work(3600, 1.0) == 3600
        # Làm tròn lên trần (ceil): 100 * 1.0001 = 100.01 -> 101
        assert calculate_buffered_work(100, 1.0001) == 101
        # Số giây lẻ nguyên tố: 17 giây * 1.35 = 22.95 -> 23 giây
        assert calculate_buffered_work(17, 1.35) == 23
        # Số giây cực nhỏ: 1 giây * 1.05 = 1.05 -> 2 giây
        assert calculate_buffered_work(1, 1.05) == 2
        # Số giây rất lớn: 86400 giây (1 ngày) * 2.0 = 172800 giây
        assert calculate_buffered_work(86400, 2.0) == 172800

    def test_calculate_buffered_work_invalid_inputs(self):
        """Kiểm tra chặn giá trị âm, zero."""
        with pytest.raises(ValueError, match="số nguyên dương"):
            calculate_buffered_work(0, 1.2)
        with pytest.raises(ValueError, match="số nguyên dương"):
            calculate_buffered_work(-100, 1.2)
        with pytest.raises(ValueError, match="số dương"):
            calculate_buffered_work(100, 0.0)
        with pytest.raises(ValueError, match="số dương"):
            calculate_buffered_work(100, -0.5)

    def test_build_session_segments_single_short_chunk(self):
        """Thời gian ngắn hơn 1 quãng tập trung -> chỉ 1 segment work, không có rest."""
        segments, work, rest, duration = build_session_segments(
            buffered_work_seconds=600,  # 10 phút
            work_interval_seconds=1500,  # max 25 phút
            rest_interval_seconds=300,  # nghỉ 5 phút
        )
        assert len(segments) == 1
        assert segments[0].kind == "work"
        assert segments[0].duration_seconds == 600
        assert work == 600
        assert rest == 0
        assert duration == 600

    def test_build_session_segments_exact_multiple_no_trailing_rest(self):
        """Thời gian đúng bằng bội số chuẩn -> không bao giờ có segment rest dư ở cuối."""
        # 3000s = 1500s + 1500s -> 2 quãng work xen giữa 1 quãng rest 300s
        segments, work, rest, duration = build_session_segments(
            buffered_work_seconds=3000,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
        )
        assert len(segments) == 3
        assert [s.kind for s in segments] == ["work", "rest", "work"]
        assert segments[-1].kind == "work"
        assert work == 3000
        assert rest == 300
        assert duration == 3300

    def test_build_session_segments_exceed_by_one_second(self):
        """Vượt quá work_interval đúng 1 giây -> sinh thêm 1 rest và 1 work 1 giây."""
        segments, work, rest, duration = build_session_segments(
            buffered_work_seconds=1501,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
        )
        assert len(segments) == 3
        assert segments[0].duration_seconds == 1500
        assert segments[1].duration_seconds == 300
        assert segments[2].duration_seconds == 1
        assert work == 1501
        assert rest == 300
        assert duration == 1801

    def test_fit_session_to_slot_boundary_scenarios(self):
        """Kiểm tra hàm fit_session_to_slot khi khe thời gian vừa khít hoặc quá nhỏ."""
        # Khe rảnh nhỏ hơn 1 work segment: slot=1000s, work_interval=1500s -> nhận 1000s work, 0 rest
        segs, w, r, dur = fit_session_to_slot(
            remaining_work_seconds=3000,
            slot_seconds=1000,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
        )
        assert w == 1000
        assert r == 0
        assert dur == 1000
        assert len(segs) == 1

        # Khe rảnh đúng 1800s: đủ cho 1500s work + 0 rest (nếu lấy 1501s thì cần 1501 + 300 = 1801s > 1800s)
        segs, w, r, dur = fit_session_to_slot(
            remaining_work_seconds=3000,
            slot_seconds=1800,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
        )
        assert w == 1500
        assert r == 0
        assert dur == 1500

        # Slot rỗng (0 giây)
        segs, w, r, dur = fit_session_to_slot(
            remaining_work_seconds=1000,
            slot_seconds=0,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
        )
        assert w == 0
        assert dur == 0
        assert segs == []


# ==============================================================================
# PHẦN 2: AVAILABILITY - TÍNH KHOẢNG RẢNH VÀ TRỪ LỊCH BẬN
# ==============================================================================


class TestAvailabilityDeepScenarios:
    """Kiểm tra giải thuật sweep-line và trừ khoảng thời gian (Interval Subtraction)."""

    def test_merge_intervals_disjoint_and_touching(self):
        """Khoảng chạm nhau tại biên [10:00, 11:00] và [11:00, 12:00] phải gộp thành 1."""
        t10 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        t11 = datetime(2026, 11, 2, 11, 0, tzinfo=UTC)
        t12 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
        t14 = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
        t15 = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)

        merged = _merge_intervals([(t10, t11), (t11, t12), (t14, t15)])
        assert len(merged) == 2
        assert merged[0] == (t10, t12)
        assert merged[1] == (t14, t15)

    def test_subtract_intervals_swiss_cheese(self):
        """Một khoảng rảnh lớn bị nhiều lịch bận nhỏ đục lỗ liên tiếp."""
        base = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        free = [(base, base + timedelta(hours=10))]  # 08:00 -> 18:00 (10 tiếng)

        busy = [
            (base + timedelta(hours=1), base + timedelta(hours=2)),
            (base + timedelta(hours=3, minutes=30), base + timedelta(hours=4, minutes=30)),
            (base + timedelta(hours=6), base + timedelta(hours=7)),
        ]

        result = _subtract_intervals(free, busy)
        assert len(result) == 4
        assert result[0] == (base, base + timedelta(hours=1))
        assert result[1] == (base + timedelta(hours=2), base + timedelta(hours=3, minutes=30))
        assert result[2] == (base + timedelta(hours=4, minutes=30), base + timedelta(hours=6))
        assert result[3] == (base + timedelta(hours=7), base + timedelta(hours=10))

    def test_subtract_intervals_busy_covers_everything(self):
        """Lịch bận bao trùm toàn bộ hoặc vượt ra ngoài khoảng rảnh."""
        base = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        free = [(base + timedelta(hours=2), base + timedelta(hours=4))]
        busy = [(base + timedelta(hours=1), base + timedelta(hours=5))]

        result = _subtract_intervals(free, busy)
        assert result == [], "Lịch bận bao trùm thì không còn khoảng rảnh nào."

    def test_compute_effective_free_intervals_own_event_protection(self):
        """Bảo vệ own-event: Phiên trùng với Session đang dời được gỡ bỏ, nhưng sự kiện ngoại vi khác trùng giờ vẫn bị coi là bận."""
        vn_tz = "Asia/Ho_Chi_Minh"
        now = datetime(2026, 11, 2, 8, 0, tzinfo=timezone(timedelta(hours=7)))
        window_start = now
        window_end = now + timedelta(days=1)

        avail = Availability(
            availability_id="av1",
            revision=1,
            source="user_confirmed",
            timezone=vn_tz,
            confirmed_at=now,
            weekly_rules=[WeeklyRule(weekday=1, start_local="08:00", end_local="18:00")],  # Thứ 2
            date_overrides=[],
            task_limits=[],
            effective_intervals=None,
            calendar_context=None,
        )

        t_start = datetime(2026, 11, 2, 10, 0, tzinfo=timezone(timedelta(hours=7)))
        t_end = datetime(2026, 11, 2, 11, 0, tzinfo=timezone(timedelta(hours=7)))

        cal_context = CalendarContext(
            context_id="ctx_1",
            revision=1,
            availability_ref=AvailabilityRef(availability_id="av1", revision=1),
            sync_state="synced",
            selected_calendar_ids=["cal_primary"],
            selected_calendar_sources=[],
            query_start_end=TimeInterval(start_at=window_start, end_at=window_end),
            coverage_state="full",
            fetched_at=now,
            busy_intervals=[
                BusyInterval(
                    source=BusyIntervalSource(kind="confirmed_session", session_id="sess_123"),
                    start_at=t_start,
                    end_at=t_end,
                ),
                BusyInterval(
                    source=BusyIntervalSource(kind="calendar_event", google_event_id="external_meeting_999"),
                    start_at=t_start,
                    end_at=t_end,
                ),
            ],
        )

        free_intervals = compute_effective_free_intervals(
            availability=avail,
            window_start=window_start,
            window_end=window_end,
            calendar_context=cal_context,
            rescheduled_session_ids={"sess_123"},
        )

        for interval in free_intervals:
            assert not (interval.start_at >= t_start and interval.end_at <= t_end)


# ==============================================================================
# PHẦN 3: SCHEDULING - XẾP LỊCH CANDIDATE & RÀNG BUỘC KHE GAP 15 PHÚT
# ==============================================================================


class TestSchedulingDeepScenarios:
    """Kiểm tra thuật toán First-fit, kiểm soát gap 900s và multi-part ordering."""

    def test_schedule_exact_fit_vs_short_one_second(self):
        """Khối lượng vừa khít slot -> Feasible. Khối lượng hụt 1 giây -> Infeasible."""
        t_start = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        free_slot = TimeInterval(start_at=t_start, end_at=t_start + timedelta(seconds=3600))
        tc = TimeConstraint(
            kind="deadline",
            start_at=t_start,
            deadline_at=t_start + timedelta(days=1),
            input_timezone="UTC",
        )
        pref = UserPreference(
            revision=1,
            effective_revision=1,
            work_interval_seconds=3600,
            rest_interval_seconds=0,
            buffer_multiplier=1.0,
            reminder_offsets=[15],
            reminder_style="balanced",
        )
        parts = [
            CandidatePart(
                client_part_key="p1",
                scope_description="P1",
                order_index=0,
                decomposition_state="complete",
                scheduling_state="not_scheduled",
            )
        ]

        step_exact = CandidateStep(
            client_step_key="s1",
            client_part_key="p1",
            title="Vừa khít",
            expected_output="Done",
            order_index=0,
            estimated_base_seconds=3600,
            remaining_base_seconds=3600,
            buffer_multiplier=1.0,
            estimate_source="user_confirmed",
            status="pending",
        )
        sessions, report = schedule_candidate_sessions(
            steps=[step_exact],
            effective_intervals=[free_slot],
            fixed_sessions=[],
            time_constraint=tc,
            pref=pref,
            planning_parts=parts,
        )
        assert report.status == "feasible"
        assert report.fully_scheduled is True
        assert len(sessions) == 1

        step_exceed = CandidateStep(
            client_step_key="s2",
            client_part_key="p1",
            title="Thừa 1s",
            expected_output="Done",
            order_index=0,
            estimated_base_seconds=3601,
            remaining_base_seconds=3601,
            buffer_multiplier=1.0,
            estimate_source="user_confirmed",
            status="pending",
        )
        sessions_exc, report_exc = schedule_candidate_sessions(
            steps=[step_exceed],
            effective_intervals=[free_slot],
            fixed_sessions=[],
            time_constraint=tc,
            pref=pref,
            planning_parts=parts,
        )
        assert report_exc.status == "infeasible"
        assert report_exc.fully_scheduled is False
        assert report_exc.deficit_seconds >= 1

    def test_schedule_enforces_minimum_gap_900s(self):
        """Hai phiên liên tiếp bắt buộc cách nhau tối thiểu 900 giây (15 phút)."""
        t_start = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        free_slot = TimeInterval(start_at=t_start, end_at=t_start + timedelta(hours=10))
        tc = TimeConstraint(
            kind="deadline",
            start_at=t_start,
            deadline_at=t_start + timedelta(days=1),
            input_timezone="UTC",
        )
        pref = UserPreference(
            revision=1,
            effective_revision=1,
            work_interval_seconds=1800,
            rest_interval_seconds=0,
            buffer_multiplier=1.0,
            reminder_offsets=[15],
            reminder_style="balanced",
        )
        parts = [
            CandidatePart(
                client_part_key="p1",
                scope_description="P1",
                order_index=0,
                decomposition_state="complete",
                scheduling_state="not_scheduled",
            )
        ]
        step1 = CandidateStep(
            client_step_key="s1",
            client_part_key="p1",
            title="B1",
            expected_output="Done",
            order_index=0,
            estimated_base_seconds=1800,
            remaining_base_seconds=1800,
            buffer_multiplier=1.0,
            estimate_source="user_confirmed",
            status="pending",
        )
        step2 = CandidateStep(
            client_step_key="s2",
            client_part_key="p1",
            title="B2",
            expected_output="Done",
            order_index=1,
            estimated_base_seconds=1800,
            remaining_base_seconds=1800,
            buffer_multiplier=1.0,
            estimate_source="user_confirmed",
            status="pending",
        )

        sessions, report = schedule_candidate_sessions(
            steps=[step1, step2],
            effective_intervals=[free_slot],
            fixed_sessions=[],
            time_constraint=tc,
            pref=pref,
            planning_parts=parts,
            min_gap_seconds=900,
        )
        assert len(sessions) == 2
        gap = (sessions[1].scheduled_start - sessions[0].scheduled_end).total_seconds()
        assert gap >= 900, f"Khoảng cách giữa 2 session phải >= 900s, thực tế={gap}s"


# ==============================================================================
# PHẦN 4: ADAPTATION - HỌC HỆ SỐ ĐỆM THÍCH ỨNG & KHÁNG NHIỄU MEDIAN
# ==============================================================================


class TestAdaptationDeepScenarios:
    """Kiểm tra độ bền của thuật toán trung vị trước các ngoại lệ ADHD cực đoan."""

    def test_evaluate_buffer_adaptation_extreme_adhd_outliers(self):
        """Người dùng ADHD có ngày siêu tập trung (hyperfocus 0.1) và ngày phân tâm nặng (5.0).
        Trung vị (Median) loại bỏ các ngày đột biến này."""
        samples = [
            {"step_id": "s_hyp", "status": "completed", "estimated_base_seconds": 3000, "actual_work_seconds": 300, "scope_modified": False},
            {"step_id": "s_norm1", "status": "completed", "estimated_base_seconds": 2000, "actual_work_seconds": 2700, "scope_modified": False},
            {"step_id": "s_norm2", "status": "completed", "estimated_base_seconds": 2000, "actual_work_seconds": 2700, "scope_modified": False},
            {"step_id": "s_norm3", "status": "completed", "estimated_base_seconds": 2000, "actual_work_seconds": 2700, "scope_modified": False},
            {"step_id": "s_dist", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 5000, "scope_modified": False},
        ]

        suggestion = evaluate_buffer_adaptation(
            step_samples=samples,
            current_buffer_multiplier=1.05,
            min_sample_count=5,
            delta_threshold=0.2,
        )
        assert suggestion is not None
        assert suggestion["suggested_value"] == 1.35
        assert suggestion["sample_count"] == 5

    def test_evaluate_buffer_adaptation_sample_filtering_and_clamping(self):
        """Bỏ qua bước chưa xong hoặc bị sửa scope, và kẹp policy [1.0, 2.0]."""
        samples = [
            {"step_id": "s1", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 3500, "scope_modified": False},
            {"step_id": "s2", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 3500, "scope_modified": False},
            {"step_id": "s3", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 3500, "scope_modified": False},
            {"step_id": "s4", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 3500, "scope_modified": False},
            {"step_id": "s5", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 3500, "scope_modified": False},
            {"step_id": "s_incomp", "status": "in_progress", "estimated_base_seconds": 1000, "actual_work_seconds": 3500},
            {"step_id": "s_mod", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 3500, "scope_modified": True},
        ]

        suggestion = evaluate_buffer_adaptation(
            step_samples=samples,
            current_buffer_multiplier=1.0,
            min_sample_count=5,
            policy_min=1.0,
            policy_max=2.0,
            delta_threshold=0.2,
        )
        assert suggestion is not None
        assert suggestion["suggested_value"] == 2.0, "Phải bị kẹp trần policy_max=2.0"
        assert suggestion["sample_count"] == 5


# ==============================================================================
# PHẦN 5: RESCHEDULING - TÁI LẬP LỊCH THÍCH ỨNG (UF6)
# ==============================================================================


class TestReschedulingDeepScenarios:
    """Kiểm tra dịch chuyển các phiên trôi qua khi mở lại app."""

    def test_shift_elapsed_sessions_isolation(self):
        """Chỉ dời các phiên thuộc allowed_task_ids, giữ nguyên các task cố định khác."""
        anchor = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

        s1 = Session(
            session_id="s1",
            step_id="step_1",
            scheduled_start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
            scheduled_end=datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
            duration_seconds=3600,
            work_seconds=3600,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            micro_task_text="Bắt đầu 2p",
            schedule_revision=1,
            status="incomplete",
            start_state="waiting",
            start_decision_due_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
        )
        s2 = Session(
            session_id="s2",
            step_id="step_2",
            scheduled_start=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
            scheduled_end=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
            duration_seconds=3600,
            work_seconds=3600,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            micro_task_text="Bắt đầu 2p",
            schedule_revision=1,
            status="scheduled",
            start_state="waiting",
            start_decision_due_at=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
        )

        future_free = [
            TimeInterval(
                start_at=datetime(2026, 11, 2, 12, 30, tzinfo=UTC),
                end_at=datetime(2026, 11, 2, 18, 0, tzinfo=UTC),
            )
        ]

        moved, affected, all_shifted, deficit = shift_elapsed_sessions(
            sessions=[s1, s2],
            allowed_task_ids=["task_allow"],
            effective_intervals=future_free,
            real_time_anchor=anchor,
            step_to_task_map={"step_1": "task_allow", "step_2": "task_fixed"},
        )

        assert all_shifted is True
        assert affected == ["task_allow"]
        assert len(moved) == 1
        assert moved[0]["session_id"] == "s1"
        assert moved[0]["new_start"] >= datetime(2026, 11, 2, 12, 30, tzinfo=UTC)


# ==============================================================================
# PHẦN 6: AI SERVICE - TIER 3 FALLBACK & ĐẶC TẢ ADHD
# ==============================================================================


class TestAIServiceDeepScenarios:
    """Kiểm tra cơ chế Tier 3 Rule-based Fallback & Guardrails khi offline hoặc lỗi API."""

    @pytest.fixture
    def svc(self) -> AIService:
        mock_settings = Settings(
            GEMINI_API_KEY="",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
        )
        return AIService(app_settings=mock_settings)

    @pytest.mark.asyncio
    async def test_extract_intent_tier3_rambling_adhd_input(self, svc: AIService):
        """Input lan man đặc trưng ADHD: nói nhiều nhưng thiếu giờ rảnh và deadline cụ thể.
        Tier 3 fallback trích xuất nội dung và hỏi gom đúng các thông tin còn thiếu."""
        rambling_text = (
            "Ê trợ lý ơi chiều mai hoặc tối mai tao muốn học bài toán nhưng mà từ từ đã "
            "chắc tầm 8h tối rảnh khoảng 1 tiếng deadline chắc chủ nhật tuần này đi."
        )

        result = await svc.extract_task_intent(
            user_input=rambling_text,
        )

        assert result.generation_tier == "tier_3_rule_based"
        assert result.is_complete is False
        assert len(result.clarification_questions) >= 1
        assert "học bài toán" in result.summary or rambling_text[:40] in result.summary

    @pytest.mark.asyncio
    async def test_decompose_task_tier3_micro_task_generation(self, svc: AIService):
        """Tier 3 Fallback phân rã công việc: bắt buộc sinh đủ 3 bước chuẩn kèm Micro-task khởi động."""
        decomp = await svc.decompose_task(
            task_title="Ôn tập cấu trúc dữ liệu và giải thuật",
            user_focus_duration=25,
            scope="3 chương đầu",
        )

        assert decomp.generation_tier == "tier_3_rule_based"
        assert len(decomp.steps) == 3
        for step in decomp.steps:
            assert step.micro_task_prompt is not None
            assert len(step.micro_task_prompt.strip()) >= 5
            assert step.estimated_minutes == 25

    @pytest.mark.asyncio
    async def test_adjust_candidate_plan_tier3_safe_preservation(self, svc: AIService):
        """Tier 3 Fallback điều chỉnh: bảo toàn nguyên vẹn danh sách bước của người dùng."""
        original_steps = [
            StepPromptItem(order_index=1, title="Bước 1", expected_output="Out 1", estimated_minutes=20, micro_task_prompt="Bắt đầu 2 phút xem lại dàn ý"),
            StepPromptItem(order_index=2, title="Bước 2", expected_output="Out 2", estimated_minutes=30, micro_task_prompt="Bắt đầu 2 phút mở tài liệu ra"),
        ]

        adj = await svc.adjust_plan_by_nl(
            user_instruction="gộp bước 1 và 2 lại",
            current_steps=original_steps,
        )

        assert adj.generation_tier == "tier_3_rule_based"
        assert len(adj.updated_steps) == 2
        assert adj.requires_clarification is True
        assert adj.assistant_note is not None

    @pytest.mark.asyncio
    async def test_generate_reschedule_empathy_guilt_free(self, svc: AIService):
        """UF6: Sinh lời an ủi/giải thích dời lịch không gây tội lỗi (Guilt-free)."""
        empathy = await svc.generate_reschedule_empathy(
            trigger_reason="missed_session",
            task_title="Báo cáo tiến độ",
        )

        assert empathy.generation_tier == "tier_3_rule_based"
        assert empathy.empathetic_note is not None
        assert empathy.encouragement_tip is not None
        lower_note = empathy.empathetic_note.lower()
        assert "lười biếng" not in lower_note
        assert "thất bại" not in lower_note
