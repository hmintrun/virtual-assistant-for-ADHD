# API endpoints cho UF7 hỗ trợ UF1-UF2: Đọc cài đặt trợ lý (Dòng 39) và giờ rảnh đã xác nhận (Dòng 40)
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.errors import AppError
from app.models.settings import SettingChangeRequest
from app.models.task import Task as TaskRow
from app.models.user import UserPreference as PreferenceRow
from app.schemas.common import (
    ErrorEnvelope,
    SuccessEnvelope,
    build_response_meta,
)
from app.schemas.settings import (
    Availability,
    SettingsChangeRequest,
    SettingsChangeResult,
    UserPreference,
)
from app.services.command_service import durable_command, owner_uuid
from app.services.draft_service import get_user_availability
from app.services.plan_service import get_preferences as load_preferences
from app.services.scheduler_engine import compute_effective_free_intervals

router = APIRouter(tags=["UF7 - Settings & Availability"])


@router.get(
    "/preferences",
    response_model=SuccessEnvelope[UserPreference],
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Chưa đăng nhập hoặc thiếu Bearer Token",
        },
    },
    summary="Đọc cài đặt trợ lý (Dòng 39 trên API Sheet)",
)
async def get_preferences(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[UserPreference]:
    """Trả về cấu hình trợ lý đang có hiệu lực và yêu cầu đang chờ duyệt của người dùng."""
    pref = await load_preferences(user_id=current_user.user_id, db=db)
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=pref,
        meta=build_response_meta(request_id),
    )


@router.get(
    "/availability",
    response_model=SuccessEnvelope[Availability],
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Chưa đăng nhập hoặc thiếu Bearer Token",
        },
        422: {
            "model": ErrorEnvelope,
            "description": "Cặp tham số truy vấn from/to không hợp lệ",
        },
    },
    summary="Đọc nguồn giờ rảnh cũ; UF1 mới dùng summary.availability (Dòng 40)",
)
async def get_availability(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    from_at: Annotated[
        datetime | None,
        Query(
            alias="from",
            description="Mốc bắt đầu triển khai khoảng giờ rảnh (ISO-8601 có múi giờ)",
        ),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(
            alias="to",
            description="Mốc kết thúc triển khai khoảng giờ rảnh (ISO-8601 có múi giờ)",
        ),
    ] = None,
) -> SuccessEnvelope[Availability]:
    """Đọc nguồn cũ nếu tồn tại; không phải bước bắt buộc trước UF1 mới."""
    # Quy tắc hợp đồng Dòng 40 (T21, T24): from và to bắt buộc gửi cùng nhau hoặc cùng bỏ trống
    if (from_at is None) != (to_at is None):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Cần gửi cặp from/to hợp lệ.",
            next_action="fix_request",
        )
    if from_at is not None and to_at is not None:
        if from_at.tzinfo is None or to_at.tzinfo is None or from_at >= to_at:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Cần gửi cặp from/to hợp lệ.",
                next_action="fix_request",
            )

    base_avail = await get_user_availability(current_user.user_id, db)
    if base_avail is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không có nguồn giờ rảnh cũ. Hãy nhập giờ cùng yêu cầu tạo kế hoạch ở UF1.",
            next_action="provide_draft_availability",
        )
    result_avail = base_avail
    if from_at is not None and to_at is not None:
        # Khai triển nguồn giờ rảnh; UF2 còn phải trừ lịch bận/lịch đã chốt và kiểm collision.
        result_avail = base_avail.model_copy(
            update={
                "effective_intervals": compute_effective_free_intervals(base_avail, from_at, to_at),
            }
        )

    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=result_avail,
        meta=build_response_meta(request_id),
    )


@durable_command("settings_change", SettingsChangeResult)
async def apply_settings_change(
    *, user_id: str, idempotency_key: str, payload: SettingsChangeRequest, db: AsyncSession
):
    from app.schemas.draft import UpdateDraftRequest
    from app.services import draft_service

    owner = owner_uuid(user_id)
    preferences = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    pref_revision = preferences.effective_revision if preferences else 0
    if (
        payload.expected_preferences_revision is not None
        and payload.expected_preferences_revision != pref_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Cấu hình đã thay đổi.",
            current_revisions={"preferences": pref_revision},
            next_action="reload_and_review",
        )
    updated_draft = None
    if payload.changes.availability is not None:
        if payload.target_task_id is not None:
            from app.services import plan_service

            task = await plan_service.get_task_by_id(
                user_id=user_id, task_id=payload.target_task_id, db=db
            )
            if task.revision != payload.expected_task_revision:
                raise AppError(
                    status_code=409,
                    code="REVISION_CONFLICT",
                    message="Task đã thay đổi.",
                    next_action="reload_and_review",
                )
            raise AppError(
                status_code=503,
                code="POLICY_NOT_CONFIGURED",
                message="Chỉnh giờ Task cần xem tác động/UF6; giờ và lịch hiện hành được giữ nguyên.",
                next_action="wait_for_configuration",
            )
        if payload.target_draft_id is None or payload.expected_planning_input_revision is None:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Cần Draft và revision để chỉnh giờ.",
                next_action="fix_fields",
            )
        draft = await draft_service.get_draft_by_id(
            user_id=user_id, draft_id=payload.target_draft_id, db=db
        )
        if draft.confirmed_task_id is not None:
            raise AppError(
                status_code=409,
                code="PLAN_NOT_CONFIRMABLE",
                message="Draft đã thành Task; cần chỉnh Task qua xem tác động.",
                next_action="review_task",
            )
        updated_draft = await draft_service.patch_draft(
            user_id=user_id,
            draft_id=payload.target_draft_id,
            idempotency_key=idempotency_key,
            payload=UpdateDraftRequest(
                expected_summary_revision=payload.expected_summary_revision,
                expected_planning_input_revision=payload.expected_planning_input_revision,
                changes={
                    "summary": {
                        "availability": payload.changes.availability.model_dump(mode="json")
                    }
                },
            ),
            db=db,
        )
    if payload.changes.preferences is not None:
        values = payload.changes.preferences.model_dump(exclude_unset=True)
        required = {
            "work_interval_seconds",
            "rest_interval_seconds",
            "buffer_multiplier",
            "reminder_offsets",
            "reminder_style",
        }
        if preferences is None and not required.issubset(values):
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Lần đầu cần đủ cấu hình làm/nghỉ, buffer và nhắc.",
                next_action="fix_fields",
            )
        work = values.get(
            "work_interval_seconds", preferences.work_interval_seconds if preferences else 0
        )
        rest = values.get(
            "rest_interval_seconds", preferences.rest_interval_seconds if preferences else 0
        )
        buffer = values.get(
            "buffer_multiplier", preferences.buffer_multiplier if preferences else 0
        )
        offsets = values.get(
            "reminder_offsets", preferences.reminder_offsets if preferences else []
        )
        style = values.get("reminder_style", preferences.reminder_style if preferences else "")
        if (
            work <= 0
            or rest < 0
            or buffer < 1
            or not 1 <= len(offsets) <= 3
            or len(set(offsets)) != len(offsets)
            or any(offset < 0 for offset in offsets)
            or not style.strip()
        ):
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Cấu hình không hợp lệ.",
                next_action="fix_fields",
            )
        has_tasks = await db.scalar(select(TaskRow.id).where(TaskRow.user_id == owner).limit(1))
        if has_tasks is not None and any(
            k != "reminder_style" and (preferences is None or getattr(preferences, k) != v)
            for k, v in values.items()
        ):
            raise AppError(
                status_code=503,
                code="POLICY_NOT_CONFIGURED",
                message="Thay cấu hình ảnh hưởng lịch/nhắc cần luồng xem tác động.",
                next_action="wait_for_configuration",
            )
        if preferences is None:
            preferences = PreferenceRow(
                user_id=owner, effective_revision=1, micro_task_seconds=120, **values
            )
            db.add(preferences)
        elif any(getattr(preferences, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(preferences, k, v)
            preferences.effective_revision += 1
    await db.flush()
    change = SettingChangeRequest(
        user_id=owner,
        application_state="applied_to_draft" if updated_draft else "effective_from_next_session",
        effective_revision=preferences.effective_revision if preferences else 0,
        effective_availability_revision=None,
        requested_changes={
            "target_draft_id": payload.target_draft_id,
            "target_task_id": payload.target_task_id,
            **payload.changes.model_dump(mode="json", exclude_unset=True),
        },
    )
    db.add(change)
    await db.flush()
    return SettingsChangeResult(
        change_request_id=str(change.id),
        request_revision=change.revision,
        application_state=change.application_state,
        requested_revision=None,
        effective_revision=change.effective_revision,
        requested_availability_revision=None,
        effective_availability_revision=None,
        requested_changes=payload.changes,
        pending_proposal_id=None,
        target_draft_id=payload.target_draft_id,
        target_task_id=payload.target_task_id,
        summary_revision=updated_draft.summary_revision if updated_draft else None,
        planning_input_revision=updated_draft.planning_input_revision if updated_draft else None,
    )


@router.post(
    "/settings/change-requests",
    response_model=SuccessEnvelope[SettingsChangeResult],
    summary="Chỉnh cấu hình hoặc giờ đã khai báo của Draft/Task (Dòng 41)",
)
async def create_settings_change(
    payload: SettingsChangeRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
):
    result = await apply_settings_change(
        user_id=current_user.user_id, idempotency_key=idempotency_key, payload=payload, db=db
    )
    return SuccessEnvelope(data=result, meta=build_response_meta())
