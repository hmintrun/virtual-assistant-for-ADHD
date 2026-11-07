import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../providers/focus_provider.dart' show ThoughtRecord;

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
  Future<void> markThoughtSynced(String thoughtId);
  Future<void> deleteThought(String thoughtId);
  Future<void> clearAllThoughts();
}

/// Triển khai lưu bền bằng FlutterSecureStorage cho thiết bị thật.
class SecureStorageRunRepository extends LocalRunRepository {
  const SecureStorageRunRepository({
    FlutterSecureStorage? storage,
    this.runKey = 'proplan.uf3.active_run.v1',
    this.thoughtsKey = 'proplan.uf4.local_thoughts.v1',
  }) : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  final String runKey;
  final String thoughtsKey;

  @override
  Future<void> saveActiveRun(Map<String, dynamic> runData) async {
    await _storage.write(key: runKey, value: jsonEncode(runData));
  }

  @override
  Future<Map<String, dynamic>?> loadActiveRun() async {
    final raw = await _storage.read(key: runKey);
    if (raw == null) return null;
    try {
      return jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {
      await clearActiveRun();
      return null;
    }
  }

  @override
  Future<void> clearActiveRun() async {
    await _storage.delete(key: runKey);
  }

  @override
  Future<void> saveThoughts(List<ThoughtRecord> thoughts) async {
    final list = thoughts.map((t) => t.toJson()).toList();
    await _storage.write(key: thoughtsKey, value: jsonEncode(list));
  }

  @override
  Future<List<ThoughtRecord>> loadThoughts() async {
    final raw = await _storage.read(key: thoughtsKey);
    if (raw == null) return [];
    try {
      final decoded = jsonDecode(raw) as List<dynamic>;
      return decoded
          .whereType<Map<String, dynamic>>()
          .map(ThoughtRecord.fromJson)
          .toList();
    } catch (_) {
      await clearAllThoughts();
      return [];
    }
  }

  @override
  Future<void> queueThought(ThoughtRecord thought) async {
    final existing = await loadThoughts();
    existing.removeWhere((item) => item.id == thought.id);
    existing.insert(0, thought);
    await saveThoughts(existing);
  }

  @override
  Future<void> markThoughtSynced(String thoughtId) async {
    final existing = await loadThoughts();
    final index = existing.indexWhere((item) => item.id == thoughtId);
    if (index >= 0) {
      final item = existing[index];
      existing[index] = ThoughtRecord(
        id: item.id,
        createdAt: item.createdAt,
        audioPath: item.audioPath,
        text: item.text,
        isPendingSync: false,
      );
      await saveThoughts(existing);
    }
  }

  @override
  Future<void> deleteThought(String thoughtId) async {
    final existing = await loadThoughts();
    existing.removeWhere((item) => item.id == thoughtId);
    await saveThoughts(existing);
  }

  @override
  Future<void> clearAllThoughts() async {
    await _storage.delete(key: thoughtsKey);
  }
}

/// Triển khai lưu trữ trên bộ nhớ (In-Memory) phục vụ Unit Test và mocking nhanh.
class InMemoryRunRepository extends LocalRunRepository {
  Map<String, dynamic>? _activeRun;
  final List<ThoughtRecord> _thoughts = [];

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
  Future<void> saveThoughts(List<ThoughtRecord> thoughts) async {
    _thoughts
      ..clear()
      ..addAll(thoughts);
  }

  @override
  Future<List<ThoughtRecord>> loadThoughts() async {
    return List<ThoughtRecord>.from(_thoughts);
  }

  @override
  Future<void> queueThought(ThoughtRecord thought) async {
    _thoughts.removeWhere((item) => item.id == thought.id);
    _thoughts.insert(0, thought);
  }

  @override
  Future<void> markThoughtSynced(String thoughtId) async {
    final index = _thoughts.indexWhere((item) => item.id == thoughtId);
    if (index >= 0) {
      final item = _thoughts[index];
      _thoughts[index] = ThoughtRecord(
        id: item.id,
        createdAt: item.createdAt,
        audioPath: item.audioPath,
        text: item.text,
        isPendingSync: false,
      );
    }
  }

  @override
  Future<void> deleteThought(String thoughtId) async {
    _thoughts.removeWhere((item) => item.id == thoughtId);
  }

  @override
  Future<void> clearAllThoughts() async {
    _thoughts.clear();
  }
}
