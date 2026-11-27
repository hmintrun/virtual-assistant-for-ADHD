from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, RecordMixin, UTCDateTime

if TYPE_CHECKING:
    from app.models.step import Step
    from app.models.user import User


class Task(RecordMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (CheckConstraint("avoidance_count >= 0", name="avoidance_nonnegative"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    extra_requirements: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    time_constraint: Mapped[dict[str, Any]] = mapped_column(JSON)
    importance: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # FK một chiều: không tạo confirmed_task_id ngược trên Draft.
    source_draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("planning_drafts.id", ondelete="RESTRICT"), unique=True
    )
    availability_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    source_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    # Giờ có hiệu lực tách khỏi bản yêu cầu ban đầu trong source_summary.
    availability: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    source_summary_revision: Mapped[int | None]
    source_planning_input_revision: Mapped[int | None]
    decomposition_state: Mapped[str] = mapped_column(String(32), default="complete")
    unexpanded_scope: Mapped[str | None] = mapped_column(Text)
    extension_candidate_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    avoidance_count: Mapped[int] = mapped_column(default=0)
    user: Mapped[User] = relationship(back_populates="tasks", lazy="raise", foreign_keys=[user_id])
    steps: Mapped[list[Step]] = relationship(
        back_populates="task",
        order_by="Step.order_index",
        foreign_keys="Step.task_id",
        lazy="raise",
        passive_deletes="all",
    )
