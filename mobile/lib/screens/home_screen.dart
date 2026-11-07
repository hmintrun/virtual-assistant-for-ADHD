import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'calendar_screen.dart';
import 'focus_screen.dart';
import 'task_entry_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _selectedTab = 0;

  String get _greetingText {
    final hour = DateTime.now().hour;
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

  Future<void> _openFocusForTask(TaskProvider taskProvider) async {
    final task = taskProvider.confirmedTask;
    if (task == null || task.sessions.isEmpty) return;
    final sessions = [...task.sessions]
      ..sort((a, b) => a.scheduledStart.compareTo(b.scheduledStart));
    final session = sessions.firstWhere(
      (item) => item.status == 'scheduled',
      orElse: () => sessions.first,
    );
    final matchingSteps =
        task.steps.where((item) => item.stepId == session.stepId);
    final step = matchingSteps.isNotEmpty
        ? matchingSteps.first
        : (task.steps.isEmpty ? null : task.steps.first);
    final focusSession = FocusSessionInfo(
      sessionId: session.sessionId,
      taskTitle: task.title,
      stepTitle: step?.title ?? 'Bước hiện tại',
      scheduledStart: session.scheduledStart,
      scheduledEnd: session.scheduledEnd,
      workSeconds: session.workSeconds,
      restSeconds: session.restSeconds,
      microTaskText: session.microTaskText,
      segments: session.segments,
      deadline: taskProvider.deadline,
    );
    final focus = context.read<FocusProvider>();
    await focus.startSession(focusSession);
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
  }

  Future<void> _reopenFocus(FocusProvider focus) async {
    if (!focus.isActiveRun || focus.session == null) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
  }

  void _onTabSelected(int index) {
    if (index == _selectedTab) return;
    if (index == 1) {
      Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const CalendarScreen()),
      );
    } else if (index == 2) {
      _openNewTask();
    } else if (index == 3) {
      Navigator.of(context).pushNamed('/account');
    } else {
      setState(() => _selectedTab = index);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Trang Chủ',
        leadingIcon: Icons.dark_mode_outlined,
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
              isSelected: _selectedTab == 0,
              onTap: () => _onTabSelected(0),
            ),
            _BottomTab(
              icon: Icons.calendar_month,
              label: 'Lịch',
              isSelected: _selectedTab == 1,
              onTap: () => _onTabSelected(1),
            ),
            _BottomTab(
              icon: Icons.add_circle,
              label: 'Thêm việc',
              isSelected: _selectedTab == 2,
              onTap: () => _onTabSelected(2),
            ),
            _BottomTab(
              icon: Icons.settings,
              label: 'Tài khoản',
              isSelected: _selectedTab == 3,
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
            // Greeting Header
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _greetingText,
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  'Không gian hôm nay',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 24,
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kOnSurface,
                    letterSpacing: -0.5,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // Quick Add Task Button
            InkWell(
              onTap: _openNewTask,
              borderRadius: BorderRadius.circular(12),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 12,
                ),
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainer,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: MelbelColors.kOutlineVariant),
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.add_circle,
                      color: MelbelColors.kPrimaryContainer,
                      size: 20,
                    ),
                    SizedBox(width: 8),
                    Text(
                      'Thêm việc mới',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 14,
                        fontWeight: FontWeight.w500,
                        color: MelbelColors.kOnSurface,
                      ),
                    ),
                    Spacer(),
                    Icon(
                      Icons.chevron_right,
                      color: MelbelColors.kOnSurfaceVariant,
                      size: 18,
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),

            Consumer<FocusProvider>(
              builder: (context, focus, _) {
                if (!focus.isActiveRun || focus.session == null) {
                  return const SizedBox.shrink();
                }
                return Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: MelbelCard(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Eyebrow('Phiên đang chạy'),
                        const SizedBox(height: 8),
                        Text(
                          focus.session!.taskTitle,
                          style: const TextStyle(
                              fontSize: 17, fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 12),
                        PrimaryCta(
                          label: focus.stage == FocusStage.recovery
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

            // Section: Next Session (Hero Card)
            Consumer<TaskProvider>(
              builder: (context, task, _) {
                final saved = task.confirmedTask;
                if (saved == null || saved.sessions.isEmpty) {
                  return const MelbelCard(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                        Text('Chưa có phiên được tải cho công việc hiện tại.'),
                        SizedBox(height: 8),
                        Text(
                            'Mở Lịch để xem các phiên đã xác nhận, hoặc thêm công việc mới.'),
                      ]));
                }

                final displayTitle = saved.title;
                final displayScope = saved.steps.isEmpty
                    ? 'Phiên làm việc đã xác nhận'
                    : saved.steps.first.title;
                final displayDeadline = task.deadline == null
                    ? 'Chưa đặt hạn chót'
                    : task.formatDeadline(task.deadline);

                return MelbelCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          const Eyebrow('Phiên tiếp theo'),
                          Row(
                            children: [
                              Icon(
                                Icons.schedule,
                                size: 16,
                                color: MelbelColors.kTertiaryFixedDim,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                'Hạn chót: $displayDeadline',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
                                  fontSize: 12,
                                  color: MelbelColors.kOnSurfaceVariant,
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Text(
                        displayTitle,
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        displayScope,
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 14,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                      const SizedBox(height: 18),

                      // Progress Bar with Moon Marker
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            'Tiến độ công việc',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                          Text(
                            '${saved.sessions.where((s) => s.status == 'completed').length}/${saved.sessions.length} phiên hoàn thành',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              fontWeight: FontWeight.w500,
                              color: MelbelColors.kPrimaryContainer,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      LayoutBuilder(
                        builder: (context, constraints) {
                          final factor = saved.sessions
                                  .where((s) => s.status == 'completed')
                                  .length /
                              saved.sessions.length;
                          final barWidth = constraints.maxWidth;
                          return Stack(
                            clipBehavior: Clip.none,
                            alignment: Alignment.centerLeft,
                            children: [
                              Container(
                                height: 12,
                                width: double.infinity,
                                decoration: BoxDecoration(
                                  color: MelbelColors.kSurfaceContainerHighest,
                                  borderRadius: BorderRadius.circular(999),
                                ),
                              ),
                              Container(
                                height: 12,
                                width: barWidth * factor,
                                decoration: BoxDecoration(
                                  color: MelbelColors.kPrimaryContainer,
                                  borderRadius: BorderRadius.circular(999),
                                ),
                              ),
                              Positioned(
                                left: (barWidth * factor) - 10,
                                child: Container(
                                  width: 20,
                                  height: 20,
                                  decoration: BoxDecoration(
                                    color: MelbelColors.kPrimaryContainer,
                                    shape: BoxShape.circle,
                                  ),
                                  child: Icon(
                                    Icons.nightlight,
                                    size: 13,
                                    color: MelbelColors.kOnPrimaryContainer,
                                  ),
                                ),
                              ),
                            ],
                          );
                        },
                      ),
                      const SizedBox(height: 20),

                      PrimaryCta(
                        label: 'Chuẩn bị phiên',
                        icon: Icons.play_arrow,
                        onPressed: () => _openFocusForTask(task),
                      ),
                      Center(
                        child: TextButton(
                          onPressed: () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => const CalendarScreen(),
                            ),
                          ),
                          child: const Text('Mở lịch phiên'),
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
            const SizedBox(height: 24),

            Consumer<TaskProvider>(
              builder: (context, task, _) {
                final confirmed = task.confirmedTask;
                final sessions = (confirmed?.sessions ?? const [])
                    .where((session) => session.status == 'scheduled')
                    .toList()
                  ..sort(
                      (a, b) => a.scheduledStart.compareTo(b.scheduledStart));
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text('Còn lại hôm nay',
                            style: TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kOnSurfaceVariant)),
                        Text(
                          sessions.isEmpty ? '—' : '${sessions.length} phiên',
                          style: TextStyle(
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    if (sessions.isEmpty)
                      const MelbelCard(
                        child: Text(
                            'Chưa có phiên nào được xác nhận cho hôm nay.'),
                      )
                    else
                      for (final session in sessions)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: _RemainingTaskCard(
                            title:
                                '${_clock(session.scheduledStart)} · ${confirmed!.title}',
                            deadline: task.deadline == null
                                ? 'Chưa đặt hạn chót'
                                : 'Hạn ${task.formatDeadline(task.deadline)}',
                            tag: '${(session.workSeconds / 60).ceil()} phút',
                            note: 'Phiên đã xác nhận',
                          ),
                        ),
                  ],
                );
              },
            ),
            const SizedBox(height: 20),

            // Bottom 2 Action Buttons
            Row(
              children: [
                Expanded(
                  child: InkWell(
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => const CalendarScreen(),
                      ),
                    ),
                    borderRadius: BorderRadius.circular(12),
                    child: Container(
                      height: 56,
                      decoration: BoxDecoration(
                        color: MelbelColors.kSurfaceContainer,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: MelbelColors.kOutlineVariant),
                      ),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.calendar_month,
                            size: 20,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                          SizedBox(width: 8),
                          Text(
                            'Lịch tháng',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 14,
                              fontWeight: FontWeight.w500,
                              color: MelbelColors.kOnSurface,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: InkWell(
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => const TaskEntryScreen(),
                      ),
                    ),
                    borderRadius: BorderRadius.circular(12),
                    child: Container(
                      height: 56,
                      decoration: BoxDecoration(
                        color: MelbelColors.kSurfaceContainer,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: MelbelColors.kOutlineVariant),
                      ),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.add_circle,
                            size: 20,
                            color: MelbelColors.kPrimaryContainer,
                          ),
                          SizedBox(width: 8),
                          Text(
                            'Thêm việc',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 14,
                              fontWeight: FontWeight.w500,
                              color: MelbelColors.kOnSurface,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _clock(DateTime value) =>
      '${value.hour.toString().padLeft(2, '0')}:${value.minute.toString().padLeft(2, '0')}';
}

class _RemainingTaskCard extends StatelessWidget {
  const _RemainingTaskCard({
    required this.title,
    required this.deadline,
    required this.tag,
    required this.note,
  });

  final String title;
  final String deadline;
  final String tag;
  final String note;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: MelbelColors.kSurfaceContainer,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: MelbelColors.kOutlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 15,
                    fontWeight: FontWeight.w500,
                    color: MelbelColors.kOnSurface,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                deadline,
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 12,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainerHighest,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  tag,
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  note,
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ],
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
