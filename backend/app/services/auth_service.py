"""App login owns its DB transaction; identity is Google subject, never email."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    decode_app_refresh_token,
    issue_app_access_token,
    unauthenticated,
    validate_app_signing_configuration,
    validate_auth_configuration,
)
from app.models.user import RefreshToken, User, UserPreference
from app.schemas.auth import (
    GoogleLoginData,
    LogoutData,
    TokenBundle,
    VerifiedGoogleIdentity,
)
from app.schemas.user import CurrentUser
from app.services.google_identity_verifier import verify_google_identity


def _ensure_app_auth_configured() -> None:
    try:
        validate_auth_configuration()
        validate_app_signing_configuration()
    except ValueError:
        raise AppError(
            status_code=503,
            code="AUTH_PROVIDER_UNAVAILABLE",
            message="Chưa cấu hình đăng nhập ứng dụng.",
            next_action="retry_login",
            retryable=True,
        ) from None


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def _lock_user_for_auth_tx(db: AsyncSession, user_id: UUID) -> None:
    """Thực thi lệnh ghi đầu tiên trong transaction để tuần tự hóa theo user_id trên cả SQLite và PostgreSQL."""
    locked_uid = await db.scalar(
        update(User).where(User.id == user_id).values(revision=User.revision).returning(User.id)
    )
    if locked_uid is None:
        raise unauthenticated()


async def _revoke_active_family_tokens(
    db: AsyncSession,
    *,
    family_id: UUID,
    reason: str,
    now: datetime,
) -> None:
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(
            revoked_at=now,
            revoked_reason=reason,
            updated_at=now,
            revision=RefreshToken.revision + 1,
        )
        .execution_options(synchronize_session="fetch")
    )


def _persist_refresh_token(
    db: AsyncSession,
    *,
    user_id: UUID,
    refresh_token: str,
    family_id: UUID | None = None,
) -> RefreshToken:
    claims = decode_app_refresh_token(refresh_token)
    row = RefreshToken(
        user_id=user_id,
        jti=claims.jti,
        family_id=family_id or uuid4(),
        issued_at=claims.issued_at,
        expires_at=claims.expires_at,
    )
    db.add(row)
    return row


def current_user(user: User) -> CurrentUser:
    if not user.is_active:
        raise AppError(
            status_code=403,
            code="ACCOUNT_DISABLED",
            message="Tài khoản đã bị vô hiệu hóa.",
            next_action="contact_support",
        )
    return CurrentUser(
        user_id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        current_streak_days=user.current_streak_days,
        streak_frozen=user.streak_frozen,
        streak_freeze_tokens=user.streak_freeze_tokens,
        last_streak_date=user.last_streak_date,
    )


async def load_current_user(db: AsyncSession, user_id: UUID) -> CurrentUser:
    user = await db.get(User, user_id, populate_existing=True)
    if user is None:
        raise unauthenticated()
    return current_user(user)


def link_conflict() -> AppError:
    return AppError(
        status_code=409,
        code="ACCOUNT_LINK_CONFLICT",
        message="Không thể liên kết tài khoản này.",
        next_action="review_account_link",
    )


async def login_with_google(
    db: AsyncSession,
    id_token: str,
    verifier: Callable[[str], Awaitable[VerifiedGoogleIdentity]] = verify_google_identity,
) -> GoogleLoginData:
    _ensure_app_auth_configured()
    identity = await verifier(id_token)
    # This service is called with a fresh request session; a parent transaction
    # must retain ownership rather than being silently committed/rolled back.
    if db.in_transaction():
        raise RuntimeError("Auth login requires a fresh session")
    user_id = None
    for attempt in range(2):
        try:
            async with db.begin():
                user = await db.scalar(select(User).where(User.google_sub == identity.sub))
                if user is not None:
                    current_user(user)
                collision = await db.scalar(select(User).where(User.email == identity.email))
                # Under READ COMMITTED a winner may become visible between the
                # subject lookup and the email lookup. Same subject is the same
                # identity, not an email-link conflict.
                if user is None and collision is not None and collision.google_sub == identity.sub:
                    user = collision
                    current_user(user)
                if collision is not None and (user is None or collision.id != user.id):
                    raise link_conflict()
                if user is None:
                    user = User(
                        id=uuid4(),
                        google_sub=identity.sub,
                        email=identity.email,
                        display_name=identity.display_name,
                        is_active=True,
                    )
                    db.add(user)
                    await db.flush()
                    db.add(
                        UserPreference(
                            user_id=user.id,
                            work_interval_seconds=settings.DEFAULT_WORK_INTERVAL_SECONDS,
                            rest_interval_seconds=settings.DEFAULT_REST_INTERVAL_SECONDS,
                            micro_task_seconds=settings.DEFAULT_MICRO_TASK_SECONDS,
                            buffer_multiplier=settings.DEFAULT_BUFFER_MULTIPLIER,
                            reminder_offsets=[900, 600, 300],
                            reminder_style="gentle",
                            effective_revision=1,
                        )
                    )
                else:
                    user.email, user.display_name = identity.email, identity.display_name
                user_id = user.id
            break
        except IntegrityError:
            # begin() rolled back only this service's transaction. Concurrent
            # subject creation is retried once, then resolved as a link conflict.
            if attempt:
                raise link_conflict() from None
    assert user_id is not None
    async with db.begin():
        # Re-read after commit, including active state, before issuing credentials.
        profile = await load_current_user(db, user_id)
        bundle = issue_app_access_token(user_id)
        if bundle.refresh_token is None:
            raise RuntimeError("issue_app_access_token did not issue a refresh token")
        _persist_refresh_token(
            db,
            user_id=user_id,
            refresh_token=bundle.refresh_token,
            family_id=uuid4(),
        )
    return GoogleLoginData(**bundle.model_dump(), user=profile)


async def refresh_app_tokens(db: AsyncSession, refresh_token: str) -> TokenBundle:
    _ensure_app_auth_configured()
    claims = decode_app_refresh_token(refresh_token)
    if db.in_transaction():
        raise RuntimeError("Auth refresh requires a fresh session")

    now = datetime.now(UTC)
    reuse_detected = False
    bundle: TokenBundle | None = None

    async with db.begin():
        await _lock_user_for_auth_tx(db, claims.user_id)
        token_row = await db.scalar(
            select(RefreshToken).where(RefreshToken.jti == claims.jti).with_for_update()
        )
        if token_row is None or token_row.user_id != claims.user_id:
            raise unauthenticated()

        if token_row.revoked_at is not None or token_row.replaced_by_jti is not None:
            await _revoke_active_family_tokens(
                db,
                family_id=token_row.family_id,
                reason="reuse_detected",
                now=now,
            )
            reuse_detected = True
        else:
            user = await db.get(User, claims.user_id, populate_existing=True)
            if user is None:
                raise unauthenticated()
            current_user(user)

            if _ensure_utc(token_row.expires_at) <= now:
                raise unauthenticated()

            bundle = issue_app_access_token(claims.user_id)
            if bundle.refresh_token is None:
                raise RuntimeError("issue_app_access_token did not issue a refresh token")
            new_claims = decode_app_refresh_token(bundle.refresh_token)
            rotated_res = await db.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.id == token_row.id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.replaced_by_jti.is_(None),
                )
                .values(
                    revoked_at=now,
                    revoked_reason="rotated",
                    replaced_by_jti=new_claims.jti,
                    updated_at=now,
                    revision=RefreshToken.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
            if int(getattr(rotated_res, "rowcount", 0) or 0) != 1:
                await _revoke_active_family_tokens(
                    db,
                    family_id=token_row.family_id,
                    reason="reuse_detected",
                    now=now,
                )
                reuse_detected = True
            else:
                _persist_refresh_token(
                    db,
                    user_id=claims.user_id,
                    refresh_token=bundle.refresh_token,
                    family_id=token_row.family_id,
                )

    if reuse_detected:
        raise unauthenticated()
    assert bundle is not None
    return bundle


async def logout_app_session(db: AsyncSession, refresh_token: str) -> LogoutData:
    _ensure_app_auth_configured()
    claims = decode_app_refresh_token(refresh_token)
    if db.in_transaction():
        raise RuntimeError("Auth logout requires a fresh session")

    async with db.begin():
        await _lock_user_for_auth_tx(db, claims.user_id)
        user = await db.get(User, claims.user_id, populate_existing=True)
        if user is None:
            raise unauthenticated()
        current_user(user)

        token_row = await db.scalar(
            select(RefreshToken).where(RefreshToken.jti == claims.jti).with_for_update()
        )
        if token_row is None or token_row.user_id != claims.user_id:
            raise unauthenticated()

        if token_row.revoked_at is not None:
            return LogoutData(logged_out=True, revoked=False)

        await _revoke_active_family_tokens(
            db,
            family_id=token_row.family_id,
            reason="logout",
            now=datetime.now(UTC),
        )
        return LogoutData(logged_out=True, revoked=True)
