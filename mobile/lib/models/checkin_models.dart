import 'package:flutter/foundation.dart';

/// Các loại Check-in được hỗ trợ ở UF5 (Khớp với backend/app/schemas/execution.py)
enum CheckInKind { mid, finalCheckin }

enum SessionOutcome {
  completed,
  partial,
  incomplete,
  busy,
  skipped,
  noRespond,
  unknown;

  static SessionOutcome fromString(String value) {
    switch (value) {
      case 'completed':
        return SessionOutcome.completed;
      case 'partial':
        return SessionOutcome.partial;
      case 'incomplete':
        return SessionOutcome.incomplete;
      case 'busy':
        return SessionOutcome.busy;
      case 'skipped':
        return SessionOutcome.skipped;
      case 'no_respond':
        return SessionOutcome.noRespond;
      default:
        return SessionOutcome.unknown;
    }
  }

  String toDbValue() {
    switch (this) {
      case SessionOutcome.completed:
        return 'completed';
      case SessionOutcome.partial:
        return 'partial';
      case SessionOutcome.incomplete:
        return 'incomplete';
      case SessionOutcome.busy:
        return 'busy';
      case SessionOutcome.skipped:
        return 'skipped';
      case SessionOutcome.noRespond:
        return 'no_respond';
      case SessionOutcome.unknown:
        return 'unknown';
    }
  }
}

/// 3 Chip Bỏ qua chuẩn W3C COGA cho người dùng ADHD
enum SkipScope {
  postponeSession,
  skipSession,
  skipStep;

  static SkipScope? fromString(String? value) {
    if (value == null) return null;
    switch (value) {
      case 'postpone_session':
        return SkipScope.postponeSession;
      case 'skip_session':
        return SkipScope.skipSession;
      case 'skip_step':
        return SkipScope.skipStep;
      default:
        return null;
    }
  }

  String toDbValue() {
    switch (this) {
      case SkipScope.postponeSession:
        return 'postpone_session';
      case SkipScope.skipSession:
        return 'skip_session';
      case SkipScope.skipStep:
        return 'skip_step';
    }
  }

  String get labelVi {
    switch (this) {
      case SkipScope.postponeSession:
        return 'Hoãn làm sau';
      case SkipScope.skipSession:
        return 'Bỏ phiên này';
      case SkipScope.skipStep:
        return 'Bỏ hẳn bước này';
    }
  }
}

/// Model dữ liệu phản hồi Check-in (Khớp với CheckInResponseRequest trên Backend)
@immutable
class CheckInResponseModel {
  final int expectedResponseRevision;
  final DateTime respondedAt;
  final SessionOutcome sessionOutcome;
  final bool? stepCompleted;
  final bool? taskCompleted;
  final SkipScope? skipScope;
  final String? remainingNote;

  CheckInResponseModel({
    required this.expectedResponseRevision,
    required this.respondedAt,
    required this.sessionOutcome,
    this.stepCompleted,
    this.taskCompleted,
    this.skipScope,
    this.remainingNote,
  }) {
    // Invariant R01: Kiểm tra bất biến ngay trên Frontend Mobile trước khi gửi API
    if (sessionOutcome == SessionOutcome.skipped && skipScope == null) {
      throw ArgumentError('Phải chọn phạm vi bỏ qua (skipScope) khi outcome là skipped.');
    }
    if (sessionOutcome != SessionOutcome.skipped && skipScope != null) {
      throw ArgumentError('Chỉ được chọn skipScope khi outcome là skipped.');
    }
    if (sessionOutcome != SessionOutcome.completed && stepCompleted == true) {
      throw ArgumentError('Chỉ được đánh dấu hoàn thành Step khi phiên đạt completed.');
    }
    if (taskCompleted == true && stepCompleted != true) {
      throw ArgumentError('Không thể hoàn thành Task khi Step hiện tại chưa hoàn thành.');
    }
  }

  Map<String, dynamic> toJson() => {
        'expected_response_revision': expectedResponseRevision,
        'responded_at': respondedAt.toUtc().toIso8601String(),
        'session_outcome': sessionOutcome.toDbValue(),
        if (stepCompleted != null) 'step_completed': stepCompleted,
        if (taskCompleted != null) 'task_completed': taskCompleted,
        if (skipScope != null) 'skip_scope': skipScope!.toDbValue(),
        if (remainingNote != null && remainingNote!.isNotEmpty)
          'remaining_note': remainingNote,
      };

  factory CheckInResponseModel.fromJson(Map<String, dynamic> json) {
    return CheckInResponseModel(
      expectedResponseRevision: json['expected_response_revision'] as int? ?? 0,
      respondedAt: DateTime.parse(json['responded_at'] as String),
      sessionOutcome: SessionOutcome.fromString(json['session_outcome'] as String? ?? 'unknown'),
      stepCompleted: json['step_completed'] as bool?,
      taskCompleted: json['task_completed'] as bool?,
      skipScope: SkipScope.fromString(json['skip_scope'] as String?),
      remainingNote: json['remaining_note'] as String?,
    );
  }
}
