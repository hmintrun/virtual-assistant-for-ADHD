# Xử lý băm mật khẩu, mã hóa và xác thực JWT Token
from datetime import UTC, datetime
from uuid import UUID, uuid4

from jose import JWTError, jwt

from app.core.config import Settings, settings
from app.core.errors import AppError
from app.schemas.auth import AppAccessClaims, AppRefreshClaims, TokenBundle
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
    if not dev_seed_enabled():
        raise AppError(
            status_code=401,
            code="UNAUTHENTICATED",
            message="Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
            next_action="authenticate",
        )
    return SEED_USER_A


def dev_seed_enabled() -> bool:
    return settings.AUTH_MODE == "dev_user" and settings.ENVIRONMENT in {"development", "testing"}


def validate_auth_configuration(config: Settings | None = None) -> None:
    """Fail before workers/DB access; never use the Calendar encryption key for JWT."""
    config = config or settings
    if config.AUTH_MODE == "dev_user":
        if config.ENVIRONMENT not in {"development", "testing"}:
            raise ValueError("dev_user authentication requires development/testing")
        return
    validate_app_signing_configuration(config)
    if not config.GOOGLE_SIGN_IN_AUDIENCES or any(
        not aud.strip()
        or not aud.endswith(".apps.googleusercontent.com")
        or any(word in aud.lower() for word in ("your-", "example", "placeholder"))
        for aud in config.GOOGLE_SIGN_IN_AUDIENCES
    ):
        raise ValueError("Google Sign-In audiences must be configured")


def validate_app_signing_configuration(config: Settings | None = None) -> None:
    config = config or settings
    key = config.JWT_SECRET_KEY
    if (
        len(key.encode()) < 32
        or key == config.SECRET_KEY
        or any(word in key.lower() for word in ("change", "example", "your-", "placeholder"))
    ):
        raise ValueError("google authentication requires a separate strong JWT_SECRET_KEY")
    if not config.JWT_ISSUER.strip() or not config.JWT_AUDIENCE.strip():
        raise ValueError("JWT issuer/audience must be configured")


def unauthenticated() -> AppError:
    return AppError(
        status_code=401,
        code="UNAUTHENTICATED",
        message="Vui lòng đăng nhập lại.",
        next_action="authenticate",
        headers={"WWW-Authenticate": "Bearer"},
    )


def issue_app_refresh_token(user_id: UUID | str) -> tuple[str, int]:
    validate_auth_configuration()
    validate_app_signing_configuration()
    now = int(datetime.now(UTC).timestamp())
    ttl = settings.REFRESH_TOKEN_TTL_SECONDS
    claims = {
        "sub": str(UUID(str(user_id))),
        "iat": now,
        "exp": now + ttl,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "token_type": "refresh",
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm="HS256"), ttl


def issue_app_access_token(user_id: UUID | str) -> TokenBundle:

    validate_auth_configuration()
    validate_app_signing_configuration()
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "sub": str(UUID(str(user_id))),
        "iat": now,
        "exp": now + settings.ACCESS_TOKEN_TTL_SECONDS,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "token_type": "access",
    }
    refresh_token, refresh_expires_in = issue_app_refresh_token(user_id)
    return TokenBundle(
        access_token=jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm="HS256"),
        expires_in=settings.ACCESS_TOKEN_TTL_SECONDS,
        refresh_token=refresh_token,
        refresh_expires_in=refresh_expires_in,
    )


def _decode_and_validate_jwt_claims(token: str, *, expected_token_type: str) -> dict:
    validate_auth_configuration()
    validate_app_signing_configuration()
    claims = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=["HS256"],
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        options={
            "require_sub": True,
            "require_exp": True,
            "require_iat": True,
            "require_iss": True,
            "require_aud": True,
            "leeway": settings.AUTH_CLOCK_SKEW_SECONDS,
        },
    )
    now = int(datetime.now(UTC).timestamp())
    if (
        type(claims["exp"]) is not int
        or type(claims["iat"]) is not int
        or claims["token_type"] != expected_token_type
        or claims["aud"] != settings.JWT_AUDIENCE
        or claims["iat"] > now + settings.AUTH_CLOCK_SKEW_SECONDS
        or claims["exp"] <= now - settings.AUTH_CLOCK_SKEW_SECONDS
        or claims["exp"] <= claims["iat"]
    ):
        raise ValueError(f"invalid {expected_token_type} claims")
    return claims


def decode_app_access_token(token: str) -> AppAccessClaims:

    try:
        claims = _decode_and_validate_jwt_claims(token, expected_token_type="access")
        return AppAccessClaims(user_id=UUID(claims["sub"]))
    except (JWTError, ValueError, TypeError, KeyError):
        raise unauthenticated() from None


def decode_app_refresh_token(token: str) -> AppRefreshClaims:

    try:
        claims = _decode_and_validate_jwt_claims(token, expected_token_type="refresh")
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti.strip():
            raise ValueError("missing refresh jti")
        return AppRefreshClaims(user_id=UUID(claims["sub"]), jti=jti)
    except (JWTError, ValueError, TypeError, KeyError):
        raise unauthenticated() from None
