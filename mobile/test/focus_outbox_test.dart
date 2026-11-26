import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/repositories/local_run_repository.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _Repository extends InMemoryRunRepository {
  final synced = Completer<void>();

  @override
  Future<void> markThoughtSynced(
      String thoughtId, String serverThoughtId) async {
    await super.markThoughtSynced(thoughtId, serverThoughtId);
    synced.complete();
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  for (final serverRunId in [null, 'backend-run-id']) {
    test('UF4 sends only the real run ID (serverRunId=$serverRunId)', () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      addTearDown(() => debugDefaultTargetPlatformOverride = null);
      SharedPreferences.setMockInitialValues({});
      final repository = _Repository();
      final created = DateTime.now().subtract(const Duration(minutes: 1));
      await repository.queueThought(ThoughtRecord(
        clientThoughtId: 'local-note',
        createdAt: created,
        undoDeadlineAt: created.add(const Duration(seconds: 5)),
        sourceRunId: 'legacy-session-id',
        serverRunId: serverRunId,
        textContent: 'Ghi chú thử',
      ));

      RequestOptions? sent;
      final dio = Dio()
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
          sent = request;
          handler.resolve(Response(
            requestOptions: request,
            statusCode: 201,
            data: {
              'data': {'thought_id': 'server-note'},
            },
          ));
        }));
      final provider =
          FocusProvider(api: ApiClient(dio: dio), repository: repository);
      addTearDown(provider.dispose);

      await provider.restore();
      await repository.synced.future.timeout(const Duration(seconds: 3));

      expect(sent?.path, '/thoughts');
      if (serverRunId == null) {
        expect(sent?.data, isNot(contains('source_run_id')));
      } else {
        expect(sent?.data, containsPair('source_run_id', serverRunId));
      }
      final saved = (await repository.loadThoughts()).single;
      expect(saved.isPendingSync, isFalse);
      expect(saved.serverThoughtId, 'server-note');
      expect(provider.thoughts.single.serverThoughtId, 'server-note');
    });
  }
}
