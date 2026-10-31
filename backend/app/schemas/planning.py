"""UF2 candidate, feasibility and confirmation DTOs."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import (
    ApiModel,
    BlockingReason,
    SessionSegment,
    SourceRevisions,
    TimeInterval,
)
from app.schemas.task import Task


class CandidatePart(ApiModel):
    client_part_key: str
    scope_description: str
    order_index: int = Field(ge=0)
    decomposition_state: str
    scheduling_state: str
    unexpanded_scope: str | None
    rough_estimate_seconds: int | None = Field(gt=0)
    estimate_source: str | None


class CandidateStep(ApiModel):
    client_step_key: str
    client_part_key: str
    title: str
    expected_output: str
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(gt=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(ge=1)
    estimate_source: str
    status: str


class CandidateSession(ApiModel):
    client_session_key: str
    client_step_key: str
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_text: str
    micro_task_seconds: int | None = Field(ge=0)
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment]
    duration_seconds: int = Field(gt=0)
    start_state: str
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def valid_duration(self):
        if self.scheduled_start.utcoffset() is None or self.scheduled_end.utcoffset() is None:
            raise ValueError("Giờ phiên phải có múi giờ")
        if self.duration_seconds != self.work_seconds + self.rest_seconds:
            raise ValueError("Thời lượng phiên không khớp")
        if sum(x.duration_seconds for x in self.segments if x.kind == "work") != self.work_seconds:
            raise ValueError("Quãng làm chính không khớp")
        if sum(x.duration_seconds for x in self.segments if x.kind == "rest") != self.rest_seconds:
            raise ValueError("Quãng nghỉ không khớp")
        if (self.scheduled_end - self.scheduled_start).total_seconds() != self.duration_seconds:
            raise ValueError("Giờ kết thúc không khớp")
        return self


class CandidateVerificationScope(ApiModel):
    scope_type: Literal["candidate_parts"]
    client_part_keys: list[str]
    planning_horizon: TimeInterval


class PersistedVerificationScope(ApiModel):
    scope_type: Literal["persisted_schedule"]
    task_ids: list[str]
    session_ids: list[str]
    planning_part_ids: list[str]
    planning_horizon: TimeInterval


class FeasibilityReport(ApiModel):
    status: str
    required_seconds: int | None = Field(ge=0)
    available_seconds: int | None = Field(ge=0)
    unscheduled_seconds: int | None = Field(ge=0)
    verification_scope: CandidateVerificationScope | PersistedVerificationScope = Field(
        discriminator="scope_type"
    )
    fully_scheduled: bool
    blocking_reasons: list[BlockingReason]
    deficit_seconds: int | None = Field(ge=0)
    unestimated_part_ids: list[str]
    unplanned_part_ids: list[str]
    extension_candidate_at: datetime | None


class PlanCandidate(ApiModel):
    candidate_id: str
    draft_id: str
    source_summary_revision: int = Field(ge=0)
    source_planning_input_revision: int = Field(ge=0)
    review_revision: int = Field(ge=0)
    planning_parts: list[CandidatePart]
    steps: list[CandidateStep]
    sessions: list[CandidateSession]
    planning_horizon: TimeInterval
    source_revisions: SourceRevisions
    min_inter_session_gap_seconds: int = Field(ge=900)
    feasibility: FeasibilityReport
    stale: bool
    user_step_proposal: str | None

    @model_validator(mode="after")
    def valid_client_keys(self):
        parts = {x.client_part_key for x in self.planning_parts}
        steps = {x.client_step_key for x in self.steps}
        sessions = {x.client_session_key for x in self.sessions}
        if len(parts) != len(self.planning_parts) or len(steps) != len(self.steps):
            raise ValueError("Client key phần hoặc bước bị trùng")
        if len(sessions) != len(self.sessions):
            raise ValueError("Client key phiên bị trùng")
        if any(x.client_part_key not in parts for x in self.steps):
            raise ValueError("Bước tham chiếu phần không có trong phương án")
        if any(x.client_step_key not in steps for x in self.sessions):
            raise ValueError("Phiên tham chiếu bước không có trong phương án")
        return self


class ScheduleVersion(ApiModel):
    version_number: int = Field(ge=1)
    base_version: int | None = Field(ge=1)
    approved_at: datetime
    affected_task_ids: list[str]
    min_inter_session_gap_seconds: int = Field(ge=900)
    alarm_sync_state: str
    calendar_sync_summary: str


class KeyMapping(ApiModel):
    planning_parts: dict[str, str]
    steps: dict[str, str]
    sessions: dict[str, str]


class ConfirmCandidateResult(ApiModel):
    app_save_state: str
    task: Task
    key_mapping: KeyMapping
    schedule_version: ScheduleVersion
    alarm_sync_state: str
    calendar_sync_summary: str


class GenerateCandidateRequest(ApiModel):
    source_summary_revision: int = Field(ge=0)
    source_planning_input_revision: int = Field(ge=0)


class CandidateEdits(ApiModel):
    planning_parts: list[dict[str, object]] | None = None
    steps: list[dict[str, object]] | None = None
    sessions: list[dict[str, object]] | None = None


class UpdateCandidateRequest(ApiModel):
    expected_review_revision: int = Field(ge=0)
    edits: CandidateEdits


class ConfirmCandidateRequest(ApiModel):
    expected_review_revision: int = Field(ge=0)
    expected_summary_revision: int = Field(ge=0)
    expected_planning_input_revision: int = Field(ge=0)
    expected_source_revisions: SourceRevisions
