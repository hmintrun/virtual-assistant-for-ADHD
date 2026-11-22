"""Kiểm thử tối ưu độ trễ TTS (hedged request, gộp request trùng, cache kết quả về muộn, prewarm).

Số đo dẫn tới thiết kế: docs/backend/VOICE_STT_BENCHMARK.md §8 (Edge-TTS ~0,6s nhưng có đợt chậm 4–10s).
Provider giả lập, không gọi mạng.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.services.tts_prewarm import prewarm_uf5_checkin_audio, uf5_checkin_prewarm_texts
from app.services.voice_service import TtsError, TtsErrorCode, TtsService, build_tts_service


class _ScriptedProvider:
    """Mỗi lần gọi lấy (độ trễ, kết quả|lỗi) theo kịch bản."""

    name = "scripted"

    def __init__(self, script: list[tuple[float, bytes | Exception]]) -> None:
        self._script = list(script)
        self.calls = 0
        self.cancelled = 0

    async def synthesize(self, text: str, voice: str) -> tuple[bytes, str]:
        delay, outcome = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, "audio/mpeg"


def _svc(provider: _ScriptedProvider, **kw: float) -> TtsService:
    return TtsService(provider, timeout_seconds=kw.get("timeout", 2.0), hedge_delay_seconds=kw.get("hedge", 0.05))


@pytest.mark.asyncio
async def test_fast_request_does_not_hedge() -> None:
    provider = _ScriptedProvider([(0.0, b"fast")])
    svc = _svc(provider)
    result = await svc.synthesize("Xin chào")
    assert result.audio_bytes == b"fast"
    assert provider.calls == 1
    assert svc.hedges_started == 0


@pytest.mark.asyncio
async def test_slow_first_request_is_beaten_by_hedge_and_loser_cancelled() -> None:
    # Lần 1 chậm 5s (Edge vào đợt chậm), lần 2 (hedge) 0.01s → trả sau ~hedge + 0.01s.
    provider = _ScriptedProvider([(5.0, b"slow"), (0.01, b"hedged")])
    svc = _svc(provider)
    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await svc.synthesize("Phiên vừa rồi thế nào?")
    elapsed = loop.time() - started

    assert result.audio_bytes == b"hedged"
    assert svc.hedges_started == 1
    assert elapsed < 0.5
    await asyncio.sleep(0)
    assert provider.cancelled == 1  # request chậm bị hủy, không chạy tiếp vô ích


@pytest.mark.asyncio
async def test_hedge_still_succeeds_when_one_attempt_fails() -> None:
    provider = _ScriptedProvider(
        [(0.2, TtsError(TtsErrorCode.PROVIDER_ERROR, "boom")), (0.3, b"second-ok")]
    )
    result = await _svc(provider).synthesize("Câu A")
    assert result.audio_bytes == b"second-ok"


@pytest.mark.asyncio
async def test_both_attempts_fail_raises_tts_error() -> None:
    provider = _ScriptedProvider([(0.1, TtsError(TtsErrorCode.EMPTY_AUDIO, "x"))])
    with pytest.raises(TtsError) as exc_info:
        await _svc(provider).synthesize("Câu B")
    assert exc_info.value.code == TtsErrorCode.EMPTY_AUDIO
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_hedge_disabled_keeps_single_request() -> None:
    provider = _ScriptedProvider([(0.1, b"only")])
    svc = _svc(provider, hedge=0.0)
    assert (await svc.synthesize("Câu C")).audio_bytes == b"only"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_concurrent_same_text_synthesized_once() -> None:
    provider = _ScriptedProvider([(0.02, b"shared")])
    svc = _svc(provider, hedge=0.0)
    results = await asyncio.gather(*(svc.synthesize("Câu  giống   nhau") for _ in range(5)))
    assert {r.audio_bytes for r in results} == {b"shared"}
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_late_result_after_timeout_is_cached_for_next_request() -> None:
    provider = _ScriptedProvider([(0.3, b"late")])
    svc = _svc(provider, timeout=0.05, hedge=0.0)
    with pytest.raises(TtsError) as exc_info:
        await svc.synthesize("Câu về muộn")
    assert exc_info.value.code == TtsErrorCode.TIMEOUT

    await asyncio.sleep(0.4)  # request vẫn chạy tiếp (shield) và lưu cache
    again = await svc.synthesize("Câu về muộn")
    assert again.cached is True
    assert again.audio_bytes == b"late"
    assert provider.calls == 1


def test_prewarm_texts_cover_all_uf5_templates_without_duplicates() -> None:
    texts = uf5_checkin_prewarm_texts()
    assert len(texts) == len(set(texts)) >= 5
    assert all(t == " ".join(t.split()) for t in texts)


@pytest.mark.asyncio
async def test_prewarm_fills_cache_so_checkin_prompt_is_instant() -> None:
    provider = _ScriptedProvider([(0.0, b"mp3")])
    svc = _svc(provider, hedge=0.0)
    ready = await prewarm_uf5_checkin_audio(svc)
    texts = uf5_checkin_prewarm_texts()
    assert ready == len(texts)

    calls_after_prewarm = provider.calls
    for text in texts:
        assert (await svc.synthesize(text)).cached is True
    assert provider.calls == calls_after_prewarm


@pytest.mark.asyncio
async def test_prewarm_skips_failures_without_raising() -> None:
    provider = _ScriptedProvider([(0.0, TtsError(TtsErrorCode.PROVIDER_ERROR, "down"))])
    assert await _svc(provider, hedge=0.0).prewarm(["A", "B"]) == 0


def test_settings_defaults_enable_hedge_and_prewarm() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.VOICE_TTS_HEDGE_DELAY_SECONDS == 1.2
    assert cfg.VOICE_TTS_PREWARM is True
    assert build_tts_service(cfg)._hedge_delay_seconds == 1.2


@pytest.mark.asyncio
async def test_hedge_consumes_sibling_exception_on_same_tick_win() -> None:
    """PR #22 Minor #4: Nếu winner và task lỗi xong cùng tick, .exception() của sibling phải được thu hồi."""
    import gc

    class _SameTickRaceProvider:
        name = "same-tick-race"

        def __init__(self) -> None:
            self.calls = 0
            self.release = asyncio.Event()

        async def synthesize(self, text: str, voice: str) -> tuple[bytes, str]:
            del text, voice
            call_idx = self.calls
            self.calls += 1
            if call_idx == 0:
                await self.release.wait()
                return b"winner-audio", "audio/mpeg"
            self.release.set()
            raise TtsError(TtsErrorCode.PROVIDER_ERROR, "sibling-boom")

    loop = asyncio.get_running_loop()
    loop_errors: list[dict[str, object]] = []
    prev_handler = loop.get_exception_handler()

    def _capture_handler(_loop, context: dict[str, object]) -> None:
        loop_errors.append(context)

    loop.set_exception_handler(_capture_handler)
    try:
        provider = _SameTickRaceProvider()
        svc = TtsService(provider, timeout_seconds=2.0, hedge_delay_seconds=0.02)
        res = await svc.synthesize("Kiểm tra hedge cùng tick")
        assert res.audio_bytes == b"winner-audio"
        assert svc.hedges_started == 1
        await asyncio.sleep(0.01)
        gc.collect()
        assert not loop_errors
    finally:
        loop.set_exception_handler(prev_handler)
