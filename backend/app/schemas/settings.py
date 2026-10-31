# Schema/DTO cho cài đặt trợ lý (UserPreference - Dòng 39) và tái xuất Availability từ availability.py (Dòng 40)
from pydantic import Field

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
