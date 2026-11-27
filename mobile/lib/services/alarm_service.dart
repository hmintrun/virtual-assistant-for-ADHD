import 'dart:async';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timezone/timezone.dart' as tz;

/// Một phiên sắp tới cần nhắc trước (UF3). Chỉ chứa dữ liệu hiển thị, không
/// chứa trạng thái nghiệp vụ.
class ReminderSession {
  const ReminderSession({
    required this.sessionId,
    required this.scheduledStart,
    required this.stepTitle,
    this.plannedMinutes,
    this.microTaskText,
  });

  final String sessionId;
  final DateTime scheduledStart;
  final String stepTitle;

  /// Thời lượng phiên, để nội dung nhắc ghi rõ "phiên 25 phút".
  final int? plannedMinutes;

  /// Gợi ý Micro-task khởi động 2 phút hiển thị CÙNG lời nhắc (Project.md UF3).
  /// Chỉ là gợi ý: không có timer riêng, không bắt đánh dấu xong.
  final String? microTaskText;
}

/// Một đoạn trong lượt Focus: `work` (làm) hoặc `rest` (nghỉ). Trùng với
/// `FocusSegment.kind` / `durationSeconds` mà FocusProvider đang dùng.
class FocusPhase {
  const FocusPhase({required this.kind, required this.durationSeconds});

  final String kind;
  final int durationSeconds;

  bool get isRest => kind == 'rest';

  Map<String, dynamic> toJson() =>
      {'kind': kind, 'duration_seconds': durationSeconds};

  factory FocusPhase.fromJson(Map<String, dynamic> json) => FocusPhase(
        kind: json['kind'] as String,
        durationSeconds: json['duration_seconds'] as int,
      );
}

/// Chuông tại ranh giới giữa 2 đoạn (Work → Rest hoặc Rest → Work).
class PhaseAlarm {
  const PhaseAlarm({
    required this.notificationId,
    required this.fireAt,
    required this.next,
  });

  final int notificationId;
  final DateTime fireAt;

  /// Đoạn bắt đầu tại mốc này.
  final FocusPhase next;
}

/// Một mốc nhắc đã được tính (đã bỏ mốc quá khứ).
class PreSessionReminder {
  const PreSessionReminder({
    required this.notificationId,
    required this.session,
    required this.offsetSeconds,
    required this.fireAt,
  });

  final int notificationId;
  final ReminderSession session;

  /// Số GIÂY trước giờ bắt đầu (cùng đơn vị với `reminder_offsets` của Backend).
  final int offsetSeconds;
  final DateTime fireAt;
}

/// Chuông kết thúc phiên Focus (UF3), kêu đúng giờ kể cả khi máy ở Doze Mode.
/// CHỈ phát chuông — KHÔNG tự đánh dấu hoàn thành phiên (AGENTS.md 2.1, xác nhận ở UF5).
class AlarmService {
  AlarmService._();
  static final AlarmService instance = AlarmService._();

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  static const int _endAlarmId = 9001;
  static const String _channelId = 'focus_session_end';
  static const String _channelName = 'Chuông kết thúc phiên';

  final StreamController<String> _taps = StreamController<String>.broadcast();

  /// Payload mỗi khi người dùng chạm vào thông báo của app khi app đang chạy
  /// (vd. `pre_session:<sessionId>` → UI mở màn Chuẩn bị của phiên đó).
  Stream<String> get notificationTaps => _taps.stream;

  Future<void> init() async {
    tz.setLocalLocation(tz.UTC);
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    await _plugin.initialize(
      const InitializationSettings(android: android),
      onDidReceiveNotificationResponse: (response) {
        final payload = response.payload;
        if (payload != null && payload.isNotEmpty) _taps.add(payload);
      },
    );
  }

  /// Payload của thông báo đã MỞ app từ trạng thái tắt hẳn (null nếu mở bình thường).
  Future<String?> launchPayload() async {
    final details = await _plugin.getNotificationAppLaunchDetails();
    if (details?.didNotificationLaunchApp != true) return null;
    return details?.notificationResponse?.payload;
  }

  /// Hàng đợi tuần tự cho các thao tác "hủy bộ cũ → đặt bộ mới": hai lời gọi
  /// chồng nhau (vd. duyệt UF6 đúng lúc mở app) không để lại báo thức mồ côi.
  Future<void> _queue = Future<void>.value();

  Future<T> _exclusive<T>(Future<T> Function() task) {
    final result = _queue.then((_) => task());
    _queue = result.then<void>((_) {}, onError: (_) {});
    return result;
  }

  AndroidFlutterLocalNotificationsPlugin? get _android =>
      _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();

  Future<bool> requestNotificationPermission() async =>
      await _android?.requestNotificationsPermission() ?? false;

  Future<bool> requestExactAlarmPermission() async {
    final android = _android;
    if (android == null) return false;
    if (await android.canScheduleExactNotifications() == true) return true;
    return await android.requestExactAlarmsPermission() ?? false;
  }

  /// Trả về true nếu đủ quyền để chuông kêu đúng giờ.
  Future<bool> requestPermissions() async {
    final notificationsOk = await requestNotificationPermission();
    final exactOk = await requestExactAlarmPermission();
    return notificationsOk && exactOk;
  }

  /// Làm tròn LÊN giây chẵn kế tiếp (14:58:00.900 → 14:58:01.000).
  ///
  /// flutter_local_notifications chỉ gửi giờ xuống Android ở dạng
  /// `yyyy-mm-dd hh:mm:ss` (cắt bỏ mili-giây) → nếu không làm tròn, chuông có
  /// thể kêu SỚM tới ~1 giây so với đồng hồ trên màn hình. Nơi tạo phiên phải
  /// dùng cùng mốc đã làm tròn này cho cả đồng hồ hiển thị và chuông.
  static DateTime alignToSecond(DateTime time) {
    final extraMicros = time.millisecond * 1000 + time.microsecond;
    if (extraMicros == 0) return time;
    return time.add(Duration(microseconds: 1000000 - extraMicros));
  }

  /// Đặt chuông kết thúc phiên. Trả về mốc thực sự đã đặt (đã làm tròn giây).
  Future<DateTime> scheduleEnd(
    DateTime endsAt, {
    required String stepTitle,
  }) async {
    final aligned = alignToSecond(endsAt);
    final when = tz.TZDateTime.from(aligned.toUtc(), tz.UTC);

    await _plugin.zonedSchedule(
      _endAlarmId,
      'Hết phiên tập trung',
      'Bạn làm "$stepTitle" tới đâu rồi?',
      when,
      const NotificationDetails(
        android: AndroidNotificationDetails(
          _channelId,
          _channelName,
          importance: Importance.max,
          priority: Priority.high,
          category: AndroidNotificationCategory.alarm,
          audioAttributesUsage: AudioAttributesUsage.alarm,
          playSound: true,
        ),
      ),
      androidScheduleMode: AndroidScheduleMode.alarmClock,
      uiLocalNotificationDateInterpretation:
          UILocalNotificationDateInterpretation.absoluteTime,
    );
    return aligned;
  }

  Future<void> cancelEnd() => _plugin.cancel(_endAlarmId);

  // ---------------------------------------------------------------------------
  // Chuông chuyển pha Work ↔ Rest (UF3, MOB-06)
  // ---------------------------------------------------------------------------

  static const int phaseIdBase = 9100;
  static const int maxPhaseAlarms = 50;
  static const String _phaseIdsKey = 'phase_alarm_ids';
  static const String _phaseChannelId = 'focus_phase_change';
  static const String _phaseChannelName = 'Chuông chuyển pha làm/nghỉ';

  /// Tính chuông tại mỗi ranh giới đoạn (hàm thuần → test được).
  ///
  /// Mốc được tính LÙI từ [endsAt] — đúng cách FocusProvider tính hạn từng
  /// đoạn (`runEndsAt - tổng các đoạn phía sau`) — nên chuông khớp tuyệt đối
  /// với đồng hồ trên màn hình. Không đặt chuông ở cuối đoạn cuối: đó là
  /// chuông hết phiên [scheduleEnd].
  static List<PhaseAlarm> planPhaseAlarms({
    required DateTime endsAt,
    required List<FocusPhase> phases,
    required DateTime now,
  }) {
    final usable = phases.where((p) => p.durationSeconds > 0).toList();
    final alarms = <PhaseAlarm>[];
    var secondsAfter = 0;
    // Duyệt từ đoạn cuối về đầu: ranh giới trước đoạn i nằm ở endsAt - (tổng i..n).
    for (var i = usable.length - 1; i >= 1; i--) {
      secondsAfter += usable[i].durationSeconds;
      final next = usable[i];
      // Hai đoạn liền nhau cùng loại → không đổi pha → không kêu.
      if (next.kind == usable[i - 1].kind) continue;
      final fireAt = endsAt.subtract(Duration(seconds: secondsAfter));
      if (!fireAt.isAfter(now)) continue;
      alarms.add(PhaseAlarm(notificationId: 0, fireAt: fireAt, next: next));
    }
    alarms.sort((a, b) => a.fireAt.compareTo(b.fireAt));
    final kept = alarms.take(maxPhaseAlarms).toList();
    return [
      for (var i = 0; i < kept.length; i++)
        PhaseAlarm(
          notificationId: phaseIdBase + i,
          fireAt: kept[i].fireAt,
          next: kept[i].next,
        ),
    ];
  }

  /// Nội dung chuông chuyển pha: nói rõ đang chuyển sang gì và bao lâu.
  static ({String title, String body}) phaseText(
    PhaseAlarm alarm, {
    required String stepTitle,
  }) {
    final minutes = (alarm.next.durationSeconds / 60).ceil();
    if (alarm.next.isRest) {
      return (
        title: 'Hết giờ làm · nghỉ $minutes phút',
        body: 'Đứng dậy, uống nước. Chuông sẽ báo khi hết giờ nghỉ.',
      );
    }
    return (
      title: 'Hết giờ nghỉ · vào làm $minutes phút',
      body: stepTitle,
    );
  }

  /// Đặt chuông cho mọi ranh giới Work ↔ Rest của lượt hiện tại (thay bộ cũ).
  /// Chuông CHỈ báo chuyển pha, KHÔNG đánh dấu hoàn thành gì (AGENTS.md 2.1).
  /// Trả về số chuông đã đặt.
  Future<int> schedulePhaseAlarms({
    required DateTime endsAt,
    required List<FocusPhase> phases,
    required String stepTitle,
    DateTime? now,
  }) =>
      _exclusive(() => _schedulePhaseAlarms(
            endsAt: endsAt,
            phases: phases,
            stepTitle: stepTitle,
            now: now,
          ));

  Future<int> _schedulePhaseAlarms({
    required DateTime endsAt,
    required List<FocusPhase> phases,
    required String stepTitle,
    DateTime? now,
  }) async {
    await _cancelPhaseAlarms();
    final plan = planPhaseAlarms(
      endsAt: endsAt,
      phases: phases,
      now: now ?? DateTime.now(),
    );
    final ids = <int>[];
    for (final alarm in plan) {
      final text = phaseText(alarm, stepTitle: stepTitle);
      try {
        await _plugin.zonedSchedule(
          alarm.notificationId,
          text.title,
          text.body,
          tz.TZDateTime.from(alarm.fireAt.toUtc(), tz.UTC),
          const NotificationDetails(
            android: AndroidNotificationDetails(
              _phaseChannelId,
              _phaseChannelName,
              importance: Importance.max,
              priority: Priority.high,
              category: AndroidNotificationCategory.alarm,
              audioAttributesUsage: AudioAttributesUsage.alarm,
              playSound: true,
            ),
          ),
          // alarmClock: kêu đúng giờ cả trong Doze, giống chuông hết phiên.
          androidScheduleMode: AndroidScheduleMode.alarmClock,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
        );
        ids.add(alarm.notificationId);
      } catch (_) {
        // Thiếu quyền: đồng hồ trên màn hình vẫn chuyển pha bình thường.
      }
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(_phaseIdsKey, ids.map((id) => '$id').toList());
    return ids.length;
  }

  Future<void> cancelPhaseAlarms() => _exclusive(_cancelPhaseAlarms);

  Future<void> _cancelPhaseAlarms() async {
    final prefs = await SharedPreferences.getInstance();
    for (final raw in prefs.getStringList(_phaseIdsKey) ?? const <String>[]) {
      final id = int.tryParse(raw);
      if (id != null) await _plugin.cancel(id);
    }
    await prefs.remove(_phaseIdsKey);
  }

  // ---------------------------------------------------------------------------
  // Nhắc trước phiên 15/10/5 phút (UF3, MOB-06)
  // ---------------------------------------------------------------------------

  /// Mốc nhắc mặc định khi người dùng chưa cấu hình — ĐƠN VỊ GIÂY, trùng
  /// `default_reminder_offsets` của Backend (`[900, 600, 300]` = 15/10/5 phút).
  static const List<int> defaultReminderOffsetsSeconds = [900, 600, 300];

  /// Backend nhận tối đa 3 mốc.
  static const int maxReminderOffsets = 3;

  /// Dải ID riêng cho nhắc trước phiên, không đụng chuông hết phiên (9001),
  /// báo thức trả DND (9002) hay chuông chuyển pha (9100+).
  static const int preSessionIdBase = 20000;

  /// Android giới hạn ~500 báo thức/app → chỉ giữ các mốc gần nhất.
  static const int maxPreSessionReminders = 60;

  /// Mỗi phần tử: `"<notificationId>|<sessionId>"` để hủy được theo phiên.
  static const String _preSessionIdsKey = 'pre_session_reminder_ids';
  static const String _preSessionChannelId = 'focus_pre_session';
  static const String _preSessionChannelName = 'Nhắc trước phiên tập trung';

  /// Payload khi người dùng chạm thông báo nhắc: `pre_session:<sessionId>`.
  static const String preSessionPayloadPrefix = 'pre_session:';

  /// Chuẩn hóa `reminder_offsets` (giây) theo đúng luật Backend: số nguyên
  /// không âm, không trùng, giảm dần, tối đa 3 mốc. Rỗng/null → mặc định.
  static List<int> normalizeOffsetsSeconds(List<int>? offsets) {
    final source = (offsets == null || offsets.isEmpty)
        ? defaultReminderOffsetsSeconds
        : offsets;
    final cleaned = source
        .where((sec) => sec >= 0 && sec <= 24 * 3600)
        .toSet()
        .toList()
      ..sort((a, b) => b.compareTo(a));
    return cleaned.take(maxReminderOffsets).toList();
  }

  /// Tính các mốc nhắc cần đặt (hàm thuần, không gọi plugin → test được).
  ///
  /// - [offsetsSeconds] truyền NGUYÊN `preferences['reminder_offsets']` (giây).
  /// - Bỏ mốc đã qua (`fireAt <= now`) và phiên đã bắt đầu — không phát dồn
  ///   các mốc đã qua (Project.md UF3).
  /// - Xếp theo thời điểm kêu, chỉ giữ [maxPreSessionReminders] mốc gần nhất.
  static List<PreSessionReminder> planPreSessionReminders({
    required List<ReminderSession> sessions,
    required List<int>? offsetsSeconds,
    required DateTime now,
  }) {
    final offsets = normalizeOffsetsSeconds(offsetsSeconds);
    final pending =
        <({ReminderSession session, int offset, DateTime fireAt})>[];
    final seen = <String>{};
    for (final session in sessions) {
      if (!seen.add(session.sessionId)) continue;
      if (!session.scheduledStart.isAfter(now)) continue;
      for (final offset in offsets) {
        final fireAt = alignToSecond(
          session.scheduledStart.subtract(Duration(seconds: offset)),
        );
        if (!fireAt.isAfter(now)) continue;
        pending.add((session: session, offset: offset, fireAt: fireAt));
      }
    }
    pending.sort((a, b) => a.fireAt.compareTo(b.fireAt));
    final kept = pending.take(maxPreSessionReminders).toList();
    return [
      for (var i = 0; i < kept.length; i++)
        PreSessionReminder(
          notificationId: preSessionIdBase + i,
          session: kept[i].session,
          offsetSeconds: kept[i].offset,
          fireAt: kept[i].fireAt,
        ),
    ];
  }

  /// Nội dung thông báo nhắc: nhãn cụ thể, kèm gợi ý Micro-task, không gây áp
  /// lực (COGA).
  static ({String title, String body}) preSessionText(
    PreSessionReminder reminder,
  ) {
    final minutes = (reminder.offsetSeconds / 60).ceil();
    final title = minutes <= 0
        ? 'Đến giờ vào phiên tập trung'
        : 'Còn $minutes phút nữa vào phiên tập trung';
    final planned = reminder.session.plannedMinutes;
    final length = planned == null ? '' : ' · phiên $planned phút';
    final micro = reminder.session.microTaskText?.trim();
    final hint =
        (micro == null || micro.isEmpty) ? '' : '\nKhởi động 2 phút: $micro';
    return (title: title, body: '${reminder.session.stepTitle}$length$hint');
  }

  /// Thay TOÀN BỘ nhắc trước phiên bằng danh sách mới (gọi sau khi chốt lịch
  /// UF2, duyệt UF6, đổi `reminder_offsets` UF7 hoặc khi mở app).
  ///
  /// Hủy bộ nhắc cũ trước rồi mới đặt bộ mới → không bao giờ nhắc phiên đã
  /// dời/xóa. Thông báo đã đặt được flutter_local_notifications tự khôi phục
  /// sau khi khởi động lại máy (`ScheduledNotificationBootReceiver`).
  ///
  /// Trả về số mốc đã đặt thành công.
  Future<int> schedulePreSessionReminders({
    required List<ReminderSession> sessions,
    List<int>? offsetsSeconds,
    DateTime? now,
  }) =>
      _exclusive(() => _schedulePreSessionReminders(
            sessions: sessions,
            offsetsSeconds: offsetsSeconds,
            now: now,
          ));

  /// Đặt danh sách nhắc trước phiên đã được đối chiếu từ máy chủ (`GET /reminders`).
  Future<int> scheduleExplicitReminders(List<PreSessionReminder> plan) =>
      _exclusive(() => _applyPreSessionReminderPlan(plan));

  Future<int> _schedulePreSessionReminders({
    required List<ReminderSession> sessions,
    List<int>? offsetsSeconds,
    DateTime? now,
  }) async {
    final plan = planPreSessionReminders(
      sessions: sessions,
      offsetsSeconds: offsetsSeconds,
      now: now ?? DateTime.now(),
    );
    return _applyPreSessionReminderPlan(plan);
  }

  Future<int> _applyPreSessionReminderPlan(List<PreSessionReminder> plan) async {
    await _cancelPreSessionReminders();
    final entries = <String>[];
    for (final reminder in plan) {
      final text = preSessionText(reminder);
      try {
        await _plugin.zonedSchedule(
          reminder.notificationId,
          text.title,
          text.body,
          tz.TZDateTime.from(reminder.fireAt.toUtc(), tz.UTC),
          NotificationDetails(
            android: AndroidNotificationDetails(
              _preSessionChannelId,
              _preSessionChannelName,
              // Mức mặc định: có âm báo nhưng KHÔNG bật pop-up đè màn hình
              // (AGENTS.md §4: cấm pop-up bất ngờ).
              importance: Importance.defaultImportance,
              priority: Priority.defaultPriority,
              category: AndroidNotificationCategory.reminder,
              styleInformation: BigTextStyleInformation(text.body),
            ),
          ),
          androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
          payload: '$preSessionPayloadPrefix${reminder.session.sessionId}',
        );
        entries.add('${reminder.notificationId}|${reminder.session.sessionId}');
      } catch (_) {
        // Thiếu quyền báo thức chính xác: bỏ qua mốc này, các mốc khác vẫn đặt.
      }
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(_preSessionIdsKey, entries);
    return entries.length;
  }

  /// Hủy nhắc còn lại của 1 phiên — gọi khi người dùng bấm Bắt đầu sớm, để
  /// không phát nhắc thừa sau start (Project.md UF3).
  Future<void> cancelPreSessionRemindersFor(String sessionId) =>
      _exclusive(() => _cancelPreSessionRemindersFor(sessionId));

  Future<void> _cancelPreSessionRemindersFor(String sessionId) async {
    final prefs = await SharedPreferences.getInstance();
    final entries = prefs.getStringList(_preSessionIdsKey) ?? const <String>[];
    final keep = <String>[];
    for (final entry in entries) {
      final parsed = parseReminderEntry(entry);
      if (parsed != null && parsed.sessionId == sessionId) {
        await _plugin.cancel(parsed.id);
      } else {
        keep.add(entry);
      }
    }
    await prefs.setStringList(_preSessionIdsKey, keep);
  }

  /// Hủy mọi nhắc trước phiên đã đặt (đăng xuất, xóa Task...).
  Future<void> cancelPreSessionReminders() =>
      _exclusive(_cancelPreSessionReminders);

  Future<void> _cancelPreSessionReminders() async {
    final prefs = await SharedPreferences.getInstance();
    for (final entry in prefs.getStringList(_preSessionIdsKey) ?? const []) {
      final parsed = parseReminderEntry(entry);
      if (parsed != null) await _plugin.cancel(parsed.id);
    }
    await prefs.remove(_preSessionIdsKey);
  }

  /// Đọc `"<id>|<sessionId>"` (chấp nhận bản cũ chỉ có `"<id>"`).
  static ({int id, String? sessionId})? parseReminderEntry(String entry) {
    final cut = entry.indexOf('|');
    final id = int.tryParse(cut < 0 ? entry : entry.substring(0, cut));
    if (id == null) return null;
    return (id: id, sessionId: cut < 0 ? null : entry.substring(cut + 1));
  }
}
