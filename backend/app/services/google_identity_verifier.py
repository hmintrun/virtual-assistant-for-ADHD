"""Google boundary: fixed certificate URL, bounded I/O and Cache-Control expiry."""

import asyncio
import re
import time
from typing import Any

import httpx
from google.auth import jwt
from google.auth.exceptions import GoogleAuthError
from pydantic import ValidationError

from app.core.config import settings
from app.core.errors import AppError
from app.schemas.auth import VerifiedGoogleIdentity

CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
_certs: dict[str, str] = {}
_certs_expiry = 0.0
_last_force_refresh_at: float | None = None
FORCE_REFRESH_COOLDOWN_SECONDS = 60.0


def invalid_google_token() -> AppError:
    return AppError(
        status_code=401,
        code="INVALID_GOOGLE_ID_TOKEN",
        message="Không xác minh được đăng nhập Google.",
        next_action="authenticate",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _get_certificates(*, force_refresh: bool = False) -> dict[str, str]:
    global _certs, _certs_expiry, _last_force_refresh_at
    now = time.monotonic()
    if not force_refresh:
        if _certs and now < _certs_expiry:
            return _certs
    else:
        if (
            _certs
            and _last_force_refresh_at is not None
            and (now - _last_force_refresh_at) < FORCE_REFRESH_COOLDOWN_SECONDS
        ):
            return _certs
        _last_force_refresh_at = now
    try:
        async with httpx.AsyncClient(timeout=settings.GOOGLE_AUTH_TIMEOUT_SECONDS) as client:
            response = await client.get(CERTS_URL)
            response.raise_for_status()
        certs = response.json()
        if (
            not isinstance(certs, dict)
            or not certs
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in certs.items())
        ):
            raise ValueError("invalid certificate response")
        max_age = re.search(r"(?:^|,)\s*max-age=(\d+)", response.headers.get("cache-control", ""))
        ttl = min(int(max_age[1]), 86400) if max_age else 0
        age = max(0, int(response.headers.get("age", "0")))
        _certs, _certs_expiry = certs, time.monotonic() + max(0, ttl - age)
        return certs
    except (httpx.HTTPError, ValueError):
        raise AppError(
            status_code=503,
            code="AUTH_PROVIDER_UNAVAILABLE",
            message="Google tạm thời chưa sẵn sàng.",
            next_action="retry_login",
            retryable=True,
        ) from None


def _verified_profile(claims: dict[str, Any]) -> VerifiedGoogleIdentity:
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise invalid_google_token()
    if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
        raise invalid_google_token()
    email = claims.get("email")
    if (
        claims.get("email_verified") is not True
        or not isinstance(email, str)
        or email != email.strip()
        or email.count("@") != 1
        or any(c.isspace() for c in email)
        or not all(email.split("@"))
    ):
        raise AppError(
            status_code=422,
            code="GOOGLE_PROFILE_INCOMPLETE",
            message="Cần email Google đã xác minh.",
            next_action="review_google_profile",
        )
    name = claims.get("name")
    name = name.strip() if isinstance(name, str) else ""
    try:
        return VerifiedGoogleIdentity(
            sub=claims["sub"], email=email, display_name=(name or email.split("@")[0])[:100]
        )
    except ValidationError:
        raise AppError(
            status_code=422,
            code="GOOGLE_PROFILE_INCOMPLETE",
            message="Hồ sơ Google không hợp lệ.",
            next_action="review_google_profile",
        ) from None


async def verify_google_identity(id_token: str) -> VerifiedGoogleIdentity:
    # No tokeninfo endpoint, unverified client claims, or Calendar credentials.
    if not settings.GOOGLE_SIGN_IN_AUDIENCES:
        raise AppError(
            status_code=503,
            code="AUTH_PROVIDER_UNAVAILABLE",
            message="Chưa cấu hình đăng nhập Google.",
            next_action="retry_login",
            retryable=True,
        )
    try:
        header = jwt.decode_header(id_token)
        if header.get("alg") != "RS256":
            raise invalid_google_token()
        kid = header.get("kid")
    except (ValueError, TypeError, GoogleAuthError):
        raise invalid_google_token() from None
    certs = await _get_certificates()
    try:
        claims = await asyncio.to_thread(
            jwt.decode,
            id_token,
            certs=certs,
            audience=settings.GOOGLE_SIGN_IN_AUDIENCES,
            clock_skew_in_seconds=settings.AUTH_CLOCK_SKEW_SECONDS,
        )
        return _verified_profile(claims)
    except (ValueError, TypeError, GoogleAuthError):
        if not (isinstance(kid, str) and kid in certs):
            try:
                fresh_certs = await _get_certificates(force_refresh=True)
            except TypeError:
                fresh_certs = certs
            if fresh_certs != certs:
                try:
                    claims = await asyncio.to_thread(
                        jwt.decode,
                        id_token,
                        certs=fresh_certs,
                        audience=settings.GOOGLE_SIGN_IN_AUDIENCES,
                        clock_skew_in_seconds=settings.AUTH_CLOCK_SKEW_SECONDS,
                    )
                    return _verified_profile(claims)
                except (ValueError, TypeError, GoogleAuthError):
                    raise invalid_google_token() from None
        raise invalid_google_token() from None
