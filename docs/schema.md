# ĐẶC TẢ ĐỐI TƯỢNG VÀ HỆ THỐNG SCHEMA (O00 – O26) — PROPLAN VA

> **Tài liệu tham chiếu chuẩn (Single Source of Truth) cho nhóm 8 thành viên**  
> Dự án: **ProPlan VA** (Trợ lý ảo hỗ trợ lập kế hoạch & nhắc lịch chủ động cho người trưởng thành ADHD-I)  
> Phiên bản: `2.1.0` (Đã rà soát chéo toàn diện với `Project.md` & `AGENTS.md`: Bổ sung `O00 User` + Streak Freeze, khử Circular FKs, tương thích 100% SQLite/PostgreSQL, chuẩn hóa DND UF5, Soft Delete cho Undo 5s & 3-Tier AI Fallback)

---

## 1. Hướng dẫn Đọc & Quy ước Chung

1. **Đơn vị thời gian:** Mọi trường kết thúc bằng `_seconds` bắt buộc dùng số nguyên (`int`), tính bằng giây.
2. **Khóa định danh & Phiên bản:** 
   - `id`: Mã định danh UUID chuẩn trên Server (`UUIDv4`).
   - `client_*_key` / `client_*_id`: Mã định danh sinh từ phía Client (Mobile) để giữ tính ổn định khi hoạt động Offline, đồng bộ hoặc gửi lại mà không bị tạo trùng lặp.
   - `revision` / `*_revision`: Số phiên bản tăng dần (`int >= 1`) nhằm phát hiện xung đột dữ liệu cũ (Optimistic Concurrency Control).
3. **Xử lý giá trị rỗng:** Giá trị chưa biết hoặc chưa có dữ liệu bắt buộc là `null`, **tuyệt đối không tự động thay thế bằng 0** (tránh làm sai lệch các phép toán trung bình và học máy).
4. **Trường dẫn xuất:** Các trường như `effective_intervals`, `decomposition_state`, `scheduling_state`, `calendar_sync_summary`, `avoidance_count` được tính toán từ dữ liệu nguồn; không ghi ngược vào dữ liệu gốc do người dùng nhập.
5. **Khóa ngoại 1 chiều (DAG Foreign Keys) & Tương thích SQLite / PostgreSQL:**
   - Chỉ gắn ràng buộc `ForeignKey` vật lý **1 chiều** theo dòng thời gian sinh dữ liệu (`O14 Thought` $\leftarrow$ `O05 PlanningDraft` $\leftarrow$ `O07 Task` $\leftarrow$ `O22 PlanningPart` $\leftarrow$ `O08 Step` $\leftarrow$ `O09 Session` $\leftarrow$ `O24 CalendarEventLink`). Các trường chiều ngược lại (`linked_draft_id`, `converted_task_id`, `confirmed_task_id`, `calendar_event_link_id`) là **thuộc tính dẫn xuất qua SQLAlchemy `relationship` trên DTO trả về**, tuyệt đối không tạo cột FK vòng tròn gây lỗi migration trên SQLite.
   - Mọi trường mảng định danh `List[UUID]` trên bảng DB đều lưu bằng cột `JSON` (mảng chuỗi UUIDv4) để tương thích 100% giữa SQLite và PostgreSQL (không dùng kiểu `ARRAY` đặc thù của PostgreSQL); tầng Pydantic v2 tự động parse sang `List[UUID]`.
6. **Cấm Hard Delete & Hỗ trợ `Undo 5 giây` (Chuẩn W3C COGA):** Thao tác xóa/bỏ qua được giữ 5 giây tại Mobile (Optimistic UI kèm `UndoSnackbar`) trước khi gọi API để tránh bắn nhầm `CalendarSyncJob`; trên Server cấm `DELETE` cứng các thực thể nghiệp vụ mà dùng trạng thái hủy/bỏ (`cancelled`, `discarded`) nhằm bảo toàn khóa ngoại cho `SessionRun` và `AuditEvent`.
7. **Bảo mật & Nhật ký kỹ thuật:** Mật khẩu băm (`hashed_password`), token bí mật (`credential_ref`), file âm thanh thô (`raw audio`) và văn bản nhận dạng (`transcript`) tuyệt đối không được đưa vào log kỹ thuật.

---

## 2. Bảng Phân loại 27 Đối tượng Hệ thống (O00 – O26)

| Nhóm Đối tượng | Danh sách Đối tượng | Hình thức Lưu trữ trong Hệ thống |
| :--- | :--- | :--- |
| **Tài khoản, Hồ sơ & Ràng buộc** | `O00 User` | Bảng DB Persistent (`users` - Auth & Streak Freeze) |
| | `O01 UserPreference` | Bảng DB Persistent (`user_preferences`) |
| | `O02 Availability`, `O03 TimeConstraint` | Pydantic Schema / Value Object (Nhúng JSON vào Task/Draft) |
| **Khởi tạo & Lập phương án (UF1–2)** | `O04 TaskSummary`, `O06 PlanCandidate`, `O10 FeasibilityReport` | In-memory DTO / AI Structured Output |
| | `O05 PlanningDraft` | Bảng DB Persistent (`planning_drafts`) |
| **Cấu trúc 3 Tầng & Rolling Horizon** | `O07 Task` | Bảng DB Persistent (`tasks`) |
| | `O22 PlanningPart` | Bảng DB Persistent (`planning_parts`) |
| | `O08 Step` | Bảng DB Persistent (`steps`) |
| | `O09 Session` | Bảng DB Persistent (`sessions`) |
| **Quản lý Lịch & Thực thi (UF3–5)** | `O11 ScheduleVersion` | Bảng DB Persistent (`schedule_versions`) |
| | `O12 SessionRun` | Bảng DB Persistent (`session_runs`) |
| | `O13 CheckIn` | Bảng DB Persistent (`check_ins`) |
| | `O14 Thought` | Bảng DB Persistent (`thoughts`) |
| | `O16 Reminder` | Bảng DB Persistent (`reminders`) |
| **Tích hợp Google Calendar (UF1–2, 6–7)** | `O21 CalendarContext` | In-memory DTO (Snapshot đọc từ Calendar) |
| | `O23 CalendarConnection` | Bảng DB Persistent (`calendar_connections`) |
| | `O24 CalendarEventLink` | Bảng DB Persistent (`calendar_event_links`) |
| | `O25 CalendarSyncJob` | Bảng DB Persistent (`calendar_sync_jobs` - Hàng đợi đồng bộ) |
| | `O26 CalendarEvent` | In-memory DTO / CRUD Model Sự kiện Google |
| **Đổi lịch & Thích ứng (UF6–7)** | `O15 RescheduleProposal` | Bảng DB Persistent (`reschedule_proposals`) |
| | `O19 AdaptationSuggestion` | Bảng DB Persistent (`adaptation_suggestions`) |
| **Hạ tầng & Hệ thống** | `O17 DeviceState` | Client-only State / Báo cáo Device qua API |
| | `O18 CommandReceipt` | Bảng DB Persistent (`command_receipts` - Idempotency) |
| | `O20 AuditEvent` | Bảng DB Persistent (`audit_events`) |

---

## 3. Đặc tả Chi tiết 27 Đối tượng (O00 – O26)

### O00 User
*Tài khoản người dùng gốc và trạng thái giữ chân chuỗi ngày duy trì (Guilt-free Streak Freeze). Luồng sử dụng: Dùng chung toàn hệ thống (Auth, UF5, UF7). Hình thức: Bảng DB (`users`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `email` | `str` | Email chuẩn (Unique, Indexed) | `"minhtrung@proplan.vn"` | Địa chỉ email đăng nhập duy nhất của người dùng. |
| `hashed_password` | `Optional[str]` | Chuỗi băm Bcrypt/Argon2 (ẩn khỏi DTO) | `"$2b$12$KIX..."` | Mật khẩu đã băm phục vụ xác thực JWT; `null` nếu chỉ dùng Google Sign-In. Tuyệt đối không trả ra API hoặc log. |
| `google_sub` | `Optional[str]` | Google Subject ID String (Unique) | `"109876543210123456789"` | Định danh tài khoản Google OAuth khi đăng nhập nhanh (tách biệt với kết nối lịch `O23`). |
| `display_name` | `str` | Chuỗi ký tự (tối đa 100 ký tự) | `"Minh Trung"` | Tên hiển thị để trợ lý xưng hô thân thiện khi Voice Check-in và khích lệ. |
| `is_active` | `bool` | Boolean (`true` / `false`, Default = `true`) | `true` | Cho biết tài khoản đang hoạt động hợp lệ. |
| `current_streak_days` | `int` | Số ngày liên tiếp (`days >= 0`, Default = `0`) | `12` | Chuỗi ngày duy trì tập trung hiện tại (NFR 5.4 — củng cố động lực tích cực). |
| `streak_frozen` | `bool` | Boolean (`true` / `false`, Default = `false`) | `false` | Cho biết chuỗi hiện tại đang được bảo vệ bởi **Đóng băng chuỗi (Streak Freeze)** khi lỡ nhịp, không xóa sạch chuỗi về `0`. |
| `streak_freeze_tokens` | `int` | Số lượt đóng băng dự trữ (`tokens >= 0`, Default = `2`) | `2` | Số thẻ Đóng băng chuỗi còn lại để tự động kích hoạt khi người dùng nghỉ hoặc lỡ ngày. |
| `last_streak_date` | `Optional[str]` | Ngày cục bộ `YYYY-MM-DD` theo múi giờ user | `"2026-10-29"` | Ngày gần nhất người dùng có phiên tập trung được ghi nhận hoặc được đóng băng chuỗi. |

---

### O01 UserPreference
*Cài đặt đã có hiệu lực và yêu cầu thay đổi đang chờ duyệt. Luồng sử dụng: UF2–3, UF7. Hình thức: Bảng DB (`user_preferences`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `work_interval_seconds` | `int` | Giây (`seconds`, thường 1200–1500s) | `1500` | Độ dài tối đa một quãng làm chính theo cấu hình có hiệu lực; không phải toàn Session. |
| `rest_interval_seconds` | `int` | Giây (`seconds`, thường 300s) | `300` | Độ dài nghỉ bên trong khi có; khác khoảng cách 900 giây giữa hai Session. Có thể không tạo quãng nghỉ bên trong. |
| `micro_task_seconds` | `int` | Giây (`seconds`, thường 120s) | `120` | Độ dài khởi động một lần đầu phiên; 2 phút là giá trị thử nghiệm (để phá vỡ tê liệt ADHD). |
| `reminder_offsets` | `List[int]` | Cột `JSON` lưu mảng giây trước phiên (tập `{900, 600, 300}`, Default = `[900]`) | `[900]` | Các mốc nhắc trước phiên; mặc định bật 1 mốc `900s` (15p) chống bão hòa thông báo, cho phép người dùng bật thêm mốc `600s` (10p) và `300s` (5p) trong UF7. |
| `requested_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `3` | Phiên bản cài đặt mới được yêu cầu, có thể đang chờ đổi lịch. |
| `effective_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản đã có hiệu lực; dùng tạo lịch hoặc snapshot thực thi. |
| `buffer_multiplier` | `float` | Hệ số thực (`float >= 1.0`, thường 1.2–1.5) | `1.25` | Hệ số dự phòng đang có hiệu lực; chỉ áp một lần lên làm chính. Giá trị cụ thể cần nghiên cứu/pilot. |
| `reminder_style` | `str` (Enum) | `enum: 'supportive' \| 'direct' \| 'gentle'` | `"supportive"` | Giọng, cách diễn đạt và hình thức nhắc người dùng đã chọn; không tự thay thời lượng phiên. |
| `requested_changes` | `Optional[Dict[str, Any]]` | JSON Object các giá trị cài đặt mới | `{"work_interval_seconds": 1800, "buffer_multiplier": 1.3}` | Các giá trị mới được yêu cầu; chưa dùng thay bản hiệu lực khi còn chờ phương án lịch. |
| `pending_proposal_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `reschedule_proposals.id`) | `"a55a8b21-6543-4c91-a201-9876543210fc"` | Phương án UF6 liên quan nếu thay cài đặt làm ảnh hưởng lịch; null khi không cần đổi lịch. |
| `suggestion_cooldown_until` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-11-05T00:00:00Z"` | Mốc thời gian hết hạn Cooldown chống làm phiền đối với các gợi ý cá nhân hóa/tái cấu trúc lịch định kỳ ở UF7; `null` khi sẵn sàng gợi ý. |

---

### O02 Availability
*Giờ rảnh do người dùng cung cấp hoặc xác nhận. Luồng sử dụng: UF1–2, UF6–7. Hình thức: Pydantic Schema / Value Object (Nhúng JSON).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `weekly_rules` | `List[Dict[str, Any]]` | JSON Array các ô giờ lặp tuần | `[{"day": "mon", "start": "19:00", "end": "21:00"}]` | Các khoảng rảnh lặp theo ngày trong tuần. |
| `date_overrides` | `List[Dict[str, Any]]` | JSON Array các ngoại lệ ngày | `[{"date": "2026-10-30", "type": "busy", "reason": "Hop phu huynh"}]` | Ngoại lệ cho ngày cụ thể, như một ngày bận đột xuất. |
| `task_limits` | `Optional[Dict[str, Any]]` | JSON Object giới hạn khung giờ | `{"max_sessions_per_day": 2, "time_window": "evening"}` | Giới hạn giờ thực hiện riêng của Task nếu người dùng xác nhận; không tự ghi đè giờ rảnh chung. |
| `source` | `str` (Enum) | `enum: 'manual' \| 'calendar_inferred' \| 'hybrid'` | `"manual"` | Cho biết giờ rảnh được nhập hoặc xác nhận bởi người dùng. |
| `effective_intervals` | `List[Dict[str, str]]` | Mảng khoảng thời gian ISO 8601 UTC | `[{"start": "2026-10-29T19:00:00Z", "end": "2026-10-29T21:00:00Z"}]` | Kết quả tính từ giờ rảnh, khoảng thực hiện và lịch bận; là dữ liệu dẫn xuất, không ghi ngược vào giờ rảnh người dùng nhập. |
| `calendar_context` | `Optional[Dict[str, Any]]` | JSON Object (`O21` schema) | `{"snapshot_id": "snap-9876", "fetched_at": "2026-10-29T14:00:00Z"}` | Tham chiếu O21 và phiên bản dữ liệu Calendar dùng tính lịch; không hiểu lỗi đọc lịch là không có lịch bận. |
| `timezone` | `str` | IANA Timezone ID | `"Asia/Ho_Chi_Minh"` | Múi giờ áp dụng cho quy tắc theo tuần và ngày ngoại lệ; mốc tuyệt đối vẫn được chuẩn hóa UTC. |
| `confirmed_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T14:10:00Z"` | Thời điểm người dùng xác nhận bộ giờ rảnh; không suy là đã xác nhận chỉ vì Calendar trả về khoảng trống. |

---

### O03 TimeConstraint
*Khoảng thời gian được phép thực hiện. Luồng sử dụng: UF1–2, UF6. Hình thức: Pydantic Schema / Value Object.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `kind` | `str` (Enum) | `enum: 'deadline' \| 'timeframe'` | `"deadline"` | Phân biệt cách nhập theo hạn chót hoặc khoảng thời gian. |
| `start_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T14:30:00Z"` | Mốc sớm nhất được bắt đầu; cần làm rõ trước UF2. |
| `finish_by_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-11-05T17:00:00Z"` | Mốc muộn nhất phải hoàn tất; cả Session phải kết thúc không sau mốc này. |
| `deadline_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-11-05T17:00:00Z"` | Hạn chót gốc khi kind là deadline; phải khớp finish_by_at. Với window không tạo thêm một hạn khác mâu thuẫn. |
| `input_timezone` | `str` | IANA Timezone ID | `"Asia/Ho_Chi_Minh"` | Múi giờ để hiểu đúng các ngày và giờ người dùng nhập. |

---

### O04 TaskSummary
*Bảng tóm tắt người dùng xem ở UF1. Luồng sử dụng: UF1–2. Hình thức: In-memory DTO / AI Structured Output.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `title` | `str` | Chuỗi ký tự (tối đa 100 ký tự) | `"Viết báo cáo chuyên đề AI"` | Tên ngắn gọn để nhận diện công việc. |
| `description` | `str` | Văn bản tự do | `"Hoàn thành slide và tài liệu tóm tắt 10 trang"` | Nội dung cụ thể người dùng muốn thực hiện. |
| `goal` | `Optional[str]` | Chuỗi ký tự mục tiêu SMART | `"Nộp bản PDF trước 17h thứ Năm cho giảng viên"` | Mục tiêu muốn đạt, nếu cần lưu riêng với nội dung và phạm vi. |
| `scope` | `Optional[str]` | Chuỗi ký tự | `"10 trang Word, tài liệu tham khảo chuẩn APA"` | Đầu ra và giới hạn công việc để chia bước đúng ý người dùng. |
| `extra_requirements` | `Optional[List[str]]` | Mảng chuỗi ký tự | `["Font Times New Roman 13", "Chỉ làm buổi tối"]` | Các yêu cầu bổ sung cần giữ khi chia việc và xếp lịch. |
| `time_constraint` | `Dict[str, Any]` | JSON Object theo `O03` | `{"kind": "deadline", "finish_by_at": "2026-11-05T17:00:00Z"}` | Khoảng bắt đầu và kết thúc đã làm rõ theo O03. |
| `availability_ref` | `Optional[Dict[str, Any]]` | JSON Object theo `O02` | `{"source": "manual", "confirmed_at": "2026-10-29T14:10:00Z"}` | Bộ giờ rảnh và revision đã xác nhận; Calendar chỉ bổ sung lịch bận, không thay trường bắt buộc này. |
| `clarification_issues` | `List[str]` | Mảng câu hỏi gộp 1 vòng | `["Bạn dự kiến nộp bài vào ngày giờ nào?", "Báo cáo dài bao nhiêu trang?"]` | Những chỗ thiếu, mơ hồ hoặc mâu thuẫn cần hỏi lại trong 1 vòng duy nhất. |
| `can_plan` | `bool` | Boolean (`true` / `false`) | `true` | Cho biết đã đủ dữ liệu để chuyển sang lập phương án UF2. |
| `field_sources` | `Dict[str, str]` | JSON Object mapping trường -> nguồn | `{"title": "user_input", "scope": "ai_clarified", "deadline": "user_confirmed"}` | Nguồn của từng giá trị: người dùng nhập, sửa, xác nhận hoặc AI đề xuất; giữ được yêu cầu gốc khi sửa tóm tắt. |
| `generation_tier` | `str` (Enum) | `enum: 'tier_1_gemini' \| 'tier_2_groq' \| 'tier_3_rule_based'` | `"tier_1_gemini"` | Tầng AI / Rule-based đã sinh bản tóm tắt theo cơ chế 3-Tier Graceful Degradation (NFR 5.3); giúp Mobile hiển thị thông báo thân thiện khi rơi xuống Tầng 3. |

---

### O05 PlanningDraft
*Lưu nháp trước khi tạo công việc chính thức. Luồng sử dụng: UF1–2, UF4. Hình thức: Bảng DB (`planning_drafts`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `client_draft_id` | `str` | UUIDv4 Client String | `"c8b3a720-3b4e-4e89-9a72-f1d7e2e8b910"` | Mã nháp ổn định để lưu máy và đồng bộ không bị nhân đôi. |
| `stage` | `str` (Enum) | `enum: 'collecting' \| 'reviewing_summary' \| 'candidate_ready' \| 'confirmed' \| 'discarded'` | `"reviewing_summary"` | Giai đoạn của nháp: đang thu thập, xem tóm tắt, có phương án, đã xác nhận hoặc đã hủy bỏ (`discarded` sau 5s Undo, không xóa cứng). |
| `summary` | `Dict[str, Any]` | JSON Object (`O04` Schema) | `{"title": "Báo cáo AI", "can_plan": false, ...}` | Bản tóm tắt hiện tại, kể cả khi còn thiếu dữ liệu. |
| `summary_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `1` | Phiên bản tóm tắt; tránh dùng kết quả AI từ bản đã cũ. |
| `candidate` | `Optional[Dict[str, Any]]` | JSON Object (`O06` Schema) | `{"candidate_id": "cand-001", "steps": [...]}` | Phương án UF2 đang xem xét, gồm cả phạm vi chưa chia/chưa xếp; chưa là lịch chính thức. |
| `source_thought_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `thoughts.id`) | `"14fa8b21-6543-4c91-a201-9876543210ab"` | Bản ghi suy nghĩ (`O14`) đã khởi tạo nháp này (sau khi kết thúc phiên và gọi STT), nếu có. |
| `confirmed_task_id` | `Optional[UUID]` | UUIDv4 (**Read-only ORM Derived** từ `O07.source_draft_id`) | `"32fa8b21-6543-4c91-a201-9876543210ef"` | Thuộc tính dẫn xuất ORM liên kết Task thật sau khi UF2 lưu thành công; **không tạo cột FK vật lý vòng tròn** trên bảng `planning_drafts`. |
| `raw_input` | `Optional[str]` | Văn bản thô / Transcript STT | `"Muốn viết báo cáo AI nộp tuần sau"` | Nội dung chữ ban đầu hoặc bản STT nhận được (kể cả khi chuyển từ `O14 Thought` sau phiên để không phải upload lại file ghi âm nếu LLM lỗi). Không lưu audio thô vào log. |
| `input_source` | `str` (Enum) | `enum: 'text' \| 'voice_direct' \| 'thought_conversion'` | `"voice_direct"` | Nhập chữ, nói trực tiếp hoặc tạo từ Thought; phân biệt cách tiếp nhận và quyền cần dùng. |

---

### O06 PlanCandidate
*Phương án UF2 trước xác nhận, có thể chỉ lập chi tiết một phần Task. Luồng sử dụng: UF2. Hình thức: In-memory DTO.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `candidate_id` | `str` | Chuỗi định danh duy nhất | `"cand-uf2-20261029-01"` | Mã nhận diện phương án đang hiển thị. |
| `source_summary_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản tóm tắt đã dùng để tạo phương án. |
| `steps` | `List[Dict[str, Any]]` | JSON Array các bước dự kiến | `[{"client_step_key": "step-k1", "title": "Tìm 5 tài liệu", "planning_part_key": "part-k1"}]` | Các bước đã chia trong phần được chọn; mỗi bước có client_step_key và tham chiếu phần nguồn, chưa giả làm ID chính thức. |
| `sessions` | `List[Dict[str, Any]]` | JSON Array các phiên dự kiến | `[{"client_session_key": "sess-k1", "scheduled_start": "2026-10-29T19:00:00Z"}]` | Các phiên đã đề xuất; mỗi phiên có client_session_key và tham chiếu bước dự kiến. Chưa có timer, alarm hoặc sự kiện Calendar vận hành. |
| `feasibility` | `Dict[str, Any]` | JSON Object (`O10` Schema) | `{"status": "valid", "required_seconds": 3840, "available_seconds": 7200}` | Kết quả kiểm tra phương án có đáp ứng các ràng buộc. |
| `min_inter_session_gap_seconds` | `int` | Giây (`seconds`, cố định 900s) | `900` | Khoảng cách tối thiểu giữa phiên: 900 giây (15 phút), kể cả khác Task. |
| `review_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `1` | Phiên bản phương án người dùng xem; xác nhận phải đúng bản này. |
| `planning_parts` | `List[Dict[str, Any]]` | JSON Array (`O22` dự kiến) | `[{"client_part_key": "part-k1", "scope_description": "Phần 1: Nghiên cứu"}]` | Toàn bộ phần phạm vi dự kiến theo O22, kể cả phần chưa có Step. Mỗi phần có client_part_key ổn định trong nháp. |
| `planning_horizon` | `Dict[str, datetime]` | Khoảng ISO 8601 UTC (start/end) | `{"start": "2026-10-29T00:00:00Z", "end": "2026-11-12T23:59:59Z"}` | Khoảng bắt đầu–kết thúc của giai đoạn chi tiết, mặc định 14 ngày sắp tới và không vượt window được xác nhận. |
| `source_revisions` | `Dict[str, int]` | JSON Object mapping nguồn -> revision | `{"availability_rev": 1, "preference_rev": 2, "calendar_context_rev": 1}` | Các revision của giờ rảnh, cấu hình, lịch nền và CalendarContext dùng tính; xác nhận phải kiểm lại các nguồn này. |
| `generation_tier` | `str` (Enum) | `enum: 'tier_1_gemini' \| 'tier_2_groq' \| 'tier_3_rule_based'` | `"tier_1_gemini"` | Tầng AI / Rule-based đã sinh phương án bẻ việc (Tầng 3 = Python tự cắt phiên cố định 20–25p khi mất kết nối cả Gemini lẫn Groq). |

---

### O07 Task
*Công việc chính thức được tạo sau xác nhận UF2. Luồng sử dụng: UF2, UF5–6. Hình thức: Bảng DB (`tasks`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `title` | `str` | Chuỗi ký tự (tối đa 255 ký tự) | `"Viết báo cáo chuyên đề AI"` | Tên công việc đã được xác nhận. |
| `description` | `Optional[str]` | Văn bản dài | `"Hoàn thành slide và tài liệu tóm tắt 10 trang"` | Nội dung công việc cần thực hiện. |
| `scope` | `Optional[str]` | Văn bản | `"10 trang Word, định dạng chuẩn APA"` | Đầu ra và giới hạn đã thống nhất; giúp đánh giá hoàn thành. |
| `extra_requirements` | `Optional[List[str]]` | JSON Array chuỗi ký tự | `["Không xếp lịch sau 22h đêm"]` | Giữ các yêu cầu riêng từ bảng tóm tắt UF1. |
| `time_constraint` | `Dict[str, Any]` | JSON Object theo `O03` | `{"kind": "deadline", "deadline_at": "2026-11-05T17:00:00Z"}` | Giới hạn bắt đầu và hoàn tất của công việc. |
| `importance` | `str` (Enum) | `enum: 'low' \| 'medium' \| 'high' \| 'urgent'` | `"high"` | Mức quan trọng do người dùng chọn để hỗ trợ ưu tiên lịch. |
| `status` | `str` (Enum) | `enum: 'in_progress' \| 'completed' \| 'cancelled' \| 'archived'` | `"in_progress"` | Trạng thái thực hiện của Task; không đồng nghĩa trạng thái đã chia hay đã xếp và không tự complete do timer hết. Dùng `'cancelled'` thay cho xóa cứng. |
| `source_draft_id` | `Optional[UUID]` | UUIDv4 (Unique, Foreign Key -> `planning_drafts.id`) | `"d12a8b21-6543-4c91-a201-9876543210aa"` | Khóa ngoại 1 chiều liên kết nháp nguồn và ngăn một nháp tạo nhiều Task. |
| `decomposition_state` | `str` (Enum) | `enum: 'not_started' \| 'partial' \| 'complete'` | `"partial"` | Tổng hợp từ các PlanningPart: not_started, partial, complete; còn phần chưa chia thì chưa complete. |
| `scheduling_state` | `str` (Enum) | `enum: 'not_scheduled' \| 'partial' \| 'complete'` | `"partial"` | Tổng hợp: not_scheduled, partial, complete; chỉ complete khi toàn bộ phạm vi đã được chia và xếp đủ. |
| `availability_ref` | `Optional[Dict[str, Any]]` | JSON Object tham chiếu giờ rảnh | `{"source": "manual", "revision": 1}` | Giờ rảnh và revision đã xác nhận áp dụng cho Task; giúp tái lập lịch đúng phạm vi người dùng cho phép. |
| `avoidance_count` | `int` | Số nguyên không âm (`count >= 0`, Default = `0`) | `2` | Bộ đếm dẫn xuất số lần công việc bị né tránh/bỏ qua/tạm hoãn (tự tăng khi `O13 CheckIn` ghi nhận `skipped`/`postpone`), phục vụ truy vấn $O(1)$ ($\le 300\text{ms}$) cho **Algorithmic Priority Queue**. |

---

### O08 Step
*Một bước có đầu ra trong công việc. Luồng sử dụng: UF2–3, UF5–6. Hình thức: Bảng DB (`steps`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `task_id` | `UUID` | UUIDv4 (Foreign Key -> `tasks.id`) | `"e56a8b21-6543-4c91-a201-9876543210bb"` | Công việc sở hữu bước này. |
| `title` | `str` | Chuỗi ký tự (tối đa 255 ký tự) | `"Tìm 5 bài báo khoa học về Prompting"` | Hành động cần làm ở bước này. |
| `expected_output` | `Optional[str]` | Văn bản | `"5 file PDF tài liệu được lưu trong thư mục Research"` | Đầu ra dùng hỏi người dùng bước đã xong hay chưa. |
| `order_index` | `int` | Số nguyên dương (`order >= 1`) | `1` | Thứ tự Step trong Task; phải kết hợp order_index của PlanningPart để giữ đúng thứ tự qua các lần chia dần. |
| `estimated_base_seconds` | `int` | Giây (`seconds`, thường 1200–3600s) | `1500` | Ước lượng làm chính gốc, chưa buffer, micro-task hoặc nghỉ; không bị ghi đè bằng actual hay thời lượng đã nhân k. |
| `remaining_base_seconds` | `int` | Giây (`seconds`) | `1500` | Ước lượng phần còn lại để lập lịch tiếp; không tự lấy timer trừ estimate. |
| `buffer_multiplier` | `float` | Hệ số thực (`float >= 1.0`, thường 1.2–1.5) | `1.25` | Hệ số dự phòng đã dùng; chỉ nhân một lần vào làm chính. |
| `status` | `str` (Enum) | `enum: 'pending' \| 'scheduled' \| 'in_progress' \| 'completed' \| 'skipped'` | `"scheduled"` | Bước đang chờ, đang làm, hoàn thành hay bị bỏ qua. |
| `planning_part_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `planning_parts.id`) | `"f11a8b21-6543-4c91-a201-9876543210cc"` | PlanningPart sở hữu bước sau xác nhận; giữ liên kết để lập phần tiếp mà không tạo trùng phần cũ. |
| `estimate_source` | `str` (Enum) | `enum: 'ai_suggested' \| 'user_defined' \| 'user_adjusted'` | `"ai_suggested"` | Nguồn ước lượng gốc và revision liên quan; phân biệt AI đề xuất với người dùng đã chỉnh/xác nhận. |
| `cognitive_load` | `Optional[str]` (Enum) | `enum: 'light' \| 'medium' \| 'heavy' \| null` | `"medium"` | Mức tiêu hao năng lượng nhận thức của bước do **AI tự gắn nhãn ngầm ở UF2** (tuyệt đối không hỏi thêm người dùng ở UF1), hỗ trợ thuật toán ưu tiên xếp lịch. |

---

### O09 Session
*Một phiên dành riêng cho một Task qua Step liên quan. Luồng sử dụng: UF2–3, UF5–6. Hình thức: Bảng DB (`sessions`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `step_id` | `UUID` | UUIDv4 (Foreign Key -> `steps.id`) | `"f78a8b21-6543-4c91-a201-9876543210cc"` | Bước được thực hiện trong phiên; từ đó xác định Task. |
| `scheduled_start` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:00:00Z"` | Giờ bắt đầu theo lịch đã xếp. |
| `scheduled_end` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:32:00Z"` | Giờ kết thúc; bằng giờ bắt đầu cộng tổng thời lượng phiên. |
| `micro_task_seconds` | `int` | Giây (`seconds`, cố định 120s) | `120` | Thời lượng khởi động duy nhất ở đầu phiên; không lặp sau nghỉ. |
| `work_seconds` | `int` | Giây (`seconds`) | `1500` | Tổng thời gian làm chính của mọi quãng, đã gồm buffer phân bổ. |
| `rest_seconds` | `int` | Giây (`seconds`) | `300` | Tổng nghỉ bên trong phiên; không gồm 15 phút giữa hai Session. |
| `segments` | `List[Dict[str, Any]]` | JSON Array các quãng làm/nghỉ | `[{"order_index": 1, "kind": "micro_task", "duration_seconds": 120}, ...]` | Các quãng làm/nghỉ có thứ tự; micro-task được giữ riêng ở đầu phiên. |
| `segments[].order_index` | `int` | Số nguyên dương (`1, 2, 3...`) | `1` | Vị trí của từng quãng trong phiên. |
| `segments[].kind` | `str` (Enum) | `enum: 'micro_task' \| 'work' \| 'rest'` | `"work"` | Quãng làm việc (work) hoặc nghỉ (rest). |
| `segments[].duration_seconds` | `int` | Giây (`seconds`) | `1500` | Thời lượng quãng; cộng các quãng để tính tổng làm và nghỉ. |
| `duration_seconds` | `int` | Giây (`seconds` = micro + work + rest) | `1920` | Tổng micro-task (120s) + làm chính (1500s) + nghỉ bên trong (300s); toàn bộ được giữ chỗ. |
| `micro_task_text` | `Optional[str]` | Chuỗi ký tự hành động 2p | `"Mở Google Scholar và gõ từ khóa Prompt Engineering"` | Nội dung hành động khởi động duy nhất ở đầu Session; không tạo micro-task mới sau nghỉ bên trong. |
| `schedule_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản lịch đã xác nhận chứa Session; dùng loại yêu cầu start và lời nhắc đã cũ. |
| `status` | `str` (Enum) | `enum: 'scheduled' \| 'active' \| 'completed' \| 'cancelled'` | `"scheduled"` | Trạng thái lịch của Session, như đã xếp hoặc đã hủy; kết quả thực hiện nằm ở Run/CheckIn và không thay kết quả Task. |
| `calendar_event_link_id` | `Optional[UUID]` | UUIDv4 (**Read-only ORM Derived** từ `O24.session_id`) | `"c99a8b21-6543-4c91-a201-9876543210aa"` | Thuộc tính dẫn xuất ORM tham chiếu `O24` hiện hành khi đã xuất Calendar (**không tạo cột FK vật lý vòng tròn** trên bảng `sessions`). `session_id` vẫn là ID nội bộ; không thay bằng `google_event_id`. |

---

### O10 FeasibilityReport
*Kết quả kiểm tra khả thi của lịch. Luồng sử dụng: UF2, UF6. Hình thức: In-memory DTO.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `status` | `str` (Enum) | `enum: 'valid' \| 'deficit' \| 'conflict' \| 'unsolvable' \| 'insufficient_data'` | `"valid"` | Kết quả theo phạm vi: có lịch hợp lệ, đã chứng minh vi phạm, chưa tìm được phương án hoặc chưa đủ dữ liệu; không gọi mọi thất bại tìm kiếm là vô nghiệm. |
| `required_seconds` | `int` | Giây ($T_{\text{req}}$) | `3840` | Tổng thời lượng các Session, gồm micro-task và nghỉ bên trong. |
| `available_seconds` | `int` | Giây ($T_{\text{free}}$) | `7200` | Tổng giờ rảnh còn lại sau khi loại lịch bận, không cộng trùng. |
| `unscheduled_seconds` | `int` | Giây (`seconds`) | `0` | Thời lượng đã xác định nhưng chưa xếp trong phạm vi report; không đồng nghĩa thiếu từng ấy tổng giờ. Phần chưa ước lượng được liệt kê riêng. |
| `verification_scope` | `str` (Enum) | `enum: 'horizon_part' \| 'full_task'` | `"horizon_part"` | Phạm vi được kiểm gồm Task/phần được chọn và horizon; không dùng kết quả phần đầu để tuyên bố toàn Task kịp hạn. |
| `fully_scheduled` | `bool` | Boolean (`true` / `false`) | `true` | Chỉ true khi toàn bộ phạm vi ghi trong report có Session cụ thể và qua các kiểm tra; không tự đồng nghĩa toàn Task đã có lịch. |
| `blocking_reasons` | `List[str]` | Mảng chuỗi ký tự nguyên nhân | `["Khoảng rảnh lúc 20:00 chỉ có 10 phút, không đủ xếp phiên 20p"]` | Lý do vướng: ô quá ngắn, sai thứ tự, thiếu khoảng nghỉ giữa phiên… |
| `deficit_seconds` | `int` | Giây ($\Delta T = \max(0, T_{\text{req}} - T_{\text{free}})$) | `0` | Cận dưới thiếu thời lượng khi so required_seconds với available_seconds cho phần đã ước lượng. Bằng 0 vẫn có thể không xếp được; không cộng 900 giây vào từng phiên. |
| `unestimated_part_ids` | `List[UUID]` | JSON Array of UUIDv4 strings | `["p22a8b21-6543-4c91-a201-9876543210cc"]` | Các phần chưa có ước lượng đủ nghĩa; giữ rõ phần chưa biết, không tự gán 0 phút. |
| `unplanned_part_ids` | `List[UUID]` | JSON Array of UUIDv4 strings | `["p33a8b21-6543-4c91-a201-9876543210dd"]` | Các phần còn chưa chia hoặc chưa xếp đầy đủ; giúp hiển thị phần còn lại của Task. |
| `extension_candidate_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-11-06T12:00:00Z"` | Mốc kết thúc được gợi ý sau khi thử xếp thêm; không gọi là hạn tối thiểu tối ưu nếu chưa chứng minh. |

---

### O11 ScheduleVersion
*Phiên bản lịch chính thức theo người dùng. Luồng sử dụng: UF2–3, UF6. Hình thức: Bảng DB (`schedule_versions`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `version_number` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Số phiên bản lịch; tăng khi một thay đổi được duyệt. |
| `base_version` | `Optional[int]` | Số nguyên (`counter >= 0`) | `1` | Bản lịch nền dùng tạo thay đổi; giúp phát hiện dữ liệu cũ. |
| `approved_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T14:35:10Z"` | Thời điểm người dùng xác nhận bản lịch này. |
| `affected_task_ids` | `List[UUID]` | Cột `JSON` lưu mảng chuỗi UUIDv4 | `["32fa8b21-6543-4c91-a201-9876543210ef"]` | Những công việc bị thay đổi trong lần cập nhật (dùng cột `JSON` để tương thích SQLite & PostgreSQL). |
| `min_inter_session_gap_seconds` | `int` | Giây (`seconds`, cố định 900s) | `900` | Lưu quy tắc cách phiên 900 giây (15 phút) của bản lịch. |
| `alarm_sync_state` | `str` (Enum) | `enum: 'synced' \| 'pending_sync' \| 'failed'` | `"synced"` | Kết quả thiết bị xác nhận đã cập nhật lời nhắc; không suy từ việc lưu DB hoặc đồng bộ Calendar thành công. |
| `calendar_sync_summary` | `str` (Enum) | `enum: 'all_synced' \| 'partially_synced' \| 'pending' \| 'failed'` | `"all_synced"` | Trạng thái tổng hợp từ O24/O25 cho phiên bản lịch; lưu app thành công và ghi Google thành công là hai kết quả riêng. |

---

### O12 SessionRun
*Lần thực hiện thực tế của một Session. Luồng sử dụng: UF3–5. Hình thức: Bảng DB (`session_runs`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `UUID` | UUIDv4 (Foreign Key -> `sessions.id`) | `"a11a8b21-6543-4c91-a201-9876543210dd"` | Phiên theo lịch mà người dùng đang thực hiện. |
| `started_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:01:05Z"` | Thời điểm thực sự bắt đầu sau khi thao tác được chấp nhận. |
| `ended_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:33:05Z"` | Mốc kết thúc thực tế của Run, ghi ngay khi hết toàn phiên hoặc user dừng; không phải giờ trả lời check-in hay server nhận đồng bộ. |
| `run_state` | `str` (Enum) | `enum: 'running' \| 'paused' \| 'awaiting_checkin' \| 'completed' \| 'abandoned'` | `"awaiting_checkin"` | Lần thực hiện đang chạy, chờ phản hồi hay đã đóng; nghỉ nội bộ vẫn đang chạy. |
| `active_phase` | `str` (Enum) | `enum: 'micro_task' \| 'work' \| 'rest'` | `"work"` | Đang khởi động, làm chính hay nghỉ bên trong phiên. |
| `segment_index` | `int` | Số nguyên vị trí quãng (`1, 2, 3...`) | `2` | Quãng đang chạy; giúp phục hồi đúng vị trí khi mở lại app. |
| `micro_elapsed_seconds` | `int` | Giây (`seconds`) | `120` | Thời gian micro-task thực tế, tách khỏi phần làm chính. |
| `work_elapsed_seconds` | `int` | Giây (`seconds`) | `1500` | Số giây timer ghi ở quãng làm chính; giữ dữ liệu đã ghi dù user không trả lời check-in, không coi đây là bằng chứng hoàn thành. |
| `rest_elapsed_seconds` | `int` | Giây (`seconds`) | `300` | Thời gian nghỉ bên trong đã ghi nhận; không đưa vào học buffer. |
| `actual_work_seconds` | `Optional[int]` | Giây (`seconds`) | `1500` | Giá trị làm chính dùng đối chiếu, có nguồn rõ; thiếu dữ liệu là null. Không tính micro-task, nghỉ hoặc chờ check-in. |
| `actual_source` | `Optional[str]` (Enum) | `enum: 'timer_proxy' \| 'self_report' \| null` | `"timer_proxy"` | timer_proxy hoặc self_report; null nếu chưa có nguồn hợp lệ. Sửa tự báo cáo phải giữ lịch sử nguồn trước. |
| `outcome` | `Optional[str]` (Enum) | `enum: 'completed' \| 'incompleted' \| 'emergency' \| 'skipped' \| 'no_response'` | `"completed"` | Kết quả riêng của phiên; không thay xác nhận Step hoặc Task đã xong. |
| `checkin_timeout_at` | `Optional[datetime]` | ISO 8601 UTC (= `ended_at + 300s`) | `"2026-10-29T19:38:05Z"` | **Mốc UTC bằng `ended_at + 300 giây` (5 phút):** Giữ DND trong lúc chờ Check-in cuối phiên; tắt DND ngay khi user trả lời nghỉ/xong/bận/bỏ qua, chuyển quyền sở hữu sang Run mới nếu làm tiếp phiên sau, **HOẶC dùng Exact Alarm tại mốc này để tự động thu hồi DND** nếu hết 5 phút không phản hồi (chống kẹt DND kể cả khi app bị kill). |
| `settings_snapshot` | `Dict[str, Any]` | JSON Object snapshot cấu hình | `{"work_interval_seconds": 1500, "rest_interval_seconds": 300, "micro_task_seconds": 120}` | Cấu trúc Session và cấu hình effective tại lúc start; đổi cài đặt sau đó không sửa Run hiện tại. |

---

### O13 CheckIn
*Phản hồi về kết quả thực hiện. Luồng sử dụng: UF5. Hình thức: Bảng DB (`check_ins`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `run_id` | `UUID` | UUIDv4 (Foreign Key -> `session_runs.id`) | `"b22a8b21-6543-4c91-a201-9876543210ee"` | Lần thực hiện mà phản hồi này nói tới. |
| `kind` | `str` (Enum) | `enum: 'mid_session' \| 'post_session'` | `"post_session"` | Phân biệt hỏi giữa phiên và hỏi khi kết thúc toàn phiên. |
| `response_state` | `str` (Enum) | `enum: 'open' \| 'answered' \| 'timed_out'` | `"answered"` | Cuối phiên dùng open, answered, timed_out. Trả lời sau timeout cập nhật cùng CheckIn thành answered, không tạo Run mới. |
| `session_outcome` | `str` (Enum) | `enum: 'completed' \| 'incompleted' \| 'emergency' \| 'skipped'` | `"completed"` | Kết quả phiên người dùng xác nhận; không tự suy từ timer. |
| `step_completed` | `bool` | Boolean (`true` / `false`) | `true` | Người dùng xác nhận đầu ra Step đã đạt hay chưa. |
| `task_completed` | `Optional[bool]` | Boolean (`true` / `false` / `null`) | `false` | Xác nhận toàn bộ phạm vi Task, kể cả phần chưa chia; không lấy việc xong Step cuối của phần đang lập lịch làm hoàn tất Task. |
| `skip_scope` | `Optional[str]` (Enum) | `enum: 'session_only' \| 'postpone' \| 'entire_step'` | `"session_only"` | Phân biệt bỏ phiên, để làm sau hoặc bỏ cả Step. |
| `remaining_note` | `Optional[str]` | Văn bản ngắn | `"Chỉ còn thiếu phần kết luận 1 đoạn"` | Mô tả phần chưa xong để hỗ trợ ước lượng và xếp lại. |
| `asked_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:33:05Z"` | Thời điểm mở câu hỏi; phân biệt câu hỏi giữa phiên và check-in cuối phiên. |
| `responded_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:34:10Z"` | Thời điểm người dùng thực sự trả lời; không dùng mốc này để kéo dài ended_at hoặc actual. |
| `response_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `1` | Phiên bản phản hồi để sửa câu trả lời trên cùng CheckIn và tránh ghi đè/đếm kết quả lặp. |

---

### O14 Thought
*Suy nghĩ được cất trong phiên. Luồng sử dụng: UF4, chuyển sang UF1–2 khi tạo việc. Hình thức: Bảng DB (`thoughts`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `client_thought_id` | `str` | UUIDv4 Client String | `"e33a8b21-6543-4c91-a201-9876543210fa"` | Mã ổn định giúp lưu hoặc đồng bộ không nhân đôi suy nghĩ. |
| `source_run_id` | `UUID` | UUIDv4 (Foreign Key -> `session_runs.id`) | `"b22a8b21-6543-4c91-a201-9876543210ee"` | Lần thực hiện đang diễn ra khi suy nghĩ được ghi lại. |
| `input_type` | `str` (Enum) | `enum: 'raw_audio' \| 'text_fallback'` | `"raw_audio"` | Nội dung được ghi âm hay nhập chữ dự phòng. |
| `local_audio_path` | `Optional[str]` | Vị trí file thiết bị (`.m4a`/`.wav`) | `"/recordings/thought_20261029_191500.m4a"` | Vị trí file trên thiết bị; chỉ gửi STT khi user chọn tạo việc sau phiên. Không phải URL backend tự đọc hoặc ghi vào log. |
| `text_content` | `Optional[str]` | Văn bản ngắn | `"Nhớ gọi điện hỏi giảng viên format nộp"` | Nội dung chữ khi dùng phương án nhập dự phòng (sau 3 lần lỗi mic); **không** lưu transcript STT tại đây để bảo vệ ranh giới Raw Audio của UF4. |
| `attempt_count` | `int` | Số nguyên đếm lần thử (`1 <= count <= 3`) | `1` | Số lượt thu/lưu chủ động; tối đa ba lượt cho một lần cất. |
| `linked_draft_id` | `Optional[UUID]` | UUIDv4 (**Read-only ORM Derived** từ `O05.source_thought_id`) | `"d12a8b21-6543-4c91-a201-9876543210aa"` | Nháp tạo việc từ suy nghĩ (suy ra qua `relationship` từ `O05.source_thought_id`, không tạo cột FK vòng lặp vật lý trên bảng `thoughts`); chưa chứng minh đã tạo Task. |
| `converted_task_id` | `Optional[UUID]` | UUIDv4 (**Read-only ORM Derived** qua `O05 -> O07.source_draft_id`) | `"32fa8b21-6543-4c91-a201-9876543210ef"` | Task thật sau UF2 xác nhận và lưu thành công (suy ra qua chuỗi `O05 -> O07.source_draft_id`, không tạo cột FK vòng lặp vật lý). |
| `capture_state` | `str` (Enum) | `enum: 'recording' \| 'saved' \| 'failed' \| 'discarded'` | `"saved"` | Đang thu, đã lưu, lỗi hoặc hủy mềm (`discarded` sau 5 giây Undo trên Mobile); không báo Đã lưu khi chỉ mới thu xong. |
| `retention_expires_at` | `Optional[datetime]` | ISO 8601 UTC | `null` | Mốc giữ/xóa khi đã có chính sách; null khi chưa áp dụng. Không mặc định một số ngày khi nhóm chưa chốt. |

---

### O15 RescheduleProposal
*Phương án đổi lịch đang chờ người dùng xem. Luồng sử dụng: UF6–7. Hình thức: Bảng DB (`reschedule_proposals`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `trigger_reason` | `str` (Enum) | `enum: 'incomplete' \| 'busy' \| 'setting_changed' \| 'deadline_risk' \| 'skipped'` | `"incomplete"` | Nguyên nhân đổi lịch: bận, chưa xong, yêu cầu mới, đổi cài đặt… |
| `affected_task_ids` | `List[UUID]` | Cột `JSON` lưu mảng chuỗi UUIDv4 | `["32fa8b21-6543-4c91-a201-9876543210ef"]` | Phạm vi các Task thực sự bị tác động (tương thích SQLite & PostgreSQL). |
| `base_schedule_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Bản lịch nền; dùng ngăn áp một phương án đã cũ. |
| `proposed_changes` | `List[Dict[str, Any]]` | JSON Array các thao tác thay đổi | `[{"action": "reschedule_session", "session_id": "a11...", "new_start": "2026-10-30T19:00:00Z"}]` | Các phần mới được chia, Steps/Sessions mới, phiên thêm/hủy/dời và giờ trước–sau. Mở rộng dùng cùng Task, không ghi đè phần đã thực hiện. |
| `feasibility` | `Dict[str, Any]` | JSON Object (`O10` Schema) | `{"status": "valid", "required_seconds": 1920, "available_seconds": 7200}` | Kết quả kiểm cả collision, thời hạn và khoảng cách 15 phút. |
| `explanation` | `str` | Câu thấu cảm ngắn gọn (Guilt-free) | `"Não bạn hôm nay đã làm việc rất chăm chỉ rồi! Mình dời 1 phiên dở dang sang tối mai lúc 19:00 nhé."` | Lý do ngắn gọn để người dùng hiểu phương án. |
| `generation_tier` | `str` (Enum) | `enum: 'tier_1_gemini' \| 'tier_2_groq' \| 'tier_3_rule_based'` | `"tier_1_gemini"` | Tầng AI / Rule-based đã sinh lời giải thích dời lịch (`explanation`) theo cơ chế dự phòng 3 tầng ở UF6. |
| `status` | `str` (Enum) | `enum: 'pending_approval' \| 'approved' \| 'rejected' \| 'superseded'` | `"pending_approval"` | Đang chờ, được duyệt, bị từ chối hoặc bị bản mới thay thế. |
| `proposal_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `1` | Phiên bản phương án; duyệt phải đúng bản đã xem. |
| `allowed_task_ids` | `List[UUID]` | Cột `JSON` lưu mảng chuỗi UUIDv4 | `["32fa8b21-6543-4c91-a201-9876543210ef"]` | Phạm vi người dùng cho phép dời; `affected_task_ids` là tập thực sự bị tác động và không được vượt phạm vi này. |
| `source_revisions` | `Dict[str, int]` | JSON Object mapping nguồn -> rev | `{"availability_rev": 1, "preference_rev": 2, "calendar_context_rev": 1}` | Revision giờ rảnh, cấu hình và CalendarContext nguồn bên cạnh base_schedule_revision; nguồn đổi thì phải kiểm và trình lại. |
| `supersedes_proposal_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `reschedule_proposals.id`) | `"b88a8b21-6543-4c91-a201-9876543210aa"` | Bản cũ bị thay thế bởi proposal này; chỉ một bản hiện hành còn được phép duyệt cho mỗi user. |

---

### O16 Reminder
*Lời nhắc gắn với phiên chính thức. Luồng sử dụng: UF3, UF6–7. Hình thức: Bảng DB (`reminders`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `UUID` | UUIDv4 (Foreign Key -> `sessions.id`) | `"a11a8b21-6543-4c91-a201-9876543210dd"` | Phiên mà lời nhắc thuộc về; không nhắc từ candidate chưa duyệt. |
| `schedule_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Bản lịch tạo lời nhắc; dùng bỏ lời nhắc cũ sau đổi lịch. |
| `kind` | `str` (Enum) | `enum: 'pre_session' \| 'check_in'` | `"pre_session"` | Nhắc trước phiên hoặc check-in. Micro-task là chặng đầu Session, không phải một alarm riêng tự bắt đầu trước phiên. |
| `fire_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T18:45:00Z"` | Thời điểm dự kiến phát lời nhắc. |
| `dedupe_key` | `str` | Chuỗi khóa duy nhất | `"rem-sess-a11-pre-900-v2"` | Khóa chống tạo hoặc phát trùng cùng một lời nhắc. |
| `delivery_state` | `str` (Enum) | `enum: 'scheduled' \| 'delivered' \| 'dismissed' \| 'cancelled' \| 'failed'` | `"scheduled"` | Lời nhắc đã lên lịch, đã phát, user gạt bỏ, bị vô hiệu hóa khi lên `schedule_revision` mới (`cancelled`), hoặc gặp lỗi. |
| `user_action` | `Optional[str]` (Enum) | `enum: 'opened' \| 'dismissed' \| 'snoozed'` | `"opened"` | Thao tác thực nhận được, như mở hoặc bỏ qua; không suy người dùng đã đọc. |
| `device_id` | `Optional[str]` | Chuỗi định danh thiết bị | `"dev-android-pixel7-987a6b5c"` | Thiết bị thực thi lời nhắc; giúp phân biệt lưu lịch với đã cài thông báo trên thiết bị cụ thể. |

---

### O17 DeviceState
*Quyền và trạng thái do thiết bị báo về. Luồng sử dụng: UF3–5. Hình thức: Client-only / API State.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `device_id` | `str` | Chuỗi định danh thiết bị | `"dev-android-pixel7-987a6b5c"` | Nhận diện thiết bị hoặc bản cài ứng dụng. |
| `notification_permission` | `bool` | Boolean (`true` / `false`) | `true` | Thiết bị có quyền hiển thị thông báo hay không. |
| `microphone_permission` | `bool` | Boolean (`true` / `false`) | `true` | Có được dùng micro khi người dùng chủ động ghi âm. |
| `dnd_permission` | `bool` | Boolean (`true` / `false`) | `true` | Quyền hoặc khả năng điều khiển Không làm phiền. |
| `alarm_capability` | `bool` | Boolean (`true` / `false`) | `true` | Khả năng và quyền cài báo thức; khác quyền thông báo. |
| `active_run_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `session_runs.id`) | `"b22a8b21-6543-4c91-a201-9876543210ee"` | Lần thực hiện đang hoạt động trên thiết bị. |
| `last_schedule_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản lịch mới nhất thiết bị đã nhận. |
| `dnd_owner_run_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `session_runs.id`) | `"b22a8b21-6543-4c91-a201-9876543210ee"` | Run mà phần DND do app áp dụng thuộc về; timeout cũ không được tắt DND của Run mới hoặc DND do user bật. |
| `dnd_restore_snapshot` | `Optional[Dict[str, Any]]` | JSON Object trạng thái DND trước bật | `{"was_dnd_active": false, "interruption_filter": "all"}` | Dữ liệu phục hồi phần DND app đã thay đổi, chủ yếu giữ tại thiết bị theo khả năng hệ điều hành. |
| `dnd_apply_state` | `str` (Enum) | `enum: 'success' \| 'failed' \| 'unsupported'` | `"success"` | Thiết bị báo áp dụng thành công, thất bại hoặc không hỗ trợ; backend không tự suy là đã bật. |
| `dnd_restore_state` | `str` (Enum) | `enum: 'holding_for_checkin' \| 'restored' \| 'failed' \| 'pending'` | `"restored"` | Đang giữ DND trong cửa sổ 5 phút hỏi Check-in UF5 (`holding_for_checkin`), đã khôi phục (`restored`), lỗi hoặc đang chờ đối soát cục bộ; không phụ thuộc push từ backend. |
| `reported_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:01:05Z"` | Mốc thiết bị báo trạng thái; trạng thái cũ không là bằng chứng DND hiện tại đã được dọn. |

---

### O18 CommandReceipt
*Chống ghi lặp và ghi đè dữ liệu cũ. Luồng sử dụng: Dùng chung. Hình thức: Bảng DB (`command_receipts`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `command_id` | `UUID` | UUIDv4 Client String | `"f44a8b21-6543-4c91-a201-9876543210fb"` | Mã một ý định thao tác; giữ nguyên khi gửi lại do mất phản hồi. |
| `command_type` | `str` (Enum) | `enum: 'confirm_plan' \| 'start_session' \| 'submit_checkin' \| 'approve_reschedule'` | `"start_session"` | Loại thao tác như xác nhận kế hoạch, bắt đầu hoặc duyệt đổi lịch. |
| `payload_hash` | `str` | Hex String SHA-256 (64 ký tự) | `"a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e"` | Nhận ra cùng mã lệnh nhưng nội dung gửi đã khác. |
| `expected_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản người gửi đang dựa vào để phát hiện xung đột. |
| `result_reference` | `Optional[Dict[str, Any]]` | JSON Object kết quả phản hồi | `{"status": "success", "session_run_id": "b22a8b21-..."}` | Kết quả đã tạo; gửi lại cùng lệnh nhận lại kết quả này. |
| `applied_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:01:06Z"` | Thời điểm thao tác được ghi thành công. |
| `queue_state` | `str` (Enum) | `enum: 'queued' \| 'synced' \| 'conflict'` | `"synced"` | Trạng thái hàng chờ ở thiết bị; là metadata cục bộ, tách với receipt thao tác đã áp dụng trên server. |

---

### O19 AdaptationSuggestion
*Gợi ý cá nhân hóa để người dùng lựa chọn. Luồng sử dụng: UF7. Hình thức: Bảng DB (`adaptation_suggestions`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `kind` | `str` (Enum) | `enum: 'buffer_multiplier' \| 'reminder_frequency'` | `"buffer_multiplier"` | Gợi ý đổi buffer hoặc giảm tần suất lời nhắc. |
| `sample_count` | `int` | Số nguyên đếm Step hoàn tất (`count >= 3`) | `5` | Số mẫu hợp lệ đã dùng; một Step không thành nhiều mẫu theo số phiên. |
| `current_value` | `float` | Hệ số float / giá trị cấu hình | `1.2` | Giá trị cấu hình hiện đang có hiệu lực. |
| `suggested_value` | `float` | Hệ số float / giá trị cấu hình | `1.35` | Giá trị mới được gợi ý, chưa tự áp dụng. |
| `reason` | `str` | Văn bản giải thích khách quan | `"Trong 5 bước vừa qua, bạn thường cần thêm trung bình 7 phút. Tăng nhẹ hệ số đệm sẽ giúp lịch trình vừa vặn hơn."` | Giải thích vì sao có gợi ý; không suy diễn chẩn đoán. |
| `policy_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `1` | Quy tắc hoặc thuật toán đã tạo gợi ý. |
| `status` | `str` (Enum) | `enum: 'pending' \| 'accepted' \| 'rejected' \| 'superseded'` | `"pending"` | Gợi ý đang chờ, được chấp nhận, bị từ chối hoặc thay thế. |
| `sample_refs` | `List[UUID]` | Cột `JSON` lưu mảng chuỗi Step UUIDv4 | `["e56a8b21-6543-4c91-a201-9876543210bb"]` | Tham chiếu các Step hoàn tất cùng phạm vi và actual hợp lệ; một Step nhiều Session vẫn chỉ một mẫu. |
| `model_revision` | `Optional[str]` | Chuỗi phiên bản model AI | `null` | Phiên bản model nếu gợi ý có dùng AI; null khi chỉ dùng quy tắc hoặc thống kê. |
| `supersedes_suggestion_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `adaptation_suggestions.id`) | `"s11a8b21-6543-4c91-a201-9876543210ee"` | Thẻ cũ bị thay bằng gợi ý hiện hành của cùng vấn đề; không tạo nhiều thẻ vì một chuỗi sửa cài đặt. |

---

### O20 AuditEvent
*Sự kiện tối thiểu để đối soát và tính chỉ số. Luồng sử dụng: Dùng chung. Hình thức: Bảng DB (`audit_events`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `event_id` | `UUID` | UUIDv4 String | `"a55a8b21-6543-4c91-a201-9876543210fc"` | Mã sự kiện; giúp đồng bộ mà không đếm lặp. |
| `event_type` | `str` (Enum) | `enum: 'session_started' \| 'session_ended' \| 'thought_stored' \| 'schedule_modified'` | `"session_started"` | Loại sự kiện như bắt đầu, kết thúc hoặc đổi lịch. |
| `resource_ref` | `str` | URI / Resource Key String | `"session:a11a8b21-6543-4c91-a201-9876543210dd"` | Đối tượng liên quan để truy vết sự kiện. |
| `occurred_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:01:05Z"` | Thời điểm sự kiện thực sự xảy ra, kể cả khi offline. |
| `received_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T19:01:07Z"` | Thời điểm hệ thống nhận được; có thể muộn hơn lúc xảy ra. |
| `command_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `command_receipts.command_id`) | `"f44a8b21-6543-4c91-a201-9876543210fb"` | Liên kết lệnh nguồn, giúp đối soát retry và tác động. |
| `duration_source` | `Optional[str]` (Enum) | `enum: 'timer_proxy' \| 'self_report' \| null` | `"timer_proxy"` | Nguồn thời gian được ghi thống nhất với `O12.actual_source`: đồng hồ xấp xỉ (`timer_proxy`), người dùng tự báo cáo (`self_report`) hoặc chưa biết (`null`). |
| `resource_revision` | `Optional[int]` | Số nguyên (`counter >= 1`) | `2` | Revision dữ liệu tại thời điểm xảy ra để đối soát thay đổi, trả lời muộn và đồng bộ offline. |

---

### O21 CalendarContext
*Ảnh chụp dữ liệu lịch nguồn dùng tính và kiểm phương án. Không thay giờ rảnh user xác nhận. Luồng sử dụng: UF1–2, UF6–7. Hình thức: In-memory DTO.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `selected_calendar_ids` | `List[str]` | Mảng email / Calendar ID String | `["primary", "work_calendar@gmail.com"]` | Những lịch người dùng đã chọn và cho phép đọc. |
| `query_start_end` | `Dict[str, datetime]` | JSON Object (khoảng bắt đầu và kết thúc) | `{"start": "2026-10-29T00:00:00Z", "end": "2026-11-12T23:59:59Z"}` | Khoảng thời gian đã truy vấn; biết dữ liệu bao phủ đến đâu. |
| `busy_intervals` | `List[Dict[str, str]]` | JSON Array các khoảng ISO 8601 UTC | `[{"start": "2026-10-29T15:00:00Z", "end": "2026-10-29T16:30:00Z"}]` | Các khoảng bận có nguồn. Khi nhận diện sự kiện của chính Session phải dựa O24/O26; không trừ một khoảng gộp rồi vô tình bỏ luôn sự kiện bên ngoài trùng giờ. |
| `fetched_at` | `datetime` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T14:00:00Z"` | Thời điểm lấy dữ liệu; dùng đánh giá độ mới trước xác nhận. |
| `sync_state` | `str` (Enum) | `enum: 'valid' \| 'partial_error' \| 'unreachable'` | `"valid"` | Cho biết dữ liệu hợp lệ, lỗi một phần hoặc không truy cập được. |
| `calendar_errors` | `Optional[List[Dict[str, str]]]` | JSON Array chi tiết lỗi từng nguồn | `[{"calendar_id": "work@gmail.com", "error": "Rate limit"}]` | Lỗi của từng nguồn; tránh hiểu nhầm lỗi thành lịch trống. |
| `event_snapshots` | `List[Dict[str, Any]]` | JSON Array các snapshot sự kiện O26 | `[{"google_event_id": "ev-123", "summary": "Hop", "transparency": "opaque"}]` | Các sự kiện O26 đã đọc để nhận diện bản của app và thay đổi bên ngoài; không mặc định lưu toàn bộ nội dung Calendar dài hạn. |
| `coverage_state` | `str` (Enum) | `enum: 'full' \| 'partial' \| 'unreachable'` | `"full"` | Dữ liệu đã bao phủ đủ nguồn/thời gian được chọn, chỉ một phần hoặc không truy cập được; thiếu nguồn không được coi là lịch trống. |

---

### O22 PlanningPart
*Một phần phạm vi của Task để chia và lập lịch dần, kể cả khi chưa có Step. Luồng sử dụng: UF2, UF5–6. Hình thức: Bảng DB (`planning_parts`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `task_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `tasks.id`) | `"32fa8b21-6543-4c91-a201-9876543210ef"` | Task chính thức sở hữu phần này; trước confirm dùng tham chiếu draft/candidate, chưa giả có Task ID. |
| `client_part_key` | `str` | Chuỗi định danh ổn định phía Client | `"part-draft-01-phase-1"` | Mã phần ổn định từ nháp đến xác nhận; yêu cầu chia tiếp hoặc gửi lại không tạo trùng phần. |
| `scope_description` | `str` | Văn bản mô tả phạm vi | `"Phần 1: Nghiên cứu tài liệu và thu thập 5 bài báo khoa học"` | Phạm vi và đầu ra của phần công việc, giữ được cả phần chưa chia chi tiết. |
| `order_index` | `int` | Số nguyên dương (`order >= 1`) | `1` | Thứ tự các phần trong Task; lập phần tiếp phải tôn trọng phần trước và phụ thuộc liên quan. |
| `decomposition_state` | `str` (Enum) | `enum: 'not_started' \| 'partial' \| 'complete'` | `"complete"` | not_started, partial, complete tương ứng chưa chia, chia một phần và chia đủ phạm vi. |
| `scheduling_state` | `str` (Enum) | `enum: 'not_scheduled' \| 'partial' \| 'complete'` | `"complete"` | not_scheduled, partial, complete; chỉ complete khi toàn bộ phạm vi đã chia và có lịch đủ. |
| `unexpanded_scope` | `Optional[str]` | Văn bản phạm vi chưa chia | `null` | Phần mô tả phạm vi còn chưa chia thành Step; không biến thành Step/Session giả. |
| `rough_estimate_seconds` | `Optional[int]` | Giây (ước lượng sơ bộ, null nếu chưa biết) | `3600` | Ước lượng sơ bộ làm chính trước buffer cho phần này; chưa biết thì null, không cộng trùng với Steps đã ước lượng. |
| `estimate_source` | `Optional[str]` (Enum) | `enum: 'ai_preliminary' \| 'user_rough' \| 'historical_proxy' \| null` | `"ai_preliminary"` | Nguồn và độ mới của ước lượng sơ bộ; không dùng số chưa đáng tin để kết luận toàn Task kịp hạn. |

---

### O23 CalendarConnection
*Kết nối tài khoản và lựa chọn lịch đọc/ghi, quản lý trong UF7. Luồng sử dụng: UF1–2, UF6–7. Hình thức: Bảng DB (`calendar_connections`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `provider` | `str` (Enum) | `enum: 'google_calendar'` | `"google_calendar"` | Nguồn lịch tích hợp; hiện là Google Calendar. |
| `account_ref` | `str` | Email / Account identifier (ẩn token) | `"user.minhtrung@gmail.com"` | Tài khoản đã kết nối để hiển thị và phân biệt các kết nối; không phải access token. |
| `read_calendar_ids` | `List[str]` | Cột `JSON` lưu mảng Calendar ID String | `["primary", "work@gmail.com"]` | Các lịch user chọn đọc giờ bận; không mặc định đọc mọi lịch. |
| `write_calendar_id` | `Optional[str]` | Calendar ID String đích | `"c_proactive_focus@group.calendar.google.com"` | Một lịch đích có quyền ghi để xuất Session; có thể là lịch riêng ProActive. |
| `granted_scopes` | `List[str]` | Cột `JSON` lưu mảng OAuth Scope URLs | `["https://www.googleapis.com/auth/calendar.events"]` | Các quyền đã được cấp; có quyền đọc chưa có nghĩa được tạo/sửa/xóa. |
| `connection_state` | `str` (Enum) | `enum: 'connected' \| 'read_only' \| 'needs_reauth' \| 'disconnected'` | `"connected"` | Đang kết nối, chỉ đọc, cần cấp lại quyền hoặc đã ngắt; cho UI phản ánh đúng khả năng thực tế. |
| `credential_ref` | `str` | Key / Vault reference ID (ẩn) | `"vault://creds/oauth_google_user_01"` | Tham chiếu bí mật xác thực do backend quản lý; không trả token trong DTO thông thường hoặc log. |
| `last_successful_sync_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T14:00:00Z"` | Lần đồng bộ thành công gần nhất; không nhầm với lần thử gần nhất. |

---

### O24 CalendarEventLink
*Liên kết Session với sự kiện xuất lên lịch đích. Luồng sử dụng: UF2–3, UF5–7. Hình thức: Bảng DB (`calendar_event_links`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `UUID` | UUIDv4 (Foreign Key -> `sessions.id`) | `"a11a8b21-6543-4c91-a201-9876543210dd"` | ID Session nội bộ; giữ nguyên khi dời cùng một phiên. |
| `connection_id` | `UUID` | UUIDv4 (Foreign Key -> `calendar_connections.id`) | `"c01a8b21-6543-4c91-a201-9876543210aa"` | Kết nối O23 đã dùng để đồng bộ sự kiện. |
| `google_calendar_id` | `str` | Google Calendar ID String | `"primary"` | Lịch Google chứa sự kiện; event ID phải được hiểu cùng lịch và kết nối. |
| `google_event_id` | `Optional[str]` | Google Event ID String | `"7abcde1234567890fghijklmno_20261029T120000Z"` | ID sự kiện Google; có thể chưa có khi chưa tạo thành công. Không dùng thay Session ID. |
| `google_etag` | `Optional[str]` | ETag Hex / String | `"\"3245678901234000\""` | Phiên bản sự kiện đã biết để phát hiện cập nhật/xóa trên bản đã cũ. |
| `desired_schedule_revision` | `int` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản lịch app muốn xuất; giúp yêu cầu cũ không ghi đè thay đổi mới. |
| `synced_schedule_revision` | `Optional[int]` | Số nguyên tăng dần (`counter >= 1`) | `2` | Phiên bản lịch đã được Google xác nhận ghi thành công; có thể còn chậm hơn bản mong muốn. |
| `sync_state` | `str` (Enum) | `enum: 'pending' \| 'synced' \| 'failed' \| 'conflict' \| 'needs_reauth'` | `"synced"` | Chờ, đã đồng bộ, lỗi, xung đột hoặc cần kết nối lại; không xóa Task khi đồng bộ lỗi. |
| `last_synced_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `"2026-10-29T14:02:15Z"` | Mốc ghi thành công gần nhất của đúng liên kết. |

---

### O25 CalendarSyncJob
*Công việc đồng bộ sau khi lưu thay đổi hợp lệ; retry phải không gây tác động lặp. Luồng sử dụng: UF2, UF5–7. Hình thức: Bảng DB (`calendar_sync_jobs`).*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `connection_id` | `UUID` | UUIDv4 (Foreign Key -> `calendar_connections.id`) | `"c01a8b21-6543-4c91-a201-9876543210aa"` | Kết nối cần dùng; ngắt kết nối dừng job mới/đang chờ theo chính sách hiện hành. |
| `target_kind` | `str` (Enum) | `enum: 'session_event' \| 'direct_calendar_event'` | `"session_event"` | Phân biệt sự kiện gắn Session với sự kiện Calendar do user thao tác trực tiếp. |
| `target_ref` | `str` | UUIDv4 (O24) hoặc Google Event ID (O26) | `"c99a8b21-6543-4c91-a201-9876543210aa"` | O24 khi xuất Session hoặc định danh sự kiện O26 khi CRUD sự kiện ngoài app. |
| `operation` | `str` (Enum) | `enum: 'create' \| 'update' \| 'delete'` | `"create"` | create, update hoặc delete; thao tác đọc phục vụ context không phải job ghi. |
| `target_schedule_revision` | `Optional[int]` | Số nguyên tăng dần (`counter >= 1`) | `2` | Bản lịch đích khi job gắn Session; null cho thao tác sự kiện ngoài Task. |
| `expected_google_etag` | `Optional[str]` | ETag Hex / String | `"\"3245678901234000\""` | Bản sự kiện nguồn cho sửa/xóa; khác phiên bản phải đối soát, không ghi đè mù. |
| `payload_snapshot` | `Dict[str, Any]` | JSON Object payload gửi Google API | `{"summary": "Focus: Viết báo cáo", "start": "...", "end": "..."}` | Nội dung của thao tác đã được user xác nhận; không tự đổi payload của lệnh cũ để áp một phương án mới. |
| `idempotency_key` | `str` | Chuỗi UUID / Hash chống trùng | `"job-sess-a11-v2-create"` | Khóa ổn định để retry không tạo sự kiện hoặc tác động trùng; dùng cùng O18 khi phù hợp. |
| `job_state` | `str` (Enum) | `enum: 'pending' \| 'running' \| 'success' \| 'retry_needed' \| 'conflict' \| 'reauth_needed' \| 'cancelled'` | `"success"` | Chờ, đang chạy, thành công, cần thử lại, xung đột, cần kết nối lại hoặc bị hủy. |
| `attempt_count` | `int` | Số lần đã thử (`count >= 0`) | `0` | Số lần đã thử; khác ba lượt ghi âm của UF4. |
| `next_retry_at` | `Optional[datetime]` | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) | `null` | Mốc được phép thử lại theo lỗi và chính sách vận hành, chưa gắn một chu kỳ cố định. |
| `last_error_code` | `Optional[str]` | Chuỗi mã lỗi chuẩn hóa | `null` | Mã lỗi phục vụ trạng thái UI và đối soát; không chứa token hoặc toàn bộ nội dung riêng tư. |

---

### O26 CalendarEvent
*Dữ liệu đọc/CRUD sự kiện Google và nhận diện nguồn; không mặc định là một bảng sao chép Calendar. Luồng sử dụng: UF1–2, UF6–7. Hình thức: In-memory DTO / CRUD Model.*

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `connection_id` | `UUID` | UUIDv4 (Foreign Key -> `calendar_connections.id`) | `"c01a8b21-6543-4c91-a201-9876543210aa"` | Tài khoản/kết nối dùng đọc hoặc ghi sự kiện. |
| `google_calendar_id` | `str` | Google Calendar ID String | `"primary"` | Lịch chứa sự kiện, đi cùng google_event_id khi gọi CRUD. |
| `google_event_id` | `str` | Google Event ID String | `"ev-gcal-987654321_20261029"` | Mã sự kiện do Google trả hoặc ID tạo theo quy tắc provider; không nhầm với iCalUID. |
| `etag` | `str` | ETag Hex / String | `"\"3245678901234000\""` | Phiên bản sự kiện đang xem; dùng kiểm xung đột khi sửa/xóa. |
| `title` | `str` | Chuỗi ký tự tiêu đề sự kiện | `"Họp giao ban đồ án"` | Tiêu đề sự kiện để hiển thị và chỉnh theo quyền. |
| `description` | `Optional[str]` | Văn bản ghi chú | `"Thảo luận tiến độ tuần 1"` | Nội dung bổ sung khi người dùng muốn lưu; không bắt buộc đưa mọi yêu cầu riêng của Task lên Google. |
| `location` | `Optional[str]` | Địa điểm / Link meeting | `"Google Meet (meet.google.com/xyz)"` | Địa điểm nếu có; không ảnh hưởng cách xác nhận Task. |
| `all_day` | `bool` | Boolean (`true` / `false`) | `false` | Phân biệt cả ngày với sự kiện có giờ; giúp hiểu đúng start/end. |
| `start` | `str` | ISO 8601 UTC hoặc Date (`YYYY-MM-DD`) | `"2026-10-29T15:00:00Z"` | Mốc bắt đầu có giờ và múi giờ, hoặc ngày bắt đầu nếu all_day. |
| `end` | `str` | ISO 8601 UTC hoặc Date (`YYYY-MM-DD`) | `"2026-10-29T16:00:00Z"` | Mốc kết thúc không bao gồm chính đầu mút; với all_day là ngày kế sau ngày cuối, không tự đặt 23:59. |
| `transparency` | `str` (Enum) | `enum: 'opaque' \| 'transparent'` | `"opaque"` | Sự kiện có chiếm giờ bận hay chỉ để tham khảo; không mặc định mọi sự kiện nhìn thấy đều chặn lịch. |
| `status` | `str` (Enum) | `enum: 'confirmed' \| 'tentative' \| 'cancelled'` | `"confirmed"` | Trạng thái provider như confirmed, tentative hoặc cancelled; không có nghĩa Step/Task completed. |
| `session_link_id` | `Optional[UUID]` | UUIDv4 (Foreign Key -> `calendar_event_links.id`) | `null` | O24 nếu đây là sự kiện của một Session; null cho sự kiện ngoài app. |
| `reminders` | `Optional[Dict[str, Any]]` | JSON Object cấu hình thông báo Google | `{"useDefault": false, "overrides": []}` | Lời nhắc Calendar; sự kiện Session do app tạo mặc định không bật thêm nhắc trùng. Không tự sửa nhắc của sự kiện ngoài. |
| `recurrence_ref` | `Optional[Dict[str, Any]]` | JSON Object thông tin chuỗi lặp | `null` | Nhận diện sự kiện lặp/lần xuất hiện nếu có. Trước sửa/xóa phải làm rõ phạm vi; không ngầm áp cho toàn chuỗi. |

---

## 4. Trường Dữ liệu Chung cho Các Thực thể Persistent Lưu Server

Các bảng Database lưu trữ trên Backend (SQLAlchemy 2.0) tự động kế thừa nhóm trường chung này:

| Tên trường dữ liệu | Kiểu dữ liệu (Type) | Định dạng / Đơn vị (Format/Unit) | Ví dụ (Example) | Tác dụng & Ràng buộc |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `UUID` | UUIDv4 (Primary Key) | `"c8b3a720-3b4e-4e89-9a72-f1d7e2e8b910"` | Mã định danh của bản ghi trên server. |
| `user_id` | `UUID` | UUIDv4 (Foreign Key -> `users.id`) | `"11111111-2222-3333-4444-555555555555"` | Người dùng sở hữu dữ liệu; lấy từ ngữ cảnh đăng nhập (lưu ý: riêng bảng gốc `O00 User` / `users` dùng trực tiếp `id` làm khóa chính, không có cột `user_id` tự tham chiếu). |
| `created_at` | `datetime` | DateTime with timezone (ISO 8601 UTC) | `"2026-10-29T14:00:00Z"` | Thời điểm bản ghi được tạo. |
| `updated_at` | `datetime` | DateTime with timezone (ISO 8601 UTC) | `"2026-10-29T14:05:00Z"` | Thời điểm bản ghi được cập nhật gần nhất. |
| `revision` | `int` | Số nguyên tăng dần (`counter >= 1`, Default = 1) | `1` | Số phiên bản khi cần kiểm soát cập nhật đồng thời (Optimistic Locking). |

---

## 5. Mười Ba Quy tắc Bất biến Cần đọc cùng Dữ liệu (Strict Guardrails)

1. **Cấu trúc Phiên & Buffer:**
   - Session có đúng **một micro-task ở đầu (120s)**, sau đó là các quãng làm/nghỉ luân phiên (`work`/`rest`).
   - `duration_seconds` gồm toàn bộ phiên; buffer chỉ áp một lần lên làm chính.
2. **Khoảng cách An toàn 900 giây (15 phút) Giữa Hai Phiên:**
   - Hai Session kế tiếp của cùng user cách ít nhất **900 giây**, kể cả cùng Task hoặc khác Task.
   - Khoảng này nằm ngoài `duration_seconds`; phiên đứng riêng không cộng 15 phút trước/sau.
3. **Quy trình Lập lịch UF1 $\rightarrow$ UF2 & Rolling Horizon:**
   - UF1 chỉ giữ tóm tắt (`O04`) và nháp (`O05`).
   - Xác nhận UF2 lưu `Task`, `PlanningPart` và `Steps/Sessions` của phần được chọn trong cùng một transaction. Phần chưa chia/chưa xếp không sinh Session hoặc sự kiện Calendar giả.
4. **Mở rộng Kế hoạch Cuốn chiếu:**
   - Mở rộng dùng cùng Task và các khóa phần/bước/phiên nguồn để tránh tạo trùng.
   - Xong phần đã lập lịch **chưa đồng nghĩa xong toàn Task**.
5. **Chính sách Giữ/Thu hồi DND & Timeout Check-in (UF5):**
   - Khi hết đồng hồ đếm ngược, Run ghi nhận `ended_at` nhưng **giữ nguyên DND (`holding_for_checkin`)** trong cửa sổ hỏi Check-in cuối phiên để bảo vệ sự tập trung.
   - Tắt DND & Focus Mode **ngay lập tức** khi user trả lời nghỉ/hoàn thành/bận/bỏ qua; chuyển giao quyền sở hữu (`dnd_owner_run_id`) nếu user chọn làm tiếp luôn phiên kế tiếp; **HOẶC tự động thu hồi tại mốc `checkin_timeout_at = ended_at + 300 giây`** qua Local Exact Alarm trên thiết bị khi không phản hồi (`no_response`), đảm bảo không bao giờ kẹt DND kể cả khi app bị đóng/kill.
6. **Bảo tồn Dữ liệu Timer & Check-in Muộn:**
   - Không phản hồi giữ kết quả chưa biết; không xóa dữ liệu timer có nguồn.
   - Trả lời muộn sửa cùng CheckIn, không kéo dài actual hoặc mở Run mới.
7. **Nguyên tắc Học Hệ số Dự phòng ($k_{\text{buffer}}$):**
   - Actual dùng học buffer chỉ gồm làm chính của Step hoàn tất cùng phạm vi; loại micro-task, nghỉ và chờ check-in.
   - Một Step nhiều Session vẫn là **một mẫu học duy nhất**.
8. **Độc lập Giờ rảnh và Nguồn Lịch Google:**
   - Giờ rảnh do user xác nhận khác giờ khả dụng tính từ lịch bận.
   - `O21 CalendarContext` phải ghi phạm vi và độ mới nguồn; không xem lỗi Calendar là lịch trống.
9. **Quản lý Liên kết Sự kiện Google (CalendarEventLink):**
   - `CalendarEventLink` có tối đa một liên kết hiện hành cho mỗi Session và lịch đích.
   - Dời cùng phiên giữ nguyên `google_event_id`; đổi lịch đích phải đối soát sự kiện cũ để tránh nhân đôi.
10. **Transaction Nội bộ & Hàng đợi Đồng bộ Lịch (Job Queue):**
    - Lưu lịch và job đồng bộ trong cùng transaction nội bộ, gọi Google sau khi commit thành công.
    - Job cũ không ghi đè bản mới; hủy Session ngăn yêu cầu tạo cũ làm sống lại sự kiện.
11. **Xử lý Tác động Bên ngoài từ Google Calendar:**
    - Sửa/xóa sự kiện của app trực tiếp trên Google phải được đối soát và xem tác động trong UF6; không tự hoàn thành Task, tự duyệt lịch mới hoặc tạo lại sự kiện đã xóa.
12. **Ngắt Kết nối & An toàn Thông tin Tuyệt đối:**
    - Ngắt kết nối giữ sự kiện đã xuất và dừng job theo chính sách; xóa sự kiện là thao tác riêng được user chọn.
    - Token bí mật, file âm thanh thô (`raw audio`) và văn bản nhận dạng (`transcript`) **tuyệt đối không đưa vào log kỹ thuật**.
13. **Undo 5 giây (W3C COGA), Soft Delete & Khóa ngoại 1 chiều (DAG):**
    - Mọi thao tác xóa/bỏ qua trên Mobile phải giữ cục bộ 5 giây với `UndoSnackbar` trước khi gọi API để không sinh sớm `O25 CalendarSyncJob(delete)`; cấm SQL Hard Delete trên các thực thể nghiệp vụ (dùng trạng thái `cancelled` hoặc `discarded`).
    - Khóa ngoại vật lý SQL chỉ đi theo **1 chiều DAG xuôi dòng tạo dữ liệu** (`O14 <- O05 <- O07 <- O22 <- O08 <- O09 <- O24`), các chiều ngược lại truy xuất qua ORM `relationship` (Read-only Derived); toàn bộ danh sách UUID (`List[UUID]`) lưu bằng cột `JSON` để tương thích 100% giữa SQLite và PostgreSQL.
