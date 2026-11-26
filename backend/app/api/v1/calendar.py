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

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunparse
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.config import settings
from app.core.errors import AppError
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
)
from app.schemas.calendar import (
    CalendarAuthorizationRequest,
    CalendarAuthorizationResponse,
    CalendarConfigurationConfirmRequest,
    CalendarConfigurationPreviewRequest,
    CalendarConfigurationPreviewResponse,
    CalendarConnectionRead,
    CalendarEvent,
    CalendarListResponse,
    CalendarRefreshRequest,
    CalendarSelectionUpdateRequest,
    CalendarSyncJobRead,
    CalendarSyncJobRetryRequest,
    EventChangeConfirmRequest,
    EventChangePreviewRequest,
    EventChangePreviewResponse,
    OAuthAuthUrlResponse,
)
from app.schemas.common import FieldError, SuccessEnvelope, build_response_meta
from app.services import command_service
from app.services.calendar_service import (
    CalendarService,
    _ensure_utc,
    _resolve_uuid,
    get_owner_calendar_lock,
)
from app.services.calendar_sync_worker import (
    get_connection_sync_lock,
    process_calendar_sync_jobs,
)
from app.services.google_calendar_client import CalendarAuthError, CalendarClientError
from app.services.planning.calendar_ingestion import normalize_google_event

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _calendar_auth_error(exc: CalendarAuthError) -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="CALENDAR_AUTH_REQUIRED",
        message=exc.message,
        next_action="reconnect_google_calendar",
    )


def _calendar_client_error(exc: CalendarClientError) -> AppError:
    code = "CALENDAR_CLIENT_ERROR"
    next_action = "retry_later"
    if exc.status_code == 404:
        code = "RESOURCE_NOT_FOUND"
        next_action = "check_calendar_connection"
    elif exc.status_code == 409:
        if exc.error_code in {
            "REVISION_CONFLICT",
            "PREVIEW_EXPIRED",
            "PREVIEW_ALREADY_PROCESSED",
            "CALENDAR_CONFLICT",
        }:
            code = exc.error_code
        else:
            code = "CALENDAR_CONFLICT"
        next_action = (
            "create_preview"
            if code in {"PREVIEW_EXPIRED", "PREVIEW_ALREADY_PROCESSED"}
            else "reload_and_review"
        )
    elif exc.status_code == 400:
        code = "VALIDATION_ERROR"
        next_action = "fix_request"
    elif exc.status_code == 422:
        code = "VALIDATION_ERROR"
        next_action = "fix_fields"
    elif exc.status_code == 403:
        code = "CALENDAR_PERMISSION_DENIED"
        next_action = "reconnect_google_calendar"
    return AppError(
        status_code=exc.status_code,
        code=code,
        message=exc.message,
        next_action=next_action,
        retryable=exc.status_code >= 500,
    )


def _not_found(message: str) -> AppError:
    return AppError(
        status_code=404,
        code="RESOURCE_NOT_FOUND",
        message=message,
        next_action="check_resource",
    )


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


MOBILE_RETURN_TARGET = "proplan://calendar/connected"


def _allowed_return_target(target: str | None) -> str | None:
    """Chỉ nhận deep link của app hoặc route Calendar trên origin Web đã cấu hình."""
    if not target or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in target):
        return None
    try:
        parsed = urlsplit(target)
    except ValueError:
        return None
    base = parsed._replace(query="", fragment="").geturl()
    if base == MOBILE_RETURN_TARGET:
        return target
    web_target = parsed.geturl()
    if any(web_target == f"{origin}/#/google-calendar" for origin in settings.browser_origins):
        return target
    return None


def _append_query_params(url: str, params: dict[str, str | None]) -> str:
    """Nối các tham số query vào deep-link/redirect URL mà vẫn giữ nguyên query cũ."""
    parsed = urlparse(url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, val in params.items():
        if val is not None:
            query_items[key] = val
    return urlunparse(parsed._replace(query=urlencode(query_items)))


# 46 | GET /api/v1/calendar/oauth/callback
@router.get(
    "/oauth/callback",
    summary="Tiếp nhận Authorization Code từ Google và redirect về mobile app",
    response_class=RedirectResponse,
)
async def oauth_callback(
    state: Annotated[str, Query(min_length=1, description="State token bảo vệ chống CSRF")],
    db: DbSessionDep,
    code: Annotated[str | None, Query(description="Authorization code do Google cấp")] = None,
    error: Annotated[str | None, Query(description="Mã lỗi OAuth từ Google nếu có")] = None,
    error_description: Annotated[str | None, Query(description="Mô tả chi tiết lỗi OAuth từ Google")] = None,
) -> RedirectResponse:
    service = CalendarService(db)
    if error or not code:
        try:
            return_target = await service.handle_oauth_error(state=state)
        except CalendarAuthError as exc:
            raise _calendar_auth_error(exc) from exc
        base_target = _allowed_return_target(return_target) or MOBILE_RETURN_TARGET
        target_url = _append_query_params(
            base_target,
            {"error": error or "missing_code", "error_description": error_description},
        )
        return RedirectResponse(url=target_url, status_code=302)

    try:
        conn, return_target = await service.handle_oauth_callback(
            user_id=None,
            code=code,
            state=state,
        )
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc

    # Các yêu cầu OAuth đã lưu trước khi triển khai vẫn phải qua allowlist.
    target_url = _allowed_return_target(return_target) or _append_query_params(
        MOBILE_RETURN_TARGET, {"connection_id": str(conn.id)}
    )
    return RedirectResponse(url=target_url, status_code=302)


# Tương thích ngược: GET /api/v1/calendar/oauth2callback
@router.get(
    "/oauth2callback",
    include_in_schema=False,
    response_class=RedirectResponse,
)
async def oauth2_callback_legacy(
    state: Annotated[str, Query(min_length=1)],
    db: DbSessionDep,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    return await oauth_callback(
        state=state,
        db=db,
        code=code,
        error=error,
        error_description=error_description,
    )


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
        raise _not_found("Không tìm thấy kết nối lịch tương ứng.")

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
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc

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
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc

    data = CalendarConfigurationPreviewResponse(
        preview_id=str(preview.id),
        connection_id=str(preview.connection_id),
        read_calendar_ids=list((preview.payload or {}).get("read_calendar_ids") or []),
        write_calendar_id=payload.write_calendar_id,
        existing_event_action=payload.existing_event_action,
        status=preview.status,
        preview_revision=preview.revision,
        base_connection_revision=preview.base_connection_revision,
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
            expected_preview_revision=payload.expected_preview_revision,
        )
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc

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
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc

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
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc

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
    response_model=SuccessEnvelope[list[CalendarEvent]],
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
) -> SuccessEnvelope[list[CalendarEvent]]:
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, connection_id)
    if not conn:
        raise _not_found("Không tìm thấy kết nối lịch.")
    conn_id = conn.id
    try:
        access_token = await service.get_valid_access_token(conn)
        q_start = from_at or datetime.now(UTC)
        q_end = to_at or (q_start + timedelta(days=14))
        try:
            raw_events = await service.client.list_events(
                access_token, google_calendar_id, q_start, q_end
            )
        except CalendarAuthError:
            await service.mark_connection_needs_reconnect(conn_id)
            raise
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc
    events: list[CalendarEvent] = []
    for raw in raw_events[:limit]:
        norm = normalize_google_event(raw, str(connection_id), google_calendar_id)
        if norm:
            events.append(norm)
    return SuccessEnvelope(data=events, meta=build_response_meta())


# 54 | GET /api/v1/calendar/connections/{connection_id}/events/{event_id}
@router.get(
    "/connections/{connection_id}/events/{event_id}",
    response_model=SuccessEnvelope[CalendarEvent],
    summary="Lấy chi tiết một sự kiện trên Google Calendar",
)
async def get_connection_event_detail(
    connection_id: UUID,
    event_id: str,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    google_calendar_id: Annotated[str, Query(description="ID của lịch Google chứa sự kiện")],
) -> SuccessEnvelope[CalendarEvent]:
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, connection_id)
    if not conn:
        raise _not_found("Không tìm thấy kết nối lịch.")
    conn_id = conn.id
    try:
        access_token = await service.get_valid_access_token(conn)
        try:
            raw = await service.client.get_event(access_token, google_calendar_id, event_id)
        except CalendarAuthError:
            await service.mark_connection_needs_reconnect(conn_id)
            raise
    except CalendarAuthError as exc:
        raise _calendar_auth_error(exc) from exc
    except CalendarClientError as exc:
        raise _calendar_client_error(exc) from exc
    if not raw:
        raise _not_found("Không tìm thấy sự kiện trên Google Calendar.")
    norm = normalize_google_event(raw, str(connection_id), google_calendar_id)
    if not norm:
        raise _not_found("Sự kiện không hợp lệ hoặc đã bị hủy.")
    return SuccessEnvelope(data=norm, meta=build_response_meta())


# 55 | POST /api/v1/calendar/event-change-previews
@router.post(
    "/event-change-previews",
    response_model=SuccessEnvelope[EventChangePreviewResponse],
    summary="Tạo bản xem trước thay đổi sự kiện trên Google Calendar",
)
async def create_event_change_preview(
    payload: EventChangePreviewRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[EventChangePreviewResponse]:
    conn_id = _resolve_uuid(payload.connection_id)
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, conn_id)
    if not conn:
        raise _not_found("Không tìm thấy kết nối lịch tương ứng.")

    # BUG-16: Kiểm tra bắt buộc có google_event_id khi update hoặc delete
    if payload.operation in ("update", "delete") and not payload.google_event_id:
        raise AppError(
            status_code=400,
            code="VALIDATION_ERROR",
            message="Thao tác cập nhật hoặc xóa sự kiện yêu cầu cung cấp google_event_id.",
            next_action="fix_request",
        )

    preview_payload = {
        "operation": payload.operation,
        "connection_id": str(conn_id),
        "google_calendar_id": payload.google_calendar_id,
        "payload": payload.payload or {},
        "recurrence_scope": payload.recurrence_scope,
        "google_event_id": payload.google_event_id,
        "expected_google_etag": payload.expected_google_etag,
    }
    preview = CalendarPreview(
        user_id=_resolve_uuid(current_user.user_id),
        connection_id=conn_id,
        kind="event_change",
        base_connection_revision=conn.revision,
        payload=preview_payload,
        status="pending",
        source_revisions={
            "connection_revision": conn.revision,
            "preview_revision": 1,
        },
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db.add(preview)
    await db.commit()
    await db.refresh(preview)

    data = EventChangePreviewResponse(
        preview_id=str(preview.id),
        connection_id=str(conn_id),
        kind="event_change",
        operation=payload.operation,
        status="pending",
        payload=preview_payload,
        preview_revision=preview.revision,
        base_connection_revision=preview.base_connection_revision,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())


# 56 | POST /api/v1/calendar/event-change-previews/{preview_id}/confirm
@router.post(
    "/event-change-previews/{preview_id}/confirm",
    response_model=SuccessEnvelope[CalendarSyncJobRead],
    summary="Xác nhận và đưa thay đổi sự kiện vào hàng đợi Outbox sync",
)
async def confirm_event_change_preview(
    preview_id: UUID,
    payload: EventChangeConfirmRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[CalendarSyncJobRead]:
    uid = _resolve_uuid(current_user.user_id)
    async with get_owner_calendar_lock(str(uid)):
        await command_service.lock_owner(db, str(uid))
        preview = (
            await db.execute(
                select(CalendarPreview)
                .where(CalendarPreview.id == preview_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not preview or preview.user_id != uid:
            raise _not_found("Bản xem trước thay đổi không tồn tại.")

        # Bước 2 (UF6-BUG-04 / RG05): bắt buộc expected_preview_revision không được null
        if payload.expected_preview_revision is None:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Thiếu expected_preview_revision khi xác nhận bản xem trước.",
                field_errors=[
                    FieldError(
                        field_path="expected_preview_revision",
                        message="expected_preview_revision là bắt buộc và không được để trống.",
                    )
                ],
                next_action="fix_fields",
            )

        if preview.status == "pending" and payload.expected_preview_revision != preview.revision:
            raise AppError(
                status_code=409,
                code="REVISION_CONFLICT",
                message="Phiên bản bản xem trước không khớp (đã bị cập nhật).",
                current_revisions={"preview_revision": preview.revision},
                next_action="reload_and_review",
            )

        # Bước 3: kiểm tra hết hạn TRƯỚC kiểm tra stale connection để giữ đúng 409 PREVIEW_EXPIRED
        now_utc = datetime.now(UTC)
        if preview.status == "pending" and preview.expires_at and preview.expires_at < now_utc:
            preview.status = "expired"
            await db.commit()
            raise AppError(
                status_code=409,
                code="PREVIEW_EXPIRED",
                message="Bản xem trước thay đổi đã hết hạn (15 phút). Vui lòng tạo bản xem trước mới.",
                next_action="create_preview",
            )

        idempotency_key = f"manual_event_change_{preview.id}"

        # Bước 4 (Idempotent replay khi đã confirmed): chấp nhận preview.revision hoặc preview.revision - 1
        if preview.status == "confirmed":
            valid_replay_revisions = {preview.revision, max(1, preview.revision - 1)}
            if payload.expected_preview_revision not in valid_replay_revisions:
                raise AppError(
                    status_code=409,
                    code="REVISION_CONFLICT",
                    message="Phiên bản bản xem trước không khớp (đã bị cập nhật).",
                    current_revisions={"preview_revision": preview.revision},
                    next_action="reload_and_review",
                )
            existing_job = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == uid,
                        CalendarSyncJob.idempotency_key == idempotency_key,
                    )
                )
            ).first()
            if existing_job:
                data = CalendarSyncJobRead(
                    job_id=str(existing_job.id),
                    connection_id=str(existing_job.connection_id),
                    target_kind=existing_job.target_kind,
                    target_ref=existing_job.target_ref,
                    operation=existing_job.operation,
                    job_state=existing_job.job_state,
                    attempt_count=existing_job.attempt_count,
                    next_retry_at=existing_job.next_retry_at,
                    last_error_code=existing_job.last_error_code,
                )
                return SuccessEnvelope(data=data, meta=build_response_meta())

        if preview.status != "pending":
            raise AppError(
                status_code=409,
                code="PREVIEW_ALREADY_PROCESSED",
                message="Bản xem trước đã được xử lý hoặc hết hạn.",
                next_action="create_preview",
            )

        # Bước 5: kiểm tra stale connection (base_connection_revision == conn.revision)
        preview_data = preview.payload or {}
        conn_id = _resolve_uuid(preview_data.get("connection_id", preview.connection_id))
        conn = (
            await db.execute(
                select(CalendarConnection)
                .where(CalendarConnection.id == conn_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not conn or conn.user_id != uid:
            raise _not_found("Không tìm thấy kết nối lịch tương ứng.")
        if conn.connection_state == "disconnected":
            raise AppError(
                status_code=409,
                code="CALENDAR_DISCONNECTED",
                message="Kết nối Google Calendar đã bị ngắt. Vui lòng kết nối lại trước khi đồng bộ.",
                next_action="reconnect_google_calendar",
            )
        if (
            preview.base_connection_revision is None
            or conn.revision != preview.base_connection_revision
        ):
            raise AppError(
                status_code=409,
                code="REVISION_CONFLICT",
                message="Kết nối lịch đã thay đổi kể từ khi tạo bản xem trước. Vui lòng tạo lại bản xem trước.",
                current_revisions={
                    "connection_revision": conn.revision,
                    "preview_revision": preview.revision,
                },
                next_action="reload_and_review",
            )

        # Phòng ngừa double-click trước commit
        existing_job = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == uid,
                    CalendarSyncJob.idempotency_key == idempotency_key,
                )
            )
        ).first()
        if existing_job:
            data = CalendarSyncJobRead(
                job_id=str(existing_job.id),
                connection_id=str(existing_job.connection_id),
                target_kind=existing_job.target_kind,
                target_ref=existing_job.target_ref,
                operation=existing_job.operation,
                job_state=existing_job.job_state,
                attempt_count=existing_job.attempt_count,
                next_retry_at=existing_job.next_retry_at,
                last_error_code=existing_job.last_error_code,
            )
            return SuccessEnvelope(data=data, meta=build_response_meta())

        op = preview_data.get("operation", "create")
        matched_link_id: UUID | None = None
        google_event_id = preview_data.get("google_event_id")
        google_calendar_id = preview_data.get("google_calendar_id")
        if (
            isinstance(google_event_id, str)
            and google_event_id
            and isinstance(google_calendar_id, str)
            and google_calendar_id
        ):
            matched_link_id = await db.scalar(
                select(CalendarEventLink.id).where(
                    CalendarEventLink.user_id == uid,
                    CalendarEventLink.connection_id == conn_id,
                    CalendarEventLink.google_calendar_id == google_calendar_id,
                    CalendarEventLink.google_event_id == google_event_id,
                    CalendarEventLink.is_current.is_(True),
                )
            )

        job_payload_snapshot = dict(preview_data.get("payload") or {})
        if isinstance(google_calendar_id, str) and google_calendar_id.strip():
            job_payload_snapshot["_google_calendar_id"] = google_calendar_id.strip()

        job = CalendarSyncJob(
            user_id=uid,
            connection_id=conn_id,
            event_link_id=matched_link_id,
            target_kind="event",
            target_ref=google_event_id or str(preview.id),
            operation=op,
            idempotency_key=idempotency_key,
            payload_snapshot=job_payload_snapshot,
            expected_google_etag=preview_data.get("expected_google_etag"),
            job_state="queued",
            attempt_count=0,
        )
        preview.status = "confirmed"
        try:
            db.add(job)
            await db.commit()
            await db.refresh(job)
        except IntegrityError:
            await db.rollback()
            existing_job = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == uid,
                        CalendarSyncJob.idempotency_key == idempotency_key,
                    )
                )
            ).first()
            if existing_job:
                job = existing_job
            else:
                raise

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
    service = CalendarService(db)
    conn = await service.get_connection_by_id(current_user.user_id, connection_id)
    if not conn:
        raise _not_found("Không tìm thấy kết nối lịch.")

    async with get_connection_sync_lock(connection_id):
        db.expire_all()
        processed = await process_calendar_sync_jobs(
            db,
            user_id=current_user.user_id,
            connection_id=connection_id,
            limit=20,
            connection_lock_held=True,
        )
        try:
            reconciled = await service.reconcile_external_link_changes(
                current_user.user_id, connection_id
            )
        except CalendarAuthError as exc:
            raise _calendar_auth_error(exc) from exc
        except CalendarClientError as exc:
            raise _calendar_client_error(exc) from exc

    return SuccessEnvelope(
        data={
            "status": "refreshed",
            "jobs_processed": str(processed),
            "links_checked": str(reconciled["links_checked"]),
            "links_deleted_externally": str(reconciled["links_deleted_externally"]),
            "links_diverged": str(reconciled["links_diverged"]),
        },
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
        raise _not_found("Không tìm thấy sync job.")

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
    job_probe = await db.get(CalendarSyncJob, job_id)
    if not job_probe or job_probe.user_id != uid:
        raise _not_found("Không tìm thấy sync job.")
    job_connection_id = job_probe.connection_id

    if db.in_transaction():
        await db.rollback()

    # Kiểm tra CAS, lease và chạy worker hoàn toàn bên trong get_connection_sync_lock
    async with get_connection_sync_lock(job_connection_id):
        if db.in_transaction():
            await db.rollback()
        job = (
            await db.execute(
                select(CalendarSyncJob)
                .where(CalendarSyncJob.id == job_id, CalendarSyncJob.user_id == uid)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not job:
            raise _not_found("Không tìm thấy sync job.")

        if (
            payload.expected_job_revision is not None
            and job.revision != payload.expected_job_revision
        ):
            raise AppError(
                status_code=409,
                code="REVISION_CONFLICT",
                message="Phiên bản tác vụ đồng bộ đã thay đổi.",
                current_revisions={"job_revision": job.revision},
                next_action="reload_and_review",
            )

        if job.job_state == "cancelled":
            raise AppError(
                status_code=409,
                code="JOB_CANCELLED",
                message="Tác vụ đồng bộ đã bị hủy do ngắt kết nối lịch và không thể thử lại.",
                next_action="create_preview",
            )
        if job.job_state == "completed":
            raise AppError(
                status_code=409,
                code="INVALID_STATE_TRANSITION",
                message="Tác vụ đồng bộ đã hoàn tất, không thể thử lại.",
                next_action="reload",
            )

        now_utc = datetime.now(UTC)
        if job.job_state == "running" or (
            job.lease_expires_at is not None and _ensure_utc(job.lease_expires_at) > now_utc
        ):
            raise AppError(
                status_code=409,
                code="INVALID_STATE_TRANSITION",
                message="Tác vụ đồng bộ đang được xử lý.",
                retryable=True,
                next_action="reload",
            )

        conn = (
            await db.execute(
                select(CalendarConnection)
                .where(
                    CalendarConnection.id == job.connection_id,
                    CalendarConnection.user_id == uid,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if conn is None or conn.connection_state == "disconnected":
            raise AppError(
                status_code=409,
                code="CALENDAR_DISCONNECTED",
                message="Kết nối Google Calendar đã bị ngắt. Vui lòng kết nối lại trước khi thử lại đồng bộ.",
                next_action="reconnect_google_calendar",
            )

        job.job_state = "queued"
        job.attempt_count = 0
        job.next_retry_at = None
        job.last_error_code = None
        job.lease_expires_at = None
        job.updated_at = now_utc
        await db.commit()

        await process_calendar_sync_jobs(
            db,
            user_id=current_user.user_id,
            connection_id=job_connection_id,
            limit=5,
            connection_lock_held=True,
        )
        fresh_job = (
            await db.execute(
                select(CalendarSyncJob)
                .where(CalendarSyncJob.id == job_id, CalendarSyncJob.user_id == uid)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    data = CalendarSyncJobRead(
        job_id=str(fresh_job.id),
        connection_id=str(fresh_job.connection_id),
        target_kind=fresh_job.target_kind,
        target_ref=fresh_job.target_ref,
        operation=fresh_job.operation,
        job_state=fresh_job.job_state,
        attempt_count=fresh_job.attempt_count,
        next_retry_at=fresh_job.next_retry_at,
        last_error_code=fresh_job.last_error_code,
    )
    return SuccessEnvelope(data=data, meta=build_response_meta())
