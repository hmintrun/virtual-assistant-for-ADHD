import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/providers/theme_provider.dart';
import 'package:proplan_va/screens/settings_screen.dart';
import 'package:proplan_va/services/adaptation_api.dart';

class _FakeSettingsBackend {
  _FakeSettingsBackend({this.returnPendingProposal = true});

  final bool returnPendingProposal;
  final List<RequestOptions> requests = [];
  Map<String, dynamic>? suggestion = {
    'suggestion_id': 's-a20-1',
    'suggestion_revision': 2,
    'kind': 'buffer_multiplier',
    'current_value': 1.2,
    'suggested_value': 1.6,
    'reason': '10 phiên gần đây cần thêm thời gian đệm.',
    'status': 'pending',
  };

  ApiClient buildClient() {
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        Object? data;
        if (request.path == '/preferences') {
          data = {
            'revision': 3,
            'effective_revision': 3,
            'work_interval_seconds': 1500,
            'rest_interval_seconds': 300,
            'reminder_offsets': [900, 600, 300],
            'buffer_multiplier': 1.2,
            'reminder_style': 'gentle',
            'pending_proposal_id':
                returnPendingProposal && suggestion == null ? 'proposal-a20' : null,
          };
        } else if (request.path == '/suggestions/current') {
          data = suggestion;
        } else if (request.path == '/suggestions/s-a20-1/response') {
          final body = request.data as Map<String, dynamic>;
          final action = body['action'] as String;
          suggestion = null;
          data = {
            'suggestion_id': 's-a20-1',
            'suggestion_revision': 3,
            'kind': 'buffer_multiplier',
            'action': action,
            'status': action == 'reject' ? 'rejected' : 'accepted',
            'accepted_value': action == 'reject' ? null : 1.6,
            'application_state':
                action != 'reject' && returnPendingProposal
                    ? 'pending_review'
                    : (action == 'reject' ? 'not_applied' : 'applied_next_session'),
            'effective_revision': 3,
            'requested_revision':
                action != 'reject' && returnPendingProposal ? 4 : null,
            'pending_proposal_id':
                action != 'reject' && returnPendingProposal ? 'proposal-a20' : null,
          };
        } else if (request.path == '/proposals/proposal-a20') {
          data = {
            'proposal_id': 'proposal-a20',
            'proposal_revision': 1,
            'status': 'pending',
            'stale': false,
            'explanation': 'Điều chỉnh phiên tương lai theo hệ số đệm mới.',
            'feasibility': {'confirmable': true},
            'proposed_changes': {
              'sessions_to_move': const [],
              'sessions_to_add': const [],
              'sessions_to_cancel': const [],
            },
          };
        }
        handler.resolve(Response(
          requestOptions: request,
          data: {'data': data},
        ));
      }));
    return ApiClient(dio: dio, bindSession: false);
  }
}

Widget _wrapSettings(_FakeSettingsBackend backend) {
  final client = backend.buildClient();
  return ChangeNotifierProvider<ThemeProvider>(
    create: (_) => ThemeProvider(),
    child: MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: SettingsScreen(
        api: client,
        adaptation: AdaptationApi(client),
      ),
    ),
  );
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets(
      'accepting A20 suggestion with pending_proposal_id opens ScheduleComparisonScreen',
      (tester) async {
    final backend = _FakeSettingsBackend(returnPendingProposal: true);
    await tester.pumpWidget(_wrapSettings(backend));
    await tester.pumpAndSettle();

    expect(find.text('Gợi ý cho phiên sau'), findsOneWidget);
    await tester.tap(find.text('Áp dụng'));
    await tester.pumpAndSettle();

    expect(
      backend.requests.any((r) =>
          r.path == '/suggestions/s-a20-1/response' &&
          (r.data as Map)['action'] == 'accept'),
      isTrue,
    );
    expect(
      backend.requests.any((r) => r.path == '/proposals/proposal-a20'),
      isTrue,
    );
    expect(find.text('Điều chỉnh lịch'), findsOneWidget);
    expect(find.text('Áp dụng lịch mới'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('dismissing suggestion once sends reject without annoyance_feedback',
      (tester) async {
    final backend = _FakeSettingsBackend(returnPendingProposal: false);
    await tester.pumpWidget(_wrapSettings(backend));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Bỏ qua'));
    await tester.pumpAndSettle();
    expect(find.text('Bỏ qua gợi ý này?'), findsOneWidget);

    await tester.tap(find.text('Chỉ bỏ qua lần này'));
    await tester.pumpAndSettle();

    final responseReq = backend.requests
        .lastWhere((r) => r.path == '/suggestions/s-a20-1/response');
    final body = responseReq.data as Map<String, dynamic>;
    expect(body['action'], 'reject');
    expect(body.containsKey('annoyance_feedback'), isFalse);
    expect(find.text('Gợi ý cho phiên sau'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('dismissing with annoyance reason sends annoyance_feedback string',
      (tester) async {
    final backend = _FakeSettingsBackend(returnPendingProposal: false);
    await tester.pumpWidget(_wrapSettings(backend));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Bỏ qua'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Gợi ý quá thường xuyên'));
    await tester.pumpAndSettle();

    final responseReq = backend.requests
        .lastWhere((r) => r.path == '/suggestions/s-a20-1/response');
    final body = responseReq.data as Map<String, dynamic>;
    expect(body['action'], 'reject');
    expect(body['annoyance_feedback'], 'too_frequent');
    expect(find.text('Gợi ý cho phiên sau'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
