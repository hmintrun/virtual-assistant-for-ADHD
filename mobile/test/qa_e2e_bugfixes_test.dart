import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/models/planning_models.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/providers/streak_provider.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/providers/theme_provider.dart';
import 'package:proplan_va/screens/candidate_review_screen.dart';
import 'package:proplan_va/screens/focus_review_screens.dart';
import 'package:proplan_va/screens/focus_screen.dart';
import 'package:proplan_va/screens/home_screen.dart';
import 'package:proplan_va/screens/main_shell_screen.dart';
import 'package:proplan_va/services/adaptation_api.dart';
import 'package:proplan_va/services/auth_service.dart';
import 'package:proplan_va/services/calendar_api.dart';
import 'package:proplan_va/services/planning_api.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';
import 'package:proplan_va/widgets/task_delete_sheet.dart';

class _RecordingApiClient extends ApiClient {
  _RecordingApiClient({
    this.draftItems = const [],
    this.streakResponse,
    this.repairResponse,
  }) : super(bindSession: false);

  int scheduleRequests = 0;
  final List<Map<String, dynamic>> calls = [];
  final List<Map<String, dynamic>> draftItems;
  final Map<String, dynamic>? streakResponse;
  final Map<String, dynamic>? repairResponse;

  @override
  Future<Map<String, dynamic>> checkHealth() async =>
      {'status': 'ok', 'database': 'ok'};

  @override
  Future<List<Map<String, dynamic>>> getListData(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    calls.add({'method': 'GET_LIST', 'path': path});
    return const [];
  }

  @override
  Future<Map<String, dynamic>?> getOptionalData(String path) async => null;

  @override
  Future<Map<String, dynamic>> getData(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    calls.add({
      'method': 'GET',
      'path': path,
      'query': queryParameters,
    });
    if (path == '/schedule') {
      scheduleRequests++;
      return {'items': <Map<String, dynamic>>[]};
    }
    if (path == '/drafts') {
      return {'items': draftItems};
    }
    if (path == '/streak') {
      return streakResponse ?? <String, dynamic>{};
    }
    return {'items': <Map<String, dynamic>>[]};
  }

  @override
  Future<Map<String, dynamic>> postData(
    String path, {
    Object? data,
    String? idempotencyKey,
    bool idempotent = true,
    Duration? receiveTimeout,
  }) async {
    calls.add({
      'method': 'POST',
      'path': path,
      'data': data,
      'idempotencyKey': idempotencyKey,
      'receiveTimeout': receiveTimeout,
    });
    if (path == '/streak/repair') {
      return repairResponse ?? streakResponse ?? <String, dynamic>{};
    }
    if (path.endsWith('/analyze')) {
      return {
        'draft_id': 'draft_1',
        'stage': 'summary_review',
        'summary': {'title': 'Việc đã phân tích', 'can_plan': true},
        'summary_revision': 2,
        'planning_input_revision': 2,
        'input_source': 'text',
      };
    }
    return <String, dynamic>{};
  }

  @override
  Future<Map<String, dynamic>> deleteData(
    String path, {
    Object? data,
    Map<String, dynamic>? queryParameters,
    String? idempotencyKey,
    bool idempotent = false,
  }) async {
    calls.add({
      'method': 'DELETE',
      'path': path,
      'query': queryParameters,
      'idempotencyKey': idempotencyKey,
      'idempotent': idempotent,
    });
    return {'task_id': 'task_42', 'deleted': true, 'status': 'cancelled'};
  }
}

class _ReplanFocusProvider extends FocusProvider {
  _ReplanFocusProvider(this._session);

  final FocusSessionInfo _session;

  @override
  FocusSessionInfo? get session => _session;

  @override
  FocusStage get stage => FocusStage.ready;

  @override
  String? get runError => 'Quá hạn bắt đầu phiên này.';

  @override
  bool get startRequiresReplan => true;
}

class _FakeAdaptationApi extends AdaptationApi {
  _FakeAdaptationApi() : super(ApiClient(bindSession: false));

  @override
  Future<Map<String, dynamic>> proposal(String id) async => {
        'proposal_id': id,
        'status': 'pending',
        'target_task_id': 'task_1',
        'proposed_changes': <String, dynamic>{},
        'feasibility': {'confirmable': false, 'deficit_seconds': 600},
      };
}

class _UnknownFeasibilityTaskProvider extends TaskProvider {
  _UnknownFeasibilityTaskProvider()
      : super(
          planningApi: PlanningApi(client: _RecordingApiClient()),
          ownerId: 'qa_user',
        );

  final _candidate = const PlanCandidateModel(raw: {
    'candidate_id': 'cand_1',
    'draft_id': 'draft_1',
    'review_revision': 1,
    'source_summary_revision': 1,
    'source_planning_input_revision': 1,
    'stale': false,
    'feasibility': {'status': 'mystery_internal_token'},
    'steps': <Map<String, dynamic>>[],
    'sessions': <Map<String, dynamic>>[],
  });

  @override
  String get title => 'Kiểm thử kế hoạch';

  @override
  PlanCandidateModel? get candidate => _candidate;
}

class _FailingAnalyzeApiClient extends _RecordingApiClient {
  @override
  Future<Map<String, dynamic>> postData(
    String path, {
    Object? data,
    String? idempotencyKey,
    bool idempotent = true,
    Duration? receiveTimeout,
  }) async {
    if (path == '/drafts') {
      return {
        'draft_id': 'draft_err',
        'stage': 'collecting',
        'summary': {'title': 'Việc lỗi phân tích'},
        'summary_revision': 1,
        'planning_input_revision': 1,
        'input_source': 'text',
        'raw_input': 'Việc lỗi phân tích',
      };
    }
    throw const ApiException('Phân tích thất bại tạm thời');
  }
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
  });

  testWidgets(
      'MOB-BUG-01: FocusScreen renders direct replan CTA above Xem lịch when startRequiresReplan',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final start = DateTime.now().add(const Duration(minutes: 5));
    final focus = _ReplanFocusProvider(
      FocusSessionInfo(
        sessionId: 'sess_replan_1',
        taskTitle: 'Ôn tập',
        stepTitle: 'Đọc lại ghi chú',
        scheduledStart: start,
        scheduledEnd: start.add(const Duration(minutes: 25)),
        workSeconds: 1500,
        restSeconds: 300,
        microTaskText: 'Mở file',
        segments: const [],
      ),
    );
    addTearDown(focus.dispose);

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

    expect(
      find.widgetWithText(PrimaryCta, 'Mở đề xuất xếp lại lịch'),
      findsOneWidget,
    );
    expect(find.widgetWithText(SecondaryCta, 'Xem lịch'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'MOB-BUG-02: MainShellScreen keeps persistent bottom nav bar and hides back arrow on tabs',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final api = _RecordingApiClient();
    final auth = AuthService();
    final theme = ThemeProvider();
    final focus = FocusProvider(api: api, ownerId: 'qa_shell');
    final task = TaskProvider(
      planningApi: PlanningApi(client: api, ownerId: 'qa_shell'),
      ownerId: 'qa_shell',
    );

    try {
      await tester.pumpWidget(
        MultiProvider(
          providers: [
            ChangeNotifierProvider<AuthService>.value(value: auth),
            ChangeNotifierProvider<ThemeProvider>.value(value: theme),
            ChangeNotifierProvider<FocusProvider>.value(value: focus),
            ChangeNotifierProvider<TaskProvider>.value(value: task),
          ],
          child: MaterialApp(
            theme: MelbelTheme.themeFor(isDark: false),
            home: MainShellScreen(api: api),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byType(MelbelFloatingNavBar), findsOneWidget);
      expect(find.byTooltip('Quay lại'), findsNothing);

      await tester.tap(find.text('Lịch'));
      await tester.pumpAndSettle();
      expect(find.byType(MelbelFloatingNavBar), findsOneWidget);
      expect(find.byTooltip('Quay lại'), findsNothing);

      await tester.tap(find.text('Tài khoản'));
      await tester.pumpAndSettle();
      expect(find.byType(MelbelFloatingNavBar), findsOneWidget);
      expect(find.byTooltip('Quay lại'), findsNothing);

      await tester.tap(find.text('Hôm nay'));
      await tester.pumpAndSettle();
      expect(find.byType(MelbelFloatingNavBar), findsOneWidget);
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      auth.dispose();
      theme.dispose();
      focus.dispose();
      task.dispose();
    }
  });

  testWidgets(
      'MOB-BUG-03: HomeScreen reloads /schedule when FocusProvider.markScheduleSynced fires',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final api = _RecordingApiClient();
    final focus = FocusProvider(api: api, ownerId: 'qa_sync');
    final task = TaskProvider(
      planningApi: PlanningApi(client: api, ownerId: 'qa_sync'),
      ownerId: 'qa_sync',
    );
    addTearDown(focus.dispose);
    addTearDown(task.dispose);

    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<FocusProvider>.value(value: focus),
          ChangeNotifierProvider<TaskProvider>.value(value: task),
        ],
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: false),
          home: HomeScreen(api: api),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final initialRequests = api.scheduleRequests;
    expect(initialRequests, greaterThanOrEqualTo(1));

    focus.markScheduleSynced();
    await tester.pump();
    await tester.pumpAndSettle();

    expect(api.scheduleRequests, greaterThan(initialRequests));
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'MOB-BUG-04: COGA deadline SnackBar and feasibility fallback contain no technical jargon',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: MelbelTheme.themeFor(isDark: false),
        home: ScheduleComparisonScreen(
          reason: 'Chưa xong',
          proposalId: 'prop_1',
          api: _FakeAdaptationApi(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Lùi hạn chót'));
    await tester.pump();
    final snack = tester.widget<SnackBar>(find.byType(SnackBar));
    final text = (snack.content as Text).data ?? '';
    expect(text, isNotEmpty);
    expect(text, isNot(contains('Contract')));
    expect(text, isNot(contains('API')));
    expect(text, isNot(contains('Draft')));
    expect(text, isNot(contains('Task')));

    final task = _UnknownFeasibilityTaskProvider();
    addTearDown(task.dispose);
    await tester.pumpWidget(
      ChangeNotifierProvider<TaskProvider>.value(
        value: task,
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: false),
          home: const CandidateReviewScreen(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Cần kiểm tra lại khả năng xếp lịch.'), findsOneWidget);
    expect(find.textContaining('mystery_internal_token'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  test(
      'MOB-BUG-05: excludes ProPlan VA Focus write calendar and hides google_calendar_user placeholder',
      () {
    const primaryCal = CalendarItem(
      id: 'primary@example.com',
      summary: 'tan@example.com',
      primary: true,
      accessRole: 'owner',
      selectedForRead: true,
      selectedForWrite: false,
    );
    const focusCal = CalendarItem(
      id: 'cal_focus_id',
      summary: 'ProPlan VA Focus',
      primary: false,
      accessRole: 'owner',
      selectedForRead: true,
      selectedForWrite: true,
    );
    const workCal = CalendarItem(
      id: 'work_id',
      summary: 'Lịch Học',
      primary: false,
      accessRole: 'reader',
      selectedForRead: true,
      selectedForWrite: false,
    );

    expect(
      isDedicatedFocusWriteCalendar(focusCal, writeCalendarId: 'cal_focus_id'),
      isTrue,
    );
    expect(
      isDedicatedFocusWriteCalendar(focusCal),
      isTrue,
    );
    expect(
      isDedicatedFocusWriteCalendar(primaryCal, writeCalendarId: 'primary@example.com'),
      isFalse,
    );
    expect(isDedicatedFocusWriteCalendar(workCal), isFalse);

    expect(
      formatCalendarAccountLabel(
        'google_calendar_user',
        fallbackEmail: 'tan@example.com',
      ),
      'tan@example.com',
    );
    expect(
      formatCalendarAccountLabel('google_calendar_user'),
      'Tài khoản Google đã liên kết',
    );
    expect(
      formatCalendarAccountLabel('personal@gmail.com'),
      'personal@gmail.com',
    );
  });

  test(
      'MOB-BUG-06 & TC_FAIL_05: analyzeDraft uses 35s timeout and fetchOpenDrafts filters confirmed items',
      () async {
    final api = _RecordingApiClient(
      draftItems: [
        {
          'draft_id': 'd_open',
          'stage': 'collecting',
          'summary': {'title': 'Bản nháp còn mở'},
          'summary_revision': 1,
          'planning_input_revision': 1,
          'input_source': 'text',
        },
        {
          'draft_id': 'd_confirmed',
          'stage': 'confirmed',
          'confirmed_task_id': 'task_99',
          'summary': {'title': 'Đã chốt'},
          'summary_revision': 2,
          'planning_input_revision': 2,
          'input_source': 'text',
        },
      ],
    );
    final planningApi = PlanningApi(client: api, ownerId: 'qa_drafts');
    const draft = PlanningDraftModel(
      draftId: 'draft_1',
      stage: 'collecting',
      summary: {'title': 'Việc thử nghiệm'},
      summaryRevision: 1,
      planningInputRevision: 1,
      inputSource: 'text',
    );

    await planningApi.analyzeDraft(draft);
    final analyzeCall =
        api.calls.lastWhere((call) => call['path'] == '/drafts/draft_1/analyze');
    expect(analyzeCall['receiveTimeout'], const Duration(seconds: 35));

    final failingPlanningApi = PlanningApi(
      client: _FailingAnalyzeApiClient(),
      ownerId: 'qa_err',
    );
    final errTask = TaskProvider(
      planningApi: failingPlanningApi,
      ownerId: 'qa_err',
    );
    addTearDown(errTask.dispose);
    errTask.setTitle('Việc lỗi phân tích');
    await expectLater(errTask.prepareDraftAndAnalyze(), throwsA(isA<ApiException>()));
    expect(errTask.apiError, 'Phân tích thất bại tạm thời');
    errTask.clearApiError();
    expect(errTask.apiError, isNull);

    final directTask = TaskProvider(planningApi: planningApi, ownerId: 'qa_drafts');
    addTearDown(directTask.dispose);
    final openDrafts = await directTask.fetchOpenDrafts();
    expect(openDrafts.map((d) => d.draftId), ['d_open']);
  });

  test('MOB-BUG-07: Android unconfigured Google Sign-In sets clear error safely',
      () async {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    final auth = AuthService();
    try {
      await auth.signIn();
      expect(auth.error, isNotNull);
    } finally {
      auth.dispose();
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets(
      'Confirmed Task Delete: PlanningApi.deleteTask & COGA bottom sheet work without AlertDialog',
      (tester) async {
    final api = _RecordingApiClient();
    final planningApi = PlanningApi(client: api, ownerId: 'qa_delete');
    await planningApi.deleteTask('task_42', expectedTaskRevision: 3);

    final deleteCall =
        api.calls.lastWhere((call) => call['method'] == 'DELETE');
    expect(deleteCall['path'], '/tasks/task_42');
    expect(deleteCall['query'], {'expected_task_revision': 3});
    expect(deleteCall['idempotent'], isTrue);
    expect((deleteCall['idempotencyKey'] as String?)?.isNotEmpty, isTrue);

    final taskProvider = TaskProvider(planningApi: planningApi, ownerId: 'qa_delete');
    addTearDown(taskProvider.dispose);

    bool? result;
    await tester.pumpWidget(
      ChangeNotifierProvider<TaskProvider>.value(
        value: taskProvider,
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: false),
          home: Scaffold(
            body: Builder(
              builder: (context) => ElevatedButton(
                onPressed: () async {
                  result = await confirmAndDeleteTask(
                    context,
                    taskId: 'task_42',
                    taskTitle: 'Viết báo cáo',
                    taskRevision: 3,
                    api: api,
                  );
                },
                child: const Text('Mở xóa'),
              ),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('Mở xóa'));
    await tester.pumpAndSettle();

    expect(find.byType(AlertDialog), findsNothing);
    expect(find.text('Xóa công việc này?'), findsOneWidget);
    expect(find.text('Viết báo cáo'), findsOneWidget);

    await tester.tap(find.text('Xóa công việc'));
    await tester.pumpAndSettle();

    expect(result, isTrue);
    expect(find.text('Đã xóa công việc "Viết báo cáo".'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  test(
      'Streak Sync: GET /streak and POST /streak/repair synchronize state with backend',
      () async {
    final today = DateTime(2026, 11, 26, 10);
    final api = _RecordingApiClient(
      streakResponse: {
        'current_streak_days': 0,
        'effective_display_streak': 4,
        'unlocked': true,
        'streak_frozen': false,
        'streak_freeze_tokens': 1,
        'max_freeze_tokens': 2,
        'last_streak_date': '2026-11-24',
        'repair_pending': true,
        'repair_window_ends_at': '2026-11-26T23:59:59+07:00',
        'repairs_used_this_month': 0,
        'repairs_left_this_month': 2,
        'max_repairs_per_month': 2,
        'longest_streak_days': 6,
        'today_completed': false,
        'today_focus_minutes': 0,
        'timezone': 'Asia/Ho_Chi_Minh',
        'day_records': [
          {'date': '2026-11-24', 'kind': 'done', 'minutes': 25},
          {'date': '2026-11-25', 'kind': 'repair', 'minutes': 0},
        ],
      },
      repairResponse: {
        'current_streak_days': 5,
        'effective_display_streak': 5,
        'unlocked': true,
        'streak_frozen': false,
        'streak_freeze_tokens': 1,
        'max_freeze_tokens': 2,
        'last_streak_date': '2026-11-26',
        'repair_pending': false,
        'repair_window_ends_at': null,
        'repairs_used_this_month': 1,
        'repairs_left_this_month': 1,
        'max_repairs_per_month': 2,
        'longest_streak_days': 6,
        'today_completed': true,
        'today_focus_minutes': 3,
        'timezone': 'Asia/Ho_Chi_Minh',
        'day_records': [
          {'date': '2026-11-24', 'kind': 'done', 'minutes': 25},
          {'date': '2026-11-25', 'kind': 'freeze', 'minutes': 0},
          {'date': '2026-11-26', 'kind': 'done', 'minutes': 3},
        ],
      },
    );

    final streak = StreakProvider(
      ownerId: 'qa_streak',
      api: api,
      now: () => today,
    );
    addTearDown(streak.dispose);
    await streak.restore();

    final synced = await streak.syncFromBackend();
    expect(synced, isTrue);
    expect(streak.syncedFromBackend, isTrue);
    expect(streak.repairPending, isTrue);
    expect(streak.effectiveDisplayStreak, 4);
    expect(streak.longestStreak, 6);
    expect(streak.freezeCount, 1);
    expect(streak.dayStatus(DateTime(2026, 11, 25)), StreakDayKind.repair);
    expect(streak.canRepairNow, isTrue);

    final err = await streak.repairWithBackend();
    expect(err, isNull);
    expect(streak.repairPending, isFalse);
    expect(streak.currentStreak, 5);
    expect(streak.effectiveDisplayStreak, 5);
    expect(streak.repairsUsedThisMonth, 1);
    expect(streak.todayDone, isTrue);
    expect(streak.dayStatus(DateTime(2026, 11, 25)), StreakDayKind.freeze);
    final celebration = streak.consumePendingCelebration();
    expect(celebration, isNotNull);
    expect(celebration!.restoredFromRepair, isTrue);

    final repairCall =
        api.calls.lastWhere((call) => call['path'] == '/streak/repair');
    expect(repairCall['idempotencyKey'], isNotNull);
  });
}
