import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'drafts_screen.dart';
import 'candidate_review_screen.dart';

class TaskSummaryScreen extends StatefulWidget {
  const TaskSummaryScreen({super.key});

  @override
  State<TaskSummaryScreen> createState() => _TaskSummaryScreenState();
}

class _TaskSummaryScreenState extends State<TaskSummaryScreen> {
  late final TextEditingController _scopeController;
  late final TextEditingController _clarificationController;
  bool _isSubmitting = false;

  @override
  void initState() {
    super.initState();
    _scopeController =
        TextEditingController(text: context.read<TaskProvider>().scope);
    _clarificationController = TextEditingController();
  }

  @override
  void dispose() {
    _scopeController.dispose();
    _clarificationController.dispose();
    super.dispose();
  }

  Future<void> _pickAvailability(TaskProvider task) async {
    final now = DateTime.now().toUtc().add(const Duration(hours: 7));
    final today = DateTime(now.year, now.month, now.day);
    final date = await showDatePicker(
      context: context,
      helpText: 'Chọn ngày bạn rảnh',
      firstDate: today,
      lastDate: DateTime(today.year + 2, today.month, today.day),
      initialDate: today,
    );
    if (date == null || !mounted) return;
    final start = await showTimePicker(
      context: context,
      helpText: 'Giờ bắt đầu (giờ Việt Nam)',
      initialTime: const TimeOfDay(hour: 18, minute: 0),
    );
    if (start == null || !mounted) return;
    final end = await showTimePicker(
      context: context,
      helpText: 'Giờ kết thúc (giờ Việt Nam)',
      initialTime: start,
    );
    if (end == null || !mounted) return;

    DateTime instant(TimeOfDay time) => DateTime.utc(
          date.year,
          date.month,
          date.day,
          time.hour - 7,
          time.minute,
        );
    final from = instant(start);
    final to = instant(end);
    final deadline = task.deadline;
    if (!to.isAfter(from) ||
        !to.isAfter(DateTime.now()) ||
        (deadline != null && to.isAfter(deadline))) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Chọn giờ kết thúc sau giờ bắt đầu và trước hạn chót.'),
        ),
      );
      return;
    }
    task.setAvailability(AvailabilitySlot(start: from, end: to));
  }

  Future<void> _continue() async {
    if (_isSubmitting) return;
    final task = context.read<TaskProvider>();
    if (task.isApiBusy) return;
    task.setScope(_scopeController.text.trim());
    setState(() => _isSubmitting = true);
    try {
      if (task.draft != null &&
          !task.canPlan &&
          _clarificationController.text.trim().isNotEmpty) {
        await task.submitClarification(_clarificationController.text);
        _clarificationController.clear();
      } else {
        await task.prepareDraftAndAnalyze();
      }
      if (!mounted) return;
      if (!task.canPlan) {
        final questions = task.draft?.clarificationIssues
            .where((issue) => issue['blocking'] == true)
            .map((i) => i['question'] ?? i['message'])
            .whereType<String>()
            .join('\n');
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(questions == null || questions.isEmpty
                ? 'Bổ sung thông tin trước khi lập kế hoạch.'
                : questions)));
        setState(() {});
        return;
      }
      await task.confirmDraftSummaryOnBackend();
      await task.generateCandidateOnBackend();
      if (!mounted) return;
      await Navigator.push(
          context,
          MaterialPageRoute<void>(
            builder: (_) => const CandidateReviewScreen(),
          ));
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Chưa cập nhật được bản nháp: $error')));
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  Future<void> _saveDraft() async {
    final task = context.read<TaskProvider>();
    if (_isSubmitting || task.isApiBusy) return;
    setState(() => _isSubmitting = true);
    task.setScope(_scopeController.text.trim());
    task.saveDraft();
    try {
      try {
        await task.saveDraftToBackend();
      } catch (error) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Bản nháp chưa đồng bộ máy chủ: $error')));
      }
      if (!mounted) return;
      await Navigator.push(context,
          MaterialPageRoute<void>(builder: (_) => const DraftSavedScreen()));
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Tóm tắt công việc', showBack: true),
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
                      decoration: BoxDecoration(
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
                            Text(
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
                                  ? 'Chưa có nội dung công việc'
                                  : task.title,
                              style: TextStyle(
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
                            Icon(
                              Icons.schedule,
                              size: 14,
                              color: MelbelColors.kTertiaryFixedDim,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              task.formatDeadline(task.deadline),
                              style: TextStyle(
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
                              decoration: BoxDecoration(
                                color: MelbelColors.kSurfaceContainerHigh,
                                shape: BoxShape.circle,
                              ),
                              child: Icon(
                                Icons.timelapse,
                                size: 14,
                                color: MelbelColors.kSecondary,
                              ),
                            ),
                            const SizedBox(width: 10),
                            Text(
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
                              decoration: BoxDecoration(
                                color: MelbelColors.kSurfaceContainerHigh,
                                shape: BoxShape.circle,
                              ),
                              child: Icon(
                                Icons.spa,
                                size: 14,
                                color: MelbelColors.kSecondary,
                              ),
                            ),
                            const SizedBox(width: 10),
                            Text(
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
                        Icon(
                          Icons.info_outline,
                          size: 16,
                          color: MelbelColors.kTertiaryFixedDim,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                            child: Text(
                          'Giờ rảnh: ${task.availabilitySummary}',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 13,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                        )),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Calming Spacer
            Center(
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
                  Text(
                    'Giờ rảnh cho công việc này',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    task.availabilitySummary,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 8),
                  TextButton(
                    onPressed: _isSubmitting || task.isApiBusy
                        ? null
                        : () => _pickAvailability(task),
                    child: Text(
                      task.draft?.summary['availability'] != null
                          ? 'Thay bằng một khung giờ khác'
                          : 'Chọn ngày và giờ rảnh',
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            for (final issue in task.draft?.clarificationIssues ?? const [])
              if (issue['code'] == 'AI_FALLBACK_NOTICE')
                Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Text(
                    issue['message'] as String,
                    style:
                        TextStyle(color: MelbelColors.kOnSurfaceVariant),
                  ),
                ),

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
                  Text(
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
                    style: TextStyle(
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
                        borderSide: BorderSide(
                          color: MelbelColors.kOutlineVariant,
                        ),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: BorderSide(
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

            if (task.draft != null && !task.canPlan) ...[
              const SizedBox(height: 16),
              MelbelCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Cùng làm rõ một lượt',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: MelbelColors.kOnSurface,
                      ),
                    ),
                    const SizedBox(height: 8),
                    for (final issue in task.draft!.clarificationIssues)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(
                          (issue['question'] ??
                                  issue['message'] ??
                                  'Cần thêm thông tin')
                              .toString(),
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 14,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ),
                    TextField(
                      controller: _clarificationController,
                      minLines: 2,
                      maxLines: 4,
                      decoration: const InputDecoration(
                        hintText: 'Gõ câu trả lời hoặc thông tin còn thiếu…',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 24),

            // Actions
            PrimaryCta(
              label: task.draft != null && !task.canPlan
                  ? 'Cập nhật thông tin'
                  : 'Xác nhận và tạo phương án',
              icon: Icons.arrow_forward,
              onPressed: _isSubmitting || task.isApiBusy ? null : _continue,
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu vào Hộp nháp',
              icon: Icons.drafts_outlined,
              height: 48,
              onPressed: _isSubmitting || task.isApiBusy ? null : _saveDraft,
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
  int _selectedOption = -1;

  Future<void> _resolve(TaskProvider task) async {
    if (_selectedOption == 0) {
      final now = DateTime.now();
      final day = await showDatePicker(
        context: context,
        initialDate: task.deadline ?? now.add(const Duration(days: 1)),
        firstDate: DateTime(now.year, now.month, now.day),
        lastDate: DateTime(9999, 12, 31),
        helpText: 'Chọn hạn chót mới',
      );
      if (day == null || !mounted) return;
      final time = await showTimePicker(
        context: context,
        initialTime: task.deadline == null
            ? const TimeOfDay(hour: 18, minute: 0)
            : TimeOfDay.fromDateTime(task.deadline!),
      );
      if (time == null || !mounted) return;
      final newDeadline =
          DateTime(day.year, day.month, day.day, time.hour, time.minute);
      if (!newDeadline.isAfter(DateTime.now())) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Chọn thời điểm trong tương lai nhé.')),
        );
        return;
      }
      task.setDeadline(newDeadline);
      Navigator.pop(context);
      return;
    }
    if (_selectedOption == 1) {
      Navigator.pop(context);
      return;
    }
    if (_selectedOption == 2) {
      task.saveDraft();
      await Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const DraftSavedScreen()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final issueText = task.draft?.clarificationIssues
            .map((issue) => issue['question'] ?? issue['message'])
            .whereType<String>()
            .join('\n') ??
        '';

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
                  decoration: BoxDecoration(
                    color: MelbelColors.kSecondary,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
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
                    children: [
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
                        size: 15,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Hạn chót: ${task.formatDeadline(task.deadline)}',
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
                    decoration: BoxDecoration(
                      color: MelbelColors.kTertiaryContainer,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      Icons.info,
                      size: 20,
                      color: MelbelColors.kOnTertiary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
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
                          issueText.isEmpty
                              ? 'Chưa có chi tiết xung đột từ máy chủ. Bạn có thể cập nhật thông tin hoặc lưu nháp để làm tiếp sau.'
                              : issueText,
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
            Text(
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
              label: 'Chọn hạn chót mới',
              subLabel: 'Chọn ngày và giờ phù hợp',
              isSelected: _selectedOption == 0,
              onTap: () => setState(() => _selectedOption = 0),
            ),
            const SizedBox(height: 10),

            _ConflictResolutionOption(
              label: 'Sửa thông tin giờ rảnh',
              subLabel: 'Quay lại bước tóm tắt để cập nhật',
              isSelected: _selectedOption == 1,
              onTap: () => setState(() => _selectedOption = 1),
            ),
            const SizedBox(height: 10),

            _ConflictResolutionOption(
              label: 'Lưu nháp, làm sau',
              subLabel: 'Tiếp tục khi bạn sẵn sàng',
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
                  Row(
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
                      side: BorderSide(
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
              onPressed: _selectedOption < 0 ? null : () => _resolve(task),
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu nháp, làm sau',
              icon: Icons.archive_outlined,
              height: 48,
              onPressed: () async {
                task.saveDraft();
                await Navigator.push(
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
                  style: TextStyle(
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
