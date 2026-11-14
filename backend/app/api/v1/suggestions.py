"""API router for UF7 adaptation suggestions."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, service_handler, success
from app.schemas.common import ErrorEnvelope, SuccessEnvelope
from app.schemas.suggestions import (
    AdaptationSuggestionData,
    SuggestionKind,
    SuggestionResponseData,
    SuggestionResponseRequest,
)

router = APIRouter(tags=["UF7 - Adaptation Suggestions"])
errors: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 404, 409, 422, 503)
}


@router.get(
    "/suggestions/current",
    response_model=SuccessEnvelope[list[AdaptationSuggestionData]],
    responses=errors,
    summary="List Adaptation Suggestions",
)
async def get_current_suggestions(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    kind: Annotated[SuggestionKind | None, Query()] = None,
) -> SuccessEnvelope[list[AdaptationSuggestionData]]:
    data = await service_handler("adaptation_service", "list_current_suggestions")(
        db=db, user_id=current_user.user_id, kind=kind
    )
    return success(request, data)


@router.post(
    "/suggestions/{suggestion_id}/response",
    response_model=SuccessEnvelope[SuggestionResponseData],
    responses=errors,
    summary="Respond to Suggestion",
)
async def respond_to_suggestion(
    request: Request,
    suggestion_id: Annotated[str, Path(min_length=1)],
    payload: SuggestionResponseRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[SuggestionResponseData]:
    data = await service_handler("adaptation_service", "respond_to_suggestion")(
        db=db,
        user_id=current_user.user_id,
        suggestion_id=suggestion_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)
