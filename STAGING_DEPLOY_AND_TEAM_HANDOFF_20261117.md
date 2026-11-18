# Sơ đồ Hạ tầng Deploy Staging & Hướng dẫn Bàn giao Nhóm (ProPlan VA)

**URL Staging Chính thức:** `https://virtual-assistant-for-adhd.onrender.com`  
**API Base URL (cho Flutter):** `https://virtual-assistant-for-adhd.onrender.com/api/v1`  
**Swagger UI:** `https://virtual-assistant-for-adhd.onrender.com/docs`  
**Health Check:** `https://virtual-assistant-for-adhd.onrender.com/health` (đã bật UptimeRobot ping mỗi 5 phút)

---

## 1. Sơ đồ Tổng thể Hạ tầng Deploy (Deployment Topology)

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         1. THIẾT BỊ NGƯỜI DÙNG & NGUỒN GỌI                        │
│                                                                                   │
│  ┌──────────────────────────────┐   ┌───────────────────────┐   ┌──────────────┐  │
│  │ Flutter App (Web / Android)  │   │ Trình duyệt Người dùng│   │ UptimeRobot  │  │
│  │ • Web: http://localhost:8080 │   │ • Màn hình bấm        │   │ • Ping mỗi   │  │
│  │ • Mobile: Android APK        │   │   "Cho phép" Google   │   │   5 phút     │  │
│  └──────────────┬───────────────┘   └───────────┬───────────┘   └──────┬───────┘  │
└─────────────────┼───────────────────────────────┼──────────────────────┼──────────┘
                  │ HTTPS                         │ Redirect             │ GET /health
                  ▼ (/api/v1/*)                   ▼ (/oauth/callback)    ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                    2. RENDER CLOUD (Region: Singapore - Free)                     │
│                                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────────┐  │
│  │ WEB SERVICE (Docker Container từ nhánh GitHub `dev`, thư mục `backend/`)    │  │
│  │ Public URL: https://virtual-assistant-for-adhd.onrender.com                 │  │
│  │                                                                             │  │
│  │  [Khi bật container]: chạy `alembic upgrade head` ──► tạo/cập nhật 11 bảng  │  │
│  │  [Khi chạy]:          `uvicorn app.main:app` (1 worker)                     │  │
│  │                        + `_calendar_sync_background_loop` (quét mỗi 5 giây) │  │
│  └──────────────────────────────────────┬──────────────────────────────────────┘  │
│                                         │                                         │
│                                         │ Mạng nội bộ Render (Private Network)    │
│                                         │ DATABASE_URL (postgresql+asyncpg://...) │
│                                         ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────┐  │
│  │ RENDER POSTGRES 16 (`proplan-staging-db`)                                   │  │
│  │ • Lưu Users, Preferences, Drafts, Tasks, Steps, Sessions, Proposals         │  │
│  │ • Lưu Google Calendar Token đã mã hóa bằng CALENDAR_CREDENTIAL_SECRET_KEY   │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
└─────────────────┬───────────────────────────────┬──────────────────────┬──────────┘
                  │                               │                      │
      Verify ID   │                   Đọc lịch bận│                      │ Gọi AI &
        Token     │                   & Ghi phiên │                      │ Voice
                  ▼                               ▼                      ▼
┌───────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────┐
│ 3A. GOOGLE IDENTITY       │   │ 3B. GOOGLE CALENDAR API   │   │ 3C. AI & VOICE    │
│ • Kiểm tra chữ ký Google  │   │ • Đọc sự kiện bận (UF1)   │   │ • Gemini 3.5      │
│ • Đối chiếu Client ID với │   │ • Ghi/Sửa/Xóa các phiên   │   │ • Groq Qwen/      │
│   GOOGLE_SIGN_IN_AUDIENCES│   │   Focus Session (UF2/UF6) │   │   Whisper STT     │
└───────────────────────────┘   └───────────────────────────┘   │ • Edge-TTS        │
                                                                └───────────────────┘
```

---

## 2. Giải thích Chi tiết Từng Luồng Gọi

### 2.1. Luồng Khởi tạo & Chống ngủ (Build $\rightarrow$ Migrate $\rightarrow$ Uptime)
1. **Build Docker:** Render đọc [Dockerfile](file:///D:/HCMUS/CT/ProActive/backend/Dockerfile) trong thư mục `backend/` trên nhánh `dev`, cài đặt môi trường Python 3.12 theo `uv.lock`.
2. **Tự động tạo bảng (`alembic upgrade head`):** Trước khi mở cổng nhận request, container tự kết nối vào `proplan-staging-db` qua `DATABASE_URL` nội bộ (`postgresql+asyncpg://...`) và chạy toàn bộ 11 bản migration để tạo đủ bảng, khóa ngoại và index.
3. **Chạy Server & Worker ngầm:** `uvicorn app.main:app` chạy với **1 worker duy nhất** kèm vòng lặp ngầm `_calendar_sync_background_loop` (quét các job đồng bộ lịch mỗi 5 giây).
4. **Giữ thức 24/7:** UptimeRobot gọi `GET /health` mỗi 5 phút để kiểm tra kết nối PostgreSQL và giữ gói Render Free không bị ngủ đông sau 15 phút rảnh.

### 2.2. Luồng A — Đăng nhập App bằng Google (`AUTH_MODE=google`)
1. **Trên thiết bị người dùng:** App Flutter (Web tại `http://localhost:8080` hoặc Android APK) mở hộp thoại đăng nhập Google.
   - Trên Web, Google kiểm tra địa chỉ trang web có nằm trong mục **`Authorized JavaScript origins`** (`http://localhost:8080`) trên Google Cloud Console hay không.
   - Trên Android APK, Google Play Services kiểm tra cặp **`Package Name` (`com.proplan.proplan_va`) + mã `SHA-1`** của người xuất file APK, và cấp `id_token` gắn với `serverClientId` (chính là Web Client ID).
2. **Gửi lên Render:** App gọi `POST /api/v1/auth/google` kèm `id_token`.
3. **Xác thực & Cấp JWT:** Render xác minh chữ ký của Google, kiểm tra mã Client ID trong `id_token` có nằm trong danh sách `GOOGLE_SIGN_IN_AUDIENCES` không, tự động tạo/cập nhật bản ghi `User` trong PostgreSQL, rồi dùng `JWT_SECRET_KEY` ký cặp `access_token` + `refresh_token` trả về cho App.

### 2.3. Luồng B — Kết nối & Đồng bộ Google Calendar 2 chiều (`CALENDAR_PROVIDER_MODE=real`)
1. **Lấy link cấp quyền:** App gọi `GET /api/v1/calendar/oauth/auth-url`. Render ghép `GOOGLE_OAUTH_CLIENT_ID` và `GOOGLE_OAUTH_REDIRECT_URI` (`https://virtual-assistant-for-adhd.onrender.com/api/v1/calendar/oauth/callback`) thành đường dẫn Google OAuth.
2. **Người dùng bấm "Cho phép":** Google kiểm tra đường dẫn callback có khớp với mục **`Authorized redirect URIs`** trên Google Cloud Console không, sau đó chuyển hướng (redirect) trình duyệt thẳng về `https://virtual-assistant-for-adhd.onrender.com/api/v1/calendar/oauth/callback?code=...`.
3. **Mã hóa & Lưu trữ an toàn:** Render dùng `GOOGLE_OAUTH_CLIENT_SECRET` đổi `code` lấy `refresh_token` của Google Calendar, dùng chìa khóa **`CALENDAR_CREDENTIAL_SECRET_KEY`** (chuẩn Fernet) mã hóa thành chuỗi `fernet:v1:...` rồi lưu vào `proplan-staging-db`.
4. **Đồng bộ tự động:** Ở UF1 hệ thống đọc các khung giờ bận từ lịch chính; sau khi người dùng bấm Xác nhận lịch ở UF2/UF6, `CalendarSyncWorker` tự đẩy/cập nhật các phiên Focus lên Google Calendar.

### 2.4. Luồng C — Tạo việc bằng Giọng nói / Text & Lập lịch (UF1 $\rightarrow$ UF6)
1. App gửi âm thanh (`.m4a`) hoặc văn bản lên Render (`/api/v1/voice/transcribe`, `/api/v1/drafts/{id}/analyze`).
2. Render gọi **Gemini 3.5 Flash-Lite** (`GEMINI_API_KEY`) hoặc tự động chuyển sang **Groq** (`GROQ_API_KEY` — `qwen/qwen3.8-27b` / `whisper-large-v3-turbo`) để bóc tách 4 trường UF1 và gợi ý chia bước UF2.
3. Thuật toán Python (`scheduler_engine.py`) tính toán 100% các phép toán thời gian, hệ số đệm $k_{\text{buffer}}$ và kiểm tra thiếu hụt $\Delta T_{\text{deficit}}$.
4. Cuối phiên (UF5), Render gọi `Edge-TTS` sinh giọng đọc tiếng Việt (hoặc trả cờ `fallback_to_device_tts = true` để điện thoại tự đọc bằng `flutter_tts`).

---

## 3. Lưu ý Kỹ thuật Quan trọng: Chạy Flutter Web & Xuất 1 File APK Dùng Chung

### 3.1. Bẫy chạy Flutter Web lệch cổng `8080` (`redirect_uri_mismatch` / `origin_mismatch`)
- **Nguyên nhân:** Trên Google Cloud Console mục `Authorized JavaScript origins`, hệ thống đang mở cho `http://localhost:8080`. Nếu thành viên chạy `flutter run -d chrome` mà không khóa cổng, Flutter sẽ mở cổng ngẫu nhiên (như `localhost:53124`) và bị Google chặn đăng nhập.
- **Cách chạy đúng trên Web:** Bắt buộc khóa cổng `--web-port 8080` và truyền đủ `API_BASE_URL` (có `/api/v1` ở cuối theo [api_config.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/core/api_config.dart#L15-L17)) cùng `GOOGLE_CLIENT_ID` (theo [auth_service.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/services/auth_service.dart#L13)):
  ```bash
  flutter run -d chrome --web-port 8080 \
    --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 \
    --dart-define=GOOGLE_CLIENT_ID=<MÃ_GOOGLE_OAUTH_CLIENT_ID_72_KÝ_TỰ>
  ```

### 3.2. Phương án Cả Nhóm Cài Chung 1 File APK (Nhàn nhất) — Mã nào trong `.env` và mã nào trên máy?
1. **Đoạn mã lấy từ `backend/.env` của bạn:**
   - Chính là giá trị của biến **`GOOGLE_OAUTH_CLIENT_ID`** (chuỗi 72 ký tự có đuôi `.apps.googleusercontent.com`).
   - Khi xuất APK cho Android, theo [auth_service.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/services/auth_service.dart#L14-L15), biến truyền vào có tên là **`GOOGLE_SERVER_CLIENT_ID`** và nhận **chính giá trị `GOOGLE_OAUTH_CLIENT_ID` đó**:
     ```bash
     flutter build apk --debug \
       --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 \
       --dart-define=GOOGLE_SERVER_CLIENT_ID=<MÃ_GOOGLE_OAUTH_CLIENT_ID_72_KÝ_TỰ>
     ```
2. **Còn mã `SHA-1` (KHÔNG nằm trong `.env`):**
   - `SHA-1` là dấu vân tay chữ ký file APK nằm trên **máy tính của người chạy lệnh `flutter build apk`** (`~/.android/debug.keystore`).
   - Trên máy tính sẽ đứng ra build APK cho cả nhóm, mở PowerShell chạy lệnh này để lấy `SHA-1`:
     ```powershell
     keytool -list -v -keystore "$env:USERPROFILE\.android\debug.keystore" -alias androiddebugkey -storepass android -keypass android
     ```
   - Copy dòng `SHA1: AA:BB:CC:...` vừa hiện ra $\rightarrow$ vào **Google Cloud Console $\rightarrow$ Credentials $\rightarrow$ `+ CREATE CREDENTIALS` $\rightarrow$ OAuth client ID**:
     - **Application type:** `Android`
     - **Package name:** `com.proplan.proplan_va` *(khớp với `mobile/android/app/build.gradle.kts`)*
     - **SHA-1 certificate fingerprint:** dán mã `SHA1` vừa lấy.
   - Bấm **Create** 1 lần duy nhất. Từ đó, file `.apk` xuất từ máy này gửi qua Zalo/Drive cho cả nhóm cài lên bất kỳ điện thoại Android nào cũng đều đăng nhập Google được ngay.
3. **Sau khi bấm Create mã `Android` trên Google Cloud Console xong có cần thêm vào đâu nữa không? $\rightarrow$ KHÔNG CẦN:**
   - **Không cần copy** mã Client ID loại `Android` mới sinh đó đi đâu cả (Google Play Services trên điện thoại tự đối chiếu ngầm bằng `Package name + SHA-1`).
   - **Trên Render giữ nguyên 100%**, không cần sửa `GOOGLE_SIGN_IN_AUDIENCES` (vì khi build APK đã truyền `GOOGLE_SERVER_CLIENT_ID` là mã `GOOGLE_OAUTH_CLIENT_ID` 72 ký tự loại Web, khớp sẵn với Render).
   - **Không cần build lại APK** dù bạn thêm `SHA-1` lên Google Cloud trước hay sau khi chạy lệnh `flutter build apk` (vì `SHA-1` của máy là cố định).

---

## 4. Mẫu Tin Nhắn Bàn Giao Gửi Cho Cả Nhóm (Copy gửi nhóm)

```text
Mọi người ơi, mình đã dựng xong Server Backend + PostgreSQL 16 + Google Auth/Calendar + AI trên Cloud (đã bật UptimeRobot giữ server thức 24/7). Mọi người kết nối theo thông tin dưới đây nhé:

1. THÔNG TIN SERVER STAGING:
- API Base URL (nhớ có /api/v1 ở cuối): https://virtual-assistant-for-adhd.onrender.com/api/v1
- Swagger UI (xem/test API): https://virtual-assistant-for-adhd.onrender.com/docs
- Health Check: https://virtual-assistant-for-adhd.onrender.com/health
- Google OAuth Web Client ID: <DÁN_MÃ_GOOGLE_OAUTH_CLIENT_ID_72_KÝ_TỰ_CỦA_BẠN_VÀO_ĐÂY>

2. QUAN TRỌNG — GỬI GMAIL ĐỂ MỞ QUYỀN ĐĂNG NHẬP GOOGLE (Tránh lỗi Error 403: access_denied):
- Do app trên Google Cloud Console đang ở chế độ Testing, chỉ các Gmail nằm trong danh sách "Test users" mới bấm Đăng nhập Google và nối Google Calendar được.
- Mọi người nhắn giúp mình địa chỉ Gmail dùng để test để mình thêm hết vào mục OAuth consent screen -> Test users trên Google Cloud trước khi bấm đăng nhập nhé!

3. LỆNH CHẠY KHI TEST TRÊN FLUTTER WEB (Bắt buộc khóa cổng 8080):
- Trên Google Cloud chỉ mở Authorized JavaScript origins cho cổng 8080. Khi chạy Web mọi người bắt buộc thêm `--web-port 8080`:
flutter run -d chrome --web-port 8080 --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 --dart-define=GOOGLE_CLIENT_ID=<MÃ_GOOGLE_OAUTH_CLIENT_ID>

4. KHI TEST TRÊN ĐIỆN THOẠI ANDROID (Dùng chung 1 file APK):
- Khi xuất APK dùng lệnh:
flutter build apk --debug --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 --dart-define=GOOGLE_SERVER_CLIENT_ID=<MÃ_GOOGLE_OAUTH_CLIENT_ID>
- Mình sẽ đăng ký mã SHA-1 của máy build APK lên Google Cloud và xuất 1 file APK chuẩn gửi cho cả nhóm cài chung để ai cũng đăng nhập Google trên điện thoại được ngay.

5. LƯU Ý KHI DÙNG CHUNG SERVER:
- Server đang dùng chung API Key Gemini/Groq thật và Google Calendar thật, nên mọi người chỉ thao tác test luồng trên App/UI như người dùng thật, KHÔNG chạy pytest hoặc script spam tự động vào link onrender.com để tránh cạn quota AI nhé!
- Mỗi khi nhánh `dev` vừa merge thêm PR mới của Backend, nhắn mình một tiếng để mình bấm Manual Deploy trên Render cập nhật code mới.
```


---

## 5. Giới hạn Dung lượng & Tài nguyên của Server Render (Gói Free)

### 5.1. Giới hạn của Render Web Service (Gói Free)

| Hạng mục | Giới hạn gói Free | Đánh giá thực tế với ProPlan VA |
| :--- | :--- | :--- |
| **RAM & CPU** | **512 MB RAM** (`0.1 CPU`) | **Dư dả (~100–150 MB).** [Dockerfile](file:///D:/HCMUS/CT/ProActive/backend/Dockerfile) chỉ chạy **1 worker `uvicorn`**, các model AI nặng (Gemini, Groq Whisper) chạy trên cloud của Google/Groq chứ không nạp vào RAM của Render, và cache TTS khóa ở tối đa 64 câu ngắn ([config.py:L115](file:///D:/HCMUS/CT/ProActive/backend/app/core/config.py#L115)). |
| **Ổ đĩa Container** | **Tạm thời (*Ephemeral*)** — tự xóa sạch khi redeploy/restart | **Không ảnh hưởng.** Toàn bộ dữ liệu lưu ở PostgreSQL riêng; file ghi âm UF4 lưu trên điện thoại; còn file audio gửi lên STT (`/voice/transcribe`) xử lý xong là xóa ngay. |
| **Kích thước 1 file Audio gửi lên** | **Tối đa 10 MB / file** | Do biến `VOICE_MAX_AUDIO_BYTES = 10 MB` trong [config.py:L108](file:///D:/HCMUS/CT/ProActive/backend/app/core/config.py#L108) khóa sẵn (1 đoạn nói 1–2 phút định dạng `.m4a` chỉ nặng ~0.5–2 MB). |
| **Tổng giờ chạy miễn phí** | **750 giờ / tháng** | **Đủ chạy 24/7 cả tháng** (1 tháng 31 ngày = `744 giờ`). *Lưu ý: chỉ để 1 Web Service Free chạy 24/7 với UptimeRobot trên tài khoản này; nếu tạo thêm cái thứ 2 cùng bật 24/7 thì tổng giờ 2 cái mới vượt 750h.* |
| **Băng thông mạng** | **100 GB / tháng** | Quá dư cho nhóm 8 người test JSON và voice ngắn. |

### 5.2. Giới hạn của Render PostgreSQL (`proplan-staging-db` — Gói Free)

| Hạng mục | Giới hạn gói Free | Đánh giá thực tế với ProPlan VA |
| :--- | :--- | :--- |
| **Dung lượng ổ cứng DB** | **1 GB** | **Không thể đầy.** DB chỉ lưu dữ liệu chữ và JSON lịch biểu (`Users`, `Tasks`, `Steps`, `Sessions`, `Proposals`); tạo vài nghìn công việc cũng chỉ tốn vài chục MB. |
| **Số kết nối đồng thời** | **~97 kết nối** | Web Service chạy 1 worker nên chỉ mở vài kết nối trong pool, không bao giờ nghẽn. |
| **Thời hạn sử dụng** | **30 ngày** từ lúc tạo | Tới ngày `17/12/2026`. Nếu sau ngày đó nhóm vẫn cần dùng miễn phí tiếp, chỉ mất 2 phút tạo 1 Postgres Free mới và đổi lại `DATABASE_URL` (container sẽ tự chạy `alembic upgrade head` dựng lại toàn bộ bảng). |


### 5.3. Giới hạn của Google Calendar API & Google Cloud OAuth

| Hạng mục | Giới hạn mặc định của Google | Ảnh hưởng thực tế tới ProPlan VA |
| :--- | :--- | :--- |
| **Chi phí sử dụng** | **Miễn phí 100% (\$0)** | Google Calendar API không tính tiền theo lượt gọi (dù chạm trần cũng chỉ bị chặn tạm thời `429/403`, không trừ tiền). |
| **Số lượt gọi API / ngày** | **~1,000,000 (1 triệu) requests / ngày** | **Không bao giờ hết.** Worker trong [main.py](file:///D:/HCMUS/CT/ProActive/backend/app/main.py#L21-L33) chỉ quét DB nội bộ mỗi 5 giây và chỉ gọi ra Google Calendar khi có job đồng bộ mới (UF1, UF2, UF6) hoặc bấm Refresh. |
| **Tốc độ gọi / phút** | **Hàng trăm requests / phút / user** | Thao tác người dùng thật trên App không thể vượt ngưỡng này. |
| **Số lượng tài khoản Gmail test (`Test users`)** | **Tối đa 100 Gmail** *(khi OAuth để chế độ `Testing`)* | Nhóm 8 người + giảng viên (~10 Gmail) hoàn toàn dư dả. |
| **Thời hạn Token Lịch (`refresh_token`)** | **Tự hết hạn sau 7 ngày** *(quy định bắt buộc của Google cho app ở chế độ `Testing`)* | **Lưu ý trước khi demo:** Sau 7 ngày kể từ lúc nối lịch, token lịch sẽ hết hạn; chỉ cần bấm **"Kết nối lại Google Calendar"** trên App (5 giây) là tự cấp mới thêm 7 ngày. |

### 5.4. Cơ chế `CALENDAR_CREDENTIAL_SECRET_KEY` (1 Khóa Chủ Server vs Token Riêng Từng Người)
- **`CALENDAR_CREDENTIAL_SECRET_KEY` là 1 khóa chủ duy nhất trên Server Render** (đã cấu hình sẵn 1 lần trên Render; các thành viên cài App/Web **không cần biết và không phải điền biến này**).
- **Phần riêng của từng người được sinh tự động 100%:** Khi mỗi thành viên bấm *"Kết nối Google Calendar"*, Google tự cấp `refresh_token` riêng cho Gmail của người đó $\rightarrow$ Backend ([credential_crypto.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/credential_crypto.py#L42-L60)) tự động đóng gói `refresh_token` kèm `user_id` + `connection_id` của riêng người đó rồi dùng `CALENDAR_CREDENTIAL_SECRET_KEY` mã hóa thành một chuỗi `fernet:v1:...` riêng biệt lưu vào PostgreSQL (người A không thể dùng hoặc tráo chuỗi mã hóa của người B).
