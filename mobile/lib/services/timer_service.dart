import 'dart:async';

import 'package:flutter/foundation.dart';

/// Trừu tượng hóa đồng hồ thời gian thực để hỗ trợ kiểm thử đơn vị với đồng hồ giả.
abstract class Clock {
  const Clock();
  DateTime now();
}

/// Triển khai đồng hồ thời gian thực mặc định dùng cho ứng dụng production.
class SystemClock extends Clock {
  const SystemClock();

  @override
  DateTime now() => DateTime.now();
}

/// Đồng hồ giả định cho phép tua thời gian mà không phải chờ đợi trong unit test.
class FakeClock extends Clock {
  FakeClock([DateTime? initialTime])
      : _currentTime = initialTime ?? DateTime(2026, 11, 7, 9, 0, 0);

  DateTime _currentTime;

  @override
  DateTime now() => _currentTime;

  void setTime(DateTime time) {
    _currentTime = time;
  }

  void advance(Duration duration) {
    _currentTime = _currentTime.add(duration);
  }
}

/// Service đếm giờ phiên tập trung sử dụng Checkpoint tuyệt đối (Anti-Drift Clock).
///
/// Cơ chế Checkpoint: Thay vì đếm lùi biến số `count--` qua timer callback của Dart
/// (vốn sẽ bị trôi và dừng khi Android rơi vào Doze Mode hoặc app vào Background),
/// service lưu `endsAt` tuyệt đối và mỗi lần hiển thị lấy `endsAt - clock.now()`.
class FocusTimerService extends ChangeNotifier {
  FocusTimerService({Clock? clock}) : _clock = clock ?? const SystemClock();

  final Clock _clock;
  Timer? _ticker;
  DateTime? _endsAt;
  int _durationSeconds = 0;
  bool _isRunning = false;
  bool _isPaused = false;
  bool _isCompleted = false;
  int _pausedRemainingSeconds = 0;

  DateTime? get endsAt => _endsAt;
  int get durationSeconds => _durationSeconds;
  bool get isRunning => _isRunning;
  bool get isPaused => _isPaused;
  bool get isCompleted => _isCompleted;
  Clock get clock => _clock;

  int get remainingSeconds {
    if (_isCompleted) return 0;
    if (_isPaused) return _pausedRemainingSeconds;
    final target = _endsAt;
    if (!_isRunning || target == null) return _durationSeconds;
    final difference = target.difference(_clock.now()).inSeconds;
    return difference < 0 ? 0 : difference;
  }

  int get elapsedSeconds {
    if (_durationSeconds <= 0) return 0;
    if (_isCompleted) return _durationSeconds;
    return (_durationSeconds - remainingSeconds).clamp(0, _durationSeconds);
  }

  double get progress {
    if (_durationSeconds <= 0) return 0.0;
    if (_isCompleted) return 1.0;
    return (1.0 - remainingSeconds / _durationSeconds).clamp(0.0, 1.0).toDouble();
  }

  VoidCallback? onElapsed;
  VoidCallback? onTick;

  void start(int seconds, {DateTime? deadline}) {
    _durationSeconds = seconds < 1 ? 1 : seconds;
    _isPaused = false;
    _isCompleted = false;
    _pausedRemainingSeconds = 0;
    _endsAt = deadline ?? _clock.now().add(Duration(seconds: _durationSeconds));
    _isRunning = true;
    _ticker?.cancel();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      tick();
    });
    notifyListeners();
  }

  /// Kích hoạt cập nhật đồng hồ (dùng cho cả Timer định kỳ và FakeClock test).
  void tick() {
    if (!_isRunning || _isPaused || _isCompleted) return;
    notifyListeners();
    onTick?.call();
    if (remainingSeconds == 0) {
      _ticker?.cancel();
      _isRunning = false;
      _isCompleted = true;
      onElapsed?.call();
    }
  }

  void pause() {
    if (!_isRunning || _isPaused || _isCompleted) return;
    _pausedRemainingSeconds = remainingSeconds;
    _isPaused = true;
    _ticker?.cancel();
    notifyListeners();
  }

  void resume() {
    if (!_isRunning || !_isPaused || _isCompleted) return;
    _endsAt = _clock.now().add(Duration(seconds: _pausedRemainingSeconds));
    _isPaused = false;
    _pausedRemainingSeconds = 0;
    _ticker?.cancel();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      tick();
    });
    notifyListeners();
  }

  void restore(DateTime deadline, int originalDurationSeconds) {
    final remaining = deadline.difference(_clock.now()).inSeconds;
    _durationSeconds = originalDurationSeconds;
    _isPaused = false;
    _pausedRemainingSeconds = 0;
    if (remaining <= 0) {
      _endsAt = deadline;
      _isRunning = false;
      _isCompleted = true;
      onElapsed?.call();
      notifyListeners();
      return;
    }
    _isCompleted = false;
    start(remaining, deadline: deadline);
    _durationSeconds = originalDurationSeconds;
  }

  void stop() {
    _ticker?.cancel();
    _ticker = null;
    _isRunning = false;
    _isPaused = false;
    _isCompleted = false;
    _endsAt = null;
    _durationSeconds = 0;
    _pausedRemainingSeconds = 0;
    notifyListeners();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }
}
