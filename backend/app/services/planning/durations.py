"""Module durations: Hiện thực hóa bài toán [A05] - Buffer Time và [A08] - Tạo cấu trúc Sessions.

Đảm nhận:
- Nhân hệ số đệm dự phòng cá nhân hóa k_buffer đúng 1 lần vào thời lượng làm việc chính (A05).
- Bảo toàn ước lượng gốc estimated_base_seconds của Step (A05).
- Phân rã thời gian làm chính thành chuỗi các quãng work/rest xen kẽ vừa sức tập trung (A08).
- Không cộng micro-task hay khoảng cách 15 phút giữa các phiên vào duration_seconds của phiên (A08).
"""

from __future__ import annotations

import math

from app.schemas.common import SessionSegment
from app.schemas.planning import CandidateStep
from app.schemas.settings import UserPreference


def calculate_buffered_work(
    estimated_base_seconds: int,
    buffer_multiplier: float,
) -> int:
    """Tính thời lượng làm việc chính sau khi nhân hệ số dự phòng (A05).

    Công thức: W_buffered = int(ceil(estimated_base_seconds * k))

    Args:
        estimated_base_seconds: Ước lượng làm việc chính gốc (giây) > 0.
        buffer_multiplier: Hệ số đệm k > 0 (lấy từ cấu hình effective).

    Returns:
        Thời lượng làm việc chính sau buffer (giây).
    """
    if estimated_base_seconds <= 0:
        raise ValueError("estimated_base_seconds phải là số nguyên dương (> 0).")
    if buffer_multiplier <= 0:
        raise ValueError("buffer_multiplier phải là số dương (> 0).")

    return int(math.ceil(estimated_base_seconds * buffer_multiplier))


def build_session_segments(
    buffered_work_seconds: int,
    work_interval_seconds: int,
    rest_interval_seconds: int,
) -> tuple[list[SessionSegment], int, int, int]:
    """Phân rã thời lượng làm chính thành chuỗi quãng work/rest xen kẽ (A08).

    Quy tắc:
    - Mỗi quãng làm việc chính tối đa work_interval_seconds.
    - Giữa 2 quãng làm việc chính chèn 1 quãng nghỉ nội bộ rest_interval_seconds (nếu > 0).
    - Quãng cuối cùng luôn là quãng làm việc (không có quãng nghỉ dư thừa ở cuối phiên).
    - Mọi quãng đều có duration_seconds > 0.

    Args:
        buffered_work_seconds: Tổng thời gian làm chính đã tính buffer (giây).
        work_interval_seconds: Độ dài tối đa của 1 quãng làm chính (giây, ví dụ 1500s = 25p).
        rest_interval_seconds: Độ dài quãng nghỉ nội bộ giữa giờ (giây, ví dụ 300s = 5p).

    Returns:
        tuple (segments, total_work_seconds, total_rest_seconds, total_duration_seconds)
    """
    if buffered_work_seconds <= 0:
        raise ValueError("buffered_work_seconds phải > 0.")
    if work_interval_seconds <= 0:
        raise ValueError("work_interval_seconds phải > 0.")
    if rest_interval_seconds < 0:
        raise ValueError("rest_interval_seconds không được âm (>= 0).")

    segments: list[SessionSegment] = []
    rem_work = buffered_work_seconds
    order_idx = 0
    total_work = 0
    total_rest = 0

    while rem_work > 0:
        chunk_work = min(rem_work, work_interval_seconds)
        segments.append(
            SessionSegment(
                order_index=order_idx,
                kind="work",
                duration_seconds=chunk_work,
            )
        )
        total_work += chunk_work
        rem_work -= chunk_work
        order_idx += 1

        if rem_work > 0 and rest_interval_seconds > 0:
            segments.append(
                SessionSegment(
                    order_index=order_idx,
                    kind="rest",
                    duration_seconds=rest_interval_seconds,
                )
            )
            total_rest += rest_interval_seconds
            order_idx += 1

    total_duration = total_work + total_rest
    return segments, total_work, total_rest, total_duration


def prepare_step_durations(
    step: CandidateStep,
    pref: UserPreference,
) -> tuple[int, list[SessionSegment], int, int, int]:
    """Tính toán toàn bộ thông số thời lượng cho một Step (A05 + A08).

    Bảo toàn Step.estimated_base_seconds gốc và nhân k_buffer đúng 1 lần.

    Args:
        step: CandidateStep cần tính.
        pref: UserPreference chứa cấu hình effective.

    Returns:
        tuple (buffered_work_seconds, segments, total_work, total_rest, duration_seconds)
    """
    if step.remaining_base_seconds == 0:
        return 0, [], 0, 0, 0
    buffered_work = calculate_buffered_work(
        step.remaining_base_seconds,
        step.buffer_multiplier if step.buffer_multiplier > 0 else pref.buffer_multiplier,
    )
    segments, total_work, total_rest, duration = build_session_segments(
        buffered_work_seconds=buffered_work,
        work_interval_seconds=pref.work_interval_seconds,
        rest_interval_seconds=pref.rest_interval_seconds,
    )
    return buffered_work, segments, total_work, total_rest, duration


def fit_session_to_slot(
    remaining_work_seconds: int,
    slot_seconds: int,
    work_interval_seconds: int,
    rest_interval_seconds: int,
) -> tuple[list[SessionSegment], int, int, int]:
    """Tìm lượng work lớn nhất vừa khe; nghỉ chỉ chèn giữa hai quãng work.

    Binary search trên số giây work, không nhân buffer lần nữa và không
    cộng gap giữa Sessions vào duration của Session.
    """
    if work_interval_seconds <= 0 or rest_interval_seconds < 0:
        raise ValueError("Cấu hình work/rest không hợp lệ.")
    if remaining_work_seconds < 0 or slot_seconds < 0:
        raise ValueError("Khối lượng và sức chứa không được âm.")
    lower, upper = 0, min(remaining_work_seconds, slot_seconds)
    while lower < upper:
        work = (lower + upper + 1) // 2
        rest = ((work - 1) // work_interval_seconds) * rest_interval_seconds
        if work + rest <= slot_seconds:
            lower = work
        else:
            upper = work - 1
    if lower == 0:
        return [], 0, 0, 0
    segments, work, rest, duration = build_session_segments(
        lower, work_interval_seconds, rest_interval_seconds
    )
    if (
        work < remaining_work_seconds
        and len(segments) > 1
        and segments[-1].kind == "work"
        and segments[-1].duration_seconds < min(120, work_interval_seconds)
    ):
        lower = (work // work_interval_seconds) * work_interval_seconds
        return build_session_segments(lower, work_interval_seconds, rest_interval_seconds)
    return segments, work, rest, duration
