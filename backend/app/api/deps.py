# Các Dependency dùng chung cho FastAPI Router (cung cấp current_user, DB session...)
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import resolve_dev_current_user
from app.schemas.user import CurrentUser


async def get_current_user() -> CurrentUser:
    """Dependency cung cấp người dùng đang đăng nhập cho các API Router."""
    return resolve_dev_current_user()


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
