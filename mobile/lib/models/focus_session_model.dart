import 'task_model.dart';

class FocusSessionInfo {
  const FocusSessionInfo({
    required this.sessionId,
    required this.taskTitle,
    required this.stepTitle,
    required this.scheduledStart,
    required this.scheduledEnd,
    required this.workSeconds,
    required this.restSeconds,
    required this.microTaskText,
    required this.segments,
    this.scheduleRevision = 1,
    this.isLastSessionOfStep = false,
    this.deadline,
    this.expectedOutput,
  });

  final String sessionId;
  String get id => sessionId;
  final String taskTitle;
  final String stepTitle;
  final DateTime scheduledStart;
  final DateTime scheduledEnd;
  final int workSeconds;
  final int restSeconds;
  final String microTaskText;
  final List<SessionSegmentModel> segments;
  final int scheduleRevision;
  final bool isLastSessionOfStep;
  final DateTime? deadline;
  final String? expectedOutput;

  factory FocusSessionInfo.fromJson(Map<String, dynamic> json) =>
      FocusSessionInfo(
        sessionId: json['session_id'] as String? ?? '',
        taskTitle: json['task_title'] as String? ?? 'Phiên tập trung',
        stepTitle: json['step_title'] as String? ?? 'Bước hiện tại',
        scheduledStart: DateTime.parse(json['scheduled_start'] as String),
        scheduledEnd: DateTime.parse(json['scheduled_end'] as String),
        workSeconds: json['work_seconds'] as int? ?? 1500,
        restSeconds: json['rest_seconds'] as int? ?? 300,
        microTaskText:
            json['micro_task_text'] as String? ?? 'Mở tài liệu cần làm',
        segments: (json['segments'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(SessionSegmentModel.fromJson)
            .toList(growable: false),
        scheduleRevision: json['schedule_revision'] as int? ?? 1,
        isLastSessionOfStep: json['is_last_session_of_step'] == true,
        deadline: DateTime.tryParse(json['deadline'] as String? ?? ''),
        expectedOutput: json['expected_output'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'session_id': sessionId,
        'schedule_revision': scheduleRevision,
        'is_last_session_of_step': isLastSessionOfStep,
        'task_title': taskTitle,
        'step_title': stepTitle,
        'scheduled_start': scheduledStart.toIso8601String(),
        'scheduled_end': scheduledEnd.toIso8601String(),
        'work_seconds': workSeconds,
        'rest_seconds': restSeconds,
        'micro_task_text': microTaskText,
        'segments': segments.map((segment) => segment.toJson()).toList(),
        if (deadline != null) 'deadline': deadline!.toIso8601String(),
        if (expectedOutput != null) 'expected_output': expectedOutput,
      };

  List<SessionSegmentModel> get effectiveSegments {
    if (segments.isNotEmpty) {
      return [...segments]
        ..sort((a, b) => a.orderIndex.compareTo(b.orderIndex));
    }
    return [
      SessionSegmentModel(
        orderIndex: 0,
        kind: 'work',
        durationSeconds: workSeconds,
      ),
      if (restSeconds > 0)
        SessionSegmentModel(
          orderIndex: 1,
          kind: 'rest',
          durationSeconds: restSeconds,
        ),
    ];
  }
}
