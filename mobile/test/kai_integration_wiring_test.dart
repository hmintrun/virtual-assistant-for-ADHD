import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_outbox_repository.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/repositories/local_thought_repository.dart';
import 'package:proplan_va/screens/focus_screen.dart';
import 'package:proplan_va/services/pre_session_reminder_sync.dart';
import 'package:proplan_va/services/timer_service.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FixedClock implements Clock {
  _FixedClock(this.current);
  DateTime current;

  @override
  DateTime now() => current;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('PreSessionReminderSync extracts scheduled sessions and offsets cleanly',
      () {
    final sessions = PreSessionReminderSync.buildReminderSessions([
      {
        'session_id': 's1',
        'status': 'scheduled',
        'task_title': 'Ôn thi',
        'step_title': 'Đọc chương 1',
        'scheduled_start': '2026-11-23T09:00:00Z',
        'scheduled_end': '2026-11-23T09:25:00Z',
        'work_seconds': 1500,
        'rest_seconds': 300,
        'micro_task_text': 'Mở PDF chương 1',
      },
      {
        'session_id': 's2',
        'status': 'completed',
        'step_title': 'Đã xong',
        'scheduled_start': '2026-11-23T08:00:00Z',
        'scheduled_end': '2026-11-23T08:25:00Z',
      },
      {
        'session_id': 'broken',
        'status': 'scheduled',
      },
    ]);

    expect(sessions, hasLength(1));
    expect(sessions.single.sessionId, 's1');
    expect(sessions.single.stepTitle, 'Đọc chương 1');
    expect(sessions.single.plannedMinutes, 25);
    expect(sessions.single.microTaskText, 'Mở PDF chương 1');

    expect(
      PreSessionReminderSync.extractOffsetsSeconds({
        'reminder_offsets': [900, 600, 300],
      }),
      [900, 600, 300],
    );
    expect(PreSessionReminderSync.extractOffsetsSeconds({'reminder_offsets': []}),
        isNull);
    expect(PreSessionReminderSync.extractOffsetsSeconds(null), isNull);
  });

  test('parseCheckInVoiceOutcome maps Vietnamese check-in responses accurately',
      () {
    expect(parseCheckInVoiceOutcome('Mình hoàn thành phiên rồi'), 'completed');
    expect(parseCheckInVoiceOutcome('Đã xong hết bước này'), 'completed');
    expect(parseCheckInVoiceOutcome('Vẫn chưa xong, còn dang dở'), 'incomplete');
    expect(parseCheckInVoiceOutcome('Chưa hoàn thành phần 2'), 'incomplete');
    expect(parseCheckInVoiceOutcome('Có việc đột xuất nên phải dừng'), 'busy');
    expect(parseCheckInVoiceOutcome('Bận việc khác mất rồi'), 'busy');
    expect(parseCheckInVoiceOutcome('Cho mình bỏ qua phiên này'), 'skipped');
    expect(parseCheckInVoiceOutcome('Hoãn lại lát làm tiếp'), 'skipped');
    expect(parseCheckInVoiceOutcome('Alo một hai ba'), isNull);
  });

  test(
      'FocusProvider.checkCheckInExpiry marks expired open check-in as no_response cleanly',
      () async {
    SharedPreferences.setMockInitialValues({'focus_device_id': 'dev-1'});
    final clock = _FixedClock(DateTime.utc(2026, 11, 23, 8, 10, 0));
    final runRepo = InMemoryRunRepository();
    await runRepo.saveActiveRun({
      'session': {
        'session_id': 'sess-exp',
        'task_title': 'Viết báo cáo',
        'step_title': 'Soạn dàn ý',
        'scheduled_start': '2026-11-23T07:30:00Z',
        'scheduled_end': '2026-11-23T08:00:00Z',
        'work_seconds': 1500,
        'rest_seconds': 300,
        'micro_task_text': 'Mở file mẫu',
        'segments': const [
          {'order_index': 0, 'kind': 'work', 'duration_seconds': 1500},
        ],
      },
      'server_run': {
        'run_id': 'run-exp',
        'run_revision': 2,
        'checkin_timeout_at': '2026-11-23T08:05:00Z',
        'check_in': {
          'checkin_id': 'ci-exp',
          'response_revision': 0,
          'response_state': 'open',
        },
      },
      'stage': 'ended',
      'run_ends_at': '2026-11-23T08:00:00Z',
      'actual_work_seconds': 1500,
    });

    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        handler.reject(DioException(
          requestOptions: request,
          type: DioExceptionType.connectionError,
        ));
      }));

    final focus = FocusProvider(
      ownerId: 'u-expiry',
      clock: clock,
      api: ApiClient(dio: dio, bindSession: false),
      repository: runRepo,
      outboxRepository: InMemoryOutboxRepository(),
      thoughtRepository: InMemoryThoughtRepository(),
      nativeRecoveryChecker: NativeRecoveryChecker.fromCallback(() async => null),
    );
    addTearDown(focus.dispose);

    await focus.restore();
    expect(focus.checkInExpired, isTrue);
    expect(focus.awaitingCheckIn, isFalse);

    // Also test explicit live checkCheckInExpiry() when timeout elapses while screen is open.
    await runRepo.saveActiveRun({
      'session': {
        'session_id': 'sess-live-exp',
        'task_title': 'Viết báo cáo',
        'step_title': 'Soạn dàn ý',
        'scheduled_start': '2026-11-23T08:00:00Z',
        'scheduled_end': '2026-11-23T08:30:00Z',
        'work_seconds': 1500,
        'rest_seconds': 300,
        'micro_task_text': 'Mở file mẫu',
        'segments': const [
          {'order_index': 0, 'kind': 'work', 'duration_seconds': 1500},
        ],
      },
      'server_run': {
        'run_id': 'run-live-exp',
        'run_revision': 1,
        'checkin_timeout_at': '2026-11-23T08:15:00Z',
        'check_in': {
          'checkin_id': 'ci-live',
          'response_revision': 0,
          'response_state': 'open',
        },
      },
      'stage': 'ended',
      'run_ends_at': '2026-11-23T08:10:00Z',
      'actual_work_seconds': 1500,
    });

    await focus.restore();
    expect(focus.awaitingCheckIn, isTrue);
    expect(focus.checkInExpired, isFalse);

    clock.current = DateTime.utc(2026, 11, 23, 8, 16, 0);
    final expiredNow = await focus.checkCheckInExpiry();
    expect(expiredNow, isTrue);
    expect(focus.checkInExpired, isTrue);
    expect(focus.awaitingCheckIn, isFalse);
    expect(focus.hasOpenCheckIn, isFalse);
    expect(focus.missedCheckIn, isTrue);
    expect(await runRepo.loadActiveRun(), isNull);
  });

  testWidgets('FocusScreen lockPreview renders real Android reminder copy',
      (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    SharedPreferences.setMockInitialValues({});
    final now = DateTime.utc(2026, 11, 23, 9, 0, 0);
    final focus = FocusProvider(
      ownerId: 'u-preview',
      clock: _FixedClock(now),
      api: ApiClient(dio: Dio(), bindSession: false),
      repository: InMemoryRunRepository(),
      outboxRepository: InMemoryOutboxRepository(),
      thoughtRepository: InMemoryThoughtRepository(),
    );

    try {
      await tester.runAsync(() => focus.startSession(FocusSessionInfo(
            sessionId: 'sess-prev',
            taskTitle: 'Đọc tài liệu kỹ thuật',
            stepTitle: 'Đọc mục 1',
            scheduledStart: now.add(const Duration(minutes: 20)),
            scheduledEnd: now.add(const Duration(minutes: 45)),
            workSeconds: 1500,
            restSeconds: 300,
            microTaskText: 'Mở tài liệu cần thiết',
            segments: const [],
          )));
      focus.showLockPreview();

      await tester.pumpWidget(
        ChangeNotifierProvider<FocusProvider>.value(
          value: focus,
          child: MaterialApp(
            theme: MelbelTheme.themeFor(isDark: false),
            home: const FocusScreen(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.textContaining('Đây là mô phỏng'), findsNothing);
      expect(
        find.textContaining('Khởi động 2 phút: Mở tài liệu cần thiết'),
        findsOneWidget,
      );
      expect(
        find.textContaining('được đồng bộ với lịch nhắc hệ thống trên Android.'),
        findsOneWidget,
      );
      expect(find.byType(AlertDialog), findsNothing);
    } finally {
      focus.dispose();
      debugDefaultTargetPlatformOverride = null;
    }
  });
}
