"""Confirmed UF2 task hierarchy returned by the API."""

from datetime import datetime

from pydantic import Field, model_validator

from app.schemas.common import ApiModel, AvailabilityRef, SessionSegment, TimeConstraint
from app.schemas.draft import TaskSummary


class PlanningPart(ApiModel):
    planning_part_id: str
    task_id: str
    client_part_key: str
    scope_description: str
    order_index: int = Field(ge=0)
    decomposition_state: str
    scheduling_state: str
    unexpanded_scope: str | None
    rough_estimate_seconds: int | None = Field(gt=0)
    estimate_source: str | None


class Step(ApiModel):
    step_id: str
    task_id: str
    planning_part_id: str
    title: str
    expected_output: str
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(gt=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(ge=1)
    estimate_source: str
    status: str


class Session(ApiModel):
    session_id: str
    step_id: str
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_seconds: int | None = Field(ge=0)
    micro_task_text: str
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment]
    duration_seconds: int = Field(gt=0)
    schedule_revision: int = Field(ge=1)
    status: str
    calendar_event_link_id: str | None
    start_state: str
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def valid_durations(self):
        if self.scheduled_start.utcoffset() is None or self.scheduled_end.utcoffset() is None:
            raise ValueError("Giờ phiên phải có múi giờ")
        if self.duration_seconds != self.work_seconds + self.rest_seconds:
            raise ValueError("Thời lượng phiên không bằng làm chính cộng nghỉ")
        if sum(x.duration_seconds for x in self.segments if x.kind == "work") != self.work_seconds:
            raise ValueError("Các quãng làm chính không khớp")
        if sum(x.duration_seconds for x in self.segments if x.kind == "rest") != self.rest_seconds:
            raise ValueError("Các quãng nghỉ không khớp")
        if (self.scheduled_end - self.scheduled_start).total_seconds() != self.duration_seconds:
            raise ValueError("Giờ kết thúc không khớp thời lượng phiên")
        if [x.order_index for x in self.segments] != list(range(len(self.segments))):
            raise ValueError("Thứ tự các quãng không liên tục")
        return self


class Task(ApiModel):
    task_id: str
    title: str
    description: str
    scope: str
    extra_requirements: list[str]
    time_constraint: TimeConstraint
    importance: int | None = Field(ge=1)
    status: str
    source_draft_id: str
    decomposition_state: str
    scheduling_state: str
    availability_ref: AvailabilityRef
    planning_parts: list[PlanningPart]
    steps: list[Step]
    sessions: list[Session]
    source_summary: TaskSummary
