import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/providers/streak_provider.dart';
import 'package:proplan_va/screens/calendar_screen.dart';
import 'package:proplan_va/screens/support_screens.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';
import 'package:proplan_va/widgets/streak_widgets.dart';

class _EmptyScheduleClient extends ApiClient {
  _EmptyScheduleClient() : super(bindSession: false);

  @override
  Future<Map<String, dynamic>> getData(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async =>
      {'items': <Map<String, dynamic>>[]};
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('3-day unlock rule: 0 -> 1 -> 2 warmup, day 3 unlocks streak', () async {
    var clock = DateTime(2026, 11, 20, 9);
    final streak = StreakProvider(ownerId: 'u1', now: () => clock);
    await streak.restore();

    expect(streak.currentStreak, 0);
    expect(streak.isUnlocked, isFalse);

    final day1 = await streak.recordFocusActivity(seconds: 120);
    expect(day1, isNotNull);
    expect(day1!.streakDays, 1);
    expect(day1.justUnlocked, isFalse);
    expect(streak.isUnlocked, isFalse);
    expect(streak.longestStreak, 0);

    clock = DateTime(2026, 11, 21, 9);
    final day2 = await streak.recordFocusActivity(seconds: 300);
    expect(day2, isNotNull);
    expect(day2!.streakDays, 2);
    expect(day2.justUnlocked, isFalse);
    expect(streak.isUnlocked, isFalse);

    clock = DateTime(2026, 11, 22, 9);
    final day3 = await streak.recordFocusActivity(seconds: 1500);
    expect(day3, isNotNull);
    expect(day3!.streakDays, 3);
    expect(day3.justUnlocked, isTrue);
    expect(day3.evolved, isTrue);
    expect(day3.tier.level, 1);
    expect(streak.isUnlocked, isTrue);
    expect(streak.longestStreak, 3);
  });

  test('enforces >= 120s minimum and deduplicates same-day sessions', () async {
    final clock = DateTime(2026, 11, 22, 10);
    final streak = StreakProvider(ownerId: 'u2', now: () => clock);
    await streak.restore();

    expect(await streak.recordFocusActivity(seconds: 90), isNull);
    expect(streak.currentStreak, 0);
    expect(streak.todayDone, isFalse);

    final first = await streak.recordFocusActivity(seconds: 120);
    expect(first, isNotNull);
    expect(streak.currentStreak, 1);
    expect(streak.dayMinutes(clock), 2);

    final duplicate = await streak.recordFocusActivity(seconds: 1500);
    expect(duplicate, isNull);
    expect(streak.currentStreak, 1);
    expect(streak.dayMinutes(clock), 27);
  });

  test('warmup miss (< 3 days) resets cleanly without consuming Freeze shield',
      () async {
    var clock = DateTime(2026, 11, 18, 9);
    final streak = StreakProvider(ownerId: 'u3', now: () => clock);
    await streak.restore();

    await streak.recordFocusActivity(seconds: 180);
    clock = DateTime(2026, 11, 19, 9);
    await streak.recordFocusActivity(seconds: 180);
    expect(streak.currentStreak, 2);
    expect(streak.freezeCount, 1);

    // Miss Nov 20, return Nov 21.
    clock = DateTime(2026, 11, 21, 9);
    await streak.reconcileNow();
    expect(streak.currentStreak, 0);
    expect(streak.warmupMissedNotice, isTrue);
    expect(streak.freezeCount, 1);
    expect(streak.usedFreezeNotice, isFalse);
  });

  test('Tier 1 Auto-Freeze preserves >= 3 day streak across a 1-day gap',
      () async {
    var clock = DateTime(2026, 11, 18, 9);
    final streak = StreakProvider(ownerId: 'u4', now: () => clock);
    await streak.restore();

    for (var i = 0; i < 3; i++) {
      await streak.recordFocusActivity(seconds: 600);
      clock = clock.add(const Duration(days: 1));
    }
    // Now clock is Nov 21; miss Nov 21 and check on Nov 22.
    clock = DateTime(2026, 11, 22, 10);
    await streak.reconcileNow();

    expect(streak.currentStreak, 3);
    expect(streak.freezeCount, 0);
    expect(streak.usedFreezeNotice, isTrue);
    expect(streak.dayStatus(DateTime(2026, 11, 21)), StreakDayKind.freeze);

    final day4 = await streak.recordFocusActivity(seconds: 300);
    expect(day4, isNotNull);
    expect(day4!.streakDays, 4);
  });

  test('Tier 2 24h Repair recovers missed day when out of Freeze shields',
      () async {
    var clock = DateTime(2026, 11, 10, 9);
    final streak = StreakProvider(ownerId: 'u5', now: () => clock);
    await streak.restore();

    for (var i = 0; i < 4; i++) {
      await streak.recordFocusActivity(seconds: 600);
      clock = clock.add(const Duration(days: 1));
    }
    // Miss Nov 14 -> auto-uses initial Freeze shield.
    clock = DateTime(2026, 11, 15, 9);
    await streak.reconcileNow();
    expect(streak.freezeCount, 0);
    await streak.recordFocusActivity(seconds: 600);
    expect(streak.currentStreak, 5); // Wait: 5th day earns +1 freeze?
    // Notice day 5 earned +1 freeze; verify and advance cleanly to spend it.
    if (streak.freezeCount > 0) {
      clock = DateTime(2026, 11, 17, 9);
      await streak.reconcileNow();
      expect(streak.freezeCount, 0);
      await streak.recordFocusActivity(seconds: 600);
    }

    // Now freezeCount == 0 and currentStreak == 6. Miss Nov 18, open on Nov 19.
    final beforeGap = streak.currentStreak;
    clock = DateTime(2026, 11, 19, 9);
    await streak.reconcileNow();
    expect(streak.repairPending, isTrue);
    expect(streak.repairPreviousStreak, beforeGap);
    expect(streak.dayStatus(DateTime(2026, 11, 18)), StreakDayKind.repair);

    final repaired = await streak.recordFocusActivity(seconds: 120);
    expect(repaired, isNotNull);
    expect(repaired!.restoredFromRepair, isTrue);
    expect(repaired.streakDays, beforeGap + 1);
    expect(streak.repairPending, isFalse);
    expect(streak.repairsUsedThisMonth, 1);
  });

  test('Tier 3 Soft Reset keeps longestStreak and activeDaysInMonth', () async {
    var clock = DateTime(2026, 11, 10, 9);
    final streak = StreakProvider(ownerId: 'u6', now: () => clock);
    await streak.restore();

    for (var i = 0; i < 4; i++) {
      await streak.recordFocusActivity(seconds: 900);
      clock = clock.add(const Duration(days: 1));
    }
    expect(streak.longestStreak, 4);

    // Miss multiple days (Nov 14..16), return Nov 17.
    clock = DateTime(2026, 11, 17, 9);
    await streak.reconcileNow();
    expect(streak.currentStreak, 0);
    expect(streak.softResetNotice, isTrue);
    expect(streak.longestStreak, 4);
    expect(streak.activeDaysInMonth(DateTime(2026, 11)), 4);
  });

  test('calendar ribbon activates only for >= 3 contiguous done/freeze days',
      () async {
    var clock = DateTime(2026, 11, 2, 9);
    final streak = StreakProvider(ownerId: 'u7', now: () => clock);
    await streak.restore();

    // Isolated 2-day run: Nov 2, Nov 3
    await streak.recordFocusActivity(seconds: 300);
    clock = DateTime(2026, 11, 3, 9);
    await streak.recordFocusActivity(seconds: 300);
    expect(streak.isInUnlockedRibbon(DateTime(2026, 11, 2)), isFalse);
    expect(streak.isInUnlockedRibbon(DateTime(2026, 11, 3)), isFalse);

    // Reach day 3 on Nov 4 -> ribbon unlocks across Nov 2..4!
    clock = DateTime(2026, 11, 4, 9);
    await streak.recordFocusActivity(seconds: 300);
    expect(streak.isInUnlockedRibbon(DateTime(2026, 11, 2)), isTrue);
    expect(streak.hasRibbonPrev(DateTime(2026, 11, 2)), isFalse);
    expect(streak.hasRibbonNext(DateTime(2026, 11, 2)), isTrue);
    expect(streak.hasRibbonPrev(DateTime(2026, 11, 3)), isTrue);
    expect(streak.hasRibbonNext(DateTime(2026, 11, 3)), isTrue);
    expect(streak.hasRibbonPrev(DateTime(2026, 11, 4)), isTrue);
    expect(streak.hasRibbonNext(DateTime(2026, 11, 4)), isFalse);
  });

  test('persists streak state per ownerId across instances', () async {
    var clock = DateTime(2026, 11, 20, 9);
    final first = StreakProvider(ownerId: 'alice', now: () => clock);
    await first.restore();
    await first.recordFocusActivity(seconds: 600);
    clock = DateTime(2026, 11, 21, 9);
    await first.recordFocusActivity(seconds: 600);
    clock = DateTime(2026, 11, 22, 9);
    await first.recordFocusActivity(seconds: 600);

    final reloaded = StreakProvider(ownerId: 'alice', now: () => clock);
    await reloaded.restore();
    expect(reloaded.currentStreak, 3);
    expect(reloaded.isUnlocked, isTrue);

    final otherUser = StreakProvider(ownerId: 'bob', now: () => clock);
    await otherUser.restore();
    expect(otherUser.currentStreak, 0);
  });

  for (final isDark in [false, true]) {
    testWidgets(
        'MelbelStreakPill, StreakSheet, Celebration & CalendarScreen render cleanly (dark=$isDark)',
        (tester) async {
      final today = DateUtils.dateOnly(DateTime.now());
      var cursor = DateTime(today.year, today.month, today.day - 2);
      final streak = StreakProvider(ownerId: 'ui_$isDark', now: () => cursor);
      await streak.restore();
      await streak.recordFocusActivity(seconds: 600);
      cursor = DateTime(cursor.year, cursor.month, cursor.day + 1);
      await streak.recordFocusActivity(seconds: 600);
      cursor = today;
      final event = await streak.recordFocusActivity(seconds: 600);
      expect(event, isNotNull);

      await tester.pumpWidget(ChangeNotifierProvider<StreakProvider>.value(
        value: streak,
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: isDark),
          home: Builder(
            builder: (context) => Scaffold(
              appBar: MelbelAppBar(
                title: 'Hôm nay',
                showThemeToggle: true,
                showAccountAction: false,
                trailingAction: MelbelStreakPill(
                  streak: streak,
                  onTap: () => showMelbelStreakSheet(context, streak),
                ),
              ),
              body: Column(
                children: [
                  ElevatedButton(
                    onPressed: () =>
                        showMelbelStreakCelebration(context, event!),
                    child: const Text('Celebrate'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.text('3 ngày'), findsOneWidget);
      final pillSize = tester.getSize(find.byType(MelbelStreakPill));
      expect(pillSize.width, greaterThanOrEqualTo(48));
      expect(pillSize.height, greaterThanOrEqualTo(48));

      // Open streak detail sheet
      await tester.tap(find.byType(MelbelStreakPill));
      await tester.pumpAndSettle();
      expect(find.text('Chuỗi tập trung'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('Các chặng tiến hoá cùng Melbel'),
        220,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.pumpAndSettle();
      expect(find.text('Các chặng tiến hoá cùng Melbel'), findsOneWidget);
      await tester.tap(find.byTooltip('Đóng'));
      await tester.pumpAndSettle();

      // Open celebration sheet under disableAnimations
      await tester.pumpWidget(MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: isDark),
          home: Builder(
            builder: (context) => Scaffold(
              body: ElevatedButton(
                onPressed: () => showMelbelStreakCelebration(context, event!),
                child: const Text('Celebrate'),
              ),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('Celebrate'));
      await tester.pumpAndSettle();
      expect(find.text('Mở khóa Chuỗi tập trung!'), findsOneWidget);
      await tester.tap(find.text('Tiếp tục cùng Melbel'));
      await tester.pumpAndSettle();

      // CalendarScreen with StreakProvider renders paw cells & pill cleanly
      await tester.pumpWidget(ChangeNotifierProvider<StreakProvider>.value(
        value: streak,
        child: MaterialApp(
          theme: MelbelTheme.themeFor(isDark: isDark),
          home: CalendarScreen(client: _EmptyScheduleClient()),
        ),
      ));
      await tester.pumpAndSettle();
      expect(find.text('3 ngày'), findsOneWidget);
      expect(find.text('Khiên ngủ nướng'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('MelbelStreakIntroCard opens streak guide and dismissIntro persists',
      (tester) async {
    final streak = StreakProvider(ownerId: 'intro_card');
    await streak.restore();
    expect(streak.introDismissed, isFalse);

    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: Builder(
        builder: (context) => Scaffold(
          body: MelbelStreakIntroCard(
            streak: streak,
            onLearnMore: () => showMelbelStreakGuide(context, streak: streak),
            onDismiss: streak.dismissIntro,
          ),
        ),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Giữ 3 ngày liên tiếp để mở Streak'), findsOneWidget);
    await tester.tap(find.byTooltip('Xem giới thiệu'));
    await tester.pumpAndSettle();

    expect(find.text('Nhịp cùng Melbel (Streak)'), findsOneWidget);
    expect(find.text('3 ngày liên tiếp mở chuỗi (≥ 2 phút/ngày)'),
        findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('Đã hiểu · Bắt đầu giữ nhịp'),
      220,
      scrollable: find.byType(Scrollable).last,
    );
    await tester.tap(find.text('Đã hiểu · Bắt đầu giữ nhịp'));
    await tester.pumpAndSettle();

    expect(streak.introDismissed, isTrue);
    final reloaded = StreakProvider(ownerId: 'intro_card');
    await reloaded.restore();
    expect(reloaded.introDismissed, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('HelpScreen introduces Streak feature and opens guide sheet',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: const HelpScreen(),
    ));
    await tester.pumpAndSettle();

    expect(find.byType(MelbelSectionBanner), findsNWidgets(2));
    expect(find.text('Nhịp tập trung (Streak)'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Chuỗi tập trung (Streak) hoạt động thế nào?'),
      200,
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Chuỗi tập trung (Streak) hoạt động thế nào?'),
      findsOneWidget,
    );

    await tester.scrollUntilVisible(
      find.text('Nhịp tập trung (Streak)'),
      -200,
    );
    await tester.tap(find.text('Nhịp tập trung (Streak)'));
    await tester.pumpAndSettle();

    expect(find.text('Nhịp cùng Melbel (Streak)'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'showMelbelStreakSheet switch restores hidden Home Streak card and persists',
      (tester) async {
    final streak = StreakProvider(ownerId: 'restore_intro_sheet');
    await streak.restore();
    await streak.dismissIntro();
    expect(streak.introDismissed, isTrue);

    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.themeFor(isDark: false),
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: ElevatedButton(
              onPressed: () => showMelbelStreakSheet(context, streak),
              child: const Text('Mở sheet'),
            ),
          ),
        ),
      ),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Mở sheet'));
    await tester.pumpAndSettle();

    final toggleFinder = find.text('Hiện thẻ ở Trang chủ');
    await tester.scrollUntilVisible(
      toggleFinder,
      200,
      scrollable: find.byType(Scrollable).last,
    );
    await tester.pumpAndSettle();

    var tile = tester.widget<SwitchListTile>(
      find.ancestor(of: toggleFinder, matching: find.byType(SwitchListTile)),
    );
    expect(tile.value, isFalse);

    await tester.tap(toggleFinder);
    await tester.pumpAndSettle();

    expect(streak.introDismissed, isFalse);
    tile = tester.widget<SwitchListTile>(
      find.ancestor(of: toggleFinder, matching: find.byType(SwitchListTile)),
    );
    expect(tile.value, isTrue);

    final reloaded = StreakProvider(ownerId: 'restore_intro_sheet');
    await reloaded.restore();
    expect(reloaded.introDismissed, isFalse);
    expect(tester.takeException(), isNull);
  });

  for (final isDark in [false, true]) {
    testWidgets(
        'Streak sheet stays theme-synced and compact without overflow at 320x640 1.3x (dark=$isDark) and toggles tiers',
        (tester) async {
      tester.view.physicalSize = const Size(320, 640);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final streak = StreakProvider(ownerId: 'storybook_$isDark');
      await streak.restore();

      await tester.pumpWidget(
        MediaQuery(
          data: const MediaQueryData(
            size: Size(320, 640),
            textScaler: TextScaler.linear(1.3),
          ),
          child: MaterialApp(
            theme: MelbelTheme.themeFor(isDark: isDark),
            home: Builder(
              builder: (context) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => showMelbelStreakSheet(context, streak),
                    child: const Text('Mở Streak'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Mở Streak'));
      await tester.pumpAndSettle();

      expect(find.text('Chuỗi tập trung'), findsOneWidget);
      expect(find.byKey(const ValueKey('black_neutral')), findsWidgets);
      expect(find.byType(MelbelCard), findsAtLeastNWidgets(4));

      await tester.scrollUntilVisible(
        find.text('Khiên'),
        180,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.pumpAndSettle();
      expect(find.text('Khiên'), findsOneWidget);
      expect(find.text('Tháng này'), findsOneWidget);
      expect(find.text('Kỷ lục'), findsOneWidget);

      final tiersHeader = find.text('Các chặng tiến hoá cùng Melbel');
      await tester.scrollUntilVisible(
        tiersHeader,
        180,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.pumpAndSettle();
      expect(find.text('Lv.1 · Mở chuỗi · Vào guồng'), findsNothing);

      await tester.tap(tiersHeader);
      await tester.pumpAndSettle();

      await tester.scrollUntilVisible(
        find.text('Lv.1 · Mở chuỗi · Vào guồng'),
        140,
        scrollable: find.byType(Scrollable).last,
      );
      expect(find.text('Lv.1 · Mở chuỗi · Vào guồng'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
