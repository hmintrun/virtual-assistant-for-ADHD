"""Bộ kiểm thử đối kháng (Adversarial QA Tests) độc lập cho 3 Phase sửa lỗi non-AI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import timezone as _tz
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db, make_engine, session_factory
from app.core.security import issue_app_access_token
from app.main import app
from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
)
from app.models.execution import SessionRun
from app.models.planning import PlanningDraft as PlanningDraftRow
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion as AdaptationSuggestionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.common import TimeConstraint, TimeInterval
from app.schemas.draft import PlanningDraft, TaskSummary
from app.schemas.user import CurrentUser
from app.services import plan_service
from app.services.calendar_service import CalendarService, _hash_state
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.google_calendar_client import CalendarAuthError, CalendarConflictError
from app.services.settings_service import get_current_suggestion

VN_TZ = _tz(timedelta(hours=7))


async def _make_mem_db():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def _override_get_db():
        async with factory() as session:
            yield session

    return engine, factory, _override_get_db


def _set_user_override(user_id: UUID, email: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=email or f"qa_{user_id.hex[:8]}@example.com",
        display_name="QA Tester",
        is_active=True,
    )
    return {"Authorization": f"Bearer qa-token-{user_id.hex[:8]}"}


def _daily_availability_dict(start_hm: str, end_hm: str, tz_name: str = "Asia/Ho_Chi_Minh") -> dict[str, Any]:
    return {
        "timezone": tz_name,
        "weekly_rules": [
            {"weekday": d, "start_local": start_hm, "end_local": end_hm}
            for d in range(1, 8)
        ],
        "date_overrides": [],
    }


def _weekly_rules_db(start_hm: str, end_hm: str) -> list[dict[str, Any]]:
    return [
        {"day_of_week": d, "start_local_time": start_hm, "end_local_time": end_hm}
        for d in range(7)
    ]


async def _seed_user_pref_avail(
    db: AsyncSession,
    *,
    work_sec: int = 1500,
    rest_sec: int = 300,
    buffer_mult: float = 1.2,
    avail_start: str = "08:00",
    avail_end: str = "22:00",
    schedule_rev: int = 1,
) -> tuple[UUID, UUID]:
    user_id = uuid4()
    avail_id = uuid4()
    db.add(
        User(
            id=user_id,
            email=f"qa_{user_id.hex[:8]}@example.com",
            hashed_password="x",
            display_name="QA User",
            is_active=True,
        )
    )
    await db.flush()
    db.add(
        UserPreferenceRow(
            user_id=user_id,
            revision=1,
            effective_revision=1,
            work_interval_seconds=work_sec,
            rest_interval_seconds=rest_sec,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=buffer_mult,
            reminder_style="gentle",
        )
    )
    db.add(
        AvailabilityRow(
            id=avail_id,
            user_id=user_id,
            revision=1,
            timezone="Asia/Ho_Chi_Minh",
            weekly_rules=_weekly_rules_db(avail_start, avail_end),
            date_overrides=[],
            task_limits=[],
            source="manual",
            confirmed_at=datetime(2026, 11, 1, 8, 0, tzinfo=VN_TZ),
        )
    )
    db.add(
        ScheduleVersionRow(
            user_id=user_id,
            version_number=schedule_rev,
            base_version=max(0, schedule_rev - 1),
            approved_at=datetime(2026, 11, 1, 9, 0, tzinfo=VN_TZ),
            affected_task_ids=[],
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )
    await db.flush()
    return user_id, avail_id


# =====================================================================
# PHASE 1: Adversarial Tests (UF6 / UF7 & State Machine)
# =====================================================================


@pytest.mark.asyncio
async def test_adv_p1_uf7_resegment_step_with_completed_session_and_buffer_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bẫy BUG: Step có 1 session completed (1500s) + 1 session scheduled (1500s).

    Khi tăng buffer_multiplier 1.2 -> 1.5 ở UF7, chỉ được co giãn phần scheduled còn lại
    (1500 / 1.2 * 1.5 = 1875s), KHÔNG được tính lại toàn bộ Step.remaining_base_seconds (2500 * 1.5 = 3750s)
    vì Step.remaining_base_seconds chỉ về 0 khi Step hoàn tất.
    """
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    base_now = datetime(2026, 11, 10, 8, 0, tzinfo=VN_TZ)
    monkeypatch.setattr("app.services.settings_service._utc_now", lambda: base_now)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or base_now).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, work_sec=1500, rest_sec=0, buffer_mult=1.2)
            task_id = uuid4()
            step_id = uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Task nhiều phiên",
                    scope="2 phiên",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-12T20:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    availability=_daily_availability_dict("08:00", "22:00"),
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=1,
                    title="Bước 1",
                    estimated_base_seconds=2500,
                    remaining_base_seconds=2500,  # Chưa hoàn tất bước nên checkin_service chưa đặt về 0
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="in_progress",
                )
            )
            await db.flush()
            # Session 1 đã completed (1500s work = 1250s base đã làm xong)
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=user_id,
                    step_id=step_id,
                    revision=2,
                    scheduled_start=datetime(2026, 11, 10, 8, 0, tzinfo=VN_TZ),
                    scheduled_end=datetime(2026, 11, 10, 8, 25, tzinfo=VN_TZ),
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="completed",
                    start_state="started",
                )
            )
            # Session 2 còn scheduled (1500s work)
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=user_id,
                    step_id=step_id,
                    revision=1,
                    scheduled_start=datetime(2026, 11, 10, 10, 0, tzinfo=VN_TZ),
                    scheduled_end=datetime(2026, 11, 10, 10, 25, tzinfo=VN_TZ),
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                )
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/settings/change-requests",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"buffer_multiplier": 1.5}},
                },
            )
            assert resp.status_code == 200, resp.text
            prop_id = resp.json()["data"]["pending_proposal_id"]
            assert prop_id is not None

            prop_resp = await client.get(f"/api/v1/proposals/{prop_id}", headers=headers)
            assert prop_resp.status_code == 200, prop_resp.text
            adds = prop_resp.json()["data"]["proposed_changes"]["sessions_to_add"]
            total_new_work = sum(item["after"]["work_seconds"] for item in adds)
            # Chỉ phần scheduled 1500s (1250s base * 1.5 = 1875s) được lên lịch lại, KHÔNG phải 3750s!
            assert total_new_work == 1875, (
                f"Đã tính lại cả Session 1 completed! Expected 1875s, got {total_new_work}s"
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p1_uf7_availability_moved_earlier_with_seg_same_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bẫy BUG: Dời Task.availability từ chiều (14:00-17:00) sang sáng (09:00-12:00)
    đồng thời tăng work_interval_seconds (1500 -> 1800, seg_same=True).

    Phải xếp được vào khung sáng 09:00-09:25, KHÔNG được dùng max(base_cursor, o_s)=14:00 bỏ qua buổi sáng!
    """
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    base_now = datetime(2026, 11, 10, 8, 0, tzinfo=VN_TZ)
    monkeypatch.setattr("app.services.settings_service._utc_now", lambda: base_now)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or base_now).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, work_sec=1500, rest_sec=0, buffer_mult=1.2)
            task_id = uuid4()
            step_id = uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Task dời sáng",
                    scope="1 phiên",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-10T12:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    availability=_daily_availability_dict("14:00", "17:00"),
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=1,
                    title="Bước 1",
                    estimated_base_seconds=1250,
                    remaining_base_seconds=1250,
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="pending",
                )
            )
            await db.flush()
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=user_id,
                    step_id=step_id,
                    revision=1,
                    scheduled_start=datetime(2026, 11, 10, 14, 0, tzinfo=VN_TZ),
                    scheduled_end=datetime(2026, 11, 10, 14, 25, tzinfo=VN_TZ),
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                )
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/settings/change-requests",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_preferences_revision": 1,
                    "target_task_id": str(task_id),
                    "expected_task_revision": 1,
                    "changes": {
                        "availability": _daily_availability_dict("09:00", "12:00"),
                        "preferences": {"work_interval_seconds": 1800},
                    },
                },
            )
            assert resp.status_code == 200, resp.text
            prop_id = resp.json()["data"]["pending_proposal_id"]
            assert prop_id is not None

            prop_resp = await client.get(f"/api/v1/proposals/{prop_id}", headers=headers)
            assert prop_resp.status_code == 200
            prop = prop_resp.json()["data"]
            assert prop["feasibility"]["status"] == "feasible", (
                f"Khung 09:00-12:00 đủ chỗ trước deadline 12:00 nhưng bị báo sai: {prop['feasibility']}"
            )
            assert prop["feasibility"]["confirmable"] is True
            new_windows = [
                (
                    datetime.fromisoformat(item["after"]["start"]),
                    datetime.fromisoformat(item["after"]["end"]),
                )
                for item in prop["proposed_changes"]["sessions_to_add"]
            ] + [
                (
                    datetime.fromisoformat(item["to_start"]),
                    datetime.fromisoformat(item["to_end"]),
                )
                for item in prop["proposed_changes"]["sessions_to_move"]
            ]
            assert new_windows, f"Phải có phiên được xếp lại vào khung sáng, nhận: {prop['proposed_changes']}"
            morning_start = datetime(2026, 11, 10, 9, 0, tzinfo=VN_TZ)
            morning_end = datetime(2026, 11, 10, 12, 0, tzinfo=VN_TZ)
            for ws, we in new_windows:
                assert morning_start <= ws and we <= morning_end, (
                    f"Phiên bị xếp ngoài khung sáng 09:00-12:00: {ws.isoformat()}..{we.isoformat()}"
                )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p1_uf7_availability_moved_earlier_with_seg_same_rest_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bẫy BUG seg_same=True: Dời Task.availability sang sáng (09:00-12:00) kèm đổi rest_interval_seconds=600."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    base_now = datetime(2026, 11, 10, 8, 0, tzinfo=VN_TZ)
    monkeypatch.setattr("app.services.settings_service._utc_now", lambda: base_now)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or base_now).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, work_sec=1500, rest_sec=300, buffer_mult=1.2)
            task_id = uuid4()
            step_id = uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Task dời sáng rest change",
                    scope="1 phiên",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-10T12:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    availability=_daily_availability_dict("14:00", "17:00"),
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=1,
                    title="Bước 1",
                    estimated_base_seconds=1250,
                    remaining_base_seconds=1250,
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="pending",
                )
            )
            await db.flush()
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=user_id,
                    step_id=step_id,
                    revision=1,
                    scheduled_start=datetime(2026, 11, 10, 14, 0, tzinfo=VN_TZ),
                    scheduled_end=datetime(2026, 11, 10, 14, 25, tzinfo=VN_TZ),
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                )
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/settings/change-requests",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_preferences_revision": 1,
                    "target_task_id": str(task_id),
                    "expected_task_revision": 1,
                    "changes": {
                        "availability": _daily_availability_dict("09:00", "12:00"),
                        "preferences": {"rest_interval_seconds": 600},
                    },
                },
            )
            assert resp.status_code == 200, resp.text
            prop_id = resp.json()["data"]["pending_proposal_id"]
            assert prop_id is not None

            prop_resp = await client.get(f"/api/v1/proposals/{prop_id}", headers=headers)
            assert prop_resp.status_code == 200
            prop = prop_resp.json()["data"]
            assert prop["feasibility"]["status"] == "feasible", (
                f"Khung 09:00-12:00 đủ chỗ nhưng seg_same=True bị báo sai: {prop['feasibility']}"
            )
            assert prop["feasibility"]["confirmable"] is True
            new_windows = [
                (
                    datetime.fromisoformat(item["after"]["start"]),
                    datetime.fromisoformat(item["after"]["end"]),
                )
                for item in prop["proposed_changes"]["sessions_to_add"]
            ] + [
                (
                    datetime.fromisoformat(item["to_start"]),
                    datetime.fromisoformat(item["to_end"]),
                )
                for item in prop["proposed_changes"]["sessions_to_move"]
            ]
            assert new_windows, f"Phải dời phiên 14:00 vào khung 09:00-12:00! changes={prop['proposed_changes']}"
            morning_start = datetime(2026, 11, 10, 9, 0, tzinfo=VN_TZ)
            morning_end = datetime(2026, 11, 10, 12, 0, tzinfo=VN_TZ)
            for ws, we in new_windows:
                assert morning_start <= ws and we <= morning_end, (
                    f"Phiên bị xếp ngoài khung sáng 09:00-12:00: {ws.isoformat()}..{we.isoformat()}"
                )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p1_uf6_continuation_shifted_session_exceeds_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bẫy BUG: Phiên continuation đẩy phiên phía sau vượt quá deadline_at của Task nhưng vẫn trả confirmable=True."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    now_ref = datetime(2026, 11, 2, 14, 25, 0, tzinfo=VN_TZ)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or now_ref).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, schedule_rev=3)
            task_id = uuid4()
            step_id = uuid4()
            sess1_id = uuid4()
            sess2_id = uuid4()
            run1_id = uuid4()
            # Deadline đúng 15:05 - chỉ đủ cho sess2 ở 14:40-15:05 nếu không bị đẩy!
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Task deadline sát",
                    scope="Gấp",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-02T15:05:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    availability=_daily_availability_dict("08:00", "22:00"),
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=1,
                    title="Bước 1",
                    estimated_base_seconds=1800,
                    remaining_base_seconds=600,
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="in_progress",
                )
            )
            await db.flush()
            db.add_all(
                [
                    SessionRow(
                        id=sess1_id,
                        user_id=user_id,
                        step_id=step_id,
                        revision=2,
                        scheduled_start=datetime(2026, 11, 2, 14, 0, tzinfo=VN_TZ),
                        scheduled_end=datetime(2026, 11, 2, 14, 25, tzinfo=VN_TZ),
                        micro_task_seconds=120,
                        work_seconds=1500,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        duration_seconds=1500,
                        schedule_revision=3,
                        status="incomplete",
                        start_state="started",
                    ),
                    SessionRow(
                        id=sess2_id,
                        user_id=user_id,
                        step_id=step_id,
                        revision=1,
                        scheduled_start=datetime(2026, 11, 2, 14, 40, tzinfo=VN_TZ),
                        scheduled_end=datetime(2026, 11, 2, 15, 5, tzinfo=VN_TZ),
                        micro_task_seconds=120,
                        work_seconds=1500,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        duration_seconds=1500,
                        schedule_revision=3,
                        status="scheduled",
                        start_state="waiting",
                    ),
                ]
            )
            await db.flush()
            db.add(
                SessionRun(
                    id=run1_id,
                    user_id=user_id,
                    session_id=sess1_id,
                    revision=2,
                    device_id="dev-1",
                    started_at=datetime(2026, 11, 2, 14, 0, tzinfo=VN_TZ),
                    ended_at=datetime(2026, 11, 2, 14, 25, tzinfo=VN_TZ),
                    run_state="closed",
                    active_phase="ended",
                    segment_index=0,
                    work_elapsed_seconds=1500,
                    rest_elapsed_seconds=0,
                    actual_work_seconds=1500,
                    actual_source="timer_proxy",
                    outcome="incomplete",
                    settings_snapshot={"work_seconds": 1500, "rest_seconds": 0},
                )
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ctx_resp = await client.get(f"/api/v1/runs/{run1_id}/upcoming-schedule-context", headers=headers)
            assert ctx_resp.status_code == 200, ctx_resp.text
            ctx_data = ctx_resp.json()["data"]
            src_revs = ctx_data["source_revisions"]
            src_run_rev = ctx_data["source_run_revision"]

            resp = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(task_id),
                    "allowed_task_ids": [str(task_id)],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": src_revs,
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(run1_id),
                        "expected_run_revision": src_run_rev,
                        "additional_work_seconds": 900,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                    },
                },
            )
            # Nếu continuation làm sess2 bị đẩy qua deadline 15:05, KHÔNG ĐƯỢC trả về feasible=True, confirmable=True!
            if resp.status_code == 201:
                prop = resp.json()["data"]
                assert prop["feasibility"]["confirmable"] is False, (
                    f"Phiên sess2 bị đẩy quá deadline 15:05 nhưng vẫn confirmable=True: "
                    f"moves={prop['proposed_changes']['sessions_to_move']}"
                )
            else:
                err_code = resp.json().get("error", {}).get("code")
                assert resp.status_code in (409, 422) and err_code != "REVISION_CONFLICT", resp.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p1_uf7_rebuild_after_reopen_and_reject_preserves_old_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kiểm tra đóng/mở lại app (rebuild) giữ nguyên override UF7 & reject giữ nguyên lịch cũ + xóa requested_*."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    t0 = datetime(2026, 11, 10, 8, 0, tzinfo=VN_TZ)
    monkeypatch.setattr("app.services.settings_service._utc_now", lambda: t0)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or t0).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, work_sec=1500, rest_sec=300, buffer_mult=1.2)
            task_id = uuid4()
            step_id = uuid4()
            sess_id = uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Task UF7 rebuild/reject",
                    scope="2 phiên",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-12T20:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    availability=_daily_availability_dict("08:00", "22:00"),
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=1,
                    title="Bước 1",
                    estimated_base_seconds=2500,
                    remaining_base_seconds=2500,
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="pending",
                )
            )
            await db.flush()
            orig_start = datetime(2026, 11, 10, 10, 0, tzinfo=VN_TZ)
            orig_end = datetime(2026, 11, 10, 10, 25, tzinfo=VN_TZ)
            db.add(
                SessionRow(
                    id=sess_id,
                    user_id=user_id,
                    step_id=step_id,
                    revision=1,
                    scheduled_start=orig_start,
                    scheduled_end=orig_end,
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                )
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            cr = await client.post(
                "/api/v1/settings/change-requests",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"work_interval_seconds": 900}},
                },
            )
            assert cr.status_code == 200, cr.text
            prop_id = cr.json()["data"]["pending_proposal_id"]
            assert prop_id is not None

            # Official Schedule chưa được chạm vào trước khi approve!
            async with factory() as db:
                sess_mid = await db.get(SessionRow, sess_id)
                pref_mid = await db.scalar(
                    select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id)
                )
                assert sess_mid is not None
                assert sess_mid.work_seconds == 1500
                assert sess_mid.status == "scheduled"
                assert pref_mid is not None
                assert pref_mid.work_interval_seconds == 1500
                assert pref_mid.requested_revision == 2

            # Giả lập thời gian trôi qua đến 10:15 (làm proposal cũ bị stale khi mở lại app)
            t1 = datetime(2026, 11, 10, 10, 15, tzinfo=VN_TZ)
            monkeypatch.setattr(
                "app.services.reschedule_service._current_utc_now",
                lambda now=None: (now or t1).astimezone(UTC),
            )
            rebuilt = await client.post(
                f"/api/v1/proposals/{prop_id}/rebuild",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={"expected_proposal_revision": 1},
            )
            assert rebuilt.status_code == 201, rebuilt.text
            rebuilt_data = rebuilt.json()["data"]
            assert rebuilt_data["proposal_revision"] == 2
            adds = rebuilt_data["proposed_changes"]["sessions_to_add"]
            assert adds and all(
                all(
                    seg["duration_seconds"] <= 900
                    for seg in item["after"]["segments"]
                    if seg["kind"] == "work"
                )
                for item in adds
            )
            assert all(
                datetime.fromisoformat(item["after"]["start"]) >= t1 for item in adds
            )

            # Reject proposal -> lịch cũ & preference effective giữ nguyên, requested_* dọn sạch
            rej = await client.post(
                f"/api/v1/proposals/{prop_id}/reject",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={"expected_proposal_revision": 2, "reason": "Giữ lịch cũ"},
            )
            assert rej.status_code == 200, rej.text
            assert rej.json()["data"]["status"] == "rejected"

            async with factory() as db:
                sess_after = await db.get(SessionRow, sess_id)
                pref_after = await db.scalar(
                    select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id)
                )
                assert sess_after is not None and sess_after.status == "scheduled"
                assert sess_after.scheduled_start.replace(tzinfo=UTC) == orig_start.astimezone(UTC)
                assert pref_after is not None
                assert pref_after.work_interval_seconds == 1500
                assert pref_after.effective_revision == 1
                assert pref_after.requested_revision is None
                assert pref_after.requested_changes is None
                assert pref_after.pending_proposal_id is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p1_uf6_continuation_waiting_start_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kiểm tra biên gap_exceptions (120s immediate, 780s borrowed_break) & start sớm >15p khi start_continuation=False."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    t_end_run1 = datetime(2026, 11, 2, 14, 25, 0, tzinfo=VN_TZ)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or t_end_run1).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, schedule_rev=3)
            task_id = uuid4()
            step_id = uuid4()
            sess1_id = uuid4()
            sess2_id = uuid4()
            run1_id = uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Task boundary continuation",
                    scope="Biên gap",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-05T18:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    availability=_daily_availability_dict("08:00", "22:00"),
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=1,
                    title="Bước 1",
                    estimated_base_seconds=2400,
                    remaining_base_seconds=900,
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="in_progress",
                )
            )
            await db.flush()
            db.add_all(
                [
                    SessionRow(
                        id=sess1_id,
                        user_id=user_id,
                        step_id=step_id,
                        revision=2,
                        scheduled_start=datetime(2026, 11, 2, 14, 0, tzinfo=VN_TZ),
                        scheduled_end=t_end_run1,
                        micro_task_seconds=120,
                        work_seconds=1500,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        duration_seconds=1500,
                        schedule_revision=3,
                        status="incomplete",
                        start_state="started",
                    ),
                    # Sess2 ở 14:55 (sau 14:25 + 2p + 15p + 13p=780s! Đúng biên 780s khi borrow 120s cho 15p continuation)
                    SessionRow(
                        id=sess2_id,
                        user_id=user_id,
                        step_id=step_id,
                        revision=1,
                        scheduled_start=datetime(2026, 11, 2, 14, 55, tzinfo=VN_TZ),
                        scheduled_end=datetime(2026, 11, 2, 15, 20, tzinfo=VN_TZ),
                        micro_task_seconds=120,
                        work_seconds=1500,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        duration_seconds=1500,
                        schedule_revision=3,
                        status="scheduled",
                        start_state="waiting",
                    ),
                ]
            )
            await db.flush()
            db.add(
                SessionRun(
                    id=run1_id,
                    user_id=user_id,
                    session_id=sess1_id,
                    revision=2,
                    device_id="dev-1",
                    started_at=datetime(2026, 11, 2, 14, 0, tzinfo=VN_TZ),
                    ended_at=t_end_run1,
                    run_state="closed",
                    active_phase="ended",
                    segment_index=0,
                    work_elapsed_seconds=1500,
                    rest_elapsed_seconds=0,
                    actual_work_seconds=1500,
                    actual_source="timer_proxy",
                    outcome="incomplete",
                    settings_snapshot={"work_seconds": 1500, "rest_seconds": 0},
                )
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ctx_resp = await client.get(f"/api/v1/runs/{run1_id}/upcoming-schedule-context", headers=headers)
            assert ctx_resp.status_code == 200
            ctx_data = ctx_resp.json()["data"]
            src_revs = ctx_data["source_revisions"]
            src_run_rev = ctx_data["source_run_revision"]

            # Borrow 120s -> continuation 14:27..14:42, gap trước sess2(14:55) = 780s (>= 900 - 120)
            p_resp = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(task_id),
                    "allowed_task_ids": [str(task_id)],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": src_revs,
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(run1_id),
                        "expected_run_revision": src_run_rev,
                        "additional_work_seconds": 900,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 120,
                    },
                },
            )
            assert p_resp.status_code == 201, p_resp.text
            prop = p_resp.json()["data"]

            appr = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_proposal_revision": prop["proposal_revision"],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                    "start_continuation": False,
                },
            )
            assert appr.status_code == 200, appr.text
            appr_data = appr.json()["data"]
            cont_id = appr_data["continuation_session_id"] or next(
                s["session_id"] for s in appr_data["sessions"] if s["session_id"] != str(sess2_id)
            )
            new_sched_rev = appr_data["updated_schedule_revision"]

            # Subcase A: Start sớm trước immediate gap 120s (14:25 + 30s = 14:25:30) -> bị chặn 409
            r_too_early = await client.post(
                f"/api/v1/sessions/{cont_id}/start",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_schedule_revision": new_sched_rev,
                    "device_id": "dev-1",
                    "requested_start_at": (t_end_run1 + timedelta(seconds=30)).isoformat(),
                },
            )
            assert r_too_early.status_code == 409

            # Subcase B: Start sớm >15p trước sess2 trên session thường (lệch >900s) -> bị chặn 409
            r_sess2_early = await client.post(
                f"/api/v1/sessions/{sess2_id}/start",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_schedule_revision": new_sched_rev,
                    "device_id": "dev-1",
                    "requested_start_at": datetime(2026, 11, 2, 14, 30, 0, tzinfo=VN_TZ).isoformat(),
                },
            )
            assert r_sess2_early.status_code == 409

            # Subcase C: Start đúng giờ 14:27 (cách sess2 780s, thỏa gap_exceptions) -> 201 Created
            r_ok = await client.post(
                f"/api/v1/sessions/{cont_id}/start",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "expected_schedule_revision": new_sched_rev,
                    "device_id": "dev-1",
                    "requested_start_at": (t_end_run1 + timedelta(seconds=120)).isoformat(),
                },
            )
            assert r_ok.status_code == 201, r_ok.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p1_uf6_infeasible_does_not_mutate_other_locked_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Khi Task A thiếu giờ (infeasible), Task B đã chốt tuyệt đối không bị dời hoặc sửa đổi."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db
    now_ref = datetime(2026, 11, 10, 10, 0, tzinfo=VN_TZ)
    monkeypatch.setattr(
        "app.services.reschedule_service._current_utc_now",
        lambda now=None: (now or now_ref).astimezone(UTC),
    )

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db, avail_start="09:00", avail_end="12:00")
            task_a, step_a, sess_a = uuid4(), uuid4(), uuid4()
            task_b, step_b, sess_b = uuid4(), uuid4(), uuid4()
            b_start = datetime(2026, 11, 10, 10, 0, tzinfo=VN_TZ)
            b_end = datetime(2026, 11, 10, 11, 30, tzinfo=VN_TZ)

            db.add_all(
                [
                    TaskRow(
                        id=task_a,
                        user_id=user_id,
                        revision=1,
                        title="Task A thiếu chỗ",
                        scope="A",
                        time_constraint={
                            "mode": "deadline",
                            "at": "2026-11-10T11:45:00+07:00",
                            "timezone": "Asia/Ho_Chi_Minh",
                        },
                        availability=_daily_availability_dict("09:00", "12:00"),
                        status="in_progress",
                        decomposition_state="complete",
                    ),
                    TaskRow(
                        id=task_b,
                        user_id=user_id,
                        revision=1,
                        title="Task B cố định",
                        scope="B",
                        time_constraint={
                            "mode": "deadline",
                            "at": "2026-11-11T18:00:00+07:00",
                            "timezone": "Asia/Ho_Chi_Minh",
                        },
                        availability=_daily_availability_dict("09:00", "12:00"),
                        status="in_progress",
                        decomposition_state="complete",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    StepRow(
                        id=step_a,
                        user_id=user_id,
                        task_id=task_a,
                        revision=1,
                        order_index=1,
                        title="Step A",
                        estimated_base_seconds=3600,
                        remaining_base_seconds=3600,
                        buffer_multiplier=1.2,
                        estimate_source="user",
                        status="in_progress",
                    ),
                    StepRow(
                        id=step_b,
                        user_id=user_id,
                        task_id=task_b,
                        revision=1,
                        order_index=1,
                        title="Step B",
                        estimated_base_seconds=5400,
                        remaining_base_seconds=5400,
                        buffer_multiplier=1.2,
                        estimate_source="user",
                        status="pending",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    SessionRow(
                        id=sess_a,
                        user_id=user_id,
                        step_id=step_a,
                        revision=1,
                        scheduled_start=datetime(2026, 11, 10, 9, 0, tzinfo=VN_TZ),
                        scheduled_end=datetime(2026, 11, 10, 9, 45, tzinfo=VN_TZ),
                        micro_task_seconds=120,
                        work_seconds=2700,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 2700}],
                        duration_seconds=2700,
                        schedule_revision=1,
                        status="scheduled",
                        start_state="waiting",
                    ),
                    SessionRow(
                        id=sess_b,
                        user_id=user_id,
                        step_id=step_b,
                        revision=4,
                        scheduled_start=b_start,
                        scheduled_end=b_end,
                        micro_task_seconds=120,
                        work_seconds=5400,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 5400}],
                        duration_seconds=5400,
                        schedule_revision=1,
                        status="scheduled",
                        start_state="waiting",
                    ),
                ]
            )
            await db.commit()

        headers = _set_user_override(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": f"idem-{uuid4()}"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(task_a),
                    "allowed_task_ids": [str(task_a)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert resp.status_code == 201, resp.text
            prop = resp.json()["data"]
            assert prop["feasibility"]["status"] == "infeasible"
            assert prop["feasibility"]["confirmable"] is False
            assert (prop["feasibility"]["deficit_seconds"] or 0) > 0
            # Không được đụng vào Task B trong proposed_changes
            moved_ids = {m["session_id"] for m in prop["proposed_changes"]["sessions_to_move"]}
            cancelled_ids = {c["session_id"] for c in prop["proposed_changes"]["sessions_to_cancel"]}
            assert str(sess_b) not in moved_ids
            assert str(sess_b) not in cancelled_ids

        async with factory() as db:
            sess_b_db = await db.get(SessionRow, sess_b)
            assert sess_b_db is not None
            assert sess_b_db.revision == 1
            assert sess_b_db.scheduled_start.replace(tzinfo=UTC) == b_start.astimezone(UTC)
            assert sess_b_db.scheduled_end.replace(tzinfo=UTC) == b_end.astimezone(UTC)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =====================================================================
# PHASE 2: Preferences 503, Refresh Token Security & A20 Window
# =====================================================================


@pytest.mark.asyncio
async def test_adv_p2_preferences_new_user_auto_create_and_rollback_safety() -> None:
    """User mới chưa có UserPreference: GET /preferences trả 200 idempotent, commit_if_created=False tôn trọng rollback."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db

    try:
        async with factory() as db:
            user1 = uuid4()
            user2 = uuid4()
            db.add_all(
                [
                    User(id=user1, email=f"u1_{user1.hex[:6]}@ex.com", display_name="U1", is_active=True),
                    User(id=user2, email=f"u2_{user2.hex[:6]}@ex.com", display_name="U2", is_active=True),
                ]
            )
            await db.commit()

        # 1. commit_if_created=False bên trong transaction đã có DML + rollback không được ghi vĩnh viễn vào DB
        async with factory() as db:
            async with db.begin():
                u_obj = await db.get(User, user1)
                assert u_obj is not None
                u_obj.display_name = "U1-dirty"
                await db.flush()  # Đảm bảo SQLite đã mở transaction thật trước SAVEPOINT begin_nested()
                pref_temp = await plan_service.get_preferences(
                    user_id=str(user1), db=db, commit_if_created=False
                )
                assert pref_temp.work_interval_seconds == 1500
                await db.rollback()

        async with factory() as db:
            count_after_rb = (
                await db.scalars(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user1))
            ).all()
            assert len(count_after_rb) == 0

        # 2. GET /api/v1/preferences cho user2 chưa có row trả 200 & idempotent
        headers = _set_user_override(user2)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/v1/preferences", headers=headers)
            assert r1.status_code == 200, r1.text
            r2 = await client.get("/api/v1/preferences", headers=headers)
            assert r2.status_code == 200, r2.text
            assert r1.json()["data"]["effective_revision"] == r2.json()["data"]["effective_revision"]

        async with factory() as db:
            rows = (
                await db.scalars(select(UserPreferenceRow).where(UserPreferenceRow.user_id == user2))
            ).all()
            assert len(rows) == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p2_refresh_token_security_traps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bẫy bảo mật toàn diện cho Refresh Token, JWT claims, inactive/deleted user và type confusion."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db

    monkeypatch.setattr(settings, "AUTH_MODE", "google")
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "qa-strong-jwt-secret-key-at-least-32-bytes-long")
    monkeypatch.setattr(settings, "GOOGLE_SIGN_IN_AUDIENCES", ["test.apps.googleusercontent.com"])

    try:
        async with factory() as db:
            active_uid = uuid4()
            inactive_uid = uuid4()
            db.add_all(
                [
                    User(
                        id=active_uid,
                        email=f"act_{active_uid.hex[:6]}@ex.com",
                        display_name="Active",
                        is_active=True,
                    ),
                    User(
                        id=inactive_uid,
                        email=f"inact_{inactive_uid.hex[:6]}@ex.com",
                        display_name="Inactive",
                        is_active=False,
                    ),
                ]
            )
            await db.commit()

        bundle = issue_app_access_token(active_uid)
        assert bundle.refresh_token is not None
        assert bundle.refresh_expires_in == settings.REFRESH_TOKEN_TTL_SECONDS == 30 * 86400

        now_ts = int(datetime.now(UTC).timestamp())
        base_claims = {
            "sub": str(active_uid),
            "iat": now_ts - 60,
            "exp": now_ts + 3600,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "token_type": "refresh",
            "jti": str(uuid4()),
        }

        expired_tok = jwt.encode(
            {**base_claims, "iat": now_ts - 7200, "exp": now_ts - 600},
            settings.JWT_SECRET_KEY,
            algorithm="HS256",
        )
        wrong_sig_tok = jwt.encode(base_claims, "completely-different-secret-key-32-bytes", algorithm="HS256")
        alg_none_tok = jwt.encode(base_claims, "", algorithm="none") if False else (
            "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
            + bundle.refresh_token.split(".")[1]
            + "."
        )
        no_jti_tok = jwt.encode(
            {k: v for k, v in base_claims.items() if k != "jti"},
            settings.JWT_SECRET_KEY,
            algorithm="HS256",
        )
        blank_jti_tok = jwt.encode({**base_claims, "jti": "   "}, settings.JWT_SECRET_KEY, algorithm="HS256")
        future_iat_tok = jwt.encode(
            {**base_claims, "iat": now_ts + 3600, "exp": now_ts + 7200},
            settings.JWT_SECRET_KEY,
            algorithm="HS256",
        )
        inactive_bundle = issue_app_access_token(inactive_uid)
        deleted_bundle = issue_app_access_token(uuid4())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Access token gửi vào /auth/refresh -> 401
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": bundle.access_token})
            assert r.status_code == 401

            # 2. Refresh token gửi làm Bearer vào /auth/me -> 401
            r = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {bundle.refresh_token}"},
            )
            assert r.status_code == 401

            # 3-7. Token giả / hết hạn / thiếu jti / blank jti / future iat / alg=none -> 401
            for bad_tok in (expired_tok, wrong_sig_tok, alg_none_tok, no_jti_tok, blank_jti_tok, future_iat_tok):
                r = await client.post("/api/v1/auth/refresh", json={"refresh_token": bad_tok})
                assert r.status_code == 401, f"Token đáng lẽ phải bị 401 nhưng nhận {r.status_code}: {bad_tok}"

            # 8. Padded whitespace -> 422
            r = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": f" {bundle.refresh_token} "}
            )
            assert r.status_code == 422

            # 9. User bị vô hiệu hóa -> 403 (hoặc 401)
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": inactive_bundle.refresh_token})
            assert r.status_code in (401, 403)

            # 10. User đã bị xóa -> 401
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": deleted_bundle.refresh_token})
            assert r.status_code == 401

            # 11. Refresh token hợp lệ -> 200 & cấp cặp token mới
            r_ok = await client.post("/api/v1/auth/refresh", json={"refresh_token": bundle.refresh_token})
            assert r_ok.status_code == 200, r_ok.text
            assert r_ok.json()["data"]["access_token"]
            assert r_ok.json()["data"]["refresh_token"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p2_a20_boundary_6_vs_7_and_latest_10_sliding_window() -> None:
    """Biên 6 vs 7 mẫu sạch và cửa sổ trượt chỉ xét 10 mẫu GẦN NHẤT (bỏ mẫu cũ cực đoan)."""
    engine, factory, override_get_db = await _make_mem_db()
    app.dependency_overrides[get_db] = override_get_db

    async def _seed_completed_step(
        db: AsyncSession,
        *,
        user_id: UUID,
        task_id: UUID,
        order_idx: int,
        est_base: int,
        actual_work: int,
        ended_at: datetime,
    ) -> None:
        step_id = uuid4()
        sess_id = uuid4()
        run_id = uuid4()
        db.add(
            StepRow(
                id=step_id,
                user_id=user_id,
                task_id=task_id,
                revision=1,
                order_index=order_idx,
                title=f"Step {order_idx}",
                estimated_base_seconds=est_base,
                remaining_base_seconds=0,
                buffer_multiplier=1.2,
                estimate_source="user",
                status="completed",
            )
        )
        await db.flush()
        db.add(
            SessionRow(
                id=sess_id,
                user_id=user_id,
                step_id=step_id,
                revision=1,
                scheduled_start=ended_at - timedelta(seconds=actual_work),
                scheduled_end=ended_at,
                micro_task_seconds=120,
                work_seconds=actual_work,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": actual_work}],
                duration_seconds=actual_work,
                schedule_revision=1,
                status="completed",
                start_state="started",
            )
        )
        await db.flush()
        db.add(
            SessionRun(
                id=run_id,
                user_id=user_id,
                session_id=sess_id,
                revision=1,
                device_id="dev-1",
                started_at=ended_at - timedelta(seconds=actual_work),
                ended_at=ended_at,
                run_state="closed",
                active_phase="ended",
                segment_index=0,
                work_elapsed_seconds=actual_work,
                rest_elapsed_seconds=0,
                actual_work_seconds=actual_work,
                actual_source="timer_proxy",
                outcome="completed",
                settings_snapshot={"work_seconds": actual_work, "rest_seconds": 0},
            )
        )
        await db.flush()

    try:
        # Case 1: Đúng 6 mẫu lệch cao -> null; thêm mẫu thứ 7 -> sinh suggestion & GET lặp idempotent
        async with factory() as db:
            user1, _ = await _seed_user_pref_avail(db, buffer_mult=1.2)
            task1 = uuid4()
            db.add(
                TaskRow(
                    id=task1,
                    user_id=user1,
                    revision=1,
                    title="Task A20 6vs7",
                    scope="A20",
                    time_constraint={"mode": "deadline", "at": "2026-11-20T18:00:00+07:00", "timezone": "Asia/Ho_Chi_Minh"},
                    status="completed",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            base_t = datetime(2026, 11, 1, 10, 0, tzinfo=VN_TZ)
            for idx in range(6):
                await _seed_completed_step(
                    db,
                    user_id=user1,
                    task_id=task1,
                    order_idx=idx,
                    est_base=1000,
                    actual_work=1800,
                    ended_at=base_t + timedelta(hours=idx),
                )
            await db.commit()

        async with factory() as db:
            assert await get_current_suggestion(db, user_id=str(user1), kind="buffer_multiplier") is None

        async with factory() as db:
            await _seed_completed_step(
                db,
                user_id=user1,
                task_id=task1,
                order_idx=6,
                est_base=1000,
                actual_work=1800,
                ended_at=base_t + timedelta(hours=6),
            )
            await db.commit()

        headers1 = _set_user_override(user1)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            s1 = await client.get("/api/v1/suggestions/current?kind=buffer_multiplier", headers=headers1)
            s2 = await client.get("/api/v1/suggestions/current?kind=buffer_multiplier", headers=headers1)
            assert s1.status_code == 200 and s2.status_code == 200
            d1, d2 = s1.json()["data"], s2.json()["data"]
            assert d1 is not None and d1["sample_count"] == 7
            assert d2 is not None and d2["suggestion_id"] == d1["suggestion_id"]

        async with factory() as db:
            cnt = len(
                (
                    await db.scalars(
                        select(AdaptationSuggestionRow).where(AdaptationSuggestionRow.user_id == user1)
                    )
                ).all()
            )
            assert cnt == 1

        # Case 2: 2 mẫu CŨ có tỷ lệ cực đoan (2.5), 10 mẫu GẦN NHẤT có tỷ lệ 1.2 (khớp buffer hiện tại 1.2) -> null!
        async with factory() as db:
            user2, _ = await _seed_user_pref_avail(db, buffer_mult=1.2)
            task2 = uuid4()
            db.add(
                TaskRow(
                    id=task2,
                    user_id=user2,
                    revision=1,
                    title="Task A20 Window 10",
                    scope="A20",
                    time_constraint={"mode": "deadline", "at": "2026-11-20T18:00:00+07:00", "timezone": "Asia/Ho_Chi_Minh"},
                    status="completed",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            base_t2 = datetime(2026, 10, 20, 9, 0, tzinfo=VN_TZ)
            # 2 mẫu cũ cực đoan (ratio 2.5)
            for idx in range(2):
                await _seed_completed_step(
                    db,
                    user_id=user2,
                    task_id=task2,
                    order_idx=idx,
                    est_base=1000,
                    actual_work=2500,
                    ended_at=base_t2 + timedelta(hours=idx),
                )
            # 10 mẫu gần nhất chuẩn 1.2
            for idx in range(2, 12):
                await _seed_completed_step(
                    db,
                    user_id=user2,
                    task_id=task2,
                    order_idx=idx,
                    est_base=1000,
                    actual_work=1200,
                    ended_at=base_t2 + timedelta(days=1, hours=idx),
                )
            await db.commit()

        async with factory() as db:
            s_win = await get_current_suggestion(db, user_id=str(user2), kind="buffer_multiplier")
            assert s_win is None, f"Cửa sổ trượt 10 phải bỏ 2 mẫu cũ cực đoan nhưng lại gợi ý: {s_win}"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =====================================================================
# PHASE 3: Calendar Sync Worker, Backfill, Health & Error Envelope
# =====================================================================


@pytest.mark.asyncio
async def test_adv_p3_sync_worker_409_missing_event_and_auth_error_no_retry_overwrite() -> None:
    """409 khi create mà get_event=None phải về 'conflict'; CalendarAuthError -> failed & batch sau không ghi đè."""
    engine, factory, _ = await _make_mem_db()

    class _Fake409MissingClient:
        async def create_event(self, **kwargs):
            raise CalendarConflictError("409 conflict")

        async def get_event(self, **kwargs):
            return None

    class _FakeAuthErrClient:
        async def create_event(self, **kwargs):
            raise CalendarAuthError("revoked")

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db)
            task_id, step_id, sess_id = uuid4(), uuid4(), uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    revision=1,
                    title="Sync task",
                    scope="1",
                    time_constraint={"mode": "deadline", "at": "2026-11-20T18:00:00+07:00", "timezone": "Asia/Ho_Chi_Minh"},
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    revision=1,
                    order_index=0,
                    title="S1",
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.2,
                    estimate_source="user",
                    status="pending",
                )
            )
            await db.flush()
            db.add(
                SessionRow(
                    id=sess_id,
                    user_id=user_id,
                    step_id=step_id,
                    revision=1,
                    scheduled_start=datetime(2026, 11, 10, 9, 0, tzinfo=VN_TZ),
                    scheduled_end=datetime(2026, 11, 10, 9, 25, tzinfo=VN_TZ),
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

            conn = CalendarConnection(
                id=uuid4(),
                user_id=user_id,
                revision=1,
                provider="google",
                account_ref="sync@example.com",
                read_calendar_ids=["primary"],
                write_calendar_id="proplan_cal",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref='{"access_token":"tok"}',
            )
            db.add(conn)
            await db.flush()

            link1 = CalendarEventLink(
                id=uuid4(),
                user_id=user_id,
                revision=1,
                session_id=sess_id,
                connection_id=conn.id,
                google_calendar_id="proplan_cal",
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            db.add(link1)
            await db.flush()

            job_409 = CalendarSyncJob(
                id=uuid4(),
                user_id=user_id,
                revision=1,
                connection_id=conn.id,
                event_link_id=link1.id,
                target_kind="session",
                target_ref=str(sess_id),
                operation="create",
                target_schedule_revision=1,
                payload_snapshot={
                    "id": "evt-missing",
                    "summary": "Work",
                    "start": "2026-11-10T09:00:00+07:00",
                    "end": "2026-11-10T09:25:00+07:00",
                },
                idempotency_key=f"sync-409-{uuid4()}",
                job_state="queued",
                attempt_count=0,
            )
            job_auth = CalendarSyncJob(
                id=uuid4(),
                user_id=user_id,
                revision=1,
                connection_id=conn.id,
                event_link_id=link1.id,
                target_kind="session",
                target_ref=str(sess_id),
                operation="create",
                target_schedule_revision=1,
                payload_snapshot={
                    "id": "evt-auth",
                    "summary": "Work",
                    "start": "2026-11-10T09:00:00+07:00",
                    "end": "2026-11-10T09:25:00+07:00",
                },
                idempotency_key=f"sync-auth-{uuid4()}",
                job_state="queued",
                attempt_count=0,
            )
            db.add_all([job_409, job_auth])
            await db.commit()

            cal_svc = CalendarService(db)
            cal_svc.client = _Fake409MissingClient()  # type: ignore[assignment]
            res1 = await process_sync_job(db, job_409, cal_svc)
            assert res1 == "conflict"
            j409_db = await db.get(CalendarSyncJob, job_409.id)
            l1_db = await db.get(CalendarEventLink, link1.id)
            assert j409_db is not None and j409_db.job_state == "conflict"
            assert l1_db is not None and l1_db.sync_state == "conflict"

            cal_svc.client = _FakeAuthErrClient()  # type: ignore[assignment]
            res2 = await process_sync_job(db, job_auth, cal_svc)
            assert res2 == "failed"

            jauth_db = await db.get(CalendarSyncJob, job_auth.id)
            conn_db = await db.get(CalendarConnection, conn.id)
            assert jauth_db is not None and jauth_db.job_state == "failed"
            assert jauth_db.last_error_code == "AUTH_EXPIRED"
            assert jauth_db.attempt_count == 5
            assert conn_db is not None and conn_db.connection_state == "needs_reconnect"

            # Batch quét sau đó KHÔNG được nhặt lại hoặc ghi đè retry_wait/queued
            picked = await process_calendar_sync_jobs(db, user_id=user_id, limit=10)
            assert picked == 0
            jauth_after = await db.get(CalendarSyncJob, job_auth.id)
            assert jauth_after is not None and jauth_after.job_state == "failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p3_gcal_unknown_backfill_and_disconnected_source_ignored() -> None:
    """handle_oauth_callback backfill row unknown@google.com và plan_service._sources bỏ qua connection disconnected."""
    engine, factory, _ = await _make_mem_db()

    class _FakeOAuthClient:
        async def exchange_auth_code(self, code: str, redirect_uri: str | None = None) -> dict[str, Any]:
            # Giả lập client đã fallback calendarList/primary ra email thật
            return {
                "access_token": "acc_1",
                "refresh_token": "ref_1",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/calendar",
                "account_ref": "real.user@gmail.com",
            }

        async def ensure_secondary_calendar(self, access_token: str, summary: str = "ProPlan Focus") -> str:
            return "proplan_secondary_id"

    try:
        async with factory() as db:
            user_id, _ = await _seed_user_pref_avail(db)
            legacy_conn = CalendarConnection(
                id=uuid4(),
                user_id=user_id,
                revision=1,
                provider="google",
                account_ref="unknown@google.com",
                read_calendar_ids=["primary"],
                write_calendar_id="proplan_secondary_id",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="needs_reconnect",
                credential_ref='{"access_token":"old"}',
            )
            db.add(legacy_conn)
            raw_state = "qa-oauth-state-123"
            db.add(
                CalendarAuthorization(
                    id=uuid4(),
                    user_id=user_id,
                    revision=1,
                    state_hash=_hash_state(raw_state),
                    redirect_uri="http://localhost:8000/api/v1/calendar/oauth/callback",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
            await db.commit()

            svc = CalendarService(db)
            svc.client = _FakeOAuthClient()  # type: ignore[assignment]
            conn, _ = await svc.handle_oauth_callback(
                user_id=user_id, state=raw_state, code="auth_code_ok"
            )
            assert conn.id == legacy_conn.id
            assert conn.account_ref == "real.user@gmail.com"
            assert conn.connection_state == "connected"

            all_conns = (
                await db.scalars(select(CalendarConnection).where(CalendarConnection.user_id == user_id))
            ).all()
            assert len(all_conns) == 1

            # Nếu disconnected, plan_service._sources không được dùng connection đó làm nguồn lịch đang kết nối
            conn.connection_state = "disconnected"
            await db.commit()
            now_live = datetime.now(UTC)
            draft_row_id = uuid4()
            db.add(
                PlanningDraftRow(
                    id=draft_row_id,
                    user_id=user_id,
                    revision=1,
                    client_draft_id="qa-draft-1",
                    stage="confirmed",
                    input_source="text",
                    summary={},
                    summary_revision=1,
                    summary_confirmed_revision=1,
                    planning_input_revision=1,
                )
            )
            await db.flush()
            dummy_draft = PlanningDraft(
                draft_id=str(draft_row_id),
                client_draft_id="qa-draft-1",
                stage="confirmed",
                summary=TaskSummary(
                    title="QA Draft",
                    description="Mô tả công việc QA",
                    goal="Hoàn thành QA",
                    scope="1 bước",
                    time_constraint=TimeConstraint(
                        kind="deadline",
                        start_at=now_live + timedelta(hours=1),
                        deadline_at=now_live + timedelta(days=5),
                        input_timezone="Asia/Ho_Chi_Minh",
                    ),
                    can_plan=True,
                    availability=DraftAvailability(
                        timezone="Asia/Ho_Chi_Minh",
                        weekly_rules=[
                            WeeklyRule(weekday=d, start_local="08:00", end_local="22:00")
                            for d in range(1, 8)
                        ],
                    ),
                ),
                summary_revision=1,
                planning_input_revision=1,
                summary_confirmed_revision=1,
                input_source="text",
            )
            horizon = TimeInterval(
                start_at=now_live + timedelta(hours=1),
                end_at=now_live + timedelta(days=5),
            )
            res_sources = await plan_service._sources(
                db, str(user_id), dummy_draft, horizon, create_manual=True
            )
            assert res_sources.context.selected_calendar_sources == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_adv_p3_health_db_failure_and_calendar_oauth_callback_error_envelope() -> None:
    """Kiểm tra /api/v1/health có trong OpenAPI, DB lỗi trả 503 ErrorEnvelope, và oauth/callback error phải trả ErrorEnvelope."""
    openapi_paths = app.openapi()["paths"]
    assert "/api/v1/health" in openapi_paths

    class _BrokenSession:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("DB down")

    async def _broken_db():
        yield _BrokenSession()

    engine, factory, override_get_db = await _make_mem_db()
    try:
        app.dependency_overrides[get_db] = _broken_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            h1 = await client.get("/api/v1/health")
            h2 = await client.get("/health")
            assert h1.status_code == 503
            assert h2.status_code == 503
            assert h1.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
            assert h2.json()["error"]["code"] == "DATABASE_UNAVAILABLE"

        # Kiểm tra /api/v1/calendar/oauth/callback khi lỗi state -> phải trả ErrorEnvelope chuẩn, KHÔNG trả {"detail": ...}
        app.dependency_overrides[get_db] = override_get_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            cb_err = await client.get(
                "/api/v1/calendar/oauth/callback",
                params={"state": "invalid-or-expired-state", "code": "dummy"},
            )
            assert cb_err.status_code in (400, 401, 404)
            body = cb_err.json()
            assert "error" in body and isinstance(body["error"], dict), (
                f"Vi phạm RISK-CALENDAR-ERROR-ENVELOPE! oauth/callback trả raw body thay vì ErrorEnvelope: {body}"
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
