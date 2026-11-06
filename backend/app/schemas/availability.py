"""Confirmed availability used by UF1 and UF2."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from app.schemas.common import ApiModel, CalendarContextRef, TimeInterval


class LocalInterval(ApiModel):
    start_local: time
    end_local: time

    @model_validator(mode="after")
    def valid_order(self):
        if self.start_local.tzinfo or self.end_local.tzinfo or self.start_local >= self.end_local:
            raise ValueError("Giờ địa phương phải tăng trong cùng ngày")
        return self


class WeeklyRule(LocalInterval):
    weekday: int = Field(ge=1, le=7)


class DateOverride(ApiModel):
    date: date
    intervals: list[LocalInterval]


class DraftAvailability(ApiModel):
    """Giờ user khai báo cho riêng yêu cầu UF1; xác nhận cùng summary."""

    timezone: str = Field(min_length=1)
    weekly_rules: list[WeeklyRule] = Field(default_factory=list)
    date_overrides: list[DateOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_rules(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Múi giờ IANA không hợp lệ") from exc
        dates = [item.date for item in self.date_overrides]
        if len(dates) != len(set(dates)):
            raise ValueError("Ngày ngoại lệ bị trùng")
        return self


class TaskAvailabilityLimit(ApiModel):
    task_id: str
    intervals: list[TimeInterval]


class Availability(ApiModel):
    availability_id: str
    revision: int = Field(ge=0)
    timezone: str = Field(min_length=1)
    weekly_rules: list[WeeklyRule]
    date_overrides: list[DateOverride]
    task_limits: list[TaskAvailabilityLimit]
    source: str
    effective_intervals: list[TimeInterval] | None
    calendar_context: CalendarContextRef | None
    confirmed_at: datetime | None

    @model_validator(mode="after")
    def valid_overrides(self):
        dates = [item.date for item in self.date_overrides]
        if len(dates) != len(set(dates)):
            raise ValueError("Ngày ngoại lệ bị trùng")
        if self.confirmed_at is not None and self.confirmed_at.utcoffset() is None:
            raise ValueError("Thời điểm xác nhận phải có múi giờ")
        return self
