# Các Dependency dùng chung cho FastAPI Router (cung cấp current_user, DB session...)
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import resolve_dev_current_user
from app.schemas.user import CurrentUser
from app.services.draft_service import ensure_dev_seed_in_db

DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(db: DbSessionDep) -> CurrentUser:
    """Dependency cung cấp người dùng đang đăng nhập cho các API Router."""
    user = resolve_dev_current_user()
    # Chỉ seed sau khi đã xác thực môi trường dev/test, trước mọi lệnh khóa user.
    await ensure_dev_seed_in_db(db)
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
