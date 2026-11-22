import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/responsive_layout.dart';
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
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa xác nhận được thông tin: $error')),
        );
      }
    } finally {
      if (mounted) setState(() => _isConfirming = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final displayTitle = task.title;
    final displayDeadline = task.formatDeadline(task.deadline);
    final availabilityStr = task.availabilitySummary;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Sẵn sàng',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: MelbelLayout.pageInsets(context, top: 12, bottom: 32),
            children: [
              const PlanningStageHeader(step: 2, label: 'Xác nhận thông tin'),
              const SizedBox(height: 16),
              MelbelCard(
                borderRadius: 26,
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Eyebrow('Tên việc'),
                    const SizedBox(height: 6),
                    Text(
                      displayTitle,
                      style: textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: palette.accentInk,
                      ),
                    ),
                    const SizedBox(height: 16),
                    _DetailLine(
                      icon: Icons.description_outlined,
                      title: 'Phạm vi',
                      content:
                          task.scope.isEmpty ? 'Chưa có phạm vi' : task.scope,
                    ),
                    const SizedBox(height: 14),
                    _DetailLine(
                        icon: Icons.schedule_outlined,
                        title: 'Giờ rảnh',
                        content: availabilityStr),
                    const SizedBox(height: 14),
                    _DetailLine(
                        icon: Icons.flag_outlined,
                        title: 'Hạn chót',
                        content: displayDeadline),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              const MelbelChatBlock(
                expression: CatExpression.happy,
                tone: MelbelCardTone.sage,
                senderLabel: 'Melbel',
                badge: 'Sẵn sàng',
                message:
                    'Bạn sẽ được xem và chỉnh phương án trước khi chốt lịch.',
                mascotWidth: 58,
                mascotHeight: 52,
              ),
              const SizedBox(height: 24),
              PrimaryCta(
                label: _isConfirming ? 'Đang chuẩn bị...' : 'Xác nhận công việc',
                icon: _isConfirming
                    ? Icons.hourglass_top_rounded
                    : Icons.check_rounded,
                onPressed: _isConfirming ? null : _onConfirm,
              ),
            ],
          ),
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
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: palette.sageSoft,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Icon(icon, size: 19, color: palette.accentInk),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: textTheme.labelSmall?.copyWith(
                  color: palette.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                content,
                style: textTheme.bodyMedium?.copyWith(
                  fontWeight: FontWeight.w500,
                  color: palette.onSurface,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
