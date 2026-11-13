import '../core/api_client.dart';

class ScheduleService {
  ScheduleService(this._api);

  final ApiClient _api;

  Future<List<Map<String, dynamic>>> loadSessions(
    DateTime from,
    DateTime to, {
    bool includeTitles = false,
  }) async {
    final sessions = await _loadPages('/schedule', {
      'from': from.toUtc().toIso8601String(),
      'to': to.toUtc().toIso8601String(),
    });
    if (!includeTitles || sessions.isEmpty) return sessions;

    final steps = <String, Map<String, dynamic>>{};
    final lastSessionIds = <Object?>{};
    try {
      for (final task in await _loadPages('/tasks', const {})) {
        for (final step in (task['steps'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()) {
          final stepId = step['step_id'] as String?;
          if (stepId == null) continue;
          final time = task['time_constraint'] as Map?;
          final remaining = (task['sessions'] as List? ?? const [])
              .whereType<Map<String, dynamic>>()
              .where((session) =>
                  session['step_id'] == stepId &&
                  session['status'] == 'scheduled')
              .toList()
            ..sort((a, b) => DateTime.parse(a['scheduled_start'] as String)
                .compareTo(DateTime.parse(b['scheduled_start'] as String)));
          if (remaining.isNotEmpty) {
            lastSessionIds.add(remaining.last['session_id']);
          }
          steps[stepId] = {
            'step_title': step['title'],
            'task_title': task['title'],
            'expected_output': step['expected_output'],
            'deadline': time?['deadline_at'] ?? time?['finish_by_at'],
          };
        }
      }
    } on ApiException {
      // The calendar is usable even if titles cannot be loaded.
    }
    return [
      for (final session in sessions)
        {
          ...?steps[session['step_id']],
          ...session,
          'is_last_session_of_step':
              lastSessionIds.contains(session['session_id']),
        },
    ];
  }

  Future<List<Map<String, dynamic>>> _loadPages(
    String path,
    Map<String, dynamic> query,
  ) async {
    final items = <Map<String, dynamic>>[];
    final visited = <String>{};
    String? cursor;
    do {
      final page = await _api.getData(path, queryParameters: {
        ...query,
        'limit': 100,
        if (cursor != null) 'cursor': cursor,
      });
      items.addAll((page['items'] as List? ?? const [])
          .whereType<Map<String, dynamic>>());
      cursor = page['next_cursor'] as String?;
      if (cursor != null && !visited.add(cursor)) {
        throw const ApiException('Máy chủ trả con trỏ bị lặp.');
      }
    } while (cursor != null);
    return items;
  }
}
