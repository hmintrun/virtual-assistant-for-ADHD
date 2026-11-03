"""UF2 orchestration: persisted sources, AI decomposition and atomic plan confirmation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.planning import PlanCandidate as CandidateRow
from app.models.planning import PlanningDraft as DraftRow
from app.models.planning import PlanningPart as PartRow
from app.models.planning import ScheduleVersion as VersionRow
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import CommandReceipt, Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as PreferenceRow
from app.schemas.availability import Availability
from app.schemas.calendar import (
    BusyInterval,
    BusyIntervalSource,
    CalendarContext,
    CalendarError,
    PlanningContextCreateRequest,
)
from app.schemas.common import (
    AvailabilityRef,
    BlockingReason,
    CalendarContextRef,
    CandidateRef,
    CommandResult,
    FieldError,
    SourceRevisions,
    TimeConstraint,
    TimeInterval,
)
from app.schemas.draft import PlanningDraft
from app.schemas.planning import (
    CandidatePart,
    CandidateSession,
    CandidateStep,
    CandidateVerificationScope,
    ConfirmCandidateRequest,
    ConfirmCandidateResult,
    FeasibilityReport,
    GenerateCandidateRequest,
    KeyMapping,
    PlanCandidate,
    ScheduleVersion,
    UpdateCandidateRequest,
)
from app.schemas.schedule import ScheduleData
from app.schemas.settings import UserPreference
from app.schemas.task import (
    PlanningPart,
    Session,
    Step,
    Task,
    TaskDecompositionResult,
    TaskListData,
)
from app.services import draft_service
from app.services.ai_service import ai_service
from app.services.scheduler_engine import (
    calculate_buffered_work,
    compute_effective_free_intervals,
    schedule_candidate_sessions,
    validate_planning_prerequisites,
)


@dataclass(frozen=True)
class PlanningSources:
    availability: Availability
    preferences: UserPreference
    context: CalendarContext
    revisions: SourceRevisions
    fixed_sessions: list[Session]


def _error(
    code: str, message: str, status: int = 409, action: str = "reload_and_review"
) -> AppError:
    return AppError(status_code=status, code=code, message=message, next_action=action)


def _invalid(message: str, field: str = "planning") -> AppError:
    return AppError(
        status_code=422,
        code="VALIDATION_ERROR",
        message=message,
        field_errors=[FieldError(field_path=field, message=message)],
        next_action="fix_fields",
    )


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy tài nguyên.", 404, "reload") from None


def _hash(operation: str, resource: str, payload: Any) -> str:
    encoded = json.dumps(
        {"operation": operation, "resource": resource, "body": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _receipt(db: AsyncSession, user_id: str, key: str, digest: str) -> dict[str, Any] | None:
    row = await db.scalar(
        select(CommandReceipt).where(
            CommandReceipt.user_id == draft_service.resolve_user_uuid(user_id),
            CommandReceipt.command_id == key,
        )
    )
    if row is None:
        return None
    if row.payload_hash != digest:
        raise _error(
            "IDEMPOTENCY_KEY_REUSED",
            "Mã lệnh đã được dùng với nội dung khác.",
            action="use_original_payload_or_new_command",
        )
    return (row.result_reference or {}).get("_response")


def _record(
    db: AsyncSession,
    user_id: str,
    key: str,
    digest: str,
    operation: str,
    response: Any,
    **refs: str,
) -> None:
    db.add(
        CommandReceipt(
            user_id=draft_service.resolve_user_uuid(user_id),
            command_id=key,
            command_type=operation,
            payload_hash=digest,
            expected_revision=None,
            state="applied",
            result_reference={**refs, "_response": response.model_dump(mode="json")},
            applied_at=datetime.now(UTC),
        )
    )


async def _lock_user(db: AsyncSession, user_id: str) -> None:
    # Khóa cùng một dòng trước mọi lệnh ghi UF2; không gọi AI/Google trong transaction này.
    # UPDATE không đổi revision tương thích cả SQLite và PostgreSQL.
    found = await db.scalar(
        update(User)
        .where(User.id == draft_service.resolve_user_uuid(user_id))
        .values(revision=User.revision)
        .returning(User.id)
        .execution_options(synchronize_session=False)
    )
    if found is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy người dùng.", 404, "reload")


async def _read_draft(
    db: AsyncSession, user_id: str, draft_id: str
) -> tuple[DraftRow, PlanningDraft]:
    row = await db.scalar(
        select(DraftRow)
        .where(
            DraftRow.id == draft_service.resolve_draft_uuid(draft_id),
            DraftRow.user_id == draft_service.resolve_user_uuid(user_id),
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy bản nháp.", 404, "reload")
    return row, draft_service._row_to_schema(row, fallback_draft_id=draft_id)


def _require_draft(draft: PlanningDraft, summary_revision: int, input_revision: int) -> None:
    if (
        draft.summary_revision != summary_revision
        or draft.planning_input_revision != input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Bản nháp đã thay đổi.",
            current_revisions={
                "summary_revision": draft.summary_revision,
                "planning_input_revision": draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )
    if draft.summary_confirmed_revision != draft.summary_revision or draft.confirmed_task_id:
        raise _error(
            "PLAN_NOT_CONFIRMABLE", "Cần xác nhận UF1 hiện tại và chưa lưu Task từ bản nháp này."
        )
    if any(issue.blocking for issue in draft.summary.clarification_issues):
        raise _error("PLAN_NOT_CONFIRMABLE", "Bản nháp còn thông tin cần làm rõ.")


async def get_preferences(*, user_id: str, db: AsyncSession) -> UserPreference:
    row = await db.scalar(
        select(PreferenceRow)
        .where(PreferenceRow.user_id == draft_service.resolve_user_uuid(user_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error(
            "POLICY_NOT_CONFIGURED",
            "Chưa có cấu hình làm việc/nghỉ đã lưu cho người dùng.",
            503,
            "configure_preferences",
        )
    return UserPreference(
        revision=row.revision,
        effective_revision=row.effective_revision,
        requested_revision=row.requested_revision,
        work_interval_seconds=row.work_interval_seconds,
        rest_interval_seconds=row.rest_interval_seconds,
        micro_task_seconds=row.micro_task_seconds,
        reminder_offsets=row.reminder_offsets,
        buffer_multiplier=row.buffer_multiplier,
        reminder_style=row.reminder_style,
        requested_changes=row.requested_changes,
        pending_proposal_id=str(row.pending_proposal_id) if row.pending_proposal_id else None,
    )


async def _availability(db: AsyncSession, user_id: str) -> Availability:
    row = await db.scalar(
        select(AvailabilityRow)
        .where(AvailabilityRow.user_id == draft_service.resolve_user_uuid(user_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error("SOURCE_CHANGED", "Không tìm thấy nguồn giờ rảnh đã chọn.")
    return Availability(
        availability_id=str(row.id),
        revision=row.revision,
        timezone=row.timezone,
        weekly_rules=row.weekly_rules,
        date_overrides=row.date_overrides,
        task_limits=row.task_limits,
        source=row.source,
        confirmed_at=row.confirmed_at,
        effective_intervals=None,
        calendar_context=None,
    )


def _context_schema(row: PlanningContext) -> CalendarContext:
    return CalendarContext(
        context_id=str(row.id),
        revision=row.revision,
        availability_ref=AvailabilityRef(
            availability_id=str(row.availability_id), revision=row.availability_revision
        ),
        selected_calendar_ids=row.selected_calendar_ids,
        query_start_end=TimeInterval(start_at=row.query_start, end_at=row.query_end),
        busy_intervals=row.busy_intervals,
        fetched_at=row.fetched_at,
        sync_state=row.sync_state,
        calendar_errors=row.calendar_errors,
        event_snapshots=row.event_snapshots,
        coverage_state=row.coverage_state,
        manual_busy_intervals=row.manual_busy_intervals,
        excluded_sources=row.excluded_sources,
        selected_calendar_sources=row.selected_calendar_sources,
    )


async def get_planning_context(
    *, user_id: str, context_id: str, db: AsyncSession
) -> CalendarContext:
    row = await db.scalar(
        select(PlanningContext)
        .where(
            PlanningContext.id == _uuid(context_id),
            PlanningContext.user_id == draft_service.resolve_user_uuid(user_id),
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy ngữ cảnh xếp lịch.", 404, "reload")
    return _context_schema(row)


async def create_planning_context(
    *, user_id: str, payload: PlanningContextCreateRequest, idempotency_key: str, db: AsyncSession
) -> CalendarContext:
    digest = _hash("create_context", "", payload.model_dump(mode="json", by_alias=True))
    try:
        TimeInterval(start_at=payload.from_at, end_at=payload.to_at)
        for item in payload.manual_busy_intervals:
            TimeInterval(start_at=item.start_at, end_at=item.end_at)
    except ValidationError as exc:
        raise _invalid("Khoảng thời gian phải có múi giờ và bắt đầu trước kết thúc.") from exc
    if len({item.client_interval_id for item in payload.manual_busy_intervals}) != len(
        payload.manual_busy_intervals
    ):
        raise _invalid("Mã khoảng bận nhập tay bị trùng.", "manual_busy_intervals")
    await db.rollback()
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return CalendarContext.model_validate(saved)
        availability = await _availability(db, user_id)
        if (
            payload.availability_ref.availability_id != availability.availability_id
            or payload.availability_ref.revision != availability.revision
            or availability.confirmed_at is None
        ):
            raise _error("SOURCE_CHANGED", "Giờ rảnh chưa xác nhận hoặc phiên bản đã thay đổi.")
        selected = {
            (item.connection_id, item.google_calendar_id)
            for item in payload.selected_calendar_sources
        }
        excluded = {
            (item.connection_id, item.google_calendar_id) for item in payload.excluded_sources
        }
        if (
            len(selected) != len(payload.selected_calendar_sources)
            or len(excluded) != len(payload.excluded_sources)
            or not excluded.issubset(selected)
        ):
            raise _invalid(
                "Nguồn lịch bị trùng hoặc loại trừ nguồn chưa được chọn.",
                "selected_calendar_sources",
            )
        for connection_id, calendar_id in selected:
            connection = await db.scalar(
                select(CalendarConnection).where(
                    CalendarConnection.id == _uuid(connection_id),
                    CalendarConnection.user_id == draft_service.resolve_user_uuid(user_id),
                )
            )
            if connection is None or calendar_id not in connection.read_calendar_ids:
                raise _error(
                    "RESOURCE_NOT_FOUND", "Không tìm thấy nguồn lịch được phép đọc.", 404, "reload"
                )
        now = datetime.now(UTC)
        if any(
            item.user_confirmed_at.utcoffset() is None
            or item.user_confirmed_at > now
            or not item.reason.strip()
            for item in payload.excluded_sources
        ):
            raise _invalid(
                "Cần lý do và thời điểm user đã xác nhận loại nguồn lịch.", "excluded_sources"
            )
        if selected - excluded:
            raise _error(
                "CALENDAR_UNAVAILABLE",
                "Chưa ghép bộ đọc Google Calendar; không thể coi các lịch đã chọn là trống.",
                503,
                "retry_or_review_calendar_sources",
            )
        result = CalendarContext(
            context_id=str(uuid4()),
            revision=1,
            availability_ref=payload.availability_ref,
            query_start_end=TimeInterval(start_at=payload.from_at, end_at=payload.to_at),
            fetched_at=now,
            selected_calendar_ids=[
                item.google_calendar_id for item in payload.selected_calendar_sources
            ],
            selected_calendar_sources=payload.selected_calendar_sources,
            busy_intervals=[
                BusyInterval(
                    start_at=item.start_at,
                    end_at=item.end_at,
                    source=BusyIntervalSource(
                        kind="manual", client_interval_id=item.client_interval_id
                    ),
                )
                for item in payload.manual_busy_intervals
            ],
            manual_busy_intervals=payload.manual_busy_intervals,
            excluded_sources=payload.excluded_sources,
            sync_state="valid",
            coverage_state="manual_fallback" if excluded else "complete",
            calendar_errors=[
                CalendarError(
                    connection_id=item.connection_id,
                    google_calendar_id=item.google_calendar_id,
                    error_code="CALENDAR_READ_NOT_IMPLEMENTED",
                    retryable=True,
                    requires_reconnect=False,
                    observed_at=now,
                )
                for item in payload.excluded_sources
            ],
        )
        db.add(_context_row(user_id, result))
        _record(
            db,
            user_id,
            idempotency_key,
            digest,
            "create_context",
            result,
            context_id=result.context_id,
        )
    return result


def _context_row(user_id: str, context: CalendarContext) -> PlanningContext:
    data = context.model_dump(mode="json")
    return PlanningContext(
        id=_uuid(context.context_id),
        user_id=draft_service.resolve_user_uuid(user_id),
        availability_id=_uuid(context.availability_ref.availability_id),
        availability_revision=context.availability_ref.revision,
        query_start=context.query_start_end.start_at,
        query_end=context.query_start_end.end_at,
        fetched_at=context.fetched_at,
        sync_state=context.sync_state,
        coverage_state=context.coverage_state,
        selected_calendar_ids=data["selected_calendar_ids"],
        selected_calendar_sources=data["selected_calendar_sources"],
        busy_intervals=data["busy_intervals"],
        calendar_errors=data["calendar_errors"],
        event_snapshots=data["event_snapshots"],
        manual_busy_intervals=data["manual_busy_intervals"],
        excluded_sources=data["excluded_sources"],
        expires_at=None,
    )


async def _schedule_revision(db: AsyncSession, owner: UUID) -> int:
    version = await db.scalar(
        select(func.max(VersionRow.version_number)).where(VersionRow.user_id == owner)
    )
    session_version = await db.scalar(
        select(func.max(SessionRow.schedule_revision)).where(SessionRow.user_id == owner)
    )
    return max(version or 1, session_version or 1)


def _session_schema(row: SessionRow, link_id: str | None = None) -> Session:
    return Session(
        session_id=str(row.id),
        step_id=str(row.step_id),
        scheduled_start=row.scheduled_start,
        scheduled_end=row.scheduled_end,
        micro_task_text=row.micro_task_text or "Chuẩn bị cho phiên làm việc",
        micro_task_seconds=row.micro_task_seconds,
        work_seconds=row.work_seconds,
        rest_seconds=row.rest_seconds,
        segments=row.segments,
        duration_seconds=row.duration_seconds,
        schedule_revision=row.schedule_revision,
        status=row.status,
        start_state=row.start_state,
        start_decision_due_at=row.start_decision_due_at
        or row.scheduled_start + timedelta(seconds=900),
        calendar_event_link_id=link_id,
    )


async def _sources(
    db: AsyncSession,
    user_id: str,
    draft: PlanningDraft,
    horizon: TimeInterval,
    *,
    create_manual: bool = False,
) -> PlanningSources:
    # UF1 đã nhận giờ rảnh riêng; UF2 chưa có bộ đọc/context theo Draft.
    # Chặn rõ thay vì âm thầm dùng giờ chung còn lưu từ trước.
    if draft.summary.availability is not None:
        raise _error(
            "PLAN_NOT_CONFIRMABLE",
            "UF1 đã lưu giờ rảnh của yêu cầu này. UF2 chưa nối nguồn giờ rảnh theo Draft; "
            "chưa thể tạo hoặc xác nhận phương án từ nguồn mới.",
            action="wait_for_configuration",
        )
    owner = draft_service.resolve_user_uuid(user_id)
    availability = await _availability(db, user_id)
    ref = draft.summary.availability_ref
    if (
        ref is None
        or ref.availability_id != availability.availability_id
        or ref.revision != availability.revision
    ):
        raise _error("SOURCE_CHANGED", "Nguồn giờ rảnh đã thay đổi.")
    valid, issues = validate_planning_prerequisites(draft.summary, availability)
    if not valid:
        raise AppError(
            status_code=409,
            code="PLAN_NOT_CONFIRMABLE",
            message="Thông tin UF1 chưa đủ để xếp lịch.",
            field_errors=[
                FieldError(field_path=item.field_path, code=item.code, message=item.message)
                for item in issues
            ],
            next_action="fix_fields",
        )
    preferences = await get_preferences(user_id=user_id, db=db)
    row = await db.scalar(
        select(PlanningContext)
        .where(
            PlanningContext.user_id == owner,
            PlanningContext.availability_id == _uuid(availability.availability_id),
            PlanningContext.availability_revision == availability.revision,
            PlanningContext.query_start <= horizon.start_at,
            PlanningContext.query_end >= horizon.end_at,
            PlanningContext.sync_state == "valid",
            or_(
                PlanningContext.expires_at.is_(None), PlanningContext.expires_at > datetime.now(UTC)
            ),
        )
        .order_by(PlanningContext.created_at.desc(), PlanningContext.id.desc())
        .limit(1)
        .with_for_update()
    )
    if row is None:
        configured_calendars = (
            await db.scalars(select(CalendarConnection).where(CalendarConnection.user_id == owner))
        ).all()
        if not create_manual or any(item.read_calendar_ids for item in configured_calendars):
            raise _error(
                "CALENDAR_CONTEXT_UNAVAILABLE",
                "Cần ngữ cảnh lịch bận đủ phạm vi trước khi xếp lịch.",
                409,
                "create_planning_context",
            )
        context = CalendarContext(
            context_id=str(uuid4()),
            revision=1,
            availability_ref=ref,
            query_start_end=horizon,
            fetched_at=datetime.now(UTC),
            sync_state="valid",
            coverage_state="complete",
        )
        db.add(_context_row(user_id, context))
        # Snapshot thủ công không có nguồn Google được lưu trước khi chờ AI.
        await db.commit()
    else:
        context = _context_schema(row)
    if context.coverage_state not in {"complete", "manual_fallback"}:
        raise _error(
            "CALENDAR_CONTEXT_UNAVAILABLE",
            "Ngữ cảnh chưa bao phủ đủ các nguồn đã chọn.",
            action="create_planning_context",
        )
    selected = {
        (item.connection_id, item.google_calendar_id) for item in context.selected_calendar_sources
    }
    excluded = {(item.connection_id, item.google_calendar_id) for item in context.excluded_sources}
    errors = {(item.connection_id, item.google_calendar_id) for item in context.calendar_errors}
    if (
        not excluded.issubset(selected)
        or errors - excluded
        or (excluded and context.coverage_state != "manual_fallback")
    ):
        raise _error(
            "CALENDAR_CONTEXT_UNAVAILABLE",
            "Nguồn lỗi chưa được user xác nhận loại trừ.",
            action="create_planning_context",
        )
    fixed = (
        await db.scalars(
            select(SessionRow).where(SessionRow.user_id == owner, SessionRow.status != "cancelled")
        )
    ).all()
    return PlanningSources(
        availability,
        preferences,
        context,
        SourceRevisions(
            availability=availability.revision,
            preferences=preferences.effective_revision,
            schedule=await _schedule_revision(db, owner),
            calendar_context=CalendarContextRef(
                context_id=context.context_id, revision=context.revision
            ),
        ),
        [_session_schema(item) for item in fixed],
    )


def _horizon(draft: PlanningDraft, requested: TimeInterval | None) -> TimeInterval:
    tc = draft.summary.time_constraint
    if tc is None:
        raise _invalid("Thiếu ràng buộc thời gian.")
    terminal = tc.deadline_at if tc.kind == "deadline" else tc.finish_by_at
    if terminal is None:
        raise _invalid("Thiếu mốc kết thúc.")
    start = max(tc.start_at, datetime.now(UTC))
    end = min(terminal, start + timedelta(days=14))
    if requested is not None:
        start = max(start, requested.start_at)
        end = min(terminal, requested.end_at, start + timedelta(days=14))
    if start >= end:
        raise _invalid("Phạm vi lập lịch không còn thời gian hợp lệ.", "planning_horizon")
    return TimeInterval(start_at=start, end_at=end)


def _candidate_schema(row: CandidateRow, draft_id: str) -> PlanCandidate:
    return PlanCandidate(
        candidate_id=str(row.id),
        draft_id=draft_id,
        source_summary_revision=row.source_summary_revision,
        source_planning_input_revision=row.source_planning_input_revision,
        review_revision=row.review_revision,
        planning_parts=row.planning_parts,
        steps=[
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in row.steps
        ],
        sessions=row.sessions,
        planning_horizon=row.planning_horizon,
        source_revisions=row.source_revisions,
        min_inter_session_gap_seconds=row.min_inter_session_gap_seconds,
        feasibility=row.feasibility,
        stale=False,
        user_step_proposal=row.user_step_proposal,
    )


async def _read_candidate(
    db: AsyncSession, user_id: str, candidate_id: str
) -> tuple[CandidateRow, PlanningDraft, PlanCandidate]:
    row = await db.scalar(
        select(CandidateRow)
        .where(
            CandidateRow.id == _uuid(candidate_id),
            CandidateRow.user_id == draft_service.resolve_user_uuid(user_id),
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy phương án.", 404, "reload")
    _, draft = await _read_draft(db, user_id, str(row.draft_id))
    return row, draft, _candidate_schema(row, draft.draft_id)


async def get_candidate_by_id(
    *, user_id: str, candidate_id: str, db: AsyncSession
) -> PlanCandidate:
    _, draft, candidate = await _read_candidate(db, user_id, candidate_id)
    try:
        _require_draft(
            draft, candidate.source_summary_revision, candidate.source_planning_input_revision
        )
        source = await _sources(db, user_id, draft, candidate.planning_horizon)
        stale = source.revisions != candidate.source_revisions
    except AppError:
        stale = True
    return candidate.model_copy(update={"stale": stale})


def _micro_tasks(row: CandidateRow) -> dict[str, str]:
    return {
        item["client_step_key"]: item.get("_micro_task_text", f"Chuẩn bị cho bước: {item['title']}")
        for item in row.steps
    }


def _candidate_values(candidate: PlanCandidate, micro_tasks: dict[str, str]) -> dict[str, Any]:
    data = candidate.model_dump(mode="json")
    return {
        "source_summary_revision": candidate.source_summary_revision,
        "source_planning_input_revision": candidate.source_planning_input_revision,
        "review_revision": candidate.review_revision,
        "planning_parts": data["planning_parts"],
        "steps": [
            {
                **item,
                "_micro_task_text": micro_tasks.get(
                    item["client_step_key"], f"Chuẩn bị cho bước: {item['title']}"
                ),
            }
            for item in data["steps"]
        ],
        "sessions": data["sessions"],
        "planning_horizon": data["planning_horizon"],
        "source_revisions": data["source_revisions"],
        "feasibility": data["feasibility"],
        "min_inter_session_gap_seconds": candidate.min_inter_session_gap_seconds,
        "user_step_proposal": candidate.user_step_proposal,
    }


def _selection(parts: list[CandidatePart], selected: list[str] | None) -> list[str]:
    ordered = [item.client_part_key for item in sorted(parts, key=lambda item: item.order_index)]
    if selected is None:
        return ordered
    if len(set(selected)) != len(selected) or not set(selected).issubset(ordered):
        raise _invalid(
            "Phần được chọn bị trùng hoặc không có trong phương án nguồn.", "selected_part_keys"
        )
    result = [key for key in ordered if key in selected]
    if result != ordered[: len(result)]:
        raise _invalid(
            "Các bước đang tuần tự; cần lập lịch từ phần đầu trước.", "selected_part_keys"
        )
    return result


def _reschedule(
    candidate: PlanCandidate,
    source: PlanningSources,
    micro_tasks: dict[str, str],
    selected: list[str],
) -> PlanCandidate:
    horizon = candidate.planning_horizon
    steps = [item for item in candidate.steps if item.client_part_key in selected]
    free = compute_effective_free_intervals(
        source.availability,
        horizon.start_at,
        horizon.end_at,
        calendar_context=source.context,
        manual_busy=source.context.manual_busy_intervals,
    )
    # Chỉ thu hẹp phạm vi lần tính, không sửa deadline/window gốc trong Draft.
    tc = TimeConstraint(
        kind="window",
        start_at=horizon.start_at,
        finish_by_at=horizon.end_at,
        input_timezone=source.availability.timezone,
    )
    sessions, report = schedule_candidate_sessions(
        steps,
        free,
        source.fixed_sessions,
        tc,
        source.preferences,
        candidate.planning_parts,
        horizon_days=max(1, math.ceil((horizon.end_at - horizon.start_at).total_seconds() / 86400)),
        min_gap_seconds=candidate.min_inter_session_gap_seconds,
    )
    sessions = [
        item.model_copy(
            update={
                "micro_task_text": micro_tasks.get(item.client_step_key, item.micro_task_text),
                "micro_task_seconds": source.preferences.micro_task_seconds,
            }
        )
        for item in sessions
    ]
    work = {
        step.client_step_key: sum(
            item.work_seconds for item in sessions if item.client_step_key == step.client_step_key
        )
        for step in candidate.steps
    }
    parts = []
    for part in candidate.planning_parts:
        children = [
            step for step in candidate.steps if step.client_part_key == part.client_part_key
        ]
        done = bool(children) and all(
            work[step.client_step_key]
            == calculate_buffered_work(step.remaining_base_seconds, step.buffer_multiplier)
            for step in children
        )
        any_work = any(work[step.client_step_key] > 0 for step in children)
        parts.append(
            part.model_copy(
                update={
                    "scheduling_state": "complete"
                    if done and part.decomposition_state == "complete"
                    else "partial"
                    if any_work
                    else "not_scheduled"
                }
            )
        )
    return candidate.model_copy(
        update={"sessions": sessions, "planning_parts": parts, "feasibility": report}
    )


async def _update_draft(db: AsyncSession, row: DraftRow, draft: PlanningDraft) -> None:
    changed = await db.scalar(
        update(DraftRow)
        .where(
            DraftRow.id == row.id,
            DraftRow.user_id == row.user_id,
            DraftRow.revision == row.revision,
        )
        .values(
            summary=draft_service._serialize_summary_payload(draft),
            stage=draft.stage,
            revision=DraftRow.revision + 1,
        )
        .returning(DraftRow.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        raise _error("REVISION_CONFLICT", "Bản nháp đã thay đổi trong lúc xử lý.")


async def create_candidate(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: GenerateCandidateRequest,
    db: AsyncSession,
) -> PlanCandidate:
    digest = _hash(
        "create_candidate", draft_id, payload.model_dump(mode="json", exclude_unset=True)
    )
    saved = await _receipt(db, user_id, idempotency_key, digest)
    if saved is not None:
        return PlanCandidate.model_validate(saved)
    _, draft = await _read_draft(db, user_id, draft_id)
    _require_draft(draft, payload.source_summary_revision, payload.source_planning_input_revision)
    horizon = _horizon(draft, payload.planning_horizon)
    source = await _sources(db, user_id, draft, horizon, create_manual=True)
    previous = None
    if payload.selected_part_keys is not None:
        previous = await db.scalar(
            select(CandidateRow)
            .where(
                CandidateRow.user_id == draft_service.resolve_user_uuid(user_id),
                CandidateRow.draft_id == draft_service.resolve_draft_uuid(draft_id),
                CandidateRow.source_summary_revision == draft.summary_revision,
                CandidateRow.source_planning_input_revision == draft.planning_input_revision,
            )
            .order_by(CandidateRow.created_at.desc(), CandidateRow.id.desc())
            .limit(1)
        )
        if previous is None:
            raise _invalid(
                "Chưa có phương án nguồn để chọn phần; hãy tạo phương án ban đầu trước.",
                "selected_part_keys",
            )
        old = _candidate_schema(previous, draft_id)
        parts, steps, micro_tasks, tier = (
            old.planning_parts,
            old.steps,
            _micro_tasks(previous),
            previous.generation_tier,
        )
        # Ước lượng gốc được giữ; cấu hình effective phải lấy lại cho lần tính mới.
        steps = [
            step.model_copy(update={"buffer_multiplier": source.preferences.buffer_multiplier})
            for step in steps
        ]
        selected = _selection(parts, payload.selected_part_keys)
    await db.rollback()
    if previous is None:
        try:
            decomposition = TaskDecompositionResult.model_validate(
                await ai_service.decompose_task(
                    task_title=draft.summary.title or draft.summary.description or "",
                    scope="\n".join(
                        value
                        for value in (
                            draft.summary.description,
                            draft.summary.scope,
                            draft.summary.goal,
                        )
                        if value
                    ),
                    extra_requirements=draft.summary.extra_requirements,
                    user_step_proposal=draft.user_step_proposal,
                    user_focus_duration=max(1, source.preferences.work_interval_seconds // 60),
                )
            )
            if any(
                not item.expected_output or not item.expected_output.strip()
                for item in decomposition.steps
            ):
                raise ValueError("Missing expected output")
        except (ValidationError, ValueError, TimeoutError) as exc:
            raise _error(
                "AI_UNAVAILABLE",
                "Chưa có kết quả chia bước hợp lệ; bản nháp UF1 được giữ nguyên.",
                503,
                "retry_or_edit_manually",
            ) from exc
        tier = decomposition.generation_tier
        parts, steps, micro_tasks = [], [], {}
        for index, item in enumerate(decomposition.steps):
            output = item.expected_output
            if output is None or not output.strip():
                raise _error(
                    "AI_UNAVAILABLE", "Bước chưa có đầu ra cụ thể.", 503, "retry_or_edit_manually"
                )
            part_key, step_key = f"part-{index + 1}", f"step-{index + 1}"
            parts.append(
                CandidatePart(
                    client_part_key=part_key,
                    scope_description=output,
                    order_index=index,
                    decomposition_state="complete",
                    scheduling_state="not_scheduled",
                    rough_estimate_seconds=item.estimated_base_seconds,
                    estimate_source="ai_suggested",
                )
            )
            steps.append(
                CandidateStep(
                    client_step_key=step_key,
                    client_part_key=part_key,
                    title=item.title,
                    expected_output=output,
                    order_index=index,
                    estimated_base_seconds=item.estimated_base_seconds,
                    remaining_base_seconds=item.estimated_base_seconds,
                    buffer_multiplier=source.preferences.buffer_multiplier,
                    estimate_source="ai_suggested",
                    status="pending",
                )
            )
            micro_tasks[step_key] = item.micro_task_prompt
        selected = _selection(parts, None)
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return PlanCandidate.model_validate(saved)
        draft_row, current = await _read_draft(db, user_id, draft_id)
        _require_draft(
            current, payload.source_summary_revision, payload.source_planning_input_revision
        )
        current_source = await _sources(db, user_id, current, horizon)
        if current_source.revisions != source.revisions:
            raise _error(
                "SOURCE_CHANGED", "Nguồn lập lịch đã đổi khi AI đang xử lý; cần tạo phương án mới."
            )
        candidate = PlanCandidate(
            candidate_id=str(uuid4()),
            draft_id=current.draft_id,
            source_summary_revision=current.summary_revision,
            source_planning_input_revision=current.planning_input_revision,
            review_revision=1,
            planning_parts=parts,
            steps=steps,
            sessions=[],
            planning_horizon=horizon,
            source_revisions=current_source.revisions,
            min_inter_session_gap_seconds=900,
            feasibility=FeasibilityReport(
                status="not_checked",
                fully_scheduled=False,
                verification_scope=CandidateVerificationScope(
                    client_part_keys=selected, planning_horizon=horizon
                ),
            ),
            stale=False,
            user_step_proposal=current.user_step_proposal,
        )
        candidate = _reschedule(candidate, current_source, micro_tasks, selected)
        db.add(
            CandidateRow(
                id=_uuid(candidate.candidate_id),
                user_id=draft_service.resolve_user_uuid(user_id),
                draft_id=draft_row.id,
                generation_tier=tier,
                **_candidate_values(candidate, micro_tasks),
            )
        )
        await db.flush()
        await _update_draft(
            db,
            draft_row,
            current.model_copy(
                update={
                    "candidate_ref": CandidateRef(
                        candidate_id=candidate.candidate_id, review_revision=1
                    ),
                    "stage": "candidate_review",
                }
            ),
        )
        _record(
            db,
            user_id,
            idempotency_key,
            digest,
            "create_candidate",
            candidate,
            candidate_id=candidate.candidate_id,
        )
    return candidate


def _concrete_issues(candidate: PlanCandidate, source: PlanningSources) -> list[BlockingReason]:
    reasons: list[BlockingReason] = []

    def issue(code: str, message: str) -> None:
        if not any(item.code == code for item in reasons):
            reasons.append(BlockingReason(code=code, message=message))

    horizon = candidate.planning_horizon
    free = compute_effective_free_intervals(
        source.availability,
        horizon.start_at,
        horizon.end_at,
        calendar_context=source.context,
        manual_busy=source.context.manual_busy_intervals,
    )
    sessions = sorted(candidate.sessions, key=lambda item: item.scheduled_start)
    scope = candidate.feasibility.verification_scope
    if not isinstance(scope, CandidateVerificationScope):
        raise _invalid("Phạm vi Candidate không hợp lệ.")
    selected = set(scope.client_part_keys)
    part_order = {part.client_part_key: part.order_index for part in candidate.planning_parts}
    ordered_steps = sorted(
        [step for step in candidate.steps if step.client_part_key in selected],
        key=lambda step: (part_order[step.client_part_key], step.order_index),
    )
    if not sessions or not ordered_steps:
        issue("NO_SCHEDULE", "Chưa có lịch cụ thể trong phạm vi được duyệt.")
    previous_end = None
    for step in ordered_steps:
        if step.buffer_multiplier != source.preferences.buffer_multiplier:
            issue(
                "BUFFER_CONFIG_MISMATCH",
                "Buffer của bước không khớp cấu hình có hiệu lực; cần tính lại phương án.",
            )
        children = [item for item in sessions if item.client_step_key == step.client_step_key]
        if sum(item.work_seconds for item in children) != calculate_buffered_work(
            step.remaining_base_seconds, step.buffer_multiplier
        ):
            issue(
                "UNSCHEDULED_WORK",
                "Chưa xếp đủ thời gian làm việc của các bước trong phạm vi duyệt.",
            )
        if children and previous_end is not None and children[0].scheduled_start < previous_end:
            issue(
                "STEP_ORDER", "Bước sau không được bắt đầu trước khi bước trước có lịch hoàn tất."
            )
        if children:
            previous_end = children[-1].scheduled_end
    step_keys = {step.client_step_key for step in ordered_steps}
    for item in sessions:
        if item.client_step_key not in step_keys:
            issue("UNEXPECTED_SESSION", "Phiên không thuộc phạm vi được kiểm chứng.")
        if item.scheduled_start < datetime.now(UTC):
            issue(
                "PAST_SESSION", "Giờ bắt đầu đã trôi qua; cần tính lại phương án trước khi duyệt."
            )
        if not any(
            interval.start_at <= item.scheduled_start and item.scheduled_end <= interval.end_at
            for interval in free
        ):
            issue(
                "OUTSIDE_AVAILABILITY", "Phiên phải nằm trọn trong giờ rảnh và không đè lịch bận."
            )
        if [segment.order_index for segment in item.segments] != list(range(len(item.segments))):
            issue("INVALID_SEGMENTS", "Thứ tự các quãng làm/nghỉ không hợp lệ.")
        work_segments = [
            segment.duration_seconds for segment in item.segments if segment.kind == "work"
        ]
        rest_segments = [
            segment.duration_seconds for segment in item.segments if segment.kind == "rest"
        ]
        if (
            any(value > source.preferences.work_interval_seconds for value in work_segments)
            or any(value != source.preferences.rest_interval_seconds for value in rest_segments)
            or [segment.kind for segment in item.segments]
            != ["work" if index % 2 == 0 else "rest" for index in range(len(item.segments))]
            or item.segments[-1].kind != "work"
        ):
            issue(
                "INVALID_SEGMENTS", "Các quãng phải tuân theo cấu hình làm việc/nghỉ có hiệu lực."
            )
        for fixed in source.fixed_sessions:
            if not (
                item.scheduled_end + timedelta(seconds=900) <= fixed.scheduled_start
                or fixed.scheduled_end + timedelta(seconds=900) <= item.scheduled_start
            ):
                issue(
                    "SESSION_COLLISION",
                    "Phiên bị trùng hoặc chưa cách phiên đã chốt ít nhất 15 phút.",
                )
    for first, second in pairwise(sessions):
        if (second.scheduled_start - first.scheduled_end).total_seconds() < 900:
            issue("SESSION_COLLISION", "Hai phiên phải cách nhau ít nhất 15 phút, kể cả khác Task.")
    return reasons


def _review_feasibility(candidate: PlanCandidate, source: PlanningSources) -> PlanCandidate:
    reasons = _concrete_issues(candidate, source)
    if not reasons:
        return candidate.model_copy(
            update={
                "feasibility": candidate.feasibility.model_copy(
                    update={
                        "status": "feasible",
                        "fully_scheduled": True,
                        "blocking_reasons": [],
                        "required_seconds": sum(
                            item.duration_seconds for item in candidate.sessions
                        ),
                        "unscheduled_seconds": 0,
                        "deficit_seconds": 0,
                    }
                )
            }
        )
    existing = list(candidate.feasibility.blocking_reasons)
    existing.extend(
        item for item in reasons if item.code not in {reason.code for reason in existing}
    )
    report = candidate.feasibility.model_copy(
        update={
            "status": "infeasible"
            if any(
                item.code
                in {
                    "SESSION_COLLISION",
                    "OUTSIDE_AVAILABILITY",
                    "STEP_ORDER",
                    "PAST_SESSION",
                    "INVALID_SEGMENTS",
                }
                for item in reasons
            )
            else candidate.feasibility.status,
            "fully_scheduled": False,
            "blocking_reasons": existing,
        }
    )
    return candidate.model_copy(update={"feasibility": report})


def _apply_edits[ModelT: BaseModel](
    items: list[ModelT],
    edits: Sequence[BaseModel] | None,
    key_name: str,
    model: type[ModelT],
) -> list[ModelT]:
    result = list(items)
    seen = set()
    for edit in edits or []:
        key = getattr(edit, key_name)
        indexes = [index for index, item in enumerate(result) if getattr(item, key_name) == key]
        if not indexes or key in seen:
            raise _invalid("Client key không tồn tại hoặc bị gửi trùng.", f"edits.{key_name}")
        seen.add(key)
        changes = edit.model_dump(exclude_unset=True, exclude={key_name})
        if "estimated_base_seconds" in changes:
            changes["remaining_base_seconds"] = changes["estimated_base_seconds"]
            changes["estimate_source"] = "user_confirmed"
        if "rough_estimate_seconds" in changes:
            changes["estimate_source"] = "user_confirmed"
        result[indexes[0]] = model.model_validate({**result[indexes[0]].model_dump(), **changes})
    return result


async def patch_candidate(
    *,
    user_id: str,
    candidate_id: str,
    idempotency_key: str,
    payload: UpdateCandidateRequest,
    db: AsyncSession,
) -> PlanCandidate:
    digest = _hash(
        "patch_candidate", candidate_id, payload.model_dump(mode="json", exclude_unset=True)
    )
    await db.rollback()
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return PlanCandidate.model_validate(saved)
        row, draft, candidate = await _read_candidate(db, user_id, candidate_id)
        _require_draft(
            draft, candidate.source_summary_revision, candidate.source_planning_input_revision
        )
        if payload.expected_review_revision != candidate.review_revision:
            raise _error("REVISION_CONFLICT", "Phiên bản phương án đã thay đổi.")
        source = await _sources(db, user_id, draft, candidate.planning_horizon)
        if source.revisions != candidate.source_revisions:
            raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã đổi; cần tạo phương án mới.")
        parts, steps, sessions = (
            list(candidate.planning_parts),
            list(candidate.steps),
            list(candidate.sessions),
        )
        micro_tasks = _micro_tasks(row)
        try:
            parts = _apply_edits(
                parts, payload.edits.planning_parts, "client_part_key", CandidatePart
            )
            steps = _apply_edits(steps, payload.edits.steps, "client_step_key", CandidateStep)
            if len({step.order_index for step in steps}) != len(steps):
                raise _invalid("Thứ tự các bước không được trùng nhau.", "edits.steps")
            if any("order_index" in edit.model_fields_set for edit in payload.edits.steps or []):
                steps = sorted(steps, key=lambda step: step.order_index)
                part_keys = [step.client_part_key for step in steps]
                groups = [
                    key
                    for index, key in enumerate(part_keys)
                    if index == 0 or key != part_keys[index - 1]
                ]
                if len(groups) != len(set(groups)):
                    raise _invalid("Các bước của cùng một phần phải nằm liền nhau.", "edits.steps")
                order = {key: index for index, key in enumerate(groups)}
                parts = sorted(
                    [
                        part.model_copy(
                            update={
                                "order_index": order.get(
                                    part.client_part_key, len(order) + part.order_index
                                )
                            }
                        )
                        for part in parts
                    ],
                    key=lambda part: part.order_index,
                )
            changed_timing = any(
                {"estimated_base_seconds", "order_index"} & edit.model_fields_set
                for edit in payload.edits.steps or []
            )
            updated = PlanCandidate.model_validate(
                {
                    **candidate.model_dump(),
                    "planning_parts": parts,
                    "steps": steps,
                    "review_revision": candidate.review_revision + 1,
                }
            )
            scope = candidate.feasibility.verification_scope
            if not isinstance(scope, CandidateVerificationScope):
                raise _invalid("Phạm vi Candidate không hợp lệ.")
            selected = _selection(parts, list(scope.client_part_keys))
            if changed_timing:
                updated = _reschedule(updated, source, micro_tasks, selected)
                sessions = list(updated.sessions)
            seen = set()
            for edit in payload.edits.sessions or []:
                indexes = [
                    index
                    for index, item in enumerate(sessions)
                    if item.client_session_key == edit.client_session_key
                ]
                if not indexes or edit.client_session_key in seen:
                    raise _invalid(
                        "Không tìm thấy phiên hoặc client_session_key bị trùng.", "edits.sessions"
                    )
                seen.add(edit.client_session_key)
                index = indexes[0]
                changes = edit.model_dump(exclude_unset=True, exclude={"client_session_key"})
                if (
                    "scheduled_start" in changes
                    and "scheduled_end" not in changes
                    and changes["scheduled_start"] is not None
                ):
                    changes["scheduled_end"] = changes["scheduled_start"] + timedelta(
                        seconds=sessions[index].duration_seconds
                    )
                elif (
                    "scheduled_end" in changes
                    and "scheduled_start" not in changes
                    and changes["scheduled_end"] is not None
                ):
                    changes["scheduled_start"] = changes["scheduled_end"] - timedelta(
                        seconds=sessions[index].duration_seconds
                    )
                if "scheduled_start" in changes and changes["scheduled_start"] is not None:
                    changes["start_decision_due_at"] = changes["scheduled_start"] + timedelta(
                        seconds=900
                    )
                sessions[index] = CandidateSession.model_validate(
                    {**sessions[index].model_dump(), **changes}
                )
                if edit.micro_task_text is not None:
                    micro_tasks[sessions[index].client_step_key] = edit.micro_task_text
            updated = updated.model_copy(update={"sessions": sessions})
        except ValidationError as exc:
            raise _invalid(
                "Giá trị sửa không hợp lệ hoặc thời lượng phiên không khớp.", "edits"
            ) from exc
        # Kiểm lại lịch cụ thể, không suy khả thi chỉ từ tổng phút.
        updated = _review_feasibility(updated, source)
        for name, value in _candidate_values(updated, micro_tasks).items():
            setattr(row, name, value)
        await db.flush()
        draft_row, current = await _read_draft(db, user_id, draft.draft_id)
        if current.candidate_ref is not None and current.candidate_ref.candidate_id == candidate_id:
            await _update_draft(
                db,
                draft_row,
                current.model_copy(
                    update={
                        "candidate_ref": CandidateRef(
                            candidate_id=candidate_id, review_revision=updated.review_revision
                        )
                    }
                ),
            )
        _record(
            db,
            user_id,
            idempotency_key,
            digest,
            "patch_candidate",
            updated,
            candidate_id=candidate_id,
        )
    return updated


async def _enqueue_calendar(
    db: AsyncSession, owner: UUID, sessions: list[SessionRow], version: int
) -> str:
    connections = (
        await db.scalars(
            select(CalendarConnection).where(
                CalendarConnection.user_id == owner,
                CalendarConnection.connection_state == "connected",
                CalendarConnection.write_calendar_id.is_not(None),
            )
        )
    ).all()
    for connection in connections:
        for session in sessions:
            link = CalendarEventLink(
                id=uuid4(),
                user_id=owner,
                session_id=session.id,
                connection_id=connection.id,
                google_calendar_id=connection.write_calendar_id,
                google_event_id=None,
                google_etag=None,
                desired_schedule_revision=version,
                synced_schedule_revision=None,
                sync_state="pending",
                is_current=True,
            )
            db.add(link)
            await db.flush()
            db.add(
                CalendarSyncJob(
                    user_id=owner,
                    connection_id=connection.id,
                    event_link_id=link.id,
                    target_kind="session",
                    target_ref=str(session.id),
                    operation="create",
                    target_schedule_revision=version,
                    expected_google_etag=None,
                    payload_snapshot={
                        "session_id": str(session.id),
                        "google_calendar_id": connection.write_calendar_id,
                        "start_at": session.scheduled_start.isoformat(),
                        "end_at": session.scheduled_end.isoformat(),
                    },
                    idempotency_key=f"session:{session.id}:version:{version}:create",
                    job_state="queued",
                    attempt_count=0,
                )
            )
    return "pending" if connections else "not_connected"


async def confirm_candidate(
    *,
    user_id: str,
    candidate_id: str,
    idempotency_key: str,
    payload: ConfirmCandidateRequest,
    db: AsyncSession,
) -> ConfirmCandidateResult:
    digest = _hash("confirm_candidate", candidate_id, payload.model_dump(mode="json"))
    await db.rollback()
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return ConfirmCandidateResult.model_validate(saved)
        _, draft, candidate = await _read_candidate(db, user_id, candidate_id)
        _require_draft(
            draft, payload.expected_summary_revision, payload.expected_planning_input_revision
        )
        if candidate.review_revision != payload.expected_review_revision:
            raise _error("REVISION_CONFLICT", "Phương án đã đổi phiên bản.")
        if (
            candidate.source_summary_revision != draft.summary_revision
            or candidate.source_planning_input_revision != draft.planning_input_revision
        ):
            raise _error("SOURCE_CHANGED", "Phương án không còn khớp bản nháp nguồn.")
        source = await _sources(db, user_id, draft, candidate.planning_horizon)
        if (
            candidate.source_revisions != source.revisions
            or payload.expected_source_revisions != source.revisions
        ):
            raise _error(
                "SOURCE_CHANGED",
                "Nguồn lịch, giờ rảnh hoặc cấu hình đã đổi; cần tạo phương án mới.",
            )
        issues = _concrete_issues(candidate, source)
        if (
            candidate.feasibility.status != "feasible"
            or not candidate.feasibility.fully_scheduled
            or issues
        ):
            raise _error(
                "PLAN_NOT_CONFIRMABLE",
                "Chưa có lịch cụ thể thỏa giờ rảnh, thứ tự bước, thời hạn và khoảng nghỉ 15 phút.",
                action="review_feasibility",
            )
        existing = await db.scalar(
            select(TaskRow.id).where(
                TaskRow.source_draft_id == draft_service.resolve_draft_uuid(draft.draft_id)
            )
        )
        if existing is not None:
            raise _error("PLAN_NOT_CONFIRMABLE", "Bản nháp đã được lưu thành công việc.")
        tc, availability_ref = draft.summary.time_constraint, draft.summary.availability_ref
        if tc is None or availability_ref is None:
            raise _error("PLAN_NOT_CONFIRMABLE", "Thiếu ràng buộc thời gian hoặc nguồn giờ rảnh.")
        owner, task_id = draft_service.resolve_user_uuid(user_id), uuid4()
        next_version = source.revisions.schedule + 1
        db.add(
            TaskRow(
                id=task_id,
                user_id=owner,
                title=draft.summary.title,
                description=draft.summary.description or draft.summary.goal,
                scope=draft.summary.scope,
                extra_requirements=draft.summary.extra_requirements,
                time_constraint=tc.model_dump(mode="json"),
                importance=draft.summary.importance,
                status="pending",
                source_draft_id=draft_service.resolve_draft_uuid(draft.draft_id),
                availability_ref=availability_ref.model_dump(mode="json"),
                source_summary=draft.summary.model_dump(mode="json"),
            )
        )
        await db.flush()
        part_ids = {part.client_part_key: uuid4() for part in candidate.planning_parts}
        for part in candidate.planning_parts:
            db.add(
                PartRow(
                    id=part_ids[part.client_part_key],
                    user_id=owner,
                    task_id=task_id,
                    **part.model_dump(),
                )
            )
        await db.flush()
        step_ids = {step.client_step_key: uuid4() for step in candidate.steps}
        for step in candidate.steps:
            db.add(
                StepRow(
                    id=step_ids[step.client_step_key],
                    user_id=owner,
                    task_id=task_id,
                    planning_part_id=part_ids[step.client_part_key],
                    **step.model_dump(exclude={"client_step_key", "client_part_key"}),
                )
            )
        await db.flush()
        session_ids = {item.client_session_key: uuid4() for item in candidate.sessions}
        persisted_sessions = []
        for item in candidate.sessions:
            data = item.model_dump(exclude={"client_session_key", "client_step_key", "segments"})
            session = SessionRow(
                id=session_ids[item.client_session_key],
                user_id=owner,
                step_id=step_ids[item.client_step_key],
                schedule_revision=next_version,
                segments=[segment.model_dump() for segment in item.segments],
                status="scheduled",
                **data,
            )
            db.add(session)
            persisted_sessions.append(session)
        await db.flush()
        for session in persisted_sessions:
            for offset in source.preferences.reminder_offsets:
                db.add(
                    Reminder(
                        user_id=owner,
                        session_id=session.id,
                        schedule_revision=next_version,
                        kind="pre_session",
                        fire_at=session.scheduled_start - timedelta(seconds=offset),
                        dedupe_key=f"session:{session.id}:version:{next_version}:reminder:{offset}",
                        delivery_state="scheduled",
                    )
                )
        calendar_state = await _enqueue_calendar(db, owner, persisted_sessions, next_version)
        approved_at = datetime.now(UTC)
        db.add(
            VersionRow(
                user_id=owner,
                version_number=next_version,
                base_version=source.revisions.schedule,
                approved_at=approved_at,
                affected_task_ids=[str(task_id)],
                min_inter_session_gap_seconds=900,
                alarm_sync_state="pending",
            )
        )
        draft_row, current = await _read_draft(db, user_id, draft.draft_id)
        _require_draft(current, draft.summary_revision, draft.planning_input_revision)
        await _update_draft(
            db,
            draft_row,
            current.model_copy(update={"confirmed_task_id": str(task_id), "stage": "confirmed"}),
        )
        await db.flush()
        task = await get_task_by_id(user_id=user_id, task_id=str(task_id), db=db)
        result = ConfirmCandidateResult(
            app_save_state="saved",
            task=task,
            key_mapping=KeyMapping(
                planning_parts={key: str(value) for key, value in part_ids.items()},
                steps={key: str(value) for key, value in step_ids.items()},
                sessions={key: str(value) for key, value in session_ids.items()},
            ),
            schedule_version=ScheduleVersion(
                version_number=next_version,
                base_version=source.revisions.schedule,
                approved_at=approved_at,
                affected_task_ids=[str(task_id)],
                min_inter_session_gap_seconds=900,
                alarm_sync_state="pending",
                calendar_sync_summary=calendar_state,
            ),
            alarm_sync_state="pending",
            calendar_sync_summary=calendar_state,
        )
        _record(
            db,
            user_id,
            idempotency_key,
            digest,
            "confirm_candidate",
            result,
            task_id=str(task_id),
            candidate_id=candidate_id,
        )
    return result


def _aggregate(states: list[str], *, scheduling: bool = False) -> str:
    initial = "not_scheduled" if scheduling else "not_started"
    if not states or all(state == initial for state in states):
        return initial
    return "complete" if all(state == "complete" for state in states) else "partial"


async def get_task_by_id(*, user_id: str, task_id: str, db: AsyncSession) -> Task:
    owner = draft_service.resolve_user_uuid(user_id)
    row = await db.scalar(
        select(TaskRow)
        .where(TaskRow.id == _uuid(task_id), TaskRow.user_id == owner)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy công việc.", 404, "reload")
    if row.source_draft_id is None or row.source_summary is None or row.availability_ref is None:
        raise _error("SOURCE_CHANGED", "Công việc thiếu snapshot nguồn theo hợp đồng hiện hành.")
    _, draft = await _read_draft(db, user_id, str(row.source_draft_id))
    part_rows = (
        await db.scalars(
            select(PartRow)
            .where(PartRow.task_id == row.id, PartRow.user_id == owner)
            .order_by(PartRow.order_index)
        )
    ).all()
    step_rows = (
        await db.scalars(
            select(StepRow)
            .where(StepRow.task_id == row.id, StepRow.user_id == owner)
            .order_by(StepRow.order_index)
        )
    ).all()
    session_rows = (
        await db.scalars(
            select(SessionRow)
            .join(StepRow, StepRow.id == SessionRow.step_id)
            .where(StepRow.task_id == row.id, SessionRow.user_id == owner)
            .order_by(SessionRow.scheduled_start)
        )
    ).all()
    links = (
        (
            await db.scalars(
                select(CalendarEventLink)
                .where(
                    CalendarEventLink.user_id == owner,
                    CalendarEventLink.session_id.in_([item.id for item in session_rows]),
                    CalendarEventLink.is_current.is_(True),
                )
                .order_by(CalendarEventLink.id)
            )
        ).all()
        if session_rows
        else []
    )
    link_ids = {item.session_id: str(item.id) for item in links}
    parts = [
        PlanningPart(
            planning_part_id=str(part.id),
            task_id=task_id,
            **{name: getattr(part, name) for name in CandidatePart.model_fields},
        )
        for part in part_rows
    ]
    steps = [
        Step.model_validate(
            dict(
                step_id=str(step.id),
                task_id=task_id,
                planning_part_id=str(step.planning_part_id),
                title=step.title,
                expected_output=step.expected_output,
                order_index=step.order_index,
                estimated_base_seconds=step.estimated_base_seconds,
                remaining_base_seconds=step.remaining_base_seconds,
                buffer_multiplier=step.buffer_multiplier,
                estimate_source=step.estimate_source,
                status=step.status,
            )
        )
        for step in step_rows
    ]
    return Task.model_validate(
        dict(
            task_id=str(row.id),
            title=row.title,
            description=row.description,
            scope=row.scope,
            extra_requirements=row.extra_requirements or [],
            time_constraint=row.time_constraint,
            importance=row.importance,
            status=row.status,
            source_draft_id=draft.draft_id,
            decomposition_state=_aggregate([part.decomposition_state for part in parts]),
            scheduling_state=_aggregate([part.scheduling_state for part in parts], scheduling=True),
            availability_ref=row.availability_ref,
            planning_parts=parts,
            steps=steps,
            sessions=[_session_schema(item, link_ids.get(item.id)) for item in session_rows],
            source_summary=row.source_summary,
        )
    )


def _page(
    items: list[Any], limit: int, cursor: str | None, key: str
) -> tuple[list[Any], str | None]:
    start = 0
    if cursor is not None:
        positions = [index for index, item in enumerate(items) if str(getattr(item, key)) == cursor]
        if not positions:
            raise _invalid("Con trỏ phân trang không hợp lệ.", "cursor")
        start = positions[0] + 1
    result = items[start : start + limit]
    return result, str(getattr(result[-1], key)) if result and start + limit < len(items) else None


async def list_tasks(
    *, user_id: str, db: AsyncSession, limit: int = 20, cursor: str | None = None
) -> TaskListData:
    rows = (
        await db.scalars(
            select(TaskRow)
            .where(TaskRow.user_id == draft_service.resolve_user_uuid(user_id))
            .order_by(TaskRow.created_at, TaskRow.id)
        )
    ).all()
    page, next_cursor = _page(list(rows), limit, cursor, "id")
    return TaskListData(
        items=[await get_task_by_id(user_id=user_id, task_id=str(item.id), db=db) for item in page],
        next_cursor=next_cursor,
    )


async def _calendar_state(db: AsyncSession, owner: UUID, version: int) -> str:
    states = (
        await db.scalars(
            select(CalendarSyncJob.job_state).where(
                CalendarSyncJob.user_id == owner,
                CalendarSyncJob.target_schedule_revision == version,
            )
        )
    ).all()
    if not states:
        return "not_connected"
    if any(state == "failed" for state in states):
        return "failed"
    return "synced" if all(state == "completed" for state in states) else "pending"


async def get_schedule(
    *,
    user_id: str,
    from_at: datetime,
    to_at: datetime,
    db: AsyncSession,
    limit: int = 50,
    cursor: str | None = None,
) -> ScheduleData:
    try:
        TimeInterval(start_at=from_at, end_at=to_at)
    except ValidationError as exc:
        raise _invalid("Cần cặp from/to có múi giờ và bắt đầu trước kết thúc.", "from") from exc
    owner = draft_service.resolve_user_uuid(user_id)
    rows = (
        await db.scalars(
            select(SessionRow)
            .where(
                SessionRow.user_id == owner,
                SessionRow.scheduled_start < to_at,
                SessionRow.scheduled_end > from_at,
                SessionRow.status != "cancelled",
            )
            .order_by(SessionRow.scheduled_start, SessionRow.id)
        )
    ).all()
    page, next_cursor = _page(list(rows), limit, cursor, "id")
    links = (
        (
            await db.scalars(
                select(CalendarEventLink)
                .where(
                    CalendarEventLink.user_id == owner,
                    CalendarEventLink.session_id.in_([item.id for item in page]),
                    CalendarEventLink.is_current.is_(True),
                )
                .order_by(CalendarEventLink.id)
            )
        ).all()
        if page
        else []
    )
    link_ids = {item.session_id: str(item.id) for item in links}
    version = await _schedule_revision(db, owner)
    latest = await db.scalar(
        select(VersionRow).where(VersionRow.user_id == owner, VersionRow.version_number == version)
    )
    return ScheduleData(
        schedule_revision=version,
        items=[_session_schema(item, link_ids.get(item.id)) for item in page],
        next_cursor=next_cursor,
        alarm_sync_state=latest.alarm_sync_state if latest else "not_required",
        calendar_sync_summary=await _calendar_state(db, owner, version),
    )


async def get_command_result(*, user_id: str, command_id: str, db: AsyncSession) -> CommandResult:
    row = await db.scalar(
        select(CommandReceipt).where(
            CommandReceipt.user_id == draft_service.resolve_user_uuid(user_id),
            CommandReceipt.command_id == command_id,
        )
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy biên nhận lệnh.", 404, "reload")
    refs = {
        key: value
        for key, value in (row.result_reference or {}).items()
        if not key.startswith("_") and isinstance(value, str)
    }
    return CommandResult(
        command_id=row.command_id,
        command_type=row.command_type,
        state=row.state,
        result_reference=refs,
        applied_at=row.applied_at,
    )
