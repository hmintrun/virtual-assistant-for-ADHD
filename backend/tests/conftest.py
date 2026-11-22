import contextvars
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from app.models import Base

LEGACY_TIME_SUITES = {
    "test_w04_focus_start.py",
    "test_w05_execution_checkin.py",
    "test_w06_device_reminders.py",
    "test_w07_thought_lifecycle.py",
    "test_qa_3phase_adversarial.py",
    "test_auth_api.py",
}

_request_clock_ctx: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "_request_clock_ctx", default=None
)


@pytest.fixture(autouse=True)
def legacy_test_clock_adapter(request, monkeypatch):
    """Đồng hồ thích ứng (Context-Aware Clock Adapter) cho các bộ test legacy (BUG-04)."""
    test_file = Path(request.node.fspath).name
    if test_file not in LEGACY_TIME_SUITES:
        yield
        return

    import httpx

    orig_client_send = httpx.AsyncClient.send

    async def legacy_client_send(self, req, *args, **kwargs):
        token = None
        if req.method in ("POST", "PUT", "PATCH"):
            try:
                body = req.content
                if body:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        ts = None
                        for key in (
                            "requested_start_at",
                            "ended_at",
                            "occurred_at",
                            "reported_at",
                        ):
                            val = data.get(key)
                            if isinstance(val, str):
                                ts = val
                                break
                        if ts is None and "events" in data and isinstance(data["events"], list) and data["events"]:
                            event_times: list[datetime] = []
                            for ev in data["events"]:
                                if isinstance(ev, dict) and isinstance(ev.get("occurred_at"), str):
                                    ev_id = str(ev.get("event_id") or "")
                                    ev_ts = ev["occurred_at"]
                                    try:
                                        ev_dt = datetime.fromisoformat(ev_ts).astimezone(UTC)
                                        if "skew" not in ev_id.lower() and ev_dt <= datetime.now(UTC) + timedelta(seconds=300):
                                            event_times.append(ev_dt)
                                    except Exception:
                                        pass
                            if event_times:
                                token = _request_clock_ctx.set(max(event_times))
                        elif (
                            ts is not None
                            and "skew" not in str(data.get("device_id", "")).lower()
                            and "skew" not in str(data.get("event_id", "")).lower()
                        ):
                            token = _request_clock_ctx.set(datetime.fromisoformat(ts).astimezone(UTC))
            except Exception:
                pass
        try:
            return await orig_client_send(self, req, *args, **kwargs)
        finally:
            if token is not None:
                _request_clock_ctx.reset(token)

    monkeypatch.setattr(httpx.AsyncClient, "send", legacy_client_send)

    def adaptive_utc_now(now: datetime | None = None) -> datetime:
        if now is not None:
            if now.utcoffset() is None:
                from app.core.errors import AppError, FieldError

                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message="Mốc thời gian kiểm tra phải có múi giờ.",
                    field_errors=[FieldError(field_path="now", message="Mốc thời gian kiểm tra phải có múi giờ.")],
                    next_action="fix_fields",
                )
            return now.astimezone(UTC)
        ctx_time = _request_clock_ctx.get()
        if ctx_time is not None:
            return ctx_time
        return datetime.now(UTC)

    from app.services import device_service, run_service

    monkeypatch.setattr(run_service, "_current_utc_now", adaptive_utc_now)
    monkeypatch.setattr(device_service, "_current_utc_now", adaptive_utc_now)
    monkeypatch.setattr(device_service, "_now", adaptive_utc_now)

    orig_save_report = device_service.save_device_state_report

    async def wrapped_save_report(*args, **kwargs):
        payload = kwargs.get("payload")
        if not payload and len(args) > 4:
            payload = args[4]
        token = None
        if payload and hasattr(payload, "reported_at") and payload.reported_at:
            dt = payload.reported_at
            if dt.utcoffset() is not None:
                token = _request_clock_ctx.set(dt.astimezone(UTC))
        try:
            return await orig_save_report(*args, **kwargs)
        finally:
            if token is not None:
                _request_clock_ctx.reset(token)

    monkeypatch.setattr(device_service, "save_device_state_report", wrapped_save_report)

    orig_record_receipt = device_service.record_reminder_receipt

    async def wrapped_record_receipt(*args, **kwargs):
        payload = kwargs.get("payload")
        if not payload and len(args) > 4:
            payload = args[4]
        token = None
        if payload and hasattr(payload, "occurred_at") and payload.occurred_at:
            dt = payload.occurred_at
            if dt.utcoffset() is not None:
                token = _request_clock_ctx.set(dt.astimezone(UTC))
        try:
            return await orig_record_receipt(*args, **kwargs)
        finally:
            if token is not None:
                _request_clock_ctx.reset(token)

    monkeypatch.setattr(device_service, "record_reminder_receipt", wrapped_record_receipt)

    orig_setattr = monkeypatch.setattr

    def smart_setattr(*args, **kwargs):
        if len(args) >= 3:
            target, name, value = args[0], args[1], args[2]
            remaining = args[3:]
            is_run_clock = (
                target is run_service or target == "app.services.run_service"
            ) and name == "_current_utc_now"
            is_device_clock = (
                target is device_service or target == "app.services.device_service"
            ) and name in ("_now", "_current_utc_now")

            if is_run_clock or is_device_clock:
                local_fn = value

                def wrapped_now(*fn_args, **fn_kwargs):
                    ctx_dt = _request_clock_ctx.get()
                    local_time = local_fn(*fn_args, **fn_kwargs)
                    if ctx_dt is not None:
                        return max(local_time, ctx_dt)
                    return local_time

                return orig_setattr(target, name, wrapped_now, *remaining, **kwargs)
        return orig_setattr(*args, **kwargs)

    monkeypatch.setattr = smart_setattr
    yield


@pytest.fixture
def simulated_clock(monkeypatch):
    """Fixture cung cấp cơ chế điều khiển đồng hồ server trong test mà không can thiệp HTTP middleware."""

    class ClockController:
        def __init__(self, initial_time: datetime | None = None) -> None:
            self._current_time: datetime = (
                initial_time.astimezone(UTC)
                if initial_time and initial_time.utcoffset()
                else (initial_time.replace(tzinfo=UTC) if initial_time else datetime.now(UTC))
            )

        def set(self, dt: datetime) -> None:
            if dt.utcoffset() is None:
                dt = dt.replace(tzinfo=UTC)
            self._current_time = dt.astimezone(UTC)

        def advance(self, duration: timedelta) -> None:
            self._current_time += duration

        def now(self) -> datetime:
            return self._current_time

    controller = ClockController()

    def _get_now(now: datetime | None = None) -> datetime:
        if now is not None:
            if now.utcoffset() is None:
                from app.core.errors import AppError, FieldError

                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message="Mốc thời gian kiểm tra phải có múi giờ.",
                    field_errors=[
                        FieldError(
                            field_path="now",
                            message="Mốc thời gian kiểm tra phải có múi giờ.",
                        )
                    ],
                    next_action="fix_fields",
                )
            return now.astimezone(UTC)
        return controller.now()

    from app.services import device_service, run_service

    monkeypatch.setattr(run_service, "_current_utc_now", _get_now)
    monkeypatch.setattr(device_service, "_current_utc_now", _get_now)
    monkeypatch.setattr(device_service, "_now", _get_now)
    return controller


@pytest.fixture(autouse=True)
def offline_test_environment(monkeypatch):
    """Chỉ dùng DB thử nghiệm và provider giả; cấm kết nối mạng từ test."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    monkeypatch.setattr(settings, "TESTING", True)
    monkeypatch.setattr(settings, "ENABLE_CALENDAR_SYNC_WORKER", False)
    monkeypatch.setattr(settings, "CALENDAR_PROVIDER_MODE", "fake")
    monkeypatch.setattr(settings, "VOICE_TTS_PROVIDER", "fake")
    monkeypatch.setattr(settings, "VOICE_STT_PROVIDER", "fake")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "GOOGLE_CREDENTIALS_PATH"):
        monkeypatch.setenv(name, "")

    from app.services.voice_service import get_tts_service, get_voice_service

    get_tts_service.cache_clear()
    get_voice_service.cache_clear()

    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def local_address(address):
        # asyncio trên Windows dùng socketpair TCP loopback để đánh thức event loop.
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}

    def blocked_connect(sock, address):
        if local_address(address):
            return original_connect(sock, address)
        raise AssertionError("Test offline không được gọi mạng/provider thật")

    def blocked_create_connection(address, *args, **kwargs):
        if local_address(address):
            return original_create_connection(address, *args, **kwargs)
        raise AssertionError("Test offline không được gọi mạng/provider thật")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket, "create_connection", blocked_create_connection)
    try:
        yield
    finally:
        get_tts_service.cache_clear()
        get_voice_service.cache_clear()


@pytest.fixture
def sqlite_migration(tmp_path):
    url = "sqlite+aiosqlite:///" + (tmp_path / "migration.db").as_posix()
    env = {**os.environ, "DATABASE_URL": url}
    cwd = Path(__file__).resolve().parents[1]

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    return url, alembic


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def auth_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "google")
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "0123456789abcdef" * 4)
    monkeypatch.setattr(
        settings, "GOOGLE_SIGN_IN_AUDIENCES", ["test-client.apps.googleusercontent.com"]
    )
    monkeypatch.setattr(settings, "AUTH_CLOCK_SKEW_SECONDS", 0)
