import 'package:flutter/foundation.dart';

import '../core/api_client.dart';

/// Client wrapper for `POST /api/v1/devices/{device_id}/telemetry`.
///
/// Strictly enforces backend constraints (`extra="forbid"`, regex-safe
/// `error_code`, timezone-aware UTC `occurred_at`, `Idempotency-Key`, and zero
/// PII/file paths/transcripts).
class DeviceTelemetryService {
  DeviceTelemetryService([ApiClient? api]) : _api = api ?? ApiClient();

  static const uf4AudioCaptureFailed = 'uf4_audio_capture_failed';
  static const uf3AlarmDrift = 'uf3_alarm_drift';
  static const uf5DndRestoreFailed = 'uf5_dnd_restore_failed';

  static const Set<String> allowedEventTypes = {
    uf4AudioCaptureFailed,
    uf3AlarmDrift,
    uf5DndRestoreFailed,
  };

  final ApiClient _api;

  /// Normalizes any internal reason string into `[A-Z0-9_]{2,64}`.
  static String sanitizeErrorCode(
    String? raw, {
    String fallback = 'UNKNOWN_ERROR',
  }) {
    final upper = (raw ?? '').trim().toUpperCase();
    final replaced = upper.replaceAll(RegExp(r'[^A-Z0-9_]'), '_');
    final collapsed = replaced.replaceAll(RegExp(r'_+'), '_');
    final trimmed = collapsed.replaceAll(RegExp(r'^_+|_+$'), '');
    if (trimmed.length < 2) {
      return fallback;
    }
    return trimmed.length > 64 ? trimmed.substring(0, 64) : trimmed;
  }

  /// Short OS tag fitting the backend `max_length=32` schema constraint.
  static String currentPlatformOs() {
    if (kIsWeb) return 'web';
    return switch (defaultTargetPlatform) {
      TargetPlatform.android => 'android',
      TargetPlatform.iOS => 'ios',
      TargetPlatform.windows => 'windows',
      TargetPlatform.macOS => 'macos',
      TargetPlatform.linux => 'linux',
      TargetPlatform.fuchsia => 'fuchsia',
    };
  }

  /// Sends a telemetry event, throwing [ArgumentError] or [ApiException] on invalid/failed calls.
  Future<Map<String, dynamic>> report({
    required String deviceId,
    required String eventType,
    required String errorCode,
    String? eventId,
    int? attemptCount,
    String? sourceRunId,
    DateTime? occurredAt,
    String? appVersion = '1.0.0+1',
    String? platformOs,
  }) {
    if (!allowedEventTypes.contains(eventType)) {
      throw ArgumentError.value(
        eventType,
        'eventType',
        'Must be one of $allowedEventTypes',
      );
    }
    final resolvedDeviceId = deviceId.trim();
    if (resolvedDeviceId.isEmpty) {
      throw ArgumentError.value(deviceId, 'deviceId', 'Must not be empty');
    }
    final trimmedEventId =
        (eventId ?? ApiClient.newIdempotencyKey('evt')).trim();
    final safeEventId = trimmedEventId.isEmpty
        ? ApiClient.newIdempotencyKey('evt')
        : (trimmedEventId.length > 128
            ? trimmedEventId.substring(0, 128)
            : trimmedEventId);
    final safeAppVersion = appVersion?.trim();
    final safePlatformOs = (platformOs ?? currentPlatformOs()).trim();
    final trimmedRunId = sourceRunId?.trim();

    final payload = <String, dynamic>{
      'event_id': safeEventId,
      'event_type': eventType,
      'error_code': sanitizeErrorCode(errorCode),
      if (attemptCount != null) 'attempt_count': attemptCount.clamp(1, 5),
      if (trimmedRunId != null && trimmedRunId.isNotEmpty)
        'source_run_id': trimmedRunId,
      'occurred_at': (occurredAt ?? DateTime.now()).toUtc().toIso8601String(),
      if (safeAppVersion != null && safeAppVersion.isNotEmpty)
        'app_version': safeAppVersion.length > 32
            ? safeAppVersion.substring(0, 32)
            : safeAppVersion,
      if (safePlatformOs.isNotEmpty)
        'platform_os': safePlatformOs.length > 32
            ? safePlatformOs.substring(0, 32)
            : safePlatformOs,
    };

    return _api.postData(
      '/devices/${Uri.encodeComponent(resolvedDeviceId)}/telemetry',
      data: payload,
      idempotencyKey: safeEventId,
    );
  }

  /// Best-effort variant that returns `null` when offline or telemetry fails.
  Future<Map<String, dynamic>?> reportBestEffort({
    required String deviceId,
    required String eventType,
    required String errorCode,
    String? eventId,
    int? attemptCount,
    String? sourceRunId,
    DateTime? occurredAt,
    String? appVersion = '1.0.0+1',
    String? platformOs,
  }) async {
    try {
      return await report(
        deviceId: deviceId,
        eventType: eventType,
        errorCode: errorCode,
        eventId: eventId,
        attemptCount: attemptCount,
        sourceRunId: sourceRunId,
        occurredAt: occurredAt,
        appVersion: appVersion,
        platformOs: platformOs,
      );
    } catch (_) {
      return null;
    }
  }
}
