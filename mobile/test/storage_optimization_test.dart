import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';
import 'package:proplan_va/models/outbox_record.dart';
import 'package:proplan_va/models/thought_model.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_database_manager.dart';
import 'package:proplan_va/repositories/local_outbox_repository.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/repositories/local_schedule_repository.dart';
import 'package:proplan_va/repositories/local_thought_repository.dart';
import 'package:proplan_va/services/audio_recorder_service.dart';
import 'package:proplan_va/services/voice_playback_service.dart';
import 'package:proplan_va/services/voice_service.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:timezone/timezone.dart' as tz;

class _FakeDraftApiClient extends ApiClient {
  _FakeDraftApiClient({this.failDraftPost = false}) : super(bindSession: false);

  final bool failDraftPost;

  @override
  Future<Map<String, dynamic>> getData(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    return {
      'thought_id': 'srv_thought_1',
      'thought_revision': 1,
      'linked_draft_id': null,
    };
  }

  @override
  Future<Map<String, dynamic>> postData(
    String path, {
    Object? data,
    String? idempotencyKey,
    bool idempotent = true,
    Duration? receiveTimeout,
  }) async {
    if (failDraftPost) {
      throw const ApiException('Mất kết nối máy chủ');
    }
    return {
      'draft': {
        'draft_id': 'draft_1',
        'client_draft_id': 'cd_1',
        'stage': 'clarified',
        'summary': {
          'title': 'Gọi điện cho bác sĩ',
          'scope': 'Cá nhân',
          'can_plan': true,
        },
        'summary_revision': 1,
        'planning_input_revision': 1,
        'input_source': 'voice',
      },
    };
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  sqfliteFfiInit();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
    ApiSession.current = ApiSession('user_storage_opt', token: 'test_token');
  });

  tearDown(() {
    ApiSession.current = null;
  });

  group('Level 0 Storage Optimizations', () {
    test('cleanupOrphanRecordings deletes stale files and preserves fresh ones', () async {
      final tempDir = await Directory.systemTemp.createTemp('uf1_orphan_test_');
      addTearDown(() async {
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });

      final now = DateTime(2026, 11, 27, 12, 0);
      final staleFile = File('${tempDir.path}/orphan_old.m4a')
        ..writeAsBytesSync([1, 2, 3, 4]);
      staleFile.setLastModifiedSync(now.subtract(const Duration(hours: 25)));

      final freshFile = File('${tempDir.path}/recent.m4a')
        ..writeAsBytesSync([5, 6, 7, 8]);
      freshFile.setLastModifiedSync(now.subtract(const Duration(hours: 2)));

      final deleted = await Uf1VoiceRecorderService.cleanupOrphanRecordings(
        directoryOverride: tempDir,
        nowOverride: now,
      );

      expect(deleted, 1);
      expect(staleFile.existsSync(), isFalse);
      expect(freshFile.existsSync(), isTrue);

      final missingCount = await Uf1VoiceRecorderService.cleanupOrphanRecordings(
        directoryOverride: Directory('${tempDir.path}/does_not_exist'),
        nowOverride: now,
      );
      expect(missingCount, 0);
    });

    test('SQLite auto_vacuum, pruneSynced, and pruneStaleUpcomingContexts keep pending items', () async {
      final tempDir = await Directory.systemTemp.createTemp('sqlite_opt_test_');
      final dbManager = LocalDatabaseManager(
        databaseFactory: databaseFactoryFfi,
        customDatabasesPath: tempDir.path,
      );
      addTearDown(() async {
        await dbManager.closeCurrentDatabase();
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });

      final db = await dbManager.openUserDatabase('user_storage_opt');
      final modeRows = await db.rawQuery('PRAGMA auto_vacuum');
      expect((modeRows.first.values.first as num).toInt(), 2);

      final now = DateTime.utc(2026, 11, 27, 12, 0);
      final outboxRepo = SqliteOutboxRepository(db: db);
      final scheduleRepo = SqliteScheduleRepository(db: db);

      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'evt_old_synced',
          eventType: OutboxEventType.thoughtCapture,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: const {'text': 'old'},
          occurredAt: now.subtract(const Duration(days: 10)),
          holdUntil: now.subtract(const Duration(days: 10)),
        ),
      );
      await outboxRepo.markSynced('evt_old_synced');
      await db.update(
        'local_outbox',
        {'synced_at': now.subtract(const Duration(days: 10)).toIso8601String()},
        where: 'client_event_id = ?',
        whereArgs: ['evt_old_synced'],
      );

      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'evt_recent_synced',
          eventType: OutboxEventType.thoughtCapture,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: const {'text': 'recent'},
          occurredAt: now.subtract(const Duration(days: 2)),
          holdUntil: now.subtract(const Duration(days: 2)),
        ),
      );
      await outboxRepo.markSynced('evt_recent_synced');
      await db.update(
        'local_outbox',
        {'synced_at': now.subtract(const Duration(days: 2)).toIso8601String()},
        where: 'client_event_id = ?',
        whereArgs: ['evt_recent_synced'],
      );

      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'evt_old_pending',
          eventType: OutboxEventType.thoughtCapture,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: const {'text': 'pending'},
          occurredAt: now.subtract(const Duration(days: 20)),
          holdUntil: now.subtract(const Duration(days: 20)),
        ),
      );

      final prunedOutbox = await outboxRepo.pruneSynced(
        olderThan: const Duration(days: 7),
        asOf: now,
      );
      expect(prunedOutbox, 1);
      expect(await outboxRepo.getRecord('evt_old_synced'), isNull);
      expect(await outboxRepo.getRecord('evt_recent_synced'), isNotNull);
      expect(await outboxRepo.getRecord('evt_old_pending'), isNotNull);

      await db.insert('cached_upcoming_contexts', {
        'run_id': 'run_stale',
        'context_payload': jsonEncode({'next': 'old'}),
        'updated_at': now.subtract(const Duration(days: 20)).toIso8601String(),
      });
      await db.insert('cached_upcoming_contexts', {
        'run_id': 'run_fresh',
        'context_payload': jsonEncode({'next': 'fresh'}),
        'updated_at': now.subtract(const Duration(days: 2)).toIso8601String(),
      });

      final prunedContexts = await scheduleRepo.pruneStaleUpcomingContexts(
        olderThan: const Duration(days: 14),
        asOf: now,
      );
      expect(prunedContexts, 1);
      expect(await scheduleRepo.getCachedUpcomingContext('run_stale'), isNull);
      expect(await scheduleRepo.getCachedUpcomingContext('run_fresh'), isNotNull);

      await dbManager.runMaintenanceVacuum(pages: 50);
      final modeAfter = await db.rawQuery('PRAGMA auto_vacuum');
      expect((modeAfter.first.values.first as num).toInt(), 2);
    });

    test('SecureStorageRunRepository caps synced thoughts at 50 and keeps all pending', () async {
      const storage = FlutterSecureStorage();
      final repo = SecureStorageRunRepository(storage: storage);
      final base = DateTime.utc(2026, 11, 1);

      final seeded = <ThoughtRecord>[
        for (var i = 0; i < 65; i++)
          ThoughtRecord(
            clientThoughtId: 'synced_$i',
            createdAt: base.add(Duration(hours: i)),
            undoDeadlineAt: base.add(Duration(hours: i, seconds: 5)),
            textContent: 'Synced $i',
            isPendingSync: false,
            serverThoughtId: 'srv_$i',
          ),
        for (var i = 0; i < 6; i++)
          ThoughtRecord(
            clientThoughtId: 'pending_$i',
            createdAt: base.subtract(Duration(days: i + 1)),
            undoDeadlineAt: base,
            textContent: 'Pending $i',
            isPendingSync: true,
          ),
      ];

      await repo.saveThoughts(seeded);
      final loaded = await repo.loadThoughts();
      final synced = loaded.where((t) => !t.isPendingSync).toList();
      final pending = loaded.where((t) => t.isPendingSync).toList();

      expect(pending.length, 6);
      expect(synced.length, SecureStorageRunRepository.maxPersistedSyncedThoughts);
      expect(synced.any((t) => t.clientThoughtId == 'synced_64'), isTrue);
      expect(synced.any((t) => t.clientThoughtId == 'synced_0'), isFalse);

      // Verify compactForStorage trims legacy oversized raw payloads too.
      await storage.write(
        key: repo.thoughtsKey,
        value: jsonEncode(seeded.map((t) => t.toJson()).toList()),
      );
      final removed = await repo.compactForStorage();
      expect(removed, 15);
      final afterCompact = await repo.loadThoughts();
      expect(afterCompact.length, 56);
    });

    test('convertThoughtToDraft deletes local audio file and clears paths after success only', () async {
      final tempDir = await Directory.systemTemp.createTemp('uf4_audio_cleanup_');
      final dbManager = LocalDatabaseManager(
        databaseFactory: databaseFactoryFfi,
        customDatabasesPath: tempDir.path,
      );
      addTearDown(() async {
        await dbManager.closeCurrentDatabase();
        if (await tempDir.exists()) {
          await tempDir.delete(recursive: true);
        }
      });

      final db = await dbManager.openUserDatabase('user_storage_opt');
      final thoughtRepo = SqliteThoughtRepository(db: db);
      final runRepo = SecureStorageRunRepository();

      final audioFail = File('${tempDir.path}/fail_keep.m4a')
        ..writeAsBytesSync([9, 9, 9]);
      final failThought = ThoughtRecord(
        clientThoughtId: 'ct_fail',
        createdAt: DateTime.utc(2026, 11, 27),
        undoDeadlineAt: DateTime.utc(2026, 11, 27),
        inputType: 'audio',
        localAudioPath: audioFail.path,
        isPendingSync: false,
        serverThoughtId: 'srv_thought_1',
      );
      final failingProvider = FocusProvider(
        ownerId: 'user_storage_opt',
        api: _FakeDraftApiClient(failDraftPost: true),
        repository: runRepo,
        thoughtRepository: thoughtRepo,
      );
      addTearDown(failingProvider.dispose);

      await expectLater(
        () => failingProvider.convertThoughtToDraft(failThought, 'Test fail'),
        throwsA(isA<ApiException>()),
      );
      expect(audioFail.existsSync(), isTrue);

      final audioOk = File('${tempDir.path}/converted_clean.m4a')
        ..writeAsBytesSync([1, 2, 3, 4, 5]);
      final okThought = ThoughtRecord(
        clientThoughtId: 'ct_ok',
        createdAt: DateTime.utc(2026, 11, 27),
        undoDeadlineAt: DateTime.utc(2026, 11, 27),
        inputType: 'audio',
        localAudioPath: audioOk.path,
        isPendingSync: false,
        serverThoughtId: 'srv_thought_1',
      );
      await runRepo.saveThoughts([okThought]);
      await thoughtRepo.insertThought(
        LocalThought(
          id: 'ct_ok',
          ownerId: 'user_storage_opt',
          title: 'Gọi điện cho bác sĩ',
          audioPath: audioOk.path,
          state: LocalThoughtState.synced,
          serverThoughtId: 'srv_thought_1',
          createdAt: DateTime.utc(2026, 11, 27),
          updatedAt: DateTime.utc(2026, 11, 27),
        ),
      );

      final provider = FocusProvider(
        ownerId: 'user_storage_opt',
        api: _FakeDraftApiClient(),
        repository: runRepo,
        thoughtRepository: thoughtRepo,
      );
      addTearDown(provider.dispose);

      final draft = await provider.convertThoughtToDraft(
        okThought,
        'Gọi điện cho bác sĩ',
      );
      expect(draft.draftId, 'draft_1');
      expect(audioOk.existsSync(), isFalse);

      final updatedSqlite = await thoughtRepo.getThought('ct_ok');
      expect(updatedSqlite?.audioPath, '');

      final updatedSecure = await runRepo.loadThoughts();
      expect(updatedSecure.single.localAudioPath, isNull);
    });

    test('FileTtsAudioCache enforces maxTotalBytes and pruneCache removes stale/empty/over-budget files', () async {
      final dir = await Directory.systemTemp.createTemp('tts_budget_test_');
      addTearDown(() async {
        if (await dir.exists()) await dir.delete(recursive: true);
      });

      final now = DateTime.now().toUtc();
      final cache = FileTtsAudioCache(
        maxFiles: 10,
        maxTotalBytes: 100,
        dir: () async => dir,
      );

      final bytes45 = Uint8List.fromList(List<int>.filled(45, 7));
      await cache.put('k1', CachedTtsAudio(bytes45, 'audio/mpeg'));
      File('${dir.path}/k1.mp3').setLastModifiedSync(
        now.subtract(const Duration(minutes: 30)),
      );

      await cache.put('k2', CachedTtsAudio(bytes45, 'audio/mpeg'));
      File('${dir.path}/k2.mp3').setLastModifiedSync(
        now.subtract(const Duration(minutes: 20)),
      );

      await cache.put('k3', CachedTtsAudio(bytes45, 'audio/mpeg'));

      expect(File('${dir.path}/k1.mp3').existsSync(), isFalse);
      expect(File('${dir.path}/k2.mp3').existsSync(), isTrue);
      expect(File('${dir.path}/k3.mp3').existsSync(), isTrue);

      // Seed an empty 0-byte file and an expired >14d file directly on disk.
      File('${dir.path}/empty.mp3')
        ..createSync()
        ..setLastModifiedSync(now);
      File('${dir.path}/expired.mp3')
        ..writeAsBytesSync(bytes45)
        ..setLastModifiedSync(now.subtract(const Duration(days: 15)));
      File('${dir.path}/k2.mp3').setLastModifiedSync(
        now.subtract(const Duration(days: 2)),
      );
      File('${dir.path}/k3.mp3').setLastModifiedSync(
        now.subtract(const Duration(days: 1)),
      );

      final pruned = await FileTtsAudioCache.pruneCache(
        maxFiles: 1,
        maxTotalBytes: 200,
        maxAge: const Duration(days: 14),
        directoryOverride: dir,
        nowOverride: now,
      );
      // Deletes empty.mp3 + expired.mp3 + oldest remaining k2.mp3 (maxFiles=1).
      expect(pruned, 3);
      expect(File('${dir.path}/k3.mp3').existsSync(), isTrue);
      expect(File('${dir.path}/k2.mp3').existsSync(), isFalse);
    });

    test('AudioRecorderService.cleanupStaleRecordings removes old 0-byte files safely', () async {
      final dir = await Directory.systemTemp.createTemp('thoughts_cleanup_test_');
      addTearDown(() async {
        if (await dir.exists()) await dir.delete(recursive: true);
      });

      final now = DateTime.utc(2026, 11, 28, 12, 0);
      final oldEmpty = File('${dir.path}/thought_100.m4a')
        ..createSync()
        ..setLastModifiedSync(now.subtract(const Duration(hours: 2)));
      final freshEmpty = File('${dir.path}/thought_200.m4a')
        ..createSync()
        ..setLastModifiedSync(now.subtract(const Duration(minutes: 10)));
      final oldReal = File('${dir.path}/thought_300.m4a')
        ..writeAsBytesSync(const [1, 2, 3, 4])
        ..setLastModifiedSync(now.subtract(const Duration(days: 30)));
      final unrelatedEmpty = File('${dir.path}/other.txt')
        ..createSync()
        ..setLastModifiedSync(now.subtract(const Duration(days: 5)));

      final deleted = await AudioRecorderService.cleanupStaleRecordings(
        emptyFileMaxAge: const Duration(hours: 1),
        directoryOverride: dir,
        nowOverride: now,
      );

      expect(deleted, 1);
      expect(oldEmpty.existsSync(), isFalse);
      expect(freshEmpty.existsSync(), isTrue);
      expect(oldReal.existsSync(), isTrue);
      expect(unrelatedEmpty.existsSync(), isTrue);
    });

    test('tz.setLocalLocation(tz.UTC) constructs scheduled UTC TZDateTime without tzdb', () {
      tz.setLocalLocation(tz.UTC);
      final scheduled = tz.TZDateTime.from(DateTime.utc(2026, 11, 28, 10, 0), tz.UTC);
      expect(scheduled.isUtc, isTrue);
      expect(scheduled.year, 2026);
      expect(scheduled.month, 11);
      expect(scheduled.day, 28);
      expect(scheduled.hour, 10);
    });
  });
}
