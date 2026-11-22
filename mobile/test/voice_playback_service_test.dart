import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/services/voice_playback_service.dart';

class _FakeSink implements VoiceAudioSink {
  final played = <Uint8List>[];
  bool fail = false;
  int stops = 0;

  @override
  Future<void> playBytes(Uint8List bytes, {String? mimeType}) async {
    if (fail) throw Exception('decoder');
    played.add(bytes);
  }

  @override
  Future<void> stop() async => stops++;
}

class _FakeDevice implements DeviceSpeaker {
  _FakeDevice({this.hasVietnamese = true});
  final bool hasVietnamese;
  final spoken = <String>[];
  int stops = 0;
  Completer<void>? hold; // giữ "đang đọc" để kiểm isSpeaking

  @override
  Future<bool> speak(String text) async {
    if (!hasVietnamese) return false;
    spoken.add(text);
    await hold?.future;
    return true;
  }

  @override
  Future<void> stop() async => stops++;
}

final _audio = base64Encode(utf8.encode('mp3-bytes'));

void main() {
  late _FakeSink sink;
  late _FakeDevice device;
  late MemoryTtsAudioCache cache;

  setUp(() {
    sink = _FakeSink();
    device = _FakeDevice();
    cache = MemoryTtsAudioCache();
  });

  VoicePlaybackService service(
    VoicePost post, {
    DeviceSpeaker? speaker,
    Duration timeout = const Duration(milliseconds: 50),
  }) =>
      VoicePlaybackService(
        post: post,
        audioSink: sink,
        deviceSpeaker: speaker ?? device,
        cache: cache,
        backendTimeout: timeout,
      );

  group('speakText', () {
    test('Ca 1 — online: phát audio Edge-TTS từ Backend và lưu cache',
        () async {
      String? calledPath;
      final result = await service((path, body) async {
        calledPath = path;
        expect(body['text'], 'Bắt đầu phiên nhé');
        return {'audio_base64': _audio, 'mime_type': 'audio/mpeg'};
      }).speakText('  Bắt đầu   phiên nhé ');

      expect(calledPath, '/voice/tts');
      expect(result.source, VoicePlaybackSource.backend);
      expect(result.fromCache, isFalse);
      expect(utf8.decode(sink.played.single), 'mp3-bytes');
      expect(
        await cache.get(VoicePlaybackService.cacheKey('Bắt đầu phiên nhé')),
        isNotNull,
      );
    });

    test('Ca 2a — Backend 503 → đọc bằng flutter_tts trên máy', () async {
      final result = await service((path, body) async {
        throw const ApiException('Edge-TTS lỗi',
            code: 'TTS_UNAVAILABLE', statusCode: 503);
      }).speakText('Nghỉ 5 phút nhé');

      expect(result.source, VoicePlaybackSource.device);
      expect(result.reason, 'TTS_UNAVAILABLE');
      expect(device.spoken, ['Nghỉ 5 phút nhé']);
    });

    test('Ca 2b — Backend chậm quá mốc hedge → đọc bằng máy ngay', () async {
      final never = Completer<Map<String, dynamic>>();
      final result =
          await service((path, body) => never.future).speakText('Xin chào');

      expect(result.source, VoicePlaybackSource.device);
      expect(result.reason, 'timeout');
    });

    test('Ca 2c — audio rỗng hoặc phát lỗi → rơi xuống máy', () async {
      final empty = await service((path, body) async => {'audio_base64': ''})
          .speakText('A');
      expect(empty.source, VoicePlaybackSource.device);

      sink.fail = true;
      final broken =
          await service((path, body) async => {'audio_base64': _audio})
              .speakText('B');
      expect(broken.source, VoicePlaybackSource.device);
      expect(broken.reason, 'playback_failed');
    });

    test('Ca 3 — máy thiếu gói vi-VN → chỉ hiện chữ, không crash', () async {
      final result = await service(
        (path, body) async => throw const ApiException('down', statusCode: 503),
        speaker: _FakeDevice(hasVietnamese: false),
      ).speakText('Bạn làm tới đâu rồi?');

      expect(result.source, VoicePlaybackSource.textOnly);
      expect(result.reason, 'no_vi_voice');
      expect(result.text, 'Bạn làm tới đâu rồi?');
      expect(result.spoke, isFalse);
    });

    test('Ca 4 — bấm mic giữa chừng: stop() hủy, audio về muộn không phát',
        () async {
      final pending = Completer<Map<String, dynamic>>();
      final svc = service(
        (path, body) => pending.future,
        timeout: const Duration(seconds: 5),
      );
      final future = svc.speakText('Câu dài');
      expect(svc.isSpeaking, isTrue);

      await svc.stop(); // người dùng bấm giữ mic
      pending.complete({'audio_base64': _audio});
      final result = await future;

      expect(result.source, VoicePlaybackSource.cancelled);
      expect(sink.played, isEmpty);
      expect(device.spoken, isEmpty);
      expect(svc.isSpeaking, isFalse);
      expect(sink.stops, greaterThan(0));
      expect(device.stops, greaterThan(0));
    });

    test('isSpeaking vẫn true trong lúc máy đang đọc dự phòng', () async {
      device.hold = Completer<void>();
      final svc = service((path, body) async => throw const ApiException('x'));
      final future = svc.speakText('Đang đọc');
      await Future<void>.delayed(const Duration(milliseconds: 10));
      expect(device.spoken, ['Đang đọc']);
      expect(svc.isSpeaking, isTrue);
      device.hold!.complete();
      await future;
      expect(svc.isSpeaking, isFalse);
    });

    test('chữ rỗng → không gọi Backend', () async {
      var called = false;
      final result = await service((path, body) async {
        called = true;
        return {};
      }).speakText('   ');
      expect(called, isFalse);
      expect(result.source, VoicePlaybackSource.textOnly);
    });
  });

  group('tối ưu thời gian', () {
    test('cache: câu đã nghe phát lại KHÔNG gọi mạng (kể cả offline)',
        () async {
      var calls = 0;
      final svc = service((path, body) async {
        calls++;
        if (calls > 1) throw const ApiException('offline');
        return {'audio_base64': _audio, 'mime_type': 'audio/mpeg'};
      });
      await svc.speakText('Phiên vừa rồi thế nào?');
      final again = await svc.speakText('Phiên vừa rồi thế nào?');

      expect(calls, 1);
      expect(again.source, VoicePlaybackSource.backend);
      expect(again.fromCache, isTrue);
      expect(sink.played, hasLength(2));
    });

    test('hedge: Backend chậm → đọc bằng máy, audio về sau vẫn vào cache',
        () async {
      final slow = Completer<Map<String, dynamic>>();
      var calls = 0;
      final svc = service((path, body) {
        calls++;
        return slow.future;
      });
      final first = await svc.speakText('Hết giờ nghỉ rồi');
      expect(first.source, VoicePlaybackSource.device);

      slow.complete({'audio_base64': _audio, 'mime_type': 'audio/mpeg'});
      await Future<void>.delayed(Duration.zero);

      final second = await svc.speakText('Hết giờ nghỉ rồi');
      expect(second.fromCache, isTrue);
      expect(calls, 1);
    });

    test('prefetchText rồi speakText: chỉ 1 lần gọi mạng, phát từ cache',
        () async {
      var calls = 0;
      final svc = service((path, body) async {
        calls++;
        return {'audio_base64': _audio, 'mime_type': 'audio/mpeg'};
      });
      expect(await svc.prefetchText('Bắt đầu đếm giờ 25p'), isTrue);
      final result = await svc.speakText('Bắt đầu đếm giờ 25p');
      expect(calls, 1);
      expect(result.fromCache, isTrue);
    });

    test('prefetch và speak cùng lúc dùng chung 1 request', () async {
      final gate = Completer<Map<String, dynamic>>();
      var calls = 0;
      final svc = service(
        (path, body) {
          calls++;
          return gate.future;
        },
        timeout: const Duration(seconds: 5),
      );
      final pre = svc.prefetchText('Câu A');
      final speak = svc.speakText('Câu A');
      gate.complete({'audio_base64': _audio, 'mime_type': 'audio/mpeg'});
      await pre;
      final result = await speak;
      expect(calls, 1);
      expect(result.source, VoicePlaybackSource.backend);
    });

    test('khóa cache ổn định và phân biệt giọng/câu', () {
      final a = VoicePlaybackService.cacheKey('Xin chào');
      expect(a, VoicePlaybackService.cacheKey('Xin chào'));
      expect(a, hasLength(16));
      expect(a, isNot(VoicePlaybackService.cacheKey('Xin chao')));
      expect(a, isNot(VoicePlaybackService.cacheKey('Xin chào', voice: 'x')));
    });

    test('MemoryTtsAudioCache bỏ mục cũ nhất khi đầy', () async {
      final small = MemoryTtsAudioCache(maxEntries: 2);
      final audio = CachedTtsAudio(Uint8List(1), 'audio/mpeg');
      await small.put('a', audio);
      await small.put('b', audio);
      await small.get('a'); // a vừa dùng → b cũ nhất
      await small.put('c', audio);
      expect(await small.get('b'), isNull);
      expect(await small.get('a'), isNotNull);
    });

    test('FileTtsAudioCache ghi/đọc lại từ đĩa sau khi mở app lại', () async {
      final tmp = await Directory.systemTemp.createTemp('tts_cache_test');
      addTearDown(() => tmp.delete(recursive: true));
      final audio =
          CachedTtsAudio(Uint8List.fromList(utf8.encode('mp3')), 'audio/mpeg');
      await FileTtsAudioCache(dir: () async => tmp).put('k1', audio);

      final reopened = FileTtsAudioCache(dir: () async => tmp);
      final hit = await reopened.get('k1');
      expect(utf8.decode(hit!.bytes), 'mp3');
      expect(hit.mimeType, 'audio/mpeg');
    });
  });

  group('speakCheckInPrompt', () {
    Map<String, dynamic> promptResponse({String? audio}) => {
          'prompt': {'prompt_text': 'Phiên vừa rồi thế nào?', 'kind': 'final'},
          'audio_base64': audio,
          'audio_mime_type': 'audio/mpeg',
          'fallback_to_device_tts': audio == null,
        };

    test('Backend trả audio: phát audio, trả nguyên prompt cho UI', () async {
      final result = await service((path, body) async {
        expect(path, '/voice/uf5-checkin-prompt');
        expect(body['kind'], 'final');
        expect(body['include_audio'], isTrue);
        return promptResponse(audio: _audio);
      }).speakCheckInPrompt(fallbackText: 'Bạn làm tới đâu rồi?');

      expect(result.promptText, 'Phiên vừa rồi thế nào?');
      expect(result.prompt['kind'], 'final');
      expect(result.playback.source, VoicePlaybackSource.backend);
    });

    test('prefetch trong lúc phiên chạy → hết giờ phát tức thì, 0 gọi mạng',
        () async {
      var calls = 0;
      final svc = service((path, body) async {
        calls++;
        return promptResponse(audio: _audio);
      });
      expect(
          await svc.prefetchCheckInPrompt(isFinalSessionOfStep: true), isTrue);
      expect(calls, 1);

      final result = await svc.speakCheckInPrompt(
        fallbackText: 'Bạn làm tới đâu rồi?',
        isFinalSessionOfStep: true,
      );
      expect(calls, 1); // không gọi thêm
      expect(result.playback.fromCache, isTrue);
      expect(result.promptText, 'Phiên vừa rồi thế nào?');
    });

    test('fallback_to_device_tts = true → đọc câu Backend bằng máy', () async {
      final result = await service((path, body) async => promptResponse())
          .speakCheckInPrompt(fallbackText: 'Bạn làm tới đâu rồi?');

      expect(result.playback.source, VoicePlaybackSource.device);
      expect(result.playback.reason, 'fallback_to_device_tts');
      expect(device.spoken, ['Phiên vừa rồi thế nào?']);
    });

    test(
        'prompt_text rỗng → đọc câu dự phòng bằng máy (không phát audio lệch chữ)',
        () async {
      final result = await service((path, body) async => {
            'prompt': {'prompt_text': ''},
            'audio_base64': _audio,
          }).speakCheckInPrompt(fallbackText: 'Bạn làm tới đâu rồi?');

      expect(sink.played, isEmpty);
      expect(result.promptText, 'Bạn làm tới đâu rồi?');
      expect(result.playback.source, VoicePlaybackSource.device);
    });

    test('mất mạng → dùng câu dự phòng của UI, vẫn đọc bằng máy', () async {
      final result = await service(
        (path, body) async => throw const ApiException('offline'),
      ).speakCheckInPrompt(fallbackText: 'Bạn làm tới đâu rồi?');

      expect(result.prompt, isEmpty);
      expect(result.promptText, 'Bạn làm tới đâu rồi?');
      expect(result.playback.source, VoicePlaybackSource.device);
    });
  });
}
