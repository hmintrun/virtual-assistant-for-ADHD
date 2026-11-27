"""Mini-Duckling phân tích biểu thức thời gian tiếng Việt (Trụ cột 2b — Deterministic AST).

Trích xuất tất định theo tiểu mệnh đề (`TemporalAnchor`):
- Thứ tiếng Việt (`Thứ 2..7` -> ISO `1..6`, `Chủ nhật`/`CN` -> `7`), chặn dương tính giả số thứ tự
  (`lần/bước/phần/chương/tuần/ngày/buổi/mục/câu/bài thứ N`).
- Ngày lịch tường minh (`ngày DD tháng MM [năm YYYY]`, `DD/MM[/YYYY]`, `YYYY-MM-DD`).
- Ngày tương đối (`hôm nay`, `tối/sáng/chiều/trưa/đêm/khuya nay`, `ngày mai`, `mai`, `mốt`, `tuần sau`, `cuối tuần`).
- Phát hiện mâu thuẫn Thứ ↔ Ngày (`weekday_date_conflict`) khi cả 2 cùng xuất hiện trong 1 span.
- Giờ 24h (`start_local`, `end_local`) và cờ `has_explicit_clock_time`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from app.utils.vietnamese_itn import (
    _TIME_CORE_PAT,
    _clean_period,
    _parse_time_core,
    _resolve_24h_time,
    normalize_vietnamese_transcript,
)

PeriodType = Literal["sáng", "trưa", "chiều", "tối", "đêm", "khuya"]

_VALID_PERIODS: dict[str, PeriodType] = {
    "sáng": "sáng",
    "trưa": "trưa",
    "chiều": "chiều",
    "tối": "tối",
    "đêm": "đêm",
    "khuya": "khuya",
}

_PUNCT_SPLIT_RE = re.compile(r"[,;!?\n]+|\.(?=\s|$)")
_TRANSITION_SPLIT_RE = re.compile(
    r"(?<!\w)(?=hạn(?:\s+chót)?\b|deadline\b|nộp(?:\s+trước|\s+vào)?\b|trừ\b|ngoại\s+trừ\b|riêng\b"
    r"|(?<!cuộc\s)(?<!lịch\s)(?<!buổi\s)(?<!đi\s)(?<!có\s)hẹn"
    r"(?=\s+(?:tối|sáng|trưa|chiều|đêm|khuya|buổi|thứ|chủ\s+nhật|cn|ngày|hôm|mai|mốt|cuối\s+tuần|tuần|\d)))",
    re.IGNORECASE,
)
_CONJ_SPLIT_RE = re.compile(
    r"(?:\d[ \t]*(?:(?-i:[hHg])|p|phút)?|(?<!\w)(?:giờ|tiếng|rưỡi|đúng|sáng|trưa|chiều|tối|đêm|khuya"
    r"|nay|mai|mốt|qua|tuần|nhật|cn|hai|ba|tư|bốn|năm|sáu|bảy|bẩy))"
    r"\s+(?:và|với|còn|nhưng|rồi)\s+"
    r"(?=(?:(?:rảnh|bận|từ|lúc|vào|buổi)\s+)*(?:sáng|trưa|chiều|tối|đêm|khuya|thứ|chủ\s+nhật|cn|hôm\s+nay|ngày\s+mai|mai|mốt|ngày\s+\d|\d{1,2}/|(?:1[3-9]|2[0-4])(?::|(?-i:[hHg])\b|(?-i:[hHg])\d|\s*giờ\b)))",
    re.IGNORECASE,
)

_ORDINAL_FALSE_POSITIVE_RE = re.compile(
    r"\b(?:lần|bước|phần|chương|tuần|ngày|buổi|mục|câu|bài|phương\s+án|ý|hạng|tập|vị\s+trí)\s+thứ\s+"
    r"(?:\d+|nhất|hai|ba|tư|bốn|năm|sáu|bảy|bẩy)\b",
    re.IGNORECASE,
)
_REPLACED_COMPARAND_RE = re.compile(
    r"\bthay\s+vì\s+(?:(?:buổi\s+)?(?:sáng|trưa|chiều|tối|đêm|khuya)\s+)?"
    r"(?:thứ\s*(?:[2-7]|hai|ba|tư|bốn|năm|sáu|bảy|bẩy)|chủ\s+nhật|cn"
    r"|(?:ngày\s+)?\d{1,2}(?:/\d{1,2}(?:/\d{4})?|\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?)"
    r"|hôm\s+nay|ngày\s+mai|mai|mốt)\b",
    re.IGNORECASE,
)

_WEEKDAY_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?<!\w)thứ\s*(?:2|hai)(?!\w)", re.IGNORECASE), 1),
    (
        re.compile(
            r"(?<!\w)thứ\s*(?:3|ba)(?!\w)"
            r"(?![ \t]+(?:mươi|chục|trăm|nghìn|ngàn|triệu|tỷ|mốt|lăm|nhăm)\b)",
            re.IGNORECASE,
        ),
        2,
    ),
    (
        re.compile(
            r"(?<!\w)thứ\s*(?:4|tư|bốn)(?!\w)"
            r"(?![ \t]+(?:duy|vấn|liệu|cách|thế|chất|tưởng)\b)",
            re.IGNORECASE,
        ),
        3,
    ),
    (re.compile(r"(?<!\w)thứ\s*(?:5|năm)(?!\w)", re.IGNORECASE), 4),
    (re.compile(r"(?<!\w)thứ\s*(?:6|sáu)(?!\w)", re.IGNORECASE), 5),
    (re.compile(r"(?<!\w)thứ\s*(?:7|bảy|bẩy)(?!\w)", re.IGNORECASE), 6),
    (re.compile(r"(?<!\w)(?:chủ\s+nhật|cn)(?!\w)", re.IGNORECASE), 7),
)

_PERIOD_WORD_RE = re.compile(
    r"(?<!\w)(?:buổi[ \t]+)?"
    r"(?P<p>sáng(?![ \t]+(?:tạo|kiến|suốt|lập|chế)\b)"
    r"|trưa"
    r"|chiều(?![ \t]+(?:dài|rộng|cao|sâu|hướng)\b)"
    r"|tối(?![ \t]+(?:đa|thiểu|ưu|hậu)\b)"
    r"|đêm"
    r"|khuya)(?!\w)",
    re.IGNORECASE,
)

_TEXT_DATE_RE = re.compile(
    r"(?<!\w)(?<!\d)(?:(?:ngày|mùng|mồng)[ \t]+)?(\d{1,2})[ \t]+tháng[ \t]+(\d{1,2})"
    r"(?:[ \t]+năm[ \t]+(\d{4}))?(?!\w)",
    re.IGNORECASE,
)
_SLASH_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?!\d)")
_SLASH_PERCENT_SUFFIX_RE = re.compile(r"^[ \t]*%")
_SLASH_CALENDAR_PREFIX_RE = re.compile(
    r"(?<!\w)(?:"
    r"ngày|mùng|mồng|hạn(?:[ \t]+chót)?|deadline|nộp|trước|vào|từ|đến|tới"
    r"|thứ(?:[ \t]*(?:[2-7]|hai|ba|tư|bốn|năm|sáu|bảy|bẩy))?"
    r"|chủ[ \t]+nhật|cn"
    r")[ \t]*$",
    re.IGNORECASE,
)
_SLASH_UNIT_SUFFIX_RE = re.compile(
    r"^[ \t]*(?:bài|câu|phần|chương|trang|slide|điểm|phút|giờ|lần)\b",
    re.IGNORECASE,
)
_SLASH_RATIO_PREFIX_RE = re.compile(
    r"(?<!\w)(?:điểm|đạt|tỷ[ \t]*lệ|tỉ[ \t]*lệ|làm|xong|hoàn[ \t]*thành|đọc|viết|giải)"
    r"(?:[ \t]+(?:được|xong))?[ \t]*$",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")

_DEICTIC_TODAY_RE = re.compile(
    r"(?<!\w)(?:hôm\s+nay|(?:sáng|trưa|chiều|tối|đêm|khuya)\s+nay)(?!\w)",
    re.IGNORECASE,
)
_DEICTIC_TOMORROW_RE = re.compile(r"(?<!\w)(?:ngày\s+)?mai(?!\w)", re.IGNORECASE)
_DEICTIC_MOT_RE = re.compile(r"(?<!\w)(?:ngày\s+)?mốt(?!\w)", re.IGNORECASE)
_DEICTIC_YESTERDAY_RE = re.compile(r"(?<!\w)hôm\s+qua(?!\w)", re.IGNORECASE)

_NEXT_WEEK_RE = re.compile(r"(?<!\w)tuần\s+(?:sau|tới)(?!\w)", re.IGNORECASE)
_THIS_WEEK_RE = re.compile(r"(?<!\w)tuần\s+này(?!\w)", re.IGNORECASE)
_WEEKEND_RE = re.compile(r"(?<!\w)cuối\s+tuần(?!\w)", re.IGNORECASE)

_DURATION_GIO_GUARD_RE = re.compile(
    r"(?:(?<!\blúc[ \t])(?<!\bvào[ \t])(?<!\btừ[ \t])(?<!\btrước[ \t])(?<!\bsau[ \t])"
    r"(?<!\w)(?:tối\s+đa|ít\s+nhất|trong|mất|khoảng|tầm|cỡ|hơn|dưới|trên|được"
    r"|làm|học|ôn|viết|đọc|ngủ|nghỉ|tốn|dành|kéo\s+dài)\s+"
    r"\d{1,2}\s+giờ(?![ \t]*(?:\d|rưỡi|kém|đến|tới|-|–)\b)"
    r"|\b\d{1,2}\s+giờ[ \t]+đồng[ \t]+hồ\b)",
    re.IGNORECASE,
)
_BARE_G_TIME_PREFIX_RE = re.compile(
    r"(?<!\w)(?:lúc|vào|từ|đến|tới|trước|sau|khoảng|tầm|cỡ)[ \t]*$",
    re.IGNORECASE,
)
_CLOCK_RANGE_RE = re.compile(
    r"(?<![\d/])(?P<sh>2[0-4]|1\d|0?\d)[ \t]*"
    r"(?P<s_unit>(?::|(?-i:[hHg]))[ \t]*(?P<sm>[0-5]\d)?|giờ(?:[ \t]+(?P<sm2>[0-5]\d))?)?"
    r"[ \t]*(?:đến|tới|-|–|~)[ \t]*(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    r"(?:(?:buổi[ \t]+)?(?P<e_period>sáng(?![ \t]+(?:tạo|kiến|suốt|lập|chế)\b)|trưa|chiều(?![ \t]+(?:dài|rộng|cao|sâu|hướng)\b)|tối(?![ \t]+(?:đa|thiểu|ưu|hậu)\b)|đêm|khuya)(?!\w)[ \t]+)?"
    r"(?P<eh>2[0-4]|1\d|0?\d)[ \t]*"
    r"(?P<e_unit>(?::|(?-i:[hHg]))[ \t]*(?P<em>[0-5]\d)?|giờ(?:[ \t]+(?P<em2>[0-5]\d))?)"
    r"(?![ \t]*kém\b)(?!\d)",
    re.IGNORECASE,
)
_SINGLE_CLOCK_RE = re.compile(
    r"(?<![\d/])(?P<h>2[0-4]|1\d|0?\d)[ \t]*(?:"
    r":(?P<colon_m>[0-5]\d)"
    r"|(?P<hg_unit>(?-i:[hHg]))[ \t]*(?P<hg_m>[0-5]\d)?"
    r"|giờ(?:[ \t]+(?P<gio_m>[0-5]\d)|[ \t]+(?P<gio_ruoi>rưỡi))?"
    r")(?![ \t]*kém\b)(?![ \t]*đồng[ \t]+hồ\b)(?!\w)",
    re.IGNORECASE,
)
_TIME_CORE_TOKEN_RE = re.compile(_TIME_CORE_PAT, re.IGNORECASE)
_ANY_TEMPORAL_SIGNAL_RE = re.compile(
    r"(?:"
    r"thứ\s*(?:[2-7]|hai|ba|tư|bốn|năm|sáu|bảy|bẩy)|chủ\s+nhật|\bcn\b|"
    r"hôm\s+nay|hôm\s+qua|ngày\s+mai|\bmai\b|\bmốt\b|"
    r"(?:sáng|trưa|chiều|tối|đêm|khuya)|"
    r"tuần\s+(?:sau|tới|này)|cuối\s+tuần|"
    r"\d{1,2}/\d{1,2}|\d{1,2}\s+tháng\s+\d{1,2}|\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}:\d{2}|\d{1,2}(?-i:[hHg])(?:\d{2})?|\d{1,2}\s*giờ"
    r")",
    re.IGNORECASE,
)


class TemporalAnchor(BaseModel):
    """Nút thời gian tiếng Việt có neo vị trí và kiểm chứng lịch tất định."""

    model_config = ConfigDict(from_attributes=True)

    raw_span: str
    span_start: int
    span_end: int
    period: PeriodType | None = None
    iso_weekday: int | None = None
    explicit_date: str | None = None
    resolved_date: str | None = None
    weekday_date_conflict: bool = False
    start_local: str | None = None
    end_local: str | None = None
    has_explicit_clock_time: bool = False
    is_single_day_deictic: bool = False
    is_next_week: bool = False


def _resolve_local_now(reference_now: datetime, timezone: str) -> datetime:
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    if reference_now.tzinfo is None:
        return reference_now.replace(tzinfo=tz)
    return reference_now.astimezone(tz)


def _mask_with_spaces(text: str, start: int, end: int) -> str:
    return text[:start] + (" " * (end - start)) + text[end:]


def _infer_year(ref_date: date, month: int) -> int:
    if ref_date.month >= 11 and month <= 2:
        return ref_date.year + 1
    return ref_date.year


def _trim_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def _split_clauses_with_offsets(text: str) -> list[tuple[int, int]]:
    """Tách văn bản thành các tiểu mệnh đề và giữ chính xác chỉ số ký tự gốc."""
    punct_spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _PUNCT_SPLIT_RE.finditer(text):
        trimmed = _trim_span(text, cursor, match.start())
        if trimmed is not None:
            punct_spans.append(trimmed)
        cursor = match.end()
    trimmed_tail = _trim_span(text, cursor, len(text))
    if trimmed_tail is not None:
        punct_spans.append(trimmed_tail)

    final_spans: list[tuple[int, int]] = []
    for p_start, p_end in punct_spans:
        sub_text = text[p_start:p_end]
        split_points: list[int] = [0]

        for m in _TRANSITION_SPLIT_RE.finditer(sub_text):
            pos = m.start()
            if pos > 0 and _ANY_TEMPORAL_SIGNAL_RE.search(sub_text[:pos]):
                split_points.append(pos)

        for m in _CONJ_SPLIT_RE.finditer(sub_text):
            conj_start = m.start()
            # Tách sau vế có tín hiệu thời gian khi nối sang vế thời gian mới
            match_conj = re.search(r"\s+(?:và|với|còn|nhưng|rồi)\s+", sub_text[conj_start:], re.IGNORECASE)
            if match_conj is not None:
                after_conj = conj_start + match_conj.end()
                if _ANY_TEMPORAL_SIGNAL_RE.search(sub_text[: conj_start + match_conj.start()]):
                    split_points.append(after_conj)

        ordered_points = sorted(set(split_points))
        ordered_points.append(len(sub_text))
        for idx in range(len(ordered_points) - 1):
            trimmed = _trim_span(text, p_start + ordered_points[idx], p_start + ordered_points[idx + 1])
            if trimmed is not None:
                final_spans.append(trimmed)
    return final_spans


def _is_slash_fraction_or_ratio(
    masked_span: str,
    ds: int,
    de: int,
    dy: int,
    mo: int,
) -> bool:
    """Loại bỏ phân số, tỷ lệ %, điểm số hoặc '24/7' khi biểu thức DD/MM không có năm."""
    suffix = masked_span[de:]
    if _SLASH_PERCENT_SUFFIX_RE.search(suffix):
        return True
    prefix = masked_span[:ds]
    if _SLASH_CALENDAR_PREFIX_RE.search(prefix):
        return False
    if dy == 24 and mo == 7:
        return True
    if _SLASH_UNIT_SUFFIX_RE.search(suffix):
        return True
    if _SLASH_RATIO_PREFIX_RE.search(prefix):
        return True
    return False


def _apply_period_to_hour(hour: int, period: PeriodType | None, *, is_end: bool = False) -> int:
    if hour >= 13 or hour == 0:
        return 23 if (hour == 24 and is_end) else hour
    if period is None:
        return hour
    if period == "sáng":
        return 0 if hour == 12 else hour
    if period == "trưa":
        return hour + 12 if 1 <= hour <= 4 else hour
    if period == "chiều":
        return hour + 12 if 1 <= hour <= 11 else hour
    if period == "tối":
        if 1 <= hour <= 11:
            return hour + 12
        return 23 if (hour == 12 and is_end) else hour
    # đêm / khuya
    if 6 <= hour <= 11:
        return hour + 12
    if hour == 12:
        return 23 if is_end else 0
    return hour


def _extract_clock_times(
    masked_span: str,
    period: PeriodType | None,
) -> tuple[str | None, str | None, bool]:
    """Trích ``start_local``, ``end_local`` 24h và cờ ``has_explicit_clock_time`` từ span đã che ngày/số thứ tự."""
    clean_for_clock = masked_span
    for dm in _DURATION_GIO_GUARD_RE.finditer(clean_for_clock):
        clean_for_clock = _mask_with_spaces(clean_for_clock, dm.start(), dm.end())

    normalized = normalize_vietnamese_transcript(clean_for_clock)

    if rm := _CLOCK_RANGE_RE.search(normalized):
        sh = int(rm.group("sh"))
        sm = int(rm.group("sm") or rm.group("sm2") or 0)
        eh = int(rm.group("eh"))
        em = int(rm.group("em") or rm.group("em2") or 0)
        if 0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 24 and 0 <= em <= 59:
            has_colon_24h = (
                rm.group("s_unit") is not None
                and ":" in rm.group("s_unit")
                and ":" in rm.group("e_unit")
                and (sh >= 13 or eh >= 13)
            )
            e_lead_raw = _clean_period(rm.group("e_period"))
            e_lead_period = _VALID_PERIODS.get(e_lead_raw) if e_lead_raw else None
            if has_colon_24h:
                s_eff_period: PeriodType | None = None
                e_eff_period: PeriodType | None = None
            elif e_lead_period is not None:
                pre_periods = [
                    _clean_period(pm.group("p"))
                    for pm in _PERIOD_WORD_RE.finditer(normalized[: rm.start()])
                ]
                valid_pre = [
                    _VALID_PERIODS[p] for p in pre_periods if p is not None and p in _VALID_PERIODS
                ]
                s_eff_period = valid_pre[-1] if valid_pre else None
                e_eff_period = e_lead_period
            else:
                s_eff_period = period
                e_eff_period = period
            sh_res = _apply_period_to_hour(sh, s_eff_period, is_end=False)
            eh_res = 23 if eh == 24 and em == 0 else _apply_period_to_hour(eh, e_eff_period, is_end=True)
            if eh == 24 and em == 0:
                em = 59
            if (
                s_eff_period in ("sáng", "trưa")
                and e_lead_period is None
                and eh_res * 60 + em <= sh_res * 60 + sm
                and 1 <= eh <= 11
            ):
                eh_res = eh + 12
            if 0 <= sh_res <= 23 and 0 <= eh_res <= 23:
                return f"{sh_res:02d}:{sm:02d}", f"{eh_res:02d}:{em:02d}", True

    for sm in _SINGLE_CLOCK_RE.finditer(normalized):
        hg_unit = sm.group("hg_unit")
        if hg_unit == "g" and sm.group("hg_m") is None:
            if not _BARE_G_TIME_PREFIX_RE.search(normalized[: sm.start()]):
                continue
        h = int(sm.group("h"))
        if sm.group("gio_ruoi"):
            m = 30
        else:
            m = int(sm.group("colon_m") or sm.group("hg_m") or sm.group("gio_m") or 0)
        if 0 <= h <= 24 and 0 <= m <= 59:
            if h == 24 and m == 0:
                return "23:59", None, True
            if sm.group("colon_m") is not None or h >= 13 or period is not None:
                h_res = _apply_period_to_hour(h, period, is_end=False)
                if 0 <= h_res <= 23:
                    return f"{h_res:02d}:{m:02d}", None, True
            return None, None, True

    # Fallback kiểm tra token giờ nói bằng chữ (VD: "bảy giờ", "tám rưỡi") chưa qua period
    for cm in _TIME_CORE_TOKEN_RE.finditer(clean_for_clock):
        if re.fullmatch(r"\d{1,2}[ \t]*g", cm.group(0).strip()) and not _BARE_G_TIME_PREFIX_RE.search(
            clean_for_clock[: cm.start()]
        ):
            continue
        parsed = _parse_time_core(cm.group(0))
        if parsed is None:
            continue
        if period is not None:
            resolved = _resolve_24h_time(parsed, period)
            if resolved is not None:
                if resolved == "24:00":
                    resolved = "23:59"
                return resolved, None, True
        return None, None, True

    return None, None, False


def extract_temporal_anchors(
    text: str,
    *,
    reference_now: datetime,
    timezone: str = "Asia/Ho_Chi_Minh",
) -> list[TemporalAnchor]:
    """Phân tích câu tiếng Việt theo tiểu mệnh đề thành danh sách ``TemporalAnchor`` tất định."""
    if not text or not text.strip():
        return []

    norm_text = unicodedata.normalize("NFC", text)
    local_now = _resolve_local_now(reference_now, timezone)
    ref_date = local_now.date()
    ref_wd = ref_date.isoweekday()

    anchors: list[TemporalAnchor] = []
    for span_start, span_end in _split_clauses_with_offsets(norm_text):
        raw_span = norm_text[span_start:span_end]
        masked_wd_span = raw_span
        for om in _ORDINAL_FALSE_POSITIVE_RE.finditer(raw_span):
            masked_wd_span = _mask_with_spaces(masked_wd_span, om.start(), om.end())
        for rm_comp in _REPLACED_COMPARAND_RE.finditer(masked_wd_span):
            masked_wd_span = _mask_with_spaces(masked_wd_span, rm_comp.start(), rm_comp.end())

        # 1. Weekdays
        matched_weekdays: list[int] = []
        for pattern, wd in _WEEKDAY_PATTERNS:
            if pattern.search(masked_wd_span) and wd not in matched_weekdays:
                matched_weekdays.append(wd)
        iso_weekday = matched_weekdays[0] if len(matched_weekdays) == 1 else None

        # 2. Explicit calendar dates (mask them out before clock extraction)
        masked_clock_span = masked_wd_span
        parsed_dates: list[date] = []
        occupied_date_spans: list[tuple[int, int]] = []

        for pattern, is_iso in (
            (_ISO_DATE_RE, True),
            (_TEXT_DATE_RE, False),
            (_SLASH_DATE_RE, False),
        ):
            for dm in pattern.finditer(masked_wd_span):
                ds, de = dm.start(), dm.end()
                if any(ds < oe and de > os for os, oe in occupied_date_spans):
                    continue
                try:
                    if is_iso:
                        yr, mo, dy = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
                    else:
                        dy, mo = int(dm.group(1)), int(dm.group(2))
                        if (
                            pattern is _SLASH_DATE_RE
                            and dm.group(3) is None
                            and _is_slash_fraction_or_ratio(masked_wd_span, ds, de, dy, mo)
                        ):
                            continue
                        yr = int(dm.group(3)) if dm.group(3) else _infer_year(ref_date, mo)
                    d_obj = date(yr, mo, dy)
                except ValueError:
                    continue
                occupied_date_spans.append((ds, de))
                masked_clock_span = _mask_with_spaces(masked_clock_span, ds, de)
                if d_obj not in parsed_dates:
                    parsed_dates.append(d_obj)

        explicit_date: str | None = parsed_dates[0].isoformat() if len(parsed_dates) == 1 else None

        # 3. Period
        periods = [
            _clean_period(pm.group("p"))
            for pm in _PERIOD_WORD_RE.finditer(masked_clock_span)
        ]
        period_val: PeriodType | None = None
        valid_periods = [_VALID_PERIODS[p] for p in periods if p is not None and p in _VALID_PERIODS]
        if valid_periods:
            period_val = valid_periods[-1]

        # 4. Relative day & week markers
        is_next_week = bool(_NEXT_WEEK_RE.search(masked_wd_span))
        is_this_week = bool(_THIS_WEEK_RE.search(masked_wd_span))
        has_weekend = bool(_WEEKEND_RE.search(masked_wd_span))

        deictic_dates: list[date] = []
        if _DEICTIC_TODAY_RE.search(masked_wd_span):
            deictic_dates.append(ref_date)
        if _DEICTIC_TOMORROW_RE.search(masked_wd_span):
            deictic_dates.append(ref_date + timedelta(days=1))
        if _DEICTIC_MOT_RE.search(masked_wd_span):
            deictic_dates.append(ref_date + timedelta(days=2))
        if _DEICTIC_YESTERDAY_RE.search(masked_wd_span):
            deictic_dates.append(ref_date - timedelta(days=1))

        unique_deictic = list(dict.fromkeys(deictic_dates))
        is_single_day_deictic = len(unique_deictic) == 1

        # 5. Conflict & resolved_date
        weekday_date_conflict = False
        if explicit_date is not None and iso_weekday is not None:
            if date.fromisoformat(explicit_date).isoweekday() != iso_weekday:
                weekday_date_conflict = True

        resolved_date: str | None = None
        if explicit_date is not None:
            resolved_date = explicit_date
        elif is_single_day_deictic:
            resolved_date = unique_deictic[0].isoformat()
        elif iso_weekday is not None:
            if is_next_week:
                resolved_date = (
                    ref_date + timedelta(days=(7 - ref_wd) + iso_weekday)
                ).isoformat()
            elif is_this_week:
                resolved_date = (ref_date + timedelta(days=iso_weekday - ref_wd)).isoformat()
            else:
                delta = (iso_weekday - ref_wd) % 7
                resolved_date = (ref_date + timedelta(days=delta)).isoformat()

        # 6. Clock extraction
        start_local, end_local, has_explicit_clock_time = _extract_clock_times(
            masked_clock_span, period_val
        )

        has_any_feature = any(
            (
                period_val is not None,
                iso_weekday is not None,
                explicit_date is not None,
                resolved_date is not None,
                start_local is not None,
                end_local is not None,
                has_explicit_clock_time,
                is_single_day_deictic,
                is_next_week,
                has_weekend,
            )
        )
        if not has_any_feature:
            continue

        anchors.append(
            TemporalAnchor(
                raw_span=raw_span,
                span_start=span_start,
                span_end=span_end,
                period=period_val,
                iso_weekday=iso_weekday,
                explicit_date=explicit_date,
                resolved_date=resolved_date,
                weekday_date_conflict=weekday_date_conflict,
                start_local=start_local,
                end_local=end_local,
                has_explicit_clock_time=has_explicit_clock_time,
                is_single_day_deictic=is_single_day_deictic,
                is_next_week=is_next_week,
            )
        )

    return anchors
