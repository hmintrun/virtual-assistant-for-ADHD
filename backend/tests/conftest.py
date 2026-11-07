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
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "GOOGLE_CREDENTIALS_PATH"):
        monkeypatch.setenv(name, "")

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
