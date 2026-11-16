"""Support endpoints for persisted planning contexts and command receipts."""

from typing import Annotated

from fastapi import APIRouter, Header, Path, Request
from sqlalchemy import text

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.config import settings
from app.core.errors import AppError
from app.schemas.calendar import CalendarContext, PlanningContextCreateRequest
from app.schemas.common import (
    CapabilitiesData,
    CommandResult,
    ConfiguredParameters,
    SuccessEnvelope,
    build_response_meta,
)
from app.services import plan_service

router = APIRouter(tags=["System - Capabilities, Contexts & Commands"])

_DEFAULT_CAPABILITIES = CapabilitiesData(
    contract_version="v1",
    policy_revision="demo-policy",
    offline_start_supported=False,
    configured_parameters=ConfiguredParameters(
        buffer_multiplier_range=[1.0, 2.0],
        minimum_sample_count=7,
        audio_max_seconds=120,
        audio_max_bytes=int(settings.VOICE_MAX_AUDIO_BYTES),
        preview_ttl_seconds=900,
        default_reminder_offset_seconds=900,
        default_reminder_offsets=[900, 600, 300],
        start_grace_seconds=900,
        min_inter_session_gap_seconds=900,
        checkin_timeout_seconds=300,
    ),
    policy_state="ready",
)


@router.get("/health", summary="Kiểm tra trạng thái hoạt động Backend & Database")
async def health_check(db: DbSessionDep) -> dict[str, str]:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        raise AppError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Cơ sở dữ liệu hiện không sẵn sàng.",
            retryable=True,
            next_action="retry_later",
        ) from None
    return {"status": "ok", "database": "ok"}


@router.get("/capabilities", response_model=SuccessEnvelope[CapabilitiesData])
async def get_capabilities(
    request: Request, current_user: CurrentUserDep
) -> SuccessEnvelope[CapabilitiesData]:
    return SuccessEnvelope(
        data=_DEFAULT_CAPABILITIES,
        meta=build_response_meta(getattr(request.state, "request_id", None)),
    )


@router.post("/planning-contexts", status_code=201, response_model=SuccessEnvelope[CalendarContext])
async def create_planning_context(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    payload: PlanningContextCreateRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> SuccessEnvelope[CalendarContext]:
    context = await plan_service.create_planning_context(
        user_id=current_user.user_id, db=db, payload=payload, idempotency_key=idempotency_key
    )
    return SuccessEnvelope(
        data=context, meta=build_response_meta(getattr(request.state, "request_id", None))
    )


@router.get("/planning-contexts/{context_id}", response_model=SuccessEnvelope[CalendarContext])
async def get_planning_context(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    context_id: Annotated[str, Path(min_length=1)],
) -> SuccessEnvelope[CalendarContext]:
    context = await plan_service.get_planning_context(
        user_id=current_user.user_id, context_id=context_id, db=db
    )
    return SuccessEnvelope(
        data=context, meta=build_response_meta(getattr(request.state, "request_id", None))
    )


@router.get("/commands/{command_id}", response_model=SuccessEnvelope[CommandResult])
async def get_command_result(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    command_id: Annotated[str, Path(min_length=1)],
) -> SuccessEnvelope[CommandResult]:
    result = await plan_service.get_command_result(
        user_id=current_user.user_id, command_id=command_id, db=db
    )
    return SuccessEnvelope(
        data=result, meta=build_response_meta(getattr(request.state, "request_id", None))
    )
