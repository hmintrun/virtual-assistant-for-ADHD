"""Bộ kiểm thử hồi quy cho gói nâng cấp BE-UF3 (2026-11-25).

Bao phủ đủ 6 hạng mục Phase 1 BE-UF3:
- UF3-F01 + UF6-01: GET /schedule lazy-reconcile start_state='not_started' & approve_proposal reset start_state='waiting'
- UF3-F02 (D1-A): Non-blocking no_respond cho bước trước + negative guards (scheduled / active run / open check-in)
- UF3-F03: Server-authoritative planned_ends_at (running, pause+resume, open pause, ended while paused, continuation run)
- UF3-F05: Bảo toàn title / session_goal / expected_output trên SessionSnapshot (start, active, fallback, continuation run)
- UF3-F12 (D2-Safe): Latest-only PlanningContext (break) + lọc lịch phụ write_calendar_id != 'primary' (giữ nguyên primary)
- UF3-F13: end_reason='cancelled' đặt actual_work_seconds=None & actual_source=None (user_stopped vẫn giữ timer_proxy)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models import (
    Availability,
    CalendarConnection,
    CheckIn,
    PlanningContext,
    PlanningDraft,
    RescheduleProposal,
    RunEvent,
    ScheduleVersion,
    Session,
    SessionRun,
    Step,
    Task,
    User,
    UserPreference,
)
from app.schemas.execution import (
    RunEndRequest,
    RunEventBatchRequest,
    RunEventItem,
    SessionStartRequest,
)
from app.schemas.reschedule import (
    ApproveProposalRequest,
    ProposalSourceRevisions,
)
from app.schemas.user import CurrentUser
from app.services import reschedule_service, run_service


@pytest.fixture
async def uf3_env(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sessions = session_factory(engine)

    user_id = uuid4()
    async with sessions() as db:
        user = User(id=user_id, email="uf3@example.test", display_name="UF3 User")
        db.add(user)
        await db.flush()
        pref = UserPreference(user_id=user.id)
        avail = Availability(
            user_id=user.id,
            timezone="UTC",
            weekly_rules=[
                {"weekday": d, "start_local": "00:00", "end_local": "23:59"}
                for d in range(1, 8)
            ],
            date_overrides=[],
            task_limits={},
            source="manual",
            confirmed_at=datetime.now(UTC) - timedelta(days=1),
        )
        ver = ScheduleVersion(
            user_id=user.id,
            version_number=1,
            base_version=None,
            approved_at=datetime.now(UTC) - timedelta(days=1),
            affected_task_ids=[],
            min_inter_session_gap_seconds=900,
        )
        db.add_all([pref, avail, ver])
        await db.commit()
        avail_id = avail.id

    try:
        yield {
            "user_id": user_id,
            "availability_id": avail_id,
            "sessions": sessions,
            "engine": engine,
        }
    finally:
        await engine.dispose()


@pytest.fixture
async def uf3_client(uf3_env):
    sessions = uf3_env["sessions"]
    user_id = str(uf3_env["user_id"])

    async def override_db():
        async with sessions() as db:
            yield db

    async def override_user():
        return CurrentUser(user_id=user_id, email="uf3@example.test", display_name="UF3 User")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _make_session_row(
    *,
    user_id: UUID,
    step_id: UUID,
    start_at: datetime,
    work_seconds: int = 1500,
    rest_seconds: int = 300,
    status: str = "scheduled",
    start_state: str = "waiting",
    start_decision_due_at: datetime | None = None,
    title: str | None = None,
    session_goal: str | None = None,
    expected_output: str | None = None,
) -> Session:
    duration = work_seconds + rest_seconds
    segments = [{"order_index": 0, "kind": "work", "duration_seconds": work_seconds}]
    if rest_seconds > 0:
        segments.append({"order_index": 1, "kind": "rest", "duration_seconds": rest_seconds})
    return Session(
        id=uuid4(),
        user_id=user_id,
        step_id=step_id,
        scheduled_start=start_at,
        scheduled_end=start_at + timedelta(seconds=duration),
        title=title,
        session_goal=session_goal,
        expected_output=expected_output,
        micro_task_text="Mở tài liệu và đọc 2 phút đầu",
        micro_task_seconds=120,
        work_seconds=work_seconds,
        rest_seconds=rest_seconds,
        duration_seconds=duration,
        segments=segments,
        schedule_revision=1,
        status=status,
        start_state=start_state,
        start_decision_due_at=start_decision_due_at
        or (start_at + timedelta(seconds=run_service.START_GRACE_SECONDS)),
    )


# ==============================================================================
# 1. UF3-F01 + UF6-01: GET /schedule lazy reconcile & approve_proposal reset
# ==============================================================================


@pytest.mark.asyncio
async def test_uf3_f01_get_schedule_reconciles_overdue_waiting_session(uf3_env, uf3_client):
    """UF3-F01: GET /schedule tự chuyển phiên quá start_decision_due_at sang not_started và persist DB."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)
    overdue_start = now - timedelta(minutes=30)
    future_start = now + timedelta(hours=2)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F01", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=3000,
            remaining_base_seconds=3000,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        overdue_sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=overdue_start,
            start_state="waiting",
            start_decision_due_at=overdue_start + timedelta(minutes=15),
        )
        future_sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=future_start,
            start_state="waiting",
            start_decision_due_at=future_start + timedelta(minutes=15),
        )
        db.add_all([overdue_sess, future_sess])
        await db.commit()
        overdue_id, future_id = overdue_sess.id, future_sess.id

    resp = await uf3_client.get(
        "/api/v1/schedule",
        params={
            "from": (now - timedelta(hours=2)).isoformat(),
            "to": (now + timedelta(hours=5)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    items_by_id = {item["session_id"]: item for item in resp.json()["data"]["items"]}
    assert items_by_id[str(overdue_id)]["start_state"] == "not_started"
    assert items_by_id[str(future_id)]["start_state"] == "waiting"

    async with uf3_env["sessions"]() as db:
        refreshed_overdue = await db.get(Session, overdue_id)
        refreshed_future = await db.get(Session, future_id)
        assert refreshed_overdue is not None and refreshed_overdue.start_state == "not_started"
        assert refreshed_future is not None and refreshed_future.start_state == "waiting"


@pytest.mark.asyncio
async def test_uf6_01_approve_proposal_resets_not_started_to_waiting(uf3_env, uf3_client):
    """UF3-F01 + UF6-01: approve_proposal dời phiên not_started -> reset start_state='waiting' và due_at mới."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)
    old_start = now - timedelta(hours=1)
    new_start = now + timedelta(hours=3)
    new_end = new_start + timedelta(seconds=1800)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task UF6-01", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=old_start,
            start_state="not_started",
            start_decision_due_at=old_start + timedelta(minutes=15),
        )
        db.add(sess)
        await db.flush()

        source_revs = {
            "task": task.revision,
            "schedule": 1,
            "availability": 1,
            "settings": 1,
            "calendar": 0,
        }
        proposal = RescheduleProposal(
            id=uuid4(),
            user_id=user_id,
            trigger_reason="missed_start",
            target_task_id=task.id,
            allowed_task_ids=[str(task.id)],
            affected_task_ids=[str(task.id)],
            base_schedule_revision=1,
            source_revisions=source_revs,
            proposal_revision=1,
            proposed_changes={
                "sessions_to_move": [
                    {
                        "session_id": str(sess.id),
                        "task_id": str(task.id),
                        "step_id": str(step.id),
                        "from_start": old_start.isoformat(),
                        "from_end": (old_start + timedelta(seconds=1800)).isoformat(),
                        "to_start": new_start.isoformat(),
                        "to_end": new_end.isoformat(),
                        "reason": "reschedule_missed",
                    }
                ],
                "sessions_to_add": [],
                "sessions_to_cancel": [],
                "steps_to_add": [],
            },
            feasibility={"status": "feasible", "confirmable": True, "blocking_reasons": []},
            explanation="Đã dời phiên sang khung giờ rảnh kế tiếp.",
            status="pending",
        )
        db.add(proposal)
        await db.commit()
        sess_id, prop_id = sess.id, proposal.id

        res = await reschedule_service.approve_proposal(
            db,
            user_id=str(user_id),
            proposal_id=str(prop_id),
            idempotency_key="idem-uf6-01-reset",
            payload=ApproveProposalRequest(
                expected_proposal_revision=1,
                expected_base_schedule_revision=1,
                expected_source_revisions=ProposalSourceRevisions(**source_revs),
            ),
            now=now,
        )
        assert res.status == "approved"

    async with uf3_env["sessions"]() as db:
        moved = await db.get(Session, sess_id)
        assert moved is not None
        assert moved.start_state == "waiting"
        assert moved.start_decision_due_at == new_start + timedelta(
            seconds=reschedule_service.START_GRACE_SECONDS
        )

    resp = await uf3_client.get(
        "/api/v1/schedule",
        params={
            "from": (now - timedelta(hours=2)).isoformat(),
            "to": (now + timedelta(hours=6)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    items_by_id = {item["session_id"]: item for item in resp.json()["data"]["items"]}
    assert items_by_id[str(sess_id)]["start_state"] == "waiting"


# ==============================================================================
# 2. UF3-F02 (D1-A): Non-blocking no_respond + negative guards
# ==============================================================================


@pytest.mark.asyncio
async def test_uf3_f02_d1a_no_respond_does_not_block_next_step(uf3_env, uf3_client):
    """UF3-F02 (D1-A): Bước 1 đã kết thúc và quá hạn check-in (no_respond) -> Bước 2 start trả 201."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)
    s1_start = now - timedelta(hours=2)
    s1_end = s1_start + timedelta(seconds=1800)
    s2_start = now

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task D1A", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step1 = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        step2 = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 2",
            order_index=1,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add_all([step1, step2])
        await db.flush()

        s1 = _make_session_row(
            user_id=user_id,
            step_id=step1.id,
            start_at=s1_start,
            status="in_progress",
            start_state="started",
        )
        s2 = _make_session_row(
            user_id=user_id,
            step_id=step2.id,
            start_at=s2_start,
            status="scheduled",
            start_state="waiting",
        )
        db.add_all([s1, s2])
        await db.flush()

        run1 = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=s1.id,
            device_id="dev-1",
            started_at=s1_start,
            ended_at=s1_end,
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=300,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="unknown",
            checkin_timeout_at=s1_end + timedelta(seconds=300),
            dnd_release_at=s1_end + timedelta(seconds=300),
            settings_snapshot={},
        )
        db.add(run1)
        await db.flush()
        # Check-in vẫn 'open' nhưng checkin_timeout_at đã quá hạn -> reconcile_expired_checkins chuyển sang no_respond
        ci1 = CheckIn(
            user_id=user_id,
            run_id=run1.id,
            kind="final",
            response_state="open",
            session_outcome="unknown",
            asked_at=s1_end,
            response_revision=0,
        )
        db.add(ci1)
        await db.commit()
        s2_id = s2.id

    resp = await uf3_client.post(
        f"/api/v1/sessions/{s2_id}/start",
        headers={"Idempotency-Key": "idem-f02-allow"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": now.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["session_id"] == str(s2_id)


@pytest.mark.asyncio
async def test_uf3_f02_negative_guards_still_block_next_step(uf3_env, uf3_client):
    """UF3-F02 (Negative): Bước 1 còn phiên scheduled hoặc có CheckIn open chưa hết hạn -> vẫn chặn 409."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)
    s1_start = now - timedelta(hours=2)
    s2_start = now

    # Case A: Bước 1 còn phiên scheduled chưa chạy
    async with uf3_env["sessions"]() as db:
        task_a = Task(user_id=user_id, title="Task A", time_constraint={"kind": "window"})
        db.add(task_a)
        await db.flush()
        st1 = Step(
            user_id=user_id,
            task_id=task_a.id,
            title="A Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        st2 = Step(
            user_id=user_id,
            task_id=task_a.id,
            title="A Step 2",
            order_index=1,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add_all([st1, st2])
        await db.flush()
        s1_a = _make_session_row(user_id=user_id, step_id=st1.id, start_at=s1_start)
        s2_a = _make_session_row(user_id=user_id, step_id=st2.id, start_at=s2_start)
        db.add_all([s1_a, s2_a])
        await db.commit()
        s2_a_id = s2_a.id

    resp_a = await uf3_client.post(
        f"/api/v1/sessions/{s2_a_id}/start",
        headers={"Idempotency-Key": "idem-f02-block-scheduled"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": now.isoformat(),
        },
    )
    assert resp_a.status_code == 409
    assert resp_a.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # Case B: Bước 1 có run kết thúc nhưng CheckIn còn 'open' (chưa tới hạn 300s) -> vẫn chặn 409
    async with uf3_env["sessions"]() as db:
        task_b = Task(user_id=user_id, title="Task B", time_constraint={"kind": "window"})
        db.add(task_b)
        await db.flush()
        b_st1 = Step(
            user_id=user_id,
            task_id=task_b.id,
            title="B Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        b_st2 = Step(
            user_id=user_id,
            task_id=task_b.id,
            title="B Step 2",
            order_index=1,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add_all([b_st1, b_st2])
        await db.flush()
        b_s1 = _make_session_row(
            user_id=user_id,
            step_id=b_st1.id,
            start_at=now - timedelta(hours=3),
            status="in_progress",
            start_state="started",
        )
        b_s2 = _make_session_row(
            user_id=user_id,
            step_id=b_st2.id,
            start_at=now + timedelta(hours=2),
        )
        db.add_all([b_s1, b_s2])
        await db.flush()
        b_run1 = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=b_s1.id,
            device_id="dev-1",
            started_at=now - timedelta(hours=3),
            ended_at=now + timedelta(hours=1, minutes=58),
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=300,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="unknown",
            checkin_timeout_at=now + timedelta(hours=2, minutes=3),
            dnd_release_at=now + timedelta(hours=2, minutes=3),
            settings_snapshot={},
        )
        db.add(b_run1)
        await db.flush()
        db.add(
            CheckIn(
                user_id=user_id,
                run_id=b_run1.id,
                kind="final",
                response_state="open",
                session_outcome="unknown",
                asked_at=now + timedelta(hours=1, minutes=58),
                response_revision=0,
            )
        )
        await db.commit()
        b_s2_id, b_s2_start = b_s2.id, b_s2.scheduled_start

    async with uf3_env["sessions"]() as db:
        with pytest.raises(Exception) as exc_info:
            await run_service.start_session_run(
                db,
                user_id=str(user_id),
                session_id=str(b_s2_id),
                idempotency_key="idem-f02-block-open",
                payload=SessionStartRequest(
                    expected_schedule_revision=1,
                    device_id="dev-1",
                    requested_start_at=b_s2_start,
                ),
                now=b_s2_start,
            )
        assert getattr(exc_info.value, "status_code", None) == 409
        assert getattr(exc_info.value, "code", None) == "START_REQUIRES_REPLAN"


@pytest.mark.asyncio
async def test_uf3_f02_negative_guard_active_run_blocks_even_if_prior_no_respond(uf3_env):
    """UF3-F02 (Negative): Bước 1 có s1(in_progress) vừa có run no_respond vừa có run ended_at IS NULL -> vẫn bị chặn 409."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime(2026, 11, 25, 14, 0, 0, tzinfo=UTC)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task Active Guard", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        st1 = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        st2 = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 2",
            order_index=1,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add_all([st1, st2])
        await db.flush()
        s1 = _make_session_row(
            user_id=user_id,
            step_id=st1.id,
            start_at=now - timedelta(hours=2),
            status="in_progress",
            start_state="started",
        )
        s2 = _make_session_row(
            user_id=user_id,
            step_id=st2.id,
            start_at=now,
            status="scheduled",
            start_state="waiting",
        )
        db.add_all([s1, s2])
        await db.flush()

        # Run 1 đã kết thúc với final CheckIn no_respond -> đưa s1.id vào ended_no_respond_session_ids
        prior_no_respond_run = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=s1.id,
            device_id="dev-1",
            started_at=now - timedelta(hours=2),
            ended_at=now - timedelta(hours=1, minutes=30),
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=300,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="unknown",
            checkin_timeout_at=now - timedelta(hours=1, minutes=25),
            dnd_release_at=now - timedelta(hours=1, minutes=25),
            settings_snapshot={},
        )
        db.add(prior_no_respond_run)
        await db.flush()
        db.add(
            CheckIn(
                user_id=user_id,
                run_id=prior_no_respond_run.id,
                kind="final",
                response_state="no_respond",
                session_outcome="unknown",
                asked_at=now - timedelta(hours=1, minutes=30),
                response_revision=0,
            )
        )
        # Run 2 vẫn đang mở (ended_at IS NULL) -> đưa s1.id vào active_run_session_ids
        # Đặt run_state='awaiting_checkin' để vượt check live_run toàn cục và đi thẳng vào _validate_start_feasibility
        db.add(
            SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=s1.id,
                device_id="dev-1",
                started_at=now - timedelta(minutes=20),
                ended_at=None,
                run_state="awaiting_checkin",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=0,
                rest_elapsed_seconds=0,
                outcome="unknown",
                settings_snapshot={},
            )
        )
        await db.commit()

        with pytest.raises(Exception) as exc_info:
            await run_service.start_session_run(
                db,
                user_id=str(user_id),
                session_id=str(s2.id),
                idempotency_key="idem-f02-block-unended-run",
                payload=SessionStartRequest(
                    expected_schedule_revision=1,
                    device_id="dev-1",
                    requested_start_at=now,
                ),
                now=now,
            )
        assert getattr(exc_info.value, "status_code", None) == 409
        assert getattr(exc_info.value, "code", None) == "START_REQUIRES_REPLAN"


@pytest.mark.asyncio
async def test_uf3_f02_step1_mixed_no_respond_and_scheduled_still_blocks_step2(
    uf3_env, uf3_client
):
    """UF3-F02 (Edge): Bước 1 có 2 phiên (s1a đã no_respond, s1b vẫn scheduled) -> Bước 2 vẫn bị chặn 409."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)
    s1a_start = now - timedelta(hours=3)
    s1a_end = s1a_start + timedelta(seconds=1800)
    s1b_start = now - timedelta(hours=1, minutes=30)
    s2_start = now

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F02 Mixed", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step1 = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=3000,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        step2 = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 2",
            order_index=1,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add_all([step1, step2])
        await db.flush()
        s1a = _make_session_row(
            user_id=user_id,
            step_id=step1.id,
            start_at=s1a_start,
            status="in_progress",
            start_state="started",
        )
        s1b = _make_session_row(
            user_id=user_id,
            step_id=step1.id,
            start_at=s1b_start,
            status="scheduled",
            start_state="waiting",
        )
        s2 = _make_session_row(
            user_id=user_id,
            step_id=step2.id,
            start_at=s2_start,
            status="scheduled",
            start_state="waiting",
        )
        db.add_all([s1a, s1b, s2])
        await db.flush()
        run1a = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=s1a.id,
            device_id="dev-1",
            started_at=s1a_start,
            ended_at=s1a_end,
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=300,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="unknown",
            checkin_timeout_at=s1a_end + timedelta(seconds=300),
            dnd_release_at=s1a_end + timedelta(seconds=300),
            settings_snapshot={},
        )
        db.add(run1a)
        await db.flush()
        db.add(
            CheckIn(
                user_id=user_id,
                run_id=run1a.id,
                kind="final",
                response_state="no_respond",
                session_outcome="unknown",
                asked_at=s1a_end,
                response_revision=0,
            )
        )
        await db.commit()
        s2_id = s2.id

    resp = await uf3_client.post(
        f"/api/v1/sessions/{s2_id}/start",
        headers={"Idempotency-Key": "idem-f02-mixed-block"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": now.isoformat(),
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "START_REQUIRES_REPLAN"


# ==============================================================================
# 3. UF3-F03: Server-Authoritative planned_ends_at (running, pause+resume, open pause, ended)
# ==============================================================================


@pytest.mark.asyncio
async def test_uf3_f03_planned_ends_at_running_and_active_detail(uf3_env, uf3_client):
    """UF3-F03 (Running): POST /start, GET /runs/active và GET /runs/{id} trả planned_ends_at chuẩn."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F03 Run", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(user_id=user_id, step_id=step.id, start_at=now)
        db.add(sess)
        await db.commit()
        sess_id = sess.id

    expected_end = now + timedelta(seconds=1800)

    start_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f03-start"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f03",
            "requested_start_at": now.isoformat(),
        },
    )
    assert start_resp.status_code == 201, start_resp.text
    start_data = start_resp.json()["data"]
    run_id = start_data["run_id"]
    assert datetime.fromisoformat(start_data["planned_ends_at"]) == expected_end

    active_resp = await uf3_client.get("/api/v1/runs/active")
    assert active_resp.status_code == 200
    assert datetime.fromisoformat(active_resp.json()["data"]["planned_ends_at"]) == expected_end

    detail_resp = await uf3_client.get(f"/api/v1/runs/{run_id}")
    assert detail_resp.status_code == 200
    assert datetime.fromisoformat(detail_resp.json()["data"]["planned_ends_at"]) == expected_end


@pytest.mark.asyncio
async def test_uf3_f03_planned_ends_at_pause_resume_and_open_pause(uf3_env):
    """UF3-F03: Cộng chính xác thời lượng pause->resume, open pause khi đang paused và khi end lúc paused."""
    user_id: UUID = uf3_env["user_id"]
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=UTC)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F03 Pause", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(user_id=user_id, step_id=step.id, start_at=t0)
        db.add(sess)
        await db.commit()

        started = await run_service.start_session_run(
            db,
            user_id=str(user_id),
            session_id=str(sess.id),
            idempotency_key="idem-f03-p-start",
            payload=SessionStartRequest(
                expected_schedule_revision=1,
                device_id="dev-p",
                requested_start_at=t0,
            ),
            now=t0,
        )
        assert started.planned_ends_at == t0 + timedelta(seconds=1800)

        # Cặp pause (+60s) -> resume (+180s) => tổng pause = 120s
        ev1 = await run_service.record_run_events(
            db,
            user_id=str(user_id),
            run_id=started.run_id,
            idempotency_key="idem-f03-p-ev1",
            payload=RunEventBatchRequest(
                device_id="dev-p",
                expected_run_revision=started.run_revision,
                events=[
                    RunEventItem(
                        event_id="ev-pause-1",
                        event_type="pause",
                        occurred_at=t0 + timedelta(seconds=60),
                        segment_index=0,
                        work_elapsed_seconds=60,
                        rest_elapsed_seconds=0,
                    ),
                    RunEventItem(
                        event_id="ev-resume-1",
                        event_type="resume",
                        occurred_at=t0 + timedelta(seconds=180),
                        segment_index=0,
                        work_elapsed_seconds=60,
                        rest_elapsed_seconds=0,
                    ),
                ],
            ),
            now=t0 + timedelta(seconds=180),
        )

        active_after_resume = await run_service.get_active_run(
            db, user_id=str(user_id), now=t0 + timedelta(seconds=200)
        )
        assert active_after_resume is not None
        assert active_after_resume.planned_ends_at == t0 + timedelta(seconds=1800 + 120)

        # Mở pause lần 2 tại t0 + 240s, chưa resume
        ev2 = await run_service.record_run_events(
            db,
            user_id=str(user_id),
            run_id=started.run_id,
            idempotency_key="idem-f03-p-ev2",
            payload=RunEventBatchRequest(
                device_id="dev-p",
                expected_run_revision=ev1.run_revision,
                events=[
                    RunEventItem(
                        event_id="ev-pause-2",
                        event_type="pause",
                        occurred_at=t0 + timedelta(seconds=240),
                        segment_index=0,
                        work_elapsed_seconds=120,
                        rest_elapsed_seconds=0,
                    ),
                ],
            ),
            now=t0 + timedelta(seconds=240),
        )

        # Kiểm tra tại t0 + 300s (đang pause mở thêm 60s => tổng pause = 180s)
        active_while_paused = await run_service.get_active_run(
            db, user_id=str(user_id), now=t0 + timedelta(seconds=300)
        )
        assert active_while_paused is not None
        assert active_while_paused.run_state == "paused"
        assert active_while_paused.planned_ends_at == t0 + timedelta(seconds=1800 + 180)

        # Kết thúc run trong khi vẫn đang paused tại t0 + 340s (open pause = 100s => tổng pause = 220s)
        ended = await run_service.end_session_run(
            db,
            user_id=str(user_id),
            run_id=started.run_id,
            idempotency_key="idem-f03-p-end",
            payload=RunEndRequest(
                device_id="dev-p",
                expected_run_revision=ev2.run_revision,
                event_id="ev-end-paused",
                ended_at=t0 + timedelta(seconds=340),
                end_reason="user_stopped",
                work_elapsed_seconds=120,
                rest_elapsed_seconds=0,
            ),
            now=t0 + timedelta(seconds=340),
        )
        assert ended.planned_ends_at == t0 + timedelta(seconds=1800 + 220)


@pytest.mark.asyncio
async def test_uf3_f03_same_timestamp_pause_resume_duplicate_pause_and_orphan_resume(uf3_env):
    """UF3-F03 (Edge): pause+resume cùng occurred_at (bất kể UUID), pause->pause->resume, và resume lẻ."""
    user_id: UUID = uf3_env["user_id"]
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=UTC)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F03 Edge", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(user_id=user_id, step_id=step.id, start_at=t0)
        db.add(sess)
        await db.commit()

        started = await run_service.start_session_run(
            db,
            user_id=str(user_id),
            session_id=str(sess.id),
            idempotency_key="idem-f03-edge-start",
            payload=SessionStartRequest(
                expected_schedule_revision=1,
                device_id="dev-edge",
                requested_start_at=t0,
            ),
            now=t0,
        )
        run_uuid = UUID(started.run_id)

        # 1) Cố tình chèn resume có UUID NHỎ HƠN pause ở CÙNG mốc t0 + 60s
        #    (nếu phụ thuộc RunEvent.id sẽ để sót open_pause_at và cộng nhầm toàn bộ thời gian sau đó!)
        small_uuid = UUID("00000000-0000-4000-8000-000000000001")
        large_uuid = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
        db.add_all(
            [
                RunEvent(
                    id=small_uuid,
                    user_id=user_id,
                    run_id=run_uuid,
                    client_event_id="ev-same-ts-resume",
                    event_type="resume",
                    occurred_at=t0 + timedelta(seconds=60),
                    payload={},
                ),
                RunEvent(
                    id=large_uuid,
                    user_id=user_id,
                    run_id=run_uuid,
                    client_event_id="ev-same-ts-pause",
                    event_type="pause",
                    occurred_at=t0 + timedelta(seconds=60),
                    payload={},
                ),
            ]
        )
        await db.commit()

        active_same_ts = await run_service.get_active_run(
            db, user_id=str(user_id), now=t0 + timedelta(seconds=100)
        )
        assert active_same_ts is not None
        assert active_same_ts.planned_ends_at == t0 + timedelta(seconds=1800)

        # 2) pause(120s) -> pause(150s) -> resume(240s) => chỉ cộng 120s (từ 120s đến 240s)
        # 3) resume lẻ(300s) không có pause trước đó => cộng 0s
        db.add_all(
            [
                RunEvent(
                    id=uuid4(),
                    user_id=user_id,
                    run_id=run_uuid,
                    client_event_id="ev-dup-pause-1",
                    event_type="pause",
                    occurred_at=t0 + timedelta(seconds=120),
                    payload={},
                ),
                RunEvent(
                    id=uuid4(),
                    user_id=user_id,
                    run_id=run_uuid,
                    client_event_id="ev-dup-pause-2",
                    event_type="pause",
                    occurred_at=t0 + timedelta(seconds=150),
                    payload={},
                ),
                RunEvent(
                    id=uuid4(),
                    user_id=user_id,
                    run_id=run_uuid,
                    client_event_id="ev-dup-resume",
                    event_type="resume",
                    occurred_at=t0 + timedelta(seconds=240),
                    payload={},
                ),
                RunEvent(
                    id=uuid4(),
                    user_id=user_id,
                    run_id=run_uuid,
                    client_event_id="ev-orphan-resume",
                    event_type="resume",
                    occurred_at=t0 + timedelta(seconds=300),
                    payload={},
                ),
            ]
        )
        await db.commit()

        active_after_edges = await run_service.get_active_run(
            db, user_id=str(user_id), now=t0 + timedelta(seconds=360)
        )
        assert active_after_edges is not None
        assert active_after_edges.planned_ends_at == t0 + timedelta(seconds=1800 + 120)


# ==============================================================================
# 4. UF3-F05: SessionSnapshot bảo toàn title / session_goal / expected_output (+ continuation run)
# ==============================================================================


@pytest.mark.asyncio
async def test_uf3_f05_session_metadata_preserved_on_start_active_and_fallback(
    uf3_env, uf3_client
):
    """UF3-F05: SessionSnapshot giữ đủ title, session_goal, expected_output ở start, active và fallback."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F05", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step F05",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=now,
            title="Viết dàn ý Chương 2",
            session_goal="Hoàn thành 3 mục lớn của phần Cơ sở lý thuyết",
            expected_output="File outline.md tối thiểu 12 gạch đầu dòng",
        )
        db.add(sess)
        await db.commit()
        sess_id = sess.id

    start_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f05-start"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f05",
            "requested_start_at": now.isoformat(),
        },
    )
    assert start_resp.status_code == 201, start_resp.text
    snap_start = start_resp.json()["data"]["settings_snapshot"]["session"]
    assert snap_start["title"] == "Viết dàn ý Chương 2"
    assert snap_start["session_goal"] == "Hoàn thành 3 mục lớn của phần Cơ sở lý thuyết"
    assert snap_start["expected_output"] == "File outline.md tối thiểu 12 gạch đầu dòng"

    active_resp = await uf3_client.get("/api/v1/runs/active")
    assert active_resp.status_code == 200
    snap_active = active_resp.json()["data"]["settings_snapshot"]["session"]
    assert snap_active["title"] == "Viết dàn ý Chương 2"
    assert snap_active["session_goal"] == "Hoàn thành 3 mục lớn của phần Cơ sở lý thuyết"
    assert snap_active["expected_output"] == "File outline.md tối thiểu 12 gạch đầu dòng"

    # Kiểm tra nhánh fallback _resolve_settings_snapshot (khi run.settings_snapshot trống)
    run_uuid = UUID(start_resp.json()["data"]["run_id"])
    async with uf3_env["sessions"]() as db:
        run_row = await db.get(SessionRun, run_uuid)
        assert run_row is not None
        run_row.settings_snapshot = {}
        await db.commit()

        detail = await run_service.get_run_detail(
            db, user_id=str(user_id), run_id=str(run_uuid), now=now
        )
        assert detail.settings_snapshot.session.title == "Viết dàn ý Chương 2"
        assert (
            detail.settings_snapshot.session.session_goal
            == "Hoàn thành 3 mục lớn của phần Cơ sở lý thuyết"
        )
        assert (
            detail.settings_snapshot.session.expected_output
            == "File outline.md tối thiểu 12 gạch đầu dòng"
        )


@pytest.mark.asyncio
async def test_uf3_f05_legacy_settings_snapshot_missing_uf2_fields_returns_none(
    uf3_env, uf3_client
):
    """UF3-F05 (Backward compat): settings_snapshot JSON bản legacy (thiếu 3 khóa UF2) -> GET /runs/{id} trả 200 với None."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task Legacy Snap", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step Legacy",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=now,
            status="in_progress",
            start_state="started",
        )
        db.add(sess)
        await db.flush()

        legacy_snapshot = {
            "effective_revision": 1,
            "session": {
                "session_id": str(sess.id),
                "step_id": str(step.id),
                "scheduled_start": now.isoformat(),
                "scheduled_end": (now + timedelta(seconds=1800)).isoformat(),
                "micro_task_text": "Mở tài liệu",
                "micro_task_seconds": 120,
                "work_seconds": 1500,
                "rest_seconds": 300,
                "segments": [
                    {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                ],
                "duration_seconds": 1800,
                "schedule_revision": 1,
                "status": "in_progress",
                "calendar_event_link_id": None,
                "start_state": "started",
                "start_decision_due_at": (now + timedelta(seconds=900)).isoformat(),
            },
        }
        run = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess.id,
            device_id="dev-legacy",
            started_at=now,
            ended_at=None,
            run_state="running",
            active_phase="work",
            segment_index=0,
            work_elapsed_seconds=0,
            rest_elapsed_seconds=0,
            outcome="unknown",
            settings_snapshot=legacy_snapshot,
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    resp = await uf3_client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    sess_snap = resp.json()["data"]["settings_snapshot"]["session"]
    assert sess_snap["title"] is None
    assert sess_snap["session_goal"] is None
    assert sess_snap["expected_output"] is None


@pytest.mark.asyncio
async def test_uf3_f03_f05_continuation_run_preserves_metadata_and_planned_ends_at(uf3_env):
    """UF3-F03 + UF3-F05: Continuation run từ approve_proposal có đủ metadata UF2 và planned_ends_at."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime(2026, 11, 25, 11, 0, 0, tzinfo=UTC)
    prev_start = now - timedelta(minutes=32)
    prev_end = now - timedelta(minutes=2)
    cont_start = now
    cont_end = cont_start + timedelta(seconds=900)

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task Cont", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step Cont",
            order_index=0,
            estimated_base_seconds=3000,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        prev_sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=prev_start,
            status="in_progress",
            start_state="started",
        )
        db.add(prev_sess)
        await db.flush()
        src_run = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=prev_sess.id,
            device_id="dev-cont",
            started_at=prev_start,
            ended_at=prev_end,
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=300,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="partial",
            checkin_timeout_at=prev_end + timedelta(seconds=300),
            dnd_release_at=prev_end + timedelta(seconds=300),
            settings_snapshot={},
        )
        db.add(src_run)
        await db.flush()

        source_revs = {
            "task": task.revision,
            "schedule": 1,
            "availability": 1,
            "settings": 1,
            "calendar": 0,
        }
        proposal = RescheduleProposal(
            id=uuid4(),
            user_id=user_id,
            trigger_reason="checkin_incomplete",
            target_task_id=task.id,
            allowed_task_ids=[str(task.id)],
            affected_task_ids=[str(task.id)],
            base_schedule_revision=1,
            source_revisions=source_revs,
            proposal_revision=1,
            proposed_changes={
                "sessions_to_move": [],
                "sessions_to_add": [
                    {
                        "temp_session_id": "temp-cont-1",
                        "action": "create_continuation_session",
                        "task_id": str(task.id),
                        "step_id": str(step.id),
                        "source_run_id": str(src_run.id),
                        "before": None,
                        "after": {
                            "step_id": str(step.id),
                            "start": cont_start.isoformat(),
                            "end": cont_end.isoformat(),
                            "title": "Làm tiếp phần phân tích số liệu",
                            "session_goal": "Hoàn tất bảng so sánh 2 nhóm",
                            "expected_output": "Bảng số liệu đã kiểm chéo",
                            "work_seconds": 900,
                            "rest_seconds": 0,
                            "duration_seconds": 900,
                            "micro_task_text": "Mở lại bảng tính đang làm dở",
                            "micro_task_seconds": 0,
                            "segments": [
                                {"order_index": 0, "kind": "work", "duration_seconds": 900}
                            ],
                        },
                        "reason": "immediate_continuation",
                    }
                ],
                "sessions_to_cancel": [],
                "steps_to_add": [],
                "_meta": {
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(src_run.id),
                        "expected_run_revision": 1,
                        "additional_work_seconds": 900,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                    },
                    "gap_exceptions": [
                        {
                            "from_session_ref": str(prev_sess.id),
                            "to_session_ref": "temp-cont-1",
                            "standard_gap_seconds": 900,
                            "applied_gap_seconds": 120,
                            "reason": "immediate_continuation",
                        }
                    ],
                    "start_window": {
                        "earliest_start": cont_start.isoformat(),
                        "latest_start": (cont_start + timedelta(minutes=10)).isoformat(),
                        "not_before": cont_start.isoformat(),
                        "not_after": (cont_start + timedelta(minutes=10)).isoformat(),
                    },
                    "approval_envelope": {
                        "rule": "actual_start_plus_fixed_work_plus_min_break_le_latest_start",
                        "continuation_fixed_work_seconds": 900,
                        "continuation_min_break_after_seconds": 900,
                        "shifted_sessions_bounds": [],
                    },
                },
            },
            feasibility={"status": "feasible", "confirmable": True, "blocking_reasons": []},
            explanation="Mình đã xếp thêm 15 phút làm tiếp ngay cho bạn.",
            status="pending",
        )
        db.add(proposal)
        await db.commit()

        approved = await reschedule_service.approve_proposal(
            db,
            user_id=str(user_id),
            proposal_id=str(proposal.id),
            idempotency_key="idem-cont-meta",
            payload=ApproveProposalRequest(
                expected_proposal_revision=1,
                expected_base_schedule_revision=1,
                expected_source_revisions=ProposalSourceRevisions(**source_revs),
                start_continuation=True,
                device_id="dev-cont",
                event_id="ev-cont-start-1",
                expected_source_run_revision=1,
            ),
            now=now,
        )
        assert approved.continuation_run is not None
        cont_run = approved.continuation_run
        assert cont_run.planned_ends_at == cont_run.started_at + timedelta(seconds=900)
        assert cont_run.settings_snapshot.session.title == "Làm tiếp phần phân tích số liệu"
        assert (
            cont_run.settings_snapshot.session.session_goal == "Hoàn tất bảng so sánh 2 nhóm"
        )
        assert (
            cont_run.settings_snapshot.session.expected_output == "Bảng số liệu đã kiểm chéo"
        )


# ==============================================================================
# 5. UF3-F12 (D2-Safe): Latest-only PlanningContext + primary vs secondary write calendar
# ==============================================================================


@pytest.mark.asyncio
async def test_uf3_f12_d2_safe_latest_planning_context_only(uf3_env, uf3_client):
    """UF3-F12 (Latest-only): 2 PlanningContext (cũ có bận trùng giờ, mới nhất trống) -> chỉ đọc bản mới nhất -> 201."""
    user_id: UUID = uf3_env["user_id"]
    avail_id: UUID = uf3_env["availability_id"]
    now = datetime.now(UTC).replace(microsecond=0)

    async with uf3_env["sessions"]() as db:
        draft_own = PlanningDraft(
            user_id=user_id,
            client_draft_id="draft-own-f12",
            input_source="text",
            summary={"title": "Own"},
        )
        draft_other = PlanningDraft(
            user_id=user_id,
            client_draft_id="draft-other-f12",
            input_source="text",
            summary={"title": "Other"},
        )
        db.add_all([draft_own, draft_other])
        await db.flush()

        task = Task(
            user_id=user_id,
            source_draft_id=draft_own.id,
            title="Task F12 Latest",
            time_constraint={"kind": "window"},
        )
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(user_id=user_id, step_id=step.id, start_at=now)
        db.add(sess)

        # Context cũ: có sự kiện bận trùng giờ
        old_ctx = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            availability_id=avail_id,
            availability_revision=1,
            query_start=now - timedelta(hours=2),
            query_end=now + timedelta(hours=4),
            fetched_at=now - timedelta(hours=2),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["primary"],
            selected_calendar_sources=[],
            busy_intervals=[
                {
                    "start_at": (now + timedelta(minutes=5)).isoformat(),
                    "end_at": (now + timedelta(minutes=25)).isoformat(),
                    "source": {
                        "kind": "calendar_event",
                        "google_calendar_id": "primary",
                        "google_event_id": "stale-ev-1",
                    },
                }
            ],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
            created_at=now - timedelta(hours=2),
        )
        # Context mới nhất hợp lệ cho task hiện tại: không còn bận trùng giờ
        new_ctx = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            availability_id=avail_id,
            availability_revision=1,
            query_start=now - timedelta(hours=2),
            query_end=now + timedelta(hours=4),
            fetched_at=now - timedelta(minutes=5),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["primary"],
            selected_calendar_sources=[],
            busy_intervals=[],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
            created_at=now - timedelta(minutes=5),
        )
        # Context còn mới hơn nữa nhưng thuộc draft khác -> phải bị bỏ qua (continue)
        other_draft_ctx = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            source_draft_id=draft_other.id,
            source_summary_revision=1,
            availability_snapshot={"timezone": "UTC", "weekly_rules": [], "date_overrides": []},
            query_start=now - timedelta(hours=2),
            query_end=now + timedelta(hours=4),
            fetched_at=now - timedelta(minutes=1),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["primary"],
            selected_calendar_sources=[],
            busy_intervals=[
                {
                    "start_at": now.isoformat(),
                    "end_at": (now + timedelta(minutes=30)).isoformat(),
                    "source": {
                        "kind": "calendar_event",
                        "google_calendar_id": "primary",
                        "google_event_id": "other-draft-ev",
                    },
                }
            ],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
            created_at=now - timedelta(minutes=1),
        )
        db.add_all([old_ctx, new_ctx, other_draft_ctx])
        await db.commit()
        sess_id = sess.id

    resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f12-latest"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f12",
            "requested_start_at": now.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_uf3_f12_d2_safe_primary_blocked_but_secondary_write_calendar_filtered(
    uf3_env, uf3_client
):
    """UF3-F12 (Primary guard): write_calendar_id='primary' vẫn chặn 409; lịch phụ ProPlan được lọc bỏ đúng."""
    user_id: UUID = uf3_env["user_id"]
    avail_id: UUID = uf3_env["availability_id"]
    now = datetime.now(UTC).replace(microsecond=0)

    # Scenario 1: write_calendar_id == "primary" -> sự kiện trên "primary" PHẢI chặn 409
    async with uf3_env["sessions"]() as db:
        conn_primary = CalendarConnection(
            id=uuid4(),
            user_id=user_id,
            provider="google",
            account_ref="primary@example.test",
            read_calendar_ids=["primary"],
            write_calendar_id="primary",
            granted_scopes=["calendar.events"],
            connection_state="connected",
            credential_ref="cred-primary",
        )
        task = Task(user_id=user_id, title="Task F12 Primary", time_constraint={"kind": "window"})
        db.add_all([conn_primary, task])
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(user_id=user_id, step_id=step.id, start_at=now)
        db.add(sess)
        ctx_primary = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            availability_id=avail_id,
            availability_revision=1,
            query_start=now - timedelta(hours=1),
            query_end=now + timedelta(hours=3),
            fetched_at=now - timedelta(minutes=2),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["primary"],
            selected_calendar_sources=[],
            busy_intervals=[
                {
                    "start_at": (now + timedelta(minutes=5)).isoformat(),
                    "end_at": (now + timedelta(minutes=20)).isoformat(),
                    "source": {
                        "kind": "calendar_event",
                        "connection_id": str(conn_primary.id),
                        "google_calendar_id": "primary",
                        "google_event_id": "personal-meeting-1",
                    },
                }
            ],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
            created_at=now - timedelta(minutes=2),
        )
        db.add(ctx_primary)
        await db.commit()
        sess_id = sess.id
        conn_id = conn_primary.id

    blocked_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f12-primary-block"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f12",
            "requested_start_at": now.isoformat(),
        },
    )
    assert blocked_resp.status_code == 409
    assert blocked_resp.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # Scenario 2: Chuyển write_calendar_id sang lịch phụ "proplan_secondary_id"
    # + PlanningContext mới nhất chứa sự kiện trên lịch phụ (cả có connection_id và legacy connection_id=None) -> được lọc -> 201
    async with uf3_env["sessions"]() as db:
        conn_row = await db.get(CalendarConnection, conn_id)
        assert conn_row is not None
        conn_row.write_calendar_id = "proplan_secondary_id"
        conn_row.read_calendar_ids = ["primary", "proplan_secondary_id"]
        ctx_secondary = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            availability_id=avail_id,
            availability_revision=1,
            query_start=now - timedelta(hours=1),
            query_end=now + timedelta(hours=3),
            fetched_at=now - timedelta(minutes=1),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["primary", "proplan_secondary_id"],
            selected_calendar_sources=[],
            busy_intervals=[
                {
                    "start_at": (now + timedelta(minutes=5)).isoformat(),
                    "end_at": (now + timedelta(minutes=15)).isoformat(),
                    "source": {
                        "kind": "calendar_event",
                        "connection_id": str(conn_id),
                        "google_calendar_id": "proplan_secondary_id",
                        "google_event_id": "proplan-mirror-ev-1",
                    },
                },
                {
                    "start_at": (now + timedelta(minutes=15)).isoformat(),
                    "end_at": (now + timedelta(minutes=25)).isoformat(),
                    "source": {
                        "kind": "calendar_event",
                        "connection_id": None,
                        "google_calendar_id": "proplan_secondary_id",
                        "google_event_id": "proplan-mirror-ev-legacy",
                    },
                },
            ],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
            created_at=now - timedelta(minutes=1),
        )
        db.add(ctx_secondary)
        await db.commit()

    allowed_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f12-secondary-allow"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f12",
            "requested_start_at": now.isoformat(),
        },
    )
    assert allowed_resp.status_code == 201, allowed_resp.text


@pytest.mark.asyncio
async def test_uf3_f12_manual_busy_and_account_email_as_primary_still_block(
    uf3_env, uf3_client
):
    """UF3-F12 (Edge): manual_busy_intervals vẫn chặn 409 khi có lịch phụ; write_calendar_id == account_ref (email chính) vẫn chặn 409."""
    user_id: UUID = uf3_env["user_id"]
    avail_id: UUID = uf3_env["availability_id"]
    now = datetime.now(UTC).replace(microsecond=0)

    # Case 1: write_calendar_id là lịch phụ nhưng có manual_busy_intervals trùng giờ -> PHẢI chặn 409
    async with uf3_env["sessions"]() as db:
        conn_sec = CalendarConnection(
            id=uuid4(),
            user_id=user_id,
            provider="google",
            account_ref="bob@gmail.com",
            read_calendar_ids=["primary", "proplan_secondary_bob"],
            write_calendar_id="proplan_secondary_bob",
            granted_scopes=["calendar.events"],
            connection_state="connected",
            credential_ref="cred-bob",
        )
        task = Task(user_id=user_id, title="Task F12 Edge", time_constraint={"kind": "window"})
        db.add_all([conn_sec, task])
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(user_id=user_id, step_id=step.id, start_at=now)
        db.add(sess)
        ctx_manual = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            availability_id=avail_id,
            availability_revision=1,
            query_start=now - timedelta(hours=1),
            query_end=now + timedelta(hours=3),
            fetched_at=now - timedelta(minutes=2),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["primary", "proplan_secondary_bob"],
            selected_calendar_sources=[],
            busy_intervals=[],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[
                {
                    "client_interval_id": "manual-1",
                    "start_at": (now + timedelta(minutes=10)).isoformat(),
                    "end_at": (now + timedelta(minutes=25)).isoformat(),
                }
            ],
            excluded_sources=[],
            created_at=now - timedelta(minutes=2),
        )
        db.add(ctx_manual)
        await db.commit()
        sess_id = sess.id
        conn_id = conn_sec.id

    manual_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f12-manual-block"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f12-edge",
            "requested_start_at": now.isoformat(),
        },
    )
    assert manual_resp.status_code == 409
    assert manual_resp.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # Case 2: write_calendar_id == account_ref (alice@gmail.com) -> là lịch chính, KHÔNG bị lọc -> vẫn chặn 409
    async with uf3_env["sessions"]() as db:
        conn_row = await db.get(CalendarConnection, conn_id)
        assert conn_row is not None
        conn_row.account_ref = "alice@gmail.com"
        conn_row.write_calendar_id = "alice@gmail.com"
        conn_row.read_calendar_ids = ["alice@gmail.com"]

        ctx_email_primary = PlanningContext(
            id=uuid4(),
            user_id=user_id,
            availability_id=avail_id,
            availability_revision=1,
            query_start=now - timedelta(hours=1),
            query_end=now + timedelta(hours=3),
            fetched_at=now - timedelta(minutes=1),
            sync_state="valid",
            coverage_state="complete",
            selected_calendar_ids=["alice@gmail.com"],
            selected_calendar_sources=[],
            busy_intervals=[
                {
                    "start_at": (now + timedelta(minutes=5)).isoformat(),
                    "end_at": (now + timedelta(minutes=20)).isoformat(),
                    "source": {
                        "kind": "calendar_event",
                        "connection_id": str(conn_id),
                        "google_calendar_id": "alice@gmail.com",
                        "google_event_id": "alice-personal-1",
                    },
                }
            ],
            calendar_errors=[],
            event_snapshots=[],
            manual_busy_intervals=[],
            excluded_sources=[],
            created_at=now - timedelta(minutes=1),
        )
        db.add(ctx_email_primary)
        await db.commit()

    email_primary_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-f12-email-primary-block"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f12-edge",
            "requested_start_at": now.isoformat(),
        },
    )
    assert email_primary_resp.status_code == 409
    assert email_primary_resp.json()["error"]["code"] == "START_REQUIRES_REPLAN"


# ==============================================================================
# 6. UF3-F13: end_reason == "cancelled" vs "user_stopped" (+ giữ nguyên step/task.status lúc Start)
# ==============================================================================


@pytest.mark.asyncio
async def test_uf3_f13_end_reason_cancelled_clears_actual_metrics_and_preserves_step_task_status(
    uf3_env, uf3_client
):
    """UF3-F13: end_reason='cancelled' -> actual_work_seconds=None, actual_source=None; user_stopped giữ timer_proxy."""
    user_id: UUID = uf3_env["user_id"]
    now = datetime.now(UTC).replace(microsecond=0)
    t1 = now - timedelta(minutes=45)
    t2 = now

    async with uf3_env["sessions"]() as db:
        task = Task(user_id=user_id, title="Task F13", time_constraint={"kind": "window"})
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Step F13",
            order_index=0,
            estimated_base_seconds=3000,
            remaining_base_seconds=3000,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        s1 = _make_session_row(user_id=user_id, step_id=step.id, start_at=t1)
        s2 = _make_session_row(user_id=user_id, step_id=step.id, start_at=t2)
        db.add_all([s1, s2])
        await db.commit()
        task_id, step_id, s1_id, s2_id = task.id, step.id, s1.id, s2.id

        # Run 1: start rồi end với end_reason="cancelled"
        r1 = await run_service.start_session_run(
            db,
            user_id=str(user_id),
            session_id=str(s1_id),
            idempotency_key="idem-f13-start-1",
            payload=SessionStartRequest(
                expected_schedule_revision=1,
                device_id="dev-f13",
                requested_start_at=t1,
            ),
            now=t1,
        )
        # Bảo toàn step.status == "pending" & task.status == "pending" lúc Start
        st_check = await db.get(Step, step_id)
        tk_check = await db.get(Task, task_id)
        assert st_check is not None and st_check.status == "pending"
        assert tk_check is not None and tk_check.status == "pending"

        ended_cancel = await run_service.end_session_run(
            db,
            user_id=str(user_id),
            run_id=r1.run_id,
            idempotency_key="idem-f13-end-cancel",
            payload=RunEndRequest(
                device_id="dev-f13",
                expected_run_revision=1,
                event_id="ev-f13-cancel",
                ended_at=t1 + timedelta(seconds=120),
                end_reason="cancelled",
                work_elapsed_seconds=120,
                rest_elapsed_seconds=0,
            ),
            now=t1 + timedelta(seconds=120),
        )
        assert ended_cancel.work_elapsed_seconds == 120
        assert ended_cancel.actual_work_seconds is None
        assert ended_cancel.actual_source is None

        # Verify persisted DB values
        r1_row = await db.scalar(
            select(SessionRun).where(SessionRun.id == UUID(r1.run_id))
        )
        assert r1_row is not None
        assert r1_row.actual_work_seconds is None
        assert r1_row.actual_source is None

    # Run 2 qua HTTP API: start rồi end với end_reason="user_stopped" -> giữ timer_proxy
    start_r2 = await uf3_client.post(
        f"/api/v1/sessions/{s2_id}/start",
        headers={"Idempotency-Key": "idem-f13-start-2"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-f13",
            "requested_start_at": t2.isoformat(),
        },
    )
    assert start_r2.status_code == 201, start_r2.text
    r2_id = start_r2.json()["data"]["run_id"]

    end_r2 = await uf3_client.post(
        f"/api/v1/runs/{r2_id}/end",
        headers={"Idempotency-Key": "idem-f13-end-stop"},
        json={
            "device_id": "dev-f13",
            "expected_run_revision": 1,
            "event_id": "ev-f13-stop",
            "ended_at": (t2 + timedelta(seconds=60)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 60,
            "rest_elapsed_seconds": 0,
        },
    )
    assert end_r2.status_code == 200, end_r2.text
    end_r2_data = end_r2.json()["data"]
    assert end_r2_data["actual_work_seconds"] == 60
    assert end_r2_data["actual_source"] == "timer_proxy"


@pytest.mark.asyncio
async def test_uf3_f05_full_http_continuation_preserves_metadata_after_timer_elapsed(
    uf3_env, uf3_client, monkeypatch
):
    """BUG-E2E-01: Full HTTP Start -> End(timer_elapsed) -> POST /proposals -> POST /approve giữ đủ metadata UF2 & planned_ends_at."""
    user_id: UUID = uf3_env["user_id"]
    base_now = datetime(2026, 11, 26, 9, 0, 0, tzinfo=UTC)
    clock_state = {"now": base_now}

    def _fake_now(now: datetime | None = None) -> datetime:
        return (now or clock_state["now"]).astimezone(UTC)

    monkeypatch.setattr(run_service, "_current_utc_now", _fake_now)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", _fake_now)

    async with uf3_env["sessions"]() as db:
        task = Task(
            user_id=user_id,
            title="Task HTTP Continuation Full",
            time_constraint={"kind": "window"},
        )
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Bước mặc định nếu thiếu session title",
            order_index=0,
            estimated_base_seconds=3600,
            remaining_base_seconds=3600,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=base_now,
            work_seconds=1500,
            rest_seconds=0,
            title="Lập dàn ý thuyết trình",
            session_goal="Hoàn thành trọn vẹn bước: Lập dàn ý thuyết trình",
            expected_output="Dàn ý 5 mục chính",
        )
        db.add(sess)
        await db.commit()
        task_id, sess_id, sess_end = task.id, sess.id, sess.scheduled_end

    # 1. Start qua HTTP
    start_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-http-cont-7a-start"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-http-7a",
            "requested_start_at": base_now.isoformat(),
        },
    )
    assert start_resp.status_code == 201, start_resp.text
    run_id = start_resp.json()["data"]["run_id"]

    # 2. End khi hết giờ (timer_elapsed) qua HTTP
    clock_state["now"] = sess_end
    end_resp = await uf3_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "idem-http-cont-7a-end"},
        json={
            "device_id": "dev-http-7a",
            "expected_run_revision": 1,
            "event_id": "ev-http-cont-7a-end",
            "ended_at": sess_end.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 0,
        },
    )
    assert end_resp.status_code == 200, end_resp.text
    run_rev = end_resp.json()["data"]["run_revision"]

    # 3. Tạo Proposal qua POST /api/v1/proposals
    clock_state["now"] = sess_end + timedelta(seconds=30)
    prop_resp = await uf3_client.post(
        "/api/v1/proposals",
        headers={"Idempotency-Key": "idem-http-cont-7a-prop"},
        json={
            "trigger_reason": "checkin_incomplete",
            "target_task_id": str(task_id),
            "allowed_task_ids": [str(task_id)],
            "expected_base_schedule_revision": 1,
            "expected_source_revisions": {
                "task": 1,
                "schedule": 1,
                "availability": 1,
                "settings": 1,
                "calendar": 0,
            },
            "continuation_request": {
                "mode": "continue_now",
                "source_run_id": run_id,
                "expected_run_revision": run_rev,
                "additional_work_seconds": 900,
                "strategy": "extend_then_shift_later_sessions",
                "immediate_continuation": True,
                "borrow_break_seconds": 0,
            },
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text
    prop_data = prop_resp.json()["data"]
    assert prop_data["feasibility"]["confirmable"] is True
    added_after = prop_data["proposed_changes"]["sessions_to_add"][0]["after"]
    assert added_after["title"] == "Lập dàn ý thuyết trình"
    assert added_after["session_goal"] == "Hoàn thành trọn vẹn bước: Lập dàn ý thuyết trình"
    assert added_after["expected_output"] == "Dàn ý 5 mục chính"

    # 4. Approve tại earliest_start với start_continuation=True qua HTTP
    earliest_cont = datetime.fromisoformat(prop_data["start_window"]["earliest_start"])
    clock_state["now"] = earliest_cont
    app_resp = await uf3_client.post(
        f"/api/v1/proposals/{prop_data['proposal_id']}/approve",
        headers={"Idempotency-Key": "idem-http-cont-7a-approve"},
        json={
            "expected_proposal_revision": prop_data["proposal_revision"],
            "expected_base_schedule_revision": prop_data["base_schedule_revision"],
            "expected_source_revisions": prop_data["source_revisions"],
            "start_continuation": True,
            "device_id": "dev-http-7a",
            "event_id": "ev-http-cont-7a-start",
            "expected_source_run_revision": run_rev,
        },
    )
    assert app_resp.status_code == 200, app_resp.text
    cont_run = app_resp.json()["data"]["continuation_run"]
    assert cont_run is not None
    assert datetime.fromisoformat(cont_run["planned_ends_at"]) == (
        datetime.fromisoformat(cont_run["started_at"]) + timedelta(seconds=900)
    )
    cont_snap = cont_run["settings_snapshot"]["session"]
    assert cont_snap["title"] == "Lập dàn ý thuyết trình"
    assert cont_snap["session_goal"] == "Hoàn thành trọn vẹn bước: Lập dàn ý thuyết trình"
    assert cont_snap["expected_output"] == "Dàn ý 5 mục chính"


@pytest.mark.asyncio
async def test_uf3_continuation_early_user_stopped_before_checkin_approves_without_preview_stale(
    uf3_env, uf3_client, monkeypatch
):
    """BUG-E2E-02: Dừng sớm user_stopped trước check-in -> POST /proposals -> POST /approve tại earliest_start phải trả 200 OK (không 409 PREVIEW_STALE)."""
    user_id: UUID = uf3_env["user_id"]
    base_now = datetime(2026, 11, 26, 10, 0, 0, tzinfo=UTC)
    clock_state = {"now": base_now}

    def _fake_now(now: datetime | None = None) -> datetime:
        return (now or clock_state["now"]).astimezone(UTC)

    monkeypatch.setattr(run_service, "_current_utc_now", _fake_now)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", _fake_now)

    async with uf3_env["sessions"]() as db:
        task = Task(
            user_id=user_id,
            title="Task HTTP Continuation Early Stop",
            time_constraint={"kind": "window"},
        )
        db.add(task)
        await db.flush()
        step = Step(
            user_id=user_id,
            task_id=task.id,
            title="Bước dừng sớm",
            order_index=0,
            estimated_base_seconds=3600,
            remaining_base_seconds=3600,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()
        sess = _make_session_row(
            user_id=user_id,
            step_id=step.id,
            start_at=base_now,
            work_seconds=1800,
            rest_seconds=0,
            title="Soạn đề cương sớm",
            session_goal="Xong 2 mục đầu",
            expected_output="2 mục hoàn chỉnh",
        )
        db.add(sess)
        await db.commit()
        task_id, sess_id = task.id, sess.id

    # 1. Start phiên 30 phút
    start_resp = await uf3_client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": "idem-http-cont-7b-start"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-http-7b",
            "requested_start_at": base_now.isoformat(),
        },
    )
    assert start_resp.status_code == 201, start_resp.text
    run_id = start_resp.json()["data"]["run_id"]

    # 2. Dừng sớm sau 10 phút (ended_at < scheduled_end, chưa trả lời CheckIn nên session.status == 'in_progress')
    early_end_at = base_now + timedelta(seconds=600)
    clock_state["now"] = early_end_at
    end_resp = await uf3_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "idem-http-cont-7b-end"},
        json={
            "device_id": "dev-http-7b",
            "expected_run_revision": 1,
            "event_id": "ev-http-cont-7b-end",
            "ended_at": early_end_at.isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 600,
            "rest_elapsed_seconds": 0,
        },
    )
    assert end_resp.status_code == 200, end_resp.text
    run_rev = end_resp.json()["data"]["run_revision"]

    # 3. Tạo continuation proposal (earliest_start = early_end_at + 120s, sớm hơn scheduled_end)
    clock_state["now"] = early_end_at + timedelta(seconds=30)
    prop_resp = await uf3_client.post(
        "/api/v1/proposals",
        headers={"Idempotency-Key": "idem-http-cont-7b-prop"},
        json={
            "trigger_reason": "checkin_incomplete",
            "target_task_id": str(task_id),
            "allowed_task_ids": [str(task_id)],
            "expected_base_schedule_revision": 1,
            "expected_source_revisions": {
                "task": 1,
                "schedule": 1,
                "availability": 1,
                "settings": 1,
                "calendar": 0,
            },
            "continuation_request": {
                "mode": "continue_now",
                "source_run_id": run_id,
                "expected_run_revision": run_rev,
                "additional_work_seconds": 900,
                "strategy": "extend_then_shift_later_sessions",
                "immediate_continuation": True,
                "borrow_break_seconds": 0,
            },
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text
    prop_data = prop_resp.json()["data"]
    assert prop_data["feasibility"]["confirmable"] is True

    # 4. Approve ngay tại earliest_start -> PHẢI trả 200 OK (không còn 409 PREVIEW_STALE)
    earliest_cont = datetime.fromisoformat(prop_data["start_window"]["earliest_start"])
    clock_state["now"] = earliest_cont
    app_resp = await uf3_client.post(
        f"/api/v1/proposals/{prop_data['proposal_id']}/approve",
        headers={"Idempotency-Key": "idem-http-cont-7b-approve"},
        json={
            "expected_proposal_revision": prop_data["proposal_revision"],
            "expected_base_schedule_revision": prop_data["base_schedule_revision"],
            "expected_source_revisions": prop_data["source_revisions"],
            "start_continuation": True,
            "device_id": "dev-http-7b",
            "event_id": "ev-http-cont-7b-start",
            "expected_source_run_revision": run_rev,
        },
    )
    assert app_resp.status_code == 200, app_resp.text
    cont_run = app_resp.json()["data"]["continuation_run"]
    assert cont_run is not None
    assert datetime.fromisoformat(cont_run["planned_ends_at"]) == (
        datetime.fromisoformat(cont_run["started_at"]) + timedelta(seconds=900)
    )
    cont_snap = cont_run["settings_snapshot"]["session"]
    assert cont_snap["title"] == "Soạn đề cương sớm"
    assert cont_snap["session_goal"] == "Xong 2 mục đầu"
    assert cont_snap["expected_output"] == "2 mục hoàn chỉnh"
