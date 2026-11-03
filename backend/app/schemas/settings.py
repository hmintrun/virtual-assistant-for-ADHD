# Schema/DTO cho cài đặt trợ lý (UserPreference - Dòng 39) và tái xuất Availability từ availability.py (Dòng 40)
from pydantic import Field, model_validator

from app.schemas.availability import (
    Availability,
    DateOverride,
    LocalInterval,
    TaskAvailabilityLimit,
    WeeklyRule,
)
from app.schemas.common import ApiModel


class PreferenceChanges(ApiModel):
    """Các thay đổi cài đặt đang chờ duyệt (nếu ảnh hưởng lịch tương lai)."""

    work_interval_seconds: int | None = Field(default=None, ge=0)
    rest_interval_seconds: int | None = Field(default=None, ge=0)
    reminder_offsets: list[int] | None = None
    buffer_multiplier: float | None = Field(default=None, gt=0)
    reminder_style: str | None = None


class UserPreference(ApiModel):
    """Cấu hình trợ lý có hiệu lực và yêu cầu đang chờ duyệt (Dòng 39 - GET /api/v1/preferences)."""

    revision: int = Field(ge=0)
    effective_revision: int = Field(ge=0)
    requested_revision: int | None = Field(default=None, ge=1)
    work_interval_seconds: int = Field(ge=0)
    rest_interval_seconds: int = Field(ge=0)
    micro_task_seconds: int | None = Field(default=None, ge=0)
    reminder_offsets: list[int] = Field(min_length=1, max_length=1)
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
    availability: AvailabilityChanges | None = None
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
    changes: SettingsChanges

    @model_validator(mode="after")
    def required_revisions(self):
        if self.changes.availability is not None and self.expected_availability_revision is None:
            raise ValueError("Đổi giờ rảnh cần expected_availability_revision; lần đầu dùng 0")
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
    effective_availability_revision: int
    requested_changes: SettingsChanges
    pending_proposal_id: str | None
