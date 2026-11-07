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

  /// Trả về true nếu đủ quyền để chuông kêu đúng giờ.
  Future<bool> requestPermissions() async {
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android == null) return false;

    final notificationsOk =
        await android.requestNotificationsPermission() ?? false;
    final exactOk = await android.requestExactAlarmsPermission() ?? false;
    return notificationsOk && exactOk;
  }

  Future<void> scheduleEnd(DateTime endsAt, {required String stepTitle}) async {
    final when = tz.TZDateTime.from(endsAt.toUtc(), tz.UTC);

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
  }

  Future<void> cancelEnd() => _plugin.cancel(_endAlarmId);
}
