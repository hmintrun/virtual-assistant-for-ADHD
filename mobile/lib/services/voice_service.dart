// Service giọng nói on-device (speech_to_text) — DỰ PHÒNG cho V01 (UF1) khi Backend STT
// (POST /api/v1/voice/transcriptions, Gemini Transcribe) lỗi, quá hạn hoặc mất mạng.
//
// Phạm vi (khảo sát STT 01/11/2026):
// - CHỈ dùng cho V01: speech_to_text nghe mic trực tiếp, KHÔNG đọc được file ghi âm.
//   -> A21 (chép bản ghi UF4 sau phiên) không có fallback on-device: chờ có mạng hoặc gõ tay.
// - KHÔNG dùng trong phiên tập trung (UF4 cấm STT/LLM trong phiên).
// - Kết quả phụ thuộc thiết bị (có máy hỏng đường thu VOICE_RECOGNITION, kể cả Gboard), nên
//   UI luôn phải có lối thoát "gõ chữ".
//
// Yêu cầu nền tảng Android (Mobile Lead thêm khi tạo thư mục android/):
//   AndroidManifest.xml: <uses-permission android:name="android.permission.RECORD_AUDIO"/>
//                        <uses-permission android:name="android.permission.INTERNET"/>
//   Trong <queries>, thêm MỘT <intent> RIÊNG (không gộp vào intent PROCESS_TEXT):
//     <intent><action android:name="android.speech.RecognitionService"/></intent>

import 'dart:async';

import 'package:speech_to_text/speech_recognition_error.dart';
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart';

/// Mã lỗi chuẩn của STT on-device để UI chọn hướng xử lý (nói lại / gõ chữ).
enum OnDeviceSttErrorCode {
  /// Máy không có bộ nhận dạng giọng nói hoặc khởi tạo thất bại.
  unavailable,

  /// Bộ nhận dạng không có tiếng Việt (hoặc thiếu gói offline khi bắt buộc onDevice).
  languageUnavailable,

  /// Không nghe thấy lời nói / không nhận ra chữ nào.
  noSpeech,

  /// Lỗi mạng khi nhận dạng online.
  network,

  /// Người dùng chưa cấp quyền micro.
  permissionDenied,

  /// Đang có một lượt nghe khác chạy.
  busy,

  /// Người dùng chủ động hủy.
  cancelled,

  unknown,
}

class OnDeviceSttException implements Exception {
  const OnDeviceSttException(this.code, [this.detail = '']);

  final OnDeviceSttErrorCode code;

  /// Mã lỗi kỹ thuật gốc của nền tảng (vd `error_no_match`); không chứa nội dung lời nói.
  final String detail;

  @override
  String toString() => 'OnDeviceSttException($code${detail.isEmpty ? '' : ', $detail'})';
}

/// Bọc `speech_to_text`: mỗi lần gọi [listenOnce] trả về đúng 1 câu đã nhận dạng.
class OnDeviceVoiceService {
  OnDeviceVoiceService({SpeechToText? speech}) : _speech = speech ?? SpeechToText();

  final SpeechToText _speech;

  /// Recognizer Android đòi người dùng nói gần như ngay khi bấm; người ADHD hay ngập ngừng
  /// trước khi nói nên để khoảng chờ rộng (khảo sát: 3 giây gây lỗi timeout liên tục).
  static const Duration pauseFor = Duration(seconds: 8);
  static const Duration listenFor = Duration(seconds: 60);

  bool _initialized = false;
  bool _available = false;
  String? _localeId;
  Completer<String>? _pending;
  void Function(String partialText)? _onPartial;

  bool get isListening => _speech.isListening;

  /// Mã ngôn ngữ tiếng Việt máy hỗ trợ (`vi_VN` / `vi-VN`), null nếu không có.
  String? get vietnameseLocaleId => _localeId;

  /// Khởi tạo bộ nhận dạng (tự xin quyền micro lần đầu). Trả về true nếu dùng được tiếng Việt.
  Future<bool> initialize() async {
    if (_initialized) return _available && _localeId != null;
    _initialized = true;

    try {
      _available = await _speech.initialize(
        onError: _handleError,
        onStatus: _handleStatus,
      );
    } catch (_) {
      _available = false;
    }
    if (!_available) return false;

    final locales = await _speech.locales();
    for (final locale in locales) {
      if (locale.localeId.toLowerCase().startsWith('vi')) {
        _localeId = locale.localeId;
        break;
      }
    }
    return _localeId != null;
  }

  /// Nghe một câu tiếng Việt. [onPartial] nhận chữ tạm thời để hiển thị dần trên UI.
  /// [requireOnDevice] = true buộc nhận dạng trên máy (cần gói tiếng Việt offline).
  ///
  /// Ném [OnDeviceSttException] nếu không nhận được chữ nào.
  Future<String> listenOnce({
    void Function(String partialText)? onPartial,
    bool requireOnDevice = false,
  }) async {
    if (!_initialized) await initialize();
    if (!_available) {
      throw const OnDeviceSttException(OnDeviceSttErrorCode.unavailable);
    }
    final localeId = _localeId;
    if (localeId == null) {
      throw const OnDeviceSttException(OnDeviceSttErrorCode.languageUnavailable);
    }
    if (_speech.isListening || (_pending != null && !_pending!.isCompleted)) {
      throw const OnDeviceSttException(OnDeviceSttErrorCode.busy);
    }

    final pending = Completer<String>();
    _pending = pending;
    _onPartial = onPartial;

    try {
      await _speech.listen(
        onResult: _handleResult,
        listenOptions: SpeechListenOptions(
          localeId: localeId,
          onDevice: requireOnDevice,
          partialResults: true,
          listenMode: ListenMode.dictation,
          cancelOnError: true,
          pauseFor: pauseFor,
          listenFor: listenFor,
        ),
      );
    } catch (_) {
      _fail(OnDeviceSttErrorCode.unknown, 'listen_failed');
    }
    return pending.future;
  }

  /// Người dùng bấm "Nói xong": dừng nghe, bộ nhận dạng trả kết quả cuối.
  Future<void> stop() => _speech.stop();

  /// Người dùng hủy lượt nói: bỏ kết quả.
  Future<void> cancel() async {
    await _speech.cancel();
    _fail(OnDeviceSttErrorCode.cancelled);
  }

  void _handleResult(SpeechRecognitionResult result) {
    final text = result.recognizedWords.trim();
    if (!result.finalResult) {
      if (text.isNotEmpty) _onPartial?.call(text);
      return;
    }
    if (text.isEmpty) {
      _fail(OnDeviceSttErrorCode.noSpeech, 'empty_final_result');
    } else {
      _complete(text);
    }
  }

  void _handleStatus(String status) {
    // Plugin báo `doneNoResult` khi kết thúc mà không có chữ (thường đi kèm error_no_match).
    if (status == 'doneNoResult') {
      _fail(OnDeviceSttErrorCode.noSpeech, status);
    }
  }

  void _handleError(SpeechRecognitionError error) {
    _fail(_mapError(error.errorMsg), error.errorMsg);
  }

  static OnDeviceSttErrorCode _mapError(String errorMsg) {
    switch (errorMsg) {
      case 'error_no_match':
      case 'error_speech_timeout':
        return OnDeviceSttErrorCode.noSpeech;
      case 'error_network':
      case 'error_network_timeout':
      case 'error_server':
      case 'error_server_disconnected':
        return OnDeviceSttErrorCode.network;
      case 'error_language_unavailable':
      case 'error_language_not_supported':
        return OnDeviceSttErrorCode.languageUnavailable;
      case 'error_permission':
      case 'error_insufficient_permissions':
        return OnDeviceSttErrorCode.permissionDenied;
      case 'error_busy':
      case 'error_recognizer_busy':
        return OnDeviceSttErrorCode.busy;
      default:
        return OnDeviceSttErrorCode.unknown;
    }
  }

  void _complete(String text) {
    final pending = _pending;
    if (pending != null && !pending.isCompleted) pending.complete(text);
    _onPartial = null;
  }

  void _fail(OnDeviceSttErrorCode code, [String detail = '']) {
    final pending = _pending;
    if (pending != null && !pending.isCompleted) {
      pending.completeError(OnDeviceSttException(code, detail));
    }
    _onPartial = null;
  }
}
