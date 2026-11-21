import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'core/api_session.dart';
import 'core/api_client.dart';
import 'core/theme.dart';
import 'services/auth_service.dart';
import 'providers/focus_provider.dart';
import 'providers/task_provider.dart';
import 'providers/theme_provider.dart';
import 'screens/onboarding_screens.dart';
import 'screens/support_screens.dart';
import 'screens/home_screen.dart';
import 'screens/calendar_screen.dart';
import 'screens/google_calendar_screen.dart';
import 'screens/calendar_connect_screen.dart';
import 'screens/focus_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/focus_review_screens.dart';
import 'services/alarm_service.dart';
import 'services/focus_native_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
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

class _AuthGate extends StatelessWidget {
  const _AuthGate();

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthService>();
    if (auth.loading) {
      return const MaterialApp(
        home: Scaffold(body: Center(child: CircularProgressIndicator())),
      );
    }
    final session = auth.session;
    return session == null
        ? const ProPlanApp(key: ValueKey('login'), signedIn: false)
        : _OwnerApp(
            key: ObjectKey(session),
            session: session,
            onboardingRequired: auth.onboardingRequired,
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
  late Future<FocusProvider> _focusProvider;
  bool _restoreStarted = false;
  FocusProvider? _initializedFocus;

  @override
  void initState() {
    super.initState();
    _focusProvider = _createFocusProvider();
  }

  Future<FocusProvider> _createFocusProvider() =>
      FocusProvider.createOfflineAware(
        ownerId: widget.session.ownerId,
        apiClient: ApiClient(),
      );

  @override
  void dispose() {
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
            return const MaterialApp(
              home: Scaffold(
                body: Center(child: CircularProgressIndicator()),
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
      theme: MelbelTheme.currentTheme,
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
      builder: (context, child) => AnnotatedRegion<SystemUiOverlayStyle>(
        value: SystemUiOverlayStyle(
          statusBarColor: Colors.transparent,
          statusBarIconBrightness:
              MelbelColors.isDark ? Brightness.light : Brightness.dark,
          systemNavigationBarColor: MelbelColors.kSurface,
          systemNavigationBarIconBrightness:
              MelbelColors.isDark ? Brightness.light : Brightness.dark,
          systemNavigationBarDividerColor: MelbelColors.kOutline,
        ),
        child: ColoredBox(
          color: MelbelColors.kBackground,
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 430),
              child: Column(
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
              ),
            ),
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
