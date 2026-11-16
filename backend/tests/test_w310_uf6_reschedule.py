"""Kiểm thử toàn diện Gói W3-10 (UF6: Tái lập lịch Thích ứng, Upcoming Schedule Context & Continuation)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models.calendar import CalendarConnection, PlanningContext
from app.models.execution import SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.user import CurrentUser
from app.services import reschedule_service

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


async def _seed_uf6_fixture(
    db: AsyncSession,
    *,
    with_availability: bool = True,
    with_calendar_busy: bool = True,
    with_second_task: bool = False,
) -> dict[str, UUID]:
    """Tạo dữ liệu chuẩn bám sát ví dụ Sheet Dòng 64 và Dòng 33–38."""
    user_id = uuid4()
    task1_id = uuid4()
    step1_id = uuid4()
    sess1_id = uuid4()
    sess2_id = uuid4()
    run1_id = uuid4()
    task2_id = uuid4()
    step2_id = uuid4()
    sess_other_id = uuid4()

    t_run_start = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    t_run_end = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    t_sess2_start = datetime(2026, 11, 2, 14, 40, 0, tzinfo=TZ_VN)
    t_sess2_end = datetime(2026, 11, 2, 15, 5, 0, tzinfo=TZ_VN)

    db.add(
        User(
            id=user_id,
            email=f"uf6_{user_id.hex[:8]}@example.com",
            hashed_password="hashed_pw",
            display_name="Võ Quốc Hưng",
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

    avail_id = uuid4()
    if with_availability:
        db.add(
            AvailabilityRow(
                id=avail_id,
                user_id=user_id,
                revision=2,
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[
                    {
                        "day_of_week": d,
                        "start_local_time": "08:00",
                        "end_local_time": "22:00",
                    }
                    for d in range(7)
                ],
                date_overrides=[],
                task_limits=[],
                source="manual",
                confirmed_at=t_run_start - timedelta(days=1),
            )
        )
    await db.flush()

    conn_id = uuid4()
    db.add(
        CalendarConnection(
            id=conn_id,
            user_id=user_id,
            revision=1,
            provider="google",
            account_ref="vqh@example.com",
            read_calendar_ids=["primary"],
            write_calendar_id="proplan_secondary",
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            connection_state="connected",
            credential_ref="encrypted_cred_ref",
            last_successful_sync_at=datetime(2026, 11, 2, 14, 20, 0, tzinfo=TZ_VN),
        )
    )
    await db.flush()

    if with_calendar_busy and with_availability:
        db.add(
            PlanningContext(
                id=uuid4(),
                user_id=user_id,
                revision=1,
                availability_id=avail_id,
                availability_revision=2,
                selected_calendar_ids=["primary"],
                query_start=datetime(2026, 11, 2, 0, 0, 0, tzinfo=TZ_VN),
                query_end=datetime(2026, 11, 9, 0, 0, 0, tzinfo=TZ_VN),
                busy_intervals=[],
                fetched_at=datetime(2026, 11, 2, 14, 20, 0, tzinfo=TZ_VN),
                sync_state="ok",
                calendar_errors=[],
                event_snapshots=[
                    {
                        "event_id": "gcal_event_9",
                        "title": "Họp nhóm",
                        "start": "2026-11-02T15:15:00+07:00",
                        "end": "2026-11-02T16:00:00+07:00",
                        "is_locked": True,
                        "is_movable": False,
                    }
                ],
                coverage_state="complete",
                manual_busy_intervals=[],
                excluded_sources=[],
                selected_calendar_sources=[],
            )
        )

    db.add(
        TaskRow(
            id=task1_id,
            user_id=user_id,
            revision=1,
            title="Hoàn thành báo cáo tuần",
            description="Viết và rà soát báo cáo",
            scope="5 trang",
            time_constraint={
                "mode": "deadline",
                "at": "2026-11-05T18:00:00+07:00",
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
            task_id=task1_id,
            revision=1,
            order_index=1,
            title="Viết phần mở đầu",
            expected_output="Xong dàn ý và 3 đoạn đầu",
            estimated_base_seconds=1800,
            remaining_base_seconds=600,
            buffer_multiplier=1.2,
            estimate_source="ai",
            status="in_progress",
        )
    )
    await db.flush()

    db.add(
        SessionRow(
            id=sess1_id,
            user_id=user_id,
            step_id=step1_id,
            revision=2,
            scheduled_start=t_run_start,
            scheduled_end=t_run_end,
            micro_task_text="Mở tài liệu báo cáo",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=0,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1500,
            schedule_revision=3,
            status="incomplete",
            start_state="started",
            start_decision_due_at=t_run_start + timedelta(minutes=5),
        )
    )
    db.add(
        SessionRow(
            id=sess2_id,
            user_id=user_id,
            step_id=step1_id,
            revision=1,
            scheduled_start=t_sess2_start,
            scheduled_end=t_sess2_end,
            micro_task_text="Viết tiếp phần kết",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=0,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1500,
            schedule_revision=3,
            status="scheduled",
            start_state="pending",
            start_decision_due_at=t_sess2_start + timedelta(minutes=5),
        )
    )

    if with_second_task:
        db.add(
            TaskRow(
                id=task2_id,
                user_id=user_id,
                revision=1,
                title="Ôn tập bài giảng",
                description="Ôn tập chương 3",
                scope="Chương 3",
                time_constraint={
                    "mode": "deadline",
                    "at": "2026-11-06T18:00:00+07:00",
                    "timezone": "Asia/Ho_Chi_Minh",
                },
                importance=2,
                status="pending",
                decomposition_state="complete",
                unexpanded_scope=None,
            )
        )
        await db.flush()
        db.add(
            StepRow(
                id=step2_id,
                user_id=user_id,
                task_id=task2_id,
                revision=1,
                order_index=1,
                title="Đọc slide chương 3",
                expected_output="Tóm tắt được ý chính",
                estimated_base_seconds=1500,
                remaining_base_seconds=1500,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="pending",
            )
        )
        await db.flush()
        # Đặt phiên của task2 sát ngay 14:40 để test UNALLOWED_TASK_COLLISION
        db.add(
            SessionRow(
                id=sess_other_id,
                user_id=user_id,
                step_id=step2_id,
                revision=1,
                scheduled_start=t_sess2_start,
                scheduled_end=t_sess2_end,
                micro_task_text="Mở slide chương 3",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=3,
                status="scheduled",
                start_state="pending",
                start_decision_due_at=t_sess2_start + timedelta(minutes=5),
            )
        )
    await db.flush()

    db.add(
        ScheduleVersionRow(
            id=uuid4(),
            user_id=user_id,
            version_number=3,
            base_version=2,
            approved_at=t_run_start - timedelta(hours=1),
            affected_task_ids=[str(task1_id)],
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )

    db.add(
        SessionRun(
            id=run1_id,
            user_id=user_id,
            session_id=sess1_id,
            device_id="android_pixel_8",
            revision=4,
            started_at=t_run_start,
            ended_at=t_run_end,
            run_state="closed",
            active_phase="ended",
            segment_index=0,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="incomplete",
            checkin_timeout_at=t_run_end + timedelta(minutes=5),
            dnd_release_at=t_run_end,
            settings_snapshot={},
        )
    )

    await db.commit()
    return {
        "user_id": user_id,
        "task1_id": task1_id,
        "step1_id": step1_id,
        "sess1_id": sess1_id,
        "sess2_id": sess2_id,
        "run1_id": run1_id,
        "task2_id": task2_id,
        "step2_id": step2_id,
        "sess_other_id": sess_other_id,
    }


@pytest.mark.asyncio
async def test_upcoming_schedule_context_row64_contract(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kiểm tra Dòng 64: GET /api/v1/runs/{run_id}/upcoming-schedule-context khớp toán học Sheet."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/runs/{ids['run1_id']}/upcoming-schedule-context",
                headers=_auth_headers(ids["user_id"]),
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()["data"]

            assert body["run_id"] == str(ids["run1_id"])
            assert body["task_id"] == str(ids["task1_id"])
            assert body["current_step"]["step_id"] == str(ids["step1_id"])
            assert body["current_step"]["remaining_seconds"] == 600
            assert body["source_run_revision"] == 1
            assert body["source_revisions"] == {
                "task": 1,
                "schedule": 3,
                "availability": 1,
                "settings": 1,
                "calendar": 1,
            }
            assert body["max_extension_seconds_without_borrow"] == 480
            assert body["recommended_extension_seconds"] == [300, 480]
            assert body["limit_assumptions"]["immediate_continuation"] is True
            assert body["limit_assumptions"]["inter_session_gap_seconds"] == 900
            assert len(body["timeline"]) == 2
            assert body["timeline"][0]["kind"] == "session"
            assert body["timeline"][0]["session_id"] == str(ids["sess2_id"])
            assert body["timeline"][1]["kind"] == "google_busy"
            assert body["timeline"][1]["event_id"] == "gcal_event_9"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_proposal_insufficient_data_blocks_approval(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kiểm tra Dòng 33 Variant 1: Khi chưa có Availability xác nhận -> insufficient_data & chặn approve 409."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db, with_availability=False, with_calendar_busy=False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-prop-insuff-1"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 0,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "request_text": "Dời phần còn lại sang chiều nay",
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            prop = create_resp.json()["data"]
            assert prop["status"] == "pending"
            assert prop["feasibility"]["status"] == "insufficient_data"
            assert prop["feasibility"]["confirmable"] is False
            assert prop["feasibility"]["blocking_reasons"][0]["code"] == "AVAILABILITY_NOT_CONFIRMED"

            # Cố tình approve khi insufficient_data -> 409 PLAN_NOT_CONFIRMABLE
            app_resp = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "idem-app-insuff-1"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 0,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert app_resp.status_code == 409
            assert app_resp.json()["error"]["code"] == "PLAN_NOT_CONFIRMABLE"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_normal_proposal_e2e_stale_rebuild_and_approve(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kiểm tra luồng E2E chuẩn R06: create -> current -> đóng/mở lại app (stale) -> rebuild -> approve."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    clock = {"now": datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)}
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or clock["now"])

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Tạo đề xuất dời lịch thường
            create_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-norm-create-1"),
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
                    "request_text": "Lùi lịch lại giúp tôi",
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            prop = create_resp.json()["data"]
            proposal_id = prop["proposal_id"]
            assert prop["stale"] is False
            assert len(prop["proposed_changes"]["sessions_to_move"]) == 1

            # Đảm bảo lịch chính thức trong DB chưa bị thay đổi khi chưa approve
            async with sf() as db:
                s2 = await db.get(SessionRow, ids["sess2_id"])
                assert s2 is not None
                assert s2.schedule_revision == 3

            # 2. Giả lập người dùng đóng app và mở lại sau 3 tiếng (ô lịch đề xuất đã trôi qua)
            clock["now"] = datetime(2026, 11, 2, 18, 0, 0, tzinfo=TZ_VN)

            cur_resp = await client.get(
                "/api/v1/proposals/current",
                headers=_auth_headers(ids["user_id"]),
            )
            assert cur_resp.status_code == 200
            assert cur_resp.json()["data"]["proposal_id"] == proposal_id
            assert cur_resp.json()["data"]["stale"] is True

            # Cố bấm approve bản đã stale -> bị chặn 409 PREVIEW_STALE
            stale_app = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-norm-stale-app"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert stale_app.status_code == 409
            assert stale_app.json()["error"]["code"] == "PREVIEW_STALE"

            # 3. Gọi rebuild tại thời điểm mở lại app (18:00) -> cập nhật lên proposal_revision = 2, stale = False
            rebuild_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/rebuild",
                headers=_auth_headers(ids["user_id"], "idem-norm-rebuild-1"),
                json={
                    "expected_proposal_revision": 1,
                    "request_text": "Dời sang sáng mai",
                },
            )
            assert rebuild_resp.status_code == 201, rebuild_resp.text
            rebuilt = rebuild_resp.json()["data"]
            assert rebuilt["proposal_id"] == proposal_id
            assert rebuilt["proposal_revision"] == 2
            assert rebuilt["stale"] is False

            # 4. Duyệt đề xuất sau khi rebuild (proposal_revision = 2)
            approve_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-norm-approve-1"),
                json={
                    "expected_proposal_revision": 2,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert approve_resp.status_code == 200, approve_resp.text
            approved_data = approve_resp.json()["data"]
            assert approved_data["status"] == "approved"
            assert approved_data["schedule_version"]["version_number"] == 4
            assert approved_data["calendar_sync_job_enqueued"] is True

            # Sau khi approve, GET /proposals/current trả về data: null
            after_cur = await client.get(
                "/api/v1/proposals/current",
                headers=_auth_headers(ids["user_id"]),
            )
            assert after_cur.status_code == 200
            assert after_cur.json()["data"] is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_continuation_sy02_r04_r05_and_anti_chaining(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kiểm tra SY02 / R04 / R05:
    - Tạo Continuation Proposal với immediate_continuation=true (120s) + borrow_break_seconds=120 (780s).
    - Kiểm tra R05: bấm duyệt trễ 30s (14:27:30) làm lố approval_envelope -> từ chối 409 PREVIEW_STALE.
    - Bấm duyệt đúng cửa sổ (14:27:00) với start_continuation=true -> tạo Session + SessionRun mới.
    - Kiểm tra R04: chống mượn nối chuỗi (từ chối xin continuation lần 2).
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    clock = {"now": datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)}
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or clock["now"])

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Tạo Continuation Proposal (additional_work_seconds = 600, borrow_break_seconds = 120)
            cont_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-cont-create-1"),
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
                        "additional_work_seconds": 600,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 120,
                        "allowed_task_ids": [str(ids["task1_id"])],
                    },
                },
            )
            assert cont_resp.status_code == 201, cont_resp.text
            prop = cont_resp.json()["data"]
            proposal_id = prop["proposal_id"]

            # Kiểm tra 2 ngoại lệ R04 trong gap_exceptions
            gap_reasons = {g["reason"]: g["applied_gap_seconds"] for g in prop["gap_exceptions"]}
            assert gap_reasons == {
                "immediate_continuation": 120,
                "user_borrowed_break": 780,
            }
            assert prop["approval_envelope"]["continuation_fixed_work_seconds"] == 600
            assert prop["approval_envelope"]["continuation_min_break_after_seconds"] == 780
            assert len(prop["proposed_changes"]["sessions_to_add"]) == 1
            assert len(prop["proposed_changes"]["sessions_to_move"]) == 1
            assert prop["proposed_changes"]["sessions_to_move"][0]["shift_seconds"] == 600

            # 2. R05 Guard: Giả lập người dùng bấm Approve trễ 30s (14:27:30 > not_after 14:27:00)
            clock["now"] = datetime(2026, 11, 2, 14, 27, 30, tzinfo=TZ_VN)
            late_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-cont-approve-late"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "start_continuation": True,
                    "device_id": "android_pixel_8",
                    "event_id": "evt_cont_late",
                    "expected_source_run_revision": 1,
                },
            )
            assert late_resp.status_code == 409
            assert late_resp.json()["error"]["code"] == "PREVIEW_STALE"

            # 3. Bấm Approve đúng cửa sổ hợp lệ (14:27:00)
            clock["now"] = datetime(2026, 11, 2, 14, 27, 0, tzinfo=TZ_VN)
            ok_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-cont-approve-ok"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "start_continuation": True,
                    "device_id": "android_pixel_8",
                    "event_id": "evt_cont_start_1",
                    "expected_source_run_revision": 1,
                },
            )
            assert ok_resp.status_code == 200, ok_resp.text
            app_data = ok_resp.json()["data"]
            assert app_data["status"] == "approved"
            assert app_data["updated_schedule_revision"] == 4
            assert app_data["continuation_session_id"] is not None
            assert app_data["continuation_run"] is not None
            assert app_data["continuation_run"]["run_state"] == "running"
            assert app_data["continuation_run"]["continuation_of_run_id"] == str(ids["run1_id"])
            assert app_data["continuation_run"]["settings_snapshot"]["session"]["work_seconds"] == 600

            cont_run_id = app_data["continuation_run"]["run_id"]

            # 4. Kết thúc phiên continuation_run và thử xin mượn nối chuỗi lần 2 (R04 Anti-chaining)
            end_cont = await client.post(
                f"/api/v1/runs/{cont_run_id}/end",
                headers=_auth_headers(ids["user_id"], "idem-end-cont-run"),
                json={
                    "device_id": "android_pixel_8",
                    "expected_run_revision": 1,
                    "event_id": "evt_cont_end_1",
                    "ended_at": "2026-11-02T14:37:00+07:00",
                    "end_reason": "timer_elapsed",
                    "work_elapsed_seconds": 600,
                    "rest_elapsed_seconds": 0,
                },
            )
            assert end_cont.status_code == 200

            chain_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-cont-chain-block"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 4,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 4,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": cont_run_id,
                        "expected_run_revision": 2,
                        "additional_work_seconds": 300,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task1_id"])],
                    },
                },
            )
            assert chain_resp.status_code == 409
            assert chain_resp.json()["error"]["code"] == "CONTINUATION_LIMIT_REACHED"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_proposal_supersede_reject_and_collision_guards(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kiểm tra:
    - Chặn UNALLOWED_TASK_COLLISION khi dời trúng phiên của Task khác không nằm trong allowed_task_ids.
    - Chặn LOCKED_CALENDAR_COLLISION khi làm thêm quá dài đụng sự kiện cố định trên lịch.
    - Supersede khi tạo proposal mới đè lên proposal pending cũ.
    - Reject proposal giữ nguyên lịch cũ (requested_state='unchanged').
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db, with_second_task=True)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. UNALLOWED_TASK_COLLISION: không cho phép dời task2_id
            unallowed_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-unallowed-1"),
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
                        "additional_work_seconds": 300,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task1_id"])],
                    },
                },
            )
            assert unallowed_resp.status_code == 409
            assert unallowed_resp.json()["error"]["code"] == "UNALLOWED_TASK_COLLISION"

            # 2. LOCKED_CALENDAR_COLLISION: cho phép cả 2 task nhưng xin làm thêm 1500s -> đụng Họp nhóm 15:15
            locked_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-locked-1"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"]), str(ids["task2_id"])],
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
                        "additional_work_seconds": 1500,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task1_id"]), str(ids["task2_id"])],
                    },
                },
            )
            assert locked_resp.status_code == 409
            assert locked_resp.json()["error"]["code"] == "LOCKED_CALENDAR_COLLISION"

            # 3. Tạo Proposal 1 rồi tạo Proposal 2 -> Proposal 1 chuyển sang superseded
            p1_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-sup-p1"),
                json={
                    "trigger_reason": "user_reschedule",
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
                },
            )
            assert p1_resp.status_code == 201
            p1_id = p1_resp.json()["data"]["proposal_id"]

            p2_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-sup-p2"),
                json={
                    "trigger_reason": "user_reschedule",
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
                    "request_text": "Dời sang sáng mai",
                },
            )
            assert p2_resp.status_code == 201
            p2_data = p2_resp.json()["data"]
            p2_id = p2_data["proposal_id"]
            assert p2_data["supersedes_proposal_id"] == p1_id

            p1_detail = await client.get(
                f"/api/v1/proposals/{p1_id}",
                headers=_auth_headers(ids["user_id"]),
            )
            assert p1_detail.status_code == 200
            assert p1_detail.json()["data"]["status"] == "superseded"

            # 4. Reject Proposal 2 -> status='rejected', requested_state='unchanged'
            rej_resp = await client.post(
                f"/api/v1/proposals/{p2_id}/reject",
                headers=_auth_headers(ids["user_id"], "idem-rej-p2"),
                json={
                    "expected_proposal_revision": 1,
                    "reason": "Giữ lịch cũ",
                },
            )
            assert rej_resp.status_code == 200
            assert rej_resp.json()["data"] == {
                "proposal_id": p2_id,
                "status": "rejected",
                "requested_state": "unchanged",
            }
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotency_active_run_source_changed_and_uf7_link(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kiểm tra:
    - Idempotency replay & IDEMPOTENCY_KEY_REUSED (409).
    - Chặn 404 RESOURCE_NOT_FOUND khi user khác truy cập proposal.
    - Chặn 409 ACTIVE_RUN_EXISTS khi đang có SessionRun chạy song song.
    - Chặn 409 SOURCE_CHANGED khi lịch nền đã đổi trước lúc approve.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "trigger_reason": "settings_change",
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
                "request_text": "Cập nhật thời lượng phiên từ cài đặt",
            }
            r1 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-replay-1"),
                json=payload,
            )
            assert r1.status_code == 201
            prop_id = r1.json()["data"]["proposal_id"]

            # Replay cùng Idempotency-Key + cùng payload -> trả về đúng proposal_id
            r1_replay = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-replay-1"),
                json=payload,
            )
            assert r1_replay.status_code == 201
            assert r1_replay.json()["data"]["proposal_id"] == prop_id

            # Dùng lại Idempotency-Key với payload khác -> 409 IDEMPOTENCY_KEY_REUSED
            r1_conflict = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-replay-1"),
                json={**payload, "request_text": "Nội dung khác"},
            )
            assert r1_conflict.status_code == 409
            assert r1_conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

            # User khác truy cập -> 404 RESOURCE_NOT_FOUND
            other_user = uuid4()
            other_resp = await client.get(
                f"/api/v1/proposals/{prop_id}",
                headers=_auth_headers(other_user),
            )
            assert other_resp.status_code == 404
            assert other_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

            # Đang có SessionRun running -> approve trả về 409 ACTIVE_RUN_EXISTS
            async with sf() as db:
                run_row = await db.get(SessionRun, ids["run1_id"])
                assert run_row is not None
                run_row.run_state = "running"
                await db.commit()

            act_resp = await client.post(
                f"/api/v1/proposals/{prop_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-act-run"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert act_resp.status_code == 409
            assert act_resp.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"

            # Đóng lại run, nhưng tăng ScheduleVersion lên 4 ở ngoài -> approve trả về 409 SOURCE_CHANGED
            async with sf() as db:
                run_row = await db.get(SessionRun, ids["run1_id"])
                assert run_row is not None
                run_row.run_state = "closed"
                db.add(
                    ScheduleVersionRow(
                        id=uuid4(),
                        user_id=ids["user_id"],
                        version_number=4,
                        base_version=3,
                        approved_at=fixed_now,
                        affected_task_ids=[str(ids["task1_id"])],
                        min_inter_session_gap_seconds=900,
                        alarm_sync_state="synced",
                    )
                )
                await db.commit()

            src_resp = await client.post(
                f"/api/v1/proposals/{prop_id}/approve",
                headers=_auth_headers(ids["user_id"], "idem-src-changed"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert src_resp.status_code == 409
            assert src_resp.json()["error"]["code"] == "SOURCE_CHANGED"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_uf6_create_proposal_wires_ai_empathy_and_generation_tier(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """UF6: create_proposal gọi ai_service.generate_reschedule_empathy và lưu explanation + generation_tier."""
    from unittest.mock import AsyncMock

    from sqlalchemy import select

    from app.core.config import Settings
    from app.models.schedule_proposal import RescheduleProposal
    from app.schemas.schedule import RescheduleEmpatheticResult
    from app.services.ai_service import AIService

    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 10, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    empathy_mock = AsyncMock(
        return_value=RescheduleEmpatheticResult(
            empathetic_note="Hôm nay có chút việc chen ngang, không sao đâu nhé.",
            encouragement_tip="Mình nghỉ 2 phút rồi bắt đầu lại nhẹ nhàng nha.",
            generation_tier="tier_1_gemini",
        )
    )
    monkeypatch.setattr(
        reschedule_service.ai_service,
        "generate_reschedule_empathy",
        empathy_mock,
    )

    try:
        async with sf() as db:
            ids = await _seed_uf6_fixture(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-uf6-ai-tier1"),
                json={
                    "trigger_reason": "checkin_busy",
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
                    "request_text": "Có việc bận đột xuất",
                },
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]
            assert data["generation_tier"] == "tier_1_gemini"
            assert "Hôm nay có chút việc chen ngang" in data["explanation"]
            assert "Mình nghỉ 2 phút rồi bắt đầu lại nhẹ nhàng nha." in data["explanation"]

            empathy_mock.assert_awaited_once()
            call_kwargs = empathy_mock.await_args.kwargs
            assert call_kwargs["trigger_reason"] == "busy"
            assert call_kwargs["task_title"] is None
            assert call_kwargs["proposal_available"] is True

            async with sf() as db:
                row = await db.scalar(
                    select(RescheduleProposal).where(RescheduleProposal.id == UUID(data["proposal_id"]))
                )
                assert row is not None
                assert row.generation_tier == "tier_1_gemini"
                assert "Hôm nay có chút việc chen ngang" in row.explanation

            # Kiểm tra Fallback Tier 3 thật khi không có API key (không mock generate_reschedule_empathy)
            offline_ai = AIService(
                app_settings=Settings(_env_file=None, GEMINI_API_KEY="", GROQ_API_KEY="")
            )
            monkeypatch.setattr(reschedule_service, "ai_service", offline_ai)

            fb_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "idem-uf6-ai-tier3"),
                json={
                    "trigger_reason": "checkin_skipped",
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
                    "request_text": "Bỏ qua phiên này",
                },
            )
            assert fb_resp.status_code == 201, fb_resp.text
            fb_data = fb_resp.json()["data"]
            assert fb_data["generation_tier"] == "tier_3_rule_based"
            assert isinstance(fb_data["explanation"], str) and len(fb_data["explanation"]) > 10
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
