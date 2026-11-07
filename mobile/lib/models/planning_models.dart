typedef JsonMap = Map<String, dynamic>;

class PlanningDraftModel {
  const PlanningDraftModel({
    required this.draftId,
    required this.stage,
    required this.summary,
    required this.summaryRevision,
    required this.planningInputRevision,
    required this.inputSource,
    this.rawInput,
    this.summaryConfirmedRevision,
    this.userStepProposal,
    this.clientDraftId = '',
  });

  final String draftId;
  final String clientDraftId;
  final String stage;
  final JsonMap summary;
  final int summaryRevision;
  final int planningInputRevision;
  final String inputSource;
  final String? rawInput;
  final int? summaryConfirmedRevision;
  final String? userStepProposal;

  bool get canPlan => summary['can_plan'] == true;
  String get title => summary['title'] as String? ?? '';
  String get scope => summary['scope'] as String? ?? '';
  List<JsonMap> get clarificationIssues =>
      (summary['clarification_issues'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .toList(growable: false);

  factory PlanningDraftModel.fromJson(JsonMap json) => PlanningDraftModel(
        draftId: json['draft_id'] as String,
        clientDraftId: json['client_draft_id'] as String? ?? '',
        stage: json['stage'] as String,
        summary: Map<String, dynamic>.from(
          json['summary'] as Map<String, dynamic>? ?? const {},
        ),
        summaryRevision: json['summary_revision'] as int? ?? 0,
        planningInputRevision: json['planning_input_revision'] as int? ?? 0,
        inputSource: json['input_source'] as String? ?? 'text',
        rawInput: json['raw_input'] as String?,
        summaryConfirmedRevision: json['summary_confirmed_revision'] as int?,
        userStepProposal: json['user_step_proposal'] as String?,
      );
}

class PlanCandidateModel {
  const PlanCandidateModel({required this.raw});

  final JsonMap raw;
  String get candidateId => raw['candidate_id'] as String;
  String get draftId => raw['draft_id'] as String;
  int get reviewRevision => raw['review_revision'] as int;
  int get sourceSummaryRevision => raw['source_summary_revision'] as int;
  int get sourcePlanningInputRevision =>
      raw['source_planning_input_revision'] as int;
  bool get stale => raw['stale'] as bool? ?? true;
  JsonMap get feasibility =>
      Map<String, dynamic>.from(raw['feasibility'] as Map<String, dynamic>);
  String get feasibilityStatus => feasibility['status'] as String? ?? 'unknown';
  bool get isFeasible => feasibilityStatus == 'feasible' && !stale;
  String? get unexpandedScope => raw['unexpanded_scope'] as String?;
  List<JsonMap> get steps => _maps(raw['steps']);
  List<JsonMap> get sessions => _maps(raw['sessions']);
  JsonMap get sourceRevisions => Map<String, dynamic>.from(
      raw['source_revisions'] as Map<String, dynamic>);

  factory PlanCandidateModel.fromJson(JsonMap json) =>
      PlanCandidateModel(raw: Map<String, dynamic>.from(json));

  static List<JsonMap> _maps(Object? value) =>
      (value as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(Map<String, dynamic>.from)
          .toList(growable: false);
}

class AvailabilityModel {
  const AvailabilityModel({
    required this.availabilityId,
    required this.revision,
    required this.timezone,
    required this.raw,
  });

  final String availabilityId;
  final int revision;
  final String timezone;
  final JsonMap raw;

  factory AvailabilityModel.fromJson(JsonMap json) => AvailabilityModel(
        availabilityId: json['availability_id'] as String,
        revision: json['revision'] as int,
        timezone: json['timezone'] as String,
        raw: Map<String, dynamic>.from(json),
      );

  JsonMap get reference => {
        'availability_id': availabilityId,
        'revision': revision,
      };
}
