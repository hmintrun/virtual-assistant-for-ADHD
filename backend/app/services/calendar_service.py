"""Dịch vụ quản lý kết nối Google Calendar (O23 CalendarConnection), xác thực OAuth 2.0 và danh sách lịch."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_DNS

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarAuthorization, CalendarConnection
from app.models.user import User
from app.schemas.calendar import (
    CalendarConnectionRead,
    CalendarListResponse,
    CalendarSummaryItem,
)
from app.services.google_calendar_client import (
    CalendarAuthError,
    CalendarClientError,
    get_calendar_client,
)

logger = logging.getLogger(__name__)


def _hash_state(state: str) -> str:
    """Băm state token bằng SHA256 để lưu trữ an toàn trong DB."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _serialize_credentials(token_data: dict[str, Any]) -> str:
    """Đóng gói token credentials thành chuỗi JSON."""
    return json.dumps(
        {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in"),
            "token_type": token_data.get("token_type", "Bearer"),
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _deserialize_credentials(credential_ref: str) -> dict[str, Any]:
    """Giải mã chuỗi JSON credentials."""
    try:
        return json.loads(credential_ref)
    except Exception:
        return {}


def _resolve_uuid(value: str | UUID) -> UUID:
    """Chuyển đổi string sang UUID đối tượng nếu cần, hỗ trợ chuỗi dev identifier."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return uuid5(NAMESPACE_DNS, f"proplan.identifier:{value}")


async def _ensure_user_exists(db: AsyncSession, user_id: UUID) -> None:
    """Đảm bảo tài khoản người dùng tồn tại trong DB (hỗ trợ dev user seed)."""
    user = await db.get(User, user_id)
    if not user:
        dev_user = User(
            id=user_id,
            email=f"dev_{user_id.hex[:8]}@example.com",
            display_name="ProPlan User",
            is_active=True,
            current_streak_days=0,
            streak_frozen=False,
            streak_freeze_tokens=2,
        )
        db.add(dev_user)
        await db.flush()



class CalendarService:
    """Dịch vụ nghiệp vụ chính quản lý OAuth và Calendar Connections."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = get_calendar_client()

    async def create_authorization_url(
        self, user_id: str | UUID, redirect_uri: str | None = None
    ) -> str:
        """Sinh URL Google OAuth 2.0 và lưu state intent vào cơ sở dữ liệu."""
        uid = _resolve_uuid(user_id)
        await _ensure_user_exists(self.db, uid)
        state = secrets.token_urlsafe(32)
        state_hash = _hash_state(state)

        # Xóa các authorization cũ chưa hoàn thành của user (nếu có)
        await self.db.execute(
            delete(CalendarAuthorization).where(CalendarAuthorization.user_id == uid)
        )

        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(minutes=10)

        auth_intent = CalendarAuthorization(
            user_id=uid,
            state_hash=state_hash,
            redirect_uri=redirect_uri or "",
            expires_at=expires_at,
        )
        self.db.add(auth_intent)
        await self.db.commit()

        return self.client.build_authorization_url(state=state, redirect_uri=redirect_uri)

    async def handle_oauth_callback(
        self,
        user_id: str | UUID,
        code: str,
        state: str,
        redirect_uri: str | None = None,
    ) -> CalendarConnection:
        """Xác thực mã callback từ Google và tạo hoặc cập nhật CalendarConnection."""
        uid = _resolve_uuid(user_id)
        await _ensure_user_exists(self.db, uid)
        state_hash = _hash_state(state)
        now_utc = datetime.now(timezone.utc)
        stmt = select(CalendarAuthorization).where(
            CalendarAuthorization.user_id == uid,
            CalendarAuthorization.state_hash == state_hash,
            CalendarAuthorization.consumed_at.is_(None),
            CalendarAuthorization.expires_at > now_utc,
        )
        auth_intent = (await self.db.execute(stmt)).scalar_one_or_none()
        if not auth_intent:
            logger.warning("Invalid or expired OAuth state for user_id=%s", uid)
            raise CalendarAuthError("Trạng thái OAuth không hợp lệ hoặc phiên đăng nhập đã hết hạn.")

        # Đổi code lấy token
        target_redirect_uri = redirect_uri or auth_intent.redirect_uri or None
        token_data = await self.client.exchange_auth_code(code, redirect_uri=target_redirect_uri)

        account_ref = token_data.get("account_ref", "unknown@gmail.com")
        granted_scopes = (
            token_data.get("scope", "").split()
            if isinstance(token_data.get("scope"), str)
            else []
        )
        serialized_creds = _serialize_credentials(token_data)

        # Kiểm tra xem tài khoản này đã từng liên kết chưa
        conn_stmt = select(CalendarConnection).where(
            CalendarConnection.user_id == uid,
            CalendarConnection.provider == "google",
            CalendarConnection.account_ref == account_ref,
        )
        existing_conn = (await self.db.execute(conn_stmt)).scalar_one_or_none()

        now_utc = datetime.now(timezone.utc)
        if existing_conn:
            existing_conn.credential_ref = serialized_creds
            existing_conn.granted_scopes = granted_scopes
            existing_conn.connection_state = "connected"
            existing_conn.last_successful_sync_at = now_utc
            conn = existing_conn
        else:
            conn = CalendarConnection(
                user_id=uid,
                provider="google",
                account_ref=account_ref,
                read_calendar_ids=[],
                write_calendar_id=None,
                granted_scopes=granted_scopes,
                connection_state="connected",
                credential_ref=serialized_creds,
                last_successful_sync_at=now_utc,
            )
            self.db.add(conn)

        # Đánh dấu authorization đã tiêu thụ thành công
        auth_intent.consumed_at = now_utc
        await self.db.commit()
        await self.db.refresh(conn)
        return conn

    async def get_connections(self, user_id: str | UUID) -> list[CalendarConnectionRead]:
        """Lấy danh sách các kết nối Calendar của người dùng."""
        uid = _resolve_uuid(user_id)
        stmt = (
            select(CalendarConnection)
            .where(CalendarConnection.user_id == uid)
            .order_by(CalendarConnection.created_at.desc())
        )
        connections = (await self.db.scalars(stmt)).all()
        return [
            CalendarConnectionRead(
                connection_id=str(c.id),
                provider=c.provider,
                account_ref=c.account_ref,
                connection_state=c.connection_state,
                read_calendar_ids=c.read_calendar_ids or [],
                write_calendar_id=c.write_calendar_id,
                granted_scopes=c.granted_scopes or [],
                last_successful_sync_at=c.last_successful_sync_at,
            )
            for c in connections
        ]

    async def get_connection_by_id(
        self, user_id: str | UUID, connection_id: str | UUID
    ) -> CalendarConnection | None:
        """Tìm kiếm kết nối theo ID."""
        uid = _resolve_uuid(user_id)
        cid = _resolve_uuid(connection_id)
        stmt = select(CalendarConnection).where(
            CalendarConnection.user_id == uid,
            CalendarConnection.id == cid,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_valid_access_token(self, connection: CalendarConnection) -> str:
        """Lấy access_token hợp lệ từ credential_ref, tự động refresh nếu cần."""
        creds = _deserialize_credentials(connection.credential_ref)
        access_token = creds.get("access_token")
        refresh_token = creds.get("refresh_token")

        if not access_token and not refresh_token:
            connection.connection_state = "needs_reconnect"
            await self.db.commit()
            raise CalendarAuthError("Không tìm thấy thông tin đăng nhập hợp lệ. Vui lòng kết nối lại.")

        # Thử dùng access_token hiện tại, nếu client báo 401 thì gọi refresh
        return access_token or ""

    async def list_calendars(
        self, user_id: str | UUID, connection_id: str | UUID
    ) -> CalendarListResponse:
        """Lấy danh sách các Calendar của một kết nối tài khoản."""
        conn = await self.get_connection_by_id(user_id, connection_id)
        if not conn:
            raise CalendarClientError("Không tìm thấy kết nối lịch tương ứng.", status_code=404)

        creds = _deserialize_credentials(conn.credential_ref)
        access_token = creds.get("access_token", "")
        refresh_token = creds.get("refresh_token", "")

        try:
            raw_items = await self.client.list_calendar_list(access_token)
        except CalendarAuthError:
            # Thử tự động refresh token nếu có refresh_token
            if refresh_token:
                try:
                    refreshed = await self.client.refresh_access_token(refresh_token)
                    access_token = refreshed.get("access_token", "")
                    creds["access_token"] = access_token
                    conn.credential_ref = json.dumps(creds)
                    conn.connection_state = "connected"
                    await self.db.commit()
                    raw_items = await self.client.list_calendar_list(access_token)
                except Exception as e:
                    logger.warning("Token refresh failed: %s", e)
                    conn.connection_state = "needs_reconnect"
                    await self.db.commit()
                    raise CalendarAuthError("Phiên kết nối Google đã hết hạn. Vui lòng kết nối lại.")
            else:
                conn.connection_state = "needs_reconnect"
                await self.db.commit()
                raise CalendarAuthError("Cần cấp lại quyền truy cập Google Calendar.")

        read_ids = set(conn.read_calendar_ids or [])
        write_id = conn.write_calendar_id

        items = [
            CalendarSummaryItem(
                id=item.get("id", ""),
                summary=item.get("summary", "Không có tiêu đề"),
                description=item.get("description"),
                primary=item.get("primary", False),
                access_role=item.get("accessRole", "reader"),
                background_color=item.get("backgroundColor"),
                selected_for_read=item.get("id") in read_ids,
                selected_for_write=item.get("id") == write_id,
            )
            for item in raw_items
            if item.get("id")
        ]

        return CalendarListResponse(
            connection_id=str(conn.id),
            account_ref=conn.account_ref,
            items=items,
        )

    async def update_calendar_selection(
        self,
        user_id: str | UUID,
        connection_id: str | UUID,
        read_calendar_ids: list[str],
        write_calendar_id: str | None,
    ) -> CalendarConnectionRead:
        """Cập nhật các lịch được chọn để đọc lịch bận hoặc ghi phiên tập trung."""
        conn = await self.get_connection_by_id(user_id, connection_id)
        if not conn:
            raise CalendarClientError("Không tìm thấy kết nối lịch tương ứng.", status_code=404)

        conn.read_calendar_ids = read_calendar_ids
        conn.write_calendar_id = write_calendar_id
        await self.db.commit()
        await self.db.refresh(conn)

        return CalendarConnectionRead(
            connection_id=str(conn.id),
            provider=conn.provider,
            account_ref=conn.account_ref,
            connection_state=conn.connection_state,
            read_calendar_ids=conn.read_calendar_ids or [],
            write_calendar_id=conn.write_calendar_id,
            granted_scopes=conn.granted_scopes or [],
            last_successful_sync_at=conn.last_successful_sync_at,
        )
