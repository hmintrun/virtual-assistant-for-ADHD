import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/theme.dart';
import '../models/planning_models.dart';
import '../providers/task_provider.dart';
import '../widgets/calendar_fallback.dart';
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
  late final FocusNode _scopeFocusNode;
  bool _isSubmitting = false;

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
    final start = await showTimePicker(
      context: context,
      helpText: 'Giờ bắt đầu',
      initialEntryMode: TimePickerEntryMode.input,
      initialTime: const TimeOfDay(hour: 18, minute: 0),
    );
    if (start == null || !mounted) return;
    final end = await showTimePicker(
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
            task.deadline != _initialDeadline);
    task.setScope(scope);
    setState(() => _isSubmitting = true);
    try {
      final useAnswerAsAvailability = onlyAvailabilityIsMissing &&
          answer.isNotEmpty &&
          task.applyExplicitAvailabilityAnswer(answer);
      if (draft != null &&
          (useAnswerAsAvailability ||
              useAnswerAsScope ||
              optionalRequirement.isNotEmpty ||
              answer.isEmpty && hasFormChanges)) {
        await task.updateDraftOnBackend(extraRequirement: optionalRequirement);
        if (mounted &&
            (optionalRequirement.isNotEmpty ||
                useAnswerAsAvailability && task.canPlan)) {
          _clarificationController.clear();
        }
        _initialDeadline = task.deadline;
        if (mounted && useAnswerAsScope && task.scope.trim() == answer) {
          _scopeFocusNode.unfocus();
          _scopeController.text = task.scope;
          _clarificationController.clear();
        }
      } else if (draft != null && !task.canPlan && answer.isNotEmpty) {
        await task.submitClarification(answer);
        if (mounted && task.canPlan) _clarificationController.clear();
      } else if (draft == null || !task.canPlan) {
        await task.prepareDraftAndAnalyze();
      }
      if (!mounted) return;
      if (!task.canPlan) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Chưa đủ thông tin bắt buộc.'),
        ));
        setState(() {});
        return;
      }
      if (!createCandidate) return;
      await task.confirmDraftSummaryOnBackend();
      await task.generateCandidateOnBackend();
      if (!mounted) return;
      await Navigator.push(
          context,
          MaterialPageRoute<void>(
            builder: (_) => const CandidateReviewScreen(),
          ));
    } on ApiException catch (error) {
      if (!mounted) return;
      if (isCalendarUnavailable(error)) {
        showCalendarFallbackSnackBar(context,
            onUseManualAvailability: _generateWithManualAvailability);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Chưa cập nhật được bản nháp: $error')));
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
    final time = await showTimePicker(
      context: context,
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

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Tóm tắt công việc', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            const PlanningStageHeader(step: 2, label: 'Kiểm tra thông tin'),
            if (usedFallback) ...[
              const SizedBox(height: 8),
              const Text('AI đang gián đoạn. Bạn vẫn có thể sửa thông tin.'),
            ],
            const SizedBox(height: 16),
            MelbelCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Row(
                    children: [
                      SizedBox(width: 48, height: 48, child: MelbelMascot()),
                      SizedBox(width: 12),
                      Expanded(
                          child:
                              Text('Xem lại bốn thông tin để lập kế hoạch.')),
                    ],
                  ),
                  const Divider(),
                  ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    title: const Text('Tên công việc'),
                    subtitle: Text(
                        displayTitle.isEmpty ? 'Chưa có tên' : displayTitle),
                    children: [
                      TextFormField(
                        initialValue: title,
                        decoration: const InputDecoration(
                          labelText: 'Sửa tên công việc',
                          border: OutlineInputBorder(),
                        ),
                        onChanged: task.setSummaryTitle,
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _scopeController,
                    focusNode: _scopeFocusNode,
                    minLines: 1,
                    maxLines: null,
                    keyboardType: TextInputType.multiline,
                    decoration: const InputDecoration(
                      labelText: 'Phạm vi',
                      hintText: 'VD: 5 trang Word, kèm biểu đồ',
                      border: OutlineInputBorder(),
                    ),
                    onChanged: task.setScope,
                  ),
                  const SizedBox(height: 8),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.event_available_outlined),
                    title: const Text('Giờ rảnh'),
                    subtitle: Text(task.availabilitySummary),
                    trailing: Text(task.hasAvailability
                        ? 'Đổi khung giờ'
                        : 'Chọn khung giờ'),
                    onTap: _isSubmitting || task.isApiBusy
                        ? null
                        : () => _pickAvailability(task),
                  ),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.schedule_outlined),
                    title: const Text('Hạn chót'),
                    subtitle: Text(task.formatDeadline(task.deadline)),
                    trailing: const Icon(Icons.edit_outlined),
                    onTap: _isSubmitting || task.isApiBusy
                        ? null
                        : () => _pickSummaryDeadline(task),
                  ),
                ],
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
              const SizedBox(height: 16),
              MelbelCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      missingRequiredQuestions.isEmpty
                          ? 'Cần xác nhận'
                          : 'Cần bổ sung',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: MelbelColors.kOnSurface,
                      ),
                    ),
                    const SizedBox(height: 8),
                    for (final question in missingRequiredQuestions)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(
                          question,
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 14,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ),
                    if (missingRequiredQuestions.isNotEmpty &&
                        reviewQuestions.isNotEmpty)
                      const Text('Thông tin cần xác nhận'),
                    for (final question in reviewQuestions)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(question),
                      ),
                    TextField(
                      controller: _clarificationController,
                      minLines: 1,
                      maxLines: 3,
                      decoration: const InputDecoration(
                        hintText: 'Nhập thông tin còn thiếu',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            if (task.draft != null && !task.canPlan && !hasQuestions) ...[
              const SizedBox(height: 8),
              Text(
                'Chưa đủ điều kiện lập kế hoạch.',
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 14,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
              ),
            ],
            const SizedBox(height: 24),

            if (task.canPlan) ...[
              TextField(
                controller: _clarificationController,
                minLines: 1,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: 'Yêu cầu thêm (không bắt buộc)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
            ],

            // Actions
            if (!task.canPlan) ...[
              SecondaryCta(
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
        issue['field_path'],
  };
  final questions = <String>{};
  for (final issue in blockers) {
    if (genericCodes.contains(issue['code']) &&
        specificFields.contains(issue['field_path'])) {
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
      final time = await showTimePicker(
        context: context,
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

    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Điều chỉnh thời gian',
        showBack: true,
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
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
                        'Hạn: ${task.formatDeadline(task.deadline)}',
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
                      color: MelbelColors.kOnTertiaryContainer,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Cần điều chỉnh',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                        ),
                        SizedBox(height: 4),
                        Text(
                          issueText.isEmpty ? task.conflictMessage : issueText,
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

            MelbelCard(
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.event_available_outlined),
                title: const Text('Giờ rảnh đã khai báo'),
                subtitle: Text(task.availabilitySummary),
              ),
            ),
            const SizedBox(height: 16),

            // 3. ONE Clear Question & Quick Direct Resolution
            Text(
              'Chọn cách điều chỉnh',
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: MelbelColors.kOnSurface,
              ),
            ),
            const SizedBox(height: 14),

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
            const SizedBox(height: 10),

            const SizedBox(height: 16),

            // Confirm Resolution Button
            PrimaryCta(
              label: 'Xác nhận',
              icon: Icons.check,
              onPressed: _selectedOption < 0 ? null : () => _resolve(task),
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
    required this.isSelected,
    required this.onTap,
  });

  final String label;
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
          ],
        ),
      ),
    );
  }
}
