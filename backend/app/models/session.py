from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, RecordMixin, UTCDateTime

if TYPE_CHECKING:
    from app.models.step import Step


class Session(RecordMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("scheduled_end > scheduled_start", name="time_order"),
        CheckConstraint(
            "work_seconds > 0 AND rest_seconds >= 0 AND (micro_task_seconds IS NULL OR micro_task_seconds >= 0)",
            name="durations_valid",
        ),
        CheckConstraint(
            "duration_seconds = work_seconds + rest_seconds",
            name="duration_sum",
        ),
        CheckConstraint("schedule_revision >= 1", name="schedule_positive"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    step_id: Mapped[UUID] = mapped_column(ForeignKey("steps.id", ondelete="RESTRICT"), index=True)
    scheduled_start: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    scheduled_end: Mapped[datetime] = mapped_column(UTCDateTime())
    micro_task_text: Mapped[str | None] = mapped_column(Text)
    micro_task_seconds: Mapped[int | None]
    work_seconds: Mapped[int]
    rest_seconds: Mapped[int]
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    duration_seconds: Mapped[int]
    schedule_revision: Mapped[int]
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    start_state: Mapped[str] = mapped_column(String(32), default="waiting")
    start_decision_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    step: Mapped[Step] = relationship(
        back_populates="sessions", lazy="raise", foreign_keys=[step_id]
    )
