from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, RecordMixin

if TYPE_CHECKING:
    from app.models.session import Session
    from app.models.task import Task


class Step(RecordMixin, Base):
    __tablename__ = "steps"
    __table_args__ = (
        CheckConstraint(
            "order_index >= 0 AND estimated_base_seconds > 0 AND remaining_base_seconds >= 0 AND buffer_multiplier >= 1",
            name="estimate_valid",
        ),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    expected_output: Mapped[str | None] = mapped_column(Text)
    order_index: Mapped[int]
    estimated_base_seconds: Mapped[int]
    remaining_base_seconds: Mapped[int]
    buffer_multiplier: Mapped[float]
    estimate_source: Mapped[str] = mapped_column(String(32))
    cognitive_load: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    task: Mapped[Task] = relationship(back_populates="steps", lazy="raise", foreign_keys=[task_id])
    sessions: Mapped[list[Session]] = relationship(
        back_populates="step",
        order_by="Session.scheduled_start",
        foreign_keys="Session.step_id",
        lazy="raise",
        passive_deletes="all",
    )
