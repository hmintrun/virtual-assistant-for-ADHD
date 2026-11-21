"""A07/A09/A10: xếp tuần tự theo khe rảnh, bảo toàn gap và phạm vi kiểm.

Greedy theo Step; một Step có thể gồm nhiều Sessions. Không di chuyển
phiên cố định, không chứng minh vô nghiệm chỉ từ việc greedy chưa tìm đủ lịch.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.common import BlockingReason, TimeConstraint, TimeInterval
from app.schemas.planning import (
    CandidateSession,
    CandidateStep,
    CandidateVerificationScope,
    FeasibilityReport,
)
from app.schemas.settings import UserPreference
from app.schemas.task import Session
from app.services.planning.availability import _merge_intervals, _subtract_intervals
from app.services.planning.durations import calculate_buffered_work, fit_session_to_slot

_DEFAULT_TZ_NAME = "Asia/Ho_Chi_Minh"
_DUR_PATTERN = (
    r"(?:(\d+)\s*(?:h|giờ|tiếng)\s*(\d{1,2})\s*(?:p|phút)?"
    r"|(\d+(?:[.,]\d+)?)\s*(phút|p|giờ|tiếng|h)(?![a-zà-ỹ0-9_]))"
)
_DAILY_MAX_AFTER_RE = re.compile(
    rf"(?:mỗi|hằng|hàng)\s+ngày[^.;\n]*?(?:tối\s*đa|không\s*(?:làm\s*)?quá|nhiều\s*nhất|giới\s*hạn)\s*{_DUR_PATTERN}",
    re.IGNORECASE,
)
_DAILY_MAX_BEFORE_RE = re.compile(
    rf"(?:tối\s*đa|không\s*(?:làm\s*)?quá|nhiều\s*nhất|giới\s*hạn)\s*{_DUR_PATTERN}\s*(?:/|mỗi|hằng|hàng|trong)?\s*(?:1\s*)?ngày(?![a-zà-ỹ0-9_])",
    re.IGNORECASE,
)
_MIN_SLOT_AFTER_RE = re.compile(
    rf"(?:ít\s*nhất|tối\s*thiểu|>=|từ)\s*{_DUR_PATTERN}\s*(?:trống\s*liền|trống\s*liên\s*tục|liền\s*mạch|liên\s*tục|liền)(?![a-zà-ỹ0-9_])",
    re.IGNORECASE,
)
_MIN_SLOT_BEFORE_RE = re.compile(
    rf"(?:khe\s*(?:trống\s*)?(?:liền|liên\s*tục)|khoảng\s*trống\s*(?:liền|liên\s*tục))\s*(?:ít\s*nhất|tối\s*thiểu|>=)?\s*{_DUR_PATTERN}",
    re.IGNORECASE,
)
_PREF_RANGE_RE = re.compile(
    r"ưu\s*tiên\s*(?:từ|trong\s*khoảng)\s*(\d{1,2})(?:[:h](\d{1,2}))?\s*(?:h|giờ)?\s*(?:đến|tới|-)\s*(\d{1,2})(?:[:h](\d{1,2}))?\s*(?:h|giờ)?(?![a-zà-ỹ0-9_])",
    re.IGNORECASE,
)
_PREF_AFTER_RE = re.compile(
    r"ưu\s*tiên\s*(?:sau|từ)\s*(\d{1,2})(?:[:h](\d{1,2}))?\s*(?:h|giờ)?(?!\s*(?:đến|tới|-)\s*\d)(?![a-zà-ỹ0-9_])",
    re.IGNORECASE,
)
_PREF_BEFORE_RE = re.compile(
    r"ưu\s*tiên\s*trước\s*(\d{1,2})(?:[:h](\d{1,2}))?\s*(?:h|giờ)?(?![a-zà-ỹ0-9_])",
    re.IGNORECASE,
)
_PREF_PERIOD_RE = re.compile(
    r"(buổi\s+sáng|buổi\s+trưa|buổi\s+chiều|buổi\s+tối|ban\s+đêm)",
    re.IGNORECASE,
)
_NEGATED_PREFIX_RE = re.compile(
    r"(?:tránh(?:\s+làm|\s+xếp)?|không(?:\s+làm|\s+xếp)?|bỏ|trừ|ngoài)\s*(?:vào\s*)?$",
    re.IGNORECASE,
)
_PERIOD_WINDOWS: dict[str, tuple[int, int]] = {
    "buổi sáng": (5 * 60, 12 * 60),
    "buổi trưa": (11 * 60, 14 * 60),
    "buổi chiều": (12 * 60, 18 * 60),
    "buổi tối": (18 * 60, 24 * 60),
    "ban đêm": (20 * 60, 24 * 60),
}


@dataclass(frozen=True)
class ExtraSchedulingConstraints:
    """Ràng buộc lập lịch bổ sung được phân tích offline từ extra_requirements."""

    max_daily_work_seconds: int | None = None
    min_contiguous_slot_seconds: int | None = None
    preferred_time_windows: tuple[tuple[int, int], ...] = ()


def _duration_from_match(match: re.Match[str]) -> int | None:
    h_str, m_str, num_str, unit_str = match.groups()
    if h_str is not None and m_str is not None:
        total = int(h_str) * 3600 + int(m_str) * 60
        return total if total > 0 else None
    if num_str is not None and unit_str is not None:
        value = float(num_str.replace(",", "."))
        unit = unit_str.lower()
        total = round(value * 3600) if unit in {"giờ", "tiếng", "h"} else round(value * 60)
        return total if total > 0 else None
    return None


def _minutes_from_hhmm(hour_str: str, min_str: str | None) -> int | None:
    hour = int(hour_str)
    minute = int(min_str) if min_str is not None else 0
    if hour == 24 and minute == 0:
        return 24 * 60
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour * 60 + minute
    return None


def parse_extra_requirements(
    extra_requirements: Sequence[str] | None,
) -> ExtraSchedulingConstraints:
    """Phân tích thuần Python các ràng buộc lặp lại từ `extra_requirements` (RISK-UF2-02)."""
    if not extra_requirements:
        return ExtraSchedulingConstraints()

    daily_caps: list[int] = []
    min_slots: list[int] = []
    preferred: list[tuple[int, int]] = []

    for raw in extra_requirements:
        if not isinstance(raw, str):
            continue
        text = " ".join(raw.strip().lower().split())
        if not text:
            continue

        for pattern in (_DAILY_MAX_AFTER_RE, _DAILY_MAX_BEFORE_RE):
            for match in pattern.finditer(text):
                secs = _duration_from_match(match)
                if secs is not None:
                    daily_caps.append(secs)

        for pattern in (_MIN_SLOT_AFTER_RE, _MIN_SLOT_BEFORE_RE):
            for match in pattern.finditer(text):
                secs = _duration_from_match(match)
                if secs is not None:
                    min_slots.append(secs)

        for match in _PREF_RANGE_RE.finditer(text):
            s_min = _minutes_from_hhmm(match.group(1), match.group(2))
            e_min = _minutes_from_hhmm(match.group(3), match.group(4))
            if s_min is not None and e_min is not None and s_min < e_min:
                preferred.append((s_min, e_min))

        for match in _PREF_AFTER_RE.finditer(text):
            s_min = _minutes_from_hhmm(match.group(1), match.group(2))
            if s_min is not None and s_min < 24 * 60:
                preferred.append((s_min, 24 * 60))

        for match in _PREF_BEFORE_RE.finditer(text):
            e_min = _minutes_from_hhmm(match.group(1), match.group(2))
            if e_min is not None and e_min > 0:
                preferred.append((0, e_min))

        for match in _PREF_PERIOD_RE.finditer(text):
            prefix = text[max(0, match.start() - 24) : match.start()]
            if _NEGATED_PREFIX_RE.search(prefix):
                continue
            period_key = " ".join(match.group(1).split())
            window = _PERIOD_WINDOWS.get(period_key)
            if window is not None:
                preferred.append(window)

    return ExtraSchedulingConstraints(
        max_daily_work_seconds=min(daily_caps) if daily_caps else None,
        min_contiguous_slot_seconds=max(min_slots) if min_slots else None,
        preferred_time_windows=tuple(dict.fromkeys(preferred)),
    )


def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    if tz_name and tz_name.strip():
        try:
            return ZoneInfo(tz_name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo(_DEFAULT_TZ_NAME)


def _split_intervals_by_local_day(
    intervals: Sequence[tuple[datetime, datetime]],
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for s_utc, e_utc in intervals:
        curr = s_utc
        while curr < e_utc:
            curr_local = curr.astimezone(tz)
            next_day = curr_local.date() + timedelta(days=1)
            next_midnight_utc = datetime.combine(next_day, time.min, tzinfo=tz).astimezone(UTC)
            if next_midnight_utc <= curr:
                next_midnight_utc = curr + timedelta(days=1)
            chunk_end = min(e_utc, next_midnight_utc)
            if chunk_end > curr:
                result.append((curr, chunk_end))
            curr = chunk_end
    return result


def _intersect_preferred_windows(
    intervals: Sequence[tuple[datetime, datetime]],
    preferred_windows: tuple[tuple[int, int], ...],
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    daily_chunks = _split_intervals_by_local_day(intervals, tz)
    matched: list[tuple[datetime, datetime]] = []
    for s_utc, e_utc in daily_chunks:
        local_day = s_utc.astimezone(tz).date()
        day_start = datetime.combine(local_day, time.min, tzinfo=tz)
        for win_start_min, win_end_min in preferred_windows:
            w_start_utc = (day_start + timedelta(minutes=win_start_min)).astimezone(UTC)
            w_end_utc = (day_start + timedelta(minutes=win_end_min)).astimezone(UTC)
            inter_start = max(s_utc, w_start_utc)
            inter_end = min(e_utc, w_end_utc)
            if inter_end > inter_start:
                matched.append((inter_start, inter_end))
    return _merge_intervals(matched)


def subtract_fixed_session_gaps(
    effective_intervals: Sequence[TimeInterval],
    fixed_sessions: Sequence[Session],
    window_start: datetime,
    window_end: datetime,
    min_gap_seconds: int = 900,
) -> list[tuple[datetime, datetime]]:
    """Kẹp khoảng rảnh vào [window_start, window_end] và trừ fixed sessions kèm gap hai phía."""
    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    gap = timedelta(seconds=min_gap_seconds)
    free: list[tuple[datetime, datetime]] = []
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
        if s.status not in ("cancelled", "skipped")
    ]
    return _subtract_intervals(_merge_intervals(free), _merge_intervals(protected))


def compute_task_priority_key(
    time_constraint: TimeConstraint,
    importance: int | None = None,
    created_at: datetime | None = None,
    task_id: str = "",
) -> tuple[datetime, int, datetime, tuple[datetime, str]]:
    """Tính khóa ưu tiên PriorityKey(T) của một Task theo đặc tả [A07].

    Quy tắc từ điển tăng dần:
    1. finish_by_at / deadline_at (UTC): Hạn sớm hơn đứng trước (EDF).
    2. -importance: Độ quan trọng cao hơn đứng trước (null -> 0).
    3. start_at (UTC): Mốc cho phép bắt đầu sớm hơn đứng trước.
    4. stable_tie_breaker: (created_at_utc, task_id) đảm bảo tính tất định.
    """
    task_end = (
        time_constraint.deadline_at
        if time_constraint.kind == "deadline"
        else time_constraint.finish_by_at
    )
    if time_constraint.start_at is None or task_end is None:
        raise ValueError("time_constraint cần đủ mốc bắt đầu và kết thúc.")
    if time_constraint.start_at.utcoffset() is None or task_end.utcoffset() is None:
        raise ValueError("Mốc thời gian phải có offset.")

    end_utc = task_end.astimezone(UTC)
    start_utc = time_constraint.start_at.astimezone(UTC)
    imp_score = -(importance if importance is not None else 0)
    created_utc = (
        created_at.astimezone(UTC) if created_at is not None else datetime.min.replace(tzinfo=UTC)
    )

    return (end_utc, imp_score, start_utc, (created_utc, task_id))


def order_steps_by_priority(
    steps: list[CandidateStep],
    task_priority_map: dict[str, tuple] | None = None,
    step_to_task_map: dict[str, str] | None = None,
) -> list[CandidateStep]:
    """Sắp xếp chuỗi Steps cần lên lịch tuân thủ 2 tầng phân cấp của [A07]:

    1. Cấp Task: Thứ tự ưu tiên giữa các Task theo PriorityKey(T) (Deadline -> Importance -> StartAt -> TieBreaker).
    2. Cấp Step: Phụ thuộc nội bộ trong từng Task theo Step.order_index.
    """
    if len({s.client_step_key for s in steps}) != len(steps):
        raise ValueError("Client Step key bị trùng.")

    default_task_key = (
        datetime.max.replace(tzinfo=UTC),
        0,
        datetime.max.replace(tzinfo=UTC),
        (datetime.max.replace(tzinfo=UTC), ""),
    )

    def sort_key(step: CandidateStep) -> tuple:
        task_id = step_to_task_map.get(step.client_step_key, "") if step_to_task_map else ""
        task_prio = task_priority_map.get(task_id, default_task_key) if task_priority_map else (0,)
        return (task_prio, step.order_index, step.client_step_key)

    return sorted(steps, key=sort_key)


def _run_schedule_pass(
    ordered: list[CandidateStep],
    free_slots: list[tuple[datetime, datetime]],
    pref: UserPreference,
    gap: timedelta,
    constraints: ExtraSchedulingConstraints,
    local_tz: ZoneInfo,
) -> tuple[list[CandidateSession], dict[str, int]]:
    slots = (
        _split_intervals_by_local_day(free_slots, local_tz)
        if constraints.max_daily_work_seconds is not None
        else free_slots
    )
    remaining = {
        s.client_step_key: calculate_buffered_work(s.remaining_base_seconds, s.buffer_multiplier)
        if s.remaining_base_seconds
        else 0
        for s in ordered
    }
    scheduled: list[CandidateSession] = []
    daily_work: dict[date, int] = {}
    min_chunk = min(300, pref.work_interval_seconds)
    last_end: datetime | None = None

    for step in ordered:
        key = step.client_step_key
        session_index = 0
        for slot_start, slot_end in slots:
            if remaining[key] == 0:
                break
            if (
                constraints.min_contiguous_slot_seconds is not None
                and (slot_end - slot_start).total_seconds()
                < constraints.min_contiguous_slot_seconds
            ):
                continue
            left = max(slot_start, last_end + gap) if last_end is not None else slot_start
            capacity = int((slot_end - left).total_seconds())
            if capacity <= 0:
                continue
            if (
                constraints.min_contiguous_slot_seconds is not None
                and capacity < constraints.min_contiguous_slot_seconds
            ):
                continue
            target_work = remaining[key]
            local_day = left.astimezone(local_tz).date()
            if constraints.max_daily_work_seconds is not None:
                used_today = daily_work.get(local_day, 0)
                daily_remaining = constraints.max_daily_work_seconds - used_today
                if daily_remaining <= 0:
                    continue
                target_work = min(remaining[key], daily_remaining)
                if target_work < remaining[key] and target_work < min_chunk:
                    continue
            segments, work, rest, duration = fit_session_to_slot(
                target_work,
                capacity,
                pref.work_interval_seconds,
                pref.rest_interval_seconds,
            )
            if work == 0 or (work < remaining[key] and work < min_chunk):
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
            if constraints.max_daily_work_seconds is not None:
                daily_work[local_day] = daily_work.get(local_day, 0) + work
        # Steps hiện tuần tự: không xếp bước sau nếu bước trước chưa có lịch đủ.
        if remaining[key] > 0:
            break
    return scheduled, remaining


def schedule_candidate_sessions(
    steps: list[CandidateStep],
    effective_intervals: list[TimeInterval],
    fixed_sessions: list[Session],
    time_constraint: TimeConstraint,
    pref: UserPreference,
    horizon_days: int = 14,
    min_gap_seconds: int = 900,
    importance: int | None = None,
    task_id: str = "",
    created_at: datetime | None = None,
    task_priority_map: dict[str, tuple] | None = None,
    step_to_task_map: dict[str, str] | None = None,
    extra_requirements: Sequence[str] | None = None,
) -> tuple[list[CandidateSession], FeasibilityReport]:
    """Trả lịch cụ thể và report cho các phần có Steps được đưa vào lần tính.

    available_seconds là quỹ sau khi trừ fixed Sessions kèm gap hai phía.
    Khi chưa xếp đủ, unscheduled_seconds là work còn lại, deficit_seconds
    là cận dưới thiếu work; required_seconds=null vì nghỉ nội bộ phụ thuộc
    cách chia các Sessions chưa biết. Khi khả thi, required là duration thực.
    """
    if task_priority_map and len(task_priority_map) > 1:
        raise ValueError("Scheduler này chỉ có ràng buộc thời gian cho một Task")
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
    free = subtract_fixed_session_gaps(
        effective_intervals,
        fixed_sessions,
        start,
        end,
        min_gap_seconds=min_gap_seconds,
    )
    available = int(sum((b - a).total_seconds() for a, b in free))

    if task_priority_map is None and task_id:
        task_prio_map = {
            task_id: compute_task_priority_key(time_constraint, importance, created_at, task_id)
        }
        s_to_t_map = step_to_task_map or {s.client_step_key: task_id for s in steps}
    else:
        task_prio_map = task_priority_map
        s_to_t_map = step_to_task_map

    ordered = order_steps_by_priority(
        steps=steps,
        task_priority_map=task_prio_map,
        step_to_task_map=s_to_t_map,
    )
    total_work = sum(
        calculate_buffered_work(s.remaining_base_seconds, s.buffer_multiplier)
        if s.remaining_base_seconds
        else 0
        for s in ordered
    )
    constraints = parse_extra_requirements(extra_requirements)
    local_tz = _resolve_tz(time_constraint.input_timezone)

    scheduled: list[CandidateSession] = []
    remaining: dict[str, int] = {}
    if constraints.preferred_time_windows:
        preferred_free = _intersect_preferred_windows(
            free, constraints.preferred_time_windows, local_tz
        )
        if preferred_free:
            scheduled, remaining = _run_schedule_pass(
                ordered, preferred_free, pref, gap, constraints, local_tz
            )
    if not (bool(steps) and remaining and sum(remaining.values()) == 0):
        scheduled, remaining = _run_schedule_pass(
            ordered, free, pref, gap, constraints, local_tz
        )

    unscheduled = sum(remaining.values())
    fully_scheduled = bool(steps) and unscheduled == 0
    deficit = max(0, total_work - available)
    status = "feasible" if fully_scheduled else "infeasible" if deficit else "search_inconclusive"
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
            client_step_keys=[s.client_step_key for s in ordered], planning_horizon=horizon
        ),
        fully_scheduled=fully_scheduled,
        blocking_reasons=reasons,
        unscheduled_step_keys=[s.client_step_key for s in ordered if remaining[s.client_step_key]],
        extension_candidate_at=None,
    )
