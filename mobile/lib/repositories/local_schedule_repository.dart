import 'dart:async';
import 'dart:convert';
import 'package:sqflite/sqflite.dart';

import 'local_database_manager.dart';

/// Mục lịch làm việc chính thức được lưu cache cục bộ trong SQLite.
class CachedScheduleItem {
  const CachedScheduleItem({
    required this.sessionId,
    this.runId,
    required this.taskId,
    required this.stepId,
    required this.plannedStart,
    required this.plannedEnd,
    required this.durationMin,
    this.stepTitle,
    this.taskTitle,
    required this.status,
    required this.scheduleRevision,
    required this.updatedAt,
  });

  final String sessionId;
  final String? runId;
  final String taskId;
  final String stepId;
  final DateTime plannedStart;
  final DateTime plannedEnd;
  final int durationMin;
  final String? stepTitle;
  final String? taskTitle;
  final String status;
  final int scheduleRevision;
  final DateTime updatedAt;

  Map<String, dynamic> toMap() => {
        'session_id': sessionId,
        'run_id': runId,
        'task_id': taskId,
        'step_id': stepId,
        'planned_start': plannedStart.toUtc().toIso8601String(),
        'planned_end': plannedEnd.toUtc().toIso8601String(),
        'duration_min': durationMin,
        'step_title': stepTitle,
        'task_title': taskTitle,
        'status': status,
        'schedule_revision': scheduleRevision,
        'updated_at': updatedAt.toUtc().toIso8601String(),
      };

  factory CachedScheduleItem.fromMap(Map<String, dynamic> map) =>
      CachedScheduleItem(
        sessionId: map['session_id'] as String,
        runId: map['run_id'] as String?,
        taskId: map['task_id'] as String,
        stepId: map['step_id'] as String,
        plannedStart: DateTime.parse(map['planned_start'] as String),
        plannedEnd: DateTime.parse(map['planned_end'] as String),
        durationMin: map['duration_min'] as int,
        stepTitle: map['step_title'] as String?,
        taskTitle: map['task_title'] as String?,
        status: map['status'] as String,
        scheduleRevision: map['schedule_revision'] as int,
        updatedAt: DateTime.parse(map['updated_at'] as String),
      );
}

/// Kết quả truy vấn lịch chính thức từ cache SQLite.
class CachedScheduleResult {
  const CachedScheduleResult({
    required this.items,
    required this.scheduleRevision,
    this.updatedAt,
  });

  final List<CachedScheduleItem> items;
  final int scheduleRevision;
  final DateTime? updatedAt;
}

/// Bản ghi đề xuất dời lịch (UF6) được lưu trữ ngoại tuyến.
class CachedProposalResult {
  const CachedProposalResult({
    required this.proposalId,
    required this.status,
    required this.baseScheduleRevision,
    required this.sourceRevisions,
    this.isLocalPreview = false,
    required this.payload,
    required this.createdAt,
    required this.updatedAt,
  });

  final String proposalId;
  final String status;
  final int baseScheduleRevision;
  final Map<String, dynamic> sourceRevisions;
  final bool isLocalPreview;
  final Map<String, dynamic> payload;
  final DateTime createdAt;
  final DateTime updatedAt;

  Map<String, dynamic> toMap() => {
        'proposal_id': proposalId,
        'status': status,
        'base_schedule_revision': baseScheduleRevision,
        'source_revisions': jsonEncode(sourceRevisions),
        'is_local_preview': isLocalPreview ? 1 : 0,
        'payload': jsonEncode(payload),
        'created_at': createdAt.toUtc().toIso8601String(),
        'updated_at': updatedAt.toUtc().toIso8601String(),
      };

  factory CachedProposalResult.fromMap(Map<String, dynamic> map) {
    dynamic rawSources = map['source_revisions'];
    Map<String, dynamic> sources = {};
    if (rawSources is String) {
      try {
        sources = jsonDecode(rawSources) as Map<String, dynamic>;
      } catch (_) {}
    } else if (rawSources is Map<String, dynamic>) {
      sources = rawSources;
    }

    dynamic rawPayload = map['payload'];
    Map<String, dynamic> parsedPayload = {};
    if (rawPayload is String) {
      try {
        parsedPayload = jsonDecode(rawPayload) as Map<String, dynamic>;
      } catch (_) {}
    } else if (rawPayload is Map<String, dynamic>) {
      parsedPayload = rawPayload;
    }

    return CachedProposalResult(
      proposalId: map['proposal_id'] as String,
      status: map['status'] as String,
      baseScheduleRevision: map['base_schedule_revision'] as int,
      sourceRevisions: sources,
      isLocalPreview: (map['is_local_preview'] as int? ?? 0) == 1,
      payload: parsedPayload,
      createdAt: DateTime.parse(map['created_at'] as String),
      updatedAt: DateTime.parse(map['updated_at'] as String),
    );
  }
}

/// Interface lưu trữ lịch, đề xuất và ngữ cảnh phiên ngoại tuyến.
abstract class LocalScheduleRepository {
  const LocalScheduleRepository();

  Future<void> cacheScheduleItems(
    List<CachedScheduleItem> items, {
    required int revision,
  });

  Future<CachedScheduleResult> getCachedSchedule({
    DateTime? from,
    DateTime? to,
  });

  Future<void> cacheProposal(CachedProposalResult proposal);

  Future<CachedProposalResult?> getCachedProposal();

  Future<void> clearPendingProposal();

  Future<void> cacheUpcomingContext(
    String runId,
    Map<String, dynamic> contextPayload,
  );

  Future<Map<String, dynamic>?> getCachedUpcomingContext(String runId);

  Future<void> clearUpcomingContext(String runId);

  Future<void> clearAll();
}

/// Triển khai LocalScheduleRepository trên SQLite.
class SqliteScheduleRepository extends LocalScheduleRepository {
  SqliteScheduleRepository({LocalDatabaseManager? dbManager})
      : _dbManager = dbManager ?? LocalDatabaseManager.instance;

  final LocalDatabaseManager _dbManager;

  Future<Database> _getDb() => _dbManager.getDatabase();

  @override
  Future<void> cacheScheduleItems(
    List<CachedScheduleItem> items, {
    required int revision,
  }) async {
    final db = await _getDb();
    await db.transaction((txn) async {
      await txn.delete('cached_schedules');
      final batch = txn.batch();
      for (final item in items) {
        batch.insert(
          'cached_schedules',
          item.toMap(),
          conflictAlgorithm: ConflictAlgorithm.replace,
        );
      }
      await batch.commit(noResult: true);
    });
  }

  @override
  Future<CachedScheduleResult> getCachedSchedule({
    DateTime? from,
    DateTime? to,
  }) async {
    final db = await _getDb();
    String? whereClause;
    List<dynamic>? whereArgs;

    if (from != null && to != null) {
      whereClause = 'planned_start >= ? AND planned_start <= ?';
      whereArgs = [from.toUtc().toIso8601String(), to.toUtc().toIso8601String()];
    } else if (from != null) {
      whereClause = 'planned_start >= ?';
      whereArgs = [from.toUtc().toIso8601String()];
    } else if (to != null) {
      whereClause = 'planned_start <= ?';
      whereArgs = [to.toUtc().toIso8601String()];
    }

    final rows = await db.query(
      'cached_schedules',
      where: whereClause,
      whereArgs: whereArgs,
      orderBy: 'planned_start ASC',
    );

    final items = rows.map(CachedScheduleItem.fromMap).toList();
    final revision = items.isNotEmpty ? items.first.scheduleRevision : 0;
    final latestUpdate = items.isNotEmpty ? items.first.updatedAt : null;

    return CachedScheduleResult(
      items: items,
      scheduleRevision: revision,
      updatedAt: latestUpdate,
    );
  }

  @override
  Future<void> cacheProposal(CachedProposalResult proposal) async {
    final db = await _getDb();
    await db.insert(
      'cached_proposals',
      proposal.toMap(),
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  @override
  Future<CachedProposalResult?> getCachedProposal() async {
    final db = await _getDb();
    final rows = await db.query(
      'cached_proposals',
      orderBy: 'updated_at DESC',
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return CachedProposalResult.fromMap(rows.first);
  }

  @override
  Future<void> clearPendingProposal() async {
    final db = await _getDb();
    await db.delete('cached_proposals');
  }

  @override
  Future<void> cacheUpcomingContext(
    String runId,
    Map<String, dynamic> contextPayload,
  ) async {
    final db = await _getDb();
    await db.insert(
      'cached_upcoming_contexts',
      {
        'run_id': runId,
        'context_payload': jsonEncode(contextPayload),
        'updated_at': DateTime.now().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  @override
  Future<Map<String, dynamic>?> getCachedUpcomingContext(String runId) async {
    final db = await _getDb();
    final rows = await db.query(
      'cached_upcoming_contexts',
      where: 'run_id = ?',
      whereArgs: [runId],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    final raw = rows.first['context_payload'];
    if (raw is String) {
      try {
        return jsonDecode(raw) as Map<String, dynamic>;
      } catch (_) {}
    }
    return null;
  }

  @override
  Future<void> clearUpcomingContext(String runId) async {
    final db = await _getDb();
    await db.delete(
      'cached_upcoming_contexts',
      where: 'run_id = ?',
      whereArgs: [runId],
    );
  }

  @override
  Future<void> clearAll() async {
    final db = await _getDb();
    await db.delete('cached_schedules');
    await db.delete('cached_proposals');
    await db.delete('cached_upcoming_contexts');
  }
}

/// Triển khai LocalScheduleRepository trong bộ nhớ (In-Memory).
class InMemoryScheduleRepository extends LocalScheduleRepository {
  final List<CachedScheduleItem> _items = [];
  int _revision = 0;
  CachedProposalResult? _proposal;
  final Map<String, Map<String, dynamic>> _contexts = {};

  @override
  Future<void> cacheScheduleItems(
    List<CachedScheduleItem> items, {
    required int revision,
  }) async {
    _items
      ..clear()
      ..addAll(items);
    _revision = revision;
  }

  @override
  Future<CachedScheduleResult> getCachedSchedule({
    DateTime? from,
    DateTime? to,
  }) async {
    var filtered = _items;
    if (from != null) {
      filtered = filtered.where((item) => !item.plannedStart.toUtc().isBefore(from.toUtc())).toList();
    }
    if (to != null) {
      filtered = filtered.where((item) => !item.plannedStart.toUtc().isAfter(to.toUtc())).toList();
    }
    return CachedScheduleResult(
      items: List.unmodifiable(filtered),
      scheduleRevision: _revision,
      updatedAt: _items.isNotEmpty ? _items.first.updatedAt : null,
    );
  }

  @override
  Future<void> cacheProposal(CachedProposalResult proposal) async {
    _proposal = proposal;
  }

  @override
  Future<CachedProposalResult?> getCachedProposal() async {
    return _proposal;
  }

  @override
  Future<void> clearPendingProposal() async {
    _proposal = null;
  }

  @override
  Future<void> cacheUpcomingContext(
    String runId,
    Map<String, dynamic> contextPayload,
  ) async {
    _contexts[runId] = Map<String, dynamic>.from(contextPayload);
  }

  @override
  Future<Map<String, dynamic>?> getCachedUpcomingContext(String runId) async {
    return _contexts[runId];
  }

  @override
  Future<void> clearUpcomingContext(String runId) async {
    _contexts.remove(runId);
  }

  @override
  Future<void> clearAll() async {
    _items.clear();
    _revision = 0;
    _proposal = null;
    _contexts.clear();
  }
}
