import 'dart:async';
import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../providers/focus_provider.dart' show ThoughtRecord;

/// Khóa bất đồng bộ (Async Mutex) tuần tự hóa các tác vụ Read-Modify-Write trên kho lưu trữ.
class _AsyncLock {
  Future<void>? _last;

  Future<T> synchronized<T>(Future<T> Function() action) {
    final prev = _last;
    final completer = Completer<void>();
    _last = completer.future;

    return Future(() async {
      if (prev != null) {
        try {
          await prev;
        } catch (_) {}
      }
      try {
        return await action();
      } finally {
        completer.complete();
      }
    });
  }
}

/// Interface chung cho tầng lưu trữ cục bộ trạng thái phiên tập trung (UF3) và suy nghĩ (UF4).
///
/// Phục vụ yêu cầu bàn giao:
/// "Timer/clock và local repository dùng được qua interface chung; có unit test với clock giả."
abstract class LocalRunRepository {
  const LocalRunRepository();

  Future<void> saveActiveRun(Map<String, dynamic> runData);
  Future<Map<String, dynamic>?> loadActiveRun();
  Future<void> clearActiveRun();

  Future<void> saveThoughts(List<ThoughtRecord> thoughts);
  Future<List<ThoughtRecord>> loadThoughts();
  Future<void> queueThought(ThoughtRecord thought);
  Future<void> markThoughtSynced(String thoughtId, String serverThoughtId);
  Future<void> deleteThought(String thoughtId);
  Future<void> clearAllThoughts();
}

/// Triển khai lưu bền bằng FlutterSecureStorage cho thiết bị thật,
/// được bảo vệ bằng AsyncLock chống Race Condition khi đọc-ghi đồng thời.
class SecureStorageRunRepository extends LocalRunRepository {
  SecureStorageRunRepository({
    FlutterSecureStorage? storage,
    this.runKey = 'proplan.uf3.active_run.v1',
    this.thoughtsKey = 'proplan.uf4.local_thoughts.v1',
  }) : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  final String runKey;
  final String thoughtsKey;
  final _AsyncLock _thoughtsLock = _AsyncLock();
  final _AsyncLock _runLock = _AsyncLock();

  @override
  Future<void> saveActiveRun(Map<String, dynamic> runData) =>
      _runLock.synchronized(() async {
        await _storage.write(key: runKey, value: jsonEncode(runData));
      });

  @override
  Future<Map<String, dynamic>?> loadActiveRun() =>
      _runLock.synchronized(() async {
        final raw = await _storage.read(key: runKey);
        if (raw == null) return null;
        try {
          return jsonDecode(raw) as Map<String, dynamic>;
        } catch (_) {
          await _storage.delete(key: runKey);
          return null;
        }
      });

  @override
  Future<void> clearActiveRun() =>
      _runLock.synchronized(() async {
        await _storage.delete(key: runKey);
      });

  @override
  Future<void> saveThoughts(List<ThoughtRecord> thoughts) =>
      _thoughtsLock.synchronized(() async {
        final list = thoughts.map((t) => t.toJson()).toList();
        await _storage.write(key: thoughtsKey, value: jsonEncode(list));
      });

  @override
  Future<List<ThoughtRecord>> loadThoughts() =>
      _thoughtsLock.synchronized(() async {
        final raw = await _storage.read(key: thoughtsKey);
        if (raw == null) return [];
        try {
          final decoded = jsonDecode(raw) as List<dynamic>;
          return decoded
              .whereType<Map<String, dynamic>>()
              .map(ThoughtRecord.fromJson)
              .toList();
        } catch (_) {
          // KHÔNG XÓA KEY: Bảo toàn dữ liệu thô trên thiết bị khi gặp lỗi giải mã tạm thời
          return [];
        }
      });

  @override
  Future<void> queueThought(ThoughtRecord thought) =>
      _thoughtsLock.synchronized(() async {
        final raw = await _storage.read(key: thoughtsKey);
        List<ThoughtRecord> existing = [];
        if (raw != null && raw.isNotEmpty) {
          try {
            existing = (jsonDecode(raw) as List<dynamic>)
                .whereType<Map<String, dynamic>>()
                .map(ThoughtRecord.fromJson)
                .toList();
          } catch (_) {
            await _storage.write(key: '${thoughtsKey}.corrupted_backup', value: raw);
            existing = [];
          }
        }
        existing.removeWhere((item) => item.clientThoughtId == thought.clientThoughtId);
        existing.insert(0, thought);
        final list = existing.map((t) => t.toJson()).toList();
        await _storage.write(key: thoughtsKey, value: jsonEncode(list));
      });

  @override
  Future<void> markThoughtSynced(String thoughtId, String serverThoughtId) =>
      _thoughtsLock.synchronized(() async {
        final raw = await _storage.read(key: thoughtsKey);
        if (raw == null) return;
        List<ThoughtRecord> existing = [];
        try {
          existing = (jsonDecode(raw) as List<dynamic>)
              .whereType<Map<String, dynamic>>()
              .map(ThoughtRecord.fromJson)
              .toList();
        } catch (_) {
          return;
        }
        final index = existing.indexWhere((item) => item.clientThoughtId == thoughtId);
        if (index >= 0) {
          existing[index] = existing[index].copyWith(
            isPendingSync: false,
            serverThoughtId: serverThoughtId,
          );
          final list = existing.map((t) => t.toJson()).toList();
          await _storage.write(key: thoughtsKey, value: jsonEncode(list));
        }
      });

  @override
  Future<void> deleteThought(String thoughtId) =>
      _thoughtsLock.synchronized(() async {
        final raw = await _storage.read(key: thoughtsKey);
        if (raw == null) return;
        List<ThoughtRecord> existing = [];
        try {
          existing = (jsonDecode(raw) as List<dynamic>)
              .whereType<Map<String, dynamic>>()
              .map(ThoughtRecord.fromJson)
              .toList();
        } catch (_) {
          return;
        }
        existing.removeWhere((item) => item.clientThoughtId == thoughtId);
        final list = existing.map((t) => t.toJson()).toList();
        await _storage.write(key: thoughtsKey, value: jsonEncode(list));
      });

  @override
  Future<void> clearAllThoughts() =>
      _thoughtsLock.synchronized(() async {
        await _storage.delete(key: thoughtsKey);
      });
}

/// Triển khai lưu trữ trên bộ nhớ (In-Memory) phục vụ Unit Test và mocking nhanh,
/// cũng được serialize để an toàn tuyệt đối trước async concurrency.
class InMemoryRunRepository extends LocalRunRepository {
  Map<String, dynamic>? _activeRun;
  final List<ThoughtRecord> _thoughts = [];
  final _AsyncLock _thoughtsLock = _AsyncLock();

  @override
  Future<void> saveActiveRun(Map<String, dynamic> runData) async {
    _activeRun = Map<String, dynamic>.from(runData);
  }

  @override
  Future<Map<String, dynamic>?> loadActiveRun() async {
    if (_activeRun == null) return null;
    return Map<String, dynamic>.from(_activeRun!);
  }

  @override
  Future<void> clearActiveRun() async {
    _activeRun = null;
  }

  @override
  Future<void> saveThoughts(List<ThoughtRecord> thoughts) =>
      _thoughtsLock.synchronized(() async {
        _thoughts
          ..clear()
          ..addAll(thoughts);
      });

  @override
  Future<List<ThoughtRecord>> loadThoughts() =>
      _thoughtsLock.synchronized(() async => List<ThoughtRecord>.from(_thoughts));

  @override
  Future<void> queueThought(ThoughtRecord thought) =>
      _thoughtsLock.synchronized(() async {
        _thoughts.removeWhere((item) => item.clientThoughtId == thought.clientThoughtId);
        _thoughts.insert(0, thought);
      });

  @override
  Future<void> markThoughtSynced(String thoughtId, String serverThoughtId) =>
      _thoughtsLock.synchronized(() async {
        final index = _thoughts.indexWhere((item) => item.clientThoughtId == thoughtId);
        if (index >= 0) {
          _thoughts[index] = _thoughts[index].copyWith(
            isPendingSync: false,
            serverThoughtId: serverThoughtId,
          );
        }
      });

  @override
  Future<void> deleteThought(String thoughtId) =>
      _thoughtsLock.synchronized(() async {
        _thoughts.removeWhere((item) => item.clientThoughtId == thoughtId);
      });

  @override
  Future<void> clearAllThoughts() =>
      _thoughtsLock.synchronized(() async {
        _thoughts.clear();
      });
}
