# Bàn giao module Native Focus (UF3–UF5) — Kai → Tân

> **Người bàn giao:** Quang Khải (`kai`) · **Người nhận:** Thanh An (`tan`)
> **Ngày:** 07/11/2026 · **Nhánh:** `feat/kai-w2-dnd+alarm+recorder` (tạo từ `origin/backend` @ `7a924a3`)
> **Trạng thái:** _[Kai điền: đã push / PR số …]_

Tài liệu này giúp Tân ghép phần native (chuông hết phiên, Không làm phiền, giữ màn hình sáng, ghi âm "Cất suy nghĩ") vào Provider/UI của UF3–UF5. **Tân chỉ cần gọi `FocusNativeController`**, không cần gọi trực tiếp các service bên dưới (trừ phần xin quyền và ghi âm).

---

## 1. Tóm tắt nhanh

| Tân cần làm gì | Gọi hàm |
|---|---|
| Mở app (sau khi giao diện hiện lên) | `FocusNativeController.instance.recoverOnAppStart()` |
| Trước phiên đầu tiên / màn onboarding | Xin quyền — xem [mục 5](#5-xin-quyền--onboarding) |
| Bấm **"Bắt đầu đếm giờ [X]p"** | `FocusNativeController.instance.start(...)` → **dùng `endsAt` trả về cho đồng hồ** |
| Chuông hết phiên kêu | **KHÔNG gọi gì** — mở Check-in UF5, DND vẫn giữ |
| User xác nhận kết quả / nghỉ / báo bận / bỏ qua / kết thúc sớm | `FocusNativeController.instance.stop(runId: ...)` |
| User chọn **"Làm tiếp phiên kế"** ngay sau Check-in | `FocusNativeController.instance.continueToNextRun(...)` (thay cho `stop` + `start`) |
| User không phản hồi Check-in 5 phút | **Không cần làm gì** — báo thức ngầm tự trả DND (kể cả khi app đã chết) |
| Bấm giữ / nhả **"Cất suy nghĩ"** | `AudioRecorderService.instance.start()` / `.stopAndFinalize()` |

---

## 2. Các file

| File | Vai trò |
|---|---|
| `mobile/lib/services/focus_native_controller.dart` | **Điểm giao duy nhất cho UI.** Gộp chuông + DND + Wakelock + ghi âm |
| `mobile/lib/services/alarm_service.dart` | Chuông hết phiên (Android giữ báo thức, kêu đúng giờ kể cả Doze / app đã tắt) |
| `mobile/lib/services/dnd_service.dart` | Bật/trả lại DND có snapshot + owner (`runId`) |
| `mobile/lib/services/audio_recorder_service.dart` | Thu âm thô UF4, kiểm tra file, đếm lỗi 3 lần |
| `mobile/lib/main.dart` | Đã thêm `AlarmService.instance.init()` và `FocusNativeController.instance.init()` |
| `mobile/android/app/src/main/AndroidManifest.xml` | Quyền + receiver/service cho chuông và báo thức ngầm |
| `mobile/pubspec.yaml` | Thêm `timezone`, `path_provider`, `shared_preferences`, `android_alarm_manager_plus` |
| `mobile/test/alarm_service_test.dart` | Unit test làm tròn giây (4/4 đạt) |

> ⚠️ **Tránh conflict:** nhánh `feat/tan-w2-mobile` của Tân cũng có `dnd_service.dart` và `audio_recorder_service.dart`. Hai file này thuộc phần của Kai theo kế hoạch tuần 2 → **Tân dùng bản của Kai**, bỏ bản cũ của Tân khỏi PR. `pubspec.yaml` / `pubspec.lock` có thể cùng thêm dependency như `path_provider`: gộp danh sách dependency nhưng chỉ giữ **một khai báo cho mỗi key** trong YAML, sau đó chạy `flutter pub get`. Không giữ hai dòng `path_provider` vì YAML trùng key sẽ làm Flutter dừng ngay khi đọc manifest.

---

## 3. API `FocusNativeController`

Singleton: `FocusNativeController.instance`.

### 3.1. `start()`

```dart
final result = await FocusNativeController.instance.start(
  runId: run.id,                       // ID phiên (Run) — dùng lại khi stop
  duration: Duration(minutes: 25),
  stepTitle: step.title,               // hiện trong thông báo: Bạn làm "…" tới đâu rồi?
);
```

Trả về `FocusStartResult`:

| Trường | Ý nghĩa | UI nên làm |
|---|---|---|
| `endsAt` | Mốc kết thúc **đã làm tròn lên giây chẵn** | **Đồng hồ trên màn hình phải đếm tới đúng mốc này** (không tự tính `now + duration`) để đồng hồ về 0 và chuông kêu cùng một giây |
| `alarmScheduled` | `false` = thiếu quyền báo thức | Nhắc nhẹ, phiên vẫn chạy |
| `dndApplied` | `false` = thiếu quyền DND, hoặc user đã tự bật DND từ trước | Có thể hiện gợi ý nhẹ "Bật Không làm phiền để tập trung hơn?" — không chặn phiên |
| `wakelockOn` | `false` = máy không hỗ trợ giữ màn hình sáng | Không cần làm gì |

Thứ tự bên trong: lưu "phiên đang chạy" xuống máy → đặt chuông → bật DND → bật Wakelock. Bước nào lỗi cũng không làm hỏng bước khác.

### 3.2. `stop()`

```dart
final r = await FocusNativeController.instance.stop(runId: run.id);
if (r.savedRecording != null) {
  // User đang giữ "Cất suy nghĩ" đúng lúc kết thúc phiên → bản thu ĐÃ ĐƯỢC LƯU
  // (không hủy). Đưa vào Hộp nháp như một Thought bình thường.
}
```

- Hủy chuông + báo thức ngầm, trả lại DND đúng trạng thái trước phiên, tắt Wakelock.
- Chỉ hủy chuông nếu `runId` đúng phiên đang chạy (tránh hủy nhầm chuông phiên mới).
- **Gọi nhiều lần vẫn an toàn.**

**Gọi `stop()` khi:** user xác nhận kết quả ở UF5 / chọn nghỉ / báo bận đột xuất / bỏ qua / bấm kết thúc sớm.
**KHÔNG gọi `stop()` khi:** chuông vừa kêu (DND phải giữ trong lúc hỏi Check-in — AGENTS.md UF5).

### 3.3. `continueToNextRun()`

```dart
final result = await FocusNativeController.instance.continueToNextRun(
  previousRunId: previousRun.id,
  runId: nextRun.id,
  duration: Duration(minutes: 25),
  stepTitle: step.title,
);
```

Dùng khi user chọn **làm tiếp phiên kế ngay**. Khác với `stop()` + `start()`: **DND được giữ nguyên** (chỉ chuyển "quyền chủ" sang phiên mới), không có khoảng hở làm thông báo bị chặn dồn ra một lúc. Trả về `FocusStartResult` như `start()`.

### 3.4. `recoverOnAppStart()`

Gọi **1 lần khi mở app**, sau khi giao diện hiện lên (ví dụ trong `initState` của màn đầu tiên / Provider khởi tạo).

```dart
final rec = await FocusNativeController.instance.recoverOnAppStart();
switch (rec.state) {
  case FocusRecoveryState.none:            // không có phiên dở → màn hình chính
  case FocusRecoveryState.running:         // còn giờ → khôi phục đồng hồ theo rec.endsAt
  case FocusRecoveryState.awaitingCheckIn: // hết giờ < 5 phút → mở Check-in UF5 (DND vẫn giữ)
  case FocusRecoveryState.checkInExpired:  // quá 5 phút → ghi nhận no_response, KHÔNG đánh dấu hoàn thành
}
```

Ở trạng thái `none`, controller tự dọn chuông "mồ côi" và DND bị kẹt (nếu app từng crash giữa phiên). Ở `running`, controller tự bật lại Wakelock và đặt lại chuông.

### 3.5. Báo thức ngầm 5 phút (tự động — Tân không cần gọi)

Lúc `start()`, controller đặt thêm 1 báo thức ngầm (ID 9002) tại `endsAt + 5 phút`. Nếu tới lúc đó chưa ai gọi `stop()`, Android chạy hàm `releaseDndAfterCheckInTimeout` trong tiến trình nền — **kể cả khi app đã bị tắt** — để trả lại DND (UF5: tự tắt DND sau 5 phút không phản hồi). Hàm này **chỉ trả DND**, không đánh dấu hoàn thành; lần mở app sau `recoverOnAppStart()` trả về `checkInExpired` để UI/Backend ghi `no_response`.

Hằng số dùng chung: `FocusNativeController.checkInDndTimeout` (5 phút) — nếu UI có đếm ngược Check-in, dùng hằng số này để khớp.

---

## 4. Ghi âm "Cất suy nghĩ" (UF4)

Singleton: `AudioRecorderService.instance`. Gợi ý ghép với nút Push-to-Talk:

```dart
// Bấm giữ
try {
  await AudioRecorderService.instance.start();       // mặc định tối đa 2 phút
} on RecorderException catch (e) {
  showGentleError(e.message);
  if (AudioRecorderService.instance.shouldFallbackToText) openQuickTextField();
}

// Nhả tay
try {
  final rec = await AudioRecorderService.instance.stopAndFinalize();
  showSaved();            // CHỈ báo "Đã cất" khi tới được dòng này
  saveThought(rec.path, rec.duration, rec.sizeBytes);
} on RecorderException catch (e) {
  showGentleError(e.message);
  if (AudioRecorderService.instance.shouldFallbackToText) openQuickTextField();
}

// Hủy (ví dụ trượt ngón tay ra ngoài nút — cách hủy do Tân/nhóm quyết)
await AudioRecorderService.instance.cancel();         // xóa file dở, không tính lỗi
```

| API | Ghi chú |
|---|---|
| `start({Duration maxDuration})` | Thu AAC mono 16 kHz 32 kbps (`.m4a`, ~400 KB/phút) vào `app_flutter/thoughts/` |
| `stopAndFinalize()` | Kiểm tra: ≥ 0,6 giây, file tồn tại, > 0 byte. Trả về `ThoughtRecording(path, duration, sizeBytes)` |
| `cancel()` | Dừng + xóa file dở. Không phải lỗi |
| `deleteRecording(path)` | Xóa file của Thought đã cất (dùng cho Undo/xóa trong Hộp nháp) |
| `shouldFallbackToText` | `true` sau **3 lỗi thật** → mở ô nhập chữ tại chỗ (quy tắc UF4) |
| `resetFailures()` | Gọi sau khi user đã dùng ô nhập chữ hoặc bắt đầu phiên mới |
| `onAutoStopped` / `onAutoStopFailed` | Callback khi chạm giới hạn thời lượng và tự dừng |
| `RecorderException.message` | Microcopy tiếng Việt, giọng nhẹ — hiển thị thẳng được |

**Lỡ chạm quá ngắn** (< 0,6 giây) ném lỗi với `countsAsFailure = false` → **không** tính vào 3 lần, không mở ô chữ.

**Quy tắc bất biến UF4** (đã đảm bảo trong service): chỉ thu file thô, **không gọi STT/LLM**, không đụng timer UF3.

**Chuông kêu khi đang giữ mic:** recorder dùng `AudioInterruptionMode.none` → chuông vẫn kêu đúng giờ (không chờ thả nút) và recorder **không tự dừng** (không mất nửa sau suy nghĩ). Đã kiểm trên máy thật. Phần hỏi Check-in có thể chờ user thả nút, nhưng **không lùi `endsAt`** / `ended_at` / không cộng thêm vào thời lượng thực tế.

---

## 5. Xin quyền & Onboarding

Controller **không tự xin quyền** — Tân gọi ở màn onboarding hoặc ngay trước phiên đầu tiên:

| Quyền | Cách xin | Ghi chú |
|---|---|---|
| Thông báo (Android 13+) | `await AlarmService.instance.requestPermissions()` | Hộp thoại hệ thống. Trả `true` khi đủ quyền thông báo + báo thức chính xác |
| Báo thức chính xác | (tự động) | Dùng `USE_EXACT_ALARM` → Android 13+ **tự cấp khi cài**, user không phải bật tay |
| Không làm phiền | `await DndService.instance.hasPermission` → nếu `false`: `DndService.instance.openPermissionSettings()` | Android bắt user **tự bật trong Cài đặt** (không có hộp thoại) |
| Micro | `await AudioRecorderService.instance.hasPermission()` | Hộp thoại hệ thống; cũng tự hỏi ở lần `start()` đầu |

**Đề xuất cho máy Xiaomi/HyperOS (xem mục 7):** thêm 1 bước onboarding nhẹ nhàng hướng dẫn bật **Tự khởi chạy** và **Pin → Không hạn chế** cho app.

---

## 6. Kết quả test trên máy thật

**Thiết bị:** Redmi Note 13 Pro · Android 16 · HyperOS 3.0. Chi tiết số liệu trong `docs/kai_notes.md` mục 6b.

| Nhóm | Ca | Kết quả |
|---|---|---|
| Chuông | Kêu khi app mở / tắt màn hình / app bị vuốt tắt; hủy chuông; Android giữ báo thức | ✅ |
| Chuông | Doze sâu |  ✅ |
| DND | Trả về đúng: tắt / "Chỉ báo thức" / không đụng DND user tự bật / user đổi giữa phiên / app bị kill | ✅ 5/5 |
| DND | Khi đang trong phiên gọi vẫn nghe được  | ✅ 1/1 |
| Ghi âm | Thu + nghe lại / lỡ chạm / hủy / lỗi 3 lần → ô chữ / chuông kêu khi đang giữ mic | ✅ 5/5 |
| Controller | Start → chuông → Stop | ✅ |
| Controller | Báo thức ngầm trả DND sau 5 phút khi app đã chết + RECOVER → `checkInExpired` | ✅ (DND tắt trễ ~44 giây) |
| Controller | `continueToNextRun` (chuyển giao DND) | ✅ |

---

## 7. Giới hạn đã biết

| Vấn đề | Ảnh hưởng | Đề xuất |
|---|---|---|
| **HyperOS chặn mở lại app "chết nhiều lần"** (`proc frequent died!`) | Chuông có thể không kêu khi app đã bị tắt | Onboarding hướng dẫn bật Tự khởi chạy + Pin không hạn chế; ghi vào giới hạn thiết bị |
| Báo thức ngầm trễ ~44 giây (JobScheduler Android 16) | DND tắt sau ~5 phút 44 giây | Chấp nhận được |
| User bấm **Buộc dừng** trong Cài đặt | Android xóa mọi báo thức của app | Giới hạn của Android |
| Kênh thông báo `focus_session_end` bị Android "đóng băng" cấu hình sau lần tạo đầu | Đổi âm thanh/độ ưu tiên trong code không có tác dụng trên máy đã cài | Khi đổi cấu hình kênh: gỡ app cài lại, hoặc đổi `_channelId` |

---

## 8. Quyết định & câu hỏi mở

| Nội dung | Trạng thái |
|---|---|
| Quyền báo thức: `USE_EXACT_ALARM` (Android 13+) + `SCHEDULE_EXACT_ALARM maxSdkVersion=32` | Kai đã chốt — chỉ phát APK, không lên Google Play |
| Làm tiếp phiên kế → chuyển giao DND (không tắt rồi bật lại) | Kai đã chốt — `week2_baseline.md` ghi là policy mở, cần nhóm xác nhận |
| Tự trả DND sau 5 phút bằng báo thức ngầm | Kai đã chốt (theo AGENTS.md UF5) |
| Cho cuộc gọi ưu tiên đi qua DND trên Android 15+ | **Mở — cần nhóm quyết** |
| Cách hủy bản thu trên UI (trượt ra ngoài nút, …) | Tân/nhóm quyết; nên kèm SnackBar Hoàn tác thay vì hộp thoại xác nhận |
| Giới hạn thời lượng thu theo `capabilities` Backend | Đang dùng mặc định 2 phút; truyền vào `start(maxDuration: …)` khi Backend có |

---

## 9. Liên hệ

Có gì vướng khi ghép, nhắn Kai. Màn test nội bộ `lib/screens/debug_alarm_screen.dart` (không commit) có sẵn các nút bấm thử từng chức năng — Kai có thể gửi riêng nếu Tân cần.
