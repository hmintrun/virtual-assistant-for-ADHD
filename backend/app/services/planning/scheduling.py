"""Module scheduling: Hiện thực hóa các bài toán [A07], [A09], [A10].

Đảm nhận:
- [A10]: Thiết lập cửa sổ Rolling Horizon (14 ngày), gói CandidateVerificationScope và theo dõi phần chưa chia/chưa xếp.
- [A07]: Sắp xếp thứ tự ưu tiên các bước theo StepOrder (PlanningPart.order_index, Step.order_index).
- [A09]: Thuật toán xếp lịch First-Fit Interval Allocation, đảm bảo khoảng cách tối thiểu 15 phút (900s) giữa các phiên,
         kiểm tra deadline, tính toán số phút thiếu hụt delta T_deficit và sinh FeasibilityReport (O10).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.schemas.common import BlockingReason, TimeConstraint, TimeInterval
from app.schemas.planning import (
    CandidatePart,
    CandidateSession,
    CandidateStep,
    CandidateVerificationScope,
    FeasibilityReport,
)
from app.schemas.settings import UserPreference
from app.schemas.task import Session
from app.services.planning.durations import prepare_step_durations


def schedule_candidate_sessions(
    steps: list[CandidateStep],
    effective_intervals: list[TimeInterval],
    fixed_sessions: list[Session],
    time_constraint: TimeConstraint,
    pref: UserPreference,
    planning_parts: list[CandidatePart],
    horizon_days: int = 14,
    min_gap_seconds: int = 900,
) -> tuple[list[CandidateSession], FeasibilityReport]:
    """Xếp các phiên CandidateSession vào các ô giờ rảnh khả dụng và kiểm tra tính khả thi (A07 + A09 + A10).

    Args:
        steps: Danh sách các bước CandidateStep.
        effective_intervals: Danh sách khoảng rảnh đã trừ lịch bận (từ A06).
        fixed_sessions: Danh sách các phiên đã chốt cố định của task khác.
        time_constraint: Ràng buộc thời gian (start_at, deadline_at / finish_by_at).
        pref: Cấu hình UserPreference của người dùng.
        planning_parts: Danh sách các phần việc CandidatePart.
        horizon_days: Số ngày của cửa sổ Rolling Horizon (mặc định 14).
        min_gap_seconds: Khoảng cách tối thiểu giữa 2 phiên liên tiếp (mặc định 900s).

    Returns:
        tuple (sessions: list[CandidateSession], report: FeasibilityReport)
    """
    task_end = (
        time_constraint.deadline_at
        if time_constraint.kind == "deadline"
        else time_constraint.finish_by_at
    )
    if task_end is None:
        raise ValueError("time_constraint thiếu mốc thời gian kết thúc.")

    t_start_utc = time_constraint.start_at.astimezone(UTC)
    t_end_utc = task_end.astimezone(UTC)

    horizon_end = min(t_start_utc + timedelta(days=horizon_days), t_end_utc)
    planning_horizon = TimeInterval(start_at=t_start_utc, end_at=horizon_end)

    client_part_keys = [
        p.client_part_key for p in planning_parts if p.client_part_key
    ]
    if not client_part_keys:
        client_part_keys = ["part_default"]

    verification_scope = CandidateVerificationScope(
        scope_type="candidate_parts",
        client_part_keys=client_part_keys,
        planning_horizon=planning_horizon,
    )

    unestimated_parts = [
        p.client_part_key
        for p in planning_parts
        if p.rough_estimate_seconds is None and p.decomposition_state != "complete"
    ]
    unplanned_parts = [
        p.client_part_key
        for p in planning_parts
        if p.decomposition_state != "complete" or p.scheduling_state != "complete"
    ]

    part_order_map = {p.client_part_key: p.order_index for p in planning_parts}
    sorted_steps = sorted(
        steps,
        key=lambda s: (
            part_order_map.get(s.client_part_key or "", 0),
            s.order_index,
        ),
    )

    prepared_sessions: list[dict] = []
    total_required_seconds = 0

    for step_idx, step in enumerate(sorted_steps):
        buffered_work, segments, total_work, total_rest, duration = (
            prepare_step_durations(step, pref)
        )
        micro_text = (
            "Mở tài liệu và đọc lướt nội dung trước phiên"
            if not step.title
            else f"Chuẩn bị tài liệu cho bước: {step.title}"
        )
        prepared_sessions.append(
            {
                "client_session_key": f"session-{step.client_step_key}-{step_idx}",
                "client_step_key": step.client_step_key,
                "micro_task_text": micro_text,
                "work_seconds": total_work,
                "rest_seconds": total_rest,
                "segments": segments,
                "duration_seconds": duration,
            }
        )
        total_required_seconds += duration

    free_slots: list[list[datetime]] = []
    total_available_seconds = 0

    for interval in effective_intervals:
        s_utc = interval.start_at.astimezone(UTC)
        e_utc = interval.end_at.astimezone(UTC)

        clamped_s = max(s_utc, t_start_utc)
        clamped_e = min(e_utc, horizon_end)

        if clamped_e > clamped_s:
            free_slots.append([clamped_s, clamped_e])
            total_available_seconds += int((clamped_e - clamped_s).total_seconds())

    fixed_ranges = [
        (
            fs.scheduled_start.astimezone(UTC),
            fs.scheduled_end.astimezone(UTC),
        )
        for fs in fixed_sessions
        if fs.scheduled_end.astimezone(UTC) > t_start_utc
    ]
    fixed_ranges.sort(key=lambda x: x[0])

    scheduled_sessions: list[CandidateSession] = []
    last_session_end: datetime | None = None
    unscheduled_count = 0
    unscheduled_seconds = 0
    blocking_reasons: list[BlockingReason] = []

    for session_data in prepared_sessions:
        duration_sec = session_data["duration_seconds"]
        placed = False

        for slot_idx, slot in enumerate(free_slots):
            slot_start, slot_end = slot[0], slot[1]

            earliest_start = slot_start
            if last_session_end is not None:
                earliest_start = max(
                    earliest_start,
                    last_session_end + timedelta(seconds=min_gap_seconds),
                )

            for f_start, f_end in fixed_ranges:
                if earliest_start < f_end and earliest_start + timedelta(seconds=duration_sec) > f_start:
                    earliest_start = max(
                        earliest_start,
                        f_end + timedelta(seconds=min_gap_seconds),
                    )

            candidate_end = earliest_start + timedelta(seconds=duration_sec)

            if candidate_end <= slot_end and candidate_end <= t_end_utc:
                sched_start = earliest_start
                sched_end = candidate_end
                due_at = sched_start + timedelta(seconds=min_gap_seconds)

                cand_session = CandidateSession(
                    client_session_key=session_data["client_session_key"],
                    client_step_key=session_data["client_step_key"],
                    scheduled_start=sched_start,
                    scheduled_end=sched_end,
                    micro_task_text=session_data["micro_task_text"],
                    micro_task_seconds=None,
                    work_seconds=session_data["work_seconds"],
                    rest_seconds=session_data["rest_seconds"],
                    segments=session_data["segments"],
                    duration_seconds=duration_sec,
                    start_state="waiting",
                    start_decision_due_at=due_at,
                )
                scheduled_sessions.append(cand_session)
                last_session_end = sched_end
                placed = True

                if sched_end < slot_end:
                    free_slots[slot_idx][0] = sched_end
                else:
                    free_slots.pop(slot_idx)
                break

        if not placed:
            unscheduled_count += 1
            unscheduled_seconds += duration_sec

    fully_scheduled = (unscheduled_count == 0)
    deficit_seconds = max(0, total_required_seconds - total_available_seconds)

    if not fully_scheduled:
        if deficit_seconds > 0:
            status = "infeasible"
            blocking_reasons.append(
                BlockingReason(
                    code="TOTAL_DEFICIT",
                    message=(
                        f"Tổng quỹ giờ rảnh trong 14 ngày thiếu {deficit_seconds // 60} phút "
                        f"so với nhu cầu {total_required_seconds // 60} phút."
                    ),
                )
            )
        else:
            status = "search_inconclusive"
            blocking_reasons.append(
                BlockingReason(
                    code="FRAGMENTED_FREE_SLOTS",
                    message=(
                        "Quỹ giờ rảnh bị phân mảnh, không có ô rảnh liên tục nào đủ thời lượng phiên "
                        f"hoặc vướng khoảng cách nghỉ {min_gap_seconds // 60} phút giữa các phiên."
                    ),
                )
            )
    else:
        status = "feasible"

    report = FeasibilityReport(
        status=status,
        required_seconds=total_required_seconds,
        available_seconds=total_available_seconds,
        unscheduled_seconds=unscheduled_seconds if not fully_scheduled else 0,
        verification_scope=verification_scope,
        fully_scheduled=fully_scheduled,
        blocking_reasons=blocking_reasons,
        deficit_seconds=deficit_seconds if not fully_scheduled else 0,
        unestimated_part_ids=unestimated_parts,
        unplanned_part_ids=unplanned_parts,
        extension_candidate_at=None,
    )

    return scheduled_sessions, report
