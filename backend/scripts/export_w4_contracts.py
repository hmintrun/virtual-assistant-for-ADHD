"""Xuất contract W4-02; không khởi chạy lifespan, DB hoặc provider bên ngoài.

Chạy từ backend: python -m scripts.export_w4_contracts [--check].
"""

import argparse
import copy
import json
from pathlib import Path

from app.main import app
from app.schemas import auth, calendar, common, execution, reschedule, settings, thought, voice
from app.services.ai_service import ai_service

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/backend"
META = {"request_id": "w4-example", "server_time": "2026-11-20T03:00:00Z"}
USER = "11111111-1111-4111-8111-111111111111"
CONNECTION = "22222222-2222-4222-8222-222222222222"
PREVIEW = "33333333-3333-4333-8333-333333333333"
RUN = "44444444-4444-4444-8444-444444444444"
SESSION = "55555555-5555-4555-8555-555555555555"
THOUGHT = "66666666-6666-4666-8666-666666666666"
MARKER = "## 15. W4-02 — Bộ bàn giao OpenAPI và JSON fixtures"


def success(model, data):
    return common.SuccessEnvelope[model](data=data, meta=META).model_dump(mode="json")


def error(code, message, action, *, retryable=False, revisions=None, fields=None):
    return common.ErrorEnvelope.model_validate({
        "error": {"code": code, "message": message, "next_action": action,
                  "retryable": retryable, "current_revisions": revisions or {},
                  "field_errors": fields or []}, "meta": META,
    }).model_dump(mode="json")


def case(name, method, path, status, body, *, request=None, scope="runtime", headers=None,
         request_headers=None, client_expectations=None):
    value = {"name": name, "scope": scope, "method": method, "path": path,
             "response": {"status": status, "body": body}}
    if request is not None:
        value["request"] = request
    if request_headers:
        value["request_headers"] = request_headers
    if client_expectations:
        value["client_expectations"] = client_expectations
    if headers:
        value["response"]["headers"] = headers
    return value


def fixture_sets():
    token_body = success(auth.TokenBundle, {
        "access_token": "example-access-token", "token_type": "bearer", "expires_in": 3600,
        "refresh_token": "example-new-refresh-token", "refresh_expires_in": 2592000,
    })
    unauthenticated = error("UNAUTHENTICATED", "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.", "authenticate")
    group1 = [
        case("refresh_success", "POST", "/api/v1/auth/refresh", 200, token_body,
             request={"refresh_token": "example-old-refresh-token"}),
        case("app_unauthenticated", "GET", "/api/v1/auth/me", 401, unauthenticated,
             headers={"WWW-Authenticate": "Bearer"}),
        case("calendar_reconnect", "GET", f"/api/v1/calendar/connections/{CONNECTION}/calendars", 401,
             error("CALENDAR_AUTH_REQUIRED", "Cần kết nối lại Google Calendar.", "reconnect_google_calendar")),
        *[case(name, "POST", "/api/v1/auth/logout", 200,
               success(auth.LogoutData, {"logged_out": True, "revoked": revoked}),
               request={"refresh_token": "example-old-refresh-token"})
          for name, revoked in [("logout_first", True), ("logout_retry", False)]],
        case("refresh_reuse", "POST", "/api/v1/auth/refresh", 401, unauthenticated,
             request={"refresh_token": "example-old-refresh-token"},
             headers={"WWW-Authenticate": "Bearer"}),
    ]
    pref = {"revision": 5, "effective_revision": 3, "requested_revision": None,
            "work_interval_seconds": 1500, "rest_interval_seconds": 300, "micro_task_seconds": 120,
            "reminder_offsets": [900, 600, 300], "buffer_multiplier": 1.2, "reminder_style": "gentle"}
    change_request = {"expected_preferences_revision": 3, "changes": {"preferences": {"buffer_multiplier": 1.5}}}
    change = {"change_request_id": "change-1", "request_revision": 1, "application_state": "pending_review",
              "requested_revision": 4, "effective_revision": 3, "requested_availability_revision": None,
              "effective_availability_revision": None, "requested_changes": change_request["changes"],
              "pending_proposal_id": "proposal-1"}
    group2 = [
        case("preferences_after_reject", "GET", "/api/v1/preferences", 200, success(settings.UserPreference, pref)),
        case("settings_pending", "POST", "/api/v1/settings/change-requests", 200,
             success(settings.SettingsChangeResult, change), request=change_request),
        case("settings_conflict", "POST", "/api/v1/settings/change-requests", 409,
             error("REVISION_CONFLICT", "Cấu hình đã thay đổi.", "reload_and_review", revisions={"preferences": 3}),
             request={**change_request, "expected_preferences_revision": 5}),
        case("settings_buffer_low", "POST", "/api/v1/settings/change-requests", 422,
             error("VALIDATION_ERROR", "Cấu hình không hợp lệ.", "fix_fields"),
             request={"expected_preferences_revision": 3, "changes": {"preferences": {"buffer_multiplier": 0.5}}}),
        *[
            case(f"settings_buffer_boundary_{value:g}", "POST", "/api/v1/settings/change-requests", 200,
                 success(settings.SettingsChangeResult, {**change, "requested_changes": {"preferences": {"buffer_multiplier": value}}}),
                 request={"expected_preferences_revision": 3, "changes": {"preferences": {"buffer_multiplier": value}}})
            for value in (1.0, 2.0)
        ],
        case("settings_buffer_high", "POST", "/api/v1/settings/change-requests", 422,
             error("VALIDATION_ERROR", "Cấu hình không hợp lệ.", "fix_fields"),
             request={"expected_preferences_revision": 3, "changes": {"preferences": {"buffer_multiplier": 2.01}}}),
        case("settings_cancel", "POST", "/api/v1/settings/change-requests/change-1/cancel", 200,
             success(settings.CancelSettingsChangeResult, {"change_request_id": "change-1", "effective_revision": 3}),
             request={"expected_request_revision": 1}),
        case("suggestion_accept", "POST", "/api/v1/suggestions/suggestion-1/response", 200,
             success(settings.SuggestionResponseResult, {"suggestion_id": "suggestion-1", "suggestion_revision": 2,
                 "kind": "buffer_multiplier", "action": "accept", "status": "accepted", "accepted_value": 1.5,
                 "application_state": "pending_review", "effective_revision": 3, "requested_revision": 4,
                 "change_request_id": "change-1", "pending_proposal_id": "proposal-1"}),
             request={"expected_suggestion_revision": 1, "expected_preference_revision": 3, "action": "accept"}),
    ]
    event = {"operation": "update", "connection_id": CONNECTION, "google_calendar_id": "primary",
             "payload": {"summary": "Họp nhóm"}, "recurrence_scope": "single",
             "google_event_id": "event-1", "expected_google_etag": '"etag-v1"'}
    group3 = [
        case("configuration_preview", "POST", f"/api/v1/calendar/connections/{CONNECTION}/configuration-previews", 200,
             success(calendar.CalendarConfigurationPreviewResponse, {"preview_id": PREVIEW, "connection_id": CONNECTION,
                 "read_calendar_ids": ["primary"], "write_calendar_id": "primary", "existing_event_action": "keep",
                 "preview_revision": 1, "base_connection_revision": 2}),
             request={"read_calendar_ids": ["primary"], "write_calendar_id": "primary", "existing_event_action": "keep"}),
        case("configuration_preview_dynamic_revision", "POST", f"/api/v1/calendar/connections/{CONNECTION}/configuration-previews", 200,
             success(calendar.CalendarConfigurationPreviewResponse, {"preview_id": PREVIEW, "connection_id": CONNECTION,
                 "read_calendar_ids": ["primary"], "write_calendar_id": "primary", "existing_event_action": "keep",
                 "status": "pending", "preview_revision": 7, "base_connection_revision": 2}),
             request={"read_calendar_ids": ["primary"], "write_calendar_id": "primary", "existing_event_action": "keep"}),
        case("event_preview", "POST", "/api/v1/calendar/event-change-previews", 200,
             success(calendar.EventChangePreviewResponse, {"preview_id": PREVIEW, "connection_id": CONNECTION,
                 "operation": "update", "payload": event, "preview_revision": 1, "base_connection_revision": 2}),
             request=event),
        case("event_preview_dynamic_revision", "POST", "/api/v1/calendar/event-change-previews", 200,
             success(calendar.EventChangePreviewResponse, {"preview_id": PREVIEW, "connection_id": CONNECTION,
                 "kind": "event_change", "operation": "update", "status": "pending", "payload": event,
                 "preview_revision": 7, "base_connection_revision": 2}),
             request=event),
        case("preview_conflict", "POST", f"/api/v1/calendar/event-change-previews/{PREVIEW}/confirm", 409,
             error("REVISION_CONFLICT", "Phiên bản bản xem trước không khớp (đã bị cập nhật).", "reload_and_review",
                   revisions={"preview_revision": 2}),
             request={"expected_preview_revision": 7, "source_revisions": {}}),
        case("google_etag_412", "PUT", "/calendar/v3/calendars/primary/events/event-1", 412,
             {"error": {"code": 412, "message": "Precondition Failed"}}, scope="google_upstream"),
        case("sync_job_after_412", "GET", "/api/v1/calendar/sync-jobs/job-1", 200,
             success(calendar.CalendarSyncJobRead, {"job_id": "job-1", "connection_id": CONNECTION,
                 "target_kind": "external_event", "target_ref": PREVIEW, "operation": "update",
                 "job_state": "conflict", "attempt_count": 1, "last_error_code": "ETAG_CONFLICT_412"})),
    ]
    thought_request = {"client_thought_id": "client-thought-1", "source_run_id": RUN, "input_type": "audio",
                       "capture_state": "raw_saved", "attempt_count": 1, "text_content": None}
    run_data = {
        "run_id": RUN, "session_id": SESSION, "device_id": "device-1", "started_at": META["server_time"],
        "run_state": "running", "active_phase": "work", "settings_snapshot": {
            "effective_revision": 3, "session": {
                "session_id": SESSION, "step_id": "step-1", "scheduled_start": META["server_time"],
                "scheduled_end": "2026-11-20T03:30:00Z", "work_seconds": 1500, "rest_seconds": 300,
                "segments": [{"order_index": 0, "kind": "work", "duration_seconds": 1500},
                             {"order_index": 1, "kind": "rest", "duration_seconds": 300}],
                "duration_seconds": 1800, "schedule_revision": 8,
                "start_decision_due_at": "2026-11-20T03:05:00Z",
            },
        },
    }
    start_body = {"expected_schedule_revision": 8, "device_id": "device-1", "requested_start_at": META["server_time"]}
    start_headers = {"Idempotency-Key": "w4-start-1"}
    group4 = [
        case("start_idempotent", "POST", f"/api/v1/sessions/{SESSION}/start", 201,
             success(execution.SessionRunData, run_data), request=start_body, request_headers=start_headers),
        case("start_idempotent_replay", "POST", f"/api/v1/sessions/{SESSION}/start", 201,
             success(execution.SessionRunData, run_data), request=start_body, request_headers=start_headers,
             client_expectations={"replay_returns_same_run": True}),
        case("command_receipt", "GET", "/api/v1/commands/start-1", 200, success(common.CommandResult,
             {"command_id": "start-1", "command_type": "start_session_run", "state": "applied",
              "result_reference": {"run_id": RUN}, "applied_at": META["server_time"]})),
        case("start_clock_skew", "POST", f"/api/v1/sessions/{SESSION}/start", 422,
             error("VALIDATION_ERROR", "Thiếu mã thiết bị hoặc thời điểm bắt đầu hợp lệ.", "fix_fields", fields=[
                 {"field_path": "requested_start_at", "message": "Thiếu mã thiết bị hoặc thời điểm bắt đầu hợp lệ.", "code": None}]),
             request={"expected_schedule_revision": 8, "device_id": "device-1", "requested_start_at": "2026-11-20T03:05:01Z"},
             request_headers={"Idempotency-Key": "w4-start-clock-skew"}),
        case("thought_capture", "POST", "/api/v1/thoughts", 201,
             success(thought.ThoughtData, {**thought_request, "thought_id": THOUGHT, "thought_revision": 1}), request=thought_request),
        case("thought_delete", "DELETE", f"/api/v1/thoughts/{THOUGHT}", 200,
             success(thought.ThoughtDeleteResult, {"thought_id": THOUGHT, "deleted": True, "local_cleanup_required": True}),
             request={"expected_thought_revision": 1}),
    ]
    proposal = {"proposal_id": "proposal-1", "status": "pending", "trigger_reason": "settings_change",
        "target_task_id": "task-1", "allowed_task_ids": ["task-1"], "affected_task_ids": ["task-1"],
        "base_schedule_revision": 8, "source_revisions": {"task": 2, "schedule": 8, "availability": 1, "settings": 3, "calendar": 1},
        "proposal_revision": 1, "proposed_changes": {"steps_to_add": [{"step_id": "step-2", "task_id": "task-1",
            "order_index": 2, "title": "Hoàn thiện bản nháp", "estimated_base_seconds": 3600}],
            "sessions_to_cancel": [{"session_id": SESSION, "reason": "resegmented_by_settings_change"}],
            "next_horizon_from": "2026-11-20T03:00:00Z", "next_horizon_to": "2026-12-04T03:00:00Z"},
        "feasibility": {"status": "deficit", "confirmable": False, "deficit_seconds": 3600,
            "blocking_reasons": [{"code": "INSUFFICIENT_FREE_TIME", "message": "Còn thiếu 60 phút trước hạn."}],
            "next_horizon_preview": None}, "explanation": "Bạn có thể mở thêm giờ rảnh hoặc lùi hạn chót để tính lại phương án.",
        "pending_notification_state": "not_sent", "created_at": META["server_time"], "updated_at": META["server_time"]}
    prompt = ai_service.build_uf5_checkin_prompt(kind="final").model_dump(mode="json")
    group5 = [
        case("proposal_deficit", "GET", "/api/v1/proposals/current", 200, success(reschedule.ProposalData, proposal)),
        case("tts_empty_audio", "POST", "/api/v1/voice/tts", 503,
             error("TTS_EMPTY_AUDIO", "Không tạo được âm thanh lúc này. Ứng dụng sẽ dùng giọng đọc trên máy.", "use_on_device_tts"),
             request={"text": "Đến giờ kiểm tra tiến độ."}),
        case("tts_timeout", "POST", "/api/v1/voice/tts", 504,
             error("TTS_TIMEOUT", "Tổng hợp giọng nói mất nhiều thời gian hơn dự kiến.", "use_on_device_tts", retryable=True),
             request={"text": "Đến giờ kiểm tra tiến độ."}),
        case("stt_timeout", "POST", "/api/v1/voice/transcriptions", 504,
             error("STT_TIMEOUT", "Nhận giọng nói mất nhiều thời gian hơn dự kiến.", "use_on_device_stt_or_type", retryable=True),
             request={"purpose": "uf1_input", "device_id": "device-1", "file": "sample.m4a"},
             request_headers={"Idempotency-Key": "w4-stt-timeout"},
             client_expectations={"receive_timeout_seconds_min": 45}),
        case("uf5_tts_fallback", "POST", "/api/v1/voice/uf5-checkin-prompt", 200,
             success(voice.UF5VoiceCheckInPromptResponse, {"prompt": prompt, "fallback_to_device_tts": True}),
             request={"kind": "final", "include_audio": True}),
    ]
    return dict(zip(["01_auth_calendar", "02_settings", "03_calendar_previews", "04_run_thought", "05_uf6_voice"],
                    [group1, group2, group3, group4, group5], strict=True))


def auth_target_spec(runtime):
    """Đặc tả riêng cho BE-03 đã chốt; không thêm route giả vào app runtime."""
    refresh = copy.deepcopy(runtime["paths"]["/api/v1/auth/refresh"]["post"])
    refresh["security"] = []
    refresh["x-implementation-status"] = "target_be03"
    refresh["description"] = "Rotate jti; reuse thu hồi token family và trả 401 UNAUTHENTICATED."
    refresh["responses"] = {k: v for k, v in refresh["responses"].items() if k in {"200", "401", "403", "422", "503"}}
    refresh["responses"]["401"]["headers"] = {"WWW-Authenticate": {"schema": {"type": "string", "const": "Bearer"}}}
    logout = copy.deepcopy(refresh)
    logout.update(operationId="logout_target_be03", summary="Logout Option 1 — target BE-03",
                  description="Public theo refresh_token; không cần access token/Idempotency-Key. Retry đã revoke trả 200 revoked=false.")
    logout["responses"]["200"] = {"description": "Đã đăng xuất; revoked cho biết có thu hồi mới ở lần gọi này.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LogoutEnvelope"}}}}
    schemas = copy.deepcopy(runtime["components"]["schemas"])
    schemas["LogoutEnvelope"] = {"type": "object", "additionalProperties": False, "required": ["data", "meta"],
        "properties": {"data": {"type": "object", "additionalProperties": False,
            "required": ["logged_out", "revoked"], "properties": {"logged_out": {"type": "boolean", "const": True}, "revoked": {"type": "boolean"}}},
            "meta": {"$ref": "#/components/schemas/ResponseMeta"}}}
    paths = {"/api/v1/auth/refresh": {"post": refresh}, "/api/v1/auth/logout": {"post": logout}}
    needed = set()

    def collect(node):
        if isinstance(node, dict):
            ref = node.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in needed:
                    needed.add(name)
                    collect(schemas[name])
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(paths)
    return {"openapi": runtime["openapi"], "info": {"title": "ProPlan VA — Auth BE-03 target", "version": "w4-02",
        "description": "Contract VQH ba16055, schema.md 14.1. Chưa phải API đã triển khai."},
        "paths": paths, "components": {"schemas": {name: schemas[name] for name in sorted(needed)}}}


GUIDE = """## 15. W4-02 — Bộ bàn giao OpenAPI và JSON fixtures

Nguồn runtime: `backend` (đã tích hợp `23e856b`, `37c4491` và `BE-03`); contract Auth do VQH chốt tại `ba16055` (mục 14.1). Mã và thời gian trong fixture là dữ liệu giả, không chứa token thật. `scope=runtime` mô tả code hiện tại (đã bao gồm `BE-03`: refresh rotation/reuse detection và `POST /api/v1/auth/logout`). Contract tests đối chiếu OpenAPI runtime và validate DTO/fixtures.

- [openapi.json](openapi.json): xuất trực tiếp `app.openapi()` của code đang tích hợp (đã có `POST /api/v1/auth/logout` và `LogoutData`).
- [openapi-auth-target.json](openapi-auth-target.json): bản OpenAPI tham chiếu lịch sử cho Auth BE-03 Option 1 (gắn nhãn `target_be03`), giữ lại cho tương thích kiểm thử hợp đồng sau khi `BE-03` đã vào `openapi.json` runtime.
- Năm bộ JSON dưới đây cũng có file riêng trong [fixtures/w4-02/](fixtures/w4-02/). `scope=runtime` là contract của code hiện tại; `google_upstream` là phản hồi từ Google tới worker.

### Quy tắc client và trạng thái đồng bộ code

1. Auth: đọc `body.error.code`. `401 CALENDAR_AUTH_REQUIRED` chỉ yêu cầu nối lại Calendar. `BE-03` đã hoàn thành trong runtime: `POST /api/v1/auth/logout` (public theo `refresh_token`) trả `logged_out=true` và `revoked=true/false` (retry idempotent `200`), không cần header `Authorization` hay `Idempotency-Key`; `POST /api/v1/auth/refresh` xoay `jti` có lưu vết `refresh_tokens` và khi phát hiện reuse sẽ thu hồi toàn bộ family cùng `401 UNAUTHENTICATED` kèm header `WWW-Authenticate: Bearer`.
2. Settings: gửi `effective_revision` vào `expected_preferences_revision`; suggestion dùng tên **số ít** `expected_preference_revision`. Hủy dùng `change_request_id` + `request_revision` từ response save. Sau reopen hoặc accept suggestion, response hiện chưa cung cấp đủ request revision để hủy: không tự lấy preference revision thay thế. `409` tải lại cấu hình. Chính sách client `buffer_multiplier ∈ [1.0, 2.0]`: runtime đã chặn cả dưới `1.0` và trên `2.0` (`422 VALIDATION_ERROR` ở `scope=runtime`).
3. Calendar: runtime preview đã trả `preview_revision` (và `base_connection_revision`) trong response; client gửi lại giá trị này vào trường `expected_preview_revision` khi gọi `/confirm` (lệch revision trả `409 REVISION_CONFLICT` kèm `current_revisions.preview_revision`, thiếu trả `422`). JSON `412` là lỗi Google upstream; client đọc job `200` với `job_state=conflict`, `last_error_code=ETAG_CONFLICT_412`, không retry mù.
4. Run: Start chống lặp bằng `Idempotency-Key` (body Start không có `event_id`); fixture ghi cả request gốc và retry cùng key, kèm receipt. End có cả key và `event_id`. Khi mất phản hồi, đối soát `/commands/{command_id}`/Run trước retry. Clock-skew Start được kiểm bằng giờ server, giới hạn `300s`; fixture vượt `301s` trả `422 VALIDATION_ERROR` trên `requested_start_at`. `offline_start_supported=false`. Thought giữ `source_run_id`, metadata audio không chứa transcript/path local; thử audio tối đa 3 lần. Chỉ sau phiên: `/voice/transcriptions` với `purpose=thought_to_draft`, rồi `/thoughts/{id}/draft`.
5. UF6: giữ `steps_to_add`, `pending_notification_state`, `feasibility.next_horizon_preview` (nullable). Hiển thị mã `resegmented_by_settings_change` thành **“Chia lại phiên theo cài đặt mới”**; không đổi enum wire. `deficit_seconds>0` hoặc `confirmable=false` hiển thị hai lựa chọn mở giờ rảnh/lùi hạn chót. Approve `409` tải lại và cho user duyệt lại. Voice: STT timeout phía client ít nhất `45s`; fixture STT/TTS có nhánh `504`; TTS `503` chuyển `flutter_tts`; UF5 trả `200` với `fallback_to_device_tts=true` khi TTS lỗi.

### Tái tạo và kiểm tra

Chạy từ `backend/`:

```powershell
.\\.venv\\Scripts\\python.exe -m scripts.export_w4_contracts
.\\.venv\\Scripts\\python.exe -m scripts.export_w4_contracts --check
.\\.venv\\Scripts\\python.exe -m pytest tests/contract/test_w4_02_openapi_fixtures.py -q
```

Chưa có xác nhận các thành viên đã nhận fixture; người thực hiện bàn giao chỉ đánh dấu DoD này sau khi team xác nhận.
"""


def rendered_artifacts():
    runtime = app.openapi()
    files = {DOCS / "openapi.json": runtime, DOCS / "openapi-auth-target.json": auth_target_spec(runtime)}
    docs = GUIDE
    for index, (name, cases) in enumerate(fixture_sets().items(), 1):
        value = {"contract_source": "schema.md sections 14–15; backend 23e856b; auth target ba16055", "cases": cases}
        files[DOCS / "fixtures/w4-02" / f"{name}.json"] = value
        docs += f"\n### Fixture {index} — {name}\n\n[JSON riêng](fixtures/w4-02/{name}.json)\n\n<details>\n<summary>Xem JSON request/response</summary>\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```\n\n</details>\n"
    rendered = {path: json.dumps(value, ensure_ascii=False, indent=2) + "\n" for path, value in files.items()}
    current = (DOCS / "schema.md").read_text(encoding="utf-8")
    # Chỉ quản lý phần W4-02, giữ nguyên contract VQH đã bàn giao ở mục 1–14.
    prefix = current.split("## 15. W4-02", 1)[0].rstrip()
    rendered[DOCS / "schema.md"] = prefix + "\n\n" + docs.rstrip() + "\n"
    return rendered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in rendered_artifacts().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if stale:
        raise SystemExit("Stale contract artifacts: " + ", ".join(stale))
    print("W4-02: artifacts up to date." if args.check else "W4-02: exported OpenAPI, Auth target and five fixture sets.")


if __name__ == "__main__":
    main()
