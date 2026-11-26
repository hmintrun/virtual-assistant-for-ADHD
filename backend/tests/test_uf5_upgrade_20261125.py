"""Bộ kiểm thử nâng cấp Phase 1 BE-UF5 (14 async cases theo UF5_UPGRADE_DETAILED_PLAN_20261125 §8.1).

Bao phủ:
1.  BE-UF5-01 (E2E-UF5-03): skip_step hủy chuỗi 3 bước (Session/Reminder/Calendar delete) + giữ ScheduleVersion.
2.  BE-UF5-01 (E2E-UF5-06): actual-corrections + step_completed=True hủy phiên thừa, giữ sess1 trên GET /schedule.
3.  BE-UF5-01: Unattempted create job optimization (không sinh job delete thừa).
4.  BE-UF5-01: task_completed=True đa Step hủy phiên thừa toàn Task, không giả lập Step 2 completed, giữ ScheduleVersion.
5.  BE-UF5-01: Correction Undo chuỗi 3 bước (Bẫy #6: completed -> skip_step -> incomplete -> start sess1b rev=1).
6.  BE-UF5-01: Multi-revision Calendar Idempotency Key (Bẫy #4 & #5: rev 1 -> 2 -> 3 + worker delete -> rev 4 link mới).
7.  BE-UF5-01: skip_session & postpone_session KHÔNG hủy phiên tương lai.
8.  BE-UF5-01: Defense-in-depth ở GET /schedule, POST /proposals & GET upcoming-schedule-context khi DB bẩn.
9.  BE-UF5-01: Bảo vệ xác nhận mới hơn từ Run 2 khi sửa Run 1 về incomplete (không khôi phục nhầm sess1c).
10. BE-UF5-02: Explicit False vs None trên phiên cuối (giữ hợp đồng W3-06 & chặn khi còn sess2).
11. BE-UF5-03: no_respond trả lời bù expected_response_revision=0 + skip_step.
12. BE-UF5-03: actual-corrections + 0 Live GCal Call Guard.
13. BE-UF5-01 + BE-UF5-02: skip_step -> completed(step_completed=None, task_completed=None) khôi phục Step & sess2.
14. BE-UF5-01: Undo Calendar State Machine nhánh (a) leased delete và (b) create attempted (attempt_count=1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.execution import CheckIn, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import CommandReceipt, Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.user import CurrentUser
from app.services import reschedule_service, run_service
from app.services.calendar_sync_worker import process_calendar_sync_jobs
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import FakeCalendarClient, reset_fake_calendar_client

TZ_VN = timezone(timedelta(hours=7))


def _auth_headers(user_id: UUID, idem_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"uf5_tester_{user_id.hex[:8]}@example.test",
        display_name="Quốc Hưng",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-token-{user_id.hex[:8]}"}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return headers


@asynccontextmanager
async def _uf5_test_app(
    sqlite_migration: tuple[str, Any],
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield sf, client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _clock_at(monkeypatch: pytest.MonkeyPatch, target_dt: datetime) -> None:
    utc_dt = target_dt.astimezone(UTC)
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or utc_dt).astimezone(UTC),
    )
    monkeypatch.setattr(
        reschedule_service,
        "_current_utc_now",
        lambda now=None: (now or utc_dt).astimezone(UTC),
    )


async def _seed_uf5_plan(
    db: AsyncSession,
    *,
    with_step2: bool = False,
    with_sess1b: bool = False,
    with_sess1c: bool = False,
    calendar_mode: str = "synced",
) -> dict[str, Any]:
    """Tạo dữ liệu sạch chuẩn BE-UF5 (User, Availability, Calendar, Task, Step1/2, Sessions, Reminders, Run1, CheckIn1)."""
    user_id = uuid4()
    conn_id = uuid4()
    avail_id = uuid4()
    task_id = uuid4()
    step1_id = uuid4()
    step2_id = uuid4()

    t_s1_start = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    t_s1_end = t_s1_start + timedelta(minutes=25)
    t_s1b_start = t_s1_end + timedelta(minutes=20)
    t_s1b_end = t_s1b_start + timedelta(minutes=25)
    t_s1c_start = t_s1b_end + timedelta(minutes=20)
    t_s1c_end = t_s1c_start + timedelta(minutes=25)
    t_s2_start = (t_s1c_end if with_sess1c else t_s1b_end) + timedelta(minutes=20)
    t_s2_end = t_s2_start + timedelta(minutes=25)
    deadline_dt = datetime(2026, 11, 5, 18, 0, 0, tzinfo=TZ_VN)

    db.add(
        User(
            id=user_id,
            email=f"uf5_{user_id.hex[:8]}@proplan.test",
            hashed_password="pw",
            display_name="Tester",
        )
    )
    await db.flush()

    db.add(
        UserPreferenceRow(
            user_id=user_id,
            revision=1,
            effective_revision=1,
            work_interval_seconds=1500,
            rest_interval_seconds=0,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=1.2,
            reminder_style="gentle",
        )
    )
    db.add(
        AvailabilityRow(
            id=avail_id,
            user_id=user_id,
            revision=1,
            timezone="Asia/Ho_Chi_Minh",
            weekly_rules=[
                {"day_of_week": d, "start_local_time": "08:00", "end_local_time": "22:00"}
                for d in range(7)
            ],
            date_overrides=[],
            task_limits=[],
            source="manual",
            confirmed_at=t_s1_start - timedelta(days=1),
        )
    )
    await db.flush()
    creds = {
        "access_token": "acc_uf5_1",
        "refresh_token": "ref_uf5_1",
        "expires_in": 3600,
        "stored_at": datetime.now(UTC).isoformat(),
        "user_id": str(user_id),
        "connection_id": str(conn_id),
    }
    db.add(
        CalendarConnection(
            id=conn_id,
            user_id=user_id,
            revision=1,
            provider="google",
            account_ref=f"uf5_{user_id.hex[:8]}@proplan.test",
            read_calendar_ids=["primary"],
            write_calendar_id="proplan_secondary",
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            connection_state="connected",
            credential_ref=encrypt_credentials(creds),
            last_successful_sync_at=t_s1_start - timedelta(minutes=10),
        )
    )
    db.add(
        PlanningContext(
            id=uuid4(),
            user_id=user_id,
            revision=1,
            availability_id=avail_id,
            availability_revision=1,
            selected_calendar_ids=["primary"],
            query_start=datetime(2026, 11, 2, 0, 0, 0, tzinfo=TZ_VN),
            query_end=datetime(2026, 11, 9, 0, 0, 0, tzinfo=TZ_VN),
            busy_intervals=[],
            fetched_at=t_s1_start - timedelta(minutes=10),
            sync_state="ok",
            calendar_errors=[],
            event_snapshots=[],
            coverage_state="complete",
            manual_busy_intervals=[],
            excluded_sources=[],
            selected_calendar_sources=[],
        )
    )
    await db.flush()

    db.add(
        TaskRow(
            id=task_id,
            user_id=user_id,
            revision=1,
            title="Hoàn thiện báo cáo UF5",
            description="Đặc tả và kiểm thử UF5",
            scope="Báo cáo hoàn chỉnh",
            time_constraint={
                "mode": "deadline",
                "at": deadline_dt.isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            },
            importance=1,
            status="in_progress",
            decomposition_state="complete",
            unexpanded_scope=None,
        )
    )
    await db.flush()

    db.add(
        StepRow(
            id=step1_id,
            user_id=user_id,
            task_id=task_id,
            revision=1,
            order_index=0,
            title="Bước 1: Thu thập dữ liệu",
            expected_output="Bảng tổng hợp dữ liệu",
            estimated_base_seconds=3000,
            remaining_base_seconds=1800,
            buffer_multiplier=1.2,
            estimate_source="ai",
            status="in_progress",
        )
    )
    if with_step2:
        db.add(
            StepRow(
                id=step2_id,
                user_id=user_id,
                task_id=task_id,
                revision=1,
                order_index=1,
                title="Bước 2: Viết tổng kết",
                expected_output="Trang tổng kết",
                estimated_base_seconds=1500,
                remaining_base_seconds=1500,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="pending",
            )
        )
    await db.flush()

    def _make_session(sid: UUID, st_id: UUID, s_start: datetime, s_end: datetime, title: str, status: str) -> SessionRow:
        return SessionRow(
            id=sid,
            user_id=user_id,
            step_id=st_id,
            revision=1,
            scheduled_start=s_start,
            scheduled_end=s_end,
            title=title,
            session_goal=f"Mục tiêu {title}",
            expected_output=f"Đầu ra {title}",
            micro_task_text="Mở tài liệu",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=0,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1500,
            schedule_revision=1,
            status=status,
            start_state="started" if status == "awaiting_checkin" else "waiting",
            start_decision_due_at=s_start + timedelta(minutes=15),
        )

    sess1_id = uuid4()
    sess1b_id = uuid4()
    sess1c_id = uuid4()
    sess2_id = uuid4()

    db.add(_make_session(sess1_id, step1_id, t_s1_start, t_s1_end, "Phiên 1A", "awaiting_checkin"))
    surplus_specs: list[tuple[UUID, UUID, datetime, datetime, str]] = []
    if with_sess1b:
        db.add(_make_session(sess1b_id, step1_id, t_s1b_start, t_s1b_end, "Phiên 1B", "scheduled"))
        surplus_specs.append((sess1b_id, step1_id, t_s1b_start, t_s1b_end, "Phiên 1B"))
    if with_sess1c:
        db.add(_make_session(sess1c_id, step1_id, t_s1c_start, t_s1c_end, "Phiên 1C", "scheduled"))
        surplus_specs.append((sess1c_id, step1_id, t_s1c_start, t_s1c_end, "Phiên 1C"))
    if with_step2:
        db.add(_make_session(sess2_id, step2_id, t_s2_start, t_s2_end, "Phiên 2", "scheduled"))
        surplus_specs.append((sess2_id, step2_id, t_s2_start, t_s2_end, "Phiên 2"))
    await db.flush()

    db.add(
        ScheduleVersionRow(
            user_id=user_id,
            version_number=1,
            base_version=None,
            approved_at=t_s1_start - timedelta(days=1),
            affected_task_ids=[str(task_id)],
            min_inter_session_gap_seconds=900,
        )
    )

    reminders: dict[UUID, UUID] = {}
    links: dict[UUID, UUID] = {}
    create_jobs: dict[UUID, UUID] = {}

    for sid, _st_id, s_start, s_end, s_title in surplus_specs:
        rem_id = uuid4()
        db.add(
            Reminder(
                id=rem_id,
                user_id=user_id,
                session_id=sid,
                schedule_revision=1,
                kind="pre_session",
                fire_at=s_start - timedelta(seconds=300),
                dedupe_key=f"session:{sid}:version:1:reminder:300",
                delivery_state="scheduled",
            )
        )
        reminders[sid] = rem_id

        if calendar_mode != "none":
            link_id = uuid4()
            event_id = f"session{sid.hex}"
            is_synced = calendar_mode == "synced"
            db.add(
                CalendarEventLink(
                    id=link_id,
                    user_id=user_id,
                    session_id=sid,
                    connection_id=conn_id,
                    google_calendar_id="proplan_secondary",
                    google_event_id=event_id,
                    google_etag=f"etag_{sid.hex[:8]}" if is_synced else None,
                    desired_schedule_revision=1,
                    synced_schedule_revision=1 if is_synced else None,
                    sync_state="synced" if is_synced else "pending",
                    is_current=True,
                )
            )
            await db.flush()
            links[sid] = link_id

            if not is_synced:
                job_id = uuid4()
                attempt_cnt = 1 if calendar_mode == "create_attempted" else 0
                db.add(
                    CalendarSyncJob(
                        id=job_id,
                        user_id=user_id,
                        connection_id=conn_id,
                        event_link_id=link_id,
                        target_kind="session_event",
                        target_ref=str(sid),
                        operation="create",
                        target_schedule_revision=1,
                        expected_google_etag=None,
                        payload_snapshot={
                            "id": event_id,
                            "summary": f"[ProPlan] {s_title}",
                            "start": {"dateTime": s_start.isoformat(), "timeZone": "Asia/Ho_Chi_Minh"},
                            "end": {"dateTime": s_end.isoformat(), "timeZone": "Asia/Ho_Chi_Minh"},
                        },
                        idempotency_key=f"sync_session:{sid}:v1:create",
                        job_state="queued",
                        attempt_count=attempt_cnt,
                    )
                )
                create_jobs[sid] = job_id

    run1_id = uuid4()
    checkin1_id = uuid4()
    db.add(
        SessionRun(
            id=run1_id,
            user_id=user_id,
            session_id=sess1_id,
            device_id="pixel_8",
            run_state="awaiting_checkin",
            started_at=t_s1_start,
            ended_at=t_s1_end,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            actual_work_seconds=1500,
            actual_source="timer",
            outcome="unknown",
            checkin_timeout_at=t_s1_end + timedelta(seconds=300),
            dnd_release_at=t_s1_end + timedelta(seconds=300),
            settings_snapshot={
                "work_interval_seconds": 1500,
                "rest_interval_seconds": 0,
                "micro_task_seconds": 120,
                "buffer_multiplier": 1.2,
                "reminder_style": "gentle",
            },
            revision=1,
        )
    )
    await db.flush()
    db.add(
        CheckIn(
            id=checkin1_id,
            user_id=user_id,
            run_id=run1_id,
            kind="final",
            response_state="open",
            session_outcome="unknown",
            asked_at=t_s1_end,
            response_revision=0,
        )
    )
    await db.commit()
    return {
        "user_id": user_id,
        "conn_id": conn_id,
        "task_id": task_id,
        "step1_id": step1_id,
        "step2_id": step2_id if with_step2 else None,
        "sess1_id": sess1_id,
        "sess1b_id": sess1b_id if with_sess1b else None,
        "sess1c_id": sess1c_id if with_sess1c else None,
        "sess2_id": sess2_id if with_step2 else None,
        "run1_id": run1_id,
        "checkin1_id": checkin1_id,
        "reminders": reminders,
        "links": links,
        "create_jobs": create_jobs,
        "t_s1_start": t_s1_start,
        "t_s1_end": t_s1_end,
        "t_s1b_start": t_s1b_start,
        "t_s1b_end": t_s1b_end,
        "t_s2_start": t_s2_start,
        "t_s2_end": t_s2_end,
    }


@pytest.mark.asyncio
async def test_01_skip_step_3step_cascade_and_schedule_filtering(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 1 (E2E-UF5-03): skip_step hủy phiên thừa, Reminder, tạo CalendarSyncJob(delete), giữ ScheduleVersion."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=15))

        resp = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-01"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=15)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert resp.status_code == 200, resp.text

        async with sf() as db:
            step1 = await db.get(StepRow, env["step1_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            rem1b = await db.get(Reminder, env["reminders"][env["sess1b_id"]])
            link1b = await db.get(CalendarEventLink, env["links"][env["sess1b_id"]])
            del_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.event_link_id == env["links"][env["sess1b_id"]],
                        CalendarSyncJob.operation == "delete",
                    )
                )
            ).all()
            max_ver = await db.scalar(
                select(func.max(ScheduleVersionRow.version_number)).where(
                    ScheduleVersionRow.user_id == env["user_id"]
                )
            )

            assert step1 is not None and step1.status == "skipped" and step1.remaining_base_seconds == 0
            assert sess1b is not None and sess1b.status == "cancelled" and sess1b.start_state == "superseded"
            assert sess1b.schedule_revision == 1
            assert rem1b is not None and rem1b.delivery_state == "cancelled"
            assert link1b is not None and link1b.sync_state == "pending" and link1b.is_current is True
            assert len(del_jobs) == 1 and del_jobs[0].job_state == "queued"
            assert max_ver == 1

        sched_resp = await client.get(
            "/api/v1/schedule",
            headers=_auth_headers(env["user_id"]),
            params={
                "from": (env["t_s1_start"] - timedelta(hours=1)).isoformat(),
                "to": (env["t_s1b_end"] + timedelta(hours=2)).isoformat(),
            },
        )
        assert sched_resp.status_code == 200, sched_resp.text
        items = sched_resp.json()["data"]["items"]
        returned_ids = {item["session_id"]: item["status"] for item in items}
        assert str(env["sess1_id"]) in returned_ids
        assert returned_ids[str(env["sess1_id"])] == "skipped"
        assert str(env["sess1b_id"]) not in returned_ids


@pytest.mark.asyncio
async def test_02_actual_correction_then_step_completed_keeps_completed_session_on_schedule(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 2 (E2E-UF5-06): actual-corrections + step_completed=True hủy sess thừa nhưng giữ sess1 trên GET /schedule."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=20))

        r_corr = await client.post(
            f"/api/v1/runs/{env['run1_id']}/actual-corrections",
            headers=_auth_headers(env["user_id"], "cmd-uf5-02-corr"),
            json={
                "expected_run_revision": 1,
                "self_report_work_seconds": 1200,
                "reason": "Tạm ngừng nghe điện thoại 5 phút",
                "occurred_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
            },
        )
        assert r_corr.status_code == 200, r_corr.text

        r_ans = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-02-ans"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": False,
            },
        )
        assert r_ans.status_code == 200, r_ans.text

        async with sf() as db:
            run_row = await db.get(SessionRun, env["run1_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            rem1b = await db.get(Reminder, env["reminders"][env["sess1b_id"]])
            del_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.operation == "delete",
                    )
                )
            ).all()
            assert run_row is not None
            assert run_row.actual_source == "self_report"
            assert run_row.actual_work_seconds == 1200
            assert sess1b is not None and sess1b.status == "cancelled"
            assert rem1b is not None and rem1b.delivery_state == "cancelled"
            assert len(del_jobs) == 1 and del_jobs[0].job_state == "queued"

        sched_resp = await client.get(
            "/api/v1/schedule",
            headers=_auth_headers(env["user_id"]),
            params={
                "from": (env["t_s1_start"] - timedelta(hours=1)).isoformat(),
                "to": (env["t_s1b_end"] + timedelta(hours=2)).isoformat(),
            },
        )
        assert sched_resp.status_code == 200
        returned = {item["session_id"]: item["status"] for item in sched_resp.json()["data"]["items"]}
        assert returned.get(str(env["sess1_id"])) == "completed"
        assert str(env["sess1b_id"]) not in returned


@pytest.mark.asyncio
async def test_03_unattempted_create_job_closed_without_delete_job(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 3: Link có job create chưa chạy (attempt_count=0) -> đóng job create & link, KHÔNG tạo job delete."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="create_unattempted")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

        resp = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-03"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert resp.status_code == 200, resp.text

        async with sf() as db:
            create_job = await db.get(CalendarSyncJob, env["create_jobs"][env["sess1b_id"]])
            link = await db.get(CalendarEventLink, env["links"][env["sess1b_id"]])
            del_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.operation == "delete",
                    )
                )
            ).all()
            assert create_job is not None and create_job.job_state == "completed"
            assert link is not None and link.is_current is False and link.sync_state == "deleted"
            assert del_jobs == []


@pytest.mark.asyncio
async def test_04_task_completed_cancels_all_task_surplus_without_faking_step2(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 4: task_completed=True hủy cả sess1b và sess2, giữ step2.status=='pending' và ScheduleVersion==1."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(
                db, with_sess1b=True, with_step2=True, calendar_mode="synced"
            )
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

        resp = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-04"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": True,
            },
        )
        assert resp.status_code == 200, resp.text

        async with sf() as db:
            task = await db.get(TaskRow, env["task_id"])
            step1 = await db.get(StepRow, env["step1_id"])
            step2 = await db.get(StepRow, env["step2_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            sess2 = await db.get(SessionRow, env["sess2_id"])
            ver_count = await db.scalar(
                select(func.count(ScheduleVersionRow.id)).where(
                    ScheduleVersionRow.user_id == env["user_id"]
                )
            )
            assert task is not None and task.status == "completed"
            assert step1 is not None and step1.status == "completed"
            assert step2 is not None and step2.status == "pending"
            assert sess1b is not None and sess1b.status == "cancelled"
            assert sess2 is not None and sess2.status == "cancelled"
            assert ver_count == 1


@pytest.mark.asyncio
async def test_05_correction_undo_3step_chain_restores_by_layer_and_allows_start_rev1(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 5 (Bẫy #6): rev 1 (task_completed) -> rev 2 (skip_step: khôi phục sess2, giữ sess1b cancelled) -> rev 3 (incomplete: khôi phục sess1b, start rev=1 -> 201)."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(
                db, with_sess1b=True, with_step2=True, calendar_mode="synced"
            )
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

        # rev 0 -> 1: task_completed=True
        r1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-05-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": True,
            },
        )
        assert r1.status_code == 200

        # rev 1 -> 2: skip_step (chỉ Step 1 skipped, Task hồi in_progress -> sess2 phải hồi, sess1b vẫn cancelled)
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=20))
        r2 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-05-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r2.status_code == 200, r2.text

        async with sf() as db:
            task = await db.get(TaskRow, env["task_id"])
            step1 = await db.get(StepRow, env["step1_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            sess2 = await db.get(SessionRow, env["sess2_id"])
            rem2 = await db.get(Reminder, env["reminders"][env["sess2_id"]])
            link2 = await db.get(CalendarEventLink, env["links"][env["sess2_id"]])
            del2 = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == env["links"][env["sess2_id"]],
                    CalendarSyncJob.operation == "delete",
                )
            )
            assert task is not None and task.status == "in_progress"
            assert step1 is not None and step1.status == "skipped"
            assert sess1b is not None and sess1b.status == "cancelled"
            assert sess2 is not None and sess2.status == "scheduled" and sess2.start_state == "waiting"
            assert rem2 is not None and rem2.delivery_state == "scheduled"
            assert link2 is not None and link2.sync_state == "synced"
            assert del2 is not None and del2.job_state == "completed"

        # rev 2 -> 3: incomplete -> step1 hồi in_progress, sess1b hồi scheduled với schedule_revision=1
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=30))
        r3 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-05-r3"),
            json={
                "expected_response_revision": 2,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=30)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r3.status_code == 200, r3.text

        async with sf() as db:
            step1 = await db.get(StepRow, env["step1_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            rem1b = await db.get(Reminder, env["reminders"][env["sess1b_id"]])
            assert step1 is not None and step1.status == "in_progress" and step1.remaining_base_seconds == 1800
            assert sess1b is not None and sess1b.status == "scheduled" and sess1b.schedule_revision == 1
            assert rem1b is not None and rem1b.delivery_state == "scheduled"

        # Khởi động sess1b với expected_schedule_revision=1 -> 201 Created!
        _clock_at(monkeypatch, env["t_s1b_start"])
        r_start = await client.post(
            f"/api/v1/sessions/{env['sess1b_id']}/start",
            headers=_auth_headers(env["user_id"], "cmd-uf5-05-start-1b"),
            json={
                "expected_schedule_revision": 1,
                "device_id": "pixel_8",
                "requested_start_at": env["t_s1b_start"].isoformat(),
            },
        )
        assert r_start.status_code == 201, r_start.text


@pytest.mark.asyncio
async def test_06_multi_revision_calendar_idempotency_and_post_worker_delete_restore(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 6 (Bẫy #4 & #5): rev 1 -> 2 -> 3 không đụng idempotency_key; worker chạy delete rồi rev 4 tạo link mới."""
    reset_fake_calendar_client()
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

        # rev 1: skip_step
        r1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-06-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r1.status_code == 200

        # rev 2: incomplete (đóng job delete rev 1)
        r2 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-06-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r2.status_code == 200

        # rev 3: skip_step lần nữa trên cùng schedule_revision=1 -> 0 IntegrityError, tạo delete job mới
        r3 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-06-r3"),
            json={
                "expected_response_revision": 2,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=30)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r3.status_code == 200, r3.text

        async with sf() as db:
            del_jobs = (
                await db.scalars(
                    select(CalendarSyncJob)
                    .where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.event_link_id == env["links"][env["sess1b_id"]],
                        CalendarSyncJob.operation == "delete",
                    )
                    .order_by(CalendarSyncJob.created_at.asc())
                )
            ).all()
            assert len(del_jobs) == 2
            assert del_jobs[0].job_state == "completed"
            assert del_jobs[1].job_state == "queued"
            assert ":r3:" in del_jobs[1].idempotency_key

            # Giả lập worker đã thực thi xong delete job của rev 3
            processed = await process_calendar_sync_jobs(db, user_id=env["user_id"], limit=10)
            assert processed == 1
            await db.commit()

            old_link = await db.get(CalendarEventLink, env["links"][env["sess1b_id"]])
            assert old_link is not None
            assert old_link.is_current is False and old_link.sync_state == "deleted"

        # rev 4: Undo về incomplete -> phải tạo link mới qua _next_session_google_event_id + job create mới
        r4 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-06-r4"),
            json={
                "expected_response_revision": 3,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=40)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r4.status_code == 200, r4.text

        async with sf() as db:
            all_links = (
                await db.scalars(
                    select(CalendarEventLink).where(
                        CalendarEventLink.user_id == env["user_id"],
                        CalendarEventLink.session_id == env["sess1b_id"],
                    )
                )
            ).all()
            current_links = [lk for lk in all_links if lk.is_current]
            assert len(all_links) == 2
            assert len(current_links) == 1
            new_link = current_links[0]
            assert new_link.id != env["links"][env["sess1b_id"]]
            assert new_link.google_event_id == f"session{env['sess1b_id'].hex}v2"
            assert new_link.sync_state == "pending"

            new_create_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.event_link_id == new_link.id,
                        CalendarSyncJob.operation == "create",
                    )
                )
            ).all()
            assert len(new_create_jobs) == 1
            assert new_create_jobs[0].job_state == "queued"
            assert new_create_jobs[0].payload_snapshot["id"] == new_link.google_event_id


@pytest.mark.asyncio
async def test_07_skip_session_and_postpone_session_do_not_cancel_future_sessions(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 7: skip_session và postpone_session giữ nguyên sess2 scheduled, Reminder & CalendarEventLink."""
    for chip in ("skip_session", "postpone_session"):
        async with _uf5_test_app(sqlite_migration) as (sf, client):
            async with sf() as db:
                env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
            _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

            resp = await client.put(
                f"/api/v1/check-ins/{env['checkin1_id']}/response",
                headers=_auth_headers(env["user_id"], f"cmd-uf5-07-{chip}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                    "session_outcome": "skipped",
                    "skip_scope": chip,
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert resp.status_code == 200, resp.text

            async with sf() as db:
                step1 = await db.get(StepRow, env["step1_id"])
                sess1 = await db.get(SessionRow, env["sess1_id"])
                sess1b = await db.get(SessionRow, env["sess1b_id"])
                rem1b = await db.get(Reminder, env["reminders"][env["sess1b_id"]])
                link1b = await db.get(CalendarEventLink, env["links"][env["sess1b_id"]])
                del_jobs = (
                    await db.scalars(
                        select(CalendarSyncJob).where(
                            CalendarSyncJob.user_id == env["user_id"],
                            CalendarSyncJob.operation == "delete",
                        )
                    )
                ).all()
                assert sess1 is not None and sess1.status == "skipped"
                assert step1 is not None and step1.status == "in_progress"
                assert sess1b is not None and sess1b.status == "scheduled"
                assert rem1b is not None and rem1b.delivery_state == "scheduled"
                assert link1b is not None and link1b.sync_state == "synced"
                assert del_jobs == []


@pytest.mark.asyncio
async def test_08_defense_in_depth_schedule_and_uf6_filter_stale_scheduled_sessions(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 8: Khi DB bẩn (step1='skipped' nhưng sess1b='scheduled' còn sót), GET /schedule, POST /proposals và GET upcoming-schedule-context đều lọc bỏ sess1b."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(
                db, with_sess1b=True, with_step2=True, calendar_mode="none"
            )
            # Chủ động tạo tình huống bẩn: step1 đã skipped, sess1 đã completed, nhưng sess1b vẫn scheduled
            step1 = await db.get(StepRow, env["step1_id"])
            sess1 = await db.get(SessionRow, env["sess1_id"])
            run1 = await db.get(SessionRun, env["run1_id"])
            ci1 = await db.get(CheckIn, env["checkin1_id"])
            assert step1 and sess1 and run1 and ci1
            step1.status = "skipped"
            step1.remaining_base_seconds = 0
            sess1.status = "completed"
            run1.run_state = "closed"
            run1.outcome = "completed"
            ci1.response_state = "answered"
            ci1.session_outcome = "completed"
            ci1.response_revision = 1
            await db.commit()

        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=30))

        # 1. GET /schedule: ẩn sess1b (stale scheduled), vẫn giữ sess1 (completed) và sess2 (scheduled của step2 pending)
        r_sched = await client.get(
            "/api/v1/schedule",
            headers=_auth_headers(env["user_id"]),
            params={
                "from": (env["t_s1_start"] - timedelta(hours=1)).isoformat(),
                "to": (env["t_s2_end"] + timedelta(hours=2)).isoformat(),
            },
        )
        assert r_sched.status_code == 200, r_sched.text
        sched_map = {item["session_id"]: item["status"] for item in r_sched.json()["data"]["items"]}
        assert sched_map.get(str(env["sess1_id"])) == "completed"
        assert str(env["sess1b_id"]) not in sched_map
        assert sched_map.get(str(env["sess2_id"])) == "scheduled"

        # 2. GET /runs/{run_id}/upcoming-schedule-context: không liệt kê sess1b
        r_ctx = await client.get(
            f"/api/v1/runs/{env['run1_id']}/upcoming-schedule-context",
            headers=_auth_headers(env["user_id"]),
            params={
                "from": env["t_s1_end"].isoformat(),
                "to": (env["t_s2_end"] + timedelta(hours=2)).isoformat(),
            },
        )
        assert r_ctx.status_code == 200, r_ctx.text
        ctx_sess_ids = {
            item["session_id"]
            for item in r_ctx.json()["data"]["timeline"]
            if item.get("kind") == "session"
        }
        assert str(env["sess1b_id"]) not in ctx_sess_ids
        assert str(env["sess2_id"]) in ctx_sess_ids

        # 3. POST /api/v1/proposals: _build_normal_proposal_plan không đưa sess1b vào sessions_to_move
        r_prop = await client.post(
            "/api/v1/proposals",
            headers=_auth_headers(env["user_id"], "cmd-uf5-08-prop"),
            json={
                "trigger_reason": "early_completion_pull_forward",
                "target_task_id": str(env["task_id"]),
                "allowed_task_ids": [str(env["task_id"])],
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": {
                    "task": 1,
                    "schedule": 1,
                    "availability": 1,
                    "settings": 1,
                    "calendar": 1,
                },
                "request_text": "Kéo lịch bước 2 lên sớm hơn",
            },
        )
        assert r_prop.status_code == 201, r_prop.text
        moved_ids = {
            m["session_id"] for m in r_prop.json()["data"]["proposed_changes"]["sessions_to_move"]
        }
        assert str(env["sess1b_id"]) not in moved_ids


@pytest.mark.asyncio
async def test_09_newer_run2_confirmation_protected_against_run1_edit(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 9: Run 2 đã đóng Step & hủy sess1c -> sửa Run 1 về incomplete vẫn giữ step='completed' & sess1c='cancelled'."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(
                db, with_sess1b=True, with_sess1c=True, calendar_mode="synced"
            )

        # Run 1 trả lời completed, step_completed=False
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))
        r1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-09-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r1.status_code == 200

        # Run 2 chạy sess1b và xác nhận completed + step_completed=True -> tự hủy sess1c!
        _clock_at(monkeypatch, env["t_s1b_start"])
        r_start2 = await client.post(
            f"/api/v1/sessions/{env['sess1b_id']}/start",
            headers=_auth_headers(env["user_id"], "cmd-uf5-09-start2"),
            json={
                "expected_schedule_revision": 1,
                "device_id": "pixel_8",
                "requested_start_at": env["t_s1b_start"].isoformat(),
            },
        )
        assert r_start2.status_code == 201, r_start2.text
        run2_id = r_start2.json()["data"]["run_id"]

        _clock_at(monkeypatch, env["t_s1b_end"])
        r_end2 = await client.post(
            f"/api/v1/runs/{run2_id}/end",
            headers=_auth_headers(env["user_id"], "cmd-uf5-09-end2"),
            json={
                "device_id": "pixel_8",
                "expected_run_revision": 1,
                "event_id": "ev-uf5-09-end2",
                "ended_at": env["t_s1b_end"].isoformat(),
                "end_reason": "timer_elapsed",
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 0,
            },
        )
        assert r_end2.status_code == 200
        ci2_id = r_end2.json()["data"]["check_in"]["checkin_id"]

        _clock_at(monkeypatch, env["t_s1b_end"] + timedelta(seconds=10))
        r_ans2 = await client.put(
            f"/api/v1/check-ins/{ci2_id}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-09-ans2"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1b_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": False,
            },
        )
        assert r_ans2.status_code == 200

        async with sf() as db:
            sess1c = await db.get(SessionRow, env["sess1c_id"])
            assert sess1c is not None and sess1c.status == "cancelled"

        # Sửa Run 1 về incomplete -> không được ghi đè Run 2 và không được khôi phục sess1c!
        r_edit1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-09-edit1"),
            json={
                "expected_response_revision": 1,
                "responded_at": (env["t_s1b_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r_edit1.status_code == 200, r_edit1.text

        async with sf() as db:
            step1 = await db.get(StepRow, env["step1_id"])
            sess1c = await db.get(SessionRow, env["sess1c_id"])
            assert step1 is not None and step1.status == "completed"
            assert sess1c is not None and sess1c.status == "cancelled"


@pytest.mark.asyncio
async def test_10_be_uf5_02_explicit_false_vs_none_on_last_and_non_last_session(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 10 (BE-UF5-02): Còn sess2 -> None không tự đóng Step; chỉ còn sess1 -> False chặn, None giữ W3-06."""
    # Phần A: Còn sess1b scheduled -> step_completed=None KHÔNG tự đóng Step
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env_a = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="none")
        _clock_at(monkeypatch, env_a["t_s1_end"] + timedelta(seconds=10))
        r_a = await client.put(
            f"/api/v1/check-ins/{env_a['checkin1_id']}/response",
            headers=_auth_headers(env_a["user_id"], "cmd-uf5-10-a"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env_a["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
            },
        )
        assert r_a.status_code == 200
        async with sf() as db:
            step1 = await db.get(StepRow, env_a["step1_id"])
            sess1b = await db.get(SessionRow, env_a["sess1b_id"])
            assert step1 is not None and step1.status == "in_progress"
            assert sess1b is not None and sess1b.status == "scheduled"

    # Phần B: Chỉ còn sess1 là phiên cuối -> False chặn hoàn thành Step, None giữ auto-complete W3-06
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env_b = await _seed_uf5_plan(db, with_sess1b=False, calendar_mode="none")
        _clock_at(monkeypatch, env_b["t_s1_end"] + timedelta(seconds=10))

        r_false = await client.put(
            f"/api/v1/check-ins/{env_b['checkin1_id']}/response",
            headers=_auth_headers(env_b["user_id"], "cmd-uf5-10-false"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env_b["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r_false.status_code == 200
        async with sf() as db:
            step1 = await db.get(StepRow, env_b["step1_id"])
            assert step1 is not None and step1.status == "in_progress"

        r_none = await client.put(
            f"/api/v1/check-ins/{env_b['checkin1_id']}/response",
            headers=_auth_headers(env_b["user_id"], "cmd-uf5-10-none"),
            json={
                "expected_response_revision": 1,
                "responded_at": (env_b["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "completed",
            },
        )
        assert r_none.status_code == 200
        async with sf() as db:
            step1 = await db.get(StepRow, env_b["step1_id"])
            task = await db.get(TaskRow, env_b["task_id"])
            assert step1 is not None and step1.status == "completed" and step1.remaining_base_seconds == 0
            assert task is not None and task.status == "completed"


@pytest.mark.asyncio
async def test_11_no_respond_late_response_rev0_with_skip_step_cascade(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 11 (BE-UF5-03): Quá 300s thành no_respond (rev=0) -> trả lời bù skip_step rev=0 giữ dnd_release_at & hủy sess2."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        late_now = env["t_s1_end"] + timedelta(seconds=360)
        _clock_at(monkeypatch, late_now)

        async with sf() as db:
            await run_service.reconcile_expired_checkins(
                db, str(env["user_id"]), now=late_now, commit_if_read=True
            )
            ci = await db.get(CheckIn, env["checkin1_id"])
            assert ci is not None
            assert ci.response_state == "no_respond"
            assert ci.response_revision == 0

        r_late = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-11-late"),
            json={
                "expected_response_revision": 0,
                "responded_at": late_now.isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r_late.status_code == 200, r_late.text

        async with sf() as db:
            run_row = await db.get(SessionRun, env["run1_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            rem1b = await db.get(Reminder, env["reminders"][env["sess1b_id"]])
            del_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.operation == "delete",
                    )
                )
            ).all()
            assert run_row is not None
            assert run_row.run_state == "closed"
            assert run_row.actual_work_seconds == 1500
            assert run_row.dnd_release_at is not None
            assert run_row.dnd_release_at.astimezone(UTC) == (
                env["t_s1_end"] + timedelta(seconds=300)
            ).astimezone(UTC)
            assert sess1b is not None and sess1b.status == "cancelled"
            assert rem1b is not None and rem1b.delivery_state == "cancelled"
            assert len(del_jobs) == 1 and del_jobs[0].job_state == "queued"


@pytest.mark.asyncio
async def test_12_actual_corrections_and_checkin_make_zero_live_gcal_or_ai_calls(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 12 (BE-UF5-03): actual-corrections + skip_step + reconcile_expired_checkins -> 0 live GCal / AI calls."""
    async def _forbid(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Không được gọi Google Calendar live hoặc AI/STT trong luồng check-in")

    monkeypatch.setattr(FakeCalendarClient, "delete_event", _forbid)
    monkeypatch.setattr(FakeCalendarClient, "create_event", _forbid)
    monkeypatch.setattr(FakeCalendarClient, "update_event", _forbid)
    monkeypatch.setattr("app.services.ai_service.ai_service.generate_reschedule_empathy", _forbid)

    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=20))

        r_corr = await client.post(
            f"/api/v1/runs/{env['run1_id']}/actual-corrections",
            headers=_auth_headers(env["user_id"], "cmd-uf5-12-corr"),
            json={
                "expected_run_revision": 1,
                "self_report_work_seconds": 1350,
                "reason": "Sửa giờ làm thực tế",
                "occurred_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
            },
        )
        assert r_corr.status_code == 200, r_corr.text

        r_ans = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-12-ans"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r_ans.status_code == 200, r_ans.text

        async with sf() as db:
            await run_service.reconcile_expired_checkins(
                db,
                str(env["user_id"]),
                now=env["t_s1_end"] + timedelta(seconds=400),
                commit_if_read=True,
            )


@pytest.mark.asyncio
async def test_13_skip_step_then_completed_none_none_restores_step_and_surplus_session(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 13: skip_step (rev 1) -> completed(step_completed=None, task_completed=None) (rev 2) không tự đóng Step/Task vì sess2 bị tạm hủy bởi chính CheckIn này."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

        # rev 1: skip_step -> step1="skipped", sess1b="cancelled"
        r1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-13-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r1.status_code == 200
        async with sf() as db:
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            assert sess1b is not None and sess1b.status == "cancelled"

        # rev 2: completed với step_completed=None, task_completed=None
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=20))
        r2 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-13-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "completed",
            },
        )
        assert r2.status_code == 200, r2.text

        async with sf() as db:
            step1 = await db.get(StepRow, env["step1_id"])
            task = await db.get(TaskRow, env["task_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            assert step1 is not None and step1.status == "in_progress"
            assert step1.remaining_base_seconds == 1800
            assert task is not None and task.status != "completed"
            assert sess1b is not None and sess1b.status == "scheduled"


@pytest.mark.asyncio
async def test_14_undo_calendar_state_machine_leased_delete_and_attempted_create(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 14: State machine khôi phục lịch (a) nhánh iii leased delete, và (b) nhánh ii create attempted=1 + skip lại rev 3."""
    # Nhánh (a): delete job đang bị worker giữ lease -> đóng link cũ, tạo link mới + job create chuẩn payload_snapshot
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env_a = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        now_a = env_a["t_s1_end"] + timedelta(seconds=10)
        _clock_at(monkeypatch, now_a)

        r1 = await client.put(
            f"/api/v1/check-ins/{env_a['checkin1_id']}/response",
            headers=_auth_headers(env_a["user_id"], "cmd-uf5-14a-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": now_a.isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r1.status_code == 200

        async with sf() as db:
            del_job = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == env_a["links"][env_a["sess1b_id"]],
                    CalendarSyncJob.operation == "delete",
                )
            )
            assert del_job is not None
            del_job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            await db.commit()

        r2 = await client.put(
            f"/api/v1/check-ins/{env_a['checkin1_id']}/response",
            headers=_auth_headers(env_a["user_id"], "cmd-uf5-14a-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (now_a + timedelta(seconds=10)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r2.status_code == 200, r2.text

        async with sf() as db:
            old_link = await db.get(CalendarEventLink, env_a["links"][env_a["sess1b_id"]])
            current_links = (
                await db.scalars(
                    select(CalendarEventLink).where(
                        CalendarEventLink.session_id == env_a["sess1b_id"],
                        CalendarEventLink.is_current.is_(True),
                    )
                )
            ).all()
            assert old_link is not None and old_link.is_current is False
            assert len(current_links) == 1
            new_link = current_links[0]
            assert new_link.id != old_link.id
            assert new_link.google_event_id == f"session{env_a['sess1b_id'].hex}v2"

            create_job = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == new_link.id,
                    CalendarSyncJob.operation == "create",
                )
            )
            assert create_job is not None and create_job.job_state == "queued"
            snap = create_job.payload_snapshot
            assert snap["id"] == new_link.google_event_id
            assert isinstance(snap["summary"], str) and "[ProPlan]" in snap["summary"]
            assert "dateTime" in snap["start"] and snap["start"]["timeZone"] == "Asia/Ho_Chi_Minh"
            assert "dateTime" in snap["end"] and snap["end"]["timeZone"] == "Asia/Ho_Chi_Minh"
            assert snap["transparency"] == "opaque"
            assert snap["reminders"] == {"useDefault": False, "overrides": []}
            assert snap["extendedProperties"]["private"]["proactive_session_id"] == str(env_a["sess1b_id"])

    # Nhánh (b): create job từng thử (attempt_count=1) -> skip_step -> Undo xếp lại create -> skip_step rev 3 ok
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env_b = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="create_attempted")
        now_b = env_b["t_s1_end"] + timedelta(seconds=10)
        _clock_at(monkeypatch, now_b)

        # rev 1: skip_step -> đóng create(attempt_count=1) và xếp delete job
        rb1 = await client.put(
            f"/api/v1/check-ins/{env_b['checkin1_id']}/response",
            headers=_auth_headers(env_b["user_id"], "cmd-uf5-14b-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": now_b.isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert rb1.status_code == 200

        async with sf() as db:
            orig_create = await db.get(CalendarSyncJob, env_b["create_jobs"][env_b["sess1b_id"]])
            del_job = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == env_b["links"][env_b["sess1b_id"]],
                    CalendarSyncJob.operation == "delete",
                )
            )
            assert orig_create is not None and orig_create.job_state == "completed"
            assert del_job is not None and del_job.job_state == "queued"

        # rev 2: Undo -> đóng delete job unleased và xếp lại job create chuẩn payload_snapshot (nhánh ii)
        rb2 = await client.put(
            f"/api/v1/check-ins/{env_b['checkin1_id']}/response",
            headers=_auth_headers(env_b["user_id"], "cmd-uf5-14b-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (now_b + timedelta(seconds=10)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert rb2.status_code == 200, rb2.text

        async with sf() as db:
            del_job = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == env_b["links"][env_b["sess1b_id"]],
                    CalendarSyncJob.operation == "delete",
                )
            )
            restored_creates = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.event_link_id == env_b["links"][env_b["sess1b_id"]],
                        CalendarSyncJob.operation == "create",
                        CalendarSyncJob.job_state == "queued",
                    )
                )
            ).all()
            assert del_job is not None and del_job.job_state == "completed"
            assert len(restored_creates) == 1
            snap = restored_creates[0].payload_snapshot
            assert snap["id"] == f"session{env_b['sess1b_id'].hex}"
            assert snap["transparency"] == "opaque"
            assert snap["reminders"] == {"useDefault": False, "overrides": []}
            assert snap["extendedProperties"]["private"]["proactive_session_id"] == str(env_b["sess1b_id"])

        # rev 3: skip_step lần nữa không đụng UniqueConstraint -> đóng create mới (attempt_count=0) & đóng link
        rb3 = await client.put(
            f"/api/v1/check-ins/{env_b['checkin1_id']}/response",
            headers=_auth_headers(env_b["user_id"], "cmd-uf5-14b-r3"),
            json={
                "expected_response_revision": 2,
                "responded_at": (now_b + timedelta(seconds=20)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert rb3.status_code == 200, rb3.text
        async with sf() as db:
            link_b = await db.get(CalendarEventLink, env_b["links"][env_b["sess1b_id"]])
            queued_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.event_link_id == env_b["links"][env_b["sess1b_id"]],
                        CalendarSyncJob.job_state == "queued",
                    )
                )
            ).all()
            assert link_b is not None and link_b.is_current is False and link_b.sync_state == "deleted"
            assert queued_jobs == []


@pytest.mark.asyncio
async def test_15_uf6_cancel_after_checkin_restore_never_resurrected_and_allows_auto_complete(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 15 (P2-1): CheckIn1 rev1 (skip_step) -> rev2 (incomplete khôi phục sess1b) -> UF6 approve_proposal hủy sess1b (schedule_revision=2) -> rev3 (partial) & rev4 (completed step=None) không hồi sinh sess1b."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        _clock_at(monkeypatch, env["t_s1_end"] + timedelta(seconds=10))

        # rev 1: skip_step -> tự hủy sess1b (is_currently_cancelled=True, schedule_revision=1)
        r1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-15-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=10)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r1.status_code == 200, r1.text

        # rev 2: incomplete -> khôi phục sess1b (is_currently_cancelled=False)
        r2 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-15-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=20)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r2.status_code == 200, r2.text

        # UF6 approve_proposal hủy sess1b với schedule_revision=2
        prop_id = uuid4()
        async with sf() as db:
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            assert sess1b is not None and sess1b.status == "scheduled" and sess1b.schedule_revision == 1
            db.add(
                RescheduleProposal(
                    id=prop_id,
                    user_id=env["user_id"],
                    revision=1,
                    trigger_reason="user_reschedule",
                    target_task_id=env["task_id"],
                    allowed_task_ids=[str(env["task_id"])],
                    affected_task_ids=[str(env["task_id"])],
                    base_schedule_revision=1,
                    source_revisions={
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    proposal_revision=1,
                    status="pending",
                    proposed_changes={
                        "sessions_to_move": [],
                        "sessions_to_add": [],
                        "sessions_to_cancel": [
                            {
                                "session_id": str(env["sess1b_id"]),
                                "task_id": str(env["task_id"]),
                                "step_id": str(env["step1_id"]),
                                "reason": "Hủy phiên thừa theo đề xuất UF6",
                            }
                        ],
                        "steps_to_add": [],
                    },
                    feasibility={"status": "feasible", "confirmable": True, "blocking_reasons": []},
                    explanation="Giữ lịch gọn nhẹ cho bạn.",
                    generation_tier="python_fallback",
                )
            )
            await db.commit()

        r_app = await client.post(
            f"/api/v1/proposals/{prop_id}/approve",
            headers=_auth_headers(env["user_id"], "cmd-uf5-15-approve"),
            json={
                "expected_proposal_revision": 1,
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": {
                    "task": 1,
                    "schedule": 1,
                    "availability": 1,
                    "settings": 1,
                    "calendar": 1,
                },
                "start_continuation": False,
            },
        )
        assert r_app.status_code == 200, r_app.text

        async with sf() as db:
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            assert sess1b is not None and sess1b.status == "cancelled" and sess1b.schedule_revision == 2

        # rev 3: partial -> TUYỆT ĐỐI không được hồi sinh sess1b do UF6 đã hủy ở schedule_revision=2!
        r3 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-15-r3"),
            json={
                "expected_response_revision": 2,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=30)).isoformat(),
                "session_outcome": "partial",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r3.status_code == 200, r3.text

        async with sf() as db:
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            assert sess1b is not None and sess1b.status == "cancelled" and sess1b.schedule_revision == 2

        # rev 4: completed với step_completed=None -> _has_self_cancelled_step_sessions bỏ qua sess1b
        # (vì is_currently_cancelled=False và schedule_revision=2 != 1), Step 1 tự hoàn thành bình thường!
        r4 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-15-r4"),
            json={
                "expected_response_revision": 3,
                "responded_at": (env["t_s1_end"] + timedelta(seconds=40)).isoformat(),
                "session_outcome": "completed",
            },
        )
        assert r4.status_code == 200, r4.text

        async with sf() as db:
            step1 = await db.get(StepRow, env["step1_id"])
            sess1b = await db.get(SessionRow, env["sess1b_id"])
            assert step1 is not None and step1.status == "completed"
            assert sess1b is not None and sess1b.status == "cancelled" and sess1b.schedule_revision == 2


@pytest.mark.asyncio
async def test_16_restore_branch_iii_existing_target_current_link_avoids_integrity_error(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 16 (P2-2): Nhánh (iii) khi đã tồn tại other_current_link trên cùng (sess, conn, write_calendar_id) không ném IntegrityError."""
    async with _uf5_test_app(sqlite_migration) as (sf, client):
        async with sf() as db:
            env = await _seed_uf5_plan(db, with_sess1b=True, calendar_mode="synced")
        now_t = env["t_s1_end"] + timedelta(seconds=10)
        _clock_at(monkeypatch, now_t)

        # rev 1: skip_step xếp job delete cho link1
        r1 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-16-r1"),
            json={
                "expected_response_revision": 0,
                "responded_at": now_t.isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r1.status_code == 200, r1.text

        other_link_id = uuid4()
        async with sf() as db:
            link1 = await db.get(CalendarEventLink, env["links"][env["sess1b_id"]])
            del_job = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == env["links"][env["sess1b_id"]],
                    CalendarSyncJob.operation == "delete",
                )
            )
            assert link1 is not None and del_job is not None
            # Giả lập link1 đã đóng/xóa và đã có sẵn other_current_link khác trên cùng (sess1b, conn, write_calendar_id)
            link1.is_current = False
            link1.sync_state = "deleted"
            del_job.job_state = "completed"
            await db.flush()

            db.add(
                CalendarEventLink(
                    id=other_link_id,
                    user_id=env["user_id"],
                    session_id=env["sess1b_id"],
                    connection_id=env["conn_id"],
                    google_calendar_id="proplan_secondary",
                    google_event_id=f"session{env['sess1b_id'].hex}v99",
                    google_etag="etag_other_99",
                    desired_schedule_revision=1,
                    synced_schedule_revision=1,
                    sync_state="synced",
                    is_current=True,
                )
            )
            await db.commit()

        # rev 2: Undo -> nhánh (iii) gặp existing_target_current != None -> giữ nguyên link có sẵn, 0 IntegrityError
        r2 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-16-r2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (now_t + timedelta(seconds=10)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r2.status_code == 200, r2.text

        async with sf() as db:
            current_links = (
                await db.scalars(
                    select(CalendarEventLink).where(
                        CalendarEventLink.session_id == env["sess1b_id"],
                        CalendarEventLink.is_current.is_(True),
                    )
                )
            ).all()
            create_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == env["user_id"],
                        CalendarSyncJob.target_ref == str(env["sess1b_id"]),
                        CalendarSyncJob.operation == "create",
                    )
                )
            ).all()
            receipt = await db.scalar(
                select(CommandReceipt).where(
                    CommandReceipt.user_id == env["user_id"],
                    CommandReceipt.command_id == "cmd-uf5-16-r2",
                )
            )
            assert len(current_links) == 1
            assert current_links[0].id == other_link_id
            assert create_jobs == []
            assert receipt is not None
            proj = (receipt.result_reference or {})["_projection"]
            cal_snap = proj["baseline"]["auto_cancelled_sessions"][str(env["sess1b_id"])]["calendar_snapshot"]
            assert cal_snap["link_id"] == str(other_link_id)
            assert cal_snap["enqueued_delete_job_id"] is None

        # rev 3: Hủy lại bằng skip_step vẫn thành công và xếp delete trên other_link_id
        r3 = await client.put(
            f"/api/v1/check-ins/{env['checkin1_id']}/response",
            headers=_auth_headers(env["user_id"], "cmd-uf5-16-r3"),
            json={
                "expected_response_revision": 2,
                "responded_at": (now_t + timedelta(seconds=20)).isoformat(),
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r3.status_code == 200, r3.text
        async with sf() as db:
            del_on_other = await db.scalar(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.event_link_id == other_link_id,
                    CalendarSyncJob.operation == "delete",
                    CalendarSyncJob.job_state == "queued",
                )
            )
            assert del_on_other is not None
