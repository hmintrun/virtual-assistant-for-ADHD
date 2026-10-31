from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class Availability(OwnedMixin, RecordMixin, Base):
    __tablename__ = "availabilities"
    __table_args__ = (UniqueConstraint("user_id"),)
    timezone: Mapped[str] = mapped_column(String(64))
    weekly_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    date_overrides: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    task_limits: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32))
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class SettingChangeRequest(OwnedMixin, RecordMixin, Base):
    __tablename__ = "setting_change_requests"
    application_state: Mapped[str] = mapped_column(String(48))
    requested_revision: Mapped[int | None]
    effective_revision: Mapped[int]
    requested_availability_revision: Mapped[int | None]
    effective_availability_revision: Mapped[int | None]
    requested_changes: Mapped[dict[str, Any]] = mapped_column(JSON)
    pending_proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reschedule_proposals.id", ondelete="RESTRICT")
    )


class AdaptationSuggestion(OwnedMixin, RecordMixin, Base):
    __tablename__ = "adaptation_suggestions"
    __table_args__ = (
        CheckConstraint("sample_count >= 0", name="sample_nonnegative"),
        UniqueConstraint("supersedes_suggestion_id"),
    )
    kind: Mapped[str] = mapped_column(String(32))
    sample_count: Mapped[int]
    current_value: Mapped[Any] = mapped_column(JSON)
    suggested_value: Mapped[Any] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    policy_revision: Mapped[str] = mapped_column(String(128))
    sample_refs: Mapped[list[str]] = mapped_column(JSON)
    model_revision: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    supersedes_suggestion_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("adaptation_suggestions.id", ondelete="RESTRICT")
    )
