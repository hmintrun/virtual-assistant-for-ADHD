import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../core/api_client.dart';
import '../models/focus_session_model.dart';
import '../models/task_model.dart';
import '../repositories/local_run_repository.dart';
import '../services/audio_recorder_service.dart';
import '../services/dnd_service.dart';
import '../services/timer_service.dart';

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
    required this.id,
    required this.createdAt,
    this.audioPath,
    this.text,
    this.isPendingSync = true,
  });

  final String id;
  final DateTime createdAt;
  final String? audioPath;
  final String? text;
  final bool isPendingSync;

  Map<String, dynamic> toJson() => {
        'id': id,
        'created_at': createdAt.toIso8601String(),
        if (audioPath != null) 'audio_path': audioPath,
        if (text != null) 'text': text,
        'pending_sync': isPendingSync,
      };

  factory ThoughtRecord.fromJson(Map<String, dynamic> json) => ThoughtRecord(
        id: json['id'] as String,
        createdAt: DateTime.parse(json['created_at'] as String),
        audioPath: json['audio_path'] as String?,
        text: json['text'] as String?,
        isPendingSync: json['pending_sync'] as bool? ?? true,
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
        recorder = recorder ?? AudioRecorderService(),
        dnd = dnd ?? DndService(),
        _api = api ?? ApiClient(),
        _repository = repository ??
            (storage != null
                ? SecureStorageRunRepository(storage: storage)
                : const SecureStorageRunRepository()) {
    this.timer.onElapsed = _onSegmentElapsed;
    this.timer.addListener(_onTimerChanged);
    this.microTaskTimer.onElapsed = _onMicroTaskElapsed;
    this.microTaskTimer.addListener(_onTimerChanged);
  }

  final Clock _clock;
  final FocusTimerService timer;
  final FocusTimerService microTaskTimer;
  final AudioRecorderService recorder;
  final DndService dnd;
  final ApiClient _api;
  final LocalRunRepository _repository;
  final List<ThoughtRecord> _thoughts = [];

  FocusSessionInfo? _session;
  FocusStage _stage = FocusStage.ended;
  FocusStage _resumeStage = FocusStage.active;
  DndSnapshot? _dndSnapshot;
  List<SessionSegmentModel> _segments = const [];
  int _segmentIndex = 0;
  int _actualWorkSeconds = 0;
  bool _dndPermission = false;
  bool _dndApplied = false;
  bool _isOffline = false;
  bool _isRecording = false;
  bool _canUndoThought = false;
  int _recordingAttempts = 0;
  String? _recordingPath;
  String? _lastError;
  Timer? _offlineProbe;
  Timer? _undoExpiry;
  Timer? _toastExpiry;
  bool _disposed = false;

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
  bool get isOffline => _isOffline;
  bool get isRecording => _isRecording;
  bool get canUndoThought => _canUndoThought;
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
    final json = await _repository.loadActiveRun();
    if (json == null) return;
    try {
      _session = FocusSessionInfo.fromJson(
        Map<String, dynamic>.from(json['session'] as Map),
      );
      _segments = (json['segments'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(SessionSegmentModel.fromJson)
          .toList(growable: false);
      _segmentIndex = json['segment_index'] as int? ?? 0;
      _actualWorkSeconds = json['actual_work_seconds'] as int? ?? 0;
      _resumeStage = FocusStage.values.firstWhere(
        (stage) => stage.name == json['stage'],
        orElse: () => FocusStage.active,
      );
      final dndJson = json['dnd_snapshot'] as Map<String, dynamic>?;
      _dndSnapshot = dndJson == null ? null : DndSnapshot.fromJson(dndJson);
      _dndApplied = _dndSnapshot?.appApplied ?? false;
      _stage = _resumeStage;
      final deadline =
          DateTime.tryParse(json['timer_deadline'] as String? ?? '');
      final duration = json['timer_duration'] as int? ?? 0;
      if (deadline != null && duration > 0) timer.restore(deadline, duration);
      _stage = FocusStage.recovery;
      await _refreshDndPermission();
      _startOfflineProbe();
      _notify();
    } catch (_) {
      await _repository.clearActiveRun();
    }
  }

  Future<void> startSession(FocusSessionInfo session) async {
    microTaskTimer.stop();
    _session = session;
    _segments = session.effectiveSegments
        .where((segment) => segment.durationSeconds > 0)
        .toList(growable: false);
    _segmentIndex = 0;
    _actualWorkSeconds = 0;
    _stage = FocusStage.reminder;
    _resumeStage = FocusStage.reminder;
    _recordingAttempts = 0;
    _lastError = null;
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

  /// Bắt đầu phiên tập trung với cơ chế kiểm tra Feasibility Grace Window của A12.1:
  /// Nếu đến trước hoặc trễ trong vòng 15 phút (<= 15m), cho phép bắt đầu phiên bình thường.
  /// Nếu trễ quá 15 phút (> 15m), chuyển sang trạng thái lateStart để người dùng xác nhận.
  Future<void> startRun() async {
    final session = _session;
    if (session == null) return;
    final now = _clock.now();
    final graceDeadline = session.scheduledStart.add(const Duration(minutes: 15));
    if (now.isAfter(graceDeadline)) {
      _setStage(FocusStage.lateStart);
      return;
    }
    await _beginRun();
  }

  Future<void> startLateRun() async => _beginRun();

  Future<void> _beginRun() async {
    _lastError = null;
    await _refreshDndPermission();
    try {
      _dndSnapshot = await dnd.enable();
      _dndApplied = _dndSnapshot?.appApplied ?? false;
    } catch (_) {
      _dndSnapshot = const DndSnapshot(previousFilter: null, appApplied: false);
      _dndApplied = false;
    }
    try {
      await WakelockPlus.enable();
    } catch (_) {
      // The timer remains usable if a device does not support wakelock.
    }
    _segmentIndex = _firstWorkIndex;
    await _startCurrentSegment();
    _startOfflineProbe();
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
    timer.start(segment.durationSeconds);
    await _persistRun();
  }

  void _onSegmentElapsed() {
    unawaited(_advanceSegment());
  }

  Future<void> _advanceSegment() async {
    if (!isActiveRun) return;
    final segment = currentSegment;
    if (segment != null && segment.kind == 'work') {
      // Chỉ cộng dồn thời gian của các segment loại work vào actualWorkSeconds
      // Micro-task 2 phút và thời gian nghỉ (rest) hoàn toàn không được tính.
      _actualWorkSeconds += segment.durationSeconds;
    }
    _segmentIndex++;
    if (_segmentIndex >= _segments.length) {
      await _finishLocalRun();
      return;
    }
    await _startCurrentSegment();
  }

  Future<void> _finishLocalRun() async {
    timer.stop();
    _stopOfflineProbe();
    await _restoreFocusMode();
    _stage = FocusStage.ended;
    // Kết thúc đếm giờ cục bộ KHÔNG tự động đánh dấu hoàn thành Session/Step/Task (UF5).
    await _repository.clearActiveRun();
    _notify();
  }

  Future<void> endEarly() async {
    if (_isRecording) await cancelRecording();
    final segment = currentSegment;
    if (segment != null && segment.kind == 'work') {
      _actualWorkSeconds += timer.elapsedSeconds;
    }
    await _finishLocalRun();
  }

  Future<void> resumeRecoveredRun() async {
    _stage = _resumeStage;
    if (!timer.isRunning && currentSegment != null) {
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
    String? path;
    try {
      path = await recorder.createRecordingPath();
      await recorder.start(path);
      _recordingPath = path;
      _isRecording = true;
      _notify();
    } catch (error) {
      if (path != null) await recorder.delete(path);
      _lastError = error.toString();
      await _recordingFailed();
    }
  }

  Future<void> stopAndSaveThought() async {
    final path = _recordingPath;
    if (!_isRecording || path == null) return;
    try {
      final savedPath = await recorder.stopAndValidate(path);
      _isRecording = false;
      _recordingPath = null;
      final thought = ThoughtRecord(
        id: _clock.now().microsecondsSinceEpoch.toString(),
        createdAt: _clock.now(),
        audioPath: savedPath,
        isPendingSync: true,
      );
      await _saveThought(thought);
      _canUndoThought = true;
      _stage = FocusStage.thoughtSaved;
      _undoExpiry?.cancel();
      _toastExpiry?.cancel();
      _undoExpiry = Timer(const Duration(seconds: 5), () {
        _canUndoThought = false;
        _notify();
        unawaited(flushOutbox());
      });
      _toastExpiry = Timer(const Duration(seconds: 5), () {
        if (_stage == FocusStage.thoughtSaved) _stage = FocusStage.active;
        _notify();
      });
      await _persistRun();
      _notify();
    } catch (error) {
      _isRecording = false;
      _recordingPath = null;
      try {
        await recorder.delete(path);
      } catch (_) {}
      _lastError = error.toString();
      await _recordingFailed();
    }
  }

  Future<void> cancelRecording() async {
    await recorder.cancel();
    final path = _recordingPath;
    if (path != null) await recorder.delete(path);
    _recordingPath = null;
    _isRecording = false;
    _notify();
  }

  Future<void> _recordingFailed() async {
    _isRecording = false;
    _recordingPath = null;
    _recordingAttempts++;
    _stage = _recordingAttempts >= 3
        ? FocusStage.textFallback
        : FocusStage.saveFailed;
    await _persistRun();
    _notify();
  }

  Future<void> retryRecording() async {
    if (_recordingAttempts >= 3) {
      _setStage(FocusStage.textFallback);
      return;
    }
    _stage = FocusStage.active;
    await beginRecording();
  }

  Future<void> saveTextThought(String text) async {
    final normalized = text.trim();
    if (normalized.isEmpty) return;
    final thought = ThoughtRecord(
      id: _clock.now().microsecondsSinceEpoch.toString(),
      createdAt: _clock.now(),
      text: normalized,
      isPendingSync: true,
    );
    await _saveThought(thought);
    _canUndoThought = true;
    _stage = FocusStage.thoughtSaved;
    _undoExpiry?.cancel();
    _toastExpiry?.cancel();
    _undoExpiry = Timer(const Duration(seconds: 5), () {
      _canUndoThought = false;
      _notify();
      unawaited(flushOutbox());
    });
    _toastExpiry = Timer(const Duration(seconds: 5), () {
      if (_stage == FocusStage.thoughtSaved) _stage = FocusStage.active;
      _notify();
    });
    await _persistRun();
    _notify();
  }

  Future<void> undoLastThought() async {
    if (!_canUndoThought || _thoughts.isEmpty) return;
    _undoExpiry?.cancel();
    final thought = _thoughts.removeAt(0);
    if (thought.audioPath != null) await recorder.delete(thought.audioPath!);
    await _repository.deleteThought(thought.id);
    _canUndoThought = false;
    if (_stage == FocusStage.thoughtSaved) _stage = FocusStage.active;
    await _persistRun();
    _notify();
  }

  Future<void> _saveThought(ThoughtRecord thought) async {
    _thoughts.insert(0, thought);
    await _repository.queueThought(thought);
  }

  Future<void> deleteThought(ThoughtRecord thought) async {
    _thoughts.removeWhere((item) => item.id == thought.id);
    if (thought.audioPath != null) await recorder.delete(thought.audioPath!);
    await _repository.deleteThought(thought.id);
    _notify();
  }

  Future<void> _loadThoughts() async {
    final loaded = await _repository.loadThoughts();
    _thoughts
      ..clear()
      ..addAll(loaded);
  }

  /// Đồng bộ hàng đợi Outbox lên Backend khi có kết nối mạng:
  /// Áp dụng cơ chế Idempotency dựa trên `thought.id` để không bao giờ tạo trùng trên server.
  Future<void> flushOutbox() async {
    if (_isOffline) return;
    final pending = _thoughts.where((t) => t.isPendingSync).toList();
    for (final thought in pending) {
      try {
        await _api.postData(
          '/thoughts',
          data: {
            'client_thought_id': thought.id,
            'created_at': thought.createdAt.toIso8601String(),
            'text': thought.text,
            'audio_path': thought.audioPath,
          },
          idempotencyKey: thought.id,
        );
        await _repository.markThoughtSynced(thought.id);
        final index = _thoughts.indexWhere((t) => t.id == thought.id);
        if (index >= 0) {
          final old = _thoughts[index];
          _thoughts[index] = ThoughtRecord(
            id: old.id,
            createdAt: old.createdAt,
            audioPath: old.audioPath,
            text: old.text,
            isPendingSync: false,
          );
        }
      } catch (_) {
        // Mạng gián đoạn hoặc server bận: giữ nguyên isPendingSync để thử lại lần sau.
      }
    }
    _notify();
  }

  Future<void> _persistRun() async {
    final session = _session;
    if (session == null || !isActiveRun) return;
    await _repository.saveActiveRun({
      'session': session.toJson(),
      'segments': _segments.map((segment) => segment.toJson()).toList(),
      'segment_index': _segmentIndex,
      'actual_work_seconds': _actualWorkSeconds,
      'stage': _stage.name,
      'timer_deadline': timer.endsAt?.toIso8601String(),
      'timer_duration': timer.durationSeconds,
      'dnd_snapshot': _dndSnapshot?.toJson(),
    });
  }

  void _startOfflineProbe() {
    _offlineProbe?.cancel();
    unawaited(_probeBackend());
    _offlineProbe = Timer.periodic(const Duration(seconds: 12), (_) {
      unawaited(_probeBackend());
    });
  }

  Future<void> _probeBackend() async {
    try {
      await _api.dio.get<Map<String, dynamic>>('/capabilities');
      if (_isOffline) {
        _isOffline = false;
        unawaited(flushOutbox());
        _notify();
      }
    } catch (_) {
      if (!_isOffline) {
        _isOffline = true;
        _notify();
      }
    }
  }

  void _stopOfflineProbe() {
    _offlineProbe?.cancel();
    _offlineProbe = null;
  }

  Future<void> _restoreFocusMode() async {
    try {
      await dnd.restore(_dndSnapshot);
    } catch (_) {}
    try {
      await WakelockPlus.disable();
    } catch (_) {}
    _dndSnapshot = null;
    _dndApplied = false;
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
    _stopOfflineProbe();
    _undoExpiry?.cancel();
    _toastExpiry?.cancel();
    timer.removeListener(_onTimerChanged);
    microTaskTimer.removeListener(_onTimerChanged);
    timer.dispose();
    microTaskTimer.dispose();
    unawaited(recorder.dispose());
    super.dispose();
  }
}
