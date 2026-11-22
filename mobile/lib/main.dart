import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'core/api_session.dart';
import 'core/api_client.dart';
import 'core/responsive_layout.dart';
import 'core/theme.dart';
import 'models/focus_session_model.dart';
import 'repositories/local_run_repository.dart';
import 'services/auth_service.dart';
import 'services/schedule_service.dart';
import 'providers/focus_provider.dart';
import 'providers/streak_provider.dart';
import 'providers/task_provider.dart';
import 'providers/theme_provider.dart';
import 'screens/onboarding_screens.dart';
import 'screens/splash_screen.dart';
import 'screens/support_screens.dart';
import 'screens/home_screen.dart';
import 'screens/calendar_screen.dart';
import 'screens/google_calendar_screen.dart';
import 'screens/calendar_connect_screen.dart';
import 'screens/focus_screen.dart';
import 'widgets/melbel_ui.dart';
import 'screens/settings_screen.dart';
import 'screens/focus_review_screens.dart';
import 'services/alarm_service.dart';
import 'services/focus_native_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  LicenseRegistry.addLicense(() async* {
    final license = await rootBundle.loadString('assets/fonts/OFL.txt');
    yield LicenseEntryWithLineBreaks(const ['Be Vietnam Pro'], license);
  });
  if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
    await AlarmService.instance.init();
    await FocusNativeController.instance.init();
  }
  final themeProvider = ThemeProvider();
  await themeProvider.restore();
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: themeProvider),
        ChangeNotifierProvider(create: (_) => AuthService()..restore()),
      ],
      child: const _AuthGate(),
    ),
  );
}

class _AuthGate extends StatefulWidget {
  const _AuthGate();

  @override
  State<_AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<_AuthGate> {
  bool _splashDone = false;

  @override
  Widget build(BuildContext context) {
    context.watch<ThemeProvider>();
    final auth = context.watch<AuthService>();
    final session = auth.session;
    final Widget child;
    if (!_splashDone || auth.loading) {
      child = MaterialApp(
        key: const ValueKey('splash'),
        debugShowCheckedModeBanner: false,
        title: 'ProPlan VA',
        theme: MelbelTheme.currentTheme,
        themeAnimationDuration: Duration.zero,
        home: MelbelSplashScreen(
          canSkip: !auth.loading,
          onComplete: () {
            if (mounted && !_splashDone) {
              setState(() => _splashDone = true);
            }
          },
        ),
      );
    } else {
      child = session == null
          ? const ProPlanApp(key: ValueKey('login'), signedIn: false)
          : _OwnerApp(
              key: ObjectKey(session),
              session: session,
              onboardingRequired: auth.onboardingRequired,
            );
    }

    return AnimatedSwitcher(
      duration: (MediaQuery.maybeOf(context)?.disableAnimations ?? false)
          ? Duration.zero
          : const Duration(milliseconds: 280),
      switchInCurve: Curves.easeOutCubic,
      switchOutCurve: Curves.easeInCubic,
      child: child,
    );
  }
}

class _OwnerApp extends StatefulWidget {
  const _OwnerApp({
    super.key,
    required this.session,
    required this.onboardingRequired,
  });
  final ApiSession session;
  final bool onboardingRequired;

  @override
  State<_OwnerApp> createState() => _OwnerAppState();
}

class _OwnerAppState extends State<_OwnerApp> {
  late final StreakProvider _streakProvider =
      StreakProvider(ownerId: widget.session.ownerId);
  late Future<FocusProvider> _focusProvider;
  bool _restoreStarted = false;
  FocusProvider? _initializedFocus;

  @override
  void initState() {
    super.initState();
    unawaited(_streakProvider.restore());
    _focusProvider = _createFocusProvider();
  }

  Future<FocusProvider> _createFocusProvider() async {
    void onFocusRecorded(int seconds) {
      unawaited(_streakProvider.recordFocusActivity(seconds: seconds));
    }

    if (kIsWeb) {
      return FocusProvider(
        ownerId: widget.session.ownerId,
        api: ApiClient(),
        repository: SecureStorageRunRepository(
          runKey: 'proplan.uf3.active_run.v1.${widget.session.ownerId}',
          thoughtsKey: 'proplan.uf4.local_thoughts.v1.${widget.session.ownerId}',
        ),
        onFocusRecorded: onFocusRecorded,
      );
    }
    return FocusProvider.createOfflineAware(
      ownerId: widget.session.ownerId,
      apiClient: ApiClient(),
      onFocusRecorded: onFocusRecorded,
    );
  }

  @override
  void dispose() {
    _streakProvider.dispose();
    _initializedFocus?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<FocusProvider>(
        future: _focusProvider,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return MaterialApp(
              home: Scaffold(
                body: Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Text('Chưa mở được dữ liệu ngoại tuyến.'),
                      const SizedBox(height: 12),
                      FilledButton(
                        onPressed: () => setState(() {
                          _restoreStarted = false;
                          _focusProvider = _createFocusProvider();
                        }),
                        child: const Text('Thử lại'),
                      ),
                    ],
                  ),
                ),
              ),
            );
          }
          if (!snapshot.hasData) {
            return MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: MelbelTheme.themeFor(isDark: MelbelColors.isDark),
              highContrastTheme: MelbelTheme.themeFor(
                isDark: MelbelColors.isDark,
                highContrast: true,
              ),
              home: Scaffold(
                backgroundColor: MelbelColors.kBackground,
                body: Center(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 32),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const MelbelMascot(
                          width: 72,
                          height: 60,
                          interactive: false,
                        ),
                        const SizedBox(height: 14),
                        Text(
                          'Đang chuẩn bị không gian của bạn...',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                            color: MelbelColors.kOnSurface,
                          ),
                        ),
                        const SizedBox(height: 14),
                        const SizedBox(
                          width: 140,
                          child: ClipRRect(
                            borderRadius: BorderRadius.all(Radius.circular(99)),
                            child: LinearProgressIndicator(minHeight: 4),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            );
          }
          final focus = snapshot.data!;
          _initializedFocus = focus;
          if (!_restoreStarted) {
            _restoreStarted = true;
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (mounted) unawaited(focus.restore());
            });
          }
          return MultiProvider(
            providers: [
              ChangeNotifierProvider(
                  lazy: false,
                  create: (_) {
                    final task = TaskProvider(ownerId: widget.session.ownerId);
                    unawaited(task.restoreCurrentDraft());
                    return task;
                  }),
              ChangeNotifierProvider<FocusProvider>.value(value: focus),
              ChangeNotifierProvider<StreakProvider>.value(
                  value: _streakProvider),
            ],
            child: ProPlanApp(
              signedIn: true,
              onboardingRequired: widget.onboardingRequired,
            ),
          );
        },
      );
}

class ProPlanApp extends StatefulWidget {
  const ProPlanApp({
    super.key,
    required this.signedIn,
    this.onboardingRequired = false,
  });

  final bool signedIn;
  final bool onboardingRequired;

  @override
  State<ProPlanApp> createState() => _ProPlanAppState();
}

class _ProPlanAppState extends State<ProPlanApp> {
  final _navigatorKey = GlobalKey<NavigatorState>();
  bool _reauthAccountOpen = false;
  StreamSubscription<String>? _notificationTapSub;
  bool _handlingNotificationNav = false;

  @override
  void initState() {
    super.initState();
    if (widget.signedIn &&
        !kIsWeb &&
        defaultTargetPlatform == TargetPlatform.android) {
      _notificationTapSub = AlarmService.instance.notificationTaps.listen(
        _handleNotificationPayload,
      );
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        final payload = await AlarmService.instance.launchPayload();
        if (mounted) await _handleNotificationPayload(payload);
      });
    }
  }

  @override
  void dispose() {
    unawaited(_notificationTapSub?.cancel());
    super.dispose();
  }

  Future<void> _handleNotificationPayload(String? payload) async {
    if (!mounted || payload == null || _handlingNotificationNav) return;
    if (!payload.startsWith(AlarmService.preSessionPayloadPrefix)) return;
    final sessionId =
        payload.substring(AlarmService.preSessionPayloadPrefix.length).trim();
    if (sessionId.isEmpty) return;
    final nav = _navigatorKey.currentState;
    final navContext = _navigatorKey.currentContext;
    if (nav == null || navContext == null) return;
    _handlingNotificationNav = true;
    try {
      final focus = navContext.read<FocusProvider>();
      if (!focus.isActiveRun && !focus.hasUnfinishedServerRun) {
        final now = DateTime.now();
        final rows = await ScheduleService(ApiClient()).loadSessions(
          DateTime(now.year, now.month, now.day)
              .subtract(const Duration(days: 1)),
          now.add(const Duration(days: 14)),
          includeTitles: true,
        );
        Map<String, dynamic>? matched;
        for (final row in rows) {
          if (row['session_id'] == sessionId) {
            matched = row;
            break;
          }
        }
        if (matched == null || !mounted) return;
        await focus.startSession(FocusSessionInfo.fromJson(matched));
      }
      if (!mounted) return;
      await nav.push(
        MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
      );
    } catch (_) {
      // Giữ nguyên màn hiện tại nếu mạng lỗi hoặc phiên không còn.
    } finally {
      _handlingNotificationNav = false;
    }
  }

  Future<void> _openAccount() async {
    final navigator = _navigatorKey.currentState;
    if (navigator == null || _reauthAccountOpen) return;
    _reauthAccountOpen = true;
    try {
      await navigator.pushNamed('/account');
    } finally {
      _reauthAccountOpen = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    context.watch<ThemeProvider>();
    final signedIn = widget.signedIn;
    final needsSignIn =
        signedIn && (context.watch<AuthService?>()?.needsSignIn ?? false);
    return MaterialApp(
      navigatorKey: _navigatorKey,
      title: 'ProPlan VA',
      locale: const Locale('vi'),
      supportedLocales: const [Locale('vi')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      debugShowCheckedModeBanner: false,
      theme: MelbelTheme.themeFor(isDark: MelbelColors.isDark),
      highContrastTheme: MelbelTheme.themeFor(
        isDark: MelbelColors.isDark,
        highContrast: true,
      ),
      themeAnimationDuration: Duration.zero,
      routes: signedIn
          ? {
              '/login': (_) => const HomeScreen(),
              '/home': (_) => const HomeScreen(),
              '/calendar': (_) => const CalendarScreen(),
              '/google-calendar': (_) =>
                  !kIsWeb && defaultTargetPlatform == TargetPlatform.android
                      ? const CalendarConnectScreen()
                      : const GoogleCalendarScreen(),
              '/settings': (_) => const SettingsScreen(),
              '/thoughts': (_) => const ThoughtInboxScreen(),
              '/schedule-change': (_) => const ScheduleComparisonScreen(
                    reason: 'Đề xuất chờ duyệt',
                    trigger: 'resume',
                  ),
              '/help': (_) => const HelpScreen(),
              '/account': (_) => const AccountScreen(),
            }
          : const {},
      onGenerateRoute: signedIn
          ? null
          : (_) => MaterialPageRoute<void>(
                builder: (_) => const LoginScreen(),
              ),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(alwaysUse24HourFormat: true),
        child: AnnotatedRegion<SystemUiOverlayStyle>(
          value: SystemUiOverlayStyle(
            statusBarColor: Colors.transparent,
            statusBarIconBrightness:
                MelbelColors.isDark ? Brightness.light : Brightness.dark,
            systemNavigationBarColor: MelbelColors.kSurface,
            systemNavigationBarIconBrightness:
                MelbelColors.isDark ? Brightness.light : Brightness.dark,
            systemNavigationBarDividerColor: MelbelColors.kOutline,
          ),
          child: LayoutBuilder(
            builder: (context, constraints) {
              final isWide =
                  constraints.maxWidth > MelbelLayout.maxContentWidth + 16;
              final frameBackdrop = MelbelColors.isDark
                  ? const Color(0xFF0F0C15)
                  : const Color(0xFFE9E2D4);
              final shell = Column(
                children: [
                  Expanded(child: child ?? const SizedBox.shrink()),
                  if (needsSignIn)
                    Material(
                      color: MelbelColors.kSurface,
                      child: SafeArea(
                        top: false,
                        child: ListTile(
                          leading: const Icon(Icons.info_outline),
                          title: const Text('Phiên đăng nhập đã hết hạn'),
                          trailing: TextButton(
                            onPressed: _openAccount,
                            child: const Text('Đăng nhập lại'),
                          ),
                        ),
                      ),
                    ),
                ],
              );

              if (!isWide) {
                return ColoredBox(color: MelbelColors.kBackground, child: shell);
              }

              return ColoredBox(
                color: frameBackdrop,
                child: Center(
                  child: Container(
                    width: MelbelLayout.maxContentWidth,
                    decoration: BoxDecoration(
                      color: MelbelColors.kBackground,
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(
                            alpha: MelbelColors.isDark ? 0.42 : 0.08,
                          ),
                          blurRadius: 34,
                        ),
                      ],
                    ),
                    clipBehavior: Clip.antiAlias,
                    child: shell,
                  ),
                ),
              );
            },
          ),
        ),
      ),
      home: signedIn
          ? widget.onboardingRequired
              ? const SetupFlowScreen()
              : const HomeScreen()
          : const LoginScreen(),
    );
  }
}
