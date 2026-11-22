import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/models/planning_models.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/screens/candidate_review_screen.dart';

class _CandidateTask extends TaskProvider {
  _CandidateTask(this.candidate);

  @override
  final PlanCandidateModel candidate;
}

void main() {
  final cases = <String, JsonMap>{
    'valid': {'expected_output': 'PDF 3 trang, có biểu đồ'},
    'missing': {},
    'null': {'expected_output': null},
    'empty': {'expected_output': ''},
    'whitespace': {'expected_output': '   '},
  };

  for (final entry in cases.entries) {
    testWidgets('candidate output: ${entry.key}', (tester) async {
      final task = _CandidateTask(PlanCandidateModel.fromJson({
        'candidate_id': 'demo',
        'review_revision': 2,
        'stale': false,
        'feasibility': {'status': 'feasible'},
        'steps': [
          {
            'client_step_key': 'step-1',
            'title': 'Soạn báo cáo',
            'estimated_base_seconds': 1800,
            ...entry.value,
          },
        ],
        'sessions': [],
      }));
      addTearDown(task.dispose);
      await tester.pumpWidget(ChangeNotifierProvider<TaskProvider>.value(
        value: task,
        child: const MaterialApp(home: CandidateReviewScreen()),
      ));
      expect(find.text('Phiên bản 2'), findsOneWidget);
      final output = entry.value['expected_output'] as String?;
      expect(
        find.text(output == null || output.trim().isEmpty
            ? 'Chưa có kết quả đầu ra'
            : output),
        findsOneWidget,
      );
    });
  }
  testWidgets('chỉnh bước giữ dữ liệu khi thiếu kết quả', (tester) async {
    final task = _CandidateTask(PlanCandidateModel.fromJson({
      'candidate_id': 'edit-demo',
      'review_revision': 1,
      'feasibility': {'status': 'feasible'},
      'steps': [
        {
          'client_step_key': 'step-1',
          'title': 'Chuẩn bị báo cáo',
          'estimated_base_seconds': 1200,
        },
      ],
      'sessions': [],
    }));
    addTearDown(task.dispose);
    await tester.pumpWidget(ChangeNotifierProvider<TaskProvider>.value(
      value: task,
      child: const MaterialApp(home: CandidateReviewScreen()),
    ));
    await tester.ensureVisible(find.byTooltip('Chỉnh bước'));
    await tester.tap(find.byTooltip('Chỉnh bước'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextFormField, 'Tên bước'), 'Viết phần mở đầu');
    await tester.ensureVisible(find.text('Gửi thay đổi'));
    await tester.tap(find.text('Gửi thay đổi'));
    await tester.pumpAndSettle();
    expect(find.text('Nhập kết quả bạn muốn đạt.'), findsOneWidget);
    expect(find.text('Viết phần mở đầu'), findsOneWidget);
    expect(find.text('Chỉnh bước kế hoạch'), findsOneWidget);
  });
}
