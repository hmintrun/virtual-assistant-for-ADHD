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
    this.taskId,
    this.taskRevision,
    this.scheduleRevision = 1,
    this.isLastSessionOfStep = false,
    this.deadline,
    this.expectedOutput,
    this.sessionGoal,
    this.microTaskSeconds,
  });

  final String sessionId;
  String get id => sessionId;
  final String? taskId;
  final int? taskRevision;
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
  final String? sessionGoal;
  final int? microTaskSeconds;

  factory FocusSessionInfo.fromJson(Map<String, dynamic> json) =>
      FocusSessionInfo(
        sessionId: json['session_id'] as String? ?? '',
        taskId: json['task_id'] as String?,
        taskRevision: (json['task_revision'] as num?)?.toInt(),
        taskTitle: json['task_title'] as String? ?? 'Phiên tập trung',
        stepTitle: (json['step_title'] as String?) ??
            (json['title'] as String?) ??
            'Bước hiện tại',
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
        sessionGoal: json['session_goal'] as String?,
        microTaskSeconds: (json['micro_task_seconds'] as num?)?.toInt(),
      );

  factory FocusSessionInfo.fromSessionSnapshot(
    Map<String, dynamic> snapshot, {
    String? taskId,
    String? taskTitle,
  }) {
    final title = (snapshot['title'] as String?)?.trim();
    return FocusSessionInfo(
      sessionId: snapshot['session_id'] as String? ?? '',
      taskId: taskId,
      taskTitle: taskTitle ?? 'Phiên tập trung',
      stepTitle: (title != null && title.isNotEmpty) ? title : 'Bước hiện tại',
      scheduledStart: DateTime.parse(snapshot['scheduled_start'] as String),
      scheduledEnd: DateTime.parse(snapshot['scheduled_end'] as String),
      workSeconds: (snapshot['work_seconds'] as num?)?.toInt() ?? 1500,
      restSeconds: (snapshot['rest_seconds'] as num?)?.toInt() ?? 300,
      microTaskText: (snapshot['micro_task_text'] as String?) ??
          'Mở tài liệu cần làm',
      microTaskSeconds: (snapshot['micro_task_seconds'] as num?)?.toInt(),
      segments: (snapshot['segments'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(SessionSegmentModel.fromJson)
          .toList(growable: false),
      scheduleRevision: (snapshot['schedule_revision'] as num?)?.toInt() ?? 1,
      expectedOutput: snapshot['expected_output'] as String?,
      sessionGoal: snapshot['session_goal'] as String?,
    );
  }

  FocusSessionInfo copyWith({
    String? stepTitle,
    String? taskTitle,
    String? sessionGoal,
    String? expectedOutput,
    String? microTaskText,
    int? microTaskSeconds,
    int? scheduleRevision,
  }) =>
      FocusSessionInfo(
        sessionId: sessionId,
        taskId: taskId,
        taskRevision: taskRevision,
        taskTitle: taskTitle ?? this.taskTitle,
        stepTitle: stepTitle ?? this.stepTitle,
        scheduledStart: scheduledStart,
        scheduledEnd: scheduledEnd,
        workSeconds: workSeconds,
        restSeconds: restSeconds,
        microTaskText: microTaskText ?? this.microTaskText,
        segments: segments,
        scheduleRevision: scheduleRevision ?? this.scheduleRevision,
        isLastSessionOfStep: isLastSessionOfStep,
        deadline: deadline,
        expectedOutput: expectedOutput ?? this.expectedOutput,
        sessionGoal: sessionGoal ?? this.sessionGoal,
        microTaskSeconds: microTaskSeconds ?? this.microTaskSeconds,
      );

  Map<String, dynamic> toJson() => {
        'session_id': sessionId,
        if (taskId != null) 'task_id': taskId,
        if (taskRevision != null) 'task_revision': taskRevision,
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
        if (sessionGoal != null) 'session_goal': sessionGoal,
        if (microTaskSeconds != null) 'micro_task_seconds': microTaskSeconds,
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
