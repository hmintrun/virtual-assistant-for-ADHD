# Schema/DTO cho cài đặt trợ lý (UserPreference - Dòng 39), tái xuất Availability (Dòng 40), ChangeRequests (Dòng 41-42) và Suggestions (Dòng 43-44)
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.schemas.availability import (
    Availability,
    DateOverride,
    DraftAvailability,
    LocalInterval,
    TaskAvailabilityLimit,
    WeeklyRule,
)
from app.schemas.common import ApiModel

ReminderOffsets = Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1, max_length=3)]
SuggestionKind = Literal["reminder_offset", "buffer_multiplier"]
SuggestionStatus = Literal["pending", "accepted", "rejected", "superseded"]
SuggestionAction = Literal["reject", "accept", "edit"]
SuggestionApplicationState = Literal[
    "not_applied",
    "effective_from_next_session",
    "pending_review",
]
AnnoyanceFeedback = Literal["too_frequent", "not_helpful", "interrupted_me"]


class PreferenceChanges(ApiModel):
    """Các thay đổi cài đặt đang chờ duyệt (nếu ảnh hưởng lịch tương lai)."""

    work_interval_seconds: int | None = Field(default=None, ge=0)
    rest_interval_seconds: int | None = Field(default=None, ge=0)
    reminder_offsets: ReminderOffsets | None = None
    buffer_multiplier: float | None = Field(default=None, gt=0)
    reminder_style: str | None = None

    @model_validator(mode="after")
    def distinct_offsets(self):
        if self.reminder_offsets is not None and len(set(self.reminder_offsets)) != len(
            self.reminder_offsets
        ):
            raise ValueError("Các mốc nhắc không được trùng nhau")
        return self


class UserPreference(ApiModel):
    """Cấu hình trợ lý có hiệu lực và yêu cầu đang chờ duyệt (Dòng 39 - GET /api/v1/preferences)."""

    revision: int = Field(ge=0)
    effective_revision: int = Field(ge=0)
    requested_revision: int | None = Field(default=None, ge=1)
    work_interval_seconds: int = Field(ge=0)
    rest_interval_seconds: int = Field(ge=0)
    micro_task_seconds: int | None = Field(default=None, ge=0)
    reminder_offsets: ReminderOffsets
    buffer_multiplier: float = Field(gt=0)
    reminder_style: str = Field(min_length=1)
    requested_changes: PreferenceChanges | None = None
    pending_proposal_id: str | None = None


# Giữ bí danh tương thích với tên cũ
LocalTimeInterval = LocalInterval

__all__ = [
    "AdaptationSuggestionData",
    "AnnoyanceFeedback",
    "Availability",
    "CancelSettingsChangeRequest",
    "CancelSettingsChangeResult",
    "DateOverride",
    "LocalInterval",
    "LocalTimeInterval",
    "PreferenceChanges",
    "SettingsChangeRequest",
    "SettingsChangeResult",
    "SettingsChanges",
    "SuggestionAction",
    "SuggestionApplicationState",
    "SuggestionKind",
    "SuggestionResponse",
    "SuggestionResponseRequest",
    "SuggestionResponseResult",
    "SuggestionStatus",
    "TaskAvailabilityLimit",
    "UserPreference",
    "WeeklyRule",
]


class AvailabilityChanges(ApiModel):
    timezone: str | None = None
    weekly_rules: list[WeeklyRule] | None = None
    date_overrides: list[DateOverride] | None = None
    task_limits: list[TaskAvailabilityLimit] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, k) is None for k in self.model_fields_set
        ):
            raise ValueError(
                "Gửi ít nhất một trường giờ rảnh hợp lệ; không dùng null để xóa quy tắc"
            )
        return self


class SettingsChanges(ApiModel):
    availability: DraftAvailability | None = None
    preferences: PreferenceChanges | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, k) is None for k in self.model_fields_set
        ):
            raise ValueError("Cần ít nhất một nhóm thay đổi không rỗng")
        if self.preferences is not None and (
            not self.preferences.model_fields_set
            or any(getattr(self.preferences, k) is None for k in self.preferences.model_fields_set)
        ):
            raise ValueError("preferences không được rỗng hoặc có null")
        return self


class SettingsChangeRequest(ApiModel):
    expected_preferences_revision: int | None = Field(default=None, ge=0)
    expected_availability_revision: int | None = Field(default=None, ge=0)
    target_draft_id: str | None = Field(default=None, min_length=1)
    target_task_id: str | None = Field(default=None, min_length=1)
    expected_summary_revision: int | None = Field(default=None, ge=1)
    expected_planning_input_revision: int | None = Field(default=None, ge=1)
    expected_task_revision: int | None = Field(default=None, ge=1)
    changes: SettingsChanges

    @model_validator(mode="after")
    def required_revisions(self):
        if self.changes.availability is not None:
            if (self.target_draft_id is None) == (self.target_task_id is None):
                raise ValueError("Chỉnh giờ cần chọn đúng một Draft hoặc Task đã có")
            if self.target_draft_id is not None and (
                self.expected_summary_revision is None
                or self.expected_planning_input_revision is None
            ):
                raise ValueError("Chỉnh Draft cần hai revision hiện hành")
            if self.target_task_id is not None and self.expected_task_revision is None:
                raise ValueError("Chỉnh Task cần expected_task_revision")
            if self.expected_availability_revision is not None:
                raise ValueError("Giờ riêng không dùng revision bộ giờ chung")
        elif self.target_draft_id is not None or self.target_task_id is not None:
            raise ValueError("Target chỉ áp dụng khi chỉnh giờ đã khai báo")
        if self.changes.preferences is not None and self.expected_preferences_revision is None:
            raise ValueError("Đổi cài đặt cần expected_preferences_revision; lần đầu dùng 0")
        return self


class SettingsChangeResult(ApiModel):
    change_request_id: str
    request_revision: int
    application_state: str
    requested_revision: int | None
    effective_revision: int
    requested_availability_revision: int | None
    effective_availability_revision: int | None
    requested_changes: SettingsChanges
    pending_proposal_id: str | None
    target_draft_id: str | None = None
    target_task_id: str | None = None
    summary_revision: int | None = None
    planning_input_revision: int | None = None


class CancelSettingsChangeRequest(ApiModel):
    """Dòng 42 trên API Sheet: POST /api/v1/settings/change-requests/{change_request_id}/cancel."""

    expected_request_revision: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict) and "expected_change_request_revision" in data:
            copied = dict(data)
            alias_val = copied.pop("expected_change_request_revision")
            if "expected_request_revision" not in copied:
                copied["expected_request_revision"] = alias_val
            return copied
        return data


class CancelSettingsChangeResult(ApiModel):
    """Kết quả hủy yêu cầu đổi cài đặt đang chờ duyệt (Dòng 42)."""

    change_request_id: str
    application_state: Literal["cancelled"] = "cancelled"
    effective_revision: int = Field(ge=0)


class AdaptationSuggestionData(ApiModel):
    """Dòng 43 trên API Sheet (O19 AdaptationSuggestion): GET /api/v1/suggestions/current."""

    suggestion_id: str
    suggestion_revision: int = Field(ge=1)
    kind: SuggestionKind
    sample_count: int = Field(ge=0)
    current_value: float | list[int]
    suggested_value: float | list[int]
    reason: str
    policy_revision: str
    sample_refs: list[str]
    model_revision: str | None = None
    status: SuggestionStatus
    supersedes_suggestion_id: str | None = None


class SuggestionResponseRequest(ApiModel):
    """Dòng 44 trên API Sheet: POST /api/v1/suggestions/{suggestion_id}/response."""

    expected_suggestion_revision: int = Field(ge=1)
    expected_preference_revision: int | None = Field(default=None, ge=0)
    action: SuggestionAction
    edited_value: float | list[int] | None = None
    annoyance_feedback: AnnoyanceFeedback | Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        copied = dict(data)
        if "decision" in copied:
            raw_decision = copied.pop("decision")
            if "action" not in copied:
                copied["action"] = (
                    "edit" if raw_decision == "edit_and_accept" else raw_decision
                )
        elif copied.get("action") == "edit_and_accept":
            copied["action"] = "edit"

        if "expected_preferences_revision" in copied:
            pref_rev = copied.pop("expected_preferences_revision")
            if "expected_preference_revision" not in copied:
                copied["expected_preference_revision"] = pref_rev
        return copied

    @model_validator(mode="after")
    def check_edited_value_consistency(self):
        if self.action == "edit" and self.edited_value is None:
            raise ValueError("Cần gửi edited_value khi action là edit.")
        if self.action in ("accept", "reject") and self.edited_value is not None:
            raise ValueError("Chỉ gửi edited_value khi action là edit.")
        return self


class SuggestionResponseResult(ApiModel):
    """Dòng 44 trên API Sheet (SuggestionResponse): Kết quả phản hồi gợi ý trợ lý."""

    suggestion_id: str
    suggestion_revision: int = Field(ge=1)
    kind: SuggestionKind
    action: SuggestionAction
    status: Literal["accepted", "rejected"]
    accepted_value: float | list[int] | None = None
    application_state: SuggestionApplicationState
    effective_revision: int = Field(ge=0)
    requested_revision: int | None = None
    change_request_id: str | None = None
    pending_proposal_id: str | None = None


SuggestionResponse = SuggestionResponseResult
