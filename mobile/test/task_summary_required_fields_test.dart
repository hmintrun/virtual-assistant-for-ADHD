import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/models/planning_models.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/screens/task_summary_screen.dart';

class _SummaryTask extends TaskProvider {
  _SummaryTask({required this.testDraft, required this.mayPlan});

  final PlanningDraftModel testDraft;
  final bool mayPlan;

  @override
  String get title => 'Hoàn thành báo cáo';

  @override
  String get scope => 'Báo cáo tổng kết';

  @override
  DateTime? get deadline => DateTime(2027, 1, 15, 18);

  @override
  String get availabilitySummary => 'Chưa chọn khung giờ rảnh.';

  @override
  PlanningDraftModel? get draft => testDraft;

  @override
  bool get canPlan => mayPlan;

  @override
  bool get isApiBusy => false;
}

PlanningDraftModel _draft(
  List<JsonMap> issues, {
  JsonMap fields = const {},
}) =>
    PlanningDraftModel(
      draftId: 'draft-1',
      stage: 'summary',
      summary: {
        'title': 'Hoàn thành báo cáo',
        'scope': 'Báo cáo tổng kết',
        ...fields,
        'can_plan': false,
        'clarification_issues': issues,
      },
      summaryRevision: 1,
      planningInputRevision: 1,
      inputSource: 'text',
    );

Future<void> _openScreen(WidgetTester tester, _SummaryTask task) async {
  tester.view.physicalSize = const Size(900, 1800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(task.dispose);
  await tester.pumpWidget(ChangeNotifierProvider<TaskProvider>.value(
    value: task,
    child: const MaterialApp(home: TaskSummaryScreen()),
  ));
}

void main() {
  testWidgets('chỉ hiển thị câu hỏi của trường bắt buộc còn thiếu',
      (tester) async {
    final task = _SummaryTask(
      testDraft: _draft([
        {
          'code': 'DEADLINE_MISSING',
          'blocking': true,
          'question': 'Hạn chót của công việc này là khi nào?',
        },
        {
          'code': 'OPTIONAL_STYLE',
          'blocking': false,
          'question': 'Bạn muốn trình bày theo phong cách nào?',
        },
        {
          'code': 'AI_FALLBACK_NOTICE',
          'blocking': false,
          'message': 'Phân tích AI chưa khả dụng; vui lòng thử lại sau.',
        },
      ]),
      mayPlan: false,
    );
    await _openScreen(tester, task);

    expect(find.text('Cần bổ sung'), findsOneWidget);
    expect(find.text('Hạn chót của công việc này là khi nào?'), findsOneWidget);
    expect(find.text('Bạn muốn trình bày theo phong cách nào?'), findsNothing);
    expect(find.text('Phân tích AI chưa khả dụng; vui lòng thử lại sau.'),
        findsNothing);
    expect(find.text('Nhập thông tin còn thiếu'), findsOneWidget);
  });

  testWidgets('không hiện khung hỏi khi chỉ còn lưu ý không bắt buộc',
      (tester) async {
    final task = _SummaryTask(
      testDraft: _draft([
        {
          'code': 'OPTIONAL_STYLE',
          'blocking': false,
          'question': 'Bạn muốn trình bày theo phong cách nào?',
        },
      ]),
      mayPlan: false,
    );
    await _openScreen(tester, task);

    expect(find.text('Cần bổ sung'), findsNothing);
    expect(find.text('Bạn muốn trình bày theo phong cách nào?'), findsNothing);
    expect(find.text('Nhập thông tin còn thiếu'), findsNothing);
  });

  testWidgets(
      'dùng message cho blocker thiếu question và giữ thao tác lưu nháp',
      (tester) async {
    final task = _SummaryTask(
      testDraft: _draft([
        {
          'code': 'DEADLINE_MISSING',
          'blocking': true,
          'message': 'Cần hạn chót để lập kế hoạch.',
        },
      ]),
      mayPlan: false,
    );
    await _openScreen(tester, task);

    expect(find.text('Cần hạn chót để lập kế hoạch.'), findsOneWidget);
    expect(find.text('Lưu nháp'), findsOneWidget);
    expect(find.text('Bạn muốn trình bày theo phong cách nào?'), findsNothing);
  });

  testWidgets('không tự đặt câu hỏi khi backend không trả blocker',
      (tester) async {
    final task = _SummaryTask(
      testDraft: _draft([
        {
          'code': 'OPTIONAL_STYLE',
          'blocking': false,
          'question': 'Bạn muốn trình bày theo phong cách nào?',
        },
      ]),
      mayPlan: false,
    );
    await _openScreen(tester, task);

    expect(find.text('Chưa đủ điều kiện lập kế hoạch.'), findsOneWidget);
    expect(find.text('Thử lại'), findsOneWidget);
    expect(find.text('Cần bổ sung thông tin bắt buộc để lập kế hoạch.'),
        findsNothing);
  });

  testWidgets('nhận availability_ref của backend để cho phép đổi giờ rảnh',
      (tester) async {
    final task = _SummaryTask(
      testDraft: _draft(
        const [],
        fields: {
          'availability_ref': {
            'availability_id': 'availability-1',
            'revision': 2,
          },
        },
      ),
      mayPlan: false,
    );
    await _openScreen(tester, task);

    expect(find.text('Đổi khung giờ'), findsOneWidget);
    expect(find.text('Nghỉ 5 phút giữa phiên'), findsNothing);
  });
}
