"""Boundary tests A20 (7d/14d cooldown, sliding window, conflicts) và E2E seed W4-12B."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db
from app.core.database import make_engine, session_factory
from app.core.security import issue_app_access_token
from app.main import app
from app.models import (
    ActualCorrection,
    AdaptationSuggestion,
    AuditEvent,
    Availability,
    Base,
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
    CheckIn,
    CommandReceipt,
    DeviceState,
    PlanCandidate,
    PlanningContext,
    PlanningDraft,
    RefreshToken,
    Reminder,
    RescheduleProposal,
    RunEvent,
    ScheduleVersion,
    Session,
    SessionRun,
    SettingChangeRequest,
    Step,
    Task,
    Thought,
    User,
    UserPreference,
)
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.common import TimeConstraint
from app.schemas.draft import TaskSummary
from app.services import reschedule_service, settings_service
from scripts import seed_w4_a20_cases, seed_w4_qa

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _bearer(user_id: UUID, idem_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {issue_app_access_token(user_id).access_token}"}
    if idem_key is not None:
        headers["Idempotency-Key"] = idem_key
    return headers


async def _purge_pg_users(
    factory: async_sessionmaker[AsyncSession],
    user_ids: list[UUID],
) -> None:
    if not user_ids:
        return
    async with factory() as db:
        await db.execute(
            update(UserPreference)
            .where(UserPreference.user_id.in_(user_ids))
            .values(pending_proposal_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(DeviceState)
            .where(DeviceState.user_id.in_(user_ids))
            .values(active_run_id=None, dnd_owner_run_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(PlanningDraft)
            .where(PlanningDraft.user_id.in_(user_ids))
            .values(source_thought_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(AdaptationSuggestion)
            .where(AdaptationSuggestion.user_id.in_(user_ids))
            .values(supersedes_suggestion_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(RescheduleProposal)
            .where(RescheduleProposal.user_id.in_(user_ids))
            .values(supersedes_proposal_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(CalendarPreview)
            .where(CalendarPreview.user_id.in_(user_ids))
            .values(required_proposal_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.flush()

        for model in (
            AuditEvent,
            CommandReceipt,
            CheckIn,
            RunEvent,
            ActualCorrection,
            DeviceState,
            Thought,
            SessionRun,
            Reminder,
            CalendarSyncJob,
            CalendarEventLink,
            CalendarPreview,
            CalendarAuthorization,
            PlanningContext,
            CalendarConnection,
            SettingChangeRequest,
            RescheduleProposal,
            AdaptationSuggestion,
            Session,
            Step,
            Task,
            PlanCandidate,
            PlanningDraft,
            ScheduleVersion,
            Availability,
            RefreshToken,
            UserPreference,
        ):
            await db.execute(
                delete(model)
                .where(model.user_id.in_(user_ids))
                .execution_options(synchronize_session=False)
            )
        await db.execute(
            delete(User)
            .where(User.id.in_(user_ids))
            .execution_options(synchronize_session=False)
        )
        await db.commit()


def _run_alembic_upgrade(url: str) -> None:
    env = {**os.environ, "DATABASE_URL": url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@asynccontextmanager
async def _open_test_env(
    tmp_path: Path,
    db_filename: str,
    *,
    use_alembic: bool = False,
):
    pg_url = os.environ.get("PROACTIVE_TEST_PG_URL", "").strip()
    url = pg_url if pg_url else "sqlite+aiosqlite:///" + (tmp_path / db_filename).as_posix()
    if not pg_url and use_alembic:
        await asyncio.to_thread(_run_alembic_upgrade, url)

    engine = make_engine(url)
    if pg_url or not use_alembic:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    tracked_user_ids: list[UUID] = []

    async def override_get_db():
        async with factory() as db:
            yield db

    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield url, factory, tracked_user_ids
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        if pg_url and tracked_user_ids:
            await _purge_pg_users(factory, tracked_user_ids)
        await engine.dispose()


async def _create_google_user(
    db: AsyncSession,
    tracked_user_ids: list[UUID],
    *,
    tag: str,
    buffer_multiplier: float = 1.2,
    effective_revision: int = 1,
) -> tuple[UUID, str]:
    uid = uuid4()
    email = f"w4-12b-{tag}-{uid.hex[:8]}@example.test"
    db.add(
        User(
            id=uid,
            email=email,
            google_sub=f"google-sub-{uid.hex}",
            display_name=f"QA {tag}",
            is_active=True,
        )
    )
    await db.flush()
    db.add(
        UserPreference(
            user_id=uid,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            effective_revision=effective_revision,
            buffer_multiplier=buffer_multiplier,
            reminder_style="gentle",
        )
    )
    await db.flush()
    tracked_user_ids.append(uid)
    return uid, email


async def _seed_step_history(
    db: AsyncSession,
    *,
    owner: UUID,
    ratios: list[float],
    anchor_now: datetime,
    include_all_dirty_types: bool = False,
    with_future_session: bool = False,
) -> dict[str, Any]:
    draft_id = uuid4()
    task_id = uuid4()
    availability = DraftAvailability(
        timezone="Asia/Ho_Chi_Minh",
        weekly_rules=[
            WeeklyRule(weekday=wd, start_local="00:00", end_local="23:59")
            for wd in range(1, 8)
        ],
    )
    constraint = TimeConstraint(
        kind="deadline",
        start_at=anchor_now - timedelta(days=3),
        deadline_at=anchor_now + timedelta(days=7),
        input_timezone="Asia/Ho_Chi_Minh",
    )
    summary = TaskSummary(
        title="Lịch sử mẫu A20",
        description="Công việc mẫu kiểm thử ranh giới A20.",
        scope=f"{len(ratios)} bước",
        time_constraint=constraint,
        availability=availability,
        can_plan=True,
    )
    snapshot = summary.model_dump(mode="json")
    db.add(
        PlanningDraft(
            id=draft_id,
            user_id=owner,
            client_draft_id=f"test-a20-draft-{draft_id.hex[:8]}",
            stage="confirmed",
            input_source="text",
            raw_input=summary.title,
            summary={**snapshot, "_api_meta": {"confirmed_task_id": str(task_id)}},
            summary_confirmed_revision=1,
        )
    )
    await db.flush()
    db.add(
        Task(
            id=task_id,
            user_id=owner,
            source_draft_id=draft_id,
            title=summary.title or "Lịch sử mẫu A20",
            description=summary.description,
            scope=summary.scope,
            time_constraint=constraint.model_dump(mode="json"),
            availability=snapshot["availability"],
            source_summary=snapshot,
            source_summary_revision=1,
            source_planning_input_revision=1,
            decomposition_state="complete",
            status="pending" if with_future_session else "completed",
        )
    )
    await db.flush()

    clean_step_ids: list[UUID] = []
    base_seconds = 1000
    total = len(ratios)
    for idx, ratio in enumerate(ratios):
        ended_at = anchor_now - timedelta(hours=total - idx + 1)
        actual_seconds = int(round(base_seconds * ratio))
        started_at = ended_at - timedelta(seconds=max(actual_seconds, 600))
        step_id = uuid4()
        sess_id = uuid4()
        run_id = uuid4()
        db.add(
            Step(
                id=step_id,
                user_id=owner,
                task_id=task_id,
                title=f"Bước sạch {idx + 1}",
                expected_output="Xong bước",
                order_index=idx,
                estimated_base_seconds=base_seconds,
                remaining_base_seconds=0,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="completed",
                created_at=started_at,
                updated_at=ended_at,
            )
        )
        await db.flush()
        db.add(
            Session(
                id=sess_id,
                user_id=owner,
                step_id=step_id,
                scheduled_start=started_at,
                scheduled_end=started_at + timedelta(seconds=base_seconds),
                start_decision_due_at=started_at + timedelta(minutes=15),
                micro_task_text="Bắt đầu 2 phút",
                micro_task_seconds=120,
                work_seconds=base_seconds,
                rest_seconds=0,
                duration_seconds=base_seconds,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": base_seconds}],
                schedule_revision=1,
                start_state="started",
                status="completed",
                created_at=started_at,
                updated_at=ended_at,
            )
        )
        await db.flush()
        db.add(
            SessionRun(
                id=run_id,
                user_id=owner,
                session_id=sess_id,
                device_id="test-device",
                started_at=started_at,
                ended_at=ended_at,
                run_state="ended",
                active_phase="ended",
                work_elapsed_seconds=actual_seconds,
                actual_work_seconds=actual_seconds,
                actual_source="timer_proxy",
                outcome="completed",
                settings_snapshot={"buffer_multiplier": 1.2},
                created_at=started_at,
                updated_at=ended_at,
            )
        )
        await db.flush()
        db.add(
            CheckIn(
                user_id=owner,
                run_id=run_id,
                kind="final",
                response_state="answered",
                session_outcome="completed",
                step_completed=True,
                task_completed=False,
                asked_at=ended_at,
                responded_at=ended_at + timedelta(seconds=10),
                response_revision=1,
            )
        )
        clean_step_ids.append(step_id)

    if include_all_dirty_types:
        dirty_specs = (
            ("ai", "skipped", "skipped", "answered", 1600),
            ("ai", "completed", "no_response", "no_respond", 1600),
            ("scope_modified", "completed", "completed", "answered", 1600),
            ("ai", "completed", "completed", "answered", 0),
        )
        for offset, (est_src, sess_status, run_outcome, ci_state, actual_sec) in enumerate(
            dirty_specs
        ):
            ended_at = anchor_now - timedelta(hours=30 - offset)
            started_at = ended_at - timedelta(seconds=max(actual_sec, 600))
            step_id = uuid4()
            sess_id = uuid4()
            run_id = uuid4()
            db.add(
                Step(
                    id=step_id,
                    user_id=owner,
                    task_id=task_id,
                    title=f"Bước bẩn {offset}",
                    expected_output="Bỏ qua/bẩn",
                    order_index=100 + offset,
                    estimated_base_seconds=base_seconds,
                    remaining_base_seconds=0,
                    buffer_multiplier=1.2,
                    estimate_source=est_src,
                    status="completed",
                    created_at=started_at,
                    updated_at=ended_at,
                )
            )
            await db.flush()
            db.add(
                Session(
                    id=sess_id,
                    user_id=owner,
                    step_id=step_id,
                    scheduled_start=started_at,
                    scheduled_end=started_at + timedelta(seconds=base_seconds),
                    start_decision_due_at=started_at + timedelta(minutes=15),
                    micro_task_text="Bắt đầu",
                    micro_task_seconds=120,
                    work_seconds=base_seconds,
                    rest_seconds=0,
                    duration_seconds=base_seconds,
                    segments=[
                        {"order_index": 0, "kind": "work", "duration_seconds": base_seconds}
                    ],
                    schedule_revision=1,
                    start_state="started",
                    status=sess_status,
                )
            )
            await db.flush()
            db.add(
                SessionRun(
                    id=run_id,
                    user_id=owner,
                    session_id=sess_id,
                    device_id="test-device",
                    started_at=started_at,
                    ended_at=ended_at,
                    run_state="ended",
                    active_phase="ended",
                    work_elapsed_seconds=actual_sec,
                    actual_work_seconds=actual_sec,
                    actual_source="timer_proxy",
                    outcome=run_outcome,
                    settings_snapshot={"buffer_multiplier": 1.2},
                )
            )
            await db.flush()
            db.add(
                CheckIn(
                    user_id=owner,
                    run_id=run_id,
                    kind="final",
                    response_state=ci_state,
                    session_outcome=run_outcome,
                    step_completed=(run_outcome == "completed"),
                    task_completed=False,
                    asked_at=ended_at,
                    responded_at=ended_at + timedelta(seconds=10)
                    if ci_state == "answered"
                    else None,
                    response_revision=1 if ci_state == "answered" else 0,
                )
            )

    future_session_id: UUID | None = None
    if with_future_session:
        future_step_id = uuid4()
        future_session_id = uuid4()
        future_start = anchor_now + timedelta(days=1)
        db.add(
            Step(
                id=future_step_id,
                user_id=owner,
                task_id=task_id,
                title="Bước tương lai",
                expected_output="Hoàn tất bước tương lai",
                order_index=999,
                estimated_base_seconds=1250,
                remaining_base_seconds=1250,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="pending",
            )
        )
        await db.flush()
        db.add(
            Session(
                id=future_session_id,
                user_id=owner,
                step_id=future_step_id,
                scheduled_start=future_start,
                scheduled_end=future_start + timedelta(seconds=1500),
                start_decision_due_at=future_start + timedelta(minutes=15),
                micro_task_text="Mở tài liệu",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=0,
                duration_seconds=1500,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                schedule_revision=1,
                start_state="waiting",
                status="scheduled",
            )
        )

    await db.commit()
    return {
        "task_id": task_id,
        "clean_step_ids": clean_step_ids,
        "future_session_id": future_session_id,
    }


# =============================================================================
# NHÓM 1: BOUNDARY TESTS A20 QUA HTTP API
# =============================================================================


@pytest.mark.asyncio
async def test_a20_boundary_below_7_dirty_filter_and_exact_delta_020(
    tmp_path, auth_config, monkeypatch
):
    fixed_now = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    async with _open_test_env(tmp_path, "a20_boundaries_1.db") as (_, factory, tracked):
        async with factory() as db:
            uid_below, _ = await _create_google_user(
                db, tracked, tag="below6", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db,
                owner=uid_below,
                ratios=[1.6] * 6,
                anchor_now=fixed_now,
                include_all_dirty_types=True,
            )

            uid_eq, _ = await _create_google_user(
                db, tracked, tag="eq020", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db,
                owner=uid_eq,
                ratios=[1.4] * 7,
                anchor_now=fixed_now,
            )

            uid_gt, _ = await _create_google_user(
                db, tracked, tag="gt020", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db,
                owner=uid_gt,
                ratios=[1.45] * 7,
                anchor_now=fixed_now,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) < 7 mẫu sạch (6 sạch + 4 bẩn skipped/no_response/scope_modified/actual=0) -> null
            r_below = await client.get(
                "/api/v1/suggestions/current", headers=_bearer(uid_below)
            )
            assert r_below.status_code == 200
            assert r_below.json()["data"] is None

            # 2) == 7 mẫu sạch với |1.40 - 1.20| == 0.20 -> null
            r_eq = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_eq))
            assert r_eq.status_code == 200
            assert r_eq.json()["data"] is None

            # 3) Ca ngược: == 7 mẫu sạch với |1.45 - 1.20| = 0.25 > 0.20 -> pending
            r_gt = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_gt))
            assert r_gt.status_code == 200
            s_gt = r_gt.json()["data"]
            assert s_gt is not None
            assert s_gt["status"] == "pending"
            assert s_gt["sample_count"] == 7
            assert s_gt["current_value"] == 1.2
            assert s_gt["suggested_value"] == 1.45

            pref_gt = await client.get("/api/v1/preferences", headers=_bearer(uid_gt))
            assert pref_gt.status_code == 200
            assert pref_gt.json()["data"]["buffer_multiplier"] == 1.2


@pytest.mark.asyncio
async def test_a20_boundary_sliding_window_10_and_clamp_bounds(
    tmp_path, auth_config, monkeypatch
):
    fixed_now = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    async with _open_test_env(tmp_path, "a20_boundaries_2.db") as (_, factory, tracked):
        async with factory() as db:
            uid_win, _ = await _create_google_user(
                db, tracked, tag="win10", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db,
                owner=uid_win,
                ratios=[2.5, 2.5] + [1.55] * 10,
                anchor_now=fixed_now,
            )

            uid_hi, _ = await _create_google_user(
                db, tracked, tag="clamp-hi", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db,
                owner=uid_hi,
                ratios=[2.6] * 7,
                anchor_now=fixed_now,
            )

            uid_lo, _ = await _create_google_user(
                db, tracked, tag="clamp-lo", buffer_multiplier=1.5
            )
            await _seed_step_history(
                db,
                owner=uid_lo,
                ratios=[0.7] * 7,
                anchor_now=fixed_now,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Cửa sổ trượt 10/12: loại 2 mẫu cũ (2.50), chỉ giữ 10 mẫu mới (1.55)
            r_win = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_win))
            assert r_win.status_code == 200
            d_win = r_win.json()["data"]
            assert d_win is not None
            assert d_win["sample_count"] == 10
            assert d_win["current_value"] == 1.2
            assert d_win["suggested_value"] == 1.55
            assert d_win["status"] == "pending"

            pref_win = await client.get("/api/v1/preferences", headers=_bearer(uid_win))
            assert pref_win.json()["data"]["buffer_multiplier"] == 1.2

            # 2) Kẹp trần [1.0, 2.0] khi ratio=2.6 -> suggested_value=2.0, giữ nguyên buffer_multiplier
            r_hi = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_hi))
            assert r_hi.status_code == 200
            d_hi = r_hi.json()["data"]
            assert d_hi is not None
            assert d_hi["suggested_value"] == 2.0
            pref_hi = await client.get("/api/v1/preferences", headers=_bearer(uid_hi))
            assert pref_hi.json()["data"]["buffer_multiplier"] == 1.2

            # 3) Kẹp đáy [1.0, 2.0] khi ratio=0.7 -> suggested_value=1.0, giữ nguyên buffer_multiplier
            r_lo = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_lo))
            assert r_lo.status_code == 200
            d_lo = r_lo.json()["data"]
            assert d_lo is not None
            assert d_lo["suggested_value"] == 1.0
            pref_lo = await client.get("/api/v1/preferences", headers=_bearer(uid_lo))
            assert pref_lo.json()["data"]["buffer_multiplier"] == 1.5


@pytest.mark.asyncio
async def test_a20_response_409_conflicts_and_accept_edit_to_uf6(
    tmp_path, auth_config, monkeypatch
):
    fixed_now = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    async with _open_test_env(tmp_path, "a20_conflicts_uf6.db") as (_, factory, tracked):
        async with factory() as db:
            uid_accept, _ = await _create_google_user(
                db, tracked, tag="accept-uf6", buffer_multiplier=1.2, effective_revision=1
            )
            await _seed_step_history(
                db,
                owner=uid_accept,
                ratios=[1.6] * 10,
                anchor_now=fixed_now,
                with_future_session=True,
            )

            uid_edit, _ = await _create_google_user(
                db, tracked, tag="edit-uf6", buffer_multiplier=1.2, effective_revision=1
            )
            await _seed_step_history(
                db,
                owner=uid_edit,
                ratios=[1.6] * 10,
                anchor_now=fixed_now,
                with_future_session=True,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            s_acc = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_accept))
            ).json()["data"]
            assert s_acc is not None
            assert s_acc["suggested_value"] == 1.6

            # 1) Stale expected_suggestion_revision -> 409 REVISION_CONFLICT
            stale_sugg = await client.post(
                f"/api/v1/suggestions/{s_acc['suggestion_id']}/response",
                headers=_bearer(uid_accept, "idem-stale-sugg"),
                json={
                    "expected_suggestion_revision": s_acc["suggestion_revision"] + 5,
                    "expected_preference_revision": 1,
                    "action": "accept",
                },
            )
            assert stale_sugg.status_code == 409
            assert stale_sugg.json()["error"]["code"] == "REVISION_CONFLICT"

            # 2) Stale expected_preference_revision -> 409 REVISION_CONFLICT
            stale_pref = await client.post(
                f"/api/v1/suggestions/{s_acc['suggestion_id']}/response",
                headers=_bearer(uid_accept, "idem-stale-pref"),
                json={
                    "expected_suggestion_revision": s_acc["suggestion_revision"],
                    "expected_preference_revision": 99,
                    "action": "accept",
                },
            )
            assert stale_pref.status_code == 409
            assert stale_pref.json()["error"]["code"] == "REVISION_CONFLICT"

            # 3) accept khi có phiên tương lai -> pending_review + pending_proposal_id, giữ buffer & effective_revision
            ok_acc = await client.post(
                f"/api/v1/suggestions/{s_acc['suggestion_id']}/response",
                headers=_bearer(uid_accept, "idem-ok-acc"),
                json={
                    "expected_suggestion_revision": s_acc["suggestion_revision"],
                    "expected_preference_revision": 1,
                    "action": "accept",
                },
            )
            assert ok_acc.status_code == 200
            acc_data = ok_acc.json()["data"]
            assert acc_data["status"] == "accepted"
            assert acc_data["accepted_value"] == 1.6
            assert acc_data["application_state"] == "pending_review"
            assert acc_data["effective_revision"] == 1
            assert acc_data["requested_revision"] == 2
            assert acc_data["pending_proposal_id"] is not None

            pref_acc = (
                await client.get("/api/v1/preferences", headers=_bearer(uid_accept))
            ).json()["data"]
            assert pref_acc["buffer_multiplier"] == 1.2
            assert pref_acc["effective_revision"] == 1
            assert pref_acc["requested_revision"] == 2
            assert pref_acc["pending_proposal_id"] == acc_data["pending_proposal_id"]

            # 4) Gửi lại với gợi ý đã xử lý -> 409 SUGGESTION_CHANGED
            dup_acc = await client.post(
                f"/api/v1/suggestions/{s_acc['suggestion_id']}/response",
                headers=_bearer(uid_accept, "idem-dup-acc"),
                json={
                    "expected_suggestion_revision": s_acc["suggestion_revision"],
                    "action": "accept",
                },
            )
            assert dup_acc.status_code == 409
            assert dup_acc.json()["error"]["code"] == "SUGGESTION_CHANGED"

            # 5) edit khi có phiên tương lai -> pending_review + pending_proposal_id, chỉ áp dụng khi duyệt UF6
            s_ed = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_edit))
            ).json()["data"]
            assert s_ed is not None

            ok_ed = await client.post(
                f"/api/v1/suggestions/{s_ed['suggestion_id']}/response",
                headers=_bearer(uid_edit, "idem-ok-edit"),
                json={
                    "expected_suggestion_revision": s_ed["suggestion_revision"],
                    "expected_preference_revision": 1,
                    "action": "edit",
                    "edited_value": 1.45,
                },
            )
            assert ok_ed.status_code == 200
            ed_data = ok_ed.json()["data"]
            assert ed_data["action"] == "edit"
            assert ed_data["status"] == "accepted"
            assert ed_data["accepted_value"] == 1.45
            assert ed_data["application_state"] == "pending_review"
            assert ed_data["effective_revision"] == 1
            assert ed_data["requested_revision"] == 2
            assert ed_data["pending_proposal_id"] is not None

            pref_ed_before = (
                await client.get("/api/v1/preferences", headers=_bearer(uid_edit))
            ).json()["data"]
            assert pref_ed_before["buffer_multiplier"] == 1.2
            assert pref_ed_before["effective_revision"] == 1

            prop_ed = (
                await client.get("/api/v1/proposals/current", headers=_bearer(uid_edit))
            ).json()["data"]
            assert prop_ed["proposal_id"] == ed_data["pending_proposal_id"]

            app_ed = await client.post(
                f"/api/v1/proposals/{prop_ed['proposal_id']}/approve",
                headers=_bearer(uid_edit, "idem-approve-ed"),
                json={
                    "expected_proposal_revision": prop_ed["proposal_revision"],
                    "expected_base_schedule_revision": prop_ed["base_schedule_revision"],
                    "expected_source_revisions": prop_ed["source_revisions"],
                },
            )
            assert app_ed.status_code == 200
            pref_ed_after = (
                await client.get("/api/v1/preferences", headers=_bearer(uid_edit))
            ).json()["data"]
            assert pref_ed_after["buffer_multiplier"] == 1.45
            assert pref_ed_after["effective_revision"] == 2
            assert pref_ed_after["pending_proposal_id"] is None


@pytest.mark.asyncio
async def test_a20_cooldown_boundary_7d_normal_vs_14d_annoyance(
    tmp_path, auth_config, monkeypatch
):
    t_fetch = datetime(2026, 11, 10, 9, 59, 0, tzinfo=UTC)
    t0 = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    clock = [t_fetch]
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or clock[0])

    async with _open_test_env(tmp_path, "a20_cooldown_7d_14d.db") as (_, factory, tracked):
        async with factory() as db:
            uid_7d, _ = await _create_google_user(
                db, tracked, tag="cd7d", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db, owner=uid_7d, ratios=[1.6] * 7, anchor_now=t_fetch
            )

            uid_14d, _ = await _create_google_user(
                db, tracked, tag="cd14d", buffer_multiplier=1.2
            )
            await _seed_step_history(
                db, owner=uid_14d, ratios=[1.6] * 7, anchor_now=t_fetch
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # --- Nhánh 1: Reject thường -> cooldown đúng 7 ngày ---
            clock[0] = t_fetch
            s7 = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_7d))
            ).json()["data"]
            assert s7 is not None

            clock[0] = t0
            rej7 = await client.post(
                f"/api/v1/suggestions/{s7['suggestion_id']}/response",
                headers=_bearer(uid_7d, "idem-rej-7d"),
                json={
                    "expected_suggestion_revision": s7["suggestion_revision"],
                    "action": "reject",
                },
            )
            assert rej7.status_code == 200
            assert rej7.json()["data"]["status"] == "rejected"

            async with factory() as db:
                pref_7d = await db.scalar(
                    select(UserPreference).where(UserPreference.user_id == uid_7d)
                )
                assert pref_7d is not None
                assert pref_7d.suggestion_cooldown_until == t0 + timedelta(days=7)

            clock[0] = t0 + timedelta(days=6, hours=23, minutes=59)
            r7_before = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_7d))
            assert r7_before.status_code == 200
            assert r7_before.json()["data"] is None

            clock[0] = t0 + timedelta(days=7, seconds=1)
            r7_after = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_7d))
            assert r7_after.status_code == 200
            s7_reopened = r7_after.json()["data"]
            assert s7_reopened is not None
            assert s7_reopened["status"] == "pending"
            assert s7_reopened["suggestion_id"] != s7["suggestion_id"]

            # --- Nhánh 2: Reject kèm annoyance_feedback -> cooldown đúng 14 ngày ---
            clock[0] = t_fetch
            s14 = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_14d))
            ).json()["data"]
            assert s14 is not None

            clock[0] = t0
            rej14 = await client.post(
                f"/api/v1/suggestions/{s14['suggestion_id']}/response",
                headers=_bearer(uid_14d, "idem-rej-14d"),
                json={
                    "expected_suggestion_revision": s14["suggestion_revision"],
                    "action": "reject",
                    "annoyance_feedback": "too_frequent",
                },
            )
            assert rej14.status_code == 200
            assert rej14.json()["data"]["status"] == "rejected"

            async with factory() as db:
                pref_14d = await db.scalar(
                    select(UserPreference).where(UserPreference.user_id == uid_14d)
                )
                assert pref_14d is not None
                assert pref_14d.suggestion_cooldown_until == t0 + timedelta(days=14)

            # Ở mốc 7 ngày + 1 giây vẫn bị chặn bởi annoyance cooldown 14 ngày
            clock[0] = t0 + timedelta(days=7, seconds=1)
            r14_at_7d = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_14d))
            assert r14_at_7d.status_code == 200
            assert r14_at_7d.json()["data"] is None

            clock[0] = t0 + timedelta(days=13, hours=23, minutes=59)
            r14_before = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_14d))
            assert r14_before.status_code == 200
            assert r14_before.json()["data"] is None

            clock[0] = t0 + timedelta(days=14, seconds=1)
            r14_after = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_14d))
            assert r14_after.status_code == 200
            s14_reopened = r14_after.json()["data"]
            assert s14_reopened is not None
            assert s14_reopened["status"] == "pending"
            assert s14_reopened["suggestion_id"] != s14["suggestion_id"]


# =============================================================================
# NHÓM 2: END-TO-END TESTS CHO scripts/seed_w4_a20_cases.py
# =============================================================================


async def _snapshot_w4_03a_counts(db: AsyncSession, owner: UUID) -> dict[str, int]:
    return {
        "drafts": int(
            await db.scalar(
                select(func.count())
                .select_from(PlanningDraft)
                .where(
                    PlanningDraft.user_id == owner,
                    or_(
                        PlanningDraft.client_draft_id == "w4-03a-draft",
                        PlanningDraft.client_draft_id == "w4-03a-history",
                    ),
                )
            )
            or 0
        ),
        "tasks": int(
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.user_id == owner, Task.title.like("[W4 QA %"))
            )
            or 0
        ),
        "steps": int(
            await db.scalar(select(func.count()).select_from(Step).where(Step.user_id == owner))
            or 0
        ),
        "sessions": int(
            await db.scalar(
                select(func.count()).select_from(Session).where(Session.user_id == owner)
            )
            or 0
        ),
        "runs": int(
            await db.scalar(
                select(func.count()).select_from(SessionRun).where(SessionRun.user_id == owner)
            )
            or 0
        ),
        "checkins": int(
            await db.scalar(
                select(func.count()).select_from(CheckIn).where(CheckIn.user_id == owner)
            )
            or 0
        ),
    }


@pytest.mark.asyncio
async def test_seed_w4_a20_cases_all_6_scenarios_idempotent_fk_reset_and_isolation(
    tmp_path, auth_config
):
    async with _open_test_env(
        tmp_path, "a20_seed_e2e.db", use_alembic=True
    ) as (url, factory, tracked):
        uid_a = uuid4()
        uid_b = uuid4()
        email_a = f"qa-a-{uid_a.hex[:8]}@example.test"
        email_b = f"qa-b-{uid_b.hex[:8]}@example.test"

        async with factory() as db:
            db.add_all(
                [
                    User(
                        id=uid_a,
                        email=email_a,
                        google_sub=f"sub-qa-a-{uid_a.hex[:8]}",
                        display_name="Thành QA A",
                        is_active=True,
                    ),
                    User(
                        id=uid_b,
                        email=email_b,
                        google_sub=f"sub-qa-b-{uid_b.hex[:8]}",
                        display_name="Thành QA B",
                        is_active=True,
                    ),
                ]
            )
            await db.commit()
            tracked.extend([uid_a, uid_b])

        await seed_w4_qa.seed_qa(url, (email_a, email_b))

        async with factory() as db:
            baseline_a = await _snapshot_w4_03a_counts(db, uid_a)
            baseline_b = await _snapshot_w4_03a_counts(db, uid_b)
            assert baseline_a == {
                "drafts": 2,
                "tasks": 1,
                "steps": 1,
                "sessions": 1,
                "runs": 1,
                "checkins": 1,
            }
            assert baseline_b == baseline_a
            pref_b_initial = await db.scalar(
                select(UserPreference).where(UserPreference.user_id == uid_b)
            )
            assert pref_b_initial is not None
            pref_b_initial.work_interval_seconds = 1800
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) below_min_6
            res_below = await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "below_min_6")
            assert res_below["added_clean_steps"] == 5
            assert res_below["added_dirty_steps"] == 4
            r_below = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            assert r_below.status_code == 200
            assert r_below.json()["data"] is None
            r_tasks = await client.get("/api/v1/tasks", headers=_bearer(uid_a))
            assert r_tasks.status_code == 200

            # 2) exact_delta_020
            res_eq = await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "exact_delta_020")
            assert res_eq["added_clean_steps"] == 10
            r_eq = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            assert r_eq.status_code == 200
            assert r_eq.json()["data"] is None

            # 3) trigger_window_10 + idempotency khi chạy lại lần 2
            res_win1 = await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "trigger_window_10")
            assert res_win1["added_clean_steps"] == 12
            r_win1 = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            assert r_win1.status_code == 200
            d_win1 = r_win1.json()["data"]
            assert d_win1 is not None
            assert d_win1["status"] == "pending"
            assert d_win1["sample_count"] == 10
            assert d_win1["current_value"] == 1.2
            assert d_win1["suggested_value"] == 1.55
            pref_win1 = (await client.get("/api/v1/preferences", headers=_bearer(uid_a))).json()[
                "data"
            ]
            assert pref_win1["buffer_multiplier"] == 1.2

            async with factory() as db:
                tasks_cnt_1 = await db.scalar(
                    select(func.count()).select_from(Task).where(Task.user_id == uid_a)
                )

            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "trigger_window_10")
            async with factory() as db:
                tasks_cnt_2 = await db.scalar(
                    select(func.count()).select_from(Task).where(Task.user_id == uid_a)
                )
            assert tasks_cnt_2 == tasks_cnt_1
            r_win2 = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            assert r_win2.status_code == 200
            assert r_win2.json()["data"]["suggested_value"] == 1.55

            # 4) accept_and_conflict -> 409 REVISION_CONFLICT -> accept -> 409 SUGGESTION_CHANGED
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "accept_and_conflict")
            s_ac = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"]
            assert s_ac is not None
            assert s_ac["suggested_value"] == 1.6
            assert s_ac["sample_count"] == 10

            r_409_rev = await client.post(
                f"/api/v1/suggestions/{s_ac['suggestion_id']}/response",
                headers=_bearer(uid_a, "idem-e2e-409-rev"),
                json={
                    "expected_suggestion_revision": 99,
                    "expected_preference_revision": 1,
                    "action": "accept",
                },
            )
            assert r_409_rev.status_code == 409
            assert r_409_rev.json()["error"]["code"] == "REVISION_CONFLICT"

            r_accept = await client.post(
                f"/api/v1/suggestions/{s_ac['suggestion_id']}/response",
                headers=_bearer(uid_a, "idem-e2e-accept-1"),
                json={
                    "expected_suggestion_revision": s_ac["suggestion_revision"],
                    "action": "accept",
                },
            )
            assert r_accept.status_code == 200
            accept_data = r_accept.json()["data"]
            assert accept_data["application_state"] == "pending_review"
            assert accept_data["pending_proposal_id"] is not None

            r_409_changed = await client.post(
                f"/api/v1/suggestions/{s_ac['suggestion_id']}/response",
                headers=_bearer(uid_a, "idem-e2e-409-changed"),
                json={
                    "expected_suggestion_revision": s_ac["suggestion_revision"],
                    "action": "accept",
                },
            )
            assert r_409_changed.status_code == 409
            assert r_409_changed.json()["error"]["code"] == "SUGGESTION_CHANGED"

            # Đang có pending_proposal_id trỏ vào RescheduleProposal A20 -> chuyển sang below_min_6 không lỗi FK RESTRICT
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "below_min_6")
            pref_after_switch = (
                await client.get("/api/v1/preferences", headers=_bearer(uid_a))
            ).json()["data"]
            assert pref_after_switch["pending_proposal_id"] is None
            assert pref_after_switch["buffer_multiplier"] == 1.2
            assert (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"] is None

            # Kiểm tra thêm nhánh accept -> UF6 approve rồi mới reset
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "accept_and_conflict")
            s_ac2 = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"]
            assert s_ac2 is not None
            r_accept2 = await client.post(
                f"/api/v1/suggestions/{s_ac2['suggestion_id']}/response",
                headers=_bearer(uid_a, "idem-e2e-accept-2"),
                json={
                    "expected_suggestion_revision": s_ac2["suggestion_revision"],
                    "action": "accept",
                },
            )
            assert r_accept2.status_code == 200
            prop_cur = (
                await client.get("/api/v1/proposals/current", headers=_bearer(uid_a))
            ).json()["data"]
            assert prop_cur["proposal_id"] == r_accept2.json()["data"]["pending_proposal_id"]
            r_approve = await client.post(
                f"/api/v1/proposals/{prop_cur['proposal_id']}/approve",
                headers=_bearer(uid_a, "idem-e2e-approve-2"),
                json={
                    "expected_proposal_revision": prop_cur["proposal_revision"],
                    "expected_base_schedule_revision": prop_cur["base_schedule_revision"],
                    "expected_source_revisions": prop_cur["source_revisions"],
                },
            )
            assert r_approve.status_code == 200
            assert (
                await client.get("/api/v1/preferences", headers=_bearer(uid_a))
            ).json()["data"]["buffer_multiplier"] == 1.6

            # 5) cooldown_7d & 6) cooldown_14d
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "cooldown_7d")
            assert (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"] is None

            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "cooldown_14d")
            assert (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"] is None

            # --reset xóa sạch A20, giữ nguyên 100% W4-03A và cô lập hoàn toàn qa-b
            await seed_w4_a20_cases.reset_a20_data(url, email_a)

            async with factory() as db:
                after_a = await _snapshot_w4_03a_counts(db, uid_a)
                after_b = await _snapshot_w4_03a_counts(db, uid_b)
                assert after_a == baseline_a
                assert after_b == baseline_b
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AdaptationSuggestion)
                        .where(AdaptationSuggestion.user_id == uid_a)
                    )
                    == 0
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(SettingChangeRequest)
                        .where(SettingChangeRequest.user_id == uid_a)
                    )
                    == 0
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(RescheduleProposal)
                        .where(RescheduleProposal.user_id == uid_a)
                    )
                    == 0
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(ScheduleVersion)
                        .where(ScheduleVersion.user_id == uid_a)
                    )
                    == 0
                )
                pref_a = await db.scalar(
                    select(UserPreference).where(UserPreference.user_id == uid_a)
                )
                pref_b = await db.scalar(
                    select(UserPreference).where(UserPreference.user_id == uid_b)
                )
                assert pref_a is not None
                assert pref_a.buffer_multiplier == 1.2
                assert pref_a.pending_proposal_id is None
                assert pref_a.requested_revision is None
                assert pref_a.requested_changes is None
                assert pref_a.suggestion_cooldown_until is None
                assert pref_b is not None and pref_b.work_interval_seconds == 1800

            tasks_a_final = (
                await client.get("/api/v1/tasks", headers=_bearer(uid_a))
            ).json()["data"]["items"]
            assert len(tasks_a_final) == 1
            assert "[W4 QA A]" in tasks_a_final[0]["title"]

            drafts_b_final = (
                await client.get("/api/v1/drafts", headers=_bearer(uid_b))
            ).json()["data"]["items"]
            assert len(drafts_b_final) == 2
            assert all("[W4 QA B]" in (d.get("summary") or {}).get("title", "") for d in drafts_b_final)


@pytest.mark.parametrize(
    "user_kwargs",
    [
        None,
        {"is_active": False, "google_sub": "sub-inactive"},
        {"is_active": True, "google_sub": ""},
        {"is_active": True, "google_sub": None},
    ],
)
def test_seed_w4_a20_cases_rejects_invalid_or_missing_user_and_cli_errors(
    tmp_path, monkeypatch, user_kwargs: dict[str, Any] | None
):
    pg_url = os.environ.get("PROACTIVE_TEST_PG_URL", "").strip()
    url = pg_url if pg_url else "sqlite+aiosqlite:///" + (tmp_path / "a20_cli_errors.db").as_posix()
    email = f"invalid-{uuid4().hex[:8]}@example.test"
    tracked: list[UUID] = []

    async def _setup_and_verify_async_errors() -> None:
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = session_factory(engine)
            if user_kwargs is not None:
                uid = uuid4()
                async with factory() as db:
                    db.add(
                        User(
                            id=uid,
                            email=email,
                            display_name="Invalid QA",
                            **user_kwargs,
                        )
                    )
                    await db.commit()
                    tracked.append(uid)

            async with factory() as db:
                before_counts = {
                    model.__name__: int(
                        await db.scalar(select(func.count()).select_from(model)) or 0
                    )
                    for model in (Task, Step, Session, SessionRun, PlanningDraft, AdaptationSuggestion)
                }

            with pytest.raises(ValueError, match="tài khoản Google"):
                await seed_w4_a20_cases.seed_a20_scenario(url, email, "below_min_6")

            with pytest.raises(ValueError, match="tài khoản Google"):
                await seed_w4_a20_cases.reset_a20_data(url, email)

            async with factory() as db:
                for model in (Task, Step, Session, SessionRun, PlanningDraft, AdaptationSuggestion):
                    after_count = int(
                        await db.scalar(select(func.count()).select_from(model)) or 0
                    )
                    assert after_count == before_counts[model.__name__]
        finally:
            await engine.dispose()

    async def _cleanup_pg() -> None:
        if not (pg_url and tracked):
            return
        engine = make_engine(url)
        try:
            await _purge_pg_users(session_factory(engine), tracked)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_setup_and_verify_async_errors())
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(
            sys, "argv", ["seed_w4_a20_cases.py", email, "--scenario", "below_min_6"]
        )
        with pytest.raises(SystemExit) as user_exc:
            seed_w4_a20_cases.main()
        assert user_exc.value.code == 1
    finally:
        asyncio.run(_cleanup_pg())


def test_seed_w4_a20_cases_cli_argument_and_env_validation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(
        sys, "argv", ["seed_w4_a20_cases.py", "qa@example.test", "--scenario", "unknown_case"]
    )
    with pytest.raises(SystemExit) as bad_sc:
        seed_w4_a20_cases.main()
    assert bad_sc.value.code != 0

    monkeypatch.setattr(sys, "argv", ["seed_w4_a20_cases.py", "qa@example.test"])
    with pytest.raises(SystemExit) as missing_mode:
        seed_w4_a20_cases.main()
    assert missing_mode.value.code != 0

    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_w4_a20_cases.py", "qa@example.test", "--scenario", "below_min_6", "--reset"],
    )
    with pytest.raises(SystemExit) as both_modes:
        seed_w4_a20_cases.main()
    assert both_modes.value.code != 0

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["seed_w4_a20_cases.py", "qa@example.test", "--scenario", "below_min_6"]
    )
    with pytest.raises(SystemExit) as missing_env:
        seed_w4_a20_cases.main()
    assert missing_env.value.code != 0


async def _snapshot_w4_03a_full(db: AsyncSession, owner: UUID) -> dict[str, Any]:
    counts = await _snapshot_w4_03a_counts(db, owner)
    pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == owner))
    counts["preferences"] = 1 if pref is not None else 0
    return {
        "counts": counts,
        "pref": (
            {
                "id": str(pref.id),
                "work_interval_seconds": pref.work_interval_seconds,
                "rest_interval_seconds": pref.rest_interval_seconds,
                "micro_task_seconds": pref.micro_task_seconds,
                "reminder_offsets": list(pref.reminder_offsets),
                "buffer_multiplier": pref.buffer_multiplier,
                "reminder_style": pref.reminder_style,
                "pending_proposal_id": pref.pending_proposal_id,
                "suggestion_cooldown_until": pref.suggestion_cooldown_until,
            }
            if pref is not None
            else None
        ),
    }


@pytest.mark.asyncio
async def test_seed_w4_a20_cases_reset_idempotent_before_any_a20_and_without_preference(
    tmp_path, auth_config
):
    async with _open_test_env(
        tmp_path, "a20_reset_before_seed.db", use_alembic=True
    ) as (url, factory, tracked):
        uid_a = uuid4()
        uid_b = uuid4()
        uid_c = uuid4()
        email_a = f"qa-a-rst-{uid_a.hex[:8]}@example.test"
        email_b = f"qa-b-rst-{uid_b.hex[:8]}@example.test"
        email_c = f"qa-c-nopref-{uid_c.hex[:8]}@example.test"

        async with factory() as db:
            db.add_all(
                [
                    User(
                        id=uid_a,
                        email=email_a,
                        google_sub=f"sub-rst-a-{uid_a.hex[:8]}",
                        display_name="QA Reset A",
                        is_active=True,
                    ),
                    User(
                        id=uid_b,
                        email=email_b,
                        google_sub=f"sub-rst-b-{uid_b.hex[:8]}",
                        display_name="QA Reset B",
                        is_active=True,
                    ),
                    User(
                        id=uid_c,
                        email=email_c,
                        google_sub=f"sub-rst-c-{uid_c.hex[:8]}",
                        display_name="QA Reset NoPref C",
                        is_active=True,
                    ),
                ]
            )
            await db.commit()
            tracked.extend([uid_a, uid_b, uid_c])

        await seed_w4_qa.seed_qa(url, (email_a, email_b))

        async with factory() as db:
            pref_a = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid_a))
            pref_b = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid_b))
            assert pref_a is not None and pref_b is not None
            pref_a.work_interval_seconds = 1800
            pref_a.rest_interval_seconds = 600
            pref_a.reminder_offsets = [600, 300]
            pref_a.reminder_style = "firm"
            pref_a.buffer_multiplier = 1.4

            pref_b.work_interval_seconds = 2100
            pref_b.buffer_multiplier = 1.5
            await db.commit()

            snap_a_before = await _snapshot_w4_03a_full(db, uid_a)
            snap_b_before = await _snapshot_w4_03a_full(db, uid_b)

        # Chạy --reset 2 lần liên tiếp khi chưa từng seed A20
        res1 = await seed_w4_a20_cases.reset_a20_data(url, email_a)
        res2 = await seed_w4_a20_cases.reset_a20_data(url, email_a)
        assert all(v == 0 for v in res1["removed"].values())
        assert all(v == 0 for v in res2["removed"].values())

        # User C chưa từng có UserPreference -> --reset không lỗi và không tạo row rác
        res_c1 = await seed_w4_a20_cases.reset_a20_data(url, email_c)
        res_c2 = await seed_w4_a20_cases.reset_a20_data(url, email_c)
        assert all(v == 0 for v in res_c1["removed"].values())
        assert all(v == 0 for v in res_c2["removed"].values())

        async with factory() as db:
            snap_a_after = await _snapshot_w4_03a_full(db, uid_a)
            snap_b_after = await _snapshot_w4_03a_full(db, uid_b)
            assert snap_a_after["counts"] == snap_a_before["counts"]
            assert snap_b_after == snap_b_before

            # Các field cấu hình khác ngoài buffer_multiplier giữ nguyên, buffer về baseline 1.2
            assert snap_a_after["pref"] is not None
            assert snap_a_after["pref"]["work_interval_seconds"] == 1800
            assert snap_a_after["pref"]["rest_interval_seconds"] == 600
            assert snap_a_after["pref"]["reminder_offsets"] == [600, 300]
            assert snap_a_after["pref"]["reminder_style"] == "firm"
            assert snap_a_after["pref"]["buffer_multiplier"] == 1.2
            assert snap_a_after["pref"]["suggestion_cooldown_until"] is None

            pref_c = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid_c))
            assert pref_c is None


@pytest.mark.asyncio
async def test_seed_w4_a20_cases_adversarial_uf6_calendar_approve_cooldown_transitions_and_preservation(
    tmp_path, auth_config
):
    async with _open_test_env(
        tmp_path, "a20_adversarial_uf6_cal.db", use_alembic=True
    ) as (url, factory, tracked):
        uid_a = uuid4()
        uid_b = uuid4()
        email_a = f"qa-a-adv-{uid_a.hex[:8]}@example.test"
        email_b = f"qa-b-adv-{uid_b.hex[:8]}@example.test"

        async with factory() as db:
            db.add_all(
                [
                    User(
                        id=uid_a,
                        email=email_a,
                        google_sub=f"sub-adv-a-{uid_a.hex[:8]}",
                        display_name="QA Adversarial A",
                        is_active=True,
                    ),
                    User(
                        id=uid_b,
                        email=email_b,
                        google_sub=f"sub-adv-b-{uid_b.hex[:8]}",
                        display_name="QA Adversarial B",
                        is_active=True,
                    ),
                ]
            )
            await db.commit()
            tracked.extend([uid_a, uid_b])

        await seed_w4_qa.seed_qa(url, (email_a, email_b))

        conn_id = uuid4()
        preview_id = uuid4()
        async with factory() as db:
            pref_b = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid_b))
            assert pref_b is not None
            pref_b.work_interval_seconds = 1800

            db.add(
                CalendarConnection(
                    id=conn_id,
                    user_id=uid_a,
                    provider="google",
                    account_ref=email_a,
                    read_calendar_ids=["primary"],
                    write_calendar_id="primary",
                    granted_scopes=["https://www.googleapis.com/auth/calendar"],
                    connection_state="connected",
                    credential_ref="qa-adv-cred-ref",
                )
            )
            await db.commit()

            baseline_a = await _snapshot_w4_03a_full(db, uid_a)
            baseline_b = await _snapshot_w4_03a_full(db, uid_b)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) accept_and_conflict -> accept -> approve UF6 có CalendarConnection -> tạo đầy đủ bảng con RESTRICT
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "accept_and_conflict")
            sugg = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"]
            assert sugg is not None

            r_acc = await client.post(
                f"/api/v1/suggestions/{sugg['suggestion_id']}/response",
                headers=_bearer(uid_a, "idem-adv-accept-1"),
                json={
                    "expected_suggestion_revision": sugg["suggestion_revision"],
                    "action": "accept",
                },
            )
            assert r_acc.status_code == 200
            prop_id = UUID(r_acc.json()["data"]["pending_proposal_id"])

            async with factory() as db:
                db.add(
                    CalendarPreview(
                        id=preview_id,
                        user_id=uid_a,
                        connection_id=conn_id,
                        kind="configuration",
                        base_connection_revision=1,
                        required_proposal_id=prop_id,
                        source_revisions={"connection_revision": 1},
                        payload={"write_calendar_id": "primary"},
                        status="pending",
                    )
                )
                await db.commit()

            prop_cur = (
                await client.get("/api/v1/proposals/current", headers=_bearer(uid_a))
            ).json()["data"]
            r_app = await client.post(
                f"/api/v1/proposals/{prop_cur['proposal_id']}/approve",
                headers=_bearer(uid_a, "idem-adv-approve-1"),
                json={
                    "expected_proposal_revision": prop_cur["proposal_revision"],
                    "expected_base_schedule_revision": prop_cur["base_schedule_revision"],
                    "expected_source_revisions": prop_cur["source_revisions"],
                },
            )
            assert r_app.status_code == 200
            assert r_app.json()["data"]["calendar_sync_job_enqueued"] is True

            async with factory() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(CalendarEventLink)
                        .where(CalendarEventLink.user_id == uid_a)
                    )
                    >= 1
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(CalendarSyncJob)
                        .where(CalendarSyncJob.user_id == uid_a)
                    )
                    >= 1
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(Reminder)
                        .where(Reminder.user_id == uid_a)
                    )
                    >= 1
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(ScheduleVersion)
                        .where(ScheduleVersion.user_id == uid_a)
                    )
                    == 1
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(CommandReceipt)
                        .where(CommandReceipt.user_id == uid_a)
                    )
                    >= 2
                )

            # 2) Chuyển thẳng sang cooldown_7d -> không lỗi FK RESTRICT, dọn sạch toàn bộ bảng con A20
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "cooldown_7d")
            assert (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"] is None

            async with factory() as db:
                for model in (
                    CalendarSyncJob,
                    CalendarEventLink,
                    Reminder,
                    ScheduleVersion,
                    SettingChangeRequest,
                    RescheduleProposal,
                ):
                    assert (
                        await db.scalar(
                            select(func.count()).select_from(model).where(model.user_id == uid_a)
                        )
                        == 0
                    ), f"{model.__name__} vẫn còn dư sau khi chuyển sang cooldown_7d"
                preview_row = await db.get(CalendarPreview, preview_id)
                assert preview_row is not None and preview_row.required_proposal_id is None
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(CalendarConnection)
                        .where(CalendarConnection.user_id == uid_a)
                    )
                    == 1
                )

            # 3) Chuyển từ cooldown_7d sang trigger_window_10 -> xóa sạch cooldown và tạo lại gợi ý pending
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "trigger_window_10")
            async with factory() as db:
                pref_after_w10 = await db.scalar(
                    select(UserPreference).where(UserPreference.user_id == uid_a)
                )
                assert pref_after_w10 is not None
                assert pref_after_w10.suggestion_cooldown_until is None
                assert pref_after_w10.buffer_multiplier == 1.2

            r_w10 = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            assert r_w10.status_code == 200
            d_w10 = r_w10.json()["data"]
            assert d_w10 is not None
            assert d_w10["status"] == "pending"
            assert d_w10["suggested_value"] == 1.55
            assert d_w10["sample_count"] == 10

            # 4) Vòng lại accept_and_conflict -> accept -> rebuild -> chuyển sang cooldown_14d -> trigger_window_10 -> --reset
            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "accept_and_conflict")
            sugg2 = (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"]
            r_acc2 = await client.post(
                f"/api/v1/suggestions/{sugg2['suggestion_id']}/response",
                headers=_bearer(uid_a, "idem-adv-accept-2"),
                json={
                    "expected_suggestion_revision": sugg2["suggestion_revision"],
                    "action": "accept",
                },
            )
            assert r_acc2.status_code == 200
            prop2_cur = (
                await client.get("/api/v1/proposals/current", headers=_bearer(uid_a))
            ).json()["data"]
            r_reb = await client.post(
                f"/api/v1/proposals/{prop2_cur['proposal_id']}/rebuild",
                headers=_bearer(uid_a, "idem-adv-rebuild-2"),
                json={
                    "expected_proposal_revision": prop2_cur["proposal_revision"],
                    "request_text": "Xếp lại gọn trong ngày mai",
                },
            )
            assert r_reb.status_code == 201

            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "cooldown_14d")
            assert (
                await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            ).json()["data"] is None

            await seed_w4_a20_cases.seed_a20_scenario(url, email_a, "trigger_window_10")
            async with factory() as db:
                pref_after_14d = await db.scalar(
                    select(UserPreference).where(UserPreference.user_id == uid_a)
                )
                assert pref_after_14d is not None
                assert pref_after_14d.suggestion_cooldown_until is None
            r_w10_2 = await client.get("/api/v1/suggestions/current", headers=_bearer(uid_a))
            assert r_w10_2.status_code == 200
            assert r_w10_2.json()["data"]["status"] == "pending"
            assert r_w10_2.json()["data"]["suggested_value"] == 1.55

            # 5) --reset cuối cùng: xóa sạch A20, bảo toàn 100% W4-03A và cô lập User B
            await seed_w4_a20_cases.reset_a20_data(url, email_a)

            async with factory() as db:
                for model in (
                    AdaptationSuggestion,
                    SettingChangeRequest,
                    RescheduleProposal,
                    ScheduleVersion,
                    CalendarEventLink,
                    CalendarSyncJob,
                    Reminder,
                ):
                    assert (
                        await db.scalar(
                            select(func.count()).select_from(model).where(model.user_id == uid_a)
                        )
                        == 0
                    )
                final_a = await _snapshot_w4_03a_full(db, uid_a)
                final_b = await _snapshot_w4_03a_full(db, uid_b)
                assert final_a["counts"] == baseline_a["counts"]
                assert final_a["pref"] is not None and baseline_a["pref"] is not None
                assert final_a["pref"]["id"] == baseline_a["pref"]["id"]
                assert final_a["pref"]["buffer_multiplier"] == 1.2
                assert final_a["pref"]["pending_proposal_id"] is None
                assert final_a["pref"]["suggestion_cooldown_until"] is None
                assert final_b == baseline_b
