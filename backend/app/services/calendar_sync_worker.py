"""Worker nền xử lý hàng chờ đồng bộ sự kiện sang Google Calendar (CalendarSyncJob Outbox).

Đảm bảo:
- Xử lý bất đồng bộ, eventual consistency.
- Tự động lấy token hợp lệ từ Fernet envelope.
- Ghi vào đúng secondary calendar (write_calendar_id).
- Xử lý lỗi 412 (If-Match Etag conflict) -> chuyển job_state thành 'conflict' và dừng retry mù.
- Cơ chế Exponential Backoff cho các lỗi mạng thông thường.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, exists, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.calendar import CalendarConnection, CalendarEventLink, CalendarSyncJob
from app.services.calendar_service import (
    _CONNECTION_SYNC_LOCKS,
    CalendarService,
    _resolve_uuid,
    get_connection_sync_lock,
)
from app.services.google_calendar_client import CalendarAuthError, CalendarConflictError

logger = logging.getLogger(__name__)


def _busy_connection_ids_for_current_loop() -> list[UUID]:
    """Liệt kê các connection_id đang giữ sync lock trên event loop hiện tại."""
    loop_id = id(asyncio.get_running_loop())
    busy: list[UUID] = []
    for (entry_loop_id, conn_id_str), entry in list(_CONNECTION_SYNC_LOCKS.items()):
        if entry_loop_id == loop_id and entry.lock.locked():
            try:
                busy.append(UUID(conn_id_str))
            except ValueError:
                continue
    return busy


async def _sync_identity_map(
    db: AsyncSession,
    *,
    job_id: UUID | None = None,
    conn_id: UUID | None = None,
    link_id: UUID | None = None,
) -> None:
    """Đồng bộ lại các ORM entity trong identity map sau Core SQL update (tránh stale/expired attribute)."""
    if job_id is not None:
        (
            await db.execute(
                select(CalendarSyncJob)
                .where(CalendarSyncJob.id == job_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    if conn_id is not None:
        (
            await db.execute(
                select(CalendarConnection)
                .where(CalendarConnection.id == conn_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    if link_id is not None:
        (
            await db.execute(
                select(CalendarEventLink)
                .where(CalendarEventLink.id == link_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()


async def _update_sync_job_core(
    db: AsyncSession,
    job_id: UUID,
    *,
    values: dict[str, Any],
) -> None:
    """Cập nhật CalendarSyncJob bằng Core SQL nguyên tử để tránh StaleDataError."""
    await db.execute(
        update(CalendarSyncJob)
        .where(CalendarSyncJob.id == job_id)
        .values(**values, revision=CalendarSyncJob.revision + 1)
        .execution_options(synchronize_session="fetch")
    )
    await _sync_identity_map(db, job_id=job_id)


async def _mark_job_retry_or_failed(
    db: AsyncSession,
    job_id: UUID,
    exc: Exception,
    now_utc: datetime,
) -> None:
    """Ghi nhận retry backoff hoặc đánh dấu failed bằng Core SQL sau khi đã rollback."""
    job_row = (
        await db.execute(
            select(CalendarSyncJob)
            .where(CalendarSyncJob.id == job_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if job_row is None:
        logger.warning("[CalendarWorker] Error processing job %s: %s", job_id, exc)
        return

    next_attempt = int(job_row.attempt_count or 0) + 1
    err_code = str(exc)[:120]
    logger.warning(
        "[CalendarWorker] Error processing job %s (attempt %d): %s",
        job_id,
        next_attempt,
        exc,
    )
    if next_attempt >= 5:
        values: dict[str, Any] = {
            "attempt_count": next_attempt,
            "job_state": "failed",
            "last_error_code": err_code,
            "lease_expires_at": None,
            "updated_at": now_utc,
        }
    else:
        values = {
            "attempt_count": next_attempt,
            "job_state": "queued",
            "next_retry_at": now_utc + timedelta(seconds=2**next_attempt),
            "last_error_code": err_code,
            "lease_expires_at": None,
            "updated_at": now_utc,
        }
    await _update_sync_job_core(db, job_id, values=values)
    await db.commit()


async def process_sync_job(
    db: AsyncSession,
    job: CalendarSyncJob,
    cal_service: CalendarService,
) -> str:
    """Xử lý một CalendarSyncJob đơn lẻ."""
    now_utc = datetime.now(UTC)
    client = cal_service.client
    insp = sa_inspect(job)
    identity = insp.identity if insp is not None else None
    job_id: UUID = identity[0] if identity else job.id
    fresh_job = (
        await db.execute(
            select(CalendarSyncJob)
            .where(CalendarSyncJob.id == job_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if fresh_job is None:
        return "failed"
    conn_id = fresh_job.connection_id
    link_id = fresh_job.event_link_id

    # 1. Kiểm tra CalendarConnection (nạp tươi từ DB)
    conn = (
        await db.execute(
            select(CalendarConnection)
            .where(CalendarConnection.id == conn_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not conn or conn.connection_state != "connected":
        await _update_sync_job_core(
            db,
            job_id,
            values={
                "job_state": "failed",
                "last_error_code": "CONNECTION_NOT_ACTIVE",
                "attempt_count": 5,
                "lease_expires_at": None,
                "updated_at": now_utc,
            },
        )
        await db.commit()
        return "failed"

    if not conn.write_calendar_id:
        await _update_sync_job_core(
            db,
            job_id,
            values={
                "job_state": "failed",
                "last_error_code": "NO_WRITE_CALENDAR",
                "attempt_count": 5,
                "lease_expires_at": None,
                "updated_at": now_utc,
            },
        )
        await db.commit()
        return "failed"
    write_calendar_id: str = conn.write_calendar_id

    # 2. Lấy Access Token
    try:
        access_token = await cal_service.get_valid_access_token(conn)
    except CalendarAuthError as exc:
        await db.rollback()
        logger.warning("Permanent token acquisition failed for job %s: %s", job_id, exc)
        await _update_sync_job_core(
            db,
            job_id,
            values={
                "job_state": "failed",
                "last_error_code": "AUTH_FAILED",
                "attempt_count": 5,
                "lease_expires_at": None,
                "updated_at": now_utc,
            },
        )
        await db.commit()
        return "failed"
    except Exception as exc:
        # ISSUE-A: Lỗi mạng tạm thời khi refresh token -> rollback transaction trước khi ghi retry backoff
        await db.rollback()
        logger.warning("Transient token acquisition error for job %s: %s", job_id, exc)
        await _mark_job_retry_or_failed(db, job_id, exc, now_utc)
        return "retry"

    # 3. Tải lại Job / Connection / Event Link với populate_existing=True trước khi gọi Google API
    job_row = (
        await db.execute(
            select(CalendarSyncJob)
            .where(CalendarSyncJob.id == job_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if job_row is None:
        return "failed"

    conn_row = (
        await db.execute(
            select(CalendarConnection)
            .where(CalendarConnection.id == conn_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if conn_row and conn_row.write_calendar_id:
        write_calendar_id = conn_row.write_calendar_id

    link: CalendarEventLink | None = None
    if link_id:
        link = (
            await db.execute(
                select(CalendarEventLink)
                .where(CalendarEventLink.id == link_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    operation = job_row.operation
    target_kind = job_row.target_kind
    target_ref = job_row.target_ref
    target_schedule_revision = job_row.target_schedule_revision
    expected_google_etag = job_row.expected_google_etag
    payload_snapshot = job_row.payload_snapshot
    link_google_calendar_id = link.google_calendar_id if link is not None else None
    link_google_event_id = link.google_event_id if link is not None else None
    link_google_etag = link.google_etag if link is not None else None

    payload_cal_id = (
        payload_snapshot.get("google_calendar_id")
        if isinstance(payload_snapshot, dict)
        else None
    )
    target_calendar_id = (
        (link_google_calendar_id if link_google_calendar_id else None)
        or (str(payload_cal_id) if payload_cal_id else None)
        or write_calendar_id
    )
    effective_etag = (
        link_google_etag
        if (link is not None and link_google_etag)
        else expected_google_etag
    )

    # 4. Thực thi theo loại operation (persist kết quả bằng Core SQL để không bao giờ mất ETag do StaleDataError)
    try:
        if operation == "create":
            try:
                res = await client.create_event(
                    access_token=access_token,
                    calendar_id=target_calendar_id,
                    event_payload=payload_snapshot,
                )
            except CalendarConflictError:
                existing_id = (payload_snapshot or {}).get("id")
                if not existing_id:
                    raise
                existing_event = await client.get_event(
                    access_token=access_token,
                    calendar_id=target_calendar_id,
                    event_id=str(existing_id),
                )
                if existing_event is None:
                    raise
                res = existing_event

            now_done = datetime.now(UTC)
            created_event_id = res.get("id")
            if link_id is not None:
                sync_state_expr: Any = (
                    case(
                        (
                            CalendarEventLink.desired_schedule_revision
                            <= target_schedule_revision,
                            "synced",
                        ),
                        else_=CalendarEventLink.sync_state,
                    )
                    if target_schedule_revision is not None
                    else "synced"
                )
                await db.execute(
                    update(CalendarEventLink)
                    .where(CalendarEventLink.id == link_id)
                    .values(
                        google_event_id=created_event_id,
                        google_etag=res.get("etag"),
                        synced_schedule_revision=target_schedule_revision,
                        sync_state=sync_state_expr,
                        last_synced_at=now_done,
                        updated_at=now_done,
                        revision=CalendarEventLink.revision + 1,
                    )
                    .execution_options(synchronize_session="fetch")
                )

            job_values: dict[str, Any] = {
                "job_state": "completed",
                "lease_expires_at": None,
                "last_error_code": None,
                "updated_at": now_done,
            }
            if link_id is None and target_kind == "event" and created_event_id:
                job_values["target_ref"] = created_event_id

            await db.execute(
                update(CalendarSyncJob)
                .where(CalendarSyncJob.id == job_id)
                .values(**job_values, revision=CalendarSyncJob.revision + 1)
                .execution_options(synchronize_session="fetch")
            )
            await db.execute(
                update(CalendarConnection)
                .where(CalendarConnection.id == conn_id)
                .values(last_successful_sync_at=now_done, updated_at=now_done)
                .execution_options(synchronize_session="fetch")
            )
            await _sync_identity_map(db, job_id=job_id, conn_id=conn_id, link_id=link_id)
            await db.commit()
            return "completed"

        elif operation == "update":
            target_google_event_id = (
                link_google_event_id
                if link is not None
                else (target_ref if target_kind == "event" else None)
            )
            if not target_google_event_id:
                await _update_sync_job_core(
                    db,
                    job_id,
                    values={
                        "job_state": "failed",
                        "last_error_code": "EVENT_ID_NOT_FOUND",
                        "attempt_count": 5,
                        "lease_expires_at": None,
                        "updated_at": now_utc,
                    },
                )
                await db.commit()
                return "failed"

            res = await client.update_event(
                access_token=access_token,
                calendar_id=target_calendar_id,
                event_id=target_google_event_id,
                event_payload=payload_snapshot,
                expected_etag=effective_etag,
            )
            now_done = datetime.now(UTC)
            if link_id is not None:
                sync_state_expr = (
                    case(
                        (
                            CalendarEventLink.desired_schedule_revision
                            <= target_schedule_revision,
                            "synced",
                        ),
                        else_=CalendarEventLink.sync_state,
                    )
                    if target_schedule_revision is not None
                    else "synced"
                )
                await db.execute(
                    update(CalendarEventLink)
                    .where(CalendarEventLink.id == link_id)
                    .values(
                        google_etag=res.get("etag"),
                        synced_schedule_revision=target_schedule_revision,
                        sync_state=sync_state_expr,
                        last_synced_at=now_done,
                        updated_at=now_done,
                        revision=CalendarEventLink.revision + 1,
                    )
                    .execution_options(synchronize_session="fetch")
                )

            await db.execute(
                update(CalendarSyncJob)
                .where(CalendarSyncJob.id == job_id)
                .values(
                    job_state="completed",
                    lease_expires_at=None,
                    last_error_code=None,
                    updated_at=now_done,
                    revision=CalendarSyncJob.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
            await db.execute(
                update(CalendarConnection)
                .where(CalendarConnection.id == conn_id)
                .values(last_successful_sync_at=now_done, updated_at=now_done)
                .execution_options(synchronize_session="fetch")
            )
            await _sync_identity_map(db, job_id=job_id, conn_id=conn_id, link_id=link_id)
            await db.commit()
            return "completed"

        elif operation == "delete":
            target_google_event_id = (
                link_google_event_id
                if link is not None
                else (target_ref if target_kind == "event" else None)
            )
            if not target_google_event_id:
                await _update_sync_job_core(
                    db,
                    job_id,
                    values={
                        "job_state": "failed",
                        "last_error_code": "EVENT_ID_NOT_FOUND",
                        "attempt_count": 5,
                        "lease_expires_at": None,
                        "updated_at": now_utc,
                    },
                )
                await db.commit()
                return "failed"

            await client.delete_event(
                access_token=access_token,
                calendar_id=target_calendar_id,
                event_id=target_google_event_id,
                expected_etag=effective_etag,
            )
            now_done = datetime.now(UTC)
            if link_id is not None:
                await db.execute(
                    update(CalendarEventLink)
                    .where(CalendarEventLink.id == link_id)
                    .values(
                        is_current=False,
                        sync_state="deleted",
                        last_synced_at=now_done,
                        updated_at=now_done,
                        revision=CalendarEventLink.revision + 1,
                    )
                    .execution_options(synchronize_session="fetch")
                )

            await db.execute(
                update(CalendarSyncJob)
                .where(CalendarSyncJob.id == job_id)
                .values(
                    job_state="completed",
                    lease_expires_at=None,
                    last_error_code=None,
                    updated_at=now_done,
                    revision=CalendarSyncJob.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
            await db.execute(
                update(CalendarConnection)
                .where(CalendarConnection.id == conn_id)
                .values(last_successful_sync_at=now_done, updated_at=now_done)
                .execution_options(synchronize_session="fetch")
            )
            await _sync_identity_map(db, job_id=job_id, conn_id=conn_id, link_id=link_id)
            await db.commit()
            return "completed"

        else:
            await _update_sync_job_core(
                db,
                job_id,
                values={
                    "job_state": "failed",
                    "last_error_code": f"UNKNOWN_OPERATION_{operation}",
                    "lease_expires_at": None,
                    "updated_at": now_utc,
                },
            )
            await db.commit()
            return "failed"

    except CalendarConflictError as exc:
        # BUG-17: Rollback any failed partial session state before updating job metadata
        await db.rollback()
        # Xung đột phiên bản (412 Precondition Failed / Etag mismatch)
        logger.warning("[CalendarWorker] 412 Conflict on job %s: %s", job_id, exc)
        now_err = datetime.now(UTC)
        await db.execute(
            update(CalendarSyncJob)
            .where(CalendarSyncJob.id == job_id)
            .values(
                job_state="conflict",
                last_error_code="ETAG_CONFLICT_412",
                lease_expires_at=None,
                updated_at=now_err,
                revision=CalendarSyncJob.revision + 1,
            )
            .execution_options(synchronize_session="fetch")
        )
        if link_id is not None:
            await db.execute(
                update(CalendarEventLink)
                .where(CalendarEventLink.id == link_id)
                .values(
                    sync_state="conflict",
                    updated_at=now_err,
                    revision=CalendarEventLink.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
        await _sync_identity_map(db, job_id=job_id, conn_id=conn_id, link_id=link_id)
        await db.commit()
        return "conflict"

    except CalendarAuthError as exc:
        # BUG-15 & BUG-17 & RISK-I05: Token hết hạn/thu hồi -> cần kết nối lại, dừng retry vô ích
        await db.rollback()
        logger.warning("[CalendarWorker] Auth failure on job %s: %s", job_id, exc)
        now_err = datetime.now(UTC)
        await db.execute(
            update(CalendarConnection)
            .where(CalendarConnection.id == conn_id)
            .values(
                connection_state="needs_reconnect",
                revision=CalendarConnection.revision + 1,
                updated_at=now_err,
            )
            .execution_options(synchronize_session="fetch")
        )
        await db.execute(
            update(CalendarSyncJob)
            .where(CalendarSyncJob.id == job_id)
            .values(
                job_state="failed",
                last_error_code="AUTH_EXPIRED",
                attempt_count=5,
                lease_expires_at=None,
                updated_at=now_err,
                revision=CalendarSyncJob.revision + 1,
            )
            .execution_options(synchronize_session="fetch")
        )
        await _sync_identity_map(db, job_id=job_id, conn_id=conn_id)
        await db.commit()
        return "failed"

    except Exception as exc:
        # BUG-17: Rollback any failed partial session state before updating job metadata
        await db.rollback()
        # Lỗi mạng hoặc lỗi tạm thời -> tăng attempt_count và retry backoff
        await _mark_job_retry_or_failed(db, job_id, exc, datetime.now(UTC))
        return "retry"


async def process_calendar_sync_jobs(
    db: AsyncSession,
    user_id: UUID | str | None = None,
    limit: int = 10,
    *,
    connection_id: UUID | str | None = None,
    skip_busy_connections: bool = False,
    connection_lock_held: bool = False,
) -> int:
    """Quét và xử lý các sync jobs đang chờ trong hàng đợi."""
    other_job = aliased(CalendarSyncJob)

    def _build_query(
        pass_now: datetime,
        remaining_limit: int,
        only_link_ids: set[UUID] | None = None,
    ):
        active_same_link_exists = exists(
            select(1).where(
                other_job.id != CalendarSyncJob.id,
                other_job.event_link_id == CalendarSyncJob.event_link_id,
                other_job.job_state == "queued",
                other_job.attempt_count < 5,
                or_(
                    and_(
                        other_job.lease_expires_at.is_not(None),
                        other_job.lease_expires_at > pass_now,
                    ),
                    and_(
                        other_job.next_retry_at.is_not(None),
                        other_job.next_retry_at > pass_now,
                        or_(
                            other_job.created_at < CalendarSyncJob.created_at,
                            and_(
                                other_job.created_at == CalendarSyncJob.created_at,
                                other_job.id < CalendarSyncJob.id,
                            ),
                        ),
                    ),
                ),
            )
        )
        stmt = select(CalendarSyncJob).where(
            CalendarSyncJob.job_state == "queued",
            CalendarSyncJob.attempt_count < 5,
            or_(
                CalendarSyncJob.next_retry_at.is_(None),
                CalendarSyncJob.next_retry_at <= pass_now,
            ),
            or_(
                CalendarSyncJob.lease_expires_at.is_(None),
                CalendarSyncJob.lease_expires_at <= pass_now,
            ),
            or_(
                CalendarSyncJob.event_link_id.is_(None),
                ~active_same_link_exists,
            ),
        )
        if only_link_ids is not None:
            stmt = stmt.where(CalendarSyncJob.event_link_id.in_(only_link_ids))
        if user_id is not None:
            uid = _resolve_uuid(user_id)
            stmt = stmt.where(CalendarSyncJob.user_id == uid)
        if connection_id is not None:
            cid = _resolve_uuid(connection_id)
            stmt = stmt.where(CalendarSyncJob.connection_id == cid)
        if skip_busy_connections:
            busy_conn_ids = _busy_connection_ids_for_current_loop()
            if busy_conn_ids:
                stmt = stmt.where(CalendarSyncJob.connection_id.notin_(busy_conn_ids))

        stmt = stmt.order_by(CalendarSyncJob.created_at.asc(), CalendarSyncJob.id.asc()).limit(
            remaining_limit
        )
        dialect_name = db.bind.dialect.name if getattr(db, "bind", None) else ""
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return stmt

    cal_service = CalendarService(db)
    processed_count = 0
    only_link_ids: set[UUID] | None = None

    while processed_count < limit:
        now_utc = datetime.now(UTC)
        jobs = (await db.scalars(_build_query(now_utc, limit - processed_count, only_link_ids))).all()
        if not jobs:
            break

        # Claim toàn bộ batch ngay lập tức bằng Core SQL để tránh tranh chấp / duplicate execution / StaleDataError.
        # Tối đa 1 job cho mỗi event_link_id khác null trong cùng 1 batch để job sau (update/delete) chờ job trước hoàn tất.
        claimed_by_conn: dict[UUID, list[UUID]] = {}
        claimed_order: list[tuple[UUID, UUID]] = []
        claimed_link_ids: set[UUID] = set()
        for job in jobs:
            job_id = job.id
            job_conn_id = job.connection_id
            job_link_id = job.event_link_id
            if job_link_id is not None:
                if job_link_id in claimed_link_ids:
                    continue
                claimed_link_ids.add(job_link_id)
            claim_res = await db.execute(
                update(CalendarSyncJob)
                .where(
                    CalendarSyncJob.id == job_id,
                    CalendarSyncJob.job_state == "queued",
                    or_(
                        CalendarSyncJob.lease_expires_at.is_(None),
                        CalendarSyncJob.lease_expires_at <= now_utc,
                    ),
                )
                .values(
                    claimed_at=now_utc,
                    lease_expires_at=now_utc + timedelta(minutes=2),
                    updated_at=now_utc,
                    revision=CalendarSyncJob.revision + 1,
                )
                .execution_options(synchronize_session=False)
            )
            if (getattr(claim_res, "rowcount", 0) or 0) > 0:
                claimed_by_conn.setdefault(job_conn_id, []).append(job_id)
                claimed_order.append((job_id, job_conn_id))
        await db.commit()

        if not claimed_order:
            break

        completed_link_ids: set[UUID] = set()

        async def _run_claimed_job(jid: UUID, done_links: set[UUID]) -> int:
            current_job = (
                await db.execute(
                    select(CalendarSyncJob)
                    .where(CalendarSyncJob.id == jid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if current_job is None or current_job.job_state != "queued":
                return 0
            job_link_id = current_job.event_link_id
            outcome = await process_sync_job(db, current_job, cal_service)
            if outcome == "completed" and job_link_id is not None:
                done_links.add(job_link_id)
            return 1

        if connection_lock_held:
            for jid, _ in claimed_order:
                processed_count += await _run_claimed_job(jid, completed_link_ids)
        else:
            for conn_id_key, conn_job_ids in claimed_by_conn.items():
                async with get_connection_sync_lock(conn_id_key):
                    for jid in conn_job_ids:
                        processed_count += await _run_claimed_job(jid, completed_link_ids)

        if not completed_link_ids:
            break
        only_link_ids = completed_link_ids

    return processed_count
