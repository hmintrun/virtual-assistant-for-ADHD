"""Async SQLAlchemy setup shared by API services and Alembic."""

from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base


class DatabaseSettings(BaseSettings):
    """Read the local URL or DATABASE_URL without exposing credentials."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )
    database_url: str = "sqlite+aiosqlite:///./proplan.db"


def make_engine(url: str) -> AsyncEngine:
    """Create a portable async engine; enforce foreign keys on SQLite."""

    engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


@lru_cache
def get_engine() -> AsyncEngine:
    return make_engine(DatabaseSettings().database_url)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provide one session per request; business services commit explicitly."""

    async with session_factory(get_engine())() as session:
        yield session


__all__ = ["Base", "DatabaseSettings", "get_db", "get_engine", "make_engine", "session_factory"]
