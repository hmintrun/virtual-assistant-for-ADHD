# Pydantic v2 schemas cho User đăng nhập, đăng ký và UserPreference
from pydantic import Field

from app.schemas.common import StrictSchema


class CurrentUser(StrictSchema):
    """O00 User tối thiểu: Danh tính người dùng hiện tại do dependency get_current_user cung cấp."""

    user_id: str = Field(min_length=1)
    email: str | None = None
    display_name: str | None = None
    is_active: bool = True

