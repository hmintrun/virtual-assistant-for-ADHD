import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';

class DraftSavedScreen extends StatelessWidget {
  const DraftSavedScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final now = DateTime.now();
    final timeStr =
        '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}';

    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Draft Stored',
        showBack: true,
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Auto-Save Toast Notification
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerLow,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: MelbelColors.kOutlineVariant.withValues(alpha: 0.3),
                ),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Row(
                    children: [
                      Icon(
                        Icons.check_circle,
                        size: 18,
                        color: MelbelColors.kSecondary,
                      ),
                      SizedBox(width: 8),
                      Text(
                        'Đã tự lưu nháp an toàn',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 14,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                    ],
                  ),
                  Text(
                    timeStr,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Reassuring Low-Stimulation Greeting
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: const [
                Row(
                  children: [
                    Icon(
                      Icons.bedtime,
                      size: 18,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                    SizedBox(width: 6),
                    Text(
                      'Không gian yên tĩnh',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 13,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ),
                  ],
                ),
                SizedBox(height: 6),
                Text(
                  'Cứ thong thả, bạn nhé',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 24,
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kPrimary,
                    letterSpacing: -0.3,
                  ),
                ),
                SizedBox(height: 4),
                Text(
                  'Mọi thứ đã được giữ nguyên vẹn. Bạn có thể tiếp tục bất cứ khi nào sẵn sàng.',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    color: MelbelColors.kOnSurfaceVariant,
                    height: 1.4,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 24),

            // Prominent Draft Card (D-19)
            Container(
              padding: const EdgeInsets.all(18),
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
                      Row(
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: const BoxDecoration(
                              color: MelbelColors.kPrimaryContainer,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          const Eyebrow(
                            'Công việc nháp',
                            color: MelbelColors.kPrimaryContainer,
                          ),
                        ],
                      ),
                      IconButton(
                        tooltip: 'Tạm gác lại',
                        padding: EdgeInsets.zero,
                        constraints:
                            const BoxConstraints(minWidth: 32, minHeight: 32),
                        onPressed: () => Navigator.popUntil(
                            context, (route) => route.isFirst),
                        icon: const Icon(
                          Icons.archive_outlined,
                          size: 20,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    task.title.isEmpty
                        ? 'Soạn báo cáo đánh giá quý 3'
                        : task.title,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      const Icon(
                        Icons.schedule,
                        size: 16,
                        color: MelbelColors.kTertiaryFixedDim,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Hạn: ${task.formatDeadline(task.deadline)}',
                        style: const TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 14,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kTertiaryFixedDim,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Trạng thái máy chủ: ${task.draft?.stage ?? 'draft cục bộ'} · Chưa có phiên chính thức',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 13,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 18),
                  PrimaryCta(
                    label: 'Làm tiếp việc này',
                    icon: Icons.play_arrow,
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Ambient Schedule
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: const [
                Row(
                  children: [
                    Icon(
                      Icons.calendar_today,
                      size: 18,
                      color: MelbelColors.kSecondary,
                    ),
                    SizedBox(width: 8),
                    Text(
                      'Lịch hôm nay',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 15,
                        fontWeight: FontWeight.w500,
                        color: MelbelColors.kOnSurface,
                      ),
                    ),
                  ],
                ),
                Text(
                  '2 phiên còn lại',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Calm Scheduled Task Item 1
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerLow,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: MelbelColors.kOutlineVariant.withValues(alpha: 0.3),
                ),
              ),
              child: Row(
                children: [
                  Container(
                    width: 24,
                    height: 24,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainer,
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: const Icon(
                      Icons.done,
                      size: 16,
                      color: MelbelColors.kSecondary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Đồng bộ nhanh cùng nhóm',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kOnSurface,
                          ),
                        ),
                        SizedBox(height: 2),
                        Text(
                          '15:30 · 15 phút nhẹ nhàng',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 12,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainer,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: const Text(
                      'Sắp tới',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 11,
                        color: MelbelColors.kSecondary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 10),

            // Calm Scheduled Task Item 2
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerLow,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: MelbelColors.kOutlineVariant.withValues(alpha: 0.3),
                ),
              ),
              child: Row(
                children: [
                  Container(
                    width: 24,
                    height: 24,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainer,
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: const Icon(
                      Icons.spa,
                      size: 16,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 12),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Nghỉ mắt & uống nước',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 14,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                        SizedBox(height: 2),
                        Text(
                          '16:30 · Thư giãn thần kinh',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 12,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const Icon(
                    Icons.more_horiz,
                    size: 18,
                    color: MelbelColors.kOnSurfaceVariant,
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
