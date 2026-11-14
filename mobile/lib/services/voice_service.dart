import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import 'package:flutter/foundation.dart';
import 'package:speech_to_text/speech_to_text.dart';

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

  Future<String> transcribeM4a(
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
      data: FormData.fromMap({
        'purpose': purpose,
        'device_id': device,
        if (thoughtId != null) 'thought_id': thoughtId,
        'file': await MultipartFile.fromFile(path, filename: 'recording.m4a'),
      }),
    );
    final text = (data['text'] as String?)?.trim();
    if (text == null || text.isEmpty) {
      throw const ApiException('Chưa nhận được giọng nói.');
    }
    return text;
  }
}
