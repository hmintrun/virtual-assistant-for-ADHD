import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/api_client.dart';
import '../models/planning_models.dart';
import '../models/task_model.dart';
import '../services/planning_api.dart';

class AvailabilitySlot {
  const AvailabilitySlot({required this.start, required this.end});

  final DateTime start;
  final DateTime end;

  String get label {
    final date = _dayLabel(start);
    return date + ', ' + _time(start) + '–' + _time(end);
  }

  static String _dayLabel(DateTime value) {
    final today = DateTime.now();
    final date = DateTime(value.year, value.month, value.day);
    final todayDate = DateTime(today.year, today.month, today.day);
    if (date == todayDate) return 'Hôm nay';
    if (date == todayDate.add(const Duration(days: 1))) return 'Ngày mai';
    return value.day.toString().padLeft(2, '0') +
        '/' +
        value.month.toString().padLeft(2, '0');
  }

  static String _time(DateTime value) =>
      value.hour.toString().padLeft(2, '0') +
      ':' +
      value.minute.toString().padLeft(2, '0');
}

class DeadlineOption {
  const DeadlineOption({required this.label, required this.value});

  final String label;
  final DateTime value;
}

class TaskProvider extends ChangeNotifier {
  static const _storageKey = 'proplan.uf1.current_draft.v1';
  static const _storage = FlutterSecureStorage();

  TaskProvider({PlanningApi? planningApi})
      : _planningApi = planningApi ?? PlanningApi();

  final PlanningApi _planningApi;

  String _title = '';
  String _scope = '';
  DateTime? _deadline;
  AvailabilitySlot? _availability;
  bool _draftSaved = false;
  bool _confirmed = false;
  String _voiceQuestion = '';
  String? _decompositionChoice;
  bool _planLocked = false;
  bool _planSavedForLater = false;
  PlanningDraftModel? _draft;
  PlanCandidateModel? _candidate;
  AvailabilityModel? _serverAvailability;
  TaskModel? _confirmedTask;
  bool _isApiBusy = false;
  String? _apiError;
  String? _clientDraftId;
  String? _clientDraftFingerprint;

  String get title => _title;
  String get scope => _scope;
  DateTime? get deadline => _deadline;
  AvailabilitySlot? get availability => _availability;
  bool get draftSaved => _draftSaved;
  bool get confirmed => _confirmed;
  String get voiceQuestion => _voiceQuestion;
  String? get decompositionChoice => _decompositionChoice;
  bool get planLocked => _planLocked;
  bool get planSavedForLater => _planSavedForLater;
  PlanningDraftModel? get draft => _draft;
  PlanCandidateModel? get candidate => _candidate;
  TaskModel? get confirmedTask => _confirmedTask;
  bool get isApiBusy => _isApiBusy;
  String? get apiError => _apiError;
  bool get canPlan => _draft?.canPlan ?? false;
  String get serverAvailabilitySummary {
    final availability = _serverAvailability;
    if (availability == null) return 'Chưa tải được giờ rảnh từ máy chủ.';
    const weekdays = {
      1: 'Thứ 2',
      2: 'Thứ 3',
      3: 'Thứ 4',
      4: 'Thứ 5',
      5: 'Thứ 6',
      6: 'Thứ 7',
      7: 'Chủ nhật',
    };
    final rules =
        availability.raw['weekly_rules'] as List<dynamic>? ?? const [];
    final labels = rules.whereType<Map<String, dynamic>>().map((rule) {
      final day = weekdays[rule['weekday'] as int?] ?? 'Ngày khác';
      return '$day ${rule['start_local']}–${rule['end_local']}';
    }).toList();
    if (labels.isEmpty) return 'Chưa có khung giờ định kỳ đã xác nhận.';
    return '${labels.join(' · ')} (${availability.timezone})';
  }

  Future<void> restoreBackendState() async {
    final stored = await _storage.read(key: _storageKey);
    if (stored == null) return;
    try {
      final data = jsonDecode(stored) as Map<String, dynamic>;
      final draftId = data['backend_draft_id'] as String? ?? _draft?.draftId;
      final candidateId = data['backend_candidate_id'] as String?;
      if (draftId == null) return;
      _serverAvailability = await _planningApi.getAvailability();
      _draft = await _planningApi.getDraft(draftId);
      _applyDraftSummary();
      // Local storage is only a cache. Re-read any confirmed Task from the API
      // so a backend restart cannot leave a stale "confirmed" screen behind.
      _candidate = null;
      _confirmedTask = null;
      _confirmed = false;
      _planLocked = false;
      if (candidateId != null) {
        try {
          _candidate = await _planningApi.getCandidate(candidateId);
        } on ApiException {
          // Candidate service is currently in-memory on backend; keep the draft usable.
        }
      }
      final savedTask = data['confirmed_task'] as Map<String, dynamic>?;
      final savedTaskId = savedTask?['task_id'] as String?;
      if (savedTaskId != null) {
        try {
          _confirmedTask = await _planningApi.getTask(savedTaskId);
          _confirmed = true;
          _planLocked = true;
        } on ApiException {
          // Do not show a cached task as confirmed if the server no longer has it.
        }
      }
      await _persist();
      notifyListeners();
    } on ApiException catch (error) {
      _apiError = error.message;
      notifyListeners();
    }
  }

  Future<void> prepareDraftAndAnalyze() async {
    if (_title.trim().isEmpty) {
      throw const ApiException('Hãy nhập nội dung công việc trước.');
    }
    await _runApi(() async {
      if (_deadline != null && !_deadline!.isAfter(DateTime.now())) {
        throw const ApiException(
            'Hạn chót đã qua. Hãy chọn một thời điểm trong tương lai.');
      }
      _serverAvailability = await _planningApi.getAvailability();
      if (_draft == null || _draft!.rawInput != _title.trim()) {
        if (_draft != null) {
          _clientDraftId = ApiClient.newIdempotencyKey('draft');
          _candidate = null;
          _confirmedTask = null;
          _planLocked = false;
        }
        _ensureClientDraftId();
        await _persist();
        _draft = await _planningApi.createDraft(
          clientDraftId: _clientDraftId!,
          rawInput: _title.trim(),
          title: _title.trim(),
          scope: _scope,
          deadline: _deadline,
          availability: _serverAvailability!,
        );
        await _persist();
      } else {
        _draft = await _planningApi.updateSummary(
          draft: _draft!,
          title: _title.trim(),
          scope: _scope.trim(),
          deadline: _deadline,
          timezone: _serverAvailability!.timezone,
        );
      }
      _draft = await _planningApi.analyzeDraft(_draft!);
      _applyDraftSummary();
      await _persist();
    });
  }

  Future<void> saveDraftToBackend() async {
    if (_title.trim().isEmpty) {
      throw const ApiException('Nhập nội dung công việc để lưu nháp.');
    }
    await _runApi(() async {
      if (_deadline != null && !_deadline!.isAfter(DateTime.now())) {
        throw const ApiException(
            'Hạn chót đã qua. Hãy chọn một thời điểm trong tương lai.');
      }
      _serverAvailability ??= await _planningApi.getAvailability();
      if (_draft == null || _draft!.rawInput != _title.trim()) {
        if (_draft != null) {
          _clientDraftId = ApiClient.newIdempotencyKey('draft');
          _candidate = null;
          _confirmedTask = null;
          _planLocked = false;
        }
        _ensureClientDraftId();
        await _persist();
        _draft = await _planningApi.createDraft(
          clientDraftId: _clientDraftId!,
          rawInput: _title.trim(),
          title: _title.trim(),
          scope: _scope,
          deadline: _deadline,
          availability: _serverAvailability!,
        );
      } else {
        _draft = await _planningApi.updateSummary(
          draft: _draft!,
          title: _title.trim(),
          scope: _scope.trim(),
          deadline: _deadline,
          timezone: _serverAvailability!.timezone,
        );
      }
      await _persist();
    });
  }

  Future<void> updateDraftOnBackend() async {
    final draft = _draft;
    final availability = _serverAvailability;
    if (draft == null || availability == null) {
      throw const ApiException('Chưa có bản nháp trên máy chủ.');
    }
    await _runApi(() async {
      _draft = await _planningApi.updateSummary(
        draft: draft,
        title: _title.trim(),
        scope: _scope.trim(),
        deadline: _deadline,
        timezone: availability.timezone,
      );
      _applyDraftSummary();
      _candidate = null;
      await _persist();
    });
  }

  Future<void> confirmDraftSummaryOnBackend() async {
    final draft = _draft;
    if (draft == null)
      throw const ApiException('Chưa tạo bản nháp trên máy chủ.');
    if (!draft.canPlan) {
      final issues = draft.clarificationIssues;
      final issue = issues.isEmpty ? null : issues.first;
      throw ApiException(issue?['question'] as String? ??
          'Bổ sung các thông tin còn thiếu trước khi lập kế hoạch.');
    }
    await _runApi(() async {
      await _planningApi.confirmSummary(draft);
      // API confirm-summary chỉ trả receipt. Giữ summary revision nguồn.
      _draft = PlanningDraftModel(
        draftId: draft.draftId,
        clientDraftId: draft.clientDraftId,
        stage: 'summary_confirmed',
        summary: draft.summary,
        summaryRevision: draft.summaryRevision,
        planningInputRevision: draft.planningInputRevision,
        inputSource: draft.inputSource,
        rawInput: draft.rawInput,
        summaryConfirmedRevision: draft.summaryRevision,
        userStepProposal: draft.userStepProposal,
      );
      await _persist();
    });
  }

  Future<void> generateCandidateOnBackend({String? stepProposal}) async {
    var draft = _draft;
    if (draft == null ||
        draft.summaryConfirmedRevision != draft.summaryRevision) {
      throw const ApiException('Hãy xác nhận thông tin công việc trước.');
    }
    await _runApi(() async {
      final proposal = stepProposal?.trim();
      if (proposal != null && proposal.isNotEmpty) {
        draft = await _planningApi.updateStepProposal(
          draft: draft!,
          proposal: proposal,
        );
        _draft = PlanningDraftModel(
          draftId: draft!.draftId,
          clientDraftId: draft!.clientDraftId,
          stage: draft!.stage,
          summary: draft!.summary,
          summaryRevision: draft!.summaryRevision,
          planningInputRevision: draft!.planningInputRevision,
          inputSource: draft!.inputSource,
          rawInput: draft!.rawInput,
          summaryConfirmedRevision: draft!.summaryConfirmedRevision,
          userStepProposal: proposal,
        );
      }
      _candidate = await _planningApi.createCandidate(_draft!);
      await _persist();
    });
  }

  Future<void> refreshCandidateFromBackend() async {
    final candidate = _candidate;
    if (candidate == null) return;
    await _runApi(() async {
      _candidate = await _planningApi.getCandidate(candidate.candidateId);
      await _persist();
    });
  }

  Future<void> editCandidateStep({
    required String clientStepKey,
    required String title,
    required String expectedOutput,
    required int estimatedBaseSeconds,
  }) async {
    final candidate = _candidate;
    if (candidate == null)
      throw const ApiException('Chưa có phương án để sửa.');
    await _runApi(() async {
      _candidate = await _planningApi.updateCandidateStep(
        candidate: candidate,
        clientStepKey: clientStepKey,
        title: title,
        expectedOutput: expectedOutput,
        estimatedBaseSeconds: estimatedBaseSeconds,
      );
      await _persist();
    });
  }

  Future<void> confirmCandidateOnBackend() async {
    final candidate = _candidate;
    final draft = _draft;
    if (candidate == null || draft == null) {
      throw const ApiException('Chưa có phương án từ máy chủ để xác nhận.');
    }
    if (!candidate.isFeasible) {
      throw const ApiException(
          'Phương án chưa khả thi hoặc đã cũ. Hãy tính lại.');
    }
    await _runApi(() async {
      final receipt = await _planningApi.confirmCandidate(
        candidate: candidate,
        draft: draft,
      );
      _confirmedTask = TaskModel.fromJson(
        receipt['task'] as Map<String, dynamic>,
      );
      _planLocked = true;
      _planSavedForLater = false;
      _confirmed = true;
      await _persist();
    });
  }

  Future<void> reviseDeadlineAndRegenerate(DateTime deadline) async {
    setDeadline(deadline);
    await updateDraftOnBackend();
    await confirmDraftSummaryOnBackend();
    await generateCandidateOnBackend();
  }

  Future<void> _runApi(Future<void> Function() action) async {
    _isApiBusy = true;
    _apiError = null;
    notifyListeners();
    try {
      await action();
    } on ApiException catch (error) {
      _apiError = error.message;
      rethrow;
    } finally {
      _isApiBusy = false;
      notifyListeners();
    }
  }

  void _ensureClientDraftId() {
    final fingerprint =
        '${_title.trim()}|${_scope.trim()}|${_deadline?.toIso8601String()}';
    if (_clientDraftId == null || _clientDraftFingerprint != fingerprint) {
      _clientDraftId = ApiClient.newIdempotencyKey('draft');
      _clientDraftFingerprint = fingerprint;
    }
  }

  void _applyDraftSummary() {
    final summary = _draft?.summary;
    if (summary == null) return;
    _title = summary['title'] as String? ?? _title;
    _scope = summary['scope'] as String? ?? _scope;
    final timeConstraint = summary['time_constraint'] as Map<String, dynamic>?;
    final deadline = timeConstraint?['deadline_at'] as String?;
    if (deadline != null) _deadline = DateTime.tryParse(deadline)?.toLocal();
  }

  void setDecompositionChoice(String value) {
    _decompositionChoice = value;
    _planSavedForLater = false;
    notifyListeners();
  }

  void savePlanForLater() {
    _planSavedForLater = true;
    unawaited(_persist());
    notifyListeners();
  }

  void lockPlan() {
    _planLocked = true;
    _planSavedForLater = false;
    _confirmed = true;
    unawaited(_persist());
    notifyListeners();
  }

  bool get hasConflict {
    final deadline = _deadline;
    if (deadline == null) return false;
    if (!deadline.isAfter(DateTime.now())) return true;
    return _availability != null && _availability!.end.isAfter(deadline);
  }

  String get conflictMessage {
    if (_deadline != null && !_deadline!.isAfter(DateTime.now())) {
      return 'Hạn chót đã qua. Hãy chọn một hạn mới.';
    }
    if (_deadline != null && _availability != null) {
      return 'Khung giờ rảnh kết thúc sau hạn chót đã chọn.';
    }
    return 'Thông tin thời gian chưa khớp.';
  }

  List<DeadlineOption> get deadlineOptions {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final tomorrow = today.add(const Duration(days: 1));
    final daysToSunday = (DateTime.sunday - today.weekday) % 7;
    final weekend = today.add(Duration(days: daysToSunday));
    return [
      DeadlineOption(
        label: '18:00 hôm nay',
        value: today.add(const Duration(hours: 18)),
      ),
      DeadlineOption(
        label: '18:00 ngày mai',
        value: tomorrow.add(const Duration(hours: 18)),
      ),
      DeadlineOption(
        label: 'Cuối tuần',
        value: weekend.add(const Duration(hours: 18)),
      ),
    ];
  }

  List<AvailabilitySlot> get availabilityOptions {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final tomorrow = today.add(const Duration(days: 1));
    final earlyWindowDay =
        now.isBefore(today.add(const Duration(hours: 14))) ? today : tomorrow;
    final afternoonWindowDay =
        now.isBefore(today.add(const Duration(hours: 15, minutes: 30)))
            ? today
            : tomorrow;
    final eveningWindowDay =
        now.isBefore(today.add(const Duration(hours: 19))) ? today : tomorrow;
    return [
      AvailabilitySlot(
        start: earlyWindowDay.add(const Duration(hours: 14)),
        end: earlyWindowDay.add(const Duration(hours: 15, minutes: 30)),
      ),
      AvailabilitySlot(
        start: afternoonWindowDay.add(const Duration(hours: 15, minutes: 30)),
        end: afternoonWindowDay.add(const Duration(hours: 17)),
      ),
      AvailabilitySlot(
        start: eveningWindowDay.add(const Duration(hours: 19)),
        end: eveningWindowDay.add(const Duration(hours: 20, minutes: 30)),
      ),
    ];
  }

  String formatDeadline(DateTime? value) {
    if (value == null) return 'Chưa đặt hạn chót';
    final today = DateTime.now();
    final date = DateTime(value.year, value.month, value.day);
    final todayDate = DateTime(today.year, today.month, today.day);
    final dateLabel = date == todayDate
        ? 'Hôm nay'
        : date == todayDate.add(const Duration(days: 1))
            ? 'Ngày mai'
            : value.day.toString().padLeft(2, '0') +
                '/' +
                value.month.toString().padLeft(2, '0') +
                '/' +
                value.year.toString();
    return dateLabel +
        ', ' +
        value.hour.toString().padLeft(2, '0') +
        ':' +
        value.minute.toString().padLeft(2, '0');
  }

  void setTitle(String value) {
    if (_title != value) {
      _candidate = null;
      _confirmedTask = null;
      _planLocked = false;
    }
    _title = value;
    _confirmed = false;
    _changed();
  }

  void setScope(String value) {
    if (_scope != value) _candidate = null;
    _scope = value;
    _confirmed = false;
    _changed();
  }

  void setDeadline(DateTime value) {
    if (_deadline != value) _candidate = null;
    _deadline = value;
    _confirmed = false;
    _changed();
  }

  void setAvailability(AvailabilitySlot value) {
    _availability = value;
    _confirmed = false;
    _changed();
  }

  void setVoiceQuestion(String value) {
    _voiceQuestion = value;
    notifyListeners();
  }

  void extendDeadlineByOneHour({int hours = 1}) {
    final deadline = _deadline;
    if (deadline == null) return;
    final base = deadline.isAfter(DateTime.now()) ? deadline : DateTime.now();
    setDeadline(base.add(Duration(hours: hours)));
  }

  void saveDraft() {
    if (_title.trim().isEmpty) return;
    _draftSaved = true;
    unawaited(_persist());
    notifyListeners();
  }

  void confirmTask() {
    _confirmed = true;
    _draftSaved = false;
    unawaited(_persist());
    notifyListeners();
  }

  Future<void> restoreDraft() async {
    try {
      final value = await _storage.read(key: _storageKey);
      if (value == null) return;
      final data = jsonDecode(value) as Map<String, dynamic>;
      _title = data['title'] as String? ?? '';
      _scope = data['scope'] as String? ?? '';
      final deadline = data['deadline'] as String?;
      _deadline = deadline == null ? null : DateTime.tryParse(deadline);
      final start = data['availability_start'] as String?;
      final end = data['availability_end'] as String?;
      _availability = start == null || end == null
          ? null
          : AvailabilitySlot(
              start: DateTime.parse(start),
              end: DateTime.parse(end),
            );
      _draftSaved = data['draft_saved'] as bool? ?? true;
      _confirmed = data['confirmed'] as bool? ?? false;
      _decompositionChoice = data['decomposition_choice'] as String?;
      _planLocked = data['plan_locked'] as bool? ?? false;
      _planSavedForLater = data['plan_saved_for_later'] as bool? ?? false;
      // A cached Task is not proof of a backend confirmation. It is restored
      // only after GET /tasks/{task_id} succeeds in restoreBackendState().
      _confirmedTask = null;
      _confirmed = false;
      _planLocked = false;
      _candidate = null;
      var draftId = data['backend_draft_id'] as String?;
      final candidateId = data['backend_candidate_id'] as String?;
      _clientDraftId = data['client_draft_id'] as String?;
      _clientDraftFingerprint = data['client_draft_fingerprint'] as String?;
      if (draftId == null && _clientDraftId != null) {
        final drafts = await _planningApi.listDrafts();
        final match =
            drafts.where((item) => item.clientDraftId == _clientDraftId);
        if (match.isNotEmpty) {
          _draft = match.first;
          draftId = _draft!.draftId;
        }
      }
      if (draftId != null && _draft?.draftId != draftId) {
        _draft = PlanningDraftModel(
          draftId: draftId,
          clientDraftId: _clientDraftId ?? '',
          stage: data['backend_draft_stage'] as String? ?? 'collecting',
          summary: const {},
          summaryRevision: data['summary_revision'] as int? ?? 0,
          planningInputRevision: data['planning_input_revision'] as int? ?? 0,
          inputSource: 'text',
        );
      }
      if (candidateId != null) {
        // Candidate is reloaded from the API by restoreBackendState().
        _candidate = null;
      }
      notifyListeners();
    } catch (_) {
      // A damaged local draft should not prevent the app from opening.
    }
  }

  void clearDraft() {
    _title = '';
    _scope = '';
    _deadline = null;
    _availability = null;
    _draftSaved = false;
    _confirmed = false;
    _draft = null;
    _candidate = null;
    _confirmedTask = null;
    _serverAvailability = null;
    _planLocked = false;
    _planSavedForLater = false;
    unawaited(_deleteDraft());
    notifyListeners();
  }

  void _changed() {
    _draftSaved = _title.trim().isNotEmpty;
    unawaited(_persist());
    notifyListeners();
  }

  Future<void> _persist() async {
    if (_title.trim().isEmpty) {
      await _deleteDraft();
      return;
    }
    final data = <String, Object?>{
      'title': _title,
      'scope': _scope,
      'deadline': _deadline?.toIso8601String(),
      'availability_start': _availability?.start.toIso8601String(),
      'availability_end': _availability?.end.toIso8601String(),
      'draft_saved': _draftSaved,
      'confirmed': _confirmed,
      'decomposition_choice': _decompositionChoice,
      'plan_locked': _planLocked,
      'plan_saved_for_later': _planSavedForLater,
      'confirmed_task': _confirmedTask?.toJson(),
      'backend_draft_id': _draft?.draftId,
      'client_draft_id': _clientDraftId,
      'client_draft_fingerprint': _clientDraftFingerprint,
      'backend_draft_stage': _draft?.stage,
      'backend_candidate_id': _candidate?.candidateId,
      'summary_revision': _draft?.summaryRevision,
      'planning_input_revision': _draft?.planningInputRevision,
    };
    try {
      await _storage.write(key: _storageKey, value: jsonEncode(data));
    } catch (_) {
      // Local storage may be unavailable in an unsupported browser context.
    }
  }

  Future<void> _deleteDraft() async {
    try {
      await _storage.delete(key: _storageKey);
    } catch (_) {
      // The in-memory flow remains usable if local storage is unavailable.
    }
  }
}
