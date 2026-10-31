"""Pydantic v2 schemas cho Task và Step (Request/Response & AI Structured Outputs cho UF1, UF2)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GenerationTier = Literal["tier_1_gemini", "tier_2_groq", "tier_3_rule_based"]
CognitiveLoad = Literal["light", "medium", "heavy"]
AvailabilitySource = Literal["manual", "calendar_inferred", "hybrid"]
TimeConstraintKind = Literal["deadline", "window", "timeframe"]
InputSource = Literal["text", "voice_direct", "thought_conversion"]
FreeWindowKind = Literal["weekly_rule", "specific_date"]

_LOCAL_TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_LOCAL_DATE_YYYYMMDD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


class ExtractedFreeWindowSchema(BaseModel):
    """Khung giờ rảnh có cấu trúc chuẩn hóa từ câu nói tự nhiên để chuyển cho scheduler_engine (O02)."""

    model_config = ConfigDict(from_attributes=True)

    kind: FreeWindowKind = Field(
        default="weekly_rule",
        description=(
            "weekly_rule cho lịch rảnh lặp theo thứ (1=Thứ Hai .. 7=Chủ Nhật); "
            "specific_date cho ngày cụ thể YYYY-MM-DD."
        ),
    )
    weekday: int | None = Field(
        default=None,
        ge=1,
        le=7,
        description="Thứ trong tuần theo chuẩn ISO/O02 (1=Thứ Hai .. 7=Chủ Nhật) khi kind='weekly_rule'.",
    )
    date: str | None = Field(
        default=None,
        description="Ngày cụ thể định dạng YYYY-MM-DD theo múi giờ người dùng khi kind='specific_date'.",
    )
    start_local: str | None = Field(
        default=None,
        description=(
            "Giờ bắt đầu định dạng HH:mm (24h). Bắt buộc để null nếu người dùng nói mơ hồ "
            "chưa rõ mốc giờ (ví dụ chỉ nói 'tối thứ 6'), CẤM tự đoán giờ."
        ),
    )
    end_local: str | None = Field(
        default=None,
        description=(
            "Giờ kết thúc định dạng HH:mm (24h, lớn hơn start_local). "
            "Bắt buộc để null nếu người dùng chưa nêu rõ mốc giờ kết thúc."
        ),
    )
    raw_phrase: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Cụm từ gốc người dùng nói/nhập (ví dụ: 'Tối thứ 6 từ 19:00 đến 21:30').",
    )
    is_ambiguous: bool = Field(
        default=False,
        description=(
            "Đặt true nếu khung giờ người dùng nêu còn thiếu giờ bắt đầu/kết thúc cụ thể "
            "hoặc chưa xác định được ngày/thứ rõ ràng."
        ),
    )

    @model_validator(mode="after")
    def validate_and_flag_ambiguity(self) -> ExtractedFreeWindowSchema:
        """Kiểm chứng định dạng HH:mm / YYYY-MM-DD và tự động bật cờ is_ambiguous nếu thiếu mốc giờ."""
        self.raw_phrase = self.raw_phrase.strip()
        if not self.raw_phrase:
            raise ValueError("raw_phrase không được để trống.")

        if self.date is not None:
            self.date = self.date.strip()
            if not _LOCAL_DATE_YYYYMMDD_RE.match(self.date):
                self.date = None
                self.is_ambiguous = True

        for attr_name in ("start_local", "end_local"):
            val = getattr(self, attr_name)
            if val is not None:
                cleaned = val.strip()
                if _LOCAL_TIME_HHMM_RE.match(cleaned):
                    setattr(self, attr_name, cleaned)
                else:
                    setattr(self, attr_name, None)
                    self.is_ambiguous = True

        if self.kind == "weekly_rule" and self.weekday is None:
            self.is_ambiguous = True
        elif self.kind == "specific_date" and self.date is None:
            self.is_ambiguous = True

        if self.start_local is None or self.end_local is None:
            self.is_ambiguous = True
        elif self.start_local >= self.end_local:
            # Khoảng qua đêm hoặc giờ kết thúc <= giờ bắt đầu cần làm rõ hoặc tách 2 ngày theo O02
            self.is_ambiguous = True

        return self


class TimeConstraintSchema(BaseModel):
    """Khoảng thời gian được phép thực hiện công việc (O03 TimeConstraint)."""

    model_config = ConfigDict(from_attributes=True)

    kind: TimeConstraintKind = Field(
        default="deadline",
        description="Phân biệt nhập theo hạn chót (deadline) hoặc khung thực hiện (window).",
    )
    start_at: datetime | None = Field(
        default=None,
        description="Mốc sớm nhất được bắt đầu (ISO 8601); null nếu đang ở bản nháp chưa làm rõ.",
    )
    finish_by_at: datetime | None = Field(
        default=None,
        description="Mốc muộn nhất hoàn tất khi kind='window' (ISO 8601).",
    )
    deadline_at: datetime | None = Field(
        default=None,
        description="Hạn chót gốc khi kind='deadline' (ISO 8601).",
    )
    input_timezone: str = Field(
        default="Asia/Ho_Chi_Minh",
        description="Múi giờ IANA để diễn giải ngày giờ người dùng nhập.",
    )

    @model_validator(mode="after")
    def validate_time_bounds(self) -> TimeConstraintSchema:
        """Chuẩn hóa nhánh deadline/window theo O03 và đảm bảo start_at nhỏ hơn mốc kết thúc."""
        if self.kind == "timeframe":
            self.kind = "window"

        if self.kind == "deadline":
            end_bound = self.deadline_at or self.finish_by_at
            if end_bound is None:
                raise ValueError(
                    "Khi kind='deadline', bắt buộc cung cấp deadline_at (hoặc finish_by_at)."
                )
            if (
                self.deadline_at is not None
                and self.finish_by_at is not None
                and self.deadline_at != self.finish_by_at
            ):
                raise ValueError(
                    "Khi kind='deadline', deadline_at không được lệch với finish_by_at."
                )
            self.deadline_at = end_bound
            if self.finish_by_at is None:
                self.finish_by_at = end_bound
        else:
            if self.finish_by_at is None:
                raise ValueError("Khi kind='window', bắt buộc cung cấp finish_by_at.")
            end_bound = self.finish_by_at

        if self.start_at is not None and self.start_at >= end_bound:
            raise ValueError("start_at phải nhỏ hơn mốc thời gian kết thúc.")
        return self


def _normalize_sequential_steps(steps: list[StepPromptItem]) -> list[StepPromptItem]:
    """Chuẩn hóa lại order_index của danh sách Step liên tục 1..N (tránh LLM đánh số nhảy/trùng)."""
    indexed = sorted(enumerate(steps), key=lambda item: (item[1].order_index, item[0]))
    normalized: list[StepPromptItem] = []
    for seq_idx, (_, step) in enumerate(indexed, start=1):
        step.order_index = seq_idx
        normalized.append(step)
    return normalized


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
    task_limits: list[dict[str, Any]] | dict[str, Any] | None = Field(
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
            "Danh sách các khung giờ rảnh người dùng có thể làm việc dạng chữ thân thiện hiển thị UI "
            "(Trường bắt buộc 3/4). Bắt buộc để mảng rỗng [] nếu chưa có thông tin, CẤM tự bịa khung giờ."
        ),
    )
    structured_free_windows: list[ExtractedFreeWindowSchema] = Field(
        default_factory=list,
        description=(
            "Danh sách khung giờ rảnh đã chuẩn hóa sang (weekday/date, start_local HH:mm, end_local HH:mm) "
            "để chuyển cho scheduler_engine. Nếu người dùng chỉ nói buổi mơ hồ (VD: 'tối thứ 6') mà chưa "
            "rõ từ mấy giờ đến mấy giờ, đặt start_local=null, end_local=null và is_ambiguous=true."
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
        description=(
            "Chỉ đặt true khi đủ cả 4 trường bắt buộc, không có khung giờ mơ hồ "
            "(is_ambiguous=false) và has_conflict là false."
        ),
    )
    has_conflict: bool = Field(
        default=False,
        description=(
            "Đặt true nếu phát hiện mâu thuẫn (ví dụ: deadline ở quá khứ, "
            "giờ rảnh người dùng nói trùng khớp hoàn toàn với lịch bận hoặc sau deadline)."
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
            "Danh sách TẤT CẢ câu hỏi làm rõ thông tin còn thiếu, mơ hồ hoặc mâu thuẫn, "
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
           bắt buộc (content, scope, free_time_slots, deadline), có khung giờ rảnh mơ hồ
           (is_ambiguous=True) hoặc đang có mâu thuẫn (has_conflict=True).
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
        if not cleaned_slots and self.structured_free_windows:
            cleaned_slots = [w.raw_phrase for w in self.structured_free_windows if w.raw_phrase]
        self.free_time_slots = cleaned_slots

        ambiguous_phrases = [w.raw_phrase for w in self.structured_free_windows if w.is_ambiguous]
        if not self.free_time_slots:
            missing_questions.append(
                "Bạn thường rảnh vào những khung giờ nào để mình xếp phiên tập trung phù hợp?"
            )
        elif ambiguous_phrases:
            joined_phrases = ", ".join(f"'{p}'" for p in ambiguous_phrases)
            missing_questions.append(
                f"Khung giờ {joined_phrases} bạn có thể làm từ mấy giờ đến mấy giờ cụ thể để mình xếp lịch chuẩn xác?"
            )

        if self.deadline is None:
            missing_questions.append("Khi nào bạn cần hoàn thành xong công việc này (hạn chót)?")

        if missing_questions or self.has_conflict:
            self.is_complete = False
            if not self.clarification_questions:
                if self.has_conflict and not missing_questions:
                    self.clarification_questions = [
                        "Thời gian hoàn thành hoặc khung giờ rảnh đang có điểm mâu thuẫn với lịch hiện tại, bạn kiểm tra và xác nhận lại giúp mình nhé?"
                    ]
                else:
                    self.clarification_questions = missing_questions

        return self


class TaskParseRequest(BaseModel):
    """Payload đầu vào cho API trích xuất và hợp nhất thông tin công việc ở UF1."""

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
    previous_draft: LLMTaskExtractionPayload | None = Field(
        default=None,
        description="Bản nháp trích xuất từ lượt trước để hợp nhất khi người dùng trả lời câu hỏi gộp.",
    )
    default_free_windows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Khung giờ rảnh mặc định đã xác nhận của người dùng (O02 Availability) nếu có.",
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

    @model_validator(mode="after")
    def normalize_step_order_indices(self) -> LLMTaskDecompositionPayload:
        """Đảm bảo order_index luôn liên tục 1..N dù LLM trả thứ tự nhảy cóc hoặc trùng nhau."""
        self.steps = _normalize_sequential_steps(self.steps)
        return self


class TaskDecompositionResult(LLMTaskDecompositionPayload):
    """Phương án phân rã công việc mặc định ở UF2 (Default Decomposition Proposal)."""

    model_config = ConfigDict(from_attributes=True)

    generation_tier: GenerationTier = Field(
        default="tier_1_gemini",
        description="Tầng AI hoặc Rule-based đã sinh phương án bẻ việc (O06.generation_tier).",
    )


# Alias tương thích với tài liệu kế hoạch chi tiết tuần 1 (week1_detailed_plan.md)
TaskDecompositionProposal = TaskDecompositionResult


class LLMPlanAdjustmentPayload(BaseModel):
    """
    Schema JSON ép kiểu trực tiếp cho LLM ở UF2 Bước 4 khi người dùng bấm 'Tùy chỉnh theo ý tôi'
    hoặc yêu cầu sửa kế hoạch/khung giờ/deadline bằng ngôn ngữ tự nhiên.
    Tuyệt đối KHÔNG chứa giờ bắt đầu/kết thúc Session (phép toán thời gian do scheduler_engine đảm nhận).
    """

    updated_steps: list[StepPromptItem] = Field(
        ...,
        min_length=1,
        max_length=7,
        description=(
            "Danh sách các bước sau khi áp dụng yêu cầu chỉnh sửa của người dùng "
            "(giữ nguyên danh sách bước cũ nếu người dùng chỉ đổi giờ rảnh hoặc deadline)."
        ),
    )
    added_free_windows: list[ExtractedFreeWindowSchema] = Field(
        default_factory=list,
        description="Các khung giờ rảnh mới người dùng muốn mở thêm cho công việc này (nếu có).",
    )
    removed_free_phrases: list[str] = Field(
        default_factory=list,
        description="Các khung giờ/buổi người dùng yêu cầu tránh hoặc gỡ bỏ khỏi phương án hiện tại (nếu có).",
    )
    updated_deadline: datetime | None = Field(
        default=None,
        description=(
            "Hạn chót mới định dạng ISO 8601 CHỈ KHI người dùng chủ động yêu cầu lùi/đổi hạn chót; "
            "bắt buộc để null nếu người dùng không nhắc tới việc đổi deadline."
        ),
    )
    requires_clarification: bool = Field(
        default=False,
        description=(
            "Đặt true nếu yêu cầu chỉnh sửa của người dùng còn mơ hồ (ví dụ: mở giờ rảnh chưa rõ mấy giờ) "
            "hoặc mâu thuẫn thời gian."
        ),
    )
    clarification_question: str | None = Field(
        default=None,
        max_length=350,
        description="Câu hỏi ngắn gọn, thân thiện để làm rõ nếu requires_clarification=true.",
    )
    assistant_note: str = Field(
        ...,
        min_length=1,
        max_length=400,
        description="1 câu phản hồi ấm áp xác nhận thay đổi để hệ thống xếp lại các phiên.",
    )

    @model_validator(mode="after")
    def enforce_uf2_adjustment_guardrails(self) -> LLMPlanAdjustmentPayload:
        """Chuẩn hóa lại order_index 1..N và kiểm tra tính rõ ràng của các khung giờ mới."""
        self.updated_steps = _normalize_sequential_steps(self.updated_steps)
        self.removed_free_phrases = [
            p.strip() for p in self.removed_free_phrases if p and p.strip()
        ]

        ambiguous_windows = [w.raw_phrase for w in self.added_free_windows if w.is_ambiguous]
        if ambiguous_windows:
            self.requires_clarification = True
            if not self.clarification_question or not self.clarification_question.strip():
                joined = ", ".join(f"'{p}'" for p in ambiguous_windows)
                self.clarification_question = f"Bạn muốn mở khung giờ {joined} cụ thể từ mấy giờ đến mấy giờ để mình tính lại lịch nhé?"
        elif self.requires_clarification and (
            not self.clarification_question or not self.clarification_question.strip()
        ):
            self.clarification_question = "Bạn có thể nói rõ hơn phần bước hoặc khung giờ muốn điều chỉnh giúp mình được không?"
        return self


class PlanAdjustmentResult(LLMPlanAdjustmentPayload):
    """Kết quả phân tích yêu cầu chỉnh sửa tự nhiên ở UF2 Bước 4 để nạp lại cho scheduler_engine."""

    model_config = ConfigDict(from_attributes=True)

    generation_tier: GenerationTier = Field(
        default="tier_1_gemini",
        description="Tầng AI hoặc Rule-based đã xử lý yêu cầu chỉnh sửa kế hoạch.",
    )


class PlanAdjustmentRequest(BaseModel):
    """Payload đầu vào cho API chỉnh sửa phương án chia việc/lịch bằng ngôn ngữ tự nhiên ở UF2."""

    model_config = ConfigDict(extra="forbid")

    user_instruction: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Câu yêu cầu điều chỉnh tự nhiên của người dùng (Voice STT hoặc Text).",
    )
    current_steps: list[StepPromptItem] = Field(
        ...,
        min_length=1,
        description="Danh sách các bước hiện tại trên màn hình UF2.",
    )
    current_deadline: datetime | None = Field(
        default=None,
        description="Hạn chót hiện tại của công việc.",
    )
    current_free_slots: list[str] = Field(
        default_factory=list,
        description="Các khung giờ rảnh hiện hành của công việc.",
    )
    busy_intervals: list[dict[str, str]] = Field(
        default_factory=list,
        description="Các khoảng bận từ Google Calendar MCP / các Task khác (chỉ đọc, cấm xáo trộn Task khác).",
    )
    timezone: str = Field(
        default="Asia/Ho_Chi_Minh",
        description="Múi giờ IANA của người dùng.",
    )
