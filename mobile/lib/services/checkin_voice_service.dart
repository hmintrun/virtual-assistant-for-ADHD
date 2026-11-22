import 'dart:convert';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_tts/flutter_tts.dart';

import '../core/api_client.dart';

/// Plays the UF5 two-way final check-in prompt via backend Edge-TTS audio
/// and falls back cleanly to on-device Vietnamese TTS when needed.
class CheckInVoiceService {
  CheckInVoiceService({
    ApiClient? apiClient,
    AudioPlayer? player,
    FlutterTts? tts,
  })  : _api = apiClient,
        _player = player,
        _tts = tts;

  static const String _defaultPromptText =
      'Phiên tập trung vừa xong rồi. Bạn thấy tiến độ thế nào?';

  final ApiClient? _api;
  AudioPlayer? _player;
  FlutterTts? _tts;
  bool _disposed = false;

  Future<void> speakFinalPrompt(String runId) async {
    if (_disposed) return;
    var promptText = _defaultPromptText;
    try {
      final api = _api ?? ApiClient();
      final data = await api.postData(
        '/voice/uf5-checkin-prompt',
        idempotencyKey: ApiClient.stableIdempotencyKey(
          'uf5_checkin_prompt',
          runId,
        ),
        data: const {
          'kind': 'final',
          'include_audio': true,
        },
      );
      if (_disposed) return;

      final prompt = data['prompt'];
      if (prompt is Map) {
        final rawText = prompt['prompt_text'];
        if (rawText is String && rawText.trim().isNotEmpty) {
          promptText = rawText.trim();
        }
      }

      final audioBase64 = data['audio_base64'];
      final fallbackToDevice = data['fallback_to_device_tts'] == true;
      if (!fallbackToDevice && audioBase64 is String && audioBase64.isNotEmpty) {
        final bytes = base64Decode(audioBase64);
        final player = _player ??= AudioPlayer();
        await player.stop();
        if (_disposed) return;
        await player.play(BytesSource(bytes));
        return;
      }
    } catch (_) {
      // Fall back quietly to on-device TTS so UF5 check-in never blocks.
    }

    await _speakOnDevice(promptText);
  }

  Future<void> _speakOnDevice(String text) async {
    if (_disposed) return;
    try {
      final tts = _tts ??= FlutterTts();
      await tts.setLanguage('vi-VN');
      await tts.setSpeechRate(0.48);
      if (_disposed) return;
      await tts.speak(text);
    } catch (_) {
      // Ignore missing platform TTS plugin on web/headless test environments.
    }
  }

  Future<void> dispose() async {
    _disposed = true;
    try {
      await _player?.stop();
      await _player?.dispose();
    } catch (_) {}
    try {
      await _tts?.stop();
    } catch (_) {}
  }
}
