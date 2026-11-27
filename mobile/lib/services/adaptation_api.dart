import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/api_client.dart';

class AdaptationApi {
  AdaptationApi(this._api);
  final ApiClient _api;
  static const _storage = FlutterSecureStorage();

  Future<void> cacheProposal(Map<String, dynamic>? proposal) async {
    final owner = _api.ownerId;
    if (owner == null) return;
    final key = 'proplan.proposal.$owner';
    try {
      if (proposal == null || proposal['status'] != 'pending') {
        await _storage.delete(key: key);
      } else {
        await _storage.write(key: key, value: jsonEncode(proposal));
      }
    } catch (_) {}
  }

  Future<Map<String, dynamic>?> cachedProposal() async {
    final owner = _api.ownerId;
    if (owner == null) return null;
    try {
      final value = await _storage.read(key: 'proplan.proposal.$owner');
      if (value == null || owner != _api.ownerId) return null;
      return jsonDecode(value) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>?> currentProposal() =>
      _api.getOptionalData('/proposals/current');

  Future<Map<String, dynamic>> proposal(String id) =>
      _api.getData('/proposals/${Uri.encodeComponent(id)}');

  Future<Map<String, dynamic>> createProposal(String runId, String reason,
      {String? requestText}) async {
    try {
      return await _createProposal(runId, reason, requestText);
    } on ApiException catch (error) {
      if (error.statusCode != 409 || error.code != 'REVISION_CONFLICT') rethrow;
      return _createProposal(runId, reason, requestText);
    }
  }

  /// Creates a UF6 proposal directly from a confirmed task's `source_revisions`
  /// (e.g. `expand_next_horizon`, `missed_start`, or `early_completion_pull_forward`).
  Future<Map<String, dynamic>> createProposalForTask({
    required String taskId,
    required String reason,
    Map<String, int>? sourceRevisions,
    List<String>? allowedTaskIds,
    String? requestText,
  }) async {
    Future<Map<String, dynamic>> send(Map<String, dynamic> revisions) =>
        _command('/proposals', {
          'trigger_reason': reason,
          'target_task_id': taskId,
          'allowed_task_ids': allowedTaskIds ?? [taskId],
          'expected_base_schedule_revision': revisions['schedule'] ?? 0,
          'expected_source_revisions': revisions,
          if (requestText != null && requestText.isNotEmpty)
            'request_text': requestText,
        });

    Future<Map<String, dynamic>> loadRevisionsAndSend() async {
      final task = await _api.getData('/tasks/${Uri.encodeComponent(taskId)}');
      final revMap = task['source_revisions'] as Map?;
      if (revMap == null) {
        throw const ApiException('Công việc chưa có thông tin source_revisions.');
      }
      return send(revMap.cast<String, dynamic>());
    }

    try {
      if (sourceRevisions != null) {
        return await send(sourceRevisions);
      }
      return await loadRevisionsAndSend();
    } on ApiException catch (error) {
      if (error.statusCode != 409 || !error.isRevisionConflict) rethrow;
      return loadRevisionsAndSend();
    }
  }

  /// Rebuilds a stale or source-changed proposal (`POST /api/v1/proposals/{id}/rebuild`).
  Future<Map<String, dynamic>> rebuildProposal(
    Map<String, dynamic> proposal, {
    String? requestText,
  }) {
    final id = proposal['proposal_id'] as String;
    final body = {
      'expected_proposal_revision': proposal['proposal_revision'],
      if (requestText != null && requestText.trim().isNotEmpty)
        'request_text': requestText.trim(),
    };
    return _api.postData(
      '/proposals/${Uri.encodeComponent(id)}/rebuild',
      data: body,
      idempotencyKey: ApiClient.newIdempotencyKey('proposal_rebuild'),
    );
  }

  /// Returns true when any proposed schedule slot in [proposal] starts in the past.
  static bool hasExpiredProposedSlots(
    Map<String, dynamic> proposal, {
    DateTime? now,
  }) {
    final changes = proposal['proposed_changes'];
    if (changes is! Map) return false;
    final cutoff = (now ?? DateTime.now()).toUtc();
    for (final item in (changes['sessions_to_move'] as List? ?? const [])) {
      if (item is! Map) continue;
      final raw = (item['after'] as Map?)?['start'] ?? item['to_start'];
      if (raw is String) {
        final parsed = DateTime.tryParse(raw)?.toUtc();
        if (parsed != null && parsed.isBefore(cutoff)) return true;
      }
    }
    for (final item in (changes['sessions_to_add'] as List? ?? const [])) {
      if (item is! Map) continue;
      final raw = (item['after'] as Map?)?['start'];
      if (raw is String) {
        final parsed = DateTime.tryParse(raw)?.toUtc();
        if (parsed != null && parsed.isBefore(cutoff)) return true;
      }
    }
    return false;
  }

  Future<Map<String, dynamic>> _createProposal(
      String runId, String reason, String? requestText) async {
    final context = await _api.getData(
        '/runs/${Uri.encodeComponent(runId)}/upcoming-schedule-context');
    final taskId = context['task_id'] as String;
    final revisions = context['source_revisions'] as Map;
    return _command('/proposals', {
      'trigger_reason': reason,
      'target_task_id': taskId,
      'allowed_task_ids': [taskId],
      'expected_base_schedule_revision': revisions['schedule'],
      'expected_source_revisions': revisions,
      if (requestText != null && requestText.isNotEmpty)
        'request_text': requestText,
    });
  }

  Future<Map<String, dynamic>> updateProposal(
    String action,
    Map<String, dynamic> proposal,
  ) async {
    try {
      return await _updateProposal(action, proposal);
    } on ApiException catch (error) {
      // An approval is bound to the exact version the user reviewed. Let the
      // screen reload it and ask for confirmation instead of approving a new
      // schedule silently.
      if (action == 'approve' ||
          error.statusCode != 409 ||
          error.code != 'REVISION_CONFLICT') {
        rethrow;
      }
      final id = proposal['proposal_id'] as String;
      final latest = await this.proposal(id);
      if (latest['status'] != 'pending') rethrow;
      return _updateProposal(action, latest);
    }
  }

  Future<Map<String, dynamic>> _updateProposal(
    String action,
    Map<String, dynamic> proposal,
  ) {
    final path =
        '/proposals/${Uri.encodeComponent(proposal['proposal_id'] as String)}/$action';
    final body = {
      'expected_proposal_revision': proposal['proposal_revision'],
      if (action == 'approve') ...{
        'expected_base_schedule_revision': proposal['base_schedule_revision'],
        'expected_source_revisions': proposal['source_revisions'],
      },
    };
    if (action == 'rebuild') {
      return _api.postData(
        path,
        data: body,
        idempotencyKey: ApiClient.newIdempotencyKey('proposal_rebuild'),
      );
    }
    return _command(path, body);
  }

  Future<Map<String, dynamic>?> suggestion() =>
      _api.getOptionalData('/suggestions/current');

  Future<Map<String, dynamic>> respond(
    Map<String, dynamic> suggestion,
    int revision,
    String action, {
    Object? editedValue,
    String? annoyanceFeedback,
  }) {
    final id = Uri.encodeComponent(suggestion['suggestion_id'] as String);
    final feedback = annoyanceFeedback?.trim();
    return _command('/suggestions/$id/response', {
      'expected_suggestion_revision': suggestion['suggestion_revision'],
      'expected_preference_revision': revision,
      'action': action,
      if (editedValue != null) 'edited_value': editedValue,
      if (feedback != null && feedback.isNotEmpty)
        'annoyance_feedback': feedback,
    });
  }

  Future<Map<String, dynamic>> _command(
    String path,
    Map<String, dynamic> body,
  ) =>
      _api.postData(path,
          data: body,
          idempotencyKey:
              ApiClient.stableIdempotencyKey(path, body.toString()));
}
