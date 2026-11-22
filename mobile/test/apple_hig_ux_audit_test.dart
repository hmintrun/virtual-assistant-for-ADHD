import 'dart:math' as math;

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_outbox_repository.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:proplan_va/repositories/local_thought_repository.dart';
import 'package:proplan_va/screens/focus_screen.dart';
import 'package:proplan_va/services/timer_service.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';

class _FixedClock implements Clock {
  _FixedClock(this.value);
  final DateTime value;

  @override
  DateTime now() => value;
}

double _linearizeChannel(double c) =>
    c <= 0.03928 ? c / 12.92 : math.pow((c + 0.055) / 1.055, 2.4).toDouble();

double _relativeLuminance(Color color) =>
    0.2126 * _linearizeChannel(color.r) +
    0.7152 * _linearizeChannel(color.g) +
    0.0722 * _linearizeChannel(color.b);

double _contrastRatio(Color fg, Color bg) {
  final l1 = _relativeLuminance(fg);
  final l2 = _relativeLuminance(bg);
  final bright = math.max(l1, l2);
  final dark = math.min(l1, l2);
  return (bright + 0.05) / (dark + 0.05);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiSession.current = ApiSession('user_hig_audit', token: 'test_token');
  });

  test('All MelbelPalette variants satisfy WCAG AA foreground contrast floors',
      () {
    final palettes = <String, MelbelPalette>{
      'light': MelbelPalette.light,
      'dark': MelbelPalette.dark,
      'lightHighContrast': MelbelPalette.lightHighContrast,
      'darkHighContrast': MelbelPalette.darkHighContrast,
    };

    for (final entry in palettes.entries) {
      final name = entry.key;
      final p = entry.value;

      expect(_contrastRatio(p.onSurface, p.background), greaterThanOrEqualTo(4.5),
          reason: '$name: onSurface on background');
      expect(_contrastRatio(p.onSurface, p.surface), greaterThanOrEqualTo(4.5),
          reason: '$name: onSurface on surface');
      expect(
          _contrastRatio(p.onSurfaceVariant, p.background), greaterThanOrEqualTo(4.5),
          reason: '$name: onSurfaceVariant on background');
      expect(_contrastRatio(p.onSurfaceVariant, p.surface), greaterThanOrEqualTo(4.5),
          reason: '$name: onSurfaceVariant on surface');
      expect(
          _contrastRatio(p.onSurfaceVariant, p.mutedSurface),
          greaterThanOrEqualTo(4.5),
          reason: '$name: onSurfaceVariant on mutedSurface');

      expect(_contrastRatio(p.onSage, p.sage), greaterThanOrEqualTo(4.5),
          reason: '$name: onSage on sage');
      expect(_contrastRatio(MelbelColors.kOnBrandHoney, p.amber),
          greaterThanOrEqualTo(7.0),
          reason: '$name: kOnBrandHoney on amber');
      expect(_contrastRatio(MelbelColors.kOnError, p.softError),
          greaterThanOrEqualTo(4.5),
          reason: '$name: kOnError on softError');

      for (final bg in [p.background, p.surface, p.sageSoft]) {
        expect(_contrastRatio(p.accentInk, bg), greaterThanOrEqualTo(4.5),
            reason: '$name: accentInk vs $bg');
      }
      for (final bg in [p.background, p.surface, p.amberSoft]) {
        expect(_contrastRatio(p.amberInk, bg), greaterThanOrEqualTo(4.5),
            reason: '$name: amberInk vs $bg');
      }
      for (final bg in [p.background, p.surface, p.softErrorSoft]) {
        expect(_contrastRatio(p.errorInk, bg), greaterThanOrEqualTo(4.5),
            reason: '$name: errorInk vs $bg');
      }

      expect(_contrastRatio(p.onSageSoft, p.sageSoft), greaterThanOrEqualTo(4.5),
          reason: '$name: onSageSoft on sageSoft');
      expect(
          _contrastRatio(p.onAmberSoft, p.amberSoft), greaterThanOrEqualTo(4.5),
          reason: '$name: onAmberSoft on amberSoft');
      expect(_contrastRatio(p.onSoftErrorSoft, p.softErrorSoft),
          greaterThanOrEqualTo(4.5),
          reason: '$name: onSoftErrorSoft on softErrorSoft');

      expect(_contrastRatio(p.controlOutline, p.surface),
          greaterThanOrEqualTo(3.0),
          reason: '$name: controlOutline on surface');
    }
  });

  testWidgets(
      'MelbelFloatingNavBar distinguishes active tab icon and keeps >= 48dp hit target',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: Scaffold(
        bottomNavigationBar: MelbelFloatingNavBar(
          selectedIndex: 0,
          onSelected: (_) {},
        ),
      ),
    ));

    expect(find.byIcon(Icons.today_rounded), findsOneWidget);
    expect(find.byIcon(Icons.today_outlined), findsNothing);
    expect(find.byIcon(Icons.calendar_month_outlined), findsOneWidget);
    expect(find.byIcon(Icons.add_circle_outline_rounded), findsOneWidget);
    expect(find.byIcon(Icons.tune_outlined), findsOneWidget);

    for (final inkWell in tester.widgetList<InkWell>(find.byType(InkWell))) {
      final size = tester.getSize(find.byWidget(inkWell));
      expect(size.height, greaterThanOrEqualTo(48));
    }
  });

  testWidgets('High-Contrast MediaQuery adds crisp border to MelbelCard',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: const MediaQuery(
        data: MediaQueryData(highContrast: true),
        child: Scaffold(
          body: MelbelCard(
            child: Text('Thẻ kiểm tra tương phản cao'),
          ),
        ),
      ),
    ));

    final cardMaterial = tester.widget<Material>(
      find.descendant(
        of: find.byType(MelbelCard),
        matching: find.byType(Material),
      ),
    );
    final shape = cardMaterial.shape as RoundedRectangleBorder;
    expect(shape.side.width, greaterThanOrEqualTo(1.5));
  });

  testWidgets(
      'FocusScreen actual-correction bottom sheet uses presets, live validation, and zero AlertDialog',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

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
              'actual_work_seconds': 900,
              'actual_source': 'self_report',
              'work_elapsed_seconds': 1200,
            }
          },
        ));
      }));

    final clock = _FixedClock(DateTime.utc(2026, 11, 22, 8, 30, 0));
    final runRepo = InMemoryRunRepository();
    await tester.runAsync(() async {
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
    });

    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    final focus = FocusProvider(
      ownerId: 'user_hig_audit',
      clock: clock,
      api: ApiClient(dio: dio, bindSession: false),
      thoughtRepository: InMemoryThoughtRepository(),
      outboxRepository: InMemoryOutboxRepository(),
      nativeRecoveryChecker: NativeRecoveryChecker.fromCallback(() async => null),
      repository: runRepo,
    );

    try {
      await tester.runAsync(() => focus.restore());
      await tester.pumpWidget(ChangeNotifierProvider<FocusProvider>.value(
        value: focus,
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: false),
          home: const FocusScreen(),
        ),
      ));
      await tester.pumpAndSettle();

      final trigger = find.text('Sửa thời gian thực tế');
      await tester.ensureVisible(trigger);
      await tester.tap(trigger);
      await tester.pumpAndSettle();

      expect(find.byType(AlertDialog), findsNothing);
      expect(find.text('-5p'), findsOneWidget);
      expect(find.text('Quên bấm kết thúc'), findsOneWidget);

      ElevatedButton saveButton() => tester.widget<ElevatedButton>(
            find.ancestor(
              of: find.text('Lưu điều chỉnh'),
              matching: find.byType(ElevatedButton),
            ),
          );

      expect(saveButton().onPressed, isNull);

      await tester.tap(find.text('-5p'));
      await tester.pump();
      await tester.ensureVisible(find.text('Quên bấm kết thúc'));
      await tester.tap(find.text('Quên bấm kết thúc'));
      await tester.pumpAndSettle();

      expect(find.text('15'), findsOneWidget);
      expect(saveButton().onPressed, isNotNull);

      await tester.ensureVisible(find.text('Lưu điều chỉnh'));
      await tester.tap(find.text('Lưu điều chỉnh'));
      await tester.pumpAndSettle();

      expect(
        requests.any((r) => r.path == '/runs/run-123/actual-corrections'),
        isTrue,
      );
      expect(focus.actualWorkSeconds, 900);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      focus.dispose();
      debugDefaultTargetPlatformOverride = null;
    }
  });

  for (final isDark in [false, true]) {
    testWidgets(
        'MelbelCatEarAvatar renders cat-ear frame and clean fallback (dark=$isDark)',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: MelbelTheme.themeFor(isDark: isDark),
        home: const Scaffold(
          body: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              MelbelCatEarAvatar(displayName: 'Tân Nguyễn'),
              SizedBox(width: 16),
              MelbelCatEarAvatar(),
            ],
          ),
        ),
      ));

      expect(find.byType(MelbelCatEarAvatar), findsNWidgets(2));
      expect(
        find.descendant(
          of: find.byType(MelbelCatEarAvatar),
          matching: find.byType(CustomPaint),
        ),
        findsNWidgets(2),
      );
      expect(find.text('T'), findsOneWidget);
      expect(find.byIcon(Icons.person_rounded), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'showMelbelTimePicker renders Melbel alarm clock, validates 24h live, and returns TimeOfDay',
      (tester) async {
    TimeOfDay? picked;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: ElevatedButton(
              onPressed: () async {
                picked = await showMelbelTimePicker(
                  context: context,
                  helpText: 'Giờ bắt đầu',
                  initialTime: const TimeOfDay(hour: 18, minute: 0),
                );
              },
              child: const Text('Mở đồng hồ'),
            ),
          ),
        ),
      ),
    ));

    await tester.tap(find.text('Mở đồng hồ'));
    await tester.pumpAndSettle();

    expect(find.text('Giờ bắt đầu'), findsOneWidget);
    expect(find.byType(MelbelMascot), findsOneWidget);
    expect(find.text('18'), findsOneWidget);
    expect(find.text('00'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, '25');
    await tester.pumpAndSettle();

    expect(find.text('Nhập thời gian hợp lệ'), findsOneWidget);
    final disabledOk = tester.widget<ElevatedButton>(
      find.ancestor(
        of: find.text('OK'),
        matching: find.byType(ElevatedButton),
      ),
    );
    expect(disabledOk.onPressed, isNull);

    await tester.enterText(find.byType(TextField).first, '09');
    await tester.enterText(find.byType(TextField).last, '30');
    await tester.pumpAndSettle();

    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(picked, const TimeOfDay(hour: 9, minute: 30));
  });

  testWidgets(
      'MelbelFlowPageTransitionsBuilder respects disableAnimations and MelbelFloatingNavBar glides smoothly',
      (tester) async {
    const childWidget = SizedBox(key: ValueKey('flow_child'));
    Widget? builtReduced;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: Builder(
          builder: (context) {
            const builder = MelbelFlowPageTransitionsBuilder();
            builtReduced = builder.buildTransitions<void>(
              MaterialPageRoute<void>(builder: (_) => childWidget),
              context,
              const AlwaysStoppedAnimation<double>(1),
              const AlwaysStoppedAnimation<double>(0),
              childWidget,
            );
            return builtReduced!;
          },
        ),
      ),
    ));
    expect(identical(builtReduced, childWidget), isTrue);

    var selected = 0;
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: StatefulBuilder(
        builder: (context, setState) => Scaffold(
          bottomNavigationBar: MelbelFloatingNavBar(
            selectedIndex: selected,
            onSelected: (next) => setState(() => selected = next),
          ),
        ),
      ),
    ));

    await tester.tap(find.text('Thêm việc'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 260));
    expect(selected, 2);
    expect(find.byIcon(Icons.add_circle_rounded), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
