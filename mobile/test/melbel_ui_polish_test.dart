import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/providers/theme_provider.dart';
import 'package:proplan_va/screens/calendar_screen.dart';
import 'package:proplan_va/screens/splash_screen.dart';
import 'package:proplan_va/screens/support_screens.dart';
import 'package:proplan_va/screens/task_entry_screen.dart';
import 'package:proplan_va/services/auth_service.dart';
import 'package:proplan_va/widgets/countdown_timer_widget.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';

class _OfflineCalendarClient extends ApiClient {
  _OfflineCalendarClient() : super(bindSession: false);

  @override
  Future<Map<String, dynamic>> getData(String path,
      {Map<String, dynamic>? queryParameters}) async {
    throw const ApiException('Offline');
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

  test('Unified lavender and honey brand accents stay identical in light/dark',
      () {
    expect(MelbelPalette.light.sage, MelbelPalette.dark.sage);
    expect(MelbelPalette.light.onSage, MelbelPalette.dark.onSage);
    expect(MelbelPalette.light.amber, MelbelPalette.dark.amber);
    expect(MelbelPalette.light.softError, MelbelPalette.dark.softError);
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
      expect(find.text('Thêm việc cho ngày này'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets(
        'HelpScreen renders banners, doodle rays and pill CTA cleanly (dark=$isDark)',
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
      await tester.scrollUntilVisible(find.text('Quay lại'), 200);
      await tester.pumpAndSettle();
      expect(find.widgetWithText(SecondaryCta, 'Quay lại'), findsOneWidget);
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
        expect(find.text('Nhập bằng bàn phím'), findsOneWidget);
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
}
