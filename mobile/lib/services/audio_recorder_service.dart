import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// Records raw audio only. UF4 must not invoke STT/LLM until the user converts
/// the thought after the focus session.
class AudioRecorderService {
  final AudioRecorder _recorder = AudioRecorder();

  Future<String> createRecordingPath() async {
    final directory = await getApplicationDocumentsDirectory();
    final thoughts = Directory('${directory.path}/thoughts');
    await thoughts.create(recursive: true);
    return '${thoughts.path}/thought_${DateTime.now().microsecondsSinceEpoch}.m4a';
  }

  Future<void> start(String path) async {
    if (!await _recorder.hasPermission()) {
      throw const AudioRecorderException('Chưa được cấp quyền ghi âm.');
    }
    await _recorder.start(
      const RecordConfig(encoder: AudioEncoder.aacLc),
      path: path,
    );
  }

  Future<String> stopAndValidate(String expectedPath) async {
    final path = await _recorder.stop() ?? expectedPath;
    final file = File(path);
    if (!await file.exists() || await file.length() == 0) {
      throw const AudioRecorderException('Chưa lưu được tệp ghi âm.');
    }
    return path;
  }

  Future<void> cancel() async {
    if (await _recorder.isRecording()) await _recorder.cancel();
  }

  Future<bool> get hasPermission => _recorder.hasPermission();

  Future<void> delete(String path) async {
    final file = File(path);
    if (await file.exists()) await file.delete();
  }

  Future<void> dispose() => _recorder.dispose();
}

class AudioRecorderException implements Exception {
  const AudioRecorderException(this.message);
  final String message;

  @override
  String toString() => message;
}
