import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/providers/theme_provider.dart';
import 'package:proplan_va/screens/calendar_screen.dart';
import 'package:proplan_va/screens/onboarding_screens.dart';

class _CalendarClient extends ApiClient {
  _CalendarClient(this.unavailable) : super(bindSession: false);

  final bool unavailable;
  final requestedMonths = <DateTime>[];

  @override
  Future<Map<String, dynamic>> getData(String path,
      {Map<String, dynamic>? queryParameters}) async {
    if (path == '/schedule') {
      requestedMonths
          .add(DateTime.parse(queryParameters!['from'] as String).toLocal());
      if (unavailable) throw const ApiException('Offline');
    }
    return {'items': <Map<String, dynamic>>[]};
  }
}

void main() {
  for (final unavailable in [false, true]) {
    testWidgets('return to today opens its agenda (unavailable=$unavailable)',
        (tester) async {
      final client = _CalendarClient(unavailable);
      final today = DateUtils.dateOnly(DateTime.now());
      await tester
          .pumpWidget(MaterialApp(home: CalendarScreen(client: client)));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Tháng sau'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Xem hôm nay'));
      await tester.pumpAndSettle();
      expect(client.requestedMonths, [
        DateTime(today.year, today.month),
        DateTime(today.year, today.month + 1),
        DateTime(today.year, today.month),
      ]);
      expect(find.text('Thêm việc ngày này'), findsOneWidget);
      expect(find.text('Ngày này chưa có phiên đã chốt.'),
          unavailable ? findsNothing : findsOneWidget);
      expect(find.text('Chưa tải được các phiên của ngày này.'),
          unavailable ? findsOneWidget : findsNothing);
    });
  }

  testWidgets('setup keeps choices when moving between stages', (tester) async {
    await tester.pumpWidget(ChangeNotifierProvider(
      create: (_) => ThemeProvider(),
      child: const MaterialApp(home: SetupFlowScreen()),
    ));
    await tester.tap(find.text('Chọn nhịp làm việc'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('40 phút'));
    await tester.tap(find.text('Chọn cách nhắc'));
    await tester.pumpAndSettle();
    expect(find.text('Thiết lập · Bước 3/4'), findsOneWidget);
    expect(find.text('Khởi động 2 phút'), findsNothing);
    await tester.tap(find.text('Tùy chọn bổ sung'));
    await tester.pumpAndSettle();
    expect(find.text('Khởi động 2 phút'), findsOneWidget);
    await tester.tap(find.byTooltip('Quay lại'));
    await tester.pumpAndSettle();
    expect(find.text('40 phút tập trung · 5 phút nghỉ'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
