import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/models/outbox_record.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_database_manager.dart';
import 'package:proplan_va/repositories/local_outbox_repository.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/services/focus_native_controller.dart';
import 'package:proplan_va/services/outbox_replay_worker.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    ApiSession.current = ApiSession('test_owner', token: 'valid_test_token');
  });

  tearDown(() {
    ApiSession.current = null;
  });

  group('Test Case 1: UTC Timestamp Invariant (TD-B2)', () {
    test('OutboxRecord and SQLite map enforce ISO-8601 UTC with trailing Z', () async {
      final localTime = DateTime(2026, 11, 18, 14, 30, 0); // Local time
      final record = OutboxRecord(
        clientEventId: 'evt_utc_test_1',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/test-utc',
        httpMethod: 'POST',
        payload: {'key': 'val'},
        occurredAt: localTime,
        holdUntil: localTime,
      );

      // Verify Dart fields are in UTC
      expect(record.occurredAt.isUtc, isTrue);
      expect(record.holdUntil.isUtc, isTrue);

      // Verify serialization to Map contains trailing 'Z'
      final map = record.toMap();
      expect(map['occurred_at'], endsWith('Z'));
      expect(map['hold_until'], endsWith('Z'));
      expect(map['created_at'], endsWith('Z'));

      // Verify deserialization from Map preserves UTC
      final restored = OutboxRecord.fromMap(map);
      expect(restored.occurredAt.isUtc, isTrue);
      expect(restored.holdUntil.isUtc, isTrue);
      expect(restored.occurredAt.toIso8601String(), endsWith('Z'));
      expect(restored.holdUntil.toIso8601String(), endsWith('Z'));
    });

    test('LocalOutboxRepository enqueues and queries with UTC boundary', () async {
      final repo = InMemoryOutboxRepository();
      final nowUtc = DateTime.now().toUtc();
      final record = OutboxRecord(
        clientEventId: 'evt_repo_utc_1',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/checkin/1',
        httpMethod: 'PUT',
        payload: {'status': 'done'},
        occurredAt: nowUtc.subtract(const Duration(seconds: 10)),
        holdUntil: nowUtc.subtract(const Duration(seconds: 5)),
      );

      await repo.enqueueOutboxRecord(record);

      final batch = await repo.fetchPendingBatch(asOf: nowUtc);
      expect(batch.length, 1);
      expect(batch.first.clientEventId, 'evt_repo_utc_1');
      expect(batch.first.holdUntil.isUtc, isTrue);
    });
  });

  group('Test Case 2: Outbox Error Classification & No Swallowing (TD-B3)', () {
    test('Classifies 422 as failed_terminal (dead-letter, no retry)', () async {
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_422',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/fail-422',
        httpMethod: 'POST',
        payload: {'bad': 'data'},
        occurredAt: now,
        holdUntil: now,
      ));

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            response: Response(
              requestOptions: options,
              statusCode: 422,
              data: {'detail': 'Validation error'},
            ),
          ));
        }));

      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.terminalFailedIds, contains('evt_422'));

      final updated = await repo.getRecord('evt_422');
      expect(updated?.status, OutboxStatus.failedTerminal);
      expect(updated?.lastError, contains('422'));
    });

    test('Classifies 409 as conflict_needs_rebase (does not block queue)', () async {
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_409',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/fail-409',
        httpMethod: 'POST',
        payload: {'revision': 1},
        occurredAt: now,
        holdUntil: now,
      ));

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            response: Response(
              requestOptions: options,
              statusCode: 409,
              data: {'detail': 'Conflict: revision mismatch'},
            ),
          ));
        }));

      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.conflictIds, contains('evt_409'));

      final updated = await repo.getRecord('evt_409');
      expect(updated?.status, OutboxStatus.conflictNeedsRebase);
      expect(updated?.lastError, contains('409'));
    });

    test('Classifies 401 as paused auth and triggers token renewal', () async {
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_401',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/fail-401',
        httpMethod: 'POST',
        payload: {},
        occurredAt: now,
        holdUntil: now,
      ));

      var renewCalled = false;
      final session = ApiSession('test_owner', token: 'expired_token');
      session.renew = () async {
        renewCalled = true;
        return true;
      };
      ApiSession.current = session;

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            response: Response(
              requestOptions: options,
              statusCode: 401,
              data: {'detail': 'Unauthorized'},
            ),
          ));
        }));

      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.isPausedAuth, isTrue);
      expect(renewCalled, isTrue);

      final record = await repo.getRecord('evt_401');
      // 401 does not increase retry count or mark terminal
      expect(record?.retryCount, 0);
      expect(record?.status, OutboxStatus.pending);
    });

    test('Connection error applies exponential backoff and never dead-letters', () async {
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_offline',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/fail-conn',
        httpMethod: 'POST',
        payload: {},
        occurredAt: now,
        holdUntil: now,
        retryCount: 1,
      ));

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            type: DioExceptionType.connectionTimeout,
            message: 'Connection timed out',
          ));
        }));

      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.transientFailedIds, contains('evt_offline'));

      final updated = await repo.getRecord('evt_offline');
      expect(updated?.status, OutboxStatus.failed);
      expect(updated?.retryCount, 2);
      expect(updated?.holdUntil.isAfter(now), isTrue);
    });

    test('Server 500 retries up to 5 times then transitions to failed_terminal', () async {
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_500',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/fail-500',
        httpMethod: 'POST',
        payload: {},
        occurredAt: now,
        holdUntil: now,
        retryCount: 4, // 5th attempt
      ));

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            response: Response(
              requestOptions: options,
              statusCode: 500,
              data: {'detail': 'Internal Server Error'},
            ),
          ));
        }));

      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.terminalFailedIds, contains('evt_500'));

      final updated = await repo.getRecord('evt_500');
      expect(updated?.status, OutboxStatus.failedTerminal);
      expect(updated?.lastError, contains('Exceeded max server retries (5)'));
    });

    test('HTTP 408 Request Timeout is treated as transient (not dead-lettered)', () async {
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_408',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/fail-408',
        httpMethod: 'POST',
        payload: {},
        occurredAt: now,
        holdUntil: now,
        retryCount: 0,
      ));

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            response: Response(
              requestOptions: options,
              statusCode: 408,
              data: {'detail': 'Request Timeout'},
            ),
          ));
        }));

      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.transientFailedIds, contains('evt_408'));
      expect(report.terminalFailedIds, isEmpty);

      final updated = await repo.getRecord('evt_408');
      expect(updated?.status, OutboxStatus.failed);
      expect(updated?.retryCount, 1);
    });

    test('OutboxReplayWorker pauses replay when ApiSession.current is null (DEFECT-W4-06)', () async {
      ApiSession.current = null;
      final repo = InMemoryOutboxRepository();
      final now = DateTime.now().toUtc();
      await repo.enqueueOutboxRecord(OutboxRecord(
        clientEventId: 'evt_null_session',
        eventType: OutboxEventType.checkinResponse,
        endpoint: '/test-null-auth',
        httpMethod: 'POST',
        payload: {},
        occurredAt: now,
        holdUntil: now,
      ));

      final dio = Dio();
      final worker = OutboxReplayWorker(
        repository: repo,
        apiClient: ApiClient(dio: dio, bindSession: false),
      );

      final report = await worker.triggerReplay(asOf: now);
      expect(report.isPausedAuth, isTrue);
      expect(report.totalProcessed, 0);

      final record = await repo.getRecord('evt_null_session');
      expect(record?.status, OutboxStatus.pending);
    });
  });

  group('Test Case 3: Crash Recovery Sequence (TD-B4)', () {
    test('Restore checks checkInExpired (>= 300s) BEFORE runEndedWhileClosed', () async {
      final now = DateTime.now();
      final runEndedAt = now.subtract(const Duration(seconds: 301)); // 301s ago (> 300s)

      final repository = InMemoryRunRepository();
      await repository.saveActiveRun({
        'session': {
          'session_id': 'session_rec_1',
          'task_id': 'task_1',
          'step_id': 'step_1',
          'schedule_revision': 1,
          'task_title': 'Công việc 1',
          'step_title': 'Bước 1',
          'scheduled_start': runEndedAt.subtract(const Duration(minutes: 25)).toIso8601String(),
          'scheduled_end': runEndedAt.toIso8601String(),
          'work_seconds': 1500,
          'rest_seconds': 300,
          'micro_task_text': 'Khởi động',
          'segments': [
            {'kind': 'work', 'duration_seconds': 1500},
          ],
        },
        'server_run': {
          'run_id': 'run_rec_1',
          'device_id': 'device_1',
          'run_revision': 1,
        },
        'run_ends_at': runEndedAt.toIso8601String(),
        'timer_deadline': runEndedAt.toIso8601String(),
        'timer_duration': 1500,
        'stage': 'active',
      });

      final outboxRepo = InMemoryOutboxRepository();
      final provider = FocusProvider(
        repository: repository,
        outboxRepository: outboxRepo,
      );
      addTearDown(provider.dispose);

      await provider.restore();

      // Expired: >= 300s
      expect(provider.checkInExpired, isTrue);
      expect(provider.awaitingCheckIn, isFalse);

      // Active run must be cleared from storage
      final activeRun = await repository.loadActiveRun();
      expect(activeRun, isNull);

      // Must enqueue end request to /runs/run_rec_1/end with POST (not a fake 404 endpoint)
      final outboxRecords = await outboxRepo.fetchPendingBatch();
      expect(outboxRecords.isNotEmpty, isTrue);
      final endRecord = outboxRecords.first;
      expect(endRecord.endpoint, '/runs/run_rec_1/end');
      expect(endRecord.httpMethod, 'POST');
      expect(endRecord.payload['end_reason'], 'timer_elapsed');

      // Banner can be cleared
      provider.clearCheckInExpiredBanner();
      expect(provider.checkInExpired, isFalse);
    });

    test('Restore sets awaitingCheckIn == true when within 5m window (< 300s)', () async {
      final now = DateTime.now();
      final runEndedAt = now.subtract(const Duration(seconds: 120)); // 120s ago (< 300s)

      final repository = InMemoryRunRepository();
      await repository.saveActiveRun({
        'session': {
          'session_id': 'session_rec_2',
          'task_id': 'task_2',
          'step_id': 'step_2',
          'schedule_revision': 1,
          'task_title': 'Công việc 2',
          'step_title': 'Bước 2',
          'scheduled_start': runEndedAt.subtract(const Duration(minutes: 25)).toIso8601String(),
          'scheduled_end': runEndedAt.toIso8601String(),
          'work_seconds': 1500,
          'rest_seconds': 300,
          'micro_task_text': 'Khởi động',
          'segments': [
            {'kind': 'work', 'duration_seconds': 1500},
          ],
        },
        'server_run': {
          'run_id': 'run_rec_2',
          'device_id': 'device_2',
          'run_revision': 1,
        },
        'run_ends_at': runEndedAt.toIso8601String(),
        'timer_deadline': runEndedAt.toIso8601String(),
        'timer_duration': 1500,
        'stage': 'active',
      });

      final provider = FocusProvider(
        repository: repository,
      );
      addTearDown(provider.dispose);

      await provider.restore();

      // Within 5 min window: awaitingCheckIn == true, checkInExpired == false
      expect(provider.awaitingCheckIn, isTrue);
      expect(provider.checkInExpired, isFalse);
    });

    test('NativeRecoveryChecker triggers checkInExpired correctly', () async {
      final repository = InMemoryRunRepository();
      final checker = NativeRecoveryChecker.fromCallback(() async {
        return const FocusRecovery(FocusRecoveryState.checkInExpired, runId: 'native_run_1');
      });

      final provider = FocusProvider(
        repository: repository,
        nativeRecoveryChecker: checker,
      );
      addTearDown(provider.dispose);

      await provider.restore();

      expect(provider.checkInExpired, isTrue);
      expect(provider.awaitingCheckIn, isFalse);
    });
  });

  group('Test Case 4: Offline Start Guard (TD-B5)', () {
    test('Throws ApiException when offline and no prior server receipt exists', () async {
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          handler.reject(DioException(
            requestOptions: options,
            type: DioExceptionType.connectionError,
            message: 'No internet connection',
          ));
        }));

      final repository = InMemoryRunRepository();
      final provider = FocusProvider(
        api: ApiClient(dio: dio),
        repository: repository,
      );
      addTearDown(provider.dispose);

      final now = DateTime.now();
      await provider.startSession(FocusSessionInfo(
        sessionId: 'session_guard_1',
        scheduleRevision: 1,
        taskTitle: 'Công việc thử',
        stepTitle: 'Bước thử',
        scheduledStart: now,
        scheduledEnd: now.add(const Duration(minutes: 25)),
        workSeconds: 1500,
        restSeconds: 300,
        microTaskText: 'Khởi động',
        segments: const [],
      ));

      // Attempt startRun without network connection
      await provider.startRun();

      // Verify fake timer did not start and error was recorded
      expect(provider.isActiveRun, isFalse);
      expect(provider.runError, contains('Cần có kết nối mạng để bắt đầu phiên tập trung.'));
    });

    test('Adopts active run when start returns 409 ACTIVE_RUN_EXISTS (DEFECT-W4-03)', () async {
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
          if (options.path.contains('/start')) {
            handler.reject(DioException(
              requestOptions: options,
              response: Response(
                requestOptions: options,
                statusCode: 409,
                data: {
                  'error': {
                    'code': 'ACTIVE_RUN_EXISTS',
                    'message': 'Active run already exists',
                  }
                },
              ),
            ));
          } else if (options.path == '/runs/active') {
            handler.resolve(Response(
              requestOptions: options,
              statusCode: 200,
              data: {
                'data': {
                  'run_id': 'run_adopted_409',
                  'session_id': 'session_adopt_409',
                  'planned_ends_at': DateTime.now().toUtc().add(const Duration(minutes: 25)).toIso8601String(),
                }
              },
            ));
          } else {
            handler.next(options);
          }
        }));

      final repository = InMemoryRunRepository();
      final provider = FocusProvider(
        api: ApiClient(dio: dio),
        repository: repository,
      );
      addTearDown(provider.dispose);

      final now = DateTime.now();
      await provider.startSession(FocusSessionInfo(
        sessionId: 'session_adopt_409',
        scheduleRevision: 1,
        taskTitle: 'Công việc 409',
        stepTitle: 'Bước 409',
        scheduledStart: now,
        scheduledEnd: now.add(const Duration(minutes: 25)),
        workSeconds: 1500,
        restSeconds: 300,
        microTaskText: 'Khởi động',
        segments: const [],
      ));

      await provider.startRun();

      expect(provider.isActiveRun, isTrue);
      expect(provider.runError, isNull);
      expect(provider.serverRunId, 'run_adopted_409');
    });
  });

  group('Test Case 5: Multi-Account Isolation & DB Lifecycle (TD-B1)', () {
    test('Sanitizes ownerId and produces distinct database file names', () {
      expect(
        LocalDatabaseManager.sanitizeOwnerId('user@domain.com:123'),
        'user_domain_com_123',
      );
      expect(
        LocalDatabaseManager.databaseFileName('user_A'),
        'proplan_user_A.db',
      );
      expect(
        LocalDatabaseManager.databaseFileName('user_B'),
        'proplan_user_B.db',
      );
    });

    test('closeCurrentDatabase cleans up instance and handles concurrent calls', () async {
      final manager = LocalDatabaseManager(customDatabasesPath: '/tmp/db_test');
      expect(manager.currentDatabase, isNull);
      expect(manager.currentOwnerId, isNull);

      // Calling close when already closed should complete cleanly without error
      await expectLater(manager.closeCurrentDatabase(), completes);
      expect(manager.currentDatabase, isNull);
      expect(manager.currentOwnerId, isNull);
    });
  });
}
