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
      onResult: (result) => onWords(result.recognizedWords, result.finalResult),
    );
    return true;
  }

  Future<void> stop() async {
    if (_speech.isListening) await _speech.stop();
  }

  Future<void> dispose() => _speech.cancel();
}
