"""Module rescheduling: Hiện thực hóa bài toán [A17] - Tái lập lịch thích ứng UF6.

Đảm nhận:
- Tịnh tiến các ô lịch đã trôi qua theo thời gian thực (real_time_anchor) khi người dùng mở lại app sau gián đoạn.
- Cô lập phạm vi: chỉ dời các công việc nằm trong allowed_task_ids; giữ nguyên lịch các công việc khác.
- Kiểm tra lại tính khả thi và sinh phương án dời lịch (ProposedChanges / FeasibilityReport).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.schemas.common import TimeInterval
from app.schemas.task import Session


def shift_elapsed_sessions(
    sessions: list[Session],
    allowed_task_ids: list[str],
    effective_intervals: list[TimeInterval],
    real_time_anchor: datetime,
    min_gap_seconds: int = 900,
    step_to_task_map: dict[str, str] | None = None,
) -> tuple[list[dict], list[str], bool, int]:
    """Tịnh tiến các phiên đã trôi qua hoặc cần dời sang các ô rảnh trong tương lai (A17).

    Args:
        sessions: Danh sách các Session hiện tại của người dùng.
        allowed_task_ids: Danh sách ID các Task người dùng cho phép dời.
        effective_intervals: Danh sách khoảng rảnh trong tương lai.
        real_time_anchor: Thời điểm thực tế hiện tại (có múi giờ).
        min_gap_seconds: Khoảng cách tối thiểu giữa 2 phiên (mặc định 900s).
        step_to_task_map: Bản đồ map step_id sang task_id tương ứng nếu có.

    Returns:
        tuple (moved_sessions: list[dict], affected_task_ids: list[str], all_shifted: bool, deficit_seconds: int)
    """
    anchor_utc = real_time_anchor.astimezone(UTC)
    allowed_set = set(allowed_task_ids)

    sessions_to_move: list[Session] = []
    fixed_ranges: list[tuple[datetime, datetime]] = []
    affected_tasks: set[str] = set()

    for s in sessions:
        s_start = s.scheduled_start.astimezone(UTC)
        s_end = s.scheduled_end.astimezone(UTC)
        tid = (
            step_to_task_map.get(s.step_id, s.step_id)
            if step_to_task_map
            else getattr(s, "task_id", s.step_id)
        )

        if tid not in allowed_set or s.status in ("completed", "active"):
            fixed_ranges.append((s_start, s_end))
        else:
            if s_start < anchor_utc or s.status == "incomplete":
                sessions_to_move.append(s)
                affected_tasks.add(tid)
            else:
                fixed_ranges.append((s_start, s_end))

    if not sessions_to_move:
        return [], [], True, 0

    fixed_ranges.sort(key=lambda x: x[0])

    future_free_slots: list[list[datetime]] = []
    total_free_sec = 0

    for interval in effective_intervals:
        s_utc = interval.start_at.astimezone(UTC)
        e_utc = interval.end_at.astimezone(UTC)
        clamped_s = max(s_utc, anchor_utc)
        if e_utc > clamped_s:
            future_free_slots.append([clamped_s, e_utc])
            total_free_sec += int((e_utc - clamped_s).total_seconds())

    moved_results: list[dict] = []
    last_end = anchor_utc
    total_req_sec = sum(s.duration_seconds for s in sessions_to_move)
    all_shifted = True

    for session in sessions_to_move:
        duration_sec = session.duration_seconds
        placed = False

        for slot_idx, slot in enumerate(future_free_slots):
            slot_start, slot_end = slot[0], slot[1]
            earliest_start = max(slot_start, last_end + timedelta(seconds=min_gap_seconds))

            for f_s, f_e in fixed_ranges:
                if earliest_start < f_e and earliest_start + timedelta(seconds=duration_sec) > f_s:
                    earliest_start = max(earliest_start, f_e + timedelta(seconds=min_gap_seconds))

            cand_end = earliest_start + timedelta(seconds=duration_sec)

            if cand_end <= slot_end:
                moved_results.append(
                    {
                        "session_id": session.session_id,
                        "old_start": session.scheduled_start,
                        "old_end": session.scheduled_end,
                        "new_start": earliest_start,
                        "new_end": cand_end,
                        "duration_seconds": duration_sec,
                    }
                )
                last_end = cand_end
                placed = True

                if cand_end < slot_end:
                    future_free_slots[slot_idx][0] = cand_end
                else:
                    future_free_slots.pop(slot_idx)
                break

        if not placed:
            all_shifted = False

    deficit = max(0, total_req_sec - total_free_sec)
    return moved_results, list(affected_tasks), all_shifted, deficit
