# Router cho API đọc lịch chính thức đã xác nhận UF2 (Dòng 15 trên API Sheet: GET /api/v1/schedule)
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep
from app.schemas.common import SuccessEnvelope, build_response_meta
from app.schemas.schedule import ScheduleData
from app.services import plan_service

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("", response_model=SuccessEnvelope[ScheduleData])
async def get_schedule(
    from_at: Annotated[
        datetime,
        Query(
            alias="from",
            description="Mốc bắt đầu khoảng truy vấn có múi giờ (ví dụ: 2026-10-30T07:00:00+07:00)",
        ),
    ],
    to_at: Annotated[
        datetime,
        Query(
            alias="to",
            description="Mốc kết thúc khoảng truy vấn có múi giờ (ví dụ: 2026-11-13T07:00:00+07:00)",
        ),
    ],
    current_user: CurrentUserDep,
    limit: Annotated[
        int,
        Query(gt=0, description="Số phiên tối đa trả về trong một trang (ví dụ: 50)"),
    ] = 50,
    cursor: Annotated[
        str | None,
        Query(min_length=1, description="Con trỏ phân trang từ next_cursor của trang trước"),
    ] = None,
) -> SuccessEnvelope[ScheduleData]:
    """Dòng 15 trên API Sheet: Đọc các phiên tập trung chính thức trong khoảng [from, to) (không gộp Candidate chưa duyệt)."""
    data = await plan_service.get_schedule(
        user_id=current_user.user_id,
        from_at=from_at,
        to_at=to_at,
        limit=limit,
        cursor=cursor,
    )
    return SuccessEnvelope(
        data=data,
        meta=build_response_meta(),
    )
