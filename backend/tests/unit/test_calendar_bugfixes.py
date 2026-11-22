"""Regression tests for the 5 W3 Google Calendar backend bugfixes and Kai compatibility checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.core.database import make_engine, session_factory
from app.main import app
from app.models.base import Base
from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
)
from app.models.user import User
from app.services.ai_service import ai_service
from app.services.calendar_service import CalendarService, _resolve_uuid
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.credential_crypto import encrypt_credentials
from app.services.draft_service import ensure_dev_seed_in_db, resolve_user_uuid
from app.services.google_calendar_client import (
    CalendarAuthError,
    CalendarConflictError,
    FakeCalendarClient,
    GoogleCalendarClient,
    reset_fake_calendar_client,
)


@pytest.fixture
async def api(monkeypatch):
    reset_fake_calendar_client()
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    async def unavailable(**kwargs):
        raise ConnectionError("Providers unavailable in regression test")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", unavailable)
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
        reset_fake_calendar_client()


def _draft_summary() -> dict:
    now = datetime.now(UTC)
    return {
        "title": "Ôn tập kiến trúc hệ thống",
        "description": "Đọc tài liệu và tóm tắt",
        "scope": "Hoàn thành 5 trang tóm tắt",
        "time_constraint": {
            "kind": "deadline",
            "start_at": (now - timedelta(hours=1)).isoformat(),
            "deadline_at": (now + timedelta(days=5)).isoformat(),
            "input_timezone": "Asia/Ho_Chi_Minh",
        },
        "availability": {
            "timezone": "Asia/Ho_Chi_Minh",
            "weekly_rules": [
                {"weekday": i, "start_local": "00:00", "end_local": "23:59"} for i in range(1, 8)
            ],
        },
    }


async def _create_confirmed_draft(client: AsyncClient, key: str = "cal-bugfix") -> str:
    resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": f"draft-{key}"},
        json={"client_draft_id": key, "input_source": "text", "summary": _draft_summary()},
    )
    assert resp.status_code == 201, resp.text
    draft_id = resp.json()["data"]["draft_id"]
    conf = await client.post(
        f"/api/v1/drafts/{draft_id}/confirm-summary",
        headers={"Idempotency-Key": f"conf-{key}"},
        json={"expected_summary_revision": 1},
    )
    assert conf.status_code == 200, conf.text
    return draft_id


def _seed_connection(
    *,
    user_id,
    connection_state: str = "connected",
    read_calendar_ids: list[str] | None = None,
    write_calendar_id: str | None = None,
    stored_at: datetime | None = None,
    expires_in: int = 3600,
    refresh_token: str | None = "refresh_tok_123",
) -> CalendarConnection:
    conn_id = uuid4()
    creds = {
        "access_token": "acc_tok_123",
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "stored_at": (stored_at or datetime.now(UTC)).isoformat(),
        "user_id": str(user_id),
        "connection_id": str(conn_id),
    }
    return CalendarConnection(
        id=conn_id,
        user_id=user_id,
        provider="google",
        account_ref=f"user_{conn_id.hex[:6]}@gmail.com",
        read_calendar_ids=read_calendar_ids if read_calendar_ids is not None else ["primary"],
        write_calendar_id=write_calendar_id,
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        connection_state=connection_state,
        credential_ref=encrypt_credentials(creds),
    )


# ---------------------------------------------------------------------------
# Bug 1 + Kai compatibility: manual fallback vs disconnected / needs_reconnect
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["disconnected", "needs_reconnect"])
async def test_bug1_manual_fallback_allowed_when_connection_inactive(api, state: str):
    client, factory = api
    draft_id = await _create_confirmed_draft(client, key=f"bug1-{state}")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(
            user_id=uid,
            connection_state=state,
            read_calendar_ids=["primary"],
        )
        db.add(conn)
        await db.commit()

    resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": f"cand-bug1-{state}"},
        json={"source_summary_revision": 1, "source_planning_input_revision": 1},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["feasibility"]["status"] == "feasible"


async def test_bug1_manual_fallback_blocked_when_active_connected_with_read_calendars(api):
    client, factory = api
    draft_id = await _create_confirmed_draft(client, key="bug1-active")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["primary"],
        )
        db.add(conn)
        await db.commit()

    resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "cand-bug1-active"},
        json={"source_summary_revision": 1, "source_planning_input_revision": 1},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"


def test_kai_resolve_uuid_matches_draft_service():
    assert _resolve_uuid("user_a") == resolve_user_uuid("user_a")


# ---------------------------------------------------------------------------
# Bug 2 + Kai timedelta / complete-context compatibility
# ---------------------------------------------------------------------------
async def test_bug2_create_planning_context_refreshes_expired_token_without_nested_commit(
    api, monkeypatch
):
    client, factory = api
    draft_id = await _create_confirmed_draft(client, key="bug2-refresh")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
            stored_at=datetime.now(UTC) - timedelta(hours=2),
            expires_in=3600,
            refresh_token="refresh_valid_1",
        )
        db.add(conn)
        await db.commit()
        conn_id = str(conn.id)

    refresh_calls = 0
    original_refresh = FakeCalendarClient.refresh_access_token

    async def spy_refresh(self, refresh_token: str):
        nonlocal refresh_calls
        refresh_calls += 1
        return await original_refresh(self, refresh_token)

    monkeypatch.setattr(FakeCalendarClient, "refresh_access_token", spy_refresh)

    now = datetime.now(UTC)
    ctx_resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "ctx-bug2-refresh"},
        json={
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": conn_id, "google_calendar_id": "mock_user@gmail.com"}
            ],
        },
    )
    assert ctx_resp.status_code == 201, ctx_resp.text
    ctx_data = ctx_resp.json()["data"]
    assert ctx_data["coverage_state"] == "complete"
    assert refresh_calls == 1
    assert len(ctx_data["busy_intervals"]) >= 1

    # Also verify candidate generation accepts complete Google coverage_state (Kai tweak #3)
    cand_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "cand-bug2-refresh"},
        json={
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "calendar_context_ref": {
                "context_id": ctx_data["context_id"],
                "revision": ctx_data["revision"],
            },
        },
    )
    assert cand_resp.status_code == 201, cand_resp.text


async def test_bug2_create_planning_context_auth_failure_persists_needs_reconnect(
    api, monkeypatch
):
    client, factory = api
    draft_id = await _create_confirmed_draft(client, key="bug2-auth-fail")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
            stored_at=datetime.now(UTC) - timedelta(hours=2),
            expires_in=3600,
            refresh_token="refresh_revoked",
        )
        db.add(conn)
        await db.commit()
        conn_id = conn.id

    async def fail_refresh(self, refresh_token: str):
        raise CalendarAuthError("invalid_grant")

    monkeypatch.setattr(FakeCalendarClient, "refresh_access_token", fail_refresh)

    now = datetime.now(UTC)
    ctx_resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "ctx-bug2-fail"},
        json={
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": str(conn_id), "google_calendar_id": "mock_user@gmail.com"}
            ],
        },
    )
    assert ctx_resp.status_code == 503, ctx_resp.text
    assert ctx_resp.json()["error"]["code"] == "CALENDAR_UNAVAILABLE"

    async with factory() as db:
        refreshed_conn = await db.get(CalendarConnection, conn_id)
        assert refreshed_conn is not None
        assert refreshed_conn.connection_state == "needs_reconnect"


async def test_bug2_partial_excluded_manual_fallback_context_works_for_planning(api):
    client, factory = api
    draft_id = await _create_confirmed_draft(client, key="bug2-partial-fallback")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com", "broken_cal@group.calendar.google.com"],
        )
        db.add(conn)
        await db.commit()
        conn_id = str(conn.id)

    now = datetime.now(UTC)
    ctx_resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "ctx-bug2-partial"},
        json={
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": conn_id, "google_calendar_id": "mock_user@gmail.com"},
                {
                    "connection_id": conn_id,
                    "google_calendar_id": "broken_cal@group.calendar.google.com",
                },
            ],
            "excluded_sources": [
                {
                    "connection_id": conn_id,
                    "google_calendar_id": "broken_cal@group.calendar.google.com",
                    "reason": "Lịch lỗi tạm thời",
                    "user_confirmed_at": (now - timedelta(seconds=5)).isoformat(),
                }
            ],
        },
    )
    assert ctx_resp.status_code == 201, ctx_resp.text
    ctx_data = ctx_resp.json()["data"]
    assert ctx_data["coverage_state"] == "manual_fallback"

    cand_resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "cand-bug2-partial"},
        json={
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "calendar_context_ref": {
                "context_id": ctx_data["context_id"],
                "revision": ctx_data["revision"],
            },
        },
    )
    assert cand_resp.status_code == 201, cand_resp.text


# ---------------------------------------------------------------------------
# Bug 3: CalendarSyncWorker post-rollback expired ORM access (MissingGreenlet)
# ---------------------------------------------------------------------------
async def test_bug3_worker_post_rollback_does_not_raise_missing_greenlet(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # expire_on_commit=True simulates strict SQLAlchemy expiration on commit/rollback
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    try:
        async with strict_factory() as db:
            uid = uuid4()
            user = User(
                id=uid,
                email="worker_bug3@example.com",
                hashed_password="pw",
                display_name="Worker Bug3",
                is_active=True,
            )
            conn = _seed_connection(
                user_id=uid,
                connection_state="connected",
                write_calendar_id="focus_cal",
            )
            link = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=uuid4(),
                connection_id=conn.id,
                google_calendar_id="focus_cal",
                google_event_id="evt_1",
                google_etag='"etag_1"',
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            job_conflict = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                event_link_id=link.id,
                target_kind="session",
                target_ref="sess_1",
                operation="update",
                idempotency_key="bug3_conflict",
                expected_google_etag='"etag_1"',
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "Test"},
            )
            job_auth = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                target_kind="session",
                target_ref="sess_2",
                operation="create",
                idempotency_key="bug3_auth",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "Test Auth"},
            )
            job_err = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                target_kind="session",
                target_ref="sess_3",
                operation="create",
                idempotency_key="bug3_err",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "Test Err"},
            )
            conflict_id = job_conflict.id
            auth_id = job_auth.id
            err_id = job_err.id
            conn_id = conn.id
            link_id = link.id
            db.add_all([user, conn, link, job_conflict, job_auth, job_err])
            await db.commit()

        # 1. Conflict path under expire_on_commit=True
        async with strict_factory() as db:
            cal_service = CalendarService(db)

            async def fail_conflict(*args, **kwargs):
                raise CalendarConflictError("412")

            monkeypatch.setattr(cal_service.client, "update_event", fail_conflict)
            j = await db.get(CalendarSyncJob, conflict_id)
            assert j is not None
            assert await process_sync_job(db, j, cal_service) == "conflict"

        async with strict_factory() as db:
            j = await db.get(CalendarSyncJob, conflict_id)
            l_row = await db.get(CalendarEventLink, link_id)
            assert j is not None and j.job_state == "conflict"
            assert l_row is not None and l_row.sync_state == "conflict"

        # 2. Generic exception path under expire_on_commit=True
        async with strict_factory() as db:
            cal_service = CalendarService(db)

            async def fail_network(*args, **kwargs):
                raise RuntimeError("Transient 503")

            with monkeypatch.context() as mp:
                mp.setattr(cal_service.client, "create_event", fail_network)
                j = await db.get(CalendarSyncJob, err_id)
                assert j is not None
                assert await process_sync_job(db, j, cal_service) == "retry"

        async with strict_factory() as db:
            j = await db.get(CalendarSyncJob, err_id)
            assert j is not None
            assert j.job_state == "queued"
            assert j.attempt_count == 1
            assert j.last_error_code == "Transient 503"

        # 3. Auth error path under expire_on_commit=True
        async with strict_factory() as db:
            cal_service = CalendarService(db)

            async def fail_auth(*args, **kwargs):
                raise CalendarAuthError("Token revoked")

            with monkeypatch.context() as mp:
                mp.setattr(cal_service.client, "create_event", fail_auth)
                j = await db.get(CalendarSyncJob, auth_id)
                assert j is not None
                assert await process_sync_job(db, j, cal_service) == "failed"

        async with strict_factory() as db:
            j = await db.get(CalendarSyncJob, auth_id)
            c = await db.get(CalendarConnection, conn_id)
            assert j is not None and j.attempt_count == 5
            assert j.job_state == "failed"
            assert j.lease_expires_at is None
            assert j.last_error_code == "AUTH_EXPIRED"
            assert c is not None and c.connection_state == "needs_reconnect"

        # 4. Batch runner: job 1 rolls back, job 2 still processes cleanly
        async with strict_factory() as db:
            c = await db.get(CalendarConnection, conn_id)
            assert c is not None
            c.connection_state = "connected"
            j_fail = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                target_kind="session",
                target_ref="batch_fail",
                operation="create",
                idempotency_key="bug3_batch_fail",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "FAIL_JOB"},
            )
            j_ok = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                target_kind="session",
                target_ref="batch_ok",
                operation="create",
                idempotency_key="bug3_batch_ok",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "OK_JOB"},
            )
            # Clear retry timers on earlier jobs so only batch jobs run
            for old_id in (auth_id, err_id):
                old_j = await db.get(CalendarSyncJob, old_id)
                if old_j:
                    old_j.job_state = "failed"
            j_fail_id, j_ok_id = j_fail.id, j_ok.id
            db.add_all([j_fail, j_ok])
            await db.commit()

        async with strict_factory() as db:
            from app.services import google_calendar_client as gcc

            fake_client = gcc.get_calendar_client()
            orig_create = fake_client.create_event

            async def selective_create(access_token, calendar_id, event_payload):
                if event_payload.get("summary") == "FAIL_JOB":
                    raise RuntimeError("boom")
                return await orig_create(access_token, calendar_id, event_payload)

            with monkeypatch.context() as mp:
                mp.setattr(fake_client, "create_event", selective_create)
                processed = await process_calendar_sync_jobs(db, user_id=uid, limit=10)
            assert processed == 2

        async with strict_factory() as db:
            jf = await db.get(CalendarSyncJob, j_fail_id)
            jo = await db.get(CalendarSyncJob, j_ok_id)
            assert jf is not None and jf.attempt_count == 1
            assert jo is not None and jo.job_state == "completed"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Bug 4: OAuth callback redirects to deep-link on error/denial (not 422)
# ---------------------------------------------------------------------------
async def test_bug4_oauth_callback_error_redirects_to_deep_link(api):
    client, factory = api

    auth_resp = await client.post(
        "/api/v1/calendar/authorizations",
        json={"device_id": "android-1", "return_target": "proplan://calendar/connected?tab=sync"},
    )
    assert auth_resp.status_code == 200, auth_resp.text
    auth_id = auth_resp.json()["data"]["authorization_id"]
    state = parse_qs(urlparse(auth_resp.json()["data"]["auth_url"]).query)["state"][0]

    cb = await client.get(
        "/api/v1/calendar/oauth/callback",
        params={"error": "access_denied", "error_description": "User cancelled", "state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302, cb.text
    loc = urlparse(cb.headers["location"])
    assert loc.scheme == "proplan"
    assert loc.netloc == "calendar"
    assert loc.path == "/connected"
    qs = parse_qs(loc.query)
    assert qs["error"] == ["access_denied"]
    assert qs["error_description"] == ["User cancelled"]
    assert qs["tab"] == ["sync"]

    # State was consumed and cannot be replayed
    replay = await client.get(
        "/api/v1/calendar/oauth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert replay.status_code == 401

    async with factory() as db:
        from uuid import UUID

        rec = await db.get(CalendarAuthorization, UUID(auth_id))
        assert rec is not None and rec.consumed_at is not None

    # Default return_target + legacy endpoint + missing code without error
    auth2 = await client.post("/api/v1/calendar/authorizations", json={"device_id": "android-2"})
    state2 = parse_qs(urlparse(auth2.json()["data"]["auth_url"]).query)["state"][0]
    cb2 = await client.get(
        "/api/v1/calendar/oauth2callback",
        params={"state": state2},
        follow_redirects=False,
    )
    assert cb2.status_code == 302, cb2.text
    assert cb2.headers["location"] == "proplan://calendar/connected?error=missing_code"


# ---------------------------------------------------------------------------
# Bug 5: URL-escaped calendar_id/event_id & confirm_configuration_preview guard
# ---------------------------------------------------------------------------
async def test_bug5_google_calendar_client_escapes_calendar_and_event_ids(monkeypatch):
    seen_requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.method, str(request.url)))
        assert request.url.fragment == ""
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"items": [], "id": "evt/123#part", "etag": '"1"'})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_async_client)

    client = GoogleCalendarClient("cid", "csec", "http://localhost/cb")
    cal_id = "vi.vietnamese#holiday@group.v.calendar.google.com"
    evt_id = "evt/123#part"
    now = datetime.now(UTC)

    await client.list_events("tok", cal_id, now, now + timedelta(days=1))
    await client.get_event("tok", cal_id, evt_id)
    await client.create_event("tok", cal_id, {"summary": "Hi"})
    await client.update_event("tok", cal_id, evt_id, {"summary": "Upd"}, expected_etag='"1"')
    await client.delete_event("tok", cal_id, evt_id, expected_etag='"1"')

    encoded_cal = "vi.vietnamese%23holiday%40group.v.calendar.google.com"
    encoded_evt = "evt%2F123%23part"
    assert len(seen_requests) == 5
    for _, url in seen_requests:
        assert encoded_cal in url
        assert "#" not in url
    assert seen_requests[1][1].endswith(f"/calendars/{encoded_cal}/events/{encoded_evt}")
    assert seen_requests[3][1].endswith(f"/calendars/{encoded_cal}/events/{encoded_evt}")
    assert seen_requests[4][1].endswith(f"/calendars/{encoded_cal}/events/{encoded_evt}")


@pytest.mark.parametrize("state", ["disconnected", "needs_reconnect"])
async def test_bug5_confirm_configuration_preview_rejects_inactive_connection(api, state: str):
    client, factory = api

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(user_id=uid, connection_state="connected", read_calendar_ids=[])
        db.add(conn)
        await db.commit()
        conn_id = conn.id

    preview_resp = await client.post(
        f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
        json={
            "read_calendar_ids": ["mock_user@gmail.com"],
            "write_calendar_id": "provision_new",
            "existing_event_action": "provision_new",
        },
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview_id = preview_resp.json()["data"]["preview_id"]

    async with factory() as db:
        conn = await db.get(CalendarConnection, conn_id)
        assert conn is not None
        conn.connection_state = state
        await db.commit()

    confirm_resp = await client.post(
        f"/api/v1/calendar/configuration-previews/{preview_id}/confirm",
        json={"expected_preview_revision": 1},
    )
    assert confirm_resp.status_code == 409, confirm_resp.text


# ---------------------------------------------------------------------------
# Follow-up QA fixes: disconnected connection guards & legacy selected_calendar_ids
# ---------------------------------------------------------------------------
async def test_disconnected_connection_does_not_refresh_or_resurrect_in_get_valid_access_token(
    api, monkeypatch
):
    _, factory = api
    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _seed_connection(
            user_id=uid,
            connection_state="disconnected",
            stored_at=datetime.now(UTC) - timedelta(hours=2),
            expires_in=3600,
            refresh_token="refresh_should_not_run",
        )
        db.add(conn)
        await db.commit()
        conn_id = conn.id

    refresh_called = False

    async def fail_if_called(self, refresh_token: str):
        nonlocal refresh_called
        refresh_called = True
        return {"access_token": "oops", "expires_in": 3600}

    monkeypatch.setattr(FakeCalendarClient, "refresh_access_token", fail_if_called)

    async with factory() as db:
        service = CalendarService(db)
        loaded = await db.get(CalendarConnection, conn_id)
        assert loaded is not None
        with pytest.raises(CalendarAuthError):
            await service.get_valid_access_token(loaded)
        await db.refresh(loaded)
        assert loaded.connection_state == "disconnected"
        assert refresh_called is False


async def test_create_planning_context_rejects_disconnected_and_midflight_toctou(
    api, monkeypatch
):
    client, factory = api
    draft_id = await _create_confirmed_draft(client, key="qa-followup-disc")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        disconnected_conn = _seed_connection(
            user_id=uid,
            connection_state="disconnected",
            read_calendar_ids=["mock_user@gmail.com"],
        )
        active_conn = _seed_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
        )
        db.add_all([disconnected_conn, active_conn])
        await db.commit()
        disc_id = str(disconnected_conn.id)
        active_id = active_conn.id

    now = datetime.now(UTC)
    disc_resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "ctx-disc-1"},
        json={
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": disc_id, "google_calendar_id": "mock_user@gmail.com"}
            ],
        },
    )
    assert disc_resp.status_code == 409, disc_resp.text
    assert disc_resp.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"

    # Simulate concurrent disconnect between Phase 1 and Phase 3 during Google HTTP
    orig_list_events = FakeCalendarClient.list_events

    async def disconnect_midflight(self, access_token, calendar_id, time_min, time_max):
        async with factory() as other_db:
            c = await other_db.get(CalendarConnection, active_id)
            assert c is not None
            c.connection_state = "disconnected"
            await other_db.commit()
        return await orig_list_events(self, access_token, calendar_id, time_min, time_max)

    monkeypatch.setattr(FakeCalendarClient, "list_events", disconnect_midflight)

    toctou_resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "ctx-disc-toctou"},
        json={
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": str(active_id), "google_calendar_id": "mock_user@gmail.com"}
            ],
        },
    )
    assert toctou_resp.status_code == 409, toctou_resp.text
    assert toctou_resp.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"


async def test_legacy_selected_calendar_ids_with_manual_only_rejected(api):
    from app.models.calendar import PlanningContext
    from app.services.draft_service import resolve_draft_uuid

    client, factory = api
    draft_id = await _create_confirmed_draft(client, key="qa-followup-legacy-ids")
    now = datetime.now(UTC)

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        ctx = PlanningContext(
            id=uuid4(),
            user_id=uid,
            availability_id=None,
            availability_revision=None,
            source_draft_id=resolve_draft_uuid(draft_id),
            source_summary_revision=1,
            availability_snapshot=_draft_summary()["availability"],
            query_start=now - timedelta(days=1),
            query_end=now + timedelta(days=14),
            fetched_at=now,
            sync_state="valid",
            coverage_state="manual_only",
            selected_calendar_ids=["legacy_cal@gmail.com"],
            selected_calendar_sources=[],
            busy_intervals=[],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
        )
        db.add(ctx)
        await db.commit()
        ctx_id = str(ctx.id)

    resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "cand-legacy-ids"},
        json={
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "calendar_context_ref": {"context_id": ctx_id, "revision": 1},
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"


async def test_finding3_same_link_queued_update_waits_when_create_hits_transient_error(
    monkeypatch,
):
    """PR #22 Finding #3: j1 (create) & j2 (update) cùng event_link_id; j1 lỗi mạng tạm thời -> j2 chưa bị claim/execute."""
    from app.models.session import Session as SessionRow
    from app.models.step import Step as StepRow
    from app.models.task import Task as TaskRow

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    try:
        uid = uuid4()
        now = datetime.now(UTC)
        async with strict_factory() as db:
            db.add(User(id=uid, email="f3_link@example.com", display_name="F3", is_active=True))
            await db.flush()
            conn = _seed_connection(user_id=uid, write_calendar_id="focus_cal")
            db.add(conn)
            await db.flush()
            task = TaskRow(
                id=uuid4(),
                user_id=uid,
                title="T",
                time_constraint={},
                decomposition_state="complete",
            )
            db.add(task)
            await db.flush()
            step = StepRow(
                id=uuid4(),
                user_id=uid,
                task_id=task.id,
                title="S",
                order_index=0,
                estimated_base_seconds=1500,
                remaining_base_seconds=1500,
                buffer_multiplier=1.0,
                estimate_source="user_confirmed",
            )
            db.add(step)
            await db.flush()
            sess = SessionRow(
                id=uuid4(),
                user_id=uid,
                step_id=step.id,
                scheduled_start=now + timedelta(hours=1),
                scheduled_end=now + timedelta(hours=1, minutes=30),
                micro_task_text="M",
                work_seconds=1500,
                rest_seconds=300,
                segments=[],
                duration_seconds=1800,
                schedule_revision=1,
                status="scheduled",
                start_state="not_started",
                start_decision_due_at=now + timedelta(hours=1, minutes=15),
            )
            db.add(sess)
            await db.flush()
            link = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=sess.id,
                connection_id=conn.id,
                google_calendar_id="focus_cal",
                google_event_id=None,
                desired_schedule_revision=2,
                sync_state="pending",
                is_current=True,
            )
            db.add(link)
            await db.flush()

            j1 = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                event_link_id=link.id,
                target_kind="session_event",
                target_ref=str(sess.id),
                operation="create",
                target_schedule_revision=1,
                payload_snapshot={"summary": "Focus 1"},
                idempotency_key="f3-create-1",
                job_state="queued",
                attempt_count=0,
                created_at=now - timedelta(seconds=10),
            )
            j2 = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                event_link_id=link.id,
                target_kind="session_event",
                target_ref=str(sess.id),
                operation="update",
                target_schedule_revision=2,
                payload_snapshot={"summary": "Focus 1 moved"},
                idempotency_key="f3-update-2",
                job_state="queued",
                attempt_count=0,
                created_at=now - timedelta(seconds=5),
            )
            db.add_all([j1, j2])
            j1_id, j2_id, link_id = j1.id, j2.id, link.id
            await db.commit()

        from app.services import google_calendar_client as gcc

        fake_client = gcc.get_calendar_client()
        orig_create = fake_client.create_event

        async def _transient_create(*args, **kwargs):
            raise RuntimeError("transient network failure")

        monkeypatch.setattr(fake_client, "create_event", _transient_create)

        async with strict_factory() as db:
            processed = await process_calendar_sync_jobs(db, user_id=uid, limit=10)
        assert processed == 1

        # Poll lần 2 ngay trong lúc j1 đang đợi backoff: j2 vẫn KHÔNG được vượt mặt j1!
        async with strict_factory() as db:
            processed_during_backoff = await process_calendar_sync_jobs(db, user_id=uid, limit=10)
        assert processed_during_backoff == 0

        async with strict_factory() as db:
            r1 = await db.get(CalendarSyncJob, j1_id)
            r2 = await db.get(CalendarSyncJob, j2_id)
            assert r1 is not None and r1.job_state == "queued" and r1.attempt_count == 1
            assert r2 is not None and r2.job_state == "queued" and r2.attempt_count == 0
            assert r2.last_error_code is None
            # Cho phép j1 hết backoff và khôi phục mạng -> j1 hoàn tất rồi j2 chạy tiếp thành công
            r1.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        monkeypatch.setattr(fake_client, "create_event", orig_create)
        async with strict_factory() as db:
            assert await process_calendar_sync_jobs(db, user_id=uid, limit=10) == 2
        async with strict_factory() as db:
            r1_ok = await db.get(CalendarSyncJob, j1_id)
            r2_ok = await db.get(CalendarSyncJob, j2_id)
            lk_ok = await db.get(CalendarEventLink, link_id)
            assert r1_ok is not None and r1_ok.job_state == "completed"
            assert r2_ok is not None and r2_ok.job_state == "completed"
            assert lk_ok is not None and lk_ok.sync_state == "synced"
    finally:
        reset_fake_calendar_client()
        await engine.dispose()


async def test_p3_1_refcounted_calendar_locks_cleanup_after_release():
    """PR #22 P3.1: _OWNER_CALENDAR_LOCKS & _CONNECTION_SYNC_LOCKS tự dọn sạch sau khi giải phóng."""
    import asyncio

    from app.services.calendar_service import _OWNER_CALENDAR_LOCKS, get_owner_calendar_lock
    from app.services.calendar_sync_worker import (
        _CONNECTION_SYNC_LOCKS,
        _busy_connection_ids_for_current_loop,
        get_connection_sync_lock,
    )

    _OWNER_CALENDAR_LOCKS.clear()
    _CONNECTION_SYNC_LOCKS.clear()
    owner_id = f"owner-{uuid4().hex}"
    conn_id = uuid4()
    events: list[str] = []

    async def _owner_worker(tag: str, hold: float) -> None:
        async with get_owner_calendar_lock(owner_id):
            events.append(f"enter-{tag}")
            await asyncio.sleep(hold)
            events.append(f"exit-{tag}")

    await asyncio.gather(_owner_worker("a", 0.03), _owner_worker("b", 0.01))
    assert events in (
        ["enter-a", "exit-a", "enter-b", "exit-b"],
        ["enter-b", "exit-b", "enter-a", "exit-a"],
    )
    assert _OWNER_CALENDAR_LOCKS == {}

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _conn_holder() -> None:
        async with get_connection_sync_lock(conn_id):
            entered.set()
            await release.wait()

    async def _conn_waiter() -> None:
        await entered.wait()
        assert conn_id in _busy_connection_ids_for_current_loop()
        release.set()
        async with get_connection_sync_lock(conn_id):
            pass

    await asyncio.gather(_conn_holder(), _conn_waiter())
    assert _busy_connection_ids_for_current_loop() == []
    assert _CONNECTION_SYNC_LOCKS == {}
