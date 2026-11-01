/// Models cho bản nháp công việc UF1 (Khớp 100% với backend/app/schemas/draft.py)

class ClarificationIssueModel {
  const ClarificationIssueModel({
    required this.code,
    required this.fieldPath,
    required this.message,
    this.options,
  });

  final String code;
  final String fieldPath;
  final String message;
  final List<String>? options;

  factory ClarificationIssueModel.fromJson(Map<String, dynamic> json) {
    return ClarificationIssueModel(
      code: json['code'] as String? ?? '',
      fieldPath: json['field_path'] as String? ?? '',
      message: json['message'] as String? ?? '',
      options: (json['options'] as List<dynamic>?)?.map((e) => e.toString()).toList(),
    );
  }

  Map<String, dynamic> toJson() => {
        'code': code,
        'field_path': fieldPath,
        'message': message,
        if (options != null) 'options': options,
      };
}

class TimeConstraintModel {
  const TimeConstraintModel({
    required this.kind,
    this.startAt,
    this.finishByAt,
    this.deadlineAt,
    this.inputTimezone = 'Asia/Ho_Chi_Minh',
  });

  final String kind; // "deadline", "window", "timeframe"
  final DateTime? startAt;
  final DateTime? finishByAt;
  final DateTime? deadlineAt;
  final String inputTimezone;

  factory TimeConstraintModel.fromJson(Map<String, dynamic> json) {
    return TimeConstraintModel(
      kind: json['kind'] as String? ?? 'deadline',
      startAt: json['start_at'] != null ? DateTime.tryParse(json['start_at'] as String) : null,
      finishByAt: json['finish_by_at'] != null
          ? DateTime.tryParse(json['finish_by_at'] as String)
          : null,
      deadlineAt: json['deadline_at'] != null
          ? DateTime.tryParse(json['deadline_at'] as String)
          : null,
      inputTimezone: json['input_timezone'] as String? ?? 'Asia/Ho_Chi_Minh',
    );
  }

  Map<String, dynamic> toJson() => {
        'kind': kind,
        if (startAt != null) 'start_at': startAt!.toIso8601String(),
        if (finishByAt != null) 'finish_by_at': finishByAt!.toIso8601String(),
        if (deadlineAt != null) 'deadline_at': deadlineAt!.toIso8601String(),
        'input_timezone': inputTimezone,
      };
}

class AvailabilityRefModel {
  const AvailabilityRefModel({
    required this.availabilityId,
    required this.revision,
  });

  final String availabilityId;
  final int revision;

  factory AvailabilityRefModel.fromJson(Map<String, dynamic> json) {
    return AvailabilityRefModel(
      availabilityId: json['availability_id'] as String? ?? '',
      revision: json['revision'] as int? ?? 1,
    );
  }

  Map<String, dynamic> toJson() => {
        'availability_id': availabilityId,
        'revision': revision,
      };
}

class TaskSummaryModel {
  const TaskSummaryModel({
    this.title,
    this.description,
    this.goal,
    this.scope,
    this.extraRequirements = const [],
    this.timeConstraint,
    this.availabilityRef,
    this.importance,
    this.clarificationIssues = const [],
    this.canPlan = false,
  });

  final String? title;
  final String? description;
  final String? goal;
  final String? scope;
  final List<String> extraRequirements;
  final TimeConstraintModel? timeConstraint;
  final AvailabilityRefModel? availabilityRef;
  final int? importance;
  final List<ClarificationIssueModel> clarificationIssues;
  final bool canPlan;

  factory TaskSummaryModel.fromJson(Map<String, dynamic> json) {
    return TaskSummaryModel(
      title: json['title'] as String?,
      description: json['description'] as String?,
      goal: json['goal'] as String?,
      scope: json['scope'] as String?,
      extraRequirements: (json['extra_requirements'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
      timeConstraint: json['time_constraint'] != null
          ? TimeConstraintModel.fromJson(json['time_constraint'] as Map<String, dynamic>)
          : null,
      availabilityRef: json['availability_ref'] != null
          ? AvailabilityRefModel.fromJson(json['availability_ref'] as Map<String, dynamic>)
          : null,
      importance: json['importance'] as int?,
      clarificationIssues: (json['clarification_issues'] as List<dynamic>?)
              ?.map((e) => ClarificationIssueModel.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      canPlan: json['can_plan'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
        if (title != null) 'title': title,
        if (description != null) 'description': description,
        if (goal != null) 'goal': goal,
        if (scope != null) 'scope': scope,
        'extra_requirements': extraRequirements,
        if (timeConstraint != null) 'time_constraint': timeConstraint!.toJson(),
        if (availabilityRef != null) 'availability_ref': availabilityRef!.toJson(),
        if (importance != null) 'importance': importance,
        'can_plan': canPlan,
      };
}

class PlanningDraftModel {
  const PlanningDraftModel({
    required this.draftId,
    required this.clientDraftId,
    required this.stage,
    required this.summary,
    required this.summaryRevision,
    required this.planningInputRevision,
    this.summaryConfirmedRevision,
    this.candidateRefId,
    this.rawInput,
    required this.inputSource,
    this.userStepProposal,
  });

  final String draftId;
  final String clientDraftId;
  final String stage;
  final TaskSummaryModel summary;
  final int summaryRevision;
  final int planningInputRevision;
  final int? summaryConfirmedRevision;
  final String? candidateRefId;
  final String? rawInput;
  final String inputSource; // "text", "voice", "thought"
  final String? userStepProposal;

  factory PlanningDraftModel.fromJson(Map<String, dynamic> json) {
    return PlanningDraftModel(
      draftId: json['draft_id'] as String? ?? '',
      clientDraftId: json['client_draft_id'] as String? ?? '',
      stage: json['stage'] as String? ?? 'collecting',
      summary: json['summary'] != null
          ? TaskSummaryModel.fromJson(json['summary'] as Map<String, dynamic>)
          : const TaskSummaryModel(),
      summaryRevision: json['summary_revision'] as int? ?? 1,
      planningInputRevision: json['planning_input_revision'] as int? ?? 1,
      summaryConfirmedRevision: json['summary_confirmed_revision'] as int?,
      candidateRefId: json['candidate_ref']?['candidate_id'] as String?,
      rawInput: json['raw_input'] as String?,
      inputSource: json['input_source'] as String? ?? 'text',
      userStepProposal: json['user_step_proposal'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'draft_id': draftId,
        'client_draft_id': clientDraftId,
        'stage': stage,
        'summary': summary.toJson(),
        'summary_revision': summaryRevision,
        'planning_input_revision': planningInputRevision,
        if (summaryConfirmedRevision != null)
          'summary_confirmed_revision': summaryConfirmedRevision,
        if (rawInput != null) 'raw_input': rawInput,
        'input_source': inputSource,
        if (userStepProposal != null) 'user_step_proposal': userStepProposal,
      };
}
