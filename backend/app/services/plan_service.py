# Tầng Service điều phối nghiệp vụ cho phương án chia bước và xếp lịch UF2 (PlanCandidate)
import json
from datetime import UTC, datetime

from app.core.errors import AppError
from app.schemas.common import (
    AvailabilityRef,
    CalendarContextRef,
    CandidateRef,
    FieldError,
    SessionSegment,
    SourceRevisions,
    TimeInterval,
)
from app.schemas.planning import (
    CandidatePart,
    CandidateSession,
    CandidateStep,
    CandidateVerificationScope,
    ConfirmCandidateRequest,
    ConfirmCandidateResult,
    FeasibilityReport,
    GenerateCandidateRequest,
    KeyMapping,
    PlanCandidate,
    ScheduleVersion,
    UpdateCandidateRequest,
)
from app.schemas.schedule import ScheduleData
from app.schemas.task import PlanningPart, Session, Step, Task, TaskListData
from app.services import draft_service


def _build_seed_candidate(
    *,
    candidate_id: str,
    draft_id: str,
    source_summary_revision: int = 5,
    source_planning_input_revision: int = 5,
    review_revision: int = 3,
    user_step_proposal: str | None = (
        "Tìm tài liệu tham khảo, sau đó viết bản nháp báo cáo."
    ),
    horizon: TimeInterval | None = None,
) -> PlanCandidate:
    """Tạo phương án mẫu chuẩn theo Dòng 9 & 10 trên API Sheet (chờ ghép AI A04 và Algorithm A05-A10)."""
    effective_horizon = horizon or TimeInterval(
        start_at=datetime.fromisoformat("2026-10-30T07:00:00+07:00"),
        end_at=datetime.fromisoformat("2026-11-13T07:00:00+07:00"),
    )
    return PlanCandidate(
        candidate_id=candidate_id,
        draft_id=draft_id,
        source_summary_revision=source_summary_revision,
        review_revision=review_revision,
        planning_parts=[
            CandidatePart(
                client_part_key="part-research",
                scope_description="Tìm tài liệu tham khảo",
                order_index=0,
                decomposition_state="complete",
                scheduling_state="complete",
                unexpanded_scope=None,
                rough_estimate_seconds=3000,
                estimate_source="user_confirmed",
            ),
            CandidatePart(
                client_part_key="part-writing",
                scope_description="Viết bản nháp báo cáo",
                order_index=1,
                decomposition_state="not_started",
                scheduling_state="not_scheduled",
                unexpanded_scope="Phần viết bản nháp chưa được chia thành Steps.",
                rough_estimate_seconds=None,
                estimate_source=None,
            ),
        ],
        steps=[
            CandidateStep(
                title="Tìm tài liệu tham khảo",
                expected_output="Danh sách tài liệu kèm nguồn trích dẫn",
                order_index=0,
                estimated_base_seconds=3000,
                remaining_base_seconds=3000,
                buffer_multiplier=1.2,
                estimate_source="user_confirmed",
                status="pending",
                client_step_key="step-research",
                client_part_key="part-research",
            )
        ],
        sessions=[
            CandidateSession(
                scheduled_start=datetime.fromisoformat("2026-10-30T09:00:00+07:00"),
                scheduled_end=datetime.fromisoformat("2026-10-30T10:10:00+07:00"),
                micro_task_text="Mở file báo cáo và đọc đề bài",
                micro_task_seconds=None,
                work_seconds=3600,
                rest_seconds=600,
                segments=[
                    SessionSegment(order_index=0, kind="work", duration_seconds=1500),
                    SessionSegment(order_index=1, kind="rest", duration_seconds=300),
                    SessionSegment(order_index=2, kind="work", duration_seconds=1500),
                    SessionSegment(order_index=3, kind="rest", duration_seconds=300),
                    SessionSegment(order_index=4, kind="work", duration_seconds=600),
                ],
                duration_seconds=4200,
                client_session_key="session-research",
                client_step_key="step-research",
                start_state="waiting",
                start_decision_due_at=datetime.fromisoformat(
                    "2026-10-30T09:15:00+07:00"
                ),
            )
        ],
        planning_horizon=effective_horizon,
        source_revisions=SourceRevisions(
            availability=2,
            preferences=4,
            schedule=7,
            calendar_context=CalendarContextRef(context_id="context_1", revision=2),
        ),
        min_inter_session_gap_seconds=900,
        feasibility=FeasibilityReport(
            status="feasible",
            required_seconds=4200,
            available_seconds=7200,
            unscheduled_seconds=0,
            deficit_seconds=0,
            verification_scope=CandidateVerificationScope(
                client_part_keys=["part-research"],
                planning_horizon=effective_horizon,
                scope_type="candidate_parts",
            ),
            fully_scheduled=True,
            blocking_reasons=[],
            unestimated_part_ids=["part-writing"],
            unplanned_part_ids=["part-writing"],
            extension_candidate_at=None,
        ),
        stale=False,
        user_step_proposal=user_step_proposal,
        source_planning_input_revision=source_planning_input_revision,
    )


# Kho dữ liệu giả lập tạm thời theo từng chủ sở hữu (user_id, candidate_id) trước khi ghép DB ở Bước 5.1
_FAKE_CANDIDATES_BY_OWNER: dict[tuple[str, str], PlanCandidate] = {
    ("user_a", "candidate_1"): _build_seed_candidate(
        candidate_id="candidate_1",
        draft_id="draft_1",
        source_summary_revision=5,
        source_planning_input_revision=5,
    ),
    ("user_b", "candidate_b_1"): _build_seed_candidate(
        candidate_id="candidate_b_1",
        draft_id="draft_b_1",
        source_summary_revision=5,
        source_planning_input_revision=5,
    ),
}

# Lưu biên nhận Idempotency-Key cho lệnh tạo Candidate: (user_id, idempotency_key) -> (fingerprint, candidate_id)
_CREATE_CANDIDATE_IDEMPOTENCY_STORE: dict[tuple[str, str], tuple[str, str]] = {}

# Lưu biên nhận Idempotency-Key cho lệnh sửa Candidate: (user_id, idempotency_key) -> (fingerprint, PlanCandidate)
_PATCH_CANDIDATE_IDEMPOTENCY_STORE: dict[
    tuple[str, str], tuple[str, PlanCandidate]
] = {}

# Lưu biên nhận Idempotency-Key cho lệnh chốt Candidate UF2: (user_id, idempotency_key) -> (fingerprint, ConfirmCandidateResult)
_CONFIRM_CANDIDATE_IDEMPOTENCY_STORE: dict[
    tuple[str, str], tuple[str, ConfirmCandidateResult]
] = {}

# Phiên bản lịch chính thức hiện tại theo từng user_id (khởi tạo ở 7 theo mẫu Dòng 9-12 trên Sheet)
_USER_SCHEDULE_REVISIONS: dict[str, int] = {
    "user_a": 7,
    "user_b": 1,
}

# Kho lưu các Task chính thức sau khi chốt UF2 theo (user_id, task_id)
_FAKE_TASKS_BY_OWNER: dict[tuple[str, str], Task] = {}



_KNOWN_SOURCE_PART_KEYS: set[str] = {"part-research", "part-writing"}


def _compute_candidate_stale(user_id: str, candidate: PlanCandidate) -> bool:
    """Tính động cờ stale từ revision hiện tại của Draft nguồn (T05, dto_catalog.md mục PlanCandidate)."""
    draft = draft_service._FAKE_DRAFTS_BY_OWNER.get((user_id, candidate.draft_id))
    if draft is None:
        return True
    if draft.summary_revision != candidate.source_summary_revision:
        return True
    if draft.planning_input_revision != candidate.source_planning_input_revision:
        return True
    if (
        draft.summary_confirmed_revision is not None
        and draft.summary_confirmed_revision != candidate.source_summary_revision
    ):
        return True
    return candidate.stale


async def create_candidate(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: GenerateCandidateRequest,
) -> PlanCandidate:
    """Dòng 9 (POST /api/v1/drafts/{draft_id}/candidates): Tạo phương án chia bước và xếp lịch UF2 (T06, T07, T08, T10)."""
    fingerprint = json.dumps(
        {
            "draft_id": draft_id,
            "body": payload.model_dump(mode="json", exclude_unset=True),
        },
        sort_keys=True,
    )
    idem_record = _CREATE_CANDIDATE_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_candidate_id = idem_record
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
        return await get_candidate_by_id(
            user_id=user_id, candidate_id=saved_candidate_id
        )

    draft = await draft_service.get_draft_by_id(user_id=user_id, draft_id=draft_id)

    # 1. Kiểm tra source_summary_revision và source_planning_input_revision khớp với Draft hiện tại
    if (
        payload.source_summary_revision != draft.summary_revision
        or payload.source_planning_input_revision != draft.planning_input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản nguồn đã đổi. Tải bản hiện tại trước khi sửa.",
            current_revisions={
                "summary_revision": draft.summary_revision,
                "planning_input_revision": draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    # 2. Kiểm tra Draft đã xác nhận UF1 chưa (T06: chỉ tạo Candidate sau khi đã confirm-summary)
    if (
        draft.summary_confirmed_revision is None
        or draft.summary_confirmed_revision != draft.summary_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Bản nháp chưa xác nhận thông tin UF1 ở phiên bản hiện tại.",
            current_revisions={
                "summary_revision": draft.summary_revision,
                "planning_input_revision": draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    # 3. Nếu gửi selected_part_keys, chỉ chấp nhận các client_part_key đã tồn tại trong nguồn
    if payload.selected_part_keys is not None:
        unknown_keys = [
            key
            for key in payload.selected_part_keys
            if key not in _KNOWN_SOURCE_PART_KEYS
        ]
        if unknown_keys:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Thông tin đầu vào để chia việc và xếp lịch chưa hợp lệ.",
                field_errors=[
                    FieldError(
                        field_path="selected_part_keys",
                        code="UNKNOWN_PART_KEY",
                        message=f"Không nhận diện được client_part_key: {', '.join(unknown_keys)}.",
                    )
                ],
                next_action="fix_fields",
            )

    # TODO(AI A04/A12.0 & Algorithm A05-A10): Ghép hàm chia bước và xếp lịch tại đây khi 2 role bàn giao.
    new_candidate_id = f"candidate_{len(_FAKE_CANDIDATES_BY_OWNER) + 1}"
    candidate = _build_seed_candidate(
        candidate_id=new_candidate_id,
        draft_id=draft.draft_id,
        source_summary_revision=draft.summary_revision,
        source_planning_input_revision=draft.planning_input_revision,
        review_revision=1,
        user_step_proposal=draft.user_step_proposal
        or "Tìm tài liệu tham khảo, sau đó viết bản nháp báo cáo.",
        horizon=payload.planning_horizon,
    )
    _FAKE_CANDIDATES_BY_OWNER[(user_id, new_candidate_id)] = candidate

    # Cập nhật tham chiếu candidate_ref trên Draft (không làm tăng planning_input_revision hay summary_revision)
    updated_draft = draft.model_copy(
        update={
            "candidate_ref": CandidateRef(
                candidate_id=new_candidate_id,
                review_revision=candidate.review_revision,
            ),
            "stage": "candidate_review",
        }
    )
    draft_service._FAKE_DRAFTS_BY_OWNER[(user_id, draft.draft_id)] = updated_draft
    _CREATE_CANDIDATE_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (
        fingerprint,
        new_candidate_id,
    )
    return candidate


async def get_candidate_by_id(*, user_id: str, candidate_id: str) -> PlanCandidate:
    """Dòng 10 (GET /api/v1/candidates/{candidate_id}): Đọc lại phương án lịch và tính động cờ stale."""
    candidate = _FAKE_CANDIDATES_BY_OWNER.get((user_id, candidate_id))
    if candidate is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )
    is_stale = _compute_candidate_stale(user_id, candidate)
    if is_stale != candidate.stale:
        candidate = candidate.model_copy(update={"stale": is_stale})
        _FAKE_CANDIDATES_BY_OWNER[(user_id, candidate_id)] = candidate
    return candidate


async def patch_candidate(
    *,
    user_id: str,
    candidate_id: str,
    idempotency_key: str,
    payload: UpdateCandidateRequest,
) -> PlanCandidate:
    """Dòng 11 (PATCH /api/v1/candidates/{candidate_id}): Sửa phương án nháp theo Allowlist và tăng review_revision (T11)."""
    fingerprint = json.dumps(
        {
            "candidate_id": candidate_id,
            "body": payload.model_dump(mode="json", exclude_unset=True),
        },
        sort_keys=True,
    )
    idem_record = _PATCH_CANDIDATE_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_candidate = idem_record
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
        return saved_candidate

    candidate = await get_candidate_by_id(user_id=user_id, candidate_id=candidate_id)

    # 1. Nếu Candidate đã stale do Draft nguồn bị sửa -> chặn 409 SOURCE_CHANGED
    if candidate.stale:
        raise AppError(
            status_code=409,
            code="SOURCE_CHANGED",
            message="Đầu vào hoặc nguồn lập kế hoạch đã đổi. Tải lại và xem phương án mới.",
            next_action="reload_and_review",
        )

    # 2. Kiểm tra expected_review_revision khớp với review_revision hiện tại (409 REVISION_CONFLICT)
    if payload.expected_review_revision != candidate.review_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={"review_revision": candidate.review_revision},
            next_action="reload_and_review",
        )

    # 3. Áp dụng chỉnh sửa theo client_part_key
    new_parts = list(candidate.planning_parts)
    if payload.edits.planning_parts:
        parts_by_key = {p.client_part_key: idx for idx, p in enumerate(new_parts)}
        for part_edit in payload.edits.planning_parts:
            if part_edit.client_part_key not in parts_by_key:
                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message="Thông tin đầu vào để chia việc và xếp lịch chưa hợp lệ.",
                    field_errors=[
                        FieldError(
                            field_path="edits.planning_parts",
                            code="UNKNOWN_PART_KEY",
                            message=f"Không tìm thấy client_part_key '{part_edit.client_part_key}'.",
                        )
                    ],
                    next_action="fix_fields",
                )
            idx = parts_by_key[part_edit.client_part_key]
            patch_dict = part_edit.model_dump(
                exclude_unset=True, exclude={"client_part_key"}
            )
            if "rough_estimate_seconds" in patch_dict:
                patch_dict["estimate_source"] = "user_confirmed"
            new_parts[idx] = new_parts[idx].model_copy(update=patch_dict)

    # 4. Áp dụng chỉnh sửa theo client_step_key
    new_steps = list(candidate.steps)
    if payload.edits.steps:
        steps_by_key = {s.client_step_key: idx for idx, s in enumerate(new_steps)}
        for step_edit in payload.edits.steps:
            if step_edit.client_step_key not in steps_by_key:
                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message="Thông tin đầu vào để chia việc và xếp lịch chưa hợp lệ.",
                    field_errors=[
                        FieldError(
                            field_path="edits.steps",
                            code="UNKNOWN_STEP_KEY",
                            message=f"Không tìm thấy client_step_key '{step_edit.client_step_key}'.",
                        )
                    ],
                    next_action="fix_fields",
                )
            idx = steps_by_key[step_edit.client_step_key]
            patch_dict = step_edit.model_dump(
                exclude_unset=True, exclude={"client_step_key"}
            )
            if (
                "estimated_base_seconds" in patch_dict
                and patch_dict["estimated_base_seconds"] is not None
            ):
                patch_dict["remaining_base_seconds"] = patch_dict[
                    "estimated_base_seconds"
                ]
                patch_dict["estimate_source"] = "user_confirmed"
            new_steps[idx] = new_steps[idx].model_copy(update=patch_dict)

    # 5. Áp dụng chỉnh sửa theo client_session_key
    new_sessions = list(candidate.sessions)
    if payload.edits.sessions:
        sessions_by_key = {
            s.client_session_key: idx for idx, s in enumerate(new_sessions)
        }
        for sess_edit in payload.edits.sessions:
            if sess_edit.client_session_key not in sessions_by_key:
                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message="Thông tin đầu vào để chia việc và xếp lịch chưa hợp lệ.",
                    field_errors=[
                        FieldError(
                            field_path="edits.sessions",
                            code="UNKNOWN_SESSION_KEY",
                            message=f"Không tìm thấy client_session_key '{sess_edit.client_session_key}'.",
                        )
                    ],
                    next_action="fix_fields",
                )
            idx = sessions_by_key[sess_edit.client_session_key]
            patch_dict = sess_edit.model_dump(
                exclude_unset=True, exclude={"client_session_key"}
            )
            new_sessions[idx] = new_sessions[idx].model_copy(update=patch_dict)

    # TODO(Algorithm A05-A10): Gọi lại hàm tính thời lượng và feasibility từ scheduler_engine khi Role Algorithm bàn giao.
    next_review_rev = candidate.review_revision + 1
    updated_candidate = candidate.model_copy(
        update={
            "planning_parts": new_parts,
            "steps": new_steps,
            "sessions": new_sessions,
            "review_revision": next_review_rev,
        }
    )
    _FAKE_CANDIDATES_BY_OWNER[(user_id, candidate_id)] = updated_candidate

    # Đồng bộ lại candidate_ref.review_revision trên Draft nguồn
    draft = draft_service._FAKE_DRAFTS_BY_OWNER.get((user_id, candidate.draft_id))
    if draft is not None and draft.candidate_ref is not None:
        if draft.candidate_ref.candidate_id == candidate_id:
            draft_service._FAKE_DRAFTS_BY_OWNER[(user_id, candidate.draft_id)] = (
                draft.model_copy(
                    update={
                        "candidate_ref": CandidateRef(
                            candidate_id=candidate_id,
                            review_revision=next_review_rev,
                        )
                    }
                )
            )

    _PATCH_CANDIDATE_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (
        fingerprint,
        updated_candidate,
    )
    return updated_candidate


async def confirm_candidate(
    *,
    user_id: str,
    candidate_id: str,
    idempotency_key: str,
    payload: ConfirmCandidateRequest,
) -> ConfirmCandidateResult:
    """Dòng 12 (POST /api/v1/candidates/{candidate_id}/confirm): Duyệt và lưu kế hoạch chính thức nguyên tử (A18, T12-T16)."""
    fingerprint = json.dumps(
        {
            "candidate_id": candidate_id,
            "body": payload.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    idem_record = _CONFIRM_CANDIDATE_IDEMPOTENCY_STORE.get((user_id, idempotency_key))
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

    candidate = await get_candidate_by_id(user_id=user_id, candidate_id=candidate_id)
    draft = await draft_service.get_draft_by_id(
        user_id=user_id, draft_id=candidate.draft_id
    )

    # 1. Kiểm tra đồng bộ review_revision, summary_revision và planning_input_revision (409 REVISION_CONFLICT)
    if (
        payload.expected_review_revision != candidate.review_revision
        or payload.expected_summary_revision != draft.summary_revision
        or payload.expected_planning_input_revision != draft.planning_input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản nguồn đã đổi. Tải bản hiện tại trước khi sửa.",
            current_revisions={
                "review_revision": candidate.review_revision,
                "summary_revision": draft.summary_revision,
                "planning_input_revision": draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    # 2. Kiểm tra Candidate chưa bị stale và expected_source_revisions khớp với nguồn hiện tại (T12, T14 -> 409 SOURCE_CHANGED)
    current_schedule_rev = _USER_SCHEDULE_REVISIONS.get(user_id, 1)
    if (
        candidate.stale
        or payload.expected_source_revisions != candidate.source_revisions
        or payload.expected_source_revisions.schedule != current_schedule_rev
    ):
        raise AppError(
            status_code=409,
            code="SOURCE_CHANGED",
            message=(
                "Dữ liệu dùng tạo phương án đã thay đổi. "
                "Hãy tải lại và xem lại trước khi xác nhận."
            ),
            current_revisions={"schedule": current_schedule_rev},
            next_action="rebuild_and_review",
        )

    # 3. Kiểm tra phương án phải ở trạng thái khả thi và Draft đủ điều kiện (409 PLAN_NOT_CONFIRMABLE)
    if (
        candidate.feasibility.status != "feasible"
        or draft.summary.time_constraint is None
        or draft.summary.availability_ref is None
    ):
        raise AppError(
            status_code=409,
            code="PLAN_NOT_CONFIRMABLE",
            message=(
                "Chưa có lịch hợp lệ để xác nhận. "
                "Hãy bổ sung dữ liệu và tính lại phương án."
            ),
            next_action="review_feasibility",
        )

    # 4. Kiểm tra khoảng cách tối thiểu 900 giây giữa các phiên (T08, T14)
    existing_sessions = [
        sess
        for (owner_id, _), existing_task in _FAKE_TASKS_BY_OWNER.items()
        if owner_id == user_id
        for sess in existing_task.sessions
    ]
    all_sessions_sorted = sorted(
        [*existing_sessions, *candidate.sessions],
        key=lambda s: s.scheduled_start,
    )
    for idx in range(len(all_sessions_sorted) - 1):
        curr_s = all_sessions_sorted[idx]
        next_s = all_sessions_sorted[idx + 1]
        gap_seconds = (
            next_s.scheduled_start - curr_s.scheduled_end
        ).total_seconds()
        if gap_seconds < candidate.min_inter_session_gap_seconds:
            raise AppError(
                status_code=409,
                code="PLAN_NOT_CONFIRMABLE",
                message="Phiên làm việc bị trùng hoặc chưa đủ khoảng cách nghỉ 900 giây với lịch hiện tại.",
                next_action="review_feasibility",
            )

    # 5. Sinh ID chính thức trên Server và bảng ánh xạ key_mapping (nguyên tử trong 1 bước ghi)
    next_task_num = len(_FAKE_TASKS_BY_OWNER) + 1
    task_id = f"task_{next_task_num}"
    next_schedule_rev = current_schedule_rev + 1

    part_mapping: dict[str, str] = {}
    persisted_parts: list[PlanningPart] = []
    for p_idx, c_part in enumerate(candidate.planning_parts, start=1):
        part_id = f"part_{p_idx}" if next_task_num == 1 else f"part_{next_task_num}_{p_idx}"
        part_mapping[c_part.client_part_key] = part_id
        persisted_parts.append(
            PlanningPart(
                planning_part_id=part_id,
                task_id=task_id,
                client_part_key=c_part.client_part_key,
                scope_description=c_part.scope_description,
                order_index=c_part.order_index,
                decomposition_state=c_part.decomposition_state,
                scheduling_state=c_part.scheduling_state,
                unexpanded_scope=c_part.unexpanded_scope,
                rough_estimate_seconds=c_part.rough_estimate_seconds,
                estimate_source=c_part.estimate_source,
            )
        )

    step_mapping: dict[str, str] = {}
    persisted_steps: list[Step] = []
    for s_idx, c_step in enumerate(candidate.steps, start=1):
        step_id = f"step_{s_idx}" if next_task_num == 1 else f"step_{next_task_num}_{s_idx}"
        step_mapping[c_step.client_step_key] = step_id
        persisted_steps.append(
            Step(
                step_id=step_id,
                task_id=task_id,
                planning_part_id=part_mapping[c_step.client_part_key],
                title=c_step.title,
                expected_output=c_step.expected_output,
                order_index=c_step.order_index,
                estimated_base_seconds=c_step.estimated_base_seconds,
                remaining_base_seconds=c_step.remaining_base_seconds,
                buffer_multiplier=c_step.buffer_multiplier,
                estimate_source=c_step.estimate_source,
                status=c_step.status,
            )
        )

    session_mapping: dict[str, str] = {}
    persisted_sessions: list[Session] = []
    for sess_idx, c_sess in enumerate(candidate.sessions, start=1):
        session_id = (
            f"session_{sess_idx}"
            if next_task_num == 1
            else f"session_{next_task_num}_{sess_idx}"
        )
        session_mapping[c_sess.client_session_key] = session_id
        persisted_sessions.append(
            Session(
                session_id=session_id,
                step_id=step_mapping[c_sess.client_step_key],
                scheduled_start=c_sess.scheduled_start,
                scheduled_end=c_sess.scheduled_end,
                micro_task_text=c_sess.micro_task_text,
                micro_task_seconds=c_sess.micro_task_seconds,
                work_seconds=c_sess.work_seconds,
                rest_seconds=c_sess.rest_seconds,
                segments=c_sess.segments,
                duration_seconds=c_sess.duration_seconds,
                schedule_revision=next_schedule_rev,
                status="scheduled",
                calendar_event_link_id=None,
                start_state=c_sess.start_state,
                start_decision_due_at=c_sess.start_decision_due_at,
            )
        )

    all_parts_complete = all(
        p.decomposition_state == "complete" for p in persisted_parts
    )
    all_parts_scheduled = all(
        p.scheduling_state == "complete" for p in persisted_parts
    )
    persisted_task = Task(
        task_id=task_id,
        title=draft.summary.title or "Công việc mới",
        description=draft.summary.description or draft.raw_input or "Công việc mới",
        scope=draft.summary.scope or "",
        extra_requirements=list(draft.summary.extra_requirements),
        time_constraint=draft.summary.time_constraint,
        importance=draft.summary.importance,
        status="pending",
        source_draft_id=draft.draft_id,
        decomposition_state="complete" if all_parts_complete else "partial",
        scheduling_state="complete" if all_parts_scheduled else "partial",
        availability_ref=AvailabilityRef(
            availability_id=draft.summary.availability_ref.availability_id,
            revision=draft.summary.availability_ref.revision,
        ),
        planning_parts=persisted_parts,
        steps=persisted_steps,
        sessions=persisted_sessions,
        source_summary=draft.summary,
    )

    result = ConfirmCandidateResult(
        app_save_state="saved",
        task=persisted_task,
        key_mapping=KeyMapping(
            planning_parts=part_mapping,
            steps=step_mapping,
            sessions=session_mapping,
        ),
        schedule_version=ScheduleVersion(
            version_number=next_schedule_rev,
            base_version=current_schedule_rev,
            approved_at=datetime.now(UTC),
            affected_task_ids=[task_id],
            min_inter_session_gap_seconds=candidate.min_inter_session_gap_seconds,
            alarm_sync_state="pending",
            calendar_sync_summary="pending",
        ),
        alarm_sync_state="pending",
        calendar_sync_summary="pending",
    )

    # Cập nhật nguyên tử vào kho dữ liệu của User
    _FAKE_TASKS_BY_OWNER[(user_id, task_id)] = persisted_task
    _USER_SCHEDULE_REVISIONS[user_id] = next_schedule_rev
    draft_service._FAKE_DRAFTS_BY_OWNER[(user_id, draft.draft_id)] = draft.model_copy(
        update={
            "confirmed_task_id": task_id,
            "stage": "confirmed",
        }
    )
    _CONFIRM_CANDIDATE_IDEMPOTENCY_STORE[(user_id, idempotency_key)] = (
        fingerprint,
        result,
    )
    return result


async def list_tasks(
    *,
    user_id: str,
    limit: int = 20,
    cursor: str | None = None,
) -> TaskListData:
    """Dòng 13 (GET /api/v1/tasks): Liệt kê các công việc chính thức thuộc về user_id có phân trang."""
    user_tasks = [
        task
        for (owner_id, _), task in _FAKE_TASKS_BY_OWNER.items()
        if owner_id == user_id
    ]
    start_index = 0
    if cursor is not None:
        for idx, task in enumerate(user_tasks):
            if task.task_id == cursor:
                start_index = idx + 1
                break
        else:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Tham số lọc không hợp lệ.",
                next_action="fix_request",
            )

    sliced = user_tasks[start_index : start_index + limit]
    next_cursor = (
        sliced[-1].task_id
        if (start_index + limit) < len(user_tasks) and sliced
        else None
    )
    return TaskListData(items=sliced, next_cursor=next_cursor)


async def get_task_by_id(*, user_id: str, task_id: str) -> Task:
    """Dòng 14 (GET /api/v1/tasks/{task_id}): Đọc chi tiết 1 công việc chính thức của đúng chủ sở hữu."""
    task = _FAKE_TASKS_BY_OWNER.get((user_id, task_id))
    if task is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )
    return task


async def get_schedule(
    *,
    user_id: str,
    from_at: datetime,
    to_at: datetime,
    limit: int = 50,
    cursor: str | None = None,
) -> ScheduleData:
    """Dòng 15 (GET /api/v1/schedule): Đọc lịch các phiên chính thức trong khoảng [from_at, to_at) (T19)."""
    if from_at.tzinfo is None or to_at.tzinfo is None or from_at >= to_at:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Tham số lọc không hợp lệ.",
            field_errors=[
                FieldError(
                    field_path="from",
                    code="INVALID_TIME_RANGE",
                    message="Mốc 'from' và 'to' phải có múi giờ và 'from' phải đứng trước 'to'.",
                )
            ],
            next_action="fix_request",
        )

    # Chỉ lấy các Session thuộc Task ĐÃ XÁC NHẬN của user_id (tuyệt đối không gộp Candidate chưa duyệt - T19)
    matching_sessions = [
        sess
        for (owner_id, _), task in _FAKE_TASKS_BY_OWNER.items()
        if owner_id == user_id
        for sess in task.sessions
        if sess.scheduled_start < to_at and sess.scheduled_end > from_at
    ]
    matching_sessions.sort(key=lambda s: s.scheduled_start)

    start_index = 0
    if cursor is not None:
        for idx, sess in enumerate(matching_sessions):
            if sess.session_id == cursor:
                start_index = idx + 1
                break
        else:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Tham số lọc không hợp lệ.",
                next_action="fix_request",
            )

    sliced = matching_sessions[start_index : start_index + limit]
    next_cursor = (
        sliced[-1].session_id
        if (start_index + limit) < len(matching_sessions) and sliced
        else None
    )
    current_schedule_rev = _USER_SCHEDULE_REVISIONS.get(user_id, 1)
    return ScheduleData(
        schedule_revision=current_schedule_rev,
        items=sliced,
        next_cursor=next_cursor,
        alarm_sync_state="pending",
        calendar_sync_summary="pending",
    )




