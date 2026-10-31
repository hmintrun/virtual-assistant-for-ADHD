# Pydantic v2 schemas cho ScheduleData (Dòng 15); Session và ScheduleVersion lấy từ task.py và planning.py
from pydantic import Field

from app.schemas.common import ApiModel
from app.schemas.planning import ScheduleVersion
from app.schemas.task import Session


class ScheduleData(ApiModel):
    """Dòng 15 trên API Sheet (GET /api/v1/schedule): Dữ liệu lịch các phiên đã xác nhận trong khoảng truy vấn."""

    schedule_revision: int = Field(ge=1)
    items: list[Session]
    next_cursor: str | None = None
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)


__all__ = ["ScheduleData", "ScheduleVersion", "Session"]
