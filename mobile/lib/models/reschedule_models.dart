import 'package:flutter/foundation.dart';

/// Nguyên nhân kích hoạt đề xuất tái lập lịch UF6
enum RescheduleTriggerReason {
  incomplete,
  busy,
  settingChanged,
  deadlineRisk,
  skipped,
  manual;

  static RescheduleTriggerReason fromString(String value) {
    switch (value) {
      case 'incomplete':
        return RescheduleTriggerReason.incomplete;
      case 'busy':
        return RescheduleTriggerReason.busy;
      case 'setting_changed':
        return RescheduleTriggerReason.settingChanged;
      case 'deadline_risk':
        return RescheduleTriggerReason.deadlineRisk;
      case 'skipped':
        return RescheduleTriggerReason.skipped;
      default:
        return RescheduleTriggerReason.manual;
    }
  }

  String toDbValue() {
    switch (this) {
      case RescheduleTriggerReason.incomplete:
        return 'incomplete';
      case RescheduleTriggerReason.busy:
        return 'busy';
      case RescheduleTriggerReason.settingChanged:
        return 'setting_changed';
      case RescheduleTriggerReason.deadlineRisk:
        return 'deadline_risk';
      case RescheduleTriggerReason.skipped:
        return 'skipped';
      case RescheduleTriggerReason.manual:
        return 'manual';
    }
  }
}

/// Báo cáo tính khả thi thời gian trước Deadline (Feasibility)
@immutable
class FeasibilityModel {
  final String status; // 'valid', 'deficit', 'conflict', 'unsolvable'
  final int requiredSeconds;
  final int availableSeconds;
  final int deficitSeconds;
  final bool fullyScheduled;
  final bool confirmable;
  final List<String> blockingReasons;

  const FeasibilityModel({
    required this.status,
    required this.requiredSeconds,
    required this.availableSeconds,
    required this.deficitSeconds,
    required this.fullyScheduled,
    required this.confirmable,
    this.blockingReasons = const [],
  });

  int get deficitMinutes => (deficitSeconds / 60).ceil();

  factory FeasibilityModel.fromJson(Map<String, dynamic> json) {
    return FeasibilityModel(
      status: json['status'] as String? ?? 'valid',
      requiredSeconds: json['required_seconds'] as int? ?? 0,
      availableSeconds: json['available_seconds'] as int? ?? 0,
      deficitSeconds: json['deficit_seconds'] as int? ?? 0,
      fullyScheduled: json['fully_scheduled'] as bool? ?? true,
      confirmable: json['confirmable'] as bool? ?? (json['status'] == 'valid'),
      blockingReasons: (json['blocking_reasons'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
    );
  }
}

/// Đề xuất tái lập lịch thích ứng không phán xét (ScheduleProposal UF6)
@immutable
class ScheduleProposalModel {
  final String proposalId;
  final String triggerReason;
  final String status; // 'pending', 'approved', 'rejected', 'superseded'
  final int proposalRevision;
  final int baseScheduleRevision;
  final String empatheticNote;
  final String? encouragementTip;
  final FeasibilityModel feasibility;
  final Map<String, int> sourceRevisions;

  const ScheduleProposalModel({
    required this.proposalId,
    required this.triggerReason,
    required this.status,
    required this.proposalRevision,
    required this.baseScheduleRevision,
    required this.empatheticNote,
    this.encouragementTip,
    required this.feasibility,
    required this.sourceRevisions,
  });

  bool get isConfirmable => feasibility.confirmable && status == 'pending';
  bool get hasDeficit => feasibility.deficitMinutes > 0;

  factory ScheduleProposalModel.fromJson(Map<String, dynamic> json) {
    final data = json['data'] as Map<String, dynamic>? ?? json;
    return ScheduleProposalModel(
      proposalId: data['proposal_id'] as String? ?? '',
      triggerReason: data['trigger_reason'] as String? ?? 'manual',
      status: data['status'] as String? ?? 'pending',
      proposalRevision: data['proposal_revision'] as int? ?? 1,
      baseScheduleRevision: data['base_schedule_revision'] as int? ?? 1,
      empatheticNote: data['empathetic_note'] as String? ?? '',
      encouragementTip: data['encouragement_tip'] as String?,
      feasibility: FeasibilityModel.fromJson(
        data['feasibility'] as Map<String, dynamic>? ?? {},
      ),
      sourceRevisions: (data['source_revisions'] as Map<String, dynamic>?)?.map(
            (k, v) => MapEntry(k, v as int? ?? 1),
          ) ??
          {},
    );
  }
}
