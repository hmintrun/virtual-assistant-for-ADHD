import 'dart:convert';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_tts/flutter_tts.dart';

import '../core/api_client.dart';

/// Plays the UF5 check-in prompt and general TTS audio via backend Edge-TTS,
/// falling back cleanly to on-device Vietnamese TTS when needed.
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

  Future<void> speakFinalPrompt(
    String runId, {
    bool isFinalSessionOfStep = false,
    String? expectedOutput,
    String? voice,
  }) async {
    if (_disposed) return;
    var promptText = _defaultPromptText;
    final trimmedOutput = expectedOutput?.trim();
    try {
      final api = _api ?? ApiClient();
      final data = await api.postData(
        '/voice/uf5-checkin-prompt',
        idempotencyKey: ApiClient.stableIdempotencyKey(
          'uf5_checkin_prompt',
          '$runId:${isFinalSessionOfStep ? 1 : 0}:${trimmedOutput ?? ''}',
        ),
        data: {
          'kind': 'final',
          'is_final_session_of_step': isFinalSessionOfStep,
          if (trimmedOutput != null && trimmedOutput.isNotEmpty)
            'expected_output': trimmedOutput,
          'include_audio': true,
          if (voice != null && voice.trim().isNotEmpty) 'voice': voice.trim(),
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

      await _playAudioOrFallback(
        data['audio_base64'] as String?,
        promptText,
        fallbackFlag: data['fallback_to_device_tts'] == true,
      );
      return;
    } catch (_) {
      // Fall back quietly to on-device TTS so UF5 check-in never blocks.
    }

    await _speakOnDevice(promptText);
  }

  /// Synthesizes arbitrary short Vietnamese feedback via `POST /voice/tts`
  /// with on-device TTS fallback.
  Future<void> speakText(String text, {String? voice}) async {
    final trimmed = text.trim();
    if (_disposed || trimmed.isEmpty) return;
    try {
      final api = _api ?? ApiClient();
      final data = await api.postData(
        '/voice/tts',
        idempotencyKey: ApiClient.stableIdempotencyKey('tts', trimmed),
        data: {
          'text': trimmed,
          if (voice != null && voice.trim().isNotEmpty) 'voice': voice.trim(),
        },
      );
      if (_disposed) return;
      await _playAudioOrFallback(data['audio_base64'] as String?, trimmed);
      return;
    } catch (_) {
      // Fall back quietly to on-device TTS.
    }

    await _speakOnDevice(trimmed);
  }

  Future<void> _playAudioOrFallback(
    String? audioBase64,
    String fallbackText, {
    bool fallbackFlag = false,
  }) async {
    if (_disposed) return;
    if (!fallbackFlag && audioBase64 != null && audioBase64.isNotEmpty) {
      final bytes = base64Decode(audioBase64);
      final player = _player ??= AudioPlayer();
      await player.stop();
      if (_disposed) return;
      await player.play(BytesSource(bytes));
      return;
    }
    await _speakOnDevice(fallbackText);
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
