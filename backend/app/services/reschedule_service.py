"""Dịch vụ điều phối Tái lập lịch Thích ứng (UF6: W3-10).

Triển khai đầy đủ 7 endpoints theo API Sheet (Dòng 64 và Dòng 33–38) và đặc tả R04, R05, R06, R10:
1. GET  /api/v1/runs/{run_id}/upcoming-schedule-context (Dòng 64)
2. POST /api/v1/proposals                               (Dòng 33 - Normal & Continuation SY02/R04)
3. GET  /api/v1/proposals/current                       (Dòng 34)
4. GET  /api/v1/proposals/{proposal_id}                 (Dòng 35)
5. POST /api/v1/proposals/{proposal_id}/rebuild         (Dòng 36)
6. POST /api/v1/proposals/{proposal_id}/approve         (Dòng 37 - Atomic commit + R05 envelope guard)
7. POST /api/v1/proposals/{proposal_id}/reject          (Dòng 38)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.execution import RunEvent, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.settings import SettingChangeRequest
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.common import (
    BlockingReason,
    FieldError,
    SessionSegment,
)
from app.schemas.execution import SessionSnapshot, SettingsSnapshot
from app.schemas.reschedule import (
    ApprovalEnvelope,
    ApproveProposalData,
    ApproveProposalRequest,
    ContinuationRequest,
    CoverageWindow,
    CreateProposalRequest,
    CurrentStepBrief,
    GapException,
    LimitAssumptions,
    ProposalData,
    ProposalFeasibilityData,
    ProposalGenerationTier,
    ProposalScheduleVersionData,
    ProposalSessionData,
    ProposalSourceRevisions,
    ProposalStatus,
    ProposalTimeRange,
    ProposedChanges,
    ProposedSessionAddAfter,
    ProposedSessionAddItem,
    ProposedSessionMoveItem,
    RebuildProposalRequest,
    RejectProposalData,
    RejectProposalRequest,
    ShiftedSessionBound,
    StartWindow,
    TimelineGoogleBusyItem,
    TimelineItem,
    TimelineSessionItem,
    UpcomingScheduleContextData,
)
from app.services import command_service, draft_service, run_service
from app.services.ai_service import ai_service

STANDARD_INTER_SESSION_GAP_SECONDS = 900
SourceRevisions = ProposalSourceRevisions
FeasibilityData = ProposalFeasibilityData
GenerationTier = ProposalGenerationTier

_UF6_REASON_MAP: dict[str, str] = {
    "checkin_busy": "busy",
    "busy": "busy",
    "checkin_incomplete": "incomplete",
    "incomplete": "incomplete",
    "partial": "incomplete",
    "checkin_skipped": "skipped",
    "skipped": "skipped",
    "settings_change": "setting_changed",
    "setting_changed": "setting_changed",
    "early_completion_pull_forward": "setting_changed",
    "missed_start": "deadline_risk",
    "user_reschedule": "deadline_risk",
    "expand_next_horizon": "deadline_risk",
    "deadline_risk": "deadline_risk",
}


async def _generate_uf6_explanation_and_tier(
    trigger_reason: str,
    feasibility: FeasibilityData,
) -> tuple[str, GenerationTier]:
    """Sinh lời giải thích Guilt-free UF6 qua AI Service (fallback Python an toàn)."""
    mapped_reason = _UF6_REASON_MAP.get(
        (trigger_reason or "").strip().lower(),
        "deadline_risk",
    )
    deficit_secs = feasibility.deficit_seconds or 0
    deficit_minutes = (deficit_secs + 59) // 60 if deficit_secs > 0 else None
    proposal_available = feasibility.status in ("feasible", "valid") and feasibility.confirmable

    empathy = await ai_service.generate_reschedule_empathy(
        trigger_reason=mapped_reason,
        task_title=None,
        proposal_available=proposal_available,
        deficit_minutes=deficit_minutes,
    )
    parts = [empathy.empathetic_note.strip()]
    if feasibility.status == "insufficient_data":
        parts.append("Mình giữ nguyên lịch cũ và cần bạn xác nhận lại giờ rảnh trước khi xếp lịch mới.")
    if empathy.encouragement_tip and empathy.encouragement_tip.strip():
        parts.append(empathy.encouragement_tip.strip())
    explanation = " ".join(part for part in parts if part)
    return explanation, empathy.generation_tier
ScheduleVersionData = ProposalScheduleVersionData
SessionData = ProposalSessionData

IMMEDIATE_CONTINUATION_GAP_SECONDS = 120
MAX_BORROW_BREAK_SECONDS = 120
MIN_BORROWED_GAP_SECONDS = STANDARD_INTER_SESSION_GAP_SECONDS - MAX_BORROW_BREAK_SECONDS  # 780s
START_GRACE_SECONDS = 300


def _current_utc_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def _ensure_aware(dt: datetime) -> datetime:
    return dt if dt.utcoffset() is not None else dt.replace(tzinfo=UTC)


def _parse_iso_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return _ensure_aware(parsed)
        except ValueError:
            return None
    return None


def _uuid_or_404(raw_id: str, message: str = "Không tìm thấy tài nguyên.") -> UUID:
    try:
        return UUID(str(raw_id))
    except (ValueError, TypeError) as exc:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message=message,
            next_action="reload",
        ) from exc


def _validation_error(message: str, field_path: str = "body") -> AppError:
    return AppError(
        status_code=422,
        code="VALIDATION_ERROR",
        message=message,
        field_errors=[FieldError(field_path=field_path, message=message)],
        next_action="fix_fields",
    )


async def _get_current_schedule_revision(db: AsyncSession, owner: UUID) -> int:
    max_ver = await db.scalar(
        select(ScheduleVersionRow.version_number)
        .where(ScheduleVersionRow.user_id == owner)
        .order_by(ScheduleVersionRow.version_number.desc())
        .limit(1)
    )
    if max_ver is not None:
        return int(max_ver)
    max_sess_rev = await db.scalar(
        select(SessionRow.schedule_revision)
        .where(SessionRow.user_id == owner)
        .order_by(SessionRow.schedule_revision.desc())
        .limit(1)
    )
    return int(max_sess_rev) if max_sess_rev is not None else 0


async def _compute_db_source_revisions(
    db: AsyncSession,
    owner: UUID,
    *,
    target_task_id: str | None = None,
    allowed_task_ids: list[str] | None = None,
    fallback_revisions: SourceRevisions | None = None,
) -> tuple[SourceRevisions, dict[str, bool]]:
    """Đọc các revision nguồn thực tế từ DB và đánh dấu trường nào có bản ghi thực trong DB."""
    present: dict[str, bool] = {
        "task": False,
        "schedule": False,
        "availability": False,
        "settings": False,
        "calendar": False,
    }

    # 1. Schedule revision
    ver_row = await db.scalar(
        select(ScheduleVersionRow.version_number)
        .where(ScheduleVersionRow.user_id == owner)
        .order_by(ScheduleVersionRow.version_number.desc())
        .limit(1)
    )
    sess_rev = await db.scalar(
        select(SessionRow.schedule_revision)
        .where(SessionRow.user_id == owner)
        .order_by(SessionRow.schedule_revision.desc())
        .limit(1)
    )
    if ver_row is not None or sess_rev is not None:
        present["schedule"] = True
        schedule_rev = max(int(ver_row or 0), int(sess_rev or 0))
    else:
        schedule_rev = fallback_revisions.schedule if fallback_revisions else 0

    # 2. Task revision
    task_rev: int | None = None
    if target_task_id:
        try:
            t_uuid = UUID(str(target_task_id))
            t_row = await db.scalar(
                select(TaskRow).where(TaskRow.id == t_uuid, TaskRow.user_id == owner)
            )
            if t_row is not None:
                present["task"] = True
                task_rev = int(t_row.revision)
        except ValueError:
            pass
    if task_rev is None and allowed_task_ids:
        valid_uuids: list[UUID] = []
        for tid in allowed_task_ids:
            try:
                valid_uuids.append(UUID(str(tid)))
            except ValueError:
                continue
        if valid_uuids:
            t_rows = (
                await db.scalars(
                    select(TaskRow).where(
                        TaskRow.user_id == owner,
                        TaskRow.id.in_(valid_uuids),
                    )
                )
            ).all()
            if t_rows:
                present["task"] = True
                task_rev = max(int(r.revision) for r in t_rows)
    if task_rev is None:
        latest_task_rev = await db.scalar(
            select(TaskRow.revision)
            .where(TaskRow.user_id == owner)
            .order_by(TaskRow.revision.desc())
            .limit(1)
        )
        if latest_task_rev is not None:
            present["task"] = True
            task_rev = int(latest_task_rev)
        else:
            task_rev = fallback_revisions.task if fallback_revisions else 0

    # 3. Availability revision
    avail_row = await db.scalar(
        select(AvailabilityRow)
        .where(AvailabilityRow.user_id == owner)
        .order_by(AvailabilityRow.revision.desc())
        .limit(1)
    )
    if avail_row is not None:
        present["availability"] = True
        avail_rev = int(avail_row.revision) if avail_row.confirmed_at is not None else 0
    else:
        avail_rev = fallback_revisions.availability if fallback_revisions else 0

    # 4. Settings revision
    pref_row = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == owner)
    )
    if pref_row is not None:
        present["settings"] = True
        settings_rev = int(pref_row.effective_revision)
    else:
        settings_rev = fallback_revisions.settings if fallback_revisions else 1

    # 5. Calendar revision
    ctx_rev = await db.scalar(
        select(PlanningContext.revision)
        .where(PlanningContext.user_id == owner)
        .order_by(PlanningContext.created_at.desc())
        .limit(1)
    )
    conn_rev = await db.scalar(
        select(CalendarConnection.revision)
        .where(CalendarConnection.user_id == owner)
        .order_by(CalendarConnection.created_at.desc())
        .limit(1)
    )
    if ctx_rev is not None:
        present["calendar"] = True
        calendar_rev = int(ctx_rev)
    elif conn_rev is not None:
        present["calendar"] = True
        calendar_rev = int(conn_rev)
    else:
        calendar_rev = fallback_revisions.calendar if fallback_revisions else 0

    return (
        SourceRevisions(
            task=task_rev,
            schedule=schedule_rev,
            availability=avail_rev,
            settings=settings_rev,
            calendar=calendar_rev,
        ),
        present,
    )


async def _load_google_busy_items(
    db: AsyncSession,
    owner: UUID,
    from_time: datetime,
    to_time: datetime,
) -> tuple[list[TimelineGoogleBusyItem], Literal["ok", "stale", "unavailable"], datetime | None]:
    """Đọc các khoảng bận Google Calendar từ PlanningContext gần nhất của user."""
    ctx = await db.scalar(
        select(PlanningContext)
        .where(PlanningContext.user_id == owner)
        .order_by(PlanningContext.created_at.desc())
        .limit(1)
    )
    if ctx is None:
        conn = await db.scalar(
            select(CalendarConnection)
            .where(CalendarConnection.user_id == owner)
            .order_by(CalendarConnection.created_at.desc())
            .limit(1)
        )
        fetched_at = conn.last_successful_sync_at if conn is not None else None
        return [], "ok", fetched_at

    cal_status: Literal["ok", "stale", "unavailable"] = "ok"
    if ctx.coverage_state == "degraded" or ctx.sync_state in ("error", "failed"):
        cal_status = "unavailable"
    elif ctx.sync_state == "stale":
        cal_status = "stale"

    busy_items: list[TimelineGoogleBusyItem] = []
    seen_ids: set[str] = set()

    # Đọc từ event_snapshots hoặc busy_intervals trong PlanningContext
    raw_snapshots = ctx.event_snapshots if isinstance(ctx.event_snapshots, list) else []
    for idx, item in enumerate(raw_snapshots):
        if not isinstance(item, dict):
            continue
        start_dt = _parse_iso_dt(item.get("start") or item.get("start_at"))
        end_dt = _parse_iso_dt(item.get("end") or item.get("end_at"))
        if start_dt is None or end_dt is None:
            continue
        if end_dt <= from_time or start_dt >= to_time:
            continue
        ev_id = str(item.get("event_id") or item.get("google_event_id") or item.get("id") or f"gcal_event_{idx + 1}")
        if ev_id in seen_ids:
            continue
        seen_ids.add(ev_id)
        busy_items.append(
            TimelineGoogleBusyItem(
                kind="google_busy",
                event_id=ev_id,
                title=str(item.get("title") or item.get("summary") or "Busy"),
                start=start_dt,
                end=end_dt,
                is_locked=bool(item.get("is_locked", True)),
                is_movable=bool(item.get("is_movable", False)),
            )
        )

    raw_busy = ctx.busy_intervals if isinstance(ctx.busy_intervals, list) else []
    for idx, item in enumerate(raw_busy):
        if not isinstance(item, dict):
            continue
        start_dt = _parse_iso_dt(item.get("start") or item.get("start_at"))
        end_dt = _parse_iso_dt(item.get("end") or item.get("end_at"))
        if start_dt is None or end_dt is None:
            continue
        if end_dt <= from_time or start_dt >= to_time:
            continue
        ev_id = str(
            item.get("event_id")
            or item.get("source_event_id")
            or item.get("id")
            or f"gcal_busy_{idx + 1}"
        )
        if ev_id in seen_ids:
            continue
        seen_ids.add(ev_id)
        busy_items.append(
            TimelineGoogleBusyItem(
                kind="google_busy",
                event_id=ev_id,
                title=str(item.get("title") or item.get("summary") or "Busy"),
                start=start_dt,
                end=end_dt,
                is_locked=True,
                is_movable=False,
            )
        )

    return busy_items, cal_status, ctx.fetched_at


def _compute_max_extension_without_borrow(
    continuation_start: datetime,
    upcoming_sessions: list[TimelineSessionItem],
    busy_items: list[TimelineGoogleBusyItem],
) -> tuple[int, list[int]]:
    """Tính số giây tối đa có thể làm thêm mà không cần mượn giờ nghỉ (gap = 900s)."""
    locked_points: list[datetime] = [b.start for b in busy_items if b.end > continuation_start]
    for s in upcoming_sessions:
        if not s.is_movable and s.scheduled_end > continuation_start:
            locked_points.append(s.scheduled_start)

    earliest_locked = min(locked_points) if locked_points else None
    movable_sessions = [
        s for s in upcoming_sessions if s.is_movable and s.scheduled_end > continuation_start
    ]

    if not movable_sessions and earliest_locked is None:
        max_ext = 3600
    elif not movable_sessions and earliest_locked is not None:
        avail_secs = int((earliest_locked - continuation_start).total_seconds())
        max_ext = max(0, min(3600, avail_secs - STANDARD_INTER_SESSION_GAP_SECONDS))
    else:
        # Tính latest_start của phiên movable đầu tiên trước rào chắn cố định gần nhất
        first_sess = movable_sessions[0]
        if earliest_locked is not None and earliest_locked > first_sess.scheduled_start:
            # Các phiên movable nằm trước earliest_locked
            squeezed = [
                s for s in movable_sessions if s.scheduled_start < earliest_locked
            ]
            total_squeezed_dur = sum(s.duration_seconds for s in squeezed)
            inter_gaps = max(0, len(squeezed) - 1) * STANDARD_INTER_SESSION_GAP_SECONDS
            first_latest_start = earliest_locked - timedelta(seconds=total_squeezed_dur + inter_gaps)
        else:
            # Nếu không bị chặn bởi sự kiện cố định gần đó, cho phép dịch tối đa 60 phút
            first_latest_start = first_sess.scheduled_start + timedelta(seconds=3600)

        avail_before_first = int((first_latest_start - continuation_start).total_seconds())
        max_ext = max(0, min(3600, avail_before_first - STANDARD_INTER_SESSION_GAP_SECONDS))

    candidates = [x for x in (300, 600, 900, 1200, 1500) if x <= max_ext]
    if max_ext >= 60 and max_ext not in candidates:
        if len(candidates) < 2:
            candidates.append(max_ext)
            candidates.sort()
        else:
            candidates = [candidates[0], max_ext]
    return max_ext, candidates


async def get_upcoming_schedule_context(
    db: AsyncSession,
    *,
    user_id: str,
    run_id: str,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> UpcomingScheduleContextData:
    """Dòng 64 trên API Sheet: GET /api/v1/runs/{run_id}/upcoming-schedule-context."""
    calc_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)
    run_uuid = _uuid_or_404(run_id, "Không tìm thấy lượt chạy phiên tập trung.")

    await run_service.reconcile_expired_checkins(db, user_id, now=calc_now, commit_if_read=True)

    run = await db.scalar(
        select(SessionRun).where(SessionRun.id == run_uuid, SessionRun.user_id == owner)
    )
    if run is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy lượt chạy phiên tập trung.",
            next_action="reload",
        )

    if from_time is not None and from_time.utcoffset() is None:
        raise _validation_error("Tham số from phải có múi giờ (ISO-8601).", "from")
    if to_time is not None and to_time.utcoffset() is None:
        raise _validation_error("Tham số to phải có múi giờ (ISO-8601).", "to")

    base_ref = _ensure_aware(run.ended_at or run.started_at or calc_now)
    window_from = _ensure_aware(from_time) if from_time is not None else base_ref
    window_to = _ensure_aware(to_time) if to_time is not None else (window_from + timedelta(hours=8))

    if window_from >= window_to:
        raise _validation_error("Khoảng thời gian truy vấn không hợp lệ (from phải nhỏ hơn to).", "to")

    offset = 0
    if cursor is not None:
        try:
            offset = int(cursor)
            if offset < 0:
                raise ValueError
        except ValueError as exc:
            raise _validation_error("Giá trị cursor không hợp lệ.", "cursor") from exc

    session_row = await db.scalar(
        select(SessionRow).where(SessionRow.id == run.session_id, SessionRow.user_id == owner)
    )
    step_row = (
        await db.scalar(
            select(StepRow).where(StepRow.id == session_row.step_id, StepRow.user_id == owner)
        )
        if session_row is not None
        else None
    )
    task_row = (
        await db.scalar(
            select(TaskRow).where(TaskRow.id == step_row.task_id, TaskRow.user_id == owner)
        )
        if step_row is not None
        else None
    )

    if session_row is None or step_row is None or task_row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy thông tin công việc hoặc bước của lượt chạy này.",
            next_action="reload",
        )

    revisions, _ = await _compute_db_source_revisions(
        db,
        owner,
        target_task_id=str(task_row.id),
    )

    avail_row = await db.scalar(
        select(AvailabilityRow)
        .where(AvailabilityRow.user_id == owner)
        .order_by(AvailabilityRow.revision.desc())
        .limit(1)
    )
    tz_name = avail_row.timezone if avail_row is not None and avail_row.timezone else "Asia/Ho_Chi_Minh"

    # Lấy các phiên sắp tới trong khoảng [window_from, window_to)
    upcoming_rows = (
        await db.scalars(
            select(SessionRow)
            .where(
                SessionRow.user_id == owner,
                SessionRow.id != run.session_id,
                SessionRow.status.in_(("scheduled", "in_progress")),
                SessionRow.scheduled_end > window_from,
                SessionRow.scheduled_start < window_to,
            )
            .order_by(SessionRow.scheduled_start.asc())
        )
    ).all()

    step_ids = list({r.step_id for r in upcoming_rows})
    steps_map: dict[UUID, StepRow] = {}
    tasks_map: dict[UUID, TaskRow] = {task_row.id: task_row}
    if step_ids:
        s_rows = (
            await db.scalars(
                select(StepRow).where(StepRow.user_id == owner, StepRow.id.in_(step_ids))
            )
        ).all()
        steps_map = {s.id: s for s in s_rows}
        t_ids = list({s.task_id for s in s_rows if s.task_id not in tasks_map})
        if t_ids:
            t_rows = (
                await db.scalars(
                    select(TaskRow).where(TaskRow.user_id == owner, TaskRow.id.in_(t_ids))
                )
            ).all()
            for t in t_rows:
                tasks_map[t.id] = t

    session_items: list[TimelineSessionItem] = []
    for r in upcoming_rows:
        st = steps_map.get(r.step_id)
        tk = tasks_map.get(st.task_id) if st is not None else None
        session_items.append(
            TimelineSessionItem(
                kind="session",
                session_id=str(r.id),
                task_id=str(tk.id) if tk is not None else str(task_row.id),
                task_title=tk.title if tk is not None else task_row.title,
                step_id=str(r.step_id),
                step_title=st.title if st is not None else step_row.title,
                scheduled_start=_ensure_aware(r.scheduled_start),
                scheduled_end=_ensure_aware(r.scheduled_end),
                duration_seconds=r.duration_seconds,
                status=r.status,
                is_movable=(r.status == "scheduled"),
            )
        )

    busy_items, cal_status, cal_fetched_at = await _load_google_busy_items(
        db, owner, window_from, window_to
    )

    combined: list[TimelineItem] = [*session_items, *busy_items]
    combined.sort(
        key=lambda item: item.scheduled_start
        if isinstance(item, TimelineSessionItem)
        else item.start
    )

    paged_timeline = combined[offset : offset + limit]
    next_cursor = str(offset + limit) if (offset + limit) < len(combined) else None

    cont_base = _ensure_aware(run.ended_at or calc_now)
    continuation_start = cont_base + timedelta(seconds=IMMEDIATE_CONTINUATION_GAP_SECONDS)
    max_ext, rec_ext = _compute_max_extension_without_borrow(
        continuation_start, session_items, busy_items
    )

    rem_secs = (
        step_row.remaining_base_seconds
        if step_row.remaining_base_seconds is not None
        else step_row.estimated_base_seconds
    )

    return UpcomingScheduleContextData(
        run_id=str(run.id),
        task_id=str(task_row.id),
        task_title=task_row.title,
        current_step=CurrentStepBrief(
            step_id=str(step_row.id),
            order_index=step_row.order_index,
            title=step_row.title,
            remaining_seconds=max(0, rem_secs),
            status=step_row.status,
        ),
        calculated_at=calc_now,
        source_run_revision=run.revision,
        source_revisions=revisions,
        calendar_status=cal_status,
        calendar_fetched_at=cal_fetched_at,
        coverage=CoverageWindow(
            **{
                "from": window_from,
                "to": window_to,
                "timezone": tz_name,
                "complete": next_cursor is None,
            }
        ),
        next_cursor=next_cursor,
        max_extension_seconds_without_borrow=max_ext,
        recommended_extension_seconds=rec_ext,
        limit_assumptions=LimitAssumptions(
            immediate_continuation=True,
            continuation_start=continuation_start,
            inter_session_gap_seconds=STANDARD_INTER_SESSION_GAP_SECONDS,
            borrow_break_seconds=0,
        ),
        timeline=paged_timeline,
    )


def _pack_proposed_changes(
    changes: ProposedChanges,
    *,
    calculated_at: datetime | None = None,
    continuation_request: ContinuationRequest | None = None,
    gap_exceptions: list[GapException] | None = None,
    start_window: StartWindow | None = None,
    approval_envelope: ApprovalEnvelope | None = None,
    request_text: str | None = None,
) -> dict[str, Any]:
    raw = changes.model_dump(mode="json")
    raw["_meta"] = {
        "calculated_at": calculated_at.isoformat() if calculated_at else None,
        "continuation_request": continuation_request.model_dump(mode="json")
        if continuation_request
        else None,
        "gap_exceptions": [g.model_dump(mode="json") for g in (gap_exceptions or [])],
        "start_window": start_window.model_dump(mode="json") if start_window else None,
        "approval_envelope": approval_envelope.model_dump(mode="json")
        if approval_envelope
        else None,
        "request_text": request_text,
    }
    return raw


def _unpack_proposal_meta(
    raw_changes: dict[str, Any],
) -> tuple[
    ProposedChanges,
    datetime | None,
    ContinuationRequest | None,
    list[GapException],
    StartWindow | None,
    ApprovalEnvelope | None,
    str | None,
]:
    clean_changes = {k: v for k, v in (raw_changes or {}).items() if not k.startswith("_")}
    meta = (raw_changes or {}).get("_meta") or {}

    changes = ProposedChanges.model_validate(clean_changes)
    calc_at = _parse_iso_dt(meta.get("calculated_at"))
    cont_req = (
        ContinuationRequest.model_validate(meta["continuation_request"])
        if meta.get("continuation_request")
        else None
    )
    gap_ex = [GapException.model_validate(item) for item in (meta.get("gap_exceptions") or [])]
    start_win = (
        StartWindow.model_validate(meta["start_window"]) if meta.get("start_window") else None
    )
    app_env = (
        ApprovalEnvelope.model_validate(meta["approval_envelope"])
        if meta.get("approval_envelope")
        else None
    )
    req_text = meta.get("request_text")
    return changes, calc_at, cont_req, gap_ex, start_win, app_env, req_text


async def _evaluate_proposal_staleness(
    db: AsyncSession,
    owner: UUID,
    proposal: RescheduleProposal,
    now: datetime,
) -> tuple[bool, SourceRevisions, int]:
    """Kiểm tra tính lỗi thời (stale) của RescheduleProposal theo thời gian thực `now` và nguồn hiện hành."""
    prop_revs = SourceRevisions.model_validate(proposal.source_revisions)
    current_sched_rev = await _get_current_schedule_revision(db, owner)
    db_revs, present = await _compute_db_source_revisions(
        db,
        owner,
        target_task_id=str(proposal.target_task_id) if proposal.target_task_id else None,
        allowed_task_ids=proposal.allowed_task_ids,
        fallback_revisions=prop_revs,
    )

    if proposal.status != "pending":
        return False, db_revs, current_sched_rev

    # 1. Lệch base_schedule_revision hoặc các revision nguồn có trong DB
    if present["schedule"] and current_sched_rev != proposal.base_schedule_revision:
        return True, db_revs, current_sched_rev
    if present["task"] and db_revs.task != prop_revs.task:
        return True, db_revs, current_sched_rev
    if present["availability"] and db_revs.availability != prop_revs.availability:
        return True, db_revs, current_sched_rev
    if present["settings"] and db_revs.settings != prop_revs.settings:
        return True, db_revs, current_sched_rev
    if present["calendar"] and db_revs.calendar != prop_revs.calendar:
        return True, db_revs, current_sched_rev

    # 2. Kiểm tra start_window và approval_envelope (cho Continuation SY02 / R05)
    changes, _, _, _, start_win, app_env, _ = _unpack_proposal_meta(proposal.proposed_changes)
    if start_win is not None:
        if now > _ensure_aware(start_win.not_after) or now > _ensure_aware(start_win.latest_start):
            return True, db_revs, current_sched_rev

    if app_env is not None:
        earliest = _ensure_aware(start_win.earliest_start) if start_win is not None else now
        actual_start = max(now, earliest)
        required_end_plus_break = actual_start + timedelta(
            seconds=app_env.continuation_fixed_work_seconds
            + app_env.continuation_min_break_after_seconds
        )
        for bound in app_env.shifted_sessions_bounds:
            if required_end_plus_break > _ensure_aware(bound.latest_start):
                return True, db_revs, current_sched_rev

    # 3. Kiểm tra các ô lịch đề xuất đã trôi qua thời điểm `now` (đóng/mở lại app ở UF6)
    if start_win is None:
        for mv in changes.sessions_to_move:
            target_start = mv.to_start or (mv.after.start if mv.after else None)
            if target_start is not None and _ensure_aware(target_start) < now:
                return True, db_revs, current_sched_rev
        for ad in changes.sessions_to_add:
            if _ensure_aware(ad.after.start) < now:
                return True, db_revs, current_sched_rev

    return False, db_revs, current_sched_rev


async def _proposal_row_to_schema(
    db: AsyncSession,
    owner: UUID,
    proposal: RescheduleProposal,
    now: datetime,
) -> ProposalData:
    changes, calc_at, cont_req, gap_ex, start_win, app_env, _ = _unpack_proposal_meta(
        proposal.proposed_changes
    )
    stale, _, _ = await _evaluate_proposal_staleness(db, owner, proposal, now)
    return ProposalData(
        proposal_id=str(proposal.id),
        status=cast(ProposalStatus, proposal.status),
        trigger_reason=proposal.trigger_reason,
        target_task_id=str(proposal.target_task_id) if proposal.target_task_id else None,
        allowed_task_ids=[str(x) for x in (proposal.allowed_task_ids or [])],
        affected_task_ids=[str(x) for x in (proposal.affected_task_ids or [])],
        base_schedule_revision=proposal.base_schedule_revision,
        source_revisions=SourceRevisions.model_validate(proposal.source_revisions),
        proposal_revision=proposal.proposal_revision,
        supersedes_proposal_id=str(proposal.supersedes_proposal_id)
        if proposal.supersedes_proposal_id
        else None,
        calculated_at=calc_at,
        continuation_request=cont_req,
        proposed_changes=changes,
        gap_exceptions=gap_ex,
        start_window=start_win,
        approval_envelope=app_env,
        feasibility=FeasibilityData.model_validate(proposal.feasibility),
        explanation=proposal.explanation,
        generation_tier=cast(GenerationTier, proposal.generation_tier),
        stale=stale,
        created_at=_ensure_aware(proposal.created_at),
        updated_at=_ensure_aware(proposal.updated_at),
    )


async def _check_continuation_chain_limit(
    db: AsyncSession,
    owner: UUID,
    source_run: SessionRun,
) -> None:
    """R04 Guard: Mỗi phiên gốc chỉ được phép xin Continuation tối đa 1 lần (chống mượn nối chuỗi)."""
    raw_snap = source_run.settings_snapshot or {}
    if isinstance(raw_snap, dict) and raw_snap.get("_continuation_of_run_id"):
        raise AppError(
            status_code=409,
            code="CONTINUATION_LIMIT_REACHED",
            message="Phiên hiện tại đã là phiên làm tiếp (continuation). Không được phép mượn nối chuỗi liên tiếp.",
            next_action="finish_and_take_break",
        )

    # Kiểm tra xem đã có lượt chạy continuation nào được tạo từ source_run.id chưa
    user_runs = (
        await db.scalars(select(SessionRun).where(SessionRun.user_id == owner))
    ).all()
    for r in user_runs:
        snap = r.settings_snapshot or {}
        if isinstance(snap, dict) and snap.get("_continuation_of_run_id") == str(source_run.id):
            raise AppError(
                status_code=409,
                code="CONTINUATION_LIMIT_REACHED",
                message="Phiên gốc này đã sử dụng quyền làm tiếp 1 lần. Không được phép xin làm tiếp lần thứ hai.",
                next_action="finish_and_take_break",
            )


async def _build_continuation_plan(
    db: AsyncSession,
    owner: UUID,
    *,
    cont_req: ContinuationRequest,
    allowed_task_ids: list[str],
    calc_now: datetime,
) -> tuple[
    UUID,
    list[str],
    ProposedChanges,
    list[GapException],
    StartWindow,
    ApprovalEnvelope,
    FeasibilityData,
    str,
]:
    """Tính toán phương án Continuation (SY02 / R04 / R05)."""
    run_uuid = _uuid_or_404(cont_req.source_run_id, "Không tìm thấy lượt chạy nguồn.")
    source_run = await db.scalar(
        select(SessionRun).where(SessionRun.id == run_uuid, SessionRun.user_id == owner)
    )
    if source_run is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy lượt chạy nguồn.",
            next_action="reload",
        )

    if source_run.revision != cont_req.expected_run_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản của lượt chạy nguồn đã thay đổi.",
            current_revisions={"run_revision": source_run.revision},
            next_action="reload_and_review",
        )

    if source_run.ended_at is None or source_run.run_state not in ("awaiting_checkin", "closed"):
        raise AppError(
            status_code=409,
            code="INVALID_RUN_STATE",
            message="Chỉ có thể tạo đề xuất làm tiếp sau khi phiên gốc đã kết thúc.",
            current_revisions={"run_revision": source_run.revision},
            next_action="end_run_first",
        )

    await _check_continuation_chain_limit(db, owner, source_run)

    source_session = await db.scalar(
        select(SessionRow).where(SessionRow.id == source_run.session_id, SessionRow.user_id == owner)
    )
    source_step = (
        await db.scalar(
            select(StepRow).where(StepRow.id == source_session.step_id, StepRow.user_id == owner)
        )
        if source_session is not None
        else None
    )
    source_task = (
        await db.scalar(
            select(TaskRow).where(TaskRow.id == source_step.task_id, TaskRow.user_id == owner)
        )
        if source_step is not None
        else None
    )
    if source_session is None or source_step is None or source_task is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy phiên hoặc bước của lượt chạy nguồn.",
            next_action="reload",
        )

    effective_allowed = list(
        dict.fromkeys(
            cont_req.allowed_task_ids
            if cont_req.allowed_task_ids is not None
            else (allowed_task_ids or [str(source_task.id)])
        )
    )
    if str(source_task.id) not in effective_allowed:
        effective_allowed.append(str(source_task.id))

    transition_gap = (
        IMMEDIATE_CONTINUATION_GAP_SECONDS
        if cont_req.immediate_continuation
        else STANDARD_INTER_SESSION_GAP_SECONDS
    )
    ended_at = _ensure_aware(source_run.ended_at)
    earliest_cont_start = ended_at + timedelta(seconds=transition_gap)
    cont_start = max(earliest_cont_start, calc_now)
    work_secs = cont_req.additional_work_seconds
    cont_end = cont_start + timedelta(seconds=work_secs)

    borrow_secs = cont_req.borrow_break_seconds
    required_after_gap = STANDARD_INTER_SESSION_GAP_SECONDS - borrow_secs  # >= 780s

    # Tải các phiên phía sau và sự kiện Google Calendar bận trong 24h tới
    horizon_end = cont_start + timedelta(hours=24)
    upcoming_sessions = (
        await db.scalars(
            select(SessionRow)
            .where(
                SessionRow.user_id == owner,
                SessionRow.id != source_session.id,
                SessionRow.status == "scheduled",
                SessionRow.scheduled_end > cont_start,
                SessionRow.scheduled_start < horizon_end,
            )
            .order_by(SessionRow.scheduled_start.asc())
        )
    ).all()

    busy_items, _, _ = await _load_google_busy_items(db, owner, cont_start, horizon_end)

    # Kiểm tra va chạm trực tiếp giữa phiên continuation mới và sự kiện lịch cố định
    for busy in busy_items:
        if cont_start < busy.end and cont_end > busy.start:
            raise AppError(
                status_code=409,
                code="LOCKED_CALENDAR_COLLISION",
                message="Phiên làm tiếp bị trùng với sự kiện cố định trên Google Calendar.",
                next_action="reduce_extension",
            )

    # Map step_id -> task_id cho các phiên sắp tới
    up_step_ids = list({s.step_id for s in upcoming_sessions})
    step_to_task: dict[UUID, UUID] = {source_step.id: source_task.id}
    if up_step_ids:
        s_rows = (
            await db.scalars(
                select(StepRow).where(StepRow.user_id == owner, StepRow.id.in_(up_step_ids))
            )
        ).all()
        for s in s_rows:
            step_to_task[s.id] = s.task_id

    temp_session_id = "temp_cont_session_1"
    moves: list[ProposedSessionMoveItem] = []
    bounds: list[ShiftedSessionBound] = []
    affected_tasks: list[str] = [str(source_task.id)]

    prev_end = cont_end
    for idx, sess in enumerate(upcoming_sessions):
        req_gap = required_after_gap if idx == 0 else STANDARD_INTER_SESSION_GAP_SECONDS
        s_start = _ensure_aware(sess.scheduled_start)
        s_end = _ensure_aware(sess.scheduled_end)
        min_allowed_start = prev_end + timedelta(seconds=req_gap)

        if s_start < min_allowed_start:
            sess_task_id = str(step_to_task.get(sess.step_id, source_task.id))
            if sess_task_id not in effective_allowed:
                raise AppError(
                    status_code=409,
                    code="UNALLOWED_TASK_COLLISION",
                    message="Không được phép tự ý dời lịch của công việc khác ngoài danh sách cho phép.",
                    next_action="expand_allowed_tasks_or_reduce_extension",
                )

            new_start = min_allowed_start
            new_end = new_start + timedelta(seconds=sess.duration_seconds)
            shift_secs = int((new_start - s_start).total_seconds())

            # Kiểm tra va chạm của phiên bị đẩy với sự kiện lịch cố định
            for busy in busy_items:
                if new_start < busy.end and new_end > busy.start:
                    raise AppError(
                        status_code=409,
                        code="LOCKED_CALENDAR_COLLISION",
                        message="Không thể dời phiên kế tiếp do vướng sự kiện cố định trên Google Calendar.",
                        next_action="reduce_extension",
                    )

            # Tìm sự kiện cố định gần nhất phía sau phiên này để tính latest_start
            later_busies = [b for b in busy_items if b.start >= new_end]
            if later_busies:
                nearest_busy = min(later_busies, key=lambda b: b.start)
                latest_sess_start = nearest_busy.start - timedelta(seconds=sess.duration_seconds)
                locked_event_id: str | None = nearest_busy.event_id
            else:
                latest_sess_start = new_start + timedelta(seconds=300)
                locked_event_id = None

            moves.append(
                ProposedSessionMoveItem(
                    session_id=str(sess.id),
                    task_id=sess_task_id,
                    step_id=str(sess.step_id),
                    from_start=s_start,
                    from_end=s_end,
                    to_start=new_start,
                    to_end=new_end,
                    before=ProposalTimeRange(start=s_start, end=s_end),
                    after=ProposalTimeRange(start=new_start, end=new_end),
                    shift_seconds=shift_secs,
                    reason="shift_after_continuation",
                )
            )
            bounds.append(
                ShiftedSessionBound(
                    session_id=str(sess.id),
                    earliest_start=new_start,
                    latest_start=latest_sess_start,
                    fixed_duration_seconds=sess.duration_seconds,
                    locked_after_event_id=locked_event_id,
                )
            )
            if sess_task_id not in affected_tasks:
                affected_tasks.append(sess_task_id)
            prev_end = new_end
        else:
            # Phiên kế tiếp không cần dịch chuyển nhưng vẫn đóng vai trò biên giới hạn
            sess_task_id = str(step_to_task.get(sess.step_id, source_task.id))
            later_busies = [b for b in busy_items if b.start >= s_end]
            if later_busies and idx == 0:
                nearest_busy = min(later_busies, key=lambda b: b.start)
                latest_sess_start = nearest_busy.start - timedelta(seconds=sess.duration_seconds)
                bounds.append(
                    ShiftedSessionBound(
                        session_id=str(sess.id),
                        earliest_start=s_start,
                        latest_start=latest_sess_start,
                        fixed_duration_seconds=sess.duration_seconds,
                        locked_after_event_id=nearest_busy.event_id,
                    )
                )
            break

    # Tính latest_start cho chính phiên continuation B theo R05
    if bounds:
        first_bound = bounds[0]
        cont_latest_start = first_bound.latest_start - timedelta(
            seconds=work_secs + required_after_gap
        )
    elif busy_items:
        nearest_busy = min(busy_items, key=lambda b: b.start)
        cont_latest_start = nearest_busy.start - timedelta(
            seconds=work_secs + required_after_gap
        )
    else:
        cont_latest_start = cont_start + timedelta(seconds=300)

    if cont_start > cont_latest_start:
        raise AppError(
            status_code=409,
            code="LOCKED_CALENDAR_COLLISION",
            message="Thời lượng làm thêm vượt quá khoảng trống cho phép trước sự kiện cố định.",
            next_action="reduce_extension",
        )

    add_item = ProposedSessionAddItem(
        temp_session_id=temp_session_id,
        action="create_continuation_session",
        task_id=str(source_task.id),
        step_id=str(source_step.id),
        source_run_id=str(source_run.id),
        before=None,
        after=ProposedSessionAddAfter(
            step_id=str(source_step.id),
            start=cont_start,
            end=cont_end,
            work_seconds=work_secs,
            rest_seconds=0,
            duration_seconds=work_secs,
            micro_task_text=None,
            micro_task_seconds=0,
            segments=[
                SessionSegment(
                    order_index=0,
                    kind="work",
                    duration_seconds=work_secs,
                )
            ],
        ),
        reason="continue_current_step_with_user_approved_extension",
    )

    gap_exceptions: list[GapException] = []
    if cont_req.immediate_continuation:
        gap_exceptions.append(
            GapException(
                from_session_ref=str(source_session.id),
                to_session_ref=temp_session_id,
                standard_gap_seconds=STANDARD_INTER_SESSION_GAP_SECONDS,
                applied_gap_seconds=IMMEDIATE_CONTINUATION_GAP_SECONDS,
                reason="immediate_continuation",
            )
        )
    if borrow_secs > 0 and upcoming_sessions:
        gap_exceptions.append(
            GapException(
                from_session_ref=temp_session_id,
                to_session_ref=str(upcoming_sessions[0].id),
                standard_gap_seconds=STANDARD_INTER_SESSION_GAP_SECONDS,
                applied_gap_seconds=required_after_gap,
                borrowed_seconds=borrow_secs,
                reason="user_borrowed_break",
            )
        )

    start_window = StartWindow(
        earliest_start=cont_start,
        latest_start=cont_latest_start,
        not_before=cont_start,
        not_after=cont_latest_start,
    )
    approval_envelope = ApprovalEnvelope(
        rule="actual_start_plus_fixed_work_plus_min_break_le_latest_start",
        continuation_fixed_work_seconds=work_secs,
        continuation_min_break_after_seconds=required_after_gap,
        shifted_sessions_bounds=bounds,
    )
    changes = ProposedChanges(
        sessions_to_move=moves,
        sessions_to_add=[add_item],
        sessions_to_cancel=[],
        steps_to_add=[],
        next_horizon_from=None,
        next_horizon_to=None,
    )
    feasibility = FeasibilityData(
        status="feasible",
        confirmable=True,
        deficit_seconds=0,
        blocking_reasons=[],
        next_horizon_preview=None,
    )
    work_mins = max(1, work_secs // 60)
    explanation = (
        f"Bạn có thể làm tiếp {work_mins} phút để hoàn tất bước đang dở"
        + (
            f", phiên kế tiếp sẽ dời nhẹ về sau và vẫn giữ tối thiểu {required_after_gap // 60} phút nghỉ."
            if moves
            else " mà không ảnh hưởng các phiên khác."
        )
    )
    return (
        source_task.id,
        affected_tasks,
        changes,
        gap_exceptions,
        start_window,
        approval_envelope,
        feasibility,
        explanation,
    )


async def _build_normal_proposal_plan(
    db: AsyncSession,
    owner: UUID,
    *,
    trigger_reason: str,
    target_task_id: str | None,
    allowed_task_ids: list[str],
    request_text: str | None,
    calc_now: datetime,
) -> tuple[UUID | None, list[str], ProposedChanges, FeasibilityData, str]:
    """Tính toán phương án tái lập lịch thường (giữ cứng min_inter_session_gap_seconds = 900)."""
    target_uuid: UUID | None = None
    target_row: TaskRow | None = None
    if target_task_id:
        target_uuid = _uuid_or_404(target_task_id, "Không tìm thấy công việc mục tiêu.")
        target_row = await db.scalar(
            select(TaskRow).where(TaskRow.id == target_uuid, TaskRow.user_id == owner)
        )
        if target_row is None:
            raise AppError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                message="Không tìm thấy công việc mục tiêu.",
                next_action="reload",
            )

    avail_row = await db.scalar(
        select(AvailabilityRow)
        .where(AvailabilityRow.user_id == owner)
        .order_by(AvailabilityRow.revision.desc())
        .limit(1)
    )
    has_task_avail = (
        (target_row is not None and bool(target_row.availability))
        or trigger_reason == "settings_change"
    )

    # Nếu chưa có Availability đã xác nhận -> trả về trạng thái insufficient_data (Sheet Dòng 33 Variant 1)
    if (avail_row is None or avail_row.confirmed_at is None) and not has_task_avail:
        changes = ProposedChanges(
            sessions_to_move=[],
            sessions_to_add=[],
            sessions_to_cancel=[],
            steps_to_add=[],
            next_horizon_from=None,
            next_horizon_to=None,
        )
        feasibility = FeasibilityData(
            status="insufficient_data",
            confirmable=False,
            deficit_seconds=None,
            blocking_reasons=[
                BlockingReason(
                    code="AVAILABILITY_NOT_CONFIRMED",
                    message="Chưa có khung giờ rảnh đã xác nhận.",
                )
            ],
            next_horizon_preview=None,
        )
        explanation = "Mình giữ nguyên lịch cũ và cần bạn xác nhận lại giờ rảnh trước khi xếp lịch mới."
        return target_uuid, [], changes, feasibility, explanation

    effective_allowed = list(dict.fromkeys(allowed_task_ids))
    if target_uuid is not None and str(target_uuid) not in effective_allowed:
        effective_allowed.append(str(target_uuid))

    # Tải tất cả các phiên scheduled của user
    all_scheduled = (
        await db.scalars(
            select(SessionRow)
            .where(SessionRow.user_id == owner, SessionRow.status == "scheduled")
            .order_by(SessionRow.scheduled_start.asc())
        )
    ).all()

    step_ids = list({s.step_id for s in all_scheduled})
    step_to_task: dict[UUID, UUID] = {}
    if step_ids:
        s_rows = (
            await db.scalars(
                select(StepRow).where(StepRow.user_id == owner, StepRow.id.in_(step_ids))
            )
        ).all()
        for s in s_rows:
            step_to_task[s.id] = s.task_id

    busy_items, _, _ = await _load_google_busy_items(
        db, owner, calc_now, calc_now + timedelta(days=14)
    )

    # Các phiên thuộc allowed_task_ids cần dịch chuyển (hoặc đã trôi qua mốc calc_now, hoặc dời theo yêu cầu)
    movable_sessions: list[SessionRow] = []
    fixed_intervals: list[tuple[datetime, datetime, str]] = [
        (b.start, b.end, f"busy:{b.event_id}") for b in busy_items
    ]

    for sess in all_scheduled:
        s_task_id = str(step_to_task.get(sess.step_id, target_uuid or ""))
        s_start = _ensure_aware(sess.scheduled_start)
        s_end = _ensure_aware(sess.scheduled_end)
        if not effective_allowed or s_task_id in effective_allowed:
            movable_sessions.append(sess)
        else:
            fixed_intervals.append((s_start, s_end, f"task:{s_task_id}"))

    # Nếu không có phiên scheduled nào có sẵn nhưng target_task có step chưa xong, kiểm tra để dịch
    moves: list[ProposedSessionMoveItem] = []
    affected_tasks: list[str] = []

    # Mốc bắt đầu xếp lịch mới sau calc_now (làm tròn lên phút kế tiếp + 15p hoặc +1h nếu user yêu cầu dời)
    base_cursor = (calc_now + timedelta(minutes=15)).replace(second=0, microsecond=0)
    if request_text and "mai" in request_text.lower():
        base_cursor = (calc_now + timedelta(days=1)).replace(
            hour=2, minute=0, second=0, microsecond=0
        )

    for sess in movable_sessions:
        s_start = _ensure_aware(sess.scheduled_start)
        s_end = _ensure_aware(sess.scheduled_end)
        s_task_id = str(step_to_task.get(sess.step_id, target_uuid or ""))
        dur = sess.duration_seconds

        # Nếu phiên đã ở trong quá khứ hoặc trùng vùng cần dời, hoặc là phiên đầu tiên của task mục tiêu
        needs_move = (
            s_start <= base_cursor
            or trigger_reason
            in (
                "checkin_incomplete",
                "checkin_busy",
                "checkin_skipped",
                "missed_start",
                "user_reschedule",
                "settings_change",
                "early_completion_pull_forward",
                "expand_next_horizon",
            )
        )
        if not needs_move:
            fixed_intervals.append((s_start, s_end, f"session:{sess.id}"))
            continue

        candidate_start = max(base_cursor, s_start + timedelta(minutes=30))
        if s_start <= calc_now:
            candidate_start = base_cursor

        # Dịch candidate_start để giữ khoảng cách >= 900s với mọi fixed_intervals
        while True:
            candidate_end = candidate_start + timedelta(seconds=dur)
            conflict_end: datetime | None = None
            for f_start, f_end, _ in fixed_intervals:
                # Phải thỏa mãn: candidate_end + 900s <= f_start HOẶC f_end + 900s <= candidate_start
                if not (
                    candidate_end + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS) <= f_start
                    or f_end + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS)
                    <= candidate_start
                ):
                    conflict_end = f_end + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS)
                    break
            if conflict_end is None:
                break
            candidate_start = conflict_end

        candidate_end = candidate_start + timedelta(seconds=dur)
        shift_secs = int((candidate_start - s_start).total_seconds())
        moves.append(
            ProposedSessionMoveItem(
                session_id=str(sess.id),
                task_id=s_task_id or None,
                step_id=str(sess.step_id),
                from_start=s_start,
                from_end=s_end,
                to_start=candidate_start,
                to_end=candidate_end,
                before=ProposalTimeRange(start=s_start, end=s_end),
                after=ProposalTimeRange(start=candidate_start, end=candidate_end),
                shift_seconds=shift_secs,
                reason=trigger_reason,
            )
        )
        if s_task_id and s_task_id not in affected_tasks:
            affected_tasks.append(s_task_id)
        fixed_intervals.append((candidate_start, candidate_end, f"session:{sess.id}"))
        base_cursor = candidate_end + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS)

    if target_uuid is not None and str(target_uuid) not in affected_tasks and (
        moves or trigger_reason == "settings_change"
    ):
        affected_tasks.insert(0, str(target_uuid))
    if not affected_tasks and trigger_reason == "settings_change" and effective_allowed:
        affected_tasks.extend(effective_allowed)

    changes = ProposedChanges(
        sessions_to_move=moves,
        sessions_to_add=[],
        sessions_to_cancel=[],
        steps_to_add=[],
        next_horizon_from=calc_now if trigger_reason == "expand_next_horizon" else None,
        next_horizon_to=(calc_now + timedelta(days=14))
        if trigger_reason == "expand_next_horizon"
        else None,
    )
    feasibility = FeasibilityData(
        status="feasible",
        confirmable=True,
        deficit_seconds=0,
        blocking_reasons=[],
        next_horizon_preview=None,
    )
    explanation = (
        "Mình đã sắp xếp lại các phiên vào khung giờ rảnh tiếp theo, đảm bảo khoảng nghỉ 15 phút giữa các phiên và giữ nguyên các công việc khác."
    )
    return target_uuid, affected_tasks, changes, feasibility, explanation


@command_service.durable_command("create_proposal", ProposalData, resource_arg="")
async def create_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    idempotency_key: str,
    payload: CreateProposalRequest,
    now: datetime | None = None,
) -> ProposalData:
    """Dòng 33 trên API Sheet: POST /api/v1/proposals."""
    calc_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)

    current_sched_rev = await _get_current_schedule_revision(db, owner)
    db_revs, present = await _compute_db_source_revisions(
        db,
        owner,
        target_task_id=payload.target_task_id,
        allowed_task_ids=payload.allowed_task_ids,
        fallback_revisions=payload.expected_source_revisions,
    )

    # Kiểm tra expected_base_schedule_revision và expected_source_revisions với DB thực tế
    if present["schedule"] and (
        payload.expected_base_schedule_revision != current_sched_rev
        or payload.expected_source_revisions.schedule != current_sched_rev
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản lịch chính thức đã thay đổi.",
            current_revisions=db_revs.model_dump(),
            next_action="reload_and_review",
        )
    if present["task"] and payload.expected_source_revisions.task != db_revs.task:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản công việc đã thay đổi.",
            current_revisions=db_revs.model_dump(),
            next_action="reload_and_review",
        )
    if present["availability"] and (
        payload.expected_source_revisions.availability != db_revs.availability
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản khung giờ rảnh đã thay đổi.",
            current_revisions=db_revs.model_dump(),
            next_action="reload_and_review",
        )
    if present["settings"] and (
        payload.expected_source_revisions.settings != db_revs.settings
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản cài đặt người dùng đã thay đổi.",
            current_revisions=db_revs.model_dump(),
            next_action="reload_and_review",
        )
    if present["calendar"] and (
        payload.expected_source_revisions.calendar != db_revs.calendar
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản ngữ cảnh lịch đã thay đổi.",
            current_revisions=db_revs.model_dump(),
            next_action="reload_and_review",
        )

    base_sched_rev = (
        current_sched_rev if present["schedule"] else payload.expected_base_schedule_revision
    )

    if payload.continuation_request is not None:
        (
            target_uuid,
            affected_tasks,
            changes,
            gap_exceptions,
            start_window,
            approval_envelope,
            feasibility,
            explanation,
        ) = await _build_continuation_plan(
            db,
            owner,
            cont_req=payload.continuation_request,
            allowed_task_ids=payload.allowed_task_ids,
            calc_now=calc_now,
        )
        effective_allowed = list(
            dict.fromkeys(
                payload.continuation_request.allowed_task_ids
                if payload.continuation_request.allowed_task_ids is not None
                else (payload.allowed_task_ids or [str(target_uuid)])
            )
        )
        packed_changes = _pack_proposed_changes(
            changes,
            calculated_at=calc_now,
            continuation_request=payload.continuation_request,
            gap_exceptions=gap_exceptions,
            start_window=start_window,
            approval_envelope=approval_envelope,
            request_text=payload.request_text,
        )
        gen_tier: GenerationTier = "python_fallback"
    else:
        (
            target_uuid,
            affected_tasks,
            changes,
            feasibility,
            explanation,
        ) = await _build_normal_proposal_plan(
            db,
            owner,
            trigger_reason=payload.trigger_reason,
            target_task_id=payload.target_task_id,
            allowed_task_ids=payload.allowed_task_ids,
            request_text=payload.request_text,
            calc_now=calc_now,
        )
        effective_allowed = list(dict.fromkeys(payload.allowed_task_ids))
        packed_changes = _pack_proposed_changes(
            changes,
            calculated_at=calc_now,
            continuation_request=None,
            gap_exceptions=[],
            start_window=None,
            approval_envelope=None,
            request_text=payload.request_text,
        )
        explanation, gen_tier = await _generate_uf6_explanation_and_tier(
            payload.trigger_reason,
            feasibility,
        )

    # Duy trì tối đa 1 proposal pending / user: supersede bản cũ trước khi insert bản mới
    supersedes_id = await _supersede_existing_pending_proposal(db, owner, calc_now)

    new_proposal = RescheduleProposal(
        id=uuid4(),
        user_id=owner,
        created_at=calc_now,
        updated_at=calc_now,
        trigger_reason=payload.trigger_reason,
        target_task_id=target_uuid,
        allowed_task_ids=effective_allowed,
        affected_task_ids=affected_tasks,
        base_schedule_revision=base_sched_rev,
        source_revisions=db_revs.model_dump(mode="json"),
        proposal_revision=1,
        status="pending",
        supersedes_proposal_id=supersedes_id,
        proposed_changes=packed_changes,
        feasibility=feasibility.model_dump(mode="json", by_alias=True),
        explanation=explanation,
        generation_tier=gen_tier,
        pending_notification_state="not_needed",
        pending_notification_sent_at=None,
    )
    db.add(new_proposal)
    await db.flush()

    return await _proposal_row_to_schema(db, owner, new_proposal, calc_now)


async def _supersede_existing_pending_proposal(
    db: AsyncSession,
    owner: UUID,
    calc_now: datetime,
) -> UUID | None:
    """Supersede proposal pending cũ (nếu có) và dọn dẹp liên kết SettingChangeRequest đang treo."""
    existing_pending = await db.scalar(
        select(RescheduleProposal).where(
            RescheduleProposal.user_id == owner,
            RescheduleProposal.status == "pending",
        )
    )
    if existing_pending is None:
        return None

    existing_pending.status = "superseded"
    existing_pending.updated_at = calc_now

    pref_row = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == owner)
    )
    if pref_row is not None and pref_row.pending_proposal_id == existing_pending.id:
        pref_row.requested_revision = None
        pref_row.requested_changes = None
        pref_row.pending_proposal_id = None
        pref_row.updated_at = calc_now

    pending_setting_reqs = (
        await db.scalars(
            select(SettingChangeRequest).where(
                SettingChangeRequest.user_id == owner,
                SettingChangeRequest.pending_proposal_id == existing_pending.id,
                SettingChangeRequest.application_state.in_(
                    ("pending_review", "pending_proposal")
                ),
            )
        )
    ).all()
    for s_req in pending_setting_reqs:
        s_req.application_state = "cancelled"
        s_req.requested_revision = None
        s_req.updated_at = calc_now

    await db.flush()
    return existing_pending.id


async def create_settings_change_proposal(
    db: AsyncSession,
    *,
    owner: UUID,
    target_task_id: str | None = None,
    allowed_task_ids: list[str] | None = None,
    request_text: str | None = None,
    now: datetime | None = None,
) -> ProposalData:
    """Helper nội bộ (không bọc @durable_command) tạo RescheduleProposal cho UF7 settings_change."""
    calc_now = _current_utc_now(now)
    current_sched_rev = await _get_current_schedule_revision(db, owner)

    if allowed_task_ids is not None:
        effective_allowed = list(dict.fromkeys(allowed_task_ids))
    elif target_task_id is not None:
        effective_allowed = [target_task_id]
    else:
        active_task_ids = (
            await db.scalars(
                select(TaskRow.id)
                .where(
                    TaskRow.user_id == owner,
                    TaskRow.status.not_in(("completed", "cancelled")),
                )
                .order_by(TaskRow.created_at.asc())
            )
        ).all()
        effective_allowed = [str(tid) for tid in active_task_ids]

    db_revs, _ = await _compute_db_source_revisions(
        db,
        owner,
        target_task_id=target_task_id,
        allowed_task_ids=effective_allowed,
        fallback_revisions=None,
    )

    (
        target_uuid,
        affected_tasks,
        changes,
        feasibility,
        explanation,
    ) = await _build_normal_proposal_plan(
        db,
        owner,
        trigger_reason="settings_change",
        target_task_id=target_task_id,
        allowed_task_ids=effective_allowed,
        request_text=request_text,
        calc_now=calc_now,
    )
    packed_changes = _pack_proposed_changes(
        changes,
        calculated_at=calc_now,
        continuation_request=None,
        gap_exceptions=[],
        start_window=None,
        approval_envelope=None,
        request_text=request_text,
    )
    explanation, gen_tier = await _generate_uf6_explanation_and_tier(
        "settings_change",
        feasibility,
    )

    supersedes_id = await _supersede_existing_pending_proposal(db, owner, calc_now)

    new_proposal = RescheduleProposal(
        id=uuid4(),
        user_id=owner,
        created_at=calc_now,
        updated_at=calc_now,
        trigger_reason="settings_change",
        target_task_id=target_uuid,
        allowed_task_ids=effective_allowed,
        affected_task_ids=affected_tasks,
        base_schedule_revision=current_sched_rev,
        source_revisions=db_revs.model_dump(mode="json"),
        proposal_revision=1,
        status="pending",
        supersedes_proposal_id=supersedes_id,
        proposed_changes=packed_changes,
        feasibility=feasibility.model_dump(mode="json", by_alias=True),
        explanation=explanation,
        generation_tier=gen_tier,
        pending_notification_state="not_needed",
        pending_notification_sent_at=None,
    )
    db.add(new_proposal)
    await db.flush()

    return await _proposal_row_to_schema(db, owner, new_proposal, calc_now)


async def get_current_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    now: datetime | None = None,
) -> ProposalData | None:
    """Dòng 34 trên API Sheet: GET /api/v1/proposals/current."""
    calc_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)
    row = await db.scalar(
        select(RescheduleProposal).where(
            RescheduleProposal.user_id == owner,
            RescheduleProposal.status == "pending",
        )
    )
    if row is None:
        return None
    return await _proposal_row_to_schema(db, owner, row, calc_now)


async def get_proposal_detail(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    now: datetime | None = None,
) -> ProposalData:
    """Dòng 35 trên API Sheet: GET /api/v1/proposals/{proposal_id}."""
    calc_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)
    prop_uuid = _uuid_or_404(proposal_id, "Không tìm thấy đề xuất điều chỉnh lịch.")
    row = await db.scalar(
        select(RescheduleProposal).where(
            RescheduleProposal.id == prop_uuid,
            RescheduleProposal.user_id == owner,
        )
    )
    if row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy đề xuất điều chỉnh lịch.",
            next_action="reload",
        )
    return await _proposal_row_to_schema(db, owner, row, calc_now)


@command_service.durable_command("rebuild_proposal", ProposalData, resource_arg="proposal_id")
async def rebuild_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    idempotency_key: str,
    payload: RebuildProposalRequest,
    now: datetime | None = None,
) -> ProposalData:
    """Dòng 36 trên API Sheet: POST /api/v1/proposals/{proposal_id}/rebuild."""
    calc_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)
    prop_uuid = _uuid_or_404(proposal_id, "Không tìm thấy đề xuất điều chỉnh lịch.")

    row = await db.scalar(
        select(RescheduleProposal).where(
            RescheduleProposal.id == prop_uuid,
            RescheduleProposal.user_id == owner,
        )
    )
    if row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy đề xuất điều chỉnh lịch.",
            next_action="reload",
        )
    if row.status != "pending":
        raise AppError(
            status_code=409,
            code="PROPOSAL_CHANGED",
            message="Chỉ có thể tính toán lại đề xuất đang ở trạng thái chờ duyệt.",
            current_revisions={"proposal_revision": row.proposal_revision},
            next_action="reload_and_review",
        )
    if row.proposal_revision != payload.expected_proposal_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản đề xuất đã thay đổi.",
            current_revisions={"proposal_revision": row.proposal_revision},
            next_action="reload_and_review",
        )

    _, _, cont_req, _, _, _, old_req_text = _unpack_proposal_meta(row.proposed_changes)
    effective_req_text = (
        payload.request_text if payload.request_text is not None else old_req_text
    )

    current_sched_rev = await _get_current_schedule_revision(db, owner)
    old_revs = SourceRevisions.model_validate(row.source_revisions)
    db_revs, present = await _compute_db_source_revisions(
        db,
        owner,
        target_task_id=str(row.target_task_id) if row.target_task_id else None,
        allowed_task_ids=row.allowed_task_ids,
        fallback_revisions=old_revs,
    )
    base_sched_rev = (
        current_sched_rev if present["schedule"] else row.base_schedule_revision
    )

    if cont_req is not None:
        # Cập nhật expected_run_revision theo revision hiện tại của source_run khi rebuild
        s_run = await db.scalar(
            select(SessionRun).where(
                SessionRun.id == _uuid_or_404(cont_req.source_run_id),
                SessionRun.user_id == owner,
            )
        )
        updated_cont_req = (
            cont_req.model_copy(update={"expected_run_revision": s_run.revision})
            if s_run is not None
            else cont_req
        )
        (
            target_uuid,
            affected_tasks,
            changes,
            gap_exceptions,
            start_window,
            approval_envelope,
            feasibility,
            explanation,
        ) = await _build_continuation_plan(
            db,
            owner,
            cont_req=updated_cont_req,
            allowed_task_ids=row.allowed_task_ids,
            calc_now=calc_now,
        )
        packed_changes = _pack_proposed_changes(
            changes,
            calculated_at=calc_now,
            continuation_request=updated_cont_req,
            gap_exceptions=gap_exceptions,
            start_window=start_window,
            approval_envelope=approval_envelope,
            request_text=effective_req_text,
        )
        row.target_task_id = target_uuid
        gen_tier: GenerationTier = "python_fallback"
    else:
        (
            target_uuid,
            affected_tasks,
            changes,
            feasibility,
            explanation,
        ) = await _build_normal_proposal_plan(
            db,
            owner,
            trigger_reason=row.trigger_reason,
            target_task_id=str(row.target_task_id) if row.target_task_id else None,
            allowed_task_ids=row.allowed_task_ids,
            request_text=effective_req_text,
            calc_now=calc_now,
        )
        packed_changes = _pack_proposed_changes(
            changes,
            calculated_at=calc_now,
            continuation_request=None,
            gap_exceptions=[],
            start_window=None,
            approval_envelope=None,
            request_text=effective_req_text,
        )
        explanation, gen_tier = await _generate_uf6_explanation_and_tier(
            row.trigger_reason,
            feasibility,
        )

    row.affected_task_ids = affected_tasks
    row.base_schedule_revision = base_sched_rev
    row.source_revisions = db_revs.model_dump(mode="json")
    row.proposal_revision += 1
    row.updated_at = calc_now
    row.proposed_changes = packed_changes
    row.feasibility = feasibility.model_dump(mode="json", by_alias=True)
    row.explanation = explanation
    row.generation_tier = gen_tier
    await db.flush()

    return await _proposal_row_to_schema(db, owner, row, calc_now)


async def _sync_calendar_on_approve(
    db: AsyncSession,
    owner: UUID,
    *,
    added_sessions: list[SessionRow],
    moved_sessions: list[SessionRow],
    cancelled_sessions: list[SessionRow],
    version: int,
) -> tuple[Literal["not_integrated", "pending"], bool]:
    """Tạo CalendarEventLink và CalendarSyncJob trong cùng transaction khi Approve UF6."""
    conn = await db.scalar(
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == owner,
            CalendarConnection.connection_state == "connected",
            CalendarConnection.write_calendar_id.is_not(None),
        )
        .order_by(CalendarConnection.created_at.desc())
        .limit(1)
    )
    if conn is None or not conn.write_calendar_id:
        return "not_integrated", False

    job_enqueued = False

    for sess in added_sessions:
        event_id = f"session{sess.id.hex}"
        link = CalendarEventLink(
            id=uuid4(),
            user_id=owner,
            session_id=sess.id,
            connection_id=conn.id,
            google_calendar_id=conn.write_calendar_id,
            google_event_id=event_id,
            google_etag=None,
            desired_schedule_revision=version,
            synced_schedule_revision=None,
            sync_state="pending",
            is_current=True,
        )
        db.add(link)
        await db.flush()

        payload_snapshot = {
            "id": event_id,
            "summary": "ProPlan VA Focus Session",
            "start": {
                "dateTime": _ensure_aware(sess.scheduled_start).isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "end": {
                "dateTime": _ensure_aware(sess.scheduled_end).isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "transparency": "opaque",
            "reminders": {"useDefault": False, "overrides": []},
            "extendedProperties": {
                "private": {
                    "proactive_session_id": str(sess.id),
                    "proactive_schedule_revision": str(version),
                }
            },
        }
        db.add(
            CalendarSyncJob(
                id=uuid4(),
                user_id=owner,
                connection_id=conn.id,
                event_link_id=link.id,
                target_kind="session_event",
                target_ref=str(sess.id),
                operation="create",
                target_schedule_revision=version,
                expected_google_etag=None,
                payload_snapshot=payload_snapshot,
                idempotency_key=f"sync_session:{sess.id}:v{version}:create",
                job_state="queued",
                attempt_count=0,
            )
        )
        job_enqueued = True

    for sess in moved_sessions:
        existing_link = await db.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.user_id == owner,
                CalendarEventLink.session_id == sess.id,
                CalendarEventLink.is_current.is_(True),
            )
        )
        event_id = (
            existing_link.google_event_id
            if existing_link and existing_link.google_event_id
            else f"session{sess.id.hex}"
        )
        if existing_link is None:
            existing_link = CalendarEventLink(
                id=uuid4(),
                user_id=owner,
                session_id=sess.id,
                connection_id=conn.id,
                google_calendar_id=conn.write_calendar_id,
                google_event_id=event_id,
                google_etag=None,
                desired_schedule_revision=version,
                synced_schedule_revision=None,
                sync_state="pending",
                is_current=True,
            )
            db.add(existing_link)
            await db.flush()
            op = "create"
        else:
            existing_link.desired_schedule_revision = version
            existing_link.sync_state = "pending"
            op = "update"

        payload_snapshot = {
            "id": event_id,
            "summary": "ProPlan VA Focus Session",
            "start": {
                "dateTime": _ensure_aware(sess.scheduled_start).isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "end": {
                "dateTime": _ensure_aware(sess.scheduled_end).isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "transparency": "opaque",
            "reminders": {"useDefault": False, "overrides": []},
            "extendedProperties": {
                "private": {
                    "proactive_session_id": str(sess.id),
                    "proactive_schedule_revision": str(version),
                }
            },
        }
        db.add(
            CalendarSyncJob(
                id=uuid4(),
                user_id=owner,
                connection_id=conn.id,
                event_link_id=existing_link.id,
                target_kind="session_event",
                target_ref=str(sess.id),
                operation=op,
                target_schedule_revision=version,
                expected_google_etag=existing_link.google_etag,
                payload_snapshot=payload_snapshot,
                idempotency_key=f"sync_session:{sess.id}:v{version}:{op}",
                job_state="queued",
                attempt_count=0,
            )
        )
        job_enqueued = True

    for sess in cancelled_sessions:
        existing_link = await db.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.user_id == owner,
                CalendarEventLink.session_id == sess.id,
                CalendarEventLink.is_current.is_(True),
            )
        )
        if existing_link is not None:
            existing_link.desired_schedule_revision = version
            existing_link.sync_state = "pending"
            db.add(
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=owner,
                    connection_id=conn.id,
                    event_link_id=existing_link.id,
                    target_kind="session_event",
                    target_ref=str(sess.id),
                    operation="delete",
                    target_schedule_revision=version,
                    expected_google_etag=existing_link.google_etag,
                    payload_snapshot={"id": existing_link.google_event_id},
                    idempotency_key=f"sync_session:{sess.id}:v{version}:delete",
                    job_state="queued",
                    attempt_count=0,
                )
            )
            job_enqueued = True

    return ("pending" if job_enqueued else "not_integrated"), job_enqueued


def _session_row_to_schema(sess: SessionRow, link_id: str | None = None) -> SessionData:
    due_at = sess.start_decision_due_at or (
        _ensure_aware(sess.scheduled_start) + timedelta(seconds=START_GRACE_SECONDS)
    )
    return SessionData(
        session_id=str(sess.id),
        step_id=str(sess.step_id),
        scheduled_start=_ensure_aware(sess.scheduled_start),
        scheduled_end=_ensure_aware(sess.scheduled_end),
        micro_task_text=sess.micro_task_text,
        micro_task_seconds=sess.micro_task_seconds,
        work_seconds=sess.work_seconds,
        rest_seconds=sess.rest_seconds,
        segments=[SessionSegment.model_validate(seg) for seg in (sess.segments or [])],
        duration_seconds=sess.duration_seconds,
        schedule_revision=sess.schedule_revision,
        status=cast(
            Literal["scheduled", "in_progress", "completed", "incomplete", "skipped", "cancelled"],
            sess.status,
        ),
        calendar_event_link_id=link_id,
        start_state=cast(
            Literal["pending", "started", "missed_pending_replan", "superseded"],
            sess.start_state,
        ),
        start_decision_due_at=_ensure_aware(due_at),
    )


@command_service.durable_command("approve_proposal", ApproveProposalData, resource_arg="proposal_id")
async def approve_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    idempotency_key: str,
    payload: ApproveProposalRequest,
    now: datetime | None = None,
) -> ApproveProposalData:
    """Dòng 37 trên API Sheet: POST /api/v1/proposals/{proposal_id}/approve."""
    approve_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)
    prop_uuid = _uuid_or_404(proposal_id, "Không tìm thấy đề xuất điều chỉnh lịch.")

    row = await db.scalar(
        select(RescheduleProposal).where(
            RescheduleProposal.id == prop_uuid,
            RescheduleProposal.user_id == owner,
        )
    )
    if row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy đề xuất điều chỉnh lịch.",
            next_action="reload",
        )

    if row.status != "pending":
        raise AppError(
            status_code=409,
            code="PROPOSAL_CHANGED",
            message="Đề xuất điều chỉnh lịch không còn ở trạng thái chờ duyệt.",
            current_revisions={"proposal_revision": row.proposal_revision},
            next_action="reload_and_review",
        )

    if row.proposal_revision != payload.expected_proposal_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản đề xuất đã thay đổi.",
            current_revisions={"proposal_revision": row.proposal_revision},
            next_action="reload_and_review",
        )

    prop_revs = SourceRevisions.model_validate(row.source_revisions)
    current_sched_rev = await _get_current_schedule_revision(db, owner)
    db_revs, present = await _compute_db_source_revisions(
        db,
        owner,
        target_task_id=str(row.target_task_id) if row.target_task_id else None,
        allowed_task_ids=row.allowed_task_ids,
        fallback_revisions=prop_revs,
    )

    # Kiểm tra expected_base_schedule_revision và expected_source_revisions
    if (
        payload.expected_base_schedule_revision != row.base_schedule_revision
        or (present["schedule"] and current_sched_rev != row.base_schedule_revision)
    ):
        raise AppError(
            status_code=409,
            code="SOURCE_CHANGED",
            message="Lịch chính thức đã thay đổi kể từ khi tạo đề xuất. Vui lòng tính toán lại.",
            current_revisions={
                "schedule": current_sched_rev,
                "proposal_revision": row.proposal_revision,
            },
            next_action="rebuild_and_review",
        )

    if payload.expected_source_revisions != prop_revs or (
        (present["task"] and db_revs.task != prop_revs.task)
        or (present["availability"] and db_revs.availability != prop_revs.availability)
        or (present["settings"] and db_revs.settings != prop_revs.settings)
        or (present["calendar"] and db_revs.calendar != prop_revs.calendar)
    ):
        raise AppError(
            status_code=409,
            code="SOURCE_CHANGED",
            message="Dữ liệu nguồn (công việc, giờ rảnh, cài đặt hoặc lịch) đã thay đổi.",
            current_revisions=db_revs.model_dump(),
            next_action="rebuild_and_review",
        )

    feas = FeasibilityData.model_validate(row.feasibility)
    if feas.status != "feasible" or not feas.confirmable:
        raise AppError(
            status_code=409,
            code="PLAN_NOT_CONFIRMABLE",
            message="Đề xuất lịch chưa đủ điều kiện khả thi để áp dụng.",
            next_action="rebuild_and_review",
        )

    # Kiểm tra không có SessionRun đang chạy song song
    active_run = await db.scalar(
        select(SessionRun).where(
            SessionRun.user_id == owner,
            SessionRun.run_state.in_(("running", "paused")),
        )
    )
    if active_run is not None:
        raise AppError(
            status_code=409,
            code="ACTIVE_RUN_EXISTS",
            message="Đang có một phiên tập trung khác đang chạy.",
            next_action="finish_active_run",
        )

    changes, _, cont_req, _, start_win, app_env, _ = _unpack_proposal_meta(
        row.proposed_changes
    )

    # Kiểm tra R05: start_window & approval_envelope theo thời điểm bấm thực tế (approve_now)
    if start_win is not None:
        if approve_now > _ensure_aware(start_win.not_after) or approve_now > _ensure_aware(
            start_win.latest_start
        ):
            raise AppError(
                status_code=409,
                code="PREVIEW_STALE",
                message="Đã quá khung giờ cho phép bắt đầu phiên làm tiếp. Vui lòng tính toán lại đề xuất.",
                current_revisions={
                    "schedule": row.base_schedule_revision,
                    "proposal_revision": row.proposal_revision,
                },
                next_action="rebuild_and_review",
            )

    if app_env is not None:
        earliest = (
            _ensure_aware(start_win.earliest_start) if start_win is not None else approve_now
        )
        actual_start = max(approve_now, earliest)
        required_end_plus_break = actual_start + timedelta(
            seconds=app_env.continuation_fixed_work_seconds
            + app_env.continuation_min_break_after_seconds
        )
        for bound in app_env.shifted_sessions_bounds:
            if required_end_plus_break > _ensure_aware(bound.latest_start):
                raise AppError(
                    status_code=409,
                    code="PREVIEW_STALE",
                    message="Thời điểm duyệt hiện tại không còn đủ khoảng trống cho thời lượng làm và nghỉ tối thiểu.",
                    current_revisions={
                        "schedule": row.base_schedule_revision,
                        "proposal_revision": row.proposal_revision,
                    },
                    next_action="rebuild_and_review",
                )
    else:
        # Với đề xuất thường: kiểm tra không có phiên nào trong proposed_changes đã trôi qua approve_now
        for mv in changes.sessions_to_move:
            target_start = mv.to_start or (mv.after.start if mv.after else None)
            if target_start is not None and _ensure_aware(target_start) < approve_now:
                raise AppError(
                    status_code=409,
                    code="PREVIEW_STALE",
                    message="Khung giờ đề xuất đã trôi qua. Vui lòng tính toán lại đề xuất lịch.",
                    current_revisions={
                        "schedule": row.base_schedule_revision,
                        "proposal_revision": row.proposal_revision,
                    },
                    next_action="rebuild_and_review",
                )
        for ad in changes.sessions_to_add:
            if _ensure_aware(ad.after.start) < approve_now:
                raise AppError(
                    status_code=409,
                    code="PREVIEW_STALE",
                    message="Khung giờ đề xuất đã trôi qua. Vui lòng tính toán lại đề xuất lịch.",
                    current_revisions={
                        "schedule": row.base_schedule_revision,
                        "proposal_revision": row.proposal_revision,
                    },
                    next_action="rebuild_and_review",
                )

    source_run: SessionRun | None = None
    if payload.start_continuation:
        if cont_req is None:
            raise _validation_error(
                "Đề xuất này không phải là đề xuất làm tiếp (continuation).",
                "start_continuation",
            )
        source_run = await db.scalar(
            select(SessionRun).where(
                SessionRun.id == _uuid_or_404(cont_req.source_run_id),
                SessionRun.user_id == owner,
            )
        )
        if source_run is None:
            raise AppError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                message="Không tìm thấy lượt chạy nguồn.",
                next_action="reload",
            )
        if source_run.revision != payload.expected_source_run_revision:
            raise AppError(
                status_code=409,
                code="REVISION_CONFLICT",
                message="Phiên bản lượt chạy nguồn đã thay đổi.",
                current_revisions={"run_revision": source_run.revision},
                next_action="reload_and_review",
            )
        await _check_continuation_chain_limit(db, owner, source_run)

    next_version = row.base_schedule_revision + 1
    pref_row = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == owner)
    )
    pending_req_offsets = (
        pref_row.requested_changes.get("reminder_offsets")
        if pref_row is not None
        and pref_row.pending_proposal_id == row.id
        and isinstance(pref_row.requested_changes, dict)
        else None
    )
    reminder_offsets = (
        list(pending_req_offsets)
        if pending_req_offsets
        else (
            pref_row.reminder_offsets
            if pref_row is not None and pref_row.reminder_offsets
            else [900, 600, 300]
        )
    )

    # 1. Áp dụng steps_to_add (nếu có)
    for st_item in changes.steps_to_add:
        st_uuid = UUID(st_item.step_id)
        existing_st = await db.scalar(
            select(StepRow).where(StepRow.id == st_uuid, StepRow.user_id == owner)
        )
        if existing_st is None:
            db.add(
                StepRow(
                    id=st_uuid,
                    user_id=owner,
                    task_id=UUID(st_item.task_id),
                    order_index=st_item.order_index,
                    title=st_item.title,
                    expected_output=st_item.title,
                    estimated_base_seconds=st_item.estimated_base_seconds,
                    remaining_base_seconds=st_item.estimated_base_seconds,
                    buffer_multiplier=1.2,
                    estimate_source="reschedule",
                    status="pending",
                )
            )
    await db.flush()

    # 2. Áp dụng sessions_to_cancel
    cancelled_rows: list[SessionRow] = []
    for c_item in changes.sessions_to_cancel:
        c_uuid = _uuid_or_404(c_item.session_id)
        s_row = await db.scalar(
            select(SessionRow).where(SessionRow.id == c_uuid, SessionRow.user_id == owner)
        )
        if s_row is not None:
            s_row.status = "cancelled"
            s_row.start_state = "superseded"
            s_row.schedule_revision = next_version
            s_row.updated_at = approve_now
            cancelled_rows.append(s_row)
            old_rems = (
                await db.scalars(
                    select(Reminder).where(
                        Reminder.user_id == owner,
                        Reminder.session_id == s_row.id,
                        Reminder.delivery_state == "scheduled",
                    )
                )
            ).all()
            for rem in old_rems:
                rem.delivery_state = "cancelled"
                rem.updated_at = approve_now

    # 3. Áp dụng sessions_to_add (bao gồm phiên Continuation)
    added_rows: list[SessionRow] = []
    continuation_session_row: SessionRow | None = None
    actual_cont_end: datetime | None = None

    for a_item in changes.sessions_to_add:
        after = a_item.after
        add_start = _ensure_aware(after.start)
        if payload.start_continuation and approve_now > add_start:
            add_start = approve_now
        add_end = add_start + timedelta(seconds=after.duration_seconds)
        actual_cont_end = add_end

        is_cont_start = bool(
            payload.start_continuation
            and a_item.action == "create_continuation_session"
        )
        new_sess = SessionRow(
            id=uuid4(),
            user_id=owner,
            step_id=UUID(after.step_id),
            scheduled_start=add_start,
            scheduled_end=add_end,
            micro_task_text=after.micro_task_text,
            micro_task_seconds=after.micro_task_seconds,
            work_seconds=after.work_seconds,
            rest_seconds=after.rest_seconds,
            segments=[seg.model_dump() for seg in after.segments]
            if after.segments
            else [{"order_index": 0, "kind": "work", "duration_seconds": after.work_seconds}],
            duration_seconds=after.duration_seconds,
            schedule_revision=next_version,
            status="in_progress" if is_cont_start else "scheduled",
            start_state="started" if is_cont_start else "pending",
            start_decision_due_at=add_start + timedelta(seconds=START_GRACE_SECONDS),
        )
        db.add(new_sess)
        added_rows.append(new_sess)
        if is_cont_start:
            continuation_session_row = new_sess
        else:
            for offset in reminder_offsets:
                db.add(
                    Reminder(
                        user_id=owner,
                        session_id=new_sess.id,
                        schedule_revision=next_version,
                        kind="pre_session",
                        fire_at=add_start - timedelta(seconds=offset),
                        dedupe_key=f"session:{new_sess.id}:version:{next_version}:reminder:{offset}",
                        delivery_state="scheduled",
                    )
                )
    await db.flush()

    # 4. Áp dụng sessions_to_move
    moved_rows: list[SessionRow] = []
    min_break_after_cont = (
        app_env.continuation_min_break_after_seconds
        if app_env is not None
        else STANDARD_INTER_SESSION_GAP_SECONDS
    )
    prev_shifted_end = actual_cont_end

    for idx, m_item in enumerate(changes.sessions_to_move):
        m_uuid = _uuid_or_404(m_item.session_id)
        s_row = await db.scalar(
            select(SessionRow).where(SessionRow.id == m_uuid, SessionRow.user_id == owner)
        )
        if s_row is None:
            continue
        target_start = m_item.to_start or (m_item.after.start if m_item.after else None)
        target_end = m_item.to_end or (m_item.after.end if m_item.after else None)
        if target_start is None or target_end is None:
            continue
        new_start = _ensure_aware(target_start)
        new_end = _ensure_aware(target_end)

        # Nếu bấm duyệt trễ hợp lệ trong start_window làm actual_cont_end nhích lên, đẩy nhẹ phiên kế tiếp
        if prev_shifted_end is not None:
            req_gap = (
                min_break_after_cont if idx == 0 else STANDARD_INTER_SESSION_GAP_SECONDS
            )
            min_allowed = prev_shifted_end + timedelta(seconds=req_gap)
            if new_start < min_allowed:
                new_start = min_allowed
                new_end = new_start + timedelta(seconds=s_row.duration_seconds)

        s_row.scheduled_start = new_start
        s_row.scheduled_end = new_end
        s_row.schedule_revision = next_version
        if s_row.status == "scheduled":
            s_row.start_decision_due_at = new_start + timedelta(seconds=START_GRACE_SECONDS)
        s_row.updated_at = approve_now
        moved_rows.append(s_row)
        prev_shifted_end = new_end

        # Hủy reminder cũ và tạo reminder mới cho phiên bản lịch mới
        old_rems = (
            await db.scalars(
                select(Reminder).where(
                    Reminder.user_id == owner,
                    Reminder.session_id == s_row.id,
                    Reminder.delivery_state == "scheduled",
                )
            )
        ).all()
        for rem in old_rems:
            rem.delivery_state = "cancelled"
            rem.updated_at = approve_now

        for offset in reminder_offsets:
            db.add(
                Reminder(
                    user_id=owner,
                    session_id=s_row.id,
                    schedule_revision=next_version,
                    kind="pre_session",
                    fire_at=new_start - timedelta(seconds=offset),
                    dedupe_key=f"session:{s_row.id}:version:{next_version}:reminder:{offset}",
                    delivery_state="scheduled",
                )
            )
    await db.flush()

    # 5. Đồng bộ Google Calendar Outbox trong cùng transaction
    cal_summary, job_enqueued = await _sync_calendar_on_approve(
        db,
        owner,
        added_sessions=added_rows,
        moved_sessions=moved_rows,
        cancelled_sessions=cancelled_rows,
        version=next_version,
    )
    await db.flush()

    # 6. Khởi tạo SessionRun nếu start_continuation=True
    cont_run_schema = None
    if payload.start_continuation and continuation_session_row is not None and source_run is not None:
        eff_rev = pref_row.effective_revision if pref_row is not None else 1
        link_id = await db.scalar(
            select(CalendarEventLink.id).where(
                CalendarEventLink.user_id == owner,
                CalendarEventLink.session_id == continuation_session_row.id,
                CalendarEventLink.is_current.is_(True),
            )
        )
        snap_model = SettingsSnapshot(
            effective_revision=eff_rev,
            session=SessionSnapshot(
                session_id=str(continuation_session_row.id),
                step_id=str(continuation_session_row.step_id),
                scheduled_start=_ensure_aware(continuation_session_row.scheduled_start),
                scheduled_end=_ensure_aware(continuation_session_row.scheduled_end),
                micro_task_text=continuation_session_row.micro_task_text,
                micro_task_seconds=continuation_session_row.micro_task_seconds,
                work_seconds=continuation_session_row.work_seconds,
                rest_seconds=continuation_session_row.rest_seconds,
                segments=[
                    SessionSegment.model_validate(seg)
                    for seg in continuation_session_row.segments
                ],
                duration_seconds=continuation_session_row.duration_seconds,
                schedule_revision=next_version,
                status="in_progress",
                calendar_event_link_id=str(link_id) if link_id else None,
                start_state="started",
                start_decision_due_at=_ensure_aware(
                    continuation_session_row.start_decision_due_at
                    or (
                        continuation_session_row.scheduled_start
                        + timedelta(seconds=START_GRACE_SECONDS)
                    )
                ),
            ),
        )
        raw_snap = snap_model.model_dump(mode="json")
        raw_snap["_continuation_of_run_id"] = str(source_run.id)

        cont_run_row = SessionRun(
            id=uuid4(),
            user_id=owner,
            session_id=continuation_session_row.id,
            device_id=str(payload.device_id),
            started_at=_ensure_aware(continuation_session_row.scheduled_start),
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
            dnd_release_at=None,
            settings_snapshot=raw_snap,
        )
        db.add(cont_run_row)
        await db.flush()

        db.add(
            RunEvent(
                id=uuid4(),
                user_id=owner,
                run_id=cont_run_row.id,
                client_event_id=str(payload.event_id),
                event_type="session_started",
                occurred_at=_ensure_aware(continuation_session_row.scheduled_start),
                payload={
                    "segment_index": 0,
                    "work_elapsed_seconds": 0,
                    "rest_elapsed_seconds": 0,
                    "continuation_of_run_id": str(source_run.id),
                },
            )
        )
        await db.flush()
        cont_run_schema = await run_service._run_to_schema(
            db, cont_run_row, settings_snapshot=snap_model
        )

    # 7. Tạo ScheduleVersion mới
    version_row = ScheduleVersionRow(
        user_id=owner,
        version_number=next_version,
        base_version=row.base_schedule_revision,
        approved_at=approve_now,
        affected_task_ids=[str(x) for x in (row.affected_task_ids or [])],
        min_inter_session_gap_seconds=STANDARD_INTER_SESSION_GAP_SECONDS,
        alarm_sync_state="pending",
    )
    db.add(version_row)

    # 8. Cập nhật trạng thái RescheduleProposal -> approved
    row.status = "approved"
    row.updated_at = approve_now

    # 9. Nếu có SettingChangeRequest / UserPreference.pending_proposal_id liên kết (UF7 -> UF6)
    if pref_row is not None and pref_row.pending_proposal_id == row.id:
        req_changes = pref_row.requested_changes or {}
        if isinstance(req_changes, dict):
            if "work_interval_seconds" in req_changes:
                pref_row.work_interval_seconds = int(req_changes["work_interval_seconds"])
            if "rest_interval_seconds" in req_changes:
                pref_row.rest_interval_seconds = int(req_changes["rest_interval_seconds"])
            if "micro_task_seconds" in req_changes:
                pref_row.micro_task_seconds = int(req_changes["micro_task_seconds"])
            if "reminder_offsets" in req_changes:
                pref_row.reminder_offsets = list(req_changes["reminder_offsets"])
            if "buffer_multiplier" in req_changes:
                pref_row.buffer_multiplier = float(req_changes["buffer_multiplier"])
            if "reminder_style" in req_changes:
                pref_row.reminder_style = str(req_changes["reminder_style"])
        pref_row.effective_revision = pref_row.requested_revision or (
            pref_row.effective_revision + 1
        )
        pref_row.requested_revision = None
        pref_row.requested_changes = None
        pref_row.pending_proposal_id = None
        pref_row.updated_at = approve_now

    setting_reqs = (
        await db.scalars(
            select(SettingChangeRequest).where(
                SettingChangeRequest.user_id == owner,
                SettingChangeRequest.pending_proposal_id == row.id,
                SettingChangeRequest.application_state.in_(
                    ("pending_review", "pending_proposal")
                ),
            )
        )
    ).all()
    for s_req in setting_reqs:
        s_req.application_state = "applied"
        if pref_row is not None:
            s_req.effective_revision = pref_row.effective_revision
        s_req.requested_revision = None
        if isinstance(s_req.requested_changes, dict):
            req_avail = s_req.requested_changes.get("availability")
            req_task_id = s_req.requested_changes.get("target_task_id")
            if req_avail is not None and req_task_id:
                try:
                    t_uuid = UUID(str(req_task_id))
                    t_row = await db.scalar(
                        select(TaskRow).where(
                            TaskRow.id == t_uuid,
                            TaskRow.user_id == owner,
                        )
                    )
                    if t_row is not None:
                        t_row.availability = req_avail
                        t_row.revision = s_req.requested_availability_revision or (
                            t_row.revision + 1
                        )
                        t_row.updated_at = approve_now
                        s_req.effective_availability_revision = t_row.revision
                        s_req.requested_availability_revision = None
                except ValueError:
                    pass
        s_req.updated_at = approve_now

    await db.flush()

    # 10. Chuẩn bị danh sách SessionData đầu ra
    output_session_rows = [*added_rows, *moved_rows]
    if not output_session_rows:
        output_session_rows = list(
            (
                await db.scalars(
                    select(SessionRow)
                    .where(
                        SessionRow.user_id == owner,
                        SessionRow.status.in_(("scheduled", "in_progress")),
                    )
                    .order_by(SessionRow.scheduled_start.asc())
                )
            ).all()
        )
    output_session_rows.sort(key=lambda s: _ensure_aware(s.scheduled_start))

    sess_ids = [s.id for s in output_session_rows]
    link_map: dict[UUID, str] = {}
    if sess_ids:
        links = (
            await db.scalars(
                select(CalendarEventLink).where(
                    CalendarEventLink.user_id == owner,
                    CalendarEventLink.session_id.in_(sess_ids),
                    CalendarEventLink.is_current.is_(True),
                )
            )
        ).all()
        link_map = {lk.session_id: str(lk.id) for lk in links}

    return ApproveProposalData(
        proposal_id=str(row.id),
        status="approved",
        schedule_version=ScheduleVersionData(
            version_number=next_version,
            base_version=row.base_schedule_revision,
            approved_at=approve_now,
            affected_task_ids=[str(x) for x in (row.affected_task_ids or [])],
            min_inter_session_gap_seconds=STANDARD_INTER_SESSION_GAP_SECONDS,
            alarm_sync_state="pending",
            calendar_sync_summary=cal_summary,
        ),
        updated_schedule_revision=next_version,
        sessions=[_session_row_to_schema(s, link_map.get(s.id)) for s in output_session_rows],
        continuation_session_id=str(continuation_session_row.id)
        if continuation_session_row is not None
        else None,
        continuation_run=cont_run_schema,
        calendar_sync_job_enqueued=job_enqueued,
    )


@command_service.durable_command("reject_proposal", RejectProposalData, resource_arg="proposal_id")
async def reject_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    idempotency_key: str,
    payload: RejectProposalRequest,
    now: datetime | None = None,
) -> RejectProposalData:
    """Dòng 38 trên API Sheet: POST /api/v1/proposals/{proposal_id}/reject."""
    reject_now = _current_utc_now(now)
    owner = draft_service.resolve_user_uuid(user_id)
    prop_uuid = _uuid_or_404(proposal_id, "Không tìm thấy đề xuất điều chỉnh lịch.")

    row = await db.scalar(
        select(RescheduleProposal).where(
            RescheduleProposal.id == prop_uuid,
            RescheduleProposal.user_id == owner,
        )
    )
    if row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy đề xuất điều chỉnh lịch.",
            next_action="reload",
        )
    if row.status != "pending":
        raise AppError(
            status_code=409,
            code="PROPOSAL_CHANGED",
            message="Đề xuất điều chỉnh lịch không còn ở trạng thái chờ duyệt.",
            current_revisions={"proposal_revision": row.proposal_revision},
            next_action="reload_and_review",
        )
    if row.proposal_revision != payload.expected_proposal_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản đề xuất đã thay đổi.",
            current_revisions={"proposal_revision": row.proposal_revision},
            next_action="reload_and_review",
        )

    row.status = "rejected"
    row.updated_at = reject_now

    # Nếu có cài đặt UF7 đang treo chờ duyệt qua proposal này thì hủy yêu cầu đổi cài đặt, giữ nguyên cấu hình cũ
    pref_row = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == owner)
    )
    if pref_row is not None and pref_row.pending_proposal_id == row.id:
        pref_row.requested_revision = None
        pref_row.requested_changes = None
        pref_row.pending_proposal_id = None
        pref_row.updated_at = reject_now

    setting_reqs = (
        await db.scalars(
            select(SettingChangeRequest).where(
                SettingChangeRequest.user_id == owner,
                SettingChangeRequest.pending_proposal_id == row.id,
                SettingChangeRequest.application_state.in_(
                    ("pending_review", "pending_proposal")
                ),
            )
        )
    ).all()
    for s_req in setting_reqs:
        s_req.application_state = "cancelled"
        s_req.requested_revision = None
        s_req.requested_availability_revision = None
        s_req.updated_at = reject_now

    await db.flush()

    return RejectProposalData(
        proposal_id=str(row.id),
        status="rejected",
        requested_state="unchanged",
    )
