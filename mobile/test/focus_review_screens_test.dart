import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/theme.dart';
import 'package:proplan_va/screens/focus_review_screens.dart';

void main() {
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
