"""Router cho các API Điều chỉnh Lịch & Đề xuất (Reschedule Proposals - UF6)."""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, service_handler, success
from app.schemas.common import ErrorEnvelope, SuccessEnvelope
from app.schemas.reschedule import (
    ApproveProposalData,
    ApproveProposalRequest,
    CreateProposalRequest,
    ProposalData,
    RebuildProposalRequest,
    RejectProposalData,
    RejectProposalRequest,
)

router = APIRouter(tags=["Reschedule Proposals - UF6"])


def _error_responses(*, has_conflict: bool = True, has_validation: bool = True) -> dict[int | str, dict[str, object]]:
    responses: dict[int | str, dict[str, object]] = {
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập hoặc thiếu Bearer Token"},
        404: {"model": ErrorEnvelope, "description": "Không tìm thấy tài nguyên hoặc proposal"},
        503: {"model": ErrorEnvelope, "description": "Dịch vụ điều chỉnh lịch chưa sẵn sàng"},
    }
    if has_conflict:
        responses[409] = {
            "model": ErrorEnvelope,
            "description": "Xung đột revision, nguồn lịch đổi, proposal stale hoặc Idempotency-Key",
        }
    if has_validation:
        responses[422] = {
            "model": ErrorEnvelope,
            "description": "Dữ liệu yêu cầu không hợp lệ hoặc ngoài phạm vi cho phép",
        }
    return responses


@router.post(
    "/proposals",
    status_code=201,
    response_model=SuccessEnvelope[ProposalData],
    responses=_error_responses(),
    summary="Tạo đề xuất điều chỉnh lịch hoặc xin làm tiếp (Dòng 33)",
)
async def create_proposal(
    request: Request,
    payload: CreateProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ProposalData]:
    data = await service_handler("reschedule_service", "create_proposal")(
        db=db,
        user_id=current_user.user_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return success(request, data)


@router.get(
    "/proposals/current",
    status_code=200,
    response_model=SuccessEnvelope[ProposalData | None],
    responses=_error_responses(has_conflict=False, has_validation=False),
    summary="Đọc đề xuất điều chỉnh lịch hiện hành hoặc null (Dòng 34)",
)
async def get_current_proposal(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[ProposalData | None]:
    data = await service_handler("reschedule_service", "get_current_proposal")(
        db=db,
        user_id=current_user.user_id,
    )
    return success(request, data)


@router.get(
    "/proposals/{proposal_id}",
    status_code=200,
    response_model=SuccessEnvelope[ProposalData],
    responses=_error_responses(has_conflict=False, has_validation=False),
    summary="Xem chi tiết đề xuất điều chỉnh lịch theo ID (Dòng 35)",
)
async def get_proposal_detail(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[ProposalData]:
    data = await service_handler("reschedule_service", "get_proposal_detail")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
    )
    return success(request, data)


@router.post(
    "/proposals/{proposal_id}/rebuild",
    status_code=201,
    response_model=SuccessEnvelope[ProposalData],
    responses=_error_responses(),
    summary="Tính lại đề xuất điều chỉnh lịch mới thay thế bản cũ (Dòng 36)",
)
async def rebuild_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    payload: RebuildProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ProposalData]:
    data = await service_handler("reschedule_service", "rebuild_proposal")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return success(request, data)


@router.post(
    "/proposals/{proposal_id}/approve",
    status_code=200,
    response_model=SuccessEnvelope[ApproveProposalData],
    responses=_error_responses(),
    summary="Duyệt đề xuất điều chỉnh lịch hoặc tiếp tục phiên (Dòng 37)",
)
async def approve_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    payload: ApproveProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ApproveProposalData]:
    data = await service_handler("reschedule_service", "approve_proposal")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return success(request, data)


@router.post(
    "/proposals/{proposal_id}/reject",
    status_code=200,
    response_model=SuccessEnvelope[RejectProposalData],
    responses=_error_responses(),
    summary="Từ chối đề xuất điều chỉnh lịch và giữ nguyên lịch hiện hành (Dòng 38)",
)
async def reject_proposal(
    request: Request,
    proposal_id: Annotated[str, Path(min_length=1)],
    payload: RejectProposalRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[RejectProposalData]:
    data = await service_handler("reschedule_service", "reject_proposal")(
        db=db,
        user_id=current_user.user_id,
        proposal_id=proposal_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return success(request, data)
