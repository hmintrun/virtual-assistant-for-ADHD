import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../widgets/melbel_ui.dart';

class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key});

  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  late DateTime _month;
  late DateTime _selectedDate;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _month = DateTime(now.year, now.month);
    _selectedDate = DateTime(now.year, now.month, now.day);
  }

  void _changeMonth(int amount) {
    setState(() {
      _month = DateTime(_month.year, _month.month + amount);
    });
  }

  void _openDay(DateTime date) {
    setState(() => _selectedDate = date);
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      backgroundColor: MelbelColors.kSurfaceContainer,
      builder: (_) => DayDetailsSheet(date: date),
    );
  }

  void _openVoiceSchedule() {
    Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const VoiceScheduleScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final monthLabel = 'Tháng ${_month.month}, ${_month.year}';
    final firstDayWeekday = DateTime(_month.year, _month.month, 1).weekday; // 1 = Mon
    final offset = firstDayWeekday - 1;
    final daysInMonth = DateTime(_month.year, _month.month + 1, 0).day;
    final totalCells = ((offset + daysInMonth) / 7).ceil() * 7;

    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Lịch tháng',
        showBack: true,
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 32),
          children: [
            // Month Header with Arrows
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  monthLabel,
                  style: const TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 22,
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kOnSurface,
                  ),
                ),
                Row(
                  children: [
                    IconButton(
                      tooltip: 'Tháng trước',
                      style: IconButton.styleFrom(
                        backgroundColor: MelbelColors.kSurfaceContainer,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      onPressed: () => _changeMonth(-1),
                      icon: const Icon(
                        Icons.chevron_left,
                        color: MelbelColors.kOnSurfaceVariant,
                        size: 20,
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton(
                      tooltip: 'Tháng sau',
                      style: IconButton.styleFrom(
                        backgroundColor: MelbelColors.kSurfaceContainer,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      onPressed: () => _changeMonth(1),
                      icon: const Icon(
                        Icons.chevron_right,
                        color: MelbelColors.kOnSurfaceVariant,
                        size: 20,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Google Calendar Sync Badge
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: MelbelColors.kSurfaceContainer,
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(
                      color:
                          MelbelColors.kOutlineVariant.withValues(alpha: 0.4),
                    ),
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.sync,
                        size: 15,
                        color: MelbelColors.kSecondary,
                      ),
                      SizedBox(width: 6),
                      Text(
                        'Đồng bộ Google Calendar',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
                const Text(
                  'Tuần này',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // Calendar Card Matrix (D-12)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: MelbelColors.kOutlineVariant),
              ),
              child: Column(
                children: [
                  // Weekdays row
                  const Row(
                    mainAxisAlignment: MainAxisAlignment.spaceAround,
                    children: [
                      _WeekdayLabel('T2'),
                      _WeekdayLabel('T3'),
                      _WeekdayLabel('T4'),
                      _WeekdayLabel('T5'),
                      _WeekdayLabel('T6'),
                      _WeekdayLabel('T7'),
                      _WeekdayLabel('CN'),
                    ],
                  ),
                  const SizedBox(height: 8),
                  const Divider(
                    color: MelbelColors.kOutlineVariant,
                    thickness: 0.5,
                  ),
                  const SizedBox(height: 8),

                  // Days Grid
                  GridView.builder(
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    itemCount: totalCells,
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 7,
                      mainAxisSpacing: 6,
                      crossAxisSpacing: 4,
                      childAspectRatio: 0.9,
                    ),
                    itemBuilder: (context, index) {
                      final dayNum = index - offset + 1;
                      final isCurrentMonth =
                          dayNum >= 1 && dayNum <= daysInMonth;

                      if (!isCurrentMonth) {
                        return const Center(
                          child: Text(
                            '·',
                            style: TextStyle(
                              color: MelbelColors.kSurfaceContainerHighest,
                              fontSize: 16,
                            ),
                          ),
                        );
                      }

                      final cellDate =
                          DateTime(_month.year, _month.month, dayNum);
                      final isSelected = cellDate.year == _selectedDate.year &&
                          cellDate.month == _selectedDate.month &&
                          cellDate.day == _selectedDate.day;

                      // Simulated dot indicator
                      final hasPrimary = dayNum % 3 == 0;
                      final hasSecondary = dayNum % 2 == 0;

                      return InkWell(
                        onTap: () => _openDay(cellDate),
                        borderRadius: BorderRadius.circular(10),
                        child: Container(
                          decoration: BoxDecoration(
                            color: isSelected
                                ? MelbelColors.kPrimaryContainer
                                : Colors.transparent,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Text(
                                '$dayNum',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
                                  fontSize: 14,
                                  fontWeight: isSelected
                                      ? FontWeight.w700
                                      : FontWeight.w500,
                                  color: isSelected
                                      ? MelbelColors.kOnPrimary
                                      : MelbelColors.kOnSurface,
                                ),
                              ),
                              const SizedBox(height: 3),
                              if (isSelected)
                                const SizedBox(height: 4)
                              else if (hasPrimary)
                                Container(
                                  width: 4,
                                  height: 4,
                                  decoration: const BoxDecoration(
                                    color: MelbelColors.kPrimaryContainer,
                                    shape: BoxShape.circle,
                                  ),
                                )
                              else if (hasSecondary)
                                Container(
                                  width: 4,
                                  height: 4,
                                  decoration: const BoxDecoration(
                                    color: MelbelColors.kSecondary,
                                    shape: BoxShape.circle,
                                  ),
                                )
                              else
                                const SizedBox(height: 4),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Voice schedule query CTA (D-14 Entry)
            InkWell(
              onTap: _openVoiceSchedule,
              borderRadius: BorderRadius.circular(12),
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainer,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: MelbelColors.kOutlineVariant),
                ),
                child: const Row(
                  children: [
                    Icon(
                      Icons.mic,
                      color: MelbelColors.kPrimaryContainer,
                      size: 24,
                    ),
                    SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Hỏi lịch bằng giọng nói',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                              color: MelbelColors.kOnSurface,
                            ),
                          ),
                          SizedBox(height: 2),
                          Text(
                            'Ví dụ: "Hôm nay tôi có những việc gì lúc 8h?"',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                    ),
                    Icon(
                      Icons.chevron_right,
                      color: MelbelColors.kOnSurfaceVariant,
                      size: 18,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _WeekdayLabel extends StatelessWidget {
  const _WeekdayLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 32,
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: const TextStyle(
          fontFamily: 'Be Vietnam Pro',
          fontSize: 12,
          fontWeight: FontWeight.w500,
          color: MelbelColors.kOnSurfaceVariant,
        ),
      ),
    );
  }
}

// -------------------------------------------------------------
// D-13 Calendar Day Popup Sheet
// -------------------------------------------------------------
class DayDetailsSheet extends StatelessWidget {
  const DayDetailsSheet({required this.date, super.key});
  final DateTime date;

  @override
  Widget build(BuildContext context) {
    final title = 'Ngày ${date.day} tháng ${date.month}, ${date.year}';

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 18,
                        fontWeight: FontWeight.w600,
                        color: MelbelColors.kOnSurface,
                      ),
                    ),
                    const SizedBox(height: 4),
                    const Row(
                      children: [
                        Icon(
                          Icons.circle,
                          size: 6,
                          color: MelbelColors.kSecondary,
                        ),
                        SizedBox(width: 6),
                        Text(
                          '2 phiên tập trung (50 phút)',
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
                IconButton(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(
                    Icons.close,
                    color: MelbelColors.kOnSurfaceVariant,
                    size: 20,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // Task 1
            _DayTaskCard(
              title: 'Soạn đề cương kế hoạch tuần',
              timeRange: '10:00 - 10:30',
              durationText: '1 phiên 25p',
              isCompleted: true,
            ),
            const SizedBox(height: 10),

            // Task 2
            _DayTaskCard(
              title: 'Đọc tài liệu tham khảo quý 3',
              timeRange: '15:30 - 16:00',
              durationText: '1 phiên 25p',
              isCompleted: false,
            ),
            const SizedBox(height: 18),

            PrimaryCta(
              label: 'Thêm việc vào ngày này',
              icon: Icons.add,
              height: 48,
              onPressed: () => Navigator.pop(context),
            ),
          ],
        ),
      ),
    );
  }
}

class _DayTaskCard extends StatelessWidget {
  const _DayTaskCard({
    required this.title,
    required this.timeRange,
    required this.durationText,
    required this.isCompleted,
  });

  final String title;
  final String timeRange;
  final String durationText;
  final bool isCompleted;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: MelbelColors.kSurface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: MelbelColors.kOutlineVariant.withValues(alpha: 0.5),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            isCompleted ? Icons.check_circle : Icons.radio_button_unchecked,
            size: 20,
            color: isCompleted
                ? MelbelColors.kSecondary
                : MelbelColors.kOnSurfaceVariant,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: MelbelColors.kOnSurface,
                    decoration: isCompleted
                        ? TextDecoration.lineThrough
                        : TextDecoration.none,
                  ),
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    const Icon(
                      Icons.schedule,
                      size: 13,
                      color: MelbelColors.kTertiaryFixedDim,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      timeRange,
                      style: const TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 12,
                        color: MelbelColors.kTertiaryFixedDim,
                      ),
                    ),
                    const SizedBox(width: 10),
                    const Icon(
                      Icons.timer_outlined,
                      size: 13,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      durationText,
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
          ),
        ],
      ),
    );
  }
}

// -------------------------------------------------------------
// D-14 Voice Answer Day View Screen
// -------------------------------------------------------------
class VoiceScheduleScreen extends StatelessWidget {
  const VoiceScheduleScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Lịch trình theo giọng nói',
        showBack: true,
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Voice Assistant Context Bubble (D-14)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: MelbelColors.kOutlineVariant),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: const [
                  Row(
                    children: [
                      CircleAvatar(
                        radius: 14,
                        backgroundColor: MelbelColors.kPrimaryContainer,
                        child: Icon(
                          Icons.mic,
                          size: 16,
                          color: MelbelColors.kOnPrimaryContainer,
                        ),
                      ),
                      SizedBox(width: 10),
                      Text(
                        'Bạn vừa hỏi: "Tôi có lịch 8h?"',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 13,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  SizedBox(height: 8),
                  Padding(
                    padding: EdgeInsets.only(left: 38),
                    child: Text(
                      'Bạn có 1 việc lúc 08:00 sáng nay.',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                        color: MelbelColors.kOnSurface,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Timeline Slot 08:00
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    const Text(
                      '08:00',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 20,
                        fontWeight: FontWeight.w600,
                        color: MelbelColors.kPrimaryContainer,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: MelbelColors.kSecondaryContainer,
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: const Text(
                        'Đang tới giờ',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 11,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
                const Text(
                  'Sáng nay',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),

            // Highlighted Focus Task Card
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerHigh,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color:
                      MelbelColors.kPrimaryContainer.withValues(alpha: 0.4),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Họp giao ban đầu tuần với nhóm',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Row(
                    children: [
                      Icon(
                        Icons.schedule,
                        size: 14,
                        color: MelbelColors.kTertiaryFixedDim,
                      ),
                      SizedBox(width: 4),
                      Text(
                        'Hạn: 08:45',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: MelbelColors.kTertiaryFixedDim,
                        ),
                      ),
                      SizedBox(width: 12),
                      Icon(
                        Icons.timer,
                        size: 14,
                        color: MelbelColors.kSecondary,
                      ),
                      SizedBox(width: 4),
                      Text(
                        '1 phiên 25 phút',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  PrimaryCta(
                    label: 'Bắt đầu phiên',
                    icon: Icons.play_arrow,
                    height: 44,
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
