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

import zoneinfo
from datetime import UTC, date, datetime, time, timedelta
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
from app.schemas.availability import DateOverride, DraftAvailability, LocalInterval, WeeklyRule
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
    ProposedSessionCancelItem,
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
from app.services.planning.availability import compute_effective_free_intervals
from app.services.planning.durations import (
    calculate_buffered_work,
    fit_session_to_slot,
)

STANDARD_INTER_SESSION_GAP_SECONDS = 900
SourceRevisions = ProposalSourceRevisions
FeasibilityData = ProposalFeasibilityData
GenerationTier = ProposalGenerationTier
ScheduleVersionData = ProposalScheduleVersionData
SessionData = ProposalSessionData

IMMEDIATE_CONTINUATION_GAP_SECONDS = 120
MAX_BORROW_BREAK_SECONDS = 120
MIN_BORROWED_GAP_SECONDS = STANDARD_INTER_SESSION_GAP_SECONDS - MAX_BORROW_BREAK_SECONDS  # 780s
START_GRACE_SECONDS = 900


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


def _parse_local_time_hhmm(raw: Any) -> time | None:
    if isinstance(raw, time):
        return raw.replace(tzinfo=None)
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if s.startswith("24:00"):
            return time(23, 59, 59)
        parts = s.split(":")
        try:
            hh = int(parts[0])
            mm = int(parts[1])
            ss = int(parts[2]) if len(parts) > 2 else 0
            if hh == 24 and mm == 0:
                return time(23, 59, 59)
            return time(hh, mm, ss)
        except (ValueError, IndexError):
            return None
    return None


def _normalize_availability_schema(
    raw_avail: Any,
    *,
    default_tz: str = "Asia/Ho_Chi_Minh",
    allow_empty: bool = False,
) -> DraftAvailability | None:
    """Chuẩn hóa AvailabilityRow hoặc dict (hỗ trợ cả schema chuẩn & legacy day_of_week/start_local_time)."""
    if raw_avail is None:
        return None
    if isinstance(raw_avail, DraftAvailability):
        return raw_avail

    if isinstance(raw_avail, AvailabilityRow):
        tz_raw = raw_avail.timezone or default_tz
        rules_raw = raw_avail.weekly_rules or []
        overrides_raw = raw_avail.date_overrides or []
    elif isinstance(raw_avail, dict):
        tz_raw = raw_avail.get("timezone") or default_tz
        rules_raw = raw_avail.get("weekly_rules") or []
        overrides_raw = raw_avail.get("date_overrides") or []
        if not rules_raw and not overrides_raw and not allow_empty:
            return None
    else:
        return None

    try:
        zoneinfo.ZoneInfo(str(tz_raw))
        tz_name = str(tz_raw)
    except Exception:
        tz_name = default_tz

    weekly_rules: list[WeeklyRule] = []
    for item in rules_raw:
        if not isinstance(item, dict):
            continue
        if "weekday" in item and item["weekday"] is not None:
            wday = int(item["weekday"])
        elif "day_of_week" in item and item["day_of_week"] is not None:
            dow = int(item["day_of_week"])
            wday = 7 if dow == 0 else dow
        else:
            continue
        if not (1 <= wday <= 7):
            continue
        st = _parse_local_time_hhmm(item.get("start_local") or item.get("start_local_time"))
        en = _parse_local_time_hhmm(item.get("end_local") or item.get("end_local_time"))
        if st is None or en is None or st >= en:
            continue
        weekly_rules.append(WeeklyRule(weekday=wday, start_local=st, end_local=en))

    date_overrides: list[DateOverride] = []
    seen_dates: set[date] = set()
    for ov in overrides_raw:
        if not isinstance(ov, dict) or not ov.get("date"):
            continue
        try:
            ov_date = date.fromisoformat(str(ov["date"])[:10])
        except ValueError:
            continue
        if ov_date in seen_dates:
            continue
        seen_dates.add(ov_date)
        ov_ints: list[LocalInterval] = []
        for iv in ov.get("intervals") or []:
            if not isinstance(iv, dict):
                continue
            st = _parse_local_time_hhmm(iv.get("start_local") or iv.get("start_local_time"))
            en = _parse_local_time_hhmm(iv.get("end_local") or iv.get("end_local_time"))
            if st is None or en is None or st >= en:
                continue
            ov_ints.append(LocalInterval(start_local=st, end_local=en))
        date_overrides.append(DateOverride(date=ov_date, intervals=ov_ints))

    if not weekly_rules and not date_overrides and not allow_empty:
        return None
    return DraftAvailability(
        timezone=tz_name,
        weekly_rules=weekly_rules,
        date_overrides=date_overrides,
    )


def _resolve_task_availability(
    task_row: TaskRow | None,
    avail_row: AvailabilityRow | None,
    *,
    override_availability: dict[str, Any] | None = None,
    override_target_task_id: str | None = None,
) -> DraftAvailability | None:
    if override_availability is not None and (
        override_target_task_id is None
        or (task_row is not None and str(task_row.id) == str(override_target_task_id))
    ):
        norm_override = _normalize_availability_schema(override_availability, allow_empty=True)
        if norm_override is not None:
            return norm_override
    if task_row is not None and isinstance(task_row.availability, dict):
        norm_task = _normalize_availability_schema(task_row.availability, allow_empty=False)
        if norm_task is not None:
            return norm_task
    if avail_row is not None:
        return _normalize_availability_schema(avail_row, allow_empty=True)
    return None


def _extract_task_time_bounds(
    task_row: TaskRow | None,
) -> tuple[datetime | None, datetime | None]:
    if task_row is None or not isinstance(task_row.time_constraint, dict):
        return None, None
    tc = task_row.time_constraint
    start_at = _parse_iso_dt(tc.get("start_at"))
    deadline_at = _parse_iso_dt(tc.get("deadline_at") or tc.get("finish_by_at") or tc.get("at"))
    return (
        start_at.astimezone(UTC) if start_at is not None else None,
        deadline_at.astimezone(UTC) if deadline_at is not None else None,
    )


def _get_local_timezone(
    avail_row: AvailabilityRow | None,
    target_row: TaskRow | None = None,
    override_availability: dict[str, Any] | None = None,
) -> zoneinfo.ZoneInfo:
    candidates: list[Any] = [
        (override_availability or {}).get("timezone") if isinstance(override_availability, dict) else None,
        (target_row.availability or {}).get("timezone")
        if target_row is not None and isinstance(target_row.availability, dict)
        else None,
        (target_row.time_constraint or {}).get("input_timezone") or (target_row.time_constraint or {}).get("timezone")
        if target_row is not None and isinstance(target_row.time_constraint, dict)
        else None,
        avail_row.timezone if avail_row is not None else None,
        "Asia/Ho_Chi_Minh",
    ]
    for raw in candidates:
        if isinstance(raw, str) and raw.strip():
            try:
                return zoneinfo.ZoneInfo(raw.strip())
            except Exception:
                continue
    return zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")


def _subtract_blocked_from_free(
    free_intervals: list[tuple[datetime, datetime]],
    fixed_intervals: list[tuple[datetime, datetime, str]],
    *,
    gap_seconds: int = STANDARD_INTER_SESSION_GAP_SECONDS,
) -> list[tuple[datetime, datetime]]:
    """Trừ các khoảng bận/phiên cố định (mở rộng thêm gap hai đầu) khỏi tập khoảng rảnh."""
    if not free_intervals:
        return []
    gap = timedelta(seconds=gap_seconds)
    blocked = sorted(
        [(s - gap, e + gap) for s, e, _ in fixed_intervals if e > s],
        key=lambda x: (x[0], x[1]),
    )
    if not blocked:
        return list(free_intervals)
    merged_blocked: list[tuple[datetime, datetime]] = [blocked[0]]
    for b_s, b_e in blocked[1:]:
        p_s, p_e = merged_blocked[-1]
        if b_s <= p_e:
            merged_blocked[-1] = (p_s, max(p_e, b_e))
        else:
            merged_blocked.append((b_s, b_e))

    result: list[tuple[datetime, datetime]] = []
    b_idx = 0
    num_b = len(merged_blocked)
    for f_s, f_e in free_intervals:
        curr = f_s
        while b_idx < num_b and merged_blocked[b_idx][1] <= curr:
            b_idx += 1
        ptr = b_idx
        while ptr < num_b and merged_blocked[ptr][0] < f_e:
            b_s, b_e = merged_blocked[ptr]
            if b_s > curr:
                result.append((curr, min(b_s, f_e)))
            curr = max(curr, b_e)
            if curr >= f_e:
                break
            ptr += 1
        if curr < f_e:
            result.append((curr, f_e))
    return [(s, e) for s, e in result if e > s]


def _find_free_slot(
    free_intervals: list[tuple[datetime, datetime]],
    fixed_intervals: list[tuple[datetime, datetime, str]],
    earliest_start: datetime,
    duration_seconds: int,
) -> tuple[datetime, datetime] | None:
    """Tìm khoảng trống đầu tiên >= earliest_start vừa duration_seconds và giữ gap 900s."""
    available_slots = _subtract_blocked_from_free(free_intervals, fixed_intervals)
    for slot_s, slot_e in available_slots:
        cand_s = max(earliest_start, slot_s)
        cand_e = cand_s + timedelta(seconds=duration_seconds)
        if cand_e <= slot_e:
            return cand_s, cand_e
    return None


def _compute_max_extension_without_borrow(
    continuation_start: datetime,
    upcoming_sessions: list[TimelineSessionItem],
    busy_items: list[TimelineGoogleBusyItem],
    *,
    deadline_at: datetime | None = None,
    availability_end: datetime | None = None,
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

    hard_caps: list[int] = []
    if deadline_at is not None:
        hard_caps.append(max(0, int((_ensure_aware(deadline_at) - continuation_start).total_seconds())))
    if availability_end is not None:
        hard_caps.append(
            max(0, int((_ensure_aware(availability_end) - continuation_start).total_seconds()))
        )
    if hard_caps:
        max_ext = min(max_ext, min(hard_caps))

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
    _, task_deadline = _extract_task_time_bounds(task_row)
    avail_schema = _resolve_task_availability(task_row, avail_row)
    availability_end: datetime | None = None
    if avail_schema is not None:
        free_ivs = compute_effective_free_intervals(
            avail_schema,
            window_start=continuation_start,
            window_end=continuation_start + timedelta(hours=24),
            task_id=str(task_row.id),
        )
        matching_iv = next(
            (
                iv
                for iv in free_ivs
                if iv.start_at.astimezone(UTC) <= continuation_start < iv.end_at.astimezone(UTC)
            ),
            None,
        )
        availability_end = (
            matching_iv.end_at.astimezone(UTC) if matching_iv is not None else continuation_start
        )
    max_ext, rec_ext = _compute_max_extension_without_borrow(
        continuation_start,
        session_items,
        busy_items,
        deadline_at=task_deadline,
        availability_end=availability_end,
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
    override_preferences: dict[str, Any] | None = None,
    override_availability: dict[str, Any] | None = None,
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
        "override_preferences": dict(override_preferences) if override_preferences else None,
        "override_availability": dict(override_availability) if override_availability else None,
    }
    return raw


def _unpack_settings_overrides(
    raw_changes: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    meta = (raw_changes or {}).get("_meta") or {}
    prefs = meta.get("override_preferences")
    avail = meta.get("override_availability")
    return (
        dict(prefs) if isinstance(prefs, dict) else None,
        dict(avail) if isinstance(avail, dict) else None,
    )


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

    # 3. Kiểm tra các ô lịch đề xuất đã trôi qua thời điểm `now` hoặc vượt quá deadline (đóng/mở lại app ở UF6)
    if start_win is None:
        task_deadlines: dict[str, datetime] = {}
        task_ids_needed = {
            t for t in ([mv.task_id for mv in changes.sessions_to_move] + [ad.task_id for ad in changes.sessions_to_add]) if t
        }
        if task_ids_needed:
            valid_tuuids = []
            for tid in task_ids_needed:
                try:
                    valid_tuuids.append(UUID(str(tid)))
                except ValueError:
                    continue
            if valid_tuuids:
                t_rows = (
                    await db.scalars(
                        select(TaskRow).where(TaskRow.user_id == owner, TaskRow.id.in_(valid_tuuids))
                    )
                ).all()
                for t_row in t_rows:
                    _, d_at = _extract_task_time_bounds(t_row)
                    if d_at is not None:
                        task_deadlines[str(t_row.id)] = d_at
        for mv in changes.sessions_to_move:
            target_start = mv.to_start or (mv.after.start if mv.after else None)
            target_end = mv.to_end or (mv.after.end if mv.after else None)
            if target_start is not None and _ensure_aware(target_start) < now:
                return True, db_revs, current_sched_rev
            if mv.task_id and mv.task_id in task_deadlines and target_end is not None:
                if _ensure_aware(target_end) > task_deadlines[mv.task_id]:
                    return True, db_revs, current_sched_rev
        for ad in changes.sessions_to_add:
            if _ensure_aware(ad.after.start) < now:
                return True, db_revs, current_sched_rev
            if ad.task_id in task_deadlines and _ensure_aware(ad.after.end) > task_deadlines[ad.task_id]:
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

    _, cont_deadline = _extract_task_time_bounds(source_task)
    if cont_deadline is not None and cont_end > cont_deadline:
        raise AppError(
            status_code=409,
            code="LOCKED_CALENDAR_COLLISION",
            message="Phiên làm tiếp vượt quá hạn chót của công việc.",
            next_action="reduce_extension",
        )
    cont_avail_row = await db.scalar(
        select(AvailabilityRow)
        .where(AvailabilityRow.user_id == owner)
        .order_by(AvailabilityRow.revision.desc())
        .limit(1)
    )
    cont_avail = _resolve_task_availability(source_task, cont_avail_row)
    if cont_avail is not None:
        cont_free = compute_effective_free_intervals(
            cont_avail,
            window_start=cont_start,
            window_end=cont_end,
            task_id=str(source_task.id),
        )
        if not any(
            iv.start_at.astimezone(UTC) <= cont_start and cont_end <= iv.end_at.astimezone(UTC)
            for iv in cont_free
        ):
            raise AppError(
                status_code=409,
                code="LOCKED_CALENDAR_COLLISION",
                message="Phiên làm tiếp vượt ra ngoài khung giờ rảnh khả dụng.",
                next_action="reduce_extension",
            )

    # Map step_id -> task_id và tải TaskRow cho các phiên sắp tới
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

    other_task_ids = [tid for tid in set(step_to_task.values()) if tid != source_task.id]
    cont_tasks_map: dict[UUID, TaskRow] = {source_task.id: source_task}
    if other_task_ids:
        t_rows = (
            await db.scalars(
                select(TaskRow).where(TaskRow.user_id == owner, TaskRow.id.in_(other_task_ids))
            )
        ).all()
        for t in t_rows:
            cont_tasks_map[t.id] = t

    cont_busy_fixed: list[tuple[datetime, datetime, str]] = [
        (b.start, b.end, f"gcal:{b.event_id}") for b in busy_items if b.end > b.start
    ]
    cont_task_windows_cache: dict[
        UUID, tuple[list[tuple[datetime, datetime]], datetime | None, bool]
    ] = {}

    def _get_cont_task_windows(
        task_uuid: UUID,
    ) -> tuple[list[tuple[datetime, datetime]], datetime | None, bool]:
        if task_uuid in cont_task_windows_cache:
            return cont_task_windows_cache[task_uuid]
        t_row = cont_tasks_map.get(task_uuid)
        t_start_bound, t_deadline = _extract_task_time_bounds(t_row)
        t_avail = _resolve_task_availability(t_row, cont_avail_row)
        if t_avail is None:
            res = ([], t_deadline, False)
            cont_task_windows_cache[task_uuid] = res
            return res
        eff_end = horizon_end
        if t_deadline is not None and t_deadline < eff_end:
            eff_end = t_deadline
        if eff_end <= cont_start:
            res = ([], t_deadline, True)
            cont_task_windows_cache[task_uuid] = res
            return res
        eff_ivs = compute_effective_free_intervals(
            t_avail,
            window_start=cont_start,
            window_end=eff_end,
            task_id=str(task_uuid),
        )
        windows: list[tuple[datetime, datetime]] = []
        for iv in eff_ivs:
            s_utc = max(cont_start, iv.start_at.astimezone(UTC))
            e_utc = min(eff_end, iv.end_at.astimezone(UTC))
            if t_start_bound is not None:
                s_utc = max(s_utc, t_start_bound)
            if e_utc > s_utc:
                windows.append((s_utc, e_utc))
        res = (windows, t_deadline, True)
        cont_task_windows_cache[task_uuid] = res
        return res

    temp_session_id = "temp_cont_session_1"
    moves: list[ProposedSessionMoveItem] = []
    bounds: list[ShiftedSessionBound] = []
    affected_tasks: list[str] = [str(source_task.id)]
    cont_blocking_reasons: list[BlockingReason] = []
    cont_seen_codes: set[str] = set()
    cont_deficit_seconds = 0

    def _add_cont_blocking(code: str, msg: str, shortfall: int) -> None:
        nonlocal cont_deficit_seconds
        cont_deficit_seconds += max(1, shortfall)
        if code not in cont_seen_codes:
            cont_seen_codes.add(code)
            cont_blocking_reasons.append(BlockingReason(code=code, message=msg))

    prev_end = cont_end
    for idx, sess in enumerate(upcoming_sessions):
        req_gap = required_after_gap if idx == 0 else STANDARD_INTER_SESSION_GAP_SECONDS
        s_start = _ensure_aware(sess.scheduled_start)
        s_end = _ensure_aware(sess.scheduled_end)
        min_allowed_start = prev_end + timedelta(seconds=req_gap)

        if s_start < min_allowed_start:
            sess_task_uuid = step_to_task.get(sess.step_id, source_task.id)
            sess_task_id = str(sess_task_uuid)
            if sess_task_id not in effective_allowed:
                raise AppError(
                    status_code=409,
                    code="UNALLOWED_TASK_COLLISION",
                    message="Không được phép tự ý dời lịch của công việc khác ngoài danh sách cho phép.",
                    next_action="expand_allowed_tasks_or_reduce_extension",
                )

            new_start = min_allowed_start
            new_end = new_start + timedelta(seconds=sess.duration_seconds)

            # Kiểm tra va chạm của phiên bị đẩy với sự kiện lịch cố định
            for busy in busy_items:
                if new_start < busy.end and new_end > busy.start:
                    raise AppError(
                        status_code=409,
                        code="LOCKED_CALENDAR_COLLISION",
                        message="Không thể dời phiên kế tiếp do vướng sự kiện cố định trên Google Calendar.",
                        next_action="reduce_extension",
                    )

            t_free, t_deadline, has_t_avail = _get_cont_task_windows(sess_task_uuid)
            if t_deadline is not None and new_end > t_deadline:
                _add_cont_blocking(
                    "DEADLINE_EXCEEDED",
                    f"Dời phiên kế tiếp làm công việc vượt quá hạn chót ({t_deadline.isoformat()}).",
                    int((new_end - t_deadline).total_seconds()),
                )
            elif has_t_avail and not any(
                fs <= new_start and new_end <= fe for fs, fe in t_free
            ):
                # Thử tìm khe rảnh hợp lệ tiếp theo trong khung giờ rảnh của task (tránh giờ bận Google)
                avail_slots = _subtract_blocked_from_free(
                    t_free, cont_busy_fixed, gap_seconds=0
                )
                found_slot: tuple[datetime, datetime] | None = None
                for fs, fe in avail_slots:
                    cand_s = max(new_start, fs)
                    cand_e = cand_s + timedelta(seconds=sess.duration_seconds)
                    if cand_e <= fe:
                        found_slot = (cand_s, cand_e)
                        break
                if found_slot is not None:
                    new_start, new_end = found_slot
                else:
                    usable = sum(
                        max(0, int((fe - max(fs, new_start)).total_seconds()))
                        for fs, fe in avail_slots
                        if fe > max(fs, new_start)
                    )
                    shortfall = max(1, sess.duration_seconds - usable)
                    if t_deadline is not None and (
                        t_deadline <= new_start + timedelta(seconds=sess.duration_seconds)
                    ):
                        _add_cont_blocking(
                            "DEADLINE_EXCEEDED",
                            f"Không còn đủ khung giờ rảnh trước hạn chót ({t_deadline.isoformat()}) cho phiên kế tiếp.",
                            shortfall,
                        )
                    else:
                        _add_cont_blocking(
                            "INSUFFICIENT_AVAILABILITY",
                            "Dời phiên kế tiếp vượt ra ngoài khung giờ rảnh khả dụng của công việc.",
                            shortfall,
                        )

            shift_secs = int((new_start - s_start).total_seconds())

            # Tìm sự kiện cố định gần nhất phía sau phiên này để tính latest_start
            later_busies = [b for b in busy_items if b.start >= new_end]
            if later_busies:
                nearest_busy = min(later_busies, key=lambda b: b.start)
                latest_sess_start = nearest_busy.start - timedelta(seconds=sess.duration_seconds)
                locked_event_id: str | None = nearest_busy.event_id
            else:
                latest_sess_start = new_start + timedelta(seconds=300)
                locked_event_id = None

            if not cont_blocking_reasons and t_deadline is not None:
                deadline_cap = t_deadline - timedelta(seconds=sess.duration_seconds)
                if deadline_cap < latest_sess_start:
                    latest_sess_start = deadline_cap

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

    if not cont_blocking_reasons and cont_start > cont_latest_start:
        raise AppError(
            status_code=409,
            code="LOCKED_CALENDAR_COLLISION",
            message="Thời lượng làm thêm vượt quá khoảng trống cho phép trước sự kiện cố định.",
            next_action="reduce_extension",
        )
    if cont_blocking_reasons and cont_latest_start < cont_start:
        cont_latest_start = cont_start

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
    if cont_blocking_reasons:
        feasibility = FeasibilityData(
            status="infeasible",
            confirmable=False,
            deficit_seconds=max(1, cont_deficit_seconds),
            blocking_reasons=cont_blocking_reasons,
            next_horizon_preview=None,
        )
        explanation = cont_blocking_reasons[0].message
    else:
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
    allowed_task_ids: list[str] | None,
    request_text: str | None,
    calc_now: datetime,
    override_preferences: dict[str, Any] | None = None,
    override_availability: dict[str, Any] | None = None,
) -> tuple[UUID | None, list[str], ProposedChanges, FeasibilityData, str]:
    """Tính toán phương án tái lập lịch thường & tái phân đoạn UF7 (giữ cứng gap = 900s)."""
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
        or override_availability is not None
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

    effective_allowed = (
        list(dict.fromkeys(allowed_task_ids)) if allowed_task_ids is not None else []
    )
    if target_uuid is not None and str(target_uuid) not in effective_allowed:
        effective_allowed.append(str(target_uuid))
    allow_all_tasks = (
        allowed_task_ids is None
        and target_uuid is None
        and trigger_reason
        in ("settings_change", "expand_next_horizon", "early_completion_pull_forward")
    )

    pref_row = await db.scalar(
        select(UserPreferenceRow).where(UserPreferenceRow.user_id == owner)
    )
    old_work_int = pref_row.work_interval_seconds if pref_row else 1500
    old_rest_int = pref_row.rest_interval_seconds if pref_row else 300
    old_micro_sec = (
        pref_row.micro_task_seconds
        if (pref_row is not None and pref_row.micro_task_seconds is not None)
        else 120
    )
    old_buffer = float(pref_row.buffer_multiplier) if pref_row else 1.2

    eff_override_prefs: dict[str, Any] = {}
    if override_preferences:
        eff_override_prefs = dict(override_preferences)
    elif (
        trigger_reason == "settings_change"
        and pref_row is not None
        and isinstance(pref_row.requested_changes, dict)
    ):
        eff_override_prefs = dict(pref_row.requested_changes)

    new_work_int = int(eff_override_prefs.get("work_interval_seconds", old_work_int))
    new_rest_int = int(eff_override_prefs.get("rest_interval_seconds", old_rest_int))
    new_micro_sec = int(eff_override_prefs.get("micro_task_seconds", old_micro_sec))
    new_buffer = float(eff_override_prefs.get("buffer_multiplier", old_buffer))

    seg_config_changed = trigger_reason == "settings_change" and (
        new_work_int != old_work_int
        or new_rest_int != old_rest_int
        or abs(new_buffer - old_buffer) > 1e-9
        or new_micro_sec != old_micro_sec
    )

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
    steps_map: dict[UUID, StepRow] = {}
    tasks_map: dict[UUID, TaskRow] = {}
    if target_row is not None:
        tasks_map[target_row.id] = target_row

    if step_ids:
        s_rows = (
            await db.scalars(
                select(StepRow).where(StepRow.user_id == owner, StepRow.id.in_(step_ids))
            )
        ).all()
        for s in s_rows:
            step_to_task[s.id] = s.task_id
            steps_map[s.id] = s
        needed_task_ids = list({s.task_id for s in s_rows if s.task_id not in tasks_map})
        if needed_task_ids:
            t_rows = (
                await db.scalars(
                    select(TaskRow).where(TaskRow.user_id == owner, TaskRow.id.in_(needed_task_ids))
                )
            ).all()
            for t in t_rows:
                tasks_map[t.id] = t

    horizon_days = 21 if trigger_reason != "expand_next_horizon" else 14
    horizon_end = calc_now + timedelta(days=horizon_days)
    busy_items, _, _ = await _load_google_busy_items(db, owner, calc_now, horizon_end)

    movable_sessions: list[SessionRow] = []
    fixed_intervals: list[tuple[datetime, datetime, str]] = [
        (b.start, b.end, f"busy:{b.event_id}") for b in busy_items
    ]

    for sess in all_scheduled:
        s_task_id = str(step_to_task.get(sess.step_id, target_uuid or ""))
        s_start = _ensure_aware(sess.scheduled_start)
        s_end = _ensure_aware(sess.scheduled_end)
        if allow_all_tasks or s_task_id in effective_allowed:
            movable_sessions.append(sess)
        else:
            fixed_intervals.append((s_start, s_end, f"task:{s_task_id}"))

    local_tz = _get_local_timezone(avail_row, target_row, override_availability)
    initial_cursor = (calc_now + timedelta(minutes=15)).replace(second=0, microsecond=0)
    if request_text and "mai" in request_text.lower():
        local_tomorrow_9am = (calc_now.astimezone(local_tz) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        initial_cursor = max(initial_cursor, local_tomorrow_9am.astimezone(UTC))
    task_cursors: dict[UUID | None, datetime] = {}

    def _task_free_windows(task_obj: TaskRow | None) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
        t_start_bound, t_deadline = _extract_task_time_bounds(task_obj)
        w_start = max(calc_now, t_start_bound) if t_start_bound is not None else calc_now
        w_end = min(horizon_end, t_deadline) if t_deadline is not None else horizon_end
        if w_start >= w_end:
            return [], t_deadline
        t_avail = _resolve_task_availability(
            task_obj,
            avail_row,
            override_availability=override_availability,
            override_target_task_id=target_task_id,
        )
        if t_avail is not None:
            ivs = compute_effective_free_intervals(
                t_avail,
                window_start=w_start,
                window_end=w_end,
                task_id=str(task_obj.id) if task_obj is not None else None,
            )
            return [(iv.start_at.astimezone(UTC), iv.end_at.astimezone(UTC)) for iv in ivs], t_deadline
        return [(w_start, w_end)], t_deadline

    moves: list[ProposedSessionMoveItem] = []
    adds: list[ProposedSessionAddItem] = []
    cancels: list[ProposedSessionCancelItem] = []
    affected_tasks: list[str] = []
    blocking_reasons: list[BlockingReason] = []
    seen_blocking_codes: set[str] = set()
    deficit_seconds = 0

    def _record_blocking(
        t_deadline: datetime | None,
        earliest_req: datetime,
        needed_secs: int,
        task_free: list[tuple[datetime, datetime]],
    ) -> None:
        nonlocal deficit_seconds
        avail_slots = _subtract_blocked_from_free(task_free, fixed_intervals)
        usable_avail = sum(
            max(0, int((e - max(s, earliest_req)).total_seconds()))
            for s, e in avail_slots
            if e > max(s, earliest_req)
        )
        shortfall = max(needed_secs - usable_avail, 1)
        deficit_seconds += shortfall
        if t_deadline is not None and (t_deadline <= earliest_req + timedelta(seconds=needed_secs)):
            code = "DEADLINE_EXCEEDED"
            msg = (
                f"Không đủ khung giờ rảnh trước hạn chót "
                f"({t_deadline.astimezone(local_tz).isoformat()})."
            )
        else:
            code = "INSUFFICIENT_AVAILABILITY"
            msg = "Không còn khung giờ rảnh khả dụng phù hợp để xếp toàn bộ phiên tập trung."
        if code not in seen_blocking_codes:
            seen_blocking_codes.add(code)
            blocking_reasons.append(BlockingReason(code=code, message=msg))

    if seg_config_changed and movable_sessions:
        grouped_steps: dict[UUID, list[SessionRow]] = {}
        for sess in movable_sessions:
            grouped_steps.setdefault(sess.step_id, []).append(sess)

        # Kiểm tra xem mỗi step có các phiên đã hoàn thành / bỏ qua / đang chạy / cố định khác hay không
        step_ids_list = list(grouped_steps.keys())
        all_step_sessions = (
            await db.scalars(
                select(SessionRow).where(
                    SessionRow.user_id == owner,
                    SessionRow.step_id.in_(step_ids_list),
                    SessionRow.status != "cancelled",
                )
            )
        ).all()
        movable_ids = {s.id for s in movable_sessions}
        other_sessions_by_step: dict[UUID, list[SessionRow]] = {}
        for st_s in all_step_sessions:
            if st_s.id not in movable_ids:
                other_sessions_by_step.setdefault(st_s.step_id, []).append(st_s)

        reseg_counter = 0
        for step_id, step_sessions in grouped_steps.items():
            step_row = steps_map.get(step_id)
            t_uuid = step_to_task.get(step_id, target_uuid)
            task_row = tasks_map.get(t_uuid) if t_uuid is not None else target_row
            s_task_id = str(t_uuid or target_uuid or "")
            task_free, t_deadline = _task_free_windows(task_row)

            scheduled_work = sum(s.work_seconds for s in step_sessions)
            has_other_step_sessions = bool(other_sessions_by_step.get(step_id))
            if abs(new_buffer - old_buffer) > 1e-9 and scheduled_work > 0:
                eff_old_buffer = (
                    old_buffer
                    if old_buffer > 0
                    else (
                        step_row.buffer_multiplier
                        if step_row is not None and step_row.buffer_multiplier > 0
                        else 1.2
                    )
                )
                if (
                    not has_other_step_sessions
                    and step_row is not None
                    and step_row.remaining_base_seconds > 0
                ):
                    rem_base = step_row.remaining_base_seconds
                else:
                    rem_base = max(1, round(scheduled_work / eff_old_buffer))
                target_work = calculate_buffered_work(rem_base, new_buffer)
            else:
                target_work = scheduled_work

            micro_text = step_sessions[0].micro_task_text
            micro_secs = new_micro_sec if micro_text else 0

            # Thử phân đoạn và xếp vào các khe rảnh khả dụng (ưu tiên không đổi giờ nếu khe cũ vẫn vừa)
            avail_slots = _subtract_blocked_from_free(task_free, fixed_intervals)
            rem_work = target_work
            planned: list[tuple[datetime, datetime, int, int, int, list[SessionSegment]]] = []
            task_cursor = task_cursors.get(t_uuid, initial_cursor)
            step_cursor = task_cursor

            for slot_s, slot_e in avail_slots:
                if rem_work <= 0:
                    break
                left = max(slot_s, step_cursor)
                cap = int((slot_e - left).total_seconds())
                if cap <= 0:
                    continue
                segs, work, rest, dur = fit_session_to_slot(
                    rem_work, cap, new_work_int, new_rest_int
                )
                if work <= 0:
                    continue
                if work < rem_work and work < min(300, new_work_int):
                    continue
                right = left + timedelta(seconds=dur)
                planned.append((left, right, work, rest, dur, segs))
                rem_work -= work
                step_cursor = right + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS)

            if rem_work > 0:
                _record_blocking(t_deadline, task_cursor, rem_work, task_free)
                if s_task_id and s_task_id not in affected_tasks:
                    affected_tasks.append(s_task_id)
                continue

            # Kiểm tra cấu hình phân đoạn có thực sự thay đổi so với phiên cũ hay không
            seg_same = len(planned) == len(step_sessions) and all(
                p_dur == old_s.duration_seconds
                and p_work == old_s.work_seconds
                and p_rest == old_s.rest_seconds
                and (old_s.micro_task_seconds or 0) == micro_secs
                and [sg.model_dump() for sg in p_segs] == (old_s.segments or [])
                for (_, _, p_work, p_rest, p_dur, p_segs), old_s in zip(
                    planned, step_sessions, strict=True
                )
            )
            if seg_same:
                for (_p_s, _p_e, _, _, _, _), old_s in zip(
                    planned, step_sessions, strict=True
                ):
                    o_s = _ensure_aware(old_s.scheduled_start)
                    o_e = _ensure_aware(old_s.scheduled_end)
                    cur_cursor = task_cursors.get(t_uuid, initial_cursor)
                    # Kiểm tra phiên cũ có còn nằm gọn trong task_free và không đụng fixed_intervals không
                    old_valid = any(fs <= o_s and o_e <= fe for fs, fe in avail_slots)
                    if old_valid and o_s >= cur_cursor:
                        fixed_intervals.append((o_s, o_e, f"session:{old_s.id}"))
                        task_cursors[t_uuid] = max(
                            cur_cursor,
                            o_e + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS),
                        )
                        continue
                    slot = _find_free_slot(task_free, fixed_intervals, cur_cursor, old_s.duration_seconds)
                    if slot is None:
                        _record_blocking(t_deadline, cur_cursor, old_s.duration_seconds, task_free)
                        continue
                    cand_s, cand_e = slot
                    moves.append(
                        ProposedSessionMoveItem(
                            session_id=str(old_s.id),
                            task_id=s_task_id or None,
                            step_id=str(old_s.step_id),
                            from_start=o_s,
                            from_end=o_e,
                            to_start=cand_s,
                            to_end=cand_e,
                            before=ProposalTimeRange(start=o_s, end=o_e),
                            after=ProposalTimeRange(start=cand_s, end=cand_e),
                            shift_seconds=int((cand_s - o_s).total_seconds()),
                            reason=trigger_reason,
                        )
                    )
                    fixed_intervals.append((cand_s, cand_e, f"session:{old_s.id}"))
                    task_cursors[t_uuid] = max(
                        cur_cursor,
                        cand_e + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS),
                    )
                    if s_task_id and s_task_id not in affected_tasks:
                        affected_tasks.append(s_task_id)
            else:
                for old_s in step_sessions:
                    cancels.append(
                        ProposedSessionCancelItem(
                            session_id=str(old_s.id),
                            task_id=s_task_id or None,
                            step_id=str(old_s.step_id),
                            reason="resegmented_by_settings_change",
                        )
                    )
                for p_idx, (p_s, p_e, p_work, p_rest, p_dur, p_segs) in enumerate(planned):
                    reseg_counter += 1
                    adds.append(
                        ProposedSessionAddItem(
                            temp_session_id=f"temp_reseg_{reseg_counter}",
                            action="create_session",
                            task_id=s_task_id,
                            step_id=str(step_id),
                            source_run_id=None,
                            before=None,
                            after=ProposedSessionAddAfter(
                                step_id=str(step_id),
                                start=p_s,
                                end=p_e,
                                work_seconds=p_work,
                                rest_seconds=p_rest,
                                duration_seconds=p_dur,
                                micro_task_text=micro_text if p_idx == 0 else None,
                                micro_task_seconds=micro_secs if p_idx == 0 else 0,
                                segments=p_segs,
                            ),
                            reason="resegmented_by_settings_change",
                        )
                    )
                    fixed_intervals.append((p_s, p_e, f"reseg:{reseg_counter}"))
                    cur_cursor = task_cursors.get(t_uuid, initial_cursor)
                    task_cursors[t_uuid] = max(
                        cur_cursor,
                        p_e + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS),
                    )
                if s_task_id and s_task_id not in affected_tasks:
                    affected_tasks.append(s_task_id)
    else:
        for sess in movable_sessions:
            s_start = _ensure_aware(sess.scheduled_start)
            s_end = _ensure_aware(sess.scheduled_end)
            t_uuid = step_to_task.get(sess.step_id, target_uuid)
            task_row = tasks_map.get(t_uuid) if t_uuid is not None else target_row
            s_task_id = str(t_uuid or "")
            dur = sess.duration_seconds
            task_cursor = task_cursors.get(t_uuid, initial_cursor)

            task_free, t_deadline = _task_free_windows(task_row)
            avail_for_existing = _subtract_blocked_from_free(task_free, fixed_intervals)
            existing_valid = any(fs <= s_start and s_end <= fe for fs, fe in avail_for_existing)

            is_targeted_reschedule = trigger_reason in (
                "checkin_incomplete",
                "checkin_busy",
                "checkin_skipped",
                "missed_start",
                "user_reschedule",
            ) and (target_uuid is None or t_uuid == target_uuid)
            needs_move = (not existing_valid) or (s_start < task_cursor) or is_targeted_reschedule
            if not needs_move:
                fixed_intervals.append((s_start, s_end, f"session:{sess.id}"))
                task_cursors[t_uuid] = max(
                    task_cursor,
                    s_end + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS),
                )
                continue

            if (
                s_start <= calc_now
                or not existing_valid
                or s_start < task_cursor
                or not is_targeted_reschedule
            ):
                candidate_earliest = task_cursor
            else:
                candidate_earliest = max(task_cursor, s_start + timedelta(minutes=30))

            slot = _find_free_slot(task_free, fixed_intervals, candidate_earliest, dur)
            if slot is None:
                _record_blocking(t_deadline, candidate_earliest, dur, task_free)
                if s_task_id and s_task_id not in affected_tasks:
                    affected_tasks.append(s_task_id)
                continue

            candidate_start, candidate_end = slot
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
            task_cursors[t_uuid] = max(
                task_cursor,
                candidate_end + timedelta(seconds=STANDARD_INTER_SESSION_GAP_SECONDS),
            )

    if target_uuid is not None and str(target_uuid) not in affected_tasks and (
        moves or adds or cancels or trigger_reason == "settings_change"
    ):
        affected_tasks.insert(0, str(target_uuid))
    if not affected_tasks and trigger_reason == "settings_change" and effective_allowed:
        affected_tasks.extend(effective_allowed)

    changes = ProposedChanges(
        sessions_to_move=moves,
        sessions_to_add=adds,
        sessions_to_cancel=cancels,
        steps_to_add=[],
        next_horizon_from=calc_now if trigger_reason == "expand_next_horizon" else None,
        next_horizon_to=(calc_now + timedelta(days=14))
        if trigger_reason == "expand_next_horizon"
        else None,
    )
    if deficit_seconds > 0:
        feasibility = FeasibilityData(
            status="infeasible",
            confirmable=False,
            deficit_seconds=deficit_seconds,
            blocking_reasons=blocking_reasons,
            next_horizon_preview=None,
        )
        explanation = (
            "Không đủ khung giờ rảnh khả dụng trước hạn chót để xếp lại toàn bộ các phiên. "
            "Mình giữ nguyên lịch cũ để bạn nới hạn chót hoặc mở thêm giờ rảnh."
        )
    else:
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
        generation_tier="python_fallback",
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
    override_preferences: dict[str, Any] | None = None,
    override_availability: dict[str, Any] | None = None,
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
        override_preferences=override_preferences,
        override_availability=override_availability,
    )
    packed_changes = _pack_proposed_changes(
        changes,
        calculated_at=calc_now,
        continuation_request=None,
        gap_exceptions=[],
        start_window=None,
        approval_envelope=None,
        request_text=request_text,
        override_preferences=override_preferences,
        override_availability=override_availability,
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
        generation_tier="python_fallback",
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
    else:
        override_prefs, override_avail = _unpack_settings_overrides(row.proposed_changes)
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
            override_preferences=override_prefs,
            override_availability=override_avail,
        )
        packed_changes = _pack_proposed_changes(
            changes,
            calculated_at=calc_now,
            continuation_request=None,
            gap_exceptions=[],
            start_window=None,
            approval_envelope=None,
            request_text=effective_req_text,
            override_preferences=override_prefs,
            override_availability=override_avail,
        )

    row.affected_task_ids = affected_tasks
    row.base_schedule_revision = base_sched_rev
    row.source_revisions = db_revs.model_dump(mode="json")
    row.proposal_revision += 1
    row.updated_at = calc_now
    row.proposed_changes = packed_changes
    row.feasibility = feasibility.model_dump(mode="json", by_alias=True)
    row.explanation = explanation
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
            Literal[
                "waiting",
                "not_started",
                "started",
                "pending",
                "missed_pending_replan",
                "superseded",
            ],
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

        is_cont_start = bool(
            payload.start_continuation
            and a_item.action == "create_continuation_session"
        )
        if a_item.action == "create_continuation_session":
            actual_cont_end = add_end
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
            start_state="started" if is_cont_start else "waiting",
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
