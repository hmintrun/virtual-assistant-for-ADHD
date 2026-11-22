"""End-to-end API route coverage tests for Calendar v1 endpoints and Reminder receipt endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.database import make_engine, session_factory
from app.core.security import resolve_dev_current_user
from app.main import app
from app.models.base import Base
from app.models.calendar import CalendarConnection, CalendarSyncJob
from app.models.session import Session as SessionModel
from app.models.step import Step as StepModel
from app.models.system import Reminder
from app.models.task import Task as TaskModel
from app.models.user import User
from app.services.calendar_service import _resolve_uuid
from app.services.credential_crypto import encrypt_credentials
from app.services.draft_service import ensure_dev_seed_in_db
from app.services.google_calendar_client import (
    FakeCalendarClient,
    get_calendar_client,
    reset_fake_calendar_client,
)


@pytest.fixture
async def api():
    """Create isolated in-memory SQLite FastAPI client with fake calendar provider."""
    reset_fake_calendar_client()
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
        reset_fake_calendar_client()


def _make_connection(
    *,
    user_id,
    write_calendar_id: str | None = "primary",
) -> CalendarConnection:
    conn_id = uuid4()
    creds = {
        "access_token": "acc_cov_1",
        "refresh_token": "refresh_cov_1",
        "expires_in": 3600,
        "stored_at": datetime.now(UTC).isoformat(),
        "user_id": str(user_id),
        "connection_id": str(conn_id),
    }
    return CalendarConnection(
        id=conn_id,
        user_id=user_id,
        provider="google",
        account_ref="coverage_user@gmail.com",
        read_calendar_ids=["primary"],
        write_calendar_id=write_calendar_id,
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        connection_state="connected",
        credential_ref=encrypt_credentials(creds),
    )


async def _seed_user_and_session(
    db: AsyncSession,
) -> tuple[User, TaskModel, StepModel, SessionModel]:
    now = datetime.now(UTC).replace(microsecond=0)
    await ensure_dev_seed_in_db(db)
    dev_user = resolve_dev_current_user()
    user_uuid = _resolve_uuid(dev_user.user_id)
    user = await db.get(User, user_uuid)
    assert user is not None

    task = TaskModel(
        id=uuid4(),
        user_id=user.id,
        title="Route Coverage Task",
        time_constraint={
            "kind": "deadline",
            "deadline_at": (now + timedelta(days=2)).isoformat(),
        },
        status="scheduled",
    )
    step = StepModel(
        id=uuid4(),
        user_id=user.id,
        task_id=task.id,
        title="Coverage Step 1",
        order_index=0,
        estimated_base_seconds=1800,
        remaining_base_seconds=1800,
        buffer_multiplier=1.0,
        estimate_source="user_confirmed",
        status="pending",
    )
    sess = SessionModel(
        id=uuid4(),
        user_id=user.id,
        step_id=step.id,
        scheduled_start=now + timedelta(hours=2),
        scheduled_end=now + timedelta(hours=3),
        work_seconds=3000,
        rest_seconds=600,
        segments=[
            {"order_index": 0, "kind": "work", "duration_seconds": 3000},
            {"order_index": 1, "kind": "rest", "duration_seconds": 600},
        ],
        duration_seconds=3600,
        schedule_revision=1,
        status="scheduled",
        start_state="pending",
        start_decision_due_at=now + timedelta(hours=2, minutes=15),
    )
    db.add_all([task, step, sess])
    await db.flush()
    return user, task, step, sess


async def test_calendar_connection_and_event_routes_end_to_end(api):
    """Exercise GET/POST calendar connection, preview, events, refresh, and disconnect routes."""
    client, factory = api
    now = datetime.now(UTC).replace(microsecond=0)

    async with factory() as db:
        user, _, _, _ = await _seed_user_and_session(db)
        conn = _make_connection(user_id=user.id)
        db.add(conn)
        await db.commit()
        conn_id = str(conn.id)

    fake_client = get_calendar_client()
    assert isinstance(fake_client, FakeCalendarClient)
    fake_client.mock_events["primary"] = [
        {
            "id": "evt_cov_101",
            "summary": "Deep Work Sync",
            "start": {"dateTime": (now + timedelta(hours=1)).isoformat()},
            "end": {"dateTime": (now + timedelta(hours=2)).isoformat()},
            "transparency": "opaque",
            "status": "confirmed",
            "etag": '"etag_cov_101"',
        }
    ]

    # 1. GET /api/v1/calendar/connections
    list_resp = await client.get("/api/v1/calendar/connections")
    assert list_resp.status_code == 200, list_resp.text
    connections = list_resp.json()["data"]
    assert len(connections) == 1
    assert connections[0]["connection_id"] == conn_id
    assert connections[0]["connection_state"] == "connected"

    # 2. GET /api/v1/calendar/connections/{connection_id} (200 + 404)
    get_resp = await client.get(f"/api/v1/calendar/connections/{conn_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["data"]["connection_id"] == conn_id

    missing_conn_resp = await client.get(f"/api/v1/calendar/connections/{uuid4()}")
    assert missing_conn_resp.status_code == 404

    # 3. GET /api/v1/calendar/connections/{connection_id}/calendars
    cal_resp = await client.get(f"/api/v1/calendar/connections/{conn_id}/calendars")
    assert cal_resp.status_code == 200, cal_resp.text
    calendars = cal_resp.json()["data"]["items"]
    assert len(calendars) >= 1
    assert any(c["primary"] for c in calendars)

    # 4. POST /api/v1/calendar/connections/{connection_id}/configuration-previews
    preview_resp = await client.post(
        f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
        json={
            "read_calendar_ids": ["primary"],
            "write_calendar_id": "primary",
            "existing_event_action": "keep",
        },
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview_data = preview_resp.json()["data"]
    assert preview_data["connection_id"] == conn_id
    assert preview_data["read_calendar_ids"] == ["primary"]
    assert preview_data["write_calendar_id"] == "primary"
    assert preview_data["preview_id"]

    # 5. PATCH /api/v1/calendar/{connection_id}/selection
    sel_resp = await client.patch(
        f"/api/v1/calendar/{conn_id}/selection",
        json={
            "read_calendar_ids": ["primary", "work_cal_id"],
            "write_calendar_id": "primary",
        },
    )
    assert sel_resp.status_code == 200, sel_resp.text
    assert sel_resp.json()["data"]["read_calendar_ids"] == ["primary", "work_cal_id"]

    # 6. GET /api/v1/calendar/connections/{connection_id}/events
    events_resp = await client.get(
        f"/api/v1/calendar/connections/{conn_id}/events",
        params={"google_calendar_id": "primary"},
    )
    assert events_resp.status_code == 200, events_resp.text
    events = events_resp.json()["data"]
    assert len(events) == 1
    assert events[0]["google_event_id"] == "evt_cov_101"
    assert events[0]["title"] == "Deep Work Sync"

    # 7. GET /api/v1/calendar/connections/{connection_id}/events/{event_id} (200 + 404)
    event_resp = await client.get(
        f"/api/v1/calendar/connections/{conn_id}/events/evt_cov_101",
        params={"google_calendar_id": "primary"},
    )
    assert event_resp.status_code == 200, event_resp.text
    assert event_resp.json()["data"]["google_event_id"] == "evt_cov_101"

    missing_event_resp = await client.get(
        f"/api/v1/calendar/connections/{conn_id}/events/not_found",
        params={"google_calendar_id": "primary"},
    )
    assert missing_event_resp.status_code == 404

    # 8. POST /api/v1/calendar/connections/{connection_id}/refresh
    refresh_resp = await client.post(
        f"/api/v1/calendar/connections/{conn_id}/refresh",
        json={},
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    assert refresh_resp.json()["data"]["status"] == "refreshed"

    # 9. POST /api/v1/calendar/connections/{connection_id}/disconnect
    disc_resp = await client.post(f"/api/v1/calendar/connections/{conn_id}/disconnect")
    assert disc_resp.status_code == 200, disc_resp.text
    assert disc_resp.json()["data"]["connection_state"] == "disconnected"


async def test_calendar_sync_job_get_and_retry_routes(api):
    """Exercise GET /api/v1/calendar/sync-jobs/{job_id} and POST /api/v1/calendar/sync-jobs/{job_id}/retry."""
    client, factory = api

    async with factory() as db:
        user, _, _, sess = await _seed_user_and_session(db)
        conn = _make_connection(user_id=user.id, write_calendar_id="primary")
        db.add(conn)
        await db.flush()

        job = CalendarSyncJob(
            id=uuid4(),
            user_id=user.id,
            connection_id=conn.id,
            target_kind="session",
            target_ref=str(sess.id),
            operation="create",
            target_schedule_revision=1,
            payload_snapshot={"summary": "Focus Session"},
            idempotency_key=f"sync-cov-{uuid4().hex[:8]}",
            job_state="failed_retryable",
            attempt_count=2,
            last_error_code="TRANSIENT_NETWORK_ERROR",
        )
        db.add(job)
        await db.commit()
        job_id = str(job.id)

    # 1. GET /api/v1/calendar/sync-jobs/{job_id} (200 + 404)
    get_job_resp = await client.get(f"/api/v1/calendar/sync-jobs/{job_id}")
    assert get_job_resp.status_code == 200, get_job_resp.text
    job_data = get_job_resp.json()["data"]
    assert job_data["job_id"] == job_id
    assert job_data["job_state"] == "failed_retryable"
    assert job_data["attempt_count"] == 2

    missing_job_resp = await client.get(f"/api/v1/calendar/sync-jobs/{uuid4()}")
    assert missing_job_resp.status_code == 404

    # 2. POST /api/v1/calendar/sync-jobs/{job_id}/retry (200 + 404)
    retry_resp = await client.post(
        f"/api/v1/calendar/sync-jobs/{job_id}/retry",
        json={},
    )
    assert retry_resp.status_code == 200, retry_resp.text
    retried = retry_resp.json()["data"]
    assert retried["job_id"] == job_id
    assert retried["job_state"] == "completed"
    assert retried["last_error_code"] is None

    missing_retry_resp = await client.post(
        f"/api/v1/calendar/sync-jobs/{uuid4()}/retry",
        json={},
    )
    assert missing_retry_resp.status_code == 404


async def test_reminder_receipt_route_happy_path_idempotency_and_guards(api):
    """Exercise POST /api/v1/reminders/{reminder_id}/receipt including idempotent replay and 404/409 guards."""
    client, factory = api
    now = datetime.now(UTC).replace(microsecond=0)

    async with factory() as db:
        user, _, _, sess = await _seed_user_and_session(db)
        reminder = Reminder(
            id=uuid4(),
            user_id=user.id,
            session_id=sess.id,
            schedule_revision=sess.schedule_revision,
            kind="before_session",
            fire_at=sess.scheduled_start - timedelta(minutes=15),
            dedupe_key=f"{sess.id}:1:900:dev-android-1",
            delivery_state="scheduled",
            device_id="dev-android-1",
        )
        db.add(reminder)
        await db.commit()
        reminder_id = str(reminder.id)

    payload = {
        "device_id": "dev-android-1",
        "schedule_revision": 1,
        "delivery_state": "fired",
        "occurred_at": now.isoformat(),
        "user_action": "start_session",
    }

    # 1. Happy path 200
    resp1 = await client.post(
        f"/api/v1/reminders/{reminder_id}/receipt",
        headers={"Idempotency-Key": "rem-receipt-cov-1"},
        json=payload,
    )
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()["data"]
    assert data1["reminder_id"] == reminder_id
    assert data1["delivery_state"] == "fired"
    assert data1["user_action"] == "start_session"
    assert data1["device_id"] == "dev-android-1"

    # 2. Idempotent replay with same Idempotency-Key returns identical result
    resp_replay = await client.post(
        f"/api/v1/reminders/{reminder_id}/receipt",
        headers={"Idempotency-Key": "rem-receipt-cov-1"},
        json=payload,
    )
    assert resp_replay.status_code == 200, resp_replay.text
    assert resp_replay.json()["data"] == data1

    # 3. 409 REVISION_CONFLICT when schedule_revision mismatches
    conflict_resp = await client.post(
        f"/api/v1/reminders/{reminder_id}/receipt",
        headers={"Idempotency-Key": "rem-receipt-cov-conflict"},
        json={**payload, "schedule_revision": 99},
    )
    assert conflict_resp.status_code == 409, conflict_resp.text
    assert conflict_resp.json()["error"]["code"] == "REVISION_CONFLICT"

    # 4. 404 RESOURCE_NOT_FOUND for unknown reminder_id
    not_found_resp = await client.post(
        f"/api/v1/reminders/{uuid4()}/receipt",
        headers={"Idempotency-Key": "rem-receipt-cov-404"},
        json=payload,
    )
    assert not_found_resp.status_code == 404, not_found_resp.text
    assert not_found_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_calendar_auth_url_event_change_previews_and_schedule_routes(api):
    """Exercise GET /api/v1/calendar/auth-url, POST /api/v1/calendar/event-change-previews, confirm, and GET /api/v1/schedule."""
    client, factory = api
    now = datetime.now(UTC).replace(microsecond=0)

    async with factory() as db:
        user, _, _, sess = await _seed_user_and_session(db)
        conn = _make_connection(user_id=user.id, write_calendar_id="primary")
        db.add(conn)
        await db.commit()
        conn_id = str(conn.id)
        sess_id = str(sess.id)

    # 1. GET /api/v1/calendar/auth-url (backward-compatible hidden route)
    auth_url_resp = await client.get("/api/v1/calendar/auth-url")
    assert auth_url_resp.status_code == 200, auth_url_resp.text
    auth_url_data = auth_url_resp.json()["data"]
    assert "auth_url" in auth_url_data
    assert "state=" in auth_url_data["auth_url"]

    # 2. POST /api/v1/calendar/event-change-previews (200 create + 400 validation guard)
    ev_preview_resp = await client.post(
        "/api/v1/calendar/event-change-previews",
        json={
            "operation": "create",
            "connection_id": conn_id,
            "google_calendar_id": "primary",
            "payload": {
                "summary": "Planned Focus Block",
                "start": {"dateTime": (now + timedelta(hours=4)).isoformat()},
                "end": {"dateTime": (now + timedelta(hours=5)).isoformat()},
            },
        },
    )
    assert ev_preview_resp.status_code == 200, ev_preview_resp.text
    ev_preview_data = ev_preview_resp.json()["data"]
    preview_id = ev_preview_data["preview_id"]
    assert preview_id
    assert ev_preview_data["connection_id"] == conn_id
    assert ev_preview_data["kind"] == "event_change"
    assert ev_preview_data["operation"] == "create"
    assert ev_preview_data["status"] == "pending"

    invalid_preview_resp = await client.post(
        "/api/v1/calendar/event-change-previews",
        json={
            "operation": "update",
            "connection_id": conn_id,
            "google_calendar_id": "primary",
            "payload": {"summary": "Missing Event ID"},
        },
    )
    assert invalid_preview_resp.status_code == 400

    # 3. POST /api/v1/calendar/event-change-previews/{preview_id}/confirm (200 + idempotent replay + 404)
    confirm_resp = await client.post(
        f"/api/v1/calendar/event-change-previews/{preview_id}/confirm",
        json={"expected_preview_revision": 1},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    confirm_data = confirm_resp.json()["data"]
    assert confirm_data["job_id"]
    assert confirm_data["connection_id"] == conn_id
    assert confirm_data["target_kind"] == "event"
    assert confirm_data["operation"] == "create"
    assert confirm_data["job_state"] == "queued"

    confirm_replay_resp = await client.post(
        f"/api/v1/calendar/event-change-previews/{preview_id}/confirm",
        json={"expected_preview_revision": 1},
    )
    assert confirm_replay_resp.status_code == 200, confirm_replay_resp.text
    assert confirm_replay_resp.json()["data"]["job_id"] == confirm_data["job_id"]

    missing_confirm_resp = await client.post(
        f"/api/v1/calendar/event-change-previews/{uuid4()}/confirm",
        json={"expected_preview_revision": 1},
    )
    assert missing_confirm_resp.status_code == 404

    # 4. GET /api/v1/schedule (with timezone-aware from & to query params)
    sched_resp = await client.get(
        "/api/v1/schedule",
        params={
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=7)).isoformat(),
        },
    )
    assert sched_resp.status_code == 200, sched_resp.text
    sched_data = sched_resp.json()["data"]
    assert sched_data["schedule_revision"] >= 1
    assert "items" in sched_data
    assert "alarm_sync_state" in sched_data
    assert "calendar_sync_summary" in sched_data
    assert any(s["session_id"] == sess_id for s in sched_data["items"])

