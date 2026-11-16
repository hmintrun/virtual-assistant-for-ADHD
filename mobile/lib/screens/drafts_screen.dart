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
        title: 'Hộp nháp',
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
                  Row(
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
                    style: TextStyle(
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
              children: [
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
                            decoration: BoxDecoration(
                              color: MelbelColors.kPrimaryContainer,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Eyebrow(
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
                        icon: Icon(
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
                        ? 'Chưa có nội dung công việc'
                        : task.title,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      Icon(
                        Icons.schedule,
                        size: 16,
                        color: MelbelColors.kTertiaryFixedDim,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Hạn: ${task.formatDeadline(task.deadline)}',
                        style: TextStyle(
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

            const _NoScheduleCard(),
          ],
        ),
      ),
    );
  }
}

class _NoScheduleCard extends StatelessWidget {
  const _NoScheduleCard();

  @override
  Widget build(BuildContext context) => MelbelCard(
        child: Row(
          children: [
            Icon(Icons.calendar_today, color: MelbelColors.kSecondary),
            SizedBox(width: 12),
            Expanded(
              child: Text('Chưa có phiên nào được xác nhận cho hôm nay.'),
            ),
          ],
        ),
      );
}
