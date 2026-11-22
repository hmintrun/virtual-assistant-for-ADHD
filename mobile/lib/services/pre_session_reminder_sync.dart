import 'package:flutter/foundation.dart';

import '../core/api_client.dart';
import '../models/focus_session_model.dart';
import 'alarm_service.dart';
import 'schedule_service.dart';

typedef ReminderScheduler = Future<int> Function({
  required List<ReminderSession> sessions,
  List<int>? offsetsSeconds,
  DateTime? now,
});

/// Synchronizes scheduled Android pre-session reminder notifications
/// (`AlarmService.schedulePreSessionReminders`) with the user's current schedule
/// and `preferences['reminder_offsets']`.
class PreSessionReminderSync {
  PreSessionReminderSync({
    ReminderScheduler? schedule,
    Future<void> Function()? cancelAll,
    bool Function()? isAndroid,
  })  : _schedule = schedule ?? AlarmService.instance.schedulePreSessionReminders,
        _cancelAll =
            cancelAll ?? AlarmService.instance.cancelPreSessionReminders,
        _isAndroid = isAndroid ??
            (() => !kIsWeb && defaultTargetPlatform == TargetPlatform.android);

  static final PreSessionReminderSync instance = PreSessionReminderSync();

  final ReminderScheduler _schedule;
  final Future<void> Function() _cancelAll;
  final bool Function() _isAndroid;

  static List<ReminderSession> buildReminderSessions(
    List<Map<String, dynamic>> rawSessions,
  ) {
    final result = <ReminderSession>[];
    for (final row in rawSessions) {
      if (row['status'] != 'scheduled') continue;
      try {
        final info = FocusSessionInfo.fromJson(row);
        if (info.sessionId.isEmpty) continue;
        result.add(
          ReminderSession(
            sessionId: info.sessionId,
            scheduledStart: info.scheduledStart,
            stepTitle: info.stepTitle,
            plannedMinutes: (info.workSeconds / 60).ceil(),
            microTaskText: info.microTaskText,
          ),
        );
      } catch (_) {
        // Ignore malformed schedule entries so valid reminders still schedule.
      }
    }
    return result;
  }

  static List<int>? extractOffsetsSeconds(Map<String, dynamic>? preferences) {
    final raw = preferences?['reminder_offsets'];
    if (raw is! List) return null;
    final parsed = raw.whereType<num>().map((e) => e.toInt()).toList();
    return parsed.isEmpty ? null : parsed;
  }

  Future<int> sync({
    ApiClient? api,
    List<Map<String, dynamic>>? sessions,
    List<int>? offsetsSeconds,
  }) async {
    if (!_isAndroid()) return 0;
    try {
      final client = api ?? ApiClient();
      var rawSessions = sessions;
      if (rawSessions == null) {
        final now = DateTime.now();
        rawSessions = await ScheduleService(client).loadSessions(
          DateTime(now.year, now.month, now.day),
          now.add(const Duration(days: 14)),
          includeTitles: true,
        );
      }
      var offsets = offsetsSeconds;
      if (offsets == null) {
        final prefs = await client.getData('/preferences');
        offsets = extractOffsetsSeconds(prefs);
      }
      final reminderSessions = buildReminderSessions(rawSessions);
      return await _schedule(
        sessions: reminderSessions,
        offsetsSeconds: offsets,
      );
    } catch (_) {
      return 0;
    }
  }

  Future<void> cancelAll() async {
    if (!_isAndroid()) return;
    try {
      await _cancelAll();
    } catch (_) {}
  }
}
