"""Persist the latest report. Service validates enums, Run/device ownership and retries."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import DeviceState


async def get_device_state(db: AsyncSession, user_id: UUID, device_id: str) -> DeviceState | None:
    return await db.scalar(
        select(DeviceState)
        .where(DeviceState.user_id == user_id, DeviceState.device_id == device_id)
        .execution_options(populate_existing=True)
    )


async def save_report(db: AsyncSession, report: DeviceState) -> bool:
    """Service khóa owner trước khi gọi, rồi commit cùng receipt. Báo cáo cũ trả False."""
    existing = await get_device_state(db, report.user_id, report.device_id)
    if existing is None:
        db.add(report)
    else:
        if report.device_report_revision <= existing.device_report_revision:
            return False
        for field in (
            "device_report_revision",
            "notification_permission",
            "microphone_permission",
            "dnd_permission",
            "alarm_capability",
            "active_run_id",
            "last_schedule_revision",
            "dnd_owner_run_id",
            "dnd_apply_state",
            "dnd_restore_state",
            "reported_at",
            "app_visibility",
        ):
            setattr(existing, field, getattr(report, field))
    await db.flush()
    return True
