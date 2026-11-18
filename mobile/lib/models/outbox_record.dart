import 'dart:convert';
import 'dart:math';

/// Các loại sự kiện được đẩy vào hàng đợi outbox ngoại tuyến.
class OutboxEventType {
  static const String checkinCreate = 'checkin_create';
  static const String checkinResponse = 'checkin_response';
  static const String thoughtCapture = 'thought_capture';
  static const String runEventBatch = 'run_event_batch';
  static const String actualCorrection = 'actual_correction';
}

/// Trạng thái xử lý của một bản ghi outbox trong SQLite.
class OutboxStatus {
  static const String pending = 'pending';
  static const String synced = 'synced';
  static const String conflictNeedsRebase = 'conflict_needs_rebase';
  static const String failedTerminal = 'failed_terminal';
  static const String failed = 'failed';
}

/// Mô hình một bản ghi trong hàng đợi `local_outbox`.
class OutboxRecord {
  OutboxRecord({
    required this.clientEventId,
    required this.eventType,
    this.targetResourceId,
    required this.endpoint,
    required this.httpMethod,
    required this.payload,
    required DateTime occurredAt,
    required DateTime holdUntil,
    this.status = OutboxStatus.pending,
    this.retryCount = 0,
    this.lastError,
    DateTime? createdAt,
    DateTime? syncedAt,
  })  : occurredAt = occurredAt.toUtc(),
        holdUntil = holdUntil.toUtc(),
        createdAt = createdAt?.toUtc(),
        syncedAt = syncedAt?.toUtc();

  final String clientEventId;
  final String eventType;
  final String? targetResourceId;
  final String endpoint;
  final String httpMethod;
  final Map<String, dynamic> payload;
  final DateTime occurredAt;
  final DateTime holdUntil;
  final String status;
  final int retryCount;
  final String? lastError;
  final DateTime? createdAt;
  final DateTime? syncedAt;

  /// Tạo một client_event_id ngẫu nhiên chuẩn an toàn cho cả Web và Native.
  static String generateId([String prefix = 'evt']) {
    final secureRandom = Random.secure();
    final randomHex = List.generate(
      4,
      (_) => secureRandom.nextInt(0x10000).toRadixString(16).padLeft(4, '0'),
    ).join();
    return '${prefix}_${DateTime.now().microsecondsSinceEpoch}_$randomHex';
  }

  /// Kiểm tra xem bản ghi đã hết thời gian giam giữ (hold_until) và sẵn sàng replay hay chưa.
  bool isReadyForReplay({DateTime? asOf}) {
    final checkTime = (asOf ?? DateTime.now()).toUtc();
    final isAllowedStatus = status == OutboxStatus.pending || status == OutboxStatus.failed;
    return isAllowedStatus && !checkTime.isBefore(holdUntil.toUtc());
  }

  /// Kiểm tra xem bản ghi còn trong cửa sổ cho phép hủy (Undo 5s) hay không.
  bool isCancellable({DateTime? asOf}) {
    final checkTime = (asOf ?? DateTime.now()).toUtc();
    return status == OutboxStatus.pending && checkTime.isBefore(holdUntil.toUtc());
  }

  Map<String, dynamic> toMap() => {
        'client_event_id': clientEventId,
        'event_type': eventType,
        'target_resource_id': targetResourceId,
        'endpoint': endpoint,
        'http_method': httpMethod,
        'payload': jsonEncode(payload),
        'occurred_at': occurredAt.toUtc().toIso8601String(),
        'hold_until': holdUntil.toUtc().toIso8601String(),
        'status': status,
        'retry_count': retryCount,
        'last_error': lastError,
        'created_at': (createdAt ?? occurredAt).toUtc().toIso8601String(),
        'synced_at': syncedAt?.toUtc().toIso8601String(),
      };

  factory OutboxRecord.fromMap(Map<String, dynamic> map) {
    dynamic rawPayload = map['payload'];
    Map<String, dynamic> parsedPayload = {};
    if (rawPayload is String) {
      try {
        parsedPayload = jsonDecode(rawPayload) as Map<String, dynamic>;
      } catch (_) {
        parsedPayload = {'raw': rawPayload};
      }
    } else if (rawPayload is Map<String, dynamic>) {
      parsedPayload = rawPayload;
    }

    return OutboxRecord(
      clientEventId: map['client_event_id'] as String,
      eventType: map['event_type'] as String,
      targetResourceId: map['target_resource_id'] as String?,
      endpoint: map['endpoint'] as String,
      httpMethod: map['http_method'] as String,
      payload: parsedPayload,
      occurredAt: DateTime.parse(map['occurred_at'] as String).toUtc(),
      holdUntil: DateTime.parse(map['hold_until'] as String).toUtc(),
      status: map['status'] as String? ?? OutboxStatus.pending,
      retryCount: map['retry_count'] as int? ?? 0,
      lastError: map['last_error'] as String?,
      createdAt: map['created_at'] != null ? DateTime.parse(map['created_at'] as String).toUtc() : null,
      syncedAt: map['synced_at'] != null ? DateTime.parse(map['synced_at'] as String).toUtc() : null,
    );
  }

  OutboxRecord copyWith({
    String? status,
    int? retryCount,
    String? lastError,
    bool clearLastError = false,
    DateTime? holdUntil,
    Map<String, dynamic>? payload,
    String? targetResourceId,
    DateTime? createdAt,
    DateTime? syncedAt,
  }) =>
      OutboxRecord(
        clientEventId: clientEventId,
        eventType: eventType,
        targetResourceId: targetResourceId ?? this.targetResourceId,
        endpoint: endpoint,
        httpMethod: httpMethod,
        payload: payload ?? this.payload,
        occurredAt: occurredAt.toUtc(),
        holdUntil: (holdUntil ?? this.holdUntil).toUtc(),
        status: status ?? this.status,
        retryCount: retryCount ?? this.retryCount,
        lastError: clearLastError ? null : (lastError ?? this.lastError),
        createdAt: (createdAt ?? this.createdAt)?.toUtc(),
        syncedAt: (syncedAt ?? this.syncedAt)?.toUtc(),
      );
}
