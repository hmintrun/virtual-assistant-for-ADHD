"""Bộ kiểm thử Hardening Batch H1 (`H1-01` .. `H1-08`): Cross-UF5/UF6/UF7 & Focus Streak."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import Base
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.execution import CheckIn, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.execution import ActualCorrectionRequest, CheckInResponseRequest
from app.schemas.reschedule import ApproveProposalRequest, ProposalSourceRevisions
from app.schemas.schedule import RescheduleEmpatheticResult
from app.schemas.settings import SettingsChangeRequest, SuggestionResponseRequest
from app.schemas.user import CurrentUser
from app.services import (
    checkin_service,
    command_service,
    plan_service,
    reschedule_service,
    run_service,
    settings_service,
    streak_service,
)
from app.services.credential_crypto import encrypt_credentials

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


async def _make_env(db_url: str = "sqlite+aiosqlite:///:memory:"):
    engine = make_engine(db_url)
    sf = session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return engine, sf


def _auth_headers(user_id: UUID, idempotency_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"h1_{user_id.hex[:8]}@example.com",
        display_name="H1 Hardening Tester",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-{user_id.hex[:8]}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _seed_user_base(
    db: AsyncSession,
    *,
    now: datetime,
    timezone_name: str = "Asia/Ho_Chi_Minh",
    streak_days: int = 0,
    streak_frozen: bool = False,
    freeze_tokens: int = 2,
    last_streak_date=None,
    with_calendar: bool = False,
) -> dict[str, UUID]:
    uid = uuid4()
    avail_id = uuid4()
    conn_id = uuid4()

    db.add(
        User(
            id=uid,
            email=f"h1_{uid.hex[:8]}@example.com",
            hashed_password="pw",
            display_name="H1 Tester",
            current_streak_days=streak_days,
            streak_frozen=streak_frozen,
            streak_freeze_tokens=freeze_tokens,
            last_streak_date=last_streak_date,
        )
    )
    await db.flush()
    db.add(
        UserPreferenceRow(
            user_id=uid,
            revision=1,
            effective_revision=1,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=1.2,
            reminder_style="gentle",
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        AvailabilityRow(
            id=avail_id,
            user_id=uid,
            revision=1,
            timezone=timezone_name,
            weekly_rules=[
                {"weekday": w, "start_local": "08:00", "end_local": "23:00"}
                for w in range(1, 8)
            ],
            date_overrides=[],
            task_limits=[],
            source="manual_input",
            confirmed_at=now - timedelta(days=1),
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        PlanningContext(
            id=uuid4(),
            user_id=uid,
            revision=1,
            availability_id=avail_id,
            availability_revision=1,
            selected_calendar_ids=["primary"],
            query_start=now - timedelta(days=1),
            query_end=now + timedelta(days=14),
            busy_intervals=[],
            fetched_at=now - timedelta(minutes=10),
            sync_state="ok",
            calendar_errors=[],
            event_snapshots=[],
            coverage_state="complete",
            manual_busy_intervals=[],
            excluded_sources=[],
            selected_calendar_sources=[],
        )
    )
    if with_calendar:
        creds = {
            "access_token": "acc_h1",
            "refresh_token": "ref_h1",
            "expires_in": 3600,
            "stored_at": now.isoformat(),
            "user_id": str(uid),
            "connection_id": str(conn_id),
        }
        db.add(
            CalendarConnection(
                id=conn_id,
                user_id=uid,
                revision=1,
                provider="google",
                account_ref=f"h1_{uid.hex[:8]}@example.com",
                read_calendar_ids=["primary"],
                write_calendar_id="proplan_secondary",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref=encrypt_credentials(creds),
                last_successful_sync_at=now - timedelta(minutes=5),
            )
        )
    await db.flush()
    return {"user_id": uid, "avail_id": avail_id, "conn_id": conn_id}


async def _seed_task_step_session(
    db: AsyncSession,
    *,
    uid: UUID,
    now: datetime,
    start: datetime,
    duration_seconds: int = 1800,
    title: str = "Task H1",
    schedule_revision: int = 1,
    add_schedule_version: bool = True,
) -> dict[str, UUID]:
    task_id = uuid4()
    step_id = uuid4()
    sess_id = uuid4()

    db.add(
        TaskRow(
            id=task_id,
            user_id=uid,
            revision=1,
            title=title,
            description="Mô tả H1",
            scope="Phạm vi H1",
            extra_requirements=[],
            time_constraint={
                "mode": "deadline",
                "at": (now + timedelta(days=10)).isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            },
            importance=1,
            status="scheduled",
            decomposition_state="complete",
            availability={
                "timezone": "Asia/Ho_Chi_Minh",
                "weekly_rules": [
                    {"weekday": w, "start_local": "08:00", "end_local": "23:00"}
                    for w in range(1, 8)
                ],
                "date_overrides": [],
            },
        )
    )
    await db.flush()
    db.add(
        StepRow(
            id=step_id,
            user_id=uid,
            task_id=task_id,
            revision=1,
            order_index=0,
            title=f"Bước {title}",
            expected_output=f"Đầu ra {title}",
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="ai",
            status="scheduled",
        )
    )
    await db.flush()
    end = start + timedelta(seconds=duration_seconds)
    db.add(
        SessionRow(
            id=sess_id,
            user_id=uid,
            step_id=step_id,
            revision=1,
            scheduled_start=start,
            scheduled_end=end,
            title=f"Phiên {title}",
            session_goal=f"Mục tiêu {title}",
            expected_output=f"Đầu ra {title}",
            micro_task_text="Mở tài liệu",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=300,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                {"order_index": 1, "kind": "rest", "duration_seconds": 300},
            ],
            duration_seconds=duration_seconds,
            schedule_revision=schedule_revision,
            status="scheduled",
            start_state="waiting",
            start_decision_due_at=start + timedelta(minutes=5),
        )
    )
    db.add(
        Reminder(
            user_id=uid,
            session_id=sess_id,
            schedule_revision=schedule_revision,
            kind="pre_session",
            fire_at=start - timedelta(seconds=900),
            dedupe_key=f"session:{sess_id}:version:{schedule_revision}:reminder:900",
            delivery_state="scheduled",
        )
    )
    if add_schedule_version:
        db.add(
            ScheduleVersionRow(
                user_id=uid,
                version_number=schedule_revision,
                base_version=None,
                approved_at=now - timedelta(hours=1),
                affected_task_ids=[str(task_id)],
                min_inter_session_gap_seconds=900,
                alarm_sync_state="synced",
            )
        )
    await db.flush()
    return {"task_id": task_id, "step_id": step_id, "session_id": sess_id}


def _make_proposal_row(
    *,
    uid: UUID,
    now: datetime,
    target_task_id: UUID,
    proposed_changes: dict[str, Any],
    base_schedule_revision: int = 1,
    pending_notification_state: str = "not_sent",
) -> RescheduleProposal:
    return RescheduleProposal(
        id=uuid4(),
        user_id=uid,
        status="pending",
        trigger_reason="manual_request",
        target_task_id=target_task_id,
        allowed_task_ids=[str(target_task_id)],
        affected_task_ids=[str(target_task_id)],
        base_schedule_revision=base_schedule_revision,
        source_revisions={
            "task": 1,
            "schedule": base_schedule_revision,
            "availability": 1,
            "settings": 1,
            "calendar": 1,
        },
        proposal_revision=1,
        proposed_changes=proposed_changes,
        feasibility={
            "status": "feasible",
            "confirmable": True,
            "deficit_seconds": 0,
            "blocking_reasons": [],
            "next_horizon_preview": None,
        },
        explanation="Đã sắp xếp lại khung giờ phù hợp hơn.",
        generation_tier="tier_1_gemini",
        pending_notification_state=pending_notification_state,
        pending_notification_sent_at=None,
        created_at=now,
        updated_at=now,
    )


async def test_h1_01_approve_rejects_moved_or_cancelled_session_already_completed_or_terminal_step():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=now)
            uid = base["user_id"]
            s_start = now + timedelta(hours=4)
            s_end = s_start + timedelta(minutes=30)
            t = await _seed_task_step_session(db, uid=uid, now=now, start=s_start)

            # (a) sessions_to_move có session đã chuyển sang completed
            prop_move = _make_proposal_row(
                uid=uid,
                now=now,
                target_task_id=t["task_id"],
                proposed_changes={
                    "sessions_to_move": [
                        {
                            "session_id": str(t["session_id"]),
                            "from_start": s_start.isoformat(),
                            "from_end": s_end.isoformat(),
                            "to_start": (s_start + timedelta(hours=2)).isoformat(),
                            "to_end": (s_end + timedelta(hours=2)).isoformat(),
                            "reason": "avoid_conflict",
                        }
                    ],
                    "sessions_to_cancel": [],
                    "sessions_to_add": [],
                    "steps_to_add": [],
                },
            )
            db.add(prop_move)
            sess_row = await db.get(SessionRow, t["session_id"])
            assert sess_row is not None
            sess_row.status = "completed"
            await db.commit()

            with pytest.raises(AppError) as exc_move:
                await reschedule_service.approve_proposal(
                    db,
                    user_id=str(uid),
                    proposal_id=str(prop_move.id),
                    idempotency_key="h1-01-move-completed",
                    payload=ApproveProposalRequest(
                        expected_proposal_revision=1,
                        expected_base_schedule_revision=1,
                        expected_source_revisions=ProposalSourceRevisions(
                            task=1, schedule=1, availability=1, settings=1, calendar=1
                        ),
                    ),
                    now=now,
                )
            assert exc_move.value.status_code == 409
            assert exc_move.value.code == "SOURCE_CHANGED"
            assert exc_move.value.next_action == "rebuild_and_review"

            # (b) sessions_to_cancel có Step chuyển sang completed (session vẫn scheduled)
            prop_move.status = "rejected"
            await db.flush()
            sess_row.status = "scheduled"
            step_row = await db.get(StepRow, t["step_id"])
            assert step_row is not None
            step_row.status = "completed"
            prop_cancel = _make_proposal_row(
                uid=uid,
                now=now,
                target_task_id=t["task_id"],
                proposed_changes={
                    "sessions_to_move": [],
                    "sessions_to_cancel": [
                        {"session_id": str(t["session_id"]), "reason": "resegmented_by_settings_change"}
                    ],
                    "sessions_to_add": [],
                    "steps_to_add": [
                        {
                            "step_id": str(uuid4()),
                            "task_id": str(t["task_id"]),
                            "order_index": 1,
                            "title": "Bước mới không được phép flush",
                            "estimated_base_seconds": 1500,
                        }
                    ],
                },
            )
            db.add(prop_cancel)
            await db.commit()

            with pytest.raises(AppError) as exc_cancel:
                await reschedule_service.approve_proposal(
                    db,
                    user_id=str(uid),
                    proposal_id=str(prop_cancel.id),
                    idempotency_key="h1-01-cancel-terminal-step",
                    payload=ApproveProposalRequest(
                        expected_proposal_revision=1,
                        expected_base_schedule_revision=1,
                        expected_source_revisions=ProposalSourceRevisions(
                            task=1, schedule=1, availability=1, settings=1, calendar=1
                        ),
                    ),
                    now=now,
                )
            assert exc_cancel.value.status_code == 409
            assert exc_cancel.value.code == "SOURCE_CHANGED"
            assert exc_cancel.value.next_action == "rebuild_and_review"

            # Không có bước mới hoặc ScheduleVersion mới nào được ghi dở
            steps_count = len((await db.scalars(select(StepRow).where(StepRow.user_id == uid))).all())
            versions_count = len(
                (await db.scalars(select(ScheduleVersionRow).where(ScheduleVersionRow.user_id == uid))).all()
            )
            assert steps_count == 1
            assert versions_count == 1

            # (c) UUID hỏng trong sessions_to_cancel -> 409 SOURCE_CHANGED (không 500 ValueError)
            prop_cancel.status = "rejected"
            await db.flush()
            prop_bad = _make_proposal_row(
                uid=uid,
                now=now,
                target_task_id=t["task_id"],
                proposed_changes={
                    "sessions_to_move": [],
                    "sessions_to_cancel": [{"session_id": "not-a-uuid", "reason": "resegmented"}],
                    "sessions_to_add": [],
                    "steps_to_add": [],
                },
            )
            db.add(prop_bad)
            await db.commit()
            with pytest.raises(AppError) as exc_bad:
                await reschedule_service.approve_proposal(
                    db,
                    user_id=str(uid),
                    proposal_id=str(prop_bad.id),
                    idempotency_key="h1-01-bad-uuid",
                    payload=ApproveProposalRequest(
                        expected_proposal_revision=1,
                        expected_base_schedule_revision=1,
                        expected_source_revisions=ProposalSourceRevisions(
                            task=1, schedule=1, availability=1, settings=1, calendar=1
                        ),
                    ),
                    now=now,
                )
            assert exc_bad.value.status_code == 409
            assert exc_bad.value.code == "SOURCE_CHANGED"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_01_approve_rejects_added_session_targeting_terminal_step_or_task():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=now)
            uid = base["user_id"]
            t = await _seed_task_step_session(db, uid=uid, now=now, start=now + timedelta(hours=3))

            step_row = await db.get(StepRow, t["step_id"])
            assert step_row is not None
            step_row.status = "completed"

            add_start = now + timedelta(hours=6)
            add_end = add_start + timedelta(minutes=30)
            prop = _make_proposal_row(
                uid=uid,
                now=now,
                target_task_id=t["task_id"],
                proposed_changes={
                    "steps_to_add": [],
                    "sessions_to_cancel": [],
                    "sessions_to_move": [],
                    "sessions_to_add": [
                        {
                            "temp_session_id": "tmp-1",
                            "action": "create_session",
                            "task_id": str(t["task_id"]),
                            "step_id": str(t["step_id"]),
                            "after": {
                                "step_id": str(t["step_id"]),
                                "start": add_start.isoformat(),
                                "end": add_end.isoformat(),
                                "work_seconds": 1500,
                                "rest_seconds": 300,
                                "duration_seconds": 1800,
                            },
                            "reason": "add_missing_session",
                        }
                    ],
                },
            )
            db.add(prop)
            await db.commit()

            with pytest.raises(AppError) as exc_add:
                await reschedule_service.approve_proposal(
                    db,
                    user_id=str(uid),
                    proposal_id=str(prop.id),
                    idempotency_key="h1-01-add-terminal",
                    payload=ApproveProposalRequest(
                        expected_proposal_revision=1,
                        expected_base_schedule_revision=1,
                        expected_source_revisions=ProposalSourceRevisions(
                            task=1, schedule=1, availability=1, settings=1, calendar=1
                        ),
                    ),
                    now=now,
                )
            assert exc_add.value.status_code == 409
            assert exc_add.value.code == "SOURCE_CHANGED"
            sess_count = len((await db.scalars(select(SessionRow).where(SessionRow.user_id == uid))).all())
            assert sess_count == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_02_cancel_surplus_skips_delete_job_when_calendar_disconnected_and_undo_clean():
    engine, sf = await _make_env()
    try:
        t0 = datetime(2026, 11, 20, 7, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=t0, with_calendar=True)
            uid = base["user_id"]
            conn_id = base["conn_id"]

            t = await _seed_task_step_session(db, uid=uid, now=t0, start=t0 + timedelta(hours=1))
            sess1_id = t["session_id"]
            sess2_id = uuid4()
            s2_start = t0 + timedelta(hours=3)
            db.add(
                SessionRow(
                    id=sess2_id,
                    user_id=uid,
                    step_id=t["step_id"],
                    revision=1,
                    scheduled_start=s2_start,
                    scheduled_end=s2_start + timedelta(minutes=30),
                    title="Phiên dư 2",
                    micro_task_text="Mở tài liệu",
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=300,
                    segments=[
                        {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                        {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                    ],
                    duration_seconds=1800,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                    start_decision_due_at=s2_start + timedelta(minutes=5),
                )
            )
            await db.flush()
            link2 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=sess2_id,
                connection_id=conn_id,
                google_calendar_id="proplan_secondary",
                google_event_id=f"ev_{sess2_id.hex[:12]}",
                google_etag='"etag-1"',
                desired_schedule_revision=1,
                synced_schedule_revision=1,
                sync_state="synced",
                is_current=True,
            )
            db.add(link2)

            # Ngắt kết nối lịch trước khi check-in hoàn thành sớm bước
            conn = await db.get(CalendarConnection, conn_id)
            assert conn is not None
            conn.connection_state = "disconnected"

            sess1 = await db.get(SessionRow, sess1_id)
            assert sess1 is not None
            sess1.status = "awaiting_checkin"
            sess1.start_state = "started"
            run = SessionRun(
                id=uuid4(),
                user_id=uid,
                session_id=sess1_id,
                device_id="dev-h1-02",
                started_at=t0 + timedelta(hours=1),
                ended_at=t0 + timedelta(hours=1, minutes=25),
                run_state="awaiting_checkin",
                active_phase="ended",
                segment_index=1,
                work_elapsed_seconds=1500,
                rest_elapsed_seconds=0,
                actual_work_seconds=1500,
                actual_source="timer",
                outcome="unknown",
                checkin_timeout_at=t0 + timedelta(hours=1, minutes=30),
                settings_snapshot={"effective_revision": 1},
            )
            db.add(run)
            await db.flush()
            ci = CheckIn(
                id=uuid4(),
                user_id=uid,
                run_id=run.id,
                kind="final",
                response_state="open",
                session_outcome="unknown",
                asked_at=t0 + timedelta(hours=1, minutes=25),
                response_revision=0,
            )
            db.add(ci)
            await db.commit()

            ci_res = await checkin_service.respond_to_checkin(
                db,
                user_id=str(uid),
                checkin_id=str(ci.id),
                idempotency_key="h1-02-step-done",
                payload=CheckInResponseRequest(
                    expected_response_revision=0,
                    session_outcome="completed",
                    step_completed=True,
                    responded_at=t0 + timedelta(hours=1, minutes=26),
                ),
                now=t0 + timedelta(hours=1, minutes=26),
            )
            assert ci_res.response_revision == 1

            sess2_after = await db.get(SessionRow, sess2_id)
            assert sess2_after is not None and sess2_after.status == "cancelled"
            del_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(
                        CalendarSyncJob.user_id == uid,
                        CalendarSyncJob.operation == "delete",
                    )
                )
            ).all()
            assert del_jobs == []

            link2_after = await db.get(CalendarEventLink, link2.id)
            assert link2_after is not None
            assert link2_after.sync_state == "synced"
            assert link2_after.is_current is True
            assert await plan_service._calendar_state(db, uid, 1) == "not_integrated"

            # Undo (correction -> incomplete) khôi phục sạch không lỗi và không tạo job
            await checkin_service.respond_to_checkin(
                db,
                user_id=str(uid),
                checkin_id=str(ci.id),
                idempotency_key="h1-02-undo",
                payload=CheckInResponseRequest(
                    expected_response_revision=1,
                    session_outcome="incomplete",
                    step_completed=False,
                    responded_at=t0 + timedelta(hours=1, minutes=27),
                ),
                now=t0 + timedelta(hours=1, minutes=27),
            )
            sess2_restored = await db.get(SessionRow, sess2_id)
            assert sess2_restored is not None and sess2_restored.status == "scheduled"
            assert await plan_service._calendar_state(db, uid, 1) == "not_integrated"
            all_jobs = (await db.scalars(select(CalendarSyncJob).where(CalendarSyncJob.user_id == uid))).all()
            assert all_jobs == []
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_03_streak_late_checkin_on_frozen_day_then_undo_and_actual_correction_preserves_freeze():
    engine, sf = await _make_env()
    try:
        day1 = datetime(2026, 11, 10, 10, 0, tzinfo=TZ_VN)
        day2 = day1 + timedelta(days=1)
        day3 = day1 + timedelta(days=2)

        async def _setup_frozen_user_and_checkin(db: AsyncSession, suffix: str):
            base = await _seed_user_base(
                db,
                now=day1.astimezone(UTC),
                streak_days=3,
                streak_frozen=False,
                freeze_tokens=2,
                last_streak_date=day1.date(),
            )
            uid = base["user_id"]
            t = await _seed_task_step_session(db, uid=uid, now=day1.astimezone(UTC), start=day2.astimezone(UTC))
            await db.commit()

            # Auto-Freeze ngày day2 khi mở app ở day3 -> streak_frozen=True, last_streak_date=day2, tokens=1
            summary = await streak_service.get_streak_summary(
                db, user_id=str(uid), commit_if_changed=True, now=day3.astimezone(UTC)
            )
            assert summary.streak_frozen is True
            assert summary.last_streak_date == day2.date()
            assert summary.streak_freeze_tokens == 1
            assert summary.current_streak_days == 3

            # Check-in muộn rơi vào đúng day2 (đang frozen)
            sess = await db.get(SessionRow, t["session_id"])
            assert sess is not None
            sess.status = "awaiting_checkin"
            sess.start_state = "started"
            run = SessionRun(
                id=uuid4(),
                user_id=uid,
                session_id=sess.id,
                device_id=f"dev-{suffix}",
                started_at=day2.astimezone(UTC),
                ended_at=(day2 + timedelta(minutes=25)).astimezone(UTC),
                run_state="awaiting_checkin",
                active_phase="ended",
                segment_index=1,
                work_elapsed_seconds=1500,
                rest_elapsed_seconds=0,
                actual_work_seconds=1500,
                actual_source="timer",
                outcome="unknown",
                checkin_timeout_at=(day2 + timedelta(minutes=30)).astimezone(UTC),
                settings_snapshot={"effective_revision": 1},
            )
            db.add(run)
            await db.flush()
            ci = CheckIn(
                id=uuid4(),
                user_id=uid,
                run_id=run.id,
                kind="final",
                response_state="open",
                session_outcome="unknown",
                asked_at=(day2 + timedelta(minutes=25)).astimezone(UTC),
                response_revision=0,
            )
            db.add(ci)
            await db.commit()

            await checkin_service.respond_to_checkin(
                db,
                user_id=str(uid),
                checkin_id=str(ci.id),
                idempotency_key=f"late-{suffix}",
                payload=CheckInResponseRequest(
                    expected_response_revision=0,
                    session_outcome="completed",
                    responded_at=(day2 + timedelta(minutes=26)).astimezone(UTC),
                ),
                now=(day2 + timedelta(minutes=26)).astimezone(UTC),
            )
            u_mid = await db.get(User, uid)
            assert u_mid is not None
            assert u_mid.current_streak_days == 3
            assert u_mid.streak_frozen is False
            return uid, run.id, ci.id

        async with sf() as db:
            # Biến thể 1: Undo check-in về skipped
            uid1, _, ci1 = await _setup_frozen_user_and_checkin(db, "undo")
            await checkin_service.respond_to_checkin(
                db,
                user_id=str(uid1),
                checkin_id=str(ci1),
                idempotency_key="undo-frozen-day",
                payload=CheckInResponseRequest(
                    expected_response_revision=1,
                    session_outcome="skipped",
                    skip_scope="skip_session",
                    responded_at=(day2 + timedelta(minutes=27)).astimezone(UTC),
                ),
                now=(day2 + timedelta(minutes=27)).astimezone(UTC),
            )
            u1 = await db.get(User, uid1)
            assert u1 is not None
            assert u1.current_streak_days == 3
            assert u1.last_streak_date == day2.date()
            assert u1.streak_frozen is True
            assert u1.streak_freeze_tokens == 1
            # Gọi lại get_streak_summary ở day3 không trừ đúp khiên
            s1 = await streak_service.get_streak_summary(
                db, user_id=str(uid1), commit_if_changed=True, now=day3.astimezone(UTC)
            )
            assert s1.current_streak_days == 3
            assert s1.streak_freeze_tokens == 1
            assert s1.streak_frozen is True

            # Biến thể 2: actual-corrections giảm xuống 60s (< 120s)
            uid2, run2_id, _ = await _setup_frozen_user_and_checkin(db, "corr")
            run2 = await db.get(SessionRun, run2_id)
            assert run2 is not None
            await run_service.record_actual_correction(
                db,
                user_id=str(uid2),
                run_id=str(run2_id),
                idempotency_key="corr-under-120",
                payload=ActualCorrectionRequest(
                    expected_run_revision=run2.revision,
                    self_report_work_seconds=60,
                    reason="Chạy nhầm đồng hồ",
                    occurred_at=(day2 + timedelta(minutes=28)).astimezone(UTC),
                ),
                now=(day2 + timedelta(minutes=28)).astimezone(UTC),
            )
            u2 = await db.get(User, uid2)
            assert u2 is not None
            assert u2.current_streak_days == 3
            assert u2.last_streak_date == day2.date()
            assert u2.streak_frozen is True
            assert u2.streak_freeze_tokens == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_04_settings_and_suggestion_concurrent_same_idempotency_key_replays_200():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=now)
            uid = base["user_id"]
            sugg_id = uuid4()
            db.add(
                AdaptationSuggestion(
                    id=sugg_id,
                    user_id=uid,
                    kind="buffer_multiplier",
                    sample_count=8,
                    current_value=1.2,
                    suggested_value=1.4,
                    reason="Lịch sử cần thêm đệm.",
                    policy_revision="v2",
                    sample_refs=["s1"],
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        # (1) apply_settings_change: request 1 commit cùng Idempotency-Key trước khi request 2 trượt replay đầu hàm
        orig_replay = command_service.replay
        call_count_set = 0

        async def _replay_slide_settings(db_arg, u_id, key, digest):
            nonlocal call_count_set
            if key == "h1-04-set-key":
                call_count_set += 1
                if call_count_set == 1:
                    # Giả lập trượt ở replay đầu hàm
                    return None
            return await orig_replay(db_arg, u_id, key, digest)

        async with sf() as db1, sf() as db2:
            req_payload = SettingsChangeRequest(
                expected_preferences_revision=1,
                changes={"preferences": {"reminder_style": "upbeat"}},  # type: ignore[arg-type]
            )
            res1 = await settings_service.apply_settings_change(
                user_id=str(uid),
                idempotency_key="h1-04-set-key",
                payload=req_payload,
                db=db1,
            )
            assert res1.effective_revision == 2

            with patch.object(command_service, "replay", side_effect=_replay_slide_settings):
                res2 = await settings_service.apply_settings_change(
                    user_id=str(uid),
                    idempotency_key="h1-04-set-key",
                    payload=req_payload,
                    db=db2,
                )
                assert res2.change_request_id == res1.change_request_id
                assert res2.effective_revision == 2

                # Cùng Idempotency-Key nhưng khác payload vẫn phải ném 409 IDEMPOTENCY_KEY_REUSED
                call_count_set = 0
                with pytest.raises(AppError) as exc_reuse:
                    await settings_service.apply_settings_change(
                        user_id=str(uid),
                        idempotency_key="h1-04-set-key",
                        payload=SettingsChangeRequest(
                            expected_preferences_revision=1,
                            changes={"preferences": {"reminder_style": "encouraging"}},  # type: ignore[arg-type]
                        ),
                        db=db2,
                    )
                assert exc_reuse.value.status_code == 409
                assert exc_reuse.value.code == "IDEMPOTENCY_KEY_REUSED"

        # (2) respond_to_suggestion: request 1 commit trước khi request 2 trượt replay đầu hàm
        call_count_sugg = 0

        async def _replay_slide_sugg(db_arg, u_id, key, digest):
            nonlocal call_count_sugg
            if key == "h1-04-sugg-key":
                call_count_sugg += 1
                if call_count_sugg == 1:
                    return None
            return await orig_replay(db_arg, u_id, key, digest)

        async with sf() as db1, sf() as db2:
            sugg_payload = SuggestionResponseRequest(
                expected_suggestion_revision=1,
                expected_preference_revision=2,
                action="accept",
            )
            s_res1 = await settings_service.respond_to_suggestion(
                user_id=str(uid),
                idempotency_key="h1-04-sugg-key",
                suggestion_id=str(sugg_id),
                payload=sugg_payload,
                db=db1,
            )
            assert s_res1.status == "accepted"

            with patch.object(command_service, "replay", side_effect=_replay_slide_sugg):
                s_res2 = await settings_service.respond_to_suggestion(
                    user_id=str(uid),
                    idempotency_key="h1-04-sugg-key",
                    suggestion_id=str(sugg_id),
                    payload=sugg_payload,
                    db=db2,
                )
                assert s_res2.change_request_id == s_res1.change_request_id
                assert s_res2.accepted_value == s_res1.accepted_value
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_05_respond_suggestion_immediate_supersedes_other_pending_buffer_suggestions():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=now)
            uid = base["user_id"]
            sugg_a = uuid4()
            sugg_b = uuid4()
            db.add_all(
                [
                    AdaptationSuggestion(
                        id=sugg_a,
                        user_id=uid,
                        kind="buffer_multiplier",
                        sample_count=8,
                        current_value=1.2,
                        suggested_value=1.4,
                        reason="Gợi ý A",
                        policy_revision="v2",
                        sample_refs=["a1"],
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    ),
                    AdaptationSuggestion(
                        id=sugg_b,
                        user_id=uid,
                        kind="buffer_multiplier",
                        sample_count=7,
                        current_value=1.2,
                        suggested_value=1.35,
                        reason="Gợi ý B",
                        policy_revision="v2",
                        sample_refs=["b1"],
                        status="pending",
                        created_at=now - timedelta(days=1),
                        updated_at=now - timedelta(days=1),
                    ),
                ]
            )
            await db.commit()

            res = await settings_service.respond_to_suggestion(
                user_id=str(uid),
                idempotency_key="h1-05-accept-a",
                suggestion_id=str(sugg_a),
                payload=SuggestionResponseRequest(
                    expected_suggestion_revision=1,
                    expected_preference_revision=1,
                    action="accept",
                ),
                db=db,
            )
            assert res.application_state == "effective_from_next_session"
            row_a = await db.get(AdaptationSuggestion, sugg_a)
            row_b = await db.get(AdaptationSuggestion, sugg_b)
            assert row_a is not None and row_a.status == "accepted"
            assert row_b is not None and row_b.status == "superseded"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_06_concurrent_get_current_and_detail_proposal_no_stale_data_or_sqlite_lock_error(
    tmp_path: Path,
):
    db_file = tmp_path / "h1_06_concurrent_get.db"
    engine, sf = await _make_env(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=now)
            uid = base["user_id"]
            t = await _seed_task_step_session(db, uid=uid, now=now, start=now + timedelta(hours=3))
            prop = _make_proposal_row(
                uid=uid,
                now=now,
                target_task_id=t["task_id"],
                proposed_changes={
                    "steps_to_add": [],
                    "sessions_to_cancel": [],
                    "sessions_to_move": [],
                    "sessions_to_add": [],
                },
                pending_notification_state="not_sent",
            )
            db.add(prop)
            await db.commit()
            prop_id = str(prop.id)

        headers = _auth_headers(uid)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = await asyncio.gather(
                client.get("/api/v1/proposals/current", headers=headers),
                client.get(f"/api/v1/proposals/{prop_id}", headers=headers),
                client.get("/api/v1/proposals/current", headers=headers),
                client.get(f"/api/v1/proposals/{prop_id}", headers=headers),
            )
        for resp in responses:
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["proposal_id"] == prop_id
            assert data["pending_notification_state"] == "sent"

        async with sf() as db:
            refreshed = await db.get(RescheduleProposal, UUID(prop_id))
            assert refreshed is not None
            assert refreshed.pending_notification_state == "sent"
            assert refreshed.pending_notification_sent_at is not None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h1_07_approve_uf7_reminder_offsets_regenerates_untouched_future_sessions():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            base = await _seed_user_base(db, now=now)
            uid = base["user_id"]
            t_a = await _seed_task_step_session(
                db, uid=uid, now=now, start=now + timedelta(hours=4), title="Task A Moved"
            )
            t_b = await _seed_task_step_session(
                db,
                uid=uid,
                now=now,
                start=now + timedelta(days=2, hours=2),
                title="Task B Untouched",
                add_schedule_version=False,
            )
            await db.commit()

        with patch.object(
            reschedule_service.ai_service,
            "generate_reschedule_empathy",
            return_value=RescheduleEmpatheticResult(
                empathetic_note="Đã cập nhật nhịp tập trung và nhắc nhở mới.",
                encouragement_tip="Bắt đầu từng phiên thật nhẹ nhàng.",
                generation_tier="tier_1_gemini",
            ),
        ):
            async with sf() as db:
                res = await settings_service.apply_settings_change(
                    user_id=str(uid),
                    idempotency_key="h1-07-change-req",
                    payload=SettingsChangeRequest(
                        expected_preferences_revision=1,
                        changes={
                            "preferences": {
                                "work_interval_seconds": 1800,
                                "reminder_offsets": [600],
                            }
                        },  # type: ignore[arg-type]
                    ),
                    db=db,
                )
                assert res.application_state == "pending_review"
                assert res.pending_proposal_id is not None
                prop_id = res.pending_proposal_id

            async with sf() as db:
                prop = await db.get(RescheduleProposal, UUID(prop_id))
                assert prop is not None
                # Giả lập proposal chỉ chạm Task A (còn Task B untouched)
                raw_changes = dict(prop.proposed_changes or {})
                raw_changes["sessions_to_move"] = []
                raw_changes["sessions_to_add"] = []
                raw_changes["sessions_to_cancel"] = []
                prop.proposed_changes = raw_changes
                await db.commit()

                await reschedule_service.approve_proposal(
                    db,
                    user_id=str(uid),
                    proposal_id=prop_id,
                    idempotency_key="h1-07-approve",
                    payload=ApproveProposalRequest(
                        expected_proposal_revision=prop.proposal_revision,
                        expected_base_schedule_revision=prop.base_schedule_revision,
                        expected_source_revisions=ProposalSourceRevisions.model_validate(
                            prop.source_revisions
                        ),
                    ),
                    now=now + timedelta(minutes=5),
                )

                # Kiểm tra Task B (untouched) đã hủy reminder 900 cũ và có reminder 600 mới gắn :pref:2
                rems_b = (
                    await db.scalars(
                        select(Reminder).where(
                            Reminder.user_id == uid,
                            Reminder.session_id == t_b["session_id"],
                        )
                    )
                ).all()
                cancelled_b = [r for r in rems_b if r.delivery_state == "cancelled"]
                active_b = [r for r in rems_b if r.delivery_state == "scheduled"]
                assert len(cancelled_b) == 1
                assert len(active_b) == 1
                assert ":reminder:600:pref:2" in active_b[0].dedupe_key

                # Task A cũng có reminder 600 mới nếu chưa được tái tạo ở nhánh move/add
                rems_a = (
                    await db.scalars(
                        select(Reminder).where(
                            Reminder.user_id == uid,
                            Reminder.session_id == t_a["session_id"],
                            Reminder.delivery_state == "scheduled",
                        )
                    )
                ).all()
                assert len(rems_a) == 1
                assert ":reminder:600:pref:2" in rems_a[0].dedupe_key
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def test_h1_08_w4_02_settings_fixture_and_schema_match_be_uf7_01_02():
    root = Path(__file__).resolve().parents[2]
    fixture_path = root / "docs" / "backend" / "fixtures" / "w4-02" / "02_settings.json"
    schema_path = root / "docs" / "backend" / "schema.md"

    fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases_by_name = {c["name"]: c for c in fixture_data["cases"]}
    assert "suggestion_accept" in cases_by_name
    assert cases_by_name["suggestion_accept"]["response"]["body"]["data"]["suggestion_revision"] == 1

    schema_text = schema_path.read_text(encoding="utf-8")
    assert "pending_change_request_id" in schema_text
    assert "pending_change_request_revision" in schema_text
    assert "BE-UF7-01" in schema_text
    assert "BE-UF7-02" in schema_text
