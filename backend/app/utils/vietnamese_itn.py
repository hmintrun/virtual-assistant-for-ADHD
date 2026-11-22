"""Chuẩn hóa văn bản giọng nói tiếng Việt (Vietnamese ITN & Disfluency Removal) cho UF1 (T3.1, T3.2).

Bộ tiền xử lý tất định (deterministic, < 5ms/request, không phụ thuộc thư viện ngoài):
- T3.2: Lọc từ đệm ngập ngừng đứng độc lập (ờm, ừm, à thì, ơ kìa, ờ, ừ) và gộp từ chức năng lặp
  do nói lắp (thứ, thứ hai -> thứ hai), bảo vệ tuyệt đối 100% các cụm từ mang ngữ nghĩa điều hành
  ("à nhầm", "à quên", "à trừ", "thôi đổi", "thay vì", "sửa lại", "cũng giờ đó", "sau họp",
  "sau cuộc họp", "hoặc", "phân vân", "chưa chốt").
- T3.1: Chuẩn hóa giờ nói tiếng Việt có buổi/phút/rưỡi/kém sang HH:MM (hoặc 24:00 cho 12 giờ đêm),
  hỗ trợ kế thừa buổi ở vế cuối cho khoảng giờ ("từ bảy giờ đến chín giờ tối" -> "từ 19:00 đến 21:00"),
  chuẩn hóa Thứ chữ tiếng Việt ("thứ hai".."thứ bảy" -> "Thứ 2".."Thứ 7") và thời lượng nói
  ("một tiếng rưỡi" -> "90 phút", "nửa tiếng" -> "30 phút", "hai tiếng" -> "120 phút",
  "chín mươi phút" -> "90 phút") mà không làm hỏng từ ghép tiếng Việt ("tiếng Anh", "thứ tự", "tư duy").
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# Danh sách cụm từ điều hành / đính chính / do dự bắt buộc bảo toàn 100% (T3.2 Guardrail)
PROTECTED_DISCOURSE_WHITELIST: tuple[str, ...] = (
    "à nhầm",
    "à quên",
    "à trừ",
    "thôi đổi",
    "thay vì",
    "sửa lại",
    "cũng giờ đó",
    "sau họp",
    "sau cuộc họp",
    "hoặc",
    "phân vân",
    "chưa chốt",
)


def _build_number_maps() -> tuple[
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    """Khởi tạo bảng tra số tiếng Việt O(1) tại thời điểm nạp module."""
    clock_hours: dict[str, int] = {
        "không": 0,
        "một": 1,
        "hai": 2,
        "ba": 3,
        "bốn": 4,
        "tư": 4,
        "năm": 5,
        "sáu": 6,
        "bảy": 7,
        "bẩy": 7,
        "tám": 8,
        "chín": 9,
        "mười": 10,
        "mười một": 11,
        "mười hai": 12,
        "mười ba": 13,
        "mười bốn": 14,
        "mười tư": 14,
        "mười lăm": 15,
        "mười năm": 15,
        "mười sáu": 16,
        "mười bảy": 17,
        "mười bẩy": 17,
        "mười tám": 18,
        "mười chín": 19,
        "hai mươi": 20,
        "hai chục": 20,
        "hai mươi mốt": 21,
        "hai mốt": 21,
        "hai mươi một": 21,
        "hai mươi hai": 22,
        "hai hai": 22,
        "hai mươi ba": 23,
        "hai ba": 23,
        "hai mươi bốn": 24,
        "hai mươi tư": 24,
        "hai tư": 24,
        "hai bốn": 24,
    }

    unit_words: dict[int, tuple[str, ...]] = {
        1: ("một",),
        2: ("hai",),
        3: ("ba",),
        4: ("bốn", "tư"),
        5: ("năm", "lăm"),
        6: ("sáu",),
        7: ("bảy", "bẩy"),
        8: ("tám",),
        9: ("chín",),
    }

    clock_mins: dict[str, int] = {
        "không": 0,
        "không không": 0,
        "một": 1,
        "hai": 2,
        "ba": 3,
        "bốn": 4,
        "năm": 5,
        "sáu": 6,
        "bảy": 7,
        "bẩy": 7,
        "tám": 8,
        "chín": 9,
        "mười": 10,
        "một chục": 10,
        "mười một": 11,
        "mười hai": 12,
        "mười ba": 13,
        "mười bốn": 14,
        "mười tư": 14,
        "mười lăm": 15,
        "mười năm": 15,
        "mười sáu": 16,
        "mười bảy": 17,
        "mười bẩy": 17,
        "mười tám": 18,
        "mười chín": 19,
    }
    for u_val, u_variants in unit_words.items():
        for prefix in ("không", "lẻ", "linh"):
            for u_str in u_variants:
                clock_mins[f"{prefix} {u_str}"] = u_val

    tens_words: dict[int, tuple[str, ...]] = {
        2: ("hai",),
        3: ("ba",),
        4: ("bốn",),
        5: ("năm",),
        6: ("sáu",),
        7: ("bảy", "bẩy"),
        8: ("tám",),
        9: ("chín",),
    }

    duration_mins: dict[str, int] = {
        k: v
        for k, v in clock_mins.items()
        if v >= 1 and not k.startswith(("không ", "lẻ ", "linh "))
    }

    for t_val, t_variants in tens_words.items():
        for t_str in t_variants:
            exact_val = t_val * 10
            for exact_phrase in (f"{t_str} mươi", f"{t_str} chục"):
                duration_mins[exact_phrase] = exact_val
                if exact_val < 60:
                    clock_mins[exact_phrase] = exact_val
            for u_val in range(1, 10):
                comp_val = exact_val + u_val
                if u_val == 1:
                    comp_phrases = (
                        f"{t_str} mươi mốt",
                        f"{t_str} mốt",
                        f"{t_str} mươi một",
                    )
                elif u_val == 4:
                    comp_phrases = (
                        f"{t_str} mươi bốn",
                        f"{t_str} mươi tư",
                        f"{t_str} tư",
                        f"{t_str} bốn",
                    )
                elif u_val == 5:
                    comp_phrases = (
                        f"{t_str} mươi lăm",
                        f"{t_str} mươi nhăm",
                        f"{t_str} mươi năm",
                        f"{t_str} lăm",
                        f"{t_str} nhăm",
                    )
                elif u_val == 7:
                    comp_phrases = (
                        f"{t_str} mươi bảy",
                        f"{t_str} mươi bẩy",
                        f"{t_str} bảy",
                        f"{t_str} bẩy",
                    )
                else:
                    u_word = unit_words[u_val][0]
                    comp_phrases = (
                        f"{t_str} mươi {u_word}",
                        f"{t_str} {u_word}",
                    )
                for cp in comp_phrases:
                    duration_mins[cp] = comp_val
                    if comp_val < 60:
                        clock_mins[cp] = comp_val

    duration_mins.update(
        {
            "một trăm": 100,
            "một trăm mười": 110,
            "một trăm hai mươi": 120,
            "một trăm hai": 120,
            "trăm hai mươi": 120,
            "trăm hai": 120,
            "một trăm ba mươi": 130,
            "một trăm ba": 130,
            "một trăm bốn mươi": 140,
            "một trăm năm mươi": 150,
            "một trăm rưỡi": 150,
            "trăm rưỡi": 150,
            "một trăm tám mươi": 180,
        }
    )

    duration_hours: dict[str, int] = {
        k: v for k, v in clock_hours.items() if 1 <= v <= 16
    }

    return clock_hours, clock_mins, duration_hours, duration_mins


(
    _VN_CLOCK_HOUR_MAP,
    _VN_CLOCK_MIN_MAP,
    _VN_DURATION_HOUR_MAP,
    _VN_DURATION_MIN_MAP,
) = _build_number_maps()


def _alternation_from_keys(mapping: dict[str, int]) -> str:
    """Tạo chuỗi regex alternation sắp xếp từ dài đến ngắn để ưu tiên khớp cụm dài trước."""
    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)
    return "|".join(re.escape(k).replace(r"\ ", r"[ \t]+") for k in sorted_keys)


_CLOCK_HOUR_WORDS_PAT = _alternation_from_keys(_VN_CLOCK_HOUR_MAP)
_CLOCK_MIN_WORDS_PAT = _alternation_from_keys(_VN_CLOCK_MIN_MAP)
_DURATION_HOUR_WORDS_PAT = _alternation_from_keys(_VN_DURATION_HOUR_MAP)
_DURATION_MIN_WORDS_PAT = _alternation_from_keys(_VN_DURATION_MIN_MAP)

_HOUR_TOKEN_PAT = rf"(?:2[0-4]|1\d|0?\d|{_CLOCK_HOUR_WORDS_PAT})"
_MIN_TOKEN_PAT = rf"(?:[0-5]\d|\d|{_CLOCK_MIN_WORDS_PAT})"
_DURATION_HOUR_TOKEN_PAT = rf"(?:\d+|{_DURATION_HOUR_WORDS_PAT})"

# ==============================================================================
# T3.2: CONSERVATIVE DISFLUENCY & STUTTER REMOVAL REGEXES
# ==============================================================================

# Từ đệm đứng ở đầu chuỗi/dòng hoặc ngay sau dấu câu (, ; . : ? ! \n).
# Tuyệt đối không khớp "à" đơn lẻ để bảo vệ 100% "à nhầm", "à quên", "à trừ".
_LEADING_OR_POST_PUNCT_FILLER_RE = re.compile(
    r"(^|[,;.:?!\n])[ \t]*"
    r"(?:(?<!\w)(?:ờm|ừm|à[ \t]+thì|ờ[ \t]+thì|ừ[ \t]+thì|ơ[ \t]+kìa|ờ|ừ)(?!\w)[ \t]*,?[ \t]*)+",
    re.IGNORECASE,
)

# Từ ngập ngừng phi từ vựng xuất hiện giữa câu
_INLINE_HESITATION_RE = re.compile(
    r"[ \t]+(?<!\w)(?:ờm|ừm|à[ \t]+thì|ờ[ \t]+thì|ừ[ \t]+thì|ơ[ \t]+kìa)(?!\w)[ \t]*,?[ \t]*",
    re.IGNORECASE,
)

# Lặp từ chức năng liên tiếp do nói lắp (VD: "thứ, thứ hai" -> "thứ hai", "từ, từ 7h" -> "từ 7h").
# Riêng "từ từ" và "ngày ngày" không có dấu phẩy chỉ gộp khi đứng ngay trước mốc thời gian/ngày/thứ
# để không làm hỏng trạng từ tiếng Việt hợp lệ ("làm việc từ từ thôi", "ngày ngày ôn tập").
_STUTTER_FUNC_WORD_RE = re.compile(
    r"(?<!\w)(thứ|đến|tới|khoảng|vào|lúc)(?:[ \t]*,?[ \t]+\1)+(?!\w)",
    re.IGNORECASE,
)

_STUTTER_COMMA_OR_TRIPLE_TU_NGAY_RE = re.compile(
    r"(?<!\w)(từ|ngày)(?:(?:[ \t]*,[ \t]+\1)+|(?:[ \t]*,?[ \t]+\1){2,})(?!\w)",
    re.IGNORECASE,
)

_STUTTER_TU_TEMPORAL_RE = re.compile(
    r"(?<!\w)(từ)[ \t]+\1(?!\w)(?=[ \t]+(?:"
    r"\d|thứ\b|chủ[ \t]+nhật\b|cn\b|ngày\b|nay\b|mai\b|mốt\b|hôm\b|tuần\b|tháng\b|năm\b|"
    r"sáng\b|trưa\b|chiều\b|tối\b|đêm\b|khuya\b|buổi\b|cuối[ \t]+tuần\b|đầu[ \t]+tuần\b|"
    rf"giờ\b|bây[ \t]+giờ\b|lúc\b|(?:{_CLOCK_HOUR_WORDS_PAT})[ \t]+(?:giờ|rưỡi|kém)\b"
    r"))",
    re.IGNORECASE,
)

_STUTTER_NGAY_TEMPORAL_RE = re.compile(
    r"(?<!\w)(ngày)[ \t]+\1(?!\w)(?=[ \t]+(?:"
    r"\d|mai\b|mốt\b|nay\b|hôm\b|kia\b|thứ\b|chủ[ \t]+nhật\b|cn\b|cuối[ \t]+tuần\b|đầu[ \t]+tuần\b|"
    rf"(?:{_CLOCK_HOUR_WORDS_PAT})\b"
    r"))",
    re.IGNORECASE,
)

# ==============================================================================
# T3.1: SPOKEN WEEKDAY, CLOCK TIME & DURATION NORMALIZATION REGEXES
# ==============================================================================

_WEEKDAY_WORD_TO_DIGIT: dict[str, str] = {
    "hai": "2",
    "ba": "3",
    "tư": "4",
    "bốn": "4",
    "năm": "5",
    "sáu": "6",
    "bảy": "7",
    "bẩy": "7",
}

# Chỉ khớp "thứ hai".."thứ bảy", chống dương tính giả với "thứ tự", "thứ tư duy", "thứ ba mươi", "bước thứ hai"
_SPOKEN_WEEKDAY_RE = re.compile(
    r"(?<!\w)(?<!bước\s)(?<!lần\s)(?<!phần\s)(?<!chương\s)(?<!mục\s)"
    r"thứ[ \t]+(?P<wd>hai|ba|tư|bốn|năm|sáu|bảy|bẩy)(?!\w)"
    r"(?![ \t]+(?:mươi|chục|trăm|nghìn|ngàn|triệu|tỷ|mốt|lăm|nhăm|duy|vấn|liệu|cách|thế|chất|tưởng)\b)",
    re.IGNORECASE,
)

# Buổi trong ngày kèm negative lookahead chống khớp nhầm từ ghép ("sáng tạo", "tối đa", "chiều dài")
_PERIOD_PAT = (
    r"(?:buổi[ \t]+)?"
    r"(?:sáng(?![ \t]+(?:tạo|kiến|suốt|lập|chế)\b)"
    r"|trưa"
    r"|chiều(?![ \t]+(?:dài|rộng|cao|sâu|hướng)\b)"
    r"|tối(?![ \t]+(?:đa|thiểu|ưu|hậu)\b)"
    r"|đêm"
    r"|khuya)(?!\w)"
)

# Biểu thức lõi của 1 mốc giờ (chưa gồm buổi):
# - Nhánh 1: <hour> (chữ hoặc số) + giờ kém <min> | kém <min> | giờ rưỡi | rưỡi | giờ <min> | giờ
# - Nhánh 2: <digit_hour> + h/g kém <min> | h/g rưỡi | h/g/: <digit_min> | h/g
_TIME_CORE_PAT = (
    r"(?<!\w)(?<!\d[.,])(?<!Thứ\s)(?<!thứ\s)(?<!ngày\s)(?<!tháng\s)"
    r"(?:"
    rf"{_HOUR_TOKEN_PAT}"
    r"(?:"
    rf"[ \t]+giờ[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    rf"|[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    r"|[ \t]+giờ[ \t]+rưỡi"
    r"|[ \t]+rưỡi"
    rf"|[ \t]+giờ[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?(?![ \t]*(?:giờ|h|g|:|rưỡi|kém)\b)"
    r"|[ \t]+giờ(?:[ \t]+đúng)?"
    r")"
    r"|"
    r"(?:2[0-4]|1\d|0?\d)[ \t]*"
    r"(?:"
    rf"(?:h|g)[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    r"|(?:h|g)[ \t]+rưỡi"
    r"|(?:h|g|:)[ \t]*(?:[0-5]\d|\d)(?:[ \t]+phút|[ \t]*p)?"
    r"|(?:h|g)"
    r")"
    r")(?!\w)"
)

_TIME_CORE_PARSE_RE = re.compile(
    r"^(?:"
    rf"(?P<hour>{_HOUR_TOKEN_PAT})"
    r"(?:"
    rf"[ \t]+giờ[ \t]+kém[ \t]+(?P<gio_kem_min>{_MIN_TOKEN_PAT})(?:[ \t]+phút|[ \t]*p)?"
    rf"|[ \t]+kém[ \t]+(?P<bare_kem_min>{_MIN_TOKEN_PAT})(?:[ \t]+phút|[ \t]*p)?"
    r"|(?P<gio_ruoi>[ \t]+giờ[ \t]+rưỡi)"
    r"|(?P<bare_ruoi>[ \t]+rưỡi)"
    rf"|[ \t]+giờ[ \t]+(?P<gio_min>{_MIN_TOKEN_PAT})(?:[ \t]+phút|[ \t]*p)?"
    r"|(?P<exact_gio>[ \t]+giờ(?:[ \t]+đúng)?)"
    r")"
    r"|"
    r"(?P<d_hour>2[0-4]|1\d|0?\d)[ \t]*"
    r"(?:"
    rf"(?:h|g)[ \t]+kém[ \t]+(?P<h_kem_min>{_MIN_TOKEN_PAT})(?:[ \t]+phút|[ \t]*p)?"
    r"|(?P<h_ruoi>(?:h|g)[ \t]+rưỡi)"
    r"|(?:h|g|:)[ \t]*(?P<hm_min>[0-5]\d|\d)(?:[ \t]+phút|[ \t]*p)?"
    r"|(?P<exact_h>(?:h|g))"
    r")"
    r")$",
    re.IGNORECASE,
)

_TIME_RANGE_RE = re.compile(
    rf"(?P<start_core>{_TIME_CORE_PAT})"
    rf"(?:[ \t]+(?P<start_period>{_PERIOD_PAT}))?"
    r"(?P<sep>[ \t]*(?:đến|tới|-|–|~)[ \t]*)"
    rf"(?P<end_core>{_TIME_CORE_PAT})"
    rf"(?:[ \t]+(?P<end_period>{_PERIOD_PAT}))?",
    re.IGNORECASE,
)

_SINGLE_TIME_WITH_PERIOD_RE = re.compile(
    rf"(?P<core>{_TIME_CORE_PAT})[ \t]+(?P<period>{_PERIOD_PAT})",
    re.IGNORECASE,
)

# Giờ 24h tường minh không cần buổi (13..23 giờ rưỡi / 13..23 giờ <phút>)
_UNAMBIGUOUS_24H_TIME_RE = re.compile(
    rf"(?P<core>{_TIME_CORE_PAT})(?![ \t]+đồng[ \t]+hồ\b)",
    re.IGNORECASE,
)

# Thời lượng nói (T3.1 Strict Spoken Durations)
_HALF_HOUR_RE = re.compile(
    r"(?<!\w)nửa[ \t]+(?:tiếng|giờ)(?:[ \t]+đồng[ \t]+hồ)?(?!\w)",
    re.IGNORECASE,
)

_N_TIENG_RUOI_RE = re.compile(
    rf"(?<!\w)(?<!\d[.,])(?:(?P<num>{_DURATION_HOUR_TOKEN_PAT})[ \t]+)?tiếng[ \t]+rưỡi(?:[ \t]+đồng[ \t]+hồ)?(?!\w)",
    re.IGNORECASE,
)

# Chống dương tính giả với ngôn ngữ ("tiếng Anh", "tiếng Việt", "tiếng Nhật") và âm thanh ("tiếng chuông", "tiếng nói")
_TIENG_NON_DURATION_FOLLOWERS = (
    r"rưỡi|anh|việt|nhật|pháp|trung|hàn|đức|nga|tây|hoa|bồ|ý|thái|lào|khmer|ả|"
    r"động|nổ|kêu|chuông|còi|thở|cười|khóc|nói|gọi|vang|hét|la|gầm|sấm|sét|đàn|hát"
)

_DECIMAL_TIENG_RE = re.compile(
    rf"(?<!\w)(?<!\d[.,])(?P<whole>\d+)[.,](?P<frac>\d+)[ \t]*tiếng(?:[ \t]+đồng[ \t]+hồ)?"
    rf"(?![ \t]+(?:{_TIENG_NON_DURATION_FOLLOWERS})\b)(?!\w)",
    re.IGNORECASE,
)

_N_TIENG_RE = re.compile(
    rf"(?<!\w)(?<!\d[.,])(?P<num>{_DURATION_HOUR_TOKEN_PAT})[ \t]*tiếng(?:[ \t]+đồng[ \t]+hồ)?"
    rf"(?:[ \t]+(?P<extra_min>\d+|{_CLOCK_MIN_WORDS_PAT})[ \t]*(?:phút|p)(?!\w))?"
    rf"(?![ \t]+(?:{_TIENG_NON_DURATION_FOLLOWERS})\b)(?!\w)",
    re.IGNORECASE,
)

_SPOKEN_MIN_DURATION_RE = re.compile(
    rf"(?<!\w)(?<!giờ\s)(?<!kém\s)(?P<mins>{_DURATION_MIN_WORDS_PAT})[ \t]+phút(?!\w)",
    re.IGNORECASE,
)

_MULTI_HSPACE_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([,;.:?!])")


class _ParsedTimeCore(NamedTuple):
    hour: int
    minute: int
    is_kem: bool
    has_minutes_or_marker: bool


def _parse_hour_token(raw: str) -> int | None:
    cleaned = " ".join(raw.strip().lower().split())
    if cleaned.isdigit():
        val = int(cleaned)
        return val if 0 <= val <= 24 else None
    return _VN_CLOCK_HOUR_MAP.get(cleaned)


def _parse_min_token(raw: str) -> int | None:
    cleaned = " ".join(raw.strip().lower().split())
    if cleaned.isdigit():
        val = int(cleaned)
        return val if 0 <= val <= 59 else None
    return _VN_CLOCK_MIN_MAP.get(cleaned)


def _parse_duration_hour_token(raw: str) -> int | None:
    cleaned = " ".join(raw.strip().lower().split())
    if cleaned.isdigit():
        val = int(cleaned)
        return val if 1 <= val <= 16 else None
    return _VN_DURATION_HOUR_MAP.get(cleaned)


def _parse_time_core(core_str: str) -> _ParsedTimeCore | None:
    m = _TIME_CORE_PARSE_RE.match(core_str.strip())
    if m is None:
        return None
    raw_hour = m.group("hour") or m.group("d_hour")
    if raw_hour is None:
        return None
    hour = _parse_hour_token(raw_hour)
    if hour is None:
        return None

    kem_raw = m.group("gio_kem_min") or m.group("bare_kem_min") or m.group("h_kem_min")
    if kem_raw is not None:
        kem_min = _parse_min_token(kem_raw)
        if kem_min is None or not (1 <= kem_min <= 59):
            return None
        return _ParsedTimeCore(hour=hour, minute=kem_min, is_kem=True, has_minutes_or_marker=True)

    if m.group("gio_ruoi") or m.group("bare_ruoi") or m.group("h_ruoi"):
        return _ParsedTimeCore(hour=hour, minute=30, is_kem=False, has_minutes_or_marker=True)

    min_raw = m.group("gio_min") or m.group("hm_min")
    if min_raw is not None:
        minute = _parse_min_token(min_raw)
        if minute is None:
            return None
        return _ParsedTimeCore(hour=hour, minute=minute, is_kem=False, has_minutes_or_marker=True)

    return _ParsedTimeCore(hour=hour, minute=0, is_kem=False, has_minutes_or_marker=False)


def _clean_period(raw_period: str | None) -> str | None:
    if not raw_period:
        return None
    cleaned = " ".join(raw_period.strip().lower().split())
    if cleaned.startswith("buổi "):
        cleaned = cleaned[5:].strip()
    return cleaned or None


def _resolve_24h_time(core: _ParsedTimeCore, period: str | None) -> str | None:
    """Quy đổi (_ParsedTimeCore, period) sang chuỗi 'HH:MM' (hoặc '24:00' cho nửa đêm)."""
    h = core.hour
    m = core.minute
    p = _clean_period(period)

    base_h24: int | None = None
    if p is None:
        if 13 <= h <= 23:
            base_h24 = h
        elif h == 24 and m == 0 and not core.is_kem:
            base_h24 = 24
        else:
            return None
    elif p == "sáng":
        if h == 12:
            base_h24 = 0
        elif 0 <= h <= 11:
            base_h24 = h
    elif p == "trưa":
        if 5 <= h <= 12:
            base_h24 = h
        elif 1 <= h <= 4:
            base_h24 = h + 12
        elif 13 <= h <= 16:
            base_h24 = h
    elif p == "chiều":
        if 1 <= h <= 11:
            base_h24 = h + 12
        elif h == 12:
            base_h24 = 12
        elif 13 <= h <= 23:
            base_h24 = h
    elif p == "tối":
        if 1 <= h <= 11:
            base_h24 = h + 12
        elif h in (0, 12, 24):
            base_h24 = 24
        elif 13 <= h <= 23:
            base_h24 = h
    elif p in ("đêm", "khuya"):
        if h in (0, 12, 24):
            base_h24 = 24
        elif 1 <= h <= 5:
            base_h24 = h
        elif 6 <= h <= 11:
            base_h24 = h + 12
        elif 18 <= h <= 23:
            base_h24 = h

    if base_h24 is None:
        return None

    if core.is_kem:
        effective_base = 24 if base_h24 == 0 else base_h24
        total_mins = effective_base * 60 - m
        hh = (total_mins // 60) % 24
        mm = total_mins % 60
        return f"{hh:02d}:{mm:02d}"

    if base_h24 == 24:
        if m == 0:
            return "24:00"
        return f"00:{m:02d}"

    return f"{base_h24:02d}:{m:02d}"


def _hhmm_to_minutes(hhmm: str) -> int:
    hh, mm = (int(part) for part in hhmm.split(":"))
    return hh * 60 + mm


def _replace_time_range(match: re.Match[str]) -> str:
    start_core = _parse_time_core(match.group("start_core"))
    end_core = _parse_time_core(match.group("end_core"))
    if start_core is None or end_core is None:
        return match.group(0)

    start_period = _clean_period(match.group("start_period"))
    end_period = _clean_period(match.group("end_period"))

    if start_period is None and end_period is None:
        # Chỉ quy đổi khi cả hai đầu đã là giờ 24h tường minh (>= 13h)
        start_str = _resolve_24h_time(start_core, None)
        end_str = _resolve_24h_time(end_core, None)
        if start_str is not None and end_str is not None:
            return f"{start_str}{match.group('sep')}{end_str}"
        return match.group(0)

    eff_start_period = start_period if start_period is not None else end_period
    eff_end_period = end_period if end_period is not None else start_period

    start_str = _resolve_24h_time(start_core, eff_start_period)
    end_str = _resolve_24h_time(end_core, eff_end_period)

    # Nếu vế đầu kế thừa buổi "chiều"/"tối" của vế sau nhưng làm start >= end (VD: "8 giờ đến 5 giờ chiều"),
    # thử quy đổi vế đầu theo buổi sáng.
    if (
        start_period is None
        and end_period in ("chiều", "tối")
        and start_str is not None
        and end_str is not None
        and _hhmm_to_minutes(start_str) >= _hhmm_to_minutes(end_str)
    ):
        alt_start = _resolve_24h_time(start_core, "sáng")
        if alt_start is not None and _hhmm_to_minutes(alt_start) < _hhmm_to_minutes(end_str):
            start_str = alt_start

    # Nếu vế sau kế thừa buổi của vế đầu nhưng làm end <= start
    # (VD: "7 giờ sáng đến 12 giờ" -> 12:00, "bảy giờ sáng đến hai giờ" -> 14:00, "11 giờ tối đến 1 giờ" -> 01:00)
    if (
        end_period is None
        and start_period is not None
        and start_str is not None
        and end_str is not None
        and _hhmm_to_minutes(end_str) <= _hhmm_to_minutes(start_str)
    ):
        if start_period == "sáng":
            alt_end = _resolve_24h_time(end_core, "chiều")
            if alt_end is not None and _hhmm_to_minutes(alt_end) > _hhmm_to_minutes(start_str):
                end_str = alt_end
        elif start_period == "trưa":
            alt_end = _resolve_24h_time(end_core, "chiều")
            if alt_end is not None and _hhmm_to_minutes(alt_end) > _hhmm_to_minutes(start_str):
                end_str = alt_end
        elif start_period in ("tối", "đêm", "khuya") and 1 <= end_core.hour <= 5:
            alt_end = _resolve_24h_time(end_core, "đêm")
            if alt_end is not None:
                end_str = alt_end

    if start_str is None or end_str is None:
        return match.group(0)

    return f"{start_str}{match.group('sep')}{end_str}"


def _replace_single_time_with_period(match: re.Match[str]) -> str:
    core = _parse_time_core(match.group("core"))
    if core is None:
        return match.group(0)
    resolved = _resolve_24h_time(core, match.group("period"))
    return resolved if resolved is not None else match.group(0)


def _replace_unambiguous_24h_time(match: re.Match[str]) -> str:
    core = _parse_time_core(match.group("core"))
    if core is None or not core.has_minutes_or_marker:
        return match.group(0)
    resolved = _resolve_24h_time(core, None)
    return resolved if resolved is not None else match.group(0)


def _replace_spoken_weekday(match: re.Match[str]) -> str:
    wd_word = match.group("wd").lower()
    digit = _WEEKDAY_WORD_TO_DIGIT.get(wd_word)
    return f"Thứ {digit}" if digit is not None else match.group(0)


def _replace_n_tieng_ruoi(match: re.Match[str]) -> str:
    raw_num = match.group("num")
    if raw_num is None:
        return "90 phút"
    n_val = _parse_duration_hour_token(raw_num)
    if n_val is None:
        return match.group(0)
    return f"{n_val * 60 + 30} phút"


def _replace_decimal_tieng(match: re.Match[str]) -> str:
    val = float(f"{match.group('whole')}.{match.group('frac')}")
    mins = int(round(val * 60))
    if 1 <= mins <= 960:
        return f"{mins} phút"
    return match.group(0)


def _replace_n_tieng(match: re.Match[str]) -> str:
    n_val = _parse_duration_hour_token(match.group("num"))
    if n_val is None:
        return match.group(0)
    extra_raw = match.group("extra_min")
    extra_min = 0
    if extra_raw is not None:
        parsed_extra = _parse_min_token(extra_raw)
        if parsed_extra is None:
            return match.group(0)
        extra_min = parsed_extra
    return f"{n_val * 60 + extra_min} phút"


def _replace_spoken_min_duration(match: re.Match[str]) -> str:
    cleaned = " ".join(match.group("mins").strip().lower().split())
    val = _VN_DURATION_MIN_MAP.get(cleaned)
    if val is None:
        return match.group(0)
    return f"{val} phút"


def _replace_leading_filler(match: re.Match[str]) -> str:
    punct = match.group(1)
    if not punct:
        return ""
    if punct == "\n":
        return "\n"
    return f"{punct} "


def normalize_vietnamese_transcript(text: str) -> str:
    """Chuẩn hóa câu nói tiếng Việt (ITN & lọc từ đệm bảo thủ) trước khi đưa vào UF1 extractor.

    Đảm bảo tất định (deterministic), độ trễ < 5ms/request, bảo toàn 100% các cụm từ
    đính chính / điều hành / phân vân trong `PROTECTED_DISCOURSE_WHITELIST`.
    """
    if not text or not text.strip():
        return text

    out = unicodedata.normalize("NFC", text)

    # 1. T3.2: Lọc từ đệm ngập ngừng & gộp từ chức năng lặp do nói lắp
    out = _LEADING_OR_POST_PUNCT_FILLER_RE.sub(_replace_leading_filler, out)
    out = _INLINE_HESITATION_RE.sub(" ", out)
    out = _STUTTER_FUNC_WORD_RE.sub(r"\1", out)
    out = _STUTTER_COMMA_OR_TRIPLE_TU_NGAY_RE.sub(r"\1", out)
    out = _STUTTER_TU_TEMPORAL_RE.sub(r"\1", out)
    out = _STUTTER_NGAY_TEMPORAL_RE.sub(r"\1", out)
    out = _MULTI_HSPACE_RE.sub(" ", out)

    # 2. T3.1: Chuẩn hóa Thứ chữ tiếng Việt ("thứ hai".."thứ bảy" -> "Thứ 2".."Thứ 7")
    out = _SPOKEN_WEEKDAY_RE.sub(_replace_spoken_weekday, out)

    # 3. T3.1: Chuẩn hóa khoảng giờ nói (kế thừa buổi ở vế sau nếu vế đầu chưa có buổi) & mốc giờ đơn lẻ
    out = _TIME_RANGE_RE.sub(_replace_time_range, out)
    out = _SINGLE_TIME_WITH_PERIOD_RE.sub(_replace_single_time_with_period, out)
    out = _UNAMBIGUOUS_24H_TIME_RE.sub(_replace_unambiguous_24h_time, out)

    # 4. T3.1: Chuẩn hóa thời lượng nói tiếng Việt sang "<N> phút"
    out = _HALF_HOUR_RE.sub("30 phút", out)
    out = _N_TIENG_RUOI_RE.sub(_replace_n_tieng_ruoi, out)
    out = _DECIMAL_TIENG_RE.sub(_replace_decimal_tieng, out)
    out = _N_TIENG_RE.sub(_replace_n_tieng, out)
    out = _SPOKEN_MIN_DURATION_RE.sub(_replace_spoken_min_duration, out)

    # 5. Dọn khoảng trắng ngang thừa, giữ nguyên cấu trúc xuống dòng \n
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _MULTI_HSPACE_RE.sub(" ", out)
    lines = [line.strip() for line in out.split("\n")]
    return "\n".join(line for line in lines if line)
