import 'dart:async';

import 'package:flutter/foundation.dart';

/// Clock uses an absolute deadline, so a delayed Dart callback does not drift
/// the displayed remaining time. A native exact alarm is still needed for a
/// Doze-safe sound when Android has suspended the process.
class FocusTimerService extends ChangeNotifier {
  Timer? _ticker;
  DateTime? _endsAt;
  int _durationSeconds = 0;
  bool _isRunning = false;

  DateTime? get endsAt => _endsAt;
  int get durationSeconds => _durationSeconds;
  bool get isRunning => _isRunning;
  int get remainingSeconds {
    final target = _endsAt;
    if (!_isRunning || target == null) return _durationSeconds;
    final difference = target.difference(DateTime.now()).inSeconds;
    return difference < 0 ? 0 : difference;
  }

  double get progress {
    if (_durationSeconds <= 0) return 0;
    return (1 - remainingSeconds / _durationSeconds).clamp(0, 1).toDouble();
  }

  void start(int seconds, {DateTime? deadline}) {
    _durationSeconds = seconds < 1 ? 1 : seconds;
    _endsAt =
        deadline ?? DateTime.now().add(Duration(seconds: _durationSeconds));
    _isRunning = true;
    _ticker?.cancel();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      notifyListeners();
      if (remainingSeconds == 0) {
        _ticker?.cancel();
        _isRunning = false;
        onElapsed?.call();
      }
    });
    notifyListeners();
  }

  VoidCallback? onElapsed;

  void restore(DateTime deadline, int originalDurationSeconds) {
    final remaining = deadline.difference(DateTime.now()).inSeconds;
    _durationSeconds = originalDurationSeconds;
    if (remaining <= 0) {
      _endsAt = deadline;
      _isRunning = false;
      onElapsed?.call();
      notifyListeners();
      return;
    }
    start(remaining, deadline: deadline);
    _durationSeconds = originalDurationSeconds;
  }

  void stop() {
    _ticker?.cancel();
    _ticker = null;
    _isRunning = false;
    _endsAt = null;
    _durationSeconds = 0;
    notifyListeners();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }
}
