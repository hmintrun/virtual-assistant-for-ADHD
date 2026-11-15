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
  ) =>
      _command(
          '/proposals/${Uri.encodeComponent(proposal['proposal_id'] as String)}/$action',
          {
            'expected_proposal_revision': proposal['proposal_revision'],
            if (action == 'approve') ...{
              'expected_base_schedule_revision':
                  proposal['base_schedule_revision'],
              'expected_source_revisions': proposal['source_revisions'],
            },
          });

  Future<Map<String, dynamic>?> suggestion() =>
      _api.getOptionalData('/suggestions/current');

  Future<void> respond(
      Map<String, dynamic> suggestion, int revision, String action,
      {Object? editedValue}) async {
    final id = Uri.encodeComponent(suggestion['suggestion_id'] as String);
    await _command('/suggestions/$id/response', {
      'expected_suggestion_revision': suggestion['suggestion_revision'],
      'expected_preference_revision': revision,
      'action': action,
      if (editedValue != null) 'edited_value': editedValue,
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
