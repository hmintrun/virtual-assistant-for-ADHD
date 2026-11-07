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
from app.services.calendar_service import CalendarService
from app.services.google_calendar_client import CalendarConflictError, get_calendar_client

logger = logging.getLogger(__name__)


async def process_sync_job(
    db: AsyncSession,
    job: CalendarSyncJob,
    cal_service: CalendarService,
) -> str:
    """Xử lý một CalendarSyncJob đơn lẻ."""
    now_utc = datetime.now(timezone.utc)
    client = cal_service.client

    # 1. Kiểm tra CalendarConnection
    conn = await db.get(CalendarConnection, job.connection_id)
    if not conn or conn.connection_state != "connected":
        job.job_state = "failed"
        job.last_error_code = "CONNECTION_NOT_ACTIVE"
        await db.commit()
        return "failed"

    if not conn.write_calendar_id:
        job.job_state = "failed"
        job.last_error_code = "NO_WRITE_CALENDAR"
        await db.commit()
        return "failed"

    # 2. Lấy Access Token
    try:
        access_token = await cal_service.get_valid_access_token(conn)
    except Exception as exc:
        logger.warning("Token acquisition failed for job %s: %s", job.id, exc)
        job.job_state = "failed"
        job.last_error_code = "AUTH_FAILED"
        await db.commit()
        return "failed"

    # 3. Lấy Event Link tương ứng
    link: CalendarEventLink | None = None
    if job.event_link_id:
        link = await db.get(CalendarEventLink, job.event_link_id)

    # 4. Thực thi theo loại operation
    try:
        if job.operation == "create":
            res = await client.create_event(
                access_token=access_token,
                calendar_id=conn.write_calendar_id,
                event_payload=job.payload_snapshot,
            )
            if link:
                link.google_event_id = res.get("id")
                link.google_etag = res.get("etag")
                link.synced_schedule_revision = job.target_schedule_revision
                link.sync_state = "synced"
                link.last_synced_at = now_utc

            job.job_state = "completed"
            conn.last_successful_sync_at = now_utc
            await db.commit()
            return "completed"

        elif job.operation == "update":
            if not link or not link.google_event_id:
                job.job_state = "failed"
                job.last_error_code = "LINK_OR_EVENT_NOT_FOUND"
                await db.commit()
                return "failed"

            res = await client.update_event(
                access_token=access_token,
                calendar_id=conn.write_calendar_id,
                event_id=link.google_event_id,
                event_payload=job.payload_snapshot,
                expected_etag=job.expected_google_etag,
            )
            link.google_etag = res.get("etag")
            link.synced_schedule_revision = job.target_schedule_revision
            link.sync_state = "synced"
            link.last_synced_at = now_utc

            job.job_state = "completed"
            conn.last_successful_sync_at = now_utc
            await db.commit()
            return "completed"

        elif job.operation == "delete":
            if link and link.google_event_id:
                await client.delete_event(
                    access_token=access_token,
                    calendar_id=conn.write_calendar_id,
                    event_id=link.google_event_id,
                    expected_etag=job.expected_google_etag,
                )
                link.is_current = False
                link.sync_state = "deleted"
                link.last_synced_at = now_utc

            job.job_state = "completed"
            await db.commit()
            return "completed"

        else:
            job.job_state = "failed"
            job.last_error_code = f"UNKNOWN_OPERATION_{job.operation}"
            await db.commit()
            return "failed"

    except CalendarConflictError as exc:
        # Xung đột phiên bản (412 Precondition Failed / Etag mismatch)
        logger.warning("[CalendarWorker] 412 Conflict on job %s: %s", job.id, exc)
        job.job_state = "conflict"
        job.last_error_code = "ETAG_CONFLICT_412"
        if link:
            link.sync_state = "conflict"
        await db.commit()
        return "conflict"

    except Exception as exc:
        # Lỗi mạng hoặc lỗi tạm thời -> tăng attempt_count và retry backoff
        job.attempt_count += 1
        logger.warning(
            "[CalendarWorker] Error processing job %s (attempt %d): %s",
            job.id,
            job.attempt_count,
            exc,
        )
        if job.attempt_count >= 5:
            job.job_state = "failed"
            job.last_error_code = str(exc)[:120]
        else:
            job.job_state = "queued"
            job.next_retry_at = now_utc + timedelta(seconds=2 ** job.attempt_count)
            job.last_error_code = str(exc)[:120]

        await db.commit()
        return "retry"


async def process_calendar_sync_jobs(
    db: AsyncSession,
    limit: int = 10,
) -> int:
    """Quét và xử lý các sync jobs đang chờ trong hàng đợi."""
    now_utc = datetime.now(timezone.utc)
    stmt = (
        select(CalendarSyncJob)
        .where(
            CalendarSyncJob.job_state.in_(["queued", "failed"]),
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
        .order_by(CalendarSyncJob.created_at.asc())
        .limit(limit)
    )
    jobs = (await db.scalars(stmt)).all()
    if not jobs:
        return 0

    cal_service = CalendarService(db)
    processed_count = 0

    for job in jobs:
        job.claimed_at = now_utc
        job.lease_expires_at = now_utc + timedelta(minutes=2)
        await db.commit()

        await process_sync_job(db, job, cal_service)
        processed_count += 1

    return processed_count
