// Điểm giao duy nhất giữa UI/Provider (UF3–UF5) và phần native: chuông, DND,
// Wakelock, ghi âm. UI chỉ cần gọi init() / start() / continueToNextRun() /
// stop() / recoverOnAppStart().
import 'dart:convert';
import 'dart:ui';

import 'package:android_alarm_manager_plus/android_alarm_manager_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import 'alarm_service.dart';
import 'audio_recorder_service.dart';
import 'dnd_service.dart';

/// Kết quả khi bắt đầu phiên. UI dùng [endsAt] cho đồng hồ trên màn hình để
/// khớp tuyệt đối với chuông.
class FocusStartResult {
  const FocusStartResult({
    required this.endsAt,
    required this.alarmScheduled,
    required this.dndApplied,
    required this.wakelockOn,
  });

  /// Mốc kết thúc đã làm tròn lên giây chẵn — dùng chung cho timer và chuông.
  final DateTime endsAt;

  /// false → thiếu quyền báo thức chính xác: UI nên nhắc nhẹ, phiên vẫn chạy.
  final bool alarmScheduled;

  /// false → thiếu quyền DND / user đã tự bật sẵn: phiên vẫn chạy bình thường.
  final bool dndApplied;
  final bool wakelockOn;
}

/// Kết quả khi kết thúc phiên.
class FocusStopResult {
  const FocusStopResult({
    this.savedRecording,
    this.dndRestoreState = 'not_needed',
  });

  final String dndRestoreState;

  /// Nếu phiên kết thúc lúc user đang giữ "Cất suy nghĩ", bản thu được LƯU
  /// (không hủy) để không mất suy nghĩ. UI chuyển nó vào Hộp nháp như bình thường.
  final ThoughtRecording? savedRecording;
}

enum FocusRecoveryState {
  /// Không có phiên nào dang dở (đã dọn chuông/DND mồ côi nếu có).
  none,

  /// Phiên vẫn còn giờ → UI khôi phục đồng hồ theo [FocusRecovery.endsAt].
  running,

  /// Đã hết giờ nhưng chưa quá hạn check-in → UI mở Check-in UF5 (giữ DND).
  awaitingCheckIn,

  /// Hết giờ và quá hạn check-in → đã tắt DND/Wakelock. Ghi nhận
  /// `no_response` là việc của UI/Backend, KHÔNG tự đánh dấu hoàn thành.
  checkInExpired,
}

class FocusRecovery {
  const FocusRecovery(this.state, {this.runId, this.endsAt});

  final FocusRecoveryState state;
  final String? runId;
  final DateTime? endsAt;
}

/// Chạy NGẦM (isolate riêng, kể cả khi app đã bị kill) lúc `endsAt + 5 phút`:
/// trả lại DND nếu user không phản hồi Check-in (UF5 `no_response`).
///
/// CHỈ trả lại DND — không đánh dấu hoàn thành, không xóa phiên đang chạy, để
/// lần mở app sau [FocusNativeController.recoverOnAppStart] vẫn báo
/// `checkInExpired` cho UI/Backend ghi nhận `no_response`.
@pragma('vm:entry-point')
Future<void> releaseDndAfterCheckInTimeout(
  int alarmId,
  Map<String, dynamic> params,
) async {
  // Isolate nền chưa đăng ký plugin phía Dart (shared_preferences...).
  DartPluginRegistrant.ensureInitialized();
  final runId = params['run_id'] as String?;
  if (runId == null) return;
  await DndService.instance.restore(runId: runId);
}

/// Gộp chuông + DND + Wakelock + ghi âm thành vài thao tác cho UI.
///
/// Quy tắc nghiệp vụ (AGENTS.md 2.1, UF5):
/// - Chuông kêu KHÔNG tự đánh dấu hoàn thành; KHÔNG gọi [stop] khi chuông kêu.
/// - Giữ DND trong lúc hỏi Check-in. Gọi [stop] khi user xác nhận kết quả /
///   nghỉ / báo bận / bỏ qua. Nếu user không phản hồi, báo thức ngầm tự trả
///   lại DND sau [checkInDndTimeout] (kể cả khi app đã chết).
/// - User làm tiếp phiên kế ngay → gọi [continueToNextRun] (giữ nguyên DND).
class FocusNativeController {
  FocusNativeController._();
  static final FocusNativeController instance = FocusNativeController._();

  /// UF5: tự tắt DND & Focus Mode sau 5 phút nếu không phản hồi Check-in.
  static const Duration checkInDndTimeout = Duration(minutes: 5);

  static const String _activeRunKey = 'focus_active_run';
  static const int _checkInTimeoutAlarmId = 9002;

  final AlarmService _alarm = AlarmService.instance;
  final DndService _dnd = DndService.instance;
  final AudioRecorderService _recorder = AudioRecorderService.instance;

  /// Gọi 1 lần trong main(), sau AlarmService.instance.init().
  Future<void> init() => AndroidAlarmManager.initialize();

  /// Gọi khi user bấm "Bắt đầu đếm giờ [X]p".
  ///
  /// [phases]: các đoạn Work/Rest của lượt (cùng thứ tự, cùng thời lượng với
  /// `FocusSegment` trong FocusProvider, tính từ đoạn work đầu tiên). Khi truyền,
  /// mỗi ranh giới Work ↔ Rest có 1 chuông riêng. Tổng thời lượng phải bằng
  /// [duration]; nếu lệch, bỏ qua chuông chuyển pha (vẫn có chuông hết phiên).
  Future<FocusStartResult> start({
    required String runId,
    required Duration duration,
    required String stepTitle,
    List<FocusPhase> phases = const [],
  }) {
    return _beginRun(
      runId: runId,
      duration: duration,
      stepTitle: stepTitle,
      phases: phases,
    );
  }

  /// Gọi khi user vừa xong phiên `previousRunId` và chọn làm tiếp phiên kế
  /// NGAY: chuyển DND sang phiên mới không có khoảng hở (UF5).
  Future<FocusStartResult> continueToNextRun({
    required String previousRunId,
    required String runId,
    required Duration duration,
    required String stepTitle,
    List<FocusPhase> phases = const [],
  }) async {
    await _alarm.cancelEnd();
    await _alarm.cancelPhaseAlarms();
    await AndroidAlarmManager.cancel(_checkInTimeoutAlarmId);
    final handedOver = await _dnd.handOver(
      fromRunId: previousRunId,
      toRunId: runId,
    );
    return _beginRun(
      runId: runId,
      duration: duration,
      stepTitle: stepTitle,
      phases: phases,
      dndAlreadyHeld: handedOver,
    );
  }

  /// Kết thúc phiên `runId`: hủy chuông + báo thức ngầm, trả lại DND, tắt
  /// Wakelock. Gọi nhiều lần vẫn an toàn.
  Future<FocusStopResult> stop({required String runId}) async {
    final active = await _loadActiveRun();
    final isActiveRun = active != null && active.runId == runId;

    ThoughtRecording? saved;
    if (_recorder.isRecording) {
      try {
        saved = await _recorder.stopAndFinalize();
      } on RecorderException {
        // Không lưu được thì thôi; recorder đã tự dọn file dở.
      }
    }

    // Chỉ hủy chuông nếu đúng phiên đang chạy, tránh hủy nhầm chuông phiên mới.
    if (isActiveRun) {
      await _alarm.cancelEnd();
      await _alarm.cancelPhaseAlarms();
      await AndroidAlarmManager.cancel(_checkInTimeoutAlarmId);
    }
    final dndRestoreState = await _dnd.restore(runId: runId);
    if (isActiveRun) {
      await _setWakelock(false);
      await _clearActiveRun();
    }
    return FocusStopResult(
      savedRecording: saved,
      dndRestoreState: dndRestoreState,
    );
  }

  /// Gọi định kỳ khi app ĐANG MỞ ở màn Check-in (vd. mỗi lần tick đồng hồ).
  ///
  /// Báo thức ngầm lúc `endsAt + 5 phút` chạy ở isolate riêng nên chỉ trả lại
  /// DND được, không tắt được Wakelock (cờ giữ màn hình gắn với Activity). Nếu
  /// app đang mở mà người dùng im lặng quá hạn, hàm này dọn nốt DND + Wakelock
  /// và trả về true để UI ghi nhận `no_response` (KHÔNG đánh dấu hoàn thành).
  Future<bool> releaseIfCheckInExpired({
    required String runId,
    DateTime? now,
  }) async {
    final active = await _loadActiveRun();
    if (active == null || active.runId != runId) return false;
    final deadline = active.endsAt.add(checkInDndTimeout);
    if ((now ?? DateTime.now()).isBefore(deadline)) return false;
    await stop(runId: runId);
    return true;
  }

  /// Gọi 1 lần khi mở app (sau khi giao diện hiện lên). Khôi phục hoặc dọn dẹp
  /// sau khi Android kill app giữa phiên.
  Future<FocusRecovery> recoverOnAppStart() async {
    final active = await _loadActiveRun();
    final now = DateTime.now();

    if (active == null) {
      // Không có phiên: dọn chuông/báo thức ngầm mồ côi và DND bị kẹt.
      await _alarm.cancelEnd();
      await _alarm.cancelPhaseAlarms();
      await AndroidAlarmManager.cancel(_checkInTimeoutAlarmId);
      final staleOwner = await _dnd.pendingOwnerRunId();
      if (staleOwner != null) await _dnd.restore(runId: staleOwner);
      return const FocusRecovery(FocusRecoveryState.none);
    }

    if (now.isBefore(active.endsAt)) {
      // Phiên còn giờ: Wakelock mất theo tiến trình → bật lại. Đặt lại chuông
      // và báo thức ngầm cùng mốc (ID cố định nên không bao giờ bị nhân đôi).
      await _setWakelock(true);
      await _scheduleAlarms(
        active.runId,
        active.endsAt,
        active.stepTitle,
        active.phases,
      );
      return FocusRecovery(
        FocusRecoveryState.running,
        runId: active.runId,
        endsAt: active.endsAt,
      );
    }

    if (now.isBefore(active.endsAt.add(checkInDndTimeout))) {
      // Đang trong 5 phút hỏi Check-in: giữ nguyên DND.
      return FocusRecovery(
        FocusRecoveryState.awaitingCheckIn,
        runId: active.runId,
        endsAt: active.endsAt,
      );
    }

    // Quá hạn Check-in: tắt DND & Focus Mode (UF5), không đánh dấu hoàn thành.
    await stop(runId: active.runId);
    return FocusRecovery(
      FocusRecoveryState.checkInExpired,
      runId: active.runId,
      endsAt: active.endsAt,
    );
  }

  Future<FocusStartResult> _beginRun({
    required String runId,
    required Duration duration,
    required String stepTitle,
    List<FocusPhase> phases = const [],
    bool dndAlreadyHeld = false,
  }) async {
    final endsAt = AlarmService.alignToSecond(DateTime.now().add(duration));
    final validPhases =
        phasesMatchDuration(phases, duration) ? phases : const <FocusPhase>[];

    // Ghi phiên đang chạy TRƯỚC mọi thao tác native: nếu app chết giữa chừng,
    // recoverOnAppStart() vẫn biết để dọn hoặc khôi phục.
    await _saveActiveRun(runId, endsAt, stepTitle, validPhases);

    // Chuông trước tiên: quan trọng nhất cho độ chính xác ≤1s.
    final alarmScheduled =
        await _scheduleAlarms(runId, endsAt, stepTitle, validPhases);
    final dndApplied = dndAlreadyHeld || await _dnd.enable(runId: runId);
    final wakelockOn = await _setWakelock(true);

    return FocusStartResult(
      endsAt: endsAt,
      alarmScheduled: alarmScheduled,
      dndApplied: dndApplied,
      wakelockOn: wakelockOn,
    );
  }

  /// Đặt chuông hết phiên + báo thức ngầm trả DND lúc endsAt + 5 phút.
  /// Trả về false nếu chuông hết phiên không đặt được.
  Future<bool> _scheduleAlarms(
    String runId,
    DateTime endsAt,
    String stepTitle,
    List<FocusPhase> phases,
  ) async {
    var endAlarmOk = true;
    try {
      await _alarm.scheduleEnd(endsAt, stepTitle: stepTitle);
    } catch (_) {
      endAlarmOk = false;
    }
    try {
      // Luôn gọi (kể cả danh sách rỗng) để xóa chuông pha của lượt trước.
      await _alarm.schedulePhaseAlarms(
        endsAt: endsAt,
        phases: phases,
        stepTitle: stepTitle,
      );
    } catch (_) {
      // Chuông pha là phụ: đồng hồ trên màn hình vẫn chuyển pha.
    }
    try {
      await AndroidAlarmManager.oneShotAt(
        endsAt.add(checkInDndTimeout),
        _checkInTimeoutAlarmId,
        releaseDndAfterCheckInTimeout,
        exact: true,
        wakeup: true,
        allowWhileIdle: true,
        rescheduleOnReboot: true,
        params: {'run_id': runId},
      );
    } catch (_) {
      // Không đặt được: recoverOnAppStart() vẫn dọn DND khi mở app.
    }
    return endAlarmOk;
  }

  Future<bool> _setWakelock(bool on) async {
    try {
      on ? await WakelockPlus.enable() : await WakelockPlus.disable();
      return on;
    } catch (_) {
      return false; // Máy không hỗ trợ: timer vẫn dùng được.
    }
  }

  /// Chuông pha chỉ hợp lệ khi tổng các đoạn đúng bằng thời lượng lượt.
  static bool phasesMatchDuration(List<FocusPhase> phases, Duration duration) {
    if (phases.isEmpty) return false;
    final total = phases.fold<int>(0, (sum, p) => sum + p.durationSeconds);
    return total == duration.inSeconds;
  }

  Future<void> _saveActiveRun(
    String runId,
    DateTime endsAt,
    String stepTitle,
    List<FocusPhase> phases,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _activeRunKey,
      jsonEncode({
        'run_id': runId,
        'ends_at': endsAt.toUtc().toIso8601String(),
        'step_title': stepTitle,
        'phases': [for (final p in phases) p.toJson()],
      }),
    );
  }

  Future<
      ({
        String runId,
        DateTime endsAt,
        String stepTitle,
        List<FocusPhase> phases,
      })?> _loadActiveRun() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_activeRunKey);
    if (raw == null) return null;
    final data = jsonDecode(raw) as Map<String, dynamic>;
    // Bản ghi cũ (trước tuần 4) không có step_title/phases → giá trị mặc định.
    final rawPhases = data['phases'] as List? ?? const [];
    return (
      runId: data['run_id'] as String,
      endsAt: DateTime.parse(data['ends_at'] as String).toLocal(),
      stepTitle: data['step_title'] as String? ?? 'phiên hiện tại',
      phases: [
        for (final p in rawPhases)
          FocusPhase.fromJson(Map<String, dynamic>.from(p as Map)),
      ],
    );
  }

  Future<void> _clearActiveRun() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_activeRunKey);
  }
}
