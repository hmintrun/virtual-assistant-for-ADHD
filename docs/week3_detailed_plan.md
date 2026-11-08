# Kế hoạch tuần 3 — Hoàn thiện UF5–UF7, Tích hợp Google Calendar 2 chiều & Nghiệm thu toàn trình

**Thời gian:** 10–16/11/2026, giờ Việt Nam (UTC+7).  
**Mục tiêu:** Hoàn thiện toàn bộ giai đoạn Thích ứng & Tái lập lịch (Adaptation Phase: UF5 Voice Check-In, UF6 Adaptive Rescheduling, UF7 Personalization & Buffer Evolution); tích hợp Google Calendar 2 chiều bền vững (Mốc C01–C04); dọn sạch nợ kỹ thuật RAM Cache và nghiệm thu E2E trên Android APK thật cùng cơ sở dữ liệu PostgreSQL Staging.

Kế hoạch tập trung vào nội dung mỗi người cần làm, output phải bàn giao và kết quả chung khi ghép tất cả phần việc. Quy tắc nghiệp vụ tuân thủ [Project.md](../Project.md), [AGENTS.md](../AGENTS.md), [WEEK3_SPRINT_HANDOFF.md](backend/WEEK3_SPRINT_HANDOFF.md) và [schema Backend](backend/schema.md).

---

## 1. Tổng kết tuần trước (Tuần 2)

Phần tổng kết ghi nhận kết quả tuần 2 đã thực hiện đến ngày 07/11. Các hạng mục chưa hoàn tất hoặc nợ kỹ thuật được tiếp nối trong tuần này.

| Phân hệ | Kết quả đã có (Tuần 2) | Phần cần tiếp nối cho Tuần 3 |
| --- | --- | --- |
| **Backend Core & Database** | Có đủ 11 models ORM (SQLAlchemy 2.0), 7 migrations; logic 3 tầng Task–Step–Session, nguồn giờ DraftAvailability riêng; test suite 213 tests pass trên SQLite. | Dọn sạch 100% RAM cache trong `draft_service.py` (`_FAKE_DRAFTS_BY_OWNER`); chuyển sang `PlanningDraftModel` và `command_receipts`; kiểm chứng 216+ tests trên Docker PostgreSQL 16 Staging. |
| **UF3 — Phiên tập trung** | Đã hoàn thiện service Start/Active/Detail/Events/End, 1 live run/user (`uq_user_live_run`), device/reminder offsets 15/10/5p; Micro-task 2 phút khởi động; Backend test đạt 100%. | Hoàn thiện router Check-in và lazy timeout 300s tự giải phóng DND/Wakelock; Mobile hoàn tất kết nối API thật trên Android. |
| **UF4 — Cất suy nghĩ Raw Audio** | Service lưu Thought metadata, Push-to-Talk raw audio `.m4a`, cấm gọi STT/LLM trong phiên, retry 3 lần $\rightarrow$ text fallback; chuyển Thought thành Draft sau phiên; test đạt 100%. | Ghép nút PTT trên APK thật; kiểm tra SnackBar Undo 5 giây; gọi Groq Whisper STT chuyển Thought thành Draft UF1 sau phiên. |
| **UF5 — Voice Check-In** | Đã phân tích kiến trúc 5 nhánh kết quả, DTO và state transitions; có bảng `check_ins` và partial index `uq_run_final_checkin`. | Triển khai Router/Service Voice Check-in 2 chiều (`edge-tts` prompt + Groq Whisper STT); 3 chip lựa chọn khi Bỏ qua kèm Undo 5s; tự động nhả DND sau 5 phút nếu không phản hồi (`no_response`). |
| **UF6 — Tái lập lịch thích ứng** | Đã có model `RescheduleProposal`, hàm Python `shift_elapsed_sessions` và logic tính $\Delta T_{\text{deficit}}$. | Triển khai 5 API endpoints dời lịch; cơ chế "Đóng app không phải là đồng ý" (`pending_approval`) và "Tịnh tiến giờ thực tế khi mở lại app" (`real_time_anchor`); prompt Gemini Flash sinh lời giải thích ấm áp không phán xét. |
| **UF7 — Cài đặt & Buffer** | Đã có model `Availability`, `UserPreference`, `AdaptationSuggestion`; công thức A20 Median Step. | Triển khai API Cài đặt; worker định kỳ `adaptation_worker.py` chạy tối Chủ nhật quét MA 7–10 phiên, sinh gợi ý khi lệch $\ge 0.20$; độc lập phiên đang chạy. |
| **Google Calendar & Auth** | Đã phân tích kiến trúc 4 mốc C01–C04, mã hóa Fernet AES-256, Outbox Pattern và ETag 412. | Thay thế stub `resolve_dev_current_user` bằng Google Sign-In $\rightarrow$ JWT thật; tạo Secondary Calendar "ProPlan VA Focus"; worker đồng bộ sự kiện 2 chiều. |

---

## 2. Bảng phân công công việc từng người (8 Thành viên)

Mỗi công việc có đúng **một người chịu trách nhiệm chính** theo dõi đến cùng. Người này chủ động phối hợp, kiểm tra và bàn giao đầu ra theo tiêu chuẩn DoD.

| Thành viên | Nội dung công việc cần làm trong Tuần 3 | Output phải bàn giao |
| --- | --- | --- |
| **Minh Trung (mt)** — BA, Prompt và Git | Điều phối review và tích hợp toàn bộ code tuần 3 vào nhánh `dev`; rà soát toàn bộ System Prompt cho Gemini Flash tại UF5 và UF6 (lời động viên ấm áp, đồng cảm, cấm phán xét, cấm đổ lỗi cho người ADHD).<br><br>Giám sát trải nghiệm người dùng W3C COGA trên các màn hình mới (3 chip Skip, so sánh lịch cũ/mới, cài đặt cá nhân hóa); bảo đảm mật độ tương tác tối đa 3–5 phần tử.<br><br>Tổ chức nghiệm thu E2E toàn trình từ UF1 đến UF7; viết tài liệu hướng dẫn demo và biên bản nghiệm thu cuối kỳ. | Các thay đổi được merge vào `dev` sạch sẽ; bộ Prompt tiếng Việt chuẩn cho UF5/UF6; báo cáo rà soát W3C COGA đạt chuẩn; kịch bản demo và biên bản nghiệm thu đồ án. |
| **Quốc Hưng (vqh)** — Backend Lead và Thuật toán | Chịu trách nhiệm thuật toán dời lịch thích ứng UF6 (`rescheduling.py`): dịch chuyển ô lịch, bảo vệ các Task cố định khác (`allowed_task_ids`), tính toán số phút thiếu hụt $\Delta T_{\text{deficit}}$ so với Deadline.<br><br>Chịu trách nhiệm thuật toán tiến hóa hệ số đệm A20 Median Step trong UF7: lấy trung vị $N=15$ bước việc gần nhất, kẹp $[1.0, 2.0]$, làm tròn 0.05; xây dựng worker chạy định kỳ `adaptation_worker.py`.<br><br>Chịu trách nhiệm thuật toán FreeBusy Subtraction cho Google Calendar (Mốc C03): $T_{\text{free}} = T_{\text{user}} - T_{\text{busy}}$ kèm bộ lọc bảo vệ sự kiện của ứng dụng (Own-Event Protection). | Service UF6 và UF7 hoàn chỉnh, có unit test bao phủ $\ge 90\%$; thuật toán tính toán chính xác $\le 1\text{s}$, không bị lệch múi giờ; báo cáo kiểm thử thuật toán dời lịch. |
| **Kim Cương (kc)** — API Router và Auth Bridge | Chịu trách nhiệm xây dựng API routers UF5: `/sessions/{id}/checkin-prompt`, `/checkin`, `/skip`, `/undo-skip` và `/timeout-check`.<br><br>Chịu trách nhiệm routers UF6: `/proposals/reschedule`, `/current`, `/{id}/approve`, `/{id}/reject` và `/adjust-natural-language`.<br><br>Chịu trách nhiệm routers UF7: `/settings/preferences`, `/adaptation-suggestions` và `/{id}/apply`.<br><br>Triển khai Auth Bridge (Mốc C01): tiếp nhận Google ID Token, verify qua `google-auth`, phát hành JWT Access/Refresh Token thay thế stub `resolve_dev_current_user`. | 12 endpoints API mới đăng ký đầy đủ trên FastAPI, tài liệu Swagger/OpenAPI cập nhật chuẩn Pydantic v2; integration tests cho router pass 100%; Google Auth JWT hoạt động ổn định. |
| **Trang Tín (tn)** — Database và Tầng lưu trữ | Chịu trách nhiệm dọn sạch 100% nợ kỹ thuật RAM cache trong `draft_service.py` (`_FAKE_DRAFTS_BY_OWNER`, `_CLIENT_DRAFT_INDEX`, `_CREATE_IDEMPOTENCY_STORE`), chuyển toàn bộ sang `PlanningDraftModel` và bảng `command_receipts`.<br><br>Viết migration Alembic `20261108_01`: drop bảng `planning_parts` cũ, bổ sung composite index cho `planning_drafts`, `session_runs` và `reminders`.<br><br>Xây dựng `CalendarSyncWorker` chạy ngầm cho Outbox Pattern (Mốc C04): đẩy Session lên Google Calendar kèm Header `If-Match` chống xung đột ETag 412.<br><br>Thiết lập container Docker PostgreSQL 16 Staging và chạy kiểm thử 216+ tests trên DB thật. | `draft_service.py` sạch nợ kỹ thuật, không còn biến RAM dict; migration chạy mượt trên cả SQLite và Postgres; PostgreSQL Staging pass 100% test suite; Outbox sync worker xử lý tốt ETag 412. |
| **Thanh An (tan)** — Mobile UI và APK | Xây dựng màn hình Voice Check-in UF5 trên Flutter: hiển thị văn bản kết hợp phát âm thanh `edge-tts`, 3 chip lựa chọn khi Bỏ qua (`[Hoãn làm sau]`, `[Bỏ phiên này]`, `[Bỏ toàn bộ bước]`), thanh SnackBar Hoàn tác trong 5 giây.<br><br>Xây dựng màn hình So sánh Kế hoạch UF6: thẻ trực quan so sánh Lịch Cũ vs. Lịch Mới, thông báo thiếu giờ $\Delta T_{\text{deficit}}$, nút Duyệt và nút Tùy chỉnh.<br><br>Xây dựng màn hình Cài đặt UF7: điều chỉnh thời lượng phiên/nghỉ, chọn giọng đọc, banner gợi ý cập nhật hệ số đệm nhẹ nhàng.<br><br>Đóng gói bản build APK Android nghiệm thu hoàn thiện toàn trình. | Toàn bộ UI mới trên Flutter đạt chuẩn 60 FPS, không có lỗi `dart analyze`; SnackBar Undo 5s mượt mà; file APK nghiệm thu kèm hướng dẫn cài đặt và cấu hình backend. |
| **Quang Khải (kai)** — Android, Native và Âm thanh | Xây dựng module native Android tự động tắt DND và hủy Wakelock sau 300 giây (5 phút) lazy timeout khi người dùng không phản hồi check-in, kể cả trong trạng thái màn hình tắt hoặc Doze Mode.<br><br>Tích hợp thư viện `google_sign_in` trên Flutter, xử lý luồng đăng nhập 1 chạm lấy `id_token` gửi về Backend.<br><br>Xây dựng pipeline phát audio base64 câu hỏi Check-in từ backend, fallback về `flutter_tts` khi offline.<br><br>Bảo đảm độ tin cậy của chuông báo thức hết giờ phiên $\le 1\text{s}$ trên các dòng máy Android phổ biến (Samsung, Xiaomi, Pixel). | Native DND receiver tự tắt sau 300s chuẩn xác; Google Sign-In hoạt động trơn tru trên Android thật; audio check-in phát rõ ràng; bằng chứng log/video trên thiết bị thật. |
| **Quốc Thịnh (qt)** — Đồng bộ Thời gian và Offline Cache | Xây dựng cơ chế cache `RescheduleProposal` và `OfficialSchedule` cục bộ bằng SQLite trên thiết bị di động.<br><br>Triển khai thuật toán auto-shift giờ đề xuất cục bộ khi người dùng mở lại app trong trạng thái Offline (mất mạng).<br><br>Kiểm tra đối soát đồng bộ thời gian thực giữa native clock Android, Flutter Dart và Server UTC; bảo đảm định dạng thời gian ISO-8601 kèm timezone offset `+07:00`.<br><br>Quản lý outbox queue trên Mobile để gửi lại các phản hồi Check-in và Thought metadata khi có mạng trở lại. | Cache cục bộ hoạt động trơn tru; mở app offline vẫn tịnh tiến lịch trôi qua chính xác; không lệch múi giờ giữa client và server; queue gửi lại không bị nhân bản bản ghi. |
| **Doãn Thành (thành)** — QA và Nghiệm thu Toàn trình | Xây dựng bộ ca kiểm thử tự động E2E cho chuỗi thích ứng: Hết giờ $\rightarrow$ Check-in 5 nhánh $\rightarrow$ Dời lịch UF6 $\rightarrow$ Cài đặt UF7.<br><br>Kiểm thử chuyên sâu kịch bản Bỏ qua (Skip) với 3 chip và bấm Hoàn tác trong 5 giây.<br><br>Chạy kịch bản kiểm thử 34 bước Console E2E trên cơ sở dữ liệu PostgreSQL 16 Staging.<br><br>Thực hiện đo đạc thực tế trên ma trận thiết bị Android (đo Doze Mode, độ trễ chuông báo, tắt DND sau 300s, không STT trong UF4).<br><br>Lập biên bản nghiệm thu cuối kỳ và quay video minh chứng chạy thật trên máy Android. | Bộ test tự động E2E mới; biên bản kiểm thử PostgreSQL Staging có log chi tiết; video minh chứng toàn trình từ UF1 đến UF7; báo cáo nghiệm thu sản phẩm đạt chuẩn đồ án. |

---

## 3. Những điểm cần phối hợp ngay đầu tuần (Critical Path)

1. **Dọn dẹp Nợ Kỹ Thuật RAM Dict & Kích hoạt JWT Thật (Ngày 1 — P0):**
   - Hưng cùng Tín refactor `draft_service.py`, xóa bỏ `_FAKE_DRAFTS_BY_OWNER`, bắt buộc `db: AsyncSession`, chuyển idempotency sang `command_receipts`.
   - Cương cùng Khải triển khai endpoint `POST /api/v1/auth/google`, kiểm tra xác thực Google ID Token và cấp phát JWT.
2. **Khóa Contract API UF5 & UF6 với Mobile (Ngày 2 — P1):**
   - Cương bàn giao DTOs và Swagger endpoints cho An và Thịnh.
   - Thống nhất cơ chế trả về `CheckInPromptDTO` kèm audio base64 từ `edge-tts`.
   - Thống nhất định dạng trả về của `RescheduleProposalDTO` (danh sách session cũ vs. mới và số phút thiếu hụt $\Delta T_{\text{deficit}}$).
3. **Môi trường PostgreSQL Staging & Migrations (Ngày 2–3 — P1):**
   - Tín dựng container Docker PostgreSQL 16 Staging (Port 5433).
   - Chạy toàn bộ 213+ test hiện tại trên PostgreSQL thật để xác nhận không có lỗi cú pháp DDL hoặc xung đột asyncpg.
4. **Kiểm tra Máy thật: Giải phóng DND sau 300s & SnackBar Undo 5s (Ngày 3–4 — P1):**
   - Khải và Thành kiểm chứng native receiver tự động tắt DND sau 5 phút trên thiết bị Android thật khi không phản hồi.
   - An và Thành kiểm tra tính năng Undo 5 giây của 3 chip Skip, xác nhận không dùng bất kỳ hộp thoại Confirm nào.
5. **Đồng bộ Google Calendar 2 Chiều C01–C04 (Ngày 4–5 — P2):**
   - Hưng hoàn thiện thuật toán FreeBusy Subtraction với Own-Event Protection.
   - Tín hoàn thiện Outbox Worker với ETag If-Match xử lý mã lỗi 412.
6. **Nghiệm thu Toàn trình E2E & Đóng gói APK (Ngày 6–7):**
   - An xuất bản file APK Release hoàn chỉnh.
   - Thành chạy trọn vẹn kịch bản 34 bước Console E2E và test luồng người dùng trên điện thoại thật.
   - Trung tổng hợp biên bản nghiệm thu và tài liệu báo cáo đồ án.

---

## 4. Tiêu chí nghiệm thu hoàn thành (Definition of Done — DoD)

- [ ] **Mã nguồn Backend:** 100% SQLAlchemy 2.0 và Pydantic v2; không còn biến RAM dict in-memory nào trong `draft_service.py`; toàn bộ 12 endpoints mới được đăng ký và ghi nhận trong Swagger.
- [ ] **Mã nguồn Mobile:** Vượt qua kiểm tra `dart analyze` không có warning; giao diện tuân thủ 1-Step UI trên màn hình Focus; tốc độ đạt 60 FPS; tối đa 3–5 thành phần tương tác mỗi màn hình.
- [ ] **Bảo vệ Nghiệp vụ (Guardrails):**
  - [ ] Đồng hồ đếm ngược hết giờ KHÔNG tự động complete session.
  - [ ] Đóng app KHÔNG phải là đồng ý dời lịch (lưu `pending_approval`, tịnh tiến giờ thực tế khi mở lại app).
  - [ ] Thuật toán dời lịch không tự ý thay đổi các Task khác ngoài `allowed_task_ids`.
  - [ ] DND tự động tắt sau 300s timeout nếu không phản hồi check-in.
  - [ ] Nút Bỏ qua hiển thị 3 chip kèm SnackBar Undo trong 5 giây (W3C COGA).
- [ ] **Độ bao phủ Kiểm thử:** Toàn bộ test suite backend $\ge 216$ tests pass trên cả SQLite và PostgreSQL Staging; 34 bước Console E2E pass 100%.
- [ ] **Bảo mật & Tích hợp:** Google Sign-In cấp phát JWT thật; Google Calendar tạo lịch phụ "ProPlan VA Focus" với scope tối thiểu `calendar.app.created`; token được mã hóa AES-256 Fernet.
- [ ] **Đóng gói Sản phẩm:** File APK chạy ổn định trên máy thật Android 12–14; video demo minh chứng đầy đủ 7 luồng UF1–UF7; commit history trên Git sạch sẽ, tuân thủ Conventional Commits.
