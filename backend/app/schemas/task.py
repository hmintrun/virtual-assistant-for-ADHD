"""Confirmed UF2 task hierarchy returned by the API."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import (
    ApiModel,
    AvailabilityRef,
    SessionSegment,
    TimeConstraint,
)
from app.schemas.draft import TaskSummary


class PlanningPart(ApiModel):
    planning_part_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    client_part_key: str = Field(min_length=1)
    scope_description: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    decomposition_state: Literal["not_started", "partial", "complete"]
    scheduling_state: Literal["not_scheduled", "partial", "complete"]
    unexpanded_scope: str | None = None
    rough_estimate_seconds: int | None = Field(default=None, gt=0)
    estimate_source: str | None = None


class Step(ApiModel):
    step_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    planning_part_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(gt=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(ge=1)
    estimate_source: str = Field(min_length=1)
    status: str = Field(min_length=1)


class Session(ApiModel):
    session_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_seconds: int | None = Field(default=None, ge=0)
    micro_task_text: str = Field(min_length=1)
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    schedule_revision: int = Field(ge=1)
    status: str = Field(min_length=1)
    calendar_event_link_id: str | None = None
    start_state: str = Field(min_length=1)
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def valid_durations(self):
        if (
            self.scheduled_start.utcoffset() is None
            or self.scheduled_end.utcoffset() is None
        ):
            raise ValueError("Giờ phiên phải có múi giờ")
        if self.duration_seconds != self.work_seconds + self.rest_seconds:
            raise ValueError("Thời lượng phiên không bằng làm chính cộng nghỉ")
        if (
            sum(x.duration_seconds for x in self.segments if x.kind == "work")
            != self.work_seconds
        ):
            raise ValueError("Các quãng làm chính không khớp")
        if (
            sum(x.duration_seconds for x in self.segments if x.kind == "rest")
            != self.rest_seconds
        ):
            raise ValueError("Các quãng nghỉ không khớp")
        if (
            self.scheduled_end - self.scheduled_start
        ).total_seconds() != self.duration_seconds:
            raise ValueError("Giờ kết thúc không khớp thời lượng phiên")
        if [x.order_index for x in self.segments] != list(range(len(self.segments))):
            raise ValueError("Thứ tự các quãng không liên tục")
        return self


class Task(ApiModel):
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint
    importance: int | None = Field(default=None, ge=1)
    status: str = Field(min_length=1)
    source_draft_id: str = Field(min_length=1)
    decomposition_state: Literal["not_started", "partial", "complete"]
    scheduling_state: Literal["not_scheduled", "partial", "complete"]
    availability_ref: AvailabilityRef
    planning_parts: list[PlanningPart]
    steps: list[Step]
    sessions: list[Session]
    source_summary: TaskSummary


class TaskListData(ApiModel):
    """Dòng 13 trên API Sheet (GET /api/v1/tasks): Danh sách công việc chính thức có phân trang."""

    items: list[Task]
    next_cursor: str | None = None
