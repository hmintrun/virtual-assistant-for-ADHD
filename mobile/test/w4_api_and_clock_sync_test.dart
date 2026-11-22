import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/services/checkin_voice_service.dart';
import 'package:proplan_va/services/google_calendar_api.dart';
import 'package:proplan_va/services/planning_api.dart';
import 'package:proplan_va/services/reminder_api.dart';
import 'package:proplan_va/services/timer_service.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';

class _FixedClock implements Clock {
  _FixedClock(this.value);
  final DateTime value;

  @override
  DateTime now() => value;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiSession.current = ApiSession('user_w4_test', token: 'test_token');
  });

  tearDown(() {
    ApiSession.current = null;
  });

  testWidgets(
      'showMelbelTimePicker enforces 24-hour clock even when parent MediaQuery is 12-hour',
      (tester) async {
    TimeOfDay? picked;
    await tester.pumpWidget(
      MediaQuery(
        data: const MediaQueryData(alwaysUse24HourFormat: false),
        child: MaterialApp(
          theme: MelbelTheme.currentTheme,
          home: Builder(
            builder: (context) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () async {
                    picked = await showMelbelTimePicker(
                      context: context,
                      initialTime: const TimeOfDay(hour: 18, minute: 30),
                      initialEntryMode: TimePickerEntryMode.input,
                    );
                  },
                  child: const Text('Pick Time'),
                ),
              ),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('Pick Time'));
    await tester.pumpAndSettle();

    expect(find.text('18'), findsAtLeastNWidgets(1));
    expect(find.text('30'), findsAtLeastNWidgets(1));
    expect(find.text('AM'), findsNothing);
    expect(find.text('PM'), findsNothing);

    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(picked, const TimeOfDay(hour: 18, minute: 30));
  });

  test('ApiClient.deleteData and checkHealth with 404 fallback work properly',
      () async {
    var failHealth = false;
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        if (request.path == '/health') {
          if (failHealth) {
            handler.reject(DioException(
              requestOptions: request,
              type: DioExceptionType.badResponse,
              response: Response(requestOptions: request, statusCode: 404),
            ));
            return;
          }
          handler.resolve(Response(
            requestOptions: request,
            data: {'status': 'ok', 'database': 'ok'},
          ));
          return;
        }
        if (request.path == '/capabilities') {
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {'server_time': '2026-11-22T08:00:00Z'}
            },
          ));
          return;
        }
        if (request.method == 'DELETE' && request.path == '/drafts/draft_1') {
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {'draft_id': 'draft_1', 'deleted': true}
            },
          ));
          return;
        }
        handler.resolve(Response(requestOptions: request, data: {'data': {}}));
      }));

    final client = ApiClient(dio: dio, bindSession: false);

    final health = await client.checkHealth();
    expect(health['status'], 'ok');
    expect(health['database'], 'ok');

    failHealth = true;
    final fallback = await client.checkHealth();
    expect(fallback['server_time'], '2026-11-22T08:00:00Z');

    final deleted = await client.deleteData('/drafts/draft_1');
    expect(deleted['deleted'], isTrue);

    await PlanningApi(client: client).deleteDraft('draft_1');
    expect(requests.last.method, 'DELETE');
    expect(requests.last.path, '/drafts/draft_1');
  });

  test(
      'FocusProvider.submitActualCorrection sends POST /runs/{id}/actual-corrections and updates state',
      () async {
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        handler.resolve(Response(
          requestOptions: request,
          data: {
            'data': {
              'run_id': 'run-123',
              'run_revision': 2,
              'actual_work_seconds': 1800,
              'actual_source': 'self_report',
              'work_elapsed_seconds': 1500,
            }
          },
        ));
      }));

    final clock = _FixedClock(DateTime.utc(2026, 11, 22, 8, 30, 0));
    final runRepo = InMemoryRunRepository();
    await runRepo.saveActiveRun({
      'session': {
        'session_id': 'sess-1',
        'task_title': 'Báo cáo',
        'step_title': 'Bước 1',
        'scheduled_start': clock.now().toIso8601String(),
        'scheduled_end':
            clock.now().add(const Duration(minutes: 30)).toIso8601String(),
        'work_seconds': 1500,
        'rest_seconds': 300,
        'micro_task_text': 'Mở file',
        'segments': [
          {'order_index': 0, 'kind': 'work', 'duration_seconds': 1500}
        ],
      },
      'server_run': {
        'run_id': 'run-123',
        'run_revision': 1,
      },
      'stage': 'ended',
      'run_ends_at': clock.now().toIso8601String(),
      'actual_work_seconds': 1200,
    });

    final focus = FocusProvider(
      ownerId: 'user_w4_test',
      clock: clock,
      api: ApiClient(dio: dio, bindSession: false),
      repository: runRepo,
    );
    addTearDown(focus.dispose);
    await focus.restore();

    final ok = await focus.submitActualCorrection(
      correctedWorkMinutes: 30,
      reason: 'Quên bấm dừng sớm 5 phút',
    );
    expect(ok, isTrue);
    expect(focus.actualWorkSeconds, 1800);
    expect(requests.last.path, '/runs/run-123/actual-corrections');
    expect(requests.last.data, {
      'expected_run_revision': 1,
      'self_report_work_seconds': 1800,
      'reason': 'Quên bấm dừng sớm 5 phút',
      'occurred_at': '2026-11-22T08:30:00.000Z',
    });
  });

  test('CheckInVoiceService sends UF5 context fields and POST /voice/tts',
      () async {
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        if (request.path == '/voice/uf5-checkin-prompt') {
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {
                'prompt': {'prompt_text': 'Bạn thấy phiên vừa rồi ổn không?'},
                'fallback_to_device_tts': true,
              },
            },
          ));
          return;
        }
        handler.resolve(Response(
          requestOptions: request,
          data: {
            'data': {
              'audio_base64': base64Encode(const [1, 2, 3]),
              'mime_type': 'audio/mpeg',
              'provider': 'fake_tts',
              'voice': 'vi-VN-HoaiMyNeural',
              'cached': false,
              'duration_ms': 10,
            },
          },
        ));
      }));

    final service =
        CheckInVoiceService(apiClient: ApiClient(dio: dio, bindSession: false));
    addTearDown(service.dispose);

    await service.speakFinalPrompt(
      'run-99',
      isFinalSessionOfStep: true,
      expectedOutput: 'Xong 2 trang PDF',
    );
    expect(requests.last.path, '/voice/uf5-checkin-prompt');
    expect(requests.last.data, {
      'kind': 'final',
      'is_final_session_of_step': true,
      'expected_output': 'Xong 2 trang PDF',
      'include_audio': true,
    });

    await service.speakText('Hoàn thành tốt lắm.');
    expect(requests.last.path, '/voice/tts');
    expect(requests.last.data, {'text': 'Hoàn thành tốt lắm.'});
  });

  test('ReminderApi and GoogleCalendarApi sync routes send expected contracts',
      () async {
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        if (request.path == '/reminders') {
          handler.resolve(Response(
            requestOptions: request,
            data: {
              'data': {
                'items': [
                  {
                    'reminder_id': 'rem-1',
                    'session_id': 'sess-1',
                    'schedule_revision': 3,
                    'kind': 'before_session',
                    'fire_at': '2026-11-22T09:00:00Z',
                    'dedupe_key': 'k1',
                    'delivery_state': 'scheduled',
                  },
                ],
                'next_cursor': null,
              },
            },
          ));
          return;
        }
        handler.resolve(Response(
          requestOptions: request,
          data: {
            'data': {'status': 'ok', 'job_id': 'job-1', 'job_state': 'queued'}
          },
        ));
      }));

    final client = ApiClient(dio: dio, bindSession: false);
    final reminderApi = ReminderApi(client);
    final upcoming = await reminderApi.fetchUpcoming(
      deviceId: 'dev-1',
      scheduleRevision: 3,
    );
    expect(upcoming.length, 1);
    expect(requests.last.path, '/reminders');
    expect(requests.last.queryParameters['device_id'], 'dev-1');
    expect(requests.last.queryParameters['schedule_revision'], 3);

    await reminderApi.reportReceipt(
      reminderId: 'rem-1',
      deviceId: 'dev-1',
      scheduleRevision: 3,
      deliveryState: 'shown',
      occurredAt: DateTime.utc(2026, 11, 22, 8, 45, 0),
      userAction: 'opened',
    );
    expect(requests.last.path, '/reminders/rem-1/receipt');
    expect(requests.last.data['delivery_state'], 'shown');
    expect(requests.last.data['user_action'], 'opened');

    final calApi = GoogleCalendarApi(client);
    await calApi.refreshConnection('conn-1');
    expect(requests.last.path, '/calendar/connections/conn-1/refresh');

    await calApi.getSyncJob('job-1');
    expect(requests.last.path, '/calendar/sync-jobs/job-1');

    await calApi.retrySyncJob('job-1', expectedJobRevision: 2);
    expect(requests.last.path, '/calendar/sync-jobs/job-1/retry');
    expect(requests.last.data, {'expected_job_revision': 2});
  });
}
