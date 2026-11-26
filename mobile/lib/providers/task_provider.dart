import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/api_client.dart';
import '../models/planning_models.dart';
import '../models/task_model.dart';
import '../services/calendar_api.dart';
import '../services/planning_api.dart';

class AvailabilitySlot {
  const AvailabilitySlot({required this.start, required this.end});

  final DateTime start;
  final DateTime end;

  String get label {
    final localStart = _inVietnam(start);
    final localEnd = _inVietnam(end);
    return '${_dayLabel(localStart)}, ${_time(localStart)}–${_time(localEnd)}';
  }

  static DateTime _inVietnam(DateTime value) =>
      value.toUtc().add(const Duration(hours: 7));

  static String _dayLabel(DateTime value) {
    final today = _inVietnam(DateTime.now());
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
  final String _storageKey;
  static const _storage = FlutterSecureStorage();

  TaskProvider(
      {PlanningApi? planningApi, String? ownerId, CalendarApi? calendarApi})
      : _storageKey = ownerId == null
            ? 'proplan.uf1.current_draft.v1'
            : 'proplan.uf1.current_draft.v1.$ownerId',
        _planningApi = planningApi ?? PlanningApi(ownerId: ownerId),
        _calendarApi = calendarApi ?? CalendarApi();

  final PlanningApi _planningApi;
  final CalendarApi _calendarApi;

  String _title = '';
  String _rawInputText = '';
  String _entrySource = 'text';
  bool _isVoiceLowConfidence = false;
  String _scope = '';
  DateTime? _deadline;
  AvailabilitySlot? _availability;
  bool _draftSaved = false;
  bool _confirmed = false;
  bool _availabilityCleared = false;
  String _voiceQuestion = '';
  String? _decompositionChoice;
  bool _planLocked = false;
  bool _planSavedForLater = false;
  PlanningDraftModel? _draft;
  PlanCandidateModel? _candidate;
  TaskModel? _confirmedTask;
  String? _calendarSyncState;
  int? _confirmedScheduleRevision;
  bool get canRefreshCalendarSync =>
      _confirmedScheduleRevision != null &&
      (_confirmedTask?.sessions.isNotEmpty ?? false) &&
      _calendarSyncState == 'pending';
  String? get calendarSyncState => _calendarSyncState;
  bool _isApiBusy = false;
  String? _apiError;
  String? _clientDraftId;
  String? _clientDraftFingerprint;
  int _editRevision = 0;
  int _operationSerial = 0;
  int? _activeOperation;
  bool _disposed = false;
  Future<void> _storageTail = Future.value();

  String get title => _title;
  String get inputText => _rawInputText.isEmpty ? _title : _rawInputText;
  bool get isVoiceLowConfidence => _isVoiceLowConfidence;
  String get scope => _scope;
  DateTime? get deadline => _deadline;
  AvailabilitySlot? get availability => _availability;
  bool get availabilityCleared => _availabilityCleared;
  bool get hasAvailability =>
      (!_availabilityCleared && _availability != null) ||
      (!_availabilityCleared &&
          (draft?.summary['availability'] != null ||
              draft?.summary['availability_ref'] != null));
  bool get draftSaved => _draftSaved;
  bool get draftPersisted =>
      _title.trim().isNotEmpty && _savedEditRevision == _editRevision;
  int? _savedEditRevision;
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
  String get availabilitySummary {
    if (_availabilityCleared) {
      return 'Chưa chọn khung giờ rảnh cho công việc này.';
    }
    final selected = _availability;
    if (selected != null) return selected.label;
    final summary = _draft?.summary;
    final availability = summary?['availability'] as JsonMap?;
    if (availability != null) {
      final weekly = (availability['weekly_rules'] as List?) ??
          (availability['rules'] as List?)
              ?.whereType<JsonMap>()
              .where((rule) => rule['kind'] == 'weekly_rule')
              .toList() ??
          const [];
      final overrides = (availability['date_overrides'] as List?) ?? const [];
      final labels = [
        for (final rule in weekly.whereType<JsonMap>())
          '${_weekdayLabel(rule['weekday'])} ${_intervalLabel(rule)}',
        for (final day in overrides.whereType<JsonMap>())
          for (final interval
              in (day['intervals'] as List).whereType<JsonMap>())
            '${_availabilityDayLabel(day['date'], availability['timezone'])} ${_intervalLabel(interval)}',
      ];
      if (labels.isNotEmpty) return labels.join(' · ');
    }
    if (summary?['availability_ref'] != null) {
      return 'Dùng lịch rảnh đã lưu.';
    }
    return 'Chưa chọn khung giờ rảnh cho công việc này.';
  }

  static String _weekdayLabel(Object? weekday) =>
      weekday == 7 ? 'Chủ nhật' : 'Thứ ${(weekday as int) + 1}';

  static String _intervalLabel(JsonMap interval) {
    String shortTime(Object? value) =>
        value is String ? value.split(':').take(2).join(':') : '—';
    return '${shortTime(interval['start_local'])}–${shortTime(interval['end_local'])}';
  }

  static String _availabilityDayLabel(Object? date, Object? timezone) {
    if (date is! String) return '';
    if (timezone != PlanningApi.defaultTimezone) return date;
    final day = DateTime.tryParse(date);
    return day == null ? date : AvailabilitySlot._dayLabel(day);
  }

  JsonMap? get _pendingAvailability {
    final slot = _availability;
    if (slot == null) return null;
    final start = AvailabilitySlot._inVietnam(slot.start);
    final end = AvailabilitySlot._inVietnam(slot.end);
    String two(int value) => value.toString().padLeft(2, '0');
    String date(DateTime value) =>
        '${value.year}-${two(value.month)}-${two(value.day)}';
    return {
      'timezone': PlanningApi.defaultTimezone,
      'date_overrides': [
        {
          'date': date(start),
          'intervals': [
            {
              'start_local': AvailabilitySlot._time(start),
              'end_local': AvailabilitySlot._time(end),
            },
          ],
        },
      ],
    };
  }

  Future<void> restoreCurrentDraft() async {
    final revision = _editRevision;
    try {
      await restoreDraft();
      if (!_disposed && revision == _editRevision) await restoreBackendState();
    } on ApiException catch (error) {
      if (!_disposed && revision == _editRevision) {
        _apiError = error.message;
        notifyListeners();
      }
    }
  }

  Future<void> restoreBackendState() async {
    final revision = _editRevision;
    final stored = await _accept(_storage.read(key: _storageKey), revision);
    if (stored == null) return;
    try {
      final data = jsonDecode(stored) as Map<String, dynamic>;
      final draftId = data['backend_draft_id'] as String? ?? _draft?.draftId;
      final candidateId = data['backend_candidate_id'] as String?;
      if (draftId == null) return;
      _draft = await _accept(_planningApi.getDraft(draftId), revision);
      _applyDraftSummary();
      // Local storage is only a cache. Re-read any confirmed Task from the API
      // so a backend restart cannot leave a stale "confirmed" screen behind.
      _candidate = null;
      _confirmedTask = null;
      _confirmed = false;
      _planLocked = false;
      if (candidateId != null) {
        try {
          _candidate = await _accept(
            _planningApi.getCandidate(candidateId),
            revision,
          );
        } on ApiException catch (error) {
          if (error.code == 'REQUEST_SUPERSEDED') rethrow;
          // Candidate service is currently in-memory on backend; keep the draft usable.
        }
      }
      final savedTask = data['confirmed_task'] as Map<String, dynamic>?;
      final savedTaskId = savedTask?['task_id'] as String?;
      if (savedTaskId != null) {
        try {
          _confirmedTask = await _accept(
            _planningApi.getTask(savedTaskId),
            revision,
          );
          _confirmed = true;
          _planLocked = true;
        } on ApiException catch (error) {
          if (error.code == 'REQUEST_SUPERSEDED') rethrow;
          // Do not show a cached task as confirmed if the server no longer has it.
        }
      }
      await _persist();
      notifyListeners();
    } on ApiException catch (error) {
      if (_disposed || revision != _editRevision) return;
      _apiError = error.message;
      notifyListeners();
    }
  }

  Future<T> _accept<T>(Future<T> request, int revision) async {
    final result = await request;
    if (_disposed || revision != _editRevision) {
      throw const ApiException(
        'Nội dung đã thay đổi. Kết quả cũ được bỏ qua.',
        code: 'REQUEST_SUPERSEDED',
      );
    }
    return result;
  }

  Future<void> prepareDraftAndAnalyze() async {
    if (_title.trim().isEmpty)
      throw const ApiException('Hãy nhập nội dung công việc trước.');
    await _runApi((revision) async {
      var current = await _saveInput(revision);
      current = await _accept(_planningApi.analyzeDraft(current), revision);
      _draft = current;
      _applyDraftSummary();
      await _persist();
    });
  }

  bool get hasUnconfirmedDraft =>
      !_planLocked && (_draft != null || _title.trim().isNotEmpty);

  Future<void> analyzeThoughtDraft(PlanningDraftModel draft) async {
    if (draft.stage == 'confirmed') {
      throw const ApiException('Suy nghĩ này đã được tạo thành công việc.');
    }
    if (hasUnconfirmedDraft) {
      throw const ApiException('Hãy lưu hoặc hoàn tất bản nháp đang mở.');
    }
    await _runApi((revision) async {
      _draft = draft;
      _rawInputText = draft.rawInput ?? draft.title;
      _entrySource = 'text';
      _isVoiceLowConfidence = false;
      _clientDraftId = draft.clientDraftId;
      _clientDraftFingerprint = null;
      _title = draft.title.isEmpty ? _rawInputText : draft.title;
      _draftSaved = true;
      _scope = '';
      _deadline = null;
      _availability = null;
      _confirmedTask = null;
      _calendarSyncState = null;
      _confirmedScheduleRevision = null;
      _candidate = null;
      _confirmed = false;
      _planLocked = false;
      _planSavedForLater = false;
      _applyDraftSummary();
      await _persist();
      _draft = await _accept(_planningApi.analyzeDraft(draft), revision);
      _applyDraftSummary();
      await _persist();
    });
  }

  Future<void> submitClarification(String message) async {
    final answer = message.trim();
    if (answer.isEmpty) {
      throw const ApiException('Nhập câu trả lời trước khi gửi.');
    }
    await _runApi((revision) async {
      final current = await _saveInput(revision);
      final analyzed = await _accept(
        _planningApi.analyzeDraft(current, message: answer),
        revision,
      );
      _draft = analyzed;
      _applyDraftSummary();
      await _persist();
    });
  }

  Future<PlanningDraftModel> _saveInput(int revision) async {
    final title = _title.trim(), scope = _scope.trim();
    final input = inputText.trim();
    final deadline = _deadline;
    final existing = _draft;
    final availability = _pendingAvailability;
    PlanningDraftModel current;
    if (existing == null || existing.rawInput != input) {
      if (existing != null) {
        _clientDraftId = null;
        _clientDraftFingerprint = null;
      }
      _ensureClientDraftId();
      await _persist();
      if (revision != _editRevision)
        throw const ApiException(
          'Nội dung đã thay đổi.',
          code: 'REQUEST_SUPERSEDED',
        );
      current = await _accept(
        _planningApi.createDraft(
          clientDraftId: _clientDraftId!,
          rawInput: input,
          inputSource: _entrySource,
          title: title,
          scope: scope,
          deadline: deadline,
          availability: availability,
        ),
        revision,
      );
    } else {
      current = await _accept(
        _planningApi.updateSummary(
          draft: existing,
          title: title,
          scope: scope,
          deadline: deadline,
          availability: availability,
          clearAvailability: _availabilityCleared,
        ),
        revision,
      );
    }
    _draft = current;
    _applyDraftSummary();
    _availability = null;
    _availabilityCleared = false;
    _candidate = null;
    await _persist();
    return current;
  }

  Future<void> saveDraftToBackend() async {
    if (_title.trim().isEmpty)
      throw const ApiException('Nhập nội dung công việc để lưu nháp.');
    await _runApi((revision) async {
      await _saveInput(revision);
    });
  }

  Future<void> updateDraftOnBackend({String? extraRequirement}) async {
    final draft = _draft;
    final availability = _pendingAvailability;
    final clearAvailability = _availabilityCleared;
    if (draft == null)
      throw const ApiException('Chưa có bản nháp trên máy chủ.');
    final extras = (draft.summary['extra_requirements'] as List? ?? const [])
        .whereType<String>()
        .toSet();
    final extra = extraRequirement?.trim() ?? '';
    final extraRequirements =
        extra.isNotEmpty && extras.add(extra) ? extras.toList() : null;
    await _runApi((revision) async {
      final current = await _accept(
        _planningApi.updateSummary(
          draft: draft,
          title: _title.trim(),
          scope: _scope.trim(),
          deadline: _deadline,
          availability: availability,
          clearAvailability: clearAvailability,
          extraRequirements: extraRequirements,
        ),
        revision,
      );
      _draft = current;
      _availability = null;
      _availabilityCleared = false;
      _applyDraftSummary();
      _candidate = null;
      await _persist();
    });
  }

  Future<void> confirmDraftSummaryOnBackend() async {
    final draft = _draft;
    if (draft == null)
      throw const ApiException(
        'Cần lưu và kiểm tra bản nháp trên máy chủ trước.',
      );
    if (!draft.canPlan) {
      final questions = draft.clarificationIssues
          .where((issue) => issue['blocking'] == true)
          .map((i) => i['question'] ?? i['message'])
          .whereType<String>();
      throw ApiException(
        questions.isEmpty
            ? 'Bổ sung các thông tin còn thiếu trước khi lập kế hoạch.'
            : questions.join('\n'),
      );
    }
    await _runApi((revision) async {
      await _accept(_planningApi.confirmSummary(draft), revision);
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

  /// [useManualAvailability]: user đã thấy lỗi đọc Google Calendar và chủ động
  /// chọn xếp theo giờ rảnh nhập tay (W3-15: thu hồi quyền vẫn lập được lịch).
  Future<void> generateCandidateOnBackend({
    String? stepProposal,
    bool useManualAvailability = false,
  }) async {
    final source = _draft;
    if (source == null ||
        source.summaryConfirmedRevision != source.summaryRevision) {
      throw const ApiException('Hãy xác nhận thông tin công việc trước.');
    }
    await _runApi((revision) async {
      var current = source;
      final proposal = stepProposal?.trim();
      if (proposal != null && proposal.isNotEmpty) {
        current = await _accept(
          _planningApi.updateStepProposal(draft: current, proposal: proposal),
          revision,
        );
        _draft = current;
      }
      // Chụp lịch bận Google (nếu user đã chọn lịch đọc) để thuật toán xếp
      // phiên né giờ bận. Không có lịch nào → null, xếp theo giờ rảnh như cũ.
      final calendarContextRef = await _createCalendarContext(
        current,
        useManualAvailability: useManualAvailability,
      );
      final generated = await _accept(
        _planningApi.createCandidate(
          current,
          calendarContextRef: calendarContextRef,
        ),
        revision,
      );
      _candidate = generated;
      await _persist();
    });
  }

  /// Khoảng chụp lịch bận = [start_at, hạn chót] của Draft: luôn phủ trọn
  /// phạm vi Backend xếp lịch, và giữ cố định giữa các lần xếp lại (Backend
  /// không cho bản chụp mới hẹp hơn bản cũ).
  Future<Map<String, dynamic>?> _createCalendarContext(
    PlanningDraftModel draft, {
    bool useManualAvailability = false,
  }) async {
    final constraint = draft.summary['time_constraint'];
    if (constraint is! Map) return null;
    DateTime? read(String key) =>
        DateTime.tryParse(constraint[key] as String? ?? '');
    final end = read('deadline_at') ?? read('finish_by_at');
    if (end == null) return null;
    final start = read('start_at') ?? DateTime.now();
    if (!start.isBefore(end)) return null;
    return _calendarApi.createPlanningContextForDraft(
      draftId: draft.draftId,
      summaryRevision: draft.summaryRevision,
      from: start,
      to: end,
      useManualAvailability: useManualAvailability,
    );
  }

  Future<void> refreshCandidateFromBackend() async {
    final current = _candidate;
    if (current == null) return;
    await _runApi((revision) async {
      _candidate = await _accept(
        _planningApi.getCandidate(current.candidateId),
        revision,
      );
      await _persist();
    });
  }

  Future<void> editCandidateStep({
    required String clientStepKey,
    required String title,
    required String expectedOutput,
    required int estimatedBaseSeconds,
  }) async {
    final current = _candidate;
    if (current == null) throw const ApiException('Chưa có phương án để sửa.');
    await _runApi((revision) async {
      _candidate = await _accept(
        _planningApi.updateCandidateStep(
          candidate: current,
          clientStepKey: clientStepKey,
          title: title,
          expectedOutput: expectedOutput,
          estimatedBaseSeconds: estimatedBaseSeconds,
        ),
        revision,
      );
      await _persist();
    });
  }

  Future<void> confirmCandidateOnBackend() async {
    final candidate = _candidate, draft = _draft;
    if (candidate == null || draft == null)
      throw const ApiException('Chưa có phương án từ máy chủ để xác nhận.');
    if (!candidate.isFeasible)
      throw const ApiException(
        'Phương án chưa khả thi hoặc đã cũ. Hãy tính lại.',
      );
    await _runApi((revision) async {
      final receipt = await _accept(
        _planningApi.confirmCandidate(candidate: candidate, draft: draft),
        revision,
      );
      final saved = TaskModel.fromJson(receipt['task'] as Map<String, dynamic>);
      if (saved.taskId.isEmpty)
        throw const ApiException('Máy chủ chưa trả công việc đã lưu.');
      _confirmedTask = saved;
      _calendarSyncState = receipt['calendar_sync_summary'] as String?;
      _confirmedScheduleRevision =
          (receipt['schedule_version'] as Map?)?['version_number'] as int?;
      _planLocked = true;
      _planSavedForLater = false;
      _confirmed = true;
      await _persist();
    });
  }

  Future<void> refreshCalendarSync() async {
    if (!canRefreshCalendarSync) return;
    final sessions = _confirmedTask!.sessions;
    final from = sessions
        .map((item) => item.scheduledStart)
        .reduce((a, b) => a.isBefore(b) ? a : b);
    final to = sessions
        .map((item) => item.scheduledEnd)
        .reduce((a, b) => a.isAfter(b) ? a : b);
    await _runApi((revision) async {
      final state = await _accept(
          _planningApi.refreshCalendarSync(
            scheduleRevision: _confirmedScheduleRevision!,
            from: from,
            to: to,
          ),
          revision);
      if (state != null) {
        _calendarSyncState = state;
        await _persist();
      }
    });
  }

  Future<void> reviseDeadlineAndRegenerate(DateTime deadline) async {
    setDeadline(deadline);
    await updateDraftOnBackend();
    await confirmDraftSummaryOnBackend();
    await generateCandidateOnBackend();
  }

  Future<void> _runApi(Future<void> Function(int revision) action) async {
    if (_isApiBusy)
      throw const ApiException(
        'Đang xử lý yêu cầu trước. Vui lòng chờ.',
        code: 'REQUEST_IN_PROGRESS',
      );
    final operation = ++_operationSerial, revision = _editRevision;
    _activeOperation = operation;
    _isApiBusy = true;
    _apiError = null;
    notifyListeners();
    try {
      await action(revision);
    } on ApiException catch (error) {
      if (!_disposed &&
          revision == _editRevision &&
          _activeOperation == operation) _apiError = error.message;
      rethrow;
    } finally {
      if (!_disposed && _activeOperation == operation) {
        _activeOperation = null;
        _isApiBusy = false;
        notifyListeners();
      }
    }
  }

  Future<void> startNewDraft() async {
    if (_isApiBusy)
      throw const ApiException('Đang xử lý công việc hiện tại. Vui lòng chờ.');
    if (_title.trim().isNotEmpty && !_planLocked) await saveDraftToBackend();
    clearDraft();
  }

  void _ensureClientDraftId() {
    final fingerprint = jsonEncode([
      _title.trim(),
      _scope.trim(),
      _deadline?.toIso8601String(),
      _pendingAvailability,
    ]);
    if (_clientDraftId == null || _clientDraftFingerprint != fingerprint) {
      _clientDraftId = ApiClient.newIdempotencyKey('draft');
      _clientDraftFingerprint = fingerprint;
    }
  }

  void _applyDraftSummary() {
    final summary = _draft?.summary;
    if (summary == null) return;
    _rawInputText = _draft?.rawInput ?? _rawInputText;
    final suggestedTitle =
        summary['title'] ?? summary['goal'] ?? summary['description'];
    if (suggestedTitle is String && suggestedTitle.trim().isNotEmpty) {
      _title = suggestedTitle.trim();
    }
    if (summary.containsKey('scope')) {
      _scope = (summary['scope'] as String? ?? '').trim();
    }
    final timeConstraint = summary['time_constraint'] as Map<String, dynamic>?;
    final deadline = timeConstraint?['deadline_at'] as String? ??
        timeConstraint?['finish_by_at'] as String?;
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
    if (_confirmedTask == null || _confirmedTask!.taskId.isEmpty) {
      throw const ApiException(
        'Chỉ chốt kế hoạch sau khi máy chủ xác nhận đã lưu.',
      );
    }
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
    final localValue = value.toLocal();
    final date = DateTime(localValue.year, localValue.month, localValue.day);
    final todayDate = DateTime(today.year, today.month, today.day);
    final dateLabel = date == todayDate
        ? 'Hôm nay'
        : date == todayDate.add(const Duration(days: 1))
            ? 'Ngày mai'
            : date == todayDate.add(const Duration(days: 2))
                ? 'Ngày kia'
                : date.day.toString().padLeft(2, '0') +
                    '/' +
                    date.month.toString().padLeft(2, '0') +
                    '/' +
                    date.year.toString();
    return dateLabel +
        ', ' +
        localValue.hour.toString().padLeft(2, '0') +
        ':' +
        localValue.minute.toString().padLeft(2, '0');
  }

  bool applyExplicitAvailabilityAnswer(String answer) {
    final text = answer.trim();
    const day = r'(hôm nay|ngày mai|ngày kia)';
    const hours = r'(?:từ\s+)?([01]?\d|2[0-3]):([0-5]\d)\s*(?:đến|[-–—])\s*'
        r'([01]?\d|2[0-3]):([0-5]\d)';
    final dayFirst =
        RegExp('^$day\\s+$hours[.!]?\$', caseSensitive: false).firstMatch(text);
    final dayLast =
        RegExp('^$hours\\s+$day[.!]?\$', caseSensitive: false).firstMatch(text);
    final match = dayFirst ?? dayLast;
    if (match == null) return false;
    final hourIndex = dayFirst == null ? 1 : 2;
    final dayIndex = dayFirst == null ? 5 : 1;
    final now = DateTime.now();
    final vn = AvailabilitySlot._inVietnam(now);
    final offset = switch (match[dayIndex]!.toLowerCase()) {
      'ngày mai' => 1,
      'ngày kia' => 2,
      _ => 0,
    };
    final date = DateTime.utc(vn.year, vn.month, vn.day + offset);
    DateTime instant(int hourIndex, int minuteIndex) => date.add(Duration(
          hours: int.parse(match[hourIndex]!) - 7,
          minutes: int.parse(match[minuteIndex]!),
        ));
    final start = instant(hourIndex, hourIndex + 1);
    final end = instant(hourIndex + 2, hourIndex + 3);
    if (!end.isAfter(start) || !end.isAfter(now)) {
      throw const ApiException(
          'Chọn khung giờ còn hiệu lực, kết thúc sau giờ bắt đầu.');
    }
    if (_deadline case final deadline? when end.isAfter(deadline)) {
      throw const ApiException('Giờ kết thúc phải trước hoặc bằng hạn chót.');
    }
    setAvailability(AvailabilitySlot(start: start, end: end));
    return true;
  }

  /// Prefill only explicit, single clauses in free text. Other wording still
  /// goes through the normal analysis flow, and existing form values win.
  void fillExplicitPlanningInput() {
    final input = inputText;
    final now = DateTime.now();
    final vn = AvailabilitySlot._inVietnam(now);
    final day = DateTime.utc(vn.year, vn.month, vn.day);
    const relativeDay = r'(hôm nay|ngày mai|ngày kia)';
    const time = r'([01]?\d|2[0-3]):([0-5]\d)';
    const clauseStart = r'(?:^|[.!?\n]\s*)';
    const clauseEnd = r'\s*(?:[.!?\n]|$)';

    RegExpMatch? single(String pattern) {
      final matches = RegExp(pattern, caseSensitive: false).allMatches(input);
      final iterator = matches.iterator;
      if (!iterator.moveNext()) return null;
      final match = iterator.current;
      return iterator.moveNext() ? null : match;
    }

    DateTime instant(String date, String hour, String minute) {
      final days = switch (date.toLowerCase()) {
        'ngày mai' => 1,
        'ngày kia' => 2,
        _ => 0,
      };
      return day.add(Duration(
        days: days,
        hours: int.parse(hour) - 7,
        minutes: int.parse(minute),
      ));
    }

    final deadline = single(
      '${clauseStart}Hạn(?: chót)?(?: lúc)?\\s+$time\\s+$relativeDay$clauseEnd',
    );
    if (_deadline == null && deadline != null) {
      final end = instant(deadline[3]!, deadline[1]!, deadline[2]!);
      if (end.isAfter(now)) setDeadline(end.toLocal());
    }

    final available = single(
      '${clauseStart}Tôi rảnh\\s+([^.!?\\n]+)$clauseEnd',
    );
    if (!hasAvailability && available != null) {
      try {
        applyExplicitAvailabilityAnswer(available[1]!);
      } on ApiException {
        // Leave invalid ranges to the summary review.
      }
    }

    final scope = single(
      r'(?:^|[.!?\n]\s*)Đầu ra\s*(?:là\s+|:\s*)(.+?)(?=[.!?](?:\s|$)|\n|$)',
    );
    if (_scope.trim().isEmpty && scope != null) setScope(scope[1]!.trim());
  }

  void setTitle(
    String value, {
    bool fromVoice = false,
    bool lowConfidence = false,
  }) {
    final nextLowConfidence = fromVoice ? lowConfidence : false;
    if (inputText.trim() == value.trim()) {
      if (fromVoice || _isVoiceLowConfidence != nextLowConfidence) {
        _isVoiceLowConfidence = nextLowConfidence;
        if (fromVoice && value.trim().isNotEmpty) {
          _entrySource = 'voice';
        }
        unawaited(_persist());
        notifyListeners();
      }
      return;
    }
    _isVoiceLowConfidence = nextLowConfidence;
    if (_draft != null) {
      _scope = '';
      _deadline = null;
      _availability = null;
      _availabilityCleared = false;
    }
    _entrySource =
        fromVoice || (_entrySource == 'voice' && value.trim().isNotEmpty)
            ? 'voice'
            : 'text';
    if (_title != value) {
      _candidate = null;
      _confirmedTask = null;
      _planLocked = false;
    }
    _title = value;
    _rawInputText = value;
    _confirmed = false;
    _changed();
  }

  void setScope(String value) {
    if (_scope == value) return;
    if (_scope != value) _candidate = null;
    _scope = value;
    _confirmed = false;
    _changed();
  }

  void setSummaryTitle(String value) {
    if (_title == value) return;
    _title = value;
    _candidate = null;
    _confirmed = false;
    _changed();
  }

  void setDeadline(DateTime value) {
    if (_deadline == value) return;
    if (_deadline != value) _candidate = null;
    _deadline = value;
    _confirmed = false;
    _changed();
  }

  void setAvailability(AvailabilitySlot value) {
    _availability = value;
    _availabilityCleared = false;
    _candidate = null;
    _confirmed = false;
    _changed();
  }

  AvailabilitySlot? clearAvailability() {
    if (!hasAvailability) return null;
    final previous = _availability;
    _availability = null;
    _availabilityCleared = true;
    _candidate = null;
    _confirmed = false;
    _changed();
    return previous;
  }

  void undoClearAvailability(AvailabilitySlot? previous) {
    if (!_availabilityCleared) return;
    _availability = previous;
    _availabilityCleared = false;
    _candidate = null;
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
    final revision = _editRevision;
    try {
      final value = await _accept(_storage.read(key: _storageKey), revision);
      if (value == null) return;
      final data = jsonDecode(value) as Map<String, dynamic>;
      _title = data['title'] as String? ?? '';
      _rawInputText = data['raw_input_text'] as String? ?? _title;
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
      _availabilityCleared = data['availability_cleared'] as bool? ?? false;
      _entrySource = data['entry_source'] == 'voice' ? 'voice' : 'text';
      _isVoiceLowConfidence = false;
      _draftSaved = data['draft_saved'] as bool? ?? true;
      _confirmed = data['confirmed'] as bool? ?? false;
      _decompositionChoice = data['decomposition_choice'] as String?;
      _planLocked = data['plan_locked'] as bool? ?? false;
      _calendarSyncState = data['calendar_sync_state'] as String?;
      _confirmedScheduleRevision = data['confirmed_schedule_revision'] as int?;
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
        final drafts = await _accept(_planningApi.listDrafts(), revision);
        final match = drafts.where(
          (item) => item.clientDraftId == _clientDraftId,
        );
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

  Future<void> deleteCurrentDraft() async {
    if (_isApiBusy) return;
    final draft = _draft;
    if (draft != null && draft.confirmedTaskId == null) {
      _isApiBusy = true;
      _apiError = null;
      notifyListeners();
      try {
        await _planningApi.deleteDraft(draft.draftId);
      } on ApiException catch (error) {
        if (error.statusCode != 404) {
          _isApiBusy = false;
          _apiError = error.message;
          notifyListeners();
          rethrow;
        }
      } finally {
        _isApiBusy = false;
      }
    }
    clearDraft();
  }

  void clearApiError() {
    if (_apiError == null) return;
    _apiError = null;
    notifyListeners();
  }

  void clearConfirmedTaskIfMatches(String taskId) {
    if (_confirmedTask?.taskId != taskId) return;
    _confirmedTask = null;
    _calendarSyncState = null;
    _confirmedScheduleRevision = null;
    _planLocked = false;
    _confirmed = false;
    unawaited(_persist());
    notifyListeners();
  }

  Future<List<PlanningDraftModel>> fetchOpenDrafts() async {
    final all = await _planningApi.listDrafts();
    return all
        .where((d) =>
            (d.confirmedTaskId == null || d.confirmedTaskId!.isEmpty) &&
            d.stage != 'confirmed' &&
            d.stage != 'discarded')
        .toList(growable: false);
  }

  Future<void> openServerDraft(PlanningDraftModel draft) async {
    _editRevision++;
    _activeOperation = null;
    _isApiBusy = false;
    _apiError = null;
    _draft = draft;
    _clientDraftId =
        draft.clientDraftId.isNotEmpty ? draft.clientDraftId : null;
    _clientDraftFingerprint = null;
    _entrySource = draft.inputSource;
    _rawInputText = draft.rawInput ?? '';
    _title = draft.title.isNotEmpty ? draft.title : (draft.rawInput ?? '');
    _scope = draft.scope;
    _deadline = null;
    _availability = null;
    _availabilityCleared = false;
    _applyDraftSummary();
    _candidate = null;
    _confirmedTask = null;
    _calendarSyncState = null;
    _confirmedScheduleRevision = null;
    _planLocked = false;
    _planSavedForLater = false;
    _confirmed = false;
    _draftSaved = true;
    final candidateId = draft.candidateRef?['candidate_id'] as String?;
    if (candidateId != null && candidateId.isNotEmpty) {
      try {
        _candidate = await _planningApi.getCandidate(candidateId);
      } on ApiException {
        // Keep draft usable even if candidate cannot be fetched.
      }
    }
    await _persist();
    notifyListeners();
  }

  Future<void> deleteDraftById(String draftId) async {
    if (_isApiBusy) return;
    _isApiBusy = true;
    _apiError = null;
    notifyListeners();
    try {
      try {
        await _planningApi.deleteDraft(draftId);
      } on ApiException catch (error) {
        if (error.statusCode != 404) {
          _apiError = error.message;
          notifyListeners();
          rethrow;
        }
      }
    } finally {
      _isApiBusy = false;
    }
    if (_draft?.draftId == draftId) {
      clearDraft();
    } else {
      notifyListeners();
    }
  }

  void clearDraft() {
    _editRevision++;
    _activeOperation = null;
    _isApiBusy = false;
    _apiError = null;
    _clientDraftId = null;
    _clientDraftFingerprint = null;
    _voiceQuestion = '';
    _decompositionChoice = null;
    _title = '';
    _rawInputText = '';
    _entrySource = 'text';
    _isVoiceLowConfidence = false;
    _scope = '';
    _deadline = null;
    _availability = null;
    _availabilityCleared = false;
    _draftSaved = false;
    _confirmed = false;
    _draft = null;
    _candidate = null;
    _confirmedTask = null;
    _calendarSyncState = null;
    _confirmedScheduleRevision = null;
    _planLocked = false;
    _planSavedForLater = false;
    unawaited(_deleteDraft());
    notifyListeners();
  }

  void _changed() {
    _editRevision++;
    _draftSaved = _title.trim().isNotEmpty;
    unawaited(_persist());
    notifyListeners();
  }

  Future<void> _persist() async {
    if (_title.trim().isEmpty) {
      await _deleteDraft();
      return;
    }
    final revision = _editRevision;
    final data = <String, Object?>{
      'title': _title,
      'raw_input_text': _rawInputText,
      'entry_source': _entrySource,
      'scope': _scope,
      'deadline': _deadline?.toIso8601String(),
      'availability_start': _availability?.start.toIso8601String(),
      'availability_end': _availability?.end.toIso8601String(),
      'availability_cleared': _availabilityCleared,
      'draft_saved': _draftSaved,
      'confirmed': _confirmed,
      'decomposition_choice': _decompositionChoice,
      'plan_locked': _planLocked,
      'plan_saved_for_later': _planSavedForLater,
      'confirmed_task': _confirmedTask?.toJson(),
      'calendar_sync_state': _calendarSyncState,
      'confirmed_schedule_revision': _confirmedScheduleRevision,
      'backend_draft_id': _draft?.draftId,
      'client_draft_id': _clientDraftId,
      'client_draft_fingerprint': _clientDraftFingerprint,
      'backend_draft_stage': _draft?.stage,
      'backend_candidate_id': _candidate?.candidateId,
      'summary_revision': _draft?.summaryRevision,
      'planning_input_revision': _draft?.planningInputRevision,
    };
    try {
      final encoded = jsonEncode(data);
      final write = _storageTail.then(
        (_) => _storage.write(key: _storageKey, value: encoded),
      );
      _storageTail = write.catchError((Object _) {});
      await write;
      if (!_disposed &&
          revision == _editRevision &&
          _savedEditRevision != revision) {
        _savedEditRevision = revision;
        notifyListeners();
      }
    } catch (_) {
      // Local storage may be unavailable in an unsupported browser context.
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _editRevision++;
    super.dispose();
  }

  Future<void> _deleteDraft() async {
    try {
      final write = _storageTail.then((_) => _storage.delete(key: _storageKey));
      _storageTail = write.catchError((Object _) {});
      await write;
    } catch (_) {
      // The in-memory flow remains usable if local storage is unavailable.
    }
  }
}
