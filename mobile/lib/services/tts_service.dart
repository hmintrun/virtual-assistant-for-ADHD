// Service đọc câu của trợ lý thành giọng nói (Role Voice): check-in UF5, nhắc UF3, động viên UF6.
//
// Luồng dự phòng (đã chốt): Gemini TTS qua Backend -> flutter_tts trên máy -> chỉ hiện chữ.
//   1. Câu đã đọc trước đó (bộ nhớ đệm trong máy)  -> phát lại, không tốn lượt gọi.
//   2. Gọi Backend (Gemini TTS, trả WAV)            -> phát + lưu đệm.
//   3. flutter_tts (bộ đọc của máy, chạy offline)   -> khi Backend lỗi / hết lượt / mất mạng.
//   4. Không đọc được                               -> trả SpeechRoute.textOnly để UI chỉ hiện chữ
//      (doc UF5: "cho trả lời chữ khi voice lỗi").
//
// Ghi chú:
// - Free tier Gemini TTS chỉ ~10 lượt/ngày nên bước 3 sẽ chạy khá thường xuyên.
// - Backend CHƯA có endpoint TTS (API Sheet chưa có dòng). Khi Backend Lead chốt, Mobile truyền
//   hàm gọi API vào tham số `fetchRemote`; trước đó để null -> dùng thẳng flutter_tts.
// - UF3: nếu đang trong một phiên khác thì KHÔNG phát TTS -> gọi speak(..., allowSpeech: false).
// - Android 11+: AndroidManifest cần <queries><intent><action
//   android:name="android.intent.action.TTS_SERVICE"/></intent></queries> (intent RIÊNG).

import 'dart:async';
import 'dart:collection';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_tts/flutter_tts.dart';

/// Hàm lấy audio WAV từ Backend cho một câu. Ném lỗi nếu Backend không trả được audio.
typedef RemoteSpeechFetcher = Future<Uint8List> Function(String text);

/// Cách câu đã được phát — để UI/analytics biết đang dùng tầng nào.
enum SpeechRoute {
  /// Phát lại audio Gemini đã lưu đệm (không tốn lượt gọi).
  remoteCached,

  /// Vừa gọi Backend (Gemini TTS) thành công.
  remote,

  /// Bộ đọc của máy (flutter_tts).
  onDevice,

  /// Không phát tiếng (bị chặn theo ngữ cảnh hoặc mọi tầng đều lỗi) -> UI chỉ hiện chữ.
  textOnly,
}

class TtsService {
  TtsService({
    RemoteSpeechFetcher? fetchRemote,
    FlutterTts? onDeviceTts,
    AudioPlayer? player,
    this.remoteTimeout = const Duration(seconds: 15),
    this.cacheSize = 32,
    this.onDeviceSpeechRate = 0.58,
    String? onDeviceVoiceName,
  })  : _fetchRemote = fetchRemote,
        _tts = onDeviceTts ?? FlutterTts(),
        _player = player ?? AudioPlayer(),
        _voiceName = onDeviceVoiceName;

  final RemoteSpeechFetcher? _fetchRemote;
  final FlutterTts _tts;
  final AudioPlayer _player;

  /// Chờ Backend tối đa bao lâu trước khi chuyển sang flutter_tts.
  final Duration remoteTimeout;

  /// Số câu giữ trong bộ nhớ đệm (audio WAV của Gemini).
  final int cacheSize;

  /// Tốc độ đọc của flutter_tts trên Android (0.5 = bình thường, 0.58 = nhanh hơn một chút).
  final double onDeviceSpeechRate;

  /// Tên giọng flutter_tts đang chọn (null = giọng tiếng Việt mặc định của máy).
  String? _voiceName;

  final LinkedHashMap<String, Uint8List> _cache = LinkedHashMap();
  bool _onDeviceReady = false;
  bool _onDeviceAvailable = false;

  /// Các giọng tiếng Việt máy đang có, dạng {name, locale, network_required, ...}.
  /// Dùng cho màn hình Cài đặt (UF7) để người dùng chọn giọng.
  /// `network_required == 'true'` nghĩa là giọng đó cần mạng, không dùng được khi offline.
  Future<List<Map<String, String>>> vietnameseVoices() async {
    try {
      final voices = (await _tts.getVoices) as List<dynamic>? ?? const [];
      return voices
          .map((v) => Map<String, String>.from(
              (v as Map).map((k, val) => MapEntry('$k', '$val'))))
          .where((v) => (v['locale'] ?? '').toLowerCase().startsWith('vi'))
          .toList();
    } catch (_) {
      return const [];
    }
  }

  /// Đổi giọng flutter_tts. UF7: chỉ áp dụng từ lần đọc sau, không cắt câu đang đọc.
  /// Truyền null để quay về giọng mặc định của máy.
  Future<void> setOnDeviceVoice(String? voiceName) async {
    _voiceName = voiceName;
    if (_onDeviceReady && _onDeviceAvailable) await _applyVoice();
  }

  Future<void> _applyVoice() async {
    final name = _voiceName;
    if (name == null) return;
    final match = (await vietnameseVoices()).where((v) => v['name'] == name);
    if (match.isEmpty) return; // Giọng không còn trên máy -> giữ giọng mặc định.
    final voice = match.first;
    await _tts.setVoice({'name': voice['name']!, 'locale': voice['locale']!});
  }

  /// Đọc [text]. Trả về tầng đã dùng. Chờ đến khi đọc xong (để UF5 có thể nghe trả lời ngay sau).
  ///
  /// [allowSpeech] = false khi ngữ cảnh không cho phát tiếng (vd đang trong phiên khác — UF3).
  Future<SpeechRoute> speak(String text, {bool allowSpeech = true}) async {
    final cleaned = text.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (cleaned.isEmpty || !allowSpeech) return SpeechRoute.textOnly;

    await stop();

    final cached = _cache.remove(cleaned);
    if (cached != null) {
      _cache[cleaned] = cached; // đưa lên mới nhất
      if (await _playWav(cached)) return SpeechRoute.remoteCached;
    }

    final fetch = _fetchRemote;
    if (fetch != null) {
      try {
        final audio = await fetch(cleaned).timeout(remoteTimeout);
        if (audio.isNotEmpty) {
          _remember(cleaned, audio);
          if (await _playWav(audio)) return SpeechRoute.remote;
        }
      } catch (_) {
        // Hết lượt (429) / timeout / mất mạng / chưa có endpoint -> xuống tầng flutter_tts.
      }
    }

    if (await _speakOnDevice(cleaned)) return SpeechRoute.onDevice;
    return SpeechRoute.textOnly;
  }

  /// Dừng mọi âm thanh đang phát (vd người dùng bấm trả lời check-in giữa chừng).
  Future<void> stop() async {
    try {
      await _player.stop();
    } catch (_) {}
    try {
      await _tts.stop();
    } catch (_) {}
  }

  Future<void> dispose() async {
    await stop();
    await _player.dispose();
  }

  void _remember(String text, Uint8List audio) {
    if (cacheSize <= 0) return;
    _cache.remove(text);
    _cache[text] = audio;
    while (_cache.length > cacheSize) {
      _cache.remove(_cache.keys.first);
    }
  }

  Future<bool> _playWav(Uint8List audio) async {
    try {
      final finished = _player.onPlayerStateChanged.firstWhere(
        (s) => s == PlayerState.completed || s == PlayerState.stopped,
      );
      await _player.play(BytesSource(audio, mimeType: 'audio/wav'));
      // Câu của trợ lý ngắn; giới hạn chờ để không treo nếu không nhận được sự kiện kết thúc.
      await finished.timeout(const Duration(seconds: 60), onTimeout: () => PlayerState.stopped);
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _ensureOnDevice() async {
    if (_onDeviceReady) return;
    _onDeviceReady = true;
    try {
      final available = await _tts.isLanguageAvailable('vi-VN');
      _onDeviceAvailable = available == true || available == 1;
      if (!_onDeviceAvailable) return;
      await _tts.setLanguage('vi-VN');
      await _tts.setSpeechRate(onDeviceSpeechRate);
      await _applyVoice();
      await _tts.awaitSpeakCompletion(true);
    } catch (_) {
      _onDeviceAvailable = false;
    }
  }

  Future<bool> _speakOnDevice(String text) async {
    await _ensureOnDevice();
    if (!_onDeviceAvailable) return false;
    try {
      final result = await _tts.speak(text);
      return result == 1 || result == true;
    } catch (_) {
      return false;
    }
  }
}
