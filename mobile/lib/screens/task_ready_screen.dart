import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'uf2_screens.dart';

class TaskReadyScreen extends StatefulWidget {
  const TaskReadyScreen({super.key});

  @override
  State<TaskReadyScreen> createState() => _TaskReadyScreenState();
}

class _TaskReadyScreenState extends State<TaskReadyScreen> {
  bool _isConfirming = false;

  Future<void> _onConfirm() async {
    if (_isConfirming || context.read<TaskProvider>().isApiBusy) return;
    setState(() => _isConfirming = true);
    try {
      await context.read<TaskProvider>().confirmDraftSummaryOnBackend();
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
          builder: (_) => const DecompositionChoiceScreen(),
        ),
      );
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa xác nhận được thông tin: $error')),
        );
    } finally {
      if (mounted) setState(() => _isConfirming = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final displayTitle = task.title;
    final displayDeadline = task.formatDeadline(task.deadline);
    final availabilityStr = task.availabilitySummary;

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Ready State', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Top Status
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    Container(
                      width: 10,
                      height: 10,
                      decoration: const BoxDecoration(
                        color: MelbelColors.kSecondary,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 8),
                    const Text(
                      'Mọi thứ đã rõ ràng',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 14,
                        fontWeight: FontWeight.w500,
                        color: MelbelColors.kSecondary,
                      ),
                    ),
                  ],
                ),
                const Eyebrow('Bước cuối'),
              ],
            ),
            const SizedBox(height: 16),

            // Heading
            const Text(
              'Bạn đã sẵn sàng chứ?',
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 24,
                fontWeight: FontWeight.w600,
                color: MelbelColors.kOnSurface,
                letterSpacing: -0.4,
              ),
            ),
            const SizedBox(height: 4),
            const Text(
              'Cứ thong thả, hệ thống đã chuẩn bị nhịp làm việc êm dịu nhất cho bạn.',
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 14,
                color: MelbelColors.kOnSurfaceVariant,
              ),
            ),
            const SizedBox(height: 20),

            // Main Details Card (D-20)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: MelbelColors.kOutlineVariant),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Tên việc',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    displayTitle,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 20,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kPrimary,
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Deadline box inside card
                  Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Row(
                          children: [
                            Container(
                              width: 40,
                              height: 40,
                              decoration: const BoxDecoration(
                                color: MelbelColors.kSurfaceContainerHighest,
                                shape: BoxShape.circle,
                              ),
                              child: const Icon(
                                Icons.flag,
                                size: 20,
                                color: MelbelColors.kPrimaryContainer,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text(
                                  'Hạn chót',
                                  style: TextStyle(
                                    fontFamily: 'Be Vietnam Pro',
                                    fontSize: 12,
                                    color: MelbelColors.kOnSurfaceVariant,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  displayDeadline,
                                  style: const TextStyle(
                                    fontFamily: 'Be Vietnam Pro',
                                    fontSize: 15,
                                    fontWeight: FontWeight.w600,
                                    color: MelbelColors.kOnSurface,
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 10,
                            vertical: 4,
                          ),
                          decoration: BoxDecoration(
                            color: MelbelColors.kSecondaryContainer.withValues(
                              alpha: 0.4,
                            ),
                            borderRadius: BorderRadius.circular(999),
                          ),
                          child: const Text(
                            'Hạn đã chọn',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              fontWeight: FontWeight.w500,
                              color: MelbelColors.kSecondary,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Three Info Lines
                  _DetailLine(
                    icon: Icons.schedule,
                    title: 'Giờ rảnh theo hồ sơ',
                    content: availabilityStr,
                  ),
                  const SizedBox(height: 14),
                  const _DetailLine(
                    icon: Icons.timer,
                    title: 'Kế hoạch phiên',
                    content:
                        'Backend sẽ đề xuất phiên sau khi xác nhận thông tin.',
                  ),
                  const SizedBox(height: 14),
                  const _DetailLine(
                    icon: Icons.bedtime,
                    title: 'Nhịp hỗ trợ',
                    content: 'Báo thức nhẹ nhàng, không tiếng giật mình',
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Helper box
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerLow,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: MelbelColors.kOutlineVariant.withValues(alpha: 0.3),
                ),
              ),
              child: const Row(
                children: [
                  Icon(Icons.eco, size: 22, color: MelbelColors.kSecondary),
                  SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      'Không áp lực, không phán xét. Bạn có thể tạm dừng hoặc điều chỉnh bất kỳ lúc nào.',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 13,
                        color: MelbelColors.kOnSurfaceVariant,
                        height: 1.4,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Confirm Action
            PrimaryCta(
              label: _isConfirming ? 'Đang chuẩn bị...' : 'Xác nhận công việc',
              icon: _isConfirming ? Icons.hourglass_top : Icons.check,
              onPressed: _isConfirming ? null : _onConfirm,
            ),
          ],
        ),
      ),
    );
  }
}

class _DetailLine extends StatelessWidget {
  const _DetailLine({
    required this.icon,
    required this.title,
    required this.content,
  });

  final IconData icon;
  final String title;
  final String content;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 20, color: MelbelColors.kOnSurfaceVariant),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 12,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                content,
                style: const TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                  color: MelbelColors.kOnSurface,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
