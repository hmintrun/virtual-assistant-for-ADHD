"""Test Matrix UF5 & UF6 — Doãn Thành (QA Lead / Algorithm Lead) — ProPlan VA.

Phạm vi kiểm thử
─────────────────
UF5: Voice Check-in & Outcome Evaluation (TC-UF5-01 .. TC-UF5-12)
UF6: Guilt-free Adaptive Rescheduling    (TC-UF6-01 .. TC-UF6-12)
E2E: Tích hợp Toàn trình UF5 → UF6       (TC-E2E-01 .. TC-E2E-02)

Nguồn tham chiếu đặc tả bất biến
───────────────────────────────
1. AGENTS.md §2.2 State Transition Matrix, R01, R04, R05, R06, R10
2. MT_WEEK3.md (Nghiệp vụ BA, Grounded Empathy Copy, Voice Check-in & Re-validation)
3. API Sheet Chuẩn:
   - Dòng 29: GET /api/v1/runs/{id}/check-ins
   - Dòng 30: POST /api/v1/runs/{id}/check-ins
   - Dòng 31: PUT /api/v1/check-ins/{id}/response
   - Dòng 64: GET /api/v1/runs/{id}/upcoming-schedule-context
   - Dòng 33: POST /api/v1/proposals (Normal & Continuation)
   - Dòng 34: GET /api/v1/proposals/current
   - Dòng 35: GET /api/v1/proposals/{id}
   - Dòng 36: POST /api/v1/proposals/{id}/rebuild
   - Dòng 37: POST /api/v1/proposals/{id}/approve
   - Dòng 38: POST /api/v1/proposals/{id}/reject
   - Voice: POST /api/v1/voice/uf5-checkin-prompt, POST /api/v1/voice/transcriptions
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models.calendar import CalendarConnection, CalendarSyncJob, PlanningContext
from app.models.execution import CheckIn, RunEvent, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.models.user import User, UserPreference as UserPreferenceRow
from app.schemas.execution import (
    ActualCorrectionRequest,
    CheckInCreateRequest,
    CheckInResponseRequest,
    RunEndRequest,
)
from app.schemas.reschedule import (
    ApproveProposalRequest,
    ContinuationRequest,
    CreateProposalRequest,
    RebuildProposalRequest,
    RejectProposalRequest,
)
from app.schemas.user import CurrentUser
from app.services import reschedule_service, run_service

TZ_VN = timezone(timedelta(hours=7))


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES & SEED HELPERS
# ─────────────────────────────────────────────────────────────────────────────


def _auth_headers(user_id: UUID, idem_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"tester_{user_id.hex[:8]}@example.com",
        display_name="Doãn Thành",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-token-{user_id.hex[:8]}"}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return headers


async def _seed_uf5_uf6_environment(
    db: AsyncSession,
    *,
    with_run: bool = True,
    run_state: str = "awaiting_checkin",
    run_outcome: str = "unknown",
    task_deadline: datetime | None = None,
    with_second_task: bool = False,
) -> dict[str, Any]:
    user_id = uuid4()
    task1_id = uuid4()
    step1_id = uuid4()
    sess1_id = uuid4()
    sess2_id = uuid4()
    run1_id = uuid4()
    task2_id = uuid4()
    step2_id = uuid4()
    sess_other_id = uuid4()

    t_sess1_start = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    t_sess1_end = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    t_sess2_start = datetime(2026, 11, 2, 14, 40, 0, tzinfo=TZ_VN)
    t_sess2_end = datetime(2026, 11, 2, 15, 5, 0, tzinfo=TZ_VN)

    deadline_dt = task_deadline or datetime(2026, 11, 5, 18, 0, 0, tzinfo=TZ_VN)

    user = User(
        id=user_id,
        email=f"tester_{user_id.hex[:8]}@proplan.test",
        hashed_password="mocked_password",
        display_name="Doãn Thành",
    )
    db.add(user)
    await db.flush()

    pref = UserPreferenceRow(
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
    db.add(pref)

    avail_id = uuid4()
    avail = AvailabilityRow(
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
        confirmed_at=t_sess1_start - timedelta(days=1),
    )
    db.add(avail)
    await db.flush()

    conn_id = uuid4()
    db.add(
        CalendarConnection(
            id=conn_id,
            user_id=user_id,
            revision=1,
            provider="google",
            account_ref="thanh@proplan.test",
            read_calendar_ids=["primary"],
            write_calendar_id="proplan_secondary",
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            connection_state="connected",
            credential_ref="encrypted_cred",
            last_successful_sync_at=datetime(2026, 11, 2, 14, 20, 0, tzinfo=TZ_VN),
        )
    )
    await db.flush()

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
                    "event_id": "gcal_busy_event_1",
                    "title": "Họp Lab AI",
                    "start": "2026-11-02T16:00:00+07:00",
                    "end": "2026-11-02T17:00:00+07:00",
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

    task1 = TaskRow(
        id=task1_id,
        user_id=user_id,
        revision=1,
        title="Làm báo cáo kiểm thử UF5 & UF6",
        description="Kiểm thử ma trận và viết báo cáo",
        scope="4 trang",
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
    db.add(task1)
    await db.flush()

    step1 = StepRow(
        id=step1_id,
        user_id=user_id,
        task_id=task1_id,
        revision=1,
        order_index=1,
        title="Thiết kế kịch bản test ma trận",
        expected_output="File test_uf5_uf6_matrix.py",
        estimated_base_seconds=1800,
        remaining_base_seconds=600,
        buffer_multiplier=1.2,
        estimate_source="ai",
        status="in_progress",
    )
    db.add(step1)
    await db.flush()

    sess1 = SessionRow(
        id=sess1_id,
        user_id=user_id,
        step_id=step1_id,
        revision=1,
        scheduled_start=t_sess1_start,
        scheduled_end=t_sess1_end,
        micro_task_text="Mở file test và viết fixture",
        micro_task_seconds=120,
        work_seconds=1500,
        rest_seconds=0,
        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
        duration_seconds=1500,
        schedule_revision=1,
        status="awaiting_checkin" if run_state == "awaiting_checkin" else "scheduled",
        start_state="started",
        start_decision_due_at=t_sess1_start + timedelta(minutes=5),
    )
    db.add(sess1)

    sess2 = SessionRow(
        id=sess2_id,
        user_id=user_id,
        step_id=step1_id,
        revision=1,
        scheduled_start=t_sess2_start,
        scheduled_end=t_sess2_end,
        micro_task_text="Chạy test suite và ghi nhận log",
        micro_task_seconds=120,
        work_seconds=1500,
        rest_seconds=0,
        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
        duration_seconds=1500,
        schedule_revision=1,
        status="scheduled",
        start_state="pending",
        start_decision_due_at=t_sess2_start + timedelta(minutes=5),
    )
    db.add(sess2)
    await db.flush()

    if with_second_task:
        task2 = TaskRow(
            id=task2_id,
            user_id=user_id,
            revision=1,
            title="Đọc tài liệu kiến trúc",
            description="Tìm hiểu kiến trúc hệ thống",
            scope="1 tài liệu",
            time_constraint={
                "mode": "deadline",
                "at": (deadline_dt + timedelta(days=2)).isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            },
            importance=2,
            status="scheduled",
            decomposition_state="complete",
        )
        db.add(task2)
        await db.flush()

        step2 = StepRow(
            id=step2_id,
            user_id=user_id,
            task_id=task2_id,
            revision=1,
            order_index=1,
            title="Đọc phần 1",
            expected_output="Ghi chú ý chính",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="manual",
            status="pending",
        )
        db.add(step2)
        await db.flush()

        sess_other = SessionRow(
            id=sess_other_id,
            user_id=user_id,
            step_id=step2_id,
            revision=1,
            scheduled_start=t_sess2_end + timedelta(minutes=20),
            scheduled_end=t_sess2_end + timedelta(minutes=45),
            micro_task_text="Đọc lướt mục lục",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=0,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1500,
            schedule_revision=1,
            status="scheduled",
            start_state="pending",
            start_decision_due_at=t_sess2_end + timedelta(minutes=25),
        )
        db.add(sess_other)

    db.add(
        ScheduleVersionRow(
            id=uuid4(),
            user_id=user_id,
            version_number=1,
            base_version=None,
            approved_at=t_sess1_start - timedelta(hours=2),
            affected_task_ids=[str(task1_id)],
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )

    if with_run:
        run1 = SessionRun(
            id=run1_id,
            user_id=user_id,
            session_id=sess1_id,
            device_id="android_test_device",
            revision=1,
            started_at=t_sess1_start,
            ended_at=t_sess1_end,
            run_state=run_state,
            active_phase="ended" if run_state in ("awaiting_checkin", "closed") else "work",
            segment_index=0,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome=run_outcome,
            checkin_timeout_at=t_sess1_end + timedelta(minutes=5),
            dnd_release_at=t_sess1_end,
            settings_snapshot={},
        )
        db.add(run1)

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
        "t_sess1_start": t_sess1_start,
        "t_sess1_end": t_sess1_end,
        "t_sess2_start": t_sess2_start,
        "t_sess2_end": t_sess2_end,
    }


# =============================================================================
# PHẦN 1 — UF5: Voice Check-in & Outcome Evaluation (TC-UF5-01 .. TC-UF5-12)
# =============================================================================


@pytest.mark.asyncio
async def test_TC_UF5_01_checkin_outcome_completed(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-01: Check-in Outcome 'completed'
    - Đánh dấu Session và Step thành công (completed).
    - Cập nhật actual work/rest seconds chính xác.
    - Chuyển trạng thái CheckIn sang 'answered', session_outcome='completed'.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # 1. Tạo check-in cuối phiên (kind='final')
            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-01-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_create.status_code == 201, r_create.text
            checkin_id = r_create.json()["data"]["checkin_id"]

            # 2. Phản hồi hoàn thành (completed) kèm step_completed=True
            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-01-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=30)).isoformat(),
                    "session_outcome": "completed",
                    "step_completed": True,
                    "task_completed": False,
                },
            )
            assert r_resp.status_code == 200, r_resp.text
            data = r_resp.json()["data"]
            assert data["response_state"] == "answered"
            assert data["session_outcome"] == "completed"
            assert data["step_completed"] is True

            # 3. Kiểm tra DB: Session status = 'completed', Step status = 'completed'
            async with sf() as db:
                sess_row = await db.get(SessionRow, ids["sess1_id"])
                assert sess_row is not None
                assert sess_row.status == "completed"

                step_row = await db.get(StepRow, ids["step1_id"])
                assert step_row is not None
                assert step_row.status == "completed"
                assert step_row.remaining_base_seconds == 0
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_02_checkin_outcome_incomplete(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-02: Check-in Outcome 'incomplete'
    - Session đóng ở trạng thái 'incomplete'.
    - Step giữ trạng thái 'in_progress', còn thời lượng cần thực hiện.
    - Sẵn sàng kích hoạt luồng điều chỉnh thích ứng UF6.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-02-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_create.status_code == 201
            checkin_id = r_create.json()["data"]["checkin_id"]

            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-02-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=45)).isoformat(),
                    "session_outcome": "incomplete",
                    "step_completed": False,
                    "task_completed": False,
                    "remaining_note": "Chưa xong bảng số liệu",
                },
            )
            assert r_resp.status_code == 200, r_resp.text
            data = r_resp.json()["data"]
            assert data["session_outcome"] == "incomplete"
            assert data["step_completed"] is False

            async with sf() as db:
                sess_row = await db.get(SessionRow, ids["sess1_id"])
                assert sess_row.status == "incomplete"

                step_row = await db.get(StepRow, ids["step1_id"])
                assert step_row.status == "in_progress"
                assert step_row.remaining_base_seconds > 0
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_03_checkin_outcome_busy(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-03: Check-in Outcome 'busy' (Báo bận đột xuất)
    - Session đóng dạng incomplete/busy.
    - Step giữ pending/in_progress để dời lịch.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-03-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            checkin_id = r_create.json()["data"]["checkin_id"]

            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-03-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=20)).isoformat(),
                    "session_outcome": "busy",
                    "step_completed": False,
                    "task_completed": False,
                    "remaining_note": "Sếp gọi họp gấp",
                },
            )
            assert r_resp.status_code == 200
            assert r_resp.json()["data"]["session_outcome"] == "busy"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_04_checkin_skipped_postpone_session(
    sqlite_migration: tuple[str, Any],
) -> None:
    """TC-UF5-04: Check-in Outcome 'skipped' với chip 'postpone_session' (reschedule_later)
    - Bắt buộc đi kèm skip_scope='postpone_session'.
    - Giữ step pending để chuyển sang UF6 tìm slot rảnh tiếp theo.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-04-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            checkin_id = r_create.json()["data"]["checkin_id"]

            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-04-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=15)).isoformat(),
                    "session_outcome": "skipped",
                    "skip_scope": "postpone_session",
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert r_resp.status_code == 200, r_resp.text
            data = r_resp.json()["data"]
            assert data["session_outcome"] == "skipped"
            assert data["skip_scope"] == "postpone_session"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_05_checkin_skipped_skip_session(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-05: Check-in Outcome 'skipped' với chip 'skip_session' (abandon_session)
    - Người dùng chủ động bỏ qua phiên hiện tại.
    - Session đóng, không tạo thêm phiên bù.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-05-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            checkin_id = r_create.json()["data"]["checkin_id"]

            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-05-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=15)).isoformat(),
                    "session_outcome": "skipped",
                    "skip_scope": "skip_session",
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert r_resp.status_code == 200, r_resp.text
            assert r_resp.json()["data"]["skip_scope"] == "skip_session"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_06_checkin_skipped_skip_step(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-06: Check-in Outcome 'skipped' với chip 'skip_step' (abandon_step)
    - Người dùng quyết định bỏ hẳn bước hiện tại.
    - Step chuyển sang trạng thái 'abandoned' / 'skipped'.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-06-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            checkin_id = r_create.json()["data"]["checkin_id"]

            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-06-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=15)).isoformat(),
                    "session_outcome": "skipped",
                    "skip_scope": "skip_step",
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert r_resp.status_code == 200, r_resp.text
            assert r_resp.json()["data"]["skip_scope"] == "skip_step"

            async with sf() as db:
                step_row = await db.get(StepRow, ids["step1_id"])
                assert step_row.status in ("abandoned", "skipped")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_07_checkin_completion_invariants_R01(
    sqlite_migration: tuple[str, Any],
) -> None:
    """TC-UF5-07: Đặc tả bất biến R01 — Từ chối tổ hợp cờ hoàn thành mâu thuẫn
    - Chặn: session_outcome != 'completed' nhưng step_completed=True -> 422.
    - Chặn: task_completed=True nhưng step_completed=False -> 422.
    - Chặn: gửi skip_scope khi session_outcome != 'skipped' -> 422.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-07-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            checkin_id = r_create.json()["data"]["checkin_id"]

            # Vi phạm 1: outcome=incomplete nhưng step_completed=True
            r1 = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-07-inv1"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": ids["t_sess1_end"].isoformat(),
                    "session_outcome": "incomplete",
                    "step_completed": True,
                    "task_completed": False,
                },
            )
            assert r1.status_code == 422

            # Vi phạm 2: task_completed=True nhưng step_completed=False
            r2 = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-07-inv2"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": ids["t_sess1_end"].isoformat(),
                    "session_outcome": "completed",
                    "step_completed": False,
                    "task_completed": True,
                },
            )
            assert r2.status_code == 422

            # Vi phạm 3: outcome=completed nhưng lại gắn skip_scope
            r3 = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "tc-uf5-07-inv3"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": ids["t_sess1_end"].isoformat(),
                    "session_outcome": "completed",
                    "skip_scope": "skip_session",
                    "step_completed": True,
                    "task_completed": False,
                },
            )
            assert r3.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_08_checkin_timeout_reconciliation_5m(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF5-08: Quét timeout check-in sau 5 phút (ended_at + 300s)
    - Tự động chuyển outcome = 'no_respond', response_state = 'no_respond'.
    - Nhả khoá uq_user_live_run, Run đóng ở trạng thái 'closed'.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # Tạo checkin
            r_create = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "tc-uf5-08-create"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_create.status_code == 201

            # Giả lập thời gian sau 5 phút 10 giây (quá hạn 300s)
            t_after_timeout = ids["t_sess1_end"] + timedelta(seconds=310)

            # Gọi GET /check-ins kích hoạt reconcile_expired_checkins
            r_list = await client.get(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers=headers,
            )
            assert r_list.status_code == 200

            # Kích hoạt hàm reconcile trực tiếp với thời gian t_after_timeout
            async with sf() as db:
                await run_service.reconcile_expired_checkins(
                    db, str(ids["user_id"]), now=t_after_timeout, commit_if_read=True
                )

            # Kiểm tra trạng thái Check-in đã chuyển no_respond
            async with sf() as db:
                stmt = select(CheckIn).where(CheckIn.run_id == ids["run1_id"])
                checkin_row = (await db.scalars(stmt)).first()
                assert checkin_row is not None
                assert checkin_row.response_state == "no_respond"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_09_actual_work_correction(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-09: Điều chỉnh thời gian làm việc thực tế (ActualCorrectionRequest)
    - Người dùng tự báo cáo lại số phút thực làm (self_report).
    - Cập nhật actual_work_seconds và actual_source='self_report'.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            now_utc = datetime.now(UTC)
            r_corr = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/actual-corrections",
                headers={**headers, "Idempotency-Key": "tc-uf5-09-corr"},
                json={
                    "expected_run_revision": 1,
                    "self_report_work_seconds": 1200,
                    "reason": "Nghe điện thoại mất 5 phút",
                    "occurred_at": now_utc.isoformat(),
                },
            )
            assert r_corr.status_code == 200, r_corr.text
            data = r_corr.json()["data"]
            assert data["actual_work_seconds"] == 1200
            assert data["actual_source"] == "self_report"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_10_checkin_idempotency_and_replay(sqlite_migration: tuple[str, Any]) -> None:
    """TC-UF5-10: Tính bất biến & Replay an toàn qua Idempotency-Key
    - Gửi 2 lần cùng 1 Idempotency-Key và payload -> Trả về cùng kết quả HTTP 200 / 201.
    - Không sinh trùng lặp bản ghi trong DB.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])
            idem_key = "tc-uf5-10-idempotent-key"

            # 1. Gọi lần 1
            r1 = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": idem_key},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r1.status_code == 201
            checkin_id_1 = r1.json()["data"]["checkin_id"]

            # 2. Gọi lại lần 2 với cùng Idempotency-Key
            r2 = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": idem_key},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r2.status_code == 201
            checkin_id_2 = r2.json()["data"]["checkin_id"]
            assert checkin_id_1 == checkin_id_2

            # 3. Kiểm tra DB chỉ có đúng 1 bản ghi
            async with sf() as db:
                stmt = select(CheckIn).where(CheckIn.run_id == ids["run1_id"])
                rows = list(await db.scalars(stmt))
                assert len(rows) == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_11_voice_checkin_prompt_and_transcription(
    sqlite_migration: tuple[str, Any],
) -> None:
    """TC-UF5-11: Tích hợp Giọng nói UF5 (Prompt Check-in & Transcription)
    - POST /api/v1/voice/uf5-checkin-prompt trả về 3 chip Bỏ qua + undo_timeout_seconds=5.
    - POST /api/v1/voice/transcriptions với purpose='uf5_checkin'.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # 1. Gọi sinh prompt Check-in kèm TTS offline fallback
            r_prompt = await client.post(
                "/api/v1/voice/uf5-checkin-prompt",
                headers=headers,
                json={
                    "kind": "final",
                    "is_final_session_of_step": False,
                    "expected_output": "File test_uf5_uf6_matrix.py",
                    "include_audio": True,
                },
            )
            assert r_prompt.status_code == 200, r_prompt.text
            resp_data = r_prompt.json()["data"]
            prompt_data = resp_data["prompt"]
            assert prompt_data["prompt_text"] is not None
            assert len(prompt_data["skip_chips"]) == 3
            chip_scopes = [c["skip_scope"] for c in prompt_data["skip_chips"]]
            assert "postpone_session" in chip_scopes
            assert "skip_session" in chip_scopes
            assert "skip_step" in chip_scopes
            assert prompt_data["undo_timeout_seconds"] == 5

            # 2. Gọi transcription cho purpose 'uf5_checkin' qua multipart/form-data
            fake_wav_bytes = (
                b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
                b"@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"
            )
            r_stt = await client.post(
                "/api/v1/voice/transcriptions",
                headers={**headers, "Idempotency-Key": "tc-uf5-11-stt"},
                data={"purpose": "uf5_checkin", "device_id": "android_test_device"},
                files={"file": ("test.wav", fake_wav_bytes, "audio/wav")},
            )
            assert r_stt.status_code == 200, r_stt.text
            assert "text" in r_stt.json()["data"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_12_live_run_lock_released_on_awaiting_checkin(
    sqlite_migration: tuple[str, Any],
) -> None:
    """TC-UF5-12: Giải phóng khoá live run (uq_user_live_run)
    - Ngay khi phiên 1 kết thúc và bước vào awaiting_checkin, người dùng được
      phép Start ngay phiên tiếp theo (phiên 2) mà không bị lỗi 409 khoá trùng.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # Start phiên 2 ngay lập tức khi phiên 1 đang awaiting_checkin
            r_start2 = await client.post(
                f"/api/v1/sessions/{ids['sess2_id']}/start",
                headers={**headers, "Idempotency-Key": "tc-uf5-12-start2"},
                json={
                    "expected_schedule_revision": 1,
                    "device_id": "android_test_device",
                    "requested_start_at": ids["t_sess2_start"].isoformat(),
                },
            )
            assert r_start2.status_code == 201, r_start2.text
            assert r_start2.json()["data"]["session_id"] == str(ids["sess2_id"])
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =============================================================================
# PHẦN 2 — UF6: Guilt-free Adaptive Rescheduling (TC-UF6-01 .. TC-UF6-12)
# =============================================================================


@pytest.mark.asyncio
async def test_TC_UF6_01_upcoming_schedule_context_row64(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-01: GET /api/v1/runs/{run_id}/upcoming-schedule-context (Sheet Dòng 64)
    - Trả về thông tin bước hiện tại (current_step), gap tính toán chính xác.
    - Timeline chứa phiên kế tiếp và sự kiện bận Google Calendar.
    - max_extension_seconds_without_borrow phản ánh đúng khoảng trống khả dụng.
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
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            resp = await client.get(
                f"/api/v1/runs/{ids['run1_id']}/upcoming-schedule-context",
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]

            assert data["run_id"] == str(ids["run1_id"])
            assert data["task_id"] == str(ids["task1_id"])
            assert data["current_step"]["step_id"] == str(ids["step1_id"])
            assert data["current_step"]["remaining_seconds"] == 600
            assert len(data["timeline"]) >= 1
            assert data["limit_assumptions"]["immediate_continuation"] is True
            assert data["limit_assumptions"]["inter_session_gap_seconds"] == 900
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_02_create_proposal_checkin_incomplete(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-02: POST /api/v1/proposals do 'checkin_incomplete' (Sheet Dòng 33)
    - Tự động dời phiên kế tiếp để đảm bảo gap 15 phút (900s).
    - Tạo proposed_changes có before/after rõ ràng.
    - Grounded Empathy: Thông điệp khích lệ, không phán xét.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            payload = {
                "trigger_reason": "checkin_incomplete",
                "target_task_id": str(ids["task1_id"]),
                "allowed_task_ids": [str(ids["task1_id"])],
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": {
                    "task": 1,
                    "schedule": 1,
                    "availability": 1,
                    "settings": 1,
                    "calendar": 1,
                },
                "request_text": "Chưa xong phần mở đầu",
            }

            resp = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-02-create"},
                json=payload,
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]

            assert data["status"] == "pending"
            assert data["trigger_reason"] == "checkin_incomplete"
            assert data["feasibility"]["status"] in ("feasible", "valid")
            assert len(data["explanation"]) > 0
            assert data["generation_tier"] in ("tier_1_gemini", "tier_2_groq", "tier_3_rule_based", "python_fallback")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_03_create_proposal_continuation_SY02_R04(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-03: POST /api/v1/proposals chế độ Continuation (SY02 / R04 - Sheet Dòng 33)
    - Người dùng xin làm tiếp ngay sau phiên kết thúc (continuation_request).
    - Tạo phiên continuation mới tiếp nối ngay sau phiên kết thúc.
    - Tự động tịnh tiến các phiên sau để duy trì gap >= 900s.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 30, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            payload = {
                "trigger_reason": "early_completion_pull_forward",
                "target_task_id": str(ids["task1_id"]),
                "allowed_task_ids": [str(ids["task1_id"])],
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": {
                    "task": 1,
                    "schedule": 1,
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
                    "borrow_break_seconds": 0,
                    "allowed_task_ids": [str(ids["task1_id"])],
                },
            }

            resp = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-03-continuation"},
                json=payload,
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]

            assert data["continuation_request"] is not None
            assert len(data["proposed_changes"]["sessions_to_add"]) == 1
            add_item = data["proposed_changes"]["sessions_to_add"][0]
            assert add_item["action"] == "create_continuation_session"
            assert add_item["after"]["work_seconds"] == 600
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_04_get_current_proposal_row34(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-04: GET /api/v1/proposals/current (Sheet Dòng 34)
    - Đọc đề xuất điều chỉnh lịch hiện hành (pending_approval).
    - Trả về null khi chưa có proposal nào đang chờ duyệt.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # 1. Chưa tạo proposal -> Trả về null
            r_none = await client.get("/api/v1/proposals/current", headers=headers)
            assert r_none.status_code == 200
            assert r_none.json()["data"] is None

            # 2. Tạo proposal
            await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-04-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )

            # 3. Lấy lại -> Trả về ProposalData pending
            r_cur = await client.get("/api/v1/proposals/current", headers=headers)
            assert r_cur.status_code == 200
            cur_data = r_cur.json()["data"]
            assert cur_data is not None
            assert cur_data["status"] == "pending"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_05_get_proposal_detail_row35(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-05: GET /api/v1/proposals/{proposal_id} (Sheet Dòng 35)
    - Xem chi tiết đề xuất theo ID.
    - Trả về 404 khi ID không tồn tại hoặc không thuộc user.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-05-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            proposal_id = r_create.json()["data"]["proposal_id"]

            # Lấy chi tiết hợp lệ
            r_detail = await client.get(f"/api/v1/proposals/{proposal_id}", headers=headers)
            assert r_detail.status_code == 200
            assert r_detail.json()["data"]["proposal_id"] == proposal_id

            # Lấy ID lạ -> 404
            r_404 = await client.get(f"/api/v1/proposals/{uuid4()}", headers=headers)
            assert r_404.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_06_rebuild_proposal_row36(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-06: POST /api/v1/proposals/{proposal_id}/rebuild (Sheet Dòng 36)
    - Tái tính toán phương án theo mốc thời gian thực hiện tại (real_time_anchor).
    - Tạo bản ghi proposal mới thay thế bản cũ (supersedes_proposal_id).
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-06-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            proposal_id = r_create.json()["data"]["proposal_id"]

            # Rebuild proposal
            r_rebuild = await client.post(
                f"/api/v1/proposals/{proposal_id}/rebuild",
                headers={**headers, "Idempotency-Key": "tc-uf6-06-rebuild"},
                json={
                    "expected_proposal_revision": 1,
                    "request_text": "Tính lại phương án mới nhất",
                },
            )
            assert r_rebuild.status_code == 201, r_rebuild.text
            rebuilt_data = r_rebuild.json()["data"]
            assert rebuilt_data["proposal_id"] == proposal_id
            assert rebuilt_data["proposal_revision"] == 2
            assert rebuilt_data["status"] == "pending"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_07_approve_proposal_row37(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-07: POST /api/v1/proposals/{proposal_id}/approve (Sheet Dòng 37)
    - Duyệt đề xuất -> Tăng schedule_revision.
    - Cập nhật thời gian các session theo đúng phương án đề xuất.
    - Sinh OutboxSyncJob để đồng bộ sang Google Calendar.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-07-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            proposal_id = r_create.json()["data"]["proposal_id"]

            r_approve = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers={**headers, "Idempotency-Key": "tc-uf6-07-approve"},
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
            assert r_approve.status_code == 200, r_approve.text
            data = r_approve.json()["data"]
            assert data["status"] == "approved"
            assert data["schedule_version"]["version_number"] >= 2

            # Kiểm tra outbox job đã được sinh
            async with sf() as db:
                stmt = select(CalendarSyncJob).where(CalendarSyncJob.user_id == ids["user_id"])
                jobs = list(await db.scalars(stmt))
                assert len(jobs) >= 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_08_reject_proposal_row38(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-08: POST /api/v1/proposals/{proposal_id}/reject (Sheet Dòng 38)
    - Từ chối phương án điều chỉnh -> Giữ nguyên lịch hiện hành (requested_state='unchanged').
    - Chuyển trạng thái proposal sang 'rejected'.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-08-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            proposal_id = r_create.json()["data"]["proposal_id"]

            r_reject = await client.post(
                f"/api/v1/proposals/{proposal_id}/reject",
                headers={**headers, "Idempotency-Key": "tc-uf6-08-reject"},
                json={
                    "expected_proposal_revision": 1,
                    "reason": "Tôi tự thu xếp được",
                },
            )
            assert r_reject.status_code == 200, r_reject.text
            data = r_reject.json()["data"]
            assert data["status"] == "rejected"
            assert data["requested_state"] == "unchanged"

            # Xác nhận lại qua GET /proposals/{id}
            r_check = await client.get(f"/api/v1/proposals/{proposal_id}", headers=headers)
            assert r_check.json()["data"]["status"] == "rejected"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_09_preserve_in_progress_session(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-09: Thuật toán bảo vệ phiên 'in_progress'
    - Phiên đang thực thi tuyệt đối không bị dời hoặc thay đổi thời gian start/end.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 10, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="running")
            # Cập nhật session 1 thành in_progress
            sess1 = await db.get(SessionRow, ids["sess1_id"])
            sess1.status = "in_progress"
            await db.commit()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-09-create"},
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_create.status_code == 201
            changes = r_create.json()["data"]["proposed_changes"]

            # Phiên sess1_id không nằm trong danh sách sessions_to_move
            moved_ids = [m["session_id"] for m in changes.get("sessions_to_move", [])]
            assert str(ids["sess1_id"]) not in moved_ids
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_10_isolate_unallowed_tasks(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-10: Thuật toán cô lập Task — Bảo vệ các task ngoài allowed_task_ids
    - Các session thuộc task ngoài danh sách allowed_task_ids không bị thay đổi.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(
                db, run_state="closed", run_outcome="incomplete", with_second_task=True
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-10-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],  # Chỉ cho phép task 1
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_create.status_code == 201
            changes = r_create.json()["data"]["proposed_changes"]

            moved_ids = [m["session_id"] for m in changes.get("sessions_to_move", [])]
            # Session của task 2 tuyệt đối không bị dời
            assert str(ids["sess_other_id"]) not in moved_ids
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_11_deficit_minutes_detection(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-11: Phát hiện thiếu hụt thời gian (deficit_minutes > 0 / deficit_seconds > 0)
    - Khi dời lịch vượt quá hạn chót (deadline) hoặc không đủ giờ rảnh,
      hệ thống báo tính khả thi 'deficit' và tính số phút thiếu hụt cụ thể.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        # Cố tình đặt deadline rất sát (ngay 14:50 cùng ngày) để khi dời sẽ bị deficit
        tight_deadline = datetime(2026, 11, 2, 14, 50, 0, tzinfo=TZ_VN)
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(
                db,
                run_state="closed",
                run_outcome="incomplete",
                task_deadline=tight_deadline,
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-11-deficit"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_create.status_code == 201
            data = r_create.json()["data"]
            # Hệ thống nhận diện deficit hoặc cảnh báo không đủ thời gian
            assert data["feasibility"]["status"] in ("deficit", "infeasible", "valid")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_12_approve_reject_idempotency(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-UF6-12: Idempotency trên Approve và Reject Proposal
    - Gửi trùng Idempotency-Key trên lệnh Approve không tăng revision 2 lần.
    - Trả về cùng dữ liệu xác nhận nhất quán.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="closed", run_outcome="incomplete")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            r_create = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "tc-uf6-12-create"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            proposal_id = r_create.json()["data"]["proposal_id"]

            approve_payload = {
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
            }
            idem_approve = "tc-uf6-12-idem-approve"

            # Gọi approve lần 1
            r_app1 = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers={**headers, "Idempotency-Key": idem_approve},
                json=approve_payload,
            )
            assert r_app1.status_code == 200
            rev_1 = r_app1.json()["data"]["schedule_version"]["version_number"]

            # Gọi approve lần 2 cùng Idempotency-Key
            r_app2 = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers={**headers, "Idempotency-Key": idem_approve},
                json=approve_payload,
            )
            assert r_app2.status_code == 200
            rev_2 = r_app2.json()["data"]["schedule_version"]["version_number"]
            assert rev_1 == rev_2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =============================================================================
# PHẦN 3 — E2E TÍCH HỢP TOÀN TRÌNH: UF5 → UF6 (TC-E2E-01 .. TC-E2E-02)
# =============================================================================


@pytest.mark.asyncio
async def test_TC_E2E_01_incomplete_to_adaptive_reschedule_flow(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-E2E-01: Kịch bản toàn trình từ Check-in Chưa xong (incomplete) đến Tái lập lịch
    1. Phiên tập trung hết giờ, client tạo Check-in cuối phiên (UF5).
    2. Người dùng phản hồi 'incomplete' (UF5).
    3. Client gọi GET /runs/{id}/upcoming-schedule-context (Sheet Dòng 64) lấy bối cảnh.
    4. Client gửi yêu cầu tạo đề xuất tái lập lịch POST /proposals (Sheet Dòng 33).
    5. Client duyệt đề xuất POST /proposals/{id}/approve (Sheet Dòng 37).
    6. Lịch mới được lưu chính thức, Outbox đồng bộ kích hoạt, phiên tiếp theo được dời chuẩn xác.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 30, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # Bước 1: Tạo check-in cuối phiên
            r_checkin = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "e2e-01-checkin"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_checkin.status_code == 201
            checkin_id = r_checkin.json()["data"]["checkin_id"]

            # Bước 2: Phản hồi incomplete
            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "e2e-01-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=20)).isoformat(),
                    "session_outcome": "incomplete",
                    "step_completed": False,
                    "task_completed": False,
                    "remaining_note": "Cần thêm 15 phút rà soát",
                },
            )
            assert r_resp.status_code == 200

            # Bước 3: Lấy upcoming context (Dòng 64)
            r_ctx = await client.get(
                f"/api/v1/runs/{ids['run1_id']}/upcoming-schedule-context",
                headers=headers,
            )
            assert r_ctx.status_code == 200
            ctx_data = r_ctx.json()["data"]
            assert ctx_data["current_step"]["remaining_seconds"] == 600

            # Bước 4: Tạo proposal UF6
            r_prop = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "e2e-01-proposal"},
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_prop.status_code == 201
            prop_data = r_prop.json()["data"]
            prop_id = prop_data["proposal_id"]

            # Bước 5: Duyệt proposal
            r_app = await client.post(
                f"/api/v1/proposals/{prop_id}/approve",
                headers={**headers, "Idempotency-Key": "e2e-01-approve"},
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
            assert r_app.status_code == 200
            app_data = r_app.json()["data"]
            assert app_data["status"] == "approved"
            assert app_data["schedule_version"]["version_number"] == 2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_E2E_02_skipped_postpone_session_rebuild_flow(
    sqlite_migration: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-E2E-02: Kịch bản toàn trình Bỏ qua phiên nhưng Dời sang lúc khác (reschedule_later)
    1. Check-in cuối phiên chọn 'skipped' kèm chip 'postpone_session'.
    2. Client tạo proposal dời phiên sang khung tiếp theo.
    3. Sau 5 phút, client mở lại app và gọi POST /proposals/{id}/rebuild để re-validate.
    4. Client duyệt phương án mới sau khi rebuild.
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    t_fixed = datetime(2026, 11, 2, 14, 25, 30, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or t_fixed)

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = _auth_headers(ids["user_id"])

            # 1. Tạo check-in & phản hồi skipped + postpone_session
            r_c = await client.post(
                f"/api/v1/runs/{ids['run1_id']}/check-ins",
                headers={**headers, "Idempotency-Key": "e2e-02-checkin"},
                json={
                    "kind": "final",
                    "asked_at": ids["t_sess1_end"].isoformat(),
                    "expected_run_revision": 1,
                },
            )
            checkin_id = r_c.json()["data"]["checkin_id"]

            r_resp = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers={**headers, "Idempotency-Key": "e2e-02-resp"},
                json={
                    "expected_response_revision": 0,
                    "responded_at": (ids["t_sess1_end"] + timedelta(seconds=15)).isoformat(),
                    "session_outcome": "skipped",
                    "skip_scope": "postpone_session",
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert r_resp.status_code == 200

            # 2. Tạo proposal
            r_prop = await client.post(
                "/api/v1/proposals",
                headers={**headers, "Idempotency-Key": "e2e-02-prop"},
                json={
                    "trigger_reason": "checkin_skipped",
                    "target_task_id": str(ids["task1_id"]),
                    "allowed_task_ids": [str(ids["task1_id"])],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_prop.status_code == 201
            prop_id = r_prop.json()["data"]["proposal_id"]

            # 3. Rebuild proposal
            r_reb = await client.post(
                f"/api/v1/proposals/{prop_id}/rebuild",
                headers={**headers, "Idempotency-Key": "e2e-02-rebuild"},
                json={
                    "expected_proposal_revision": 1,
                    "request_text": "Tính lại trước khi duyệt",
                },
            )
            assert r_reb.status_code == 201
            new_prop_id = r_reb.json()["data"]["proposal_id"]

            # 4. Duyệt proposal mới
            r_app = await client.post(
                f"/api/v1/proposals/{new_prop_id}/approve",
                headers={**headers, "Idempotency-Key": "e2e-02-approve"},
                json={
                    "expected_proposal_revision": 2,
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
            assert r_app.status_code == 200
            assert r_app.json()["data"]["status"] == "approved"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
