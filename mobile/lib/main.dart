import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'core/theme.dart';
import 'providers/task_provider.dart';
import 'screens/home_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final taskProvider = TaskProvider();
  unawaited(() async {
    await taskProvider.restoreDraft();
    await taskProvider.restoreBackendState();
  }());
  runApp(
    ChangeNotifierProvider.value(
      value: taskProvider,
      child: const ProPlanApp(),
    ),
  );
}

class ProPlanApp extends StatelessWidget {
  const ProPlanApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Melbel',
      debugShowCheckedModeBanner: false,
      theme: MelbelTheme.darkTheme,
      builder: (context, child) => ColoredBox(
        color: const Color(0xFF080A10),
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 430),
            child: child ?? const SizedBox.shrink(),
          ),
        ),
      ),
      home: const HomeScreen(),
    );
  }
}
