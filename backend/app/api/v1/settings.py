# API endpoints cho UF7 hỗ trợ UF1-UF2 và UF6-UF7: Cài đặt (Dòng 39-42) và Gợi ý thích ứng (Dòng 43-44).
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.errors import AppError
from app.schemas.common import (
    ErrorEnvelope,
    SuccessEnvelope,
    build_response_meta,
)
from app.schemas.settings import (
    AdaptationSuggestionData,
    Availability,
    CancelSettingsChangeRequest,
    CancelSettingsChangeResult,
    SettingsChangeRequest,
    SettingsChangeResult,
    SuggestionKind,
    SuggestionResponseRequest,
    SuggestionResponseResult,
    UserPreference,
)
from app.services.draft_service import get_user_availability
from app.services.plan_service import get_preferences as load_preferences
from app.services.scheduler_engine import compute_effective_free_intervals
from app.services.settings_service import (
    apply_settings_change,
    cancel_settings_change,
    get_current_suggestion,
    respond_to_suggestion,
)

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
    pref = await load_preferences(
        user_id=current_user.user_id, db=db, commit_if_created=True
    )
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


@router.post(
    "/settings/change-requests",
    response_model=SuccessEnvelope[SettingsChangeResult],
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập"},
        404: {"model": ErrorEnvelope, "description": "Không tìm thấy Draft hoặc Task"},
        409: {"model": ErrorEnvelope, "description": "Lệch revision hoặc idempotency key"},
        422: {"model": ErrorEnvelope, "description": "Giá trị cài đặt không hợp lệ"},
    },
    summary="Chỉnh cấu hình hoặc giờ đã khai báo của Draft/Task (Dòng 41)",
)
async def create_settings_change(
    request: Request,
    payload: SettingsChangeRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> SuccessEnvelope[SettingsChangeResult]:
    result = await apply_settings_change(
        user_id=current_user.user_id,
        idempotency_key=idempotency_key,
        payload=payload,
        db=db,
    )
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(data=result, meta=build_response_meta(request_id))


@router.post(
    "/settings/change-requests/{change_request_id}/cancel",
    response_model=SuccessEnvelope[CancelSettingsChangeResult],
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập"},
        404: {"model": ErrorEnvelope, "description": "Không tìm thấy yêu cầu cài đặt"},
        409: {
            "model": ErrorEnvelope,
            "description": "Lệch revision, không ở trạng thái pending hoặc trùng Idempotency-Key",
        },
        422: {"model": ErrorEnvelope, "description": "Thiếu/sai revision gửi lên"},
    },
    summary="Hủy yêu cầu cài đặt đang chờ duyệt (Dòng 42)",
)
async def cancel_settings_change_endpoint(
    request: Request,
    change_request_id: str,
    payload: CancelSettingsChangeRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> SuccessEnvelope[CancelSettingsChangeResult]:
    result = await cancel_settings_change(
        user_id=current_user.user_id,
        idempotency_key=idempotency_key,
        change_request_id=change_request_id,
        payload=payload,
        db=db,
    )
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(data=result, meta=build_response_meta(request_id))


@router.get(
    "/suggestions/current",
    response_model=SuccessEnvelope[AdaptationSuggestionData | None],
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập"},
        422: {"model": ErrorEnvelope, "description": "Tham số lọc kind không hợp lệ"},
    },
    summary="Xem gợi ý thích ứng hiện hành của trợ lý (Dòng 43)",
)
async def get_current_suggestion_endpoint(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    kind: Annotated[
        SuggestionKind | None,
        Query(description="Lọc loại gợi ý: reminder_offset hoặc buffer_multiplier"),
    ] = None,
) -> SuccessEnvelope[AdaptationSuggestionData | None]:
    result = await get_current_suggestion(
        db,
        user_id=current_user.user_id,
        kind=kind,
        commit_if_created=True,
    )
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(data=result, meta=build_response_meta(request_id))


@router.post(
    "/suggestions/{suggestion_id}/response",
    response_model=SuccessEnvelope[SuggestionResponseResult],
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập"},
        404: {"model": ErrorEnvelope, "description": "Không tìm thấy gợi ý"},
        409: {
            "model": ErrorEnvelope,
            "description": "Gợi ý đã xử lý (SUGGESTION_CHANGED), lệch revision hoặc trùng Idempotency-Key",
        },
        422: {"model": ErrorEnvelope, "description": "Giá trị chỉnh sửa hoặc phản hồi không hợp lệ"},
    },
    summary="Phản hồi gợi ý thích ứng (accept / edit / reject) (Dòng 44)",
)
async def respond_to_suggestion_endpoint(
    request: Request,
    suggestion_id: str,
    payload: SuggestionResponseRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> SuccessEnvelope[SuggestionResponseResult]:
    result = await respond_to_suggestion(
        user_id=current_user.user_id,
        idempotency_key=idempotency_key,
        suggestion_id=suggestion_id,
        payload=payload,
        db=db,
    )
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(data=result, meta=build_response_meta(request_id))
