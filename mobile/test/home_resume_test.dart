import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/models/planning_models.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/screens/home_screen.dart';
import 'package:proplan_va/screens/task_summary_screen.dart';

class _HomeApi extends ApiClient {
  _HomeApi({this.sessions = const []});
  final List<Map<String, dynamic>> sessions;
  @override
  Future<Map<String, dynamic>> getData(String path,
          {Map<String, dynamic>? queryParameters}) async =>
      {'items': path == '/schedule' ? sessions : []};

  @override
  Future<Map<String, dynamic>?> getOptionalData(String path) async => {};
}

class _ActiveFocus extends FocusProvider {
  _ActiveFocus(this.session);
  @override
  final FocusSessionInfo session;
  @override
  bool get isActiveRun => true;
  @override
  FocusStage get stage => FocusStage.active;
}

class _ReviewDraft extends TaskProvider {
  @override
  String get title => 'Soạn dàn ý';
  @override
  bool get hasUnconfirmedDraft => true;
  @override
  PlanningDraftModel get draft => const PlanningDraftModel(
      draftId: 'draft',
      stage: 'summary_review',
      summary: {},
      summaryRevision: 1,
      planningInputRevision: 1,
      inputSource: 'text');
}

Future<void> _openHome(WidgetTester tester, TaskProvider task,
    FocusProvider focus, _HomeApi api) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(task.dispose);
  addTearDown(focus.dispose);
  await tester.pumpWidget(MultiProvider(providers: [
    ChangeNotifierProvider<TaskProvider>.value(value: task),
    ChangeNotifierProvider<FocusProvider>.value(value: focus),
  ], child: MaterialApp(home: HomeScreen(api: api))));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'đang làm thì mở lại đúng bước trước các gợi ý và phiên tiếp theo',
      (tester) async {
    final start = DateTime.now().add(const Duration(days: 1));
    final next = {
      'session_id': 'next',
      'status': 'scheduled',
      'step_title': 'Phiên ngày mai',
      'scheduled_start': start.toIso8601String(),
      'scheduled_end': start.add(const Duration(minutes: 25)).toIso8601String()
    };
    final current = FocusSessionInfo.fromJson(
        {...next, 'session_id': 'current', 'step_title': 'Bước đang làm'});
    await _openHome(tester, TaskProvider(), _ActiveFocus(current),
        _HomeApi(sessions: [next]));
    expect(find.text('Bước đang làm').hitTestable(), findsOneWidget);
    expect(find.text('Quay lại phiên').hitTestable(), findsOneWidget);
    expect(find.text('Phiên ngày mai'), findsNothing);
    await tester.tap(find.text('Xem lịch tiếp theo'));
    await tester.pumpAndSettle();
    expect(find.text('Phiên ngày mai'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('mở lại bản nháp đã phân tích vào tóm tắt khi lịch trống',
      (tester) async {
    await _openHome(tester, _ReviewDraft(), FocusProvider(), _HomeApi());
    expect(find.text('Làm tiếp bản nháp'), findsOneWidget);
    expect(find.text('Bạn chưa có phiên sắp tới'), findsNothing);
    await tester.tap(find.text('Làm tiếp bản nháp'));
    await tester.pumpAndSettle();
    expect(find.byType(TaskSummaryScreen), findsOneWidget);
    expect(find.textContaining('Soạn dàn ý'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
