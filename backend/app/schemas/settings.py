# Schema/DTO cho cài đặt trợ lý (UserPreference - Dòng 39) và tái xuất Availability từ availability.py (Dòng 40)
from typing import Annotated

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
    "Availability",
    "DateOverride",
    "LocalInterval",
    "LocalTimeInterval",
    "PreferenceChanges",
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
    """Dòng 42: hủy yêu cầu cài đặt theo đúng revision hiện hành."""

    expected_request_revision: int = Field(ge=0)
