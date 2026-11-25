"""UF2 orchestration: persisted sources, AI decomposition and atomic plan confirmation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.execution import CheckIn, SessionRun
from app.models.planning import PlanCandidate as CandidateRow
from app.models.planning import PlanningDraft as DraftRow
from app.models.planning import ScheduleVersion as VersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.settings import SettingChangeRequest
from app.models.step import Step as StepRow
from app.models.system import CommandReceipt, Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as PreferenceRow
from app.schemas.availability import Availability, DraftAvailability
from app.schemas.calendar import (
    CalendarContext,
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
    CandidateEdits,
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
    UndoCandidateRequest,
    UpdateCandidateRequest,
)
from app.schemas.schedule import ScheduleData
from app.schemas.settings import UserPreference
from app.schemas.task import (
    Session,
    SplitSessionInputItem,
    Step,
    Task,
    TaskDecompositionResult,
    TaskDeleteResult,
    TaskListData,
)
from app.services import draft_service
from app.services.ai_service import ai_service
from app.services.scheduler_engine import (
    calculate_buffered_work,
    compute_effective_free_intervals,
    compute_feasibility_recovery,
    ingest_calendar_sources,
    schedule_candidate_sessions,
    subtract_fixed_session_gaps,
    validate_planning_prerequisites,
)


@dataclass(frozen=True)
class PlanningSources:
    availability: Availability | DraftAvailability
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


async def get_preferences(
    *, user_id: str, db: AsyncSession, commit_if_created: bool = False
) -> UserPreference:
    owner_uuid = draft_service.resolve_user_uuid(user_id)
    row = await db.scalar(
        select(PreferenceRow)
        .where(PreferenceRow.user_id == owner_uuid)
        .execution_options(populate_existing=True)
    )
    if row is None:
        created = False
        new_row = PreferenceRow(
            user_id=owner_uuid,
            revision=1,
            effective_revision=1,
            work_interval_seconds=settings.DEFAULT_WORK_INTERVAL_SECONDS,
            rest_interval_seconds=settings.DEFAULT_REST_INTERVAL_SECONDS,
            micro_task_seconds=settings.DEFAULT_MICRO_TASK_SECONDS,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=settings.DEFAULT_BUFFER_MULTIPLIER,
            reminder_style="gentle",
        )
        try:
            if not db.in_transaction():
                await db.begin()
            async with db.begin_nested():
                db.add(new_row)
                await db.flush()
            row = new_row
            created = True
        except IntegrityError:
            row = await db.scalar(
                select(PreferenceRow)
                .where(PreferenceRow.user_id == owner_uuid)
                .execution_options(populate_existing=True)
            )
            if row is None:
                # Lỗi ràng buộc toàn vẹn khác (ví dụ FK user_id không tồn tại), không che giấu bằng default giả.
                raise
        if created and commit_if_created:
            await db.commit()
    return UserPreference(
        revision=row.revision,
        effective_revision=row.effective_revision,
        requested_revision=row.requested_revision,
        work_interval_seconds=row.work_interval_seconds,
        rest_interval_seconds=row.rest_interval_seconds,
        micro_task_seconds=row.micro_task_seconds if row.micro_task_seconds is not None else 120,
        reminder_offsets=row.reminder_offsets,
        buffer_multiplier=row.buffer_multiplier,
        reminder_style=row.reminder_style,
        requested_changes=row.requested_changes,
        pending_proposal_id=str(row.pending_proposal_id) if row.pending_proposal_id else None,
    )


async def _availability(db: AsyncSession, user_id: str, *, for_update: bool = True) -> Availability:
    stmt = (
        select(AvailabilityRow)
        .where(AvailabilityRow.user_id == draft_service.resolve_user_uuid(user_id))
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
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


def _context_schema(row: PlanningContext, *, source_draft_id: str | None) -> CalendarContext:
    ref = None
    if row.availability_id is not None:
        revision = row.availability_revision
        if revision is None:
            raise _error("SOURCE_CHANGED", "Nguồn giờ cũ thiếu revision.")
        ref = AvailabilityRef(availability_id=str(row.availability_id), revision=revision)
    return CalendarContext(
        context_id=str(row.id),
        revision=row.revision,
        availability_ref=ref,
        source_draft_id=source_draft_id,
        source_summary_revision=row.source_summary_revision,
        availability_snapshot=row.availability_snapshot,
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
    source_draft_id = None
    if row.source_draft_id is not None:
        _, draft = await _read_draft(db, user_id, str(row.source_draft_id))
        source_draft_id = draft.draft_id
    return _context_schema(row, source_draft_id=source_draft_id)


async def _validate_context_sources_and_overlaps(
    db: AsyncSession,
    *,
    user_id: str,
    payload: PlanningContextCreateRequest,
    selected: set[tuple[str, str]],
    for_update: bool,
) -> PlanningDraft | None:
    """Validate draft/availability, selected calendar connections, and non-narrowing overlaps."""
    draft: PlanningDraft | None = None
    if payload.source_draft_id is not None:
        _, draft = await _read_draft(db, user_id, payload.source_draft_id)
        if draft.summary_revision != payload.expected_summary_revision:
            raise _error("REVISION_CONFLICT", "Bản tóm tắt đã thay đổi.")
        _require_draft(draft, draft.summary_revision, draft.planning_input_revision)
        if draft.summary.availability is None:
            raise _error("PLAN_NOT_CONFIRMABLE", "Draft chưa có giờ rảnh riêng được xác nhận.")
    else:
        if payload.availability_ref is None:
            raise _invalid("Thiếu nguồn giờ của Context.", "availability_ref")
        availability = await _availability(db, user_id, for_update=for_update)
        if (
            payload.availability_ref.availability_id != availability.availability_id
            or payload.availability_ref.revision != availability.revision
            or availability.confirmed_at is None
        ):
            raise _error("SOURCE_CHANGED", "Nguồn giờ rảnh cũ đã thay đổi.")

    owner_uuid = draft_service.resolve_user_uuid(user_id)
    for connection_id, calendar_id in selected:
        connection = await db.scalar(
            select(CalendarConnection)
            .where(
                CalendarConnection.id == _uuid(connection_id),
                CalendarConnection.user_id == owner_uuid,
            )
            .execution_options(populate_existing=True)
        )
        if connection is None or calendar_id not in connection.read_calendar_ids:
            raise _error(
                "RESOURCE_NOT_FOUND", "Không tìm thấy nguồn lịch được phép đọc.", 404, "reload"
            )
        if connection.connection_state == "disconnected":
            raise _error(
                "CALENDAR_CONTEXT_UNAVAILABLE",
                "Kết nối Google Calendar đã bị ngắt. Vui lòng kết nối lại.",
                action="connect_calendar",
            )

    if draft is not None:
        overlaps = (
            await db.scalars(
                select(PlanningContext).where(
                    PlanningContext.user_id == owner_uuid,
                    PlanningContext.source_draft_id
                    == draft_service.resolve_draft_uuid(draft.draft_id),
                    PlanningContext.source_summary_revision == draft.summary_revision,
                    PlanningContext.query_start < payload.to_at,
                    PlanningContext.query_end > payload.from_at,
                    PlanningContext.sync_state == "valid",
                )
            )
        ).all()
        if any(
            (row.busy_intervals or row.manual_busy_intervals or row.selected_calendar_sources)
            and (row.query_start < payload.from_at or row.query_end > payload.to_at)
            for row in overlaps
        ):
            raise _invalid(
                "Ngữ cảnh mới cần bao phủ ngữ cảnh cũ để không làm mất lịch bận ngoài khoảng cập nhật.",
                "from/to",
            )
    return draft


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
    validation_now = datetime.now(UTC)
    if any(
        item.user_confirmed_at.utcoffset() is None
        or item.user_confirmed_at > validation_now
        or not item.reason.strip()
        for item in payload.excluded_sources
    ):
        raise _invalid(
            "Cần lý do và thời điểm user đã xác nhận loại nguồn lịch.", "excluded_sources"
        )

    # Phase 1: kiểm tra điều kiện tiên quyết ở chế độ đọc, không giữ lock/transaction khi gọi Google HTTP.
    await db.rollback()
    saved = await _receipt(db, user_id, idempotency_key, digest)
    if saved is not None:
        await db.rollback()
        return CalendarContext.model_validate(saved)
    initial_draft = await _validate_context_sources_and_overlaps(
        db,
        user_id=user_id,
        payload=payload,
        selected=selected,
        for_update=False,
    )
    await db.rollback()

    # Phase 2: gọi Google Calendar HTTP và cập nhật token (nếu refresh/lỗi) ngoài transaction chính.
    ingestion = await ingest_calendar_sources(
        db=db,
        user_id=user_id,
        selected_sources=payload.selected_calendar_sources,
        excluded_sources=payload.excluded_sources,
        query_start=payload.from_at,
        query_end=payload.to_at,
        manual_busy_intervals=payload.manual_busy_intervals,
        target_task_id=initial_draft.confirmed_task_id if initial_draft else None,
    )
    await db.rollback()
    if ingestion.coverage_state == "degraded":
        raise _error(
            "CALENDAR_UNAVAILABLE",
            "Không thể đọc một hoặc nhiều nguồn lịch đã chọn. Vui lòng thử lại hoặc xác nhận loại trừ nguồn lỗi.",
            503,
            "retry_or_review_calendar_sources",
        )

    # Phase 3: khóa user và ghi PlanningContext + CommandReceipt nguyên tử.
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return CalendarContext.model_validate(saved)
        draft = await _validate_context_sources_and_overlaps(
            db,
            user_id=user_id,
            payload=payload,
            selected=selected,
            for_update=True,
        )
        if (
            initial_draft is not None
            and draft is not None
            and (
                draft.summary.availability != initial_draft.summary.availability
                or draft.confirmed_task_id != initial_draft.confirmed_task_id
            )
        ):
            raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã thay đổi trong lúc đọc Google Calendar.")

        now = datetime.now(UTC)
        result = CalendarContext(
            context_id=str(uuid4()),
            revision=1,
            availability_ref=payload.availability_ref,
            source_draft_id=draft.draft_id if draft else None,
            source_summary_revision=draft.summary_revision if draft else None,
            availability_snapshot=draft.summary.availability if draft else None,
            query_start_end=TimeInterval(start_at=payload.from_at, end_at=payload.to_at),
            fetched_at=now,
            selected_calendar_ids=[
                item.google_calendar_id for item in payload.selected_calendar_sources
            ],
            selected_calendar_sources=payload.selected_calendar_sources,
            busy_intervals=ingestion.busy_intervals,
            event_snapshots=ingestion.event_snapshots,
            manual_busy_intervals=payload.manual_busy_intervals,
            excluded_sources=payload.excluded_sources,
            calendar_errors=ingestion.calendar_errors,
            sync_state="valid",
            coverage_state=ingestion.coverage_state,
        )
        if draft is not None:
            await db.execute(
                update(PlanningContext)
                .where(
                    PlanningContext.user_id == draft_service.resolve_user_uuid(user_id),
                    PlanningContext.source_draft_id
                    == draft_service.resolve_draft_uuid(draft.draft_id),
                    PlanningContext.source_summary_revision == draft.summary_revision,
                    PlanningContext.query_start < payload.to_at,
                    PlanningContext.query_end > payload.from_at,
                    PlanningContext.sync_state == "valid",
                )
                .values(sync_state="superseded")
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
        availability_id=_uuid(context.availability_ref.availability_id)
        if context.availability_ref
        else None,
        availability_revision=context.availability_ref.revision
        if context.availability_ref
        else None,
        source_draft_id=draft_service.resolve_draft_uuid(context.source_draft_id)
        if context.source_draft_id
        else None,
        source_summary_revision=context.source_summary_revision,
        availability_snapshot=context.availability_snapshot.model_dump(mode="json")
        if context.availability_snapshot
        else None,
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
        title=row.title,
        session_goal=row.session_goal,
        expected_output=row.expected_output,
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
    context_ref: CalendarContextRef | None = None,
    read_only: bool = False,
) -> PlanningSources:
    owner = draft_service.resolve_user_uuid(user_id)
    inline = draft.summary.availability
    ref = draft.summary.availability_ref
    legacy = None
    if inline is None:
        legacy = await _availability(db, user_id, for_update=not read_only)
        availability = legacy
        if (
            ref is None
            or ref.availability_id != legacy.availability_id
            or ref.revision != legacy.revision
        ):
            raise _error("SOURCE_CHANGED", "Nguồn giờ rảnh cũ đã thay đổi.")
    else:
        availability = inline
    valid, issues = validate_planning_prerequisites(draft.summary, availability, datetime.now(UTC))
    if not valid:
        raise AppError(
            status_code=409,
            code="PLAN_NOT_CONFIRMABLE",
            message="Thông tin UF1 chưa đủ để xếp lịch.",
            field_errors=[
                FieldError(field_path=i.field_path, code=i.code, message=i.message) for i in issues
            ],
            next_action="fix_fields",
        )
    preferences = await get_preferences(
        user_id=user_id,
        db=db,
        commit_if_created=(create_manual and not read_only),
    )
    query = select(PlanningContext).where(
        PlanningContext.user_id == owner,
        PlanningContext.query_start <= horizon.start_at,
        PlanningContext.query_end >= horizon.end_at,
        PlanningContext.sync_state == "valid",
        or_(PlanningContext.expires_at.is_(None), PlanningContext.expires_at > datetime.now(UTC)),
    )
    if inline is not None:
        query = query.where(
            PlanningContext.source_draft_id == draft_service.resolve_draft_uuid(draft.draft_id),
            PlanningContext.source_summary_revision == draft.summary_revision,
            PlanningContext.availability_id.is_(None),
        )
    else:
        assert legacy is not None
        query = query.where(
            PlanningContext.availability_id == _uuid(legacy.availability_id),
            PlanningContext.availability_revision == legacy.revision,
            PlanningContext.source_draft_id.is_(None),
        )
    if context_ref is not None:
        query = query.where(
            PlanningContext.id == _uuid(context_ref.context_id),
            PlanningContext.revision == context_ref.revision,
        )
    ctx_stmt = query.order_by(PlanningContext.created_at.desc(), PlanningContext.id.desc()).limit(1)
    if not read_only:
        ctx_stmt = ctx_stmt.with_for_update()
    row = await db.scalar(ctx_stmt)
    if row is None:
        # Context ngắn/hết hạn vẫn có thể chứa lịch bận đã khai báo. Không biến
        # việc thiếu coverage thành một lịch trống và ghi đè hiểu biết đó.
        known_query = select(PlanningContext).where(
            PlanningContext.user_id == owner,
            PlanningContext.query_start < horizon.end_at,
            PlanningContext.query_end > horizon.start_at,
        )
        if inline is not None:
            known_query = known_query.where(
                PlanningContext.source_draft_id == draft_service.resolve_draft_uuid(draft.draft_id)
            )
        else:
            assert legacy is not None
            known_query = known_query.where(
                PlanningContext.availability_id == _uuid(legacy.availability_id)
            )
        known = (await db.scalars(known_query)).all()
        if any(
            c.busy_intervals or c.manual_busy_intervals or c.selected_calendar_sources
            for c in known
        ):
            raise _error(
                "CALENDAR_CONTEXT_UNAVAILABLE",
                "Đã có lịch bận nhưng ngữ cảnh chưa đủ phạm vi hoặc đã hết hạn. Hãy cập nhật lịch bận trước khi xếp lại.",
                action="create_planning_context",
            )
        # Kết nối user đã chủ động ngắt không còn là nguồn lịch → được xếp theo giờ
        # rảnh nhập tay. Kết nối lỗi/thu hồi (needs_reconnect) vẫn chặn cho tới khi
        # user xác nhận loại nguồn (excluded_sources) qua planning-context.
        configured = (
            await db.scalars(
                select(CalendarConnection).where(
                    CalendarConnection.user_id == owner,
                    CalendarConnection.connection_state != "disconnected",
                )
            )
        ).all()
        if (
            context_ref is not None
            or not create_manual
            or any(c.connection_state == "connected" and c.read_calendar_ids for c in configured)
        ):
            raise _error(
                "CALENDAR_CONTEXT_UNAVAILABLE",
                "Cần ngữ cảnh đúng nguồn và còn hiệu lực; Google thật chưa được tích hợp.",
                action="create_planning_context",
            )
        context = CalendarContext(
            context_id=str(uuid4()),
            revision=1,
            availability_ref=ref if inline is None else None,
            source_draft_id=draft.draft_id if inline is not None else None,
            source_summary_revision=draft.summary_revision if inline is not None else None,
            availability_snapshot=inline,
            query_start_end=horizon,
            fetched_at=datetime.now(UTC),
            sync_state="valid",
            coverage_state="manual_only",
        )
        db.add(_context_row(user_id, context))
        await db.commit()
    else:
        context = _context_schema(
            row, source_draft_id=draft.draft_id if inline is not None else None
        )
    if inline is not None and context.availability_snapshot != inline:
        raise _error("SOURCE_CHANGED", "Bản chụp giờ rảnh không khớp Draft.")
    if context.coverage_state not in {"complete", "manual_fallback", "manual_only"}:
        raise _error(
            "CALENDAR_CONTEXT_UNAVAILABLE",
            "Ngữ cảnh chưa đủ nguồn.",
            action="create_planning_context",
        )
    selected = {(x.connection_id, x.google_calendar_id) for x in context.selected_calendar_sources}
    excluded = {(x.connection_id, x.google_calendar_id) for x in context.excluded_sources}
    errors = {(x.connection_id, x.google_calendar_id) for x in context.calendar_errors}
    if (
        not excluded.issubset(selected)
        or errors - excluded
        or (excluded and context.coverage_state != "manual_fallback")
        or (
            ((selected - excluded) or (context.selected_calendar_ids and not excluded))
            and context.coverage_state not in {"complete", "manual_fallback"}
        )
    ):
        # Không coi một nguồn Google chưa đọc trọn là không bận. Nguồn đã đọc
        # thành công (coverage complete) thì khoảng bận của nó được trừ khỏi giờ
        # rảnh trong compute_effective_free_intervals.
        raise _error(
            "CALENDAR_CONTEXT_UNAVAILABLE",
            "Nguồn Google chưa được kiểm; cần xem lại phạm vi nguồn.",
            action="create_planning_context",
        )
    fixed = (
        await db.scalars(
            select(SessionRow).where(
                SessionRow.user_id == owner,
                SessionRow.status.notin_(("cancelled", "skipped")),
            )
        )
    ).all()
    return PlanningSources(
        availability,
        preferences,
        context,
        SourceRevisions(
            availability=legacy.revision if legacy is not None else None,
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
    # Giữ thời gian xem/duyệt và khởi động trước phiên đầu; không tự chốt lịch đã quá giờ.
    start = max(
        tc.start_at, datetime.now(UTC) + timedelta(seconds=settings.PLANNING_REVIEW_LEAD_SECONDS)
    )
    end = min(terminal, start + timedelta(days=settings.DEFAULT_PLANNING_HORIZON_DAYS))
    if requested is not None:
        start = max(start, requested.start_at)
        end = min(
            terminal,
            requested.end_at,
            start + timedelta(days=settings.MAX_PLANNING_HORIZON_DAYS),
        )
    if start >= end:
        raise _invalid("Phạm vi lập lịch không còn thời gian hợp lệ.", "planning_horizon")
    return TimeInterval(start_at=start, end_at=end)


def _full_project_end(draft: PlanningDraft) -> datetime:
    tc = draft.summary.time_constraint
    if tc is None:
        raise _invalid("Thiếu ràng buộc thời gian.")
    terminal = tc.deadline_at if tc.kind == "deadline" else tc.finish_by_at
    if terminal is None:
        raise _invalid("Thiếu mốc kết thúc.")
    return terminal.astimezone(UTC)


def _candidate_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Đọc snapshot cũ tại biên, không đổi receipt hoặc áp dụng lại nghiệp vụ."""
    data = json.loads(json.dumps(data, default=str))
    parts = data.pop("planning_parts", [])
    part_order = {p["client_part_key"]: p["order_index"] for p in parts}
    steps = data.get("steps", [])
    legacy = any("client_part_key" in step for step in steps)
    if (
        parts
        or legacy
        or data.get("feasibility", {}).get("verification_scope", {}).get("scope_type")
        == "candidate_parts"
    ):
        data["stale"] = True
    selected_parts = set(
        data.get("feasibility", {}).get("verification_scope", {}).get("client_part_keys", [])
    )
    selected_steps = [
        step["client_step_key"]
        for step in steps
        if not selected_parts or step.get("client_part_key") in selected_parts
    ]
    if legacy:
        steps.sort(
            key=lambda item: (
                part_order.get(item.get("client_part_key"), 0),
                item["order_index"],
                item["client_step_key"],
            )
        )
    for index, step in enumerate(steps):
        step.pop("client_part_key", None)
        for key in list(step):
            if key.startswith("_"):
                step.pop(key)
        if legacy:
            step["order_index"] = index
    remaining = [
        p.get("unexpanded_scope") or p.get("scope_description", "")
        for p in parts
        if p.get("decomposition_state") != "complete"
    ]
    data.setdefault("unexpanded_scope", "\n".join(filter(None, remaining)) or None)
    data.setdefault("decomposition_state", "partial" if data["unexpanded_scope"] else "complete")
    report = data.get("feasibility", {})
    scope = report.get("verification_scope", {})
    if scope.get("scope_type") == "candidate_parts":
        scope["scope_type"] = "candidate_steps"
        scope["client_step_keys"] = selected_steps
    scope.pop("client_part_keys", None)
    report.pop("unestimated_part_ids", None)
    report.pop("unplanned_part_ids", None)
    return data


def _candidate_schema(row: CandidateRow, draft_id: str) -> PlanCandidate:
    if not row.steps:
        raise _error("SOURCE_CHANGED", "Phương án cũ chưa đủ dữ liệu; cần tạo lại.")
    return PlanCandidate.model_validate(
        _candidate_payload(
            dict(
                candidate_id=str(row.id),
                draft_id=draft_id,
                source_summary_revision=row.source_summary_revision,
                source_planning_input_revision=row.source_planning_input_revision,
                review_revision=row.review_revision,
                decomposition_state=row.decomposition_state,
                unexpanded_scope=row.unexpanded_scope,
                steps=row.steps,
                sessions=row.sessions,
                planning_horizon=row.planning_horizon,
                source_revisions=row.source_revisions,
                min_inter_session_gap_seconds=row.min_inter_session_gap_seconds,
                feasibility=row.feasibility,
                stale=row.schema_version < 2,
                can_undo=row.previous_snapshot is not None,
                user_step_proposal=row.user_step_proposal,
            )
        )
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
        source = await _sources(
            db,
            user_id,
            draft,
            candidate.planning_horizon,
            context_ref=candidate.source_revisions.calendar_context,
            read_only=True,
        )
        stale = candidate.stale or source.revisions != candidate.source_revisions
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
        "decomposition_state": candidate.decomposition_state,
        "unexpanded_scope": candidate.unexpanded_scope,
        "schema_version": 2,
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


def _selection(steps: list[CandidateStep], selected: list[str] | None) -> list[str]:
    ordered = [item.client_step_key for item in sorted(steps, key=lambda item: item.order_index)]
    if selected is None:
        return ordered
    if len(set(selected)) != len(selected) or not set(selected).issubset(ordered):
        raise _invalid(
            "Bước được chọn bị trùng hoặc không có trong phương án.", "selected_step_keys"
        )
    result = [key for key in ordered if key in selected]
    if result != ordered[: len(result)]:
        raise _invalid("Các bước tuần tự; phải chọn từ bước đầu.", "selected_step_keys")
    return result


def _reschedule(
    candidate: PlanCandidate,
    source: PlanningSources,
    micro_tasks: dict[str, str],
    selected: list[str],
    *,
    full_end: datetime | None = None,
    extra_requirements: Sequence[str] | None = None,
    allow_horizon_slice: bool = False,
) -> PlanCandidate:
    horizon = candidate.planning_horizon
    steps = [item for item in candidate.steps if item.client_step_key in selected]
    free = compute_effective_free_intervals(
        source.availability,
        horizon.start_at,
        horizon.end_at,
        calendar_context=source.context,
        manual_busy=source.context.manual_busy_intervals,
        fixed_sessions=source.fixed_sessions,
    )
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
        horizon_days=max(1, math.ceil((horizon.end_at - horizon.start_at).total_seconds() / 86400)),
        min_gap_seconds=candidate.min_inter_session_gap_seconds,
        extra_requirements=extra_requirements,
    )
    step1_overflow_first_horizon = False
    evaluated_full_horizon = False
    if (
        allow_horizon_slice
        and full_end is not None
        and full_end > horizon.end_at
        and not report.fully_scheduled
    ):
        evaluated_full_horizon = True
        full_free = compute_effective_free_intervals(
            source.availability,
            horizon.start_at,
            full_end,
            calendar_context=source.context,
            manual_busy=source.context.manual_busy_intervals,
            fixed_sessions=source.fixed_sessions,
        )
        full_tc = TimeConstraint(
            kind="window",
            start_at=horizon.start_at,
            finish_by_at=full_end,
            input_timezone=source.availability.timezone,
        )
        _, full_report = schedule_candidate_sessions(
            steps,
            full_free,
            source.fixed_sessions,
            full_tc,
            source.preferences,
            horizon_days=max(1, math.ceil((full_end - horizon.start_at).total_seconds() / 86400)),
            min_gap_seconds=candidate.min_inter_session_gap_seconds,
            extra_requirements=extra_requirements,
        )
        ordered_steps = sorted(steps, key=lambda item: item.order_index)
        visible_step_keys: list[str] = []
        for step in ordered_steps:
            expected_work = (
                calculate_buffered_work(step.remaining_base_seconds, step.buffer_multiplier)
                if step.remaining_base_seconds
                else 0
            )
            scheduled_work = sum(
                item.work_seconds
                for item in sessions
                if item.client_step_key == step.client_step_key
            )
            if scheduled_work == expected_work:
                visible_step_keys.append(step.client_step_key)
            else:
                break
        visible_sessions = [
            item for item in sessions if item.client_step_key in visible_step_keys
        ]
        if full_report.fully_scheduled and visible_step_keys:
            sessions = visible_sessions
            report = report.model_copy(
                update={
                    "status": "feasible",
                    "fully_scheduled": True,
                    "verification_scope": CandidateVerificationScope(
                        client_step_keys=visible_step_keys,
                        planning_horizon=horizon,
                    ),
                    "required_seconds": sum(item.duration_seconds for item in visible_sessions),
                    "available_seconds": report.available_seconds,
                    "deficit_seconds": 0,
                    "unscheduled_seconds": 0,
                    "blocking_reasons": [],
                    "unscheduled_step_keys": [],
                    "extension_candidate_at": visible_sessions[-1].scheduled_end
                    if visible_sessions
                    else None,
                    "applied_extra_constraints": report.applied_extra_constraints,
                }
            )
        else:
            sessions = visible_sessions
            if full_report.fully_scheduled and not visible_step_keys:
                step1_overflow_first_horizon = True
                fallback_status = "search_inconclusive"
                fallback_reasons = [
                    BlockingReason(
                        code="FRAGMENTED_FREE_SLOTS",
                        message="Bước đầu tiên vượt quá quỹ thời gian của chặng 14 ngày đầu; cần chia nhỏ bước đầu tiên hơn nữa.",
                    )
                ]
                fallback_unscheduled = report.unscheduled_seconds
                fallback_unscheduled_keys = report.unscheduled_step_keys
            else:
                fallback_status = full_report.status
                fallback_reasons = full_report.blocking_reasons
                fallback_unscheduled = full_report.unscheduled_seconds
                fallback_unscheduled_keys = full_report.unscheduled_step_keys
            report = report.model_copy(
                update={
                    "status": fallback_status,
                    "fully_scheduled": False,
                    "required_seconds": None,
                    "verification_scope": CandidateVerificationScope(
                        client_step_keys=visible_step_keys or selected,
                        planning_horizon=horizon,
                    ),
                    "available_seconds": report.available_seconds,
                    "deficit_seconds": full_report.deficit_seconds,
                    "unscheduled_seconds": fallback_unscheduled,
                    "unscheduled_step_keys": fallback_unscheduled_keys,
                    "blocking_reasons": fallback_reasons,
                    "extension_candidate_at": None,
                    "applied_extra_constraints": report.applied_extra_constraints,
                }
            )
    if report.fully_scheduled:
        report = report.model_copy(
            update={
                "earliest_feasible_end_at": None,
                "suggested_extra_free_seconds_per_day": None,
            }
        )
    else:
        horizon_only_overflow = step1_overflow_first_horizon or (
            not evaluated_full_horizon
            and full_end is not None
            and full_end > horizon.end_at
        )
        recovery_end = (
            horizon.end_at
            if horizon_only_overflow
            else max(horizon.end_at, full_end or horizon.end_at)
        )
        earliest_end, extra_per_day = compute_feasibility_recovery(
            report,
            steps,
            source.availability,
            source.fixed_sessions,
            horizon.start_at,
            recovery_end,
            source.preferences,
            calendar_context=source.context,
            manual_busy=source.context.manual_busy_intervals,
            min_gap_seconds=candidate.min_inter_session_gap_seconds,
            extra_requirements=extra_requirements,
            input_timezone=source.availability.timezone,
            skip_deadline_extension=horizon_only_overflow,
        )
        report = report.model_copy(
            update={
                "earliest_feasible_end_at": earliest_end,
                "suggested_extra_free_seconds_per_day": extra_per_day,
            }
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
    report = report.model_copy(update={"unexpanded_scope": candidate.unexpanded_scope})
    return candidate.model_copy(update={"sessions": sessions, "feasibility": report})


def build_calendar_event_text(
    session_title: str | None,
    step_title: str | None,
    session_goal: str | None,
    expected_output: str | None,
) -> tuple[str, str | None]:
    """Tạo cặp (summary, description) chuẩn hóa cho Google Calendar event từ session & step."""
    clean_title = (session_title or "").strip() or (step_title or "").strip() or "Focus Session"
    summary = f"[ProPlan] {clean_title}"[:200]
    desc_parts: list[str] = []
    if session_goal and session_goal.strip():
        desc_parts.append(f"Mục tiêu: {session_goal.strip()}")
    if expected_output and expected_output.strip():
        desc_parts.append(f"Đầu ra dự kiến: {expected_output.strip()}")
    description = "\n".join(desc_parts) if desc_parts else None
    return summary, description


async def _enrich_candidate_sessions(candidate: PlanCandidate, task_title: str) -> PlanCandidate:
    """Làm giàu title/session_goal/expected_output/micro_task_text cho sessions (chạy không giữ lock DB)."""
    if not candidate.sessions:
        return candidate

    step_map = {step.client_step_key: step for step in candidate.steps}
    grouped: dict[str, list[CandidateSession]] = {}
    for sess in sorted(candidate.sessions, key=lambda item: item.scheduled_start):
        grouped.setdefault(sess.client_step_key, []).append(sess)

    updates_by_key: dict[str, dict[str, Any]] = {}
    split_inputs: list[SplitSessionInputItem] = []
    split_part_index: dict[str, int] = {}

    def _build_single_update(
        sess: CandidateSession,
        *,
        title: str,
        expected_output: str,
        continuation_micro: str | None = None,
    ) -> dict[str, Any]:
        input_micro = (
            continuation_micro if continuation_micro is not None else sess.micro_task_text
        )
        enriched = ai_service.build_single_session_enrichment(
            client_session_key=sess.client_session_key,
            step_title=title,
            step_expected_output=expected_output,
            micro_task_text=input_micro,
        )
        has_micro = bool(sess.micro_task_text and sess.micro_task_text.strip())
        return {
            "title": enriched.title,
            "session_goal": enriched.session_goal,
            "expected_output": enriched.expected_output,
            "micro_task_text": (
                enriched.micro_task_text
                if (continuation_micro is not None or not has_micro)
                else sess.micro_task_text
            ),
        }

    def _fallback_continuation_micro(clean_title: str, part_index: int, total: int) -> str | None:
        if part_index < 2:
            return None
        return f"Mở lại tài liệu đang làm dở của '{clean_title[:180].rstrip()}' để làm tiếp phần {part_index}/{total}."

    for step_key, group in grouped.items():
        step = step_map.get(step_key)
        if step is None:
            continue
        clean_title = (step.title or "").strip() or "Bước hiện tại"
        clean_output = (step.expected_output or "").strip() or clean_title
        if len(group) == 1:
            sess = group[0]
            updates_by_key[sess.client_session_key] = _build_single_update(
                sess,
                title=clean_title,
                expected_output=clean_output,
            )
        elif len(group) <= 50:
            total = len(group)
            group_inputs: list[SplitSessionInputItem] = []
            try:
                for idx, sess in enumerate(group):
                    part_index = idx + 1
                    split_part_index[sess.client_session_key] = part_index
                    group_inputs.append(
                        SplitSessionInputItem(
                            client_session_key=sess.client_session_key,
                            client_step_key=step.client_step_key,
                            step_title=clean_title[:255],
                            step_expected_output=clean_output[:500],
                            step_micro_task_prompt=(
                                sess.micro_task_text.strip()[:300]
                                if sess.micro_task_text and sess.micro_task_text.strip()
                                else None
                            ),
                            session_index=part_index,
                            total_sessions_in_step=total,
                            work_minutes=max(1, min(480, round(sess.work_seconds / 60))),
                        )
                    )
                split_inputs.extend(group_inputs)
            except ValidationError:
                for idx, sess in enumerate(group):
                    part_index = idx + 1
                    updates_by_key[sess.client_session_key] = _build_single_update(
                        sess,
                        title=f"{clean_title} (Phần {part_index}/{total})",
                        expected_output=clean_output,
                        continuation_micro=_fallback_continuation_micro(
                            clean_title, part_index, total
                        ),
                    )
        else:
            total = len(group)
            for idx, sess in enumerate(group):
                part_index = idx + 1
                updates_by_key[sess.client_session_key] = _build_single_update(
                    sess,
                    title=f"{clean_title} (Phần {part_index}/{total})",
                    expected_output=clean_output,
                    continuation_micro=_fallback_continuation_micro(
                        clean_title, part_index, total
                    ),
                )

    if split_inputs:
        enrichment_results: list[Any] = []
        try:
            for start in range(0, len(split_inputs), 60):
                chunk = split_inputs[start : start + 60]
                res = await ai_service.enrich_split_sessions(
                    split_sessions=chunk, task_title=task_title
                )
                enrichment_results.extend(res.sessions)
        except (ValidationError, ValueError):
            enrichment_results = []
        enriched_map = {item.client_session_key: item for item in enrichment_results}
        for sess in candidate.sessions:
            if sess.client_session_key not in split_part_index:
                continue
            item = enriched_map.get(sess.client_session_key)
            part_index = split_part_index[sess.client_session_key]
            if item is None:
                step = step_map.get(sess.client_step_key)
                clean_title = (step.title if step else "").strip() or "Bước hiện tại"
                clean_output = (step.expected_output if step else "").strip() or clean_title
                total = len(grouped.get(sess.client_step_key, [sess]))
                updates_by_key[sess.client_session_key] = _build_single_update(
                    sess,
                    title=f"{clean_title} (Phần {part_index}/{total})",
                    expected_output=clean_output,
                    continuation_micro=_fallback_continuation_micro(
                        clean_title, part_index, total
                    ),
                )
                continue
            has_micro = bool(sess.micro_task_text and sess.micro_task_text.strip())
            updates_by_key[sess.client_session_key] = {
                "title": item.title,
                "session_goal": item.session_goal,
                "expected_output": item.expected_output,
                "micro_task_text": (
                    item.micro_task_text
                    if (part_index >= 2 or not has_micro)
                    else sess.micro_task_text
                ),
            }

    if not updates_by_key:
        return candidate

    enriched_sessions = [
        sess.model_copy(update=updates_by_key[sess.client_session_key])
        if sess.client_session_key in updates_by_key
        else sess
        for sess in candidate.sessions
    ]
    return candidate.model_copy(update={"sessions": enriched_sessions})


def _next_step_key(existing_keys: set[str]) -> str:
    """Sinh client_step_key dạng 'step-{n}' chưa tồn tại trong tập khóa hiện có."""
    max_num = 0
    for key in existing_keys:
        if key.startswith("step-"):
            suffix = key[5:]
            if suffix.isdigit():
                max_num = max(max_num, int(suffix))
    candidate_num = max_num + 1
    while f"step-{candidate_num}" in existing_keys:
        candidate_num += 1
    return f"step-{candidate_num}"


def _apply_structural_step_ops(
    steps: list[CandidateStep],
    micro_tasks: dict[str, str],
    edits: CandidateEdits,
    *,
    default_buffer: float,
    tracked_scope: set[str] | None = None,
) -> tuple[list[CandidateStep], dict[str, str], dict[str, str], bool]:
    """Áp dụng delete, merge, split, add và edit lên CandidateSteps; giữ và cập nhật tập khóa scope nếu truyền vào."""
    work_steps = list(steps)
    work_micro = dict(micro_tasks)
    step_micro_updates: dict[str, str] = {}
    existing_keys = {step.client_step_key for step in work_steps}
    in_scope = set(tracked_scope or ())
    all_were_in_scope = tracked_scope is not None and tracked_scope >= existing_keys

    edited_keys = [edit.client_step_key for edit in (edits.steps or [])]
    deleted_keys = list(edits.delete_step_keys or [])
    merged_keys = [key for op in (edits.merge_steps or []) for key in op.source_step_keys]
    split_keys = [op.source_step_key for op in (edits.split_steps or [])]

    for collection_name, keys in (
        ("edits.steps", edited_keys),
        ("edits.delete_step_keys", deleted_keys),
        ("edits.merge_steps", merged_keys),
        ("edits.split_steps", split_keys),
    ):
        if len(set(keys)) != len(keys):
            raise _invalid("Client key không tồn tại hoặc bị gửi trùng.", collection_name)
        if any(key not in existing_keys for key in keys):
            raise _invalid("Client key bước không tồn tại trong phương án.", collection_name)

    edited_set = set(edited_keys)
    deleted_set = set(deleted_keys)
    merged_set = set(merged_keys)
    split_set = set(split_keys)
    combined_sets = edited_set | deleted_set | merged_set | split_set
    if len(edited_set) + len(deleted_set) + len(merged_set) + len(split_set) != len(combined_sets):
        raise _invalid("Không thể thực hiện nhiều thao tác xung đột trên cùng một bước.", "edits")

    structural_changed = bool(
        edits.delete_step_keys or edits.merge_steps or edits.split_steps or edits.add_steps
    )
    used_keys = set(existing_keys)

    if edits.delete_step_keys:
        work_steps = [step for step in work_steps if step.client_step_key not in deleted_set]
        for key in deleted_set:
            work_micro.pop(key, None)
        in_scope -= deleted_set

    for merge_op in edits.merge_steps or []:
        src_set = set(merge_op.source_step_keys)
        src_indices = [
            idx for idx, step in enumerate(work_steps) if step.client_step_key in src_set
        ]
        if len(src_indices) != len(src_set):
            raise _invalid("Client key bước cần gộp không còn tồn tại.", "edits.merge_steps")
        src_steps = [work_steps[idx] for idx in src_indices]
        insert_pos = min(src_indices)
        merged_key = _next_step_key(used_keys)
        used_keys.add(merged_key)
        merged_base_seconds = (
            merge_op.estimated_base_seconds
            if merge_op.estimated_base_seconds is not None
            else min(28800, max(300, sum(s.estimated_base_seconds for s in src_steps)))
        )
        merged_step = CandidateStep(
            client_step_key=merged_key,
            title=merge_op.title,
            expected_output=merge_op.expected_output,
            order_index=0,
            estimated_base_seconds=merged_base_seconds,
            remaining_base_seconds=merged_base_seconds,
            buffer_multiplier=default_buffer,
            estimate_source="user_confirmed",
            status="pending",
        )
        merged_micro = (
            merge_op.micro_task_text
            or work_micro.get(src_steps[0].client_step_key)
            or f"Chuẩn bị cho bước: {merge_op.title}"
        )
        for key in src_set:
            work_micro.pop(key, None)
        work_micro[merged_key] = merged_micro
        work_steps = [step for step in work_steps if step.client_step_key not in src_set]
        work_steps.insert(insert_pos, merged_step)
        if any(key in in_scope for key in src_set):
            in_scope -= src_set
            in_scope.add(merged_key)

    for split_op in edits.split_steps or []:
        matches = [
            idx
            for idx, step in enumerate(work_steps)
            if step.client_step_key == split_op.source_step_key
        ]
        if not matches:
            raise _invalid("Client key bước cần tách không còn tồn tại.", "edits.split_steps")
        split_idx = matches[0]
        work_micro.pop(split_op.source_step_key, None)
        child_steps: list[CandidateStep] = []
        child_keys: list[str] = []
        for item in split_op.new_steps:
            child_key = _next_step_key(used_keys)
            used_keys.add(child_key)
            child_keys.append(child_key)
            child_steps.append(
                CandidateStep(
                    client_step_key=child_key,
                    title=item.title,
                    expected_output=item.expected_output,
                    order_index=0,
                    estimated_base_seconds=item.estimated_base_seconds,
                    remaining_base_seconds=item.estimated_base_seconds,
                    buffer_multiplier=default_buffer,
                    estimate_source="user_confirmed",
                    status="pending",
                )
            )
            work_micro[child_key] = item.micro_task_text or f"Chuẩn bị cho bước: {item.title}"
        work_steps = work_steps[:split_idx] + child_steps + work_steps[split_idx + 1 :]
        if split_op.source_step_key in in_scope:
            in_scope.discard(split_op.source_step_key)
            in_scope.update(child_keys)

    for add_item in edits.add_steps or []:
        new_key = _next_step_key(used_keys)
        used_keys.add(new_key)
        new_step = CandidateStep(
            client_step_key=new_key,
            title=add_item.title,
            expected_output=add_item.expected_output,
            order_index=0,
            estimated_base_seconds=add_item.estimated_base_seconds,
            remaining_base_seconds=add_item.estimated_base_seconds,
            buffer_multiplier=default_buffer,
            estimate_source="user_confirmed",
            status="pending",
        )
        work_micro[new_key] = add_item.micro_task_text or f"Chuẩn bị cho bước: {add_item.title}"
        if add_item.insert_at_index is not None:
            pos = max(0, min(add_item.insert_at_index, len(work_steps)))
            if all_were_in_scope or all(
                s.client_step_key in in_scope for s in work_steps[:pos]
            ):
                in_scope.add(new_key)
            work_steps.insert(pos, new_step)
        else:
            if all_were_in_scope:
                in_scope.add(new_key)
            work_steps.append(new_step)

    if structural_changed:
        work_steps = [
            step.model_copy(update={"order_index": idx}) for idx, step in enumerate(work_steps)
        ]

    if not 1 <= len(work_steps) <= 20:
        raise _invalid("Số lượng bước trong phương án phải nằm trong khoảng 1..20.", "edits")

    if edits.steps:
        step_index_by_key = {step.client_step_key: idx for idx, step in enumerate(work_steps)}
        for edit in edits.steps:
            idx = step_index_by_key[edit.client_step_key]
            changes = edit.model_dump(
                exclude_unset=True, exclude={"client_step_key", "micro_task_text"}
            )
            if "estimated_base_seconds" in changes:
                changes["remaining_base_seconds"] = changes["estimated_base_seconds"]
                changes["estimate_source"] = "user_confirmed"
            if changes:
                work_steps[idx] = CandidateStep.model_validate(
                    {**work_steps[idx].model_dump(), **changes}
                )
            if edit.micro_task_text is not None:
                work_micro[edit.client_step_key] = edit.micro_task_text
                step_micro_updates[edit.client_step_key] = edit.micro_task_text

    if len({step.order_index for step in work_steps}) != len(work_steps):
        raise _invalid("Thứ tự bước không được trùng.", "edits.steps")
    work_steps = sorted(work_steps, key=lambda step: step.order_index)

    if tracked_scope is not None:
        tracked_scope.clear()
        if all_were_in_scope:
            tracked_scope.update(step.client_step_key for step in work_steps)
        else:
            tracked_scope.update(in_scope)

    return work_steps, work_micro, step_micro_updates, structural_changed


def _make_undo_snapshot(row: CandidateRow) -> dict[str, Any]:
    """Chụp snapshot JSON đầy đủ (gồm cả _micro_task_text trong steps) để hỗ trợ hoàn tác 1 bước."""
    return copy.deepcopy(
        {
            "steps": row.steps,
            "sessions": row.sessions,
            "feasibility": row.feasibility,
            "decomposition_state": row.decomposition_state,
            "unexpanded_scope": row.unexpanded_scope,
            "planning_horizon": row.planning_horizon,
            "review_revision": row.review_revision,
        }
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
        return PlanCandidate.model_validate(_candidate_payload(saved))
    _, draft = await _read_draft(db, user_id, draft_id)
    _require_draft(draft, payload.source_summary_revision, payload.source_planning_input_revision)
    horizon = _horizon(draft, payload.planning_horizon)
    source = await _sources(
        db, user_id, draft, horizon, create_manual=True, context_ref=payload.calendar_context_ref
    )
    full_end = max(horizon.end_at, _full_project_end(draft))
    planning_horizon_days = max(
        1, math.ceil((horizon.end_at - horizon.start_at).total_seconds() / 86400)
    )
    total_project_days = max(1, math.ceil((full_end - horizon.start_at).total_seconds() / 86400))
    full_effective = compute_effective_free_intervals(
        source.availability,
        horizon.start_at,
        full_end,
        calendar_context=source.context,
        manual_busy=source.context.manual_busy_intervals,
        fixed_sessions=source.fixed_sessions,
    )
    full_free_with_gaps = subtract_fixed_session_gaps(
        full_effective,
        source.fixed_sessions,
        horizon.start_at,
        full_end,
        min_gap_seconds=900,
    )
    total_free_seconds = int(sum((b - a).total_seconds() for a, b in full_free_with_gaps))
    total_free_hours = round(total_free_seconds / 3600.0, 2)
    previous = None
    if payload.selected_step_keys is not None:
        previous = await db.scalar(
            select(CandidateRow)
            .where(
                CandidateRow.user_id == draft_service.resolve_user_uuid(user_id),
                CandidateRow.draft_id == draft_service.resolve_draft_uuid(draft_id),
                CandidateRow.source_summary_revision == draft.summary_revision,
                CandidateRow.source_planning_input_revision == draft.planning_input_revision,
                CandidateRow.schema_version == 2,
            )
            .order_by(CandidateRow.created_at.desc(), CandidateRow.id.desc())
            .limit(1)
        )
        if previous is None:
            raise _invalid("Cần có phương án nguồn trước khi chọn Steps.", "selected_step_keys")
        old = _candidate_schema(previous, draft_id)
        steps = [
            step.model_copy(update={"buffer_multiplier": source.preferences.buffer_multiplier})
            for step in old.steps
        ]
        micro_tasks, tier, unexpanded = (
            _micro_tasks(previous),
            previous.generation_tier,
            old.unexpanded_scope,
        )
        selected = _selection(steps, payload.selected_step_keys)
    await db.rollback()
    if payload.manual_steps is not None:
        steps = [
            CandidateStep(
                client_step_key=f"step-{index + 1}",
                title=item.title,
                expected_output=item.expected_output,
                order_index=index,
                estimated_base_seconds=item.estimated_base_seconds,
                remaining_base_seconds=item.estimated_base_seconds,
                buffer_multiplier=source.preferences.buffer_multiplier,
                estimate_source="user_confirmed",
                status="pending",
            )
            for index, item in enumerate(
                sorted(payload.manual_steps, key=lambda step: step.order_index)
            )
        ]
        micro_tasks, tier, unexpanded = {}, "manual_user", payload.unexpanded_scope
        selected = _selection(steps, None)
    elif previous is None:
        task_title_text = draft.summary.title or draft.summary.description or ""
        task_scope_text = "\n".join(
            v
            for v in (
                draft.summary.description,
                draft.summary.scope,
                draft.summary.goal,
            )
            if v
        )
        focus_minutes = max(1, source.preferences.work_interval_seconds // 60)
        try:
            if total_free_seconds == 0:
                raw_decomposition = ai_service._fallback_decompose_task(
                    task_title_text,
                    user_focus_duration=focus_minutes,
                    scope=task_scope_text,
                    user_step_proposal=draft.user_step_proposal,
                    extra_requirements=draft.summary.extra_requirements,
                    planning_horizon_days=planning_horizon_days,
                    total_project_days=total_project_days,
                )
            else:
                raw_decomposition = await ai_service.decompose_task(
                    task_title=task_title_text,
                    scope=task_scope_text,
                    extra_requirements=draft.summary.extra_requirements,
                    user_step_proposal=draft.user_step_proposal,
                    user_focus_duration=focus_minutes,
                    planning_horizon_days=planning_horizon_days,
                    total_project_days=total_project_days,
                    total_free_hours=total_free_hours,
                )
            decomposition = TaskDecompositionResult.model_validate(raw_decomposition)
            if any(
                not item.expected_output or not item.expected_output.strip()
                for item in decomposition.steps
            ):
                raise ValueError("Missing expected output")
        except (ValidationError, ValueError, TimeoutError) as exc:
            raise _error(
                "AI_UNAVAILABLE",
                "Chưa có kết quả chia bước hợp lệ; bản nháp được giữ nguyên.",
                503,
                "retry",
            ) from exc
        if decomposition.requires_clarification:
            question = (
                decomposition.clarification_question or "Bạn muốn chia các bước theo cách nào?"
            )
            raise AppError(
                status_code=422,
                code="DECOMPOSITION_REQUIRES_CLARIFICATION",
                message=question,
                next_action="clarify_step_proposal",
                field_errors=[FieldError(field_path="user_step_proposal", message=question)],
            )
        tier, unexpanded = decomposition.generation_tier, decomposition.unexpanded_scope
        steps, micro_tasks = [], {}
        for index, item in enumerate(decomposition.steps):
            key = f"step-{index + 1}"
            if item.expected_output is None:
                raise _error("AI_UNAVAILABLE", "Bước chưa có đầu ra.", 503, "retry")
            steps.append(
                CandidateStep(
                    client_step_key=key,
                    title=item.title,
                    expected_output=item.expected_output,
                    order_index=index,
                    estimated_base_seconds=item.estimated_base_seconds,
                    remaining_base_seconds=item.estimated_base_seconds,
                    buffer_multiplier=source.preferences.buffer_multiplier,
                    estimate_source="ai_suggested",
                    status="pending",
                )
            )
            micro_tasks[key] = item.micro_task_prompt
        selected = _selection(steps, None)
    draft_title_text = draft.summary.title or draft.summary.description or ""
    tentative = PlanCandidate(
        candidate_id=str(uuid4()),
        draft_id=draft.draft_id,
        source_summary_revision=draft.summary_revision,
        source_planning_input_revision=draft.planning_input_revision,
        review_revision=1,
        decomposition_state="partial" if unexpanded else "complete",
        unexpanded_scope=unexpanded,
        steps=steps,
        sessions=[],
        planning_horizon=horizon,
        source_revisions=source.revisions,
        min_inter_session_gap_seconds=900,
        feasibility=FeasibilityReport(
            status="not_checked",
            fully_scheduled=False,
            verification_scope=CandidateVerificationScope(
                client_step_keys=selected, planning_horizon=horizon
            ),
        ),
        stale=False,
        user_step_proposal=draft.user_step_proposal,
    )
    tentative = _reschedule(
        tentative,
        source,
        micro_tasks,
        selected,
        full_end=full_end,
        extra_requirements=draft.summary.extra_requirements,
        allow_horizon_slice=(payload.selected_step_keys is None and not unexpanded),
    )
    tentative = await _enrich_candidate_sessions(tentative, draft_title_text)
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return PlanCandidate.model_validate(_candidate_payload(saved))
        draft_row, current = await _read_draft(db, user_id, draft_id)
        _require_draft(
            current, payload.source_summary_revision, payload.source_planning_input_revision
        )
        current_source = await _sources(
            db, user_id, current, horizon, context_ref=source.revisions.calendar_context
        )
        if current_source.revisions != source.revisions:
            raise _error("SOURCE_CHANGED", "Nguồn đổi trong khi AI xử lý; cần tạo lại phương án.")
        candidate = tentative.model_copy(
            update={
                "draft_id": current.draft_id,
                "source_summary_revision": current.summary_revision,
                "source_planning_input_revision": current.planning_input_revision,
                "source_revisions": current_source.revisions,
                "user_step_proposal": current.user_step_proposal,
            }
        )
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
        fixed_sessions=source.fixed_sessions,
    )
    sessions = sorted(candidate.sessions, key=lambda item: item.scheduled_start)
    scope = candidate.feasibility.verification_scope
    if not isinstance(scope, CandidateVerificationScope):
        raise _invalid("Phạm vi Candidate không hợp lệ.")
    selected = set(scope.client_step_keys)
    ordered_steps = sorted(
        [step for step in candidate.steps if step.client_step_key in selected],
        key=lambda step: step.order_index,
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
        expected_work = (
            calculate_buffered_work(step.remaining_base_seconds, step.buffer_multiplier)
            if step.remaining_base_seconds
            else 0
        )
        if sum(item.work_seconds for item in children) != expected_work:
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
        rest_int = source.preferences.rest_interval_seconds
        if not item.segments or [
            segment.order_index for segment in item.segments
        ] != list(range(len(item.segments))):
            issue("INVALID_SEGMENTS", "Thứ tự các quãng làm/nghỉ không hợp lệ.")
        work_segments = [
            segment.duration_seconds for segment in item.segments if segment.kind == "work"
        ]
        rest_segments = [
            segment.duration_seconds for segment in item.segments if segment.kind == "rest"
        ]
        common_segments_ok = (
            bool(item.segments)
            and all(segment.duration_seconds > 0 for segment in item.segments)
            and sum(work_segments) == item.work_seconds
            and sum(rest_segments) == item.rest_seconds
            and item.work_seconds + item.rest_seconds == item.duration_seconds
            and bool(work_segments)
            and all(
                0 < value <= source.preferences.work_interval_seconds for value in work_segments
            )
            and item.segments[-1].kind == "work"
        )
        if rest_int == 0:
            rest_segments_ok = (
                not rest_segments
                and item.rest_seconds == 0
                and [segment.kind for segment in item.segments] == ["work"] * len(item.segments)
            )
        else:
            rest_segments_ok = (
                all(value == rest_int for value in rest_segments)
                and [segment.kind for segment in item.segments]
                == ["work" if index % 2 == 0 else "rest" for index in range(len(item.segments))]
            )
        if not (common_segments_ok and rest_segments_ok):
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
    scope = candidate.feasibility.verification_scope
    all_steps_in_scope = isinstance(scope, CandidateVerificationScope) and {
        step.client_step_key for step in candidate.steps
    }.issubset(set(scope.client_step_keys))
    global_codes = {"TOTAL_DEFICIT", "FRAGMENTED_FREE_SLOTS"}
    global_reasons = [
        item for item in candidate.feasibility.blocking_reasons if item.code in global_codes
    ]
    if not reasons:
        has_global_deficit = (candidate.feasibility.deficit_seconds or 0) > 0
        has_global_unscheduled = (candidate.feasibility.unscheduled_seconds or 0) > 0 or bool(
            candidate.feasibility.unscheduled_step_keys
        )
        if (source is None or not all_steps_in_scope) and (
            has_global_deficit or has_global_unscheduled
        ):
            preserved_status = "infeasible" if has_global_deficit else "search_inconclusive"
            return candidate.model_copy(
                update={
                    "feasibility": candidate.feasibility.model_copy(
                        update={
                            "status": preserved_status,
                            "fully_scheduled": False,
                            "blocking_reasons": global_reasons,
                            "required_seconds": candidate.feasibility.required_seconds
                            or sum(item.duration_seconds for item in candidate.sessions),
                            "deficit_seconds": candidate.feasibility.deficit_seconds or 0,
                            "unscheduled_seconds": candidate.feasibility.unscheduled_seconds or 0,
                        }
                    )
                }
            )

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
                        "unscheduled_step_keys": [],
                        "earliest_feasible_end_at": None,
                        "suggested_extra_free_seconds_per_day": None,
                    }
                )
            }
        )

    carry_global = any(item.code == "UNSCHEDULED_WORK" for item in reasons) or (
        (source is None or not all_steps_in_scope)
        and (candidate.feasibility.deficit_seconds or 0) > 0
    )
    merged_reasons = list(global_reasons) if carry_global else []
    seen_codes = {reason.code for reason in merged_reasons}
    for item in reasons:
        if item.code not in seen_codes:
            seen_codes.add(item.code)
            merged_reasons.append(item)

    deficit_val = candidate.feasibility.deficit_seconds or 0
    has_deficit = deficit_val > 0 or any(item.code == "TOTAL_DEFICIT" for item in merged_reasons)
    has_unscheduled_work = any(item.code == "UNSCHEDULED_WORK" for item in reasons)
    infeasible_codes = {
        "SESSION_COLLISION",
        "OUTSIDE_AVAILABILITY",
        "STEP_ORDER",
        "PAST_SESSION",
        "INVALID_SEGMENTS",
    }
    if (
        any(item.code in infeasible_codes for item in reasons)
        or has_deficit
        or (has_unscheduled_work and deficit_val > 0)
    ):
        new_status = "infeasible"
    elif candidate.feasibility.status in {"feasible", "not_checked"}:
        new_status = "search_inconclusive"
    else:
        new_status = candidate.feasibility.status

    report = candidate.feasibility.model_copy(
        update={
            "status": new_status,
            "fully_scheduled": False,
            "required_seconds": None,
            "blocking_reasons": merged_reasons,
            "deficit_seconds": deficit_val,
            "unscheduled_seconds": candidate.feasibility.unscheduled_seconds or 0,
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
        changes = edit.model_dump(exclude_unset=True, exclude={key_name, "micro_task_text"})
        if "estimated_base_seconds" in changes:
            changes["remaining_base_seconds"] = changes["estimated_base_seconds"]
            changes["estimate_source"] = "user_confirmed"
        if changes:
            result[indexes[0]] = model.model_validate({**result[indexes[0]].model_dump(), **changes})
    return result


def _apply_session_edits(
    sessions: list[CandidateSession],
    session_edits: Sequence[Any] | None,
    micro_tasks: dict[str, str],
) -> list[CandidateSession]:
    """Áp dụng chỉnh sửa lên CandidateSessions (thời gian, title, session_goal, expected_output, micro_task_text)."""
    result = list(sessions)
    first_session_by_step: dict[str, str] = {}
    for sess in sorted(result, key=lambda item: item.scheduled_start):
        first_session_by_step.setdefault(sess.client_step_key, sess.client_session_key)
    seen: set[str] = set()
    for edit in session_edits or []:
        indexes = [
            index
            for index, item in enumerate(result)
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
                seconds=result[index].duration_seconds
            )
        elif (
            "scheduled_end" in changes
            and "scheduled_start" not in changes
            and changes["scheduled_end"] is not None
        ):
            changes["scheduled_start"] = changes["scheduled_end"] - timedelta(
                seconds=result[index].duration_seconds
            )
        if "scheduled_start" in changes and changes["scheduled_start"] is not None:
            changes["start_decision_due_at"] = changes["scheduled_start"] + timedelta(seconds=900)
        result[index] = CandidateSession.model_validate({**result[index].model_dump(), **changes})
        if edit.micro_task_text is not None:
            step_key = result[index].client_step_key
            if first_session_by_step.get(step_key) == edit.client_session_key:
                micro_tasks[step_key] = edit.micro_task_text
    return result


def _resolve_selected_steps_after_ops(
    snapshot: PlanCandidate,
    new_steps: list[CandidateStep],
    *,
    full_end: datetime,
    structural_changed: bool,
    tracked_scope: set[str] | None = None,
) -> list[str]:
    """Xác định tập bước trong verification_scope sau khi áp dụng thay đổi hoặc thao tác cấu trúc."""
    scope = snapshot.feasibility.verification_scope
    if not isinstance(scope, CandidateVerificationScope):
        raise _invalid("Phạm vi Candidate không hợp lệ.")
    is_horizon_sliced = (
        full_end > snapshot.planning_horizon.end_at
        and not snapshot.unexpanded_scope
        and (
            snapshot.feasibility.extension_candidate_at is not None
            or len(scope.client_step_keys) < len(snapshot.steps)
        )
    )
    if is_horizon_sliced:
        return [step.client_step_key for step in new_steps]
    if not structural_changed:
        return _selection(new_steps, list(scope.client_step_keys))
    if set(scope.client_step_keys) >= {step.client_step_key for step in snapshot.steps}:
        return [step.client_step_key for step in new_steps]
    ordered = [step.client_step_key for step in sorted(new_steps, key=lambda item: item.order_index)]
    in_scope = tracked_scope if tracked_scope is not None else set(scope.client_step_keys)
    selected_indices = [idx for idx, key in enumerate(ordered) if key in in_scope]
    if not selected_indices:
        return [ordered[0]]
    return ordered[: max(selected_indices) + 1]


def _nl_adjustment_to_edits(
    adjustment: Any,
    snapshot: PlanCandidate,
    current_micro_tasks: dict[str, str],
) -> CandidateEdits:
    """Chuyển đổi 5 mảng thao tác từ NL adjustment sang CandidateEdits đã kiểm chứng."""
    existing = {step.client_step_key: step for step in snapshot.steps}
    step_changes: list[dict[str, Any]] = []
    for edit in adjustment.step_edits:
        old_step = existing.get(edit.client_step_key)
        if old_step is None:
            raise _invalid("AI trả về khóa bước không có trong phương án.", "user_instruction")
        values = edit.model_dump(exclude_none=True)
        est_mins = values.pop("estimated_minutes", None)
        if values.get("estimated_base_seconds") is None and est_mins is not None:
            values["estimated_base_seconds"] = est_mins * 60
        filtered: dict[str, Any] = {"client_step_key": edit.client_step_key}
        for key, value in values.items():
            if key == "client_step_key":
                continue
            if key == "micro_task_text":
                if current_micro_tasks.get(edit.client_step_key) != value:
                    filtered[key] = value
            elif getattr(old_step, key) != value:
                filtered[key] = value
        if len(filtered) > 1:
            step_changes.append(filtered)

    added_steps = [
        {
            "title": item.title,
            "expected_output": item.expected_output,
            "estimated_base_seconds": (
                item.estimated_base_seconds
                if item.estimated_base_seconds is not None
                else (item.estimated_minutes * 60 if item.estimated_minutes else 1500)
            ),
            "micro_task_text": item.micro_task_prompt,
            "insert_at_index": item.insert_at_index,
        }
        for item in adjustment.add_steps
    ]
    deleted_keys = list(adjustment.delete_step_keys)
    merged_steps = [
        {
            "source_step_keys": list(item.source_step_keys),
            "title": item.title,
            "expected_output": item.expected_output,
            "estimated_base_seconds": (
                item.estimated_base_seconds
                if item.estimated_base_seconds is not None
                else (item.estimated_minutes * 60 if item.estimated_minutes is not None else None)
            ),
            "micro_task_text": item.micro_task_prompt,
        }
        for item in adjustment.merge_steps
    ]
    split_steps = [
        {
            "source_step_key": item.source_step_key,
            "new_steps": [
                {
                    "title": sub.title,
                    "expected_output": sub.expected_output,
                    "estimated_base_seconds": (
                        sub.estimated_base_seconds
                        if sub.estimated_base_seconds is not None
                        else (sub.estimated_minutes * 60 if sub.estimated_minutes else 1500)
                    ),
                    "micro_task_text": sub.micro_task_prompt,
                    "insert_at_index": sub.insert_at_index,
                }
                for sub in item.new_steps
            ],
        }
        for item in adjustment.split_steps
    ]

    edits_dict: dict[str, Any] = {}
    if step_changes:
        edits_dict["steps"] = step_changes
    if added_steps:
        edits_dict["add_steps"] = added_steps
    if deleted_keys:
        edits_dict["delete_step_keys"] = deleted_keys
    if merged_steps:
        edits_dict["merge_steps"] = merged_steps
    if split_steps:
        edits_dict["split_steps"] = split_steps
    if not edits_dict:
        raise _error(
            "NO_CANDIDATE_CHANGES",
            "Chưa có thay đổi hợp lệ; phương án được giữ nguyên.",
            422,
            "review_candidate",
        )
    try:
        return CandidateEdits.model_validate(edits_dict)
    except ValidationError as exc:
        raise _invalid(
            "AI chưa trả chỉnh sửa hợp lệ; phương án được giữ nguyên.", "user_instruction"
        ) from exc


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
    edits = payload.edits
    snapshot: PlanCandidate | None = None
    initial_source: PlanningSources | None = None
    initial_micro_tasks: dict[str, str] | None = None
    task_title_text = ""
    full_end: datetime | None = None
    extra_requirements: list[str] = []

    if payload.user_instruction is not None:
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return PlanCandidate.model_validate(_candidate_payload(saved))
        row, draft, snapshot = await _read_candidate(db, user_id, candidate_id)
        _require_draft(
            draft, snapshot.source_summary_revision, snapshot.source_planning_input_revision
        )
        if payload.expected_review_revision != snapshot.review_revision:
            raise _error("REVISION_CONFLICT", "Phiên bản phương án đã thay đổi.")
        initial_source = await _sources(
            db,
            user_id,
            draft,
            snapshot.planning_horizon,
            context_ref=snapshot.source_revisions.calendar_context,
            read_only=True,
        )
        if snapshot.stale or initial_source.revisions != snapshot.source_revisions:
            raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã đổi; cần tạo phương án mới.")
        initial_micro_tasks = _micro_tasks(row)
        task_title_text = draft.summary.title or draft.summary.description or ""
        extra_requirements = list(draft.summary.extra_requirements or [])
        full_end = max(snapshot.planning_horizon.end_at, _full_project_end(draft))
        # Chụp DTO trước khi chờ AI; không giữ transaction/khóa DB khi gọi provider.
        await db.rollback()
        try:
            adjustment = await ai_service.adjust_candidate_by_nl(
                payload.user_instruction,
                current_steps=snapshot.steps,
                time_constraint=draft.summary.time_constraint,
                availability=draft.summary.availability,
                unexpanded_scope=snapshot.unexpanded_scope,
            )
        except (ValidationError, ValueError, TimeoutError) as exc:
            raise _error(
                "AI_UNAVAILABLE",
                "Chưa phân tích được yêu cầu; phương án cũ được giữ nguyên.",
                503,
                "retry",
            ) from exc
        if adjustment.requires_draft_update:
            raise AppError(
                status_code=422,
                code="DRAFT_UPDATE_REQUIRED",
                message="Yêu cầu đổi giờ rảnh/thời hạn cần sửa và xác nhận lại tóm tắt UF1 trước.",
                next_action="edit_draft_and_reconfirm",
                field_errors=[
                    FieldError(
                        field_path="user_instruction",
                        message=adjustment.clarification_question or adjustment.assistant_note,
                    )
                ],
            )
        if adjustment.requires_clarification or adjustment.unsupported_changes:
            question = adjustment.clarification_question or adjustment.assistant_note
            raise AppError(
                status_code=422,
                code="UNSUPPORTED_CANDIDATE_CHANGE"
                if adjustment.unsupported_changes
                else "ADJUSTMENT_REQUIRES_CLARIFICATION",
                message=question,
                next_action="clarify_candidate_adjustment",
                field_errors=[FieldError(field_path="user_instruction", message=question)],
            )
        edits = _nl_adjustment_to_edits(adjustment, snapshot, initial_micro_tasks)

    if edits is None:
        raise _invalid("Cần nội dung chỉnh phương án.", "edits")

    has_structural_ops = bool(
        edits.add_steps or edits.delete_step_keys or edits.merge_steps or edits.split_steps
    )
    changed_timing = has_structural_ops or any(
        {"estimated_base_seconds", "order_index"} & edit.model_fields_set
        for edit in edits.steps or []
    )
    if changed_timing and edits.sessions:
        raise _invalid(
            "Không thể đồng thời đổi thời lượng/thứ tự bước và chỉnh giờ phiên trong cùng một yêu cầu; hãy áp thay đổi bước trước.",
            "edits",
        )

    if changed_timing:
        if snapshot is None or initial_source is None or initial_micro_tasks is None:
            saved = await _receipt(db, user_id, idempotency_key, digest)
            if saved is not None:
                return PlanCandidate.model_validate(_candidate_payload(saved))
            row, draft, snapshot = await _read_candidate(db, user_id, candidate_id)
            _require_draft(
                draft, snapshot.source_summary_revision, snapshot.source_planning_input_revision
            )
            if payload.expected_review_revision != snapshot.review_revision:
                raise _error("REVISION_CONFLICT", "Phiên bản phương án đã thay đổi.")
            initial_source = await _sources(
                db,
                user_id,
                draft,
                snapshot.planning_horizon,
                context_ref=snapshot.source_revisions.calendar_context,
                read_only=True,
            )
            if snapshot.stale or initial_source.revisions != snapshot.source_revisions:
                raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã đổi; cần tạo phương án mới.")
            initial_micro_tasks = _micro_tasks(row)
            task_title_text = draft.summary.title or draft.summary.description or ""
            extra_requirements = list(draft.summary.extra_requirements or [])
            full_end = max(snapshot.planning_horizon.end_at, _full_project_end(draft))
            await db.rollback()

        assert full_end is not None
        scope = snapshot.feasibility.verification_scope
        if not isinstance(scope, CandidateVerificationScope):
            raise _invalid("Phạm vi Candidate không hợp lệ.")
        tracked_scope = set(scope.client_step_keys)
        try:
            new_steps, new_micro_tasks, _, structural_changed = _apply_structural_step_ops(
                list(snapshot.steps),
                initial_micro_tasks,
                edits,
                default_buffer=initial_source.preferences.buffer_multiplier,
                tracked_scope=tracked_scope,
            )
            selected = _resolve_selected_steps_after_ops(
                snapshot,
                new_steps,
                full_end=full_end,
                structural_changed=structural_changed,
                tracked_scope=tracked_scope,
            )
            tentative = PlanCandidate.model_validate(
                {
                    **snapshot.model_dump(),
                    "steps": new_steps,
                    "sessions": [],
                    "review_revision": snapshot.review_revision + 1,
                }
            )
        except ValidationError as exc:
            raise _invalid(
                "Giá trị sửa không hợp lệ hoặc thời lượng phiên không khớp.", "edits"
            ) from exc
        tentative = _reschedule(
            tentative,
            initial_source,
            new_micro_tasks,
            selected,
            full_end=full_end,
            extra_requirements=extra_requirements,
            allow_horizon_slice=(
                not snapshot.unexpanded_scope and full_end > snapshot.planning_horizon.end_at
            ),
        )
        tentative = await _enrich_candidate_sessions(tentative, task_title_text)
        tentative = _review_feasibility(tentative, initial_source)

        async with db.begin():
            await _lock_user(db, user_id)
            saved = await _receipt(db, user_id, idempotency_key, digest)
            if saved is not None:
                return PlanCandidate.model_validate(_candidate_payload(saved))
            row, draft, candidate = await _read_candidate(db, user_id, candidate_id)
            _require_draft(
                draft, candidate.source_summary_revision, candidate.source_planning_input_revision
            )
            if payload.expected_review_revision != candidate.review_revision:
                raise _error("REVISION_CONFLICT", "Phiên bản phương án đã thay đổi.")
            source = await _sources(
                db,
                user_id,
                draft,
                candidate.planning_horizon,
                context_ref=candidate.source_revisions.calendar_context,
            )
            if (
                source.revisions != candidate.source_revisions
                or source.revisions != initial_source.revisions
            ):
                raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã đổi; cần tạo phương án mới.")
            if candidate.stale:
                raise _error("SOURCE_CHANGED", "Phương án cũ cần tạo lại theo hợp đồng mới.")
            row.previous_snapshot = _make_undo_snapshot(row)
            updated = tentative.model_copy(update={"can_undo": True})
            for name, value in _candidate_values(updated, new_micro_tasks).items():
                setattr(row, name, value)
            await db.flush()
            draft_row, current = await _read_draft(db, user_id, draft.draft_id)
            if (
                current.candidate_ref is not None
                and current.candidate_ref.candidate_id == candidate_id
            ):
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

    await db.rollback()
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return PlanCandidate.model_validate(_candidate_payload(saved))
        row, draft, candidate = await _read_candidate(db, user_id, candidate_id)
        _require_draft(
            draft, candidate.source_summary_revision, candidate.source_planning_input_revision
        )
        if payload.expected_review_revision != candidate.review_revision:
            raise _error("REVISION_CONFLICT", "Phiên bản phương án đã thay đổi.")
        source = await _sources(
            db,
            user_id,
            draft,
            candidate.planning_horizon,
            context_ref=candidate.source_revisions.calendar_context,
        )
        if source.revisions != candidate.source_revisions:
            raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã đổi; cần tạo phương án mới.")
        if candidate.stale:
            raise _error("SOURCE_CHANGED", "Phương án cũ cần tạo lại theo hợp đồng mới.")
        try:
            steps, micro_tasks, step_micro_updates, _ = _apply_structural_step_ops(
                list(candidate.steps),
                _micro_tasks(row),
                edits,
                default_buffer=source.preferences.buffer_multiplier,
            )
            sessions = list(candidate.sessions)
            if step_micro_updates:
                first_session_by_step: dict[str, str] = {}
                for sess in sorted(sessions, key=lambda item: item.scheduled_start):
                    first_session_by_step.setdefault(sess.client_step_key, sess.client_session_key)
                sessions = [
                    item.model_copy(
                        update={"micro_task_text": step_micro_updates[item.client_step_key]}
                    )
                    if (
                        item.client_step_key in step_micro_updates
                        and first_session_by_step.get(item.client_step_key)
                        == item.client_session_key
                    )
                    else item
                    for item in sessions
                ]
            sessions = _apply_session_edits(sessions, edits.sessions, micro_tasks)
            updated = PlanCandidate.model_validate(
                {
                    **candidate.model_dump(),
                    "steps": steps,
                    "sessions": sessions,
                    "review_revision": candidate.review_revision + 1,
                }
            )
        except ValidationError as exc:
            raise _invalid(
                "Giá trị sửa không hợp lệ hoặc thời lượng phiên không khớp.", "edits"
            ) from exc
        # Kiểm lại lịch cụ thể, không suy khả thi chỉ từ tổng phút.
        updated = _review_feasibility(updated, source)
        row.previous_snapshot = _make_undo_snapshot(row)
        updated = updated.model_copy(update={"can_undo": True})
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


async def undo_candidate_edit(
    *,
    user_id: str,
    candidate_id: str,
    idempotency_key: str,
    payload: UndoCandidateRequest,
    db: AsyncSession,
) -> PlanCandidate:
    """Hoàn tác 1 bước chỉnh sửa gần nhất trên Candidate trước khi Confirm (UF2)."""
    digest = _hash("undo_candidate", candidate_id, payload.model_dump(mode="json"))
    await db.rollback()
    async with db.begin():
        await _lock_user(db, user_id)
        saved = await _receipt(db, user_id, idempotency_key, digest)
        if saved is not None:
            return PlanCandidate.model_validate(_candidate_payload(saved))
        row, draft, candidate = await _read_candidate(db, user_id, candidate_id)
        _require_draft(
            draft, candidate.source_summary_revision, candidate.source_planning_input_revision
        )
        if payload.expected_review_revision != row.review_revision:
            raise _error("REVISION_CONFLICT", "Phiên bản phương án đã thay đổi.")
        if row.previous_snapshot is None:
            raise _error(
                "UNDO_UNAVAILABLE",
                "Không còn thao tác chỉnh sửa nào để hoàn tác.",
                409,
                "review_candidate",
            )
        source = await _sources(
            db,
            user_id,
            draft,
            candidate.planning_horizon,
            context_ref=candidate.source_revisions.calendar_context,
        )
        if source.revisions != candidate.source_revisions:
            raise _error("SOURCE_CHANGED", "Nguồn lập lịch đã đổi; cần tạo phương án mới.")
        if candidate.stale:
            raise _error("SOURCE_CHANGED", "Phương án cũ cần tạo lại theo hợp đồng mới.")

        snap = copy.deepcopy(row.previous_snapshot)
        snap_steps = snap["steps"]
        restored_micro_tasks = {
            item["client_step_key"]: item.get(
                "_micro_task_text", f"Chuẩn bị cho bước: {item['title']}"
            )
            for item in snap_steps
        }
        clean_steps = [
            {k: v for k, v in item.items() if not k.startswith("_")} for item in snap_steps
        ]
        restored = PlanCandidate.model_validate(
            {
                "candidate_id": str(row.id),
                "draft_id": draft.draft_id,
                "source_summary_revision": row.source_summary_revision,
                "source_planning_input_revision": row.source_planning_input_revision,
                "review_revision": candidate.review_revision + 1,
                "decomposition_state": snap["decomposition_state"],
                "unexpanded_scope": snap["unexpanded_scope"],
                "steps": clean_steps,
                "sessions": snap["sessions"],
                "planning_horizon": snap["planning_horizon"],
                "source_revisions": candidate.source_revisions.model_dump(mode="json"),
                "min_inter_session_gap_seconds": candidate.min_inter_session_gap_seconds,
                "feasibility": snap["feasibility"],
                "stale": False,
                "can_undo": False,
                "user_step_proposal": candidate.user_step_proposal,
            }
        )
        restored = _review_feasibility(restored, source)
        restored = restored.model_copy(update={"can_undo": False})
        row.previous_snapshot = None
        for name, value in _candidate_values(restored, restored_micro_tasks).items():
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
                            candidate_id=candidate_id, review_revision=restored.review_revision
                        )
                    }
                ),
            )
        _record(
            db,
            user_id,
            idempotency_key,
            digest,
            "undo_candidate",
            restored,
            candidate_id=candidate_id,
        )
    return restored


async def _enqueue_calendar(
    db: AsyncSession,
    owner: UUID,
    sessions: list[SessionRow],
    version: int,
    *,
    step_titles: dict[UUID, str] | None = None,
) -> str:
    """Tạo CalendarEventLink và CalendarSyncJob trong cùng transaction khi chốt lịch UF2."""
    stmt = (
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == owner,
            CalendarConnection.connection_state == "connected",
            CalendarConnection.write_calendar_id.is_not(None),
        )
        .order_by(CalendarConnection.created_at.desc())
        .limit(1)
    )
    conn = (await db.execute(stmt)).scalar_one_or_none()
    if not conn or not conn.write_calendar_id:
        return "not_integrated"

    for session in sessions:
        event_id = f"session{session.id.hex}"
        link = CalendarEventLink(
            id=uuid4(),
            user_id=owner,
            session_id=session.id,
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

        step_title = step_titles.get(session.step_id) if step_titles else None
        summary, description = build_calendar_event_text(
            session.title,
            step_title,
            session.session_goal,
            session.expected_output,
        )
        payload_snapshot: dict[str, Any] = {
            "id": event_id,
            "summary": summary,
            "start": {
                "dateTime": session.scheduled_start.isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "end": {
                "dateTime": session.scheduled_end.isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "transparency": "opaque",
            "reminders": {"useDefault": False, "overrides": []},
            "extendedProperties": {
                "private": {
                    "proactive_session_id": str(session.id),
                    "proactive_schedule_revision": str(version),
                }
            },
        }
        if description is not None:
            payload_snapshot["description"] = description

        job = CalendarSyncJob(
            id=uuid4(),
            user_id=owner,
            connection_id=conn.id,
            event_link_id=link.id,
            target_kind="session_event",
            target_ref=str(session.id),
            operation="create",
            target_schedule_revision=version,
            expected_google_etag=None,
            payload_snapshot=payload_snapshot,
            idempotency_key=f"sync_session:{session.id}:v{version}:create",
            job_state="queued",
            attempt_count=0,
        )
        db.add(job)

    return "pending"


async def _enqueue_calendar_deletes_for_task(
    db: AsyncSession,
    owner: UUID,
    session_ids: list[UUID],
    version: int,
    now: datetime,
) -> Literal["not_integrated", "pending", "synced", "failed"]:
    """Đóng job create chưa đồng bộ hoặc xếp hàng job delete khi hủy Task."""
    if not session_ids:
        return "not_integrated"

    links = (
        await db.scalars(
            select(CalendarEventLink)
            .where(
                CalendarEventLink.user_id == owner,
                CalendarEventLink.session_id.in_(session_ids),
                CalendarEventLink.is_current.is_(True),
            )
            .order_by(CalendarEventLink.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    if not links:
        return "not_integrated"

    for link in links:
        queued_jobs = (
            await db.scalars(
                select(CalendarSyncJob)
                .where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.event_link_id == link.id,
                    CalendarSyncJob.job_state == "queued",
                )
                .order_by(CalendarSyncJob.created_at, CalendarSyncJob.id)
                .execution_options(populate_existing=True)
            )
        ).all()

        unleased_jobs = [
            job
            for job in queued_jobs
            if job.lease_expires_at is None or job.lease_expires_at <= now
        ]
        leased_jobs = [
            job
            for job in queued_jobs
            if job.lease_expires_at is not None and job.lease_expires_at > now
        ]

        if unleased_jobs:
            await db.execute(
                update(CalendarSyncJob)
                .where(
                    CalendarSyncJob.id.in_([job.id for job in unleased_jobs]),
                    CalendarSyncJob.job_state == "queued",
                    or_(
                        CalendarSyncJob.lease_expires_at.is_(None),
                        CalendarSyncJob.lease_expires_at <= now,
                    ),
                )
                .values(
                    job_state="completed",
                    last_error_code=None,
                    lease_expires_at=None,
                    updated_at=now,
                    revision=CalendarSyncJob.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )

        is_unattempted_create = (
            link.synced_schedule_revision is None
            and not leased_jobs
            and any(job.operation == "create" for job in unleased_jobs)
            and all(
                job.attempt_count == 0
                for job in unleased_jobs
                if job.operation == "create"
            )
        )

        if is_unattempted_create or link.google_event_id is None:
            await db.execute(
                update(CalendarEventLink)
                .where(CalendarEventLink.id == link.id)
                .values(
                    is_current=False,
                    sync_state="deleted",
                    desired_schedule_revision=version,
                    updated_at=now,
                    revision=CalendarEventLink.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
        else:
            await db.execute(
                update(CalendarEventLink)
                .where(CalendarEventLink.id == link.id)
                .values(
                    desired_schedule_revision=version,
                    sync_state="pending",
                    updated_at=now,
                    revision=CalendarEventLink.revision + 1,
                )
                .execution_options(synchronize_session="fetch")
            )
            db.add(
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=owner,
                    connection_id=link.connection_id,
                    event_link_id=link.id,
                    target_kind="session_event",
                    target_ref=str(link.session_id),
                    operation="delete",
                    target_schedule_revision=version,
                    expected_google_etag=link.google_etag,
                    payload_snapshot={
                        "id": link.google_event_id,
                        "google_calendar_id": link.google_calendar_id,
                    },
                    idempotency_key=(
                        f"sync_session:{link.session_id}:v{version}:delete:{link.id.hex[:8]}"
                    ),
                    job_state="queued",
                    attempt_count=0,
                )
            )

    await db.flush()
    state = await _calendar_state(db, owner, version)
    if state == "not_integrated":
        return "not_integrated"
    if state == "pending":
        return "pending"
    if state == "synced":
        return "synced"
    if state == "failed":
        return "failed"
    return "pending"


def _confirmed_payload(saved: dict[str, Any]) -> dict[str, Any]:
    data = json.loads(json.dumps(saved, default=str))
    task = data["task"]
    task.pop("planning_parts", None)
    task.setdefault("revision", 1)
    task.setdefault("availability", task.get("source_summary", {}).get("availability"))
    for step in task.get("steps", []):
        step.pop("planning_part_id", None)
    data.get("key_mapping", {}).pop("planning_parts", None)
    return data


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
            return ConfirmCandidateResult.model_validate(_confirmed_payload(saved))
        _, draft, candidate = await _read_candidate(db, user_id, candidate_id)
        if candidate.stale:
            raise _error("SOURCE_CHANGED", "Phương án cũ cần tạo lại theo hợp đồng mới.")
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
        source = await _sources(
            db,
            user_id,
            draft,
            candidate.planning_horizon,
            context_ref=candidate.source_revisions.calendar_context,
        )
        if (
            candidate.source_revisions != source.revisions
            or payload.expected_source_revisions != source.revisions
        ):
            raise _error(
                "SOURCE_CHANGED",
                "Nguồn lịch, giờ rảnh hoặc cấu hình đã đổi; cần tạo phương án mới.",
            )
        issues = _concrete_issues(candidate, source)
        if any(issue.code == "PAST_SESSION" for issue in issues):
            raise _error(
                "CANDIDATE_EXPIRED",
                "Giờ bắt đầu đã qua. Hãy tạo lại phương án và xem lịch mới trước khi xác nhận.",
                action="regenerate_candidate",
            )
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
        if tc is None:
            raise _error("PLAN_NOT_CONFIRMABLE", "Thiếu ràng buộc thời gian.")
        owner, task_id = draft_service.resolve_user_uuid(user_id), uuid4()
        next_version = source.revisions.schedule + 1
        extension_candidate_at = candidate.feasibility.extension_candidate_at
        if (
            extension_candidate_at is None
            and candidate.decomposition_state == "partial"
            and candidate.sessions
        ):
            extension_candidate_at = max(item.scheduled_end for item in candidate.sessions)
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
                availability_ref=availability_ref.model_dump(mode="json")
                if availability_ref
                else None,
                availability=draft.summary.availability.model_dump(mode="json")
                if draft.summary.availability
                else None,
                source_summary=draft.summary.model_dump(mode="json"),
                source_summary_revision=draft.summary_revision,
                source_planning_input_revision=draft.planning_input_revision,
                decomposition_state=candidate.decomposition_state,
                unexpanded_scope=candidate.unexpanded_scope,
                extension_candidate_at=extension_candidate_at,
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
                    **step.model_dump(exclude={"client_step_key"}),
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
        approved_at = datetime.now(UTC)
        for session in persisted_sessions:
            for offset in source.preferences.reminder_offsets:
                fire_at = session.scheduled_start - timedelta(seconds=offset)
                if fire_at < approved_at:
                    continue
                db.add(
                    Reminder(
                        user_id=owner,
                        session_id=session.id,
                        schedule_revision=next_version,
                        kind="pre_session",
                        fire_at=fire_at,
                        dedupe_key=f"session:{session.id}:version:{next_version}:reminder:{offset}",
                        delivery_state="scheduled",
                    )
                )
        step_titles = {step_ids[step.client_step_key]: step.title for step in candidate.steps}
        calendar_state = await _enqueue_calendar(
            db, owner, persisted_sessions, next_version, step_titles=step_titles
        )
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
        .where(
            TaskRow.id == _uuid(task_id),
            TaskRow.user_id == owner,
            TaskRow.status != "cancelled",
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy công việc.", 404, "reload")
    if row.source_draft_id is None or row.source_summary is None:
        raise _error("SOURCE_CHANGED", "Công việc thiếu snapshot nguồn theo hợp đồng hiện hành.")
    _, draft = await _read_draft(db, user_id, str(row.source_draft_id))
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
    from app.services.task_state_service import get_task_planning_states

    state = (await get_task_planning_states(db, owner, [row.id]))[row.id]
    steps = [
        Step.model_validate(
            dict(
                step_id=str(step.id),
                task_id=task_id,
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
            decomposition_state=state.decomposition_state,
            scheduling_state=state.scheduling_state,
            availability_ref=row.availability_ref,
            revision=row.revision,
            availability=row.availability,
            source_summary_revision=row.source_summary_revision,
            source_planning_input_revision=row.source_planning_input_revision,
            unexpanded_scope=row.unexpanded_scope,
            extension_candidate_at=row.extension_candidate_at,
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
    owner = draft_service.resolve_user_uuid(user_id)
    rows = (
        await db.scalars(
            select(TaskRow)
            .where(TaskRow.user_id == owner, TaskRow.status != "cancelled")
            .order_by(TaskRow.created_at, TaskRow.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    page, next_cursor = _page(list(rows), limit, cursor, "id")
    if not page:
        return TaskListData(items=[], next_cursor=None)

    for row in page:
        if row.source_draft_id is None or row.source_summary is None:
            raise _error(
                "SOURCE_CHANGED", "Công việc thiếu snapshot nguồn theo hợp đồng hiện hành."
            )
    task_ids = [item.id for item in page]
    draft_ids = [item.source_draft_id for item in page]

    # Batch 1: Drafts
    draft_rows = (
        (
            await db.scalars(
                select(DraftRow)
                .where(DraftRow.id.in_(draft_ids), DraftRow.user_id == owner)
                .execution_options(populate_existing=True)
            )
        ).all()
        if draft_ids
        else []
    )
    drafts_by_id = {
        row.id: draft_service._row_to_schema(row, fallback_draft_id=str(row.id))
        for row in draft_rows
    }

    # Batch 2: Steps
    all_steps = (
        await db.scalars(
            select(StepRow)
            .where(StepRow.task_id.in_(task_ids), StepRow.user_id == owner)
            .order_by(StepRow.order_index)
        )
    ).all()
    steps_by_task: dict[UUID, list[StepRow]] = {tid: [] for tid in task_ids}
    for s in all_steps:
        steps_by_task.setdefault(s.task_id, []).append(s)

    # Batch 3: Sessions
    all_sessions = (
        await db.scalars(
            select(SessionRow)
            .join(StepRow, StepRow.id == SessionRow.step_id)
            .where(StepRow.task_id.in_(task_ids), SessionRow.user_id == owner)
            .order_by(SessionRow.scheduled_start)
        )
    ).all()
    step_to_task = {s.id: s.task_id for s in all_steps}
    sessions_by_task: dict[UUID, list[SessionRow]] = {tid: [] for tid in task_ids}
    for sess in all_sessions:
        tid = step_to_task.get(sess.step_id)
        if tid is not None:
            sessions_by_task.setdefault(tid, []).append(sess)

    # Batch 4: Calendar links
    session_ids = [s.id for s in all_sessions]
    all_links = (
        (
            await db.scalars(
                select(CalendarEventLink)
                .where(
                    CalendarEventLink.user_id == owner,
                    CalendarEventLink.session_id.in_(session_ids),
                    CalendarEventLink.is_current.is_(True),
                )
                .order_by(CalendarEventLink.id)
            )
        ).all()
        if session_ids
        else []
    )
    link_ids = {item.session_id: str(item.id) for item in all_links}

    # Batch 5: Task planning states
    from app.services.task_state_service import get_task_planning_states

    task_states = await get_task_planning_states(db, owner, task_ids)

    # Construct Task DTOs in-memory without extra DB queries
    tasks: list[Task] = []
    for row in page:
        task_id = str(row.id)
        draft = drafts_by_id.get(row.source_draft_id)
        if draft is None:
            raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy bản nháp.", 404, "reload")
        step_rows = steps_by_task.get(row.id, [])
        session_rows = sessions_by_task.get(row.id, [])
        state = task_states[row.id]

        steps = [
            Step.model_validate(
                dict(
                    step_id=str(step.id),
                    task_id=task_id,
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
        tasks.append(
            Task.model_validate(
                dict(
                    task_id=task_id,
                    title=row.title,
                    description=row.description,
                    scope=row.scope,
                    extra_requirements=row.extra_requirements or [],
                    time_constraint=row.time_constraint,
                    importance=row.importance,
                    status=row.status,
                    source_draft_id=draft.draft_id,
                    decomposition_state=state.decomposition_state,
                    scheduling_state=state.scheduling_state,
                    availability_ref=row.availability_ref,
                    revision=row.revision,
                    availability=row.availability,
                    source_summary_revision=row.source_summary_revision,
                    source_planning_input_revision=row.source_planning_input_revision,
                    unexpanded_scope=row.unexpanded_scope,
                    extension_candidate_at=row.extension_candidate_at,
                    steps=steps,
                    sessions=[
                        _session_schema(item, link_ids.get(item.id)) for item in session_rows
                    ],
                    source_summary=row.source_summary,
                )
            )
        )

    return TaskListData(
        items=tasks,
        next_cursor=next_cursor,
    )


async def delete_task(
    *,
    user_id: str,
    task_id: str,
    db: AsyncSession,
    idempotency_key: str | None = None,
    expected_task_revision: int | None = None,
) -> TaskDeleteResult:
    """Hủy mềm công việc chính thức: đóng bước/phiên/nhắc nhở còn mở và xếp hàng xóa Google Calendar."""
    digest = (
        _hash("delete_task", task_id, {"expected_task_revision": expected_task_revision})
        if idempotency_key
        else ""
    )
    await db.rollback()
    async with db.begin():
        await _lock_user(db, user_id)
        if idempotency_key:
            saved = await _receipt(db, user_id, idempotency_key, digest)
            if saved is not None:
                return TaskDeleteResult.model_validate(saved)

        owner = draft_service.resolve_user_uuid(user_id)
        task_uuid = _uuid(task_id)
        row = await db.scalar(
            select(TaskRow)
            .where(TaskRow.id == task_uuid, TaskRow.user_id == owner)
            .execution_options(populate_existing=True)
        )
        if row is None or row.status == "cancelled":
            raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy công việc.", 404, "reload")
        if expected_task_revision is not None and row.revision != expected_task_revision:
            raise AppError(
                status_code=409,
                code="REVISION_CONFLICT",
                message="Công việc đã thay đổi.",
                current_revisions={"task": row.revision},
                next_action="reload_and_review",
            )

        step_rows = (
            await db.scalars(
                select(StepRow)
                .where(StepRow.task_id == row.id, StepRow.user_id == owner)
                .order_by(StepRow.order_index, StepRow.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        session_rows = (
            await db.scalars(
                select(SessionRow)
                .join(StepRow, StepRow.id == SessionRow.step_id)
                .where(StepRow.task_id == row.id, SessionRow.user_id == owner)
                .order_by(SessionRow.scheduled_start, SessionRow.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        all_session_uuids = [sess.id for sess in session_rows]

        now = datetime.now(UTC)
        live_run_id = await db.scalar(
            select(SessionRun.id)
            .where(
                SessionRun.user_id == owner,
                SessionRun.run_state.in_(("running", "paused")),
            )
            .limit(1)
        )
        awaiting_run_id = (
            await db.scalar(
                select(SessionRun.id)
                .where(
                    SessionRun.user_id == owner,
                    SessionRun.session_id.in_(all_session_uuids),
                    SessionRun.run_state == "awaiting_checkin",
                    or_(
                        SessionRun.checkin_timeout_at.is_(None),
                        SessionRun.checkin_timeout_at > now,
                    ),
                    ~exists(
                        select(CheckIn.id).where(
                            CheckIn.run_id == SessionRun.id,
                            CheckIn.kind == "final",
                            CheckIn.response_state.in_(("answered", "no_respond")),
                        )
                    ),
                )
                .limit(1)
            )
            if all_session_uuids
            else None
        )
        if live_run_id is not None or awaiting_run_id is not None:
            raise _error(
                "ACTIVE_RUN_EXISTS",
                "Đang có phiên tập trung hoạt động hoặc chờ check-in; vui lòng hoàn tất trước khi xóa công việc.",
                409,
                "finish_active_run",
            )

        current_version = await _schedule_revision(db, owner)
        next_version = current_version + 1

        row.status = "cancelled"
        row.unexpanded_scope = None
        row.revision += 1
        row.updated_at = now

        for step in step_rows:
            if step.status not in ("completed", "skipped", "cancelled"):
                step.status = "cancelled"
                step.revision += 1
                step.updated_at = now

        cancelled_session_ids: list[str] = []
        cancelled_session_uuids: list[UUID] = []
        for sess in session_rows:
            if sess.status not in ("completed", "skipped", "cancelled", "incomplete"):
                sess.status = "cancelled"
                sess.start_state = "superseded"
                sess.schedule_revision = next_version
                sess.revision += 1
                sess.updated_at = now
                cancelled_session_ids.append(str(sess.id))
                cancelled_session_uuids.append(sess.id)

        cancelled_reminders = 0
        if all_session_uuids:
            reminder_rows = (
                await db.scalars(
                    select(Reminder)
                    .where(
                        Reminder.user_id == owner,
                        Reminder.session_id.in_(all_session_uuids),
                        Reminder.delivery_state.in_(("scheduled", "snoozed")),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
            for rem in reminder_rows:
                rem.delivery_state = "cancelled"
                rem.updated_at = now
                rem.revision += 1
                cancelled_reminders += 1

        calendar_summary = await _enqueue_calendar_deletes_for_task(
            db, owner, cancelled_session_uuids, next_version, now
        )

        pref_row = await db.scalar(
            select(PreferenceRow)
            .where(PreferenceRow.user_id == owner)
            .execution_options(populate_existing=True)
        )
        linked_proposal_id = pref_row.pending_proposal_id if pref_row is not None else None
        pending_proposals = (
            await db.scalars(
                select(RescheduleProposal)
                .where(
                    RescheduleProposal.user_id == owner,
                    RescheduleProposal.status == "pending",
                    RescheduleProposal.target_task_id == row.id,
                )
                .execution_options(populate_existing=True)
            )
        ).all()
        for prop in pending_proposals:
            if linked_proposal_id is not None and prop.id == linked_proposal_id:
                continue
            prop.status = "superseded"
            prop.revision += 1
            prop.updated_at = now
            linked_setting_reqs = (
                await db.scalars(
                    select(SettingChangeRequest)
                    .where(
                        SettingChangeRequest.user_id == owner,
                        SettingChangeRequest.pending_proposal_id == prop.id,
                        SettingChangeRequest.application_state.in_(
                            ("pending_review", "pending_proposal")
                        ),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
            for s_req in linked_setting_reqs:
                s_req.application_state = "cancelled"
                s_req.requested_revision = None
                s_req.requested_availability_revision = None
                s_req.updated_at = now
                s_req.revision += 1

        alarm_state: Literal["pending", "not_required"] = (
            "pending" if (cancelled_session_ids or cancelled_reminders > 0) else "not_required"
        )
        db.add(
            VersionRow(
                user_id=owner,
                version_number=next_version,
                base_version=current_version if current_version > 0 else None,
                approved_at=now,
                affected_task_ids=[str(row.id)],
                min_inter_session_gap_seconds=900,
                alarm_sync_state=alarm_state,
            )
        )
        await db.flush()

        result = TaskDeleteResult(
            task_id=str(row.id),
            deleted=True,
            status="cancelled",
            task_revision=row.revision,
            schedule_revision=next_version,
            cancelled_session_ids=cancelled_session_ids,
            alarm_sync_state=alarm_state,
            calendar_sync_summary=calendar_summary,
        )
        if idempotency_key:
            _record(
                db,
                user_id,
                idempotency_key,
                digest,
                "delete_task",
                result,
                task_id=str(row.id),
            )
    return result


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
        return "not_integrated"
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
    from app.services import run_service

    await run_service.reconcile_expired_checkins(db, user_id, commit_if_read=True)
    rows = (
        await db.scalars(
            select(SessionRow)
            .join(StepRow, StepRow.id == SessionRow.step_id)
            .join(TaskRow, TaskRow.id == StepRow.task_id)
            .where(
                SessionRow.user_id == owner,
                SessionRow.scheduled_start < to_at,
                SessionRow.scheduled_end > from_at,
                SessionRow.status != "cancelled",
                or_(
                    SessionRow.status != "scheduled",
                    and_(
                        StepRow.status.notin_(("completed", "skipped", "cancelled")),
                        TaskRow.status.notin_(("completed", "skipped", "cancelled")),
                    ),
                ),
            )
            .order_by(SessionRow.scheduled_start, SessionRow.id)
            .execution_options(populate_existing=True)
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
