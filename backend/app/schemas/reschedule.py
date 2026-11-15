"""Pydantic v2 DTOs cho phân hệ Tái lập lịch Thích ứng (UF6: RescheduleProposal, UpcomingScheduleContext, Continuation)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import ApiModel, BlockingReason, SessionSegment
from app.schemas.execution import SessionRunData

TriggerReason = Literal[
    "checkin_incomplete",
    "checkin_busy",
    "checkin_skipped",
    "missed_start",
    "user_reschedule",
    "early_completion_pull_forward",
    "settings_change",
    "expand_next_horizon",
]

ProposalStatus = Literal["pending", "approved", "rejected", "superseded"]

ProposalGenerationTier = Literal[
    "gemini_primary",
    "gemini_backup",
    "groq_fallback",
    "python_fallback",
    "tier_1_gemini",
    "tier_2_groq",
    "tier_3_rule_based",
]


class ProposalSourceRevisions(ApiModel):
    """Bản chụp phiên bản các nguồn dữ liệu tại thời điểm tính toán UF6 (Dòng 64, 33–37)."""

    task: int = Field(ge=0)
    schedule: int = Field(ge=0)
    availability: int = Field(ge=0)
    settings: int = Field(ge=0)
    calendar: int = Field(ge=0)


class ProposalFeasibilityData(ApiModel):
    """Kết quả đánh giá tính khả thi của RescheduleProposal (Dòng 33–36)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Literal[
        "feasible",
        "infeasible",
        "deficit",
        "insufficient_data",
        "valid",
        "conflict",
        "unsolvable",
    ]
    confirmable: bool
    deficit_seconds: int | None = Field(default=None, alias="deficit_seconds")
    blocking_reasons: list[BlockingReason] = Field(default_factory=list)
    next_horizon_preview: dict[str, Any] | None = Field(default=None, alias="next_horizon_preview")


class ProposalScheduleVersionData(ApiModel):
    """Thông tin phiên bản lịch chính thức sau khi duyệt Proposal (Dòng 37)."""

    version_number: int = Field(ge=1)
    base_version: int | None = Field(default=None, ge=0)
    approved_at: datetime
    affected_task_ids: list[str] = Field(default_factory=list)
    min_inter_session_gap_seconds: int = Field(default=900, ge=900)
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)


class ProposalSessionData(ApiModel):
    """Thông tin Session trả về khi duyệt Proposal (hỗ trợ cả phiên thường và phiên Continuation micro_task_text=None)."""

    session_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_text: str | None = None
    micro_task_seconds: int | None = Field(default=None, ge=0)
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    schedule_revision: int = Field(ge=1)
    status: str = Field(min_length=1)
    calendar_event_link_id: str | None = None
    start_state: str = Field(min_length=1)
    start_decision_due_at: datetime


class CurrentStepBrief(ApiModel):
    """Thông tin bước hiện tại của Run trong upcoming-schedule-context (Dòng 64)."""

    step_id: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    remaining_seconds: int = Field(ge=0)
    status: str = Field(min_length=1)


class CoverageWindow(ApiModel):
    """Khung thời gian bao phủ của timeline (Dòng 64)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    timezone: str = Field(min_length=1)
    complete: bool = True


class LimitAssumptions(ApiModel):
    """Giả định giới hạn tính toán phương án làm thêm giờ (Dòng 64)."""

    immediate_continuation: bool = True
    continuation_start: datetime
    inter_session_gap_seconds: int = Field(default=900, ge=0)
    borrow_break_seconds: int = Field(default=0, ge=0, le=120)


class TimelineSessionItem(ApiModel):
    """Mục phiên tập trung trên timeline sắp tới (Dòng 64)."""

    kind: Literal["session"] = "session"
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_title: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    step_title: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    duration_seconds: int = Field(gt=0)
    status: str = Field(min_length=1)
    is_movable: bool = True


class TimelineGoogleBusyItem(ApiModel):
    """Mục sự kiện bận Google Calendar trên timeline sắp tới (Dòng 64)."""

    kind: Literal["google_busy"] = "google_busy"
    event_id: str = Field(min_length=1)
    title: str = Field(default="Busy")
    start: datetime
    end: datetime
    is_locked: bool = True
    is_movable: bool = False


TimelineItem = Annotated[
    TimelineSessionItem | TimelineGoogleBusyItem,
    Field(discriminator="kind"),
]


class UpcomingScheduleContextData(ApiModel):
    """Dữ liệu ngữ cảnh lịch sắp tới phía sau Run (Dòng 64)."""

    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_title: str = Field(min_length=1)
    current_step: CurrentStepBrief
    calculated_at: datetime
    source_run_revision: int = Field(ge=1)
    source_revisions: ProposalSourceRevisions
    calendar_status: Literal["ok", "stale", "unavailable"] = "ok"
    calendar_fetched_at: datetime | None = None
    coverage: CoverageWindow
    next_cursor: str | None = None
    max_extension_seconds_without_borrow: int = Field(ge=0)
    recommended_extension_seconds: list[int] = Field(default_factory=list)
    limit_assumptions: LimitAssumptions
    timeline: list[TimelineItem] = Field(default_factory=list)


class ContinuationRequest(ApiModel):
    """Yêu cầu làm tiếp ngay sau khi kết thúc phiên (SY02 / R04 - Dòng 33)."""

    mode: Literal["continue_now"] = "continue_now"
    source_run_id: str = Field(min_length=1)
    expected_run_revision: int = Field(ge=1)
    additional_work_seconds: int = Field(ge=60, le=3600)
    strategy: Literal["extend_then_shift_later_sessions"] = "extend_then_shift_later_sessions"
    immediate_continuation: bool = True
    borrow_break_seconds: int = Field(default=0, ge=0, le=120)
    allowed_task_ids: list[str] | None = None


class CreateProposalRequest(ApiModel):
    """Dòng 33 trên API Sheet (POST /api/v1/proposals)."""

    trigger_reason: TriggerReason
    target_task_id: str | None = None
    allowed_task_ids: list[str] = Field(default_factory=list)
    expected_base_schedule_revision: int = Field(ge=0)
    expected_source_revisions: ProposalSourceRevisions
    request_text: str | None = None
    continuation_request: ContinuationRequest | None = None


class ProposalTimeRange(ApiModel):
    """Khoảng thời gian start/end trong biến đổi phiên."""

    start: datetime
    end: datetime


class ProposedSessionAddAfter(ApiModel):
    """Thông tin phiên continuation mới trong proposed_changes.sessions_to_add."""

    step_id: str = Field(min_length=1)
    start: datetime
    end: datetime
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    duration_seconds: int = Field(gt=0)
    micro_task_text: str | None = None
    micro_task_seconds: int = Field(default=0, ge=0)
    segments: list[SessionSegment] = Field(default_factory=list)


class ProposedSessionAddItem(ApiModel):
    """Mục thêm mới phiên trong proposed_changes.sessions_to_add."""

    temp_session_id: str = Field(min_length=1)
    action: Literal["create_continuation_session", "create_session"] = "create_continuation_session"
    task_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    source_run_id: str | None = None
    before: None = None
    after: ProposedSessionAddAfter
    reason: str = Field(min_length=1)


class ProposedSessionMoveItem(ApiModel):
    """Mục dịch chuyển phiên trong proposed_changes.sessions_to_move."""

    session_id: str = Field(min_length=1)
    task_id: str | None = None
    step_id: str | None = None
    from_start: datetime | None = None
    from_end: datetime | None = None
    to_start: datetime | None = None
    to_end: datetime | None = None
    before: ProposalTimeRange | None = None
    after: ProposalTimeRange | None = None
    shift_seconds: int | None = None
    reason: str | None = None


class ProposedSessionCancelItem(ApiModel):
    """Mục hủy phiên trong proposed_changes.sessions_to_cancel."""

    session_id: str = Field(min_length=1)
    task_id: str | None = None
    step_id: str | None = None
    reason: str | None = None


class ProposedStepItem(ApiModel):
    """Mục thêm mới bước trong proposed_changes.steps_to_add."""

    step_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    estimated_base_seconds: int = Field(gt=0)


class ProposedChanges(ApiModel):
    """Chi tiết các thay đổi dự kiến trong RescheduleProposal."""

    sessions_to_move: list[ProposedSessionMoveItem] = Field(default_factory=list)
    sessions_to_add: list[ProposedSessionAddItem] = Field(default_factory=list)
    sessions_to_cancel: list[ProposedSessionCancelItem] = Field(default_factory=list)
    steps_to_add: list[ProposedStepItem] = Field(default_factory=list)
    next_horizon_from: datetime | None = None
    next_horizon_to: datetime | None = None


class GapException(ApiModel):
    """Ngoại lệ khoảng nghỉ giữa 2 phiên trong Continuation (R04)."""

    from_session_ref: str = Field(min_length=1)
    to_session_ref: str = Field(min_length=1)
    standard_gap_seconds: int = Field(default=900, ge=0)
    applied_gap_seconds: int = Field(ge=0)
    borrowed_seconds: int | None = Field(default=None, ge=0)
    reason: Literal["immediate_continuation", "user_borrowed_break"]


class StartWindow(ApiModel):
    """Cửa sổ thời gian hợp lệ để bấm duyệt Continuation (R05)."""

    earliest_start: datetime
    latest_start: datetime
    not_before: datetime
    not_after: datetime


class ShiftedSessionBound(ApiModel):
    """Ràng buộc biên độ dịch chuyển của các phiên phía sau trong ApprovalEnvelope (R05)."""

    session_id: str = Field(min_length=1)
    earliest_start: datetime
    latest_start: datetime
    fixed_duration_seconds: int = Field(gt=0)
    locked_after_event_id: str | None = None


class ApprovalEnvelope(ApiModel):
    """Phong bì kiểm tra tính hợp lệ khi bấm Approve tại thời điểm thực tế (R05)."""

    rule: Literal["actual_start_plus_fixed_work_plus_min_break_le_latest_start"] = (
        "actual_start_plus_fixed_work_plus_min_break_le_latest_start"
    )
    continuation_fixed_work_seconds: int = Field(gt=0)
    continuation_min_break_after_seconds: int = Field(ge=0)
    shifted_sessions_bounds: list[ShiftedSessionBound] = Field(default_factory=list)


class ProposalData(ApiModel):
    """Đối tượng RescheduleProposal đầy đủ trả về cho client (O15 - Dòng 33, 34, 35, 36)."""

    proposal_id: str = Field(min_length=1)
    status: ProposalStatus
    trigger_reason: str = Field(min_length=1)
    target_task_id: str | None = None
    allowed_task_ids: list[str] = Field(default_factory=list)
    affected_task_ids: list[str] = Field(default_factory=list)
    base_schedule_revision: int = Field(ge=0)
    source_revisions: ProposalSourceRevisions
    proposal_revision: int = Field(ge=1)
    supersedes_proposal_id: str | None = None
    calculated_at: datetime | None = None
    continuation_request: ContinuationRequest | None = None
    proposed_changes: ProposedChanges
    gap_exceptions: list[GapException] = Field(default_factory=list)
    start_window: StartWindow | None = None
    approval_envelope: ApprovalEnvelope | None = None
    feasibility: ProposalFeasibilityData
    explanation: str
    generation_tier: ProposalGenerationTier = "python_fallback"
    stale: bool = False
    created_at: datetime
    updated_at: datetime


class RebuildProposalRequest(ApiModel):
    """Dòng 36 trên API Sheet (POST /api/v1/proposals/{proposal_id}/rebuild)."""

    expected_proposal_revision: int = Field(ge=1)
    request_text: str | None = None


class ApproveProposalRequest(ApiModel):
    """Dòng 37 trên API Sheet (POST /api/v1/proposals/{proposal_id}/approve)."""

    expected_proposal_revision: int = Field(ge=1)
    expected_base_schedule_revision: int = Field(ge=0)
    expected_source_revisions: ProposalSourceRevisions
    start_continuation: bool = False
    device_id: str | None = None
    event_id: str | None = None
    expected_source_run_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_continuation_fields(self) -> ApproveProposalRequest:
        if self.start_continuation:
            if not self.device_id:
                raise ValueError("Bắt buộc truyền device_id khi start_continuation=true.")
            if not self.event_id:
                raise ValueError("Bắt buộc truyền event_id khi start_continuation=true.")
            if self.expected_source_run_revision is None:
                raise ValueError(
                    "Bắt buộc truyền expected_source_run_revision khi start_continuation=true."
                )
        return self


class ApproveProposalData(ApiModel):
    """Kết quả duyệt RescheduleProposal (Dòng 37)."""

    proposal_id: str = Field(min_length=1)
    status: Literal["approved"] = "approved"
    schedule_version: ProposalScheduleVersionData
    updated_schedule_revision: int | None = None
    sessions: list[ProposalSessionData]
    continuation_session_id: str | None = None
    continuation_run: SessionRunData | None = None
    calendar_sync_job_enqueued: bool | None = None


class RejectProposalRequest(ApiModel):
    """Dòng 38 trên API Sheet (POST /api/v1/proposals/{proposal_id}/reject)."""

    expected_proposal_revision: int = Field(ge=1)
    reason: str | None = None


class RejectProposalData(ApiModel):
    """Kết quả từ chối RescheduleProposal (Dòng 38)."""

    proposal_id: str = Field(min_length=1)
    status: Literal["rejected"] = "rejected"
    requested_state: Literal["unchanged"] = "unchanged"
