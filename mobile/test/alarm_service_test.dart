import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/services/alarm_service.dart';

void main() {
  group('AlarmService.alignToSecond', () {
    test('giữ nguyên mốc đã chẵn giây', () {
      final t = DateTime(2026, 11, 7, 14, 58, 0);
      expect(AlarmService.alignToSecond(t), t);
    });

    test('làm tròn LÊN khi có mili-giây (không bao giờ kêu sớm)', () {
      final t = DateTime(2026, 11, 7, 14, 58, 0, 900);
      expect(
        AlarmService.alignToSecond(t),
        DateTime(2026, 11, 7, 14, 58, 1),
      );
    });

    test('làm tròn LÊN cả khi chỉ lệch 1 micro-giây', () {
      final t = DateTime(2026, 11, 7, 14, 58, 59, 0, 1);
      expect(
        AlarmService.alignToSecond(t),
        DateTime(2026, 11, 7, 14, 59, 0),
      );
    });

    test('kết quả luôn >= mốc gốc và lệch < 1 giây', () {
      final t = DateTime(2026, 11, 7, 23, 59, 59, 999, 999);
      final aligned = AlarmService.alignToSecond(t);
      expect(aligned.isBefore(t), isFalse);
      expect(aligned.difference(t), lessThan(const Duration(seconds: 1)));
      expect(aligned, DateTime(2026, 11, 8));
    });
  });
}
