import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/screens/focus_review_screens.dart';
import 'package:proplan_va/services/adaptation_api.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';

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

class _EmptyProposalApi extends AdaptationApi {
  _EmptyProposalApi() : super(ApiClient(bindSession: false));

  @override
  Future<Map<String, dynamic>?> currentProposal() async => null;
}

class _DeficitProposalApi extends AdaptationApi {
  _DeficitProposalApi() : super(ApiClient(bindSession: false));

  static const String longExplanation =
      'Phiên vừa qua chưa kịp bắt đầu đúng nhịp. Quỹ giờ rảnh hiện tại đang thiếu khoảng 19 phút so với hạn chót; bạn có thể mở thêm khung giờ rảnh hoặc điều chỉnh hạn chót cho thoải mái hơn nhé.';

  @override
  Future<Map<String, dynamic>> proposal(String id) async {
    final tomorrow = DateTime.now().add(const Duration(days: 1));
    String slot(int hour, int minute) => DateTime(
          tomorrow.year,
          tomorrow.month,
          tomorrow.day,
          hour,
          minute,
        ).toIso8601String();
    return {
      'proposal_id': id,
      'status': 'pending',
      'target_task_id': 'task_deficit',
      'explanation': longExplanation,
      'feasibility': {'confirmable': false, 'deficit_seconds': 1140},
      'proposed_changes': {
        'sessions_to_move': [
          {
            'before': {
              'start': slot(16, 0),
              'end': slot(16, 35),
            },
            'after': {
              'start': slot(19, 10),
              'end': slot(19, 45),
            },
          },
          {
            'before': {
              'start': slot(16, 50),
              'end': slot(18, 22),
            },
            'after': {
              'start': slot(20, 0),
              'end': slot(21, 32),
            },
          },
          {
            'before': {
              'start': slot(20, 24),
              'end': slot(21, 33),
            },
            'after': {
              'start': slot(21, 47),
              'end': slot(22, 56),
            },
          },
        ],
      },
    };
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
      expect(find.text('Chưa có đề xuất đổi lịch.'), findsOneWidget);
      expect(find.text('Dùng lịch này'), findsNothing);
      await tester.tap(find.text('Giữ lịch hiện tại'));
      await tester.pumpAndSettle();
      expect(find.text('Vì sao cần đổi lịch?'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'ScheduleComparisonScreen hides redundant pending reason subtext and AvailabilityAdjustmentScreen omits redundant subtext and back button',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: ScheduleComparisonScreen(
        reason: 'Đề xuất chờ duyệt',
        trigger: 'resume',
        api: _EmptyProposalApi(),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Xem lại lịch'), findsOneWidget);
    expect(find.text('Đề xuất chờ duyệt'), findsNothing);
    expect(find.text('Chưa có đề xuất đổi lịch.'), findsOneWidget);

    await tester.pumpWidget(MaterialApp(
      theme: MelbelTheme.currentTheme,
      home: const AvailabilityAdjustmentScreen(taskId: 'task-1'),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Thêm một khung giờ'), findsOneWidget);
    expect(find.text('Tạo phương án giờ rảnh'), findsOneWidget);
    expect(
      find.text('Chỉ áp dụng cho việc này · lịch cũ giữ nguyên đến khi duyệt.'),
      findsNothing,
    );
    expect(find.text('Quay lại'), findsNothing);
    expect(find.byTooltip('Quay lại'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'ScheduleComparisonScreen collapses long explanation, compacts moved times, and promotes Mở thêm giờ rảnh to sticky PrimaryCta',
      (tester) async {
    tester.view.physicalSize = const Size(360, 780);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: MelbelTheme.themeFor(isDark: true),
        home: ScheduleComparisonScreen(
          reason: 'Xem lại lịch',
          proposalId: 'prop-deficit',
          api: _DeficitProposalApi(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Thiếu 19 phút'), findsOneWidget);
    expect(find.text(_DeficitProposalApi.longExplanation), findsNothing);
    expect(
      find.widgetWithText(PrimaryCta, 'Mở thêm giờ rảnh'),
      findsOneWidget,
    );
    expect(find.text('Lùi hạn chót'), findsOneWidget);
    expect(find.text('Giữ lịch hiện tại'), findsOneWidget);
    expect(find.text('16:00–16:35'), findsOneWidget);
    expect(find.text('19:10–19:45'), findsOneWidget);
    expect(find.textContaining('Lịch cũ:'), findsNothing);

    await tester.tap(find.byTooltip('Xem giải thích'));
    await tester.pumpAndSettle();
    expect(find.text(_DeficitProposalApi.longExplanation), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
