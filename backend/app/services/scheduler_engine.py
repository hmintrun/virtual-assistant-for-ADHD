# Facade cho toàn bộ thuật toán lập lịch UF2 & UF6
# Phân công: Quốc Thịnh (Backend & Algorithm Designer)
# Kiến trúc: Đóng gói thuật toán thuần (Pure Python), không phụ thuộc DB hay Network

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
    calculate_planned_break_minutes,
    calculate_planned_duration_minutes,
)
from app.services.planning.exceptions import EmptyScheduleError, UnfeasibleScheduleError
from app.services.planning.models import PlanStepDraft
from app.services.planning.priority import compute_step_priority_score
from app.services.planning.scheduling import (
    order_steps_by_priority,
    schedule_candidate_sessions,
)
from app.services.planning.validation import validate_planning_prerequisites

__all__ = [
    "CalendarIngestionResult",
    "clip_busy_interval",
    "ingest_calendar_sources",
    "merge_busy_intervals",
    "normalize_google_event",
    "build_session_segments",
    "calculate_buffered_work",
    "compute_effective_free_intervals",
    "calculate_planned_break_minutes",
    "calculate_planned_duration_minutes",
    "compute_step_priority_score",
    "EmptyScheduleError",
    "evaluate_buffer_adaptation",
    "order_steps_by_priority",
    "PlanStepDraft",
    "schedule_candidate_sessions",
    "shift_elapsed_sessions",
    "validate_planning_prerequisites",
]
