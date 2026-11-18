# BÁO CÁO NGHIÊN CỨU & MA TRẬN KIỂM THỬ UF5 & UF6 (TEST MATRIX)

**Người thực hiện:** Doãn Thành (Tester / QA Lead / Algorithm Lead) — ProPlan VA  
**Ngày thực hiện:** 16/11/2026  
**Mã kiểm thử tự động:** [`backend/tests/test_uf5_uf6_matrix.py`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/backend/tests/test_uf5_uf6_matrix.py)  
**Tình trạng kiểm thử:** **26/26 PASSED (100%)**  

---

## 1. ĐỐI CHIẾU NGHIỆP VỤ & ĐÁNH GIÁ TÍNH PHÙ HỢP (FIT & GAP ANALYSIS)

### A. Phân hệ UF5 — Voice Check-in & Outcome Evaluation (Check-in & Đánh giá kết quả)

| Tiêu chuẩn / Đặc tả | Hiện trạng triển khai trên Backend | Đánh giá tính phù hợp |
| :--- | :--- | :--- |
| **5 kết quả Check-in chuẩn** | `completed`, `partial`, `incomplete`, `busy`, `skipped` (kèm `no_respond` khi timeout). | **Đạt chuẩn (100%)**: Được định nghĩa chuẩn tại `SessionOutcome` và bảng `CheckIn`. Tách biệt rõ `session_outcome` với tiến độ `Step` (`step_completed`). |
| **3 Chip Bỏ qua (W3C COGA)** | Hỗ trợ chuẩn `skip_scope`: `postpone_session` (dời sang lúc khác), `skip_session` (bỏ phiên này), `skip_step` (bỏ hẳn bước). | **Đạt chuẩn (100%)**: Schema `UF5SkipChipSchema` bắt buộc `min_length=3, max_length=3` với mô tả và nhãn cụ thể, không phán xét. |
| **Ràng buộc bất biến R01** | Chặn các tổ hợp logic mâu thuẫn: `session_outcome != completed` mà `step_completed=True` $\rightarrow$ 422; `task_completed=True` mà `step_completed=False` $\rightarrow$ 422; cấm `skip_scope` khi outcome không phải `skipped`. | **Đạt chuẩn (100%)**: Được kiểm tra nghiêm ngặt tại `checkin_service._validate_completion_flags`. |
| **Timeout 5 phút & DND** | `run_service.reconcile_expired_checkins` tự động quét các check-in quá hạn `ended_at + 300s`, chuyển `response_state='no_respond'`, nhả khoá `uq_user_live_run`. | **Đạt chuẩn (100%)**: Đã được đối soát lazy trên các API đọc/ghi và giải phóng khoá để phiên sau có thể khởi động ngay. |
| **Điều chỉnh giờ thực tế** | Hỗ trợ API `POST /runs/{id}/actual-corrections` điều chỉnh `actual_work_seconds`, `actual_rest_seconds` và chuyển `actual_source='self_report'`. | **Đạt chuẩn (100%)**: Đáp ứng yêu cầu hiệu chỉnh trung thực của người dùng ADHD. |
| **Giọng nói 2 chiều (Voice Pipeline)** | Dual-tier Backend Voice Pipeline: Groq Whisper STT + Edge-TTS (`vi-VN-HoaiMyNeural`), có fallback trên thiết bị `use_on_device_tts` khi offline. | **Đạt chuẩn (100%)**: Endpoint `POST /voice/uf5-checkin-prompt` và `POST /voice/transcriptions` với `purpose="uf5_checkin"` hoạt động ổn định. |

---

### B. Phân hệ UF6 — Guilt-free Adaptive Rescheduling (Tái lập lịch thích ứng không phán xét)

| Tiêu chuẩn / Đặc tả | Hiện trạng triển khai trên Backend | Đánh giá tính phù hợp |
| :--- | :--- | :--- |
| **7 API Endpoints chuẩn Sheet** | Đầy đủ 7 API:<br>1. `GET /runs/{id}/upcoming-schedule-context` (Dòng 64)<br>2. `POST /proposals` (Dòng 33)<br>3. `GET /proposals/current` (Dòng 34)<br>4. `GET /proposals/{id}` (Dòng 35)<br>5. `POST /proposals/{id}/rebuild` (Dòng 36)<br>6. `POST /proposals/{id}/approve` (Dòng 37)<br>7. `POST /proposals/{id}/reject` (Dòng 38) | **Đạt chuẩn (100%)**: Toàn bộ 7 router đều đăng ký tại `router.py`, dùng schema chuẩn Envelope Pydantic v2. |
| **Grounded Empathy Copy** | Python dựng `allowed_notes`/`allowed_tip`, không gửi task title thô cho LLM (chống prompt injection/PII), sinh câu khích lệ ngắn gọn, không phán xét. | **Đạt chuẩn (100%)**: Tự động chuyển tier: `tier_1_gemini` $\rightarrow$ `tier_2_groq` $\rightarrow$ `tier_3_rule_based` / `python_fallback` khi offline. |
| **Continuation Mode (SY02/R04)** | Hỗ trợ người dùng xin làm tiếp ngay sau phiên (`mode="continue_now"`): thêm phiên nối tiếp, tự động tịnh tiến các phiên sau giữ gap $\ge 900s$. | **Đạt chuẩn (100%)**: Tính toán chính xác thời gian và ràng buộc `ApprovalEnvelope`. |
| **Bảo vệ phiên in_progress** | Thuật toán tịnh tiến thời gian tuyệt đối không dịch chuyển các session đang chạy (`status="in_progress"`). | **Đạt chuẩn (100%)**: Đã được xác minh qua `test_TC_UF6_09`. |
| **Cô lập phạm vi Task** | Chỉ các task nằm trong `allowed_task_ids` mới bị dời; task khác giữ nguyên. | **Đạt chuẩn (100%)**: Đã được xác minh qua `test_TC_UF6_10`. |
| **Thiếu hụt thời gian ($\Delta T$)** | Phát hiện `deficit_minutes > 0` và `feasibility.status="deficit"` khi quỹ giờ rảnh kịch trần trước deadline. | **Đạt chuẩn (100%)**: Đã được xác minh qua `test_TC_UF6_11`. |
| **Atomic Approve & Outbox Sync** | Khi Approve: tăng `schedule_revision`, lưu sessions mới, sinh `CalendarSyncJob` vào hàng chờ outbox trong cùng 1 transaction. | **Đạt chuẩn (100%)**: Đã được xác minh qua `test_TC_UF6_07`. |

---

## 2. MA TRẬN KIỂM THỬ TỔNG HỢP (TEST MATRIX SUITE)

File thực thi: `backend/tests/test_uf5_uf6_matrix.py`

### Nhóm 1: UF5 Check-in & Outcome Evaluation (12 Test Cases)
1. `TC_UF5_01_checkin_outcome_completed`: Check-in Outcome `completed` $\rightarrow$ Đóng session, cập nhật actual work/rest seconds, cập nhật step thành công.
2. `TC_UF5_02_checkin_outcome_incomplete`: Check-in Outcome `incomplete` $\rightarrow$ Session incomplete, step giữ `in_progress`, sẵn sàng cho UF6.
3. `TC_UF5_03_checkin_outcome_busy`: Check-in Outcome `busy` $\rightarrow$ Báo bận đột xuất, session đóng, kích hoạt đề xuất dời lịch.
4. `TC_UF5_04_checkin_skipped_postpone_session`: Check-in Outcome `skipped` kèm chip `postpone_session` (reschedule later).
5. `TC_UF5_05_checkin_skipped_skip_session`: Check-in Outcome `skipped` kèm chip `skip_session` (abandon session).
6. `TC_UF5_06_checkin_skipped_skip_step`: Check-in Outcome `skipped` kèm chip `skip_step` $\rightarrow$ Hủy luôn bước hiện tại (step `abandoned`).
7. `TC_UF5_07_checkin_completion_invariants_R01`: Kiểm tra bất biến R01: Chặn cờ hoàn thành mâu thuẫn (`step_completed` khi chưa xong, `task_completed` khi step chưa xong).
8. `TC_UF5_08_checkin_timeout_reconciliation_5m`: Quét timeout check-in sau 5 phút (300s) $\rightarrow$ Tự động chuyển `no_respond`, nhả khoá live run.
9. `TC_UF5_09_actual_work_correction`: Người dùng tự báo cáo lại số phút thực làm (`ActualCorrectionRequest`), chuyển `actual_source='self_report'`.
10. `TC_UF5_10_checkin_idempotency_and_replay`: Chống gửi trùng request qua `Idempotency-Key`, trả về cùng receipt mà không tạo duplicate.
11. `TC_UF5_11_voice_checkin_prompt_and_transcription`: Sinh prompt check-in giọng nói (3 chip bỏ qua + `undo_timeout=5s`) và nhận diện âm thanh multipart `purpose="uf5_checkin"`.
12. `TC_UF5_12_live_run_lock_released_on_awaiting_checkin`: Ngay khi phiên 1 bước vào `awaiting_checkin`, người dùng được phép Start ngay phiên 2 mà không bị kẹt `uq_user_live_run`.

### Nhóm 2: UF6 Guilt-free Adaptive Rescheduling (12 Test Cases)
13. `TC_UF6_01_upcoming_schedule_context_row64`: `GET /runs/{id}/upcoming-schedule-context` (Dòng 64) trả về bối cảnh phiên sau, gap và sự kiện Google Calendar.
14. `TC_UF6_02_create_proposal_checkin_incomplete`: `POST /proposals` (Dòng 33) tạo đề xuất dời lịch do `checkin_incomplete`, sinh câu khích lệ không phán xét.
15. `TC_UF6_03_create_proposal_continuation_SY02_R04`: `POST /proposals` (Dòng 33) chế độ Continuation xin làm tiếp ngay sau phiên, tự động lùi phiên sau đúng gap $\ge 900s$.
16. `TC_UF6_04_get_current_proposal_row34`: `GET /proposals/current` (Dòng 34) trả về đề xuất đang chờ duyệt hoặc null.
17. `TC_UF6_05_get_proposal_detail_row35`: `GET /proposals/{id}` (Dòng 35) xem chi tiết đề xuất, trả về 404 nếu ID không hợp lệ.
18. `TC_UF6_06_rebuild_proposal_row36`: `POST /proposals/{id}/rebuild` (Dòng 36) tính lại đề xuất theo mốc thời gian thực hiện tại, tăng `proposal_revision=2`.
19. `TC_UF6_07_approve_proposal_row37`: `POST /proposals/{id}/approve` (Dòng 37) duyệt đề xuất, tăng `schedule_revision`, cập nhật sessions và sinh `CalendarSyncJob`.
20. `TC_UF6_08_reject_proposal_row38`: `POST /proposals/{id}/reject` (Dòng 38) từ chối đề xuất, giữ nguyên lịch cũ (`requested_state='unchanged'`).
21. `TC_UF6_09_preserve_in_progress_session`: Thuật toán bảo vệ: Phiên đang chạy `in_progress` tuyệt đối không bị dời hoặc thay đổi.
22. `TC_UF6_10_isolate_unallowed_tasks`: Thuật toán cô lập: Các task ngoài `allowed_task_ids` hoàn toàn không bị ảnh hưởng.
23. `TC_UF6_11_deficit_minutes_detection`: Phát hiện thiếu hụt thời gian khi quỹ giờ rảnh không đủ trước hạn chót (deadline).
24. `TC_UF6_12_approve_reject_idempotency`: Idempotency trên Approve / Reject proposal, gửi trùng không tăng revision 2 lần.

### Nhóm 3: E2E Tích hợp Toàn trình UF5 $\rightarrow$ UF6 (2 Test Cases)
25. `TC_E2E_01_incomplete_to_adaptive_reschedule_flow`: Toàn trình: Run kết thúc $\rightarrow$ Check-in `incomplete` $\rightarrow$ Lấy Upcoming Context $\rightarrow$ Tạo Proposal $\rightarrow$ Duyệt Proposal $\rightarrow$ Lịch mới được lưu và đồng bộ Google Calendar.
26. `TC_E2E_02_skipped_postpone_session_rebuild_flow`: Toàn trình: Check-in `skipped` chip `postpone_session` $\rightarrow$ Tạo Proposal dời phiên $\rightarrow$ Rebuild tái tính toán sau thời gian chờ $\rightarrow$ Duyệt Proposal mới (`revision=2`).

---

## 3. KẾT QUẢ THỰC THI KIỂM THỬ (TEST EXECUTION EVIDENCE)

```
============================= test session starts =============================
platform win32 -- Python 3.12.15, pytest-9.2.0, pluggy-1.7.0
rootdir: E:\code\Project\CT\Virtual Assistant\virtual-assistant-for-ADHD\backend
configfile: pyproject.toml
plugins: anyio-4.15.1, asyncio-1.4.1
collected 26 items

tests/test_uf5_uf6_matrix.py::test_TC_UF5_01_checkin_outcome_completed PASSED [  3%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_02_checkin_outcome_incomplete PASSED [  7%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_03_checkin_outcome_busy PASSED [ 11%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_04_checkin_skipped_postpone_session PASSED [ 15%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_05_checkin_skipped_skip_session PASSED [ 19%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_06_checkin_skipped_skip_step PASSED [ 23%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_07_checkin_completion_invariants_R01 PASSED [ 26%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_08_checkin_timeout_reconciliation_5m PASSED [ 30%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_09_actual_work_correction PASSED [ 34%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_10_checkin_idempotency_and_replay PASSED [ 38%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_11_voice_checkin_prompt_and_transcription PASSED [ 42%]
tests/test_uf5_uf6_matrix.py::test_TC_UF5_12_live_run_lock_released_on_awaiting_checkin PASSED [ 46%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_01_upcoming_schedule_context_row64 PASSED [ 50%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_02_create_proposal_checkin_incomplete PASSED [ 53%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_03_create_proposal_continuation_SY02_R04 PASSED [ 57%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_04_get_current_proposal_row34 PASSED [ 61%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_05_get_proposal_detail_row35 PASSED [ 65%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_06_rebuild_proposal_row36 PASSED [ 69%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_07_approve_proposal_row37 PASSED [ 73%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_08_reject_proposal_row38 PASSED [ 76%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_09_preserve_in_progress_session PASSED [ 80%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_10_isolate_unallowed_tasks PASSED [ 84%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_11_deficit_minutes_detection PASSED [ 88%]
tests/test_uf5_uf6_matrix.py::test_TC_UF6_12_approve_reject_idempotency PASSED [ 92%]
tests/test_uf5_uf6_matrix.py::test_TC_E2E_01_incomplete_to_adaptive_reschedule_flow PASSED [ 96%]
tests/test_uf5_uf6_matrix.py::test_TC_E2E_02_skipped_postpone_session_rebuild_flow PASSED [100%]

======================== 26 passed in 70.31s (0:01:10) ========================
```

---

## 4. KẾT LUẬN & ĐỀ XUẤT CHO SPRINT TUẦN 3

1. **Về tính phù hợp:** Nhánh backend hiện tại đã **đáp ứng 100% đặc tả kỹ thuật và nghiệp vụ của UF5 và UF6**. Các bất biến cốt lõi (R01, R04, R05, R06, R10), hợp đồng API Sheet (Dòng 29–31, 33–38, 64), và hành vi xử lý ngoại lệ đều hoạt động chính xác.
2. **Bộ test ma trận:** Đã hoàn thành và lưu trữ tại [`backend/tests/test_uf5_uf6_matrix.py`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/backend/tests/test_uf5_uf6_matrix.py), bao phủ 26 kịch bản toàn diện, sẵn sàng bàn giao cho đội ngũ nghiệm thu và CI/CD.
