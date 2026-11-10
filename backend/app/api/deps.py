"""Shared DB and app authentication dependencies."""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    decode_app_access_token,
    dev_seed_enabled,
    resolve_dev_current_user,
    unauthenticated,
)
from app.schemas.user import CurrentUser
from app.services.auth_service import load_current_user
from app.services.draft_service import ensure_dev_seed_in_db

DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if authorization is None and dev_seed_enabled():
        user = resolve_dev_current_user()
        await ensure_dev_seed_in_db(db)
        return user
    if authorization is None:
        raise unauthenticated()
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise unauthenticated()
    claims = decode_app_access_token(parts[1])
    return await load_current_user(db, claims.user_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
