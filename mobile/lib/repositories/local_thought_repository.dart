import 'dart:async';
import 'package:sqflite/sqflite.dart';

import '../models/thought_model.dart';
import 'local_database_manager.dart';

/// Interface cho việc lưu trữ và đồng bộ Thought ngoại tuyến (UF4 & Tombstone).
abstract class LocalThoughtRepository {
  const LocalThoughtRepository();

  /// Thêm mới hoặc cập nhật một Thought vào kho lưu trữ cục bộ.
  Future<void> insertThought(LocalThought thought);

  /// Lấy danh sách các Thought đang hoạt động của người dùng (loại trừ các bản ghi đã đánh dấu tombstone).
  Future<List<LocalThought>> getActiveThoughts(String ownerId);

  /// Đánh dấu xóa tạm thời (Local Tombstone) để ẩn khỏi UI ngay lập tức.
  Future<void> markTombstone(String id, {DateTime? asOf, String? ownerId});

  /// Xóa vĩnh viễn dòng tombstone khỏi cơ sở dữ liệu sau khi nhận phản hồi DELETE 200 OK từ máy chủ.
  Future<int> purgeTombstone(String id);

  /// Đánh dấu bản ghi đã đồng bộ thành công lên máy chủ (state = 'synced').
  Future<void> markSynced(String id, {DateTime? asOf});

  /// Lấy danh sách toàn bộ ID đang bị đánh dấu tombstone của một người dùng.
  Future<List<String>> getTombstoneIds(String ownerId);

  /// Đối chiếu và lọc bỏ các bản ghi từ Server nếu ID của chúng nằm trong danh sách Tombstone cục bộ.
  Future<List<Map<String, dynamic>>> reconcileWithServer(
    String ownerId,
    List<Map<String, dynamic>> serverItems,
  );

  /// Lấy thông tin một Thought cụ thể theo ID.
  Future<LocalThought?> getThought(String id);

  /// Lấy toàn bộ Thought của người dùng (kể cả tombstone - dùng cho kiểm thử và gỡ lỗi).
  Future<List<LocalThought>> getAllThoughts(String ownerId);
}

/// Hiện thực hóa `LocalThoughtRepository` lưu trữ bền vững trên SQLite theo từng tài khoản.
class SqliteThoughtRepository extends LocalThoughtRepository {
  SqliteThoughtRepository({
    LocalDatabaseManager? dbManager,
    Database? db,
  })  : _dbManager = dbManager ?? LocalDatabaseManager.instance,
        _explicitDb = db;

  final LocalDatabaseManager _dbManager;
  final Database? _explicitDb;

  Future<Database> _getDb() async =>
      _explicitDb ?? await _dbManager.getDatabase();

  @override
  Future<void> insertThought(LocalThought thought) async {
    final db = await _getDb();
    await db.insert(
      'local_thoughts',
      thought.toMap(),
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  @override
  Future<List<LocalThought>> getActiveThoughts(String ownerId) async {
    final db = await _getDb();
    final rows = await db.query(
      'local_thoughts',
      where: 'owner_id = ? AND state != ?',
      whereArgs: [ownerId, LocalThoughtState.tombstone],
      orderBy: 'created_at DESC',
    );
    return rows.map(LocalThought.fromMap).toList();
  }

  @override
  Future<void> markTombstone(String id, {DateTime? asOf, String? ownerId}) async {
    final db = await _getDb();
    final nowUtc = (asOf ?? DateTime.now()).toUtc().toIso8601String();
    final count = await db.update(
      'local_thoughts',
      {
        'state': LocalThoughtState.tombstone,
        'updated_at': nowUtc,
      },
      where: 'id = ?',
      whereArgs: [id],
    );
    if (count == 0) {
      final resolvedOwner = ownerId ??
          _dbManager.currentOwnerId ??
          'default';
      await db.insert(
        'local_thoughts',
        {
          'id': id,
          'owner_id': resolvedOwner,
          'title': null,
          'audio_path': '',
          'source_run_id': null,
          'state': LocalThoughtState.tombstone,
          'created_at': nowUtc,
          'updated_at': nowUtc,
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
    }
  }

  @override
  Future<int> purgeTombstone(String id) async {
    final db = await _getDb();
    return await db.delete(
      'local_thoughts',
      where: 'id = ? AND state = ?',
      whereArgs: [id, LocalThoughtState.tombstone],
    );
  }

  @override
  Future<void> markSynced(String id, {DateTime? asOf}) async {
    final db = await _getDb();
    final nowUtc = (asOf ?? DateTime.now()).toUtc().toIso8601String();
    await db.update(
      'local_thoughts',
      {
        'state': LocalThoughtState.synced,
        'updated_at': nowUtc,
      },
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  @override
  Future<List<String>> getTombstoneIds(String ownerId) async {
    final db = await _getDb();
    final rows = await db.query(
      'local_thoughts',
      columns: ['id'],
      where: 'owner_id = ? AND state = ?',
      whereArgs: [ownerId, LocalThoughtState.tombstone],
    );
    return rows.map((r) => r['id'] as String).toList();
  }

  @override
  Future<List<Map<String, dynamic>>> reconcileWithServer(
    String ownerId,
    List<Map<String, dynamic>> serverItems,
  ) async {
    final tombstoneIds = (await getTombstoneIds(ownerId)).toSet();
    if (tombstoneIds.isEmpty) return serverItems;

    return serverItems.where((item) {
      final thoughtId = item['thought_id']?.toString();
      final clientId = item['client_thought_id']?.toString();
      final id = item['id']?.toString();
      if (thoughtId != null && tombstoneIds.contains(thoughtId)) return false;
      if (clientId != null && tombstoneIds.contains(clientId)) return false;
      if (id != null && tombstoneIds.contains(id)) return false;
      return true;
    }).toList();
  }

  @override
  Future<LocalThought?> getThought(String id) async {
    final db = await _getDb();
    final rows = await db.query(
      'local_thoughts',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return LocalThought.fromMap(rows.first);
  }

  @override
  Future<List<LocalThought>> getAllThoughts(String ownerId) async {
    final db = await _getDb();
    final rows = await db.query(
      'local_thoughts',
      where: 'owner_id = ?',
      whereArgs: [ownerId],
      orderBy: 'created_at DESC',
    );
    return rows.map(LocalThought.fromMap).toList();
  }
}

/// Hiện thực hóa `LocalThoughtRepository` trong bộ nhớ phục vụ Unit Test và Fast Mocking.
class InMemoryThoughtRepository extends LocalThoughtRepository {
  final Map<String, LocalThought> _thoughts = {};

  @override
  Future<void> insertThought(LocalThought thought) async {
    _thoughts[thought.id] = thought;
  }

  @override
  Future<List<LocalThought>> getActiveThoughts(String ownerId) async {
    final items = _thoughts.values
        .where((t) => t.ownerId == ownerId && t.state != LocalThoughtState.tombstone)
        .toList();
    items.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return items;
  }

  @override
  Future<void> markTombstone(String id, {DateTime? asOf, String? ownerId}) async {
    final existing = _thoughts[id];
    final nowUtc = (asOf ?? DateTime.now()).toUtc();
    if (existing != null) {
      _thoughts[id] = existing.copyWith(
        state: LocalThoughtState.tombstone,
        updatedAt: nowUtc,
      );
    } else {
      _thoughts[id] = LocalThought(
        id: id,
        ownerId: ownerId ?? 'default',
        audioPath: '',
        state: LocalThoughtState.tombstone,
        createdAt: nowUtc,
        updatedAt: nowUtc,
      );
    }
  }

  @override
  Future<int> purgeTombstone(String id) async {
    final existing = _thoughts[id];
    if (existing != null && existing.state == LocalThoughtState.tombstone) {
      _thoughts.remove(id);
      return 1;
    }
    return 0;
  }

  @override
  Future<void> markSynced(String id, {DateTime? asOf}) async {
    final existing = _thoughts[id];
    if (existing != null) {
      final nowUtc = (asOf ?? DateTime.now()).toUtc();
      _thoughts[id] = existing.copyWith(
        state: LocalThoughtState.synced,
        updatedAt: nowUtc,
      );
    }
  }

  @override
  Future<List<String>> getTombstoneIds(String ownerId) async {
    return _thoughts.values
        .where((t) => t.ownerId == ownerId && t.state == LocalThoughtState.tombstone)
        .map((t) => t.id)
        .toList();
  }

  @override
  Future<List<Map<String, dynamic>>> reconcileWithServer(
    String ownerId,
    List<Map<String, dynamic>> serverItems,
  ) async {
    final tombstoneIds = (await getTombstoneIds(ownerId)).toSet();
    if (tombstoneIds.isEmpty) return serverItems;

    return serverItems.where((item) {
      final thoughtId = item['thought_id']?.toString();
      final clientId = item['client_thought_id']?.toString();
      final id = item['id']?.toString();
      if (thoughtId != null && tombstoneIds.contains(thoughtId)) return false;
      if (clientId != null && tombstoneIds.contains(clientId)) return false;
      if (id != null && tombstoneIds.contains(id)) return false;
      return true;
    }).toList();
  }

  @override
  Future<LocalThought?> getThought(String id) async {
    return _thoughts[id];
  }

  @override
  Future<List<LocalThought>> getAllThoughts(String ownerId) async {
    final items = _thoughts.values.where((t) => t.ownerId == ownerId).toList();
    items.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return items;
  }
}
