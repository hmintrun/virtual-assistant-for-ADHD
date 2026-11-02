import 'package:dio/dio.dart';

import '../core/api_config.dart';
import '../models/draft_model.dart';
import '../models/session_model.dart';
import '../models/task_model.dart';

class ApiService {
  ApiService({Dio? client})
      : _dio = client ??
            Dio(
              BaseOptions(
                baseUrl: ApiConfig.baseUrl,
                connectTimeout: ApiConfig.connectTimeout,
                receiveTimeout: ApiConfig.receiveTimeout,
                headers: const {
                  'Content-Type': 'application/json',
                  'Accept': 'application/json',
                },
              ),
            );

  final Dio _dio;

  Dio get client => _dio;

  // Cập nhật lại base URL nếu người dùng đổi cấu hình IP máy chủ
  void updateBaseUrl(String url) {
    _dio.options.baseUrl = url;
  }

  // ==========================================================================
  // CỤM 1: QUẢN LÝ BẢN NHÁP UF1 (/api/v1/drafts)
  // ==========================================================================

  /// API 1: Tạo bản nháp công việc mới (POST /api/v1/drafts)
  Future<PlanningDraftModel> createDraft({
    required String clientDraftId,
    required String rawInput,
    String inputSource = 'text',
    TaskSummaryModel? summary,
    String? sourceThoughtId,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('create_draft');
    final payload = <String, dynamic>{
      'client_draft_id': clientDraftId,
      'input_source': inputSource,
      'raw_input': rawInput,
      if (summary != null) 'summary': summary.toJson(),
      if (sourceThoughtId != null) 'source_thought_id': sourceThoughtId,
    };

    final response = await _dio.post<Map<String, dynamic>>(
      '/drafts',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanningDraftModel.fromJson(data);
  }

  /// API 2: Lấy danh sách các bản nháp (GET /api/v1/drafts)
  Future<List<PlanningDraftModel>> listDrafts({int limit = 20, String? cursor}) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/drafts',
      queryParameters: {
        'limit': limit,
        if (cursor != null) 'cursor': cursor,
      },
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    final items = data['items'] as List<dynamic>? ?? [];
    return items.map((e) => PlanningDraftModel.fromJson(e as Map<String, dynamic>)).toList();
  }

  /// API 3: Mở lại chi tiết 1 bản nháp (GET /api/v1/drafts/{draft_id})
  Future<PlanningDraftModel> getDraft(String draftId) async {
    final response = await _dio.get<Map<String, dynamic>>('/drafts/$draftId');
    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanningDraftModel.fromJson(data);
  }

  /// API 4: Gọi AI phân tích & hỏi làm rõ 1 vòng (POST /api/v1/drafts/{draft_id}/analyze)
  Future<PlanningDraftModel> analyzeDraft({
    required String draftId,
    required int expectedSummaryRevision,
    required int expectedPlanningInputRevision,
    String? rawInput,
    String? message,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('analyze_draft');
    final payload = <String, dynamic>{
      'expected_summary_revision': expectedSummaryRevision,
      'expected_planning_input_revision': expectedPlanningInputRevision,
      if (rawInput != null) 'raw_input': rawInput,
      if (message != null) 'message': message,
    };

    final response = await _dio.post<Map<String, dynamic>>(
      '/drafts/$draftId/analyze',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanningDraftModel.fromJson(data);
  }

  /// API 5: Sửa trực tiếp bảng tóm tắt hoặc lưu ý tưởng chia bước (PATCH /api/v1/drafts/{draft_id})
  Future<PlanningDraftModel> updateDraft({
    required String draftId,
    required int expectedPlanningInputRevision,
    int? expectedSummaryRevision,
    required Map<String, dynamic> changes,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('patch_draft');
    final payload = <String, dynamic>{
      'expected_planning_input_revision': expectedPlanningInputRevision,
      if (expectedSummaryRevision != null)
        'expected_summary_revision': expectedSummaryRevision,
      'changes': changes,
    };

    final response = await _dio.patch<Map<String, dynamic>>(
      '/drafts/$draftId',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanningDraftModel.fromJson(data);
  }

  /// API 6: Chốt bảng tóm tắt UF1 (POST /api/v1/drafts/{draft_id}/confirm-summary)
  Future<Map<String, dynamic>> confirmSummary({
    required String draftId,
    required int expectedSummaryRevision,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('confirm_summary');
    final payload = <String, dynamic>{
      'expected_summary_revision': expectedSummaryRevision,
    };

    final response = await _dio.post<Map<String, dynamic>>(
      '/drafts/$draftId/confirm-summary',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    return response.data!['data'] as Map<String, dynamic>;
  }

  // ==========================================================================
  // CỤM 3: CHIA BƯỚC, XẾP LỊCH NHÁP & CHỐT KẾ HOẠCH UF2 (/api/v1)
  // ==========================================================================

  /// API 8: Sinh phương án chia bước & xếp lịch (POST /api/v1/drafts/{draft_id}/candidates)
  Future<PlanCandidateModel> generateCandidate({
    required String draftId,
    required int sourceSummaryRevision,
    required int sourcePlanningInputRevision,
    List<String>? selectedPartKeys,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('gen_candidate');
    final payload = <String, dynamic>{
      'source_summary_revision': sourceSummaryRevision,
      'source_planning_input_revision': sourcePlanningInputRevision,
      if (selectedPartKeys != null) 'selected_part_keys': selectedPartKeys,
    };

    final response = await _dio.post<Map<String, dynamic>>(
      '/drafts/$draftId/candidates',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanCandidateModel.fromJson(data);
  }

  /// API 9: Đọc lại phương án lịch nháp (GET /api/v1/candidates/{candidate_id})
  Future<PlanCandidateModel> getCandidate(String candidateId) async {
    final response = await _dio.get<Map<String, dynamic>>('/candidates/$candidateId');
    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanCandidateModel.fromJson(data);
  }

  /// API 10: Tùy chỉnh bước/phiên trong phương án nháp (PATCH /api/v1/candidates/{candidate_id})
  Future<PlanCandidateModel> updateCandidate({
    required String candidateId,
    required int expectedReviewRevision,
    required Map<String, dynamic> edits,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('patch_candidate');
    final payload = <String, dynamic>{
      'expected_review_revision': expectedReviewRevision,
      'edits': edits,
    };

    final response = await _dio.patch<Map<String, dynamic>>(
      '/candidates/$candidateId',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    return PlanCandidateModel.fromJson(data);
  }

  /// API 11: Chốt kế hoạch chính thức (POST /api/v1/candidates/{candidate_id}/confirm)
  Future<ConfirmCandidateResult> confirmCandidate({
    required String candidateId,
    required int expectedReviewRevision,
    required int expectedSummaryRevision,
    required int expectedPlanningInputRevision,
  }) async {
    final idempotencyKey = ApiConfig.generateIdempotencyKey('confirm_candidate');
    final payload = <String, dynamic>{
      'expected_review_revision': expectedReviewRevision,
      'expected_summary_revision': expectedSummaryRevision,
      'expected_planning_input_revision': expectedPlanningInputRevision,
      'expected_source_revisions': {
        'availability': 2,
        'preferences': 1,
        'schedule': 0,
      },
    };

    final response = await _dio.post<Map<String, dynamic>>(
      '/candidates/$candidateId/confirm',
      data: payload,
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    return ConfirmCandidateResult.fromJson(data);
  }

  // ==========================================================================
  // CỤM 4 & 5: ĐỌC TASK & LỊCH TRÌNH CHÍNH THỨC (/api/v1/tasks, /api/v1/schedule)
  // ==========================================================================

  /// API 12: Đọc danh sách Task chính thức (GET /api/v1/tasks)
  Future<List<TaskModel>> getTasks({String? status}) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/tasks',
      queryParameters: {
        if (status != null) 'status': status,
      },
    );

    final data = response.data!['data'] as Map<String, dynamic>;
    final items = data['items'] as List<dynamic>? ?? [];
    return items.map((e) => TaskModel.fromJson(e as Map<String, dynamic>)).toList();
  }

  /// API 16: Đọc lịch trình chính thức (GET /api/v1/schedule)
  Future<Map<String, dynamic>> getSchedule({
    DateTime? fromTime,
    DateTime? toTime,
  }) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/schedule',
      queryParameters: {
        if (fromTime != null) 'from': fromTime.toIso8601String(),
        if (toTime != null) 'to': toTime.toIso8601String(),
      },
    );

    return response.data!['data'] as Map<String, dynamic>;
  }
}
