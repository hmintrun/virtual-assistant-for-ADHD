import 'dart:async';
import 'package:sqflite/sqflite.dart';

import '../models/outbox_record.dart';
import 'local_database_manager.dart';

/// Interface cho hàng đợi Outbox cục bộ lưu trữ các thao tác cần gửi lên máy chủ.
abstract class LocalOutboxRepository {
  const LocalOutboxRepository();

  Future<String> enqueueOutboxRecord(
    OutboxRecord record, {
    Duration holdDuration = Duration.zero,
  });

  Future<bool> cancelOutboxRecord(String clientEventId, {DateTime? asOf});

  Future<List<OutboxRecord>> fetchPendingBatch({
    int limit = 20,
    DateTime? asOf,
  });

  Future<void> markSynced(String clientEventId);

  Future<void> markConflict(String clientEventId, {String? reason});

  Future<void> markFailedTerminal(String clientEventId, String reason);

  Future<void> scheduleRetry(
    String clientEventId, {
    required int nextRetrySeconds,
    String? error,
  });

  Future<OutboxRecord?> getRecord(String clientEventId);

  Future<int> getPendingCount({DateTime? asOf});

  Future<void> deleteRecord(String clientEventId);

  Future<void> clearAll();
}

/// Triển khai LocalOutboxRepository lưu trữ bền vững trên SQLite theo từng tài khoản.
class SqliteOutboxRepository extends LocalOutboxRepository {
  SqliteOutboxRepository({LocalDatabaseManager? dbManager})
      : _dbManager = dbManager ?? LocalDatabaseManager.instance;

  final LocalDatabaseManager _dbManager;

  Future<Database> _getDb() => _dbManager.getDatabase();

  @override
  Future<String> enqueueOutboxRecord(
    OutboxRecord record, {
    Duration holdDuration = Duration.zero,
  }) async {
    final db = await _getDb();
    final effectiveHoldUntil = holdDuration > Duration.zero
        ? record.occurredAt.add(holdDuration)
        : record.holdUntil;

    final readyRecord = record.copyWith(holdUntil: effectiveHoldUntil);
    await db.insert(
      'local_outbox',
      readyRecord.toMap(),
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
    return readyRecord.clientEventId;
  }

  @override
  Future<bool> cancelOutboxRecord(String clientEventId, {DateTime? asOf}) async {
    final db = await _getDb();
    final results = await db.query(
      'local_outbox',
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
      limit: 1,
    );

    if (results.isEmpty) return false;
    final record = OutboxRecord.fromMap(results.first);

    if (record.isCancellable(asOf: asOf)) {
      await db.delete(
        'local_outbox',
        where: 'client_event_id = ?',
        whereArgs: [clientEventId],
      );
      return true;
    }
    return false;
  }

  @override
  Future<List<OutboxRecord>> fetchPendingBatch({
    int limit = 20,
    DateTime? asOf,
  }) async {
    final db = await _getDb();
    final checkTimeIso = (asOf ?? DateTime.now()).toIso8601String();

    final rows = await db.query(
      'local_outbox',
      where: "(status = ? OR status = ?) AND hold_until <= ?",
      whereArgs: [OutboxStatus.pending, OutboxStatus.failed, checkTimeIso],
      orderBy: 'occurred_at ASC',
      limit: limit,
    );

    return rows.map(OutboxRecord.fromMap).toList();
  }

  @override
  Future<void> markSynced(String clientEventId) async {
    final db = await _getDb();
    await db.update(
      'local_outbox',
      {
        'status': OutboxStatus.synced,
        'last_error': null,
      },
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
    );
  }

  @override
  Future<void> markConflict(String clientEventId, {String? reason}) async {
    final db = await _getDb();
    await db.update(
      'local_outbox',
      {
        'status': OutboxStatus.conflictNeedsRebase,
        'last_error': reason,
      },
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
    );
  }

  @override
  Future<void> markFailedTerminal(String clientEventId, String reason) async {
    final db = await _getDb();
    await db.update(
      'local_outbox',
      {
        'status': OutboxStatus.failedTerminal,
        'last_error': reason,
      },
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
    );
  }

  @override
  Future<void> scheduleRetry(
    String clientEventId, {
    required int nextRetrySeconds,
    String? error,
  }) async {
    final db = await _getDb();
    final current = await getRecord(clientEventId);
    final nextRetryCount = (current?.retryCount ?? 0) + 1;
    final nextHoldUntil = DateTime.now().add(Duration(seconds: nextRetrySeconds));

    await db.update(
      'local_outbox',
      {
        'status': OutboxStatus.failed,
        'retry_count': nextRetryCount,
        'hold_until': nextHoldUntil.toIso8601String(),
        'last_error': error,
      },
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
    );
  }

  @override
  Future<OutboxRecord?> getRecord(String clientEventId) async {
    final db = await _getDb();
    final rows = await db.query(
      'local_outbox',
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return OutboxRecord.fromMap(rows.first);
  }

  @override
  Future<int> getPendingCount({DateTime? asOf}) async {
    final db = await _getDb();
    final checkTimeIso = (asOf ?? DateTime.now()).toIso8601String();
    final result = await db.rawQuery(
      '''
      SELECT COUNT(*) as count FROM local_outbox 
      WHERE (status = ? OR status = ?) AND hold_until <= ?
      ''',
      [OutboxStatus.pending, OutboxStatus.failed, checkTimeIso],
    );
    if (result.isNotEmpty && result.first.values.isNotEmpty) {
      final val = result.first.values.first;
      if (val is int) return val;
    }
    return 0;
  }

  @override
  Future<void> deleteRecord(String clientEventId) async {
    final db = await _getDb();
    await db.delete(
      'local_outbox',
      where: 'client_event_id = ?',
      whereArgs: [clientEventId],
    );
  }

  @override
  Future<void> clearAll() async {
    final db = await _getDb();
    await db.delete('local_outbox');
  }
}

/// Triển khai LocalOutboxRepository trên bộ nhớ (In-Memory) phục vụ kiểm thử đơn vị.
class InMemoryOutboxRepository extends LocalOutboxRepository {
  final Map<String, OutboxRecord> _storage = {};

  @override
  Future<String> enqueueOutboxRecord(
    OutboxRecord record, {
    Duration holdDuration = Duration.zero,
  }) async {
    final effectiveHoldUntil = holdDuration > Duration.zero
        ? record.occurredAt.add(holdDuration)
        : record.holdUntil;
    final ready = record.copyWith(holdUntil: effectiveHoldUntil);
    _storage[ready.clientEventId] = ready;
    return ready.clientEventId;
  }

  @override
  Future<bool> cancelOutboxRecord(String clientEventId, {DateTime? asOf}) async {
    final record = _storage[clientEventId];
    if (record == null) return false;
    if (record.isCancellable(asOf: asOf)) {
      _storage.remove(clientEventId);
      return true;
    }
    return false;
  }

  @override
  Future<List<OutboxRecord>> fetchPendingBatch({
    int limit = 20,
    DateTime? asOf,
  }) async {
    final checkTime = asOf ?? DateTime.now();
    final pending = _storage.values
        .where((r) => r.isReadyForReplay(asOf: checkTime))
        .toList()
      ..sort((a, b) => a.occurredAt.compareTo(b.occurredAt));

    return pending.take(limit).toList();
  }

  @override
  Future<void> markSynced(String clientEventId) async {
    final existing = _storage[clientEventId];
    if (existing != null) {
      _storage[clientEventId] = existing.copyWith(
        status: OutboxStatus.synced,
        lastError: null,
      );
    }
  }

  @override
  Future<void> markConflict(String clientEventId, {String? reason}) async {
    final existing = _storage[clientEventId];
    if (existing != null) {
      _storage[clientEventId] = existing.copyWith(
        status: OutboxStatus.conflictNeedsRebase,
        lastError: reason,
      );
    }
  }

  @override
  Future<void> markFailedTerminal(String clientEventId, String reason) async {
    final existing = _storage[clientEventId];
    if (existing != null) {
      _storage[clientEventId] = existing.copyWith(
        status: OutboxStatus.failedTerminal,
        lastError: reason,
      );
    }
  }

  @override
  Future<void> scheduleRetry(
    String clientEventId, {
    required int nextRetrySeconds,
    String? error,
  }) async {
    final existing = _storage[clientEventId];
    if (existing != null) {
      final nextHoldUntil = DateTime.now().add(Duration(seconds: nextRetrySeconds));
      _storage[clientEventId] = existing.copyWith(
        status: OutboxStatus.failed,
        retryCount: existing.retryCount + 1,
        holdUntil: nextHoldUntil,
        lastError: error,
      );
    }
  }

  @override
  Future<OutboxRecord?> getRecord(String clientEventId) async {
    return _storage[clientEventId];
  }

  @override
  Future<int> getPendingCount({DateTime? asOf}) async {
    final checkTime = asOf ?? DateTime.now();
    return _storage.values.where((r) => r.isReadyForReplay(asOf: checkTime)).length;
  }

  @override
  Future<void> deleteRecord(String clientEventId) async {
    _storage.remove(clientEventId);
  }

  @override
  Future<void> clearAll() async {
    _storage.clear();
  }
}
