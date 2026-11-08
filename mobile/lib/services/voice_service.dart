import 'package:speech_to_text/speech_to_text.dart';

/// Speech recognition handled by Android/iOS; it does not require the backend.
class OnDeviceSpeechService {
  final SpeechToText _speech = SpeechToText();
  bool _initialized = false;

  bool get isListening => _speech.isListening;

  Future<bool> start({
    required void Function(String words, bool isFinal) onWords,
    required void Function(String message) onError,
  }) async {
    if (!_initialized) {
      _initialized = await _speech.initialize(
        onError: (error) => onError(error.errorMsg),
      );
    }
    if (!_initialized) return false;

    final locales = await _speech.locales();
    final vietnamese = locales.where(
      (locale) => locale.localeId.toLowerCase().startsWith('vi'),
    );
    if (vietnamese.isEmpty) {
      onError('Thiết bị chưa có dịch vụ nhận diện tiếng Việt.');
      return false;
    }

    await _speech.listen(
      listenOptions: SpeechListenOptions(
        listenMode: ListenMode.dictation,
        partialResults: true,
        cancelOnError: true,
        localeId: vietnamese.first.localeId,
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
