"""Tests for Phases MW-1 -> MW-3 (5-layer Pure ASGI Middleware Stack) and GAP-06."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.deps import get_db
from app.api.v1.auth import get_google_verifier
from app.core.config import settings
from app.core.database import make_engine
from app.core.errors import AppError, register_exception_handlers
from app.core.metrics import (
    PROMETHEUS_CONTENT_TYPE,
    _escape_label,
    _read_pool_metric,
    collect_prometheus_metrics,
    get_process_rss_bytes,
)
from app.core.middleware import (
    LATENCY_BUCKETS,
    BodySizeLimitMiddleware,
    OAuthLogRedactionFilter,
    ObservabilityMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    clear_metric_hooks,
    get_current_request_id,
    get_http_observations_snapshot,
    install_access_log_redaction_filter,
    redact_query_string,
    register_metric_hook,
    reset_http_observations,
    sanitize_or_generate_request_id,
)
from app.core.security import issue_app_access_token
from app.main import app
from app.models import Base, CalendarConnection, CalendarSyncJob, User
from app.schemas.auth import VerifiedGoogleIdentity
from app.schemas.common import build_response_meta
from app.services import plan_service
from app.services.ai_service import ai_service
from app.services.calendar_service import CalendarService


@pytest.fixture(autouse=True)
def _clean_observability_state():
    reset_http_observations()
    clear_metric_hooks()
    try:
        yield
    finally:
        reset_http_observations()
        clear_metric_hooks()


def test_middlewares_are_pure_asgi_not_base_http_middleware():
    for cls in (
        RequestIdMiddleware,
        SecurityHeadersMiddleware,
        BodySizeLimitMiddleware,
        ObservabilityMiddleware,
    ):
        assert not issubclass(cls, BaseHTTPMiddleware)


@pytest.mark.asyncio
async def test_non_http_scopes_pass_through_all_middlewares():
    seen_scopes: list[str] = []

    async def dummy_asgi(scope, receive, send):
        seen_scopes.append(scope["type"])

    wrapped = SecurityHeadersMiddleware(
        RequestIdMiddleware(ObservabilityMiddleware(BodySizeLimitMiddleware(dummy_asgi)))
    )
    await wrapped({"type": "websocket", "path": "/ws"}, None, None)  # type: ignore[arg-type]
    await wrapped({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen_scopes == ["websocket", "lifespan"]


def test_sanitize_or_generate_request_id_and_contextvar():
    assert get_current_request_id() is None
    assert sanitize_or_generate_request_id("trace-123.abc_XYZ") == "trace-123.abc_XYZ"
    assert sanitize_or_generate_request_id("a" * 64) == "a" * 64

    for invalid in (None, "", "a" * 65, "bad id", "bad\nid", "bad\r\nid", "<script>"):
        generated = sanitize_or_generate_request_id(invalid)
        assert generated.startswith("req-")
        assert len(generated) == 12


@pytest.mark.asyncio
async def test_mw1_request_id_security_headers_cors_and_error_envelopes(auth_config):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with sf() as session:
            yield session

    uid = uuid4()
    async with sf() as db:
        db.add(User(id=uid, email="mw1@example.com", display_name="MW1", is_active=True))
        await db.commit()

    app.dependency_overrides[get_db] = override_get_db
    try:
        headers = {
            "Authorization": f"Bearer {issue_app_access_token(uid).access_token}",
            "X-Request-ID": "mobile-trace_01.ok",
            "Origin": "http://localhost:8080",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Non-auth route: RequestId, SecurityHeaders, CORS expose_headers, no cache-control: no-store
            cap_resp = await client.get("/api/v1/capabilities", headers=headers)
            assert cap_resp.status_code == 200
            assert cap_resp.headers["x-request-id"] == "mobile-trace_01.ok"
            assert cap_resp.headers["x-content-type-options"] == "nosniff"
            assert cap_resp.headers["x-frame-options"] == "DENY"
            assert cap_resp.headers["referrer-policy"] == "no-referrer"
            assert cap_resp.headers.get("cache-control") != "no-store"
            assert "x-request-id" in cap_resp.headers.get("access-control-expose-headers", "").lower()
            assert cap_resp.json()["meta"]["request_id"] == "mobile-trace_01.ok"

            # 2. Auth route gets cache-control: no-store
            me_resp = await client.get("/api/v1/auth/me", headers=headers)
            assert me_resp.status_code == 200
            assert me_resp.headers["cache-control"] == "no-store"
            assert me_resp.headers["x-request-id"] == "mobile-trace_01.ok"
            assert me_resp.json()["meta"]["request_id"] == "mobile-trace_01.ok"

            # 3. Calendar OAuth route gets cache-control: no-store even on error
            oauth_resp = await client.get(
                "/api/v1/calendar/oauth/callback?code=sec_code&state=bad_state",
                headers={"X-Request-ID": "oauth-req-1"},
            )
            assert oauth_resp.status_code == 401
            assert oauth_resp.headers["cache-control"] == "no-store"
            assert oauth_resp.headers["x-request-id"] == "oauth-req-1"
            assert oauth_resp.json()["meta"]["request_id"] == "oauth-req-1"

            # 4. RequestValidationError (422) propagates request_id
            val_resp = await client.post(
                "/api/v1/auth/google",
                json={},
                headers={"X-Request-ID": "val-err-99"},
            )
            assert val_resp.status_code == 422
            assert val_resp.headers["x-request-id"] == "val-err-99"
            assert val_resp.json()["meta"]["request_id"] == "val-err-99"

            # 5. Invalid X-Request-ID is sanitized/replaced consistently across header and meta
            bad_id_resp = await client.get(
                "/api/v1/capabilities",
                headers={
                    "Authorization": headers["Authorization"],
                    "X-Request-ID": "bad header value!",
                },
            )
            assert bad_id_resp.status_code == 200
            gen_id = bad_id_resp.headers["x-request-id"]
            assert gen_id.startswith("req-")
            assert bad_id_resp.json()["meta"]["request_id"] == gen_id

        # ContextVar must be reset after requests finish
        assert get_current_request_id() is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_gap06_capabilities_reflects_voice_max_audio_bytes_dynamically(auth_config, monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with sf() as session:
            yield session

    uid = uuid4()
    async with sf() as db:
        db.add(User(id=uid, email="gap06@example.com", display_name="G6", is_active=True))
        await db.commit()

    app.dependency_overrides[get_db] = override_get_db
    try:
        monkeypatch.setattr(settings, "VOICE_MAX_AUDIO_BYTES", 4_321_000)
        headers = {"Authorization": f"Bearer {issue_app_access_token(uid).access_token}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/capabilities", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["configured_parameters"]["audio_max_bytes"] == 4_321_000
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_mw1_unexpected_exception_handler_and_dedupe_headers(caplog):
    test_app = FastAPI()
    test_app.add_middleware(BodySizeLimitMiddleware)
    test_app.add_middleware(ObservabilityMiddleware)
    test_app.add_middleware(RequestIdMiddleware)
    test_app.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(test_app)

    @test_app.get("/boom")
    async def boom():
        raise RuntimeError("unexpected boom")

    @test_app.get("/custom-headers")
    async def custom_headers(request: Request):
        from fastapi.responses import JSONResponse

        assert get_current_request_id() == "req-custom-1"
        return JSONResponse(
            content={"meta": build_response_meta().model_dump(mode="json")},
            headers={
                "X-Request-ID": "should-be-replaced",
                "Cache-Control": "public, max-age=60",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    @test_app.get("/api/v1/auth/custom")
    async def auth_custom():
        from fastapi.responses import JSONResponse

        return JSONResponse(content={"ok": True}, headers={"Cache-Control": "private"})

    with caplog.at_level(logging.ERROR):
        async with AsyncClient(
            transport=ASGITransport(app=test_app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp_500 = await client.get("/boom", headers={"X-Request-ID": "err-500-id"})
            assert resp_500.status_code == 500
            body_500 = resp_500.json()
            assert body_500["error"]["code"] == "INTERNAL_ERROR"
            assert body_500["error"]["retryable"] is True
            assert body_500["error"]["next_action"] == "retry_later"
            assert body_500["meta"]["request_id"] == "err-500-id"
            assert resp_500.headers["x-request-id"] == "err-500-id"

            resp_custom = await client.get("/custom-headers", headers={"X-Request-ID": "req-custom-1"})
            assert resp_custom.status_code == 200
            assert resp_custom.headers.get_list("x-request-id") == ["req-custom-1"]
            assert resp_custom.headers["x-frame-options"] == "SAMEORIGIN"
            assert resp_custom.headers["cache-control"] == "public, max-age=60"
            assert resp_custom.json()["meta"]["request_id"] == "req-custom-1"

            resp_auth_custom = await client.get("/api/v1/auth/custom")
            assert resp_auth_custom.headers["cache-control"] == "private"

    assert any("Unhandled exception [request_id=err-500-id]" in rec.message for rec in caplog.records)
    assert get_current_request_id() is None
    snap = get_http_observations_snapshot()
    assert snap[("GET", "/boom", 500)]["count"] == 1


class _EchoPayload(BaseModel):
    text: str


@pytest.mark.asyncio
async def test_mw2_body_size_limit_content_length_and_chunked_streaming(monkeypatch):
    monkeypatch.setattr(settings, "MAX_JSON_BODY_BYTES", 128)
    monkeypatch.setattr(settings, "VOICE_MAX_AUDIO_BYTES", 200)
    monkeypatch.setattr(settings, "VOICE_MULTIPART_OVERHEAD_BYTES", 100)

    test_app = FastAPI()
    test_app.add_middleware(BodySizeLimitMiddleware)
    test_app.add_middleware(ObservabilityMiddleware)
    test_app.add_middleware(RequestIdMiddleware)
    test_app.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(test_app)

    @test_app.post("/api/v1/echo")
    async def echo(payload: _EchoPayload):
        return {"len": len(payload.text)}

    @test_app.post("/api/v1/voice/transcriptions")
    async def upload_voice(request: Request):
        body = await request.body()
        return {"received": len(body)}

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        # 1. Content-Length over JSON limit -> 413 ErrorEnvelope
        oversized_json = b'{"text":"' + (b"x" * 200) + b'"}'
        r1 = await client.post(
            "/api/v1/echo",
            content=oversized_json,
            headers={"Content-Type": "application/json", "X-Request-ID": "too-big-1"},
        )
        assert r1.status_code == 413
        assert r1.headers["x-request-id"] == "too-big-1"
        assert r1.headers["x-content-type-options"] == "nosniff"
        j1 = r1.json()
        assert j1["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert j1["error"]["retryable"] is False
        assert j1["error"]["next_action"] == "reduce_payload_size"
        assert j1["meta"]["request_id"] == "too-big-1"

        # 2. Exact JSON limit (128 bytes) -> allowed
        exact_json = b'{"text":"' + (b"a" * (128 - len(b'{"text":""}'))) + b'"}'
        assert len(exact_json) == 128
        r2 = await client.post(
            "/api/v1/echo",
            content=exact_json,
            headers={"Content-Type": "application/json"},
        )
        assert r2.status_code == 200

        # 3. Chunked stream (no Content-Length) exceeding JSON limit -> 413 (not 400!)
        async def chunk_stream(total: int) -> AsyncIterator[bytes]:
            yield b'{"text":"'
            remaining = total - len(b'{"text":""}')
            while remaining > 0:
                chunk_len = min(50, remaining)
                yield b"y" * chunk_len
                remaining -= chunk_len
            yield b'"}'

        r3 = await client.post(
            "/api/v1/echo",
            content=chunk_stream(160),
            headers={"Content-Type": "application/json", "X-Request-ID": "chunk-big-1"},
        )
        assert r3.status_code == 413
        assert r3.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert r3.json()["meta"]["request_id"] == "chunk-big-1"

        # 3b. Chunked stream exactly at limit (128 bytes) -> 200 OK
        r3_ok = await client.post(
            "/api/v1/echo",
            content=chunk_stream(128),
            headers={"Content-Type": "application/json"},
        )
        assert r3_ok.status_code == 200
        assert r3_ok.json() == {"len": 128 - len(b'{"text":""}')}

        # 4. Voice transcriptions endpoint allows VOICE_MAX_AUDIO_BYTES + VOICE_MULTIPART_OVERHEAD_BYTES (300)
        r4_ok = await client.post(
            "/api/v1/voice/transcriptions",
            content=b"a" * 300,
        )
        assert r4_ok.status_code == 200
        assert r4_ok.json() == {"received": 300}

        r4_big = await client.post(
            "/api/v1/voice/transcriptions",
            content=b"a" * 301,
        )
        assert r4_big.status_code == 413
        assert r4_big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

        # Trailing slash variant is also matched by BodySizeLimitMiddleware
        r4_slash_big = await client.post(
            "/api/v1/voice/transcriptions/",
            content=b"a" * 301,
        )
        assert r4_slash_big.status_code == 413
        assert r4_slash_big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

        # 5. Spoofed small Content-Length with actual body > max_bytes on streaming chunks -> 413
        async def spoofed_asgi(scope, receive, send):
            while True:
                msg = await receive()
                if msg["type"] == "http.request" and not msg.get("more_body", False):
                    break

        mw = BodySizeLimitMiddleware(spoofed_asgi)
        sent: list[dict] = []
        chunks = iter(
            [
                {"type": "http.request", "body": b"x" * 80, "more_body": True},
                {"type": "http.request", "body": b"y" * 80, "more_body": False},
            ]
        )

        async def fake_receive():
            return next(chunks)

        async def fake_send(msg):
            sent.append(msg)

        await mw(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/echo",
                "headers": [(b"content-length", b"10")],
                "state": {"request_id": "spoof-1"},
            },
            fake_receive,
            fake_send,
        )
        assert sent[0]["status"] == 413
        assert b"PAYLOAD_TOO_LARGE" in sent[1]["body"]
        assert b"spoof-1" in sent[1]["body"]


def test_mw3_redact_query_string_and_oauth_log_filter():
    raw = (
        "code=4/0SecretAuthCode&STATE=csrf_secret&token=tok1&access_token=at1"
        "&refresh_token=rt1&id_token=id1&client_secret=cs1&password=pw1&key=k1&scope=email&empty="
    )
    redacted = redact_query_string(raw)
    for secret in ("4/0SecretAuthCode", "csrf_secret", "tok1", "at1", "rt1", "id1", "cs1", "pw1", "k1"):
        assert secret not in redacted
    assert "code=<REDACTED>" in redacted
    assert "STATE=<REDACTED>" in redacted
    assert "scope=email" in redacted
    assert redact_query_string(b"code=bytes_secret&ok=yes") == "code=<REDACTED>&ok=yes"
    assert redact_query_string(b"") == ""

    log_filter = OAuthLogRedactionFilter()
    rec1 = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:5000",
            "GET",
            "/api/v1/calendar/oauth/callback?code=secret_code&state=secret_state&status_code=ok",
            "1.1",
            302,
        ),
        exc_info=None,
    )
    assert log_filter.filter(rec1) is True
    formatted1 = rec1.getMessage()
    assert "secret_code" not in formatted1
    assert "secret_state" not in formatted1
    assert "code=<REDACTED>" in formatted1
    assert "state=<REDACTED>" in formatted1
    assert "status_code=ok" in formatted1
    assert "302" in formatted1

    rec2 = logging.LogRecord(
        name="app.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_id=r1 method=GET path=/cb code=raw_secret status_code=200 idempotency_key=idem1",
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(rec2) is True
    formatted2 = rec2.getMessage()
    assert "raw_secret" not in formatted2
    assert "code=<REDACTED>" in formatted2
    assert "status_code=200" in formatted2
    assert "idempotency_key=idem1" in formatted2

    # Idempotent filter installation on uvicorn.access and app.access
    install_access_log_redaction_filter()
    install_access_log_redaction_filter()
    for name in ("uvicorn.access", "app.access"):
        filters = [f for f in logging.getLogger(name).filters if isinstance(f, OAuthLogRedactionFilter)]
        assert len(filters) == 1


@pytest.mark.asyncio
async def test_mw3_observability_route_template_hooks_and_access_log(caplog):
    recorded_hooks: list[tuple[str, str, int, float]] = []

    def good_hook(method: str, route: str, status: int, duration: float) -> None:
        recorded_hooks.append((method, route, status, duration))

    def bad_hook(method: str, route: str, status: int, duration: float) -> None:
        raise ValueError("hook failure should not break response")

    register_metric_hook(good_hook)
    register_metric_hook(bad_hook)

    test_app = FastAPI()
    test_app.add_middleware(BodySizeLimitMiddleware)
    test_app.add_middleware(ObservabilityMiddleware)
    test_app.add_middleware(RequestIdMiddleware)
    test_app.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(test_app)

    router = APIRouter(prefix="/api/v1")

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str):
        return {"task_id": task_id}

    @router.get("/calendar/oauth/callback")
    async def oauth_cb(code: str, state: str):
        raise AppError(
            status_code=400,
            code="OAUTH_ERR",
            message="Err",
            next_action="retry",
        )

    test_app.include_router(router)
    install_access_log_redaction_filter()

    with caplog.at_level(logging.INFO, logger="app.access"):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            r1 = await client.get("/api/v1/tasks/1111", headers={"X-Request-ID": "obs-1"})
            r2 = await client.get("/api/v1/tasks/2222", headers={"X-Request-ID": "obs-2"})
            r3 = await client.get(
                "/api/v1/calendar/oauth/callback?code=super_secret&state=csrf_val&foo=bar",
                headers={"X-Request-ID": "obs-3"},
            )
            r4 = await client.get("/does-not-exist", headers={"X-Request-ID": "obs-4"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 400
    assert r4.status_code == 404

    snap = get_http_observations_snapshot()
    assert snap[("GET", "/api/v1/tasks/{task_id}", 200)]["count"] == 2
    assert snap[("GET", "/api/v1/calendar/oauth/callback", 400)]["count"] == 1
    assert snap[("GET", "unmatched", 404)]["count"] == 1
    assert len(recorded_hooks) == 4

    access_logs = [rec.getMessage() for rec in caplog.records if rec.name == "app.access"]
    assert any(
        "request_id=obs-1" in line
        and "method=GET" in line
        and "route_template=/api/v1/tasks/{task_id}" in line
        and "status_code=200" in line
        and "duration_ms=" in line
        for line in access_logs
    )
    oauth_log = next(line for line in access_logs if "request_id=obs-3" in line)
    assert "super_secret" not in oauth_log
    assert "csrf_val" not in oauth_log
    assert "code=<REDACTED>" in oauth_log
    assert "state=<REDACTED>" in oauth_log
    assert "foo=bar" in oauth_log


@asynccontextmanager
async def _real_app_sqlite_env(extra_overrides: dict | None = None):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with sf() as session:
            yield session

    prev_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    if extra_overrides:
        app.dependency_overrides.update(extra_overrides)
    try:
        yield sf
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev_overrides)
        await engine.dispose()


def _build_voice_multipart(audio_bytes: bytes, boundary: str = "----qa-voice-boundary") -> bytes:
    return (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="purpose"\r\n\r\nuf1_input\r\n'
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="device_id"\r\n\r\nqa-device\r\n'
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="clip.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()


def _assert_security_and_cors_headers(resp, *, expect_no_store: bool) -> None:
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers.get("access-control-allow-origin") in {"*", "http://localhost:8080"}
    assert "x-request-id" in resp.headers.get("access-control-expose-headers", "").lower()
    if expect_no_store:
        assert resp.headers.get("cache-control") == "no-store"
    else:
        assert "cache-control" not in resp.headers


@pytest.mark.asyncio
async def test_mw_lifo_413_early_rejection_preserves_headers_cors_and_observations():
    oversized = b"x" * (settings.MAX_JSON_BODY_BYTES + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_auth = await client.post(
            "/api/v1/auth/google",
            content=oversized,
            headers={
                "Content-Type": "application/json",
                "Origin": "http://localhost:8080",
                "X-Request-ID": "lifo-413-auth",
            },
        )
        assert r_auth.status_code == 413
        assert r_auth.headers["x-request-id"] == "lifo-413-auth"
        assert r_auth.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert r_auth.json()["meta"]["request_id"] == "lifo-413-auth"
        _assert_security_and_cors_headers(r_auth, expect_no_store=True)

        r_non_auth = await client.post(
            "/api/v1/tasks",
            content=oversized,
            headers={
                "Content-Type": "application/json",
                "Origin": "http://localhost:8080",
                "X-Request-ID": "lifo-413-nonauth",
            },
        )
        assert r_non_auth.status_code == 413
        assert r_non_auth.headers["x-request-id"] == "lifo-413-nonauth"
        assert r_non_auth.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert r_non_auth.json()["meta"]["request_id"] == "lifo-413-nonauth"
        _assert_security_and_cors_headers(r_non_auth, expect_no_store=False)

    snap = get_http_observations_snapshot()
    assert snap[("POST", "unmatched", 413)]["count"] == 2
    assert get_current_request_id() is None


@pytest.mark.asyncio
async def test_mw2_real_app_json_1mib_exact_boundary_chunked_spoofed_and_invalid_cl(
    auth_config, monkeypatch
):
    async def fake_verify(token: str) -> VerifiedGoogleIdentity:
        return VerifiedGoogleIdentity(sub="sub-1mib", email="1mib@example.com", display_name="1MiB")

    async with _real_app_sqlite_env({get_google_verifier: lambda: fake_verify}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Exact 1 MiB (1,048,576 bytes) passes BodySizeLimitMiddleware
            base_json = b'{"id_token":"ok"}'
            exact_1mib = base_json + (b" " * (1024 * 1024 - len(base_json)))
            assert len(exact_1mib) == 1024 * 1024
            r_exact = await client.post(
                "/api/v1/auth/google",
                content=exact_1mib,
                headers={"Content-Type": "application/json", "X-Request-ID": "exact-1mib"},
            )
            assert r_exact.status_code == 200
            assert r_exact.headers["x-request-id"] == "exact-1mib"
            assert r_exact.json()["meta"]["request_id"] == "exact-1mib"

            # 2. 1 MiB + 1 (1,048,577 bytes) -> 413 PAYLOAD_TOO_LARGE
            over_1mib = exact_1mib + b" "
            assert len(over_1mib) == (1024 * 1024) + 1
            r_over = await client.post(
                "/api/v1/auth/google",
                content=over_1mib,
                headers={"Content-Type": "application/json", "X-Request-ID": "over-1mib"},
            )
            assert r_over.status_code == 413
            assert r_over.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
            assert r_over.json()["meta"]["request_id"] == "over-1mib"

            # 3. Invalid/negative/non-integer Content-Length with small body does not crash (no 500)
            for bad_cl in ("-1", "not-an-int", ""):
                r_bad_cl = await client.post(
                    "/api/v1/auth/google",
                    content=b"{}",
                    headers={
                        "Content-Length": bad_cl,
                        "Content-Type": "application/json",
                        "X-Request-ID": f"cl-{bad_cl or 'empty'}",
                    },
                )
                assert r_bad_cl.status_code == 422
                assert r_bad_cl.json()["error"]["code"] == "VALIDATION_ERROR"

            # 4. Chunked (no CL) and spoofed/negative/non-int CL with oversized body -> 413
            monkeypatch.setattr(settings, "MAX_JSON_BODY_BYTES", 128)

            async def oversized_chunks() -> AsyncIterator[bytes]:
                yield b'{"id_token":"'
                yield b"z" * 160
                yield b'"}'

            for label, extra_headers in (
                ("chunk-real", {}),
                ("spoof-real", {"Content-Length": "10"}),
                ("neg-cl-real", {"Content-Length": "-5"}),
                ("bad-cl-real", {"Content-Length": "abc"}),
            ):
                r_stream = await client.post(
                    "/api/v1/auth/google",
                    content=oversized_chunks(),
                    headers={
                        "Content-Type": "application/json",
                        "X-Request-ID": label,
                        **extra_headers,
                    },
                )
                assert r_stream.status_code == 413
                assert r_stream.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
                assert r_stream.headers["x-request-id"] == label
                assert r_stream.json()["meta"]["request_id"] == label


@pytest.mark.asyncio
async def test_mw2_real_app_voice_transcriptions_audio_vs_multipart_limits(auth_config, monkeypatch):
    monkeypatch.setattr(settings, "VOICE_MAX_AUDIO_BYTES", 200)
    monkeypatch.setattr(settings, "VOICE_MULTIPART_OVERHEAD_BYTES", 400)

    async with _real_app_sqlite_env() as sf:
        uid = uuid4()
        async with sf() as db:
            db.add(User(id=uid, email="voice@example.com", display_name="Voice", is_active=True))
            await db.commit()

        token = issue_app_access_token(uid).access_token
        boundary = "----qa-voice-boundary"
        base_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": "http://localhost:8080",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Audio == VOICE_MAX_AUDIO_BYTES (200) & total multipart <= 600 -> 200 OK
            mp_ok = _build_voice_multipart(b"A" * 200, boundary=boundary)
            assert len(mp_ok) <= 600
            r_ok = await client.post(
                "/api/v1/voice/transcriptions",
                content=mp_ok,
                headers={**base_headers, "Idempotency-Key": "voice-ok-1", "X-Request-ID": "v-ok"},
            )
            assert r_ok.status_code == 200
            assert r_ok.headers["x-request-id"] == "v-ok"
            assert r_ok.json()["meta"]["request_id"] == "v-ok"

            # 2. Audio == VOICE_MAX_AUDIO_BYTES + 1 (201) & total multipart <= 600 -> voice.py 413 AUDIO_TOO_LARGE
            mp_audio_too_big = _build_voice_multipart(b"A" * 201, boundary=boundary)
            assert len(mp_audio_too_big) <= 600
            r_audio_big = await client.post(
                "/api/v1/voice/transcriptions",
                content=mp_audio_too_big,
                headers={
                    **base_headers,
                    "Idempotency-Key": "voice-big-audio",
                    "X-Request-ID": "v-audio-413",
                },
            )
            assert r_audio_big.status_code == 413
            assert r_audio_big.json()["error"]["code"] == "AUDIO_TOO_LARGE"
            assert r_audio_big.headers["x-request-id"] == "v-audio-413"
            assert r_audio_big.json()["meta"]["request_id"] == "v-audio-413"
            _assert_security_and_cors_headers(r_audio_big, expect_no_store=False)

            # 3. Multipart payload > VOICE_MAX_AUDIO_BYTES + VOICE_MULTIPART_OVERHEAD_BYTES (600) -> 413 PAYLOAD_TOO_LARGE
            mp_over_limit = _build_voice_multipart(b"A" * 400, boundary=boundary)
            assert len(mp_over_limit) > 600
            r_mp_cl = await client.post(
                "/api/v1/voice/transcriptions",
                content=mp_over_limit,
                headers={
                    **base_headers,
                    "Idempotency-Key": "voice-mp-cl",
                    "X-Request-ID": "v-mp-cl-413",
                },
            )
            assert r_mp_cl.status_code == 413
            assert r_mp_cl.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
            assert r_mp_cl.json()["meta"]["request_id"] == "v-mp-cl-413"

            async def mp_chunks() -> AsyncIterator[bytes]:
                for i in range(0, len(mp_over_limit), 100):
                    yield mp_over_limit[i : i + 100]

            for label, extra in (
                ("v-mp-chunk-413", {}),
                ("v-mp-spoof-413", {"Content-Length": "10"}),
            ):
                r_mp_stream = await client.post(
                    "/api/v1/voice/transcriptions",
                    content=mp_chunks(),
                    headers={**base_headers, "Idempotency-Key": label, "X-Request-ID": label, **extra},
                )
                assert r_mp_stream.status_code == 413
                assert r_mp_stream.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
                assert r_mp_stream.headers["x-request-id"] == label
                assert r_mp_stream.json()["meta"]["request_id"] == label


@pytest.mark.asyncio
async def test_mw1_request_id_across_200_401_404_409_422_500_and_malformed_ids(auth_config):
    state = {"sub": "sub-1", "boom": False}

    async def dynamic_verifier(token: str) -> VerifiedGoogleIdentity:
        if state["boom"]:
            raise RuntimeError("unhandled verifier boom")
        return VerifiedGoogleIdentity(sub=state["sub"], email="rid@example.com", display_name="RID")

    async with _real_app_sqlite_env({get_google_verifier: lambda: dynamic_verifier}) as sf:
        uid = uuid4()
        async with sf() as db:
            db.add(User(id=uid, email="rid@example.com", display_name="RID", is_active=True))
            await db.commit()
        token = issue_app_access_token(uid).access_token

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            # 200 OK
            r200 = await client.get(
                "/api/v1/capabilities",
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": "rid-200"},
            )
            assert r200.status_code == 200
            assert r200.headers["x-request-id"] == "rid-200"
            assert r200.json()["meta"]["request_id"] == "rid-200"

            # 401 Unauthenticated
            r401 = await client.get("/api/v1/auth/me", headers={"X-Request-ID": "rid-401"})
            assert r401.status_code == 401
            assert r401.headers["x-request-id"] == "rid-401"
            assert r401.json()["meta"]["request_id"] == "rid-401"

            # 404 AppError (task not found) & unmatched 404
            r404 = await client.get(
                f"/api/v1/tasks/{uuid4()}",
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": "rid-404"},
            )
            assert r404.status_code == 404
            assert r404.headers["x-request-id"] == "rid-404"
            assert r404.json()["meta"]["request_id"] == "rid-404"

            r404_unmatched = await client.get("/no-such-path", headers={"X-Request-ID": "rid-404-u"})
            assert r404_unmatched.status_code == 404
            assert r404_unmatched.headers["x-request-id"] == "rid-404-u"

            # 422 RequestValidationError
            r422 = await client.post(
                "/api/v1/auth/google",
                json={},
                headers={"X-Request-ID": "rid-422"},
            )
            assert r422.status_code == 422
            assert r422.headers["x-request-id"] == "rid-422"
            assert r422.json()["meta"]["request_id"] == "rid-422"

            # 409 AppError (email collision with existing user when sub differs)
            r409 = await client.post(
                "/api/v1/auth/google",
                json={"id_token": "tok"},
                headers={"X-Request-ID": "rid-409"},
            )
            assert r409.status_code == 409
            assert r409.headers["x-request-id"] == "rid-409"
            assert r409.json()["meta"]["request_id"] == "rid-409"

            # 500 Unhandled Exception -> INTERNAL_ERROR with preserved X-Request-ID
            state["boom"] = True
            r500 = await client.post(
                "/api/v1/auth/google",
                json={"id_token": "tok"},
                headers={"X-Request-ID": "rid-500"},
            )
            assert r500.status_code == 500
            assert r500.headers["x-request-id"] == "rid-500"
            assert r500.json()["error"]["code"] == "INTERNAL_ERROR"
            assert r500.json()["meta"]["request_id"] == "rid-500"

            # Malformed / oversized / whitespace X-Request-ID replaced with req-<8hex>
            for bad_id in ("", "has space", "a" * 65, "bad;value"):
                r_bad = await client.get(
                    "/api/v1/capabilities",
                    headers={"Authorization": f"Bearer {token}", "X-Request-ID": bad_id},
                )
                assert r_bad.status_code == 200
                safe_id = r_bad.headers["x-request-id"]
                assert safe_id.startswith("req-") and len(safe_id) == 12
                assert r_bad.json()["meta"]["request_id"] == safe_id

    # CRLF injection via raw ASGI scope (httpx blocks raw \r\n in client headers)
    sent_messages: list[dict] = []

    async def inner_asgi(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = RequestIdMiddleware(inner_asgi)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "headers": [(b"x-request-id", b"evil\r\nX-Injected: 1")],
    }

    async def dummy_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def capture_send(msg):
        sent_messages.append(msg)

    await mw(scope, dummy_receive, capture_send)
    state_id = scope["state"]["request_id"]
    assert state_id.startswith("req-") and len(state_id) == 12
    assert "\r" not in state_id and "\n" not in state_id
    resp_headers = dict(sent_messages[0]["headers"])
    assert resp_headers[b"x-request-id"] == state_id.encode("ascii")
    assert get_current_request_id() is None


@pytest.mark.asyncio
async def test_gap06_capabilities_and_security_cache_control_headers(auth_config, monkeypatch):
    async with _real_app_sqlite_env() as sf:
        uid = uuid4()
        async with sf() as db:
            db.add(User(id=uid, email="sec@example.com", display_name="Sec", is_active=True))
            await db.commit()
            _, auth_url = await CalendarService(db).create_authorization(
                user_id=uid,
                device_id="dev1",
                return_target="proplan://calendar/connected",
            )
        state_token = parse_qs(urlparse(auth_url).query)["state"][0]

        token = issue_app_access_token(uid).access_token
        auth_headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:8080"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # GAP-06 dynamic capabilities update
            monkeypatch.setattr(settings, "VOICE_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
            r_cap = await client.get("/api/v1/capabilities", headers=auth_headers)
            assert r_cap.status_code == 200
            assert r_cap.json()["data"]["configured_parameters"]["audio_max_bytes"] == 5 * 1024 * 1024
            _assert_security_and_cors_headers(r_cap, expect_no_store=False)

            # Public health routes: no Cache-Control header
            for health_path in ("/api/v1/health", "/health"):
                r_h = await client.get(health_path, headers={"Origin": "http://localhost:8080"})
                assert r_h.status_code == 200
                _assert_security_and_cors_headers(r_h, expect_no_store=False)

            # /api/v1/auth/* routes -> Cache-Control: no-store
            r_me = await client.get("/api/v1/auth/me", headers=auth_headers)
            assert r_me.status_code == 200
            _assert_security_and_cors_headers(r_me, expect_no_store=True)

            r_google = await client.post(
                "/api/v1/auth/google",
                json={},
                headers={"Origin": "http://localhost:8080"},
            )
            assert r_google.status_code == 422
            _assert_security_and_cors_headers(r_google, expect_no_store=True)

            r_refresh = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": "bad"},
                headers={"Origin": "http://localhost:8080"},
            )
            assert r_refresh.status_code == 401
            _assert_security_and_cors_headers(r_refresh, expect_no_store=True)

            # /api/v1/calendar/oauth/callback (both 401 error and 302 redirect) -> Cache-Control: no-store
            r_cb_err = await client.get(
                "/api/v1/calendar/oauth/callback?code=c&state=missing",
                headers={"Origin": "http://localhost:8080"},
            )
            assert r_cb_err.status_code == 401
            _assert_security_and_cors_headers(r_cb_err, expect_no_store=True)

            r_cb_redir = await client.get(
                f"/api/v1/calendar/oauth/callback?state={state_token}&error=access_denied",
                headers={"Origin": "http://localhost:8080"},
                follow_redirects=False,
            )
            assert r_cb_redir.status_code == 302
            _assert_security_and_cors_headers(r_cb_redir, expect_no_store=True)


@pytest.mark.asyncio
async def test_mw3_real_app_oauth_callback_redacts_logs_and_uvicorn_access_filter(caplog):
    async with _real_app_sqlite_env():
        with caplog.at_level(logging.INFO):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/calendar/oauth/callback?code=SECRET_OAUTH_CODE_123&state=SECRET_STATE_456",
                    headers={"X-Request-ID": "oauth-redact-1"},
                )
            assert resp.status_code == 401

    app_messages = [rec.getMessage() for rec in caplog.records if rec.name.startswith("app")]
    for msg in app_messages:
        assert "SECRET_OAUTH_CODE_123" not in msg
        assert "SECRET_STATE_456" not in msg

    app_access_msgs = [rec.getMessage() for rec in caplog.records if rec.name == "app.access"]
    assert any(
        "request_id=oauth-redact-1" in m
        and "code=<REDACTED>" in m
        and "state=<REDACTED>" in m
        for m in app_access_msgs
    )

    # Direct verification against simulated uvicorn.access log formats
    log_filter = OAuthLogRedactionFilter()
    uvicorn_rec = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s" %d',
        args=(
            "127.0.0.1:5000",
            "GET /api/v1/calendar/oauth/callback?code=ABC&state=XYZ HTTP/1.1",
            302,
        ),
        exc_info=None,
    )
    assert log_filter.filter(uvicorn_rec) is True
    rendered = uvicorn_rec.getMessage()
    assert "ABC" not in rendered
    assert "XYZ" not in rendered
    assert "code=<REDACTED>" in rendered
    assert "state=<REDACTED>" in rendered


@pytest.mark.asyncio
async def test_mw3_real_app_observability_route_templates_and_unmatched_404(auth_config, caplog):
    async with _real_app_sqlite_env() as sf:
        uid = uuid4()
        async with sf() as db:
            db.add(User(id=uid, email="obsreal@example.com", display_name="Obs", is_active=True))
            await db.commit()
        token = issue_app_access_token(uid).access_token
        headers = {"Authorization": f"Bearer {token}"}

        with caplog.at_level(logging.INFO, logger="app.access"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r1 = await client.get(f"/api/v1/tasks/{uuid4()}", headers=headers)
                r2 = await client.get(f"/api/v1/tasks/{uuid4()}", headers=headers)
                r_h_v1 = await client.get("/api/v1/health")
                r_h_root = await client.get("/health")
                r_404 = await client.get(f"/random-missing-{uuid4()}")

        assert r1.status_code == 404
        assert r2.status_code == 404
        assert r_h_v1.status_code == 200
        assert r_h_root.status_code == 200
        assert r_404.status_code == 404

    snap = get_http_observations_snapshot()
    assert snap[("GET", "/api/v1/tasks/{task_id}", 404)]["count"] == 2
    assert snap[("GET", "/api/v1/health", 200)]["count"] == 1
    assert snap[("GET", "/health", 200)]["count"] == 1
    assert snap[("GET", "unmatched", 404)]["count"] == 1


@pytest.mark.asyncio
async def test_mw4a_unhandled_500_preserves_security_cors_and_request_id_headers(auth_config):
    async def boom_verifier(token: str) -> VerifiedGoogleIdentity:
        raise RuntimeError("boom inside route")

    async with _real_app_sqlite_env({get_google_verifier: lambda: boom_verifier}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/google",
                json={"id_token": "any"},
                headers={
                    "Origin": "http://localhost:8080",
                    "X-Request-ID": "mw4a-500-trace",
                },
            )

    assert resp.status_code == 500
    assert resp.headers["x-request-id"] == "mw4a-500-trace"
    _assert_security_and_cors_headers(resp, expect_no_store=True)
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["retryable"] is True
    assert body["error"]["next_action"] == "retry_later"
    assert body["meta"]["request_id"] == "mw4a-500-trace"
    assert get_current_request_id() is None
    snap = get_http_observations_snapshot()
    assert snap[("POST", "/api/v1/auth/google", 500)]["count"] == 1

    # Non-auth route unhandled 500 still gets CORS + security headers without Cache-Control: no-store
    test_app = FastAPI()
    test_app.add_middleware(BodySizeLimitMiddleware)
    test_app.add_middleware(ObservabilityMiddleware)
    test_app.add_middleware(RequestIdMiddleware)
    test_app.add_middleware(SecurityHeadersMiddleware)
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    register_exception_handlers(test_app)

    @test_app.get("/api/v1/boom-non-auth")
    async def boom_non_auth():
        raise ValueError("non-auth boom")

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        r_non_auth = await client.get(
            "/api/v1/boom-non-auth",
            headers={"Origin": "http://localhost:8080", "X-Request-ID": "mw4a-nonauth-500"},
        )
    assert r_non_auth.status_code == 500
    assert r_non_auth.headers["x-request-id"] == "mw4a-nonauth-500"
    _assert_security_and_cors_headers(r_non_auth, expect_no_store=False)

    # If exception occurs AFTER response_started=True, ObservabilityMiddleware re-raises to abort stream
    async def mid_stream_fail_asgi(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("mid-stream failure")

    obs_mw = ObservabilityMiddleware(mid_stream_fail_asgi)
    with pytest.raises(RuntimeError, match="mid-stream failure"):
        await obs_mw(
            {"type": "http", "method": "GET", "path": "/stream", "headers": []},
            lambda: None,  # type: ignore[arg-type]
            lambda msg: _noop_async(msg),
        )


async def _noop_async(_msg) -> None:
    return None


@pytest.mark.asyncio
async def test_mw4a_metrics_endpoints_prometheus_format_buckets_outbox_ai_and_db_down(
    auth_config, monkeypatch
):
    ai_service.health.reset()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: object())
    monkeypatch.setattr(ai_service, "_get_groq_client", lambda: None)

    try:
        async with _real_app_sqlite_env() as sf:
            uid = uuid4()
            conn_id = uuid4()
            async with sf() as db:
                db.add(User(id=uid, email="metrics@example.com", display_name="M", is_active=True))
                await db.flush()
                db.add(
                    CalendarConnection(
                        id=conn_id,
                        user_id=uid,
                        provider="google",
                        account_ref="metrics@gmail.com",
                        read_calendar_ids=["primary"],
                        write_calendar_id="primary",
                        granted_scopes=["https://www.googleapis.com/auth/calendar"],
                        connection_state="connected",
                        credential_ref="enc",
                    )
                )
                await db.flush()
                for idx, job_state in enumerate(("queued", "queued", "failed", "conflict")):
                    db.add(
                        CalendarSyncJob(
                            user_id=uid,
                            connection_id=conn_id,
                            target_kind="event",
                            target_ref=f"ref-{idx}",
                            operation="create",
                            idempotency_key=f"metrics-idem-{idx}",
                            job_state=job_state,
                            attempt_count=0,
                            payload_snapshot={"summary": "Focus"},
                        )
                    )
                await db.commit()

            # Trigger 2 consecutive failures on Gemini Flash model so circuit opens (state=2, failures=2)
            ai_service.health.record_failure(
                "gemini", settings.GEMINI_FLASH_MODEL, RuntimeError("fail-1")
            )
            ai_service.health.record_failure(
                "gemini", settings.GEMINI_FLASH_MODEL, RuntimeError("fail-2")
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                h1 = await client.get("/api/v1/health")
                h2 = await client.get("/api/v1/health")
                assert h1.status_code == 200 and h2.status_code == 200

                m_v1 = await client.get("/api/v1/metrics")
                m_root = await client.get("/metrics")

            for resp in (m_v1, m_root):
                assert resp.status_code == 200
                assert resp.headers["content-type"] == PROMETHEUS_CONTENT_TYPE

            text_body = m_v1.text
            assert text_body.endswith("\n")
            assert "# TYPE proactive_http_requests_total counter" in text_body
            assert "# TYPE proactive_http_request_duration_seconds histogram" in text_body
            assert (
                'proactive_http_requests_total{method="GET",route_template="/api/v1/health",status_code="200"} 2'
                in text_body
            )

            # Verify histogram buckets are monotonic and +Inf == _count
            bucket_values: list[int] = []
            for threshold in LATENCY_BUCKETS:
                prefix = (
                    f'proactive_http_request_duration_seconds_bucket{{method="GET",'
                    f'route_template="/api/v1/health",status_code="200",le="{threshold}"}} '
                )
                line = next(ln for ln in text_body.splitlines() if ln.startswith(prefix))
                bucket_values.append(int(line.split()[-1]))
            assert bucket_values == sorted(bucket_values)
            assert (
                'proactive_http_request_duration_seconds_bucket{method="GET",'
                'route_template="/api/v1/health",status_code="200",le="+Inf"} 2'
                in text_body
            )
            assert (
                'proactive_http_request_duration_seconds_count{method="GET",'
                'route_template="/api/v1/health",status_code="200"} 2'
                in text_body
            )
            assert bucket_values[-1] <= 2

            # Process, DB pool, DB readiness, Calendar outbox, PG defaults, AI circuit, scrape duration
            assert "proactive_process_uptime_seconds " in text_body
            assert "proactive_process_cpu_seconds_total " in text_body
            assert "proactive_process_resident_memory_bytes " in text_body
            assert get_process_rss_bytes() >= 0
            assert "proactive_db_pool_size " in text_body
            assert "proactive_db_pool_checkedin " in text_body
            assert "proactive_db_pool_checkedout " in text_body
            assert "proactive_db_pool_overflow " in text_body
            assert "proactive_db_up 1" in text_body
            assert 'proactive_calendar_outbox_jobs{job_state="queued"} 2' in text_body
            assert 'proactive_calendar_outbox_jobs{job_state="processing"} 0' in text_body
            assert 'proactive_calendar_outbox_jobs{job_state="completed"} 0' in text_body
            assert 'proactive_calendar_outbox_jobs{job_state="failed"} 1' in text_body
            assert 'proactive_calendar_outbox_jobs{job_state="conflict"} 1' in text_body
            assert "proactive_db_pg_lock_waiting_connections 0" in text_body
            assert (
                f'proactive_ai_circuit_state{{provider="gemini",model="{settings.GEMINI_FLASH_MODEL}"}} 2'
                in text_body
            )
            assert (
                f'proactive_ai_consecutive_failures{{provider="gemini",model="{settings.GEMINI_FLASH_MODEL}"}} 2'
                in text_body
            )
            assert (
                f'proactive_ai_circuit_state{{provider="groq",model="{settings.GROQ_FALLBACK_MODEL}"}} -1'
                in text_body
            )
            assert "proactive_metrics_scrape_duration_seconds " in text_body

        # Broken DB session still returns HTTP 200 with proactive_db_up 0
        class _BrokenSession:
            async def execute(self, *_args, **_kwargs):
                raise RuntimeError("db unavailable")

        async def broken_db():
            yield _BrokenSession()

        prev = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = broken_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                m_down = await client.get("/api/v1/metrics")
            assert m_down.status_code == 200
            assert m_down.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
            assert "proactive_db_up 0" in m_down.text
            assert 'proactive_calendar_outbox_jobs{job_state="queued"} 0' in m_down.text
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prev)
    finally:
        ai_service.health.reset()


@pytest.mark.asyncio
async def test_mw4a_metrics_label_escaping_and_postgresql_activity_queries():
    assert _escape_label('a\\b"c\nd\re') == 'a\\\\b\\"c\\nd\\re'

    class _FakeResult:
        def __init__(self, rows=None, scalar=None):
            self._rows = rows or []
            self._scalar = scalar

        def all(self):
            return self._rows

        def scalar_one_or_none(self):
            return self._scalar

    class _FakePGSession:
        def get_bind(self):
            class _Dialect:
                name = "postgresql"

            class _Bind:
                dialect = _Dialect()

            return _Bind()

        async def execute(self, stmt, *_args, **_kwargs):
            sql = str(stmt)
            if "SELECT 1" in sql:
                return _FakeResult()
            if "pg_stat_activity" in sql and "GROUP BY" in sql:
                return _FakeResult(rows=[("active", 3), ("idle", 7)])
            if "wait_event_type = 'Lock'" in sql:
                return _FakeResult(scalar=2)
            return _FakeResult(rows=[("queued", 4)])

    out = await collect_prometheus_metrics(_FakePGSession())  # type: ignore[arg-type]
    assert "proactive_db_up 1" in out
    assert 'proactive_calendar_outbox_jobs{job_state="queued"} 4' in out
    assert 'proactive_db_pg_connections{state="active"} 3' in out
    assert 'proactive_db_pg_connections{state="idle"} 7' in out
    assert "proactive_db_pg_lock_waiting_connections 2" in out


@pytest.mark.asyncio
async def test_mw4b_500_non_auth_real_app_cors_preflight_and_snapshot_isolation(
    auth_config, monkeypatch
):
    async def boom_get_task(*_args, **_kwargs):
        raise RuntimeError("non-auth endpoint failure")

    monkeypatch.setattr(plan_service, "get_task_by_id", boom_get_task)

    async with _real_app_sqlite_env() as sf:
        uid = uuid4()
        async with sf() as db:
            db.add(User(id=uid, email="mw4b500@example.com", display_name="500", is_active=True))
            await db.commit()
        token = issue_app_access_token(uid).access_token

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. CORS preflight on auth and non-auth endpoints
            for path, method in (
                ("/api/v1/auth/google", "POST"),
                (f"/api/v1/tasks/{uuid4()}", "GET"),
            ):
                pre = await client.options(
                    path,
                    headers={
                        "Origin": "http://localhost:8080",
                        "Access-Control-Request-Method": method,
                        "Access-Control-Request-Headers": "Authorization, Content-Type, X-Request-ID",
                    },
                )
                assert pre.status_code == 200
                assert pre.headers.get("access-control-allow-origin") in {
                    "*",
                    "http://localhost:8080",
                }
                assert method in pre.headers.get("access-control-allow-methods", "")

            # 2. Auth 500 vs Non-auth 500 on REAL app: non-auth 500 must NOT get Cache-Control: no-store
            r_non_auth = await client.get(
                f"/api/v1/tasks/{uuid4()}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": "http://localhost:8080",
                    "X-Request-ID": "mw4b-nonauth-500",
                },
            )
            assert r_non_auth.status_code == 500
            assert r_non_auth.headers["x-request-id"] == "mw4b-nonauth-500"
            assert r_non_auth.json()["error"]["code"] == "INTERNAL_ERROR"
            assert r_non_auth.json()["meta"]["request_id"] == "mw4b-nonauth-500"
            _assert_security_and_cors_headers(r_non_auth, expect_no_store=False)

    # 3. Deep-copy isolation on snapshot buckets
    snap1 = get_http_observations_snapshot()
    key = ("GET", "/api/v1/tasks/{task_id}", 500)
    assert snap1[key]["count"] == 1
    snap1[key]["buckets"][0.01] = 999999
    snap2 = get_http_observations_snapshot()
    assert snap2[key]["buckets"][0.01] != 999999

    # 4. Mid-stream exception after response_started=True still records started status code & re-raises
    reset_http_observations()

    async def mid_stream_fail_asgi(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("mid-stream boom")

    obs_mw = ObservabilityMiddleware(mid_stream_fail_asgi)
    with pytest.raises(RuntimeError, match="mid-stream boom"):
        await obs_mw(
            {"type": "http", "method": "GET", "path": "/stream", "headers": []},
            lambda: None,  # type: ignore[arg-type]
            _noop_async,
        )
    stream_snap = get_http_observations_snapshot()
    assert stream_snap[("GET", "unmatched", 200)]["count"] == 1


@pytest.mark.asyncio
async def test_mw4b_prometheus_multi_route_grammar_escaping_db_none_and_pool_resilience(
    auth_config, monkeypatch
):
    # 1. OpenAPI: /api/v1/metrics is public (no AppBearer security), /metrics is excluded
    schema = app.openapi()
    assert "/metrics" not in schema["paths"]
    metrics_op = schema["paths"]["/api/v1/metrics"]["get"]
    assert not metrics_op.get("security")

    # 2. Multi-route/multi-status scrape on real app (AUTH_MODE='google', no Authorization header)
    async with _real_app_sqlite_env():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/health")).status_code == 200
            assert (await client.get("/health")).status_code == 200
            assert (await client.post("/api/v1/auth/google", json={})).status_code == 422
            assert (await client.get("/missing-metrics-probe")).status_code == 404

            m_v1 = await client.get("/api/v1/metrics")
            m_root = await client.get("/metrics")
            assert m_v1.status_code == 200
            assert m_root.status_code == 200

    lines = m_v1.text.splitlines()
    help_names = [ln.split()[2] for ln in lines if ln.startswith("# HELP ")]
    type_names = [ln.split()[2] for ln in lines if ln.startswith("# TYPE ")]
    assert len(help_names) == len(set(help_names))
    assert len(type_names) == len(set(type_names))
    assert help_names.count("proactive_http_request_duration_seconds") == 1
    assert type_names.count("proactive_http_request_duration_seconds") == 1
    assert help_names.count("proactive_http_requests_total") == 1
    assert type_names.count("proactive_http_requests_total") == 1

    # Strict Prometheus 0.0.4 sample grammar check
    prom_line_re = re.compile(
        r"^[a-zA-Z_:][a-zA-Z0-9_:]*"
        r'(\{[a-zA-Z_][a-zA-Z0-9_]*="([^"\\]|\\["\\nr])*"'
        r'(,[a-zA-Z_][a-zA-Z0-9_]*="([^"\\]|\\["\\nr])*")*\})?'
        r" [-+]?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$"
    )
    for ln in lines:
        if ln.startswith("#"):
            continue
        assert prom_line_re.fullmatch(ln), f"Invalid Prometheus 0.0.4 sample line: {ln}"

    # Verify bucket ordering, count, and +Inf == _count for every observed route/status series
    expected_les = [str(b) for b in LATENCY_BUCKETS] + ["+Inf"]
    for method, route_tpl, status in (
        ("GET", "/api/v1/health", 200),
        ("GET", "/health", 200),
        ("POST", "/api/v1/auth/google", 422),
        ("GET", "unmatched", 404),
    ):
        series_prefix = (
            f'proactive_http_request_duration_seconds_bucket{{method="{method}",'
            f'route_template="{route_tpl}",status_code="{status}",le="'
        )
        matched_buckets = [ln for ln in lines if ln.startswith(series_prefix)]
        assert len(matched_buckets) == len(expected_les)
        observed_les = [ln.split('le="')[1].split('"')[0] for ln in matched_buckets]
        assert observed_les == expected_les
        counts = [int(ln.split()[-1]) for ln in matched_buckets]
        assert counts == sorted(counts)
        count_line = next(
            ln
            for ln in lines
            if ln.startswith(
                f'proactive_http_request_duration_seconds_count{{method="{method}",'
                f'route_template="{route_tpl}",status_code="{status}"}} '
            )
        )
        total_count = int(count_line.split()[-1])
        assert counts[-1] == total_count
        assert counts[-2] <= total_count

    # 3. collect_prometheus_metrics(db=None) resilience
    out_none = await collect_prometheus_metrics(db=None)
    assert "proactive_db_up 0" in out_none
    for state_name in ("queued", "processing", "completed", "failed", "conflict"):
        assert f'proactive_calendar_outbox_jobs{{job_state="{state_name}"}} 0' in out_none
    assert "proactive_db_pg_lock_waiting_connections 0" in out_none

    # 4. Pool error/coroutine & AI check failure resilience + end-to-end label escaping
    class _WeirdPool:
        def size(self):
            raise RuntimeError("pool error")

        async def checkedin(self):
            return 3

        def checkedout(self):
            return "not-an-int"

        def overflow(self):
            return 0

    class _WeirdEngine:
        class _Sync:
            pool = _WeirdPool()

        sync_engine = _Sync()

    assert _read_pool_metric(_WeirdPool(), "size") == 0
    assert _read_pool_metric(_WeirdPool(), "checkedin") == 0
    assert _read_pool_metric(_WeirdPool(), "checkedout") == 0

    class _EscapedPGResult:
        def __init__(self, rows=None, scalar=None):
            self._rows = rows or []
            self._scalar = scalar

        def all(self):
            return self._rows

        def scalar_one_or_none(self):
            return self._scalar

    class _EscapedPGSession:
        def get_bind(self):
            class _Dialect:
                name = "postgresql"

            class _Bind:
                dialect = _Dialect()

            return _Bind()

        async def execute(self, stmt, *_args, **_kwargs):
            sql = str(stmt)
            if "SELECT 1" in sql:
                return _EscapedPGResult()
            if "pg_stat_activity" in sql and "GROUP BY" in sql:
                return _EscapedPGResult(rows=[('idle\\"\n\r', 1)])
            if "wait_event_type = 'Lock'" in sql:
                return _EscapedPGResult(scalar=0)
            return _EscapedPGResult(rows=[('weird\\"\n\r', 2)])

    async def failing_ai_status(*_args, **_kwargs):
        raise RuntimeError("ai health unavailable")

    monkeypatch.setattr("app.core.metrics.get_engine", lambda: _WeirdEngine())
    monkeypatch.setattr(ai_service, "check_providers_status", failing_ai_status)

    escaped_out = await collect_prometheus_metrics(_EscapedPGSession())  # type: ignore[arg-type]
    assert "proactive_db_pool_size 0" in escaped_out
    assert "proactive_db_pool_checkedin 0" in escaped_out
    assert "proactive_db_pool_checkedout 0" in escaped_out
    assert 'proactive_calendar_outbox_jobs{job_state="weird\\\\\\"\\n\\r"} 2' in escaped_out
    assert 'proactive_db_pg_connections{state="idle\\\\\\"\\n\\r"} 1' in escaped_out
    assert "# TYPE proactive_ai_circuit_state gauge" in escaped_out
    assert "# TYPE proactive_ai_consecutive_failures gauge" in escaped_out


@pytest.mark.asyncio
async def test_p3_2_metrics_protected_by_metrics_auth_token_when_deployed(monkeypatch):
    """PR #22 P3.2: /metrics & /api/v1/metrics mở ở dev/test, khóa bằng METRICS_AUTH_TOKEN khi staging/production, không đổi OpenAPI."""
    from app.core.config import Settings

    schema = app.openapi()
    assert "/metrics" not in schema["paths"]
    assert not schema["paths"]["/api/v1/metrics"]["get"].get("security")

    secret_cfg = Settings(_env_file=None, METRICS_AUTH_TOKEN="super-secret-scrape-token")
    assert "super-secret-scrape-token" not in repr(secret_cfg)

    async with _real_app_sqlite_env():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Ở development/testing: mở hoàn toàn không cần token
            monkeypatch.setattr(settings, "ENVIRONMENT", "development")
            monkeypatch.setattr(settings, "METRICS_AUTH_TOKEN", "")
            for path in ("/metrics", "/api/v1/metrics"):
                r_dev = await client.get(path)
                assert r_dev.status_code == 200

            # 2) Ở staging/production nhưng chưa cấu hình METRICS_AUTH_TOKEN -> 403 FORBIDDEN
            monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
            monkeypatch.setattr(settings, "METRICS_AUTH_TOKEN", "")
            for path in ("/metrics", "/api/v1/metrics"):
                r_unconfigured = await client.get(path)
                assert r_unconfigured.status_code == 403
                assert r_unconfigured.json()["error"]["code"] == "FORBIDDEN"

            # 3) Ở staging/production có METRICS_AUTH_TOKEN: thiếu/sai token -> 401; đúng Bearer token -> 200
            monkeypatch.setattr(settings, "ENVIRONMENT", "production")
            monkeypatch.setattr(settings, "METRICS_AUTH_TOKEN", "prom-token-123")
            for path in ("/metrics", "/api/v1/metrics"):
                r_no_hdr = await client.get(path)
                assert r_no_hdr.status_code == 401
                assert r_no_hdr.json()["error"]["code"] == "UNAUTHENTICATED"
                assert r_no_hdr.headers.get("www-authenticate") == "Bearer"

                r_bad_tok = await client.get(
                    path, headers={"Authorization": "Bearer wrong-token"}
                )
                assert r_bad_tok.status_code == 401

                # Token chứa ký tự non-ASCII Latin-1 (vd "Bearer caf\xe9") hoặc toàn khoảng trắng phải trả 401, không bao giờ 500 TypeError
                r_non_ascii_tok = await client.get(
                    path, headers=[(b"authorization", b"Bearer caf\xe9")]
                )
                assert r_non_ascii_tok.status_code == 401
                assert r_non_ascii_tok.json()["error"]["code"] == "UNAUTHENTICATED"
                assert r_non_ascii_tok.headers.get("www-authenticate") == "Bearer"

                r_blank_tok = await client.get(
                    path, headers={"Authorization": "Bearer    "}
                )
                assert r_blank_tok.status_code == 401
                assert r_blank_tok.json()["error"]["code"] == "UNAUTHENTICATED"

                r_ok = await client.get(
                    path, headers={"Authorization": "Bearer prom-token-123"}
                )
                assert r_ok.status_code == 200
                assert r_ok.headers["content-type"] == PROMETHEUS_CONTENT_TYPE



