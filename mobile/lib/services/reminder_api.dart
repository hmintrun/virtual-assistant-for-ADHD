import '../core/api_client.dart';

/// Client wrapper for UF3 server reminder schedule & delivery receipts.
class ReminderApi {
  ReminderApi([ApiClient? api]) : _api = api ?? ApiClient();

  final ApiClient _api;

  Future<List<Map<String, dynamic>>> fetchUpcoming({
    required String deviceId,
    DateTime? from,
    DateTime? to,
    int? scheduleRevision,
  }) async {
    final data = await _api.getData(
      '/reminders',
      queryParameters: {
        'device_id': deviceId,
        if (from != null) 'from': from.toUtc().toIso8601String(),
        if (to != null) 'to': to.toUtc().toIso8601String(),
        if (scheduleRevision != null) 'schedule_revision': scheduleRevision,
      },
    );
    final items = data['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList(growable: false);
  }

  Future<Map<String, dynamic>> reportReceipt({
    required String reminderId,
    required String deviceId,
    required int scheduleRevision,
    required String deliveryState,
    DateTime? occurredAt,
    String? userAction,
  }) {
    final timestamp = (occurredAt ?? DateTime.now()).toUtc().toIso8601String();
    return _api.postData(
      '/reminders/${Uri.encodeComponent(reminderId)}/receipt',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'reminder_receipt_$reminderId',
        '$deviceId:$scheduleRevision:$deliveryState:${userAction ?? ''}',
      ),
      data: {
        'device_id': deviceId,
        'schedule_revision': scheduleRevision,
        'delivery_state': deliveryState,
        'occurred_at': timestamp,
        if (userAction != null && userAction.trim().isNotEmpty)
          'user_action': userAction.trim(),
      },
    );
  }
}
