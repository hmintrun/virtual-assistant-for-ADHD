import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'uf2_screens.dart'
    show DecompositionChoiceScreen, PlanLockedScreen, UnconfirmedPlanScreen;

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

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Kế hoạch đề xuất', showBack: true),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
          children: [
            const Eyebrow('UF2 · ĐỀ XUẤT CHƯA CHỐT'),
            const SizedBox(height: 8),
            Text(title,
                style: TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kOnSurface)),
            const SizedBox(height: 6),
            Text('Hạn chót: ${task.formatDeadline(task.deadline)}',
                style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
            const SizedBox(height: 14),
            MelbelCard(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                  Row(children: [
                    Icon(
                        candidate.isFeasible
                            ? Icons.check_circle_outline
                            : Icons.info_outline,
                        color: candidate.isFeasible
                            ? MelbelColors.kSecondary
                            : MelbelColors.kTertiaryFixedDim),
                    const SizedBox(width: 8),
                    Expanded(
                        child: Text(
                            candidate.isFeasible && hasRemainder
                                ? 'Phần có lịch đã xếp được'
                                : _feasibilityLabel(
                                    candidate.feasibilityStatus),
                            style: TextStyle(
                                color: MelbelColors.kOnSurface,
                                fontWeight: FontWeight.w600)))
                  ]),
                  const SizedBox(height: 8),
                  Text(
                      'Mã phương án ${candidate.candidateId} · bản ${candidate.reviewRevision}',
                      style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
                  if (deficitSeconds != null) ...[
                    const SizedBox(height: 8),
                    Text(
                        'Thiếu tối thiểu ${_minutes(deficitSeconds)} phút theo kết quả backend.',
                        style:
                            TextStyle(color: MelbelColors.kOnSurfaceVariant)),
                  ],
                  if (candidate.stale) ...[
                    const SizedBox(height: 8),
                    Text(
                        'Nguồn dữ liệu đã thay đổi. Hãy tạo lại phương án trước khi xác nhận.',
                        style:
                            TextStyle(color: MelbelColors.kTertiaryFixedDim)),
                  ],
                  if (hasPastSession) ...[
                    const SizedBox(height: 8),
                    Text(
                        'Backend đang trả phiên đã qua hoặc thiếu giờ bắt đầu; chưa thể chốt lịch này.',
                        style:
                            TextStyle(color: MelbelColors.kTertiaryFixedDim)),
                  ],
                ])),
            const SizedBox(height: 14),
            if (hasRemainder) ...[
              MelbelCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Phần chưa có lịch',
                        style: TextStyle(fontWeight: FontWeight.w600)),
                    if (unexpandedScope.isNotEmpty)
                      Text('Chưa chia thành bước: $unexpandedScope'),
                    if (stepsWithoutFullSchedule.isNotEmpty)
                      Text(
                        'Bước chưa xếp đủ: ${stepsWithoutFullSchedule.join(', ')}',
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
            ],
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: task.isApiBusy
                    ? null
                    : () => Navigator.push(
                          context,
                          MaterialPageRoute<void>(
                            builder: (_) => const DecompositionChoiceScreen(),
                          ),
                        ),
                icon: const Icon(Icons.tune),
                label: const Text('Tùy chỉnh theo ý tôi'),
              ),
            ),
            for (var i = 0; i < candidate.steps.length; i++) ...[
              _stepCard(context, candidate.steps[i], i),
              const SizedBox(height: 10),
            ],
            if (candidate.sessions.isNotEmpty) ...[
              const Eyebrow('CÁC PHIÊN DO BACKEND XẾP'),
              const SizedBox(height: 8),
              for (final session in candidate.sessions) ...[
                _sessionCard(session),
                const SizedBox(height: 8),
              ],
            ],
            if (candidate.planningParts
                .any((part) => part['decomposition_state'] != 'complete'))
              MelbelCard(
                  child: Text(
                      'Một số phần nằm ngoài phạm vi phân rã chi tiết hiện tại; có thể tiếp tục khi kế hoạch được cập nhật.',
                      style: TextStyle(color: MelbelColors.kOnSurfaceVariant))),
            const SizedBox(height: 14),
            if (!candidate.isFeasible)
              SecondaryCta(
                label: task.isApiBusy
                    ? 'Đang tính lại…'
                    : 'Chọn hạn mới và tính lại',
                icon: Icons.edit_calendar,
                onPressed:
                    task.isApiBusy ? null : () => _reviseDeadline(context),
              ),
            if (!candidate.isFeasible) const SizedBox(height: 8),
            PrimaryCta(
              label: task.isApiBusy
                  ? 'Đang xác nhận…'
                  : hasRemainder
                      ? 'Chốt phần đã xếp lịch'
                      : 'Chốt kế hoạch',
              icon: task.isApiBusy ? Icons.hourglass_top : Icons.lock_outline,
              onPressed: canConfirm ? () => _confirm(context) : null,
            ),
            const SizedBox(height: 8),
            SecondaryCta(
                label: 'Lưu phương án chưa chốt',
                onPressed: task.isApiBusy
                    ? null
                    : () {
                        task.savePlanForLater();
                        Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                                builder: (_) => const UnconfirmedPlanScreen()));
                      }),
            if (task.apiError != null) ...[
              const SizedBox(height: 10),
              Text(task.apiError!,
                  style: TextStyle(color: MelbelColors.kTertiaryFixedDim)),
            ],
          ],
        ),
      ),
    );
  }

  static String _feasibilityLabel(String status) => switch (status) {
        'feasible' => 'Backend đánh giá phương án khả thi',
        'infeasible' => 'Chưa đủ thời gian trước hạn chót',
        'search_inconclusive' => 'Chưa thể kết luận khả năng xếp lịch',
        _ => 'Trạng thái khả thi: $status',
      };

  static int _minutes(int seconds) => (seconds / 60).ceil();

  Widget _stepCard(BuildContext context, Map<String, dynamic> step, int index) {
    final seconds = step['estimated_base_seconds'] as int? ?? 0;
    return MelbelCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        CircleAvatar(radius: 14, child: Text('${index + 1}')),
        const SizedBox(width: 10),
        Expanded(
            child: Text(step['title'] as String? ?? 'Bước chưa có tên',
                style: TextStyle(
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kOnSurface))),
        IconButton(
            tooltip: 'Chỉnh bước',
            onPressed: () => _editStep(context, step),
            icon: const Icon(Icons.edit_outlined))
      ]),
      Text(step['expected_output'] as String? ?? 'Chưa có kết quả đầu ra',
          style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
      const SizedBox(height: 6),
      Text(
          'Ước tính gốc: ${_minutes(seconds)} phút · hệ số đệm ${step['buffer_multiplier'] ?? '—'}',
          style:
              TextStyle(color: MelbelColors.kOnSurfaceVariant, fontSize: 12)),
    ]));
  }

  Widget _sessionCard(Map<String, dynamic> session) {
    final start =
        DateTime.tryParse(session['scheduled_start'] as String? ?? '');
    final end = DateTime.tryParse(session['scheduled_end'] as String? ?? '');
    String time(DateTime? value) => value == null
        ? 'Chưa xếp giờ'
        : '${value.toLocal().day.toString().padLeft(2, '0')}/${value.toLocal().month.toString().padLeft(2, '0')} ${value.toLocal().hour.toString().padLeft(2, '0')}:${value.toLocal().minute.toString().padLeft(2, '0')}';
    return MelbelCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('${time(start)} – ${time(end)}',
          style: TextStyle(
              fontWeight: FontWeight.w600, color: MelbelColors.kOnSurface)),
      const SizedBox(height: 5),
      Text(
          'Làm ${_minutes(session['work_seconds'] as int? ?? 0)}p · nghỉ ${_minutes(session['rest_seconds'] as int? ?? 0)}p',
          style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
      const SizedBox(height: 5),
      Text('Khởi động 2 phút: ${session['micro_task_text'] ?? 'Chưa có gợi ý'}',
          style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
    ]));
  }

  Future<void> _editStep(
      BuildContext context, Map<String, dynamic> step) async {
    final task = context.read<TaskProvider>();
    final title = TextEditingController(text: step['title'] as String? ?? '');
    final output =
        TextEditingController(text: step['expected_output'] as String? ?? '');
    final minutes = TextEditingController(
        text: (((step['estimated_base_seconds'] as int? ?? 1500) / 60).round())
            .toString());
    final save = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (sheetContext) => Padding(
              padding: EdgeInsets.fromLTRB(
                  20, 8, 20, MediaQuery.viewInsetsOf(sheetContext).bottom + 20),
              child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Chỉnh bước kế hoạch',
                        style: TextStyle(
                            fontSize: 20, fontWeight: FontWeight.w600)),
                    TextField(
                        controller: title,
                        decoration:
                            const InputDecoration(labelText: 'Tên bước')),
                    TextField(
                        controller: output,
                        decoration: const InputDecoration(
                            labelText: 'Kết quả mong muốn')),
                    TextField(
                        controller: minutes,
                        keyboardType: TextInputType.number,
                        decoration: const InputDecoration(
                            labelText: 'Thời lượng ước tính (phút)')),
                    const SizedBox(height: 14),
                    PrimaryCta(
                        label: 'Gửi thay đổi',
                        onPressed: () => Navigator.pop(sheetContext, true)),
                    const SizedBox(height: 8),
                    SecondaryCta(
                        label: 'Để sau',
                        onPressed: () => Navigator.pop(sheetContext, false)),
                  ]),
            ));
    if (save == true && context.mounted) {
      final value = int.tryParse(minutes.text.trim());
      if (value == null ||
          value < 1 ||
          title.text.trim().isEmpty ||
          output.text.trim().isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Nhập tên, kết quả và số phút hợp lệ.')));
      } else {
        try {
          await task.editCandidateStep(
            clientStepKey: step['client_step_key'] as String,
            title: title.text.trim(),
            expectedOutput: output.text.trim(),
            estimatedBaseSeconds: value * 60,
          );
        } catch (error) {
          if (context.mounted)
            ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text('Chưa sửa được phương án: $error')));
        }
      }
    }
    title.dispose();
    output.dispose();
    minutes.dispose();
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
            SnackBar(content: Text('Backend chưa chốt được kế hoạch: $error')));
    }
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
