# Hồ Sơ Bàn Giao Sửa Lỗi & Rủi Ro — Phân Hệ UF7 (Cài Đặt Trợ Lý & Gợi Ý Thích Ứng A20)

- **Ngày lập:** `2026-11-19`
- **Tài liệu nền bắt buộc đọc trước:** [00_UF3_TO_UF7_SHARED_RULES_AND_UF12_ALIGNMENT_20261119.md](file:///C:/Users/admin/.gemini/antigravity/brain/6676fd32-d80c-44c4-881a-e6296d8e998c/00_UF3_TO_UF7_SHARED_RULES_AND_UF12_ALIGNMENT_20261119.md)
- **Phạm vi file phụ trách (`D:/HCMUS/CT/ProActive/backend`):**
  - Router: [settings.py (router)](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/settings.py)
  - Services: [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py), [adaptation.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/planning/adaptation.py)
  - Schema: [settings.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py)
  - Phối hợp: [device_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/device_service.py) (UF3 hủy reminder cũ), [reschedule_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/reschedule_service.py) (UF6 settings_change), [Project.md](file:///D:/HCMUS/CT/ProActive/Project.md#L507) & [AGENTS.md](file:///D:/HCMUS/CT/ProActive/AGENTS.md)

---

## 1. Các Điểm Đồng Bộ Từ UF1–UF2 & Quyết Định Đã Chốt Cho UF7

1. **`[DECISION LOCKED A5]` (`P4-RISK-01`) — Chặn trộn Global Preferences với `target_task_id`:**
   - Bắt buộc trả **`422 VALIDATION_ERROR`** nếu user gửi đồng thời `target_task_id != None` cùng với bất kỳ cấu hình toàn cục ảnh hưởng lịch nào (`work_interval_seconds`, `rest_interval_seconds`, `buffer_multiplier`). Yêu cầu tách làm 2 lệnh rõ ràng.
2. **`[DECISION LOCKED A6]` (`P4-GAP-01` & `BE-07`) — Chính sách A20 Học Buffer:**
   - **Lọc mẫu sạch (`P4-GAP-01`):** Chỉ nhận `SessionRun` đã kết thúc (`run_state in ("closed", "ended")`, `ended_at is not None`), `outcome` không nằm trong `("cancelled", "unknown", "no_response", "skipped")`, và có final `CheckIn.response_state == "answered"` (giữ compat fixture seed DB trực tiếp khi `final_ci is None and run_state in ("closed", "ended") and outcome == "completed"`).
   - **Công thức & Ngưỡng (`BE-07`):** **GIỮ nguyên công thức Median theo Step** (`v2_step_median_checked_inputs`), `min_sample_count = 7`, `sliding_window_steps = 10`, ngưỡng tuyệt đối **lớn hơn nghiêm ngặt `0.20` (`|suggested - current| > 0.20`)**, cooldown `7 ngày` (hoặc `14 ngày` khi có `annoyance_feedback`). Sau khi sửa xong code, **đồng bộ lại [AGENTS.md](file:///D:/HCMUS/CT/ProActive/AGENTS.md) §2.2 UF7 và [Project.md](file:///D:/HCMUS/CT/ProActive/Project.md#L507)** theo công thức này!
3. **`[UF1/UF2 SYNC]` (`RG03 rest=0`):** Giữ `rest_interval_seconds >= 0` hợp lệ (kể cả `0`), nhưng siết `work_interval_seconds > 0` tại [settings.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L31).

---

## 2. Danh Sách Chi Tiết Các Bugs (`[BUG][VERIFIED]` — `8` hạng mục)

#### UF7-BUG-01 `[BUG][VERIFIED]` (**HIGH** — `RG08 / P4-BUG-02 & H03`)
- **Tiêu đề:** Thiếu kiểm chặn trên `buffer_multiplier <= 2.0` / `isfinite`, cho `work_interval_seconds = 0` ở Pydantic, và không kiểm `reminder_offsets` giảm dần nghiêm ngặt
- **Vị trí:**
  - [settings.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L16), [L31-L43](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L31-L43), [L56](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L56)
  - [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L169-L222) (`_validate_preferences_payload`), [L792-L835](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L792-L835) (`_validate_accepted_suggestion_value`)
- **Nguyên nhân:**
  1. `PreferenceChanges.work_interval_seconds` dùng `Field(default=None, ge=0)` (`L31`, cho phép `0`), trong khi service/UF2 bắt buộc `> 0`.
  2. `PreferenceChanges.buffer_multiplier` chỉ dùng `gt=0` (`L34`) và `_validate_preferences_payload` chỉ kiểm `buffer < 1` (`L211`) — nhận cả `buffer = 2.5` (QA `P4-4C-05(c)` trả `200 OK`) và `NaN`/`Infinity`!
  3. `distinct_offsets` (`L37-L43`), `_validate_preferences_payload` (`L213`), và nhánh `reminder_offset` trong `_validate_accepted_suggestion_value` (`L815-L828`) chỉ kiểm `len(set(...)) == len(...)`, không kiểm `offsets[i] > offsets[i + 1]` (nhận `[300, 900, 600]` trả `200 OK` — QA `H03`).
- **Hướng sửa:**
  - Viết helper `validate_strict_descending_offsets(offsets: list[int]) -> list[int]` trong `schemas/settings.py`: kiểm `1 <= len <= 3`, từng phần tử `int` (không phải `bool`) `>= 0`, và `all(offsets[i] > offsets[i + 1] for i in range(len(offsets) - 1))`.
  - Trên input DTO `PreferenceChanges`: đặt `work_interval_seconds: int | None = Field(default=None, gt=0)`, `buffer_multiplier: float | None = Field(default=None, ge=1.0, le=2.0)` + `@field_validator` kiểm `math.isfinite`, và kiểm `reminder_offsets` qua helper giảm dần nghiêm ngặt.
  - Tại `_validate_preferences_payload` và `_validate_accepted_suggestion_value`: kiểm chặt tương tự (`1.0 <= buffer <= 2.0`, `math.isfinite`, giảm dần nghiêm ngặt) kèm `field_path` rõ ràng (`422 VALIDATION_ERROR`).
  - **CẢNH BÁO TƯƠNG THÍCH DB CŨ (`QUAN TRỌNG`):** Trên response DTO **`UserPreference`** ([settings.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L46-L59)), **GIỮ NGUYÊN `buffer_multiplier: float = Field(gt=0)`** (không siết `le=2.0` lên model đọc ra) để `GET /api/v1/preferences` không bị `ResponseValidationError 500` nếu DB staging/cũ có bản ghi `buffer_multiplier = 2.5`! (Nếu `requested_changes` cũ trong DB có giá trị bẩn thì cẩn trọng khi deserialize).

#### UF7-BUG-02 `[BUG][VERIFIED]` (**HIGH** — `H10` phối hợp UF3 & UF6)
- **Tiêu đề:** Đổi chỉ `reminder_offsets` bị coi là thay đổi ảnh hưởng lịch (`pending_review`) và tạo UF6 proposal thừa
- **Vị trí:** [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L54-L61), [L318-L328](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L318-L328) (`apply_settings_change`), [L940-L974](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L940-L974) (`respond_to_suggestion`)
- **Nguyên nhân:** `_SCHEDULE_IMPACT_PREF_FIELDS` (`L54-L60`) chứa `"reminder_offsets"`, nên mọi thay đổi chỉ gồm mốc nhắc khi có Session tương lai đều bị đẩy qua `create_settings_change_proposal` (`pending_review`), dù không làm dịch chuyển `scheduled_start` / `scheduled_end` hay `segments` nào cả. Tương tự ở `respond_to_suggestion` (`L940-L974`), khi `kind == "reminder_offset"` cũng bị đẩy qua UF6.
- **Hướng sửa:**
  1. Xóa `"reminder_offsets"` khỏi `_SCHEDULE_IMPACT_PREF_FIELDS` (chỉ giữ `{"work_interval_seconds", "rest_interval_seconds", "buffer_multiplier"}`).
  2. Trong `respond_to_suggestion`: chỉ kiểm `_has_future_confirmed_sessions` và tạo UF6 proposal khi `kind == "buffer_multiplier"`; nếu `kind == "reminder_offset"` thì áp thẳng `effective_from_next_session`.
  3. **Đồng bộ `Reminder` tương lai ngay khi áp thẳng `reminder_offsets` (`QUAN TRỌNG`):** Khi `reminder_offsets` đổi trực tiếp qua `apply_settings_change` hoặc `respond_to_suggestion`:
     - Tìm các `Session(status="scheduled", scheduled_start > now)` của user: hủy các `Reminder(delivery_state.in_(("scheduled", "snoozed")))` thuộc offset không còn dùng (hoặc tái sinh dòng `device_id=None` mới theo mốc mới với `if fire_at < now: continue` và `dedupe_key` có `pref_rev` tránh `UniqueConstraint("user_id", "dedupe_key")`), **đồng thời giữ nguyên `scheduled_start`/`scheduled_end`/`schedule_revision` của Session** và **giữ nguyên `SessionRun.settings_snapshot` của Run đang chạy**.
     - Phối hợp với **UF3** (`UF3-BUG-01`) để `GET /reminders` cũng hủy các bản ghi per-device cũ lệch `pref.reminder_offsets`.
- **Cảnh báo Test V2 (`Phase 6`):** Trong suite V2, `test_rg08_reminder_only_impact_and_global_supersede_metadata` (`tests/regressions/test_confirmed_bugs.py:1461`) từng được viết để probe hành vi cũ (`assert application_state == "pending_review"`). Phải cập nhật `test_rg08` ở Phase 6 để kiểm chứng `effective_from_next_session` cho `reminder_offsets` và dùng field thật sự ảnh hưởng lịch (`work_interval_seconds` / `buffer_multiplier`) cho nhánh global supersede!

#### UF7-BUG-03 `[BUG][VERIFIED]` (High — Mới phát hiện)
- **Tiêu đề:** Đổi cài đặt không ảnh hưởng lịch (`reminder_style` / `reminder_offsets`) khi đang có `pending_proposal_id` làm va chạm `effective_revision == requested_revision`
- **Vị trí:** [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L348-L356), [L417-L422](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L417-L422), đối chiếu [reschedule_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/reschedule_service.py#L3840-L3863)
- **Nguyên nhân:** Giả sử `effective_revision = 1`, user gửi đổi `work_interval_seconds` tạo pending proposal (`requested_revision = 2`, `proposal.source_revisions["settings"] = 1`). Trước khi duyệt proposal đó, user đổi `reminder_style` (nhánh áp thẳng): `L420` tăng `preferences.effective_revision += 1` $\rightarrow$ thành `2` == `requested_revision` (`2`), **và** làm `proposal.source_revisions["settings"] = 1 != 2` khiến proposal đang chờ bị đánh `SOURCE_CHANGED` vô lý hoặc khi approve ghi đè `effective_revision` thành `2` rồi mất dấu thứ tự tăng!
- **Hướng sửa:** Khi nhánh áp thẳng (`apply_settings_change` / `respond_to_suggestion(reminder_offset)`) tăng `effective_revision` trong lúc `preferences.pending_proposal_id is not None`:
  - Tính `new_eff_rev = max(preferences.effective_revision, preferences.requested_revision or 0) + 1`, và nâng `preferences.requested_revision = new_eff_rev + 1` (cùng cập nhật `SettingChangeRequest.effective_revision` / `requested_revision` tương ứng).
  - Nếu proposal đang chờ là một UF6 proposal bình thường (hoặc đang giữ `source_revisions["settings"]`), cân nhắc rõ giữa 2 lựa chọn: (a) giữ nguyên cờ đánh stale `SOURCE_CHANGED` để user `rebuild` lấy cài đặt mới (nếu chọn cách này thì **bắt buộc** giữ `requested_revision` lớn hơn `new_eff_rev`), hoặc (b) giữ lịch nguyên và cập nhật `effective_revision` sạch. Ít nhất `effective_revision` và `requested_revision` không bao giờ được trùng nhau!

#### UF7-BUG-04 `[BUG][VERIFIED]` (**HIGH — `[DECISION LOCKED A5]`** `P4-RISK-01`)
- **Tiêu đề:** Cho phép gửi lẫn Global Preferences ảnh hưởng lịch (`work`/`rest`/`buffer`) cùng `target_task_id` khiến chỉ Task mục tiêu re-segment còn Task khác bị lệch
- **Vị trí:** [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L251-L345), [settings.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L126-L154)
- **Nguyên nhân:** Khi client gửi cả `changes.preferences` (VD `work_interval_seconds=900`) và `changes.availability` kèm `target_task_id=TaskA`: `L330-336` gán `allowed_ids = [TaskA]`, dẫn tới UF6 chỉ re-segment `TaskA`, còn `TaskB` giữ nguyên block cũ trong khi `UserPreference.work_interval_seconds` toàn cục lại biến thành `900`.
- **Hướng sửa (theo chốt **A5**):**
  - Tại `_validate_preferences_payload` hoặc ngay đầu `apply_settings_change` (sau check `expected_*_revision` / hoặc ở `@model_validator` của `SettingsChangeRequest`):
    - Nếu `payload.target_task_id is not None` (hoặc `payload.target_draft_id is not None`) **VÀ** `payload.changes.preferences` chạm vào bất kỳ field nào trong `_SCHEDULE_IMPACT_PREF_FIELDS` (`{"work_interval_seconds", "rest_interval_seconds", "buffer_multiplier"}`) $\rightarrow$ raise `AppError(status_code=422, code="VALIDATION_ERROR", message="Không thể đổi cấu hình làm/nghỉ/buffer toàn cục kèm target_task_id; hãy tách thành yêu cầu toàn cục và yêu cầu sửa giờ riêng của công việc.", field_errors=[FieldError(field_path="changes.preferences", ...)], next_action="fix_fields")`.

#### UF7-BUG-05 `[BUG][VERIFIED]` (**CRITICAL — `[DECISION LOCKED A6]`** `P4-GAP-01`)
- **Tiêu đề:** `collect_buffer_step_samples` không lọc theo `CheckIn.response_state == "answered"` nên cộng cả mẫu `no_respond` / `awaiting_checkin` vào A20
- **Vị trí:** [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L547-L646) (`collect_buffer_step_samples`)
- **Nguyên nhân:** `L600-608` chỉ loại `run_state in ("running", "paused")`, `outcome == "cancelled"`, `actual_work_seconds <= 0`, `actual_source not in ("timer_proxy", "self_report")`. Hàm **không đọc bảng `check_ins`** và **không loại** `run_state == "awaiting_checkin"`, `ended_at is None`, `outcome in ("unknown", "no_response", "skipped")`.
- **Kịch bản lỗi (đã tái hiện thật trong QA `P4-4C-08`):** Run cha hết giờ bị timeout `no_respond` (`actual=3s`, `run_state="awaiting_checkin"`), Run con continuation được xác nhận `completed` (`actual=3s`, `response_state="answered"`) $\rightarrow$ collector cộng cả hai thành `6s` thay vì `3s`.
- **Hướng sửa (theo chốt **A6**):**
  1. Tải toàn bộ `CheckIn(kind="final")` cho `run_ids` (`CheckIn.user_id == owner, CheckIn.run_id.in_(run_ids), CheckIn.kind == "final"`) thành map `final_ci_by_run_id` (lưu ý nếu có hơn 1 bản ghi legacy thì lấy theo thứ tự nhất quán).
  2. Loại `run` nếu thỏa BẤT KỲ điều kiện nào sau:
     - `run.ended_at is None` hoặc `run.run_state in ("running", "paused", "awaiting_checkin")` (chỉ nhận `"closed"` hoặc legacy `"ended"`).
     - `run.outcome in ("cancelled", "unknown", "no_response", "skipped")`.
     - `run.actual_work_seconds is None or run.actual_work_seconds <= 0`.
     - `run.actual_source not in ("timer_proxy", "self_report")`.
     - `final_ci = final_ci_by_run_id.get(run.id)`:
       - Nếu `final_ci is not None`: **bắt buộc `final_ci.response_state == "answered"`** (loại thẳng `"open"` và `"no_respond"`).
       - Nếu `final_ci is None`: chỉ chấp nhận nếu `run.run_state in ("closed", "ended") and run.outcome == "completed"` (giữ tương thích fixture direct-DB của `test_phase2_non_ai_fixes.py` & phần fixture sạch của `test_rg07`).
  - **Cảnh báo Test V2 (`Phase 6`):** Theo quyết định **A6**, tại `tests/regressions/test_confirmed_bugs.py:1304, 1320-1323, 1426` (`test_rg07`), fixture `st_mixed` có Run `(1200s, no_respond)` + Run `(600s, answered)` $\rightarrow$ tổng sạch sau sửa sẽ là **`600s`** (thay vì `1800s`). Ghi chú để cập nhật `test_rg07` ở Phase 6.

#### UF7-BUG-06 `[BUG][VERIFIED]` (**HIGH — `[DECISION LOCKED A6]`** `BE-07 / P4-GAP-02`)
- **Tiêu đề:** Lệch tham số mặc định & dấu biên `0.20` giữa `planning/adaptation.py` (`5/15/0`, `< 0.2`) và `settings_service.py` (`7/10/7–14d`, `<= 0.2`) và tài liệu
- **Vị trí:** [adaptation.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/planning/adaptation.py#L24-L39), [L125](file:///D:/HCMUS/CT/ProActive/backend/app/services/planning/adaptation.py#L125); [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L46-L52), [L762-L766](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L762-L766); [Project.md](file:///D:/HCMUS/CT/ProActive/Project.md#L507); [AGENTS.md](file:///D:/HCMUS/CT/ProActive/AGENTS.md)
- **Nguyên nhân:**
  1. `evaluate_buffer_adaptation` mặc định `min_sample_count=5, sliding_window_steps=15, cooldown_seconds=0`, trong khi `settings_service.py` dùng `7 / 10 / 7–14 ngày`.
  2. Dấu biên ở `adaptation.py:125` là `abs(suggested - current) < threshold` (độ lệch **đúng bằng `0.20`** vẫn trả về gợi ý, sau đó bị `settings_service.py:763` `if diff <= Decimal("0.2"): return None` chặn lại!).
  3. `AGENTS.md` §2.2 UF7 vẫn ghi *"Moving Average 7–10 phiên gần nhất"* và `Project.md:507` ghi tham số cũ `5/15/0`.
- **Hướng sửa (theo chốt **A6**):**
  1. Sửa default của `evaluate_buffer_adaptation` trong `adaptation.py`: `min_sample_count: int = 7`, `sliding_window_steps: int = 10`, `cooldown_seconds: int = 7 * 24 * 3600`, `delta_threshold: float = 0.2`.
  2. Thống nhất dấu biên `<=` tại `adaptation.py:125` (`if suggested == current or abs(suggested - current) <= threshold: return None`) nếu quy tắc là chênh lệch **lớn hơn `0.2`** (`> 0.20` — nhớ rà toàn bộ unit test `abs(suggested - current) == 0.2` trong `test_scheduler_engine.py`, `test_deep_unit_functions.py`, `test_deep_chained_pipeline.py`!).
  3. **Cảnh báo Test Hiện Có (`BẮT BUỘC KIỂM`):** Kiểm [test_deep_chained_pipeline.py](file:///D:/HCMUS/CT/ProActive/backend/tests/extended/test_deep_chained_pipeline.py#L612-L617) (`min_sample_count=5`), `test_deep_unit_functions.py`, `test_scheduler_engine.py`, `test_w312_w317_uf7_settings_suggestions.py` xem các test nào truyền tay `min_sample_count=5` hay gọi default (với `min_sample_count=5` ở `L615`, `suggested=1.40, current=1.05 -> diff=0.35 > 0.20`, vẫn pass nếu truyền `min_sample_count=5`, chỉ gãy nếu có test bỏ trống `min_sample_count` với 5 mẫu hoặc truyền đúng biên `diff == 0.20`).
  4. Cập nhật [AGENTS.md](file:///D:/HCMUS/CT/ProActive/AGENTS.md) §2.2 UF7 và [Project.md](file:///D:/HCMUS/CT/ProActive/Project.md#L507) mô tả đúng công thức Median theo Step sạch (`7/10/>0.20/7–14 ngày`) ở đợt đồng bộ tài liệu.

#### UF7-BUG-07 `[BUG][VERIFIED]` (Medium — Mới phát hiện)
- **Tiêu đề:** `GET /suggestions/current` có thể nổ `500 ValueError` nếu `last_rejected.updated_at > calc_now`
- **Vị trí:** [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L729-L755), [adaptation.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/planning/adaptation.py#L84-L85)
- **Nguyên nhân:** Trong `get_current_suggestion`, `last_rejected_at` đọc từ `SuggestionRow.updated_at`. Nếu DB lưu dấu thời gian hơi vượt `calc_now` (do clock skew nhỏ hoặc truyền `now` kiểm thử trước `updated_at`), `evaluate_buffer_adaptation` ném `ValueError("last_suggestion_at không được nằm sau current_time")` tại `adaptation.py:85` mà `settings_service` không bắt $\rightarrow$ `500 Internal Server Error`!
- **Hướng sửa:** Trước khi gọi `evaluate_buffer_adaptation` ở `settings_service.py:743`, kiểm `if last_rejected_at is not None and last_rejected_at > calc_now:` thì trả `None` (coi như đang trong cooldown) hoặc truyền `effective_now = max(calc_now, last_rejected_at)` hợp lý.

#### UF7-BUG-08 `[BUG][VERIFIED]` (Low — Mới phát hiện)
- **Tiêu đề:** Thiếu `expected_task_revision` khi sửa `availability` theo `target_task_id` có thể lọt tới service và trả `409` thay vì `422` nếu gọi thẳng service
- **Vị trí:** [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L259-L278) vs [L281-L291](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L281-L291)
- **Nguyên nhân:** Ở tầng HTTP, Pydantic `SettingsChangeRequest` ([settings.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/settings.py#L146-L147)) đã chặn `422`, nhưng trong service `apply_settings_change`: nhánh `target_draft_id` (`L281-291`) kiểm `None` trả `422`, còn nhánh `target_task_id` (`L271`) so thẳng `target_task.revision != payload.expected_task_revision` nên ném `409 REVISION_CONFLICT` khi gọi thẳng service với `expected_task_revision=None`.
- **Hướng sửa:** Bổ sung `if payload.expected_task_revision is None: raise AppError(status_code=422, code="VALIDATION_ERROR", ...)` trước `L271` để đối xứng hoàn toàn với nhánh Draft.

---

## 3. Các Risks & Điểm Cần Lưu Ý Cho UF7 (`[RISK][VERIFIED]`)

1. **`[RISK][VERIFIED]` — Nhãn `estimate_source == "scope_modified"` chưa có writer ứng dụng:**
   - [adaptation.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/planning/adaptation.py#L100) và [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L639) loại mẫu có `step.estimate_source == "scope_modified"`, nhưng chưa có endpoint nào trong app hiện tại cho phép sửa phạm vi `Step` đã confirm và gắn `estimate_source = "scope_modified"`. Khi phát triển tính năng sửa phạm vi Step về sau, **bắt buộc** gán `step.estimate_source = "scope_modified"` để không làm bẩn A20.
2. **`[RISK][VERIFIED]` — `GET /suggestions/current` dùng `SELECT ... FOR UPDATE` và `db.commit()` trong `GET`:**
   - Tại [settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L674-L789), `GET /api/v1/suggestions/current` tạo `PreferenceRow` mặc định hoặc `AdaptationSuggestion` mới ngay trong `GET` kèm lock `FOR UPDATE`. Cần cảnh giác tranh chấp lock nếu client poll endpoint này đồng thời với `POST /settings/change-requests`.
3. **`[RISK][VERIFIED]` — `SettingsChangeRequest.pending_proposal_id` trên nhiều request liên tiếp:**
   - Nhờ sửa `P4-RISK-02` bên UF6 (`reschedule_service.py:2748-2751`), nhưng phía UF7 cần luôn đảm bảo không xóa/ghi đè `UserPreference` của proposal mới khi có 2 lệnh tạo change-request liên tiếp (`L329-356` tạo proposal trước khi ghi `preferences`).

---

## 4. Checklist Nghiệm Thu Cho Người Sửa UF7

```powershell
cd D:\HCMUS\CT\ProActive\backend
uv run ruff check app/api/v1/settings.py app/services/settings_service.py app/services/planning/adaptation.py app/schemas/settings.py
uv run ty check app/api/v1/settings.py app/services/settings_service.py app/services/planning/adaptation.py app/schemas/settings.py
uv run pytest tests/test_w312_w317_uf7_settings_suggestions.py tests/test_planning_api.py tests/unit/test_scheduler_engine.py tests/extended/test_deep_unit_functions.py tests/extended/test_deep_chained_pipeline.py -q
```
- **Ca kiểm mới bắt buộc phải có:**
  1. `buffer_multiplier`: `0.99` (422), `1.0` (200), `2.0` (200), `2.01` (422), `2.5` (422), `NaN`/`Inf` (422) ở cả `/settings/change-requests` và `/suggestions/{id}/response`; `work_interval_seconds=0` (422); `GET /preferences` vẫn đọc được dòng DB cũ `buffer=2.5` không nổ `500`.
  2. `reminder_offsets`: `[900, 600, 300]` (200), `[300, 900, 600]` (422), `[900, 600, 600]` (422), `[-1]` (422) ở cả manual & suggestion edit.
  3. **`H10`:** có Session tương lai, chỉ đổi `reminder_offsets=[1200, 600]` $\rightarrow$ `200`, `effective_from_next_session`, không tạo proposal UF6, Reminder tương lai cập nhật theo mốc mới, active Run `settings_snapshot` không đổi.
  4. **`A5`:** gửi `work_interval_seconds=900` (hoặc `buffer_multiplier=1.4`) kèm `target_task_id` $\rightarrow$ `422 VALIDATION_ERROR`.
  5. **`A6` (`P4-GAP-01` + `BE-07`):** parent `no_respond` (`actual=3s`) + child `answered` (`actual=3s`) $\rightarrow$ collector trả `3s`; `< 7` mẫu $\rightarrow$ `None`; `>= 7` mẫu với `diff == 0.20` $\rightarrow$ `None`, `diff > 0.20` $\rightarrow$ tạo gợi ý; `last_rejected_at > calc_now` trả `None` thay vì `500`.
  6. Revision guard: đang có `pending_proposal_id` (`requested_revision=2`), đổi `reminder_style` $\rightarrow$ không làm `effective_revision == requested_revision`.
