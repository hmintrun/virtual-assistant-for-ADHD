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
from collections.abc import Callable
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

_NON_STUTTER_NGAY_PREFIX_RE = re.compile(
    r"(?<!\w)(?:hằng|hàng|mỗi|mọi|cả|suốt|nguyên|ban)[ \t]+$",
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
_ORDINAL_BEFORE_THU_RE = re.compile(
    r"(?<!\w)(?:lần|bước|phần|chương|tuần|ngày|buổi|mục|câu|bài|phương[ \t]+án|ý|hạng|tập|vị[ \t]+trí)[ \t]+$",
    re.IGNORECASE,
)
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
# - Nhánh 2: <digit_hour> + (:00|h|g) kém <min> | h/g rưỡi | h/g/: <digit_min> | h/g (không khớp G hoa)
_TIME_CORE_PAT = (
    r"(?<!\w)(?<!\d[.,/])(?<!Thứ\s)(?<!thứ\s)(?<!ngày\s)(?<!tháng\s)"
    r"(?:"
    rf"{_HOUR_TOKEN_PAT}"
    r"(?:"
    rf"[ \t]+giờ[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    rf"|[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    r"|[ \t]+giờ[ \t]+rưỡi"
    r"|[ \t]+rưỡi"
    rf"|[ \t]+giờ[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?(?![ \t]*(?:giờ|(?-i:[hHg])|:|rưỡi|kém)\b)"
    r"|[ \t]+giờ(?:[ \t]+đúng)?"
    r")"
    r"|"
    r"(?:2[0-4]|1\d|0?\d)[ \t]*"
    r"(?:"
    rf"(?::[ \t]*00|(?-i:[hHg])(?:[ \t]*00)?)[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    r"|(?-i:[hHg])[ \t]+rưỡi"
    r"|(?-i:[hHg:])[ \t]*(?:[0-5]\d|\d)(?:[ \t]+phút|[ \t]*p)?"
    r"|(?-i:[hHg])"
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
    rf"(?::[ \t]*00|(?-i:[hHg])(?:[ \t]*00)?)[ \t]+kém[ \t]+(?P<h_kem_min>{_MIN_TOKEN_PAT})(?:[ \t]+phút|[ \t]*p)?"
    r"|(?P<h_ruoi>(?-i:[hHg])[ \t]+rưỡi)"
    r"|(?-i:[hHg:])[ \t]*(?P<hm_min>[0-5]\d|\d)(?:[ \t]+phút|[ \t]*p)?"
    r"|(?P<exact_h>(?-i:[hHg]))"
    r")"
    r")$",
    re.IGNORECASE,
)

_TIME_RANGE_RE = re.compile(
    rf"(?P<start_core>{_TIME_CORE_PAT})"
    rf"(?:[ \t]+(?P<start_period>{_PERIOD_PAT}))?"
    r"(?P<sep>[ \t]*(?:đến|tới|-|–|~)[ \t]*(?:(?:khoảng|tầm|cỡ)[ \t]+)?)"
    rf"(?P<end_core>{_TIME_CORE_PAT})"
    rf"(?:[ \t]+(?P<end_period>{_PERIOD_PAT}))?",
    re.IGNORECASE,
)

_PERIOD_WORD_RE = re.compile(rf"(?<!\w){_PERIOD_PAT}", re.IGNORECASE)
_SUBCLAUSE_BOUNDARY_RE = re.compile(
    r"[,;!?]+|\n+|\.(?=\s|$)|"
    r"(?<!\w)(?:"
    r"hạn(?:[ \t]+chót)?|deadline|trước|trừ|ngoại[ \t]+trừ|riêng"
    r"|nộp(?=[ \t]+(?:trước|vào|lúc|ngày|thứ|chủ[ \t]+nhật|cn|hôm|mai|mốt|\d))"
    r"|(?:còn|nhưng|rồi|và)(?=[ \t]+(?:(?:rảnh|bận|vào|lúc|từ)[ \t]+)*(?:ngày|mai|mốt|hôm|thứ|chủ[ \t]+nhật|cn|\d{1,2}/))"
    r")(?!\w)",
    re.IGNORECASE,
)
_NUMERIC_COLON_KEM_PAT = (
    rf"(?<!\w)(?<!\d[.,/])(?<!Thứ\s)(?<!thứ\s)(?<!ngày\s)(?<!tháng\s)"
    rf"(?:(?:2[0-4]|1\d|0?\d):[ \t]*00|(?:2[0-4]|1\d|0?\d)[ \t]*(?-i:[hHg])(?:[ \t]*00)?|{_HOUR_TOKEN_PAT}[ \t]+giờ)"
    rf"[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?(?!\w)"
)
_NUMERIC_COLON_TIME_PAT = (
    rf"(?<!\w)(?<!\d[.,/])(?<!Thứ\s)(?<!thứ\s)(?<!ngày\s)(?<!tháng\s)"
    rf"(?:"
    rf"(?:2[0-4]|1\d|0?\d):[ \t]*00[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    rf"|(?:1[0-2]|0?[1-9]):[0-5]\d"
    rf"|(?:{_CLOCK_HOUR_WORDS_PAT})[ \t]+giờ[ \t]+(?:rưỡi|{_CLOCK_MIN_WORDS_PAT})"
    rf")(?!\w)"
)
_NUMERIC_COLON_END_PAT = (
    rf"(?<!\w)(?<!\d[.,/])(?<!Thứ\s)(?<!thứ\s)(?<!ngày\s)(?<!tháng\s)"
    rf"(?:"
    rf"(?:2[0-4]|1\d|0?\d):[ \t]*00[ \t]+kém[ \t]+{_MIN_TOKEN_PAT}(?:[ \t]+phút|[ \t]*p)?"
    rf"|(?:2[0-4]|1\d|0?\d):[0-5]\d"
    rf"|(?:{_CLOCK_HOUR_WORDS_PAT})[ \t]+giờ[ \t]+(?:rưỡi|{_CLOCK_MIN_WORDS_PAT})"
    rf")(?!\w)"
)
_LEADING_PERIOD_NUMERIC_RANGE_RE = re.compile(
    rf"(?P<start_core>{_NUMERIC_COLON_TIME_PAT})"
    r"(?P<sep>[ \t]*(?:đến|tới|-|–)[ \t]*(?:(?:khoảng|tầm|cỡ)[ \t]+)?)"
    rf"(?P<end_core>{_NUMERIC_COLON_END_PAT})"
    rf"(?![ \t]+{_PERIOD_PAT})",
    re.IGNORECASE,
)
_LEADING_PERIOD_SINGLE_KEM_RE = re.compile(
    rf"(?P<core>{_NUMERIC_COLON_KEM_PAT})(?![ \t]+{_PERIOD_PAT})",
    re.IGNORECASE,
)
_PRE_ITN_3PT_STUTTER_RE = re.compile(
    rf"(?P<c1>{_TIME_CORE_PAT})"
    r"[ \t]+(?:đến|tới)[ \t]+(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    rf"(?P<c2>{_TIME_CORE_PAT})"
    r"[ \t]+(?:đến|tới)[ \t]+(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    rf"(?P<c3>{_TIME_CORE_PAT})"
    rf"(?P<tail_period>(?:[ \t]+{_PERIOD_PAT})?)",
    re.IGNORECASE,
)
_POST_ITN_3PT_STUTTER_RE = re.compile(
    r"(?<!\w)(?<!\d[:./])"
    r"(?P<t1>(?:2[0-4]|[01]?\d):[0-5]\d)"
    r"[ \t]+(?:đến|tới)[ \t]+(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    r"(?P<t2>(?:2[0-4]|[01]?\d):[0-5]\d)"
    r"[ \t]+(?:đến|tới)[ \t]+(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    r"(?P<t3>(?:2[0-4]|[01]?\d):[0-5]\d)"
    r"(?![:\d/])(?!\w)",
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

# Chuẩn hóa định dạng giờ nói <giờ_chữ>:<phút_chữ> từ STT (VD Deepgram Nova-3: "bảy:không", "tám:ba")
_SPOKEN_COLON_AMBIGUOUS_SINGLE_MIN_WORDS: frozenset[str] = frozenset(
    {"một", "hai", "ba", "bốn", "tư", "năm", "sáu", "bảy", "bẩy", "tám", "chín"}
)
_SPOKEN_COLON_TIME_RE = re.compile(
    rf"(?<!\w)(?P<hour>{_CLOCK_HOUR_WORDS_PAT}):(?P<min>{_CLOCK_MIN_WORDS_PAT}|rưỡi)(?!\w)",
    re.IGNORECASE,
)
_SPOKEN_COLON_NON_TIME_PREFIX_RE = re.compile(
    r"(?<!\w)(?:thứ|lần|bước|phần|chương|mục|câu|bài|hạng|tập|"
    r"tỉ[ \t]*lệ|tỷ[ \t]*lệ|tỉ[ \t]*số|tỷ[ \t]*số|phân[ \t]*số|"
    r"điểm|số|mã|cấp|loại|nhóm|tổ|lớp|thắng|thua|hòa)[ \t]+$",
    re.IGNORECASE,
)
_SPOKEN_COLON_TRAILING_PERIOD_RE = re.compile(rf"^[ \t]+{_PERIOD_PAT}", re.IGNORECASE)
_SPOKEN_COLON_RIGHT_RANGE_RE = re.compile(
    rf"^(?:[ \t]+{_PERIOD_PAT})?[ \t]*(?:đến|tới|-|–|~)[ \t]*(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    rf"(?:{_PERIOD_PAT}[ \t]+)?"
    rf"(?:(?:{_CLOCK_HOUR_WORDS_PAT}):(?:{_CLOCK_MIN_WORDS_PAT}|rưỡi)(?!\w)|{_TIME_CORE_PAT})",
    re.IGNORECASE,
)
_SPOKEN_COLON_LEFT_RANGE_RE = re.compile(
    rf"(?:(?P<left_colon>(?<!\w)(?:{_CLOCK_HOUR_WORDS_PAT}):(?:{_CLOCK_MIN_WORDS_PAT}|rưỡi)(?!\w))|{_TIME_CORE_PAT})"
    rf"(?:[ \t]+{_PERIOD_PAT})?[ \t]*(?:đến|tới|-|–|~)[ \t]*(?:(?:khoảng|tầm|cỡ)[ \t]+)?"
    rf"(?:{_PERIOD_PAT}[ \t]+)?$",
    re.IGNORECASE,
)
_SPOKEN_COLON_LEAD_CUE_RE = re.compile(
    r"(?<!\w)(?:từ|lúc|vào|khoảng|tầm|cỡ|trước|sau|sáng|trưa|chiều|tối|đêm|khuya)[ \t]+$",
    re.IGNORECASE,
)


def _normalize_spoken_colon_clock_times(text: str) -> str:
    """Chuẩn hóa định dạng ``<giờ_chữ>:<phút_chữ>`` (VD: ``bảy:không``, ``tám:ba``) sang dạng giờ chuẩn.

    Chỉ đổi token phút đơn ``ba`` -> ``ba mươi`` khi đứng trong ngữ cảnh giờ rõ ràng
    (nối khoảng giờ với ``đến/tới/-`` hoặc đi kèm buổi ``sáng/trưa/chiều/tối/đêm/khuya``)
    và chặn dương tính giả từ tỷ lệ/tỉ số/chương/bước.
    """
    if ":" not in text:
        return text

    def _repl(match: re.Match[str]) -> str:
        prefix = text[: match.start()]
        if _SPOKEN_COLON_NON_TIME_PREFIX_RE.search(prefix):
            return match.group(0)

        hour_raw = match.group("hour")
        min_raw = match.group("min")
        min_norm = " ".join(min_raw.strip().lower().split())

        if min_norm in _SPOKEN_COLON_AMBIGUOUS_SINGLE_MIN_WORDS:
            if min_norm != "ba":
                return match.group(0)
            suffix = text[match.end() :]
            has_trailing_period = bool(_SPOKEN_COLON_TRAILING_PERIOD_RE.match(suffix))
            has_right_range = bool(_SPOKEN_COLON_RIGHT_RANGE_RE.match(suffix))
            left_range_m = _SPOKEN_COLON_LEFT_RANGE_RE.search(prefix)
            has_left_range = False
            if left_range_m is not None:
                if left_range_m.group("left_colon") is not None:
                    left_colon_start = left_range_m.start("left_colon")
                    has_left_range = not bool(
                        _SPOKEN_COLON_NON_TIME_PREFIX_RE.search(prefix[:left_colon_start])
                    )
                else:
                    has_left_range = True
            last_boundary = 0
            for bm in _SUBCLAUSE_BOUNDARY_RE.finditer(prefix):
                last_boundary = bm.end()
            sub_prefix = prefix[last_boundary:]
            has_leading_period_ctx = bool(
                _PERIOD_WORD_RE.search(sub_prefix) and _SPOKEN_COLON_LEAD_CUE_RE.search(sub_prefix)
            )
            if not (
                has_trailing_period
                or has_right_range
                or has_left_range
                or has_leading_period_ctx
            ):
                return match.group(0)
            return f"{hour_raw} giờ ba mươi"

        return f"{hour_raw} giờ {min_raw}"

    return _SPOKEN_COLON_TIME_RE.sub(_repl, text)


# Chuẩn hóa ngày/tháng viết bằng chữ tiếng Việt (VD Deepgram smart_format=False:
# "ngày hai mươi tám tháng mười một" -> "ngày 28 tháng 11", "hảng hai mươi tám tháng mười một" -> "hảng 28 tháng 11")
_VN_CALENDAR_DAY_MAP: dict[str, int] = {
    "một": 1,
    "mốt": 1,
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
    "hai mươi lăm": 25,
    "hai mươi năm": 25,
    "hai lăm": 25,
    "hai mươi sáu": 26,
    "hai sáu": 26,
    "hai mươi bảy": 27,
    "hai mươi bẩy": 27,
    "hai bảy": 27,
    "hai bẩy": 27,
    "hai mươi tám": 28,
    "hai tám": 28,
    "hai mươi chín": 29,
    "hai chín": 29,
    "ba mươi": 30,
    "ba chục": 30,
    "ba mươi mốt": 31,
    "ba mốt": 31,
    "ba mươi một": 31,
}
_VN_CALENDAR_MONTH_MAP: dict[str, int] = {
    "một": 1,
    "giêng": 1,
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
    "chạp": 12,
}
_CALENDAR_DAY_WORDS_PAT = _alternation_from_keys(_VN_CALENDAR_DAY_MAP)
_CALENDAR_MONTH_WORDS_PAT = _alternation_from_keys(_VN_CALENDAR_MONTH_MAP)
_SPOKEN_CALENDAR_DATE_RE = re.compile(
    r"(?<!\w)(?:(?P<prefix>ngày|mùng|mồng)[ \t]+)?"
    rf"(?P<day>3[01]|[12]\d|0?[1-9]|{_CALENDAR_DAY_WORDS_PAT})[ \t]+"
    rf"tháng[ \t]+(?P<month>1[0-2]|0?[1-9]|{_CALENDAR_MONTH_WORDS_PAT})(?!\w)",
    re.IGNORECASE,
)
_BARE_CALENDAR_DATE_PREFIX_RE = re.compile(
    r"(?<!\w)(?:"
    r"ngày|mùng|mồng|hạn(?:[ \t]+chót)?|deadline|nộp|chậm[ \t]+nhất|xong|hoàn[ \t]+thành|"
    r"(?:hẹn|hạng|hảng|hãng|hàng|hản|háng|hán)(?:[ \t]+chót)?|"
    r"trước|sau|đến|tới|từ|vào|lúc|"
    r"thứ[ \t]*(?:[2-7]|hai|ba|tư|bốn|năm|sáu|bảy|bẩy)|chủ[ \t]+nhật|cn|"
    r"sáng|trưa|chiều|tối|đêm|khuya"
    r")[ \t]*$",
    re.IGNORECASE,
)
_BARE_CALENDAR_DATE_SUFFIX_CUE_RE = re.compile(
    r"^[ \t]+(?:lúc|vào|năm)\b",
    re.IGNORECASE,
)
_CALENDAR_DATE_NON_DATE_SUFFIX_RE = re.compile(
    r"^[ \t]+(?:lần|lượt|đợt|kỳ|bài|quyển|cuốn|trang|slide|buổi|tuần|ngày|tiếng|phút|người|cái|"
    r"mươi|chục|trăm|nghìn|ngàn|triệu|tỷ)\b",
    re.IGNORECASE,
)
_MAX_DAYS_IN_MONTH: dict[int, int] = {
    1: 31,
    2: 29,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}


def _parse_calendar_token(raw: str, mapping: dict[str, int], *, lo: int, hi: int) -> int | None:
    cleaned = " ".join(raw.strip().lower().split())
    if cleaned.isdigit():
        val = int(cleaned)
        return val if lo <= val <= hi else None
    return mapping.get(cleaned)


def _normalize_spoken_calendar_dates(text: str) -> str:
    """Chuẩn hóa cụm ngày/tháng viết bằng chữ sang số (VD: ``ngày hai mươi tám tháng mười một`` -> ``ngày 28 tháng 11``).

    Giữ nguyên các cụm thời lượng/tần suất như ``ba tháng trước``, ``năm tháng nữa``,
    ``mười hai tháng sau``, ``sáu tháng một lần``.
    """
    if "tháng" not in text.lower():
        return text

    def _repl(match: re.Match[str]) -> str:
        raw_day = match.group("day")
        raw_month = match.group("month")
        if raw_day.strip().isdigit() and raw_month.strip().isdigit():
            return match.group(0)

        day_val = _parse_calendar_token(raw_day, _VN_CALENDAR_DAY_MAP, lo=1, hi=31)
        month_val = _parse_calendar_token(raw_month, _VN_CALENDAR_MONTH_MAP, lo=1, hi=12)
        if day_val is None or month_val is None:
            return match.group(0)
        if day_val > _MAX_DAYS_IN_MONTH[month_val]:
            return match.group(0)

        suffix = text[match.end() :]
        if _CALENDAR_DATE_NON_DATE_SUFFIX_RE.match(suffix):
            return match.group(0)

        prefix_word = match.group("prefix")
        if prefix_word is None:
            before = text[: match.start()]
            has_left_ctx = bool(_BARE_CALENDAR_DATE_PREFIX_RE.search(before))
            has_right_ctx = bool(_BARE_CALENDAR_DATE_SUFFIX_CUE_RE.match(suffix))
            if not (has_left_ctx or has_right_ctx):
                return match.group(0)
            return f"{day_val} tháng {month_val}"

        return f"{prefix_word} {day_val} tháng {month_val}"

    return _SPOKEN_CALENDAR_DATE_RE.sub(_repl, text)


_NEW_CLAUSE_AFTER_PERIOD_RE = re.compile(
    r"^[ \t]+(?:(?:thứ[ \t]*(?:[2-7]|hai|ba|tư|bốn|năm|sáu|bảy|bẩy)|chủ[ \t]+nhật|cn|"
    r"hôm[ \t]+nay|ngày[ \t]+mai|mai|mốt|cuối[ \t]+tuần|ngày[ \t]+\d{1,2})[ \t]+)+"
    r"(?:(?:mình|tôi|em|anh|chị|bạn)[ \t]+)?"
    r"(?:bận|kẹt|họp|học|nghỉ|trừ|ngoại[ \t]+trừ|không[ \t]+rảnh|rảnh|rãnh|từ|lúc|"
    r"có[ \t]+(?:lịch|việc|buổi|cuộc|họp|học|lớp|ca)|"
    r"đi[ \t]+(?:học|họp|làm|chơi|công[ \t]+tác|khám|thi))\b",
    re.IGNORECASE,
)


def _leading_period_in_current_subclause(text_before: str) -> str | None:
    """Tìm buổi gần nhất đứng trước trong cùng tiểu mệnh đề (không vượt dấu câu/từ chuyển ý)."""
    last_boundary = 0
    for bm in _SUBCLAUSE_BOUNDARY_RE.finditer(text_before):
        last_boundary = bm.end()
    sub_before = text_before[last_boundary:]
    active_period: str | None = None
    for pm in _PERIOD_WORD_RE.finditer(sub_before):
        cleaned = _clean_period(pm.group(0))
        if cleaned:
            active_period = cleaned
    return active_period


def _is_period_starting_new_clause(
    *,
    text_before: str,
    text_after: str,
    period_name: str,
    start_core: _ParsedTimeCore | None = None,
    end_core: _ParsedTimeCore | None = None,
) -> bool:
    """Nhận diện buổi (VD: 'tối' trong '...sáng Thứ 4 từ 7h đến 9h tối Thứ 4 bận họp') thực chất mở đầu mệnh đề sau."""
    if _NEW_CLAUSE_AFTER_PERIOD_RE.match(text_after):
        return True
    leading_period = _leading_period_in_current_subclause(text_before)
    if (
        leading_period in ("sáng", "trưa")
        and period_name in ("tối", "đêm", "khuya")
        and start_core is not None
        and end_core is not None
        and 1 <= start_core.hour < end_core.hour <= 12
        and re.match(
            r"^[ \t]+(?:thứ\b|chủ[ \t]+nhật\b|cn\b|hôm[ \t]+nay\b|ngày\b|mai\b|mốt\b|"
            r"mình\b|tôi\b|bận\b|họp\b|học\b)",
            text_after,
            re.IGNORECASE,
        )
    ):
        return True
    return False


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
        elif h == 24 and (m == 0 or core.is_kem):
            base_h24 = 24
        elif h == 0 and core.is_kem:
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

    if (
        start_period is None
        and end_period is not None
        and _is_period_starting_new_clause(
            text_before=match.string[: match.start()],
            text_after=match.string[match.end() :],
            period_name=end_period,
            start_core=start_core,
            end_core=end_core,
        )
    ):
        leading_period = _leading_period_in_current_subclause(match.string[: match.start()])
        if leading_period is not None:
            start_str = _resolve_24h_time(start_core, leading_period)
            end_str = _resolve_24h_time(end_core, leading_period)
            if (
                start_str is not None
                and end_str is not None
                and _hhmm_to_minutes(end_str) <= _hhmm_to_minutes(start_str)
            ):
                if leading_period in ("sáng", "trưa"):
                    alt_end = _resolve_24h_time(end_core, "chiều")
                    if alt_end is not None and _hhmm_to_minutes(alt_end) > _hhmm_to_minutes(
                        start_str
                    ):
                        end_str = alt_end
                elif leading_period in ("tối", "đêm", "khuya") and 1 <= end_core.hour <= 5:
                    alt_end = _resolve_24h_time(end_core, "đêm")
                    if alt_end is not None:
                        end_str = alt_end
            if start_str is not None and end_str is not None:
                return f"{start_str}{match.group('sep')}{end_str}, {match.group('end_period').strip()}"
        return match.group(0)

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

    trailing_period_for_nay = (
        f" {match.group('end_period').strip()}"
        if match.group("end_period")
        and re.match(r"^[ \t]+nay\b", match.string[match.end() :], re.IGNORECASE)
        else ""
    )
    return f"{start_str}{match.group('sep')}{end_str}{trailing_period_for_nay}"


def _replace_single_time_with_period(match: re.Match[str]) -> str:
    core = _parse_time_core(match.group("core"))
    if core is None:
        return match.group(0)
    period_name = _clean_period(match.group("period"))
    if period_name is not None and _is_period_starting_new_clause(
        text_before=match.string[: match.start()],
        text_after=match.string[match.end() :],
        period_name=period_name,
    ):
        return match.group(0)
    resolved = _resolve_24h_time(core, match.group("period"))
    if resolved is None:
        return match.group(0)
    if re.match(r"^[ \t]+nay\b", match.string[match.end() :], re.IGNORECASE):
        return f"{resolved} {match.group('period').strip()}"
    return resolved


def _replace_unambiguous_24h_time(match: re.Match[str]) -> str:
    core = _parse_time_core(match.group("core"))
    if core is None or not core.has_minutes_or_marker:
        return match.group(0)
    resolved = _resolve_24h_time(core, None)
    return resolved if resolved is not None else match.group(0)


def _replace_spoken_weekday(match: re.Match[str]) -> str:
    if _ORDINAL_BEFORE_THU_RE.search(match.string[: match.start()]):
        return match.group(0)
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


def _replace_stutter_tu_ngay(match: re.Match[str]) -> str:
    word = match.group(1)
    if word.lower() == "ngày" and _NON_STUTTER_NGAY_PREFIX_RE.search(
        match.string[: match.start()]
    ):
        return match.group(0)
    return word


def _replace_leading_filler(match: re.Match[str]) -> str:
    punct = match.group(1)
    if not punct:
        return ""
    if punct == "\n":
        return "\n"
    return f"{punct} "


def _sub_tracking_spans(
    text: str,
    pattern: re.Pattern[str],
    repl: Callable[[re.Match[str]], str],
    locked_spans: list[tuple[int, int]],
) -> tuple[str, list[tuple[int, int]]]:
    """Thay thế các khớp ngoài ``locked_spans`` và cập nhật lại tập span đã chuẩn hóa."""
    parts: list[str] = []
    new_locked: list[tuple[int, int]] = []
    cursor = 0
    out_len = 0
    lock_idx = 0
    n_locks = len(locked_spans)

    def _emit_untouched(seg_start: int, seg_end: int) -> None:
        nonlocal out_len, lock_idx
        if seg_end <= seg_start:
            while lock_idx < n_locks and locked_spans[lock_idx][1] <= seg_end:
                lock_idx += 1
            return
        delta = out_len - seg_start
        while lock_idx < n_locks and locked_spans[lock_idx][0] < seg_end:
            ls, le = locked_spans[lock_idx]
            if ls >= seg_start and le <= seg_end:
                new_locked.append((ls + delta, le + delta))
            lock_idx += 1
        seg = text[seg_start:seg_end]
        parts.append(seg)
        out_len += len(seg)

    for match in pattern.finditer(text):
        ms, me = match.start(), match.end()
        overlaps_locked = any(ms < le and me > ls for ls, le in locked_spans)
        if overlaps_locked:
            continue
        replacement = repl(match)
        if replacement == match.group(0):
            continue
        _emit_untouched(cursor, ms)
        while lock_idx < n_locks and locked_spans[lock_idx][1] <= me:
            lock_idx += 1
        rep_start = out_len
        parts.append(replacement)
        out_len += len(replacement)
        new_locked.append((rep_start, out_len))
        cursor = me

    _emit_untouched(cursor, len(text))
    return "".join(parts), new_locked


def _core_minutes(core: _ParsedTimeCore) -> int | None:
    """Quy đổi `_ParsedTimeCore` sang số phút đơn điệu để so sánh thứ tự tăng dần."""
    h = core.hour
    m = core.minute
    if core.is_kem:
        effective_h = 24 if h == 0 else h
        return effective_h * 60 - m
    return h * 60 + m


def _collapse_pre_itn_stutters(text: str) -> str:
    """Gộp nói lắp 3 mốc thời gian tăng nghiêm ngặt trước bước quy đổi buổi (VD: 7h đến 8h đến 9h tối)."""
    def _repl(match: re.Match[str]) -> str:
        c1 = _parse_time_core(match.group("c1"))
        c2 = _parse_time_core(match.group("c2"))
        c3 = _parse_time_core(match.group("c3"))
        if c1 is None or c2 is None or c3 is None:
            return match.group(0)
        m1, m2, m3 = _core_minutes(c1), _core_minutes(c2), _core_minutes(c3)
        if m1 is None or m2 is None or m3 is None or not (m1 < m2 < m3):
            return match.group(0)
        tail = match.group("tail_period") or ""
        return f"{match.group('c1')} đến {match.group('c3')}{tail}"

    out = text
    for _ in range(4):
        updated = _PRE_ITN_3PT_STUTTER_RE.sub(_repl, out)
        if updated == out:
            break
        out = updated
    return out


def _propagate_leading_period_in_subclauses(
    text: str, locked_spans: list[tuple[int, int]]
) -> tuple[str, list[tuple[int, int]]]:
    """Kế thừa buổi đứng trước trong cùng tiểu mệnh đề cho khoảng giờ H1:M1..H2:M2 (1<=H1<=11) và <digit>:00 kém <min>."""
    boundaries: list[tuple[int, int]] = [(0, 0)]
    for b_match in _SUBCLAUSE_BOUNDARY_RE.finditer(text):
        boundaries.append((b_match.start(), b_match.end()))
    boundaries.append((len(text), len(text)))

    subclauses: list[tuple[int, int]] = []
    for idx in range(len(boundaries) - 1):
        s_start = boundaries[idx][1]
        s_end = boundaries[idx + 1][0]
        if s_end > s_start:
            subclauses.append((s_start, s_end))

    edits: list[tuple[int, int, str]] = []
    for sub_start, sub_end in subclauses:
        sub_text = text[sub_start:sub_end]
        period_matches = [
            (sub_start + pm.start(), sub_start + pm.end(), _clean_period(pm.group(0)))
            for pm in _PERIOD_WORD_RE.finditer(sub_text)
        ]
        if not period_matches:
            continue

        covered_ranges: list[tuple[int, int]] = []
        if ":" in sub_text or "giờ" in sub_text.lower():
            for rm in _LEADING_PERIOD_NUMERIC_RANGE_RE.finditer(sub_text):
                abs_s = sub_start + rm.start()
                abs_e = sub_start + rm.end()
                abs_start_core_end = abs_s + len(rm.group("start_core"))
                if any(abs_s < le and abs_start_core_end > ls for ls, le in locked_spans):
                    continue
                active_period: str | None = None
                for _, pe, p_name in period_matches:
                    if pe <= abs_s and p_name:
                        active_period = p_name
                if not active_period:
                    continue
                start_core = _parse_time_core(rm.group("start_core"))
                end_core = _parse_time_core(rm.group("end_core"))
                if start_core is None or end_core is None:
                    continue
                if 1 <= start_core.hour <= 11:
                    pass
                elif start_core.hour == 12 and (
                    active_period == "trưa"
                    or (active_period == "sáng" and 1 <= end_core.hour <= 11)
                ):
                    pass
                else:
                    continue
                start_str = _resolve_24h_time(start_core, active_period)
                end_str = _resolve_24h_time(end_core, active_period)
                if (
                    start_str is not None
                    and end_str is not None
                    and _hhmm_to_minutes(end_str) <= _hhmm_to_minutes(start_str)
                ):
                    if active_period in ("sáng", "trưa"):
                        alt_end = _resolve_24h_time(end_core, "chiều")
                        if alt_end is not None and _hhmm_to_minutes(alt_end) > _hhmm_to_minutes(start_str):
                            end_str = alt_end
                    elif active_period in ("tối", "đêm", "khuya") and 1 <= end_core.hour <= 5:
                        alt_end = _resolve_24h_time(end_core, "đêm")
                        if alt_end is not None:
                            end_str = alt_end
                if start_str is None or end_str is None:
                    continue
                edits.append((abs_s, abs_e, f"{start_str}{rm.group('sep')}{end_str}"))
                covered_ranges.append((abs_s, abs_e))

        for km in _LEADING_PERIOD_SINGLE_KEM_RE.finditer(sub_text):
            abs_s = sub_start + km.start()
            abs_e = sub_start + km.end()
            if any(abs_s < le and abs_e > ls for ls, le in locked_spans):
                continue
            if any(abs_s < ce and abs_e > cs for cs, ce in covered_ranges):
                continue
            active_period = None
            for _, pe, p_name in period_matches:
                if pe <= abs_s and p_name:
                    active_period = p_name
            if not active_period:
                continue
            core = _parse_time_core(km.group("core"))
            if core is None:
                continue
            resolved = _resolve_24h_time(core, active_period)
            if resolved is not None:
                edits.append((abs_s, abs_e, resolved))

    if not edits:
        return text, locked_spans

    edits.sort(key=lambda item: item[0])
    parts: list[str] = []
    new_locked: list[tuple[int, int]] = []
    cursor = 0
    out_len = 0
    lock_idx = 0
    n_locks = len(locked_spans)
    for es, ee, rep in edits:
        if es < cursor:
            continue
        delta = out_len - cursor
        while lock_idx < n_locks and locked_spans[lock_idx][0] < es:
            ls, le = locked_spans[lock_idx]
            if ls >= cursor and le <= es:
                new_locked.append((ls + delta, le + delta))
            lock_idx += 1
        if es > cursor:
            seg = text[cursor:es]
            parts.append(seg)
            out_len += len(seg)
        while lock_idx < n_locks and locked_spans[lock_idx][1] <= ee:
            lock_idx += 1
        rep_start = out_len
        parts.append(rep)
        out_len += len(rep)
        new_locked.append((rep_start, out_len))
        cursor = ee
    if cursor < len(text):
        delta = out_len - cursor
        while lock_idx < n_locks:
            ls, le = locked_spans[lock_idx]
            if ls >= cursor:
                new_locked.append((ls + delta, le + delta))
            lock_idx += 1
        parts.append(text[cursor:])
    return "".join(parts), new_locked


def _collapse_post_itn_stutters(text: str) -> str:
    """Gộp chuỗi 3 mốc HH:MM tăng nghiêm ngặt T1 đến T2 đến T3 (T1 < T2 < T3) thành T1 đến T3."""
    def _repl(match: re.Match[str]) -> str:
        t1 = match.group("t1")
        t2 = match.group("t2")
        t3 = match.group("t3")
        m1, m2, m3 = _hhmm_to_minutes(t1), _hhmm_to_minutes(t2), _hhmm_to_minutes(t3)
        if not (m1 < m2 < m3):
            return match.group(0)
        return f"{t1} đến {t3}"

    out = text
    for _ in range(4):
        updated = _POST_ITN_3PT_STUTTER_RE.sub(_repl, out)
        if updated == out:
            break
        out = updated
    return out


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
    out = _STUTTER_COMMA_OR_TRIPLE_TU_NGAY_RE.sub(_replace_stutter_tu_ngay, out)
    out = _STUTTER_TU_TEMPORAL_RE.sub(r"\1", out)
    out = _STUTTER_NGAY_TEMPORAL_RE.sub(r"\1", out)
    out = _MULTI_HSPACE_RE.sub(" ", out)

    # 2. T3.1: Chuẩn hóa Thứ chữ tiếng Việt ("thứ hai".."thứ bảy" -> "Thứ 2".."Thứ 7")
    out = _SPOKEN_WEEKDAY_RE.sub(_replace_spoken_weekday, out)

    # 2a. Chuẩn hóa giờ dạng <giờ_chữ>:<phút_chữ> từ STT (VD Deepgram Nova-3: "bảy:không", "tám:ba")
    out = _normalize_spoken_colon_clock_times(out)

    # 2a2. Chuẩn hóa ngày/tháng viết bằng chữ tiếng Việt (VD: "ngày hai mươi tám tháng mười một" -> "ngày 28 tháng 11")
    out = _normalize_spoken_calendar_dates(out)

    # 2b. Gộp nói lắp 3 mốc thời gian tăng dần trước khi quy đổi buổi
    out = _collapse_pre_itn_stutters(out)

    # 3. T3.1: Chuẩn hóa khoảng giờ nói (kế thừa buổi ở vế sau nếu vế đầu chưa có buổi) & mốc giờ đơn lẻ
    locked_spans: list[tuple[int, int]] = []
    out, locked_spans = _sub_tracking_spans(out, _TIME_RANGE_RE, _replace_time_range, locked_spans)
    out, locked_spans = _sub_tracking_spans(
        out, _SINGLE_TIME_WITH_PERIOD_RE, _replace_single_time_with_period, locked_spans
    )
    out, locked_spans = _sub_tracking_spans(
        out, _UNAMBIGUOUS_24H_TIME_RE, _replace_unambiguous_24h_time, locked_spans
    )

    # 3b. Kế thừa buổi đứng trước trong cùng tiểu mệnh đề (không băng qua dấu câu hoặc hạn/trước/trừ/ngoại trừ)
    out, locked_spans = _propagate_leading_period_in_subclauses(out, locked_spans)

    # 3c. Gộp chuỗi 3 mốc HH:MM tăng nghiêm ngặt T1 đến T2 đến T3 thành T1 đến T3
    out = _collapse_post_itn_stutters(out)

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
