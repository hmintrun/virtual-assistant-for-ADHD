import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';

enum CalendarSyncRetryStatus {
  retried,
  reloadRequired,
  cancelled,
  invalidState,
  reconnectRequired,
}

class CalendarSyncRetryResult {
  const CalendarSyncRetryResult({
    required this.status,
    this.syncJob,
    this.message,
  });

  final CalendarSyncRetryStatus status;
  final Map<String, dynamic>? syncJob;
  final String? message;
}

Future<CalendarSyncRetryResult> executeCalendarSyncRetryWithHandling({
  required Future<Map<String, dynamic>> Function() retryCall,
  required Future<Map<String, dynamic>> Function() reloadCall,
}) async {
  try {
    final data = await retryCall();
    return CalendarSyncRetryResult(
      status: CalendarSyncRetryStatus.retried,
      syncJob: data,
    );
  } on ApiException catch (error) {
    if (error.requiresCalendarReconnect) {
      return CalendarSyncRetryResult(
        status: CalendarSyncRetryStatus.reconnectRequired,
        message: error.message,
      );
    }
    if (error.isRevisionConflict) {
      Map<String, dynamic>? latest;
      try {
        latest = await reloadCall();
      } catch (_) {}
      return CalendarSyncRetryResult(
        status: CalendarSyncRetryStatus.reloadRequired,
        syncJob: latest,
        message: error.message,
      );
    }
    if (error.isJobCancelled) {
      return CalendarSyncRetryResult(
        status: CalendarSyncRetryStatus.cancelled,
        message: error.message,
      );
    }
    if (error.code == 'INVALID_STATE_TRANSITION') {
      return CalendarSyncRetryResult(
        status: CalendarSyncRetryStatus.invalidState,
        message: error.message,
      );
    }
    rethrow;
  }
}

class GoogleCalendarApi {
  GoogleCalendarApi(this._api);

  final ApiClient _api;

  static String _connection(String id) =>
      '/calendar/connections/${Uri.encodeComponent(id)}';

  Future<Uri> authorize({String? returnTarget}) async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString('focus_device_id');
    final device = stored ?? ApiClient.newIdempotencyKey('device');
    if (stored == null) await prefs.setString('focus_device_id', device);
    final data = await _api.postData(
      '/calendar/authorizations',
      data: {
        'device_id': device,
        if (returnTarget != null) 'return_target': returnTarget,
      },
      idempotent: false,
    );
    final uri = Uri.tryParse(data['auth_url'] as String? ?? '');
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host != 'accounts.google.com') {
      throw const ApiException(
          'Máy chủ chưa trả liên kết cấp quyền Google hợp lệ.');
    }
    return uri;
  }

  Future<List<Map<String, dynamic>>> connections() =>
      _api.getListData('/calendar/connections');

  Future<List<Map<String, dynamic>>> calendars(String connectionId) async {
    final data = await _api.getData('${_connection(connectionId)}/calendars');
    return (data['items'] as List).whereType<Map<String, dynamic>>().toList();
  }

  Future<List<Map<String, dynamic>>> events(
    String connectionId,
    String calendarId,
    DateTime from,
    DateTime to,
  ) =>
      _api.getListData('${_connection(connectionId)}/events', queryParameters: {
        'google_calendar_id': calendarId,
        'from': from.toUtc().toIso8601String(),
        'to': to.toUtc().toIso8601String(),
        'limit': 250,
      });

  Future<({String id, int? revision})> previewWriteCalendar(
    String connectionId,
    List<String> readIds,
    String? writeId, {
    required bool createCalendar,
  }) async {
    final data = await _api.postData(
      '${_connection(connectionId)}/configuration-previews',
      data: {
        'read_calendar_ids': readIds,
        'write_calendar_id': writeId,
        'existing_event_action': createCalendar ? 'provision_new' : 'keep',
      },
      idempotent: false,
    );
    return (
      id: data['preview_id'] as String,
      revision: data['preview_revision'] as int?,
    );
  }

  Future<void> confirmPreview(({String id, int? revision}) preview) async {
    await _api.postData(
      '/calendar/configuration-previews/${Uri.encodeComponent(preview.id)}/confirm',
      data: {
        if (preview.revision case final int revision)
          'expected_preview_revision': revision,
      },
      idempotent: false,
    );
  }

  Future<void> disconnect(String connectionId) async {
    await _api.postData('${_connection(connectionId)}/disconnect',
        idempotent: false);
  }

  Future<Map<String, dynamic>> refreshConnection(
    String connectionId, {
    DateTime? from,
    DateTime? to,
    int? expectedConnectionRevision,
  }) =>
      _api.postData(
        '${_connection(connectionId)}/refresh',
        data: {
          if (from != null) 'from_at': from.toUtc().toIso8601String(),
          if (to != null) 'to_at': to.toUtc().toIso8601String(),
          if (expectedConnectionRevision != null)
            'expected_connection_revision': expectedConnectionRevision,
        },
        idempotent: false,
      );

  Future<Map<String, dynamic>> getSyncJob(String jobId) =>
      _api.getData('/calendar/sync-jobs/${Uri.encodeComponent(jobId)}');

  Future<Map<String, dynamic>> retrySyncJob(
    String jobId, {
    int? expectedJobRevision,
  }) =>
      _api.postData(
        '/calendar/sync-jobs/${Uri.encodeComponent(jobId)}/retry',
        data: {
          if (expectedJobRevision != null)
            'expected_job_revision': expectedJobRevision,
        },
        idempotent: false,
      );

  Future<CalendarSyncRetryResult> retrySyncJobWithHandling(
    String jobId, {
    int? expectedJobRevision,
  }) =>
      executeCalendarSyncRetryWithHandling(
        retryCall: () =>
            retrySyncJob(jobId, expectedJobRevision: expectedJobRevision),
        reloadCall: () => getSyncJob(jobId),
      );
}
