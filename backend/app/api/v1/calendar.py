"""Router cho các API tích hợp Google Calendar chuẩn hóa theo docs/backend/schema.md (45–62).

Hỗ trợ:
- POST /authorizations & GET /oauth/callback (Consent Screen).
- GET /connections & GET /connections/{id} (Trạng thái kết nối).
- GET /connections/{id}/calendars (Liệt kê lịch cá nhân & phụ).
- POST /connections/{id}/configuration-previews & POST /configuration-previews/{id}/confirm (Provisioning lịch riêng ProPlan).
- POST /connections/{id}/disconnect (Ngắt kết nối).
- GET /connections/{id}/events & GET /connections/{id}/events/{event_id} (Đọc sự kiện Google).
- POST /connections/{id}/refresh (Làm mới & chạy worker).
- GET /sync-jobs/{id} & POST /sync-jobs/{id}/retry (Kiểm tra và thử lại outbox sync).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, DbSessionDep
from app.models.calendar import CalendarSyncJob
from app.schemas.calendar import (
    CalendarAuthorizationRequest,
    CalendarAuthorizationResponse,
    CalendarConfigurationConfirmRequest,
    CalendarConfigurationPreviewRequest,
    CalendarConfigurationPreviewResponse,
    CalendarConnectionRead,
    CalendarListResponse,
    CalendarRefreshRequest,
    CalendarSelectionUpdateRequest,
    CalendarSyncJobRead,
    CalendarSyncJobRetryRequest,
    OAuthAuthUrlResponse,
)
from app.schemas.common import SuccessEnvelope, build_response_meta
from app.services.calendar_service import CalendarService, _resolve_uuid
from app.services.calendar_sync_worker import process_calendar_sync_jobs
from app.services.google_calendar_client import CalendarAuthError, CalendarClientError

router = APIRouter(prefix="/calendar", tags=["calendar"])


# 45 | POST /api/v1/calendar/authorizations
@router.post(
    "/authorizations",
    response_model=SuccessEnvelope[CalendarAuthorizationResponse],
    summary="Khởi tạo luồng cấp quyền Google Calendar từ thiết bị",
)
async def create_authorization(
    payload: CalendarAuthorizationRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarAuthorizationResponse]:
    service = CalendarService(db)
    auth_intent, auth_url = await service.create_authorization(
        user_id=current_user.user_id,
        device_id=payload.device_id,
        return_target=payload.return_target,
    )
    data = CalendarAuthorizationResponse(
        authorization_id=str(auth_intent.id),
        auth_url=auth_url,
        expires_at=auth_intent.expires_at,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())


# Tương thích ngược: GET /api/v1/calendar/auth-url
@router.get(
    "/auth-url",
    response_model=SuccessEnvelope[OAuthAuthUrlResponse],
    summary="Lấy URL dẫn tới màn hình đồng ý cấp quyền Google Calendar (tương thích ngược)",
    include_in_schema=False,
)
async def get_authorization_url(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    redirect_uri: Annotated[str | None, Query(description="Redirect URI tùy chỉnh nếu có")] = None,
) -> SuccessEnvelope[OAuthAuthUrlResponse]:
    service = CalendarService(db)
    auth_url = await service.create_authorization_url(
        user_id=current_user.user_id, redirect_uri=redirect_uri
    )
    return SuccessEnvelope(data=OAuthAuthUrlResponse(auth_url=auth_url), meta=build_response_meta())


# 46 | GET /api/v1/calendar/oauth/callback
@router.get(
    "/oauth/callback",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Tiếp nhận Authorization Code từ Google và hoàn tất kết nối",
)
async def oauth_callback(
    code: Annotated[str, Query(min_length=1, description="Authorization code do Google cấp")],
    state: Annotated[str, Query(min_length=1, description="State token bảo vệ chống CSRF")],
    db: DbSessionDep,
    current_user: CurrentUserDep | None = None,
) -> SuccessEnvelope[CalendarConnectionRead]:
    service = CalendarService(db)
    user_id = current_user.user_id if current_user else None
    try:
        conn = await service.handle_oauth_callback(
            user_id=user_id,
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


# Tương thích ngược: GET /api/v1/calendar/oauth2callback
@router.get(
    "/oauth2callback",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    include_in_schema=False,
)
async def oauth2_callback_legacy(
    code: Annotated[str, Query(min_length=1)],
    state: Annotated[str, Query(min_length=1)],
    db: DbSessionDep,
    current_user: CurrentUserDep | None = None,
) -> SuccessEnvelope[CalendarConnectionRead]:
    return await oauth_callback(code=code, state=state, db=db, current_user=current_user)


# 47 | GET /api/v1/calendar/connections
@router.get(
    "/connections",
    response_model=SuccessEnvelope[list[CalendarConnectionRead]],
    summary="Lấy danh sách các tài khoản Google Calendar đã kết nối",
)
async def get_connections(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[list[CalendarConnectionRead]]:
    service = CalendarService(db)
    connections = await service.get_connections(current_user.user_id)
    return SuccessEnvelope(data=connections, meta=build_response_meta())


# 48 | GET /api/v1/calendar/connections/{connection_id}
@router.get(
    "/connections/{connection_id}",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Lấy thông tin chi tiết một kết nối lịch",
)
async def get_connection_detail(
    connection_id: UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConnectionRead]:
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy kết nối lịch tương ứng.")

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


# 49 | GET /api/v1/calendar/connections/{connection_id}/calendars
@router.get(
    "/connections/{connection_id}/calendars",
    response_model=SuccessEnvelope[CalendarListResponse],
    summary="Lấy danh sách các lịch Google của một kết nối",
)
async def get_calendars(
    connection_id: UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarListResponse]:
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


# 50 | POST /api/v1/calendar/connections/{connection_id}/configuration-previews
@router.post(
    "/connections/{connection_id}/configuration-previews",
    response_model=SuccessEnvelope[CalendarConfigurationPreviewResponse],
    summary="Tạo bản xem trước thay đổi cấu hình lịch đọc/ghi",
)
async def create_configuration_preview(
    connection_id: UUID,
    payload: CalendarConfigurationPreviewRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConfigurationPreviewResponse]:
    service = CalendarService(db)
    try:
        preview = await service.create_configuration_preview(
            user_id=current_user.user_id,
            connection_id=connection_id,
            read_calendar_ids=payload.read_calendar_ids,
            write_calendar_id=payload.write_calendar_id,
            existing_event_action=payload.existing_event_action,
        )
    except CalendarClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    data = CalendarConfigurationPreviewResponse(
        preview_id=str(preview.id),
        connection_id=str(preview.connection_id),
        read_calendar_ids=payload.read_calendar_ids,
        write_calendar_id=payload.write_calendar_id,
        existing_event_action=payload.existing_event_action,
        status=preview.status,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())


# 51 | POST /api/v1/calendar/configuration-previews/{preview_id}/confirm
@router.post(
    "/configuration-previews/{preview_id}/confirm",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Xác nhận áp dụng cấu hình lịch từ bản xem trước",
)
async def confirm_configuration_preview(
    preview_id: UUID,
    payload: CalendarConfigurationConfirmRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConnectionRead]:
    service = CalendarService(db)
    try:
        conn = await service.confirm_configuration_preview(
            user_id=current_user.user_id,
            preview_id=preview_id,
        )
    except CalendarClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

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


# Tương thích ngược: PATCH /api/v1/calendar/{connection_id}/selection
@router.patch(
    "/{connection_id}/selection",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Cập nhật nhanh danh sách lịch được chọn (tương thích ngược)",
    include_in_schema=False,
)
async def update_selection(
    connection_id: UUID,
    payload: CalendarSelectionUpdateRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConnectionRead]:
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


# 52 | POST /api/v1/calendar/connections/{connection_id}/disconnect
@router.post(
    "/connections/{connection_id}/disconnect",
    response_model=SuccessEnvelope[CalendarConnectionRead],
    summary="Ngắt kết nối tài khoản Calendar",
)
async def disconnect_connection(
    connection_id: UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarConnectionRead]:
    service = CalendarService(db)
    try:
        conn = await service.disconnect(user_id=current_user.user_id, connection_id=connection_id)
    except CalendarClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

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


# 53 | GET /api/v1/calendar/connections/{connection_id}/events
@router.get(
    "/connections/{connection_id}/events",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="Đọc danh sách sự kiện trong khoảng thời gian từ Google Calendar",
)
async def get_connection_events(
    connection_id: UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    google_calendar_id: Annotated[str, Query(description="ID của lịch Google cần đọc")],
    from_at: Annotated[datetime | None, Query(alias="from", description="Thời điểm bắt đầu")] = None,
    to_at: Annotated[datetime | None, Query(alias="to", description="Thời điểm kết thúc")] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 50,
) -> SuccessEnvelope[list[dict[str, Any]]]:
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy kết nối lịch.")
    access_token = await service.get_valid_access_token(conn)
    q_start = from_at or datetime.now(timezone.utc)
    q_end = to_at or (q_start + timedelta(days=14))
    events = await service.client.list_events(access_token, google_calendar_id, q_start, q_end)
    return SuccessEnvelope(data=events[:limit], meta=build_response_meta())


# 54 | GET /api/v1/calendar/connections/{connection_id}/events/{event_id}
@router.get(
    "/connections/{connection_id}/events/{event_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
    summary="Lấy chi tiết một sự kiện trên Google Calendar",
)
async def get_connection_event_detail(
    connection_id: UUID,
    event_id: str,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    google_calendar_id: Annotated[str, Query(description="ID của lịch Google chứa sự kiện")],
) -> SuccessEnvelope[dict[str, Any]]:
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy kết nối lịch.")
    access_token = await service.get_valid_access_token(conn)
    event = await service.client.get_event(access_token, google_calendar_id, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện trên Google Calendar.")
    return SuccessEnvelope(data=event, meta=build_response_meta())


# 57 | POST /api/v1/calendar/connections/{connection_id}/refresh
@router.post(
    "/connections/{connection_id}/refresh",
    response_model=SuccessEnvelope[dict[str, str]],
    summary="Kích hoạt làm mới và chạy worker xử lý sync jobs",
)
async def refresh_calendar(
    connection_id: UUID,
    payload: CalendarRefreshRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[dict[str, str]]:
    processed = await process_calendar_sync_jobs(db, limit=20)
    return SuccessEnvelope(
        data={"status": "refreshed", "jobs_processed": str(processed)},
        meta=build_response_meta(),
    )


# 58 | GET /api/v1/calendar/sync-jobs/{job_id}
@router.get(
    "/sync-jobs/{job_id}",
    response_model=SuccessEnvelope[CalendarSyncJobRead],
    summary="Kiểm tra trạng thái của một sync job",
)
async def get_sync_job(
    job_id: UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarSyncJobRead]:
    uid = _resolve_uuid(current_user.user_id)
    job = await db.get(CalendarSyncJob, job_id)
    if not job or job.user_id != uid:
        raise HTTPException(status_code=404, detail="Không tìm thấy sync job.")

    data = CalendarSyncJobRead(
        job_id=str(job.id),
        connection_id=str(job.connection_id),
        target_kind=job.target_kind,
        target_ref=job.target_ref,
        operation=job.operation,
        job_state=job.job_state,
        attempt_count=job.attempt_count,
        next_retry_at=job.next_retry_at,
        last_error_code=job.last_error_code,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())


# 59 | POST /api/v1/calendar/sync-jobs/{job_id}/retry
@router.post(
    "/sync-jobs/{job_id}/retry",
    response_model=SuccessEnvelope[CalendarSyncJobRead],
    summary="Thử lại một sync job bị lỗi hoặc conflict",
)
async def retry_sync_job(
    job_id: UUID,
    payload: CalendarSyncJobRetryRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarSyncJobRead]:
    uid = _resolve_uuid(current_user.user_id)
    job = await db.get(CalendarSyncJob, job_id)
    if not job or job.user_id != uid:
        raise HTTPException(status_code=404, detail="Không tìm thấy sync job.")

    job.job_state = "queued"
    job.attempt_count = 0
    job.next_retry_at = None
    job.last_error_code = None
    await db.commit()
    await db.refresh(job)

    # Thử xử lý ngay lập tức
    await process_calendar_sync_jobs(db, limit=5)
    await db.refresh(job)

    data = CalendarSyncJobRead(
        job_id=str(job.id),
        connection_id=str(job.connection_id),
        target_kind=job.target_kind,
        target_ref=job.target_ref,
        operation=job.operation,
        job_state=job.job_state,
        attempt_count=job.attempt_count,
        next_retry_at=job.next_retry_at,
        last_error_code=job.last_error_code,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())
