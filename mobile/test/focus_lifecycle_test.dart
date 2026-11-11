import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
      'M05 starts a server run, retries its end, and submits the final check-in',
      () async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    addTearDown(() => debugDefaultTargetPlatformOverride = null);
    SharedPreferences.setMockInitialValues({'focus_device_id': 'test-device'});

    final commands = <RequestOptions>[];
    var endAttempts = 0;
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        if (request.method != 'GET') commands.add(request);
        final run = {
          'run_id': 'server-run',
          'device_id': 'test-device',
          'run_revision': 2,
        };
        final checkIn = {
          'checkin_id': 'final-checkin',
          'response_revision': 0,
          'response_state': 'open',
        };
        if (request.path.endsWith('/end') && ++endAttempts == 1) {
          handler.reject(DioException(
            requestOptions: request,
            type: DioExceptionType.connectionError,
          ));
          return;
        }
        final data = switch (request.path) {
          '/sessions/calendar-session/start' => run,
          '/runs/server-run/end' => {...run, 'check_in': checkIn},
          '/check-ins/final-checkin/response' => {
              ...checkIn,
              'response_state': 'answered',
            },
          _ => <String, dynamic>{},
        };
        handler.resolve(Response(
          requestOptions: request,
          statusCode: 200,
          data: {'data': data},
        ));
      }));
    final repository = InMemoryRunRepository();
    final focus =
        FocusProvider(api: ApiClient(dio: dio), repository: repository);
    addTearDown(focus.dispose);
    final now = DateTime.now();
    await focus.startSession(FocusSessionInfo(
      sessionId: 'calendar-session',
      scheduleRevision: 7,
      taskTitle: 'Công việc',
      stepTitle: 'Bước',
      scheduledStart: now,
      scheduledEnd: now.add(const Duration(minutes: 10)),
      workSeconds: 600,
      restSeconds: 0,
      microTaskText: 'Mở tài liệu',
      segments: const [],
    ));

    await focus.startRun();
    expect(focus.isActiveRun, isTrue);
    expect(commands.first.path, '/sessions/calendar-session/start');
    expect(commands.first.data['expected_schedule_revision'], 7);

    await focus.endEarly();
    expect(focus.needsEndSync, isTrue);
    expect((await repository.loadActiveRun())?['pending_end'], isNotNull);
    await focus.syncRunEnd();
    expect(focus.hasOpenCheckIn, isTrue);
    expect(commands[1].path, '/runs/server-run/end');
    expect(commands[2].data, commands[1].data);
    expect(commands[2].headers['Idempotency-Key'],
        commands[1].headers['Idempotency-Key']);

    await focus.answerCheckIn('incomplete');
    expect(commands.last.method, 'PUT');
    expect(commands.last.path, '/check-ins/final-checkin/response');
    expect(commands.last.data['session_outcome'], 'incomplete');
    expect(focus.hasUnfinishedServerRun, isFalse);
    expect(await repository.loadActiveRun(), isNull);
  });
}
