# ProPlan VA (adhd-assistant)

Trợ lý ảo AI hỗ trợ lập kế hoạch, chia nhỏ công việc và nhắc lịch chủ động bằng giọng nói trên thiết bị di động cho người mắc hội chứng ADHD (thể giảm chú ý ADHD-I).

---

## 📌 Nguồn tham chiếu chuẩn cho Nhà phát triển & AI Agent

- **[Project.md](./Project.md):** Toàn bộ đặc tả chi tiết (Problem Statement 5W1H, Cơ sở khoa học 12+ nghiên cứu, NFR, ERD, Flowchart UF1–UF7, Phân công nhân sự).
- **[AGENTS.md](./AGENTS.md):** Quy tắc bất biến, ranh giới kiến trúc LLM vs. Thuật toán Python, chế độ Strict Guardrail và quy chuẩn code cho AI Agent.

---

## 🏗️ Cấu trúc Monorepo

```text
.
├── backend/            # Python 3.12 + FastAPI + SQLAlchemy 2.0
├── mobile/             # Flutter SDK (Dart)
├── docs/               # Báo cáo, sơ đồ kiến trúc, link Figma
├── .gitignore          # Cấu hình loại trừ file nhạy cảm (.env, .db, audio)
├── AGENTS.md           # Quy tắc cho AI Agent
├── Project.md          # Đặc tả Single Source of Truth
└── README.md           # Hướng dẫn dự án
```
