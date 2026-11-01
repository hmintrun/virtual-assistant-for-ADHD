"""A07/A09/A10: xếp tuần tự theo khe rảnh, bảo toàn gap và phạm vi kiểm.

Greedy theo Part/Step; một Step có thể gồm nhiều Sessions. Không di chuyển
phiên cố định, không chứng minh vô nghiệm chỉ từ việc greedy chưa tìm đủ lịch.
"""

from __future__ import annotations

from datetime import UTC, timedelta

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
from app.services.planning.availability import _merge_intervals, _subtract_intervals
from app.services.planning.durations import calculate_buffered_work, fit_session_to_slot


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
    """Trả lịch cụ thể và report cho các phần có Steps được đưa vào lần tính.

    available_seconds là quỹ sau khi trừ fixed Sessions kèm gap hai phía.
    Khi chưa xếp đủ, unscheduled_seconds là work còn lại, deficit_seconds
    là cận dưới thiếu work; required_seconds=null vì nghỉ nội bộ phụ thuộc
    cách chia các Sessions chưa biết. Khi khả thi, required là duration thực.
    """
    task_end = (
        time_constraint.deadline_at
        if time_constraint.kind == "deadline"
        else time_constraint.finish_by_at
    )
    if time_constraint.start_at is None or task_end is None:
        raise ValueError("Cần đủ mốc bắt đầu và kết thúc.")
    if time_constraint.start_at.utcoffset() is None or task_end.utcoffset() is None:
        raise ValueError("Mốc thời gian phải có offset.")
    if horizon_days <= 0 or min_gap_seconds < 900:
        raise ValueError("Horizon phải dương và gap không được dưới 900 giây.")
    if pref.work_interval_seconds <= 0 or pref.rest_interval_seconds < 0:
        raise ValueError("Cấu hình work/rest không hợp lệ.")
    start = time_constraint.start_at.astimezone(UTC)
    end = min(start + timedelta(days=horizon_days), task_end.astimezone(UTC))
    horizon = TimeInterval(start_at=start, end_at=end)
    gap = timedelta(seconds=min_gap_seconds)
    free = []
    for interval in effective_intervals:
        if interval.start_at.utcoffset() is None or interval.end_at.utcoffset() is None:
            raise ValueError("Khoảng rảnh phải có offset.")
        left, right = (
            max(start, interval.start_at.astimezone(UTC)),
            min(end, interval.end_at.astimezone(UTC)),
        )
        if left < right:
            free.append((left, right))
    # Giữ cả fixed nằm sát bên ngoài horizon: chúng vẫn có thể ảnh hưởng gap.
    protected = [
        (s.scheduled_start.astimezone(UTC) - gap, s.scheduled_end.astimezone(UTC) + gap)
        for s in fixed_sessions
        if s.status != "cancelled"
    ]
    free = _subtract_intervals(_merge_intervals(free), _merge_intervals(protected))
    available = int(sum((b - a).total_seconds() for a, b in free))
    part_order = {p.client_part_key: p.order_index for p in planning_parts}
    if len({s.client_step_key for s in steps}) != len(steps):
        raise ValueError("Client Step key bị trùng.")
    ordered = sorted(steps, key=lambda s: (part_order.get(s.client_part_key, 0), s.order_index))
    remaining = {
        s.client_step_key: calculate_buffered_work(s.remaining_base_seconds, s.buffer_multiplier)
        if s.remaining_base_seconds
        else 0
        for s in ordered
    }
    total_work = sum(remaining.values())
    scheduled: list[CandidateSession] = []
    last_end = None
    for step in ordered:
        key = step.client_step_key
        session_index = 0
        for slot_start, slot_end in free:
            if remaining[key] == 0:
                break
            left = max(slot_start, last_end + gap) if last_end is not None else slot_start
            capacity = int((slot_end - left).total_seconds())
            if capacity <= 0:
                continue
            segments, work, rest, duration = fit_session_to_slot(
                remaining[key],
                capacity,
                pref.work_interval_seconds,
                pref.rest_interval_seconds,
            )
            if work == 0:
                continue
            right = left + timedelta(seconds=duration)
            scheduled.append(
                CandidateSession(
                    client_session_key=f"session-{key}-{session_index}",
                    client_step_key=key,
                    scheduled_start=left,
                    scheduled_end=right,
                    micro_task_text=f"Chuẩn bị tài liệu cho bước: {step.title}",
                    micro_task_seconds=None,
                    work_seconds=work,
                    rest_seconds=rest,
                    segments=segments,
                    duration_seconds=duration,
                    start_state="waiting",
                    start_decision_due_at=left + timedelta(seconds=900),
                )
            )
            session_index += 1
            remaining[key] -= work
            last_end = right
        # Steps hiện tuần tự: không xếp bước sau nếu bước trước chưa có lịch đủ.
        if remaining[key] > 0:
            break
    unscheduled = sum(remaining.values())
    fully_scheduled = bool(steps) and unscheduled == 0
    deficit = max(0, total_work - available)
    status = "feasible" if fully_scheduled else "infeasible" if deficit else "search_inconclusive"
    selected_parts = list(dict.fromkeys(s.client_part_key for s in ordered))
    scope_keys = selected_parts or [p.client_part_key for p in planning_parts] or ["part_default"]
    unplanned = []
    for part in planning_parts:
        children = [s for s in ordered if s.client_part_key == part.client_part_key]
        if (
            part.decomposition_state != "complete"
            or not children
            or any(remaining[s.client_step_key] for s in children)
        ):
            unplanned.append(part.client_part_key)
    reasons = (
        []
        if fully_scheduled
        else [
            BlockingReason(
                code="TOTAL_DEFICIT" if deficit else "FRAGMENTED_FREE_SLOTS",
                message=f"Thiếu ít nhất {deficit} giây làm chính trong phạm vi lập lịch."
                if deficit
                else "Chưa xếp đủ Steps theo thứ tự, giờ rảnh, nghỉ nội bộ và gap; chưa chứng minh vô nghiệm.",
            )
        ]
    )
    return scheduled, FeasibilityReport(
        status=status,
        required_seconds=sum(s.duration_seconds for s in scheduled) if fully_scheduled else None,
        available_seconds=available,
        unscheduled_seconds=unscheduled,
        deficit_seconds=deficit,
        verification_scope=CandidateVerificationScope(
            scope_type="candidate_parts", client_part_keys=scope_keys, planning_horizon=horizon
        ),
        fully_scheduled=fully_scheduled,
        blocking_reasons=reasons,
        unestimated_part_ids=[
            p.client_part_key
            for p in planning_parts
            if p.rough_estimate_seconds is None and p.decomposition_state != "complete"
        ],
        unplanned_part_ids=unplanned,
        extension_candidate_at=None,
    )
