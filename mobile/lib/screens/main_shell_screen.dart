import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'calendar_screen.dart';
import 'home_screen.dart';
import 'support_screens.dart';
import 'task_entry_screen.dart';

/// Root navigation shell keeping [MelbelFloatingNavBar] persistent across the
/// three top-level tabs: Hôm nay (0), Lịch (1), and Tài khoản (3).
class MainShellScreen extends StatefulWidget {
  const MainShellScreen({
    this.initialIndex = 0,
    this.api,
    super.key,
  });

  final int initialIndex;
  final ApiClient? api;

  static _MainShellScreenState? _active;

  /// Returns to the shell (defaulting to the "Hôm nay" tab) and refreshes
  /// its schedule without stacking duplicate routes.
  static void returnHome(BuildContext context, {int tabIndex = 0}) {
    final active = _active;
    if (active != null && active.mounted) {
      active.switchToTab(tabIndex, forceRefresh: true);
      Navigator.of(context).popUntil((route) => route.isFirst);
      return;
    }
    Navigator.of(context).pushNamedAndRemoveUntil('/home', (_) => false);
  }

  @override
  State<MainShellScreen> createState() => _MainShellScreenState();
}

class _MainShellScreenState extends State<MainShellScreen> {
  late int _selectedNavIndex =
      widget.initialIndex == 1 || widget.initialIndex == 3
          ? widget.initialIndex
          : 0;
  final ValueNotifier<int> _homeRefreshSignal = ValueNotifier<int>(0);
  final ValueNotifier<int> _calendarRefreshSignal = ValueNotifier<int>(0);

  int get _stackIndex => switch (_selectedNavIndex) {
        1 => 1,
        3 => 2,
        _ => 0,
      };

  @override
  void initState() {
    super.initState();
    MainShellScreen._active = this;
  }

  @override
  void dispose() {
    if (identical(MainShellScreen._active, this)) {
      MainShellScreen._active = null;
    }
    _homeRefreshSignal.dispose();
    _calendarRefreshSignal.dispose();
    super.dispose();
  }

  Future<void> switchToTab(int navIndex, {bool forceRefresh = false}) async {
    if (navIndex == 2) {
      await _openNewTask();
      return;
    }
    final target = (navIndex == 1 || navIndex == 3) ? navIndex : 0;
    if (mounted && _selectedNavIndex != target) {
      setState(() => _selectedNavIndex = target);
    }
    if (forceRefresh) {
      _homeRefreshSignal.value++;
      _calendarRefreshSignal.value++;
    } else if (target == 0) {
      _homeRefreshSignal.value++;
    } else if (target == 1) {
      _calendarRefreshSignal.value++;
    }
  }

  Future<void> _openNewTask() async {
    try {
      await context.read<TaskProvider>().startNewDraft();
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const TaskEntryScreen()),
      );
      if (!mounted) return;
      _homeRefreshSignal.value++;
      _calendarRefreshSignal.value++;
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Chưa mở việc mới: $error')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(
        index: _stackIndex,
        children: [
          HomeScreen(
            api: widget.api,
            embeddedInShell: true,
            refreshSignal: _homeRefreshSignal,
            onSwitchTab: (index) => unawaited(switchToTab(index)),
          ),
          CalendarScreen(
            client: widget.api,
            embeddedInShell: true,
            refreshSignal: _calendarRefreshSignal,
          ),
          AccountScreen(
            api: widget.api,
            embeddedInShell: true,
          ),
        ],
      ),
      bottomNavigationBar: MelbelFloatingNavBar(
        selectedIndex: _selectedNavIndex,
        onSelected: (index) {
          HapticFeedback.selectionClick();
          unawaited(switchToTab(index));
        },
      ),
    );
  }
}
