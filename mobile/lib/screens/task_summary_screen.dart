import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../models/planning_models.dart';
import '../providers/task_provider.dart';
import '../widgets/calendar_fallback.dart';
import '../widgets/melbel_ui.dart';
import 'drafts_screen.dart';
import 'candidate_review_screen.dart';

class TaskSummaryScreen extends StatefulWidget {
  const TaskSummaryScreen({super.key, this.returnToReview = false});

  final bool returnToReview;

  @override
  State<TaskSummaryScreen> createState() => _TaskSummaryScreenState();
}

class _TaskSummaryScreenState extends State<TaskSummaryScreen> {
  late final TextEditingController _scopeController;
  late final TextEditingController _clarificationController;
  late final FocusNode _scopeFocusNode;
  bool _isSubmitting = false;
  String? _clarificationError;

  DateTime? _initialDeadline;

  @override
  void initState() {
    super.initState();
    _initialDeadline = context.read<TaskProvider>().deadline;
    _scopeController =
        TextEditingController(text: context.read<TaskProvider>().scope);
    _clarificationController = TextEditingController();
    _scopeFocusNode = FocusNode();
  }

  @override
  void dispose() {
    _scopeController.dispose();
    _clarificationController.dispose();
    _scopeFocusNode.dispose();
    super.dispose();
  }

  void _syncScopeFromBackend(String scope) {
    if (_scopeFocusNode.hasFocus || _scopeController.text == scope) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _scopeFocusNode.hasFocus) return;
      final latestScope = context.read<TaskProvider>().scope;
      if (_scopeController.text == latestScope) return;
      _scopeController.value = TextEditingValue(
        text: latestScope,
        selection: TextSelection.collapsed(offset: latestScope.length),
      );
    });
  }

  Future<void> _pickAvailability(TaskProvider task) async {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final date = await showDatePicker(
      context: context,
      helpText: 'Chọn ngày bạn rảnh',
      firstDate: today,
      lastDate: DateTime(today.year + 2, today.month, today.day),
      initialDate: today,
    );
    if (date == null || !mounted) return;
    final start = await showMelbelTimePicker(
      context: context,
      helpText: 'Giờ bắt đầu',
      initialEntryMode: TimePickerEntryMode.input,
      initialTime: const TimeOfDay(hour: 18, minute: 0),
    );
    if (start == null || !mounted) return;
    final end = await showMelbelTimePicker(
      context: context,
      helpText: 'Giờ kết thúc',
      initialEntryMode: TimePickerEntryMode.input,
      initialTime: start,
    );
    if (end == null || !mounted) return;

    DateTime instant(TimeOfDay time) => DateTime(
          date.year,
          date.month,
          date.day,
          time.hour,
          time.minute,
        ).toUtc();
    final from = instant(start);
    final to = instant(end);
    final deadline = task.deadline;
    final invalidReason = !to.isAfter(from)
        ? 'Giờ kết thúc phải sau giờ bắt đầu.'
        : !to.isAfter(DateTime.now())
            ? 'Khung giờ này đã qua. Hãy chọn giờ rảnh khác.'
            : deadline != null && to.isAfter(deadline)
                ? 'Khung giờ vượt hạn ${task.formatDeadline(deadline)}. '
                    'Hãy đổi hạn trước hoặc chọn giờ kết thúc sớm hơn.'
                : null;
    if (invalidReason != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(invalidReason)),
      );
      return;
    }
    task.setAvailability(AvailabilitySlot(start: from, end: to));
  }

  Future<void> _continue({bool createCandidate = true}) async {
    if (_isSubmitting) return;
    final task = context.read<TaskProvider>();
    if (task.isApiBusy) return;
    final draft = task.draft;
    final answer = _clarificationController.text.trim();
    final optionalRequirement = task.canPlan ? answer : '';

    final blockers = draft?.clarificationIssues
            .where((issue) => issue['blocking'] == true)
            .toList() ??
        const <JsonMap>[];
    final onlyAvailabilityIsMissing = blockers.isNotEmpty &&
        blockers.every((issue) => const {
              'availability',
              'availability_ref',
            }.contains(issue['field_path']));
    final onlyScopeIsMissing = blockers.isNotEmpty &&
        blockers.every((issue) => issue['field_path'] == 'scope') &&
        (draft?.scope.trim().isEmpty ?? false);
    final useAnswerAsScope = onlyScopeIsMissing && answer.isNotEmpty;
    final scope = useAnswerAsScope ? answer : _scopeController.text.trim();
    final hasFormChanges = draft != null &&
        (task.title.trim() != draft.title.trim() ||
            scope != draft.scope ||
            task.availability != null ||
            task.availabilityCleared ||
            task.deadline != _initialDeadline);
    if (draft != null &&
        !task.canPlan &&
        blockers.isNotEmpty &&
        answer.isEmpty &&
        !hasFormChanges) {
      setState(() {
        _clarificationError = 'Nhập ngắn gọn hoặc chọn 1 gợi ý ở trên nhé.';
      });
      return;
    }
    task.setScope(scope);
    setState(() {
      _clarificationError = null;
      _isSubmitting = true;
    });
    var creatingCandidate = false;
    try {
      final useAnswerAsAvailability = onlyAvailabilityIsMissing &&
          answer.isNotEmpty &&
          task.applyExplicitAvailabilityAnswer(answer);
      if (draft != null &&
          (useAnswerAsAvailability ||
              useAnswerAsScope ||
              optionalRequirement.isNotEmpty ||
              hasFormChanges)) {
        await task.updateDraftOnBackend(extraRequirement: optionalRequirement);
        if (mounted &&
            (optionalRequirement.isNotEmpty || useAnswerAsAvailability)) {
          _clarificationController.clear();
        }
        _initialDeadline = task.deadline;
        if (!task.canPlan &&
            answer.isNotEmpty &&
            !useAnswerAsScope &&
            !useAnswerAsAvailability) {
          await task.submitClarification(answer);
          if (mounted) _clarificationController.clear();
        }
        if (mounted && useAnswerAsScope && task.scope.trim() == answer) {
          _scopeFocusNode.unfocus();
          _scopeController.text = task.scope;
          _clarificationController.clear();
        }
      } else if (draft != null && !task.canPlan && answer.isNotEmpty) {
        await task.submitClarification(answer);
        if (mounted) _clarificationController.clear();
      } else if (draft == null || !task.canPlan) {
        await task.prepareDraftAndAnalyze();
      }
      if (!mounted) return;
      if (!task.canPlan) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(const SnackBar(
            content: Text('Chưa đủ thông tin bắt buộc.'),
          ));
        setState(() {});
        return;
      }
      if (!createCandidate) return;
      await task.confirmDraftSummaryOnBackend();
      creatingCandidate = true;
      await task.generateCandidateOnBackend();
      if (!mounted) return;
      if (widget.returnToReview) {
        Navigator.pop(context, true);
        return;
      }
      await Navigator.push(
          context,
          MaterialPageRoute<void>(
            builder: (_) => const CandidateReviewScreen(),
          ));
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.isConfirmedDraftTransition) {
        unawaited(task.restoreBackendState());
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(const SnackBar(
            content: Text('Bản nháp này đã được chốt thành công việc.'),
          ));
      } else if (isCalendarUnavailable(error)) {
        ScaffoldMessenger.of(context).hideCurrentSnackBar();
        showCalendarFallbackSnackBar(context,
            onUseManualAvailability: _generateWithManualAvailability);
      } else {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(
            content: Text(
              creatingCandidate
                  ? 'Đã lưu bản nháp, nhưng chưa tạo được phương án lịch: $error'
                  : 'Chưa cập nhật được bản nháp: $error',
            ),
          ));
      }
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  /// User đã thấy lỗi đọc Google Calendar và chọn xếp theo giờ rảnh nhập tay.
  Future<void> _generateWithManualAvailability() async {
    final task = context.read<TaskProvider>();
    if (_isSubmitting || task.isApiBusy) return;
    setState(() => _isSubmitting = true);
    try {
      await task.generateCandidateOnBackend(useManualAvailability: true);
      if (!mounted) return;
      await Navigator.push(
          context,
          MaterialPageRoute<void>(
            builder: (_) => const CandidateReviewScreen(),
          ));
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Chưa tạo được phương án: $error')));
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  Future<void> _pickSummaryDeadline(TaskProvider task) async {
    final now = DateTime.now();
    final current = task.deadline;
    final initial = current != null && current.isAfter(now)
        ? current.toLocal()
        : now.add(const Duration(days: 1));
    final day = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateUtils.dateOnly(now),
      lastDate: DateTime(9999, 12, 31),
      helpText: 'Chọn hạn chót',
    );
    if (day == null || !mounted) return;
    final time = await showMelbelTimePicker(
      context: context,
      helpText: 'Giờ hạn chót',
      initialTime: TimeOfDay.fromDateTime(initial),
      initialEntryMode: TimePickerEntryMode.input,
    );
    if (time == null || !mounted) return;
    final deadline =
        DateTime(day.year, day.month, day.day, time.hour, time.minute);
    if (!deadline.isAfter(DateTime.now())) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Chọn hạn chót trong tương lai.')),
      );
      return;
    }
    task.setDeadline(deadline);
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
      } catch (_) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Chưa lưu được bản nháp. Thử lại nhé.'),
        ));
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
    _syncScopeFromBackend(task.scope);
    final title = task.title.trim();
    final original = task.inputText.trim();
    final firstSentence = original.split(RegExp(r'[.!?](?:\s|$)')).first.trim();
    final displayTitle = title.isNotEmpty &&
            original.startsWith(title) &&
            firstSentence.isNotEmpty &&
            firstSentence.length < title.length
        ? firstSentence
        : title;
    final issues = task.draft?.clarificationIssues ?? const <JsonMap>[];
    final missingRequiredQuestions = _requiredQuestions(
      issues
          .where((issue) => issue['code'] != 'AI_FIELD_REVIEW_REQUIRED')
          .toList(),
    );
    final reviewQuestions = _requiredQuestions(
      issues
          .where((issue) => issue['code'] == 'AI_FIELD_REVIEW_REQUIRED')
          .toList(),
    );
    final hasQuestions =
        missingRequiredQuestions.isNotEmpty || reviewQuestions.isNotEmpty;

    final usedFallback = task.draft?.clarificationIssues.any((issue) =>
            issue['code'] == 'AI_FALLBACK_NOTICE' &&
            issue['blocking'] != true) ??
        false;
    final blockers =
        issues.where((issue) => issue['blocking'] == true).toList();
    final anyAvailabilityMissing = blockers.any((issue) {
      final path = (issue['field_path'] as String?) ?? '';
      return path == 'availability' ||
          path == 'availability_ref' ||
          path == 'summary.availability' ||
          path.startsWith('availability.') ||
          path.startsWith('summary.availability.');
    });
    final quickOptions = <String>[
      for (final issue in blockers)
        for (final key in const ['options', 'quick_options'])
          if (issue[key] is List)
            for (final item in issue[key] as List)
              if (item is String && item.trim().isNotEmpty) item.trim(),
      if (anyAvailabilityMissing) ...const [
        'Tối nay 20:00–22:00',
        'Sáng mai 09:00–11:00',
        'Tối mai 19:30–21:30',
      ],
    ].toSet().toList(growable: false);

    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;

    Widget fieldBadge(IconData icon, {bool ready = true}) => Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: ready ? palette.sageSoft : palette.amberSoft,
            borderRadius: BorderRadius.circular(14),
          ),
          alignment: Alignment.center,
          child: Icon(
            icon,
            size: 20,
            color: ready ? palette.accentInk : palette.amberInk,
          ),
        );

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Tóm tắt công việc',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 32),
          children: [
            const PlanningStageHeader(step: 2, label: 'Kiểm tra thông tin'),
            if (usedFallback) ...[
              const SizedBox(height: 10),
              MelbelCard(
                tone: MelbelCardTone.amber,
                borderRadius: 24,
                padding: const EdgeInsets.all(14),
                child: Row(
                  children: [
                    Icon(Icons.info_outline_rounded,
                        size: 18, color: palette.amberInk),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'AI tạm gián đoạn · bạn vẫn có thể chỉnh tay.',
                        style: textTheme.bodySmall?.copyWith(
                          color: palette.onAmberSoft,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 16),
            MelbelEntrance(
              child: MelbelChatBlock(
                senderLabel: 'Melbel',
                title: task.canPlan
                    ? 'Đủ thông tin để chia việc'
                    : 'Thông tin công việc',
                titleIsHeader: true,
                message: null,
                tone: task.canPlan
                    ? MelbelCardTone.sage
                    : MelbelCardTone.amber,
                mascotWidth: 52,
                mascotHeight: 48,
              ),
            ),
            const SizedBox(height: 14),
            MelbelEntrance(
              child: MelbelCard(
                borderRadius: 26,
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Theme(
                      data: Theme.of(context)
                          .copyWith(dividerColor: Colors.transparent),
                      child: ExpansionTile(
                        tilePadding: EdgeInsets.zero,
                        leading: fieldBadge(
                          Icons.title_rounded,
                          ready: displayTitle.isNotEmpty,
                        ),
                        title: const Text('Tên công việc'),
                        subtitle: Text(
                          displayTitle.isEmpty ? 'Chưa có tên' : displayTitle,
                          style: textTheme.titleSmall?.copyWith(
                            color: palette.onSurface,
                          ),
                        ),
                        children: [
                          TextFormField(
                            initialValue: title,
                            decoration: const InputDecoration(
                              labelText: 'Sửa tên công việc',
                            ),
                            onChanged: task.setSummaryTitle,
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: fieldBadge(
                            Icons.track_changes_rounded,
                            ready: _scopeController.text.trim().isNotEmpty,
                          ),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('Phạm vi', style: textTheme.bodyLarge),
                              const SizedBox(height: 6),
                              TextField(
                                controller: _scopeController,
                                focusNode: _scopeFocusNode,
                                minLines: 1,
                                maxLines: null,
                                keyboardType: TextInputType.multiline,
                                decoration: const InputDecoration(
                                  hintText: 'VD: 5 trang Word, kèm biểu đồ',
                                  isDense: true,
                                ),
                                onChanged: (value) {
                                  task.setScope(value);
                                  setState(() {});
                                },
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: fieldBadge(
                        Icons.event_available_outlined,
                        ready: task.hasAvailability,
                      ),
                      title: const Text('Giờ rảnh'),
                      subtitle: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(task.availabilitySummary),
                          Text(
                            task.hasAvailability
                                ? 'Đổi khung giờ'
                                : 'Chọn khung giờ',
                            style: textTheme.labelLarge?.copyWith(
                              color: task.hasAvailability
                                  ? palette.accentInk
                                  : palette.amberInk,
                            ),
                          ),
                        ],
                      ),
                      trailing: Icon(Icons.edit_outlined,
                          color: palette.onSurfaceVariant),
                      onTap: _isSubmitting || task.isApiBusy
                          ? null
                          : () => _pickAvailability(task),
                    ),
                    if (task.hasAvailability)
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton.icon(
                          onPressed: _isSubmitting || task.isApiBusy
                              ? null
                              : () {
                                  final previous = task.clearAvailability();
                                  if (previous == null &&
                                      !task.availabilityCleared) return;
                                  ScaffoldMessenger.of(context)
                                    ..hideCurrentSnackBar()
                                    ..showSnackBar(SnackBar(
                                      content: const Text(
                                        'Đã xóa giờ rảnh khỏi bản nháp.',
                                      ),
                                      duration: const Duration(seconds: 5),
                                      action: SnackBarAction(
                                        label: 'Hoàn tác',
                                        onPressed: () =>
                                            task.undoClearAvailability(
                                                previous),
                                      ),
                                    ));
                                },
                          icon: const Icon(Icons.delete_outline),
                          label: const Text('Xóa giờ rảnh'),
                        ),
                      ),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: fieldBadge(
                        Icons.schedule_outlined,
                        ready: task.deadline != null,
                      ),
                      title: const Text('Hạn chót'),
                      subtitle: Text(
                        task.formatDeadline(task.deadline),
                        style: task.deadline == null
                            ? textTheme.bodySmall?.copyWith(
                                color: palette.amberInk,
                                fontWeight: FontWeight.w600,
                              )
                            : null,
                      ),
                      trailing: Icon(
                        Icons.edit_outlined,
                        color: palette.onSurfaceVariant,
                      ),
                      onTap: _isSubmitting || task.isApiBusy
                          ? null
                          : () => _pickSummaryDeadline(task),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            if (task.hasConflict ||
                (task.draft?.clarificationIssues.any((issue) =>
                        issue['blocking'] == true &&
                        const {
                          'AI_CONFLICT',
                          'INVALID_TIME_ORDER',
                          'END_TIME_PASSED',
                          'AVAILABILITY_OUTSIDE_TIME_CONSTRAINT',
                          'CONFLICTS_WITH_BUSY_EVENT',
                        }.contains(issue['code'])) ??
                    false)) ...[
              SecondaryCta(
                label: 'Điều chỉnh thời gian',
                icon: Icons.schedule_rounded,
                onPressed: _isSubmitting || task.isApiBusy
                    ? null
                    : () async {
                        final editAvailability = await Navigator.push<bool>(
                          context,
                          MaterialPageRoute<bool>(
                            builder: (_) => const TimeConflictScreen(),
                          ),
                        );
                        if (mounted && editAvailability == true) {
                          await _pickAvailability(task);
                        }
                      },
              ),
              const SizedBox(height: 12),
            ],
            if (task.draft != null && !task.canPlan && hasQuestions) ...[
              const SizedBox(height: 12),
              MelbelCard(
                tone: MelbelCardTone.amber,
                borderRadius: 26,
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.help_outline_rounded,
                          size: 18,
                          color: palette.amberInk,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            missingRequiredQuestions.isEmpty
                                ? 'Cần xác nhận'
                                : 'Cần bổ sung',
                            style: textTheme.titleMedium?.copyWith(
                              color: palette.onAmberSoft,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    for (final question in missingRequiredQuestions)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(
                          question,
                          style: textTheme.bodyMedium?.copyWith(
                            color: palette.onAmberSoft,
                          ),
                        ),
                      ),
                    if (missingRequiredQuestions.isNotEmpty &&
                        reviewQuestions.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Text(
                          'Thông tin cần xác nhận',
                          style: textTheme.titleSmall?.copyWith(
                            color: palette.onAmberSoft,
                          ),
                        ),
                      ),
                    for (final question in reviewQuestions)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(
                          question,
                          style: textTheme.bodyMedium?.copyWith(
                            color: palette.onAmberSoft,
                          ),
                        ),
                      ),
                    if (quickOptions.isNotEmpty) ...[
                      Wrap(
                        spacing: 8,
                        runSpacing: 6,
                        children: [
                          for (final option in quickOptions)
                            ActionChip(
                              label: Text(option),
                              onPressed: _isSubmitting || task.isApiBusy
                                  ? null
                                  : () {
                                      final current =
                                          _clarificationController.text.trim();
                                      final next = current.isEmpty
                                          ? option
                                          : current.contains(option)
                                              ? current
                                              : '$current, $option';
                                      _clarificationController.value =
                                          TextEditingValue(
                                        text: next,
                                        selection: TextSelection.collapsed(
                                          offset: next.length,
                                        ),
                                      );
                                      setState(() => _clarificationError = null);
                                    },
                            ),
                        ],
                      ),
                      const SizedBox(height: 10),
                    ],
                    TextField(
                      controller: _clarificationController,
                      minLines: 1,
                      maxLines: 3,
                      decoration: InputDecoration(
                        labelText: 'Nhập thông tin còn thiếu',
                        errorText: _clarificationError,
                        errorMaxLines: 2,
                      ),
                      onChanged: (_) {
                        if (_clarificationError != null) {
                          setState(() => _clarificationError = null);
                        }
                      },
                    ),
                  ],
                ),
              ),
            ],
            if (task.draft != null && !task.canPlan && !hasQuestions) ...[
              const SizedBox(height: 8),
              Text(
                'Chưa đủ điều kiện lập kế hoạch.',
                style: textTheme.bodyMedium?.copyWith(
                  color: palette.onSurfaceVariant,
                ),
              ),
            ],
            const SizedBox(height: 24),
            if (task.canPlan) ...[
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('Yêu cầu thêm (không bắt buộc)'),
                children: [
                  TextField(
                    controller: _clarificationController,
                    minLines: 1,
                    maxLines: 3,
                    decoration:
                        const InputDecoration(labelText: 'Yêu cầu của bạn'),
                  ),
                ],
              ),
              const SizedBox(height: 16),
            ],

            // Actions
            if (!task.canPlan) ...[
              PrimaryCta(
                label: missingRequiredQuestions.isNotEmpty
                    ? 'Bổ sung thông tin'
                    : reviewQuestions.isNotEmpty
                        ? 'Gửi xác nhận'
                        : 'Thử lại',
                onPressed: _isSubmitting || task.isApiBusy
                    ? null
                    : () => _continue(createCandidate: false),
              ),
              const SizedBox(height: 8),
            ],
            if (_isSubmitting || task.isApiBusy)
              const Padding(
                padding: EdgeInsets.only(bottom: 8),
                child: LinearProgressIndicator(semanticsLabel: 'Đang xử lý'),
              ),
            if (task.canPlan)
              PrimaryCta(
                label: _isSubmitting ? 'Đang xử lý…' : 'Tạo phương án',
                icon: Icons.arrow_forward,
                onPressed: _isSubmitting || task.isApiBusy || !task.canPlan
                    ? null
                    : _continue,
              ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu nháp',
              icon: Icons.drafts_outlined,
              height: 48,
              onPressed: _isSubmitting || task.isApiBusy ? null : _saveDraft,
            ),
          ],
        ),
      ),
    ),
    );
  }
}

// -------------------------------------------------------------
// D-18 Unclear or Conflicting Info Screen (TimeConflictScreen)
// -------------------------------------------------------------
List<String> _requiredQuestions(List<JsonMap> issues) {
  const genericCodes = {'MISSING_TIME_CONSTRAINT', 'MISSING_AVAILABILITY'};
  const specificCodes = {
    'AI_TIME_REVIEW_REQUIRED',
    'AI_TIME_INCOMPLETE',
    'AI_AVAILABILITY_INCOMPLETE',
    'AI_AVAILABILITY_REVIEW_REQUIRED',
  };
  String fieldGroup(JsonMap issue) {
    final path = (issue['field_path'] as String?) ?? '';
    final field = path.startsWith('summary.') ? path.substring(8) : path;
    final root = field.split('.').first;
    return root == 'availability_ref' ? 'availability' : root;
  }

  final blockers = issues.where((issue) => issue['blocking'] == true).toList();
  final specificFields = {
    for (final issue in blockers)
      if (specificCodes.contains(issue['code']) &&
          issue['field_path'] is String &&
          ((issue['question'] ?? issue['message'])
                  ?.toString()
                  .trim()
                  .isNotEmpty ??
              false))
        fieldGroup(issue),
  };
  final questions = <String>{};
  final seenGroups = <String>{};
  for (final issue in blockers) {
    final group = fieldGroup(issue);
    if (genericCodes.contains(issue['code']) &&
        specificFields.contains(group)) {
      continue;
    }
    if (group.isNotEmpty && !seenGroups.add(group)) {
      continue;
    }
    final question = (issue['question'] ?? issue['message'])?.toString().trim();
    questions.add(question == null || question.isEmpty
        ? 'Cần bổ sung thông tin bắt buộc để lập kế hoạch.'
        : question);
  }
  return questions.toList(growable: false);
}

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
        initialDate: task.deadline?.isAfter(now) == true
            ? task.deadline!.toLocal()
            : now.add(const Duration(days: 1)),
        firstDate: DateTime(now.year, now.month, now.day),
        lastDate: DateTime(9999, 12, 31),
        helpText: 'Chọn hạn chót mới',
      );
      if (day == null || !mounted) return;
      final time = await showMelbelTimePicker(
        context: context,
        helpText: 'Giờ hạn chót',
        initialTime: task.deadline == null
            ? const TimeOfDay(hour: 18, minute: 0)
            : TimeOfDay.fromDateTime(task.deadline!.toLocal()),
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
      Navigator.pop(context, true);
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
            .where((issue) => issue['blocking'] == true)
            .map((issue) => issue['question'] ?? issue['message'])
            .whereType<String>()
            .join('\n') ??
        '';

    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Điều chỉnh thời gian',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 32),
          children: [
            MelbelEntrance(
              child: MelbelCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Eyebrow('Thông tin đã lưu'),
                        Icon(
                          Icons.lock_outline_rounded,
                          size: 16,
                          color: palette.onSurfaceVariant,
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      task.title.isEmpty
                          ? 'Chưa có nội dung công việc'
                          : task.title,
                      style: textTheme.titleLarge?.copyWith(
                        color: palette.onSurface,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Icon(
                          Icons.schedule_rounded,
                          size: 15,
                          color: palette.onSurfaceVariant,
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            'Hạn: ${task.formatDeadline(task.deadline)}',
                            style: textTheme.bodySmall?.copyWith(
                              color: palette.onSurfaceVariant,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            MelbelCard(
              tone: MelbelCardTone.amber,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 34,
                    height: 34,
                    decoration: BoxDecoration(
                      color: palette.amber.withValues(alpha: 0.14),
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      Icons.info_outline_rounded,
                      size: 20,
                      color: palette.amberInk,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Cần điều chỉnh',
                          style: textTheme.titleSmall?.copyWith(
                            color: palette.onAmberSoft,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          issueText.isEmpty ? task.conflictMessage : issueText,
                          style: textTheme.bodyMedium?.copyWith(
                            color: palette.onAmberSoft,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            MelbelCard(
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  Icons.event_available_outlined,
                  color: palette.accentInk,
                ),
                title: const Text('Giờ rảnh đã khai báo'),
                subtitle: Text(task.availabilitySummary),
              ),
            ),
            const SizedBox(height: 18),
            Text(
              'Chọn cách điều chỉnh',
              style: textTheme.titleMedium?.copyWith(
                color: palette.onSurface,
              ),
            ),
            const SizedBox(height: 12),
            _ConflictResolutionOption(
              label: 'Đổi hạn chót',
              isSelected: _selectedOption == 0,
              onTap: () => setState(() => _selectedOption = 0),
            ),
            const SizedBox(height: 10),
            _ConflictResolutionOption(
              label: 'Sửa giờ rảnh',
              isSelected: _selectedOption == 1,
              onTap: () => setState(() => _selectedOption = 1),
            ),
            const SizedBox(height: 10),
            _ConflictResolutionOption(
              label: 'Lưu nháp',
              isSelected: _selectedOption == 2,
              onTap: () => setState(() => _selectedOption = 2),
            ),
            const SizedBox(height: 22),
            PrimaryCta(
              label: 'Áp dụng lựa chọn',
              icon: Icons.check_rounded,
              onPressed: _selectedOption < 0 ? null : () => _resolve(task),
            ),
          ],
        ),
      ),
    ),
    );
  }
}

class _ConflictResolutionOption extends StatelessWidget {
  const _ConflictResolutionOption({
    required this.label,
    required this.isSelected,
    required this.onTap,
  });

  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    return Semantics(
      selected: isSelected,
      button: true,
      label: label,
      child: MelbelCard(
        tone: isSelected ? MelbelCardTone.sage : MelbelCardTone.neutral,
        borderColor: isSelected ? palette.sage : palette.hairline,
        borderWidth: isSelected ? 1.5 : 1.0,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        onTap: onTap,
        child: Row(
          children: [
            Container(
              width: 22,
              height: 22,
              decoration: BoxDecoration(
                color: isSelected ? palette.sage : palette.surface,
                shape: BoxShape.circle,
                border: Border.all(
                  color: isSelected ? palette.sage : palette.controlOutline,
                  width: 1.5,
                ),
              ),
              child: isSelected
                  ? Icon(Icons.check_rounded, size: 14, color: palette.onSage)
                  : null,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                label,
                style: Theme.of(context).textTheme.titleSmall?.copyWith(
                      color:
                          isSelected ? palette.onSageSoft : palette.onSurface,
                    ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
