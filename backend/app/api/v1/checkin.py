"""Router CheckIn tối thiểu, dùng interface checkin_service đã chốt."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, service_handler, success
from app.schemas.common import SuccessEnvelope
from app.schemas.execution import (
    CheckInCreateRequest,
    CheckInData,
    CheckInKind,
    CheckInListResult,
    CheckInResponseRequest,
)

router = APIRouter(tags=["CheckIn - UF5"])


@router.get(
    "/runs/{run_id}/check-ins",
    response_model=SuccessEnvelope[CheckInListResult],
    summary="List Check-Ins",
)
async def get_run_checkins(
    request: Request,
    run_id: Annotated[str, Path(min_length=1)],
    current_user: CurrentUserDep,
    db: DbSessionDep,
    kind: Annotated[CheckInKind | None, Query()] = None,
) -> SuccessEnvelope[CheckInListResult]:
    data = await service_handler("checkin_service", "list_run_checkins")(
        db=db, user_id=current_user.user_id, run_id=run_id, kind=kind
    )
    return success(request, data)


@router.post(
    "/runs/{run_id}/check-ins",
    status_code=201,
    response_model=SuccessEnvelope[CheckInData],
    summary="Create Check-In",
)
async def create_run_checkin(
    request: Request,
    run_id: Annotated[str, Path(min_length=1)],
    payload: CheckInCreateRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[CheckInData]:
    data = await service_handler("checkin_service", "create_run_checkin")(
        db=db,
        user_id=current_user.user_id,
        run_id=run_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.put(
    "/check-ins/{checkin_id}/response",
    status_code=200,
    response_model=SuccessEnvelope[CheckInData],
    summary="Respond to Check-In",
)
async def submit_checkin_response(
    request: Request,
    checkin_id: Annotated[str, Path(min_length=1)],
    payload: CheckInResponseRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[CheckInData]:
    data = await service_handler("checkin_service", "respond_to_checkin")(
        db=db,
        user_id=current_user.user_id,
        checkin_id=checkin_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)
