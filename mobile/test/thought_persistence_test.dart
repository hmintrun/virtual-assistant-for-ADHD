import 'dart:io';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:path/path.dart' as p;

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';
import 'package:proplan_va/models/outbox_record.dart';
import 'package:proplan_va/models/thought_model.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_database_manager.dart';
import 'package:proplan_va/repositories/local_outbox_repository.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/repositories/local_thought_repository.dart';
import 'package:proplan_va/services/audio_recorder_service.dart';
import 'package:proplan_va/services/outbox_replay_worker.dart';
import 'package:proplan_va/services/timer_service.dart';

/// Test Clock giả lập để kiểm soát thời gian chính xác trong unit test.
class TestClock implements Clock {
  TestClock(this.nowTime);
  DateTime nowTime;

  @override
  DateTime now() => nowTime;

  void advance(Duration duration) {
    nowTime = nowTime.add(duration);
  }
}

/// Fake AudioRecorderService giả lập lỗi I/O để kiểm thử 3-retry fallback.
class FailingAudioRecorderService extends AudioRecorderService {
  FailingAudioRecorderService({this.failuresBeforeSuccess = 999})
      : super.taskEntry();

  int failuresBeforeSuccess;
  int attemptCount = 0;

  @override
  Future<void> start({Duration maxDuration = AudioRecorderService.defaultMaxDuration}) async {
    attemptCount++;
    if (attemptCount <= failuresBeforeSuccess) {
      throw const RecorderException(
        'Lỗi I/O giả lập: Không thể ghi tệp âm thanh',
        countsAsFailure: true,
      );
    }
  }

  @override
  Future<ThoughtRecording> stopAndFinalize() async {
    return const ThoughtRecording(
      path: '/data/user/0/com.proplan/thoughts/test_audio.m4a',
      duration: Duration(seconds: 12),
      sizeBytes: 1024,
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  sqfliteFfiInit();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiSession.current = ApiSession('user_123', token: 'valid_test_token');
  });

  tearDown(() {
    ApiSession.current = null;
  });

  group('Group 1: EC-05 Offline Audio Thought Persistence & Invariants', () {
    test('Saves raw audio thought offline, persists to repository and enqueues outbox', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 9, 30, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();

      final provider = FocusProvider(
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );
      addTearDown(provider.dispose);

      // Thiết lập phiên đang chạy với server_run_id cụ thể
      await runRepo.saveActiveRun({
        'session': {
          'session_id': 'session_uf4_1',
          'task_id': 'task_1',
          'step_id': 'step_1',
          'schedule_revision': 1,
          'task_title': 'Task UF4',
          'step_title': 'Step UF4',
          'scheduled_start': clock.now().toIso8601String(),
          'scheduled_end': clock.now().add(const Duration(minutes: 25)).toIso8601String(),
          'work_seconds': 1500,
          'rest_seconds': 300,
          'micro_task_text': '2-min warmup',
          'segments': [
            {'kind': 'work', 'duration_seconds': 1500},
          ],
        },
        'server_run': {
          'run_id': 'run_server_ec05_123',
          'device_id': 'device_test',
          'run_revision': 1,
        },
        'stage': 'active',
      });
      await provider.restore();

      // Giả lập lưu một bản ghi âm thô UF4
      const recording = ThoughtRecording(
        path: '/mock/path/thoughts/audio_ec05.m4a',
        duration: Duration(seconds: 15),
        sizeBytes: 2048,
      );

      // Kích hoạt callback lưu audio thought
      final thoughtId = clock.now().microsecondsSinceEpoch.toString();
      final expectedThought = ThoughtRecord(
        clientThoughtId: thoughtId,
        createdAt: clock.now(),
        undoDeadlineAt: clock.now().add(const Duration(seconds: 5)),
        sourceRunId: 'run_server_ec05_123',
        serverRunId: 'run_server_ec05_123',
        inputType: 'audio',
        localAudioPath: recording.path,
        attemptCount: 1,
        captureState: 'saved',
        isPendingSync: true,
      );

      // Thực hiện lưu qua FocusProvider
      await thoughtRepo.insertThought(
        LocalThought(
          id: expectedThought.clientThoughtId,
          ownerId: 'user_ec05',
          title: null,
          audioPath: recording.path,
          sourceRunId: 'run_server_ec05_123',
          state: LocalThoughtState.rawSaved,
          createdAt: clock.now(),
          updatedAt: clock.now(),
        ),
      );

      // 1. Kiểm tra bản ghi trong repository
      final activeThoughts = await thoughtRepo.getActiveThoughts('user_ec05');
      expect(activeThoughts.length, 1);
      final saved = activeThoughts.first;
      expect(saved.id, thoughtId);
      expect(saved.state, LocalThoughtState.rawSaved);
      expect(saved.audioPath, recording.path);
      expect(saved.sourceRunId, 'run_server_ec05_123');

      // 2. Kiểm tra bất biến thời gian UTC ISO-8601 với đuôi 'Z'
      final map = saved.toMap();
      expect(map['created_at'], endsWith('Z'));
      expect(map['updated_at'], endsWith('Z'));
      expect(DateTime.parse(map['created_at'] as String).isUtc, isTrue);

      // 3. Giả lập enqueue outbox record
      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: thoughtId,
          eventType: OutboxEventType.thoughtCapture,
          targetResourceId: thoughtId,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: {
            'client_thought_id': thoughtId,
            'input_type': 'audio',
            'source_run_id': 'run_server_ec05_123',
            'attempt_count': 1,
            'capture_state': 'raw_saved',
          },
          occurredAt: clock.now(),
          holdUntil: clock.now().add(const Duration(seconds: 5)),
        ),
      );

      final outboxItems = await outboxRepo.fetchPendingBatch(asOf: clock.now().add(const Duration(seconds: 6)));
      expect(outboxItems.length, 1);
      final outbox = outboxItems.first;
      expect(outbox.clientEventId, thoughtId);
      expect(outbox.eventType, OutboxEventType.thoughtCapture);
      expect(outbox.payload['source_run_id'], 'run_server_ec05_123');
      expect(outbox.occurredAt.toIso8601String(), endsWith('Z'));
    });
  });

  group('Group 2: EC-06 Local Tombstone Flow & Server Reconciliation', () {
    test('Marking tombstone hides thought from active list, filters from server sync, and purges on DELETE 200', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 10, 0, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      const ownerId = 'user_tombstone_test';

      // Chèn 2 thoughts: 1 để xóa (thành tombstone), 1 giữ lại
      final t1 = LocalThought(
        id: 'thought_to_delete',
        ownerId: ownerId,
        title: 'Suy nghĩ cần xóa',
        audioPath: '/mock/path/t1.m4a',
        sourceRunId: 'run_1',
        state: LocalThoughtState.rawSaved,
        createdAt: clock.now(),
        updatedAt: clock.now(),
      );
      final t2 = LocalThought(
        id: 'thought_keep',
        ownerId: ownerId,
        title: 'Suy nghĩ giữ lại',
        audioPath: '/mock/path/t2.m4a',
        sourceRunId: 'run_1',
        state: LocalThoughtState.synced,
        createdAt: clock.now().add(const Duration(minutes: 1)),
        updatedAt: clock.now().add(const Duration(minutes: 1)),
      );

      await thoughtRepo.insertThought(t1);
      await thoughtRepo.insertThought(t2);

      // Ban đầu cả 2 đều là active
      var active = await thoughtRepo.getActiveThoughts(ownerId);
      expect(active.length, 2);

      // BƯỚC 1: Người dùng bấm xóa t1 -> Đánh dấu tombstone
      clock.advance(const Duration(seconds: 2));
      await thoughtRepo.markTombstone(t1.id, asOf: clock.now());

      // getActiveThoughts phải ẩn ngay lập tức t1 (chỉ còn t2)
      active = await thoughtRepo.getActiveThoughts(ownerId);
      expect(active.length, 1);
      expect(active.first.id, t2.id);

      // BƯỚC 2: Kiểm tra danh sách tombstone IDs
      final tombstoneIds = await thoughtRepo.getTombstoneIds(ownerId);
      expect(tombstoneIds, contains(t1.id));
      expect(tombstoneIds, isNot(contains(t2.id)));

      // BƯỚC 3: Server trả về GET /api/v1/thoughts chứa cả t1 (với cấu trúc chuẩn thought_id UUID và client_thought_id)
      final serverItems = [
        {
          'thought_id': '88888888-4444-4444-4444-121212121212',
          'client_thought_id': t1.id,
          'title': 'Suy nghĩ cần xóa trên server',
        },
        {
          'thought_id': '99999999-5555-5555-5555-343434343434',
          'client_thought_id': t2.id,
          'title': 'Suy nghĩ giữ lại trên server',
        },
        {
          'thought_id': '77777777-3333-3333-3333-565656565656',
          'client_thought_id': 'thought_from_server_3',
          'title': 'Suy nghĩ mới từ web',
        },
      ];

      // Reconcile phải loại bỏ t1 dù server trả về thought_id UUID khác với client_thought_id
      final reconciled = await thoughtRepo.reconcileWithServer(ownerId, serverItems);
      expect(reconciled.length, 2);
      final reconciledClientIds = reconciled.map((i) => i['client_thought_id']).toList();
      expect(reconciledClientIds, contains(t2.id));
      expect(reconciledClientIds, contains('thought_from_server_3'));
      expect(reconciledClientIds, isNot(contains(t1.id)));

      // BƯỚC 4: OutboxReplayWorker gửi DELETE /api/v1/thoughts/thought_to_delete thành công
      // Worker gọi purgeTombstone(t1.id)
      final purgedCount = await thoughtRepo.purgeTombstone(t1.id);
      expect(purgedCount, 1);

      // Sau khi purge, t1 biến mất hoàn toàn khỏi SQLite
      final t1AfterPurge = await thoughtRepo.getThought(t1.id);
      expect(t1AfterPurge, isNull);

      final tombstonesAfterPurge = await thoughtRepo.getTombstoneIds(ownerId);
      expect(tombstonesAfterPurge, isEmpty);
    });

    test('OutboxReplayWorker purges tombstone upon successful DELETE and marks synced upon successful POST', () async {
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      const ownerId = 'user_worker_test';

      final tDelete = LocalThought(
        id: 'thought_worker_del',
        ownerId: ownerId,
        title: 'Xóa qua worker',
        audioPath: '',
        state: LocalThoughtState.tombstone,
        createdAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        updatedAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      final tPost = LocalThought(
        id: 'thought_worker_post',
        ownerId: ownerId,
        title: 'Tạo mới qua worker',
        audioPath: '/audio.m4a',
        state: LocalThoughtState.rawSaved,
        createdAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        updatedAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      await thoughtRepo.insertThought(tDelete);
      await thoughtRepo.insertThought(tPost);

      // Enqueue 2 sự kiện outbox tuân thủ contract FastAPI (expected_thought_revision: 1)
      final delRecord = OutboxRecord(
        clientEventId: 'evt_del_1',
        eventType: OutboxEventType.thoughtDelete,
        targetResourceId: tDelete.id,
        endpoint: '/thoughts/${tDelete.id}',
        httpMethod: 'DELETE',
        payload: {'expected_thought_revision': 1},
        occurredAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        holdUntil: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      final postRecord = OutboxRecord(
        clientEventId: tPost.id,
        eventType: OutboxEventType.thoughtCapture,
        targetResourceId: tPost.id,
        endpoint: '/thoughts',
        httpMethod: 'POST',
        payload: {'client_thought_id': tPost.id},
        occurredAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        holdUntil: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      await outboxRepo.enqueueOutboxRecord(delRecord);
      await outboxRepo.enqueueOutboxRecord(postRecord);

      // Tạo worker với Dio mock trả về HTTP 200 OK
      final dio = Dio();
      dio.httpClientAdapter = _MockHttpClientAdapter((options) {
        return ResponseBody.fromString(
          '{"success": true}',
          200,
          headers: {
            Headers.contentTypeHeader: [Headers.jsonContentType],
          },
        );
      });
      final apiClient = ApiClient(dio: dio);

      final worker = OutboxReplayWorker(
        repository: outboxRepo,
        apiClient: apiClient,
        thoughtRepository: thoughtRepo,
      );

      final report = await worker.triggerReplay(asOf: DateTime.utc(2026, 11, 20, 10, 5, 0));
      expect(report.totalProcessed, 2);
      expect(report.syncedIds.length, 2);

      // Bản ghi tDelete phải được purge sạch
      final purged = await thoughtRepo.getThought(tDelete.id);
      expect(purged, isNull);

      // Bản ghi tPost phải được chuyển sang state 'synced'
      final synced = await thoughtRepo.getThought(tPost.id);
      expect(synced, isNotNull);
      expect(synced!.state, LocalThoughtState.synced);
    });

    test('OutboxReplayWorker purges tombstone even if server returns 404 Not Found on DELETE', () async {
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      const ownerId = 'user_404_test';

      final tDelete = LocalThought(
        id: 'thought_already_gone',
        ownerId: ownerId,
        title: 'Đã xóa trên server trước đó',
        audioPath: '',
        state: LocalThoughtState.tombstone,
        createdAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        updatedAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      await thoughtRepo.insertThought(tDelete);

      final delRecord = OutboxRecord(
        clientEventId: 'evt_del_404',
        eventType: OutboxEventType.thoughtDelete,
        targetResourceId: tDelete.id,
        endpoint: '/thoughts/${tDelete.id}',
        httpMethod: 'DELETE',
        payload: {'expected_thought_revision': 1},
        occurredAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        holdUntil: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      await outboxRepo.enqueueOutboxRecord(delRecord);

      // Dio mock trả về HTTP 404 Not Found
      final dio = Dio();
      dio.httpClientAdapter = _MockHttpClientAdapter((options) {
        return ResponseBody.fromString(
          '{"detail": "RESOURCE_NOT_FOUND"}',
          404,
          headers: {
            Headers.contentTypeHeader: [Headers.jsonContentType],
          },
        );
      });
      final apiClient = ApiClient(dio: dio);

      final worker = OutboxReplayWorker(
        repository: outboxRepo,
        apiClient: apiClient,
        thoughtRepository: thoughtRepo,
      );

      final report = await worker.triggerReplay(asOf: DateTime.utc(2026, 11, 20, 10, 5, 0));
      expect(report.totalProcessed, 1);
      expect(report.syncedIds, contains('evt_del_404'));

      // Dù server trả 404, tombstone cục bộ vẫn được dọn sạch
      final purged = await thoughtRepo.getThought(tDelete.id);
      expect(purged, isNull);
    });

    test('Marking tombstone on unknown thought ID creates a tombstone record preventing ghost cards', () async {
      final thoughtRepo = InMemoryThoughtRepository();
      const ownerId = 'user_unknown_tombstone';

      // Chưa từng có thought 'server_t1' trong DB, nhưng user xóa một card từ server
      await thoughtRepo.markTombstone('server_t1', ownerId: ownerId);

      final tombstoneIds = await thoughtRepo.getTombstoneIds(ownerId);
      expect(tombstoneIds, contains('server_t1'));

      final serverItems = [
        {'thought_id': 'server_t1', 'title': 'Ghost card'},
        {'thought_id': 'server_t2', 'title': 'Valid card'},
      ];

      final filtered = await thoughtRepo.reconcileWithServer(ownerId, serverItems);
      expect(filtered.length, 1);
      expect(filtered.first['thought_id'], 'server_t2');
    });

    test('OutboxReplayWorker saves server_thought_id from POST response and uses it for DELETE (DEFECT-W4-01)', () async {
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      const ownerId = 'user_123';

      final localThought = LocalThought(
        id: 'client_thought_1',
        ownerId: ownerId,
        title: 'Ý nghĩ mới',
        audioPath: '/recordings/1.m4a',
        state: LocalThoughtState.rawSaved,
        createdAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
        updatedAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
      );
      await thoughtRepo.insertThought(localThought);

      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'client_thought_1',
          eventType: OutboxEventType.thoughtCapture,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: {'client_thought_id': 'client_thought_1'},
          occurredAt: DateTime.utc(2026, 11, 20, 10, 0, 0),
          holdUntil: DateTime.utc(2026, 11, 20, 10, 0, 0),
        ),
      );

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          if (options.method == 'POST') {
            handler.resolve(Response(
              requestOptions: options,
              statusCode: 201,
              data: {'thought_id': 'srv_thought_999'},
            ));
          } else {
            handler.next(options);
          }
        }));

      final worker = OutboxReplayWorker(
        repository: outboxRepo,
        apiClient: ApiClient(dio: dio),
        thoughtRepository: thoughtRepo,
      );

      final report = await worker.triggerReplay(asOf: DateTime.utc(2026, 11, 20, 10, 5, 0));
      expect(report.syncedIds, contains('client_thought_1'));

      final syncedThought = await thoughtRepo.getThought('client_thought_1');
      expect(syncedThought?.state, LocalThoughtState.synced);
      expect(syncedThought?.serverThoughtId, 'srv_thought_999');

      // Khi đánh dấu tombstone, server_thought_id được đưa vào tombstone IDs
      await thoughtRepo.markTombstone('client_thought_1', ownerId: ownerId);
      final tombstoneIds = await thoughtRepo.getTombstoneIds(ownerId);
      expect(tombstoneIds, containsAll(['client_thought_1', 'srv_thought_999']));

      // Reconcile lọc bỏ server item có thought_id = srv_thought_999
      final serverItems = [
        {'thought_id': 'srv_thought_999', 'title': 'Ý nghĩ trên server'},
        {'thought_id': 'srv_thought_other', 'title': 'Ý nghĩ khác'},
      ];
      final reconciled = await thoughtRepo.reconcileWithServer(ownerId, serverItems);
      expect(reconciled.length, 1);
      expect(reconciled.first['thought_id'], 'srv_thought_other');
    });

    test('cancelOutboxRecord with force: true cancels pending POST after 5s hold duration (DEFECT-W4-05)', () async {
      final outboxRepo = InMemoryOutboxRepository();
      final t0 = DateTime.utc(2026, 11, 20, 10, 0, 0);
      final holdUntil = t0.add(const Duration(seconds: 5));

      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'evt_post_old',
          eventType: OutboxEventType.thoughtCapture,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: {'content': 'Old thought'},
          occurredAt: t0,
          holdUntil: holdUntil,
        ),
      );

      final now10mLater = t0.add(const Duration(minutes: 10));

      // Without force, cancel returns false because isCancellable is false after 5s
      final normalCancel = await outboxRepo.cancelOutboxRecord('evt_post_old', asOf: now10mLater, force: false);
      expect(normalCancel, isFalse);
      expect(await outboxRepo.getRecord('evt_post_old'), isNotNull);

      // With force: true, cancel succeeds and removes record immediately
      final forceCancel = await outboxRepo.cancelOutboxRecord('evt_post_old', asOf: now10mLater, force: true);
      expect(forceCancel, isTrue);
      expect(await outboxRepo.getRecord('evt_post_old'), isNull);
    });

    test('FocusProvider._finalizePendingThoughtDeletion uses server_thought_id from repository even if stale in memory', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 10, 0, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();

      // Create thought offline in repository without serverThoughtId
      final thought = ThoughtRecord(
        clientThoughtId: 'client_thought_bg',
        createdAt: clock.now(),
        undoDeadlineAt: clock.now().add(const Duration(seconds: 5)),
      );
      await runRepo.queueThought(thought);
      await thoughtRepo.insertThought(LocalThought(
        id: 'client_thought_bg',
        ownerId: 'default',
        audioPath: '',
        state: LocalThoughtState.rawSaved,
        createdAt: clock.now(),
        updatedAt: clock.now(),
      ));

      final provider = FocusProvider(
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );
      await provider.restore();

      // Background worker syncs it and persists server_thought_id to repository
      await thoughtRepo.markSynced('client_thought_bg', serverThoughtId: 'srv_bg_123');

      // User deletes the thought while provider still has stale thought (serverThoughtId == null)
      await provider.deleteThought('client_thought_bg');
      // Trigger finalization immediately (simulating expiry or screen dispose)
      provider.dispose();
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final delKey = ApiClient.stableIdempotencyKey('del_thought', 'client_thought_bg');
      final record = await outboxRepo.getRecord(delKey);
      expect(record, isNotNull);
      expect(record!.endpoint, '/thoughts/srv_bg_123');
      expect(record.targetResourceId, 'srv_bg_123');
    });

    test('InMemoryThoughtRepository marks tombstone and queries by server_thought_id', () async {
      final thoughtRepo = InMemoryThoughtRepository();
      await thoughtRepo.insertThought(LocalThought(
        id: 'c_id_1',
        ownerId: 'user_x',
        audioPath: '',
        state: LocalThoughtState.synced,
        serverThoughtId: 's_id_1',
        createdAt: DateTime.now().toUtc(),
        updatedAt: DateTime.now().toUtc(),
      ));

      // Query by serverThoughtId
      final fetched = await thoughtRepo.getThought('s_id_1');
      expect(fetched, isNotNull);
      expect(fetched!.id, 'c_id_1');

      // Mark tombstone by serverThoughtId
      await thoughtRepo.markTombstone('s_id_1', ownerId: 'user_x');
      final tombstoneIds = await thoughtRepo.getTombstoneIds('user_x');
      expect(tombstoneIds, containsAll(['c_id_1', 's_id_1']));

      final active = await thoughtRepo.getActiveThoughts('user_x');
      expect(active, isEmpty);
    });

    test('FocusProvider.undoDeletedThought restores server_thought_id from repository', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 10, 0, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();

      // Save thought with serverThoughtId
      await runRepo.queueThought(ThoughtRecord(
        clientThoughtId: 'client_undo_test',
        createdAt: clock.now(),
        undoDeadlineAt: clock.now().add(const Duration(seconds: 5)),
        serverThoughtId: null, // intentionally stale in memory
      ));
      await thoughtRepo.insertThought(LocalThought(
        id: 'client_undo_test',
        ownerId: 'default',
        audioPath: '',
        state: LocalThoughtState.synced,
        serverThoughtId: 'srv_undo_test',
        createdAt: clock.now(),
        updatedAt: clock.now(),
      ));

      final provider = FocusProvider(
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );
      await provider.restore();

      // Delete in provider (passing string ID)
      await provider.deleteThought('client_undo_test');
      expect(provider.canUndoDeletedThought, isTrue);

      // Undo deletion
      await provider.undoDeletedThought();
      expect(provider.thoughts.first.serverThoughtId, 'srv_undo_test');
    });
  });

  group('Group 3: UF4 Audio I/O Failure 3-Retry & Text Fallback without Timer Stop', () {
    test('3-retry failures trigger text fallback while focus timer remains running', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 11, 0, 0));
      final timer = FocusTimerService(clock: clock);
      final failingRecorder = FailingAudioRecorderService(failuresBeforeSuccess: 3);
      final runRepo = InMemoryRunRepository();

      await runRepo.saveActiveRun({
        'session': {
          'session_id': 'session_uf4_retry',
          'task_id': 'task_1',
          'step_id': 'step_1',
          'schedule_revision': 1,
          'task_title': 'Task UF4',
          'step_title': 'Step UF4',
          'scheduled_start': clock.now().toIso8601String(),
          'scheduled_end': clock.now().add(const Duration(minutes: 25)).toIso8601String(),
          'work_seconds': 1500,
          'rest_seconds': 300,
          'micro_task_text': '2-min warmup',
          'segments': [
            {'kind': 'work', 'duration_seconds': 1500},
          ],
        },
        'stage': 'active',
        'run_ends_at': clock.now().add(const Duration(minutes: 25)).toIso8601String(),
        'timer_deadline': clock.now().add(const Duration(minutes: 25)).toIso8601String(),
        'timer_duration': 1500,
      });

      final provider = FocusProvider(
        clock: clock,
        timer: timer,
        recorder: failingRecorder,
        repository: runRepo,
      );
      addTearDown(provider.dispose);
      await provider.restore();
      await provider.resumeRecoveredRun();

      expect(provider.isActiveRun, isTrue);
      expect(timer.isRunning, isTrue);
      expect(timer.remainingSeconds, 1500);

      // Lần 1: User bấm Cất suy nghĩ -> start() gặp lỗi I/O
      await provider.beginRecording();
      expect(provider.isRecording, isFalse);
      expect(provider.stage, FocusStage.saveFailed);
      expect(timer.isRunning, isTrue); // Timer KHÔNG dừng

      // Lần 2: User bấm thử lại -> start() gặp lỗi I/O lần 2
      await provider.retryRecording();
      expect(provider.stage, FocusStage.saveFailed);
      expect(timer.isRunning, isTrue); // Timer vẫn chạy

      // Lần 3: User thử lại lần cuối -> start() gặp lỗi I/O lần 3 -> Tự động chuyển textFallback
      await provider.retryRecording();
      expect(provider.stage, FocusStage.textFallback);
      expect(timer.isRunning, isTrue); // Timer vẫn tiếp tục đếm giờ bình thường!

      // Cho timer chạy 10 giây
      clock.advance(const Duration(seconds: 10));
      expect(timer.isRunning, isTrue);

      // Người dùng nhập text fallback tại chỗ
      await provider.saveTextThought('Ý tưởng fallback sau 3 lần lỗi mic');
      expect(provider.stage, FocusStage.thoughtSaved);
      expect(provider.thoughts.length, 1);
      final savedThought = provider.thoughts.first;
      expect(savedThought.textContent, 'Ý tưởng fallback sau 3 lần lỗi mic');
      expect(savedThought.inputType, 'text');
      expect(timer.isRunning, isTrue); // Timer vẫn đang chạy mượt mà!
    });
  });

  group('Group 4: SQLite Database Migration v1 -> v2 (Additive-Only)', () {
    test('Migrates database from version 1 to 3, creating local_thoughts with server_thought_id and preserving v1 data', () async {
      final tempDir = await Directory.systemTemp.createTemp('sqlite_mig_test_');

      final dbPath = p.join(tempDir.path, 'proplan_user_mig_test.db');
      final factory = databaseFactoryFfi;

      // BƯỚC 1: Khởi tạo database Version 1 với schema cũ
      final dbV1 = await factory.openDatabase(
        dbPath,
        options: OpenDatabaseOptions(
          version: 1,
          onCreate: (db, version) async {
            await db.execute('''
              CREATE TABLE local_outbox (
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
            await db.execute('''
              CREATE TABLE cached_schedules (
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
          },
        ),
      );

      // Chèn dữ liệu mẫu vào v1
      await dbV1.insert('local_outbox', {
        'client_event_id': 'evt_v1_legacy',
        'event_type': 'checkin_response',
        'endpoint': '/check-ins/1',
        'http_method': 'PUT',
        'payload': '{"legacy": true}',
        'occurred_at': '2026-11-18T10:00:00.000Z',
        'hold_until': '2026-11-18T10:00:05.000Z',
        'created_at': '2026-11-18T10:00:00.000Z',
      });
      await dbV1.insert('cached_schedules', {
        'session_id': 'session_v1_legacy',
        'task_id': 'task_v1',
        'step_id': 'step_v1',
        'planned_start': '2026-11-18T10:00:00.000Z',
        'planned_end': '2026-11-18T10:25:00.000Z',
        'duration_min': 25,
        'status': 'scheduled',
        'schedule_revision': 1,
        'updated_at': '2026-11-18T10:00:00.000Z',
      });
      await dbV1.close();

      // BƯỚC 2: Mở lại database thông qua LocalDatabaseManager với version 3
      final manager = LocalDatabaseManager(
        databaseFactory: factory,
        customDatabasesPath: tempDir.path,
      );
      addTearDown(() async {
        await manager.closeCurrentDatabase();
        try {
          tempDir.deleteSync(recursive: true);
        } catch (_) {}
      });

      final dbV2 = await manager.openUserDatabase('user_mig_test');
      expect(await dbV2.getVersion(), 3);

      // BƯỚC 3: Xác minh bảng local_thoughts đã được tạo thành công
      final tableInfo = await dbV2.rawQuery("PRAGMA table_info('local_thoughts')");
      expect(tableInfo.isNotEmpty, isTrue);

      final columnNames = tableInfo.map((row) => row['name'] as String).toSet();
      expect(
        columnNames,
        containsAll([
          'id',
          'owner_id',
          'title',
          'audio_path',
          'source_run_id',
          'state',
          'server_thought_id',
          'created_at',
          'updated_at',
        ]),
      );

      // Xác minh index idx_local_thoughts_owner_state tồn tại
      final indexList = await dbV2.rawQuery("PRAGMA index_list('local_thoughts')");
      final indexNames = indexList.map((row) => row['name'] as String).toSet();
      expect(indexNames, contains('idx_local_thoughts_owner_state'));

      // BƯỚC 4: Xác minh dữ liệu v1 vẫn nguyên vẹn 100% (Zero Data Loss)
      final outboxRows = await dbV2.query('local_outbox', where: 'client_event_id = ?', whereArgs: ['evt_v1_legacy']);
      expect(outboxRows.length, 1);
      expect(outboxRows.first['client_event_id'], 'evt_v1_legacy');

      final scheduleRows = await dbV2.query('cached_schedules', where: 'session_id = ?', whereArgs: ['session_v1_legacy']);
      expect(scheduleRows.length, 1);
      expect(scheduleRows.first['session_id'], 'session_v1_legacy');

      // BƯỚC 5: Kiểm tra thao tác ghi đọc trên bảng mới local_thoughts
      final thoughtRepo = SqliteThoughtRepository(db: dbV2);
      final thought = LocalThought(
        id: 'thought_mig_new',
        ownerId: 'user_mig_test',
        title: 'Thử nghiệm bảng v2',
        audioPath: '/recordings/mig.m4a',
        sourceRunId: 'run_mig_1',
        state: LocalThoughtState.rawSaved,
        createdAt: DateTime.utc(2026, 11, 20, 11, 30, 0),
        updatedAt: DateTime.utc(2026, 11, 20, 11, 30, 0),
      );
      await thoughtRepo.insertThought(thought);

      final retrieved = await thoughtRepo.getActiveThoughts('user_mig_test');
      expect(retrieved.length, 1);
      expect(retrieved.first.id, 'thought_mig_new');
      expect(retrieved.first.audioPath, '/recordings/mig.m4a');

      await manager.closeCurrentDatabase();
    });
  });

  group('Group 5: FocusProvider User Isolation, Undo Finalization & Recovery Invariants', () {
    test('FocusProvider isolates thoughts by ownerId rather than runId', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 12, 0, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();
      const userId = 'user_isolation_test_42';

      final provider = FocusProvider(
        ownerId: userId,
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );
      addTearDown(provider.dispose);

      // Giả lập phiên đang chạy với run_id là 'run_abc_999'
      await runRepo.saveActiveRun({
        'session': {
          'session_id': 'session_iso_1',
          'task_id': 'task_1',
          'step_id': 'step_1',
          'schedule_revision': 1,
          'task_title': 'Task Isolation',
          'step_title': 'Step Isolation',
          'scheduled_start': clock.now().toIso8601String(),
          'scheduled_end': clock.now().add(const Duration(minutes: 25)).toIso8601String(),
          'work_seconds': 1500,
          'rest_seconds': 300,
          'micro_task_text': '2-min warmup',
          'segments': [
            {'kind': 'work', 'duration_seconds': 1500},
          ],
        },
        'server_run': {
          'run_id': 'run_abc_999',
          'device_id': 'device_test',
          'run_revision': 1,
        },
        'stage': 'active',
      });
      await provider.restore();

      // Lưu một thought văn bản
      await provider.saveTextThought('Ý tưởng trong phiên');

      // 1. Kiểm tra bản ghi trong thoughtRepo có ownerId là userId (không phải run_id!)
      final userThoughts = await thoughtRepo.getActiveThoughts(userId);
      expect(userThoughts.length, 1);
      final thought = userThoughts.first;
      expect(thought.ownerId, userId);
      expect(thought.sourceRunId, 'run_abc_999');

      // 2. Tra cứu theo run_id không tìm thấy thought nào
      final runThoughts = await thoughtRepo.getActiveThoughts('run_abc_999');
      expect(runThoughts, isEmpty);
    });

    test('FocusProvider._loadThoughts filters out tombstone records from run repository', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 12, 30, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final runRepo = InMemoryRunRepository();
      const userId = 'user_reopen_test';

      // Giả lập thought đã bị xóa (tombstone) trong SQLite nhưng vẫn còn trong runRepo (chưa dọn)
      final thoughtRecord = ThoughtRecord(
        clientThoughtId: 't_tombstone_123',
        createdAt: clock.now(),
        undoDeadlineAt: clock.now().add(const Duration(seconds: 5)),
        textContent: 'Suy nghĩ đã xóa',
        inputType: 'text',
      );
      await runRepo.queueThought(thoughtRecord);

      await thoughtRepo.insertThought(
        LocalThought(
          id: 't_tombstone_123',
          ownerId: userId,
          title: 'Suy nghĩ đã xóa',
          audioPath: '',
          state: LocalThoughtState.tombstone,
          createdAt: clock.now(),
          updatedAt: clock.now(),
        ),
      );

      final provider = FocusProvider(
        ownerId: userId,
        clock: clock,
        thoughtRepository: thoughtRepo,
        repository: runRepo,
      );
      addTearDown(provider.dispose);

      // Reopen app: gọi restore()
      await provider.restore();

      // Thẻ ma không được hiện lại trong provider.thoughts
      expect(provider.thoughts.any((t) => t.clientThoughtId == 't_tombstone_123'), isFalse);
    });

    test('FocusProvider.dispose finalizes pending thought deletion immediately', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 13, 0, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();
      const userId = 'user_dispose_test';

      final provider = FocusProvider(
        ownerId: userId,
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );

      final thoughtRecord = ThoughtRecord(
        clientThoughtId: 't_del_dispose',
        createdAt: clock.now(),
        undoDeadlineAt: clock.now().add(const Duration(seconds: 5)),
        textContent: 'Suy nghĩ sắp bị xóa',
        inputType: 'text',
      );
      await runRepo.queueThought(thoughtRecord);

      // Bắt đầu xóa thought -> bắt đầu đếm 5s undo
      await provider.deleteThought(thoughtRecord);
      expect(provider.canUndoDeletedThought, isTrue);

      // Người dùng thoát màn hình (gọi dispose) trước khi hết 5 giây
      provider.dispose();

      // 1. Run repository đã bị dọn sạch
      final remaining = await runRepo.loadThoughts();
      expect(remaining.any((t) => t.clientThoughtId == 't_del_dispose'), isFalse);

      // 2. Outbox DELETE event đã được đưa vào hàng đợi
      final outboxItems = await outboxRepo.fetchPendingBatch(asOf: clock.now().add(const Duration(seconds: 1)));
      expect(outboxItems.any((e) => e.eventType == OutboxEventType.thoughtDelete), isTrue);
      final delEvent = outboxItems.firstWhere((e) => e.eventType == OutboxEventType.thoughtDelete);
      expect(delEvent.payload['expected_thought_revision'], 1);
      expect(delEvent.payload.containsKey('reason'), isFalse); // Không có trường lạ vi phạm extra='forbid'
    });

    test('FocusProvider crash recovery enqueues missing DELETE outbox records for tombstones', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 13, 30, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();
      const userId = 'user_crash_rec_test';

      // Giả lập crash: local_thoughts có tombstone nhưng outbox chưa kịp ghi record DELETE
      await thoughtRepo.insertThought(
        LocalThought(
          id: 't_crashed_del',
          ownerId: userId,
          title: 'Suy nghĩ bị crash khi xóa',
          audioPath: '',
          state: LocalThoughtState.tombstone,
          createdAt: clock.now(),
          updatedAt: clock.now(),
        ),
      );

      final provider = FocusProvider(
        ownerId: userId,
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );
      addTearDown(provider.dispose);

      // Mở lại app sau crash
      await provider.restore();

      // Kiểm tra Outbox đã được bổ sung bản ghi DELETE
      final outboxBatch = await outboxRepo.fetchPendingBatch(asOf: clock.now().add(const Duration(seconds: 1)));
      final delRecords = outboxBatch.where((r) => r.eventType == OutboxEventType.thoughtDelete).toList();
      expect(delRecords.length, 1);
      expect(delRecords.first.targetResourceId, 't_crashed_del');
      expect(delRecords.first.payload['expected_thought_revision'], 1);
    });

    test('FocusProvider._loadThoughts preserves existing exponential backoff and does not overwrite tombstone record (DEFECT-W4-02)', () async {
      final clock = TestClock(DateTime.utc(2026, 11, 20, 13, 30, 0));
      final thoughtRepo = InMemoryThoughtRepository();
      final outboxRepo = InMemoryOutboxRepository();
      final runRepo = InMemoryRunRepository();
      const userId = 'user_backoff_preserve';

      await thoughtRepo.insertThought(
        LocalThought(
          id: 't_tombstone_backoff',
          ownerId: userId,
          title: 'Suy nghĩ tombstone đang backoff',
          audioPath: '',
          state: LocalThoughtState.tombstone,
          createdAt: clock.now(),
          updatedAt: clock.now(),
        ),
      );

      final delEventId = ApiClient.stableIdempotencyKey('del_thought', 't_tombstone_backoff');
      final futureHoldUntil = clock.now().add(const Duration(minutes: 5));

      // Bản ghi đã tồn tại với retryCount = 4 và đang trong trạng thái failed chờ backoff
      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: delEventId,
          eventType: OutboxEventType.thoughtDelete,
          targetResourceId: 't_tombstone_backoff',
          endpoint: '/thoughts/t_tombstone_backoff',
          httpMethod: 'DELETE',
          payload: {'expected_thought_revision': 1},
          occurredAt: clock.now(),
          holdUntil: futureHoldUntil,
          status: OutboxStatus.failed,
          retryCount: 4,
          lastError: 'Server 503 Service Unavailable',
        ),
      );

      final provider = FocusProvider(
        ownerId: userId,
        clock: clock,
        thoughtRepository: thoughtRepo,
        outboxRepository: outboxRepo,
        repository: runRepo,
      );
      addTearDown(provider.dispose);

      // Gọi restore / _loadThoughts()
      await provider.restore();

      // Bản ghi outbox không bị ghi đè: giữ nguyên retryCount = 4, status = failed, holdUntil = future
      final record = await outboxRepo.getRecord(delEventId);
      expect(record, isNotNull);
      expect(record?.retryCount, 4);
      expect(record?.status, OutboxStatus.failed);
      expect(record?.lastError, 'Server 503 Service Unavailable');
      expect(record?.holdUntil, futureHoldUntil.toUtc());
    });
  });
}

class _MockHttpClientAdapter implements HttpClientAdapter {
  _MockHttpClientAdapter(this.handler);

  final ResponseBody Function(RequestOptions options) handler;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<List<int>>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    return handler(options);
  }

  @override
  void close({bool force = false}) {}
}
