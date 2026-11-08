from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class SessionRun(OwnedMixin, RecordMixin, Base):
    __tablename__ = "session_runs"
    __table_args__ = (
        CheckConstraint(
            "work_elapsed_seconds >= 0 AND rest_elapsed_seconds >= 0", name="elapsed_nonnegative"
        ),
        CheckConstraint(
            "actual_work_seconds IS NULL OR actual_work_seconds >= 0", name="actual_nonnegative"
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="time_order"),
        CheckConstraint("segment_index IS NULL OR segment_index >= 0", name="segment_nonnegative"),
        Index(
            "uq_user_live_run",
            "user_id",
            unique=True,
            sqlite_where=text("run_state IN ('running', 'paused')"),
            postgresql_where=text("run_state IN ('running', 'paused')"),
        ),
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    device_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    run_state: Mapped[str] = mapped_column(String(32), default="running")
    active_phase: Mapped[str] = mapped_column(String(32), default="work")
    segment_index: Mapped[int | None] = mapped_column(default=0)
    work_elapsed_seconds: Mapped[int] = mapped_column(default=0)
    rest_elapsed_seconds: Mapped[int] = mapped_column(default=0)
    actual_work_seconds: Mapped[int | None]
    actual_source: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32), default="unknown")
    checkin_timeout_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    dnd_release_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CheckIn(OwnedMixin, RecordMixin, Base):
    __tablename__ = "check_ins"
    __table_args__ = (
        CheckConstraint("response_revision >= 0", name="response_revision_nonnegative"),
        CheckConstraint(
            "response_state IN ('open', 'answered', 'no_respond')",
            name="response_state_valid",
        ),
        Index(
            "uq_run_final_checkin",
            "run_id",
            unique=True,
            sqlite_where=text("kind = 'final'"),
            postgresql_where=text("kind = 'final'"),
        ),
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("session_runs.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    response_state: Mapped[str] = mapped_column(String(32), default="open")
    session_outcome: Mapped[str] = mapped_column(String(32), default="unknown")
    step_completed: Mapped[bool | None]
    task_completed: Mapped[bool | None]
    skip_scope: Mapped[str | None] = mapped_column(String(32))
    remaining_note: Mapped[str | None] = mapped_column(Text)
    asked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    response_revision: Mapped[int] = mapped_column(default=0)


class RunEvent(OwnedMixin, RecordMixin, Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("user_id", "client_event_id"),)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("session_runs.id", ondelete="RESTRICT"), index=True
    )
    client_event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ActualCorrection(OwnedMixin, RecordMixin, Base):
    __tablename__ = "actual_corrections"
    __table_args__ = (CheckConstraint("actual_work_seconds >= 0", name="actual_nonnegative"),)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("session_runs.id", ondelete="RESTRICT"), index=True
    )
    previous_actual_work_seconds: Mapped[int | None]
    previous_actual_source: Mapped[str | None] = mapped_column(String(32))
    actual_work_seconds: Mapped[int]
    actual_source: Mapped[str] = mapped_column(String(32), default="self_report")
    reason: Mapped[str | None] = mapped_column(Text)
