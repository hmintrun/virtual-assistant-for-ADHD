"""Router Thought: metadata UF4, xóa và chuyển nháp theo hợp đồng."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, service_handler, success
from app.schemas.common import SuccessEnvelope
from app.schemas.thought import (
    ThoughtCreateRequest,
    ThoughtData,
    ThoughtDeleteRequest,
    ThoughtDeleteResult,
    ThoughtListResult,
    ThoughtToDraftRequest,
    ThoughtToDraftResult,
)

router = APIRouter(prefix="/thoughts", tags=["Thoughts - UF4"])


@router.post("", status_code=201, response_model=SuccessEnvelope[ThoughtData])
async def create_thought(
    request: Request,
    payload: ThoughtCreateRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ThoughtData]:
    data = await service_handler("thought_service", "capture_thought")(
        db=db, user_id=current_user.user_id, payload=payload, idempotency_key=idempotency_key
    )
    return success(request, data)


@router.get("", response_model=SuccessEnvelope[ThoughtListResult])
async def list_thoughts(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> SuccessEnvelope[ThoughtListResult]:
    data = await service_handler("thought_service", "list_thoughts")(
        db=db, user_id=current_user.user_id, limit=limit, cursor=cursor
    )
    return success(request, data)


@router.get("/{thought_id}", status_code=200, response_model=SuccessEnvelope[ThoughtData])
async def get_thought(
    request: Request,
    thought_id: Annotated[str, Path(min_length=1)],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[ThoughtData]:
    data = await service_handler("thought_service", "get_thought_detail")(
        db=db, user_id=current_user.user_id, thought_id=thought_id
    )
    return success(request, data)


@router.delete(
    "/{thought_id}", status_code=200, response_model=SuccessEnvelope[ThoughtDeleteResult]
)
async def delete_thought(
    request: Request,
    thought_id: Annotated[str, Path(min_length=1)],
    payload: ThoughtDeleteRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ThoughtDeleteResult]:
    data = await service_handler("thought_service", "delete_thought")(
        db=db,
        user_id=current_user.user_id,
        thought_id=thought_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.post(
    "/{thought_id}/draft", status_code=201, response_model=SuccessEnvelope[ThoughtToDraftResult]
)
async def convert_thought_to_draft(
    request: Request,
    thought_id: Annotated[str, Path(min_length=1)],
    payload: ThoughtToDraftRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ThoughtToDraftResult]:
    data = await service_handler("thought_service", "convert_thought_to_draft")(
        db=db,
        user_id=current_user.user_id,
        thought_id=thought_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)
