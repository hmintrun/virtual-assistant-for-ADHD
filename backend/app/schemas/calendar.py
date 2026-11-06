# Schema/DTO cho bản chụp ngữ cảnh lập lịch và nguồn lịch bận Google/Manual (CalendarContext - Dòng 61, 62)
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.availability import DraftAvailability
from app.schemas.common import AvailabilityRef, StrictSchema, TimeInterval


class CalendarSource(StrictSchema):
    """Cặp nguồn lịch chuẩn gồm connection_id và google_calendar_id."""

    connection_id: str = Field(min_length=1)
    google_calendar_id: str = Field(min_length=1)


class BusyIntervalSource(StrictSchema):
    """Nguồn gốc của một khoảng thời gian bận (calendar_event, confirmed_session, hoặc manual)."""

    kind: Literal["calendar_event", "confirmed_session", "manual"]
    connection_id: str | None = None
    google_calendar_id: str | None = None
    google_event_id: str | None = None
    session_id: str | None = None
    client_interval_id: str | None = None


class BusyInterval(StrictSchema):
    """Khoảng thời gian bận đã có nguồn đối chiếu rõ ràng."""

    start_at: datetime
    end_at: datetime
    source: BusyIntervalSource


class ManualBusyInterval(StrictSchema):
    """Khoảng thời gian bận do người dùng nhập tay xác nhận."""

    client_interval_id: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime


class ExcludedCalendarSource(StrictSchema):
    """Nguồn lịch Google bị lỗi được người dùng chủ động xác nhận bỏ qua trong lần lập phương án này."""

    connection_id: str = Field(min_length=1)
    google_calendar_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    user_confirmed_at: datetime


class CalendarError(StrictSchema):
    """Thông tin lỗi khi đọc một nguồn Google Calendar cụ thể."""

    connection_id: str = Field(min_length=1)
    google_calendar_id: str = Field(min_length=1)
    error_code: str = Field(min_length=1)
    retryable: bool
    requires_reconnect: bool
    observed_at: datetime


class CalendarEvent(StrictSchema):
    """Bản chụp sự kiện từ Google Calendar tại thời điểm lấy ngữ cảnh."""

    connection_id: str = Field(min_length=1)
    google_calendar_id: str = Field(min_length=1)
    google_event_id: str = Field(min_length=1)
    title: str | None = None
    start_at: datetime
    end_at: datetime


class PlanningContextCreateRequest(StrictSchema):
    """Body yêu cầu gom giờ rảnh, lịch bận và nguồn Calendar thành bản chụp có phiên bản (Dòng 61)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    availability_ref: AvailabilityRef | None = None
    source_draft_id: str | None = Field(default=None, min_length=1)
    expected_summary_revision: int | None = Field(default=None, ge=1)
    from_at: datetime = Field(alias="from")
    to_at: datetime = Field(alias="to")
    selected_calendar_sources: list[CalendarSource] = Field(default_factory=list)
    manual_busy_intervals: list[ManualBusyInterval] = Field(default_factory=list)
    excluded_sources: list[ExcludedCalendarSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_source(self):
        if (self.availability_ref is None) == (self.source_draft_id is None):
            raise ValueError("Chỉ chọn một nguồn: Draft hoặc Availability cũ")
        if (self.source_draft_id is None) != (self.expected_summary_revision is None):
            raise ValueError("Nguồn Draft cần expected_summary_revision")
        TimeInterval(start_at=self.from_at, end_at=self.to_at)
        return self


class CalendarContext(StrictSchema):
    """Bản chụp dữ liệu nguồn (giờ rảnh + lịch bận) đã dùng để tính phương án xếp lịch (Dòng 61 & 62)."""

    context_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    availability_ref: AvailabilityRef | None = None
    source_draft_id: str | None = None
    source_summary_revision: int | None = None
    availability_snapshot: DraftAvailability | None = None
    selected_calendar_ids: list[str] = Field(default_factory=list)
    query_start_end: TimeInterval
    busy_intervals: list[BusyInterval] = Field(default_factory=list)
    fetched_at: datetime
    sync_state: str = Field(min_length=1)
    calendar_errors: list[CalendarError] = Field(default_factory=list)
    event_snapshots: list[CalendarEvent] = Field(default_factory=list)
    coverage_state: str = Field(min_length=1)
    manual_busy_intervals: list[ManualBusyInterval] = Field(default_factory=list)
    excluded_sources: list[ExcludedCalendarSource] = Field(default_factory=list)
    selected_calendar_sources: list[CalendarSource] = Field(default_factory=list)
