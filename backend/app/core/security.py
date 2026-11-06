# Xử lý băm mật khẩu, mã hóa và xác thực JWT Token
from app.core.config import settings
from app.core.errors import AppError
from app.schemas.user import CurrentUser

# Hai tài khoản thử nghiệm chuẩn (User A và User B) phục vụ dev/test trước khi ghép Google Sign-In
SEED_USER_A = CurrentUser(
    user_id="user_a",
    email="user.a@example.com",
    display_name="User A (Dev/Test)",
    is_active=True,
)

SEED_USER_B = CurrentUser(
    user_id="user_b",
    email="user.b@example.com",
    display_name="User B (Dev/Test)",
    is_active=True,
)


def resolve_dev_current_user() -> CurrentUser:
    """Trả về user thử nghiệm trong môi trường development/test; từ chối nếu ở môi trường dùng thật."""
    if settings.ENVIRONMENT not in {"development", "testing"}:
        raise AppError(
            status_code=401,
            code="UNAUTHENTICATED",
            message="Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
            next_action="authenticate",
        )
    return SEED_USER_A
