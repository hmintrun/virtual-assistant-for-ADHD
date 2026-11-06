from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class DeviceState(OwnedMixin, RecordMixin, Base):
    __tablename__ = "device_states"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id"),
        CheckConstraint("device_report_revision >= 1", name="report_revision_positive"),
        CheckConstraint(
            "last_schedule_revision IS NULL OR last_schedule_revision >= 0",
            name="schedule_revision_nonnegative",
        ),
    )
    device_id: Mapped[str] = mapped_column(String(128))
    device_report_revision: Mapped[int]
    notification_permission: Mapped[str] = mapped_column(String(32))
    microphone_permission: Mapped[str] = mapped_column(String(32))
    dnd_permission: Mapped[str] = mapped_column(String(32))
    alarm_capability: Mapped[str] = mapped_column(String(32))
    active_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("session_runs.id", ondelete="RESTRICT")
    )
    last_schedule_revision: Mapped[int | None]
    dnd_owner_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("session_runs.id", ondelete="RESTRICT")
    )
    dnd_apply_state: Mapped[str] = mapped_column(String(32))
    dnd_restore_state: Mapped[str] = mapped_column(String(32))
    reported_at: Mapped[datetime] = mapped_column(UTCDateTime())
    app_visibility: Mapped[str] = mapped_column(String(32))
