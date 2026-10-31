# Schema/DTO cho phương án chia bước và xếp lịch UF2 (PlanCandidate - O06, FeasibilityReport - O10)
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.common import SourceRevisions, StrictSchema, TimeInterval


class SessionSegment(StrictSchema):
    """Đoạn làm việc (work) hoặc nghỉ ngắn (rest) bên trong một phiên tập trung."""

    order_index: int = Field(ge=0)
    kind: Literal["work", "rest"]
    duration_seconds: int = Field(gt=0)


class CandidatePart(StrictSchema):
    """Phần việc (PlanningPart) ở giai đoạn dự thảo Candidate dùng client_part_key."""

    client_part_key: str = Field(min_length=1)
    scope_description: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    decomposition_state: Literal["not_started", "partial", "complete"]
    scheduling_state: Literal["not_scheduled", "partial", "complete"]
    unexpanded_scope: str | None = None
    rough_estimate_seconds: int | None = Field(default=None, ge=0)
    estimate_source: str | None = None


class CandidateStep(StrictSchema):
    """Bước công việc (Step) ở giai đoạn dự thảo Candidate dùng client_step_key."""

    title: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(ge=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(gt=0)
    estimate_source: str = Field(min_length=1)
    status: str = Field(min_length=1)
    client_step_key: str = Field(min_length=1)
    client_part_key: str = Field(min_length=1)


class CandidateSession(StrictSchema):
    """Phiên làm việc dự kiến (Session) ở giai đoạn dự thảo Candidate dùng client_session_key."""

    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_text: str = Field(min_length=1)
    micro_task_seconds: int | None = Field(default=None, ge=0)
    work_seconds: int = Field(ge=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(ge=0)
    client_session_key: str = Field(min_length=1)
    client_step_key: str = Field(min_length=1)
    start_state: Literal["waiting", "not_started", "started"]
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def validate_session_durations(self) -> Self:
        # duration_seconds chỉ bằng work_seconds + rest_seconds, không cộng micro_task_seconds (T07)
        if self.duration_seconds != (self.work_seconds + self.rest_seconds):
            raise ValueError("duration_seconds phải bằng đúng work_seconds + rest_seconds.")
        return self


class VerificationScope(StrictSchema):
    """Phạm vi được kiểm tra tính khả thi trên Candidate (scope_type='candidate_parts')."""

    client_part_keys: list[str] = Field(min_length=1)
    planning_horizon: TimeInterval
    scope_type: Literal["candidate_parts"] = "candidate_parts"


class BlockingReason(StrictSchema):
    """Nguyên nhân chặn khiến phương án lịch chưa thể xác nhận."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class FeasibilityReport(StrictSchema):
    """O10 FeasibilityReport: Kết quả kiểm tra tính khả thi trước Deadline của phương án lịch."""

    status: str = Field(min_length=1)
    required_seconds: int | None = Field(default=None, ge=0)
    available_seconds: int | None = Field(default=None, ge=0)
    unscheduled_seconds: int | None = Field(default=None, ge=0)
    deficit_seconds: int | None = Field(default=None, ge=0)
    verification_scope: VerificationScope
    fully_scheduled: bool
    blocking_reasons: list[BlockingReason] = Field(default_factory=list)
    unestimated_part_ids: list[str] = Field(default_factory=list)
    unplanned_part_ids: list[str] = Field(default_factory=list)
    extension_candidate_at: datetime | None = None


class PlanCandidate(StrictSchema):
    """O06 PlanCandidate: Phương án chia bước và xếp lịch dự thảo ở UF2 (Dòng 9, 10, 11)."""

    candidate_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    source_summary_revision: int = Field(ge=1)
    review_revision: int = Field(ge=1)
    planning_parts: list[CandidatePart]
    steps: list[CandidateStep]
    sessions: list[CandidateSession]
    planning_horizon: TimeInterval
    source_revisions: SourceRevisions
    min_inter_session_gap_seconds: int = Field(ge=0)
    feasibility: FeasibilityReport
    stale: bool
    user_step_proposal: str | None = None
    source_planning_input_revision: int = Field(ge=1)


class CandidateCreateRequest(StrictSchema):
    """Dòng 9 trên API Sheet (POST /api/v1/drafts/{draft_id}/candidates): Đầu vào tạo phương án lịch UF2."""

    source_summary_revision: int = Field(ge=1)
    source_planning_input_revision: int = Field(ge=1)
    selected_part_keys: list[str] | None = Field(default=None, min_length=1)
    planning_horizon: TimeInterval | None = None


class CandidatePartEdit(StrictSchema):
    """Allowlist các trường được phép sửa trên một CandidatePart theo client_part_key."""

    client_part_key: str = Field(min_length=1)
    scope_description: str | None = Field(default=None, min_length=1)
    rough_estimate_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_has_editable_field(self) -> Self:
        if len(self.model_fields_set - {"client_part_key"}) == 0:
            raise ValueError(
                "Phải gửi ít nhất một trường cần sửa cho CandidatePart ngoài client_part_key."
            )
        return self


class CandidateStepEdit(StrictSchema):
    """Allowlist các trường được phép sửa trên một CandidateStep theo client_step_key (Dòng 11)."""

    client_step_key: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    expected_output: str | None = Field(default=None, min_length=1)
    estimated_base_seconds: int | None = Field(default=None, ge=0)
    order_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_has_editable_field(self) -> Self:
        if len(self.model_fields_set - {"client_step_key"}) == 0:
            raise ValueError(
                "Phải gửi ít nhất một trường cần sửa cho CandidateStep ngoài client_step_key."
            )
        return self


class CandidateSessionEdit(StrictSchema):
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


class CandidateEdits(StrictSchema):
    """Danh sách chỉnh sửa theo Allowlist trên Candidate (chặn client tự gửi feasibility/stale/revisions)."""

    planning_parts: list[CandidatePartEdit] | None = Field(default=None, min_length=1)
    steps: list[CandidateStepEdit] | None = Field(default=None, min_length=1)
    sessions: list[CandidateSessionEdit] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_non_empty_edits(self) -> Self:
        if not (self.planning_parts or self.steps or self.sessions):
            raise ValueError(
                "edits phải chứa ít nhất một thay đổi trong planning_parts, steps hoặc sessions."
            )
        return self


class CandidatePatchRequest(StrictSchema):
    """Dòng 11 trên API Sheet (PATCH /api/v1/candidates/{candidate_id}): Sửa phương án nháp UF2."""

    expected_review_revision: int = Field(ge=1)
    edits: CandidateEdits


from app.schemas.schedule import ScheduleVersion  # noqa: E402
from app.schemas.task import Task  # noqa: E402


class ConfirmCandidateRequest(StrictSchema):
    """Dòng 12 trên API Sheet (POST /api/v1/candidates/{candidate_id}/confirm): Đầu vào duyệt và lưu kế hoạch UF2."""

    expected_review_revision: int = Field(ge=1)
    expected_summary_revision: int = Field(ge=1)
    expected_planning_input_revision: int = Field(ge=1)
    expected_source_revisions: SourceRevisions


class KeyMapping(StrictSchema):
    """Bảng ánh xạ từ client_*_key lúc xem Candidate sang ID thật trên Server sau khi lưu."""

    planning_parts: dict[str, str]
    steps: dict[str, str]
    sessions: dict[str, str]


class ConfirmCandidateResult(StrictSchema):
    """Dòng 12 trên API Sheet: Kết quả trả về (201 Created) sau khi chốt kế hoạch UF2 nguyên tử."""

    app_save_state: str = Field(min_length=1)
    task: Task
    key_mapping: KeyMapping
    schedule_version: ScheduleVersion
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)



