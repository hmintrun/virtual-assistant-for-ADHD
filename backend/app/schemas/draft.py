"""UF1 draft and single-round summary DTOs."""

from pydantic import Field

from app.schemas.common import (
    ApiModel,
    AvailabilityRef,
    CandidateRef,
    ClarificationIssue,
    FieldSource,
    TimeConstraint,
)


class TaskSummary(ApiModel):
    title: str | None
    description: str | None
    goal: str | None
    scope: str | None
    extra_requirements: list[str]
    time_constraint: TimeConstraint | None
    availability_ref: AvailabilityRef | None
    importance: int | None = Field(ge=1)
    clarification_issues: list[ClarificationIssue]
    can_plan: bool
    field_sources: dict[str, FieldSource]


class PlanningDraft(ApiModel):
    draft_id: str
    client_draft_id: str
    stage: str
    summary: TaskSummary
    summary_revision: int = Field(ge=0)
    planning_input_revision: int = Field(ge=0)
    summary_confirmed_revision: int | None = Field(ge=0)
    candidate_ref: CandidateRef | None
    source_thought_id: str | None
    confirmed_task_id: str | None
    raw_input: str | None
    input_source: str
    user_step_proposal: str | None


class ConfirmSummaryResult(ApiModel):
    draft_id: str
    stage: str
    summary_confirmed_revision: int = Field(ge=0)
    summary_revision: int = Field(ge=0)
    planning_input_revision: int = Field(ge=0)


class CreateDraftRequest(ApiModel):
    client_draft_id: str = Field(min_length=1)
    input_source: str = Field(min_length=1)
    raw_input: str | None


class DraftChanges(ApiModel):
    summary: dict[str, object] | None = None
    raw_input: str | None = None
    user_step_proposal: str | None = None


class UpdateDraftRequest(ApiModel):
    expected_summary_revision: int = Field(ge=0)
    expected_planning_input_revision: int = Field(ge=0)
    changes: DraftChanges


class ConfirmSummaryRequest(ApiModel):
    expected_summary_revision: int = Field(ge=0)
