"""Bộ Test Tái Hiện Các Trường Hợp Thất Bại & Lỗi Nghiệp Vụ UF3 & UF4.

Doãn Thành (QA Lead) — ProPlan VA.
Tài liệu tham chiếu: Project.md §7.2, §10, AGENTS.md §2.2.

Mục đích:
- Tái hiện chính xác các test case thất bại (Failing / Bug-reproducing Test Cases).
- Phân biệt rõ giữa:
  1. Lỗi code / Lỗi Schema Backend (Cần Dev sửa).
  2. Bẫy nghiệp vụ (Negative tests & Guardrails bắt buộc phải fail để bảo vệ hệ thống).
  3. Lỗ hổng kiểm tra dữ liệu đầu vào (Security & Contract Gaps).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.api.deps import get_current_user
from app.main import app
from app.models import CheckIn, SessionRun, Task, Thought
from app.schemas.user import CurrentUser
from tests import test_w05_execution_checkin as baseline

w05_env = baseline.w05_env
w05_client = baseline.w05_client
_start_run = baseline._start_run


# =============================================================================
# NHÓM 1: LỖI SCHEMA & CONTRACT TRÊN BACKEND (BUGS CẦN BÁO DEV FIX GẤP)
# =============================================================================


async def test_BUG_UF4_01_text_fallback_attempt_count_4_blocked_by_schema(w05_env, w05_client):
    """[BUG-UF4-01] Text Fallback lần thứ 4 bị schema chặn 422 Unprocessable Entity.
    
    Nghiệp vụ Spec (Project.md §7.2):
    - Khi ghi âm bị lỗi tối đa 3 lần, lần thứ 4 hệ thống phải mở ô Text Fallback để user nhập chữ.
    
    Thực tế Code (ThoughtCreateRequest):
    - Trường `attempt_count: int = Field(ge=1, le=3)` đang chặn cứng `le=3`.
    - Khi client gửi `attempt_count=4` với `input_type="text"`, server ném 422 thay vì 201!
    """
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "text",
            "capture_state": "saved",
            "attempt_count": 4,  # Lần 4 sau 3 lần nói thất bại
            "text_content": "Mua thêm giấy in và bút nhớ",
        },
    )
    # TÁI HIỆN BUG: Backend hiện tại trả về 422 vì schema le=3
    assert res.status_code == 422
    err_body = res.json()["error"]
    assert err_body["code"] == "VALIDATION_ERROR"
    field_paths = [f["field_path"] for f in err_body["field_errors"]]
    assert "attempt_count" in field_paths
    # Ghi chú QA: Dev cần sửa `le=3` thành `le=5` hoặc nới lỏng cho input_type="text"


async def test_BUG_UF4_02_audio_thought_allows_text_content_security_flaw(w05_env, w05_client):
    """[BUG-UF4-02] Lỗ hổng Zero AI: Thought Audio vẫn cho phép gửi kèm text_content.
    
    Nghiệp vụ Spec (Project.md §10.2):
    - Trong phiên tập trung, CẤM gọi STT, CẤM gọi LLM. Chỉ lưu raw audio.
    - Input type 'audio' bắt buộc text_content phải là None.
    
    Thực tế Code:
    - Backend hiện không có validator chặn trường hợp `input_type="audio"` nhưng lại có `text_content`.
    - Request gửi kèm text vẫn được trả về 201 Created thay vì ném lỗi 422!
    """
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
            "text_content": "Văn bản này lẽ ra phải bị cấm khi gửi audio!",
        },
    )
    # TÁI HIỆN BUG: Server chấp nhận 201 thay vì chặn 422
    assert res.status_code == 201
    assert res.json()["data"]["text_content"] is not None
    # Ghi chú QA: Dev cần bổ sung @model_validator chặn text_content != None khi input_type == 'audio'


async def test_BUG_UF4_03_whitespace_only_client_thought_id_accepted(w05_env, w05_client):
    """[BUG-UF4-03] Client Thought ID toàn khoảng trắng vẫn được nhận 201 Created.
    
    Nghiệp vụ Spec:
    - `client_thought_id` là UUID / định danh ổn định của client, không được là chuỗi rỗng/vô nghĩa.
    
    Thực tế Code:
    - `ThoughtCreateRequest` chỉ khai báo `min_length=1` mà không strip whitespace.
    - Chuỗi "   " (3 dấu cách) có length = 3 nên vượt qua validation và lưu vào DB!
    """
    res = await w05_client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "client_thought_id": "   ",  # Chỉ toàn dấu cách
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
        },
    )
    # TÁI HIỆN BUG: Server trả về 201 Created thay vì chặn 422 Unprocessable Entity
    assert res.status_code == 201
    # Ghi chú QA: Dev cần thêm @field_validator strip whitespace


async def test_BUG_UF3_01_extra_fields_in_start_session_blocked_422(w05_env, w05_client):
    """[BUG-UF3-01] Gửi kèm dnd_state trong start session bị ném lỗi 422 do extra='forbid'.
    
    Nghiệp vụ Spec:
    - Mobile khi bấm Start thường kèm trạng thái DND để tối ưu số lần gọi API.
    
    Thực tế Code:
    - `SessionStartRequest` cấu hình `extra="forbid"`. Bất kỳ trường ngoài nào (như `dnd_state`)
      đều khiến API bị từ chối với lỗi 422.
    """
    session_id = str(w05_env["session_id"])
    res = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "android_pixel",
            "requested_start_at": w05_env["scheduled_start"].isoformat(),
            "expected_schedule_revision": 1,
            "dnd_state": "applied",  # Trường bị cấm trong SessionStartRequest
        },
    )
    # TÁI HIỆN BUG/RÀNG BUỘC: Server ném 422
    assert res.status_code == 422
    err_body = res.json()["error"]
    assert err_body["code"] == "VALIDATION_ERROR"
    field_paths = [f["field_path"] for f in err_body["field_errors"]]
    assert "dnd_state" in field_paths
    # Ghi chú QA: Báo Mobile Dev gọi riêng PUT /api/v1/devices/{device_id}/state


# =============================================================================
# NHÓM 2: CÁC BẪY NGHIỆP VỤ & LỖI BẮT BUỘC HỆ THỐNG PHẢI CHẶN (GUARDRAILS)
# =============================================================================


async def test_FAIL_UF3_02_late_start_in_grace_window_fails_due_to_gap_collision(
    w05_env, w05_client
):
    """[FAIL-UF3-02] Bắt đầu muộn ở giây 899 (trong Grace 15p) vẫn bị CHẶN 409 Conflict.
    
    Bẫy nghiệp vụ cực hiểm:
    - Session 1: 09:00 -> 09:30 (thời lượng 30 phút).
    - Session 2 (1B): bắt đầu lúc 09:50 (cách phiên 1 đúng 20 phút = 1200s).
    - Nếu Session 1 bắt đầu muộn lúc 09:14:59 (899s sau scheduled_start):
      Thời gian kết thúc mới sẽ là: 09:14:59 + 30m = 09:44:59.
      Khoảng cách với Session 2 lúc này chỉ còn: 09:50:00 - 09:44:59 = 301s (< 900s Min Gap).
    
    Kết quả bắt buộc:
    - Dù nằm trong 15p Grace Window, Backend VẪN PHẢI NÉM LỖI 409 Conflict
      để bảo vệ khoảng cách nghỉ 15 phút giữa 2 phiên!
    """
    session_id = str(w05_env["session_id"])
    scheduled_start = w05_env["scheduled_start"]
    late_899s = scheduled_start + timedelta(seconds=899)

    res = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "android_pixel",
            "requested_start_at": late_899s.isoformat(),
            "expected_schedule_revision": 1,
        },
    )
    # BẮT BUỘC PHẢI THẤT BẠI: 409 Conflict (START_REQUIRES_REPLAN)
    assert res.status_code == 409


async def test_FAIL_UF3_03_reminders_query_without_time_range_fails_422(w05_env, w05_client):
    """[FAIL-UF3-03] Truy vấn Reminders thiếu query param 'from' và 'to' bị chặn 422.
    
    Thực tế:
    - Endpoint `GET /api/v1/reminders` yêu cầu bắt buộc cả 2 tham số thời gian.
    - Nếu Mobile chỉ gửi `?device_id=xyz`, request sẽ bị lỗi ngay lập tức.
    """
    res = await w05_client.get("/api/v1/reminders?device_id=test_dev")
    assert res.status_code == 422
    err_body = res.json()["error"]
    assert err_body["code"] == "VALIDATION_ERROR"
    field_paths = [f["field_path"] for f in err_body["field_errors"]]
    assert any("from_time" in p or "to_time" in p or "from" in p or "to" in p for p in field_paths)


async def test_FAIL_UF3_04_end_session_missing_actual_source_fails_422(w05_env, w05_client):
    """[FAIL-UF3-04] Kết thúc phiên thiếu nguồn actual_source bị chặn 422.
    
    Quy tắc Spec (Project.md §6.5):
    - `actual_work_seconds` bắt buộc phải có nguồn `actual_source` ('timer_proxy' hoặc 'self_report').
    - Thiếu nguồn thì actual phải là null, không được gửi actual mà thiếu nguồn.
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
            # Thiếu actual_source
        },
    )
    assert res.status_code == 422


async def test_FAIL_UF3_05_concurrent_start_same_session_fails_409(w05_env, w05_client):
    """[FAIL-UF3-05] 2 thiết bị cùng bấm Start một phiên cùng lúc: bên gửi sau bị chặn 409.
    
    Bảo vệ bằng Database Transaction & Unique Constraint:
    - Chỉ 1 request được tạo SessionRun (HTTP 201).
    - Request thứ 2 bị ném lỗi HTTP 409 Conflict.
    """
    session_id = str(w05_env["session_id"])
    start_time = w05_env["scheduled_start"].isoformat()

    req1 = w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={"device_id": "phone_1", "requested_start_at": start_time, "expected_schedule_revision": 1},
    )
    req2 = w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={"device_id": "tablet_2", "requested_start_at": start_time, "expected_schedule_revision": 1},
    )

    res1, res2 = await asyncio.gather(req1, req2)
    statuses = {res1.status_code, res2.status_code}
    assert 201 in statuses
    assert 409 in statuses


async def test_FAIL_UF3_06_stale_schedule_revision_fails_409(w05_env, w05_client):
    """[FAIL-UF3-06] Gửi expected_schedule_revision cũ bị chặn bằng 409 REVISION_CONFLICT."""
    session_id = str(w05_env["session_id"])
    res = await w05_client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "dev_stale",
            "requested_start_at": w05_env["scheduled_start"].isoformat(),
            "expected_schedule_revision": 9999,
        },
    )
    assert res.status_code == 409


async def test_FAIL_UF4_07_cross_user_thought_access_fails_403_or_404(w05_env, w05_client):
    """[FAIL-UF4-07] User B cố tình đọc hoặc xóa Thought của User A bị chặn 403 hoặc 404."""
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

    # Đổi sang User B
    alien_user = CurrentUser(user_id=str(uuid4()), email="alien@test.com")
    app.dependency_overrides[get_current_user] = lambda: alien_user

    try:
        res_get = await w05_client.get(f"/api/v1/thoughts/{tid}")
        assert res_get.status_code in (403, 404)
    finally:
        # Khôi phục User A
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=str(w05_env["user_id"]), email="user_a@proplan.test"
        )


async def test_FAIL_UF4_08_delete_thought_with_stale_revision_fails_409(w05_env, w05_client):
    """[FAIL-UF4-08] Xóa Thought với revision sai lệch bị chặn 409 REVISION_CONFLICT."""
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
        json={"expected_thought_revision": 888},  # Revision không khớp
    )
    assert res_del.status_code == 409


async def test_BUG_UF3_04_run_event_future_clock_skew_unvalidated(w05_env, w05_client):
    """[BUG-UF3-04] Gửi Run Event có timestamp trong tương lai xa 2 ngày vẫn được chấp nhận (200 OK).
    
    Quy tắc Spec:
    - Bất kỳ event nào có occurred_at lệch clock quá mức (vượt xa hiện tại) phải bị từ chối 422 Clock Skew.
    
    Thực tế Code:
    - Backend chưa validate occurred_at đối với clock skew, cho phép client gửi thời gian trong tương lai!
    """
    run_data = await _start_run(w05_client, w05_env)
    run_id = run_data["run_id"]
    future_time = datetime.now(UTC) + timedelta(days=2)

    res = await w05_client.post(
        f"/api/v1/runs/{run_id}/events",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": 1,
            "events": [
                {
                    "event_id": str(uuid4()),
                    "event_type": "pause",
                    "occurred_at": future_time.isoformat(),
                    "segment_index": 0,
                    "work_elapsed_seconds": 60,
                    "rest_elapsed_seconds": 0,
                }
            ],
        },
    )
    # TÁI HIỆN BUG: Lẽ ra phải 422 nhưng hiện tại backend chấp nhận 200 OK
    assert res.status_code == 200
