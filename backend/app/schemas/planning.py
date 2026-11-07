"""UF2 candidate, feasibility and confirmation DTOs."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.common import (
    ApiModel,
    BlockingReason,
    CalendarContextRef,
    SessionSegment,
    SourceRevisions,
    TimeInterval,
)
from app.schemas.task import Task


class CandidateStep(ApiModel):
    client_step_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(gt=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(ge=1)
    estimate_source: str = Field(min_length=1)
    status: str = Field(min_length=1)


class CandidateSession(ApiModel):
    client_session_key: str = Field(min_length=1)
    client_step_key: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_text: str = Field(min_length=1)
    micro_task_seconds: int | None = Field(default=None, ge=0)
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    start_state: Literal["waiting", "not_started", "started"]
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
    scope_type: Literal["candidate_steps"] = "candidate_steps"
    client_step_keys: list[str] = Field(min_length=1)
    planning_horizon: TimeInterval


class PersistedVerificationScope(ApiModel):
    scope_type: Literal["persisted_schedule"] = "persisted_schedule"
    task_ids: list[str]
    session_ids: list[str]
    step_ids: list[str] = Field(default_factory=list)
    planning_horizon: TimeInterval


# Giữ bí danh tương thích cho CandidateVerificationScope
VerificationScope = CandidateVerificationScope


class FeasibilityReport(ApiModel):
    status: str = Field(min_length=1)
    required_seconds: int | None = Field(default=None, ge=0)
    available_seconds: int | None = Field(default=None, ge=0)
    unscheduled_seconds: int | None = Field(default=None, ge=0)
    verification_scope: CandidateVerificationScope | PersistedVerificationScope = Field(
        discriminator="scope_type"
    )
    fully_scheduled: bool
    blocking_reasons: list[BlockingReason] = Field(default_factory=list)
    deficit_seconds: int | None = Field(default=None, ge=0)
    unscheduled_step_keys: list[str] = Field(default_factory=list)
    unexpanded_scope: str | None = None
    extension_candidate_at: datetime | None = None


class PlanCandidate(ApiModel):
    candidate_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    source_summary_revision: int = Field(ge=0)
    source_planning_input_revision: int = Field(ge=0)
    review_revision: int = Field(ge=0)
    decomposition_state: Literal["partial", "complete"] = "complete"
    unexpanded_scope: str | None = None
    steps: list[CandidateStep]
    sessions: list[CandidateSession]
    planning_horizon: TimeInterval
    source_revisions: SourceRevisions
    min_inter_session_gap_seconds: int = Field(ge=900)
    feasibility: FeasibilityReport
    stale: bool
    user_step_proposal: str | None = None

    @model_validator(mode="after")
    def valid_client_keys(self):
        steps = {x.client_step_key for x in self.steps}
        sessions = {x.client_session_key for x in self.sessions}
        if len(steps) != len(self.steps):
            raise ValueError("Client key bước bị trùng")
        if len(sessions) != len(self.sessions):
            raise ValueError("Client key phiên bị trùng")
        if len({step.order_index for step in self.steps}) != len(self.steps):
            raise ValueError("Thứ tự bước bị trùng")
        if self.unexpanded_scope and self.unexpanded_scope.strip():
            self.decomposition_state = "partial"
        elif self.decomposition_state == "partial":
            raise ValueError("Cần mô tả phạm vi chưa chia")
        if any(x.client_step_key not in steps for x in self.sessions):
            raise ValueError("Phiên tham chiếu bước không có trong phương án")
        return self


class ScheduleVersion(ApiModel):
    version_number: int = Field(ge=1)
    base_version: int | None = Field(default=None, ge=1)
    approved_at: datetime
    affected_task_ids: list[str]
    min_inter_session_gap_seconds: int = Field(ge=900)
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)


class KeyMapping(ApiModel):
    steps: dict[str, str]
    sessions: dict[str, str]


class ConfirmCandidateResult(ApiModel):
    app_save_state: str = Field(min_length=1)
    task: Task
    key_mapping: KeyMapping
    schedule_version: ScheduleVersion
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)


class ManualStepInput(ApiModel):
    """Bước do user nhập khi chưa có Candidate; thời lượng gốc, chưa buffer."""

    order_index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=255)
    expected_output: str = Field(min_length=1)
    estimated_base_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_content(self) -> Self:
        self.title, self.expected_output = self.title.strip(), self.expected_output.strip()
        if not self.title or not self.expected_output:
            raise ValueError("Bước thủ công cần tiêu đề và đầu ra rõ ràng")
        return self


class GenerateCandidateRequest(ApiModel):
    source_summary_revision: int = Field(ge=0)
    source_planning_input_revision: int = Field(ge=0)
    selected_step_keys: list[str] | None = Field(default=None, min_length=1)
    planning_horizon: TimeInterval | None = None
    calendar_context_ref: CalendarContextRef | None = None
    manual_steps: list[ManualStepInput] | None = Field(default=None, min_length=1)
    unexpanded_scope: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_empty_legacy_selection(cls, value):
        # Client trước C01 luôn gửi khóa này với null. Không ánh xạ Part sang
        # Step theo vị trí vì có thể chọn nhầm phạm vi công việc.
        if isinstance(value, dict) and "selected_part_keys" in value:
            if value["selected_part_keys"] is not None:
                raise ValueError("Client cũ cần đổi lựa chọn Part thành selected_step_keys của C01")
            value = dict(value)
            value.pop("selected_part_keys")
        return value

    @model_validator(mode="after")
    def valid_manual_source(self) -> Self:
        if self.manual_steps is None:
            if self.unexpanded_scope is not None:
                raise ValueError("unexpanded_scope chỉ gửi cùng manual_steps")
            return self
        if self.selected_step_keys is not None:
            raise ValueError("Không chọn Steps nguồn cùng manual_steps")
        orders = [step.order_index for step in self.manual_steps]
        if sorted(orders) != list(range(len(orders))):
            raise ValueError("Thứ tự bước thủ công phải liên tục từ 0 và không trùng")
        if self.unexpanded_scope is not None:
            self.unexpanded_scope = self.unexpanded_scope.strip() or None
        return self


class CandidateStepEdit(ApiModel):
    """Allowlist các trường được phép sửa trên một CandidateStep theo client_step_key (Dòng 11)."""

    client_step_key: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    expected_output: str | None = Field(default=None, min_length=1)
    estimated_base_seconds: int | None = Field(default=None, gt=0)
    order_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_has_editable_field(self) -> Self:
        if len(self.model_fields_set - {"client_step_key"}) == 0:
            raise ValueError(
                "Phải gửi ít nhất một trường cần sửa cho CandidateStep ngoài client_step_key."
            )
        return self


class CandidateSessionEdit(ApiModel):
    """Allowlist các trường được phép sửa trên một CandidateSession theo client_session_key."""

    client_session_key: str = Field(min_length=1)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    micro_task_text: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_has_editable_field(self) -> Self:
        if len(self.model_fields_set - {"client_session_key"}) == 0:
            raise ValueError(
                "Phải gửi ít nhất một trường cần sửa cho CandidateSession ngoài client_session_key."
            )
        return self


class CandidateEdits(ApiModel):
    steps: list[CandidateStepEdit] | None = Field(default=None, min_length=1)
    sessions: list[CandidateSessionEdit] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_non_empty_edits(self) -> Self:
        if not (self.steps or self.sessions):
            raise ValueError("edits phải chứa ít nhất một thay đổi trong steps hoặc sessions.")
        return self


class UpdateCandidateRequest(ApiModel):
    expected_review_revision: int = Field(ge=0)
    edits: CandidateEdits | None = None
    user_instruction: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def one_edit_source(self) -> Self:
        if (self.edits is None) == (self.user_instruction is None):
            raise ValueError("Chọn đúng một nguồn: edits hoặc user_instruction")
        if self.user_instruction is not None:
            self.user_instruction = self.user_instruction.strip()
            if not self.user_instruction:
                raise ValueError("Cần nội dung yêu cầu điều chỉnh")
        return self


class ConfirmCandidateRequest(ApiModel):
    expected_review_revision: int = Field(ge=0)
    expected_summary_revision: int = Field(ge=0)
    expected_planning_input_revision: int = Field(ge=0)
    expected_source_revisions: SourceRevisions


# Giữ bí danh tương thích
CandidateCreateRequest = GenerateCandidateRequest
CandidatePatchRequest = UpdateCandidateRequest
