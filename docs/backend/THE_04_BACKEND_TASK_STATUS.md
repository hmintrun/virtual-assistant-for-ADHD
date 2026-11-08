# Danh Mục & Trạng Thái Toàn Bộ Bài Toán Thẻ 04 (Backend Docs)

**Cập nhật:** 07/11/2026 (Sau khi hoàn tất và đẩy 3 commits Tuần 2 lên remote backend)  
**Tài liệu gốc đối chiếu:** Google Docs `1X1zIWny1nOWCfkS4zLEdn71uZLhYLu5OkHlUPKcdoxE`, Thẻ 04 (`t.dk4juq71zmx2`), `docs/week1_detailed_plan.md`, `docs/week2_detailed_plan.md`, `docs/backend/UF3_UF4_DEEP_DIVE_ANALYSIS.md`.

---

## 1. Bảng Đối Chiếu Chi Tiết Toàn Bộ Tasks Thẻ 04 (Chuẩn Tuần 1)

| Mã Task | Role phụ trách | Đầu ra cần bàn giao | Trạng thái kỹ thuật & Phần còn lại |
|:---|:---|:---|:---|
| **C01 / P01** | Backend Lead (`vqh`) | Chuẩn hóa 3 tầng Task–Step–Session, nguồn giờ rảnh riêng, optimistic revision | **[HOÀN THÀNH 100%]** Đã loại bỏ hoàn toàn PlanningPart; nối nguồn giờ riêng DraftAvailability; 41/41 test SQLite đạt. |
| **P02** | Database (`tn`) | SQLAlchemy 2.0 ORM, migrations core, snapshot giờ và tương thích dữ liệu cũ | **[HOÀN THÀNH 100%]** 11 models ORM hoàn chỉnh; 3 migration scripts (20261030_01, 20261103_01, 20261107_02); test độ bền DB đạt. |
| **P03** | Backend Lead (`vqh`) | Hạ tầng FastAPI, Exception Handlers, Uvicorn, Lockfile | **[HOÀN THÀNH 100%]** Khởi động localhost ổn định; Swagger/OpenAPI đầy đủ; cấu hình CORS và middleware. |
| **V01 / P04** | Voice Lead (`kai`) | Audio do user gửi lên → STT bóc chữ / báo lỗi rõ ràng | **[HOÀN THÀNH 100%]** Nối Groq Whisper và Gemini 3.5 Transcribe; bóc chữ chuẩn xác tiếng Việt; có fallback khi mạng lỗi. |
| **A01** | AI Lead (`mt`) | Trích xuất nội dung, mục tiêu, phạm vi công việc từ text/voice | **[HOÀN THÀNH 100%]** Prompt Gemini Flash trích xuất chuẩn xác; Pydantic v2 schema validation chặt chẽ. |
| **A02.1** | AI Lead (`mt`) | Trích xuất thời gian, deadline, giờ rảnh; gộp câu hỏi làm rõ 1 vòng | **[HOÀN THÀNH 100%]** Tự động phát hiện thông tin thiếu, gom toàn bộ vào clarification_questions (Single-Round Clarification). |
| **A02.2** | Algorithm (`vqh`, `qt`) | Kiểm tra tính khả thi đầu vào, chặn ảo giác thời gian của AI | **[HOÀN THÀNH 100%]** Hàm validate_planning_prerequisites kiểm tra deadline không trôi về quá khứ và có giờ rảnh. |
| **A03 / I01** | Backend Lead (`vqh`) | Lưu nháp Draft, merge bổ sung, confirm chốt đầu vào UF1 | **[HOÀN THÀNH 100%]** Endpoint /tasks/draft và /confirm-summary hoạt động tốt; lưu bền vững. |
| **A04** | AI Lead (`mt`) | AI phân rã Task → Steps có thứ tự, đầu ra; giữ unexpanded_scope | **[HOÀN THÀNH 100%]** Prompt bẻ nhỏ công việc chuẩn; bảo toàn phạm vi chưa chia hết khi công việc quá lớn. |
| **A05** | Algorithm (`vqh`, `qt`) | Hệ số đệm cá nhân hóa (k_buffer = 1.2) | **[HOÀN THÀNH 100%]** Nhân buffer vào thời lượng làm việc chính; tuyệt đối không nhân vào thời gian nghỉ hoặc micro-task. |
| **A06** | Algorithm (`vqh`, `qt`) | Cắt khoảng rảnh trống (T_free) và xếp các Sessions work/rest | **[HOÀN THÀNH 100%]** Thuật toán Two-pointer interval subtraction; giữ khoảng nghỉ tối thiểu 15 phút (gap 900s). |
| **A07** | Algorithm (`qt`, `vqh`) | Ưu tiên lịch ổn định đa nhiệm 4-tuple, không dời Task đã chốt | **[HOÀN THÀNH 100%]** Hàm compute_task_priority_key (deadline, importance, start_at, stable_tie_breaker) đạt 69+ assertions test. |
| **A08** | Algorithm (`vqh`, `qt`) | Tính toán thiếu hụt thời gian trước Deadline (Delta T_deficit) | **[HOÀN THÀNH 100%]** Công thức tính Delta T = T_req - T_free; báo lỗi PLAN_NOT_CONFIRMABLE để user nới deadline. |
| **A09** | Algorithm (`vqh`, `qt`) | Chiến lược Rolling Horizon 14 ngày (2–3 tuần gần nhất) | **[HOÀN THÀNH 100%]** Chỉ xếp lịch chi tiết các phiên cho 14 ngày tới, giữ nguyên tổng thể dự án. |
| **A10** | Algorithm (`vqh`, `qt`) | Đóng gói Candidate và báo cáo khả thi phương án chia việc | **[HOÀN THÀNH 100%]** Endpoint POST /candidates và GET /candidates/{id} trả về đầy đủ sessions và cảnh báo thiếu giờ. |
| **P07 / I02** | Backend Lead (`vqh`) | Điều phối nguồn Draft → Context → Candidate | **[HOÀN THÀNH 100%]** Khóa cứng nguồn Draft và Context; cho phép nhập bước thủ công khi AI gặp sự cố. |
| **P07 / A18 (UF2)**| Backend Lead (`vqh`) | Confirm UF2 nguyên tử, Optimistic Locking revision, receipt chống trùng | **[HOÀN THÀNH 100%]** Lưu Task, Steps, Sessions trong 1 transaction duy nhất; rollback sạch nếu lỗi. |
| **A11** | Router (`kc`, `vqh`) | Đặt lịch lời nhắc trước phiên 15p, 10p, 5p ([900, 600, 300]) | **[HOÀN THÀNH 100%]** DTO và validation trong settings.py nhận mảng 3 số nguyên giảm dần; API GET /reminders hoạt động. |
| **A12.0** | AI Lead (`mt`) | Sinh Micro-task 2 phút khởi động (Launchpad) | **[HOÀN THÀNH 100%]** AI sinh câu hành động 2p khi tạo Candidate; lưu cột sessions.micro_task_text; không cộng duration. |
| **A12.1** | Algorithm (`vqh`) | Kiểm tra điều kiện bắt đầu phiên (Start Window Feasibility) | **[HOÀN THÀNH 100%]** Logic kiểm tra requested_start_at, started_at, start window, grace period 15p trong run_service.py. |
| **A12.2** | Router (`kc`) / Service (`vqh`) | Router focus.py và run_service.py: quản lý SessionRun | **[HOÀN THÀNH 100%]** Đã code và push commit 0c51a59, d927a14; ràng buộc uq_user_live_run cho running, paused. |
| **A12.3** | Voice / Mobile (`kai`) | Bật Focus Mode, DND an toàn và Wakelock | **[ĐANG TIẾN HÀNH TRÊN MOBILE]** Backend đã sẵn sàng endpoint; Mobile đang ghép flutter_dnd và wakelock_plus. |
| **A13** | Router (`kc`) / Service (`vqh`) | Thu âm thô UF4 (Raw Audio PTT .m4a), thoughts.py, thought_service.py | **[HOÀN THÀNH 100% Ở BE]** Đã code và push commit; lưu metadata audio thô, Undo 5s, bảo vệ cấm gọi STT/LLM trong phiên. |
| **A21** | Voice (`kai`, `kc`) | Chuyển âm thanh Thought sang chữ sau phiên (STT) | **[HOÀN THÀNH 100% Ở BE]** Hàm check_thought() chặn 409 khi phiên đang chạy; cho phép bóc chữ khi đã kết thúc. |
| **A18 (UF3/4)** | Backend Lead (`vqh`) | Idempotency replay lệnh qua CommandReceipt cho start/end/thought | **[HOÀN THÀNH 100%]** Bảng command_receipts lưu kết quả, replay an toàn khi rớt mạng hoặc double-tap. |
| **Check-in min**| Router (`kc`) / Service (`vqh`) | Endpoint PUT /check-ins/{id}/response cho Chọn nghỉ / Báo bận | **[HOÀN THÀNH 100%]** Đã code và push commit; lazy timeout 5p tự dọn Run sang no_respond, nhả DND an toàn. |
| **A20** | Algorithm / Nghiên cứu | Bằng chứng thực nghiệm pilot để chứng minh hệ số k = 1.2 | ⚠️ **[TỒN ĐỌNG — ĐANG HOÃN]** Chưa đủ dữ liệu lấy mẫu người dùng thực tế; hiện dùng k=1.2 làm giả định thiết kế. |
| **A22.1 / A22.2**| Backend Lead (`vqh`) | Tích hợp Google Calendar thật qua MCP (đọc bận & đẩy Focus) | ⚠️ **[TỒN ĐỌNG — SẮP LÀM TUẦN 3]** Hoãn tích hợp thật ở Tuần 2; đã có tài liệu hướng dẫn 2 chiều; chuẩn bị làm mốc C01–C04. |
| **Auth JWT** | Backend Lead (`vqh`) | Xác thực người dùng thực tế bằng JWT Token | ⚠️ **[NỢ KỸ THUẬT — LÀM NGAY]** security.py hiện đang stub trả về User mặc định; Hưng chuẩn bị làm Google Sign-In + JWT. |
| **RAM Cache** | Backend Lead (`vqh`) | Dọn sạch biến _FAKE_DRAFTS_BY_OWNER sang Database | ⚠️ **[NỢ KỸ THUẬT — LÀM NGAY]** Biến dict in-memory trong draft_service.py cần dọn sạch hoàn toàn sang DB. |
| **UF5 Mở rộng** | Voice (`kai`) / Service (`vqh`) | Voice Check-in 2 chiều qua edge-tts (5 nhánh, 3-chip Skip) | ⏳ **[CHƯA LÀM — SPRINT TUẦN 3]** Tuần 2 chỉ làm CheckIn tối thiểu bằng nút bấm. Chưa có TTS giọng nói, chưa có 3 chip Bỏ qua. |
| **UF6** | Backend Lead (`vqh`) | Tái lập lịch thích ứng (Adaptive Rescheduling), tịnh tiến giờ thực tế | ⏳ **[CHƯA LÀM — SPRINT TUẦN 3]** Chưa có API POST /proposals và logic ScheduleProposal(pending) khi tắt app. |
| **UF7** | Backend Lead (`vqh`) | Màn hình Cài đặt hoàn chỉnh & Worker tính Buffer MA 7–10 phiên | ⏳ **[CHƯA LÀM — SPRINT TUẦN 3]** Chưa có Worker chạy ngầm tính độ lệch k_history và tự động gợi ý cập nhật cuối tuần. |
| **DB Staging** | Database (`tn`) | Kiểm chứng toàn bộ migrations và queries trên PostgreSQL thật | ⏳ **[CHƯA LÀM — SPRINT TUẦN 3]** Mới kiểm thử 100% trên SQLite; cần chạy test song song trên PostgreSQL staging. |

---

## 2. Highlight Nhóm Tasks Chưa Hoàn Thành Cần Thực Hiện Tiếp

### Nhóm 1: Nợ Kỹ Thuật & Cần Làm Ngay Trước Khi Sang Tuần 3
1. **Google Sign-In & Authentication JWT Thực Tế:**
   - Thay thế hàm stub `resolve_dev_current_user` trong `app/core/security.py`.
   - Tạo endpoint `POST /api/v1/auth/google` tiếp nhận Google ID Token, verify qua `google-auth` và sinh JWT ProPlan VA (`access_token`, `refresh_token`).
2. **Dọn dẹp RAM Cache trong `draft_service.py`:**
   - Gỡ bỏ `_FAKE_DRAFTS_BY_OWNER`, `_CLIENT_DRAFT_INDEX`, `_CREATE_IDEMPOTENCY_STORE`.
   - Chuyển 100% sang model `PlanningDraftModel` và bảng `command_receipts`.

### Nhóm 2: Lộ Trình Tích Hợp Google Calendar 2 Chiều (Mốc C01 – C04)
1. **C01:** App Login & Auth Bridge (Google Sign-In → JWT).
2. **C02:** OAuth Calendar Callback, mã hóa Fernet cho `credential_ref`, tạo secondary calendar "ProPlan VA Focus".
3. **C03:** Đọc sự kiện thật và trừ khoảng bận vào `scheduler_engine.py` ($T_{\text{free}} = T_{\text{user}} - T_{\text{busy}}$).
4. **C04:** Outbox Sync Worker quét `calendar_sync_jobs` đẩy Focus Sessions lên Google Calendar.

### Nhóm 3: Tính Năng Mới Sprint Tuần 3 (Adaptation Phase)
1. **UF5 Voice Check-In:** Hội thoại 2 chiều qua `edge-tts`, xử lý 3-chip Skip (`[Hoãn làm sau]`, `[Bỏ phiên]`, `[Bỏ bước]`).
2. **UF6 Adaptive Rescheduling:** Endpoint `POST /api/v1/proposals` và `POST /api/v1/proposals/{id}/approve`; logic lưu `pending` khi tắt app và tịnh tiến giờ thực tế khi mở lại app.
3. **UF7 Buffer Evolution:** Worker tính $k_{\text{history}}$ theo Moving Average 7–10 phiên gần nhất, sinh gợi ý nhẹ nhàng nếu lệch $> 20\%$.
4. **PostgreSQL Staging Verification:** Chạy kiểm thử toàn bộ 209+ tests trên PostgreSQL để bảo đảm tương thích trước khi release.
