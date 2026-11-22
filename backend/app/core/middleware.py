"""Pure ASGI middleware stack: RequestId, SecurityHeaders, BodySizeLimit, and Observability."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from typing import TypedDict
from urllib.parse import parse_qsl, urlencode
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)
_access_logger = logging.getLogger("app.access")

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_VALID_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
_NO_STORE_PATH_PREFIXES: tuple[str, ...] = ("/api/v1/auth", "/api/v1/calendar/oauth")

_SENSITIVE_QUERY_KEYS: set[str] = {
    "code",
    "state",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "password",
    "key",
}
_QUERY_SUBSTRING_RE = re.compile(r"\?([^\s\"']+)")
_SENSITIVE_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    + "|".join(sorted(_SENSITIVE_QUERY_KEYS, key=len, reverse=True))
    + r")=([^&\s\"']+)",
    re.IGNORECASE,
)

MetricHook = Callable[[str, str, int, float], None]

_LATENCY_BUCKETS: tuple[float, ...] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.3,
    0.5,
    1.0,
    2.5,
    4.5,
    7.5,
    9.0,
    20.0,
)
LATENCY_BUCKETS = _LATENCY_BUCKETS


class HttpObservationBucket(TypedDict):
    """Tổng hợp số lượng, tổng/min/max thời gian và các bucket histogram của 1 route."""

    count: int
    duration_sum_seconds: float
    duration_min_seconds: float
    duration_max_seconds: float
    buckets: dict[float, int]


_observations_lock = threading.Lock()
_http_observations: dict[tuple[str, str, int], HttpObservationBucket] = {}
_metric_hooks: list[MetricHook] = []


def get_current_request_id() -> str | None:
    """Trả về request_id hiện tại từ ContextVar (hoặc None ngoài ngữ cảnh HTTP)."""
    return request_id_ctx.get()


def sanitize_or_generate_request_id(raw: str | None) -> str:
    """Giữ nguyên X-Request-ID nếu khớp whitelist ASCII an toàn; ngược lại sinh ID mới."""
    if raw is not None and _VALID_REQUEST_ID_RE.fullmatch(raw) is not None:
        return raw
    return f"req-{uuid4().hex[:8]}"


def redact_query_string(raw_query: str | bytes) -> str:
    """Che giá trị các tham số nhạy cảm trong query string trước khi ghi log."""
    query_str = (
        raw_query.decode("utf-8", errors="replace")
        if isinstance(raw_query, bytes)
        else raw_query
    )
    if not query_str:
        return ""
    pairs = parse_qsl(query_str, keep_blank_values=True)
    if not pairs:
        return ""
    redacted_pairs = [
        (key, "<REDACTED>" if key.lower() in _SENSITIVE_QUERY_KEYS else value)
        for key, value in pairs
    ]
    return urlencode(redacted_pairs, safe="<>")


def _redact_log_text(text: str) -> str:
    """Che các query param nhạy cảm (code=, state=, ...) trong thông điệp log."""

    def _replace_query(match: re.Match[str]) -> str:
        redacted = redact_query_string(match.group(1))
        return f"?{redacted}" if redacted else "?"

    step1 = _QUERY_SUBSTRING_RE.sub(_replace_query, text)
    return _SENSITIVE_ASSIGN_RE.sub(r"\1=<REDACTED>", step1)


class OAuthLogRedactionFilter(logging.Filter):
    """Filter logging che query param nhạy cảm (code=, state=, token=, ...) trước khi xuất log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_log_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _redact_log_text(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                k: (_redact_log_text(v) if isinstance(v, str) else v)
                for k, v in record.args.items()
            }
        return True


def install_access_log_redaction_filter() -> None:
    """Gắn OAuthLogRedactionFilter một lần (idempotent) lên uvicorn.access và app.access."""
    for logger_name in ("uvicorn.access", "app.access"):
        target_logger = logging.getLogger(logger_name)
        if not any(isinstance(f, OAuthLogRedactionFilter) for f in target_logger.filters):
            target_logger.addFilter(OAuthLogRedactionFilter())


def register_metric_hook(fn: MetricHook) -> None:
    """Đăng ký callback quan sát số liệu HTTP (tích hợp /metrics ở phase sau)."""
    with _observations_lock:
        if fn not in _metric_hooks:
            _metric_hooks.append(fn)


def clear_metric_hooks() -> None:
    """Xóa toàn bộ metric hooks đã đăng ký."""
    with _observations_lock:
        _metric_hooks.clear()


def record_http_observation(
    method: str,
    route_template: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Lưu tổng hợp số liệu HTTP trong bộ nhớ và gọi các metric hooks đã đăng ký."""
    key = (method.upper(), route_template, int(status_code))
    safe_duration = max(0.0, float(duration_seconds))
    with _observations_lock:
        bucket = _http_observations.get(key)
        if bucket is None:
            _http_observations[key] = {
                "count": 1,
                "duration_sum_seconds": safe_duration,
                "duration_min_seconds": safe_duration,
                "duration_max_seconds": safe_duration,
                "buckets": {
                    threshold: 1 if safe_duration <= threshold else 0
                    for threshold in _LATENCY_BUCKETS
                },
            }
        else:
            bucket["count"] += 1
            bucket["duration_sum_seconds"] += safe_duration
            bucket["duration_min_seconds"] = min(bucket["duration_min_seconds"], safe_duration)
            bucket["duration_max_seconds"] = max(bucket["duration_max_seconds"], safe_duration)
            buckets = bucket["buckets"]
            for threshold in _LATENCY_BUCKETS:
                if safe_duration <= threshold:
                    buckets[threshold] += 1
        hooks = list(_metric_hooks)
    for hook in hooks:
        try:
            hook(key[0], key[1], key[2], safe_duration)
        except Exception:
            logger.exception("Metric hook raised an unexpected error")


def get_http_observations_snapshot() -> dict[tuple[str, str, int], HttpObservationBucket]:
    """Trả về bản sao an toàn các số liệu HTTP đã thu thập theo (method, route_template, status_code)."""
    with _observations_lock:
        return {
            key: {
                "count": metrics["count"],
                "duration_sum_seconds": metrics["duration_sum_seconds"],
                "duration_min_seconds": metrics["duration_min_seconds"],
                "duration_max_seconds": metrics["duration_max_seconds"],
                "buckets": dict(metrics["buckets"]),
            }
            for key, metrics in _http_observations.items()
        }


def reset_http_observations() -> None:
    """Đặt lại bộ đếm số liệu HTTP trong bộ nhớ."""
    with _observations_lock:
        _http_observations.clear()


class _PayloadTooLargeError(Exception):
    """Ngoại lệ nội bộ ngắt stream khi body vượt giới hạn kích thước cho phép."""


async def _send_json_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    code: str,
    message: str,
    next_action: str,
    retryable: bool = False,
) -> None:
    # Import trong hàm để tránh circular import giữa app.schemas.common và app.core.middleware.
    from app.schemas.common import ErrorDetail, ErrorEnvelope, build_response_meta

    state = scope.get("state")
    raw_req_id = state.get("request_id") if isinstance(state, Mapping) else None
    request_id = raw_req_id if isinstance(raw_req_id, str) else None
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            field_errors=[],
            retryable=retryable,
            current_revisions={},
            next_action=next_action,
        ),
        meta=build_response_meta(request_id),
    )
    response = JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
    )
    await response(scope, receive, send)


async def _send_payload_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    """Trả về 413 ErrorEnvelope chuẩn khi request body vượt ngưỡng cho phép."""
    await _send_json_error(
        scope,
        receive,
        send,
        status_code=413,
        code="PAYLOAD_TOO_LARGE",
        message="Kích thước dữ liệu gửi lên vượt quá giới hạn cho phép.",
        next_action="reduce_payload_size",
        retryable=False,
    )


def _resolve_route_template(scope: Scope) -> str:
    """Resolve route template đầy đủ gồm prefix router lồng nhau trong FastAPI 0.162+."""
    fastapi_scope = scope.get("fastapi")
    if isinstance(fastapi_scope, Mapping):
        effective_ctx = fastapi_scope.get("effective_route_context")
        effective_path = getattr(effective_ctx, "path", None)
        if isinstance(effective_path, str) and effective_path:
            return effective_path
    route_obj = scope.get("route")
    route_path = getattr(route_obj, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return "unmatched"


class RequestIdMiddleware:
    """Pure ASGI middleware gắn X-Request-ID vào scope.state, ContextVar và response headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_request_id: str | None = None
        for name, value in scope.get("headers", ()):
            if name.lower() == b"x-request-id":
                raw_request_id = value.decode("latin-1").strip(" \t")
                break

        req_id = sanitize_or_generate_request_id(raw_request_id)
        scope.setdefault("state", {})["request_id"] = req_id
        token = request_id_ctx.set(req_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", req_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx.reset(token)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware bổ sung header bảo mật tiêu chuẩn và Cache-Control cho OAuth/Auth."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        is_sensitive_auth_path = path.startswith(_NO_STORE_PATH_PREFIXES)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                if is_sensitive_auth_path and b"cache-control" not in existing:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """Pure ASGI middleware giới hạn kích thước request body và trả 413 ErrorEnvelope."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        if method == "POST" and path.rstrip("/") == "/api/v1/voice/transcriptions":
            max_bytes = int(settings.VOICE_MAX_AUDIO_BYTES) + int(
                settings.VOICE_MULTIPART_OVERHEAD_BYTES
            )
        else:
            max_bytes = int(settings.MAX_JSON_BODY_BYTES)

        for name, value in scope.get("headers", ()):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value.decode("latin-1").strip())
                except ValueError:
                    continue
                if content_length > max_bytes:
                    await _send_payload_too_large(scope, receive, send)
                    return

        total_received = 0
        limit_exceeded = False
        response_started = False

        async def wrapped_receive() -> Message:
            nonlocal total_received, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                total_received += len(message.get("body", b""))
                if total_received > max_bytes:
                    limit_exceeded = True
                    raise _PayloadTooLargeError
            return message

        async def wrapped_send(message: Message) -> None:
            nonlocal response_started
            # Nếu đã vượt giới hạn trước khi response bắt đầu, chặn mọi response lỗi parse 400 từ FastAPI
            # để trả về 413 ErrorEnvelope nhất quán.
            if limit_exceeded and not response_started:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except _PayloadTooLargeError:
            if not response_started:
                await _send_payload_too_large(scope, receive, send)
                return
            raise

        if limit_exceeded and not response_started:
            await _send_payload_too_large(scope, receive, send)


class ObservabilityMiddleware:
    """Pure ASGI middleware đo thời gian phản hồi, ghi nhận metric và access log đã che bí mật."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        t0 = time.perf_counter()
        status_code = 500
        response_started = False
        method = str(scope.get("method", "GET")).upper()
        raw_path = str(scope.get("path", ""))
        redacted_qs = redact_query_string(scope.get("query_string", b""))
        display_path = f"{raw_path}?{redacted_qs}" if redacted_qs else raw_path

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                state = scope.get("state")
                raw_req_id = state.get("request_id") if isinstance(state, Mapping) else None
                request_id_for_log = (
                    raw_req_id
                    if isinstance(raw_req_id, str) and raw_req_id
                    else get_current_request_id()
                )
                logger.exception(
                    "Unhandled exception [request_id=%s]",
                    request_id_for_log,
                    exc_info=exc,
                )
                if response_started:
                    raise
                await _send_json_error(
                    scope,
                    receive,
                    send_wrapper,
                    status_code=500,
                    code="INTERNAL_ERROR",
                    message="Đã xảy ra lỗi hệ thống ngoài dự kiến.",
                    next_action="retry_later",
                    retryable=True,
                )
        finally:
            duration_seconds = time.perf_counter() - t0
            duration_ms = round(duration_seconds * 1000.0, 2)
            route_template = _resolve_route_template(scope)
            state = scope.get("state")
            raw_req_id = state.get("request_id") if isinstance(state, Mapping) else None
            request_id = (
                raw_req_id
                if isinstance(raw_req_id, str) and raw_req_id
                else (get_current_request_id() or "-")
            )
            record_http_observation(
                method=method,
                route_template=route_template,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            _access_logger.info(
                "request_id=%s method=%s path=%s route_template=%s status_code=%d duration_ms=%.2f",
                request_id,
                method,
                display_path,
                route_template,
                status_code,
                duration_ms,
            )
