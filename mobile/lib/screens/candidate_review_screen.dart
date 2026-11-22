import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'task_summary_screen.dart';
import 'uf2_screens.dart'
    show
        DecompositionChoiceScreen,
        FeasibilityAdjustment,
        FeasibilityScreen,
        PlanLockedScreen,
        UnconfirmedPlanScreen,
        VoiceAssistantModal;

class CandidateReviewScreen extends StatelessWidget {
  const CandidateReviewScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final candidate = task.candidate;
    if (candidate == null) {
      return Scaffold(
        appBar: const MelbelAppBar(title: 'Kế hoạch', showBack: true),
        body: Center(
            child: Text(task.apiError ?? 'Chưa có phương án từ máy chủ.')),
      );
    }
    final feasibility = candidate.feasibility;
    final deficitSeconds = feasibility['deficit_seconds'] as int?;
    final scheduledKeys =
        candidate.sessions.map((session) => session['client_step_key']).toSet();
    final unscheduledSeconds = feasibility['unscheduled_seconds'] as int? ?? 0;
    final unscheduledKeys =
        (feasibility['unscheduled_step_keys'] as List?)?.toSet() ?? const {};
    final stepsWithoutFullSchedule = candidate.steps
        .where((step) =>
            !scheduledKeys.contains(step['client_step_key']) ||
            unscheduledKeys.contains(step['client_step_key']))
        .map((step) => step['title'])
        .whereType<String>()
        .toList();
    final unexpandedScope = candidate.unexpandedScope?.trim() ?? '';
    final hasRemainder =
        unexpandedScope.isNotEmpty || stepsWithoutFullSchedule.isNotEmpty;
    final hasPastSession = candidate.sessions.any((session) {
      final value =
          DateTime.tryParse(session['scheduled_start'] as String? ?? '');
      return value == null || !value.isAfter(DateTime.now());
    });
    final canConfirm =
        candidate.isFeasible && !hasPastSession && !task.isApiBusy;
    final title = task.title.isEmpty ? 'Công việc' : task.title;
    final horizon = candidate.raw['planning_horizon'] as Map?;
    final windowStart =
        DateTime.tryParse(horizon?['start_at'] as String? ?? '');
    final windowEnd = DateTime.tryParse(horizon?['end_at'] as String? ?? '');
    final dates = MaterialLocalizations.of(context);

    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final textTheme = Theme.of(context).textTheme;
    final firstMicroTask = candidate.sessions
        .map((session) => session['micro_task_text'])
        .whereType<String>()
        .map((text) => text.trim())
        .where((text) => text.isNotEmpty)
        .firstOrNull;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Kế hoạch đề xuất',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          child: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 28),
          children: [
            const PlanningStageHeader(step: 3, label: 'Xem phương án'),
            const SizedBox(height: 16),
            MelbelEntrance(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: textTheme.headlineSmall?.copyWith(
                      color: palette.onSurface,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 6,
                    children: [
                      MelbelStatusPill(
                        label:
                            'Hạn chót: ${task.formatDeadline(task.deadline)}',
                        icon: Icons.flag_outlined,
                        tone: MelbelCardTone.muted,
                      ),
                      if (windowStart != null && windowEnd != null)
                        MelbelStatusPill(
                          label:
                              'Khoảng lập lịch: ${dates.formatShortDate(windowStart.toLocal())} – ${dates.formatShortDate(windowEnd.toLocal())}',
                          icon: Icons.date_range_outlined,
                          tone: MelbelCardTone.muted,
                        ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            MelbelCard(
              tone: candidate.isFeasible && !candidate.stale && !hasPastSession
                  ? MelbelCardTone.sage
                  : MelbelCardTone.amber,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        candidate.isFeasible
                            ? Icons.check_circle_outline_rounded
                            : Icons.info_outline_rounded,
                        color:
                            candidate.isFeasible ? palette.sage : palette.amber,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          candidate.isFeasible && hasRemainder
                              ? 'Phần có lịch đã xếp được'
                              : _feasibilityLabel(candidate.feasibilityStatus),
                          style: textTheme.titleSmall?.copyWith(
                            color: candidate.isFeasible
                                ? palette.onSageSoft
                                : palette.onAmberSoft,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Phiên bản ${candidate.reviewRevision}',
                    style: textTheme.bodySmall?.copyWith(
                      color: candidate.isFeasible
                          ? palette.onSageSoft.withValues(alpha: 0.8)
                          : palette.onAmberSoft.withValues(alpha: 0.8),
                    ),
                  ),
                  if (!candidate.isFeasible &&
                      deficitSeconds != null &&
                      deficitSeconds > 0) ...[
                    const SizedBox(height: 8),
                    Text(
                      'Thiếu tối thiểu ${_minutes(deficitSeconds)} phút trước hạn chót.',
                      style: textTheme.bodyMedium?.copyWith(
                        color: palette.onAmberSoft,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                  if (!candidate.isFeasible &&
                      (deficitSeconds ?? 0) <= 0 &&
                      unscheduledSeconds > 0) ...[
                    const SizedBox(height: 8),
                    Text(
                      'Còn ${_minutes(unscheduledSeconds)} phút chưa xếp vào giờ rảnh.',
                      style: textTheme.bodyMedium?.copyWith(
                        color: palette.onAmberSoft,
                      ),
                    ),
                  ],
                  if (candidate.stale) ...[
                    const SizedBox(height: 8),
                    Text(
                      'Nguồn dữ liệu đã thay đổi. Hãy tạo lại phương án trước khi xác nhận.',
                      style: textTheme.bodySmall?.copyWith(
                        color: palette.onAmberSoft,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                  if (hasPastSession) ...[
                    const SizedBox(height: 8),
                    Text(
                      'Có phiên đã qua hoặc chưa có giờ bắt đầu. Hãy chỉnh lại lịch.',
                      style: textTheme.bodySmall?.copyWith(
                        color: palette.onAmberSoft,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 14),
            if (firstMicroTask != null) ...[
              MelbelCard(
                tone: isDark ? MelbelCardTone.sage : MelbelCardTone.amber,
                borderRadius: 26,
                padding: const EdgeInsets.all(18),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: (isDark ? palette.sage : palette.amber)
                            .withValues(alpha: 0.16),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      alignment: Alignment.center,
                      child: Icon(
                        Icons.rocket_launch_outlined,
                        size: 22,
                        color: isDark ? palette.sage : palette.amber,
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Eyebrow(
                            'KHỞI ĐỘNG 2 PHÚT ĐẦU TIÊN',
                            color: isDark
                                ? palette.onSageSoft
                                : palette.onAmberSoft,
                          ),
                          const SizedBox(height: 6),
                          Text(
                            firstMicroTask,
                            style: textTheme.titleSmall?.copyWith(
                              fontWeight: FontWeight.w700,
                              color: isDark
                                  ? palette.onSageSoft
                                  : palette.onAmberSoft,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            'Chỉ 2 phút để vào nhịp · không tính vào thời lượng phiên chính',
                            style: textTheme.bodySmall?.copyWith(
                              color: (isDark
                                      ? palette.onSageSoft
                                      : palette.onAmberSoft)
                                  .withValues(alpha: 0.78),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
            ],
            if (hasRemainder) ...[
              MelbelCard(
                tone: MelbelCardTone.amber,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Phần chưa có lịch',
                      style: textTheme.titleSmall?.copyWith(
                        color: palette.onAmberSoft,
                      ),
                    ),
                    const SizedBox(height: 6),
                    if (unexpandedScope.isNotEmpty)
                      Text(
                        'Chưa chia thành bước: $unexpandedScope',
                        style: textTheme.bodyMedium?.copyWith(
                          color: palette.onAmberSoft,
                        ),
                      ),
                    if (stepsWithoutFullSchedule.isNotEmpty)
                      Text(
                        'Bước chưa xếp đủ: ${stepsWithoutFullSchedule.join(', ')}',
                        style: textTheme.bodyMedium?.copyWith(
                          color: palette.onAmberSoft,
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
            ],
            Wrap(
              spacing: 10,
              runSpacing: 8,
              children: [
                ActionChip(
                  avatar: Icon(Icons.tune_rounded,
                      size: 18, color: palette.sage),
                  label: const Text('Tùy chỉnh theo ý tôi'),
                  backgroundColor: palette.surface,
                  onPressed: task.isApiBusy
                      ? null
                      : () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => const DecompositionChoiceScreen(
                                returnToReview: true,
                              ),
                            ),
                          ),
                ),
                if (candidate.steps.isNotEmpty)
                  ActionChip(
                    avatar: Icon(Icons.mic_none_rounded,
                        size: 18, color: palette.sage),
                    label: const Text('Tùy chỉnh bằng giọng nói'),
                    backgroundColor: palette.surface,
                    onPressed:
                        task.isApiBusy ? null : () => _adjustByVoice(context),
                  ),
              ],
            ),
            if (candidate.steps.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Eyebrow('Bước đầu tiên'),
              const SizedBox(height: 8),
              _stepCard(
                  context,
                  candidate.steps.first,
                  0,
                  candidate.sessions.where((session) =>
                      session['client_step_key'] ==
                      candidate.steps.first['client_step_key'])),
            ],
            if (candidate.steps.length > 1)
              ExpansionTile(
                key: const PageStorageKey('proposal-other-steps'),
                title: Text('Xem ${candidate.steps.length - 1} bước còn lại'),
                children: [
                  for (var i = 1; i < candidate.steps.length; i++)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _stepCard(
                          context,
                          candidate.steps[i],
                          i,
                          candidate.sessions.where((session) =>
                              session['client_step_key'] ==
                              candidate.steps[i]['client_step_key'])),
                    ),
                ],
              ),
            if (candidate.sessions.isNotEmpty)
              ExpansionTile(
                key: const PageStorageKey('proposal-sessions'),
                title: Text('Xem lịch ${candidate.sessions.length} phiên'),
                children: [
                  for (final session in candidate.sessions)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _sessionCard(context, session),
                    ),
                ],
              ),
            if (candidate.isPartiallyDecomposed) ...[
              const SizedBox(height: 4),
              MelbelCard(
                tone: MelbelCardTone.muted,
                child: Text(
                  'Phần còn lại sẽ được chia bước khi bạn cập nhật kế hoạch.',
                  style: textTheme.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
                ),
              ),
            ],
            const SizedBox(height: 16),
            if (!candidate.isFeasible) ...[
              SecondaryCta(
                label:
                    task.isApiBusy ? 'Đang tính lại…' : 'Xem cách điều chỉnh',
                icon: Icons.edit_calendar_outlined,
                onPressed: task.isApiBusy
                    ? null
                    : () => _openFeasibility(context, deficitSeconds),
              ),
              const SizedBox(height: 10),
            ],
            PrimaryCta(
              label: task.isApiBusy
                  ? 'Đang xác nhận…'
                  : hasRemainder
                      ? 'Chốt phần đã xếp lịch'
                      : 'Chốt kế hoạch',
              icon: task.isApiBusy
                  ? Icons.hourglass_top_rounded
                  : Icons.lock_outline_rounded,
              onPressed: canConfirm ? () => _confirm(context) : null,
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu phương án chưa chốt',
              onPressed: task.isApiBusy
                  ? null
                  : () {
                      task.savePlanForLater();
                      Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                          builder: (_) => const UnconfirmedPlanScreen(),
                        ),
                      );
                    },
            ),
            if (task.apiError != null) ...[
              const SizedBox(height: 12),
              MelbelCard(
                tone: MelbelCardTone.amber,
                padding: const EdgeInsets.all(12),
                child: Text(
                  task.apiError!,
                  style: textTheme.bodySmall?.copyWith(
                    color: palette.onAmberSoft,
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    ),
    );
  }

  static String _feasibilityLabel(String status) => switch (status) {
        'feasible' => 'Đủ thời gian trước hạn chót',
        'infeasible' => 'Chưa đủ thời gian trước hạn chót',
        'search_inconclusive' => 'Chưa thể kết luận khả năng xếp lịch',
        _ => 'Trạng thái khả thi: $status',
      };

  static int _minutes(int seconds) => (seconds / 60).ceil();

  Widget _stepCard(
    BuildContext context,
    Map<String, dynamic> step,
    int index,
    Iterable<Map<String, dynamic>> sessions,
  ) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final seconds = step['estimated_base_seconds'] as int? ?? 0;
    final output = step['expected_output'] as String?;
    final firstSession = sessions.firstOrNull;
    final microTask = firstSession?['micro_task_text'] as String?;

    return MelbelCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CircleAvatar(
                radius: 15,
                backgroundColor: palette.sageSoft,
                foregroundColor: palette.onSageSoft,
                child: Text(
                  '${index + 1}',
                  style: textTheme.labelLarge?.copyWith(
                    color: palette.onSageSoft,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  step['title'] as String? ?? 'Bước chưa có tên',
                  style: textTheme.titleSmall?.copyWith(
                    color: palette.onSurface,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'Chỉnh bước',
                onPressed: () => _editStep(context, step),
                icon: Icon(
                  Icons.edit_outlined,
                  color: palette.onSurfaceVariant,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          const Eyebrow('Đầu ra'),
          const SizedBox(height: 4),
          Text(
            output == null || output.trim().isEmpty
                ? 'Chưa có kết quả đầu ra'
                : output,
            style: textTheme.bodyMedium,
          ),
          if (microTask != null && microTask.trim().isNotEmpty) ...[
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: palette.sageSoft,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.spa_outlined, size: 17, color: palette.sage),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Khởi động 2 phút: $microTask',
                      style: textTheme.bodySmall?.copyWith(
                        color: palette.onSageSoft,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              if (sessions.isNotEmpty)
                MelbelStatusPill(
                  label: '${sessions.length} phiên đề xuất',
                  tone: MelbelCardTone.sage,
                ),
              MelbelStatusPill(
                label:
                    'Ước tính gốc: ${_minutes(seconds)} phút · đệm ${step['buffer_multiplier'] ?? '—'}',
                tone: MelbelCardTone.muted,
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _sessionCard(BuildContext context, Map<String, dynamic> session) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final start =
        DateTime.tryParse(session['scheduled_start'] as String? ?? '');
    final end = DateTime.tryParse(session['scheduled_end'] as String? ?? '');
    String time(DateTime? value) => value == null
        ? 'Chưa xếp giờ'
        : '${value.toLocal().day.toString().padLeft(2, '0')}/${value.toLocal().month.toString().padLeft(2, '0')} ${value.toLocal().hour.toString().padLeft(2, '0')}:${value.toLocal().minute.toString().padLeft(2, '0')}';

    return MelbelCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              MelbelStatusPill(
                label: '${time(start)} – ${time(end)}',
                icon: Icons.schedule_rounded,
                tone: MelbelCardTone.sage,
              ),
              MelbelStatusPill(
                label:
                    'Làm ${_minutes(session['work_seconds'] as int? ?? 0)}p · nghỉ ${_minutes(session['rest_seconds'] as int? ?? 0)}p',
                tone: MelbelCardTone.muted,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Khởi động 2 phút: ${session['micro_task_text'] ?? 'Chưa có gợi ý'}',
            style: textTheme.bodySmall?.copyWith(
              color: palette.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _editStep(
    BuildContext context,
    Map<String, dynamic> step,
  ) async {
    final task = context.read<TaskProvider>();
    var title = step['title'] as String? ?? '';
    var output = step['expected_output'] as String? ?? '';
    var minutes = ((step['estimated_base_seconds'] as int? ?? 1500) / 60)
        .round()
        .toString();
    final form = GlobalKey<FormState>();
    final save = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(
            20,
            8,
            20,
            MediaQuery.viewInsetsOf(sheetContext).bottom + 20,
          ),
          child: Form(
            key: form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Chỉnh bước kế hoạch',
                    style: Theme.of(sheetContext).textTheme.titleLarge),
                const SizedBox(height: 14),
                TextFormField(
                  initialValue: title,
                  decoration: const InputDecoration(labelText: 'Tên bước'),
                  onChanged: (value) => title = value,
                  validator: (value) =>
                      (value ?? '').trim().isEmpty ? 'Nhập tên bước.' : null,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  initialValue: output,
                  decoration:
                      const InputDecoration(labelText: 'Kết quả mong muốn'),
                  maxLines: null,
                  onChanged: (value) => output = value,
                  validator: (value) => (value ?? '').trim().isEmpty
                      ? 'Nhập kết quả bạn muốn đạt.'
                      : null,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  initialValue: minutes,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                      labelText: 'Thời lượng ước tính (phút)'),
                  onChanged: (value) => minutes = value,
                  validator: (value) =>
                      (int.tryParse(value?.trim() ?? '') ?? 0) < 1
                          ? 'Nhập số phút từ 1 trở lên.'
                          : null,
                ),
                const SizedBox(height: 16),
                PrimaryCta(
                  label: 'Gửi thay đổi',
                  onPressed: () {
                    if (form.currentState!.validate())
                      Navigator.pop(sheetContext, true);
                  },
                ),
                const SizedBox(height: 8),
                SecondaryCta(
                  label: 'Để sau',
                  onPressed: () => Navigator.pop(sheetContext, false),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (save != true || !context.mounted) return;
    try {
      await task.editCandidateStep(
        clientStepKey: step['client_step_key'] as String,
        title: title.trim(),
        expectedOutput: output.trim(),
        estimatedBaseSeconds: int.parse(minutes.trim()) * 60,
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa sửa được phương án: $error')),
        );
      }
    }
  }

  Future<void> _adjustByVoice(BuildContext context) async {
    final task = context.read<TaskProvider>();
    final request = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => VoiceAssistantModal(
        onApply: (value) => Navigator.pop(sheetContext, value),
      ),
    );
    if (request == null || !context.mounted || task.isApiBusy) return;
    try {
      await task.generateCandidateOnBackend(stepProposal: request);
    } catch (_) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Chưa tạo được phương án mới.')),
      );
    }
  }

  Future<void> _confirm(BuildContext context) async {
    final task = context.read<TaskProvider>();
    try {
      await task.confirmCandidateOnBackend();
      if (!context.mounted) return;
      await Navigator.push(context,
          MaterialPageRoute<void>(builder: (_) => const PlanLockedScreen()));
    } catch (error) {
      if (context.mounted)
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Chưa chốt được kế hoạch: $error')));
    }
  }

  Future<void> _openFeasibility(
      BuildContext context, int? deficitSeconds) async {
    final choice = await Navigator.push<FeasibilityAdjustment>(
      context,
      MaterialPageRoute(
        builder: (_) => FeasibilityScreen(
          deficitMinutes:
              deficitSeconds == null ? null : (deficitSeconds / 60).ceil(),
        ),
      ),
    );
    if (choice == null || !context.mounted) return;
    if (choice == FeasibilityAdjustment.deadline) {
      await _reviseDeadline(context);
      return;
    }
    await Navigator.push<bool>(
      context,
      MaterialPageRoute(
        builder: (_) => const TaskSummaryScreen(returnToReview: true),
      ),
    );
  }

  Future<void> _reviseDeadline(BuildContext context) async {
    final task = context.read<TaskProvider>();
    final current = task.deadline;
    final initial = current != null && current.isAfter(DateTime.now())
        ? current
        : DateTime.now().add(const Duration(days: 1));
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 365)),
      helpText: 'Chọn ngày hạn mới',
    );
    if (picked == null || !context.mounted) return;
    final revised = DateTime(
      picked.year,
      picked.month,
      picked.day,
      current?.hour ?? 18,
      current?.minute ?? 0,
    );
    try {
      await task.reviseDeadlineAndRegenerate(revised);
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa tính lại được: $error')),
        );
      }
    }
  }
}
