from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class Thought(OwnedMixin, RecordMixin, Base):
    __tablename__ = "thoughts"
    __table_args__ = (
        UniqueConstraint("user_id", "client_thought_id"),
        CheckConstraint("attempt_count BETWEEN 1 AND 5", name="attempt_limit"),
    )
    client_thought_id: Mapped[str] = mapped_column(String(128))
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("session_runs.id", ondelete="RESTRICT"), index=True
    )
    input_type: Mapped[str] = mapped_column(String(32))
    local_audio_path: Mapped[str | None] = mapped_column(Text)
    audio_storage_ref: Mapped[str | None] = mapped_column(Text)
    text_content: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int]
    capture_state: Mapped[str] = mapped_column(String(32))
    retention_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
