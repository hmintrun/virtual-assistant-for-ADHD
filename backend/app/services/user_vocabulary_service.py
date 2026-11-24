"""Trụ cột 1b — Dynamic Personal Vocabulary Biasing (Zero Overfitting, Zero PII).

Trích xuất động danh sách cụm từ khóa / danh từ riêng / thuật ngữ nổi bật (<= 30 cụm)
từ dữ liệu cá nhân gần nhất của chính người dùng (Task.title, PlanningDraft.summary['title'],
PlanningContext.event_snapshots) để mớm ngữ cảnh cho tầng STT mà không dùng từ điển cố định.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import PlanningContext
from app.models.planning import PlanningDraft
from app.models.task import Task

logger = logging.getLogger(__name__)

_EMAIL_PII_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_PII_RE = re.compile(
    r"(?:https?://|ftp://|www\.)\S+"
    r"|\b(?:[a-z0-9-]+\.)+(?:com|net|org|edu|gov|vn|io|dev|app|co|me|info|biz|local)(?:/\S*)?\b",
    re.IGNORECASE,
)
_UUID_PII_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_DIGIT_SPAN_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")
_PROMPT_INJECTION_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f#`<>\"\\]+")
_CLAUSE_SPLIT_RE = re.compile(r"[|,:;!?()\[\]{}\"\n\r\t/\\]+|\s+[-–—]\s+")
_WORD_TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹĐđ0-9][A-Za-zÀ-ỹĐđ0-9+._'\-]*")

# Stopwords ngữ pháp & động từ/thời gian phổ thông tiếng Việt + tiếng Anh (không chứa thuật ngữ chuyên ngành)
_COMMON_STOPWORDS: frozenset[str] = frozenset(
    {
        # Tiếng Việt phổ thông & từ chức năng
        "của",
        "và",
        "hoặc",
        "hay",
        "nhưng",
        "mà",
        "thì",
        "là",
        "với",
        "cho",
        "vào",
        "lúc",
        "từ",
        "đến",
        "tới",
        "trong",
        "ngoài",
        "trên",
        "dưới",
        "về",
        "theo",
        "tại",
        "ở",
        "để",
        "khi",
        "nào",
        "trước",
        "sau",
        "cần",
        "phải",
        "muốn",
        "nên",
        "mình",
        "bạn",
        "tôi",
        "em",
        "anh",
        "chị",
        "chúng",
        "nhóm",
        "nhé",
        "nha",
        "ạ",
        "ơi",
        "này",
        "kia",
        "đó",
        "ấy",
        "các",
        "những",
        "một",
        "hai",
        "ba",
        "bốn",
        "tư",
        "năm",
        "sáu",
        "bảy",
        "bẩy",
        "tám",
        "chín",
        "mười",
        "có",
        "không",
        "chưa",
        "đã",
        "đang",
        "sẽ",
        "được",
        "hãy",
        "giúp",
        "xin",
        "cái",
        "con",
        "chiếc",
        "như",
        "vì",
        "do",
        "bởi",
        "rất",
        "quá",
        "lắm",
        "cũng",
        "đều",
        "mới",
        "cũ",
        "chỉ",
        "vừa",
        "bị",
        "nếu",
        "thôi",
        "hết",
        "nữa",
        "thêm",
        "xong",
        "lại",
        "ra",
        "lên",
        "xuống",
        "sang",
        "qua",
        # Động từ tác vụ & danh từ chung không mang tính phân biệt âm học riêng
        "làm",
        "viết",
        "đọc",
        "học",
        "ôn",
        "tập",
        "soạn",
        "nộp",
        "gửi",
        "xem",
        "kiểm",
        "tra",
        "sửa",
        "chỉnh",
        "hoàn",
        "thành",
        "chuẩn",
        "bắt",
        "đầu",
        "kết",
        "thúc",
        "cập",
        "nhật",
        "tạo",
        "thực",
        "hiện",
        "liên",
        "hệ",
        "gặp",
        "họp",
        "buổi",
        "cuộc",
        "việc",
        "công",
        "bài",
        "bản",
        "nháp",
        "phần",
        "trang",
        "chương",
        "mục",
        "câu",
        "môn",
        "lớp",
        "kỳ",
        "tuần",
        "tháng",
        "ngày",
        "hôm",
        "nay",
        "mai",
        "mốt",
        "sáng",
        "trưa",
        "chiều",
        "tối",
        "đêm",
        "khuya",
        "thứ",
        "chủ",
        "cn",
        "giờ",
        "phút",
        "tiếng",
        "hạn",
        "chót",
        "rảnh",
        "bận",
        "lịch",
        "phương",
        "án",
        # Tiếng Anh phổ thông
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "for",
        "nor",
        "with",
        "without",
        "from",
        "into",
        "onto",
        "to",
        "in",
        "on",
        "at",
        "by",
        "of",
        "about",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "does",
        "did",
        "will",
        "would",
        "should",
        "can",
        "could",
        "my",
        "our",
        "your",
        "their",
        "his",
        "her",
        "its",
        "this",
        "that",
        "these",
        "those",
        "task",
        "tasks",
        "todo",
        "draft",
        "meeting",
        "call",
        "sync",
        "work",
        "study",
        "review",
        "finish",
        "complete",
        "prepare",
        "write",
        "read",
        "update",
        "fix",
        "check",
        "today",
        "tomorrow",
        "daily",
        "weekly",
        "before",
        "after",
        "deadline",
        "page",
        "pages",
    }
)


def _scrub_pii(raw_text: str) -> str:
    """Loại bỏ triệt để ký tự điều khiển/prompt-injection, Email, URL, UUID và chuỗi số >= 8 chữ số."""
    text = unicodedata.normalize("NFC", raw_text or "")
    text = _EMAIL_PII_RE.sub(" | ", text)
    text = _URL_PII_RE.sub(" | ", text)
    text = _UUID_PII_RE.sub(" | ", text)

    def _replace_long_digits(match: re.Match[str]) -> str:
        span = match.group(0)
        if sum(ch.isdigit() for ch in span) >= 8:
            return " | "
        return span

    text = _LONG_DIGIT_SPAN_RE.sub(_replace_long_digits, text)
    return _PROMPT_INJECTION_UNSAFE_RE.sub(" | ", text)


def _is_safe_non_pii_phrase(phrase: str) -> bool:
    """Hàng rào hậu kiểm đảm bảo cụm từ gợi ý tuyệt đối không chứa PII."""
    if not phrase or "@" in phrase:
        return False
    low = phrase.lower()
    if "http://" in low or "https://" in low or "ftp://" in low or "www." in low:
        return False
    if sum(ch.isdigit() for ch in phrase) >= 8:
        return False
    if _EMAIL_PII_RE.search(phrase) or _URL_PII_RE.search(phrase) or _UUID_PII_RE.search(phrase):
        return False
    return True


def _clean_token(raw_tok: str) -> str:
    return raw_tok.strip("._'-")


def _is_technical_or_acronym_token(tok: str) -> bool:
    """Nhận diện cấu trúc từ kỹ thuật / viết tắt / mã môn / CamelCase (không dùng từ điển cứng)."""
    if len(tok) < 2 or tok.isdigit():
        return False
    alpha_chars = [ch for ch in tok if ch.isalpha()]
    if not alpha_chars:
        return False
    upper_count = sum(ch.isupper() for ch in alpha_chars)
    # Viết tắt (VD: ADHD, API, SQL) hoặc CamelCase / nội tự viết hoa (VD: FastAPI, PostgreSQL, PyTorch, iOS)
    if upper_count >= 2:
        return True
    if any(ch.isupper() for ch in tok[1:]):
        return True
    # Ký hiệu kỹ thuật hoặc mã chữ+số ngắn < 8 chữ số (VD: C++, Node.js, OAuth2, CS101)
    if any(sym in tok for sym in ("+", ".")) and len(alpha_chars) >= 1:
        return True
    if any(ch.isdigit() for ch in tok) and sum(ch.isdigit() for ch in tok) <= 4:
        return True
    return False


def _is_capitalized_word(tok: str) -> bool:
    return bool(tok) and tok[0].isupper() and any(ch.isalpha() for ch in tok)


def extract_salient_vocabulary_hints(
    texts: list[str],
    *,
    max_hints: int = 30,
    max_phrase_chars: int = 48,
) -> list[str]:
    """Tách tối đa ``max_hints`` cụm danh từ riêng / thuật ngữ / từ khóa chuyên đề không chứa PII."""
    if max_hints <= 0 or not texts:
        return []

    priority_candidates: list[str] = []
    content_candidates: list[str] = []

    for raw_text in texts:
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        scrubbed = _scrub_pii(raw_text)
        for clause in _CLAUSE_SPLIT_RE.split(scrubbed):
            clause_stripped = clause.strip()
            if not clause_stripped:
                continue
            raw_tokens = [_clean_token(m.group(0)) for m in _WORD_TOKEN_RE.finditer(clause_stripped)]
            tokens = [t for t in raw_tokens if t and sum(ch.isdigit() for ch in t) < 8]
            if not tokens:
                continue

            # 1. Trích xuất token kỹ thuật / viết tắt & chuỗi danh từ riêng viết hoa
            n_toks = len(tokens)
            idx = 0
            while idx < n_toks:
                tok = tokens[idx]
                tok_low = tok.casefold()
                if _is_technical_or_acronym_token(tok) and tok_low not in _COMMON_STOPWORDS:
                    priority_candidates.append(tok)

                if _is_capitalized_word(tok) and tok_low not in _COMMON_STOPWORDS:
                    cap_span = [tok]
                    j = idx + 1
                    while (
                        j < n_toks
                        and len(cap_span) < 4
                        and _is_capitalized_word(tokens[j])
                        and tokens[j].casefold() not in _COMMON_STOPWORDS
                    ):
                        cap_span.append(tokens[j])
                        j += 1
                    if len(cap_span) >= 2:
                        priority_candidates.append(" ".join(cap_span))
                        idx = j
                        continue
                    # Từ viết hoa đơn lẻ giữa mệnh đề (không đứng đầu câu) thường là tên riêng / thuật ngữ
                    if idx > 0 and len(tok) >= 3:
                        priority_candidates.append(tok)
                idx += 1

            # 2. Tách cụm từ khóa nội dung sau khi loại bỏ stopword phổ thông
            current_run: list[str] = []
            for tok in tokens:
                tok_low = tok.casefold()
                if tok_low in _COMMON_STOPWORDS or tok.isdigit() or len(tok) < 2:
                    if current_run:
                        content_candidates.append(" ".join(current_run[:4]))
                        current_run = []
                    continue
                current_run.append(tok)
                if len(current_run) == 4:
                    content_candidates.append(" ".join(current_run))
                    current_run = []
            if current_run:
                content_candidates.append(" ".join(current_run[:4]))

    seen_folded: set[str] = set()
    hints: list[str] = []

    for candidate in (*priority_candidates, *content_candidates):
        cleaned = _PROMPT_INJECTION_UNSAFE_RE.sub(" ", candidate)
        cleaned = " ".join(cleaned.split()).strip(" ,.;:-–—")
        if len(cleaned) < 3 or len(cleaned) > max_phrase_chars:
            continue
        if cleaned.isdigit() or cleaned.casefold() in _COMMON_STOPWORDS:
            continue
        if not _is_safe_non_pii_phrase(cleaned):
            continue
        folded = cleaned.casefold()
        if folded in seen_folded:
            continue
        seen_folded.add(folded)
        hints.append(cleaned)
        if len(hints) >= max_hints:
            break

    return hints


class UserVocabularyService:
    """Bộ nhớ đệm LRU + TTL cung cấp từ vựng cá nhân hóa an toàn cho tầng STT."""

    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 600.0,
        max_hints: int = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl_seconds = max(1.0, ttl_seconds)
        self._max_hints = max(1, max_hints)
        self._clock = clock
        self._cache: OrderedDict[UUID, tuple[float, tuple[str, ...]]] = OrderedDict()

    @staticmethod
    def _parse_user_uuid(user_id: str | UUID | None) -> UUID | None:
        if user_id is None:
            return None
        if isinstance(user_id, UUID):
            return user_id
        try:
            return UUID(str(user_id).strip())
        except (ValueError, TypeError, AttributeError):
            return None

    def _evict_expired(self, now_mono: float) -> None:
        expired = [uid for uid, (exp, _) in self._cache.items() if exp <= now_mono]
        for uid in expired:
            self._cache.pop(uid, None)

    def invalidate(self, user_id: str | UUID | None = None) -> None:
        """Xóa cache từ vựng của 1 ``user_id`` (hoặc xóa toàn bộ nếu ``user_id=None``)."""
        if user_id is None:
            self._cache.clear()
            return
        uid = self._parse_user_uuid(user_id)
        if uid is not None:
            self._cache.pop(uid, None)

    async def get_hints(
        self,
        db: AsyncSession | None,
        user_id: str | UUID | None,
    ) -> list[str]:
        """Lấy tối đa 30 cụm từ khóa cá nhân từ DB (read-only, fail-open trả ``[]`` khi lỗi)."""
        if db is None:
            return []
        uid = self._parse_user_uuid(user_id)
        if uid is None:
            return []

        now_mono = self._clock()
        self._evict_expired(now_mono)
        cached = self._cache.get(uid)
        if cached is not None:
            expires_at, cached_hints = cached
            if now_mono < expires_at:
                self._cache.move_to_end(uid)
                return list(cached_hints)
            self._cache.pop(uid, None)

        try:
            raw_texts: list[str] = []

            task_stmt = (
                select(Task.title)
                .where(Task.user_id == uid, Task.status != "cancelled")
                .order_by(Task.updated_at.desc(), Task.id.desc())
                .limit(20)
            )
            for title in (await db.scalars(task_stmt)).all():
                if isinstance(title, str) and title.strip():
                    raw_texts.append(title)

            draft_stmt = (
                select(PlanningDraft.summary)
                .where(PlanningDraft.user_id == uid)
                .order_by(PlanningDraft.updated_at.desc(), PlanningDraft.id.desc())
                .limit(20)
            )
            for summary_dict in (await db.scalars(draft_stmt)).all():
                if isinstance(summary_dict, dict):
                    draft_title = summary_dict.get("title")
                    if isinstance(draft_title, str) and draft_title.strip():
                        raw_texts.append(draft_title)

            ctx_stmt = (
                select(PlanningContext.event_snapshots)
                .where(PlanningContext.user_id == uid)
                .order_by(PlanningContext.fetched_at.desc(), PlanningContext.id.desc())
                .limit(5)
            )
            for snapshots in (await db.scalars(ctx_stmt)).all():
                if not isinstance(snapshots, list):
                    continue
                for event_item in snapshots:
                    if not isinstance(event_item, dict):
                        continue
                    ev_label: Any = event_item.get("summary") or event_item.get("title")
                    if isinstance(ev_label, str) and ev_label.strip():
                        raw_texts.append(ev_label)

            hints = extract_salient_vocabulary_hints(raw_texts, max_hints=self._max_hints)
            stored_tuple = tuple(hints)
            done_mono = self._clock()
            self._cache[uid] = (done_mono + self._ttl_seconds, stored_tuple)
            self._cache.move_to_end(uid)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            return hints
        except (SQLAlchemyError, Exception) as exc:
            logger.warning(
                "UserVocabularyService fail-open (error=%s).",
                type(exc).__name__,
            )
            return []


@lru_cache(maxsize=1)
def get_user_vocabulary_service() -> UserVocabularyService:
    """Singleton ``UserVocabularyService`` dùng chung cho Voice Router."""
    return UserVocabularyService()
