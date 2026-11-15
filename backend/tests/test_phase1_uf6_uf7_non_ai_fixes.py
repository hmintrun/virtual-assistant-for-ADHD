"""Kiểm thử Phase 1: UF6 deadline/availability/extension-cap, UF7 re-segmentation và continuation start_state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.user import CurrentUser
from app.services import reschedule_service, run_service, settings_service
from tests.test_w310_uf6_reschedule import _seed_uf6_fixture

TZ_VN = timezone(timedelta(hours=7))


def _auth_headers(user_id: UUID, idem_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"p1_{user_id.hex[:8]}@example.com",
        display_name="Phase 1 Tester",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer p1-token-{user_id.hex[:8]}"}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return headers


async def _make_mem_env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    sf = session_factory(engine)
    async with engine.begin() as conn:
        from app.models import Base

        await conn.run_sync(Base.metadata.create_all)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return engine, sf


async def _seed_basic_task(
    db: AsyncSession,
    *,
    tz_name: str = "Asia/Ho_Chi_Minh",
    weekly_rules: list[dict],
    deadline_at: datetime,
    sess_start: datetime,
    sess_duration_secs: int = 1500,
    sess_work_secs: int = 1500,
    sess_rest_secs: int = 0,
    sess_segments: list[dict] | None = None,
    step_remaining_base_secs: int = 1500,
) -> dict[str, UUID]:
    user_id = uuid4()
    task_id = uuid4()
    step_id = uuid4()
    sess_id = uuid4()

    db.add(
        User(
            id=user_id,
            email=f"p1_{user_id.hex[:8]}@example.com",
            hashed_password="pw",
            display_name="Phase 1 User",
        )
    )
    await db.flush()

    db.add(
        UserPreferenceRow(
            user_id=user_id,
            revision=1,
            effective_revision=1,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=1.2,
            reminder_style="gentle",
        )
    )
    db.add(
        AvailabilityRow(
            user_id=user_id,
            revision=1,
            timezone=tz_name,
            weekly_rules=weekly_rules,
            date_overrides=[],
            task_limits=[],
            source="manual_input",
            confirmed_at=sess_start - timedelta(days=1),
        )
    )
    db.add(
        TaskRow(
            id=task_id,
            user_id=user_id,
            revision=1,
            title="Nhiệm vụ kiểm thử Phase 1",
            description="Kiểm tra deadline & availability",
            scope="1 phần",
            time_constraint={
                "kind": "deadline",
                "start_at": (sess_start - timedelta(days=1)).isoformat(),
                "deadline_at": deadline_at.isoformat(),
                "input_timezone": tz_name,
            },
            importance=1,
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
            title="Bước 1",
            expected_output="Xong bước 1",
            estimated_base_seconds=step_remaining_base_secs,
            remaining_base_seconds=step_remaining_base_secs,
            buffer_multiplier=1.2,
            estimate_source="ai",
            status="in_progress",
        )
    )
    await db.flush()
    db.add(
        SessionRow(
            id=sess_id,
            user_id=user_id,
            step_id=step_id,
            revision=1,
            scheduled_start=sess_start,
            scheduled_end=sess_start + timedelta(seconds=sess_duration_secs),
            micro_task_text="Mở tài liệu",
            micro_task_seconds=120,
            work_seconds=sess_work_secs,
            rest_seconds=sess_rest_secs,
            segments=sess_segments
            or [{"order_index": 0, "kind": "work", "duration_seconds": sess_work_secs}],
            duration_seconds=sess_duration_secs,
            schedule_revision=1,
            status="scheduled",
            start_state="waiting",
            start_decision_due_at=sess_start + timedelta(seconds=900),
        )
    )
    db.add(
        ScheduleVersionRow(
            user_id=user_id,
            revision=1,
            version_number=1,
            base_version=0,
            approved_at=sess_start - timedelta(hours=2),
            affected_task_ids=[str(task_id)],
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )
    await db.commit()
    return {"user_id": user_id, "task_id": task_id, "step_id": step_id, "sess_id": sess_id}


@pytest.mark.asyncio
async def test_uf6_proposal_blocks_when_exceeding_deadline_or_availability(monkeypatch):
    engine, sf = await _make_mem_env()
    # Thứ 2 2026-11-02 10:30+07:00; phiên 14:00-14:25; deadline 15:00+07:00; availability chỉ mở 09:00-12:00 Thứ 2
    fixed_now = datetime(2026, 11, 2, 10, 30, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_basic_task(
                db,
                weekly_rules=[{"weekday": 1, "start_local": "09:00", "end_local": "12:00"}],
                deadline_at=datetime(2026, 11, 2, 15, 0, 0, tzinfo=TZ_VN),
                sess_start=datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN),
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Dời phiên 14:00 (cộng 30p = 14:30) rơi ngoài availability 09:00-12:00 -> infeasible
            resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-p1-infeasible"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_id"]),
                    "allowed_task_ids": [str(ids["task_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                    "request_text": "Dời sang sáng mai",
                },
            )
            assert resp.status_code == 201, resp.text
            prop = resp.json()["data"]
            assert prop["feasibility"]["status"] == "infeasible"
            assert prop["feasibility"]["confirmable"] is False
            assert prop["feasibility"]["deficit_seconds"] > 0
            assert len(prop["feasibility"]["blocking_reasons"]) >= 1

            # Cố approve proposal infeasible -> 409 PLAN_NOT_CONFIRMABLE
            app_resp = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "idem-p1-app-fail"),
                json={
                    "expected_proposal_revision": prop["proposal_revision"],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert app_resp.status_code == 409
            assert app_resp.json()["error"]["code"] == "PLAN_NOT_CONFIRMABLE"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_uf6_tomorrow_keyword_respects_real_iana_timezone(monkeypatch):
    engine, sf = await _make_mem_env()
    # America/New_York (EST = UTC-5 vào 2026-11-02): 18:00-05:00 = 23:00 UTC
    ny_tz = timezone(timedelta(hours=-5))
    fixed_now = datetime(2026, 11, 2, 18, 0, 0, tzinfo=ny_tz)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_basic_task(
                db,
                tz_name="America/New_York",
                weekly_rules=[
                    {"weekday": wd, "start_local": "08:00", "end_local": "20:00"}
                    for wd in range(1, 8)
                ],
                deadline_at=datetime(2026, 11, 5, 18, 0, 0, tzinfo=ny_tz),
                sess_start=datetime(2026, 11, 2, 19, 0, 0, tzinfo=ny_tz),
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-p1-tz-mai"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_id"]),
                    "allowed_task_ids": [str(ids["task_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                    "request_text": "Dời sang sáng mai",
                },
            )
            assert resp.status_code == 201, resp.text
            prop = resp.json()["data"]
            assert prop["feasibility"]["status"] == "feasible"
            moved = prop["proposed_changes"]["sessions_to_move"][0]
            to_start = datetime.fromisoformat(moved["to_start"]).astimezone(UTC)
            # 09:00 America/New_York (EST = UTC-5) -> 14:00 UTC ngày 2026-11-03 (KHÔNG phải 02:00 UTC)
            assert to_start == datetime(2026, 11, 3, 14, 0, 0, tzinfo=UTC)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_uf6_max_extension_without_borrow_capped_by_deadline_and_availability():
    cont_start = datetime(2026, 11, 2, 14, 27, 0, tzinfo=TZ_VN)

    # Cap bởi deadline_at (+20 phút = 1200s) dù không có sự kiện cố định nào phía sau
    max_dl, rec_dl = reschedule_service._compute_max_extension_without_borrow(
        cont_start,
        [],
        [],
        deadline_at=cont_start + timedelta(seconds=1200),
        availability_end=cont_start + timedelta(hours=5),
    )
    assert max_dl == 1200
    assert all(x <= 1200 for x in rec_dl)

    # Cap bởi availability_end (+30 phút = 1800s) dù deadline còn xa
    max_av, rec_av = reschedule_service._compute_max_extension_without_borrow(
        cont_start,
        [],
        [],
        deadline_at=cont_start + timedelta(days=2),
        availability_end=cont_start + timedelta(seconds=1800),
    )
    assert max_av == 1800
    assert all(x <= 1800 for x in rec_av)


@pytest.mark.asyncio
async def test_uf7_work_interval_change_resegments_3300s_session_through_uf6_approve(monkeypatch):
    engine, sf = await _make_mem_env()
    fixed_now = datetime(2026, 11, 2, 10, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_basic_task(
                db,
                weekly_rules=[{"weekday": wd, "start_local": "08:00", "end_local": "20:00"} for wd in range(1, 8)],
                deadline_at=datetime(2026, 11, 10, 18, 0, 0, tzinfo=TZ_VN),
                sess_start=datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN),
                sess_duration_secs=3300,
                sess_work_secs=3000,
                sess_rest_secs=300,
                sess_segments=[
                    {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                    {"order_index": 2, "kind": "work", "duration_seconds": 1500},
                ],
                step_remaining_base_secs=2500,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ch_resp = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(ids["user_id"], "idem-reseg-1800"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"work_interval_seconds": 1800}},
                },
            )
            assert ch_resp.status_code == 200, ch_resp.text
            ch_data = ch_resp.json()["data"]
            assert ch_data["application_state"] == "pending_review"
            prop_id = ch_data["pending_proposal_id"]
            assert prop_id is not None

            prop = (
                await client.get("/api/v1/proposals/current", headers=_auth_headers(ids["user_id"]))
            ).json()["data"]
            cancels = prop["proposed_changes"]["sessions_to_cancel"]
            adds = prop["proposed_changes"]["sessions_to_add"]
            assert len(cancels) == 1
            assert cancels[0]["session_id"] == str(ids["sess_id"])
            assert len(adds) == 1
            assert adds[0]["action"] == "create_session"
            assert adds[0]["after"]["work_seconds"] == 3000
            assert adds[0]["after"]["rest_seconds"] == 300
            assert adds[0]["after"]["duration_seconds"] == 3300
            assert [seg["duration_seconds"] for seg in adds[0]["after"]["segments"]] == [
                1800,
                300,
                1200,
            ]

            # Trước khi approve, phiên 3300s cũ vẫn còn nguyên trạng thái scheduled
            async with sf() as db:
                old_row = await db.get(SessionRow, ids["sess_id"])
                assert old_row is not None
                assert old_row.status == "scheduled"
                assert [s["duration_seconds"] for s in old_row.segments] == [1500, 300, 1500]

            app_resp = await client.post(
                f"/api/v1/proposals/{prop_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-reseg-approve"),
                json={
                    "expected_proposal_revision": prop["proposal_revision"],
                    "expected_base_schedule_revision": prop["base_schedule_revision"],
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert app_resp.status_code == 200, app_resp.text

            async with sf() as db:
                old_after = await db.get(SessionRow, ids["sess_id"])
                assert old_after is not None
                assert old_after.status == "cancelled"
                assert old_after.start_state == "superseded"

                new_sessions = list(
                    await db.scalars(
                        select(SessionRow).where(
                            SessionRow.user_id == ids["user_id"],
                            SessionRow.status == "scheduled",
                        )
                    )
                )
                assert len(new_sessions) == 1
                ns = new_sessions[0]
                assert ns.start_state == "waiting"
                assert ns.duration_seconds == 3300
                assert [s["duration_seconds"] for s in ns.segments] == [1800, 300, 1200]
                rems = list(
                    await db.scalars(
                        select(Reminder).where(
                            Reminder.session_id == ns.id,
                            Reminder.delivery_state == "scheduled",
                        )
                    )
                )
                assert len(rems) == 3
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_continuation_start_false_sets_waiting_starts_201_and_reconciles_with_now_none(
    monkeypatch,
):
    engine, sf = await _make_mem_env()
    fixed_now = datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            cont_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-cont-wait-create"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(ids["run1_id"]),
                        "expected_run_revision": 1,
                        "additional_work_seconds": 480,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task1_id"])],
                    },
                },
            )
            assert cont_resp.status_code == 201, cont_resp.text
            prop = cont_resp.json()["data"]

            # Approve với start_continuation=False -> session mới phải có start_state='waiting'
            app_resp = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "idem-cont-wait-app"),
                json={
                    "expected_proposal_revision": prop["proposal_revision"],
                    "expected_base_schedule_revision": prop["base_schedule_revision"],
                    "expected_source_revisions": prop["source_revisions"],
                    "start_continuation": False,
                },
            )
            assert app_resp.status_code == 200, app_resp.text
            app_data = app_resp.json()["data"]
            cont_sessions = [
                s for s in app_data["sessions"] if s["work_seconds"] == 480 and s["status"] == "scheduled"
            ]
            assert len(cont_sessions) == 1
            cont_sess = cont_sessions[0]
            assert cont_sess["start_state"] == "waiting"

            # Bấm POST /sessions/{id}/start -> 201 Created thành công
            cont_start_at = datetime.fromisoformat(cont_sess["scheduled_start"])
            monkeypatch.setattr(
                run_service,
                "_current_utc_now",
                lambda now=None: (now or cont_start_at).astimezone(UTC),
            )
            start_resp = await client.post(
                f"/api/v1/sessions/{cont_sess['session_id']}/start",
                headers=_auth_headers(ids["user_id"], "idem-cont-wait-start"),
                json={
                    "expected_schedule_revision": app_data["updated_schedule_revision"],
                    "device_id": "android_pixel_8",
                    "requested_start_at": cont_start_at.isoformat(),
                },
            )
            assert start_resp.status_code == 201, start_resp.text
            assert start_resp.json()["data"]["run_state"] == "running"

        # Kiểm tra reconcile_expired_checkins với now=None vẫn đối soát được overdue 'waiting' session -> 'not_started'
        async with sf() as db:
            monkeypatch.setattr(
                run_service,
                "_current_utc_now",
                lambda now=None: (now or (cont_start_at + timedelta(minutes=45))).astimezone(UTC),
            )
            # Đảm bảo sess2_id vẫn đang scheduled (trong fixture sess2_id có start_state='pending' -> set sang 'waiting')
            s2 = await db.get(SessionRow, ids["sess2_id"])
            assert s2 is not None
            s2.start_state = "waiting"
            s2.start_decision_due_at = datetime(2026, 11, 2, 15, 0, 0, tzinfo=TZ_VN)
            await db.commit()

            changed = await run_service.reconcile_expired_checkins(
                db, str(ids["user_id"]), now=None, commit_if_read=True
            )
            assert changed is True
            s2_after = await db.get(SessionRow, ids["sess2_id"])
            assert s2_after is not None
            assert s2_after.start_state == "not_started"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_uf6_empty_allowed_task_ids_with_none_target_and_task_cursor_isolation():
    engine, sf = await _make_mem_env()
    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db, with_second_task=True)
            # Đặt sess2 của task1 lúc 11:00..11:25 và sess_other của task2 lúc 12:00..12:25 (tất cả đều hợp lệ)
            s1_fut = await db.get(SessionRow, ids["sess2_id"])
            s2_fut = await db.get(SessionRow, ids["sess_other_id"])
            assert s1_fut is not None and s2_fut is not None
            s1_fut.scheduled_start = datetime(2026, 11, 2, 11, 0, tzinfo=TZ_VN)
            s1_fut.scheduled_end = datetime(2026, 11, 2, 11, 25, tzinfo=TZ_VN)
            s2_fut.scheduled_start = datetime(2026, 11, 2, 12, 0, tzinfo=TZ_VN)
            s2_fut.scheduled_end = datetime(2026, 11, 2, 12, 25, tzinfo=TZ_VN)
            await db.commit()

            calc_now = datetime(2026, 11, 2, 10, 0, tzinfo=TZ_VN).astimezone(UTC)

            # 1. target_task_id=None và allowed_task_ids=[] tường minh -> không được phép kéo mọi task vào movable_sessions
            t_id, affected, changes, feas, _ = await reschedule_service._build_normal_proposal_plan(
                db,
                ids["user_id"],
                trigger_reason="user_reschedule",
                target_task_id=None,
                allowed_task_ids=[],
                request_text=None,
                calc_now=calc_now,
            )
            assert t_id is None
            assert affected == []
            assert changes.sessions_to_move == []
            assert feas.confirmable is True

            # 2. settings_change trên task1 (dời sang 16:15 do override_availability 16:00..18:00) trong khi allowed=[task1, task2]
            # -> task2 ở 12:00 hoàn toàn hợp lệ không bị task1 đẩy cursor hoặc bị +30p vô cớ
            _, affected2, changes2, feas2, _ = await reschedule_service._build_normal_proposal_plan(
                db,
                ids["user_id"],
                trigger_reason="settings_change",
                target_task_id=str(ids["task1_id"]),
                allowed_task_ids=[str(ids["task1_id"]), str(ids["task2_id"])],
                request_text=None,
                calc_now=calc_now,
                override_availability={
                    "timezone": "Asia/Ho_Chi_Minh",
                    "weekly_rules": [
                        {"weekday": d, "start_local": "16:00", "end_local": "18:00"}
                        for d in range(1, 8)
                    ],
                },
            )
            assert feas2.confirmable is True
            moved_ids = {m.session_id for m in changes2.sessions_to_move}
            assert str(ids["sess2_id"]) in moved_ids
            assert str(ids["sess_other_id"]) not in moved_ids
            assert str(ids["task2_id"]) not in affected2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
