import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

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

  Future<void> init() async {
    tzdata.initializeTimeZones();
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    await _plugin.initialize(const InitializationSettings(android: android));
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
}
