import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/providers/task_provider.dart';
import 'package:proplan_va/screens/task_entry_screen.dart';
import 'package:proplan_va/services/audio_recorder_service.dart';
import 'package:proplan_va/services/planning_api.dart';
import 'package:proplan_va/services/voice_service.dart';
import 'package:provider/provider.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeAudioRecorder extends Fake implements AudioRecorder {
  _FakeAudioRecorder({
    this.stopCompleter,
    this.stopCalledCompleter,
  });

  final Completer<String?>? stopCompleter;
  final Completer<void>? stopCalledCompleter;
  final StreamController<Amplitude> _amplitudeController =
      StreamController<Amplitude>.broadcast();

  RecordConfig? lastConfig;
  String? lastPath;
  int startCount = 0;
  int stopCount = 0;
  int cancelCount = 0;
  int disposeCount = 0;

  @override
  Future<bool> hasPermission() async => true;

  @override
  Future<void> start(RecordConfig config, {required String path}) async {
    startCount++;
    lastConfig = config;
    lastPath = path;
    final file = File(path);
    file.parent.createSync(recursive: true);
    file.writeAsBytesSync(const [1, 2, 3, 4]);
  }

  @override
  Stream<Amplitude> onAmplitudeChanged(Duration interval) =>
      _amplitudeController.stream;

  void emitAmplitude(double currentDb, {double? maxDb}) {
    if (!_amplitudeController.isClosed) {
      _amplitudeController.add(
        Amplitude(current: currentDb, max: maxDb ?? currentDb),
      );
    }
  }

  @override
  Future<String?> stop() async {
    stopCount++;
    if (stopCalledCompleter != null && !stopCalledCompleter!.isCompleted) {
      stopCalledCompleter!.complete();
    }
    if (stopCompleter != null) {
      return stopCompleter!.future;
    }
    await Future<void>.delayed(const Duration(milliseconds: 10));
    return lastPath;
  }

  @override
  Future<void> cancel() async {
    cancelCount++;
  }

  @override
  Future<void> dispose() async {
    disposeCount++;
    if (!_amplitudeController.isClosed) {
      await _amplitudeController.close();
    }
  }
}

class _FakeUf1VoiceRecorderService extends Fake
    implements Uf1VoiceRecorderService {
  @override
  void Function(ThoughtRecording recording)? onAutoStopped;

  @override
  void Function(RecorderException error)? onAutoStopFailed;

  bool _recording = false;
  bool _finalizing = false;
  int startCount = 0;
  int stopCount = 0;
  int deleteCount = 0;
  int disposeCount = 0;
  String nextPath = '/tmp/uf1_fake_recording.m4a';

  @override
  bool get isRecording => _recording;

  @override
  bool get isFinalizing => _finalizing;

  void beginAutoStopFinalizing() {
    _finalizing = true;
  }

  @override
  Future<bool> hasPermission() async => true;

  @override
  Future<void> start({
    Duration maxDuration = Uf1VoiceRecorderService.defaultMaxDuration,
  }) async {
    startCount++;
    _recording = true;
    _finalizing = false;
  }

  @override
  Future<ThoughtRecording> stopAndFinalize() async {
    stopCount++;
    _recording = false;
    _finalizing = false;
    return ThoughtRecording(
      path: nextPath,
      duration: const Duration(seconds: 2),
      sizeBytes: 1024,
    );
  }

  @override
  Future<void> deleteRecording(String path) async {
    deleteCount++;
  }

  @override
  Future<void> dispose() async {
    disposeCount++;
    _recording = false;
    _finalizing = false;
  }

  void triggerAutoStop(ThoughtRecording recording) {
    _recording = false;
    _finalizing = false;
    onAutoStopped?.call(recording);
  }
}

class _FakeVoiceTranscriptionService extends Fake
    implements VoiceTranscriptionService {
  _FakeVoiceTranscriptionService({
    this.nextResult = const VoiceTranscriptionResult(
      text: 'Làm bài tập toán tối nay',
      inputSource: 'voice',
      lowConfidence: true,
    ),
    this.errorToThrow,
  });

  VoiceTranscriptionResult nextResult;
  Object? errorToThrow;
  int callCount = 0;

  @override
  Future<VoiceTranscriptionResult> transcribeM4aWithMetadata(
    String path, {
    required String recordingId,
    String? thoughtId,
  }) async {
    callCount++;
    final error = errorToThrow;
    if (error != null) throw error;
    return nextResult;
  }
}

class _FakeOnDeviceSpeechService extends Fake
    implements OnDeviceSpeechService {
  int startCallCount = 0;
  int stopCallCount = 0;
  int cancelCallCount = 0;
  int disposeCallCount = 0;

  void Function(String words, bool isFinal)? _onWords;
  void Function()? _onDone;

  @override
  Future<void> requestAndroidPermission() async {}

  @override
  Future<bool> start({
    required void Function(String words, bool isFinal) onWords,
    required void Function(String message) onError,
    required void Function() onDone,
    void Function(double level)? onSoundLevel,
  }) async {
    startCallCount++;
    _onWords = onWords;
    _onDone = onDone;
    return true;
  }

  void emitWords(String words, {bool isFinal = true}) {
    _onWords?.call(words, isFinal);
    if (isFinal) _onDone?.call();
  }

  @override
  Future<void> stop() async {
    stopCallCount++;
  }

  @override
  Future<void> cancel() async {
    cancelCallCount++;
  }

  @override
  Future<void> dispose() async {
    disposeCallCount++;
  }
}

Future<void> _pumpTaskEntryScreen(
  WidgetTester tester, {
  required TaskProvider taskProvider,
  required Uf1VoiceRecorderService recorder,
  required _FakeVoiceTranscriptionService transcriptionService,
  required _FakeOnDeviceSpeechService speechService,
}) async {
  tester.view.physicalSize = const Size(900, 1800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(taskProvider.dispose);

  await tester.pumpWidget(
    ChangeNotifierProvider<TaskProvider>.value(
      value: taskProvider,
      child: MaterialApp(
        home: TaskEntryScreen(
          recorder: recorder,
          transcriptionService: transcriptionService,
          speechService: speechService,
        ),
      ),
    ),
  );
  // Trigger addPostFrameCallback in TaskEntryScreen.initState.
  await tester.pump();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('VoiceTranscriptionService (T1.1, T1.3)', () {
    test(
      'transcribeM4aWithMetadata sends multipart uf1_input with Idempotency-Key and parses metadata',
      () async {
        SharedPreferences.setMockInitialValues({'focus_device_id': 'dev-uf1'});
        final tempDir = await Directory.systemTemp.createTemp('uf1_stt_');
        addTearDown(() => tempDir.delete(recursive: true));
        final audioFile =
            await File('${tempDir.path}/sample.m4a').writeAsBytes([10, 20, 30]);

        final requests = <RequestOptions>[];
        final responses = <Map<String, dynamic>>[
          {
            'text': '  Học bài lúc 8 giờ  ',
            'input_source': 'voice',
            'low_confidence': true,
            'thought_id': 'thought-abc',
          },
          {
            'text': '  Ôn tập chương 2  ',
            'input_source': 'voice',
            'low_confidence': false,
            'thought_id': null,
          },
          {
            'text': '  Nộp báo cáo trước thứ Sáu  ',
            'input_source': 'voice',
            'low_confidence': false,
          },
        ];
        var callIndex = 0;

        final dio = Dio()
          ..interceptors.add(
            InterceptorsWrapper(
              onRequest: (request, handler) {
                requests.add(request);
                final payload = responses[callIndex++];
                handler.resolve(
                  Response(
                    requestOptions: request,
                    statusCode: 200,
                    data: {'data': payload},
                  ),
                );
              },
            ),
          );

        final service = VoiceTranscriptionService(api: ApiClient(dio: dio));

        final lowConfidenceResult = await service.transcribeM4aWithMetadata(
          audioFile.path,
          recordingId: 'rec-1',
        );
        final clearResult = await service.transcribeM4aWithMetadata(
          audioFile.path,
          recordingId: 'rec-1',
        );
        final plainText = await service.transcribeM4a(
          audioFile.path,
          recordingId: 'rec-2',
        );

        expect(requests, hasLength(3));
        for (final req in requests) {
          expect(req.method, 'POST');
          expect(req.path, '/voice/transcriptions');
          expect(req.receiveTimeout, const Duration(seconds: 25));
          expect(req.data, isA<FormData>());
          final formData = req.data as FormData;
          final fields = Map.fromEntries(formData.fields);
          expect(fields['purpose'], 'uf1_input');
          expect(fields['device_id'], 'dev-uf1');
          expect(fields.containsKey('thought_id'), isFalse);
          expect(formData.files.single.key, 'file');
          expect(req.headers['Idempotency-Key'], isA<String>());
          expect((req.headers['Idempotency-Key'] as String).isNotEmpty, isTrue);
        }

        // Same recordingId keeps deterministic Idempotency-Key; different ID changes it.
        expect(
          requests[0].headers['Idempotency-Key'],
          requests[1].headers['Idempotency-Key'],
        );
        expect(
          requests[2].headers['Idempotency-Key'],
          isNot(requests[0].headers['Idempotency-Key']),
        );

        expect(lowConfidenceResult.text, 'Học bài lúc 8 giờ');
        expect(lowConfidenceResult.lowConfidence, isTrue);
        expect(lowConfidenceResult.inputSource, 'voice');
        expect(lowConfidenceResult.thoughtId, 'thought-abc');

        expect(clearResult.text, 'Ôn tập chương 2');
        expect(clearResult.lowConfidence, isFalse);
        expect(clearResult.inputSource, 'voice');
        expect(clearResult.thoughtId, isNull);

        expect(plainText, 'Nộp báo cáo trước thứ Sáu');
      },
    );
  });

  group('SilenceEndpointTracker (T1.2 ADHD-I Endpointing)', () {
    final baseTime = DateTime(2026, 11, 20, 10, 0, 0);

    test('never cuts off before user starts speaking even after 10+s silence',
        () {
      final tracker = SilenceEndpointTracker();

      for (var sec = 0; sec <= 12; sec++) {
        final shouldStop = tracker.onSample(
          -55.0,
          baseTime.add(Duration(seconds: sec)),
        );
        expect(shouldStop, isFalse);
      }
      expect(tracker.onSample(-45.0, baseTime.add(const Duration(seconds: 15))),
          isFalse);
      expect(tracker.hasDetectedSpeech, isFalse);
    });

    test('preserves 2.0s and 3.0s ADHD-I pauses when followed by renewed speech',
        () {
      final tracker = SilenceEndpointTracker();

      // Initial speech.
      expect(tracker.onSample(-28.0, baseTime), isFalse);
      expect(tracker.hasDetectedSpeech, isTrue);

      // 2.0s pause.
      expect(
        tracker.onSample(-50.0, baseTime.add(const Duration(milliseconds: 200))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 2200))),
        isFalse,
      );

      // Renewed speech resets timer.
      expect(
        tracker.onSample(
            -25.0, baseTime.add(const Duration(milliseconds: 2400))),
        isFalse,
      );

      // 3.0s pause.
      expect(
        tracker.onSample(
            -48.0, baseTime.add(const Duration(milliseconds: 2600))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -48.0, baseTime.add(const Duration(milliseconds: 5600))),
        isFalse,
      );

      // Renewed speech again resets timer.
      expect(
        tracker.onSample(
            -25.0, baseTime.add(const Duration(milliseconds: 5800))),
        isFalse,
      );

      // Still under 3800ms after renewed speech -> false.
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 6000))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 9799))),
        isFalse,
      );
    });

    test('returns true after >= 3800ms continuous silence following speech', () {
      final tracker = SilenceEndpointTracker();

      expect(tracker.onSample(-40.0, baseTime), isFalse);
      expect(
        tracker.onSample(
            -45.0, baseTime.add(const Duration(milliseconds: 500))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -52.0, baseTime.add(const Duration(milliseconds: 4299))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -45.0, baseTime.add(const Duration(milliseconds: 4300))),
        isTrue,
      );
      expect(
        tracker.onSample(
            -55.0, baseTime.add(const Duration(milliseconds: 4800))),
        isTrue,
      );
    });

    test('soft breath between -45 and -40 dBFS resets silence accumulation', () {
      final tracker = SilenceEndpointTracker();

      expect(tracker.onSample(-22.0, baseTime), isFalse);
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 200))),
        isFalse,
      );
      // 3.0s of silence so far.
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 3200))),
        isFalse,
      );
      // Soft breath (-42 dBFS) resets silence accumulation.
      expect(
        tracker.onSample(
            -42.0, baseTime.add(const Duration(milliseconds: 3400))),
        isFalse,
      );
      // New silence starts at 3600ms -> at 4500ms (would be 4.3s from 200ms) it must still be false.
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 3600))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 4500))),
        isFalse,
      );
      // Triggers only once 3800ms elapses from 3600ms (i.e. 7400ms).
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 7400))),
        isTrue,
      );
    });

    test('safely ignores double.nan and infinities', () {
      final tracker = SilenceEndpointTracker();

      expect(tracker.onSample(double.nan, baseTime), isFalse);
      expect(tracker.onSample(double.infinity, baseTime), isFalse);
      expect(tracker.onSample(double.negativeInfinity, baseTime), isFalse);
      expect(tracker.hasDetectedSpeech, isFalse);

      // NaN / Infinity during silence neither crashes nor resets existing silence start.
      expect(tracker.onSample(-20.0, baseTime), isFalse);
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 100))),
        isFalse,
      );
      expect(
        tracker.onSample(
            double.nan, baseTime.add(const Duration(milliseconds: 2000))),
        isFalse,
      );
      expect(
        tracker.onSample(
            double.infinity, baseTime.add(const Duration(milliseconds: 2500))),
        isFalse,
      );
      expect(
        tracker.onSample(
            -50.0, baseTime.add(const Duration(milliseconds: 3900))),
        isTrue,
      );
    });
  });

  group('Uf1VoiceRecorderService & RISK-04 separation (T1.1, T1.2, RISK-04)',
      () {
    test(
      'start() passes speech-processing RecordConfig with pauseResume and auto-stops cleanly after 3.8s+ silence without duplicate stop race',
      () async {
        final tempDir = await Directory.systemTemp.createTemp('uf1_rec_');
        addTearDown(() => tempDir.delete(recursive: true));

        final fakeRecorder = _FakeAudioRecorder();
        final service = Uf1VoiceRecorderService(
          recorder: fakeRecorder,
          tempDirectoryProvider: () async => tempDir,
        );
        addTearDown(service.dispose);

        final autoStopped = <ThoughtRecording>[];
        final autoStopFailed = <RecorderException>[];
        final completed = Completer<void>();
        service.onAutoStopped = (recording) {
          autoStopped.add(recording);
          if (!completed.isCompleted) completed.complete();
        };
        service.onAutoStopFailed = autoStopFailed.add;

        await service.start();

        expect(service.isRecording, isTrue);
        expect(fakeRecorder.startCount, 1);
        final config = fakeRecorder.lastConfig;
        expect(config, isNotNull);
        expect(config!.echoCancel, isTrue);
        expect(config.noiseSuppress, isTrue);
        expect(config.autoGain, isTrue);
        expect(config.audioInterruption, AudioInterruptionMode.pauseResume);
        expect(config.audioInterruption, isNot(AudioInterruptionMode.none));

        // Emit speech, then start silence and exceed default 3.8s timeout.
        fakeRecorder.emitAmplitude(-20.0);
        await Future<void>.delayed(const Duration(milliseconds: 50));
        fakeRecorder.emitAmplitude(-50.0);
        await Future<void>.delayed(const Duration(milliseconds: 3900));

        // Burst multiple silence samples simultaneously to ensure no duplicate stop race.
        fakeRecorder.emitAmplitude(-52.0);
        fakeRecorder.emitAmplitude(-55.0);
        fakeRecorder.emitAmplitude(-60.0);

        await completed.future.timeout(const Duration(seconds: 2));
        await Future<void>.delayed(const Duration(milliseconds: 40));

        expect(autoStopFailed, isEmpty);
        expect(autoStopped, hasLength(1));
        expect(fakeRecorder.stopCount, 1);
        expect(service.isRecording, isFalse);
        expect(autoStopped.single.sizeBytes, 4);
        expect(
          autoStopped.single.duration,
          greaterThanOrEqualTo(Uf1VoiceRecorderService.minDuration),
        );
      },
    );

    test(
      'isFinalizing stays true while stop() is in flight during _autoStop() and resets to false once onAutoStopped fires',
      () async {
        final tempDir = await Directory.systemTemp.createTemp('uf1_fin_');
        addTearDown(() => tempDir.delete(recursive: true));

        final stopCalledCompleter = Completer<void>();
        final stopCompleter = Completer<String?>();
        addTearDown(() {
          if (!stopCompleter.isCompleted) stopCompleter.complete(null);
        });

        final fakeRecorder = _FakeAudioRecorder(
          stopCalledCompleter: stopCalledCompleter,
          stopCompleter: stopCompleter,
        );
        final service = Uf1VoiceRecorderService(
          recorder: fakeRecorder,
          silenceTimeout: const Duration(milliseconds: 200),
          tempDirectoryProvider: () async => tempDir,
        );
        addTearDown(service.dispose);

        final autoStopped = <ThoughtRecording>[];
        final completed = Completer<void>();
        service.onAutoStopped = (recording) {
          autoStopped.add(recording);
          if (!completed.isCompleted) completed.complete();
        };

        await service.start();
        expect(service.isFinalizing, isFalse);

        // Wait past minDuration (600ms) and trigger silence auto-stop.
        fakeRecorder.emitAmplitude(-20.0);
        await Future<void>.delayed(const Duration(milliseconds: 650));
        fakeRecorder.emitAmplitude(-50.0);
        await Future<void>.delayed(const Duration(milliseconds: 250));
        fakeRecorder.emitAmplitude(-52.0);

        await stopCalledCompleter.future.timeout(const Duration(seconds: 2));
        expect(service.isFinalizing, isTrue);
        expect(fakeRecorder.stopCount, 1);

        stopCompleter.complete(fakeRecorder.lastPath);
        await completed.future.timeout(const Duration(seconds: 2));

        expect(service.isFinalizing, isFalse);
        expect(autoStopped, hasLength(1));
        expect(fakeRecorder.stopCount, 1);
      },
    );

    test(
      'RISK-04: UF4 AudioRecorderService remains completely intact with AudioInterruptionMode.none',
      () {
        expect(AudioRecorderService.maxFailures, 3);
        expect(
          AudioRecorderService.minDuration,
          const Duration(milliseconds: 600),
        );
        expect(
          AudioRecorderService.defaultMaxDuration,
          const Duration(minutes: 2),
        );

        final uf4Recorder = AudioRecorderService.taskEntry();
        expect(uf4Recorder.isRecording, isFalse);
        expect(uf4Recorder.failedAttempts, 0);
        expect(uf4Recorder.shouldFallbackToText, isFalse);

        final uf4SourceFile = File('lib/services/audio_recorder_service.dart');
        expect(uf4SourceFile.existsSync(), isTrue);
        final source = uf4SourceFile.readAsStringSync();
        expect(source, contains('audioInterruption: AudioInterruptionMode.none'));
        expect(source, isNot(contains('AudioInterruptionMode.pauseResume')));
        expect(source, isNot(contains('SilenceEndpointTracker')));
      },
    );
  });

  group('TaskProvider (T1.3)', () {
    test(
      'tracks isVoiceLowConfidence, preserves entry_source voice, handles identical trimmed re-apply, and resets on clearDraft',
      () async {
        FlutterSecureStorage.setMockInitialValues({});

        final requests = <RequestOptions>[];
        final dio = Dio()
          ..interceptors.add(
            InterceptorsWrapper(
              onRequest: (request, handler) {
                requests.add(request);
                handler.resolve(
                  Response(
                    requestOptions: request,
                    statusCode: 201,
                    data: {
                      'data': {
                        'draft_id': 'draft-voice-1',
                        'client_draft_id': 'client-1',
                        'stage': 'captured',
                        'summary_revision': 1,
                        'planning_input_revision': 1,
                        'input_source': 'voice',
                        'raw_input': 'Học bài lúc 8 giờ',
                        'summary': {
                          'title': 'Học bài lúc 8 giờ',
                          'can_plan': false,
                          'clarification_issues': <Object>[],
                        },
                      },
                    },
                  ),
                );
              },
            ),
          );

        final provider = TaskProvider(
          planningApi: PlanningApi(client: ApiClient(dio: dio)),
        );
        addTearDown(provider.dispose);

        provider.setTitle(
          'Học bài lúc 8 giờ',
          fromVoice: true,
          lowConfidence: true,
        );
        expect(provider.isVoiceLowConfidence, isTrue);

        // Allow async _persist() to flush to FlutterSecureStorage.
        await Future<void>.delayed(const Duration(milliseconds: 30));
        final persistedJson = await const FlutterSecureStorage().read(
          key: 'proplan.uf1.current_draft.v1',
        );
        expect(persistedJson, isNotNull);
        final persistedMap = jsonDecode(persistedJson!) as Map<String, dynamic>;
        expect(persistedMap['entry_source'], 'voice');

        // Also confirm draft payload sent to backend keeps input_source == 'voice'.
        await provider.saveDraftToBackend();
        expect(requests, isNotEmpty);
        expect(requests.first.path, '/drafts');
        expect((requests.first.data as Map)['input_source'], 'voice');

        // Editing text manually clears low-confidence banner flag.
        provider.setTitle('Học bài lúc 9 giờ');
        expect(provider.isVoiceLowConfidence, isFalse);

        // Re-applying voice result with identical trimmed text still sets low confidence.
        provider.setTitle(
          'Học bài lúc 9 giờ',
          fromVoice: true,
          lowConfidence: true,
        );
        expect(provider.isVoiceLowConfidence, isTrue);

        // clearDraft resets low confidence and entry source.
        provider.clearDraft();
        expect(provider.isVoiceLowConfidence, isFalse);
        expect(provider.title, isEmpty);

        // Starting from manual text and then applying identical trimmed text
        // from voice must persist entry_source == 'voice' in FlutterSecureStorage.
        provider.setTitle('Đọc tài liệu mới');
        await Future<void>.delayed(const Duration(milliseconds: 30));
        final textOnlyJson = await const FlutterSecureStorage().read(
          key: 'proplan.uf1.current_draft.v1',
        );
        expect(textOnlyJson, isNotNull);
        expect(
          (jsonDecode(textOnlyJson!) as Map<String, dynamic>)['entry_source'],
          'text',
        );

        provider.setTitle(
          'Đọc tài liệu mới',
          fromVoice: true,
          lowConfidence: true,
        );
        expect(provider.isVoiceLowConfidence, isTrue);
        await Future<void>.delayed(const Duration(milliseconds: 30));
        final voiceReappliedJson = await const FlutterSecureStorage().read(
          key: 'proplan.uf1.current_draft.v1',
        );
        expect(voiceReappliedJson, isNotNull);
        expect(
          (jsonDecode(voiceReappliedJson!)
              as Map<String, dynamic>)['entry_source'],
          'voice',
        );
      },
    );
  });

  group('TaskEntryScreen Widget Tests (T1.1, T1.2, T1.3, W3C COGA)', () {
    testWidgets(
      'low_confidence transcript populates TextField, shows banner without auto-navigating, focuses on tap, and dismisses banner on edit',
      (tester) async {
        FlutterSecureStorage.setMockInitialValues({});
        final taskProvider = TaskProvider();
        final fakeRecorder = _FakeUf1VoiceRecorderService();
        final fakeTranscription = _FakeVoiceTranscriptionService(
          nextResult: const VoiceTranscriptionResult(
            text: 'Làm bài tập toán tối nay',
            inputSource: 'voice',
            lowConfidence: true,
          ),
        );
        final fakeSpeech = _FakeOnDeviceSpeechService();

        await _pumpTaskEntryScreen(
          tester,
          taskProvider: taskProvider,
          recorder: fakeRecorder,
          transcriptionService: fakeTranscription,
          speechService: fakeSpeech,
        );

        expect(
          find.byKey(const Key('uf1_low_confidence_banner')),
          findsNothing,
        );

        // Start recording via mic button.
        await tester.tap(find.byTooltip('Bắt đầu nói'));
        await tester.pump();
        expect(fakeRecorder.startCount, 1);

        // Stop recording (or auto-stop) -> backend returns low_confidence: true.
        await tester.tap(find.byTooltip('Dừng nghe'));
        await tester.pump();
        await tester.pump();

        expect(fakeTranscription.callCount, 1);
        expect(
          tester.widget<TextField>(find.byType(TextField)).controller?.text,
          'Làm bài tập toán tối nay',
        );
        expect(
          find.byKey(const Key('uf1_low_confidence_banner')),
          findsOneWidget,
        );
        expect(find.byType(AiLoadingScreen), findsNothing);

        // Tapping banner focuses the TextField.
        final textFieldFocus =
            tester.widget<TextField>(find.byType(TextField)).focusNode!;
        expect(textFieldFocus.hasFocus, isFalse);

        await tester.tap(find.byKey(const Key('uf1_low_confidence_banner')));
        await tester.pump();
        expect(textFieldFocus.hasFocus, isTrue);

        // Editing text inside TextField dismisses the low-confidence banner.
        await tester.enterText(
          find.byType(TextField),
          'Làm bài tập toán lúc 20 giờ',
        );
        await tester.pump();

        expect(
          find.byKey(const Key('uf1_low_confidence_banner')),
          findsNothing,
        );
        expect(taskProvider.isVoiceLowConfidence, isFalse);
      },
    );

    testWidgets(
      'auto-stop silence endpointing transcribes without auto-navigating when low_confidence is true',
      (tester) async {
        FlutterSecureStorage.setMockInitialValues({});
        final taskProvider = TaskProvider();
        final fakeRecorder = _FakeUf1VoiceRecorderService();
        final fakeTranscription = _FakeVoiceTranscriptionService(
          nextResult: const VoiceTranscriptionResult(
            text: 'Đọc xong chương 3',
            inputSource: 'voice',
            lowConfidence: true,
          ),
        );
        final fakeSpeech = _FakeOnDeviceSpeechService();

        await _pumpTaskEntryScreen(
          tester,
          taskProvider: taskProvider,
          recorder: fakeRecorder,
          transcriptionService: fakeTranscription,
          speechService: fakeSpeech,
        );

        await tester.tap(find.byTooltip('Bắt đầu nói'));
        await tester.pump();

        fakeRecorder.triggerAutoStop(
          const ThoughtRecording(
            path: '/tmp/uf1_autostop.m4a',
            duration: Duration(seconds: 4),
            sizeBytes: 2048,
          ),
        );
        await tester.pump();
        await tester.pump();

        expect(fakeTranscription.callCount, 1);
        expect(
          tester.widget<TextField>(find.byType(TextField)).controller?.text,
          'Đọc xong chương 3',
        );
        expect(
          find.byKey(const Key('uf1_low_confidence_banner')),
          findsOneWidget,
        );
        expect(find.byType(AiLoadingScreen), findsNothing);
      },
    );

    testWidgets(
      'falls back gracefully to OnDeviceSpeechService when transcription throws',
      (tester) async {
        FlutterSecureStorage.setMockInitialValues({});
        final taskProvider = TaskProvider();
        final fakeRecorder = _FakeUf1VoiceRecorderService();
        final fakeTranscription = _FakeVoiceTranscriptionService(
          errorToThrow: const ApiException('Mất kết nối máy chủ STT'),
        );
        final fakeSpeech = _FakeOnDeviceSpeechService();

        await _pumpTaskEntryScreen(
          tester,
          taskProvider: taskProvider,
          recorder: fakeRecorder,
          transcriptionService: fakeTranscription,
          speechService: fakeSpeech,
        );

        // Start & stop recording -> backend STT throws.
        await tester.tap(find.byTooltip('Bắt đầu nói'));
        await tester.pump();
        await tester.tap(find.byTooltip('Dừng nghe'));
        await tester.pump();
        await tester.pump();

        expect(fakeTranscription.callCount, 1);
        expect(
          find.textContaining('Chạm mic để thử nhận diện trên máy'),
          findsOneWidget,
        );
        expect(tester.takeException(), isNull);

        // Tapping mic again switches cleanly to OnDeviceSpeechService.
        await tester.tap(find.byTooltip('Bắt đầu nói'));
        await tester.pump();
        expect(fakeSpeech.startCallCount, 1);

        fakeSpeech.emitWords('Ôn bài tiếng Anh');
        await tester.pump();

        expect(
          tester.widget<TextField>(find.byType(TextField)).controller?.text,
          'Ôn bài tiếng Anh',
        );
        expect(
          find.byKey(const Key('uf1_low_confidence_banner')),
          findsNothing,
        );
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'manual stop tap during in-flight silence auto-stop does not throw or trigger on-device fallback',
      (tester) async {
        FlutterSecureStorage.setMockInitialValues({});
        final taskProvider = TaskProvider();
        final fakeRecorder = _FakeUf1VoiceRecorderService();
        final fakeTranscription = _FakeVoiceTranscriptionService(
          nextResult: const VoiceTranscriptionResult(
            text: 'Hoàn tất bài tập nhóm',
            inputSource: 'voice',
            lowConfidence: false,
          ),
        );
        final fakeSpeech = _FakeOnDeviceSpeechService();

        await _pumpTaskEntryScreen(
          tester,
          taskProvider: taskProvider,
          recorder: fakeRecorder,
          transcriptionService: fakeTranscription,
          speechService: fakeSpeech,
        );

        await tester.tap(find.byTooltip('Bắt đầu nói'));
        await tester.pump();
        expect(fakeRecorder.isRecording, isTrue);

        // Simulate _autoStop() currently finalizing native stop().
        fakeRecorder.beginAutoStopFinalizing();
        expect(fakeRecorder.isFinalizing, isTrue);

        // User taps the stop button while isFinalizing == true.
        await tester.tap(find.byTooltip('Dừng nghe'));
        await tester.pump();

        // Now _autoStop() finishes and delivers the recorded audio.
        fakeRecorder.triggerAutoStop(
          const ThoughtRecording(
            path: '/tmp/uf1_race.m4a',
            duration: Duration(seconds: 4),
            sizeBytes: 2048,
          ),
        );
        await tester.pump();
        await tester.pump();

        expect(
          find.textContaining('Chưa chuyển được giọng nói'),
          findsNothing,
        );
        expect(fakeRecorder.stopCount, 0);
        expect(fakeTranscription.callCount, 1);
        expect(fakeSpeech.startCallCount, 0);
        expect(
          tester.widget<TextField>(find.byType(TextField)).controller?.text,
          'Hoàn tất bài tập nhóm',
        );
        expect(tester.takeException(), isNull);
      },
    );
  });
}
