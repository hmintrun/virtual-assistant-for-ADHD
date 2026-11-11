"""Shared DB and app authentication dependencies."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
app_bearer = HTTPBearer(scheme_name="AppBearer", bearerFormat="JWT", auto_error=False)


async def get_current_user(
    request: Request,
    db: DbSessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(app_bearer)],
) -> CurrentUser:
    # HTTPBearer có thể trả None cho cả thiếu header và sai scheme; chỉ thiếu mới được seed.
    authorization = request.headers.get("Authorization")
    if authorization is None and dev_seed_enabled():
        user = resolve_dev_current_user()
        await ensure_dev_seed_in_db(db)
        return user
    if authorization is None:
        raise unauthenticated()
    parts = authorization.split()
    if credentials is None or len(parts) != 2 or parts[0].lower() != "bearer":
        raise unauthenticated()
    claims = decode_app_access_token(parts[1])
    return await load_current_user(db, claims.user_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
