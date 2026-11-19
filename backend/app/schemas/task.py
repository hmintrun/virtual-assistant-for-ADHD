"""Pydantic v2 schemas cho Task, Step, Session (API DTO & AI Structured Outputs cho UF1, UF2)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
FreeWindowKind = Literal["weekly_rule", "specific_date", "daily", "date_range"]
FreeWindowOp = Literal["add", "replace_day", "exclude", "reset_override"]

_LOCAL_TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_LOCAL_DATE_YYYYMMDD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
_SPOKEN_WEEKDAY_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bthứ\s*(?:hai|2)\b", re.IGNORECASE), 1),
    (re.compile(r"\bthứ\s*(?:ba|3)\b", re.IGNORECASE), 2),
    (re.compile(r"\bthứ\s*(?:tư|bốn|4)\b", re.IGNORECASE), 3),
    (re.compile(r"\bthứ\s*(?:năm|5)\b", re.IGNORECASE), 4),
    (re.compile(r"\bthứ\s*(?:sáu|6)\b", re.IGNORECASE), 5),
    (re.compile(r"\bthứ\s*(?:bảy|bẩy|7)\b", re.IGNORECASE), 6),
    (re.compile(r"\bchủ\s*nhật\b", re.IGNORECASE), 7),
)
_WORD_WEEKDAY_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bthứ\s+hai\b", re.IGNORECASE), 1),
    (re.compile(r"\bthứ\s+ba\b", re.IGNORECASE), 2),
    (re.compile(r"\bthứ\s+(?:tư|bốn)\b", re.IGNORECASE), 3),
    (re.compile(r"\bthứ\s+năm\b", re.IGNORECASE), 4),
    (re.compile(r"\bthứ\s+sáu\b", re.IGNORECASE), 5),
    (re.compile(r"\bthứ\s+(?:bảy|bẩy)\b", re.IGNORECASE), 6),
    (re.compile(r"\b(?:chủ\s*nhật|cn)\b", re.IGNORECASE), 7),
)
_SAME_TIME_INHERIT_TOKENS: tuple[str, ...] = (
    "cũng giờ đó",
    "giờ đó",
    "như trên",
    "khung giờ đó",
    "cùng giờ đó",
    "y như vậy",
)
_DAILY_REPEAT_TOKENS: tuple[str, ...] = (
    "hằng ngày",
    "hàng ngày",
    "mỗi ngày",
    "ngày nào cũng",
    "cả tuần",
    "tất cả các ngày",
    "mọi ngày",
    "tối nào cũng",
    "sáng nào cũng",
    "chiều nào cũng",
)
_WEEKDAY_GROUP_TOKENS: tuple[str, ...] = (
    "các ngày trong tuần",
    "ngày thường",
    "thứ 2 đến thứ 6",
    "thứ hai đến thứ sáu",
)
_EVENT_RELATIVE_TOKENS: tuple[str, ...] = (
    "sau cuộc họp",
    "sau buổi họp",
    "sau sự kiện",
    "sau giờ họp",
    "sau khi họp",
)
_ALL_DAY_TOKENS: tuple[str, ...] = (
    "cả ngày",
    "nguyên ngày",
    "suốt ngày",
)


def _extract_single_spoken_weekday(phrase: str) -> int | None:
    """Trích xuất thứ đơn lẻ từ câu nói nếu câu không phải dạng khoảng 'từ ... đến ...'."""
    matched: list[int] = []
    for pattern, wd in _SPOKEN_WEEKDAY_PATTERNS:
        if pattern.search(phrase) and wd not in matched:
            matched.append(wd)
    return matched[0] if len(matched) == 1 else None


def _extract_single_word_weekday(phrase: str | None) -> int | None:
    """Chỉ bắt thứ viết bằng chữ tiếng Việt đơn lẻ (không bắt số 'thứ 2..7')."""
    if not phrase:
        return None
    word_matched: list[int] = []
    for pattern, wd in _WORD_WEEKDAY_PATTERNS:
        if pattern.search(phrase) and wd not in word_matched:
            word_matched.append(wd)
    if len(word_matched) != 1:
        return None
    spoken_matched: list[int] = []
    for pattern, wd in _SPOKEN_WEEKDAY_PATTERNS:
        if pattern.search(phrase) and wd not in spoken_matched:
            spoken_matched.append(wd)
    if len(spoken_matched) > 1:
        return None
    return word_matched[0]


_EXPLICIT_TIME_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:(?::|h|g)\s*(\d{2})?|giờ\s*(\d{2})?)\s*"
    r"(?:đến|tới|-|–|~)\s*"
    r"(\d{1,2})\s*(?:(?::|h|g)\s*(\d{2})?|giờ\s*(\d{2})?)(?!\d)",
    re.IGNORECASE,
)


def _extract_explicit_time_range(phrase: str | None) -> tuple[str, str] | None:
    """Trích xuất khoảng giờ HH:mm–HH:mm tường minh từ câu nói (VD: '15h-16h', '15:00 đến 16:30')."""
    if not phrase:
        return None
    match = _EXPLICIT_TIME_RANGE_RE.search(phrase)
    if match is None:
        return None
    sh = int(match.group(1))
    sm = int(match.group(2) or match.group(3) or 0)
    eh = int(match.group(4))
    em = int(match.group(5) or match.group(6) or 0)
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 24 and 0 <= em <= 59):
        return None
    if eh == 24:
        if em != 0:
            return None
        eh, em = 23, 59
    start_str = f"{sh:02d}:{sm:02d}"
    end_str = f"{eh:02d}:{em:02d}"
    if start_str >= end_str:
        return None
    return start_str, end_str


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
    model_config = ConfigDict(from_attributes=True)

    kind: FreeWindowKind = Field(default="weekly_rule")
    weekday: int | None = Field(
        default=None,
        ge=1,
        le=7,
        description="ISO weekday: 1=T2..5=T6,6=T7,7=CN (Thứ N -> N-1)",
    )
    weekdays: list[int] | None = Field(
        default=None,
        description="ISO weekday: 1=T2..5=T6,6=T7,7=CN (Thứ N -> N-1)",
    )
    date: str | None = Field(default=None)
    date_from: str | None = Field(default=None)
    date_to: str | None = Field(default=None)
    start_local: str | None = Field(default=None)
    end_local: str | None = Field(default=None)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    op: FreeWindowOp = Field(default="add")
    is_excluded: bool = Field(default=False)
    window_timezone: str | None = Field(default=None)
    clarification_hint: str | None = Field(default=None)
    raw_phrase: str = Field(..., min_length=1, max_length=200)
    is_ambiguous: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_and_flag_ambiguity(self) -> ExtractedFreeWindowSchema:
        """Kiểm chứng định dạng HH:mm / YYYY-MM-DD, ngữ cảnh văn nói #1..#13 và tự động gắn cờ is_ambiguous."""
        self.raw_phrase = self.raw_phrase.strip()
        if not self.raw_phrase:
            raise ValueError("raw_phrase không được để trống.")
        if self.clarification_hint is not None:
            self.clarification_hint = self.clarification_hint.strip() or None

        had_validation_error = False

        # 1. Đồng bộ is_excluded và op (#2, #3, #4)
        if self.is_excluded and self.op == "add":
            self.op = "exclude"
        elif self.op == "exclude":
            self.is_excluded = True

        # 2. Validate weekdays (B0.3)
        if self.weekdays is not None:
            cleaned_wds: list[int] = []
            invalid_wd = False
            for w in self.weekdays:
                if isinstance(w, int) and not isinstance(w, bool) and 1 <= w <= 7:
                    if w not in cleaned_wds:
                        cleaned_wds.append(w)
                else:
                    invalid_wd = True
            if invalid_wd or not cleaned_wds:
                self.weekdays = None
                had_validation_error = True
            else:
                self.weekdays = sorted(cleaned_wds)

        # 3. Validate date, date_from, date_to (B0.4)
        for date_attr in ("date", "date_from", "date_to"):
            d_val = getattr(self, date_attr)
            if d_val is not None:
                d_clean = d_val.strip()
                if _LOCAL_DATE_YYYYMMDD_RE.match(d_clean):
                    try:
                        date.fromisoformat(d_clean)
                        setattr(self, date_attr, d_clean)
                    except ValueError:
                        setattr(self, date_attr, None)
                        had_validation_error = True
                else:
                    setattr(self, date_attr, None)
                    had_validation_error = True

        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                had_validation_error = True
            elif self.kind == "weekly_rule" and self.weekday is None and not self.weekdays:
                self.kind = "date_range"

        # 4. Validate window_timezone (#13)
        if self.window_timezone is not None:
            tz_clean = self.window_timezone.strip()
            if not tz_clean:
                self.window_timezone = None
            else:
                try:
                    ZoneInfo(tz_clean)
                    self.window_timezone = tz_clean
                except (ZoneInfoNotFoundError, ValueError):
                    self.window_timezone = None
                    had_validation_error = True

        # 5. Validate start_local, end_local & chuẩn hóa nửa đêm "24:00" / "00:00" (#9)
        for attr_name in ("start_local", "end_local"):
            val = getattr(self, attr_name)
            if val is not None:
                cleaned = val.strip()
                if attr_name == "end_local" and cleaned == "24:00":
                    cleaned = "23:59"
                if _LOCAL_TIME_HHMM_RE.match(cleaned):
                    setattr(self, attr_name, cleaned)
                else:
                    setattr(self, attr_name, None)
                    had_validation_error = True

        if (
            self.start_local is not None
            and self.end_local == "00:00"
            and self.start_local > "00:00"
        ):
            self.end_local = "23:59"

        # 5b. Khôi phục giờ bận cụ thể từ raw_phrase khi op='exclude' bị bỏ trống giờ (TC-LIVE-04)
        if (
            (self.is_excluded or self.op == "exclude")
            and self.start_local is None
            and self.end_local is None
        ):
            recovered = _extract_explicit_time_range(self.raw_phrase)
            if recovered is not None:
                self.start_local, self.end_local = recovered

        # 6. Tính end_local từ duration_minutes nếu có start_local (#8)
        if (
            self.start_local is not None
            and self.end_local is None
            and self.duration_minutes is not None
        ):
            sh, sm = (int(part) for part in self.start_local.split(":"))
            end_total = sh * 60 + sm + self.duration_minutes
            if end_total == 1440:
                self.end_local = "23:59"
            elif end_total < 1440:
                self.end_local = f"{end_total // 60:02d}:{end_total % 60:02d}"
            elif end_total <= 1440 + 16 * 60:
                wrapped = end_total % 1440
                self.end_local = f"{wrapped // 60:02d}:{wrapped % 60:02d}"

        phrase_low = self.raw_phrase.lower()

        # 6b. Chuẩn hóa weekday từ thứ chữ tiếng Việt trước khi đối chiếu Thứ/Ngày (TC-LIVE-08)
        if self.weekday is not None and not self.weekdays:
            word_wd = _extract_single_word_weekday(self.raw_phrase)
            if word_wd is not None and word_wd != self.weekday:
                self.weekday = word_wd

        # 7. Ngữ cảnh #7: Phát hiện mâu thuẫn giữa Thứ và Ngày
        has_weekday_date_conflict = False
        if self.date is not None:
            expected_wd = (
                self.weekday
                if self.weekday is not None
                else _extract_single_spoken_weekday(self.raw_phrase)
            )
            actual_date = date.fromisoformat(self.date)
            if expected_wd is not None and actual_date.isoweekday() != expected_wd:
                has_weekday_date_conflict = True
                had_validation_error = True
                alt_date = actual_date + timedelta(days=expected_wd - actual_date.isoweekday())
                if not self.clarification_hint:
                    self.clarification_hint = (
                        f"Khung giờ '{self.raw_phrase}' đang có mâu thuẫn giữa thứ và ngày: "
                        f"bạn muốn xếp vào {alt_date.isoformat()} hay {self.date} "
                        f"cho khung {self.start_local or ''}–{self.end_local or ''}?"
                    )
            elif self.kind == "weekly_rule" and self.weekday is None and not self.weekdays:
                self.kind = "specific_date"

        # 8. Ngữ cảnh #12: Giờ phụ thuộc sự kiện ("Sau cuộc họp nhóm 30 phút thì mình rảnh hai tiếng")
        if self.start_local is None and any(tok in phrase_low for tok in _EVENT_RELATIVE_TOKENS):
            had_validation_error = True
            if self.duration_minutes is None:
                if "hai tiếng" in phrase_low or "hai giờ" in phrase_low:
                    self.duration_minutes = 120
                elif "một tiếng" in phrase_low or "một giờ" in phrase_low:
                    self.duration_minutes = 60
                elif m_hr := re.search(r"rảnh\s+(\d+)\s*(?:tiếng|giờ)", phrase_low):
                    self.duration_minutes = min(1440, max(1, int(m_hr.group(1)) * 60))
                elif m_min := re.search(r"rảnh\s+(\d+)\s*phút", phrase_low):
                    self.duration_minutes = min(1440, max(1, int(m_min.group(1))))
            if not self.clarification_hint:
                self.clarification_hint = (
                    f"Với khoảng rảnh '{self.raw_phrase}', cuộc họp/sự kiện diễn ra vào ngày nào "
                    "và dự kiến kết thúc lúc mấy giờ để mình tính khung giờ bắt đầu chính xác?"
                )

        # 9. Ngữ cảnh #8: Câu hở đầu ra ("Tối nay rảnh sau 19h30")
        if (
            self.start_local is not None
            and self.end_local is None
            and self.duration_minutes is None
            and not self.is_excluded
            and self.op in ("add", "replace_day")
        ):
            had_validation_error = True
            if not self.clarification_hint:
                self.clarification_hint = (
                    f"Với khung giờ '{self.raw_phrase}' (bắt đầu từ {self.start_local}), "
                    "bạn có thể làm đến mấy giờ kết thúc?"
                )

        # 10. Ngữ cảnh #9: "Mai rảnh cả ngày" thiếu giới hạn giờ trong ngày
        if (
            not self.is_excluded
            and self.op in ("add", "replace_day")
            and any(tok in phrase_low for tok in _ALL_DAY_TOKENS)
            and (
                self.start_local is None
                or self.end_local is None
                or (
                    self.start_local == "00:00"
                    and self.end_local == "23:59"
                    and not re.search(r"\b\d{1,2}\s*(?:h|:|giờ)\b", phrase_low)
                )
            )
        ):
            had_validation_error = True
            if not self.clarification_hint:
                self.clarification_hint = (
                    f"Với '{self.raw_phrase}', bạn muốn giới hạn khung giờ làm việc trong ngày "
                    "từ mấy giờ sáng đến mấy giờ tối (để tránh giờ ăn và giờ nghỉ ngơi)?"
                )

        # 11. Fallback suy luận neo ngày/thứ từ raw_phrase & tự sửa lệch Thứ chữ tiếng Việt (B0.1, B0.3)
        if (
            self.weekday is None
            and not self.weekdays
            and self.date is None
            and self.date_from is None
        ):
            if any(tok in phrase_low for tok in _DAILY_REPEAT_TOKENS):
                self.kind = "daily"
            elif "cuối tuần" in phrase_low:
                self.kind = "weekly_rule"
                self.weekdays = [6, 7]
            elif any(tok in phrase_low for tok in _WEEKDAY_GROUP_TOKENS):
                self.kind = "weekly_rule"
                self.weekdays = [1, 2, 3, 4, 5]
            elif self.kind == "weekly_rule" and not had_validation_error:
                self.weekday = _extract_single_spoken_weekday(self.raw_phrase)
        elif self.kind == "weekly_rule" and self.date is None and self.date_from is None:
            word_wd = _extract_single_word_weekday(self.raw_phrase)
            if word_wd is not None:
                if self.weekday is not None and not self.weekdays and word_wd != self.weekday:
                    self.weekday = word_wd
                elif self.weekdays and len(self.weekdays) == 1 and word_wd != self.weekdays[0]:
                    self.weekdays = [word_wd]

        # 12. Quyết định is_ambiguous cuối cùng theo toán tử op
        has_date_range = (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from <= self.date_to
        )
        if self.op == "reset_override":
            has_valid_anchor = self.date is not None or has_date_range
            has_valid_time = True
        elif self.op == "exclude" or self.is_excluded:
            has_valid_anchor = (
                self.weekday is not None
                or bool(self.weekdays)
                or self.date is not None
                or has_date_range
            )
            has_valid_time = (self.start_local is None and self.end_local is None) or (
                self.start_local is not None
                and self.end_local is not None
                and self.start_local < self.end_local
            )
        else:
            if self.kind == "daily":
                has_valid_anchor = True
            elif self.kind == "weekly_rule":
                has_valid_anchor = self.weekday is not None or bool(self.weekdays)
            elif self.kind == "specific_date":
                has_valid_anchor = self.date is not None
            elif self.kind == "date_range":
                has_valid_anchor = has_date_range
            else:
                has_valid_anchor = False

            if self.start_local is None or self.end_local is None:
                has_valid_time = False
            elif self.start_local < self.end_local:
                has_valid_time = True
            elif self.start_local > self.end_local:
                sh, sm = (int(part) for part in self.start_local.split(":"))
                eh, em = (int(part) for part in self.end_local.split(":"))
                start_min = sh * 60 + sm
                end_min = eh * 60 + em
                overnight_minutes = (1440 - start_min) + end_min
                has_valid_time = 0 < overnight_minutes <= 16 * 60
            else:
                has_valid_time = False

        self.is_ambiguous = bool(
            had_validation_error
            or has_weekday_date_conflict
            or not has_valid_anchor
            or not has_valid_time
        )
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
    kind: Literal["deadline", "window"] | None = None
    start_at: datetime | None = None
    deadline_at: datetime | None = None
    finish_by_at: datetime | None = None
    input_timezone: str | None = None


def _strip_schema_titles(schema: dict[str, Any]) -> None:
    """Loại bỏ trường 'title' tự sinh và default dư thừa khỏi JSON Schema để giảm token gửi LLM."""
    schema.pop("title", None)
    schema.pop("default", None)
    if "properties" in schema:
        schema.pop("description", None)
        props = schema["properties"]
        if isinstance(props, dict):
            for prop_schema in props.values():
                if isinstance(prop_schema, dict):
                    _strip_schema_titles(prop_schema)
    for key in ("$defs", "definitions"):
        defs = schema.get(key)
        if isinstance(defs, dict):
            for def_schema in defs.values():
                if isinstance(def_schema, dict):
                    _strip_schema_titles(def_schema)
    for key in ("items", "additionalProperties"):
        sub = schema.get(key)
        if isinstance(sub, dict):
            _strip_schema_titles(sub)
    for key in ("anyOf", "allOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    _strip_schema_titles(variant)


class LLMTaskExtractionPayload(BaseModel):
    model_config = ConfigDict(json_schema_extra=_strip_schema_titles)

    title: str | None = Field(
        default=None,
        max_length=100,
        description="Tên ngắn<=100 ký tự, BẮT BUỘC sinh từ content",
    )
    goal: str | None = None
    time_constraint: ExtractedTimeConstraint | None = None
    clarification_issues: list[ClarificationIssue] = Field(default_factory=list)
    resolved_issue_keys: list[str] = Field(default_factory=list)
    field_review_notes: dict[str, str] = Field(default_factory=dict)
    content: str | None = Field(default=None)
    scope: str | None = Field(default=None)
    free_time_slots: list[str] = Field(default_factory=list)
    availability_update_mode: Literal["merge", "replace"] = Field(default="merge")
    structured_free_windows: list[ExtractedFreeWindowSchema] = Field(default_factory=list)
    deadline: datetime | None = Field(default=None)
    extra_requirements: list[str] = Field(default_factory=list)
    is_complete: bool = Field(default=False)
    has_conflict: bool = Field(default=False)
    summary: str = Field(..., min_length=1)
    clarification_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def inherit_same_time_windows(self) -> LLMTaskExtractionPayload:
        """Kế thừa giờ bắt đầu–kết thúc cho window 'cũng giờ đó' (#6) từ window dương liền trước."""
        if not self.structured_free_windows:
            return self
        resolved: list[ExtractedFreeWindowSchema] = []
        last_start: str | None = None
        last_end: str | None = None
        for item in self.structured_free_windows:
            phrase_low = item.raw_phrase.lower()
            if (
                item.start_local is None
                and item.end_local is None
                and not item.is_excluded
                and item.op in ("add", "replace_day")
                and last_start is not None
                and last_end is not None
                and any(tok in phrase_low for tok in _SAME_TIME_INHERIT_TOKENS)
            ):
                payload = item.model_dump()
                payload["start_local"] = last_start
                payload["end_local"] = last_end
                payload["is_ambiguous"] = False
                payload["clarification_hint"] = None
                item = ExtractedFreeWindowSchema.model_validate(payload)
            if (
                item.start_local is not None
                and item.end_local is not None
                and not item.is_excluded
                and item.op in ("add", "replace_day")
            ):
                last_start = item.start_local
                last_end = item.end_local
            resolved.append(item)
        self.structured_free_windows = resolved
        return self


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

        if self.title is not None and not self.title.strip():
            self.title = None

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

        ambiguous_windows = [w for w in self.structured_free_windows if w.is_ambiguous]
        specific_hints = [
            w.clarification_hint for w in ambiguous_windows if w.clarification_hint
        ]
        if not self.free_time_slots:
            missing_questions.append(
                "Bạn thường rảnh vào những khung giờ nào để mình xếp phiên tập trung phù hợp?"
            )
        elif ambiguous_windows:
            unhinted_phrases = [
                w.raw_phrase for w in ambiguous_windows if not w.clarification_hint
            ]
            if specific_hints:
                missing_questions.extend(dict.fromkeys(specific_hints))
            if unhinted_phrases:
                joined_phrases = ", ".join(f"'{p}'" for p in unhinted_phrases)
                missing_questions.append(
                    f"Khung giờ {joined_phrases} bạn có thể làm từ mấy giờ đến mấy giờ cụ thể để mình xếp lịch chuẩn xác?"
                )

        has_tc_end = self.time_constraint is not None and (
            self.time_constraint.deadline_at is not None
            or self.time_constraint.finish_by_at is not None
        )
        if self.deadline is None and has_tc_end and self.time_constraint is not None:
            self.deadline = self.time_constraint.deadline_at or self.time_constraint.finish_by_at
        if self.deadline is None and not has_tc_end:
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
            elif specific_hints:
                for hint in dict.fromkeys(specific_hints):
                    if hint not in self.clarification_questions:
                        self.clarification_questions.append(hint)
        elif self.is_complete:
            self.clarification_questions = []

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
