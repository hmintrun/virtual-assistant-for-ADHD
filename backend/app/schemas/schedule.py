# Pydantic v2 schemas cho Session, ScheduleVersion và lịch chính thức (Dòng 12, 14, 15 trên API Sheet)
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.common import StrictSchema
from app.schemas.planning import SessionSegment


class Session(StrictSchema):
    """O09 Session: Phiên tập trung chính thức đã lưu vào lịch sau khi xác nhận UF2."""

    session_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_text: str = Field(min_length=1)
    micro_task_seconds: int | None = Field(default=None, ge=0)
    work_seconds: int = Field(ge=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(ge=0)
    schedule_revision: int = Field(ge=0)
    status: str = Field(min_length=1)
    calendar_event_link_id: str | None = None
    start_state: Literal["waiting", "not_started", "started"]
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def validate_session_durations(self) -> Self:
        if self.duration_seconds != (self.work_seconds + self.rest_seconds):
            raise ValueError("duration_seconds phải bằng đúng work_seconds + rest_seconds.")
        return self


class ScheduleVersion(StrictSchema):
    """O11 ScheduleVersion: Phiên bản lịch chính thức của người dùng sau khi xác nhận UF2."""

    version_number: int = Field(ge=0)
    base_version: int | None = Field(default=None, ge=0)
    approved_at: datetime
    affected_task_ids: list[str]
    min_inter_session_gap_seconds: int = Field(ge=0)
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)


class ScheduleData(StrictSchema):
    """Dòng 15 trên API Sheet (GET /api/v1/schedule): Dữ liệu lịch chính thức đã xác nhận trong khoảng [from, to)."""

    schedule_revision: int = Field(ge=0)
    items: list[Session]
    next_cursor: str | None = None
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)

