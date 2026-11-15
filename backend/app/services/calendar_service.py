"""Dịch vụ quản lý kết nối Google Calendar (O23 CalendarConnection), xác thực OAuth 2.0 và danh sách lịch."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4, uuid5, NAMESPACE_DNS

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarPreview,
)
from app.models.user import User
from app.schemas.calendar import (
    CalendarConnectionRead,
    CalendarListResponse,
    CalendarSummaryItem,
)
from app.services.credential_crypto import (
    ENVELOPE_PREFIX,
    decrypt_credentials,
    encrypt_credentials,
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


def _resolve_uuid(value: str | UUID) -> UUID:
    """Chuyển đổi string sang UUID đối tượng nếu cần, hỗ trợ chuỗi dev identifier."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        # Match the Draft service owner namespace in dev_user mode.
        return uuid5(NAMESPACE_DNS, f"proplan.user:{value}")


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


def _deserialize_credentials_safe(
    credential_ref: str,
    user_id: UUID | None = None,
    connection_id: UUID | None = None,
) -> dict[str, Any]:
    """Giải mã an toàn credential_ref (hỗ trợ Fernet Envelope có binding và fallback legacy JSON)."""
    if credential_ref.startswith(ENVELOPE_PREFIX):
        return decrypt_credentials(
            credential_ref,
            expected_user_id=str(user_id) if user_id else None,
            expected_connection_id=str(connection_id) if connection_id else None,
        )
    try:
        return json.loads(credential_ref)
    except Exception:
        return {}


class CalendarService:
    """Dịch vụ nghiệp vụ chính quản lý OAuth và Calendar Connections."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = get_calendar_client()

    async def create_authorization(
        self,
        user_id: str | UUID,
        device_id: str = "default_device",
        return_target: str | None = None,
        redirect_uri: str | None = None,
    ) -> tuple[CalendarAuthorization, str]:
        """Khởi tạo authorization intent và trả về authorization record cùng auth URL."""
        uid = _resolve_uuid(user_id)
        await _ensure_user_exists(self.db, uid)
        state = secrets.token_urlsafe(32)
        state_hash = _hash_state(state)

        now_utc = datetime.now(timezone.utc)
        # BUG-13: Chỉ dọn dẹp các authorization đã hết hạn hoặc đã tiêu thụ, giữ lại các session khác đang mở của user
        await self.db.execute(
            delete(CalendarAuthorization).where(
                CalendarAuthorization.user_id == uid,
                or_(
                    CalendarAuthorization.expires_at < now_utc,
                    CalendarAuthorization.consumed_at.is_not(None),
                ),
            )
        )

        expires_at = now_utc + timedelta(minutes=15)

        auth_intent = CalendarAuthorization(
            id=uuid4(),
            user_id=uid,
            state_hash=state_hash,
            redirect_uri=redirect_uri or "",
            return_target=return_target,
            expires_at=expires_at,
        )
        self.db.add(auth_intent)
        await self.db.commit()
        await self.db.refresh(auth_intent)

        auth_url = self.client.build_authorization_url(state=state, redirect_uri=redirect_uri)
        return auth_intent, auth_url

    async def create_authorization_url(
        self, user_id: str | UUID, redirect_uri: str | None = None
    ) -> str:
        """Sinh URL Google OAuth 2.0 (tương thích ngược với endpoint cũ)."""
        _, auth_url = await self.create_authorization(user_id, redirect_uri=redirect_uri)
        return auth_url

    async def _consume_valid_authorization(
        self,
        state: str,
        user_id: str | UUID | None = None,
    ) -> tuple[CalendarAuthorization, UUID, str | None]:
        """Xác thực state token chưa hết hạn/tiêu thụ, đánh dấu đã tiêu thụ và trả về metadata."""
        state_hash = _hash_state(state)
        now_utc = datetime.now(UTC)

        query = select(CalendarAuthorization).where(
            CalendarAuthorization.state_hash == state_hash,
            CalendarAuthorization.consumed_at.is_(None),
            CalendarAuthorization.expires_at > now_utc,
        )
        if user_id:
            uid = _resolve_uuid(user_id)
            query = query.where(CalendarAuthorization.user_id == uid)

        auth_intent = (await self.db.execute(query)).scalar_one_or_none()
        if not auth_intent:
            logger.warning("Invalid or expired OAuth state")
            raise CalendarAuthError("Trạng thái OAuth không hợp lệ hoặc phiên đăng nhập đã hết hạn.")

        uid = auth_intent.user_id
        await _ensure_user_exists(self.db, uid)

        # Lấy return_target và tiêu thụ authorization ngay để chống race condition / code reuse
        return_target = auth_intent.return_target
        auth_intent.consumed_at = now_utc
        await self.db.commit()
        return auth_intent, uid, return_target

    async def handle_oauth_error(
        self,
        state: str,
        user_id: str | UUID | None = None,
    ) -> str | None:
        """Xác thực và tiêu thụ authorization intent khi Google OAuth callback trả lỗi/từ chối."""
        _, _, return_target = await self._consume_valid_authorization(state=state, user_id=user_id)
        return return_target

    async def handle_oauth_callback(
        self,
        user_id: str | UUID | None,
        code: str,
        state: str,
        redirect_uri: str | None = None,
    ) -> tuple[CalendarConnection, str | None]:
        """Xác thực mã callback từ Google và tạo hoặc cập nhật CalendarConnection với Fernet encryption."""
        now_utc = datetime.now(UTC)
        auth_intent, uid, return_target = await self._consume_valid_authorization(
            state=state, user_id=user_id
        )
        if not code:
            raise CalendarAuthError("Thiếu mã xác thực OAuth từ Google.")

        # Đổi authorization code lấy token
        target_redirect_uri = redirect_uri or auth_intent.redirect_uri or None
        token_data = await self.client.exchange_auth_code(code, redirect_uri=target_redirect_uri)

        account_ref = token_data.get("account_ref", "unknown@gmail.com")
        granted_scopes = (
            token_data.get("scope", "").split()
            if isinstance(token_data.get("scope"), str)
            else []
        )

        # Kiểm tra xem kết nối này đã tồn tại chưa
        conn_stmt = select(CalendarConnection).where(
            CalendarConnection.user_id == uid,
            CalendarConnection.provider == "google",
            CalendarConnection.account_ref == account_ref,
        )
        existing_conn = (await self.db.execute(conn_stmt)).scalar_one_or_none()

        if existing_conn:
            conn = existing_conn
            conn_id = conn.id
        else:
            conn_id = uuid4()
            conn = CalendarConnection(
                id=conn_id,
                user_id=uid,
                provider="google",
                account_ref=account_ref,
                read_calendar_ids=[],
                write_calendar_id=None,
                granted_scopes=granted_scopes,
                connection_state="connected",
                credential_ref="",
                last_successful_sync_at=now_utc,
            )
            self.db.add(conn)

        # Giữ lại refresh_token cũ nếu Google không trả refresh_token mới khi reconnect
        existing_refresh_token = None
        if existing_conn and existing_conn.credential_ref:
            try:
                old_creds = _deserialize_credentials_safe(
                    existing_conn.credential_ref,
                    user_id=existing_conn.user_id,
                    connection_id=existing_conn.id,
                )
                existing_refresh_token = old_creds.get("refresh_token")
            except Exception:
                pass

        final_refresh_token = token_data.get("refresh_token") or existing_refresh_token

        # Mã hóa Fernet Envelope có ràng buộc user_id và connection_id
        token_payload = {
            "access_token": token_data.get("access_token"),
            "refresh_token": final_refresh_token,
            "expires_in": token_data.get("expires_in"),
            "token_type": token_data.get("token_type", "Bearer"),
            "account_ref": account_ref,
            "user_id": str(uid),
            "connection_id": str(conn_id),
            "stored_at": now_utc.isoformat(),
        }
        conn.credential_ref = encrypt_credentials(token_payload)
        conn.granted_scopes = granted_scopes
        conn.connection_state = "connected"
        conn.last_successful_sync_at = now_utc

        await self.db.commit()
        await self.db.refresh(conn)
        return conn, return_target

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
        """Lấy access_token hợp lệ từ Fernet credential_ref, tự động refresh nếu hết hạn."""
        if connection.connection_state == "disconnected":
            raise CalendarAuthError(
                "Kết nối Google Calendar đã bị ngắt. Vui lòng kết nối lại."
            )

        creds = _deserialize_credentials_safe(
            connection.credential_ref,
            user_id=connection.user_id,
            connection_id=connection.id,
        )
        access_token = creds.get("access_token")
        refresh_token = creds.get("refresh_token")
        stored_at_str = creds.get("stored_at")
        expires_in = creds.get("expires_in") or 3600

        is_expired = False
        if stored_at_str:
            try:
                stored_at = datetime.fromisoformat(stored_at_str)
                # Đệm an toàn 5 phút (300s) trước khi token hết hạn thực sự
                if datetime.now(timezone.utc) >= stored_at + timedelta(seconds=max(0, expires_in - 300)):
                    is_expired = True
            except Exception:
                pass

        if (not access_token or is_expired) and refresh_token:
            try:
                new_token_data = await self.client.refresh_access_token(refresh_token)
                access_token = new_token_data.get("access_token")
                new_expires_in = new_token_data.get("expires_in", 3600)
                now_utc = datetime.now(timezone.utc)
                creds["access_token"] = access_token
                creds["expires_in"] = new_expires_in
                creds["stored_at"] = now_utc.isoformat()
                if new_token_data.get("refresh_token"):
                    creds["refresh_token"] = new_token_data.get("refresh_token")
                connection.credential_ref = encrypt_credentials(creds)
                connection.connection_state = "connected"
                await self.db.commit()
                await self.db.refresh(connection)
            except CalendarAuthError as e:
                logger.error("Permanent auth failure refreshing token for connection %s: %s", connection.id, e)
                connection.connection_state = "needs_reconnect"
                await self.db.commit()
                raise CalendarAuthError("Phiên đăng nhập Google Calendar đã hết hạn và không thể làm mới. Vui lòng kết nối lại.") from e
            except Exception as e:
                logger.warning("Transient network error refreshing token for connection %s: %s", connection.id, e)
                raise CalendarClientError("Không thể kết nối đến Google Calendar để làm mới token. Vui lòng thử lại sau.", status_code=502) from e

        if not access_token:
            connection.connection_state = "needs_reconnect"
            await self.db.commit()
            raise CalendarAuthError("Không tìm thấy thông tin đăng nhập hợp lệ. Vui lòng kết nối lại.")

        return access_token

    async def list_calendars(
        self, user_id: str | UUID, connection_id: str | UUID
    ) -> CalendarListResponse:
        """Lấy danh sách các Calendar của một kết nối tài khoản."""
        conn = await self.get_connection_by_id(user_id, connection_id)
        if not conn:
            raise CalendarClientError("Không tìm thấy kết nối lịch tương ứng.", status_code=404)

        access_token = await self.get_valid_access_token(conn)
        raw_items = await self.client.list_calendar_list(access_token)

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

    async def create_configuration_preview(
        self,
        user_id: str | UUID,
        connection_id: str | UUID,
        read_calendar_ids: list[str],
        write_calendar_id: str | None,
        existing_event_action: str | None = None,
    ) -> CalendarPreview:
        """Tạo bản xem trước thay đổi cấu hình lịch (O24 CalendarPreview)."""
        uid = _resolve_uuid(user_id)
        conn = await self.get_connection_by_id(uid, connection_id)
        if not conn:
            raise CalendarClientError("Không tìm thấy kết nối lịch.", status_code=404)

        preview = CalendarPreview(
            id=uuid4(),
            user_id=uid,
            connection_id=conn.id,
            kind="configuration",
            base_connection_revision=1,
            source_revisions={"read_calendar_ids": conn.read_calendar_ids, "write_calendar_id": conn.write_calendar_id},
            payload={
                "read_calendar_ids": read_calendar_ids,
                "write_calendar_id": write_calendar_id,
                "existing_event_action": existing_event_action,
            },
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        self.db.add(preview)
        await self.db.commit()
        await self.db.refresh(preview)
        return preview

    async def confirm_configuration_preview(
        self,
        user_id: str | UUID,
        preview_id: str | UUID,
        expected_preview_revision: int | None = None,
    ) -> CalendarConnection:
        """Áp dụng cấu hình từ preview đã xác nhận, thực hiện provisioning secondary calendar nếu cần."""
        uid = _resolve_uuid(user_id)
        pid = _resolve_uuid(preview_id)

        stmt = select(CalendarPreview).where(
            CalendarPreview.user_id == uid,
            CalendarPreview.id == pid,
            CalendarPreview.status == "pending",
        )
        preview = (await self.db.execute(stmt)).scalar_one_or_none()
        if not preview:
            raise CalendarClientError("Bản xem trước cấu hình không tồn tại hoặc đã được xử lý.", status_code=404)

        if (
            expected_preview_revision is not None
            and preview.base_connection_revision is not None
            and preview.base_connection_revision != expected_preview_revision
        ):
            raise CalendarClientError(
                "Phiên bản bản xem trước không khớp (đã bị cập nhật).", status_code=409
            )

        now_utc = datetime.now(timezone.utc)
        if preview.expires_at and preview.expires_at < now_utc:
            preview.status = "expired"
            await self.db.commit()
            raise CalendarClientError("Bản xem trước cấu hình đã hết hạn. Vui lòng tạo bản xem trước mới.", status_code=409)

        conn = await self.db.get(CalendarConnection, preview.connection_id)
        if not conn or conn.user_id != uid:
            raise CalendarClientError("Không tìm thấy kết nối liên quan.", status_code=404)
        if conn.connection_state != "connected":
            raise CalendarClientError(
                "Kết nối lịch không ở trạng thái đang kết nối. Vui lòng kết nối lại.",
                status_code=409,
            )

        payload = preview.payload or {}
        read_ids = payload.get("read_calendar_ids", [])
        write_id = payload.get("write_calendar_id")
        action = payload.get("existing_event_action")

        # Provisioning secondary calendar "ProPlan VA Focus" nếu được yêu cầu (BUG-14: kiểm tra tránh tạo trùng)
        if action == "provision_new" or write_id in ("provision_new", "create_new"):
            access_token = await self.get_valid_access_token(conn)
            # Kiểm tra xem tài khoản đã có lịch ProPlan VA Focus từ trước chưa
            existing_cals = await self.client.list_calendar_list(access_token)
            found_id = None
            for c in existing_cals:
                if c.get("summary") == "ProPlan VA Focus":
                    found_id = c.get("id")
                    break

            if found_id:
                write_id = found_id
            else:
                try:
                    new_cal = await self.client.provision_proactive_calendar(
                        access_token=access_token,
                        summary="ProPlan VA Focus",
                        time_zone="Asia/Ho_Chi_Minh",
                    )
                    write_id = new_cal.get("id")
                except Exception as e:
                    logger.error("Failed to provision secondary calendar: %s", e)
                    raise CalendarClientError(f"Không thể tạo lịch ProPlan VA Focus: {e}", status_code=502)

        conn.read_calendar_ids = read_ids
        conn.write_calendar_id = write_id
        conn.last_successful_sync_at = datetime.now(timezone.utc)
        preview.status = "confirmed"

        await self.db.commit()
        await self.db.refresh(conn)
        return conn

    async def disconnect(
        self, user_id: str | UUID, connection_id: str | UUID
    ) -> CalendarConnection:
        """Ngắt kết nối tài khoản Calendar."""
        conn = await self.get_connection_by_id(user_id, connection_id)
        if not conn:
            raise CalendarClientError("Không tìm thấy kết nối lịch tương ứng.", status_code=404)

        conn.connection_state = "disconnected"
        await self.db.commit()
        await self.db.refresh(conn)
        return conn
