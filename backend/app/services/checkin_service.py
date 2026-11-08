"""Service điều phối Check-in giữa phiên và cuối phiên (UF5/W05: list_run_checkins, create_run_checkin, respond_to_checkin)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.execution import CheckIn
from app.models.session import Session as SessionRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.repositories import run_repository
from app.schemas.execution import (
    CheckInCreateRequest,
    CheckInData,
    CheckInKind,
    CheckInListResult,
    CheckInResponseRequest,
)
from app.services import command_service, run_service

VALID_ANSWER_OUTCOMES = {"completed", "partial", "incomplete", "busy", "skipped"}


async def list_run_checkins(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    kind: CheckInKind | None = None,
    now: datetime | None = None,
) -> CheckInListResult:
    """Dòng 29 trên API Sheet (GET /api/v1/runs/{run_id}/check-ins - HTTP 200)."""
    await run_service.reconcile_expired_checkins(db, user_id, now=now, commit_if_read=True)
    user_uuid = command_service.owner_uuid(user_id)
    run_uuid = run_service._parse_uuid(run_id)

    run = await run_repository.get_run(db, user_uuid, run_uuid)
    if run is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    stmt = select(CheckIn).where(
        CheckIn.user_id == user_uuid,
        CheckIn.run_id == run_uuid,
    )
    if kind is not None:
        stmt = stmt.where(CheckIn.kind == kind)
    stmt = stmt.order_by(CheckIn.asked_at.asc(), CheckIn.created_at.asc()).execution_options(
        populate_existing=True
    )

    rows = list(await db.scalars(stmt))
    return CheckInListResult(
        items=[run_service._checkin_to_schema(row) for row in rows],
        next_cursor=None,
    )


@command_service.durable_command(
    "create_run_checkin", CheckInData, resource_arg="run_id"
)
async def create_run_checkin(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    payload: CheckInCreateRequest,
) -> CheckInData:
    """Dòng 30 trên API Sheet (POST /api/v1/runs/{run_id}/check-ins - HTTP 201)."""
    user_uuid = command_service.owner_uuid(user_id)
    run_uuid = run_service._parse_uuid(run_id)

    run = await run_repository.get_run(db, user_uuid, run_uuid)
    if run is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    if payload.expected_run_revision != run.revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Dữ liệu đã thay đổi so với phiên bản gửi lên. Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới.",
            current_revisions={"run_revision": run.revision},
            next_action="reload_and_review",
        )

    if payload.asked_at.astimezone(UTC) < run.started_at.astimezone(UTC):
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            "asked_at",
        )

    if payload.kind == "mid":
        if run.run_state not in ("running", "paused"):
            raise AppError(
                status_code=409,
                code="INVALID_RUN_STATE",
                message="Chỉ có thể mở check-in giữa phiên khi phiên đang chạy hoặc tạm dừng.",
                next_action="reload_and_review",
            )
    else:
        existing_final = await run_repository.get_final_checkin(db, user_uuid, run.id)
        if existing_final is not None:
            raise AppError(
                status_code=409,
                code="FINAL_CHECKIN_EXISTS",
                message="Phiên tập trung này đã có bản ghi check-in cuối phiên.",
                next_action="reload_checkin",
            )

    checkin = CheckIn(
        user_id=user_uuid,
        run_id=run.id,
        kind=payload.kind,
        response_state="open",
        session_outcome="unknown",
        step_completed=None,
        task_completed=None,
        skip_scope=None,
        remaining_note=None,
        asked_at=payload.asked_at,
        responded_at=None,
        response_revision=0,
    )
    db.add(checkin)
    await db.flush()
    return run_service._checkin_to_schema(checkin)


@command_service.durable_command(
    "respond_to_checkin", CheckInData, resource_arg="checkin_id"
)
async def respond_to_checkin(
    db: AsyncSession,
    *,
    user_id: str,
    checkin_id: str,
    idempotency_key: str,
    payload: CheckInResponseRequest,
) -> CheckInData:
    """Dòng 31 trên API Sheet (PUT /api/v1/check-ins/{checkin_id}/response - HTTP 200)."""
    user_uuid = command_service.owner_uuid(user_id)
    checkin_uuid = run_service._parse_uuid(checkin_id)

    checkin = await db.scalar(
        select(CheckIn)
        .where(CheckIn.id == checkin_uuid, CheckIn.user_id == user_uuid)
        .execution_options(populate_existing=True)
    )
    if checkin is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    if payload.expected_response_revision != checkin.response_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Check-in đã được sửa trên thiết bị khác. Hãy tải câu trả lời mới trước khi gửi lại.",
            current_revisions={"response_revision": checkin.response_revision},
            next_action="reload_checkin",
        )

    if payload.session_outcome not in VALID_ANSWER_OUTCOMES:
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            "session_outcome",
        )

    if payload.responded_at.astimezone(UTC) < checkin.asked_at.astimezone(UTC):
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            "responded_at",
        )

    run = await run_repository.get_run(db, user_uuid, checkin.run_id)
    if run is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    checkin.response_state = "answered"
    checkin.responded_at = payload.responded_at
    checkin.session_outcome = payload.session_outcome
    checkin.step_completed = payload.step_completed
    checkin.task_completed = payload.task_completed
    checkin.skip_scope = payload.skip_scope
    checkin.remaining_note = payload.remaining_note
    checkin.response_revision += 1
    checkin.updated_at = datetime.now(UTC)

    # TÁCH NHÁNH THEO checkin.kind (D5 & D6):
    # - Nếu kind == 'mid': giữ nguyên SessionRun đang chạy, không đóng Run, không đụng checkin_timeout_at/dnd_release_at.
    # - Nếu kind == 'final': cập nhật run.outcome, đóng Run (run_state='closed'), rút ngắn dnd_release_at nếu trả lời trước hạn 300s.
    if checkin.kind == "final":
        run.outcome = payload.session_outcome
        run.run_state = "closed"
        resp_utc = payload.responded_at.astimezone(UTC)
        if run.checkin_timeout_at is not None and resp_utc < run.checkin_timeout_at.astimezone(UTC):
            if run.dnd_release_at is not None:
                run.dnd_release_at = min(run.dnd_release_at.astimezone(UTC), resp_utc)
            else:
                run.dnd_release_at = payload.responded_at
        run.updated_at = datetime.now(UTC)

        session = await db.scalar(
            select(SessionRow).where(
                SessionRow.id == run.session_id,
                SessionRow.user_id == user_uuid,
            )
        )
        if session is not None:
            if payload.session_outcome == "completed":
                session.status = "completed"
            elif payload.session_outcome in ("partial", "incomplete", "busy"):
                session.status = "incomplete"
            elif payload.session_outcome == "skipped":
                session.status = "skipped"

            step = await db.scalar(
                select(StepRow).where(
                    StepRow.id == session.step_id,
                    StepRow.user_id == user_uuid,
                )
            )
            if step is not None:
                if payload.step_completed is True:
                    step.status = "completed"
                    step.remaining_base_seconds = 0
                elif payload.session_outcome == "skipped" and payload.skip_scope == "skip_step":
                    step.status = "skipped"
                    step.remaining_base_seconds = 0

                if payload.task_completed is True:
                    task = await db.scalar(
                        select(TaskRow).where(
                            TaskRow.id == step.task_id,
                            TaskRow.user_id == user_uuid,
                        )
                    )
                    if task is not None and not task.unexpanded_scope:
                        task.status = "completed"

    await db.flush()
    return run_service._checkin_to_schema(checkin)
