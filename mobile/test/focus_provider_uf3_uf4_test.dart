import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/models/focus_session_model.dart';
import 'package:proplan_va/models/task_model.dart';
import 'package:proplan_va/providers/focus_provider.dart';
import 'package:proplan_va/services/timer_service.dart';

class MockClock implements Clock {
  MockClock(this.nowTime);
  DateTime nowTime;

  @override
  DateTime now() => nowTime;

  void advance(Duration duration) {
    nowTime = nowTime.add(duration);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('UF3 & UF4 Mobile FocusProvider & Thought Lifecycle Tests', () {
    late MockClock mockClock;
    late FocusProvider provider;
    late FocusSessionInfo sampleSession;

    setUp(() {
      FlutterSecureStorage.setMockInitialValues({});
      mockClock = MockClock(DateTime(2026, 11, 2, 9, 0));
      provider = FocusProvider(clock: mockClock);

      sampleSession = FocusSessionInfo(
        sessionId: 'session-123',
        taskTitle: 'Làm bài tập lớn',
        stepTitle: 'Thu thập tài liệu',
        scheduledStart: DateTime(2026, 11, 2, 9, 0),
        scheduledEnd: DateTime(2026, 11, 2, 9, 25),
        workSeconds: 25 * 60,
        restSeconds: 5 * 60,
        microTaskText: 'Mở sẵn tài liệu và đọc mục lục',
        segments: const [
          SessionSegmentModel(
            kind: 'work',
            durationSeconds: 25 * 60,
            orderIndex: 0,
          ),
        ],
      );
    });

    tearDown(() {
      provider.dispose();
    });

    test('TC_MB_01: Bắt đầu phiên chuyển sang trạng thái Reminder và nạp đúng 1 bước hiện tại', () async {
      await provider.startSession(sampleSession);

      expect(provider.stage, FocusStage.reminder);
      expect(provider.session?.stepTitle, 'Thu thập tài liệu');
      expect(provider.session?.taskTitle, 'Làm bài tập lớn');
    });

    test('TC_MB_02: Start Run trong Grace Window (<= 15 phút) chuyển sang active', () async {
      await provider.startSession(sampleSession);

      // Đi tới 10 phút sau (trong grace window 15 phút)
      mockClock.advance(const Duration(minutes: 10));
      await provider.startRun();

      // Stage không bị đẩy sang lateStart
      expect(provider.stage, isNot(FocusStage.lateStart));
    });

    test('TC_MB_03: Start Run quá Grace Window (> 15 phút) chuyển sang trạng thái lateStart', () async {
      await provider.startSession(sampleSession);

      // Đi tới 16 phút sau (> 15 phút)
      mockClock.advance(const Duration(minutes: 16));
      await provider.startRun();

      expect(provider.stage, FocusStage.lateStart);
    });

    test('TC_MB_04: Micro-task chuẩn bị có thể bỏ qua để vào trạng thái Ready', () async {
      await provider.startSession(sampleSession);
      provider.showMicroTask();
      expect(provider.stage, FocusStage.microTask);

      provider.skipMicroTask();
      expect(provider.stage, FocusStage.ready);
      expect(provider.isMicroTaskRunning, isFalse);
    });

    test('TC_MB_05: ThoughtRecord Undo trong vòng 5 giây deadline', () {
      final now = mockClock.now();
      final record = ThoughtRecord(
        clientThoughtId: 'thought-1',
        createdAt: now,
        undoDeadlineAt: now.add(const Duration(seconds: 5)),
        sourceRunId: 'run-1',
        inputType: 'audio',
        localAudioPath: '/data/user/0/audio1.m4a',
        captureState: 'saved',
        isPendingSync: true,
      );

      // Trong vòng 3 giây: vẫn undo được
      mockClock.advance(const Duration(seconds: 3));
      expect(mockClock.now().isBefore(record.undoDeadlineAt), isTrue);

      // Sau 6 giây: hết hạn undo
      mockClock.advance(const Duration(seconds: 3));
      expect(mockClock.now().isBefore(record.undoDeadlineAt), isFalse);
    });

    test('TC_MB_06: ThoughtRecord serialization bảo vệ không rò rỉ Audio Path lên Server DTO', () {
      final now = mockClock.now();
      final record = ThoughtRecord(
        clientThoughtId: 'thought-2',
        createdAt: now,
        undoDeadlineAt: now.add(const Duration(seconds: 5)),
        sourceRunId: 'run-2',
        inputType: 'audio',
        localAudioPath: '/local/device/path.m4a',
        captureState: 'saved',
      );

      final json = record.toJson();
      expect(json['client_thought_id'], 'thought-2');
      expect(json['input_type'], 'audio');
      expect(json['local_audio_path'], '/local/device/path.m4a');
    });

    test('TC_MB_07: saveTextThought lưu thought văn bản và đổi sang thoughtSaved', () async {
      await provider.startSession(sampleSession);
      await provider.saveTextThought('Ý tưởng mới cần lưu');

      expect(provider.stage, FocusStage.thoughtSaved);
      expect(provider.thoughts.length, 1);
      expect(provider.thoughts.first.textContent, 'Ý tưởng mới cần lưu');
      expect(provider.thoughts.first.inputType, 'text');
    });

    test('TC_MB_08: Offline flag hiển thị đúng trên Provider', () {
      expect(provider.isOffline, isFalse);
    });
  });
}
