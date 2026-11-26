"""Tổng hợp sẵn giọng đọc các câu hỏi Check-in UF5 khi server khởi động (giảm độ trễ TTS).

Câu hỏi Check-in sinh từ mẫu cố định trong ``AIService.build_uf5_checkin_prompt`` (không gọi LLM).
Các biến thể không kèm ``expected_output`` là hữu hạn → render sẵn vào LRU cache của ``TtsService``
để ``POST /voice/uf5-checkin-prompt`` trả audio ngay (cache hit, ~0 ms TTS) thay vì chờ Edge-TTS.
"""

from __future__ import annotations

import logging

from app.services.ai_service import ai_service
from app.services.voice_service import TtsService

logger = logging.getLogger(__name__)

_KINDS = ("mid", "final")
_FINAL_FLAGS = (False, True)
_OUTCOMES: tuple[str | None, ...] = (
    None,
    "completed",
    "partial",
    "incomplete",
    "busy",
    "skipped",
    "unknown",
)


def uf5_checkin_prewarm_texts() -> list[str]:
    """Mọi câu hỏi Check-in UF5 có thể sinh ra khi KHÔNG có expected_output (đã bỏ trùng, giữ thứ tự)."""
    seen: dict[str, None] = {}
    for kind in _KINDS:
        for is_final in _FINAL_FLAGS:
            for outcome in _OUTCOMES:
                prompt = ai_service.build_uf5_checkin_prompt(
                    kind=kind,  # type: ignore[arg-type]
                    is_final_session_of_step=is_final,
                    expected_output=None,
                    session_outcome=outcome,
                )
                text = " ".join(prompt.prompt_text.split())
                if text:
                    seen.setdefault(text, None)
    return list(seen)


async def prewarm_uf5_checkin_audio(tts: TtsService) -> int:
    """Render sẵn audio các câu Check-in; lỗi từng câu chỉ ghi log, không chặn server."""
    texts = uf5_checkin_prewarm_texts()
    ready = await tts.prewarm(texts)
    logger.info("Prewarm TTS Check-in UF5: %d/%d câu sẵn sàng.", ready, len(texts))
    return ready
