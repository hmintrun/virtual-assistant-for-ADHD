"""W3-15: retry đồng bộ phiên lên Google không tạo sự kiện trùng, và lỗi Google
không làm worker sập (rollback rồi đọc lại job/link bằng await)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.calendar import CalendarConnection, CalendarEventLink, CalendarSyncJob
from app.models.user import User
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_sync_job
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import CalendarClientError, CalendarConflictError


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        yield s
    await engine.dispose()


async def _create_job(db: AsyncSession):
    uid, conn_id, session_id = uuid4(), uuid4(), uuid4()
    conn = CalendarConnection(
        id=conn_id,
        user_id=uid,
        provider="google",
        account_ref="kai@example.com",
        read_calendar_ids=["kai@example.com"],
        write_calendar_id="kai@example.com",
        granted_scopes=[],
        connection_state="connected",
        credential_ref=encrypt_credentials(
            {"access_token": "valid", "user_id": str(uid), "connection_id": str(conn_id)}
        ),
    )
    link = CalendarEventLink(
        id=uuid4(),
        user_id=uid,
        session_id=session_id,
        connection_id=conn_id,
        google_calendar_id="kai@example.com",
        desired_schedule_revision=2,
        sync_state="pending",
        is_current=True,
    )
    event_id = f"session{session_id.hex}"
    job = CalendarSyncJob(
        id=uuid4(),
        user_id=uid,
        connection_id=conn_id,
        event_link_id=link.id,
        target_kind="session_event",
        target_ref=str(session_id),
        operation="create",
        target_schedule_revision=2,
        idempotency_key=f"job_{session_id.hex}",
        job_state="queued",
        attempt_count=0,
        payload_snapshot={"id": event_id, "summary": "ProPlan VA Focus Session"},
    )
    db.add_all([User(id=uid, email="kai@example.com", hashed_password="pw",
                     display_name="Kai", is_active=True), conn, link, job])
    await db.commit()
    return job, link, event_id


@pytest.mark.asyncio
async def test_create_retry_after_lost_response_is_not_duplicated(db: AsyncSession):
    job, link, event_id = await _create_job(db)
    service = CalendarService(db)

    async def already_exists(*args, **kwargs):
        raise CalendarConflictError("Event ID already exists on Google Calendar")

    service.client.create_event = already_exists
    assert await process_sync_job(db, job, service) == "completed"
    assert job.job_state == "completed"
    assert link.sync_state == "synced"
    assert link.google_event_id == event_id


@pytest.mark.asyncio
async def test_google_error_is_queued_for_retry_without_crash(db: AsyncSession):
    job, link, _ = await _create_job(db)
    service = CalendarService(db)

    async def google_down(*args, **kwargs):
        raise CalendarClientError("Google create event error: 503", status_code=503)

    service.client.create_event = google_down
    assert await process_sync_job(db, job, service) == "retry"
    assert job.job_state == "queued"
    assert job.attempt_count == 1
    assert link.sync_state == "pending"
