"""Pydantic v2 schemas cho ScheduleData (Dòng 15), Session, FeasibilityReport và ScheduleProposal (UF2, UF6)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import ApiModel
from app.schemas.planning import ScheduleVersion
from app.schemas.task import GenerationTier, Session

RescheduleTriggerReason = Literal[
    "incomplete",
    "busy",
    "setting_changed",
    "deadline_risk",
    "skipped",
    "manual",
]
FeasibilityStatus = Literal[
    "valid",
    "deficit",
    "conflict",
    "unsolvable",
    "insufficient_data",
]
VerificationScope = Literal["horizon_part", "full_task"]
SegmentKind = Literal["work", "rest"]


class ScheduleData(ApiModel):
    """Dòng 15 trên API Sheet (GET /api/v1/schedule): Dữ liệu lịch các phiên đã xác nhận trong khoảng truy vấn."""

    schedule_revision: int = Field(ge=1)
    items: list[Session]
    next_cursor: str | None = None
    alarm_sync_state: str = Field(min_length=1)
    calendar_sync_summary: str = Field(min_length=1)


class SessionSegmentSchema(BaseModel):
    """Một quãng thời gian bên trong Session chính (O09.segments: chỉ gồm work và rest)."""

    model_config = ConfigDict(from_attributes=True)

    order_index: int = Field(..., ge=1, description="Vị trí thứ tự của quãng trong phiên.")
    kind: SegmentKind = Field(..., description="Loại quãng: work hoặc rest (không gồm micro_task).")
    duration_seconds: int = Field(..., gt=0, description="Thời lượng của quãng tính bằng giây.")


class ProposedSessionSchema(BaseModel):
    """Phiên tập trung dự kiến sinh bởi scheduler_engine ở UF2/UF6 (trước khi chốt lịch)."""

    model_config = ConfigDict(from_attributes=True)

    client_session_key: str = Field(
        ..., min_length=1, description="Khóa định danh tạm cho phiên dự kiến."
    )
    client_step_key: str = Field(..., min_length=1, description="Khóa tham chiếu tới bước dự kiến.")
    scheduled_start: datetime = Field(..., description="Giờ bắt đầu dự kiến (ISO 8601 UTC).")
    scheduled_end: datetime = Field(..., description="Giờ kết thúc dự kiến (ISO 8601 UTC).")
    micro_task_seconds: int | None = Field(
        default=120,
        ge=0,
        description=(
            "Thời lượng ước lượng cho gợi ý khởi động 2 phút trước phiên (O09: hiển thị trước "
            "phiên, KHÔNG cộng vào duration_seconds của phiên chính)."
        ),
    )
    work_seconds: int = Field(..., gt=0, description="Tổng thời gian làm chính (đã gồm buffer).")
    rest_seconds: int = Field(default=0, ge=0, description="Tổng thời gian nghỉ bên trong phiên.")
    duration_seconds: int = Field(
        ...,
        gt=0,
        description="Tổng thời lượng phiên chính (= work_seconds + rest_seconds, không cộng micro_task).",
    )
    micro_task_text: str | None = Field(
        default=None,
        description="Nội dung hành động khởi động 2 phút hiển thị trước phiên.",
    )
    segments: list[SessionSegmentSchema] = Field(
        default_factory=list,
        description="Danh sách các quãng làm/nghỉ tuần tự trong phiên.",
    )

    @model_validator(mode="after")
    def validate_session_durations(self) -> ProposedSessionSchema:
        """Kiểm chứng tính nhất quán thời gian của Session theo quy tắc bất biến O09."""
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end phải lớn hơn scheduled_start.")
        expected_total = self.work_seconds + self.rest_seconds
        if self.duration_seconds != expected_total:
            raise ValueError(
                f"duration_seconds ({self.duration_seconds}) phải bằng "
                f"work_seconds + rest_seconds ({expected_total}), không cộng micro_task_seconds."
            )
        if self.segments:
            sorted_segments = sorted(
                enumerate(self.segments),
                key=lambda item: (item[1].order_index, item[0]),
            )
            normalized: list[SessionSegmentSchema] = []
            seg_work = 0
            seg_rest = 0
            for new_idx, (_, seg) in enumerate(sorted_segments, start=1):
                seg.order_index = new_idx
                normalized.append(seg)
                if seg.kind == "work":
                    seg_work += seg.duration_seconds
                else:
                    seg_rest += seg.duration_seconds
            self.segments = normalized
            if seg_work != self.work_seconds or seg_rest != self.rest_seconds:
                raise ValueError(
                    f"Tổng các segments (work={seg_work}, rest={seg_rest}) phải khớp với "
                    f"work_seconds ({self.work_seconds}) và rest_seconds ({self.rest_seconds})."
                )
        return self


class FeasibilityReportSchema(BaseModel):
    """Báo cáo kiểm tra tính khả thi thời gian trước Deadline của thuật toán Python (O10)."""

    model_config = ConfigDict(from_attributes=True)

    status: FeasibilityStatus = Field(
        ...,
        description="Trạng thái khả thi: valid, deficit, conflict, unsolvable, insufficient_data.",
    )
    required_seconds: int = Field(
        ...,
        ge=0,
        description="Tổng thời gian yêu cầu T_req tính bằng giây.",
    )
    available_seconds: int = Field(
        ...,
        ge=0,
        description="Quỹ giờ rảnh khả dụng T_free trước Deadline tính bằng giây.",
    )
    unscheduled_seconds: int = Field(
        default=0,
        ge=0,
        description="Thời lượng đã ước lượng nhưng chưa xếp được vào ô trống.",
    )
    deficit_seconds: int = Field(
        default=0,
        ge=0,
        description="Số giây thiếu hụt tối thiểu Delta_T = max(0, T_req - T_free).",
    )
    verification_scope: VerificationScope = Field(
        default="horizon_part",
        description="Phạm vi kiểm chứng: horizon_part (2-3 tuần) hoặc full_task.",
    )
    fully_scheduled: bool = Field(
        default=True,
        description="True khi toàn bộ phạm vi trong report đã được xếp đủ Session hợp lệ.",
    )
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Danh sách nguyên nhân cụ thể gây thiếu giờ hoặc không xếp được phiên.",
    )
    unestimated_part_ids: list[UUID] = Field(
        default_factory=list,
        description="Danh sách các phần công việc chưa có ước lượng (không gán 0 phút).",
    )
    unplanned_part_ids: list[UUID] = Field(
        default_factory=list,
        description="Danh sách các phần công việc chưa chia hoặc chưa xếp lịch đầy đủ.",
    )
    extension_candidate_at: datetime | None = Field(
        default=None,
        description="Mốc gia hạn Deadline gợi ý (nếu người dùng muốn lùi hạn chót).",
    )

    @model_validator(mode="after")
    def enforce_deficit_formula(self) -> FeasibilityReportSchema:
        """Đảm bảo công thức bất biến Delta_T = max(0, T_req - T_free)."""
        computed_deficit = max(0, self.required_seconds - self.available_seconds)
        self.deficit_seconds = computed_deficit
        if computed_deficit > 0 and self.status == "valid":
            self.status = "deficit"
            self.fully_scheduled = False
        return self

    @property
    def deficit_minutes(self) -> int:
        """Số phút thiếu hụt làm tròn lên để hiển thị rõ ràng trên cảnh báo UI UF2."""
        if self.deficit_seconds <= 0:
            return 0
        return math.ceil(self.deficit_seconds / 60)


class LLMRescheduleEmpathyPayload(BaseModel):
    """Schema JSON ép kiểu trực tiếp cho LLM khi sinh lời động viên dời lịch ở UF6."""

    empathetic_note: str = Field(
        ...,
        min_length=5,
        max_length=400,
        description=(
            "Chọn nguyên văn đúng 1 câu từ danh sách allowed_notes do hệ thống cung cấp "
            "(Guilt-free Adaptive Rescheduling), không tự viết lại hoặc bịa thêm giờ, ngày, hạn chót."
        ),
    )
    encouragement_tip: str | None = Field(
        default=None,
        max_length=250,
        description="Giữ nguyên văn allowed_tip từ đầu vào hoặc trả null (không tự bịa gợi ý mới).",
    )


class RescheduleEmpatheticResult(LLMRescheduleEmpathyPayload):
    """Kết quả sinh lời giải thích & động viên khi tái lập lịch thích ứng ở UF6 (O15)."""

    model_config = ConfigDict(from_attributes=True)

    generation_tier: GenerationTier = Field(
        default="tier_1_gemini",
        description="Tầng AI hoặc Rule-based đã sinh lời khích lệ (O15.generation_tier).",
    )


class UF5SkipChipSchema(ApiModel):
    """Một lựa chọn bỏ qua rõ phạm vi ở UF5 (W3C COGA 3 Skip Chips)."""

    skip_scope: Literal["postpone_session", "skip_session", "skip_step"]
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)


class UF5CheckInPromptData(ApiModel):
    """Nội dung prompt Check-in UF5 tách biệt câu hỏi Session với xác nhận Step và 3 chip Bỏ qua."""

    kind: Literal["mid", "final"]
    prompt_text: str = Field(min_length=1)
    asks_step_completion: bool
    skip_chips: list[UF5SkipChipSchema] = Field(min_length=3, max_length=3)
    undo_notice: str = Field(min_length=1)
    undo_timeout_seconds: int = Field(default=5, ge=1)


__all__ = [
    "FeasibilityReportSchema",
    "FeasibilityStatus",
    "LLMRescheduleEmpathyPayload",
    "ProposedSessionSchema",
    "RescheduleEmpatheticResult",
    "RescheduleTriggerReason",
    "ScheduleData",
    "ScheduleVersion",
    "SegmentKind",
    "Session",
    "SessionSegmentSchema",
    "UF5CheckInPromptData",
    "UF5SkipChipSchema",
    "VerificationScope",
]
