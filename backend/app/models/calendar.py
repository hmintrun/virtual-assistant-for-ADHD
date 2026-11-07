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


class CalendarConnection(OwnedMixin, RecordMixin, Base):
    __tablename__ = "calendar_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", "account_ref"),)
    provider: Mapped[str] = mapped_column(String(32), default="google")
    account_ref: Mapped[str] = mapped_column(String(320))
    read_calendar_ids: Mapped[list[str]] = mapped_column(JSON)
    write_calendar_id: Mapped[str | None] = mapped_column(String(320))
    granted_scopes: Mapped[list[str]] = mapped_column(JSON)
    connection_state: Mapped[str] = mapped_column(String(32))
    credential_ref: Mapped[str] = mapped_column(Text)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CalendarEventLink(OwnedMixin, RecordMixin, Base):
    __tablename__ = "calendar_event_links"
    __table_args__ = (
        UniqueConstraint("connection_id", "google_calendar_id", "google_event_id"),
        Index(
            "uq_current_session_calendar_link",
            "session_id",
            "connection_id",
            "google_calendar_id",
            unique=True,
            sqlite_where=text("is_current = true"),
            postgresql_where=text("is_current = true"),
        ),
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("calendar_connections.id", ondelete="RESTRICT")
    )
    google_calendar_id: Mapped[str] = mapped_column(String(320))
    google_event_id: Mapped[str | None] = mapped_column(String(1024))
    google_etag: Mapped[str | None] = mapped_column(String(255))
    desired_schedule_revision: Mapped[int]
    synced_schedule_revision: Mapped[int | None]
    sync_state: Mapped[str] = mapped_column(String(32), default="pending")
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_current: Mapped[bool] = mapped_column(default=True)


class CalendarSyncJob(OwnedMixin, RecordMixin, Base):
    __tablename__ = "calendar_sync_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        Index("ix_calendar_job_queue", "job_state", "next_retry_at"),
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("calendar_connections.id", ondelete="RESTRICT")
    )
    event_link_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("calendar_event_links.id", ondelete="RESTRICT")
    )
    target_kind: Mapped[str] = mapped_column(String(32))
    target_ref: Mapped[str] = mapped_column(String(1024))
    operation: Mapped[str] = mapped_column(String(32))
    target_schedule_revision: Mapped[int | None]
    expected_google_etag: Mapped[str | None] = mapped_column(String(255))
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    job_state: Mapped[str] = mapped_column(String(32), default="queued")
    attempt_count: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PlanningContext(OwnedMixin, RecordMixin, Base):
    __tablename__ = "planning_contexts"
    __table_args__ = (
        CheckConstraint(
            "(availability_id IS NOT NULL AND availability_revision IS NOT NULL "
            "AND source_draft_id IS NULL AND source_summary_revision IS NULL AND availability_snapshot IS NULL) "
            "OR (availability_id IS NULL AND availability_revision IS NULL "
            "AND source_draft_id IS NOT NULL AND source_summary_revision IS NOT NULL "
            "AND source_summary_revision > 0 AND availability_snapshot IS NOT NULL)",
            name="context_source_exclusive",
        ),
    )
    availability_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("availabilities.id", ondelete="RESTRICT")
    )
    availability_revision: Mapped[int | None]
    source_draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("planning_drafts.id", ondelete="RESTRICT")
    )
    source_summary_revision: Mapped[int | None]
    availability_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    query_start: Mapped[datetime] = mapped_column(UTCDateTime())
    query_end: Mapped[datetime] = mapped_column(UTCDateTime())
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sync_state: Mapped[str] = mapped_column(String(32))
    coverage_state: Mapped[str] = mapped_column(String(32))
    selected_calendar_ids: Mapped[list[str]] = mapped_column(JSON)
    selected_calendar_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    busy_intervals: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    calendar_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    event_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    manual_busy_intervals: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    excluded_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CalendarPreview(OwnedMixin, RecordMixin, Base):
    __tablename__ = "calendar_previews"
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("calendar_connections.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(32))
    base_connection_revision: Mapped[int | None]
    required_proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reschedule_proposals.id", ondelete="RESTRICT")
    )
    source_revisions: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Snapshot chứa before/after, etag, recurrence_scope hoặc cấu hình keep/move.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CalendarAuthorization(OwnedMixin, RecordMixin, Base):
    __tablename__ = "calendar_authorizations"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    credential_ref: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
