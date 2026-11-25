"""W06: nhận trạng thái thiết bị thực và lời nhắc theo Session còn hiệu lực."""

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
from app.models.system import AuditEvent, Reminder
from app.models.user import UserPreference
from app.repositories import device_repository, run_repository
from app.schemas.execution import (
    DeviceStateData,
    DeviceStateReportRequest,
    DeviceTelemetryData,
    DeviceTelemetryRequest,
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


def _ensure_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


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
    if payload.reported_at.astimezone(UTC) > server_now + timedelta(seconds=300):
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
        pref_rev = pref.effective_revision if pref is not None else 0
        offsets = list(pref.reminder_offsets) if pref is not None else [900, 600, 300]
        if (
            not 1 <= len(offsets) <= 3
            or any(type(x) is not int or x < 0 for x in offsets)
            or len(set(offsets)) != len(offsets)
        ):
            raise _error("VALIDATION_ERROR", "Các mốc nhắc đã lưu không hợp lệ.")
        offset_set = set(offsets)
        pref_token = f":pref:{pref_rev}:"
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
            stale_pref = False
            if (
                reminder.kind == "before_session"
                and ":pref:" in reminder.dedupe_key
                and pref_token not in reminder.dedupe_key
            ):
                actual_offset = round(
                    (
                        _ensure_aware(session.scheduled_start) - _ensure_aware(reminder.fire_at)
                    ).total_seconds()
                )
                if actual_offset not in offset_set:
                    stale_pref = True
            if (
                session.status != "scheduled"
                or reminder.schedule_revision != session.schedule_revision
                or stale_pref
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
        active_or_delivered_slots = {
            (r.session_id, r.schedule_revision, _ensure_aware(r.fire_at))
            for r in existing
            if r.kind == "before_session" and r.delivery_state != "cancelled"
        }
        identity = sha256(device_id.encode()).hexdigest()
        now = _now()
        from_utc = _ensure_aware(from_time)
        to_utc = _ensure_aware(to_time)
        for session in sessions:
            s_start_utc = _ensure_aware(session.scheduled_start)
            for offset in offsets:
                fire_at = s_start_utc - timedelta(seconds=offset)
                if not from_utc <= fire_at <= to_utc or fire_at < now:
                    continue
                key = (
                    f"session:{session.id}:rev:{session.schedule_revision}"
                    f":pref:{pref_rev}:offset:{offset}:device:{identity}"
                )
                if (
                    key in keys
                    or (session.id, session.schedule_revision, fire_at)
                    in active_or_delivered_slots
                ):
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
                active_or_delivered_slots.add((session.id, session.schedule_revision, fire_at))
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
    if payload.occurred_at.astimezone(UTC) > server_now + timedelta(seconds=300):
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
    if row.delivery_state in ("dismissed", "acknowledged", "cancelled") or session.status != "scheduled":
        if row.delivery_state == payload.delivery_state and row.user_action == payload.user_action:
            return _reminder_data(row)
        raise _error(
            "INVALID_REMINDER_STATE",
            "Lời nhắc đã ở trạng thái kết thúc (dismissed/acknowledged/cancelled) hoặc phiên đã bắt đầu.",
            status=409,
            next_action="ignore_or_reload",
        )
    if (
        row.delivery_state in ("delivered", "fired", "snoozed", "dismissed", "acknowledged")
        and payload.delivery_state == "scheduled"
    ):
        raise _error("INVALID_REMINDER_STATE", "Không thể quay lại trạng thái scheduled.", status=409)
    if (
        row.delivery_state in ("fired", "snoozed", "dismissed", "acknowledged")
        and payload.delivery_state == "delivered"
    ):
        raise _error("INVALID_REMINDER_STATE", "Không thể lùi trạng thái về delivered.", status=409)
    if row.device_id is not None and row.device_id != payload.device_id:
        raise _error("DEVICE_CONFLICT", "Lời nhắc thuộc thiết bị khác.", status=409)
    if payload.delivery_state not in DELIVERY_STATES or payload.user_action not in USER_ACTIONS:
        raise _error("VALIDATION_ERROR", "Biên nhận lời nhắc không hợp lệ.")
    row.delivery_state = payload.delivery_state
    row.user_action = payload.user_action
    row.device_id = payload.device_id
    await db.flush()
    return _reminder_data(row)


async def _resolve_existing_telemetry(
    db: AsyncSession,
    owner: UUID,
    device_id: str,
    payload: DeviceTelemetryRequest,
    normalized_source_run_id: str | None,
) -> DeviceTelemetryData | None:
    """Kiểm tra (user_id, event_id) đã tồn tại trong AuditEvent để trả về idempotent hoặc 409 xung đột."""
    existing = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.user_id == owner, AuditEvent.event_id == payload.event_id)
        .execution_options(populate_existing=True)
    )
    if existing is None:
        return None
    ref = existing.resource_ref or {}
    existing_occ = (
        existing.occurred_at
        if existing.occurred_at.tzinfo is not None
        else existing.occurred_at.replace(tzinfo=UTC)
    )
    if (
        existing.event_type == payload.event_type
        and ref.get("device_id") == device_id
        and ref.get("error_code") == payload.error_code
        and ref.get("attempt_count") == payload.attempt_count
        and ref.get("source_run_id") == normalized_source_run_id
        and ref.get("app_version") == payload.app_version
        and ref.get("platform_os") == payload.platform_os
        and existing_occ.astimezone(UTC) == payload.occurred_at.astimezone(UTC)
    ):
        return DeviceTelemetryData(
            audit_event_id=str(existing.id),
            device_id=device_id,
            event_id=existing.event_id,
            event_type=existing.event_type,
            error_code=ref["error_code"],
            received_at=existing.received_at,
        )
    raise _error(
        "CLIENT_EVENT_CONFLICT",
        "Mã sự kiện đã được dùng cho thông tin khác.",
        status=409,
        next_action="use_new_event_id",
    )


@command_service.durable_command(
    "record_device_telemetry", DeviceTelemetryData, resource_arg="device_id"
)
async def record_device_telemetry(
    db: AsyncSession,
    *,
    user_id: str,
    device_id: str,
    idempotency_key: str,
    payload: DeviceTelemetryRequest,
) -> DeviceTelemetryData:
    """Ghi nhận telemetry lỗi thiết bị (Zero-PII, Zero-AI) vào AuditEvent."""
    owner = command_service.owner_uuid(user_id)
    server_now = _now()
    if payload.occurred_at.astimezone(UTC) > server_now + timedelta(seconds=300):
        raise _error(
            "VALIDATION_ERROR",
            "Thời điểm sự kiện vượt quá giới hạn sai lệch đồng hồ cho phép.",
        )
    normalized_source_run_id: str | None = None
    if payload.source_run_id is not None:
        source_uuid = _uuid(payload.source_run_id)
        if await run_repository.get_run(db, owner, source_uuid) is None:
            raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy phiên nguồn.", status=404)
        normalized_source_run_id = str(source_uuid)

    matched = await _resolve_existing_telemetry(
        db, owner, device_id, payload, normalized_source_run_id
    )
    if matched is not None:
        return matched

    row = AuditEvent(
        user_id=owner,
        event_id=payload.event_id,
        event_type=payload.event_type,
        resource_ref={
            "device_id": device_id,
            "error_code": payload.error_code,
            "attempt_count": payload.attempt_count,
            "source_run_id": normalized_source_run_id,
            "app_version": payload.app_version,
            "platform_os": payload.platform_os,
        },
        occurred_at=payload.occurred_at.astimezone(UTC),
        received_at=server_now,
        command_id=idempotency_key,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        matched = await _resolve_existing_telemetry(
            db, owner, device_id, payload, normalized_source_run_id
        )
        if matched is not None:
            return matched
        raise

    return DeviceTelemetryData(
        audit_event_id=str(row.id),
        device_id=device_id,
        event_id=row.event_id,
        event_type=payload.event_type,
        error_code=payload.error_code,
        received_at=row.received_at,
    )
