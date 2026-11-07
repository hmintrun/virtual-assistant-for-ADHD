import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'core/theme.dart';
import 'providers/focus_provider.dart';
import 'providers/task_provider.dart';
import 'providers/theme_provider.dart';
import 'screens/onboarding_screens.dart';
import 'screens/support_screens.dart';
import 'screens/home_screen.dart';
import 'services/alarm_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AlarmService.instance.init();
  final taskProvider = TaskProvider();
  final focusProvider = FocusProvider();
  final themeProvider = ThemeProvider();
  unawaited(taskProvider.restoreCurrentDraft());
  unawaited(focusProvider.restore());
  await themeProvider.restore();
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: taskProvider),
        ChangeNotifierProvider.value(value: focusProvider),
        ChangeNotifierProvider.value(value: themeProvider),
      ],
      child: const ProPlanApp(),
    ),
  );
}

class ProPlanApp extends StatelessWidget {
  const ProPlanApp({super.key});

  @override
  Widget build(BuildContext context) {
    context.watch<ThemeProvider>();
    return MaterialApp(
      title: 'Melbel',
      debugShowCheckedModeBanner: false,
      theme: MelbelTheme.currentTheme,
      routes: {
        '/login': (_) => const LoginScreen(),
        '/home': (_) => const HomeScreen(),
        '/help': (_) => const HelpScreen(),
        '/account': (_) => const AccountScreen(),
      },
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
      home: const LoginScreen(),
    );
  }
}
