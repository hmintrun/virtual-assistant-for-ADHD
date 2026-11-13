"""Kiểm thử toàn diện W04 (Steps 1, 2, 3, 4): Unit, Integration, Concurrency, Restart Durability & Migration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import (
    Availability,
    CheckIn,
    CommandReceipt,
    PlanningContext,
    Reminder,
    ScheduleVersion,
    Session,
    SessionRun,
    Step,
    Task,
)
from app.schemas.common import SessionSegment
from app.schemas.execution import (
    CheckInData,
    CheckInResponseRequest,
    SessionRunData,
    SessionSnapshot,
    SessionStartRequest,
    SettingsSnapshot,
)
from app.schemas.user import CurrentUser
from app.services import command_service, run_service
from tests.test_models import make_plan


def _seed_w04_plan(db: SyncSession) -> dict[str, object]:
    user, _pref, task, step, session = make_plan(db)
    version = ScheduleVersion(
        user_id=user.id,
        version_number=1,
        base_version=None,
        approved_at=session.scheduled_start - timedelta(days=1),
        affected_task_ids=[str(task.id)],
        min_inter_session_gap_seconds=900,
    )
    reminder = Reminder(
        user_id=user.id,
        session_id=session.id,
        schedule_revision=1,
        kind="pre_session",
        fire_at=session.scheduled_start - timedelta(seconds=300),
        dedupe_key=f"session:{session.id}:version:1:reminder:300",
        delivery_state="scheduled",
    )
    db.add_all([version, reminder])
    db.commit()
    return {
        "user_id": user.id,
        "task_id": task.id,
        "step_id": step.id,
        "session_id": session.id,
        "scheduled_start": session.scheduled_start,
        "scheduled_end": session.scheduled_end,
    }


@pytest.fixture
async def w04_env(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sessions = session_factory(engine)

    try:
        async with sessions() as db:
            info = await db.run_sync(_seed_w04_plan)
        info["url"] = url
        info["alembic"] = alembic
        info["sessions"] = sessions
        info["engine"] = engine
        yield info
    finally:
        await engine.dispose()


@pytest.fixture
async def w04_client(w04_env):
    sessions = w04_env["sessions"]
    user_id = str(w04_env["user_id"])

    async def override_db():
        async with sessions() as db:
            yield db

    async def override_user():
        return CurrentUser(user_id=user_id, email="tn@example.test", display_name="TN")

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


# ==============================================================================
# PHẦN 1: UNIT TESTS (Command Hash, DTO Validation, Clock Skew)
# ==============================================================================


class _LegacyDummyResult(BaseModel):
    draft_id: str
    status: str


@pytest.mark.asyncio
async def test_unit_command_hash_legacy_compat_and_resource_arg_differentiation(w04_env):
    """Kiểm chứng D2 (Unit): command_hash tương thích ngược 100% với receipt UF1/UF2 (draft_id) và phân biệt resource_arg."""
    body = {"expected_summary_revision": 1}
    raw_pre_w04 = json.dumps(
        {"operation": "confirm_draft", "resource": "draft-legacy-001", "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected_sha256 = hashlib.sha256(raw_pre_w04.encode()).hexdigest()
    assert command_service.command_hash("confirm_draft", "draft-legacy-001", body) == expected_sha256

    # Cùng body nhưng khác session_id phải sinh 2 SHA-256 khác nhau
    hash_s1 = command_service.command_hash("start_session_run", "session-1", body)
    hash_s2 = command_service.command_hash("start_session_run", "session-2", body)
    assert hash_s1 != hash_s2

    # Mặc định resource_arg="draft_id" hoạt động giống hệt pre-W04
    @command_service.durable_command("legacy_draft_op", _LegacyDummyResult)
    async def _legacy_op(db, *, user_id: str, draft_id: str, idempotency_key: str, payload: SessionStartRequest):
        return _LegacyDummyResult(draft_id=draft_id, status="confirmed")

    req = SessionStartRequest(
        expected_schedule_revision=1,
        device_id="dev-unit",
        requested_start_at=datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
    )
    user_id = str(w04_env["user_id"])

    async with w04_env["sessions"]() as db:
        r1 = await _legacy_op(
            db,
            user_id=user_id,
            draft_id="draft-A",
            idempotency_key="idem-legacy-unit",
            payload=req,
        )
        assert r1.draft_id == "draft-A"

        # Replay cùng draft-A -> trả về kết quả từ receipt
        r2 = await _legacy_op(
            db,
            user_id=user_id,
            draft_id="draft-A",
            idempotency_key="idem-legacy-unit",
            payload=req,
        )
        assert r2.draft_id == "draft-A"

        # Dùng lại idem-legacy-unit cho draft-B khác -> 409 IDEMPOTENCY_KEY_REUSED
        with pytest.raises(AppError) as exc_info:
            await _legacy_op(
                db,
                user_id=user_id,
                draft_id="draft-B",
                idempotency_key="idem-legacy-unit",
                payload=req,
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_unit_execution_dtos_pydantic_v2_validation():
    """Kiểm chứng DTO (Unit): SessionStartRequest, SessionSnapshot, SettingsSnapshot, CheckInData, SessionRunData, CheckInResponseRequest."""
    now_utc = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)

    # 1. SessionStartRequest: chặn naive datetime, expected_schedule_revision < 1, device_id rỗng, extra fields
    with pytest.raises(ValidationError) as exc_tz:
        SessionStartRequest(
            expected_schedule_revision=1,
            device_id="dev-1",
            requested_start_at=datetime(2026, 10, 30, 9, 0),
        )
    assert "Thời điểm bắt đầu phải có múi giờ" in str(exc_tz.value)

    with pytest.raises(ValidationError):
        SessionStartRequest(
            expected_schedule_revision=0,
            device_id="dev-1",
            requested_start_at=now_utc,
        )

    with pytest.raises(ValidationError):
        SessionStartRequest(
            expected_schedule_revision=1,
            device_id="",
            requested_start_at=now_utc,
        )

    with pytest.raises(ValidationError) as exc_extra:
        SessionStartRequest.model_validate(
            {
                "expected_schedule_revision": 1,
                "device_id": "dev-1",
                "requested_start_at": now_utc.isoformat(),
                "extra_hack": "forbidden",
            }
        )
    assert "extra_forbidden" in str(exc_extra.value)

    # 2. SessionSnapshot: chặn duration_seconds != work_seconds + rest_seconds & naive datetime
    with pytest.raises(ValidationError) as exc_dur:
        SessionSnapshot(
            session_id="sess-1",
            step_id="step-1",
            scheduled_start=now_utc,
            scheduled_end=now_utc + timedelta(seconds=4200),
            micro_task_text="Mở tài liệu",
            work_seconds=3600,
            rest_seconds=600,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            duration_seconds=3600,  # Sai: 3600 != 3600 + 600
            schedule_revision=1,
            start_decision_due_at=now_utc + timedelta(seconds=900),
        )
    assert "Thời lượng phiên không bằng làm chính cộng nghỉ" in str(exc_dur.value)

    valid_snap = SessionSnapshot(
        session_id="sess-1",
        step_id="step-1",
        scheduled_start=now_utc,
        scheduled_end=now_utc + timedelta(seconds=4200),
        micro_task_text="Mở tài liệu",
        micro_task_seconds=120,
        work_seconds=3600,
        rest_seconds=600,
        segments=[
            SessionSegment(order_index=0, kind="work", duration_seconds=3600),
            SessionSegment(order_index=1, kind="rest", duration_seconds=600),
        ],
        duration_seconds=4200,
        schedule_revision=1,
        start_decision_due_at=now_utc + timedelta(seconds=900),
    )
    settings_snap = SettingsSnapshot(effective_revision=1, session=valid_snap)
    checkin_dto = CheckInData(
        checkin_id="ci-1",
        run_id="run-1",
        kind="final",
        asked_at=now_utc + timedelta(seconds=4200),
    )
    run_dto = SessionRunData(
        run_id="run-1",
        session_id="sess-1",
        device_id="dev-1",
        started_at=now_utc,
        settings_snapshot=settings_snap,
        check_in=checkin_dto,
    )
    assert run_dto.run_state == "running"
    assert run_dto.check_in is not None
    assert run_dto.check_in.response_revision == 0

    # 3. CheckInResponseRequest: kiểm tra ràng buộc skip_scope theo session_outcome
    with pytest.raises(ValidationError) as exc_skip_missing:
        CheckInResponseRequest(
            expected_response_revision=0,
            responded_at=now_utc,
            session_outcome="skipped",
            skip_scope=None,
        )
    assert "skip_scope" in str(exc_skip_missing.value)

    with pytest.raises(ValidationError) as exc_skip_forbidden:
        CheckInResponseRequest(
            expected_response_revision=0,
            responded_at=now_utc,
            session_outcome="completed",
            skip_scope="skip_session",
        )
    assert "skip_scope" in str(exc_skip_forbidden.value)


@pytest.mark.asyncio
async def test_unit_clock_skew_validation_422(w04_env):
    """Kiểm chứng Clock Skew (Unit): lệch > 300s trả 422 VALIDATION_ERROR; đúng 300s hợp lệ."""
    session_id = str(w04_env["session_id"])
    user_id = str(w04_env["user_id"])
    start_time = w04_env["scheduled_start"]
    payload = SessionStartRequest(
        expected_schedule_revision=1,
        device_id="device-skew",
        requested_start_at=start_time,
    )

    async with w04_env["sessions"]() as db:
        # 1. now thiếu múi giờ -> 422 VALIDATION_ERROR (field_path="now")
        with pytest.raises(AppError) as exc_naive:
            await run_service.start_session_run(
                db,
                user_id=user_id,
                session_id=session_id,
                idempotency_key=str(uuid4()),
                payload=payload,
                now=datetime(2026, 10, 30, 9, 0),
            )
        assert exc_naive.value.status_code == 422
        assert exc_naive.value.code == "VALIDATION_ERROR"
        assert exc_naive.value.field_errors[0].field_path == "now"

        # 2. Lệch +301s -> 422 VALIDATION_ERROR
        with pytest.raises(AppError) as exc_future:
            await run_service.start_session_run(
                db,
                user_id=user_id,
                session_id=session_id,
                idempotency_key=str(uuid4()),
                payload=payload,
                now=start_time - timedelta(seconds=301),
            )
        assert exc_future.value.status_code == 422
        assert exc_future.value.code == "VALIDATION_ERROR"
        assert exc_future.value.field_errors[0].field_path == "requested_start_at"

        # 3. Lệch -301s -> 422 VALIDATION_ERROR
        with pytest.raises(AppError) as exc_past:
            await run_service.start_session_run(
                db,
                user_id=user_id,
                session_id=session_id,
                idempotency_key=str(uuid4()),
                payload=payload,
                now=start_time + timedelta(seconds=301),
            )
        assert exc_past.value.status_code == 422
        assert exc_past.value.code == "VALIDATION_ERROR"

        # 4. Đúng biên 300s -> Hợp lệ (thành công)
        res_ok = await run_service.start_session_run(
            db,
            user_id=user_id,
            session_id=session_id,
            idempotency_key=str(uuid4()),
            payload=payload,
            now=start_time + timedelta(seconds=300),
        )
        assert res_ok.run_state == "running"


# ==============================================================================
# PHẦN 2: INTEGRATION TESTS (HTTP Endpoints, Feasibility, Concurrency, Restart, Migration)
# ==============================================================================


@pytest.mark.asyncio
async def test_step1_durable_command_resource_arg_and_refs(w04_env, w04_client):
    """Kiểm chứng D2: replay receipt cùng session_id, chặn tái sử dụng Idempotency-Key cho session_id khác, lưu đủ refs và truy vấn qua GET /commands/{id}."""
    session_1_id = str(w04_env["session_id"])
    start_time = w04_env["scheduled_start"]
    async with w04_env["sessions"]() as db:
        session_2 = Session(
            user_id=w04_env["user_id"],
            step_id=w04_env["step_id"],
            scheduled_start=start_time + timedelta(hours=3),
            scheduled_end=start_time + timedelta(hours=3, seconds=4200),
            work_seconds=3600,
            rest_seconds=600,
            duration_seconds=4200,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 3600},
                {"order_index": 1, "kind": "rest", "duration_seconds": 600},
            ],
            schedule_revision=1,
        )
        db.add(session_2)
        await db.commit()
        session_2_id = str(session_2.id)

    payload = {
        "expected_schedule_revision": 1,
        "device_id": "device-alpha",
        "requested_start_at": start_time.isoformat(),
    }
    idem_key = "idem-start-1"

    # Lần 1: Start thành công HTTP 201
    r1 = await w04_client.post(
        f"/api/v1/sessions/{session_1_id}/start",
        headers={"Idempotency-Key": idem_key},
        json=payload,
    )
    assert r1.status_code == 201, r1.text
    data_1 = r1.json()["data"]
    assert data_1["session_id"] == session_1_id
    assert data_1["device_id"] == "device-alpha"
    assert data_1["run_state"] == "running"

    # Lần 2: Replay cùng Idempotency-Key + cùng session_id + cùng payload -> trả lại 201 từ receipt (không bị 409 ACTIVE_RUN_EXISTS)
    r2 = await w04_client.post(
        f"/api/v1/sessions/{session_1_id}/start",
        headers={"Idempotency-Key": idem_key},
        json=payload,
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["data"]["run_id"] == data_1["run_id"]

    # Lần 3: Dùng cùng Idempotency-Key + cùng payload nhưng sang session_2_id khác -> bị chặn 409 IDEMPOTENCY_KEY_REUSED
    r3 = await w04_client.post(
        f"/api/v1/sessions/{session_2_id}/start",
        headers={"Idempotency-Key": idem_key},
        json=payload,
    )
    assert r3.status_code == 409, r3.text
    assert r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # Kiểm tra GET /api/v1/commands/{command_id} trả về đầy đủ run_id, session_id, device_id
    r_cmd = await w04_client.get(f"/api/v1/commands/{idem_key}")
    assert r_cmd.status_code == 200, r_cmd.text
    cmd_data = r_cmd.json()["data"]
    assert cmd_data["command_id"] == idem_key
    assert cmd_data["command_type"] == "start_session_run"
    assert cmd_data["state"] == "applied"
    assert cmd_data["result_reference"]["run_id"] == data_1["run_id"]
    assert cmd_data["result_reference"]["session_id"] == session_1_id
    assert cmd_data["result_reference"]["device_id"] == "device-alpha"

    # Kiểm tra Reminder của session_1 đã bị chuyển sang cancelled
    async with w04_env["sessions"]() as db:
        rem = await db.scalar(
            select(Reminder).where(Reminder.session_id == w04_env["session_id"])
        )
        assert rem is not None
        assert rem.delivery_state == "cancelled"


@pytest.mark.asyncio
async def test_grace_900s_allows_late_start_and_blocks_infeasible_late_start(w04_env, w04_client):
    """Kiểm chứng D3: Grace 900s KHÔNG phải hạn cấm Start; bấm Start muộn khả thi -> 201, nhưng trễ đẩy lấn phiên sau -> 409 START_REQUIRES_REPLAN."""
    session_id = str(w04_env["session_id"])
    start_time = w04_env["scheduled_start"]  # 09:00 -> 10:10 (4200s)

    # Thêm phiên tiếp theo lúc 11:00 -> 12:10
    async with w04_env["sessions"]() as db:
        next_sess = Session(
            user_id=w04_env["user_id"],
            step_id=w04_env["step_id"],
            scheduled_start=start_time + timedelta(hours=2),  # 11:00
            scheduled_end=start_time + timedelta(hours=3, minutes=10),
            work_seconds=3600,
            rest_seconds=600,
            duration_seconds=4200,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 3600},
                {"order_index": 1, "kind": "rest", "duration_seconds": 600},
            ],
            schedule_revision=1,
        )
        db.add(next_sess)
        await db.commit()

    # Đối soát sau 25 phút (> 15p grace) chuyển start_state sang 'not_started'
    late_feasible = start_time + timedelta(minutes=25)  # 09:25 -> kết thúc 10:35 (cách 11:00 là 25p >= 15p)
    async with w04_env["sessions"]() as db:
        await run_service.reconcile_expired_checkins(
            db, str(w04_env["user_id"]), now=late_feasible, commit_if_read=True
        )
        sess = await db.get(Session, w04_env["session_id"])
        assert sess is not None
        assert sess.start_state == "not_started"

    # Nếu bấm Start quá muộn lúc 09:40 -> kết thúc 10:50 (chỉ cách phiên 11:00 có 10p < 15p) -> 409 START_REQUIRES_REPLAN
    late_infeasible = start_time + timedelta(minutes=40)
    r_infeasible = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": late_infeasible.isoformat(),
        },
    )
    assert r_infeasible.status_code == 409
    assert r_infeasible.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # Bấm Start ở phút thứ 25 (09:25 -> 10:35, cách phiên 11:00 đúng 25p >= 15p) -> 201 Created!
    resp = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": late_feasible.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    async with w04_env["sessions"]() as db:
        sess = await db.get(Session, w04_env["session_id"])
        assert sess is not None
        assert sess.status == "in_progress"
        assert sess.start_state == "started"


@pytest.mark.asyncio
async def test_start_error_codes_and_899s_vs_900s_cross_task_gap(w04_env, w04_client):
    """Kiểm chứng D3: tách riêng REVISION_CONFLICT, ACTIVE_RUN_EXISTS, START_REQUIRES_REPLAN và biên 899s vs 900s."""
    session_1_id = str(w04_env["session_id"])
    start_time = w04_env["scheduled_start"]  # 09:00 -> 10:10 (4200s)

    # 1. Lệch expected_schedule_revision -> 409 REVISION_CONFLICT kèm current_revisions={"schedule": 1}
    r_rev = await w04_client.post(
        f"/api/v1/sessions/{session_1_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 99,
            "device_id": "device-1",
            "requested_start_at": start_time.isoformat(),
        },
    )
    assert r_rev.status_code == 409
    err_rev = r_rev.json()["error"]
    assert err_rev["code"] == "REVISION_CONFLICT"
    assert err_rev["current_revisions"] == {"schedule": 1}

    # 2. Tạo Task 2 có Session B lúc 10:30 -> 11:15
    session_b_start = datetime(2026, 10, 30, 10, 30, tzinfo=UTC)
    async with w04_env["sessions"]() as db:
        task_2 = Task(
            user_id=w04_env["user_id"],
            title="Công việc thứ 2",
            time_constraint={"kind": "window"},
        )
        db.add(task_2)
        await db.flush()
        step_2 = Step(
            user_id=w04_env["user_id"],
            task_id=task_2.id,
            title="Bước Task 2",
            order_index=0,
            estimated_base_seconds=1800,
            remaining_base_seconds=1800,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step_2)
        await db.flush()
        session_b = Session(
            user_id=w04_env["user_id"],
            step_id=step_2.id,
            scheduled_start=session_b_start,
            scheduled_end=session_b_start + timedelta(seconds=2700),
            work_seconds=2250,
            rest_seconds=450,
            duration_seconds=2700,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 2250},
                {"order_index": 1, "kind": "rest", "duration_seconds": 450},
            ],
            schedule_revision=1,
        )
        db.add(session_b)
        await db.commit()
        session_b_id = str(session_b.id)

    # 3. Nếu Session 1 (dài 4200s = 70p) xin bắt đầu lúc 09:05:01 -> kết thúc 10:15:01.
    # Khoảng cách tới Session B (10:30:00) chỉ còn 899 giây (< 900s) -> 409 START_REQUIRES_REPLAN!
    start_899 = datetime(2026, 10, 30, 9, 5, 1, tzinfo=UTC)
    r_899 = await w04_client.post(
        f"/api/v1/sessions/{session_1_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": start_899.isoformat(),
        },
    )
    assert r_899.status_code == 409, r_899.text
    assert r_899.json()["error"]["code"] == "START_REQUIRES_REPLAN"
    assert r_899.json()["error"]["next_action"] == "create_proposal"

    # 4. Nếu Session 1 xin bắt đầu lúc 09:05:00 -> kết thúc 10:15:00.
    # Khoảng cách tới Session B (10:30:00) đúng bằng 900 giây -> 201 Created!
    start_900 = datetime(2026, 10, 30, 9, 5, 0, tzinfo=UTC)
    r_900 = await w04_client.post(
        f"/api/v1/sessions/{session_1_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": start_900.isoformat(),
        },
    )
    assert r_900.status_code == 201, r_900.text
    run_1_id = r_900.json()["data"]["run_id"]

    # 5. Khi Run 1 đang 'running', cố start Session B -> 409 ACTIVE_RUN_EXISTS
    r_active = await w04_client.post(
        f"/api/v1/sessions/{session_b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": session_b_start.isoformat(),
        },
    )
    assert r_active.status_code == 409
    assert r_active.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"
    assert r_active.json()["error"]["next_action"] == "open_active_run"

    # 6. Khi Run 1 kết thúc sớm lúc 10:00:00 và chuyển sang 'awaiting_checkin':
    # - Run 1 đã nhả slot live run.
    # - Tính gap cho Session B phải dựa trên ended_at thực tế (10:00:00) thay vì scheduled_end (10:10:00):
    #   + Bấm start Session B lúc 10:14:59 (gap 899s so với ended_at 10:00:00) -> 409 START_REQUIRES_REPLAN
    #   + Bấm start Session B lúc 10:15:00 (gap 900s so với ended_at 10:00:00) -> 201 Created!
    actual_end_1 = datetime(2026, 10, 30, 10, 0, 0, tzinfo=UTC)
    async with w04_env["sessions"]() as db:
        run_row = await db.get(SessionRun, run_service._parse_uuid(run_1_id))
        assert run_row is not None
        run_row.ended_at = actual_end_1
        run_row.run_state = "awaiting_checkin"
        run_row.active_phase = "ended"
        run_row.segment_index = None
        run_row.checkin_timeout_at = actual_end_1 + timedelta(seconds=300)
        db.add(
            CheckIn(
                user_id=w04_env["user_id"],
                run_id=run_row.id,
                kind="final",
                response_state="open",
                asked_at=actual_end_1,
            )
        )
        await db.commit()

    b_899 = datetime(2026, 10, 30, 10, 14, 59, tzinfo=UTC)
    r_b_899 = await w04_client.post(
        f"/api/v1/sessions/{session_b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": b_899.isoformat(),
        },
    )
    assert r_b_899.status_code == 409
    assert r_b_899.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    b_900 = datetime(2026, 10, 30, 10, 15, 0, tzinfo=UTC)
    r_b_900 = await w04_client.post(
        f"/api/v1/sessions/{session_b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": b_900.isoformat(),
        },
    )
    assert r_b_900.status_code == 201, r_b_900.text


@pytest.mark.asyncio
async def test_step_and_session_dependency_guards(w04_env, w04_client):
    """Kiểm chứng D3: Bước trước (order_index < current) chưa xong hoặc phiên trước cùng bước chưa chạy -> 409 START_REQUIRES_REPLAN."""
    user_id = w04_env["user_id"]
    task_id = w04_env["task_id"]
    step_0_id = w04_env["step_id"]
    start_time = w04_env["scheduled_start"]

    async with w04_env["sessions"]() as db:
        step_1 = Step(
            user_id=user_id,
            task_id=task_id,
            title="Bước 2 (order_index=1)",
            order_index=1,
            status="pending",
            estimated_base_seconds=1800,
            remaining_base_seconds=1800,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
        )
        db.add(step_1)
        await db.flush()
        sess_step1_a = Session(
            user_id=user_id,
            step_id=step_1.id,
            scheduled_start=start_time + timedelta(hours=2),
            scheduled_end=start_time + timedelta(hours=3),
            work_seconds=3000,
            rest_seconds=600,
            duration_seconds=3600,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 3000},
                {"order_index": 1, "kind": "rest", "duration_seconds": 600},
            ],
            schedule_revision=1,
        )
        sess_step1_b = Session(
            user_id=user_id,
            step_id=step_1.id,
            scheduled_start=start_time + timedelta(hours=4),
            scheduled_end=start_time + timedelta(hours=5),
            work_seconds=3000,
            rest_seconds=600,
            duration_seconds=3600,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 3000},
                {"order_index": 1, "kind": "rest", "duration_seconds": 600},
            ],
            schedule_revision=1,
        )
        db.add_all([sess_step1_a, sess_step1_b])
        await db.commit()
        s1_a_id = str(sess_step1_a.id)
        s1_b_id = str(sess_step1_b.id)

    # 1. Cố start phiên của Step 1 khi Step 0 chưa hoàn thành -> 409 START_REQUIRES_REPLAN
    r_step = await w04_client.post(
        f"/api/v1/sessions/{s1_a_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": (start_time + timedelta(hours=2)).isoformat(),
        },
    )
    assert r_step.status_code == 409
    assert r_step.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # 2. Đánh dấu Step 0 completed và Session 0 completed, nhưng cố start phiên thứ 2 của Step 1 (s1_b) trước khi chạy s1_a -> 409 START_REQUIRES_REPLAN
    async with w04_env["sessions"]() as db:
        st0 = await db.get(Step, step_0_id)
        assert st0 is not None
        st0.status = "completed"
        sess0 = await db.get(Session, w04_env["session_id"])
        assert sess0 is not None
        sess0.status = "completed"
        await db.commit()

    r_earlier_sess = await w04_client.post(
        f"/api/v1/sessions/{s1_b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": (start_time + timedelta(hours=4)).isoformat(),
        },
    )
    assert r_earlier_sess.status_code == 409
    assert r_earlier_sess.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # 3. Khi s1_a được start đúng thứ tự -> 201 Created!
    r_s1_a_ok = await w04_client.post(
        f"/api/v1/sessions/{s1_a_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": (start_time + timedelta(hours=2)).isoformat(),
        },
    )
    assert r_s1_a_ok.status_code == 201, r_s1_a_ok.text


@pytest.mark.asyncio
async def test_deadline_and_availability_feasibility_guard(w04_env, w04_client):
    """Kiểm chứng D3: vượt deadline hoặc nằm ngoài khung giờ rảnh Task.availability trả 409 START_REQUIRES_REPLAN."""
    session_id = str(w04_env["session_id"])
    start_time = w04_env["scheduled_start"]  # 2026-10-30 09:00 UTC (Thứ Sáu, weekday=5)

    async with w04_env["sessions"]() as db:
        task = await db.get(Task, w04_env["task_id"])
        assert task is not None
        # Đặt deadline lúc 10:00 UTC trong khi phiên dài 70p (09:00 -> 10:10 UTC)
        task.time_constraint = {
            "kind": "deadline",
            "start_at": "2026-10-30T08:00:00+00:00",
            "deadline_at": "2026-10-30T10:00:00+00:00",
            "input_timezone": "UTC",
        }
        await db.commit()

    r_dl = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": start_time.isoformat(),
        },
    )
    assert r_dl.status_code == 409
    assert r_dl.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # Nới deadline lên 12:00 UTC nhưng cấu hình Task.availability chỉ rảnh 09:00 -> 10:00 UTC
    async with w04_env["sessions"]() as db:
        task = await db.get(Task, w04_env["task_id"])
        assert task is not None
        task.time_constraint = {
            "kind": "deadline",
            "start_at": "2026-10-30T08:00:00+00:00",
            "deadline_at": "2026-10-30T12:00:00+00:00",
            "input_timezone": "UTC",
        }
        task.availability = {
            "timezone": "UTC",
            "weekly_rules": [
                {"weekday": 5, "start_local": "09:00:00", "end_local": "10:00:00"}
            ],
            "date_overrides": [],
        }
        await db.commit()

    r_avail = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-1",
            "requested_start_at": start_time.isoformat(),
        },
    )
    assert r_avail.status_code == 409
    assert r_avail.json()["error"]["code"] == "START_REQUIRES_REPLAN"


@pytest.mark.asyncio
async def test_planning_context_busy_intervals_and_own_session_exclusion(w04_env, w04_client):
    """Kiểm chứng D3: trùng busy_intervals / manual_busy_intervals bị chặn 409, nhưng sự kiện của chính session_id được bỏ qua."""
    user_id = w04_env["user_id"]
    session_id = str(w04_env["session_id"])
    start_time = w04_env["scheduled_start"]
    end_time = w04_env["scheduled_end"]

    # 1. Trùng busy_intervals external -> 409 START_REQUIRES_REPLAN
    async with w04_env["sessions"]() as db:
        avail = Availability(
            user_id=user_id,
            timezone="UTC",
            weekly_rules=[],
            date_overrides=[],
            task_limits=[],
            source="manual",
        )
        db.add(avail)
        await db.flush()
        ctx = PlanningContext(
            user_id=user_id,
            availability_id=avail.id,
            availability_revision=1,
            sync_state="valid",
            coverage_state="complete",
            fetched_at=start_time - timedelta(minutes=30),
            query_start=start_time - timedelta(days=1),
            query_end=start_time + timedelta(days=1),
            selected_calendar_ids=[],
            selected_calendar_sources=[],
            calendar_errors=[],
            event_snapshots=[],
            excluded_sources=[],
            busy_intervals=[
                {
                    "start_at": (start_time + timedelta(minutes=10)).isoformat(),
                    "end_at": (start_time + timedelta(minutes=30)).isoformat(),
                    "source": {"event_id": "ext-cal-1"},
                }
            ],
            manual_busy_intervals=[],
        )
        db.add(ctx)
        await db.commit()
        ctx_id = ctx.id

    r_busy = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": start_time.isoformat(),
        },
    )
    assert r_busy.status_code == 409
    assert r_busy.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # 2. Trùng manual_busy_intervals -> 409 START_REQUIRES_REPLAN
    async with w04_env["sessions"]() as db:
        ctx_row = await db.get(PlanningContext, ctx_id)
        assert ctx_row is not None
        ctx_row.busy_intervals = []
        ctx_row.manual_busy_intervals = [
            {
                "start_at": (start_time + timedelta(minutes=5)).isoformat(),
                "end_at": (start_time + timedelta(minutes=20)).isoformat(),
                "title": "Họp đột xuất",
            }
        ]
        await db.commit()

    r_manual = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": start_time.isoformat(),
        },
    )
    assert r_manual.status_code == 409
    assert r_manual.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # 3. Sự kiện trong busy_intervals mang source.session_id == session_id của chính phiên -> được loại trừ và trả 201!
    async with w04_env["sessions"]() as db:
        ctx_row = await db.get(PlanningContext, ctx_id)
        assert ctx_row is not None
        ctx_row.manual_busy_intervals = []
        ctx_row.busy_intervals = [
            {
                "start_at": start_time.isoformat(),
                "end_at": end_time.isoformat(),
                "source": {"session_id": session_id, "event_id": "own-cal-event"},
            }
        ]
        await db.commit()

    r_own = await w04_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev-1",
            "requested_start_at": start_time.isoformat(),
        },
    )
    assert r_own.status_code == 201, r_own.text
    assert r_own.json()["data"]["session_id"] == session_id


@pytest.mark.asyncio
async def test_double_start_concurrency_same_and_different_keys(w04_env, w04_client):
    """Kiểm chứng đồng thời (asyncio.gather): cùng Idempotency-Key trả [201, 201] với 1 dòng DB; khác key trả [201, 409]."""
    session_1_id = str(w04_env["session_id"])
    start_time = w04_env["scheduled_start"]
    payload_1 = {
        "expected_schedule_revision": 1,
        "device_id": "device-concurrent",
        "requested_start_at": start_time.isoformat(),
    }
    same_key = f"idem-concurrent-same-{uuid4()}"

    # Kịch bản A: 2 request song song với CÙNG Idempotency-Key
    r1, r2 = await asyncio.gather(
        w04_client.post(
            f"/api/v1/sessions/{session_1_id}/start",
            headers={"Idempotency-Key": same_key},
            json=payload_1,
        ),
        w04_client.post(
            f"/api/v1/sessions/{session_1_id}/start",
            headers={"Idempotency-Key": same_key},
            json=payload_1,
        ),
    )
    assert [r1.status_code, r2.status_code] == [201, 201]
    assert r1.json()["data"]["run_id"] == r2.json()["data"]["run_id"]
    run_1_id = r1.json()["data"]["run_id"]

    async with w04_env["sessions"]() as db:
        runs = list(
            await db.scalars(
                select(SessionRun).where(SessionRun.session_id == w04_env["session_id"])
            )
        )
        assert len(runs) == 1
        receipts = list(
            await db.scalars(
                select(CommandReceipt).where(CommandReceipt.command_id == same_key)
            )
        )
        assert len(receipts) == 1
        # Đóng Run 1 và tạo Session 2 để test đồng thời với 2 Idempotency-Key khác nhau
        runs[0].ended_at = start_time + timedelta(seconds=3600)
        runs[0].run_state = "closed"
        runs[0].active_phase = "ended"
        sess_1 = await db.get(Session, w04_env["session_id"])
        assert sess_1 is not None
        sess_1.status = "completed"

        s2_start = start_time + timedelta(hours=2)
        sess_2 = Session(
            user_id=w04_env["user_id"],
            step_id=w04_env["step_id"],
            scheduled_start=s2_start,
            scheduled_end=s2_start + timedelta(seconds=3600),
            work_seconds=3000,
            rest_seconds=600,
            duration_seconds=3600,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 3000},
                {"order_index": 1, "kind": "rest", "duration_seconds": 600},
            ],
            schedule_revision=1,
        )
        db.add(sess_2)
        await db.commit()
        session_2_id = str(sess_2.id)

    # Kịch bản B: 2 request song song với 2 Idempotency-Key KHÁC NHAU trên cùng Session 2
    payload_2 = {
        "expected_schedule_revision": 1,
        "device_id": "device-concurrent-2",
        "requested_start_at": s2_start.isoformat(),
    }
    r3, r4 = await asyncio.gather(
        w04_client.post(
            f"/api/v1/sessions/{session_2_id}/start",
            headers={"Idempotency-Key": f"idem-diff-a-{uuid4()}"},
            json=payload_2,
        ),
        w04_client.post(
            f"/api/v1/sessions/{session_2_id}/start",
            headers={"Idempotency-Key": f"idem-diff-b-{uuid4()}"},
            json=payload_2,
        ),
    )
    assert sorted([r3.status_code, r4.status_code]) == [201, 409]
    conflict_resp = r3 if r3.status_code == 409 else r4
    assert conflict_resp.json()["error"]["code"] in (
        "ACTIVE_RUN_EXISTS",
        "START_REQUIRES_REPLAN",
    )
    assert run_1_id != ""


@pytest.mark.asyncio
async def test_engine_restart_reopen_durability_and_lazy_timeout_299s_vs_300s(sqlite_migration):
    """Kiểm chứng độ bền qua Restart Engine (engine.dispose -> make_engine) và biên Lazy Timeout 299s vs 300s."""
    url, alembic = sqlite_migration
    alembic("upgrade", "head")

    engine_1 = make_engine(url)
    sessions_1 = session_factory(engine_1)
    async with sessions_1() as db:
        info = await db.run_sync(_seed_w04_plan)

    user_id = str(info["user_id"])
    session_id = str(info["session_id"])
    start_time = info["scheduled_start"]
    assert isinstance(start_time, datetime)

    async def override_user():
        return CurrentUser(user_id=user_id, email="tn@example.test", display_name="TN")

    async def override_db_1():
        async with sessions_1() as db:
            yield db

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db] = override_db_1

    idem_key = "idem-restart-durable-1"
    payload = {
        "expected_schedule_revision": 1,
        "device_id": "device-restart",
        "requested_start_at": start_time.isoformat(),
    }

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client_1:
            r_start = await client_1.post(
                f"/api/v1/sessions/{session_id}/start",
                headers={"Idempotency-Key": idem_key},
                json=payload,
            )
            assert r_start.status_code == 201, r_start.text
            run_data_1 = r_start.json()["data"]
            run_id = run_data_1["run_id"]

        # Đóng Engine 1 hoàn toàn (mô phỏng tắt tiến trình server)
        await engine_1.dispose()

        # Mở Engine 2 mới trên cùng file SQLite
        engine_2 = make_engine(url)
        sessions_2 = session_factory(engine_2)

        async def override_db_2():
            async with sessions_2() as db:
                yield db

        app.dependency_overrides[get_db] = override_db_2

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client_2:
            # 1. Replay cùng Idempotency-Key sau khi restart -> trả về đúng SessionRunData đã lưu
            r_replay = await client_2.post(
                f"/api/v1/sessions/{session_id}/start",
                headers={"Idempotency-Key": idem_key},
                json=payload,
            )
            assert r_replay.status_code == 201
            assert r_replay.json()["data"] == run_data_1

            # 2. GET /api/v1/commands/{command_id} sống sót qua restart
            r_cmd = await client_2.get(f"/api/v1/commands/{idem_key}")
            assert r_cmd.status_code == 200
            assert r_cmd.json()["data"]["result_reference"]["run_id"] == run_id

            # 3. GET /api/v1/runs/active và GET /api/v1/runs/{run_id} sống sót qua restart
            r_act = await client_2.get("/api/v1/runs/active")
            assert r_act.status_code == 200
            assert r_act.json()["data"]["run_id"] == run_id

            r_det = await client_2.get(f"/api/v1/runs/{run_id}")
            assert r_det.status_code == 200
            assert r_det.json()["data"]["run_id"] == run_id

        # Chuyển Run sang awaiting_checkin với ended_at và checkin_timeout_at = ended_at + 300s
        ended_at = datetime(2026, 10, 30, 10, 10, 0, tzinfo=UTC)
        timeout_at = ended_at + timedelta(seconds=300)
        async with sessions_2() as db:
            run_row = await db.get(SessionRun, UUID(run_id))
            assert run_row is not None
            run_row.ended_at = ended_at
            run_row.run_state = "awaiting_checkin"
            run_row.active_phase = "ended"
            run_row.segment_index = None
            run_row.checkin_timeout_at = timeout_at
            run_row.dnd_release_at = timeout_at
            ci = CheckIn(
                user_id=UUID(user_id),
                run_id=run_row.id,
                kind="final",
                response_state="open",
                session_outcome="unknown",
                asked_at=ended_at,
                response_revision=0,
            )
            db.add(ci)
            await db.commit()
            checkin_id = ci.id

        # Kiểm tra biên 299s (chưa tới hạn 300s): CheckIn vẫn giữ nguyên 'open'
        async with sessions_2() as db:
            changed_299 = await run_service.reconcile_expired_checkins(
                db,
                user_id,
                now=ended_at + timedelta(seconds=299),
                commit_if_read=True,
            )
            assert changed_299 is False
            ci_299 = await db.get(CheckIn, checkin_id)
            assert ci_299 is not None
            assert ci_299.response_state == "open"

        # Kiểm tra đúng mốc 300s qua GET /api/v1/runs/active và GET /api/v1/runs/{run_id}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client_2:
            r_active_after = await client_2.get("/api/v1/runs/active")
            assert r_active_after.status_code == 200
            assert r_active_after.json()["data"] is None

        # Đóng Engine 2 và mở Engine 3 để xác minh trạng thái 'no_respond' đã được commit bền vững xuống đĩa
        await engine_2.dispose()
        engine_3 = make_engine(url)
        sessions_3 = session_factory(engine_3)
        try:
            async with sessions_3() as db:
                ci_300 = await db.get(CheckIn, checkin_id)
                assert ci_300 is not None
                assert ci_300.response_state == "no_respond"
                assert ci_300.response_revision == 0
                assert ci_300.session_outcome == "unknown"
        finally:
            await engine_3.dispose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_migration_20261107_01_to_20261107_02_populated_awaiting_checkin(sqlite_migration):
    """Kiểm chứng D1 (Step 3): Nâng cấp DB SQLite đã có dữ liệu từ 20261107_01 lên 20261107_02."""
    url, alembic = sqlite_migration

    # 1. Nâng cấp tới 20261107_01 (phiên bản trước W04)
    alembic("upgrade", "20261107_01")
    sync_url = url.replace("+aiosqlite", "")
    engine_pre = create_engine(sync_url)

    @event.listens_for(engine_pre, "connect")
    def _fk_pre(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with SyncSession(engine_pre) as db:
        user, _pref, _task, _step, session = make_plan(db)
        run_awaiting = SessionRun(
            user_id=user.id,
            session_id=session.id,
            device_id="dev-pre-w04",
            started_at=session.scheduled_start,
            ended_at=session.scheduled_end,
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=3600,
            actual_work_seconds=3600,
            actual_source="timer_proxy",
            settings_snapshot={"session": {}},
        )
        db.add(run_awaiting)
        db.commit()

        # Tại 20261107_01: run_state='awaiting_checkin' vẫn nằm trong uq_user_live_run nên chèn 'running' bị IntegrityError
        run_blocked = SessionRun(
            user_id=user.id,
            session_id=session.id,
            device_id="dev-blocked",
            started_at=session.scheduled_end + timedelta(minutes=20),
            run_state="running",
            active_phase="work",
            segment_index=0,
            work_elapsed_seconds=0,
            settings_snapshot={"session": {}},
        )
        db.add(run_blocked)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        user_id = user.id
        session_id = session.id
        session_end = session.scheduled_end

    engine_pre.dispose()

    # 2. Chạy Alembic upgrade lên 20261107_02 trên DB đã có sẵn dòng awaiting_checkin
    alembic("upgrade", "20261107_02")

    # 3. Kiểm chứng sau nâng cấp:
    # - Dòng awaiting_checkin giữ nguyên
    # - Cho phép chèn ngay 1 SessionRun mới ở trạng thái 'running'
    # - Vẫn chặn chèn thêm SessionRun thứ 2 ở trạng thái 'running' hoặc 'paused'
    engine_post = create_engine(sync_url)
    try:
        with SyncSession(engine_post) as db:
            existing = db.scalars(
                select(SessionRun).where(SessionRun.user_id == user_id)
            ).all()
            assert len(existing) == 1
            assert existing[0].run_state == "awaiting_checkin"

            run_live = SessionRun(
                user_id=user_id,
                session_id=session_id,
                device_id="dev-post-w04",
                started_at=session_end + timedelta(minutes=20),
                run_state="running",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=0,
                settings_snapshot={"session": {}},
            )
            db.add(run_live)
            db.commit()

            for conflict_state in ("running", "paused"):
                run_conflict = SessionRun(
                    user_id=user_id,
                    session_id=session_id,
                    device_id=f"dev-conflict-{conflict_state}",
                    started_at=session_end + timedelta(minutes=45),
                    run_state=conflict_state,
                    active_phase="work",
                    segment_index=0,
                    work_elapsed_seconds=0,
                    settings_snapshot={"session": {}},
                )
                db.add(run_conflict)
                with pytest.raises(IntegrityError):
                    db.commit()
                db.rollback()
    finally:
        engine_post.dispose()


@pytest.mark.asyncio
async def test_e2e_uf1_uf2_uf3_w04_system_flow(w04_env, w04_client):
    """Kiểm chứng End-to-End toàn trình: UF1 Draft -> Confirm Summary -> UF2 Candidate -> Confirm Official Schedule -> UF3 Start Session -> Active Run -> Run Detail -> Command Receipt."""
    # Xóa phiên/lịch mồi sẵn của fixture để chạy luồng UF1 -> UF2 -> UF3 từ ScheduleVersion v1 mới tinh
    now_ref = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    window_start = now_ref + timedelta(days=1)
    window_end = now_ref + timedelta(days=5)

    # 1. UF1: Tạo bản nháp công việc đủ 4 trường
    r_draft = await w04_client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": "e2e-uf1-create-1"},
        json={
            "client_draft_id": "client-e2e-draft-1",
            "input_source": "text",
            "raw_input": "Hoàn thành báo cáo kiểm thử W04",
            "summary": {
                "title": "Báo cáo kiểm thử W04",
                "description": "Kiểm thử tích hợp và hệ thống cho luồng bắt đầu phiên UF3",
                "goal": "Đạt 100% pass rate",
                "scope": "Bao gồm Unit, Integration và Console System Test, không gồm UI Mobile",
                "time_constraint": {
                    "kind": "deadline",
                    "start_at": window_start.isoformat(),
                    "deadline_at": window_end.isoformat(),
                    "input_timezone": "UTC",
                },
                "availability": {
                    "timezone": "UTC",
                    "weekly_rules": [
                        {
                            "weekday": d,
                            "start_local": "08:00:00",
                            "end_local": "18:00:00",
                        }
                        for d in range(1, 8)
                    ],
                    "date_overrides": [],
                },
            },
        },
    )
    assert r_draft.status_code == 201, r_draft.text
    draft_data = r_draft.json()["data"]
    draft_id = draft_data["draft_id"]
    assert draft_data["summary"]["can_plan"] is True

    # 2. UF1: Xác nhận tóm tắt 1 vòng duy nhất
    r_conf_sum = await w04_client.post(
        f"/api/v1/drafts/{draft_id}/confirm-summary",
        headers={"Idempotency-Key": "e2e-uf1-confirm-sum-1"},
        json={"expected_summary_revision": draft_data["summary_revision"]},
    )
    assert r_conf_sum.status_code == 200, r_conf_sum.text
    sum_res = r_conf_sum.json()["data"]
    assert sum_res["stage"] == "summary_confirmed"

    # 3. UF2: Sinh phương án chia bước và xếp lịch (Candidate)
    r_cand = await w04_client.post(
        f"/api/v1/drafts/{draft_id}/candidates",
        headers={"Idempotency-Key": "e2e-uf2-cand-1"},
        json={
            "source_summary_revision": sum_res["summary_revision"],
            "source_planning_input_revision": sum_res["planning_input_revision"],
            "manual_steps": [
                {
                    "order_index": 0,
                    "title": "Viết bộ kiểm thử tự động W04",
                    "expected_output": "File test_w04_focus_start.py xanh 100%",
                    "estimated_base_seconds": 1500,
                }
            ],
        },
    )
    assert r_cand.status_code == 201, r_cand.text
    cand_data = r_cand.json()["data"]
    candidate_id = cand_data["candidate_id"]
    assert cand_data["feasibility"]["status"] == "feasible"
    assert len(cand_data["sessions"]) >= 1
    first_cand_sess = cand_data["sessions"][0]
    client_sess_key = first_cand_sess["client_session_key"]

    # 4. UF2: Chốt lịch chính thức (Confirm Candidate -> ScheduleVersion)
    r_conf_cand = await w04_client.post(
        f"/api/v1/candidates/{candidate_id}/confirm",
        headers={"Idempotency-Key": "e2e-uf2-confirm-cand-1"},
        json={
            "expected_review_revision": cand_data["review_revision"],
            "expected_summary_revision": cand_data["source_summary_revision"],
            "expected_planning_input_revision": cand_data["source_planning_input_revision"],
            "expected_source_revisions": cand_data["source_revisions"],
        },
    )
    assert r_conf_cand.status_code == 201, r_conf_cand.text
    conf_data = r_conf_cand.json()["data"]
    official_session_id = conf_data["key_mapping"]["sessions"][client_sess_key]
    sched_ver = conf_data["schedule_version"]["version_number"]

    # 5. UF3 (W04): Bắt đầu phiên tập trung chính thức
    r_start = await w04_client.post(
        f"/api/v1/sessions/{official_session_id}/start",
        headers={"Idempotency-Key": "e2e-uf3-start-1"},
        json={
            "expected_schedule_revision": sched_ver,
            "device_id": "device-e2e-pixel",
            "requested_start_at": first_cand_sess["scheduled_start"],
        },
    )
    assert r_start.status_code == 201, r_start.text
    run_data = r_start.json()["data"]
    run_id = run_data["run_id"]
    assert run_data["session_id"] == official_session_id
    assert run_data["run_state"] == "running"
    assert run_data["active_phase"] == "work"

    # 6. UF3 (W04): Truy vấn GET /api/v1/runs/active & GET /api/v1/runs/{run_id} & GET /api/v1/commands/{command_id}
    r_active = await w04_client.get("/api/v1/runs/active")
    assert r_active.status_code == 200
    assert r_active.json()["data"]["run_id"] == run_id

    r_detail = await w04_client.get(f"/api/v1/runs/{run_id}")
    assert r_detail.status_code == 200
    assert r_detail.json()["data"]["settings_snapshot"]["session"]["session_id"] == official_session_id

    r_cmd = await w04_client.get("/api/v1/commands/e2e-uf3-start-1")
    assert r_cmd.status_code == 200
    assert r_cmd.json()["data"]["result_reference"]["run_id"] == run_id
    assert r_cmd.json()["data"]["result_reference"]["session_id"] == official_session_id


@pytest.mark.asyncio
async def test_w04_start_session_earlier_than_15m_grace_requires_replan(w04_client, w04_env):
    too_early = (w04_env["scheduled_start"] - timedelta(seconds=901)).isoformat()
    res = await w04_client.post(
        f"/api/v1/sessions/{w04_env['session_id']}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "device-early-check",
            "requested_start_at": too_early,
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "START_REQUIRES_REPLAN"

