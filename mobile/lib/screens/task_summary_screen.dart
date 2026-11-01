import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'drafts_screen.dart';
import 'task_ready_screen.dart';

class TaskSummaryScreen extends StatefulWidget {
  const TaskSummaryScreen({super.key});

  @override
  State<TaskSummaryScreen> createState() => _TaskSummaryScreenState();
}

class _TaskSummaryScreenState extends State<TaskSummaryScreen> {
  late final TextEditingController _scopeController;

  @override
  void initState() {
    super.initState();
    _scopeController =
        TextEditingController(text: context.read<TaskProvider>().scope);
  }

  @override
  void dispose() {
    _scopeController.dispose();
    super.dispose();
  }

  Future<void> _continue() async {
    final task = context.read<TaskProvider>();
    if (_scopeController.text.trim().isNotEmpty) {
      task.setScope(_scopeController.text.trim());
    }
    if (task.scope.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
            content: Text('Bổ sung phạm vi công việc trước khi xác nhận.')),
      );
      return;
    }

    try {
      if (task.draft != null) {
        await task.updateDraftOnBackend();
        if (!task.canPlan) {
          final issues = task.draft?.clarificationIssues ?? const [];
          final question = issues.isNotEmpty
              ? issues.map((issue) => issue['question']).join('\n')
              : 'Thông tin chưa đủ để lập kế hoạch.';
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(question.toString())),
          );
          return;
        }
      }
      if (!mounted) return;
      Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const TaskReadyScreen()),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Chưa cập nhật được bản nháp trên máy chủ: $error'),
          action: SnackBarAction(
            label: 'Tiếp tục',
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute<void>(
                    builder: (_) => const TaskReadyScreen()),
              );
            },
          ),
        ),
      );
    }
  }

  Future<void> _saveDraft() async {
    final task = context.read<TaskProvider>();
    if (_scopeController.text.trim().isNotEmpty) {
      task.setScope(_scopeController.text.trim());
    }
    task.saveDraft();
    try {
      await task.saveDraftToBackend();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Đã lưu nháp an toàn trên thiết bị.'),
          duration: Duration(seconds: 2),
        ),
      );
    }
    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const DraftSavedScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Task Summary', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Subtle Moon Phase Graphic Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    Container(
                      width: 8,
                      height: 8,
                      decoration: const BoxDecoration(
                        color: MelbelColors.kSecondary,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 8),
                    const Eyebrow('Không gian êm dịu'),
                  ],
                ),
                Row(
                  children: [
                    _moonDot(6, MelbelColors.kOutlineVariant),
                    const SizedBox(width: 6),
                    _moonDot(8, MelbelColors.kOutlineVariant),
                    const SizedBox(width: 6),
                    _moonDot(10, MelbelColors.kPrimaryContainer),
                    const SizedBox(width: 6),
                    _moonDot(8, MelbelColors.kOutlineVariant),
                    const SizedBox(width: 6),
                    _moonDot(6, MelbelColors.kOutlineVariant),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Section 1: Task Summary Card (D-17)
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
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'Mục tiêu hiện tại',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 12,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kSecondary,
                              ),
                            ),
                            const SizedBox(height: 4),
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
                          ],
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 5),
                        decoration: BoxDecoration(
                          color: MelbelColors.kSurfaceContainerHigh,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(
                              Icons.schedule,
                              size: 14,
                              color: MelbelColors.kTertiaryFixedDim,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              task.formatDeadline(task.deadline),
                              style: const TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 12,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kTertiaryFixedDim,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),

                  // Structured Plan Breakdown
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurface,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Column(
                      children: [
                        Row(
                          children: [
                            Container(
                              width: 20,
                              height: 20,
                              decoration: const BoxDecoration(
                                color: MelbelColors.kSurfaceContainerHigh,
                                shape: BoxShape.circle,
                              ),
                              child: const Icon(
                                Icons.timelapse,
                                size: 14,
                                color: MelbelColors.kSecondary,
                              ),
                            ),
                            const SizedBox(width: 10),
                            const Text(
                              'Phiên tập trung sẽ do backend đề xuất',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 13,
                                color: MelbelColors.kOnSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        Row(
                          children: [
                            Container(
                              width: 20,
                              height: 20,
                              decoration: const BoxDecoration(
                                color: MelbelColors.kSurfaceContainerHigh,
                                shape: BoxShape.circle,
                              ),
                              child: const Icon(
                                Icons.spa,
                                size: 14,
                                color: MelbelColors.kSecondary,
                              ),
                            ),
                            const SizedBox(width: 10),
                            const Text(
                              'Có thời gian nghỉ ngắn 5p giữa phiên',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 13,
                                color: MelbelColors.kOnSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Missing Info Badge
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      children: [
                        const Icon(
                          Icons.info_outline,
                          size: 16,
                          color: MelbelColors.kTertiaryFixedDim,
                        ),
                        const SizedBox(width: 8),
                        Text(
                          'Giờ rảnh theo cài đặt: ${task.serverAvailabilitySummary}',
                          style: const TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 13,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Calming Spacer
            const Center(
              child: Text(
                'Cứ thong thả, chọn lúc bạn thấy thoải mái nhất',
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 12,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
              ),
            ),
            const SizedBox(height: 16),

            // Availability is read from the confirmed backend profile; this screen
            // does not invent a local-only time window.
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
                    'Giờ rảnh đã xác nhận',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    task.serverAvailabilitySummary,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Muốn đổi giờ rảnh, cập nhật trong Cài đặt trước khi lập lịch.',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Section 3: Scope Input Box (Bổ sung phạm vi)
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
                    'Phạm vi / Kết quả mong muốn:',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: _scopeController,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurface,
                    ),
                    decoration: InputDecoration(
                      hintText: 'Ví dụ: 5 trang Word và biểu đồ tổng kết...',
                      hintStyle: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 13,
                        color: MelbelColors.kOnSurfaceVariant
                            .withValues(alpha: 0.5),
                      ),
                      filled: true,
                      fillColor: MelbelColors.kSurfaceContainerLow,
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: 14, vertical: 12),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(
                          color: MelbelColors.kOutlineVariant,
                        ),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(
                          color: MelbelColors.kPrimaryContainer,
                        ),
                      ),
                    ),
                    onChanged: (val) => task.setScope(val),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Actions
            PrimaryCta(
              label: 'Tiếp tục',
              icon: Icons.arrow_forward,
              onPressed: _continue,
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu vào Hộp nháp',
              icon: Icons.drafts_outlined,
              height: 48,
              onPressed: _saveDraft,
            ),
          ],
        ),
      ),
    );
  }

  Widget _moonDot(double size, Color color) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
      ),
    );
  }
}

// -------------------------------------------------------------
// D-18 Unclear or Conflicting Info Screen (TimeConflictScreen)
// -------------------------------------------------------------
class TimeConflictScreen extends StatefulWidget {
  const TimeConflictScreen({super.key});

  @override
  State<TimeConflictScreen> createState() => _TimeConflictScreenState();
}

class _TimeConflictScreenState extends State<TimeConflictScreen> {
  int _selectedOption = 0;

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();

    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Clarification Review',
        showBack: true,
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Mindful Breathing Note
            Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: const BoxDecoration(
                    color: MelbelColors.kSecondary,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                const Text(
                  'Không sao cả, hãy điều chỉnh nhịp một chút nhé',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 13,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // 1. Previous Requirements Card
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
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: const [
                      Eyebrow('Thông tin đã lưu'),
                      Icon(
                        Icons.lock_outline,
                        size: 16,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
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
                        size: 15,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Backend sẽ tính lại theo hạn chót: ${task.formatDeadline(task.deadline)}',
                        style: const TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 13,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // 2. Conflict Callout Card (Soft Amber, Calm & Non-Judgmental)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerHigh,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: MelbelColors.kTertiaryFixedDim.withValues(alpha: 0.4),
                ),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 32,
                    height: 32,
                    decoration: const BoxDecoration(
                      color: MelbelColors.kTertiaryContainer,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.info,
                      size: 20,
                      color: MelbelColors.kOnTertiary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Điểm cần đồng điệu lại',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                        ),
                        SizedBox(height: 4),
                        Text(
                          'Khung giờ rảnh bạn chọn (16:30 - 17:30) bị muộn hơn so với hạn chót (17:00).',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 14,
                            color: MelbelColors.kOnSurface,
                            height: 1.4,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // 3. ONE Clear Question & Quick Direct Resolution
            const Text(
              'Hạn hoặc khoảng thời gian bạn muốn hoàn thành là khi nào?',
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: MelbelColors.kOnSurface,
              ),
            ),
            const SizedBox(height: 14),

            _ConflictResolutionOption(
              label: 'Dời hạn sang 18:00',
              subLabel: '+60 phút thư thả',
              isSelected: _selectedOption == 0,
              onTap: () => setState(() => _selectedOption = 0),
            ),
            const SizedBox(height: 10),

            _ConflictResolutionOption(
              label: 'Làm sớm hơn: 15:00 - 16:30',
              subLabel: 'Trước hạn 30p',
              isSelected: _selectedOption == 1,
              onTap: () => setState(() => _selectedOption = 1),
            ),
            const SizedBox(height: 10),

            _ConflictResolutionOption(
              label: 'Bắt đầu ngay bây giờ',
              subLabel: 'Sẵn sàng ngay',
              isSelected: _selectedOption == 2,
              onTap: () => setState(() => _selectedOption = 2),
            ),
            const SizedBox(height: 14),

            // Speech / Free Input Alternative
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerLow,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Row(
                    children: [
                      Icon(
                        Icons.mic,
                        size: 20,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                      SizedBox(width: 8),
                      Text(
                        'Nói để chỉnh',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 13,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      backgroundColor: MelbelColors.kSurfaceContainer,
                      foregroundColor: MelbelColors.kOnSurface,
                      side: const BorderSide(
                        color: MelbelColors.kOutlineVariant,
                      ),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                    onPressed: () {},
                    icon: const Icon(Icons.graphic_eq, size: 16),
                    label: const Text(
                      'Bật thu âm',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 12,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Confirm Resolution Button
            PrimaryCta(
              label: 'Xác nhận mốc thời gian',
              icon: Icons.check,
              onPressed: () {
                final now = DateTime.now();
                if (_selectedOption == 0) {
                  task.setDeadline(
                      DateTime(now.year, now.month, now.day, 18, 0));
                } else if (_selectedOption == 1 &&
                    task.availabilityOptions.length > 1) {
                  task.setAvailability(task.availabilityOptions[1]);
                }
                Navigator.pushReplacement(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => const TaskReadyScreen(),
                  ),
                );
              },
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu nháp, làm sau',
              icon: Icons.archive_outlined,
              height: 48,
              onPressed: () {
                task.saveDraft();
                Navigator.push(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => const DraftSavedScreen(),
                  ),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _ConflictResolutionOption extends StatelessWidget {
  const _ConflictResolutionOption({
    required this.label,
    required this.subLabel,
    required this.isSelected,
    required this.onTap,
  });

  final String label;
  final String subLabel;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: isSelected
              ? MelbelColors.kSurfaceContainerHighest
              : MelbelColors.kSurfaceContainer,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isSelected
                ? MelbelColors.kPrimaryContainer
                : MelbelColors.kOutlineVariant.withValues(alpha: 0.5),
          ),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Row(
              children: [
                Container(
                  width: 20,
                  height: 20,
                  decoration: BoxDecoration(
                    color: MelbelColors.kSurfaceContainerHighest,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: isSelected
                          ? MelbelColors.kPrimaryContainer
                          : MelbelColors.kOutline,
                    ),
                  ),
                  child: Center(
                    child: Container(
                      width: 8,
                      height: 8,
                      decoration: BoxDecoration(
                        color: isSelected
                            ? MelbelColors.kPrimaryContainer
                            : Colors.transparent,
                        shape: BoxShape.circle,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Text(
                  label,
                  style: const TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: MelbelColors.kOnSurface,
                  ),
                ),
              ],
            ),
            Text(
              subLabel,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 12,
                color: isSelected
                    ? MelbelColors.kSecondary
                    : MelbelColors.kOnSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
