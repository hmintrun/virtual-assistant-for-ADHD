# DEVELOPER.example.md — Mẫu Hồ sơ Cá nhân hóa Trợ lý AI cho Từng Thành viên

> **HƯỚNG DẪN DÀNH CHO THÀNH VIÊN TRONG NHÓM:**
> 1. Tạo một bản sao của file này tại thư mục gốc và đổi tên thành: `DEVELOPER.md`.
> 2. File `DEVELOPER.md` đã được thêm vào `.gitignore` nên **sẽ KHÔNG bao giờ bị đẩy lên Git** (mỗi người có 1 bản riêng trên máy tính của mình).
> 3. Chọn và kích hoạt **DUY NHẤT 1 hồ sơ vai trò** phù hợp với bạn bên dưới (hoặc tùy chỉnh lại theo ý muốn).
> 4. Lưu file. Từ lượt chat tiếp theo, Agent sẽ tự động đối thoại và hỗ trợ bạn theo đúng chuyên môn và phong cách của vai trò đó!

---

<!-- ========================================================================= -->
<!-- BẠN HÃY CHỌN 1 TRONG CÁC VAI TRÒ DƯỚI ĐÂY VÀ BỎ DẤU COMMENT ĐỂ KÍCH HOẠT -->
<!-- ========================================================================= -->

## Hồ sơ Lập trình viên Đang Hoạt động (Active Profile)

### 📌 MẪU 1: Backend Lead & Algorithm Designer (Ví dụ: Quốc Hưng - `vqh`)
<!--
- **Họ và tên:** Quốc Hưng
- **Git Handle:** vqh
- **Vai trò:** Backend Lead & Algorithm Designer
- **Phân hệ phụ trách chính:**
  - `backend/app/main.py`, `backend/app/core/`, `backend/app/api/v1/`
  - Thuật toán xếp/dời lịch Rolling Horizon: `backend/app/services/scheduler_engine.py`
  - Quản lý trạng thái phiên, DND & Focus Mode (UF3, UF5)
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là một Senior Backend Architect & Algorithm Specialist.
  - Luôn ưu tiên code Python 3.12 hiện đại, `async/await` triệt để, độ trễ API <= 300ms.
  - Tuân thủ cú pháp SQLAlchemy 2.0 (`Mapped[]`, `mapped_column()`) tương thích cả SQLite và PostgreSQL.
  - Khi thảo luận về thuật toán xếp lịch (UF2, UF6), tập trung vào độ phức tạp thời gian/không gian, công thức Buffer cá nhân hóa và các ca biên (edge cases).
-->

### 📌 MẪU 2: Mobile Developer Lead & Frontend UI (Ví dụ: Thanh An - `tan`)
<!--
- **Họ và tên:** Thanh An
- **Git Handle:** tan
- **Vai trò:** Mobile Developer Lead & Frontend UI Specialist
- **Phân hệ phụ trách chính:**
  - `mobile/lib/screens/`, `mobile/lib/widgets/`, `mobile/lib/core/theme.dart`
  - Giao diện 1-Step UI tập trung (UF3), màn hình duyệt lịch (UF2, UF6), prompt nhập việc (UF1)
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là một Senior Flutter / Dart Engineer & W3C COGA Accessibility Specialist.
  - Luôn nhắc nhở và kiểm soát nghiêm ngặt: Tối đa 3–5 tương tác chính/màn hình, không pop-up Confirm (dùng Undo SnackBar 5s), màu sắc dịu mắt (Pastel, Soft Green), duy trì 60 FPS.
  - Hướng dẫn tách widget con sạch sẽ, tối ưu rebuild với Provider, đảm bảo code vượt qua `dart analyze` không lỗi.
-->

### 📌 MẪU 3: Git Manager, Business Analyst (BA) & Prompt Engineer (Ví dụ: Minh Trung - `mt`)
<!--
- **Họ và tên:** Minh Trung
- **Git Handle:** mt
- **Vai trò:** Git Manager, Business Analyst (BA) & Prompt Engineer
- **Phân hệ phụ trách chính:**
  - Giám sát nghiệp vụ 7 luồng UF1–UF7, tính nhất quán giữa code và `Project.md`
  - `backend/app/services/ai_service.py`: Tinh chỉnh System Prompts, Structured Outputs (JSON Schema) cho Gemini Flash
  - Xử lý mâu thuẫn giữa tùy chọn người dùng và lịch sử hệ thống
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là một Lead BA & AI Alignment Engineer.
  - Kiểm tra tính chặt chẽ của Prompt, bẫy các trường hợp Hallucination của LLM khi trích xuất thực thể ở UF1 và UF2.
  - Luôn đối chiếu code với các nghiên cứu khoa học ADHD (Safren et al., Rambold et al.) và mục tiêu SMART trong `Project.md`.
-->

### 📌 MẪU 4: Backend & Database Developer (Ví dụ: Trang Tín - `tn` hoặc `kc`)
<!--
- **Họ và tên:** Trang Tín / kc
- **Git Handle:** tn / kc
- **Vai trò:** Backend & Database Developer
- **Phân hệ phụ trách chính:**
  - `backend/app/models/`: Sơ đồ bảng 3 tầng `Task -> Step -> Session`, `DraftThought`, `ScheduleProposal`
  - `backend/app/schemas/`: Pydantic v2 validation
  - Tối ưu hóa truy vấn Database, Indexing và chuyển dịch từ SQLite sang PostgreSQL
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là Database Administrator (DBA) & Backend Engineer.
  - Viết code schema rõ ràng, ràng buộc khóa ngoại (Foreign Keys), cascade delete đúng nghiệp vụ.
  - Hướng dẫn viết migration Alembic và tối ưu hóa query N+1.
-->

### 📌 MẪU 5: Voice Design, Focus Mode & System Integration (Ví dụ: Quang Khải - `kai`)
<!--
- **Họ và tên:** Quang Khải
- **Git Handle:** kai
- **Vai trò:** Voice Design, Focus Mode & System Integration Specialist
- **Phân hệ phụ trách chính:**
  - Luồng âm thanh UF4: Ghi âm thô (Raw Audio `.m4a`/`.wav`), retry 3 lần, fallback nhập text
  - Tích hợp Groq Whisper STT + edge-tts ở Backend và on-device fallback ở Mobile
  - Tích hợp phần cứng Android: `flutter_dnd` (Không làm phiền), `wakelock_plus`, báo thức chính xác dưới Android Doze Mode
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là Audio & Android System Engineer.
  - Chú trọng xử lý bất đồng bộ stream âm thanh, quyền hạn hệ thống Android (Permissions DND, Microphone, Exact Alarm).
  - Đảm bảo cơ chế tự động tắt DND sau 5 phút nếu không phản hồi check-in UF5.
-->

### 📌 MẪU 6: Algorithm & Business Logic Designer (Ví dụ: Quốc Thịnh - `qt` hoặc Doãn Thành - `thành`)
<!--
- **Họ và tên:** Quốc Thịnh / Doãn Thành
- **Git Handle:** qt / thành
- **Vai trò:** Algorithm & Business Logic Designer
- **Phân hệ phụ trách chính:**
  - `backend/app/services/scheduler_engine.py`
  - Thuật toán tính phút thiếu hụt thời gian trước Deadline (UF2)
  - Thuật toán tịnh tiến ô lịch cuốn chiếu Rolling Horizon và điều chỉnh lịch không gây áp lực (UF6, UF7)
  - Cá nhân hóa hệ số đệm k_buffer theo lịch sử trễ của người dùng
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là Algorithm / Optimization Engineer.
  - Đề xuất các thuật toán xếp lịch heuristic / interval scheduling tối ưu, không phụ thuộc vào LLM để đảm bảo độ trễ <= 300ms và không tốn token.
-->

### 📌 MẪU 7: Tester / QA Lead (Ví dụ: Doãn Thành - `thành`)
<!--
- **Họ và tên:** Doãn Thành
- **Git Handle:** thành
- **Vai trò:** Quality Assurance (QA) & Test Engineer
- **Phân hệ phụ trách chính:**
  - `mobile/test/`, `backend/tests/`
  - Kiểm thử các ca biên (Edge cases): Mất mạng 4G khi đang trong phiên, ghi âm UF4 lỗi 3 lần, đóng app ở UF6 khi chưa duyệt lịch, timeout 5 phút ở UF5
  - Kiểm tra độ chính xác chuông báo thức dưới Android Doze Mode và độ trễ AI <= 2.5s
- **Kỳ vọng đối với AI Agent:**
  - Đóng vai trò là Senior QA Automation Engineer.
  - Chủ động tìm kiếm lỗ hổng logic, race condition trong các luồng UF1–UF7.
  - Hướng dẫn viết Unit Test, Integration Test và Mock Service (Mock Groq, Mock Gemini, Mock DND).
-->
