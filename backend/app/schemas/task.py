"""Pydantic v2 schemas cho Task, Step, Session (API DTO & AI Structured Outputs cho UF1, UF2)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.availability import DraftAvailability
from app.schemas.common import (
    ApiModel,
    AvailabilityRef,
    ClarificationIssue,
    SessionSegment,
    TimeConstraint,
)
from app.schemas.draft import TaskSummary

GenerationTier = Literal["tier_1_gemini", "tier_2_groq", "tier_3_rule_based"]
CognitiveLoad = Literal["light", "medium", "heavy"]
AvailabilitySource = Literal["manual", "calendar_inferred", "hybrid"]
TimeConstraintKind = Literal["deadline", "window", "timeframe"]
InputSource = Literal["text", "voice_direct", "thought_conversion"]
FreeWindowKind = Literal["weekly_rule", "specific_date"]

_LOCAL_TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_LOCAL_DATE_YYYYMMDD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


class Step(ApiModel):
    step_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    estimated_base_seconds: int = Field(gt=0)
    remaining_base_seconds: int = Field(ge=0)
    buffer_multiplier: float = Field(ge=1)
    estimate_source: str = Field(min_length=1)
    status: str = Field(min_length=1)


class Session(ApiModel):
    session_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    scheduled_start: datetime
    scheduled_end: datetime
    micro_task_seconds: int | None = Field(default=None, ge=0)
    micro_task_text: str = Field(min_length=1)
    work_seconds: int = Field(gt=0)
    rest_seconds: int = Field(ge=0)
    segments: list[SessionSegment] = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    schedule_revision: int = Field(ge=1)
    status: str = Field(min_length=1)
    calendar_event_link_id: str | None = None
    start_state: str = Field(min_length=1)
    start_decision_due_at: datetime

    @model_validator(mode="after")
    def valid_durations(self):
        if self.scheduled_start.utcoffset() is None or self.scheduled_end.utcoffset() is None:
            raise ValueError("Giờ phiên phải có múi giờ")
        if self.duration_seconds != self.work_seconds + self.rest_seconds:
            raise ValueError("Thời lượng phiên không bằng làm chính cộng nghỉ")
        if sum(x.duration_seconds for x in self.segments if x.kind == "work") != self.work_seconds:
            raise ValueError("Các quãng làm chính không khớp")
        if sum(x.duration_seconds for x in self.segments if x.kind == "rest") != self.rest_seconds:
            raise ValueError("Các quãng nghỉ không khớp")
        if (self.scheduled_end - self.scheduled_start).total_seconds() != self.duration_seconds:
            raise ValueError("Giờ kết thúc không khớp thời lượng phiên")
        if [x.order_index for x in self.segments] != list(range(len(self.segments))):
            raise ValueError("Thứ tự các quãng không liên tục")
        return self


class Task(ApiModel):
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    extra_requirements: list[str] = Field(default_factory=list)
    time_constraint: TimeConstraint
    importance: int | None = Field(default=None, ge=1)
    status: str = Field(min_length=1)
    source_draft_id: str = Field(min_length=1)
    decomposition_state: Literal["not_started", "partial", "complete"]
    scheduling_state: Literal["not_scheduled", "partial", "complete"]
    revision: int = Field(default=1, ge=1)
    availability_ref: AvailabilityRef | None = None
    availability: DraftAvailability | None = None
    source_summary_revision: int | None = None
    source_planning_input_revision: int | None = None
    unexpanded_scope: str | None = None
    steps: list[Step]
    sessions: list[Session]
    source_summary: TaskSummary


class TaskListData(ApiModel):
    """Dòng 13 trên API Sheet (GET /api/v1/tasks): Danh sách công việc chính thức có phân trang."""

    items: list[Task]
    next_cursor: str | None = None


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


class ExtractedTimeConstraint(BaseModel):
    """Incomplete AI proposal; only a complete value becomes public TimeConstraint."""

    kind: Literal["deadline", "window"] | None = None
    start_at: datetime | None = None
    deadline_at: datetime | None = None
    finish_by_at: datetime | None = None
    input_timezone: str | None = None


class LLMTaskExtractionPayload(BaseModel):
    """Schema JSON ép kiểu trực tiếp cho LLM ở UF1 (không lộ trường nội bộ hệ thống)."""

    title: str | None = Field(
        default=None,
        max_length=100,
        description="Tên ngắn gọn nhận diện công việc (tối đa 100 ký tự). Null nếu chưa rõ.",
    )
    goal: str | None = None
    time_constraint: ExtractedTimeConstraint | None = None
    clarification_issues: list[ClarificationIssue] = Field(default_factory=list)
    resolved_issue_keys: list[str] = Field(default_factory=list)
    field_review_notes: dict[str, str] = Field(default_factory=dict)
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
    availability_update_mode: Literal["merge", "replace"] = Field(
        default="merge",
        description="merge bổ sung/hoàn thiện giờ rảnh; replace chỉ khi user yêu cầu thay/xóa khung cũ.",
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

        if self.deadline is None and self.time_constraint is None:
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

    unexpanded_scope: str | None = Field(
        default=None, description="Phạm vi tổng quan chưa chia Steps trong lần lập phương án này."
    )

    task_title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tên công việc đã chuẩn hóa ngắn gọn.",
    )
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=350)
    steps: list[StepPromptItem] = Field(
        ...,
        max_length=7,
        description="Danh sách Steps; chỉ để rỗng khi cần làm rõ ý tưởng chia bước.",
    )
    reasoning: str = Field(
        ...,
        min_length=1,
        description="1 câu giải thích ngắn gọn, khích lệ vì sao chia công việc theo lộ trình này.",
    )

    @model_validator(mode="after")
    def normalize_step_order_indices(self) -> LLMTaskDecompositionPayload:
        """Không sinh phương án khi ý tưởng còn cần làm rõ; giữ thứ tự 1..N."""
        self.clarification_question = (self.clarification_question or "").strip() or None
        if self.requires_clarification or self.clarification_question:
            self.requires_clarification = True
            self.clarification_question = self.clarification_question or (
                "Bạn muốn điều chỉnh ý tưởng chia bước thế nào để phù hợp với phạm vi đã xác nhận?"
            )
            self.steps = []
        else:
            if not self.steps:
                raise ValueError("Phương án đã rõ cần ít nhất một Step.")
            self.steps = _normalize_sequential_steps(self.steps)
        self.unexpanded_scope = (self.unexpanded_scope or "").strip() or None
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


class CandidateStepAdjustment(ApiModel):
    """Edit ngữ nghĩa trên một Step đã có; giữ nguyên client key của Candidate."""

    client_step_key: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    expected_output: str | None = Field(default=None, min_length=1)
    estimated_base_seconds: int | None = Field(default=None, gt=0)
    order_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_edit(self) -> CandidateStepAdjustment:
        if not self.client_step_key.strip():
            raise ValueError("Cần client_step_key của Step hiện có.")
        for field in ("title", "expected_output"):
            value = getattr(self, field)
            if value is not None:
                value = value.strip()
                if not value:
                    raise ValueError(f"{field} không được rỗng.")
                setattr(self, field, value)
        if all(
            getattr(self, field) is None
            for field in ("title", "expected_output", "estimated_base_seconds", "order_index")
        ):
            raise ValueError("Edit cần ít nhất một trường được hỗ trợ.")
        return self


class LLMCandidateAdjustmentPayload(ApiModel):
    """Đề xuất có giới hạn; Backend kiểm nguồn và áp edit, AI không ghi lịch."""

    step_edits: list[CandidateStepAdjustment]
    requires_clarification: bool
    clarification_question: str | None = Field(max_length=350)
    requires_draft_update: bool
    unsupported_changes: list[str]
    assistant_note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def mark_blocked_request(self) -> LLMCandidateAdjustmentPayload:
        self.clarification_question = (self.clarification_question or "").strip() or None
        self.unsupported_changes = [item.strip() for item in self.unsupported_changes]
        if any(not item for item in self.unsupported_changes):
            raise ValueError("Thao tác chưa hỗ trợ cần được mô tả rõ.")
        self.assistant_note = self.assistant_note.strip()
        if not self.assistant_note:
            raise ValueError("Cần phản hồi ngắn cho người dùng.")
        if self.requires_draft_update or self.unsupported_changes or self.clarification_question:
            self.requires_clarification = True
        if self.requires_clarification or not self.step_edits:
            self.requires_clarification = True
            self.clarification_question = self.clarification_question or (
                "Hãy sửa thời gian hoặc giờ rảnh trên bản tóm tắt rồi xác nhận lại UF1."
                if self.requires_draft_update
                else "Bạn muốn sửa tên, đầu ra, thời lượng hay thứ tự của bước nào?"
            )
            self.assistant_note = "Mình chưa thay đổi phương án hiện tại."
        return self


class CandidateAdjustmentResult(LLMCandidateAdjustmentPayload):
    """Kết quả nội bộ; yêu cầu chưa rõ/ngoài phạm vi không chứa edit để áp."""

    generation_tier: GenerationTier

    @model_validator(mode="after")
    def keep_blocked_request_atomic(self) -> CandidateAdjustmentResult:
        if self.requires_clarification:
            self.step_edits = []
        return self


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
