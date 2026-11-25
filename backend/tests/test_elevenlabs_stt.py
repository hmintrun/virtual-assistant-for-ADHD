"""Unit tests cho ElevenLabsScribe v2 STT (ElevenLabsSttProvider) và chuỗi STT 3 tầng mới:
Gemini 3.5 Transcribe (7s) -> ElevenLabs Scribe v2 -> Deepgram Nova-3 (bỏ Groq STT).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.voice_service import (
    DeepgramSttProvider,
    ElevenLabsSttProvider,
    FallbackSttProvider,
    SttError,
    SttErrorCode,
    SttProviderDetails,
    VoiceService,
    _iso_language_code,
    build_voice_service,
    map_provider_exception,
)


def test_settings_elevenlabs_validation_defaults_and_no_groq_stt_provider() -> None:
    empty_cfg = Settings(_env_file=None, ELEVENLABS_API_KEY="")
    placeholder_cfg = Settings(_env_file=None, ELEVENLABS_API_KEY="your-elevenlabs-api-key-here")
    valid_cfg = Settings(_env_file=None, ELEVENLABS_API_KEY="sk_el_secret_test_12345")

    assert empty_cfg.has_elevenlabs_key is False
    assert placeholder_cfg.has_elevenlabs_key is False
    assert valid_cfg.has_elevenlabs_key is True
    assert valid_cfg.ELEVENLABS_STT_MODEL == "scribe_v2"
    assert valid_cfg.ELEVENLABS_STT_BASE_URL == "https://api.elevenlabs.io/v1/speech-to-text"
    assert valid_cfg.ELEVENLABS_STT_TAG_AUDIO_EVENTS is False
    assert valid_cfg.ELEVENLABS_STT_DIARIZE is False
    assert valid_cfg.ELEVENLABS_STT_TIMEOUT_SECONDS == 5.0
    assert valid_cfg.VOICE_STT_PRIMARY_TIMEOUT_SECONDS == 7.0
    assert valid_cfg.VOICE_STT_TIMEOUT_SECONDS == 12.0
    assert valid_cfg.VOICE_STT_UF5_PREFER_ELEVENLABS is True
    assert not hasattr(valid_cfg, "GROQ_STT_MODEL")
    assert not hasattr(valid_cfg, "VOICE_STT_UF5_PREFER_GROQ")
    assert Settings(_env_file=None, ELEVENLABS_API_KEY="   ").has_elevenlabs_key is False
    assert "sk_el_secret_test_12345" not in repr(valid_cfg)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, VOICE_STT_PROVIDER="groq")  # type: ignore[arg-type]


def test_build_voice_service_elevenlabs_chains_and_missing_key() -> None:
    # 1. Explicit elevenlabs + gemini + deepgram -> elevenlabs->gemini->deepgram
    cfg_el_3t = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="elevenlabs",
        ELEVENLABS_API_KEY="el-key",
        GEMINI_API_KEY="gem-key",
        DEEPGRAM_API_KEY="dg-key",
    )
    svc_3t = build_voice_service(cfg_el_3t)
    assert svc_3t.provider_name == "elevenlabs->gemini->deepgram"
    assert svc_3t._provider._primary_timeout_seconds == 5.0  # type: ignore[attr-defined]

    # 2. auto with all 3 keys -> gemini->elevenlabs->deepgram (7.0s primary timeout, 5.0s for uf5_checkin)
    cfg_auto = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-key",
        DEEPGRAM_API_KEY="dg-key",
    )
    svc_auto = build_voice_service(cfg_auto)
    assert svc_auto.provider_name == "gemini->elevenlabs->deepgram"
    assert isinstance(svc_auto._provider, FallbackSttProvider)
    assert svc_auto._provider._primary_timeout_seconds == 7.0
    assert svc_auto._provider._prefer_fallback_primary_timeout_seconds == 5.0

    # 3. auto with ElevenLabs + Deepgram only (no Gemini auth) -> elevenlabs->deepgram
    cfg_no_gem = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="el-key",
        DEEPGRAM_API_KEY="dg-key",
    )
    assert build_voice_service(cfg_no_gem).provider_name == "elevenlabs->deepgram"

    # 4. auto with ElevenLabs only -> elevenlabs
    cfg_el_only = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="el-key",
        DEEPGRAM_API_KEY="",
    )
    assert build_voice_service(cfg_el_only).provider_name == "elevenlabs"

    # 4b. auto with Deepgram only -> deepgram (5.0s timeout)
    cfg_dg_only = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="",
        DEEPGRAM_API_KEY="dg-key",
    )
    svc_dg_only = build_voice_service(cfg_dg_only)
    assert svc_dg_only.provider_name == "deepgram"
    assert isinstance(svc_dg_only._provider, DeepgramSttProvider)
    assert svc_dg_only._provider._timeout_seconds == 5.0

    # 4c. auto with all 3 keys missing -> builds gemini (sẽ raise PROVIDER_UNAVAILABLE khi gọi)
    cfg_none = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="",
        DEEPGRAM_API_KEY="",
    )
    svc_none = build_voice_service(cfg_none)
    assert svc_none.provider_name == "gemini"

    # 5. explicit gemini with both elevenlabs + deepgram -> flattened 3-tier chain, _fallback_provider is None
    cfg_gem = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="gemini",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-key",
        DEEPGRAM_API_KEY="dg-key",
    )
    svc_gem = build_voice_service(cfg_gem)
    assert svc_gem.provider_name == "gemini->elevenlabs->deepgram"
    assert svc_gem._fallback_provider is None


@pytest.mark.asyncio
async def test_elevenlabs_provider_sends_raw_aac_headers_params_and_no_secret_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "language_code": "vie",
                "language_probability": 1.0,
                "text": "  Làm báo cáo năm trang, hạn tối mai  ",
            },
        )

    raw_aac = b"\xff\xf1\x50\x80\x02\x1f\xfcraw-aac-bytes"
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http_client:
        provider = ElevenLabsSttProvider(
            api_key="el_secret_never_log",
            model="scribe_v2",
            language_code="vi-VN",
            tag_audio_events=False,
            diarize=False,
            http_client=http_client,
        )
        svc = VoiceService(provider, timeout_seconds=5.0, max_audio_bytes=1024 * 1024)
        with caplog.at_level(logging.INFO):
            res = await svc.transcribe(raw_aac, content_type="audio/aac", filename="voice.aac")

    assert res.provider == "elevenlabs"
    assert res.text == "Làm báo cáo năm trang, hạn tối mai"
    assert res.low_confidence is False
    assert len(captured) == 1
    req = captured[0]
    assert req.headers["xi-api-key"] == "el_secret_never_log"
    assert b"audio.aac" in req.content
    assert b"audio/aac" in req.content
    assert raw_aac in req.content
    assert b"scribe_v2" in req.content
    assert "el_secret_never_log" not in caplog.text
    assert "Làm báo cáo" not in caplog.text


@pytest.mark.asyncio
async def test_elevenlabs_empty_transcript_raises_no_speech_and_stops_deepgram_fallback() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"text": "   "}))
    ) as http_client:
        el = ElevenLabsSttProvider(api_key="el_key", http_client=http_client)
        dg_mock = AsyncMock(return_value=SttProviderDetails(text="Không được gọi"))

        class _SpyDeepgram:
            name = "deepgram"

            async def transcribe_verbose(self, audio: bytes, mime_type: str) -> SttProviderDetails:
                return await dg_mock(audio, mime_type)

            async def transcribe(self, audio: bytes, mime_type: str) -> str:
                return (await self.transcribe_verbose(audio, mime_type)).text

        svc = VoiceService(
            FallbackSttProvider(primary=el, fallback=_SpyDeepgram()),
            timeout_seconds=5.0,
            max_audio_bytes=1024 * 1024,
        )
        with pytest.raises(SttError) as exc_info:
            await svc.transcribe(b"aac-silence", content_type="audio/aac")

    assert exc_info.value.code == SttErrorCode.NO_SPEECH
    dg_mock.assert_not_awaited()


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, SttErrorCode.PROVIDER_UNAVAILABLE),
        (403, SttErrorCode.PROVIDER_UNAVAILABLE),
        (429, SttErrorCode.RATE_LIMITED),
        (413, SttErrorCode.AUDIO_TOO_LARGE),
        (400, SttErrorCode.UNSUPPORTED_AUDIO),
        (415, SttErrorCode.UNSUPPORTED_AUDIO),
        (422, SttErrorCode.UNSUPPORTED_AUDIO),
        (500, SttErrorCode.PROVIDER_ERROR),
        (503, SttErrorCode.PROVIDER_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_elevenlabs_http_error_mapping(
    status_code: int, expected_code: SttErrorCode
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: httpx.Response(status_code, text="err"))
    ) as http_client:
        el = ElevenLabsSttProvider(api_key="el_key_secret", http_client=http_client)
        with pytest.raises(SttError) as exc_info:
            await el.transcribe(b"bytes", "audio/aac")
        assert exc_info.value.code == expected_code
        assert "el_key_secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_elevenlabs_network_exceptions_mapping() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=req))
        )
    ) as timeout_client:
        el_to = ElevenLabsSttProvider(api_key="el_key", http_client=timeout_client)
        with pytest.raises(SttError) as exc_to:
            await el_to.transcribe(b"bytes", "audio/aac")
        assert exc_to.value.code == SttErrorCode.TIMEOUT

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(httpx.ConnectError("conn", request=req))
        )
    ) as net_client:
        el_net = ElevenLabsSttProvider(api_key="el_key", http_client=net_client)
        with pytest.raises(SttError) as exc_net:
            await el_net.transcribe(b"bytes", "audio/aac")
        assert exc_net.value.code == SttErrorCode.PROVIDER_ERROR


@pytest.mark.asyncio
async def test_elevenlabs_missing_api_key_raises_provider_unavailable() -> None:
    el = ElevenLabsSttProvider(api_key="")
    with pytest.raises(SttError) as exc_info:
        await el.transcribe(b"bytes", "audio/aac")
    assert exc_info.value.code == SttErrorCode.PROVIDER_UNAVAILABLE
    assert exc_info.value.detail == "missing_elevenlabs_api_key"


@pytest.mark.asyncio
async def test_auto_all_missing_keys_raises_provider_unavailable() -> None:
    cfg = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="auto",
        GEMINI_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
        ELEVENLABS_API_KEY="",
        DEEPGRAM_API_KEY="",
    )
    svc = build_voice_service(cfg)
    with pytest.raises(SttError) as exc_info:
        await svc.transcribe(b"RIFF-audio", content_type="audio/wav", filename="a.wav")
    assert exc_info.value.code == SttErrorCode.PROVIDER_UNAVAILABLE


@pytest.mark.asyncio
async def test_gemini_explicit_mode_3tier_end_to_end_and_legacy_fallback_preserves_low_confidence() -> None:
    # 1. VOICE_STT_PROVIDER="gemini" chạy chuỗi phẳng gemini(429) -> elevenlabs(503) -> deepgram(200, conf=0.4)
    cfg_gem = Settings(
        _env_file=None,
        VOICE_STT_PROVIDER="gemini",
        GEMINI_API_KEY="gem-key",
        ELEVENLABS_API_KEY="el-key",
        DEEPGRAM_API_KEY="dg-key",
    )
    svc_gem = build_voice_service(cfg_gem)
    assert isinstance(svc_gem._provider, FallbackSttProvider)
    assert svc_gem._provider._tertiary is not None

    el_transport = httpx.MockTransport(lambda _req: httpx.Response(503, text="el down"))
    dg_transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            json={
                "results": {
                    "channels": [
                        {"alternatives": [{"transcript": "Làm bài tập lúc 8 giờ", "confidence": 0.4}]}
                    ]
                }
            },
        )
    )
    async with (
        httpx.AsyncClient(transport=el_transport) as el_client,
        httpx.AsyncClient(transport=dg_transport) as dg_client,
    ):
        svc_gem._provider._fallback._http_client = el_client  # type: ignore[attr-defined]
        svc_gem._provider._tertiary._http_client = dg_client  # type: ignore[attr-defined]
        with patch.object(
            svc_gem._provider._primary,
            "transcribe",
            AsyncMock(side_effect=SttError(SttErrorCode.RATE_LIMITED, "http_429")),
        ):
            res = await svc_gem.transcribe(b"RIFF-audio", content_type="audio/wav", filename="g.wav")

    assert res.provider == "deepgram"
    assert res.text == "Làm bài tập lúc 8 giờ"
    assert res.low_confidence is True

    # 2. Legacy VoiceService(primary, fallback_provider=FallbackSttProvider(el, dg)) bảo toàn provider & low_confidence
    class _FailPrimary:
        name = "gemini"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            _ = (audio, mime_type)
            raise SttError(SttErrorCode.RATE_LIMITED, "http_429")

    async with (
        httpx.AsyncClient(transport=el_transport) as el_client,
        httpx.AsyncClient(transport=dg_transport) as dg_client,
    ):
        el_provider = ElevenLabsSttProvider(api_key="el-key", http_client=el_client)
        dg_provider = DeepgramSttProvider(api_key="dg-key", http_client=dg_client)
        legacy_svc = VoiceService(
            _FailPrimary(),
            fallback_provider=FallbackSttProvider(primary=el_provider, fallback=dg_provider),
            timeout_seconds=5.0,
            max_audio_bytes=1024 * 1024,
        )
        legacy_res = await legacy_svc.transcribe(
            b"RIFF-audio", content_type="audio/wav", filename="legacy.wav"
        )

    assert legacy_res.provider == "deepgram"
    assert legacy_res.text == "Làm bài tập lúc 8 giờ"
    assert legacy_res.low_confidence is True


@pytest.mark.asyncio
async def test_uf5_checkin_routing_3tier_failover_no_speech_and_separate_timeout() -> None:
    # 1. uf5_checkin failover: elevenlabs (503) -> gemini (429) -> deepgram (200)
    calls: list[str] = []

    class _MockLeaf:
        def __init__(
            self,
            name: str,
            *,
            text: str | None = None,
            error: Exception | None = None,
            delay: float = 0.0,
        ) -> None:
            self.name = name
            self._text = text
            self._error = error
            self._delay = delay

        async def transcribe_verbose(
            self, audio: bytes, mime_type: str, **kwargs: Any
        ) -> SttProviderDetails:
            _ = (audio, mime_type, kwargs)
            calls.append(self.name)
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            if self._error is not None:
                raise self._error
            return SttProviderDetails(text=str(self._text or ""), low_confidence=False)

        async def transcribe(self, audio: bytes, mime_type: str, **kwargs: Any) -> str:
            return (await self.transcribe_verbose(audio, mime_type, **kwargs)).text

    chain = FallbackSttProvider(
        primary=_MockLeaf("gemini", error=SttError(SttErrorCode.RATE_LIMITED, "http_429")),
        fallback=_MockLeaf("elevenlabs", error=SttError(SttErrorCode.PROVIDER_ERROR, "http_503")),
        tertiary=_MockLeaf("deepgram", text="Xong rồi"),
        prefer_fallback_purposes=frozenset({"uf5_checkin"}),
        prefer_fallback_primary_timeout_seconds=0.05,
        primary_timeout_seconds=0.5,
        total_timeout_seconds=1.0,
    )
    svc = VoiceService(chain, timeout_seconds=1.0, max_audio_bytes=1024 * 1024)
    res = await svc.transcribe(
        b"RIFF-audio", content_type="audio/wav", filename="uf5.wav", purpose="uf5_checkin"
    )
    assert calls == ["elevenlabs", "gemini", "deepgram"]
    assert res.provider == "deepgram"
    assert res.text == "Xong rồi"

    # 2. uf5_checkin NO_SPEECH từ ElevenLabs dừng ngay, không gọi Gemini hay Deepgram
    calls_ns: list[str] = []

    class _NoSpeechEl(_MockLeaf):
        async def transcribe_verbose(
            self, audio: bytes, mime_type: str, **kwargs: Any
        ) -> SttProviderDetails:
            calls_ns.append(self.name)
            raise SttError(SttErrorCode.NO_SPEECH, "empty_transcript")

    class _SpyLeaf(_MockLeaf):
        async def transcribe_verbose(
            self, audio: bytes, mime_type: str, **kwargs: Any
        ) -> SttProviderDetails:
            calls_ns.append(self.name)
            return SttProviderDetails(text="Không được gọi")

    svc_ns = VoiceService(
        FallbackSttProvider(
            primary=_SpyLeaf("gemini"),
            fallback=_NoSpeechEl("elevenlabs"),
            tertiary=_SpyLeaf("deepgram"),
            prefer_fallback_purposes=frozenset({"uf5_checkin"}),
        ),
        timeout_seconds=2.0,
        max_audio_bytes=1024 * 1024,
    )
    with pytest.raises(SttError) as ns_err:
        await svc_ns.transcribe(
            b"RIFF-audio", content_type="audio/wav", filename="ns.wav", purpose="uf5_checkin"
        )
    assert ns_err.value.code == SttErrorCode.NO_SPEECH
    assert calls_ns == ["elevenlabs"]

    # 3. uf5_checkin cắt ElevenLabs ở prefer_fallback_primary_timeout_seconds=0.05s thay vì 0.5s
    slow_el_calls: list[str] = []

    class _SlowEl(_MockLeaf):
        async def transcribe_verbose(
            self, audio: bytes, mime_type: str, **kwargs: Any
        ) -> SttProviderDetails:
            slow_el_calls.append(self.name)
            await asyncio.sleep(0.35)
            return SttProviderDetails(text="Quá chậm")

    class _FastGem(_MockLeaf):
        async def transcribe_verbose(
            self, audio: bytes, mime_type: str, **kwargs: Any
        ) -> SttProviderDetails:
            slow_el_calls.append(self.name)
            return SttProviderDetails(text="Gemini cứu kịp")

    svc_timeout_split = VoiceService(
        FallbackSttProvider(
            primary=_FastGem("gemini"),
            fallback=_SlowEl("elevenlabs"),
            prefer_fallback_purposes=frozenset({"uf5_checkin"}),
            prefer_fallback_primary_timeout_seconds=0.05,
            primary_timeout_seconds=0.5,
            total_timeout_seconds=0.4,
        ),
        timeout_seconds=0.4,
        max_audio_bytes=1024 * 1024,
    )
    t0 = time.perf_counter()
    res_split = await svc_timeout_split.transcribe(
        b"RIFF-audio", content_type="audio/wav", filename="split.wav", purpose="uf5_checkin"
    )
    elapsed = time.perf_counter() - t0
    assert slow_el_calls == ["elevenlabs", "gemini"]
    assert res_split.provider == "gemini"
    assert res_split.text == "Gemini cứu kịp"
    assert elapsed < 0.25


@pytest.mark.parametrize(
    ("raw_response", "expected_detail"),
    [
        (httpx.Response(200, content=b"{not-json", headers={"content-type": "application/json"}), "invalid_json"),
        (httpx.Response(200, json=["a"]), "invalid_response"),
        (httpx.Response(200, json={}), "invalid_response"),
        (httpx.Response(200, json={"text": None}), "invalid_response"),
        (httpx.Response(200, json={"text": 123}), "invalid_response"),
    ],
)
@pytest.mark.asyncio
async def test_elevenlabs_http_200_invalid_payloads_trigger_deepgram_fallback(
    raw_response: httpx.Response, expected_detail: str
) -> None:
    el_transport = httpx.MockTransport(lambda _req: raw_response)
    dg_transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            json={
                "results": {
                    "channels": [{"alternatives": [{"transcript": "Fallback Deepgram OK", "confidence": 0.9}]}]
                }
            },
        )
    )
    async with (
        httpx.AsyncClient(transport=el_transport) as el_client,
        httpx.AsyncClient(transport=dg_transport) as dg_client,
    ):
        el = ElevenLabsSttProvider(api_key="el_key", http_client=el_client)
        with pytest.raises(SttError) as err_ctx:
            await el.transcribe(b"RIFF-audio", "audio/wav")
        assert err_ctx.value.code == SttErrorCode.PROVIDER_ERROR
        assert err_ctx.value.detail == expected_detail

        dg = DeepgramSttProvider(api_key="dg_key", http_client=dg_client)
        svc = VoiceService(
            FallbackSttProvider(primary=el, fallback=dg),
            timeout_seconds=5.0,
            max_audio_bytes=1024 * 1024,
        )
        res = await svc.transcribe(b"RIFF-audio", content_type="audio/wav", filename="bad.wav")
    assert res.provider == "deepgram"
    assert res.text == "Fallback Deepgram OK"


@pytest.mark.asyncio
async def test_elevenlabs_optional_flags_iso_language_and_413_mapping() -> None:
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"text": "OK"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http_client:
        el = ElevenLabsSttProvider(
            api_key="el_key",
            tag_audio_events=True,
            diarize=True,
            http_client=http_client,
        )
        text = await el.transcribe(b"RIFF-audio", "audio/wav")
        assert text == "OK"
        assert not http_client.is_closed

    assert len(captured) == 1
    assert b'"tag_audio_events"\r\n\r\ntrue' in captured[0].content
    assert b'"diarize"\r\n\r\ntrue' in captured[0].content
    assert _iso_language_code("-VN") == "vi"

    class _Http413Err(Exception):
        status_code = 413

    mapped_413 = map_provider_exception(_Http413Err())
    assert mapped_413.code == SttErrorCode.AUDIO_TOO_LARGE
    assert mapped_413.detail == "http_413"


@pytest.mark.asyncio
async def test_elevenlabs_internal_http_client_closes_on_success_and_error() -> None:
    created_clients: list[httpx.AsyncClient] = []
    real_async_client = httpx.AsyncClient

    def _make_client_factory(handler: Any):
        def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            client = real_async_client(*args, **kwargs)
            created_clients.append(client)
            return client

        return _factory

    el = ElevenLabsSttProvider(api_key="el_key", http_client=None)

    with patch(
        "app.services.voice_service.httpx.AsyncClient",
        side_effect=_make_client_factory(lambda _req: httpx.Response(200, json={"text": "Đóng client tốt"})),
    ):
        assert await el.transcribe(b"RIFF-audio", "audio/wav") == "Đóng client tốt"
    assert len(created_clients) == 1
    assert created_clients[0].is_closed is True

    with patch(
        "app.services.voice_service.httpx.AsyncClient",
        side_effect=_make_client_factory(lambda _req: httpx.Response(500, text="server err")),
    ):
        with pytest.raises(SttError) as err_ctx:
            await el.transcribe(b"RIFF-audio", "audio/wav")
        assert err_ctx.value.code == SttErrorCode.PROVIDER_ERROR
    assert len(created_clients) == 2
    assert created_clients[1].is_closed is True


@pytest.mark.asyncio
async def test_early_raw_timeout_error_from_primary_fails_over_when_primary_timeout_is_none() -> None:
    """Raw TimeoutError từ chính provider ở 0.02s vẫn phải failover (không nhầm với hết total_timeout)."""

    class _EarlyTimeoutErrorPrimary:
        name = "gemini"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            _ = (audio, mime_type)
            await asyncio.sleep(0.02)
            raise TimeoutError("sdk_timeout")

    class _OkFallback:
        name = "elevenlabs"

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            _ = (audio, mime_type)
            return "Fallback cứu kịp"

    # 1. FallbackSttProvider(primary_timeout_seconds=None, total_timeout_seconds=1.0)
    svc_chain = VoiceService(
        FallbackSttProvider(
            primary=_EarlyTimeoutErrorPrimary(),
            fallback=_OkFallback(),
            primary_timeout_seconds=None,
            total_timeout_seconds=1.0,
        ),
        timeout_seconds=1.0,
        max_audio_bytes=1024 * 1024,
    )
    res_chain = await svc_chain.transcribe(
        b"RIFF-audio", content_type="audio/wav", filename="early_to_1.wav"
    )
    assert res_chain.provider == "elevenlabs"
    assert res_chain.text == "Fallback cứu kịp"

    # 2. Legacy VoiceService(..., fallback_provider=..., primary_timeout_seconds=None, timeout_seconds=1.0)
    svc_legacy = VoiceService(
        _EarlyTimeoutErrorPrimary(),
        fallback_provider=_OkFallback(),
        primary_timeout_seconds=None,
        timeout_seconds=1.0,
        max_audio_bytes=1024 * 1024,
    )
    res_legacy = await svc_legacy.transcribe(
        b"RIFF-audio", content_type="audio/wav", filename="early_to_2.wav"
    )
    assert res_legacy.provider == "elevenlabs"
    assert res_legacy.text == "Fallback cứu kịp"
