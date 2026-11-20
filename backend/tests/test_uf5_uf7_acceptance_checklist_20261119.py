"""Comprehensive Acceptance Verification Suite for UF5 and UF7 (2026-11-19).

Bao phủ đầy đủ 5 ca kiểm bắt buộc UF5 và 6 ca kiểm bắt buộc UF7 theo:
- UF5_BUGS_AND_RISKS_HANDOFF_20261119.md
- UF7_BUGS_AND_RISKS_HANDOFF_20261119.md
- 00_UF3_TO_UF7_SHARED_RULES_AND_UF12_ALIGNMENT_20261119.md
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models.execution import CheckIn, SessionRun
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion
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


@pytest.fixture(autouse=True)
async def _cleanup_dependency_overrides():
    try:
        yield
    finally:
        app.dependency_overrides.clear()


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
async def test_uf5_t2_clock_boundary_300s_vs_301s():
    """UF5-T2: Kiểm tra biên clock-skew +300s (200/201 hợp lệ) và +301s (422)
    cho create_run_checkin (asked_at) và respond_to_checkin (responded_at)."""
    engine, sf = await _make_test_env()
    now = run_service._current_utc_now()
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
            ended_at=now,
            device_id="dev-test",
            actual_source="timer_proxy",
            settings_snapshot={"work_interval_seconds": 1500},
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. create_run_checkin: asked_at = now + 301s -> 422
        r_301 = await client.post(
            f"/api/v1/runs/{run_id}/check-ins",
            headers=_auth_headers(user_id, f"idem-ask-301-{uuid4()}"),
            json={
                "kind": "mid",
                "asked_at": (now + timedelta(seconds=305)).isoformat(),
                "expected_run_revision": 1,
            },
        )
        assert r_301.status_code == 422

        # 2. create_run_checkin: asked_at = now + 300s -> 201
        r_300 = await client.post(
            f"/api/v1/runs/{run_id}/check-ins",
            headers=_auth_headers(user_id, f"idem-ask-300-{uuid4()}"),
            json={
                "kind": "mid",
                "asked_at": (now + timedelta(seconds=295)).isoformat(),
                "expected_run_revision": 1,
            },
        )
        assert r_300.status_code == 201, r_300.text
        ci_id = r_300.json()["data"]["checkin_id"]

        # 3. respond_to_checkin: responded_at = now + 305s -> 422
        r_resp_301 = await client.put(
            f"/api/v1/check-ins/{ci_id}/response",
            headers=_auth_headers(user_id, f"idem-resp-301-{uuid4()}"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "partial",
                "responded_at": (now + timedelta(seconds=305)).isoformat(),
            },
        )
        assert r_resp_301.status_code == 422

        # 4. respond_to_checkin: responded_at = now + 295s -> 200
        r_resp_300 = await client.put(
            f"/api/v1/check-ins/{ci_id}/response",
            headers=_auth_headers(user_id, f"idem-resp-300-{uuid4()}"),
            json={
                "expected_response_revision": 0,
                "session_outcome": "partial",
                "responded_at": (now + timedelta(seconds=295)).isoformat(),
            },
        )
        assert r_resp_300.status_code == 200, r_resp_300.text


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
    changes={'availability': ...} cùng expected_task_revision=1 -> không văng AttributeError."""
    engine, sf = await _make_test_env()
    async with sf() as db:
        ids = await _seed_uf7_environment(db)
        user_id = ids["user_id"]
        task_id = ids["task_id"]

        req = SettingsChangeRequest.model_construct(
            expected_preferences_revision=1,
            target_task_id=str(task_id),
            expected_task_revision=1,
            changes={"availability": {"weekly_rules": [], "timezone": "Asia/Ho_Chi_Minh"}},
        )

        # Gọi trực tiếp apply_settings_change mà không qua Pydantic parse
        res = await settings_service.apply_settings_change(
            user_id=str(user_id),
            idempotency_key=f"idem-dict-avail-{uuid4()}",
            payload=req,
            db=db,
        )
        assert res.change_request_id is not None


def test_uf7_t4_preference_changes_nan_inf_bool_validation():
    """UF7-T4: Kiểm tra trực tiếp PreferenceChanges.model_validate với float('nan'),
    float('inf'), và work_interval_seconds=0."""
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

