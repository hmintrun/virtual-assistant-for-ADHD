"""Official Regression Test Suite: UF3/UF4 Deep Fixes.

Covering:
a) Clock skew > 300s (reject 422) vs <= 300s (valid) on:
   - /sessions/{id}/start
   - /runs/{id}/events
   - /runs/{id}/end
   - /runs/{id}/actual-corrections
   - /devices/{id}/state
   - /reminders/{id}/receipt
b) BUG-03: recent_runs 15-minute gap enforcement when other_sessions is empty (session ended 5m ago, but scheduled > 24h ago).
c) Zero-AI Guard: capture_state="recording" before STT, linked thought, live run, MultipleResultsFound -> 409.
d) voice.py: VoiceIdempotencyCache evict/TTL fallback DB, single-flight stampede concurrency.
e) BUG-07: reminder monotonicity, terminal state locks (dismissed/acknowledged), snooze cancellation on start/approval.
f) Run guards: paused rejects progress (409), monotonic event time/segment, ended_at >= last_event_at, actual correction validations.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_current_user, get_db
from app.api.v1 import voice as voice_router_mod
from app.core.database import make_engine
from app.core.errors import AppError
from app.main import app
from app.models import (
    Base,
    PlanningDraft,
    Reminder,
    RunEvent,
    Session,
    SessionRun,
    Step,
    Task,
    Thought,
    User,
    UserPreference,
)
from app.schemas.user import CurrentUser
from app.schemas.voice import TranscriptionData
from app.services import thought_service
from app.services.voice_service import SttResult, get_voice_service


@pytest.fixture
def regression_db_factory():
    """Tạo DB in-memory SQLite hoàn chỉnh cho mỗi test case."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")

    async def init_models():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(init_models())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture
async def regression_client(regression_db_factory):
    """Client HTTP gọi API với dependency injection DB và User chuẩn."""
    user_id = uuid4()
    user_str = str(user_id)

    async with regression_db_factory() as db:
        user = User(id=user_id, email="regression_tester@example.com", display_name="Regression Tester", is_active=True)
        pref = UserPreference(
            user_id=user_id,
            reminder_offsets=[900, 600, 300],
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.2,
        )
        db.add_all([user, pref])
        await db.commit()

    async def override_db():
        async with regression_db_factory() as db:
            yield db

    async def override_user():
        return CurrentUser(user_id=user_str, email="regression_tester@example.com", display_name="Regression Tester")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client, regression_db_factory, user_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


async def _create_test_plan(
    db: AsyncSession,
    user_id: UUID,
    start_time: datetime | None = None,
    s2_gap_minutes: int = 20,
) -> tuple[Task, Step, Session, Session]:
    base_time = start_time or datetime.now(UTC)
    task = Task(
        id=uuid4(),
        user_id=user_id,
        title="Nhiệm vụ Regression",
        status="scheduled",
        decomposition_state="complete",
        time_constraint={"type": "deadline", "deadline": (base_time + timedelta(days=7)).isoformat()},
    )
    db.add(task)
    await db.flush()

    step = Step(
        id=uuid4(),
        user_id=user_id,
        task_id=task.id,
        title="Bước 1",
        order_index=0,
        estimated_base_seconds=3000,
        remaining_base_seconds=3000,
        buffer_multiplier=1.2,
        estimate_source="user",
        status="scheduled",
    )
    db.add(step)
    await db.flush()

    s1_start = base_time + timedelta(minutes=5)
    s1_end = s1_start + timedelta(minutes=25)
    sess1 = Session(
        id=uuid4(),
        user_id=user_id,
        step_id=step.id,
        scheduled_start=s1_start,
        scheduled_end=s1_end,
        work_seconds=1500,
        rest_seconds=0,
        duration_seconds=1500,
        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
        schedule_revision=1,
        status="scheduled",
        start_state="waiting",
    )

    s2_start = s1_end + timedelta(minutes=s2_gap_minutes)
    s2_end = s2_start + timedelta(minutes=25)
    sess2 = Session(
        id=uuid4(),
        user_id=user_id,
        step_id=step.id,
        scheduled_start=s2_start,
        scheduled_end=s2_end,
        work_seconds=1500,
        rest_seconds=0,
        duration_seconds=1500,
        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
        schedule_revision=1,
        status="scheduled",
        start_state="waiting",
    )
    db.add_all([sess1, sess2])
    await db.commit()
    return task, step, sess1, sess2


# ==============================================================================
# a) CLOCK SKEW TESTS (> 300s REJECT 422 vs <= 300s VALID)
# ==============================================================================
@pytest.mark.asyncio
async def test_a_clock_skew_all_endpoints(regression_client, simulated_clock):
    client, factory, user_id = regression_client
    now_t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    simulated_clock.set(now_t0)

    async with factory() as db:
        _, _, sess1, sess2 = await _create_test_plan(db, user_id, start_time=now_t0)
        sess1_id = sess1.id
        sess2_id = sess2.id

    # 1. /sessions/{id}/start
    # Skew > 300s (301s in future) -> 422
    res_start_bad = await client.post(
        f"/api/v1/sessions/{sess1_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev_clock",
            "requested_start_at": (now_t0 + timedelta(seconds=301)).isoformat(),
        },
    )
    assert res_start_bad.status_code == 422
    assert res_start_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # Skew <= 300s (200s in past) -> 201 Created
    res_start_ok = await client.post(
        f"/api/v1/sessions/{sess1_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev_clock",
            "requested_start_at": (now_t0 - timedelta(seconds=200)).isoformat(),
        },
    )
    assert res_start_ok.status_code == 201
    run_id = res_start_ok.json()["data"]["run_id"]

    # 2. /runs/{id}/events
    # Skew > 300s (301s in future) -> 422
    res_event_bad = await client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_clock",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": str(uuid4()),
                    "event_type": "progress",
                    "occurred_at": (now_t0 + timedelta(seconds=301)).isoformat(),
                    "segment_index": 0,
                    "work_elapsed_seconds": 10,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    assert res_event_bad.status_code == 422
    assert res_event_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # Skew <= 300s (100s in past) -> 200 OK
    res_event_ok = await client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_clock",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": str(uuid4()),
                    "event_type": "progress",
                    "occurred_at": (now_t0 - timedelta(seconds=100)).isoformat(),
                    "segment_index": 0,
                    "work_elapsed_seconds": 10,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    assert res_event_ok.status_code == 200

    # 3. /runs/{id}/end
    # Skew > 300s (301s in future) -> 422
    res_end_bad = await client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_clock",
            "expected_run_revision": 2,
            "event_id": str(uuid4()),
            "ended_at": (now_t0 + timedelta(seconds=301)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 10,
            "rest_elapsed_seconds": 0,
        },
    )
    assert res_end_bad.status_code == 422
    assert res_end_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # Skew <= 300s (50s in past >= event at -100s) -> 200 OK
    res_end_ok = await client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_clock",
            "expected_run_revision": 2,
            "event_id": str(uuid4()),
            "ended_at": (now_t0 - timedelta(seconds=50)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 10,
            "rest_elapsed_seconds": 0,
        },
    )
    assert res_end_ok.status_code == 200, res_end_ok.text

    # 4. /runs/{id}/actual-corrections
    # Skew > 300s (occurred_at > server_now + 300s) -> 422
    res_corr_bad = await client.post(
        f"/api/v1/runs/{run_id}/actual-corrections",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_run_revision": 3,
            "self_report_work_seconds": 12,
            "occurred_at": (now_t0 + timedelta(seconds=301)).isoformat(),
            "reason": "Điều chỉnh chính xác",
        },
    )
    assert res_corr_bad.status_code == 422
    assert res_corr_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # Skew <= 300s (occurred_at >= ended_at and <= server_now + 300s) -> 200 OK
    res_corr_ok = await client.post(
        f"/api/v1/runs/{run_id}/actual-corrections",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_run_revision": 3,
            "self_report_work_seconds": 12,
            "occurred_at": (now_t0 - timedelta(seconds=40)).isoformat(),
            "reason": "Điều chỉnh chính xác",
        },
    )
    assert res_corr_ok.status_code == 200

    # 5. /devices/{id}/state (PUT)
    # reported_at > server_now + 300s -> 422
    res_dev_bad = await client.put(
        "/api/v1/devices/dev_clock/state",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_report_revision": 1,
            "reported_at": (now_t0 + timedelta(seconds=301)).isoformat(),
            "notification_permission": "granted",
            "microphone_permission": "granted",
            "dnd_permission": "granted",
            "alarm_capability": "exact_granted",
            "dnd_apply_state": "not_applied",
            "dnd_restore_state": "not_needed",
            "app_visibility": "foreground",
        },
    )
    assert res_dev_bad.status_code == 422
    assert res_dev_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # reported_at <= server_now + 300s -> 200 OK
    res_dev_ok = await client.put(
        "/api/v1/devices/dev_clock/state",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_report_revision": 1,
            "reported_at": (now_t0 - timedelta(seconds=50)).isoformat(),
            "notification_permission": "granted",
            "microphone_permission": "granted",
            "dnd_permission": "granted",
            "alarm_capability": "exact_granted",
            "dnd_apply_state": "not_applied",
            "dnd_restore_state": "not_needed",
            "app_visibility": "foreground",
        },
    )
    assert res_dev_ok.status_code == 200

    # 6. /reminders/{id}/receipt
    async with factory() as db:
        rem = Reminder(
            id=uuid4(),
            user_id=user_id,
            session_id=sess2_id,
            device_id="dev_clock",
            schedule_revision=1,
            kind="before_session",
            fire_at=now_t0 - timedelta(minutes=10),
            dedupe_key=f"rem:{uuid4()}",
            delivery_state="scheduled",
        )
        db.add(rem)
        await db.commit()
        rem_id = rem.id

    # occurred_at > server_now + 300s -> 422
    res_rem_bad = await client.post(
        f"/api/v1/reminders/{rem_id}/receipt",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "schedule_revision": 1,
            "device_id": "dev_clock",
            "delivery_state": "delivered",
            "occurred_at": (now_t0 + timedelta(seconds=301)).isoformat(),
        },
    )
    assert res_rem_bad.status_code == 422
    assert res_rem_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # occurred_at <= server_now + 300s -> 200 OK
    res_rem_ok = await client.post(
        f"/api/v1/reminders/{rem_id}/receipt",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "schedule_revision": 1,
            "device_id": "dev_clock",
            "delivery_state": "delivered",
            "occurred_at": (now_t0 - timedelta(seconds=50)).isoformat(),
        },
    )
    assert res_rem_ok.status_code == 200


# ==============================================================================
# b) BUG-03: recent_runs 15-MINUTE GAP ENFORCEMENT WHEN other_sessions IS EMPTY
# ==============================================================================
@pytest.mark.asyncio
async def test_b_bug03_recent_runs_15m_gap_when_other_sessions_empty(regression_client, simulated_clock):
    """BUG-03: Session A scheduled > 24h ago (outside 24h other_sessions window),

    nhưng SessionRun vừa kết thúc 5 phút trước (< 15 phút gap).
    other_sessions query trả về rỗng, nhưng recent_runs BẮT BUỘC phải bắt được
    và ném 409 START_REQUIRES_REPLAN khi Session B bấm Start.
    Nếu Session A kết thúc 16 phút trước (>= 15 phút), Session B được Start (201 Created).
    """
    client, factory, user_id = regression_client
    now = datetime(2026, 11, 20, 15, 0, 0, tzinfo=UTC)
    simulated_clock.set(now)

    async with factory() as db:
        # Session A: lên lịch 26 giờ trước (> 24h window)
        task_a = Task(
            id=uuid4(),
            user_id=user_id,
            title="Task A Cũ",
            status="scheduled",
            time_constraint={"type": "deadline", "deadline": (now + timedelta(days=1)).isoformat()},
        )
        db.add(task_a)
        await db.flush()

        step_a = Step(
            id=uuid4(),
            user_id=user_id,
            task_id=task_a.id,
            title="Bước A",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=0,
            buffer_multiplier=1.2,
            estimate_source="user",
            status="scheduled",
        )
        db.add(step_a)
        await db.flush()

        sess_a = Session(
            id=uuid4(),
            user_id=user_id,
            step_id=step_a.id,
            scheduled_start=now - timedelta(hours=26),
            scheduled_end=now - timedelta(hours=25, minutes=35),
            work_seconds=1500,
            rest_seconds=0,
            duration_seconds=1500,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            schedule_revision=1,
            status="completed",
        )
        db.add(sess_a)
        await db.flush()

        # Run của Session A: vừa kết thúc 5 phút trước!
        run_a = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess_a.id,
            device_id="dev_old",
            run_state="completed",
            started_at=now - timedelta(minutes=30),
            ended_at=now - timedelta(minutes=5),  # 5 phút trước
            active_phase="ended",
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            settings_snapshot={"session": {"duration_seconds": 1500}},
        )
        db.add(run_a)

        # Session B: phiên duy nhất hiện tại, lên lịch lúc now
        task_b = Task(
            id=uuid4(),
            user_id=user_id,
            title="Task B Mới",
            status="scheduled",
            time_constraint={"type": "deadline", "deadline": (now + timedelta(days=2)).isoformat()},
        )
        db.add(task_b)
        await db.flush()

        step_b = Step(
            id=uuid4(),
            user_id=user_id,
            task_id=task_b.id,
            title="Bước B",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user",
            status="scheduled",
        )
        db.add(step_b)
        await db.flush()

        sess_b = Session(
            id=uuid4(),
            user_id=user_id,
            step_id=step_b.id,
            scheduled_start=now,
            scheduled_end=now + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=0,
            duration_seconds=1500,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            schedule_revision=1,
            status="scheduled",
        )
        db.add(sess_b)
        await db.commit()
        sess_b_id = sess_b.id
        run_a_id = run_a.id

    # 1. other_sessions hoàn toàn rỗng vì sess_a nằm ngoài 24h.
    # Nhưng Session A mới kết thúc 5 phút trước -> vi phạm 15p gap -> 409 START_REQUIRES_REPLAN!
    res_b_blocked = await client.post(
        f"/api/v1/sessions/{sess_b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev_new",
            "requested_start_at": now.isoformat(),
        },
    )
    assert res_b_blocked.status_code == 409
    assert res_b_blocked.json()["error"]["code"] == "START_REQUIRES_REPLAN"

    # 2. Cập nhật Session A đã kết thúc cách đây 16 phút (16m >= 15m gap)
    async with factory() as db:
        run_row = await db.get(SessionRun, run_a_id)
        assert run_row is not None
        run_row.ended_at = now - timedelta(minutes=16)
        await db.commit()

    # Bắt đầu Session B thành công 201 Created!
    res_b_ok = await client.post(
        f"/api/v1/sessions/{sess_b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev_new",
            "requested_start_at": now.isoformat(),
        },
    )
    assert res_b_ok.status_code == 201


# ==============================================================================
# c) ZERO-AI GUARD TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_c_zero_ai_guard_validations(regression_client, simulated_clock):
    client, factory, user_id = regression_client
    now = datetime(2026, 11, 20, 11, 0, 0, tzinfo=UTC)
    simulated_clock.set(now)

    # 1. capture_state="recording" before STT -> 409 THOUGHT_CAPTURE_NOT_SAVED
    async with factory() as db:
        t_recording = Thought(
            id=uuid4(),
            user_id=user_id,
            client_thought_id=str(uuid4()),
            source_run_id=None,
            input_type="audio",
            capture_state="recording",
            attempt_count=1,
        )
        db.add(t_recording)
        await db.commit()
        t_rec_id = t_recording.id

    res_rec = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": str(uuid4())},
        data={"purpose": "thought_to_draft", "device_id": "dev_1", "thought_id": str(t_rec_id)},
        files={"file": ("test.m4a", b"dummy_audio_bytes", "audio/m4a")},
    )
    assert res_rec.status_code == 409
    assert res_rec.json()["error"]["code"] == "THOUGHT_CAPTURE_NOT_SAVED"

    # 2. linked thought (already has draft) -> 409 THOUGHT_ALREADY_LINKED
    async with factory() as db:
        t_saved = Thought(
            id=uuid4(),
            user_id=user_id,
            client_thought_id=str(uuid4()),
            input_type="audio",
            capture_state="saved",
            attempt_count=1,
        )
        db.add(t_saved)
        await db.flush()

        draft = PlanningDraft(
            id=uuid4(),
            user_id=user_id,
            client_draft_id=f"draft-{t_saved.id}",
            input_source="audio",
            source_thought_id=t_saved.id,
            raw_input="Đã chuyển thành draft",
            stage="summary_review",
        )
        db.add(draft)
        await db.commit()
        t_saved_id = t_saved.id

    res_linked = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": str(uuid4())},
        data={"purpose": "thought_to_draft", "device_id": "dev_1", "thought_id": str(t_saved_id)},
        files={"file": ("test.m4a", b"dummy_audio_bytes", "audio/m4a")},
    )
    assert res_linked.status_code == 409
    assert res_linked.json()["error"]["code"] == "THOUGHT_ALREADY_LINKED"

    # 3. live run currently running -> 409 SESSION_STILL_RUNNING
    async with factory() as db:
        _, _, sess, _ = await _create_test_plan(db, user_id, start_time=now)
        live_run = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess.id,
            device_id="dev_1",
            run_state="running",
            started_at=now,
            active_phase="work",
            work_elapsed_seconds=30,
            rest_elapsed_seconds=0,
            settings_snapshot={"session": {"duration_seconds": 1500}},
        )
        t_unlinked = Thought(
            id=uuid4(),
            user_id=user_id,
            client_thought_id=str(uuid4()),
            source_run_id=None,
            input_type="audio",
            capture_state="saved",
            attempt_count=1,
        )
        db.add_all([live_run, t_unlinked])
        await db.commit()
        t_unlinked_id = t_unlinked.id

    res_live = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": str(uuid4())},
        data={"purpose": "thought_to_draft", "device_id": "dev_1", "thought_id": str(t_unlinked_id)},
        files={"file": ("test.m4a", b"dummy_audio_bytes", "audio/m4a")},
    )
    assert res_live.status_code == 409
    assert res_live.json()["error"]["code"] == "SESSION_STILL_RUNNING"

    # 4. MultipleResultsFound on thought links -> 409 SOURCE_CHANGED
    async with factory() as db:
        with patch.object(thought_service.thought_repository, "get_links", side_effect=MultipleResultsFound):
            with pytest.raises(AppError) as exc_multi:
                await thought_service.ensure_thought_eligible_for_draft_conversion(
                    db, user_id, t_unlinked_id
                )
            assert exc_multi.value.status_code == 409
            assert exc_multi.value.code == "SOURCE_CHANGED"


# ==============================================================================
# d) VOICE IDEMPOTENCY CACHE, TTL, DB FALLBACK & CONCURRENCY
# ==============================================================================
@pytest.mark.asyncio
async def test_d_voice_idempotency_cache_ttl_and_concurrency(regression_client, simulated_clock):
    client, factory, user_id = regression_client
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    simulated_clock.set(now)

    # 1. VoiceIdempotencyCache Unit Test: LRU & TTL
    cache = voice_router_mod.VoiceIdempotencyCache(max_entries=2, ttl_seconds=10.0)
    data1 = TranscriptionData(text="Text 1", input_source="voice")
    data2 = TranscriptionData(text="Text 2", input_source="voice")
    data3 = TranscriptionData(text="Text 3", input_source="voice")

    cache[("u1", "k1")] = ("fp1", data1)
    cache[("u1", "k2")] = ("fp2", data2)
    assert ("u1", "k1") in cache
    assert ("u1", "k2") in cache

    # Thêm k3 vượt quá max_entries=2 -> k1 bị LRU evicted
    cache[("u1", "k3")] = ("fp3", data3)
    assert cache.get(("u1", "k1")) is None
    assert cache.get(("u1", "k2")) is not None
    assert cache.get(("u1", "k3")) is not None

    # Test TTL fallback:
    cache_ttl = voice_router_mod.VoiceIdempotencyCache(max_entries=10, ttl_seconds=0.01)
    cache_ttl[("u1", "k_ttl")] = ("fp_ttl", data1)
    await asyncio.sleep(0.02)
    assert cache_ttl.get(("u1", "k_ttl")) is None

    # 2. Integration: Khi cache evicted, replay đọc từ DB CommandReceipt trả về 200
    idem_key = f"voice_idem_{uuid4()}"
    dummy_audio = b"\xaa\xbb\xcc\xdd" * 64

    # Lần 1: Gọi STT thành công
    res1 = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_key},
        data={"purpose": "uf1_input", "device_id": "dev_1"},
        files={"file": ("test.wav", dummy_audio, "audio/wav")},
    )
    assert res1.status_code == 200
    res1_data = res1.json()["data"]

    # Xóa sạch RAM cache để ép buộc fallback xuống database
    voice_router_mod._VOICE_IDEMPOTENCY_STORE.clear()
    assert voice_router_mod._VOICE_IDEMPOTENCY_STORE.get((str(user_id), idem_key)) is None

    # Lần 2: Replay đúng Idempotency-Key và payload -> DB fallback trả về 200 OK
    res2 = await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": idem_key},
        data={"purpose": "uf1_input", "device_id": "dev_1"},
        files={"file": ("test.wav", dummy_audio, "audio/wav")},
    )
    assert res2.status_code == 200
    assert res2.json()["data"] == res1_data

    # 3. Concurrency single-flight stampede protection
    concurrent_key = f"concurrent_key_{uuid4()}"
    call_count = 0

    real_service = get_voice_service()

    async def mock_transcribe(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)  # Giả lập delay mạng
        return SttResult(text="Chuyển âm thành công qua STT", provider="gemini", latency_ms=100)

    with patch.object(real_service, "transcribe", side_effect=mock_transcribe):
        tasks = [
            client.post(
                "/api/v1/voice/transcriptions",
                headers={"Idempotency-Key": concurrent_key},
                data={"purpose": "uf1_input", "device_id": "dev_1"},
                files={"file": ("test.wav", dummy_audio, "audio/wav")},
            )
            for _ in range(3)
        ]
        responses = await asyncio.gather(*tasks)

        # Tất cả 3 request đồng thời đều nhận 200 OK
        for r in responses:
            assert r.status_code == 200
            assert r.json()["data"]["text"] == "Chuyển âm thành công qua STT"

        # Khóa per-key Lock đảm bảo transcribe chỉ được gọi duy nhất 1 lần!
        assert call_count == 1


# ==============================================================================
# e) BUG-07: REMINDERS MONOTONICITY, TERMINAL STATE LOCKS & SNOOZE CANCELLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_e_bug07_reminders_monotonicity_terminal_states_and_snooze(regression_client, simulated_clock):
    client, factory, user_id = regression_client
    now = datetime(2026, 11, 20, 13, 0, 0, tzinfo=UTC)
    simulated_clock.set(now)

    async with factory() as db:
        _, _, sess, _ = await _create_test_plan(db, user_id, start_time=now)
        rem = Reminder(
            id=uuid4(),
            user_id=user_id,
            session_id=sess.id,
            device_id="dev_rem",
            schedule_revision=1,
            kind="before_session",
            fire_at=now + timedelta(minutes=5),
            dedupe_key=f"rem:{uuid4()}",
            delivery_state="delivered",
        )
        db.add(rem)
        await db.commit()
        rem_id = rem.id
        sess_id = sess.id

    # 1. Monotonicity: Không thể quay lùi từ 'delivered' về 'scheduled' -> 409
    res_mono_back = await client.post(
        f"/api/v1/reminders/{rem_id}/receipt",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "schedule_revision": 1,
            "device_id": "dev_rem",
            "delivery_state": "scheduled",
            "occurred_at": now.isoformat(),
        },
    )
    assert res_mono_back.status_code == 409
    assert res_mono_back.json()["error"]["code"] == "INVALID_REMINDER_STATE"

    # 2. Chuyển sang terminal state 'dismissed'
    res_dismiss = await client.post(
        f"/api/v1/reminders/{rem_id}/receipt",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "schedule_revision": 1,
            "device_id": "dev_rem",
            "delivery_state": "dismissed",
            "user_action": "dismiss",
            "occurred_at": now.isoformat(),
        },
    )
    assert res_dismiss.status_code == 200

    # 3. Terminal state lock: Đã ở 'dismissed', gửi trạng thái khác -> 409
    res_terminal_locked = await client.post(
        f"/api/v1/reminders/{rem_id}/receipt",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "schedule_revision": 1,
            "device_id": "dev_rem",
            "delivery_state": "acknowledged",
            "user_action": "start_now",
            "occurred_at": now.isoformat(),
        },
    )
    assert res_terminal_locked.status_code == 409
    assert res_terminal_locked.json()["error"]["code"] == "INVALID_REMINDER_STATE"

    # 4. Snooze cancellation on session start
    async with factory() as db:
        rem_snooze = Reminder(
            id=uuid4(),
            user_id=user_id,
            session_id=sess_id,
            device_id="dev_rem",
            schedule_revision=1,
            kind="before_session",
            fire_at=now + timedelta(minutes=2),
            dedupe_key=f"rem:{uuid4()}",
            delivery_state="snoozed",
        )
        db.add(rem_snooze)
        await db.commit()
        rem_snooze_id = rem_snooze.id

    # Bấm Start session -> rem_snooze phải chuyển sang 'cancelled'
    res_start = await client.post(
        f"/api/v1/sessions/{sess_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_schedule_revision": 1,
            "device_id": "dev_rem",
            "requested_start_at": now.isoformat(),
        },
    )
    assert res_start.status_code == 201

    async with factory() as db:
        rem_row = await db.get(Reminder, rem_snooze_id)
        assert rem_row is not None
        assert rem_row.delivery_state == "cancelled"


# ==============================================================================
# f) RUN GUARDS TESTS: PAUSED REJECTS PROGRESS, MONOTONICITY, ACTUAL CORRECTION
# ==============================================================================
@pytest.mark.asyncio
async def test_f_run_guards_and_validations(regression_client, simulated_clock):
    client, factory, user_id = regression_client
    now = datetime(2026, 11, 20, 14, 0, 0, tzinfo=UTC)
    simulated_clock.set(now)

    async with factory() as db:
        _, _, sess, _ = await _create_test_plan(db, user_id, start_time=now)
        # 1. Run đang ở trạng thái 'paused'
        run_paused = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess.id,
            device_id="dev_guards",
            run_state="paused",
            started_at=now - timedelta(minutes=10),
            active_phase="work",
            work_elapsed_seconds=300,
            rest_elapsed_seconds=0,
            segment_index=0,
            settings_snapshot={"session": {"duration_seconds": 1500}},
        )
        db.add(run_paused)
        await db.commit()
        run_p_id = run_paused.id

    # Paused run rejects 'progress' event -> 409 INVALID_RUN_STATE
    res_paused_progress = await client.post(
        f"/api/v1/runs/{run_p_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_guards",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": str(uuid4()),
                    "event_type": "progress",
                    "occurred_at": now.isoformat(),
                    "segment_index": 0,
                    "work_elapsed_seconds": 350,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    assert res_paused_progress.status_code == 409
    assert res_paused_progress.json()["error"]["code"] == "INVALID_RUN_STATE"

    # 2. Monotonic event time & segment_index
    async with factory() as db:
        rp = await db.get(SessionRun, run_p_id)
        if rp is not None:
            rp.run_state = "closed"
            rp.ended_at = now - timedelta(minutes=5)

        run_running = SessionRun(
            id=uuid4(),
            user_id=user_id,
            session_id=sess.id,
            device_id="dev_guards",
            run_state="running",
            started_at=now - timedelta(minutes=10),
            active_phase="work",
            work_elapsed_seconds=300,
            rest_elapsed_seconds=0,
            segment_index=1,
            settings_snapshot={"session": {"duration_seconds": 1500}},
        )
        db.add(run_running)
        await db.flush()

        # Thêm RunEvent lúc now - 2m
        ev = RunEvent(
            id=uuid4(),
            user_id=user_id,
            run_id=run_running.id,
            client_event_id=str(uuid4()),
            event_type="progress",
            occurred_at=now - timedelta(minutes=2),
            payload={"work_elapsed_seconds": 300, "rest_elapsed_seconds": 0},
        )
        db.add(ev)
        await db.commit()
        run_r_id = run_running.id

    # Gửi sự kiện kéo lùi segment_index từ 1 về 0 -> 422 VALIDATION_ERROR
    res_seg_back = await client.post(
        f"/api/v1/runs/{run_r_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_guards",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": str(uuid4()),
                    "event_type": "progress",
                    "occurred_at": now.isoformat(),
                    "segment_index": 0,
                    "work_elapsed_seconds": 300,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    assert res_seg_back.status_code == 422
    assert res_seg_back.json()["error"]["code"] == "VALIDATION_ERROR"

    # 3. ended_at >= last_event_at
    # Event cuối lúc now - 2m, gọi /end với ended_at lúc now - 5m -> 422
    res_end_before_event = await client.post(
        f"/api/v1/runs/{run_r_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_guards",
            "expected_run_revision": 1,
            "event_id": str(uuid4()),
            "ended_at": (now - timedelta(minutes=5)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 300,
            "rest_elapsed_seconds": 0,
        },
    )
    assert res_end_before_event.status_code == 422
    assert res_end_before_event.json()["error"]["code"] == "VALIDATION_ERROR"

    # 4. Actual correction validations on ended run
    # Kết thúc hợp lệ lúc now - 1m
    res_end_ok = await client.post(
        f"/api/v1/runs/{run_r_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_guards",
            "expected_run_revision": 1,
            "event_id": str(uuid4()),
            "ended_at": (now - timedelta(minutes=1)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 300,
            "rest_elapsed_seconds": 0,
        },
    )
    assert res_end_ok.status_code == 200, res_end_ok.text

    # a) Empty reason -> 422
    res_corr_empty_reason = await client.post(
        f"/api/v1/runs/{run_r_id}/actual-corrections",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_run_revision": 2,
            "self_report_work_seconds": 350,
            "occurred_at": now.isoformat(),
            "reason": "   ",
        },
    )
    assert res_corr_empty_reason.status_code == 422
    assert res_corr_empty_reason.json()["error"]["code"] == "VALIDATION_ERROR"

    # b) occurred_at < ended_at (now - 2m < now - 1m) -> 422
    res_corr_before_end = await client.post(
        f"/api/v1/runs/{run_r_id}/actual-corrections",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_run_revision": 2,
            "self_report_work_seconds": 350,
            "occurred_at": (now - timedelta(minutes=2)).isoformat(),
            "reason": "Báo giờ kết thúc trước ended_at",
        },
    )
    assert res_corr_before_end.status_code == 422
    assert res_corr_before_end.json()["error"]["code"] == "VALIDATION_ERROR"

    # c) self_report_work_seconds > max_allowed (100000 > 86400) -> 422
    res_corr_over_duration = await client.post(
        f"/api/v1/runs/{run_r_id}/actual-corrections",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_run_revision": 2,
            "self_report_work_seconds": 100000,
            "occurred_at": now.isoformat(),
            "reason": "Khai quá thời lượng phiên",
        },
    )
    assert res_corr_over_duration.status_code == 422
    assert res_corr_over_duration.json()["error"]["code"] == "VALIDATION_ERROR"
