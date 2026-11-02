"""UF1 draft and single-round summary DTOs."""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from app.schemas.availability import DraftAvailability
from app.schemas.common import (
    ApiModel,
    AvailabilityRef,
    CandidateRef,
    ClarificationIssue,
    FieldSource,
    TimeConstraint,
)


class TaskSummary(ApiModel):
    title: str | None = None
    description: str | None = None
    goal: str | None = None
    scope: str | None = None
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint | None = None
    availability: DraftAvailability | None = None
    availability_ref: AvailabilityRef | None = None
    importance: int | None = Field(default=None, ge=1)
    clarification_issues: list[ClarificationIssue] = Field(default_factory=list)
    can_plan: bool
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)


class PlanningDraft(ApiModel):
    ai_context: dict[str, Any] = Field(default_factory=dict, exclude=True)
    draft_id: str = Field(min_length=1)
    client_draft_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    summary: TaskSummary
    summary_revision: int = Field(ge=0)
    planning_input_revision: int = Field(ge=0)
    summary_confirmed_revision: int | None = Field(default=None, ge=0)
    candidate_ref: CandidateRef | None = None
    source_thought_id: str | None = None
    confirmed_task_id: str | None = None
    raw_input: str | None = None
    input_source: Literal["text", "voice", "thought"]
    user_step_proposal: str | None = None


class DraftListData(ApiModel):
    """Dòng 3 trên API Sheet (GET /api/v1/drafts): Danh sách bản nháp có phân trang."""

    items: list[PlanningDraft]
    next_cursor: str | None = None


class ConfirmSummaryResult(ApiModel):
    draft_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    summary_confirmed_revision: int = Field(ge=0)
    summary_revision: int = Field(ge=0)
    planning_input_revision: int = Field(ge=0)


class SummaryInput(ApiModel):
    """Dữ liệu tóm tắt do client gửi lên khi tạo/sửa nháp (chặn client tự gửi can_plan/clarification_issues/field_sources)."""

    title: str | None = None
    description: str | None = None
    goal: str | None = None
    scope: str | None = None
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint | None = None
    availability: DraftAvailability | None = None
    availability_ref: AvailabilityRef | None = None
    importance: int | None = Field(default=None, ge=1)


class CreateDraftRequest(ApiModel):
    client_draft_id: str = Field(min_length=1)
    input_source: Literal["text", "voice", "thought"]
    raw_input: str | None = Field(default=None, min_length=1)
    summary: SummaryInput | None = None
    source_thought_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_raw_input_or_summary(self) -> Self:
        if self.raw_input is None and self.summary is None:
            raise ValueError("Bắt buộc phải cung cấp ít nhất raw_input hoặc summary.")
        return self


class SummaryChanges(ApiModel):
    """Các trường tóm tắt được phép sửa một phần trong PATCH /drafts/{draft_id} (omitted giữ nguyên)."""

    title: str | None = None
    description: str | None = None
    goal: str | None = None
    scope: str | None = None
    extra_requirements: list[str] | None = None
    time_constraint: TimeConstraint | None = None
    availability: DraftAvailability | None = None
    availability_ref: AvailabilityRef | None = None
    importance: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_non_empty_summary_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Đối tượng summary trong changes phải có ít nhất một trường được gửi.")
        return self


class DraftChanges(ApiModel):
    summary: SummaryChanges | None = None
    raw_input: str | None = Field(default=None, min_length=1)
    user_step_proposal: str | None = None

    @model_validator(mode="after")
    def validate_at_least_one_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError(
                "changes phải chứa ít nhất một trường (raw_input, summary, hoặc user_step_proposal)."
            )
        if "raw_input" in self.model_fields_set and self.raw_input is None:
            raise ValueError("raw_input không được xóa thành null.")
        if "summary" in self.model_fields_set and self.summary is None:
            raise ValueError("summary không được xóa thành null.")
        return self


class UpdateDraftRequest(ApiModel):
    expected_summary_revision: int | None = Field(default=None, ge=0)
    expected_planning_input_revision: int = Field(ge=0)
    changes: DraftChanges

    @model_validator(mode="after")
    def validate_expected_summary_revision_when_needed(self) -> Self:
        touches_raw_or_summary = bool({"raw_input", "summary"} & self.changes.model_fields_set)
        if touches_raw_or_summary and self.expected_summary_revision is None:
            raise ValueError(
                "expected_summary_revision là bắt buộc khi sửa raw_input hoặc summary."
            )
        return self


class ConfirmSummaryRequest(ApiModel):
    expected_summary_revision: int = Field(ge=0)


class DraftAnalyzeRequest(ApiModel):
    """Dòng 5 trên API Sheet (POST /api/v1/drafts/{draft_id}/analyze): Yêu cầu AI phân tích nội dung mới hoặc tin nhắn bổ sung."""

    expected_summary_revision: int = Field(ge=0)
    expected_planning_input_revision: int = Field(ge=0)
    raw_input: str | None = Field(default=None, min_length=1)
    message: str | None = Field(default=None, min_length=1)
    source_thought_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_raw_input_or_message(self) -> Self:
        if self.raw_input is None and self.message is None:
            raise ValueError("Bắt buộc phải cung cấp ít nhất raw_input mới hoặc message bổ sung.")
        return self


# Giữ bí danh tương thích
DraftCreateRequest = CreateDraftRequest
DraftPatchRequest = UpdateDraftRequest
