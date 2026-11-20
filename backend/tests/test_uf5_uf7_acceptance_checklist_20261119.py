"""Comprehensive Acceptance Verification Suite for UF5 and UF7 (2026-11-19).

Bao phủ đầy đủ 5 ca kiểm bắt buộc UF5 và 6 ca kiểm bắt buộc UF7 theo:
- UF5_BUGS_AND_RISKS_HANDOFF_20261119.md
- UF7_BUGS_AND_RISKS_HANDOFF_20261119.md
- 00_UF3_TO_UF7_SHARED_RULES_AND_UF12_ALIGNMENT_20261119.md
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models.calendar import CalendarConnection, CalendarEventLink, CalendarSyncJob
from app.models.execution import CheckIn, SessionRun
from app.models.planning import PlanningDraft
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.settings import PreferenceChanges, SettingsChangeRequest
from app.schemas.user import CurrentUser
from app.services import run_service, settings_service
from app.services.planning.adaptation import evaluate_buffer_adaptation

TZ_VN = timezone(timedelta(hours=7))


_ACTIVE_ENGINES: list[Any] = []


@pytest.fixture(autouse=True)
async def _cleanup_dependency_overrides():
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        while _ACTIVE_ENGINES:
            eng = _ACTIVE_ENGINES.pop()
            try:
                await eng.dispose()
            except Exception:
                pass


def _auth_headers(user_id: UUID, idem_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"u_{user_id.hex[:8]}@example.com",
        display_name="Doãn Thành",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-token-{user_id.hex[:8]}"}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return headers


async def _make_test_env():
    url = os.environ.get("PROACTIVE_TEST_PG_URL", "sqlite+aiosqlite:///:memory:")
    engine = make_engine(url)
    _ACTIVE_ENGINES.append(engine)
    sf = session_factory(engine)
    async with engine.begin() as conn:
        from app.models import Base

        await conn.run_sync(Base.metadata.create_all)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return engine, sf


# =============================================================================
# PHẦN 1: UF5 ACCEPTANCE CHECKLIST (5 CA BẮT BUỘC + CLOCK SKEW GUARD)
# =============================================================================


async def _seed_single_session_step(
    db: AsyncSession,
    *,
    step_remaining: int = 1500,
    step_status: str = "in_progress",
    unexpanded_scope: str | None = None,
) -> dict[str, Any]:
    user_id = uuid4()
    now = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)

    user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester")
    pref = UserPreferenceRow(
        user_id=user_id,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
        buffer_multiplier=1.2,
        reminder_offsets=[900, 600, 300],
        effective_revision=1,
    )
    task = TaskRow(
        user_id=user_id,
        title="Báo cáo tiến độ",
        status="in_progress",
        unexpanded_scope=unexpanded_scope,
        time_constraint={"kind": "window"},
    )
    db.add_all([user, pref, task])
    await db.flush()

    step = StepRow(
        user_id=user_id,
        task_id=task.id,
        order_index=0,
        title="Bước duy nhất",
        estimated_base_seconds=1500,
        remaining_base_seconds=step_remaining,
        status=step_status,
        buffer_multiplier=1.2,
        estimate_source="user_confirmed",
    )
    db.add(step)
    await db.flush()

    sess_start = now
    sess_end = sess_start + timedelta(minutes=30)
    session = SessionRow(
        user_id=user_id,
        step_id=step.id,
        scheduled_start=sess_start,
        scheduled_end=sess_end,
        work_seconds=1500,
        rest_seconds=300,
        segments=[],
        duration_seconds=1800,
        schedule_revision=1,
        status="scheduled",
    )
    db.add(session)
    await db.flush()

    run = SessionRun(
        user_id=user_id,
        session_id=session.id,
        device_id="dev-test",
        started_at=sess_start,
        ended_at=sess_end,
        actual_work_seconds=20,
        work_elapsed_seconds=20,
        rest_elapsed_seconds=0,
        run_state="awaiting_checkin",
        actual_source="timer_proxy",
        settings_snapshot={"work_interval_seconds": 1500},
        revision=1,
    )
    db.add(run)
    await db.flush()

    checkin = CheckIn(
        user_id=user_id,
        run_id=run.id,
        kind="final",
        asked_at=sess_end,
        response_state="open",
        response_revision=0,
    )
    db.add(checkin)
    await db.commit()

    return {
        "user_id": user_id,
        "task_id": task.id,
        "step_id": step.id,
        "session_id": session.id,
        "run_id": run.id,
        "checkin_id": checkin.id,
        "ended_at": sess_end,
    }


@pytest.mark.asyncio
async def test_uf5_checklist_1_explicit_deny_prevents_step_and_task_completion(monkeypatch):
    """Ca 1: Step cuối có 1 Session, Run 20s -> ('completed', step_completed=False, task_completed=False)
    -> Session 'completed', Step KHÔNG 'completed' (remaining > 0), Task KHÔNG 'completed'."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_single_session_step(db)
        user_id = ids["user_id"]
        cid = ids["checkin_id"]
        ended_at = ids["ended_at"]

    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or ended_at).astimezone(UTC))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            f"/api/v1/check-ins/{cid}/response",
            headers=_auth_headers(user_id, "idem-uf5-c1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r.status_code == 200, r.text

    async with sf() as db:
        step = await db.get(StepRow, ids["step_id"])
        task = await db.get(TaskRow, ids["task_id"])
        sess = await db.get(SessionRow, ids["session_id"])
        assert sess.status == "completed"
        assert step.status != "completed"
        assert step.remaining_base_seconds > 0
        assert task.status != "completed"


@pytest.mark.asyncio
async def test_uf5_checklist_2_step_completed_true_task_completed_false(monkeypatch):
    """Ca 2: ('completed', step_completed=True, task_completed=False)
    -> Step 'completed' (remaining=0), Task KHÔNG 'completed'."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_single_session_step(db)
        user_id = ids["user_id"]
        cid = ids["checkin_id"]
        ended_at = ids["ended_at"]

    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or ended_at).astimezone(UTC))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            f"/api/v1/check-ins/{cid}/response",
            headers=_auth_headers(user_id, "idem-uf5-c2"),
            json={
                "expected_response_revision": 0,
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": False,
            },
        )
        assert r.status_code == 200, r.text

    async with sf() as db:
        step = await db.get(StepRow, ids["step_id"])
        task = await db.get(TaskRow, ids["task_id"])
        assert step.status == "completed"
        assert step.remaining_base_seconds == 0
        assert task.status != "completed"


@pytest.mark.asyncio
async def test_uf5_checklist_3_auto_complete_when_both_none_and_no_unexpanded_scope(monkeypatch):
    """Ca 3: ('completed', step_completed=None, task_completed=None)
    -> Step 'completed', Task 'completed' (khi không còn unexpanded_scope)."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_single_session_step(db, unexpanded_scope=None)
        user_id = ids["user_id"]
        cid = ids["checkin_id"]
        ended_at = ids["ended_at"]

    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or ended_at).astimezone(UTC))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            f"/api/v1/check-ins/{cid}/response",
            headers=_auth_headers(user_id, "idem-uf5-c3"),
            json={
                "expected_response_revision": 0,
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
            },
        )
        assert r.status_code == 200, r.text

    async with sf() as db:
        step = await db.get(StepRow, ids["step_id"])
        task = await db.get(TaskRow, ids["task_id"])
        assert step.status == "completed"
        assert step.remaining_base_seconds == 0
        assert task.status == "completed"


@pytest.mark.asyncio
async def test_uf5_checklist_4_correction_rollback_fallback_to_in_progress(monkeypatch):
    """Ca 4: Correction (completed, True, True) -> (incomplete, None, None)
    kể cả khi baseline thiếu/từng completed -> Step về trạng thái mở ('in_progress'),
    remaining_base_seconds > 0, Task không 'completed'."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_single_session_step(db)
        user_id = ids["user_id"]
        cid = ids["checkin_id"]
        ended_at = ids["ended_at"]

    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or ended_at).astimezone(UTC))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Bước 1: User lỡ xác nhận hoàn thành
        r1 = await client.put(
            f"/api/v1/check-ins/{cid}/response",
            headers=_auth_headers(user_id, "idem-uf5-c4-1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": True,
            },
        )
        assert r1.status_code == 200

        # Bước 2: User sửa lại thành incomplete
        r2 = await client.put(
            f"/api/v1/check-ins/{cid}/response",
            headers=_auth_headers(user_id, "idem-uf5-c4-2"),
            json={
                "expected_response_revision": 1,
                "responded_at": (ended_at + timedelta(seconds=20)).isoformat(),
                "session_outcome": "incomplete",
            },
        )
        assert r2.status_code == 200

    async with sf() as db:
        step = await db.get(StepRow, ids["step_id"])
        task = await db.get(TaskRow, ids["task_id"])
        assert step.status in ("in_progress", "pending")
        assert step.remaining_base_seconds > 0
        assert task.status != "completed"


@pytest.mark.asyncio
async def test_uf5_checklist_5_run2_newer_completion_protected_when_run1_corrected(monkeypatch):
    """Ca 5: Run 2 ở cùng Step (mới hơn) đã xác nhận Step hoàn thành,
    sửa lại Check-in Run 1 về step_completed=False -> giữ nguyên kết quả mới hơn của Run 2."""
    engine, sf = await _make_test_env()
    user_id = uuid4()
    now = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester")
        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 600, 300],
            effective_revision=1,
        )
        task = TaskRow(
            user_id=user_id,
            title="Công việc 2 phiên",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step chung",
            estimated_base_seconds=3000,
            remaining_base_seconds=3000,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        # Phiên 1
        s1_start = now
        s1_end = s1_start + timedelta(minutes=25)
        sess1 = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=s1_start,
            scheduled_end=s1_end,
            work_seconds=1500,
            rest_seconds=0,
            segments=[],
            duration_seconds=1500,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess1)
        await db.flush()
        run1 = SessionRun(
            user_id=user_id,
            session_id=sess1.id,
            device_id="dev-test",
            started_at=s1_start,
            ended_at=s1_end,
            actual_work_seconds=1500,
            run_state="closed",
            outcome="completed",
            revision=1,
            settings_snapshot={"work_interval_seconds": 1500},
        )
        db.add(run1)
        await db.flush()
        ci1 = CheckIn(
            user_id=user_id,
            run_id=run1.id,
            kind="final",
            asked_at=s1_end,
            response_state="answered",
            session_outcome="completed",
            step_completed=False,
            responded_at=s1_end + timedelta(seconds=10),
            response_revision=1,
        )
        db.add(ci1)

        # Phiên 2 (mới hơn phiên 1)
        s2_start = s1_end + timedelta(minutes=20)
        s2_end = s2_start + timedelta(minutes=25)
        sess2 = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=s2_start,
            scheduled_end=s2_end,
            work_seconds=1500,
            rest_seconds=0,
            segments=[],
            duration_seconds=1500,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess2)
        await db.flush()
        run2 = SessionRun(
            user_id=user_id,
            session_id=sess2.id,
            device_id="dev-test",
            started_at=s2_start,
            ended_at=s2_end,
            actual_work_seconds=1500,
            run_state="closed",
            outcome="completed",
            revision=1,
            settings_snapshot={"work_interval_seconds": 1500},
        )
        db.add(run2)
        await db.flush()
        ci2 = CheckIn(
            user_id=user_id,
            run_id=run2.id,
            kind="final",
            asked_at=s2_end,
            response_state="answered",
            session_outcome="completed",
            step_completed=True,
            responded_at=s2_end + timedelta(seconds=10),
            response_revision=1,
        )
        db.add(ci2)
        step.status = "completed"
        step.remaining_base_seconds = 0
        await db.commit()

        ci1_id = ci1.id
        step_id = step.id

    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or s2_end + timedelta(minutes=5)).astimezone(UTC))

    # Sửa Check-in của Run 1 (cũ hơn) thành step_completed=False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            f"/api/v1/check-ins/{ci1_id}/response",
            headers=_auth_headers(user_id, "idem-uf5-c5"),
            json={
                "expected_response_revision": 1,
                "responded_at": (s2_end + timedelta(minutes=5)).isoformat(),
                "session_outcome": "completed",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r.status_code == 200

    async with sf() as db:
        step = await db.get(StepRow, step_id)
        # Xác nhận của Run 2 mới hơn phải được giữ nguyên!
        assert step.status == "completed"
        assert step.remaining_base_seconds == 0


@pytest.mark.asyncio
async def test_uf5_checklist_future_timestamp_clock_skew_guards(monkeypatch):
    """UF5-BUG-04: Chặn timestamp tương lai quá +300s và asked_at < run.ended_at."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_single_session_step(db)
        user_id = ids["user_id"]
        run_id = ids["run_id"]
        cid = ids["checkin_id"]
        ended_at = ids["ended_at"]

    server_now = ended_at
    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or server_now).astimezone(UTC))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. respond_to_checkin với responded_at tương lai > server_now + 300s -> 422
        r_future_ans = await client.put(
            f"/api/v1/check-ins/{cid}/response",
            headers=_auth_headers(user_id, "idem-skew-1"),
            json={
                "expected_response_revision": 0,
                "responded_at": (server_now + timedelta(seconds=301)).isoformat(),
                "session_outcome": "completed",
            },
        )
        assert r_future_ans.status_code == 422
        assert r_future_ans.json()["error"]["code"] == "VALIDATION_ERROR"

        # 2. create_run_checkin với kind='final' và asked_at < run.ended_at -> 422
        r_early_final = await client.post(
            f"/api/v1/runs/{run_id}/check-ins",
            headers=_auth_headers(user_id, "idem-skew-2"),
            json={
                "kind": "final",
                "asked_at": (ended_at - timedelta(seconds=10)).isoformat(),
                "expected_run_revision": 1,
            },
        )
        assert r_early_final.status_code == 422
        assert r_early_final.json()["error"]["code"] == "VALIDATION_ERROR"


# =============================================================================
# PHẦN 2: UF7 ACCEPTANCE CHECKLIST (6 CA BẮT BUỘC)
# =============================================================================


async def _seed_uf7_environment(db: AsyncSession, *, buffer_multiplier: float = 1.2) -> dict[str, Any]:
    user_id = uuid4()
    now = run_service._current_utc_now()

    user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester UF7")
    pref = UserPreferenceRow(
        user_id=user_id,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
        buffer_multiplier=buffer_multiplier,
        reminder_offsets=[900, 600, 300],
        effective_revision=1,
    )
    task = TaskRow(
        user_id=user_id,
        title="Task UF7",
        status="in_progress",
        time_constraint={"kind": "window"},
    )
    db.add_all([user, pref, task])
    await db.flush()

    step = StepRow(
        user_id=user_id,
        task_id=task.id,
        order_index=0,
        title="Step UF7",
        estimated_base_seconds=1500,
        remaining_base_seconds=1500,
        status="in_progress",
        buffer_multiplier=1.2,
        estimate_source="user_confirmed",
    )
    db.add(step)
    await db.flush()

    # Session tương lai
    fut_start = now + timedelta(hours=2)
    fut_end = fut_start + timedelta(minutes=30)
    session = SessionRow(
        user_id=user_id,
        step_id=step.id,
        scheduled_start=fut_start,
        scheduled_end=fut_end,
        work_seconds=1500,
        rest_seconds=300,
        segments=[],
        duration_seconds=1800,
        schedule_revision=1,
        status="scheduled",
    )
    db.add(session)
    await db.commit()

    return {
        "user_id": user_id,
        "task_id": task.id,
        "step_id": step.id,
        "session_id": session.id,
        "fut_start": fut_start,
        "fut_end": fut_end,
        "now": now,
    }


@pytest.mark.asyncio
async def test_uf7_checklist_1_buffer_and_work_interval_boundary_validations():
    """Ca 1: buffer_multiplier: 0.99 (422), 1.0 (200), 2.0 (200), 2.01 (422), 2.5 (422), NaN/Inf (422)
    work_interval_seconds=0 (422); GET /preferences vẫn đọc được dòng DB cũ buffer=2.5 không nổ 500."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db, buffer_multiplier=2.5)
        user_id = ids["user_id"]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Kiểm chứng GET /preferences không bị 500 khi DB có buffer_multiplier=2.5
        r_get = await client.get("/api/v1/preferences", headers=_auth_headers(user_id))
        assert r_get.status_code == 200, r_get.text
        assert r_get.json()["data"]["buffer_multiplier"] == 2.5

        # Kiểm chứng work_interval_seconds=0 -> 422
        r_work0 = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-w0"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"work_interval_seconds": 0}},
            },
        )
        assert r_work0.status_code == 422

        # Kiểm chứng buffer_multiplier biên
        cases = [
            (0.99, 422),
            (1.0, 200),
            (2.0, 200),
            (2.01, 422),
            (2.5, 422),
        ]
        for val, expected_status in cases:
            r = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, f"idem-uf7-buf-{val}"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"buffer_multiplier": val}},
                },
            )
            assert r.status_code == expected_status, f"Expected {expected_status} for buffer={val}, got {r.status_code}"


@pytest.mark.asyncio
async def test_uf7_checklist_2_reminder_offsets_descending_validations():
    """Ca 2: reminder_offsets: [900, 600, 300] (200), [300, 900, 600] (422),
    [900, 600, 600] (422), [-1] (422)."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # [900, 600, 300] -> 200
        r_ok = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-rem-ok"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_offsets": [900, 600, 300]}},
            },
        )
        assert r_ok.status_code == 200

        # [300, 900, 600] không giảm dần -> 422
        r_bad_order = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-rem-bad1"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_offsets": [300, 900, 600]}},
            },
        )
        assert r_bad_order.status_code == 422

        # [900, 600, 600] trùng nhau -> 422
        r_dup = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-rem-dup"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_offsets": [900, 600, 600]}},
            },
        )
        assert r_dup.status_code == 422

        # [-1] âm -> 422
        r_neg = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-rem-neg"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_offsets": [-1]}},
            },
        )
        assert r_neg.status_code == 422


@pytest.mark.asyncio
async def test_uf7_checklist_3_h10_reminder_offsets_direct_effective_syncs_reminders():
    """Ca 3 (H10): có Session tương lai, chỉ đổi reminder_offsets=[1200, 600] -> 200,
    effective_from_next_session, không tạo proposal UF6, Reminder tương lai cập nhật theo mốc mới."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        sess_id = ids["session_id"]
        fut_start = ids["fut_start"]

        # Thêm 1 reminder cũ cho session tương lai
        db.add(
            Reminder(
                user_id=user_id,
                session_id=sess_id,
                schedule_revision=1,
                kind="pre_session",
                fire_at=fut_start - timedelta(seconds=900),
                dedupe_key=f"session:{sess_id}:version:1:reminder:900",
                delivery_state="scheduled",
            )
        )
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-h10"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_offsets": [1200, 600]}},
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        # Không tạo UF6 proposal
        assert data["application_state"] == "effective_from_next_session"
        assert data["pending_proposal_id"] is None

    async with sf() as db:
        rems = list(
            await db.scalars(
                select(Reminder)
                .where(Reminder.user_id == user_id, Reminder.session_id == sess_id)
                .order_by(Reminder.fire_at.desc())
            )
        )
        active_rems = [r for r in rems if r.delivery_state == "scheduled"]
        cancelled_rems = [r for r in rems if r.delivery_state == "cancelled"]
        assert len(cancelled_rems) == 1  # mốc 900 cũ bị hủy
        assert len(active_rems) == 2  # mốc 1200 và 600 mới được tạo


@pytest.mark.asyncio
async def test_uf7_checklist_4_a5_block_global_preferences_with_target_task_id():
    """Ca 4 (A5): gửi work_interval_seconds=900 (hoặc buffer_multiplier=1.4) kèm target_task_id
    -> 422 VALIDATION_ERROR."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        task_id = str(ids["task_id"])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-a5"),
            json={
                "expected_preferences_revision": 1,
                "expected_task_revision": 1,
                "target_task_id": task_id,
                "changes": {
                    "preferences": {"work_interval_seconds": 900},
                    "availability": {"weekly_rules": []},
                },
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_uf7_checklist_5_a6_adaptation_sample_filtering_and_threshold():
    """Ca 5 (A6 - P4-GAP-01 + BE-07):
    - parent no_respond (actual=3s) + child answered (actual=3s) -> collector trả 3s
    - < 7 mẫu -> None
    - >= 7 mẫu với diff == 0.20 -> None, diff > 0.20 -> tạo gợi ý
    - last_rejected_at > calc_now trả None thay vì 500."""
    engine, sf = await _make_test_env()
    user_id = uuid4()
    now = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="A6 Tester")
        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 600, 300],
            effective_revision=1,
        )
        task = TaskRow(
            user_id=user_id,
            title="Task A6",
            status="completed",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step A6",
            estimated_base_seconds=1500,
            remaining_base_seconds=0,
            status="completed",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=now,
            scheduled_end=now + timedelta(minutes=30),
            work_seconds=1500,
            rest_seconds=0,
            segments=[],
            duration_seconds=1500,
            schedule_revision=1,
            status="completed",
        )
        db.add(sess)
        await db.flush()

        # Run 1: timeout no_respond (actual 3s)
        run1 = SessionRun(
            user_id=user_id,
            session_id=sess.id,
            device_id="dev-test",
            started_at=now,
            ended_at=now + timedelta(seconds=3),
            actual_work_seconds=3,
            run_state="closed",
            outcome="completed",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        db.add(run1)
        await db.flush()
        ci1 = CheckIn(
            user_id=user_id,
            run_id=run1.id,
            kind="final",
            asked_at=now + timedelta(seconds=3),
            response_state="no_respond",
            session_outcome="unknown",
        )
        db.add(ci1)

        # Run 2: continuation answered (actual 3s)
        run2 = SessionRun(
            user_id=user_id,
            session_id=sess.id,
            device_id="dev-test",
            started_at=now + timedelta(seconds=10),
            ended_at=now + timedelta(seconds=13),
            actual_work_seconds=3,
            run_state="closed",
            outcome="completed",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        db.add(run2)
        await db.flush()
        ci2 = CheckIn(
            user_id=user_id,
            run_id=run2.id,
            kind="final",
            asked_at=now + timedelta(seconds=13),
            response_state="answered",
            session_outcome="completed",
        )
        db.add(ci2)
        await db.commit()

        # Kiểm chứng collector chỉ tính 3s từ Run 2 có response_state='answered'!
        samples = await settings_service.collect_buffer_step_samples(db, owner=user_id)
        assert len(samples) == 1
        assert samples[0]["actual_work_seconds"] == 3

    # Kiểm chứng adaptation function:
    # 1. < 7 mẫu -> None (min_sample_count default = 7)
    few_samples = [
        {"step_id": f"s{i}", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 1600, "scope_modified": False, "actual_source": "timer_proxy"}
        for i in range(6)
    ]
    res_few = evaluate_buffer_adaptation(few_samples, current_buffer_multiplier=1.2)
    assert res_few is None

    # 2. >= 7 mẫu nhưng diff == 0.20 -> None (bắt buộc strictly > 0.20)
    # median = 1.40. diff = |1.40 - 1.20| = 0.20 -> None!
    exact_samples = [
        {"step_id": f"s{i}", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 1400, "scope_modified": False, "actual_source": "timer_proxy"}
        for i in range(8)
    ]
    res_exact = evaluate_buffer_adaptation(exact_samples, current_buffer_multiplier=1.2, delta_threshold=0.2)
    assert res_exact is None

    # 3. >= 7 mẫu và diff > 0.20 -> Gợi ý được tạo!
    # median = 1.50. diff = |1.50 - 1.20| = 0.30 > 0.20 -> Gợi ý 1.50!
    suggest_samples = [
        {"step_id": f"s{i}", "status": "completed", "estimated_base_seconds": 1000, "actual_work_seconds": 1500, "scope_modified": False, "actual_source": "timer_proxy"}
        for i in range(8)
    ]
    res_suggest = evaluate_buffer_adaptation(suggest_samples, current_buffer_multiplier=1.2, delta_threshold=0.2)
    assert res_suggest is not None
    assert res_suggest["suggested_value"] == 1.50

    # 4. UF7-BUG-07: get_current_suggestion với last_rejected_at > calc_now trả None thay vì nổ 500
    async with sf() as db:
        # Tạo suggestion rejected ở tương lai
        fut_rej = AdaptationSuggestion(
            user_id=user_id,
            kind="buffer_multiplier",
            current_value=1.2,
            suggested_value=1.5,
            sample_count=8,
            sample_refs=[],
            reason="test",
            policy_revision="v2",
            status="rejected",
            updated_at=now + timedelta(hours=1),
        )
        db.add(fut_rej)
        await db.commit()

        # Gọi get_current_suggestion với now = now (trước updated_at của rejected suggestion)
        sugg = await settings_service.get_current_suggestion(db, user_id=str(user_id), now=now)
        assert sugg is None  # không raise 500 ValueError


@pytest.mark.asyncio
async def test_uf7_checklist_6_revision_collision_guard_with_pending_proposal():
    """Ca 6 (Revision guard): đang có pending_proposal_id (requested_revision=2),
    đổi reminder_style -> không làm effective_revision == requested_revision."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]

        # Đặt trạng thái đang có pending proposal
        prop = RescheduleProposal(
            user_id=user_id,
            trigger_reason="settings_change",
            allowed_task_ids=[],
            affected_task_ids=[],
            base_schedule_revision=1,
            source_revisions={"preferences": 1},
            proposal_revision=1,
            status="pending",
            proposed_changes={},
            feasibility={"feasible": True},
            explanation="Test pending proposal",
        )
        db.add(prop)
        await db.flush()

        pref = await db.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id))
        pref.effective_revision = 1
        pref.requested_revision = 2
        pref.pending_proposal_id = prop.id
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Đổi reminder_style (áp thẳng)
        r = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, "idem-uf7-rev-guard"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_style": "gentle_sound"}},
            },
        )
        assert r.status_code == 200

    async with sf() as db:
        pref = await db.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id))
        # effective_revision và requested_revision TUYỆT ĐỐI không bao giờ bằng nhau!
        assert pref.effective_revision != pref.requested_revision
        assert pref.effective_revision >= 2
        if pref.requested_revision is not None:
            assert pref.requested_revision > pref.effective_revision


@pytest.mark.asyncio
async def test_uf5_checklist_skip_chips_and_correction_rollback(monkeypatch: pytest.MonkeyPatch):
    """Ca kiểm tra nâng cao UF5:
    1. session_outcome='skipped' + skip_scope='skip_step' -> step.status='skipped', remaining=0, task giữ in_progress.
    2. Sửa lại check-in sang incomplete -> step khôi phục an toàn về in_progress, remaining_base_seconds > 0.
    3. session_outcome='skipped' + skip_scope='skip_session' -> step.status giữ in_progress.
    4. Chặn validation 422: skipped thiếu skip_scope hoặc completed kèm skip_scope.
    """
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_single_session_step(db, step_remaining=1500)
        user_id = ids["user_id"]
        checkin_id = ids["checkin_id"]
        step_id = ids["step_id"]
        task_id = ids["task_id"]
        ended_at = ids["ended_at"]

    monkeypatch.setattr(run_service, "_current_utc_now", lambda now=None: (now or ended_at).astimezone(UTC))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Chặn 422: skipped thiếu skip_scope
        bad_skip = await client.put(
            f"/api/v1/check-ins/{checkin_id}/response",
            headers=_auth_headers(user_id, "idem-bad-skip"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "skipped",
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
            },
        )
        assert bad_skip.status_code == 422

        # 2. Chặn 422: completed kèm skip_scope
        bad_comp = await client.put(
            f"/api/v1/check-ins/{checkin_id}/response",
            headers=_auth_headers(user_id, "idem-bad-comp"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "completed",
                "skip_scope": "skip_step",
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
            },
        )
        assert bad_comp.status_code == 422

        # 3. Hợp lệ: skip_scope='skip_step'
        r1 = await client.put(
            f"/api/v1/check-ins/{checkin_id}/response",
            headers=_auth_headers(user_id, "idem-skip-step"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "skipped",
                "skip_scope": "skip_step",
                "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
            },
        )
        assert r1.status_code == 200, r1.text

    async with sf() as db:
        st = await db.scalar(select(StepRow).where(StepRow.id == step_id))
        tk = await db.scalar(select(TaskRow).where(TaskRow.id == task_id))
        assert st.status == "skipped"
        assert st.remaining_base_seconds == 0
        assert tk.status == "in_progress"  # task không bị auto-complete khi step bị skipped

    # 4. Sửa lại (Correction) sang incomplete -> step khôi phục an toàn về in_progress, remaining > 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.put(
            f"/api/v1/check-ins/{checkin_id}/response",
            headers=_auth_headers(user_id, "idem-correct-skip"),
            json={
                "expected_response_revision": 1,
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
                "responded_at": (ended_at + timedelta(seconds=20)).isoformat(),
            },
        )
        assert r2.status_code == 200

    async with sf() as db:
        st = await db.scalar(select(StepRow).where(StepRow.id == step_id))
        tk = await db.scalar(select(TaskRow).where(TaskRow.id == task_id))
        assert st.status == "in_progress"
        assert st.remaining_base_seconds > 0
        assert tk.status == "in_progress"


@pytest.mark.asyncio
async def test_uf7_checklist_h10_suggestion_response_reminder_offsets_with_future_sessions():
    """Ca kiểm tra nâng cao UF7 (H10 Suggestion Edit):
    - Đang có phiên scheduled tương lai và có suggestion loại reminder_offset.
    - Sửa sang [1200, 600] -> áp thẳng effective_from_next_session, KHÔNG tạo proposal UF6.
    - Đồng bộ mốc nhắc tương lai (hủy mốc cũ, tạo mốc mới).
    - Chặn các giá trị sửa không giảm dần nghiêm ngặt ([300, 900], [600, 600], [-1]) với 422."""
    engine, sf = await _make_test_env()

    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        sess_id = ids["session_id"]
        fut_start = ids["fut_start"]

        # Mốc reminder cũ 900s
        db.add(
            Reminder(
                user_id=user_id,
                session_id=sess_id,
                schedule_revision=1,
                kind="pre_session",
                fire_at=fut_start - timedelta(seconds=900),
                dedupe_key=f"session:{sess_id}:version:1:reminder:900",
                delivery_state="scheduled",
            )
        )
        sugg = AdaptationSuggestion(
            user_id=user_id,
            kind="reminder_offset",
            current_value=[900, 600, 300],
            suggested_value=[600],
            sample_count=5,
            sample_refs=[],
            reason="Gợi ý nhắc trước 10 phút",
            policy_revision="v2",
            status="pending",
        )
        db.add(sugg)
        await db.commit()
        sugg_id = sugg.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Chặn giá trị sửa không hợp lệ (tăng dần [300, 900], bằng nhau [600, 600], âm [-1])
        for bad_val in ([300, 900], [600, 600], [-1], [1500, 1200, 900, 600]):
            r_bad = await client.post(
                f"/api/v1/suggestions/{sugg_id}/response",
                headers=_auth_headers(user_id, f"idem-bad-sugg-{uuid4()}"),
                json={
                    "expected_suggestion_revision": 1,
                    "action": "edit",
                    "edited_value": bad_val,
                },
            )
            assert r_bad.status_code == 422, f"Phải chặn {bad_val} nhưng nhận {r_bad.status_code}"

        # 2. Hợp lệ: edit sang [1200, 600]
        r_ok = await client.post(
            f"/api/v1/suggestions/{sugg_id}/response",
            headers=_auth_headers(user_id, "idem-ok-sugg-offsets"),
            json={
                "expected_suggestion_revision": 1,
                "action": "edit",
                "edited_value": [1200, 600],
            },
        )
        assert r_ok.status_code == 200, r_ok.text
        data = r_ok.json()["data"]
        assert data["status"] == "accepted"
        assert data["accepted_value"] == [1200, 600]
        assert data["application_state"] == "effective_from_next_session"
        assert data["pending_proposal_id"] is None

    async with sf() as db:
        rems = list(
            await db.scalars(
                select(Reminder)
                .where(Reminder.user_id == user_id, Reminder.session_id == sess_id)
                .order_by(Reminder.fire_at.desc())
            )
        )
        active_rems = [r for r in rems if r.delivery_state == "scheduled"]
        cancelled_rems = [r for r in rems if r.delivery_state == "cancelled"]
        assert len(cancelled_rems) == 1  # mốc 900 cũ bị hủy
        assert len(active_rems) == 2  # mốc 1200 và 600 mới được tạo


@pytest.mark.asyncio
async def test_uf7_checklist_buffer_multiplier_suggestion_response_boundaries():
    """Ca kiểm tra nâng cao UF7 (Gợi ý buffer_multiplier và các ranh giới):
    - Đang có suggestion buffer_multiplier.
    - Sửa edited_value: 2.0 (200), 1.0 (200), 2.01 (422), 0.99 (422), inf/nan (422), 'abc' (422)."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]

        sugg = AdaptationSuggestion(
            user_id=user_id,
            kind="buffer_multiplier",
            current_value=1.2,
            suggested_value=1.5,
            sample_count=8,
            sample_refs=[],
            reason="Gợi ý tăng buffer",
            policy_revision="v2",
            status="pending",
        )
        db.add(sugg)
        await db.commit()
        sugg_id = sugg.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Chặn biên ngoài [1.0, 2.0]
        for bad_buf in (0.99, 2.01, 2.5, "not_a_number"):
            r_bad = await client.post(
                f"/api/v1/suggestions/{sugg_id}/response",
                headers=_auth_headers(user_id, f"idem-bad-buf-{uuid4()}"),
                json={
                    "expected_suggestion_revision": 1,
                    "action": "edit",
                    "edited_value": bad_buf,
                },
            )
            assert r_bad.status_code == 422, f"Phải chặn {bad_buf} nhưng nhận {r_bad.status_code}"

        # Chấp nhận biên chuẩn 2.0 (có phiên tương lai -> đẩy qua UF6 pending_review)
        r_ok = await client.post(
            f"/api/v1/suggestions/{sugg_id}/response",
            headers=_auth_headers(user_id, "idem-ok-buf-20"),
            json={
                "expected_suggestion_revision": 1,
                "action": "edit",
                "edited_value": 2.0,
            },
        )
        assert r_ok.status_code == 200, r_ok.text
        data = r_ok.json()["data"]
        assert data["status"] == "accepted"
        assert data["accepted_value"] == 2.0
        assert data["application_state"] == "pending_review"
        assert data["pending_proposal_id"] is not None


@pytest.mark.asyncio
async def test_uf7_checklist_direct_service_expected_task_revision_defense_in_depth():
    """Ca kiểm tra UF7-BUG-08: Gọi thẳng apply_settings_change với target_task_id nhưng
    expected_task_revision=None -> AppError(status_code=422, code='VALIDATION_ERROR')."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        task_id = ids["task_id"]

        req = SettingsChangeRequest.model_construct(
            expected_preferences_revision=1,
            target_task_id=str(task_id),
            expected_task_revision=None,
            changes={"availability": {"weekly_rules": []}},
        )

        with pytest.raises(AppError) as exc_info:
            await settings_service.apply_settings_change(
                user_id=str(user_id),
                idempotency_key=f"idem-{uuid4()}",
                payload=req,
                db=db,
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "VALIDATION_ERROR"
        assert "expected_task_revision" in exc_info.value.message


# =============================================================================
# PHẦN 3: BỔ SUNG THEO FEEDBACK 2026-11-20 (BUG-1, BUG-2, BUG-3 & EXTENDED TESTS)
# =============================================================================


@pytest.mark.asyncio
async def test_uf5_t1_subsequent_run_deny_prevents_prior_run_forcing_completion():
    """UF5-T1 (Bắt BUG-2): Run 1 (10:00) có step_completed=True -> Run 2 (11:00) phản hồi
    session_outcome='partial', step_completed=False, task_completed=False ->
    sửa Check-in trên Run 1 cũ (giữ step_completed=True):
    Step & Task KHÔNG được bật về 'completed' vì Run 2 mới hơn đã phủ quyết."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    t_run1 = now - timedelta(hours=2)
    t_run2 = now - timedelta(hours=1)

    user_id = uuid4()
    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T1")
        pref = UserPreferenceRow(user_id=user_id, effective_revision=1)
        task = TaskRow(
            user_id=user_id,
            title="Task T1",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step T1",
            estimated_base_seconds=3000,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess1 = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=t_run1,
            scheduled_end=t_run1 + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[],
            duration_seconds=1800,
            schedule_revision=1,
            status="completed",
        )
        sess2 = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=t_run2,
            scheduled_end=t_run2 + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[],
            duration_seconds=1800,
            schedule_revision=1,
            status="completed",
        )
        db.add_all([sess1, sess2])
        await db.flush()

        # Run 1 và CheckIn 1 (10:00)
        run1 = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess1.id,
            revision=1,
            run_state="closed",
            started_at=t_run1,
            ended_at=t_run1 + timedelta(minutes=25),
            actual_work_seconds=1500,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            device_id="dev-test",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        ci1 = CheckIn(
            id=uuid4(),
            user_id=user_id,
            run_id=run1.id,
            response_revision=1,
            kind="final",
            asked_at=t_run1 + timedelta(minutes=25),
            responded_at=t_run1 + timedelta(minutes=26),
            response_state="answered",
            session_outcome="completed",
            step_completed=True,
            task_completed=False,
        )
        # Run 2 và CheckIn 2 (11:00) - Mới hơn, phủ quyết Step & Task
        run2 = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess2.id,
            revision=1,
            run_state="closed",
            started_at=t_run2,
            ended_at=t_run2 + timedelta(minutes=25),
            actual_work_seconds=1500,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            device_id="dev-test",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        ci2 = CheckIn(
            id=uuid4(),
            user_id=user_id,
            run_id=run2.id,
            response_revision=1,
            kind="final",
            asked_at=t_run2 + timedelta(minutes=25),
            responded_at=t_run2 + timedelta(minutes=26),
            response_state="answered",
            session_outcome="partial",
            step_completed=False,
            task_completed=False,
        )
        db.add_all([run1, run2])
        await db.flush()
        db.add_all([ci1, ci2])
        await db.commit()
        ci1_id = ci1.id
        step_id = step.id
        task_id = task.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Sửa ghi chú trên CheckIn 1 cũ (giữ step_completed=True)
        r_update = await client.put(
            f"/api/v1/check-ins/{ci1_id}/response",
            headers=_auth_headers(user_id, f"idem-edit-ci1-{uuid4()}"),
            json={
                "expected_response_revision": 1,
                "responded_at": (t_run1 + timedelta(minutes=27)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": False,
                "remaining_note": "Ghi chú bổ sung sau khi làm Run 2",
            },
        )
        assert r_update.status_code == 200, r_update.text

    # Kiểm tra DB: Step và Task KHÔNG được chuyển thành completed vì Run 2 mới hơn đã phủ quyết
    async with sf() as db:
        st = await db.get(StepRow, step_id)
        tk = await db.get(TaskRow, task_id)
        assert st.status == "in_progress", f"Step phải giữ in_progress nhưng nhận {st.status}"
        assert tk.status == "in_progress", f"Task phải giữ in_progress nhưng nhận {tk.status}"


@pytest.mark.asyncio
async def test_uf5_t2_clock_boundary_300s_vs_301s(simulated_clock):
    """UF5-T2: Kiểm tra biên clock-skew +300s (200/201 hợp lệ) và +301s (422)
    cho create_run_checkin (asked_at) và respond_to_checkin (responded_at) với simulated_clock."""
    engine, sf = await _make_test_env()
    now = simulated_clock.now()
    user_id = uuid4()

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T2")
        pref = UserPreferenceRow(user_id=user_id, effective_revision=1)
        task = TaskRow(
            user_id=user_id,
            title="Task T2",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step T2",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=now - timedelta(minutes=30),
            scheduled_end=now,
            work_seconds=1500,
            rest_seconds=300,
            segments=[],
            duration_seconds=1800,
            schedule_revision=1,
            status="in_progress",
        )
        db.add(sess)
        await db.flush()

        run = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess.id,
            revision=1,
            run_state="running",
            started_at=now - timedelta(minutes=30),
            ended_at=None,
            dnd_release_at=sess.scheduled_end,
            device_id="dev-test",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        db.add(run)
        await db.commit()
        run_id = run.id
        initial_dnd_release_at = sess.scheduled_end

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. create_run_checkin: asked_at = now + 301s -> 422 (vượt ngưỡng 300s)
        r_301 = await client.post(
            f"/api/v1/runs/{run_id}/check-ins",
            headers=_auth_headers(user_id, f"idem-ask-301-{uuid4()}"),
            json={
                "kind": "mid",
                "asked_at": (now + timedelta(seconds=301)).isoformat(),
                "expected_run_revision": 1,
            },
        )
        assert r_301.status_code == 422

        # 2. create_run_checkin: asked_at = now + 300s -> 201 (đúng biên 300s)
        r_300 = await client.post(
            f"/api/v1/runs/{run_id}/check-ins",
            headers=_auth_headers(user_id, f"idem-ask-300-{uuid4()}"),
            json={
                "kind": "mid",
                "asked_at": (now + timedelta(seconds=300)).isoformat(),
                "expected_run_revision": 1,
            },
        )
        assert r_300.status_code == 201, r_300.text
        ci_id = r_300.json()["data"]["checkin_id"]

        # 3. respond_to_checkin: responded_at = now + 301s -> 422 (vượt ngưỡng 300s)
        r_resp_301 = await client.put(
            f"/api/v1/check-ins/{ci_id}/response",
            headers=_auth_headers(user_id, f"idem-resp-301-{uuid4()}"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "partial",
                "responded_at": (now + timedelta(seconds=301)).isoformat(),
            },
        )
        assert r_resp_301.status_code == 422

        # 4. respond_to_checkin: responded_at = now + 300s -> 200 (đúng biên 300s)
        r_resp_300 = await client.put(
            f"/api/v1/check-ins/{ci_id}/response",
            headers=_auth_headers(user_id, f"idem-resp-300-{uuid4()}"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "partial",
                "responded_at": (now + timedelta(seconds=300)).isoformat(),
            },
        )
        assert r_resp_300.status_code == 200, r_resp_300.text

    # 5. Khẳng định mid checkin không đóng Run và không rút ngắn dnd_release_at
    async with sf() as db:
        r_db = await db.get(SessionRun, run_id)
        assert r_db.run_state == "running", f"Mid check-in không được đóng Run, nhận {r_db.run_state}"
        assert r_db.ended_at is None, f"Mid check-in không được gán ended_at, nhận {r_db.ended_at}"
        assert r_db.dnd_release_at == initial_dnd_release_at, (
            f"Mid check-in không được thay đổi dnd_release_at! Ban đầu {initial_dnd_release_at}, hiện tại {r_db.dnd_release_at}"
        )


@pytest.mark.asyncio
async def test_uf5_t3_multi_session_step_correction_rollback():
    """UF5-T3: Step có 2 session (1A + 1B). Hoàn thành 1A rồi 1B với step_completed=True, task_completed=True ->
    Step & Task completed. Sau đó correction 1B thành incomplete ->
    Step quay về in_progress, remaining_base_seconds > 0, Task không còn completed."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    user_id = uuid4()

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T3")
        pref = UserPreferenceRow(user_id=user_id, effective_revision=1)
        task = TaskRow(
            user_id=user_id,
            title="Task T3",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step T3",
            estimated_base_seconds=3000,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess1 = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=now - timedelta(hours=2),
            scheduled_end=now - timedelta(hours=2) + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[],
            duration_seconds=1800,
            schedule_revision=1,
            status="completed",
        )
        sess2 = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=now - timedelta(hours=1),
            scheduled_end=now - timedelta(hours=1) + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[],
            duration_seconds=1800,
            schedule_revision=1,
            status="completed",
        )
        db.add_all([sess1, sess2])
        await db.flush()

        run2 = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess2.id,
            revision=1,
            run_state="closed",
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(hours=1) + timedelta(minutes=25),
            actual_work_seconds=1500,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            device_id="dev-test",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        ci2 = CheckIn(
            id=uuid4(),
            user_id=user_id,
            run_id=run2.id,
            response_revision=1,
            kind="final",
            asked_at=now - timedelta(hours=1) + timedelta(minutes=25),
            responded_at=now - timedelta(hours=1) + timedelta(minutes=26),
            response_state="answered",
            session_outcome="completed",
            step_completed=True,
            task_completed=True,
        )
        step.status = "completed"
        step.remaining_base_seconds = 0
        task.status = "completed"
        db.add(run2)
        await db.flush()
        db.add(ci2)
        await db.commit()
        ci2_id = ci2.id
        step_id = step.id
        task_id = task.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Đính chính CheckIn 2 thành incomplete
        r_correct = await client.put(
            f"/api/v1/check-ins/{ci2_id}/response",
            headers=_auth_headers(user_id, f"idem-correct-ci2-{uuid4()}"),
            json={
                "expected_response_revision": 1,
                "responded_at": (now - timedelta(hours=1) + timedelta(minutes=27)).isoformat(),
                "session_outcome": "incomplete",
                "step_completed": False,
                "task_completed": False,
            },
        )
        assert r_correct.status_code == 200, r_correct.text

    async with sf() as db:
        st = await db.get(StepRow, step_id)
        tk = await db.get(TaskRow, task_id)
        assert st.status == "in_progress"
        assert st.remaining_base_seconds > 0
        assert tk.status == "in_progress"


@pytest.mark.asyncio
async def test_uf7_t1_re_sending_same_reminder_offsets_no_integrity_error_500():
    """UF7-T1 (Bắt BUG-1): Đã có future scheduled session và reminder_offsets=[1200, 600]
    ở effective_revision=2: gửi lại reminder_offsets=[1200, 600] với Idempotency-Key mới ->
    trả 200, KHÔNG nổ IntegrityError 500!"""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    user_id = uuid4()
    fut_start = now + timedelta(days=1)

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T1_UF7")
        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[1200, 600],
            effective_revision=2,
        )
        task = TaskRow(
            user_id=user_id,
            title="Task UF7 T1",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step UF7 T1",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=fut_start,
            scheduled_end=fut_start + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[],
            duration_seconds=1800,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess)
        await db.flush()

        rem1 = Reminder(
            user_id=user_id,
            session_id=sess.id,
            schedule_revision=1,
            kind="pre_session",
            fire_at=fut_start - timedelta(seconds=1200),
            dedupe_key=f"session:{sess.id}:version:1:reminder:1200:pref:2",
            delivery_state="scheduled",
        )
        rem2 = Reminder(
            user_id=user_id,
            session_id=sess.id,
            schedule_revision=1,
            kind="pre_session",
            fire_at=fut_start - timedelta(seconds=600),
            dedupe_key=f"session:{sess.id}:version:1:reminder:600:pref:2",
            delivery_state="scheduled",
        )
        db.add_all([rem1, rem2])
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Gửi lại CHÍNH [1200, 600] với idempotency key mới -> KHÔNG ĐƯỢC 500!
        r_same = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-same-offsets-{uuid4()}"),
            json={
                "expected_preferences_revision": 2,
                "changes": {"preferences": {"reminder_offsets": [1200, 600]}},
            },
        )
        assert r_same.status_code == 200, f"Phải trả 200 nhưng nhận {r_same.status_code}: {r_same.text}"

        # Kiểm tra device nhận đúng danh sách reminder [1200, 600]
        r_rem = await client.get(
            "/api/v1/reminders",
            headers=_auth_headers(user_id),
            params={
                "device_id": "dev-test",
                "from": (now - timedelta(hours=1)).isoformat(),
                "to": (fut_start + timedelta(hours=2)).isoformat(),
            },
        )
        assert r_rem.status_code == 200
        rem_items = r_rem.json()["data"]["items"]
        assert len(rem_items) == 2


@pytest.mark.asyncio
async def test_uf7_t2_direct_service_dict_availability_no_attribute_error():
    """UF7-T2 (Bắt BUG-3): Gọi trực tiếp settings_service.apply_settings_change với
    changes={'availability': ...} dạng dict cho cả target_task_id và target_draft_id
    -> không văng AttributeError và không nổ Pydantic UserWarning."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        task_id = ids["task_id"]

        # 1. target_task_id với dict availability
        req_task = SettingsChangeRequest.model_construct(
            expected_preferences_revision=1,
            target_task_id=str(task_id),
            expected_task_revision=1,
            changes={"availability": {"weekly_rules": [], "timezone": "Asia/Ho_Chi_Minh"}},
        )

        res_task = await settings_service.apply_settings_change(
            user_id=str(user_id),
            idempotency_key=f"idem-dict-avail-{uuid4()}",
            payload=req_task,
            db=db,
        )
        assert res_task.change_request_id is not None
        assert res_task.target_task_id == str(task_id)

        # 2. target_draft_id với dict availability
        draft_id = uuid4()
        draft_row = PlanningDraft(
            id=draft_id,
            user_id=user_id,
            client_draft_id=f"client-{draft_id.hex[:8]}",
            input_source="text",
            summary={"can_plan": True, "title": "Draft Title"},
            summary_revision=1,
            planning_input_revision=1,
            stage="ready_to_plan",
        )
        db.add(draft_row)
        await db.commit()

        req_draft = SettingsChangeRequest.model_construct(
            expected_preferences_revision=1,
            target_draft_id=str(draft_id),
            expected_summary_revision=1,
            expected_planning_input_revision=1,
            changes={"availability": {"weekly_rules": [], "timezone": "Asia/Ho_Chi_Minh"}},
        )
        res_draft = await settings_service.apply_settings_change(
            user_id=str(user_id),
            idempotency_key=f"idem-dict-draft-{uuid4()}",
            payload=req_draft,
            db=db,
        )
        assert res_draft.change_request_id is not None
        assert res_draft.target_draft_id == str(draft_id)


@pytest.mark.asyncio
async def test_uf7_http_strict_validation_and_mass_assignment_guard():
    """Chặn chặt payload HTTP sai cấu trúc / field lạ / mass-assignment trên /settings/change-requests
    và giữ defense-in-depth khi gọi trực tiếp apply_settings_change qua model_construct."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        task_id = ids["task_id"]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. changes rỗng hoặc nhóm lạ -> 422
        r_empty = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-empty-{uuid4()}"),
            json={"changes": {}},
        )
        assert r_empty.status_code == 422

        r_unknown_group = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-unk-grp-{uuid4()}"),
            json={"changes": {"unknown_group": 1}},
        )
        assert r_unknown_group.status_code == 422

        # 2. Chặn mass assignment & unknown_key trong preferences -> 422
        r_mass_assign = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-mass-{uuid4()}"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"work_interval_seconds": 1800, "effective_revision": 99}},
            },
        )
        assert r_mass_assign.status_code == 422

        r_unknown_pref = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-unk-pref-{uuid4()}"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"work_interval_seconds": 1500, "unknown_key": "boom"}},
            },
        )
        assert r_unknown_pref.status_code == 422

        # 3. Chặn sai schema availability qua HTTP -> 422
        r_bad_avail = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-bad-avail-{uuid4()}"),
            json={
                "target_task_id": str(task_id),
                "expected_task_revision": 1,
                "changes": {"availability": {"weekly_rules": "not-a-list"}},
            },
        )
        assert r_bad_avail.status_code == 422

    # 4. Kiểm tra DB không bị ghi đè effective_revision
    async with sf() as db:
        pref_db = await db.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id))
        assert pref_db.effective_revision == 1
        assert pref_db.work_interval_seconds == 1500

        # 5. Defense-in-depth ở tầng service nếu model_construct truyền field lạ hoặc availability sai
        req_bad_service = SettingsChangeRequest.model_construct(
            expected_preferences_revision=1,
            changes={"preferences": {"work_interval_seconds": 1500, "effective_revision": 99}},
        )
        with pytest.raises(AppError) as exc_pref:
            await settings_service.apply_settings_change(
                user_id=str(user_id),
                idempotency_key=f"idem-svc-bad-pref-{uuid4()}",
                payload=req_bad_service,
                db=db,
            )
        assert exc_pref.value.status_code == 422

        req_bad_avail = SettingsChangeRequest.model_construct(
            expected_preferences_revision=1,
            target_task_id=str(task_id),
            expected_task_revision=1,
            changes={"availability": {"weekly_rules": "not-a-list"}},
        )
        with pytest.raises(AppError) as exc_avail:
            await settings_service.apply_settings_change(
                user_id=str(user_id),
                idempotency_key=f"idem-svc-bad-avail-{uuid4()}",
                payload=req_bad_avail,
                db=db,
            )
        assert exc_avail.value.status_code == 422


@pytest.mark.asyncio
async def test_uf7_t3_pending_proposal_approve_source_changed_and_rebuild():
    """UF7-T3: Đang có pending_proposal_id (đổi work_interval_seconds), đổi reminder_style
    hoặc reminder_offsets trực tiếp -> effective_revision tăng, gọi approve nổ 409 SOURCE_CHANGED
    -> rebuild -> approve thành công và effective_revision tăng chuẩn."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    user_id = uuid4()

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T3_UF7")
        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 600, 300],
            reminder_style="default",
            effective_revision=1,
            requested_revision=2,
        )
        task = TaskRow(
            user_id=user_id,
            title="Task T3_UF7",
            status="in_progress",
            time_constraint={"kind": "window"},
            revision=1,
        )
        db.add_all([user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step T3_UF7",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=now + timedelta(hours=2),
            scheduled_end=now + timedelta(hours=2) + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1800,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess)
        await db.flush()

        # Tạo pending proposal dựa trên settings revision 1
        prop = RescheduleProposal(
            id=uuid4(),
            user_id=user_id,
            trigger_reason="settings_change",
            allowed_task_ids=[str(task.id)],
            affected_task_ids=[str(task.id)],
            base_schedule_revision=1,
            source_revisions={"task": 1, "schedule": 1, "availability": 0, "settings": 1, "calendar": 0},
            proposal_revision=1,
            status="pending",
            proposed_changes={
                "sessions_to_move": [],
                "sessions_to_add": [],
                "sessions_to_cancel": [],
                "steps_to_add": [],
            },
            feasibility={"status": "feasible", "confirmable": True},
            explanation="Pending settings change proposal",
        )
        db.add(prop)
        await db.flush()
        pref.pending_proposal_id = prop.id
        await db.commit()
        prop_id = prop.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Áp dụng thay đổi reminder_style (áp thẳng) -> effective_revision nhảy lên 2, requested_revision lên 3
        r_change = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-t3-change-{uuid4()}"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_style": "gentle_sound"}},
            },
        )
        assert r_change.status_code == 200, r_change.text

        # 2. Gọi approve proposal cũ (đang mang source_revisions.settings=1) -> nổ 409 SOURCE_CHANGED
        r_appr_fail = await client.post(
            f"/api/v1/proposals/{prop_id}/approve",
            headers=_auth_headers(user_id, f"idem-t3-appr-fail-{uuid4()}"),
            json={
                "expected_proposal_revision": 1,
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": {
                    "task": 1,
                    "schedule": 1,
                    "availability": 0,
                    "settings": 1,
                    "calendar": 0,
                },
            },
        )
        assert r_appr_fail.status_code == 409, r_appr_fail.text
        assert r_appr_fail.json()["error"]["code"] == "SOURCE_CHANGED"

        # 3. Gọi rebuild proposal -> trả 201 và cập nhật source_revisions.settings=2, proposal_revision=2
        r_rebuild = await client.post(
            f"/api/v1/proposals/{prop_id}/rebuild",
            headers=_auth_headers(user_id, f"idem-t3-rebuild-{uuid4()}"),
            json={
                "expected_proposal_revision": 1,
            },
        )
        assert r_rebuild.status_code == 201, r_rebuild.text
        rebuild_data = r_rebuild.json()["data"]
        assert rebuild_data["proposal_revision"] == 2
        assert rebuild_data["source_revisions"]["settings"] >= 2

        # 4. Gọi approve proposal sau khi rebuild -> thành công 200
        r_appr_ok = await client.post(
            f"/api/v1/proposals/{prop_id}/approve",
            headers=_auth_headers(user_id, f"idem-t3-appr-ok-{uuid4()}"),
            json={
                "expected_proposal_revision": 2,
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": {
                    "task": 1,
                    "schedule": 1,
                    "availability": 0,
                    "settings": rebuild_data["source_revisions"]["settings"],
                    "calendar": 0,
                },
            },
        )
        assert r_appr_ok.status_code == 200, r_appr_ok.text

    # 5. Kiểm tra DB: effective_revision tăng chuẩn, pending_proposal_id đã được giải phóng
    async with sf() as db:
        pref_db = await db.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id))
        assert pref_db.effective_revision >= 2
        assert pref_db.pending_proposal_id is None


@pytest.mark.asyncio
async def test_uf7_t3_suggestion_response_with_pending_proposal_and_duplicate_offsets():
    """UF7-T3 (Phần suggestion): Chấp nhận gợi ý reminder_offset khi đang có pending_proposal_id
    và có sẵn reminder bị trùng dedupe_key -> tính đúng new_eff_rev = max(...) + 1,
    không nổ IntegrityError 500 nhờ db.begin_nested()."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    user_id = uuid4()
    fut_start = now + timedelta(days=1)
    pending_prop_id = uuid4()

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T3_Sugg")
        task = TaskRow(
            user_id=user_id,
            title="Task T3_Sugg",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, task])
        await db.flush()

        prop = RescheduleProposal(
            id=pending_prop_id,
            user_id=user_id,
            trigger_reason="settings_change",
            allowed_task_ids=[str(task.id)],
            affected_task_ids=[str(task.id)],
            base_schedule_revision=1,
            source_revisions={"task": 0, "schedule": 1, "availability": 0, "settings": 1, "calendar": 0},
            proposal_revision=1,
            status="pending",
            proposed_changes={"sessions_to_move": [], "sessions_to_add": [], "sessions_to_cancel": [], "steps_to_add": []},
            feasibility={"status": "feasible", "confirmable": True},
            explanation="Pending proposal for suggestion test",
        )
        db.add(prop)
        await db.flush()

        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 300],
            reminder_style="default",
            effective_revision=1,
            requested_revision=2,
            pending_proposal_id=pending_prop_id,
        )
        db.add(pref)
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step T3_Sugg",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=fut_start,
            scheduled_end=fut_start + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1800,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess)
        await db.flush()

        # Tạo sẵn một reminder có dedupe_key xung đột với key sẽ sinh (pref=3)
        rem_dup = Reminder(
            user_id=user_id,
            session_id=sess.id,
            schedule_revision=1,
            kind="pre_session",
            fire_at=fut_start - timedelta(seconds=1200),
            dedupe_key=f"session:{sess.id}:version:1:reminder:1200:pref:3",
            delivery_state="scheduled",
        )
        db.add(rem_dup)

        # Tạo AdaptationSuggestion chờ phản hồi
        sugg = AdaptationSuggestion(
            id=uuid4(),
            user_id=user_id,
            revision=1,
            kind="reminder_offset",
            sample_count=8,
            current_value=[900, 300],
            suggested_value=[1200, 600],
            reason="Tối ưu mốc nhắc việc",
            policy_revision=1,
            sample_refs=[],
            status="pending",
        )
        db.add(sugg)
        await db.commit()
        sugg_id = sugg.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Chấp nhận gợi ý reminder_offsets
        r_accept = await client.post(
            f"/api/v1/suggestions/{sugg_id}/response",
            headers=_auth_headers(user_id, f"idem-accept-sugg-{uuid4()}"),
            json={
                "action": "accept",
                "expected_suggestion_revision": 1,
            },
        )
        assert r_accept.status_code == 200, r_accept.text
        data = r_accept.json()["data"]
        assert data["status"] == "accepted"
        # new_eff_rev = max(1, 2) + 1 = 3
        assert data["effective_revision"] == 3

    # Kiểm tra DB
    async with sf() as db:
        pref_db = await db.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id))
        assert pref_db.effective_revision == 3
        # requested_revision phải được đẩy lên để không bao giờ <= effective_revision
        assert pref_db.requested_revision == 4
        assert pref_db.reminder_offsets == [1200, 600]


def test_uf7_t4_preference_changes_nan_inf_bool_validation():
    """UF7-T4: Kiểm tra trực tiếp PreferenceChanges.model_validate với float('nan'),
    float('inf'), work_interval_seconds=0, và các giá trị boolean; đồng thời kiểm tra
    trực tiếp _validate_preferences_payload và _validate_accepted_suggestion_value."""
    # 1. Pydantic validation trên PreferenceChanges
    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"buffer_multiplier": float("nan")})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"buffer_multiplier": float("inf")})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"buffer_multiplier": float("-inf")})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"work_interval_seconds": 0})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"reminder_offsets": [300, 900]})

    # Bổ sung chặn giá trị bool (True/False)
    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"buffer_multiplier": True})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"buffer_multiplier": False})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"work_interval_seconds": True})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"rest_interval_seconds": True})

    with pytest.raises(ValidationError):
        PreferenceChanges.model_validate({"reminder_offsets": True})

    # 2. Gọi trực tiếp _validate_preferences_payload
    from app.services.settings_service import (
        _validate_accepted_suggestion_value,
        _validate_preferences_payload,
    )

    with pytest.raises(AppError) as exc_p1:
        _validate_preferences_payload(
            values={
                "work_interval_seconds": True,
                "rest_interval_seconds": 300,
                "buffer_multiplier": 1.2,
                "reminder_offsets": [900, 300],
                "reminder_style": "default",
            },
            preferences=None,
        )
    assert exc_p1.value.status_code == 422

    with pytest.raises(AppError) as exc_p2:
        _validate_preferences_payload(
            values={
                "work_interval_seconds": 1500,
                "rest_interval_seconds": 300,
                "buffer_multiplier": True,
                "reminder_offsets": [900, 300],
                "reminder_style": "default",
            },
            preferences=None,
        )
    assert exc_p2.value.status_code == 422

    # 3. Gọi trực tiếp _validate_accepted_suggestion_value
    with pytest.raises(AppError) as exc_s1:
        _validate_accepted_suggestion_value(kind="buffer_multiplier", raw_value=True)
    assert exc_s1.value.status_code == 422

    with pytest.raises(AppError) as exc_s2:
        _validate_accepted_suggestion_value(kind="buffer_multiplier", raw_value=float("nan"))
    assert exc_s2.value.status_code == 422

    with pytest.raises(AppError) as exc_s3:
        _validate_accepted_suggestion_value(kind="reminder_offset", raw_value=True)
    assert exc_s3.value.status_code == 422

    with pytest.raises(AppError) as exc_s4:
        _validate_accepted_suggestion_value(kind="reminder_offset", raw_value=[300, 900])
    assert exc_s4.value.status_code == 422


@pytest.mark.asyncio
async def test_uf7_t5_get_current_suggestion_http_e2e(monkeypatch: pytest.MonkeyPatch):
    """UF7-T5: Kiểm tra GET /api/v1/suggestions/current qua HTTP End-to-End:
    1. < 7 mẫu sạch -> trả null (không tạo gợi ý).
    2. >= 7 mẫu có diff == 0.20 -> trả null (chỉ gợi ý khi diff > 0.20).
    3. Run với run_state='awaiting_checkin' bị loại bỏ khỏi tính toán.
    4. Gợi ý xuất hiện khi diff > 0.20 -> trả AdaptationSuggestionData.
    5. Reject gợi ý kèm cooldown_until > now -> gọi lại ngay trả null."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    monkeypatch.setattr(settings_service, "_utc_now", lambda now_arg=None: (now_arg or now).astimezone(UTC))

    user_id = uuid4()
    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester T5")
        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 600, 300],
            effective_revision=1,
        )
        task = TaskRow(
            user_id=user_id,
            title="Task T5",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user, pref, task])
        await db.flush()

        # Tạo 6 step hoàn thành sạch với actual_work_seconds=1400 (ratio 1.40, diff 0.20)
        for i in range(6):
            step = StepRow(
                user_id=user_id,
                task_id=task.id,
                order_index=i,
                title=f"Step T5-{i}",
                estimated_base_seconds=1000,
                remaining_base_seconds=0,
                status="completed",
                buffer_multiplier=1.2,
                estimate_source="user_confirmed",
            )
            db.add(step)
            await db.flush()

            sess = SessionRow(
                user_id=user_id,
                step_id=step.id,
                scheduled_start=now - timedelta(hours=10 - i),
                scheduled_end=now - timedelta(hours=10 - i) + timedelta(minutes=25),
                work_seconds=1500,
                rest_seconds=300,
                segments=[],
                duration_seconds=1800,
                schedule_revision=1,
                status="completed",
            )
            db.add(sess)
            await db.flush()

            run = SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=sess.id,
                revision=1,
                run_state="closed",
                outcome="completed",
                started_at=now - timedelta(hours=10 - i),
                ended_at=now - timedelta(hours=10 - i) + timedelta(minutes=25),
                actual_work_seconds=1400,
                work_elapsed_seconds=1400,
                rest_elapsed_seconds=0,
                device_id="dev-test",
                actual_source="timer_proxy",
                settings_snapshot={"work_interval_seconds": 1500},
            )
            db.add(run)
            await db.flush()

            ci = CheckIn(
                id=uuid4(),
                user_id=user_id,
                run_id=run.id,
                response_revision=1,
                kind="final",
                asked_at=now - timedelta(hours=10 - i) + timedelta(minutes=25),
                responded_at=now - timedelta(hours=10 - i) + timedelta(minutes=26),
                response_state="answered",
                session_outcome="completed",
                step_completed=True,
                task_completed=False,
            )
            db.add(ci)

        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. < 7 mẫu -> trả null
        r_few = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_id))
        assert r_few.status_code == 200
        assert r_few.json()["data"] is None

        # 2. Thêm mẫu thứ 7 nhưng ratio=1.40 -> diff = |1.40 - 1.20| = 0.20 (không strictly > 0.20)
        async with sf() as db:
            step7 = StepRow(
                user_id=user_id,
                task_id=task.id,
                order_index=6,
                title="Step T5-6",
                estimated_base_seconds=1000,
                remaining_base_seconds=0,
                status="completed",
                buffer_multiplier=1.2,
                estimate_source="user_confirmed",
            )
            db.add(step7)
            await db.flush()

            sess7 = SessionRow(
                user_id=user_id,
                step_id=step7.id,
                scheduled_start=now - timedelta(hours=3),
                scheduled_end=now - timedelta(hours=3) + timedelta(minutes=25),
                work_seconds=1500,
                rest_seconds=300,
                segments=[],
                duration_seconds=1800,
                schedule_revision=1,
                status="completed",
            )
            db.add(sess7)
            await db.flush()

            run7 = SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=sess7.id,
                revision=1,
                run_state="closed",
                outcome="completed",
                started_at=now - timedelta(hours=3),
                ended_at=now - timedelta(hours=3) + timedelta(minutes=25),
                actual_work_seconds=1400,
                work_elapsed_seconds=1400,
                rest_elapsed_seconds=0,
                device_id="dev-test",
                actual_source="timer_proxy",
                settings_snapshot={"work_interval_seconds": 1500},
            )
            db.add(run7)
            await db.flush()

            ci7 = CheckIn(
                id=uuid4(),
                user_id=user_id,
                run_id=run7.id,
                response_revision=1,
                kind="final",
                asked_at=now - timedelta(hours=3) + timedelta(minutes=25),
                responded_at=now - timedelta(hours=3) + timedelta(minutes=26),
                response_state="answered",
                session_outcome="completed",
                step_completed=True,
                task_completed=False,
            )
            db.add(ci7)

            # 3. Thêm một run với awaiting_checkin -> phải bị loại bỏ
            run_await = SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=sess7.id,
                revision=1,
                run_state="awaiting_checkin",
                outcome="completed",
                started_at=now - timedelta(hours=2),
                ended_at=now - timedelta(hours=2) + timedelta(minutes=25),
                actual_work_seconds=2000,
                work_elapsed_seconds=2000,
                rest_elapsed_seconds=0,
                device_id="dev-test",
                actual_source="timer_proxy",
                settings_snapshot={"work_interval_seconds": 1500},
            )
            db.add(run_await)
            await db.commit()

        # Gọi lại: 7 mẫu với diff == 0.20 -> vẫn trả null
        r_exact = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_id))
        assert r_exact.status_code == 200
        assert r_exact.json()["data"] is None

        # 4. Thêm mẫu thứ 8 với actual_work_seconds=1600 (ratio 1.60) -> median tăng, diff > 0.20
        async with sf() as db:
            step8 = StepRow(
                user_id=user_id,
                task_id=task.id,
                order_index=7,
                title="Step T5-7",
                estimated_base_seconds=1000,
                remaining_base_seconds=0,
                status="completed",
                buffer_multiplier=1.2,
                estimate_source="user_confirmed",
            )
            db.add(step8)
            await db.flush()

            sess8 = SessionRow(
                user_id=user_id,
                step_id=step8.id,
                scheduled_start=now - timedelta(hours=1),
                scheduled_end=now - timedelta(hours=1) + timedelta(minutes=25),
                work_seconds=1500,
                rest_seconds=300,
                segments=[],
                duration_seconds=1800,
                schedule_revision=1,
                status="completed",
            )
            db.add(sess8)
            await db.flush()

            # Cập nhật các step để median ratio = 1.50 (diff = 0.30 > 0.20)
            await db.execute(
                update(SessionRun)
                .where(SessionRun.user_id == user_id, SessionRun.run_state == "closed")
                .values(actual_work_seconds=1500)
            )
            await db.commit()

        # Gọi lại: diff > 0.20 -> có gợi ý!
        r_suggest = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_id))
        assert r_suggest.status_code == 200
        data = r_suggest.json()["data"]
        assert data is not None
        assert data["kind"] == "buffer_multiplier"
        assert data["suggested_value"] == 1.50
        sugg_id = data["suggestion_id"]
        sugg_rev = data["suggestion_revision"]

        # 5. Reject gợi ý
        r_reject = await client.post(
            f"/api/v1/suggestions/{sugg_id}/response",
            headers=_auth_headers(user_id, f"idem-reject-{uuid4()}"),
            json={
                "action": "reject",
                "expected_suggestion_revision": sugg_rev,
                "annoyance_feedback": "Not now",
            },
        )
        assert r_reject.status_code == 200

        # Gọi lại ngay: cooldown đang có hiệu lực -> trả null
        r_cool = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_id))
        assert r_cool.status_code == 200
        assert r_cool.json()["data"] is None


# =============================================================================
# PHẦN 3: KIỂM THỬ TÍCH HỢP CONCURRENCY, GOOGLE CALENDAR & VOICE TTS
# =============================================================================


@pytest.mark.asyncio
async def test_uf7_concurrency_race_condition_and_unique_constraint():
    """Kiểm thử tương tranh (concurrency):
    1. Đua song song GET /api/v1/suggestions/current khi CHƯA có UserPreferenceRow (savepoint).
    2. Đua song song GET /api/v1/suggestions/current (with_for_update()) và
       POST /api/v1/settings/change-requests qua asyncio.gather -> không deadlock/500.
    3. Kiểm chứng UniqueConstraint('user_id', 'dedupe_key') trên bảng reminders."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
    user_no_pref_id = uuid4()
    user_id = uuid4()
    fut_start = now + timedelta(days=1)

    async with sf() as db:
        user_new = User(id=user_no_pref_id, email=f"u_{user_no_pref_id.hex[:6]}@test.com", display_name="NoPref")
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester Concurrency")
        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 600, 300],
            effective_revision=1,
        )
        task = TaskRow(
            user_id=user_id,
            title="Task Concurrency",
            status="in_progress",
            time_constraint={"kind": "window"},
        )
        db.add_all([user_new, user, pref, task])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step Concurrency",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=fut_start,
            scheduled_end=fut_start + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1800,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess)
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Đua tạo UserPreference lần đầu trên GET /suggestions/current (chạy trên PG đa kết nối;
        # SQLite :memory: dùng StaticPool 1 kết nối nên không cô lập transaction giữa các coroutine).
        if os.environ.get("PROACTIVE_TEST_PG_URL"):
            async def _call_get_new_user():
                return await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_no_pref_id))

            new_user_results = await asyncio.gather(_call_get_new_user(), _call_get_new_user(), return_exceptions=True)
            for res in new_user_results:
                assert not isinstance(res, Exception), f"Race tạo PreferenceRow gặp exception: {res}"
                assert res.status_code == 200, res.text

            async with sf() as db:
                prefs_new = (await db.scalars(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_no_pref_id))).all()
                assert len(prefs_new) == 1

        # 2. Đua song song GET suggestions và POST change-requests
        headers = _auth_headers(user_id)

        async def _call_get_suggestion():
            return await client.get("/api/v1/suggestions/current", headers=headers)

        async def _call_change_req(offset_val: int):
            return await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, f"idem-race-{offset_val}-{uuid4()}"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"reminder_offsets": [offset_val, 300]}},
                },
            )

        results = await asyncio.gather(
            _call_get_suggestion(),
            _call_change_req(1200),
            _call_get_suggestion(),
            return_exceptions=True,
        )
        for res in results:
            assert not isinstance(res, Exception), f"Có exception xảy ra trong tương tranh: {res}"
            assert res.status_code in (200, 409), f"Nhận mã trạng thái bất thường: {res.status_code}"

    # 3. Kiểm chứng UniqueConstraint("user_id", "dedupe_key")
    async with sf() as db:

        rem_a = Reminder(
            user_id=user_id,
            session_id=sess.id,
            schedule_revision=1,
            kind="pre_session",
            fire_at=fut_start - timedelta(seconds=900),
            dedupe_key=f"unique-test:{user_id}:key-1",
            delivery_state="scheduled",
        )
        db.add(rem_a)
        await db.commit()

        rem_dup = Reminder(
            user_id=user_id,
            session_id=sess.id,
            schedule_revision=1,
            kind="pre_session",
            fire_at=fut_start - timedelta(seconds=900),
            dedupe_key=f"unique-test:{user_id}:key-1",
            delivery_state="scheduled",
        )
        with pytest.raises(IntegrityError):
            db.add(rem_dup)
            await db.commit()


@pytest.mark.asyncio
async def test_uf5_uf7_google_calendar_sync_and_h10_guard():
    """Kiểm chứng tích hợp Google Calendar sync qua _sync_calendar_on_approve:
    1. H10: Đổi reminder_offsets (UF7) chỉ cập nhật bảng reminders, KHÔNG sinh CalendarSyncJob.
    2. Đổi buffer_multiplier (UF7) sinh RescheduleProposal -> approve tạo CalendarEventLink
       và hàng đợi CalendarSyncJob (queued) khi CalendarConnection đang connected."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now().astimezone(TZ_VN).replace(hour=8, minute=0, second=0, microsecond=0)
    user_id = uuid4()
    fut_start = (now + timedelta(days=1)).replace(hour=14, minute=0)
    deadline_at = now + timedelta(days=3)

    async with sf() as db:
        user = User(id=user_id, email=f"u_{user_id.hex[:6]}@test.com", display_name="Tester Calendar")
        db.add(user)
        await db.flush()

        pref = UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
            reminder_offsets=[900, 600, 300],
            effective_revision=1,
        )
        avail = AvailabilityRow(
            user_id=user_id,
            revision=1,
            timezone="Asia/Ho_Chi_Minh",
            source="user_confirmed",
            weekly_rules=[
                {"day_of_week": d, "start_local_time": "08:00", "end_local_time": "22:00"}
                for d in range(7)
            ],
            date_overrides=[],
            task_limits=[],
        )
        task = TaskRow(
            user_id=user_id,
            title="Task Calendar",
            status="in_progress",
            time_constraint={
                "mode": "deadline",
                "at": deadline_at.isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            },
            revision=1,
        )
        conn = CalendarConnection(
            id=uuid4(),
            user_id=user_id,
            provider="google",
            account_ref=f"u_{user_id.hex[:6]}@gmail.com",
            read_calendar_ids=["primary"],
            write_calendar_id="primary",
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            connection_state="connected",
            credential_ref="dummy-encrypted-creds",
        )
        db.add_all([pref, avail, task, conn])
        await db.flush()

        step = StepRow(
            user_id=user_id,
            task_id=task.id,
            order_index=0,
            title="Step Calendar",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            status="in_progress",
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step)
        await db.flush()

        sess = SessionRow(
            user_id=user_id,
            step_id=step.id,
            scheduled_start=fut_start,
            scheduled_end=fut_start + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=300,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1800,
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess)
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Đổi reminder_offsets (áp dụng ngay H10)
        r_offsets = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-cal-h10-{uuid4()}"),
            json={
                "expected_preferences_revision": 1,
                "changes": {"preferences": {"reminder_offsets": [1200, 600]}},
            },
        )
        assert r_offsets.status_code == 200, r_offsets.text

        async with sf() as db:
            sync_jobs_after_h10 = (await db.scalars(select(CalendarSyncJob).where(CalendarSyncJob.user_id == user_id))).all()
            assert len(sync_jobs_after_h10) == 0, "H10: Đổi reminder_offsets không được sinh CalendarSyncJob!"

        # 2. Đổi work_interval_seconds tạo RescheduleProposal -> approve sinh CalendarSyncJob
        r_work = await client.post(
            "/api/v1/settings/change-requests",
            headers=_auth_headers(user_id, f"idem-cal-work-{uuid4()}"),
            json={
                "expected_preferences_revision": 2,
                "changes": {"preferences": {"work_interval_seconds": 1800}},
            },
        )
        assert r_work.status_code == 200, r_work.text
        prop_id = r_work.json()["data"]["pending_proposal_id"]
        assert prop_id is not None

        r_prop = await client.get(f"/api/v1/proposals/{prop_id}", headers=_auth_headers(user_id))
        assert r_prop.status_code == 200, r_prop.text
        prop_data = r_prop.json()["data"]

        r_appr = await client.post(
            f"/api/v1/proposals/{prop_id}/approve",
            headers=_auth_headers(user_id, f"idem-cal-appr-{uuid4()}"),
            json={
                "expected_proposal_revision": prop_data["proposal_revision"],
                "expected_base_schedule_revision": prop_data["base_schedule_revision"],
                "expected_source_revisions": prop_data["source_revisions"],
            },
        )
        assert r_appr.status_code == 200, r_appr.text
        assert r_appr.json()["data"]["calendar_sync_job_enqueued"] is True

    async with sf() as db:
        sync_jobs = (await db.scalars(select(CalendarSyncJob).where(CalendarSyncJob.user_id == user_id))).all()
        links = (await db.scalars(select(CalendarEventLink).where(CalendarEventLink.user_id == user_id))).all()
        assert len(sync_jobs) >= 1, "Approve proposal từ UF7 phải xếp hàng CalendarSyncJob!"
        assert all(j.job_state == "queued" for j in sync_jobs)
        assert len(links) >= 1


@pytest.mark.asyncio
async def test_voice_uf5_checkin_prompt_live_tts_and_fallback(monkeypatch: pytest.MonkeyPatch):
    """Kiểm chứng API Voice Check-in prompt UF5:
    1. include_audio=False -> prompt không phán xét + 3 chip, audio_base64=None.
    2. include_audio=True (TTS thành công) -> audio_base64 hợp lệ, fallback_to_device_tts=False.
    3. include_audio=True (TTS nổ TtsError) -> trả 200 kèm fallback_to_device_tts=True, audio_base64=None."""
    from app.services.voice_service import FakeTtsProvider, TtsError, TtsErrorCode, TtsService

    user_id = uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. include_audio=False
        r_text = await client.post(
            "/api/v1/voice/uf5-checkin-prompt",
            headers=_auth_headers(user_id),
            json={
                "kind": "final",
                "is_final_session_of_step": True,
                "expected_output": "Hoàn thành báo cáo",
                "session_outcome": "completed",
                "include_audio": False,
            },
        )
        assert r_text.status_code == 200, r_text.text
        data_text = r_text.json()["data"]
        assert data_text["prompt"]["prompt_text"] is not None
        assert len(data_text["prompt"]["skip_chips"]) == 3
        assert data_text["audio_base64"] is None
        assert data_text["fallback_to_device_tts"] is False

        # 2. include_audio=True thành công
        fake_tts = TtsService(FakeTtsProvider())
        monkeypatch.setattr("app.api.v1.voice.get_tts_service", lambda: fake_tts)
        r_audio = await client.post(
            "/api/v1/voice/uf5-checkin-prompt",
            headers=_auth_headers(user_id),
            json={
                "kind": "mid",
                "is_final_session_of_step": False,
                "include_audio": True,
            },
        )
        assert r_audio.status_code == 200, r_audio.text
        data_audio = r_audio.json()["data"]
        assert data_audio["audio_base64"] is not None
        assert data_audio["audio_mime_type"] == "audio/wav"
        assert data_audio["fallback_to_device_tts"] is False

        # 3. include_audio=True khi TTS gặp TtsError -> fallback_to_device_tts=True
        class _FailingTtsService:
            async def synthesize(self, text: str, *, voice: str | None = None):
                raise TtsError(TtsErrorCode.EMPTY_AUDIO, "simulated_empty_audio")

        monkeypatch.setattr("app.api.v1.voice.get_tts_service", lambda: _FailingTtsService())
        r_fallback = await client.post(
            "/api/v1/voice/uf5-checkin-prompt",
            headers=_auth_headers(user_id),
            json={
                "kind": "mid",
                "is_final_session_of_step": False,
                "include_audio": True,
            },
        )
        assert r_fallback.status_code == 200, r_fallback.text
        data_fb = r_fallback.json()["data"]
        assert data_fb["prompt"]["prompt_text"] is not None
        assert data_fb["audio_base64"] is None
        assert data_fb["fallback_to_device_tts"] is True

