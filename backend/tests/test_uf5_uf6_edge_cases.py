"""Test Edge Cases & Failure Recovery Suite for UF5 & UF6 — ProPlan VA.

File: backend/tests/test_uf5_uf6_edge_cases.py
Lead Author: Doãn Thành (QA Lead / Algorithm Lead)

Các góc khuất và kịch bản rủi ro được bao phủ:
─────────────────────────────────────────────
1. TC_UF5_EDGE_01: Hiệu chỉnh câu trả lời Check-in (Correction Rollback):
   Check-in completed -> sửa lại incomplete -> Rollback Step về in_progress và khôi phục remaining_base_seconds.
2. TC_UF5_EDGE_02: Vi phạm cờ Skip Scope:
   Gửi skip_scope khi outcome != 'skipped' hoặc outcome == 'skipped' mà thiếu skip_scope -> Bắt buộc ném 422.
3. TC_UF5_EDGE_03: Mở check-in giữa phiên (mid) khi phiên đã kết thúc:
   Run state là awaiting_checkin hoặc closed -> ném 409 INVALID_RUN_STATE.
4. TC_UF5_EDGE_04: Mở check-in cuối phiên (final) khi phiên vẫn đang chạy:
   Run state là running -> ném 409 INVALID_RUN_STATE.
5. TC_UF5_EDGE_05: Phục hồi câu trả lời sau khi Check-in bị Timeout Lazy Sweep:
   Check-in chuyển no_respond -> Client gửi câu trả lời muộn với revision=0 -> Chấp nhận và cập nhật lịch.
6. TC_UF6_EDGE_01: Phục hồi xung đột phiên bản lịch (Revision Conflict & Recovery):
   Gửi expected_source_revisions cũ -> Nhận 409 REVISION_CONFLICT -> Cập nhật revisions mới và retry -> Thành công (201 Created).
7. TC_UF6_EDGE_02: Chặn duyệt đề xuất khi kế hoạch không khả thi (Infeasible / Deficit):
   Đề xuất có confirmable=False do thiếu hụt giờ -> Gọi Approve -> Bắt buộc ném 409 PLAN_NOT_CONFIRMABLE.
8. TC_UF6_EDGE_03: Chặn duyệt đề xuất khi đang có phiên tập trung chạy (Active Run Concurrency Block):
   Có SessionRun đang ở trạng thái running -> Gọi Approve -> Bắt buộc ném 409 RUN_IN_PROGRESS.
9. TC_UF6_EDGE_04: Chặn duyệt đề xuất đã bị vô hiệu hóa (Superseded Proposal Block):
   Proposal bị superseded bởi đề xuất mới -> Cố tình Approve proposal cũ -> Bắt buộc ném 409 PROPOSAL_CHANGED.
10. TC_UF6_EDGE_05: Va chạm sự kiện cố định trên Google Calendar khi Làm tiếp ngay (Continuation Collision):
    Có sự kiện bận Google Calendar sát phiên -> Bấm continue_now -> Bắt buộc ném 409 LOCKED_CALENDAR_COLLISION.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models.calendar import PlanningContext
from app.models.execution import CheckIn, SessionRun
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.services import reschedule_service, run_service
from tests.test_uf5_uf6_matrix import TZ_VN, _auth_headers, _seed_uf5_uf6_environment

# ─────────────────────────────────────────────────────────────────────────────
# 1. UF5 EDGE TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_TC_UF5_EDGE_01_correction_rollback_matrix(
    sqlite_migration: tuple[str, Any],
) -> None:
    """Check-in completed -> sửa lại incomplete -> Rollback Step về in_progress và khôi phục remaining_base_seconds."""
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
            user_id = ids["user_id"]
            run_id = ids["run1_id"]
            step1_id = ids["step1_id"]
            t_end = ids["t_sess1_end"]

        headers = _auth_headers(user_id, idem_key=f"idem-checkin-c1-{uuid4().hex[:6]}")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Bước 1: Mở check-in cuối phiên
            r_open = await client.post(
                f"/api/v1/runs/{run_id}/check-ins",
                headers=headers,
                json={
                    "kind": "final",
                    "asked_at": t_end.isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_open.status_code == 201, r_open.text
            checkin_id = r_open.json()["data"]["checkin_id"]

            # Bước 2: Người dùng lỡ bấm "completed" -> Step & Task chuyển completed
            r_ans1 = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers=_auth_headers(user_id, idem_key=f"idem-ans1-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (t_end + timedelta(seconds=30)).isoformat(),
                    "session_outcome": "completed",
                    "step_completed": True,
                    "task_completed": False,
                },
            )
            assert r_ans1.status_code == 200, r_ans1.text

            # Xác minh step chuyển completed và remaining = 0
            async with sf() as db:
                step = await db.get(StepRow, step1_id)
                assert step is not None
                assert step.status == "completed"
                assert step.remaining_base_seconds == 0

            # Bước 3: Người dùng bấm sửa lại câu trả lời (Revision 1 -> 2) sang "incomplete"
            r_ans2 = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers=_auth_headers(user_id, idem_key=f"idem-ans2-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 1,
                    "responded_at": (t_end + timedelta(seconds=60)).isoformat(),
                    "session_outcome": "incomplete",
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert r_ans2.status_code == 200, r_ans2.text

            # Xác minh Step đã được Rollback về in_progress và khôi phục remaining_base_seconds gốc!
            async with sf() as db:
                step_restored = await db.get(StepRow, step1_id)
                assert step_restored is not None
                assert step_restored.status == "in_progress"
                assert step_restored.remaining_base_seconds == 600
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_EDGE_02_invalid_skip_scope_violation(
    sqlite_migration: tuple[str, Any],
) -> None:
    """Gửi skip_scope khi outcome != 'skipped' hoặc outcome == 'skipped' mà thiếu skip_scope -> Bắt buộc ném 422."""
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
            user_id = ids["user_id"]
            run_id = ids["run1_id"]
            t_end = ids["t_sess1_end"]

        headers = _auth_headers(user_id, idem_key=f"idem-open-{uuid4().hex[:6]}")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r_open = await client.post(
                f"/api/v1/runs/{run_id}/check-ins",
                headers=headers,
                json={
                    "kind": "final",
                    "asked_at": t_end.isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_open.status_code == 201, r_open.text
            checkin_id = r_open.json()["data"]["checkin_id"]

            # Case A: outcome='completed' nhưng gửi skip_scope='skip_step' -> 422
            r_invalid_a = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers=_auth_headers(user_id, idem_key=f"idem-inva-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (t_end + timedelta(seconds=10)).isoformat(),
                    "session_outcome": "completed",
                    "step_completed": True,
                    "skip_scope": "skip_step",
                },
            )
            assert r_invalid_a.status_code == 422, r_invalid_a.text

            # Case B: outcome='skipped' nhưng skip_scope=None -> 422
            r_invalid_b = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers=_auth_headers(user_id, idem_key=f"idem-invb-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (t_end + timedelta(seconds=10)).isoformat(),
                    "session_outcome": "skipped",
                    "skip_scope": None,
                },
            )
            assert r_invalid_b.status_code == 422, r_invalid_b.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_EDGE_03_mid_checkin_on_ended_run_block(
    sqlite_migration: tuple[str, Any],
) -> None:
    """Run state là awaiting_checkin -> cố tình tạo check-in giữa phiên (kind='mid') -> ném 409 INVALID_RUN_STATE."""
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
            user_id = ids["user_id"]
            run_id = ids["run1_id"]
            t_end = ids["t_sess1_end"]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r_mid = await client.post(
                f"/api/v1/runs/{run_id}/check-ins",
                headers=_auth_headers(user_id, idem_key=f"idem-mid-{uuid4().hex[:6]}"),
                json={
                    "kind": "mid",
                    "asked_at": t_end.isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_mid.status_code == 409, r_mid.text
            assert r_mid.json()["error"]["code"] == "INVALID_RUN_STATE"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_EDGE_04_final_checkin_on_running_run_block(
    sqlite_migration: tuple[str, Any],
) -> None:
    """Run state là running -> cố tình tạo check-in cuối phiên (kind='final') -> ném 409 INVALID_RUN_STATE."""
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
            ids = await _seed_uf5_uf6_environment(db, run_state="running")
            user_id = ids["user_id"]
            run_id = ids["run1_id"]
            t_start = ids["t_sess1_start"]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r_final = await client.post(
                f"/api/v1/runs/{run_id}/check-ins",
                headers=_auth_headers(user_id, idem_key=f"idem-fin-{uuid4().hex[:6]}"),
                json={
                    "kind": "final",
                    "asked_at": (t_start + timedelta(minutes=10)).isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_final.status_code == 409, r_final.text
            assert r_final.json()["error"]["code"] == "INVALID_RUN_STATE"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF5_EDGE_05_reconcile_timeout_and_late_response(
    sqlite_migration: tuple[str, Any],
) -> None:
    """Check-in chuyển no_respond sau timeout 300s -> Client gửi câu trả lời muộn với revision=0 -> Chấp nhận thành công."""
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
            user_id = ids["user_id"]
            run_id = ids["run1_id"]
            t_end = ids["t_sess1_end"]

        headers = _auth_headers(user_id, idem_key=f"idem-open-{uuid4().hex[:6]}")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Mở final check-in
            r_open = await client.post(
                f"/api/v1/runs/{run_id}/check-ins",
                headers=headers,
                json={
                    "kind": "final",
                    "asked_at": t_end.isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_open.status_code == 201, r_open.text
            checkin_id = r_open.json()["data"]["checkin_id"]

            # Kích hoạt lazy sweeping sau 301 giây -> response_state chuyển no_respond
            async with sf() as db:
                overdue_time = t_end + timedelta(seconds=301)
                swept = await run_service.reconcile_expired_checkins(
                    db, str(user_id), now=overdue_time, commit_if_read=True
                )
                assert swept is True

                ci_check = await db.scalar(select(CheckIn).where(CheckIn.id == UUID(checkin_id)))
                assert ci_check is not None
                assert ci_check.response_state == "no_respond"
                assert ci_check.response_revision == 0

            # Client gửi câu trả lời trễ (Offline recovery) với revision=0
            r_late_ans = await client.put(
                f"/api/v1/check-ins/{checkin_id}/response",
                headers=_auth_headers(user_id, idem_key=f"idem-late-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (t_end + timedelta(seconds=350)).isoformat(),
                    "session_outcome": "completed",
                    "step_completed": True,
                    "task_completed": False,
                },
            )
            assert r_late_ans.status_code == 200, r_late_ans.text
            data_ans = r_late_ans.json()["data"]
            assert data_ans["response_state"] == "answered"
            assert data_ans["session_outcome"] == "completed"
            assert data_ans["response_revision"] == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# 2. UF6 EDGE TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_TC_UF6_EDGE_01_revision_conflict_and_recovery(
    sqlite_migration: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gửi expected_source_revisions cũ (calendar=999) -> Nhận 409 REVISION_CONFLICT -> Retry với revision đúng -> Thành công."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(
        reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now
    )

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(
                db, run_state="closed", run_outcome="incomplete"
            )
            user_id = ids["user_id"]
            task1_id = ids["task1_id"]

        headers = _auth_headers(user_id, idem_key=f"idem-conf1-{uuid4().hex[:6]}")
        payload_bad_rev = {
            "trigger_reason": "checkin_incomplete",
            "target_task_id": str(task1_id),
            "allowed_task_ids": [str(task1_id)],
            "expected_base_schedule_revision": 1,
            "expected_source_revisions": {
                "schedule": 1,
                "task": 1,
                "availability": 1,
                "settings": 1,
                "calendar": 999,  # Cố tình gửi lệch revision lịch
            },
        }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Bước 1: Gửi lệch revision -> Bị từ chối 409 REVISION_CONFLICT
            r_conflict = await client.post(
                "/api/v1/proposals",
                headers=headers,
                json=payload_bad_rev,
            )
            assert r_conflict.status_code == 409, r_conflict.text
            err = r_conflict.json()["error"]
            assert err["code"] == "REVISION_CONFLICT"
            assert "current_revisions" in err

            # Bước 2: Client hồi phục bằng cách dùng current_revisions mới từ server
            actual_revs = err["current_revisions"]
            payload_recovered = {
                "trigger_reason": "checkin_incomplete",
                "target_task_id": str(task1_id),
                "allowed_task_ids": [str(task1_id)],
                "expected_base_schedule_revision": 1,
                "expected_source_revisions": actual_revs,
            }
            r_success = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(user_id, idem_key=f"idem-recov-{uuid4().hex[:6]}"),
                json=payload_recovered,
            )
            assert r_success.status_code == 201, r_success.text
            assert r_success.json()["data"]["status"] == "pending"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_EDGE_02_approve_infeasible_plan_block(
    sqlite_migration: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đề xuất có confirmable=False do thiếu hụt giờ (Deadline quá sát) -> Gọi Approve -> Bắt buộc ném 409 PLAN_NOT_CONFIRMABLE."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(
        reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now
    )

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(
                db, run_state="closed", run_outcome="incomplete"
            )
            user_id = ids["user_id"]
            task1_id = ids["task1_id"]
            t_sess1_start = ids["t_sess1_start"]

            # Cập nhật deadline cực sát: 45 phút sau khi phiên 1 bắt đầu (14:45, không đủ dời session 2)
            task = await db.get(TaskRow, task1_id)
            assert task is not None
            task.time_constraint = {
                "mode": "deadline",
                "at": (t_sess1_start + timedelta(minutes=45)).isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            }
            await db.commit()

            task_rev = task.revision

        headers = _auth_headers(user_id, idem_key=f"idem-prop-inf-{uuid4().hex[:6]}")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Tạo proposal dời lịch
            r_prop = await client.post(
                "/api/v1/proposals",
                headers=headers,
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(task1_id),
                    "allowed_task_ids": [str(task1_id)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": task_rev,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_prop.status_code == 201, r_prop.text
            prop_data = r_prop.json()["data"]
            prop_id = prop_data["proposal_id"]

            # Kiểm tra proposal bị cờ infeasible / confirmable=False
            feas = prop_data["feasibility"]
            assert feas["confirmable"] is False

            # Cố tình gọi Approve đề xuất không khả thi này -> Bắt buộc 409 PLAN_NOT_CONFIRMABLE
            r_approve = await client.post(
                f"/api/v1/proposals/{prop_id}/approve",
                headers=_auth_headers(user_id, idem_key=f"idem-appr-inf-{uuid4().hex[:6]}"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": task_rev,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "start_continuation": False,
                },
            )
            assert r_approve.status_code == 409, r_approve.text
            assert r_approve.json()["error"]["code"] == "PLAN_NOT_CONFIRMABLE"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_EDGE_03_approve_with_active_run_block(
    sqlite_migration: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Có SessionRun khác đang ở trạng thái running -> Gọi Approve -> Bắt buộc ném 409 RUN_IN_PROGRESS."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(
        reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now
    )

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(
                db, run_state="closed", run_outcome="incomplete"
            )
            user_id = ids["user_id"]
            task1_id = ids["task1_id"]
            sess2_id = ids["sess2_id"]

        headers = _auth_headers(user_id, idem_key=f"idem-prop-act-{uuid4().hex[:6]}")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Bước 1: Tạo proposal hợp lệ
            r_prop = await client.post(
                "/api/v1/proposals",
                headers=headers,
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(task1_id),
                    "allowed_task_ids": [str(task1_id)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_prop.status_code == 201, r_prop.text
            prop_id = r_prop.json()["data"]["proposal_id"]

            # Bước 2: Kích hoạt một SessionRun mới ở trạng thái 'running' (giả lập user bật session trên máy khác)
            async with sf() as db:
                active_run = SessionRun(
                    id=uuid4(),
                    user_id=user_id,
                    session_id=sess2_id,
                    run_state="running",
                    revision=1,
                    device_id="edge-test-dev",
                    started_at=datetime.now(UTC),
                    actual_work_seconds=0,
                    actual_source="device_timer",
                    outcome="unknown",
                    settings_snapshot={},
                )
                db.add(active_run)
                await db.commit()

            # Bước 3: Cố tình Approve proposal -> Bị chặn bởi 409 ACTIVE_RUN_EXISTS
            r_approve = await client.post(
                f"/api/v1/proposals/{prop_id}/approve",
                headers=_auth_headers(user_id, idem_key=f"idem-appr-act-{uuid4().hex[:6]}"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "start_continuation": False,
                },
            )
            assert r_approve.status_code == 409, r_approve.text
            assert r_approve.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_EDGE_04_approve_superseded_proposal_block(
    sqlite_migration: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proposal bị superseded bởi đề xuất mới -> Cố tình Approve proposal cũ -> Bắt buộc ném 409 PROPOSAL_CHANGED."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 26, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(
        reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now
    )

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(
                db, run_state="closed", run_outcome="incomplete"
            )
            user_id = ids["user_id"]
            task1_id = ids["task1_id"]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Tạo Proposal 1
            r_p1 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(user_id, idem_key=f"idem-p1-{uuid4().hex[:6]}"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(task1_id),
                    "allowed_task_ids": [str(task1_id)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_p1.status_code == 201, r_p1.text
            prop1_id = r_p1.json()["data"]["proposal_id"]

            # Tạo Proposal 2 -> Proposal 1 tự động bị chuyển sang 'superseded'
            r_p2 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(user_id, idem_key=f"idem-p2-{uuid4().hex[:6]}"),
                json={
                    "trigger_reason": "missed_start",
                    "target_task_id": str(task1_id),
                    "allowed_task_ids": [str(task1_id)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert r_p2.status_code == 201, r_p2.text

            # Cố tình Approve Proposal 1 cũ -> Bị từ chối 409 PROPOSAL_CHANGED
            r_approve_old = await client.post(
                f"/api/v1/proposals/{prop1_id}/approve",
                headers=_auth_headers(user_id, idem_key=f"idem-appr-old-{uuid4().hex[:6]}"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "schedule": 1,
                        "task": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                    "start_continuation": False,
                },
            )
            assert r_approve_old.status_code == 409, r_approve_old.text
            assert r_approve_old.json()["error"]["code"] == "PROPOSAL_CHANGED"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_TC_UF6_EDGE_05_continuation_calendar_collision_block(
    sqlite_migration: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Có sự kiện bận Google Calendar sát phiên -> Bấm continue_now -> Bắt buộc ném 409 LOCKED_CALENDAR_COLLISION."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    fixed_now = datetime(2026, 11, 2, 14, 25, 30, tzinfo=TZ_VN)
    monkeypatch.setattr(
        reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now
    )

    try:
        async with sf() as db:
            ids = await _seed_uf5_uf6_environment(db, run_state="awaiting_checkin")
            user_id = ids["user_id"]
            run_id = ids["run1_id"]
            task1_id = ids["task1_id"]

            # Cập nhật PlanningContext để có một sự kiện bận Google Calendar ngay sát giờ kết thúc phiên 1 (14:27 -> 15:30)
            ctx = await db.scalar(
                select(PlanningContext).where(PlanningContext.user_id == user_id)
            )
            assert ctx is not None
            ctx.event_snapshots = [
                {
                    "event_id": "gcal_busy_event_collision",
                    "title": "Cuộc họp Cơ quan Quan Trọng",
                    "start": "2026-11-02T14:27:00+07:00",
                    "end": "2026-11-02T15:30:00+07:00",
                    "is_locked": True,
                    "is_movable": False,
                }
            ]
            await db.commit()
            calendar_rev = ctx.revision

        headers = _auth_headers(user_id, idem_key=f"idem-cont-coll-{uuid4().hex[:6]}")
        continuation_payload = {
            "trigger_reason": "early_completion_pull_forward",
            "target_task_id": str(task1_id),
            "allowed_task_ids": [str(task1_id)],
            "expected_base_schedule_revision": 1,
            "expected_source_revisions": {
                "schedule": 1,
                "task": 1,
                "availability": 1,
                "settings": 1,
                "calendar": calendar_rev,
            },
            "continuation_request": {
                "mode": "continue_now",
                "source_run_id": str(run_id),
                "expected_run_revision": 1,
                "additional_work_seconds": 1500,  # Xin làm tiếp 25 phút
                "strategy": "extend_then_shift_later_sessions",
                "immediate_continuation": True,
                "borrow_break_seconds": 0,
                "allowed_task_ids": [str(task1_id)],
            },
        }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r_cont = await client.post(
                "/api/v1/proposals",
                headers=headers,
                json=continuation_payload,
            )
            # Bắt buộc phải bị chặn 409 LOCKED_CALENDAR_COLLISION vì đè lên lịch họp cơ quan!
            assert r_cont.status_code == 409, r_cont.text
            assert r_cont.json()["error"]["code"] == "LOCKED_CALENDAR_COLLISION"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_finding4_late_run1_skip_step_does_not_override_subsequent_run2_step_deny(
    sqlite_migration: tuple[str, Any],
) -> None:
    """PR #22 Finding #4: Run 2 báo step_completed=False, sau đó Run 1 đến muộn báo skip_step -> Step KHÔNG bị chuyển skipped và remaining > 0."""
    from app.models.session import Session as SessionRow

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
            user_id = ids["user_id"]
            run1_id = ids["run1_id"]
            step1_id = ids["step1_id"]
            t1_end = ids["t_sess1_end"]

            # Tạo thêm Session 2 & Run 2 (kết thúc muộn hơn Run 1) trên cùng Step 1
            t2_start = t1_end + timedelta(minutes=30)
            t2_end = t2_start + timedelta(minutes=25)
            sess2 = SessionRow(
                id=uuid4(),
                user_id=user_id,
                step_id=step1_id,
                scheduled_start=t2_start,
                scheduled_end=t2_end,
                micro_task_text="Tiếp tục Step 1",
                micro_task_seconds=0,
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=1,
                status="in_progress",
                start_state="started",
                start_decision_due_at=t2_start + timedelta(minutes=15),
            )
            db.add(sess2)
            await db.flush()
            run2 = SessionRun(
                id=uuid4(),
                user_id=user_id,
                session_id=sess2.id,
                device_id="device-test",
                started_at=t2_start,
                ended_at=t2_end,
                run_state="awaiting_checkin",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=1500,
                rest_elapsed_seconds=0,
                actual_work_seconds=1500,
                actual_source="timer",
                outcome="unknown",
                settings_snapshot={"effective_revision": 1},
            )
            db.add(run2)
            await db.commit()
            run2_id = run2.id

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # 1) Mở và trả lời check-in Run 2 TRƯỚC: completed nhưng step_completed=False
            r_open2 = await client.post(
                f"/api/v1/runs/{run2_id}/check-ins",
                headers=_auth_headers(user_id, idem_key=f"f4-open2-{uuid4().hex[:6]}"),
                json={
                    "kind": "final",
                    "asked_at": t2_end.isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_open2.status_code == 201, r_open2.text
            ci2_id = r_open2.json()["data"]["checkin_id"]

            r_ans2 = await client.put(
                f"/api/v1/check-ins/{ci2_id}/response",
                headers=_auth_headers(user_id, idem_key=f"f4-ans2-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (t2_end + timedelta(seconds=10)).isoformat(),
                    "session_outcome": "completed",
                    "step_completed": False,
                    "task_completed": False,
                },
            )
            assert r_ans2.status_code == 200, r_ans2.text

            # 2) Run 1 (kết thúc sớm hơn Run 2) gửi check-in muộn với skip_scope="skip_step"
            r_open1 = await client.post(
                f"/api/v1/runs/{run1_id}/check-ins",
                headers=_auth_headers(user_id, idem_key=f"f4-open1-{uuid4().hex[:6]}"),
                json={
                    "kind": "final",
                    "asked_at": t1_end.isoformat(),
                    "expected_run_revision": 1,
                },
            )
            assert r_open1.status_code == 201, r_open1.text
            ci1_id = r_open1.json()["data"]["checkin_id"]

            r_ans1 = await client.put(
                f"/api/v1/check-ins/{ci1_id}/response",
                headers=_auth_headers(user_id, idem_key=f"f4-ans1-{uuid4().hex[:6]}"),
                json={
                    "expected_response_revision": 0,
                    "responded_at": (t2_end + timedelta(seconds=30)).isoformat(),
                    "session_outcome": "skipped",
                    "step_completed": False,
                    "task_completed": False,
                    "skip_scope": "skip_step",
                },
            )
            assert r_ans1.status_code == 200, r_ans1.text

            async with sf() as db:
                step = await db.get(StepRow, step1_id)
                assert step is not None
                assert step.status != "skipped"
                assert step.remaining_base_seconds > 0
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
