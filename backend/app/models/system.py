from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class CommandReceipt(OwnedMixin, RecordMixin, Base):
    __tablename__ = "command_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "command_id"),
        CheckConstraint("length(payload_hash) = 64", name="hash_length"),
    )
    command_id: Mapped[str] = mapped_column(String(128))
    command_type: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    expected_revision: Mapped[int | None]
    state: Mapped[str] = mapped_column(String(32))
    result_reference: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditEvent(OwnedMixin, RecordMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)
    event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    resource_ref: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    command_receipt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("command_receipts.id", ondelete="RESTRICT")
    )
    command_id: Mapped[str | None] = mapped_column(String(128))
    duration_source: Mapped[str | None] = mapped_column(String(32))
    resource_revision: Mapped[int | None]


class Reminder(OwnedMixin, RecordMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    schedule_revision: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    fire_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    delivery_state: Mapped[str] = mapped_column(String(32), default="scheduled")
    user_action: Mapped[str | None] = mapped_column(String(32))
    device_id: Mapped[str | None] = mapped_column(String(128))
