# Tầng Service điều phối nghiệp vụ cho bản nháp công việc UF1 (PlanningDraft)
import json
from datetime import datetime

from app.core.errors import AppError
from app.schemas.common import AvailabilityRef, FieldError
from app.schemas.draft import (
    ClarificationIssue,
    ConfirmSummaryRequest,
    ConfirmSummaryResult,
    DraftAnalyzeRequest,
    DraftCreateRequest,
    DraftListData,
    DraftPatchRequest,
    FieldSource,
    PlanningDraft,
    SummaryInput,
    TaskSummary,
    TimeConstraint,
)


def _build_seed_draft(draft_id: str, client_draft_id: str) -> PlanningDraft:
    """Tạo dữ liệu mẫu chuẩn theo Dòng 4 trên API Sheet (dùng tạm trước khi ghép DB ở Bước 5.1)."""
    return PlanningDraft(
        draft_id=draft_id,
        client_draft_id=client_draft_id,
        stage="summary_review",
        raw_input="Tìm tài liệu và viết bản nháp, chỉ làm vào giờ rảnh đã xác nhận.",
        input_source="text",
        summary=TaskSummary(
            title="Viết báo cáo môn học",
            description="Tìm tài liệu và viết bản nháp báo cáo môn học.",
            goal="Có bản nháp báo cáo để nhóm review.",
            scope="Tìm tài liệu và viết bản nháp; không làm slide.",
            extra_requirements=["Giữ trích dẫn nguồn tài liệu."],
            time_constraint=TimeConstraint(
                kind="window",
                start_at=datetime.fromisoformat("2026-10-30T07:00:00+07:00"),
                finish_by_at=datetime.fromisoformat("2026-11-13T07:00:00+07:00"),
                deadline_at=None,
                input_timezone="Asia/Ho_Chi_Minh",
            ),
            availability_ref=AvailabilityRef(
                availability_id="availability_1",
                revision=2,
            ),
            importance=None,
            clarification_issues=[],
            can_plan=True,
            field_sources={
                "title": FieldSource(source="user_confirmed", source_ref="summary_revision:5"),
                "scope": FieldSource(source="user_confirmed", source_ref="summary_revision:5"),
                "time_constraint": FieldSource(
                    source="user_confirmed", source_ref="summary_revision:5"
                ),
                "availability_ref": FieldSource(
                    source="user_confirmed", source_ref="availability_revision:2"
                ),
                "description": FieldSource(
                    source="user_confirmed", source_ref="summary_revision:5"
                ),
                "goal": FieldSource(source="user_confirmed", source_ref="summary_revision:5"),
                "extra_requirements": FieldSource(
                    source="user_confirmed", source_ref="summary_revision:5"
                ),
            },
        ),
        summary_revision=5,
        summary_confirmed_revision=None,
        candidate_ref=None,
        source_thought_id=None,
        confirmed_task_id=None,
        user_step_proposal=None,
        planning_input_revision=5,
    )


# Kho dữ liệu giả lập tạm thời theo từng chủ sở hữu (user_id, draft_id) khi chưa ghép DB (Bước 5.1)
# Ghi chú phụ thuộc F04/F07: Khi role Database bàn giao model/migration, thay các biến dưới đây bằng truy vấn AsyncSession.
_FAKE_DRAFTS_BY_OWNER: dict[tuple[str, str], PlanningDraft] = {
    ("user_a", "draft_1"): _build_seed_draft("draft_1", "draft_may_1"),
    ("user_a", "draft_025"): _build_seed_draft("draft_025", "draft_may_025"),
    ("user_b", "draft_b_1"): _build_seed_draft("draft_b_1", "draft_may_b_1"),
}

# Ánh xạ (user_id, client_draft_id) -> draft_id để chống nhân bản Draft khi gửi lại cùng client_draft_id
_CLIENT_DRAFT_INDEX: dict[tuple[str, str], str] = {
    ("user_a", "draft_may_1"): "draft_1",
    ("user_a", "draft_may_025"): "draft_025",
    ("user_b", "draft_may_b_1"): "draft_b_1",
}

# Lưu biên nhận Idempotency-Key cho thao tác tạo Draft: (user_id, idempotency_key) -> (payload_fingerprint, draft_id)
_CREATE_IDEMPOTENCY_STORE: dict[tuple[str, str], tuple[str, str]] = {}

# Lưu biên nhận Idempotency-Key cho thao tác sửa Draft: (user_id, idempotency_key) -> (payload_fingerprint, PlanningDraft)
_PATCH_IDEMPOTENCY_STORE: dict[tuple[str, str], tuple[str, PlanningDraft]] = {}

# Lưu biên nhận Idempotency-Key cho thao tác xác nhận UF1: (user_id, idempotency_key) -> (payload_fingerprint, ConfirmSummaryResult)
_CONFIRM_SUMMARY_IDEMPOTENCY_STORE: dict[
    tuple[str, str], tuple[str, ConfirmSummaryResult]
] = {}

# Lưu biên nhận Idempotency-Key cho thao tác phân tích AI UF1: (user_id, idempotency_key) -> (payload_fingerprint, PlanningDraft)
_ANALYZE_IDEMPOTENCY_STORE: dict[tuple[str, str], tuple[str, PlanningDraft]] = {}


# Phiên bản nguồn giờ rảnh hiện tại theo từng chủ sở hữu (user_id, availability_id) -> revision
_CURRENT_AVAILABILITY_REVISIONS: dict[tuple[str, str], int] = {
    ("user_a", "availability_1"): 2,
    ("user_b", "availability_b_1"): 1,
}



def _evaluate_summary_issues(summary: TaskSummary) -> tuple[list[ClarificationIssue], bool]:
    """Kiểm tra 4 thông tin bắt buộc của UF1 (Nội dung, Phạm vi, Khung giờ rảnh, Thời hạn) để lập danh sách câu hỏi."""
    issues: list[ClarificationIssue] = []
    if not summary.description and not summary.title:
        issues.append(
            ClarificationIssue(
                code="DESCRIPTION_REQUIRED",
                field_path="description",
                message="Chưa có nội dung công việc cần thực hiện.",
                question="Bạn muốn thực hiện công việc cụ thể nào?",
                blocking=True,
            )
        )
    if not summary.scope:
        issues.append(
            ClarificationIssue(
                code="SCOPE_REQUIRED",
                field_path="scope",
                message="Chưa rõ đầu ra cần đạt.",
                question="Kết quả cụ thể bạn muốn có là gì?",
                blocking=True,
            )
        )
    if summary.time_constraint is None:
        issues.append(
            ClarificationIssue(
                code="TIME_CONSTRAINT_REQUIRED",
                field_path="time_constraint",
                message="Chưa có mốc bắt đầu và hoàn tất.",
                question="Bạn muốn bắt đầu khi nào và cần xong trước lúc nào?",
                blocking=True,
            )
        )
    if summary.availability_ref is None:
        issues.append(
            ClarificationIssue(
                code="AVAILABILITY_REQUIRED",
                field_path="availability_ref",
                message="Chưa có bộ giờ rảnh do người dùng xác nhận.",
                question="Bạn có thể làm công việc vào những khoảng nào?",
                blocking=True,
            )
        )
    can_plan = not any(issue.blocking for issue in issues)
    return issues, can_plan


def _build_initial_summary(
    raw_input: str | None, summary_in: SummaryInput | None
) -> TaskSummary:
    """Khởi tạo TaskSummary từ dữ liệu người dùng nhập khi gọi POST /drafts."""
    field_sources: dict[str, FieldSource] = {}
    if summary_in is not None:
        for field_name in (
            "title",
            "description",
            "goal",
            "scope",
            "time_constraint",
            "availability_ref",
            "importance",
        ):
            if getattr(summary_in, field_name) is not None:
                field_sources[field_name] = FieldSource(
                    source="user_input", source_ref="draft_create"
                )
        if summary_in.extra_requirements:
            field_sources["extra_requirements"] = FieldSource(
                source="user_input", source_ref="draft_create"
            )
        base_summary = TaskSummary(
            title=summary_in.title,
            description=summary_in.description or raw_input,
            goal=summary_in.goal,
            scope=summary_in.scope,
            extra_requirements=list(summary_in.extra_requirements),
            time_constraint=summary_in.time_constraint,
            availability_ref=summary_in.availability_ref,
            importance=summary_in.importance,
            clarification_issues=[],
            can_plan=False,
            field_sources=field_sources,
        )
    else:
        if raw_input is not None:
            field_sources["description"] = FieldSource(
                source="user_input", source_ref="raw_input"
            )
        base_summary = TaskSummary(
            title=None,
            description=raw_input,
            goal=None,
            scope=None,
            extra_requirements=[],
            time_constraint=None,
            availability_ref=None,
            importance=None,
            clarification_issues=[],
            can_plan=False,
            field_sources=field_sources,
        )

    issues, can_plan = _evaluate_summary_issues(base_summary)
    return base_summary.model_copy(
        update={
            "clarification_issues": issues,
            "can_plan": can_plan,
        }
    )


async def create_draft(
    *,
    user_id: str,
    idempotency_key: str,
    payload: DraftCreateRequest,
) -> PlanningDraft:
    """Dòng 2 (POST /api/v1/drafts): Tạo bản nháp mới, chống nhân bản qua Idempotency-Key và client_draft_id."""
    fingerprint = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    idem_record = _CREATE_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_draft_id = idem_record
        if saved_fingerprint != fingerprint:
            raise AppError(
                status_code=409,
                code="IDEMPOTENCY_KEY_REUSED",
                message=(
                    "Mã lệnh đã được dùng với nội dung khác. "
                    "Giữ mã cũ khi retry đúng nội dung; tạo mã mới cho ý định mới."
                ),
                next_action="use_original_payload_or_new_command",
            )
        return _FAKE_DRAFTS_BY_OWNER[(user_id, saved_draft_id)]

    existing_draft_id = _CLIENT_DRAFT_INDEX.get((user_id, payload.client_draft_id))
    if existing_draft_id is not None:
        existing_draft = _FAKE_DRAFTS_BY_OWNER[(user_id, existing_draft_id)]
        _CREATE_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (
            fingerprint,
            existing_draft_id,
        )
        return existing_draft

    new_draft_id = f"draft_{len(_FAKE_DRAFTS_BY_OWNER) + 1}"
    summary = _build_initial_summary(payload.raw_input, payload.summary)
    stage = "summary_review" if summary.can_plan else "collecting"
    new_draft = PlanningDraft(
        draft_id=new_draft_id,
        client_draft_id=payload.client_draft_id,
        stage=stage,
        raw_input=payload.raw_input,
        input_source=payload.input_source,
        summary=summary,
        summary_revision=1,
        summary_confirmed_revision=None,
        candidate_ref=None,
        source_thought_id=payload.source_thought_id,
        confirmed_task_id=None,
        user_step_proposal=None,
        planning_input_revision=1,
    )
    _FAKE_DRAFTS_BY_OWNER[(user_id, new_draft_id)] = new_draft
    _CLIENT_DRAFT_INDEX[(user_id, payload.client_draft_id)] = new_draft_id
    _CREATE_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (fingerprint, new_draft_id)
    return new_draft


async def list_drafts(
    *,
    user_id: str,
    limit: int = 20,
    cursor: str | None = None,
) -> DraftListData:
    """Dòng 3 (GET /api/v1/drafts): Liệt kê các bản nháp thuộc về user_id có phân trang."""
    user_drafts = [
        draft
        for (owner_id, _), draft in _FAKE_DRAFTS_BY_OWNER.items()
        if owner_id == user_id
    ]
    start_index = 0
    if cursor is not None:
        for idx, draft in enumerate(user_drafts):
            if draft.draft_id == cursor:
                start_index = idx + 1
                break
        else:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Tham số lọc không hợp lệ.",
                next_action="fix_request",
            )

    sliced = user_drafts[start_index : start_index + limit]
    next_cursor = (
        sliced[-1].draft_id if (start_index + limit) < len(user_drafts) and sliced else None
    )
    return DraftListData(items=sliced, next_cursor=next_cursor)


async def get_draft_by_id(*, user_id: str, draft_id: str) -> PlanningDraft:
    """Tìm bản nháp trong phạm vi của user_id. Không tồn tại hoặc khác chủ sở hữu đều trả 404."""
    draft = _FAKE_DRAFTS_BY_OWNER.get((user_id, draft_id))
    if draft is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )
    return draft


async def patch_draft(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: DraftPatchRequest,
) -> PlanningDraft:
    """Dòng 6 (PATCH /api/v1/drafts/{draft_id}): Sửa đúng trường gửi lên, kiểm revision và cập nhật trạng thái."""
    fingerprint = json.dumps(
        {
            "draft_id": draft_id,
            "body": payload.model_dump(mode="json", exclude_unset=True),
        },
        sort_keys=True,
    )
    idem_record = _PATCH_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_result = idem_record
        if saved_fingerprint != fingerprint:
            raise AppError(
                status_code=409,
                code="IDEMPOTENCY_KEY_REUSED",
                message=(
                    "Mã lệnh đã được dùng với nội dung khác. "
                    "Giữ mã cũ khi retry đúng nội dung; tạo mã mới cho ý định mới."
                ),
                next_action="use_original_payload_or_new_command",
            )
        return saved_result

    draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id)

    # Kiểm tra xung đột revision (409 REVISION_CONFLICT)
    summary_rev_mismatch = (
        payload.expected_summary_revision is not None
        and payload.expected_summary_revision != draft.summary_revision
    )
    input_rev_mismatch = (
        payload.expected_planning_input_revision != draft.planning_input_revision
    )
    if summary_rev_mismatch or input_rev_mismatch:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={
                "summary_revision": draft.summary_revision,
                "planning_input_revision": draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    changes_fields = payload.changes.model_fields_set
    new_raw_input = draft.raw_input
    raw_input_changed = False
    if "raw_input" in changes_fields and payload.changes.raw_input != draft.raw_input:
        new_raw_input = payload.changes.raw_input
        raw_input_changed = True

    new_user_step_proposal = draft.user_step_proposal
    proposal_changed = False
    if (
        "user_step_proposal" in changes_fields
        and payload.changes.user_step_proposal != draft.user_step_proposal
    ):
        new_user_step_proposal = payload.changes.user_step_proposal
        proposal_changed = True

    new_summary = draft.summary
    summary_changed = False
    if "summary" in changes_fields and payload.changes.summary is not None:
        summary_patch = payload.changes.summary.model_dump(exclude_unset=True)
        current_summary_dict = draft.summary.model_dump()
        updated_fields: list[str] = []
        for field_name, new_val in summary_patch.items():
            if field_name == "extra_requirements" and new_val is None:
                new_val = []
            if current_summary_dict.get(field_name) != new_val:
                updated_fields.append(field_name)
                summary_changed = True

        if summary_changed:
            next_summary_rev = draft.summary_revision + 1
            new_field_sources = dict(draft.summary.field_sources)
            for field_name in updated_fields:
                new_field_sources[field_name] = FieldSource(
                    source="user_confirmed",
                    source_ref=f"summary_revision:{next_summary_rev}",
                )
            merged_summary = draft.summary.model_copy(
                update={
                    **{
                        k: ([] if k == "extra_requirements" and v is None else v)
                        for k, v in summary_patch.items()
                    },
                    "field_sources": new_field_sources,
                }
            )
            issues, can_plan = _evaluate_summary_issues(merged_summary)
            new_summary = merged_summary.model_copy(
                update={
                    "clarification_issues": issues,
                    "can_plan": can_plan,
                }
            )

    # Quy tắc tăng revision đúng một lần cho mỗi lần ghi thực sự thay đổi đầu vào
    any_input_changed = raw_input_changed or summary_changed or proposal_changed
    new_planning_input_revision = (
        draft.planning_input_revision + 1
        if any_input_changed
        else draft.planning_input_revision
    )

    if summary_changed:
        new_summary_revision = draft.summary_revision + 1
    elif raw_input_changed:
        new_summary_revision = draft.summary_revision + 1
    else:
        new_summary_revision = draft.summary_revision

    # Sửa raw_input hoặc summary làm mất xác nhận UF1; chỉ sửa user_step_proposal giữ nguyên xác nhận UF1
    if raw_input_changed or summary_changed:
        new_summary_confirmed_revision = None
        new_stage = "summary_review" if new_summary.can_plan else "collecting"
    else:
        new_summary_confirmed_revision = draft.summary_confirmed_revision
        new_stage = draft.stage

    updated_draft = draft.model_copy(
        update={
            "raw_input": new_raw_input,
            "summary": new_summary,
            "user_step_proposal": new_user_step_proposal,
            "summary_revision": new_summary_revision,
            "summary_confirmed_revision": new_summary_confirmed_revision,
            "planning_input_revision": new_planning_input_revision,
            "stage": new_stage,
        }
    )
    _FAKE_DRAFTS_BY_OWNER[(user_id, draft_id)] = updated_draft
    _PATCH_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (fingerprint, updated_draft)
    return updated_draft


async def confirm_draft_summary(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: ConfirmSummaryRequest,
) -> ConfirmSummaryResult:
    """Dòng 7 (POST /api/v1/drafts/{draft_id}/confirm-summary): Xác nhận bảng tóm tắt UF1 (A02.2, T04)."""
    fingerprint = json.dumps(
        {
            "draft_id": draft_id,
            "body": payload.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    idem_record = _CONFIRM_SUMMARY_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_result = idem_record
        if saved_fingerprint != fingerprint:
            raise AppError(
                status_code=409,
                code="IDEMPOTENCY_KEY_REUSED",
                message=(
                    "Mã lệnh đã được dùng với nội dung khác. "
                    "Giữ mã cũ khi retry đúng nội dung; tạo mã mới cho ý định mới."
                ),
                next_action="use_original_payload_or_new_command",
            )
        return saved_result

    draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id)

    # 1. Kiểm tra phiên bản bảng tóm tắt người dùng đang xem (409 REVISION_CONFLICT)
    if payload.expected_summary_revision != draft.summary_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản nguồn đã đổi. Tải bản hiện tại trước khi sửa.",
            current_revisions={"summary_revision": draft.summary_revision},
            next_action="reload_and_review",
        )

    # 2. Kiểm tra điều kiện đủ 4 thông tin bắt buộc của UF1 (A02.2)
    issues, can_plan = _evaluate_summary_issues(draft.summary)
    if not can_plan or issues:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Bảng tóm tắt chưa đủ 4 thông tin bắt buộc để chuyển sang lập kế hoạch UF2.",
            field_errors=[
                FieldError(
                    field_path=issue.field_path,
                    code=issue.code,
                    message=issue.message,
                )
                for issue in issues
            ],
            next_action="fix_fields",
        )

    # 3. Kiểm tra nguồn giờ rảnh (availability_ref) có bị lệch revision hiện tại không (409 SOURCE_CHANGED)
    avail_ref = draft.summary.availability_ref
    if avail_ref is not None:
        current_avail_rev = _CURRENT_AVAILABILITY_REVISIONS.get(
            (user_id, avail_ref.availability_id)
        )
        if current_avail_rev is not None and avail_ref.revision != current_avail_rev:
            raise AppError(
                status_code=409,
                code="SOURCE_CHANGED",
                message="Bảng tóm tắt đã được sửa. Hãy tải bản mới trước khi xác nhận.",
                current_revisions={"availability": current_avail_rev},
                next_action="rebuild_and_review",
            )

    # 4. Đóng dấu xác nhận UF1 (T04: Chỉ cập nhật summary_confirmed_revision và stage, TUYỆT ĐỐI chưa tạo Task)
    confirmed_draft = draft.model_copy(
        update={
            "stage": "summary_confirmed",
            "summary_confirmed_revision": draft.summary_revision,
        }
    )
    _FAKE_DRAFTS_BY_OWNER[(user_id, draft_id)] = confirmed_draft

    result = ConfirmSummaryResult(
        draft_id=confirmed_draft.draft_id,
        stage=confirmed_draft.stage,
        summary_confirmed_revision=confirmed_draft.summary_revision,
        summary_revision=confirmed_draft.summary_revision,
        planning_input_revision=confirmed_draft.planning_input_revision,
    )
    _CONFIRM_SUMMARY_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (
        fingerprint,
        result,
    )
    return result


async def analyze_draft(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: DraftAnalyzeRequest,
) -> PlanningDraft:
    """Dòng 5 (POST /api/v1/drafts/{draft_id}/analyze): Gọi AI trích xuất & làm rõ 1 vòng (I01, A01/A02.1, T03, T18)."""
    fingerprint = json.dumps(
        {
            "draft_id": draft_id,
            "body": payload.model_dump(mode="json", exclude_unset=True),
        },
        sort_keys=True,
    )
    idem_record = _ANALYZE_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_draft = idem_record
        if saved_fingerprint != fingerprint:
            raise AppError(
                status_code=409,
                code="IDEMPOTENCY_KEY_REUSED",
                message=(
                    "Mã lệnh đã được dùng với nội dung khác. "
                    "Giữ mã cũ khi retry đúng nội dung; tạo mã mới cho ý định mới."
                ),
                next_action="use_original_payload_or_new_command",
            )
        return saved_draft

    # Pha 1: Đọc snapshot hiện tại và kiểm tra cả 2 revision trước khi gọi AI
    snapshot_draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id)
    if (
        payload.expected_summary_revision != snapshot_draft.summary_revision
        or payload.expected_planning_input_revision
        != snapshot_draft.planning_input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={
                "summary_revision": snapshot_draft.summary_revision,
                "planning_input_revision": snapshot_draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    # Pha 2: Chỗ chờ ghép hàm của Role AI (A01/A02.1).
    # Hiện tại chưa đụng vào file ai_service.py của Role AI; tạm dùng kết quả mẫu để Router chạy được trên /docs.
    proposed_title = snapshot_draft.summary.title or "Viết báo cáo môn học"
    proposed_description = payload.raw_input or snapshot_draft.summary.description
    proposed_scope = payload.message or snapshot_draft.summary.scope

    # Pha 3: Kiểm tra lại revision hiện tại của Draft trước khi merge (T03: Chống kết quả AI cũ đè dữ liệu mới)
    current_draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id)
    if (
        current_draft.summary_revision != snapshot_draft.summary_revision
        or current_draft.planning_input_revision
        != snapshot_draft.planning_input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={
                "summary_revision": current_draft.summary_revision,
                "planning_input_revision": current_draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    next_summary_rev = current_draft.summary_revision + 1
    next_input_rev = current_draft.planning_input_revision + 1
    new_field_sources = dict(current_draft.summary.field_sources)

    merged_updates: dict[str, object] = {}
    for field_name, proposed_val in (
        ("title", proposed_title),
        ("description", proposed_description),
        ("scope", proposed_scope),
    ):
        if (
            proposed_val is not None
            and proposed_val != getattr(current_draft.summary, field_name)
        ):
            merged_updates[field_name] = proposed_val
            new_field_sources[field_name] = FieldSource(
                source="user_confirmed" if payload.message else "ai_suggested",
                source_ref=f"summary_revision:{next_summary_rev}",
            )

    merged_summary = current_draft.summary.model_copy(
        update={
            **merged_updates,
            "field_sources": new_field_sources,
        }
    )
    issues, can_plan = _evaluate_summary_issues(merged_summary)

    final_summary = merged_summary.model_copy(
        update={
            "clarification_issues": issues,
            "can_plan": can_plan,
        }
    )

    updated_draft = current_draft.model_copy(
        update={
            "raw_input": payload.raw_input or current_draft.raw_input,
            "source_thought_id": (
                payload.source_thought_id or current_draft.source_thought_id
            ),
            "summary": final_summary,
            "summary_revision": next_summary_rev,
            "summary_confirmed_revision": None,
            "planning_input_revision": next_input_rev,
            "stage": "summary_review" if can_plan else "collecting",
        }
    )
    _FAKE_DRAFTS_BY_OWNER[(user_id, draft_id)] = updated_draft
    _ANALYZE_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (
        fingerprint,
        updated_draft,
    )
    return updated_draft



