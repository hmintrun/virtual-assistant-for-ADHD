# KẾ HOẠCH BÀN GIAO SPRINT 3 (TUẦN 3) — GIAI ĐOẠN THÍCH ỨNG, TÁI LẬP LỊCH & CÀI ĐẶT
## (ADAPTATION PHASE: UF5 + UF6 + UF7, GOOGLE CALENDAR 2 CHIỀU & POSTGRESQL STAGING)

**Dự án:** ProPlan VA — Trợ lý ảo AI hỗ trợ lập kế hoạch, chia nhỏ công việc và nhắc lịch chủ động bằng giọng nói cho người trưởng thành mắc hội chứng ADHD-I.  
**Thời gian Sprint 3:** 10/11/2026 – 16/11/2026 (Giờ Việt Nam — UTC+7).  
**Tài liệu tham chiếu chuẩn:** [Project.md](../../Project.md), [AGENTS.md](../../AGENTS.md), [schema.md](schema.md), [UF3_UF4_DEEP_DIVE_ANALYSIS.md](UF3_UF4_DEEP_DIVE_ANALYSIS.md), [THE_04_BACKEND_TASK_STATUS.md](THE_04_BACKEND_TASK_STATUS.md).  
**Nhánh làm việc chính:** `backend` (commit nền: `0c51a59`) $\rightarrow$ tích hợp vào nhánh chung `dev`.

---

## MỤC LỤC CHI TIẾT
1. [Mục tiêu Sprint 3 & Tổng quan Kiến trúc Toàn Hệ thống](#1-mục-tiêu-sprint-3)
2. [User Flow 5 (UF5) — Kiểm tra Tiến độ, Kết thúc Phiên & Giải phóng DND](#2-user-flow-5-uf5)
3. [User Flow 6 (UF6) — Thích ứng Lịch Linh hoạt Không Phán xét (Adaptive Rescheduling)](#3-user-flow-6-uf6)
4. [User Flow 7 (UF7) — Tùy chỉnh Trợ lý & Tiến hóa Hệ số Đệm (Buffer Evolution)](#4-user-flow-7-uf7)
5. [Lộ trình Tích hợp Google Calendar 2 Chiều & Auth JWT (Mốc C01 – C04)](#5-lộ-trình-google-calendar-auth)
6. [Kế hoạch Dọn dẹp Nợ Kỹ thuật & Kiểm chứng PostgreSQL Staging](#6-nợ-kỹ-thuật-postgresql-staging)
7. [Bảng Phân công Nhân sự (8 Thành viên), Definition of Done (DoD) & Ma trận Rủi ro](#7-phân-công-dod-rủi-ro)

---

<a id="1-mục-tiêu-sprint-3"></a>
## 1. MỤC TIÊU SPRINT 3 & TỔNG QUAN KIẾN TRÚC TOÀN HỆ THỐNG

### 1.1. Bối cảnh & Vị trí trong Lộ trình 3 Tuần
Theo chiến lược phân kỳ **Feature-First (Planning $\rightarrow$ Execution $\rightarrow$ Adaptation)**:
- **Tuần 1 (Planning Phase — UF1 + UF2):** [HOÀN THÀNH 100%] Nhập việc Voice/Text $\rightarrow$ Trích xuất 4 trường gộp 1 vòng $\rightarrow$ Default Proposal $\rightarrow$ Thuật toán Rolling Horizon 14 ngày $\rightarrow$ Chốt lịch chính thức vào Database.
- **Tuần 2 (Execution Phase — UF3 + UF4):** [HOÀN THÀNH 100% BE, MOBILE GHÉP UI] Nhắc trước phiên 15/10/5p $\rightarrow$ Micro-task 2 phút $\rightarrow$ 1-Tap Start (Focus + DND + 1-Step UI) $\rightarrow$ Visual Timer $\rightarrow$ Nút Cất suy nghĩ Raw Audio UF4 (offline, retry 3 lần $\rightarrow$ text fallback).
- **Tuần 3 (Adaptation Phase — UF5 + UF6 + UF7):** **[TRỌNG TÂM SPRINT NÀY]**
  - **UF5:** Voice Check-in 2 chiều cuối phiên; xử lý 5 nhánh kết quả; tương tác 3 chip Bỏ qua kèm SnackBar Undo 5s; tự động tắt DND sau 5 phút nếu không phản hồi (`no_response`).
  - **UF6:** Tái lập lịch thích ứng không phán xét (*Guilt-free Adaptive Rescheduling*); "Đóng app không phải là đồng ý" (lưu `pending_approval`); "Tịnh tiến giờ thực tế khi mở lại app"; cô lập `allowed_task_ids`; tính toán $\Delta T_{\text{deficit}}$.
  - **UF7:** Cài đặt cá nhân hóa; phiên đang chạy giữ độc lập; thuật toán **A20 Median Step** kẹp $[1.0, 2.0]$ triệt tiêu ngoại lai ADHD; worker định kỳ tối Chủ nhật sinh gợi ý khi lệch $\ge 0.20$.
  - **Hạ tầng Đồng bộ & Bảo mật:** Google Sign-In $\rightarrow$ JWT thực tế (thay thế stub `resolve_dev_current_user`); Google Calendar 2 chiều (Mốc C01–C04) qua secondary calendar "ProPlan VA Focus"; Outbox Pattern và ETag 412.
  - **Độ bền Dữ liệu:** Dọn dẹp triệt để 100% RAM cache trong `draft_service.py`; chạy toàn bộ 222+ tests và 34 bước Console E2E trên **PostgreSQL 16 Staging**.

### 1.2. Sơ đồ Kiến trúc Toàn trình Sprint 3 (UF5 $\rightarrow$ UF6 $\rightarrow$ UF7 $\rightarrow$ Calendar)

```mermaid
flowchart TD
    subgraph UF3_UF5["UF3 -> UF5: Kết Thúc Phiên & Voice Check-In"]
        TimerEnd["Visual Timer Hết Giờ (00:00)<br>CẤM TỰ COMPLETE"] --> CheckInPrompt["edge-tts phát câu hỏi Check-In<br>+ Giữ DND tạm thời"]
        CheckInPrompt --> BranchChoice{"Phản hồi của User"}
        BranchChoice -->|"1. Hoàn thành phiên"| S_Comp["Session completed<br>Step in_progress hoặc completed"]
        BranchChoice -->|"2. Chưa xong"| S_Incomp["Ghi nhận actual duration<br>Gợi ý thêm phiên / buffer"]
        BranchChoice -->|"3. Bận đột xuất"| S_Busy["Session busy<br>Bảo toàn tiến độ hiện tại"]
        BranchChoice -->|"4. Bỏ qua (Skip)"| S_Skip["Hiển thị 3 Chip Lựa Chọn<br>+ SnackBar Undo 5 giây"]
        BranchChoice -->|"5. Không phản hồi (Timeout 300s)"| S_NoResp["Lazy Timeout: Tự giải phóng DND<br>Session no_response, outcome unknown"]
    end

    subgraph UF6_Engine["UF6: Guilt-free Adaptive Rescheduling"]
        S_Incomp --> TriggerUF6["Kích hoạt Reschedule"]
        S_Busy --> TriggerUF6
        S_Skip --> TriggerUF6
        TriggerUF6 --> LLM_Empathy["Gemini Flash: Sinh lời giải thích<br>ấm áp, động viên (Guilt-free)"]
        TriggerUF6 --> Py_Shift["Python Engine: shift_elapsed_sessions<br>Tịnh tiến sang khe rảnh tương lai<br>Chỉ sửa allowed_task_ids"]
        Py_Shift --> CheckDeficit{"Đủ giờ trước Deadline?"}
        CheckDeficit -->|"Đủ giờ (Feasible)"| ProposalPending["Lưu Proposal (pending_approval)<br>ĐÓNG APP KHÔNG PHẢI LÀ ĐỒNG Ý"]
        CheckDeficit -->|"Thiếu giờ (Deficit)"| CalcDeficit["Báo chính xác Delta T deficit<br>Gợi ý nới Deadline / mở thêm giờ"]
        ProposalPending -->|"User bấm Duyệt"| CommitSchedule["Official Schedule cập nhật<br>Đẩy Outbox Sync Job C04"]
        ProposalPending -->|"Tắt app mở lại"| ReAnchor["Tịnh tiến lại theo real_time_anchor<br>tại thời điểm mở app"]
    end

    subgraph UF7_Analytics["UF7: Buffer Evolution & Settings"]
        S_Comp --> HistoryStore["Ghi nhận actual_min / planned_min<br>vào Lịch sử Phiên"]
        HistoryStore --> WeekendWorker["adaptation_worker.py<br>Chạy tối Chủ nhật hàng tuần"]
        WeekendWorker --> A20_Median["Thuật toán A20 Median Step<br>Lấy trung vị 15 step gần nhất<br>Kẹp [1.0, 2.0], làm tròn 0.05"]
        A20_Median --> CheckDelta{"Lệch |k_user - k_hist| >= 0.20?"}
        CheckDelta -->|"Có"| SuggestionRow["Tạo AdaptationSuggestion(pending)<br>Gợi ý nhẹ nhàng trên UI"]
        CheckDelta -->|"Không"| KeepCurrent["Giữ nguyên hệ số đệm"]
    end

    subgraph Cal_Sync["Hạ tầng Google Calendar & Auth"]
        CommitSchedule --> OutboxJob["calendar_sync_jobs (status='pending')"]
        OutboxJob --> SyncWorker["Background Outbox Worker<br>Ghi Focus Sessions lên Secondary Calendar"]
        SyncWorker --> ETagCheck["ETag / If-Match Header<br>Chống Lost Update (HTTP 412)"]
    end

    style TimerEnd fill:#fce8e6,stroke:#c5221f,stroke-width:2px
    style ProposalPending fill:#fff2cc,stroke:#f29900,stroke-width:2px
    style CommitSchedule fill:#e6f4ea,stroke:#137333,stroke-width:2px
    style S_NoResp fill:#f1f3f4,stroke:#5f6368,stroke-width:2px
```

---

<a id="2-user-flow-5-uf5"></a>
## 2. USER FLOW 5 (UF5) — KIỂM TRA TIẾN ĐỘ, KẾT THÚC PHIÊN & GIẢI PHÓNG DND

### 2.1. Quy tắc Bất biến Cốt lõi (Strict Guardrails)
1. **Tuyệt đối KHÔNG tự động complete:** Khi đồng hồ Visual Timer đếm ngược về `00:00`, Backend và Mobile **CẤM** tự ý cập nhật trạng thái `completed` cho Session, Step hoặc Task. Trạng thái hoàn thành bắt buộc phải do người dùng chủ động xác nhận qua Voice hoặc chạm màn hình.
2. **Tách bạch 3 cấp độ (Session vs. Step vs. Task):**
   - Hoàn thành một `Session` **chưa chắc** hoàn thành `Step`.
   - Nếu Step gồm 3 Session mà mới chạy xong Session 1 $\rightarrow$ `Session 1: completed`, nhưng `Step: in_progress`.
   - Chỉ khi hoàn thành Session cuối cùng của Step, Voice Check-in mới hỏi về `expected_output`. Nếu người dùng xác nhận đạt kết quả $\rightarrow$ `Step: completed`.
   - Khi toàn bộ Steps của Task đạt `completed` $\rightarrow$ `Task: completed`.
3. **Cơ chế Lazy Timeout 300s & Chính sách Giải phóng DND:**
   - Khi timer hết giờ, thiết bị **giữ nguyên DND** trong lúc phát câu hỏi Check-in để bảo vệ người dùng không bị giật mình bởi thông báo bên ngoài.
   - Nếu người dùng xác nhận nghỉ/kết thúc/báo bận/bỏ qua $\rightarrow$ Tắt DND và Focus Mode ngay lập tức.
   - Nếu người dùng chọn "Làm tiếp phiên kế tiếp" $\rightarrow$ Giữ nguyên DND.
   - **Trường hợp không phản hồi (No Response):** Sau đúng **300 giây (5 phút)** kể từ mốc `checkin_timeout_at`, nếu không nhận được tương tác:
     * Thiết bị Android native receiver tự động tắt DND và hủy Wakelock.
     * Backend cập nhật trạng thái `SessionRun`: `run_state = 'completed'`, `response_state = 'no_respond'`, `outcome = 'unknown'`, `ended_at = start_time + planned_duration`. Không phán xét, không trừ chuỗi streak.

### 2.2. Đặc tả 5 Nhánh Rẽ Trạng Thái Cuối Phiên

| STT | Nhánh Nghiệp Vụ | Trạng Thái `Session` | Trạng Thái `SessionRun` | Hành Động Tiếp Theo & Ràng Buộc |
|:---:|:---|:---|:---|:---|
| **1** | **Hoàn thành phiên** (`completed`) | `completed` | `completed`, `response_state='responded'`, `outcome='completed'` | - Nếu phiên giữa bước: Giữ Step `in_progress`, hiển thị đếm ngược nghỉ giải lao (`rest_duration`).<br>- Nếu phiên cuối bước: Hỏi kiểm tra `expected_output`. Nếu xong $\rightarrow$ Step `completed`. |
| **2** | **Chưa xong** (`incomplete`) | `completed` (đã chạy hết giờ) | `completed`, `response_state='responded'`, `outcome='incomplete'` | Ghi nhận `actual_duration_min`. Hỏi người dùng cần thêm bao nhiêu phút hoặc đề xuất phiên bổ sung $\rightarrow$ Đẩy sang **UF6** để xếp lịch tiếp nối. |
| **3** | **Việc đột xuất** (`busy`) | `interrupted` | `cancelled`, `response_state='responded'`, `outcome='busy'` | Cắt ngang phiên ngay tại thời điểm báo, bảo toàn số phút đã làm thực tế (`actual_duration_min = elapsed`). Tắt DND ngay lập tức $\rightarrow$ Đẩy phần việc còn lại sang **UF6**. |
| **4** | **Bỏ qua phiên** (`skipped`) | `skipped` | `cancelled`, `response_state='responded'`, `outcome='skipped'` | Hiển thị **3 chip lựa chọn rõ ràng**: `[Hoãn phiên làm sau]`, `[Bỏ qua phiên này]`, `[Bỏ toàn bộ bước này]`. Kèm SnackBar Hoàn tác trong 5 giây (W3C COGA). |
| **5** | **Không phản hồi** (`no_response`) | `completed` (thời lượng kế hoạch) | `completed`, `response_state='no_respond'`, `outcome='unknown'` | Quá hạn 300s: Tự giải phóng DND/Wakelock; ghi nhận `ended_at = scheduled_end_at`; chuyển về màn hình chính với thẻ ngữ cảnh nhẹ nhàng. |

### 2.3. Chi tiết 3 Chip Lựa Chọn khi Bỏ qua (Skip Interaction — W3C COGA)
Khi người dùng bấm "Bỏ qua" hoặc nói "Bỏ qua phiên này", màn hình hiển thị 3 lựa chọn không phán xét:
1. `[Hoãn phiên này làm sau]`: Tịnh tiến phiên hiện tại sang khung giờ rảnh kế tiếp trong tương lai. Giữ nguyên toàn bộ nội dung bước và các phiên còn lại.
2. `[Bỏ qua phiên này]`: Đánh dấu phiên hiện tại là `skipped`. Dồn khối lượng công việc còn lại vào phiên kế tiếp của bước đó, hoặc giảm nhẹ khối lượng nếu sắp đến Deadline.
3. `[Bỏ toàn bộ bước này]`: Đánh dấu toàn bộ Step hiện tại là `skipped`, hủy các Session chưa chạy của Step này, và chuyển trọng tâm sang Step tiếp theo trong Task.
- **Quy tắc Hoàn tác (Undo):** Tuyệt đối **CẤM** hộp thoại Confirm *"Bạn có chắc chắn muốn bỏ qua?"*. Áp dụng ngay hành động và hiển thị một thanh `SnackBar` màu dịu mắt với thông báo: *"Đã ghi nhận lựa chọn của bạn"* kèm nút **`[Hoàn tác (5s)]`**. Nếu người dùng bấm Hoàn tác trong 5 giây, gọi API `POST /undo-skip` để khôi phục trạng thái ban đầu.

### 2.4. Pipeline Giọng nói Kép trong UF5
- **TTS (Voice Prompt):** Backend sử dụng `edge-tts` (giọng đọc tiếng Việt tự nhiên `vi-VN-HoaiMyNeural` hoặc `vi-VN-NamMinhNeural`) sinh file audio base64 gửi kèm DTO: *"Bạn đã hoàn thành phiên làm việc. Hãy cho tôi biết kết quả nhé!"*. Thiết bị phát âm thanh đồng thời hiển thị text trên màn hình. Nếu thiết bị mất mạng, fallback về `flutter_tts` offline.
- **STT (Voice Input):** Người dùng trả lời tự nhiên qua giọng nói $\rightarrow$ Groq Whisper (`whisper-large-v3-turbo`) phân tích ý định (Intent Classification) thành 1 trong 5 nhánh:
  * "Xong rồi", "Hoàn thành", "Xong bước này rồi" $\rightarrow$ `completed`.
  * "Chưa xong", "Cần thêm thời gian", "Vẫn còn dang dở" $\rightarrow$ `incomplete`.
  * "Có việc gấp", "Bận đột xuất", "Phải đi ra ngoài" $\rightarrow$ `busy`.
  * "Bỏ qua", "Không làm nữa", "Để sau" $\rightarrow$ Mở 3 chip `skipped`.
  * Fallback: Chạm tay trực tiếp vào 1 trong các nút trên màn hình nếu không muốn nói.

### 2.5. Danh mục API Endpoints & Schemas UF5

```python
# backend/app/schemas/execution.py

class CheckInResponseRequest(BaseModel):
    outcome: Literal["completed", "incomplete", "busy", "skipped"]
    actual_work_minutes: int = Field(ge=0, le=720)
    user_note: str | None = None
    step_completed: bool | None = None  # Áp dụng cho phiên cuối của Step
    skip_choice: Literal["postpone_session", "skip_session", "skip_step"] | None = None

class CheckInPromptDTO(BaseModel):
    session_id: UUID
    prompt_text: str
    audio_base64: str | None = None
    is_last_session_of_step: bool
    expected_output: str | None
    options: list[str]

class CheckInResultDTO(BaseModel):
    session_id: UUID
    run_state: str
    response_state: str
    outcome: str
    dnd_released: bool
    next_action: Literal["start_rest", "trigger_reschedule", "show_dashboard", "start_next_session"]
    proposal_id: UUID | None = None  # Nếu cần dời lịch qua UF6
```

- **Endpoints triển khai trong `backend/app/api/v1/sessions.py`:**
  - `GET /api/v1/sessions/{id}/checkin-prompt`: Lấy câu hỏi check-in và audio TTS (dòng 29 Thẻ 04).
  - `POST /api/v1/sessions/{id}/checkin`: Gửi phản hồi check-in của người dùng (dòng 20, 30 Thẻ 04).
  - `POST /api/v1/sessions/{id}/skip`: Gửi lựa chọn skip từ 1 trong 3 chip (dòng 31 Thẻ 04).
  - `POST /api/v1/sessions/{id}/undo-skip`: Khôi phục phiên trong vòng 5 giây (dòng 32 Thẻ 04).
  - `POST /api/v1/sessions/{id}/timeout-check`: Worker / Client trigger lazy check quá hạn 300s để giải phóng DND.

---

<a id="3-user-flow-6-uf6"></a>
## 3. USER FLOW 6 (UF6) — THÍCH ỨNG LỊCH LINH HOẠT KHÔNG PHÁN XÉT (ADAPTIVE RESCHEDULING)

### 3.1. Bốn Nguyên Tắc Cốt Lõi Bất Biến (Strict Guardrails)
1. **"Đóng app KHÔNG phải là đồng ý":**
   Khi hệ sinh ra phương án dời lịch mới, trạng thái đề xuất được lưu vào Database là `RescheduleProposal(status='pending_approval')`. **Lịch cũ đã chốt vẫn giữ nguyên 100% hiệu lực**. Nếu người dùng vuốt tắt app, hết pin hoặc không bấm Duyệt, hệ thống **tuyệt đối không** tự động ghi đè lịch mới.
2. **"Tịnh tiến giờ thực tế khi mở lại app":**
   Giả sử lúc 10:00 hệ thống đề xuất phiên mới vào lúc 10:30. Người dùng đóng app đến 11:15 mới mở lại. Khi client gọi `GET /api/v1/proposals/current`, Backend lấy mốc thời gian thực hiện tại (`real_time_anchor = 11:15`), tự động gọi hàm Python `shift_elapsed_sessions` để đẩy các ô lịch đã trôi qua sang các khe rảnh tương lai (từ 11:15 trở đi), rồi mới hiển thị lên màn hình. Đảm bảo người dùng không bao giờ thấy một phương án đề xuất bắt đầu trong quá khứ!
3. **"Cô lập `allowed_task_ids` — Cấm xáo trộn Task khác":**
   Thuật toán dời lịch chỉ được phép thao tác trên các Session thuộc danh sách `allowed_task_ids` (Task đang bị trễ hoặc cần dời). **Tuyệt đối cấm tự ý dịch chuyển, cắt ngắn hoặc đảo lộn các Task khác đã được người dùng chốt lịch**. Các Task khác được đối xử như "vật cản cố định" trong không gian thời gian.
4. **"Công thức kiểm tra khả thi & Tính toán $\Delta T_{\text{deficit}}$":**
   Trước khi xếp lịch, thuật toán tính tổng thời gian cần thiết:
   $$T_{\text{req}} = \sum_{i=1}^{M} \left( \text{PlannedDuration}_i \times k_{\text{buffer}} + \text{BreakDuration}_i \right)$$
   Đối chiếu với quỹ giờ rảnh khả dụng trước Deadline ($T_{\text{free}}$):
   - Nếu $T_{\text{req}} \le T_{\text{free}}$: Đề xuất phương án khả thi (`is_feasible = True`).
   - Nếu $T_{\text{req}} > T_{\text{free}}$: Tính chính xác số phút thiếu hụt:
     $$\Delta T_{\text{deficit}} = T_{\text{req}} - T_{\text{free}}$$
     Hệ thống hiển thị cảnh báo minh bạch: *"Kế hoạch này đang thiếu đúng [X] phút trước hạn chót. Bạn muốn nới thêm hạn chót hay mở thêm giờ rảnh?"*, kèm 2 nút hành động trực tiếp.

### 3.2. Ranh giới Kiến trúc: LLM vs. Thuật toán Python
- **LLM (`Gemini Flash` / Fallback `Groq Qwen`):** Đảm nhiệm khía cạnh tâm lý và giao tiếp ngôn ngữ tự nhiên:
  * Sinh lời giải thích ấm áp, đồng cảm, hoàn toàn không mang tính phán xét (*"Không sao cả, chuyện bận đột xuất là rất bình thường. Tôi đã tính toán lại để bạn vẫn kịp hạn chót mà không bị quá tải!"*).
  * Phân tích câu lệnh điều chỉnh bằng ngôn ngữ tự nhiên từ người dùng (ví dụ: *"Dời các phiên còn lại sang sáng mai sau 9h nhé"* $\rightarrow$ trích xuất `date_filter='tomorrow'`, `after_time='09:00'`).
- **Thuật toán Python (`rescheduling.py` & `scheduler_engine.py`):** Đảm nhiệm **100%** các phép toán số học:
  * Tính toán khe rảnh, trừ sự kiện bận từ Google Calendar ($T_{\text{free}} = T_{\text{user}} - T_{\text{busy}}$).
  * Nhân hệ số $k_{\text{buffer}}$, chia nhỏ Session, tịnh tiến ô lịch, và kiểm tra giao cắt không gian thời gian.

### 3.3. Danh mục API Endpoints & Schemas UF6

```python
# backend/app/schemas/rescheduling.py

class RescheduleRequest(BaseModel):
    trigger_type: Literal["incomplete_session", "busy_interrupt", "skip_session", "manual_request", "settings_change"]
    task_id: UUID
    failed_session_id: UUID | None = None
    remaining_minutes: int | None = None
    user_note: str | None = None

class RescheduleProposalDTO(BaseModel):
    proposal_id: UUID
    task_id: UUID
    status: Literal["pending_approval", "approved", "rejected", "superseded"]
    is_feasible: bool
    deficit_minutes: int = 0
    empathy_message: str
    original_sessions: list[SessionDTO]
    proposed_sessions: list[SessionDTO]
    deadline_iso: datetime
    created_at: datetime
    expires_at: datetime

class NaturalLanguageAdjustRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=500)
    current_proposal_id: UUID
```

- **Endpoints triển khai trong `backend/app/api/v1/proposals.py`:**
  - `POST /api/v1/proposals/reschedule`: Khởi tạo yêu cầu tính toán phương án dời lịch mới (dòng 33 Thẻ 04).
  - `GET /api/v1/proposals/current`: Lấy đề xuất hiện tại kèm auto-shift thời gian thực tế nếu mốc giờ đã trôi qua (dòng 34 Thẻ 04).
  - `POST /api/v1/proposals/{id}/approve`: Duyệt phương án $\rightarrow$ Commit đè lên Official Schedule $\rightarrow$ Kích hoạt Outbox Sync C04 lên Google Calendar (dòng 35 Thẻ 04).
  - `POST /api/v1/proposals/{id}/reject`: Từ chối đề xuất $\rightarrow$ Khôi phục lịch cũ $\rightarrow$ Đóng màn hình dời lịch (dòng 36 Thẻ 04).
  - `POST /api/v1/proposals/adjust-natural-language`: Điều chỉnh đề xuất bằng ngôn ngữ tự nhiên (dòng 37 Thẻ 04).

---

<a id="4-user-flow-7-uf7"></a>
## 4. USER FLOW 7 (UF7) — TÙY CHỈNH TRỢ LÝ & TIẾN HÓA HỆ SỐ ĐỆM (BUFFER EVOLUTION)

### 4.1. Quy tắc Thiết kế Bất biến Cốt lõi
1. **Phiên đang chạy giữ snapshot độc lập:**
   Khi người dùng vào màn hình Cài đặt và thay đổi thời lượng phiên tập trung (ví dụ từ 25p sang 45p), hoặc đổi thời gian nghỉ, thay đổi này **tuyệt đối không bao giờ** can thiệp hay cắt ngang phiên làm việc đang chạy (`SessionRun`). Phiên hiện tại tiếp tục đếm giờ theo cấu hình lúc khởi động. Cài đặt mới chỉ có hiệu lực từ phiên tiếp theo.
2. **Đổi cấu hình làm lệch lịch tương lai $\rightarrow$ Đẩy qua UF6 duyệt:**
   Nếu việc thay đổi thời lượng phiên hoặc tăng hệ số $k_{\text{buffer}}$ làm tổng thời gian của các Task chưa chạy trong tương lai bị phình to và xung đột lịch, hệ thống tự động chạy thuật toán tính lại và chuyển kết quả sang **UF6** ở dạng `RescheduleProposal(status='pending_approval')` để người dùng duyệt. Áp dụng cơ chế Cooldown chống làm phiền: Chỉ nhắc tối đa 1 lần/giờ đối với thay đổi cài đặt.

### 4.2. Thuật toán Tiến Hóa Hệ Số Đệm (A20 Median Step Adaptation)

#### 4.2.1. Tại sao dùng Median (Trung vị) thay vì Mean (Trung bình cộng) cho ADHD?
Người mắc hội chứng ADHD thường có 2 thái cực hành vi trái ngược sâu sắc:
- **Trạng thái Hyperfocus (Siêu tập trung):** Làm việc với tốc độ thần tốc, xong bước việc 60 phút chỉ trong 20 phút ($k = 0.33$).
- **Trạng thái Executive Dysfunction / Distraction (Tê liệt khởi động / Phân tâm):** Mất tập trung hoặc kẹt ý tưởng, một bước việc 25 phút kéo dài tới 100 phút ($k = 4.0$).
Nếu sử dụng công thức trung bình cộng thông thường ($\frac{1}{N}\sum k$), chỉ cần **1 phiên bị phân tâm nặng** sẽ kéo vọt hệ số $k_{\text{buffer}}$ lên quá cao, phá vỡ toàn bộ lịch trình các ngày sau. Do đó, thuật toán **A20 Median Step** sử dụng hàm **Trung vị (Median)** trên tập $N = 15$ bước việc gần nhất (tối thiểu 5 mẫu sạch). Trung vị loại bỏ 100% ảnh hưởng của các giá trị ngoại lai (outliers), phản ánh đúng nhất năng lực thực tế ổn định của người dùng.

#### 4.2.2. Công thức Toán học KaTeX
Với mỗi bước việc $i$ đã hoàn thành:
$$k_{\text{step}, i} = \frac{\text{ActualDuration}_i}{\text{PlannedDuration}_i}$$
Tập hợp $M$ bước việc gần nhất (với $5 \le M \le 15$ mẫu hợp lệ, loại trừ các bước bị hủy hoặc gián đoạn giữa chừng):
$$k_{\text{median}} = \text{Median}\left(\{ k_{\text{step}, 1}, k_{\text{step}, 2}, \dots, k_{\text{step}, M} \}\right)$$
Kẹp giá trị trong khoảng an toàn và làm tròn bước nhảy 0.05:
$$k_{\text{suggested}} = \text{round}_{0.05}\left( \max\left(1.0, \min\left(2.0, k_{\text{median}}\right)\right) \right)$$

#### 4.2.3. Quy trình Chạy Định Kỳ của `adaptation_worker.py`
- **Tần suất thực thi:** Chạy định kỳ vào tối Chủ nhật hàng tuần (20:00 UTC+7).
- **Điều kiện kích hoạt gợi ý:**
  $$\Delta k = |k_{\text{user}} - k_{\text{suggested}}|$$
  - Nếu $\Delta k \ge 0.20$ (lệch trên 20% so với cấu hình người dùng đang đặt):
    Hệ thống tạo một bản ghi `AdaptationSuggestion(status='pending')` trong cơ sở dữ liệu.
  - Khi người dùng mở app vào sáng Thứ Hai, hiển thị một thẻ gợi ý tinh tế (Soft Banner):
    *"Dựa trên 10 phiên hoàn thành tuần qua, bạn thường cần thêm khoảng 15% thời gian để làm việc thật thoải mái. Bạn có muốn đổi hệ số đệm từ 1.2 lên 1.35 không?"* kèm 2 nút: `[Đồng ý áp dụng]` và `[Giữ nguyên]`.
  - Quyền quyết định cuối cùng 100% thuộc về người dùng ($k_{\text{user}}$). Tuyệt đối không tự ý ghi đè mà chưa được bấm Đồng ý.

### 4.3. Danh mục API Endpoints & Schemas UF7

```python
# backend/app/schemas/settings.py

class UserPreferenceUpdateDTO(BaseModel):
    default_work_minutes: int | None = Field(None, ge=10, le=120)
    default_break_minutes: int | None = Field(None, ge=3, le=30)
    buffer_multiplier: float | None = Field(None, ge=1.0, le=2.0)
    voice_character: Literal["warm_empathy", "concise_coach", "gentle_reminder"] | None = None
    voice_speed: float | None = Field(None, ge=0.8, le=1.5)
    reminder_offsets: list[int] | None = None  # [900, 600, 300] (15p, 10p, 5p)

class AdaptationSuggestionDTO(BaseModel):
    suggestion_id: UUID
    current_buffer: float
    suggested_buffer: float
    sample_size: int
    rationale: str
    status: Literal["pending", "applied", "dismissed"]
    created_at: datetime
```

- **Endpoints triển khai trong `backend/app/api/v1/settings.py`:**
  - `GET /api/v1/settings/preferences`: Lấy cấu hình tùy chỉnh hiện tại của người dùng (dòng 41 Thẻ 04).
  - `PATCH /api/v1/settings/preferences`: Cập nhật cấu hình người dùng (dòng 42 Thẻ 04).
  - `GET /api/v1/settings/adaptation-suggestions`: Lấy danh sách gợi ý điều chỉnh hệ số đệm đang chờ duyệt (dòng 43 Thẻ 04).
  - `POST /api/v1/settings/adaptation-suggestions/{id}/apply`: Người dùng đồng ý áp dụng hệ số đệm mới (dòng 44 Thẻ 04).

---

<a id="5-lộ-trình-google-calendar-auth"></a>
## 5. LỘ TRÌNH TÍCH HỢP GOOGLE CALENDAR 2 CHIỀU & AUTH JWT (MỐC C01 – C04)

### 5.1. Bốn Mốc Tích Hợp Kỹ Thuật (Milestones C01 – C04)

```mermaid
flowchart LR
    C01["Mốc C01:<br>Google Sign-In &<br>Auth JWT Bridge"] --> C02["Mốc C02:<br>OAuth Scope &<br>Secondary Calendar<br>'ProPlan VA Focus'"]
    C02 --> C03["Mốc C03:<br>FreeBusy API &<br>Own-Event Protection<br>T_free = T_user - T_busy"]
    C03 --> C04["Mốc C04:<br>Outbox Sync Worker &<br>ETag 412 Conflict<br>Resolution"]
```

#### Mốc C01: App Login & Auth Bridge (Google Sign-In $\rightarrow$ JWT Thật)
- **Vấn đề giải quyết:** Xóa bỏ hoàn toàn hàm stub `resolve_dev_current_user` trong `app/core/security.py` vốn đang hardcode user `dev_user`.
- **Luồng triển khai:**
  1. Mobile sử dụng thư viện `google_sign_in` trên Flutter, lấy về `id_token` (Google OIDC token).
  2. Mobile gửi `POST /api/v1/auth/google` kèm `{ id_token: "..." }`.
  3. Backend dùng `google-auth` verify chữ ký số của token với Google Client ID:
     - Nếu user chưa có trong bảng `users`: Tự động tạo bản ghi mới (`email`, `full_name`, `avatar_url`, `created_at`).
     - Nếu đã có: Cập nhật `last_login_at`.
  4. Backend phát hành cặp JWT tiêu chuẩn: `access_token` (hạn 60 phút) và `refresh_token` (hạn 30 ngày).
  5. Mọi API router tiếp nhận Header `Authorization: Bearer <token>`, giải mã lấy `user_id: UUID` thật từ Database.

#### Mốc C02: Calendar Authorization & Secondary Calendar Provisioning
- **Phạm vi quyền tối thiểu (Least Privilege Scope):**
  - **CẤM** xin scope toàn quyền `https://www.googleapis.com/auth/calendar` (dễ bị Google OAuth Verification từ chối và gây lo ngại bảo mật cho người dùng).
  - **CHỈ xin scope tối thiểu:**
    * `https://www.googleapis.com/auth/calendar.events.freebusy`: Đọc khoảng thời gian bận/rỗi.
    * `https://www.googleapis.com/auth/calendar.app.created`: Chỉ có toàn quyền trên các lịch và sự kiện do chính ứng dụng ProPlan VA tạo ra.
- **Quản lý Khóa & Token An Toàn:**
  - Refresh Token của Google Calendar được mã hóa đối xứng **AES-256 Fernet** trước khi lưu vào cột `credential_ref` trong bảng `calendar_connections`. Khóa mã hóa `CALENDAR_FERNET_KEY` lưu tại biến môi trường `.env`, tuyệt đối không commit lên Git.
- **Khởi tạo Secondary Calendar riêng biệt:**
  - Lần đầu kết nối, Backend gọi Google Calendar API tạo một lịch phụ mang tên: `"ProPlan VA Focus"`.
  - Toàn bộ các phiên Focus Session sẽ được ghi vào lịch phụ này. **Tuyệt đối không bao giờ ghi đè hoặc chèn lẫn lộn vào Lịch chính (Primary Calendar) cá nhân của người dùng**.

#### Mốc C03: 2-Way FreeBusy & Bảo Vệ Sự Kiện Của Ứng Dụng (Own-Event Protection)
- **Đọc sự kiện bận:** Khi chạy UF1 / UF2 để tìm khung giờ rảnh, Backend gọi API `freeBusy.query` của Google Calendar trong khoảng thời gian từ ngày hiện tại đến Deadline.
- **Thuật toán FreeBusy Subtraction:**
  $$T_{\text{free}} = T_{\text{user\_availability}} \setminus T_{\text{google\_busy}}$$
- **Quy tắc Own-Event Protection:**
  Khi người dùng đã lên lịch trước đó qua ProPlan VA, các sự kiện này đã nằm trên lịch Google. Khi tính toán lại lịch mới, thuật toán Python lọc bỏ các sự kiện có `extendedProperties.private.source == "proplan_va"` khỏi tập $T_{\text{google\_busy}}$ để không tự trừ đi các phiên làm việc của chính mình!

#### Mốc C04: Outbox Sync Worker Độc Lập & Cơ Chế Chống Xung Đột ETag 412
- **Kiến trúc Bền vững (Transactional Outbox Pattern):**
  - Khi người dùng bấm xác nhận lịch chính thức (UF2) hoặc duyệt dời lịch (UF6), Backend ghi dữ liệu Session vào bảng `sessions` và đồng thời chèn một bản ghi sự kiện vào bảng `calendar_sync_jobs` với `status = 'pending'` trong **cùng 1 Database Transaction**.
  - Nếu Google API bị mất mạng, sập nguồn hoặc timeout, Transaction của app vẫn toàn vẹn 100%.
  - `CalendarSyncWorker` chạy ngầm định kỳ quét bảng `calendar_sync_jobs`:
    * Lấy danh sách job `pending` $\rightarrow$ Đổi sang `processing`.
    * Đẩy sự kiện lên Google Calendar:
      - Tạo mới: `POST /calendars/{calendarId}/events` $\rightarrow$ Lưu `event_id` và `etag` vào bảng `calendar_event_links`.
      - Cập nhật: `PATCH /calendars/{calendarId}/events/{eventId}` kèm Header `If-Match: "<etag>"`.
      - Hủy: `DELETE /calendars/{calendarId}/events/{eventId}`.
    * Đánh dấu job `status = 'synced'`.
- **Xử lý Xung Đột ETag (HTTP 412 Precondition Failed):**
  - Nếu người dùng chỉnh sửa sự kiện trực tiếp trên giao diện web của Google Calendar, mã ETag trên server Google sẽ thay đổi.
  - Khi worker gửi PATCH với ETag cũ, Google trả về lỗi `HTTP 412 Precondition Failed`.
  - Worker tự động bắt lỗi 412, gọi `GET` để kéo phiên bản mới nhất trên Google về, hợp nhất thay đổi (`merge conflict`), cập nhật lại ETag mới nhất vào DB và thử lại (Retry).
- **Phục hồi Lỗi & Exponential Backoff:**
  Nếu gặp lỗi `HTTP 429 Rate Limit` hoặc `HTTP 503 Service Unavailable`, worker áp dụng thuật toán lùi số mũ:
  $$\text{WaitTime} = 2^{\text{retry\_count}} \times 1\text{s} + \text{jitter}$$
  Thử lại tối đa 5 lần trước khi chuyển `status = 'failed'` và ghi nhận log cảnh báo.

---

<a id="6-nợ-kỹ-thuật-postgresql-staging"></a>
## 6. KẾ HOẠCH DỌN DẸP NỢ KỸ THUẬT & KIỂM CHỨNG POSTGRESQL STAGING

### 6.1. Dọn Dẹp Triệt Để Nợ Kỹ Thuật RAM Cache trong `draft_service.py`
- **Hiện trạng tồn đọng:** 7 biến in-memory RAM dicts (`_FAKE_DRAFTS_BY_OWNER`, `_CLIENT_DRAFT_INDEX`, `_CREATE_IDEMPOTENCY_STORE`, `_PATCH_IDEMPOTENCY_STORE`, `_CONFIRM_SUMMARY_IDEMPOTENCY_STORE`, `_ANALYZE_IDEMPOTENCY_STORE`, `_CURRENT_AVAILABILITY_REVISIONS`) gây rủi ro rò rỉ bộ nhớ và không thể chạy multi-worker.
- **Kế hoạch hành động Ngày 1 của Sprint 3:**
  1. Xóa bỏ hoàn toàn 7 biến dicts khỏi `draft_service.py`.
  2. Bắt buộc tham số `db: AsyncSession` trong toàn bộ các hàm của `draft_service.py` (loại bỏ giá trị mặc định `= None`).
  3. Chuyển toàn bộ cơ chế lưu vết Idempotency sang sử dụng decorator `@durable_command` và bảng `command_receipts` trong `app/models/system.py`.
  4. Mọi thao tác truy vấn Draft chuyển 100% sang model SQLAlchemy `PlanningDraftModel`.
  5. Cập nhật các file test cũ đang gọi monkeypatching RAM dict sang sử dụng session fixture SQLite in-memory, kích hoạt lại test case `test_gap_in_memory_idempotency_dict_vulnerability`.

### 6.2. Alembic Migration Chuẩn Hóa Schema (`20261108_01_cleanup_legacy.py`)
- Drop hoàn toàn bảng cũ `planning_parts` sau khi toàn bộ code C01 đã chuyển sang cấu trúc 3 tầng chuẩn `tasks -> steps -> sessions`.
- Bổ sung composite index hiệu năng cao:
  * `CREATE INDEX ix_planning_drafts_user_created ON planning_drafts(user_id, created_at DESC);`
  * `CREATE INDEX ix_session_runs_dnd_release ON session_runs(dnd_release_at) WHERE dnd_release_at IS NOT NULL;`
  * `CREATE INDEX ix_reminders_fire_state ON reminders(fire_at, delivery_state);`

### 6.3. Kế Hoạch Kiểm Chứng Staging Trên Docker PostgreSQL 16
Hệ thống sẽ chạy kiểm chứng trên container Docker PostgreSQL 16 Alpine độc lập để chứng minh tính tương thích 100% giữa môi trường SQLite Dev và PostgreSQL Production:
1. **Thiết lập môi trường Staging:**
   - Khởi động service `postgres-staging` qua Docker Compose (Port 5433, tmpfs RAM disk).
   - Thiết lập chuỗi kết nối: `DATABASE_URL=postgresql+asyncpg://proactive_staging:staging_secure_pass_2026@localhost:5433/proactive_db`.
2. **Kiểm chứng Migration DDL:**
   - Chạy lệnh `alembic upgrade head` trên PostgreSQL thật.
   - Xác minh toàn bộ các kiểu dữ liệu đặc thù: `sa.Uuid()` dịch thành `uuid`, `UTCDateTime()` dịch thành `TIMESTAMP WITH TIME ZONE`, các bảng có circular FK (`use_alter=True`) sinh đúng câu lệnh `ALTER TABLE ADD CONSTRAINT`.
3. **Chạy toàn bộ 222 Test Items:**
   - Thực thi `pytest` kết nối PostgreSQL Staging.
   - Mục tiêu: **216+ PASSED** (giải quyết 3 ca xfailed về API endpoints khi hoàn thành UF3/UF4/UF5 router).
4. **Kiểm chứng 34 Bước Console E2E Toàn Trình:**
   - Chạy kịch bản giả lập chuỗi hành vi người dùng thật từ UF1 $\rightarrow$ UF2 $\rightarrow$ UF3 $\rightarrow$ UF4 $\rightarrow$ UF5 trên PostgreSQL.
   - Xác minh tính toàn vẹn dữ liệu: Isolation level, transaction rollback khi có lỗi, ràng buộc chống chạy 2 phiên đồng thời `uq_user_live_run`, và cascade delete an toàn.

---

<a id="7-phân-công-dod-rủi-ro"></a>
## 7. BẢNG PHÂN CÔNG NHÂN SỰ (8 THÀNH VIÊN), DEFINITION OF DONE (DoD) & MA TRẬN RỦI RO

### 7.1. Bảng Phân Công Công Việc Chi Tiết 8 Thành Viên

Mỗi hạng mục công việc có **đúng 1 người chịu trách nhiệm chính (Assignee)** theo dõi từ đầu đến cuối và 1-2 người hỗ trợ kỹ thuật:

| Thành Viên & Vai Trò | Phân Hệ Phụ Trách Chính | Nội Dung Công Việc Sprint 3 Cần Thực Hiện | Đầu Ra Bàn Giao Bắt Buộc (Deliverables) |
|:---|:---|:---|:---|
| **Minh Trung (`mt`)**<br>*BA, Prompt & Git Coordinator* | **Điều phối Toàn trình & Product Guardrails** | - Rà soát toàn bộ prompt cho Gemini Flash ở UF5/UF6 (lời động viên ấm áp, không phán xét).<br>- Giám sát quy tắc W3C COGA trên toàn bộ màn hình mới (UF5, UF6, UF7).<br>- Quản lý merge code vào nhánh chung `dev`, bảo đảm không conflict lịch sử commit. | - Bộ System Prompt chuẩn tiếng Việt cho UF5/UF6.<br>- Báo cáo rà soát giao diện đạt chuẩn W3C COGA.<br>- Nhánh `dev` sạch sẽ, tích hợp đầy đủ mã nguồn Sprint 3. |
| **Quốc Hưng (`vqh`)**<br>*Backend Lead & Algorithm Designer* | **UF6 Rescheduling, UF7 A20 Engine & Calendar Core** | - Xây dựng thuật toán Python dời lịch thích ứng (`rescheduling.py`): shift ô lịch, cô lập `allowed_task_ids`, tính $\Delta T_{\text{deficit}}$.<br>- Xây dựng thuật toán A20 Median Step kẹp $[1.0, 2.0]$ và worker `adaptation_worker.py`.<br>- Thuật toán FreeBusy Subtraction & Own-Event Protection cho Google Calendar (Mốc C03). | - Service `rescheduling.py` và `adaptation_worker.py` hoàn chỉnh.<br>- Logic FreeBusy Subtraction chính xác $\le 1\text{s}$.<br>- Unit tests thuật toán đạt độ bao phủ $\ge 90\%$. |
| **Kim Cương (`kc`)**<br>*Backend Core & API Router Specialist* | **API Routers UF5, UF6, UF7 & Google Auth Bridge** | - Xây dựng API router UF5 (`/sessions/{id}/checkin`, `/skip`, `/undo-skip`).<br>- Xây dựng API router UF6 (`/proposals/reschedule`, `/current`, `/approve`, `/reject`).<br>- Xây dựng API router UF7 (`/settings/preferences`, `/adaptation-suggestions`).<br>- Triển khai Auth Bridge: `POST /api/v1/auth/google` $\rightarrow$ JWT Token (Mốc C01). | - Toàn bộ 12 API Endpoints mới được đăng ký trên FastAPI.<br>- OpenAPI / Swagger docs chuẩn Pydantic v2.<br>- Test HTTP integration pass 100% với các mã lỗi HTTP chuẩn. |
| **Trang Tín (`tn`)**<br>*Database & Storage Engineer* | **Nợ Kỹ Thuật, Alembic Migrations & Outbox Worker** | - Dọn sạch 100% RAM cache trong `draft_service.py`, chuyển sang `PlanningDraftModel` và `command_receipts`.<br>- Viết migration `20261108_01` drop `planning_parts` và bổ sung performance indexes.<br>- Xây dựng `CalendarSyncWorker` chạy ngầm cho Outbox Pattern (Mốc C04).<br>- Dựng container Docker PostgreSQL 16 Staging và chạy kịch bản kiểm thử DB. | - `draft_service.py` không còn bất kỳ biến in-memory RAM nào.<br>- Migration Alembic chạy mượt trên cả SQLite và PostgreSQL.<br>- PostgreSQL Staging chạy thành công 216+ tests. |
| **Thanh An (`tan`)**<br>*Mobile UI/UX & Flutter Lead* | **Mobile UI UF5, UF6 & UF7 Settings** | - Dựng màn hình Voice Check-in UF5 (hiển thị text + phát âm, 3 chip Skip, SnackBar Undo 5s).<br>- Dựng màn hình So Sánh Đề Xuất Dời Lịch UF6 (thẻ trực quan: Lịch Cũ vs. Lịch Mới, thông báo thiếu giờ $\Delta T$).<br>- Dựng màn hình Cài Đặt UF7 (thanh trượt thời lượng, chọn giọng đọc, banner gợi ý hệ số đệm). | - Các màn hình Flutter mới mượt mà, đạt 60 FPS, tối đa 3–5 tương tác.<br>- SnackBar Undo 5 giây hoạt động chuẩn xác.<br>- Build thành công bản APK nghiệm thu Sprint 3. |
| **Quang Khải (`kai`)**<br>*Mobile Core, Audio & Native Lead* | **Native DND Management, Google SDK & Offline Voice** | - Xây dựng cơ chế native tự động tắt DND sau 300s lazy timeout kể cả khi app tắt màn hình.<br>- Tích hợp Google Sign-In SDK trên Flutter, lấy `id_token` gửi về Backend.<br>- Xây dựng pipeline phát âm audio base64 từ Backend và fallback `flutter_tts` khi offline. | - Module giải phóng DND chính xác sau 300s trên Android thật.<br>- Luồng Google Sign-In trên thiết bị Android mượt mà 1-tap.<br>- Audio check-in phát ổn định, không giật lag. |
| **Quốc Thịnh (`qt`)**<br>*Time Synchronization & Local Cache* | **Local State Sync & Offline Reschedule Cache** | - Xây dựng cơ chế cache `RescheduleProposal` cục bộ bằng SQLite/SharedPreferences trên Mobile.<br>- Thuật toán local auto-shift giờ đề xuất khi mở app lúc mất mạng.<br>- Đảm bảo đồng bộ thời gian thực giữa đồng hồ Native Android, Flutter Dart và Server UTC. | - Lịch đề xuất tự động tịnh tiến khi mở app kể cả khi Offline.<br>- Không bị lệch múi giờ UTC+7 giữa Mobile và Backend.<br>- Hàng đợi Outbox đồng bộ cục bộ không bị mất dữ liệu. |
| **Doãn Thành (`thành`)**<br>*QA Lead & Automated Test Engineer* | **Test Automation, Staging Verification & E2E** | - Viết kịch bản test tự động E2E cho chuỗi UF5 $\rightarrow$ UF6 $\rightarrow$ UF7.<br>- Kiểm thử độ bền 3 chip Skip và kịch bản bấm Undo trong 5 giây.<br>- Thực thi 34 bước Console E2E trên cơ sở dữ liệu PostgreSQL 16 Staging.<br>- Báo cáo ma trận thiết bị Android (Samsung, Xiaomi, Pixel) đo đạc DND và Doze Mode. | - Bộ test tự động E2E mới bổ sung vào repository.<br>- Báo cáo kiểm thử PostgreSQL Staging có log và bằng chứng chi tiết.<br>- Video demo toàn trình UF1 $\rightarrow$ UF7 trên thiết bị thật. |

### 7.2. Tiêu Chí Nghiệm Thu Hoàn Thành (Definition of Done — DoD)
Một tính năng chỉ được xem là hoàn thành và cho phép merge vào `dev` khi thỏa mãn đầy đủ các điều kiện:
1. **Mã nguồn chuẩn mực:** Tuân thủ 100% SQLAlchemy 2.0 (`Mapped[]`, `select()`), Pydantic v2, không còn biến in-memory RAM cache; code Flutter vượt qua `dart analyze` không có warning.
2. **Bảo vệ Nghiệp vụ (Guardrails):** Không tự động complete session khi hết giờ; đóng app không tự ý dời lịch; không tự dời Task khác ngoài `allowed_task_ids`; tự động tắt DND sau 300s timeout.
3. **Độ bao phủ Kiểm thử:** Tất cả các endpoint mới đều có Unit Test và Integration Test. Toàn bộ test suite backend $\ge 216$ tests pass trên cả SQLite và PostgreSQL Staging.
4. **Bảo mật & Phân quyền:** Google Sign-In trả về JWT hợp lệ; Token Google Calendar được mã hóa AES-256 Fernet; chỉ xin scope tối thiểu `calendar.app.created`.
5. **Trải nghiệm Người dùng (W3C COGA):** Giao diện tiếng Việt dịu mắt, tối đa 3–5 thành phần tương tác; có SnackBar Undo 5 giây cho mọi thao tác xóa/bỏ qua; không có popup giật mình.
6. **Tài liệu & Commit:** Commit message tuân thủ chuẩn Conventional Commits; cập nhật đầy đủ OpenAPI schema và hướng dẫn chạy kiểm thử.

### 7.3. Ma Trận Đánh Giá & Giảm Thiểu Rủi Ro (Risk Mitigation Matrix)

| Rủi Ro Kỹ Thuật | Mức Độ | Biện Pháp Phòng Ngừa & Giảm Thiểu Cụ Thể |
|:---|:---:|:---|
| **Google OAuth Refresh Token hết hạn sau 7 ngày khi ở chế độ Testing** | **CAO** | - Chuyển trạng thái Google Cloud Project sang *"In Production"* nội bộ (Internal) hoặc add toàn bộ email thành viên vào danh sách Test Users được cấp quyền vĩnh viễn.<br>- Backend tự bắt mã lỗi HTTP 401 khi refresh token, phát cờ `requires_reauth` để hiển thị nút kết nối lại nhẹ nhàng trên màn hình Cài đặt. |
| **Xung đột phiên bản khi chỉnh sửa lịch trên Google Calendar Web (ETag 412)** | **TRUNG BÌNH** | Áp dụng Header `If-Match` với ETag; khi nhận mã 412 Precondition Failed, worker tự động gọi `GET` để kéo phiên bản mới nhất về, merge thay đổi và cập nhật lại ETag mới nhất. |
| **Người dùng mở lại app sau nhiều giờ/ngày khiến lịch đề xuất bị trôi vào quá khứ** | **CAO** | Áp dụng nguyên tắc *"Tịnh tiến theo thời gian thực"*: Endpoint `GET /api/v1/proposals/current` luôn nhận `real_time_anchor` hiện tại, tự động đẩy các phiên trôi qua sang khe rảnh tương lai trước khi trả dữ liệu về Mobile. |
| **Lỗi rò rỉ bộ nhớ hoặc dữ liệu không nhất quán do RAM Cache cũ** | **CAO** | Triệt để dọn sạch 7 biến in-memory dicts trong `draft_service.py` ngay Ngày 1; bắt buộc `db: AsyncSession` và chuyển 100% sang `command_receipts`. |
| **Lệch múi giờ UTC+7 giữa Mobile Android và PostgreSQL Staging** | **TRUNG BÌNH** | Toàn bộ các mốc thời gian lưu trong DB bắt buộc dùng kiểu `UTCDateTime` chuyển đổi về UTC. Khi gửi lên API hoặc trả về Mobile, serialize theo định dạng chuẩn ISO-8601 có timezone offset `+07:00`. |
| **User ADHD bị choáng ngợp khi thấy quá nhiều lựa chọn dời lịch** | **THẤP** | Thuật toán Python chỉ sinh ra **DUY NHẤT 1 phương án tối ưu nhất** kèm giải thích ngắn gọn từ AI; chỉ cung cấp thêm nút *"Tùy chỉnh theo ý tôi"* nếu người dùng thực sự muốn thay đổi. |

---

## 8. KẾT LUẬN & HƯỚNG DẪN BẮT ĐẦU SPRINT 3

Tài liệu bàn giao này đã được **5 Subagents chuyên môn** rà soát sâu sắc trên toàn bộ mã nguồn, cấu trúc dữ liệu, sơ đồ ERD và luồng tương tác thực tế. Mọi thành viên trong đội ngũ ProPlan VA bắt đầu Sprint 3 theo thứ tự công việc ưu tiên:
1. **Ngày 1 (08/11/2026):** Quốc Hưng (`vqh`) và Trang Tín (`tn`) dọn sạch nợ kỹ thuật RAM dict trong `draft_service.py`; Kim Cương (`kc`) và Quang Khải (`kai`) hoàn thiện Google Sign-In $\rightarrow$ JWT (Mốc C01).
2. **Ngày 2–3 (09–10/11/2026):** Triển khai toàn bộ Router & Service UF5 (Voice Check-in, 3 chip Skip, Lazy Timeout 300s); dựng container Docker PostgreSQL Staging và chạy kiểm chứng 216+ tests.
3. **Ngày 4–5 (11–12/11/2026):** Triển khai UF6 (Adaptive Rescheduling, tịnh tiến giờ thực tế, cô lập task) và UF7 (Settings, A20 Median Step Worker).
4. **Ngày 6–7 (13–14/11/2026):** Hoàn thiện Outbox Worker Google Calendar (Mốc C04); QA nghiệm thu E2E toàn trình trên APK Android thật; đóng gói sản phẩm hoàn thiện.
