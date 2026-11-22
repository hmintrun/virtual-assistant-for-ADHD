import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/screens/task_entry_screen.dart';

void main() {
  testWidgets('mở ô nhập sau ba lần bật mic không thành công', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    const channel = MethodChannel('plugin.csdcorp.com/speech_to_text');
    final messenger = tester.binding.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(channel, (call) async => false);
    FlutterSecureStorage.setMockInitialValues({});
    final task = TaskProvider();
    try {
      await tester.pumpWidget(ChangeNotifierProvider<TaskProvider>.value(
        value: task,
        child: const MaterialApp(home: TaskEntryScreen()),
      ));
      for (var attempt = 1; attempt <= 3; attempt++) {
        expect(find.byType(TextField), findsNothing);
        await tester.tap(find.byTooltip('Bắt đầu nói'));
        await tester.pumpAndSettle();
        if (attempt < 3) {
          expect(find.textContaining('($attempt/3)'), findsOneWidget);
        }
      }
      expect(find.byType(TextField), findsOneWidget);
      await tester.enterText(find.byType(TextField), 'Soạn báo cáo');
      expect(task.inputText, 'Soạn báo cáo');
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      messenger.setMockMethodCallHandler(channel, null);
      debugDefaultTargetPlatformOverride = null;
      task.dispose();
    }
  });
}
