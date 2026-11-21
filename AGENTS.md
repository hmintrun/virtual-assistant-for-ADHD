# AGENTS.md — Quy tắc Bắt buộc & Ngữ cảnh Cốt lõi cho AI Agent (ProPlan VA)

Tài liệu này được nạp tự động vào mọi phiên làm việc của AI Agent trong repository **ProPlan VA** (Trợ lý ảo AI hỗ trợ lập kế hoạch, chia nhỏ công việc và nhắc lịch chủ động bằng giọng nói cho người trưởng thành mắc hội chứng ADHD-I).

---

## 0. Cá nhân hóa theo Hồ sơ Lập trình viên Cục bộ (Local Developer Profile)

1. **Kiểm tra File Hồ sơ Cục bộ:**
   - Khi bắt đầu phiên làm việc, Agent **PHẢI** kiểm tra xem có file [DEVELOPER.md](./DEVELOPER.md) tại thư mục gốc hay không.
   - File này được tạo từ [DEVELOPER.example.md](./DEVELOPER.example.md) trên từng máy tính của thành viên và nằm trong `.gitignore` để không ảnh hưởng đến người khác.
2. **Thích ứng Phong cách & Trọng tâm Chuyên môn:**
   - Nếu tồn tại [DEVELOPER.md](./DEVELOPER.md), Agent tự động nạp thông tin thành viên (Tên, Git Handle, Vai trò chính, Phân hệ phụ trách và Kỳ vọng).
   - Agent **phải chủ động cá nhân hóa** câu trả lời, góc nhìn phản biện, đề xuất kỹ thuật và phong cách code tương ứng với chuyên môn của thành viên đó (ví dụ: tư duy bảo mật & async cho Backend Lead, tư duy W3C COGA & 60 FPS cho Mobile Lead, hoặc bẫy edge case cho QA/Tester).
   - **Lưu ý:** Việc cá nhân hóa phong cách trao đổi **KHÔNG ĐƯỢC PHÉP** làm thay đổi hay vi phạm các quy tắc bất biến trong [Project.md](./Project.md) và cơ chế Strict Guardrail bên dưới.

---

## 1. Nguồn Tham chiếu Chuẩn & Cơ chế "Strict Guardrail"

1. **Single Source of Truth:**
   - File [Project.md](./Project.md) tại thư mục gốc chứa toàn bộ đặc tả chi tiết (Cơ sở khoa học, Tech Stack, NFR, Sơ đồ ERD, Flowchart Mermaid UF1–UF7, Ma trận chuyển trạng thái và Phân công nhân sự).
   - **Trước khi thiết kế hoặc viết code** cho bất kỳ phân hệ nào (`backend/`, `mobile/`, `docs/`), Agent **PHẢI** đối chiếu với mục tương ứng trong [Project.md](./Project.md).
2. **Chế độ Bảo vệ Thiết kế (Strict Guardrail — BẮT BUỘC):**
   - Nếu bất kỳ prompt nào của thành viên trong nhóm yêu cầu viết code **vi phạm các quy tắc bất biến** dưới đây hoặc trong [Project.md](./Project.md) (ví dụ: *đòi thêm hộp thoại Confirm hỏi xác nhận xóa, gọi STT/LLM ngay khi bấm Cất suy nghĩ ở UF4, tự động đánh dấu hoàn thành phiên khi hết giờ ở UF5, ghi đè lịch cũ khi user tắt app ở UF6, tự ý xáo trộn Task khác ở UF2, hoặc nhồi nhét quá 5 phần tử tương tác lên màn hình*), Agent **PHẢI DỪNG LẠI**, **cảnh báo rõ điểm vi phạm**, **trích dẫn quy tắc tương ứng trong `Project.md`** và **đề xuất giải pháp đúng chuẩn** trước khi viết code.

---

## 2. Quy tắc Bất biến về Mô hình Dữ liệu & 7 Luồng Nghiệp vụ (UF1 – UF7)

### 2.1. Mô hình Phân cấp 3 Tầng
- Cấu trúc dữ liệu bắt buộc: `1 Công việc (Task)` $\rightarrow$ `Nhiều Bước (Steps)` $\rightarrow$ `Mỗi Bước gồm Nhiều Phiên (Sessions)`.
- **Quy tắc bất biến:** Kết thúc đồng hồ đếm ngược của 1 `Session` **KHÔNG** tự động đánh dấu hoàn thành `Session`, `Step` hay `Task`. Mọi trạng thái hoàn thành phải đi qua bước xác nhận ở **UF5**.

### 2.2. Luật Cốt lõi cho Từng User Flow (UF1 – UF7)
- **UF1 (Tương tác & Xác nhận việc):**
  - Kiểm tra đủ 4 thông tin bắt buộc: `Nội dung`, `Phạm vi (Scope)`, `Khung giờ rảnh`, `Deadline`. Hỗ trợ tự động đọc lịch bận qua **Google Calendar MCP / API** để suy ra khung giờ rảnh, giảm thiểu việc bắt người dùng phải nhớ và gõ tay.
  - **Gộp 1 vòng duy nhất:** Hiển thị bản tóm tắt kèm toàn bộ câu hỏi làm rõ thông tin thiếu/mâu thuẫn trong **1 lần hiển thị duy nhất** (không hỏi lắt nhắt nhiều vòng, không tự đoán dữ liệu).
  - Tự động lưu nháp (`status = 'draft'`) nếu người dùng thoát app hoặc chưa muốn bổ sung ngay. Chỉ chuyển sang UF2 khi đủ thông tin và người dùng xác nhận 1 lần.
- **UF2 (Bẻ nhỏ việc & Chốt kế hoạch):**
  - **Trải nghiệm phân rã chủ động:** Để tránh hội chứng **Tê liệt khởi động (ADHD Paralysis / Blank Canvas)**, AI (Gemini Flash) **chủ động sinh sẵn một phương án chia bước chuẩn (Default Decomposition Proposal)** gồm danh sách bước, thời lượng dự kiến và Micro-task khởi động 2 phút, kèm nút `"Tùy chỉnh theo ý tôi"` để người dùng nhanh chóng điều chỉnh nếu muốn.
  - **Áp dụng Rolling Horizon:** Giữ tổng quan cả dự án nhưng chỉ xếp lịch chi tiết các phiên cho **2–3 tuần gần nhất**.
  - **Công thức kiểm tra khả thi trước Deadline:**
    $$T_{\text{req}} = \sum_{i=1}^{N} \left( \text{PlannedDuration}_i \times k_{\text{buffer}} + \text{BreakDuration}_i \right)$$
    Nếu $T_{\text{req}} > T_{\text{free}}$ (quỹ giờ rảnh khả dụng trước Deadline):
    $$\Delta T_{\text{deficit}} = T_{\text{req}} - T_{\text{free}}$$
    - **Phạm vi dời lịch:** **Tuyệt đối không tự ý dời hoặc xáo trộn các Task khác đã chốt**. Chỉ xếp vào các khung giờ rảnh trống ($T_{\text{free}}$). Nếu thiếu thời gian, báo rõ con số $\Delta T_{\text{deficit}}$ để người dùng nới deadline hoặc mở thêm giờ rảnh.
  - Hỗ trợ chỉnh sửa lịch trực tiếp trên UI hoặc qua yêu cầu ngôn ngữ tự nhiên trước khi bấm Xác nhận. Tự động đồng bộ các phiên Focus Sessions lên **Google Calendar qua MCP** sau khi người dùng bấm Xác nhận.
- **UF3 (Thông báo & Bắt đầu phiên tập trung):**
  - Nhắc trước phiên tại các mốc `15p`, `10p`, `5p` và gợi ý **Micro-task khởi động 2 phút** (không tính vào thời lượng phiên chính).
  - **Gộp thao tác 1 chạm:** Bấm nút `"Bắt đầu đếm giờ [X]p"` $\rightarrow$ kích hoạt đồng thời **Focus Mode** (`wakelock_plus`), bật **DND** (`flutter_dnd`) và chạy đồng hồ trên **Giao diện 1-Step UI** (chỉ hiện duy nhất bước/phiên hiện tại).
- **UF4 (Cất suy nghĩ bằng Raw Audio — Nhánh phụ trong UF3):**
  - Khi người dùng bấm giữ nút Push-to-Talk `"Cất suy nghĩ"`, **CHỈ thu và lưu file âm thanh thô (`.m4a`/`.wav`)** vào Hộp nháp (`DraftThought`).
  - **CẤM** gọi STT, **CẤM** gọi LLM, **CẤM** bắt người dùng đọc/sửa chữ ngay trong phiên và không được làm ngắt đồng hồ UF3.
  - **Xử lý lỗi thu/lưu:** Thử lại tối đa **3 lần** (Ưu tiên 1). Nếu sau 3 lần vẫn lỗi $\rightarrow$ gửi log lỗi về server và mở ô nhập văn bản nhanh tại chỗ (Ưu tiên 2).
  - Chỉ sau khi kết thúc phiên, nếu người dùng bấm *"Tạo việc từ ghi âm này"* trong Hộp nháp thì mới gọi STT và đẩy sang UF1.
- **UF5 (Kiểm tra tiến độ & Kết thúc phiên):**
  - Thực hiện Voice Check-in 2 chiều, ghi nhận `actual_duration_min`, tách bạch kết quả của `Session` với kết quả của `Step`.
  - Xử lý đủ 5 nhánh: `Hoàn thành phiên`, `Chưa xong`, `Việc đột xuất`, `Bỏ qua`, và `Không phản hồi` (không tự đánh dấu hoàn thành).
  - **Tương tác khi Bỏ qua (Skip):** Hiển thị **3 chip lựa chọn rõ ràng**: `[Hoãn phiên này làm sau]`, `[Bỏ qua phiên này]`, `[Bỏ toàn bộ bước này]`, kèm Voice Check-in ngắn gọn và thanh `SnackBar` Hoàn tác trong 5 giây (W3C COGA).
  - **Chính sách Tự động Tắt DND & Focus Mode:**
    1. Giữ DND trong lúc đang hỏi Check-in cuối phiên.
    2. Tắt DND & Focus Mode ngay khi người dùng xác nhận nghỉ/kết thúc/báo bận/bỏ qua, **HOẶC tự động tắt sau 5 phút nếu người dùng không phản hồi Check-in** (`status = 'no_response'`).
    3. Giữ nguyên DND nếu người dùng chọn làm tiếp luôn phiên kế tiếp.
- **UF6 (Đánh giá tiếp tục & Điều chỉnh lịch):**
  - Đề xuất phương án lịch mới kèm 1 câu giải thích lý do / động viên tinh thần (*Guilt-free Adaptive Rescheduling*).
  - **Quy tắc đóng App trước khi duyệt:** Đóng app **KHÔNG** phải là đồng ý. Phải giữ nguyên lịch cũ đã xác nhận, lưu lịch mới vào `ScheduleProposal(status='pending_approval')`. Khi người dùng mở lại app, bắt buộc chạy thuật toán kiểm tra lại tính khả thi theo thời gian thực tại thời điểm mở app (tịnh tiến các ô lịch đã trôi qua) rồi mới hiển thị cho người dùng duyệt.
- **UF7 (Thiết lập ban đầu & Tùy chỉnh trợ lý):**
  - Thay đổi cài đặt không bao giờ làm thay đổi phiên đang chạy (chỉ áp dụng từ phiên tiếp theo).
  - Đổi giọng đọc/tần suất nhắc $\rightarrow$ áp dụng ngay cho phiên kế tiếp.
  - Đổi thời lượng phiên/nghỉ ảnh hưởng lịch tương lai $\rightarrow$ dùng thuật toán Python tính lại các phiên chưa chạy và đẩy qua **UF6** để người dùng duyệt (kèm cooldown chống làm phiền).
  - **Quy tắc tính Hệ số đệm ($k_{\text{buffer}}$):**
    - Tính $k_{\text{history}}$ theo Moving Average 7–10 phiên gần nhất ($k_{\text{history}} = \frac{1}{N}\sum \frac{\text{actual}}{\text{planned}}$).
    - Ưu tiên 100% cấu hình thủ công của người dùng ($k_{\text{user}}$).
    - Chu kỳ kiểm tra 1 lần/tuần vào cuối tuần: Nếu $|k_{\text{user}} - k_{\text{history}}| > 0.2$ (lệch $> 20\%$), hiển thị gợi ý nhẹ nhàng để người dùng cân nhắc cập nhật.

---

### 2.3. Bảng Ma trận Chuyển trạng thái (State Transition Matrix) cho Agent

| Mã Flow | Đầu vào (Trigger) | Trạng thái / Đầu ra (Output State) | Luồng đích (Next Flow) | Ràng buộc Nghiệp vụ Cốt lõi |
| :--- | :--- | :--- | :--- | :--- |
| **UF7** | Mở Cài đặt / Lần đầu dùng app | Cấu hình lưu vào `USER_PREFERENCE` (áp dụng từ phiên sau) | **UF1** (nếu cài đặt ban đầu) hoặc **UF6** (nếu lệch lịch đã chốt) | Giữ nguyên phiên đang chạy; tính $k_{\text{history}}$ theo Moving Average 7–10 phiên; gợi ý cập nhật 1 lần/tuần nếu lệch > 20%. |
| **UF1** | Nhập công việc bằng Voice (Groq Whisper) hoặc Text | `TASK(status='draft')` nếu thiếu tin; `TASK(status='confirmed')` khi đủ 4 trường | **UF2** (khi đủ 4 thông tin & bấm xác nhận) | Gộp toàn bộ câu hỏi làm rõ vào 1 vòng duy nhất; tự động lưu nháp nếu thoát app; đọc giờ bận từ Google Calendar MCP. |
| **UF2** | `TASK(status='confirmed')` từ UF1 | `SESSION(status='scheduled')` thuộc `Official Schedule` | **UF3** | AI sinh sẵn đề xuất chia bước tránh tê liệt khởi động; thuật toán tính $\Delta T_{\text{deficit}}$; tuyệt đối không tự ý dời Task khác; đồng bộ Google Calendar qua MCP sau khi bấm xác nhận. |
| **UF3** | Đến mốc nhắc (15/10/5p) & User bấm "Bắt đầu" | Kích hoạt Focus Mode + Bật DND + Chạy Visual Timer trên 1-Step UI | **UF4** (nhánh phụ), **UF5** (hết giờ/báo kết quả), **UF6** (đột xuất) | Giao diện tuân thủ 1-Step UI; chạy offline hoàn toàn nếu mất mạng; chuông báo thức chính xác $\le 1\text{s}$. |
| **UF4** | Bấm giữ nút Push-to-Talk "Cất suy nghĩ" trong UF3 | Lưu file ghi âm thô vào `DRAFT_THOUGHT(status='raw_saved')` | **Quay lại UF3 ngay lập tức** | KHÔNG gọi STT, KHÔNG gọi LLM trong phiên; thử lại tối đa 3 lần nếu lỗi $\rightarrow$ mở ô nhập text; timer UF3 vẫn chạy liên tục. |
| **UF5** | Hết giờ phiên / Check-in / Báo kết quả | Ghi nhận `actual_duration_min` và trạng thái `completed`, `incomplete`, `busy`, `skipped`, hoặc `no_response` | **UF3** (nghỉ giải lao / giữ lịch) hoặc **UF6** (đổi lịch / đôn lịch) | Phân biệt rõ kết quả Phiên vs Bước; khi Bỏ qua hiện 3 chip rõ ràng; tự động tắt DND sau 5 phút nếu không phản hồi (`no_response`). |
| **UF6** | Trễ tiến độ / Bận đột xuất / Bỏ qua / Đôn lịch / Đổi cài đặt UF7 | `SCHEDULE_PROPOSAL(status='pending_approval')` hoặc cập nhật `Official Schedule` | **UF3** (vào phiên làm việc kế tiếp) | Đóng app không phải là đồng ý; giữ nguyên lịch cũ; tính toán lại tính khả thi theo thời gian thực tế khi mở lại app; cập nhật Google Calendar qua MCP. |

---

## 3. Ranh giới Kiến trúc: LLM vs. Thuật toán Python & Cơ chế Dự phòng

1. **Phân tách Trách nhiệm (Hybrid Architecture):**
   - **LLM (`Gemini Flash`):** Chỉ xử lý ngôn ngữ tự nhiên & ngữ nghĩa: Trích xuất thực thể & đặt câu hỏi gộp (UF1), sinh sẵn phương án chia `Task -> Steps` & gợi ý Micro-task 2 phút (UF2), phân tích yêu cầu đổi lịch tự nhiên và sinh lời động viên (UF2, UF6).
   - **Thuật toán Python (`scheduler_engine.py`):** Đảm nhận **100%** phép toán thời gian và lịch biểu: Chia `Steps -> Sessions`, nhân hệ số $k_{\text{buffer}}$ cá nhân hóa, kiểm tra trùng giờ, tính phút thiếu hụt $\Delta T$ so với Deadline (UF2) và tịnh tiến ô lịch (UF6, UF7). **CẤM** để LLM tự tính toán cộng/trừ ngày giờ mà không qua thuật toán Python kiểm chứng.
2. **Dự phòng AI Đa Tầng có Hedged Warm Backup (Quality-First 3-Tier Graceful Degradation — Mục tiêu phản hồi $\le 2.5\text{s}$):**
   - **UF1:** Flash-Lite (`MINIMAL`) ở giây 0, hedge Groq mặc định ở 3,5 giây và hết ưu tiên Gemini ở 4,5 giây. Các nhánh chia trần 6/8/10 giây theo ngữ cảnh mặc định. Có thể thử GPT-OSS khi Qwen bị rate limit và còn ngân sách. Python fallback giữ dữ kiện đã biết và hỏi gộp. Yêu cầu sửa chưa áp dụng phải được duyệt trước khi xác nhận bản nháp.
   - **Tầng 1 (Google Gemini) cho UF2/UF6:** `gemini-3.8-flash` (Primary) kết hợp **Warm Backup** `gemini-3.5-flash-lite` (Khung A UF2-Adjust/UF6 ngắt cứng `4.5s`; Khung B phân rã UF2 ưu tiên Primary tới `7.5s`, ngắt cứng `9.0s`).
   - **Tầng 2 (Groq Cross-Provider) cho UF2/UF6:** Dùng **`qwen/qwen3.8-27b`** và model phụ **`openai/gpt-oss-20b`** theo profile khi nhánh Google lỗi nhanh hoặc chạm mốc Hedge.
   - **Tầng 3:** Nếu mất kết nối cả 2 tầng AI, tự động chạy thuật toán Python chia công việc thành các phiên cố định (20–25 phút theo cấu hình user) để không bao giờ trả về màn hình lỗi trắng.
3. **Luồng Giọng nói Kép (Dual-Tier Voice Pipeline):**
   - Ở UF1/UF2/UF5/UF6: Ưu tiên dùng **Groq Whisper (`whisper-large-v3-turbo`)** cho STT và **`edge-tts`** cho TTS qua Backend; tự động fallback về `speech_to_text` và `flutter_tts` trên thiết bị Android khi mạng yếu hoặc Offline.
4. **Tích hợp Google Calendar qua Giao thức MCP:**
   - Dữ liệu sự kiện bận từ Google Calendar (qua MCP) chỉ đóng vai trò tham số đầu vào để xác định quỹ giờ rảnh ($T_{\text{free}}$) cho thuật toán Python. **CẤM** để LLM/MCP tự ý dịch chuyển hoặc ghi đè lịch mà không qua thuật toán Python (`scheduler_engine.py`).
   - Chỉ đồng bộ các phiên Focus Sessions lên Google Calendar **SAU KHI** người dùng bấm xác nhận lịch chính thức (UF2) hoặc duyệt dời lịch (UF6). Tuyệt đối không tự ý ghi sự kiện khi kế hoạch còn ở trạng thái `draft` hoặc `pending_approval`.

---

## 4. Nguyên tắc Thiết kế UI/UX cho Người ADHD (Chuẩn W3C COGA) & NFR

- **Mật độ giao diện:** Tối đa **3–5 thành phần tương tác chính** trên 1 màn hình. Màn hình tập trung (UF3) tuân thủ tuyệt đối **1-Step UI**.
- **Không gây kích thích tiêu cực:** CẤM pop-up bất ngờ, CẤM quảng cáo, CẤM badge thông báo màu đỏ tươi. Sử dụng bảng màu dịu mắt (*Pastel, Soft Green, Warm Gray, Dark Mode dịu nhẹ*).
- **Undo thay vì Confirm Dialog:** CẤM dùng hộp thoại hỏi *"Bạn có chắc chắn muốn xóa/bỏ qua?"*. Thực hiện thao tác ngay và hiện `SnackBar` có nút **Hoàn tác (Undo) trong 5 giây**.
- **Nhãn nút bấm cụ thể:** Dùng nhãn rõ hành động và thời lượng (`"Bắt đầu đếm giờ 25p"`, `"Chia nhỏ việc này"`, `"Cất suy nghĩ"`), CẤM dùng nhãn mơ hồ (`"OK"`, `"Submit"`, `"Tiếp tục"`).
- **Giải phóng trí nhớ ngắn hạn:** Luôn hiện rõ Deadline; auto-save mọi form nhập dở; hiển thị thẻ nhắc ngữ cảnh (*"Bạn đang ở bước nào? Đang làm gì?"*) khi người dùng quay lại app sau gián đoạn; hiển thị hiệu ứng chờ thư giãn (*Shimmer / Breathing circle*) kèm dòng chữ `"AI đang bẻ nhỏ công việc giúp bạn..."` khi gọi LLM.
- **Độ tin cậy & Offline:** Báo thức hết phiên phải chính xác (dung sai $\le 1\text{s}$) kể cả trong **Android Doze Mode**. Timer, lịch trong ngày và thu âm UF4 phải hoạt động bình thường khi **Offline**.
- **Giữ chân người dùng:** Áp dụng **Đóng băng chuỗi (Streak Freeze)** khi lỡ nhịp, không xóa sạch chuỗi về 0.

---

## 5. Quy chuẩn Mã nguồn, Ngôn ngữ & Quản lý Git

1. **Ngôn ngữ:**
   - **Tiếng Việt:** Dùng cho phản hồi chat với thành viên, văn bản hiển thị trên UI (Microcopy), comment giải thích nghiệp vụ phức tạp và System Prompt cho AI.
   - **Tiếng Anh:** Dùng cho toàn bộ tên file, biến, hàm, class, endpoint và tên bảng/cột Database (`snake_case` cho Python/SQL, `camelCase`/`PascalCase` cho Dart/Flutter).
2. **Tiêu chuẩn Kỹ thuật Backend (`backend/`):**
   - Python 3.12 + FastAPI + Uvicorn (ưu tiên `async/await`, độ trễ API thường $\le 300\text{ms}$).
   - Bắt buộc dùng cú pháp **SQLAlchemy 2.0** (`Mapped[]`, `mapped_column()`, `select()`) và **Pydantic v2** (`model_validate`, `ConfigDict`).
   - Truy vấn ORM phải tương thích 100% với cả **SQLite** (Giai đoạn phát triển) và **PostgreSQL** (Giai đoạn deploy).
3. **Tiêu chuẩn Kỹ thuật Mobile (`mobile/`):**
   - Flutter SDK (Dart), quản lý state bằng `provider`, gọi mạng bằng `dio` + `flutter_secure_storage`, lưu cache cục bộ bằng SQLite/`shared_preferences`.
   - Luôn đảm bảo code vượt qua kiểm tra `dart analyze` không có lỗi và duy trì tốc độ khung hình 60 FPS.
4. **Quy tắc Git & Bảo mật:**
   - Commit message tuân thủ chuẩn **Conventional Commits** (ví dụ: `feat(uf1): add single-round task confirmation`, `fix(uf5): auto disable dnd after 5m timeout`, `docs: update flowchart`).
   - **TUYỆT ĐỐI KHÔNG** commit file `.env`, API Keys (Gemini, Groq), file cơ sở dữ liệu `.db` cục bộ hoặc các file ghi âm thử nghiệm lên Git.

---

## 6. Lộ trình Phân kỳ 3 Tuần Cân bằng Tải (Sprint Roadmap)

Đội ngũ áp dụng chiến lược **Feature-First (Planning $\rightarrow$ Execution $\rightarrow$ Adaptation)** theo artifact [sprint_3_weeks_plan.md](file:///C:/Users/LENOVO/.gemini/antigravity/brain/092dee7e-439e-4f13-a834-2428c0198f0e/sprint_3_weeks_plan.md):

- **Tuần 1 — Giai đoạn Lập kế hoạch (Planning Phase: UF1 + UF2):**
  - Người dùng nhập việc (Voice/Text) $\rightarrow$ AI trích xuất 4 trường gộp 1 vòng $\rightarrow$ AI sinh Default Proposal $\rightarrow$ Thuật toán Python xếp lịch Rolling Horizon 2–3 tuần $\rightarrow$ Xác nhận lịch chính thức (sử dụng Default Config 25p làm / 5p nghỉ / buffer 1.2).
- **Tuần 2 — Giai đoạn Thực thi Tập trung (Execution Phase: UF3 + UF4):**
  - Nhắc trước phiên (15/10/5p) $\rightarrow$ Micro-task 2 phút $\rightarrow$ 1-Tap Start (Focus + DND + 1-Step UI) $\rightarrow$ Chuông báo thức Doze Mode chính xác $\le 1\text{s}$ $\rightarrow$ Nút Cất suy nghĩ Raw Audio UF4 (retry 3 lần $\rightarrow$ mở ô text).
- **Tuần 3 — Giai đoạn Thích ứng, Tái lập lịch & Cài đặt (Adaptation Phase: UF5 + UF6 + UF7):**
  - Voice Check-in 2 chiều cuối phiên (3 chip Bỏ qua, tự tắt DND sau 5p) $\rightarrow$ Adaptive Rescheduling UF6 (lưu `pending_approval`, tịnh tiến giờ thực tế khi mở lại app) $\rightarrow$ Màn hình Cài đặt UF7 hoàn chỉnh + Tiến hóa Buffer MA 7–10 phiên $\rightarrow$ Nghiệm thu E2E toàn trình.
