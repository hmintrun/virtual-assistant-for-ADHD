"""Kiểm thử toàn trình xóa/hủy mềm công việc (DELETE /api/v1/tasks/{task_id}) trên SQLite & PostgreSQL 16."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models import Base, Session, Step, Task
from app.models.calendar import CalendarConnection, CalendarEventLink, CalendarSyncJob
from app.models.execution import CheckIn, SessionRun
from app.models.planning import ScheduleVersion
from app.models.schedule_proposal import RescheduleProposal
from app.models.settings import SettingChangeRequest
from app.models.system import Reminder
from app.models.user import User, UserPreference
from app.schemas.user import CurrentUser
from app.services import plan_service
from app.services.calendar_sync_worker import process_calendar_sync_jobs
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import (
    FakeCalendarClient,
    get_calendar_client,
    reset_fake_calendar_client,
)

PG16_TEST_URL = (
    "postgresql+asyncpg://proplan_staging:qa_staging_phase0_pw_not_secret"
    "@127.0.0.1:5433/proplan_test_task_delete"
)


def _headers(key: str | None = None, *, as_other: bool = False) -> dict[str, str]:
    hdrs = {"Idempotency-Key": key or uuid4().hex}
    if as_other:
        hdrs["X-Test-User"] = "other"
    return hdrs


def _make_payload(
    *,
    day_offset: int = 2,
    start_hour: int = 9,
    hours: int = 4,
    title: str = "Viết báo cáo tiến độ",
) -> dict[str, Any]:
    start = (datetime.now(UTC) + timedelta(days=day_offset)).replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(hours=hours)
    return {
        "client_draft_id": uuid4().hex,
        "input_source": "text",
        "raw_input": f"{title}, rảnh {start_hour}h-{start_hour + hours}h ngày chọn.",
        "summary": {
            "title": title,
            "description": f"Thực hiện {title}",
            "scope": "Báo cáo 2 trang hoàn chỉnh",
            "extra_requirements": [],
            "time_constraint": {
                "kind": "deadline",
                "start_at": start.isoformat(),
                "deadline_at": end.isoformat(),
                "input_timezone": "UTC",
            },
            "availability": {
                "timezone": "UTC",
                "weekly_rules": [],
                "date_overrides": [
                    {
                        "date": start.date().isoformat(),
                        "intervals": [
                            {
                                "start_local": f"{start.hour:02d}:00",
                                "end_local": f"{end.hour:02d}:00",
                            }
                        ],
                    }
                ],
            },
        },
    }


def _make_connection(
    user_id: UUID,
    *,
    write_calendar_id: str | None = "proplan_focus@group.calendar.google.com",
) -> CalendarConnection:
    conn_id = uuid4()
    creds = {
        "access_token": "acc_task_del_1",
        "refresh_token": "ref_task_del_1",
        "expires_in": 3600,
        "stored_at": datetime.now(UTC).isoformat(),
        "user_id": str(user_id),
        "connection_id": str(conn_id),
    }
    return CalendarConnection(
        id=conn_id,
        user_id=user_id,
        provider="google",
        account_ref=f"cal_{user_id.hex[:8]}@example.test",
        read_calendar_ids=[],
        write_calendar_id=write_calendar_id,
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        connection_state="connected",
        credential_ref=encrypt_credentials(creds),
    )


async def _seed_users_and_prefs(factory) -> tuple[UUID, UUID]:
    owner, other = uuid4(), uuid4()
    async with factory() as db:
        db.add_all(
            [
                User(id=owner, email=f"owner_{owner.hex[:6]}@example.test", display_name="Owner"),
                User(id=other, email=f"other_{other.hex[:6]}@example.test", display_name="Other"),
            ]
        )
        await db.flush()
        db.add(
            UserPreference(
                user_id=owner,
                work_interval_seconds=1500,
                rest_interval_seconds=300,
                buffer_multiplier=1.2,
                effective_revision=1,
                micro_task_seconds=120,
                reminder_offsets=[900, 600],
                reminder_style="gentle",
            )
        )
        await db.commit()
    return owner, other


async def _confirm_task_via_http(
    client: httpx.AsyncClient, payload: dict[str, Any]
) -> dict[str, Any]:
    create_resp = await client.post("/api/v1/drafts", json=payload, headers=_headers())
    assert create_resp.status_code == 201, create_resp.text
    draft = create_resp.json()["data"]

    sum_resp = await client.post(
        f"/api/v1/drafts/{draft['draft_id']}/confirm-summary",
        json={"expected_summary_revision": draft["summary_revision"]},
        headers=_headers(),
    )
    assert sum_resp.status_code == 200, sum_resp.text

    cand_resp = await client.post(
        f"/api/v1/drafts/{draft['draft_id']}/candidates",
        json={
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
        },
        headers=_headers(),
    )
    assert cand_resp.status_code == 201, cand_resp.text
    candidate = cand_resp.json()["data"]

    conf_resp = await client.post(
        f"/api/v1/candidates/{candidate['candidate_id']}/confirm",
        json={
            "expected_review_revision": candidate["review_revision"],
            "expected_summary_revision": candidate["source_summary_revision"],
            "expected_planning_input_revision": candidate["source_planning_input_revision"],
            "expected_source_revisions": candidate["source_revisions"],
        },
        headers=_headers(),
    )
    assert conf_resp.status_code == 201, conf_resp.text
    return conf_resp.json()["data"]


async def _insert_session_run(
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    run_state: str,
) -> SessionRun:
    now = datetime.now(UTC)
    run = SessionRun(
        id=uuid4(),
        user_id=user_id,
        session_id=session_id,
        device_id="dev-1",
        started_at=now - timedelta(minutes=10),
        ended_at=now if run_state in ("awaiting_checkin", "closed") else None,
        run_state=run_state,
        active_phase="ended" if run_state in ("awaiting_checkin", "closed") else "work",
        segment_index=0,
        work_elapsed_seconds=600,
        rest_elapsed_seconds=0,
        actual_work_seconds=600 if run_state == "closed" else None,
        actual_source="timer_proxy" if run_state == "closed" else None,
        outcome="completed" if run_state == "closed" else "unknown",
        settings_snapshot={"effective_revision": 1},
    )
    db.add(run)
    await db.flush()
    return run


async def _insert_pending_proposal(
    db: AsyncSession,
    *,
    user_id: UUID,
    target_task_id: UUID,
    base_schedule_revision: int,
    status: str = "pending",
) -> RescheduleProposal:
    prop = RescheduleProposal(
        id=uuid4(),
        user_id=user_id,
        trigger_reason="checkin_incomplete",
        target_task_id=target_task_id,
        allowed_task_ids=[str(target_task_id)],
        affected_task_ids=[str(target_task_id)],
        base_schedule_revision=base_schedule_revision,
        source_revisions={
            "task": 1,
            "schedule": base_schedule_revision,
            "availability": 0,
            "settings": 1,
            "calendar": 1,
        },
        proposal_revision=1,
        status=status,
        proposed_changes={"sessions_to_cancel": [], "sessions_to_add": [], "sessions_to_move": []},
        feasibility={"status": "feasible", "confirmable": True, "blocking_reasons": []},
        explanation="Đề xuất điều chỉnh lịch.",
    )
    db.add(prop)
    await db.flush()
    return prop


@pytest_asyncio.fixture
async def sqlite_env(tmp_path, monkeypatch):
    reset_fake_calendar_client()
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'task_delete.sqlite3').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    owner, other = await _seed_users_and_prefs(factory)

    async def unavailable(**kwargs):
        raise ConnectionError("Providers disabled in test_task_delete")

    monkeypatch.setattr(plan_service.ai_service, "_execute_hedged_structured", unavailable)

    prev_overrides = app.dependency_overrides.copy()

    async def db_override():
        async with factory() as db:
            yield db

    async def user_override(request: Request):
        return CurrentUser(
            user_id=str(other if request.headers.get("X-Test-User") == "other" else owner)
        )

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://task-delete.test"
        ) as client:
            yield {"client": client, "db": factory, "owner": owner, "other": other}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev_overrides)
        await engine.dispose()
        reset_fake_calendar_client()


@pytest_asyncio.fixture
async def pg16_env(monkeypatch):
    reset_fake_calendar_client()
    engine = make_engine(PG16_TEST_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    owner, other = await _seed_users_and_prefs(factory)

    async def unavailable(**kwargs):
        raise ConnectionError("Providers disabled in test_task_delete")

    monkeypatch.setattr(plan_service.ai_service, "_execute_hedged_structured", unavailable)

    prev_overrides = app.dependency_overrides.copy()

    async def db_override():
        async with factory() as db:
            yield db

    async def user_override(request: Request):
        return CurrentUser(
            user_id=str(other if request.headers.get("X-Test-User") == "other" else owner)
        )

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://task-delete-pg16.test"
        ) as client:
            yield {"client": client, "db": factory, "owner": owner, "other": other}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev_overrides)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_delete_task_happy_path_and_slot_release(sqlite_env):
    client: httpx.AsyncClient = sqlite_env["client"]
    factory = sqlite_env["db"]
    owner: UUID = sqlite_env["owner"]

    payload_a = _make_payload(day_offset=2, start_hour=9, hours=4, title="Task A")
    payload_b = _make_payload(day_offset=2, start_hour=14, hours=4, title="Task B")

    conf_a = await _confirm_task_via_http(client, payload_a)
    conf_b = await _confirm_task_via_http(client, payload_b)
    task_a = conf_a["task"]
    task_b = conf_b["task"]
    task_a_id = task_a["task_id"]
    session_a_ids = [s["session_id"] for s in task_a["sessions"]]
    prev_sched_rev = conf_b["schedule_version"]["version_number"]

    del_resp = await client.delete(
        f"/api/v1/tasks/{task_a_id}",
        params={"expected_task_revision": task_a["revision"]},
        headers=_headers("del-happy-1"),
    )
    assert del_resp.status_code == 200, del_resp.text
    del_data = del_resp.json()["data"]
    assert del_data == {
        "task_id": task_a_id,
        "deleted": True,
        "status": "cancelled",
        "task_revision": 2,
        "schedule_revision": prev_sched_rev + 1,
        "cancelled_session_ids": session_a_ids,
        "alarm_sync_state": "pending",
        "calendar_sync_summary": "not_integrated",
    }

    # GET /tasks omits cancelled task
    list_resp = await client.get("/api/v1/tasks")
    assert list_resp.status_code == 200
    listed_ids = [item["task_id"] for item in list_resp.json()["data"]["items"]]
    assert listed_ids == [task_b["task_id"]]

    # GET /tasks/{id} -> 404
    get_resp = await client.get(f"/api/v1/tasks/{task_a_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # GET /schedule omits cancelled sessions
    win_from = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    win_to = (datetime.now(UTC) + timedelta(days=4)).isoformat()
    sched_resp = await client.get("/api/v1/schedule", params={"from": win_from, "to": win_to})
    assert sched_resp.status_code == 200, sched_resp.text
    sched_data = sched_resp.json()["data"]
    assert sched_data["schedule_revision"] == prev_sched_rev + 1
    assert sched_data["alarm_sync_state"] == "pending"
    sched_session_ids = {item["session_id"] for item in sched_data["items"]}
    assert not (sched_session_ids & set(session_a_ids))
    assert {s["session_id"] for s in task_b["sessions"]}.issubset(sched_session_ids)

    # DB rows soft-cancelled and reminders cancelled
    async with factory() as db:
        task_row = await db.get(Task, UUID(task_a_id))
        assert task_row is not None
        assert task_row.status == "cancelled"
        assert task_row.revision == 2

        steps = (await db.scalars(select(Step).where(Step.task_id == UUID(task_a_id)))).all()
        assert steps and all(st.status == "cancelled" and st.revision == 2 for st in steps)

        sessions = (
            await db.scalars(select(Session).where(Session.id.in_([UUID(x) for x in session_a_ids])))
        ).all()
        assert all(
            s.status == "cancelled"
            and s.start_state == "superseded"
            and s.schedule_revision == prev_sched_rev + 1
            and s.revision == 2
            for s in sessions
        )

        reminders = (
            await db.scalars(
                select(Reminder).where(Reminder.session_id.in_([UUID(x) for x in session_a_ids]))
            )
        ).all()
        assert reminders and all(r.delivery_state == "cancelled" and r.revision == 2 for r in reminders)

        latest_ver = await db.scalar(
            select(ScheduleVersion).where(
                ScheduleVersion.user_id == owner,
                ScheduleVersion.version_number == prev_sched_rev + 1,
            )
        )
        assert latest_ver is not None
        assert latest_ver.base_version == prev_sched_rev
        assert latest_ver.affected_task_ids == [task_a_id]
        assert latest_ver.alarm_sync_state == "pending"

    # Freed slot can be booked immediately by a new task
    payload_c = _make_payload(day_offset=2, start_hour=9, hours=4, title="Task C")
    conf_c = await _confirm_task_via_http(client, payload_c)
    assert conf_c["task"]["status"] == "pending"
    assert len(conf_c["task"]["sessions"]) >= 1


@pytest.mark.asyncio
async def test_delete_task_calendar_outbox_synced_and_queued_update(sqlite_env):
    client: httpx.AsyncClient = sqlite_env["client"]
    factory = sqlite_env["db"]
    owner: UUID = sqlite_env["owner"]

    async with factory() as db:
        conn = _make_connection(owner)
        db.add(conn)
        await db.commit()
        conn_id = conn.id

    conf = await _confirm_task_via_http(client, _make_payload(day_offset=3, start_hour=9, hours=4))
    task_id = conf["task"]["task_id"]
    session_uuids = [UUID(s["session_id"]) for s in conf["task"]["sessions"]]
    assert conf["calendar_sync_summary"] == "pending"

    async with factory() as db:
        processed = await process_calendar_sync_jobs(db, user_id=owner, limit=20)
        assert processed == len(session_uuids)

    fake_client = get_calendar_client()
    assert isinstance(fake_client, FakeCalendarClient)
    assert len(fake_client.mock_events.get("proplan_focus@group.calendar.google.com", [])) == len(
        session_uuids
    )

    # Inject a queued update job on first link to confirm it gets closed as completed
    async with factory() as db:
        links = (
            await db.scalars(
                select(CalendarEventLink)
                .where(
                    CalendarEventLink.user_id == owner,
                    CalendarEventLink.session_id.in_(session_uuids),
                    CalendarEventLink.is_current.is_(True),
                )
                .order_by(CalendarEventLink.id)
            )
        ).all()
        assert len(links) == len(session_uuids)
        assert all(lk.synced_schedule_revision is not None and lk.google_etag for lk in links)
        first_link = links[0]
        stale_update_job = CalendarSyncJob(
            id=uuid4(),
            user_id=owner,
            connection_id=conn_id,
            event_link_id=first_link.id,
            target_kind="session_event",
            target_ref=str(first_link.session_id),
            operation="update",
            target_schedule_revision=conf["schedule_version"]["version_number"],
            expected_google_etag=first_link.google_etag,
            payload_snapshot={"id": first_link.google_event_id},
            idempotency_key=f"stale-upd-{first_link.id.hex[:8]}",
            job_state="queued",
            attempt_count=0,
        )
        db.add(stale_update_job)
        await db.commit()
        stale_update_job_id = stale_update_job.id

    del_resp = await client.delete(f"/api/v1/tasks/{task_id}", headers=_headers("del-cal-synced"))
    assert del_resp.status_code == 200, del_resp.text
    del_data = del_resp.json()["data"]
    new_ver = del_data["schedule_revision"]
    assert del_data["calendar_sync_summary"] == "pending"

    async with factory() as db:
        stale_job = await db.get(CalendarSyncJob, stale_update_job_id)
        assert stale_job is not None and stale_job.job_state == "completed"

        links = (
            await db.scalars(
                select(CalendarEventLink).where(CalendarEventLink.session_id.in_(session_uuids))
            )
        ).all()
        assert len(links) == len(session_uuids)
        assert all(
            lk.is_current is True
            and lk.sync_state == "pending"
            and lk.desired_schedule_revision == new_ver
            for lk in links
        )

        del_jobs = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.target_schedule_revision == new_ver,
                )
            )
        ).all()
        assert len(del_jobs) == len(session_uuids)
        assert all(
            j.operation == "delete"
            and j.job_state == "queued"
            and j.expected_google_etag is not None
            for j in del_jobs
        )

        processed_del = await process_calendar_sync_jobs(db, user_id=owner, limit=20)
        assert processed_del == len(session_uuids)

    assert len(fake_client.mock_events.get("proplan_focus@group.calendar.google.com", [])) == 0

    async with factory() as db:
        links_after = (
            await db.scalars(
                select(CalendarEventLink).where(CalendarEventLink.session_id.in_(session_uuids))
            )
        ).all()
        assert all(lk.is_current is False and lk.sync_state == "deleted" for lk in links_after)

    win_from = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    win_to = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    sched_resp = await client.get("/api/v1/schedule", params={"from": win_from, "to": win_to})
    assert sched_resp.status_code == 200
    assert sched_resp.json()["data"]["calendar_sync_summary"] == "synced"


@pytest.mark.asyncio
async def test_delete_task_calendar_outbox_unsynced_create_and_null_event_id(sqlite_env):
    client: httpx.AsyncClient = sqlite_env["client"]
    factory = sqlite_env["db"]
    owner: UUID = sqlite_env["owner"]

    async with factory() as db:
        conn = _make_connection(owner)
        db.add(conn)
        await db.commit()

    # Task 1: queued create (never synced)
    conf1 = await _confirm_task_via_http(client, _make_payload(day_offset=3, start_hour=9, hours=4))
    task1_id = conf1["task"]["task_id"]
    sess1_uuids = [UUID(s["session_id"]) for s in conf1["task"]["sessions"]]

    del1 = await client.delete(f"/api/v1/tasks/{task1_id}", headers=_headers("del-unsynced-1"))
    assert del1.status_code == 200, del1.text
    del1_data = del1.json()["data"]
    assert del1_data["calendar_sync_summary"] == "not_integrated"

    async with factory() as db:
        links = (
            await db.scalars(
                select(CalendarEventLink).where(CalendarEventLink.session_id.in_(sess1_uuids))
            )
        ).all()
        assert len(links) == len(sess1_uuids)
        assert all(
            lk.is_current is False
            and lk.sync_state == "deleted"
            and lk.desired_schedule_revision == del1_data["schedule_revision"]
            for lk in links
        )

        jobs = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.event_link_id.in_([lk.id for lk in links]),
                )
            )
        ).all()
        assert len(jobs) == len(sess1_uuids)
        assert all(j.operation == "create" and j.job_state == "completed" for j in jobs)
        assert not any(j.operation == "delete" for j in jobs)

    # Task 2: link with google_event_id=None and synced_schedule_revision=None (no queued job)
    conf2 = await _confirm_task_via_http(client, _make_payload(day_offset=4, start_hour=9, hours=4))
    task2_id = conf2["task"]["task_id"]
    sess2_uuids = [UUID(s["session_id"]) for s in conf2["task"]["sessions"]]
    async with factory() as db:
        # Replace link0's state to simulate google_event_id=None and no queued create job
        first_link = await db.scalar(
            select(CalendarEventLink).where(CalendarEventLink.session_id == sess2_uuids[0])
        )
        assert first_link is not None
        first_link.google_event_id = None
        first_link.synced_schedule_revision = None
        create_job = await db.scalar(
            select(CalendarSyncJob).where(
                CalendarSyncJob.event_link_id == first_link.id,
                CalendarSyncJob.job_state == "queued",
            )
        )
        if create_job is not None:
            create_job.job_state = "failed"
        await db.commit()

    del2 = await client.delete(f"/api/v1/tasks/{task2_id}", headers=_headers("del-null-evt-2"))
    assert del2.status_code == 200, del2.text
    async with factory() as db:
        first_link_after = await db.scalar(
            select(CalendarEventLink).where(CalendarEventLink.session_id == sess2_uuids[0])
        )
        assert first_link_after is not None
        assert first_link_after.is_current is False
        assert first_link_after.sync_state == "deleted"
        del_jobs_null = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == first_link_after.id,
                    CalendarSyncJob.operation == "delete",
                )
            )
        ).all()
        assert del_jobs_null == []


@pytest.mark.asyncio
async def test_delete_task_calendar_outbox_leased_and_retried_create_jobs(sqlite_env):
    client: httpx.AsyncClient = sqlite_env["client"]
    factory = sqlite_env["db"]
    owner: UUID = sqlite_env["owner"]

    async with factory() as db:
        db.add(_make_connection(owner))
        await db.commit()

    conf = await _confirm_task_via_http(client, _make_payload(day_offset=3, start_hour=9, hours=4))
    task_id = conf["task"]["task_id"]
    sess_uuids = [UUID(s["session_id"]) for s in conf["task"]["sessions"]]
    assert len(sess_uuids) >= 2

    now = datetime.now(UTC)
    async with factory() as db:
        links = (
            await db.scalars(
                select(CalendarEventLink)
                .where(
                    CalendarEventLink.user_id == owner,
                    CalendarEventLink.session_id.in_(sess_uuids),
                )
                .order_by(CalendarEventLink.id)
            )
        ).all()
        assert len(links) == len(sess_uuids)

        # Link 0: in-flight leased create job (lease_expires_at > now)
        leased_job = await db.scalar(
            select(CalendarSyncJob).where(
                CalendarSyncJob.event_link_id == links[0].id,
                CalendarSyncJob.operation == "create",
            )
        )
        assert leased_job is not None
        leased_job.lease_expires_at = now + timedelta(minutes=2)
        leased_job.claimed_at = now
        leased_link_id = links[0].id
        leased_job_id = leased_job.id

        # Link 1: retried create job (attempt_count > 0, unleased)
        retried_job = await db.scalar(
            select(CalendarSyncJob).where(
                CalendarSyncJob.event_link_id == links[1].id,
                CalendarSyncJob.operation == "create",
            )
        )
        assert retried_job is not None
        retried_job.attempt_count = 1
        retried_job.next_retry_at = now + timedelta(seconds=10)
        retried_link_id = links[1].id
        retried_job_id = retried_job.id
        await db.commit()

    del_resp = await client.delete(
        f"/api/v1/tasks/{task_id}", headers=_headers("del-leased-retried")
    )
    assert del_resp.status_code == 200, del_resp.text
    del_data = del_resp.json()["data"]
    assert del_data["calendar_sync_summary"] == "pending"
    new_ver = del_data["schedule_revision"]

    async with factory() as db:
        # In-flight leased create job MUST remain queued with its lease intact (worker serialization)
        leased_after = await db.get(CalendarSyncJob, leased_job_id)
        assert leased_after is not None
        assert leased_after.job_state == "queued"
        assert leased_after.lease_expires_at is not None and leased_after.lease_expires_at > now

        # Unleased retried create job is closed as completed
        retried_after = await db.get(CalendarSyncJob, retried_job_id)
        assert retried_after is not None
        assert retried_after.job_state == "completed"

        # Both links stay current & pending and each gets a queued delete job
        for lid in (leased_link_id, retried_link_id):
            lk = await db.get(CalendarEventLink, lid)
            assert lk is not None
            assert lk.is_current is True
            assert lk.sync_state == "pending"
            assert lk.desired_schedule_revision == new_ver

            djob = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == lid,
                    CalendarSyncJob.operation == "delete",
                    CalendarSyncJob.target_schedule_revision == new_ver,
                )
            )
            assert djob is not None and djob.job_state == "queued"

        # Simulate lease expiry on leased create job -> worker runs create then delete cleanly (no orphan event)
        assert leased_after is not None
        leased_after.lease_expires_at = now - timedelta(seconds=1)
        await db.commit()

        processed = await process_calendar_sync_jobs(db, user_id=owner, limit=20)
        assert processed >= 2

        lk0_final = await db.get(CalendarEventLink, leased_link_id)
        lk1_final = await db.get(CalendarEventLink, retried_link_id)
        assert lk0_final is not None and lk0_final.is_current is False and lk0_final.sync_state == "deleted"
        assert lk1_final is not None and lk1_final.is_current is False and lk1_final.sync_state == "deleted"

    fake_client = get_calendar_client()
    assert isinstance(fake_client, FakeCalendarClient)
    assert len(fake_client.mock_events.get("proplan_focus@group.calendar.google.com", [])) == 0


@pytest.mark.asyncio
async def test_delete_task_idempotency_replay_reuse_and_double_delete(sqlite_env):
    client: httpx.AsyncClient = sqlite_env["client"]
    factory = sqlite_env["db"]
    owner: UUID = sqlite_env["owner"]

    conf_a = await _confirm_task_via_http(client, _make_payload(day_offset=2, start_hour=9, hours=4))
    conf_b = await _confirm_task_via_http(client, _make_payload(day_offset=2, start_hour=14, hours=4))
    task_a_id = conf_a["task"]["task_id"]
    task_b_id = conf_b["task"]["task_id"]

    # First delete with Idempotency-Key
    key1 = "idem-delete-task-1"
    resp1 = await client.delete(
        f"/api/v1/tasks/{task_a_id}",
        params={"expected_task_revision": 1},
        headers=_headers(key1),
    )
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()["data"]

    async with factory() as db:
        ver_count_before = await db.scalar(
            select(func.count()).select_from(ScheduleVersion).where(ScheduleVersion.user_id == owner)
        )

    # Replay with exact same key and params -> 200 identical payload, no version bump
    replay = await client.delete(
        f"/api/v1/tasks/{task_a_id}",
        params={"expected_task_revision": 1},
        headers=_headers(key1),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == body1

    async with factory() as db:
        ver_count_after = await db.scalar(
            select(func.count()).select_from(ScheduleVersion).where(ScheduleVersion.user_id == owner)
        )
        assert ver_count_after == ver_count_before

    # Reuse same key with different expected_task_revision -> 409 IDEMPOTENCY_KEY_REUSED
    reuse_rev = await client.delete(
        f"/api/v1/tasks/{task_a_id}",
        params={"expected_task_revision": 2},
        headers=_headers(key1),
    )
    assert reuse_rev.status_code == 409
    assert reuse_rev.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # Reuse same key on different task -> 409 IDEMPOTENCY_KEY_REUSED
    reuse_task = await client.delete(
        f"/api/v1/tasks/{task_b_id}",
        params={"expected_task_revision": 1},
        headers=_headers(key1),
    )
    assert reuse_task.status_code == 409
    assert reuse_task.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # Double delete without Idempotency-Key -> 404
    dup_no_key = await client.delete(f"/api/v1/tasks/{task_a_id}")
    assert dup_no_key.status_code == 404
    assert dup_no_key.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # Double delete with a new Idempotency-Key -> 404
    dup_new_key = await client.delete(
        f"/api/v1/tasks/{task_a_id}",
        headers=_headers("idem-delete-task-new"),
    )
    assert dup_new_key.status_code == 404
    assert dup_new_key.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_task_guards_proposals_and_preserves_completed_history(sqlite_env):
    client: httpx.AsyncClient = sqlite_env["client"]
    factory = sqlite_env["db"]
    owner: UUID = sqlite_env["owner"]

    conf_a = await _confirm_task_via_http(client, _make_payload(day_offset=2, start_hour=9, hours=4))
    conf_b = await _confirm_task_via_http(client, _make_payload(day_offset=2, start_hour=14, hours=4))
    task_a_id = conf_a["task"]["task_id"]
    task_b_id = conf_b["task"]["task_id"]
    sched_rev = conf_b["schedule_version"]["version_number"]

    # Validation errors on bad query param / empty or overly long header
    bad_rev = await client.delete(
        f"/api/v1/tasks/{task_a_id}", params={"expected_task_revision": 0}
    )
    assert bad_rev.status_code == 422
    empty_hdr = await client.delete(
        f"/api/v1/tasks/{task_a_id}", headers={"Idempotency-Key": ""}
    )
    assert empty_hdr.status_code == 422
    long_hdr = await client.delete(
        f"/api/v1/tasks/{task_a_id}", headers={"Idempotency-Key": "k" * 129}
    )
    assert long_hdr.status_code == 422

    # Missing / non-UUID / wrong owner -> 404
    assert (await client.delete("/api/v1/tasks/not-a-uuid")).status_code == 404
    assert (await client.delete(f"/api/v1/tasks/{uuid4()}")).status_code == 404
    other_del = await client.delete(
        f"/api/v1/tasks/{task_a_id}", headers=_headers("other-del", as_other=True)
    )
    assert other_del.status_code == 404

    # Revision conflict -> 409 REVISION_CONFLICT
    rev_conflict = await client.delete(
        f"/api/v1/tasks/{task_a_id}",
        params={"expected_task_revision": 99},
        headers=_headers("rev-mismatch"),
    )
    assert rev_conflict.status_code == 409
    assert rev_conflict.json()["error"]["code"] == "REVISION_CONFLICT"
    assert rev_conflict.json()["error"]["current_revisions"] == {"task": 1}

    # Active run guards: running, paused, awaiting_checkin
    sess_a0 = UUID(conf_a["task"]["sessions"][0]["session_id"])
    sess_b0 = UUID(conf_b["task"]["sessions"][0]["session_id"])
    for state in ("running", "paused"):
        async with factory() as db:
            run = await _insert_session_run(db, user_id=owner, session_id=sess_b0, run_state=state)
            await db.commit()
            run_id = run.id

        blocked = await client.delete(f"/api/v1/tasks/{task_a_id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"
        assert blocked.json()["error"]["next_action"] == "finish_active_run"

        async with factory() as db:
            r_row = await db.get(SessionRun, run_id)
            assert r_row is not None
            r_row.run_state = "closed"
            r_row.ended_at = datetime.now(UTC)
            await db.commit()

    # awaiting_checkin on task_b's session does NOT block deleting task_a,
    # whereas non-expired open awaiting_checkin on task_a's session DOES block deleting task_a
    now_utc = datetime.now(UTC)
    async with factory() as db:
        await_b_run = await _insert_session_run(
            db, user_id=owner, session_id=sess_b0, run_state="awaiting_checkin"
        )
        await_run = await _insert_session_run(
            db, user_id=owner, session_id=sess_a0, run_state="awaiting_checkin"
        )
        await_run.checkin_timeout_at = now_utc + timedelta(minutes=5)
        db.add(
            CheckIn(
                id=uuid4(),
                user_id=owner,
                run_id=await_run.id,
                kind="final",
                response_state="open",
                asked_at=now_utc - timedelta(minutes=1),
            )
        )
        await db.commit()
        await_b_run_id = await_b_run.id
        await_run_id = await_run.id

    blocked_await = await client.delete(f"/api/v1/tasks/{task_a_id}")
    assert blocked_await.status_code == 409
    assert blocked_await.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"

    # Expired awaiting_checkin (checkin_timeout_at <= now) & no_respond CheckIn must NOT block delete_task
    # Convert await_run to closed, mark Step 0 & Session 0 as completed, Session 1 as incomplete,
    # and test unexpanded_scope cleared on cancelled Task
    sess_a1 = UUID(conf_a["task"]["sessions"][1]["session_id"])
    async with factory() as db:
        r_row = await db.get(SessionRun, await_run_id)
        assert r_row is not None
        r_row.run_state = "closed"
        r_row.actual_work_seconds = 1500
        r_row.actual_source = "timer_proxy"
        r_row.outcome = "completed"

        # Create an expired awaiting_checkin with no_respond CheckIn on sess_a1 (must NOT block!)
        exp_run = await _insert_session_run(
            db, user_id=owner, session_id=sess_a1, run_state="awaiting_checkin"
        )
        exp_run.checkin_timeout_at = now_utc - timedelta(seconds=10)
        db.add(
            CheckIn(
                id=uuid4(),
                user_id=owner,
                run_id=exp_run.id,
                kind="final",
                response_state="no_respond",
                asked_at=now_utc - timedelta(minutes=6),
            )
        )

        s0 = await db.get(Session, sess_a0)
        assert s0 is not None
        s0.status = "completed"
        st0 = await db.get(Step, s0.step_id)
        assert st0 is not None
        st0.status = "completed"

        s1 = await db.get(Session, sess_a1)
        assert s1 is not None
        s1.status = "incomplete"

        t_a = await db.get(Task, UUID(task_a_id))
        assert t_a is not None
        t_a.unexpanded_scope = "Phần chưa mở rộng"

        # Add unlinked pending proposal targeting task_a + linked SettingChangeRequest
        # (UserPreference.pending_proposal_id stays None, as in task-availability changes)
        prop_a = await _insert_pending_proposal(
            db, user_id=owner, target_task_id=UUID(task_a_id), base_schedule_revision=sched_rev
        )
        s_req_a = SettingChangeRequest(
            id=uuid4(),
            user_id=owner,
            application_state="pending_review",
            effective_revision=1,
            requested_availability_revision=2,
            effective_availability_revision=1,
            requested_changes={"target_task_id": task_a_id, "availability": {"timezone": "UTC"}},
            pending_proposal_id=prop_a.id,
        )
        db.add(s_req_a)
        prop_a_id = prop_a.id
        s_req_a_id = s_req_a.id
        await db.commit()

    # Delete task_a -> succeeds even while task_b has awaiting_checkin and task_a has expired/no_respond run,
    # preserves completed Step 0 / Session 0 / incomplete Session 1 / closed SessionRun,
    # clears unexpanded_scope, supersedes prop_a, and cancels SettingChangeRequest s_req_a
    del_a = await client.delete(f"/api/v1/tasks/{task_a_id}", headers=_headers("del-a-partial"))
    assert del_a.status_code == 200, del_a.text
    del_a_data = del_a.json()["data"]
    expected_cancelled = [
        s["session_id"]
        for s in conf_a["task"]["sessions"]
        if s["session_id"] not in {str(sess_a0), str(sess_a1)}
    ]
    assert del_a_data["cancelled_session_ids"] == expected_cancelled
    assert del_a_data["alarm_sync_state"] == "pending"

    async with factory() as db:
        rb_row = await db.get(SessionRun, await_b_run_id)
        assert rb_row is not None
        rb_row.run_state = "closed"
        rb_row.outcome = "completed"
        t_a_after = await db.get(Task, UUID(task_a_id))
        assert t_a_after is not None and t_a_after.unexpanded_scope is None
        s0_after = await db.get(Session, sess_a0)
        assert s0_after is not None and s0_after.status == "completed"
        s1_after = await db.get(Session, sess_a1)
        assert s1_after is not None and s1_after.status == "incomplete"
        st0_after = await db.get(Step, s0_after.step_id)
        assert st0_after is not None and st0_after.status == "completed"
        r_after = await db.get(SessionRun, await_run_id)
        assert r_after is not None and r_after.run_state == "closed"
        prop_after = await db.get(RescheduleProposal, prop_a_id)
        assert prop_after is not None
        assert prop_after.status == "superseded"
        assert prop_after.revision == 2
        s_req_after = await db.get(SettingChangeRequest, s_req_a_id)
        assert s_req_after is not None
        assert s_req_after.application_state == "cancelled"
        assert s_req_after.requested_revision is None
        assert s_req_after.requested_availability_revision is None
        assert s_req_after.revision == 2
        await db.commit()

    # Cancelled task cannot be resurrected via UF6/UF7 endpoints -> 404 RESOURCE_NOT_FOUND
    create_prop_resp = await client.post(
        "/api/v1/proposals",
        json={
            "trigger_reason": "user_reschedule",
            "target_task_id": task_a_id,
            "allowed_task_ids": [task_a_id],
            "expected_base_schedule_revision": del_a_data["schedule_revision"],
            "expected_source_revisions": {
                "task": del_a_data["task_revision"],
                "schedule": del_a_data["schedule_revision"],
                "availability": 0,
                "settings": 1,
                "calendar": 1,
            },
        },
        headers=_headers("prop-cancelled-task"),
    )
    assert create_prop_resp.status_code == 404
    assert create_prop_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    settings_resp = await client.post(
        "/api/v1/settings/change-requests",
        json={
            "expected_preferences_revision": 1,
            "target_task_id": task_a_id,
            "expected_task_revision": del_a_data["task_revision"],
            "changes": {"availability": _make_payload()["summary"]["availability"]},
        },
        headers=_headers("settings-cancelled-task"),
    )
    assert settings_resp.status_code == 404
    assert settings_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # Linked proposal via UserPreference.pending_proposal_id must NOT be superseded;
    # rebuild on that pending proposal targeting a cancelled task MUST return 404 RESOURCE_NOT_FOUND;
    # also test all-completed task gives alarm_sync_state="not_required"
    async with factory() as db:
        prop_b = await _insert_pending_proposal(
            db, user_id=owner, target_task_id=UUID(task_b_id), base_schedule_revision=del_a_data["schedule_revision"]
        )
        pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == owner))
        assert pref is not None
        pref.pending_proposal_id = prop_b.id

        # Mark all steps/sessions/reminders of task_b as terminal/completed
        b_steps = (await db.scalars(select(Step).where(Step.task_id == UUID(task_b_id)))).all()
        for st in b_steps:
            st.status = "completed"
        b_sessions = (
            await db.scalars(
                select(Session).join(Step, Step.id == Session.step_id).where(Step.task_id == UUID(task_b_id))
            )
        ).all()
        for s in b_sessions:
            s.status = "completed"
        b_rems = (
            await db.scalars(
                select(Reminder).where(Reminder.session_id.in_([s.id for s in b_sessions]))
            )
        ).all()
        for rem in b_rems:
            rem.delivery_state = "sent"
        await db.commit()
        prop_b_id = prop_b.id

    del_b = await client.delete(f"/api/v1/tasks/{task_b_id}", headers=_headers("del-b-all-done"))
    assert del_b.status_code == 200, del_b.text
    del_b_data = del_b.json()["data"]
    assert del_b_data["cancelled_session_ids"] == []
    assert del_b_data["alarm_sync_state"] == "not_required"

    async with factory() as db:
        prop_b_after = await db.get(RescheduleProposal, prop_b_id)
        assert prop_b_after is not None
        assert prop_b_after.status == "pending"
        assert prop_b_after.revision == 1

    rebuild_resp = await client.post(
        f"/api/v1/proposals/{prop_b_id}/rebuild",
        json={"expected_proposal_revision": 1},
        headers=_headers("rebuild-cancelled-task"),
    )
    assert rebuild_resp.status_code == 404
    assert rebuild_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_pg16_delete_task_full_e2e_calendar_sync_locks_and_constraints(pg16_env):
    client: httpx.AsyncClient = pg16_env["client"]
    factory = pg16_env["db"]
    owner: UUID = pg16_env["owner"]

    async with factory() as db:
        db.add(_make_connection(owner))
        await db.commit()

    conf1 = await _confirm_task_via_http(
        client, _make_payload(day_offset=2, start_hour=9, hours=4, title="PG16 Task 1")
    )
    conf2 = await _confirm_task_via_http(
        client, _make_payload(day_offset=2, start_hour=14, hours=4, title="PG16 Task 2")
    )
    task1_id = conf1["task"]["task_id"]
    task2_id = conf2["task"]["task_id"]
    sess1_uuids = [UUID(s["session_id"]) for s in conf1["task"]["sessions"]]
    sess2_uuids = [UUID(s["session_id"]) for s in conf2["task"]["sessions"]]

    # Sync initial create jobs on real PostgreSQL 16
    async with factory() as db:
        created_count = await process_calendar_sync_jobs(db, user_id=owner, limit=20)
        assert created_count == len(sess1_uuids) + len(sess2_uuids)

    # Seed pending proposal, linked SettingChangeRequest, expired awaiting_checkin,
    # historical incomplete session, & live run guard on PG16 partial indexes
    now_pg = datetime.now(UTC)
    async with factory() as db:
        prop = await _insert_pending_proposal(
            db,
            user_id=owner,
            target_task_id=UUID(task1_id),
            base_schedule_revision=conf2["schedule_version"]["version_number"],
        )
        s_req = SettingChangeRequest(
            id=uuid4(),
            user_id=owner,
            application_state="pending_review",
            effective_revision=1,
            requested_availability_revision=2,
            effective_availability_revision=1,
            requested_changes={"target_task_id": task1_id, "availability": {"timezone": "UTC"}},
            pending_proposal_id=prop.id,
        )
        db.add(s_req)

        s1_first = await db.get(Session, sess1_uuids[0])
        assert s1_first is not None
        s1_first.status = "incomplete"
        exp_run = await _insert_session_run(
            db, user_id=owner, session_id=sess1_uuids[0], run_state="awaiting_checkin"
        )
        exp_run.checkin_timeout_at = now_pg - timedelta(seconds=30)
        db.add(
            CheckIn(
                id=uuid4(),
                user_id=owner,
                run_id=exp_run.id,
                kind="final",
                response_state="no_respond",
                asked_at=now_pg - timedelta(minutes=6),
            )
        )

        run = await _insert_session_run(
            db, user_id=owner, session_id=sess2_uuids[0], run_state="running"
        )
        await db.commit()
        prop_id = prop.id
        s_req_id = s_req.id
        run_id = run.id
        exp_run_id = exp_run.id
        exp_run_rev = exp_run.revision

    # 1. Blocked by active running SessionRun
    blocked = await client.delete(
        f"/api/v1/tasks/{task1_id}", params={"expected_task_revision": 1}
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"

    # Close run, verify revision conflict check
    async with factory() as db:
        r_row = await db.get(SessionRun, run_id)
        assert r_row is not None
        r_row.run_state = "closed"
        r_row.ended_at = datetime.now(UTC)
        await db.commit()

    conflict = await client.delete(
        f"/api/v1/tasks/{task1_id}", params={"expected_task_revision": 99}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"

    # 2. Delete Task 1 on real PG16 with Idempotency-Key
    key = "pg16-del-task-1"
    del_resp = await client.delete(
        f"/api/v1/tasks/{task1_id}",
        params={"expected_task_revision": 1},
        headers=_headers(key),
    )
    assert del_resp.status_code == 200, del_resp.text
    del_data = del_resp.json()["data"]
    assert del_data["status"] == "cancelled"
    assert del_data["task_revision"] == 2
    assert del_data["schedule_revision"] == conf2["schedule_version"]["version_number"] + 1
    assert del_data["alarm_sync_state"] == "pending"
    assert del_data["calendar_sync_summary"] == "pending"

    # 3. Replay on PG16 returns identical payload without bumping ScheduleVersion
    replay = await client.delete(
        f"/api/v1/tasks/{task1_id}",
        params={"expected_task_revision": 1},
        headers=_headers(key),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == del_data

    # 4. Read endpoints reflect soft delete (cancelled sessions omitted; preserved incomplete session retained)
    assert (await client.get(f"/api/v1/tasks/{task1_id}")).status_code == 404
    listed = (await client.get("/api/v1/tasks")).json()["data"]["items"]
    assert [item["task_id"] for item in listed] == [task2_id]
    assert str(sess1_uuids[0]) not in del_data["cancelled_session_ids"]
    assert set(del_data["cancelled_session_ids"]) == {str(x) for x in sess1_uuids[1:]}

    win_from = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    win_to = (datetime.now(UTC) + timedelta(days=4)).isoformat()
    sched_before = (
        await client.get("/api/v1/schedule", params={"from": win_from, "to": win_to})
    ).json()["data"]
    sched_ids = {item["session_id"] for item in sched_before["items"]}
    assert not (sched_ids & set(del_data["cancelled_session_ids"]))
    assert sched_ids == {str(sess1_uuids[0]), *{str(x) for x in sess2_uuids}}
    assert sched_before["calendar_sync_summary"] == "pending"

    # 5. Execute CalendarSyncWorker delete jobs on PG16 & verify FK RESTRICT rows remain intact
    cancelled_uuids = [UUID(sid) for sid in del_data["cancelled_session_ids"]]
    async with factory() as db:
        prop_after = await db.get(RescheduleProposal, prop_id)
        assert prop_after is not None and prop_after.status == "superseded"

        s_req_after = await db.get(SettingChangeRequest, s_req_id)
        assert s_req_after is not None and s_req_after.application_state == "cancelled"

        s1_first_after = await db.get(Session, sess1_uuids[0])
        assert s1_first_after is not None and s1_first_after.status == "incomplete"

        deleted_count = await process_calendar_sync_jobs(db, user_id=owner, limit=20)
        assert deleted_count == len(cancelled_uuids)

        links_after = (
            await db.scalars(
                select(CalendarEventLink).where(CalendarEventLink.session_id.in_(cancelled_uuids))
            )
        ).all()
        assert len(links_after) == len(cancelled_uuids)
        assert all(lk.is_current is False and lk.sync_state == "deleted" for lk in links_after)

        jobs_after = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.target_schedule_revision == del_data["schedule_revision"],
                )
            )
        ).all()
        assert len(jobs_after) == len(cancelled_uuids)
        assert all(j.operation == "delete" and j.job_state == "completed" for j in jobs_after)

    # 6. Cancelled task cannot be resurrected via UF6/UF7 on PG16 -> 404 RESOURCE_NOT_FOUND
    pg_prop_resp = await client.post(
        "/api/v1/proposals",
        json={
            "trigger_reason": "user_reschedule",
            "target_task_id": task1_id,
            "allowed_task_ids": [task1_id],
            "expected_base_schedule_revision": del_data["schedule_revision"],
            "expected_source_revisions": {
                "task": del_data["task_revision"],
                "schedule": del_data["schedule_revision"],
                "availability": 0,
                "settings": 1,
                "calendar": 1,
            },
        },
        headers=_headers("pg16-prop-cancelled"),
    )
    assert pg_prop_resp.status_code == 404
    assert pg_prop_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    pg_cont_resp = await client.post(
        "/api/v1/proposals",
        json={
            "trigger_reason": "checkin_incomplete",
            "target_task_id": task1_id,
            "allowed_task_ids": [task1_id],
            "expected_base_schedule_revision": del_data["schedule_revision"],
            "expected_source_revisions": {
                "task": del_data["task_revision"],
                "schedule": del_data["schedule_revision"],
                "availability": 0,
                "settings": 1,
                "calendar": 1,
            },
            "continuation_request": {
                "source_run_id": str(exp_run_id),
                "expected_run_revision": exp_run_rev,
                "additional_work_seconds": 600,
            },
        },
        headers=_headers("pg16-cont-cancelled"),
    )
    assert pg_cont_resp.status_code == 404
    assert pg_cont_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    pg_settings_resp = await client.post(
        "/api/v1/settings/change-requests",
        json={
            "expected_preferences_revision": 1,
            "target_task_id": task1_id,
            "expected_task_revision": del_data["task_revision"],
            "changes": {"availability": _make_payload()["summary"]["availability"]},
        },
        headers=_headers("pg16-settings-cancelled"),
    )
    assert pg_settings_resp.status_code == 404
    assert pg_settings_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    sched_after = (
        await client.get("/api/v1/schedule", params={"from": win_from, "to": win_to})
    ).json()["data"]
    assert sched_after["calendar_sync_summary"] == "synced"
