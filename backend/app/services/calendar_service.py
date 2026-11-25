"""Dịch vụ quản lý kết nối Google Calendar (O23 CalendarConnection), xác thực OAuth 2.0 và danh sách lịch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
)
from app.models.session import Session
from app.models.user import User
from app.schemas.calendar import (
    CalendarConnectionRead,
    CalendarListResponse,
    CalendarSummaryItem,
)
from app.services import command_service
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

@dataclass
class _RefCountedLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ref_count: int = 0


_OWNER_CALENDAR_LOCKS: dict[tuple[int, str], _RefCountedLock] = {}
_CONNECTION_SYNC_LOCKS: dict[tuple[int, str], _RefCountedLock] = {}


@asynccontextmanager
async def _acquire_refcounted_lock(
    lock_map: dict[tuple[int, str], _RefCountedLock],
    key: tuple[int, str],
) -> AsyncGenerator[None]:
    entry = lock_map.get(key)
    if entry is None:
        entry = _RefCountedLock()
        lock_map[key] = entry
    entry.ref_count += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.ref_count -= 1
        if entry.ref_count <= 0 and lock_map.get(key) is entry:
            lock_map.pop(key, None)


@asynccontextmanager
async def get_owner_calendar_lock(owner_id: str) -> AsyncGenerator[None]:
    """Tuần tự hóa thao tác confirm song song theo (event_loop_id, owner_id) và tự dọn entry khi hết người chờ."""
    loop = asyncio.get_running_loop()
    key = (id(loop), str(owner_id))
    async with _acquire_refcounted_lock(_OWNER_CALENDAR_LOCKS, key):
        yield


@asynccontextmanager
async def get_connection_sync_lock(connection_id: UUID | str) -> AsyncGenerator[None]:
    """Tuần tự hóa đồng bộ theo cặp (event_loop_id, connection_id) và tự dọn entry khi hết người chờ."""
    loop = asyncio.get_running_loop()
    key = (id(loop), str(_resolve_uuid(connection_id)))
    async with _acquire_refcounted_lock(_CONNECTION_SYNC_LOCKS, key):
        yield


def _hash_state(state: str) -> str:
    """Băm state token bằng SHA256 để lưu trữ an toàn trong DB."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _ensure_utc(dt: datetime) -> datetime:
    """Chuẩn hóa datetime về UTC có nhận biết múi giờ."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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

    @staticmethod
    def _sanitize_read_calendar_ids(
        read_calendar_ids: list[str] | None,
        write_calendar_id: str | None,
        *,
        account_ref: str | None = None,
        raw_calendars: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        """Loại bỏ lịch ghi chuyên dụng (ví dụ ProPlan VA Focus) và giá trị rỗng/trùng khỏi danh sách đọc lịch bận."""
        primary_ids: set[str] = {"primary"}
        excluded_ids: set[str] = set()

        if isinstance(account_ref, str):
            acct = account_ref.strip()
            if acct and acct.lower() not in (
                "google_calendar_user",
                "unknown@gmail.com",
                "unknown@google.com",
            ):
                primary_ids.add(acct)

        if raw_calendars:
            for cal in raw_calendars:
                cal_id = cal.get("id")
                if not isinstance(cal_id, str):
                    continue
                cal_id_clean = cal_id.strip()
                if not cal_id_clean:
                    continue
                if cal.get("primary"):
                    primary_ids.add(cal_id_clean)
                elif str(cal.get("summary") or "").strip().lower() == "proplan va focus":
                    excluded_ids.add(cal_id_clean)

        if isinstance(write_calendar_id, str):
            write_id_clean = write_calendar_id.strip()
            if (
                write_id_clean
                and write_id_clean not in ("provision_new", "create_new")
                and write_id_clean not in primary_ids
            ):
                excluded_ids.add(write_id_clean)

        sanitized: list[str] = []
        seen: set[str] = set()
        for raw_id in read_calendar_ids or []:
            if not isinstance(raw_id, str):
                continue
            cal_id = raw_id.strip()
            if not cal_id or cal_id in excluded_ids or cal_id in seen:
                continue
            seen.add(cal_id)
            sanitized.append(cal_id)
        return sanitized

    async def _try_fetch_calendars_for_sanitization(
        self,
        conn: CalendarConnection,
        read_calendar_ids: list[str] | None,
    ) -> list[dict[str, Any]] | None:
        """Đọc danh sách lịch ở chế độ read-only (không commit DB) để nhận diện ProPlan VA Focus / primary IDs."""
        has_non_primary_read = any(
            isinstance(cid, str) and cid.strip() not in ("", "primary")
            for cid in (read_calendar_ids or [])
        )
        if not has_non_primary_read or conn.connection_state != "connected":
            return None
        try:
            creds = _deserialize_credentials_safe(
                conn.credential_ref,
                user_id=conn.user_id,
                connection_id=conn.id,
            )
            access_token = creds.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                return None
            return await self.client.list_calendar_list(access_token)
        except Exception:
            return None

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

        now_utc = datetime.now(UTC)
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
        if existing_conn is None and account_ref not in ("unknown@google.com", "unknown@gmail.com"):
            unknown_stmt = (
                select(CalendarConnection)
                .where(
                    CalendarConnection.user_id == uid,
                    CalendarConnection.provider == "google",
                    CalendarConnection.account_ref.in_(
                        ("unknown@google.com", "unknown@gmail.com")
                    ),
                )
                .order_by(CalendarConnection.created_at.desc())
            )
            existing_conn = (await self.db.execute(unknown_stmt)).scalars().first()
            if existing_conn is not None:
                existing_conn.account_ref = account_ref

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
                if datetime.now(UTC) >= stored_at + timedelta(seconds=max(0, expires_in - 300)):
                    is_expired = True
            except Exception:
                pass

        if (not access_token or is_expired) and refresh_token:
            try:
                new_token_data = await self.client.refresh_access_token(refresh_token)
                access_token = new_token_data.get("access_token")
                new_expires_in = new_token_data.get("expires_in", 3600)
                now_utc = datetime.now(UTC)
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
        if conn.account_ref in ("unknown@google.com", "unknown@gmail.com"):
            for item in raw_items:
                cal_id = item.get("id")
                if (
                    item.get("primary")
                    and isinstance(cal_id, str)
                    and "@" in cal_id
                    and cal_id.strip() != "primary"
                ):
                    resolved_email = cal_id.strip()
                    conflict_id = await self.db.scalar(
                        select(CalendarConnection.id).where(
                            CalendarConnection.user_id == conn.user_id,
                            CalendarConnection.provider == conn.provider,
                            CalendarConnection.account_ref == resolved_email,
                            CalendarConnection.id != conn.id,
                        )
                    )
                    if conflict_id is None:
                        conn.account_ref = resolved_email
                        try:
                            await self.db.commit()
                            await self.db.refresh(conn)
                        except IntegrityError:
                            await self.db.rollback()
                            conn = await self.get_connection_by_id(user_id, connection_id) or conn
                    break

        current_read_ids = list(conn.read_calendar_ids or [])
        sanitized_read_ids = self._sanitize_read_calendar_ids(
            current_read_ids,
            conn.write_calendar_id,
            account_ref=conn.account_ref,
            raw_calendars=raw_items,
        )
        if sanitized_read_ids != current_read_ids:
            conn.read_calendar_ids = sanitized_read_ids
            try:
                await self.db.commit()
                await self.db.refresh(conn)
            except SQLAlchemyError:
                await self.db.rollback()
                conn = await self.get_connection_by_id(user_id, connection_id) or conn
                sanitized_read_ids = self._sanitize_read_calendar_ids(
                    conn.read_calendar_ids,
                    conn.write_calendar_id,
                    account_ref=conn.account_ref,
                    raw_calendars=raw_items,
                )

        read_ids = set(sanitized_read_ids)
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

        raw_cals = await self._try_fetch_calendars_for_sanitization(conn, read_calendar_ids)
        conn.read_calendar_ids = self._sanitize_read_calendar_ids(
            read_calendar_ids,
            write_calendar_id,
            account_ref=conn.account_ref,
            raw_calendars=raw_cals,
        )
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

        raw_cals = await self._try_fetch_calendars_for_sanitization(conn, read_calendar_ids)
        sanitized_read_ids = self._sanitize_read_calendar_ids(
            read_calendar_ids,
            write_calendar_id,
            account_ref=conn.account_ref,
            raw_calendars=raw_cals,
        )
        preview = CalendarPreview(
            id=uuid4(),
            user_id=uid,
            connection_id=conn.id,
            kind="configuration",
            base_connection_revision=conn.revision,
            source_revisions={
                "read_calendar_ids": conn.read_calendar_ids,
                "write_calendar_id": conn.write_calendar_id,
                "connection_revision": conn.revision,
            },
            payload={
                "read_calendar_ids": sanitized_read_ids,
                "write_calendar_id": write_calendar_id,
                "existing_event_action": existing_event_action,
            },
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
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

        async with get_owner_calendar_lock(str(uid)):
            await command_service.lock_owner(self.db, str(uid))

            stmt = (
                select(CalendarPreview)
                .where(
                    CalendarPreview.user_id == uid,
                    CalendarPreview.id == pid,
                    CalendarPreview.kind == "configuration",
                )
                .execution_options(populate_existing=True)
            )
            preview = (await self.db.execute(stmt)).scalar_one_or_none()
            if not preview:
                raise CalendarClientError(
                    "Bản xem trước cấu hình không tồn tại.", status_code=404
                )

            if expected_preview_revision is None:
                raise CalendarClientError(
                    "Thiếu expected_preview_revision khi xác nhận bản xem trước cấu hình.",
                    status_code=422,
                )

            if preview.status == "pending" and preview.revision != expected_preview_revision:
                raise CalendarClientError(
                    "Phiên bản bản xem trước không khớp (đã bị cập nhật).",
                    status_code=409,
                    error_code="REVISION_CONFLICT",
                )

            now_utc = datetime.now(UTC)
            if preview.status == "pending" and preview.expires_at and preview.expires_at < now_utc:
                preview.status = "expired"
                await self.db.commit()
                raise CalendarClientError(
                    "Bản xem trước cấu hình đã hết hạn. Vui lòng tạo bản xem trước mới.",
                    status_code=409,
                    error_code="PREVIEW_EXPIRED",
                )

            conn = (
                await self.db.execute(
                    select(CalendarConnection)
                    .where(CalendarConnection.id == preview.connection_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if not conn or conn.user_id != uid:
                raise CalendarClientError("Không tìm thấy kết nối liên quan.", status_code=404)

            # Idempotent replay thống nhất với event-change-previews (Risk P1-D03 / UF6-BUG-04)
            if preview.status == "confirmed":
                valid_replay_revisions = {preview.revision, max(1, preview.revision - 1)}
                if expected_preview_revision not in valid_replay_revisions:
                    raise CalendarClientError(
                        "Phiên bản bản xem trước không khớp (đã bị cập nhật).",
                        status_code=409,
                        error_code="REVISION_CONFLICT",
                    )
                return conn

            if preview.status == "expired":
                raise CalendarClientError(
                    "Bản xem trước cấu hình đã hết hạn. Vui lòng tạo bản xem trước mới.",
                    status_code=409,
                    error_code="PREVIEW_EXPIRED",
                )

            if preview.status != "pending":
                raise CalendarClientError(
                    "Bản xem trước cấu hình đã được xử lý hoặc hết hạn.",
                    status_code=409,
                    error_code="PREVIEW_ALREADY_PROCESSED",
                )

            if conn.connection_state != "connected":
                raise CalendarClientError(
                    "Kết nối lịch không ở trạng thái đang kết nối. Vui lòng kết nối lại.",
                    status_code=409,
                    error_code="CALENDAR_CONFLICT",
                )

            if (
                preview.base_connection_revision is None
                or conn.revision != preview.base_connection_revision
            ):
                raise CalendarClientError(
                    "Kết nối lịch đã thay đổi kể từ khi tạo bản xem trước. Vui lòng tạo lại bản xem trước.",
                    status_code=409,
                    error_code="REVISION_CONFLICT",
                )

            payload = preview.payload or {}
            read_ids = payload.get("read_calendar_ids", [])
            write_id = payload.get("write_calendar_id")
            action = payload.get("existing_event_action")
            raw_cals: list[dict[str, Any]] | None = None

            # Provisioning secondary calendar "ProPlan VA Focus" nếu được yêu cầu (BUG-14: kiểm tra tránh tạo trùng)
            if action == "provision_new" or write_id in ("provision_new", "create_new"):
                access_token = await self.get_valid_access_token(conn)
                expected_conn_rev = conn.revision
                # Kiểm tra xem tài khoản đã có lịch ProPlan VA Focus từ trước chưa
                existing_cals = await self.client.list_calendar_list(access_token)
                raw_cals = existing_cals
                found_id = None
                for c in existing_cals:
                    if str(c.get("summary") or "").strip().lower() == "proplan va focus":
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
                        raise CalendarClientError(
                            f"Không thể tạo lịch ProPlan VA Focus: {e}", status_code=502
                        ) from e

                # Tái chiếm khóa chủ sở hữu & tải lại preview/conn sau I/O mạng hoặc commit refresh token
                await command_service.lock_owner(self.db, str(uid))
                preview = (
                    await self.db.execute(
                        select(CalendarPreview)
                        .where(
                            CalendarPreview.user_id == uid,
                            CalendarPreview.id == pid,
                            CalendarPreview.kind == "configuration",
                        )
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if not preview:
                    raise CalendarClientError(
                        "Bản xem trước cấu hình không tồn tại.", status_code=404
                    )

                conn = (
                    await self.db.execute(
                        select(CalendarConnection)
                        .where(CalendarConnection.id == preview.connection_id)
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if not conn or conn.user_id != uid:
                    raise CalendarClientError(
                        "Không tìm thấy kết nối liên quan.", status_code=404
                    )

                if preview.status == "confirmed":
                    valid_replay_revisions = {preview.revision, max(1, preview.revision - 1)}
                    if expected_preview_revision not in valid_replay_revisions:
                        raise CalendarClientError(
                            "Phiên bản bản xem trước không khớp (đã bị cập nhật).",
                            status_code=409,
                            error_code="REVISION_CONFLICT",
                        )
                    return conn

                if preview.status == "expired":
                    raise CalendarClientError(
                        "Bản xem trước cấu hình đã hết hạn. Vui lòng tạo bản xem trước mới.",
                        status_code=409,
                        error_code="PREVIEW_EXPIRED",
                    )

                if preview.status != "pending":
                    raise CalendarClientError(
                        "Bản xem trước cấu hình đã được xử lý hoặc hết hạn.",
                        status_code=409,
                        error_code="PREVIEW_ALREADY_PROCESSED",
                    )

                if preview.revision != expected_preview_revision:
                    raise CalendarClientError(
                        "Phiên bản bản xem trước không khớp (đã bị cập nhật).",
                        status_code=409,
                        error_code="REVISION_CONFLICT",
                    )

                if conn.connection_state != "connected":
                    raise CalendarClientError(
                        "Kết nối lịch không ở trạng thái đang kết nối. Vui lòng kết nối lại.",
                        status_code=409,
                        error_code="CALENDAR_CONFLICT",
                    )

                if conn.revision != expected_conn_rev:
                    raise CalendarClientError(
                        "Kết nối lịch đã thay đổi kể từ khi tạo bản xem trước. Vui lòng tạo lại bản xem trước.",
                        status_code=409,
                        error_code="REVISION_CONFLICT",
                    )

            if raw_cals is None:
                raw_cals = await self._try_fetch_calendars_for_sanitization(conn, read_ids)
            conn.read_calendar_ids = self._sanitize_read_calendar_ids(
                read_ids,
                write_id,
                account_ref=conn.account_ref,
                raw_calendars=raw_cals,
            )
            conn.write_calendar_id = write_id
            conn.last_successful_sync_at = datetime.now(UTC)
            preview.status = "confirmed"

            await self.db.commit()
            await self.db.refresh(conn)
            return conn

    async def disconnect(
        self, user_id: str | UUID, connection_id: str | UUID
    ) -> CalendarConnection:
        """Ngắt kết nối tài khoản Calendar, hủy toàn bộ sync job còn mở và bảo toàn Task/Step/Session."""
        uid = _resolve_uuid(user_id)
        cid = _resolve_uuid(connection_id)
        await self.db.rollback()
        async with get_connection_sync_lock(cid):
            conn = (
                await self.db.execute(
                    select(CalendarConnection)
                    .where(CalendarConnection.user_id == uid, CalendarConnection.id == cid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if not conn:
                raise CalendarClientError("Không tìm thấy kết nối lịch tương ứng.", status_code=404)

            now_utc = datetime.now(UTC)
            await self.db.execute(
                update(CalendarConnection)
                .where(CalendarConnection.id == cid, CalendarConnection.user_id == uid)
                .values(
                    connection_state="disconnected",
                    updated_at=now_utc,
                    revision=CalendarConnection.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
            await self.db.execute(
                update(CalendarSyncJob)
                .where(
                    CalendarSyncJob.user_id == uid,
                    CalendarSyncJob.connection_id == cid,
                    CalendarSyncJob.job_state.in_(("queued", "retry", "conflict", "running")),
                )
                .values(
                    job_state="cancelled",
                    last_error_code="DISCONNECTED",
                    next_retry_at=None,
                    lease_expires_at=None,
                    updated_at=now_utc,
                    revision=CalendarSyncJob.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
            await self.db.commit()
            fresh_conn = (
                await self.db.execute(
                    select(CalendarConnection)
                    .where(CalendarConnection.user_id == uid, CalendarConnection.id == cid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            return fresh_conn

    async def reconcile_external_link_changes(
        self, user_id: str | UUID, connection_id: str | UUID
    ) -> dict[str, int]:
        """Đối soát thay đổi bên ngoài trên Google Calendar cho các CalendarEventLink hiện hành (A22.3 / K19 / K20)."""
        from app.services.planning.calendar_ingestion import normalize_google_event

        conn = await self.get_connection_by_id(user_id, connection_id)
        if not conn:
            raise CalendarClientError("Không tìm thấy kết nối lịch.", status_code=404)

        if conn.connection_state == "disconnected":
            return {
                "links_checked": 0,
                "links_deleted_externally": 0,
                "links_diverged": 0,
            }

        active_links = (
            await self.db.scalars(
                select(CalendarEventLink).where(
                    CalendarEventLink.user_id == conn.user_id,
                    CalendarEventLink.connection_id == conn.id,
                    CalendarEventLink.is_current.is_(True),
                    CalendarEventLink.google_event_id.is_not(None),
                )
            )
        ).all()
        if not active_links:
            return {
                "links_checked": 0,
                "links_deleted_externally": 0,
                "links_diverged": 0,
            }

        link_ids = [lk.id for lk in active_links]
        conn_id = conn.id
        conn_user_id = conn.user_id

        access_token = await self.get_valid_access_token(conn)

        active_links = (
            await self.db.scalars(
                select(CalendarEventLink).where(
                    CalendarEventLink.user_id == conn_user_id,
                    CalendarEventLink.connection_id == conn_id,
                    CalendarEventLink.id.in_(link_ids),
                    CalendarEventLink.is_current.is_(True),
                )
            )
        ).all()

        pending_job_link_ids = set(
            (
                await self.db.scalars(
                    select(CalendarSyncJob.event_link_id).where(
                        CalendarSyncJob.user_id == conn_user_id,
                        CalendarSyncJob.connection_id == conn_id,
                        CalendarSyncJob.event_link_id.in_(link_ids),
                        CalendarSyncJob.job_state.in_(("queued", "running", "retry")),
                    )
                )
            ).all()
        )

        links_checked = 0
        links_deleted_externally = 0
        links_diverged = 0

        for link in active_links:
            if not link.google_event_id:
                continue
            links_checked += 1
            remote_event = await self.client.get_event(
                access_token, link.google_calendar_id, str(link.google_event_id)
            )
            now_utc = datetime.now(UTC)

            if (
                remote_event is None
                or str(remote_event.get("status", "")).lower() == "cancelled"
            ):
                if link.id not in pending_job_link_ids:
                    link.sync_state = "deleted_externally"
                    link.is_current = False
                    link.updated_at = now_utc
                    links_deleted_externally += 1
                continue

            sess = await self.db.get(Session, link.session_id)
            norm = normalize_google_event(
                remote_event, str(conn_id), link.google_calendar_id
            )
            time_drifted = False
            if sess is not None and norm is not None:
                sess_start = _ensure_utc(sess.scheduled_start)
                sess_end = _ensure_utc(sess.scheduled_end)
                rem_start = _ensure_utc(norm.start_at)
                rem_end = _ensure_utc(norm.end_at)
                if (
                    abs((rem_start - sess_start).total_seconds()) > 1
                    or abs((rem_end - sess_end).total_seconds()) > 1
                ):
                    time_drifted = True

            remote_etag = remote_event.get("etag")
            etag_drifted = bool(
                link.google_etag is not None
                and remote_etag is not None
                and str(remote_etag) != str(link.google_etag)
            )

            if (time_drifted or etag_drifted) and link.id not in pending_job_link_ids:
                link.sync_state = "diverged"
                link.updated_at = now_utc
                links_diverged += 1

        await self.db.commit()
        return {
            "links_checked": links_checked,
            "links_deleted_externally": links_deleted_externally,
            "links_diverged": links_diverged,
        }
