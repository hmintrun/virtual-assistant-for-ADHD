# Bàn giao Tuần 4 — Android Native (W4-08) & Voice Service (W4-10)

> Người làm: Lê Quang Khải (`kai`) · Nhánh: `feat/kai-w4-native-voice` (tách từ `origin/mobile@18be48a`)
> Phạm vi file (theo WEEK4_COMPLETE_PLAN §3.7): `mobile/android/*`, `mobile/lib/services/{alarm_service,
> focus_native_controller,voice_playback_service,voice_two_tier_service}.dart`. **Không** sửa `screens/*` (tan)
> hay `providers/focus_provider.dart` (qt) — các đoạn code gắn vào UI/Provider nằm ở §3, §4.

## 1. Tóm tắt

| Mã | Hạng mục | Trạng thái |
|---|---|---|
| MOB-06 | Nhắc trước phiên 15/10/5p bằng thông báo hệ thống thật (theo `reminder_offsets` **giây**, kèm gợi ý Micro-task, hủy lịch cũ, bỏ mốc đã qua, hủy khi đã start) | ✅ Code + 16 test |
| MOB-06 | Chuông chuyển pha Work ↔ Rest (Doze-safe, `alarmClock`) | ✅ Code + 10 test |
| MOB-06 | Khôi phục sau reboot | ✅ Đã có cơ chế (xem §2.3) + đặt `RebootBroadcastReceiver enabled="true"` |
| MOB-06 | DND tự trả lại tại `ended_at + 300s` khi không phản hồi | ✅ Đã có từ tuần 2 (báo thức ngầm) + thêm `releaseIfCheckInExpired` cho lúc app đang mở |
| MOB-06 | `FocusRecoveryState.awaitingCheckIn / checkInExpired` cho qt | ✅ Đã có, qt đang dùng ở `focus_provider.dart:522-529` |
| MOB-05 | `VoicePlaybackService` (Edge-TTS → `flutter_tts` → chỉ chữ), không đè mic, **cache trên máy + prefetch + hedge 2,5s** | ✅ Code + 20 test |
| MOB-05 | STT hai tầng `transcribeTwoTier` (Backend → nghe trên máy), hủy không treo | ✅ Code + 14 test |
| — | Checklist đo trên Android (Doze, reboot, DND +300s, loa) | Lệnh ADB §5, các ca §6 (W4-14B) |
| — | Gắn vào UI/Provider | ⏳ tan/qt theo §3–§4 |

Kiểm tra: `flutter test` **93/93 pass** (30 cũ + 63 mới) · `dart analyze` **0 error, 0 warning** (95 info có sẵn từ trước).

## 2. Thay đổi chi tiết

### 2.1. `AlarmService` — nhắc trước phiên

```dart
final count = await AlarmService.instance.schedulePreSessionReminders(
  sessions: [
    ReminderSession(
      sessionId: s.id,
      scheduledStart: s.scheduledStart,
      stepTitle: s.stepTitle,
      plannedMinutes: s.plannedMinutes,
      microTaskText: s.microTaskText, // gợi ý Khởi động 2 phút hiện cùng lời nhắc
    ),
  ],
  // Truyền NGUYÊN giá trị Backend — đơn vị GIÂY, vd. [900, 600, 300] = 15/10/5 phút.
  offsetsSeconds: (preferences['reminder_offsets'] as List?)?.cast<int>(),
);

// Khi người dùng bấm "Bắt đầu" (kể cả bắt đầu sớm) → không phát nhắc thừa sau start:
await AlarmService.instance.cancelPreSessionRemindersFor(session.id);
```

- ⚠️ `reminder_offsets` là **giây** (Project.md §5, `default_reminder_offsets=[900,600,300]`). Chuẩn hóa đúng luật Backend: không âm, không trùng, giảm dần, tối đa 3 mốc.
- Nội dung: *"Còn 15 phút nữa vào phiên tập trung"* / *"Viết dàn ý · phiên 25 phút ↵ Khởi động 2 phút: Mở sẵn tài liệu…"*. Micro-task chỉ là gợi ý — không timer, không bắt đánh dấu xong.
- Mức thông báo mặc định (có âm báo, **không** pop-up đè màn hình — AGENTS.md §4 cấm pop-up bất ngờ).
- **Thay toàn bộ** bộ nhắc cũ mỗi lần gọi → gọi lại sau: chốt lịch UF2, duyệt UF6, đổi `reminder_offsets` UF7, mở app.
- Bỏ mốc đã qua, bỏ phiên đã bắt đầu, tối đa 60 mốc gần nhất (trần ~500 báo thức/app của Android).
- ID riêng `20000+` (không đụng chuông hết phiên `9001`, báo thức DND `9002`, chuông pha `9100+`).
- Chạm thông báo → payload `pre_session:<sessionId>` (tan có thể mở thẳng màn Chuẩn bị).
- `cancelPreSessionReminders()` khi đăng xuất / xóa Task.
- Các lời gọi đặt/hủy chạy **tuần tự** (hàng đợi nội bộ) → gọi chồng nhau không để lại báo thức mồ côi.
- Chạm thông báo: nghe `AlarmService.instance.notificationTaps` (app đang mở) và `launchPayload()` (app mở từ thông báo).

### 2.2. `FocusNativeController.start(phases:)` — chuông chuyển pha

Thêm tham số **tùy chọn** `phases` (code cũ không truyền vẫn chạy như trước):

```dart
await FocusNativeController.instance.start(
  runId: runId,
  duration: Duration(seconds: totalSeconds),
  stepTitle: stepTitle,
  phases: [
    for (final s in segments.skip(firstWorkIndex))
      FocusPhase(kind: s.kind, durationSeconds: s.durationSeconds),
  ],
);
```

- Mốc chuông tính **lùi từ `endsAt`** — đúng công thức hạn đoạn của FocusProvider (`runEndsAt − tổng các đoạn sau`), nên chuông khớp đồng hồ trên màn hình.
- Tổng `phases` phải bằng `duration`; lệch → bỏ chuông pha (chuông hết phiên vẫn có).
- `phases` + `step_title` được lưu cùng phiên đang chạy → `recoverOnAppStart()` đặt lại đúng chuông pha khi app bị kill.
- `continueToNextRun(..., phases:)` cũng nhận tham số này.
- Chuông chỉ báo chuyển pha, **không đánh dấu hoàn thành** (AGENTS.md §2.1).

### 2.3. Reboot

| Thứ cần khôi phục | Ai khôi phục |
|---|---|
| Chuông hết phiên, chuông pha, nhắc trước phiên | `ScheduledNotificationBootReceiver` của flutter_local_notifications (đã khai báo sẵn) |
| Báo thức trả DND `+300s` | `RebootBroadcastReceiver` của android_alarm_manager_plus (`rescheduleOnReboot: true`) |

Plugin tự bật/tắt `RebootBroadcastReceiver` lúc chạy; manifest đổi sang `enabled="true"` để chắc chắn trên ROM chặn
`setComponentEnabledSetting`. Ca kiểm: §6 dòng 7.

### 2.4. `releaseIfCheckInExpired` (mới)

Báo thức ngầm `+300s` chạy ở isolate riêng nên chỉ trả được DND, không tắt được Wakelock (cờ giữ màn hình gắn với
Activity). Khi app **đang mở** ở màn Check-in mà người dùng im lặng quá hạn, gọi hàm này để dọn cả DND + Wakelock:

```dart
// trong tick đồng hồ của màn Check-in (qt/tan)
if (await FocusNativeController.instance.releaseIfCheckInExpired(runId: runId)) {
  // ghi no_response — KHÔNG đánh dấu hoàn thành
}
```

### 2.5. `VoicePlaybackService` (file mới) — tối ưu thời gian

**Số đo TTS (23/11, `speech-lab/run_edge_tts.py`, `run_gemini_tts_gc.py`):**

| Engine | Có audio đầu tiên | Đủ audio | Ghi chú |
|---|---|---|---|
| **Edge-TTS** (Backend đang dùng) | ~0,35s | **~0,55s** (trung vị) | Nhưng có **đợt** chậm 4–10s (khi bị gọi dồn dập), không phải do khởi động nguội |
| Gemini 3.8 Flash-Lite TTS (`generate_content_stream`) | 1,02s | 1,71s | Ổn định hơn, nhưng PCM 24kHz nặng gấp ~6 lần MP3 và free tier chỉ **10 lượt/ngày** |
| Gemini 3.8 Flash TTS | 1,15s | 2,26s | 10 lượt/ngày |
| Gemini 3.1 Flash TTS preview | 1,32s | 2,97s | |
| Gemini 2.5 Flash preview TTS | 5,16s | 5,16s | Không stream |

→ Giữ **Edge-TTS** (nhanh nhất khi bình thường, không giới hạn lượt). Gemini TTS hôm 01/11 bị treo 90s là do gọi qua
`interactions`; qua `generate_content_stream` chỉ ~1,7s nhưng quota 10/ngày không đủ cho Check-in thật.
Vì không đoán trước được lúc nào Edge vào đợt chậm, cách tối ưu là **không bắt người dùng chờ TTS**:

| Kỹ thuật | Hiệu quả |
|---|---|
| **Prefetch** câu hỏi Check-in lúc phiên đang chạy | Hết giờ phát **tức thì** (0 gọi mạng) |
| **Cache audio trên máy** (RAM + thư mục cache, 80 file) theo (giọng, câu) | Câu đã nghe phát ngay, **cả khi offline**; câu hỏi UF5 chỉ có vài mẫu cố định nên cache gần như luôn trúng |
| **Hedge 2,5s** (trước: 8s) | Lần Edge chậm 3–8s → đọc bằng máy sau 2,5s; request vẫn chạy tiếp và **lưu cache cho lần sau** |
| Gộp request trùng | prefetch + speak cùng câu chỉ gọi mạng 1 lần |

Prefetch trong phiên **không vi phạm UF4**: `/voice/uf5-checkin-prompt` sinh câu từ mẫu cố định (không gọi LLM, không STT), chạy nền, không chạm timer/DND/1-Step UI.

```dart
final voice = VoicePlaybackService();

// 1) Lúc bấm Start (hoặc khi còn ~1 phút): tải sẵn, không phát, không ảnh hưởng timer/DND
unawaited(voice.prefetchCheckInPrompt(
  kind: 'final',
  isFinalSessionOfStep: isLastSession,
  expectedOutput: step.expectedOutput,
));

// 2) Hết giờ → hỏi Check-in (cùng tham số với prefetch để trúng cache)
final r = await voice.speakCheckInPrompt(
  fallbackText: 'Phiên vừa rồi bạn làm tới đâu?', // dùng khi mất mạng/chậm
  kind: 'final',
  isFinalSessionOfStep: isLastSession,
  expectedOutput: step.expectedOutput,
);
showText(r.promptText);          // LUÔN hiện chữ song song
r.prompt;                        // nguyên data.prompt của Backend (chip...)
r.playback.source;               // backend | device | textOnly | cancelled
r.playback.fromCache;            // true = phát từ cache, không chờ mạng

// Câu bất kỳ (nhắc UF3, lời giải thích UF6); có thể prefetchText(...) trước
await voice.speakText('Hết giờ nghỉ, mình vào phiên nhé');

// TRƯỚC khi mở mic — bắt buộc
await voice.stop();
```

| Tình huống | Hành vi |
|---|---|
| Đã prefetch / đã nghe câu này | Phát từ cache, không gọi mạng |
| Backend trả audio trong 2,5s | Phát Edge-TTS (audioplayers), lưu cache |
| 503/504, mất mạng, chậm > 2,5s, audio rỗng/hỏng | `flutter_tts` vi-VN (audio về muộn vẫn được cache) |
| `fallback_to_device_tts = true` | `flutter_tts` đọc câu của Backend |
| `prompt_text` rỗng | Đọc `fallbackText` bằng máy (không phát audio lệch với chữ) |
| Máy thiếu giọng vi-VN | `textOnly` — chỉ hiện chữ, không crash |
| `stop()` giữa chừng | Audio về muộn bị bỏ, trả `cancelled` |

**Phía Backend đi kèm** (nhánh `feat/kai-stt-transcribe-inline`, chi tiết `docs/backend/VOICE_STT_BENCHMARK.md` §8):
prewarm 7 câu hỏi Check-in lúc khởi động (sau đó lấy audio **0,06 ms**), hedged request 1,2s (đo thật: khi Edge bình
thường không đổi ~0,6s; khi Edge chậm TB 6,24s → 5,43s và hết lỗi timeout), gộp request trùng. Đo thêm cho thấy Edge
chậm theo **đợt** (khi bị gọi dồn dập) chứ không ngẫu nhiên từng request — nên cache/prefetch phía app vẫn là lớp chính.

### 2.6. `TwoTierSttService` (file mới)

Đặt ở file riêng `voice_two_tier_service.dart` để **không conflict** với `voice_service.dart` mà vqh đang sửa ở
`feat/mobile-uf1-voice-upgrade`. Hàm gọi Backend truyền vào nên dùng được với cả hai bản.

```dart
final stt = TwoTierSttService.fromVoiceService(VoiceTranscriptionService());
try {
  final t = await stt.transcribeTwoTier(path, recordingId: id);
  // t.text, t.lowConfidence
} on SttNeedsOnDeviceFallback catch (e) {
  // Backend không dùng được → hiện e.userMessage, mở OnDeviceSpeechService.start(...)
} on SttCancelled {
  // người dùng đã hủy — không làm gì
} on ApiException catch (e) {
  // NO_SPEECH_DETECTED / AUDIO_TOO_LARGE / UNSUPPORTED_AUDIO / 401 → nhờ ghi âm lại
}
// Nút Hủy:
stt.cancel(); // trả quyền về UI ngay, mic không bị treo
```

- `speech_to_text` **không chép được file đã ghi**, chỉ nghe mic trực tiếp → tầng 2 là "nói lại một lần".
- Chỉ fallback với lỗi hạ tầng: mất mạng, timeout 45s, 429, 5xx, `STT_UNAVAILABLE / STT_RATE_LIMITED / STT_TIMEOUT / STT_PROVIDER_ERROR`.
- **Phụ thuộc MOB-02 (tan):** Dio `receiveTimeout` hiện 20s → cần ≥ 45s cho `/voice/transcriptions`, nếu không Dio cắt trước.

## 3. Việc cho `qt` (`providers/focus_provider.dart`)

1. `_beginLocalRun()` (~dòng 673): truyền `phases:` như §2.2, và gọi `cancelPreSessionRemindersFor(session.id)`.
2. Màn Check-in đang mở: gọi `releaseIfCheckInExpired` như §2.4.
3. Sau khi lịch đổi (UF2 confirm / UF6 approve): gọi `schedulePreSessionReminders` như §2.1 (hoặc để tan gọi ở màn tương ứng — thống nhất 1 chỗ).

## 4. Việc cho `tan` (`screens/*`)

1. `focus_screen.dart:~297`: bỏ thẻ "Đây là mô phỏng…", thay bằng lời gọi `schedulePreSessionReminders` sau khi chốt lịch.
2. Check-in UF5 / `CalendarVoiceQuestionScreen`: dùng `VoicePlaybackService` (§2.5) và `TwoTierSttService` (§2.6).
3. Gọi `voice.stop()` trước mọi lần mở mic; `dispose()` khi rời màn.
5. Lúc bấm Start: `unawaited(voice.prefetchCheckInPrompt(...))` (cùng tham số sẽ dùng khi hỏi) → Check-in phát tức thì.
6. Nghe `AlarmService.instance.notificationTaps` / `launchPayload()`: payload `pre_session:<id>` → mở màn Chuẩn bị phiên đó.
4. MOB-02: tăng `receiveTimeout` cho `/voice/transcriptions` ≥ 45s.

## 5. Lệnh ADB cho `Thành` (W4-03B / W4-14B)

```bash
# Xem app đã đặt những báo thức nào (chuông hết phiên, chuông pha, nhắc trước phiên, DND +300s)
adb shell dumpsys alarm | grep -A3 com.proplan.proplan_va

# Ép máy vào Doze ngay (màn hình phải tắt, rút sạc)
adb shell dumpsys battery unplug
adb shell dumpsys deviceidle force-idle
adb shell dumpsys deviceidle get deep        # phải ra IDLE
# ... đợi chuông kêu, ghi lại độ lệch so với giờ hiển thị
adb shell dumpsys deviceidle unforce
adb shell dumpsys battery reset

# Trạng thái DND hiện tại (zen_mode: 0 tắt, 1 priority, 2 total, 3 alarms)
adb shell settings get global zen_mode

# Kill app giữa phiên (giả lập Android dọn RAM)
adb shell am kill com.proplan.proplan_va

# Reboot rồi kiểm báo thức còn không
adb reboot
adb wait-for-device && adb shell dumpsys alarm | grep -A3 com.proplan.proplan_va

# Log riêng của app
adb logcat --pid=$(adb shell pidof -s com.proplan.proplan_va)
```

## 6. Checklist đo trên Android

| # | Ca | Cách làm | Đạt khi |
|---|---|---|---|
| 1 | Nhắc trước phiên | Chốt phiên bắt đầu sau 16 phút, khóa màn hình | Có 3 thông báo đúng 15/10/5 phút trước |
| 2 | Đổi lịch hủy nhắc cũ | Dời phiên ở UF6 rồi duyệt | Không còn nhắc theo giờ cũ (`dumpsys alarm`) |
| 3 | Chuông pha trong Doze | Lượt Work 2p → Rest 1p → Work 2p, `force-idle` | Chuông tại mỗi ranh giới, lệch ≤ 1s |
| 4 | Chuông hết phiên trong Doze | Như trên | Lệch ≤ 1s, **không** tự đánh dấu hoàn thành |
| 5 | DND +300s khi app bị kill | Hết phiên → `am kill` → chờ 5 phút | `zen_mode` về giá trị trước phiên |
| 6 | DND +300s khi app đang mở | Hết phiên, để nguyên màn Check-in 5 phút | DND tắt + màn hình được phép tắt |
| 7 | Reboot giữa phiên | Start → `adb reboot` | Chuông hết phiên + báo thức DND còn trong `dumpsys alarm` |
| 8 | Giữ DND user bật sẵn | Bật DND tay trước, start, kết thúc | DND vẫn bật (app không phải owner) |
| 9 | TTS online | Check-in có mạng | Nghe giọng Edge-TTS |
| 10 | TTS offline | Tắt Wi-Fi/4G, Check-in | Nghe `flutter_tts`, vẫn hiện chữ |
| 11 | Thiếu giọng vi-VN | Gỡ dữ liệu giọng Việt trong cài đặt TTS | Chỉ hiện chữ, không crash |
| 12 | Mic không đè TTS | Bấm mic khi đang đọc câu hỏi | Giọng đọc dừng ngay, không phát tiếp |
| 13 | Prefetch Check-in | Start phiên 2p có mạng, tắt mạng trước khi hết giờ | Hết giờ vẫn nghe giọng Edge-TTS ngay (từ cache) |
| 14 | Chạm thông báo nhắc | Chạm thông báo "Còn 10 phút…" khi app tắt hẳn | App mở đúng phiên (khi tan đã nối `launchPayload`) |

## 7. Chưa làm / rủi ro

- `reminder_style` (UF7) chưa dùng để đổi kiểu nhắc; nhắc phía máy chưa đối chiếu với bản ghi `Reminder` (O16) của Backend.
- **UF4:** `TwoTierSttService` chỉ dùng cho UF1, Hộp nháp **sau phiên**, UF5. TUYỆT ĐỐI không gọi trong lúc đang ghi "Cất suy nghĩ" (AGENTS.md §2.2 UF4).
- Chưa gắn vào UI/Provider (tan/qt, §3–§4) → trước khi gắn, người dùng chưa thấy thay đổi.
- `TwoTierSttService` cần MOB-02 tăng `receiveTimeout`.
- Nhánh tách từ `origin/mobile`; khi `dev` hợp nhất mobile (W4-01), cần rebase/PR lại vào `dev`.
