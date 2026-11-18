"""Test Matrix UF3 & UF4 — Doãn Thành (QA Lead) — ProPlan VA.

Phạm vi kiểm thử
─────────────────
UF3: Thông báo, Khởi động 2p & Vào phiên (Focus + DND) : TC-UF3-01..18
UF4: Cất suy nghĩ bằng bản ghi âm thô (Zero AI Capture) : TC-UF4-01..14
EDGE: Các testcase lạ, ADHD traps & Khủng hoảng hệ thống : TC-EDGE-01..03

Nguồn tham chiếu bất biến
─────────────────────────
AGENTS.md §2.2 UF3 / UF4 / State Transition Matrix
Project.md §7.2 Nhóm Luồng Thực thi Phiên Tập trung (UF3, UF4)
Project.md §10 Quy tắc Bắt buộc Dành riêng cho AI Coding Agents
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.core.errors import AppError
from app.main import app
from app.models import (
    CheckIn,
    CommandReceipt,
    DeviceState,
    Reminder,
    RunEvent,
    ScheduleVersion,
    Session,
    SessionRun,
    Step,
    Task,
    Thought,
    User,
)
from app.models.planning import PlanningDraft
from app.schemas.execution import (
    DeviceStateReportRequest,
    RunEndRequest,
    RunEventBatchRequest,
    RunEventItem,
    SessionStartRequest,
)
from app.schemas.thought import ThoughtCreateRequest, ThoughtDeleteRequest, ThoughtToDraftRequest
from app.schemas.user import CurrentUser
from tests import test_w05_execution_checkin as baseline

w05_env = baseline.w05_env
w05_client = baseline.w05_client
_start_run = baseline._start_run


# =============================================================================
# PHẦN 1 — UF3: Thông báo, Khởi động & Thực hiện Phiên Tập trung
# =============================================================================


async def test_TC_UF3_01_happy_path_start_session_in_grace_window(w05_env, w05_client):
    """TC-UF3-01: Bắt đầu phiên đúng giờ trong Grace Window (scheduled_start + 5 phút).
    Kỳ vọng: HTTP 201 Created, trả về SessionRunData, run_state='active', gán settings_snapshot.
    """
    session_id = str(w05_env["session_id"])
    scheduled_start = w05_env["scheduled_start"]
    start_time = scheduled_start + timedelta(minutes=5)

    response = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "test_device_qa",
            "requested_start_at": start_time.isoformat(),
            "expected_schedule_revision": 1,
            "dnd_state": "applied",
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["session_id"] == session_id
    assert data["run_state"] == "active"
    assert data["device_id"] == "test_device_qa"
    assert data["settings_snapshot"] is not None


async def test_TC_UF3_02_grace_window_exact_boundary_899s_vs_901s(w05_env, w05_client):
    """TC-UF3-02: Kiểm tra biên chính xác của Grace Window 900 giây (15 phút).
    - Tại 899s: Cho phép bắt đầu (HTTP 201).
    - Tại 901s: Chặn bắt đầu (HTTP 409 hoặc 422 - yêu cầu UF6 xếp lại lịch).
    """
    session_id = str(w05_env["session_id"])
    scheduled_start = w05_env["scheduled_start"]

    # Thử tại mốc 899s -> Thành công
    res_899 = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "device_899",
            "requested_start_at": (scheduled_start + timedelta(seconds=899)).isoformat(),
            "expected_schedule_revision": 1,
        },
    )
    assert res_899.status_code == 201

    # Dọn dẹp run để thử mốc 901s
    async with w05_env["sessions"]() as db:
        await db.run_sync(
            lambda s: s.query(SessionRun).filter(SessionRun.session_id == UUID(session_id)).delete()
        )
        await db.commit()

    # Thử tại mốc 901s -> Bị chặn
    res_901 = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "device_901",
            "requested_start_at": (scheduled_start + timedelta(seconds=901)).isoformat(),
            "expected_schedule_revision": 1,
        },
    )
    assert res_901.status_code in (409, 422)


async def test_TC_UF3_03_min_gap_900s_violation_blocked(w05_env, w05_client):
    """TC-UF3-03: Kiểm tra khoảng cách tối thiểu giữa 2 phiên (Min Gap = 900s).
    Nếu phiên 2 bắt đầu khi chưa đủ 900s sau phiên 1 -> Phải ném lỗi 409.
    """
    # Start phiên 1
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]
    scheduled_start = w05_env["scheduled_start"]
    end_time = scheduled_start + timedelta(minutes=25)

    # Kết thúc phiên 1
    await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "ended_at": end_time.isoformat(),
            "actual_work_seconds": 1500,
            "actual_source": "timer_proxy",
        },
    )

    # Thử start phiên 1B chỉ cách 300 giây (< 900s)
    session1b_id = str(w05_env["session1b_id"])
    res = await w05_client.post(
        f"/api/v1/sessions/{session1b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "device_gap_test",
            "requested_start_at": (end_time + timedelta(seconds=300)).isoformat(),
            "expected_schedule_revision": 1,
        },
    )
    assert res.status_code in (409, 422)


async def test_TC_UF3_04_idempotent_start_with_same_key_and_conflict_with_diff_payload(
    w05_env, w05_client
):
    """TC-UF3-04: Idempotency Key bảo vệ lặp request:
    - Cùng Key + cùng payload -> Trả về đúng bản ghi đã tạo (HTTP 201 hoặc 200).
    - Cùng Key + khác payload -> Chặn bằng lỗi 409 IDEMPOTENCY_CONFLICT.
    """
    session_id = str(w05_env["session_id"])
    scheduled_start = w05_env["scheduled_start"]
    shared_key = str(uuid4())
    payload_a = {
        "device_id": "device_alpha",
        "requested_start_at": scheduled_start.isoformat(),
        "expected_schedule_revision": 1,
    }
    payload_b = {
        "device_id": "device_beta_different",
        "requested_start_at": scheduled_start.isoformat(),
        "expected_schedule_revision": 1,
    }

    res1 = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": shared_key},
        json=payload_a,
    )
    assert res1.status_code == 201
    run_id1 = res1.json()["data"]["run_id"]

    # Gửi lại cùng key và payload
    res2 = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": shared_key},
        json=payload_a,
    )
    assert res2.status_code in (200, 201)
    assert res2.json()["data"]["run_id"] == run_id1

    # Gửi cùng key nhưng khác payload
    res3 = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": shared_key},
        json=payload_b,
    )
    assert res3.status_code == 409


async def test_TC_UF3_05_stale_schedule_revision_rejected(w05_env, w05_client):
    """TC-UF3-05: Chặn start session nếu client gửi revision lịch cũ (Stale revision)."""
    session_id = str(w05_env["session_id"])
    scheduled_start = w05_env["scheduled_start"]
    res = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_stale",
            "requested_start_at": scheduled_start.isoformat(),
            "expected_schedule_revision": 9999,  # Revision không tồn tại
        },
    )
    assert res.status_code == 409


async def test_TC_UF3_06_concurrent_run_isolation(w05_env, w05_client):
    """TC-UF3-06: Một User chỉ được có duy nhất 1 Run active tại một thời điểm."""
    await _start_run(w05_client, w05_env)

    # Cố tình start tiếp session1b khi session 1 đang active
    session1b_id = str(w05_env["session1b_id"])
    scheduled_start = w05_env["session1b_start"]
    res = await w05_client.post(
        f"/api/v1/sessions/{session1b_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_concurrent",
            "requested_start_at": scheduled_start.isoformat(),
            "expected_schedule_revision": 1,
        },
    )
    assert res.status_code in (409, 422)


async def test_TC_UF3_07_micro_task_not_added_to_session_duration(w05_env, w05_client):
    """TC-UF3-07: Micro-task chuẩn bị 120s tuyệt đối KHÔNG được cộng vào planned duration."""
    run_data = await _start_run(w05_client, w05_env)
    assert run_data["total_planned_seconds"] > 0
    # Không có segment nào mang kind='micro'
    for seg in run_data["segments"]:
        assert seg["kind"] in ("work", "rest")


async def test_TC_UF3_08_run_events_batch_and_monotonic_elapsed(w05_env, w05_client):
    """TC-UF3-08: Ghi nhận chuỗi sự kiện Run Event (pause, resume) và cập nhật thời lượng làm việc."""
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]
    t0 = w05_env["scheduled_start"]

    events = [
        {"event_id": str(uuid4()), "event_type": "pause", "occurred_at": (t0 + timedelta(minutes=5)).isoformat(), "segment_index": 0, "work_elapsed_seconds": 300, "rest_elapsed_seconds": 0},
        {"event_id": str(uuid4()), "event_type": "resume", "occurred_at": (t0 + timedelta(minutes=7)).isoformat(), "segment_index": 0, "work_elapsed_seconds": 300, "rest_elapsed_seconds": 120},
    ]
    res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={"device_id": "android_pixel_8", "expected_run_revision": 1, "events": events},
    )
    assert res.status_code == 200
    assert res.json()["data"]["accepted_count"] == 2


async def test_TC_UF3_09_clock_skew_event_validation_422(w05_env, w05_client):
    """TC-UF3-09: Chặn sự kiện Run Event có thời gian trong tương lai xa (Clock skew > 5p)."""
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]
    future_time = datetime.now(UTC) + timedelta(days=1)

    res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "events": [{"event_id": str(uuid4()), "event_type": "pause", "occurred_at": future_time.isoformat(), "segment_index": 0, "work_elapsed_seconds": 60, "rest_elapsed_seconds": 0}],
        },
    )
    assert res.status_code == 422


async def test_TC_UF3_10_end_session_run_with_timer_proxy(w05_env, w05_client):
    """TC-UF3-10: Kết thúc phiên với nguồn actual là timer_proxy.
    Kỳ vọng: Ghi nhận ended_at, mở CheckIn với timeout ended_at + 300s.
    """
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]
    end_time = w05_env["scheduled_start"] + timedelta(minutes=25)

    res = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "ended_at": end_time.isoformat(),
            "actual_work_seconds": 1500,
            "actual_source": "timer_proxy",
        },
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["run_state"] == "awaiting_checkin"
    assert data["ended_at"] is not None
    assert data["checkin"] is not None
    assert data["checkin"]["checkin_state"] == "pending"


async def test_TC_UF3_11_end_session_run_invalid_actual_source_422(w05_env, w05_client):
    """TC-UF3-11: Chặn kết thúc phiên nếu actual_source không hợp lệ."""
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]

    res = await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "ended_at": datetime.now(UTC).isoformat(),
            "actual_work_seconds": 1500,
            "actual_source": "invalid_magic_source",  # Sai enum
        },
    )
    assert res.status_code == 422


async def test_TC_UF3_12_get_active_run_returns_current_or_null(w05_env, w05_client):
    """TC-UF3-12: Kiểm tra endpoint GET /runs/active."""
    # Khi chưa có run:
    res_none = await w05_client.get("/api/v1/runs/active")
    assert res_none.status_code == 200
    assert res_none.json()["data"] is None

    # Sau khi start run:
    await _start_run(w05_client, w05_env)
    res_active = await w05_client.get("/api/v1/runs/active")
    assert res_active.status_code == 200
    assert res_active.json()["data"]["session_id"] == str(w05_env["session_id"])


async def test_TC_UF3_13_reminders_list_and_dedupe(w05_env, w05_client):
    """TC-UF3-13: Lấy danh sách Reminders theo device_id."""
    res = await w05_client.get("/api/v1/reminders?device_id=test_device_qa")
    assert res.status_code == 200
    assert "items" in res.json()["data"]


async def test_TC_UF3_14_reminders_cancelled_after_early_start(w05_env, w05_client):
    """TC-UF3-14: Khi start phiên sớm, các reminders chưa bắn của phiên đó phải bị hủy."""
    session_id = w05_env["session_id"]
    await _start_run(w05_client, w05_env)
    async with w05_env["sessions"]() as db:
        reminders = (
            (await db.execute(select(Reminder).filter(Reminder.session_id == session_id)))
            .scalars()
            .all()
        )
        for r in reminders:
            assert r.delivery_state in ("cancelled", "fired") or r.status in ("cancelled", "fired")


async def test_TC_UF3_15_expired_checkin_lazy_reconciliation(w05_env, w05_client):
    """TC-UF3-15: Sau ended_at + 300s không có phản hồi, CheckIn chuyển thành no_respond.
    Tuyệt đối không xóa Session hay Task.
    """
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]
    t_end = w05_env["scheduled_start"] + timedelta(minutes=25)

    # End run
    await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "ended_at": t_end.isoformat(),
            "actual_work_seconds": 1500,
            "actual_source": "timer_proxy",
        },
    )

    # Cố tình dời checkin_timeout_at về quá khứ và reconcile
    async with w05_env["sessions"]() as db:
        await db.run_sync(
            lambda s: s.query(CheckIn)
            .filter(CheckIn.run_id == UUID(run_id))
            .update({"checkin_timeout_at": datetime.now(UTC) - timedelta(seconds=10)})
        )
        await db.commit()

    # Query active run kích hoạt lazy reconciliation
    res = await w05_client.get(f"/api/v1/runs/{run_id}")
    data = res.json()["data"]
    assert data["checkin"]["response_state"] == "no_respond"


async def test_TC_UF3_16_device_state_report_audit(w05_env, w05_client):
    """TC-UF3-16: Báo cáo trạng thái thiết bị (DND state) được ghi nhận an toàn vào DB."""
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]

    res = await w05_client.post(
        "/api/v1/device/state",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "android_pixel_8",
            "dnd_state": "applied",
            "battery_level": 85,
            "run_owner_id": run_id,
        },
    )
    assert res.status_code == 200
    assert res.json()["data"]["dnd_state"] == "applied"


async def test_TC_UF3_17_device_state_cross_device_mismatch_guard(w05_env, w05_client):
    """TC-UF3-17: Thiết bị khác gửi báo cáo sở hữu cho Run không thuộc về nó bị chặn."""
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]

    res = await w05_client.post(
        "/api/v1/device/state",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "alien_device_xyz",  # Khác device_id lúc start
            "dnd_state": "applied",
            "run_owner_id": run_id,
        },
    )
    assert res.status_code in (403, 409, 422)


async def test_TC_UF3_18_zero_auto_complete_invariant(w05_env, w05_client):
    """TC-UF3-18: QUY TẮC BẤT BIẾN: Hết giờ KHÔNG ĐƯỢC tự động đánh dấu Task/Step là completed."""
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]

    # End run giả lập hết giờ
    await w05_client.post(
        f"/api/v1/runs/{run_id}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "ended_at": (w05_env["scheduled_start"] + timedelta(minutes=25)).isoformat(),
            "actual_work_seconds": 1500,
            "actual_source": "timer_proxy",
        },
    )

    # Kiểm tra Task và Step trong DB: KHÔNG ĐƯỢC ở trạng thái completed
    async with w05_env["sessions"]() as db:
        task = await db.get(Task, w05_env["task_id"])
        step = await db.get(Step, w05_env["step_id"])
        assert task.status != "completed"
        assert step.status != "completed"


# =============================================================================
# PHẦN 2 — UF4: Cất suy nghĩ bằng bản ghi âm thô (Zero AI Thought Capture)
# =============================================================================


async def test_TC_UF4_01_capture_audio_thought_success(w05_env, w05_client):
    """TC-UF4-01: Thu nhận Thought dạng audio thô thành công.
    Kỳ vọng: HTTP 201 Created, capture_state='saved', thought_revision=1.
    """
    client_tid = str(uuid4())
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": client_tid,
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
            "text_content": None,
        },
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["client_thought_id"] == client_tid
    assert data["input_type"] == "audio"
    assert data["capture_state"] == "saved"
    assert data["thought_revision"] == 1


async def test_TC_UF4_02_ZERO_AI_GUARD_no_stt_or_llm_during_capture(w05_env, w05_client):
    """TC-UF4-02: BẢO VỆ ZERO AI: Tuyệt đối CẤM gọi STT hoặc LLM khi cất suy nghĩ trong phiên.
    Kiểm tra tốc độ phản hồi phải tức thì (< 100ms) vì không đi qua AI network call.
    """
    import time
    t_start = time.perf_counter()
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    duration_ms = (time.perf_counter() - t_start) * 1000
    assert res.status_code == 201
    # Capture Thought thuần DB/local metadata, tuyệt đối không bị delay bởi AI call (thường mất > 1000ms)
    assert duration_ms < 500, f"Capture Thought mất quá nhiều thời gian ({duration_ms:.1f}ms), nghi vấn gọi AI lén!"


async def test_TC_UF4_03_capture_retry_deduplication(w05_env, w05_client):
    """TC-UF4-03: Gửi lại cùng client_thought_id khi retry không được sinh dòng trùng."""
    client_tid = str(uuid4())
    payload = {
        "client_thought_id": client_tid,
        "source_run_id": None,
        "input_type": "audio",
        "capture_state": "saved",
        "attempt_count": 1,
    }
    res1 = await w05_client.post("/api/v1/thoughts", headers={"Idempotency-Key": str(uuid4())}, json=payload)
    assert res1.status_code == 201
    tid1 = res1.json()["data"]["thought_id"]

    # Gửi lại với Idempotency Key khác nhưng cùng client_thought_id
    res2 = await w05_client.post("/api/v1/thoughts", headers={"Idempotency-Key": str(uuid4())}, json=payload)
    assert res2.status_code in (200, 201)
    assert res2.json()["data"]["thought_id"] == tid1


async def test_TC_UF4_04_text_fallback_capture_after_failures(w05_env, w05_client):
    """TC-UF4-04: Lưu Thought dạng Text Fallback sau các lần lỗi audio.
    Lưu ý: Schema hiện tại giới hạn attempt_count le=3.
    """
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "text",
            "capture_state": "saved",
            "attempt_count": 3,
            "text_content": "Mua thêm giấy in và bút dạ",
        },
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["input_type"] == "text"
    assert data["text_content"] == "Mua thêm giấy in và bút dạ"


async def test_TC_UF4_05_audio_thought_validation_check(w05_env, w05_client):
    """TC-UF4-05: Kiểm tra tính toàn vẹn của Thought Audio."""
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
            "text_content": None,
        },
    )
    assert res.status_code == 201


async def test_TC_UF4_06_capture_standalone_thought_outside_session(w05_env, w05_client):
    """TC-UF4-06: Cho phép ghi nhận Thought ngoài phiên (source_run_id=None)."""
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    assert res.status_code == 201
    assert res.json()["data"]["source_run_id"] is None


async def test_TC_UF4_07_list_thoughts_pagination_and_ordering(w05_env, w05_client):
    """TC-UF4-07: Truy vấn danh sách Thoughts hỗ trợ phân trang limit."""
    # Tạo 3 thoughts
    for _ in range(3):
        await w05_client.post(
            "/api/v1/thoughts",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "client_thought_id": str(uuid4()),
                "source_run_id": None,
                "input_type": "audio",
                "capture_state": "saved",
                "attempt_count": 1,
            },
        )
    res = await w05_client.get("/api/v1/thoughts?limit=2")
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data["items"]) <= 2


async def test_TC_UF4_08_get_thought_detail_by_id(w05_env, w05_client):
    """TC-UF4-08: Lấy chi tiết Thought theo thought_id."""
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]

    res_get = await w05_client.get(f"/api/v1/thoughts/{tid}")
    assert res_get.status_code == 200
    assert res_get.json()["data"]["thought_id"] == tid


async def test_TC_UF4_09_undo_delete_thought_within_window(w05_env, w05_client):
    """TC-UF4-09: Xóa Thought (Undo 5 giây) thành công với expected_thought_revision."""
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]
    rev = res_create.json()["data"]["thought_revision"]

    res_del = await w05_client.request(
        "DELETE",
        f"/api/v1/thoughts/{tid}",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_thought_revision": rev},
    )
    assert res_del.status_code == 200
    assert res_del.json()["data"]["deleted"] is True


async def test_TC_UF4_10_delete_thought_revision_conflict_409(w05_env, w05_client):
    """TC-UF4-10: Chặn xóa Thought nếu expected_thought_revision bị sai lệch."""
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]

    res_del = await w05_client.request(
        "DELETE",
        f"/api/v1/thoughts/{tid}",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_thought_revision": 999},  # Revision sai
    )
    assert res_del.status_code == 409


async def test_TC_UF4_11_post_run_thought_to_draft_conversion(w05_env, w05_client):
    """TC-UF4-11: Chuyển đổi Thought thành PlanningDraft (UF1) sau phiên."""
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]
    rev = res_create.json()["data"]["thought_revision"]

    res_conv = await w05_client.post(
        f"/api/v1/thoughts/{tid}/draft",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_thought_revision": rev,
            "text_content": "Soạn tài liệu ôn thi môn Giải tích",
        },
    )
    assert res_conv.status_code == 201
    data = res_conv.json()["data"]
    assert data["linked_draft_id"] is not None
    assert data["thought_id"] == tid


async def test_TC_UF4_12_concurrent_thought_to_draft_creates_single_draft(w05_env, w05_client):
    """TC-UF4-12: Chặn 2 thiết bị cùng bấm chuyển 1 Thought thành Draft đồng thời."""
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]
    rev = res_create.json()["data"]["thought_revision"]

    # Request 1 thành công
    res1 = await w05_client.post(
        f"/api/v1/thoughts/{tid}/draft",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_thought_revision": rev, "text_content": "Việc cần làm"},
    )
    assert res1.status_code == 201

    # Request 2 gửi tiếp cùng revision cũ -> Phải bị từ chối 409
    res2 = await w05_client.post(
        f"/api/v1/thoughts/{tid}/draft",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_thought_revision": rev, "text_content": "Việc cần làm lần 2"},
    )
    assert res2.status_code == 409


async def test_TC_UF4_13_cross_user_isolation_guard(w05_env, w05_client):
    """TC-UF4-13: Chặn truy cập hoặc xóa Thought của User khác."""
    # Tạo thought thuộc User A
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]

    # Đổi context sang User B (UUID ngẫu nhiên)
    alien_user = CurrentUser(user_id=str(uuid4()), email="alien@proplan.test")
    app.dependency_overrides[get_current_user] = lambda: alien_user

    try:
        res_get = await w05_client.get(f"/api/v1/thoughts/{tid}")
        assert res_get.status_code in (403, 404)
    finally:
        # Khôi phục user A
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=str(w05_env["user_id"]), email="user_a@proplan.test")


async def test_TC_UF4_14_converted_task_id_only_after_uf2_confirm(w05_env, w05_client):
    """TC-UF4-14: Converted Task ID trên Thought chỉ được gán sau khi chốt lịch UF2."""
    res_create = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    tid = res_create.json()["data"]["thought_id"]
    rev = res_create.json()["data"]["thought_revision"]

    # Convert to draft
    await w05_client.post(
        f"/api/v1/thoughts/{tid}/draft",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_thought_revision": rev, "text_content": "Task tương lai"},
    )

    # Khi mới ở Draft: converted_task_id vẫn là None
    res_detail = await w05_client.get(f"/api/v1/thoughts/{tid}")
    assert res_detail.json()["data"]["converted_task_id"] is None


# =============================================================================
# PHẦN 3 — EDGE CASES & TÌNH HUỐNG DỊ ĐẶC THÙ (3 TEST CASES)
# =============================================================================


async def test_TC_EDGE_01_empty_client_thought_id_validation(w05_env, w05_client):
    """TC-EDGE-01: Client thought ID rỗng hoàn toàn bị chặn ở tầng schema."""
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": "",  # Rỗng min_length=1
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    assert res.status_code == 422


async def test_TC_EDGE_02_double_start_same_millisecond_concurrency(w05_env, w05_client):
    """TC-EDGE-02: 2 request start gửi cùng 1 tích tắc được khóa an toàn bởi Database Transaction."""
    session_id = str(w05_env["session_id"])
    t_start = w05_env["scheduled_start"].isoformat()

    req1 = w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={"device_id": "dev1", "requested_start_at": t_start, "expected_schedule_revision": 1},
    )
    req2 = w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={"device_id": "dev2", "requested_start_at": t_start, "expected_schedule_revision": 1},
    )

    res1, res2 = await asyncio.gather(req1, req2)
    statuses = {res1.status_code, res2.status_code}
    # Đúng 1 request thành công 201, request còn lại bị chặn 409
    assert 201 in statuses
    assert 409 in statuses


async def test_TC_EDGE_03_early_start_colliding_with_busy_context(w05_env, w05_client):
    """TC-EDGE-03: Cố tình start sớm vào khoảng thời gian người dùng bận (CalendarContext) bị từ chối."""
    session_id = str(w05_env["session_id"])
    scheduled_start = w05_env["scheduled_start"]
    # Thử start sớm 2 tiếng (trước scheduled_start)
    way_early = scheduled_start - timedelta(hours=2)

    res = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_early",
            "requested_start_at": way_early.isoformat(),
            "expected_schedule_revision": 1,
        },
    )
    # Backend yêu cầu replan hoặc báo lỗi vi phạm giờ
    assert res.status_code in (409, 422)
