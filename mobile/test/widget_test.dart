import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/models/planning_models.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/screens/candidate_review_screen.dart';

class _ReviewTask extends TaskProvider {
  _ReviewTask(this.candidate);

  @override
  final PlanCandidateModel candidate;
}

void main() {
  for (final partial in [false, true]) {
    testWidgets(
        'UF2 review labels the scope of confirmation (partial=$partial)',
        (tester) async {
      tester.view.physicalSize = const Size(800, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final start = DateTime.now().add(const Duration(days: 1));
      final task = _ReviewTask(PlanCandidateModel.fromJson({
        'candidate_id': 'c01-candidate',
        'review_revision': 1,
        'stale': false,
        'decomposition_state': partial ? 'partial' : 'complete',
        'unexpanded_scope': partial ? 'Viết báo cáo cuối kỳ' : null,
        'feasibility': {
          'status': 'feasible',
          'fully_scheduled': true,
          'deficit_seconds': 0,
        },
        'steps': [
          {'client_step_key': 'step-1', 'title': 'Thu thập dữ liệu'},
          if (partial) {'client_step_key': 'step-2', 'title': 'Phân tích'},
        ],
        'sessions': [
          {
            'client_step_key': 'step-1',
            'scheduled_start': start.toIso8601String(),
            'scheduled_end':
                start.add(const Duration(minutes: 25)).toIso8601String(),
          },
        ],
      }));
      addTearDown(task.dispose);
      await tester.pumpWidget(ChangeNotifierProvider<TaskProvider>.value(
        value: task,
        child: const MaterialApp(home: CandidateReviewScreen()),
      ));

      expect(find.textContaining('Thiếu tối thiểu 0 phút'), findsNothing);
      if (partial) {
        expect(find.text('Chưa chia thành bước: Viết báo cáo cuối kỳ'),
            findsOneWidget);
        expect(find.text('Bước chưa xếp đủ: Phân tích'), findsOneWidget);
        expect(find.text('Chốt phần đã xếp lịch'), findsOneWidget);
        expect(find.text('Chốt kế hoạch'), findsNothing);
      } else {
        expect(find.text('Phần chưa có lịch'), findsNothing);
        expect(find.text('Chốt kế hoạch'), findsOneWidget);
      }
    });
  }
}
