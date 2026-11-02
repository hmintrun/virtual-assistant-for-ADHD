# API endpoints cho UF7 hỗ trợ UF1-UF2: Đọc cài đặt trợ lý (Dòng 39) và giờ rảnh đã xác nhận (Dòng 40)
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.errors import AppError
from app.schemas.common import (
    ErrorEnvelope,
    SuccessEnvelope,
    build_response_meta,
)
from app.schemas.settings import Availability, UserPreference
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
