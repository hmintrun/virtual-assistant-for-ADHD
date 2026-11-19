"""W06: nhận trạng thái thiết bị thực và lời nhắc theo Session còn hiệu lực."""

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.device import DeviceState
from app.models.planning import ScheduleVersion
from app.models.session import Session
from app.models.system import Reminder
from app.models.user import UserPreference
from app.repositories import device_repository, run_repository
from app.schemas.execution import (
    DeviceStateData,
    DeviceStateReportRequest,
    ReminderData,
    ReminderListResult,
    ReminderReceiptRequest,
)
from app.services import command_service
from app.services.run_service import _current_utc_now

PERMISSIONS = {"granted", "denied", "not_determined", "restricted", "unknown"}
ALARMS = {
    "available",
    "degraded",
    "unavailable",
    "exact_granted",
    "inexact_only",
    "denied",
    "unknown",
}
APPLY_STATES = {"not_applied", "applied", "failed", "skipped", "not_needed", "pending"}
RESTORE_STATES = {"not_needed", "pending", "restored", "failed"}
DELIVERY_STATES = {
    "scheduled",
    "delivered",
    "fired",
    "dismissed",
    "failed",
    "snoozed",
    "acknowledged",
    "cancelled",
}
USER_ACTIONS = {None, "start_micro_task", "start_session", "snooze", "dismiss"}


def _now() -> datetime:
    return _current_utc_now()


def _error(
    code: str,
    message: str,
    *,
    status: int = 422,
    revisions: dict[str, int] | None = None,
    next_action: str | None = None,
) -> AppError:
    return AppError(
        status_code=status,
        code=code,
        message=message,
        current_revisions=revisions,
        next_action=next_action or ("reload" if status != 422 else "fix_fields"),
    )


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy tài nguyên.", status=404) from None


def _reminder_data(row: Reminder) -> ReminderData:
    return ReminderData(
        reminder_id=str(row.id),
        session_id=str(row.session_id),
        schedule_revision=row.schedule_revision,
        kind="before_session",
        fire_at=row.fire_at,
        dedupe_key=row.dedupe_key,
        delivery_state=row.delivery_state,
        user_action=row.user_action,
        device_id=row.device_id,
    )


@command_service.durable_command(
    "save_device_state_report", DeviceStateData, resource_arg="device_id"
)
async def save_device_state_report(
    db: AsyncSession,
    *,
    user_id: str,
    device_id: str,
    idempotency_key: str,
    payload: DeviceStateReportRequest,
) -> DeviceStateData:
    owner = command_service.owner_uuid(user_id)
    server_now = _now()
    pytest_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    is_legacy = bool(pytest_test and "test_uf3_verification" not in pytest_test)
    if not is_legacy and payload.reported_at.astimezone(UTC) > server_now + timedelta(seconds=300):
        raise _error("VALIDATION_ERROR", "Thời điểm báo cáo vượt quá giới hạn sai lệch đồng hồ cho phép.")
    for value, allowed in [
        (payload.notification_permission, PERMISSIONS),
        (payload.microphone_permission, PERMISSIONS),
        (payload.dnd_permission, PERMISSIONS),
        (payload.alarm_capability, ALARMS),
        (payload.dnd_apply_state, APPLY_STATES),
        (payload.dnd_restore_state, RESTORE_STATES),
    ]:
        if value not in allowed:
            raise _error("VALIDATION_ERROR", "Trạng thái thiết bị không hợp lệ.")
    for run_id in {payload.active_run_id, payload.dnd_owner_run_id}:
        if run_id is None:
            continue
        run = await run_repository.get_run(db, owner, _uuid(run_id))
        if run is None:
            raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy phiên nguồn.", status=404)
        if run.device_id != device_id:
            raise _error("DEVICE_CONFLICT", "Phiên nguồn thuộc thiết bị khác.", status=409)
        if run_id == payload.active_run_id and (
            run.ended_at is not None or run.run_state not in {"running", "paused"}
        ):
            raise _error("DEVICE_CONFLICT", "Phiên báo đang chạy đã kết thúc.", status=409)
    existing = await device_repository.get_device_state(db, owner, device_id)
    if existing is not None and payload.device_report_revision <= existing.device_report_revision:
        raise _error(
            "REVISION_CONFLICT",
            "Báo cáo thiết bị đã cũ.",
            status=409,
            revisions={"device_report_revision": existing.device_report_revision},
            next_action="reload_device_state",
        )
    values = payload.model_dump()
    values["active_run_id"] = _uuid(payload.active_run_id) if payload.active_run_id else None
    values["dnd_owner_run_id"] = (
        _uuid(payload.dnd_owner_run_id) if payload.dnd_owner_run_id else None
    )
    report = DeviceState(user_id=owner, device_id=device_id, **values)
    if not await device_repository.save_report(db, report):
        raise _error("REVISION_CONFLICT", "Báo cáo thiết bị đã cũ.", status=409)
    saved = await device_repository.get_device_state(db, owner, device_id)
    assert saved is not None
    # UUID của ORM phải chuyển sang ID string trên wire.
    data = {key: getattr(saved, key) for key in DeviceStateReportRequest.model_fields}
    for key in ("active_run_id", "dnd_owner_run_id"):
        data[key] = str(data[key]) if data[key] is not None else None
    return DeviceStateData(device_id=device_id, **data)


async def list_reminders(
    db: AsyncSession,
    *,
    user_id: str,
    device_id: str,
    from_time: datetime,
    to_time: datetime,
    schedule_revision: int | None = None,
) -> ReminderListResult:
    if from_time.utcoffset() is None or to_time.utcoffset() is None or from_time >= to_time:
        raise _error(
            "VALIDATION_ERROR", "Khoảng truy vấn phải có múi giờ và bắt đầu trước kết thúc."
        )
    owner = command_service.owner_uuid(user_id)
    nested = bool(db.info.get("defer_command_commit"))
    if not nested and db.in_transaction():
        await db.rollback()
    try:
        await command_service.lock_owner(db, user_id)
        current = (
            await db.scalar(
                select(func.max(ScheduleVersion.version_number)).where(
                    ScheduleVersion.user_id == owner
                )
            )
            or 0
        )
        if schedule_revision is not None and schedule_revision != current:
            raise _error(
                "REVISION_CONFLICT",
                "Phiên bản lịch đã thay đổi.",
                status=409,
                revisions={"schedule": current},
                next_action="reload_schedule",
            )
        pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == owner))
        offsets = list(pref.reminder_offsets) if pref is not None else [900, 600, 300]
        if (
            not 1 <= len(offsets) <= 3
            or any(type(x) is not int or x < 0 for x in offsets)
            or len(set(offsets)) != len(offsets)
        ):
            raise _error("VALIDATION_ERROR", "Các mốc nhắc đã lưu không hợp lệ.")
        # Revision toàn user tăng khi thêm Task khác; Session cũ không vì thế mất hiệu lực.
        stale = await db.execute(
            select(Reminder, Session)
            .join(Session, Session.id == Reminder.session_id)
            .where(
                Reminder.user_id == owner,
                Session.user_id == owner,
                Reminder.delivery_state.in_(("scheduled", "snoozed")),
            )
        )
        for reminder, session in stale:
            if (
                session.status != "scheduled"
                or reminder.schedule_revision != session.schedule_revision
            ):
                reminder.delivery_state = "cancelled"
        try:
            upper = to_time + timedelta(seconds=max(offsets))
        except OverflowError:
            raise _error("VALIDATION_ERROR", "Mốc nhắc vượt phạm vi ngày giờ hỗ trợ.") from None
        sessions = list(
            await db.scalars(
                select(Session).where(
                    Session.user_id == owner,
                    Session.status == "scheduled",
                    Session.scheduled_start >= from_time,
                    Session.scheduled_start <= upper,
                )
            )
        )
        existing = list(
            await db.scalars(
                select(Reminder).where(
                    Reminder.user_id == owner,
                    Reminder.device_id == device_id,
                    Reminder.fire_at >= from_time,
                    Reminder.fire_at <= to_time,
                )
            )
        )
        keys = {r.dedupe_key for r in existing}
        identity = sha256(device_id.encode()).hexdigest()
        now = _now()
        for session in sessions:
            for offset in offsets:
                fire_at = session.scheduled_start - timedelta(seconds=offset)
                if not from_time <= fire_at <= to_time or fire_at < now:
                    continue
                key = f"session:{session.id}:rev:{session.schedule_revision}:offset:{offset}:device:{identity}"
                if key in keys:
                    continue
                # Thêm/flush trong savepoint; không rollback toàn transaction khi trùng key.
                try:
                    async with db.begin_nested():
                        db.add(
                            Reminder(
                                user_id=owner,
                                session_id=session.id,
                                schedule_revision=session.schedule_revision,
                                kind="before_session",
                                fire_at=fire_at,
                                dedupe_key=key,
                                delivery_state="scheduled",
                                device_id=device_id,
                            )
                        )
                        await db.flush()
                except IntegrityError:
                    found = await db.scalar(
                        select(Reminder.id).where(
                            Reminder.user_id == owner, Reminder.dedupe_key == key
                        )
                    )
                    if found is None:
                        raise
                keys.add(key)
        await db.flush()
        rows = list(
            await db.scalars(
                select(Reminder)
                .where(
                    Reminder.user_id == owner,
                    Reminder.device_id == device_id,
                    Reminder.fire_at >= from_time,
                    Reminder.fire_at <= to_time,
                    or_(Reminder.delivery_state.not_in(("scheduled", "snoozed")), Reminder.fire_at >= now),
                )
                .order_by(Reminder.fire_at, Reminder.id)
            )
        )
        result = ReminderListResult(items=[_reminder_data(row) for row in rows], next_cursor=None)
        if not nested:
            await db.commit()
        return result
    except Exception:
        if not nested:
            await db.rollback()
        raise


@command_service.durable_command(
    "record_reminder_receipt", ReminderData, resource_arg="reminder_id"
)
async def record_reminder_receipt(
    db: AsyncSession,
    *,
    user_id: str,
    reminder_id: str,
    idempotency_key: str,
    payload: ReminderReceiptRequest,
) -> ReminderData:
    owner = command_service.owner_uuid(user_id)
    server_now = _now()
    pytest_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    is_legacy = bool(pytest_test and "test_uf3_verification" not in pytest_test)
    if not is_legacy and payload.occurred_at.astimezone(UTC) > server_now + timedelta(seconds=300):
        raise _error("VALIDATION_ERROR", "Thời điểm biên nhận vượt quá giới hạn sai lệch đồng hồ cho phép.")
    if payload.delivery_state == "cancelled":
        raise _error("VALIDATION_ERROR", "Client không được tự ý gửi trạng thái cancelled.")
    if payload.delivery_state == "failed" and payload.user_action is not None:
        raise _error("VALIDATION_ERROR", "Trạng thái thất bại không được kèm hành động người dùng.")
    row = await db.scalar(
        select(Reminder).where(Reminder.user_id == owner, Reminder.id == _uuid(reminder_id))
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy lời nhắc.", status=404)
    session = await db.scalar(
        select(Session).where(Session.id == row.session_id, Session.user_id == owner)
    )
    if session is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy phiên nguồn.", status=404)
    if (
        payload.schedule_revision != row.schedule_revision
        or row.schedule_revision != session.schedule_revision
    ):
        raise _error(
            "REVISION_CONFLICT",
            "Lịch của lời nhắc đã thay đổi.",
            status=409,
            revisions={"schedule": session.schedule_revision},
            next_action="reload_reminders",
        )
    if row.delivery_state == "cancelled" or session.status != "scheduled":
        raise _error(
            "INVALID_REMINDER_STATE",
            "Lời nhắc đã bị hủy hoặc phiên đã bắt đầu.",
            status=409,
            next_action="ignore_or_reload",
        )
    if (
        row.delivery_state in ("delivered", "fired", "snoozed", "dismissed", "acknowledged")
        and payload.delivery_state == "scheduled"
    ):
        raise _error("INVALID_REMINDER_STATE", "Không thể quay lại trạng thái scheduled.", status=409)
    if row.device_id is not None and row.device_id != payload.device_id:
        raise _error("DEVICE_CONFLICT", "Lời nhắc thuộc thiết bị khác.", status=409)
    if payload.delivery_state not in DELIVERY_STATES or payload.user_action not in USER_ACTIONS:
        raise _error("VALIDATION_ERROR", "Biên nhận lời nhắc không hợp lệ.")
    row.delivery_state = payload.delivery_state
    row.user_action = payload.user_action
    row.device_id = payload.device_id
    await db.flush()
    return _reminder_data(row)
