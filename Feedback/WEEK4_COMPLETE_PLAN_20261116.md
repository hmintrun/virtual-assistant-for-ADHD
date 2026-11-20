# ProPlan VA — Kế hoạch Tuần 4 Hoàn chỉnh: Tích hợp vào `dev`, Tách Rạch Ròi Tồn đọng & Chia Việc Độc lập

> **Thời gian thực hiện:** `17–23/11/2026` (Giờ Việt Nam `UTC+7`)  
> **Chiến lược Git & Tích hợp Đã Chốt:**
> - **Tích hợp `origin/backend` (`e72cefe`) + `origin/mobile` (`18be48a`) vào nhánh `dev`** (`origin/dev` hiện ở `48f0c68`, là tổ tiên chung của cả hai nhánh; điểm rẽ gần nhất giữa Backend và Mobile là `e3bbd5a`).
> - **Lưu ý merge vào `dev`:** Từ sau `e3bbd5a`, `origin/mobile@18be48a` có sửa 4 file `backend/app/services/{calendar_service.py,calendar_sync_worker.py,google_calendar_client.py,plan_service.py}`, thêm `backend/tests/unit/test_calendar_sync_retry.py` và sửa `.gitignore`. Đối chiếu trực tiếp cho thấy các bản vá chính ở 4 file dịch vụ đó (`CalendarConflictError`, fallback `calendarList/primary`, lọc kết nối `connection_state == "connected"`) đều đã nằm ở phiên bản mới hơn trong `origin/backend@e72cefe`. Do đó khi hợp nhất vào `dev`: **lấy toàn bộ `backend/` theo `e72cefe` (rà giữ `test_calendar_sync_retry.py` nếu cần)** và **lấy `mobile/`, `docs/mobile/`, `.gitignore` theo `18be48a`**.
> - **Không dùng `_temp/` cho điều phối cả nhóm:** `/_temp` nằm trong `.gitignore` (dòng 119), chỉ tồn tại cục bộ trên máy cá nhân cho các worktree/phiên AI riêng lẻ. Mọi phân công, hợp đồng API và trạng thái tích hợp của 8 thành viên phải dùng **Git nhánh `dev` + PR/Issue Board trên GitHub + tài liệu tracked trong `docs/`**.
> **Tham chiếu:** [Project.md](file:///D:/HCMUS/CT/ProActive/Project.md), [AGENTS.md](file:///D:/HCMUS/CT/ProActive/AGENTS.md), [E2E_COMBINED_REPORT_20261116.md](file:///C:/Users/admin/.gemini/antigravity/brain/6676fd32-d80c-44c4-881a-e6296d8e998c/E2E_COMBINED_REPORT_20261116.md).

---

# PHẦN 1 — KẾT QUẢ NỀN TẢNG E2E `16/11`, TỒN ĐỌNG TÁCH RIÊNG (`BACKEND` VS `MOBILE`) & PHẠM VI TUẦN 4

## 1.0. Tóm tắt Nền tảng Đã Kiểm chứng (`E2E Combined 16/11`: `origin/backend@e72cefe` + `origin/mobile@18be48a`)

| Hạng mục Kiểm chứng (`16/11/2026`) | Kết quả Trực tiếp Quan sát | Tài liệu / Bằng chứng |
| :--- | :--- | :--- |
| **Layer 1 — Mobile Static & Unit (`18be48a`)** | `flutter analyze --no-pub`: `0` error, `0` warning (`105` info, `59.2s`); `flutter test --no-pub`: `30/30` passed (`00:02 +30`). | [E2E_COMBINED_REPORT_20261116.md](file:///C:/Users/admin/.gemini/antigravity/brain/6676fd32-d80c-44c4-881a-e6296d8e998c/E2E_COMBINED_REPORT_20261116.md) |
| **Layer 2 — Live Web UI (`:8080` `dev_user`, `:8081` `google`)** | Chạy xuyên Account, UF7 Settings (cả khi chưa có & đã có phiên tương lai + reject UF6), UF1, UF2 (`PATCH /drafts` + Python `36p`), UF3 (409 khi start sớm >15p), UF4 (retry 3/3 $\rightarrow$ fallback text + `POST /thoughts 201`), UF5 (3 chip Skip), UF6 Reopen, `#/thoughts`. Bắt sống **`BE-01`** (`"hằng ngày"`), **`MOB-01`** (`409` Settings), **`MOB-03`–`MOB-05`**. | [frames/](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames), [backend_server_redacted.log](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/logs/backend_server_redacted.log) |
| **Layer 3 — Live API, Google OAuth, 3 Voice `.m4a`, GCal Worker** | `GET /health` `200`, `POST /auth/google` `200`, `POST /auth/refresh` `200`, `GET /preferences` `200`; `3/3` file `.m4a` STT `200` (`8.8s–10.4s`); `voice1` đạt `can_plan = true` vòng 1 $\rightarrow$ `PATCH /candidates` `200` $\rightarrow$ `confirm 201` $\rightarrow$ `CalendarSyncWorker` đồng bộ **`5/5`** phiên lên `ProActive QA` + `[A22.3]` `links_deleted_externally: 1` $\rightarrow$ dọn sạch về `0` sự kiện; Primary GCal đọc `6` sự kiện bận; `0` secret leak. | [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json) |
| **Giới hạn Môi trường Thực địa (`16/11`)** | `adb devices` = `0` thiết bị; emulator `android-37.0` trống $\rightarrow$ chuyển **8 ca Phần cứng / Người thật Android** vào kế hoạch QA `W4-03B` và `W4-14A/B`. *(Lưu ý: Web Google Sign-In & Calendar OAuth trên `http://localhost:8080` đã test thành công từ `14–15/11`; `:8081` hôm `16/11` bị GSI chặn origin do sai host/port `127.0.0.1:8081`)* | Mục 1.3 & Bảng QA `3.8` |

---

## 1.1. Tồn đọng Hiện tại của Backend (`origin/backend` @ `e72cefe`)

**Những lỗi Backend trước đây ĐÃ xác nhận vá trên `e72cefe` (đội KHÔNG cần làm lại):**
- `GET /api/v1/health` (`200`), `POST /api/v1/auth/google` (`200`), `POST /api/v1/auth/refresh` (`200`), `GET /api/v1/preferences` (Google User mới trả `200`, hết `503`), `POST /voice/tts` (`200`), `POST /voice/uf5-checkin-prompt` (`200` có `fallback_to_device_tts`), `UF1` trích xuất Deadline/Scope vòng 1 ([step_115.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_115.png)), `buffer_multiplier = 0.5 -> 422 VALIDATION_ERROR`, `Calendar preview/refresh` rev `1 -> 200`, `CalendarSyncWorker` đẩy `5/5` phiên lên `ProActive QA` & `[A22.3]`.

**Các tồn đọng Backend CÒN MỞ cần xử lý trong Tuần 4:**

| Mã | Mức | Mô tả Tồn đọng Cụ thể trên `origin/backend@e72cefe` | Vị trí / Bằng chứng (`16/11`) |
| :--- | :---: | :--- | :--- |
| **`BE-01`** | `HIGH` | **[Bug mới phát hiện từ E2E 16/11]** Khi input có `"19:00 đến 22:00 hằng ngày"`, người dùng bổ sung rõ ngày (`16/11..22/11`) qua `POST /drafts/{id}/analyze` đã điền đủ `7` `date_overrides`, nhưng `analyze_draft` vẫn giữ `AI_AVAILABILITY_INCOMPLETE` khiến `can_plan` kẹt `false` và khóa nút `"→ Tạo phương án"`. | [draft_service.py](file:///D:/HCMUS/CT/ProActive/backend/app/services/draft_service.py#L1431-L1475), [step_150.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_150.png), [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json#L88-L104) |
| **`BE-02`** | `HIGH` | **[Security / Staging - TD-E1]** `OPTIONS /api/v1/auth/me` với `Origin: https://evil.example` trả `200`, phản chiếu `Access-Control-Allow-Origin: https://evil.example` kèm `Access-Control-Allow-Credentials: true`. | `backend/app/main.py`, [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json#L4-L8) |
| **`BE-03`** | `HIGH` | **[Auth Lifecycle - TD-A3, TD-E2]** `POST /auth/refresh` cho phép tái sử dụng cùng một refresh token (`stateless_refresh_reuse_status = 200`); chưa lưu/rotate/thu hồi `jti` trong DB; thiếu hoàn toàn `POST /api/v1/auth/logout` (trả `404`). | `backend/app/api/v1/auth.py`, `services/auth_service.py`, `core/security.py`, [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json#L36-L40) |
| **`BE-04`** | `HIGH` | **[Focus Integrity - TD-E3]** Endpoint Start/End Run (`focus.py` / `run_service.py`) cần bật kiểm tra clock-skew theo `server_now` (không truyền `now=None`; trong test `16/11` gửi `+3m` ở phiên tương lai `2026-11-17` trả `409 START_REQUIRES_REPLAN`, chưa cô lập riêng biên `scheduled_start - 18m`), chặn `ended_at > server_now + MAX_CLOCK_SKEW_SECONDS`, và bảo đảm replay Start/End & `POST /runs/{id}/end` idempotent. | `backend/app/api/v1/focus.py`, `services/run_service.py`, [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json#L123-L128) |
| **`BE-05`** | `MEDIUM` | **[Staging & AI/Voice Config - TD-E2, E4, E5, E6]** (1) Whitelist `return_target` ở OAuth callback; (2) Fail-fast khi thiếu `SECRET_KEY` / `CALENDAR_CREDENTIAL_SECRET_KEY` ở staging/prod; (3) Cấu hình mặc định tách `GEMINI_FLASH_MODEL != GEMINI_BACKUP_MODEL` và mặc định `VOICE_STT_PROVIDER` ưu tiên `groq`; (4) Sửa khóa `lock_owner` + giới hạn kích thước/TTL `_VOICE_IDEMPOTENCY_STORE`; (5) Đọc timezone người dùng/task thay vì hardcode `"Asia/Ho_Chi_Minh"` khi tạo/sửa event Calendar; (6) Kiểm tra khóa ghi `calendar_sync_worker` trên SQLite. | `backend/app/core/config.py`, `api/v1/{calendar.py,voice.py}`, `services/{credential_crypto.py,plan_service.py,reschedule_service.py,calendar_sync_worker.py}` |
| **`BE-06`** | `MEDIUM` | **[EdgeTTS Reliability]** Live test `16/11` xác nhận `EdgeTTS` không ổn định: gặp cả `EMPTY_AUDIO` (`5666ms`) lẫn `TIMEOUT` (`10009ms`) trên `/voice/uf5-checkin-prompt` (trả `200` với `fallback_to_device_tts = true`) và `503` trên `/voice/tts`. Cần khóa rõ schema `503` + `fallback_to_device_tts` & SLA timeout cho Mobile `W4-10`. | [backend_server_redacted.log](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/logs/backend_server_redacted.log), [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json#L80-L87) |
| **`BE-07`** | `LOW` | **[A20 & Sync Jobs Route - TD-A7]** Khớp tài liệu `Project.md` & test A20 theo thông số code hiện hữu (`SUGGESTION_MIN_SAMPLES = 7`, `SUGGESTION_SLIDING_WINDOW = 10`, ngưỡng lệch `0.20`, cooldown `7–14 ngày`); rà route [calendar.py](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/calendar.py#L701-L727) hiện chỉ có `GET /sync-jobs/{job_id}` (chưa có list `GET /calendar/sync-jobs` $\rightarrow$ `404`). | `backend/app/services/settings_service.py`, [calendar.py](file:///D:/HCMUS/CT/ProActive/backend/app/api/v1/calendar.py#L701-L727) |

---

## 1.2. Tồn đọng Hiện tại của Mobile (`origin/mobile` @ `18be48a`)

| Mã | Mức | Mô tả Tồn đọng Cụ thể trên `origin/mobile@18be48a` | Vị trí / Bằng chứng (`16/11`) |
| :--- | :---: | :--- | :--- |
| **`MOB-01`** | `HIGH` | **[UF7 409 Conflict - TD-A1]** `SettingsScreen` gửi `_preferences!['revision']` thay vì `effective_revision` cho `/settings/change-requests` và `/suggestions/{id}/response` $\rightarrow$ **tái lập sống lỗi `409 REVISION_CONFLICT`** (`"Cấu hình đã thay đổi."`, `revision=5` vs `effective_revision=3` sau khi từ chối đề xuất UF6); cần thêm chặn input `buffer_multiplier` ngoài `[1.0, 2.0]`, bỏ mã lỗi ma `POLICY_NOT_CONFIGURED`, và nối nút hủy change request. | [settings_screen.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/screens/settings_screen.dart#L201-L243), [step_509_shot_3.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_509_shot_3.png) |
| **`MOB-02`** | `HIGH` | **[Auth & Calendar 401 - TD-A2, A3, A4, A5]** (1) `AuthService` bỏ rơi `refresh_token`, `_renew()` trên Web trả `false`, chưa gọi `POST /auth/logout`; (2) Backend trả `401` Calendar dạng `{"error": {"code": "CALENDAR_AUTH_REQUIRED", ...}}` (không có key `"detail"`), trong khi `ApiClient` kiểm `detail` gây nguy cơ logout nhầm app; (3) `receiveTimeout = 20s` toàn cục ngắn hơn fallback STT; (4) `GoogleCalendarApi`/`CalendarApi` chặn URL local `http://localhost`/`10.0.2.2` và hardcode `expected_preview_revision = 1`. | [api_client.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/core/api_client.dart#L251-L285), `auth_service.dart`, `calendar_api.dart`, `google_calendar_api.dart`, [results_combined_layer3.json](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/results_combined_layer3.json#L55-L57) |
| **`MOB-03`** | `HIGH` | **[UF6 Reopen, 409 Auto-Retry & DTO Gap - TD-D4, A6]** (1) `HomeScreen` khi mở app với `ScheduleProposal` `pending_approval` (`9b3acd21-...`) phát **`0` call** tới `/proposals/current` hay `/suggestions/current`; (2) `ScheduleComparisonScreen` bỏ qua `steps_to_add`, `pending_notification_state`, `next_horizon_preview`, **lộ nguyên văn `'resegmented_by_settings_change'`** ra UI, và khi `deficit > 0` / `confirmable == false` **thiếu 2 action chips `"Mở thêm giờ rảnh"` / `"Lùi Deadline"`**; (3) `AdaptationApi` (`createProposal`, `updateProposal`) chưa dùng `error.currentRevisions` để **auto-retry ngầm `409 REVISION_CONFLICT`**; (4) `PlanningApi` cắt `title > 100`, `PlanningDraftModel` thiếu `candidate_ref`/`source_thought_id`/`confirmed_task_id`, `TaskProvider` check `'outbox_queued'`, và còn file dead placeholder `checkin_screen.dart` (1 dòng comment). | [step_408_shot_1.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_408_shot_1.png), [step_495_shot_2.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_495_shot_2.png), `focus_review_screens.dart`, `adaptation_api.dart`, [checkin_screen.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/screens/checkin_screen.dart#L1), `planning_api.dart`, `planning_models.dart`, `task_provider.dart` |
| **`MOB-04`** | `HIGH` | **[Offline SQLite, Outbox, OOM Recovery & Thought Sync - TD-B1..B5]** (1) `main.dart:80-90` chỉ khởi tạo `FocusProvider` với `SecureStorageRunRepository`, bỏ trống SQLite/Outbox/ClockSync/Close-DB theo `ownerId`; (2) `hold_until` so sánh lẫn UTC và local; (3) `flushOutbox()` nuốt `4xx` trong `catch (_)`; (4) `FocusProvider.restore()` chạy `runEndedWhileClosed` trước `checkInExpired`, đồng thời khi mở lại app chưa **tự điều hướng thẳng vào Check-in khi `awaiting_checkin` còn hạn `< 300s`** và chưa **hiện thông báo nhẹ khi quá `300s`**; (5) `#/thoughts` phát **`0` request** `GET /thoughts` và `DELETE /thoughts/{id}` (`deleteThought` chỉ xóa local, trên Web click xóa vẫn còn thẻ); thiếu `source_run_id` khi enqueue Thought và thiếu đối soát receipt lúc `Start` (`offline_start_supported = false`). | [main.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/main.dart#L80-L90), [focus_provider.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/providers/focus_provider.dart#L510-L536), [home_screen.dart](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/lib/screens/home_screen.dart#L199-L236), [step_468_shot_1.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_468_shot_1.png), [step_517_shot_1.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_517_shot_1.png) |
| **`MOB-05`** | `HIGH` | **[Voice 2 chiều & UF5 Flow - TD-D1, D2]** (1) File `voice_playback_service.dart` chưa tồn tại; suốt luồng UF5 client phát **`0` call** tới `/voice/tts` hay `/voice/uf5-checkin-prompt`; (2) Chưa có fallback sang `flutter_tts` khi Backend trả `503` hoặc `fallback_to_device_tts = true`; (3) `UnfinishedReviewScreen` & `CalendarVoiceQuestionScreen` phụ thuộc `speech_to_text` on-device; (4) Màn kết thúc phiên UF5 **thiếu nút `"Làm tiếp ngay phiên kế"`** (giữ nguyên DND). | [step_371_shot_1.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_371_shot_1.png), `focus_review_screens.dart`, `support_screens.dart` |
| **`MOB-06`** | `HIGH` | **[Android Native Alarm, Doze, DND - TD-C1..C4]** (1) Nhắc trước phiên 15/10/5p mới là mô phỏng UI; (2) Thiếu alarm chuyển pha Work $\leftrightarrow$ Rest; (3) `RebootBroadcastReceiver` trong `AndroidManifest.xml:78-85` đặt `android:enabled="false"`; (4) Xin quyền Alarm/DND ngay lúc bấm `"Bắt đầu đếm giờ"` gây đứt nhịp tập trung. | [AndroidManifest.xml](file:///D:/HCMUS/CT/ProActive/_temp/wt_e2e_18be48a/mobile/android/app/src/main/AndroidManifest.xml#L78-L85), `alarm_service.dart`, `focus_screen.dart` |
| **`MOB-07`** | `MEDIUM` | **[Onboarding, W3C COGA & Web/Mic Polish - TD-D3, D5]** (1) `SetupFlowScreen` chưa nối `_AuthGate` cho user mới và chưa lưu preferences lên Backend; (2) Thiếu `SnackBar` Hoàn tác 5 giây khi xóa giờ rảnh/ngắt Calendar; (3) Còn chuỗi `'Draft Stored'`, `'Ready State'`, `CandidateReviewScreen` push chồng stack; (4) `DecompositionChoiceScreen` nút mic UF2 hiện báo `"Nhập giọng nói chưa sẵn sàng..."` (giữ ở nhánh Stretch nếu kịp cổng `19/11`); (5) `AccountScreen` hiện tĩnh `'— · Chưa có dữ liệu'` (nằm ngoài scope Tuần 4 theo quyết định backlog). | [step_217_shot_1_step_217_mic_test.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_217_shot_1_step_217_mic_test.png), [step_30_shot_1_account_dark_mode.png](file:///D:/HCMUS/CT/ProActive/_temp/e2e_combined_20261116/frames/step_30_shot_1_account_dark_mode.png), `onboarding_screens.dart` |

---

## 1.3. Hạng mục Phạm vi, Chính sách & 8 Ca Test Bắt buộc Trên Thiết bị Android Thật

- **Phạm vi BẮT BUỘC (`17–23/11`):** Tích hợp `dev`, xử lý toàn bộ `BE-01` $\rightarrow$ `BE-07` và `MOB-01` $\rightarrow$ `MOB-06` + phần Must-Have của `MOB-07` (`SetupFlowScreen`, Undo 5s, tiếng Việt hóa, sửa stack `CandidateReviewScreen`), nghiệm thu `UF1–UF7` trên Android thật.
- **Stretch (chỉ làm nếu ĐẠT cổng `19/11` & trước đóng băng `21/11`):** Nối mic UF2 (`DecompositionChoiceScreen` + `PATCH /candidates/{id}`) và TTS đọc micro-task 2 phút UF3.
- **Để sau Tuần 4 (Backlog):** Offline Start (`offline_start_supported` giữ `false`), FCM Push hai chiều, Web hoàn chỉnh, NL UF6 mở rộng, AI mở rộng horizon UF6, Worker chạy A20 tuần, `AccountScreen` Streak/Focus Stats, dọn dead code cũ & refactor bỏ commit trong HTTP GET.
- **8 Nhóm Test Bắt buộc Chờ Thiết bị Android Thật & Thao tác Người thật (QA thực hiện trực tiếp ở `W4-03B` và `W4-14A/B`):** (1) **Native Android** Google Sign-In (Android Credential Manager + SHA-1 trên APK `AUTH_MODE=google`; bản Web `http://localhost:8080` và API `POST /auth/google` đã test thành công); (2) **Android** Chrome Custom Tab OAuth Consent $\rightarrow$ Deep link `proplan://calendar/connected`; (3) Thu âm Mic thật trên điện thoại ở UF1/UF2/UF4 & từ chối quyền Mic 3 lần; (4) Nghe loa TTS/chuông & Voice Check-in 2 chiều; (5) Doze Mode (`dumpsys deviceidle force-idle`), Exact Alarm ($\le 1\text{s}$), Wakelock & DND tự tắt sau `ended_at + 300s`; (6) Force-kill & Reboot (`BOOT_COMPLETED`) khôi phục Alarm/DND; (7) Tắt/bật Wi-Fi/4G thật trên `SqliteOutboxRepository`; (8) Đo `60 FPS` cảm ứng thực (`flutter --profile`).

---

# PHẦN 2 — NGUYÊN TẮC TÁCH VIỆC ĐỘC LẬP & BẢN ĐỒ ĐIỂM HỖ TRỢ

Mỗi thành viên **sở hữu trọn vẹn một tập file/module riêng** để code và test **hoàn toàn độc lập** từ nhánh `dev` (chỉ giao tiếp qua interface/fixture đã khóa). Khi có điểm cần hỗ trợ chéo giữa hai người, **mọi điểm hỗ trợ được chỉ định rõ: hỗ trợ việc gì, tại file/vị trí nào và ở thời điểm nào**.

```
Ngày 17/11 (Mở đường độc lập):
  mt (W4-01): Merge backend@e72cefe + mobile@18be48a -> dev
  kc (W4-02): Xuất OpenAPI + 5 bộ JSON mẫu từ e72cefe vào docs/backend/schema.md
  tn (W4-03A) + thành (W4-03B): Seed DB 2 tài khoản + máy Redmi thật + PR test UF5/UF6

Ngày 18–21/11 (5 Luồng Độc lập Sở hữu File Riêng):
  • tn   -> backend/{main.py, core/config.py, alembic/, calendar.py[redirect]}
  • vqh  -> backend/services/* + api/v1/{auth.py, focus.py, voice.py}
  • tan  -> mobile/lib/{screens/*, widgets/*, models/*, core/api_client.dart, services/*_api.dart, main.dart}
  • qt   -> mobile/lib/{repositories/*, core/{local_database_manager,clock_sync_service}, outbox_*, providers/focus_provider.dart}
  • kai  -> mobile/android/* + services/{alarm_service,focus_native_controller,dnd_service,voice_service,voice_playback_service}.dart

Ngày 22–23/11 (Đóng gói & Nghiệm thu độc lập):
  tn (W4-13 Backend CI) | tan (W4-15 APK) -> thành (W4-14A/B trên Android thật) -> mt (W4-16 Bàn giao)
```

## 2.1. Bảng Phân công Tổng quan Độc lập 8 Thành viên

| STT | Thành viên (`Handle`) | Phạm vi Sở hữu File Độc lập (Không Ai Sửa Đè) | Mã việc Chính | Mốc Giao Độc lập |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Minh Trung (`mt`)** | Branch `dev` (merge PR), `Project.md`, `docs/` báo cáo AI/COGA & biên bản bàn giao *(không sửa code `services/` hay `screens/`)* | `W4-01`, `W4-12A`, `W4-16` | **17/11:** `dev` hợp nhất xong. **21/11:** Rà AI/COGA. **23/11:** Gói bàn giao. |
| **2** | **Đặng Kim Cương (`kc`)** | `docs/backend/schema.md`, OpenAPI export + JSON fixtures, contract tests trong `backend/tests/` | `W4-02` | **17/11 (trong ngày):** OpenAPI + 5 bộ JSON fixtures trên `dev`. |
| **3** | **Trang Tín (`tn`)** | `backend/alembic/versions/*`, `.env.example`, `backend/app/main.py` (CORS), `core/config.py`, `services/credential_crypto.py`, `api/v1/calendar.py` *(chỉ đoạn `return_target` redirect)* | `W4-03A`, `W4-06`, `W4-13` | **17/11:** DB seed sạch. **19/11:** Staging checklist + migration. **23/11:** `pytest` SQLite + PG16. |
| **4** | **Võ Quốc Hưng (`vqh`)** | `backend/app/api/v1/{auth.py,focus.py,voice.py}`, `core/security.py`, `services/{draft_service,auth_service,run_service,settings_service,plan_service,reschedule_service}.py`, test A20 | `W4-05B`, `W4-07B`, `W4-12B` | **19/11:** Vá `BE-01,03,04,05`. **21/11:** Bộ mẫu & test A20. |
| **5** | **Trần Thanh An (`tan`)** | `mobile/lib/screens/*`, `widgets/*`, `models/*`, `core/api_client.dart`, `services/{auth_service,calendar_api,google_calendar_api,planning_api,adaptation_api}.dart`, `providers/task_provider.dart`, `main.dart` | `W4-04`, `W4-05M`, `W4-11`, `W4-15` | **19/11:** APK #1. **21/11:** APK #2. **22/11:** APK ứng viên + SHA-256. |
| **6** | **Nguyễn Quốc Thịnh (`qt`)** | `mobile/lib/repositories/*`, `core/{local_database_manager,clock_sync_service}.dart`, `services/{outbox_replay_worker,local_schedule_repository}.dart`, `providers/focus_provider.dart` | `W4-07M`, `W4-09` | **19/11:** Factory DI + UTC `hold_until`/Outbox/`checkInExpired`. **21/11:** Thought `GET/DELETE` + unit test offline. |
| **7** | **Lê Quang Khải (`kai`)** | `mobile/android/*`, `mobile/lib/services/{alarm_service,focus_native_controller,dnd_service,voice_service,voice_playback_service}.dart` | `W4-08`, `W4-10` | **19/11:** Native Alarm/Doze/DND/Reboot. **21/11:** `VoicePlaybackService` + Dual-tier STT. |
| **8** | **Doãn Thành (`thành`)** | 2 tài khoản Google test, thiết bị Redmi thật, PR `backend/tests/test_uf5_uf6_*.py`, ma trận & video E2E | `W4-03B`, `W4-14A`, `W4-14B` | **17/11:** Smoke + PR test UF5/UF6. **19 & 21/11:** Chốt 2 cổng. **23/11:** 8 ca phần cứng + video. |

## 2.2. Bảng Điểm Hỗ trợ Chéo Duy nhất (Ai Hỗ trợ Ai — Ở Điểm Nào — Ở Đâu)

| STT | Luồng Hỗ trợ (`Người hỗ trợ` $\rightarrow$ `Người nhận`) | Hỗ trợ Ở Điểm Nào (Nội dung Cụ thể) | Vị trí Chính xác (File / Dòng / Endpoint) | Thời điểm |
| :---: | :--- | :--- | :--- | :---: |
| **1** | **`vqh` $\rightarrow$ `kc`** | Chốt nhanh chữ ký JSON của `POST /api/v1/auth/logout` và mã `503` / `fallback_to_device_tts` để `kc` đưa vào OpenAPI & JSON mẫu trong ngày 17/11. | `docs/backend/schema.md`, `backend/app/api/v1/auth.py` | **17/11** |
| **2** | **`vqh` $\rightarrow$ `tn`** | Nếu `W4-05B` (`jti` refresh token) cần thêm bảng/cột DB, `vqh` chốt trước cấu trúc ORM ở `models/user.py` để `tn` viết & chạy thử Alembic migration độc lập. | `backend/app/models/user.py` $\rightarrow$ `backend/alembic/versions/*` (`W4-06`) | **18/11** |
| **3** | **`tn` $\rightarrow$ `thành`** | Bàn giao script seed sạch cho 2 tài khoản Google thử trên SQLite & PostgreSQL để `thành` không phải tự tạo tay dữ liệu nền. | Seed script trong `backend/` $\rightarrow$ DB QA (`W4-03A` $\rightarrow$ `W4-03B`) | **17/11** |
| **4** | **`kai` $\rightarrow$ `qt`** | Cung cấp getter/trạng thái native sạch (`FocusRecoveryState.awaitingCheckIn` vs `checkInExpired`, DND owner snapshot) và API alarm pha Work/Rest để `qt` gọi bên trong `FocusProvider` mà `kai` không chạm vào `focus_provider.dart`. | `services/focus_native_controller.dart` & `alarm_service.dart` $\rightarrow$ `providers/focus_provider.dart` (`L510-536`) | **19/11** |
| **5** | **`qt` $\rightarrow$ `tan`** | Cung cấp 1 hàm khởi tạo `FocusProvider.createOfflineAware(...)` + getter `awaitingCheckIn`/`checkInExpired` + hàm xóa Thought `deleteThought` kèm `DELETE /api/v1/thoughts/{id}` để `tan` ghép vào app qua đúng 1 lời gọi. | `providers/focus_provider.dart` $\rightarrow$ `main.dart` (`L80-90`) & `home_screen.dart` (`L199-236`) | **19–20/11** |
| **6** | **`kai` $\rightarrow$ `tan`** | Cung cấp class `VoicePlaybackService` (`speakPrompt`, `stop`, fallback `503` $\rightarrow$ `flutter_tts`) và `VoiceService.transcribeTwoTier(...)` cùng đoạn gọi thay thế nhắc nền `15/10/5p` để `tan` gắn vào nút UI (vì `tan` giữ toàn bộ `screens/*`). | `services/{voice_playback_service.dart,voice_service.dart,alarm_service.dart}` $\rightarrow$ `screens/{focus_screen.dart [L297], focus_review_screens.dart, support_screens.dart}` | **19–21/11** |
| **7** | **`vqh` $\rightarrow$ `thành`** | Bàn giao bộ dữ liệu biên A20 (`< 7` mẫu, `>= 7` mẫu, ngưỡng `0.20`, cooldown `7–14` ngày) để `thành` kiểm chứng trực tiếp ở `W4-14A`. | `backend/tests/test_w310_uf6_reschedule.py` / test fixture A20 | **21/11** |
| **8** | **`kai` $\rightarrow$ `thành`** | Hướng dẫn lệnh ADB đo Doze/Alarm/DND (`dumpsys deviceidle force-idle`, `dumpsys alarm`) ở `W4-03B` và hỗ trợ đọc `adb logcat` khi `thành` test 8 ca phần cứng ở `W4-14B`. | Máy Android QA + `adb logcat` / `dumpsys` (`W4-03B` & `W4-14B`) | **17/11 & 22–23/11** |
| **9** | **`mt` $\rightarrow$ `vqh` & `tan`** | `mt` kiểm tra chéo (review không sửa đè code) PR `BE-01` và UI Mobile trước đóng băng: chỉ ra đích danh file/dòng vi phạm COGA hoặc grounded copy nếu có để chủ file tự sửa. | PR review trên `dev` (`draft_service.py`, `reschedule_service.py`, `screens/*`) | **20–21/11** |

---

# PHẦN 3 — PHÂN CÔNG CHI TIẾT TỪNG NGƯỜI

## 3.1. Minh Trung (`mt`) — Hợp nhất `dev`, Kiểm định AI/COGA & Hồ sơ Bàn giao

- **Phụ trách chính (Độc lập):** `W4-01`, `W4-12A`, `W4-16`
- **Hỗ trợ nhận / trao:**
  - *Trao hỗ trợ cho `vqh` & `tan` (`20–21/11`):* Review chất lượng grounded copy UF6 & W3C COGA trên PR của `vqh` (`draft_service.py`, `reschedule_service.py`) và `tan` (`screens/*`) tại GitHub PR vào `dev`.
  - *Nhận hỗ trợ từ `thành` (`23/11`):* Nhận kết quả kiểm tra cài đặt lại APK cuối cùng & video từ `W4-14A/B` để đóng biên bản `W4-16`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File / Nơi Thực hiện | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **17/11** | **`W4-01`** | - Tạo nhánh/PR hợp nhất **`origin/backend` (`e72cefe`)** và **`origin/mobile` (`18be48a`)** vào **`dev`** (`origin/dev@48f0c68`): giữ `backend/` theo `e72cefe` (rà giữ `test_calendar_sync_retry.py`), giữ `mobile/`, `docs/mobile/`, 2 dòng `.gitignore` theo `18be48a`.<br>- Xác nhận cả 8 người kéo từ **`dev`** (không dùng `_temp/` làm bảng điều phối chung).<br>- Cập nhật tài liệu tham số A20 (`7` mẫu / `10` Step / cooldown `7–14` ngày) trong `Project.md` và mở Issue cho từng mã `BE-01..07`, `MOB-01..07`. | GitHub PR vào `dev`, `Project.md`, [E2E_COMBINED_REPORT_20261116.md](file:///C:/Users/admin/.gemini/antigravity/brain/6676fd32-d80c-44c4-881a-e6296d8e998c/E2E_COMBINED_REPORT_20261116.md) | Nhánh `dev` chứa trọn vẹn Backend `e72cefe` + Mobile `18be48a`, build/chạy cơ bản xanh, đủ Issue có người nhận. |
| **20–21/11** | **`W4-12A`** | - Chạy test kiểm chứng độc lập sau khi `vqh` sửa `BE-01`: thử ca `"hằng ngày"` UF1 -> bổ sung text -> kiểm tra `can_plan == true`, kiểm tách profile UF1 khỏi Warm Backup `UF2/UF6` (`GEMINI_FLASH_MODEL != GEMINI_BACKUP_MODEL`).<br>- Kiểm tra `scheduler_engine.py` tính 100% $\Delta T_{\text{deficit}}$ và payload UF6 không lộ `task_title` thô.<br>- Rà UI trên bản build từ `dev` theo checklist W3C COGA (3–5 phần tử tương tác chính/màn, 1-Step UI UF3, Undo 5s thay Confirm Dialog). | PR review trên `dev` (đối chiếu `draft_service.py`, `reschedule_service.py`, `screens/*`) | `100%` ca AI `"hằng ngày"` / UF2 / UF6 grounded copy và COGA checklist đạt. |
| **22–23/11** | **`W4-16`** | - Tập hợp hồ sơ bàn giao Tuần 4: APK (`W4-15`) + SHA-256, Backend/Mobile Git SHA trên `dev`, migration head, OpenAPI/fixtures (`W4-02`), báo cáo suite (`W4-13`), video + báo cáo đo lường (`W4-14A/B`), danh sách backlog *Để sau Tuần 4* kèm người giữ. | Thư mục phát hành / tài liệu bàn giao cuối Tuần 4 | Biên bản bàn giao hoàn tất ngày `23/11`, khớp toàn bộ SHA và kết quả cài thử. |

---

## 3.2. Đặng Kim Cương (`kc`) — Chốt Contract OpenAPI & JSON Fixtures (`W4-02`)

- **Phụ trách chính (Độc lập):** `W4-02`
- **Hỗ trợ nhận / trao:**
  - *Nhận từ `vqh` (`17/11`):* Xác nhận chữ ký endpoint `POST /api/v1/auth/logout` (trong `api/v1/auth.py`) và cấu trúc `503` `/voice/tts` + `fallback_to_device_tts` `/voice/uf5-checkin-prompt` (trong `api/v1/voice.py`).
  - *Trao hỗ trợ cho `tan`, `qt`, `kai` (`17/11`):* Xuất 5 bộ JSON fixtures vào `docs/backend/schema.md` trên `dev` để Mobile code & test độc lập không cần chờ Backend sửa xong.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File Sở hữu | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **17/11** *(Xong trong ngày)* | **`W4-02`** | - Xuất OpenAPI JSON trực tiếp từ `e72cefe` trên `dev` + cập nhật đặc tả `POST /auth/refresh` & `POST /auth/logout`.<br>- Viết & commit 5 bộ JSON mẫu (đủ `200`, `401`, `409`, `412`, `422`, `503`) vào `docs/backend/schema.md`:<br>  1. `401 CALENDAR_AUTH_REQUIRED` vs `401 UNAUTHENTICATED` (`MOB-02`).<br>  2. Settings `revision` vs `effective_revision`, cancel change request, `buffer_multiplier ∈ [1.0, 2.0]` (`MOB-01`).<br>  3. Calendar `expected_preview_revision` động & `event-change-previews` (`MOB-02`).<br>  4. Run receipt/idempotency, clock-skew error, `Thought` kèm `source_run_id` (`MOB-04`).<br>  5. UF6 `steps_to_add` + lý do tiếng Việt hóa (tránh lộ `'resegmented_by_settings_change'`), timeout voice STT ($\ge 45\text{s}$), response `/voice/tts` (`503`) & `/voice/uf5-checkin-prompt` (`fallback_to_device_tts`).<br>- Bổ sung & chạy contract test API trong `backend/tests/`. | `docs/backend/schema.md`, fixtures trên `dev`, contract tests trong `backend/tests/` | Các team xác nhận lấy được fixtures trên `dev` trong ngày `17/11`; test contract Backend pass. |

---

## 3.3. Trang Tín (`tn`) — DB, Staging Security (`BE-02, BE-05`) & Full Regression (`W4-03A`, `W4-06`, `W4-13`)

- **Phụ trách chính (Độc lập):** `W4-03A`, `W4-06`, `W4-13`
- **Hỗ trợ nhận / trao:**
  - *Trao hỗ trợ cho `thành` (`17/11`):* Bàn giao DB seed sạch 2 tài khoản thử ở `W4-03A` để `thành` chạy smoke `W4-03B`.
  - *Nhận từ `vqh` (`18/11`):* Nhận mô tả trường bảng/cột refresh token `jti` (ở `backend/app/models/user.py`) để viết Alembic migration trong `backend/alembic/versions/` ở `W4-06` mà không đụng code service của `vqh`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File Sở hữu | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **17/11** | **`W4-03A`** | - Dựng DB test sạch cho SQLite và PostgreSQL (`proactive-qa-postgres`) từ `dev`, chạy `alembic upgrade head` (`20261111_01`).<br>- Chạy seed cho **2 tài khoản Google thử** (preferences mặc định, draft mẫu, lịch sử Step mẫu). | `backend/alembic/`, seed scripts | DB sạch đúng `head`, đăng nhập & truy vấn seed hoạt động. |
| **18–19/11** | **`W4-06`** | - Vá **`BE-02`** trong `backend/app/main.py`: đổi `allow_origins=["*"]` thành allowlist rõ ràng (không wildcard khi `allow_credentials=True`).<br>- Vá **`BE-05` (Cấu hình & Redirect)**: whitelist `return_target` trong `backend/app/api/v1/calendar.py` (`L142-212`); thêm kiểm tra fail-fast ở `core/config.py` + `services/credential_crypto.py` khi staging/prod dùng key mặc định; tách mặc định `GEMINI_FLASH_MODEL != GEMINI_BACKUP_MODEL` và `VOICE_STT_PROVIDER=groq` trong `core/config.py` / `.env.example`.<br>- Viết & test Alembic migration cho `jti` refresh token (nếu `W4-05B` yêu cầu).<br>- Lập checklist cấu hình staging đã che mọi secret. | `backend/app/main.py`, `core/config.py`, `services/credential_crypto.py`, `api/v1/calendar.py` (đoạn redirect), `alembic/versions/*`, `.env.example` | `OPTIONS https://evil.example` không còn trả wildcard+credentials; migration upgrade sạch; checklist staging ký đạt. |
| **22–23/11** | **`W4-13`** | - Chạy toàn bộ suite (`ruff check`, `ty check`, `pytest`) trên cả **SQLite** và **PostgreSQL** từ nhánh `dev` đóng băng.<br>- Kiểm chứng nâng cấp Alembic từ bản cũ lên `head` và kiểm tra khóa ghi `calendar_sync_worker.py` trên SQLite. | `backend/tests/*`, báo cáo chạy trên `dev` | Báo cáo đủ `dev` SHA, `migration head`, log lệnh và bảng `pass/fail/skip/xfail` cho SQLite & PG16. |

---

## 3.4. Võ Quốc Hưng (`vqh`) — Backend Core Logic (`BE-01, BE-03, BE-04, BE-05, BE-07`)

- **Phụ trách chính (Độc lập):** `W4-05B`, `W4-07B`, `W4-12B`
- **Hỗ trợ nhận / trao:**
  - *Trao hỗ trợ cho `kc` (`17/11`) & `tn` (`18/11`):* Chốt contract `POST /auth/logout` với `kc` (ở `api/v1/auth.py`), giao diff ORM nếu cần migration bảng refresh token cho `tn` viết Alembic ở `W4-06`.
  - *Trao hỗ trợ cho `thành` (`21/11`):* Bàn giao bộ mẫu & test A20 đúng ngưỡng `7 / 10 / 7–14` ngày trong `backend/tests/` cho `W4-14A`.
  - *Nhận review từ `mt` (`20–21/11`):* Tiếp nhận phản hồi grounded copy / `BE-01` trên PR `dev`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File Sở hữu | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **18–19/11** | **`W4-05B`**, **`W4-07B`** | - **Vá `[BE-01]`:** Sửa `draft_service.py` (`L1431-1475`) để khi `POST /drafts/{id}/analyze` đã tạo được tập `date_overrides` hợp lệ phủ khoảng thời gian người dùng vừa làm rõ (thay cho cụm `"hằng ngày"`), xóa `unresolved` cũ và bỏ chặn `AI_AVAILABILITY_INCOMPLETE` để `can_plan = true`.<br>- **Vá `[BE-03]` (`W4-05B`):** Implement lưu `jti`, refresh token rotation + reuse detection và bổ sung endpoint **`POST /api/v1/auth/logout`**.<br>- **Vá `[BE-04]` (`W4-07B`):** Bật kiểm tra clock-skew thực tế cho `start_session_run` (bỏ truyền `now=None` ở `focus.py`), chặn `ended_at > server_now + MAX_CLOCK_SKEW_SECONDS` ở `end_session_run`, bảo đảm replay Start/End & `POST /runs/{id}/end` idempotent.<br>- **Vá `[BE-05]` (Service):** Sửa `lock_owner` + TTL `_VOICE_IDEMPOTENCY_STORE` trong `api/v1/voice.py`; đọc timezone user/task thay hardcode `"Asia/Ho_Chi_Minh"` trong `plan_service.py` & `reschedule_service.py`. | `backend/app/services/{draft_service.py,auth_service.py,run_service.py,plan_service.py,reschedule_service.py}`, `api/v1/{auth.py,focus.py,voice.py}`, `core/security.py` | Test mới cho `"hằng ngày"` -> làm rõ đạt `can_plan = true`; refresh rotate/revoke/logout pass; clock-skew trả 4xx; replay không nhân đôi Run. |
| **20–21/11** | **`W4-12B`** | - Kiểm & khóa logic Pilot **A20 (`BE-07`)** đúng ngưỡng **`SUGGESTION_MIN_SAMPLES = 7`**, **`SUGGESTION_SLIDING_WINDOW = 10`**, `BUFFER_DELTA_THRESHOLD = 0.20`, cooldown **`7–14 ngày`**, giữ 100% ưu tiên `buffer_multiplier` thủ công.<br>- Tạo bộ dữ liệu & test mẫu A20 bàn giao cho `thành`.<br>- Review toàn bộ PR Backend vào `dev`. | `backend/app/services/settings_service.py`, `backend/tests/test_w310_uf6_reschedule.py` | Bộ test A20 pass đủ 5 ca (`<7`, `>=7`, conflict, `delta=0.20`, cooldown `7–14` ngày, giữ config tay). |

---

## 3.5. Trần Thanh An (`tan`) — Mobile UI, HTTP Client & APK (`MOB-01, MOB-02, MOB-03, MOB-07`)

- **Phụ trách chính (Độc lập):** `W4-04`, `W4-05M`, `W4-11`, `W4-15` *(sở hữu trọn vẹn `screens/`, `widgets/`, `models/`, `*_api.dart`, `main.dart`)*
- **Hỗ trợ nhận / trao:**
  - *Nhận từ `kc` (`17/11`):* Bộ JSON mẫu trên `docs/backend/schema.md` để test API client độc lập.
  - *Nhận từ `qt` (`19–20/11`):* 1 hàm khởi tạo `FocusProvider.createOfflineAware(...)` + getter `awaitingCheckIn`/`checkInExpired` + hàm `deleteThought` (tại `providers/focus_provider.dart`) để `tan` gọi từ `main.dart:80-90` và `home_screen.dart:199-236`.
  - *Nhận từ `kai` (`19–21/11`):* Interface `VoicePlaybackService` (`voice_playback_service.dart`), `VoiceService.transcribeTwoTier(...)` (`voice_service.dart`) và hàm nhắc nền `AlarmService.schedulePreSessionReminders(...)` để `tan` gắn vào `focus_screen.dart`, `focus_review_screens.dart`, `support_screens.dart` mà `kai` không cần sửa trực tiếp vào `screens/`.
  - *Trao hỗ trợ cho `thành` (`19, 21, 22/11`):* Xuất APK Tích hợp #1, #2 và APK ứng viên cuối + `SHA-256`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File Sở hữu | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **18–19/11** | **`W4-04`**, **`W4-05M`** | - **Vá `[MOB-01]` (`W4-04`):** Sửa `settings_screen.dart` gửi `effective_revision` thay `revision` cho cả `/settings/change-requests` & `/suggestions/{id}/response`; chặn `buffer_multiplier` ngoài `[1.0, 2.0]`; bỏ `POLICY_NOT_CONFIGURED`; xử lý `409` reload state & nối hủy request.<br>- **Vá `[MOB-02]` (`W4-05M` + Calendar):** Sửa `ApiClient` nhận diện `401 CALENDAR_AUTH_REQUIRED` qua `error.error.code`; tăng `receiveTimeout` `/voice/transcriptions` $\ge 45\text{s}$; `AuthService` lưu `refresh_token`/`refresh_expires_in`, `_renew()` dùng single-flight lock + `POST /auth/logout`; sửa `GoogleCalendarApi`/`CalendarApi` nhận local URL & dùng `revision` động.<br>- **Vá `[MOB-03]` (DTO, 409 Auto-Retry & UF6):** Bỏ cắt `title > 100` ở `PlanningApi`; bổ sung field còn thiếu ở `PlanningDraftModel`; sửa `'outbox_queued'` ở `TaskProvider`; trong `AdaptationApi` (`createProposal`, `updateProposal`) cài **auto-retry ngầm `409 REVISION_CONFLICT` dùng `error.currentRevisions`**; trong `ScheduleComparisonScreen` render `steps_to_add` & Việt hóa `'resegmented_by_settings_change'`.<br>- **Cuối 19/11:** Lắp factory `FocusProvider` (`qt`) và hooks `AlarmService` (`kai`) ở `main.dart` / `focus_screen.dart` $\rightarrow$ Build **APK Tích hợp #1**. | `screens/{settings_screen,focus_review_screens,focus_screen}.dart`, `core/api_client.dart`, `services/{auth_service,calendar_api,google_calendar_api,planning_api,adaptation_api}.dart`, `models/planning_models.dart`, `providers/task_provider.dart`, `main.dart` | Settings lưu $\ge 3$ lần sau reject UF6 không `409`; `401` Calendar giữ phiên; `AdaptationApi` auto-retry `409` không bung lỗi; UI UF6 sạch enum; ra APK #1 tối `19/11`. |
| **20–21/11** | **`W4-11`** | - **Vá `[MOB-07]` + `[MOB-03]` + `[MOB-04]` + `[MOB-05]` (UI):**<br>  - Nối `SetupFlowScreen` vào `_AuthGate` cho user mới + lưu preferences lên `/settings/change-requests` + đặt bước xin quyền Alarm/DND tại Onboarding/Chuẩn bị.<br>  - Nối `HomeScreen` (`WidgetsBindingObserver`) tự gọi `GET /proposals/current` & `/suggestions/current`; **tự điều hướng thẳng vào Check-in khi `awaiting_checkin` còn hạn `< 300s` & hiện thông báo nhẹ khi quá `300s`**.<br>  - Trên `ScheduleComparisonScreen`, thêm **2 action chips `"Mở thêm giờ rảnh"` / `"Lùi Deadline"`** khi `deficit > 0` / `confirmable == false`.<br>  - Bổ sung nút **`"Làm tiếp ngay phiên kế"`** ở UF5 (giữ DND), gắn `VoicePlaybackService` & `VoiceService.transcribeTwoTier` (`kai`) vào Check-in UF5 & `CalendarVoiceQuestionScreen`, và **xóa file dead placeholder `checkin_screen.dart` (hoặc đồng bộ thống nhất 1 màn Check-in)**.<br>  - Thêm Undo 5s cho xóa giờ rảnh/ngắt Calendar; Việt hóa `'Draft Stored'`, `'Ready State'`; sửa `CandidateReviewScreen` push chồng stack; nối UI `GET/DELETE /thoughts` (`qt`). *(Chỉ thêm mic UF2 nếu đạt cổng `19/11` & còn giờ trước `21/11`.)* | `screens/{onboarding_screens,home_screen,focus_review_screens,candidate_review_screen,support_screens,checkin_screen}.dart` | Mở app khi `pending_approval` hiện banner UF6; deficit có 2 action chips; `awaiting_checkin < 300s` vào thẳng Check-in; Undo 5s chuẩn COGA; ra **APK #2** tối `21/11`. |
| **22–23/11** | **`W4-15`** | - Chạy `dart analyze` (`0` errors) và toàn bộ `flutter test`.<br>- Đóng **APK ứng viên cuối**, kiểm cài mới & nâng cấp trên Android, ghi **SHA-256 APK**, **Git SHA `dev`** và hướng dẫn cài đặt. | `mobile/`, `mobile/test/*`, APK artifact | `dart analyze` & `flutter test` pass; APK + SHA-256 cài chạy được trên Redmi QA. |

---

## 3.6. Nguyễn Quốc Thịnh (`qt`) — Mobile Offline SQLite, Outbox & Thought Sync (`MOB-04`)

- **Phụ trách chính (Độc lập):** `W4-07M`, `W4-09` *(sở hữu trọn vẹn `repositories/`, `local_*`, `outbox_*`, `clock_sync_service.dart` và `providers/focus_provider.dart`)*
- **Hỗ trợ nhận / trao:**
  - *Nhận từ `kai` (`19/11`):* API nhận `FocusRecoveryState` (`awaitingCheckIn` vs `checkInExpired`) từ `services/focus_native_controller.dart` và alarm pha từ `services/alarm_service.dart` để `qt` gọi trong `FocusProvider`.
  - *Trao hỗ trợ cho `tan` (`19–20/11`):* Xuất factory `FocusProvider.createOfflineAware(...)` + getter trạng thái `awaitingCheckIn`/`checkInExpired` + method đồng bộ Thought `GET/DELETE` (tại `providers/focus_provider.dart`) để `tan` gắn vào `main.dart:80-90` và `home_screen.dart`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File Sở hữu | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **18–19/11** | **`W4-07M`** | - **Vá `[MOB-04]` (SQLite/Outbox/Recovery):**<br>  - Đóng gói factory `FocusProvider.createOfflineAware(...)` nối `LocalDatabaseManager`, `SqliteOutboxRepository`, `OutboxReplayWorker`, `LocalScheduleRepository`, `ClockSyncService` tách theo `ownerId`; cài `closeCurrentDatabase()` khi đăng xuất.<br>  - Chuẩn hóa `hold_until` và truy vấn SQLite về **UTC ISO-8601 (`Z`)**.<br>  - Sửa `flushOutbox()`: chỉ retry backoff với lỗi mạng/5xx; lỗi `4xx`/conflict xử lý rõ, **không nuốt trong `catch (_)`**.<br>  - Sửa `FocusProvider.restore()` (`L510-536`) kiểm tra **`checkInExpired` trước `runEndedWhileClosed`**, xuất rõ trạng thái `awaitingCheckIn < 300s` vs `checkInExpired > 300s`.<br>  - Cài chính sách Start (`offline_start_supported = false`): rớt mạng lúc Start phải đối soát Run/receipt hiện có trước khi retry; Run đã nhận tiếp tục chạy offline và replay không nhân đôi theo `id_run`. | `mobile/lib/core/{local_database_manager,clock_sync_service}.dart`, `repositories/*`, `services/{outbox_replay_worker,local_schedule_repository}.dart`, `providers/focus_provider.dart` | Run đã Start chạy offline xuyên restart, tách đúng `ownerId`, replay giữ thứ tự UTC & không nhân đôi; unit test UTC & `checkInExpired` pass. |
| **20–21/11** | **`W4-09`** | - **Vá `[MOB-04]` (Thought Inbox & UF4):**<br>  - Bảo toàn file `.m4a` UF4 offline sau restart app; gắn đủ `source_run_id` khi enqueue Thought.<br>  - Chuẩn hóa thử lại thu/lưu tối đa **3 lần** rồi mở ô text (giữ timer UF3).<br>  - Nối **`GET /api/v1/thoughts`** và **`DELETE /api/v1/thoughts/{id}`** kèm tombstone local để replay/GET **không làm Thought đã xóa hiện lại**; hỗ trợ luồng sau phiên chuyển Thought thành Draft (`POST /thoughts/{id}/transcribe` $\rightarrow$ `/convert-to-draft`). | `providers/focus_provider.dart`, `repositories/local_outbox_repository.dart`, `mobile/test/*` | Ghi âm UF4 offline $\rightarrow$ restart còn phát được $\rightarrow$ có mạng sync đủ `source_run_id`; xóa Thought đồng bộ sạch `DELETE 200`; unit test pass. |

---

## 3.7. Lê Quang Khải (`kai`) — Android Native Alarm/Doze/DND & Voice Service (`MOB-05, MOB-06`)

- **Phụ trách chính (Độc lập):** `W4-08`, `W4-10` *(sở hữu trọn vẹn `mobile/android/*` và `services/{alarm_service,focus_native_controller,dnd_service,voice_service,voice_playback_service}.dart`)*
- **Hỗ trợ nhận / trao:**
  - *Trao hỗ trợ cho `qt` (`19/11`):* Xuất `FocusRecoveryState` (`awaitingCheckIn` / `checkInExpired`) ở `focus_native_controller.dart` và alarm chuyển pha ở `alarm_service.dart` để `qt` gọi trong `FocusProvider`.
  - *Trao hỗ trợ cho `tan` (`19–21/11`):* Xuất `AlarmService.schedulePreSessionReminders(...)` (thay dòng mô phỏng `focus_screen.dart:297`), `VoicePlaybackService` (`voice_playback_service.dart`), và `VoiceService.transcribeTwoTier(...)` (`voice_service.dart`) để `tan` gắn vào UI `screens/`.
  - *Trao hỗ trợ cho `thành` (`17/11 & 22–23/11`):* Cung cấp quy trình/lệnh ADB đo Doze/Alarm/DND ở `W4-03B` và hỗ trợ đọc `adb logcat` khi `thành` chạy 8 ca phần cứng ở `W4-14B`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | File Sở hữu | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **18–19/11** | **`W4-08`** | - **Vá `[MOB-06]`:**<br>  - Viết API notification/alarm hệ thống thật cho mốc nhắc `15/10/5p` (theo `reminder_offsets`; hủy lịch cũ; bỏ mốc đã qua) và alarm chuyển pha **Work $\leftrightarrow$ Rest** trong `alarm_service.dart` + Kotlin native.<br>  - Bật **`RebootBroadcastReceiver` (`android:enabled="true"`)** trong `AndroidManifest.xml`, lưu metadata alarm bền vững để khôi phục sau reboot.<br>  - Khôi phục đúng trạng thái DND theo snapshot của đúng owner chậm nhất tại **`ended_at + 300s`** khi không phản hồi Check-in; nhả sạch `WakelockPlus`.<br>- Chuẩn hóa `FocusNativeController` báo `checkInExpired` / `awaitingCheckIn` cho `qt` (`W4-07M`). | `mobile/android/app/src/main/AndroidManifest.xml`, `mobile/android/app/src/main/kotlin/...`, `mobile/lib/services/{alarm_service,focus_native_controller,dnd_service}.dart` | Trên Android thật (Doze & khóa màn hình), chuông lệch $\le 1\text{s}$; reboot giữ lại alarm; DND tắt đúng hạn `ended_at + 300s`. |
| **20–21/11** | **`W4-10`** | - **Vá `[MOB-05]` (Độc lập trong `services/`):**<br>  - Tạo mới `voice_playback_service.dart`: gọi `POST /api/v1/voice/tts` và `POST /api/v1/voice/uf5-checkin-prompt`, phát audio bằng `just_audio`/`audioplayers`. **Bắt buộc fallback sang `flutter_tts` khi API trả `503` (EdgeTTS timeout/empty audio đã thấy ở Backend) hoặc trả `fallback_to_device_tts = true`.** Quản lý audio focus để **TTS và Mic không đè nhau**.<br>  - Nâng cấp STT trong `voice_service.dart` sang Dual-tier (`/voice/transcriptions` ưu tiên Groq Whisper, timeout $\ge 45\text{s}$ $\rightarrow$ `speech_to_text`), hủy giữa chừng không treo mic.<br>- Viết test đơn vị/service độc lập + xuất method gọn cho `tan` (`W4-11`). *(Stretch: thêm hàm TTS micro-task UF3 & voice UF2 nếu đạt cổng `19/11`.)* | `mobile/lib/services/{voice_playback_service.dart,voice_service.dart}`, `mobile/test/*` | Service test pass đủ 4 ca: Online EdgeTTS/Groq, fallback `503`/timeout $\rightarrow$ `flutter_tts`, thiếu gói `vi-VN`, hủy mic không treo. |

---

## 3.8. Doãn Thành (`thành`) — QA Độc lập trên Thiết bị Thật & 8 Ca Phần cứng/Người thật

- **Phụ trách chính (Độc lập):** `W4-03B`, `W4-14A`, `W4-14B`
- **Hỗ trợ nhận / trao:**
  - *Nhận từ `tn` (`17/11`), `vqh` (`21/11`), `tan` (`19, 21, 22/11`):* Nhận DB seed (`W4-03A`), fixture A20 (`W4-12B`), và các bản APK + SHA-256 (`W4-15`) để chạy test độc lập trên Redmi thật.
  - *Nhận từ `kai` (`17/11 & 22–23/11`):* Nhận lệnh ADB đo Doze/Alarm/DND (`W4-03B`) và hỗ trợ soi `adb logcat` nếu gặp bug OS khi chạy `W4-14B`.
  - *Trao hỗ trợ cho `mt` (`23/11`):* Giao báo cáo ma trận test, bảng đo `p50/p95`, video chính + 4 clip ca biên để `mt` đóng `W4-16`.

| Ngày | Mã việc | Công việc Độc lập Cần Thực hiện | Đầu vào / Thiết bị | Tiêu chí Hoàn thành (DoD) |
| :--- | :---: | :--- | :--- | :--- |
| **17/11** | **`W4-03B`** | - Chuẩn bị **2 tài khoản Google thử** khớp DB seed + điện thoại Android thật (`adb devices` nhận máy) + bộ audio tiếng Việt.<br>- Mở PR đưa 2 bộ test Backend UF5/UF6 (`test_uf5_uf6_matrix.py`, `test_uf5_uf6_edge_cases.py`) lên `dev`.<br>- Chạy smoke nền trên `dev`, xác nhận tái hiện rõ các bug (`409` Settings, UF1 `"hằng ngày"`, UF6 Reopen, Thought Delete) và chốt checklist đo alarm/DND. | Android thật + bản build từ `dev` + DB seed `W4-03A` | Có log smoke `17/11`, PR 2 file test vào `dev`, bảng OS/quyền & hướng dẫn đo alarm/DND. |
| **18–21/11** | Kiểm **2 Cổng** (`19/11` & `21/11`) | - **Cổng `19/11` (APK Tích hợp #1):** Login Google thật (`AUTH_MODE=google`) $\rightarrow$ Settings $\ge 3$ lần sau reject UF6 $\rightarrow$ `401` Calendar không văng app $\rightarrow$ Start Run $\rightarrow$ tắt Wi-Fi $\rightarrow$ restart app xem timer & alarm.<br>- **Cổng `21/11` (APK Tích hợp #2):** Đi thông 1 lượt `UF1–UF7` trên staging (gồm `"hằng ngày"` UF1, UF4 retry 3x/offline, UF5 TTS+fallback, `awaiting_checkin < 300s`, UF6 `steps_to_add` & 2 deficit chips). | APK #1 (`19/11`), APK #2 (`21/11`), Staging Backend | Xác nhận Pass/Fail từng cổng để `mt` & nhóm quyết định đóng băng `21/11`. |
| **22–23/11** | **`W4-14A`**, **`W4-14B`** | - **Nghiệm thu đầy đủ 8 ca Phần cứng & Người thật trên Redmi thật:**<br>  1. Đăng nhập Google Sign-In/2FA trên APK `AUTH_MODE=google` (SHA-1).<br>  2. OAuth Consent Google Calendar trên Chrome/Custom Tab + deep link `proplan://calendar/connected`.<br>  3. Thu âm Mic thật ở UF1, UF4 (5s đang chạy đồng hồ, phát lại ở `#/thoughts`, và từ chối Mic 3x $\rightarrow$ text).<br>  4. Nghe loa TTS + Check-in UF5 hai chiều (cả lúc EdgeTTS lỗi rớt sang `flutter_tts`).<br>  5. Đo Exact Alarm ($\le 1\text{s}$) trong Android **Doze Mode** (`dumpsys deviceidle force-idle`) & kiểm `flutter_dnd` tự tắt chậm nhất tại `ended_at + 300s`.<br>  6. Force-kill & **Reboot máy** (`BOOT_COMPLETED`) kiểm khôi phục Alarm/DND.<br>  7. Bật/tắt Wi-Fi & 4G thật kiểm `SqliteOutboxRepository` & `GET/DELETE /thoughts`.<br>  8. Đo `60 FPS` (`flutter --profile`) khi cuộn Lịch, Focus, giữ nút UF4.<br>- Kiểm bộ mẫu **A20 (`7 / 10 / 7–14 ngày`)** từ `vqh` và cài thử lại APK theo hướng dẫn `W4-15`.<br>- **Quay Video Kịch bản Chính + 4 Clip Ca biên**. | APK cuối (`W4-15`) + SHA-256, Backend `dev` cuối (`W4-13`), ADB & stopwatch | Ma trận ca khớp APK SHA-256 & Backend SHA, bảng đo `p50/p95` & alarm $\le 1\text{s}$, đủ 5 video nghiệm thu & danh sách lỗi mở. |

---

# PHỤ LỤC — GÓI BÀN GIAO NGHIỆM THU CUỐI TUẦN 4 (`23/11/2026`)

1. **APK ứng viên cuối cùng** (build từ `dev` đã tích hợp) + mã **`SHA-256`** + hướng dẫn cài đặt.
2. **Git Commit SHA cuối trên nhánh `dev`** + **Alembic `migration head`**.
3. **Báo cáo suite Backend** (`ruff`, `ty`, `pytest` trên cả SQLite & PostgreSQL từ `W4-13`) và `dart analyze` / `flutter test` (`W4-15`).
4. **OpenAPI & bộ JSON mẫu** (`W4-02`, tracked trong `docs/backend/`) + seed **2 tài khoản demo** (`W4-03A`).
5. **Báo cáo QA thực địa + bảng đo Doze/Reboot/DND/Voice (`p50/p95`)** (`W4-14A`, `W4-14B`) + **Video Kịch bản Chính & 4 Clip Ca biên** (`401` Calendar không văng app; Skip 3 chip + Undo 5s; Doze + Reboot alarm; `no_response` sau 5p tự tắt DND).
6. **Biên bản bàn giao & Backlog *Để sau Tuần 4* kèm người giữ** (`W4-16`).
