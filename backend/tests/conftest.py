import socket

import pytest
from sqlalchemy import create_engine, event

from app.models import Base


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def offline_test_environment(monkeypatch):
    """Chặn provider thật; chỉ cho socket loopback phục vụ event loop/HTTP local."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "GOOGLE_CREDENTIALS_PATH"):
        monkeypatch.setenv(name, "")
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def is_local(address):
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}

    def connect(sock, address):
        if is_local(address):
            return original_connect(sock, address)
        raise AssertionError("Tests must not connect to external providers")

    def create_connection(address, *args, **kwargs):
        if is_local(address):
            return original_create_connection(address, *args, **kwargs)
        raise AssertionError("Tests must not connect to external providers")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
