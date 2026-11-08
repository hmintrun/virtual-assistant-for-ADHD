# BÁO CÁO KỸ THUẬT & NGHIỆP VỤ CHUYÊN SÂU PHÂN HỆ BACKEND — TUẦN 2 (SPRINT 2)

* **Người lập:** Quốc Hưng (`vqh`) — Backend Lead & Algorithm Designer
* **Người nhận:** Minh Trung (`mt`) — Git Manager, Business Analyst (BA) & Prompt Engineer
* **Ngày lập:** 07/11/2026
* **Phân hệ phụ trách:** Backend Architecture, REST API Contracts, SQLAlchemy 2.0 ORM, Core Services & Scheduler Engine
* **Tình trạng:** **HOÀN TẤT 100% CÁC MỐC NHIỆM VỤ TỪ W01 ĐẾN W08** (Đạt 97/97 tests tích hợp và 34/34 bước E2E Console với AI thật).

---

## I. TỔNG QUAN KIẾN TRÚC & PHÂN TÁCH TRÁCH NHIỆM 3 TẦNG

Trong Tuần 2, phân hệ Backend đã hoàn thiện và kết nối đồng bộ 3 tầng mã nguồn:

$$\text{Tầng 1: HTTP Router / DTO (Kim Cương)} \longrightarrow \text{Tầng 2: Core Service / Logic (Quốc Hưng)} \longrightarrow \text{Tầng 3: Storage / DB (Trang Tín)}$$

1. **Tầng HTTP Router (`backend/app/api/v1/` — Kim Cương phụ trách):**
   - Đóng gói toàn bộ DTO Pydantic v2 (Input validation, Response serializing).
   - Kiểm soát bắt buộc Header `Idempotency-Key` (chống gửi lặp và race condition).
   - Cơ chế điều phối động `service_dispatch.py` kết nối trực tiếp router với các service tương ứng mà không làm gãy dependency.
2. **Tầng Core Service & Business Engine (`backend/app/services/` — Quốc Hưng phụ trách):**
   - Điều phối State Machine của phiên làm việc (`SessionRun`), cất suy nghĩ (`Thought`), lời nhắc (`Reminder`) và xử lý phản hồi (`CheckIn`).
   - Đảm bảo **Strict Guardrails**: Không gọi STT/LLM trong phiên, không tự ý dời lịch của các công việc khác, không tự động đánh dấu hoàn thành khi hết giờ đếm ngược.
3. **Tầng Database & Persistence (`backend/app/models/`, `repositories/` — Trang Tín phụ trách):**
   - Triển khai migration `20261107_02_live_run_index.py`: Thiết lập ràng buộc độc bản `uq_user_live_run` trên bảng `session_runs` (kết hợp các trạng thái `running`, `paused`, `awaiting_checkin`) — bảo đảm toàn vẹn nguyên tắc: **Mỗi người dùng tại một thời điểm chỉ có DUY NHẤT 1 phiên làm việc trực tiếp**.

---

## II. BÁO CÁO CHI TIẾT TỪNG NHIỆM VỤ HOÀN THÀNH (W01 – W08)

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 LỘ TRÌNH 8 NHIỆM VỤ BACKEND TUẦN 2                                     │
│  W01 (UF1-UF2 Sync) ──> W02 (Scheduler Engine) ──> W03 (KC Contracts) ──> W04 (Focus State Machine)   │
│         │                                                                             │                │
│         v                                                                             v                │
│  W08 (Real AI E2E Console) <── W07 (UF4 Thought) <── W06 (Reminders) <── W05 (Check-in & Timeout)     │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 1. W01: Quản Lý Nguồn Giờ Rảnh Riêng & Xác Nhận Đầu Vào UF1 $\rightarrow$ UF2
* **Mục tiêu nghiệp vụ (BA Context):** Người ADHD thường có lịch sinh hoạt biến động, không cố định. Giờ rảnh của từng công việc phải được thu thập độc lập (`DraftAvailability`) thay vì ép dùng giờ rảnh chung (`UserPreference`), giúp giảm tải nhận thức và tránh việc người dùng phải vào cài đặt sửa lại lịch nhiều lần.
* **Chi tiết kỹ thuật & DTOs:**
  - Files liên quan: `app/services/draft_service.py`, `app/schemas/availability.py`, `app/api/v1/drafts.py`.
  - Tách bạch cấu trúc: Lưu trữ `timezone`, `weekly_rules` (mỗi ngày có các khoảng `start_local`, `end_local`) và `date_overrides` trực tiếp vào bản nháp (`DraftRow.summary_json`).
  - Kiểm tra 4 trường bắt buộc của UF1: `Nội dung`, `Phạm vi (Scope)`, `Khung giờ rảnh`, và `Deadline`.
  - **Single-Round Clarification (Gộp 1 vòng duy nhất):** Khi AI phân tích phát hiện thiếu thông tin (ví dụ: thiếu giờ rảnh ở Voice 3), hệ thống chuyển sang `can_plan = False` và sinh danh sách `clarification_issues` trong 1 lần hiển thị duy nhất. Khi người dùng gửi bổ sung, AI hợp nhất trực tiếp và chuyển sang `can_plan = True`, chốt qua `POST /drafts/{id}/confirm-summary`.

---

### 2. W02: Thuật Toán Phân Rã & Lập Lịch Đa Nhiệm Không Xung Đột UF2
* **Mục tiêu nghiệp vụ (BA Context):** Giải quyết hội chứng **Tê liệt khởi động (ADHD Paralysis / Blank Canvas)** bằng cơ chế AI sinh sẵn phương án chia bước (`Default Decomposition Proposal`), kết hợp công thức toán học kiểm tra tính khả thi trước Deadline để loại bỏ cảm giác tội lỗi và áp lực thời gian.
* **Chi tiết kỹ thuật & Lõi Thuật toán Python:**
  - Files liên quan: `app/services/scheduler_engine.py`, `app/services/plan_service.py`.
  - **Phân tách trách nhiệm Hybrid (LLM vs Thuật toán):**
    - LLM (`Gemini Flash`): Chỉ bẻ nhỏ `Task -> Steps` và sinh gợi ý Micro-task 2 phút.
    - Thuật toán Python (`scheduler_engine.py`): Đảm nhận 100% việc chia `Steps -> Sessions`, nhân hệ số đệm cá nhân hóa ($k_{\text{buffer}} = 1.2$), chèn thời gian nghỉ (15 phút sau mỗi 2 phiên), và xếp theo **Rolling Horizon** (2–3 tuần gần nhất).
  - **Công thức tính khả thi:**
    $$T_{\text{req}} = \sum_{i=1}^{N} \left( \text{PlannedDuration}_i \times k_{\text{buffer}} + \text{BreakDuration}_i \right)$$
  - **Quy tắc Multi-Task Schedule Collision Avoidance (Bảo vệ Lịch Trình Đã Chốt):**
    Thuật toán kiểm tra giao thoa thời gian thực: **Tuyệt đối không tự ý dời hay xáo trộn các Task khác đã chốt**. Các phiên mới chỉ được phép lấp vào các khoảng thời gian rảnh thực tế còn trống ($T_{\text{free}}$). Nếu thiếu giờ, thuật toán trả về mã lỗi `PLAN_NOT_CONFIRMABLE` cùng độ thiếu hụt $\Delta T_{\text{deficit}}$ để người dùng nới deadline.

---

### 3. W03: Chuẩn Hóa Bộ Hợp Đồng Router & DTO UF3–UF4 (Phối hợp với Kim Cương `kc`)
* **Mục tiêu nghiệp vụ (BA Context):** Đảm bảo API Backend hoàn toàn khớp với giao diện Flutter của nhóm Mobile (Thanh An, Quốc Thịnh), bảo vệ tính toàn vẹn dữ liệu qua cơ chế Idempotency chống mất kết nối mạng.
* **Chi tiết kỹ thuật:**
  - Files liên quan: `docs/backend/KC_ROUTER_CONTRACT_UPDATE_20261107.md`, `app/api/v1/service_dispatch.py`.
  - Chuẩn hóa 13 API endpoints:
    - `POST /sessions/{id}/start`: Khởi động phiên.
    - `GET /runs/active`, `GET /runs/{id}`: Truy vấn trạng thái phiên hiện hành.
    - `POST /runs/{id}/events`: Ghi nhận sự kiện `pause`, `resume`, `heartbeat`.
    - `POST /runs/{id}/end`: Kết thúc phiên, chuyển sang chờ check-in.
    - `PUT /devices/{id}/state`, `GET /reminders`, `POST /reminders/{id}/receipt`: Quản lý thiết bị và mốc nhắc.
    - `POST /thoughts`, `GET /thoughts`, `POST /thoughts/{id}/draft`, `DELETE /thoughts/{id}`: Hộp suy nghĩ UF4.
    - `GET /runs/{id}/check-ins`, `PUT /check-ins/{id}/response`: Check-in kết thúc phiên.
  - Bọc chuẩn `SuccessEnvelope[T]` (`{ data: ..., meta: ... }`), xử lý lỗi qua `ErrorEnvelope` thống nhất.
  - Kiểm thử đạt: **49 / 49 contract tests** trong `tests/test_kc_router_contracts.py`.

---

### 4. W04: Khởi Chạy Phiên & Máy Trạng Thái Thực Thi UF3 (Focus Run State Machine)
* **Mục tiêu nghiệp vụ (BA Context):** Áp dụng nguyên tắc **1-Step UI** và **Gộp thao tác 1 chạm (1-Tap Focus)**: Người dùng bấm 1 nút duy nhất $\rightarrow$ ứng dụng tự kích hoạt đồng thời đếm giờ, chuyển giao diện sang 1 bước duy nhất, và đồng bộ trạng thái thiết bị.
* **Chi tiết kỹ thuật & State Machine:**
  - Files liên quan: `app/services/run_service.py`, `app/models/execution.py`.
  - Vòng đời trạng thái Run:
    $$\text{not\_started} \xrightarrow{\text{POST /start}} \text{running} \underset{\text{resume}}{\overset{\text{pause}}{\rightleftharpoons}} \text{paused} \xrightarrow{\text{POST /end}} \text{awaiting\_checkin} \xrightarrow{\text{PUT /response}} \text{completed / closed}$$
  - **Cơ chế Idempotency & Anti-Double-Tap:** Kiểm tra khóa idempotency trên database receipt; nếu người dùng vô tình bấm 2 lần liên tiếp, request thứ 2 trả về chính xác kết quả của phiên đang tạo, không bao giờ sinh ra 2 Run song song.
  - Kiểm thử đạt: **13 / 13 unit tests** trong `tests/test_w04_focus_start.py`.

---

### 5. W05: Check-In Tối Thiểu & Cơ Chế Lazy Timeout UF5 (Final Check-In & DND Auto-Restore)
* **Mục tiêu nghiệp vụ (BA Context):** Người ADHD rất dễ bị phân tâm khi hết giờ hoặc quên tương tác với ứng dụng. Hệ thống phải đảm bảo: (1) Không bao giờ tự nhận việc đã xong; (2) Tự động giải phóng trạng thái không làm phiền (DND) sau 5 phút để người dùng không bị cô lập liên lạc.
* **Chi tiết kỹ thuật:**
  - Files liên quan: `app/services/checkin_service.py`, `app/api/v1/checkin.py`.
  - **Quy tắc Bất biến:** Khi timer kết thúc (`end_reason = "timer_elapsed"`), hệ thống chuyển sang `awaiting_checkin` và sinh `checkin_id`. Tuyệt đối **KHÔNG** tự gán `session_outcome = "completed"`.
  - Tiếp nhận 4 trạng thái phản hồi qua `PUT /check-ins/{id}/response`: `completed`, `incomplete`, `busy`, `skipped`.
  - **Quy tắc Chọn Nghỉ (Break / Busy):** Chọn nghỉ là quyết định dừng phiên, giữ nguyên trạng thái chưa hoàn thành (`incomplete`), không tự động coi là xong bước.
  - **Cơ chế Lazy Timeout (Quá hạn 5 phút):** Khi có request đọc trạng thái hoặc bắt đầu phiên mới sau mốc `ended_at + 300s`, hệ thống tự động cập nhật CheckIn sang `no_response`, kết thúc Live Run cũ để không chặn các phiên tương lai mà vẫn bảo toàn thời lượng làm việc thực tế (`work_elapsed_seconds`).
  - Kiểm thử đạt: **12 / 12 unit tests** trong `tests/test_w05_execution_checkin.py`.

---

### 6. W06: Báo Cáo Trạng Thái Thiết Bị & Hiện Thực Hóa 3 Mốc Nhắc Lịch (Pre-session Reminders 15/10/5p)
* **Mục tiêu nghiệp vụ (BA Context):** Triệu chứng "Mù thời gian" (Time Blindness) khiến người ADHD không cảm nhận được phiên làm việc sắp tới. Việc nhắc trước tại các mốc `15p`, `10p`, `5p` kèm gợi ý **Micro-task khởi động 2 phút** giúp não bộ chuyển trạng thái nhẹ nhàng từ nghỉ ngơi sang tập trung.
* **Chi tiết kỹ thuật:**
  - Files liên quan: `app/services/device_service.py`, `app/api/v1/devices.py`, `app/api/v1/reminders.py`.
  - Lưu trữ trạng thái thiết bị (`PUT /devices/{id}/state`): `notification_permission`, `microphone_permission`, `dnd_permission`, `alarm_capability`.
  - **Hiện thực hóa lời nhắc (Materialization):** API `GET /api/v1/reminders` tự động tính toán các mốc thời gian `fire_at = scheduled_start - offset` (với các offset 900s, 600s, 300s). 
  - Chỉ tạo lời nhắc cho các mốc tương lai (`fire_at >= now_utc`), tự động hủy các mốc nhắc đã trôi qua hoặc khi phiên làm việc bị dời lịch.
  - Kiểm thử đạt: **10 / 10 unit tests** trong `tests/test_w06_device_reminders.py`.

---

### 7. W07: Cất Suy Nghĩ Bằng Raw Audio Trong Phiên & Chuyển Đổi Sau Phiên UF4
* **Mục tiêu nghiệp vụ (BA Context):** Trong lúc tập trung, người ADHD thường nảy sinh những ý tưởng bất chợt (hyper-focus / intrusive thoughts). Nếu bắt họ gõ chữ hoặc đọc màn hình sửa lỗi STT, họ sẽ lập tức bị đứt mạch tập trung. Nút "Cất suy nghĩ" cho phép nói nhanh và lưu file âm thanh thô trong 1 giây để an tâm quay lại công việc.
* **Chi tiết kỹ thuật:**
  - Files liên quan: `app/services/thought_service.py`, `app/api/v1/thoughts.py`.
  - **In-Session Raw Capture:** Endpoint `POST /api/v1/thoughts` chỉ nhận metadata và xác nhận file ghi âm đã lưu (`capture_state = "saved"`, `attempt_count = 1`).
  - **Strict Guardrail:** **CẤM** gọi STT, **CẤM** gọi LLM trong lúc phiên đang chạy. Đồng hồ đếm ngược của UF3 vẫn duy trì liên tục trên client.
  - **Post-Session Thought $\rightarrow$ Draft (UF4 $\rightarrow$ UF1):** Khi phiên kết thúc (UF5 xong), người dùng vào Hộp nháp bấm *"Tạo việc từ ghi âm này"* $\rightarrow$ API `POST /voice/transcriptions` (`purpose = "thought_to_draft"`) chuyển âm thành chữ $\rightarrow$ `POST /thoughts/{id}/draft` tạo một `Draft 4` mới với `input_source = "thought"`, cấp `linked_draft_id`, hoàn toàn sẵn sàng cho chu trình lập kế hoạch tiếp theo.
  - Kiểm thử đạt: **13 / 13 unit tests** trong `tests/test_w07_thought_lifecycle.py`.

---

### 8. W08: Kiểm Thử Hệ Thống Toàn Trình Console Trên AI Thật (Three Voices Real AI Console System Test)
* **Mục tiêu nghiệp vụ (BA Context):** Xác thực độ tin cậy thực tế của hệ thống bằng giọng nói thật của người dùng Việt Nam, đo lường khả năng chịu lỗi mạng và tính toán lịch tự động xuyên suốt từ UF1 đến UF5.
* **Chi tiết kỹ thuật & Kịch bản Chạy Thật:**
  - File runner: `_temp/three_voices_console_system_test.py`.
  - Chạy trên SQLite độc lập, gọi trực tiếp API thật qua FastAPI TestClient với **3 file giọng nói thực tế**:
    1. `test1.m4a`: Báo cáo tiến độ đồ án tốt nghiệp (Scope: 5 trang, Deadline: 18:00 T6, Rảnh: tối 19:00–21:30).
    2. `test2.m4a`: Slide thuyết trình AI (Scope: 10 slide A*, Deadline: 12:00 trưa CN, Rảnh: sáng T7 8:00–11:00 & tối T7 20:00–22:00).
    3. `test3.m4a`: Đọc sách Lean Code (Scope: 3 chương đầu, Deadline: 22:00 tối mai, **thiếu khung giờ rảnh**).
  - **Kết quả Thực thi Chung cuộc: 34 / 34 BƯỚC PASS (100%) | Thời gian: 163.40s.**
  - **Bằng chứng Hệ Thống Sinh Ra:**
    - Voice 1 chốt lịch chính thức Version 2 (3 phiên làm việc vào các buổi tối).
    - Voice 2 chốt lịch chính thức Version 3 (5 phiên làm việc vào sáng/tối thứ Bảy).
    - Voice 3: AI tự hỏi làm rõ giờ rảnh $\rightarrow$ Người dùng bổ sung rảnh chiều 14:00–17:00 $\rightarrow$ AI giải quyết đạt `can_plan = True` $\rightarrow$ Chốt lịch chính thức Version 4 (3 phiên làm việc chiều mai, **không trùng lịch tối của Voice 1**).
    - Chạy phiên 1-Step UI $\rightarrow$ Cất Raw Audio Thought $\rightarrow$ Kết thúc phiên $\rightarrow$ Check-in hoàn thành $\rightarrow$ STT sau phiên $\rightarrow$ Tạo Draft 4 thành công!
  - Toàn bộ artifact JSON được lưu tại: `_temp/w08_console_runs/three_voices_d724bbfe/three_voices_results.json`.

---

## III. MA TRẬN PHÂN ĐỊNH TRÁCH NHIỆM & RANH GIỚI TUẦN 2 vs. TUẦN 3

Để phục vụ công tác quản lý dự án và báo cáo tiến độ chung, tôi lập bảng ranh giới phân kỳ rõ ràng nhằm giải thích lý do vì sao một số module chưa xuất hiện trong Tuần 2:

| Nhóm Tính Năng / Nghiệp Vụ | Thuộc Sprint | Thành Viên Phụ Trách | Trạng Thái Đạt Được | Lý Do & Cơ Sở Quy Định |
| :--- | :---: | :--- | :---: | :--- |
| **UF1 & UF2 Core:** Trích xuất 4 trường, Clarification, Scheduler Rolling Horizon | **Tuần 1** | **Quốc Hưng (`vqh`)** | **ĐÃ XONG 100%** | Đã hồi quy 209 tests passed trên nhánh `backend`. |
| **W03 Router HTTP & DTO UF3–UF4** | **Tuần 2** | **Kim Cương (`kc`)** | **ĐÃ XONG 100%** | 49 contract tests đạt trong `test_kc_router_contracts.py`. |
| **W04 State Machine Focus Run UF3** | **Tuần 2** | **Quốc Hưng (`vqh`)** | **ĐÃ XONG 100%** | 13 unit tests đạt, xác thực chống double-tap. |
| **W05 Check-in & Lazy Timeout UF5** | **Tuần 2** | **Quốc Hưng (`vqh`)** | **ĐÃ XONG 100%** | 12 unit tests đạt, tự tắt DND sau 5 phút không phản hồi. |
| **W06 Pre-session Reminders 15/10/5p** | **Tuần 2** | **Quốc Hưng (`vqh`)** | **ĐÃ XONG 100%** | 10 unit tests đạt, tự sinh 3 nhắc nhở trước phiên. |
| **W07 In-session Raw Thought UF4** | **Tuần 2** | **Quốc Hưng (`vqh`)** | **ĐÃ XONG 100%** | 13 unit tests đạt, không gọi STT trong phiên, chuyển Draft 4 sau phiên. |
| **W08 Console Real AI System Test** | **Tuần 2** | **Quốc Hưng (`vqh`)** | **ĐÃ XONG 100%** | 34/34 bước PASS trên 3 file voice thật. |
| **UF5 Voice Check-In 2 chiều & 3 chip Bỏ qua** | **Tuần 3** | Quốc Hưng + Minh Trung | **Chưa triển khai** | Phạm vi Tuần 3 (Adaptation Phase theo `AGENTS.md` mục 6). |
| **UF6 Adaptive Rescheduling (`proposals.py`)** | **Tuần 3** | Quốc Hưng + Thuật toán | **Chưa triển khai** | Phạm vi Tuần 3 (Tính năng thích ứng dời lịch khi lỡ nhịp). |
| **UF7 Cài đặt & Học Buffer MA 7–10 phiên** | **Tuần 3** | Quốc Hưng + Thuật toán | **Chưa triển khai** | Phạm vi Tuần 3 (Học máy thống kê điều chỉnh hệ số đệm). |
| **Google Calendar Sync Thật & Background Worker** | **Tuần 3** | Quốc Hưng + Minh Trung | **Chưa triển khai** | Hiện tại API trả `not_integrated` đúng thiết kế `docs/week2_detailed_plan.md`. |
| **Android Alarm Doze $\le 1\text{s}$, DND Native, Mic PTT** | **Tuần 2** | Quang Khải (`kai`) | Đang tiến hành | Phân hệ Mobile Native phụ trách, không thuộc phạm vi Backend. |
| **Timer Flutter, Checkpoint Local, Outbox 5s** | **Tuần 2** | Quốc Thịnh (`qt`) | Đang tiến hành | Phân hệ Mobile phụ trách, không thuộc phạm vi Backend. |
| **Màn hình Focus 1-Step UI, Hộp suy nghĩ** | **Tuần 2** | Thanh An (`tan`) | Đang tiến hành | Phân hệ Flutter Mobile phụ trách. |

---

## IV. ĐỀ NGHỊ PHỐI HỢP DÀNH CHO GIT MANAGER (MINH TRUNG)

Hiện tại, toàn bộ mã nguồn Backend của Tuần 2 (Router KC + Storage TN + Services Hưng) **đã tích hợp và kiểm thử đạt 97/97 tests pass tại máy local của tôi**. 

Để chuẩn bị nghiệm thu MVP v0.2 và giúp nhóm Mobile có thể gọi API thật:
1. **Thực hiện Integration PR:** Đề nghị Minh Trung tạo PR và tiến hành hợp nhất nhánh `backend` (chứa W04–W08 của Hưng), `feat/kc-w2-uf3-uf4-router` (của Cương) và `feat/tn-w2-storage` (của Tín) vào nhánh tích hợp chung `dev`.
2. **Cập nhật Biên bản Tuần 2:** Đánh dấu hoàn thành toàn bộ các hạng mục Backend trong `docs/week2_baseline.md`.
3. **Mở đường cho Sprint Tuần 3:** Sau khi nhánh `dev` được ổn định, tôi sẽ bắt tay vào triển khai ngay các thuật toán thích ứng UF6 (Proposal dời lịch) và UF7 (Tiến hóa hệ số đệm cá nhân hóa).

Rất mong Trung xem xét và phản hồi sớm để chúng ta chốt mốc bàn giao Tuần 2!
