import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:speech_to_text/speech_to_text.dart';

import '../core/api_client.dart';
import 'audio_recorder_service.dart';

/// DTO kết quả phiên âm từ Backend (`/api/v1/voice/transcriptions`).
class VoiceTranscriptionResult {
  const VoiceTranscriptionResult({
    required this.text,
    this.inputSource = 'voice',
    this.lowConfidence = false,
    this.thoughtId,
  });

  final String text;
  final String inputSource;
  final bool lowConfidence;
  final String? thoughtId;

  factory VoiceTranscriptionResult.fromJson(Map<String, dynamic> json) {
    return VoiceTranscriptionResult(
      text: (json['text'] as String?)?.trim() ?? '',
      inputSource: json['input_source'] as String? ?? 'voice',
      lowConfidence: json['low_confidence'] == true,
      thoughtId: json['thought_id'] as String?,
    );
  }
}

/// Bộ theo dõi khoảng lặng tất định (Silence Endpointing) cho UF1 Voice Input.
///
/// Chỉ cho phép tự ngắt sau khi người dùng đã thực sự bắt đầu nói
/// ([hasDetectedSpeech] == true) và duy trì mức âm lượng <= [silenceThresholdDb]
/// liên tục trong [silenceTimeout] (mặc định 3.8 giây cho người dùng ADHD-I).
class SilenceEndpointTracker {
  SilenceEndpointTracker({
    this.silenceTimeout = const Duration(milliseconds: 3800),
    this.speechThresholdDb = -40.0,
    this.silenceThresholdDb = -45.0,
  });

  final Duration silenceTimeout;
  final double speechThresholdDb;
  final double silenceThresholdDb;

  bool hasDetectedSpeech = false;
  DateTime? _silenceStartedAt;

  void reset() {
    hasDetectedSpeech = false;
    _silenceStartedAt = null;
  }

  bool onSample(double currentDb, DateTime timestamp) {
    if (currentDb.isNaN || !currentDb.isFinite) {
      return false;
    }
    if (currentDb >= speechThresholdDb) {
      hasDetectedSpeech = true;
      _silenceStartedAt = null;
      return false;
    }
    if (!hasDetectedSpeech) {
      return false;
    }
    if (currentDb <= silenceThresholdDb) {
      _silenceStartedAt ??= timestamp;
      return timestamp.difference(_silenceStartedAt!) >= silenceTimeout;
    }
    _silenceStartedAt = null;
    return false;
  }
}

/// Dịch vụ thu âm chuyên biệt cho UF1 (Tách rời hoàn toàn khỏi UF4 Raw Audio
/// theo RISK-04 để UF4 giữ nguyên `AudioInterruptionMode.none`).
class Uf1VoiceRecorderService {
  Uf1VoiceRecorderService({
    AudioRecorder? recorder,
    Duration silenceTimeout = defaultSilenceTimeout,
    Future<Directory> Function()? tempDirectoryProvider,
  })  : _recorder = recorder ?? AudioRecorder(),
        _silenceTracker = SilenceEndpointTracker(
          silenceTimeout: silenceTimeout,
        ),
        _tempDirectoryProvider =
            tempDirectoryProvider ?? getTemporaryDirectory;

  static const Duration minDuration = Duration(milliseconds: 600);
  static const Duration defaultMaxDuration = Duration(seconds: 45);
  static const Duration defaultSilenceTimeout = Duration(milliseconds: 3800);
  static const Duration amplitudeInterval = Duration(milliseconds: 200);

  final AudioRecorder _recorder;
  final SilenceEndpointTracker _silenceTracker;
  final Future<Directory> Function() _tempDirectoryProvider;

  String? _currentPath;
  DateTime? _startedAt;
  Timer? _maxDurationTimer;
  StreamSubscription<Amplitude>? _amplitudeSub;
  Stream<Amplitude>? _rawAmplitudeStream;
  Stream<Amplitude>? _broadcastAmplitudeStream;
  bool _finalizing = false;

  void Function(ThoughtRecording recording)? onAutoStopped;
  void Function(RecorderException error)? onAutoStopFailed;

  bool get isRecording => _currentPath != null;
  bool get isFinalizing => _finalizing;

  Future<bool> hasPermission() => _recorder.hasPermission();

  Future<void> start({Duration maxDuration = defaultMaxDuration}) async {
    if (isRecording || _finalizing) return;
    String? path;
    try {
      if (!await _recorder.hasPermission()) {
        throw const RecorderException('Chưa có quyền dùng micro.');
      }
      path = await _newRecordingPath();
      _silenceTracker.reset();
      await _recorder.start(
        const RecordConfig(
          encoder: AudioEncoder.aacLc,
          sampleRate: 16000,
          numChannels: 1,
          bitRate: 64000,
          autoGain: true,
          echoCancel: true,
          noiseSuppress: true,
          audioInterruption: AudioInterruptionMode.pauseResume,
        ),
        path: path,
      );
      _currentPath = path;
      _startedAt = DateTime.now();
      await _amplitudeSub?.cancel();
      var amplitudeStream = _recorder.onAmplitudeChanged(amplitudeInterval);
      if (!amplitudeStream.isBroadcast) {
        if (!identical(amplitudeStream, _rawAmplitudeStream)) {
          _rawAmplitudeStream = amplitudeStream;
          _broadcastAmplitudeStream = amplitudeStream.asBroadcastStream();
        }
        amplitudeStream = _broadcastAmplitudeStream!;
      }
      _amplitudeSub = amplitudeStream.listen((amp) {
        if (!isRecording || _finalizing) return;
        if (_silenceTracker.onSample(amp.current, DateTime.now())) {
          unawaited(_autoStop());
        }
      });
      _maxDurationTimer = Timer(maxDuration, () => unawaited(_autoStop()));
    } catch (error) {
      final sub = _amplitudeSub;
      _amplitudeSub = null;
      await sub?.cancel();
      _maxDurationTimer?.cancel();
      _maxDurationTimer = null;
      _currentPath = null;
      _startedAt = null;
      _silenceTracker.reset();
      if (path != null) await _deleteIfExists(path);
      throw error is RecorderException
          ? error
          : const RecorderException('Chưa bắt đầu thu được.');
    }
  }

  Future<ThoughtRecording> stopAndFinalize() async {
    final expectedPath = _currentPath;
    final startedAt = _startedAt;
    if (_finalizing || expectedPath == null || startedAt == null) {
      throw const RecorderException(
        'Không có bản thu nào đang chạy.',
        countsAsFailure: false,
      );
    }
    _finalizing = true;
    _maxDurationTimer?.cancel();
    _maxDurationTimer = null;
    _currentPath = null;
    _startedAt = null;
    final sub = _amplitudeSub;
    _amplitudeSub = null;
    await sub?.cancel();

    try {
      final path = await _recorder.stop() ?? expectedPath;
      final duration = DateTime.now().difference(startedAt);
      if (duration < minDuration) {
        await _deleteIfExists(path);
        throw const RecorderException(
          'Giữ nút lâu hơn một chút để nói nhé.',
          countsAsFailure: false,
        );
      }
      final file = File(path);
      if (!await file.exists()) {
        throw const RecorderException('Chưa lưu được tệp ghi âm.');
      }
      final size = await file.length();
      if (size == 0) {
        await _deleteIfExists(path);
        throw const RecorderException('Tệp ghi âm bị rỗng.');
      }
      return ThoughtRecording(path: path, duration: duration, sizeBytes: size);
    } catch (error) {
      await _deleteIfExists(expectedPath);
      throw error is RecorderException
          ? error
          : const RecorderException('Chưa lưu được tệp ghi âm.');
    } finally {
      _finalizing = false;
      _silenceTracker.reset();
    }
  }

  Future<void> cancel() async {
    final path = _currentPath;
    _maxDurationTimer?.cancel();
    _maxDurationTimer = null;
    _currentPath = null;
    _startedAt = null;
    _silenceTracker.reset();
    final sub = _amplitudeSub;
    _amplitudeSub = null;
    await sub?.cancel();
    if (path == null) return;
    try {
      await _recorder.cancel();
    } catch (_) {}
    await _deleteIfExists(path);
  }

  Future<void> deleteRecording(String path) => _deleteIfExists(path);

  Future<void> dispose() async {
    await cancel();
    _rawAmplitudeStream = null;
    _broadcastAmplitudeStream = null;
    await _recorder.dispose();
  }

  Future<void> _autoStop() async {
    if (!isRecording || _finalizing) return;
    try {
      final recording = await stopAndFinalize();
      onAutoStopped?.call(recording);
    } on RecorderException catch (error) {
      onAutoStopFailed?.call(error);
    }
  }

  Future<String> _newRecordingPath() async {
    final tempDir = await _tempDirectoryProvider();
    final folder = Directory('${tempDir.path}/uf1_voice');
    await folder.create(recursive: true);
    final stamp = DateTime.now().microsecondsSinceEpoch;
    return '${folder.path}/uf1_voice_$stamp.m4a';
  }

  Future<void> _deleteIfExists(String path) async {
    try {
      final file = File(path);
      if (await file.exists()) await file.delete();
    } on FileSystemException {
      // Không xóa được tệp tạm thì bỏ qua, không làm gãy luồng chính.
    }
  }
}

/// Speech recognition handled by the browser or the device.
class OnDeviceSpeechService {
  final SpeechToText _speech = SpeechToText();
  Future<bool>? _initializing;
  void Function(String)? _onError;
  void Function()? _onDone;

  bool get isListening => _speech.isListening;

  Future<void> requestAndroidPermission() async {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return;
    try {
      await _initialize();
    } catch (error) {
      debugPrint('Could not initialize Android speech recognition: $error');
    }
  }

  Future<bool> _initialize() => _initializing ??= _speech
      .initialize(
        onError: (error) => _onError?.call(error.errorMsg),
        onStatus: (status) {
          if (status == SpeechToText.doneStatus) _onDone?.call();
        },
      )
      .whenComplete(() => _initializing = null);

  Future<bool> start({
    required void Function(String words, bool isFinal) onWords,
    required void Function(String message) onError,
    required void Function() onDone,
    void Function(double level)? onSoundLevel,
  }) async {
    _onError = onError;
    _onDone = onDone;
    if (!await _initialize()) return false;

    // On web, locales() returns the current recognizer language, which may be unset.
    var localeId = 'vi-VN';
    if (!kIsWeb) {
      final locales = await _speech.locales();
      final vietnamese = locales.where(
        (locale) => locale.localeId.toLowerCase().startsWith('vi'),
      );
      if (vietnamese.isEmpty) {
        onError('Thiết bị chưa có dịch vụ nhận diện tiếng Việt.');
        return false;
      }
      localeId = vietnamese.first.localeId;
    }

    await _speech.listen(
      listenOptions: SpeechListenOptions(
        listenMode: ListenMode.dictation,
        partialResults: true,
        cancelOnError: true,
        localeId: localeId,
        listenFor: const Duration(seconds: 45),
        pauseFor: const Duration(seconds: 4),
      ),
      onSoundLevelChange: onSoundLevel,
      onResult: (result) => onWords(result.recognizedWords, result.finalResult),
    );
    return true;
  }

  Future<void> stop() => _speech.stop();

  Future<void> cancel() => _speech.cancel();

  Future<void> dispose() => _speech.cancel();
}

class VoiceTranscriptionService {
  VoiceTranscriptionService({ApiClient? api}) : _api = api ?? ApiClient();
  final ApiClient _api;

  Future<VoiceTranscriptionResult> transcribeM4aWithMetadata(
    String path, {
    required String recordingId,
    String? thoughtId,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final device = prefs.getString('focus_device_id') ??
        ApiClient.newIdempotencyKey('device');
    await prefs.setString('focus_device_id', device);
    final purpose = thoughtId == null ? 'uf1_input' : 'thought_to_draft';
    final data = await _api.postData(
      '/voice/transcriptions',
      idempotencyKey: ApiClient.stableIdempotencyKey(
        'transcription',
        '$device:$purpose:$thoughtId:$recordingId',
      ),
      receiveTimeout: const Duration(seconds: 48),
      data: FormData.fromMap({
        'purpose': purpose,
        'device_id': device,
        if (thoughtId != null) 'thought_id': thoughtId,
        'file': await MultipartFile.fromFile(path, filename: 'recording.m4a'),
      }),
    );
    final result = VoiceTranscriptionResult.fromJson(data);
    if (result.text.isEmpty) {
      throw const ApiException('Chưa nhận được giọng nói.');
    }
    return result;
  }

  Future<String> transcribeM4a(
    String path, {
    required String recordingId,
    String? thoughtId,
  }) async {
    final result = await transcribeM4aWithMetadata(
      path,
      recordingId: recordingId,
      thoughtId: thoughtId,
    );
    return result.text;
  }
}
