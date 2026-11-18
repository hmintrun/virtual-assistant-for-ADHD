from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime

if TYPE_CHECKING:
    from app.models.task import Task


class User(RecordMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "current_streak_days >= 0 AND streak_freeze_tokens >= 0", name="streak_nonnegative"
        ),
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    current_streak_days: Mapped[int] = mapped_column(default=0)
    streak_frozen: Mapped[bool] = mapped_column(default=False)
    streak_freeze_tokens: Mapped[int] = mapped_column(default=2)
    last_streak_date: Mapped[date | None]
    preferences: Mapped[UserPreference | None] = relationship(
        back_populates="user",
        lazy="raise",
        passive_deletes="all",
        foreign_keys="UserPreference.user_id",
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="user", lazy="raise", passive_deletes="all", foreign_keys="Task.user_id"
    )


class UserPreference(RecordMixin, Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint(
            "work_interval_seconds > 0 AND rest_interval_seconds >= 0 AND buffer_multiplier >= 1 AND effective_revision >= 1",
            name="effective_valid",
        ),
        CheckConstraint(
            "requested_revision IS NULL OR requested_revision >= 1", name="requested_valid"
        ),
        CheckConstraint(
            "micro_task_seconds IS NULL OR micro_task_seconds >= 0", name="micro_valid"
        ),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)
    work_interval_seconds: Mapped[int] = mapped_column(default=1500)
    rest_interval_seconds: Mapped[int] = mapped_column(default=300)
    micro_task_seconds: Mapped[int | None] = mapped_column(default=120)
    reminder_offsets: Mapped[list[int]] = mapped_column(JSON, default=lambda: [900, 600, 300])
    requested_revision: Mapped[int | None]
    effective_revision: Mapped[int] = mapped_column(default=1)
    buffer_multiplier: Mapped[float] = mapped_column(default=1.2)
    reminder_style: Mapped[str] = mapped_column(String(32), default="gentle")
    requested_changes: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    # Cài đặt chờ duyệt không thay đổi cấu hình effective hoặc phiên đang chạy.
    pending_proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reschedule_proposals.id", ondelete="RESTRICT")
    )
    suggestion_cooldown_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    user: Mapped[User] = relationship(
        back_populates="preferences", lazy="raise", foreign_keys=[user_id]
    )


class RefreshToken(OwnedMixin, RecordMixin, Base):
    """Lưu vết jti refresh token phục vụ rotation, chặn reuse và /auth/logout (BE-03)."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint("length(jti) >= 1", name="jti_nonempty"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    )
    jti: Mapped[str] = mapped_column(String(128), unique=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    replaced_by_jti: Mapped[str | None] = mapped_column(String(128))
