# Schema/DTO cho bản nháp công việc (PlanningDraft - O05), bảng tóm tắt (TaskSummary - O04) và ràng buộc thời gian (TimeConstraint - O03)
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.common import AvailabilityRef, CandidateRef, StrictSchema


class TimeConstraint(StrictSchema):
    """O03 TimeConstraint: Phân biệt rõ 2 nhánh 'deadline' và 'window' theo hợp đồng Sheet."""

    kind: Literal["deadline", "window"]
    start_at: datetime
    finish_by_at: datetime | None = None
    deadline_at: datetime | None = None
    input_timezone: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_branch_and_order(self) -> Self:
        if self.kind == "deadline":
            if self.deadline_at is None or self.finish_by_at is not None:
                raise ValueError(
                    "Khi kind='deadline', deadline_at bắt buộc có giá trị và finish_by_at phải là null."
                )
            if self.start_at >= self.deadline_at:
                raise ValueError("start_at phải đứng trước deadline_at.")
        else:
            if self.finish_by_at is None or self.deadline_at is not None:
                raise ValueError(
                    "Khi kind='window', finish_by_at bắt buộc có giá trị và deadline_at phải là null."
                )
            if self.start_at >= self.finish_by_at:
                raise ValueError("start_at phải đứng trước finish_by_at.")
        return self


class ClarificationIssue(StrictSchema):
    """Câu hỏi làm rõ cho những trường còn thiếu/mơ hồ trong bảng tóm tắt UF1."""

    code: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    question: str = Field(min_length=1)
    blocking: bool


class FieldSource(StrictSchema):
    """Nguồn gốc của từng trường dữ liệu trong bảng tóm tắt (user_input, user_confirmed, ai_suggested...)."""

    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class TaskSummary(StrictSchema):
    """O04 TaskSummary: Bảng tóm tắt công việc để người dùng kiểm tra ở UF1."""

    title: str | None = None
    description: str | None = None
    goal: str | None = None
    scope: str | None = None
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint | None = None
    availability_ref: AvailabilityRef | None = None
    importance: int | None = Field(default=None, gt=0)
    clarification_issues: list[ClarificationIssue] = Field(default_factory=list)
    can_plan: bool
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)


class SummaryInput(StrictSchema):
    """Dữ liệu tóm tắt do client gửi lên khi tạo/sửa nháp (không cho phép client tự gửi can_plan/clarification_issues/field_sources)."""

    title: str | None = None
    description: str | None = None
    goal: str | None = None
    scope: str | None = None
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint | None = None
    availability_ref: AvailabilityRef | None = None
    importance: int | None = Field(default=None, gt=0)


class DraftCreateRequest(StrictSchema):
    """Dòng 2 trên API Sheet (POST /api/v1/drafts): Đầu vào tạo bản nháp mới."""

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


class SummaryChanges(StrictSchema):
    """Các trường tóm tắt được phép sửa một phần trong PATCH /drafts/{draft_id} (omitted giữ nguyên)."""

    title: str | None = None
    description: str | None = None
    goal: str | None = None
    scope: str | None = None
    extra_requirements: list[str] | None = None
    time_constraint: TimeConstraint | None = None
    availability_ref: AvailabilityRef | None = None
    importance: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_non_empty_summary_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Đối tượng summary trong changes phải có ít nhất một trường được gửi.")
        return self


class DraftChanges(StrictSchema):
    """Các nhóm trường được phép sửa trong PATCH /drafts/{draft_id}."""

    raw_input: str | None = Field(default=None, min_length=1)
    summary: SummaryChanges | None = None
    user_step_proposal: str | None = None

    @model_validator(mode="after")
    def validate_at_least_one_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("changes phải chứa ít nhất một trường (raw_input, summary, hoặc user_step_proposal).")
        if "raw_input" in self.model_fields_set and self.raw_input is None:
            raise ValueError("raw_input không được xóa thành null.")
        if "summary" in self.model_fields_set and self.summary is None:
            raise ValueError("summary không được xóa thành null.")
        return self


class DraftPatchRequest(StrictSchema):
    """Dòng 6 trên API Sheet (PATCH /api/v1/drafts/{draft_id}): Sửa một phần bản nháp."""

    expected_planning_input_revision: int = Field(ge=1)
    expected_summary_revision: int | None = Field(default=None, ge=1)
    changes: DraftChanges

    @model_validator(mode="after")
    def validate_expected_summary_revision_when_needed(self) -> Self:
        touches_raw_or_summary = bool(
            {"raw_input", "summary"} & self.changes.model_fields_set
        )
        if touches_raw_or_summary and self.expected_summary_revision is None:
            raise ValueError(
                "expected_summary_revision là bắt buộc khi sửa raw_input hoặc summary."
            )
        return self



class PlanningDraft(StrictSchema):
    """O05 PlanningDraft: Bản nháp công việc đầy đủ trả về ở Dòng 2, 4, 5, 6."""

    draft_id: str = Field(min_length=1)
    client_draft_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    raw_input: str | None = None
    input_source: Literal["text", "voice", "thought"]
    summary: TaskSummary
    summary_revision: int = Field(ge=0)
    summary_confirmed_revision: int | None = Field(default=None, ge=1)
    candidate_ref: CandidateRef | None = None
    source_thought_id: str | None = None
    confirmed_task_id: str | None = None
    user_step_proposal: str | None = None
    planning_input_revision: int = Field(ge=0)


class DraftListData(StrictSchema):
    """Dòng 3 trên API Sheet (GET /api/v1/drafts): Danh sách bản nháp có phân trang."""

    items: list[PlanningDraft]
    next_cursor: str | None = None


class ConfirmSummaryRequest(StrictSchema):
    """Dòng 7 trên API Sheet (POST /api/v1/drafts/{draft_id}/confirm-summary): Đầu vào xác nhận bảng tóm tắt UF1."""

    expected_summary_revision: int = Field(ge=1)


class ConfirmSummaryResult(StrictSchema):
    """Dòng 7 trên API Sheet: Biên nhận rút gọn sau khi người dùng xác nhận bảng tóm tắt UF1 (chưa tạo Task)."""

    draft_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    summary_confirmed_revision: int = Field(ge=1)
    summary_revision: int = Field(ge=1)
    planning_input_revision: int = Field(ge=1)


class DraftAnalyzeRequest(StrictSchema):
    """Dòng 5 trên API Sheet (POST /api/v1/drafts/{draft_id}/analyze): Yêu cầu AI phân tích nội dung mới hoặc tin nhắn bổ sung."""

    expected_summary_revision: int = Field(ge=1)
    expected_planning_input_revision: int = Field(ge=1)
    raw_input: str | None = Field(default=None, min_length=1)
    message: str | None = Field(default=None, min_length=1)
    source_thought_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_raw_input_or_message(self) -> Self:
        if self.raw_input is None and self.message is None:
            raise ValueError("Bắt buộc phải cung cấp ít nhất raw_input mới hoặc message bổ sung.")
        return self




