import 'dart:async';
import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

import '../core/api_session.dart';

/// Quản lý vòng đời kết nối SQLite cô lập theo từng tài khoản (Multi-Account Isolation).
///
/// Mỗi `ownerId` được lưu trữ trong một tệp cơ sở dữ liệu riêng:
/// `proplan_${sanitizedOwnerId}.db` để ngăn ngừa triệt để rò rỉ dữ liệu chéo.
class LocalDatabaseManager {
  LocalDatabaseManager({
    DatabaseFactory? databaseFactory,
    String? customDatabasesPath,
  })  : _databaseFactory = databaseFactory,
        _customDatabasesPath = customDatabasesPath;

  static final LocalDatabaseManager instance = LocalDatabaseManager();

  final DatabaseFactory? _databaseFactory;
  final String? _customDatabasesPath;

  Database? _db;
  String? _currentOwnerId;
  Completer<Database>? _openCompleter;

  Database? get currentDatabase => _db;
  String? get currentOwnerId => _currentOwnerId;
  String? get customDatabasesPath => _customDatabasesPath;

  /// Chuẩn hóa chuỗi ownerId để tạo tên tệp an toàn trên hệ thống tệp.
  static String sanitizeOwnerId(String ownerId) {
    return ownerId.replaceAll(RegExp(r'[^a-zA-Z0-9_]'), '_');
  }

  /// Tên tệp cơ sở dữ liệu theo ownerId.
  static String databaseFileName(String ownerId) {
    return 'proplan_${sanitizeOwnerId(ownerId)}.db';
  }

  /// Mở hoặc trả về cơ sở dữ liệu hiện tại của `ownerId`.
  /// Nếu đang mở DB của tài khoản khác, tự động đóng DB cũ trước khi mở DB mới.
  Future<Database> openUserDatabase(String ownerId) async {
    if (_db != null && _db!.isOpen && _currentOwnerId == ownerId) {
      return _db!;
    }

    while (_openCompleter != null) {
      try {
        await _openCompleter!.future;
      } catch (_) {
        break;
      }
      if (_db != null && _db!.isOpen && _currentOwnerId == ownerId) {
        return _db!;
      }
    }

    final completer = Completer<Database>();
    _openCompleter = completer;

    try {
      if (_db != null && _db!.isOpen) {
        await _db!.close();
        _db = null;
        _currentOwnerId = null;
      }

      final dbPath = await _resolveDatabasePath(ownerId);
      final factory = _databaseFactory ?? databaseFactory;

      _db = await factory.openDatabase(
        dbPath,
        options: OpenDatabaseOptions(
          version: 1,
          onCreate: _onCreate,
          onUpgrade: _onUpgrade,
        ),
      );
      _currentOwnerId = ownerId;
      completer.complete(_db!);
      return _db!;
    } catch (e, st) {
      completer.completeError(e, st);
      rethrow;
    } finally {
      _openCompleter = null;
    }
  }

  /// Lấy database đang mở hoặc tự động mở theo session hiện hành.
  Future<Database> getDatabase() async {
    if (_db != null && _db!.isOpen) {
      return _db!;
    }
    final ownerId = ApiSession.current?.ownerId ?? 'default';
    return openUserDatabase(ownerId);
  }

  /// Đóng kết nối cơ sở dữ liệu hiện tại khi chuyển tài khoản hoặc đăng xuất.
  Future<void> closeCurrentDatabase() async {
    while (_openCompleter != null) {
      try {
        await _openCompleter!.future;
      } catch (_) {
        break;
      }
    }
    final closeCompleter = Completer<Database>();
    _openCompleter = closeCompleter;
    try {
      if (_db != null && _db!.isOpen) {
        await _db!.close();
      }
      _db = null;
      _currentOwnerId = null;
    } finally {
      _openCompleter = null;
      if (!closeCompleter.isCompleted) {
        closeCompleter.completeError(StateError('Database closed'));
      }
    }
  }

  /// Xóa tệp cơ sở dữ liệu của một người dùng (dùng cho Reset hoặc Testing).
  Future<void> deleteUserDatabase(String ownerId) async {
    if (_currentOwnerId == ownerId) {
      await closeCurrentDatabase();
    }
    final dbPath = await _resolveDatabasePath(ownerId);
    final factory = _databaseFactory ?? databaseFactory;
    await factory.deleteDatabase(dbPath);
  }

  Future<String> _resolveDatabasePath(String ownerId) async {
    final fileName = databaseFileName(ownerId);
    final customPath = _customDatabasesPath;
    if (customPath != null) {
      return p.join(customPath, fileName);
    }
    final factory = _databaseFactory ?? databaseFactory;
    final baseDir = await factory.getDatabasesPath();
    return p.join(baseDir, fileName);
  }

  Future<void> _onCreate(Database db, int version) async {
    final batch = db.batch();

    // 1. local_outbox: Hàng đợi bất đồng bộ hỗ trợ retry và 5s Undo
    batch.execute('''
      CREATE TABLE IF NOT EXISTS local_outbox (
        client_event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        target_resource_id TEXT,
        endpoint TEXT NOT NULL,
        http_method TEXT NOT NULL,
        payload TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        hold_until TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        retry_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        synced_at TEXT
      );
    ''');
    batch.execute('''
      CREATE INDEX IF NOT EXISTS idx_outbox_replay
      ON local_outbox(status, hold_until, occurred_at);
    ''');

    // 2. cached_schedules: Bộ đệm lịch làm việc chính thức phục vụ UF3/UF5 offline
    batch.execute('''
      CREATE TABLE IF NOT EXISTS cached_schedules (
        session_id TEXT PRIMARY KEY,
        run_id TEXT,
        task_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        planned_start TEXT NOT NULL,
        planned_end TEXT NOT NULL,
        duration_min INTEGER NOT NULL,
        step_title TEXT,
        task_title TEXT,
        status TEXT NOT NULL,
        schedule_revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL
      );
    ''');
    batch.execute('''
      CREATE INDEX IF NOT EXISTS idx_cached_schedules
      ON cached_schedules(planned_start);
    ''');

    // 3. cached_proposals: Đề xuất dời lịch UF6 chờ duyệt
    batch.execute('''
      CREATE TABLE IF NOT EXISTS cached_proposals (
        proposal_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        base_schedule_revision INTEGER NOT NULL,
        source_revisions TEXT,
        is_local_preview INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    ''');

    // 4. cached_upcoming_contexts: Ngữ cảnh kế tiếp UF5
    batch.execute('''
      CREATE TABLE IF NOT EXISTS cached_upcoming_contexts (
        run_id TEXT PRIMARY KEY,
        context_payload TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    ''');

    await batch.commit(noResult: true);
  }

  Future<void> _onUpgrade(Database db, int oldVersion, int newVersion) async {
    // Migration logic cho các phiên bản tiếp theo
  }
}
