import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'calendar_screen.dart';
import 'task_entry_screen.dart';
import 'uf2_screens.dart';

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

  void _onTabSelected(int index) {
    if (index == _selectedTab) return;
    if (index == 1) {
      Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const CalendarScreen()),
      );
    } else if (index == 2) {
      Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const TaskEntryScreen()),
      );
    } else if (index == 3) {
      showMelbelInfo(
        context,
        title: 'Cài đặt trợ lý',
        message: 'Các tùy chọn thời lượng phiên và phong cách nhắc sẽ có ở luồng tiếp theo.',
        icon: Icons.settings_outlined,
      );
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
          border: const Border(
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
              label: 'Cài đặt',
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
                  style: const TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 4),
                const Text(
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
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => const TaskEntryScreen(),
                ),
              ),
              borderRadius: BorderRadius.circular(12),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainer,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: MelbelColors.kOutlineVariant),
                ),
                child: const Row(
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

            // Section: Next Session (Hero Card)
            Consumer<TaskProvider>(
              builder: (context, task, _) {
                final displayTitle =
                    task.title.isEmpty ? 'Soạn báo cáo đánh giá quý 3' : task.title;
                final displayScope = task.scope.isEmpty
                    ? 'Bước 1: Soạn dàn ý sơ bộ'
                    : task.scope;
                final displayDeadline = task.deadline == null
                    ? '18:00 Ngày mai'
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
                              const Icon(
                                Icons.schedule,
                                size: 16,
                                color: MelbelColors.kTertiaryFixedDim,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                'Hạn chót: $displayDeadline',
                                style: const TextStyle(
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
                        style: const TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        displayScope,
                        style: const TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 14,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                      const SizedBox(height: 18),

                      // Progress Bar with Moon Marker
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: const [
                          Text(
                            'Tiến độ công việc',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                          Text(
                            'Phiên 2/5',
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
                          const factor = 0.4;
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
                                  decoration: const BoxDecoration(
                                    color: MelbelColors.kPrimaryContainer,
                                    shape: BoxShape.circle,
                                  ),
                                  child: const Icon(
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
                        label: 'Bắt đầu 25p',
                        icon: Icons.play_arrow,
                        onPressed: () {
                          Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => const SessionReminderScreen(),
                            ),
                          );
                        },
                      ),
                    ],
                  ),
                );
              },
            ),
            const SizedBox(height: 24),

            // Section: Remaining Today
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: const [
                Text(
                  'Còn lại hôm nay',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
                Text(
                  '2 nhiệm vụ',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),

            _RemainingTaskCard(
              title: '14:00 · Trả lời email đối tác',
              deadline: 'Hạn 15:00',
              tag: '1 phiên 25p',
              note: 'Cần tập trung nhẹ nhàng',
            ),
            const SizedBox(height: 10),

            _RemainingTaskCard(
              title: '16:30 · Đọc tài liệu tham khảo',
              deadline: 'Hạn 17:30',
              tag: '1 phiên 25p',
              note: 'Không áp lực thời gian',
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
                      child: const Row(
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
                      child: const Row(
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
                  style: const TextStyle(
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
                style: const TextStyle(
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
                  style: const TextStyle(
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
                  style: const TextStyle(
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
    final color =
        isSelected ? MelbelColors.kPrimaryContainer : MelbelColors.kOnSurfaceVariant;
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
