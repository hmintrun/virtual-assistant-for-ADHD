# BÁO CÁO TEST SUITE & ĐÁNH GIÁ QA/QC - PHẦN UF3 & UF4

**Người thực hiện:** Doãn Thành (QA Lead / Tester)  
**Dự án:** ProPlan VA - Virtual Assistant for ADHD  
**Phạm vi:** UF3 (Focus Session & DND) & UF4 (Cất suy nghĩ bằng Raw Audio)  
**Tập tin kiểm thử Backend:** `backend/tests/test_uf3_uf4_matrix.py`  
**Tập tin kiểm thử Mobile:** `mobile/test/focus_provider_uf3_uf4_test.dart`  
**Tài liệu tham chiếu:** `Project.md §7.2, §10`, `AGENTS.md §2.2`, `AlgorithmDes.pdf`

---

## 🌟 PHẦN 1: TỔNG HỢP CÁC PHÁT HIỆN LỖI & RỦI RO CẦN FEEDBACK (QA BUGS & GAPS)

Qua quá trình biên soạn và chạy trực tiếp bộ test tự động trên Backend (`pytest`) và Mobile (`flutter test`), bộ phận QA phát hiện các vấn đề cốt lõi sau:

### 1. Lỗi Validation Backend (Đã phát hiện qua Test chạy thực tế)
- **Bug 1 (`TC-EDGE-01`):** Trường `client_thought_id` nhận chuỗi khoảng trắng `"   "` và trả về `201 Created` thay vì `422 Unprocessable Entity`. Cần thêm validator strip whitespace.
- **Bug 2 (`TC-UF4-04`):** Schema `ThoughtCreateRequest` đang khóa cứng `attempt_count: int = Field(ge=1, le=3)`. Tuy nhiên, nghiệp vụ UF4 quy định sau 3 lần lỗi ghi âm (lần 4) sẽ chuyển sang **Text Fallback**. Khi mobile gửi `attempt_count=4` với `input_type="text"`, backend ném lỗi `422` chặn đứng luồng fallback! Cần nới lỏng `le=5` hoặc cho phép nhánh text mang attempt count lớn hơn.
- **Bug 3 (`TC-UF4-05`):** Khi client gửi `input_type="audio"`, backend vẫn cho phép đính kèm trường `text_content` mà không chặn. Điều này vi phạm nguyên tắc bảo vệ Zero AI trong phiên (nguy cơ app lén gọi STT gửi chữ lên). Cần có model validator: `if input_type == 'audio' and text_content is not None: raise ValueError`.
- **Bug 4 (`SessionStartRequest`):** Router `POST /api/v1/sessions/{id}/start` đang dùng `ApiModel` với `extra="forbid"`. Nếu mobile gửi kèm `dnd_state` hoặc thông tin thiết bị phụ thì bị ném `422`. Thiết bị bắt buộc phải gọi endpoint riêng `PUT /api/v1/devices/{device_id}/state`.

### 2. Xung đột Nền tảng Hệ điều hành trên Mobile (Android vs iOS)
- **Quyền DND trên Android 10+:** Bắt buộc phải điều hướng người dùng ra màn hình cài đặt hệ thống (`Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS`). App không được crash nếu user không cấp quyền.
- **Giới hạn của Apple iOS:** iOS không cho phép ứng dụng bên thứ 3 tự bật/tắt Focus Mode (DND). Mobile đã xử lý graceful bypass trên iOS, nhưng cần ghi chú rõ trong spec.
- **Android Doze Mode & Báo thức:** Trên Android 12+, nếu không xin quyền `SCHEDULE_EXACT_ALARM`, chuông báo thức hết giờ có thể bị trễ đến 15 phút khi máy tắt màn hình, vi phạm cam kết dung sai $\le 1$ giây.

---

## 📋 PHẦN 2: DANH MỤC 95 TEST CASES CHI TIẾT (UF3 & UF4)

### Module 1: UF3 - Backend API & Business Logic (20 Test Cases)
1. `TC_BE_01`: Start Session đúng giờ trong Grace Window (scheduled_start + 5p) $\rightarrow$ 201 Created.
2. `TC_BE_02`: Start Session sát nút Grace Window (899 giây) $\rightarrow$ 201 Created.
3. `TC_BE_03`: Chặn Start Session quá hạn Grace Window (901 giây) $\rightarrow$ 409/422 Replan.
4. `TC_BE_04`: Kiểm tra ràng buộc Min Inter-Session Gap (900s) giữa 2 phiên $\rightarrow$ 409 Conflict.
5. `TC_BE_05`: Idempotency Key bảo vệ lặp Request Start $\rightarrow$ Trả về cùng Run ID, không nhân đôi.
6. `TC_BE_06`: Xung đột Payload với cùng Idempotency Key $\rightarrow$ 409 IDEMPOTENCY_CONFLICT.
7. `TC_BE_07`: Start Session với Stale Schedule Revision $\rightarrow$ 409 REVISION_CONFLICT.
8. `TC_BE_08`: Chống Start đồng thời 2 Session khác nhau của 1 User $\rightarrow$ 409 CONCURRENT_RUN_NOT_ALLOWED.
9. `TC_BE_09`: Micro-task 120s không được cộng vào Session planned duration / actual work.
10. `TC_BE_10`: Ghi nhận Batch Run Events (Pause, Resume) và kiểm tra monotonic work elapsed.
11. `TC_BE_11`: Chặn sự kiện Run Event có Timestamp phi lý (Clock skew > 5 phút) $\rightarrow$ 422.
12. `TC_BE_12`: Kết thúc Session Run chủ động trước giờ với `actual_source='timer_proxy'`.
13. `TC_BE_13`: Chặn trường `actual_source` rác hoặc null $\rightarrow$ 422 Unprocessable Entity.
14. `TC_BE_14`: Lấy thông tin Active Run hiện tại qua `GET /api/v1/runs/active`.
15. `TC_BE_15`: Lấy Active Run khi không có phiên nào chạy $\rightarrow$ HTTP 200, data = null.
16. `TC_BE_16`: Tạo và truy vấn danh sách Reminders theo Device $\rightarrow$ `dedupe_key` chuẩn.
17. `TC_BE_17`: Hủy bỏ Reminders khi Session đã được Start sớm $\rightarrow$ Trạng thái cancelled.
18. `TC_BE_18`: Reconcile CheckIn hết hạn (Sau ended_at + 300s) $\rightarrow$ `response_state='no_respond'`.
19. `TC_BE_19`: Báo cáo trạng thái thiết bị qua `PUT /api/v1/devices/{device_id}/state`.
20. `TC_BE_20`: Chặn thiết bị khác gửi báo cáo sở hữu cho Run không thuộc về nó $\rightarrow$ 404/403/409.

### Module 2: UF3 - Mobile UI/UX & Native Engine (25 Test Cases)
21. `TC_MB_01`: Chuẩn W3C COGA: Không quá 3-5 thành phần tương tác trên màn hình FocusScreen.
22. `TC_MB_02`: 1-Step UI: Chỉ hiển thị duy nhất 1 bước hiện tại, ẩn toàn bộ các bước khác.
23. `TC_MB_03`: Nhãn nút hành động cụ thể: "Bắt đầu đếm giờ 25p", không dùng OK/Tiếp tục.
24. `TC_MB_04`: Hiển thị gợi ý Micro-task 2 phút trước phiên không bắt buộc hoàn thành.
25. `TC_MB_05`: Nút bỏ qua Micro-task chuyển ngay sang trạng thái Ready.
26. `TC_MB_06`: Giữ sáng màn hình nhẹ (`WakelockPlus`) khi timer đang đếm ngược.
27. `TC_MB_07`: Tự động bật DND (Không làm phiền) trên Android khi vào phiên.
28. `TC_MB_08`: Lưu Snapshot DND ban đầu của user, không tự tắt DND vốn có của user sau phiên.
29. `TC_MB_09`: Xử lý khi chưa cấp quyền DND: Hiện hướng dẫn, timer vẫn chạy bình thường.
30. `TC_MB_10`: Đồng hồ đếm ngược và thanh tiến độ chạy mượt mà từng giây.
31. `TC_MB_11`: Chuyển Segment Work sang Rest nội bộ Pomodoro, DND vẫn giữ xuyên Rest.
32. `TC_MB_12`: Chuông báo thức Exact Alarm reo đúng giây hết giờ (dung sai $\le 1$ giây).
33. `TC_MB_13`: QUY TẮC BẤT BIẾN: Hết giờ KHÔNG tự động complete Session hay Task.
34. `TC_MB_14`: Trạng thái Late Start khi quá 15 phút Grace: Có 2 lựa chọn Bắt đầu / Hoãn lại.
35. `TC_MB_15`: Bấm nút Back khi đang chạy phiên bị chặn hoặc cảnh báo nhẹ.
36. `TC_MB_16`: Khôi phục Timer sau khi App bị ẩn vào Background (App Resume).
37. `TC_MB_17`: Khôi phục phiên sau khi App bị hệ thống Kill (OOM Recovery từ Local DB).
38. `TC_MB_18`: Tắt màn hình điện thoại (Screen Off) timer vẫn tính đúng số giây trôi qua.
39. `TC_MB_19`: Báo thức reo khi đang khóa màn hình (Full-screen Intent qua Keyguard).
40. `TC_MB_20`: Offline Resilience: Duy trì đếm giờ và báo thức khi mất mạng hoàn toàn.
41. `TC_MB_21`: Tự động tắt DND khi người dùng chủ động kết thúc phiên và chọn nghỉ.
42. `TC_MB_22`: Báo việc đột xuất chuyển ngay sang UF6 để tái lập lịch.
43. `TC_MB_23`: Giao diện Dark Mode tương phản cao đạt chuẩn WCAG AAA.
44. `TC_MB_24`: Nút "Cất suy nghĩ" luôn nằm cố định trong vùng với tới của ngón cái.
45. `TC_MB_25`: Tự động giải phóng Wakelock khi phiên kết thúc để tiết kiệm pin.

### Module 3: UF4 - Backend Thought Lifecycle (15 Test Cases)
46. `TC_TH_BE_01`: Capture Thought audio thô thành công $\rightarrow$ 201 Created, `capture_state='saved'`.
47. `TC_TH_BE_02`: BẢO VỆ ZERO AI: Không gọi STT hay LLM khi capture thought (< 100ms).
48. `TC_TH_BE_03`: Khử trùng Client Thought ID khi retry mạng, không sinh bản ghi trùng.
49. `TC_TH_BE_04`: Capture Thought dạng Text Fallback sau các lần lỗi audio.
50. `TC_TH_BE_05`: Chặn Thought có input_type='audio' nhưng lại gửi kèm text_content.
51. `TC_TH_BE_06`: Capture Thought ngoài phiên với `source_run_id = null`.
52. `TC_TH_BE_07`: Phân trang danh sách Thoughts theo Cursor Pagination.
53. `TC_TH_BE_08`: Lấy chi tiết Thought theo ID qua `GET /api/v1/thoughts/{id}`.
54. `TC_TH_BE_09`: Xóa Thought (Undo trong 5s) với `expected_thought_revision`.
55. `TC_TH_BE_10`: Chặn xóa Thought khi Revision bị lệch $\rightarrow$ 409 REVISION_CONFLICT.
56. `TC_TH_BE_11`: Chuyển Thought thành Draft (UF1) sau phiên qua `POST /thoughts/{id}/draft`.
57. `TC_TH_BE_12`: Xử lý STT Fallback khi chuyển Thought sang Draft nếu Whisper lỗi.
58. `TC_TH_BE_13`: Chặn chuyển 1 Thought thành nhiều Draft đồng thời $\rightarrow$ 409 Conflict.
59. `TC_TH_BE_14`: Bảo vệ quyền sở hữu Thought: User B không xem/xóa được Thought User A.
60. `TC_TH_BE_15`: `converted_task_id` chỉ xuất hiện sau khi confirm UF2 thành công.

### Module 4: UF4 - Mobile Push-to-Talk & Hộp nháp (18 Test Cases)
61. `TC_TH_MB_01`: Bấm giữ nút "Cất suy nghĩ" nói trong 3 giây lưu file audio `.m4a` local.
62. `TC_TH_MB_02`: Timer UF3 chạy liên tục không bị khựng (60fps) khi đang bấm giữ nói.
63. `TC_TH_MB_03`: Hiển thị SnackBar "Đã cất vào hộp nháp" kèm nút Hoàn tác trong 5 giây.
64. `TC_TH_MB_04`: Bấm nút Hoàn tác trong 5 giây xóa Thought ngay lập tức.
65. `TC_TH_MB_05`: SnackBar tự động ẩn sau 5 giây nếu người dùng không tương tác.
66. `TC_TH_MB_06`: Hoàn tác tuyệt đối không dùng AlertDialog xác nhận lại.
67. `TC_TH_MB_07`: Rung haptic nhẹ và nhắc thử lại lần 1 khi lỗi thu âm.
68. `TC_TH_MB_08`: Rung haptic nhắc thử lại lần 2 khi lỗi thu âm tiếp diễn.
69. `TC_TH_MB_09`: Tự động kích hoạt Text Fallback sau 3 lần lỗi ghi âm liên tiếp.
70. `TC_TH_MB_10`: Nhập văn bản nhanh trong Text Fallback và lưu thành công.
71. `TC_TH_MB_11`: Nút bỏ qua Text Fallback đóng ô nhập và giữ timer chạy.
72. `TC_TH_MB_12`: File âm thanh lưu local có dung lượng $> 0$ bytes, nghe lại rõ tiếng.
73. `TC_TH_MB_13`: Không gửi đường dẫn file nội bộ `file:///...` lên API Backend.
74. `TC_TH_MB_14`: Ghi âm bình thường khi mất kết nối Internet (Offline Thought Capture).
75. `TC_TH_MB_15`: Tự động đẩy outbox sync Thoughts lên Backend khi có mạng trở lại.
76. `TC_TH_MB_16`: Nghe lại bản ghi âm trong màn hình Hộp nháp (Drafts Screen).
77. `TC_TH_MB_17`: Nút "Tạo việc từ ghi âm này" gọi STT và đẩy sang màn hình UF1.
78. `TC_TH_MB_18`: Đặt tên file bằng UUID riêng biệt, không ghi đè giữa các lần cất suy nghĩ.

### Module 5: Testcase "Lạ" & Edge Cases Đặc thù (17 Test Cases)
79. `TC_EDGE_01`: Bấm giữ Push-to-Talk siêu ngắn (< 0.2s) $\rightarrow$ Hủy âm thầm, không sinh rác.
80. `TC_EDGE_02`: Bấm giữ Push-to-Talk quá lâu (> 60s) $\rightarrow$ Tự động ngắt, rung nhẹ và lưu an toàn.
81. `TC_EDGE_03`: Hyperactive Tapping: Nhấp nhả nút Cất suy nghĩ liên tục 10 lần $\rightarrow$ Debounce an toàn.
82. `TC_EDGE_04`: Chuông hết giờ reo đúng lúc đang bấm giữ nói $\rightarrow$ Dừng audio, chuông reo mượt.
83. `TC_EDGE_05`: Cuộc gọi điện thoại đến khi đang nói $\rightarrow$ Lưu phần đã thu, không crash app.
84. `TC_EDGE_06`: Rút tai nghe Bluetooth khi đang ghi âm $\rightarrow$ Tự chuyển mic thoại không đơ.
85. `TC_EDGE_07`: Bấm nút Hoàn tác sát nút ở giây thứ 4.99s $\rightarrow$ Xử lý an toàn, không ném exception.
86. `TC_EDGE_08`: Chỉnh lùi giờ hệ thống điện thoại khi đang đếm $\rightarrow$ Dùng Monotonic clock chống gian lận.
87. `TC_EDGE_09`: Thay đổi Timezone giữa phiên khi bay qua nước khác $\rightarrow$ Giữ nguyên mốc UTC kết thúc.
88. `TC_EDGE_10`: Thu hồi quyền DND trong Settings khi phiên đang chạy $\rightarrow$ App cập nhật cờ, không crash.
89. `TC_EDGE_11`: Chế độ siêu tiết kiệm pin (Aggressive Doze Mode) $\rightarrow$ Exact Alarm vẫn kêu đúng giây.
90. `TC_EDGE_12`: Bấm Start sớm 4 tiếng $\rightarrow$ Backend kiểm tra CalendarContext bận để chặn.
91. `TC_EDGE_13`: 2 thiết bị mở cùng tài khoản bấm Start cùng lúc $\rightarrow$ 1 bên 201, 1 bên 409 Conflict.
92. `TC_EDGE_14`: Bộ nhớ máy bị đầy (Disk Full) khi lưu audio $\rightarrow$ Chuyển sang Text Fallback.
93. `TC_EDGE_15`: Micro bị ứng dụng ngầm khác chiếm quyền $\rightarrow$ Báo lỗi nhẹ haptic nhắc kiểm tra mic.
94. `TC_EDGE_16`: Mở lại app khi phiên đã hết giờ từ 2 tiếng trước $\rightarrow$ Tự khôi phục DND, đưa vào CheckIn.
95. `TC_EDGE_17`: Spam tap nút Hoàn tác liên tục $\rightarrow$ Cờ `_undoneThoughtIds` chỉ xóa 1 lần duy nhất.

---

## 🛠️ PHẦN 3: HƯỚNG DẪN THỰC THI KIỂM THỬ (TEST EXECUTION GUIDE)

### 1. Chạy Backend Tests (`uv run pytest`)
Mở PowerShell tại thư mục `backend`:
```powershell
Set-Location "e:\code\Project\CT\Virtual Assistant\virtual-assistant-for-ADHD\backend"
uv run pytest tests/test_uf3_uf4_matrix.py -v
```

### 2. Chạy Mobile Tests (`flutter test`)
Mở PowerShell tại thư mục `mobile`:
```powershell
Set-Location "e:\code\Project\CT\Virtual Assistant\virtual-assistant-for-ADHD\mobile"
flutter test test/focus_provider_uf3_uf4_test.dart
```

### 3. Kiểm thử Phần cứng thực tế qua ADB (Thiết bị Android / Emulator)
- **Kiểm tra Doze Mode:**
  ```powershell
  adb shell dumpsys battery unplug
  adb shell dumpsys deviceidle force-idle
  # Sau khi test xong:
  adb shell dumpsys battery reset
  ```
- **Kiểm tra mất mạng đột ngột (Offline Resilience):**
  ```powershell
  adb shell svc wifi disable
  adb shell svc data disable
  # Sau khi test xong:
  adb shell svc wifi enable
  adb shell svc data enable
  ```
- **Giả lập hệ thống Kill App (Low Memory Killer / OOM):**
  ```powershell
  adb shell am kill com.melbel.virtual_assistant_adhd
  ```
