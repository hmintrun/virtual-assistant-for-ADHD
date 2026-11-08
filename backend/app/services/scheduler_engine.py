"""Scheduler Engine: Facade trung tâm cho toàn bộ lõi thuật toán lập lịch và thích ứng.

Tuân thủ kiến trúc phân tách trách nhiệm (Hybrid Architecture):
- Thuật toán Python đảm nhận 100% các phép toán thời gian, khoảng rảnh, chia phiên, kiểm tra gap và thích ứng.
- Không phụ thuộc vào LLM, không gọi API bên ngoài, không mở DB transaction dài.
- Tiếp nhận và trả về các cấu trúc dữ liệu / Pydantic schemas chuẩn hóa.
"""

from __future__ import annotations

from app.services.planning.adaptation import evaluate_buffer_adaptation
from app.services.planning.availability import compute_effective_free_intervals
from app.services.planning.calendar_ingestion import (
    CalendarIngestionResult,
    clip_busy_interval,
    ingest_calendar_sources,
    merge_busy_intervals,
    normalize_google_event,
)
from app.services.planning.durations import (
    build_session_segments,
    calculate_buffered_work,
    prepare_step_durations,
)
from app.services.planning.rescheduling import shift_elapsed_sessions
from app.services.planning.scheduling import (
    compute_task_priority_key,
    order_steps_by_priority,
    schedule_candidate_sessions,
)
from app.services.planning.validation import (
    SessionStartFeasibilityResult,
    validate_planning_prerequisites,
    validate_session_start_feasibility,
)

__all__ = [
    "CalendarIngestionResult",
    "clip_busy_interval",
    "ingest_calendar_sources",
    "merge_busy_intervals",
    "normalize_google_event",
    "build_session_segments",
    "calculate_buffered_work",
    "compute_effective_free_intervals",
    "compute_task_priority_key",
    "evaluate_buffer_adaptation",
    "order_steps_by_priority",
    "prepare_step_durations",
    "schedule_candidate_sessions",
    "shift_elapsed_sessions",
    "validate_planning_prerequisites",
    "SessionStartFeasibilityResult",
    "validate_session_start_feasibility",
]
