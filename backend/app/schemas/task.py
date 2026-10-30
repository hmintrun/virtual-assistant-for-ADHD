"""Pydantic v2 schemas cho Task và Step (Request/Response & AI Structured Outputs cho UF1, UF2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GenerationTier = Literal["tier_1_gemini", "tier_2_groq", "tier_3_rule_based"]
CognitiveLoad = Literal["light", "medium", "heavy"]
AvailabilitySource = Literal["manual", "calendar_inferred", "hybrid"]
TimeConstraintKind = Literal["deadline", "timeframe"]
InputSource = Literal["text", "voice_direct", "thought_conversion"]


class TimeConstraintSchema(BaseModel):
    """Khoảng thời gian được phép thực hiện công việc (O03 TimeConstraint)."""

    model_config = ConfigDict(from_attributes=True)

    kind: TimeConstraintKind = Field(
        default="deadline",
        description="Phân biệt nhập theo hạn chót (deadline) hoặc khung thời gian (timeframe).",
    )
    start_at: datetime | None = Field(
        default=None,
        description="Mốc sớm nhất được bắt đầu (ISO 8601); null nếu có thể bắt đầu ngay.",
    )
    finish_by_at: datetime = Field(
        ...,
        description="Mốc muộn nhất toàn bộ các phiên phải kết thúc (ISO 8601).",
    )
    deadline_at: datetime | None = Field(
        default=None,
        description="Hạn chót gốc khi kind='deadline'; đồng bộ với finish_by_at.",
    )
    input_timezone: str = Field(
        default="Asia/Ho_Chi_Minh",
        description="Múi giờ IANA để diễn giải ngày giờ người dùng nhập.",
    )

    @model_validator(mode="after")
    def validate_time_bounds(self) -> TimeConstraintSchema:
        """Đảm bảo mốc bắt đầu trước mốc kết thúc và đồng bộ deadline_at khi kind='deadline'."""
        if self.start_at is not None and self.start_at >= self.finish_by_at:
            raise ValueError("start_at phải nhỏ hơn finish_by_at.")
        if self.kind == "deadline":
            if self.deadline_at is None:
                self.deadline_at = self.finish_by_at
            elif self.deadline_at != self.finish_by_at:
                raise ValueError("Khi kind='deadline', deadline_at phải khớp với finish_by_at.")
        return self


class AvailabilitySchema(BaseModel):
    """Giờ rảnh do người dùng cung cấp hoặc xác nhận ở UF1 (O02 Availability)."""

    model_config = ConfigDict(from_attributes=True)

    weekly_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Các khoảng giờ rảnh lặp theo tuần.",
    )
    date_overrides: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Các ngoại lệ bận/rảnh theo ngày cụ thể.",
    )
    task_limits: dict[str, Any] | None = Field(
        default=None,
        description="Giới hạn khung giờ áp dụng riêng cho công việc này (nếu có).",
    )
    source: AvailabilitySource = Field(
        default="manual",
        description="Nguồn giờ rảnh: manual, calendar_inferred hoặc hybrid.",
    )
    timezone: str = Field(
        default="Asia/Ho_Chi_Minh",
        description="Múi giờ IANA áp dụng cho quy tắc giờ rảnh.",
    )
    confirmed_at: datetime | None = Field(
        default=None,
        description="Thời điểm người dùng xác nhận bộ giờ rảnh.",
    )


class LLMTaskExtractionPayload(BaseModel):
    """Schema JSON ép kiểu trực tiếp cho LLM ở UF1 (không lộ trường nội bộ hệ thống)."""

    title: str | None = Field(
        default=None,
        max_length=100,
        description="Tên ngắn gọn nhận diện công việc (tối đa 100 ký tự). Null nếu chưa rõ.",
    )
    content: str | None = Field(
        default=None,
        description="Nội dung công việc cụ thể người dùng muốn thực hiện (Trường bắt buộc 1/4).",
    )
    scope: str | None = Field(
        default=None,
        description=(
            "Phạm vi hoặc đầu ra cụ thể của công việc, ví dụ '5 trang Word', '20 slide' "
            "(Trường bắt buộc 2/4). Bắt buộc để null nếu người dùng chưa nêu rõ, CẤM tự đoán."
        ),
    )
    free_time_slots: list[str] = Field(
        default_factory=list,
        description=(
            "Danh sách các khung giờ rảnh người dùng có thể làm việc (Trường bắt buộc 3/4). "
            "Bắt buộc để mảng rỗng [] nếu chưa có thông tin, CẤM tự bịa khung giờ."
        ),
    )
    deadline: datetime | None = Field(
        default=None,
        description=(
            "Hạn chót hoàn thành theo định dạng ISO 8601 (Trường bắt buộc 4/4). "
            "Bắt buộc để null nếu người dùng chưa cung cấp mốc thời gian, CẤM tự gán hạn chót."
        ),
    )
    extra_requirements: list[str] = Field(
        default_factory=list,
        description="Các lưu ý hoặc ràng buộc phụ thêm người dùng nhắc tới (nếu có).",
    )
    is_complete: bool = Field(
        default=False,
        description="Chỉ đặt true khi đủ cả 4 trường bắt buộc và has_conflict là false.",
    )
    has_conflict: bool = Field(
        default=False,
        description=(
            "Đặt true nếu phát hiện mâu thuẫn (ví dụ: deadline ở quá khứ, "
            "giờ rảnh người dùng nói trùng khớp hoàn toàn với lịch bận)."
        ),
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="Bản tóm tắt ngắn gọn, thân thiện bằng tiếng Việt cho người dùng xem lại.",
    )
    clarification_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Danh sách TẤT CẢ câu hỏi làm rõ thông tin còn thiếu hoặc mâu thuẫn, "
            "gộp chung trong 1 lần hiển thị duy nhất (Single-Round Clarification)."
        ),
    )


class TaskExtractionResult(LLMTaskExtractionPayload):
    """Kết quả trích xuất mục tiêu UF1 hoàn chỉnh (kèm chốt chặn chống Hallucination & Tier)."""

    model_config = ConfigDict(from_attributes=True)

    generation_tier: GenerationTier = Field(
        default="tier_1_gemini",
        description="Tầng AI hoặc Rule-based đã sinh bản tóm tắt (O04.generation_tier).",
    )

    @model_validator(mode="after")
    def enforce_uf1_strict_guardrails(self) -> TaskExtractionResult:
        """
        Chốt chặn kiến trúc UF1:
        1. Không bao giờ cho phép is_complete=True nếu thiếu bất kỳ trường nào trong 4 trường
           bắt buộc (content, scope, free_time_slots, deadline) hoặc đang có mâu thuẫn.
        2. Nếu chưa hoàn chỉnh mà danh sách câu hỏi rỗng, tự động bổ sung câu hỏi gộp 1 vòng.
        """
        missing_questions: list[str] = []

        if self.content is None or not self.content.strip():
            self.content = None
            missing_questions.append("Bạn muốn thực hiện công việc cụ thể nào?")

        if self.scope is None or not self.scope.strip():
            self.scope = None
            missing_questions.append(
                "Phạm vi hoặc kết quả đầu ra mong muốn của việc này là gì (ví dụ: số trang, số bài, mức độ hoàn thiện)?"
            )

        cleaned_slots = [slot.strip() for slot in self.free_time_slots if slot and slot.strip()]
        self.free_time_slots = cleaned_slots
        if not self.free_time_slots:
            missing_questions.append(
                "Bạn thường rảnh vào những khung giờ nào để mình xếp phiên tập trung phù hợp?"
            )

        if self.deadline is None:
            missing_questions.append("Khi nào bạn cần hoàn thành xong công việc này (hạn chót)?")

        if missing_questions or self.has_conflict:
            self.is_complete = False
            if not self.clarification_questions:
                if self.has_conflict and not missing_questions:
                    self.clarification_questions = [
                        "Thời gian hoàn thành hoặc khung giờ rảnh đang có điểm mâu thuẫn, bạn kiểm tra và xác nhận lại giúp mình nhé?"
                    ]
                else:
                    self.clarification_questions = missing_questions

        return self


class TaskParseRequest(BaseModel):
    """Payload đầu vào cho API trích xuất và kiểm tra công việc ở UF1."""

    model_config = ConfigDict(extra="forbid")

    raw_input: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Chuỗi văn bản người dùng nhập hoặc bản nhận dạng giọng nói STT.",
    )
    input_source: InputSource = Field(
        default="text",
        description="Nguồn nhập liệu: text, voice_direct hoặc thought_conversion.",
    )
    timezone: str = Field(
        default="Asia/Ho_Chi_Minh",
        description="Múi giờ hiện tại của thiết bị người dùng.",
    )
    client_draft_id: str | None = Field(
        default=None,
        description="Mã nháp sinh từ Mobile để chống tạo trùng khi auto-save (O05).",
    )
    busy_intervals: list[dict[str, str]] = Field(
        default_factory=list,
        description="Các khoảng bận đọc từ Google Calendar MCP (O21) để phát hiện trùng giờ.",
    )


class StepPromptItem(BaseModel):
    """Một bước nhỏ trong phương án phân rã công việc ở UF2 (khớp O08 Step)."""

    model_config = ConfigDict(from_attributes=True)

    order_index: int = Field(
        ...,
        ge=1,
        description="Thứ tự thực hiện của bước (bắt đầu từ 1).",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên hành động cụ thể, rõ ràng của bước này.",
    )
    expected_output: str | None = Field(
        default=None,
        description="Đầu ra cụ thể để cuối phiên kiểm chứng bước đã xong hay chưa (O08).",
    )
    estimated_minutes: int = Field(
        ...,
        ge=5,
        le=180,
        description="Thời lượng làm chính dự kiến gốc tính bằng phút (chưa nhân hệ số buffer).",
    )
    micro_task_prompt: str = Field(
        ...,
        min_length=3,
        max_length=300,
        description=(
            "1 hành động khởi động siêu nhỏ làm trong 2 phút đầu (ví dụ: 'Mở file Word và gõ tiêu đề') "
            "nhằm phá vỡ tê liệt khởi động ADHD."
        ),
    )
    cognitive_load: CognitiveLoad | None = Field(
        default=None,
        description="Mức tải nhận thức AI tự gắn nhãn ngầm: light, medium hoặc heavy (O08).",
    )

    @model_validator(mode="after")
    def validate_non_blank_strings(self) -> StepPromptItem:
        """Đảm bảo tiêu đề bước và micro-task 2 phút không phải chuỗi toàn khoảng trắng."""
        self.title = self.title.strip()
        self.micro_task_prompt = self.micro_task_prompt.strip()
        if not self.title:
            raise ValueError("Tiêu đề bước (title) không được để trống.")
        if not self.micro_task_prompt:
            raise ValueError("Việc khởi động 2 phút (micro_task_prompt) không được để trống.")
        return self

    @property
    def estimated_base_seconds(self) -> int:
        """Quy đổi sang giây nguyên chuẩn O08.estimated_base_seconds cho scheduler_engine."""
        return self.estimated_minutes * 60


class LLMTaskDecompositionPayload(BaseModel):
    """Schema JSON ép kiểu trực tiếp cho LLM khi sinh Default Decomposition Proposal ở UF2."""

    task_title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên công việc đã chuẩn hóa ngắn gọn.",
    )
    steps: list[StepPromptItem] = Field(
        ...,
        min_length=1,
        max_length=7,
        description="Danh sách các bước nhỏ tuần tự (khuyến nghị 2–5 bước vừa sức cho người ADHD).",
    )
    reasoning: str = Field(
        ...,
        min_length=1,
        description="1 câu giải thích ngắn gọn, khích lệ vì sao chia công việc theo lộ trình này.",
    )


class TaskDecompositionResult(LLMTaskDecompositionPayload):
    """Phương án phân rã công việc mặc định ở UF2 (Default Decomposition Proposal)."""

    model_config = ConfigDict(from_attributes=True)

    generation_tier: GenerationTier = Field(
        default="tier_1_gemini",
        description="Tầng AI hoặc Rule-based đã sinh phương án bẻ việc (O06.generation_tier).",
    )


# Alias tương thích với tài liệu kế hoạch chi tiết tuần 1 (week1_detailed_plan.md)
TaskDecompositionProposal = TaskDecompositionResult
