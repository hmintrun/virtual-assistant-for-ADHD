import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../core/api_client.dart';
import '../models/focus_session_model.dart';
import '../models/task_model.dart';
import '../models/planning_models.dart';
import '../repositories/local_run_repository.dart';
import '../services/audio_recorder_service.dart';
import '../services/alarm_service.dart';
import '../services/dnd_service.dart';
import '../services/focus_native_controller.dart';
import '../services/timer_service.dart';
import '../services/voice_service.dart';

enum FocusStage {
  reminder,
  lockPreview,
  microTask,
  ready,
  lateStart,
  active,
  breakTime,
  recovery,
  thoughtSaved,
  saveFailed,
  textFallback,
  ended,
}

class ThoughtRecord {
  const ThoughtRecord({
    required this.clientThoughtId,
    required this.createdAt,
    required this.undoDeadlineAt,
    this.sourceRunId,
    this.serverRunId,
    this.inputType = 'text',
    this.textContent,
    this.localAudioPath,
    this.attemptCount = 1,
    this.captureState = 'saved',
    this.isPendingSync = true,
    this.serverThoughtId,
  });

  // Kháº£ nÄƒng tÆ°Æ¡ng thÃ­ch ngÆ°á»£c cho cÃ¡c getter cÅ© (F3 & UI binding)
  String get id => clientThoughtId;
  String? get text => textContent;
  String? get audioPath => localAudioPath;

  final String clientThoughtId;
  final DateTime createdAt;
  final DateTime undoDeadlineAt;
  final String? sourceRunId;
  final String? serverRunId;
  final String inputType;
  final String? textContent;
  final String? localAudioPath;
  final int attemptCount;
  final String captureState;
  final bool isPendingSync;
  final String? serverThoughtId;

  Map<String, dynamic> toJson() => {
        'client_thought_id': clientThoughtId,
        'created_at': createdAt.toIso8601String(),
        'undo_deadline_at': undoDeadlineAt.toIso8601String(),
        if (sourceRunId != null) 'source_run_id': sourceRunId,
        if (serverRunId != null) 'server_run_id': serverRunId,
        'input_type': inputType,
        if (textContent != null) 'text_content': textContent,
        if (localAudioPath != null) 'local_audio_path': localAudioPath,
        'attempt_count': attemptCount,
        'capture_state': captureState,
        'pending_sync': isPendingSync,
        if (serverThoughtId != null) 'server_thought_id': serverThoughtId,
      };

  factory ThoughtRecord.fromJson(Map<String, dynamic> json) {
    final created = DateTime.parse(json['created_at'] as String);
    final deadline = json['undo_deadline_at'] != null
        ? DateTime.parse(json['undo_deadline_at'] as String)
        : created.add(const Duration(seconds: 5));
    final rawType = json['input_type'] as String?;
    final normalizedType = (rawType == 'raw_audio' ||
            (rawType == null &&
                (json['local_audio_path'] != null ||
                    json['audio_path'] != null)))
        ? 'audio'
        : (rawType ?? 'text');
    final rawState = json['capture_state'] as String?;
    final normalizedState =
        rawState == 'raw_saved' ? 'saved' : (rawState ?? 'saved');
    return ThoughtRecord(
      clientThoughtId: (json['client_thought_id'] ?? json['id']) as String,
      createdAt: created,
      undoDeadlineAt: deadline,
      sourceRunId: json['source_run_id'] as String?,
      serverRunId: json['server_run_id'] as String?,
      inputType: normalizedType,
      textContent: (json['text_content'] ?? json['text']) as String?,
      localAudioPath:
          (json['local_audio_path'] ?? json['audio_path']) as String?,
      attemptCount: json['attempt_count'] as int? ?? 1,
      captureState: normalizedState,
      isPendingSync: json['pending_sync'] as bool? ?? true,
      serverThoughtId: json['server_thought_id'] as String?,
    );
  }

  ThoughtRecord copyWith({
    bool? isPendingSync,
    String? serverThoughtId,
    int? attemptCount,
    String? captureState,
    String? textContent,
    String? localAudioPath,
  }) =>
      ThoughtRecord(
        clientThoughtId: clientThoughtId,
        createdAt: createdAt,
        undoDeadlineAt: undoDeadlineAt,
        sourceRunId: sourceRunId,
        serverRunId: serverRunId,
        inputType: inputType,
        textContent: textContent ?? this.textContent,
        localAudioPath: localAudioPath ?? this.localAudioPath,
        attemptCount: attemptCount ?? this.attemptCount,
        captureState: captureState ?? this.captureState,
        isPendingSync: isPendingSync ?? this.isPendingSync,
        serverThoughtId: serverThoughtId ?? this.serverThoughtId,
      );
}

class FocusProvider extends ChangeNotifier {
  FocusProvider({
    FocusTimerService? timer,
    FocusTimerService? microTaskTimer,
    AudioRecorderService? recorder,
    DndService? dnd,
    ApiClient? api,
    LocalRunRepository? repository,
    Clock? clock,
    FlutterSecureStorage? storage,
  })  : _clock = clock ?? const SystemClock(),
        timer = timer ?? FocusTimerService(clock: clock),
        microTaskTimer = microTaskTimer ?? FocusTimerService(clock: clock),
        recorder = recorder ?? AudioRecorderService.instance,
        dnd = dnd ?? DndService.instance,
        _api = api ?? ApiClient(),
        _repository = repository ??
            (storage != null
                ? SecureStorageRunRepository(storage: storage)
                : SecureStorageRunRepository()) {
    this.timer.onElapsed = _onSegmentElapsed;
    this.timer.addListener(_onTimerChanged);
    this.microTaskTimer.onElapsed = _onMicroTaskElapsed;
    this.microTaskTimer.addListener(_onTimerChanged);
    _recordingStopped = (recording) {
      unawaited(_onAutoRecordingSaved(recording));
    };
    _recordingFailedCallback = (error) {
      _isRecording = false;
      _lastError = error.message;
      if (error.countsAsFailure) {
        unawaited(_recordingFailed());
      } else {
        _notify();
      }
    };
    this.recorder.onAutoStopped = _recordingStopped;
    this.recorder.onAutoStopFailed = _recordingFailedCallback;
  }

  late final void Function(ThoughtRecording) _recordingStopped;
  late final void Function(RecorderException) _recordingFailedCallback;
  final Clock _clock;
  final FocusTimerService timer;
  final FocusTimerService microTaskTimer;
  final AudioRecorderService recorder;
  final DndService dnd;
  final ApiClient _api;
  final LocalRunRepository _repository;
  final List<ThoughtRecord> _thoughts = [];

  FocusSessionInfo? _session;
  DateTime? _runEndsAt;
  Map<String, dynamic>? _serverRun;
  Map<String, dynamic>? _pendingEnd;
  Map<String, dynamic>? _pendingResponse;
  Map<String, dynamic>? _startRequest;
  bool _runCommandBusy = false;
  String? _runError;

  bool get runCommandBusy => _runCommandBusy;
  String? get runError => _runError;
  bool get needsEndSync => _pendingEnd != null;
  Map<String, dynamic>? get _checkIn =>
      _serverRun?['check_in'] as Map<String, dynamic>?;
  String? get _focusOwnerId =>
      _serverRun?['run_id'] as String? ?? _session?.sessionId;
  bool get hasPendingCheckInResponse => _pendingResponse != null;
  bool get hasOpenCheckIn => _checkIn?['response_state'] == 'open';
  bool get missedCheckIn => _checkIn?['response_state'] == 'no_respond';
  bool get canAnswerCheckIn => hasOpenCheckIn || missedCheckIn;
  bool get hasUnfinishedServerRun =>
      needsEndSync || hasOpenCheckIn || hasPendingCheckInResponse;
  FocusStage _stage = FocusStage.ended;
  FocusStage _resumeStage = FocusStage.active;
  List<SessionSegmentModel> _segments = const [];
  int _segmentIndex = 0;
  int _actualWorkSeconds = 0;
  bool _dndPermission = false;
  bool _dndApplied = false;
  bool? _alarmScheduled;
  bool _isOffline = false;
  bool _isRecording = false;
  int _recordingAttempts = 0;
  String? _lastError;
  Timer? _offlineProbe;
  Timer? _checkInDeadline;
  int _offlineProbeGeneration = 0;
  Timer? _undoExpiry;
  Timer? _deleteUndoExpiry;
  Timer? _toastExpiry;
  bool _isFlushingOutbox = false;
  final Set<String> _undoneThoughtIds = {};
  ThoughtRecord? _pendingDeletedThought;
  bool _disposed = false;
  static const _deviceStateChannel = MethodChannel('proplan/device_state');
  Future<void> _deviceReports = Future<void>.value();
  Future<String>? _deviceId;

  Future<String> _getDeviceId() => _deviceId ??= _loadDeviceId();

  Future<String> _loadDeviceId() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString('focus_device_id');
    if (stored != null) return stored;
    final device = ApiClient.newIdempotencyKey('device');
    await prefs.setString('focus_device_id', device);
    return device;
  }

  bool _appInForeground = true;
  String _dndApplyState = 'not_applied';
  String _dndRestoreState = 'not_needed';

  void reportAppVisibility({required bool foreground}) {
    _appInForeground = foreground;
    _reportDeviceState();
  }

  void _reportDeviceState() {
    if (!_usesNativeFocus || _disposed) return;
    final run = _serverRun;
    final runId = run?['run_id'] as String?;
    final scheduleRevision = _session?.scheduleRevision;
    final apply = _dndApplyState;
    final restore = _dndRestoreState;
    final activeRunId = isActiveRun ? runId : null;
    final ownerRunId = _dndApplied ? runId : null;
    final visibility = _appInForeground ? 'foreground' : 'background';
    _deviceReports = _deviceReports.then((_) async {
      if (_disposed) return;
      try {
        final permissions = await _deviceStateChannel
            .invokeMapMethod<String, String>('readPermissions');
        if (permissions == null || _disposed) return;
        final prefs = await SharedPreferences.getInstance();
        final device = run?['device_id'] as String? ?? await _getDeviceId();
        if (_disposed) return;
        final revisionKey = 'focus_device_report_revision_$device';
        final revision = (prefs.getInt(revisionKey) ?? 0) + 1;
        await prefs.setInt(revisionKey, revision);
        await _api.putData(
          '/devices/${Uri.encodeComponent(device)}/state',
          data: {
            'device_report_revision': revision,
            ...permissions,
            'active_run_id': activeRunId,
            'last_schedule_revision': scheduleRevision,
            'dnd_owner_run_id': ownerRunId,
            'dnd_apply_state': apply,
            'dnd_restore_state': restore,
            'reported_at': _clock.now().toUtc().toIso8601String(),
            'app_visibility': visibility,
          },
        );
      } on ApiException catch (error) {
        // A later report can recover from a revision conflict without blocking Focus.
        final revision = error.currentRevisions['device_report_revision'];
        if (revision != null && !_disposed) {
          try {
            final prefs = await SharedPreferences.getInstance();
            final device = run?['device_id'] as String? ??
                prefs.getString('focus_device_id');
            if (device != null) {
              final key = 'focus_device_report_revision_$device';
              if (revision > (prefs.getInt(key) ?? 0)) {
                await prefs.setInt(key, revision);
              }
            }
          } catch (_) {}
        }
      } catch (_) {
        // Unavailable Android services must not interrupt a focus session.
      }
    });
  }

  FocusSessionInfo? get session => _session;
  FocusStage get stage => _stage;
  FocusStage get resumeStage => _resumeStage;
  List<ThoughtRecord> get thoughts => List.unmodifiable(_thoughts);
  int get remainingSeconds => timer.remainingSeconds;
  int get microTaskRemainingSeconds => microTaskTimer.remainingSeconds;
  double get progress => timer.progress;
  double get microTaskProgress => microTaskTimer.progress;
  bool get isMicroTaskRunning => microTaskTimer.isRunning;
  bool get timerRunning => timer.isRunning;
  bool get dndPermission => _dndPermission;
  bool get dndApplied => _dndApplied;
  bool? get alarmScheduled => _alarmScheduled;
  bool get isOffline => _isOffline;
  bool get isRecording => _isRecording;
  bool get canUndoDeletedThought => _pendingDeletedThought != null;
  bool get _usesNativeFocus =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  bool get canUndoThought {
    if (_thoughts.isEmpty) return false;
    final now = _clock.now();
    final latest = _thoughts.first;
    return latest.isPendingSync &&
        !_undoneThoughtIds.contains(latest.clientThoughtId) &&
        now.isBefore(latest.undoDeadlineAt);
  }

  int get recordingAttempts => _recordingAttempts;
  String? get lastError => _lastError;
  int get segmentIndex => _segmentIndex;
  int get actualWorkSeconds => _actualWorkSeconds;
  List<SessionSegmentModel> get segments => _segments;
  SessionSegmentModel? get currentSegment =>
      _segmentIndex < _segments.length ? _segments[_segmentIndex] : null;

  bool get isActiveRun =>
      _session != null &&
      (_stage == FocusStage.active ||
          _stage == FocusStage.breakTime ||
          _stage == FocusStage.recovery ||
          _stage == FocusStage.saveFailed ||
          _stage == FocusStage.textFallback ||
          _stage == FocusStage.thoughtSaved);

  Future<void> restore() async {
    await _loadThoughts();
    final nativeRecovery = _usesNativeFocus
        ? await FocusNativeController.instance.recoverOnAppStart()
        : null;
    final json = await _repository.loadActiveRun();
    if (json == null) {
      if (!_usesNativeFocus) {
        final staleOwner = await dnd.pendingOwnerRunId();
        if (staleOwner != null) await dnd.restore(runId: staleOwner);
      }
      return;
    }
    try {
      _session = FocusSessionInfo.fromJson(
        Map<String, dynamic>.from(json['session'] as Map),
      );
      _serverRun = json['server_run'] as Map<String, dynamic>?;
      _pendingEnd = json['pending_end'] as Map<String, dynamic>?;
      _pendingResponse = json['pending_response'] as Map<String, dynamic>?;
      if (json['stage'] == FocusStage.ended.name) {
        _stage = FocusStage.ended;
        _watchCheckInDeadline();
        _notify();
        return;
      }
      _segments = (json['segments'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(SessionSegmentModel.fromJson)
          .toList(growable: false);
      _segmentIndex = json['segment_index'] as int? ?? 0;
      _runEndsAt = DateTime.tryParse(json['run_ends_at'] as String? ?? '');
      _runEndsAt = nativeRecovery?.endsAt ?? _runEndsAt;
      _actualWorkSeconds = json['actual_work_seconds'] as int? ?? 0;
      _resumeStage = FocusStage.values.firstWhere(
        (stage) => stage.name == json['stage'],
        orElse: () => FocusStage.active,
      );
      _dndApplied = _usesNativeFocus
          ? await dnd.isAppliedFor(_focusOwnerId)
          : await dnd.pendingOwnerRunId() == _focusOwnerId;
      _dndApplyState = _dndApplied ? 'applied' : 'not_applied';
      _dndRestoreState = _dndApplied ? 'pending' : 'not_needed';
      _stage = _resumeStage;
      final deadline =
          DateTime.tryParse(json['timer_deadline'] as String? ?? '');
      final duration = json['timer_duration'] as int? ?? 0;
      if (_runEndsAt == null && deadline != null) {
        final secondsAfterCurrent = _segments
            .skip(_segmentIndex + 1)
            .fold<int>(0, (total, segment) => total + segment.durationSeconds);
        _runEndsAt = deadline.add(Duration(seconds: secondsAfterCurrent));
      }
      if (deadline != null && duration > 0) {
        final remainingMs = deadline.difference(_clock.now()).inMilliseconds;
        if (remainingMs <= 0) {
          final runEndedWhileClosed = _serverRun != null &&
              _runEndsAt != null &&
              !_clock.now().isBefore(_runEndsAt!);
          if (runEndedWhileClosed) {
            _actualWorkSeconds = _segments
                .where((segment) => segment.kind == 'work')
                .fold(0, (total, segment) => total + segment.durationSeconds);
            _segmentIndex = _segments.length;
            await _finishLocalRun(awaitCheckIn: true);
            return;
          }
          if (nativeRecovery?.state == FocusRecoveryState.checkInExpired) {
            _stage = FocusStage.ended;
            _dndApplied = false;
            await _repository.clearActiveRun();
            _notify();
            return;
          }
          if (nativeRecovery?.state == FocusRecoveryState.awaitingCheckIn) {
            if (currentSegment?.kind == 'work') {
              _actualWorkSeconds += duration;
            }
            _stage = FocusStage.ended;
            _notify();
            return;
          }
          // Segment có thể đã hết khi app đóng nhưng còn segment kế tiếp.
          // Khôi phục theo deadline tuyệt đối để timer tự chuyển tiếp.
          _stage = FocusStage.recovery;
          timer.restore(deadline, duration);
        } else {
          timer.restorePaused(deadline, duration);
          _stage = FocusStage.recovery;
        }
      }
      await _refreshDndPermission();
      _startOfflineProbe();
      _notify();
    } catch (_) {
      await _repository.clearActiveRun();
    }
  }

  Future<void> startSession(FocusSessionInfo session) async {
    if (isActiveRun || hasUnfinishedServerRun || _runCommandBusy) return;
    microTaskTimer.stop();
    _checkInDeadline?.cancel();
    _serverRun = null;
    _startRequest = null;
    _runError = null;
    _session = session;
    _runEndsAt = null;
    _segments = session.effectiveSegments
        .where((segment) => segment.durationSeconds > 0)
        .toList(growable: false);
    _segmentIndex = 0;
    _actualWorkSeconds = 0;
    _stage = FocusStage.reminder;
    _resumeStage = FocusStage.reminder;
    _recordingAttempts = 0;
    recorder.resetFailures();
    _lastError = null;
    _alarmScheduled = null;
    _dndApplyState = 'not_applied';
    _dndRestoreState = 'not_needed';
    await _refreshDndPermission();
    _notify();
  }

  void showLockPreview() => _setStage(FocusStage.lockPreview);
  void showMicroTask() => _setStage(FocusStage.microTask);
  void skipMicroTask() {
    microTaskTimer.stop();
    _setStage(FocusStage.ready);
  }

  void returnToActive() => _setStage(FocusStage.active);

  void continueFromMicroTask() {
    if (isMicroTaskRunning) return;
    microTaskTimer.start(2 * 60);
    _notify();
  }

  void _onMicroTaskElapsed() {
    if (_stage != FocusStage.microTask) return;
    _stage = FocusStage.ready;
    _notify();
  }

  /// Báº¯t Ä‘áº§u phiÃªn táº­p trung vá»›i cÆ¡ cháº¿ kiá»ƒm tra Feasibility Grace Window cá»§a A12.1:
  /// Náº¿u Ä‘áº¿n trÆ°á»›c hoáº·c trá»… trong vÃ²ng 15 phÃºt (<= 15m), cho phÃ©p báº¯t Ä‘áº§u phiÃªn bÃ¬nh thÆ°á»ng.
  /// Náº¿u trá»… quÃ¡ 15 phÃºt (> 15m), chuyá»ƒn sang tráº¡ng thÃ¡i lateStart Ä‘á»ƒ ngÆ°á»i dÃ¹ng xÃ¡c nháº­n.
  Future<void> startRun() async {
    final session = _session;
    if (session == null) return;
    final now = _clock.now();
    final graceDeadline =
        session.scheduledStart.add(const Duration(minutes: 15));
    if (now.isAfter(graceDeadline)) {
      _setStage(FocusStage.lateStart);
      return;
    }
    await _beginRun();
  }

  Future<void> startLateRun() async => _beginRun();

  Future<void> _beginRun() async {
    final session = _session;
    if (session == null || _runCommandBusy || isActiveRun) return;
    _runCommandBusy = true;
    _runError = null;
    _notify();
    try {
      final device = await _getDeviceId();
      final request = _startRequest ??= {
        'device_id': device,
        'expected_schedule_revision': session.scheduleRevision,
        'requested_start_at': _clock.now().toUtc().toIso8601String(),
      };
      try {
        _serverRun = await _api.postData(
          '/sessions/${session.sessionId}/start',
          data: request,
          idempotencyKey: ApiClient.stableIdempotencyKey(
              'start_${session.sessionId}', request.toString()),
        );
      } on ApiException catch (error) {
        if (error.statusCode != null) {
          _startRequest = null;
          rethrow;
        }
        _isOffline = true;
        _runError =
            'Phiên đang chạy trên thiết bị. Chưa xác nhận được với máy chủ.';
      }
      await _beginLocalRun();
    } on ApiException catch (error) {
      _runError = error.message;
    } finally {
      _runCommandBusy = false;
      _notify();
    }
  }

  Future<void> _beginLocalRun() async {
    _lastError = null;
    await _refreshDndPermission();
    if (_usesNativeFocus && _session != null) {
      final totalSeconds = _segments
          .skip(_firstWorkIndex)
          .fold<int>(0, (total, segment) => total + segment.durationSeconds);
      _runEndsAt = _clock.now().add(Duration(seconds: totalSeconds));
      try {
        try {
          await AlarmService.instance.requestPermissions();
        } catch (_) {
          // Timer vẫn chạy nếu alarm permission chưa được cấp.
        }
        final result = await FocusNativeController.instance.start(
          runId: _focusOwnerId!,
          duration: Duration(seconds: totalSeconds),
          stepTitle: _session!.stepTitle,
        );
        _runEndsAt = result.endsAt;
        _dndApplied = result.dndApplied;
        _dndApplyState = _dndApplied ? 'applied' : 'not_applied';
        _dndRestoreState = _dndApplied ? 'pending' : 'not_needed';
        _alarmScheduled = result.alarmScheduled;
      } catch (_) {
        _dndApplied = false;
        _dndApplyState = 'failed';
        _alarmScheduled = false;
      }
    } else {
      final totalSeconds = _segments
          .skip(_firstWorkIndex)
          .fold<int>(0, (total, segment) => total + segment.durationSeconds);
      _runEndsAt = _clock.now().add(Duration(seconds: totalSeconds));
      try {
        _dndApplied = await dnd.enable(runId: _focusOwnerId!);
      } catch (_) {
        _dndApplied = false;
      }
      try {
        await WakelockPlus.enable();
      } catch (_) {
        // The timer remains usable if a device does not support wakelock.
      }
    }
    _segmentIndex = _firstWorkIndex;
    await _startCurrentSegment();
    _startOfflineProbe();
    _reportDeviceState();
    await _persistRun();
    _notify();
  }

  int get _firstWorkIndex {
    final first = _segments.indexWhere((segment) => segment.kind == 'work');
    return first < 0 ? 0 : first;
  }

  Future<void> _startCurrentSegment() async {
    final segment = currentSegment;
    if (segment == null) {
      await _finishLocalRun();
      return;
    }
    _setStage(
        segment.kind == 'rest' ? FocusStage.breakTime : FocusStage.active);
    final secondsAfterCurrent = _segments
        .skip(_segmentIndex + 1)
        .fold<int>(0, (total, item) => total + item.durationSeconds);
    final segmentDeadline = _runEndsAt?.subtract(
      Duration(seconds: secondsAfterCurrent),
    );
    timer.start(segment.durationSeconds, deadline: segmentDeadline);
    await _persistRun();
  }

  void _onSegmentElapsed() {
    unawaited(_advanceSegment());
  }

  Future<void> _advanceSegment() async {
    if (!isActiveRun) return;
    final segment = currentSegment;
    if (segment != null && segment.kind == 'work') {
      // Chá»‰ cá»™ng dá»“n thá»i gian cá»§a cÃ¡c segment loáº¡i work vÃ o actualWorkSeconds
      // Micro-task 2 phÃºt vÃ  thá»i gian nghá»‰ (rest) hoÃ n toÃ n khÃ´ng Ä‘Æ°á»£c tÃ­nh.
      _actualWorkSeconds += segment.durationSeconds;
    }
    _segmentIndex++;
    if (_segmentIndex >= _segments.length) {
      await _finishLocalRun(awaitCheckIn: true);
      return;
    }
    await _startCurrentSegment();
  }

  Future<void> _finishLocalRun({bool awaitCheckIn = false}) async {
    final restElapsed = _segments
            .take(_segmentIndex)
            .where((segment) => segment.kind == 'rest')
            .fold<int>(0, (total, segment) => total + segment.durationSeconds) +
        (currentSegment?.kind == 'rest' ? timer.elapsedSeconds : 0);
    timer.stop();
    _stopOfflineProbe();
    if (awaitCheckIn && _usesNativeFocus && _isRecording) {
      try {
        final saved = await recorder.stopAndFinalize();
        _isRecording = false;
        await _saveFinalizedRecording(saved);
      } catch (error) {
        _isRecording = false;
        _lastError = error.toString();
      }
    }
    _stage = FocusStage.ended;
    await _restoreFocusMode(preserveCheckIn: awaitCheckIn);
    final endedAt = awaitCheckIn ? _runEndsAt ?? _clock.now() : _clock.now();
    _runEndsAt = null;
    // Káº¿t thÃºc Ä‘áº¿m giá» cá»¥c bá»™ KHÃ”NG tá»± Ä‘á»™ng Ä‘Ã¡nh dáº¥u hoÃ n thÃ nh Session/Step/Task (UF5).
    final run = _serverRun;
    if (run != null) {
      _pendingEnd = {
        'device_id': run['device_id'],
        'expected_run_revision': run['run_revision'],
        'event_id': ApiClient.newIdempotencyKey('end'),
        'ended_at': endedAt.toUtc().toIso8601String(),
        'end_reason': awaitCheckIn ? 'timer_elapsed' : 'user_stopped',
        'work_elapsed_seconds': _actualWorkSeconds,
        'rest_elapsed_seconds': restElapsed,
      };
      await _persistRun();
      await syncRunEnd();
    } else if (!(awaitCheckIn && _usesNativeFocus)) {
      await _repository.clearActiveRun();
    }
    _reportDeviceState();
    _notify();
  }

  Future<void> syncRunEnd() async {
    final pending = _pendingEnd;
    if (pending == null || _runCommandBusy) return;
    _runCommandBusy = true;
    _runError = null;
    _notify();
    try {
      _serverRun = await _api.postData(
        '/runs/${_serverRun!['run_id']}/end',
        data: pending,
        idempotencyKey: pending['event_id'] as String,
      );
      _pendingEnd = null;
      await _persistRun();
      _watchCheckInDeadline();
    } on ApiException catch (error) {
      _runError = error.message;
    } finally {
      _runCommandBusy = false;
      _notify();
    }
    if (!needsEndSync) await refreshRunCheckIn();
  }

  Future<void> restAfterCheckIn() async {
    if (_stage != FocusStage.ended || _runCommandBusy) return;
    await _restoreFocusMode();
    _notify();
  }

  void _watchCheckInDeadline() {
    _checkInDeadline?.cancel();
    if (!hasOpenCheckIn) return;
    final deadline =
        DateTime.tryParse(_serverRun?['checkin_timeout_at'] as String? ?? '');
    if (deadline == null) return;
    final delay =
        deadline.difference(_clock.now()) + const Duration(seconds: 1);
    _checkInDeadline = Timer(delay, () => unawaited(refreshRunCheckIn()));
  }

  Future<void> refreshRunCheckIn() async {
    final run = _serverRun;
    final id = run?['run_id'] as String?;
    if (id == null ||
        _stage != FocusStage.ended ||
        needsEndSync ||
        _runCommandBusy) return;
    try {
      final result = await _api.getData(
        '/runs/${Uri.encodeComponent(id)}/check-ins',
        queryParameters: const {'kind': 'final'},
      );
      if (_disposed || !identical(_serverRun, run) || _runCommandBusy) return;
      final checkIn = (result['items'] as List?)
          ?.whereType<Map<String, dynamic>>()
          .lastOrNull;
      if (checkIn == null) return;
      _serverRun = {...run!, 'check_in': checkIn};
      if (!hasOpenCheckIn) _checkInDeadline?.cancel();
      if (canAnswerCheckIn) {
        await _persistRun();
      } else {
        _pendingResponse = null;
        await _restoreFocusMode();
        await _repository.clearActiveRun();
      }
      _notify();
    } on ApiException {
      // Keep the check-in received when ending the run if refresh fails.
    }
  }

  Future<void> retryCheckInResponse() async {
    final pending = _pendingResponse;
    if (pending == null) return;
    await answerCheckIn(
      pending['session_outcome'] as String,
      skipScope: pending['skip_scope'] as String?,
      remainingNote: pending['remaining_note'] as String?,
      stepCompleted: pending['step_completed'] as bool?,
    );
  }

  Future<void> answerCheckIn(
    String outcome, {
    String? skipScope,
    String? remainingNote,
    bool? stepCompleted,
  }) async {
    final checkIn = _checkIn;
    if (checkIn == null || !canAnswerCheckIn || _runCommandBusy) return;
    _runCommandBusy = true;
    _runError = null;
    _notify();
    try {
      final pending = _pendingResponse ??= {
        'expected_response_revision': checkIn['response_revision'],
        'responded_at': _clock.now().toUtc().toIso8601String(),
        'session_outcome': outcome,
        if (skipScope != null) 'skip_scope': skipScope,
        if (stepCompleted != null) 'step_completed': stepCompleted,
        if (remainingNote?.trim().isNotEmpty == true)
          'remaining_note': remainingNote!.trim(),
      };
      await _persistRun();
      final response = await _api.putData(
        '/check-ins/${checkIn['checkin_id']}/response',
        data: pending,
        idempotencyKey: ApiClient.stableIdempotencyKey(
            'checkin_${checkIn['checkin_id']}', pending.toString()),
      );
      _serverRun = {..._serverRun!, 'check_in': response};
      _checkInDeadline?.cancel();
      _pendingResponse = null;
      await _restoreFocusMode();
      await _repository.clearActiveRun();
    } on ApiException catch (error) {
      if (error.statusCode != null) {
        _pendingResponse = null;
        await _persistRun();
      }
      _runError = error.message;
    } finally {
      _runCommandBusy = false;
      _notify();
    }
    if (_runError != null) await refreshRunCheckIn();
  }

  Future<void> endEarly() async {
    if (!isActiveRun || _runCommandBusy || !timer.isRunning) return;
    final segment = currentSegment;
    if (segment != null && segment.kind == 'work') {
      _actualWorkSeconds += timer.elapsedSeconds;
    }
    await _finishLocalRun();
  }

  Future<void> resumeRecoveredRun() async {
    _stage = _resumeStage;
    if (timer.isPaused) {
      final deadline = timer.endsAt;
      if (deadline != null) {
        timer.restore(deadline, timer.durationSeconds);
      } else {
        timer.resume();
      }
    } else if (!timer.isRunning && currentSegment != null) {
      await _startCurrentSegment();
    }
    _startOfflineProbe();
    _notify();
  }

  Future<void> handleAppResume() async {
    if (!isActiveRun || _stage == FocusStage.recovery) return;
    _resumeStage = _stage;
    _stage = FocusStage.recovery;
    await _persistRun();
    _notify();
  }

  Future<void> openDndSettings() async {
    dnd.openPermissionSettings();
  }

  Future<void> refreshDndPermission() async {
    await _refreshDndPermission();
    _notify();
  }

  Future<void> _refreshDndPermission() async {
    try {
      _dndPermission = await dnd.hasPermission;
    } catch (_) {
      _dndPermission = false;
    }
  }

  Future<void> beginRecording() async {
    if (_isRecording || !isActiveRun || _stage == FocusStage.breakTime) return;
    _lastError = null;
    try {
      await recorder.start();
      _isRecording = true;
      _notify();
    } catch (error) {
      _lastError = error.toString();
      await _recordingFailed();
    }
  }

  Future<void> stopAndSaveThought() async {
    if (!_isRecording) return;
    try {
      final recording = await recorder.stopAndFinalize();
      _isRecording = false;
      await _saveAudioThought(recording);
      await _persistRun();
      _notify();
    } catch (error) {
      _isRecording = false;
      _lastError = error.toString();
      if (error is! RecorderException || error.countsAsFailure) {
        await _recordingFailed();
      } else {
        _stage = FocusStage.active;
        _notify();
      }
    }
  }

  Future<void> _saveAudioThought(ThoughtRecording recording) async {
    final now = _clock.now();
    final attemptCount = (_recordingAttempts + 1).clamp(1, 3);
    final thought = ThoughtRecord(
      clientThoughtId: now.microsecondsSinceEpoch.toString(),
      createdAt: now,
      undoDeadlineAt: now.add(const Duration(seconds: 5)),
      serverRunId: _serverRun?['run_id'] as String?,
      inputType: 'audio',
      localAudioPath: recording.path,
      attemptCount: attemptCount,
      captureState: 'saved',
      isPendingSync: true,
    );
    await _saveThought(thought);
    unawaited(_confirmAudioSaved());
    _recordingAttempts = 0;
    recorder.resetFailures();
    if (_stage != FocusStage.thoughtSaved) _resumeStage = _stage;
    _stage = FocusStage.thoughtSaved;
    _toastExpiry?.cancel();
    _toastExpiry = Timer(const Duration(seconds: 5), () {
      if (_stage == FocusStage.thoughtSaved) _stage = _resumeStage;
      _notify();
    });
    _scheduleOutboxFlush();
  }

  Future<void> _onAutoRecordingSaved(ThoughtRecording recording) async {
    _isRecording = false;
    await _saveAudioThought(recording);
    await _persistRun();
    _notify();
  }

  Future<void> cancelRecording() async {
    await recorder.cancel();
    _isRecording = false;
    _notify();
  }

  Future<void> _recordingFailed() async {
    _isRecording = false;
    _recordingAttempts++;
    _stage = recorder.shouldFallbackToText || _recordingAttempts >= 3
        ? FocusStage.textFallback
        : FocusStage.saveFailed;
    await _persistRun();
    _notify();
  }

  Future<void> retryRecording() async {
    if (recorder.shouldFallbackToText || _recordingAttempts >= 3) {
      _setStage(FocusStage.textFallback);
      return;
    }
    _stage = FocusStage.active;
    await beginRecording();
  }

  Future<void> saveTextThought(String text) async {
    final normalized = text.trim();
    if (normalized.isEmpty) return;
    final now = _clock.now();
    final thought = ThoughtRecord(
      clientThoughtId: now.microsecondsSinceEpoch.toString(),
      createdAt: now,
      undoDeadlineAt: now.add(const Duration(seconds: 5)),
      serverRunId: _serverRun?['run_id'] as String?,
      inputType: 'text',
      textContent: normalized,
      attemptCount: 1,
      captureState: 'saved',
      isPendingSync: true,
    );
    await _saveThought(thought);
    _recordingAttempts = 0;
    recorder.resetFailures();
    if (_stage != FocusStage.thoughtSaved) {
      _resumeStage = _stage;
    }
    _stage = FocusStage.thoughtSaved;
    _toastExpiry?.cancel();
    _toastExpiry = Timer(const Duration(seconds: 5), () {
      if (_stage == FocusStage.thoughtSaved) _stage = _resumeStage;
      _notify();
    });
    _scheduleOutboxFlush();
    await _persistRun();
    _notify();
  }

  Future<void> undoLastThought() async {
    if (!canUndoThought || _thoughts.isEmpty) return;
    _undoExpiry?.cancel();
    _toastExpiry?.cancel();
    final thought = _thoughts.removeAt(0);
    _undoneThoughtIds.add(thought.clientThoughtId);
    if (thought.localAudioPath != null) {
      try {
        await recorder.deleteRecording(thought.localAudioPath!);
      } catch (_) {}
    }
    await _repository.deleteThought(thought.clientThoughtId);
    if (_stage == FocusStage.thoughtSaved) _stage = _resumeStage;
    _scheduleOutboxFlush();
    await _persistRun();
    _notify();
  }

  Future<void> _saveThought(ThoughtRecord thought) async {
    await _repository.queueThought(thought);
    _thoughts.insert(0, thought);
  }

  Future<void> deleteThought(ThoughtRecord thought) async {
    if (_pendingDeletedThought != null) {
      await _finalizePendingThoughtDeletion();
    }
    final index = _thoughts.indexWhere(
      (item) => item.clientThoughtId == thought.clientThoughtId,
    );
    if (index < 0) return;
    final removed = _thoughts.removeAt(index);
    _pendingDeletedThought = removed;
    _undoneThoughtIds.add(removed.clientThoughtId);
    _deleteUndoExpiry?.cancel();
    _deleteUndoExpiry = Timer(const Duration(seconds: 5), () {
      unawaited(_finalizePendingThoughtDeletion());
    });
    _scheduleOutboxFlush();
    _notify();
  }

  Future<void> undoDeletedThought() async {
    final thought = _pendingDeletedThought;
    if (thought == null) return;
    _deleteUndoExpiry?.cancel();
    _deleteUndoExpiry = null;
    _pendingDeletedThought = null;
    _undoneThoughtIds.remove(thought.clientThoughtId);
    _thoughts.insert(0, thought);
    await _repository.queueThought(thought);
    _scheduleOutboxFlush();
    _notify();
  }

  Future<void> _finalizePendingThoughtDeletion() async {
    final thought = _pendingDeletedThought;
    _pendingDeletedThought = null;
    _deleteUndoExpiry?.cancel();
    _deleteUndoExpiry = null;
    if (thought == null) return;
    await _repository.deleteThought(thought.clientThoughtId);
    if (thought.localAudioPath != null) {
      try {
        await recorder.deleteRecording(thought.localAudioPath!);
      } catch (_) {}
    }
    _scheduleOutboxFlush();
    _notify();
  }

  void _scheduleOutboxFlush() {
    final now = _clock.now();
    final pending = _thoughts
        .where((t) =>
            t.isPendingSync && !_undoneThoughtIds.contains(t.clientThoughtId))
        .toList();
    if (pending.isEmpty) {
      _undoExpiry?.cancel();
      return;
    }

    final nextReady =
        pending.where((t) => t.undoDeadlineAt.isAfter(now)).toList();
    if (nextReady.isEmpty) {
      unawaited(flushOutbox());
      return;
    }

    nextReady.sort((a, b) => a.undoDeadlineAt.compareTo(b.undoDeadlineAt));
    final delay = nextReady.first.undoDeadlineAt.difference(now);

    _undoExpiry?.cancel();
    _undoExpiry = Timer(delay, () {
      _notify();
      unawaited(flushOutbox());
      _scheduleOutboxFlush();
    });
  }

  Future<void> _loadThoughts() async {
    final loaded = await _repository.loadThoughts();
    _thoughts
      ..clear()
      ..addAll(loaded);
    _scheduleOutboxFlush();
  }

  /// Đồng bộ hàng đợi Outbox lên Backend khi có kết nối mạng:
  /// - Khóa Re-entrancy _isFlushingOutbox chống gửi song song và race condition (BUG-05).
  /// - CHỈ claim và gửi các Thought đã trôi qua hạn Hoàn tác (Undo 5 giây).
  /// - Kiểm tra _undoneThoughtIds để không bao giờ gửi bản ghi đã bấm Hoàn tác (Zombie Upload BUG-07).
  /// - Chuẩn hóa 100% Payload metadata theo đúng Backend Contract (F3, BUG-10, BUG-11, BUG-12).
  Future<void> flushOutbox() async {
    if (_isOffline || _isFlushingOutbox) return;
    _isFlushingOutbox = true;
    try {
      final now = _clock.now();
      final readyToSync = _thoughts
          .where((t) =>
              t.isPendingSync &&
              !_undoneThoughtIds.contains(t.clientThoughtId) &&
              !now.isBefore(t.undoDeadlineAt))
          .toList();
      for (final thought in readyToSync) {
        if (_undoneThoughtIds.contains(thought.clientThoughtId)) continue;
        try {
          // Older entries stored a Session ID; only serverRunId is safe to send.
          final payload = {
            'client_thought_id': thought.clientThoughtId,
            if (thought.serverRunId != null)
              'source_run_id': thought.serverRunId,
            'input_type': thought.inputType,
            if (thought.textContent != null)
              'text_content': thought.textContent,
            'attempt_count': thought.attemptCount,
            'capture_state': thought.captureState,
          };
          final response = await _api.postData(
            '/thoughts',
            data: payload,
            idempotencyKey: thought.clientThoughtId,
          );
          if (_undoneThoughtIds.contains(thought.clientThoughtId)) continue;
          final serverId = response['thought_id'];
          if (serverId is! String || serverId.isEmpty) continue;
          await _repository.markThoughtSynced(
              thought.clientThoughtId, serverId);
          final index = _thoughts
              .indexWhere((t) => t.clientThoughtId == thought.clientThoughtId);
          if (index >= 0) {
            _thoughts[index] = _thoughts[index].copyWith(
              isPendingSync: false,
              serverThoughtId: serverId,
            );
          }
        } catch (_) {
          // Mạng gián đoạn hoặc server bận: giữ nguyên isPendingSync để thử lại lần sau.
        }
      }
      _notify();
    } finally {
      _isFlushingOutbox = false;
    }
  }

  Future<String> transcribeThought(ThoughtRecord thought) async {
    if (isActiveRun || isRecording || runCommandBusy || needsEndSync) {
      throw const ApiException('Hãy kết thúc phiên trước khi chuyển ghi âm.');
    }
    final path = thought.localAudioPath;
    final id = thought.serverThoughtId;
    if (kIsWeb || path == null || id == null) {
      throw const ApiException(
          'Chưa có ghi âm đã đồng bộ để chuyển thành chữ.');
    }
    return VoiceTranscriptionService(api: _api).transcribeM4a(
      path,
      recordingId: thought.clientThoughtId,
      thoughtId: id,
    );
  }

  Future<PlanningDraftModel> convertThoughtToDraft(
    ThoughtRecord thought,
    String text,
  ) async {
    if (isActiveRun || isRecording || runCommandBusy || needsEndSync) {
      throw const ApiException('Hãy kết thúc phiên trước khi tạo việc.');
    }
    final id = thought.serverThoughtId;
    if (id == null) {
      throw const ApiException('Ghi chú chưa đồng bộ. Hãy thử lại sau.');
    }
    final path = '/thoughts/${Uri.encodeComponent(id)}';
    final current = await _api.getData(path);
    final linked = current['linked_draft_id'] as String?;
    if (linked != null) {
      return PlanningDraftModel.fromJson(
        await _api.getData('/drafts/${Uri.encodeComponent(linked)}'),
      );
    }
    final payload = {
      'expected_thought_revision': current['thought_revision'],
      'text_content': text.trim(),
    };
    final response = await _api.postData(
      '$path/draft',
      data: payload,
      idempotencyKey: ApiClient.stableIdempotencyKey(
          'thought_draft_$id', payload.toString()),
    );
    return PlanningDraftModel.fromJson(
        response['draft'] as Map<String, dynamic>);
  }

  Future<void> _persistRun() async {
    final session = _session;
    if (session == null ||
        (!isActiveRun && !hasUnfinishedServerRun && _serverRun == null)) return;
    await _repository.saveActiveRun({
      'session': session.toJson(),
      if (_serverRun != null) 'server_run': _serverRun,
      if (_pendingEnd != null) 'pending_end': _pendingEnd,
      if (_pendingResponse != null) 'pending_response': _pendingResponse,
      if (_runEndsAt != null) 'run_ends_at': _runEndsAt!.toIso8601String(),
      'segments': _segments.map((segment) => segment.toJson()).toList(),
      'segment_index': _segmentIndex,
      'actual_work_seconds': _actualWorkSeconds,
      'stage': _stage.name,
      'timer_deadline': timer.endsAt?.toIso8601String(),
      'timer_duration': timer.durationSeconds,
    });
  }

  void _startOfflineProbe() {
    _stopOfflineProbe();
    unawaited(_probeBackend(_offlineProbeGeneration));
  }

  Future<void> _probeBackend(int generation) async {
    bool offline;
    try {
      await _api.dio.get<Map<String, dynamic>>('/capabilities');
      offline = false;
    } catch (_) {
      offline = true;
    }
    if (_disposed || generation != _offlineProbeGeneration) return;
    if (_isOffline != offline) {
      _isOffline = offline;
      _notify();
    }
    if (!offline && _thoughts.any((thought) => thought.isPendingSync)) {
      unawaited(flushOutbox());
    }
    _offlineProbe = Timer(Duration(seconds: offline ? 30 : 60), () {
      unawaited(_probeBackend(generation));
    });
  }

  void _stopOfflineProbe() {
    _offlineProbeGeneration++;
    _offlineProbe?.cancel();
    _offlineProbe = null;
  }

  Future<void> _restoreFocusMode({bool preserveCheckIn = false}) async {
    final session = _session;
    if (_usesNativeFocus && session != null) {
      if (preserveCheckIn) return;
      try {
        final result = await FocusNativeController.instance.stop(
          runId: _focusOwnerId!,
        );
        if (result.dndRestoreState != 'not_needed' ||
            _dndRestoreState == 'pending') {
          _dndRestoreState = result.dndRestoreState;
        }
        final saved = result.savedRecording;
        if (saved != null) {
          _isRecording = false;
          await _saveFinalizedRecording(saved);
        }
      } catch (_) {
        if (_dndApplied) _dndRestoreState = 'failed';
      }
    } else {
      try {
        final runId = _focusOwnerId;
        if (runId != null) await dnd.restore(runId: runId);
      } catch (_) {}
      try {
        await WakelockPlus.disable();
      } catch (_) {}
    }
    _dndApplied = false;
    _reportDeviceState();
  }

  Future<void> _confirmAudioSaved() async {
    if (!_usesNativeFocus) return;
    try {
      await HapticFeedback.lightImpact();
    } on PlatformException {
      // Audio is already saved even if the device cannot provide feedback.
    } on MissingPluginException {
      // Some Android test hosts do not expose haptics.
    }
  }

  Future<void> _saveFinalizedRecording(ThoughtRecording recording) async {
    final now = _clock.now();
    await _saveThought(ThoughtRecord(
      clientThoughtId: now.microsecondsSinceEpoch.toString(),
      createdAt: now,
      undoDeadlineAt: now.add(const Duration(seconds: 5)),
      serverRunId: _serverRun?['run_id'] as String?,
      inputType: 'audio',
      localAudioPath: recording.path,
      attemptCount: (_recordingAttempts + 1).clamp(1, 3),
      captureState: 'saved',
      isPendingSync: true,
    ));
    unawaited(_confirmAudioSaved());
    _scheduleOutboxFlush();
  }

  void _setStage(FocusStage stage) {
    _stage = stage;
    _notify();
  }

  void _onTimerChanged() {
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _checkInDeadline?.cancel();
    _stopOfflineProbe();
    _undoExpiry?.cancel();
    _deleteUndoExpiry?.cancel();
    _toastExpiry?.cancel();
    if (identical(recorder.onAutoStopped, _recordingStopped)) {
      recorder.onAutoStopped = null;
      recorder.onAutoStopFailed = null;
    }
    timer.removeListener(_onTimerChanged);
    microTaskTimer.removeListener(_onTimerChanged);
    timer.dispose();
    microTaskTimer.dispose();
    super.dispose();
  }
}
