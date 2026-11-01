from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class RescheduleProposal(OwnedMixin, RecordMixin, Base):
    __tablename__ = "reschedule_proposals"
    __table_args__ = (
        Index(
            "uq_user_pending_proposal",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )
    trigger_reason: Mapped[str] = mapped_column(String(32))
    target_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"))
    allowed_task_ids: Mapped[list[str]] = mapped_column(JSON)
    affected_task_ids: Mapped[list[str]] = mapped_column(JSON)
    base_schedule_revision: Mapped[int]
    source_revisions: Mapped[dict[str, Any]] = mapped_column(JSON)
    proposal_revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    supersedes_proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reschedule_proposals.id", ondelete="RESTRICT")
    )
    proposed_changes: Mapped[dict[str, Any]] = mapped_column(JSON)
    feasibility: Mapped[dict[str, Any]] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    generation_tier: Mapped[str | None] = mapped_column(String(32))
    pending_notification_state: Mapped[str] = mapped_column(String(32), default="not_sent")
    pending_notification_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
