"""App login owns its DB transaction; identity is Google subject, never email."""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    issue_app_access_token,
    unauthenticated,
    validate_app_signing_configuration,
    validate_auth_configuration,
)
from app.models.user import User, UserPreference
from app.schemas.auth import GoogleLoginData, VerifiedGoogleIdentity
from app.schemas.user import CurrentUser
from app.services.google_identity_verifier import verify_google_identity


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
    # Re-read after commit, including active state, before issuing credentials.
    profile = await load_current_user(db, user_id)
    bundle = issue_app_access_token(user_id)
    return GoogleLoginData(**bundle.model_dump(), user=profile)
