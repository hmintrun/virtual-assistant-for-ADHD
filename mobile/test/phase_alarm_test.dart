import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/services/alarm_service.dart';
import 'package:proplan_va/services/focus_native_controller.dart';

const _work25 = FocusPhase(kind: 'work', durationSeconds: 25 * 60);
const _rest5 = FocusPhase(kind: 'rest', durationSeconds: 5 * 60);

void main() {
  final start = DateTime(2026, 11, 23, 9, 0);

  group('AlarmService.planPhaseAlarms', () {
    test('1 phiên làm duy nhất: không có chuông pha (chỉ chuông hết phiên)',
        () {
      final plan = AlarmService.planPhaseAlarms(
        endsAt: start.add(const Duration(minutes: 25)),
        phases: const [_work25],
        now: start,
      );
      expect(plan, isEmpty);
    });

    test('Work 25 → Rest 5 → Work 25: 2 chuông, đúng mốc và đúng pha kế', () {
      final endsAt = start.add(const Duration(minutes: 55));
      final plan = AlarmService.planPhaseAlarms(
        endsAt: endsAt,
        phases: const [_work25, _rest5, _work25],
        now: start,
      );
      expect(plan.map((a) => a.fireAt), [
        DateTime(2026, 11, 23, 9, 25),
        DateTime(2026, 11, 23, 9, 30),
      ]);
      expect(plan.map((a) => a.next.kind), ['rest', 'work']);
      expect(plan.map((a) => a.notificationId),
          [AlarmService.phaseIdBase, AlarmService.phaseIdBase + 1]);
    });

    test('mốc tính lùi từ endsAt (khớp hạn đoạn của FocusProvider)', () {
      // endsAt đã làm tròn lên giây → mọi ranh giới lệch cùng một khoảng.
      final endsAt = DateTime(2026, 11, 23, 9, 30, 1);
      final plan = AlarmService.planPhaseAlarms(
        endsAt: endsAt,
        phases: const [_work25, _rest5],
        now: start,
      );
      expect(plan.single.fireAt, DateTime(2026, 11, 23, 9, 25, 1));
    });

    test('bỏ ranh giới đã qua (mở lại app giữa lượt)', () {
      final endsAt = start.add(const Duration(minutes: 55));
      final plan = AlarmService.planPhaseAlarms(
        endsAt: endsAt,
        phases: const [_work25, _rest5, _work25],
        now: DateTime(2026, 11, 23, 9, 27),
      );
      expect(plan.single.next.kind, 'work');
      expect(plan.single.fireAt, DateTime(2026, 11, 23, 9, 30));
    });

    test('2 đoạn liền nhau cùng loại hoặc đoạn 0 giây: không kêu', () {
      final plan = AlarmService.planPhaseAlarms(
        endsAt: start.add(const Duration(minutes: 50)),
        phases: const [
          _work25,
          FocusPhase(kind: 'rest', durationSeconds: 0),
          _work25,
        ],
        now: start,
      );
      expect(plan, isEmpty);
    });
  });

  group('AlarmService.phaseText', () {
    test('vào nghỉ: nói rõ số phút nghỉ', () {
      final alarm = PhaseAlarm(notificationId: 1, fireAt: start, next: _rest5);
      final text = AlarmService.phaseText(alarm, stepTitle: 'Viết dàn ý');
      expect(text.title, 'Hết giờ làm · nghỉ 5 phút');
    });

    test('vào làm: nói rõ số phút và tên bước', () {
      final alarm = PhaseAlarm(notificationId: 1, fireAt: start, next: _work25);
      final text = AlarmService.phaseText(alarm, stepTitle: 'Viết dàn ý');
      expect(text.title, 'Hết giờ nghỉ · vào làm 25 phút');
      expect(text.body, 'Viết dàn ý');
    });
  });

  group('FocusNativeController.phasesMatchDuration', () {
    test('tổng đoạn bằng thời lượng lượt → hợp lệ', () {
      expect(
        FocusNativeController.phasesMatchDuration(
          const [_work25, _rest5, _work25],
          const Duration(minutes: 55),
        ),
        isTrue,
      );
    });

    test('lệch tổng hoặc rỗng → bỏ chuông pha', () {
      expect(
        FocusNativeController.phasesMatchDuration(
          const [_work25, _rest5],
          const Duration(minutes: 25),
        ),
        isFalse,
      );
      expect(
        FocusNativeController.phasesMatchDuration(
          const [],
          const Duration(minutes: 25),
        ),
        isFalse,
      );
    });
  });

  test('FocusPhase JSON đi-về giữ nguyên (khôi phục sau khi app bị kill)', () {
    final back = FocusPhase.fromJson(_rest5.toJson());
    expect(back.kind, 'rest');
    expect(back.durationSeconds, 300);
  });
}
