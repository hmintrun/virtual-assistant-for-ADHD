import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import '../models/focus_session_model.dart';
import 'alarm_service.dart';
import 'reminder_api.dart';
import 'schedule_service.dart';

typedef ReminderScheduler = Future<int> Function({
  required List<ReminderSession> sessions,
  List<int>? offsetsSeconds,
  DateTime? now,
});

/// Synchronizes scheduled Android pre-session reminder notifications
/// (`AlarmService.schedulePreSessionReminders` / `scheduleExplicitReminders`)
/// with the user's current schedule, `GET /api/v1/reminders` `dedupe_key`s,
/// and `preferences['reminder_offsets']`.
class PreSessionReminderSync {
  PreSessionReminderSync({
    ReminderScheduler? schedule,
    Future<int> Function(List<PreSessionReminder> plan)? scheduleExplicit,
    Future<void> Function(String sessionId)? cancelForSession,
    Future<void> Function()? cancelAll,
    ReminderApi Function(ApiClient)? reminderApiFactory,
    bool Function()? isAndroid,
  })  : _schedule = schedule ?? AlarmService.instance.schedulePreSessionReminders,
        _scheduleExplicit =
            scheduleExplicit ?? AlarmService.instance.scheduleExplicitReminders,
        _cancelForSession =
            cancelForSession ?? AlarmService.instance.cancelPreSessionRemindersFor,
        _cancelAll =
            cancelAll ?? AlarmService.instance.cancelPreSessionReminders,
        _reminderApiFactory = reminderApiFactory ?? _defaultReminderApi,
        _isAndroid = isAndroid ??
            (() => !kIsWeb && defaultTargetPlatform == TargetPlatform.android);

  static ReminderApi _defaultReminderApi(ApiClient api) => ReminderApi(api);

  static final PreSessionReminderSync instance = PreSessionReminderSync();
  static const String dedupeKeysStorageKey = 'pre_session_reminder_dedupe_keys';

  final ReminderScheduler _schedule;
  final Future<int> Function(List<PreSessionReminder> plan) _scheduleExplicit;
  final Future<void> Function(String sessionId) _cancelForSession;
  final Future<void> Function() _cancelAll;
  final ReminderApi Function(ApiClient) _reminderApiFactory;
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

  /// Reconciles server-issued `ReminderData` rows (`dedupe_key`,
  /// `schedule_revision`, `delivery_state`) with [sessions] into concrete local
  /// [PreSessionReminder] alarms, filtering out cancelled, delivered, or past items.
  static List<PreSessionReminder> reconcileServerReminders({
    required List<Map<String, dynamic>> serverReminders,
    required List<ReminderSession> sessions,
    required DateTime now,
  }) {
    final bySessionId = <String, ReminderSession>{
      for (final s in sessions) s.sessionId: s,
    };
    final deduped = <String, Map<String, dynamic>>{};
    for (final row in serverReminders) {
      final state = row['delivery_state'] as String? ?? 'scheduled';
      if (state == 'cancelled' || state == 'delivered' || state == 'superseded') {
        continue;
      }
      final key = (row['dedupe_key'] as String?) ??
          (row['reminder_id'] as String? ?? '');
      if (key.isEmpty) continue;
      final rev = (row['schedule_revision'] as num?)?.toInt() ?? 0;
      final existing = deduped[key];
      final existingRev =
          (existing?['schedule_revision'] as num?)?.toInt() ?? -1;
      if (existing == null || rev >= existingRev) {
        deduped[key] = row;
      }
    }

    final pending =
        <({ReminderSession session, int offset, DateTime fireAt})>[];
    for (final row in deduped.values) {
      final sessionId = row['session_id'] as String?;
      if (sessionId == null) continue;
      final session = bySessionId[sessionId];
      if (session == null || !session.scheduledStart.isAfter(now)) continue;
      final fireAtRaw = row['fire_at'] as String?;
      final parsedFireAt =
          fireAtRaw == null ? null : DateTime.tryParse(fireAtRaw)?.toLocal();
      if (parsedFireAt == null) continue;
      final fireAt = AlarmService.alignToSecond(parsedFireAt);
      if (!fireAt.isAfter(now)) continue;
      final offset = session.scheduledStart
          .difference(fireAt)
          .inSeconds
          .clamp(0, 24 * 3600);
      pending.add((session: session, offset: offset, fireAt: fireAt));
    }

    pending.sort((a, b) => a.fireAt.compareTo(b.fireAt));
    final kept =
        pending.take(AlarmService.maxPreSessionReminders).toList();
    return [
      for (var i = 0; i < kept.length; i++)
        PreSessionReminder(
          notificationId: AlarmService.preSessionIdBase + i,
          session: kept[i].session,
          offsetSeconds: kept[i].offset,
          fireAt: kept[i].fireAt,
        ),
    ];
  }

  /// Cancels local Android pre-session alarms for [sessionIds] (used after
  /// `DELETE /api/v1/tasks/{task_id}` returns `cancelled_session_ids`).
  Future<void> cancelForSessions(Iterable<String> sessionIds) async {
    if (!_isAndroid()) return;
    for (final id in sessionIds) {
      final trimmed = id.trim();
      if (trimmed.isEmpty) continue;
      try {
        await _cancelForSession(trimmed);
      } catch (_) {}
    }
  }

  Future<int> sync({
    ApiClient? api,
    List<Map<String, dynamic>>? sessions,
    List<int>? offsetsSeconds,
    int? scheduleRevision,
  }) async {
    if (!_isAndroid()) return 0;
    try {
      final client = api ?? ApiClient();
      final now = DateTime.now();
      var rawSessions = sessions;
      rawSessions ??= await ScheduleService(client).loadSessions(
        DateTime(now.year, now.month, now.day),
        now.add(const Duration(days: 14)),
        includeTitles: true,
      );
      final reminderSessions = buildReminderSessions(rawSessions);

      try {
        final prefs = await SharedPreferences.getInstance();
        final deviceId = prefs.getString('focus_device_id');
        if (deviceId != null && deviceId.isNotEmpty) {
          final serverReminders =
              await _reminderApiFactory(client).fetchUpcoming(
            deviceId: deviceId,
            from: now,
            to: now.add(const Duration(days: 14)),
            scheduleRevision: scheduleRevision,
          );
          final dedupeKeys = serverReminders
              .map((r) => r['dedupe_key'])
              .whereType<String>()
              .where((k) => k.isNotEmpty)
              .toSet()
              .toList(growable: false);
          if (dedupeKeys.isNotEmpty) {
            await prefs.setStringList(dedupeKeysStorageKey, dedupeKeys);
          }
          final reconciled = reconcileServerReminders(
            serverReminders: serverReminders,
            sessions: reminderSessions,
            now: now,
          );
          if (reconciled.isNotEmpty) {
            return await _scheduleExplicit(reconciled);
          }
        }
      } catch (_) {
        // Fallback smoothly to local schedule computation when /reminders is unavailable.
      }

      var offsets = offsetsSeconds;
      if (offsets == null) {
        final prefs = await client.getData('/preferences');
        offsets = extractOffsetsSeconds(prefs);
      }
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
