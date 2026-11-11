import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import '../services/schedule_service.dart';
import 'focus_screen.dart';
import 'task_entry_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _schedule = ScheduleService(ApiClient());
  Future<List<Map<String, dynamic>>>? _sessions;
  Object? _confirmedTask;

  void _loadWhenTaskChanges() {
    final confirmed = context.watch<TaskProvider>().confirmedTask;
    if (_sessions == null || !identical(_confirmedTask, confirmed)) {
      _confirmedTask = confirmed;
      _sessions = _loadSessions();
    }
  }

  Future<List<Map<String, dynamic>>> _loadSessions() {
    final now = DateTime.now();
    return _schedule.loadSessions(
      DateUtils.dateOnly(now),
      DateTime(now.year, now.month, now.day + 30),
      includeTitles: true,
    );
  }

  void _refreshSchedule() {
    if (!mounted) return;
    setState(() {
      _sessions = _loadSessions();
    });
  }

  String get _greetingText {
    final hour = DateTime.now().hour;
    if (hour < 5) return 'Chào bạn';
    if (hour < 12) return 'Chào buổi sáng';
    if (hour < 18) return 'Chào buổi chiều';
    return 'Chào buổi tối';
  }

  Future<void> _openNewTask() async {
    try {
      await context.read<TaskProvider>().startNewDraft();
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const TaskEntryScreen()),
      );
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Chưa mở việc mới: $error')));
    }
  }

  Future<void> _openFocus(FocusSessionInfo focusSession) async {
    final focus = context.read<FocusProvider>();
    if (focus.isActiveRun || focus.hasUnfinishedServerRun) {
      await _reopenFocus(focus);
      return;
    }
    await focus.startSession(focusSession);
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
    _refreshSchedule();
  }

  Future<void> _reopenFocus(FocusProvider focus) async {
    if (focus.session == null ||
        (!focus.isActiveRun && !focus.hasUnfinishedServerRun)) return;
    await focus.refreshRunCheckIn();
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
    _refreshSchedule();
  }

  Future<void> _onTabSelected(int index) async {
    if (index == 1) {
      await Navigator.of(context).pushNamed('/calendar');
      _refreshSchedule();
    } else if (index == 2) {
      await _openNewTask();
      _refreshSchedule();
    } else if (index == 3) {
      await Navigator.of(context).pushNamed('/account');
    }
  }

  @override
  Widget build(BuildContext context) {
    _loadWhenTaskChanges();
    final theme = Theme.of(context);
    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      appBar: const MelbelAppBar(
        title: 'Trang Chủ',
        showThemeToggle: true,
      ),
      bottomNavigationBar: Container(
        height: 64 + MediaQuery.paddingOf(context).bottom,
        padding: EdgeInsets.only(bottom: MediaQuery.paddingOf(context).bottom),
        decoration: BoxDecoration(
          color: MelbelColors.kSurface.withValues(alpha: 0.95),
          border: Border(
            top: BorderSide(color: MelbelColors.kOutlineVariant, width: 0.5),
          ),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceAround,
          children: [
            _BottomTab(
              icon: Icons.today,
              label: 'Hôm nay',
              isSelected: true,
              onTap: () => _onTabSelected(0),
            ),
            _BottomTab(
              icon: Icons.date_range_rounded,
              label: 'Lịch',
              isSelected: false,
              onTap: () => _onTabSelected(1),
            ),
            _BottomTab(
              icon: Icons.add_circle,
              label: 'Thêm việc',
              isSelected: false,
              onTap: () => _onTabSelected(2),
            ),
            _BottomTab(
              icon: Icons.settings,
              label: 'Tài khoản',
              isSelected: false,
              onTap: () => _onTabSelected(3),
            ),
          ],
        ),
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
          children: [
            MelbelEntrance(
              child: MelbelCard(
                color: theme.colorScheme.surfaceContainerHigh,
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Eyebrow(_greetingText),
                          const SizedBox(height: 6),
                          Text(
                            'Không gian hôm nay',
                            style: theme.textTheme.headlineSmall?.copyWith(
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    const SizedBox(
                      width: 82,
                      height: 78,
                      child: MelbelMascot(),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            Consumer<FocusProvider>(
              builder: (context, focus, _) {
                if (focus.session == null ||
                    (!focus.isActiveRun && !focus.hasUnfinishedServerRun)) {
                  return const SizedBox.shrink();
                }
                return Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: MelbelCard(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Eyebrow(focus.isActiveRun
                            ? 'Phiên đang chạy'
                            : 'Kết quả phiên'),
                        const SizedBox(height: 8),
                        Text(
                          focus.session!.taskTitle,
                          style: const TextStyle(
                              fontSize: 17, fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 12),
                        PrimaryCta(
                          label: focus.hasUnfinishedServerRun
                              ? 'Xem kết quả phiên'
                              : focus.stage == FocusStage.recovery
                                  ? 'Khôi phục phiên'
                                  : 'Quay lại phiên',
                          icon: Icons.play_arrow_rounded,
                          onPressed: () => _reopenFocus(focus),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
            FutureBuilder<List<Map<String, dynamic>>>(
              future: _sessions,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return MelbelCard(
                    child: TextButton(
                      onPressed: _refreshSchedule,
                      child: const Text('Chưa tải được lịch · Thử lại'),
                    ),
                  );
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                final focus = context.watch<FocusProvider>();
                final now = DateTime.now();
                final sessions = snapshot.data!
                    .where((row) => row['status'] == 'scheduled')
                    .map(FocusSessionInfo.fromJson)
                    .where((session) =>
                        session.scheduledEnd.isAfter(now) &&
                        !(focus.stage == FocusStage.ended &&
                            session.sessionId == focus.session?.sessionId) &&
                        !(focus.isActiveRun &&
                            session.sessionId == focus.session?.sessionId))
                    .toList()
                  ..sort(
                      (a, b) => a.scheduledStart.compareTo(b.scheduledStart));
                final today = sessions.skip(1).where((session) =>
                    DateUtils.isSameDay(session.scheduledStart.toLocal(), now));
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Eyebrow('Phiên tiếp theo'),
                    const SizedBox(height: 8),
                    if (sessions.isEmpty)
                      const MelbelCard(child: Text('Chưa có phiên sắp tới.'))
                    else
                      _sessionTile(sessions.first, showDate: true),
                    const SizedBox(height: 20),
                    const Eyebrow('Còn lại hôm nay'),
                    const SizedBox(height: 8),
                    if (today.isEmpty)
                      const MelbelCard(
                          child: Text('Không còn phiên nào khác hôm nay.'))
                    else
                      for (final session in today) _sessionTile(session),
                  ],
                );
              },
            ),
            const SizedBox(height: 20),
          ],
        ),
      ),
    );
  }

  Widget _sessionTile(FocusSessionInfo session, {bool showDate = false}) {
    final start = session.scheduledStart.toLocal();
    final time = MaterialLocalizations.of(context).formatTimeOfDay(
        TimeOfDay.fromDateTime(start),
        alwaysUse24HourFormat: true);
    final date = showDate ? '${start.day}/${start.month} · ' : '';
    final deadline =
        context.read<TaskProvider>().formatDeadline(session.deadline);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: MelbelCard(
        child: ListTile(
          title: Text(session.stepTitle),
          subtitle: Text('$date$time · ${session.taskTitle}\nHạn: $deadline'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _openFocus(session),
        ),
      ),
    );
  }
}

class _BottomTab extends StatelessWidget {
  const _BottomTab({
    required this.icon,
    required this.label,
    required this.isSelected,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = isSelected
        ? MelbelColors.kPrimaryContainer
        : MelbelColors.kOnSurfaceVariant;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 24, color: color),
            const SizedBox(height: 4),
            Text(
              label,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 12,
                fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
