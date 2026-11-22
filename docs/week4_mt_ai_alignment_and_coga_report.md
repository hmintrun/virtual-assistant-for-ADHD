# Báo cáo Kiểm định AI Prompt Engineering, Schema Alignment & W3C COGA Tuần 4 (`mt`)

**Người thực hiện:** Minh Trung (`mt`) — Git Manager, Lead BA & Prompt Engineer  
**Ngày báo cáo:** 22/11/2026 (UTC+7)  
**Nhánh làm việc:** `feat/mt-week4-ai-alignment` (tạo từ `origin/backend` @ `c2c232e`)  
**Phạm vi Task:** `W4-01` (Đồng bộ baseline & đặc tả), `W4-12A` (Kiểm định AI Prompt, ranh giới Python/LLM, Grounded Copy UF6 & W3C COGA), chuẩn bị đầu vào cho `W4-16` (Bàn giao Tuần 4).

---

## 1. Tóm tắt Thay đổi Trong Phạm vi Sở hữu của `mt`

Tuân thủ tuyệt đối **Strict Ownership Boundary** tại [DEVELOPER.md](../DEVELOPER.md) và [AGENTS.md](../AGENTS.md) §0.3, chỉ chỉnh sửa các file thuộc quyền sở hữu của `mt`:

1. **[backend/app/schemas/schedule.py](../backend/app/schemas/schedule.py):**
   - Mở rộng `RescheduleTriggerReason` để bao phủ đầy đủ 9 trạng thái UF6 (`"incomplete"`, `"busy"`, `"setting_changed"`, `"deadline_risk"`, `"skipped"`, `"manual"`, `"missed_session"`, `"step_completed_early"`, `"expand_horizon"`), khớp 100% với `_reschedule_copy` trong `ai_service.py`.
2. **[backend/app/services/ai_service.py](../backend/app/services/ai_service.py):**
   - **`_reschedule_copy` (UF6 Grounded Copy):** Bổ sung đầy đủ ánh xạ alias từ các mã wire `TriggerReason` của `backend/app/schemas/reschedule.py` (`checkin_busy`, `checkin_incomplete`, `checkin_skipped`, `missed_start`, `early_completion_pull_forward`, `expand_next_horizon`, `settings_change`, `user_reschedule`) sang các nhóm câu đã kiểm chứng trạng thái, không làm rơi nhầm về mẫu chung khi caller truyền mã wire.
   - **`_fallback_extract_task` (UF1 Tier 3 Fallback):** Bổ sung trích xuất khung giờ rảnh theo thứ đơn lẻ tường minh (ví dụ `"Thứ 2 19:00-21:00"`, `"Thứ Bảy 09h-11h"`, `"Chủ nhật 14:00-16:00"`) bằng cách tái sử dụng `_extract_single_spoken_weekday` từ `app.schemas.task`, kèm guardrail loại trừ cụm từ thứ tự (`_TIER3_NON_WEEKDAY_ORDINAL_RE`), từ khóa báo bận (`_TIER3_BUSY_HINT_RE`), từ khóa deadline (`_TIER3_DEADLINE_PREFIX_RE`) và cụm ngày tương đối (`_UF1_RELATIVE_DATE_RE`). Luôn bảo toàn `previous_draft` và giữ `is_complete=False`.
3. **[backend/tests/test_ai_service.py](../backend/tests/test_ai_service.py):**
   - Bổ sung 3 ca kiểm thử `W4-12A`:
     - `test_w4_12a_uf6_wire_trigger_reasons_and_expand_horizon_grounded_copy`
     - `test_w4_12a_uf1_tier3_fallback_extracts_daily_and_single_weekday_windows`
     - `test_w4_12a_uf2_long_horizon_profile_and_candidate_nl_fallback`
4. **[AGENTS.md](../AGENTS.md) & [Project.md](../Project.md):**
   - Khôi phục mục `0.3. Giới hạn Phạm vi Chỉnh sửa File (Strict Ownership Boundary — BẮT BUỘC)` trong `AGENTS.md`.
   - Đồng bộ toàn bộ thông số **Pilot A20** theo code đã khóa ở Tuần 4 (`adaptation.py` & `settings_service.py`): trung vị (`Median`) tối thiểu **`7` Step hoàn tất sạch** (`SUGGESTION_MIN_SAMPLES = 7`) trong cửa sổ **`10` Step sạch gần nhất** (`SUGGESTION_SLIDING_WINDOW = 10`), bước làm tròn `0.05` (`ROUND_HALF_UP`), miền `[1.0, 2.0]`, chỉ gợi ý khi `|k_suggested - k_user| > 0.20` (lệch đúng `0.20` không gợi ý), ưu tiên 100% `k_user`, cooldown **`7 ngày`** (`604800s`) / **`14 ngày`** (`1209600s` khi có `annoyance_feedback`).
   - Đồng bộ cấu hình **AI Engine & Timing Profiles** (`config.py`): `gemini-3.5-flash-lite` mặc định thống nhất (bật Warm Backup khi `GEMINI_FLASH_MODEL != GEMINI_BACKUP_MODEL`), UF1 adaptive timeout `6/8/10s` (tối đa `15s`), Khung A `1.5s/2.2s/3.0s/4.5s`, Khung B `9.0s` cơ sở (`2.0s/5.0s/4.5s`) mở rộng 3 bậc `14.0s`/`20.0s` cho dài hạn.
   - Đồng bộ **Dual-Tier Voice Pipeline** (`voice_service.py` & `config.py`): `VOICE_STT_PROVIDER=auto` (`gemini-3.5-transcribe` inline `transcribe_inline`, chế độ `verbatim` $\rightarrow$ Groq Whisper `whisper-large-v3` cho UF1/nháp với ngân sách `8s`/`12s`; riêng `uf5_checkin` ưu tiên Groq Whisper $\rightarrow$ Gemini theo `VOICE_STT_UF5_PREFER_GROQ=true`), `edge-tts` + fallback on-device.
   - Đồng bộ quy tắc **UF6 Grounded Guilt-free Copy** (`allowed_notes`/`allowed_tip`, không gửi `task_title` thô lên LLM).

---

## 2. Kết quả Kiểm định AI Alignment & Chạy Suite Backend (`W4-12A`)

Tất cả kiểm tra dưới đây được chạy trực tiếp trên nhánh `feat/mt-week4-ai-alignment` trong `backend/.venv`:

| Hạng mục Kiểm tra | Lệnh Thực thi | Kết quả Quan sát |
| :--- | :--- | :--- |
| **Unit test AI Service (bao gồm 3 test `W4-12A` mới)** | `.\.venv\Scripts\python.exe -m pytest tests/test_ai_service.py -q` | **51 passed, 48 subtests passed** (7.53s) |
| **Suite AI + UF1 + UF2 + STT + A20 (`W4-12A` + `W4-12B`)** | `.\.venv\Scripts\python.exe -m pytest tests/test_ai_service.py tests/test_uf1_spoken_contexts.py tests/test_uf1_e2e_pipeline.py tests/test_uf2_fixes_and_risks.py tests/test_stt_gemini_flash_lite_inline.py tests/test_w4_12b_a20_qa_handoff.py -q` | **168 passed, 48 subtests passed** (40.63s) |
| **OpenAPI & Contract Fixtures (`W4-02`)** | `.\.venv\Scripts\python.exe -m pytest tests/contract/test_w4_02_openapi_fixtures.py -q` | **6 passed** (2.45s) |
| **Linting các file Python do `mt` sửa** | `.\.venv\Scripts\python.exe -m ruff check app/services/ai_service.py app/schemas/schedule.py tests/test_ai_service.py` | **All checks passed!** |
| **Alembic Migration Head** | `.\.venv\Scripts\python.exe -m alembic heads` | **`20261118_01 (head)`** |

### Kiểm chứng Ranh giới Prompt & Guardrails:
- **UF1 (`UF1_EXTRACTION_SYSTEM_PROMPT` & `_enforce_uf1_temporal_guardrails`):**
  - Giữ đủ 4 nhóm thông tin bắt buộc; hỗ trợ `default_free_windows`, `locked_fields`, ISO weekday (`1=T2..7=CN`), giờ bận/loại trừ, câu tự đính chính, `is_ambiguous` cho khung giờ mơ hồ và gộp câu hỏi trong 1 vòng.
  - Python kiểm chứng lại năm thiếu từ `relative_calendar_anchor` và chặn tuyệt đối deadline quá khứ (`has_conflict=True`, `is_complete=False`).
- **UF2 (`UF2_DECOMPOSITION_SYSTEM_PROMPT` & `UF2_CANDIDATE_ADJUSTMENT_SYSTEM_PROMPT`):**
  - Chia đề xuất mặc định kèm `micro_task_prompt` 2 phút và `cognitive_load`; hỗ trợ chia toàn kỳ `6–20` bước cho dự án `> 14` ngày; không tự tính Session/giờ/buffer trong LLM.
  - `adjust_candidate_by_nl` chỉ sửa theo `client_step_key`, đẩy thay đổi giờ rảnh/deadline về Draft (`requires_draft_update=True`), giữ nguyên phương án cũ khi AI lỗi (`TimeoutError` $\rightarrow$ `503 AI_UNAVAILABLE`).
- **UF5 (`build_uf5_checkin_prompt`):**
  - Tách bạch câu hỏi xác nhận `Session` với xác nhận `Step.expected_output` (chỉ hỏi `asks_step_completion=True` ở phiên cuối khi outcome là `None`, `completed` hoặc `partial`); đủ 3 chip Bỏ qua và `undo_timeout_seconds=5`.
- **UF6 (`UF6_EMPATHETIC_SYSTEM_PROMPT` & `generate_reschedule_empathy`):**
  - Không gửi `task_title` thô trong payload gửi LLM; chỉ gửi `allowed_notes` và `allowed_tip` đã kiểm chứng theo `trigger_reason`, `proposal_available`, `deficit_minutes`; tự fallback về `_fallback_reschedule_empathy` nếu LLM trả ngoài tập cho phép.

---

## 3. Rà soát W3C COGA trên Code Mobile Tuần 4 (`origin/uf5-uf7-w4-kc-mobile`, `origin/feat/mobile-uf1-voice-upgrade`, `origin/feat/tan-w4-mobile`)

Qua đối chiếu trực tiếp mã nguồn trên các nhánh Mobile Tuần 4:

- **Đạt chuẩn W3C COGA:**
  - **Không dùng `AlertDialog` xác nhận xóa/bỏ qua:** Đã kiểm tra toàn bộ `mobile/lib/` trên `origin/uf5-uf7-w4-kc-mobile@cf24807` — không có `AlertDialog`; sử dụng `SnackBar` kèm nút **`Hoàn tác` 5 giây** tại `focus_screen.dart` (bỏ qua check-in, xóa thought), `task_summary_screen.dart`, `weekly_availability_editor.dart`, `calendar_connect_screen.dart` và `google_calendar_screen.dart`.
  - **UF1–UF2 Loading & Voice:** `task_entry_screen.dart` (trên `origin/feat/mobile-uf1-voice-upgrade@5e111d3`) hiển thị hiệu ứng nhịp thở `_BreathingCircle` kèm thông điệp `"Melbel đang đọc công việc"` và gợi ý dài hạn `"Kế hoạch dài hạn đang được sắp theo từng cột mốc cho bạn..."`.
  - **UF3 1-Step UI & Micro-task:** `focus_screen.dart` hiển thị nút cụ thể `'Bắt đầu đếm giờ ${_minutes(session.workSeconds)} phút'`, giai đoạn khởi động `'Khởi động 2 phút · ${session.microTaskText}'` và nhánh `'Mình đã thử ghi âm 3 lần'` chuyển sang ô nhập chữ mà không ngắt đồng hồ.
  - **UF5 & UF6:** `focus_review_screens.dart` cung cấp nút `'Làm tiếp ngay phiên kế'`, ánh xạ `'resegmented_by_settings_change'` sang `'Bỏ một phiên để chia lại theo cài đặt mới'`, hiển thị `steps_to_add` và 2 `ActionChip` (`'Mở thêm giờ rảnh'`, `'Lùi hạn chót'`) khi thiếu giờ. `home_screen.dart` hiển thị banner nhẹ `'Xem đề xuất đổi lịch'` / `'Xem gợi ý cài đặt'` khi mở app. File dead placeholder `checkin_screen.dart` đã được gỡ bỏ ở `cf24807`.
- **Ghi chú tinh chỉnh nhỏ cho Mobile khi ghép nhánh:**
  - Tại `focus_screen.dart:923` (`origin/uf5-uf7-w4-kc-mobile@cf24807`), nhãn 3 chip Bỏ qua hiện là `('Hoãn phiên này', 'postpone_session')`, `('Bỏ qua phiên này', 'skip_session')`, `('Bỏ toàn bộ bước', 'skip_step')` — đã rõ ràng 3 mức và có `SnackBar` Hoàn tác 5 giây; có thể đồng bộ nguyên văn nhãn `'Hoãn phiên này làm sau'` và `'Bỏ toàn bộ bước này'` nếu `tan`/`kc` muốn khớp 100% microcopy trong `Project.md`.

---

## 4. Bảng Đối chiếu Nhánh GitHub & Việc Điều phối Tích hợp Tiếp theo (`W4-01` / `W4-16`)

| Nhánh (`origin/*`) | Commit SHA | Nội dung Chính & Trạng thái | Ghi chú Điều phối cho Nhóm |
| :--- | :---: | :--- | :--- |
| `origin/backend` | `c2c232e` | Đã gồm `W4-02` (`0b72556`), `W4-03A`/`W4-06` (`1c309de`), `BE-01..05`, STT inline (`cb0782f`), `W4-12B` (`c2c232e`) | **Sẵn sàng merge vào `dev`** cùng nhánh `feat/mt-week4-ai-alignment` |
| `origin/uf5-uf7-w4-kc-mobile` | `cf24807` | Đã gồm nền `origin/feat/tan-w4-mobile@7295820` + UI UF5–UF7 (`W4-11`) | Merge làm nhánh nền Mobile mới trên `dev` |
| `origin/feat/qt-w4-sqlite-outbox` | `71e3ae8` | SQLite/Outbox theo `ownerId`, UTC `hold_until`, `checkInExpired`, Thought tombstone (`W4-07M`, `W4-09`) | Chỉ cần nối factory `FocusProvider` vào `main.dart` khi tích hợp |
| `origin/feat/mobile-uf1-voice-upgrade` | `5e111d3` | Recorder UF1, silence endpointing, low-confidence UI, timeout UF2 (`30s`) | Giải quyết điểm chạm nhẹ với `tan`/`kc` tại `api_client.dart`, `task_provider.dart`, `planning_api.dart` |
| Nhánh Mobile Native của `kai` | *(Chưa thấy trên remote)* | Nhắc/Alarm/Doze/Reboot (`W4-08`) & `VoicePlaybackService` UF5 (`W4-10`) | Cần `kai` push để ghép trước khi `tan` build APK cuối (`W4-15`) |
| `origin/test` | `5a5b500` | Bộ test & báo cáo QA UF3–UF7 (`thành`) | **Cảnh báo bảo mật:** Không merge nguyên nhánh vì đang chứa `cilent_secret.json` (lộ OAuth `client_secret`); cần xóa file và rotate secret trên Google Cloud Console |
