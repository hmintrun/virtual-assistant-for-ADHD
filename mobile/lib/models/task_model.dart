/// Models cho Task, Step, Session chính thức (Khớp 100% với backend/app/schemas/task.py)

class StepModel {
  const StepModel({
    required this.stepId,
    required this.taskId,
    required this.planningPartId,
    required this.title,
    required this.expectedOutput,
    required this.orderIndex,
    required this.estimatedBaseSeconds,
    this.bufferMultiplier = 1.2,
    this.status = 'pending',
  });

  final String stepId;
  final String taskId;
  final String planningPartId;
  final String title;
  final String expectedOutput;
  final int orderIndex;
  final int estimatedBaseSeconds;
  final double bufferMultiplier;
  final String status;

  factory StepModel.fromJson(Map<String, dynamic> json) {
    return StepModel(
      stepId: json['step_id'] as String? ?? '',
      taskId: json['task_id'] as String? ?? '',
      planningPartId: json['planning_part_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      expectedOutput: json['expected_output'] as String? ?? '',
      orderIndex: json['order_index'] as int? ?? 0,
      estimatedBaseSeconds: json['estimated_base_seconds'] as int? ?? 1500,
      bufferMultiplier: (json['buffer_multiplier'] as num?)?.toDouble() ?? 1.2,
      status: json['status'] as String? ?? 'pending',
    );
  }

  Map<String, dynamic> toJson() => {
        'step_id': stepId,
        'task_id': taskId,
        'planning_part_id': planningPartId,
        'title': title,
        'expected_output': expectedOutput,
        'order_index': orderIndex,
        'estimated_base_seconds': estimatedBaseSeconds,
        'buffer_multiplier': bufferMultiplier,
        'status': status,
      };
}

class SessionModel {
  const SessionModel({
    required this.sessionId,
    required this.stepId,
    required this.scheduledStart,
    required this.scheduledEnd,
    required this.microTaskText,
    this.workSeconds = 1500,
    this.restSeconds = 300,
    this.durationSeconds = 1800,
    this.status = 'scheduled',
    this.segments = const [],
  });

  final String sessionId;
  final String stepId;
  final DateTime scheduledStart;
  final DateTime scheduledEnd;
  final String microTaskText;
  final int workSeconds;
  final int restSeconds;
  final int durationSeconds;
  final String status;
  final List<SessionSegmentModel> segments;

  factory SessionModel.fromJson(Map<String, dynamic> json) {
    return SessionModel(
      sessionId: json['session_id'] as String? ?? '',
      stepId: json['step_id'] as String? ?? '',
      scheduledStart: DateTime.parse(json['scheduled_start'] as String),
      scheduledEnd: DateTime.parse(json['scheduled_end'] as String),
      microTaskText: json['micro_task_text'] as String? ?? 'Khởi động 2 phút',
      workSeconds: json['work_seconds'] as int? ?? 1500,
      restSeconds: json['rest_seconds'] as int? ?? 300,
      durationSeconds: json['duration_seconds'] as int? ?? 1800,
      status: json['status'] as String? ?? 'scheduled',
      segments: (json['segments'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(SessionSegmentModel.fromJson)
          .toList(growable: false),
    );
  }

  Map<String, dynamic> toJson() => {
        'session_id': sessionId,
        'step_id': stepId,
        'scheduled_start': scheduledStart.toIso8601String(),
        'scheduled_end': scheduledEnd.toIso8601String(),
        'micro_task_text': microTaskText,
        'work_seconds': workSeconds,
        'rest_seconds': restSeconds,
        'duration_seconds': durationSeconds,
        'status': status,
        'segments': segments.map((segment) => segment.toJson()).toList(),
      };
}

class SessionSegmentModel {
  const SessionSegmentModel({
    required this.orderIndex,
    required this.kind,
    required this.durationSeconds,
  });

  final int orderIndex;
  final String kind;
  final int durationSeconds;

  factory SessionSegmentModel.fromJson(Map<String, dynamic> json) =>
      SessionSegmentModel(
        orderIndex: json['order_index'] as int? ?? 0,
        kind: json['kind'] as String? ?? 'work',
        durationSeconds: json['duration_seconds'] as int? ?? 0,
      );

  Map<String, dynamic> toJson() => {
        'order_index': orderIndex,
        'kind': kind,
        'duration_seconds': durationSeconds,
      };
}

class TaskModel {
  const TaskModel({
    required this.taskId,
    required this.title,
    this.description,
    this.scope,
    this.status = 'confirmed',
    this.steps = const [],
    this.sessions = const [],
    this.createdAt,
  });

  final String taskId;
  final String title;
  final String? description;
  final String? scope;
  final String status;
  final List<StepModel> steps;
  final List<SessionModel> sessions;
  final DateTime? createdAt;

  factory TaskModel.fromJson(Map<String, dynamic> json) {
    return TaskModel(
      taskId: json['task_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      description: json['description'] as String?,
      scope: json['scope'] as String?,
      status: json['status'] as String? ?? 'confirmed',
      steps: (json['steps'] as List<dynamic>?)
              ?.map((e) => StepModel.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      sessions: (json['sessions'] as List<dynamic>?)
              ?.map((e) => SessionModel.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'task_id': taskId,
        'title': title,
        if (description != null) 'description': description,
        if (scope != null) 'scope': scope,
        'status': status,
        'steps': steps.map((e) => e.toJson()).toList(),
        'sessions': sessions.map((e) => e.toJson()).toList(),
        if (createdAt != null) 'created_at': createdAt!.toIso8601String(),
      };
}
