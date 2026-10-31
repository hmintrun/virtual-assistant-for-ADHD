# Các Schema/DTO dùng chung: Vỏ bọc phản hồi (Envelope), Lỗi, Khoảng thời gian và Tham chiếu (Ref)
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

VN_TZ = timezone(timedelta(hours=7))


class StrictSchema(BaseModel):
    """Schema cơ sở: từ chối các trường lạ không có trong hợp đồng."""

    model_config = ConfigDict(extra="forbid")


class ResponseMeta(StrictSchema):
    request_id: str
    server_time: datetime


def build_response_meta(request_id: str | None = None) -> ResponseMeta:
    """Tạo metadata chuẩn cho mọi HTTP response."""
    return ResponseMeta(
        request_id=request_id or f"req-{uuid4().hex[:8]}",
        server_time=datetime.now(VN_TZ),
    )


class FieldError(StrictSchema):
    field_path: str
    message: str
    code: str | None = None


class ErrorDetail(StrictSchema):
    code: str
    message: str
    field_errors: list[FieldError] = Field(default_factory=list)
    retryable: bool = False
    current_revisions: dict[str, int] = Field(default_factory=dict)
    next_action: str


class SuccessEnvelope[DataT](StrictSchema):
    data: DataT
    meta: ResponseMeta


class ErrorEnvelope(StrictSchema):
    error: ErrorDetail
    meta: ResponseMeta


class AvailabilityRef(StrictSchema):
    availability_id: str = Field(min_length=1)
    revision: int = Field(ge=1)


class CalendarContextRef(StrictSchema):
    context_id: str = Field(min_length=1)
    revision: int = Field(ge=1)


class CandidateRef(StrictSchema):
    candidate_id: str = Field(min_length=1)
    review_revision: int = Field(ge=1)


class TimeInterval(StrictSchema):
    start_at: datetime
    end_at: datetime


class SourceRevisions(StrictSchema):
    availability: int = Field(ge=0)
    preferences: int = Field(ge=0)
    schedule: int = Field(ge=0)
    calendar_context: CalendarContextRef | None = None


class ConfiguredParameters(StrictSchema):
    """Các tham số cấu hình và ngưỡng vận hành của hệ thống (Dòng 60)."""

    buffer_multiplier_range: list[float] | None = None
    minimum_sample_count: int | None = None
    audio_max_seconds: int | None = None
    audio_max_bytes: int | None = None
    preview_ttl_seconds: int | None = None
    default_reminder_offset_seconds: int = Field(ge=0)
    start_grace_seconds: int = Field(ge=0)
    min_inter_session_gap_seconds: int = Field(ge=0)
    checkin_timeout_seconds: int = Field(ge=0)


class CapabilitiesData(StrictSchema):
    """Phản hồi khả năng hệ thống và phiên bản hợp đồng (Dòng 60 - GET /api/v1/capabilities)."""

    contract_version: str = Field(min_length=1)
    policy_revision: str = Field(min_length=1)
    offline_start_supported: bool = False
    configured_parameters: ConfiguredParameters
    policy_state: str = Field(min_length=1)


class CommandResult(StrictSchema):
    """Biên lai kết quả xử lý lệnh ghi để phục hồi khi mất phản hồi mạng (Dòng 63 - GET /api/v1/commands/{command_id})."""

    command_id: str = Field(min_length=1)
    command_type: str = Field(min_length=1)
    state: str = Field(min_length=1)
    result_reference: dict[str, str] | None = None
    applied_at: datetime | None = None

