"""Router UF3: wire contract và điểm nối run_service/device_service của W03."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import AwareDatetime

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, service_handler, success
from app.core.errors import AppError
from app.schemas.common import SuccessEnvelope
from app.schemas.execution import (
    ActualCorrectionData,
    ActualCorrectionRequest,
    DeviceStateData,
    DeviceStateReportRequest,
    ReminderData,
    ReminderListResult,
    ReminderReceiptRequest,
    RunEndRequest,
    RunEventBatchRequest,
    RunEventBatchResult,
    SessionRunData,
    SessionStartRequest,
)

router = APIRouter(tags=["Focus & Devices - UF3"])


@router.post(
    "/sessions/{session_id}/start", status_code=201, response_model=SuccessEnvelope[SessionRunData]
)
async def start_session_run(
    request: Request,
    session_id: Annotated[str, Path(min_length=1)],
    payload: SessionStartRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[SessionRunData]:
    data = await service_handler("run_service", "start_session_run")(
        db=db,
        user_id=current_user.user_id,
        session_id=session_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.get("/runs/active", status_code=200, response_model=SuccessEnvelope[SessionRunData | None])
async def get_active_run(
    request: Request, current_user: CurrentUserDep, db: DbSessionDep
) -> SuccessEnvelope[SessionRunData | None]:
    data = await service_handler("run_service", "get_active_run")(
        db=db, user_id=current_user.user_id
    )
    return success(request, data)


@router.get("/runs/{run_id}", status_code=200, response_model=SuccessEnvelope[SessionRunData])
async def get_run_detail(
    request: Request,
    run_id: Annotated[str, Path(min_length=1)],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[SessionRunData]:
    data = await service_handler("run_service", "get_run_detail")(
        db=db, user_id=current_user.user_id, run_id=run_id
    )
    return success(request, data)


@router.post(
    "/runs/{run_id}/events", status_code=200, response_model=SuccessEnvelope[RunEventBatchResult]
)
async def post_run_events(
    request: Request,
    run_id: Annotated[str, Path(min_length=1)],
    payload: RunEventBatchRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[RunEventBatchResult]:
    data = await service_handler("run_service", "record_run_events")(
        db=db,
        user_id=current_user.user_id,
        run_id=run_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.post("/runs/{run_id}/end", status_code=200, response_model=SuccessEnvelope[SessionRunData])
async def end_session_run(
    request: Request,
    run_id: Annotated[str, Path(min_length=1)],
    payload: RunEndRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[SessionRunData]:
    data = await service_handler("run_service", "end_session_run")(
        db=db,
        user_id=current_user.user_id,
        run_id=run_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.post(
    "/runs/{run_id}/actual-corrections",
    status_code=200,
    response_model=SuccessEnvelope[ActualCorrectionData],
)
async def post_actual_correction(
    request: Request,
    run_id: Annotated[str, Path(min_length=1)],
    payload: ActualCorrectionRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ActualCorrectionData]:
    data = await service_handler("run_service", "record_actual_correction")(
        db=db,
        user_id=current_user.user_id,
        run_id=run_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.put(
    "/devices/{device_id}/state", status_code=200, response_model=SuccessEnvelope[DeviceStateData]
)
async def report_device_state(
    request: Request,
    device_id: Annotated[str, Path(min_length=1)],
    payload: DeviceStateReportRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[DeviceStateData]:
    data = await service_handler("device_service", "save_device_state_report")(
        db=db,
        user_id=current_user.user_id,
        device_id=device_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)


@router.get("/reminders", response_model=SuccessEnvelope[ReminderListResult])
async def get_reminders(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    device_id: Annotated[str, Query(min_length=1)],
    from_time: Annotated[AwareDatetime, Query(alias="from")],
    to_time: Annotated[AwareDatetime, Query(alias="to")],
    schedule_revision: Annotated[int | None, Query(ge=0)] = None,
) -> SuccessEnvelope[ReminderListResult]:
    if from_time >= to_time:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Thời điểm bắt đầu phải trước thời điểm kết thúc.",
            next_action="fix_fields",
        )
    data = await service_handler("device_service", "list_reminders")(
        db=db,
        user_id=current_user.user_id,
        device_id=device_id,
        from_time=from_time,
        to_time=to_time,
        schedule_revision=schedule_revision,
    )
    return success(request, data)


@router.post(
    "/reminders/{reminder_id}/receipt",
    status_code=200,
    response_model=SuccessEnvelope[ReminderData],
)
async def report_reminder_receipt(
    request: Request,
    reminder_id: Annotated[str, Path(min_length=1)],
    payload: ReminderReceiptRequest,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[ReminderData]:
    data = await service_handler("device_service", "record_reminder_receipt")(
        db=db,
        user_id=current_user.user_id,
        reminder_id=reminder_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return success(request, data)
