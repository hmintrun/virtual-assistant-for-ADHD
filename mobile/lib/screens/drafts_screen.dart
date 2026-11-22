import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';

class DraftSavedScreen extends StatelessWidget {
  const DraftSavedScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final savedOnline = task.draft != null && task.apiError == null;
    final saved = savedOnline || task.draftPersisted;
    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Bản nháp',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: MelbelLayout.pageInsets(context, top: 20, bottom: 28),
            children: [
              MelbelChatBlock(
                expression: CatExpression.sleepy,
                tone: MelbelCardTone.amber,
                senderLabel: 'Melbel',
                badge: 'Bản nháp',
                title: saved ? 'Đã lưu bản nháp' : 'Bản nháp đang mở',
                titleIsHeader: true,
                message: savedOnline
                    ? 'Đã lưu trên máy chủ. Việc này chưa được xếp lịch.'
                    : task.draftPersisted
                        ? 'Đã lưu trên thiết bị. Việc này chưa được xếp lịch.'
                        : 'Chưa xác nhận được việc lưu. Bạn có thể quay lại chỉnh sửa.',
                mascotWidth: 58,
                mascotHeight: 54,
              ),
              const SizedBox(height: 14),
              MelbelCard(
                borderRadius: 24,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                        task.title.isEmpty
                            ? 'Công việc chưa đặt tên'
                            : task.title,
                        style: textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.w700,
                        )),
                    const SizedBox(height: 8),
                    Text(
                      'Hạn chót · ${task.formatDeadline(task.deadline)}',
                      style: textTheme.bodyMedium
                          ?.copyWith(color: palette.onSurfaceVariant),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              PrimaryCta(
                  label: 'Làm tiếp việc này',
                  icon: Icons.edit_note_rounded,
                  onPressed: () => Navigator.pop(context)),
              const SizedBox(height: 8),
              SecondaryCta(
                  label: 'Về trang chủ',
                  icon: Icons.home_outlined,
                  onPressed: () =>
                      Navigator.popUntil(context, (route) => route.isFirst)),
            ],
          ),
        ),
      ),
    );
  }
}
