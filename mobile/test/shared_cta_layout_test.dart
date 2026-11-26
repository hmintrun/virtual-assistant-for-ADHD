import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/widgets/melbel_ui.dart';

void main() {
  for (final size in const [Size(320, 640), Size(390, 844), Size(844, 390)]) {
    for (final dark in [false, true]) {
      testWidgets('CTA supports large text at $size dark=$dark',
          (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        var taps = 0;
        await tester.pumpWidget(MaterialApp(
          theme: dark ? ThemeData.dark() : ThemeData.light(),
          home: MediaQuery(
            data: MediaQueryData(size: size, textScaler: TextScaler.linear(2)),
            child: Scaffold(
              body: SafeArea(
                child: ListView(
                  padding: const EdgeInsets.all(20),
                  children: [
                    PrimaryCta(
                      label: 'Bắt đầu phiên tập trung',
                      icon: Icons.play_arrow,
                      onPressed: () => taps++,
                    ),
                    const SizedBox(height: 16),
                    SecondaryCta(
                      label: 'Quay về trang chủ',
                      onPressed: () {},
                    ),
                  ],
                ),
              ),
            ),
          ),
        ));
        expect(tester.takeException(), isNull);
        expect(tester.getSize(find.byType(ElevatedButton)).height,
            greaterThanOrEqualTo(56));
        await tester.tap(find.byType(ElevatedButton));
        expect(taps, 1);
      });
    }
  }
}
