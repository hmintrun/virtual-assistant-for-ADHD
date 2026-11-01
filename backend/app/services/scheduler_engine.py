"""Scheduler Engine: Facade trung tâm cho toàn bộ lõi thuật toán lập lịch và thích ứng.

Tuân thủ kiến trúc phân tách trách nhiệm (Hybrid Architecture):
- Thuật toán Python đảm nhận 100% các phép toán thời gian, khoảng rảnh, chia phiên, kiểm tra gap và thích ứng.
- Không phụ thuộc vào LLM, không gọi API bên ngoài, không mở DB transaction dài.
- Tiếp nhận và trả về các cấu trúc dữ liệu / Pydantic schemas chuẩn hóa.
"""

from __future__ import annotations

from app.services.planning.adaptation import evaluate_buffer_adaptation
from app.services.planning.availability import compute_effective_free_intervals
from app.services.planning.durations import (
    build_session_segments,
    calculate_buffered_work,
    prepare_step_durations,
)
from app.services.planning.rescheduling import shift_elapsed_sessions
from app.services.planning.scheduling import schedule_candidate_sessions
from app.services.planning.validation import validate_planning_prerequisites

__all__ = [
    "build_session_segments",
    "calculate_buffered_work",
    "compute_effective_free_intervals",
    "evaluate_buffer_adaptation",
    "prepare_step_durations",
    "schedule_candidate_sessions",
    "shift_elapsed_sessions",
    "validate_planning_prerequisites",
]
