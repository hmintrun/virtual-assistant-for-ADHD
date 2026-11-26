import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../core/api_client.dart';
import '../core/api_session.dart';
import '../models/focus_session_model.dart';
import '../models/outbox_record.dart';
import '../models/task_model.dart';
import '../models/planning_models.dart';
import '../models/thought_model.dart';
import '../repositories/local_database_manager.dart';
import '../repositories/local_outbox_repository.dart';
import '../repositories/local_run_repository.dart';
import '../repositories/local_schedule_repository.dart';
import '../repositories/local_thought_repository.dart';
import '../services/audio_recorder_service.dart';
import '../services/alarm_service.dart';
import '../services/clock_sync_service.dart';
import '../services/dnd_service.dart';
import '../services/focus_native_controller.dart';
import '../services/outbox_replay_worker.dart';
import '../services/reminder_api.dart';
import '../services/timer_service.dart';
import '../services/voice_playback_service.dart';
import '../services/voice_service.dart';
import '../services/voice_two_tier_service.dart';

/// Interface cho phép kiểm tra và khôi phục trạng thái native khi mở lại app.
abstract class NativeRecoveryChecker {
  Future<FocusRecovery?> recoverOnAppStart();

  factory NativeRecoveryChecker.fromCallback(
    Future<FocusRecovery?> Function() callback,
  ) = _CallbackNativeRecoveryChecker;
}

class _CallbackNativeRecoveryChecker implements NativeRecoveryChecker {
  const _CallbackNativeRecoveryChecker(this._callback);
  final Future<FocusRecovery?> Function() _callback;

  @override
  Future<FocusRecovery?> recoverOnAppStart() => _callback();
}

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
    String? serverRunId,
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
        serverRunId: serverRunId ?? this.serverRunId,
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
    LocalOutboxRepository? outboxRepository,
    LocalScheduleRepository? scheduleRepository,
    LocalThoughtRepository? thoughtRepository,
    OutboxReplayWorker? outboxWorker,
    ClockSyncService? clockSync,
    NativeRecoveryChecker? nativeRecoveryChecker,
    ReminderApi? reminderApi,
    VoicePlaybackService? voicePlayback,
    String? ownerId,
    ValueChanged<int>? onFocusRecorded,
  })  : _clock = clock ?? const SystemClock(),
        timer = timer ?? FocusTimerService(clock: clock),
        microTaskTimer = microTaskTimer ?? FocusTimerService(clock: clock),
        recorder = recorder ?? AudioRecorderService.instance,
        dnd = dnd ?? DndService.instance,
        _api = api ?? ApiClient(),
        _repository = repository ??
            (storage != null
                ? SecureStorageRunRepository(storage: storage)
                : SecureStorageRunRepository()),
        _outboxRepository = outboxRepository,
        _scheduleRepository = scheduleRepository,
        _thoughtRepository = thoughtRepository,
        _outboxWorker = outboxWorker,
        _clockSync = clockSync,
        _nativeRecoveryChecker = nativeRecoveryChecker,
        _voicePlayback = voicePlayback,
        _onFocusRecorded = onFocusRecorded,
        _ownerId = ownerId ?? ApiSession.current?.ownerId ?? 'default' {
    _reminderApi = reminderApi ?? ReminderApi(_api);
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

  /// Factory DI khởi tạo FocusProvider kết nối đầy đủ các thành phần ngoại tuyến và SQLite.
  static Future<FocusProvider> createOfflineAware({
    required String ownerId,
    required ApiClient apiClient,
    NativeRecoveryChecker? nativeRecoveryChecker,
    VoicePlaybackService? voicePlayback,
    ValueChanged<int>? onFocusRecorded,
  }) async {
    final dbManager = LocalDatabaseManager.instance;
    final db = await dbManager.openUserDatabase(ownerId);
    final outboxRepo = SqliteOutboxRepository(db: db);
    final scheduleRepo = SqliteScheduleRepository(db: db);
    final thoughtRepo = SqliteThoughtRepository(db: db);
    final clockSync = ClockSyncService.instance;
    void Function(OutboxRecord)? onSyncedCallback;
    final worker = OutboxReplayWorker(
      repository: outboxRepo,
      apiClient: apiClient,
      thoughtRepository: thoughtRepo,
      onRecordSynced: (record) => onSyncedCallback?.call(record),
    );

    final provider = FocusProvider(
      ownerId: ownerId,
      api: apiClient,
      outboxRepository: outboxRepo,
      scheduleRepository: scheduleRepo,
      thoughtRepository: thoughtRepo,
      outboxWorker: worker,
      clockSync: clockSync,
      nativeRecoveryChecker: nativeRecoveryChecker,
      voicePlayback: voicePlayback,
      onFocusRecorded: onFocusRecorded,
    );
    onSyncedCallback = (record) {
      if (record.eventType == OutboxEventType.runEventBatch ||
          record.eventType == OutboxEventType.checkinResponse) {
        provider.markScheduleSynced();
      }
    };
    return provider;
  }

  late final void Function(ThoughtRecording) _recordingStopped;
  late final void Function(RecorderException) _recordingFailedCallback;
  final String _ownerId;
  final ValueChanged<int>? _onFocusRecorded;
  final Clock _clock;
  final FocusTimerService timer;
  final FocusTimerService microTaskTimer;
  final AudioRecorderService recorder;
  final DndService dnd;
  final ApiClient _api;
  final LocalRunRepository _repository;
  final LocalOutboxRepository? _outboxRepository;
  final LocalScheduleRepository? _scheduleRepository;
  final LocalThoughtRepository? _thoughtRepository;
  final OutboxReplayWorker? _outboxWorker;
  final ClockSyncService? _clockSync;
  final NativeRecoveryChecker? _nativeRecoveryChecker;
  VoicePlaybackService? _voicePlayback;
  late final ReminderApi _reminderApi;
  final List<ThoughtRecord> _thoughts = [];
  List<Map<String, dynamic>> _upcomingReminders = const [];

  bool get hasVoicePlayback => _voicePlayback != null;
  VoicePlaybackService get voicePlayback => _voicePlayback ??= VoicePlaybackService(
        api: _api,
        cache: kIsWeb ? MemoryTtsAudioCache() : null,
      );

  Future<void> stopVoicePlayback() async {
    final voice = _voicePlayback;
    if (voice != null) await voice.stop();
  }

  String get ownerId => _ownerId;
  List<Map<String, dynamic>> get upcomingReminders =>
      List.unmodifiable(_upcomingReminders);
  LocalOutboxRepository? get outboxRepository => _outboxRepository;
  LocalScheduleRepository? get scheduleRepository => _scheduleRepository;
  LocalThoughtRepository? get thoughtRepository => _thoughtRepository;
  OutboxReplayWorker? get outboxWorker => _outboxWorker;
  ClockSyncService? get clockSync => _clockSync;
  NativeRecoveryChecker? get nativeRecoveryChecker => _nativeRecoveryChecker;

  bool _awaitingCheckIn = false;
  bool _checkInExpired = false;
  int _scheduleSyncRevision = 0;

  bool get awaitingCheckIn => _awaitingCheckIn;
  bool get checkInExpired => _checkInExpired;
  int get scheduleSyncRevision => _scheduleSyncRevision;

  void markScheduleSynced() {
    if (_disposed) return;
    _scheduleSyncRevision++;
    _notify();
  }

  void clearCheckInExpiredBanner() {
    _checkInExpired = false;
    _notify();
  }

  FocusSessionInfo? _session;
  DateTime? _runEndsAt;
  DateTime? _lastRunEndsAt;
  String? _handoffFromRunId;
  Map<String, dynamic>? _serverRun;
  Map<String, dynamic>? _pendingEnd;
  Map<String, dynamic>? _pendingResponse;
  DateTime? _skipUndoUntil;
  Timer? _skipSendTimer;

  bool get canUndoSkip =>
      _skipUndoUntil?.isAfter(_clock.now()) == true &&
      _pendingResponse != null &&
      !_runCommandBusy;

  Future<void> queueSkippedCheckIn(String scope) async {
    final checkIn = _checkIn;
    if (checkIn == null ||
        !canAnswerCheckIn ||
        _pendingResponse != null ||
        _runCommandBusy) return;
    _skipUndoUntil = _clock.now().add(const Duration(seconds: 5));
    _runCommandBusy = true;
    _pendingResponse = {
      'expected_response_revision': checkIn['response_revision'],
      'responded_at': _clock.now().toUtc().toIso8601String(),
      'session_outcome': 'skipped',
      'skip_scope': scope,
    };
    try {
      await _persistRun();
      _scheduleSkippedCheckIn();
    } catch (_) {
      _skipUndoUntil = null;
      _pendingResponse = null;
      _runError = 'Chưa lưu được lựa chọn. Hãy thử lại.';
    } finally {
      _runCommandBusy = false;
      _notify();
    }
  }

  Future<void> undoSkippedCheckIn() async {
    if (!canUndoSkip) return;
    final pending = _pendingResponse;
    final deadline = _skipUndoUntil;
    _skipSendTimer?.cancel();
    _runCommandBusy = true;
    _pendingResponse = null;
    _skipUndoUntil = null;
    try {
      await _persistRun();
    } catch (_) {
      _pendingResponse = pending;
      _skipUndoUntil = deadline;
      _scheduleSkippedCheckIn();
    } finally {
      _runCommandBusy = false;
      _notify();
    }
  }

  void _scheduleSkippedCheckIn() {
    final deadline = _skipUndoUntil;
    if (_disposed || deadline == null || _pendingResponse == null) return;
    _skipSendTimer?.cancel();
    _skipSendTimer = Timer(deadline.difference(_clock.now()), () async {
      await retryCheckInResponse();
    });
  }

  Map<String, dynamic>? _startRequest;
  bool _runCommandBusy = false;
  String? _runError;

  bool get runCommandBusy => _runCommandBusy;
  String? get runError => _runError;
  bool get startRequiresReplan => _runError != null && _startRequiresReplan;
  bool _startRequiresReplan = false;
  bool get supportsDnd => _usesNativeFocus;
  bool get needsEndSync => _pendingEnd != null;
  String? get serverRunId => _serverRun?['run_id'] as String?;
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
  Timer? _checkInExpiryTicker;
  int _offlineProbeGeneration = 0;
  Timer? _undoExpiry;
  Timer? _deleteUndoExpiry;
  Timer? _toastExpiry;
  bool _isFlushingOutbox = false;
  bool _isLoadingThoughts = false;
  final Set<String> _undoneThoughtIds = {};
  ThoughtRecord? _pendingDeletedThought;
  bool _disposed = false;
  static const _deviceStateChannel = MethodChannel('proplan/device_state');
  Future<void> _deviceReports = Future<void>.value();
  Future<String>? _deviceId;

  Future<String> _getDeviceId() => _deviceId ??= _loadDeviceId();

  Future<String> _loadDeviceId() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final stored = prefs.getString('focus_device_id');
      if (stored != null) return stored;
      final device = ApiClient.newIdempotencyKey('device');
      await prefs.setString('focus_device_id', device);
      return device;
    } catch (_) {
      return ApiClient.newIdempotencyKey('device');
    }
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

  Future<FocusRecovery?> _safeNativeRecover() async {
    try {
      return await FocusNativeController.instance.recoverOnAppStart();
    } catch (_) {
      return null;
    }
  }
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
    if (_outboxWorker != null) {
      unawaited(_outboxWorker.triggerReplay());
    }
    final nativeRecovery = _nativeRecoveryChecker != null
        ? await _nativeRecoveryChecker.recoverOnAppStart()
        : (_usesNativeFocus
            ? await _safeNativeRecover()
            : null);
    final json = await _repository.loadActiveRun();
    if (json == null) {
      if (nativeRecovery?.state == FocusRecoveryState.checkInExpired) {
        _stage = FocusStage.ended;
        _checkInExpired = true;
        _awaitingCheckIn = false;
        _notify();
        return;
      } else if (nativeRecovery?.state == FocusRecoveryState.awaitingCheckIn) {
        _stage = FocusStage.ended;
        _awaitingCheckIn = true;
        _checkInExpired = false;
        _notify();
        return;
      }
      if (_thoughts.any((thought) => thought.isPendingSync)) {
        _ensureOfflineProbe();
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
      _skipUndoUntil =
          DateTime.tryParse(json['skip_undo_until'] as String? ?? '');
      _scheduleSkippedCheckIn();
      if (_skipUndoUntil == null &&
          _pendingResponse?['session_outcome'] == 'skipped') {
        _skipSendTimer = Timer(Duration.zero, retryCheckInResponse);
      }
      _actualWorkSeconds = json['actual_work_seconds'] as int? ?? 0;
      final nowUtc = _clockSync?.nowAdjusted() ?? _clock.now().toUtc();

      if (json['stage'] == FocusStage.ended.name) {
        _stage = FocusStage.ended;
        final runEndsAt = DateTime.tryParse(json['run_ends_at'] as String? ?? '');
        _lastRunEndsAt = runEndsAt;
        final isNativeExpired = nativeRecovery?.state == FocusRecoveryState.checkInExpired;
        final secondsSinceEnd = runEndsAt != null
            ? nowUtc.difference(runEndsAt.toUtc()).inSeconds
            : null;
        if (isNativeExpired || (secondsSinceEnd != null && secondsSinceEnd >= 300)) {
          _checkInExpired = true;
          _awaitingCheckIn = false;
          _enqueueExpiredNoResponseOutbox();
          await _repository.clearActiveRun();
          await _restoreFocusMode();
        } else {
          _awaitingCheckIn = _pendingResponse == null && (_checkIn == null || canAnswerCheckIn);
          _checkInExpired = false;
          _watchCheckInDeadline();
          if (needsEndSync || hasPendingCheckInResponse) _startOfflineProbe();
        }
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
      final deadline =
          DateTime.tryParse(json['timer_deadline'] as String? ?? '');
      final duration = json['timer_duration'] as int? ?? 0;
      if (_runEndsAt == null && deadline != null) {
        final secondsAfterCurrent = _segments
            .skip(_segmentIndex + 1)
            .fold<int>(0, (total, segment) => total + segment.durationSeconds);
        _runEndsAt = deadline.add(Duration(seconds: secondsAfterCurrent));
      }

      final runEndUtc = _runEndsAt?.toUtc();
      final isNativeExpired = nativeRecovery?.state == FocusRecoveryState.checkInExpired;
      final isNativeAwaiting = nativeRecovery?.state == FocusRecoveryState.awaitingCheckIn;

      final bool hasRunEnded = (runEndUtc != null && !nowUtc.isBefore(runEndUtc)) ||
          isNativeExpired ||
          isNativeAwaiting;

      if (hasRunEnded) {
        final secondsSinceEnd = runEndUtc != null
            ? nowUtc.difference(runEndUtc).inSeconds
            : (isNativeExpired ? 301 : 0);

        final bool isExpired = secondsSinceEnd >= 300 || isNativeExpired;

        if (isExpired) {
          // HẾT HẠN 5 PHÚT -> Dọn dẹp phiên, tắt DND & Wakelock, không vào Check-in
          _stage = FocusStage.ended;
          _checkInExpired = true;
          _awaitingCheckIn = false;
          _dndApplied = false;
          _enqueueExpiredNoResponseOutbox();
          await _repository.clearActiveRun();
          await _restoreFocusMode();
          _notify();
          return;
        } else {
          // TRONG CỬA SỔ 5 PHÚT (< 300s) -> Vào Check-in UF5
          _actualWorkSeconds = _segments
              .where((segment) => segment.kind == 'work')
              .fold(0, (total, segment) => total + segment.durationSeconds);
          _segmentIndex = _segments.length;
          _stage = FocusStage.ended;
          _awaitingCheckIn = true;
          _checkInExpired = false;
          await _finishLocalRun(awaitCheckIn: true);
          _notify();
          return;
        }
      }

      _resumeStage = FocusStage.values.firstWhere(
        (stage) => stage.name == json['stage'],
        orElse: () => FocusStage.active,
      );
      try {
        _dndApplied = supportsDnd && await dnd.isAppliedFor(_focusOwnerId);
      } catch (_) {
        _dndApplied = false;
      }
      _dndApplyState = _dndApplied ? 'applied' : 'not_applied';
      _dndRestoreState = _dndApplied ? 'pending' : 'not_needed';
      _stage = _resumeStage;

      if (deadline != null && duration > 0) {
        final remainingMs = deadline.difference(_clock.now()).inMilliseconds;
        if (remainingMs <= 0) {
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

  void _enqueueExpiredNoResponseOutbox() {
    final run = _serverRun;
    final repo = _outboxRepository;
    if (run == null || repo == null) return;
    final runId = run['run_id'] as String?;
    if (runId == null) return;

    final nowUtc = _clockSync?.nowAdjusted() ?? _clock.now().toUtc();
    final clientEventId = (_pendingEnd?['event_id'] as String?) ??
        ApiClient.newIdempotencyKey('end');
    final endedAt = _pendingEnd?['ended_at'] as String? ??
        (_runEndsAt?.toUtc() ?? nowUtc).toIso8601String();
    final totalWork = _actualWorkSeconds > 0
        ? _actualWorkSeconds
        : _segments
            .where((segment) => segment.kind == 'work')
            .fold<int>(0, (total, segment) => total + segment.durationSeconds);

    final payload = _pendingEnd ??
        {
          'device_id': run['device_id'] ?? 'device_offline',
          'expected_run_revision': run['run_revision'] ?? 1,
          'event_id': clientEventId,
          'ended_at': endedAt,
          'end_reason': 'timer_elapsed',
          'work_elapsed_seconds': totalWork,
          'rest_elapsed_seconds': 0,
        };

    final record = OutboxRecord(
      clientEventId: clientEventId,
      eventType: OutboxEventType.runEventBatch,
      targetResourceId: runId,
      endpoint: '/runs/$runId/end',
      httpMethod: 'POST',
      payload: payload,
      occurredAt: nowUtc,
      holdUntil: nowUtc,
    );
    unawaited(repo.enqueueOutboxRecord(record));
  }

  Future<void> startSession(
    FocusSessionInfo session, {
    bool continueImmediately = false,
  }) async {
    if (isActiveRun || hasUnfinishedServerRun || _runCommandBusy) return;
    _handoffFromRunId = continueImmediately ? _focusOwnerId : null;
    _checkInExpiryTicker?.cancel();
    _checkInExpiryTicker = null;
    _lastRunEndsAt = null;
    unawaited(stopVoicePlayback().catchError((_) {}));
    microTaskTimer.stop();
    _checkInDeadline?.cancel();
    _serverRun = null;
    _startRequest = null;
    _runError = null;
    _awaitingCheckIn = false;
    _checkInExpired = false;
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
    if (!continueImmediately) {
      _dndApplyState = 'not_applied';
      _dndRestoreState = 'not_needed';
    }
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
    _onFocusRecorded?.call(120);
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
    _startRequiresReplan = false;
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
        if (error.statusCode == 409 || error.code == 'ACTIVE_RUN_EXISTS') {
          try {
            final activeRunData = await _api.getData('/runs/active');
            if (activeRunData['session_id'] == session.sessionId) {
              _serverRun = activeRunData;
              if (activeRunData['planned_ends_at'] != null) {
                _runEndsAt = DateTime.parse(activeRunData['planned_ends_at'] as String).toUtc();
              }
              _startRequest = null;
              await _beginLocalRun();
              if (activeRunData['planned_ends_at'] != null) {
                _runEndsAt = DateTime.parse(activeRunData['planned_ends_at'] as String).toUtc();
              }
              return;
            }
          } catch (_) {
            // Nếu gọi active run thất bại, giữ nguyên xử lý lỗi cũ
          }
        }
        if (error.statusCode != null) {
          _startRequest = null;
          rethrow;
        }
        // Lỗi kết nối ngoại tuyến: Khóa chính sách offline_start_supported = false
        // Kiểm tra xem đã có receipt server_run từ trước hay chưa
        if (_serverRun == null) {
          _isOffline = true;
          throw const ApiException(
            'Cần có kết nối mạng để bắt đầu phiên tập trung.',
            code: 'OFFLINE_START_NOT_SUPPORTED',
          );
        }
        _isOffline = true;
        _runError =
            'Phiên đang chạy trên thiết bị. Chưa xác nhận được với máy chủ.';
      }
      await _beginLocalRun();
    } on ApiException catch (error) {
      _startRequiresReplan = error.code == 'START_REQUIRES_REPLAN';
      _runError = error.message;
    } finally {
      _runCommandBusy = false;
      _notify();
    }
  }

  Future<void> _beginLocalRun() async {
    _lastError = null;
    await _refreshDndPermission();
    final phases = [
      for (final segment in _segments.skip(_firstWorkIndex))
        FocusPhase(kind: segment.kind, durationSeconds: segment.durationSeconds),
    ];
    final previousRunId = _handoffFromRunId;
    _handoffFromRunId = null;
    if (_usesNativeFocus && _session != null) {
      unawaited(
        AlarmService.instance
            .cancelPreSessionRemindersFor(_session!.sessionId)
            .catchError((_) {}),
      );
      final totalSeconds = _segments
          .skip(_firstWorkIndex)
          .fold<int>(0, (total, segment) => total + segment.durationSeconds);
      _runEndsAt ??= _clock.now().add(Duration(seconds: totalSeconds));
      try {
        try {
          await AlarmService.instance.requestPermissions();
        } catch (_) {
          // Timer vẫn chạy nếu alarm permission chưa được cấp.
        }
        final FocusStartResult result;
        if (previousRunId != null && previousRunId != _focusOwnerId) {
          result = await FocusNativeController.instance.continueToNextRun(
            previousRunId: previousRunId,
            runId: _focusOwnerId!,
            duration: Duration(seconds: totalSeconds),
            stepTitle: _session!.stepTitle,
            phases: phases,
          );
        } else {
          result = await FocusNativeController.instance.start(
            runId: _focusOwnerId!,
            duration: Duration(seconds: totalSeconds),
            stepTitle: _session!.stepTitle,
            phases: phases,
          );
        }
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
      _runEndsAt ??= _clock.now().add(Duration(seconds: totalSeconds));
      _dndApplied = false;
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
    if (_serverRun != null &&
        _session != null &&
        (_usesNativeFocus || kIsWeb || _voicePlayback != null)) {
      unawaited(
        voicePlayback
            .prefetchCheckInPrompt(
              kind: 'final',
              isFinalSessionOfStep: _session!.isLastSessionOfStep,
              expectedOutput: _session!.expectedOutput,
            )
            .catchError((_) => false),
      );
    }
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
    _lastRunEndsAt = awaitCheckIn ? endedAt : null;
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
    if (awaitCheckIn && _pendingResponse == null) {
      _watchCheckInDeadline();
    }
    if (_thoughts.any((thought) => thought.isPendingSync)) {
      _ensureOfflineProbe();
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
      _scheduleSyncRevision++;
      await _persistRun();
      _watchCheckInDeadline();
    } on ApiException catch (error) {
      _runError = error.message;
      if (error.statusCode == null) _ensureOfflineProbe();
    } finally {
      _runCommandBusy = false;
      _notify();
    }
    if (!needsEndSync) await refreshRunCheckIn();
  }

  Future<void> restAfterCheckIn() async {
    if (_stage != FocusStage.ended || _runCommandBusy) return;
    _checkInDeadline?.cancel();
    _checkInExpiryTicker?.cancel();
    _checkInExpiryTicker = null;
    await _restoreFocusMode();
    _notify();
  }

  DateTime? _effectiveCheckInDeadline() {
    final raw = _serverRun?['checkin_timeout_at'] as String?;
    final serverDeadline = raw == null ? null : DateTime.tryParse(raw)?.toLocal();
    if (serverDeadline != null) return serverDeadline;
    return _lastRunEndsAt?.add(FocusNativeController.checkInDndTimeout);
  }

  void _watchCheckInDeadline() {
    _checkInDeadline?.cancel();
    _checkInExpiryTicker?.cancel();
    _checkInExpiryTicker = null;
    if (_disposed || _stage != FocusStage.ended || (!hasOpenCheckIn && !_awaitingCheckIn)) {
      return;
    }
    final deadline = _effectiveCheckInDeadline();
    if (deadline == null) return;
    final remaining = deadline.difference(_clock.now());
    if (remaining <= Duration.zero) {
      unawaited(checkCheckInExpiry());
      return;
    }
    _checkInDeadline = Timer(
      remaining + const Duration(seconds: 1),
      () => unawaited(checkCheckInExpiry()),
    );
    _checkInExpiryTicker = Timer.periodic(
      const Duration(seconds: 15),
      (_) => unawaited(checkCheckInExpiry()),
    );
  }

  /// Kiểm tra quá hạn Check-in (ended_at + 300s) khi app đang mở ở màn Check-in:
  /// giải phóng DND + Wakelock và ghi nhận `no_response` (KHÔNG tự hoàn thành).
  Future<bool> checkCheckInExpiry() async {
    if (_disposed || _stage != FocusStage.ended) return false;
    var expired = false;
    final ownerId = _focusOwnerId;
    if (_usesNativeFocus && ownerId != null) {
      try {
        expired = await FocusNativeController.instance.releaseIfCheckInExpired(
          runId: ownerId,
          now: _clock.now(),
        );
      } catch (_) {}
    }
    final deadline = _effectiveCheckInDeadline();
    if (!expired && deadline != null && !_clock.now().isBefore(deadline)) {
      expired = true;
    }
    if (!expired || _disposed) return false;

    _checkInDeadline?.cancel();
    _checkInExpiryTicker?.cancel();
    _checkInExpiryTicker = null;
    _checkInExpired = true;
    _awaitingCheckIn = false;
    _dndApplied = false;

    if (!needsEndSync && _serverRun != null) {
      await refreshRunCheckIn();
    }
    if (_disposed) return true;

    final run = _serverRun;
    if (run != null && hasOpenCheckIn) {
      final checkIn = Map<String, dynamic>.from(run['check_in'] as Map);
      checkIn['response_state'] = 'no_respond';
      checkIn['status'] = 'no_response';
      _serverRun = {...run, 'check_in': checkIn};
    }
    if (needsEndSync) {
      _enqueueExpiredNoResponseOutbox();
    }
    await _restoreFocusMode();
    await _repository.clearActiveRun();
    _notify();
    return true;
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
    if (pending == null || canUndoSkip) return;
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
    bool preserveFocusMode = false,
  }) async {
    final checkIn = _checkIn;
    if (checkIn == null || !canAnswerCheckIn || _runCommandBusy || canUndoSkip)
      return;
    _skipUndoUntil = null;
    _checkInExpiryTicker?.cancel();
    _checkInExpiryTicker = null;
    _runCommandBusy = true;
    _runError = null;
    _notify();
    try {
      final respondedAt = _clockSync?.nowAdjustedIso() ??
          _clock.now().toUtc().toIso8601String();
      final pending = _pendingResponse ??= {
        'expected_response_revision': checkIn['response_revision'],
        'responded_at': respondedAt,
        'session_outcome': outcome,
        if (skipScope != null) 'skip_scope': skipScope,
        if (stepCompleted != null) 'step_completed': stepCompleted,
        if (remainingNote?.trim().isNotEmpty == true)
          'remaining_note': remainingNote!.trim(),
      };
      await _persistRun();
      if (!preserveFocusMode) {
        await _restoreFocusMode();
      }
      if (_outboxRepository != null) {
        final clientEventId = ApiClient.stableIdempotencyKey(
            'checkin_${checkIn['checkin_id']}', pending.toString());
        await _outboxRepository.enqueueOutboxRecord(
          OutboxRecord(
            clientEventId: clientEventId,
            eventType: OutboxEventType.checkinResponse,
            targetResourceId: checkIn['checkin_id'] as String?,
            endpoint: '/check-ins/${checkIn['checkin_id']}/response',
            httpMethod: 'PUT',
            payload: pending,
            occurredAt: _clock.now(),
            holdUntil: _clock.now().add(const Duration(seconds: 5)),
          ),
          holdDuration: const Duration(seconds: 5),
        );
        Timer(const Duration(milliseconds: 5200), () {
          _outboxWorker?.triggerReplay();
        });
        _checkInDeadline?.cancel();
        _pendingResponse = null;
        _awaitingCheckIn = false;
        await _repository.clearActiveRun();
      } else {
        final response = await _api.putData(
          '/check-ins/${checkIn['checkin_id']}/response',
          data: pending,
          idempotencyKey: ApiClient.stableIdempotencyKey(
              'checkin_${checkIn['checkin_id']}', pending.toString()),
        );
        _serverRun = {..._serverRun!, 'check_in': response};
        _checkInDeadline?.cancel();
        _pendingResponse = null;
        _awaitingCheckIn = false;
        _scheduleSyncRevision++;
        await _repository.clearActiveRun();
      }
    } on ApiException catch (error) {
      final status = error.statusCode;
      if (status != null &&
          status >= 400 &&
          status < 500 &&
          status != 401 &&
          status != 408 &&
          status != 429) {
        _pendingResponse = null;
        await _persistRun();
      }
      _runError = error.message;
      if (_pendingResponse != null) _ensureOfflineProbe();
    } finally {
      _runCommandBusy = false;
      _notify();
    }
    if (_runError == null &&
        outcome == 'completed' &&
        _actualWorkSeconds >= 120) {
      _onFocusRecorded?.call(_actualWorkSeconds);
    }
    if (_runError != null) await refreshRunCheckIn();
  }

  /// Hiệu chỉnh thời gian làm thực tế cho phiên đã chạy (`POST /runs/{run_id}/actual-corrections`).
  Future<bool> submitActualCorrection({
    required int correctedWorkMinutes,
    required String reason,
  }) async {
    final runId = serverRunId;
    final trimmedReason = reason.trim();
    if (runId == null ||
        correctedWorkMinutes < 0 ||
        trimmedReason.isEmpty ||
        _runCommandBusy) {
      return false;
    }
    _runCommandBusy = true;
    _runError = null;
    _notify();
    try {
      final expectedRevision = (_serverRun?['run_revision'] as int?) ?? 1;
      final selfReportSeconds = correctedWorkMinutes * 60;
      final payload = {
        'expected_run_revision': expectedRevision,
        'self_report_work_seconds': selfReportSeconds,
        'reason': trimmedReason,
        'occurred_at': _clock.now().toUtc().toIso8601String(),
      };
      final response = await _api.postData(
        '/runs/${Uri.encodeComponent(runId)}/actual-corrections',
        data: payload,
        idempotencyKey: ApiClient.stableIdempotencyKey(
          'actual_correction_$runId',
          '$expectedRevision:$selfReportSeconds:$trimmedReason',
        ),
      );
      _actualWorkSeconds =
          (response['actual_work_seconds'] as int?) ?? selfReportSeconds;
      if (_serverRun != null && response['run_revision'] is int) {
        _serverRun = {
          ..._serverRun!,
          'run_revision': response['run_revision'],
        };
      }
      await _persistRun();
      return true;
    } on ApiException catch (error) {
      _runError = error.message;
      return false;
    } finally {
      _runCommandBusy = false;
      _notify();
    }
  }

  /// Đồng bộ danh sách nhắc nhở phiên sắp tới từ máy chủ (`GET /reminders`).
  Future<void> syncUpcomingReminders({int? scheduleRevision}) async {
    if (_disposed) return;
    try {
      final deviceId = await _getDeviceId();
      final items = await _reminderApi.fetchUpcoming(
        deviceId: deviceId,
        scheduleRevision: scheduleRevision,
      );
      if (_disposed) return;
      _upcomingReminders = items;
      _notify();
    } catch (_) {
      // Bỏ qua khi ngoại tuyến để không làm gián đoạn phiên tập trung.
    }
  }

  /// Ghi nhận trạng thái phát nhắc nhở (`POST /reminders/{id}/receipt`).
  Future<void> reportReminderReceipt({
    required String reminderId,
    required int scheduleRevision,
    required String deliveryState,
    String? userAction,
  }) async {
    if (_disposed) return;
    try {
      final deviceId = await _getDeviceId();
      await _reminderApi.reportReceipt(
        reminderId: reminderId,
        deviceId: deviceId,
        scheduleRevision: scheduleRevision,
        deliveryState: deliveryState,
        occurredAt: _clock.now(),
        userAction: userAction,
      );
    } catch (_) {
      // Bỏ qua khi ngoại tuyến.
    }
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
    if (supportsDnd) dnd.openPermissionSettings();
  }

  Future<void> refreshDndPermission() async {
    await _refreshDndPermission();
    _notify();
  }

  Future<void> _refreshDndPermission() async {
    if (!supportsDnd) {
      _dndPermission = false;
      return;
    }
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
    final currentRunId = _serverRun?['run_id'] as String? ?? _focusOwnerId;
    final thought = ThoughtRecord(
      clientThoughtId: now.microsecondsSinceEpoch.toString(),
      createdAt: now,
      undoDeadlineAt: now.add(const Duration(seconds: 5)),
      sourceRunId: currentRunId,
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
    final currentRunId = _serverRun?['run_id'] as String? ?? _focusOwnerId;
    final thought = ThoughtRecord(
      clientThoughtId: now.microsecondsSinceEpoch.toString(),
      createdAt: now,
      undoDeadlineAt: now.add(const Duration(seconds: 5)),
      sourceRunId: currentRunId,
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
    if (_thoughtRepository != null) {
      await _thoughtRepository.purgeTombstone(thought.clientThoughtId);
    }
    if (_outboxRepository != null) {
      await _outboxRepository.cancelOutboxRecord(thought.clientThoughtId);
    }
    if (_stage == FocusStage.thoughtSaved) _stage = _resumeStage;
    _scheduleOutboxFlush();
    await _persistRun();
    _notify();
  }

  Future<void> _saveThought(ThoughtRecord thought) async {
    await _repository.queueThought(thought);
    _thoughts.insert(0, thought);

    if (_thoughtRepository != null) {
      final currentRunId = thought.sourceRunId ??
          _serverRun?['run_id'] as String? ??
          _focusOwnerId;
      final localThought = LocalThought(
        id: thought.clientThoughtId,
        ownerId: _ownerId,
        title: thought.textContent,
        audioPath: thought.localAudioPath ?? '',
        sourceRunId: currentRunId,
        state: LocalThoughtState.rawSaved,
        createdAt: thought.createdAt.toUtc(),
        updatedAt: thought.createdAt.toUtc(),
      );
      await _thoughtRepository.insertThought(localThought);
    }

    if (_outboxRepository != null) {
      await _outboxRepository.enqueueOutboxRecord(
        OutboxRecord(
          clientEventId: thought.clientThoughtId,
          eventType: OutboxEventType.thoughtCapture,
          targetResourceId: thought.clientThoughtId,
          endpoint: '/thoughts',
          httpMethod: 'POST',
          payload: {
            'client_thought_id': thought.clientThoughtId,
            'input_type': thought.inputType,
            if (thought.textContent != null)
              'text_content': thought.textContent,
            if (thought.sourceRunId != null)
              'source_run_id': thought.sourceRunId,
            'attempt_count': thought.attemptCount,
            'capture_state': thought.captureState,
          },
          occurredAt: thought.createdAt,
          holdUntil: thought.undoDeadlineAt,
        ),
      );
    }
  }

  Future<void> deleteThought(dynamic thoughtOrId) async {
    if (_pendingDeletedThought != null) {
      await _finalizePendingThoughtDeletion();
    }
    ThoughtRecord? target;
    if (thoughtOrId is ThoughtRecord) {
      target = thoughtOrId;
    } else if (thoughtOrId is String) {
      final found = _thoughts.where((item) => item.clientThoughtId == thoughtOrId).toList();
      target = found.isNotEmpty
          ? found.first
          : ThoughtRecord(
              clientThoughtId: thoughtOrId,
              createdAt: _clock.now(),
              undoDeadlineAt: _clock.now().add(const Duration(seconds: 5)),
            );
    }
    if (target == null) return;

    final index = _thoughts.indexWhere(
      (item) => item.clientThoughtId == target!.clientThoughtId,
    );
    final removed = index >= 0 ? _thoughts.removeAt(index) : target;
    _pendingDeletedThought = removed;
    _undoneThoughtIds.add(removed.clientThoughtId);

    // Đánh dấu tombstone ngay trong SQLite để ẩn khỏi UI và lọc khi reconcile
    if (_thoughtRepository != null) {
      await _thoughtRepository.markTombstone(
        removed.clientThoughtId,
        asOf: _clock.now(),
        ownerId: _ownerId,
      );
    }

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

    // Khôi phục trạng thái cũ trong SQLite nếu người dùng bấm Undo trong 5s
    String? restoredServerId = thought.serverThoughtId;
    if (_thoughtRepository != null) {
      final existing = await _thoughtRepository.getThought(thought.clientThoughtId);
      if (existing != null) {
        restoredServerId ??= existing.serverThoughtId;
        await _thoughtRepository.insertThought(
          existing.copyWith(
            state: thought.isPendingSync
                ? LocalThoughtState.rawSaved
                : LocalThoughtState.synced,
            updatedAt: _clock.now().toUtc(),
          ),
        );
      } else {
        await _thoughtRepository.insertThought(
          LocalThought(
            id: thought.clientThoughtId,
            ownerId: _ownerId,
            title: thought.textContent,
            audioPath: thought.localAudioPath ?? '',
            sourceRunId: thought.sourceRunId,
            state: thought.isPendingSync
                ? LocalThoughtState.rawSaved
                : LocalThoughtState.synced,
            serverThoughtId: thought.serverThoughtId,
            createdAt: thought.createdAt.toUtc(),
            updatedAt: _clock.now().toUtc(),
          ),
        );
      }
    }

    final restoredThought = restoredServerId != null && thought.serverThoughtId == null
        ? thought.copyWith(serverThoughtId: restoredServerId)
        : thought;
    _thoughts.insert(0, restoredThought);
    await _repository.queueThought(restoredThought);

    _scheduleOutboxFlush();
    _notify();
  }

  Future<void> _finalizePendingThoughtDeletion() async {
    final thought = _pendingDeletedThought;
    _pendingDeletedThought = null;
    _deleteUndoExpiry?.cancel();
    _deleteUndoExpiry = null;
    if (thought == null) return;
    try {
      await _repository.deleteThought(thought.clientThoughtId);
      if (_outboxRepository != null) {
        // Nếu có yêu cầu POST tạo thought đang chờ trong outbox chưa gửi, hủy nó đi
        await _outboxRepository.cancelOutboxRecord(thought.clientThoughtId, force: true);

        // Đẩy yêu cầu DELETE /api/v1/thoughts/{id} vào hàng đợi Outbox
        // Dòng tombstone trong local_thoughts vẫn ĐƯỢC GIỮ NGUYÊN để chống hiện thẻ ma khi GET /thoughts
        // Cho đến khi OutboxReplayWorker gửi thành công nhận HTTP 200 OK mới purge.
        final delEventId = ApiClient.stableIdempotencyKey(
          'del_thought',
          thought.clientThoughtId,
        );
        var deleteTargetId = thought.serverThoughtId;
        if (deleteTargetId == null && _thoughtRepository != null) {
          final localThought = await _thoughtRepository.getThought(thought.clientThoughtId);
          if (localThought?.serverThoughtId != null && localThought!.serverThoughtId!.isNotEmpty) {
            deleteTargetId = localThought.serverThoughtId;
          }
        }
        deleteTargetId ??= thought.clientThoughtId;
        await _outboxRepository.enqueueOutboxRecord(
          OutboxRecord(
            clientEventId: delEventId,
            eventType: OutboxEventType.thoughtDelete,
            targetResourceId: deleteTargetId,
            endpoint: '/thoughts/$deleteTargetId',
            httpMethod: 'DELETE',
            payload: {'expected_thought_revision': 1},
            occurredAt: _clock.now().toUtc(),
            holdUntil: _clock.now().toUtc(),
          ),
        );
        _outboxWorker?.triggerReplay();
      }
      if (thought.localAudioPath != null) {
        try {
          await recorder.deleteRecording(thought.localAudioPath!);
        } catch (_) {}
      }
      _scheduleOutboxFlush();
      _notify();
    } catch (_) {
      // Bẫy lỗi an toàn khi database đang đóng hoặc đã đóng trong quá trình logout / dispose
    }
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
    Set<String> tombstoneIds = const {};
    if (_thoughtRepository != null) {
      tombstoneIds = (await _thoughtRepository.getTombstoneIds(_ownerId)).toSet();
    }
    final pendingDeleteId = _pendingDeletedThought?.clientThoughtId;

    _thoughts
      ..clear()
      ..addAll(loaded.where((t) =>
          !tombstoneIds.contains(t.clientThoughtId) &&
          !_undoneThoughtIds.contains(t.clientThoughtId) &&
          t.clientThoughtId != pendingDeleteId));

    if (_thoughtRepository != null) {
      final activeLocal = await _thoughtRepository.getActiveThoughts(_ownerId);
      for (final lt in activeLocal) {
        if (!_undoneThoughtIds.contains(lt.id) &&
            lt.id != pendingDeleteId &&
            !_thoughts.any((t) => t.clientThoughtId == lt.id)) {
          _thoughts.add(
            ThoughtRecord(
              clientThoughtId: lt.id,
              createdAt: lt.createdAt,
              undoDeadlineAt: lt.createdAt.add(const Duration(seconds: 5)),
              sourceRunId: lt.sourceRunId,
              textContent: lt.title,
              localAudioPath: lt.audioPath.isNotEmpty ? lt.audioPath : null,
              inputType: lt.audioPath.isNotEmpty ? 'audio' : 'text',
              captureState: lt.state == LocalThoughtState.rawSaved ? 'saved' : 'synced',
              isPendingSync: lt.state == LocalThoughtState.rawSaved,
              serverThoughtId: lt.serverThoughtId,
            ),
          );
        }
      }

      // Xử lý ca biên: Đảm bảo các bản ghi tombstone trong SQLite sau sự cố tắt app đột ngột
      // đều đã được đưa vào hàng đợi DELETE của Outbox và hủy lệnh POST tương ứng
      if (_outboxRepository != null) {
        final allThoughts = await _thoughtRepository.getAllThoughts(_ownerId);
        final tombstones = allThoughts.where((t) => t.state == LocalThoughtState.tombstone);
        for (final tb in tombstones) {
          await _outboxRepository.cancelOutboxRecord(tb.id, force: true);
          final delEventId = ApiClient.stableIdempotencyKey('del_thought', tb.id);
          final existing = await _outboxRepository.getRecord(delEventId);
          if (existing != null) continue;
          final deleteTargetId = tb.serverThoughtId ?? tb.id;
          await _outboxRepository.enqueueOutboxRecord(
            OutboxRecord(
              clientEventId: delEventId,
              eventType: OutboxEventType.thoughtDelete,
              targetResourceId: deleteTargetId,
              endpoint: '/thoughts/$deleteTargetId',
              httpMethod: 'DELETE',
              payload: {'expected_thought_revision': 1},
              occurredAt: tb.updatedAt,
              holdUntil: _clock.now().toUtc(),
            ),
          );
        }
      }
    }

    _scheduleOutboxFlush();
  }

  /// Nạp suy nghĩ từ local rồi đồng bộ `GET /thoughts` kèm đối soát Tombstone.
  Future<void> loadThoughts() async {
    if (_disposed || _isLoadingThoughts) return;
    _isLoadingThoughts = true;
    try {
      await _loadThoughts();
      if (_disposed) return;

      final serverItems = <Map<String, dynamic>>[];
      String? cursor;
      var reachedEnd = false;
      for (var page = 0; page < 5; page++) {
        final data = await _api.getData(
          '/thoughts',
          queryParameters: {
            'limit': 100,
            if (cursor != null) 'cursor': cursor,
          },
        );
        final items = data['items'];
        if (items is List) {
          serverItems.addAll(items.whereType<Map<String, dynamic>>());
        }
        final nextCursor = data['next_cursor'] as String?;
        if (nextCursor == null || nextCursor.isEmpty) {
          reachedEnd = true;
          break;
        }
        cursor = nextCursor;
      }
      if (_disposed) return;

      final rawServerThoughtIds = <String>{};
      final rawServerClientIds = <String>{};
      for (final item in serverItems) {
        final serverId = item['thought_id']?.toString();
        final clientId = item['client_thought_id']?.toString();
        if (serverId != null && serverId.isNotEmpty) {
          rawServerThoughtIds.add(serverId);
        }
        if (clientId != null && clientId.isNotEmpty) {
          rawServerClientIds.add(clientId);
        }
      }

      var reconciled = _thoughtRepository != null
          ? await _thoughtRepository.reconcileWithServer(_ownerId, serverItems)
          : serverItems;
      if (_disposed) return;

      final pendingDeleteClientId = _pendingDeletedThought?.clientThoughtId;
      final pendingDeleteServerId = _pendingDeletedThought?.serverThoughtId;
      reconciled = reconciled.where((item) {
        final serverId = item['thought_id']?.toString();
        final clientId = item['client_thought_id']?.toString();
        if (clientId != null &&
            (_undoneThoughtIds.contains(clientId) ||
                clientId == pendingDeleteClientId)) {
          return false;
        }
        if (serverId != null &&
            (_undoneThoughtIds.contains(serverId) ||
                serverId == pendingDeleteServerId)) {
          return false;
        }
        return true;
      }).toList();

      for (final item in reconciled) {
        final serverId = item['thought_id'] as String?;
        final clientId = (item['client_thought_id'] as String?) ?? serverId;
        if (clientId == null || clientId.isEmpty) continue;

        final rawType = item['input_type'] as String?;
        final normalizedType =
            rawType == 'raw_audio' ? 'audio' : (rawType ?? 'text');
        final rawState = item['capture_state'] as String?;
        final normalizedState =
            rawState == 'raw_saved' ? 'saved' : (rawState ?? 'saved');
        final textContent = item['text_content'] as String?;
        final sourceRunId = item['source_run_id'] as String?;
        final attemptCount = (item['attempt_count'] as int?) ?? 1;
        final createdAt =
            DateTime.tryParse(item['created_at'] as String? ?? '') ??
                _clock.now().toUtc();

        final existingIndex = _thoughts.indexWhere(
          (t) =>
              t.clientThoughtId == clientId ||
              (serverId != null && t.serverThoughtId == serverId),
        );
        if (existingIndex >= 0) {
          final existing = _thoughts[existingIndex];
          final updated = existing.copyWith(
            serverThoughtId: serverId ?? existing.serverThoughtId,
            serverRunId: sourceRunId ?? existing.serverRunId,
            isPendingSync: false,
            textContent: existing.textContent ?? textContent,
            captureState: normalizedState,
          );
          _thoughts[existingIndex] = updated;
          if (_thoughtRepository != null && serverId != null) {
            await _thoughtRepository.markSynced(
              existing.clientThoughtId,
              serverThoughtId: serverId,
              asOf: _clock.now(),
            );
          }
        } else {
          final record = ThoughtRecord(
            clientThoughtId: clientId,
            createdAt: createdAt,
            undoDeadlineAt: createdAt,
            sourceRunId: sourceRunId,
            serverRunId: sourceRunId,
            inputType: normalizedType,
            textContent: textContent,
            localAudioPath: null,
            attemptCount: attemptCount,
            captureState: normalizedState,
            isPendingSync: false,
            serverThoughtId: serverId,
          );
          _thoughts.add(record);
          if (_thoughtRepository != null) {
            await _thoughtRepository.insertThought(
              LocalThought(
                id: clientId,
                ownerId: _ownerId,
                title: textContent,
                audioPath: '',
                sourceRunId: sourceRunId,
                state: LocalThoughtState.synced,
                serverThoughtId: serverId,
                createdAt: createdAt.toUtc(),
                updatedAt: _clock.now().toUtc(),
              ),
            );
          }
        }
      }

      if (reachedEnd) {
        final toRemove = _thoughts
            .where((t) =>
                !t.isPendingSync &&
                t.serverThoughtId != null &&
                !rawServerThoughtIds.contains(t.serverThoughtId) &&
                !rawServerClientIds.contains(t.clientThoughtId))
            .toList();
        for (final stale in toRemove) {
          _thoughts.remove(stale);
          if (_thoughtRepository != null) {
            await _thoughtRepository.markTombstone(
              stale.clientThoughtId,
              asOf: _clock.now(),
              ownerId: _ownerId,
            );
            await _thoughtRepository.purgeTombstone(stale.clientThoughtId);
          }
        }
      }

      await _repository.saveThoughts(List<ThoughtRecord>.from(_thoughts));
    } catch (_) {
      // Ngoại tuyến hoặc server tạm bận: giữ nguyên dữ liệu local đã nạp.
    } finally {
      _isLoadingThoughts = false;
      if (!_disposed) _notify();
    }
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
          if (_thoughtRepository != null) {
            await _thoughtRepository.markSynced(thought.clientThoughtId,
                serverThoughtId: serverId);
          }
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

  Future<String> transcribeThought(
    ThoughtRecord thought, {
    TwoTierSttService? stt,
  }) async {
    if (isActiveRun || isRecording || runCommandBusy || needsEndSync) {
      throw const ApiException('Hãy kết thúc phiên trước khi chuyển ghi âm.');
    }
    final path = thought.localAudioPath;
    final id = thought.serverThoughtId;
    if (kIsWeb || path == null || id == null) {
      throw const ApiException(
          'Chưa có ghi âm đã đồng bộ để chuyển thành chữ.');
    }
    final service = stt ??
        TwoTierSttService.fromVoiceService(VoiceTranscriptionService(api: _api));
    final result = await service.transcribeTwoTier(
      path,
      recordingId: thought.clientThoughtId,
      thoughtId: id,
    );
    return result.text;
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
      if (_skipUndoUntil != null)
        'skip_undo_until': _skipUndoUntil!.toUtc().toIso8601String(),
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

  void _ensureOfflineProbe() {
    if (_disposed || _offlineProbe != null) return;
    final generation = _offlineProbeGeneration;
    _offlineProbe = Timer(const Duration(seconds: 15), () {
      unawaited(_probeBackend(generation));
    });
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
    if (!offline) {
      if (_thoughts.any((thought) => thought.isPendingSync)) {
        unawaited(flushOutbox());
      }
      if (_stage == FocusStage.ended) {
        if (needsEndSync) await syncRunEnd();
        if (hasPendingCheckInResponse) await retryCheckInResponse();
      }
    }
    if (_disposed || generation != _offlineProbeGeneration) return;
    if (_stage == FocusStage.ended &&
        !needsEndSync &&
        !hasPendingCheckInResponse &&
        !_thoughts.any((thought) => thought.isPendingSync)) {
      _stopOfflineProbe();
      return;
    }
    _offlineProbe?.cancel();
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
    if (_pendingDeletedThought != null) {
      try {
        unawaited(_finalizePendingThoughtDeletion().catchError((_) {}));
      } catch (_) {}
    }
    _skipSendTimer?.cancel();
    _checkInDeadline?.cancel();
    _checkInExpiryTicker?.cancel();
    _stopOfflineProbe();
    _undoExpiry?.cancel();
    _deleteUndoExpiry?.cancel();
    _toastExpiry?.cancel();
    final voice = _voicePlayback;
    if (voice != null) {
      unawaited(voice.dispose().catchError((_) {}));
    }
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
