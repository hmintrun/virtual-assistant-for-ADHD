# W4-12A · Kiểm định Độc lập Kiến trúc AI Grounded & 3 Quyết định Đã Chốt

- **Phạm vi đối soát:** Worktree cô lập nhánh `backend` (base SHA `c2c232e` + WIP Phase 1 + bản vá Step 1 cho `[BUG-01]`/`[BUG-02]`; sẽ đối soát lại `dev` SHA sau khi `mt` merge vào `dev`).
- **Vai trò thực hiện:** Senior Backend QA/QC Tester (kiểm định mã nguồn và chạy test thực nghiệm; không chỉnh sửa mã nguồn production hay test).
- **Trạng thái kiểm định:** **PASS (`0 failed`)** — Hoàn tất kiểm tra trực tiếp mã nguồn và tái chạy thực nghiệm bộ test mục tiêu sau bản vá Step 1 (`109 passed, 1 warning, 23 subtests passed in 20.16s`). `[BUG-01]` liên quan tới luồng UF6 `expand_next_horizon` phát hiện ở lần chạy CI ban đầu đã được vá và tái kiểm chứng đạt `0 failed` (chi tiết tại mục 4 và [W4_13_BACKEND_CI_REPORT.md](file:///D:/HCMUS/CT/ProActive/_temp/wt_merge_stt_uf5_uf7/docs/backend/W4_13_BACKEND_CI_REPORT.md)).

---

## 1. Kiểm định 3 Trục Trọng tâm `W4-12A`

### Trục 1 — `BE-01`: Xử lý `"hằng ngày"` mơ hồ (`AI_AVAILABILITY_INCOMPLETE`) $\rightarrow$ Bổ sung 7 ngày $\rightarrow$ `can_plan = True`

- **Nhận diện & gắn cờ mơ hồ ở Schema (`backend/app/schemas/task.py`):**
  - `_DAILY_REPEAT_TOKENS` (`#L58-L69`) định nghĩa các cụm lặp lại hàng ngày gồm `"hằng ngày"`, `"hàng ngày"`, `"mỗi ngày"`, `"ngày nào cũng"`, `"cả tuần"`, `"tất cả các ngày"`, `"mọi ngày"`, `"tối nào cũng"`, `"sáng nào cũng"`, `"chiều nào cũng"`.
  - Trong `ExtractedFreeWindowSchema.validate_and_flag_ambiguity()` (`#L460-L461`), khi chưa có neo ngày/thứ tường minh và `raw_phrase` khớp `_DAILY_REPEAT_TOKENS`, trường `kind` tự động chuẩn hóa thành `"daily"`. Khi thiếu `start_local` hoặc `end_local`, validator đánh dấu `is_ambiguous = True`.
- **Khai triển 7 ngày, phát `AI_AVAILABILITY_INCOMPLETE` và hợp nhất lượt bổ sung (`backend/app/services/draft_service.py`):**
  - `_availability_proposal()` (`#L1171-L1452`):
    - Khi `item.kind == "daily"` đã đủ giờ hợp lệ (`start_local`, `end_local`), lớp nền dương (`#L1228-L1249`) khai triển đầy đủ `weekly_map[1..7]` và toàn bộ ngày trong cửa sổ thời gian hiệu lực (`eff_dates`, tối đa 60 ngày).
    - Các khung còn `item.is_ambiguous == True` được đưa vào `unresolved` (`#L1196-L1198`).
  - `_merge_free_windows()` (`#L945-L1168`): Khi lượt sau trả về các khung neo cụ thể hợp lệ (`resolved_incoming`), khối dọn ngữ cảnh mơ hồ (`#L1038-L1067`) loại bỏ có chọn lọc các khung lặp mơ hồ cũ (`is_unanchored_ambiguous` và `is_vague_repeat`, như `"tối hằng ngày"`) mà không xóa nhầm các cụm mơ hồ thuộc ngày/thứ khác.
  - `_analyze_draft_impl()` (`#L2693-L2718`): Khi `unresolved` khác rỗng, hệ thống phát `ClarificationIssue(code="AI_AVAILABILITY_INCOMPLETE", field_path="availability", blocking=True)`. Khi lượt bổ sung làm sạch `unresolved` và xác lập khung giờ hợp lệ (`availability_resolved_cleanly` tại `#L2779-L2787`), các nhánh lọc tại `#L2803-L2811`, `#L2880-L2894`, `#L3020-L3035` và `#L3121-L3161` gỡ `AI_AVAILABILITY_INCOMPLETE`, kiểm tra lại qua `_evaluate_summary_for_user` $\rightarrow$ `validate_planning_prerequisites`, đặt `can_plan = True` và chuyển `stage = "summary_review"` (`#L3177`).
- **Kiểm chứng test thực thi:** `backend/tests/test_uf1_spoken_contexts.py#L200-L362` (`test_b0_1_daily_turn1_explicit_and_fallback`, `test_b0_2_vague_daily_turn1_then_7_specific_dates_turn2`, `test_b0_2_selective_unanchored_cleanup_preserves_other_vague_window`) đều **PASS**.

### Trục 2 — Ranh giới Kiến trúc Hybrid: `scheduler_engine.py` & Python Planning tính 100% $\Delta T_{\text{deficit}}$, Duration & Buffer

- **Tách biệt hoàn toàn tại `scheduler_engine.py` (`backend/app/services/scheduler_engine.py#L1-L61`):**
  - Facade thuần Python (không import LLM/API ngoài, không mở transaction DB), gom các module dưới `backend/app/services/planning/`:
    - `durations.py` (`#L19-L169`): `calculate_buffered_work`, `build_session_segments`, `prepare_step_durations`, `fit_session_to_slot` tính toàn bộ thời lượng nhân buffer ($W_{\text{buffered}} = \lceil \text{base} \times k \rceil$), phân rã quãng work/rest và binary search nhét khe rảnh.
    - `availability.py` (`#L95-L245`): `compute_effective_free_intervals` cắt trừ tập đoạn giờ bận Google Calendar / manual busy / fixed sessions bằng two-pointer interval subtraction theo timezone IANA.
    - `scheduling.py` (`#L403-L520`): `schedule_candidate_sessions` tính `available`, `total_work`, `deficit = max(0, total_work - available)`, `status` (`"feasible"` / `"infeasible"` / `"search_inconclusive"`), `blocking_reasons` (`TOTAL_DEFICIT` / `FRAGMENTED_FREE_SLOTS`), bảo toàn khoảng nghỉ 900s giữa các phiên (`subtract_fixed_session_gaps`, `#L220-L247`).
    - `rescheduling.py` (`#L17-L122`): `shift_elapsed_sessions` tịnh tiến phiên theo mốc thời gian thực `real_time_anchor`, giữ cố định task ngoài `allowed_task_ids` và trả `deficit_seconds`.
    - `adaptation.py` (`#L24-L143`): `evaluate_buffer_adaptation` dùng `Decimal` + `ROUND_HALF_UP` kiểm tra mẫu Step sạch và đề xuất hệ số đệm.
    - `validation.py` (`#L65-L505`): `validate_planning_prerequisites`, `validate_session_start_feasibility`.
- **Áp dụng tại UF2 (`plan_service.py`) và UF6 (`reschedule_service.py`):**
  - UF2 (`backend/app/services/plan_service.py#L1020-L1160`, `#L1296-L1341`, `#L1570-L1658`, `#L1716`): `ai_service.decompose_task` và `ai_service.adjust_candidate_by_nl` chỉ sinh cấu trúc ngữ nghĩa / keyed edits; toàn bộ phiên tập trung và `deficit_seconds` do `compute_effective_free_intervals`, `schedule_candidate_sessions`, `calculate_buffered_work` quyết định.
  - UF6 (`backend/app/services/reschedule_service.py#L85-L95`, `#L2746-L2773`, `#L3255-L3277`): Thuật toán Python tính `avail_slots`, `usable_avail`, `deficit_seconds` và `FeasibilityData` trước khi gọi lớp sinh giải thích.
- **Rào chắn phía AI Service (`backend/app/services/ai_service.py`):**
  - `UF2_DECOMPOSITION_SYSTEM_PROMPT` (`#L411-L422`), `UF2_PLAN_ADJUSTMENT_SYSTEM_PROMPT` (`#L424-L436`), `UF2_CANDIDATE_ADJUSTMENT_SYSTEM_PROMPT` (`#L438-L455`) quy định rõ LLM không tự chia Session, không tính ngày giờ, không nhân buffer, không xáo trộn Task khác.
  - `_enforce_uf1_temporal_guardrails()` (`#L1533-L1667`) dùng Python kiểm chứng lại năm quá khứ và so sánh `deadline <= current_dt`.

### Trục 3 — Grounded Copy UF6 (`task_title=None`, Allowlist Verbatim, Anti-Prompt-Injection)

- **Gọi từ UF6 (`backend/app/services/reschedule_service.py#L120-L145`):**
  - Hàm `_generate_uf6_explanation_and_tier(trigger_reason, feasibility)` quy đổi `deficit_minutes = (deficit_secs + 59) // 60 if deficit_secs > 0 else None`, xác định `proposal_available = feasibility.status in ("feasible", "valid") and feasibility.confirmable`, và **cố định truyền `task_title=None`** khi gọi `ai_service.generate_reschedule_empathy(...)` (`#L133-L138`). Toàn bộ 3 điểm gọi trong `reschedule_service.py` (`#L3452`, `#L3698`, `#L3950`) đều đi qua hàm này.
- **Rào chắn Grounded Copy tại `ai_service.py` (`#L457-L462`, `#L2425-L2701`):**
  - `_reschedule_copy()` (`#L2425-L2631`) sinh tập `allowed_notes` và `allowed_tip` thuần Python dựa trên `trigger_reason`, `proposal_available` và `deficit_minutes`. Khi `proposal_available=False`, câu chữ tuyệt đối không khẳng định đã có phương án mới.
  - `generate_reschedule_empathy()` (`#L2633-L2682`): Giữ `_ = task_title` chỉ để tương thích chữ ký hàm; `context_payload` gửi lên LLM **chỉ gồm** `{"allowed_notes": notes, "allowed_tip": tip}` (`#L2648-L2651`), triệt tiêu bề mặt prompt injection từ tên Task.
  - Hậu kiểm đầu ra (`#L2662-L2668`): Nếu LLM trả `empathetic_note not in notes` hoặc `encouragement_tip not in (None, tip)`, hệ thống ném `ValueError` và rơi về `_fallback_reschedule_empathy()` (`#L2684-L2701`, `generation_tier="tier_3_rule_based"`).
- **Kiểm chứng test thực thi:** `backend/tests/test_ai_service.py#L745-L869` (`test_uf6_grounded_copy_anti_injection_deficit_and_fallback`) xác nhận không rò `task_title`, chống bịa câu/tip ngoài allowlist, kiểm phủ các `trigger_reason` và `deficit_minutes` — **PASS**.

---

## 2. Kết quả Chạy Kiểm thử Độc lập `W4-12A` (Sau bản vá Step 1)

- **Thư mục làm việc:** `backend/`
- **Lệnh thực thi (SQLite offline, không set `PROACTIVE_TEST_PG_URL`):**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_uf1_spoken_contexts.py tests/test_uf6_bugs_and_risks_regression.py tests/test_ai_service.py -q
  ```
- **Kết quả quan sát thực tế:** `109 passed, 1 warning, 23 subtests passed in 20.16s` (exit code `0`).

---

## 3. Ghi nhận Minh bạch 3 Quyết định Kỹ thuật Đã Chốt

1. **`BE-05` #3 — Model Gemini & Provider STT mặc định:**
   - Giữ nguyên chuẩn `GEMINI_FLASH_MODEL="gemini-3.5-flash-lite"` (`backend/app/core/config.py#L70`, đồng bộ với `GEMINI_BACKUP_MODEL="gemini-3.5-flash-lite"`, `GEMINI_UF1_MODEL="gemini-3.5-flash-lite"`, `GEMINI_STT_MODEL="gemini-3.5-flash-lite"`) và `VOICE_STT_PROVIDER="auto"` (`backend/app/core/config.py#L108`, logic điều phối trong `backend/app/services/voice_service.py#L1145-L1166`, trong đó `VOICE_STT_UF5_PREFER_GROQ=True` ưu tiên Groq cho `uf5_checkin`), thay cho cấu hình `3.8-flash` / `groq` trong bản đặc tả ngày 16/11.
2. **`BE-05` #5 — Múi giờ mặc định:**
   - Giữ cố định `DEFAULT_TIMEZONE: str = "Asia/Ho_Chi_Minh"` (`backend/app/core/config.py#L155`) theo xác nhận phạm vi người dùng tại Việt Nam của chủ dự án.
3. **`BE-07` — Endpoint danh sách `GET /api/v1/calendar/sync-jobs`:**
   - Chốt đẩy sang hạng mục **Để sau Tuần 4** vì ứng dụng Mobile hiện không gọi endpoint danh sách. Kiểm chứng thực tế trên `app/api/v1/calendar.py#L837-L915` xác nhận router chỉ đăng ký `GET /api/v1/calendar/sync-jobs/{job_id}` và `POST /api/v1/calendar/sync-jobs/{job_id}/retry` (không có route list `/api/v1/calendar/sync-jobs`).

---

## 4. Ghi nhận Xử lý `[BUG-01]` Liên đới khi Kiểm thử Toàn trình

- Ở lần chạy CI ban đầu trước Step 1, khi chạy full offline CI suite (`pytest -q`) vào khung giờ buổi tối VN (`19:00–21:00`), ca `tests/test_uf2_fixes_and_risks.py::test_risk_uf2_03_60_day_horizon_slice_pending_steps_and_uf6_expand_next_horizon` bị FAIL (`[BUG-01]`) do `reschedule_service._build_normal_proposal_plan()` so sánh `s_start < task_cursor` (`calc_now + 15m`) đối với phiên vừa được UF2 xác nhận trong lead `+5m` khi `trigger_reason == "expand_next_horizon"`.
- Sau bản vá Step 1 tại `backend/app/services/reschedule_service.py#L2985-L2990` (dùng `min_valid_start` bảo toàn các phiên còn hiệu lực `>= calc_now` ở nhánh `expand_next_horizon`) và bổ sung ca pin clock tại `backend/tests/test_uf2_fixes_and_risks.py#L1361-L1462`, toàn bộ full offline SQLite suite đã tái kiểm chứng đạt **`991 passed, 4 skipped, 7 warnings, 23 subtests passed in 466.11s (0:07:46)`** (`0 failed`). Chi tiết đầy đủ xem tại [W4_13_BACKEND_CI_REPORT.md](file:///D:/HCMUS/CT/ProActive/_temp/wt_merge_stt_uf5_uf7/docs/backend/W4_13_BACKEND_CI_REPORT.md).
