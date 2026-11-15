import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/api_client.dart';
import '../models/planning_models.dart';
import '../models/task_model.dart';

class PlanningApi {
  static const defaultTimezone = 'Asia/Ho_Chi_Minh';

  PlanningApi(
      {ApiClient? client,
      FlutterSecureStorage? storage,
      String? ownerId,
      DateTime Function()? now})
      : _client = client ?? ApiClient(),
        _storage = storage ?? const FlutterSecureStorage(),
        _ownerId = ownerId,
        _now = now ?? DateTime.now;

  final ApiClient _client;
  final FlutterSecureStorage _storage;
  final String? _ownerId;
  final DateTime Function() _now;

  // Một lệnh chưa biết kết quả giữ nguyên cả body và key, kể cả khi mở lại app.
  Future<JsonMap> _command({
    required String operation,
    required JsonMap intent,
    required JsonMap Function() buildBody,
    required Future<JsonMap> Function(JsonMap body, String key) send,
    String? fixedKey,
    bool retainCompleted = false,
  }) async {
    final owner = _ownerId;
    final slot = owner == null
        ? 'planning_command_$operation'
        : 'planning_command_${owner}_$operation';
    final signature = jsonEncode(intent);
    final stored = await _storage.read(key: slot);
    JsonMap? command;
    if (stored != null) {
      final previous = Map<String, dynamic>.from(jsonDecode(stored) as Map);
      if (previous['intent'] == signature) command = previous;
    }
    command ??= {
      'intent': signature,
      'key': fixedKey ?? ApiClient.newIdempotencyKey('planning'),
      'body': buildBody(),
    };
    await _storage.write(key: slot, value: jsonEncode(command));
    try {
      final result = await send(
          Map<String, dynamic>.from(command['body'] as Map),
          command['key'] as String);
      if (!retainCompleted) await _storage.delete(key: slot);
      return result;
    } on ApiException catch (error) {
      final status = error.statusCode;
      // Lỗi mạng/timeout/5xx có thể mất response sau commit: giữ lệnh để retry.
      if (status != null &&
          status >= 400 &&
          status < 500 &&
          status != 401 &&
          status != 408 &&
          status != 429) {
        await _storage.delete(key: slot);
      }
      rethrow;
    }
  }

  Future<String?> refreshCalendarSync({
    required int scheduleRevision,
    required DateTime from,
    required DateTime to,
  }) async {
    final connections = await _client.getListData('/calendar/connections');
    final connection = connections
        .where((item) =>
            item['connection_state'] == 'connected' &&
            item['write_calendar_id'] != null &&
            item['connection_id'] is String)
        .firstOrNull;
    if (connection == null) return null;
    final id = Uri.encodeComponent(connection['connection_id'] as String);
    await _client.postData('/calendar/connections/$id/refresh',
        data: const <String, dynamic>{}, idempotent: false);
    final schedule = await _client.getData('/schedule', queryParameters: {
      'from': from.toUtc().toIso8601String(),
      'to': to.toUtc().toIso8601String(),
    });
    if (schedule['schedule_revision'] != scheduleRevision) return null;
    return schedule['calendar_sync_summary'] as String?;
  }

  Future<PlanningDraftModel> getDraft(String draftId) async =>
      PlanningDraftModel.fromJson(await _client.getData(
        '/drafts/${Uri.encodeComponent(draftId)}',
      ));

  Future<List<PlanningDraftModel>> listDrafts() async {
    final data =
        await _client.getData('/drafts', queryParameters: {'limit': 50});
    return (data['items'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(PlanningDraftModel.fromJson)
        .toList(growable: false);
  }

  Future<PlanningDraftModel> createDraft({
    required String clientDraftId,
    required String rawInput,
    required String title,
    required String scope,
    required DateTime? deadline,
    JsonMap? availability,
    String inputSource = 'text',
  }) async {
    final intent = <String, dynamic>{
      'client_draft_id': clientDraftId,
      'input_source': inputSource,
      'raw_input': rawInput,
      'title': title,
      'scope': scope,
      'deadline': deadline?.toUtc().toIso8601String(),
      if (availability != null) 'availability': availability,
    };
    final json = await _command(
      operation: 'create_$clientDraftId',
      intent: intent,
      fixedKey: clientDraftId,
      retainCompleted: true,
      buildBody: () => {
        'client_draft_id': clientDraftId,
        'input_source': inputSource,
        'raw_input': rawInput,
        'summary': {
          if (title.trim().isNotEmpty &&
              title != rawInput &&
              title.length <= 100)
            'title': title,
          if (availability != null) 'availability': availability,
          'importance': 3,
          if (scope.trim().isNotEmpty) 'scope': scope.trim(),
          if (deadline != null)
            'time_constraint': {
              'kind': 'deadline',
              'start_at': _vnIso(_now()),
              'deadline_at': _vnIso(deadline),
              'finish_by_at': null,
              'input_timezone': defaultTimezone,
            },
        },
      },
      send: (body, key) =>
          _client.postData('/drafts', idempotencyKey: key, data: body),
    );
    return PlanningDraftModel.fromJson(json);
  }

  Future<PlanningDraftModel> analyzeDraft(
    PlanningDraftModel draft, {
    String? message,
  }) async {
    final json = await _client.postData(
      '/drafts/${Uri.encodeComponent(draft.draftId)}/analyze',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'analyze_${draft.draftId}',
        '${draft.summaryRevision}_${draft.planningInputRevision}_${draft.rawInput}_${message ?? ''}',
      ),
      data: {
        'expected_summary_revision': draft.summaryRevision,
        'expected_planning_input_revision': draft.planningInputRevision,
        'raw_input': draft.rawInput ?? draft.title,
        if (message != null && message.trim().isNotEmpty)
          'message': message.trim(),
      },
    );
    final analyzed = json['summary'] as JsonMap? ?? const {};
    return PlanningDraftModel.fromJson({
      ...json,
      'raw_input': json['raw_input'] ?? draft.rawInput,
      'input_source': json['input_source'] ?? draft.inputSource,
      'client_draft_id': json['client_draft_id'] ?? draft.clientDraftId,
      'summary': {
        ...draft.summary,
        // Validation is always owned by the latest analysis response.
        'can_plan': false,
        'clarification_issues': const [],
        ...analyzed,
      },
    });
  }

  Future<PlanningDraftModel> updateSummary({
    required PlanningDraftModel draft,
    required String title,
    required String scope,
    required DateTime? deadline,
    JsonMap? availability,
    List<String>? extraRequirements,
  }) async {
    final oldTime = draft.summary['time_constraint'] as Map<String, dynamic>?;
    final oldEnd = DateTime.tryParse(
        (oldTime?['deadline_at'] ?? oldTime?['finish_by_at']) as String? ?? '');
    final deadlineChanged = deadline != null &&
        (oldEnd == null || !deadline.isAtSameMomentAs(oldEnd));
    final changes = <String, dynamic>{
      if (title.length <= 100 && title != draft.title) 'title': title,
      if (scope != draft.scope) 'scope': scope,
      if (availability != null) 'availability': availability,
      if (extraRequirements != null) 'extra_requirements': extraRequirements,
      if (deadlineChanged)
        'time_constraint': {
          'kind': 'deadline',
          'start_at': oldTime?['start_at'] ?? _vnIso(_now()),
          'deadline_at': _vnIso(deadline),
          'finish_by_at': null,
          'input_timezone': oldTime?['input_timezone'] ?? defaultTimezone,
        },
    };
    if (changes.isEmpty) return draft;
    final intent = <String, dynamic>{
      'summary_revision': draft.summaryRevision,
      'input_revision': draft.planningInputRevision,
      'title': title,
      'scope': scope,
      'deadline': deadline?.toUtc().toIso8601String(),
      if (availability != null) 'availability': availability,
      'previous_time_constraint': oldTime,
      if (extraRequirements != null) 'extra_requirements': extraRequirements,
    };
    final json = await _command(
      operation: 'patch_${draft.draftId}',
      intent: intent,
      buildBody: () => {
        'expected_summary_revision': draft.summaryRevision,
        'expected_planning_input_revision': draft.planningInputRevision,
        'changes': {'summary': changes},
      },
      send: (body, key) => _client.patchData(
          '/drafts/${Uri.encodeComponent(draft.draftId)}',
          idempotencyKey: key,
          data: body),
    );
    return PlanningDraftModel.fromJson(json);
  }

  Future<PlanningDraftModel> updateStepProposal({
    required PlanningDraftModel draft,
    required String? proposal,
  }) async {
    final json = await _client.patchData(
      '/drafts/${Uri.encodeComponent(draft.draftId)}',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'patch_proposal_${draft.draftId}',
        '${draft.planningInputRevision}_$proposal',
      ),
      data: {
        'expected_planning_input_revision': draft.planningInputRevision,
        'changes': {'user_step_proposal': proposal},
      },
    );
    return PlanningDraftModel.fromJson(json);
  }

  Future<JsonMap> confirmSummary(PlanningDraftModel draft) => _client.postData(
        '/drafts/${Uri.encodeComponent(draft.draftId)}/confirm-summary',
        idempotencyKey: ApiClient.stableIdempotencyKey(
          'confirm_summary_${draft.draftId}',
          '${draft.summaryRevision}',
        ),
        data: {'expected_summary_revision': draft.summaryRevision},
      );

  /// [calendarContextRef] (`context_id` + `revision`) cho Backend biết dùng
  /// đúng bản chụp lịch bận Google nào khi xếp phiên.
  Future<PlanCandidateModel> createCandidate(
    PlanningDraftModel draft, {
    Map<String, dynamic>? calendarContextRef,
  }) async {
    final body = <String, dynamic>{
      'source_summary_revision': draft.summaryRevision,
      'source_planning_input_revision': draft.planningInputRevision,
      'selected_step_keys': null,
      'planning_horizon': null,
      if (calendarContextRef != null) 'calendar_context_ref': calendarContextRef,
    };
    final json = await _command(
      operation: 'candidate_${draft.draftId}',
      intent: body,
      buildBody: () => body,
      send: (payload, key) => _client.postData(
          '/drafts/${Uri.encodeComponent(draft.draftId)}/candidates',
          idempotencyKey: key,
          data: payload),
    );
    return PlanCandidateModel.fromJson(json);
  }

  Future<PlanCandidateModel> getCandidate(String candidateId) async =>
      PlanCandidateModel.fromJson(await _client.getData(
        '/candidates/${Uri.encodeComponent(candidateId)}',
      ));

  Future<TaskModel> getTask(String taskId) async => TaskModel.fromJson(
        await _client.getData('/tasks/${Uri.encodeComponent(taskId)}'),
      );

  Future<PlanCandidateModel> updateCandidateStep({
    required PlanCandidateModel candidate,
    required String clientStepKey,
    required String title,
    required String expectedOutput,
    required int estimatedBaseSeconds,
  }) async {
    final json = await _client.patchData(
      '/candidates/${Uri.encodeComponent(candidate.candidateId)}',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'patch_candidate_${candidate.candidateId}',
        '${candidate.reviewRevision}_${clientStepKey}_${title}_${expectedOutput}_$estimatedBaseSeconds',
      ),
      data: {
        'expected_review_revision': candidate.reviewRevision,
        'edits': {
          'steps': [
            {
              'client_step_key': clientStepKey,
              'title': title,
              'expected_output': expectedOutput,
              'estimated_base_seconds': estimatedBaseSeconds,
            }
          ],
        },
      },
    );
    return PlanCandidateModel.fromJson(json);
  }

  Future<JsonMap> confirmCandidate({
    required PlanCandidateModel candidate,
    required PlanningDraftModel draft,
  }) =>
      _client.postData(
        '/candidates/${Uri.encodeComponent(candidate.candidateId)}/confirm',
        idempotencyKey: ApiClient.stableIdempotencyKey(
          'confirm_candidate_${candidate.candidateId}',
          '${candidate.reviewRevision}_${draft.summaryRevision}_${draft.planningInputRevision}',
        ),
        data: {
          'expected_review_revision': candidate.reviewRevision,
          'expected_summary_revision': draft.summaryRevision,
          'expected_planning_input_revision': draft.planningInputRevision,
          'expected_source_revisions': candidate.sourceRevisions,
        },
      );

  static String _vnIso(DateTime value) {
    final vn = value.toUtc().add(const Duration(hours: 7));
    String two(int n) => n.toString().padLeft(2, '0');
    return '${vn.year}-${two(vn.month)}-${two(vn.day)}T${two(vn.hour)}:${two(vn.minute)}:${two(vn.second)}+07:00';
  }
}
