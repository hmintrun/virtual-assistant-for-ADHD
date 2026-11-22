# W4-06 · Checklist Cấu hình & Bảo mật Staging (ProPlan VA Backend)

Tài liệu nghiệm thu cấu hình môi trường **staging / production** cho hạng mục **`W4-06`** (`BE-02`, `BE-03`, `BE-05`, ASGI Security Headers & Observability) trên nền nhánh `backend` (Alembic head `20261118_01`).

> [!CAUTION]
> **Quy tắc bảo mật tuyệt đối:** Không bao giờ ghi, dán hoặc commit khóa bí mật, mật khẩu DB, token OAuth, Service Account JSON hay email QA thật vào tài liệu/log/Git. Mọi giá trị nhạy cảm trong bảng dưới đều dùng placeholder (`<REDACTED>`, `<32+ random bytes>`).

---

## 1. Bảng biến môi trường bắt buộc khi `ENVIRONMENT=staging` (hoặc `production`)

Nguồn đối chiếu mã nguồn: `backend/app/core/config.py`, `backend/app/core/security.py`, `backend/app/main.py`, `backend/.env.example`, `backend/Dockerfile`, `docs/backend/W4_03A_QA_DB.md`.

| Biến môi trường | Giá trị mẫu / Định dạng hợp lệ | Ràng buộc & Startup Guard trong Code |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `staging` (hoặc `production`) | Kích hoạt `settings.is_deployed == True`; bắt buộc toàn bộ guard fail-fast bên dưới khi khởi động. |
| `AUTH_MODE` | `google` | **Bắt buộc `google`.** `validate_auth_configuration()` tại `lifespan` (`app/main.py`) sẽ ném `ValueError("dev_user authentication requires development/testing")` nếu đặt `dev_user` khi `ENVIRONMENT` là `staging`/`production`. |
| `CORS_ALLOWED_ORIGINS` | `["https://<STAGING_WEB_ORIGIN>"]` | JSON list các HTTP(S) origin chính xác (`scheme://host[:port]`). Cấm wildcard (`*`), path, query, fragment, trailing slash, userinfo (`user@host`), khoảng trắng, ký tự `%` hoặc `\`. Mặc định khi bỏ trống ở staging/production là `[]` (chặn mọi cross-origin). |
| `SECRET_KEY` | `<32+ random bytes>` (`<REDACTED>`) | Tối thiểu **32 bytes UTF-8**, phải khác `DEFAULT_SECRET_KEY` (`"change-this-to-a-super-secret-jwt-key"`). Kiểm tra ngay khi khởi tạo `Settings` qua `validate_secret_configuration()`. |
| `JWT_SECRET_KEY` | `<32+ random bytes>` (`<REDACTED>`) | Tối thiểu **32 bytes UTF-8**, **tách biệt hoàn toàn khỏi `SECRET_KEY`** (`JWT_SECRET_KEY != SECRET_KEY`), và không chứa chuỗi mẫu (`change`, `example`, `your-`, `placeholder`). Được kiểm tra bởi `validate_app_signing_configuration()`. |
| `JWT_ISSUER` | `proplan-va` | Chuỗi không rỗng, khớp `iss` khi ký và giải mã app access/refresh JWT (`HS256`). |
| `JWT_AUDIENCE` | `proplan-va-api` | Chuỗi không rỗng, khớp `aud` khi ký và giải mã app access/refresh JWT. *(Có thể kèm `ACCESS_TOKEN_TTL_SECONDS=3600`, `REFRESH_TOKEN_TTL_SECONDS=2592000`, `AUTH_CLOCK_SKEW_SECONDS=0..30`).* |
| `CALENDAR_CREDENTIAL_SECRET_KEY` | `<REDACTED>` *(Fernet 32-byte URL-safe base64, 44 ký tự)* | Khóa Fernet hợp lệ (`base64.urlsafe_b64encode(os.urandom(32))`), **tách biệt hoàn toàn khỏi `SECRET_KEY` & `JWT_SECRET_KEY`**. Trên staging/production bắt buộc hợp lệ qua `calendar_credential_key()` (không được phép fallback sang `SECRET_KEY`). |
| `GOOGLE_SIGN_IN_AUDIENCES` | `["<REDACTED>.apps.googleusercontent.com"]` | JSON list tối thiểu 1 OAuth Client ID thật kết thúc bằng `.apps.googleusercontent.com`; cấm rỗng hoặc chứa từ khóa mẫu (`your-`, `example`, `placeholder`). |
| `DATABASE_URL` | `postgresql+asyncpg://<USER>:<REDACTED>@<HOST>:<PORT>/<DB_NAME>?ssl=require` | PostgreSQL 16+ bật **TLS**; trước khi chạy app phải nâng Alembic tới head (`20261118_01`, gồm bảng `refresh_tokens`). Chạy **1 worker Uvicorn duy nhất** (theo `Dockerfile`) để tránh nhân bản `_calendar_sync_background_loop`. |
| `CALENDAR_PROVIDER_MODE` | `real` *(hoặc `fake` nếu staging không nối Google thật)* | Khi đặt `real`, bắt buộc cấu hình đủ cặp OAuth Client ID/Secret thật. Lưu ý: nếu thiếu Client ID/Secret, app không ném `ValueError` fail-fast lúc khởi động mà `settings.is_calendar_real_mode` trả `False` và tự động rơi về `FakeCalendarClient`. |
| `GOOGLE_OAUTH_CLIENT_ID` | `<REDACTED>.apps.googleusercontent.com` | OAuth 2.0 Client ID trên Google Cloud Console; không dùng giá trị mẫu `.env.example`. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `<REDACTED>` | OAuth 2.0 Client Secret trên Google Cloud Console; ẩn khỏi log/repr. |
| `GOOGLE_OAUTH_REDIRECT_URI` | `https://<STAGING_API_HOST>/api/v1/calendar/oauth/callback` | URL HTTPS callback khớp chính xác Authorized Redirect URI trên Google Cloud Console. Mặc định trong `config.py#L91` là `http://localhost:8000/api/v1/calendar/oauth/callback` cho local dev và không có startup guard chặn `localhost`, nên khi deploy lên server bắt buộc ghi đè qua biến môi trường này. |
| `GEMINI_API_KEY` *(hoặc `GOOGLE_CREDENTIALS_PATH`)* | `<REDACTED>` *(hoặc đường dẫn JSON Service Account + `GOOGLE_CLOUD_PROJECT`)* | Tối thiểu có 1 phương thức xác thực Gemini hợp lệ (`has_gemini_auth == True`). |
| `GROQ_API_KEY` | `<REDACTED>` | Khóa Groq Cloud phục vụ fallback LLM (`qwen/qwen3.8-27b` / `openai/gpt-oss-20b`) và Whisper STT (`whisper-large-v3-turbo`). |
| Cấu hình AI/Voice/TZ mặc định | `GEMINI_FLASH_MODEL="gemini-3.5-flash-lite"`, `VOICE_STT_PROVIDER="auto"`, `DEFAULT_TIMEZONE="Asia/Ho_Chi_Minh"` | **Giữ nguyên mặc định** theo cấu hình hiện hành (`GEMINI_BACKUP_MODEL="gemini-3.5-flash-lite"`, `GEMINI_UF1_MODEL="gemini-3.5-flash-lite"`, `GEMINI_STT_MODEL="gemini-3.5-flash-lite"`, `VOICE_STT_UF5_PREFER_GROQ=true`). |

---

## 2. Checklist kiểm chứng hành vi bảo mật trước khi ký đạt

Thay `<STAGING_BASE_URL>` bằng URL staging thật (ví dụ `https://virtual-assistant-for-adhd.onrender.com`) và `<ALLOWED_ORIGIN>` bằng origin đã khai báo trong `CORS_ALLOWED_ORIGINS`.

### 2.1. `BE-02` — CORS Origin Hardening

- [ ] **Chặn origin lạ trên preflight `OPTIONS /api/v1/auth/me`:**
  ```bash
  curl -i -sS -X OPTIONS "<STAGING_BASE_URL>/api/v1/auth/me" \
    -H "Origin: https://evil.example" \
    -H "Access-Control-Request-Method: GET"
  ```
  - **Kỳ vọng:** HTTP `400` (`Disallowed CORS origin`), **không có** header `access-control-allow-origin` và **không có** `access-control-allow-credentials: true`.
- [ ] **Không phản chiếu origin lạ hoặc trick domain/userinfo trên `GET`:**
  ```bash
  curl -i -sS "<STAGING_BASE_URL>/api/v1/health" -H "Origin: https://evil.example"
  curl -i -sS "<STAGING_BASE_URL>/api/v1/health" -H "Origin: http://localhost:8080@evil.com"
  ```
  - **Kỳ vọng:** Response không chứa `access-control-allow-origin`.
- [ ] **Cho phép đúng origin nằm trong allowlist `CORS_ALLOWED_ORIGINS`:**
  ```bash
  curl -i -sS -X OPTIONS "<STAGING_BASE_URL>/api/v1/auth/me" \
    -H "Origin: <ALLOWED_ORIGIN>" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: Authorization,Content-Type"
  ```
  - **Kỳ vọng:** HTTP `200`, trả chính xác `access-control-allow-origin: <ALLOWED_ORIGIN>`, `access-control-allow-credentials: true`, `access-control-expose-headers: X-Request-ID`.

### 2.2. `BE-05` — OAuth Redirect Allowlist & Fail-fast Secrets

- [ ] **Whitelist `_allowed_return_target` (`app/api/v1/calendar.py`):**
  - Chỉ chấp nhận deep link `proplan://calendar/connected` (có thể kèm query sạch, không ký tự điều khiển/CRLF/`\`/trailing `/`/userinfo) hoặc `<browser_origin>/#/google-calendar` khớp đúng một phần tử trong `settings.browser_origins`.
  - Khi kiểm tra cả `/api/v1/calendar/oauth/callback` và `/api/v1/calendar/oauth2callback` (cả nhánh thành công có `code` lẫn nhánh từ chối `error=access_denied`) với `return_target` giả mạo (ví dụ `https://evil.example`, `//evil.com`, `proplan://calendar/connected%0d%0a...`), HTTP `302` `Location` **luôn ép fallback** về `proplan://calendar/connected?...` và tuyệt đối không chứa host lạ.
- [ ] **Startup Guard fail-fast khi cấu hình lỗi:**
  - Chạy smoke kiểm thử khởi động trong process/container tách biệt với `ENVIRONMENT=staging`:
    - `AUTH_MODE=dev_user` $\rightarrow$ fail-fast `ValueError` tại `validate_auth_configuration()`.
    - `SECRET_KEY` mặc định hoặc `< 32` bytes $\rightarrow$ fail-fast `ValueError` tại `validate_secret_configuration()`.
    - `CALENDAR_CREDENTIAL_SECRET_KEY` rỗng hoặc không đúng định dạng Fernet 32-byte base64 $\rightarrow$ fail-fast `ValueError` tại `calendar_credential_key()`.
    - `JWT_SECRET_KEY` rỗng, `< 32` bytes, trùng `SECRET_KEY`, hoặc chứa `placeholder`/`your-`/`change`/`example` $\rightarrow$ fail-fast `ValueError` tại `validate_app_signing_configuration()`.
    - `GOOGLE_SIGN_IN_AUDIENCES` rỗng hoặc không phải client ID `.apps.googleusercontent.com` thật $\rightarrow$ fail-fast `ValueError`.
- [ ] **Log Redaction (`OAuthLogRedactionFilter`):**
  - Kiểm tra access log không lộ các tham số nhạy cảm (`code=`, `state=`, `token=`, `access_token=`, `refresh_token=`, `id_token=`, `client_secret=`, `password=`, `key=`) — tất cả phải bị che thành `<REDACTED>`.

### 2.3. `BE-03` — Auth Rotation/Logout, ASGI Security Headers & Health/Metrics

- [ ] **ASGI Security Headers mặc định trên mọi HTTP response (`SecurityHeadersMiddleware` & `RequestIdMiddleware`):**
  ```bash
  curl -i -sS "<STAGING_BASE_URL>/api/v1/health"
  ```
  - **Kỳ vọng:** Luôn có đủ:
    - `x-content-type-options: nosniff`
    - `x-frame-options: DENY`
    - `referrer-policy: no-referrer`
    - `x-request-id: ...`
- [ ] **`Cache-Control: no-store` trên toàn bộ nhánh Auth & Calendar OAuth:**
  - Kiểm tra trên `/api/v1/auth/*` (`GET /api/v1/auth/me` dù `401` hay `200`, `POST /api/v1/auth/google`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`) và `/api/v1/calendar/oauth*` (`/api/v1/calendar/oauth/callback`, `/api/v1/calendar/oauth2callback`):
  ```bash
  curl -i -sS "<STAGING_BASE_URL>/api/v1/auth/me"
  curl -i -sS "<STAGING_BASE_URL>/api/v1/calendar/oauth/callback?state=invalid-smoke"
  ```
  - **Kỳ vọng:** Header `cache-control: no-store` xuất hiện cùng `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`. Và `GET /api/v1/auth/me` không có Bearer token phải trả `401 UNAUTHENTICATED` + `www-authenticate: Bearer` (tuyệt đối không fallback sang `user_a`).
- [ ] **`POST /api/v1/auth/refresh` — Rotation & Reuse Detection:**
  - Gọi `POST /api/v1/auth/refresh` với `{"refresh_token": "<REFRESH_1>"}` hợp lệ $\rightarrow$ `200` trả về cặp `access_token` và `refresh_token` mới (`<REFRESH_2>`, `jti` khác, cùng `family_id`); `<REFRESH_1>` được đánh dấu `revoked_reason="rotated"`.
  - Gọi lại `POST /api/v1/auth/refresh` bằng `<REFRESH_1>` đã dùng (mô phỏng replay/reuse) $\rightarrow$ **bắt buộc trả `401 UNAUTHENTICATED`** (`www-authenticate: Bearer`), đồng thời thu hồi toàn bộ token đang hoạt động trong `family_id` (`revoked_reason="reuse_detected"`).
  - Gọi tiếp `POST /api/v1/auth/refresh` bằng `<REFRESH_2>` thuộc cùng `family_id` sau khi đã phát hiện reuse $\rightarrow$ **bắt buộc trả `401 UNAUTHENTICATED`**.
- [ ] **`POST /api/v1/auth/logout` — Public & Idempotent `200`:**
  - Gọi `POST /api/v1/auth/logout` với `{"refresh_token": "<REFRESH_ACTIVE>"}` (không cần `Authorization: Bearer`) $\rightarrow$ `200` với `data: {"logged_out": true, "revoked": true}`.
  - Gọi lại lần 2 với cùng `<REFRESH_ACTIVE>` (hoặc token đã rotated/revoked trước đó thuộc DB) $\rightarrow$ `200` idempotent với `data: {"logged_out": true, "revoked": false}`.
  - Gọi `POST /api/v1/auth/refresh` bằng token của phiên vừa logout $\rightarrow$ `401 UNAUTHENTICATED`.
- [ ] **Health & Prometheus Metrics:**
  ```bash
  curl -i -sS "<STAGING_BASE_URL>/api/v1/health"
  curl -i -sS "<STAGING_BASE_URL>/metrics"
  curl -i -sS "<STAGING_BASE_URL>/api/v1/metrics"
  ```
  - **Kỳ vọng:**
    - `GET /api/v1/health` (và `/health`): HTTP `200`, JSON `{"status":"ok","database":"ok"}`.
    - `GET /api/v1/metrics` (và `/metrics`): HTTP `200`, `content-type: text/plain; version=0.0.4; charset=utf-8`, chứa các metric HTTP/DB/Outbox/AI mà không chứa thông tin nhạy cảm.

---

## 3. Bảng ký nghiệm thu `W4-06` trên môi trường Staging thật

*(Dành cho `tn` / `mt` điền sau khi triển khai và chạy smoke trực tiếp trên staging thật.)*

| Hạng mục kiểm chứng | Lệnh / Endpoint đối soát | Ngày kiểm | Người kiểm (`tn`/`mt`) | Kết quả (`PASS`/`FAIL`) | Ghi chú / SHA / Alembic head |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Bộ biến môi trường & Startup Guard (`ENVIRONMENT=staging`, `AUTH_MODE=google`, secret riêng $\ge 32$B, Fernet key) | Render / Staging Env + startup logs | | | | |
| PostgreSQL TLS & Alembic migration `20261118_01` + single-worker Uvicorn | `alembic current` + `GET /api/v1/health` | | | | |
| `BE-02` CORS allowlist & chặn origin giả mạo | `OPTIONS /api/v1/auth/me`, `GET /api/v1/health` | | | | |
| `BE-05` OAuth callback `_allowed_return_target` whitelist & log redaction | `GET /api/v1/calendar/oauth/callback` & `/oauth2callback` | | | | |
| `BE-03` Security headers & `Cache-Control: no-store` trên `/api/v1/auth/*`, `/api/v1/calendar/oauth*` | `curl -i` các endpoint Auth & OAuth | | | | |
| `BE-03` Google Login + Refresh rotation + Reuse detection `401` | `POST /api/v1/auth/google`, `POST /api/v1/auth/refresh` | | | | |
| `BE-03` Logout public idempotent `200` (`revoked: true` $\rightarrow$ `false`) | `POST /api/v1/auth/logout` | | | | |
| Prometheus Metrics & Health probe | `GET /api/v1/health`, `GET /metrics`, `GET /api/v1/metrics` | | | | |
