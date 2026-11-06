"""Shared API values for planning; timestamps always include an offset."""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

VN_TZ = timezone(timedelta(hours=7))


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Giữ bí danh tương thích nếu có module nội bộ tham chiếu StrictSchema
StrictSchema = ApiModel


class ResponseMeta(ApiModel):
    request_id: str
    server_time: datetime


def build_response_meta(request_id: str | None = None) -> ResponseMeta:
    """Tạo metadata chuẩn cho mọi HTTP response."""
    return ResponseMeta(
        request_id=request_id or f"req-{uuid4().hex[:8]}",
        server_time=datetime.now(VN_TZ),
    )


class FieldError(ApiModel):
    field_path: str
    message: str
    code: str | None = None


class ErrorDetail(ApiModel):
    code: str
    message: str
    field_errors: list[FieldError] = Field(default_factory=list)
    retryable: bool = False
    current_revisions: dict[str, int] = Field(default_factory=dict)
    next_action: str


class SuccessEnvelope[DataT](ApiModel):
    data: DataT
    meta: ResponseMeta


class ErrorEnvelope(ApiModel):
    error: ErrorDetail
    meta: ResponseMeta


class TimeInterval(ApiModel):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            raise ValueError("Mốc thời gian phải có múi giờ")
        if self.start_at >= self.end_at:
            raise ValueError("Thời điểm bắt đầu phải trước kết thúc")
        return self


class AvailabilityRef(ApiModel):
    availability_id: str = Field(min_length=1)
    revision: int = Field(ge=0)


class CalendarContextRef(ApiModel):
    context_id: str = Field(min_length=1)
    revision: int = Field(ge=0)


class CandidateRef(ApiModel):
    candidate_id: str = Field(min_length=1)
    review_revision: int = Field(ge=0)


class SourceRevisions(ApiModel):
    availability: int | None = Field(default=None, ge=0)
    preferences: int = Field(ge=0)
    schedule: int = Field(ge=0)
    calendar_context: CalendarContextRef | None = None


class FieldSource(ApiModel):
    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class ClarificationIssue(ApiModel):
    code: str
    field_path: str
    message: str
    question: str
    blocking: bool


class BlockingReason(ApiModel):
    code: str
    message: str


class SessionSegment(ApiModel):
    order_index: int = Field(ge=0)
    kind: Literal["work", "rest"]
    duration_seconds: int = Field(gt=0)


class TimeConstraint(ApiModel):
    kind: Literal["deadline", "window"]
    start_at: datetime
    finish_by_at: datetime | None = None
    deadline_at: datetime | None = None
    input_timezone: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_branch(self):
        end = self.deadline_at if self.kind == "deadline" else self.finish_by_at
        other = self.finish_by_at if self.kind == "deadline" else self.deadline_at
        if end is None or other is not None:
            raise ValueError("Chỉ điền mốc kết thúc của nhánh đã chọn")
        if self.start_at.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("Mốc thời gian phải có múi giờ")
        if self.start_at >= end:
            raise ValueError("Thời điểm bắt đầu phải trước hạn cuối")
        return self


class ConfiguredParameters(ApiModel):
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


class CapabilitiesData(ApiModel):
    """Phản hồi khả năng hệ thống và phiên bản hợp đồng (Dòng 60 - GET /api/v1/capabilities)."""

    contract_version: str = Field(min_length=1)
    policy_revision: str = Field(min_length=1)
    offline_start_supported: bool = False
    configured_parameters: ConfiguredParameters
    policy_state: str = Field(min_length=1)


class CommandResult(ApiModel):
    """Biên lai kết quả xử lý lệnh ghi để phục hồi khi mất phản hồi mạng (Dòng 63 - GET /api/v1/commands/{command_id})."""

    command_id: str = Field(min_length=1)
    command_type: str = Field(min_length=1)
    state: str = Field(min_length=1)
    result_reference: dict[str, str] | None = None
    applied_at: datetime | None = None
