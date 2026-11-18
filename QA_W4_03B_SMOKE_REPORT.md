# BÁO CÁO HẠ TẦNG STAGING & NGHIỆM THU KIỂM THỬ SMOKE TEST TOÀN TRÌNH (W4-03A + W4-03B)

**Dự án:** ProPlan VA — Virtual Assistant for Adults with ADHD-I  
**Người thực hiện:**  
- **Tín Nhiệm (`tn`):** Phụ trách Hạ tầng Deploy Staging, Render Cloud & Database Seed (`W4-03A`)  
- **Doãn Thành (`thành`):** Phụ trách QA Lead / Tester / Algorithm Lead & Smoke Test (`W4-03B`)  
**Ngày thực hiện:** 18/11/2026  
**URL Staging Chính thức:** `https://virtual-assistant-for-adhd.onrender.com`  
**API Base URL (cho Flutter):** `https://virtual-assistant-for-adhd.onrender.com/api/v1`  
**Swagger UI:** `https://virtual-assistant-for-adhd.onrender.com/docs`  
**Health Check:** `https://virtual-assistant-for-adhd.onrender.com/health` (đã bật UptimeRobot ping mỗi 5 phút)  
**Trạng thái nhánh `dev`:** Commit `6721f64` (Đã đồng bộ từ `origin/dev@4ae869e` và tích hợp 44 tests UF5/UF6)  

---

# 🌐 PHẦN I: SƠ ĐỒ HẠ TẦNG DEPLOY STAGING & BÀN GIAO NHÓM (W4-03A — TÍN NHIỆM)

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
1. **Build Docker:** Render đọc [Dockerfile](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/backend/Dockerfile) trong thư mục `backend/` trên nhánh `dev`, cài đặt môi trường Python 3.12 theo `uv.lock`.
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

## 3. Hướng dẫn Kỹ thuật: Chạy Flutter Web & Build File APK Dùng Chung

### 3.1. Chạy Flutter Web (Bắt buộc Khóa Cổng 8080)
- **Bẫy lỗi:** Trên Google Cloud Console mục `Authorized JavaScript origins`, hệ thống đang mở cho `http://localhost:8080`. Nếu chạy không khóa cổng, Google sẽ chặn với lỗi `origin_mismatch`.
- **Cách chạy đúng:**
  ```bash
  flutter run -d chrome --web-port 8080 \
    --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 \
    --dart-define=GOOGLE_CLIENT_ID=929981556286-e1h39l5kvfcecuma4cbiqt82da5on6n9.apps.googleusercontent.com
  ```

### 3.2. Xuất File APK Cài Đặt Chung Cho Cả Nhóm
1. **Lệnh build APK Android:**
   ```bash
   flutter build apk --debug \
     --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 \
     --dart-define=GOOGLE_SERVER_CLIENT_ID=929981556286-e1h39l5kvfcecuma4cbiqt82da5on6n9.apps.googleusercontent.com
   ```
2. **Đăng ký mã `SHA-1` trên máy người build:**
   - Chạy lệnh lấy mã SHA-1:
     ```powershell
     keytool -list -v -keystore "$env:USERPROFILE\.android\debug.keystore" -alias androiddebugkey -storepass android -keypass android
     ```
   - Copy dòng `SHA1: ...` $\rightarrow$ vào **Google Cloud Console $\rightarrow$ Credentials $\rightarrow$ `+ CREATE CREDENTIALS` $\rightarrow$ OAuth client ID**:
     * **Application type:** `Android`
     * **Package name:** `com.proplan.proplan_va` *(khớp với `mobile/android/app/build.gradle.kts`)*
     * **SHA-1 certificate fingerprint:** Dán mã `SHA1` vừa lấy.
   - Bấm **Create** 1 lần duy nhất. File `.apk` xuất từ máy này gửi cho bất kỳ ai cài trên điện thoại đều đăng nhập Google thành công.

---

## 4. Mẫu Tin Nhắn Bàn Giao Hạ Tầng Cho Cả Nhóm

```text
Mọi người ơi, mình đã dựng xong Server Backend + PostgreSQL 16 + Google Auth/Calendar + AI trên Cloud (đã bật UptimeRobot giữ server thức 24/7). Mọi người kết nối theo thông tin dưới đây nhé:

1. THÔNG TIN SERVER STAGING:
- API Base URL (nhớ có /api/v1 ở cuối): https://virtual-assistant-for-adhd.onrender.com/api/v1
- Swagger UI (xem/test API): https://virtual-assistant-for-adhd.onrender.com/docs
- Health Check: https://virtual-assistant-for-adhd.onrender.com/health
- Google OAuth Web Client ID: 929981556286-e1h39l5kvfcecuma4cbiqt82da5on6n9.apps.googleusercontent.com

2. QUAN TRỌNG — GỬI GMAIL ĐỂ MỞ QUYỀN ĐĂNG NHẬP GOOGLE (Tránh lỗi Error 403: access_denied):
- Do app trên Google Cloud Console đang ở chế độ Testing, chỉ các Gmail nằm trong danh sách "Test users" mới bấm Đăng nhập Google và nối Google Calendar được.
- Mọi người nhắn giúp mình địa chỉ Gmail dùng để test để mình thêm hết vào mục OAuth consent screen -> Test users trên Google Cloud trước khi bấm đăng nhập nhé!

3. LỆNH CHẠY KHI TEST TRÊN FLUTTER WEB (Bắt buộc khóa cổng 8080):
flutter run -d chrome --web-port 8080 --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 --dart-define=GOOGLE_CLIENT_ID=929981556286-e1h39l5kvfcecuma4cbiqt82da5on6n9.apps.googleusercontent.com

4. KHI TEST TRÊN ĐIỆN THOẠI ANDROID (Dùng chung 1 file APK):
flutter build apk --debug --dart-define=API_BASE_URL=https://virtual-assistant-for-adhd.onrender.com/api/v1 --dart-define=GOOGLE_SERVER_CLIENT_ID=929981556286-e1h39l5kvfcecuma4cbiqt82da5on6n9.apps.googleusercontent.com
```

---

## 5. Giới hạn Tài nguyên của Render Cloud & Google API (Gói Free)

| Hạng mục | Giới hạn gói Free | Đánh giá thực tế với ProPlan VA |
| :--- | :--- | :--- |
| **Web Service RAM & CPU** | 512 MB RAM (`0.1 CPU`) | **Dư dả (~100–150 MB).** Chạy 1 worker `uvicorn`, model AI gọi Cloud Google/Groq. |
| **Dung lượng Database** | 1 GB PostgreSQL 16 | **Không thể đầy.** Chỉ lưu text và JSON lịch biểu; tạo vài nghìn task chỉ tốn vài chục MB. |
| **Thời gian hoạt động** | 750 giờ / tháng | **Đủ chạy 24/7 cả tháng** (744 giờ/tháng). UptimeRobot ping 5 phút chống ngủ. |
| **Google Calendar API** | ~1,000,000 req/ngày (\$0) | Worker quét DB nội bộ mỗi 5s, chỉ gọi ra ngoài khi có job mới, không bao giờ cạn quota. |
| **Thời hạn Token Lịch** | Tự hết hạn sau 7 ngày | Quy định bắt buộc của Google ở chế độ Testing. Sau 7 ngày bấm "Kết nối lại" trên app là xong. |

---

# 🧪 PHẦN II: KẾT QUẢ SMOKE TEST & NGHIỆM THU KIỂM THỬ (W4-03B — DOÃN THÀNH)

## 1. Tích hợp Bộ Test UF5/UF6 & Mobile DTO vào `dev`
- Đã đồng bộ nhánh `dev` với `origin/dev@4ae869e`.
- Bổ sung và commit các tập tin kiểm thử chuyên sâu vào `dev`:
  * [`backend/tests/test_uf5_uf6_matrix.py`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/backend/tests/test_uf5_uf6_matrix.py) (26 ca kiểm thử ma trận)
  * [`backend/tests/test_uf5_uf6_edge_cases.py`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/backend/tests/test_uf5_uf6_edge_cases.py) (10 ca kiểm thử kịch bản biên & rủi ro)
  * [`mobile/lib/models/checkin_models.dart`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/mobile/lib/models/checkin_models.dart), [`reschedule_models.dart`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/mobile/lib/models/reschedule_models.dart), [`checkin_screen.dart`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/mobile/lib/screens/checkin_screen.dart) và test suite [`mobile/test/uf5_uf6_feedback_test.dart`](file:///E:/code/Project/CT/Virtual%20Assistant/virtual-assistant-for-ADHD/mobile/test/uf5_uf6_feedback_test.dart).

## 2. Kết quả Chạy Kiểm thử Tự động (44/44 PASSED — 100%)
* **Backend Edge Cases (10/10):** `uv run pytest tests/test_uf5_uf6_edge_cases.py -v` $\rightarrow$ **10 passed in 22.02s**
* **Backend Matrix (26/26):** `uv run pytest tests/test_uf5_uf6_matrix.py` $\rightarrow$ **26 passed in 54.44s**
* **Mobile Flutter (8/8):** `flutter test test/uf5_uf6_feedback_test.dart` $\rightarrow$ **8 passed in 00:01s**

## 3. Smoke Test Hạ tầng Staging Cloud (Render + PostgreSQL 16)
* Kiểm tra endpoint sức khỏe thực tế:
  ```bash
  curl -s https://virtual-assistant-for-adhd.onrender.com/health
  # Trả về: {"status":"ok","database":"ok"}
  ```
  $\rightarrow$ Server Render Staging đang hoạt động ổn định, kết nối PostgreSQL mượt mà và đã được UptimeRobot giữ thức 24/7.

## 4. Xác nhận & Bắt sống 4 Bug Tồn đọng Lớn (Theo Kế hoạch Tuần 4)
1. **`BE-01` (UF1):** Input `"19:00 đến 22:00 hằng ngày"` bị kẹt `AI_AVAILABILITY_INCOMPLETE` dù đã đủ date_overrides trong `draft_service.py:L1431-L1475`.
2. **`MOB-01` (UF7):** `SettingsScreen` gửi `revision` thay vì `effective_revision` ném lỗi `409 REVISION_CONFLICT` sau khi reject UF6.
3. **`MOB-03` (UF6):** Mở app không phát request tới `/proposals/current` khi có proposal `pending_approval`.
4. **`MOB-04` (UF4):** Đồng bộ xóa thought lúc offline / chuyển trang nhanh.

## 5. Bộ Checklist & Lệnh ADB Đo Phần cứng Android Thật (Redmi)
- **Doze Mode (Deep Sleep):** `adb shell dumpsys deviceidle force-idle` để kiểm chứng Exact Alarm reo đúng giây (dung sai $\le 1\text{s}$).
- **Khôi phục DND:** Kiểm tra lệnh ngầm tự động nhả DND chậm nhất tại `ended_at + 300s`.
- **Reboot Recovery:** `adb reboot` để kiểm chứng phục hồi Alarm & State sau khi khởi động lại máy.

---

