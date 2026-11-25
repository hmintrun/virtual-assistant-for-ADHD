"""Suite kiểm thử nâng cấp BE-UF4 (2026-11-25): created_at, lọc failed, CAS delete, telemetry Zero-PII/Zero-AI và hồi quy guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.api.v1 import voice
from app.models import AuditEvent, PlanningDraft, Session, SessionRun, Step, Task, Thought, User
from app.repositories import thought_repository
from app.schemas.thought import ThoughtData
from app.services import ai_service, device_service
from tests import test_w05_execution_checkin as baseline
from tests import test_w07_thought_lifecycle as w07

w05_env = baseline.w05_env
w05_client = baseline.w05_client
_start_run = baseline._start_run


async def _post_telemetry(
    client,
    *,
    device_id: str = "android_pixel_8",
    key: str | None = None,
    **overrides,
):
    body = {
        "event_id": str(uuid4()),
        "event_type": "uf4_audio_capture_failed",
        "error_code": "MIC_PERMISSION_DENIED",
        "attempt_count": 1,
        "source_run_id": None,
        "occurred_at": datetime.now(UTC).isoformat(),
        "app_version": "1.2.0",
        "platform_os": "android",
        **overrides,
    }
    return await client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        headers={"Idempotency-Key": key or str(uuid4())},
        json=body,
    )


async def test_uf4_01_thought_created_at_and_backward_compat(w05_client, w05_env):
    created = await w07.capture(w05_client, input_type="audio", capture_state="saved")
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["created_at"] is not None
    parsed_created = datetime.fromisoformat(data["created_at"])
    assert parsed_created.utcoffset() is not None

    detail = await w05_client.get(f"/api/v1/thoughts/{data['thought_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["created_at"] == data["created_at"]

    listing = await w05_client.get("/api/v1/thoughts")
    assert listing.status_code == 200, listing.text
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["created_at"] == data["created_at"]

    legacy_dto = ThoughtData(
        thought_id=str(uuid4()),
        thought_revision=1,
        client_thought_id="legacy-no-created-at",
        source_run_id=None,
        input_type="audio",
        capture_state="saved",
        attempt_count=1,
        text_content=None,
    )
    assert legacy_dto.created_at is None


async def test_uf4_02_list_thoughts_filters_failed_preserves_2_reads_and_rejects_failed_cursor(
    w05_client, w05_env
):
    saved_1 = (await w07.capture(w05_client, capture_state="saved")).json()["data"]
    recording_1 = (await w07.capture(w05_client, capture_state="recording")).json()["data"]
    failed_1 = (await w07.capture(w05_client, capture_state="failed")).json()["data"]
    saved_2 = (await w07.capture(w05_client, capture_state="saved")).json()["data"]
    failed_2 = (await w07.capture(w05_client, capture_state="failed")).json()["data"]

    reads: list[str] = []

    def record_select(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement)

    event.listen(w05_env["engine"].sync_engine, "before_cursor_execute", record_select)
    try:
        from app.services import thought_service

        reads.clear()
        async with w05_env["sessions"]() as db:
            page = await thought_service.list_thoughts(
                db, user_id=str(w05_env["user_id"]), limit=20
            )
            assert {item.thought_id for item in page.items} == {
                saved_1["thought_id"],
                recording_1["thought_id"],
                saved_2["thought_id"],
            }
        assert len(reads) == 2
    finally:
        event.remove(w05_env["engine"].sync_engine, "before_cursor_execute", record_select)

    listing = await w05_client.get("/api/v1/thoughts")
    assert listing.status_code == 200
    listed_ids = {item["thought_id"] for item in listing.json()["data"]["items"]}
    assert listed_ids == {saved_1["thought_id"], recording_1["thought_id"], saved_2["thought_id"]}
    assert failed_1["thought_id"] not in listed_ids
    assert failed_2["thought_id"] not in listed_ids

    bad_cursor = await w05_client.get("/api/v1/thoughts", params={"cursor": failed_1["thought_id"]})
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_uf4_03_delete_thought_conflict_includes_current_revisions_and_cas_miss(
    w05_client, w05_env, monkeypatch
):
    # 1. Linked thought -> 409 THOUGHT_ALREADY_LINKED kèm current_revisions.thought_revision
    linked_row = (await w07.capture(w05_client)).json()["data"]
    conv = await w07.convert(w05_client, linked_row)
    assert conv.status_code == 201
    del_linked = await w07.delete(w05_client, linked_row, revision=2)
    assert del_linked.status_code == 409
    assert del_linked.json()["error"]["code"] == "THOUGHT_ALREADY_LINKED"
    assert del_linked.json()["error"]["current_revisions"] == {"thought_revision": 2}

    # 2. Stale revision -> 409 REVISION_CONFLICT kèm current_revisions.thought_revision
    row = (await w07.capture(w05_client)).json()["data"]
    del_stale = await w07.delete(w05_client, row, revision=3)
    assert del_stale.status_code == 409
    assert del_stale.json()["error"]["code"] == "REVISION_CONFLICT"
    assert del_stale.json()["error"]["current_revisions"] == {"thought_revision": 1}

    # 3a. CAS miss: row bị nâng revision đồng thời -> 409 REVISION_CONFLICT với latest.revision
    async def cas_miss_bump(db, user_id, thought_id, expected_revision):
        db_row = await db.get(Thought, thought_id)
        assert db_row is not None
        db_row.revision = 5
        await db.flush()
        return False

    monkeypatch.setattr(thought_repository, "delete_thought", cas_miss_bump)
    del_cas_rev = await w07.delete(w05_client, row, revision=1)
    assert del_cas_rev.status_code == 409
    assert del_cas_rev.json()["error"]["code"] == "REVISION_CONFLICT"
    assert del_cas_rev.json()["error"]["current_revisions"] == {"thought_revision": 5}

    # 3b. CAS miss: row vừa bị gắn draft đồng thời -> 409 THOUGHT_ALREADY_LINKED với latest.revision
    async def cas_miss_linked(db, user_id, thought_id, expected_revision):
        db_row = await db.get(Thought, thought_id)
        assert db_row is not None
        db_row.revision = 6
        db.add(
            PlanningDraft(
                id=uuid4(),
                user_id=user_id,
                client_draft_id=f"race-draft-{thought_id}",
                stage="collecting",
                input_source="thought",
                raw_input="Race",
                summary={},
                source_thought_id=thought_id,
            )
        )
        await db.flush()
        return False

    monkeypatch.setattr(thought_repository, "delete_thought", cas_miss_linked)
    del_cas_linked = await w07.delete(w05_client, row, revision=1)
    assert del_cas_linked.status_code == 409
    assert del_cas_linked.json()["error"]["code"] == "THOUGHT_ALREADY_LINKED"
    assert del_cas_linked.json()["error"]["current_revisions"] == {"thought_revision": 6}

    # 3c. CAS miss: row vừa bị xóa bởi request khác -> 404 RESOURCE_NOT_FOUND
    row_gone = (await w07.capture(w05_client)).json()["data"]

    async def cas_miss_deleted(db, user_id, thought_id, expected_revision):
        db_row = await db.get(Thought, thought_id)
        assert db_row is not None
        await db.delete(db_row)
        await db.flush()
        return False

    monkeypatch.setattr(thought_repository, "delete_thought", cas_miss_deleted)
    del_cas_gone = await w07.delete(w05_client, row_gone, revision=1)
    assert del_cas_gone.status_code == 404
    assert del_cas_gone.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_uf4_04_telemetry_happy_path_writes_audit_only(
    w05_client, w05_env, simulated_clock
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)
    run = await _start_run(w05_client, w05_env)

    event_id = f"evt-uf4-{uuid4()}"
    res = await _post_telemetry(
        w05_client,
        device_id="device-uf4",
        event_id=event_id,
        event_type="uf4_audio_capture_failed",
        error_code="RECORDER_IO_ERROR",
        attempt_count=2,
        source_run_id=run["run_id"],
        occurred_at=t0.isoformat(),
        app_version="2.0.1",
        platform_os="android",
    )
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["device_id"] == "device-uf4"
    assert data["event_id"] == event_id
    assert data["event_type"] == "uf4_audio_capture_failed"
    assert data["error_code"] == "RECORDER_IO_ERROR"
    assert datetime.fromisoformat(data["received_at"]) == t0

    async with w05_env["sessions"]() as db:
        assert await db.scalar(select(func.count()).select_from(Thought)) == 0
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        audit = await db.scalar(select(AuditEvent).where(AuditEvent.id == UUID(data["audit_event_id"])))
        assert audit is not None
        assert audit.resource_ref["device_id"] == "device-uf4"
        assert audit.resource_ref["error_code"] == "RECORDER_IO_ERROR"
        assert audit.resource_ref["attempt_count"] == 2
        assert audit.resource_ref["source_run_id"] == run["run_id"]
        run_row = await db.get(SessionRun, UUID(run["run_id"]))
        assert run_row is not None
        assert run_row.revision == run["run_revision"]
        assert run_row.run_state == "running"


async def test_uf4_05_telemetry_zero_ai_no_stt_or_llm_calls(
    w05_client, w05_env, simulated_clock, monkeypatch
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)

    def forbidden(*args, **kwargs):
        pytest.fail("Telemetry must not call STT or LLM")

    monkeypatch.setattr(voice, "get_voice_service", forbidden)
    monkeypatch.setattr("app.services.voice_service.get_voice_service", forbidden)
    for method in (
        "extract_task_intent",
        "decompose_task",
        "adjust_candidate_by_nl",
        "adjust_plan_by_nl",
        "generate_reschedule_empathy",
    ):
        monkeypatch.setattr(ai_service.AIService, method, forbidden)

    res = await _post_telemetry(
        w05_client,
        event_type="uf3_alarm_drift",
        error_code="ALARM_DRIFT_HIGH",
        attempt_count=None,
        occurred_at=t0.isoformat(),
    )
    assert res.status_code == 201, res.text


async def test_uf4_06_telemetry_idempotency_replay_after_dispose_and_conflict(
    w05_client, w05_env, simulated_clock
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)
    event_id = f"evt-idem-{uuid4()}"
    key_1 = str(uuid4())

    first = await _post_telemetry(
        w05_client,
        device_id="device-a",
        key=key_1,
        event_id=event_id,
        event_type="uf4_audio_capture_failed",
        error_code="MIC_BUSY",
        attempt_count=1,
        occurred_at=t0.isoformat(),
    )
    assert first.status_code == 201, first.text
    first_data = first.json()["data"]

    simulated_clock.advance(timedelta(seconds=90))
    await w05_env["engine"].dispose()

    # Replay đúng Idempotency-Key
    replay_same_key = await _post_telemetry(
        w05_client,
        device_id="device-a",
        key=key_1,
        event_id=event_id,
        event_type="uf4_audio_capture_failed",
        error_code="MIC_BUSY",
        attempt_count=1,
        occurred_at=t0.isoformat(),
    )
    assert replay_same_key.status_code == 201
    assert replay_same_key.json()["data"] == first_data

    # Replay khác Idempotency-Key nhưng cùng event_id & nội dung khớp (kể cả khác timezone offset cùng instant) -> giữ nguyên received_at
    replay_new_key = await _post_telemetry(
        w05_client,
        device_id="device-a",
        key=str(uuid4()),
        event_id=event_id,
        event_type="uf4_audio_capture_failed",
        error_code="MIC_BUSY",
        attempt_count=1,
        occurred_at=t0.astimezone(timezone(timedelta(hours=7))).isoformat(),
    )
    assert replay_new_key.status_code == 201, replay_new_key.text
    assert replay_new_key.json()["data"]["audit_event_id"] == first_data["audit_event_id"]
    assert replay_new_key.json()["data"]["received_at"] == first_data["received_at"]

    run = await _start_run(w05_client, w05_env)

    # Cùng event_id nhưng thay đổi bất kỳ trường nào trong payload -> 409 CLIENT_EVENT_CONFLICT
    conflict_cases = [
        {"error_code": "STORAGE_FULL"},
        {"device_id": "device-b"},
        {"attempt_count": 2},
        {"source_run_id": run["run_id"]},
        {"event_type": "uf3_alarm_drift"},
        {"occurred_at": (t0 + timedelta(seconds=5)).isoformat()},
        {"app_version": "9.9.9"},
        {"platform_os": "ios"},
    ]
    for override in conflict_cases:
        req = {
            "device_id": "device-a",
            "key": str(uuid4()),
            "event_id": event_id,
            "event_type": "uf4_audio_capture_failed",
            "error_code": "MIC_BUSY",
            "attempt_count": 1,
            "occurred_at": t0.isoformat(),
            **override,
        }
        conflict = await _post_telemetry(w05_client, **req)
        assert conflict.status_code == 409, (override, conflict.text)
        assert conflict.json()["error"]["code"] == "CLIENT_EVENT_CONFLICT"


async def test_uf4_07_telemetry_validation_clock_skew_error_code_and_zero_pii(
    w05_client, w05_env, simulated_clock
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)

    # Naive timestamp (không múi giờ) -> 422 VALIDATION_ERROR
    naive_res = await _post_telemetry(w05_client, occurred_at="2026-10-30T09:00:00")
    assert naive_res.status_code == 422
    assert naive_res.json()["error"]["code"] == "VALIDATION_ERROR"

    # Clock skew > 300s -> 422 VALIDATION_ERROR
    skew_bad = await _post_telemetry(
        w05_client, occurred_at=(t0 + timedelta(seconds=301)).isoformat()
    )
    assert skew_bad.status_code == 422
    assert skew_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # Clock skew đúng biên 300s -> 201
    skew_edge = await _post_telemetry(
        w05_client, occurred_at=(t0 + timedelta(seconds=300)).isoformat()
    )
    assert skew_edge.status_code == 201, skew_edge.text

    # Sai pattern error_code -> 422
    for bad_code in ("lowercase_err", "A", "BAD-CODE"):
        res_bad_code = await _post_telemetry(
            w05_client, error_code=bad_code, occurred_at=t0.isoformat()
        )
        assert res_bad_code.status_code == 422

    # Payload chứa PII (extra="forbid") -> 422
    for pii_field in (
        {"audio_path": "/tmp/secret.m4a"},
        {"local_audio_path": "/data/user/0/secret.m4a"},
        {"text_content": "Nội dung riêng tư"},
    ):
        res_pii = await _post_telemetry(w05_client, occurred_at=t0.isoformat(), **pii_field)
        assert res_pii.status_code == 422

    # event_id chỉ chứa khoảng trắng -> 422
    res_blank = await _post_telemetry(w05_client, event_id="   ", occurred_at=t0.isoformat())
    assert res_blank.status_code == 422


async def test_uf4_08_telemetry_source_run_id_ownership_and_ended_offline_replay(
    w05_client, w05_env, simulated_clock
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)

    # source_run_id không tồn tại / sai định dạng -> 404
    missing = await _post_telemetry(
        w05_client, source_run_id=str(uuid4()), occurred_at=t0.isoformat()
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    invalid = await _post_telemetry(
        w05_client, source_run_id="not-a-uuid", occurred_at=t0.isoformat()
    )
    assert invalid.status_code == 404

    # source_run_id của user khác -> 404
    other_run_id = uuid4()
    async with w05_env["sessions"]() as db:
        other_user = User(email="other-uf4@example.test", display_name="Other")
        db.add(other_user)
        await db.flush()
        other_task = Task(
            user_id=other_user.id,
            title="Other task",
            time_constraint={"kind": "window"},
        )
        db.add(other_task)
        await db.flush()
        other_step = Step(
            user_id=other_user.id,
            task_id=other_task.id,
            title="Other step",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user",
        )
        db.add(other_step)
        await db.flush()
        other_session = Session(
            user_id=other_user.id,
            step_id=other_step.id,
            scheduled_start=t0,
            scheduled_end=t0 + timedelta(minutes=25),
            work_seconds=1500,
            rest_seconds=0,
            duration_seconds=1500,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            schedule_revision=1,
            status="scheduled",
        )
        db.add(other_session)
        await db.flush()
        db.add(
            SessionRun(
                id=other_run_id,
                user_id=other_user.id,
                session_id=other_session.id,
                device_id="other-dev",
                started_at=t0,
                run_state="running",
                active_phase="work",
                settings_snapshot={"effective_revision": 1},
            )
        )
        await db.commit()

    foreign = await _post_telemetry(
        w05_client, source_run_id=str(other_run_id), occurred_at=t0.isoformat()
    )
    assert foreign.status_code == 404

    # Telemetry cho phiên đã ended_at (offline replay) -> vẫn 201
    run = await _start_run(w05_client, w05_env)
    simulated_clock.advance(timedelta(seconds=30))
    await w07.end_run(w05_client, w05_env, run)

    offline_replay = await _post_telemetry(
        w05_client,
        source_run_id=run["run_id"],
        occurred_at=(t0 + timedelta(seconds=10)).isoformat(),
    )
    assert offline_replay.status_code == 201, offline_replay.text


async def test_uf4_09_e2e_three_audio_failures_telemetry_then_text_fallback_attempt_4(
    w05_client, w05_env, simulated_clock
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)
    run = await _start_run(w05_client, w05_env)

    for attempt in (1, 2, 3):
        tel_res = await _post_telemetry(
            w05_client,
            device_id="android_pixel_8",
            event_id=f"uf4-fail-{attempt}-{uuid4()}",
            event_type="uf4_audio_capture_failed",
            error_code="AUDIO_ENCODE_FAILED",
            attempt_count=attempt,
            source_run_id=run["run_id"],
            occurred_at=t0.isoformat(),
        )
        assert tel_res.status_code == 201, tel_res.text

    text_res = await w07.capture(
        w05_client,
        source_run_id=run["run_id"],
        input_type="text",
        capture_state="saved",
        attempt_count=4,
        text_content="Nhớ gửi mail báo cáo tiến độ",
    )
    assert text_res.status_code == 201, text_res.text
    created = text_res.json()["data"]
    assert created["input_type"] == "text"
    assert created["attempt_count"] == 4

    listing = await w05_client.get("/api/v1/thoughts")
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["thought_id"] == created["thought_id"]
    assert items[0]["text_content"] == "Nhớ gửi mail báo cáo tiến độ"

    async with w05_env["sessions"]() as db:
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 3
        assert await db.scalar(select(func.count()).select_from(Thought)) == 1


async def test_uf4_10_zero_ai_guard_regression_409_session_still_running(
    w05_client, w05_env, simulated_clock, monkeypatch
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)

    def forbidden(*args, **kwargs):
        pytest.fail("Zero-AI guard must block before calling STT")

    monkeypatch.setattr(voice, "get_voice_service", forbidden)

    thought_res = await w07.capture(
        w05_client, source_run_id=None, input_type="audio", capture_state="saved"
    )
    assert thought_res.status_code == 201
    row = thought_res.json()["data"]

    # Mở live run
    await _start_run(w05_client, w05_env)

    stt_res = await w07.transcribe(w05_client, row["thought_id"], str(uuid4()))
    assert stt_res.status_code == 409
    assert stt_res.json()["error"]["code"] == "SESSION_STILL_RUNNING"

    draft_res = await w07.convert(w05_client, row, text="Chuyển nháp trong phiên")
    assert draft_res.status_code == 409
    assert draft_res.json()["error"]["code"] == "SESSION_STILL_RUNNING"


async def test_uf4_11_telemetry_integrity_error_race_recovery(
    w05_client, w05_env, simulated_clock, monkeypatch
):
    t0 = w05_env["scheduled_start"]
    simulated_clock.set(t0)
    orig_resolve = device_service._resolve_existing_telemetry

    async def _run_race(event_id: str, winning_error_code: str):
        calls = 0

        async def racing_resolve(db, owner, device_id, payload, normalized_source_run_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                # Mô phỏng một bản ghi (owner, event_id) đã tồn tại khi flush() chạm IntegrityError
                db.add(
                    AuditEvent(
                        user_id=owner,
                        event_id=event_id,
                        event_type="uf4_audio_capture_failed",
                        resource_ref={
                            "device_id": "device-race",
                            "error_code": winning_error_code,
                            "attempt_count": 1,
                            "source_run_id": None,
                            "app_version": "1.2.0",
                            "platform_os": "android",
                        },
                        occurred_at=t0,
                        received_at=t0,
                        command_id="winning-cmd",
                    )
                )
                await db.flush()
                return None
            return await orig_resolve(db, owner, device_id, payload, normalized_source_run_id)

        monkeypatch.setattr(device_service, "_resolve_existing_telemetry", racing_resolve)
        res = await _post_telemetry(
            w05_client,
            device_id="device-race",
            event_id=event_id,
            event_type="uf4_audio_capture_failed",
            error_code="MIC_PERMISSION_DENIED",
            attempt_count=1,
            occurred_at=t0.isoformat(),
        )
        assert calls == 2
        return res

    # Nhánh 1: request song song ghi cùng payload -> IntegrityError được bắt, trả 201 idempotent
    evt_match = f"evt-race-ok-{uuid4()}"
    res_ok = await _run_race(evt_match, "MIC_PERMISSION_DENIED")
    assert res_ok.status_code == 201, res_ok.text
    assert res_ok.json()["data"]["event_id"] == evt_match

    # Nhánh 2: request song song ghi khác payload -> IntegrityError được bắt, trả 409 CLIENT_EVENT_CONFLICT
    evt_conflict = f"evt-race-conflict-{uuid4()}"
    res_conflict = await _run_race(evt_conflict, "STORAGE_FULL")
    assert res_conflict.status_code == 409, res_conflict.text
    assert res_conflict.json()["error"]["code"] == "CLIENT_EVENT_CONFLICT"
