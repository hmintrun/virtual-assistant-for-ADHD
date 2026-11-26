import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/screens/focus_review_screens.dart';
import 'package:proplan_va/services/adaptation_api.dart';

class _ResumeProposalApi extends AdaptationApi {
  _ResumeProposalApi() : super(ApiClient());

  int reads = 0;
  final actions = <String>[];

  @override
  Future<Map<String, dynamic>> proposal(String id) async => {
        'proposal_id': id,
        'status': 'pending',
        'stale': ++reads > 1,
        'feasibility': {'confirmable': true},
      };

  @override
  Future<Map<String, dynamic>> updateProposal(
      String action, Map<String, dynamic> proposal) async {
    actions.add(action);
    throw const ApiException('Cần kết nối để tính lại.');
  }
}

void main() {
  testWidgets('UF6 rechecks a pending proposal after app resume',
      (tester) async {
    final api = _ResumeProposalApi();
    await tester.pumpWidget(MaterialApp(
      home: ScheduleComparisonScreen(
        reason: 'Chờ duyệt',
        proposalId: 'proposal-pending',
        api: api,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Áp dụng lịch mới'), findsOneWidget);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();

    expect(api.reads, 2);
    expect(api.actions, ['rebuild']);
    expect(find.text('Áp dụng lịch mới'), findsNothing);
    expect(find.text('Thử lại'), findsOneWidget);
  });

  for (final dark in [false, true]) {
    testWidgets(
        'UF6 previews the selected reason without applying a schedule (dark=$dark)',
        (tester) async {
      tester.view.physicalSize = const Size(412, 915);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      MelbelColors.isDark = dark;
      addTearDown(() => MelbelColors.isDark = false);

      await tester.pumpWidget(MaterialApp(
        theme: dark ? ThemeData.dark() : ThemeData.light(),
        home: const ScheduleChangeReasonScreen(),
      ));
      await tester.tap(find.text('Bận việc khác'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Xem thay đổi'));
      await tester.pumpAndSettle();

      expect(find.text('LỊCH ĐỀ XUẤT'), findsOneWidget);
      expect(find.text('Chưa có phương án mới. Lịch hiện tại vẫn được giữ.'),
          findsOneWidget);
      expect(find.text('Dùng lịch này'), findsNothing);
      await tester.tap(find.text('Giữ lịch hiện tại'));
      await tester.pumpAndSettle();
      expect(find.text('Vì sao cần đổi lịch?'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
