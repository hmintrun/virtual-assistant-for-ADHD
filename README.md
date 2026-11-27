# ProPlan VA (adhd-assistant)

> **Trợ lý ảo AI hỗ trợ lập kế hoạch, chia nhỏ công việc và nhắc lịch chủ động bằng giọng nói trên thiết bị di động cho người trưởng thành mắc hội chứng ADHD (thể giảm chú ý ADHD-I).**

---

## 📌 Nguồn Tham chiếu Chuẩn (Documentation Index)

- **[Project.md](./Project.md):** **Single Source of Truth** — Toàn bộ đặc tả chi tiết (Phát biểu bài toán 5W1H, Cơ sở khoa học CBT/Scaffolding, Tiêu chuẩn NFR & W3C COGA, Sơ đồ ERD SQLAlchemy 2.0, Flowchart Mermaid UF1–UF7, Ma trận chuyển trạng thái và Phân công nhân sự).
- **[AGENTS.md](./AGENTS.md):** Quy tắc bất biến, chế độ Strict Guardrail, ranh giới kiến trúc Hybrid (LLM vs. Thuật toán Python), quy chuẩn mã nguồn và hướng dẫn cho AI Coding Agent.
- **[DEVELOPER.example.md](./DEVELOPER.example.md) $\rightarrow$ `DEVELOPER.md`:** Hồ sơ lập trình viên cục bộ để cá nhân hóa phong cách làm việc của AI Agent theo từng vai trò thành viên.

---

## 🚀 Lộ trình 3 Tuần Đạt MVP (3-Week Feature-First Roadmap)

Dự án áp dụng chiến lược **Feature-First (Planning $\rightarrow$ Execution $\rightarrow$ Adaptation)** giúp nhóm có bản Demo AI hoàn chỉnh ngay cuối Tuần 1 và phân bổ cân bằng tải cho toàn bộ 7 thành viên:

| Giai đoạn | Mục tiêu Trọng tâm | User Flows Chinh phục | Đầu ra Bàn giao (Deliverables) |
| :--- | :--- | :---: | :--- |
| **Tuần 1** | **Lập Kế hoạch (Planning Phase)** | **UF1 + UF2** | Nhập việc bằng Voice/Text $\rightarrow$ Gemini Flash trích xuất 4 trường & gộp 1 vòng $\rightarrow$ AI sinh Default Proposal $\rightarrow$ Thuật toán Python xếp lịch Rolling Horizon 2–3 tuần $\rightarrow$ Chốt lịch chính thức (APK v0.1). |
| **Tuần 2** | **Thực thi Tập trung (Execution Phase)** | **UF3 + UF4** | Nhắc trước giờ (15/10/5p) $\rightarrow$ Micro-task 2 phút $\rightarrow$ 1-Tap Start kích hoạt đồng thời Focus Mode + Wakelock + DND $\rightarrow$ Giao diện 1-Step UI $\rightarrow$ Chuông báo Doze Mode $\le 1\text{s}$ $\rightarrow$ Nút Push-to-Talk "Cất suy nghĩ" lưu Raw Audio (APK v0.2). |
| **Tuần 3** | **Thích ứng & Cài đặt (Adaptation Phase)** | **UF5 + UF6 + UF7** | Voice Check-in 2 chiều cuối phiên (3 chip Bỏ qua, tự tắt DND sau 5p) $\rightarrow$ Adaptive Rescheduling UF6 (lưu `pending_approval`, tịnh tiến giờ thực tế khi mở lại app) $\rightarrow$ Màn hình Cài đặt UF7 hoàn chỉnh & Tiến hóa Buffer (Moving Average 7–10 phiên) $\rightarrow$ Đóng gói APK v1.0. |

---

## 🛠️ Công nghệ Thành phần (Tech Stack)

| Thành phần | Công nghệ / Thư viện | Vai trò cốt lõi |
| :--- | :--- | :--- |
| **Mobile App** | **Flutter SDK (Dart)** | Đa nền tảng Android, Theme chuẩn W3C COGA dịu mắt, `provider`, `dio`, `flutter_dnd`, `wakelock_plus`, Exact Alarms. |
| **Backend Server** | **Python 3.12 (FastAPI + Uvicorn)** | Xử lý bất đồng bộ `async/await`, API CRUD $\le 300\text{ms}$, tích hợp thuật toán lập lịch `scheduler_engine.py`. |
| **AI / LLM Engine** | **`gemini-3.5-flash-lite`** (Google AI Studio / Vertex AI) | Phân tích ngữ nghĩa, trích xuất thực thể (UF1), sinh phương án chia bước & Micro-task 2p (UF2), chọn câu động viên không phán xét (UF6). |
| **Cơ chế Dự phòng AI** | **3-Tier Fallback** ($\le 2.5\text{s}$) | Tầng 1: Gemini 3.5 Flash-Lite $\rightarrow$ Tầng 2: Groq Cloud (`qwen/qwen3.8-27b` + `openai/gpt-oss-20b`) $\rightarrow$ Tầng 3: Thuật toán Python chia cố định 20–25p. |
| **Giọng nói (Voice)** | **Gemini 3.5 Transcribe / ElevenLabs Scribe v2 / Deepgram Nova-3** & **edge-tts** | STT đa tầng tiếng Việt (7s / 5s / 5s; `uf5_checkin` ưu tiên ElevenLabs) và đọc giọng nói tự nhiên (TTS); fallback on-device STT/TTS khi offline. |
| **Cơ sở Dữ liệu** | **SQLAlchemy 2.0 ORM** | Tương thích kép 100%: SQLite (Local Dev) và PostgreSQL (Cloud Production), lưu cache cục bộ bằng `sqflite`. |
| **Lịch Ngoại vi** | **Google Calendar MCP** | Đọc sự kiện bận ($T_{\text{busy}}$) và đồng bộ 2 chiều các khối sự kiện (`Focus Blocks`) sau khi người dùng chốt lịch. |

---

## 🏗️ Cấu trúc Thư mục Monorepo

```text
.
├── backend/                  # Mã nguồn Backend (FastAPI + SQLAlchemy 2.0 + SQLite/Postgres)
│   ├── app/
│   │   ├── api/v1/           # Endpoints UF1 – UF7 (tasks, schedule, sessions, drafts, preferences)
│   │   ├── core/             # Config, Security, Database session
│   │   ├── models/           # SQLAlchemy 2.0 ORM models
│   │   ├── schemas/          # Pydantic v2 schemas & AI Structured Outputs
│   │   └── services/         # ai_service, scheduler_engine, voice_service, calendar_mcp_service
│   ├── requirements.txt
│   └── .env.example
├── mobile/                   # Mã nguồn Ứng dụng Di động (Flutter SDK - Dart)
│   ├── lib/
│   │   ├── core/             # Theme W3C COGA, Dio client, SQLite local cache
│   │   ├── models/           # Dart data models
│   │   ├── providers/        # State management (TaskProvider, FocusTimerProvider, UserProvider)
│   │   ├── screens/          # Giao diện UF1 – UF7 (Prompt, Schedule Review, 1-Step Focus UI...)
│   │   └── widgets/          # Nút bấm nhãn cụ thể, Shimmer AI loading, Countdown Timer
│   └── pubspec.yaml
├── docs/                     # Báo cáo kỹ thuật, kế hoạch sprint, schema, biên bản cuộc họp
├── AGENTS.md                 # Bộ quy tắc cốt lõi & Strict Guardrails cho AI Coding Agents
├── DEVELOPER.example.md      # Template hồ sơ lập trình viên cục bộ
├── Project.md                # Single Source of Truth toàn dự án
└── README.md                 # Hướng dẫn tổng quan dự án
```

---

## 🔒 Quy tắc An toàn & Bảo mật (Strict Security Rules)

- **TUYỆT ĐỐI KHÔNG** commit file `.env`, API Keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `ELEVENLABS_API_KEY`, `DEEPGRAM_API_KEY`), file database cục bộ (`.db`, `test.db`) hoặc file âm thanh thử nghiệm lên Git repository.
- Mọi nhánh tính năng phải tuân thủ quy chuẩn `feat/<userflow>-<tên-ngắn>` và commit message theo chuẩn **Conventional Commits**.
