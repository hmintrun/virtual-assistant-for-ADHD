import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/models/outbox_record.dart';
import 'package:proplan_va/models/task_model.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/providers/streak_provider.dart';
import 'package:proplan_va/providers/theme_provider.dart';
import 'package:proplan_va/repositories/local_outbox_repository.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/repositories/local_thought_repository.dart';
import 'package:proplan_va/screens/focus_review_screens.dart';
import 'package:proplan_va/screens/settings_screen.dart';
import 'package:proplan_va/services/adaptation_api.dart';
import 'package:proplan_va/services/alarm_service.dart';
import 'package:proplan_va/services/audio_recorder_service.dart';
import 'package:proplan_va/services/calendar_api.dart';
import 'package:proplan_va/services/device_telemetry_service.dart';
import 'package:proplan_va/services/google_calendar_api.dart';
import 'package:proplan_va/services/outbox_replay_worker.dart';
import 'package:proplan_va/services/pre_session_reminder_sync.dart';
import 'package:proplan_va/services/schedule_service.dart';
import 'package:proplan_va/services/timer_service.dart';
import 'package:proplan_va/widgets/calendar_fallback.dart';
import 'package:proplan_va/widgets/focus_stage_widgets.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';
import 'package:proplan_va/widgets/task_delete_sheet.dart';

class _TestClock implements Clock {
  _TestClock(this.nowTime);

  DateTime nowTime;

  @override
  DateTime now() => nowTime;
}

class _FailingAudioRecorder extends AudioRecorderService {
  _FailingAudioRecorder() : super.taskEntry();

  int startAttempts = 0;

  @override
  Future<void> start({
    Duration maxDuration = AudioRecorderService.defaultMaxDuration,
  }) async {
    startAttempts++;
    throw const RecorderException(
      'Simulated mic start failure',
      errorCode: 'MIC_START_FAILED',
      countsAsFailure: true,
    );
  }

  @override
  Future<ThoughtRecording> stopAndFinalize() async => const ThoughtRecording(
        path: '/tmp/test_audio.m4a',
        duration: Duration(seconds: 5),
        sizeBytes: 512,
      );
}

FocusSessionInfo _sampleSession({
  String sessionId = 'sess_contract_1',
  DateTime? scheduledStart,
  int workSeconds = 1500,
}) {
  final start = scheduledStart ?? DateTime.utc(2026, 11, 26, 9, 0);
  return FocusSessionInfo(
    sessionId: sessionId,
    taskId: 'task_contract_1',
    taskRevision: 2,
    taskTitle: 'Viết báo cáo tuần',
    stepTitle: 'Mở tài liệu và ghi dàn ý',
    scheduledStart: start,
    scheduledEnd: start.add(Duration(seconds: workSeconds)),
    workSeconds: workSeconds,
    restSeconds: 300,
    microTaskText: 'Mở file nháp trong 2 phút',
    segments: [
      SessionSegmentModel(
        kind: 'work',
        durationSeconds: workSeconds,
        orderIndex: 0,
      ),
    ],
    scheduleRevision: 4,
  );
}

DioException _httpError(
  RequestOptions request, {
  required int statusCode,
  required String code,
  String message = 'Contract error',
  String? nextAction,
  Map<String, int>? currentRevisions,
  bool retryable = false,
}) {
  return DioException(
    requestOptions: request,
    response: Response(
      requestOptions: request,
      statusCode: statusCode,
      data: {
        'error': {
          'code': code,
          'message': message,
          if (nextAction != null) 'next_action': nextAction,
          if (currentRevisions != null) 'current_revisions': currentRevisions,
          'retryable': retryable,
        },
      },
    ),
    type: DioExceptionType.badResponse,
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({'focus_device_id': 'test-device'});
    ApiSession.current = ApiSession('user_contract', token: 'token_contract');
  });

  tearDown(() {
    ApiSession.current = null;
  });

  group('Group 1 — UF4 Telemetry, Zero-AI & Fallback attempt_count=4', () {
    test('sanitizeErrorCode enforces ^[A-Z0-9_]{2,64}\$ and report sends non-PII payload',
        () async {
      expect(
        DeviceTelemetryService.sanitizeErrorCode('mic permission-denied!!'),
        'MIC_PERMISSION_DENIED',
      );
      expect(DeviceTelemetryService.sanitizeErrorCode('a'), 'UNKNOWN_ERROR');
      expect(
        DeviceTelemetryService.sanitizeErrorCode('X' * 90).length,
        64,
      );

      final requests = <RequestOptions>[];
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {'recorded': true}
            },
          ));
        }));

      final telemetry = DeviceTelemetryService(
        ApiClient(dio: dio, bindSession: false),
      );
      await telemetry.report(
        deviceId: 'dev_1',
        eventType: DeviceTelemetryService.uf4AudioCaptureFailed,
        errorCode: 'mic write failed',
        attemptCount: 9,
        sourceRunId: 'run_99',
        occurredAt: DateTime.utc(2026, 11, 26, 10, 0),
        platformOs: 'android',
      );

      final req = requests.single;
      expect(req.path, '/devices/dev_1/telemetry');
      expect(req.headers['Idempotency-Key'], isNotEmpty);
      final body = req.data as Map<String, dynamic>;
      expect(body['event_type'], 'uf4_audio_capture_failed');
      expect(body['error_code'], 'MIC_WRITE_FAILED');
      expect(body['attempt_count'], 5);
      expect(body['source_run_id'], 'run_99');
      expect(body['occurred_at'], endsWith('Z'));
      expect(body.containsKey('text_content'), isFalse);
      expect(body.containsKey('local_audio_path'), isFalse);
    });

    test('3 audio failures emit uf4 telemetry and post-fallback saveTextThought sets attemptCount=4',
        () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      addTearDown(() => debugDefaultTargetPlatformOverride = null);

      final now = DateTime.utc(2026, 11, 26, 9, 0);
      final clock = _TestClock(now);
      final requests = <RequestOptions>[];
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          final data = switch (request.path) {
            '/sessions/sess_contract_1/start' => {
                'run_id': 'run_uf4_1',
                'device_id': 'test-device',
                'run_revision': 1,
                'planned_ends_at':
                    now.add(const Duration(minutes: 10)).toIso8601String(),
              },
            '/devices/test-device/telemetry' => {'recorded': true},
            _ => <String, dynamic>{},
          };
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': data},
          ));
        }));
      final client = ApiClient(dio: dio, bindSession: false);
      final outboxRepo = InMemoryOutboxRepository();
      final thoughtRepo = InMemoryThoughtRepository();
      final recorder = _FailingAudioRecorder();

      final provider = FocusProvider(
        clock: clock,
        api: client,
        recorder: recorder,
        repository: InMemoryRunRepository(),
        outboxRepository: outboxRepo,
        thoughtRepository: thoughtRepo,
        telemetryService: DeviceTelemetryService(client),
      );
      addTearDown(provider.dispose);

      await provider.startSession(_sampleSession(scheduledStart: now));
      await provider.startRun();
      expect(provider.isActiveRun, isTrue);
      expect(provider.remainingSeconds, inInclusiveRange(595, 600));

      await provider.beginRecording();
      expect(provider.stage, FocusStage.saveFailed);
      await provider.retryRecording();
      expect(provider.stage, FocusStage.saveFailed);
      await provider.retryRecording();
      expect(provider.stage, FocusStage.textFallback);

      await Future<void>.delayed(const Duration(milliseconds: 25));

      final telemetryReq = requests.singleWhere(
        (r) => r.path == '/devices/test-device/telemetry',
      );
      final telemetryBody = telemetryReq.data as Map<String, dynamic>;
      expect(telemetryBody['event_type'], 'uf4_audio_capture_failed');
      expect(telemetryBody['error_code'], 'MIC_START_FAILED');
      expect(telemetryBody['attempt_count'], 3);
      expect(telemetryBody['source_run_id'], 'run_uf4_1');

      await provider.saveTextThought('Ghi chú chữ sau khi mic lỗi');
      expect(provider.thoughts.first.attemptCount, 4);
      expect(provider.thoughts.first.inputType, 'text');
    });
  });

  group('Group 2 — UF4 Thought created_at, 409 Handling & Outbox Delete Rebase',
      () {
    testWidgets('FocusThoughtTile renders local createdAt timestamp subtitle',
        (tester) async {
      final createdAt = DateTime(2026, 11, 26, 10, 15);
      final thought = ThoughtRecord(
        clientThoughtId: 't_tile_1',
        createdAt: createdAt,
        undoDeadlineAt: createdAt.add(const Duration(seconds: 5)),
        inputType: 'text',
        textContent: 'Mua pin cho điều khiển',
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: MelbelTheme.currentTheme,
          home: Scaffold(
            body: FocusThoughtTile(
              thought: thought,
              isPlaying: false,
              onPlay: null,
              onDelete: () {},
            ),
          ),
        ),
      );

      expect(find.text('Mua pin cho điều khiển'), findsOneWidget);
      expect(find.text(formatFocusThoughtTime(createdAt)), findsOneWidget);
    });

    test('convertThoughtToDraft blocks on awaitingCheckIn and handles 409 THOUGHT_ALREADY_LINKED / REVISION_CONFLICT',
        () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      addTearDown(() => debugDefaultTargetPlatformOverride = null);

      final now = DateTime.utc(2026, 11, 26, 9, 0);
      var returnLinkedConflict = false;
      var returnRevisionConflict = false;
      var linkedGetCount = 0;

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          if (request.path == '/thoughts' && request.method == 'GET') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'items': [
                    {
                      'thought_id': 'srv_thought_1',
                      'client_thought_id': 'ct_1',
                      'input_type': 'text',
                      'text_content': 'Ý tưởng mới',
                      'created_at': now.toIso8601String(),
                      'capture_state': 'saved',
                      'thought_revision': 1,
                    },
                  ],
                  'next_cursor': null,
                },
              },
            ));
            return;
          }
          if (request.path == '/thoughts/srv_thought_1' &&
              request.method == 'GET') {
            final isFollowUpLinked =
                returnLinkedConflict && (++linkedGetCount >= 2);
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'thought_id': 'srv_thought_1',
                  'thought_revision': isFollowUpLinked ? 3 : 2,
                  'linked_draft_id':
                      isFollowUpLinked ? 'draft_linked_1' : null,
                },
              },
            ));
            return;
          }
          if (request.path == '/thoughts/srv_thought_1/draft' &&
              request.method == 'POST') {
            if (returnLinkedConflict) {
              handler.reject(_httpError(
                request,
                statusCode: 409,
                code: 'THOUGHT_ALREADY_LINKED',
                nextAction: 'open_linked_draft',
                currentRevisions: const {'thought_revision': 3},
              ));
              return;
            }
            if (returnRevisionConflict) {
              handler.reject(_httpError(
                request,
                statusCode: 409,
                code: 'REVISION_CONFLICT',
                currentRevisions: const {'thought_revision': 5},
              ));
              return;
            }
          }
          if (request.path == '/drafts/draft_linked_1' &&
              request.method == 'GET') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'draft_id': 'draft_linked_1',
                  'client_draft_id': 'c_1',
                  'stage': 'collecting',
                  'summary': {'title': 'Việc đã liên kết'},
                  'summary_revision': 1,
                  'planning_input_revision': 1,
                  'input_source': 'text',
                },
              },
            ));
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': <String, dynamic>{}},
          ));
        }));

      final client = ApiClient(dio: dio, bindSession: false);
      final runRepo = InMemoryRunRepository();
      final thought = ThoughtRecord(
        clientThoughtId: 'ct_1',
        createdAt: now,
        undoDeadlineAt: now.add(const Duration(seconds: 5)),
        inputType: 'text',
        textContent: 'Ý tưởng mới',
        serverThoughtId: 'srv_thought_1',
        thoughtRevision: 1,
        isPendingSync: false,
      );
      await runRepo.saveActiveRun({
        'session': _sampleSession(scheduledStart: now).toJson(),
        'server_run': {
          'run_id': 'run_wait_checkin',
          'device_id': 'test-device',
          'run_revision': 1,
          'check_in': {
            'checkin_id': 'ci_open',
            'response_revision': 0,
            'response_state': 'open',
          },
        },
        'run_ends_at': now.toIso8601String(),
      });

      final blockedProvider = FocusProvider(
        clock: _TestClock(now),
        api: client,
        repository: runRepo,
        thoughtRepository: InMemoryThoughtRepository(),
      );
      addTearDown(blockedProvider.dispose);
      await blockedProvider.restore();

      await expectLater(
        blockedProvider.convertThoughtToDraft(thought, 'Ý tưởng mới'),
        throwsA(
          isA<ApiException>().having(
            (e) => e.isSessionStillRunning,
            'isSessionStillRunning',
            isTrue,
          ),
        ),
      );

      final idleProvider = FocusProvider(
        clock: _TestClock(now),
        api: client,
        repository: InMemoryRunRepository(),
        thoughtRepository: InMemoryThoughtRepository(),
      );
      addTearDown(idleProvider.dispose);
      await idleProvider.loadThoughts();

      returnLinkedConflict = true;
      final linkedDraft =
          await idleProvider.convertThoughtToDraft(thought, 'Ý tưởng mới');
      expect(linkedDraft.draftId, 'draft_linked_1');
      expect(idleProvider.thoughts.first.thoughtRevision, 3);
      expect(idleProvider.thoughts.first.linkedDraftId, 'draft_linked_1');

      returnLinkedConflict = false;
      returnRevisionConflict = true;
      await expectLater(
        idleProvider.convertThoughtToDraft(thought, 'Ý tưởng mới'),
        throwsA(
          isA<ApiException>().having(
            (e) => e.isRevisionConflict,
            'isRevisionConflict',
            isTrue,
          ),
        ),
      );
      expect(idleProvider.thoughts.first.thoughtRevision, 5);
    });

    test('OutboxReplayWorker rebases DELETE /thoughts/{id} on 409 REVISION_CONFLICT and keeps THOUGHT_ALREADY_LINKED as conflict',
        () async {
      final requests = <RequestOptions>[];
      var conflictMode = 'REVISION_CONFLICT';
      var deleteCalls = 0;

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          deleteCalls++;
          if (deleteCalls == 1 && conflictMode == 'REVISION_CONFLICT') {
            handler.reject(_httpError(
              request,
              statusCode: 409,
              code: 'REVISION_CONFLICT',
              currentRevisions: const {'thought_revision': 3},
            ));
            return;
          }
          if (conflictMode == 'THOUGHT_ALREADY_LINKED') {
            handler.reject(_httpError(
              request,
              statusCode: 409,
              code: 'THOUGHT_ALREADY_LINKED',
              nextAction: 'open_linked_draft',
              currentRevisions: const {'thought_revision': 4},
            ));
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': {'deleted': true}},
          ));
        }));

      final client = ApiClient(dio: dio, bindSession: false);
      final outboxRepo = InMemoryOutboxRepository();
      final thoughtRepo = InMemoryThoughtRepository();
      final occurred = DateTime.utc(2026, 11, 26, 9, 0);
      await thoughtRepo.markTombstone('srv_t_1', ownerId: 'user_contract');
      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'del_evt_1',
          eventType: OutboxEventType.thoughtDelete,
          targetResourceId: 'srv_t_1',
          endpoint: '/thoughts/srv_t_1',
          httpMethod: 'DELETE',
          payload: const {'expected_thought_revision': 1},
          occurredAt: occurred,
          holdUntil: occurred,
        ),
      );

      final worker = OutboxReplayWorker(
        repository: outboxRepo,
        apiClient: client,
        thoughtRepository: thoughtRepo,
      );
      final report = await worker.triggerReplay(
        asOf: DateTime.utc(2026, 11, 26, 9, 1),
      );
      expect(report.syncedIds, ['del_evt_1']);
      expect(requests.length, 2);
      expect(requests[1].data['expected_thought_revision'], 3);
      expect(requests[1].headers['Idempotency-Key'], 'del_evt_1_rev_3');
      expect(
        await thoughtRepo.getAllThoughts('user_contract'),
        isEmpty,
      );

      // Now test THOUGHT_ALREADY_LINKED does NOT rebase or purge tombstone.
      conflictMode = 'THOUGHT_ALREADY_LINKED';
      await thoughtRepo.markTombstone('srv_t_2', ownerId: 'user_contract');
      await outboxRepo.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: 'del_evt_2',
          eventType: OutboxEventType.thoughtDelete,
          targetResourceId: 'srv_t_2',
          endpoint: '/thoughts/srv_t_2',
          httpMethod: 'DELETE',
          payload: const {'expected_thought_revision': 1},
          occurredAt: DateTime.utc(2026, 11, 26, 9, 2),
          holdUntil: DateTime.utc(2026, 11, 26, 9, 2),
        ),
      );
      final report2 = await worker.triggerReplay(
        asOf: DateTime.utc(2026, 11, 26, 9, 3),
      );
      expect(report2.conflictIds, ['del_evt_2']);
      final tombstones = await thoughtRepo.getAllThoughts('user_contract');
      expect(tombstones.any((t) => t.id == 'srv_t_2'), isTrue);
    });
  });

  group('Group 3 — UF2 source_revisions, cancelled_session_ids & ACTIVE_RUN_EXISTS',
      () {
    test('TaskModel.fromJson parses revision & source_revisions and ScheduleService enriches source_revisions',
        () async {
      final task = TaskModel.fromJson({
        'task_id': 'task_1',
        'title': 'Việc mẫu',
        'revision': 4,
        'source_revisions': {
          'task': 4,
          'schedule': 7,
          'availability': 2,
          'settings': 3,
          'calendar': 1,
        },
      });
      expect(task.revision, 4);
      expect(task.sourceRevisions, {
        'task': 4,
        'schedule': 7,
        'availability': 2,
        'settings': 3,
        'calendar': 1,
      });

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          final data = switch (request.path) {
            '/schedule' => {
                'items': [
                  {
                    'session_id': 's_1',
                    'step_id': 'st_1',
                    'scheduled_start': '2026-11-26T09:00:00Z',
                    'scheduled_end': '2026-11-26T09:25:00Z',
                    'status': 'scheduled',
                  },
                ],
                'next_cursor': null,
              },
            '/tasks' => {
                'items': [
                  {
                    'task_id': 'task_1',
                    'title': 'Việc mẫu',
                    'revision': 4,
                    'source_revisions': {'task': 4, 'schedule': 7},
                    'steps': [
                      {
                        'step_id': 'st_1',
                        'title': 'Bước 1',
                        'expected_output': 'Xong',
                      },
                    ],
                    'sessions': [
                      {
                        'session_id': 's_1',
                        'step_id': 'st_1',
                        'scheduled_start': '2026-11-26T09:00:00Z',
                        'status': 'scheduled',
                      },
                    ],
                  },
                ],
                'next_cursor': null,
              },
            _ => <String, dynamic>{},
          };
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': data},
          ));
        }));

      final sessions = await ScheduleService(
        ApiClient(dio: dio, bindSession: false),
      ).loadSessions(
        DateTime.utc(2026, 11, 26),
        DateTime.utc(2026, 11, 27),
        includeTitles: true,
      );
      expect(sessions.single['source_revisions'], {'task': 4, 'schedule': 7});
    });

    testWidgets(
        'confirmAndDeleteTask cancels alarms for cancelled_session_ids on 200 and keeps sheet open on 409 ACTIVE_RUN_EXISTS',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      try {
        final cancelledSessions = <String>[];
      var syncCalls = 0;
      var returnActiveRunConflict = false;

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          if (request.path == '/tasks/task_del_1' &&
              request.method == 'DELETE') {
            if (returnActiveRunConflict) {
              handler.reject(_httpError(
                request,
                statusCode: 409,
                code: 'ACTIVE_RUN_EXISTS',
                nextAction: 'finish_active_run',
              ));
              return;
            }
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'task_id': 'task_del_1',
                  'cancelled_session_ids': ['sess_1', 'sess_2'],
                },
              },
            ));
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': {'items': <dynamic>[], 'next_cursor': null}},
          ));
        }));

      final client = ApiClient(dio: dio, bindSession: false);
      final reminderSync = PreSessionReminderSync(
        isAndroid: () => true,
        cancelForSession: (id) async => cancelledSessions.add(id),
        scheduleExplicit: (plan) async {
          syncCalls++;
          return plan.length;
        },
        schedule: ({required sessions, offsetsSeconds, now}) async {
          syncCalls++;
          return 0;
        },
      );

      final focusProvider = FocusProvider(
        clock: _TestClock(DateTime.utc(2026, 11, 26, 9, 0)),
        api: client,
        repository: InMemoryRunRepository(),
      );
      addTearDown(focusProvider.dispose);
      await focusProvider.startSession(_sampleSession());

      bool? result;
      await tester.pumpWidget(
        ChangeNotifierProvider<FocusProvider>.value(
          value: focusProvider,
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: Scaffold(
              body: Builder(
                builder: (context) => ElevatedButton(
                  onPressed: () async {
                    result = await confirmAndDeleteTask(
                      context,
                      taskId: 'task_del_1',
                      taskTitle: 'Dọn bàn làm việc',
                      taskRevision: 2,
                      api: client,
                      reminderSync: reminderSync,
                    );
                  },
                  child: const Text('Open'),
                ),
              ),
            ),
          ),
        ),
      );

      // Conflict case first: sheet stays open and offers "Mở phiên đang chạy".
      returnActiveRunConflict = true;
      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsNothing);
      await tester.tap(find.text('Xóa công việc'));
      await tester.pumpAndSettle();
      expect(find.text('Mở phiên đang chạy'), findsOneWidget);
      expect(result, isNull);

      // Now allow delete and confirm cancelled_session_ids alarm cancellation.
      returnActiveRunConflict = false;
      await tester.tap(find.text('Xóa công việc'));
      await tester.pumpAndSettle();

      expect(result, isTrue);
      expect(cancelledSessions, ['sess_1', 'sess_2']);
      expect(syncCalls, greaterThanOrEqualTo(1));
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    });
  });

  group('Group 4 — UF3/UF5 SessionSnapshot, Reminders, skip_scope & Streak Sync',
      () {
    testWidgets(
        'FocusSessionInfo.fromSessionSnapshot hydrates sessionGoal and FocusSessionSummaryCard displays it',
        (tester) async {
      final info = FocusSessionInfo.fromSessionSnapshot({
        'session_id': 's_snap_1',
        'title': 'Soạn đề cương',
        'session_goal': 'Hoàn thành 3 gạch đầu dòng mở bài',
        'micro_task_seconds': 90,
        'scheduled_start': '2026-11-26T09:00:00Z',
        'scheduled_end': '2026-11-26T09:25:00Z',
        'work_seconds': 1500,
        'rest_seconds': 300,
      }, taskTitle: 'Báo cáo tuần');

      expect(info.sessionGoal, 'Hoàn thành 3 gạch đầu dòng mở bài');
      expect(info.microTaskSeconds, 90);

      await tester.pumpWidget(
        MaterialApp(
          theme: MelbelTheme.currentTheme,
          home: Scaffold(body: FocusSessionSummaryCard(session: info)),
        ),
      );
      expect(
        find.text('Hoàn thành 3 gạch đầu dòng mở bài'),
        findsOneWidget,
      );
    });

    test('PreSessionReminderSync.reconcileServerReminders deduplicates by dedupe_key & highest schedule_revision',
        () {
      final now = DateTime.utc(2026, 11, 26, 9, 0);
      final sessions = [
        ReminderSession(
          sessionId: 'sess_r_1',
          scheduledStart: now.add(const Duration(minutes: 30)),
          stepTitle: 'Làm bài tập',
        ),
      ];
      final plan = PreSessionReminderSync.reconcileServerReminders(
        sessions: sessions,
        now: now,
        serverReminders: [
          {
            'dedupe_key': 'sess_r_1:900',
            'session_id': 'sess_r_1',
            'offset_seconds': 900,
            'schedule_revision': 1,
            'delivery_state': 'scheduled',
            'fire_at': now.add(const Duration(minutes: 14)).toIso8601String(),
          },
          {
            'dedupe_key': 'sess_r_1:900',
            'session_id': 'sess_r_1',
            'offset_seconds': 900,
            'schedule_revision': 2,
            'delivery_state': 'scheduled',
            'fire_at': now.add(const Duration(minutes: 15)).toIso8601String(),
          },
          {
            'dedupe_key': 'sess_r_1:600',
            'session_id': 'sess_r_1',
            'offset_seconds': 600,
            'schedule_revision': 2,
            'delivery_state': 'cancelled',
            'fire_at': now.add(const Duration(minutes: 20)).toIso8601String(),
          },
          {
            'dedupe_key': 'sess_r_1:300',
            'session_id': 'sess_r_1',
            'offset_seconds': 300,
            'schedule_revision': 2,
            'delivery_state': 'delivered',
            'fire_at': now.add(const Duration(minutes: 25)).toIso8601String(),
          },
          {
            'dedupe_key': 'sess_r_1:1200',
            'session_id': 'sess_r_1',
            'offset_seconds': 1200,
            'schedule_revision': 2,
            'delivery_state': 'scheduled',
            'fire_at':
                now.subtract(const Duration(minutes: 1)).toIso8601String(),
          },
        ],
      );

      expect(plan.length, 1);
      expect(plan.single.offsetSeconds, 900);
      expect(plan.single.fireAt.toUtc(), now.add(const Duration(minutes: 15)));
    });

    test('answerCheckIn skip_scope contract, submitActualCorrection streak invalidation & StreakProvider overwrite',
        () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      addTearDown(() => debugDefaultTargetPlatformOverride = null);

      final now = DateTime.utc(2026, 11, 26, 9, 0);
      final requests = <RequestOptions>[];
      var streakSyncData = <String, dynamic>{
        'current_streak_days': 5,
        'effective_display_streak': 5,
        'longest_streak_days': 10,
        'streak_freeze_tokens': 0,
        'repairs_used_this_month': 0,
        'repair_pending': false,
        'streak_frozen': false,
        'last_streak_date': '2026-11-26',
        'today_completed': true,
        'today_focus_minutes': 25,
        'day_records': [
          {'date': '2026-11-26', 'kind': 'done', 'minutes': 25},
        ],
      };
      var correctionCalls = 0;
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          if (request.path == '/runs/run_s_1/actual-corrections' &&
              ++correctionCalls == 1) {
            handler.reject(_httpError(
              request,
              statusCode: 409,
              code: 'REVISION_CONFLICT',
              currentRevisions: const {'run_revision': 3},
            ));
            return;
          }
          final data = switch (request.path) {
            '/check-ins/ci_1/response' => {
                'checkin_id': 'ci_1',
                'response_revision': 1,
                'response_state': 'answered',
              },
            '/runs/run_s_1/actual-corrections' => {
                'run_revision': 4,
                'actual_work_seconds': 1200,
              },
            '/streak' => streakSyncData,
            _ => <String, dynamic>{},
          };
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': data},
          ));
        }));

      final client = ApiClient(dio: dio, bindSession: false);
      final runRepo = InMemoryRunRepository();
      await runRepo.saveActiveRun({
        'stage': 'ended',
        'session': _sampleSession(scheduledStart: now).toJson(),
        'server_run': {
          'run_id': 'run_s_1',
          'device_id': 'test-device',
          'run_revision': 2,
          'check_in': {
            'checkin_id': 'ci_1',
            'response_revision': 0,
            'response_state': 'open',
          },
        },
        'run_ends_at': now.toIso8601String(),
      });

      var streakInvalidations = 0;
      final provider = FocusProvider(
        clock: _TestClock(now),
        api: client,
        repository: runRepo,
        onStreakInvalidated: () => streakInvalidations++,
      );
      addTearDown(provider.dispose);
      await provider.restore();

      // Missing skipScope when skipped must fail locally before HTTP.
      await provider.answerCheckIn('skipped');
      expect(provider.runError, isNotNull);
      expect(
        requests.where((r) => r.path == '/check-ins/ci_1/response'),
        isEmpty,
      );

      // Completed must omit skip_scope and invoke onStreakInvalidated.
      await provider.answerCheckIn('completed');
      final completeReq = requests.last;
      expect((completeReq.data as Map).containsKey('skip_scope'), isFalse);
      expect(streakInvalidations, 1);

      // Skipped with skipScope sends exact skip_scope and invokes onStreakInvalidated.
      final skipRepo = InMemoryRunRepository();
      await skipRepo.saveActiveRun({
        'stage': 'ended',
        'session': _sampleSession(scheduledStart: now).toJson(),
        'server_run': {
          'run_id': 'run_s_1',
          'device_id': 'test-device',
          'run_revision': 2,
          'check_in': {
            'checkin_id': 'ci_1',
            'response_revision': 0,
            'response_state': 'open',
          },
        },
        'run_ends_at': now.toIso8601String(),
      });
      final skipProvider = FocusProvider(
        clock: _TestClock(now),
        api: client,
        repository: skipRepo,
        onStreakInvalidated: () => streakInvalidations++,
      );
      addTearDown(skipProvider.dispose);
      await skipProvider.restore();
      await skipProvider.answerCheckIn('skipped', skipScope: 'this_session');
      expect((requests.last.data as Map)['skip_scope'], 'this_session');
      expect(streakInvalidations, 2);

      // Actual correction also recovers from 409 REVISION_CONFLICT and invalidates streak.
      final ok = await provider.submitActualCorrection(
        correctedWorkMinutes: 20,
        reason: 'Sửa lại thời gian thực tế',
      );
      expect(ok, isTrue);
      expect(correctionCalls, 2);
      expect((requests.last.data as Map)['expected_run_revision'], 3);
      expect(streakInvalidations, 3);

      // StreakProvider.syncFromBackend overwrites longestStreak cleanly when backend reverses stats.
      final streakProvider = StreakProvider(
        ownerId: 'user_contract',
        api: client,
        now: () => DateTime(2026, 11, 26, 9, 0),
      );
      await streakProvider.syncFromBackend();
      expect(streakProvider.longestStreak, 10);
      expect(streakProvider.todayDone, isTrue);

      streakSyncData = {
        'current_streak_days': 2,
        'effective_display_streak': 2,
        'longest_streak_days': 2,
        'streak_freeze_tokens': 0,
        'repairs_used_this_month': 0,
        'repair_pending': false,
        'streak_frozen': false,
        'last_streak_date': null,
        'today_completed': false,
        'today_focus_minutes': 0,
        'day_records': const [],
      };
      await streakProvider.syncFromBackend();
      expect(streakProvider.longestStreak, 2);
      expect(streakProvider.currentStreak, 2);
      expect(streakProvider.todayDone, isFalse);
    });
  });

  group('Group 5 — UF6 Auto-Rebuild & createProposalForTask', () {
    test('AdaptationApi.createProposalForTask and hasExpiredProposedSlots obey backend contract',
        () async {
      final requests = <RequestOptions>[];
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {
                'proposal_id': 'prop_task_1',
                'proposal_revision': 1,
                'status': 'pending',
              },
            },
          ));
        }));

      final api = AdaptationApi(ApiClient(dio: dio, bindSession: false));
      await api.createProposalForTask(
        taskId: 'task_9',
        reason: 'missed_start',
        sourceRevisions: const {'task': 2, 'schedule': 5},
      );
      expect(requests.single.data, {
        'trigger_reason': 'missed_start',
        'target_task_id': 'task_9',
        'allowed_task_ids': ['task_9'],
        'expected_base_schedule_revision': 5,
        'expected_source_revisions': {'task': 2, 'schedule': 5},
      });

      final now = DateTime.utc(2026, 11, 26, 10, 0);
      expect(
        AdaptationApi.hasExpiredProposedSlots({
          'proposed_changes': {
            'sessions_to_move': [
              {
                'after': {'start': '2026-11-26T09:50:00Z'}
              },
            ],
          },
        }, now: now),
        isTrue,
      );
      expect(
        AdaptationApi.hasExpiredProposedSlots({
          'proposed_changes': {
            'sessions_to_move': [
              {
                'after': {'start': '2026-11-26T10:30:00Z'}
              },
            ],
          },
        }, now: now),
        isFalse,
      );
    });

    testWidgets(
        'ScheduleComparisonScreen auto-rebuilds on 409 SOURCE_CHANGED and keeps approve CTA enabled',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      try {
      final requests = <RequestOptions>[];
      var approveAttempts = 0;

      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          if (request.path == '/proposals/p_uf6_1' && request.method == 'GET') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'proposal_id': 'p_uf6_1',
                  'proposal_revision': 1,
                  'base_schedule_revision': 2,
                  'source_revisions': {'schedule': 2, 'task': 1},
                  'status': 'pending',
                  'stale': false,
                  'explanation': 'Dời 1 phiên sang mai',
                  'feasibility': {'confirmable': true},
                  'proposed_changes': {
                    'sessions_to_move': const [],
                    'sessions_to_add': const [],
                    'sessions_to_cancel': const [],
                  },
                },
              },
            ));
            return;
          }
          if (request.path == '/proposals/p_uf6_1/approve' &&
              ++approveAttempts == 1) {
            handler.reject(_httpError(
              request,
              statusCode: 409,
              code: 'SOURCE_CHANGED',
              nextAction: 'rebuild_and_review',
            ));
            return;
          }
          if (request.path == '/proposals/p_uf6_1/rebuild') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'proposal_id': 'p_uf6_1',
                  'proposal_revision': 2,
                  'base_schedule_revision': 3,
                  'source_revisions': {'schedule': 3, 'task': 1},
                  'status': 'pending',
                  'stale': false,
                  'explanation': 'Phương án vừa dựng lại',
                  'feasibility': {'confirmable': true},
                  'proposed_changes': {
                    'sessions_to_move': const [],
                    'sessions_to_add': const [],
                    'sessions_to_cancel': const [],
                  },
                },
              },
            ));
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {
                'proposal_id': 'p_uf6_1',
                'proposal_revision': 3,
                'status': 'approved',
              },
            },
          ));
        }));

      final api = AdaptationApi(ApiClient(dio: dio, bindSession: false));
      await tester.pumpWidget(
        MaterialApp(
          theme: MelbelTheme.currentTheme,
          home: ScheduleComparisonScreen(
            reason: 'Đổi giờ',
            proposalId: 'p_uf6_1',
            api: api,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Áp dụng lịch mới'));
      await tester.pumpAndSettle();

      expect(
        requests.any((r) =>
            r.path == '/proposals/p_uf6_1/rebuild' &&
            (r.headers['Idempotency-Key'] as String?)?.isNotEmpty == true),
        isTrue,
      );
      expect(find.text('Phương án vừa dựng lại'), findsOneWidget);
      expect(
        find.text('Nguồn lịch đã thay đổi. Phương án mới đã sẵn sàng để duyệt.'),
        findsOneWidget,
      );

      // Rebuilt proposal remains confirmable on tap.
      await tester.tap(find.text('Áp dụng lịch mới'));
      await tester.pumpAndSettle();
      expect(approveAttempts, 2);
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    });
  });

  group('Group 6 — UF7 pending_change_request_id, 3 Styles & Strict reminder_offsets',
      () {
    testWidgets(
        'SettingsScreen shows persisted pending change controls on fresh open, 3 reminder_style chips, and validates reminder_offsets strictly',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      try {
      final requests = <RequestOptions>[];
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          requests.add(request);
          if (request.path == '/preferences' && request.method == 'GET') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'revision': 5,
                  'effective_revision': 5,
                  'work_interval_seconds': 1500,
                  'rest_interval_seconds': 300,
                  'reminder_offsets': [600, 300],
                  'buffer_multiplier': 1.2,
                  'reminder_style': 'gentle',
                  'pending_proposal_id': 'prop_settings_1',
                  'pending_change_request_id': 'chg_settings_1',
                  'pending_change_request_revision': 2,
                },
              },
            ));
            return;
          }
          if (request.path == '/suggestions/current') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {
                  'suggestion_id': 'sug_rem_1',
                  'suggestion_revision': 1,
                  'kind': 'reminder_offsets',
                  'current_value': [600, 300],
                  'suggested_value': [900, 600],
                  'reason': 'Nhắc sớm hơn 15 phút.',
                  'status': 'pending',
                },
              },
            ));
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': <String, dynamic>{}},
          ));
        }));

      final client = ApiClient(dio: dio, bindSession: false);
      await tester.pumpWidget(
        ChangeNotifierProvider<ThemeProvider>(
          create: (_) => ThemeProvider(),
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: SettingsScreen(
              api: client,
              adaptation: AdaptationApi(client),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Fresh open from GET /preferences displays both review & cancel buttons.
      expect(find.text('Xem thay đổi chờ duyệt'), findsOneWidget);
      expect(find.text('Hủy yêu cầu đổi cài đặt'), findsOneWidget);

      // Expand reminders & buffer section to verify all 3 style chips and PUT upbeat.
      await tester.scrollUntilVisible(
        find.text('Nhắc nhở và thời gian dự phòng'),
        200,
      );
      await tester.tap(find.text('Nhắc nhở và thời gian dự phòng'));
      await tester.pumpAndSettle();

      expect(find.text('Nhẹ nhàng'), findsOneWidget);
      expect(find.text('Tươi vui'), findsOneWidget);
      expect(find.text('Khích lệ'), findsOneWidget);

      await tester.tap(find.text('Tươi vui'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Lưu cài đặt'), 200);
      await tester.tap(find.text('Lưu cài đặt'));
      await tester.pumpAndSettle();

      final saveReq = requests.firstWhere(
        (r) => r.path == '/settings/change-requests' && r.method == 'POST',
      );
      expect(
        ((saveReq.data as Map)['changes'] as Map)['preferences']['reminder_style'],
        'upbeat',
      );
      expect((saveReq.headers['Idempotency-Key'] as String?)?.isNotEmpty, isTrue);

      await tester.tap(find.text('Về cài đặt'));
      await tester.pumpAndSettle();

      // Validate strict descending 1-3 rule in reminder_offsets editor sheet.
      await tester.scrollUntilVisible(
        find.text('Gợi ý cho phiên sau'),
        -300,
      );
      await tester.ensureVisible(find.text('Tùy chỉnh'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Tùy chỉnh'));
      await tester.pumpAndSettle();

      final sheetApply = find.widgetWithText(PrimaryCta, 'Áp dụng');
      // Ascending / duplicate / 4 items disable "Áp dụng".
      for (final invalid in ['5, 10', '10, 10', '20, 15, 10, 5']) {
        await tester.enterText(find.byType(TextField), invalid);
        await tester.pump();
        final applyBtn = tester.widget<PrimaryCta>(sheetApply);
        expect(applyBtn.onPressed, isNull, reason: 'Should reject "$invalid"');
      }

      // Descending 3 offsets enable "Áp dụng" and submit [900, 600, 300].
      await tester.enterText(find.byType(TextField), '15, 10, 5');
      await tester.pump();
      await tester.tap(sheetApply);
      await tester.pumpAndSettle();

      final editReq = requests.lastWhere(
        (r) => r.path == '/suggestions/sug_rem_1/response',
      );
      expect(editReq.data['action'], 'edit');
      expect(editReq.data['edited_value'], [900, 600, 300]);
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    });
  });

  group('Group 7 — UF1 Draft Guards & Google Calendar OCC Retry States', () {
    test('ApiException exposes isNoSpeechDetected and isConfirmedDraftTransition and isCalendarUnavailable checks reconnect',
        () {
      const noSpeech = ApiException('No speech', code: 'NO_SPEECH_DETECTED');
      expect(noSpeech.isNoSpeechDetected, isTrue);

      const confirmed = ApiException(
        'Already confirmed',
        code: 'INVALID_STATE_TRANSITION',
        statusCode: 409,
        nextAction: 'review_task',
      );
      expect(confirmed.isConfirmedDraftTransition, isTrue);

      const confirmedDelete = ApiException(
        'Already confirmed',
        code: 'INVALID_STATE_TRANSITION',
        statusCode: 409,
        nextAction: 'reload_and_review',
      );
      expect(confirmedDelete.isConfirmedDraftTransition, isTrue);

      const reconnect = ApiException(
        'Token expired',
        code: 'CALENDAR_AUTH_REQUIRED',
        statusCode: 401,
        nextAction: 'reconnect_google_calendar',
      );
      expect(reconnect.requiresCalendarReconnect, isTrue);
      expect(isCalendarUnavailable(reconnect), isTrue);
    });

    test('GoogleCalendarApi and CalendarApi retrySyncJobWithHandling map all backend OCC outcomes',
        () async {
      late String mode;
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          if (request.path == '/calendar/sync-jobs/job_1' &&
              request.method == 'GET') {
            handler.resolve(Response(
              requestOptions: request,
              data: {
                'data': {'job_id': 'job_1', 'job_revision': 7, 'status': 'failed'}
              },
            ));
            return;
          }
          if (request.path == '/calendar/sync-jobs/job_1/retry') {
            switch (mode) {
              case 'retried':
                handler.resolve(Response(
                  requestOptions: request,
                  data: {
                    'data': {
                      'job_id': 'job_1',
                      'job_revision': 2,
                      'status': 'queued',
                    }
                  },
                ));
              case 'revision_conflict':
                handler.reject(_httpError(
                  request,
                  statusCode: 409,
                  code: 'REVISION_CONFLICT',
                  currentRevisions: const {'job_revision': 7},
                ));
              case 'cancelled':
                handler.reject(_httpError(
                  request,
                  statusCode: 409,
                  code: 'JOB_CANCELLED',
                ));
              case 'invalid_state':
                handler.reject(_httpError(
                  request,
                  statusCode: 409,
                  code: 'INVALID_STATE_TRANSITION',
                ));
              case 'reconnect':
                handler.reject(_httpError(
                  request,
                  statusCode: 401,
                  code: 'CALENDAR_AUTH_REQUIRED',
                  nextAction: 'reconnect_google_calendar',
                ));
            }
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {'data': <String, dynamic>{}},
          ));
        }));

      final client = ApiClient(dio: dio, bindSession: false);
      final gcal = GoogleCalendarApi(client);
      final cal = CalendarApi(client: client);

      for (final api in [
        gcal.retrySyncJobWithHandling,
        cal.retrySyncJobWithHandling,
      ]) {
        mode = 'retried';
        final ok = await api('job_1', expectedJobRevision: 1);
        expect(ok.status, CalendarSyncRetryStatus.retried);
        expect(ok.syncJob?['status'], 'queued');

        mode = 'revision_conflict';
        final conflict = await api('job_1', expectedJobRevision: 1);
        expect(conflict.status, CalendarSyncRetryStatus.reloadRequired);
        expect(conflict.syncJob?['job_revision'], 7);

        mode = 'cancelled';
        final cancelled = await api('job_1', expectedJobRevision: 1);
        expect(cancelled.status, CalendarSyncRetryStatus.cancelled);

        mode = 'invalid_state';
        final invalidState = await api('job_1', expectedJobRevision: 1);
        expect(invalidState.status, CalendarSyncRetryStatus.invalidState);

        mode = 'reconnect';
        final reconnect = await api('job_1', expectedJobRevision: 1);
        expect(reconnect.status, CalendarSyncRetryStatus.reconnectRequired);
      }
    });
  });
}
