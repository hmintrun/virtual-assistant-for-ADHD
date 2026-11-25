# Pydantic v2 schemas cho User đăng nhập, đăng ký và UserPreference
from datetime import date

from pydantic import Field

from app.schemas.common import StrictSchema


class CurrentUser(StrictSchema):
    """O00 User tối thiểu: Danh tính người dùng hiện tại do dependency get_current_user cung cấp."""

    user_id: str = Field(min_length=1)
    email: str | None = None
    display_name: str | None = None
    is_active: bool = True
    current_streak_days: int = Field(default=0, ge=0)
    streak_frozen: bool = False
    streak_freeze_tokens: int = Field(default=2, ge=0)
    last_streak_date: date | None = None

