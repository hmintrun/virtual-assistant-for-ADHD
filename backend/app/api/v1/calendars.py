"""Router cho các API tích hợp Google Calendar (A22.1 — O23 CalendarConnection).

Hỗ trợ lấy URL đăng nhập OAuth 2.0, xử lý callback, xem danh sách kết nối,
lấy danh sách lịch từ Google Calendar và lưu lựa chọn lịch đọc/ghi.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, DbSessionDep
from app.schemas.calendar import (
    CalendarConnectionRead,
    CalendarListResponse,
    CalendarSelectionUpdateRequest,
    OAuthAuthUrlResponse,
)
from app.schemas.common import SuccessEnvelope, build_response_meta
from app.services.calendar_service import CalendarService
from app.services.google_calendar_client import CalendarAuthError, CalendarClientError

router = APIRouter(prefix="/calendars", tags=["calendars"])


@router.get(
    "/auth-url",
    response_model=SuccessEnvelope[OAuthAuthUrlResponse],
    summary="Lấy URL dẫn tới màn hình đồng ý cấp quyền Google Calendar (OAuth 2.0)",
)
async def get_authorization_url(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    redirect_uri: Annotated[str | None, Query(description="Redirect URI tùy chỉnh nếu có")] = None,
) -> SuccessEnvelope[OAuthAuthUrlResponse]:
    """Tạo state token và trả về Google OAuth 2.0 URL cho ứng dụng Mobile/Web."""
    service = CalendarService(db)
    auth_url = await service.create_authorization_url(
        user_id=current_user.user_id, redirect_uri=redirect_uri
    )
    return SuccessEnvelope(
        data=OAuthAuthUrlResponse(auth_url=auth_url),
        meta=build_response_meta(),
    )


@router.get(
    "/oauth2callback",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Tiếp nhận Authorization Code từ Google và hoàn tất kết nối",
)
async def oauth2_callback(
    code: Annotated[str, Query(min_length=1, description="Authorization code do Google cấp")],
    state: Annotated[str, Query(min_length=1, description="State token bảo vệ chống CSRF")],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConnectionRead]:
    """Đổi mã code lấy token, lưu kết nối CalendarConnection vào DB và cập nhật trạng thái."""
    service = CalendarService(db)
    try:
        conn = await service.handle_oauth_callback(
            user_id=current_user.user_id,
            code=code,
            state=state,
        )
    except CalendarAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)
    except CalendarClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message)

    data = CalendarConnectionRead(
        connection_id=str(conn.id),
        provider=conn.provider,
        account_ref=conn.account_ref,
        connection_state=conn.connection_state,
        read_calendar_ids=conn.read_calendar_ids or [],
        write_calendar_id=conn.write_calendar_id,
        granted_scopes=conn.granted_scopes or [],
        last_successful_sync_at=conn.last_successful_sync_at,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())


@router.get(
    "/connections",
    response_model=SuccessEnvelope[list[CalendarConnectionRead]],
    summary="Lấy danh sách các tài khoản Google Calendar đã kết nối",
)
async def get_connections(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[list[CalendarConnectionRead]]:
    """Trả về toàn bộ các kết nối lịch của người dùng hiện tại."""
    service = CalendarService(db)
    connections = await service.get_connections(current_user.user_id)
    return SuccessEnvelope(data=connections, meta=build_response_meta())


@router.get(
    "/{connection_id}/calendars",
    response_model=SuccessEnvelope[CalendarListResponse],
    summary="Lấy danh sách các lịch Google của một kết nối",
)
async def get_calendars(
    connection_id: UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarListResponse]:
    """Gọi Google API lấy danh sách các lịch cá nhân/công việc trong tài khoản được chỉ định."""
    service = CalendarService(db)
    try:
        calendar_list = await service.list_calendars(
            user_id=current_user.user_id, connection_id=connection_id
        )
    except CalendarAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)
    except CalendarClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return SuccessEnvelope(data=calendar_list, meta=build_response_meta())


@router.patch(
    "/{connection_id}/selection",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Cập nhật danh sách lịch được chọn để đọc giờ bận và ghi phiên tập trung",
)
async def update_selection(
    connection_id: UUID,
    payload: CalendarSelectionUpdateRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConnectionRead]:
    """Lưu cấu hình các lịch dùng để tính giờ rảnh (read) và lịch xuất session (write)."""
    service = CalendarService(db)
    try:
        updated = await service.update_calendar_selection(
            user_id=current_user.user_id,
            connection_id=connection_id,
            read_calendar_ids=payload.read_calendar_ids,
            write_calendar_id=payload.write_calendar_id,
        )
    except CalendarClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return SuccessEnvelope(data=updated, meta=build_response_meta())
