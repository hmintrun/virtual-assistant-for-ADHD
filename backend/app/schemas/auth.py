"""Public authentication DTOs; Google identity and app claims stay internal."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import ApiModel
from app.schemas.user import CurrentUser


class GoogleLoginRequest(ApiModel):
    id_token: str = Field(min_length=1, max_length=16384, repr=False)

    @field_validator("id_token")
    @classmethod
    def nonblank_token(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("ID token must not be blank or padded")
        return value


class RefreshTokenRequest(ApiModel):
    refresh_token: str = Field(min_length=1, max_length=16384, repr=False)

    @field_validator("refresh_token")
    @classmethod
    def nonblank_refresh_token(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Refresh token must not be blank or padded")
        return value


class TokenBundle(ApiModel):
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str | None = Field(default=None, repr=False)
    refresh_expires_in: int | None = None


class GoogleLoginData(TokenBundle):
    user: CurrentUser


class LogoutData(ApiModel):
    logged_out: Literal[True] = True
    revoked: bool


class AppAccessClaims(ApiModel):
    user_id: UUID


class AppRefreshClaims(ApiModel):
    user_id: UUID
    jti: str
    issued_at: datetime
    expires_at: datetime


class VerifiedGoogleIdentity(ApiModel):
    sub: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=100)
