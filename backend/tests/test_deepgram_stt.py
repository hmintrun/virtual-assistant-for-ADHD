"""Unit & regression tests for Deepgram Nova-3 Tier-1 STT, 3-tier auto fallback,
spoken-colon ITN (<giờ_chữ>:<phút_chữ>), default schedule 'rãnh', and expanded Zone 2 homophones.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

import app.services.voice_service as voice_service_module
from app.core.config import Settings
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMTaskExtractionPayload,
    TaskExtractionResult,
)
from app.services.ai_service import (
    UF1_EXTRACTION_SYSTEM_PROMPT,
    AIService,
    _requests_default_availability,
)
from app.services.draft_service import (
    _availability_proposal,
    _detect_zone2_deadline_confirmation_issue,
    _user_requests_default_availability,
)
from app.services.voice_service import (
    DeepgramSttProvider,
    FallbackSttProvider,
    SttError,
    SttErrorCode,
    SttProviderDetails,
    VoiceService,
    build_voice_service,
)
from app.utils.temporal_parser import extract_temporal_anchors
from app.utils.vietnamese_itn import (
    _normalize_spoken_calendar_dates,
    _normalize_spoken_colon_clock_times,
    normalize_vietnamese_transcript,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Tuesday 2026-11-24 10:00+07:00
FROZEN_NOW = datetime(2026, 11, 24, 10, 0, tzinfo=VN_TZ)


def _build_synthetic_adts_aac() -> bytes:
    """Create a minimal 2-frame valid ADTS AAC stream."""
    payload = b"\x21\x10\x04\x60\x8c\x1c"
    frame_len = 7 + len(payload)
    header = bytes(
        [
            0xFF,
            0xF1,
            (1 << 6) | (8 << 2),  # AAC-LC, 16kHz
            (1 << 6) | ((frame_len >> 11) & 0x03),  # mono
            (frame_len >> 3) & 0xFF,
            ((frame_len & 0x07) << 5) | 0x1F,
            0xFC,
        ]
    )
    return (header + payload) * 2


def _deepgram_ok_json(transcript: str, confidence: float = 0.94) -> dict[str, Any]:
    return {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": transcript,
                            "confidence": confidence,
                        }
                    ]
                }
            ]
        }
    }


# ==============================================================================
# 1. Settings & build_voice_service 3-tier / 2-tier routing
# ==============================================================================


def test_settings_deepgram_key_validation_and_repr_hides_secret() -> None:
    empty_cfg = Settings(_env_file=None, DEEPGRAM_API_KEY="")
    placeholder_cfg = Settings(_env_file=None, DEEPGRAM_API_KEY="your-deepgram-api-key-here")
    valid_cfg = Settings(_env_file=None, DEEPGRAM_API_KEY="dg_secret_live_token_123")

    assert empty_cfg.has_deepgram_key is False
    assert placeholder_cfg.has_deepgram_key is False
    assert valid_cfg.has_deepgram_key is True
    assert valid_cfg.DEEPGRAM_STT_MODEL == "nova-3"
    assert valid_cfg.DEEPGRAM_STT_PUNCTUATE is True
    assert valid_cfg.DEEPGRAM_STT_SMART_FORMAT is False
    assert valid_cfg.DEEPGRAM_STT_USE_KEYTERMS is False
    assert valid_cfg.DEEPGRAM_STT_TIMEOUT_SECONDS == 5.0
    assert "dg_secret_live_token_123" not in repr(valid_cfg)


def test_build_voice_service_auto_and_explicit_deepgram_chains() -> None:
    # auto without ElevenLabs or Deepgram key falls back cleanly to gemini
    cfg_gem_only = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        ELEVENLABS_API_KEY="",
        DEEPGRAM_API_KEY="your-deepgram-api-key-here",
        GEMINI_API_KEY="gem-key",
    )
    assert build_voice_service(cfg_gem_only).provider_name == "gemini"

    # auto with Gemini + ElevenLabs + Deepgram builds 3-tier gemini->elevenlabs->deepgram
    cfg_auto_3t = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-real-key",
        DEEPGRAM_API_KEY="dg-real-key",
    )
    assert build_voice_service(cfg_auto_3t).provider_name == "gemini->elevenlabs->deepgram"

    # auto with Gemini + Deepgram (no ElevenLabs) builds 2-tier gemini->deepgram
    cfg_auto_dg = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        ELEVENLABS_API_KEY="",
        DEEPGRAM_API_KEY="dg-real-key",
        GEMINI_API_KEY="gem-key",
    )
    assert build_voice_service(cfg_auto_dg).provider_name == "gemini->deepgram"

    # explicit deepgram adapts fallback chain based on available keys
    cfg_dg_both = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg-real-key",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-real-key",
    )
    assert build_voice_service(cfg_dg_both).provider_name == "deepgram->gemini->elevenlabs"

    cfg_dg_gem = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg-real-key",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="",
    )
    assert build_voice_service(cfg_dg_gem).provider_name == "deepgram->gemini"

    cfg_dg_el = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg-real-key",
        GEMINI_API_KEY="your-gemini-api-key-here",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="el-real-key",
    )
    assert build_voice_service(cfg_dg_el).provider_name == "deepgram->elevenlabs"

    cfg_dg_only = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg-real-key",
        GEMINI_API_KEY="your-gemini-api-key-here",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="your-elevenlabs-api-key-here",
    )
    assert build_voice_service(cfg_dg_only).provider_name == "deepgram"


@pytest.mark.asyncio
async def test_auto_uf5_checkin_routing_with_and_without_elevenlabs_deepgram() -> None:
    # (a) With Gemini + ElevenLabs: auto swaps uf5_checkin to ElevenLabs first
    cfg_gem_el = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        DEEPGRAM_API_KEY="",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-key",
        VOICE_STT_UF5_PREFER_ELEVENLABS=True,
    )
    svc_2t = build_voice_service(cfg_gem_el)
    assert isinstance(svc_2t._provider, FallbackSttProvider)
    gem_mock_2t = AsyncMock(return_value="Gemini text")
    el_mock_2t = AsyncMock(return_value="Xong rồi")
    cast(Any, svc_2t._provider._primary).transcribe = gem_mock_2t
    cast(Any, svc_2t._provider._fallback).transcribe = el_mock_2t

    res_2t = await svc_2t.transcribe(b"RIFF-uf5", content_type="audio/wav", purpose="uf5_checkin")
    assert res_2t.provider == "elevenlabs"
    assert res_2t.text == "Xong rồi"
    el_mock_2t.assert_awaited_once()
    gem_mock_2t.assert_not_awaited()

    # (b) Full 3-tier gemini->elevenlabs->deepgram: uf5_checkin prefers ElevenLabs first without hitting Deepgram
    cfg_3t = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        DEEPGRAM_API_KEY="dg-real-key",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-real-key",
        VOICE_STT_UF5_PREFER_ELEVENLABS=True,
    )
    svc_3t = build_voice_service(cfg_3t)
    assert svc_3t.provider_name == "gemini->elevenlabs->deepgram"
    assert isinstance(svc_3t._provider, FallbackSttProvider)
    gem_mock_3t = AsyncMock(return_value="Gemini text")
    el_mock_3t = AsyncMock(return_value="Xong phiên này rồi")
    dg_mock_3t = AsyncMock(return_value="Deepgram text")
    cast(Any, svc_3t._provider._primary).transcribe = gem_mock_3t
    cast(Any, svc_3t._provider._fallback).transcribe = el_mock_3t
    cast(Any, svc_3t._provider._tertiary).transcribe = dg_mock_3t

    res_3t = await svc_3t.transcribe(b"RIFF-uf5", content_type="audio/wav", purpose="uf5_checkin")
    assert res_3t.provider == "elevenlabs"
    assert res_3t.text == "Xong phiên này rồi"
    el_mock_3t.assert_awaited_once()
    gem_mock_3t.assert_not_awaited()
    dg_mock_3t.assert_not_awaited()


# ==============================================================================
# 2. DeepgramSttProvider HTTP transport, ADTS remux, keyterms, confidence & errors
# ==============================================================================


@pytest.mark.asyncio
async def test_deepgram_provider_request_headers_params_m4a_content_type_and_confidence() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_deepgram_ok_json("Viết báo cáo 5 trang", confidence=0.91))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepgramSttProvider(
            api_key="dg_live_secret",
            model="nova-3",
            language_code="vi-VN",
            punctuate=True,
            smart_format=False,
            use_keyterms=False,
            http_client=client,
        )
        details = await provider.transcribe_verbose(
            b"fake-m4a-bytes",
            "audio/m4a",
            contextual_hints=["FastAPI", "PostgreSQL"],
        )
        # Injected external client must remain open after provider call
        assert client.is_closed is False

    assert details.text == "Viết báo cáo 5 trang"
    assert details.low_confidence is False
    assert len(captured) == 1
    req = captured[0]
    assert req.headers["Authorization"] == "Token dg_live_secret"
    assert req.headers["Content-Type"] == "audio/mp4"
    params = dict(req.url.params.multi_items())
    assert params["model"] == "nova-3"
    assert params["language"] == "vi"
    assert params["punctuate"] == "true"
    assert params["smart_format"] == "false"
    assert req.url.params.get_list("keyterm") == []


@pytest.mark.asyncio
async def test_deepgram_provider_owned_client_lifecycle_closes_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_clients: list[httpx.AsyncClient] = []
    real_async_client = httpx.AsyncClient
    responses = iter(
        [
            httpx.Response(200, json=_deepgram_ok_json("Nội dung hợp lệ", confidence=0.95)),
            httpx.Response(503, json={"err_msg": "unavailable"}),
        ]
    )

    def _mock_transport_handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    def _client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_mock_transport_handler)
        client = real_async_client(*args, **kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr(voice_service_module.httpx, "AsyncClient", _client_factory)

    provider = DeepgramSttProvider(api_key="dg_owned_client_key", http_client=None)
    details = await provider.transcribe_verbose(b"audio-ok", "audio/wav")
    assert details.text == "Nội dung hợp lệ"
    assert len(created_clients) == 1
    assert created_clients[0].is_closed is True

    with pytest.raises(SttError) as err:
        await provider.transcribe_verbose(b"audio-503", "audio/wav")
    assert err.value.code == SttErrorCode.PROVIDER_ERROR
    assert len(created_clients) == 2
    assert created_clients[1].is_closed is True


@pytest.mark.asyncio
async def test_deepgram_provider_keyterms_sanitization_and_adts_remux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_deepgram_ok_json("Ôn thi FastAPI", confidence=0.60))

    # Force thread offload branch even on small synthetic ADTS bytes
    monkeypatch.setattr(voice_service_module, "_ADTS_THREAD_OFFLOAD_BYTES", 8)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepgramSttProvider(
            api_key="dg_test_key",
            use_keyterms=True,
            http_client=client,
        )
        adts_bytes = _build_synthetic_adts_aac()
        svc = VoiceService(provider, timeout_seconds=5.0, max_audio_bytes=1024 * 1024)
        res = await svc.transcribe(
            adts_bytes,
            content_type="audio/aac",
            filename="clip.aac",
            contextual_hints=["###`FastAPI`\n", "fastapi", "<SQLAlchemy>", "  "],
        )

    assert res.text == "Ôn thi FastAPI"
    assert res.provider == "deepgram"
    assert res.low_confidence is True
    req = captured[0]
    assert req.headers["Content-Type"] == "audio/mp4"
    assert b"ftyp" in req.content[:16]
    assert req.url.params.get_list("keyterm") == ["FastAPI", "SQLAlchemy"]


@pytest.mark.asyncio
async def test_deepgram_provider_confidence_boundary_and_empty_no_speech() -> None:
    responses = iter(
        [
            httpx.Response(200, json=_deepgram_ok_json("Câu đủ rõ", confidence=0.65)),
            httpx.Response(200, json=_deepgram_ok_json("Câu hơi nhỏ", confidence=0.649)),
            httpx.Response(200, json=_deepgram_ok_json("   ", confidence=0.1)),
            httpx.Response(200, json={"results": {"channels": []}}),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepgramSttProvider(api_key="dg_test_key", http_client=client)

        d_ok = await provider.transcribe_verbose(b"audio1", "audio/wav")
        assert d_ok.low_confidence is False

        d_low = await provider.transcribe_verbose(b"audio2", "audio/wav")
        assert d_low.low_confidence is True

        with pytest.raises(SttError) as err1:
            await provider.transcribe_verbose(b"audio3", "audio/wav")
        assert err1.value.code == SttErrorCode.NO_SPEECH

        with pytest.raises(SttError) as err2:
            await provider.transcribe_verbose(b"audio4", "audio/wav")
        assert err2.value.code == SttErrorCode.NO_SPEECH


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, SttErrorCode.PROVIDER_UNAVAILABLE),
        (403, SttErrorCode.PROVIDER_UNAVAILABLE),
        (429, SttErrorCode.RATE_LIMITED),
        (400, SttErrorCode.UNSUPPORTED_AUDIO),
        (415, SttErrorCode.UNSUPPORTED_AUDIO),
        (422, SttErrorCode.UNSUPPORTED_AUDIO),
        (413, SttErrorCode.AUDIO_TOO_LARGE),
        (500, SttErrorCode.PROVIDER_ERROR),
        (503, SttErrorCode.PROVIDER_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_deepgram_provider_http_status_error_mapping(
    status_code: int,
    expected_code: SttErrorCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_key = "dg_super_secret_should_never_log"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"err_msg": "upstream failure"})

    with caplog.at_level(logging.DEBUG):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = DeepgramSttProvider(api_key=secret_key, http_client=client)
            with pytest.raises(SttError) as exc_info:
                await provider.transcribe(b"audio-bytes", "audio/wav")

    assert exc_info.value.code == expected_code
    assert secret_key not in caplog.text
    assert secret_key not in str(exc_info.value)


@pytest.mark.asyncio
async def test_deepgram_provider_missing_key_timeout_and_network_errors() -> None:
    provider_no_key = DeepgramSttProvider(api_key="")
    with pytest.raises(SttError) as err_key:
        await provider_no_key.transcribe(b"audio", "audio/wav")
    assert err_key.value.code == SttErrorCode.PROVIDER_UNAVAILABLE

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        provider_timeout = DeepgramSttProvider(api_key="dg_k", http_client=client)
        with pytest.raises(SttError) as err_to:
            await provider_timeout.transcribe(b"audio", "audio/wav")
        assert err_to.value.code == SttErrorCode.TIMEOUT

    def connect_err_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(connect_err_handler)) as client:
        provider_net = DeepgramSttProvider(api_key="dg_k", http_client=client)
        with pytest.raises(SttError) as err_net:
            await provider_net.transcribe(b"audio", "audio/wav")
        assert err_net.value.code == SttErrorCode.PROVIDER_ERROR


# ==============================================================================
# 3. 3-Tier FallbackSttProvider (gemini -> elevenlabs -> deepgram)
# ==============================================================================


class _StubSttProvider:
    def __init__(
        self,
        name: str,
        *,
        result: SttProviderDetails | str | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self._result = result
        self._error = error
        self._delay = delay
        self.calls = 0

    async def transcribe_verbose(
        self,
        audio: bytes,
        mime_type: str,
        *,
        contextual_hints: list[str] | None = None,
    ) -> SttProviderDetails:
        _ = (audio, mime_type, contextual_hints)
        self.calls += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if isinstance(self._result, SttProviderDetails):
            return self._result
        return SttProviderDetails(text=str(self._result or ""))

    async def transcribe(
        self,
        audio: bytes,
        mime_type: str,
        *,
        contextual_hints: list[str] | None = None,
    ) -> str:
        details = await self.transcribe_verbose(audio, mime_type, contextual_hints=contextual_hints)
        return details.text


@pytest.mark.asyncio
async def test_three_tier_fallback_t1_to_t2_to_t3_and_timeout_headroom() -> None:
    # 1) T1 fails (429) -> T2 fails (503) -> T3 succeeds
    t1 = _StubSttProvider("gemini", error=SttError(SttErrorCode.RATE_LIMITED, "http_429"))
    t2 = _StubSttProvider("elevenlabs", error=SttError(SttErrorCode.PROVIDER_ERROR, "http_503"))
    t3 = _StubSttProvider("deepgram", result=SttProviderDetails(text="Kết quả từ Deepgram", low_confidence=False))
    fb = FallbackSttProvider(
        primary=t1,
        fallback=t2,
        tertiary=t3,
        primary_timeout_seconds=1.0,
        total_timeout_seconds=3.0,
    )
    assert fb.name == "gemini->elevenlabs->deepgram"
    svc = VoiceService(fb, timeout_seconds=3.0, max_audio_bytes=1024 * 1024)
    res = await svc.transcribe(b"audio-bytes", content_type="audio/m4a", filename="a.m4a")
    assert res.text == "Kết quả từ Deepgram"
    assert res.provider == "deepgram"
    assert (t1.calls, t2.calls, t3.calls) == (1, 1, 1)

    # 2) T1 hangs past primary_timeout AND T2 hangs past reserved slice -> T3 still finishes in total budget
    t1_hang = _StubSttProvider("gemini", result="too late", delay=2.0)
    t2_hang = _StubSttProvider("elevenlabs", result="too late 2", delay=2.0)
    t3_fast = _StubSttProvider("deepgram", result="Cứu hộ tầng 3 kịp thời", delay=0.01)
    fb_timeout = FallbackSttProvider(
        primary=t1_hang,
        fallback=t2_hang,
        tertiary=t3_fast,
        primary_timeout_seconds=0.12,
        total_timeout_seconds=0.60,
    )
    svc_to = VoiceService(fb_timeout, timeout_seconds=0.60, max_audio_bytes=1024 * 1024)
    res_to = await svc_to.transcribe(b"audio-bytes", content_type="audio/m4a", filename="b.m4a")
    assert res_to.text == "Cứu hộ tầng 3 kịp thời"
    assert res_to.provider == "deepgram"
    assert (t1_hang.calls, t2_hang.calls, t3_fast.calls) == (1, 1, 1)


@pytest.mark.parametrize(
    "non_retryable_code",
    [SttErrorCode.NO_SPEECH, SttErrorCode.UNSUPPORTED_AUDIO, SttErrorCode.AUDIO_TOO_LARGE],
)
@pytest.mark.asyncio
async def test_three_tier_fallback_does_not_call_t2_or_t3_on_non_retryable_t1_error(
    non_retryable_code: SttErrorCode,
) -> None:
    t1 = _StubSttProvider("gemini", error=SttError(non_retryable_code, "terminal"))
    t2 = _StubSttProvider("elevenlabs", result="should not run")
    t3 = _StubSttProvider("deepgram", result="should not run")
    fb = FallbackSttProvider(primary=t1, fallback=t2, tertiary=t3)
    svc = VoiceService(fb, timeout_seconds=5.0, max_audio_bytes=1024 * 1024)

    with pytest.raises(SttError) as exc_info:
        await svc.transcribe(b"audio-bytes", content_type="audio/m4a", filename="c.m4a")

    assert exc_info.value.code == non_retryable_code
    assert (t1.calls, t2.calls, t3.calls) == (1, 0, 0)


# ==============================================================================
# 4. ITN Spoken Colon Clock Times (<giờ_chữ>:<phút_chữ>) — T03, T32 + Negatives
# ==============================================================================


@pytest.mark.parametrize(
    ("raw_text", "expected_helper"),
    [
        ("bảy:không", "bảy giờ không"),
        ("chín:ba mươi", "chín giờ ba mươi"),
        ("mười:không", "mười giờ không"),
        ("từ tám:ba đến mười:ba", "từ tám giờ ba mươi đến mười giờ ba mươi"),
        ("tám:ba sáng", "tám giờ ba mươi sáng"),
        ("mười:ba tối", "mười giờ ba mươi tối"),
        ("từ tám:ba đến 10:30", "từ tám giờ ba mươi đến 10:30"),
        ("tối thứ năm từ tám:ba đến mười:ba", "tối thứ năm từ tám giờ ba mươi đến mười giờ ba mươi"),
    ],
)
def test_spoken_colon_clock_helper_positive(raw_text: str, expected_helper: str) -> None:
    assert _normalize_spoken_colon_clock_times(raw_text) == expected_helper


def test_spoken_colon_clock_itn_t03_and_t32_end_to_end() -> None:
    t03_dg = (
        "Tối nay từ bảy:không đến chín:ba mươi thì mình viết đề cương khóa luận khoảng bốn trang, "
        "hẹn mười:không tối chủ nhật."
    )
    t03_norm = normalize_vietnamese_transcript(t03_dg)
    assert "19:00 đến 21:30" in t03_norm
    assert "hẹn 22:00 chủ nhật" in t03_norm

    t32_dg = "Tối thứ năm từ tám:ba đến mười:ba, sáng thứ bảy từ tám:ba đến mười:ba."
    t32_norm = normalize_vietnamese_transcript(t32_dg)
    assert "Tối Thứ 5 từ 20:30 đến 22:30" in t32_norm
    assert "sáng Thứ 7 từ 08:30 đến 10:30" in t32_norm


@pytest.mark.parametrize(
    "negative_text",
    [
        "tám:ba",
        "tỷ lệ hai:ba trong báo cáo",
        "tỉ số hai:không nghiêng về đội nhà",
        "chương một:không cần làm phần này",
        "phần hai:ba mươi phút đầu",
        "điểm ba:một",
        "mục bốn:không có gì",
    ],
)
def test_spoken_colon_clock_negative_controls(negative_text: str) -> None:
    assert _normalize_spoken_colon_clock_times(negative_text) == negative_text


# ==============================================================================
# 5. Default Availability 'rảnh|rãnh' (H05) & Expanded Zone 2 Homophones
# ==============================================================================


@pytest.mark.parametrize(
    "phrase",
    [
        "dùng khung giờ rãnh mặc định của tôi nhé",
        "áp dụng khung giờ rãnh mặc định",
        "theo giờ rãnh mặc định",
        "khung giờ rãnh mặc định đã lưu",
        "dùng khung giờ rảnh mặc định",
    ],
)
def test_default_availability_accepts_ranh_with_tilde(phrase: str) -> None:
    assert _user_requests_default_availability(phrase) is True
    assert _requests_default_availability(phrase) is True


def test_default_availability_still_excludes_keep_default_phrase() -> None:
    excluded = "khung giờ 19h đến 21h này chỉ dùng cho báo cáo, lịch mặc định giữ nguyên"
    assert _user_requests_default_availability(excluded) is False
    assert _requests_default_availability(excluded) is False


@pytest.mark.parametrize(
    "zone2_text",
    [
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hảng tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hản tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, háng tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hãng tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hán tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hàng tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hàng 28/11",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hàng ngày 28/11",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hàng chủ nhật",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hẹn chót tối thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hạng chót thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hảng chót tối chủ nhật",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hãng chót 28/11",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hàng chót thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hản chót thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, háng chót thứ bảy",
        "Làm báo cáo 5 trang, rảnh tối mai 19:00-21:00, hán chót thứ bảy",
        "Soạn 6 slide dùng khung giờ rãnh mặc định hảng chót tối thứ bảy",
    ],
)
def test_zone2_expanded_homophones_and_chot_variants_trigger(zone2_text: str) -> None:
    issue = _detect_zone2_deadline_confirmation_issue(
        zone2_text,
        timezone_name="Asia/Ho_Chi_Minh",
        reference_now=FROZEN_NOW,
    )
    assert issue is not None, f"Expected Zone 2 issue for: {zone2_text!r}"
    assert issue.code == "AI_DEADLINE_CONFIRMATION_NEEDED"
    assert issue.field_path == "time_constraint"
    assert issue.blocking is True


@pytest.mark.parametrize(
    "negative_text",
    [
        "Ôn tập 3 chương, rảnh hàng ngày từ 19:00 đến 21:00",
        "Đọc sách hàng ngày 19:00 đến 21:00",
        "Tổng hợp báo cáo hàng tuần thứ sáu",
        "Đối chiếu số liệu hàng tháng ngày 28",
        "Rà soát kế hoạch hàng năm tháng 12",
        "Chuẩn bị tài liệu gặp khách hàng tối mai",
        "Kiểm tra tiến độ đặt hàng thứ bảy",
        "Lên lịch giao hàng thứ bảy lúc 10:00",
        "Hoàn thiện hạng mục 3 vào tối mai",
        "Ôn thi tiếng Hán thứ bảy",
        "Luyện viết chữ Hán tối mai",
        "Làm bài tập môn hệ điều hành thứ bảy",
        "Có buổi hẹn tối mai gặp khách hàng",
        "Làm báo cáo 5 trang rảnh tối mai 19:00-21:00, hẹn đi cà phê tối thứ bảy",
        "Làm báo cáo 5 trang, hẹn đi ăn tối mai",
        "Tìm hiểu hãng xe tối mai",
        "Phân tích báo cáo của hãng thứ bảy",
        "So sánh các hãng công nghệ thứ bảy",
    ],
)
def test_zone2_expanded_homophones_negative_controls(negative_text: str) -> None:
    issue = _detect_zone2_deadline_confirmation_issue(
        negative_text,
        timezone_name="Asia/Ho_Chi_Minh",
        reference_now=FROZEN_NOW,
    )
    assert issue is None, f"Unexpected Zone 2 false positive for: {negative_text!r} -> {issue}"


# ==============================================================================
# 6. UF1 System Prompt Length Budget & Homophone Guidance
# ==============================================================================


def test_uf1_prompt_homophone_guidance_and_budget_under_8000() -> None:
    assert len(UF1_EXTRACTION_SYSTEM_PROMPT) < 8000
    assert "rãnh/đánh/đảnh/cảnh" in UF1_EXTRACTION_SYSTEM_PROMPT
    assert "nam/mời" in UF1_EXTRACTION_SYSTEM_PROMPT

    compact_schema = json.dumps(
        LLMTaskExtractionPayload.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    compact_anchor = json.dumps(
        AIService._build_relative_calendar_anchor(FROZEN_NOW),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    total_chars = len(UF1_EXTRACTION_SYSTEM_PROMPT) + len(compact_schema) + len(compact_anchor)
    assert total_chars < 8000, f"Prompt+schema+anchor budget exceeded: {total_chars}"


# ==============================================================================
# 7. E2E Bugfixes #1–#4 (H14 clarification_hint, Spoken Calendar Dates, H09/T32 Period Split, T27 Zone 2)
# ==============================================================================


def test_bug1_h14_clarification_hint_forces_ambiguous_and_unresolved_in_t3_and_t4() -> None:
    """Bug #1: Non-empty clarification_hint must force is_ambiguous=True, is_complete=False, and unresolved in T4."""
    conflict_hint = (
        "Khung giờ 'rảnh tối Thứ 5 ngày 28 tháng 11 từ 19:00 đến 21:00' đang có mâu thuẫn giữa thứ và ngày: "
        "bạn muốn xếp vào 2026-11-26 hay 2026-11-28?"
    )
    conflicting_win = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-28",
        start_local="19:00",
        end_local="21:00",
        op="add",
        raw_phrase="rảnh tối Thứ 5 ngày 28 tháng 11 từ 19:00 đến 21:00",
        is_ambiguous=False,
        clarification_hint=conflict_hint,
    )
    deadline_dt = datetime(2026, 11, 30, 22, 0, tzinfo=VN_TZ)
    raw = TaskExtractionResult(
        title="Báo cáo kiểm toán",
        content="Hoàn thiện bảy trang báo cáo kiểm toán",
        scope="7 trang",
        summary="Hoàn thiện bảy trang báo cáo kiểm toán.",
        structured_free_windows=[conflicting_win],
        deadline=deadline_dt,
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=deadline_dt,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        is_complete=False,
        clarification_questions=[],
        generation_tier="tier_1_gemini",
    )

    # T3 guardrail: even if input had no conflict anchor, non-empty clarification_hint forces ambiguity & incomplete
    verified = AIService._enforce_uf1_temporal_guardrails(
        raw,
        current_dt=FROZEN_NOW,
        timezone="Asia/Ho_Chi_Minh",
        user_input="Hoàn thiện bảy trang báo cáo kiểm toán, rảnh 19:00 đến 21:00, hạn 30/11 lúc 22:00.",
    )
    assert verified.is_complete is False
    assert verified.structured_free_windows[0].is_ambiguous is True
    assert conflict_hint in verified.clarification_questions

    # T4 defense-in-depth: even if LLM output reaches _availability_proposal with is_ambiguous=False + hint -> unresolved!
    fake_draft = SimpleNamespace(
        ai_context={},
        summary=SimpleNamespace(availability=None, time_constraint=None),
    )
    _avail, state = _availability_proposal(raw, fake_draft, None, "Asia/Ho_Chi_Minh")
    assert state is not None
    _proposal_windows, unresolved = state
    assert len(unresolved) == 1
    assert unresolved[0]["is_ambiguous"] is True
    assert unresolved[0]["clarification_hint"] == conflict_hint

    # Negative control: clean window without clarification_hint stays resolved
    clean_win = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-26",
        start_local="19:00",
        end_local="21:00",
        op="add",
        raw_phrase="tối Thứ 5 từ 19:00 đến 21:00",
        is_ambiguous=False,
        clarification_hint=None,
    )
    clean_raw = raw.model_copy(update={"structured_free_windows": [clean_win]})
    _clean_avail, clean_state = _availability_proposal(
        clean_raw, fake_draft, None, "Asia/Ho_Chi_Minh"
    )
    assert clean_state is not None
    assert clean_state[1] == []


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("ngày hai mươi tám tháng mười một", "ngày 28 tháng 11"),
        ("hảng hai mươi tám tháng mười một", "hảng 28 tháng 11"),
        ("hạn ba mươi tháng mười một lúc hai mươi hai giờ", "hạn 30 tháng 11 lúc hai mươi hai giờ"),
        (
            "hàng chủ nhật hai mươi chín tháng mười một lúc hai mươi hai giờ",
            "hàng chủ nhật 29 tháng 11 lúc hai mươi hai giờ",
        ),
        ("mùng năm tháng chín", "mùng 5 tháng 9"),
        ("ngày 28 tháng mười một", "ngày 28 tháng 11"),
    ],
)
def test_bug2_spoken_calendar_dates_positive(raw_text: str, expected: str) -> None:
    assert _normalize_spoken_calendar_dates(raw_text) == expected


@pytest.mark.parametrize(
    "negative_text",
    [
        "ba tháng trước mình đã làm xong",
        "năm tháng nữa mới thi",
        "mười hai tháng sau kiểm tra lại",
        "hai tháng qua hơi bận",
        "khám định kỳ sáu tháng một lần",
        "nộp báo cáo ba tháng hai bài",
        "ngày ba mươi mốt tháng hai",
    ],
)
def test_bug2_spoken_calendar_dates_negative_controls(negative_text: str) -> None:
    assert _normalize_spoken_calendar_dates(negative_text) == negative_text


def test_bug2_h14_conflict_and_t06_zone2_after_spoken_date_itn() -> None:
    h14_raw = (
        "Hoàn thiện bảy trang báo cáo kiểm toán, rảnh tối thứ năm ngày hai mươi tám tháng mười một "
        "từ bảy giờ đến chín giờ tối, hạn ba mươi tháng mười một lúc hai mươi hai giờ."
    )
    h14_norm = normalize_vietnamese_transcript(h14_raw)
    assert "Thứ 5 ngày 28 tháng 11" in h14_norm
    assert "hạn 30 tháng 11" in h14_norm
    h14_anchors = extract_temporal_anchors(h14_norm, reference_now=FROZEN_NOW)
    assert any(a.weekday_date_conflict is True and a.explicit_date == "2026-11-28" for a in h14_anchors)

    t06_raw = (
        "Ôn tập FastAPI viết ba unit test: rảnh tối mai từ mười chín giờ đến hai mươi mốt giờ "
        "(hảng hai mươi tám tháng mười một)."
    )
    t06_issue = _detect_zone2_deadline_confirmation_issue(
        t06_raw,
        timezone_name="Asia/Ho_Chi_Minh",
        reference_now=FROZEN_NOW,
    )
    assert t06_issue is not None
    assert t06_issue.code == "AI_DEADLINE_CONFIRMATION_NEEDED"
    assert "28/11/2026" in t06_issue.question


def test_bug3_h09_and_t32_do_not_steal_next_clause_period_and_preserve_legitimate_trailing() -> None:
    # H09: 'tối' belongs to 'tối thứ tư bận họp', NOT to morning range 'bảy giờ đến chín giờ'
    h09_raw = (
        "Làm báo cáo doanh thu BA trang rảnh sáng thứ tư từ bảy giờ đến chín giờ "
        "tối thứ tư bận họp hạn hai mươi tám tháng mười một lúc mười bảy giờ"
    )
    h09_norm = normalize_vietnamese_transcript(h09_raw)
    assert "sáng Thứ 4 từ 07:00 đến 09:00, tối Thứ 4 bận họp" in h09_norm
    assert "19:00" not in h09_norm
    assert "21:00" not in h09_norm

    # T32: unpunctuated 'tối Thứ 5 từ ... tối Thứ 5 mình bận ...' preserves both ranges and exclude period
    t32_raw = (
        "vào tối thứ tư ngày mai mình rảnh từ bảy:ba mươi đến chín:ba mươi "
        "tối thứ năm từ tám:ba đến mười:ba tối thứ năm mình bận đi họp nhóm rồi "
        "chuyển sang tối thứ sáu từ bảy giờ đến khoảng chín:ba mươi tối"
    )
    t32_norm = normalize_vietnamese_transcript(t32_raw)
    assert "19:30 đến 21:30, tối Thứ 5 từ 20:30 đến 22:30, tối Thứ 5 mình bận" in t32_norm
    assert "19:00 đến khoảng 21:30" in t32_norm

    # Negative controls: legitimate trailing periods remain intact
    assert (
        normalize_vietnamese_transcript("rảnh từ bảy giờ đến chín giờ tối thứ Sáu")
        == "rảnh từ 19:00 đến 21:00 Thứ 6"
    )
    assert (
        normalize_vietnamese_transcript("tối mai từ bảy giờ đến chín giờ tối")
        == "tối mai từ 19:00 đến 21:00"
    )
    assert (
        normalize_vietnamese_transcript("đổi thành 7:00 đến 9:00 tối mai đi.")
        == "đổi thành 19:00 đến 21:00 mai đi."
    )
    assert normalize_vietnamese_transcript("từ 8 giờ đến 5 giờ chiều") == "từ 08:00 đến 17:00"


def test_bug4_t27_zone2_triggers_after_clock_gio_and_blocks_noun_gio_hen() -> None:
    # Positive: clock time ending with 'giờ' followed by unpunctuated 'hẹn <deadline>'
    for positive in (
        "Làm năm bài tập xác suất thống kê rảnh tối thứ năm từ bảy giờ đến chín giờ hẹn tối thứ bảy",
        "Làm báo cáo 5 trang rảnh 19 giờ đến 21 giờ hẹn tối thứ bảy",
    ):
        issue = _detect_zone2_deadline_confirmation_issue(
            positive,
            timezone_name="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        assert issue is not None, f"Expected Zone 2 issue for: {positive!r}"
        assert issue.code == "AI_DEADLINE_CONFIRMATION_NEEDED"

    # Negative controls: noun phrase 'giờ hẹn' must NOT trigger Zone 2
    for negative in (
        "Có giờ hẹn tối mai",
        "Giờ hẹn tối mai là 19:00",
        "Trễ giờ hẹn tối thứ bảy",
        "Nhắc giờ hẹn tối mai",
    ):
        issue = _detect_zone2_deadline_confirmation_issue(
            negative,
            timezone_name="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        assert issue is None, f"Unexpected Zone 2 false positive for: {negative!r} -> {issue}"
