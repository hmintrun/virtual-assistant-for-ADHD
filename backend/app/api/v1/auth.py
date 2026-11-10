"""Google exchange is public; me and business APIs accept app Bearer tokens."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, DbSessionDep
from app.schemas.auth import GoogleLoginData, GoogleLoginRequest, VerifiedGoogleIdentity
from app.schemas.common import ErrorEnvelope, SuccessEnvelope, build_response_meta
from app.schemas.user import CurrentUser
from app.services.auth_service import login_with_google
from app.services.google_identity_verifier import verify_google_identity

router = APIRouter(prefix="/auth", tags=["Authentication"])
errors: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 409, 422, 503)
}


def get_google_verifier():
    return verify_google_identity


@router.post("/google", response_model=SuccessEnvelope[GoogleLoginData], responses=errors)
async def google_login(
    payload: GoogleLoginRequest,
    db: DbSessionDep,
    verifier: Annotated[
        Callable[[str], Awaitable[VerifiedGoogleIdentity]], Depends(get_google_verifier)
    ],
):
    data = await login_with_google(db, payload.id_token, verifier=verifier)
    return SuccessEnvelope(data=data, meta=build_response_meta())


@router.get("/me", response_model=SuccessEnvelope[CurrentUser], responses=errors)
async def get_me(user: CurrentUserDep):
    return SuccessEnvelope(data=user, meta=build_response_meta())
