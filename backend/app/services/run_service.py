"""Service điều phối thực thi phiên tập trung (UF3/W04–W05: start, active, detail, events, end, actual-correction, lazy timeout)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.calendar import CalendarEventLink, PlanningContext
from app.models.execution import ActualCorrection, CheckIn, RunEvent, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import UserPreference as UserPreferenceRow
from app.repositories import run_repository
from app.schemas.availability import Availability, DraftAvailability
from app.schemas.common import FieldError, SessionSegment
from app.schemas.execution import (
    ActivePhase,
    ActualCorrectionData,
    ActualCorrectionRequest,
    ActualSource,
    CheckInData,
    CheckInKind,
    CheckInResponseState,
    RunEndRequest,
    RunEventBatchRequest,
    RunEventBatchResult,
    RunState,
    SessionOutcome,
    SessionRunData,
    SessionSnapshot,
    SessionStartRequest,
    SettingsSnapshot,
    SkipScope,
)
from app.services import command_service
from app.services.planning.availability import compute_effective_free_intervals

MIN_INTER_SESSION_GAP_SECONDS = 900
START_GRACE_SECONDS = 900
CHECKIN_TIMEOUT_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 300
ALLOWED_RUN_EVENT_TYPES = {
    "progress",
    "pause",
    "resume",
    "segment_transition",
    "heartbeat",
}


def _parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        ) from None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.utcoffset() is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        return dt if dt.utcoffset() is not None else dt.replace(tzinfo=UTC)
    return None


def _validation_error(message: str, field_path: str = "") -> AppError:
    return AppError(
        status_code=422,
        code="VALIDATION_ERROR",
        message=message,
        field_errors=[FieldError(field_path=field_path, message=message)] if field_path else [],
        next_action="fix_fields",
    )


def _current_utc_now(now: datetime | None = None) -> datetime:
    if now is not None:
        if now.utcoffset() is None:
            raise _validation_error("Mốc thời gian kiểm tra phải có múi giờ.", "now")
        return now.astimezone(UTC)
    return datetime.now(UTC)


def _require_replan(message: str) -> AppError:
    return AppError(
        status_code=409,
        code="START_REQUIRES_REPLAN",
        message=message,
        next_action="create_proposal",
    )


def _checkin_to_schema(checkin: CheckIn) -> CheckInData:
    return CheckInData(
        checkin_id=str(checkin.id),
        run_id=str(checkin.run_id),
        kind=cast(CheckInKind, checkin.kind),
        response_state=cast(CheckInResponseState, checkin.response_state),
        session_outcome=cast(SessionOutcome, checkin.session_outcome),
        step_completed=checkin.step_completed,
        task_completed=checkin.task_completed,
        skip_scope=cast(SkipScope | None, checkin.skip_scope),
        remaining_note=checkin.remaining_note,
        asked_at=checkin.asked_at,
        responded_at=checkin.responded_at,
        response_revision=checkin.response_revision,
    )


async def _resolve_settings_snapshot(
    db: AsyncSession,
    run: SessionRun,
    session_override: SessionRow | None = None,
) -> SettingsSnapshot:
    raw = run.settings_snapshot or {}
    if isinstance(raw, dict) and "effective_revision" in raw and isinstance(raw.get("session"), dict):
        try:
            clean_raw = {k: v for k, v in raw.items() if not k.startswith("_")}
            return SettingsSnapshot.model_validate(clean_raw)
        except Exception:
            pass

    session = session_override or await db.scalar(
        select(SessionRow).where(
            SessionRow.id == run.session_id,
            SessionRow.user_id == run.user_id,
        )
    )
    pref = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == run.user_id)
    )
    effective_rev = pref.effective_revision if pref is not None else 1

    if session is not None:
        link_id = await db.scalar(
            select(CalendarEventLink.id).where(
                CalendarEventLink.user_id == run.user_id,
                CalendarEventLink.session_id == session.id,
                CalendarEventLink.is_current.is_(True),
            )
        )
        due_at: datetime = session.start_decision_due_at or (
            session.scheduled_start + timedelta(seconds=START_GRACE_SECONDS)
        )
        segments = [SessionSegment.model_validate(seg) for seg in session.segments]
        snap = SessionSnapshot(
            session_id=str(session.id),
            step_id=str(session.step_id),
            scheduled_start=session.scheduled_start,
            scheduled_end=session.scheduled_end,
            micro_task_text=session.micro_task_text or "Chuẩn bị cho phiên làm việc",
            micro_task_seconds=session.micro_task_seconds,
            work_seconds=session.work_seconds,
            rest_seconds=session.rest_seconds,
            segments=segments,
            duration_seconds=session.duration_seconds,
            schedule_revision=session.schedule_revision,
            status=session.status,
            calendar_event_link_id=str(link_id) if link_id else None,
            start_state=session.start_state,
            start_decision_due_at=due_at,
        )
    else:
        started = run.started_at
        ended = run.ended_at or (started + timedelta(seconds=1500))
        dur = max(60, int((ended - started).total_seconds()))
        snap = SessionSnapshot(
            session_id=str(run.session_id),
            step_id=str(run.session_id),
            scheduled_start=started,
            scheduled_end=ended,
            micro_task_text="Chuẩn bị cho phiên làm việc",
            micro_task_seconds=120,
            work_seconds=dur,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=dur)],
            duration_seconds=dur,
            schedule_revision=1,
            status="in_progress",
            calendar_event_link_id=None,
            start_state="started",
            start_decision_due_at=started + timedelta(seconds=START_GRACE_SECONDS),
        )
    return SettingsSnapshot(effective_revision=effective_rev, session=snap)


async def _run_to_schema(
    db: AsyncSession,
    run: SessionRun,
    *,
    settings_snapshot: SettingsSnapshot | None = None,
    check_in: CheckIn | CheckInData | None = None,
) -> SessionRunData:
    resolved_snapshot = settings_snapshot or await _resolve_settings_snapshot(db, run)
    resolved_checkin: CheckInData | None
    if isinstance(check_in, CheckInData):
        resolved_checkin = check_in
    elif isinstance(check_in, CheckIn):
        resolved_checkin = _checkin_to_schema(check_in)
    else:
        resolved_checkin = None

    session_snap = resolved_snapshot.session if resolved_snapshot is not None else None
    return SessionRunData(
        run_id=str(run.id),
        session_id=str(run.session_id),
        device_id=run.device_id,
        run_revision=run.revision,
        started_at=run.started_at,
        ended_at=run.ended_at,
        run_state=cast(RunState, run.run_state),
        active_phase=cast(ActivePhase, run.active_phase),
        segment_index=run.segment_index,
        work_elapsed_seconds=run.work_elapsed_seconds,
        rest_elapsed_seconds=run.rest_elapsed_seconds,
        actual_work_seconds=run.actual_work_seconds,
        actual_source=cast(ActualSource | None, run.actual_source),
        outcome=cast(SessionOutcome, run.outcome),
        checkin_timeout_at=run.checkin_timeout_at,
        settings_snapshot=resolved_snapshot,
        check_in=resolved_checkin,
        dnd_release_at=run.dnd_release_at,
        total_planned_seconds=session_snap.duration_seconds if session_snap is not None else None,
        segments=list(session_snap.segments) if session_snap is not None else [],
        continuation_of_run_id=(run.settings_snapshot or {}).get("_continuation_of_run_id")
        if isinstance(run.settings_snapshot, dict)
        else None,
    )


async def reconcile_expired_checkins(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    commit_if_read: bool = False,
) -> bool:
    """Đối soát lazy timeout cho CheckIn cuối phiên đã quá hạn (checkin_timeout_at <= now).

    - Chuyển CheckIn(kind='final', response_state='open') sang 'no_respond' bằng CAS.
    - Giữ nguyên response_revision = 0 và session_outcome = 'unknown'.
    - Khi được gọi từ luồng đọc (commit_if_read=True): thực hiện lock_owner + CAS + await db.commit().
    """
    user_uuid = command_service.owner_uuid(user_id)
    ref_now = _current_utc_now(now)

    expired_checkin_ids = list(
        await db.scalars(
            select(CheckIn.id)
            .join(SessionRun, CheckIn.run_id == SessionRun.id)
            .where(
                SessionRun.user_id == user_uuid,
                SessionRun.checkin_timeout_at.is_not(None),
                SessionRun.checkin_timeout_at <= ref_now,
                CheckIn.user_id == user_uuid,
                CheckIn.kind == "final",
                CheckIn.response_state == "open",
            )
        )
    )

    overdue_session_ids: list[UUID] = []
    waiting_sessions = list(
        await db.scalars(
            select(SessionRow).where(
                SessionRow.user_id == user_uuid,
                SessionRow.status == "scheduled",
                SessionRow.start_state == "waiting",
            )
        )
    )
    if waiting_sessions:
        started_session_ids = set(
            await db.scalars(
                select(SessionRun.session_id).where(
                    SessionRun.user_id == user_uuid,
                    SessionRun.session_id.in_([s.id for s in waiting_sessions]),
                )
            )
        )
        for s in waiting_sessions:
            if s.id in started_session_ids:
                continue
            due_at = s.start_decision_due_at or (
                s.scheduled_start + timedelta(seconds=START_GRACE_SECONDS)
            )
            if due_at.astimezone(UTC) <= ref_now:
                overdue_session_ids.append(s.id)

    if not expired_checkin_ids and not overdue_session_ids:
        return False

    in_command_tx = bool(db.info.get("defer_command_commit"))
    if not in_command_tx:
        await command_service.lock_owner(db, user_id)

    changed = False
    if expired_checkin_ids:
        updated_checkin_ids = list(
            await db.scalars(
                update(CheckIn)
                .where(
                    CheckIn.id.in_(expired_checkin_ids),
                    CheckIn.user_id == user_uuid,
                    CheckIn.kind == "final",
                    CheckIn.response_state == "open",
                )
                .values(response_state="no_respond")
                .returning(CheckIn.id)
                .execution_options(synchronize_session="fetch")
            )
        )
        if updated_checkin_ids:
            changed = True

    if overdue_session_ids:
        updated_session_ids = list(
            await db.scalars(
                update(SessionRow)
                .where(
                    SessionRow.id.in_(overdue_session_ids),
                    SessionRow.user_id == user_uuid,
                    SessionRow.status == "scheduled",
                    SessionRow.start_state == "waiting",
                )
                .values(start_state="not_started")
                .returning(SessionRow.id)
                .execution_options(synchronize_session="fetch")
            )
        )
        if updated_session_ids:
            changed = True

    if commit_if_read and not in_command_tx:
        if changed:
            await db.commit()
        else:
            await db.rollback()
    elif in_command_tx and changed:
        await db.flush()

    return changed


async def _validate_start_feasibility(
    db: AsyncSession,
    *,
    user_uuid: UUID,
    session: SessionRow,
    step: StepRow,
    task: TaskRow,
    req_start: datetime,
) -> None:
    req_end = req_start + timedelta(seconds=session.duration_seconds)

    # 1. Trạng thái Session / Step / Task không được ở trạng thái kết thúc
    if session.status != "scheduled":
        raise _require_replan("Phiên tập trung không còn ở trạng thái chờ thực hiện.")
    if step.status in ("completed", "skipped", "cancelled"):
        raise _require_replan("Bước công việc này đã kết thúc hoặc bị bỏ qua.")
    if task.status in ("completed", "skipped", "cancelled", "archived"):
        raise _require_replan("Công việc này đã kết thúc hoặc bị lưu trữ.")

    existing_run_id = await db.scalar(
        select(SessionRun.id).where(
            SessionRun.user_id == user_uuid,
            SessionRun.session_id == session.id,
        )
    )
    if existing_run_id is not None:
        raise _require_replan("Phiên tập trung này đã có lượt thực hiện.")

    # 2. Thứ tự phụ thuộc Step trong cùng Task & các Session đứng trước trong cùng Step
    earlier_incomplete_step = await db.scalar(
        select(StepRow.id).where(
            StepRow.user_id == user_uuid,
            StepRow.task_id == task.id,
            StepRow.order_index < step.order_index,
            StepRow.status.notin_(("completed", "skipped")),
        )
    )
    if earlier_incomplete_step is not None:
        raise _require_replan("Bước trước đó của công việc chưa hoàn thành. Cần xếp lại lịch.")

    earlier_step_sessions = list(
        await db.scalars(
            select(SessionRow).where(
                SessionRow.user_id == user_uuid,
                SessionRow.step_id == step.id,
                SessionRow.id != session.id,
                SessionRow.scheduled_start < session.scheduled_start,
                SessionRow.status.notin_(("cancelled", "skipped")),
            )
        )
    )
    if earlier_step_sessions:
        ended_run_session_ids = set(
            await db.scalars(
                select(SessionRun.session_id).where(
                    SessionRun.user_id == user_uuid,
                    SessionRun.session_id.in_([s.id for s in earlier_step_sessions]),
                    SessionRun.ended_at.is_not(None),
                )
            )
        )
        for earlier_sess in earlier_step_sessions:
            if earlier_sess.status not in ("completed", "skipped") and (
                earlier_sess.id not in ended_run_session_ids
            ):
                raise _require_replan(
                    "Phiên trước đó của cùng bước chưa được thực hiện. Cần xếp lại lịch."
                )

    # 3. Kiểm tra hạn chót (deadline / finish_by) và mốc bắt đầu của Task
    tc = task.time_constraint or {}
    if isinstance(tc, dict):
        deadline_dt = _parse_iso_datetime(tc.get("deadline_at") or tc.get("finish_by_at"))
        if deadline_dt is not None and req_end > deadline_dt.astimezone(UTC):
            raise _require_replan(
                "Thời điểm bắt đầu mới khiến phiên vượt quá hạn chót của công việc. Cần xếp lại lịch."
            )
        start_bound_dt = _parse_iso_datetime(tc.get("start_at"))
        if start_bound_dt is not None and req_start < start_bound_dt.astimezone(UTC):
            raise _require_replan(
                "Thời điểm bắt đầu nằm trước mốc mở công việc. Cần xếp lại lịch."
            )

    # 4. Kiểm tra khung giờ rảnh hiệu lực (Task.availability hoặc Availability của user)
    avail_schema: Availability | DraftAvailability | None = None
    if isinstance(task.availability, dict) and (
        task.availability.get("weekly_rules") or task.availability.get("date_overrides")
    ):
        try:
            avail_schema = DraftAvailability.model_validate(task.availability)
        except Exception:
            avail_schema = None
    else:
        avail_row = await db.scalar(
            select(AvailabilityRow).where(AvailabilityRow.user_id == user_uuid)
        )
        if avail_row is not None and (avail_row.weekly_rules or avail_row.date_overrides):
            try:
                avail_schema = Availability.model_validate(
                    {
                        "availability_id": str(avail_row.id),
                        "revision": avail_row.revision,
                        "timezone": avail_row.timezone,
                        "weekly_rules": avail_row.weekly_rules,
                        "date_overrides": avail_row.date_overrides,
                        "task_limits": avail_row.task_limits,
                        "source": avail_row.source,
                        "effective_intervals": None,
                        "calendar_context": None,
                        "confirmed_at": avail_row.confirmed_at,
                    }
                )
            except Exception:
                avail_schema = None

    if avail_schema is not None:
        free_intervals = compute_effective_free_intervals(
            avail_schema,
            window_start=req_start,
            window_end=req_end,
            task_id=str(task.id),
        )
        covered = any(
            iv.start_at.astimezone(UTC) <= req_start and iv.end_at.astimezone(UTC) >= req_end
            for iv in free_intervals
        )
        if not covered:
            raise _require_replan(
                "Thời điểm bắt đầu mới khiến phiên vượt khỏi giờ rảnh. Cần xếp lại lịch."
            )

    # Kiểm tra lịch bận từ PlanningContext hợp lệ (nếu có)
    contexts = list(
        await db.scalars(
            select(PlanningContext).where(
                PlanningContext.user_id == user_uuid,
                PlanningContext.sync_state == "valid",
                PlanningContext.query_start < req_end,
                PlanningContext.query_end > req_start,
                or_(
                    PlanningContext.expires_at.is_(None),
                    PlanningContext.expires_at > req_start,
                ),
            )
            .order_by(PlanningContext.created_at.desc())
        )
    )
    for ctx in contexts:
        if (
            task.source_draft_id is not None
            and ctx.source_draft_id is not None
            and ctx.source_draft_id != task.source_draft_id
        ):
            continue
        for busy in (ctx.busy_intervals or []) + (ctx.manual_busy_intervals or []):
            if not isinstance(busy, dict):
                continue
            source = busy.get("source") or {}
            if isinstance(source, dict) and source.get("session_id") == str(session.id):
                continue
            b_start = _parse_iso_datetime(busy.get("start_at"))
            b_end = _parse_iso_datetime(busy.get("end_at"))
            if b_start is not None and b_end is not None:
                if b_start.astimezone(UTC) < req_end and b_end.astimezone(UTC) > req_start:
                    raise _require_replan(
                        "Thời điểm thực hiện trùng với sự kiện bận trên lịch. Cần xếp lại lịch."
                    )

    # 5. Kiểm tra chồng lấn (Collision) & khoảng cách tối thiểu >= 900s trên TẤT CẢ các Task của user
    other_sessions = list(
        await db.scalars(
            select(SessionRow).where(
                SessionRow.user_id == user_uuid,
                SessionRow.id != session.id,
                SessionRow.status.notin_(("cancelled", "skipped")),
                SessionRow.scheduled_end >= req_start - timedelta(hours=24),
                SessionRow.scheduled_start <= req_end + timedelta(hours=24),
            )
            .order_by(SessionRow.scheduled_start.asc())
        )
    )
    gap_override_by_other_id: dict[UUID, int] = {}
    approved_proposals = list(
        await db.scalars(
            select(RescheduleProposal)
            .where(
                RescheduleProposal.user_id == user_uuid,
                RescheduleProposal.status == "approved",
            )
            .order_by(RescheduleProposal.updated_at.desc())
        )
    )
    all_relevant_sessions = [session, *other_sessions]
    for prop in approved_proposals:
        meta = (prop.proposed_changes or {}).get("_meta") or {}
        if meta.get("continuation_request") is None:
            continue
        gap_exceptions = meta.get("gap_exceptions") or []
        if not gap_exceptions:
            continue

        session_uuid_by_ref: dict[str, UUID] = {
            str(s.id): s.id for s in all_relevant_sessions
        }
        # 1. Ánh xạ trực tiếp từ applied_session_ids
        applied_ids = (prop.proposed_changes or {}).get("applied_session_ids") or {}
        for ref_k, real_id_str in applied_ids.items():
            try:
                session_uuid_by_ref[str(ref_k)] = UUID(str(real_id_str))
            except (ValueError, TypeError):
                pass

        # 2. Ánh xạ các temp_session_id trong sessions_to_add với các SessionRow thật (fallback nới lỏng cửa sổ)
        for add_item in (prop.proposed_changes or {}).get("sessions_to_add") or []:
            after_obj = add_item.get("after") or {}
            item_step_id = add_item.get("step_id") or after_obj.get("step_id")
            raw_start = after_obj.get("start")
            if not item_step_id or not raw_start:
                continue
            try:
                item_start = datetime.fromisoformat(str(raw_start)).astimezone(UTC)
            except (ValueError, TypeError):
                continue
            for s in all_relevant_sessions:
                if str(s.step_id) == str(item_step_id) and abs((s.scheduled_start.astimezone(UTC) - item_start).total_seconds()) <= 3600.0:
                    for rk in ("temp_session_id", "session_ref", "id"):
                        ref_val = add_item.get(rk)
                        if ref_val:
                            session_uuid_by_ref.setdefault(str(ref_val), s.id)

        for gx in gap_exceptions:
            from_ref = str(gx.get("from_session_ref") or "")
            to_ref = str(gx.get("to_session_ref") or "")
            applied_gap = int(gx.get("applied_gap_seconds", MIN_INTER_SESSION_GAP_SECONDS))

            from_uuid = session_uuid_by_ref.get(from_ref)
            if from_uuid is None:
                try:
                    from_uuid = UUID(from_ref)
                except (ValueError, TypeError):
                    from_uuid = None

            to_uuid = session_uuid_by_ref.get(to_ref)
            if to_uuid is None:
                try:
                    to_uuid = UUID(to_ref)
                except (ValueError, TypeError):
                    to_uuid = None

            other_target_id: UUID | None = None
            if from_uuid == session.id and to_uuid is not None:
                other_target_id = to_uuid
            elif to_uuid == session.id and from_uuid is not None:
                other_target_id = from_uuid

            if other_target_id is not None:
                prev_gap = gap_override_by_other_id.get(other_target_id)
                if prev_gap is None or applied_gap < prev_gap:
                    gap_override_by_other_id[other_target_id] = max(0, applied_gap)

    if other_sessions:
        other_ids = [s.id for s in other_sessions]
        other_runs = list(
            await db.scalars(
                select(SessionRun)
                .where(
                    SessionRun.user_id == user_uuid,
                    SessionRun.session_id.in_(other_ids),
                    SessionRun.ended_at.is_not(None),
                )
                .order_by(SessionRun.started_at.asc())
            )
        )
        ended_run_by_session: dict[UUID, SessionRun] = {}
        for r in other_runs:
            ended_run_by_session[r.session_id] = r

        for other in other_sessions:
            ended_run = ended_run_by_session.get(other.id)
            if ended_run is not None and ended_run.ended_at is not None:
                other_start = ended_run.started_at.astimezone(UTC)
                other_end = ended_run.ended_at.astimezone(UTC)
            else:
                other_start = other.scheduled_start.astimezone(UTC)
                other_end = other.scheduled_end.astimezone(UTC)

            required_gap = timedelta(
                seconds=gap_override_by_other_id.get(other.id, MIN_INTER_SESSION_GAP_SECONDS)
            )
            if not (req_end + required_gap <= other_start or other_end + required_gap <= req_start):
                raise _require_replan(
                    "Thời điểm bắt đầu mới vi phạm khoảng cách tối thiểu 15 phút hoặc chồng lấn với phiên khác. Cần xếp lại lịch."
                )

    recent_runs = list(
        await db.scalars(
            select(SessionRun).where(
                SessionRun.user_id == user_uuid,
                SessionRun.session_id != session.id,
                SessionRun.ended_at.is_not(None),
                SessionRun.ended_at >= req_start - timedelta(seconds=MIN_INTER_SESSION_GAP_SECONDS),
            )
        )
    )
    for r_run in recent_runs:
        if r_run.ended_at is None:
            continue
        req_gap_sec = gap_override_by_other_id.get(
            r_run.session_id, MIN_INTER_SESSION_GAP_SECONDS
        )
        r_ended = r_run.ended_at.astimezone(UTC)
        if (req_start - r_ended).total_seconds() < req_gap_sec:
            raise _require_replan(
                "Thời điểm bắt đầu mới vi phạm khoảng cách tối thiểu 15 phút hoặc chồng lấn với phiên khác. Cần xếp lại lịch."
            )


@command_service.durable_command(
    "start_session_run", SessionRunData, resource_arg="session_id"
)
async def start_session_run(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    idempotency_key: str,
    payload: SessionStartRequest,
    now: datetime | None = None,
) -> SessionRunData:
    """Dòng 16 trên API Sheet (POST /api/v1/sessions/{session_id}/start - HTTP 201)."""
    if now is not None and now.utcoffset() is None:
        raise _validation_error("Mốc thời gian kiểm tra phải có múi giờ.", "now")

    if payload.requested_start_at.utcoffset() is None:
        raise _validation_error(
            "Thiếu mã thiết bị hoặc thời điểm bắt đầu hợp lệ.", "requested_start_at"
        )

    server_now = _current_utc_now(now)
    skew = abs(
        (payload.requested_start_at.astimezone(UTC) - server_now).total_seconds()
    )
    if skew > MAX_CLOCK_SKEW_SECONDS:
        raise _validation_error(
            "Thiếu mã thiết bị hoặc thời điểm bắt đầu hợp lệ.", "requested_start_at"
        )

    user_uuid = command_service.owner_uuid(user_id)
    session_uuid = _parse_uuid(session_id)
    req_start = payload.requested_start_at.astimezone(UTC)
    effective_now = server_now

    # Đối soát lazy timeout cho các Run awaiting_checkin đã quá hạn trong cùng transaction
    await reconcile_expired_checkins(db, user_id, now=effective_now, commit_if_read=False)

    # 1. Kiểm tra Session tồn tại và thuộc user_id
    session = await db.scalar(
        select(SessionRow)
        .where(SessionRow.id == session_uuid, SessionRow.user_id == user_uuid)
        .execution_options(populate_existing=True)
    )
    if session is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    step = await db.scalar(
        select(StepRow).where(StepRow.id == session.step_id, StepRow.user_id == user_uuid)
    )
    task = (
        await db.scalar(
            select(TaskRow).where(TaskRow.id == step.task_id, TaskRow.user_id == user_uuid)
        )
        if step is not None
        else None
    )
    if step is None or task is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    # 1b. Kiểm tra cửa sổ bắt đầu: chặn bắt đầu quá sớm ngoài Grace Window (sớm hơn 15 phút)
    early_bound = session.scheduled_start.astimezone(UTC) - timedelta(seconds=START_GRACE_SECONDS)
    if req_start < early_bound:
        raise _require_replan(
            "Thời điểm bắt đầu quá sớm so với lịch hẹn (vượt quá 15 phút). Cần xếp lại lịch."
        )

    # 2. Kiểm tra expected_schedule_revision khớp với ScheduleVersion hiện hành hoặc session.schedule_revision
    max_schedule_ver = await db.scalar(
        select(func.max(ScheduleVersionRow.version_number)).where(
            ScheduleVersionRow.user_id == user_uuid
        )
    )
    authoritative_schedule_rev = (
        max_schedule_ver if max_schedule_ver is not None else session.schedule_revision
    )
    if payload.expected_schedule_revision not in (
        session.schedule_revision,
        authoritative_schedule_rev,
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Dữ liệu đã thay đổi so với phiên bản gửi lên. Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới.",
            current_revisions={"schedule": authoritative_schedule_rev},
            next_action="reload_and_review",
        )

    # 3. Kiểm tra không có live run (run_state IN ('running', 'paused'))
    live_run = await run_repository.get_live_run(db, user_uuid)
    if live_run is not None:
        raise AppError(
            status_code=409,
            code="ACTIVE_RUN_EXISTS",
            message="Đang có phiên chính khác chạy. Hãy mở lại phiên đó hoặc kết thúc hợp lệ trước.",
            next_action="open_active_run",
        )

    # 4. Kiểm tra tính khả thi trên toàn khoảng [requested_start_at, requested_start_at + duration_seconds]
    await _validate_start_feasibility(
        db,
        user_uuid=user_uuid,
        session=session,
        step=step,
        task=task,
        req_start=req_start,
    )

    # 5. Đột biến trạng thái nguyên tử khi Start hợp lệ
    session.status = "in_progress"
    session.start_state = "started"
    due_at: datetime = session.start_decision_due_at or (
        session.scheduled_start + timedelta(seconds=START_GRACE_SECONDS)
    )
    session.start_decision_due_at = due_at

    pref = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == user_uuid)
    )
    effective_revision = pref.effective_revision if pref is not None else 1
    link_id = await db.scalar(
        select(CalendarEventLink.id).where(
            CalendarEventLink.user_id == user_uuid,
            CalendarEventLink.session_id == session.id,
            CalendarEventLink.is_current.is_(True),
        )
    )
    segments = [SessionSegment.model_validate(seg) for seg in session.segments]

    session_snapshot = SessionSnapshot(
        session_id=str(session.id),
        step_id=str(session.step_id),
        scheduled_start=session.scheduled_start,
        scheduled_end=session.scheduled_end,
        micro_task_text=session.micro_task_text or "Chuẩn bị cho phiên làm việc",
        micro_task_seconds=session.micro_task_seconds,
        work_seconds=session.work_seconds,
        rest_seconds=session.rest_seconds,
        segments=segments,
        duration_seconds=session.duration_seconds,
        schedule_revision=session.schedule_revision,
        status=session.status,
        calendar_event_link_id=str(link_id) if link_id else None,
        start_state=session.start_state,
        start_decision_due_at=due_at,
    )
    settings_snapshot = SettingsSnapshot(
        effective_revision=effective_revision,
        session=session_snapshot,
    )

    run = SessionRun(
        user_id=user_uuid,
        session_id=session.id,
        device_id=payload.device_id,
        started_at=payload.requested_start_at,
        ended_at=None,
        run_state="running",
        active_phase="work",
        segment_index=0,
        work_elapsed_seconds=0,
        rest_elapsed_seconds=0,
        actual_work_seconds=None,
        actual_source=None,
        outcome="unknown",
        checkin_timeout_at=None,
        settings_snapshot=settings_snapshot.model_dump(mode="json"),
        dnd_release_at=None,
    )
    db.add(run)

    # Hủy các Reminder chưa phát của session_id này
    await db.execute(
        update(Reminder)
        .where(
            Reminder.user_id == user_uuid,
            Reminder.session_id == session.id,
            Reminder.delivery_state.in_(("scheduled", "snoozed")),
        )
        .values(delivery_state="cancelled")
        .execution_options(synchronize_session="fetch")
    )

    await db.flush()
    return await _run_to_schema(
        db,
        run,
        settings_snapshot=settings_snapshot,
        check_in=None,
    )


async def get_active_run(
    db: AsyncSession,
    *,
    user_id: str,
    now: datetime | None = None,
) -> SessionRunData | None:
    """Dòng 17 trên API Sheet (GET /api/v1/runs/active - HTTP 200)."""
    await reconcile_expired_checkins(db, user_id, now=now, commit_if_read=True)
    user_uuid = command_service.owner_uuid(user_id)
    run = await run_repository.get_live_run(db, user_uuid)
    if run is None:
        return None
    final_checkin = await run_repository.get_final_checkin(db, user_uuid, run.id)
    return await _run_to_schema(db, run, check_in=final_checkin)


async def get_run_detail(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    now: datetime | None = None,
) -> SessionRunData:
    """Dòng 18 trên API Sheet (GET /api/v1/runs/{run_id} - HTTP 200 / 404)."""
    await reconcile_expired_checkins(db, user_id, now=now, commit_if_read=True)
    user_uuid = command_service.owner_uuid(user_id)
    run_uuid = _parse_uuid(run_id)
    run = await run_repository.get_run(db, user_uuid, run_uuid)
    if run is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )
    final_checkin = await run_repository.get_final_checkin(db, user_uuid, run.id)
    return await _run_to_schema(db, run, check_in=final_checkin)


@command_service.durable_command(
    "record_run_events", RunEventBatchResult, resource_arg="run_id"
)
async def record_run_events(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    payload: RunEventBatchRequest,
    now: datetime | None = None,
) -> RunEventBatchResult:
    """Dòng 19 trên API Sheet (POST /api/v1/runs/{run_id}/events - HTTP 200)."""
    user_uuid = command_service.owner_uuid(user_id)
    run_uuid = _parse_uuid(run_id)
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

    if payload.device_id != run.device_id:
        raise AppError(
            status_code=409,
            code="DEVICE_CONFLICT",
            message="Thiết bị đồng bộ sự kiện không khớp với thiết bị đang chạy phiên.",
            next_action="reload_and_review",
        )

    if run.run_state not in ("running", "paused"):
        raise AppError(
            status_code=409,
            code="INVALID_RUN_STATE",
            message="Phiên tập trung không còn ở trạng thái cho phép nhận sự kiện.",
            next_action="reload_and_review",
        )

    snap = await _resolve_settings_snapshot(db, run)
    segments = snap.session.segments
    num_segments = len(segments)
    started_utc = run.started_at.astimezone(UTC)
    server_now = _current_utc_now(now)

    last_saved_occurred_at = await db.scalar(
        select(func.max(RunEvent.occurred_at)).where(
            RunEvent.user_id == user_uuid,
            RunEvent.run_id == run.id,
        )
    )
    last_occ_utc = (last_saved_occurred_at or run.started_at).astimezone(UTC)

    client_ids = [ev.event_id for ev in payload.events]
    existing_events = await run_repository.get_events_by_client_ids(db, user_uuid, client_ids)
    seen_in_batch: dict[str, dict[str, Any]] = {}

    accepted_event_ids: list[str] = []
    duplicate_event_ids: list[str] = []

    curr_work = run.work_elapsed_seconds
    curr_rest = run.rest_elapsed_seconds
    curr_seg_idx = run.segment_index if run.segment_index is not None else 0
    curr_state = run.run_state
    curr_phase = run.active_phase

    for idx, ev in enumerate(payload.events):
        if ev.occurred_at.utcoffset() is None:
            raise _validation_error(
                "Danh sách sự kiện timer thiếu mã hoặc thời điểm hợp lệ.",
                f"events.{idx}.occurred_at",
            )
        if ev.event_type not in ALLOWED_RUN_EVENT_TYPES:
            raise _validation_error(
                "Loại sự kiện timer không hợp lệ.",
                f"events.{idx}.event_type",
            )
        if not (0 <= ev.segment_index < num_segments):
            raise _validation_error(
                "Chỉ số quãng trong phiên không hợp lệ.",
                f"events.{idx}.segment_index",
            )
        occ_utc = ev.occurred_at.astimezone(UTC)
        normalized_payload = {
            "event_type": ev.event_type,
            "occurred_at": occ_utc.isoformat(),
            "segment_index": ev.segment_index,
            "work_elapsed_seconds": ev.work_elapsed_seconds,
            "rest_elapsed_seconds": ev.rest_elapsed_seconds,
        }

        if ev.event_id in existing_events:
            row = existing_events[ev.event_id]
            row_p = row.payload or {}
            row_occ = _parse_iso_datetime(row.occurred_at)
            if (
                row.run_id == run.id
                and row.event_type == ev.event_type
                and row_occ == occ_utc
                and row_p.get("segment_index") == ev.segment_index
                and row_p.get("work_elapsed_seconds") == ev.work_elapsed_seconds
                and row_p.get("rest_elapsed_seconds") == ev.rest_elapsed_seconds
            ):
                duplicate_event_ids.append(ev.event_id)
                continue
            raise AppError(
                status_code=409,
                code="CLIENT_EVENT_CONFLICT",
                message="Mã sự kiện đã được dùng cho phiên khác hoặc nội dung khác.",
                next_action="reload_and_review",
            )

        if ev.event_id in seen_in_batch:
            if seen_in_batch[ev.event_id] == normalized_payload:
                duplicate_event_ids.append(ev.event_id)
                continue
            raise AppError(
                status_code=409,
                code="CLIENT_EVENT_CONFLICT",
                message="Mã sự kiện bị lặp với nội dung mâu thuẫn trong cùng mẻ.",
                next_action="reload_and_review",
            )

        if occ_utc < started_utc or occ_utc < last_occ_utc:
            raise _validation_error(
                "Thời điểm sự kiện timer không được lùi so với sự kiện trước.",
                f"events.{idx}.occurred_at",
            )
        if occ_utc > server_now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
            raise _validation_error(
                "Thời điểm sự kiện vượt quá giới hạn sai lệch đồng hồ cho phép.",
                f"events.{idx}.occurred_at",
            )
        if ev.segment_index < curr_seg_idx:
            raise _validation_error(
                "Chỉ số quãng trong phiên không được lùi so với quãng hiện tại.",
                f"events.{idx}.segment_index",
            )

        wall_elapsed = max(0, int((occ_utc - started_utc).total_seconds()))
        max_reasonable = wall_elapsed + MAX_CLOCK_SKEW_SECONDS
        if (
            ev.work_elapsed_seconds < 0
            or ev.rest_elapsed_seconds < 0
            or (ev.work_elapsed_seconds + ev.rest_elapsed_seconds) > max_reasonable
        ):
            raise _validation_error(
                "Số giây tích lũy của sự kiện không hợp lệ.",
                f"events.{idx}.work_elapsed_seconds",
            )

        if curr_state == "paused":
            if ev.event_type == "progress" or (
                ev.event_type != "resume"
                and (
                    ev.work_elapsed_seconds > curr_work
                    or ev.rest_elapsed_seconds > curr_rest
                    or ev.segment_index != curr_seg_idx
                )
            ):
                raise AppError(
                    status_code=409,
                    code="INVALID_RUN_STATE",
                    message="Phiên đang tạm dừng, không thể cập nhật tiến trình làm việc.",
                    next_action="reload_and_review",
                )

        if ev.work_elapsed_seconds < curr_work or ev.rest_elapsed_seconds < curr_rest:
            raise _validation_error(
                "Bộ đếm thời gian làm/nghỉ không được lùi so với giá trị đã lưu.",
                f"events.{idx}.work_elapsed_seconds",
            )

        curr_work = ev.work_elapsed_seconds
        curr_rest = ev.rest_elapsed_seconds
        curr_seg_idx = ev.segment_index
        last_occ_utc = occ_utc
        seg_kind = segments[ev.segment_index].kind
        if ev.event_type == "pause":
            curr_state = "paused"
            curr_phase = seg_kind
        elif ev.event_type == "resume":
            curr_state = "running"
            curr_phase = seg_kind
        else:
            curr_phase = seg_kind

        db.add(
            RunEvent(
                user_id=user_uuid,
                run_id=run.id,
                client_event_id=ev.event_id,
                event_type=ev.event_type,
                occurred_at=ev.occurred_at,
                payload=normalized_payload,
            )
        )
        seen_in_batch[ev.event_id] = normalized_payload
        accepted_event_ids.append(ev.event_id)

    if accepted_event_ids:
        run.work_elapsed_seconds = curr_work
        run.rest_elapsed_seconds = curr_rest
        run.segment_index = curr_seg_idx
        run.run_state = curr_state
        run.active_phase = curr_phase
        run.updated_at = datetime.now(UTC)
        await db.flush()

    return RunEventBatchResult(
        run_id=str(run.id),
        run_revision=run.revision,
        accepted_event_ids=accepted_event_ids,
        duplicate_event_ids=duplicate_event_ids,
    )


@command_service.durable_command(
    "end_session_run", SessionRunData, resource_arg="run_id"
)
async def end_session_run(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    payload: RunEndRequest,
    now: datetime | None = None,
) -> SessionRunData:
    """Dòng 20 trên API Sheet (POST /api/v1/runs/{run_id}/end - HTTP 200)."""
    if payload.ended_at.utcoffset() is None:
        raise _validation_error("Chưa có thời điểm kết thúc hợp lệ của phiên.", "ended_at")

    user_uuid = command_service.owner_uuid(user_id)
    run_uuid = _parse_uuid(run_id)
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

    if payload.device_id != run.device_id:
        raise AppError(
            status_code=409,
            code="DEVICE_CONFLICT",
            message="Thiết bị kết thúc phiên không khớp với thiết bị đang chạy phiên.",
            next_action="reload_and_review",
        )

    if run.run_state not in ("running", "paused"):
        raise AppError(
            status_code=409,
            code="INVALID_RUN_STATE",
            message="Phiên tập trung đã kết thúc hoặc không ở trạng thái cho phép kết thúc.",
            next_action="reload_and_review",
        )

    server_now = _current_utc_now(now)
    ended_utc = payload.ended_at.astimezone(UTC)
    started_utc = run.started_at.astimezone(UTC)
    if (
        ended_utc < started_utc
        or ended_utc > server_now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
    ):
        raise _validation_error("Chưa có thời điểm kết thúc hợp lệ của phiên.", "ended_at")

    last_event_at = await db.scalar(
        select(func.max(RunEvent.occurred_at)).where(
            RunEvent.user_id == user_uuid,
            RunEvent.run_id == run.id,
        )
    )
    if last_event_at is not None and ended_utc < last_event_at.astimezone(UTC):
        raise _validation_error("Chưa có thời điểm kết thúc hợp lệ của phiên.", "ended_at")

    if (
        payload.work_elapsed_seconds < run.work_elapsed_seconds
        or payload.rest_elapsed_seconds < run.rest_elapsed_seconds
    ):
        raise _validation_error(
            "Bộ đếm thời gian làm/nghỉ khi kết thúc không được lùi so với giá trị đã lưu.",
            "work_elapsed_seconds",
        )

    wall_elapsed = max(0, int((ended_utc - started_utc).total_seconds()))
    max_reasonable = wall_elapsed + MAX_CLOCK_SKEW_SECONDS
    if (payload.work_elapsed_seconds + payload.rest_elapsed_seconds) > max_reasonable:
        raise _validation_error(
            "Thời lượng tích lũy vượt quá giới hạn hợp lý của phiên.",
            "work_elapsed_seconds",
        )

    existing_events = await run_repository.get_events_by_client_ids(
        db, user_uuid, [payload.event_id]
    )
    if payload.event_id in existing_events:
        raise AppError(
            status_code=409,
            code="CLIENT_EVENT_CONFLICT",
            message="Mã sự kiện kết thúc đã được sử dụng.",
            next_action="reload_and_review",
        )

    db.add(
        RunEvent(
            user_id=user_uuid,
            run_id=run.id,
            client_event_id=payload.event_id,
            event_type="end",
            occurred_at=payload.ended_at,
            payload={
                "end_reason": payload.end_reason,
                "work_elapsed_seconds": payload.work_elapsed_seconds,
                "rest_elapsed_seconds": payload.rest_elapsed_seconds,
            },
        )
    )

    timeout_at = payload.ended_at + timedelta(seconds=CHECKIN_TIMEOUT_SECONDS)
    run.ended_at = payload.ended_at
    run.segment_index = None
    run.active_phase = "ended"
    run.run_state = "awaiting_checkin"
    run.work_elapsed_seconds = payload.work_elapsed_seconds
    run.rest_elapsed_seconds = payload.rest_elapsed_seconds
    run.actual_work_seconds = payload.work_elapsed_seconds
    run.actual_source = "timer_proxy"
    run.outcome = "unknown"
    run.checkin_timeout_at = timeout_at
    run.dnd_release_at = timeout_at
    run.updated_at = datetime.now(UTC)

    # Đối soát trường hợp đồng bộ offline muộn: tính trạng thái timeout (ended_at + 300s)
    # nhất quán theo clock nội bộ/now, không giới hạn cửa sổ 1 giờ.
    ref_now = _current_utc_now(now)
    initial_response_state = (
        "no_respond" if ref_now >= timeout_at.astimezone(UTC) else "open"
    )

    checkin = CheckIn(
        user_id=user_uuid,
        run_id=run.id,
        kind="final",
        response_state=initial_response_state,
        session_outcome="unknown",
        step_completed=None,
        task_completed=None,
        skip_scope=None,
        remaining_note=None,
        asked_at=payload.ended_at,
        responded_at=None,
        response_revision=0,
    )
    db.add(checkin)
    await db.flush()

    return await _run_to_schema(
        db,
        run,
        check_in=checkin,
    )


@command_service.durable_command(
    "record_actual_correction", ActualCorrectionData, resource_arg="run_id"
)
async def record_actual_correction(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    payload: ActualCorrectionRequest,
    now: datetime | None = None,
) -> ActualCorrectionData:
    """Dòng 32 trên API Sheet (POST /api/v1/runs/{run_id}/actual-corrections - HTTP 200)."""
    user_uuid = command_service.owner_uuid(user_id)
    run_uuid = _parse_uuid(run_id)
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

    if run.ended_at is None:
        raise AppError(
            status_code=409,
            code="INVALID_RUN_STATE",
            message="Chỉ được phép hiệu chỉnh thời gian làm thực tế cho phiên đã kết thúc.",
            next_action="reload_and_review",
        )

    reason = payload.reason.strip()
    if not reason:
        raise _validation_error("Lý do hiệu chỉnh không được để trống.", "reason")

    server_now = _current_utc_now(now)
    occurred_utc = payload.occurred_at.astimezone(UTC)
    ended_utc = run.ended_at.astimezone(UTC)
    if (
        occurred_utc < ended_utc
        or occurred_utc > server_now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
    ):
        raise _validation_error(
            "Thời gian thực tế tự báo cáo không hợp lệ.",
            "occurred_at",
        )

    snap = await _resolve_settings_snapshot(db, run)
    wall_elapsed = max(0, int((ended_utc - run.started_at.astimezone(UTC)).total_seconds()))
    max_allowed = min(
        86400, max(snap.session.duration_seconds * 4, wall_elapsed + MAX_CLOCK_SKEW_SECONDS)
    )
    if payload.self_report_work_seconds > max_allowed:
        raise _validation_error(
            "Thời lượng thực tế vượt quá giới hạn hợp lý.",
            "self_report_work_seconds",
        )

    correction = ActualCorrection(
        user_id=user_uuid,
        run_id=run.id,
        previous_actual_work_seconds=run.actual_work_seconds,
        previous_actual_source=run.actual_source,
        actual_work_seconds=payload.self_report_work_seconds,
        actual_source="self_report",
        reason=reason,
    )
    db.add(correction)

    run.actual_work_seconds = payload.self_report_work_seconds
    run.actual_source = "self_report"
    run.updated_at = datetime.now(UTC)
    await db.flush()

    return ActualCorrectionData(
        run_id=str(run.id),
        run_revision=run.revision,
        actual_work_seconds=payload.self_report_work_seconds,
        actual_source="self_report",
        work_elapsed_seconds=run.work_elapsed_seconds,
    )
