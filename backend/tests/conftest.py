import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from app.models import Base


@pytest.fixture(autouse=True)
def offline_test_environment(monkeypatch):
    """Chỉ dùng DB thử nghiệm và provider giả; cấm kết nối mạng từ test."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    monkeypatch.setattr(settings, "TESTING", True)
    monkeypatch.setattr(settings, "ENABLE_CALENDAR_SYNC_WORKER", False)
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
