import 'dart:async';

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
    var responseAttempts = 0;
    final replayed = Completer<void>();
    RequestOptions? checkInRead;
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        if (request.method != 'GET') commands.add(request);
        if (request.path == '/runs/server-run/check-ins') checkInRead = request;
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
        if (request.path == '/check-ins/final-checkin/response') {
          if (++responseAttempts == 1) {
            handler.reject(DioException(
              requestOptions: request,
              type: DioExceptionType.connectionError,
            ));
            return;
          }
          if (!replayed.isCompleted) replayed.complete();
        }
        final data = switch (request.path) {
          '/sessions/calendar-session/start' => run,
          '/runs/server-run/end' => {...run, 'check_in': checkIn},
          '/runs/server-run/check-ins' => {
              'items': [
                {...checkIn, 'response_revision': 1}
              ],
            },
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
    expect(checkInRead?.queryParameters['kind'], 'final');
    expect(commands[1].path, '/runs/server-run/end');
    expect(commands[2].data, commands[1].data);
    expect(commands[2].headers['Idempotency-Key'],
        commands[1].headers['Idempotency-Key']);

    await focus.queueSkippedCheckIn('skip_session');
    expect(focus.canUndoSkip, isTrue);
    expect((await repository.loadActiveRun())?['skip_undo_until'], isNotNull);
    await focus.retryCheckInResponse();
    expect(commands.last.path, '/runs/server-run/end');
    await focus.undoSkippedCheckIn();
    expect(focus.hasPendingCheckInResponse, isFalse);
    expect((await repository.loadActiveRun())?['pending_response'], isNull);
    await focus.answerCheckIn('incomplete');
    expect(focus.hasPendingCheckInResponse, isTrue);
    expect((await repository.loadActiveRun())?['pending_response'], isNotNull);

    final recovered =
        FocusProvider(api: ApiClient(dio: dio), repository: repository);
    addTearDown(recovered.dispose);
    final settled = Completer<void>();
    recovered.addListener(() {
      if (!recovered.hasUnfinishedServerRun &&
          responseAttempts == 2 &&
          !recovered.runCommandBusy &&
          !settled.isCompleted) {
        settled.complete();
      }
    });
    await recovered.restore();
    await replayed.future.timeout(const Duration(seconds: 5));
    await settled.future.timeout(const Duration(seconds: 5));
    expect(commands[commands.length - 2].data, commands.last.data);
    expect(commands[commands.length - 2].headers['Idempotency-Key'],
        commands.last.headers['Idempotency-Key']);
    expect(commands.last.method, 'PUT');
    expect(commands.last.path, '/check-ins/final-checkin/response');
    expect(commands.last.data['session_outcome'], 'incomplete');
    expect(commands.last.data['expected_response_revision'], 1);
    expect(recovered.hasUnfinishedServerRun, isFalse);
    expect(await repository.loadActiveRun(), isNull);
  });
}
