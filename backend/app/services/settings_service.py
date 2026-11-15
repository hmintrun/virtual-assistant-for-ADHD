# Service nghiệp vụ UF7: cài đặt request/effective, hủy yêu cầu pending và gợi ý thích ứng A20.
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.execution import SessionRun as SessionRunRow
from app.models.schedule_proposal import RescheduleProposal as ProposalRow
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion as SuggestionRow
from app.models.settings import SettingChangeRequest as SettingChangeRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.models.user import UserPreference as PreferenceRow
from app.schemas.draft import UpdateDraftRequest
from app.schemas.settings import (
    AdaptationSuggestionData,
    CancelSettingsChangeRequest,
    CancelSettingsChangeResult,
    SettingsChangeRequest,
    SettingsChangeResult,
    SuggestionKind,
    SuggestionResponseRequest,
    SuggestionResponseResult,
    SuggestionStatus,
)
from app.services import draft_service, reschedule_service
from app.services.command_service import durable_command, owner_uuid
from app.services.scheduler_engine import evaluate_buffer_adaptation

DEFAULT_WORK_INTERVAL_SECONDS = 1500
DEFAULT_REST_INTERVAL_SECONDS = 300
DEFAULT_MICRO_TASK_SECONDS = 120
DEFAULT_REMINDER_OFFSETS = [900, 600, 300]
DEFAULT_BUFFER_MULTIPLIER = 1.2
DEFAULT_REMINDER_STYLE = "gentle"

BUFFER_POLICY_MIN = 1.0
BUFFER_POLICY_MAX = 2.0
BUFFER_DELTA_THRESHOLD = 0.2
SUGGESTION_MIN_SAMPLES = 5
SUGGESTION_SLIDING_WINDOW = 15
SUGGESTION_COOLDOWN_SECONDS = 7 * 24 * 3600
ANNOYANCE_COOLDOWN_SECONDS = 14 * 24 * 3600

_SCHEDULE_IMPACT_PREF_FIELDS = frozenset(
    {
        "work_interval_seconds",
        "rest_interval_seconds",
        "buffer_multiplier",
        "reminder_offsets",
    }
)
_CANCELLABLE_CHANGE_STATES = frozenset({"pending_review", "pending_proposal"})


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_uuid(raw_id: str) -> UUID:
    try:
        return UUID(raw_id)
    except ValueError as exc:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        ) from exc


def _suggestion_to_schema(row: SuggestionRow) -> AdaptationSuggestionData:
    kind: SuggestionKind = (
        "reminder_offset" if row.kind == "reminder_offset" else "buffer_multiplier"
    )
    status: SuggestionStatus
    if row.status in ("pending", "accepted", "rejected", "superseded"):
        status = row.status  # type: ignore[assignment]
    else:
        status = "pending"
    return AdaptationSuggestionData(
        suggestion_id=str(row.id),
        suggestion_revision=row.revision,
        kind=kind,
        sample_count=row.sample_count,
        current_value=row.current_value,
        suggested_value=row.suggested_value,
        reason=row.reason,
        policy_revision=row.policy_revision,
        sample_refs=list(row.sample_refs or []),
        model_revision=row.model_revision,
        status=status,
        supersedes_suggestion_id=(
            str(row.supersedes_suggestion_id) if row.supersedes_suggestion_id else None
        ),
    )


async def _has_future_confirmed_sessions(
    db: AsyncSession,
    owner: UUID,
    *,
    task_id: UUID | None = None,
) -> bool:
    stmt = (
        select(SessionRow.id)
        .join(StepRow, SessionRow.step_id == StepRow.id)
        .join(TaskRow, StepRow.task_id == TaskRow.id)
        .where(
            SessionRow.user_id == owner,
            SessionRow.status == "scheduled",
            TaskRow.user_id == owner,
            TaskRow.status.not_in(("completed", "cancelled")),
        )
        .limit(1)
    )
    if task_id is not None:
        stmt = stmt.where(TaskRow.id == task_id)
    return (await db.scalar(stmt)) is not None


async def _ensure_preference_row(
    db: AsyncSession,
    owner: UUID,
    *,
    now: datetime,
) -> PreferenceRow:
    existing = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    if existing is not None:
        return existing
    created = PreferenceRow(
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
    db.add(created)
    await db.flush()
    return created


def _validate_preferences_payload(
    *,
    values: dict[str, Any],
    preferences: PreferenceRow | None,
) -> None:
    required = {
        "work_interval_seconds",
        "rest_interval_seconds",
        "buffer_multiplier",
        "reminder_offsets",
        "reminder_style",
    }
    if preferences is None and not required.issubset(values):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Lần đầu cần đủ cấu hình làm/nghỉ, buffer và nhắc.",
            next_action="fix_fields",
        )
    work = values.get(
        "work_interval_seconds",
        preferences.work_interval_seconds if preferences else 0,
    )
    rest = values.get(
        "rest_interval_seconds",
        preferences.rest_interval_seconds if preferences else 0,
    )
    buffer = values.get(
        "buffer_multiplier",
        preferences.buffer_multiplier if preferences else 0,
    )
    offsets = values.get(
        "reminder_offsets",
        preferences.reminder_offsets if preferences else [],
    )
    style = values.get(
        "reminder_style",
        preferences.reminder_style if preferences else "",
    )
    if (
        work <= 0
        or rest < 0
        or buffer < 1
        or not 1 <= len(offsets) <= 3
        or len(set(offsets)) != len(offsets)
        or any(offset < 0 for offset in offsets)
        or not str(style).strip()
    ):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Cấu hình không hợp lệ.",
            next_action="fix_fields",
        )


@durable_command("settings_change", SettingsChangeResult, resource_arg="")
async def apply_settings_change(
    *,
    user_id: str,
    idempotency_key: str,
    payload: SettingsChangeRequest,
    db: AsyncSession,
) -> SettingsChangeResult:
    """Xử lý yêu cầu đổi cài đặt hoặc availability theo đúng ranh giới Requested vs Effective."""
    now = _utc_now()
    owner = owner_uuid(user_id)
    preferences = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    pref_revision = preferences.effective_revision if preferences else 0
    if (
        payload.expected_preferences_revision is not None
        and payload.expected_preferences_revision != pref_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Cấu hình đã thay đổi.",
            current_revisions={"preferences": pref_revision},
            next_action="reload_and_review",
        )

    pref_values: dict[str, Any] = {}
    if payload.changes.preferences is not None:
        pref_values = payload.changes.preferences.model_dump(exclude_unset=True)
        _validate_preferences_payload(values=pref_values, preferences=preferences)

    updated_draft = None
    target_task: TaskRow | None = None
    avail_impacts_schedule = False
    if payload.changes.availability is not None:
        if payload.target_task_id is not None:
            task_uuid = _parse_uuid(payload.target_task_id)
            target_task = await db.scalar(
                select(TaskRow).where(TaskRow.id == task_uuid, TaskRow.user_id == owner)
            )
            if target_task is None:
                raise AppError(
                    status_code=404,
                    code="RESOURCE_NOT_FOUND",
                    message="Không tìm thấy công việc mục tiêu.",
                    next_action="reload",
                )
            if target_task.revision != payload.expected_task_revision:
                raise AppError(
                    status_code=409,
                    code="REVISION_CONFLICT",
                    message="Task đã thay đổi.",
                    current_revisions={"task": target_task.revision},
                    next_action="reload_and_review",
                )
            avail_impacts_schedule = True
        else:
            if (
                payload.target_draft_id is None
                or payload.expected_summary_revision is None
                or payload.expected_planning_input_revision is None
            ):
                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message="Cần Draft và revision để chỉnh giờ.",
                    next_action="fix_fields",
                )
            draft = await draft_service.get_draft_by_id(
                user_id=user_id, draft_id=payload.target_draft_id, db=db
            )
            if draft.confirmed_task_id is not None:
                raise AppError(
                    status_code=409,
                    code="PLAN_NOT_CONFIRMABLE",
                    message="Draft đã thành Task; cần chỉnh Task qua xem tác động.",
                    next_action="review_task",
                )
            updated_draft = await draft_service.patch_draft(
                user_id=user_id,
                draft_id=payload.target_draft_id,
                idempotency_key=f"{idempotency_key}:draft_patch",
                payload=UpdateDraftRequest(
                    expected_summary_revision=payload.expected_summary_revision,
                    expected_planning_input_revision=payload.expected_planning_input_revision,
                    changes={
                        "summary": {
                            "availability": payload.changes.availability.model_dump(mode="json")
                        }
                    },
                ),
                db=db,
            )

    pref_impacts_schedule = False
    if pref_values:
        touches_schedule_field = any(
            k in _SCHEDULE_IMPACT_PREF_FIELDS
            and (preferences is None or getattr(preferences, k) != v)
            for k, v in pref_values.items()
        )
        if touches_schedule_field:
            pref_impacts_schedule = await _has_future_confirmed_sessions(db, owner)

    if avail_impacts_schedule or pref_impacts_schedule:
        # Tạo RescheduleProposal trước khi ghi requested_* lên UserPreference vì helper bên dưới sẽ dọn proposal pending cũ.
        proposal_target_id = payload.target_task_id if avail_impacts_schedule else None
        allowed_ids = [payload.target_task_id] if payload.target_task_id is not None else None
        proposal = await reschedule_service.create_settings_change_proposal(
            db,
            owner=owner,
            target_task_id=proposal_target_id,
            allowed_task_ids=allowed_ids,
            request_text="Đề xuất điều chỉnh lịch do thay đổi cài đặt UF7",
            now=now,
        )
        proposal_uuid = UUID(proposal.proposal_id)

        requested_rev: int | None = None
        if pref_values:
            preferences = await _ensure_preference_row(db, owner, now=now)
            requested_rev = preferences.effective_revision + 1
            preferences.requested_revision = requested_rev
            preferences.requested_changes = dict(pref_values)
            preferences.pending_proposal_id = proposal_uuid
            preferences.updated_at = now
            effective_rev = preferences.effective_revision
        else:
            effective_rev = preferences.effective_revision if preferences else 0

        req_avail_rev = (target_task.revision + 1) if target_task is not None else None
        eff_avail_rev = target_task.revision if target_task is not None else None

        stored_changes: dict[str, Any] = {}
        if payload.changes.availability is not None:
            stored_changes["availability"] = payload.changes.availability.model_dump(mode="json")
        if payload.target_task_id is not None:
            stored_changes["target_task_id"] = payload.target_task_id
        if payload.target_draft_id is not None:
            stored_changes["target_draft_id"] = payload.target_draft_id
        if pref_values:
            stored_changes["preferences"] = dict(pref_values)

        change = SettingChangeRow(
            user_id=owner,
            application_state="pending_review",
            requested_revision=requested_rev,
            effective_revision=effective_rev,
            requested_availability_revision=req_avail_rev,
            effective_availability_revision=eff_avail_rev,
            requested_changes=stored_changes,
            pending_proposal_id=proposal_uuid,
            created_at=now,
            updated_at=now,
        )
        db.add(change)
        await db.flush()
        return SettingsChangeResult(
            change_request_id=str(change.id),
            request_revision=change.revision,
            application_state=change.application_state,
            requested_revision=change.requested_revision,
            effective_revision=change.effective_revision,
            requested_availability_revision=change.requested_availability_revision,
            effective_availability_revision=change.effective_availability_revision,
            requested_changes=payload.changes,
            pending_proposal_id=proposal.proposal_id,
            target_draft_id=payload.target_draft_id,
            target_task_id=payload.target_task_id,
            summary_revision=updated_draft.summary_revision if updated_draft else None,
            planning_input_revision=(
                updated_draft.planning_input_revision if updated_draft else None
            ),
        )

    # Nhánh không ảnh hưởng lịch tương lai: áp dụng ngay cho Draft hoặc từ phiên tiếp theo (giữ nguyên settings_snapshot của phiên đang chạy).
    if pref_values:
        if preferences is None:
            preferences = PreferenceRow(
                user_id=owner,
                effective_revision=1,
                micro_task_seconds=DEFAULT_MICRO_TASK_SECONDS,
                created_at=now,
                updated_at=now,
                **pref_values,
            )
            db.add(preferences)
        elif any(getattr(preferences, k) != v for k, v in pref_values.items()):
            for k, v in pref_values.items():
                setattr(preferences, k, v)
            preferences.effective_revision += 1
            preferences.updated_at = now
        await db.flush()

    effective_rev = preferences.effective_revision if preferences else 0
    change = SettingChangeRow(
        user_id=owner,
        application_state=(
            "applied_to_draft" if updated_draft is not None else "effective_from_next_session"
        ),
        requested_revision=None,
        effective_revision=effective_rev,
        requested_availability_revision=None,
        effective_availability_revision=None,
        requested_changes=payload.changes.model_dump(mode="json", exclude_none=True),
        pending_proposal_id=None,
        created_at=now,
        updated_at=now,
    )
    db.add(change)
    await db.flush()
    return SettingsChangeResult(
        change_request_id=str(change.id),
        request_revision=change.revision,
        application_state=change.application_state,
        requested_revision=None,
        effective_revision=change.effective_revision,
        requested_availability_revision=None,
        effective_availability_revision=None,
        requested_changes=payload.changes,
        pending_proposal_id=None,
        target_draft_id=payload.target_draft_id,
        target_task_id=payload.target_task_id,
        summary_revision=updated_draft.summary_revision if updated_draft else None,
        planning_input_revision=(
            updated_draft.planning_input_revision if updated_draft else None
        ),
    )


@durable_command("cancel_settings_change", CancelSettingsChangeResult, resource_arg="change_request_id")
async def cancel_settings_change(
    *,
    user_id: str,
    idempotency_key: str,
    change_request_id: str,
    payload: CancelSettingsChangeRequest,
    db: AsyncSession,
) -> CancelSettingsChangeResult:
    """Hủy yêu cầu đổi cài đặt đang chờ duyệt; cấm hoàn tác cấu hình đã có hiệu lực."""
    del idempotency_key
    now = _utc_now()
    owner = owner_uuid(user_id)
    req_uuid = _parse_uuid(change_request_id)

    change = await db.scalar(
        select(SettingChangeRow).where(
            SettingChangeRow.id == req_uuid,
            SettingChangeRow.user_id == owner,
        )
    )
    if change is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    if payload.expected_request_revision != change.revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Dữ liệu đã thay đổi so với phiên bản gửi lên. Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới.",
            current_revisions={"request_revision": change.revision},
            next_action="reload_and_review",
        )

    if change.application_state not in _CANCELLABLE_CHANGE_STATES:
        raise AppError(
            status_code=409,
            code="SETTINGS_CHANGE_NOT_CANCELLABLE",
            message="Yêu cầu đã được xử lý hoặc đã có hiệu lực nên không thể hủy.",
            current_revisions={"request_revision": change.revision},
            next_action="reload",
        )

    linked_proposal_id = change.pending_proposal_id
    change.application_state = "cancelled"
    change.revision += 1
    change.requested_revision = None
    change.requested_availability_revision = None
    change.updated_at = now

    if linked_proposal_id is not None:
        proposal = await db.scalar(
            select(ProposalRow).where(
                ProposalRow.id == linked_proposal_id,
                ProposalRow.user_id == owner,
            )
        )
        if proposal is not None and proposal.status == "pending":
            proposal.status = "rejected"
            proposal.proposal_revision += 1
            proposal.revision += 1
            proposal.updated_at = now

    preferences = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    if (
        preferences is not None
        and linked_proposal_id is not None
        and preferences.pending_proposal_id == linked_proposal_id
    ):
        preferences.requested_revision = None
        preferences.requested_changes = None
        preferences.pending_proposal_id = None
        preferences.updated_at = now

    await db.flush()
    effective_rev = preferences.effective_revision if preferences else change.effective_revision
    return CancelSettingsChangeResult(
        change_request_id=str(change.id),
        application_state="cancelled",
        effective_revision=effective_rev,
    )


async def collect_buffer_step_samples(
    db: AsyncSession,
    *,
    owner: UUID,
) -> list[dict[str, Any]]:
    """Tổng hợp mẫu Step hoàn thành sạch (actual từ SessionRun đã kết thúc) theo thứ tự cũ -> mới."""
    step_rows = (
        await db.scalars(
            select(StepRow)
            .join(TaskRow, StepRow.task_id == TaskRow.id)
            .where(
                StepRow.user_id == owner,
                StepRow.status == "completed",
                StepRow.estimated_base_seconds > 0,
                TaskRow.user_id == owner,
                TaskRow.status != "cancelled",
            )
            .order_by(StepRow.created_at.asc(), StepRow.id.asc())
        )
    ).all()
    if not step_rows:
        return []

    step_ids = [s.id for s in step_rows]
    session_rows = (
        await db.scalars(
            select(SessionRow).where(
                SessionRow.user_id == owner,
                SessionRow.step_id.in_(step_ids),
            )
        )
    ).all()
    if not session_rows:
        return []

    session_to_step = {sess.id: sess.step_id for sess in session_rows}
    session_ids = list(session_to_step.keys())
    run_rows = (
        await db.scalars(
            select(SessionRunRow)
            .where(
                SessionRunRow.user_id == owner,
                SessionRunRow.session_id.in_(session_ids),
            )
            .order_by(SessionRunRow.created_at.asc())
        )
    ).all()

    runs_by_step: dict[UUID, list[SessionRunRow]] = {}
    for run in run_rows:
        step_id = session_to_step.get(run.session_id)
        if step_id is None:
            continue
        if run.run_state in ("running", "paused"):
            continue
        if run.outcome == "cancelled":
            continue
        if run.actual_work_seconds is None or run.actual_work_seconds <= 0:
            continue
        if run.actual_source not in ("timer_proxy", "self_report"):
            continue
        runs_by_step.setdefault(step_id, []).append(run)

    ordered_entries: list[tuple[datetime, dict[str, Any]]] = []
    for step in step_rows:
        valid_runs = runs_by_step.get(step.id)
        if not valid_runs:
            continue
        total_actual = sum(int(r.actual_work_seconds or 0) for r in valid_runs)
        if total_actual <= 0:
            continue
        source = (
            "self_report"
            if any(r.actual_source == "self_report" for r in valid_runs)
            else "timer_proxy"
        )
        completion_stamps = [
            _as_utc(r.ended_at or r.updated_at) for r in valid_runs if (r.ended_at or r.updated_at)
        ]
        completed_at = (
            max(st for st in completion_stamps if st is not None)
            if completion_stamps
            else (_as_utc(step.updated_at) or datetime.now(UTC))
        )
        ordered_entries.append(
            (
                completed_at,
                {
                    "step_id": str(step.id),
                    "status": step.status,
                    "estimated_base_seconds": step.estimated_base_seconds,
                    "actual_work_seconds": total_actual,
                    "scope_modified": step.estimate_source == "scope_modified",
                    "actual_source": source,
                },
            )
        )

    ordered_entries.sort(key=lambda item: item[0])
    return [item[1] for item in ordered_entries]


async def get_current_suggestion(
    db: AsyncSession,
    *,
    user_id: str,
    kind: SuggestionKind | None = None,
    now: datetime | None = None,
) -> AdaptationSuggestionData | None:
    """Đọc gợi ý pending hiện hành hoặc đánh giá A20 từ các mẫu Step hoàn thành sạch."""
    calc_now = _utc_now(now)
    owner = owner_uuid(user_id)

    pending_stmt = select(SuggestionRow).where(
        SuggestionRow.user_id == owner,
        SuggestionRow.status == "pending",
    )
    if kind is not None:
        pending_stmt = pending_stmt.where(SuggestionRow.kind == kind)
    pending_row = await db.scalar(pending_stmt.order_by(SuggestionRow.created_at.desc()).limit(1))
    if pending_row is not None:
        return _suggestion_to_schema(pending_row)

    if kind not in (None, "buffer_multiplier"):
        return None

    preferences = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    if preferences is not None:
        cooldown_until = _as_utc(preferences.suggestion_cooldown_until)
        if cooldown_until is not None and cooldown_until > calc_now:
            return None

    last_rejected = await db.scalar(
        select(SuggestionRow)
        .where(
            SuggestionRow.user_id == owner,
            SuggestionRow.kind == "buffer_multiplier",
            SuggestionRow.status == "rejected",
        )
        .order_by(SuggestionRow.updated_at.desc())
        .limit(1)
    )
    last_rejected_at = _as_utc(last_rejected.updated_at) if last_rejected is not None else None

    samples = await collect_buffer_step_samples(db, owner=owner)
    current_buffer = preferences.buffer_multiplier if preferences else DEFAULT_BUFFER_MULTIPLIER
    eval_result = evaluate_buffer_adaptation(
        step_samples=samples,
        current_buffer_multiplier=current_buffer,
        min_sample_count=SUGGESTION_MIN_SAMPLES,
        policy_min=BUFFER_POLICY_MIN,
        policy_max=BUFFER_POLICY_MAX,
        delta_threshold=BUFFER_DELTA_THRESHOLD,
        sliding_window_steps=SUGGESTION_SLIDING_WINDOW,
        step_increment=0.05,
        last_suggestion_at=last_rejected_at,
        current_time=calc_now,
        cooldown_seconds=SUGGESTION_COOLDOWN_SECONDS,
    )
    if eval_result is None:
        return None

    # Quy tắc UF7 (A20): chỉ tạo gợi ý khi |k_user - k_history| > 0.2.
    diff = abs(Decimal(str(eval_result["suggested_value"])) - Decimal(str(current_buffer)))
    if diff <= Decimal(str(BUFFER_DELTA_THRESHOLD)):
        return None

    created = SuggestionRow(
        user_id=owner,
        kind=eval_result["kind"],
        sample_count=int(eval_result["sample_count"]),
        current_value=eval_result["current_value"],
        suggested_value=eval_result["suggested_value"],
        reason=eval_result["reason"],
        policy_revision=eval_result["policy_revision"],
        sample_refs=list(eval_result["sample_refs"]),
        model_revision=eval_result["model_revision"],
        status="pending",
        supersedes_suggestion_id=None,
        created_at=calc_now,
        updated_at=calc_now,
    )
    db.add(created)
    await db.commit()
    await db.refresh(created)
    return _suggestion_to_schema(created)


def _validate_accepted_suggestion_value(
    *,
    kind: str,
    raw_value: Any,
) -> float | list[int]:
    if kind == "buffer_multiplier":
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Hệ số đệm phải là số thực nằm trong [1.0, 2.0].",
                next_action="fix_fields",
            )
        numeric = float(raw_value)
        if not math.isfinite(numeric) or numeric < BUFFER_POLICY_MIN or numeric > BUFFER_POLICY_MAX:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Hệ số đệm phải nằm trong khoảng [1.0, 2.0].",
                next_action="fix_fields",
            )
        return round(numeric, 4)

    if kind == "reminder_offset":
        if (
            not isinstance(raw_value, list)
            or not 1 <= len(raw_value) <= 3
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in raw_value)
            or len(set(raw_value)) != len(raw_value)
        ):
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Mốc nhắc phải gồm 1–3 số nguyên không âm và không trùng nhau.",
                next_action="fix_fields",
            )
        return [int(v) for v in raw_value]

    raise AppError(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Loại gợi ý không được hỗ trợ.",
        next_action="fix_fields",
    )


@durable_command("respond_to_suggestion", SuggestionResponseResult, resource_arg="suggestion_id")
async def respond_to_suggestion(
    *,
    user_id: str,
    idempotency_key: str,
    suggestion_id: str,
    payload: SuggestionResponseRequest,
    db: AsyncSession,
) -> SuggestionResponseResult:
    """Phản hồi gợi ý trợ lý (accept/edit/reject) và nối UF6 khi ảnh hưởng lịch tương lai (R08)."""
    del idempotency_key
    now = _utc_now()
    owner = owner_uuid(user_id)
    sugg_uuid = _parse_uuid(suggestion_id)

    suggestion = await db.scalar(
        select(SuggestionRow).where(
            SuggestionRow.id == sugg_uuid,
            SuggestionRow.user_id == owner,
        )
    )
    if suggestion is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    if suggestion.status != "pending":
        raise AppError(
            status_code=409,
            code="SUGGESTION_CHANGED",
            message="Gợi ý đã được xử lý hoặc thay thế.",
            current_revisions={"suggestion_revision": suggestion.revision},
            next_action="reload",
        )

    if payload.expected_suggestion_revision != suggestion.revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản nguồn đã đổi. Tải bản hiện tại trước khi sửa.",
            current_revisions={"suggestion_revision": suggestion.revision},
            next_action="reload_and_review",
        )

    preferences = await db.scalar(select(PreferenceRow).where(PreferenceRow.user_id == owner))
    current_pref_rev = preferences.effective_revision if preferences else 0
    if (
        payload.expected_preference_revision is not None
        and payload.expected_preference_revision != current_pref_rev
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản cài đặt đã thay đổi.",
            current_revisions={"preferences": current_pref_rev},
            next_action="reload_and_review",
        )

    kind: SuggestionKind = (
        "reminder_offset" if suggestion.kind == "reminder_offset" else "buffer_multiplier"
    )

    if payload.action == "reject":
        pref_row = await _ensure_preference_row(db, owner, now=now)
        cooldown_secs = (
            ANNOYANCE_COOLDOWN_SECONDS
            if payload.annoyance_feedback is not None
            else SUGGESTION_COOLDOWN_SECONDS
        )
        pref_row.suggestion_cooldown_until = now + timedelta(seconds=cooldown_secs)
        pref_row.updated_at = now
        suggestion.status = "rejected"
        suggestion.revision += 1
        suggestion.updated_at = now
        await db.flush()
        return SuggestionResponseResult(
            suggestion_id=str(suggestion.id),
            suggestion_revision=suggestion.revision,
            kind=kind,
            action="reject",
            status="rejected",
            accepted_value=None,
            application_state="not_applied",
            effective_revision=pref_row.effective_revision,
            requested_revision=None,
            change_request_id=None,
            pending_proposal_id=None,
        )

    raw_target = payload.edited_value if payload.action == "edit" else suggestion.suggested_value
    accepted_value = _validate_accepted_suggestion_value(kind=kind, raw_value=raw_target)
    pref_field = "reminder_offsets" if kind == "reminder_offset" else "buffer_multiplier"
    pref_delta: dict[str, Any] = {pref_field: accepted_value}

    pref_row = await _ensure_preference_row(db, owner, now=now)
    suggestion.status = "accepted"
    suggestion.revision += 1
    suggestion.updated_at = now

    has_future_sessions = await _has_future_confirmed_sessions(db, owner)
    if not has_future_sessions:
        setattr(pref_row, pref_field, accepted_value)
        pref_row.effective_revision += 1
        pref_row.updated_at = now
        change = SettingChangeRow(
            user_id=owner,
            application_state="effective_from_next_session",
            requested_revision=None,
            effective_revision=pref_row.effective_revision,
            requested_availability_revision=None,
            effective_availability_revision=None,
            requested_changes={"preferences": pref_delta, "suggestion_id": str(suggestion.id)},
            pending_proposal_id=None,
            created_at=now,
            updated_at=now,
        )
        db.add(change)
        await db.flush()
        return SuggestionResponseResult(
            suggestion_id=str(suggestion.id),
            suggestion_revision=suggestion.revision,
            kind=kind,
            action=payload.action,
            status="accepted",
            accepted_value=accepted_value,
            application_state="effective_from_next_session",
            effective_revision=pref_row.effective_revision,
            requested_revision=None,
            change_request_id=str(change.id),
            pending_proposal_id=None,
        )

    # R08: Khi đã có phiên tương lai, luôn đẩy qua UF6 pending_review để user xem và duyệt lịch.
    proposal = await reschedule_service.create_settings_change_proposal(
        db,
        owner=owner,
        request_text=f"Cập nhật {kind} từ gợi ý trợ lý",
        now=now,
    )
    proposal_uuid = UUID(proposal.proposal_id)
    req_rev = pref_row.effective_revision + 1
    pref_row.requested_revision = req_rev
    pref_row.requested_changes = dict(pref_delta)
    pref_row.pending_proposal_id = proposal_uuid
    pref_row.updated_at = now

    change = SettingChangeRow(
        user_id=owner,
        application_state="pending_review",
        requested_revision=req_rev,
        effective_revision=pref_row.effective_revision,
        requested_availability_revision=None,
        effective_availability_revision=None,
        requested_changes={"preferences": pref_delta, "suggestion_id": str(suggestion.id)},
        pending_proposal_id=proposal_uuid,
        created_at=now,
        updated_at=now,
    )
    db.add(change)
    await db.flush()
    return SuggestionResponseResult(
        suggestion_id=str(suggestion.id),
        suggestion_revision=suggestion.revision,
        kind=kind,
        action=payload.action,
        status="accepted",
        accepted_value=accepted_value,
        application_state="pending_review",
        effective_revision=pref_row.effective_revision,
        requested_revision=req_rev,
        change_request_id=str(change.id),
        pending_proposal_id=proposal.proposal_id,
    )
