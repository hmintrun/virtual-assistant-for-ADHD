"""Service điều phối Check-in giữa phiên và cuối phiên (UF5/W05: list_run_checkins, create_run_checkin, respond_to_checkin)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.calendar import CalendarConnection, CalendarEventLink, CalendarSyncJob
from app.models.execution import CheckIn, SessionRun
from app.models.session import Session as SessionRow
from app.models.step import Step as StepRow
from app.models.system import CommandReceipt, Reminder
from app.models.task import Task as TaskRow
from app.repositories import run_repository
from app.schemas.execution import (
    CheckInCreateRequest,
    CheckInData,
    CheckInKind,
    CheckInListResult,
    CheckInResponseRequest,
)
from app.services import command_service, run_service, streak_service
from app.services.plan_service import build_calendar_event_text
from app.services.reschedule_service import _next_session_google_event_id

VALID_ANSWER_OUTCOMES = {"completed", "partial", "incomplete", "busy", "skipped"}
TERMINAL_PARENT_STATUSES = ("completed", "skipped", "cancelled")


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


def _ensure_utc(dt: datetime) -> datetime:
    """Chuẩn hóa datetime về UTC-aware để so sánh an toàn trên SQLite & PostgreSQL."""
    return dt.astimezone(UTC) if dt.utcoffset() is not None else dt.replace(tzinfo=UTC)


def _receipt_sort_key(receipt: CommandReceipt) -> tuple[int, datetime, datetime]:
    """Khóa sắp xếp receipt mới nhất theo (response_revision, applied_at, created_at)."""
    refs = receipt.result_reference or {}
    proj = refs.get("_projection") if isinstance(refs, dict) else None
    rev = 0
    if isinstance(proj, dict):
        try:
            rev = int(proj.get("response_revision", 0))
        except (TypeError, ValueError):
            rev = 0
    epoch = datetime.min.replace(tzinfo=UTC)
    applied = _ensure_utc(receipt.applied_at) if receipt.applied_at is not None else epoch
    created = _ensure_utc(receipt.created_at) if receipt.created_at is not None else epoch
    return (rev, applied, created)


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
    prior_receipts.sort(key=_receipt_sort_key, reverse=True)

    for receipt in prior_receipts:
        refs = receipt.result_reference or {}
        proj = refs.get("_projection")
        if refs.get("checkin_id") == str(checkin_id) and isinstance(proj, dict):
            saved_baseline = proj.get("baseline")
            if isinstance(saved_baseline, dict):
                cloned = copy.deepcopy(saved_baseline)
                if not isinstance(cloned.get("auto_cancelled_sessions"), dict):
                    cloned["auto_cancelled_sessions"] = {}
                return cloned

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
        "auto_cancelled_sessions": {},
    }


def _build_session_calendar_payload_snapshot(
    sess: SessionRow,
    *,
    event_id: str,
    step_title: str | None,
) -> dict[str, Any]:
    """Dựng payload_snapshot chuẩn Google Calendar cho phiên cần tạo/cập nhật lại."""
    summary, description = build_calendar_event_text(
        sess.title,
        step_title,
        sess.session_goal,
        sess.expected_output,
    )
    payload_snapshot: dict[str, Any] = {
        "id": event_id,
        "summary": summary,
        "start": {
            "dateTime": _ensure_utc(sess.scheduled_start).isoformat(),
            "timeZone": "Asia/Ho_Chi_Minh",
        },
        "end": {
            "dateTime": _ensure_utc(sess.scheduled_end).isoformat(),
            "timeZone": "Asia/Ho_Chi_Minh",
        },
        "transparency": "opaque",
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {
            "private": {
                "proactive_session_id": str(sess.id),
                "proactive_schedule_revision": str(sess.schedule_revision),
            }
        },
    }
    if description is not None:
        payload_snapshot["description"] = description
    return payload_snapshot


async def _has_self_cancelled_step_sessions(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    step_id: UUID,
    current_session_id: UUID,
    baseline: dict[str, Any],
) -> bool:
    """Kiểm tra Step có phiên scheduled nào tạm hủy bởi chính CheckIn này đang ở trạng thái cancelled."""
    auto_cancelled = baseline.get("auto_cancelled_sessions")
    if not isinstance(auto_cancelled, dict) or not auto_cancelled:
        return False
    expected_rev_by_id: dict[UUID, int | None] = {}
    step_id_str = str(step_id)
    for sid, snap in auto_cancelled.items():
        if not isinstance(snap, dict):
            continue
        if snap.get("is_currently_cancelled", True) is False:
            continue
        if snap.get("step_id") != step_id_str or snap.get("status") != "scheduled":
            continue
        try:
            su = UUID(str(sid))
        except ValueError:
            continue
        if su == current_session_id:
            continue
        raw_rev = snap.get("schedule_revision")
        expected_rev_by_id[su] = int(raw_rev) if raw_rev is not None else None
    if not expected_rev_by_id:
        return False
    rows = (
        await db.execute(
            select(SessionRow.id, SessionRow.schedule_revision).where(
                SessionRow.user_id == user_uuid,
                SessionRow.id.in_(list(expected_rev_by_id.keys())),
                SessionRow.status == "cancelled",
            )
        )
    ).all()
    for sess_id, sched_rev in rows:
        expected_rev = expected_rev_by_id.get(sess_id)
        if expected_rev is None or sched_rev == expected_rev:
            return True
    return False


async def _compute_target_cancel_ids(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    current_session_id: UUID,
    step: StepRow | None,
    task: TaskRow | None,
) -> list[UUID]:
    """Xác định danh sách phiên scheduled thừa (chưa từng có SessionRun) cần hủy."""
    cancel_task = task is not None and task.status == "completed"
    cancel_step = step is not None and step.status in ("completed", "skipped")
    if not cancel_task and not cancel_step:
        return []

    no_run_exists = ~exists(
        select(SessionRun.id).where(
            SessionRun.user_id == user_uuid,
            SessionRun.session_id == SessionRow.id,
        )
    )
    if cancel_task and task is not None:
        stmt = (
            select(SessionRow.id)
            .join(StepRow, StepRow.id == SessionRow.step_id)
            .where(
                SessionRow.user_id == user_uuid,
                StepRow.user_id == user_uuid,
                StepRow.task_id == task.id,
                SessionRow.id != current_session_id,
                SessionRow.status == "scheduled",
                no_run_exists,
            )
            .order_by(SessionRow.scheduled_start.asc(), SessionRow.id.asc())
        )
    elif step is not None:
        stmt = (
            select(SessionRow.id)
            .where(
                SessionRow.user_id == user_uuid,
                SessionRow.step_id == step.id,
                SessionRow.id != current_session_id,
                SessionRow.status == "scheduled",
                no_run_exists,
            )
            .order_by(SessionRow.scheduled_start.asc(), SessionRow.id.asc())
        )
    else:
        return []
    return list(await db.scalars(stmt))


async def _restore_calendar_link_for_session(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    checkin: CheckIn,
    sess: SessionRow,
    sess_step: StepRow,
    cal_snap: dict[str, Any],
    now_utc: datetime,
) -> None:
    """Khôi phục CalendarEventLink & CalendarSyncJob theo State Machine 3 nhánh."""
    raw_link_id = cal_snap.get("link_id")
    if not raw_link_id:
        return
    try:
        link_uuid = UUID(str(raw_link_id))
    except ValueError:
        return

    link = await db.scalar(
        select(CalendarEventLink)
        .where(
            CalendarEventLink.id == link_uuid,
            CalendarEventLink.user_id == user_uuid,
        )
        .execution_options(populate_existing=True)
    )
    if link is None:
        return

    del_job: CalendarSyncJob | None = None
    raw_del_id = cal_snap.get("enqueued_delete_job_id")
    if raw_del_id:
        try:
            del_job = await db.scalar(
                select(CalendarSyncJob)
                .where(
                    CalendarSyncJob.id == UUID(str(raw_del_id)),
                    CalendarSyncJob.user_id == user_uuid,
                )
                .execution_options(populate_existing=True)
            )
        except ValueError:
            del_job = None

    del_job_is_queued = del_job is not None and del_job.job_state == "queued"
    del_job_is_leased = (
        del_job is not None
        and del_job_is_queued
        and del_job.lease_expires_at is not None
        and _ensure_utc(del_job.lease_expires_at) > now_utc
    )
    del_job_is_unleased = del_job is not None and del_job_is_queued and not del_job_is_leased
    del_job_finished = del_job is not None and not del_job_is_queued
    closed_job_ops = [
        str(op) for op in (cal_snap.get("closed_job_ops") or []) if isinstance(op, str)
    ]
    has_closed_write_op = any(op in ("create", "update") for op in closed_job_ops)

    other_current_link = await db.scalar(
        select(CalendarEventLink.id).where(
            CalendarEventLink.user_id == user_uuid,
            CalendarEventLink.session_id == sess.id,
            CalendarEventLink.connection_id == link.connection_id,
            CalendarEventLink.google_calendar_id == link.google_calendar_id,
            CalendarEventLink.is_current.is_(True),
            CalendarEventLink.id != link.id,
        )
    )

    needs_new_link = (
        del_job_is_leased
        or del_job_finished
        or (link.synced_schedule_revision is not None and link.sync_state == "deleted")
        or (not link.is_current and other_current_link is not None)
    )

    if needs_new_link:
        # Nhánh (iii): Job delete đang leased hoặc đã chạy/link đã deleted -> đóng link cũ, tạo link mới.
        if link.is_current:
            link.is_current = False
            link.updated_at = now_utc
            link.revision += 1
            await db.flush()

        conn = await db.scalar(
            select(CalendarConnection)
            .where(
                CalendarConnection.id == link.connection_id,
                CalendarConnection.user_id == user_uuid,
                CalendarConnection.connection_state == "connected",
                CalendarConnection.write_calendar_id.is_not(None),
            )
            .limit(1)
        )
        if conn is not None and conn.write_calendar_id:
            existing_target_current = await db.scalar(
                select(CalendarEventLink)
                .where(
                    CalendarEventLink.user_id == user_uuid,
                    CalendarEventLink.session_id == sess.id,
                    CalendarEventLink.connection_id == conn.id,
                    CalendarEventLink.google_calendar_id == conn.write_calendar_id,
                    CalendarEventLink.is_current.is_(True),
                    CalendarEventLink.id != link.id,
                )
                .execution_options(populate_existing=True)
            )
            if existing_target_current is not None:
                cal_snap["link_id"] = str(existing_target_current.id)
                cal_snap["prev_is_current"] = existing_target_current.is_current
                cal_snap["prev_sync_state"] = existing_target_current.sync_state
                cal_snap["prev_synced_schedule_revision"] = (
                    existing_target_current.synced_schedule_revision
                )
                cal_snap["closed_job_ids"] = []
                cal_snap["closed_job_ops"] = []
                cal_snap["enqueued_delete_job_id"] = None
                return

            new_event_id = await _next_session_google_event_id(
                db,
                connection_id=conn.id,
                calendar_id=conn.write_calendar_id,
                session_id=sess.id,
            )
            new_link = CalendarEventLink(
                id=uuid4(),
                user_id=user_uuid,
                session_id=sess.id,
                connection_id=conn.id,
                google_calendar_id=conn.write_calendar_id,
                google_event_id=new_event_id,
                google_etag=None,
                desired_schedule_revision=sess.schedule_revision,
                synced_schedule_revision=None,
                sync_state="pending",
                is_current=True,
            )
            db.add(new_link)
            await db.flush()

            payload_snapshot = _build_session_calendar_payload_snapshot(
                sess, event_id=new_event_id, step_title=sess_step.title
            )
            db.add(
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=user_uuid,
                    connection_id=conn.id,
                    event_link_id=new_link.id,
                    target_kind="session_event",
                    target_ref=str(sess.id),
                    operation="create",
                    target_schedule_revision=sess.schedule_revision,
                    expected_google_etag=None,
                    payload_snapshot=payload_snapshot,
                    idempotency_key=(
                        f"checkin_restore:{sess.id}:ci{checkin.id.hex[:8]}"
                        f":r{checkin.response_revision}:{new_link.id.hex[:8]}"
                    ),
                    job_state="queued",
                    attempt_count=0,
                )
            )
            cal_snap["link_id"] = str(new_link.id)
            cal_snap["prev_is_current"] = True
            cal_snap["prev_sync_state"] = "pending"
            cal_snap["prev_synced_schedule_revision"] = None
            cal_snap["closed_job_ids"] = []
            cal_snap["closed_job_ops"] = []
            cal_snap["enqueued_delete_job_id"] = None
        return

    if link.synced_schedule_revision is None or has_closed_write_op:
        # Nhánh (ii): Chưa sync lần nào hoặc từng đóng job create/update chưa chạy -> xếp lại create/update.
        if del_job_is_unleased and del_job is not None:
            del_job.job_state = "completed"
            del_job.last_error_code = None
            del_job.lease_expires_at = None
            del_job.updated_at = now_utc
            del_job.revision += 1

        conn = await db.scalar(
            select(CalendarConnection)
            .where(
                CalendarConnection.id == link.connection_id,
                CalendarConnection.user_id == user_uuid,
                CalendarConnection.connection_state == "connected",
                CalendarConnection.write_calendar_id.is_not(None),
            )
            .limit(1)
        )
        if conn is not None and conn.write_calendar_id and other_current_link is None:
            event_id = (
                link.google_event_id
                if link.google_event_id is not None
                else await _next_session_google_event_id(
                    db,
                    connection_id=link.connection_id,
                    calendar_id=link.google_calendar_id,
                    session_id=sess.id,
                )
            )
            link.google_event_id = event_id
            link.is_current = True
            link.sync_state = "pending"
            link.updated_at = now_utc
            link.revision += 1

            op = "create" if link.synced_schedule_revision is None else "update"
            payload_snapshot = _build_session_calendar_payload_snapshot(
                sess, event_id=event_id, step_title=sess_step.title
            )
            db.add(
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=user_uuid,
                    connection_id=link.connection_id,
                    event_link_id=link.id,
                    target_kind="session_event",
                    target_ref=str(sess.id),
                    operation=op,
                    target_schedule_revision=sess.schedule_revision,
                    expected_google_etag=link.google_etag if op == "update" else None,
                    payload_snapshot=payload_snapshot,
                    idempotency_key=(
                        f"checkin_restore:{sess.id}:ci{checkin.id.hex[:8]}"
                        f":r{checkin.response_revision}:{link.id.hex[:8]}"
                    ),
                    job_state="queued",
                    attempt_count=0,
                )
            )
            cal_snap["closed_job_ids"] = []
            cal_snap["closed_job_ops"] = []
            cal_snap["enqueued_delete_job_id"] = None
        return

    # Nhánh (i): Đã sync trước đó, delete job còn queued & unleased -> đóng delete job, hồi sync_state cũ.
    if del_job_is_unleased and del_job is not None:
        del_job.job_state = "completed"
        del_job.last_error_code = None
        del_job.lease_expires_at = None
        del_job.updated_at = now_utc
        del_job.revision += 1

    link.sync_state = str(cal_snap.get("prev_sync_state") or "synced")
    link.is_current = bool(cal_snap.get("prev_is_current", True))
    link.updated_at = now_utc
    link.revision += 1
    cal_snap["enqueued_delete_job_id"] = None


async def _restore_auto_cancelled_sessions(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    checkin: CheckIn,
    baseline: dict[str, Any],
    target_cancel_ids: set[UUID],
    now_utc: datetime,
) -> None:
    """Khôi phục các phiên tự động hủy nếu Step & Task của từng phiên đều không còn kết thúc."""
    auto_cancelled = baseline.get("auto_cancelled_sessions")
    if not isinstance(auto_cancelled, dict) or not auto_cancelled:
        return

    restore_candidates: list[tuple[UUID, dict[str, Any]]] = []
    for sid, snap in auto_cancelled.items():
        if not isinstance(snap, dict):
            continue
        if snap.get("is_currently_cancelled", True) is False:
            continue
        try:
            su = UUID(str(sid))
        except ValueError:
            continue
        if su in target_cancel_ids:
            continue
        restore_candidates.append((su, snap))

    if not restore_candidates:
        return

    candidate_uuids = [su for su, _ in restore_candidates]
    snap_by_id = {su: snap for su, snap in restore_candidates}
    sessions = list(
        await db.scalars(
            select(SessionRow)
            .where(
                SessionRow.user_id == user_uuid,
                SessionRow.id.in_(candidate_uuids),
                SessionRow.status == "cancelled",
            )
            .order_by(SessionRow.scheduled_start.asc(), SessionRow.id.asc())
            .execution_options(populate_existing=True)
        )
    )
    if not sessions:
        return

    step_ids = list({s.step_id for s in sessions})
    steps_map = {
        st.id: st
        for st in (
            await db.scalars(
                select(StepRow)
                .where(StepRow.user_id == user_uuid, StepRow.id.in_(step_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    task_ids = list({st.task_id for st in steps_map.values()})
    tasks_map = (
        {
            tk.id: tk
            for tk in (
                await db.scalars(
                    select(TaskRow)
                    .where(TaskRow.user_id == user_uuid, TaskRow.id.in_(task_ids))
                    .execution_options(populate_existing=True)
                )
            ).all()
        }
        if task_ids
        else {}
    )

    for sess in sessions:
        sess_step = steps_map.get(sess.step_id)
        sess_task = tasks_map.get(sess_step.task_id) if sess_step is not None else None
        if sess_step is None or sess_step.status in TERMINAL_PARENT_STATUSES:
            continue
        if sess_task is None or sess_task.status in TERMINAL_PARENT_STATUSES:
            continue

        snap = snap_by_id[sess.id]
        expected_sched_rev = snap.get("schedule_revision")
        if expected_sched_rev is not None and sess.schedule_revision != int(expected_sched_rev):
            continue
        sess.status = str(snap.get("status") or "scheduled")
        sess.start_state = str(snap.get("start_state") or "waiting")
        sess.updated_at = now_utc
        sess.revision += 1
        snap["is_currently_cancelled"] = False

        rem_state_map: dict[UUID, str] = {}
        for rem_item in snap.get("cancelled_reminders") or []:
            if not isinstance(rem_item, dict) or not rem_item.get("reminder_id"):
                continue
            try:
                ru = UUID(str(rem_item["reminder_id"]))
            except ValueError:
                continue
            orig_state = str(rem_item.get("delivery_state") or "scheduled")
            if orig_state not in ("scheduled", "snoozed"):
                orig_state = "scheduled"
            rem_state_map[ru] = orig_state

        if rem_state_map:
            rems = list(
                await db.scalars(
                    select(Reminder)
                    .where(
                        Reminder.user_id == user_uuid,
                        Reminder.id.in_(list(rem_state_map.keys())),
                        Reminder.delivery_state == "cancelled",
                    )
                    .execution_options(populate_existing=True)
                )
            )
            for rem in rems:
                rem.delivery_state = rem_state_map.get(rem.id, "scheduled")
                rem.updated_at = now_utc
                rem.revision += 1

        cal_snap = snap.get("calendar_snapshot")
        if isinstance(cal_snap, dict):
            await _restore_calendar_link_for_session(
                db,
                user_uuid=user_uuid,
                checkin=checkin,
                sess=sess,
                sess_step=sess_step,
                cal_snap=cal_snap,
                now_utc=now_utc,
            )


async def _cancel_surplus_scheduled_sessions(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    checkin: CheckIn,
    target_cancel_ids: list[UUID],
    baseline: dict[str, Any],
    now_utc: datetime,
) -> None:
    """Hủy các phiên scheduled thừa, Reminder liên quan và cập nhật Google Calendar outbox."""
    if not target_cancel_ids:
        return

    sessions_to_cancel = list(
        await db.scalars(
            select(SessionRow)
            .where(
                SessionRow.user_id == user_uuid,
                SessionRow.id.in_(target_cancel_ids),
                SessionRow.status == "scheduled",
            )
            .order_by(SessionRow.scheduled_start.asc(), SessionRow.id.asc())
            .execution_options(populate_existing=True)
        )
    )
    if not sessions_to_cancel:
        return

    auto_cancelled = baseline.setdefault("auto_cancelled_sessions", {})
    cancelled_uuids: list[UUID] = []
    session_by_id: dict[UUID, SessionRow] = {}
    for sess in sessions_to_cancel:
        sid = str(sess.id)
        snap = auto_cancelled.get(sid)
        if not isinstance(snap, dict):
            snap = {
                "session_id": sid,
                "step_id": str(sess.step_id),
                "status": sess.status,
                "start_state": sess.start_state,
                "schedule_revision": int(sess.schedule_revision),
                "is_currently_cancelled": True,
                "cancelled_reminders": [],
                "calendar_snapshot": None,
            }
            auto_cancelled[sid] = snap
        else:
            snap.setdefault("session_id", sid)
            snap.setdefault("step_id", str(sess.step_id))
            snap["schedule_revision"] = int(sess.schedule_revision)
            snap["is_currently_cancelled"] = True
            snap.setdefault("cancelled_reminders", [])
            snap.setdefault("calendar_snapshot", None)

        sess.status = "cancelled"
        sess.start_state = "superseded"
        sess.updated_at = now_utc
        sess.revision += 1
        cancelled_uuids.append(sess.id)
        session_by_id[sess.id] = sess

    reminder_rows = list(
        await db.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == user_uuid,
                Reminder.session_id.in_(cancelled_uuids),
                Reminder.delivery_state.in_(("scheduled", "snoozed")),
            )
            .order_by(Reminder.id.asc())
            .execution_options(populate_existing=True)
        )
    )
    for rem in reminder_rows:
        snap = auto_cancelled[str(rem.session_id)]
        existing_rems = snap.setdefault("cancelled_reminders", [])
        seen_rem_ids = {
            item.get("reminder_id") for item in existing_rems if isinstance(item, dict)
        }
        rem_id_str = str(rem.id)
        if rem_id_str not in seen_rem_ids:
            existing_rems.append(
                {
                    "reminder_id": rem_id_str,
                    "delivery_state": rem.delivery_state,
                }
            )
        rem.delivery_state = "cancelled"
        rem.updated_at = now_utc
        rem.revision += 1

    links = list(
        await db.scalars(
            select(CalendarEventLink)
            .where(
                CalendarEventLink.user_id == user_uuid,
                CalendarEventLink.session_id.in_(cancelled_uuids),
                CalendarEventLink.is_current.is_(True),
            )
            .order_by(CalendarEventLink.id.asc())
            .execution_options(populate_existing=True)
        )
    )
    conn_ids = {link.connection_id for link in links}
    connected_conn_ids = (
        set(
            await db.scalars(
                select(CalendarConnection.id).where(
                    CalendarConnection.user_id == user_uuid,
                    CalendarConnection.id.in_(conn_ids),
                    CalendarConnection.connection_state == "connected",
                )
            )
        )
        if conn_ids
        else set()
    )
    for link in links:
        queued_jobs = list(
            await db.scalars(
                select(CalendarSyncJob)
                .where(
                    CalendarSyncJob.user_id == user_uuid,
                    CalendarSyncJob.event_link_id == link.id,
                    CalendarSyncJob.job_state == "queued",
                )
                .order_by(CalendarSyncJob.created_at.asc(), CalendarSyncJob.id.asc())
                .execution_options(populate_existing=True)
            )
        )
        unleased_jobs = [
            job
            for job in queued_jobs
            if job.lease_expires_at is None or _ensure_utc(job.lease_expires_at) <= now_utc
        ]
        leased_jobs = [
            job
            for job in queued_jobs
            if job.lease_expires_at is not None and _ensure_utc(job.lease_expires_at) > now_utc
        ]

        prev_is_current = link.is_current
        prev_sync_state = link.sync_state
        prev_synced_schedule_revision = link.synced_schedule_revision

        closed_job_ids: list[str] = []
        closed_job_ops: list[str] = []
        for job in unleased_jobs:
            closed_job_ids.append(str(job.id))
            closed_job_ops.append(job.operation)
            job.job_state = "completed"
            job.last_error_code = None
            job.lease_expires_at = None
            job.updated_at = now_utc
            job.revision += 1

        is_unattempted_create = (
            link.synced_schedule_revision is None
            and not leased_jobs
            and any(job.operation == "create" for job in unleased_jobs)
            and all(job.attempt_count == 0 for job in unleased_jobs if job.operation == "create")
        )

        enqueued_delete_job_id: str | None = None
        if is_unattempted_create or link.google_event_id is None:
            link.is_current = False
            link.sync_state = "deleted"
            link.updated_at = now_utc
            link.revision += 1
        elif link.connection_id not in connected_conn_ids:
            # Kết nối lịch đã ngắt -> không enqueue delete job và giữ nguyên sync_state
            enqueued_delete_job_id = None
        else:
            link.sync_state = "pending"
            link.updated_at = now_utc
            link.revision += 1
            sess_obj = session_by_id[link.session_id]
            del_job = CalendarSyncJob(
                id=uuid4(),
                user_id=user_uuid,
                connection_id=link.connection_id,
                event_link_id=link.id,
                target_kind="session_event",
                target_ref=str(link.session_id),
                operation="delete",
                target_schedule_revision=sess_obj.schedule_revision,
                expected_google_etag=link.google_etag,
                payload_snapshot={
                    "id": link.google_event_id,
                    "google_calendar_id": link.google_calendar_id,
                },
                idempotency_key=(
                    f"checkin_cancel:{link.session_id}:ci{checkin.id.hex[:8]}"
                    f":r{checkin.response_revision}:{link.id.hex[:8]}"
                ),
                job_state="queued",
                attempt_count=0,
            )
            db.add(del_job)
            enqueued_delete_job_id = str(del_job.id)

        auto_cancelled[str(link.session_id)]["calendar_snapshot"] = {
            "link_id": str(link.id),
            "prev_is_current": prev_is_current,
            "prev_sync_state": prev_sync_state,
            "prev_synced_schedule_revision": prev_synced_schedule_revision,
            "closed_job_ids": closed_job_ids,
            "closed_job_ops": closed_job_ops,
            "enqueued_delete_job_id": enqueued_delete_job_id,
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
        # Pha 1: Tính all_step_sessions_completed (coi phiên tạm hủy bởi chính CheckIn này là chưa xong).
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
        has_self_cancelled = await _has_self_cancelled_step_sessions(
            db,
            user_uuid=user_uuid,
            step_id=step.id,
            current_session_id=session.id,
            baseline=baseline,
        )
        all_step_sessions_completed = (
            bool(step_sessions)
            and (not has_self_cancelled)
            and all(s.status == "completed" for s in step_sessions)
        )

        # Pha 2: Projection Step -> Task (giữ bảo vệ xác nhận mới hơn từ Run khác).
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
            and not other_step_wins_skipped
            and not this_explicit_step_deny
            and (this_explicit_step_complete or this_auto_step_complete)
        ):
            step.status = "completed"
            step.remaining_base_seconds = 0
        elif other_step_wins_skipped or (
            this_explicit_step_skip and not other_step_wins_deny
        ):
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

    # Pha 3: Tính target_cancel_ids 1 lần rồi khôi phục & hủy phiên thừa (0 churn, không tăng ScheduleVersion).
    now_utc = datetime.now(UTC)
    target_cancel_ids = await _compute_target_cancel_ids(
        db,
        user_uuid=user_uuid,
        current_session_id=session.id,
        step=step,
        task=task,
    )
    await _restore_auto_cancelled_sessions(
        db,
        user_uuid=user_uuid,
        checkin=checkin,
        baseline=baseline,
        target_cancel_ids=set(target_cancel_ids),
        now_utc=now_utc,
    )
    await _cancel_surplus_scheduled_sessions(
        db,
        user_uuid=user_uuid,
        checkin=checkin,
        target_cancel_ids=target_cancel_ids,
        baseline=baseline,
        now_utc=now_utc,
    )
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
    if checkin.kind == "final":
        await streak_service.sync_streak_for_run(db, user_uuid=user_uuid, run=run)
    return run_service._checkin_to_schema(checkin)
