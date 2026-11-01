/// Models cho Session và PlanCandidate UF2 (Khớp 100% với backend/app/schemas/planning.py)

class CandidatePartModel {
  const CandidatePartModel({
    required this.clientPartKey,
    required this.scopeDescription,
    required this.orderIndex,
    this.decompositionState = 'not_started',
    this.schedulingState = 'not_scheduled',
    this.roughEstimateSeconds,
  });

  final String clientPartKey;
  final String scopeDescription;
  final int orderIndex;
  final String decompositionState;
  final String schedulingState;
  final int? roughEstimateSeconds;

  factory CandidatePartModel.fromJson(Map<String, dynamic> json) {
    return CandidatePartModel(
      clientPartKey: json['client_part_key'] as String? ?? '',
      scopeDescription: json['scope_description'] as String? ?? '',
      orderIndex: json['order_index'] as int? ?? 0,
      decompositionState: json['decomposition_state'] as String? ?? 'not_started',
      schedulingState: json['scheduling_state'] as String? ?? 'not_scheduled',
      roughEstimateSeconds: json['rough_estimate_seconds'] as int?,
    );
  }

  Map<String, dynamic> toJson() => {
        'client_part_key': clientPartKey,
        'scope_description': scopeDescription,
        'order_index': orderIndex,
        'decomposition_state': decompositionState,
        'scheduling_state': schedulingState,
        if (roughEstimateSeconds != null)
          'rough_estimate_seconds': roughEstimateSeconds,
      };
}

class CandidateStepModel {
  const CandidateStepModel({
    required this.clientStepKey,
    required this.clientPartKey,
    required this.title,
    required this.expectedOutput,
    required this.orderIndex,
    required this.estimatedBaseSeconds,
    this.remainingBaseSeconds = 0,
    this.bufferMultiplier = 1.2,
    this.status = 'pending',
  });

  final String clientStepKey;
  final String clientPartKey;
  final String title;
  final String expectedOutput;
  final int orderIndex;
  final int estimatedBaseSeconds;
  final int remainingBaseSeconds;
  final double bufferMultiplier;
  final String status;

  factory CandidateStepModel.fromJson(Map<String, dynamic> json) {
    return CandidateStepModel(
      clientStepKey: json['client_step_key'] as String? ?? '',
      clientPartKey: json['client_part_key'] as String? ?? '',
      title: json['title'] as String? ?? '',
      expectedOutput: json['expected_output'] as String? ?? '',
      orderIndex: json['order_index'] as int? ?? 0,
      estimatedBaseSeconds: json['estimated_base_seconds'] as int? ?? 1500,
      remainingBaseSeconds: json['remaining_base_seconds'] as int? ?? 0,
      bufferMultiplier: (json['buffer_multiplier'] as num?)?.toDouble() ?? 1.2,
      status: json['status'] as String? ?? 'pending',
    );
  }

  Map<String, dynamic> toJson() => {
        'client_step_key': clientStepKey,
        'client_part_key': clientPartKey,
        'title': title,
        'expected_output': expectedOutput,
        'order_index': orderIndex,
        'estimated_base_seconds': estimatedBaseSeconds,
        'remaining_base_seconds': remainingBaseSeconds,
        'buffer_multiplier': bufferMultiplier,
        'status': status,
      };
}

class CandidateSessionModel {
  const CandidateSessionModel({
    required this.clientSessionKey,
    required this.clientStepKey,
    required this.scheduledStart,
    required this.scheduledEnd,
    required this.microTaskText,
    this.workSeconds = 1500,
    this.restSeconds = 300,
    this.durationSeconds = 1800,
  });

  final String clientSessionKey;
  final String clientStepKey;
  final DateTime scheduledStart;
  final DateTime scheduledEnd;
  final String microTaskText;
  final int workSeconds;
  final int restSeconds;
  final int durationSeconds;

  factory CandidateSessionModel.fromJson(Map<String, dynamic> json) {
    return CandidateSessionModel(
      clientSessionKey: json['client_session_key'] as String? ?? '',
      clientStepKey: json['client_step_key'] as String? ?? '',
      scheduledStart: DateTime.parse(json['scheduled_start'] as String),
      scheduledEnd: DateTime.parse(json['scheduled_end'] as String),
      microTaskText: json['micro_task_text'] as String? ?? 'Khởi động nhẹ nhàng',
      workSeconds: json['work_seconds'] as int? ?? 1500,
      restSeconds: json['rest_seconds'] as int? ?? 300,
      durationSeconds: json['duration_seconds'] as int? ?? 1800,
    );
  }

  Map<String, dynamic> toJson() => {
        'client_session_key': clientSessionKey,
        'client_step_key': clientStepKey,
        'scheduled_start': scheduledStart.toIso8601String(),
        'scheduled_end': scheduledEnd.toIso8601String(),
        'micro_task_text': microTaskText,
        'work_seconds': workSeconds,
        'rest_seconds': restSeconds,
        'duration_seconds': durationSeconds,
      };
}

class FeasibilityReportModel {
  const FeasibilityReportModel({
    required this.status, // "feasible" hoặc "infeasible"
    this.deficitSeconds = 0,
    this.blockingReasons = const [],
  });

  final String status;
  final int deficitSeconds;
  final List<String> blockingReasons;

  bool get isFeasible => status == 'feasible';
  int get deficitMinutes => (deficitSeconds / 60).round();

  factory FeasibilityReportModel.fromJson(Map<String, dynamic> json) {
    return FeasibilityReportModel(
      status: json['status'] as String? ?? 'feasible',
      deficitSeconds: json['deficit_seconds'] as int? ?? 0,
      blockingReasons: (json['blocking_reasons'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'status': status,
        'deficit_seconds': deficitSeconds,
        'blocking_reasons': blockingReasons,
      };
}

class PlanCandidateModel {
  const PlanCandidateModel({
    required this.candidateId,
    required this.draftId,
    required this.reviewRevision,
    this.stale = false,
    this.planningParts = const [],
    this.steps = const [],
    this.sessions = const [],
    this.feasibility = const FeasibilityReportModel(status: 'feasible'),
  });

  final String candidateId;
  final String draftId;
  final int reviewRevision;
  final bool stale;
  final List<CandidatePartModel> planningParts;
  final List<CandidateStepModel> steps;
  final List<CandidateSessionModel> sessions;
  final FeasibilityReportModel feasibility;

  factory PlanCandidateModel.fromJson(Map<String, dynamic> json) {
    return PlanCandidateModel(
      candidateId: json['candidate_id'] as String? ?? '',
      draftId: json['draft_id'] as String? ?? '',
      reviewRevision: json['review_revision'] as int? ?? 1,
      stale: json['stale'] as bool? ?? false,
      planningParts: (json['planning_parts'] as List<dynamic>?)
              ?.map((e) => CandidatePartModel.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      steps: (json['steps'] as List<dynamic>?)
              ?.map((e) => CandidateStepModel.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      sessions: (json['sessions'] as List<dynamic>?)
              ?.map((e) => CandidateSessionModel.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      feasibility: json['feasibility'] != null
          ? FeasibilityReportModel.fromJson(json['feasibility'] as Map<String, dynamic>)
          : const FeasibilityReportModel(status: 'feasible'),
    );
  }

  Map<String, dynamic> toJson() => {
        'candidate_id': candidateId,
        'draft_id': draftId,
        'review_revision': reviewRevision,
        'stale': stale,
        'planning_parts': planningParts.map((e) => e.toJson()).toList(),
        'steps': steps.map((e) => e.toJson()).toList(),
        'sessions': sessions.map((e) => e.toJson()).toList(),
        'feasibility': feasibility.toJson(),
      };
}

class ConfirmCandidateResult {
  const ConfirmCandidateResult({
    required this.appSaveState,
    required this.taskId,
    required this.scheduleVersion,
    this.keyMapping = const {},
  });

  final String appSaveState; // "saved"
  final String taskId;
  final int scheduleVersion;
  final Map<String, String> keyMapping;

  factory ConfirmCandidateResult.fromJson(Map<String, dynamic> json) {
    return ConfirmCandidateResult(
      appSaveState: json['app_save_state'] as String? ?? 'saved',
      taskId: json['task']?['task_id'] as String? ?? '',
      scheduleVersion: json['schedule_version']?['version'] as int? ?? 1,
      keyMapping: (json['key_mapping'] as Map<String, dynamic>?)?.map(
            (k, v) => MapEntry(k, v.toString()),
          ) ??
          const {},
    );
  }
}
