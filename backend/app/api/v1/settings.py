# API endpoints cho UF7 hỗ trợ UF1-UF2: Đọc cài đặt trợ lý (Dòng 39) và giờ rảnh đã xác nhận (Dòng 40)
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUserDep
from app.core.errors import AppError
from app.schemas.common import (
    CalendarContextRef,
    ErrorEnvelope,
    SuccessEnvelope,
    TimeInterval,
    build_response_meta,
)
from app.schemas.settings import Availability, UserPreference, WeeklyRule

router = APIRouter(tags=["UF7 - Settings & Availability"])

_SEED_EFFECTIVE_INTERVALS: list[TimeInterval] = [
    TimeInterval(
        start_at=datetime.fromisoformat("2026-10-30T09:00:00+07:00"),
        end_at=datetime.fromisoformat("2026-10-30T11:00:00+07:00"),
    ),
    TimeInterval(
        start_at=datetime.fromisoformat("2026-11-06T09:00:00+07:00"),
        end_at=datetime.fromisoformat("2026-11-06T11:00:00+07:00"),
    ),
]

_FAKE_PREFERENCES_BY_USER: dict[str, UserPreference] = {
    "user_a": UserPreference(
        revision=4,
        effective_revision=4,
        requested_revision=None,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
        micro_task_seconds=None,
        reminder_offsets=[900],
        buffer_multiplier=1.2,
        reminder_style="gentle",
        requested_changes=None,
        pending_proposal_id=None,
    ),
    "user_b": UserPreference(
        revision=1,
        effective_revision=1,
        requested_revision=None,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
        micro_task_seconds=None,
        reminder_offsets=[900],
        buffer_multiplier=1.2,
        reminder_style="gentle",
        requested_changes=None,
        pending_proposal_id=None,
    ),
}

_FAKE_AVAILABILITY_BY_USER: dict[str, Availability] = {
    "user_a": Availability(
        availability_id="availability_1",
        revision=2,
        timezone="Asia/Ho_Chi_Minh",
        weekly_rules=[
            WeeklyRule(weekday=5, start_local="09:00", end_local="11:00"),
        ],
        date_overrides=[],
        task_limits=[],
        source="user_confirmed",
        confirmed_at=datetime.fromisoformat("2026-10-29T15:00:00+07:00"),
        effective_intervals=None,
        calendar_context=CalendarContextRef(context_id="context_1", revision=2),
    ),
    "user_b": Availability(
        availability_id="availability_b_1",
        revision=1,
        timezone="Asia/Ho_Chi_Minh",
        weekly_rules=[
            WeeklyRule(weekday=6, start_local="14:00", end_local="16:00"),
        ],
        date_overrides=[],
        task_limits=[],
        source="user_confirmed",
        confirmed_at=datetime.fromisoformat("2026-10-29T15:00:00+07:00"),
        effective_intervals=None,
        calendar_context=CalendarContextRef(context_id="context_b_1", revision=1),
    ),
}


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
) -> SuccessEnvelope[UserPreference]:
    """Trả về cấu hình trợ lý đang có hiệu lực và yêu cầu đang chờ duyệt của người dùng."""
    pref = _FAKE_PREFERENCES_BY_USER.get(current_user.user_id)
    if pref is None:
        pref = _FAKE_PREFERENCES_BY_USER["user_a"]
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

    base_avail = _FAKE_AVAILABILITY_BY_USER.get(
        current_user.user_id, _FAKE_AVAILABILITY_BY_USER["user_a"]
    )
    if from_at is None or to_at is None:
        result_avail = base_avail.model_copy(update={"effective_intervals": None})
    else:
        matched_intervals = [
            interval
            for interval in _SEED_EFFECTIVE_INTERVALS
            if interval.start_at < to_at and interval.end_at > from_at
        ]
        result_avail = base_avail.model_copy(
            update={"effective_intervals": matched_intervals}
        )

    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=result_avail,
        meta=build_response_meta(request_id),
    )
