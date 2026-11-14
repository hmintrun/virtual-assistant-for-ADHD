"""Pydantic contracts for UF6 reschedule proposals."""

from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.common import ApiModel, SourceRevisions
from app.schemas.planning import FeasibilityReport
from app.schemas.schedule import RescheduleTriggerReason


class CreateProposalRequest(ApiModel):
    trigger_reason: RescheduleTriggerReason
    target_task_id: str = Field(min_length=1)
    allowed_task_ids: list[str] = Field(min_length=1)
    base_schedule_revision: int = Field(ge=0)
    source_revisions: SourceRevisions
    request_text: str | None = None
    expected_current_proposal_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "CreateProposalRequest":
        if len(set(self.allowed_task_ids)) != len(self.allowed_task_ids):
            raise ValueError("allowed_task_ids không được trùng")
        return self


class RebuildProposalRequest(ApiModel):
    expected_proposal_revision: int = Field(ge=0)
    request_text: str | None = Field(default=None, max_length=2000)


class ApproveProposalRequest(ApiModel):
    expected_proposal_revision: int = Field(ge=0)
    expected_base_schedule_revision: int = Field(ge=0)
    expected_source_revisions: SourceRevisions


class RejectProposalRequest(ApiModel):
    expected_proposal_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class RescheduleProposalData(ApiModel):
    proposal_id: str = Field(min_length=1)
    trigger_reason: RescheduleTriggerReason
    target_task_id: str = Field(min_length=1)
    allowed_task_ids: list[str]
    affected_task_ids: list[str]
    base_schedule_revision: int = Field(ge=0)
    source_revisions: SourceRevisions
    proposed_changes: dict[str, Any]
    feasibility: FeasibilityReport
    explanation: str
    status: Literal["pending_approval", "approved", "rejected", "superseded"]
    proposal_revision: int = Field(ge=0)
    stale: bool
    supersedes_proposal_id: str | None = None
    pending_notification_state: str | None = None
    pending_notification_sent_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_allowed_impact(self) -> "RescheduleProposalData":
        if not set(self.affected_task_ids).issubset(self.allowed_task_ids):
            raise ValueError("affected_task_ids phải nằm trong allowed_task_ids")
        return self
