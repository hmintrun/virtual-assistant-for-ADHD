import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/services/planning_api.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
    'UF1 saves and restores draft availability without global settings',
    () async {
      FlutterSecureStorage.setMockInitialValues({});
      final paths = <String>[];
      final summary = <String, dynamic>{};
      var rawInput = "";
      final dio = Dio(BaseOptions(baseUrl: 'http://backend.test'));
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (request, handler) {
            paths.add('${request.method} ${request.path}');
            final payload = request.data as Map<String, dynamic>?;
            if (request.path == '/drafts') {
              rawInput = payload!['raw_input'] as String;
              final input = payload['summary'] as Map<String, dynamic>;
              summary.clear();
              if (rawInput == 'Chuẩn bị buổi họp') {
                expect(input.containsKey('scope'), isFalse);
                expect(input.containsKey('time_constraint'), isFalse);
                expect(input.containsKey('availability'), isFalse);
              }
              expect(input.containsKey('availability_ref'), isFalse);
              expect(input.containsKey('availability'), isFalse);
              summary.addAll(input);
            }
            if (request.method == 'PATCH') {
              final changes =
                  payload!['changes']['summary'] as Map<String, dynamic>;
              expect(changes.containsKey('availability_ref'), isFalse);
              summary.addAll(changes);
            }
            handler.resolve(
              Response(
                requestOptions: request,
                data: <String, dynamic>{
                  'data': <String, dynamic>{
                    'draft_id': 'draft-uf1',
                    'raw_input': rawInput,
                    'stage': 'summary_ready',
                    'summary_revision': 1,
                    'planning_input_revision': 1,
                    'summary': Map<String, dynamic>.of(summary),
                  },
                },
              ),
            );
          },
        ),
      );
      final api = PlanningApi(
        client: ApiClient(dio: dio),
        now: () => DateTime.utc(2026, 11, 7, 3),
      );
      final task = TaskProvider(planningApi: api);
      addTearDown(task.dispose);
      task.setTitle('Viết báo cáo');
      task.setScope('Tóm tắt kết quả');
      task.setDeadline(DateTime.utc(2026, 11, 8, 15));
      await task.prepareDraftAndAnalyze();

      task.setAvailability(
        AvailabilitySlot(
          start: DateTime.utc(2026, 11, 7, 11),
          end: DateTime.utc(2026, 11, 7, 12, 30),
        ),
      );
      await task.updateDraftOnBackend();
      expect(summary['availability'], {
        'timezone': 'Asia/Ho_Chi_Minh',
        'date_overrides': [
          {
            'date': '2026-11-07',
            'intervals': [
              {'start_local': '18:00', 'end_local': '19:30'},
            ],
          },
        ],
      });

      final reopened = TaskProvider(planningApi: api);
      addTearDown(reopened.dispose);
      await reopened.restoreCurrentDraft();
      expect(reopened.draft?.draftId, 'draft-uf1');
      expect(reopened.availabilitySummary, contains('18:00–19:30'));
      expect(paths, [
        'POST /drafts',
        'POST /drafts/draft-uf1/analyze',
        'PATCH /drafts/draft-uf1',
        'GET /drafts/draft-uf1',
      ]);

      expect(reopened.scope, 'Tóm tắt kết quả');
      reopened.setTitle('Chuẩn bị buổi họp');
      expect(reopened.scope, isEmpty);
      expect(reopened.deadline, isNull);
      await reopened.prepareDraftAndAnalyze();
      expect(reopened.availabilitySummary, contains('Chưa chọn khung giờ'));
      expect(paths.skip(4), ['POST /drafts', 'POST /drafts/draft-uf1/analyze']);
    },
  );
}
