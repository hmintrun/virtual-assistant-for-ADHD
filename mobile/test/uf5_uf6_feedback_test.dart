import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/models/checkin_models.dart';
import 'package:proplan_va/models/reschedule_models.dart';

void main() {
  group('UF5 CheckIn Mobile Models & Invariants Tests', () {
    test('TC_MB_UF5_01: CheckInResponseModel serialize chuẩn xác cho outcome completed', () {
      final now = DateTime.now();
      final model = CheckInResponseModel(
        expectedResponseRevision: 1,
        respondedAt: now,
        sessionOutcome: SessionOutcome.completed,
        stepCompleted: true,
        taskCompleted: false,
      );

      final json = model.toJson();
      expect(json['session_outcome'], 'completed');
      expect(json['step_completed'], isTrue);
      expect(json['task_completed'], isFalse);
      expect(json.containsKey('skip_scope'), isFalse);
    });

    test('TC_MB_UF5_02: CheckInResponseModel bắt buộc có skipScope khi outcome là skipped', () {
      expect(
        () => CheckInResponseModel(
          expectedResponseRevision: 1,
          respondedAt: DateTime.now(),
          sessionOutcome: SessionOutcome.skipped,
          skipScope: null, // Vi phạm: Bỏ qua nhưng không chọn phạm vi
        ),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('TC_MB_UF5_03: CheckInResponseModel cấm skipScope khi outcome không phải skipped', () {
      expect(
        () => CheckInResponseModel(
          expectedResponseRevision: 1,
          respondedAt: DateTime.now(),
          sessionOutcome: SessionOutcome.completed,
          skipScope: SkipScope.skipStep, // Vi phạm: Completed nhưng lại mang skip_scope
        ),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('TC_MB_UF5_04: Invariant R01: Cấm đánh dấu stepCompleted=true khi outcome != completed', () {
      expect(
        () => CheckInResponseModel(
          expectedResponseRevision: 1,
          respondedAt: DateTime.now(),
          sessionOutcome: SessionOutcome.incomplete,
          stepCompleted: true, // Vi phạm R01
        ),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('TC_MB_UF5_05: Invariant R01: Cấm taskCompleted=true khi stepCompleted != true', () {
      expect(
        () => CheckInResponseModel(
          expectedResponseRevision: 1,
          respondedAt: DateTime.now(),
          sessionOutcome: SessionOutcome.completed,
          stepCompleted: false,
          taskCompleted: true, // Vi phạm: Bước chưa xong nhưng đòi xong cả Task
        ),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('TC_MB_UF5_06: SkipScope hỗ trợ đầy đủ 3 chip COGA với nhãn tiếng Việt', () {
      expect(SkipScope.postponeSession.labelVi, 'Hoãn làm sau');
      expect(SkipScope.skipSession.labelVi, 'Bỏ phiên này');
      expect(SkipScope.skipStep.labelVi, 'Bỏ hẳn bước này');
    });
  });

  group('UF6 Adaptive Rescheduling Mobile Models Tests', () {
    test('TC_MB_UF6_01: Parse ScheduleProposalModel từ JSON response của Backend', () {
      final json = {
        'proposal_id': 'prop-1234',
        'trigger_reason': 'checkin_incomplete',
        'status': 'pending',
        'proposal_revision': 1,
        'base_schedule_revision': 1,
        'empathetic_note': 'Đừng tự trách, hôm nay bạn đã rất nỗ lực rồi.',
        'encouragement_tip': 'Uống một ngụm nước và nghỉ ngơi nhé.',
        'feasibility': {
          'status': 'valid',
          'required_seconds': 3600,
          'available_seconds': 7200,
          'deficit_seconds': 0,
          'fully_scheduled': true,
          'confirmable': true,
        },
        'source_revisions': {
          'schedule': 1,
          'task': 1,
          'calendar': 1,
        },
      };

      final proposal = ScheduleProposalModel.fromJson(json);
      expect(proposal.proposalId, 'prop-1234');
      expect(proposal.isConfirmable, isTrue);
      expect(proposal.hasDeficit, isFalse);
      expect(proposal.empatheticNote, contains('Đừng tự trách'));
      expect(proposal.encouragementTip, isNotNull);
    });

    test('TC_MB_UF6_02: Nhận biết chính xác thâm hụt giờ (Deficit) để hiển thị cảnh báo W3C COGA', () {
      final json = {
        'proposal_id': 'prop-deficit',
        'trigger_reason': 'busy',
        'status': 'pending',
        'proposal_revision': 1,
        'base_schedule_revision': 1,
        'empathetic_note': 'Kế hoạch cần điều chỉnh thêm vì quỹ giờ sắp hết.',
        'feasibility': {
          'status': 'deficit',
          'required_seconds': 7200,
          'available_seconds': 3600,
          'deficit_seconds': 3600, // Thiếu 60 phút
          'fully_scheduled': false,
          'confirmable': false, // Bị chặn duyệt
          'blocking_reasons': ['Quỹ giờ rảnh trước Deadline không đủ.'],
        },
        'source_revisions': {'schedule': 1},
      };

      final proposal = ScheduleProposalModel.fromJson(json);
      expect(proposal.hasDeficit, isTrue);
      expect(proposal.feasibility.deficitMinutes, 60);
      expect(proposal.isConfirmable, isFalse); // Nút Duyệt phải bị disable trên Mobile UI
    });
  });
}
