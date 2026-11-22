import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/services/alarm_service.dart';

ReminderSession _session(
  String id,
  DateTime start, {
  int? minutes,
  String? micro,
}) =>
    ReminderSession(
      sessionId: id,
      scheduledStart: start,
      stepTitle: 'Viết dàn ý báo cáo',
      plannedMinutes: minutes,
      microTaskText: micro,
    );

void main() {
  final now = DateTime(2026, 11, 23, 9, 0);

  group('AlarmService.normalizeOffsetsSeconds (luật Backend)', () {
    test('rỗng hoặc null → mặc định [900, 600, 300] giây', () {
      expect(AlarmService.normalizeOffsetsSeconds(null), [900, 600, 300]);
      expect(AlarmService.normalizeOffsetsSeconds(const []), [900, 600, 300]);
    });

    test('bỏ số âm/trùng, xếp giảm dần, giữ mốc 0 giây, tối đa 3 mốc', () {
      expect(
        AlarmService.normalizeOffsetsSeconds(
            const [300, -60, 900, 300, 0, 600]),
        [900, 600, 300],
      );
      expect(AlarmService.normalizeOffsetsSeconds(const [0]), [0]);
    });
  });

  group('AlarmService.planPreSessionReminders', () {
    test(
        'truyền nguyên reminder_offsets của Backend [900,600,300] → 15/10/5 phút',
        () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [_session('s1', DateTime(2026, 11, 23, 10, 0))],
        offsetsSeconds: const [900, 600, 300],
        now: now,
      );
      expect(plan.map((r) => r.fireAt), [
        DateTime(2026, 11, 23, 9, 45),
        DateTime(2026, 11, 23, 9, 50),
        DateTime(2026, 11, 23, 9, 55),
      ]);
    });

    test('chặn lỗi đơn vị: 900 KHÔNG bị hiểu thành 900 phút', () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [_session('s1', DateTime(2026, 11, 24, 9, 0))],
        offsetsSeconds: const [900],
        now: now,
      );
      expect(plan.single.fireAt, DateTime(2026, 11, 24, 8, 45));
    });

    test('bỏ mốc đã qua: phiên còn 12 phút chỉ còn mốc 10 và 5', () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [_session('s1', now.add(const Duration(minutes: 12)))],
        offsetsSeconds: null,
        now: now,
      );
      expect(plan.map((r) => r.offsetSeconds), [600, 300]);
    });

    test('mốc trùng đúng thời điểm hiện tại cũng bị bỏ (không phát dồn)', () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [_session('s1', now.add(const Duration(minutes: 5)))],
        offsetsSeconds: const [300],
        now: now,
      );
      expect(plan, isEmpty);
    });

    test('phiên đã bắt đầu hoặc đã qua thì không nhắc', () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [
          _session('past', now.subtract(const Duration(minutes: 1))),
          _session('now', now),
        ],
        offsetsSeconds: null,
        now: now,
      );
      expect(plan, isEmpty);
    });

    test('nhiều phiên: xếp theo giờ kêu, ID tuần tự từ dải riêng', () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [
          _session('late', DateTime(2026, 11, 23, 14, 0)),
          _session('early', DateTime(2026, 11, 23, 10, 0)),
        ],
        offsetsSeconds: const [600],
        now: now,
      );
      expect(plan.map((r) => r.session.sessionId), ['early', 'late']);
      expect(plan.map((r) => r.notificationId), [
        AlarmService.preSessionIdBase,
        AlarmService.preSessionIdBase + 1,
      ]);
    });

    test('phiên trùng sessionId chỉ tính 1 lần', () {
      final start = DateTime(2026, 11, 23, 10, 0);
      final plan = AlarmService.planPreSessionReminders(
        sessions: [_session('s1', start), _session('s1', start)],
        offsetsSeconds: const [300],
        now: now,
      );
      expect(plan, hasLength(1));
    });

    test('giới hạn số mốc để không vượt trần báo thức Android', () {
      final sessions = [
        for (var i = 0; i < 40; i++)
          _session('s$i', now.add(Duration(hours: i + 1))),
      ];
      final plan = AlarmService.planPreSessionReminders(
        sessions: sessions,
        offsetsSeconds: null,
        now: now,
      );
      expect(plan, hasLength(AlarmService.maxPreSessionReminders));
      expect(plan.first.session.sessionId, 's0'); // giữ các mốc GẦN NHẤT
    });

    test('mốc kêu luôn làm tròn lên giây chẵn', () {
      final plan = AlarmService.planPreSessionReminders(
        sessions: [_session('s1', DateTime(2026, 11, 23, 10, 0, 0, 400))],
        offsetsSeconds: const [300],
        now: now,
      );
      expect(plan.single.fireAt, DateTime(2026, 11, 23, 9, 55, 1));
    });
  });

  group('AlarmService.preSessionText', () {
    PreSessionReminder one({int offset = 900, String? micro, int? minutes}) =>
        AlarmService.planPreSessionReminders(
          sessions: [
            _session('s1', DateTime(2026, 11, 23, 10, 0),
                minutes: minutes, micro: micro),
          ],
          offsetsSeconds: [offset],
          now: now,
        ).single;

    test('ghi rõ số phút, thời lượng phiên và gợi ý Micro-task 2 phút', () {
      final text = AlarmService.preSessionText(
        one(minutes: 25, micro: 'Mở sẵn tài liệu và đọc lướt mục lục'),
      );
      expect(text.title, 'Còn 15 phút nữa vào phiên tập trung');
      expect(
        text.body,
        'Viết dàn ý báo cáo · phiên 25 phút\n'
        'Khởi động 2 phút: Mở sẵn tài liệu và đọc lướt mục lục',
      );
    });

    test('không có micro-task → không thêm dòng trống', () {
      expect(AlarmService.preSessionText(one()).body, 'Viết dàn ý báo cáo');
    });

    test('mốc 0 giây → "Đến giờ vào phiên tập trung"', () {
      expect(AlarmService.preSessionText(one(offset: 0)).title,
          'Đến giờ vào phiên tập trung');
    });
  });

  group('AlarmService.parseReminderEntry', () {
    test('đọc "<id>|<sessionId>"', () {
      final e = AlarmService.parseReminderEntry('20003|sess-uuid');
      expect(e?.id, 20003);
      expect(e?.sessionId, 'sess-uuid');
    });

    test('vẫn đọc được bản cũ chỉ có id, bỏ qua chuỗi hỏng', () {
      expect(AlarmService.parseReminderEntry('20001')?.sessionId, isNull);
      expect(AlarmService.parseReminderEntry('abc'), isNull);
    });
  });
}
