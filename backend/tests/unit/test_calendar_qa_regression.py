"""Phase 2 QA/QC adversarial and edge-case regression tests for Google Calendar bugfixes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db
from app.core.database import make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models.base import Base
from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.planning import PlanningDraft as DraftRow
from app.models.session import Session as SessionModel
from app.models.step import Step as StepModel
from app.models.task import Task as TaskModel
from app.models.user import User
from app.schemas.calendar import CalendarSource, PlanningContextCreateRequest
from app.services import plan_service
from app.services.ai_service import ai_service
from app.services.calendar_service import CalendarService, _hash_state, _resolve_uuid
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.credential_crypto import decrypt_credentials, encrypt_credentials
from app.services.draft_service import ensure_dev_seed_in_db, resolve_draft_uuid, resolve_user_uuid
from app.services.google_calendar_client import (
    CalendarAuthError,
    CalendarConflictError,
    FakeCalendarClient,
    GoogleCalendarClient,
    reset_fake_calendar_client,
)
from app.services.planning.calendar_ingestion import ingest_calendar_sources


@pytest.fixture
async def api(monkeypatch):
    """Create isolated in-memory SQLite FastAPI client with offline AI fallback."""
    reset_fake_calendar_client()
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    async def unavailable(**kwargs):
        raise ConnectionError("Providers unavailable in QA regression test")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", unavailable)
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory, engine
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
        reset_fake_calendar_client()


def _draft_summary() -> dict:
    now = datetime.now(UTC)
    return {
        "title": "QA Regression Task",
        "description": "Verify calendar planning and sync edge cases",
        "scope": "Complete 3 focused verification steps",
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


async def _create_confirmed_draft(client: AsyncClient, key: str) -> str:
    resp = await client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": f"qa-draft-{key}"},
        json={"client_draft_id": key, "input_source": "text", "summary": _draft_summary()},
    )
    assert resp.status_code == 201, resp.text
    draft_id = resp.json()["data"]["draft_id"]
    conf = await client.post(
        f"/api/v1/drafts/{draft_id}/confirm-summary",
        headers={"Idempotency-Key": f"qa-conf-{key}"},
        json={"expected_summary_revision": 1},
    )
    assert conf.status_code == 200, conf.text
    return draft_id


def _make_connection(
    *,
    user_id,
    account_ref: str | None = None,
    connection_state: str = "connected",
    read_calendar_ids: list[str] | None = None,
    write_calendar_id: str | None = None,
    stored_at: datetime | None = None,
    expires_in: int = 3600,
    refresh_token: str | None = "refresh_qa_1",
) -> CalendarConnection:
    conn_id = uuid4()
    creds = {
        "access_token": "acc_qa_1",
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
        account_ref=account_ref or f"qa_{conn_id.hex[:6]}@gmail.com",
        read_calendar_ids=read_calendar_ids if read_calendar_ids is not None else ["primary"],
        write_calendar_id=write_calendar_id,
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        connection_state=connection_state,
        credential_ref=encrypt_credentials(creds),
    )


async def _seed_task_step_session(db: AsyncSession, user_id) -> tuple[TaskModel, StepModel, SessionModel]:
    now = datetime.now(UTC)
    task = TaskModel(
        id=uuid4(),
        user_id=user_id,
        title="QA Sync Task",
        time_constraint={"kind": "deadline", "deadline_at": (now + timedelta(days=2)).isoformat()},
        status="scheduled",
    )
    step = StepModel(
        id=uuid4(),
        user_id=user_id,
        task_id=task.id,
        title="Step 1",
        order_index=0,
        estimated_base_seconds=1800,
        remaining_base_seconds=1800,
        buffer_multiplier=1.0,
        estimate_source="user_confirmed",
        status="pending",
    )
    sess = SessionModel(
        id=uuid4(),
        user_id=user_id,
        step_id=step.id,
        scheduled_start=now + timedelta(hours=2),
        scheduled_end=now + timedelta(hours=3),
        work_seconds=3000,
        rest_seconds=600,
        segments=[{"kind": "work", "seconds": 3000}],
        duration_seconds=3600,
        schedule_revision=1,
        status="scheduled",
        start_state="waiting",
    )
    db.add_all([task, step, sess])
    await db.flush()
    return task, step, sess


# ---------------------------------------------------------------------------
# Bug 1: _sources, _resolve_uuid, explicit context_ref & coverage_state checks
# ---------------------------------------------------------------------------
async def test_qa_bug1_connected_with_empty_read_calendars_allows_manual_fallback(api):
    client, factory, _ = api
    draft_id = await _create_confirmed_draft(client, "qa-bug1-empty-read")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        db.add(
            _make_connection(
                user_id=uid,
                connection_state="connected",
                read_calendar_ids=[],
            )
        )
        await db.commit()

    resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "qa-cand-empty-read"},
        json={"source_summary_revision": 1, "source_planning_input_revision": 1},
    )
    assert resp.status_code == 201, resp.text
    ctx_id = resp.json()["data"]["source_revisions"]["calendar_context"]["context_id"]
    ctx_resp = await client.get(f"/api/v1/planning-contexts/{ctx_id}")
    assert ctx_resp.status_code == 200
    assert ctx_resp.json()["data"]["coverage_state"] == "manual_only"


async def test_qa_bug1_explicit_context_ref_nonexistent_or_other_user_rejected(api):
    client, factory, _ = api
    draft_id = await _create_confirmed_draft(client, "qa-bug1-explicit-ref")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        other_uid = resolve_user_uuid("user_b")
        other_draft_id = resolve_draft_uuid("draft_b_1")
        other_ctx = PlanningContext(
            id=uuid4(),
            user_id=other_uid,
            availability_id=None,
            availability_revision=None,
            source_draft_id=other_draft_id,
            source_summary_revision=5,
            availability_snapshot=_draft_summary()["availability"],
            query_start=datetime.now(UTC) - timedelta(days=1),
            query_end=datetime.now(UTC) + timedelta(days=14),
            fetched_at=datetime.now(UTC),
            sync_state="valid",
            coverage_state="manual_only",
            selected_calendar_ids=[],
            selected_calendar_sources=[],
            busy_intervals=[],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
        )
        db.add(other_ctx)
        await db.commit()
        other_ctx_id = str(other_ctx.id)

    for idx, bad_ctx_id in enumerate([str(uuid4()), other_ctx_id]):
        resp = await client.post(
            f"/api/v1/drafts/{draft_id}/candidates",
            headers={"Idempotency-Key": f"qa-cand-badref-{idx}"},
            json={
                "source_summary_revision": 1,
                "source_planning_input_revision": 1,
                "calendar_context_ref": {"context_id": bad_ctx_id, "revision": 1},
            },
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"


@pytest.mark.parametrize(
    "case_name,coverage_state,selected_sources,excluded_sources,calendar_errors,expect_status",
    [
        (
            "degraded_rejected",
            "degraded",
            [{"connection_id": "c1", "google_calendar_id": "primary"}],
            [],
            [],
            409,
        ),
        (
            "manual_only_with_selected_sources_rejected",
            "manual_only",
            [{"connection_id": "c1", "google_calendar_id": "primary"}],
            [],
            [],
            409,
        ),
        (
            "manual_fallback_with_unexcluded_error_rejected",
            "manual_fallback",
            [{"connection_id": "c1", "google_calendar_id": "primary"}],
            [],
            [
                {
                    "connection_id": "c1",
                    "google_calendar_id": "primary",
                    "error_code": "CALENDAR_READ_FAILED",
                    "retryable": True,
                    "requires_reconnect": False,
                    "observed_at": datetime.now(UTC).isoformat(),
                }
            ],
            409,
        ),
        (
            "complete_with_excluded_sources_rejected",
            "complete",
            [{"connection_id": "c1", "google_calendar_id": "primary"}],
            [
                {
                    "connection_id": "c1",
                    "google_calendar_id": "primary",
                    "reason": "skip",
                    "user_confirmed_at": datetime.now(UTC).isoformat(),
                }
            ],
            [],
            409,
        ),
        (
            "manual_fallback_all_sources_excluded_allowed",
            "manual_fallback",
            [{"connection_id": "c1", "google_calendar_id": "primary"}],
            [
                {
                    "connection_id": "c1",
                    "google_calendar_id": "primary",
                    "reason": "broken calendar",
                    "user_confirmed_at": datetime.now(UTC).isoformat(),
                }
            ],
            [],
            201,
        ),
    ],
)
async def test_qa_bug1_coverage_state_source_matrix(
    api,
    case_name: str,
    coverage_state: str,
    selected_sources: list[dict],
    excluded_sources: list[dict],
    calendar_errors: list[dict],
    expect_status: int,
):
    client, factory, _ = api
    draft_id = await _create_confirmed_draft(client, f"qa-cov-{case_name}")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        draft_row = await db.get(DraftRow, resolve_draft_uuid(draft_id))
        assert draft_row is not None
        availability_snap = draft_row.summary["availability"]
        ctx = PlanningContext(
            id=uuid4(),
            user_id=uid,
            availability_id=None,
            availability_revision=None,
            source_draft_id=draft_row.id,
            source_summary_revision=1,
            availability_snapshot=availability_snap,
            query_start=datetime.now(UTC) - timedelta(days=1),
            query_end=datetime.now(UTC) + timedelta(days=14),
            fetched_at=datetime.now(UTC),
            sync_state="valid",
            coverage_state=coverage_state,
            selected_calendar_ids=[s["google_calendar_id"] for s in selected_sources],
            selected_calendar_sources=selected_sources,
            busy_intervals=[],
            calendar_errors=calendar_errors,
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=excluded_sources,
        )
        db.add(ctx)
        await db.commit()
        ctx_id = str(ctx.id)

    resp = await client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": f"qa-cand-{case_name}"},
        json={
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "calendar_context_ref": {"context_id": ctx_id, "revision": 1},
        },
    )
    assert resp.status_code == expect_status, resp.text
    if expect_status == 409:
        assert resp.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Bug 2: create_planning_context transaction boundaries, expire_on_commit=True,
#        idempotency replay, and Phase 1 -> Phase 3 TOCTOU / SessionWindow
# ---------------------------------------------------------------------------
async def test_qa_bug2_create_planning_context_strict_session_refresh_and_idempotency_replay(
    api, monkeypatch
):
    client, _, engine = api
    draft_id = await _create_confirmed_draft(client, "qa-bug2-strict")

    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)
    async with strict_factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _make_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
            stored_at=datetime.now(UTC) - timedelta(hours=3),
            expires_in=3600,
            refresh_token="refresh_strict_1",
        )
        db.add(conn)
        conn_id = str(conn.id)
        await db.commit()

    now = datetime.now(UTC)
    payload = PlanningContextCreateRequest.model_validate(
        {
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": conn_id, "google_calendar_id": "mock_user@gmail.com"}
            ],
        }
    )

    refresh_count = 0
    orig_refresh = FakeCalendarClient.refresh_access_token

    async def spy_refresh(self, refresh_token: str):
        nonlocal refresh_count
        refresh_count += 1
        return await orig_refresh(self, refresh_token)

    monkeypatch.setattr(FakeCalendarClient, "refresh_access_token", spy_refresh)

    async with strict_factory() as db:
        ctx1 = await plan_service.create_planning_context(
            user_id="user_a",
            payload=payload,
            idempotency_key="qa-ctx-strict-1",
            db=db,
        )
        assert ctx1.coverage_state == "complete"
        assert refresh_count == 1

        # Verify refreshed token persisted across Phase 2 -> Phase 3 rollback
        refreshed_conn = await db.get(CalendarConnection, _resolve_uuid(conn_id))
        assert refreshed_conn is not None
        creds = decrypt_credentials(
            refreshed_conn.credential_ref,
            expected_user_id=str(resolve_user_uuid("user_a")),
            expected_connection_id=conn_id,
        )
        assert creds["access_token"] == "mock_refreshed_access_token_xyz"

    # Idempotency replay must return exact same context without calling refresh/Google again
    async with strict_factory() as db:
        ctx_replay = await plan_service.create_planning_context(
            user_id="user_a",
            payload=payload,
            idempotency_key="qa-ctx-strict-1",
            db=db,
        )
        assert ctx_replay.context_id == ctx1.context_id
        assert refresh_count == 1


async def test_qa_bug2_phase2_auth_failure_persists_needs_reconnect_under_expire_on_commit(
    api, monkeypatch
):
    """Ensure Phase 2 rollback does not discard needs_reconnect committed by token refresh failure."""
    client, _, engine = api
    draft_id = await _create_confirmed_draft(client, "qa-bug2-strict-authfail")
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    async with strict_factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _make_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
            stored_at=datetime.now(UTC) - timedelta(hours=3),
            expires_in=3600,
            refresh_token="revoked_strict",
        )
        db.add(conn)
        conn_id = str(conn.id)
        await db.commit()

    async def fail_refresh(self, refresh_token: str):
        raise CalendarAuthError("invalid_grant")

    monkeypatch.setattr(FakeCalendarClient, "refresh_access_token", fail_refresh)

    now = datetime.now(UTC)
    payload = PlanningContextCreateRequest.model_validate(
        {
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "selected_calendar_sources": [
                {"connection_id": conn_id, "google_calendar_id": "mock_user@gmail.com"}
            ],
        }
    )

    async with strict_factory() as db:
        with pytest.raises(AppError) as exc_info:
            await plan_service.create_planning_context(
                user_id="user_a",
                payload=payload,
                idempotency_key="qa-ctx-strict-authfail",
                db=db,
            )
        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "CALENDAR_UNAVAILABLE"

    async with strict_factory() as db:
        persisted = await db.get(CalendarConnection, _resolve_uuid(conn_id))
        assert persisted is not None
        assert persisted.connection_state == "needs_reconnect"


async def test_qa_bug2_phase1_to_phase3_toctou_draft_change_rejected(api, monkeypatch):
    client, factory, _ = api
    draft_id = await _create_confirmed_draft(client, "qa-bug2-toctou-draft")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _make_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
        )
        db.add(conn)
        conn_id = str(conn.id)
        await db.commit()

    orig_list_events = FakeCalendarClient.list_events

    async def mutate_draft_during_phase2(self, access_token, calendar_id, time_min, time_max):
        async with factory() as concurrent_db:
            row = await concurrent_db.get(DraftRow, resolve_draft_uuid(draft_id))
            assert row is not None
            summary_copy = dict(row.summary)
            avail_copy = dict(summary_copy["availability"])
            avail_copy["weekly_rules"] = [
                {"weekday": 1, "start_local": "09:00", "end_local": "12:00"}
            ]
            summary_copy["availability"] = avail_copy
            row.summary = summary_copy
            await concurrent_db.commit()
        return await orig_list_events(self, access_token, calendar_id, time_min, time_max)

    monkeypatch.setattr(FakeCalendarClient, "list_events", mutate_draft_during_phase2)

    now = datetime.now(UTC)
    resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "qa-ctx-toctou-draft"},
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
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "SOURCE_CHANGED"

    async with factory() as db:
        count = len((await db.scalars(select(PlanningContext))).all())
        assert count == 0


async def test_qa_bug2_phase1_to_phase3_toctou_connection_deleted_rejected(api, monkeypatch):
    client, factory, _ = api
    draft_id = await _create_confirmed_draft(client, "qa-bug2-toctou-del")

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        conn = _make_connection(
            user_id=uid,
            connection_state="connected",
            read_calendar_ids=["mock_user@gmail.com"],
        )
        db.add(conn)
        await db.commit()
        conn_id = conn.id

    orig_list_events = FakeCalendarClient.list_events

    async def delete_conn_during_phase2(self, access_token, calendar_id, time_min, time_max):
        async with factory() as concurrent_db:
            victim = await concurrent_db.get(CalendarConnection, conn_id)
            if victim:
                await concurrent_db.delete(victim)
                await concurrent_db.commit()
        return await orig_list_events(self, access_token, calendar_id, time_min, time_max)

    monkeypatch.setattr(FakeCalendarClient, "list_events", delete_conn_during_phase2)

    now = datetime.now(UTC)
    resp = await client.post(
        "/api/v1/planning-contexts",
        headers={"Idempotency-Key": "qa-ctx-toctou-del"},
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
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_qa_bug2_ingest_multiple_sources_with_token_commit_under_expire_on_commit(
    api, monkeypatch
):
    """Verify _SessionWindow prevents MissingGreenlet when source 1 commits token refresh."""
    _, _, engine = api
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    async with strict_factory() as db:
        await ensure_dev_seed_in_db(db)
        uid = resolve_user_uuid("user_a")
        task, _, sess = await _seed_task_step_session(db, uid)
        conn1 = _make_connection(
            user_id=uid,
            account_ref="src1@gmail.com",
            connection_state="connected",
            read_calendar_ids=["cal_1"],
            stored_at=datetime.now(UTC) - timedelta(hours=2),
            expires_in=3600,
            refresh_token="ref1",
        )
        conn2 = _make_connection(
            user_id=uid,
            account_ref="src2@gmail.com",
            connection_state="connected",
            read_calendar_ids=["cal_2"],
            stored_at=datetime.now(UTC),
            expires_in=3600,
            refresh_token="ref2",
        )
        db.add_all([conn1, conn2])
        await db.flush()
        link = CalendarEventLink(
            id=uuid4(),
            user_id=uid,
            session_id=sess.id,
            connection_id=conn2.id,
            google_calendar_id="cal_2",
            google_event_id="evt_own_sess",
            desired_schedule_revision=1,
            sync_state="synced",
            is_current=True,
        )
        db.add(link)
        c1_id, c2_id = str(conn1.id), str(conn2.id)
        sess_start, sess_end = sess.scheduled_start, sess.scheduled_end
        task_id_str = str(task.id)
        await db.commit()

    async def fake_list_events(self, access_token, calendar_id, time_min, time_max):
        if calendar_id == "cal_1":
            return []
        return [
            {
                "id": "evt_own_sess",
                "summary": "Own Task Focus",
                "start": {"dateTime": sess_start.isoformat()},
                "end": {"dateTime": sess_end.isoformat()},
                "status": "confirmed",
                "transparency": "opaque",
            }
        ]

    monkeypatch.setattr(FakeCalendarClient, "list_events", fake_list_events)

    now = datetime.now(UTC)
    async with strict_factory() as db:
        result = await ingest_calendar_sources(
            db=db,
            user_id="user_a",
            selected_sources=[
                CalendarSource(connection_id=c1_id, google_calendar_id="cal_1"),
                CalendarSource(connection_id=c2_id, google_calendar_id="cal_2"),
            ],
            excluded_sources=[],
            query_start=now - timedelta(hours=1),
            query_end=now + timedelta(days=2),
            target_task_id=task_id_str,
        )
        assert result.coverage_state == "complete"
        # Untouched session event from same target_task_id must be skipped cleanly
        assert result.busy_intervals == []


# ---------------------------------------------------------------------------
# Bug 3: Worker rollback + expire_on_commit=True edge cases
# ---------------------------------------------------------------------------
async def test_qa_bug3_worker_missing_connection_on_auth_error_and_token_refresh_mid_job(
    monkeypatch,
):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    try:
        async with strict_factory() as db:
            uid = uuid4()
            user = User(
                id=uid,
                email="qa_worker@example.com",
                display_name="QA Worker",
                is_active=True,
            )
            db.add(user)
            await db.flush()
            _, _, sess = await _seed_task_step_session(db, uid)

            # Connection 1: valid, expired token (forces commit during Step 2)
            conn_refresh = _make_connection(
                user_id=uid,
                account_ref="refresh@gmail.com",
                write_calendar_id="focus_cal",
                stored_at=datetime.now(UTC) - timedelta(hours=2),
                expires_in=3600,
                refresh_token="valid_ref",
            )
            # Connection 2: will be deleted while API call raises CalendarAuthError
            conn_deleted = _make_connection(
                user_id=uid,
                account_ref="deleted@gmail.com",
                write_calendar_id="focus_cal",
            )
            # Connection 3: Step 2 refresh raises CalendarAuthError
            conn_step2_auth = _make_connection(
                user_id=uid,
                account_ref="step2auth@gmail.com",
                write_calendar_id="focus_cal",
                stored_at=datetime.now(UTC) - timedelta(hours=2),
                expires_in=3600,
                refresh_token="revoked_ref",
            )
            db.add_all([conn_refresh, conn_deleted, conn_step2_auth])
            await db.flush()

            link = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=sess.id,
                connection_id=conn_refresh.id,
                google_calendar_id="focus_cal",
                google_event_id="evt_refresh_1",
                google_etag='"etag_1"',
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            job_refresh_then_ok = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_refresh.id,
                event_link_id=link.id,
                target_kind="session",
                target_ref=str(sess.id),
                operation="create",
                idempotency_key="qa_refresh_ok",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "Refresh then create"},
            )
            job_conn_deleted = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_deleted.id,
                target_kind="session",
                target_ref="s_del",
                operation="create",
                idempotency_key="qa_conn_del",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "Deleted conn"},
            )
            job_step2_auth = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_step2_auth.id,
                target_kind="session",
                target_ref="s_step2",
                operation="create",
                idempotency_key="qa_step2_auth",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "Step 2 auth fail"},
            )
            db.add_all([link, job_refresh_then_ok, job_conn_deleted, job_step2_auth])
            ids = (
                job_refresh_then_ok.id,
                job_conn_deleted.id,
                job_step2_auth.id,
                conn_deleted.id,
                conn_step2_auth.id,
                link.id,
            )
            await db.commit()

        j_ok_id, j_del_id, j_s2_id, c_del_id, c_s2_id, link_id = ids

        # Case A: Step 2 refreshes token (commits) then Step 4 creates event under expire_on_commit=True
        async with strict_factory() as db:
            svc = CalendarService(db)
            j = await db.get(CalendarSyncJob, j_ok_id)
            assert j is not None
            assert await process_sync_job(db, j, svc) == "completed"

        async with strict_factory() as db:
            j = await db.get(CalendarSyncJob, j_ok_id)
            l_row = await db.get(CalendarEventLink, link_id)
            assert j is not None and j.job_state == "completed"
            assert l_row is not None and l_row.sync_state == "synced"

        # Case B: Step 2 CalendarAuthError commits needs_reconnect, then worker updates job without MissingGreenlet
        async with strict_factory() as db:
            svc = CalendarService(db)

            async def fail_refresh(refresh_token: str):
                raise CalendarAuthError("invalid_grant")

            monkeypatch.setattr(svc.client, "refresh_access_token", fail_refresh)
            j = await db.get(CalendarSyncJob, j_s2_id)
            assert j is not None
            assert await process_sync_job(db, j, svc) == "failed"

        async with strict_factory() as db:
            j = await db.get(CalendarSyncJob, j_s2_id)
            c = await db.get(CalendarConnection, c_s2_id)
            assert j is not None and j.job_state == "failed"
            assert j.last_error_code == "AUTH_FAILED"
            assert j.attempt_count == 5
            assert c is not None and c.connection_state == "needs_reconnect"

        # Case C: Connection deleted during Step 4 API call that raises CalendarAuthError
        async with strict_factory() as db:
            svc = CalendarService(db)

            async def delete_conn_and_raise_auth(*args, **kwargs):
                # Delete job first to satisfy RESTRICT FK, or delete via separate session after clearing FK
                async with strict_factory() as other_db:
                    victim_job = await other_db.get(CalendarSyncJob, j_del_id)
                    victim_conn = await other_db.get(CalendarConnection, c_del_id)
                    if victim_job:
                        await other_db.delete(victim_job)
                    if victim_conn:
                        await other_db.delete(victim_conn)
                    await other_db.commit()
                raise CalendarAuthError("401 while deleted")

            monkeypatch.setattr(svc.client, "create_event", delete_conn_and_raise_auth)
            j = await db.get(CalendarSyncJob, j_del_id)
            assert j is not None
            # Must handle conn_row is None and job_row is None without AttributeError/MissingGreenlet
            assert await process_sync_job(db, j, svc) == "retry"
    finally:
        await engine.dispose()


async def test_qa_bug3_batch_job1_conflict_and_job2_auth_error_then_job3_ok(monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    try:
        async with strict_factory() as db:
            uid = uuid4()
            db.add(User(id=uid, email="batch_qa@example.com", display_name="Batch QA", is_active=True))
            await db.flush()
            _, _, sess = await _seed_task_step_session(db, uid)
            conn1 = _make_connection(
                user_id=uid, account_ref="b1@gmail.com", write_calendar_id="focus_cal"
            )
            conn2 = _make_connection(
                user_id=uid, account_ref="b2@gmail.com", write_calendar_id="focus_cal"
            )
            db.add_all([conn1, conn2])
            await db.flush()
            link1 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=sess.id,
                connection_id=conn1.id,
                google_calendar_id="focus_cal",
                google_event_id="evt_c",
                google_etag='"e1"',
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            db.add(link1)
            await db.flush()
            j1 = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn1.id,
                event_link_id=link1.id,
                target_kind="session",
                target_ref="1",
                operation="update",
                idempotency_key="qa_b1",
                expected_google_etag='"e1"',
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "CONFLICT"},
            )
            j2 = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn2.id,
                target_kind="session",
                target_ref="2",
                operation="create",
                idempotency_key="qa_b2",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "AUTH_ERR"},
            )
            j3 = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn1.id,
                target_kind="session",
                target_ref="3",
                operation="create",
                idempotency_key="qa_b3",
                job_state="queued",
                attempt_count=0,
                payload_snapshot={"summary": "OK"},
            )
            db.add_all([j1, j2, j3])
            ids = (j1.id, j2.id, j3.id, conn2.id, link1.id)
            await db.commit()

        j1_id, j2_id, j3_id, conn2_id, link1_id = ids

        from app.services import google_calendar_client as gcc

        fake = gcc.get_calendar_client()
        orig_create = fake.create_event

        async def mock_update(*args, **kwargs):
            raise CalendarConflictError("412")

        async def mock_create(access_token, calendar_id, event_payload):
            if event_payload.get("summary") == "AUTH_ERR":
                raise CalendarAuthError("401")
            return await orig_create(access_token, calendar_id, event_payload)

        monkeypatch.setattr(fake, "update_event", mock_update)
        monkeypatch.setattr(fake, "create_event", mock_create)

        async with strict_factory() as db:
            processed = await process_calendar_sync_jobs(db, user_id=uid, limit=10)
            assert processed == 3

        async with strict_factory() as db:
            r1 = await db.get(CalendarSyncJob, j1_id)
            r2 = await db.get(CalendarSyncJob, j2_id)
            r3 = await db.get(CalendarSyncJob, j3_id)
            c2 = await db.get(CalendarConnection, conn2_id)
            l1 = await db.get(CalendarEventLink, link1_id)
            assert r1 is not None and r1.job_state == "conflict" and r1.lease_expires_at is None
            assert l1 is not None and l1.sync_state == "conflict"
            assert r2 is not None and r2.job_state == "queued" and r2.attempt_count == 1
            assert c2 is not None and c2.connection_state == "needs_reconnect"
            assert r3 is not None and r3.job_state == "completed"
    finally:
        await engine.dispose()


async def test_qa_bug3_worker_fifth_auth_error_marks_job_failed(monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    strict_factory = async_sessionmaker(engine, expire_on_commit=True, class_=AsyncSession)

    try:
        async with strict_factory() as db:
            uid = uuid4()
            db.add(User(id=uid, email="fifth_qa@example.com", display_name="Fifth QA", is_active=True))
            await db.flush()
            conn = _make_connection(
                user_id=uid, account_ref="fifth@gmail.com", write_calendar_id="focus_cal"
            )
            db.add(conn)
            await db.flush()
            job = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                target_kind="session",
                target_ref="s_5",
                operation="create",
                idempotency_key="qa_fifth_auth",
                job_state="queued",
                attempt_count=4,
                payload_snapshot={"summary": "Fifth"},
            )
            db.add(job)
            job_id, conn_id = job.id, conn.id
            await db.commit()

        async with strict_factory() as db:
            svc = CalendarService(db)

            async def fail_401(*args, **kwargs):
                raise CalendarAuthError("401 revoked")

            monkeypatch.setattr(svc.client, "create_event", fail_401)
            j = await db.get(CalendarSyncJob, job_id)
            assert j is not None
            await process_sync_job(db, j, svc)

        async with strict_factory() as db:
            j = await db.get(CalendarSyncJob, job_id)
            c = await db.get(CalendarConnection, conn_id)
            assert j is not None
            assert j.attempt_count == 5
            assert j.job_state == "failed"
            assert j.last_error_code == "AUTH_EXPIRED"
            assert c is not None and c.connection_state == "needs_reconnect"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Bug 4: OAuth callback error paths, fragments, legacy route & preview guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "endpoint",
    ["/api/v1/calendar/oauth/callback", "/api/v1/calendar/oauth2callback"],
)
async def test_qa_bug4_oauth_error_and_missing_code_preserve_query_and_fragment(api, endpoint: str):
    client, _, _ = api

    # 1. error=access_denied + error_description + existing query & fragment
    auth1 = await client.post(
        "/api/v1/calendar/authorizations",
        json={
            "device_id": "android-qa-1",
            "return_target": "proplan://calendar/connected?foo=bar#frag",
        },
    )
    assert auth1.status_code == 200
    state1 = parse_qs(urlparse(auth1.json()["data"]["auth_url"]).query)["state"][0]

    cb1 = await client.get(
        endpoint,
        params={
            "error": "access_denied",
            "error_description": "User denied access",
            "state": state1,
        },
        follow_redirects=False,
    )
    assert cb1.status_code == 302, cb1.text
    loc1 = urlparse(cb1.headers["location"])
    assert loc1.scheme == "proplan"
    assert loc1.netloc == "calendar"
    assert loc1.path == "/connected"
    assert loc1.fragment == "frag"
    qs1 = parse_qs(loc1.query)
    assert qs1["foo"] == ["bar"]
    assert qs1["error"] == ["access_denied"]
    assert qs1["error_description"] == ["User denied access"]

    # 2. Missing both code and error -> error=missing_code
    auth2 = await client.post(
        "/api/v1/calendar/authorizations",
        json={
            "device_id": "android-qa-2",
            "return_target": "proplan://calendar/connected?foo=bar",
        },
    )
    state2 = parse_qs(urlparse(auth2.json()["data"]["auth_url"]).query)["state"][0]
    cb2 = await client.get(endpoint, params={"state": state2}, follow_redirects=False)
    assert cb2.status_code == 302
    qs2 = parse_qs(urlparse(cb2.headers["location"]).query)
    assert qs2["foo"] == ["bar"]
    assert qs2["error"] == ["missing_code"]
    assert "error_description" not in qs2

    # 3. Invalid state with ?error=access_denied -> 401
    bad_cb = await client.get(
        endpoint,
        params={"error": "access_denied", "state": "nonexistent_state_token"},
        follow_redirects=False,
    )
    assert bad_cb.status_code == 401


async def test_qa_bug4_expired_state_with_oauth_error_returns_401(api):
    client, factory, _ = api
    uid = resolve_user_uuid("user_a")
    raw_state = "expired_qa_state_123"
    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        db.add(
            CalendarAuthorization(
                id=uuid4(),
                user_id=uid,
                state_hash=_hash_state(raw_state),
                redirect_uri="",
                return_target="proplan://calendar/connected",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
                consumed_at=None,
            )
        )
        await db.commit()

    resp = await client.get(
        "/api/v1/calendar/oauth/callback",
        params={"error": "access_denied", "state": raw_state},
        follow_redirects=False,
    )
    assert resp.status_code == 401


async def test_qa_bug4_confirm_configuration_preview_wrong_user_and_missing_preview(api):
    client, factory, _ = api

    async with factory() as db:
        await ensure_dev_seed_in_db(db)
        uid_b = resolve_user_uuid("user_b")
        conn_b = _make_connection(user_id=uid_b, connection_state="connected")
        db.add(conn_b)
        await db.flush()
        # Preview belongs to user_b
        preview_b = CalendarPreview(
            id=uuid4(),
            user_id=uid_b,
            connection_id=conn_b.id,
            kind="configuration",
            base_connection_revision=1,
            source_revisions={},
            payload={"read_calendar_ids": ["primary"], "write_calendar_id": "primary"},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add(preview_b)
        await db.commit()
        pid_b = str(preview_b.id)

    # Wrong user -> 404
    r1 = await client.post(
        f"/api/v1/calendar/configuration-previews/{pid_b}/confirm",
        json={"expected_preview_revision": 1},
    )
    assert r1.status_code == 404

    # Nonexistent preview_id -> 404
    r2 = await client.post(
        f"/api/v1/calendar/configuration-previews/{uuid4()}/confirm",
        json={"expected_preview_revision": 1},
    )
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Bug 5: URL encoding of #, /, @, and spaces in calendar_id and event_id
# ---------------------------------------------------------------------------
async def test_qa_bug5_url_encoding_special_chars_and_spaces(monkeypatch):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        assert request.url.fragment == ""
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"items": [], "id": "ok", "etag": '"1"'})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_async_client)

    client = GoogleCalendarClient("cid", "csec", "http://localhost/cb")
    cal_id = "team cal/sub#1@group.calendar.google.com"
    evt_id = "evt 1/part#2@domain"
    now = datetime.now(UTC)

    await client.list_events("tok", cal_id, now, now + timedelta(days=1))
    await client.get_event("tok", cal_id, evt_id)
    await client.create_event("tok", cal_id, {"summary": "Space test"})
    await client.update_event("tok", cal_id, evt_id, {"summary": "Upd"}, expected_etag='"1"')
    await client.delete_event("tok", cal_id, evt_id, expected_etag='"1"')

    encoded_cal = "team%20cal%2Fsub%231%40group.calendar.google.com"
    encoded_evt = "evt%201%2Fpart%232%40domain"

    assert len(seen) == 5
    for _, url in seen:
        assert encoded_cal in url
        assert "#" not in url
        assert "+" not in url.split("?")[0]
    assert seen[0][1].split("?")[0].endswith(f"/calendars/{encoded_cal}/events")
    assert seen[1][1].endswith(f"/calendars/{encoded_cal}/events/{encoded_evt}")
    assert seen[2][1].endswith(f"/calendars/{encoded_cal}/events")
    assert seen[3][1].endswith(f"/calendars/{encoded_cal}/events/{encoded_evt}")
    assert seen[4][1].endswith(f"/calendars/{encoded_cal}/events/{encoded_evt}")
