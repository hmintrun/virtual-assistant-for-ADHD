"""Service quản lý Server-side Focus Streak, Auto-Freeze và 24h Repair (BE-UF6-04).

Sử dụng 4 cột sẵn có trên bảng `users` và bảng `audit_events` (0 Alembic migration mới).
Tương thích 100% SQLite và PostgreSQL 16 (lọc trường JSON `active` trong Python).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.execution import CheckIn, SessionRun
from app.models.settings import Availability as AvailabilityRow
from app.models.system import AuditEvent
from app.models.user import User
from app.schemas.streak import StreakData, StreakDayRecordData
from app.services import command_service

STREAK_UNLOCK_DAYS = 3
MIN_STREAK_FOCUS_SECONDS = 120
MAX_FREEZE_TOKENS = 2
MAX_REPAIRS_PER_MONTH = 2
DAY_RECORDS_WINDOW_DAYS = 35

STREAK_EVENT_DONE = "uf6_streak_day_earned"
STREAK_EVENT_FREEZE = "uf6_streak_frozen"
STREAK_EVENT_REPAIR = "uf6_streak_repaired"
_STREAK_EVENT_TYPES = (STREAK_EVENT_DONE, STREAK_EVENT_FREEZE, STREAK_EVENT_REPAIR)
_DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
_KIND_PRIORITY: dict[str, int] = {"freeze": 1, "repair": 2, "done": 3}


def _ensure_utc(dt: datetime | None = None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def _resolve_user_timezone(db: AsyncSession, owner: UUID) -> tuple[str, ZoneInfo]:
    tz_name = await db.scalar(
        select(AvailabilityRow.timezone)
        .where(AvailabilityRow.user_id == owner)
        .order_by(AvailabilityRow.revision.desc(), AvailabilityRow.created_at.desc())
        .limit(1)
    )
    candidate = (tz_name or _DEFAULT_TIMEZONE).strip() or _DEFAULT_TIMEZONE
    try:
        return candidate, ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC", ZoneInfo("UTC")


def _local_date_for_dt(dt: datetime, local_tz: ZoneInfo) -> date:
    return _ensure_utc(dt).astimezone(local_tz).date()


def _run_actual_seconds(run: SessionRun) -> int:
    if run.actual_work_seconds is not None:
        return int(run.actual_work_seconds)
    return int(run.work_elapsed_seconds or 0)


def _is_active_audit_event(ev: AuditEvent) -> bool:
    ref = ev.resource_ref if isinstance(ev.resource_ref, dict) else {}
    return bool(ref.get("active", True))


def _event_date(ev: AuditEvent) -> date | None:
    ref = ev.resource_ref if isinstance(ev.resource_ref, dict) else {}
    raw = ref.get("date")
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _event_month_key(ev: AuditEvent) -> str | None:
    ref = ev.resource_ref if isinstance(ev.resource_ref, dict) else {}
    raw = ref.get("month_key")
    if isinstance(raw, str) and raw:
        return raw
    ev_date = _event_date(ev)
    return ev_date.strftime("%Y-%m") if ev_date is not None else None


async def _load_all_streak_events(db: AsyncSession, owner: UUID) -> list[AuditEvent]:
    rows = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.user_id == owner,
                AuditEvent.event_type.in_(_STREAK_EVENT_TYPES),
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    return list(rows)


async def _upsert_audit_event(
    db: AsyncSession,
    *,
    owner: UUID,
    event_type: str,
    event_id: str,
    resource_ref: dict[str, Any],
    occurred_at: datetime,
    received_at: datetime,
) -> AuditEvent:
    existing = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.user_id == owner, AuditEvent.event_id == event_id)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        existing.event_type = event_type
        existing.resource_ref = dict(resource_ref)
        existing.occurred_at = occurred_at
        existing.received_at = received_at
        return existing
    row = AuditEvent(
        user_id=owner,
        event_id=event_id,
        event_type=event_type,
        resource_ref=dict(resource_ref),
        occurred_at=occurred_at,
        received_at=received_at,
    )
    db.add(row)
    return row


def _count_repairs_for_month(active_events: list[AuditEvent], month_key: str) -> int:
    return sum(
        1
        for ev in active_events
        if ev.event_type == STREAK_EVENT_REPAIR and _event_month_key(ev) == month_key
    )


async def _apply_reconcile_mutation(
    db: AsyncSession,
    user: User,
    *,
    today: date,
    now_utc: datetime,
    events: list[AuditEvent] | None = None,
) -> tuple[bool, bool]:
    """Đối soát khoảng trống ngày lỡ của user tại ngày địa phương `today`.

    Trả về `(changed, repair_pending)`.
    """
    if user.last_streak_date is None:
        return False, False
    gap = (today - user.last_streak_date).days
    if gap <= 1:
        return False, False

    if events is None:
        events = await _load_all_streak_events(db, user.id)
    active_events = [ev for ev in events if _is_active_audit_event(ev)]

    if gap == 2:
        yesterday = today - timedelta(days=1)
        if user.current_streak_days < STREAK_UNLOCK_DAYS:
            user.current_streak_days = 0
            user.last_streak_date = None
            user.streak_frozen = False
            user.updated_at = now_utc
            return True, False

        if user.streak_freeze_tokens > 0:
            user.streak_freeze_tokens = max(0, user.streak_freeze_tokens - 1)
            user.streak_frozen = True
            user.last_streak_date = yesterday
            user.updated_at = now_utc
            await _upsert_audit_event(
                db,
                owner=user.id,
                event_type=STREAK_EVENT_FREEZE,
                event_id=f"streak:freeze:{yesterday.isoformat()}",
                resource_ref={
                    "date": yesterday.isoformat(),
                    "tokens_remaining": user.streak_freeze_tokens,
                    "active": True,
                },
                occurred_at=now_utc,
                received_at=now_utc,
            )
            return True, False

        month_key = today.strftime("%Y-%m")
        repairs_used = _count_repairs_for_month(active_events, month_key)
        if repairs_used < MAX_REPAIRS_PER_MONTH:
            # Đang trong cửa sổ 24h Repair: giữ nguyên current_streak_days cho đến hết ngày today.
            return False, True

        user.current_streak_days = 0
        user.streak_frozen = False
        user.last_streak_date = None
        user.updated_at = now_utc
        return True, False

    # gap >= 3: soft reset, giữ nguyên lịch sử AuditEvent/CheckIn cho longest_streak_days.
    user.current_streak_days = 0
    user.streak_frozen = False
    user.last_streak_date = None
    user.updated_at = now_utc
    return True, False


async def _load_completed_seconds_by_date(
    db: AsyncSession,
    owner: UUID,
    *,
    local_tz: ZoneInfo,
) -> dict[date, int]:
    rows = (
        await db.execute(
            select(CheckIn, SessionRun)
            .join(SessionRun, CheckIn.run_id == SessionRun.id)
            .where(
                CheckIn.user_id == owner,
                CheckIn.kind == "final",
                CheckIn.response_state == "answered",
                CheckIn.session_outcome == "completed",
                SessionRun.user_id == owner,
            )
        )
    ).all()
    seconds_by_date: dict[date, int] = {}
    for checkin, run in rows:
        actual = _run_actual_seconds(run)
        if actual < MIN_STREAK_FOCUS_SECONDS:
            continue
        activity_dt = run.ended_at or checkin.responded_at
        if activity_dt is None:
            continue
        d = _local_date_for_dt(activity_dt, local_tz)
        seconds_by_date[d] = seconds_by_date.get(d, 0) + actual
    return seconds_by_date


def _compute_longest_streak_days(
    active_events: list[AuditEvent],
    current_streak_days: int,
) -> int:
    active_dates = sorted(
        {
            ev_date
            for ev in active_events
            if (ev_date := _event_date(ev)) is not None
        }
    )
    best = 0
    run_len = 0
    prev: date | None = None
    for d in active_dates:
        if prev is not None and (d - prev).days == 1:
            run_len += 1
        else:
            run_len = 1
        if run_len >= STREAK_UNLOCK_DAYS and run_len > best:
            best = run_len
        prev = d
    if current_streak_days >= STREAK_UNLOCK_DAYS and current_streak_days > best:
        best = current_streak_days
    return best


async def _build_streak_data(
    db: AsyncSession,
    *,
    user: User,
    now_utc: datetime,
    tz_name: str,
    local_tz: ZoneInfo,
) -> StreakData:
    today = _local_date_for_dt(now_utc, local_tz)
    events = await _load_all_streak_events(db, user.id)
    active_events = [ev for ev in events if _is_active_audit_event(ev)]
    seconds_by_date = await _load_completed_seconds_by_date(db, user.id, local_tz=local_tz)

    month_key = today.strftime("%Y-%m")
    repairs_used_this_month = _count_repairs_for_month(active_events, month_key)
    repairs_left_this_month = max(0, MAX_REPAIRS_PER_MONTH - repairs_used_this_month)

    gap = (today - user.last_streak_date).days if user.last_streak_date is not None else None
    repair_pending = (
        gap == 2
        and user.current_streak_days >= STREAK_UNLOCK_DAYS
        and user.streak_freeze_tokens == 0
        and repairs_used_this_month < MAX_REPAIRS_PER_MONTH
    )
    repair_window_ends_at = (
        datetime(today.year, today.month, today.day, 0, 0, tzinfo=local_tz) + timedelta(days=1)
        if repair_pending
        else None
    )

    record_by_date: dict[date, StreakDayRecordData] = {}
    active_done_dates: set[date] = set()
    event_kind_map: dict[str, Literal["done", "freeze", "repair"]] = {
        STREAK_EVENT_DONE: "done",
        STREAK_EVENT_FREEZE: "freeze",
        STREAK_EVENT_REPAIR: "repair",
    }
    for ev in active_events:
        d = _event_date(ev)
        kind = event_kind_map.get(ev.event_type)
        if d is None or kind is None:
            continue
        if kind == "done":
            active_done_dates.add(d)
        existing_rec = record_by_date.get(d)
        if existing_rec is not None and _KIND_PRIORITY[existing_rec.kind] > _KIND_PRIORITY[kind]:
            continue
        if kind == "done":
            total_secs = seconds_by_date.get(d, 0)
            minutes = max(1, round(total_secs / 60)) if total_secs > 0 else 1
        else:
            minutes = 0
        record_by_date[d] = StreakDayRecordData(date=d, kind=kind, minutes=minutes)

    if repair_pending:
        yesterday = today - timedelta(days=1)
        if yesterday not in record_by_date:
            record_by_date[yesterday] = StreakDayRecordData(
                date=yesterday,
                kind="repair",
                minutes=0,
            )

    window_start = today - timedelta(days=DAY_RECORDS_WINDOW_DAYS - 1)
    day_records = sorted(
        (rec for d, rec in record_by_date.items() if window_start <= d <= today),
        key=lambda rec: rec.date,
    )

    longest_streak_days = _compute_longest_streak_days(active_events, user.current_streak_days)
    today_secs = seconds_by_date.get(today, 0)
    today_completed = today in active_done_dates or (
        user.last_streak_date == today and not user.streak_frozen
    )
    today_focus_minutes = (
        max(1, round(today_secs / 60))
        if today_secs >= MIN_STREAK_FOCUS_SECONDS
        else (round(today_secs / 60) if today_secs > 0 else 0)
    )

    return StreakData(
        current_streak_days=user.current_streak_days,
        effective_display_streak=user.current_streak_days,
        unlocked=user.current_streak_days >= STREAK_UNLOCK_DAYS,
        streak_frozen=user.streak_frozen,
        streak_freeze_tokens=user.streak_freeze_tokens,
        max_freeze_tokens=MAX_FREEZE_TOKENS,
        last_streak_date=user.last_streak_date,
        repair_pending=repair_pending,
        repair_window_ends_at=repair_window_ends_at,
        repairs_used_this_month=repairs_used_this_month,
        repairs_left_this_month=repairs_left_this_month,
        max_repairs_per_month=MAX_REPAIRS_PER_MONTH,
        longest_streak_days=longest_streak_days,
        today_completed=today_completed,
        today_focus_minutes=today_focus_minutes,
        timezone=tz_name,
        day_records=day_records,
    )


async def get_streak_summary(
    db: AsyncSession,
    *,
    user_id: str,
    commit_if_changed: bool = True,
    now: datetime | None = None,
) -> StreakData:
    """Đọc trạng thái Focus Streak (tự động reconcile gap an toàn dưới row lock khi cần)."""
    owner = command_service.owner_uuid(user_id)
    now_utc = _ensure_utc(now)
    user = await db.scalar(
        select(User).where(User.id == owner).execution_options(populate_existing=True)
    )
    if user is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy người dùng.",
            next_action="reload",
        )
    tz_name, local_tz = await _resolve_user_timezone(db, owner)
    today = _local_date_for_dt(now_utc, local_tz)

    if user.last_streak_date is not None and (today - user.last_streak_date).days >= 2:
        if commit_if_changed:
            await db.rollback()
            await command_service.lock_owner(db, str(owner))
            locked_user = await db.scalar(
                select(User)
                .where(User.id == owner)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if locked_user is None:
                raise AppError(
                    status_code=404,
                    code="RESOURCE_NOT_FOUND",
                    message="Không tìm thấy người dùng.",
                    next_action="reload",
                )
            user = locked_user
            tz_name, local_tz = await _resolve_user_timezone(db, owner)
            today = _local_date_for_dt(now_utc, local_tz)
            changed, _ = await _apply_reconcile_mutation(
                db, user, today=today, now_utc=now_utc
            )
            if changed:
                await db.commit()
            else:
                await db.rollback()
            refreshed = await db.scalar(
                select(User).where(User.id == owner).execution_options(populate_existing=True)
            )
            if refreshed is not None:
                user = refreshed
        else:
            await _apply_reconcile_mutation(db, user, today=today, now_utc=now_utc)
            await db.flush()

    return await _build_streak_data(
        db, user=user, now_utc=now_utc, tz_name=tz_name, local_tz=local_tz
    )


async def _record_completed_activity_date(
    db: AsyncSession,
    *,
    user: User,
    activity_date: date,
    activity_dt: datetime,
    tz_name: str,
) -> None:
    activity_utc = _ensure_utc(activity_dt)
    events = await _load_all_streak_events(db, user.id)
    _, repair_pending = await _apply_reconcile_mutation(
        db, user, today=activity_date, now_utc=activity_utc, events=events
    )

    done_event_id = f"streak:done:{activity_date.isoformat()}"
    existing_done = next((ev for ev in events if ev.event_id == done_event_id), None)
    if existing_done is not None and _is_active_audit_event(existing_done):
        if user.last_streak_date == activity_date and user.streak_frozen:
            user.streak_frozen = False
            user.updated_at = activity_utc
        await db.flush()
        return

    if user.last_streak_date == activity_date:
        user.streak_frozen = False
        user.updated_at = activity_utc
        await _upsert_audit_event(
            db,
            owner=user.id,
            event_type=STREAK_EVENT_DONE,
            event_id=done_event_id,
            resource_ref={
                "date": activity_date.isoformat(),
                "prev_streak": max(0, user.current_streak_days - 1),
                "prev_last_date": (
                    (activity_date - timedelta(days=1)).isoformat()
                    if user.current_streak_days > 1
                    else None
                ),
                "prev_frozen": False,
                "earned_freeze": False,
                "backfilled": False,
                "timezone": tz_name,
                "active": True,
            },
            occurred_at=activity_utc,
            received_at=datetime.now(UTC),
        )
        await db.flush()
        return

    prev_streak = user.current_streak_days
    prev_last_date = user.last_streak_date.isoformat() if user.last_streak_date else None
    prev_frozen = user.streak_frozen

    if user.last_streak_date is not None and activity_date < user.last_streak_date:
        # Backfilled / late check-in: ghi nhận lịch sử ngày đó mà không phá chuỗi hiện tại.
        await _upsert_audit_event(
            db,
            owner=user.id,
            event_type=STREAK_EVENT_DONE,
            event_id=done_event_id,
            resource_ref={
                "date": activity_date.isoformat(),
                "prev_streak": prev_streak,
                "prev_last_date": prev_last_date,
                "prev_frozen": prev_frozen,
                "earned_freeze": False,
                "backfilled": True,
                "timezone": tz_name,
                "active": True,
            },
            occurred_at=activity_utc,
            received_at=datetime.now(UTC),
        )
        await db.flush()
        return

    if repair_pending:
        yesterday = activity_date - timedelta(days=1)
        await _upsert_audit_event(
            db,
            owner=user.id,
            event_type=STREAK_EVENT_REPAIR,
            event_id=f"streak:repair:{yesterday.isoformat()}",
            resource_ref={
                "date": yesterday.isoformat(),
                "month_key": activity_date.strftime("%Y-%m"),
                "source": "auto_checkin",
                "active": True,
            },
            occurred_at=activity_utc,
            received_at=datetime.now(UTC),
        )
        user.current_streak_days += 1
        user.last_streak_date = activity_date
        user.streak_frozen = False
    elif user.last_streak_date is None or user.current_streak_days == 0:
        user.current_streak_days = 1
        user.last_streak_date = activity_date
        user.streak_frozen = False
    elif (activity_date - user.last_streak_date).days == 1:
        user.current_streak_days += 1
        user.last_streak_date = activity_date
        user.streak_frozen = False
    else:
        user.current_streak_days = 1
        user.last_streak_date = activity_date
        user.streak_frozen = False

    earned_freeze = False
    if (
        user.current_streak_days >= STREAK_UNLOCK_DAYS
        and (user.current_streak_days == 7 or user.current_streak_days % 5 == 0)
        and user.streak_freeze_tokens < MAX_FREEZE_TOKENS
    ):
        user.streak_freeze_tokens += 1
        earned_freeze = True

    user.updated_at = activity_utc
    await _upsert_audit_event(
        db,
        owner=user.id,
        event_type=STREAK_EVENT_DONE,
        event_id=done_event_id,
        resource_ref={
            "date": activity_date.isoformat(),
            "prev_streak": prev_streak,
            "prev_last_date": prev_last_date,
            "prev_frozen": prev_frozen,
            "earned_freeze": earned_freeze,
            "backfilled": False,
            "timezone": tz_name,
            "active": True,
        },
        occurred_at=activity_utc,
        received_at=datetime.now(UTC),
    )
    await db.flush()


async def _rollback_activity_date_if_empty(
    db: AsyncSession,
    *,
    user: User,
    activity_date: date,
    local_tz: ZoneInfo,
) -> None:
    done_event_id = f"streak:done:{activity_date.isoformat()}"
    done_event = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.user_id == user.id, AuditEvent.event_id == done_event_id)
        .execution_options(populate_existing=True)
    )
    if done_event is None or not _is_active_audit_event(done_event):
        return

    seconds_by_date = await _load_completed_seconds_by_date(db, user.id, local_tz=local_tz)
    if seconds_by_date.get(activity_date, 0) >= MIN_STREAK_FOCUS_SECONDS:
        return

    ref = dict(done_event.resource_ref or {})
    prev_streak = int(ref.get("prev_streak", 0))
    prev_last_date_str = ref.get("prev_last_date")
    prev_frozen = bool(ref.get("prev_frozen", False))
    earned_freeze = bool(ref.get("earned_freeze", False))

    if user.last_streak_date == activity_date:
        user.current_streak_days = max(0, prev_streak)
        user.last_streak_date = (
            date.fromisoformat(prev_last_date_str)
            if isinstance(prev_last_date_str, str) and prev_last_date_str
            else None
        )
        user.streak_frozen = prev_frozen
        if earned_freeze:
            user.streak_freeze_tokens = max(0, user.streak_freeze_tokens - 1)
        user.updated_at = datetime.now(UTC)

    done_event.resource_ref = {**ref, "active": False}

    yesterday = activity_date - timedelta(days=1)
    repair_event = await db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.user_id == user.id,
            AuditEvent.event_id == f"streak:repair:{yesterday.isoformat()}",
        )
        .execution_options(populate_existing=True)
    )
    if (
        repair_event is not None
        and _is_active_audit_event(repair_event)
        and (repair_event.resource_ref or {}).get("source") == "auto_checkin"
        and prev_last_date_str == (activity_date - timedelta(days=2)).isoformat()
    ):
        repair_event.resource_ref = {**dict(repair_event.resource_ref or {}), "active": False}

    await db.flush()


async def sync_streak_for_run(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    run: SessionRun,
) -> None:
    """Đồng bộ Focus Streak sau Check-in cuối phiên hoặc hiệu chỉnh actual_work_seconds."""
    final_checkin = await db.scalar(
        select(CheckIn)
        .where(
            CheckIn.user_id == user_uuid,
            CheckIn.run_id == run.id,
            CheckIn.kind == "final",
        )
        .execution_options(populate_existing=True)
    )
    if final_checkin is None or final_checkin.response_state != "answered":
        return

    activity_dt = run.ended_at or final_checkin.responded_at
    if activity_dt is None:
        return

    user = await db.scalar(
        select(User)
        .where(User.id == user_uuid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        return

    tz_name, local_tz = await _resolve_user_timezone(db, user_uuid)
    activity_date = _local_date_for_dt(activity_dt, local_tz)
    actual = _run_actual_seconds(run)

    if final_checkin.session_outcome == "completed" and actual >= MIN_STREAK_FOCUS_SECONDS:
        await _record_completed_activity_date(
            db,
            user=user,
            activity_date=activity_date,
            activity_dt=activity_dt,
            tz_name=tz_name,
        )
    else:
        await _rollback_activity_date_if_empty(
            db,
            user=user,
            activity_date=activity_date,
            local_tz=local_tz,
        )


async def repair_streak(
    db: AsyncSession,
    *,
    user_id: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> StreakData:
    """Kích hoạt Nối lại nhịp 24h (24h Repair) idempotent cho ngày vừa lỡ hôm qua."""
    digest = command_service.command_hash("repair_streak", "", {})
    saved = await command_service.replay(db, user_id, idempotency_key, digest)
    if saved is not None:
        await db.rollback()
        return StreakData.model_validate(saved)

    await db.rollback()
    db.info["defer_command_commit"] = True
    try:
        await command_service.lock_owner(db, user_id)
        saved = await command_service.replay(db, user_id, idempotency_key, digest)
        if saved is not None:
            await db.rollback()
            return StreakData.model_validate(saved)

        owner = command_service.owner_uuid(user_id)
        now_utc = _ensure_utc(now)
        user = await db.scalar(
            select(User)
            .where(User.id == owner)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise AppError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                message="Không tìm thấy người dùng.",
                next_action="reload",
            )

        tz_name, local_tz = await _resolve_user_timezone(db, owner)
        today = _local_date_for_dt(now_utc, local_tz)
        yesterday = today - timedelta(days=1)

        events = await _load_all_streak_events(db, owner)
        active_events = [ev for ev in events if _is_active_audit_event(ev)]
        month_key = today.strftime("%Y-%m")
        repairs_used = _count_repairs_for_month(active_events, month_key)

        gap = (today - user.last_streak_date).days if user.last_streak_date is not None else None
        eligible = (
            gap == 2
            and user.current_streak_days >= STREAK_UNLOCK_DAYS
            and user.streak_freeze_tokens == 0
            and repairs_used < MAX_REPAIRS_PER_MONTH
        )
        if not eligible:
            raise AppError(
                status_code=409,
                code="STREAK_REPAIR_UNAVAILABLE",
                message="Hiện không có cửa sổ Nối lại nhịp 24h khả dụng để khôi phục chuỗi.",
                next_action="reload",
            )

        await _upsert_audit_event(
            db,
            owner=owner,
            event_type=STREAK_EVENT_REPAIR,
            event_id=f"streak:repair:{yesterday.isoformat()}",
            resource_ref={
                "date": yesterday.isoformat(),
                "month_key": month_key,
                "source": "manual_repair",
                "active": True,
            },
            occurred_at=now_utc,
            received_at=now_utc,
        )
        user.last_streak_date = yesterday
        user.streak_frozen = True
        user.updated_at = now_utc
        await db.flush()

        result = await _build_streak_data(
            db, user=user, now_utc=now_utc, tz_name=tz_name, local_tz=local_tz
        )
        command_service.record(db, user_id, idempotency_key, digest, "repair_streak", result)
        await db.commit()
        return result
    except (IntegrityError, AppError):
        await db.rollback()
        saved = await command_service.replay(db, user_id, idempotency_key, digest)
        if saved is not None:
            return StreakData.model_validate(saved)
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        db.info.pop("defer_command_commit", None)
        db.info.pop("command_result_meta", None)
