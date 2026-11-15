import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'core/api_session.dart';
import 'core/theme.dart';
import 'repositories/local_run_repository.dart';
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
        : _OwnerApp(key: ObjectKey(session), session: session);
  }
}

class _OwnerApp extends StatelessWidget {
  const _OwnerApp({super.key, required this.session});
  final ApiSession session;

  @override
  Widget build(BuildContext context) => MultiProvider(
        providers: [
          ChangeNotifierProvider(
              lazy: false,
              create: (_) {
                final task = TaskProvider(ownerId: session.ownerId);
                unawaited(task.restoreCurrentDraft());
                return task;
              }),
          ChangeNotifierProvider(
              lazy: false,
              create: (_) {
                final focus = FocusProvider(
                    repository: SecureStorageRunRepository(
                  runKey: 'proplan.uf3.active_run.v1.${session.ownerId}',
                  thoughtsKey:
                      'proplan.uf4.local_thoughts.v1.${session.ownerId}',
                ));
                unawaited(focus.restore());
                return focus;
              }),
        ],
        child: const ProPlanApp(signedIn: true),
      );
}

class ProPlanApp extends StatelessWidget {
  const ProPlanApp({super.key, required this.signedIn});

  final bool signedIn;

  @override
  Widget build(BuildContext context) {
    context.watch<ThemeProvider>();
    return MaterialApp(
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
              '/schedule-change': (_) => const ScheduleChangeReasonScreen(),
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
              child: child ?? const SizedBox.shrink(),
            ),
          ),
        ),
      ),
      home: signedIn ? const HomeScreen() : const LoginScreen(),
    );
  }
}
