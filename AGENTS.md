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
   - File [Project.md](./Project.md) tại thư mục gốc chứa toàn bộ đặc tả chi tiết (Cơ sở khoa học, Tech Stack, NFR, Sơ đồ ERD, Flowchart Mermaid UF1–UF7 và Phân công nhân sự).
   - **Trước khi thiết kế hoặc viết code** cho bất kỳ phân hệ nào (`backend/`, `mobile/`, `docs/`), Agent **PHẢI** đối chiếu với mục tương ứng trong [Project.md](./Project.md).
2. **Chế độ Bảo vệ Thiết kế (Strict Guardrail — BẮT BUỘC):**
   - Nếu bất kỳ prompt nào của thành viên trong nhóm yêu cầu viết code **vi phạm các quy tắc bất biến** dưới đây hoặc trong [Project.md](./Project.md) (ví dụ: *đòi thêm hộp thoại Confirm hỏi xác nhận xóa, gọi STT/LLM ngay khi bấm Cất suy nghĩ ở UF4, tự động đánh dấu hoàn thành phiên khi hết giờ ở UF5, ghi đè lịch cũ khi user tắt app ở UF6, hoặc nhồi nhét quá 5 phần tử tương tác lên màn hình*), Agent **PHẢI DỪNG LẠI**, **cảnh báo rõ điểm vi phạm**, **trích dẫn quy tắc tương ứng trong `Project.md`** và **đề xuất giải pháp đúng chuẩn** trước khi viết code.

---

## 2. Quy tắc Bất biến về Mô hình Dữ liệu & 7 Luồng Nghiệp vụ (UF1 – UF7)

### Mô hình Phân cấp 3 Tầng
- Cấu trúc dữ liệu bắt buộc: `1 Công việc (Task)` $\rightarrow$ `Nhiều Bước (Steps)` $\rightarrow$ `Mỗi Bước gồm Nhiều Phiên (Sessions)`.
- **Quy tắc bất biến:** Kết thúc đồng hồ đếm ngược của 1 `Session` **KHÔNG** tự động đánh dấu hoàn thành `Session`, `Step` hay `Task`. Mọi trạng thái hoàn thành phải đi qua bước xác nhận ở **UF5**.

### Luật Cốt lõi cho Từng User Flow (UF1 – UF7)
- **UF1 (Tương tác & Xác nhận việc):**
  - Kiểm tra đủ 4 thông tin bắt buộc: `Nội dung`, `Phạm vi (Scope)`, `Khung giờ rảnh`, `Deadline`.
  - **Gộp 1 vòng duy nhất:** Hiển thị bản tóm tắt kèm toàn bộ câu hỏi làm rõ thông tin thiếu/mâu thuẫn trong **1 lần hiển thị** (không hỏi lắt nhắt nhiều vòng, không tự đoán dữ liệu).
  - Tự động lưu nháp (`status = 'draft'`) nếu người dùng thoát app hoặc chưa muốn bổ sung ngay. Chỉ chuyển sang UF2 khi đủ thông tin và người dùng xác nhận 1 lần.
- **UF2 (Bẻ nhỏ việc & Chốt kế hoạch):**
  - Áp dụng **Rolling Horizon**: Giữ tổng quan cả dự án nhưng chỉ xếp lịch chi tiết các phiên cho **2–3 tuần gần nhất**.
  - **Công thức kiểm tra khả thi trước Deadline:**
    $$T_{\text{req}} = \sum_{i=1}^{N} \left( \text{PlannedDuration}_i \times k_{\text{buffer}} + \text{BreakDuration}_i \right)$$
    Nếu $T_{\text{req}} > T_{\text{free}}$ (quỹ giờ rảnh trước Deadline), hệ thống **cấm tự ý dời hạn hoặc cắt việc**; phải thông báo rõ số phút thiếu hụt tối thiểu $\Delta T = T_{\text{req}} - T_{\text{free}}$ để người dùng quyết định.
  - Hỗ trợ chỉnh sửa lịch trực tiếp trên UI hoặc qua yêu cầu ngôn ngữ tự nhiên trước khi bấm Xác nhận.
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
  - Xử lý đủ 5 nhánh: `Hoàn thành phiên`, `Chưa xong`, `Việc đột xuất`, `Bỏ qua` (phân rõ: bỏ phiên hiện tại / để làm sau / bỏ cả bước), và `Không phản hồi` (không tự đánh dấu hoàn thành).
  - **Chính sách Tự động Tắt DND & Focus Mode:**
    1. Giữ DND trong lúc đang hỏi Check-in cuối phiên.
    2. Tắt DND & Focus Mode ngay khi người dùng xác nhận nghỉ/kết thúc/báo bận/bỏ qua, **HOẶC tự động tắt sau 5 phút nếu người dùng không phản hồi Check-in** (`status = 'no_response'`).
    3. Giữ nguyên DND nếu người dùng chọn làm tiếp luôn phiên kế tiếp.
- **UF6 (Đánh giá tiếp tục & Điều chỉnh lịch):**
  - Đề xuất phương án lịch mới kèm 1 câu giải thích lý do / động viên tinh thần (*Guilt-free Rescheduling*).
  - **Quy tắc đóng App trước khi duyệt:** Đóng app **KHÔNG** phải là đồng ý. Phải giữ nguyên lịch cũ đã xác nhận, lưu lịch mới vào `ScheduleProposal(status='pending_approval')`. Khi người dùng mở lại app, bắt buộc chạy thuật toán kiểm tra lại tính khả thi theo thời gian thực tại thời điểm mở app rồi mới hiển thị cho người dùng duyệt.
- **UF7 (Thiết lập ban đầu & Tùy chỉnh trợ lý):**
  - Thay đổi cài đặt không bao giờ làm thay đổi phiên đang chạy (chỉ áp dụng từ phiên tiếp theo).
  - Đổi giọng đọc/tần suất nhắc $\rightarrow$ áp dụng ngay cho phiên kế tiếp.
  - Đổi thời lượng phiên/nghỉ ảnh hưởng lịch tương lai $\rightarrow$ dùng thuật toán Python tính lại các phiên chưa chạy và đẩy qua **UF6** để người dùng duyệt (kèm cooldown chống làm phiền).

---

## 3. Ranh giới Kiến trúc: LLM vs. Thuật toán Python & Cơ chế Dự phòng

1. **Phân tách Trách nhiệm (Hybrid Architecture):**
   - **LLM (`Gemini Flash`):** Chỉ xử lý ngôn ngữ tự nhiên & ngữ nghĩa: Trích xuất thực thể & đặt câu hỏi gộp (UF1), bẻ nhỏ `Task -> Steps` & sinh gợi ý Micro-task 2 phút (UF2), phân tích yêu cầu đổi lịch tự nhiên và sinh lời động viên (UF2, UF6).
   - **Thuật toán Python (`scheduler_engine.py`):** Đảm nhận **100%** phép toán thời gian và lịch biểu: Chia `Steps -> Sessions`, nhân hệ số $k_{\text{buffer}}$ cá nhân hóa, kiểm tra trùng giờ, tính phút thiếu hụt $\Delta T$ so với Deadline (UF2) và tịnh tiến ô lịch (UF6, UF7). **CẤM** để LLM tự tính toán cộng/trừ ngày giờ mà không qua thuật toán Python kiểm chứng.
2. **Dự phòng AI 3 Tầng (3-Tier Graceful Degradation — Độ trễ $\le 2.5\text{s}$):**
   - **Tầng 1:** `Gemini Flash API` (timeout $\le 2.5\text{s}$).
   - **Tầng 2:** Tự động chuyển sang LLM dự phòng (**Llama-3 trên Groq Cloud** hoặc model Gemini dự phòng) nếu Tầng 1 lỗi/timeout/rate-limit.
   - **Tầng 3:** Nếu mất kết nối cả 2 tầng AI, tự động chạy thuật toán Python chia công việc thành các phiên cố định (20–25 phút theo cấu hình user) để không bao giờ trả về màn hình lỗi trắng.
3. **Luồng Giọng nói Kép (Dual-Tier Voice Pipeline):**
   - Ở UF1/UF2/UF5/UF6: Ưu tiên dùng **Groq Whisper (`whisper-large-v3-turbo`)** cho STT và **`edge-tts`** cho TTS qua Backend; tự động fallback về `speech_to_text` và `flutter_tts` trên thiết bị Android khi mạng yếu hoặc Offline.

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
