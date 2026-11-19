# Hồ Sơ Bàn Giao Sửa Lỗi & Rủi Ro — Phân Hệ UF5 (Kiểm Tra Tiến Độ & Kết Thúc Phiên)

- **Ngày lập:** `2026-11-19`
- **Tài liệu nền bắt buộc đọc trước:** [00_UF3_TO_UF7_SHARED_RULES_AND_UF12_ALIGNMENT_20261119.md](file:///C:/Users/admin/.gemini/antigravity/brain/6676fd32-d80c-44c4-881a-e6296d8e998c/00_UF3_TO_UF7_SHARED_RULES_AND_UF12_ALIGNMENT_20261119.md)
- **Phạm vi file phụ trách (`D:/HCMUS/CT/ProActive/backend`):**
  - Routers: [checkin.py](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/checkin.py), [voice.py](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/voice.py#L370-L417) (`uf5-checkin-prompt`)
  - Service: [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py)
  - Schema: [execution.py (schema)](file:///D:/HCMUS/CT/ProActive/backend/app/schemas/execution.py#L235-L284)
- **Bất biến UF5 ([AGENTS.md](file:///D:/HCMUS/CT/ProActive/AGENTS.md) §2.1 & §2.2 UF5, [schema.md](file:///D:/HCMUS/CT/ProActive/docs/backend/schema.md#L338-L357)):**
  - Tách bạch 3 cấp: `Session` $\neq$ `Step` $\neq$ `Task`. Kết thúc đồng hồ `Session` **KHÔNG** tự động đánh dấu hoàn thành bất kỳ cấp nào.
  - Phủ quyết tường minh (`False`) ở `step_completed` hoặc `task_completed` **TUYỆT ĐỐI KHÔNG** được phép bị cascade tự động ghi đè thành `"completed"`.

---

## 1. Các Điểm Đồng Bộ Từ UF1–UF2 & Quyết Định Đã Chốt Cho UF5

1. **`[DECISION LOCKED A2]` — Chính sách rút ngắn `dnd_release_at`:**
   - Giữ nguyên hành vi hiện tại tại [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L541-L555): khi trả lời final Check-in trước `checkin_timeout_at` (`responded_at < ended_at + 300s`), `run.dnd_release_at` được rút ngắn thành `min(dnd_release_at, resp_utc)`.
   - Phối hợp **Mobile**: không tự nhả DND hệ thống nếu người dùng bấm chuyển tiếp ngay sang phiên Continuation (UF5 $\rightarrow$ UF6); Mobile quản lý theo `dnd_owner_run_id` của Run mới.
2. **`[DECISION LOCKED A6]` (`P4-GAP-01` — Liên thông UF7 A20):**
   - Toàn bộ dữ liệu `CheckIn(kind="final", response_state="answered")` và `Step.status == "completed"` do UF5 ghi ra là đầu vào trực tiếp của bộ lọc mẫu học đệm `collect_buffer_step_samples` tại UF7 ([settings_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/settings_service.py#L547-L646)). Do đó sửa dứt điểm `UF5-BUG-01` & `UF5-BUG-02` là điều kiện tiên quyết để A20 không học từ Step chưa thực sự xong!

---

## 2. Danh Sách Chi Tiết Các Bugs (`[BUG][VERIFIED]` — `4` hạng mục)

#### UF5-BUG-01 `[BUG][VERIFIED]` (**CRITICAL** — `RG01 / P4-BUG-01`)
- **Tiêu đề:** Tự động cascade ép `Step` và `Task` sang `"completed"` bất chấp user phủ quyết tường minh `step_completed = False` / `task_completed = False`
- **Vị trí:** [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L340-L433) (`_apply_final_checkin_projection`)
- **Nguyên nhân:**
  - Tại `L353-355`: `this_auto_step_complete = payload.session_outcome == "completed" and all_step_sessions_completed` — **thiếu `and payload.step_completed is None and payload.task_completed is not False`**! Do đó nếu Step cuối chỉ có 1 Session, dù client gửi `step_completed=False, task_completed=False`, `this_auto_step_complete` vẫn bằng `True` $\rightarrow$ ghi `step.status = "completed"; step.remaining_base_seconds = 0` (`L360-362`).
  - Tại `L421`: `auto_task_complete = all_task_steps_completed and not has_unexpanded_scope` — **thiếu `and payload.task_completed is None and payload.step_completed is not False`**! Khi mọi Step đã xong (kể cả trong trường hợp user gửi `step_completed=True, task_completed=False`), `auto_task_complete` vẫn thành `True` và ép `task.status = "completed"`.
- **Kịch bản lỗi (đã tái hiện thật trong QA `P4-4A-16`):** Step cuối có 1 Session $\rightarrow$ Run xong $\rightarrow$ `PUT /check-ins/{id}/response` gửi `{"session_outcome": "completed", "step_completed": false, "task_completed": false}` $\rightarrow$ Session `completed`, nhưng DB ghi sai cả `Step.status="completed"`, `remaining_base_seconds=0`, và `Task.status="completed"`.
- **Hướng sửa (`3` trạng thái rạch ròi `True` / `False` / `None`):**
  - **Cấp `Step`:**
    - `this_explicit_step_deny = (payload.step_completed is False) or (payload.session_outcome != "completed")`
    - `this_explicit_step_complete = (payload.session_outcome == "completed") and (payload.step_completed is True or payload.task_completed is True)`
    - `this_auto_step_complete = (payload.session_outcome == "completed") and (payload.step_completed is None) and (payload.task_completed is not False) and all_step_sessions_completed`
    - Chỉ đặt `step.status = "completed"; step.remaining_base_seconds = 0` khi `(not this_explicit_step_deny and (this_explicit_step_complete or this_auto_step_complete))` hoặc có Run khác mới hơn xác nhận hoàn thành (xem `UF5-BUG-03`).
  - **Cấp `Task`:**
    - `this_explicit_task_deny = (payload.task_completed is False) or (payload.step_completed is False) or (payload.session_outcome != "completed")`
    - `this_explicit_task_complete = (payload.session_outcome == "completed") and (payload.task_completed is True)`
    - `auto_task_complete = all_task_steps_completed and (not has_unexpanded_scope) and (payload.task_completed is None) and (payload.step_completed is not False)`
    - Kiểm chứng riêng tổ hợp **`(session_outcome="completed", step_completed=True, task_completed=False)`**: `step.status == "completed"`, `remaining_base_seconds == 0`, **nhưng `task.status != "completed"`**.

#### UF5-BUG-02 `[BUG][VERIFIED]` (**HIGH** — Lỗi khôi phục `baseline` khi Correction)
- **Tiêu đề:** Khi sửa/phủ quyết Check-in từ `"completed"` về chưa xong mà `baseline["step_status"]` rỗng hoặc từng là `"completed"`/`"skipped"`, code giữ nguyên `step.status = "completed"` và `remaining_base_seconds = 0`
- **Vị trí:** [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L201-L216), [L366-L375](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L366-L375), [L429-L433](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L429-L433)
- **Nguyên nhân:**
  - Tại `L368`: `if isinstance(restored_step_status, str) and restored_step_status not in ("completed", "skipped"):` — nếu trước lượt Check-in này `baseline["step_status"]` vô tình bị ghi là `"completed"` (do `CommandReceipt` mất hoặc trước đó auto-complete lỗi) hoặc `restored_rem == 0`, cả nhánh `elif` không làm gì cả, bỏ mặc `step.status = "completed"` và `step.remaining_base_seconds = 0`!
  - Tại `L431`: tương tự cho `task.status` nếu `restored_task_status` là `None` hoặc `"completed"`.
- **Hướng sửa:**
  - Khi `step.status in ("completed", "skipped")` mà không còn điều kiện hoàn thành/skip nào đúng:
    - Nếu `baseline["step_status"]` hợp lệ (`not in ("completed", "skipped")`) $\rightarrow$ khôi phục đúng trạng thái đó; ngược lại **bắt buộc đặt `step.status = "in_progress"`**.
    - Khôi phục `step.remaining_base_seconds`: lấy từ `baseline["step_remaining_base_seconds"]` nếu `> 0`; ngược lại tính lại an toàn từ các Run đã đóng trên Step: `max(1, step.estimated_base_seconds - step_actual_work)` (với tối thiểu `>= 1`, **TUYỆT ĐỐI KHÔNG giữ bằng `0`**!).
  - Tương tự cho `task.status == "completed"`: khôi phục về `baseline["task_status"]` nếu khác `"completed"`, ngược lại fallback `"in_progress"`.

#### UF5-BUG-03 `[BUG][VERIFIED]` (Medium — Precedence đa Run trên cùng Step/Task)
- **Tiêu đề:** `other_step_completed` và `other_explicit_task_complete` bỏ qua thứ tự thời gian `responded_at`
- **Vị trí:** [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L340-L348), [L414-L417](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L414-L417)
- **Nguyên nhân:** `other_step_completed` chỉ dùng `any(...)` trên toàn bộ CheckIn của Step đó bất kể CheckIn kia diễn ra trước hay sau lần trả lời hiện tại!
  - Nếu Run 1 từng nhấn `step_completed=True` lúc `10:00`, rồi Run 2 (là Run hiện tại) ở lúc `11:00` nói `step_completed=False` (hoặc ngược lại: Run 1 đang sửa về `False`, trong khi Run 2 lúc `11:00` đã xác nhận `step_completed=True`): logic `any(...)` cũ luôn bị `True` và không biết bên nào mới hơn.
  - Ngoài ra ở `L396-413`, query `other_task_checkins` thậm chí không có `.order_by(CheckIn.responded_at.desc())`!
- **Hướng sửa:** So sánh theo cặp `(ci.responded_at or ci.updated_at)` với `(checkin.responded_at or payload.responded_at)`:
  - Chỉ cho `other_step_wins_completed = True` (hoặc `other_task_wins_completed = True`) nếu trong `other_*_checkins` có CheckIn **mới hơn** thời điểm hiện tại khẳng định hoàn thành mà không bị CheckIn mới hơn nữa phủ quyết; còn nếu lần phản hồi hiện tại (`this_checkin`) là mới nhất (hoặc không có CheckIn mới hơn) và nói `step_completed is False` / `task_completed is False`, phải giữ quyết định mới nhất đó! (Giữ đúng ca [test_w05_execution_checkin.py](file:///D:/HCMUS/CT/ProActive/backend/tests/test_w05_execution_checkin.py#L1720): Run 2 mới hơn đã confirm Step $\rightarrow$ sửa Run 1 về `False` không xóa xác nhận của Run 2).

#### UF5-BUG-04 `[BUG][VERIFIED]` (Medium — Clock-skew Check-in & thứ tự `asked_at >= ended_at`)
- **Tiêu đề:** `create_run_checkin` và `respond_to_checkin` thiếu chặn timestamp tương lai quá `+300s`, và `final` check-in cho phép `asked_at < run.ended_at`
- **Vị trí:** [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L103-L125), [L498-L526](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L498-L526), [checkin.py (router)](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/checkin.py#L64-L95)
- **Nguyên nhân:**
  1. `create_run_checkin` chỉ kiểm `asked_at >= run.started_at`, **không** kiểm `asked_at <= server_now + 300s` và **không** kiểm `asked_at >= run.ended_at` khi `kind == "final"`.
  2. `respond_to_checkin` chỉ kiểm `responded_at >= checkin.asked_at` (và `>= run.ended_at`), **không** kiểm `responded_at <= server_now + 300s`! Nếu client gửi `responded_at` ở tương lai (`+10 ngày`), check-in vẫn trả `200 OK` và lưu `responded_at` tương lai làm mốc so sánh.
- **Hướng sửa:** Tiêm `now: datetime | None = None` từ `checkin.py` qua `run_service._current_utc_now()` (sau durable replay): chặn `asked_at > server_now + 300s` & `asked_at >= run.ended_at` (khi `final`), và chặn `responded_at > server_now + 300s` với `422 VALIDATION_ERROR`. *(Lưu ý: đồng bộ cập nhật clock fixture nếu các test cũ trong `test_w05_execution_checkin.py` gửi ngày tĩnh `2026-10-30`).*

---

## 3. Các Risks & Điểm Cần Lưu Ý Cho UF5 (`[RISK][VERIFIED]`)

1. **`[RISK][VERIFIED][DECISION LOCKED A2]` — `dnd_release_at` khi trả lời sớm hoặc chuyển sang Continuation:**
   - Backend giữ nguyên `run.dnd_release_at = min(dnd_release_at, resp_utc)` tại `L545-554`. Mobile khi chuyển từ Check-in sang Continuation (UF5 $\rightarrow$ UF6) phải chủ động giữ/chuyển quyền DND sang Run mới thay vì tắt DND theo Run cũ.
2. **`[RISK][VERIFIED]` (`P4-RISK-03`) — Cơ chế lưu `baseline` trong lịch sử `CommandReceipt`:**
   - Tại [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L170-L239), `_load_prior_projection_baseline` query **toàn bộ** `CommandReceipt` loại `"respond_to_checkin"` của user rồi lọc bằng Python.
   - **Rủi ro:** Nếu xóa/retention `CommandReceipt` cũ hoặc user tích lũy hàng nghìn lệnh Check-in, truy vấn sẽ chậm và mất `baseline` gốc. (Giữ nguyên nếu chưa đổi DDL ở Phase 5, nhưng không dựa mù vào `baseline` khi fallback ở `UF5-BUG-02`).
3. **`[RISK][VERIFIED]` (`P4-G-BE06`) — `Edge-TTS` chập chờn trên `/voice/uf5-checkin-prompt`:**
   - [voice.py](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/voice.py#L396-L407) đã bắt `TtsError` và đặt `fallback_to_device_tts = True` (`audio_base64 = None`). Rủi ro nằm ở phía Mobile: phải kiểm tra cờ `fallback_to_device_tts` để gọi `flutter_tts` on-device, tránh im lặng khi Edge-TTS trả `NoAudioReceived`.
4. **`[RISK][VERIFIED]` — `skip_scope == "skip_step"` đánh dấu `step.status = "skipped"` nhưng không tự hủy Session tương lai:**
   - Tại [checkin_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/checkin_service.py#L363-L365), `respond_to_checkin` chuyển `step.status = "skipped"` nhưng các `Session(status="scheduled")` còn lại của Step đó chưa được hủy ngay vì mọi sửa đổi lịch đều đi qua UF6 (`POST /proposals` với `trigger_reason="checkin_skipped"`). *(Đã an toàn hơn từ UF2/UF3 nhờ kiểm `step.status in ("completed", "skipped", "cancelled")` trong `run_service.py:370`, nhưng người dùng cần đi qua UF6 để dọn lịch trên Calendar).*

---

## 4. Checklist Nghiệm Thu Cho Người Sửa UF5

```powershell
cd D:\HCMUS\CT\ProActive\backend
uv run ruff check app/api/v1/checkin.py app/services/checkin_service.py app/schemas/execution.py
uv run ty check app/api/v1/checkin.py app/services/checkin_service.py app/schemas/execution.py
uv run pytest tests/test_w05_execution_checkin.py tests/test_phase2_non_ai_fixes.py tests/test_phase3_non_ai_fixes.py -q
```
- **Tổ hợp 5 ca kiểm bắt buộc:**
  1. Step cuối có 1 Session, Run 20s $\rightarrow$ `("completed", step_completed=False, task_completed=False)` $\rightarrow$ Session `"completed"`, Step **không** `"completed"` (`remaining_base_seconds > 0`), Task **không** `"completed"`.
  2. `("completed", step_completed=True, task_completed=False)` $\rightarrow$ Step `"completed"` (`remaining=0`), Task **không** `"completed"`.
  3. `("completed", step_completed=None, task_completed=None)` $\rightarrow$ Step `"completed"`, Task `"completed"` (khi không còn `unexpanded_scope`).
  4. Correction `(completed, True, True)` $\rightarrow$ `(incomplete, None, None)` (kể cả khi `baseline` thiếu/từng completed) $\rightarrow$ Step về trạng thái mở (`"in_progress"` nếu không có baseline gốc), `remaining_base_seconds > 0`, Task không `"completed"`.
  5. Run 2 ở cùng Step (mới hơn) đã xác nhận Step hoàn thành, sửa lại Check-in Run 1 về `step_completed=False` $\rightarrow$ giữ nguyên kết quả mới hơn của Run 2.
