# Schema/DTO cho bản chụp ngữ cảnh lập lịch và nguồn lịch bận Google/Manual (CalendarContext - Dòng 61, 62)
from datetime import datetime
from typing import Any, Literal

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


class OAuthAuthUrlResponse(StrictSchema):
    """URL dẫn tới màn hình đồng ý OAuth 2.0 của Google."""

    auth_url: str


class CalendarAuthorizationRequest(StrictSchema):
    """Yêu cầu khởi tạo luồng kết nối Google Calendar từ thiết bị."""

    device_id: str = "default_device"
    return_target: str | None = None


class CalendarAuthorizationResponse(StrictSchema):
    """Kết quả tạo authorization intent."""

    authorization_id: str
    auth_url: str
    expires_at: datetime


class CalendarConnectionRead(StrictSchema):
    """Thông tin hiển thị kết nối tài khoản lịch O23."""

    connection_id: str
    provider: str = "google"
    account_ref: str
    connection_state: str
    read_calendar_ids: list[str] = Field(default_factory=list)
    write_calendar_id: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    last_successful_sync_at: datetime | None = None


class CalendarSummaryItem(StrictSchema):
    """Tóm tắt một lịch cụ thể của người dùng trong Google Calendar."""

    id: str
    summary: str
    description: str | None = None
    primary: bool = False
    access_role: str = "reader"
    background_color: str | None = None
    selected_for_read: bool = False
    selected_for_write: bool = False


class CalendarListResponse(StrictSchema):
    """Danh sách các lịch mà người dùng sở hữu hoặc có quyền truy cập."""

    connection_id: str
    account_ref: str
    items: list[CalendarSummaryItem] = Field(default_factory=list)


class CalendarSelectionUpdateRequest(StrictSchema):
    """Yêu cầu cập nhật các lịch được chọn để đọc giờ bận và ghi session."""

    read_calendar_ids: list[str] = Field(default_factory=list)
    write_calendar_id: str | None = None


class CalendarConfigurationPreviewRequest(StrictSchema):
    """Yêu cầu xem trước tác động cấu hình lịch đọc/ghi."""

    expected_connection_revision: int | None = None
    read_calendar_ids: list[str] = Field(default_factory=list)
    write_calendar_id: str | None = None
    existing_event_action: str | None = None  # e.g., 'provision_new', 'keep', 'move'


class CalendarConfigurationPreviewResponse(StrictSchema):
    """Bản xem trước cấu hình lịch trước khi áp dụng chính thức."""

    preview_id: str
    connection_id: str
    read_calendar_ids: list[str] = Field(default_factory=list)
    write_calendar_id: str | None = None
    existing_event_action: str | None = None
    status: str = "pending"


class CalendarConfigurationConfirmRequest(StrictSchema):
    """Yêu cầu xác nhận áp dụng cấu hình lịch từ bản xem trước."""

    expected_preview_revision: int | None = 1


class CalendarDisconnectRequest(StrictSchema):
    """Yêu cầu ngắt kết nối Calendar."""

    expected_connection_revision: int | None = None


class CalendarRefreshRequest(StrictSchema):
    """Yêu cầu làm mới dữ liệu nguồn Calendar."""

    from_at: datetime | None = None
    to_at: datetime | None = None
    expected_connection_revision: int | None = None


class CalendarSyncJobRead(StrictSchema):
    """Thông tin trạng thái công việc đồng bộ lịch."""

    job_id: str
    connection_id: str
    target_kind: str
    target_ref: str
    operation: str
    job_state: str
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    last_error_code: str | None = None


class CalendarSyncJobRetryRequest(StrictSchema):
    """Yêu cầu thử lại một sync job bị lỗi."""

    expected_job_revision: int | None = None


class EventChangePreviewRequest(StrictSchema):
    """Yêu cầu tạo bản xem trước thay đổi sự kiện trên Google Calendar (Dòng 55)."""

    operation: str
    connection_id: str
    google_calendar_id: str
    payload: dict[str, Any] | None = None
    recurrence_scope: str | None = None
    google_event_id: str | None = None
    expected_google_etag: str | None = None


class EventChangePreviewResponse(StrictSchema):
    """Bản xem trước thay đổi sự kiện (Dòng 55)."""

    preview_id: str
    connection_id: str
    kind: str = "event_change"
    operation: str
    status: str = "pending"
    payload: dict[str, Any]


class EventChangeConfirmRequest(StrictSchema):
    """Yêu cầu xác nhận thay đổi sự kiện từ preview (Dòng 56)."""

    expected_preview_revision: int | None = 1
    source_revisions: dict[str, Any] | None = None
