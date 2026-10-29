# BIÊN BẢN & NỘI DUNG CUỘC HỌP KHỞI ĐỘNG KỸ THUẬT (KICK-OFF & TECHNICAL ALIGNMENT)
**Dự án:** ProPlan VA — Trợ lý ảo AI hỗ trợ lập kế hoạch & nhắc lịch chủ động cho người ADHD-I  
**Thời gian dự kiến:** 90 – 120 phút  
**Chủ trì:** Minh Trung (`mt` - Git Manager & Lead BA) & Quốc Hưng (`vqh` - Backend Lead)  
**Thành phần tham dự (8 thành viên):**  
- Minh Trung (`mt`) — Git Manager, Lead BA & AI Lead (Toàn quyền phụ trách Gemini Model, Prompts, Structured Output)  
- Quốc Hưng (`vqh`) — Backend Lead & Architecture  
- Trang Tín (`tn`), `kc` — Backend & Database Engineers  
- Quang Khải (`kai`) — Voice System Lead (Groq Whisper STT, edge-tts TTS & Audio Pipeline)  
- Quốc Thịnh (`qt`), Doãn Thành (`thành`) — Algorithm & Business Logic Designers  
- Thanh An (`tan`), `kc` — Mobile Lead & Frontend UI (Flutter)  
- Doãn Thành (`thành`) — QA / Test Lead  

---

## MỤC TIÊU CUỘC HỌP
1. **Chốt kế hoạch thần tốc 3 tuần (3-Week Fast-Track MVP Roadmap):** Phân định rạch ròi phạm vi Must-have vs. Post-MVP, phân bổ chi tiết 3 Sprint (21 ngày) cho đội ngũ 8 thành viên để kịp ra mắt bản chạy thực tế (APK).
2. **Thống nhất 7 User Flows (UF1 – UF7):** Phân định ranh giới rạch ròi giữa LLM vs. Thuật toán Python và thiết lập 8 quy tắc bất biến (*Strict Guardrails*).
3. **Thống nhất Schema & Mô hình Dữ liệu (O01 – O21):** Phân định ranh giới Persistent DB vs. In-memory DTO vs. Client-only State, chuẩn hóa đơn vị thời gian `_seconds` và cơ chế khóa Optimistic.
4. **Thiết lập Chuẩn mực Kỹ thuật trước khi Code:** Thống nhất DoR/DoD, quy trình phân nhánh Git & PR Review, chuẩn bảo mật secrets, tiêu chuẩn UI/UX chuẩn W3C COGA và chiến lược kiểm thử.

---

## CHƯƠNG TRÌNH CUỘC HỌP (AGENDA)

```text
+-------------------+---------------------------------------------------------+--------------------+
| Thời lượng        | Nội dung thảo luận trọng tâm                             | Người điều phối    |
+-------------------+---------------------------------------------------------+--------------------+
| 00:00 - 00:25 (25p)| PHẦN 1: Chốt Kế hoạch Thần tốc 3 Tuần ra MVP (21 Ngày)   | mt & vqh           |
| 00:25 - 00:55 (30p)| PHẦN 2: Thống nhất 7 User Flows & Strict Guardrails      | mt & tan & qt      |
| 00:55 - 01:25 (30p)| PHẦN 3: Thống nhất Schema & Kiến trúc Dữ liệu (O01-O21)  | vqh & tn & kc      |
| 01:25 - 01:50 (25p)| PHẦN 4: Chuẩn mực Kỹ thuật & Chiến lược Làm việc Song song| mt & tan & thành   |
| 01:50 - 02:00 (10p)| PHẦN 5: Q&A, Ký duyệt Thống nhất (Sign-off) & Action 48h| Toàn bộ thành viên |
+-------------------+---------------------------------------------------------+--------------------+
```

---

## PHẦN 1: KẾ HOẠCH THẦN TỐC 3 TUẦN ĐẠT MVP (21 NGÀY) (25 PHÚT)

### 1.1. Phân định Phạm vi MVP (Scope Triage: Must-Have vs. Post-MVP)
Để hoàn thành dự án trong 3 tuần mà vẫn giữ trọn vẹn giá trị cốt lõi giải quyết rào cản ADHD, nhóm thống nhất cắt tỉa phạm vi tính năng như sau:

| Luồng / Hạng mục | Trong Phạm vi MVP (Must-Have — Chốt trong 3 Tuần) | Dời sang Bản v1.1 (Post-MVP) |
| :--- | :--- | :--- |
| **UF1 (Nhập việc)** | Nhập Text & Voice (Groq Whisper), trích xuất 4 trường bằng Gemini Flash, gộp 1 vòng hỏi làm rõ, Auto-save Draft. | Tự động phân loại nhãn dự án nâng cao, nhập liệu hàng loạt bằng file. |
| **UF2 (Lập lịch)** | LLM bẻ `Task -> Steps` + Micro-task 2p, Thuật toán Python chia `Sessions` (Rolling Horizon 2 tuần, buffer $k_{\text{buffer}} = 1.2$, tính $\Delta T$), đồng bộ Focus Blocks lên Google Calendar sau khi chốt. | Tự động tối ưu đa mục tiêu phức tạp (Energy level matching), giải quyết xung đột lịch 2 chiều từ Google Calendar ngoài app. |
| **UF3 (Tập trung)** | 1-Step UI trên Flutter (chỉ hiện 1 bước/phiên), nhắc 15-10-5p, Micro-task 2p ngoài phiên, nút 1 chạm bật Focus + Wakelock + DND (`flutter_dnd`). | Chặn ứng dụng bên thứ ba (App Blocker tùy biến theo gói quyền nâng cao). |
| **UF4 (Cất suy nghĩ)** | Push-to-Talk CHỈ thu & lưu file thô `.m4a`/`.wav` vào Hộp nháp, retry 3 lần, fallback ô nhập tay. Không STT/LLM trong phiên. | Phân tích cảm xúc giọng nói, tự động gán độ khẩn cấp cho suy nghĩ nháp. |
| **UF5 (Check-in)** | Voice Check-in 2 chiều (`edge-tts`), xử lý 5 nhánh kết quả, tự động tắt DND sau 5 phút timeout. | Phân tích giọng nói người dùng để đo lường mức độ kiệt sức (Burnout detection). |
| **UF6 (Dời lịch)** | Guilt-free Rescheduling, lưu `pending_approval` khi tắt app, re-validate thời gian thực khi mở lại app trước khi cho duyệt. | Đề xuất hoán đổi lịch giữa nhiều người dùng (Multi-user coordination). |
| **UF7 (Cài đặt)** | Thiết lập thời lượng phiên (20/25/30p), nghỉ (5p), giọng đọc trợ lý cố định tiếng Việt, không can thiệp phiên đang chạy. | Tùy biến giọng đọc AI cá nhân hóa sâu, import lịch mẫu phức tạp. |
| **Hệ số Buffer** | Công thức thống kê đơn giản dựa trên tỷ lệ `actual / planned` của các Step hoàn thành. | Học máy dự đoán hệ số đệm theo loại công việc (ML Model O19). |

### 1.2. Lộ trình Chi tiết 3 Tuần (21 Ngày Làm việc — Feature-First)

```mermaid
gantt
    title Lộ trình Thần tốc 3 Tuần (MVP Fast-Track — Feature-First)
    dateFormat  YYYY-MM-DD
    section Tuần 1 (Lập Kế hoạch)
    Setup Monorepo & CI/CD              :done, w1_1, 2026-10-30, 2d
    DB Schemas 3 Tầng (SQLAlchemy 2.0)  :active, w1_2, 2026-10-30, 4d
    UF1: Prompt Gemini & Groq Whisper STT:active, w1_3, 2026-11-01, 3d
    UF2: Scheduler Engine & Default Proposal:active, w1_4, 2026-11-02, 3d
    Base Flutter W3C COGA UI (UF1 + UF2):active, w1_5, 2026-10-30, 5d
    Milestone 1: Demo Planning UF1 + UF2:milestone, m1, 2026-11-05, 0d
    section Tuần 2 (Thực thi Tập trung)
    UF3: 1-Step UI, Wakelock & DND      : w2_1, 2026-11-06, 4d
    UF4: Push-to-Talk Raw Audio (.m4a)  : w2_2, 2026-11-06, 3d
    Exact Alarm Doze Mode (<= 1s)       : w2_3, 2026-11-07, 3d
    Google Calendar MCP Focus Blocks    : w2_4, 2026-11-08, 3d
    Milestone 2: Demo Focus UF3 + UF4   :milestone, m2, 2026-11-12, 0d
    section Tuần 3 (Thích ứng & Đóng gói)
    UF5: Voice Check-in & DND Timeout 5p: w3_1, 2026-11-13, 3d
    UF6: Reschedule & Real-time Re-check: w3_2, 2026-11-13, 3d
    UF7: Cài đặt hoàn chỉnh & Buffer MA : w3_3, 2026-11-14, 3d
    E2E Testing, Offline & NFR Benchmark: w3_4, 2026-11-15, 4d
    Milestone 3: Đóng gói APK v1.0      :milestone, m3, 2026-11-19, 0d
```

### 1.2. Lộ trình Kỹ thuật 3 Tuần Theo Từng User Flow (Matrix Sprint Plan)

Mô hình kết hợp tối ưu: **Giữ nguyên 3 Team kỹ thuật chuyên môn (BE, Mobile, Tester)** nhưng **đồng bộ mục tiêu từng tuần theo từng User Flow** để đảm bảo xong tuần nào là chạy được E2E tuần đó:

```text
TUẦN 1: Cụm Lập kế hoạch (UF1 Nhập việc & Gộp hỏi + UF2 Phân rã AI & Scheduler Engine) -> Milestone 1
TUẦN 2: Cụm Thực thi tập trung (UF3 1-Step Focus UI + UF4 Cất suy nghĩ Raw Audio) -> Milestone 2
TUẦN 3: Cụm Thích ứng & Cài đặt (UF5 Voice Check-in + UF6 Dời lịch thích ứng + UF7 Cài đặt hoàn chỉnh) -> Milestone 3
```

#### 🔹 TUẦN 1: CHINH PHỤC CỤM UF1 & UF2 (Planning Phase)
* **Backend Team (`vqh`, `tn`, `kc`, `kai`):**
  - *Ngày 1–2:* Chốt SQLAlchemy 2.0 schema, Alembic migration các bảng core (`users`, `user_preferences` có default config, `tasks`, `steps`, `sessions`). Xuất Swagger Docs & Mock JSON cho Mobile.
  - *UF1:* API `POST /api/v1/tasks/parse` kết nối Gemini Flash trích xuất 4 trường bắt buộc, trả về bản tóm tắt + câu hỏi gộp làm rõ. API `POST /api/v1/tasks/draft` auto-save draft khi thoát app.
  - *UF2:* Thuật toán `scheduler_engine.py` chia `Steps -> Sessions` (Rolling Horizon 2–3 tuần), nhân hệ số $k_{\text{buffer}} = 1.2$, tính số phút thiếu hụt $\Delta T_{\text{deficit}} = T_{\text{req}} - T_{\text{free}}$. Tích hợp Google Calendar MCP đọc giờ bận ($T_{\text{busy}}$).
* **Mobile Team (`tan`, `kc`):**
  - *Ngày 1–2:* Dựng kiến trúc Flutter Provider, Dio client, cài bộ Theme chuẩn W3C COGA (Pastel/Warm, Dark Mode dịu nhẹ).
  - *UF1:* Màn hình nhập việc Voice/Text, màn hình Tóm tắt gộp câu hỏi làm rõ trong **1 màn hình duy nhất**, bắt sự kiện thoát app để gọi auto-save draft.
  - *UF2:* Màn hình duyệt kế hoạch: xem tổng quan dự án và chi tiết phiên 2–3 tuần, hiển thị thẻ cảnh báo $\Delta T_{\text{deficit}}$, nút "Tùy chỉnh theo ý tôi", nút bấm Xác nhận chốt lịch.
* **Tester / QA Team (`thành`, `mt`):**
  - Xây dựng Test Matrix cho UF1 & UF2; kiểm thử UF1 (bắt lỗi nếu AI hỏi lắt nhắt > 1 vòng; kiểm tra thoát app có lưu draft không).
  - Kiểm thử thuật toán UF2 (xác nhận hệ thống không tự dời deadline khi thiếu giờ, báo đúng $\Delta T_{\text{deficit}}$, đo độ trễ LLM $\le 2.5\text{s}$).
* **🏁 Nghiệm thu Tuần 1 (Milestone 1 — Core Value Demo):** Nhập việc (Voice/Text) $\rightarrow$ AI làm rõ 1 vòng $\rightarrow$ AI sinh Default Proposal $\rightarrow$ Thuật toán xếp lịch chi tiết 2–3 tuần $\rightarrow$ Xác nhận chốt lịch chính thức.

#### 🔹 TUẦN 2: CHINH PHỤC CỤM UF3 & UF4 (Execution Phase)
* **Backend Team (`vqh`, `qt`, `kai`, `tn`):**
  - *UF3:* API cung cấp phiên hiện tại và phiên kế tiếp cho màn hình 1-Step UI; API kích hoạt phiên và lưu tiến độ. Tự động đồng bộ các khối Focus Sessions lên Google Calendar qua MCP khi lịch được kích hoạt.
  - *UF4:* API `POST /api/v1/drafts/audio` nhận file âm thanh thô `.m4a`/`.wav` lưu vào storage và ghi vào bảng `draft_thoughts` (`status = 'raw_saved'`). **CẤM gọi STT/LLM trong phiên**.
* **Mobile Team (`tan`, `kc`):**
  - *UF3:* Màn hình **1-Step UI** (chỉ hiển thị duy nhất 1 bước/phiên hiện tại, đồng hồ đếm ngược trực quan, Micro-task 2 phút khởi động). Nút 1 chạm kích hoạt đồng thời Focus + Wakelock + DND (`flutter_dnd`).
  - *UF4:* Widget Push-to-Talk "Cất suy nghĩ" bằng `record`: bấm giữ để thu âm, nhả tay lưu file thô `.m4a` vào máy và đẩy lên server; xử lý retry 3 lần $\rightarrow$ fallback ô nhập text tại chỗ.
* **Voice & System (`kai`):**
  - Xây dựng hệ thống báo thức nền chính xác (**Exact Alarm / Foreground Service**) dung sai $\le 1\text{s}$ hoạt động bền bỉ trong **Android Doze Mode**.
  - Cấu hình thông báo đẩy nhắc nhở trước giờ tại các mốc `15p`, `10p`, `5p`.
* **Tester / QA Team (`thành`, `mt`):**
  - Kiểm thử chuông báo thức trong Android Doze Mode và khi thiết bị tắt màn hình (dung sai $\le 1\text{s}$).
  - Kiểm thử UF4 (test ngắt micro, test thu âm lỗi 3 lần, kiểm tra timer UF3 không bị gián đoạn khi ghi âm).
  - Kiểm thử Offline Resilience: Tắt mạng hoàn toàn, đồng hồ và ghi âm UF4 vẫn hoạt động bình thường.
* **🏁 Nghiệm thu Tuần 2 (Milestone 2 — Focus & Doze Protection):** Đếm giờ tập trung 1-Step UI + tự bật DND $\rightarrow$ Cất suy nghĩ raw audio $\rightarrow$ Báo thức Doze Mode reo chính xác $\le 1\text{s}$.

#### 🔹 TUẦN 3: CHINH PHỤC CỤM UF5, UF6, UF7 & ĐÓNG GÓI APK (Adaptation Phase)
* **Backend Team (`vqh`, `tn`, `qt`, `kai`):**
  - *UF5:* Voice Check-in 2 chiều (`edge-tts` + Groq Whisper); API Check-in xử lý 5 nhánh kết quả, State Machine tự động chuyển `no_response` và nhả tắt DND sau đúng 5 phút timeout.
  - *UF6:* API dời lịch thích ứng: tạo phương án mới kèm lời động viên nhẹ nhàng. Logic lưu `ScheduleProposal(pending_approval)` khi user tắt app; hàm re-validate tính khả thi theo thời gian thực khi mở lại app.
  - *UF7:* API lưu thiết lập thời lượng phiên/nghỉ, đảm bảo không can thiệp phiên đang chạy, đẩy thay đổi lịch tương lai qua UF6 kèm cooldown; Background Worker tính hệ số đệm Moving Average 7–10 phiên.
  - *Hardening:* Bọc 3-Tier AI Fallback ($\le 2.5\text{s}$), tối ưu độ trễ API $\le 300\text{ms}$.
* **Mobile Team (`tan`, `kc`):**
  - *UF5:* Modal Voice Check-in 2 chiều cuối phiên: phát giọng nói trợ lý, hiển thị **3 chip rõ ràng khi Bỏ qua** (`[Hoãn làm sau]`, `[Bỏ phiên]`, `[Bỏ bước]`), SnackBar Hoàn tác 5s.
  - *UF6:* Màn hình duyệt dời lịch UF6 kèm lời khích lệ; xử lý kịch bản mở lại app hiển thị phương án chờ khả thi mới.
  - *UF7:* Màn hình Cài đặt hoàn chỉnh (tùy chỉnh phiên/nghỉ, tần suất nhắc, phong cách giọng nói, gợi ý hệ số đệm cuối tuần).
  - *Offline-First & Polish:* Lưu cache SQLite cục bộ cho Timer và xem lịch trong ngày; tối ưu duy trì 60 FPS.
  - *Đóng gói:* Build file `.apk` cài đặt thử nghiệm hoàn chỉnh.
* **Tester / QA Team (`thành`, `mt`):**
  - Kiểm thử timeout 5 phút ở UF5 (để yên máy không chạm, xem đúng 5 phút sau DND có tự tắt và phiên đánh dấu `no_response` không).
  - Kiểm thử E2E toàn bộ 7 luồng UF1–UF7 trên máy thật Android.
  - Đo đạc benchmark toàn bộ tiêu chuẩn NFR.
* **🏁 Nghiệm thu Tuần 3 (Milestone 3 — Production Ready):** Toàn bộ 7 luồng UF1–UF7 chạy mượt mà, không lỗi crash, nghiệm thu bản build APK hoàn chỉnh.

### 1.3. Bảng Phân công & Cam kết Trách nhiệm Nhân sự (3-Week RACI)

| Thành viên | Git Handle | Phân hệ Phụ trách | Trách nhiệm Trọng tâm trong 3 Tuần |
| :--- | :--- | :--- | :--- |
| **Minh Trung** | `mt` | Git Manager, Lead BA & AI Lead | Toàn quyền Gemini Model, Prompts, Structured Outputs, LLM Fallback & PR Review |
| **Quốc Hưng** | `vqh` | Backend Lead & Architecture | Kiến trúc FastAPI, API contracts & Mock data (Ngày 2), State Machine DND/Focus |
| **Trang Tín** | `tn` | Database & Backend | SQLAlchemy 2.0 ORM, Migrations 6 bảng core, CRUD `tasks`, `steps`, `sessions` |
| **`kc`** | `kc` | Database & Frontend Support | CRUD `thoughts`, `planning_drafts`, tích hợp Dio Client & Local SQLite Cache |
| **Quang Khải** | `kai` | Voice System Lead | Toàn bộ Voice pipeline (`edge-tts`, Groq Whisper STT) & Audio streaming buffer |
| **Quốc Thịnh** | `qt` | Algorithm Engine | Thuật toán `scheduler_engine.py` (Rolling Horizon, Buffer cá nhân hóa, $\Delta T$) |
| **Thanh An** | `tan` | Mobile Lead & UI | Kiến trúc Flutter Provider, 1-Step UI UF3, Theme W3C COGA, Wakelock & DND |
| **Doãn Thành** | `thành` | Algorithm & QA Lead | Thuật toán tịnh tiến UF6; Test suite biên (Offline, Doze mode, 3-retry audio) |

---

## PHẦN 2: THỐNG NHẤT 7 USER FLOWS & STRICT GUARDRAILS (35 PHÚT)

### 2.1. Phân định Rạch ròi: LLM vs. Thuật toán Python (Hybrid Architecture)
- **LLM (`Gemini Flash`):** Chỉ xử lý ngôn ngữ & ngữ nghĩa.
  - Trích xuất thực thể từ câu nói/đoạn chat và đặt câu hỏi gộp làm rõ (UF1).
  - Phân tích bẻ nhỏ `Task -> Steps` và sinh gợi ý Micro-task 2 phút (UF2).
  - Phân tích yêu cầu đổi lịch tự nhiên và sinh lời giải thích, động viên tinh thần (UF2, UF6).
- **Thuật toán Python (`scheduler_engine.py`):** Đảm nhận **100%** phép toán thời gian & logic lịch.
  - Chia `Steps -> Sessions`, nhân hệ số đệm cá nhân hóa $k_{\text{buffer}}$.
  - Tính toán kiểm tra va chạm lịch trong quỹ thời gian rảnh trước Deadline:
    $$T_{\text{req}} = \sum_{i=1}^{N} \left( \text{PlannedDuration}_i \times k_{\text{buffer}} + \text{BreakDuration}_i \right)$$
  - Nếu $T_{\text{req}} > T_{\text{free}}$, tính chính xác số phút thiếu hụt $\Delta T = T_{\text{req}} - T_{\text{free}}$ để thông báo cho người dùng; **cấm LLM tự đoán hay tự dời hạn chót**.

### 2.2. Điểm Chốt Bắt buộc Cho Từng User Flow (UF1 – UF7)
- **UF1:** Kiểm tra đủ 4 thông tin bắt buộc (`Nội dung`, `Phạm vi`, `Khung giờ rảnh`, `Deadline`). Gộp toàn bộ câu hỏi thiếu vào **1 màn hình duy nhất**. Tự động lưu nháp (`draft`) khi người dùng thoát app.
- **UF2:** Lập lịch Rolling Horizon (chi tiết 2–3 tuần). Thông báo rõ $\Delta T$ nếu thiếu giờ. Chỉ đồng bộ sự kiện Focus Blocks lên Google Calendar **SAU KHI** người dùng bấm Xác nhận lịch chính thức.
- **UF3:** Nhắc trước 15p, 10p, 5p. Micro-task 2 phút **không tính** vào thời lượng phiên chính. Nút bấm 1 chạm kích hoạt đồng thời: Focus Mode + Wakelock + DND (`flutter_dnd`) trên **Giao diện 1-Step UI** (chỉ hiển thị duy nhất 1 bước/phiên hiện tại).
- **UF4 (Cất suy nghĩ):** Nút Push-to-Talk **CHỈ thu và lưu file âm thanh thô (`.m4a`/`.wav`)** vào Hộp nháp. **CẤM** gọi STT, **CẤM** gọi LLM trong phiên, không làm dừng đồng hồ UF3. Cơ chế retry tối đa 3 lần; nếu lỗi cả 3 lần $\rightarrow$ mở ô nhập text nhanh tại chỗ. Chỉ gọi STT khi người dùng chủ động bấm *"Tạo việc từ ghi âm này"* sau phiên.
- **UF5:** Voice Check-in 2 chiều; tách bạch kết quả Phiên (`Session`) với kết quả Bước (`Step`). Xử lý đủ 5 nhánh kết quả. **Chính sách Tắt DND:** Giữ DND trong lúc check-in; tắt ngay khi xong/nghỉ/bận/bỏ qua, HOẶC **tự động tắt sau đúng 5 phút nếu không phản hồi** (`no_response`).
- **UF6:** Guilt-free Rescheduling. Thoát app **KHÔNG** phải là đồng ý; phải giữ nguyên lịch cũ, lưu lịch mới vào `ScheduleProposal(status='pending_approval')`. Khi mở lại app, chạy thuật toán kiểm tra lại tính khả thi theo thời gian thực trước khi cho người dùng duyệt.
- **UF7:** Mọi thay đổi không can thiệp vào phiên đang chạy. Thay đổi độ dài phiên/nghỉ ảnh hưởng lịch tương lai phải tính bằng Python và đẩy qua UF6 kèm cooldown chống spam.
- **3-Tier AI Fallback:** Tầng 1 (`Gemini Flash`, timeout $\le 2.5\text{s}$) $\rightarrow$ Tầng 2 (`Llama-3` qua Groq Cloud) $\rightarrow$ Tầng 3 (Thuật toán Python chia phiên cố định 20–25p).

---

## PHẦN 3: THỐNG NHẤT SCHEMA & KIẾN TRÚC DỮ LIỆU (O01 – O21) (30 PHÚT)

### 3.1. Phân loại Lưu trữ Dữ liệu (Tránh Over-engineering 21 bảng DB)
1. **Persistent DB Entities (Bảng Database thực tế - SQLAlchemy 2.0):**
   - `users`, `user_preferences` (O01)
   - `tasks` (O07), `steps` (O08), `sessions` (O09)
   - `planning_drafts` (O05), `schedule_versions` (O11), `session_runs` (O12)
   - `check_ins` (O13), `thoughts` (O14)
   - `reschedule_proposals` (O15), `adaptation_suggestions` (O19)
   - `command_receipts` (O18 - Idempotency), `audit_events` (O20)
2. **In-memory DTOs / Pydantic v2 Schemas (Luân chuyển qua API & LLM Output):**
   - `Availability` (O02), `TimeConstraint` (O03), `TaskSummary` (O04)
   - `PlanCandidate` (O06), `FeasibilityReport` (O10), `CalendarContext` (O21)
3. **Client-only State (Quản lý trên Mobile App):**
   - `DeviceState` (O17 - DND status, Wakelock, battery, offline cache)

### 3.2. 5 Quy ước Bất biến về Dữ liệu Toàn Hệ thống
1. **Đơn vị thời gian:** Mọi trường thời lượng kết thúc bằng `_seconds` bắt buộc là số nguyên (giây) (ví dụ: `work_interval_seconds = 1500` cho 25p, `micro_task_seconds = 120` cho 2p).
2. **Khoảng đệm an toàn giữa 2 Phiên:** Hai Session bắt buộc cách nhau tối thiểu **900 giây (15 phút)**. Khoảng đệm này độc lập, không cộng dồn vào `duration_seconds` của Session.
3. **Cơ chế Khóa Đồng thời (Optimistic Locking):** Toàn bộ bảng persistent server có trường `revision` (Integer, default 1) để chống ghi đè dữ liệu cũ khi mất mạng hoặc đồng bộ chậm.
4. **Xử lý giá trị rỗng:** Giá trị chưa có bắt buộc là `null`, **tuyệt đối không gán mặc định bằng 0** để không làm méo mó các phép toán thống kê và học máy.
5. **Dữ liệu Học Hệ số Dự phòng ($k_{\text{buffer}}$):** Chỉ tính trên `work_elapsed_seconds` của các Bước đã **hoàn thành trọn vẹn**; loại bỏ thời gian micro-task, giờ nghỉ giải lao và thời gian chờ check-in.

---

## PHẦN 4: CHUẨN MỰC KỸ THUẬT & ĐIỀU KIỆN TIÊN QUYẾT TRƯỚC KHI CODE (25 PHÚT)

### 4.1. Definition of Ready (DoR) & Definition of Done (DoD)
- **Definition of Ready (DoR - Điều kiện để bắt đầu code 1 tính năng):**
  - Có User Story và tiêu chí chấp nhận (Acceptance Criteria) cụ thể bám sát UF1–UF7.
  - Đã có Pydantic Schema cho Request/Response hoặc AI Structured Output được Review bởi BA (`mt`) và Backend Lead (`vqh`).
  - Wireframe / UI Mockup trên Flutter đã đối chiếu chuẩn W3C COGA (tối đa 3–5 thành phần tương tác chính).
- **Definition of Done (DoD - Điều kiện để merge PR vào nhánh `dev`):**
  - Backend: Viết unit test qua `pytest` (đặc biệt cho `scheduler_engine.py` và AI fallback); kiểm tra API response $\le 300\text{ms}$.
  - Mobile: Vượt qua `dart analyze` không lỗi/cảnh báo; không drop frame dưới 60 FPS; màn hình 1-Step UI hoạt động mượt mà.
  - Tuân thủ 100% Strict Guardrails (không có dialog confirm, không STT ở UF4, không tự hoàn thành ở UF5, không ghi đè lịch ở UF6).
  - Được review và approve bởi ít nhất **2 thành viên** (trong đó có BA/Git Manager `mt` và Lead phân hệ tương ứng).

### 4.2. Chiến lược Phân nhánh Git & Quy tắc Commit
- **Nhánh chính:**
  - `main`: Chỉ chứa code Production ổn định và có release tag.
  - `dev`: Nhánh tích hợp chính của cả nhóm.
- **Nhánh tính năng:** Đặt tên theo chuẩn `feat/<userflow>-<tên-ngắn>`, ví dụ:
  - `feat/uf1-task-entry-prompt`
  - `feat/uf2-scheduler-engine`
  - `feat/uf3-focus-dnd-service`
  - `feat/uf4-raw-audio-draft`
  - `feat/uf5-voice-checkin`
  - `feat/uf6-reschedule-engine`
- **Quy chuẩn Commit:** Tuân thủ chuẩn **Conventional Commits**:
  - `feat(uf1): add single-round task summary and clarification`
  - `feat(uf4): implement push-to-talk raw audio recorder with 3-retry fallback`
  - `fix(uf5): auto disable dnd after 5-minute timeout`
  - `test(scheduler): add unit test for rolling horizon buffer calculation`

### 4.3. Quản lý Môi trường, Biến Môi trường & Bảo mật
- **Bảo mật tuyệt đối:** CẤM commit `.env`, API Keys (`GEMINI_API_KEY`, `GROQ_API_KEY`), file cơ sở dữ liệu `.db` cục bộ hoặc các file ghi âm thử nghiệm lên Git repository.
- File `.env.example` chuẩn mẫu phải có đầy đủ chú thích cho tất cả các key:
  ```env
  PROJECT_NAME="ProPlan VA"
  DATABASE_URL="sqlite+aiosqlite:///./test.db"
  GEMINI_API_KEY="your-gemini-api-key"
  GROQ_API_KEY="your-groq-api-key"
  AI_TIMEOUT_SECONDS=2.5
  JWT_SECRET_KEY="your-secret-key"
  ```

### 4.4. Chuẩn UI/UX cho Người ADHD (Chuẩn W3C COGA)
1. **Mật độ thông tin:** Tối đa **3–5 thành phần tương tác chính** trên 1 màn hình.
2. **Màn hình Focus (UF3):** Tuyệt đối tuân thủ **1-Step UI** (chỉ thấy duy nhất bước/phiên hiện tại, nút "Cất suy nghĩ" và đồng hồ).
3. **Undo thay vì Confirm Dialog:** CẤM dùng `AlertDialog` hỏi *"Bạn có chắc muốn xóa/bỏ qua?"*. Thực hiện ngay và hiện `SnackBar` có nút **Hoàn tác (Undo) trong 5 giây**.
4. **Nhãn nút bấm cụ thể:** Dùng nhãn rõ hành động và thời lượng (VD: `"Bắt đầu đếm giờ 25p"`, `"Cất suy nghĩ"`), CẤM dùng nhãn mơ hồ (`"OK"`, `"Submit"`).
5. **Hiệu ứng chờ thư giãn:** Hiển thị Breathing Circle hoặc Shimmer kèm dòng chữ *"AI đang bẻ nhỏ công việc giúp bạn..."* khi gọi LLM để giảm lo âu.

### 4.5. Chiến lược Kiểm thử & Offline-First
- **Offline First trên Mobile:** Timer, đồng hồ đếm ngược, chuông báo thức, xem lịch trong ngày và nút ghi âm UF4 phải hoạt động bình thường kể cả khi mất mạng.
- **Android Doze Mode:** Sử dụng Exact Alarms để chuông báo hết phiên không bị trễ quá 1 giây khi điện thoại tắt màn hình ngủ sâu.
- **Kiểm thử Edge Cases:** Test kịch bản rớt mạng giữa phiên, ghi âm UF4 lỗi liên tiếp 3 lần, tắt app đột ngột ở UF1 và UF6, không phản hồi check-in quá 5 phút ở UF5.

---

## PHẦN 5: BẢNG KÝ DUYỆT THỐNG NHẤT (SIGN-OFF) & ACTION ITEMS

### Bảng Ký Duyệt Thống nhất Kỹ thuật (Sign-off)

| Phân hệ / Hạng mục | Đại diện Phụ trách | Trạng thái Đồng thuận | Ghi chú cam kết |
| :--- | :--- | :---: | :--- |
| **Nghiệp vụ, Git & Gemini AI** | Minh Trung (`mt`) | [X] ĐỒNG Ý | Toàn quyền Gemini Model, Prompts, Structured Outputs, Giám sát Guardrails |
| **Backend & Architecture** | Quốc Hưng (`vqh`) | [X] ĐỒNG Ý | Hoàn thành API contracts, State Machine DND/Focus |
| **Database & ORM** | Trang Tín (`tn`), `kc` | [X] ĐỒNG Ý | Hoàn thành migration SQLAlchemy 2.0 theo spec O01–O21 |
| **Voice & Audio Pipeline** | Quang Khải (`kai`) | [X] ĐỒNG Ý | Voice pipeline kép (Groq Whisper STT + edge-tts) & Audio buffer |
| **Algorithm Engine** | Quốc Thịnh (`qt`), Doãn Thành (`thành`) | [X] ĐỒNG Ý | Xây dựng `scheduler_engine.py` chuẩn công thức $\Delta T$ |
| **Mobile App (Flutter)** | Thanh An (`tan`), `kc` | [X] ĐỒNG Ý | Đảm bảo chuẩn W3C COGA, 1-Step UI, 60 FPS |
| **QA & Testing** | Doãn Thành (`thành`) | [X] ĐỒNG Ý | Xây dựng test suite tự động cho 5 edge cases cốt lõi |

### Kế hoạch Hành động Ngay sau Cuộc họp (Action Items trong 48 Giờ)
1. **Minh Trung (`mt`):** Merge tài liệu này vào `docs/` của nhánh `dev`, cấu hình Branch Protection Rules trên GitHub; khởi tạo cấu hình Gemini 2.5 Flash SDK và Structured Outputs trong `ai_service.py`.
2. **Quốc Hưng (`vqh`) & Trang Tín (`tn`):** Khởi tạo schema SQLAlchemy 2.0 và đẩy file migration Alembic đầu tiên cho 6 bảng core.
3. **Thanh An (`tan`):** Dựng base project Flutter, cài đặt `provider`, `dio`, thiết lập Palette màu chuẩn W3C COGA (Pastel/Dark dịu nhẹ).
4. **Quang Khải (`kai`):** Dựng khung dịch vụ Voice pipeline (`voice_service.py` với Groq Whisper và `edge-tts`).
5. **Quốc Thịnh (`qt`):** Xây dựng hàm tính toán thời gian `scheduler_engine.py` (công thức tính thiếu hụt $\Delta T$).
