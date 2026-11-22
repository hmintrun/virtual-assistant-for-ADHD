"""Kiểm thử toàn diện Gói W3-12 & W3-17 (UF7: Hiệu lực Requested/Effective, Cancel Pending & Suggestions Pilot A20)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models.calendar import CalendarConnection, PlanningContext
from app.models.execution import SessionRun
from app.models.planning import PlanningDraft
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion
from app.models.settings import Availability as AvailabilityRow
from app.models.settings import SettingChangeRequest as SettingChangeRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.user import CurrentUser
from app.services import reschedule_service, settings_service
from app.services.command_service import owner_uuid

TZ_VN = timezone(timedelta(hours=7))


def _auth_headers(user_id: UUID, idem_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"u_{user_id.hex[:8]}@example.com",
        display_name="Võ Quốc Hưng",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-token-{user_id.hex[:8]}"}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return headers


async def _make_client_env():
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


async def _seed_user_with_prefs(
    db: AsyncSession,
    *,
    buffer_multiplier: float = 1.2,
    effective_revision: int = 1,
    with_availability: bool = True,
) -> UUID:
    user_id = uuid4()
    now = datetime(2026, 11, 2, 10, 0, 0, tzinfo=TZ_VN)
    db.add(
        User(
            id=user_id,
            email=f"uf7_{user_id.hex[:8]}@example.com",
            hashed_password="hashed_pw",
            display_name="Võ Quốc Hưng",
        )
    )
    await db.flush()
    db.add(
        UserPreferenceRow(
            user_id=user_id,
            revision=1,
            effective_revision=effective_revision,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=buffer_multiplier,
            reminder_style="gentle",
            created_at=now,
            updated_at=now,
        )
    )
    if with_availability:
        db.add(
            AvailabilityRow(
                user_id=user_id,
                revision=1,
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[
                    {
                        "weekday": wd,
                        "start_local": "09:00",
                        "end_local": "17:00",
                    }
                    for wd in (1, 2, 3, 4, 5)
                ],
                date_overrides=[],
                task_limits=[],
                source="manual_input",
                confirmed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()
    return user_id


async def _seed_future_task_and_session(
    db: AsyncSession,
    user_id: UUID,
    *,
    session_status: str = "scheduled",
    with_active_run: bool = False,
) -> dict[str, UUID]:
    task_id = uuid4()
    step_id = uuid4()
    sess_id = uuid4()
    run_id = uuid4()

    t_start = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    t_end = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)

    avail_row = await db.scalar(
        select(AvailabilityRow).where(AvailabilityRow.user_id == user_id).limit(1)
    )
    avail_id = avail_row.id if avail_row is not None else uuid4()

    db.add(
        CalendarConnection(
            id=uuid4(),
            user_id=user_id,
            revision=1,
            provider="google",
            account_ref="vqh@example.com",
            read_calendar_ids=["primary"],
            write_calendar_id="proplan_secondary",
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            connection_state="connected",
            credential_ref="encrypted_cred_ref",
            last_successful_sync_at=t_start - timedelta(minutes=10),
        )
    )
    await db.flush()

    db.add(
        PlanningContext(
            id=uuid4(),
            user_id=user_id,
            revision=1,
            availability_id=avail_id,
            availability_revision=1,
            selected_calendar_ids=["primary"],
            query_start=datetime(2026, 11, 2, 0, 0, 0, tzinfo=TZ_VN),
            query_end=datetime(2026, 11, 16, 20, 0, 0, tzinfo=TZ_VN),
            busy_intervals=[],
            fetched_at=t_start - timedelta(hours=2),
            sync_state="ok",
            calendar_errors=[],
            event_snapshots=[],
            coverage_state="complete",
            manual_busy_intervals=[],
            excluded_sources=[],
            selected_calendar_sources=[],
        )
    )
    db.add(
        TaskRow(
            id=task_id,
            user_id=user_id,
            revision=1,
            title="Hoàn thiện báo cáo sprint 3",
            description="Phần UF7",
            scope="5 trang",
            time_constraint={
                "mode": "deadline",
                "at": "2026-11-10T17:00:00+07:00",
                "timezone": "Asia/Ho_Chi_Minh",
            },
            importance=1,
            status="in_progress",
            decomposition_state="complete",
            availability={
                "timezone": "Asia/Ho_Chi_Minh",
                "weekly_rules": [
                    {
                        "weekday": 1,
                        "start_local": "09:00",
                        "end_local": "17:00",
                    }
                ],
                "date_overrides": [],
            },
        )
    )
    await db.flush()

    db.add(
        StepRow(
            id=step_id,
            user_id=user_id,
            task_id=task_id,
            revision=1,
            title="Viết module settings",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
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
            scheduled_start=t_start,
            scheduled_end=t_end,
            micro_task_text="Mở file settings_service.py",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=0,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1500,
            schedule_revision=2,
            status=session_status,
            start_state="started" if with_active_run else "waiting",
            start_decision_due_at=t_start + timedelta(minutes=5),
        )
    )
    await db.flush()

    db.add(
        ScheduleVersionRow(
            user_id=user_id,
            revision=1,
            version_number=2,
            base_version=1,
            approved_at=t_start - timedelta(hours=1),
            affected_task_ids=[str(task_id)],
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )

    if with_active_run:
        db.add(
            SessionRun(
                id=run_id,
                user_id=user_id,
                session_id=sess_id,
                device_id="dev-pixel-9",
                started_at=t_start,
                run_state="running",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=120,
                rest_elapsed_seconds=0,
                outcome="unknown",
                settings_snapshot={
                    "work_interval_seconds": 1500,
                    "rest_interval_seconds": 300,
                    "buffer_multiplier": 1.2,
                    "reminder_offsets": [900, 600, 300],
                    "reminder_style": "gentle",
                },
            )
        )

    await db.commit()
    return {"task_id": task_id, "step_id": step_id, "session_id": sess_id, "run_id": run_id}


async def _seed_completed_steps_for_suggestion(
    db: AsyncSession,
    user_id: UUID,
    ratios: list[float],
    *,
    include_dirty_samples: bool = False,
) -> list[UUID]:
    task_id = uuid4()
    base_time = datetime(2026, 11, 1, 9, 0, 0, tzinfo=TZ_VN)
    db.add(
        TaskRow(
            id=task_id,
            user_id=user_id,
            revision=1,
            title="Lịch sử hoàn thành",
            description="Thu thập mẫu A20",
            scope="5 bước",
            time_constraint={
                "mode": "deadline",
                "at": "2026-11-20T17:00:00+07:00",
                "timezone": "Asia/Ho_Chi_Minh",
            },
            status="in_progress",
            decomposition_state="complete",
        )
    )
    await db.flush()

    step_ids: list[UUID] = []
    for idx, ratio in enumerate(ratios):
        step_id = uuid4()
        sess_id = uuid4()
        run_id = uuid4()
        base_sec = 2000
        actual_sec = int(round(base_sec * ratio))
        t_s = base_time + timedelta(hours=idx * 2)
        t_e = t_s + timedelta(seconds=actual_sec)

        db.add(
            StepRow(
                id=step_id,
                user_id=user_id,
                task_id=task_id,
                revision=1,
                title=f"Bước sạch {idx}",
                order_index=idx,
                estimated_base_seconds=base_sec,
                remaining_base_seconds=0,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="completed",
                created_at=t_s,
                updated_at=t_e,
            )
        )
        await db.flush()
        db.add(
            SessionRow(
                id=sess_id,
                user_id=user_id,
                step_id=step_id,
                revision=1,
                scheduled_start=t_s,
                scheduled_end=t_s + timedelta(seconds=2000),
                work_seconds=2000,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 2000}],
                duration_seconds=2000,
                schedule_revision=1,
                status="completed",
                start_state="started",
                created_at=t_s,
                updated_at=t_e,
            )
        )
        await db.flush()
        db.add(
            SessionRun(
                id=run_id,
                user_id=user_id,
                session_id=sess_id,
                device_id="dev-1",
                started_at=t_s,
                ended_at=t_e,
                run_state="ended",
                active_phase="ended",
                segment_index=0,
                work_elapsed_seconds=actual_sec,
                rest_elapsed_seconds=0,
                actual_work_seconds=actual_sec,
                actual_source="timer_proxy",
                outcome="completed",
                settings_snapshot={"buffer_multiplier": 1.2},
                created_at=t_s,
                updated_at=t_e,
            )
        )
        step_ids.append(step_id)

    if include_dirty_samples:
        # 1) Step chưa hoàn thành -> phải bị loại
        dirty_step_1 = uuid4()
        dirty_sess_1 = uuid4()
        db.add(
            StepRow(
                id=dirty_step_1,
                user_id=user_id,
                task_id=task_id,
                revision=1,
                title="Bước chưa xong",
                order_index=100,
                estimated_base_seconds=2000,
                remaining_base_seconds=1000,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="incomplete",
            )
        )
        await db.flush()
        db.add(
            SessionRow(
                id=dirty_sess_1,
                user_id=user_id,
                step_id=dirty_step_1,
                revision=1,
                scheduled_start=base_time + timedelta(days=1),
                scheduled_end=base_time + timedelta(days=1, minutes=25),
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=1,
                status="incomplete",
                start_state="started",
            )
        )
        await db.flush()
        db.add(
            SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=dirty_sess_1,
                device_id="dev-1",
                started_at=base_time + timedelta(days=1),
                ended_at=base_time + timedelta(days=1, minutes=40),
                run_state="ended",
                active_phase="ended",
                actual_work_seconds=4000,
                actual_source="timer_proxy",
                outcome="incomplete",
                settings_snapshot={},
            )
        )

        # 2) Step đổi scope -> phải bị loại
        dirty_step_2 = uuid4()
        dirty_sess_2 = uuid4()
        db.add(
            StepRow(
                id=dirty_step_2,
                user_id=user_id,
                task_id=task_id,
                revision=1,
                title="Bước đổi scope",
                order_index=101,
                estimated_base_seconds=2000,
                remaining_base_seconds=0,
                buffer_multiplier=1.2,
                estimate_source="scope_modified",
                status="completed",
            )
        )
        await db.flush()
        db.add(
            SessionRow(
                id=dirty_sess_2,
                user_id=user_id,
                step_id=dirty_step_2,
                revision=1,
                scheduled_start=base_time + timedelta(days=2),
                scheduled_end=base_time + timedelta(days=2, minutes=25),
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=1,
                status="completed",
                start_state="started",
            )
        )
        await db.flush()
        db.add(
            SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=dirty_sess_2,
                device_id="dev-1",
                started_at=base_time + timedelta(days=2),
                ended_at=base_time + timedelta(days=2, minutes=40),
                run_state="ended",
                active_phase="ended",
                actual_work_seconds=4000,
                actual_source="timer_proxy",
                outcome="completed",
                settings_snapshot={},
            )
        )

    await db.commit()
    return step_ids


@pytest.mark.asyncio
async def test_get_preferences_and_availability_contract():
    engine, sf = await _make_client_env()
    try:
        async with sf() as db:
            user_id = await _seed_user_with_prefs(db, buffer_multiplier=1.25, effective_revision=2)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pref_resp = await client.get(
                "/api/v1/preferences",
                headers=_auth_headers(user_id),
            )
            assert pref_resp.status_code == 200
            pref_data = pref_resp.json()["data"]
            assert pref_data["effective_revision"] == 2
            assert pref_data["buffer_multiplier"] == 1.25
            assert pref_data["requested_revision"] is None
            assert pref_data["pending_proposal_id"] is None

            # GET /availability hợp lệ với cặp from/to
            avail_resp = await client.get(
                "/api/v1/availability",
                params={
                    "from": "2026-11-02T00:00:00+07:00",
                    "to": "2026-11-03T23:59:59+07:00",
                },
                headers=_auth_headers(user_id),
            )
            assert avail_resp.status_code == 200
            avail_data = avail_resp.json()["data"]
            assert avail_data["timezone"] == "Asia/Ho_Chi_Minh"
            assert len(avail_data["effective_intervals"]) >= 1

            # Thiếu cặp from/to -> 422 VALIDATION_ERROR
            bad_avail = await client.get(
                "/api/v1/availability",
                params={"from": "2026-11-02T00:00:00+07:00"},
                headers=_auth_headers(user_id),
            )
            assert bad_avail.status_code == 422
            assert bad_avail.json()["error"]["code"] == "VALIDATION_ERROR"

            # User chưa có availability -> 404 RESOURCE_NOT_FOUND
            other_user = uuid4()
            miss_avail = await client.get(
                "/api/v1/availability",
                headers=_auth_headers(other_user),
            )
            assert miss_avail.status_code == 404
            assert miss_avail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_settings_change_immediate_draft_and_active_run_snapshot():
    engine, sf = await _make_client_env()
    try:
        async with sf() as db:
            user_id = await _seed_user_with_prefs(db, buffer_multiplier=1.2, effective_revision=1)
            # Tạo phiên in_progress với SessionRun running (KHÔNG có phiên scheduled tương lai)
            seeded = await _seed_future_task_and_session(
                db, user_id, session_status="in_progress", with_active_run=True
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Đổi reminder_style (không ảnh hưởng lịch) khi đang chạy run -> effective_from_next_session, giữ nguyên snapshot
            resp1 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-style-1"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"reminder_style": "firm"}},
                },
            )
            assert resp1.status_code == 200
            d1 = resp1.json()["data"]
            assert d1["application_state"] == "effective_from_next_session"
            assert d1["effective_revision"] == 2
            assert d1["requested_revision"] is None
            assert d1["pending_proposal_id"] is None

            # 2) Đổi work_interval_seconds khi không có phiên scheduled tương lai -> effective_from_next_session, snapshot không đổi
            resp2 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-work-no-future"),
                json={
                    "expected_preferences_revision": 2,
                    "changes": {"preferences": {"work_interval_seconds": 1800}},
                },
            )
            assert resp2.status_code == 200
            d2 = resp2.json()["data"]
            assert d2["application_state"] == "effective_from_next_session"
            assert d2["effective_revision"] == 3

            async with sf() as db:
                run_row = await db.get(SessionRun, seeded["run_id"])
                assert run_row is not None
                assert run_row.settings_snapshot["work_interval_seconds"] == 1500
                assert run_row.settings_snapshot["reminder_style"] == "gentle"

            # 3) Chỉnh availability cho Draft -> applied_to_draft
            draft_id = uuid4()
            async with sf() as db:
                db.add(
                    PlanningDraft(
                        id=draft_id,
                        user_id=user_id,
                        client_draft_id="draft_uf7_test_1",
                        stage="summary_ready",
                        input_source="text",
                        raw_input="Ôn thi cuối kỳ",
                        summary_revision=1,
                        planning_input_revision=1,
                        summary={
                            "title": "Ôn thi cuối kỳ",
                            "scope": "3 chương",
                            "time_constraint": {
                                "kind": "deadline",
                                "start_at": "2026-11-02T09:00:00+07:00",
                                "deadline_at": "2026-11-15T17:00:00+07:00",
                                "input_timezone": "Asia/Ho_Chi_Minh",
                            },
                            "availability": {
                                "timezone": "Asia/Ho_Chi_Minh",
                                "weekly_rules": [],
                                "date_overrides": [],
                            },
                            "can_plan": True,
                        },
                    )
                )
                await db.commit()

            resp3 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-draft-avail"),
                json={
                    "target_draft_id": str(draft_id),
                    "expected_summary_revision": 1,
                    "expected_planning_input_revision": 1,
                    "changes": {
                        "availability": {
                            "timezone": "Asia/Ho_Chi_Minh",
                            "weekly_rules": [],
                            "date_overrides": [
                                {
                                    "date": "2026-11-05",
                                    "intervals": [{"start_local": "09:00", "end_local": "12:00"}],
                                }
                            ],
                        }
                    },
                },
            )
            assert resp3.status_code == 200
            d3 = resp3.json()["data"]
            assert d3["application_state"] == "applied_to_draft"
            assert d3["target_draft_id"] == str(draft_id)
            assert d3["summary_revision"] == 2
            assert d3["planning_input_revision"] == 2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_settings_change_with_future_schedule_pending_and_uf6_approve_reject(monkeypatch):
    engine, sf = await _make_client_env()
    fixed_now = datetime(2026, 11, 2, 10, 30, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            user_id = await _seed_user_with_prefs(db, buffer_multiplier=1.2, effective_revision=1)
            seeded = await _seed_future_task_and_session(
                db, user_id, session_status="scheduled", with_active_run=False
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Sửa work_interval_seconds khi có phiên scheduled tương lai -> pending_review + pending_proposal_id (KHÔNG còn 503)
            resp = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-pref-pending-1"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {
                        "preferences": {
                            "work_interval_seconds": 1800,
                            "buffer_multiplier": 1.4,
                        }
                    },
                },
            )
            assert resp.status_code == 200
            body = resp.json()["data"]
            assert body["application_state"] == "pending_review"
            assert body["effective_revision"] == 1
            assert body["requested_revision"] == 2
            proposal_id = body["pending_proposal_id"]
            assert proposal_id is not None

            # GET /preferences vẫn giữ effective cũ và hiển thị requested_changes
            pref_get = await client.get("/api/v1/preferences", headers=_auth_headers(user_id))
            assert pref_get.status_code == 200
            pref_data = pref_get.json()["data"]
            assert pref_data["work_interval_seconds"] == 1500
            assert pref_data["buffer_multiplier"] == 1.2
            assert pref_data["effective_revision"] == 1
            assert pref_data["requested_revision"] == 2
            assert pref_data["pending_proposal_id"] == proposal_id
            assert pref_data["requested_changes"]["work_interval_seconds"] == 1800

            # Đọc proposal hiện hành rồi approve qua UF6 -> chuyển requested -> effective
            prop_resp = await client.get("/api/v1/proposals/current", headers=_auth_headers(user_id))
            assert prop_resp.status_code == 200
            prop = prop_resp.json()["data"]
            assert prop["proposal_id"] == proposal_id

            approve_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(user_id, "idem-approve-pref-1"),
                json={
                    "expected_proposal_revision": prop["proposal_revision"],
                    "expected_base_schedule_revision": prop["base_schedule_revision"],
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert approve_resp.status_code == 200

            pref_after = await client.get("/api/v1/preferences", headers=_auth_headers(user_id))
            pref_after_data = pref_after.json()["data"]
            assert pref_after_data["work_interval_seconds"] == 1800
            assert pref_after_data["buffer_multiplier"] == 1.4
            assert pref_after_data["effective_revision"] == 2
            assert pref_after_data["requested_revision"] is None
            assert pref_after_data["pending_proposal_id"] is None

            # 2) Chỉnh availability của target_task_id -> pending_review -> reject qua UF6 giữ nguyên availability cũ
            async with sf() as db:
                t_before = await db.get(TaskRow, seeded["task_id"])
                assert t_before is not None
                cur_task_rev = t_before.revision

            new_task_avail = {
                "timezone": "Asia/Ho_Chi_Minh",
                "weekly_rules": [
                    {
                        "weekday": 2,
                        "start_local": "10:00",
                        "end_local": "16:00",
                    }
                ],
                "date_overrides": [],
            }
            task_req = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-task-avail-1"),
                json={
                    "target_task_id": str(seeded["task_id"]),
                    "expected_task_revision": cur_task_rev,
                    "changes": {"availability": new_task_avail},
                },
            )
            assert task_req.status_code == 200
            t_data = task_req.json()["data"]
            assert t_data["application_state"] == "pending_review"
            assert t_data["target_task_id"] == str(seeded["task_id"])
            task_prop_id = t_data["pending_proposal_id"]
            assert task_prop_id is not None

            prop2 = (
                await client.get("/api/v1/proposals/current", headers=_auth_headers(user_id))
            ).json()["data"]
            rej_resp = await client.post(
                f"/api/v1/proposals/{task_prop_id}/reject",
                headers=_auth_headers(user_id, "idem-reject-task-avail"),
                json={
                    "expected_proposal_revision": prop2["proposal_revision"],
                    "reason": "Giữ nguyên giờ cũ",
                },
            )
            assert rej_resp.status_code == 200

            async with sf() as db:
                t_row = await db.get(TaskRow, seeded["task_id"])
                assert t_row is not None
                assert t_row.availability is not None
                assert t_row.availability["weekly_rules"][0]["weekday"] == 1
                s_req = await db.get(SettingChangeRow, UUID(t_data["change_request_id"]))
                assert s_req is not None
                assert s_req.application_state == "cancelled"
                assert s_req.requested_availability_revision is None

            # 3) Chỉnh availability của target_task_id -> approve qua UF6 cập nhật Task.availability & tăng Task.revision
            task_req_app = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-task-avail-app"),
                json={
                    "target_task_id": str(seeded["task_id"]),
                    "expected_task_revision": cur_task_rev,
                    "changes": {"availability": new_task_avail},
                },
            )
            assert task_req_app.status_code == 200
            t_app_data = task_req_app.json()["data"]
            assert t_app_data["requested_availability_revision"] == cur_task_rev + 1
            prop3 = (
                await client.get("/api/v1/proposals/current", headers=_auth_headers(user_id))
            ).json()["data"]
            app_task_resp = await client.post(
                f"/api/v1/proposals/{t_app_data['pending_proposal_id']}/approve",
                headers=_auth_headers(user_id, "idem-approve-task-avail"),
                json={
                    "expected_proposal_revision": prop3["proposal_revision"],
                    "expected_base_schedule_revision": prop3["base_schedule_revision"],
                    "expected_source_revisions": prop3["source_revisions"],
                },
            )
            assert app_task_resp.status_code == 200

            async with sf() as db:
                t_after = await db.get(TaskRow, seeded["task_id"])
                assert t_after is not None
                assert t_after.revision == cur_task_rev + 1
                assert t_after.availability is not None
                assert t_after.availability["weekly_rules"][0]["weekday"] == 2
                s_req_app = await db.get(SettingChangeRow, UUID(t_app_data["change_request_id"]))
                assert s_req_app is not None
                assert s_req_app.application_state == "applied"
                assert s_req_app.effective_availability_revision == cur_task_rev + 1
                assert s_req_app.requested_availability_revision is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_settings_change_and_guards(monkeypatch):
    engine, sf = await _make_client_env()
    fixed_now = datetime(2026, 11, 2, 10, 30, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            user_id = await _seed_user_with_prefs(db, buffer_multiplier=1.2, effective_revision=4)
            await _seed_future_task_and_session(db, user_id, session_status="scheduled")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # A) Tạo yêu cầu immediate (reminder_style) -> cancel bị chặn 409 (cấm rollback hiệu lực)
            imm_resp = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-imm-style"),
                json={
                    "expected_preferences_revision": 4,
                    "changes": {"preferences": {"reminder_style": "minimal"}},
                },
            )
            assert imm_resp.status_code == 200
            imm_id = imm_resp.json()["data"]["change_request_id"]
            imm_rev = imm_resp.json()["data"]["request_revision"]

            cant_cancel = await client.post(
                f"/api/v1/settings/change-requests/{imm_id}/cancel",
                headers=_auth_headers(user_id, "idem-cancel-imm"),
                json={"expected_request_revision": imm_rev},
            )
            assert cant_cancel.status_code == 409
            assert cant_cancel.json()["error"]["code"] == "SETTINGS_CHANGE_NOT_CANCELLABLE"

            # B) Tạo yêu cầu pending_review -> lệch revision 409, cross-user 404, cancel thành công, replay idempotent
            pend_resp = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-pend-buf"),
                json={
                    "expected_preferences_revision": 5,
                    "changes": {"preferences": {"buffer_multiplier": 1.5}},
                },
            )
            assert pend_resp.status_code == 200
            pend_data = pend_resp.json()["data"]
            pend_id = pend_data["change_request_id"]
            prop_id = pend_data["pending_proposal_id"]
            assert pend_data["application_state"] == "pending_review"

            # Cross-user 404
            other_resp = await client.post(
                f"/api/v1/settings/change-requests/{pend_id}/cancel",
                headers=_auth_headers(uuid4(), "idem-other-cancel"),
                json={"expected_request_revision": pend_data["request_revision"]},
            )
            assert other_resp.status_code == 404

            # Lệch revision -> 409 REVISION_CONFLICT
            rev_err = await client.post(
                f"/api/v1/settings/change-requests/{pend_id}/cancel",
                headers=_auth_headers(user_id, "idem-bad-rev"),
                json={"expected_request_revision": pend_data["request_revision"] + 9},
            )
            assert rev_err.status_code == 409
            assert rev_err.json()["error"]["code"] == "REVISION_CONFLICT"
            assert rev_err.json()["error"]["current_revisions"]["request_revision"] == 1

            # Hủy thành công
            cancel_resp = await client.post(
                f"/api/v1/settings/change-requests/{pend_id}/cancel",
                headers=_auth_headers(user_id, "idem-cancel-ok"),
                json={"expected_request_revision": 1},
            )
            assert cancel_resp.status_code == 200
            c_data = cancel_resp.json()["data"]
            assert c_data == {
                "change_request_id": pend_id,
                "application_state": "cancelled",
                "effective_revision": 5,
            }

            # Replay cùng Idempotency-Key -> trả lại đúng kết quả
            replay_resp = await client.post(
                f"/api/v1/settings/change-requests/{pend_id}/cancel",
                headers=_auth_headers(user_id, "idem-cancel-ok"),
                json={"expected_request_revision": 1},
            )
            assert replay_resp.status_code == 200
            assert replay_resp.json()["data"] == c_data

            # Kiểm tra DB: proposal bị hủy (rejected) & UserPreference được dọn sạch
            async with sf() as db:
                prop_row = await db.get(RescheduleProposal, UUID(prop_id))
                assert prop_row is not None
                assert prop_row.status == "rejected"
                pref_row = await db.scalar(
                    select(UserPreferenceRow).where(
                        UserPreferenceRow.user_id == owner_uuid(str(user_id))
                    )
                )
                assert pref_row is not None
                assert pref_row.pending_proposal_id is None
                assert pref_row.requested_revision is None
                assert pref_row.requested_changes is None
                assert pref_row.buffer_multiplier == 1.2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_suggestions_current_evaluation_dirty_filter_threshold_and_cooldown(monkeypatch):
    engine, sf = await _make_client_env()
    fixed_now = datetime(2026, 11, 10, 10, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        # 1) Dưới 7 mẫu sạch (6 mẫu) -> data: null; lọc bỏ mẫu bẩn -> vẫn null
        async with sf() as db:
            user_below = await _seed_user_with_prefs(db, buffer_multiplier=1.05)
            await _seed_completed_steps_for_suggestion(
                db, user_below, [1.5, 1.5, 1.5, 1.5, 1.5, 1.5], include_dirty_samples=True
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_null = await client.get(
                "/api/v1/suggestions/current", headers=_auth_headers(user_below)
            )
            assert r_null.status_code == 200
            assert r_null.json()["data"] is None

        # 2) Lệch đúng bằng 0.2 (|1.25 - 1.05| = 0.2, không > 0.2) -> data: null
        async with sf() as db:
            user_eq = await _seed_user_with_prefs(db, buffer_multiplier=1.05)
            await _seed_completed_steps_for_suggestion(
                db, user_eq, [1.25, 1.25, 1.25, 1.25, 1.25, 1.25, 1.25], include_dirty_samples=False
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_eq = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_eq))
            assert r_eq.status_code == 200
            assert r_eq.json()["data"] is None

        # 3) Đủ 7 mẫu sạch với ratio > 2.0 -> kẹp miền [1.0, 2.0] thành 2.0, model_revision is None
        async with sf() as db:
            user_ok = await _seed_user_with_prefs(db, buffer_multiplier=1.1)
            await _seed_completed_steps_for_suggestion(
                db, user_ok, [2.4, 2.5, 2.6, 2.5, 2.7, 2.5, 2.6], include_dirty_samples=True
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_ok = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_ok))
            assert r_ok.status_code == 200
            s_data = r_ok.json()["data"]
            assert s_data is not None
            assert s_data["kind"] == "buffer_multiplier"
            assert s_data["sample_count"] == 7
            assert s_data["suggested_value"] == 2.0
            assert s_data["model_revision"] is None
            assert s_data["status"] == "pending"

            # Reject kèm annoyance_feedback -> kích hoạt cooldown, GET /suggestions/current trả null
            rej = await client.post(
                f"/api/v1/suggestions/{s_data['suggestion_id']}/response",
                headers=_auth_headers(user_ok, "idem-sugg-rej"),
                json={
                    "expected_suggestion_revision": s_data["suggestion_revision"],
                    "action": "reject",
                    "annoyance_feedback": "Hôm nay mình thấy gợi ý hơi nhiều",
                },
            )
            assert rej.status_code == 200
            assert rej.json()["data"]["status"] == "rejected"
            assert rej.json()["data"]["application_state"] == "not_applied"

            r_after = await client.get("/api/v1/suggestions/current", headers=_auth_headers(user_ok))
            assert r_after.status_code == 200
            assert r_after.json()["data"] is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_suggestion_response_accept_edit_validation_conflicts_and_uf6(monkeypatch):
    engine, sf = await _make_client_env()
    fixed_now = datetime(2026, 11, 2, 10, 30, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        # Kịch bản A: Không có lịch tương lai -> accept áp dụng ngay (effective_from_next_session), giữ nguyên active SessionRun snapshot
        async with sf() as db:
            user_a = await _seed_user_with_prefs(db, buffer_multiplier=1.2, effective_revision=3)
            seeded_a = await _seed_future_task_and_session(
                db, user_a, session_status="in_progress", with_active_run=True
            )
            sugg_a_id = uuid4()
            db.add(
                AdaptationSuggestion(
                    id=sugg_a_id,
                    user_id=user_a,
                    revision=1,
                    kind="reminder_offset",
                    sample_count=0,
                    current_value=[900, 600, 300],
                    suggested_value=[300],
                    reason="Bạn muốn được nhắc trước 5 phút thay vì 15 phút.",
                    policy_revision="demo-policy",
                    sample_refs=[],
                    model_revision=None,
                    status="pending",
                    supersedes_suggestion_id=None,
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Lệch expected_suggestion_revision -> 409 REVISION_CONFLICT
            bad_rev = await client.post(
                f"/api/v1/suggestions/{sugg_a_id}/response",
                headers=_auth_headers(user_a, "idem-a-rev"),
                json={"expected_suggestion_revision": 99, "action": "accept"},
            )
            assert bad_rev.status_code == 409
            assert bad_rev.json()["error"]["code"] == "REVISION_CONFLICT"

            # Accept thành công -> effective_from_next_session + tạo change_request_id
            ok_a = await client.post(
                f"/api/v1/suggestions/{sugg_a_id}/response",
                headers=_auth_headers(user_a, "idem-a-ok"),
                json={
                    "expected_suggestion_revision": 1,
                    "expected_preference_revision": 3,
                    "action": "accept",
                },
            )
            assert ok_a.status_code == 200
            da = ok_a.json()["data"]
            assert da["status"] == "accepted"
            assert da["accepted_value"] == [300]
            assert da["application_state"] == "effective_from_next_session"
            assert da["effective_revision"] == 4
            assert da["change_request_id"] is not None
            assert da["pending_proposal_id"] is None

            # Đáp lại gợi ý đã xử lý -> 409 SUGGESTION_CHANGED
            dup_a = await client.post(
                f"/api/v1/suggestions/{sugg_a_id}/response",
                headers=_auth_headers(user_a, "idem-a-dup"),
                json={"expected_suggestion_revision": 2, "action": "reject"},
            )
            assert dup_a.status_code == 409
            assert dup_a.json()["error"]["code"] == "SUGGESTION_CHANGED"

            async with sf() as db:
                run_row = await db.get(SessionRun, seeded_a["run_id"])
                assert run_row is not None
                assert run_row.settings_snapshot["reminder_offsets"] == [900, 600, 300]

        # Kịch bản B (R08): Có phiên scheduled tương lai -> edit_and_accept (trong [1.0, 2.0]) -> pending_review -> UF6 approve
        async with sf() as db:
            user_b = await _seed_user_with_prefs(db, buffer_multiplier=1.1, effective_revision=4)
            await _seed_future_task_and_session(db, user_b, session_status="scheduled")
            sugg_b_id = uuid4()
            db.add(
                AdaptationSuggestion(
                    id=sugg_b_id,
                    user_id=user_b,
                    revision=1,
                    kind="buffer_multiplier",
                    sample_count=6,
                    current_value=1.1,
                    suggested_value=1.45,
                    reason="Trung vị actual/estimate cao hơn cấu hình hiện tại.",
                    policy_revision="v2_step_median_checked_inputs",
                    sample_refs=["s1", "s2", "s3", "s4", "s5", "s6"],
                    model_revision=None,
                    status="pending",
                    supersedes_suggestion_id=None,
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Ngoài miền [1.0, 2.0] -> 422 VALIDATION_ERROR
            out_range = await client.post(
                f"/api/v1/suggestions/{sugg_b_id}/response",
                headers=_auth_headers(user_b, "idem-b-422"),
                json={
                    "expected_suggestion_revision": 1,
                    "action": "edit",
                    "edited_value": 2.5,
                },
            )
            assert out_range.status_code == 422
            assert out_range.json()["error"]["code"] == "VALIDATION_ERROR"

            # Hợp lệ (dùng alias Sheet "decision": "edit_and_accept") -> pending_review + pending_proposal_id
            ok_b = await client.post(
                f"/api/v1/suggestions/{sugg_b_id}/response",
                headers=_auth_headers(user_b, "idem-b-ok"),
                json={
                    "expected_suggestion_revision": 1,
                    "expected_preferences_revision": 4,
                    "decision": "edit_and_accept",
                    "edited_value": 1.3,
                },
            )
            assert ok_b.status_code == 200
            db_res = ok_b.json()["data"]
            assert db_res["action"] == "edit"
            assert db_res["status"] == "accepted"
            assert db_res["accepted_value"] == 1.3
            assert db_res["application_state"] == "pending_review"
            assert db_res["effective_revision"] == 4
            assert db_res["requested_revision"] == 5
            assert db_res["change_request_id"] is not None
            assert db_res["pending_proposal_id"] is not None

            # Duyệt proposal trên UF6 -> buffer_multiplier chuyển sang 1.3 và effective_revision thành 5
            prop_b = (
                await client.get("/api/v1/proposals/current", headers=_auth_headers(user_b))
            ).json()["data"]
            assert prop_b["proposal_id"] == db_res["pending_proposal_id"]
            app_b = await client.post(
                f"/api/v1/proposals/{prop_b['proposal_id']}/approve",
                headers=_auth_headers(user_b, "idem-b-approve"),
                json={
                    "expected_proposal_revision": prop_b["proposal_revision"],
                    "expected_base_schedule_revision": prop_b["base_schedule_revision"],
                    "expected_source_revisions": prop_b["source_revisions"],
                },
            )
            assert app_b.status_code == 200

            pref_b = (
                await client.get("/api/v1/preferences", headers=_auth_headers(user_b))
            ).json()["data"]
            assert pref_b["buffer_multiplier"] == 1.3
            assert pref_b["effective_revision"] == 5
            assert pref_b["pending_proposal_id"] is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_finding6_non_scheduling_pref_update_keeps_pending_proposal_fresh_and_approvable(
    monkeypatch,
):
    """PR #22 Finding #6: Đổi reminder_style/reminder_offsets khi có proposal pending không làm stale=True và approve giữ nguyên giá trị mới."""
    engine, sf = await _make_client_env()
    fixed_now = datetime(2026, 11, 2, 10, 30, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            user_id = await _seed_user_with_prefs(db, buffer_multiplier=1.2, effective_revision=1)
            await _seed_future_task_and_session(db, user_id, session_status="scheduled")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Tạo thay đổi có ảnh hưởng lịch (kèm reminder_style='gentle' trong requested_changes) -> sinh RescheduleProposal pending
            resp_sched = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-f6-sched"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {
                        "preferences": {
                            "work_interval_seconds": 1800,
                            "reminder_style": "gentle",
                        }
                    },
                },
            )
            assert resp_sched.status_code == 200, resp_sched.text
            sched_data = resp_sched.json()["data"]
            proposal_id = sched_data["pending_proposal_id"]
            req_id = sched_data["change_request_id"]
            assert proposal_id is not None

            # 2) Khi proposal đang pending, đổi chỉ reminder_style & reminder_offsets
            resp_non_sched = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-f6-non-sched"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {
                        "preferences": {
                            "reminder_style": "firm",
                            "reminder_offsets": [600, 300],
                        }
                    },
                },
            )
            assert resp_non_sched.status_code == 200, resp_non_sched.text
            non_sched_data = resp_non_sched.json()["data"]
            assert non_sched_data["application_state"] == "effective_from_next_session"
            new_eff = non_sched_data["effective_revision"]
            assert new_eff == 3

            # 3) GET /proposals/current phải giữ stale=False và đồng bộ source_revisions["settings"]
            prop_resp = await client.get("/api/v1/proposals/current", headers=_auth_headers(user_id))
            assert prop_resp.status_code == 200, prop_resp.text
            prop = prop_resp.json()["data"]
            assert prop["proposal_id"] == proposal_id
            assert prop["stale"] is False
            assert prop["source_revisions"]["settings"] == new_eff

            async with sf() as db:
                s_req = await db.get(SettingChangeRow, UUID(req_id))
                pref_row = await db.scalar(
                    select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_id)
                )
                assert s_req is not None and pref_row is not None
                assert s_req.effective_revision == new_eff
                assert s_req.requested_revision == pref_row.requested_revision
                # Key phi-lịch vừa cập nhật ("reminder_style") phải bị gỡ khỏi requested_changes để approve không hoàn tác
                assert pref_row.requested_changes == {"work_interval_seconds": 1800}

            # 4) Approve proposal -> 200 OK, giữ reminder_style='firm' & reminder_offsets=[600, 300], áp dụng work=1800
            app_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(user_id, "idem-f6-approve"),
                json={
                    "expected_proposal_revision": prop["proposal_revision"],
                    "expected_base_schedule_revision": prop["base_schedule_revision"],
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert app_resp.status_code == 200, app_resp.text

            pref_after = (
                await client.get("/api/v1/preferences", headers=_auth_headers(user_id))
            ).json()["data"]
            assert pref_after["work_interval_seconds"] == 1800
            assert pref_after["reminder_style"] == "firm"
            assert pref_after["reminder_offsets"] == [600, 300]
            assert pref_after["pending_proposal_id"] is None
            assert pref_after["requested_revision"] is None

            # 5) Edge case: effective đã là "firm", Request 1b gói work=1500 + reminder_style="gentle",
            # Request 2b gửi lại reminder_style="firm" vẫn phải gỡ "reminder_style" khỏi requested_changes.
            resp_sched_2 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-f6-sched-2"),
                json={
                    "expected_preferences_revision": pref_after["effective_revision"],
                    "changes": {
                        "preferences": {
                            "work_interval_seconds": 1500,
                            "reminder_style": "gentle",
                        }
                    },
                },
            )
            assert resp_sched_2.status_code == 200, resp_sched_2.text
            proposal_2_id = resp_sched_2.json()["data"]["pending_proposal_id"]
            assert proposal_2_id is not None

            resp_non_sched_2 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(user_id, "idem-f6-non-sched-2"),
                json={
                    "expected_preferences_revision": pref_after["effective_revision"],
                    "changes": {"preferences": {"reminder_style": "firm"}},
                },
            )
            assert resp_non_sched_2.status_code == 200, resp_non_sched_2.text
            prop_2 = (
                await client.get("/api/v1/proposals/current", headers=_auth_headers(user_id))
            ).json()["data"]
            app_2_resp = await client.post(
                f"/api/v1/proposals/{proposal_2_id}/approve",
                headers=_auth_headers(user_id, "idem-f6-approve-2"),
                json={
                    "expected_proposal_revision": prop_2["proposal_revision"],
                    "expected_base_schedule_revision": prop_2["base_schedule_revision"],
                    "expected_source_revisions": prop_2["source_revisions"],
                },
            )
            assert app_2_resp.status_code == 200, app_2_resp.text
            pref_after_2 = (
                await client.get("/api/v1/preferences", headers=_auth_headers(user_id))
            ).json()["data"]
            assert pref_after_2["work_interval_seconds"] == 1500
            assert pref_after_2["reminder_style"] == "firm"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
