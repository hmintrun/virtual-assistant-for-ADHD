"""Worker nền xử lý hàng chờ đồng bộ sự kiện sang Google Calendar (CalendarSyncJob Outbox).

Đảm bảo:
- Xử lý bất đồng bộ, eventual consistency.
- Tự động lấy token hợp lệ từ Fernet envelope.
- Ghi vào đúng secondary calendar (write_calendar_id).
- Xử lý lỗi 412 (If-Match Etag conflict) -> chuyển job_state thành 'conflict' và dừng retry mù.
- Cơ chế Exponential Backoff cho các lỗi mạng thông thường.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarConnection, CalendarEventLink, CalendarSyncJob
from app.services.calendar_service import CalendarService, _resolve_uuid
from app.services.google_calendar_client import CalendarAuthError, CalendarConflictError, get_calendar_client

logger = logging.getLogger(__name__)


async def _reload_sync_entities(
    db: AsyncSession,
    job_id: UUID,
    conn_id: UUID,
    link_id: UUID | None,
) -> tuple[CalendarSyncJob | None, CalendarConnection | None, CalendarEventLink | None]:
    """Tải lại các ORM entity sau rollback để tránh MissingGreenlet khi đọc thuộc tính đã hết hạn."""
    job = await db.get(CalendarSyncJob, job_id)
    conn = await db.get(CalendarConnection, conn_id)
    link = await db.get(CalendarEventLink, link_id) if link_id else None
    return job, conn, link


async def process_sync_job(
    db: AsyncSession,
    job: CalendarSyncJob,
    cal_service: CalendarService,
) -> str:
    """Xử lý một CalendarSyncJob đơn lẻ."""
    now_utc = datetime.now(timezone.utc)
    client = cal_service.client
    job_id = job.id
    conn_id = job.connection_id
    link_id = job.event_link_id

    # 1. Kiểm tra CalendarConnection
    conn = await db.get(CalendarConnection, conn_id)
    if not conn or conn.connection_state != "connected":
        job.job_state = "failed"
        job.last_error_code = "CONNECTION_NOT_ACTIVE"
        job.attempt_count = 5  # Đánh dấu không retry nữa
        await db.commit()
        return "failed"

    if not conn.write_calendar_id:
        job.job_state = "failed"
        job.last_error_code = "NO_WRITE_CALENDAR"
        job.attempt_count = 5  # Đánh dấu không retry nữa
        await db.commit()
        return "failed"

    # 2. Lấy Access Token
    try:
        access_token = await cal_service.get_valid_access_token(conn)
    except CalendarAuthError as exc:
        logger.warning("Permanent token acquisition failed for job %s: %s", job_id, exc)
        job_row = await db.get(CalendarSyncJob, job_id)
        if job_row:
            job_row.job_state = "failed"
            job_row.last_error_code = "AUTH_FAILED"
            job_row.attempt_count = 5
            await db.commit()
        return "failed"
    except Exception as exc:
        # ISSUE-A: Lỗi mạng tạm thời khi refresh token -> đưa vào retry backoff, không khai tử ngay
        logger.warning("Transient token acquisition error for job %s: %s", job_id, exc)
        job_row = await db.get(CalendarSyncJob, job_id)
        if job_row:
            job_row.attempt_count += 1
            if job_row.attempt_count >= 5:
                job_row.job_state = "failed"
                job_row.last_error_code = str(exc)[:120]
            else:
                job_row.job_state = "queued"
                job_row.next_retry_at = now_utc + timedelta(seconds=2 ** job_row.attempt_count)
                job_row.last_error_code = str(exc)[:120]
                job_row.lease_expires_at = None
            await db.commit()
        return "retry"

    # 3. Lấy Event Link tương ứng (làm mới lại phòng trường hợp get_valid_access_token vừa commit)
    job = await db.get(CalendarSyncJob, job_id) or job
    conn = await db.get(CalendarConnection, conn_id) or conn
    link: CalendarEventLink | None = None
    if link_id:
        link = await db.get(CalendarEventLink, link_id)

    # 4. Thực thi theo loại operation
    try:
        if job.operation == "create":
            res = await client.create_event(
                access_token=access_token,
                calendar_id=conn.write_calendar_id,
                event_payload=job.payload_snapshot,
            )
            created_event_id = res.get("id")
            if link:
                link.google_event_id = created_event_id
                link.google_etag = res.get("etag")
                link.synced_schedule_revision = job.target_schedule_revision
                link.sync_state = "synced"
                link.last_synced_at = now_utc
            elif job.target_kind == "event" and created_event_id:
                job.target_ref = created_event_id

            job.job_state = "completed"
            conn.last_successful_sync_at = now_utc
            await db.commit()
            return "completed"

        elif job.operation == "update":
            target_google_event_id = (
                link.google_event_id
                if link
                else (job.target_ref if job.target_kind == "event" else None)
            )
            if not target_google_event_id:
                job.job_state = "failed"
                job.last_error_code = "EVENT_ID_NOT_FOUND"
                job.attempt_count = 5
                await db.commit()
                return "failed"

            res = await client.update_event(
                access_token=access_token,
                calendar_id=conn.write_calendar_id,
                event_id=target_google_event_id,
                event_payload=job.payload_snapshot,
                expected_etag=job.expected_google_etag,
            )
            if link:
                link.google_etag = res.get("etag")
                link.synced_schedule_revision = job.target_schedule_revision
                link.sync_state = "synced"
                link.last_synced_at = now_utc

            job.job_state = "completed"
            conn.last_successful_sync_at = now_utc
            await db.commit()
            return "completed"

        elif job.operation == "delete":
            target_google_event_id = (
                link.google_event_id
                if link
                else (job.target_ref if job.target_kind == "event" else None)
            )
            if not target_google_event_id:
                job.job_state = "failed"
                job.last_error_code = "EVENT_ID_NOT_FOUND"
                job.attempt_count = 5
                await db.commit()
                return "failed"

            await client.delete_event(
                access_token=access_token,
                calendar_id=conn.write_calendar_id,
                event_id=target_google_event_id,
                expected_etag=job.expected_google_etag,
            )
            if link:
                link.is_current = False
                link.sync_state = "deleted"
                link.last_synced_at = now_utc

            job.job_state = "completed"
            conn.last_successful_sync_at = now_utc
            await db.commit()
            return "completed"

        else:
            job.job_state = "failed"
            job.last_error_code = f"UNKNOWN_OPERATION_{job.operation}"
            await db.commit()
            return "failed"

    except CalendarConflictError as exc:
        # BUG-17: Rollback any failed partial session state before updating job metadata
        await db.rollback()
        # Xung đột phiên bản (412 Precondition Failed / Etag mismatch)
        logger.warning("[CalendarWorker] 412 Conflict on job %s: %s", job_id, exc)
        job_row, _, link_row = await _reload_sync_entities(db, job_id, conn_id, link_id)
        if job_row:
            job_row.job_state = "conflict"
            job_row.last_error_code = "ETAG_CONFLICT_412"
            job_row.lease_expires_at = None
        if link_row:
            link_row.sync_state = "conflict"
        await db.commit()
        return "conflict"

    except CalendarAuthError as exc:
        # BUG-15 & BUG-17: Token hết hạn/thu hồi trong lúc gọi Google (401)
        await db.rollback()
        logger.warning("[CalendarWorker] Auth failure on job %s: %s", job_id, exc)
        job_row, conn_row, _ = await _reload_sync_entities(db, job_id, conn_id, None)
        if conn_row:
            conn_row.connection_state = "needs_reconnect"
        if job_row:
            job_row.attempt_count += 1
            if job_row.attempt_count >= 5:
                job_row.job_state = "failed"
                job_row.last_error_code = "AUTH_EXPIRED"
            else:
                job_row.job_state = "queued"
                job_row.next_retry_at = now_utc + timedelta(seconds=2 ** job_row.attempt_count)
                job_row.last_error_code = "AUTH_EXPIRED"
                job_row.lease_expires_at = None
        await db.commit()
        return "retry"

    except Exception as exc:
        # BUG-17: Rollback any failed partial session state before updating job metadata
        await db.rollback()
        # Lỗi mạng hoặc lỗi tạm thời -> tăng attempt_count và retry backoff
        job_row, _, _ = await _reload_sync_entities(db, job_id, conn_id, None)
        if job_row:
            job_row.attempt_count += 1
            logger.warning(
                "[CalendarWorker] Error processing job %s (attempt %d): %s",
                job_id,
                job_row.attempt_count,
                exc,
            )
            if job_row.attempt_count >= 5:
                job_row.job_state = "failed"
                job_row.last_error_code = str(exc)[:120]
            else:
                job_row.job_state = "queued"
                job_row.next_retry_at = now_utc + timedelta(seconds=2 ** job_row.attempt_count)
                job_row.last_error_code = str(exc)[:120]
                job_row.lease_expires_at = None
            await db.commit()
        else:
            logger.warning("[CalendarWorker] Error processing job %s: %s", job_id, exc)
        return "retry"


async def process_calendar_sync_jobs(
    db: AsyncSession,
    user_id: UUID | str | None = None,
    limit: int = 10,
) -> int:
    """Quét và xử lý các sync jobs đang chờ trong hàng đợi."""
    now_utc = datetime.now(timezone.utc)
    stmt = (
        select(CalendarSyncJob)
        .where(
            CalendarSyncJob.job_state == "queued",
            CalendarSyncJob.attempt_count < 5,
            or_(
                CalendarSyncJob.next_retry_at.is_(None),
                CalendarSyncJob.next_retry_at <= now_utc,
            ),
            or_(
                CalendarSyncJob.lease_expires_at.is_(None),
                CalendarSyncJob.lease_expires_at <= now_utc,
            ),
        )
    )
    if user_id:
        uid = _resolve_uuid(user_id)
        stmt = stmt.where(CalendarSyncJob.user_id == uid)

    stmt = stmt.order_by(CalendarSyncJob.created_at.asc()).limit(limit)
    dialect_name = db.bind.dialect.name if getattr(db, "bind", None) else ""
    if dialect_name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    jobs = (await db.scalars(stmt)).all()
    if not jobs:
        return 0

    # Claim toàn bộ batch ngay lập tức để tránh tranh chấp / duplicate execution
    job_ids: list[UUID] = []
    for job in jobs:
        job.claimed_at = now_utc
        job.lease_expires_at = now_utc + timedelta(minutes=2)
        job_ids.append(job.id)
    await db.commit()

    cal_service = CalendarService(db)
    processed_count = 0

    for jid in job_ids:
        current_job = await db.get(CalendarSyncJob, jid)
        if current_job is None:
            continue
        await process_sync_job(db, current_job, cal_service)
        processed_count += 1

    return processed_count
