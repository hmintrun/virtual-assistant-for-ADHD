"""Tests for Phase 3 Non-AI fixes: Sync worker 409/Auth, unknown@ backfill, /health, ErrorEnvelope."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db
from app.core.database import make_engine
from app.core.security import issue_app_access_token
from app.main import app
from app.models import (
    Base,
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    Session,
    Step,
    Task,
    User,
)
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_sync_job
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import (
    CalendarConflictError,
    GoogleCalendarClient,
)


@pytest.mark.asyncio
async def test_create_event_409_recovers_etag_via_get_event_idempotently(monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    try:
        async with strict_factory() as db:
            uid = uuid4()
            db.add(User(id=uid, email="i05@example.com", display_name="I05", is_active=True))
            await db.flush()

            task_id = uuid4()
            step_id = uuid4()
            sess_id = uuid4()
            db.add(
                Task(
                    id=task_id,
                    user_id=uid,
                    revision=1,
                    title="T",
                    description="D",
                    scope="S",
                    time_constraint={"mode": "deadline", "at": "2026-11-20T17:00:00+07:00"},
                    status="scheduled",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                Step(
                    id=step_id,
                    user_id=uid,
                    task_id=task_id,
                    revision=1,
                    title="Step",
                    order_index=0,
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="scheduled",
                )
            )
            await db.flush()
            db.add(
                Session(
                    id=sess_id,
                    user_id=uid,
                    step_id=step_id,
                    revision=1,
                    scheduled_start=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
                    scheduled_end=datetime(2026, 11, 10, 9, 25, tzinfo=UTC),
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                )
            )
            await db.flush()

            conn_id = uuid4()
            conn = CalendarConnection(
                id=conn_id,
                user_id=uid,
                provider="google",
                account_ref="i05@gmail.com",
                read_calendar_ids=["primary"],
                write_calendar_id="focus_cal",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref="",
            )
            conn.credential_ref = encrypt_credentials(
                {
                    "access_token": "tok-123",
                    "refresh_token": "ref-123",
                    "expires_in": 3600,
                    "stored_at": datetime.now(UTC).isoformat(),
                    "user_id": str(uid),
                    "connection_id": str(conn_id),
                }
            )
            db.add(conn)
            await db.flush()

            link_id = uuid4()
            db.add(
                CalendarEventLink(
                    id=link_id,
                    user_id=uid,
                    connection_id=conn_id,
                    session_id=sess_id,
                    google_calendar_id="focus_cal",
                    desired_schedule_revision=1,
                    synced_schedule_revision=0,
                    sync_state="pending_create",
                    is_current=True,
                )
            )
            await db.flush()

            job_id = uuid4()
            det_id = "proplanfocus123456"
            db.add(
                CalendarSyncJob(
                    id=job_id,
                    user_id=uid,
                    connection_id=conn_id,
                    event_link_id=link_id,
                    target_kind="session",
                    target_ref=str(sess_id),
                    operation="create",
                    idempotency_key="idem-409-create",
                    job_state="queued",
                    attempt_count=0,
                    target_schedule_revision=1,
                    payload_snapshot={"id": det_id, "summary": "Focus"},
                )
            )
            await db.commit()

        async with strict_factory() as db:
            svc = CalendarService(db)

            async def raise_409(*args, **kwargs):
                raise CalendarConflictError("409 duplicate deterministic id")

            async def fetch_existing(access_token, calendar_id, event_id):
                assert event_id == det_id
                return {"id": det_id, "etag": '"recovered-etag-99"', "status": "confirmed"}

            monkeypatch.setattr(svc.client, "create_event", raise_409)
            monkeypatch.setattr(svc.client, "get_event", fetch_existing)

            job = await db.get(CalendarSyncJob, job_id)
            assert job is not None
            outcome = await process_sync_job(db, job, svc)
            assert outcome == "completed"

        async with strict_factory() as db:
            job = await db.get(CalendarSyncJob, job_id)
            link = await db.get(CalendarEventLink, link_id)
            assert job is not None and job.job_state == "completed"
            assert link is not None
            assert link.google_event_id == det_id
            assert link.google_etag == '"recovered-etag-99"'
            assert link.sync_state == "synced"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exchange_auth_code_falls_back_to_primary_calendar_and_list_backfills(monkeypatch):
    # 1. GoogleCalendarClient.exchange_auth_code falls back to calendarList/primary when userinfo lacks email
    class _Resp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data=None):
            return _Resp(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})

        async def get(self, url, headers=None):
            if "userinfo" in url:
                return _Resp(200, {})  # no email field
            if "calendarList/primary" in url:
                return _Resp(200, {"id": "fallback.user@gmail.com", "primary": True})
            return _Resp(404, {})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)
    client = GoogleCalendarClient("cid", "csec", "http://localhost/cb")
    tok = await client.exchange_auth_code("auth-code-123")
    assert tok["account_ref"] == "fallback.user@gmail.com"

    # 2. list_calendars backfills old unknown@google.com row from primary calendar id
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)
    try:
        async with strict_factory() as db:
            uid = uuid4()
            db.add(User(id=uid, email="unk@example.com", display_name="Unk", is_active=True))
            await db.flush()
            cid = uuid4()
            c = CalendarConnection(
                id=cid,
                user_id=uid,
                provider="google",
                account_ref="unknown@google.com",
                read_calendar_ids=[],
                write_calendar_id=None,
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref="",
            )
            c.credential_ref = encrypt_credentials(
                {
                    "access_token": "valid-at",
                    "expires_in": 3600,
                    "stored_at": datetime.now(UTC).isoformat(),
                    "user_id": str(uid),
                    "connection_id": str(cid),
                }
            )
            db.add(c)
            await db.commit()

        async with strict_factory() as db:
            svc = CalendarService(db)
            monkeypatch.setattr(
                svc.client,
                "list_calendar_list",
                AsyncMock(
                    return_value=[
                        {"id": "resolved.primary@gmail.com", "summary": "Main", "primary": True}
                    ]
                ),
            )
            res = await svc.list_calendars(uid, cid)
            assert res.account_ref == "resolved.primary@gmail.com"

        async with strict_factory() as db:
            updated = await db.get(CalendarConnection, cid)
            assert updated is not None
            assert updated.account_ref == "resolved.primary@gmail.com"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_health_endpoints_and_calendar_error_envelope(auth_config):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with sf() as session:
            yield session

    uid = uuid4()
    async with sf() as db:
        db.add(User(id=uid, email="health@example.com", display_name="Health", is_active=True))
        await db.commit()

    app.dependency_overrides[get_db] = override_get_db
    try:
        headers = {"Authorization": f"Bearer {issue_app_access_token(uid).access_token}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            h1 = await client.get("/api/v1/health")
            assert h1.status_code == 200, h1.text
            assert h1.json() == {"status": "ok", "database": "ok"}

            h2 = await client.get("/health")
            assert h2.status_code == 200, h2.text
            assert h2.json() == {"status": "ok", "database": "ok"}

            # Calendar 404 returns ErrorEnvelope (error.code + meta), NOT {"detail": ...}
            missing_conn = await client.get(
                f"/api/v1/calendar/connections/{uuid4()}",
                headers=headers,
            )
            assert missing_conn.status_code == 404
            body = missing_conn.json()
            assert "detail" not in body
            assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
            assert body["meta"]["request_id"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_calendars_unknown_backfill_skips_duplicate_email_and_worker_token_rollback(
    monkeypatch,
):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)
    try:
        uid = uuid4()
        cid_real = uuid4()
        cid_unk = uuid4()
        job_id = uuid4()
        async with strict_factory() as db:
            db.add(User(id=uid, email="dup_unk@example.com", display_name="Dup", is_active=True))
            await db.flush()
            db.add(
                CalendarConnection(
                    id=cid_real,
                    user_id=uid,
                    provider="google",
                    account_ref="dup.primary@gmail.com",
                    read_calendar_ids=[],
                    write_calendar_id=None,
                    granted_scopes=["https://www.googleapis.com/auth/calendar"],
                    connection_state="disconnected",
                    credential_ref="old-cred",
                )
            )
            unk_conn = CalendarConnection(
                id=cid_unk,
                user_id=uid,
                provider="google",
                account_ref="unknown@google.com",
                read_calendar_ids=[],
                write_calendar_id="proplan_sec",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref="",
            )
            unk_conn.credential_ref = encrypt_credentials(
                {
                    "access_token": "valid-at",
                    "expires_in": 3600,
                    "stored_at": datetime.now(UTC).isoformat(),
                    "user_id": str(uid),
                    "connection_id": str(cid_unk),
                }
            )
            db.add(unk_conn)
            await db.flush()
            db.add(
                CalendarSyncJob(
                    id=job_id,
                    user_id=uid,
                    connection_id=cid_unk,
                    target_kind="event",
                    target_ref="ev_rollback_1",
                    operation="create",
                    job_state="queued",
                    attempt_count=0,
                    target_schedule_revision=1,
                    idempotency_key="idem-rollback-1",
                    payload_snapshot={"summary": "Focus"},
                )
            )
            await db.commit()

        # 1. list_calendars không bị IntegrityError khi đã tồn tại row cùng email và không xóa row nào
        async with strict_factory() as db:
            svc = CalendarService(db)
            monkeypatch.setattr(
                svc.client,
                "list_calendar_list",
                AsyncMock(
                    return_value=[
                        {"id": "dup.primary@gmail.com", "summary": "Main", "primary": True}
                    ]
                ),
            )
            res = await svc.list_calendars(uid, cid_unk)
            assert res.account_ref == "unknown@google.com"
            total = await db.scalar(
                select(func.count())
                .select_from(CalendarConnection)
                .where(CalendarConnection.user_id == uid)
            )
            assert total == 2

        # 2. get_valid_access_token làm bẩn transaction rồi raise -> process_sync_job rollback an toàn trước khi retry
        async with strict_factory() as db:
            svc = CalendarService(db)

            async def dirty_then_fail(_conn):
                db.add(
                    CalendarConnection(
                        id=uuid4(),
                        user_id=uid,
                        provider="google",
                        account_ref="dup.primary@gmail.com",  # trùng UniqueConstraint
                        read_calendar_ids=[],
                        write_calendar_id=None,
                        granted_scopes=[],
                        connection_state="connected",
                        credential_ref="",
                    )
                )
                await db.flush()
                return "never-reached"

            monkeypatch.setattr(svc, "get_valid_access_token", dirty_then_fail)
            job = await db.get(CalendarSyncJob, job_id)
            assert job is not None
            outcome = await process_sync_job(db, job, svc)
            assert outcome == "retry"

        async with strict_factory() as db:
            job_after = await db.get(CalendarSyncJob, job_id)
            assert job_after is not None
            assert job_after.job_state == "queued"
            assert job_after.attempt_count == 1
    finally:
        await engine.dispose()
