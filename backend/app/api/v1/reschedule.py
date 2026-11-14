"""API router for UF6 reschedule proposals."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, service_handler, success
from app.schemas.common import ErrorEnvelope, SuccessEnvelope
from app.schemas.planning import ScheduleVersion
from app.schemas.reschedule import (
    ApproveProposalRequest,
    CreateProposalRequest,
    RejectProposalRequest,
    RebuildProposalRequest,
    RescheduleProposalData,
)

router = APIRouter(tags=["UF6 - Reschedule Proposals"])
errors: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 404, 409, 422, 503)
}


@router.post(
    "/proposals",
    status_code=201,
    response_model=SuccessEnvelope[RescheduleProposalData],
    responses=errors,
    summary="Create Reschedule Proposal",
)
async def create_proposal(
    request: Request,
    payload: CreateProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[RescheduleProposalData]:
    data = await service_handler("reschedule_service", "create_proposal")(
        db=db,
        user_id=current_user.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.get(
    "/proposals/current",
    response_model=SuccessEnvelope[RescheduleProposalData | None],
    responses=errors,
    summary="Get Current Proposal",
)
async def get_current_proposal(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[RescheduleProposalData | None]:
    data = await service_handler("reschedule_service", "get_current_proposal")(
        db=db, user_id=current_user.user_id
    )
    return success(request, data)


@router.get(
    "/proposals/{proposal_id}",
    response_model=SuccessEnvelope[RescheduleProposalData],
    responses=errors,
    summary="Get Proposal",
)
async def get_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[RescheduleProposalData]:
    data = await service_handler("reschedule_service", "get_proposal")(
        db=db, user_id=current_user.user_id, proposal_id=proposal_id
    )
    return success(request, data)


@router.post(
    "/proposals/{proposal_id}/rebuild",
    response_model=SuccessEnvelope[RescheduleProposalData],
    responses=errors,
    summary="Rebuild Proposal",
)
async def rebuild_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    payload: RebuildProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[RescheduleProposalData]:
    data = await service_handler("reschedule_service", "rebuild_proposal")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=SuccessEnvelope[ScheduleVersion],
    responses=errors,
    summary="Approve Proposal",
)
async def approve_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    payload: ApproveProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ScheduleVersion]:
    data = await service_handler("reschedule_service", "approve_proposal")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=SuccessEnvelope[RescheduleProposalData],
    responses=errors,
    summary="Reject Proposal",
)
async def reject_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    payload: RejectProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[RescheduleProposalData]:
    data = await service_handler("reschedule_service", "reject_proposal")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)
