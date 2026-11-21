# W4-03A · Cơ sở dữ liệu QA

## Chuẩn bị SQLite và PostgreSQL 16

Chạy từ `backend/` của bản `dev` dùng cho QA. Đặt `$env:DATABASE_URL` trỏ vào DB mới dành riêng cho QA. Dùng `sqlite+aiosqlite` cho SQLite và `postgresql+asyncpg` cho PostgreSQL. Có thể dựng PostgreSQL 16 bằng `compose.staging.yml`. Compose mặc định dùng cổng `5433` và đọc mật khẩu từ `PROPLAN_STAGING_PASSWORD`.

```powershell
uv run --frozen alembic upgrade head
uv run --frozen alembic current
```

Snapshot Backend `4ae869e` dùng revision `20261111_01`. SQLite và PostgreSQL 16.15 đều đã áp migration này từ DB sạch.

> **Cập nhật sau merge `BE-03`:** Snapshot nhánh `backend` hiện tại đã nâng Alembic head lên `20261118_01` (bổ sung bảng `refresh_tokens` phục vụ xoay vòng/thu hồi refresh token của `BE-03`).

## Đăng nhập và seed

1. Khởi động Backend QA với `AUTH_MODE=google`, `GOOGLE_SIGN_IN_AUDIENCES` khớp client ID dùng trong bản QA và `JWT_SECRET_KEY` riêng cho môi trường. Cấu hình Google Sign-In theo [AUTH_GUIDE.md](AUTH_GUIDE.md).
2. Thành đăng nhập lần lượt tài khoản A và B qua app QA. Nếu kiểm tra hai DB riêng, thực hiện bước đăng nhập trên Backend trỏ vào từng DB. Lần đăng nhập đầu tạo user đã liên kết Google và cấu hình mặc định.
3. Trong terminal dùng cùng `DATABASE_URL` với Backend QA, chạy seed. Thay hai email ví dụ bằng email A và B do QA cung cấp.

   ```powershell
   uv run --frozen python scripts/seed_w4_qa.py 'qa-a@example.test' 'qa-b@example.test'
   ```

Script chỉ ghi khi tìm thấy cả hai user Google đang hoạt động, dùng chung một transaction. Mỗi user có một nháp đang thu thập thông tin và một lịch sử Task → Step → Session tổng hợp với Check-in đã trả lời. Chạy lại giữ nguyên thiết lập và tránh nhân đôi dữ liệu mẫu đang tồn tại. Script lấy `DATABASE_URL` từ terminal để người chạy chỉ định rõ DB đích.

## Smoke trước khi bàn giao cho QA

- Với từng tài khoản, kiểm tra `/api/v1/auth/me`, `/api/v1/preferences`, `/api/v1/drafts` và `/api/v1/tasks/{task_id}`. Nhãn `[W4 QA A]` và `[W4 QA B]` giúp nhận biết dữ liệu mỗi user.
- Đọc chi tiết nháp hoặc Task của tài khoản kia phải trả `404`. Kiểm tra Step và Session mẫu ở trạng thái `completed`.
- Ghi SHA, Alembic head, hệ quản trị DB và kết quả của A/B trong log smoke. Giữ email ngoài tài liệu chung và giữ token, mật khẩu ngoài log.

Đã kiểm thử trên DB SQLite và PostgreSQL 16.15 riêng với hai danh tính giả lập tại điểm xác minh Google. Kịch bản bao gồm DB sạch, seed từ chối khi thiếu tài khoản, đăng nhập qua API, chạy seed hai lần, giữ thiết lập đã sửa và cô lập dữ liệu giữa hai user.

## Dùng staging Render chung

API của staging chính thức là `https://virtual-assistant-for-adhd.onrender.com/api/v1`. Thành dùng APK QA trỏ vào URL này hoặc chạy Flutter Web trên máy mình ở `http://localhost:8080` với cùng API base URL. Nhà quản trị OAuth kiểm tra quyền Test users nếu Google chặn đăng nhập.

Staging dùng PostgreSQL `proplan-staging-db` và chạy Alembic khi khởi động. Khi seed từ máy cá nhân, dùng kết nối External Database URL có TLS do người quản lý staging cấp qua cấu hình cục bộ. Đọc `alembic_version` trước khi seed và chỉ chạy script W4-03A cho hai tài khoản đã xác minh. Giữ các bài kiểm thử tự động trên hai DB QA riêng.

**Kết quả 18/11/2026.** Hai tài khoản Google QA của Thành đã được tạo qua đăng nhập trên staging. Seed và đọc lại đều đạt cho A và B trên PostgreSQL Render tại Alembic head `20261111_01`. Đã kiểm tra preferences, nháp thu thập thông tin và lịch sử Task → Step → Session theo từng tài khoản. SQLite cục bộ đã qua cùng kịch bản seed bằng danh tính giả lập.
