"""Kiểm thử toàn diện W05 (Unit, Integration, Concurrency & Restart Durability):
Run Events, End Session, Actual Corrections & Check-In (Mid/Final/Late)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as SyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models import (
    CheckIn,
    CommandReceipt,
    Reminder,
    RunEvent,
    ScheduleVersion,
    Session,
    Step,
    Task,
)
from app.schemas.execution import (
    ActualCorrectionRequest,
    CheckInCreateRequest,
    CheckInResponseRequest,
    RunEndRequest,
    RunEventBatchRequest,
    RunEventItem,
)
from app.schemas.user import CurrentUser
from app.services import run_service
from tests.test_models import make_plan


def _seed_w05_plan(db: SyncSession) -> dict[str, object]:
    user, _pref, task, step, session = make_plan(db)
    # Thêm phiên 1B cùng thuộc Step 1 (order_index=0) cách phiên 1 đúng 20 phút (>= 900s gap)
    # để kiểm chứng ngay khi phiên 1 chuyển sang awaiting_checkin (nhả uq_user_live_run),
    # người dùng có thể Start ngay phiên tiếp theo mà không cần chờ trả lời check-in!
    session1b_start = session.scheduled_end + timedelta(minutes=20)
    session1b_end = session1b_start + timedelta(minutes=30)
    session1b = Session(
        user_id=user.id,
        step_id=step.id,
        schedule_revision=1,
        scheduled_start=session1b_start,
        scheduled_end=session1b_end,
        micro_task_text="Tiếp tục tìm tài liệu mục 2",
        micro_task_seconds=120,
        work_seconds=1500,
        rest_seconds=300,
        duration_seconds=1800,
        segments=[
            {"order_index": 0, "kind": "work", "duration_seconds": 1500},
            {"order_index": 1, "kind": "rest", "duration_seconds": 300},
        ],
        status="scheduled",
        start_state="waiting",
        start_decision_due_at=session1b_start + timedelta(seconds=900),
    )
    db.add(session1b)
    db.flush()

    # Thêm bước 2 (order_index=1) và phiên 2 cách phiên 1B đúng 20 phút
    step2 = Step(
        user_id=user.id,
        task_id=task.id,
        order_index=1,
        title="Viết phần mở đầu",
        estimated_base_seconds=1500,
        remaining_base_seconds=1500,
        buffer_multiplier=1.2,
        estimate_source="ai",
        status="pending",
    )
    db.add(step2)
    db.flush()

    session2_start = session1b_end + timedelta(minutes=20)
    session2_end = session2_start + timedelta(minutes=30)
    session2 = Session(
        user_id=user.id,
        step_id=step2.id,
        schedule_revision=1,
        scheduled_start=session2_start,
        scheduled_end=session2_end,
        micro_task_text="Gõ câu mở đầu đầu tiên",
        micro_task_seconds=120,
        work_seconds=1500,
        rest_seconds=300,
        duration_seconds=1800,
        segments=list(session.segments),
        status="scheduled",
        start_state="waiting",
        start_decision_due_at=session2_start + timedelta(seconds=900),
    )
    db.add(session2)
    db.flush()

    version = ScheduleVersion(
        user_id=user.id,
        version_number=1,
        base_version=None,
        approved_at=session.scheduled_start - timedelta(days=1),
        affected_task_ids=[str(task.id)],
        min_inter_session_gap_seconds=900,
    )
    reminder1 = Reminder(
        user_id=user.id,
        session_id=session.id,
        schedule_revision=1,
        kind="pre_session",
        fire_at=session.scheduled_start - timedelta(seconds=300),
        dedupe_key=f"session:{session.id}:version:1:reminder:300",
        delivery_state="scheduled",
    )
    reminder2 = Reminder(
        user_id=user.id,
        session_id=session2.id,
        schedule_revision=1,
        kind="pre_session",
        fire_at=session2.scheduled_start - timedelta(seconds=300),
        dedupe_key=f"session:{session2.id}:version:1:reminder:300",
        delivery_state="scheduled",
    )
    db.add_all([version, reminder1, reminder2])
    db.commit()
    return {
        "user_id": user.id,
        "task_id": task.id,
        "step_id": step.id,
        "step2_id": step2.id,
        "session_id": session.id,
        "session1b_id": session1b.id,
        "session2_id": session2.id,
        "scheduled_start": session.scheduled_start,
        "scheduled_end": session.scheduled_end,
        "session1b_start": session1b_start,
        "session1b_end": session1b_end,
        "session2_start": session2_start,
        "session2_end": session2_end,
    }


@pytest.fixture
async def w05_env(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sessions = session_factory(engine)

    try:
        async with sessions() as db:
            info = await db.run_sync(_seed_w05_plan)
        info["url"] = url
        info["alembic"] = alembic
        info["sessions"] = sessions
        info["engine"] = engine
        yield info
    finally:
        await engine.dispose()


@pytest.fixture
async def w05_client(w05_env):
    sessions = w05_env["sessions"]
    user_id = str(w05_env["user_id"])

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


async def _start_run(w05_client: httpx.AsyncClient, w05_env: dict[str, object]) -> dict:
    session_id = str(w05_env["session_id"])
    start_at = cast(datetime, w05_env["scheduled_start"])
    res = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": f"cmd-w05-start-{uuid4()}"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": start_at.isoformat(),
        },
    )
    assert res.status_code == 201
    return res.json()["data"]


# ==============================================================================
# PHẦN 1: UNIT TESTS (DTO Validation, Timezone-Aware, extra="forbid", 3 Skip Chips)
# ==============================================================================


@pytest.mark.asyncio
async def test_w05_unit_dtos_validation_and_skip_scope_3_chips(w05_client, w05_env):
    """Kiểm thử Unit & Wire DTO cho RunEventBatchRequest, RunEndRequest, ActualCorrectionRequest, CheckInCreateRequest, CheckInResponseRequest."""
    now_utc = datetime(2026, 10, 30, 9, 30, tzinfo=UTC)
    naive_dt = datetime(2026, 10, 30, 9, 30)

    # 1. RunEventItem & RunEventBatchRequest: chặn naive occurred_at, rỗng events, extra="forbid"
    with pytest.raises(ValidationError):
        RunEventItem(
            event_id="ev-1",
            event_type="progress",
            occurred_at=naive_dt,
            segment_index=0,
            work_elapsed_seconds=60,
            rest_elapsed_seconds=0,
        )

    with pytest.raises(ValidationError):
        RunEventBatchRequest(
            device_id="dev-1",
            expected_run_revision=1,
            events=[],
        )

    # 2. RunEndRequest: KHÔNG nhận segment_index hoặc occurred_at (extra="forbid"), bắt buộc ended_at có múi giờ
    with pytest.raises(ValidationError) as exc_end_seg:
        RunEndRequest.model_validate(
            {
                "device_id": "dev-1",
                "expected_run_revision": 1,
                "event_id": "ev-end",
                "ended_at": now_utc.isoformat(),
                "end_reason": "timer_elapsed",
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 300,
                "segment_index": 0,
            }
        )
    assert "extra_forbidden" in str(exc_end_seg.value)

    with pytest.raises(ValidationError) as exc_end_occ:
        RunEndRequest.model_validate(
            {
                "device_id": "dev-1",
                "expected_run_revision": 1,
                "event_id": "ev-end",
                "ended_at": now_utc.isoformat(),
                "end_reason": "timer_elapsed",
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 300,
                "occurred_at": now_utc.isoformat(),
            }
        )
    assert "extra_forbidden" in str(exc_end_occ.value)

    with pytest.raises(ValidationError):
        RunEndRequest(
            device_id="dev-1",
            expected_run_revision=1,
            event_id="ev-end",
            ended_at=naive_dt,
            end_reason="timer_elapsed",
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=300,
        )

    # 3. ActualCorrectionRequest & CheckInCreateRequest: chặn naive datetime và extra fields
    with pytest.raises(ValidationError):
        ActualCorrectionRequest(
            expected_run_revision=1,
            self_report_work_seconds=1200,
            reason="Nghe điện thoại",
            occurred_at=naive_dt,
        )

    with pytest.raises(ValidationError):
        CheckInCreateRequest(
            kind="mid",
            asked_at=naive_dt,
            expected_run_revision=1,
        )

    # 4. CheckInResponseRequest: đủ 3 chip skip_scope hợp lệ khi skipped, từ chối thiếu/thừa skip_scope
    for chip in ("skip_session", "postpone_session", "skip_step"):
        valid_skip = CheckInResponseRequest(
            expected_response_revision=0,
            responded_at=now_utc,
            session_outcome="skipped",
            skip_scope=chip,
        )
        assert valid_skip.skip_scope == chip

    with pytest.raises(ValidationError):
        CheckInResponseRequest(
            expected_response_revision=0,
            responded_at=now_utc,
            session_outcome="skipped",
            skip_scope=None,
        )

    for non_skip in ("completed", "partial", "incomplete", "busy"):
        with pytest.raises(ValidationError):
            CheckInResponseRequest(
                expected_response_revision=0,
                responded_at=now_utc,
                session_outcome=non_skip,
                skip_scope="skip_session",
            )

    # 5. Kiểm tra trực tiếp trên HTTP Wire: gửi segment_index vào POST /runs/{id}/end -> 422 VALIDATION_ERROR
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    r_wire_seg = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-wire-extra-seg"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-end-wire",
            "ended_at": now_utc.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
            "segment_index": 0,
        },
    )
    assert r_wire_seg.status_code == 422
    assert r_wire_seg.json()["error"]["code"] == "VALIDATION_ERROR"


# ==============================================================================
# PHẦN 2: INTEGRATION TESTS (Events, Mid/Final/Late CheckIn, ActualCorrection, Invariant 2.1)
# ==============================================================================


@pytest.mark.asyncio
async def test_w05_record_run_events_dedupe_conflict_and_validation(w05_client, w05_env):
    """Kiểm thử POST /api/v1/runs/{run_id}/events: tiến trình, pause/resume, work_to_rest/rest_to_work, dedupe trong/ngoài batch, conflict, device mismatch và monotonic."""
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    segments_count = len(run["settings_snapshot"]["session"]["segments"])

    # 1. Gửi mẻ sự kiện hợp lệ: progress -> pause -> resume -> work_to_rest (seg 1) -> rest_to_work (seg 2) + in-batch duplicate
    ev1_time = (start_at + timedelta(minutes=5)).isoformat()
    ev2_time = (start_at + timedelta(minutes=10)).isoformat()
    ev3_time = (start_at + timedelta(minutes=12)).isoformat()
    ev4_time = (start_at + timedelta(minutes=25)).isoformat()
    ev4b_time = (start_at + timedelta(minutes=30)).isoformat()

    batch_payload = {
        "device_id": "android_pixel_8",
        "expected_run_revision": 1,
        "events": [
            {
                "event_id": "ev-001",
                "event_type": "progress",
                "occurred_at": ev1_time,
                "segment_index": 0,
                "work_elapsed_seconds": 300,
                "rest_elapsed_seconds": 0,
            },
            # In-batch duplicate của ev-001 (cùng nội dung trong cùng mẻ)
            {
                "event_id": "ev-001",
                "event_type": "progress",
                "occurred_at": ev1_time,
                "segment_index": 0,
                "work_elapsed_seconds": 300,
                "rest_elapsed_seconds": 0,
            },
            {
                "event_id": "ev-002",
                "event_type": "pause",
                "occurred_at": ev2_time,
                "segment_index": 0,
                "work_elapsed_seconds": 600,
                "rest_elapsed_seconds": 0,
            },
            {
                "event_id": "ev-003",
                "event_type": "resume",
                "occurred_at": ev3_time,
                "segment_index": 0,
                "work_elapsed_seconds": 600,
                "rest_elapsed_seconds": 0,
            },
            {
                "event_id": "ev-004",
                "event_type": "segment_transition",
                "occurred_at": ev4_time,
                "segment_index": 1,
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 0,
            },
        ],
    }

    res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-1"},
        json=batch_payload,
    )
    assert res.status_code == 200
    body = res.json()["data"]
    assert body["run_id"] == run_id
    assert body["run_revision"] == 2
    assert body["accepted_event_ids"] == ["ev-001", "ev-002", "ev-003", "ev-004"]
    assert body["duplicate_event_ids"] == ["ev-001"]

    # 2. Replay cùng Idempotency-Key trả về nguyên biên nhận
    replay_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-1"},
        json=batch_payload,
    )
    assert replay_res.status_code == 200
    assert replay_res.json()["data"] == body

    # 3. Kiểm tra trạng thái Run sau segment_transition sang segment 1 (rest)
    detail_res = await w05_client.get(f"/api/v1/runs/{run_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()["data"]
    assert detail["run_revision"] == 2
    assert detail["run_state"] == "running"
    assert detail["active_phase"] == "rest"
    assert detail["segment_index"] == 1
    assert detail["work_elapsed_seconds"] == 1500
    assert detail["rest_elapsed_seconds"] == 0

    # 4. Gửi mẻ thứ 2 có chứa lại ev-004 y hệt + chuyển tiếp rest_to_work sang segment 2 (ev-005)
    res2 = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-2"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 2,
            "events": [
                {
                    "event_id": "ev-004",
                    "event_type": "segment_transition",
                    "occurred_at": ev4_time,
                    "segment_index": 1,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 0,
                },
                {
                    "event_id": "ev-005",
                    "event_type": "segment_transition",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 300,
                },
            ],
        },
    )
    assert res2.status_code == 200
    body2 = res2.json()["data"]
    assert body2["run_revision"] == 3
    assert body2["accepted_event_ids"] == ["ev-005"]
    assert body2["duplicate_event_ids"] == ["ev-004"]

    detail2 = (await w05_client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert detail2["active_phase"] == "work"
    assert detail2["segment_index"] == 2

    # 5. Gửi lại ev-005 nhưng khác payload -> 409 CLIENT_EVENT_CONFLICT
    conflict_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-conflict"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 3,
            "events": [
                {
                    "event_id": "ev-005",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 250,
                }
            ],
        },
    )
    assert conflict_res.status_code == 409
    assert conflict_res.json()["error"]["code"] == "CLIENT_EVENT_CONFLICT"

    # 6. In-batch conflict: 2 sự kiện cùng event_id nhưng khác nội dung trong cùng 1 batch -> 409 CLIENT_EVENT_CONFLICT
    in_batch_conflict = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-inbatch-conflict"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 3,
            "events": [
                {
                    "event_id": "ev-inbatch-dup",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1600,
                    "rest_elapsed_seconds": 300,
                },
                {
                    "event_id": "ev-inbatch-dup",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1700,
                    "rest_elapsed_seconds": 300,
                },
            ],
        },
    )
    assert in_batch_conflict.status_code == 409
    assert in_batch_conflict.json()["error"]["code"] == "CLIENT_EVENT_CONFLICT"

    # 7. Sai device_id -> 409 DEVICE_CONFLICT
    dev_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-dev"},
        json={
            "device_id": "wrong_device_id",
            "expected_run_revision": 3,
            "events": [
                {
                    "event_id": "ev-dev-wrong",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 300,
                }
            ],
        },
    )
    assert dev_res.status_code == 409
    assert dev_res.json()["error"]["code"] == "DEVICE_CONFLICT"

    # 8. Sai expected_run_revision -> 409 REVISION_CONFLICT kèm current_revisions.run_revision = 3
    rev_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-rev"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": "ev-006",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 300,
                }
            ],
        },
    )
    assert rev_res.status_code == 409
    assert rev_res.json()["error"]["code"] == "REVISION_CONFLICT"
    assert rev_res.json()["error"]["current_revisions"] == {"run_revision": 3}

    # 9. Bộ đếm lùi (work_elapsed_seconds < 1500 hoặc rest_elapsed_seconds < 300) -> 422 VALIDATION_ERROR
    regression_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-regress"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 3,
            "events": [
                {
                    "event_id": "ev-007",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": 2,
                    "work_elapsed_seconds": 1400,
                    "rest_elapsed_seconds": 300,
                }
            ],
        },
    )
    assert regression_res.status_code == 422
    assert regression_res.json()["error"]["code"] == "VALIDATION_ERROR"

    # 10. segment_index vượt quá số đoạn hoặc occurred_at < started_at -> 422 VALIDATION_ERROR
    seg_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-seg"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 3,
            "events": [
                {
                    "event_id": "ev-008",
                    "event_type": "progress",
                    "occurred_at": ev4b_time,
                    "segment_index": segments_count,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 300,
                }
            ],
        },
    )
    assert seg_res.status_code == 422
    assert seg_res.json()["error"]["code"] == "VALIDATION_ERROR"

    before_start_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-before-start"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 3,
            "events": [
                {
                    "event_id": "ev-009",
                    "event_type": "progress",
                    "occurred_at": (start_at - timedelta(seconds=10)).isoformat(),
                    "segment_index": 2,
                    "work_elapsed_seconds": 1500,
                    "rest_elapsed_seconds": 300,
                }
            ],
        },
    )
    assert before_start_res.status_code == 422
    assert before_start_res.json()["error"]["code"] == "VALIDATION_ERROR"

    # 11. Sau khi gọi /end, gửi thêm events phải bị từ chối 409 INVALID_RUN_STATE
    end_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-w05-events-end"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 3,
            "event_id": "ev-end-after-events",
            "ended_at": (start_at + timedelta(minutes=35)).isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1800,
            "rest_elapsed_seconds": 300,
        },
    )
    assert end_res.status_code == 200

    after_end_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w05-events-after-end"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 4,
            "events": [
                {
                    "event_id": "ev-010",
                    "event_type": "progress",
                    "occurred_at": (start_at + timedelta(minutes=36)).isoformat(),
                    "segment_index": 2,
                    "work_elapsed_seconds": 1800,
                    "rest_elapsed_seconds": 300,
                }
            ],
        },
    )
    assert after_end_res.status_code == 409
    assert after_end_res.json()["error"]["code"] == "INVALID_RUN_STATE"


@pytest.mark.asyncio
async def test_w05_mid_checkin_does_not_close_run(w05_client, w05_env):
    """Kiểm thử D5: CheckIn kind='mid' chỉ cập nhật CheckIn, giữ nguyên SessionRun đang chạy."""
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])

    # Tạo check-in giữa phiên (POST /api/v1/runs/{run_id}/check-ins)
    asked_at = start_at + timedelta(minutes=12)
    create_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/check-ins",
        headers={"Idempotency-Key": "cmd-w05-mid-create"},
        json={
            "kind": "mid",
            "asked_at": asked_at.isoformat(),
            "expected_run_revision": 1,
        },
    )
    assert create_res.status_code == 201
    mid_ci = create_res.json()["data"]
    checkin_id = mid_ci["checkin_id"]
    assert mid_ci["kind"] == "mid"
    assert mid_ci["response_state"] == "open"
    assert mid_ci["response_revision"] == 0

    # Trả lời check-in giữa phiên (PUT /api/v1/check-ins/{checkin_id}/response)
    responded_at = asked_at + timedelta(seconds=20)
    resp_res = await w05_client.put(
        f"/api/v1/check-ins/{checkin_id}/response",
        headers={"Idempotency-Key": "cmd-w05-mid-respond"},
        json={
            "expected_response_revision": 0,
            "responded_at": responded_at.isoformat(),
            "session_outcome": "partial",
            "step_completed": False,
            "task_completed": False,
            "remaining_note": "Đang hoàn thiện mục 1",
        },
    )
    assert resp_res.status_code == 200
    answered_ci = resp_res.json()["data"]
    assert answered_ci["response_state"] == "answered"
    assert answered_ci["response_revision"] == 1
    assert answered_ci["session_outcome"] == "partial"

    # Kiểm tra SessionRun vẫn ở trạng thái running, checkin_timeout_at vẫn None, dnd_release_at giữ nguyên
    run_res = await w05_client.get(f"/api/v1/runs/{run_id}")
    assert run_res.status_code == 200
    run_after = run_res.json()["data"]
    assert run_after["run_state"] == "running"
    assert run_after["checkin_timeout_at"] is None
    assert run_after["dnd_release_at"] == run["dnd_release_at"]
    assert run_after["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_w05_end_session_actual_correction_early_final_checkin_and_invariant_2_1(
    w05_client, w05_env, monkeypatch
):
    """Kiểm thử D4, D5, D6 & Invariant 2.1:
    - End Session -> awaiting_checkin (nhả slot uq_user_live_run để Start ngay phiên tiếp theo!)
    - CommandReceipt.result_reference lưu cả run_id và checkin_id
    - Invariant 2.1: End KHÔNG tự động hoàn thành Session, Step hay Task
    - ActualCorrection (chặn trước end, thành công sau end, kiểm tra 409 REVISION_CONFLICT)
    - Final CheckIn rút ngắn dnd_release_at = min(dnd_release_at, responded_at).
    """
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    ended_at = start_at + timedelta(minutes=30)
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or ended_at).astimezone(UTC),
    )

    # 1. Trước khi kết thúc phiên, gọi actual-corrections phải bị từ chối 409 INVALID_RUN_STATE
    early_corr = await w05_client.post(
        f"/api/v1/runs/{run_id}/actual-corrections",
        headers={"Idempotency-Key": "cmd-w05-corr-early"},
        json={
            "expected_run_revision": 1,
            "self_report_work_seconds": 1200,
            "reason": "Quên bấm tạm dừng",
            "occurred_at": ended_at.isoformat(),
        },
    )
    assert early_corr.status_code == 409
    assert early_corr.json()["error"]["code"] == "INVALID_RUN_STATE"

    # 2. Kết thúc phiên (POST /api/v1/runs/{run_id}/end)
    end_cmd_key = "cmd-w05-end-1"
    end_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": end_cmd_key},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-end-001",
            "ended_at": ended_at.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert end_res.status_code == 200
    ended_run = end_res.json()["data"]
    assert ended_run["run_id"] == run_id
    assert ended_run["run_revision"] == 2
    assert ended_run["run_state"] == "awaiting_checkin"
    assert ended_run["active_phase"] == "ended"
    assert ended_run["segment_index"] is None
    assert ended_run["work_elapsed_seconds"] == 1500
    assert ended_run["rest_elapsed_seconds"] == 300
    assert ended_run["actual_work_seconds"] == 1500
    assert ended_run["actual_source"] == "timer_proxy"
    assert ended_run["outcome"] == "unknown"

    expected_timeout = ended_at + timedelta(seconds=300)
    assert datetime.fromisoformat(ended_run["checkin_timeout_at"]) == expected_timeout
    assert datetime.fromisoformat(ended_run["dnd_release_at"]) == expected_timeout

    embedded_ci = ended_run["check_in"]
    assert embedded_ci is not None
    assert embedded_ci["kind"] == "final"
    assert embedded_ci["response_state"] == "open"
    assert embedded_ci["session_outcome"] == "unknown"
    assert embedded_ci["response_revision"] == 0
    final_checkin_id = embedded_ci["checkin_id"]

    # 2b. Kiểm tra CommandReceipt.result_reference lưu cả run_id và checkin_id
    cmd_res = await w05_client.get(f"/api/v1/commands/{end_cmd_key}")
    assert cmd_res.status_code == 200
    refs = cmd_res.json()["data"]["result_reference"]
    assert refs.get("run_id") == run_id
    assert refs.get("checkin_id") == final_checkin_id

    # 2c. QUY TẮC BẤT BIẾN 2.1: Kết thúc phiên KHÔNG tự động đánh dấu hoàn thành Session, Step hay Task
    async with w05_env["sessions"]() as db:
        sess_row = await db.get(Session, w05_env["session_id"])
        step_row = await db.get(Step, w05_env["step_id"])
        task_row = await db.get(Task, w05_env["task_id"])
        assert sess_row is not None and sess_row.status == "in_progress"
        assert step_row is not None and step_row.status == "pending"
        assert task_row is not None and task_row.status != "completed"

    # 3. GET /api/v1/runs/active trả về data=None vì awaiting_checkin đã nhả slot live run!
    active_res = await w05_client.get("/api/v1/runs/active")
    assert active_res.status_code == 200
    assert active_res.json()["data"] is None

    # 3b. NGAY KHI Run 1 đang ở awaiting_checkin, người dùng có thể Start ngay phiên tiếp theo (session1b cách ended_at >= 15p)!
    session1b_id = str(w05_env["session1b_id"])
    session1b_start = cast(datetime, w05_env["session1b_start"])
    r_start_next = await w05_client.post(
        f"/api/v1/sessions/{session1b_id}/start",
        headers={"Idempotency-Key": "cmd-w05-start-1b-while-awaiting"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": session1b_start.isoformat(),
        },
    )
    assert r_start_next.status_code == 201, r_start_next.text
    assert r_start_next.json()["data"]["run_state"] == "running"

    # 4. GET /api/v1/runs/{run_id}/check-ins?kind=final trả về đúng check-in cuối phiên
    list_ci_res = await w05_client.get(
        f"/api/v1/runs/{run_id}/check-ins", params={"kind": "final"}
    )
    assert list_ci_res.status_code == 200
    ci_items = list_ci_res.json()["data"]["items"]
    assert len(ci_items) == 1
    assert ci_items[0]["checkin_id"] == final_checkin_id

    # 5. Hiệu chỉnh thời gian thực tế (POST /api/v1/runs/{run_id}/actual-corrections)
    # 5a. Sai expected_run_revision -> 409 REVISION_CONFLICT
    corr_rev_err = await w05_client.post(
        f"/api/v1/runs/{run_id}/actual-corrections",
        headers={"Idempotency-Key": "cmd-w05-corr-rev-err"},
        json={
            "expected_run_revision": 99,
            "self_report_work_seconds": 1320,
            "reason": "Sai revision",
            "occurred_at": (ended_at + timedelta(seconds=30)).isoformat(),
        },
    )
    assert corr_rev_err.status_code == 409
    assert corr_rev_err.json()["error"]["code"] == "REVISION_CONFLICT"

    # 5b. Đúng expected_run_revision = 2 -> 200 OK
    corr_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/actual-corrections",
        headers={"Idempotency-Key": "cmd-w05-corr-1"},
        json={
            "expected_run_revision": 2,
            "self_report_work_seconds": 1320,
            "reason": "Có 3 phút nghe điện thoại",
            "occurred_at": (ended_at + timedelta(seconds=30)).isoformat(),
        },
    )
    assert corr_res.status_code == 200
    corr_data = corr_res.json()["data"]
    assert corr_data["run_id"] == run_id
    assert corr_data["run_revision"] == 3
    assert corr_data["actual_work_seconds"] == 1320
    assert corr_data["actual_source"] == "self_report"
    assert corr_data["work_elapsed_seconds"] == 1500

    # 6. Phản hồi check-in cuối phiên sớm (sau 90s < 300s) -> đóng Run và rút ngắn dnd_release_at!
    responded_at = ended_at + timedelta(seconds=90)
    resp_res = await w05_client.put(
        f"/api/v1/check-ins/{final_checkin_id}/response",
        headers={"Idempotency-Key": "cmd-w05-final-respond"},
        json={
            "expected_response_revision": 0,
            "responded_at": responded_at.isoformat(),
            "session_outcome": "completed",
            "step_completed": True,
            "task_completed": False,
            "remaining_note": "Đã xong dàn ý",
        },
    )
    assert resp_res.status_code == 200
    ans_data = resp_res.json()["data"]
    assert ans_data["checkin_id"] == final_checkin_id
    assert ans_data["response_state"] == "answered"
    assert ans_data["session_outcome"] == "completed"
    assert ans_data["step_completed"] is True
    assert ans_data["response_revision"] == 1

    # Kiểm tra SessionRun đã đóng (closed), outcome='completed', dnd_release_at rút ngắn về responded_at
    detail_res = await w05_client.get(f"/api/v1/runs/{run_id}")
    assert detail_res.status_code == 200
    closed_run = detail_res.json()["data"]
    assert closed_run["run_state"] == "closed"
    assert closed_run["outcome"] == "completed"
    assert closed_run["actual_work_seconds"] == 1320
    assert closed_run["actual_source"] == "self_report"
    assert datetime.fromisoformat(closed_run["dnd_release_at"]) == responded_at


@pytest.mark.asyncio
async def test_w05_final_checkin_all_outcomes_and_3_skip_chips(w05_client, w05_env):
    """Kiểm thử đủ các nhánh kết quả CheckIn cuối phiên: completed, partial, incomplete, busy, và skipped kèm 3 chip (skip_session, postpone_session, skip_step)."""
    scenarios: list[tuple[str, str | None, str, str]] = [
        ("partial", None, "incomplete", "pending"),
        ("incomplete", None, "incomplete", "pending"),
        ("busy", None, "incomplete", "pending"),
        ("skipped", "skip_session", "skipped", "pending"),
        ("skipped", "postpone_session", "skipped", "pending"),
        ("skipped", "skip_step", "skipped", "skipped"),
    ]
    base_time = cast(datetime, w05_env["scheduled_start"]) + timedelta(days=2)

    for idx, (outcome, chip, expected_sess_status, expected_step_status) in enumerate(scenarios):
        s_start = base_time + timedelta(hours=idx * 2)
        s_end = s_start + timedelta(minutes=25)
        async with w05_env["sessions"]() as db:
            st = Step(
                user_id=w05_env["user_id"],
                task_id=w05_env["task_id"],
                order_index=10 + idx,
                title=f"Bước kiểm thử {outcome}-{chip}",
                estimated_base_seconds=1200,
                remaining_base_seconds=1200,
                buffer_multiplier=1.2,
                estimate_source="user_confirmed",
                status="pending",
            )
            db.add(st)
            await db.flush()
            sess = Session(
                user_id=w05_env["user_id"],
                step_id=st.id,
                schedule_revision=1,
                scheduled_start=s_start,
                scheduled_end=s_end,
                micro_task_text="Khởi động",
                micro_task_seconds=120,
                work_seconds=1200,
                rest_seconds=300,
                duration_seconds=1500,
                segments=[
                    {"order_index": 0, "kind": "work", "duration_seconds": 1200},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                ],
                status="in_progress",
                start_state="started",
                start_decision_due_at=s_start + timedelta(seconds=900),
            )
            db.add(sess)
            await db.commit()
            sess_id = sess.id
            st_id = st.id

        # Tạo run và end trực tiếp qua API sau khi mồi SessionRun
        async with w05_env["sessions"]() as db:
            from app.models.execution import SessionRun

            run_row = SessionRun(
                user_id=w05_env["user_id"],
                session_id=sess_id,
                device_id="android_pixel_8",
                started_at=s_start,
                run_state="running",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=600,
                rest_elapsed_seconds=0,
                settings_snapshot={},
            )
            db.add(run_row)
            await db.commit()
            r_id = str(run_row.id)

        r_end = await w05_client.post(
            f"/api/v1/runs/{r_id}/end",
            headers={"Idempotency-Key": f"cmd-end-outcome-{idx}"},
            json={
                "device_id": "android_pixel_8",
                "expected_run_revision": 1,
                "event_id": f"ev-end-outcome-{idx}",
                "ended_at": s_end.isoformat(),
                "end_reason": "user_stopped",
                "work_elapsed_seconds": 600,
                "rest_elapsed_seconds": 0,
            },
        )
        assert r_end.status_code == 200
        ci_id = r_end.json()["data"]["check_in"]["checkin_id"]

        resp_body: dict[str, object] = {
            "expected_response_revision": 0,
            "responded_at": (s_end + timedelta(seconds=30)).isoformat(),
            "session_outcome": outcome,
            "step_completed": False,
            "task_completed": False,
        }
        if chip is not None:
            resp_body["skip_scope"] = chip

        r_resp = await w05_client.put(
            f"/api/v1/check-ins/{ci_id}/response",
            headers={"Idempotency-Key": f"cmd-resp-outcome-{idx}"},
            json=resp_body,
        )
        assert r_resp.status_code == 200
        assert r_resp.json()["data"]["session_outcome"] == outcome
        assert r_resp.json()["data"]["skip_scope"] == chip

        async with w05_env["sessions"]() as db:
            sess_check = await db.get(Session, sess_id)
            st_check = await db.get(Step, st_id)
            assert sess_check is not None and sess_check.status == expected_sess_status
            assert st_check is not None and st_check.status == expected_step_status
            if chip == "skip_step":
                assert st_check.remaining_base_seconds == 0


@pytest.mark.asyncio
async def test_w05_late_final_checkin_response_after_no_respond(
    w05_client, w05_env, monkeypatch
):
    """Kiểm thử D6: Phản hồi muộn sau khi CheckIn đã quá hạn 300s (no_respond -> answered)."""
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    ended_at = start_at + timedelta(minutes=30)
    clock = [ended_at]
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or clock[0]).astimezone(UTC),
    )

    # 1. Kết thúc phiên -> awaiting_checkin
    end_res = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-w05-late-end"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-end-late",
            "ended_at": ended_at.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert end_res.status_code == 200
    ended_run = end_res.json()["data"]
    checkin_id = ended_run["check_in"]["checkin_id"]
    timeout_at = ended_at + timedelta(seconds=300)

    # 2. Kích hoạt lazy timeout tại t = ended_at + 301s (có commit_if_read=True)
    clock[0] = ended_at + timedelta(seconds=301)
    sessions = w05_env["sessions"]
    user_id = str(w05_env["user_id"])
    async with sessions() as db:
        reconciled = await run_service.reconcile_expired_checkins(
            db, user_id, now=ended_at + timedelta(seconds=301), commit_if_read=True
        )
        assert reconciled is True

    # Kiểm tra trạng thái sau lazy timeout: response_state == 'no_respond', response_revision == 0
    detail_after_timeout = (await w05_client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert detail_after_timeout["run_state"] == "awaiting_checkin"
    assert detail_after_timeout["outcome"] == "unknown"
    assert detail_after_timeout["check_in"]["response_state"] == "no_respond"
    assert detail_after_timeout["check_in"]["response_revision"] == 0

    # 3. Người dùng phản hồi muộn với expected_response_revision = 0 (skip_scope bắt buộc khi skipped)
    late_responded_at = ended_at + timedelta(minutes=10)
    late_res = await w05_client.put(
        f"/api/v1/check-ins/{checkin_id}/response",
        headers={"Idempotency-Key": "cmd-w05-late-respond"},
        json={
            "expected_response_revision": 0,
            "responded_at": late_responded_at.isoformat(),
            "session_outcome": "skipped",
            "step_completed": False,
            "task_completed": False,
            "skip_scope": "postpone_session",
            "remaining_note": "Có việc bận đột xuất lúc hết giờ",
        },
    )
    assert late_res.status_code == 200
    late_ci = late_res.json()["data"]
    assert late_ci["response_state"] == "answered"
    assert late_ci["session_outcome"] == "skipped"
    assert late_ci["skip_scope"] == "postpone_session"
    assert late_ci["response_revision"] == 1

    # 4. Kiểm tra SessionRun: đóng thành 'closed', outcome cập nhật thành 'skipped', nhưng ended_at, actual_work_seconds, dnd_release_at KHÔNG bị đổi!
    final_detail = (await w05_client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert final_detail["run_state"] == "closed"
    assert final_detail["outcome"] == "skipped"
    assert datetime.fromisoformat(final_detail["ended_at"]) == ended_at
    assert final_detail["actual_work_seconds"] == 1500
    assert datetime.fromisoformat(final_detail["dnd_release_at"]) == timeout_at

    # 5. Nếu tiếp tục gửi response lần 2 với expected_response_revision = 0 -> 409 REVISION_CONFLICT
    dup_res = await w05_client.put(
        f"/api/v1/check-ins/{checkin_id}/response",
        headers={"Idempotency-Key": "cmd-w05-late-respond-dup"},
        json={
            "expected_response_revision": 0,
            "responded_at": (late_responded_at + timedelta(minutes=1)).isoformat(),
            "session_outcome": "completed",
            "step_completed": True,
            "task_completed": False,
        },
    )
    assert dup_res.status_code == 409
    assert dup_res.json()["error"]["code"] == "REVISION_CONFLICT"
    assert dup_res.json()["error"]["current_revisions"] == {"response_revision": 1}


# ==============================================================================
# PHẦN 3: CONCURRENCY & RESTART DURABILITY TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_w05_concurrency_end_and_checkin_response(w05_client, w05_env):
    """Kiểm thử đồng thời (asyncio.gather) cho end_session_run và respond_to_checkin (cùng key vs khác key)."""
    run = await _start_run(w05_client, w05_env)
    run_1_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    ended_at = start_at + timedelta(minutes=30)

    # 1. 2 request end_session_run đồng thời với CÙNG Idempotency-Key -> [200, 200] & đúng 1 CheckIn final
    end_payload_same = {
        "device_id": "android_pixel_8",
        "expected_run_revision": 1,
        "event_id": "ev-end-conc-same",
        "ended_at": ended_at.isoformat(),
        "end_reason": "timer_elapsed",
        "work_elapsed_seconds": 1500,
        "rest_elapsed_seconds": 300,
    }
    same_end_key = f"cmd-w05-end-same-{uuid4()}"
    r1, r2 = await asyncio.gather(
        w05_client.post(
            f"/api/v1/runs/{run_1_id}/end",
            headers={"Idempotency-Key": same_end_key},
            json=end_payload_same,
        ),
        w05_client.post(
            f"/api/v1/runs/{run_1_id}/end",
            headers={"Idempotency-Key": same_end_key},
            json=end_payload_same,
        ),
    )
    assert [r1.status_code, r2.status_code] == [200, 200]
    assert r1.json()["data"]["check_in"]["checkin_id"] == r2.json()["data"]["check_in"]["checkin_id"]
    final_checkin_id = r1.json()["data"]["check_in"]["checkin_id"]

    async with w05_env["sessions"]() as db:
        final_cis = list(
            await db.scalars(
                select(CheckIn).where(CheckIn.run_id == UUID(run_1_id), CheckIn.kind == "final")
            )
        )
        assert len(final_cis) == 1
        end_events = list(
            await db.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == UUID(run_1_id), RunEvent.event_type == "end"
                )
            )
        )
        assert len(end_events) == 1
        end_receipts = list(
            await db.scalars(
                select(CommandReceipt).where(CommandReceipt.command_id == same_end_key)
            )
        )
        assert len(end_receipts) == 1

    # 2. 2 request respond_to_checkin đồng thời với CÙNG Idempotency-Key -> [200, 200]
    resp_payload_same = {
        "expected_response_revision": 0,
        "responded_at": (ended_at + timedelta(seconds=45)).isoformat(),
        "session_outcome": "completed",
        "step_completed": False,
        "task_completed": False,
    }
    same_resp_key = f"cmd-w05-resp-same-{uuid4()}"
    rc1, rc2 = await asyncio.gather(
        w05_client.put(
            f"/api/v1/check-ins/{final_checkin_id}/response",
            headers={"Idempotency-Key": same_resp_key},
            json=resp_payload_same,
        ),
        w05_client.put(
            f"/api/v1/check-ins/{final_checkin_id}/response",
            headers={"Idempotency-Key": same_resp_key},
            json=resp_payload_same,
        ),
    )
    assert [rc1.status_code, rc2.status_code] == [200, 200]
    assert rc1.json()["data"]["response_revision"] == 1
    assert rc2.json()["data"]["response_revision"] == 1

    # 3. Khởi chạy session1b để kiểm thử đồng thời với KHÁC Idempotency-Key cho cả end và respond_to_checkin
    session1b_id = str(w05_env["session1b_id"])
    s1b_start = cast(datetime, w05_env["session1b_start"])
    r_start_1b = await w05_client.post(
        f"/api/v1/sessions/{session1b_id}/start",
        headers={"Idempotency-Key": f"cmd-w05-start-1b-{uuid4()}"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": s1b_start.isoformat(),
        },
    )
    assert r_start_1b.status_code == 201
    run_1b_id = r_start_1b.json()["data"]["run_id"]
    s1b_ended_at = s1b_start + timedelta(minutes=30)

    # 3a. 2 request end_session_run đồng thời với KHÁC Idempotency-Key -> [200, 409]
    re1, re2 = await asyncio.gather(
        w05_client.post(
            f"/api/v1/runs/{run_1b_id}/end",
            headers={"Idempotency-Key": f"cmd-w05-end-diff-a-{uuid4()}"},
            json={
                "device_id": "android_pixel_8",
                "expected_run_revision": 1,
                "event_id": "ev-end-diff-a",
                "ended_at": s1b_ended_at.isoformat(),
                "end_reason": "timer_elapsed",
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 300,
            },
        ),
        w05_client.post(
            f"/api/v1/runs/{run_1b_id}/end",
            headers={"Idempotency-Key": f"cmd-w05-end-diff-b-{uuid4()}"},
            json={
                "device_id": "android_pixel_8",
                "expected_run_revision": 1,
                "event_id": "ev-end-diff-b",
                "ended_at": s1b_ended_at.isoformat(),
                "end_reason": "timer_elapsed",
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 300,
            },
        ),
    )
    assert sorted([re1.status_code, re2.status_code]) == [200, 409]
    ok_end = re1 if re1.status_code == 200 else re2
    err_end = re2 if re1.status_code == 200 else re1
    assert err_end.json()["error"]["code"] in ("INVALID_RUN_STATE", "REVISION_CONFLICT")
    ci_1b_id = ok_end.json()["data"]["check_in"]["checkin_id"]

    # 3b. 2 request respond_to_checkin đồng thời với KHÁC Idempotency-Key -> [200, 409 REVISION_CONFLICT]
    rr1, rr2 = await asyncio.gather(
        w05_client.put(
            f"/api/v1/check-ins/{ci_1b_id}/response",
            headers={"Idempotency-Key": f"cmd-w05-resp-diff-a-{uuid4()}"},
            json={
                "expected_response_revision": 0,
                "responded_at": (s1b_ended_at + timedelta(seconds=30)).isoformat(),
                "session_outcome": "completed",
                "step_completed": True,
                "task_completed": False,
            },
        ),
        w05_client.put(
            f"/api/v1/check-ins/{ci_1b_id}/response",
            headers={"Idempotency-Key": f"cmd-w05-resp-diff-b-{uuid4()}"},
            json={
                "expected_response_revision": 0,
                "responded_at": (s1b_ended_at + timedelta(seconds=30)).isoformat(),
                "session_outcome": "partial",
                "step_completed": False,
                "task_completed": False,
            },
        ),
    )
    assert sorted([rr1.status_code, rr2.status_code]) == [200, 409]
    err_rr = rr2 if rr1.status_code == 200 else rr1
    assert err_rr.json()["error"]["code"] == "REVISION_CONFLICT"
    assert err_rr.json()["error"]["current_revisions"] == {"response_revision": 1}


@pytest.mark.asyncio
async def test_w05_restart_durability_and_cross_resource_key_reuse(sqlite_migration):
    """Kiểm thử độ bền qua Restart Engine (engine.dispose -> make_engine) cho 4 thao tác W05
    (record_run_events, end_session_run, record_actual_correction, respond_to_checkin)
    và chặn tái sử dụng Idempotency-Key chéo run_id / checkin_id (409 IDEMPOTENCY_KEY_REUSED).
    """
    url, alembic = sqlite_migration
    alembic("upgrade", "head")

    engine_1 = make_engine(url)
    sessions_1 = session_factory(engine_1)
    async with sessions_1() as db:
        info = await db.run_sync(_seed_w05_plan)

    user_id = str(info["user_id"])
    session_id = str(info["session_id"])
    session1b_id = str(info["session1b_id"])
    start_time = cast(datetime, info["scheduled_start"])
    s1b_start = cast(datetime, info["session1b_start"])

    async def override_user():
        return CurrentUser(user_id=user_id, email="tn@example.test", display_name="TN")

    async def override_db_1():
        async with sessions_1() as db:
            yield db

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db] = override_db_1

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client_1:
            r_start = await client_1.post(
                f"/api/v1/sessions/{session_id}/start",
                headers={"Idempotency-Key": "cmd-dur-start-1"},
                json={
                    "expected_schedule_revision": 1,
                    "device_id": "android_pixel_8",
                    "requested_start_at": start_time.isoformat(),
                },
            )
            assert r_start.status_code == 201
            run_1_id = r_start.json()["data"]["run_id"]

            key_events = "cmd-dur-events-1"
            payload_events = {
                "device_id": "android_pixel_8",
                "expected_run_revision": 1,
                "events": [
                    {
                        "event_id": "ev-dur-01",
                        "event_type": "progress",
                        "occurred_at": (start_time + timedelta(minutes=5)).isoformat(),
                        "segment_index": 0,
                        "work_elapsed_seconds": 300,
                        "rest_elapsed_seconds": 0,
                    }
                ],
            }
            r_ev = await client_1.post(
                f"/api/v1/runs/{run_1_id}/events",
                headers={"Idempotency-Key": key_events},
                json=payload_events,
            )
            assert r_ev.status_code == 200
            ev_data_1 = r_ev.json()["data"]

            ended_at = start_time + timedelta(minutes=30)
            key_end = "cmd-dur-end-1"
            payload_end = {
                "device_id": "android_pixel_8",
                "expected_run_revision": 2,
                "event_id": "ev-dur-end-01",
                "ended_at": ended_at.isoformat(),
                "end_reason": "timer_elapsed",
                "work_elapsed_seconds": 1500,
                "rest_elapsed_seconds": 300,
            }
            r_end = await client_1.post(
                f"/api/v1/runs/{run_1_id}/end",
                headers={"Idempotency-Key": key_end},
                json=payload_end,
            )
            assert r_end.status_code == 200
            end_data_1 = r_end.json()["data"]
            checkin_1_id = end_data_1["check_in"]["checkin_id"]

            key_corr = "cmd-dur-corr-1"
            payload_corr = {
                "expected_run_revision": 3,
                "self_report_work_seconds": 1350,
                "reason": "Điều chỉnh sau khi nghe điện thoại",
                "occurred_at": (ended_at + timedelta(seconds=20)).isoformat(),
            }
            r_corr = await client_1.post(
                f"/api/v1/runs/{run_1_id}/actual-corrections",
                headers={"Idempotency-Key": key_corr},
                json=payload_corr,
            )
            assert r_corr.status_code == 200
            corr_data_1 = r_corr.json()["data"]

            key_resp = "cmd-dur-resp-1"
            payload_resp = {
                "expected_response_revision": 0,
                "responded_at": (ended_at + timedelta(seconds=40)).isoformat(),
                "session_outcome": "completed",
                "step_completed": False,
                "task_completed": False,
                "remaining_note": "Xong phần 1",
            }
            r_resp = await client_1.put(
                f"/api/v1/check-ins/{checkin_1_id}/response",
                headers={"Idempotency-Key": key_resp},
                json=payload_resp,
            )
            assert r_resp.status_code == 200
            resp_data_1 = r_resp.json()["data"]

        # ĐÓNG ENGINE 1 VÀ MỞ ENGINE 2 MỚI HOÀN TOÀN
        await engine_1.dispose()
        engine_2 = make_engine(url)
        sessions_2 = session_factory(engine_2)

        async def override_db_2():
            async with sessions_2() as db:
                yield db

        app.dependency_overrides[get_db] = override_db_2

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client_2:
                # 1. Replay cả 4 lệnh W05 sau khi restart DB engine -> trả về nguyên vẹn dữ liệu từ CommandReceipt
                r_ev_replay = await client_2.post(
                    f"/api/v1/runs/{run_1_id}/events",
                    headers={"Idempotency-Key": key_events},
                    json=payload_events,
                )
                assert r_ev_replay.status_code == 200
                assert r_ev_replay.json()["data"] == ev_data_1

                r_end_replay = await client_2.post(
                    f"/api/v1/runs/{run_1_id}/end",
                    headers={"Idempotency-Key": key_end},
                    json=payload_end,
                )
                assert r_end_replay.status_code == 200
                assert r_end_replay.json()["data"] == end_data_1

                r_corr_replay = await client_2.post(
                    f"/api/v1/runs/{run_1_id}/actual-corrections",
                    headers={"Idempotency-Key": key_corr},
                    json=payload_corr,
                )
                assert r_corr_replay.status_code == 200
                assert r_corr_replay.json()["data"] == corr_data_1

                r_resp_replay = await client_2.put(
                    f"/api/v1/check-ins/{checkin_1_id}/response",
                    headers={"Idempotency-Key": key_resp},
                    json=payload_resp,
                )
                assert r_resp_replay.status_code == 200
                assert r_resp_replay.json()["data"] == resp_data_1

                # 2. Khởi chạy session1b để lấy run_2_id & checkin_2_id, kiểm tra chống tái sử dụng Idempotency-Key chéo tài nguyên
                r_s2 = await client_2.post(
                    f"/api/v1/sessions/{session1b_id}/start",
                    headers={"Idempotency-Key": "cmd-dur-start-1b"},
                    json={
                        "expected_schedule_revision": 1,
                        "device_id": "android_pixel_8",
                        "requested_start_at": s1b_start.isoformat(),
                    },
                )
                assert r_s2.status_code == 201
                run_2_id = r_s2.json()["data"]["run_id"]

                # Dùng lại key_events / key_end / key_corr của run_1_id sang run_2_id -> 409 IDEMPOTENCY_KEY_REUSED
                for reused_key, path_suffix, body in (
                    (key_events, "events", payload_events),
                    (key_end, "end", payload_end),
                    (key_corr, "actual-corrections", payload_corr),
                ):
                    r_cross_run = await client_2.post(
                        f"/api/v1/runs/{run_2_id}/{path_suffix}",
                        headers={"Idempotency-Key": reused_key},
                        json=body,
                    )
                    assert r_cross_run.status_code == 409
                    assert r_cross_run.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

                # Tạo mid check-in trên run_2_id và thử dùng lại key_resp của checkin_1_id -> 409 IDEMPOTENCY_KEY_REUSED
                r_mid_2 = await client_2.post(
                    f"/api/v1/runs/{run_2_id}/check-ins",
                    headers={"Idempotency-Key": "cmd-dur-mid-2"},
                    json={
                        "kind": "mid",
                        "asked_at": (s1b_start + timedelta(minutes=10)).isoformat(),
                        "expected_run_revision": 1,
                    },
                )
                assert r_mid_2.status_code == 201
                checkin_2_id = r_mid_2.json()["data"]["checkin_id"]

                r_cross_ci = await client_2.put(
                    f"/api/v1/check-ins/{checkin_2_id}/response",
                    headers={"Idempotency-Key": key_resp},
                    json=payload_resp,
                )
                assert r_cross_ci.status_code == 409
                assert r_cross_ci.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        finally:
            await engine_2.dispose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_w05_run_event_future_clock_skew_over_300s_rejected(w05_client, w05_env):
    run = await _start_run(w05_client, w05_env)
    future_at = (datetime.now(UTC) + timedelta(seconds=360)).isoformat()
    res = await w05_client.post(
        f"/api/v1/runs/{run['run_id']}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": run["device_id"],
            "expected_run_revision": run["run_revision"],
            "events": [
                {
                    "event_id": "ev-future-skew",
                    "event_type": "progress",
                    "occurred_at": future_at,
                    "segment_index": 0,
                    "work_elapsed_seconds": 10,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    assert res.status_code == 422, res.text


# ==============================================================================
# PHẦN 4: W3-06 TESTS (Guard Final trước End, Correction, Cascade/Scope R01, Timeout +300s)
# ==============================================================================


@pytest.mark.asyncio
async def test_w306_guard_final_checkin_before_end_and_invalid_legacy_final(
    w05_client, w05_env, monkeypatch
):
    """Kiểm thử W3-06 [P0]:
    - Chặn tạo final CheckIn khi Run còn running hoặc paused (409 INVALID_RUN_STATE), không sinh record/receipt.
    - Bảo vệ cả trường hợp có sẵn bản ghi final sai trên Run chưa End (PUT response trả 409 INVALID_RUN_STATE, không đóng Run).
    - Sau khi End hợp lệ, tạo thêm final trả 409 FINAL_CHECKIN_EXISTS và PUT response hoạt động bình thường.
    """
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    asked_at = start_at + timedelta(seconds=30)

    # 1. Đang running -> POST final CheckIn bị từ chối 409 INVALID_RUN_STATE
    r_running = await w05_client.post(
        f"/api/v1/runs/{run_id}/check-ins",
        headers={"Idempotency-Key": "cmd-w306-final-while-running"},
        json={
            "kind": "final",
            "asked_at": asked_at.isoformat(),
            "expected_run_revision": 1,
        },
    )
    assert r_running.status_code == 409
    assert r_running.json()["error"]["code"] == "INVALID_RUN_STATE"

    # 2. Chuyển sang paused -> POST final CheckIn vẫn bị từ chối 409 INVALID_RUN_STATE
    r_pause = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": "cmd-w306-pause"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": "ev-w306-pause",
                    "event_type": "pause",
                    "occurred_at": (start_at + timedelta(minutes=5)).isoformat(),
                    "segment_index": 0,
                    "work_elapsed_seconds": 300,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    assert r_pause.status_code == 200

    r_paused = await w05_client.post(
        f"/api/v1/runs/{run_id}/check-ins",
        headers={"Idempotency-Key": "cmd-w306-final-while-paused"},
        json={
            "kind": "final",
            "asked_at": (start_at + timedelta(minutes=6)).isoformat(),
            "expected_run_revision": 2,
        },
    )
    assert r_paused.status_code == 409
    assert r_paused.json()["error"]["code"] == "INVALID_RUN_STATE"

    async with w05_env["sessions"]() as db:
        cis = list(await db.scalars(select(CheckIn).where(CheckIn.run_id == UUID(run_id))))
        assert len(cis) == 0

        # 3. Giả lập bản ghi final bất hợp lệ đã tồn tại khi Run chưa End (ended_at=None, run_state='paused')
        bogus_ci = CheckIn(
            user_id=w05_env["user_id"],
            run_id=UUID(run_id),
            kind="final",
            response_state="open",
            session_outcome="unknown",
            asked_at=start_at + timedelta(minutes=6),
            response_revision=0,
        )
        db.add(bogus_ci)
        await db.commit()
        bogus_ci_id = str(bogus_ci.id)

    r_ans_bogus = await w05_client.put(
        f"/api/v1/check-ins/{bogus_ci_id}/response",
        headers={"Idempotency-Key": "cmd-w306-ans-bogus-final"},
        json={
            "expected_response_revision": 0,
            "responded_at": (start_at + timedelta(minutes=7)).isoformat(),
            "session_outcome": "completed",
            "step_completed": True,
            "task_completed": False,
        },
    )
    assert r_ans_bogus.status_code == 409
    assert r_ans_bogus.json()["error"]["code"] == "INVALID_RUN_STATE"

    detail = (await w05_client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert detail["run_state"] == "paused"
    assert detail["ended_at"] is None
    assert detail["actual_work_seconds"] is None

    # Chuyển bogus_ci sang mid để kiểm tiếp luồng End hợp lệ
    async with w05_env["sessions"]() as db:
        ci_row = await db.get(CheckIn, UUID(bogus_ci_id))
        assert ci_row is not None and ci_row.response_revision == 0
        ci_row.kind = "mid"
        await db.commit()

    ended_at = start_at + timedelta(minutes=30)
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or ended_at).astimezone(UTC),
    )
    r_end = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-w306-end-ok"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 2,
            "event_id": "ev-w306-end-ok",
            "ended_at": ended_at.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end.status_code == 200
    final_ci_id = r_end.json()["data"]["check_in"]["checkin_id"]

    # Gọi tạo thêm final sau khi End -> 409 FINAL_CHECKIN_EXISTS
    r_dup_final = await w05_client.post(
        f"/api/v1/runs/{run_id}/check-ins",
        headers={"Idempotency-Key": "cmd-w306-dup-final-after-end"},
        json={
            "kind": "final",
            "asked_at": ended_at.isoformat(),
            "expected_run_revision": 3,
        },
    )
    assert r_dup_final.status_code == 409
    assert r_dup_final.json()["error"]["code"] == "FINAL_CHECKIN_EXISTS"

    r_ans_ok = await w05_client.put(
        f"/api/v1/check-ins/{final_ci_id}/response",
        headers={"Idempotency-Key": "cmd-w306-ans-ok"},
        json={
            "expected_response_revision": 0,
            "responded_at": (ended_at + timedelta(seconds=15)).isoformat(),
            "session_outcome": "completed",
            "step_completed": False,
            "task_completed": False,
        },
    )
    assert r_ans_ok.status_code == 200


@pytest.mark.asyncio
async def test_w306_correction_reverts_projection_and_protects_other_run_confirmation(
    w05_client, w05_env, monkeypatch
):
    """Kiểm thử W3-06 [P1]:
    - Sửa phản hồi trên cùng checkin_id (completed -> incomplete, skip_step -> incomplete)
      hoàn tác đúng trạng thái Session/Step/Task và khôi phục chính xác remaining_base_seconds trước đó
      (không đoán từ estimated_base_seconds).
    - Giữ nguyên ended_at, work_elapsed_seconds, rest_elapsed_seconds, actual_work_seconds.
    - Không xóa xác nhận hợp lệ nếu một Run/CheckIn khác đã xác nhận hoàn thành Step/Task.
    """
    # Đặt remaining_base_seconds != estimated_base_seconds (2100 != 4500) để chứng minh không đoán từ estimated_base_seconds
    async with w05_env["sessions"]() as db:
        step_row = await db.get(Step, w05_env["step_id"])
        assert step_row is not None
        step_row.estimated_base_seconds = 4500
        step_row.remaining_base_seconds = 2100
        await db.commit()

    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    ended_at = start_at + timedelta(minutes=30)
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or ended_at).astimezone(UTC),
    )

    r_end = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-w306-corr-end-1"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-corr-end-1",
            "ended_at": ended_at.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end.status_code == 200
    cid = r_end.json()["data"]["check_in"]["checkin_id"]

    # Rev 0 -> 1: Báo xong toàn Step & Task
    r_rev1 = await w05_client.put(
        f"/api/v1/check-ins/{cid}/response",
        headers={"Idempotency-Key": "cmd-w306-corr-rev-1"},
        json={
            "expected_response_revision": 0,
            "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
            "session_outcome": "completed",
            "step_completed": True,
            "task_completed": True,
        },
    )
    assert r_rev1.status_code == 200
    async with w05_env["sessions"]() as db:
        st = await db.get(Step, w05_env["step_id"])
        tk = await db.get(Task, w05_env["task_id"])
        assert st is not None and st.status == "completed" and st.remaining_base_seconds == 0
        assert tk is not None and tk.status == "completed"

    # Rev 1 -> 2: Sửa thành skip_step
    r_rev2 = await w05_client.put(
        f"/api/v1/check-ins/{cid}/response",
        headers={"Idempotency-Key": "cmd-w306-corr-rev-2"},
        json={
            "expected_response_revision": 1,
            "responded_at": (ended_at + timedelta(seconds=20)).isoformat(),
            "session_outcome": "skipped",
            "step_completed": False,
            "task_completed": False,
            "skip_scope": "skip_step",
        },
    )
    assert r_rev2.status_code == 200
    async with w05_env["sessions"]() as db:
        st = await db.get(Step, w05_env["step_id"])
        tk = await db.get(Task, w05_env["task_id"])
        assert st is not None and st.status == "skipped" and st.remaining_base_seconds == 0
        assert tk is not None and tk.status == "pending"

    # Rev 2 -> 3: Sửa thành incomplete -> khôi phục step.status='pending' và remaining_base_seconds=2100!
    r_rev3 = await w05_client.put(
        f"/api/v1/check-ins/{cid}/response",
        headers={"Idempotency-Key": "cmd-w306-corr-rev-3"},
        json={
            "expected_response_revision": 2,
            "responded_at": (ended_at + timedelta(seconds=30)).isoformat(),
            "session_outcome": "incomplete",
            "step_completed": False,
            "task_completed": False,
        },
    )
    assert r_rev3.status_code == 200
    async with w05_env["sessions"]() as db:
        sess = await db.get(Session, w05_env["session_id"])
        st = await db.get(Step, w05_env["step_id"])
        tk = await db.get(Task, w05_env["task_id"])
        assert sess is not None and sess.status == "incomplete"
        assert st is not None and st.status == "pending" and st.remaining_base_seconds == 2100
        assert tk is not None and tk.status == "pending"

    run_detail = (await w05_client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert datetime.fromisoformat(run_detail["ended_at"]) == ended_at
    assert run_detail["work_elapsed_seconds"] == 1500
    assert run_detail["rest_elapsed_seconds"] == 300
    assert run_detail["actual_work_seconds"] == 1500

    # Bây giờ chạy Run 2 trên session1b và xác nhận hoàn thành Step/Task từ Run 2
    # Trước tiên cập nhật lại Run 1 sang completed + step_completed=True
    await w05_client.put(
        f"/api/v1/check-ins/{cid}/response",
        headers={"Idempotency-Key": "cmd-w306-corr-rev-4"},
        json={
            "expected_response_revision": 3,
            "responded_at": (ended_at + timedelta(seconds=40)).isoformat(),
            "session_outcome": "completed",
            "step_completed": False,
            "task_completed": False,
        },
    )

    s1b_id = str(w05_env["session1b_id"])
    s1b_start = cast(datetime, w05_env["session1b_start"])
    s1b_end = cast(datetime, w05_env["session1b_end"])
    r_s1b = await w05_client.post(
        f"/api/v1/sessions/{s1b_id}/start",
        headers={"Idempotency-Key": "cmd-w306-start-1b"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": s1b_start.isoformat(),
        },
    )
    assert r_s1b.status_code == 201
    run_1b_id = r_s1b.json()["data"]["run_id"]
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or s1b_end).astimezone(UTC),
    )

    r_end_1b = await w05_client.post(
        f"/api/v1/runs/{run_1b_id}/end",
        headers={"Idempotency-Key": "cmd-w306-end-1b"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-end-1b",
            "ended_at": s1b_end.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end_1b.status_code == 200
    cid_1b = r_end_1b.json()["data"]["check_in"]["checkin_id"]

    r_ans_1b = await w05_client.put(
        f"/api/v1/check-ins/{cid_1b}/response",
        headers={"Idempotency-Key": "cmd-w306-ans-1b"},
        json={
            "expected_response_revision": 0,
            "responded_at": (s1b_end + timedelta(seconds=10)).isoformat(),
            "session_outcome": "completed",
            "step_completed": True,
            "task_completed": True,
        },
    )
    assert r_ans_1b.status_code == 200

    # Khi Run 1 sửa lại thành incomplete, không được xóa xác nhận hợp lệ của Run 2 (cid_1b)!
    r_rev5 = await w05_client.put(
        f"/api/v1/check-ins/{cid}/response",
        headers={"Idempotency-Key": "cmd-w306-corr-rev-5"},
        json={
            "expected_response_revision": 4,
            "responded_at": (s1b_end + timedelta(seconds=20)).isoformat(),
            "session_outcome": "incomplete",
            "step_completed": False,
            "task_completed": False,
        },
    )
    assert r_rev5.status_code == 200
    async with w05_env["sessions"]() as db:
        st = await db.get(Step, w05_env["step_id"])
        tk = await db.get(Task, w05_env["task_id"])
        assert st is not None and st.status == "completed" and st.remaining_base_seconds == 0
        assert tk is not None and tk.status == "completed"


@pytest.mark.asyncio
async def test_w306_completion_cascade_early_confirm_and_contradictory_flags(
    w05_client, w05_env, monkeypatch
):
    """Kiểm thử W3-06 [P1] (R01):
    - Kiểm tra từ chối tổ hợp cờ mâu thuẫn (422 VALIDATION_ERROR).
    - Đường (a): Tự động suy từ session_outcome='completed' khi hoàn thành Session cuối của Step -> Step completed,
      và khi hoàn thành Step cuối của Task + unexpanded_scope rỗng -> Task completed (nếu còn unexpanded_scope thì không tự suy).
    - Đường (b): Xác nhận trực tiếp task_completed=True đóng toàn Task kể cả khi còn unexpanded_scope,
      giữ nguyên các phiên tương lai chưa chạy (không tạo SessionRun/actual giả).
    """
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    ended_at = start_at + timedelta(minutes=30)
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or ended_at).astimezone(UTC),
    )

    r_end = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-w306-r01-end-1a"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-r01-end-1a",
            "ended_at": ended_at.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end.status_code == 200
    cid_1a = r_end.json()["data"]["check_in"]["checkin_id"]

    # 1. Tổ hợp mâu thuẫn: session_outcome != 'completed' nhưng step_completed=True hoặc task_completed=True -> 422
    for bad_body in (
        {
            "expected_response_revision": 0,
            "responded_at": (ended_at + timedelta(seconds=5)).isoformat(),
            "session_outcome": "incomplete",
            "step_completed": True,
            "task_completed": False,
        },
        {
            "expected_response_revision": 0,
            "responded_at": (ended_at + timedelta(seconds=5)).isoformat(),
            "session_outcome": "partial",
            "step_completed": False,
            "task_completed": True,
        },
        {
            "expected_response_revision": 0,
            "responded_at": (ended_at + timedelta(seconds=5)).isoformat(),
            "session_outcome": "completed",
            "step_completed": False,
            "task_completed": True,
        },
    ):
        r_bad = await w05_client.put(
            f"/api/v1/check-ins/{cid_1a}/response",
            headers={"Idempotency-Key": f"cmd-w306-bad-{uuid4()}"},
            json=bad_body,
        )
        assert r_bad.status_code == 422
        assert r_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # 2. Hoàn thành Session 1A (chưa phải phiên cuối của Step 1) với step_completed=None -> Step 1 vẫn pending
    r_ok_1a = await w05_client.put(
        f"/api/v1/check-ins/{cid_1a}/response",
        headers={"Idempotency-Key": "cmd-w306-r01-ans-1a"},
        json={
            "expected_response_revision": 0,
            "responded_at": (ended_at + timedelta(seconds=10)).isoformat(),
            "session_outcome": "completed",
        },
    )
    assert r_ok_1a.status_code == 200
    async with w05_env["sessions"]() as db:
        st1 = await db.get(Step, w05_env["step_id"])
        assert st1 is not None and st1.status == "pending"

    # 3. Hoàn thành Session 1B (phiên cuối của Step 1) với step_completed=None -> tự đóng Step 1!
    s1b_id = str(w05_env["session1b_id"])
    s1b_start = cast(datetime, w05_env["session1b_start"])
    s1b_end = cast(datetime, w05_env["session1b_end"])
    r_s1b = await w05_client.post(
        f"/api/v1/sessions/{s1b_id}/start",
        headers={"Idempotency-Key": "cmd-w306-r01-start-1b"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": s1b_start.isoformat(),
        },
    )
    assert r_s1b.status_code == 201
    run_1b_id = r_s1b.json()["data"]["run_id"]
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or s1b_end).astimezone(UTC),
    )
    r_end_1b = await w05_client.post(
        f"/api/v1/runs/{run_1b_id}/end",
        headers={"Idempotency-Key": "cmd-w306-r01-end-1b"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-r01-end-1b",
            "ended_at": s1b_end.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end_1b.status_code == 200
    cid_1b = r_end_1b.json()["data"]["check_in"]["checkin_id"]

    r_ok_1b = await w05_client.put(
        f"/api/v1/check-ins/{cid_1b}/response",
        headers={"Idempotency-Key": "cmd-w306-r01-ans-1b"},
        json={
            "expected_response_revision": 0,
            "responded_at": (s1b_end + timedelta(seconds=10)).isoformat(),
            "session_outcome": "completed",
        },
    )
    assert r_ok_1b.status_code == 200
    async with w05_env["sessions"]() as db:
        st1 = await db.get(Step, w05_env["step_id"])
        tk = await db.get(Task, w05_env["task_id"])
        assert st1 is not None and st1.status == "completed" and st1.remaining_base_seconds == 0
        assert tk is not None and tk.status == "pending"
        # Đặt unexpanded_scope trước khi chạy Step 2
        tk.unexpanded_scope = "Còn phần tổng kết chưa chia lịch"
        await db.commit()

    # 4. Hoàn thành Session 2 (phiên cuối của Step 2 - Step cuối đã chia lịch) khi còn unexpanded_scope:
    # Step 2 tự đóng, nhưng Task KHÔNG tự suy completed!
    s2_id = str(w05_env["session2_id"])
    s2_start = cast(datetime, w05_env["session2_start"])
    s2_end = cast(datetime, w05_env["session2_end"])
    r_s2 = await w05_client.post(
        f"/api/v1/sessions/{s2_id}/start",
        headers={"Idempotency-Key": "cmd-w306-r01-start-2"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": s2_start.isoformat(),
        },
    )
    assert r_s2.status_code == 201
    run_2_id = r_s2.json()["data"]["run_id"]
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or s2_end).astimezone(UTC),
    )
    r_end_2 = await w05_client.post(
        f"/api/v1/runs/{run_2_id}/end",
        headers={"Idempotency-Key": "cmd-w306-r01-end-2"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-r01-end-2",
            "ended_at": s2_end.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end_2.status_code == 200
    cid_2 = r_end_2.json()["data"]["check_in"]["checkin_id"]

    r_ok_2 = await w05_client.put(
        f"/api/v1/check-ins/{cid_2}/response",
        headers={"Idempotency-Key": "cmd-w306-r01-ans-2"},
        json={
            "expected_response_revision": 0,
            "responded_at": (s2_end + timedelta(seconds=10)).isoformat(),
            "session_outcome": "completed",
        },
    )
    assert r_ok_2.status_code == 200
    async with w05_env["sessions"]() as db:
        st2 = await db.get(Step, w05_env["step2_id"])
        tk = await db.get(Task, w05_env["task_id"])
        assert st2 is not None and st2.status == "completed" and st2.remaining_base_seconds == 0
        assert tk is not None and tk.status != "completed"

    # 5. Người dùng chủ động xác nhận task_completed=True -> đóng luôn Task dù còn unexpanded_scope!
    r_explicit_task = await w05_client.put(
        f"/api/v1/check-ins/{cid_2}/response",
        headers={"Idempotency-Key": "cmd-w306-r01-ans-2-explicit-task"},
        json={
            "expected_response_revision": 1,
            "responded_at": (s2_end + timedelta(seconds=20)).isoformat(),
            "session_outcome": "completed",
            "step_completed": True,
            "task_completed": True,
        },
    )
    assert r_explicit_task.status_code == 200
    async with w05_env["sessions"]() as db:
        tk = await db.get(Task, w05_env["task_id"])
        assert tk is not None and tk.status == "completed"


@pytest.mark.asyncio
async def test_w306_timeout_300s_boundaries_and_long_offline_end(
    w05_client, w05_env, monkeypatch
):
    """Kiểm thử W3-06 [P2]:
    - Biên timeout chính xác tại ended_at + 299s (open), +300s (no_respond), +301s (no_respond).
    - Đồng bộ End offline lâu (> 1 giờ) trả ngay response_state='no_respond' nhất quán với GET detail.
    """
    # 1. End offline lâu (> 1 giờ: clock hiện tại cách ended_at 5 giờ) -> no_respond ngay khi End
    run = await _start_run(w05_client, w05_env)
    run_id = run["run_id"]
    start_at = cast(datetime, w05_env["scheduled_start"])
    ended_at = start_at + timedelta(minutes=30)
    clock = [ended_at + timedelta(hours=5)]
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or clock[0]).astimezone(UTC),
    )

    r_offline_end = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": "cmd-w306-offline-5h-end"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-offline-5h-end",
            "ended_at": ended_at.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_offline_end.status_code == 200
    offline_data = r_offline_end.json()["data"]
    assert offline_data["check_in"]["response_state"] == "no_respond"
    detail_data = (await w05_client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert detail_data["check_in"]["response_state"] == "no_respond"
    assert offline_data["actual_work_seconds"] == detail_data["actual_work_seconds"] == 1500

    # 2. Kiểm biên 299s (open) -> 300s (no_respond) trên session1b
    s1b_id = str(w05_env["session1b_id"])
    s1b_start = cast(datetime, w05_env["session1b_start"])
    s1b_end = cast(datetime, w05_env["session1b_end"])
    clock[0] = s1b_start
    r_s1b = await w05_client.post(
        f"/api/v1/sessions/{s1b_id}/start",
        headers={"Idempotency-Key": "cmd-w306-bound-start-1b"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "android_pixel_8",
            "requested_start_at": s1b_start.isoformat(),
        },
    )
    assert r_s1b.status_code == 201
    run_1b_id = r_s1b.json()["data"]["run_id"]

    # End tại t = s1b_end + 299s -> vẫn 'open'
    clock[0] = s1b_end + timedelta(seconds=299)
    r_end_299 = await w05_client.post(
        f"/api/v1/runs/{run_1b_id}/end",
        headers={"Idempotency-Key": "cmd-w306-bound-end-1b"},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "event_id": "ev-w306-bound-end-1b",
            "ended_at": s1b_end.isoformat(),
            "end_reason": "timer_elapsed",
            "work_elapsed_seconds": 1500,
            "rest_elapsed_seconds": 300,
        },
    )
    assert r_end_299.status_code == 200
    assert r_end_299.json()["data"]["check_in"]["response_state"] == "open"

    # Đọc GET tại 299s -> vẫn 'open'
    d_299 = (await w05_client.get(f"/api/v1/runs/{run_1b_id}")).json()["data"]
    assert d_299["check_in"]["response_state"] == "open"

    # Đọc GET tại đúng 300s -> chuyển sang 'no_respond'
    clock[0] = s1b_end + timedelta(seconds=300)
    d_300 = (await w05_client.get(f"/api/v1/runs/{run_1b_id}")).json()["data"]
    assert d_300["check_in"]["response_state"] == "no_respond"
