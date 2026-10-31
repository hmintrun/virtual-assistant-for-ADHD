# Schema/DTO cho cài đặt trợ lý (UserPreference - Dòng 39) và giờ rảnh đã xác nhận (Availability - Dòng 40)
from datetime import datetime

from pydantic import Field

from app.schemas.common import CalendarContextRef, StrictSchema, TimeInterval


class WeeklyRule(StrictSchema):
    """Quy tắc giờ rảnh lặp lại hàng tuần (weekday: 1=Thứ Hai .. 7=Chủ Nhật)."""

    weekday: int = Field(ge=1, le=7)
    start_local: str = Field(min_length=5, max_length=5)
    end_local: str = Field(min_length=5, max_length=5)


class LocalTimeInterval(StrictSchema):
    """Khoảng giờ địa phương HH:mm trong cùng một ngày."""

    start_local: str = Field(min_length=5, max_length=5)
    end_local: str = Field(min_length=5, max_length=5)


class DateOverride(StrictSchema):
    """Ngoại lệ giờ rảnh cho một ngày cụ thể (YYYY-MM-DD); intervals=[] nghĩa là ngày đó không rảnh."""

    date: str = Field(min_length=10, max_length=10)
    intervals: list[LocalTimeInterval] = Field(default_factory=list)


class TaskAvailabilityLimit(StrictSchema):
    """Giới hạn bổ sung trên giờ rảnh chung cho một Task đã lưu."""

    task_id: str = Field(min_length=1)
    intervals: list[TimeInterval] = Field(default_factory=list)


class PreferenceChanges(StrictSchema):
    """Các thay đổi cài đặt đang chờ duyệt (nếu ảnh hưởng lịch tương lai)."""

    work_interval_seconds: int | None = Field(default=None, ge=0)
    rest_interval_seconds: int | None = Field(default=None, ge=0)
    reminder_offsets: list[int] | None = None
    buffer_multiplier: float | None = Field(default=None, gt=0)
    reminder_style: str | None = None


class UserPreference(StrictSchema):
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


class Availability(StrictSchema):
    """Bộ giờ rảnh đã được người dùng xác nhận (Dòng 40 - GET /api/v1/availability)."""

    availability_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    timezone: str = Field(min_length=1)
    weekly_rules: list[WeeklyRule] = Field(default_factory=list)
    date_overrides: list[DateOverride] = Field(default_factory=list)
    task_limits: list[TaskAvailabilityLimit] = Field(default_factory=list)
    source: str = Field(min_length=1)
    confirmed_at: datetime | None = None
    effective_intervals: list[TimeInterval] | None = None
    calendar_context: CalendarContextRef | None = None

