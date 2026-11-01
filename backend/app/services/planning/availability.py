"""Module availability: Hiện thực hóa bài toán [A06] - Tính khoảng giờ khả dụng.

Đảm nhận:
- Khai triển quy tắc rảnh lặp theo tuần (weekly_rules) và ngày ngoại lệ (date_overrides) theo timezone IANA.
- Giao tập đoạn với task_limits và kẹp vào khung cửa sổ lập lịch [window_start, window_end].
- Tập hợp và hợp nhất các khoảng bận (Google Calendar Context, manual_busy, fixed_sessions).
- Bảo vệ own-event: chỉ loại sự kiện Google Calendar gắn đúng Session đang dời, bảo toàn sự kiện ngoại vi trùng giờ.
- Cắt trừ hai tập đoạn bằng kỹ thuật Two-Pointer Interval Subtraction để thu được effective_intervals.
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, time, timedelta

from app.schemas.availability import Availability
from app.schemas.calendar import CalendarContext, ManualBusyInterval
from app.schemas.common import TimeInterval
from app.schemas.task import Session


def _parse_time_str(time_val: str | time) -> tuple[int, int]:
    """Parse time thành (hour, minute). Hỗ trợ cả str 'HH:mm', '24:00' và datetime.time."""
    if isinstance(time_val, time):
        return time_val.hour, time_val.minute
    parts = time_val.strip().split(":")
    return int(parts[0]), int(parts[1])


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Hợp nhất các khoảng thời gian giao nhau hoặc chạm nhau (Sweep-line)."""
    if not intervals:
        return []
    sorted_ints = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged: list[tuple[datetime, datetime]] = [sorted_ints[0]]

    for current in sorted_ints[1:]:
        prev_start, prev_end = merged[-1]
        curr_start, curr_end = current

        if curr_start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, curr_end))
        else:
            merged.append(current)

    return merged


def _subtract_intervals(
    free_intervals: list[tuple[datetime, datetime]],
    busy_intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    r"""Thực hiện phép trừ hai tập đoạn: Free \ Busy bằng kỹ thuật 2 con trỏ.

    Args:
        free_intervals: Danh sách khoảng rảnh đã hợp nhất và sắp xếp.
        busy_intervals: Danh sách khoảng bận đã hợp nhất và sắp xếp.

    Returns:
        Danh sách các khoảng rảnh thực sự còn lại.
    """
    if not free_intervals:
        return []
    if not busy_intervals:
        return free_intervals

    result: list[tuple[datetime, datetime]] = []
    busy_idx = 0
    num_busy = len(busy_intervals)

    for f_start, f_end in free_intervals:
        curr_start = f_start

        while busy_idx < num_busy and busy_intervals[busy_idx][1] <= curr_start:
            busy_idx += 1

        b_pointer = busy_idx
        while b_pointer < num_busy and busy_intervals[b_pointer][0] < f_end:
            b_start, b_end = busy_intervals[b_pointer]
            if b_start > curr_start:
                result.append((curr_start, min(b_start, f_end)))
            curr_start = max(curr_start, b_end)
            if curr_start >= f_end:
                break
            b_pointer += 1

        if curr_start < f_end:
            result.append((curr_start, f_end))

    return [(s, e) for s, e in result if e > s]


def compute_effective_free_intervals(
    availability: Availability,
    window_start: datetime,
    window_end: datetime,
    calendar_context: CalendarContext | None = None,
    manual_busy: list[ManualBusyInterval] | None = None,
    fixed_sessions: list[Session] | None = None,
    rescheduled_session_ids: set[str] | None = None,
    task_id: str | None = None,
) -> list[TimeInterval]:
    """Tính danh sách khoảng giờ rảnh khả dụng thực tế (A06).

    Args:
        availability: Cấu hình giờ rảnh đã xác nhận.
        window_start: Mốc bắt đầu khung thời gian (có múi giờ).
        window_end: Mốc kết thúc khung thời gian (có múi giờ).
        calendar_context: Ngữ cảnh lịch Google (O21) nếu có.
        manual_busy: Các khoảng bận nhập tay nếu có.
        fixed_sessions: Các Session đã được chốt của task khác hoặc phiên cố định.
        rescheduled_session_ids: Tập ID các Session đang được xếp lại (để loại own-event).
        task_id: ID công việc để đối chiếu task_limits (nếu có).

    Returns:
        Danh sách TimeInterval chứa các khoảng rảnh khả dụng quy về UTC.
    """
    resched_ids = rescheduled_session_ids or set()
    w_start_utc = window_start.astimezone(UTC)
    w_end_utc = window_end.astimezone(UTC)

    if w_start_utc >= w_end_utc:
        return []

    tz_str = availability.timezone or "Asia/Ho_Chi_Minh"
    try:
        local_tz = zoneinfo.ZoneInfo(tz_str)
    except Exception:
        local_tz = UTC

    local_start = w_start_utc.astimezone(local_tz)
    local_end = w_end_utc.astimezone(local_tz)
    start_date = local_start.date()
    end_date = local_end.date()

    override_map = {
        ov.date: ov.intervals for ov in availability.date_overrides
    }

    raw_free_intervals: list[tuple[datetime, datetime]] = []
    curr_date = start_date

    while curr_date <= end_date:
        date_str = curr_date.isoformat()
        if date_str in override_map:
            day_intervals = override_map[date_str]
        else:
            wday = curr_date.isoweekday()
            day_intervals = [
                r for r in availability.weekly_rules if r.weekday == wday
            ]

        for interval in day_intervals:
            s_h, s_m = _parse_time_str(interval.start_local)
            e_h, e_m = _parse_time_str(interval.end_local)

            dt_start = datetime(
                curr_date.year, curr_date.month, curr_date.day, s_h, s_m, tzinfo=local_tz
            )
            if e_h == 24 and e_m == 0:
                dt_end = datetime(
                    curr_date.year, curr_date.month, curr_date.day, 0, 0, tzinfo=local_tz
                ) + timedelta(days=1)
            else:
                dt_end = datetime(
                    curr_date.year, curr_date.month, curr_date.day, e_h, e_m, tzinfo=local_tz
                )

            dt_start_utc = dt_start.astimezone(UTC)
            dt_end_utc = dt_end.astimezone(UTC)

            if dt_end_utc > dt_start_utc:
                raw_free_intervals.append((dt_start_utc, dt_end_utc))

        curr_date += timedelta(days=1)

    merged_free = _merge_intervals(raw_free_intervals)

    if task_id and availability.task_limits:
        task_lims = [
            lim for lim in availability.task_limits if lim.task_id == task_id
        ]
        if task_lims:
            lim_intervals = [
                (
                    lim_int.start_at.astimezone(UTC),
                    lim_int.end_at.astimezone(UTC),
                )
                for lim in task_lims
                for lim_int in lim.intervals
            ]
            merged_limits = _merge_intervals(lim_intervals)
            intersected: list[tuple[datetime, datetime]] = []
            for f_s, f_e in merged_free:
                for l_s, l_e in merged_limits:
                    inter_s = max(f_s, l_s)
                    inter_e = min(f_e, l_e)
                    if inter_e > inter_s:
                        intersected.append((inter_s, inter_e))
            merged_free = _merge_intervals(intersected)

    clipped_free: list[tuple[datetime, datetime]] = []
    for f_s, f_e in merged_free:
        s = max(f_s, w_start_utc)
        e = min(f_e, w_end_utc)
        if e > s:
            clipped_free.append((s, e))
    clipped_free = _merge_intervals(clipped_free)

    busy_list: list[tuple[datetime, datetime]] = []

    if calendar_context and calendar_context.busy_intervals:
        for busy_item in calendar_context.busy_intervals:
            is_own_event = False
            if (
                busy_item.source
                and busy_item.source.session_id
                and busy_item.source.session_id in resched_ids
            ):
                is_own_event = True

            if not is_own_event:
                b_s = busy_item.start_at.astimezone(UTC)
                b_e = busy_item.end_at.astimezone(UTC)
                if b_e > b_s:
                    busy_list.append((b_s, b_e))

    if manual_busy:
        for m_busy in manual_busy:
            b_s = m_busy.start_at.astimezone(UTC)
            b_e = m_busy.end_at.astimezone(UTC)
            if b_e > b_s:
                busy_list.append((b_s, b_e))

    if fixed_sessions:
        for fs in fixed_sessions:
            if fs.session_id not in resched_ids:
                b_s = fs.scheduled_start.astimezone(UTC)
                b_e = fs.scheduled_end.astimezone(UTC)
                if b_e > b_s:
                    busy_list.append((b_s, b_e))

    merged_busy = _merge_intervals(busy_list)

    effective_raw = _subtract_intervals(clipped_free, merged_busy)

    return [
        TimeInterval(start_at=start_dt, end_at=end_dt)
        for start_dt, end_dt in effective_raw
    ]
