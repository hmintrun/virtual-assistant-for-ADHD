"""Pydantic v2 DTOs cho thực thi phiên tập trung (UF3/UF5: SessionRun, CheckIn, RunEvent, ActualCorrection)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.common import ApiModel, SessionSegment

SkipScope = Literal["skip_session", "postpone_session", "skip_step"]
SessionOutcome = Literal[
    "unknown",
    "completed",
    "partial",
    "incomplete",
    "busy",
    "skipped",
]
CheckInResponseState = Literal["open", "answered", "no_respond"]
CheckInKind = Literal["mid", "final"]
RunState = Literal[
    "running",
    "paused",
    "awaiting_checkin",
    "closed",
]
ActivePhase = Literal["work", "rest", "ended"]
ActualSource = Literal["timer_proxy", "self_report"]
EndReason = Literal["timer_elapsed", "user_stopped", "interrupted", "cancelled"]


class SessionStartRequest(ApiModel):
    """Dòng 16 trên API Sheet (POST /api/v1/sessions/{session_id}/start)."""

    expected_schedule_revision: int = Field(ge=1)
    device_id: str = Field(min_length=1)
    requested_start_at: datetime

    @model_validator(mode="after")
    def validate_timezone(self) -> SessionStartRequest:
        if self.requested_start_at.utcoffset() is None:
            raise ValueError("Thời điểm bắt đầu phải có múi giờ.")
        return self


StartSessionRequest = SessionStartRequest


class SessionSnapshot(ApiModel):
    """Bản chụp cấu trúc Session (O09) lưu trong SettingsSnapshot khi bắt đầu Run."""

    session_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_text: str = Field(min_length=1)
    micro_task_seconds: int | None = Field(default=None, ge=0)
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    schedule_revision: int = Field(ge=1)
    status: str = Field(default="in_progress", min_length=1)
    calendar_event_link_id: str | None = None
    start_state: str = Field(default="started", min_length=1)
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def validate_snapshot_durations(self) -> SessionSnapshot:
        if self.scheduled_start.utcoffset() is None or self.scheduled_end.utcoffset() is None:
            raise ValueError("Giờ phiên phải có múi giờ.")
        if self.duration_seconds != self.work_seconds + self.rest_seconds:
            raise ValueError("Thời lượng phiên không bằng làm chính cộng nghỉ.")
        return self


class SettingsSnapshot(ApiModel):
    """Bản chụp cấu hình hiệu lực tại thời điểm khởi chạy SessionRun."""

    effective_revision: int = Field(ge=0)
    session: SessionSnapshot


class CheckInData(ApiModel):
    """Bản ghi câu hỏi check-in và phản hồi giữa phiên / cuối phiên (O13)."""

    checkin_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    kind: CheckInKind
    response_state: CheckInResponseState = "open"
    session_outcome: SessionOutcome = "unknown"
    step_completed: bool | None = None
    task_completed: bool | None = None
    skip_scope: SkipScope | None = None
    remaining_note: str | None = None
    asked_at: datetime
    responded_at: datetime | None = None
    response_revision: int = Field(default=0, ge=0)


CheckInEmbedded = CheckInData


class SessionRunData(ApiModel):
    """Bản ghi một lần thực hiện thực tế của Session (O12 - Dòng 16, 17, 18, 20)."""

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    run_revision: int = Field(default=1, ge=1)
    started_at: datetime
    ended_at: datetime | None = None
    run_state: RunState = "running"
    active_phase: ActivePhase = "work"
    segment_index: int | None = 0
    work_elapsed_seconds: int | None = Field(default=None, ge=0)
    rest_elapsed_seconds: int | None = Field(default=None, ge=0)
    actual_work_seconds: int | None = Field(default=None, ge=0)
    actual_source: ActualSource | None = None
    outcome: SessionOutcome = "unknown"
    checkin_timeout_at: datetime | None = None
    settings_snapshot: SettingsSnapshot
    check_in: CheckInData | None = None
    dnd_release_at: datetime | None = None


SessionRun = SessionRunData


class RunEventItem(ApiModel):
    """Một sự kiện timer trong mẻ đồng bộ (Dòng 19)."""

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    segment_index: int = Field(ge=0)
    work_elapsed_seconds: int = Field(ge=0)
    rest_elapsed_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_occurred_at(self) -> RunEventItem:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("Thời điểm sự kiện phải có múi giờ.")
        return self


class RunEventBatchRequest(ApiModel):
    """Dòng 19 trên API Sheet (POST /api/v1/runs/{run_id}/events)."""

    device_id: str = Field(min_length=1)
    expected_run_revision: int = Field(ge=1)
    events: list[RunEventItem] = Field(min_length=1)


RunEventsBatchRequest = RunEventBatchRequest


class RunEventBatchResult(ApiModel):
    """Kết quả xử lý mẻ sự kiện timer (Dòng 19)."""

    run_id: str = Field(min_length=1)
    run_revision: int = Field(ge=1)
    accepted_event_ids: list[str] = Field(default_factory=list)
    duplicate_event_ids: list[str] = Field(default_factory=list)


class RunEndRequest(ApiModel):
    """Dòng 20 trên API Sheet (POST /api/v1/runs/{run_id}/end)."""

    device_id: str = Field(min_length=1)
    expected_run_revision: int = Field(ge=1)
    event_id: str = Field(min_length=1)
    ended_at: datetime
    end_reason: EndReason
    work_elapsed_seconds: int = Field(ge=0)
    rest_elapsed_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_ended_at(self) -> RunEndRequest:
        if self.ended_at.utcoffset() is None:
            raise ValueError("Thời điểm kết thúc phải có múi giờ.")
        return self


EndRunRequest = RunEndRequest


class ActualCorrectionRequest(ApiModel):
    """Dòng 32 trên API Sheet (POST /api/v1/runs/{run_id}/actual-corrections)."""

    expected_run_revision: int = Field(ge=1)
    self_report_work_seconds: int = Field(ge=0)
    reason: str = Field(min_length=1)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_occurred_at(self) -> ActualCorrectionRequest:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("Thời điểm hiệu chỉnh phải có múi giờ.")
        return self


class ActualCorrectionData(ApiModel):
    """Kết quả hiệu chỉnh thời gian làm thực tế (Dòng 32)."""

    run_id: str = Field(min_length=1)
    run_revision: int = Field(ge=1)
    actual_work_seconds: int = Field(ge=0)
    actual_source: Literal["self_report"] = "self_report"
    work_elapsed_seconds: int = Field(ge=0)


ActualCorrectionResult = ActualCorrectionData


class CheckInListResult(ApiModel):
    """Dòng 29 trên API Sheet (GET /api/v1/runs/{run_id}/check-ins)."""

    items: list[CheckInData]
    next_cursor: str | None = None


class CheckInCreateRequest(ApiModel):
    """Dòng 30 trên API Sheet (POST /api/v1/runs/{run_id}/check-ins)."""

    kind: CheckInKind
    asked_at: datetime
    expected_run_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_asked_at(self) -> CheckInCreateRequest:
        if self.asked_at.utcoffset() is None:
            raise ValueError("Thời điểm hỏi check-in phải có múi giờ.")
        return self


CreateCheckInRequest = CheckInCreateRequest


class CheckInResponseRequest(ApiModel):
    """Dòng 31 trên API Sheet (PUT /api/v1/check-ins/{checkin_id}/response)."""

    expected_response_revision: int = Field(ge=0)
    responded_at: datetime
    session_outcome: Literal["completed", "partial", "incomplete", "busy", "skipped", "unknown"]
    step_completed: bool | None = None
    task_completed: bool | None = None
    skip_scope: SkipScope | None = None
    remaining_note: str | None = None

    @model_validator(mode="after")
    def validate_skip_and_timezone(self) -> CheckInResponseRequest:
        if self.responded_at.utcoffset() is None:
            raise ValueError("Thời điểm phản hồi phải có múi giờ.")
        if self.session_outcome == "skipped" and self.skip_scope is None:
            raise ValueError(
                "Phải chọn phạm vi bỏ qua (skip_scope) khi session_outcome là skipped."
            )
        if self.session_outcome != "skipped" and self.skip_scope is not None:
            raise ValueError("Chỉ được gửi skip_scope khi session_outcome là skipped.")
        return self


RespondCheckInRequest = CheckInResponseRequest


class DeviceStateReportRequest(ApiModel):
    """Dòng21: trạng thái phải do thiết bị báo, không tự suy quyền hoặc DND."""

    device_report_revision: int = Field(ge=1)
    notification_permission: str = Field(min_length=1)
    microphone_permission: str = Field(min_length=1)
    dnd_permission: str = Field(min_length=1)
    alarm_capability: str = Field(min_length=1)
    active_run_id: str | None = Field(default=None, min_length=1)
    last_schedule_revision: int | None = Field(default=None, ge=0)
    dnd_owner_run_id: str | None = Field(default=None, min_length=1)
    dnd_apply_state: str = Field(min_length=1)
    dnd_restore_state: str = Field(min_length=1)
    reported_at: AwareDatetime
    app_visibility: Literal["foreground", "background"]


class DeviceStateData(DeviceStateReportRequest):
    device_id: str = Field(min_length=1)


class ReminderData(ApiModel):
    reminder_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    schedule_revision: int = Field(ge=1)
    kind: Literal["before_session"] = "before_session"
    fire_at: AwareDatetime
    dedupe_key: str = Field(min_length=1)
    delivery_state: str = Field(min_length=1)
    user_action: str | None = None
    device_id: str | None = None


class ReminderListResult(ApiModel):
    items: list[ReminderData]
    next_cursor: str | None = None


class ReminderReceiptRequest(ApiModel):
    device_id: str = Field(min_length=1)
    schedule_revision: int = Field(ge=1)
    delivery_state: str = Field(min_length=1)
    occurred_at: AwareDatetime
    user_action: str | None = None


__all__ = [
    "DeviceStateData",
    "DeviceStateReportRequest",
    "ReminderData",
    "ReminderListResult",
    "ReminderReceiptRequest",
    "ActivePhase",
    "ActualCorrectionData",
    "ActualCorrectionRequest",
    "ActualCorrectionResult",
    "ActualSource",
    "CheckInCreateRequest",
    "CheckInData",
    "CheckInEmbedded",
    "CheckInKind",
    "CheckInListResult",
    "CheckInResponseRequest",
    "CheckInResponseState",
    "CreateCheckInRequest",
    "EndReason",
    "EndRunRequest",
    "RespondCheckInRequest",
    "RunEndRequest",
    "RunEventBatchRequest",
    "RunEventBatchResult",
    "RunEventItem",
    "RunEventsBatchRequest",
    "RunState",
    "SessionOutcome",
    "SessionRun",
    "SessionRunData",
    "SessionSnapshot",
    "SessionStartRequest",
    "SettingsSnapshot",
    "SkipScope",
    "StartSessionRequest",
]
