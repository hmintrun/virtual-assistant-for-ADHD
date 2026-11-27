import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/models/planning_models.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/providers/streak_provider.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/providers/theme_provider.dart';
import 'package:proplan_va/screens/calendar_screen.dart';
import 'package:proplan_va/screens/focus_review_screens.dart';
import 'package:proplan_va/screens/focus_screen.dart';
import 'package:proplan_va/screens/settings_screen.dart';
import 'package:proplan_va/screens/splash_screen.dart';
import 'package:proplan_va/screens/support_screens.dart';
import 'package:proplan_va/screens/task_entry_screen.dart';
import 'package:proplan_va/screens/task_summary_screen.dart';
import 'package:proplan_va/services/adaptation_api.dart';
import 'package:proplan_va/services/auth_service.dart';
import 'package:proplan_va/widgets/countdown_timer_widget.dart';
import 'package:proplan_va/widgets/focus_stage_widgets.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';
import 'package:proplan_va/widgets/undo_snackbar.dart';

class _OfflineCalendarClient extends ApiClient {
  _OfflineCalendarClient() : super(bindSession: false);

  @override
  Future<Map<String, dynamic>> getData(String path,
      {Map<String, dynamic>? queryParameters}) async {
    throw const ApiException('Offline');
  }
}

class _LocalValidationSummaryTask extends TaskProvider {
  _LocalValidationSummaryTask(this.testDraft);

  final PlanningDraftModel testDraft;
  int clarificationCalls = 0;

  @override
  String get title => testDraft.title;

  @override
  String get scope => testDraft.scope;

  @override
  DateTime? get deadline => DateTime(2027, 1, 15, 18);

  @override
  String get availabilitySummary => 'Chưa chọn khung giờ rảnh.';

  @override
  PlanningDraftModel? get draft => testDraft;

  @override
  bool get canPlan => false;

  @override
  bool get isApiBusy => false;

  @override
  Future<void> submitClarification(String answer) async {
    clarificationCalls++;
  }
}

void main() {
  testWidgets('MelbelMascot responds to tap and stroke petting cleanly',
      (tester) async {
    final whispers = <String>[];
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: Scaffold(
        body: Center(
          child: MelbelMascot(
            onPetted: whispers.add,
          ),
        ),
      ),
    ));

    await tester.tap(find.byType(MelbelMascot));
    await tester.pump(const Duration(milliseconds: 100));
    expect(whispers, hasLength(1));
    expect(whispers.first, isNotEmpty);

    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();

    await tester.drag(find.byType(MelbelMascot), const Offset(36, 0));
    await tester.pump(const Duration(milliseconds: 100));
    expect(whispers.length, greaterThanOrEqualTo(2));

    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('MelbelMascot respects interactive=false', (tester) async {
    var count = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Center(
          child: MelbelMascot(
            interactive: false,
            onPetted: (_) => count++,
          ),
        ),
      ),
    ));

    await tester.tap(find.byType(MelbelMascot));
    await tester.pumpAndSettle();
    expect(count, 0);
  });

  testWidgets('MelbelAppBar interactive buttons meet 48dp Android touch target',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: const Scaffold(
        appBar: MelbelAppBar(
          title: 'Kiểm tra chạm',
          showBack: true,
        ),
      ),
    ));

    for (final tooltip in const ['Quay lại', 'Trợ giúp & Hướng dẫn', 'Tài khoản']) {
      final size = tester.getSize(find.byTooltip(tooltip));
      expect(size.width, greaterThanOrEqualTo(48));
      expect(size.height, greaterThanOrEqualTo(48));
    }
  });

  test('MelbelPalette exposes distinct semantic colors in light and dark modes',
      () {
    for (final palette in const [MelbelPalette.light, MelbelPalette.dark]) {
      expect(palette.sage, isNot(palette.amber));
      expect(palette.sage, isNot(palette.softError));
      expect(palette.amber, isNot(palette.softError));
      expect(palette.sageSoft, isNot(palette.amberSoft));
    }
  });

  testWidgets('MelbelFocusTimerRing scales safely on narrow screens',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: const MediaQuery(
        data: MediaQueryData(
          size: Size(320, 640),
          textScaler: TextScaler.linear(1.8),
        ),
        child: Scaffold(
          body: Center(
            child: MelbelFocusTimerRing(
              timeText: '24:59',
              progress: 0.75,
              subtitle: 'Đang làm · 25 phút',
              isOffline: true,
            ),
          ),
        ),
      ),
    ));

    expect(find.text('24:59'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('MelbelMascot supports long-press cuddle petting cleanly',
      (tester) async {
    final whispers = <String>[];
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: Scaffold(
        body: Center(
          child: MelbelMascot(
            onPetted: whispers.add,
          ),
        ),
      ),
    ));

    await tester.longPress(find.byType(MelbelMascot));
    await tester.pump(const Duration(milliseconds: 100));
    expect(whispers, hasLength(1));

    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('MelbelSplashScreen reveals logo, app name, and completes',
      (tester) async {
    var completed = 0;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: MelbelSplashScreen(
        duration: const Duration(milliseconds: 600),
        onComplete: () => completed++,
      ),
    ));

    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('ProPlan VA'), findsOneWidget);
    expect(
      find.text('Trợ lý Melbel · Đồng hành từng bước nhỏ'),
      findsOneWidget,
    );

    await tester.pumpAndSettle();
    expect(completed, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets('MelbelSplashScreen supports tap-to-skip and reduced motion',
      (tester) async {
    var skipped = 0;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: MelbelSplashScreen(
        canSkip: true,
        duration: const Duration(milliseconds: 1500),
        onComplete: () => skipped++,
      ),
    ));

    await tester.pump(const Duration(milliseconds: 120));
    await tester.tap(find.byType(MelbelSplashScreen));
    await tester.pump();
    expect(skipped, 1);

    var reducedCompleted = 0;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: MelbelSplashScreen(
          onComplete: () => reducedCompleted++,
        ),
      ),
    ));

    expect(find.text('ProPlan VA'), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();
    expect(reducedCompleted, 1);
    expect(tester.takeException(), isNull);
  });

  test('Dark mode adapts primary and semantic accents with dark-ink pastel contrast',
      () {
    expect(MelbelPalette.dark.sage, isNot(MelbelPalette.light.sage));
    expect(
      MelbelPalette.dark.sage.computeLuminance(),
      greaterThan(MelbelPalette.light.sage.computeLuminance()),
    );
    expect(
      MelbelPalette.dark.onSage.computeLuminance(),
      lessThan(MelbelPalette.light.onSage.computeLuminance()),
    );
    expect(
      MelbelPalette.dark.success.computeLuminance(),
      greaterThan(MelbelPalette.light.success.computeLuminance()),
    );
    expect(
      MelbelPalette.dark.softError.computeLuminance(),
      greaterThan(MelbelPalette.light.softError.computeLuminance()),
    );
    expect(
      MelbelPalette.dark.amber.computeLuminance(),
      greaterThan(MelbelPalette.light.amber.computeLuminance()),
    );
  });

  testWidgets('MelbelChatBlock renders cleanly on narrow screens and pets',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    var message = 'Chào bạn, hôm nay đi từng bước nhỏ nhé.';
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: MediaQuery(
        data: const MediaQueryData(
          size: Size(320, 640),
          textScaler: TextScaler.linear(1.6),
        ),
        child: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) => SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: MelbelChatBlock(
                avatarOnRight: true,
                senderLabel: 'Melbel',
                badge: 'Trợ lý VA',
                title: 'Chào buổi tối',
                message: message,
                footnote: 'Chạm hoặc vuốt nhẹ Melbel',
                onPetted: (whisper) => setState(() => message = whisper),
              ),
            ),
          ),
        ),
      ),
    ));

    expect(find.text('Melbel'), findsOneWidget);
    expect(find.text('Chào buổi tối'), findsOneWidget);
    await tester.tap(find.byType(MelbelMascot));
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(find.text(MelbelMascot.companionWhispers.first), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  for (final isDark in [false, true]) {
    testWidgets(
        'MelbelHeroBanner renders cleanly at 320x640 1.5x scale (dark=$isDark)',
        (tester) async {
      tester.view.physicalSize = const Size(320, 640);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      MelbelColors.isDark = isDark;
      addTearDown(() => MelbelColors.isDark = false);

      var message = 'Hôm nay đi từng bước nhỏ nhé.';
      await tester.pumpWidget(MaterialApp(
        theme: MelbelTheme.currentTheme,
        home: MediaQuery(
          data: const MediaQueryData(
            size: Size(320, 640),
            textScaler: TextScaler.linear(1.5),
          ),
          child: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) => SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: MelbelHeroBanner(
                  badge: '21/11/2026',
                  title: 'Chào buổi tối',
                  message: message,
                  onPetted: (whisper) => setState(() => message = whisper),
                ),
              ),
            ),
          ),
        ),
      ));

      expect(find.text('Melbel'), findsOneWidget);
      expect(find.text('21/11/2026'), findsOneWidget);
      expect(find.text('Chào buổi tối'), findsOneWidget);
      expect(find.byType(MelbelDoodleRays), findsOneWidget);

      await tester.tap(find.byType(MelbelMascot));
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(find.text(MelbelMascot.companionWhispers.first), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('MelbelFloatingNavBar renders all 4 tabs and fires callbacks',
      (tester) async {
    final tapped = <int>[];
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: Scaffold(
        bottomNavigationBar: MelbelFloatingNavBar(
          selectedIndex: 0,
          onSelected: tapped.add,
        ),
      ),
    ));

    for (final label in const ['Hôm nay', 'Lịch', 'Thêm việc', 'Tài khoản']) {
      expect(find.text(label), findsOneWidget);
    }
    await tester.tap(find.text('Lịch'));
    await tester.tap(find.text('Thêm việc'));
    await tester.tap(find.text('Tài khoản'));
    expect(tapped, [1, 2, 3]);
    expect(tester.takeException(), isNull);
  });

  for (final isDark in [false, true]) {
    testWidgets(
        'CalendarScreen renders peeking Melbel and mockup tiles (dark=$isDark)',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      MelbelColors.isDark = isDark;
      addTearDown(() => MelbelColors.isDark = false);

      await tester.pumpWidget(MaterialApp(
        theme: MelbelTheme.currentTheme,
        home: CalendarScreen(client: _OfflineCalendarClient()),
      ));
      await tester.pumpAndSettle();

      expect(find.byType(MelbelMascot), findsOneWidget);
      expect(find.byType(MelbelDoodleRays), findsWidgets);
      expect(find.text('Chưa tải được lịch.'), findsOneWidget);
      expect(find.text('Google Calendar'), findsOneWidget);
      expect(find.text('Đổi lịch'), findsOneWidget);
      expect(find.text('Thêm việc'), findsOneWidget);
      expect(find.byTooltip('Xem hôm nay'), findsOneWidget);
      expect(find.byTooltip('Hỏi lịch'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets(
        'HelpScreen renders banners and top back button cleanly without duplicate bottom CTA (dark=$isDark)',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      MelbelColors.isDark = isDark;
      addTearDown(() => MelbelColors.isDark = false);

      await tester.pumpWidget(MaterialApp(
        theme: MelbelTheme.currentTheme,
        home: const HelpScreen(),
      ));
      await tester.pumpAndSettle();

      expect(find.byType(MelbelPageBackdrop), findsOneWidget);
      expect(find.byType(MelbelSectionBanner), findsNWidgets(2));
      expect(find.text('Các thao tác thường dùng'), findsOneWidget);
      expect(find.text('Câu hỏi thường gặp'), findsOneWidget);
      expect(find.byTooltip('Quay lại'), findsOneWidget);
      expect(find.widgetWithText(SecondaryCta, 'Quay lại'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets(
        'TaskEntryScreen renders cushion Melbel, voice rays and pill CTA (dark=$isDark)',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      MelbelColors.isDark = isDark;
      addTearDown(() => MelbelColors.isDark = false);
      FlutterSecureStorage.setMockInitialValues({});
      final task = TaskProvider();

      try {
        await tester.pumpWidget(ChangeNotifierProvider<TaskProvider>.value(
          value: task,
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: const TaskEntryScreen(),
          ),
        ));
        await tester.pumpAndSettle();

        expect(find.byType(MelbelPageBackdrop), findsOneWidget);
        expect(find.byType(MelbelCushionAvatar), findsOneWidget);
        expect(find.byType(MelbelDoodleRays), findsWidgets);
        expect(find.text('Chạm để nói'), findsOneWidget);
        expect(find.text('Gõ phím'), findsOneWidget);
        expect(find.byTooltip('Nhập bằng bàn phím'), findsOneWidget);
        expect(tester.takeException(), isNull);
      } finally {
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pumpAndSettle();
        task.dispose();
      }
    });

    testWidgets(
        'AccountScreen renders hero banner, grouped tiles and primary CTA (dark=$isDark)',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      MelbelColors.isDark = isDark;
      addTearDown(() => MelbelColors.isDark = false);
      FlutterSecureStorage.setMockInitialValues({});

      final auth = AuthService();
      final theme = ThemeProvider();
      final focus = FocusProvider();
      final task = TaskProvider();

      try {
        await tester.pumpWidget(MultiProvider(
          providers: [
            ChangeNotifierProvider<AuthService>.value(value: auth),
            ChangeNotifierProvider<ThemeProvider>.value(value: theme),
            ChangeNotifierProvider<FocusProvider>.value(value: focus),
            ChangeNotifierProvider<TaskProvider>.value(value: task),
          ],
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: const AccountScreen(),
          ),
        ));
        await tester.pump(const Duration(milliseconds: 500));

        expect(find.byType(MelbelPageBackdrop), findsOneWidget);
        expect(find.text('Tài khoản của bạn'), findsOneWidget);
        expect(
          find.widgetWithText(PrimaryCta, 'Mở thiết lập ban đầu'),
          findsOneWidget,
        );
        expect(tester.takeException(), isNull);
      } finally {
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump(const Duration(milliseconds: 200));
        auth.dispose();
        theme.dispose();
        focus.dispose();
        task.dispose();
      }
    });
  }

  testWidgets(
      'MelbelHeroBanner omits message cleanly when null and reveals whisper when petted',
      (tester) async {
    String? whisper;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: Scaffold(
        body: StatefulBuilder(
          builder: (context, setState) => MelbelHeroBanner(
            badge: '26/11/2026',
            title: 'Chào buổi tối',
            message: whisper,
            onPetted: (value) => setState(() => whisper = value),
          ),
        ),
      ),
    ));

    expect(find.text('Chào buổi tối'), findsOneWidget);
    expect(find.text('Hôm nay đi từng bước nhỏ nhé.'), findsNothing);

    await tester.tap(find.byType(MelbelMascot));
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();

    expect(find.text(MelbelMascot.companionWhispers.first), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'FocusInfoCard and ReviewHeader render cleanly when subtitle is null',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: const Scaffold(
        body: Column(
          children: [
            ReviewHeader('Hộp suy nghĩ', null),
            SizedBox(height: 12),
            FocusInfoCard(
              icon: Icons.schedule_outlined,
              title: 'Nhắc chuẩn bị lúc 20:45',
            ),
          ],
        ),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Hộp suy nghĩ'), findsOneWidget);
    expect(find.text('Nhắc chuẩn bị lúc 20:45'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'ThoughtInboxScreen quick-captures thought without mutating FocusStage',
      (tester) async {
    FlutterSecureStorage.setMockInitialValues({});
    final focus = FocusProvider();
    final task = TaskProvider();
    final stageBefore = focus.stage;
    try {
      await tester.pumpWidget(
        MultiProvider(
          providers: [
            ChangeNotifierProvider<FocusProvider>.value(value: focus),
            ChangeNotifierProvider<TaskProvider>.value(value: task),
          ],
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: const ThoughtInboxScreen(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Hộp suy nghĩ'), findsOneWidget);
      await tester.enterText(
        find.byWidgetPredicate(
          (w) =>
              w is TextField &&
              w.decoration?.hintText == 'Ghi nhanh ý chợt nảy ra...',
        ),
        'Nhớ mua nước uống',
      );
      await tester.tap(find.byTooltip('Cất suy nghĩ'));
      await tester.pumpAndSettle();

      expect(focus.thoughts.map((t) => t.text), contains('Nhớ mua nước uống'));
      expect(focus.stage, stageBefore);
      expect(focus.stage, isNot(FocusStage.thoughtSaved));
      expect(find.text('Nhớ mua nước uống'), findsOneWidget);
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump(const Duration(milliseconds: 200));
      focus.dispose();
      task.dispose();
    }
  });

  testWidgets(
      'TaskSummaryScreen deduplicates field blockers and validates empty submit locally',
      (tester) async {
    tester.view.physicalSize = const Size(900, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    FlutterSecureStorage.setMockInitialValues({});
    final task = _LocalValidationSummaryTask(
      const PlanningDraftModel(
        draftId: 'draft-val',
        stage: 'summary',
        summary: <String, dynamic>{
          'title': 'Soạn đề cương',
          'scope': '3 chương',
          'can_plan': false,
          'clarification_issues': <Map<String, dynamic>>[
            <String, dynamic>{
              'code': 'MISSING_AVAILABILITY',
              'field_path': 'availability',
              'blocking': true,
              'question': 'Bạn rảnh vào khung giờ nào?',
            },
            <String, dynamic>{
              'code': 'AI_AVAILABILITY_INCOMPLETE',
              'field_path': 'availability_ref',
              'blocking': true,
              'question': 'Bạn có thể làm việc khung giờ nào trong tuần?',
            },
          ],
        },
        summaryRevision: 1,
        planningInputRevision: 1,
        inputSource: 'text',
      ),
    );
    try {
      await tester.pumpWidget(
        ChangeNotifierProvider<TaskProvider>.value(
          value: task,
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: const TaskSummaryScreen(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Bạn rảnh vào khung giờ nào?'), findsNothing);
      expect(
        find.text('Bạn có thể làm việc khung giờ nào trong tuần?'),
        findsOneWidget,
      );
      expect(find.text('Tối nay 20:00–22:00'), findsOneWidget);

      await tester.ensureVisible(
        find.widgetWithText(PrimaryCta, 'Bổ sung thông tin'),
      );
      await tester.tap(find.widgetWithText(PrimaryCta, 'Bổ sung thông tin'));
      await tester.pumpAndSettle();

      expect(task.clarificationCalls, 0);
      expect(
        find.text('Nhập ngắn gọn hoặc chọn 1 gợi ý ở trên nhé.'),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump(const Duration(milliseconds: 200));
      task.dispose();
    }
  });

  testWidgets(
      'SettingsScreen exposes Home Streak visibility switch and showMelbelUndoSnackBar sets close icon',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
    final theme = ThemeProvider();
    final streak = StreakProvider(ownerId: 'settings_streak_toggle');
    await streak.restore();
    final offlineClient = _OfflineCalendarClient();
    try {
      await tester.pumpWidget(
        MultiProvider(
          providers: [
            ChangeNotifierProvider<ThemeProvider>.value(value: theme),
            ChangeNotifierProvider<StreakProvider>.value(value: streak),
          ],
          child: MaterialApp(
            theme: MelbelTheme.currentTheme,
            home: SettingsScreen(
              api: offlineClient,
              adaptation: AdaptationApi(offlineClient),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final switchFinder = find.text('Thẻ nhịp tập trung ở Trang chủ');
      expect(switchFinder, findsOneWidget);
      expect(streak.introDismissed, isFalse);

      await tester.ensureVisible(switchFinder);
      await tester.tap(switchFinder);
      await tester.pumpAndSettle();
      expect(streak.introDismissed, isTrue);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (ctx) => TextButton(
                onPressed: () => showMelbelUndoSnackBar(
                  ctx,
                  message: 'Đã ẩn',
                  onUndo: () {},
                ),
                child: const Text('Trigger'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Trigger'));
      await tester.pumpAndSettle();

      final bar = tester.widget<SnackBar>(find.byType(SnackBar));
      expect(bar.showCloseIcon, isTrue);
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump(const Duration(milliseconds: 200));
      theme.dispose();
      streak.dispose();
    }
  });

  test('Only black Melbel SVG assets exist (white cat_dark variants removed)',
      () {
    for (final expr in ['happy', 'neutral', 'sleepy']) {
      expect(
        File('assets/melbel_svg/cat_dark_$expr.svg').existsSync(),
        isFalse,
      );
      expect(
        File('assets/melbel_svg/cat_light_$expr.svg').existsSync(),
        isTrue,
      );
    }
  });

  testWidgets(
      'MelbelMascot always renders black cat SVG with theme-aware contrast backdrop',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: const Scaffold(
          body: Center(
            child: MelbelMascot(),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('black_neutral')), findsOneWidget);
    expect(find.byKey(const ValueKey('dark_neutral')), findsNothing);
    final darkBackdrop = tester.widget<Container>(
      find.byKey(const ValueKey('melbel_mascot_backdrop')),
    );
    final darkDeco = darkBackdrop.decoration! as BoxDecoration;
    expect(darkDeco.shape, BoxShape.circle);
    expect(darkDeco.color, MelbelMascot.darkBackdropColor);

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: const Scaffold(
          body: Center(
            child: MelbelMascot(showBackdrop: false),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('black_neutral')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('melbel_mascot_backdrop')),
      findsNothing,
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.light(),
        home: const Scaffold(
          body: Center(
            child: MelbelMascot(),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('black_neutral')), findsOneWidget);
    final lightBackdrop = tester.widget<Container>(
      find.byKey(const ValueKey('melbel_mascot_backdrop')),
    );
    final lightDeco = lightBackdrop.decoration! as BoxDecoration;
    expect(lightDeco.shape, BoxShape.circle);
    expect(lightDeco.color, MelbelMascot.lightBackdropColor);
    expect(tester.takeException(), isNull);
  });
}
