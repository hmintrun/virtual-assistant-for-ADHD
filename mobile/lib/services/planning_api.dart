import '../core/api_client.dart';
import '../models/planning_models.dart';
import '../models/task_model.dart';

class PlanningApi {
  PlanningApi({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  Future<AvailabilityModel> getAvailability() async =>
      AvailabilityModel.fromJson(
        await _client.getData('/availability'),
      );

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
    required AvailabilityModel availability,
    String inputSource = 'text',
  }) async {
    final summary = <String, dynamic>{
      'title': title,
      'availability_ref': availability.reference,
      'importance': 3,
      if (scope.trim().isNotEmpty) 'scope': scope.trim(),
      if (deadline != null)
        'time_constraint': {
          'kind': 'deadline',
          'start_at': _vnIso(DateTime.now()),
          'deadline_at': _vnIso(deadline),
          'finish_by_at': null,
          'input_timezone': availability.timezone,
        },
    };
    final json =
        await _client.postData('/drafts', idempotencyKey: clientDraftId, data: {
      'client_draft_id': clientDraftId,
      'input_source': inputSource,
      'raw_input': rawInput,
      'summary': summary,
    });
    return PlanningDraftModel.fromJson(json);
  }

  Future<PlanningDraftModel> analyzeDraft(PlanningDraftModel draft) async {
    final json = await _client.postData(
      '/drafts/${Uri.encodeComponent(draft.draftId)}/analyze',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'analyze_${draft.draftId}',
        '${draft.summaryRevision}_${draft.planningInputRevision}_${draft.rawInput}',
      ),
      data: {
        'expected_summary_revision': draft.summaryRevision,
        'expected_planning_input_revision': draft.planningInputRevision,
        'raw_input': draft.rawInput ?? draft.title,
      },
    );
    return PlanningDraftModel.fromJson(json);
  }

  Future<PlanningDraftModel> updateSummary({
    required PlanningDraftModel draft,
    required String title,
    required String scope,
    required DateTime? deadline,
    required String timezone,
  }) async {
    final changes = <String, dynamic>{
      'summary': <String, dynamic>{
        'title': title,
        'scope': scope,
        if (deadline != null)
          'time_constraint': {
            'kind': 'deadline',
            'start_at': _vnIso(DateTime.now()),
            'deadline_at': _vnIso(deadline),
            'finish_by_at': null,
            'input_timezone': timezone,
          },
      },
    };
    final json = await _client.patchData(
      '/drafts/${Uri.encodeComponent(draft.draftId)}',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'patch_draft_${draft.draftId}',
        '${draft.summaryRevision}_${draft.planningInputRevision}_${title}_${scope}_${deadline?.toIso8601String()}',
      ),
      data: {
        'expected_summary_revision': draft.summaryRevision,
        'expected_planning_input_revision': draft.planningInputRevision,
        'changes': changes,
      },
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

  Future<PlanCandidateModel> createCandidate(PlanningDraftModel draft) async {
    final json = await _client.postData(
      '/drafts/${Uri.encodeComponent(draft.draftId)}/candidates',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'create_candidate_${draft.draftId}',
        '${draft.summaryRevision}_${draft.planningInputRevision}',
      ),
      data: {
        'source_summary_revision': draft.summaryRevision,
        'source_planning_input_revision': draft.planningInputRevision,
        'selected_part_keys': null,
        'planning_horizon': null,
      },
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
