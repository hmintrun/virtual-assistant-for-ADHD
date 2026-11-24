// STT hai tầng cho UF1 / Hộp nháp UF4 / UF5 (MOB-05, W4-10).
//
// Tầng 1: gửi file ghi âm lên Backend `/voice/transcriptions` (Gemini Transcribe
//          → Groq Whisper phía server).
// Tầng 2: khi Backend không dùng được (mất mạng, quá giờ, 5xx, 429, STT_*),
//          báo UI mở nghe trực tiếp bằng `speech_to_text` trên máy
//          ([OnDeviceSpeechService]). Lưu ý `speech_to_text` KHÔNG chép được file
//          đã ghi — nó chỉ nghe mic trực tiếp, nên tầng 2 là "nói lại một lần".
//
// Tách file riêng (không sửa `voice_service.dart`) để không đụng phần UF1 voice
// đang làm song song ở nhánh `feat/mobile-uf1-voice-upgrade`; hàm gọi Backend
// được truyền vào nên dùng được với cả `transcribeM4a` hiện tại lẫn
// `transcribeM4aWithMetadata` của nhánh đó.
import 'dart:async';

import '../core/api_client.dart';
import 'voice_service.dart';

/// Kết quả chép lời từ Backend.
class TwoTierTranscript {
  const TwoTierTranscript({required this.text, this.lowConfidence = false});

  final String text;

  /// Backend báo độ tin cậy thấp → UI nên cho người dùng xem/sửa chữ.
  final bool lowConfidence;
}

/// Backend không chép được → UI chuyển sang nghe trực tiếp trên máy.
class SttNeedsOnDeviceFallback implements Exception {
  const SttNeedsOnDeviceFallback(this.reason);

  /// `timeout`, `network`, `STT_UNAVAILABLE`, `STT_RATE_LIMITED`, `http_503`...
  final String reason;

  /// Câu hiển thị cho người dùng (không đổ lỗi, nói rõ việc cần làm).
  String get userMessage =>
      'Máy chủ chưa nghe được. Bạn nói lại một lần, máy sẽ tự ghi chữ.';

  @override
  String toString() => 'SttNeedsOnDeviceFallback($reason)';
}

/// Người dùng đã hủy (bấm Hủy / rời màn hình) trong lúc chờ Backend.
class SttCancelled implements Exception {
  const SttCancelled();
}

/// Hàm gọi Backend chép 1 file ghi âm.
typedef BackendTranscribe = Future<TwoTierTranscript> Function(
  String path, {
  required String recordingId,
  String? thoughtId,
});

class TwoTierSttService {
  TwoTierSttService({
    required BackendTranscribe backend,
    this.timeout = const Duration(seconds: 45),
  }) : _backend = backend;

  /// Dùng [VoiceTranscriptionService] hiện có làm tầng 1.
  factory TwoTierSttService.fromVoiceService(
    VoiceTranscriptionService voice, {
    Duration timeout = const Duration(seconds: 45),
  }) {
    return TwoTierSttService(
      timeout: timeout,
      backend: (path, {required recordingId, thoughtId}) async {
        try {
          final text = await voice.transcribeM4a(
            path,
            recordingId: recordingId,
            thoughtId: thoughtId,
          );
          return TwoTierTranscript(text: text);
        } on ApiException catch (error) {
          // transcribeM4a báo chữ rỗng bằng ApiException không mã → gắn mã
          // NO_SPEECH_DETECTED để không bị hiểu nhầm là mất mạng.
          if (error.code == null &&
              error.statusCode == null &&
              error.message == 'Chưa nhận được giọng nói.') {
            throw ApiException(error.message, code: 'NO_SPEECH_DETECTED');
          }
          rethrow;
        }
      },
    );
  }

  /// Ngân sách chờ Backend. Backend tự fallback Gemini → Groq trong ~12s nên
  /// 45s là trần an toàn (cần `receiveTimeout` của Dio ≥ mốc này — MOB-02).
  final Duration timeout;

  final BackendTranscribe _backend;
  int _generation = 0;
  Completer<void>? _cancelSignal;

  /// Mã lỗi Backend nên chuyển sang nghe trên máy (lỗi hạ tầng, không phải
  /// lỗi của audio). Lỗi audio như `NO_SPEECH_DETECTED`, `AUDIO_TOO_LARGE`,
  /// `UNSUPPORTED_AUDIO` vẫn ném nguyên [ApiException] để UI nhờ ghi âm lại.
  static const Set<String> fallbackCodes = {
    'STT_UNAVAILABLE',
    'STT_RATE_LIMITED',
    'STT_TIMEOUT',
    'STT_PROVIDER_ERROR',
  };

  /// Chép file [path].
  ///
  /// - Thành công → [TwoTierTranscript].
  /// - Lỗi hạ tầng → ném [SttNeedsOnDeviceFallback] (UI mở nghe trên máy).
  /// - Lỗi audio/xác thực → ném nguyên [ApiException].
  /// - Bị [cancel] → ném [SttCancelled] ngay, không treo chờ Backend.
  Future<TwoTierTranscript> transcribeTwoTier(
    String path, {
    required String recordingId,
    String? thoughtId,
  }) async {
    final generation = ++_generation;
    final cancelSignal = Completer<void>();
    _cancelSignal = cancelSignal;

    // Future.sync: hàm backend ném lỗi đồng bộ cũng thành Future lỗi,
    // đi qua cùng nhánh phân loại bên dưới thay vì văng ra ngoài.
    final request = Future<TwoTierTranscript>.sync(
      () => _backend(path, recordingId: recordingId, thoughtId: thoughtId),
    ).timeout(timeout);
    // Không để lỗi của request bị bỏ rơi khi người dùng đã hủy.
    unawaited(request.then((_) {}, onError: (_) {}));

    try {
      final result = await Future.any<TwoTierTranscript?>([
        request,
        cancelSignal.future.then((_) => null),
      ]);
      if (result == null || generation != _generation) {
        throw const SttCancelled();
      }
      if (result.text.trim().isEmpty) {
        throw const ApiException(
          'Chưa nhận được giọng nói.',
          code: 'NO_SPEECH_DETECTED',
        );
      }
      return result;
    } on SttCancelled {
      rethrow;
    } catch (error) {
      if (generation != _generation) throw const SttCancelled();
      final reason = fallbackReason(error);
      if (reason != null) throw SttNeedsOnDeviceFallback(reason);
      rethrow;
    } finally {
      if (identical(_cancelSignal, cancelSignal)) _cancelSignal = null;
    }
  }

  /// Hủy lượt đang chờ: trả quyền điều khiển cho UI ngay (mic không bị treo).
  void cancel() {
    _generation++;
    final signal = _cancelSignal;
    if (signal != null && !signal.isCompleted) signal.complete();
    _cancelSignal = null;
  }

  /// null → không fallback (ném nguyên lỗi). Ngược lại là lý do fallback.
  static String? fallbackReason(Object error) {
    if (error is TimeoutException) return 'timeout';
    if (error is! ApiException) return 'network';
    final code = error.code;
    // Có mã lỗi nghiệp vụ → chỉ fallback với lỗi hạ tầng STT_*.
    if (code != null) return fallbackCodes.contains(code) ? code : null;
    final status = error.statusCode;
    if (status == null) return 'network'; // Dio không có phản hồi
    if (status == 429 || status >= 500) return 'http_$status';
    return null;
  }
}
