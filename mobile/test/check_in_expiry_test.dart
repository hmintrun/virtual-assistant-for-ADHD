import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/services/focus_native_controller.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final endsAt = DateTime.utc(2026, 11, 23, 2, 25);

  setUp(() {
    SharedPreferences.setMockInitialValues({
      'focus_active_run': jsonEncode({
        'run_id': 'run-1',
        'ends_at': endsAt.toIso8601String(),
      }),
    });
  });

  test('còn trong 5 phút hỏi Check-in → chưa dọn (giữ DND)', () async {
    final released =
        await FocusNativeController.instance.releaseIfCheckInExpired(
      runId: 'run-1',
      now: endsAt.add(const Duration(minutes: 4, seconds: 59)).toLocal(),
    );
    expect(released, isFalse);
  });

  test('không phải phiên đang chạy → không đụng tới', () async {
    final released =
        await FocusNativeController.instance.releaseIfCheckInExpired(
      runId: 'run-khac',
      now: endsAt.add(const Duration(hours: 1)).toLocal(),
    );
    expect(released, isFalse);
  });

  test('bản ghi phiên cũ (không có step_title/phases) vẫn đọc được', () async {
    // Không ném lỗi khi parse bản ghi tuần 2–3.
    final released =
        await FocusNativeController.instance.releaseIfCheckInExpired(
      runId: 'run-1',
      now: endsAt.toLocal(),
    );
    expect(released, isFalse);
  });
}
