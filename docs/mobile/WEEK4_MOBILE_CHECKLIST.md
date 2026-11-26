# Checklist Nghiệm thu Mobile Tuần 4 — ProPlan VA

> **Nhánh nguồn:** `feat/tan-w4-mobile`
> **Commit code Mobile (`W4-15` APK):** `08e2f52` *(đã hợp nhất `origin/feat/kai-w4-native-voice` tại `449039e` và nối đủ điểm §3–§4)*
> **Backend Staging mặc định trong APK:** `https://virtual-assistant-for-adhd.onrender.com/api/v1`
> **Cập nhật lần cuối:** `23/11/2026` (UTC+7)

---

## 1. Thông tin Bản dựng & Định danh Bàn giao (`W4-15`)

| Hạng mục | Giá trị xác nhận |
| --- | --- |
| **Package Name (Android)** | `com.proplan.proplan_va` |
| **File APK Staging** | `mobile/build/app/outputs/flutter-apk/app-debug.apk` |
| **Dung lượng APK** | `205.87 MB` (`215,868,771` bytes, build `23/11/2026 03:20:00`) |
| **APK File `SHA-256`** | `F6C2F7F94873150D4B416AD01B6A338BD610B3879AC5E739656D13C012E6C02E` |
| **Chữ ký Debug `SHA-1` (`apksigner`)** | `D3:22:D4:CB:36:DF:3F:FA:CB:12:9F:F7:D8:90:13:AD:9F:B0:12:F9` |
| **Chữ ký Debug `SHA-256` (`apksigner`)** | `52:15:9D:DB:45:56:D6:2C:16:FC:D2:D1:94:16:F5:CE:1C:06:73:EB:84:11:C3:F2:AB:88:F0:02:71:AF:B6:BC` |
| **Google Auth trong APK** | `AUTH_MODE=google` (mặc định), `GOOGLE_SERVER_CLIENT_ID` trỏ Client ID 72 ký tự `.apps.googleusercontent.com` |
| **`dart analyze lib test`** | **0 error, 0 warning** *(52 `info` sẵn có, 0 `info` mới)* |
| **`flutter test`** | **219 / 219 tests pass** *(152 sẵn có + 63 `kai` MOB-05/06 + 4 test nối tích hợp)* |
| **Rà soát `AlertDialog(` trong `mobile/lib`** | **0** *(tuân thủ W3C COGA)* |
| **`flutter build web`** | **Pass** (`--pwa-strategy=none`, biên dịch thành công) |

### Điều kiện mở Google Auth trên Staging (cho `vqh`)
1. Thêm `SHA-1`: `D3:22:D4:CB:36:DF:3F:FA:CB:12:9F:F7:D8:90:13:AD:9F:B0:12:F9` vào **Google Cloud Console -> Credentials -> Android OAuth Client (`com.proplan.proplan_va`)**.
2. Thêm Gmail của thành viên/QA vào **OAuth consent screen -> Test users** (chế độ `Testing`, tránh `Error 403: access_denied`).
3. Do đã truyền `GOOGLE_SERVER_CLIENT_ID` (Web Client ID) lúc build, **không cần sửa env trên Render** và **không cần build lại APK** sau khi `vqh` khai báo `SHA-1`.

---

## 2. Checklist Đầu việc Mobile theo Kế hoạch Tuần 4 (`docs/week4_detailed_plan.md`)

| Mã việc | Phụ trách | Trạng thái | Chi tiết triển khai & Nối tích hợp | Bằng chứng kiểm tra |
| --- | --- | --- | --- | --- |
| **W4-04** | `tan` | **Đã đạt (Dev/Unit)** | Đồng bộ `effective_revision` trên Settings (`SettingsScreen`), xử lý `409 REVISION_CONFLICT`, phân biệt cấu hình có hiệu lực ngay vs tạo proposal UF6, dùng `schedule_revision` thật khi xác nhận preview Calendar, đồng bộ đồng hồ 24h toàn app (`d0bd025`). | `adaptation_api_test.dart`, `w4_api_and_clock_sync_test.dart`, `widget_test.dart` |
| **W4-05** | `tan` (+ `vqh`) | **Đã đạt (Dev/Unit)** | Tách phiên ProPlan khỏi quyền OAuth Calendar (`Calendar 401` giữ nguyên app session), mutex single-flight refresh token, đóng SQLite + hủy nhắc trước phiên khi `signOut()`, tái sử dụng `GOOGLE_SERVER_CLIENT_ID` cho Android Staging. | `api_session_test.dart`, `task_provider` / `auth_service.dart` |
| **W4-07** | `qt` + `tan` | **Đã đạt (Dev/Unit)** | Khởi tạo `FocusProvider.createOfflineAware(...)` tách SQLite theo `ownerId`, Outbox replay theo thứ tự UTC (`ClockSyncService`), auto-adoption khi `409 ACTIVE_RUN_EXISTS`, giữ `offline_start_supported=false`, khôi phục Run/Check-in sau crash (`home_screen.dart`), `submitActualCorrection` (`POST /runs/{id}/actual-corrections`). | `focus_lifecycle_test.dart`, `focus_outbox_test.dart`, `focus_recovery_test.dart`, `w4_07m_recovery_and_outbox_test.dart` |
| **W4-08 (MOB-06)** | `kai` + `tan` | **Đã đạt (Dev/Unit) · Chờ máy thật** | `AlarmService` nhắc trước phiên theo `reminder_offsets` **giây** (`[900, 600, 300]`, kèm Micro-task 2p, dải ID `20000+`), hủy nhắc cũ khi chốt UF2 / duyệt UF6 / đổi cài đặt UF7 / mở `HomeScreen` / đăng xuất (`PreSessionReminderSync`), hủy nhắc của phiên khi Start; `FocusNativeController.start(..., phases:)` + `continueToNextRun(..., phases:)` cho chuông chuyển pha Work <-> Rest; `releaseIfCheckInExpired` kiểm định kỳ & khi resume ở `FocusProvider`/`FocusScreen`; mở thẳng phiên từ thông báo `pre_session:<id>` (`main.dart`); bỏ copy "mô phỏng" ở `_lockPreview`. | `alarm_service_test.dart`, `pre_session_reminder_test.dart`, `phase_alarm_test.dart`, `check_in_expiry_test.dart`, `kai_integration_wiring_test.dart` |
| **W4-09** | `qt` + `tan` | **Đã đạt (Dev/Unit)** | UF4 tách `AudioRecorderService` (`AudioInterruptionMode.none`) khỏi UF1 `Uf1VoiceRecorderService`, thử ghi tối đa 3 lần trước khi mở nhập chữ (`_textFallback`), Tombstone xóa mềm + SnackBar Hoàn tác 5s không hồi sinh thẻ ma sau `GET /thoughts`, chuyển Thought -> Draft (`_ThoughtDraftEntry`) nối `TwoTierSttService` sau phiên. | `thought_persistence_test.dart`, `uf1_voice_entry_test.dart` (`RISK-04`) |
| **W4-10 (MOB-05)** | `kai` + `tan` | **Đã đạt (Dev/Unit) · Chờ máy thật** | Nâng `receiveTimeout` `/voice/transcriptions` lên `48s` (`MOB-02`, > 45s budget); nối `VoicePlaybackService` (prefetch khi vào Run, cache RAM/file 80 audio, hedge `2.5s` Edge-TTS -> `flutter_tts` `vi-VN` -> chỉ chữ, luôn gọi `stop()` trước khi mở mic) vào `FocusProvider`, `FocusScreen` (UF5 Check-in + Nghe lại/Dừng đọc + trả lời Check-in bằng giọng nói `parseCheckInVoiceOutcome`), `UnfinishedReviewScreen`, và `CalendarVoiceQuestionScreen` (đọc tóm tắt lịch + STT 2 tầng); nối `TwoTierSttService` vào `TaskEntryScreen` (UF1), `_ThoughtDraftEntry` (sau phiên), `FocusScreen` (UF5) và `CalendarVoiceQuestionScreen`. | `voice_playback_service_test.dart`, `voice_two_tier_service_test.dart`, `uf1_voice_entry_test.dart`, `kai_integration_wiring_test.dart` |
| **W4-11** | `tan` | **Đã đạt (Dev/Unit)** | Onboarding `SetupFlowScreen` giữ trạng thái dở, lưu preferences + xin quyền DND/Exact Alarm/Notifications, sync nhắc trước phiên ngay khi hoàn tất; `HomeScreen` kiểm A20 suggestion & pending UF6 proposal khi mở/resume; `AdaptationApi` gửi `annoyance_feedback`. | `calendar_setup_navigation_test.dart`, `focus_review_screens_test.dart`, `home_resume_test.dart`, `adaptation_api_test.dart` |
| **W4-15** | `tan` | **Đã đạt** | Chạy sạch `dart analyze lib test` (`0` error/`0` warning), `flutter test` (`219/219` pass), `flutter build web`, đóng `app-debug.apk` Staging và ghi nhận đầy đủ SHA nguồn / SHA-256 / SHA-1. | Mục 1 tài liệu này |
| **W4-14A** | `thành` | **Chờ nghiệm thu máy thật** | Smoke toàn trình UF1–UF7 trên Staging APK + Google Sign-In/Calendar thật (2 tài khoản test), offline/reconnect, Undo 5s, UF6 stale/approve, quay video toàn trình. | Mục 4 bên dưới |
| **W4-14B** | `thành` + `kai` | **Chờ nghiệm thu máy thật** | Đo Android thật: nhắc trước phiên, chuông pha & kết thúc trong Doze (`force-idle`), reboot giữ báo thức, DND `+300s` khi kill app & khi đang mở, TTS cache/prefetch/offline, mic không đè TTS, mở phiên từ thông báo `pre_session:<id>`. | Mục 4 bên dưới |

---

## 3. Hạng mục Mở rộng Mobile đã Hoàn thiện Bổ sung (`feat/tan-w4-mobile`)

1. **Chuẩn hóa UI/UX Apple HIG & 60fps (`33b5ec8`, `b39bfa6`, `3030106`):**
   - Chuyển màn và thanh tiến độ bước (`MelbelStepProgressBar`, `MelbelAnimatedProgressBar`, thanh tiến trình Focus/Review) mượt 60fps, tôn trọng `disableAnimations`.
   - Avatar Google khung tai mèo, đồng hồ `showMelbelTimePicker` cưỡng bức 24h đồng bộ toàn app, hiệu ứng pet Melbel khi bấm chọn ngày trên lịch.
2. **Bộ nhận diện Logo mới ProPlanVA (`a08f4b2`, `ef0405d`):**
   - Cập nhật Android `mipmap-*`, Adaptive Icon (`ic_launcher.xml` nền `#FCF5EB`), Web `favicon.png`/PWA icons và màn Splash `app_logo.png` căn giữa quang học.
3. **Tính năng Focus Streak — "Nhịp cùng Melbel" (`e5b6d02`, `86e7fe1`):**
   - Hoạt động offline-first qua `SharedPreferences` (tự đối soát thêm phiên hoàn thành từ `/schedule`, **không cần đổi Backend hay migration DB**).
   - Quy tắc ADHD-safe: đủ **3 ngày liên tiếp (>= 2 phút/ngày)** mới mở chuỗi; 3 tầng khiên bảo vệ tránh áp lực; tiến hóa Melbel 5 chặng; lịch paw mèo nối dải liên tục; thẻ giới thiệu, hướng dẫn minh họa, mục Trợ giúp/FAQ và cài đặt Onboarding.

---

## 4. Checklist Nghiệm thu Trên Thiết bị Android Thật (`W4-14A` & `W4-14B`)

> Tài liệu lệnh ADB chi tiết của `kai` nằm tại [KAI_W4_NATIVE_VOICE_HANDOFF.md](KAI_W4_NATIVE_VOICE_HANDOFF.md) §5–§6.

### 4.1. Google Auth, Calendar & Toàn trình UF1–UF7 (`W4-14A` — `thành`)
- [ ] Cài mới `app-debug.apk` trên Android thật -> bấm Đăng nhập Google bằng 2 tài khoản nằm trong `Test users` -> vào Home sạch, dữ liệu 2 tài khoản tách biệt.
- [ ] Nối Google Calendar (1 tài khoản cho phép, 1 tài khoản từ chối hoặc giả lập thu hồi): bên từ chối vẫn cho nhập giờ rảnh thủ công, phiên app không bị đăng xuất.
- [ ] UF1 (`TaskEntryScreen`): nói có đoạn ngừng > 3.8s tự chốt; kiểm tra `low_confidence` hiện banner không tự điều hướng; sửa chữ làm mất banner; tắt mạng/lỗi server tự chuyển sang `OnDeviceSpeechService`.
- [ ] UF2: tạo kế hoạch tránh khung bận Google Calendar thật -> chốt -> sự kiện Focus xuất hiện trên Calendar thật, đồng thời tạo các mốc nhắc `15/10/5p` trên Android.
- [ ] UF3–UF4 ngoại tuyến: vào Focus -> tắt Wi-Fi/4G -> timer vẫn chạy tiếp khi tắt màn hình/chuyển app; giữ nút ghi âm **Cất suy nghĩ** -> Audio không bị gửi STT khi đang tập trung; bật lại mạng -> kiểm outbox đồng bộ 1 lần, xóa + Undo 5s không bị hồi sinh thẻ sau `GET /thoughts`.
- [ ] UF5 -> UF6 -> UF7: hết giờ kiểm tra `Hoàn thành` / `Chưa xong` / `Việc đột xuất` / `Bỏ qua (3 chip + Undo 5s)` / `Làm tiếp ngay phiên kế` (DND giữ liền mạch); đổi `reminder_offsets` hoặc `work/rest/buffer` trong Cài đặt (`409` xử lý ổn) -> kiểm đề xuất UF6 so sánh trước/sau và duyệt lịch.
- [ ] Focus Streak ("Nhịp cùng Melbel"): kiểm thẻ giới thiệu, sheet tiến hóa, chạm ngày trên lịch thấy paw mèo nối dải & Melbel phản ứng pet.

### 4.2. Native Alarm, Doze, DND & Voice UF5 (`W4-14B` — `thành` + `kai`)
- [ ] **Nhắc trước phiên (`15/10/5p`):** chốt phiên bắt đầu sau ~16 phút -> khóa màn -> nhận đủ 3 thông báo kèm gợi ý *Khởi động 2 phút* (không pop-up đè màn hình).
- [ ] **Hủy nhắc cũ khi đổi lịch / start sớm:** duyệt dời lịch ở UF6 hoặc bấm Start sớm -> kiểm tra `adb shell dumpsys alarm | grep -A3 com.proplan.proplan_va` không còn mốc nhắc giờ cũ.
- [ ] **Chạm thông báo nhắc (`pre_session:<id>`):** khi app đang mở hoặc đã tắt hẳn, chạm thông báo `"Còn 10 phút..."` -> app mở thẳng màn Chuẩn bị của đúng phiên đó.
- [ ] **Chuông chuyển pha & hết phiên trong Doze:** bật phiên có `Work -> Rest -> Work`, ép `deviceidle force-idle` -> chuông chuyển pha và hết phiên kêu đúng mốc (lệch <= 1 giây), **không** tự đánh dấu hoàn thành phiên.
- [ ] **DND `+300s` quá hạn Check-in (app kill & app đang mở):** hết phiên -> `am kill` chờ 5 phút **và** test trường hợp để màn Check-in mở suốt 5 phút -> `zen_mode` trả lại đúng giá trị trước phiên, Wakelock tắt, ghi nhận `Chưa phản hồi` (`no_response`/`no_respond`), không đánh dấu hoàn thành.
- [ ] **Giữ DND user tự bật sẵn:** bật DND tay trước khi Start -> kết thúc phiên DND vẫn giữ bật.
- [ ] **Reboot giữa phiên:** Start -> `adb reboot` -> mở lại kiểm tra `dumpsys alarm` vẫn còn chuông hết phiên + báo thức DND + nhắc trước phiên.
- [ ] **TTS Prefetch, Cache & Offline UF5:** Start phiên 2 phút khi có mạng -> tắt mạng trước khi hết giờ -> hết giờ Check-in vẫn phát giọng Edge-TTS ngay từ cache; thử thêm ca thiếu mạng hoàn toàn dùng `flutter_tts` `vi-VN` (luôn hiện chữ song song).
- [ ] **Mic không đè TTS & Hủy STT không treo:** đang phát câu hỏi Check-in / đọc lịch -> chạm nút micro trả lời hoặc nút Nghe lại/Dừng đọc -> giọng đọc ngắt tức thì; đang chuyển lời STT bấm `Hủy` -> trả quyền UI ngay, không treo nút micro.
