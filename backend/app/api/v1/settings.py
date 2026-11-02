# API endpoints cho UF7 hỗ trợ UF1-UF2: Đọc cài đặt trợ lý (Dòng 39) và giờ rảnh đã xác nhận (Dòng 40)
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.errors import AppError
from app.models.settings import Availability as AvailabilityRow
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
    AvailabilityChanges,
    SettingsChangeRequest,
    SettingsChangeResult,
    UserPreference,
)
from app.services.command_service import durable_command, lock_owner, owner_uuid
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
    summary="Đọc giờ rảnh đã xác nhận (Dòng 40 trên API Sheet)",
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
    """Trả về bộ giờ rảnh đã được người dùng xác nhận; triển khai effective_intervals khi gửi đủ cặp from/to."""
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
            message="Bạn chưa có bộ giờ rảnh. Hãy thiết lập và xác nhận giờ rảnh trước.",
            next_action="configure_availability",
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
    await lock_owner(db, user_id)
    owner = owner_uuid(user_id)
    availability = await db.scalar(select(AvailabilityRow).where(AvailabilityRow.user_id == owner))
    preferences = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    av_revision = availability.revision if availability else 0
    pref_revision = preferences.effective_revision if preferences else 0
    for expected, actual in (
        (payload.expected_availability_revision, av_revision),
        (payload.expected_preferences_revision, pref_revision),
    ):
        if expected is not None and expected != actual:
            raise AppError(
                status_code=409,
                code="REVISION_CONFLICT",
                message="Nguồn cài đặt hoặc giờ rảnh đã thay đổi.",
                current_revisions={"availability": av_revision, "preferences": pref_revision},
                next_action="reload_and_review",
            )
    if await db.scalar(select(TaskRow.id).where(TaskRow.user_id == owner).limit(1)) is not None:
        raise AppError(
            status_code=503,
            code="POLICY_NOT_CONFIGURED",
            message="Đã có kế hoạch. Chưa thể áp thay đổi khi luồng xem tác động/UF6 chưa sẵn sàng.",
            next_action="wait_for_configuration",
        )
    changes = payload.changes
    if changes.availability is not None:
        values = {
            "timezone": availability.timezone if availability else None,
            "weekly_rules": availability.weekly_rules if availability else [],
            "date_overrides": availability.date_overrides if availability else [],
            "task_limits": availability.task_limits if availability else [],
        }
        existing_values = (
            AvailabilityChanges.model_validate(values).model_dump(mode="json", exclude_unset=True)
            if availability is not None
            else {}
        )
        values.update(changes.availability.model_dump(mode="json", exclude_unset=True))
        try:
            if not isinstance(values["timezone"], str):
                raise ValueError("Lần đầu cần timezone")
            ZoneInfo(values["timezone"])
            checked = Availability(
                availability_id=str(availability.id) if availability else "initial",
                revision=av_revision,
                source="user_confirmed",
                confirmed_at=datetime.now(UTC),
                effective_intervals=None,
                calendar_context=None,
                **values,
            )
        except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Múi giờ hoặc quy tắc giờ rảnh không hợp lệ.",
                next_action="fix_fields",
            ) from exc
        if not checked.weekly_rules and not checked.date_overrides:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Cần ít nhất một khung giờ rảnh.",
                next_action="fix_fields",
            )
        values = checked.model_dump(
            mode="json", include={"timezone", "weekly_rules", "date_overrides", "task_limits"}
        )
        if availability is None:
            availability = AvailabilityRow(
                user_id=owner, source="user_confirmed", confirmed_at=checked.confirmed_at, **values
            )
            db.add(availability)
        elif (
            any(existing_values.get(k) != v for k, v in values.items())
            or availability.confirmed_at is None
        ):
            for name, value in values.items():
                setattr(availability, name, value)
            availability.confirmed_at = checked.confirmed_at
            availability.source = "user_confirmed"
    if changes.preferences is not None:
        values = changes.preferences.model_dump(exclude_unset=True)
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
                message="Lần đầu cần đủ cài đặt làm/nghỉ, hệ số dự phòng, mốc và kiểu nhắc.",
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
            or len(offsets) != 1
            or offsets[0] < 0
            or not style.strip()
        ):
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Cài đặt không hợp lệ.",
                next_action="fix_fields",
            )
        if preferences is None:
            preferences = PreferenceRow(
                user_id=owner, effective_revision=1, micro_task_seconds=None, **values
            )
            db.add(preferences)
        elif any(getattr(preferences, k) != v for k, v in values.items()):
            for name, value in values.items():
                setattr(preferences, name, value)
            preferences.effective_revision += 1
    await db.flush()
    change = SettingChangeRequest(
        user_id=owner,
        application_state="effective_from_next_session",
        effective_revision=preferences.effective_revision if preferences else 0,
        effective_availability_revision=availability.revision if availability else 0,
        requested_changes=changes.model_dump(mode="json", exclude_unset=True),
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
        effective_availability_revision=change.effective_availability_revision or 0,
        requested_changes=changes,
        pending_proposal_id=None,
    )


@router.post(
    "/settings/change-requests",
    response_model=SuccessEnvelope[SettingsChangeResult],
    summary="Khởi tạo giờ rảnh/cài đặt khi chưa có kế hoạch (Dòng 41)",
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
