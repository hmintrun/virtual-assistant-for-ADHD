// Phát giọng đọc cho UF5 (Voice Check-in) và các câu nhắc ngắn (MOB-05, W4-10).
//
// Tầng 1: Backend Edge-TTS (`POST /voice/tts`, `POST /voice/uf5-checkin-prompt`).
// Tầng 2: `flutter_tts` trên máy khi Backend trả 503/504, chậm quá mốc hedge,
//          mất mạng, hoặc trả `fallback_to_device_tts = true`.
// Tầng 3: Chỉ hiện chữ khi máy không có giọng đọc tiếng Việt.
//
// Tối ưu thời gian (đo 23/11: Edge-TTS ~0,6s/câu nhưng có đợt chậm 4–10s khi bị gọi dồn dập):
// - Cache audio trên máy theo (giọng, câu) → câu đã nghe phát ngay, cả khi offline.
// - [VoicePlaybackService.prefetchCheckInPrompt] / [VoicePlaybackService.prefetchText]:
//   tải sẵn trong lúc phiên đang chạy → lúc hỏi Check-in phát tức thì.
// - Hedge: Backend chậm hơn [VoicePlaybackService.backendTimeout] thì đọc bằng máy
//   ngay, nhưng request vẫn chạy tiếp để lưu cache cho lần sau.
// - Request trùng (prefetch + speak cùng câu) dùng chung 1 lần gọi mạng.
//
// Quy tắc: TTS và mic không bao giờ chạy chồng nhau — gọi [VoicePlaybackService.stop]
// trước khi mở mic; audio về muộn sau khi đã stop sẽ bị bỏ, không phát.
import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_tts/flutter_tts.dart';
import 'package:path_provider/path_provider.dart';

import '../core/api_client.dart';

/// Nguồn thực sự đã đọc câu.
enum VoicePlaybackSource {
  /// Edge-TTS từ Backend (trực tiếp hoặc từ cache trên máy).
  backend,

  /// flutter_tts trên máy.
  device,

  /// Không đọc được (máy thiếu giọng vi-VN) → UI chỉ hiện chữ.
  textOnly,

  /// Bị hủy giữa chừng (người dùng bấm mic / rời màn hình).
  cancelled,
}

class VoicePlaybackResult {
  const VoicePlaybackResult(
    this.source, {
    required this.text,
    this.reason,
    this.fromCache = false,
  });

  final VoicePlaybackSource source;

  /// Câu đã (hoặc lẽ ra đã) được đọc — UI luôn hiện chữ này song song.
  final String text;

  /// Lý do rơi xuống tầng dưới (để log), vd. `TTS_UNAVAILABLE`, `timeout`.
  final String? reason;

  /// true → audio lấy từ cache trên máy, không chờ mạng.
  final bool fromCache;

  bool get spoke =>
      source == VoicePlaybackSource.backend ||
      source == VoicePlaybackSource.device;
}

/// Kết quả câu hỏi Check-in UF5: nội dung câu hỏi từ Backend + cách đã đọc.
class CheckInPromptPlayback {
  const CheckInPromptPlayback({required this.prompt, required this.playback});

  /// Nguyên văn `data.prompt` của Backend (prompt_text, chip...). Rỗng nếu
  /// Backend lỗi/chậm và đã dùng câu dự phòng của UI.
  final Map<String, dynamic> prompt;
  final VoicePlaybackResult playback;

  String get promptText => playback.text;
}

/// Phát audio bytes (mặc định: audioplayers). Tách interface để test.
abstract class VoiceAudioSink {
  /// Hoàn thành khi phát xong HOẶC khi [stop] được gọi.
  Future<void> playBytes(Uint8List bytes, {String? mimeType});
  Future<void> stop();
}

/// Đọc chữ bằng giọng trên máy (mặc định: flutter_tts). Tách interface để test.
abstract class DeviceSpeaker {
  /// Trả về false nếu máy không có giọng `vi-VN` (khi đó không đọc gì).
  Future<bool> speak(String text);
  Future<void> stop();
}

/// Gọi Backend, trả `data` của envelope. Mặc định dùng [ApiClient.postData].
typedef VoicePost = Future<Map<String, dynamic>> Function(
  String path,
  Map<String, dynamic> body,
);

/// Audio đã tổng hợp, lưu lại để phát lại không cần mạng.
class CachedTtsAudio {
  const CachedTtsAudio(this.bytes, this.mimeType);
  final Uint8List bytes;
  final String? mimeType;
}

/// Kho audio TTS theo khóa (giọng + câu). Tách interface để test.
abstract class TtsAudioCache {
  Future<CachedTtsAudio?> get(String key);
  Future<void> put(String key, CachedTtsAudio audio);
}

/// Cache trong RAM, LRU. Dùng cho test và làm lớp nhanh phía trước cache file.
class MemoryTtsAudioCache implements TtsAudioCache {
  MemoryTtsAudioCache({this.maxEntries = 32});
  final int maxEntries;
  final LinkedHashMap<String, CachedTtsAudio> _items = LinkedHashMap();

  @override
  Future<CachedTtsAudio?> get(String key) async {
    final hit = _items.remove(key);
    if (hit != null) _items[key] = hit; // đưa lên cuối (mới dùng)
    return hit;
  }

  @override
  Future<void> put(String key, CachedTtsAudio audio) async {
    _items.remove(key);
    _items[key] = audio;
    while (_items.length > maxEntries) {
      _items.remove(_items.keys.first);
    }
  }
}

/// Cache trong thư mục cache của app (Android tự dọn khi thiếu bộ nhớ) +
/// lớp RAM phía trước. Giữ tối đa [maxFiles] file và [maxTotalBytes] bytes,
/// tự dọn file rỗng hoặc cũ hơn [maxAge].
class FileTtsAudioCache implements TtsAudioCache {
  FileTtsAudioCache({
    this.maxFiles = 80,
    this.maxTotalBytes = defaultMaxTotalBytes,
    Future<Directory> Function()? dir,
  }) : _dirProvider = dir ?? _defaultDir;

  static const int defaultMaxTotalBytes = 8 * 1024 * 1024;

  final int maxFiles;
  final int maxTotalBytes;
  final Future<Directory> Function() _dirProvider;
  final MemoryTtsAudioCache _memory = MemoryTtsAudioCache(maxEntries: 16);
  Directory? _dir;

  static Future<Directory> _defaultDir() async =>
      Directory('${(await getApplicationCacheDirectory()).path}/tts_audio');

  static const Map<String, String> _extOf = {
    'audio/mpeg': 'mp3',
    'audio/mp3': 'mp3',
    'audio/wav': 'wav',
  };

  /// Dọn file cache rỗng, quá hạn [maxAge], hoặc vượt ngưỡng [maxFiles] / [maxTotalBytes].
  static Future<int> pruneCache({
    int maxFiles = 80,
    int maxTotalBytes = defaultMaxTotalBytes,
    Duration maxAge = const Duration(days: 14),
    Directory? directoryOverride,
    DateTime? nowOverride,
  }) async {
    if (kIsWeb && directoryOverride == null) return 0;
    try {
      final dir = directoryOverride ?? await _defaultDir();
      if (!await dir.exists()) return 0;
      return await _pruneDirectory(
        dir,
        maxFiles: maxFiles,
        maxTotalBytes: maxTotalBytes,
        maxAge: maxAge,
        now: nowOverride ?? DateTime.now(),
      );
    } catch (_) {
      return 0;
    }
  }

  static Future<int> _pruneDirectory(
    Directory dir, {
    required int maxFiles,
    required int maxTotalBytes,
    Duration? maxAge,
    DateTime? now,
  }) async {
    var deleted = 0;
    final cutoff =
        maxAge != null ? (now ?? DateTime.now()).subtract(maxAge) : null;
    final kept = <({File file, DateTime modified, int size})>[];

    await for (final entity in dir.list(followLinks: false)) {
      if (entity is! File) continue;
      try {
        final stat = await entity.stat();
        if (stat.size == 0 ||
            (cutoff != null && stat.modified.isBefore(cutoff))) {
          await entity.delete();
          deleted++;
          continue;
        }
        kept.add((file: entity, modified: stat.modified, size: stat.size));
      } catch (_) {
        // Bỏ qua file lỗi truy cập để không làm gián đoạn phát audio.
      }
    }

    var totalBytes = kept.fold<int>(0, (sum, entry) => sum + entry.size);
    if (kept.length <= maxFiles && totalBytes <= maxTotalBytes) {
      return deleted;
    }

    kept.sort((a, b) => a.modified.compareTo(b.modified));
    var remainingCount = kept.length;
    for (final entry in kept) {
      if (remainingCount <= maxFiles && totalBytes <= maxTotalBytes) break;
      try {
        await entry.file.delete();
        deleted++;
        remainingCount--;
        totalBytes -= entry.size;
      } catch (_) {
        // Bỏ qua lỗi xóa file đơn lẻ.
      }
    }
    return deleted;
  }

  Future<Directory> _ensureDir() async {
    final dir = _dir ??= await _dirProvider();
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  @override
  Future<CachedTtsAudio?> get(String key) async {
    final mem = await _memory.get(key);
    if (mem != null) return mem;
    try {
      final dir = await _ensureDir();
      for (final (ext, mime) in const [
        ('mp3', 'audio/mpeg'),
        ('wav', 'audio/wav')
      ]) {
        final file = File('${dir.path}/$key.$ext');
        if (await file.exists()) {
          final bytes = await file.readAsBytes();
          if (bytes.isEmpty) {
            await file.delete().catchError((_) => file);
            continue;
          }
          final audio = CachedTtsAudio(bytes, mime);
          await _memory.put(key, audio);
          return audio;
        }
      }
    } catch (_) {
      // Lỗi đọc đĩa = coi như chưa có cache.
    }
    return null;
  }

  @override
  Future<void> put(String key, CachedTtsAudio audio) async {
    await _memory.put(key, audio);
    final ext = _extOf[audio.mimeType];
    if (ext == null || audio.bytes.isEmpty) return;
    try {
      final dir = await _ensureDir();
      await File('${dir.path}/$key.$ext').writeAsBytes(audio.bytes);
      await _pruneDirectory(
        dir,
        maxFiles: maxFiles,
        maxTotalBytes: maxTotalBytes,
      );
    } catch (_) {
      // Ghi đĩa lỗi không ảnh hưởng việc phát.
    }
  }
}

class VoicePlaybackService {
  VoicePlaybackService({
    ApiClient? api,
    VoicePost? post,
    VoiceAudioSink? audioSink,
    DeviceSpeaker? deviceSpeaker,
    TtsAudioCache? cache,
    this.backendTimeout = const Duration(milliseconds: 2500),
    this.promptCacheTtl = const Duration(minutes: 30),
  })  : _post = post ??
            ((path, body) => (api ?? ApiClient())
                .postData(path, data: body, idempotent: false)),
        _sink = audioSink ?? AudioPlayersSink(),
        _device = deviceSpeaker ?? FlutterTtsSpeaker(),
        _cache = cache ?? FileTtsAudioCache();

  /// Mốc hedge: chờ Backend tối đa chừng này rồi đọc bằng máy cho người dùng
  /// khỏi chờ. Edge-TTS ~0,6s/câu (+ mạng) → 2,5s là dư; khi Edge vào đợt chậm
  /// (4–10s) app đọc bằng máy, request chạy tiếp và lưu cache cho lần sau.
  final Duration backendTimeout;

  /// Câu hỏi Check-in tải sẵn còn dùng được trong khoảng này.
  final Duration promptCacheTtl;

  /// Giọng mặc định của Backend (`vi-VN-HoaiMyNeural`) — dùng làm khóa cache.
  static const String defaultVoiceKey = 'default';

  final VoicePost _post;
  final VoiceAudioSink _sink;
  final DeviceSpeaker _device;
  final TtsAudioCache _cache;

  final Map<String, Future<CachedTtsAudio?>> _ttsInFlight = {};
  final Map<String, Future<_PromptEntry?>> _promptInFlight = {};
  final Map<String, _PromptEntry> _promptCache = {};

  /// Tăng mỗi lần speak/stop: audio về muộn của lượt cũ sẽ bị bỏ.
  int _generation = 0;
  bool _speaking = false;

  bool get isSpeaking => _speaking;

  /// Gộp khoảng trắng giống Backend (`" ".join(text.split())`) để khóa cache khớp.
  static String normalizeText(String text) =>
      text.split(RegExp(r'\s+')).where((w) => w.isNotEmpty).join(' ');

  /// Khóa cache ổn định giữa các lần mở app (FNV-1a 64-bit, không cần thư viện).
  static String cacheKey(String text, {String? voice}) {
    final input = utf8.encode('v1|${voice ?? defaultVoiceKey}|$text');
    var hash = BigInt.parse('cbf29ce484222325', radix: 16);
    final prime = BigInt.parse('100000001b3', radix: 16);
    final mask = (BigInt.one << 64) - BigInt.one;
    for (final byte in input) {
      hash = ((hash ^ BigInt.from(byte)) * prime) & mask;
    }
    return hash.toRadixString(16).padLeft(16, '0');
  }

  // ---------------------------------------------------------------------------
  // Đọc câu bất kỳ
  // ---------------------------------------------------------------------------

  /// Đọc một câu tiếng Việt bất kỳ (nhắc UF3, lời giải thích UF6...).
  Future<VoicePlaybackResult> speakText(String text, {String? voice}) async {
    final cleaned = normalizeText(text);
    if (cleaned.isEmpty) {
      return const VoicePlaybackResult(
        VoicePlaybackSource.textOnly,
        text: '',
        reason: 'empty_text',
      );
    }
    final generation = await _beginTurn();
    try {
      final cached = await _cache.get(cacheKey(cleaned, voice: voice));
      if (generation != _generation) return _cancelled(cleaned);
      if (cached != null) {
        return await _playAudio(generation, cleaned, cached, fromCache: true);
      }
      final audio =
          await _fetchTts(cleaned, voice: voice).timeout(backendTimeout);
      if (generation != _generation) return _cancelled(cleaned);
      if (audio == null) {
        return await _speakOnDevice(generation, cleaned, reason: 'empty_audio');
      }
      return await _playAudio(generation, cleaned, audio);
    } catch (error) {
      if (generation != _generation) return _cancelled(cleaned);
      return await _speakOnDevice(generation, cleaned,
          reason: _reasonOf(error));
    } finally {
      if (generation == _generation) _speaking = false;
    }
  }

  /// Tải sẵn audio của [text] vào cache (không phát). Trả về true nếu có audio.
  Future<bool> prefetchText(String text, {String? voice}) async {
    final cleaned = normalizeText(text);
    if (cleaned.isEmpty) return false;
    if (await _cache.get(cacheKey(cleaned, voice: voice)) != null) return true;
    try {
      return await _fetchTts(cleaned, voice: voice) != null;
    } catch (_) {
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Check-in UF5
  // ---------------------------------------------------------------------------

  /// Tải sẵn câu hỏi Check-in + audio (gọi khi phiên đang chạy, vd. lúc
  /// Start hoặc còn ~1 phút) để lúc hết giờ [speakCheckInPrompt] phát tức thì.
  /// Không phát gì, không ảnh hưởng timer/DND. Trả về true nếu đã sẵn sàng.
  Future<bool> prefetchCheckInPrompt({
    String kind = 'final',
    bool isFinalSessionOfStep = false,
    String? expectedOutput,
    String? sessionOutcome,
    String? voice,
  }) async {
    final body = _checkInBody(
      kind: kind,
      isFinalSessionOfStep: isFinalSessionOfStep,
      expectedOutput: expectedOutput,
      sessionOutcome: sessionOutcome,
      voice: voice,
    );
    try {
      final entry = await _fetchPrompt(body);
      if (entry == null) return false;
      if (entry.deviceOnly) return true;
      return await _cache.get(cacheKey(entry.text, voice: voice)) != null ||
          await prefetchText(entry.text, voice: voice);
    } catch (_) {
      return false;
    }
  }

  /// Lấy câu hỏi Check-in UF5 và đọc nó (ưu tiên bản đã tải sẵn).
  ///
  /// [fallbackText]: câu hỏi mặc định của UI, dùng khi Backend không trả được
  /// câu hỏi kịp mốc hedge (mất mạng/chậm). Khi đó đọc bằng giọng trên máy.
  Future<CheckInPromptPlayback> speakCheckInPrompt({
    required String fallbackText,
    String kind = 'final',
    bool isFinalSessionOfStep = false,
    String? expectedOutput,
    String? sessionOutcome,
    String? voice,
  }) async {
    final body = _checkInBody(
      kind: kind,
      isFinalSessionOfStep: isFinalSessionOfStep,
      expectedOutput: expectedOutput,
      sessionOutcome: sessionOutcome,
      voice: voice,
    );
    final fallback = normalizeText(fallbackText);
    final generation = await _beginTurn();
    try {
      final entry = await _fetchPrompt(body).timeout(backendTimeout);
      if (generation != _generation) {
        return CheckInPromptPlayback(
          prompt: entry?.prompt ?? const {},
          playback: _cancelled(entry?.text ?? fallback),
        );
      }
      if (entry == null) {
        return CheckInPromptPlayback(
          prompt: const {},
          playback: await _speakOnDevice(generation, fallback,
              reason: 'empty_prompt'),
        );
      }
      if (entry.deviceOnly) {
        return CheckInPromptPlayback(
          prompt: entry.prompt,
          playback: await _speakOnDevice(
            generation,
            entry.text,
            reason: 'fallback_to_device_tts',
          ),
        );
      }
      final cached = await _cache.get(cacheKey(entry.text, voice: voice));
      if (generation != _generation) {
        return CheckInPromptPlayback(
          prompt: entry.prompt,
          playback: _cancelled(entry.text),
        );
      }
      final playback = cached != null
          ? await _playAudio(
              generation,
              entry.text,
              cached,
              fromCache: entry.wasPrefetched,
            )
          : await _speakOnDevice(generation, entry.text, reason: 'empty_audio');
      return CheckInPromptPlayback(prompt: entry.prompt, playback: playback);
    } catch (error) {
      final playback = generation != _generation
          ? _cancelled(fallback)
          : await _speakOnDevice(
              generation,
              fallback,
              reason: _reasonOf(error),
            );
      return CheckInPromptPlayback(prompt: const {}, playback: playback);
    } finally {
      if (generation == _generation) _speaking = false;
    }
  }

  /// Dừng mọi giọng đọc. BẮT BUỘC gọi trước khi mở mic (không đè nhau).
  Future<void> stop() async {
    _generation++;
    _speaking = false;
    await _stopOutputs();
  }

  Future<void> dispose() => stop();

  // ---------------------------------------------------------------------------

  /// Bắt đầu lượt đọc mới: đặt trạng thái ĐỒNG BỘ (UI thấy isSpeaking ngay),
  /// rồi mới dừng câu cũ → câu mới thay câu cũ, không phát chồng.
  Future<int> _beginTurn() async {
    final generation = ++_generation;
    _speaking = true;
    await _stopOutputs();
    return generation;
  }

  Future<void> _stopOutputs() async {
    await Future.wait([
      _sink.stop().catchError((_) {}),
      _device.stop().catchError((_) {}),
    ]);
  }

  /// Gọi `/voice/tts` 1 lần cho mỗi câu (dùng chung giữa prefetch và speak),
  /// lưu cache khi xong — kể cả khi người gọi đã hết kiên nhẫn (hedge).
  Future<CachedTtsAudio?> _fetchTts(String text, {String? voice}) {
    final key = cacheKey(text, voice: voice);
    return _ttsInFlight[key] ??= () async {
      try {
        final data = await _post('/voice/tts', {
          'text': text,
          if (voice != null) 'voice': voice,
        });
        final audio = _decode(data['audio_base64'], data['mime_type']);
        if (audio != null) await _cache.put(key, audio);
        return audio;
      } finally {
        _ttsInFlight.remove(key);
      }
    }();
  }

  Future<_PromptEntry?> _fetchPrompt(Map<String, dynamic> body) {
    final key = jsonEncode(body);
    final cached = _promptCache[key];
    if (cached != null &&
        DateTime.now().difference(cached.fetchedAt) < promptCacheTtl) {
      return Future.value(cached.asPrefetched());
    }
    return _promptInFlight[key] ??= () async {
      try {
        final data = await _post('/voice/uf5-checkin-prompt', body);
        final prompt = Map<String, dynamic>.from(
          data['prompt'] as Map? ?? const <String, dynamic>{},
        );
        final text = normalizeText(prompt['prompt_text'] as String? ?? '');
        if (text.isEmpty) return null;
        final voice = body['voice'] as String?;
        final audio = _decode(data['audio_base64'], data['audio_mime_type']);
        if (audio != null) {
          await _cache.put(cacheKey(text, voice: voice), audio);
        }
        final entry = _PromptEntry(
          prompt: prompt,
          text: text,
          deviceOnly: data['fallback_to_device_tts'] == true && audio == null,
          fetchedAt: DateTime.now(),
        );
        // Chỉ nhớ câu hỏi khi đã có audio, để lần sau không phải gọi lại.
        if (audio != null) _promptCache[key] = entry;
        return entry;
      } finally {
        _promptInFlight.remove(key);
      }
    }();
  }

  static Map<String, dynamic> _checkInBody({
    required String kind,
    required bool isFinalSessionOfStep,
    required String? expectedOutput,
    required String? sessionOutcome,
    required String? voice,
  }) =>
      {
        'kind': kind,
        'is_final_session_of_step': isFinalSessionOfStep,
        if (expectedOutput != null) 'expected_output': expectedOutput,
        if (sessionOutcome != null) 'session_outcome': sessionOutcome,
        'include_audio': true,
        if (voice != null) 'voice': voice,
      };

  static CachedTtsAudio? _decode(Object? base64Audio, Object? mime) {
    if (base64Audio is! String || base64Audio.isEmpty) return null;
    try {
      final bytes = base64Decode(base64Audio);
      if (bytes.isEmpty) return null;
      return CachedTtsAudio(bytes, mime is String ? mime : null);
    } on FormatException {
      return null;
    }
  }

  Future<VoicePlaybackResult> _playAudio(
    int generation,
    String text,
    CachedTtsAudio audio, {
    bool fromCache = false,
  }) async {
    try {
      await _sink.playBytes(audio.bytes, mimeType: audio.mimeType);
      if (generation != _generation) return _cancelled(text);
      return VoicePlaybackResult(
        VoicePlaybackSource.backend,
        text: text,
        fromCache: fromCache,
        reason: fromCache ? 'cache' : null,
      );
    } catch (_) {
      if (generation != _generation) return _cancelled(text);
      return await _speakOnDevice(generation, text, reason: 'playback_failed');
    }
  }

  Future<VoicePlaybackResult> _speakOnDevice(
    int generation,
    String text, {
    required String reason,
  }) async {
    try {
      final ok = await _device.speak(text);
      if (generation != _generation) return _cancelled(text);
      return VoicePlaybackResult(
        ok ? VoicePlaybackSource.device : VoicePlaybackSource.textOnly,
        text: text,
        reason: ok ? reason : 'no_vi_voice',
      );
    } catch (_) {
      return VoicePlaybackResult(
        VoicePlaybackSource.textOnly,
        text: text,
        reason: 'device_tts_failed',
      );
    }
  }

  VoicePlaybackResult _cancelled(String text) => VoicePlaybackResult(
        VoicePlaybackSource.cancelled,
        text: text,
        reason: 'cancelled',
      );

  static String _reasonOf(Object error) {
    if (error is TimeoutException) return 'timeout';
    if (error is ApiException) {
      return error.code ?? 'http_${error.statusCode ?? 'network'}';
    }
    return 'network';
  }
}

class _PromptEntry {
  const _PromptEntry({
    required this.prompt,
    required this.text,
    required this.deviceOnly,
    required this.fetchedAt,
    this.wasPrefetched = false,
  });

  final Map<String, dynamic> prompt;
  final String text;

  /// Backend báo `fallback_to_device_tts` và không có audio → đọc bằng máy.
  final bool deviceOnly;
  final DateTime fetchedAt;
  final bool wasPrefetched;

  _PromptEntry asPrefetched() => _PromptEntry(
        prompt: prompt,
        text: text,
        deviceOnly: deviceOnly,
        fetchedAt: fetchedAt,
        wasPrefetched: true,
      );
}

/// Phát audio bằng audioplayers; [playBytes] chờ tới khi phát xong, bị dừng,
/// hoặc quá [maxPlayback] (chống treo nếu plugin không báo hoàn thành).
class AudioPlayersSink implements VoiceAudioSink {
  AudioPlayersSink({
    AudioPlayer? player,
    this.maxPlayback = const Duration(seconds: 90),
  }) : _player = player ?? AudioPlayer();

  final AudioPlayer _player;
  final Duration maxPlayback;
  Completer<void>? _done;
  final List<StreamSubscription<Object?>> _subs = [];

  @override
  Future<void> playBytes(Uint8List bytes, {String? mimeType}) async {
    await stop();
    final done = Completer<void>();
    _done = done;
    void finish() {
      if (!done.isCompleted) done.complete();
    }

    _subs
      ..add(_player.onPlayerComplete.listen((_) => finish()))
      ..add(_player.onPlayerStateChanged.listen((state) {
        if (state == PlayerState.stopped || state == PlayerState.disposed) {
          finish();
        }
      }));
    try {
      await _player.play(BytesSource(bytes, mimeType: mimeType));
      await done.future.timeout(maxPlayback, onTimeout: () {});
    } finally {
      await _cancelSubs();
    }
  }

  Future<void> _cancelSubs() async {
    for (final sub in _subs) {
      await sub.cancel();
    }
    _subs.clear();
  }

  @override
  Future<void> stop() async {
    final done = _done;
    if (done != null && !done.isCompleted) done.complete();
    _done = null;
    await _cancelSubs();
    await _player.stop();
  }
}

/// Giọng đọc trên máy bằng flutter_tts, cố định tiếng Việt.
class FlutterTtsSpeaker implements DeviceSpeaker {
  FlutterTtsSpeaker({FlutterTts? tts}) : _tts = tts ?? FlutterTts();

  static const String language = 'vi-VN';

  final FlutterTts _tts;
  bool _configured = false;

  @override
  Future<bool> speak(String text) async {
    // Hỏi lại mỗi lần khi chưa có giọng: người dùng có thể vừa cài gói vi-VN.
    if (!_configured) {
      if (await _tts.isLanguageAvailable(language) != true) return false;
      await _tts.setLanguage(language);
      await _tts.awaitSpeakCompletion(true);
      _configured = true;
    }
    final result = await _tts.speak(text);
    return result == 1 || result == true;
  }

  @override
  Future<void> stop() async {
    await _tts.stop();
  }
}
