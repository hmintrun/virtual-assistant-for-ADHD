// Service thu âm thô (UF4: Raw Audio) vào bộ nhớ thiết bị, hỗ trợ retry 3 lần và fallback.
import 'dart:async';
import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// Kết quả một lần "Cất suy nghĩ" đã lưu thành công.
class ThoughtRecording {
  const ThoughtRecording({
    required this.path,
    required this.duration,
    required this.sizeBytes,
  });

  final String path;
  final Duration duration;
  final int sizeBytes;
}

/// Lỗi thu/lưu. [message] là microcopy tiếng Việt, hiển thị được cho user.
class RecorderException implements Exception {
  const RecorderException(this.message, {this.countsAsFailure = true});

  final String message;

  /// false = không tính vào bộ đếm 3 lần (ví dụ: lỡ chạm quá ngắn).
  final bool countsAsFailure;

  @override
  String toString() => message;
}

/// Thu âm thô cho UF4 "Cất suy nghĩ".
///
/// Quy tắc bất biến (AGENTS.md 2.2 — UF4):
/// - CHỈ thu và lưu file âm thanh thô. KHÔNG gọi STT, KHÔNG gọi LLM ở đây.
/// - Không đụng tới timer UF3 (service này không biết gì về timer).
/// - Lỗi thu/lưu: tối đa 3 lần → UI mở ô nhập chữ ([shouldFallbackToText]).
class AudioRecorderService {
  AudioRecorderService._();
  static final AudioRecorderService instance = AudioRecorderService._();

  static const int maxFailures = 3;

  /// Ngắn hơn mức này coi là lỡ chạm, không phải một suy nghĩ.
  static const Duration minDuration = Duration(milliseconds: 600);

  /// Giới hạn mặc định; sẽ thay bằng giá trị capabilities từ Backend.
  static const Duration defaultMaxDuration = Duration(minutes: 2);

  final AudioRecorder _recorder = AudioRecorder();

  String? _currentPath;
  DateTime? _startedAt;
  Timer? _maxDurationTimer;
  int _failedAttempts = 0;

  /// Gọi khi bản thu tự dừng vì chạm giới hạn thời lượng.
  void Function(ThoughtRecording recording)? onAutoStopped;
  void Function(RecorderException error)? onAutoStopFailed;

  bool get isRecording => _currentPath != null;
  int get failedAttempts => _failedAttempts;
  bool get shouldFallbackToText => _failedAttempts >= maxFailures;

  /// Kiểm tra (và trên Android: hỏi) quyền micro.
  Future<bool> hasPermission() => _recorder.hasPermission();

  /// Bắt đầu thu (khi user BẤM GIỮ nút "Cất suy nghĩ").
  Future<void> start({Duration maxDuration = defaultMaxDuration}) async {
    if (isRecording) return;
    String? path;
    try {
      if (!await _recorder.hasPermission()) {
        throw const RecorderException('Chưa có quyền dùng micro.');
      }
      path = await _newRecordingPath();
      await _recorder.start(
        const RecordConfig(
          encoder: AudioEncoder.aacLc,
          sampleRate: 16000,
          numChannels: 1,
          bitRate: 32000,
          // Không xin audio focus, không tự pause khi có âm thanh khác (chuông
          // hết phiên) → không mất nửa sau của suy nghĩ, chuông vẫn kêu đúng giờ.
          audioInterruption: AudioInterruptionMode.none,
        ),
        path: path,
      );
      _currentPath = path;
      _startedAt = DateTime.now();
      _maxDurationTimer = Timer(maxDuration, _autoStop);
    } catch (error) {
      if (path != null) await _deleteIfExists(path);
      throw _recordFailure(error, fallback: 'Chưa bắt đầu thu được.');
    }
  }

  /// Dừng và kiểm tra file (khi user NHẢ nút). Chỉ trả về khi file đã lưu
  /// thật sự — lúc đó UI mới được báo "Đã cất".
  Future<ThoughtRecording> stopAndFinalize() async {
    final expectedPath = _currentPath;
    final startedAt = _startedAt;
    if (expectedPath == null || startedAt == null) {
      throw const RecorderException(
        'Không có bản thu nào đang chạy.',
        countsAsFailure: false,
      );
    }
    _maxDurationTimer?.cancel();
    _currentPath = null;
    _startedAt = null;

    try {
      final path = await _recorder.stop() ?? expectedPath;
      final duration = DateTime.now().difference(startedAt);
      if (duration < minDuration) {
        await _deleteIfExists(path);
        throw const RecorderException(
          'Giữ nút lâu hơn một chút để cất suy nghĩ nhé.',
          countsAsFailure: false,
        );
      }
      final file = File(path);
      if (!await file.exists()) {
        throw const RecorderException('Chưa lưu được tệp ghi âm.');
      }
      final size = await file.length();
      if (size == 0) {
        await _deleteIfExists(path);
        throw const RecorderException('Tệp ghi âm bị rỗng.');
      }
      _failedAttempts = 0;
      return ThoughtRecording(path: path, duration: duration, sizeBytes: size);
    } catch (error) {
      await _deleteIfExists(expectedPath);
      throw _recordFailure(error, fallback: 'Chưa lưu được tệp ghi âm.');
    }
  }

  /// Hủy bản đang thu và xóa file dở. Không tính là lỗi.
  Future<void> cancel() async {
    final path = _currentPath;
    _maxDurationTimer?.cancel();
    _currentPath = null;
    _startedAt = null;
    if (path == null) return;
    try {
      await _recorder.cancel();
    } catch (_) {
      // Recorder đã dừng sẵn → chỉ cần dọn file bên dưới.
    }
    await _deleteIfExists(path);
  }

  /// Xóa file của một suy nghĩ đã cất (dùng cho Undo / xóa trong Hộp nháp).
  Future<void> deleteRecording(String path) => _deleteIfExists(path);

  /// Gọi khi user đã chuyển sang nhập chữ hoặc bắt đầu phiên mới.
  void resetFailures() => _failedAttempts = 0;

  Future<void> dispose() async {
    await cancel();
    await _recorder.dispose();
  }

  Future<void> _autoStop() async {
    try {
      final recording = await stopAndFinalize();
      onAutoStopped?.call(recording);
    } on RecorderException catch (error) {
      onAutoStopFailed?.call(error);
    }
  }

  RecorderException _recordFailure(Object error, {required String fallback}) {
    final exception =
        error is RecorderException ? error : RecorderException(fallback);
    if (exception.countsAsFailure) _failedAttempts++;
    return exception;
  }

  Future<String> _newRecordingPath() async {
    final documents = await getApplicationDocumentsDirectory();
    final folder = Directory('${documents.path}/thoughts');
    await folder.create(recursive: true);
    final stamp = DateTime.now().microsecondsSinceEpoch;
    return '${folder.path}/thought_$stamp.m4a';
  }

  Future<void> _deleteIfExists(String path) async {
    try {
      final file = File(path);
      if (await file.exists()) await file.delete();
    } on FileSystemException {
      // Không xóa được thì bỏ qua; không làm hỏng luồng chính.
    }
  }
}
