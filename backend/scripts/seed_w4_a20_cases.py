"""Seed bộ mẫu kiểm thử kịch bản A20 (UF7) và reset sạch dữ liệu A20 trên DB QA."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import make_engine, session_factory  # noqa: E402
from app.models import (  # noqa: E402
    ActualCorrection,
    AdaptationSuggestion,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
    CheckIn,
    DeviceState,
    PlanCandidate,
    PlanningContext,
    PlanningDraft,
    Reminder,
    RescheduleProposal,
    RunEvent,
    ScheduleVersion,
    Session,
    SessionRun,
    SettingChangeRequest,
    Step,
    Task,
    Thought,
    User,
    UserPreference,
)
from app.schemas.availability import DraftAvailability, WeeklyRule  # noqa: E402
from app.schemas.common import TimeConstraint  # noqa: E402
from app.schemas.draft import TaskSummary  # noqa: E402
from app.services.settings_service import (  # noqa: E402
    ANNOYANCE_COOLDOWN_SECONDS,
    DEFAULT_BUFFER_MULTIPLIER,
    DEFAULT_MICRO_TASK_SECONDS,
    DEFAULT_REMINDER_OFFSETS,
    DEFAULT_REMINDER_STYLE,
    DEFAULT_REST_INTERVAL_SECONDS,
    DEFAULT_WORK_INTERVAL_SECONDS,
    SUGGESTION_COOLDOWN_SECONDS,
    collect_buffer_step_samples,
)

SCENARIOS: tuple[str, ...] = (
    "below_min_6",
    "exact_delta_020",
    "trigger_window_10",
    "accept_and_conflict",
    "cooldown_7d",
    "cooldown_14d",
)
A20_TITLE_PREFIX = "[W4 A20]"
A20_DRAFT_PREFIX = "w4-a20:"
A20_DEVICE_ID = "w4-a20-qa"
TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC).replace(microsecond=0)
    if now.tzinfo is None or now.utcoffset() is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _a20_uuid(owner: UUID, key: str) -> UUID:
    return uuid5(owner, f"{A20_DRAFT_PREFIX}{key}")


def _known_a20_suggestion_ids(owner: UUID) -> set[UUID]:
    return {
        _a20_uuid(owner, f"{scenario}:rejected-suggestion")
        for scenario in ("cooldown_7d", "cooldown_14d")
    }


def _default_24h_availability() -> DraftAvailability:
    return DraftAvailability(
        timezone=TZ_VN.key,
        weekly_rules=[
            WeeklyRule(weekday=wd, start_local=time(0, 0), end_local=time(23, 59))
            for wd in range(1, 8)
        ],
    )


async def _ensure_preference(
    db: AsyncSession,
    owner: UUID,
    *,
    now: datetime,
    create_if_missing: bool,
) -> UserPreference | None:
    pref = await db.scalar(
        select(UserPreference).where(UserPreference.user_id == owner).with_for_update()
    )
    if pref is None and create_if_missing:
        pref = UserPreference(
            user_id=owner,
            work_interval_seconds=DEFAULT_WORK_INTERVAL_SECONDS,
            rest_interval_seconds=DEFAULT_REST_INTERVAL_SECONDS,
            micro_task_seconds=DEFAULT_MICRO_TASK_SECONDS,
            reminder_offsets=list(DEFAULT_REMINDER_OFFSETS),
            effective_revision=1,
            buffer_multiplier=DEFAULT_BUFFER_MULTIPLIER,
            reminder_style=DEFAULT_REMINDER_STYLE,
            created_at=now,
            updated_at=now,
        )
        db.add(pref)
        await db.flush()
    return pref


async def _cleanup_a20_for_user(db: AsyncSession, owner: UUID) -> dict[str, int]:
    """Xóa sạch toàn bộ bản ghi A20 (kể cả dữ liệu sinh động qua API), giữ nguyên nền W4-03A."""
    task_rows = (
        await db.execute(
            select(Task.id, Task.title, Task.source_draft_id).where(Task.user_id == owner)
        )
    ).all()
    a20_task_ids: set[UUID] = set()
    a20_draft_ids: set[UUID] = set()
    for task_id, title, source_draft_id in task_rows:
        if isinstance(title, str) and title.startswith(A20_TITLE_PREFIX):
            a20_task_ids.add(task_id)
            if source_draft_id is not None:
                a20_draft_ids.add(source_draft_id)

    draft_rows = (
        await db.execute(
            select(PlanningDraft.id, PlanningDraft.client_draft_id).where(
                PlanningDraft.user_id == owner
            )
        )
    ).all()
    for draft_id, client_draft_id in draft_rows:
        if isinstance(client_draft_id, str) and client_draft_id.startswith(A20_DRAFT_PREFIX):
            a20_draft_ids.add(draft_id)

    a20_step_ids: set[UUID] = set()
    if a20_task_ids:
        a20_step_ids = set(
            (
                await db.scalars(
                    select(Step.id).where(Step.user_id == owner, Step.task_id.in_(a20_task_ids))
                )
            ).all()
        )

    a20_session_ids: set[UUID] = set()
    if a20_step_ids:
        a20_session_ids = set(
            (
                await db.scalars(
                    select(Session.id).where(
                        Session.user_id == owner, Session.step_id.in_(a20_step_ids)
                    )
                )
            ).all()
        )

    a20_run_ids: set[UUID] = set()
    a20_event_link_ids: set[UUID] = set()
    if a20_session_ids:
        a20_run_ids = set(
            (
                await db.scalars(
                    select(SessionRun.id).where(
                        SessionRun.user_id == owner, SessionRun.session_id.in_(a20_session_ids)
                    )
                )
            ).all()
        )
        a20_event_link_ids = set(
            (
                await db.scalars(
                    select(CalendarEventLink.id).where(
                        CalendarEventLink.user_id == owner,
                        CalendarEventLink.session_id.in_(a20_session_ids),
                    )
                )
            ).all()
        )

    a20_thought_ids: set[UUID] = set()
    if a20_run_ids:
        a20_thought_ids = set(
            (
                await db.scalars(
                    select(Thought.id).where(
                        Thought.user_id == owner, Thought.source_run_id.in_(a20_run_ids)
                    )
                )
            ).all()
        )

    known_sugg_ids = _known_a20_suggestion_ids(owner)
    a20_step_id_strs = {str(sid) for sid in a20_step_ids}
    sugg_rows = (
        await db.execute(
            select(
                AdaptationSuggestion.id,
                AdaptationSuggestion.reason,
                AdaptationSuggestion.sample_refs,
            ).where(AdaptationSuggestion.user_id == owner)
        )
    ).all()
    a20_suggestion_ids: set[UUID] = set()
    for sugg_id, reason, sample_refs in sugg_rows:
        ref_strs = {str(ref) for ref in (sample_refs or [])}
        if (
            sugg_id in known_sugg_ids
            or (isinstance(reason, str) and reason.startswith(A20_TITLE_PREFIX))
            or bool(ref_strs & a20_step_id_strs)
        ):
            a20_suggestion_ids.add(sugg_id)

    a20_task_id_strs = {str(tid) for tid in a20_task_ids}
    a20_draft_id_strs = {str(did) for did in a20_draft_ids}
    a20_sugg_id_strs = {str(sid) for sid in a20_suggestion_ids}

    change_rows = (
        await db.execute(
            select(
                SettingChangeRequest.id,
                SettingChangeRequest.requested_changes,
                SettingChangeRequest.pending_proposal_id,
            ).where(SettingChangeRequest.user_id == owner)
        )
    ).all()
    proposal_rows = (
        await db.execute(
            select(
                RescheduleProposal.id,
                RescheduleProposal.trigger_reason,
                RescheduleProposal.target_task_id,
                RescheduleProposal.allowed_task_ids,
                RescheduleProposal.affected_task_ids,
            ).where(RescheduleProposal.user_id == owner)
        )
    ).all()

    a20_setting_change_ids: set[UUID] = set()
    a20_proposal_ids: set[UUID] = set()
    for change_id, requested_changes, pending_proposal_id in change_rows:
        req = requested_changes if isinstance(requested_changes, dict) else {}
        if (
            str(req.get("suggestion_id") or "") in a20_sugg_id_strs
            or str(req.get("target_task_id") or "") in a20_task_id_strs
            or str(req.get("target_draft_id") or "") in a20_draft_id_strs
        ):
            a20_setting_change_ids.add(change_id)
            if pending_proposal_id is not None:
                a20_proposal_ids.add(pending_proposal_id)

    for prop_id, trigger_reason, target_task_id, allowed_task_ids, affected_task_ids in proposal_rows:
        allowed_strs = {str(tid) for tid in (allowed_task_ids or [])}
        affected_strs = {str(tid) for tid in (affected_task_ids or [])}
        if (
            prop_id in a20_proposal_ids
            or (target_task_id is not None and target_task_id in a20_task_ids)
            or (
                trigger_reason == "settings_change"
                and (
                    bool(affected_strs & a20_task_id_strs)
                    or (bool(allowed_strs) and allowed_strs <= a20_task_id_strs)
                )
            )
        ):
            a20_proposal_ids.add(prop_id)

    for change_id, _requested_changes, pending_proposal_id in change_rows:
        if pending_proposal_id is not None and pending_proposal_id in a20_proposal_ids:
            a20_setting_change_ids.add(change_id)

    a20_schedule_version_ids: set[UUID] = set()
    if a20_task_id_strs:
        sv_rows = (
            await db.execute(
                select(ScheduleVersion.id, ScheduleVersion.affected_task_ids).where(
                    ScheduleVersion.user_id == owner
                )
            )
        ).all()
        for sv_id, affected_task_ids in sv_rows:
            affected_strs = {str(tid) for tid in (affected_task_ids or [])}
            if affected_strs and affected_strs <= a20_task_id_strs:
                a20_schedule_version_ids.add(sv_id)

    # Bước 1: Gỡ toàn bộ các FK nullable và tự tham chiếu để tránh vướng ondelete=RESTRICT.
    pref = await db.scalar(
        select(UserPreference).where(UserPreference.user_id == owner).with_for_update()
    )
    if pref is not None and pref.pending_proposal_id in a20_proposal_ids:
        pref.pending_proposal_id = None
        pref.requested_revision = None
        pref.requested_changes = None

    if a20_run_ids:
        await db.execute(
            update(DeviceState)
            .where(DeviceState.user_id == owner, DeviceState.active_run_id.in_(a20_run_ids))
            .values(active_run_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(DeviceState)
            .where(DeviceState.user_id == owner, DeviceState.dnd_owner_run_id.in_(a20_run_ids))
            .values(dnd_owner_run_id=None)
            .execution_options(synchronize_session=False)
        )
    if a20_thought_ids:
        await db.execute(
            update(PlanningDraft)
            .where(
                PlanningDraft.user_id == owner,
                PlanningDraft.source_thought_id.in_(a20_thought_ids),
            )
            .values(source_thought_id=None)
            .execution_options(synchronize_session=False)
        )
    if a20_suggestion_ids:
        await db.execute(
            update(AdaptationSuggestion)
            .where(
                AdaptationSuggestion.user_id == owner,
                or_(
                    AdaptationSuggestion.id.in_(a20_suggestion_ids),
                    AdaptationSuggestion.supersedes_suggestion_id.in_(a20_suggestion_ids),
                ),
            )
            .values(supersedes_suggestion_id=None)
            .execution_options(synchronize_session=False)
        )
    if a20_proposal_ids:
        await db.execute(
            update(RescheduleProposal)
            .where(
                RescheduleProposal.user_id == owner,
                or_(
                    RescheduleProposal.id.in_(a20_proposal_ids),
                    RescheduleProposal.supersedes_proposal_id.in_(a20_proposal_ids),
                ),
            )
            .values(supersedes_proposal_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(CalendarPreview)
            .where(
                CalendarPreview.user_id == owner,
                CalendarPreview.required_proposal_id.in_(a20_proposal_ids),
            )
            .values(required_proposal_id=None)
            .execution_options(synchronize_session=False)
        )
    await db.flush()

    # Bước 2: Xóa từ bảng lá lên bảng gốc theo thứ tự khóa ngoại RESTRICT.
    if a20_run_ids:
        for run_child in (CheckIn, RunEvent, ActualCorrection):
            await db.execute(
                delete(run_child)
                .where(run_child.user_id == owner, run_child.run_id.in_(a20_run_ids))
                .execution_options(synchronize_session=False)
            )
    if a20_thought_ids:
        await db.execute(
            delete(Thought)
            .where(Thought.user_id == owner, Thought.id.in_(a20_thought_ids))
            .execution_options(synchronize_session=False)
        )
    if a20_run_ids:
        await db.execute(
            delete(SessionRun)
            .where(SessionRun.user_id == owner, SessionRun.id.in_(a20_run_ids))
            .execution_options(synchronize_session=False)
        )
    if a20_session_ids:
        await db.execute(
            delete(Reminder)
            .where(Reminder.user_id == owner, Reminder.session_id.in_(a20_session_ids))
            .execution_options(synchronize_session=False)
        )
        session_id_strs = {str(sid) for sid in a20_session_ids}
        job_cond = (
            or_(
                CalendarSyncJob.event_link_id.in_(a20_event_link_ids),
                CalendarSyncJob.target_kind.in_(("session", "session_event"))
                & CalendarSyncJob.target_ref.in_(session_id_strs),
            )
            if a20_event_link_ids
            else CalendarSyncJob.target_kind.in_(("session", "session_event"))
            & CalendarSyncJob.target_ref.in_(session_id_strs)
        )
        await db.execute(
            delete(CalendarSyncJob)
            .where(CalendarSyncJob.user_id == owner, job_cond)
            .execution_options(synchronize_session=False)
        )
    if a20_event_link_ids:
        await db.execute(
            delete(CalendarEventLink)
            .where(
                CalendarEventLink.user_id == owner,
                CalendarEventLink.id.in_(a20_event_link_ids),
            )
            .execution_options(synchronize_session=False)
        )
    if a20_session_ids:
        await db.execute(
            delete(Session)
            .where(Session.user_id == owner, Session.id.in_(a20_session_ids))
            .execution_options(synchronize_session=False)
        )
    if a20_step_ids:
        await db.execute(
            delete(Step)
            .where(Step.user_id == owner, Step.id.in_(a20_step_ids))
            .execution_options(synchronize_session=False)
        )
    if a20_setting_change_ids:
        await db.execute(
            delete(SettingChangeRequest)
            .where(
                SettingChangeRequest.user_id == owner,
                SettingChangeRequest.id.in_(a20_setting_change_ids),
            )
            .execution_options(synchronize_session=False)
        )
    if a20_proposal_ids:
        await db.execute(
            delete(RescheduleProposal)
            .where(
                RescheduleProposal.user_id == owner,
                RescheduleProposal.id.in_(a20_proposal_ids),
            )
            .execution_options(synchronize_session=False)
        )
    if a20_suggestion_ids:
        await db.execute(
            delete(AdaptationSuggestion)
            .where(
                AdaptationSuggestion.user_id == owner,
                AdaptationSuggestion.id.in_(a20_suggestion_ids),
            )
            .execution_options(synchronize_session=False)
        )
    if a20_schedule_version_ids:
        await db.execute(
            delete(ScheduleVersion)
            .where(
                ScheduleVersion.user_id == owner,
                ScheduleVersion.id.in_(a20_schedule_version_ids),
            )
            .execution_options(synchronize_session=False)
        )
    if a20_task_ids:
        await db.execute(
            delete(Task)
            .where(Task.user_id == owner, Task.id.in_(a20_task_ids))
            .execution_options(synchronize_session=False)
        )
    if a20_draft_ids:
        await db.execute(
            delete(PlanCandidate)
            .where(PlanCandidate.user_id == owner, PlanCandidate.draft_id.in_(a20_draft_ids))
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            delete(PlanningContext)
            .where(
                PlanningContext.user_id == owner,
                PlanningContext.source_draft_id.in_(a20_draft_ids),
            )
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            delete(PlanningDraft)
            .where(PlanningDraft.user_id == owner, PlanningDraft.id.in_(a20_draft_ids))
            .execution_options(synchronize_session=False)
        )
    await db.flush()

    return {
        "tasks": len(a20_task_ids),
        "steps": len(a20_step_ids),
        "sessions": len(a20_session_ids),
        "runs": len(a20_run_ids),
        "suggestions": len(a20_suggestion_ids),
        "setting_changes": len(a20_setting_change_ids),
        "proposals": len(a20_proposal_ids),
        "drafts": len(a20_draft_ids),
    }


async def _add_history_task(
    db: AsyncSession,
    *,
    owner: UUID,
    scenario: str,
    title: str,
    description: str,
    scope: str,
    start_at: datetime,
    deadline_at: datetime,
    now: datetime,
) -> UUID:
    draft_id = _a20_uuid(owner, f"{scenario}:history-draft")
    task_id = _a20_uuid(owner, f"{scenario}:history-task")
    availability = _default_24h_availability()
    constraint = TimeConstraint(
        kind="deadline",
        start_at=start_at,
        deadline_at=deadline_at,
        input_timezone=TZ_VN.key,
    )
    summary = TaskSummary(
        title=title,
        description=description,
        scope=scope,
        time_constraint=constraint,
        availability=availability,
        can_plan=True,
    )
    snapshot = summary.model_dump(mode="json")
    db.add(
        PlanningDraft(
            id=draft_id,
            user_id=owner,
            client_draft_id=f"{A20_DRAFT_PREFIX}{scenario}:history-draft",
            stage="confirmed",
            input_source="text",
            raw_input=title,
            summary={**snapshot, "_api_meta": {"confirmed_task_id": str(task_id)}},
            summary_confirmed_revision=1,
            created_at=start_at,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        Task(
            id=task_id,
            user_id=owner,
            source_draft_id=draft_id,
            title=title,
            description=description,
            scope=scope,
            time_constraint=constraint.model_dump(mode="json"),
            availability=snapshot["availability"],
            source_summary=snapshot,
            source_summary_revision=1,
            source_planning_input_revision=1,
            decomposition_state="complete",
            status="completed",
            created_at=start_at,
            updated_at=now,
        )
    )
    await db.flush()
    return task_id


async def _add_step_sample(
    db: AsyncSession,
    *,
    owner: UUID,
    task_id: UUID,
    key: str,
    title: str,
    order_index: int,
    estimated_base_seconds: int,
    actual_work_seconds: int,
    ended_at: datetime,
    estimate_source: str = "ai",
    session_status: str = "completed",
    run_outcome: str = "completed",
    checkin_response_state: str = "answered",
    task_completed: bool = False,
) -> UUID:
    step_id = _a20_uuid(owner, f"step:{key}")
    session_id = _a20_uuid(owner, f"session:{key}")
    run_id = _a20_uuid(owner, f"run:{key}")
    checkin_id = _a20_uuid(owner, f"checkin:{key}")

    run_span = max(actual_work_seconds, 600)
    started_at = ended_at - timedelta(seconds=run_span)
    responded_at = ended_at + timedelta(seconds=10)
    sched_work = estimated_base_seconds

    db.add(
        Step(
            id=step_id,
            user_id=owner,
            task_id=task_id,
            title=title,
            expected_output=f"Hoàn thành {title}",
            order_index=order_index,
            estimated_base_seconds=estimated_base_seconds,
            remaining_base_seconds=0,
            buffer_multiplier=DEFAULT_BUFFER_MULTIPLIER,
            estimate_source=estimate_source,
            status="completed",
            created_at=started_at,
            updated_at=ended_at,
        )
    )
    await db.flush()
    db.add(
        Session(
            id=session_id,
            user_id=owner,
            step_id=step_id,
            scheduled_start=started_at,
            scheduled_end=started_at + timedelta(seconds=sched_work),
            start_decision_due_at=started_at + timedelta(minutes=15),
            micro_task_text=f"Mở tài liệu cho {title}",
            micro_task_seconds=DEFAULT_MICRO_TASK_SECONDS,
            work_seconds=sched_work,
            rest_seconds=0,
            duration_seconds=sched_work,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": sched_work}],
            schedule_revision=1,
            start_state="started",
            status=session_status,
            created_at=started_at,
            updated_at=ended_at,
        )
    )
    await db.flush()
    db.add(
        SessionRun(
            id=run_id,
            user_id=owner,
            session_id=session_id,
            device_id=A20_DEVICE_ID,
            started_at=started_at,
            ended_at=ended_at,
            run_state="ended",
            active_phase="ended",
            work_elapsed_seconds=actual_work_seconds,
            actual_work_seconds=actual_work_seconds,
            actual_source="timer_proxy",
            outcome=run_outcome,
            settings_snapshot={"buffer_multiplier": DEFAULT_BUFFER_MULTIPLIER},
            created_at=started_at,
            updated_at=ended_at,
        )
    )
    await db.flush()
    db.add(
        CheckIn(
            id=checkin_id,
            user_id=owner,
            run_id=run_id,
            kind="final",
            response_state=checkin_response_state,
            session_outcome=run_outcome,
            step_completed=(run_outcome == "completed"),
            task_completed=task_completed,
            asked_at=ended_at,
            responded_at=responded_at if checkin_response_state == "answered" else None,
            response_revision=1 if checkin_response_state == "answered" else 0,
            created_at=ended_at,
            updated_at=responded_at,
        )
    )
    await db.flush()
    return step_id


async def _add_future_scheduled_task(
    db: AsyncSession,
    *,
    owner: UUID,
    now: datetime,
) -> dict[str, str]:
    draft_id = _a20_uuid(owner, "accept_and_conflict:future-draft")
    task_id = _a20_uuid(owner, "accept_and_conflict:future-task")
    step_id = _a20_uuid(owner, "accept_and_conflict:future-step")
    session_id = _a20_uuid(owner, "accept_and_conflict:future-session")

    scheduled_start = now + timedelta(days=1)
    base_seconds = 1250
    work_seconds = 1500
    scheduled_end = scheduled_start + timedelta(seconds=work_seconds)
    title = f"{A20_TITLE_PREFIX} Phiên tương lai kiểm thử xung đột UF6"
    availability = _default_24h_availability()
    constraint = TimeConstraint(
        kind="deadline",
        start_at=now,
        deadline_at=now + timedelta(days=7),
        input_timezone=TZ_VN.key,
    )
    summary = TaskSummary(
        title=title,
        description="Công việc mẫu có phiên tương lai để kiểm thử nhánh accept/edit sang UF6.",
        scope="1 bước chuẩn bị tài liệu",
        time_constraint=constraint,
        availability=availability,
        can_plan=True,
    )
    snapshot = summary.model_dump(mode="json")

    db.add(
        PlanningDraft(
            id=draft_id,
            user_id=owner,
            client_draft_id=f"{A20_DRAFT_PREFIX}accept_and_conflict:future-draft",
            stage="confirmed",
            input_source="text",
            raw_input=title,
            summary={**snapshot, "_api_meta": {"confirmed_task_id": str(task_id)}},
            summary_confirmed_revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        Task(
            id=task_id,
            user_id=owner,
            source_draft_id=draft_id,
            title=title,
            description=summary.description,
            scope=summary.scope,
            time_constraint=constraint.model_dump(mode="json"),
            availability=snapshot["availability"],
            source_summary=snapshot,
            source_summary_revision=1,
            source_planning_input_revision=1,
            decomposition_state="complete",
            status="pending",
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        Step(
            id=step_id,
            user_id=owner,
            task_id=task_id,
            title="Hoàn thiện phần trình bày",
            expected_output="Tài liệu hoàn chỉnh",
            order_index=0,
            estimated_base_seconds=base_seconds,
            remaining_base_seconds=base_seconds,
            buffer_multiplier=DEFAULT_BUFFER_MULTIPLIER,
            estimate_source="ai",
            status="pending",
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        Session(
            id=session_id,
            user_id=owner,
            step_id=step_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            start_decision_due_at=scheduled_start + timedelta(minutes=15),
            micro_task_text="Mở tài liệu và đặt tiêu đề mục",
            micro_task_seconds=DEFAULT_MICRO_TASK_SECONDS,
            work_seconds=work_seconds,
            rest_seconds=0,
            duration_seconds=work_seconds,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": work_seconds}],
            schedule_revision=1,
            start_state="waiting",
            status="scheduled",
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    return {
        "future_task_id": str(task_id),
        "future_step_id": str(step_id),
        "future_session_id": str(session_id),
    }


async def _load_active_google_user(db: AsyncSession, email: str) -> User:
    normalized = email.strip()
    if not normalized:
        raise ValueError("Cần cung cấp email Google hợp lệ.")
    user = await db.scalar(
        select(User)
        .where(
            User.email == normalized,
            User.is_active.is_(True),
            User.google_sub.is_not(None),
            User.google_sub != "",
        )
        .with_for_update()
    )
    if user is None:
        raise ValueError(
            "Không tìm thấy tài khoản Google đang hoạt động với email này trên DB đích."
        )
    return user


async def reset_a20_data(database_url: str, email: str) -> dict[str, Any]:
    """Xóa toàn bộ dữ liệu mẫu/phát sinh của A20 cho user và đưa buffer/cooldown về baseline."""
    engine = make_engine(database_url)
    try:
        async with session_factory(engine).begin() as db:
            user = await _load_active_google_user(db, email)
            owner = user.id
            removed = await _cleanup_a20_for_user(db, owner)
            pref = await _ensure_preference(db, owner, now=_utc_now(), create_if_missing=False)
            if pref is not None:
                pref.buffer_multiplier = DEFAULT_BUFFER_MULTIPLIER
                pref.suggestion_cooldown_until = None
                pref.updated_at = _utc_now()
                await db.flush()
        return {
            "dialect": engine.dialect.name,
            "user_id": str(owner),
            "removed": removed,
        }
    finally:
        await engine.dispose()


async def seed_a20_scenario(
    database_url: str,
    email: str,
    scenario: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reset sạch A20 của user rồi seed đúng kịch bản A20 yêu cầu trong 1 transaction atomic."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Kịch bản A20 không hợp lệ: {scenario}")

    calc_now = _utc_now(now)
    engine = make_engine(database_url)
    try:
        async with session_factory(engine).begin() as db:
            user = await _load_active_google_user(db, email)
            owner = user.id
            await _cleanup_a20_for_user(db, owner)

            pref = await _ensure_preference(db, owner, now=calc_now, create_if_missing=True)
            assert pref is not None
            pref.buffer_multiplier = DEFAULT_BUFFER_MULTIPLIER
            pref.suggestion_cooldown_until = None
            pref.updated_at = calc_now

            clean_step_ids: list[UUID] = []
            dirty_step_ids: list[UUID] = []
            future_ids: dict[str, str] | None = None

            if scenario == "below_min_6":
                existing = await collect_buffer_step_samples(db, owner=owner)
                existing_clean = sum(1 for item in existing if item.get("scope_modified") is False)
                needed_clean = max(0, 6 - existing_clean)
                task_id = await _add_history_task(
                    db,
                    owner=owner,
                    scenario=scenario,
                    title=f"{A20_TITLE_PREFIX} Kịch bản below_min_6 (dưới 7 mẫu sạch + 4 mẫu bẩn)",
                    description="Kiểm thử ngưỡng tối thiểu 7 mẫu sạch và bộ lọc 4 mẫu bẩn A20.",
                    scope=f"{needed_clean} bước sạch + 4 bước bẩn",
                    start_at=calc_now - timedelta(hours=24),
                    deadline_at=calc_now,
                    now=calc_now,
                )
                for idx in range(needed_clean):
                    ended = calc_now - timedelta(hours=needed_clean - idx)
                    sid = await _add_step_sample(
                        db,
                        owner=owner,
                        task_id=task_id,
                        key=f"{scenario}:clean:{idx}",
                        title=f"Bước sạch {idx + 1}",
                        order_index=idx,
                        estimated_base_seconds=1000,
                        actual_work_seconds=1600,
                        ended_at=ended,
                        task_completed=(idx == needed_clean - 1),
                    )
                    clean_step_ids.append(sid)

                dirty_specs = (
                    (
                        "skipped",
                        "Mẫu bẩn bỏ qua (skipped)",
                        "ai",
                        "skipped",
                        "skipped",
                        "answered",
                        1600,
                    ),
                    (
                        "no_response",
                        "Mẫu bẩn không phản hồi (no_response)",
                        "ai",
                        "completed",
                        "no_response",
                        "no_respond",
                        1600,
                    ),
                    (
                        "scope_modified",
                        "Mẫu bẩn đổi phạm vi (scope_modified)",
                        "scope_modified",
                        "completed",
                        "completed",
                        "answered",
                        1600,
                    ),
                    (
                        "actual_zero",
                        "Mẫu bẩn thời gian thực bằng 0",
                        "ai",
                        "completed",
                        "completed",
                        "answered",
                        0,
                    ),
                )
                for offset, (tag, label, est_src, sess_st, run_out, ci_state, actual_sec) in enumerate(
                    dirty_specs
                ):
                    sid = await _add_step_sample(
                        db,
                        owner=owner,
                        task_id=task_id,
                        key=f"{scenario}:dirty:{tag}",
                        title=label,
                        order_index=100 + offset,
                        estimated_base_seconds=1000,
                        actual_work_seconds=actual_sec,
                        ended_at=calc_now - timedelta(hours=20 - offset),
                        estimate_source=est_src,
                        session_status=sess_st,
                        run_outcome=run_out,
                        checkin_response_state=ci_state,
                    )
                    dirty_step_ids.append(sid)

            elif scenario == "exact_delta_020":
                task_id = await _add_history_task(
                    db,
                    owner=owner,
                    scenario=scenario,
                    title=f"{A20_TITLE_PREFIX} Kịch bản exact_delta_020 (độ lệch đúng 0.20)",
                    description="Kiểm thử ranh giới |1.40 - 1.20| = 0.20 không vượt ngưỡng > 0.20.",
                    scope="10 bước hoàn thành (ratio 1.40)",
                    start_at=calc_now - timedelta(hours=12),
                    deadline_at=calc_now,
                    now=calc_now,
                )
                for idx in range(10):
                    ended = calc_now - timedelta(hours=10 - idx)
                    sid = await _add_step_sample(
                        db,
                        owner=owner,
                        task_id=task_id,
                        key=f"{scenario}:clean:{idx}",
                        title=f"Bước sạch delta 0.20 #{idx + 1}",
                        order_index=idx,
                        estimated_base_seconds=1000,
                        actual_work_seconds=1400,
                        ended_at=ended,
                        task_completed=(idx == 9),
                    )
                    clean_step_ids.append(sid)

            elif scenario == "trigger_window_10":
                task_id = await _add_history_task(
                    db,
                    owner=owner,
                    scenario=scenario,
                    title=f"{A20_TITLE_PREFIX} Kịch bản trigger_window_10 (cửa sổ trượt 10/12)",
                    description="Kiểm thử loại 2 mẫu ngoại lệ cũ (ratio 2.50), chỉ giữ 10 mẫu mới (1.55).",
                    scope="12 bước hoàn thành",
                    start_at=calc_now - timedelta(hours=16),
                    deadline_at=calc_now,
                    now=calc_now,
                )
                specs = [
                    (14, 2500, "outlier-old-1"),
                    (13, 2500, "outlier-old-2"),
                    *((10 - idx, 1550, f"window-{idx}") for idx in range(10)),
                ]
                for idx, (hours_ago, actual_sec, tag) in enumerate(specs):
                    ended = calc_now - timedelta(hours=hours_ago)
                    sid = await _add_step_sample(
                        db,
                        owner=owner,
                        task_id=task_id,
                        key=f"{scenario}:{tag}",
                        title=f"Bước cửa sổ {idx + 1}",
                        order_index=idx,
                        estimated_base_seconds=1000,
                        actual_work_seconds=actual_sec,
                        ended_at=ended,
                        task_completed=(idx == len(specs) - 1),
                    )
                    clean_step_ids.append(sid)

            elif scenario in ("accept_and_conflict", "cooldown_7d", "cooldown_14d"):
                task_id = await _add_history_task(
                    db,
                    owner=owner,
                    scenario=scenario,
                    title=f"{A20_TITLE_PREFIX} Kịch bản {scenario}",
                    description=f"Dữ liệu lịch sử 10 bước sạch (ratio 1.60) cho kịch bản {scenario}.",
                    scope="10 bước hoàn thành (ratio 1.60)",
                    start_at=calc_now - timedelta(hours=12),
                    deadline_at=calc_now,
                    now=calc_now,
                )
                for idx in range(10):
                    ended = calc_now - timedelta(hours=10 - idx)
                    sid = await _add_step_sample(
                        db,
                        owner=owner,
                        task_id=task_id,
                        key=f"{scenario}:clean:{idx}",
                        title=f"Bước sạch {idx + 1}",
                        order_index=idx,
                        estimated_base_seconds=1000,
                        actual_work_seconds=1600,
                        ended_at=ended,
                        task_completed=(idx == 9),
                    )
                    clean_step_ids.append(sid)

                if scenario == "accept_and_conflict":
                    future_ids = await _add_future_scheduled_task(db, owner=owner, now=calc_now)
                elif scenario == "cooldown_7d":
                    pref.suggestion_cooldown_until = calc_now + timedelta(
                        seconds=SUGGESTION_COOLDOWN_SECONDS
                    )
                    db.add(
                        AdaptationSuggestion(
                            id=_a20_uuid(owner, f"{scenario}:rejected-suggestion"),
                            user_id=owner,
                            revision=2,
                            kind="buffer_multiplier",
                            sample_count=10,
                            current_value=DEFAULT_BUFFER_MULTIPLIER,
                            suggested_value=1.6,
                            reason=f"{A20_TITLE_PREFIX} Gợi ý đã bị từ chối thường (cooldown 7 ngày).",
                            policy_revision="v2_step_median_checked_inputs",
                            sample_refs=[str(sid) for sid in clean_step_ids],
                            model_revision=None,
                            status="rejected",
                            supersedes_suggestion_id=None,
                            created_at=calc_now - timedelta(hours=2),
                            updated_at=calc_now - timedelta(hours=1),
                        )
                    )
                elif scenario == "cooldown_14d":
                    pref.suggestion_cooldown_until = calc_now + timedelta(
                        seconds=ANNOYANCE_COOLDOWN_SECONDS
                    )
                    db.add(
                        AdaptationSuggestion(
                            id=_a20_uuid(owner, f"{scenario}:rejected-suggestion"),
                            user_id=owner,
                            revision=2,
                            kind="buffer_multiplier",
                            sample_count=10,
                            current_value=DEFAULT_BUFFER_MULTIPLIER,
                            suggested_value=1.6,
                            reason=(
                                f"{A20_TITLE_PREFIX} Gợi ý đã bị từ chối kèm annoyance_feedback "
                                "(cooldown 14 ngày)."
                            ),
                            policy_revision="v2_step_median_checked_inputs",
                            sample_refs=[str(sid) for sid in clean_step_ids],
                            model_revision=None,
                            status="rejected",
                            supersedes_suggestion_id=None,
                            created_at=calc_now - timedelta(hours=2),
                            updated_at=calc_now - timedelta(hours=1),
                        )
                    )

            await db.flush()
        return {
            "dialect": engine.dialect.name,
            "scenario": scenario,
            "user_id": str(owner),
            "added_clean_steps": len(clean_step_ids),
            "added_dirty_steps": len(dirty_step_ids),
            "future": future_ids,
        }
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", metavar="EMAIL", help="Email Google của tài khoản QA đang hoạt động")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--scenario",
        choices=SCENARIOS,
        help="Tên kịch bản A20 cần seed cho tài khoản QA",
    )
    mode_group.add_argument(
        "--reset",
        action="store_true",
        help="Xóa sạch toàn bộ dữ liệu mẫu/phát sinh gắn với A20 (giữ nguyên nền W4-03A)",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    email = args.email.strip()
    if not email:
        parser.error("Cần email Google hợp lệ.")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("Đặt DATABASE_URL trỏ đến DB QA đã chạy alembic upgrade head.")
    try:
        if args.reset:
            result = asyncio.run(reset_a20_data(database_url, email))
            print(f"W4-12B: Đã reset dữ liệu A20 trên {result['dialect']}.")
        else:
            result = asyncio.run(seed_a20_scenario(database_url, email, args.scenario))
            print(
                f"W4-12B: Đã seed kịch bản '{result['scenario']}' trên {result['dialect']} "
                f"(+{result['added_clean_steps']} bước sạch, +{result['added_dirty_steps']} mẫu bẩn)."
            )
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
