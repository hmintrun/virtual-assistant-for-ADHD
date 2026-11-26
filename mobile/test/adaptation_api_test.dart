import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/services/adaptation_api.dart';

void main() {
  test('proposal uses server scope and approval revisions', () async {
    final requests = <RequestOptions>[];
    const revisions = {
      'task': 4,
      'schedule': 5,
      'availability': 6,
      'settings': 7,
      'calendar': 8
    };
    final proposal = {
      'proposal_id': 'p',
      'proposal_revision': 2,
      'base_schedule_revision': 5,
      'source_revisions': revisions,
    };
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        final data = switch (request.path) {
          '/runs/r/upcoming-schedule-context' => {
              'task_id': 't',
              'source_revisions': revisions
            },
          '/proposals/current' || '/suggestions/current' => null,
          _ => proposal,
        };
        handler
            .resolve(Response(requestOptions: request, data: {'data': data}));
      }));
    final api = AdaptationApi(ApiClient(dio: dio, bindSession: false));
    expect(await api.currentProposal(), isNull);
    expect(await api.suggestion(), isNull);
    final reviewed = await api.createProposal('r', 'checkin_incomplete');
    expect(requests.last.data, {
      'trigger_reason': 'checkin_incomplete',
      'target_task_id': 't',
      'allowed_task_ids': ['t'],
      'expected_base_schedule_revision': 5,
      'expected_source_revisions': revisions,
    });
    await api.updateProposal('approve', reviewed);
    expect(requests.last.path, '/proposals/p/approve');
    expect(requests.last.data, {
      'expected_proposal_revision': 2,
      'expected_base_schedule_revision': 5,
      'expected_source_revisions': revisions,
    });
  });

  test('respond returns result map and sends annoyance_feedback only when non-empty',
      () async {
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        handler.resolve(Response(
          requestOptions: request,
          data: {
            'data': {
              'suggestion_id': 's1',
              'suggestion_revision': 2,
              'kind': 'buffer_multiplier',
              'action': request.data['action'],
              'status': request.data['action'] == 'reject'
                  ? 'rejected'
                  : 'accepted',
              'application_state': request.data['action'] == 'reject'
                  ? 'not_applied'
                  : 'pending_review',
              'effective_revision': 3,
              'pending_proposal_id':
                  request.data['action'] == 'reject' ? null : 'proposal-a20',
            },
          },
        ));
      }));
    final api = AdaptationApi(ApiClient(dio: dio, bindSession: false));
    final suggestion = {
      'suggestion_id': 's1',
      'suggestion_revision': 1,
    };

    final acceptResult = await api.respond(suggestion, 3, 'accept');
    expect(acceptResult['pending_proposal_id'], 'proposal-a20');
    expect(requests.last.path, '/suggestions/s1/response');
    expect(requests.last.data, {
      'expected_suggestion_revision': 1,
      'expected_preference_revision': 3,
      'action': 'accept',
    });

    await api.respond(suggestion, 3, 'reject', annoyanceFeedback: '   ');
    expect(
      (requests.last.data as Map).containsKey('annoyance_feedback'),
      isFalse,
    );

    final rejectResult = await api.respond(
      suggestion,
      3,
      'reject',
      annoyanceFeedback: 'too_frequent',
    );
    expect(rejectResult['status'], 'rejected');
    expect(requests.last.data, {
      'expected_suggestion_revision': 1,
      'expected_preference_revision': 3,
      'action': 'reject',
      'annoyance_feedback': 'too_frequent',
    });
  });
}
