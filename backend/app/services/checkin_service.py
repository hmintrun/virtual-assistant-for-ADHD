"""Service điều phối Check-in giữa phiên và cuối phiên (UF5/W05: list_run_checkins, create_run_checkin, respond_to_checkin)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.execution import CheckIn, SessionRun
from app.models.session import Session as SessionRow
from app.models.step import Step as StepRow
from app.models.system import CommandReceipt
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
    now: datetime | None = None,
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

    ref_now = run_service._current_utc_now(now)
    asked_at_utc = payload.asked_at.astimezone(UTC)
    if asked_at_utc < run.started_at.astimezone(UTC):
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            "asked_at",
        )
    if asked_at_utc > ref_now + timedelta(seconds=300):
        raise run_service._validation_error(
            "Thời điểm check-in không được vượt quá thời gian hiện tại quá 5 phút.",
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
        if run.ended_at is None or run.run_state not in ("awaiting_checkin", "closed"):
            raise AppError(
                status_code=409,
                code="INVALID_RUN_STATE",
                message="Chỉ có thể mở check-in cuối phiên sau khi phiên tập trung đã kết thúc.",
                next_action="reload_and_review",
            )
        if asked_at_utc < run.ended_at.astimezone(UTC):
            raise run_service._validation_error(
                "Thời điểm check-in cuối phiên phải từ khi kết thúc phiên trở đi.",
                "asked_at",
            )
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


def _validate_completion_flags(payload: CheckInResponseRequest) -> None:
    """Kiểm tra tổ hợp cờ hoàn thành mâu thuẫn trước khi ghi (R01)."""
    if payload.session_outcome != "completed" and (
        payload.step_completed is True or payload.task_completed is True
    ):
        bad_field = "step_completed" if payload.step_completed is True else "task_completed"
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            bad_field,
        )
    if payload.task_completed is True and payload.step_completed is False:
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            "task_completed",
        )


async def _load_prior_projection_baseline(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    checkin_id: UUID,
    session: SessionRow,
    step: StepRow | None,
    task: TaskRow | None,
) -> dict[str, Any]:
    """Lấy hoặc khởi tạo snapshot trạng thái gốc trước khi CheckIn này áp projection."""
    prior_receipts = list(
        await db.scalars(
            select(CommandReceipt)
            .where(
                CommandReceipt.user_id == user_uuid,
                CommandReceipt.command_type == "respond_to_checkin",
            )
            .order_by(CommandReceipt.applied_at.desc(), CommandReceipt.created_at.desc())
        )
    )

    for receipt in prior_receipts:
        refs = receipt.result_reference or {}
        proj = refs.get("_projection")
        if refs.get("checkin_id") == str(checkin_id) and isinstance(proj, dict):
            saved_baseline = proj.get("baseline")
            if isinstance(saved_baseline, dict):
                return dict(saved_baseline)

    baseline_step_status = step.status if step is not None else None
    baseline_step_remaining = step.remaining_base_seconds if step is not None else None
    if step is not None and (
        step.status in ("completed", "skipped") or step.remaining_base_seconds == 0
    ):
        for receipt in prior_receipts:
            proj = (receipt.result_reference or {}).get("_projection")
            if not isinstance(proj, dict):
                continue
            saved_baseline = proj.get("baseline")
            if isinstance(saved_baseline, dict) and saved_baseline.get("step_id") == str(step.id):
                rem = saved_baseline.get("step_remaining_base_seconds")
                if isinstance(rem, int) and rem > 0:
                    baseline_step_remaining = rem
                st_status = saved_baseline.get("step_status")
                if isinstance(st_status, str) and st_status not in ("completed", "skipped"):
                    baseline_step_status = st_status
                break

    baseline_task_status = task.status if task is not None else None
    if task is not None and task.status == "completed":
        for receipt in prior_receipts:
            proj = (receipt.result_reference or {}).get("_projection")
            if not isinstance(proj, dict):
                continue
            saved_baseline = proj.get("baseline")
            if isinstance(saved_baseline, dict) and saved_baseline.get("task_id") == str(task.id):
                t_status = saved_baseline.get("task_status")
                if isinstance(t_status, str) and t_status != "completed":
                    baseline_task_status = t_status
                break

    return {
        "session_id": str(session.id),
        "session_status": session.status,
        "step_id": str(step.id) if step is not None else None,
        "step_status": baseline_step_status,
        "step_remaining_base_seconds": baseline_step_remaining,
        "task_id": str(task.id) if task is not None else None,
        "task_status": baseline_task_status,
    }


async def _apply_final_checkin_projection(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    run: SessionRun,
    checkin: CheckIn,
    payload: CheckInResponseRequest,
) -> None:
    """Áp dụng hoặc hiệu chỉnh (correction) projection Session -> Step -> Task cho final CheckIn (R01, R02)."""
    session = await db.scalar(
        select(SessionRow)
        .where(
            SessionRow.id == run.session_id,
            SessionRow.user_id == user_uuid,
        )
        .execution_options(populate_existing=True)
    )
    if session is None:
        return

    step = await db.scalar(
        select(StepRow)
        .where(
            StepRow.id == session.step_id,
            StepRow.user_id == user_uuid,
        )
        .execution_options(populate_existing=True)
    )
    task = (
        await db.scalar(
            select(TaskRow)
            .where(
                TaskRow.id == step.task_id,
                TaskRow.user_id == user_uuid,
            )
            .execution_options(populate_existing=True)
        )
        if step is not None
        else None
    )

    baseline = await _load_prior_projection_baseline(
        db,
        user_uuid=user_uuid,
        checkin_id=checkin.id,
        session=session,
        step=step,
        task=task,
    )
    before_state = {
        "session_status": session.status,
        "step_status": step.status if step is not None else None,
        "step_remaining_base_seconds": step.remaining_base_seconds if step is not None else None,
        "task_status": task.status if task is not None else None,
    }

    if payload.session_outcome == "completed":
        session.status = "completed"
    elif payload.session_outcome in ("partial", "incomplete", "busy"):
        session.status = "incomplete"
    elif payload.session_outcome == "skipped":
        session.status = "skipped"
    await db.flush()

    if step is not None:
        step_sessions = list(
            await db.scalars(
                select(SessionRow)
                .where(
                    SessionRow.user_id == user_uuid,
                    SessionRow.step_id == step.id,
                    SessionRow.status != "cancelled",
                )
                .execution_options(populate_existing=True)
            )
        )
        all_step_sessions_completed = bool(step_sessions) and all(
            s.status == "completed" for s in step_sessions
        )

        other_step_rows = list(
            await db.execute(
                select(CheckIn, SessionRun)
                .join(SessionRun, CheckIn.run_id == SessionRun.id)
                .join(SessionRow, SessionRun.session_id == SessionRow.id)
                .where(
                    CheckIn.user_id == user_uuid,
                    CheckIn.id != checkin.id,
                    CheckIn.kind == "final",
                    CheckIn.response_state == "answered",
                    SessionRun.user_id == user_uuid,
                    SessionRun.ended_at.is_not(None),
                    SessionRow.user_id == user_uuid,
                    SessionRow.step_id == step.id,
                )
                .order_by(SessionRun.ended_at.desc(), CheckIn.responded_at.desc())
            )
        )
        this_order = (run.ended_at or run.started_at or checkin.asked_at).astimezone(UTC)

        subsequent_step_checkins = [
            (ci, r)
            for ci, r in other_step_rows
            if (r.ended_at or r.started_at or ci.asked_at).astimezone(UTC) > this_order
        ]
        other_step_wins_completed = False
        other_step_wins_skipped = False
        other_step_wins_deny = False
        if subsequent_step_checkins:
            latest_ci, _ = subsequent_step_checkins[0]
            if latest_ci.session_outcome == "completed" and (
                latest_ci.step_completed is True or latest_ci.task_completed is True
            ):
                other_step_wins_completed = True
            elif (
                latest_ci.session_outcome == "skipped"
                and latest_ci.skip_scope == "skip_step"
            ):
                other_step_wins_skipped = True
            elif (
                latest_ci.step_completed is False
                or latest_ci.task_completed is False
                or latest_ci.session_outcome != "completed"
            ):
                other_step_wins_deny = True

        this_explicit_step_deny = (payload.step_completed is False) or (
            payload.session_outcome != "completed"
        )
        this_explicit_step_complete = payload.session_outcome == "completed" and (
            payload.step_completed is True or payload.task_completed is True
        )
        this_auto_step_complete = (
            (payload.session_outcome == "completed")
            and (payload.step_completed is None)
            and (payload.task_completed is not False)
            and all_step_sessions_completed
        )
        this_explicit_step_skip = (
            payload.session_outcome == "skipped" and payload.skip_scope == "skip_step"
        )

        if other_step_wins_completed or (
            not other_step_wins_deny
            and not this_explicit_step_deny
            and (this_explicit_step_complete or this_auto_step_complete)
        ):
            step.status = "completed"
            step.remaining_base_seconds = 0
        elif other_step_wins_skipped or this_explicit_step_skip:
            step.status = "skipped"
            step.remaining_base_seconds = 0
        elif step.status in ("completed", "skipped"):
            restored_step_status = baseline.get("step_status")
            if isinstance(restored_step_status, str) and restored_step_status not in (
                "completed",
                "skipped",
            ):
                step.status = restored_step_status
            else:
                step.status = "in_progress"

            restored_rem = baseline.get("step_remaining_base_seconds")
            if isinstance(restored_rem, int) and restored_rem > 0:
                step.remaining_base_seconds = restored_rem
            else:
                closed_runs = list(
                    await db.scalars(
                        select(SessionRun)
                        .join(SessionRow, SessionRun.session_id == SessionRow.id)
                        .where(
                            SessionRun.user_id == user_uuid,
                            SessionRow.user_id == user_uuid,
                            SessionRow.step_id == step.id,
                            SessionRun.ended_at.is_not(None),
                            SessionRun.run_state.in_(("closed", "ended")),
                        )
                    )
                )
                step_actual_work = sum(int(r.actual_work_seconds or 0) for r in closed_runs)
                step.remaining_base_seconds = max(
                    1, int(step.estimated_base_seconds or 0) - step_actual_work
                )
        await db.flush()

        if task is not None:
            task_steps = list(
                await db.scalars(
                    select(StepRow)
                    .where(
                        StepRow.user_id == user_uuid,
                        StepRow.task_id == task.id,
                    )
                    .execution_options(populate_existing=True)
                )
            )
            all_task_steps_completed = bool(task_steps) and all(
                st.status == "completed" for st in task_steps
            )
            has_unexpanded_scope = bool(
                task.unexpanded_scope and task.unexpanded_scope.strip()
            )

            other_task_rows = list(
                await db.execute(
                    select(CheckIn, SessionRun)
                    .join(SessionRun, CheckIn.run_id == SessionRun.id)
                    .join(SessionRow, SessionRun.session_id == SessionRow.id)
                    .join(StepRow, SessionRow.step_id == StepRow.id)
                    .where(
                        CheckIn.user_id == user_uuid,
                        CheckIn.id != checkin.id,
                        CheckIn.kind == "final",
                        CheckIn.response_state == "answered",
                        SessionRun.user_id == user_uuid,
                        SessionRun.ended_at.is_not(None),
                        StepRow.user_id == user_uuid,
                        StepRow.task_id == task.id,
                    )
                    .order_by(SessionRun.ended_at.desc(), CheckIn.responded_at.desc())
                )
            )
            subsequent_task_checkins = [
                (ci, r)
                for ci, r in other_task_rows
                if (r.ended_at or r.started_at or ci.asked_at).astimezone(UTC) > this_order
            ]
            other_task_wins_completed = False
            other_task_wins_deny = False
            if subsequent_task_checkins:
                latest_ci_task, _ = subsequent_task_checkins[0]
                if (
                    latest_ci_task.session_outcome == "completed"
                    and latest_ci_task.task_completed is True
                ):
                    other_task_wins_completed = True
                elif (
                    latest_ci_task.task_completed is False
                    or latest_ci_task.step_completed is False
                    or latest_ci_task.session_outcome != "completed"
                ):
                    other_task_wins_deny = True

            this_explicit_task_deny = (
                (payload.task_completed is False)
                or (payload.step_completed is False)
                or (payload.session_outcome != "completed")
            )
            this_explicit_task_complete = (
                payload.session_outcome == "completed" and payload.task_completed is True
            )
            auto_task_complete = (
                all_task_steps_completed
                and (not has_unexpanded_scope)
                and (payload.task_completed is None)
                and (payload.step_completed is not False)
            )

            if other_task_wins_completed or (
                not other_task_wins_deny
                and not this_explicit_task_deny
                and (this_explicit_task_complete or auto_task_complete)
            ):
                task.status = "completed"
            elif task.status == "completed":
                restored_task_status = baseline.get("task_status")
                if (
                    isinstance(restored_task_status, str)
                    and restored_task_status != "completed"
                ):
                    task.status = restored_task_status
                else:
                    task.status = "in_progress"
            await db.flush()

    after_state = {
        "session_status": session.status,
        "step_status": step.status if step is not None else None,
        "step_remaining_base_seconds": step.remaining_base_seconds if step is not None else None,
        "task_status": task.status if task is not None else None,
    }
    db.info["command_result_meta"] = {
        "_projection": {
            "checkin_id": str(checkin.id),
            "run_id": str(run.id),
            "response_revision": checkin.response_revision,
            "baseline": baseline,
            "before": before_state,
            "after": after_state,
        }
    }


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
    now: datetime | None = None,
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

    _validate_completion_flags(payload)

    ref_now = run_service._current_utc_now(now)
    responded_at_utc = payload.responded_at.astimezone(UTC)
    if responded_at_utc < checkin.asked_at.astimezone(UTC):
        raise run_service._validation_error(
            "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
            "responded_at",
        )
    if responded_at_utc > ref_now + timedelta(seconds=300):
        raise run_service._validation_error(
            "Thời điểm phản hồi check-in không được vượt quá thời gian hiện tại quá 5 phút.",
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

    if checkin.kind == "final":
        if run.ended_at is None or run.run_state not in ("awaiting_checkin", "closed"):
            raise AppError(
                status_code=409,
                code="INVALID_RUN_STATE",
                message="Chỉ có thể trả lời check-in cuối phiên sau khi phiên tập trung đã kết thúc.",
                next_action="reload_and_review",
            )
        if responded_at_utc < run.ended_at.astimezone(UTC):
            raise run_service._validation_error(
                "Thông tin check-in hoặc phiên bản câu trả lời không hợp lệ.",
                "responded_at",
            )

    prior_response_revision = checkin.response_revision
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
        if (
            prior_response_revision == 0
            and run.checkin_timeout_at is not None
            and resp_utc < run.checkin_timeout_at.astimezone(UTC)
        ):
            if run.dnd_release_at is not None:
                run.dnd_release_at = min(run.dnd_release_at.astimezone(UTC), resp_utc)
            else:
                run.dnd_release_at = payload.responded_at
        run.updated_at = datetime.now(UTC)

        await _apply_final_checkin_projection(
            db,
            user_uuid=user_uuid,
            run=run,
            checkin=checkin,
            payload=payload,
        )

    await db.flush()
    return run_service._checkin_to_schema(checkin)
