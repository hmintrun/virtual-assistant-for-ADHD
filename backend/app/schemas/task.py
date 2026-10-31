# Pydantic v2 schemas cho Task, PlanningPart và Step chính thức sau khi xác nhận UF2 (Dòng 12, 13, 14)
from typing import Literal

from pydantic import Field

from app.schemas.common import AvailabilityRef, StrictSchema
from app.schemas.draft import TaskSummary, TimeConstraint
from app.schemas.schedule import Session


class PlanningPart(StrictSchema):
    """O07 PlanningPart: Phần việc chính thức thuộc một Task (gồm cả phần đã chia và phần chưa chia trong Rolling Horizon)."""

    planning_part_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    client_part_key: str = Field(min_length=1)
    scope_description: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    decomposition_state: Literal["not_started", "partial", "complete"]
    scheduling_state: Literal["not_scheduled", "partial", "complete"]
    unexpanded_scope: str | None = None
    rough_estimate_seconds: int | None = Field(default=None, ge=0)
    estimate_source: str | None = None


class Step(StrictSchema):
    """O08 Step: Bước công việc chính thức thuộc một Task và một PlanningPart."""

    step_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    planning_part_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(ge=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(gt=0)
    estimate_source: str = Field(min_length=1)
    status: str = Field(min_length=1)


class Task(StrictSchema):
    """O02 Task: Công việc chính thức đã được xác nhận ở cuối UF2 (Dòng 12, 13, 14)."""

    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint
    importance: int | None = Field(default=None, gt=0)
    status: str = Field(min_length=1)
    source_draft_id: str = Field(min_length=1)
    decomposition_state: Literal["not_started", "partial", "complete"]
    scheduling_state: Literal["not_scheduled", "partial", "complete"]
    availability_ref: AvailabilityRef
    planning_parts: list[PlanningPart]
    steps: list[Step]
    sessions: list[Session]
    source_summary: TaskSummary


class TaskListData(StrictSchema):
    """Dòng 13 trên API Sheet (GET /api/v1/tasks): Danh sách công việc chính thức có phân trang."""

    items: list[Task]
    next_cursor: str | None = None

