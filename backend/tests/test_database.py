"""Integration coverage for ownership, workflow storage and migration parity."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, event, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as DBSession

from app.core.database import make_engine, session_factory
from app.models import (
    Base,
    CheckIn,
    CommandReceipt,
    PlanningDraft,
    RescheduleProposal,
    RunEvent,
    SessionRun,
    Step,
    Task,
    Thought,
    User,
    UserPreference,
)
from app.services import command_service
from app.services.plan_service import get_command_result
from app.services.task_state_service import get_task_planning_states
from tests.test_models import make_plan


def run_for(session, user_id, **kwargs):
    return SessionRun(
        user_id=user_id,
        session_id=session.id,
        device_id="device-1",
        started_at=session.scheduled_start,
        settings_snapshot={"session": {}},
        **kwargs,
    )


def test_cross_user_step_and_preference_proposal_rejected(engine):
    with DBSession(engine) as db:
        user, _, task, step, _ = make_plan(db)
        other = User(email="other@test.example", display_name="Other")
        db.add(other)
        db.commit()
        step.user_id = other.id
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert step.user_id == user.id


def test_pending_proposal_keeps_official_schedule_and_is_unique(engine):
    with DBSession(engine) as db:
        user, _, _, _, session = make_plan(db)
        original = session.scheduled_start

        def proposal():
            return RescheduleProposal(
                user_id=user.id,
                trigger_reason="reschedule",
                base_schedule_revision=1,
                allowed_task_ids=[],
                affected_task_ids=[],
                source_revisions={},
                proposed_changes={"sessions": []},
                feasibility={"status": "insufficient_data"},
                explanation="Chờ duyệt",
            )

        db.add(proposal())
        db.commit()
        assert session.scheduled_start == original
        db.add(proposal())
        with pytest.raises(IntegrityError):
            db.commit()


def test_run_ended_does_not_complete_task_and_late_checkin_same_row(engine):
    with DBSession(engine) as db:
        user, _, task, step, session = make_plan(db)
        run = run_for(
            session,
            user.id,
            ended_at=session.scheduled_end,
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            work_elapsed_seconds=3600,
            actual_work_seconds=3600,
            actual_source="timer_proxy",
        )
        db.add(run)
        db.flush()
        checkin = CheckIn(
            user_id=user.id, run_id=run.id, kind="final", asked_at=session.scheduled_end
        )
        db.add(checkin)
        db.commit()
        assert run.outcome == "unknown" and checkin.step_completed is None
        assert task.status == "pending" and step.status == "pending"
        checkin.response_state = "no_respond"
        db.commit()
        checkin.response_state = "answered"
        checkin.response_revision = 1
        checkin.session_outcome = "partial"
        db.commit()
        assert db.scalars(select(CheckIn)).all() == [checkin]
        assert run.actual_work_seconds == 3600
        assert run.ended_at == session.scheduled_end


@pytest.mark.parametrize("run_state", ["running", "paused"])
def test_duplicate_active_run_and_final_checkin_rejected(engine, run_state):
    with DBSession(engine) as db:
        user, _, _, _, session = make_plan(db)
        run = run_for(session, user.id, run_state=run_state)
        db.add(run)
        db.commit()
        db.add(run_for(session, user.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(
            CheckIn(user_id=user.id, run_id=run.id, kind="final", asked_at=session.scheduled_end)
        )
        db.commit()
        db.add(
            CheckIn(user_id=user.id, run_id=run.id, kind="final", asked_at=session.scheduled_end)
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_thought_draft_task_forward_references(engine):
    with DBSession(engine) as db:
        user, _, _, _, session = make_plan(db)
        run = run_for(session, user.id)
        db.add(run)
        db.flush()
        thought = Thought(
            user_id=user.id,
            source_run_id=run.id,
            client_thought_id="client-1",
            input_type="audio",
            capture_state="saved",
            attempt_count=3,
        )
        db.add(thought)
        db.flush()
        draft = PlanningDraft(
            user_id=user.id,
            source_thought_id=thought.id,
            client_draft_id="draft-1",
            input_source="thought_conversion",
        )
        db.add(draft)
        db.flush()
        task = Task(user_id=user.id, source_draft_id=draft.id, title="Việc mới", time_constraint={})
        db.add(task)
        db.commit()
        assert draft.source_thought_id == thought.id and task.source_draft_id == draft.id
        assert "converted_task_id" not in Thought.__table__.c
        assert "confirmed_task_id" not in PlanningDraft.__table__.c
        thought.attempt_count = 4
        with pytest.raises(IntegrityError):
            db.commit()


def test_command_id_is_scoped_and_deduplicated(engine):
    with DBSession(engine) as db:
        user, *_ = make_plan(db)

        def receipt():
            return CommandReceipt(
                user_id=user.id,
                command_id="same-command",
                command_type="confirm_candidate",
                payload_hash="a" * 64,
                state="applied",
            )

        db.add(receipt())
        db.commit()
        db.add(receipt())
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.asyncio
async def test_batched_task_derived_states_and_owner_filter():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine) as db:
            user = User(email="states@test.example", display_name="TN")
            db.add(user)
            await db.flush()
            task = Task(
                user_id=user.id, title="Task", time_constraint={}, decomposition_state="not_started"
            )
            db.add(task)
            await db.flush()
            task_id, user_id = task.id, user.id
            result = await get_task_planning_states(db, user_id, [task_id])
            assert result[task_id].decomposition_state == "not_started"
            first = Step(
                user_id=user_id,
                task_id=task_id,
                title="Đầu",
                order_index=0,
                estimated_base_seconds=1500,
                remaining_base_seconds=0,
                buffer_multiplier=1.0,
                estimate_source="user_confirmed",
            )
            second = Step(
                user_id=user_id,
                task_id=task_id,
                title="Sau",
                order_index=1,
                estimated_base_seconds=1500,
                remaining_base_seconds=0,
                buffer_multiplier=1.0,
                estimate_source="user_confirmed",
            )
            task.decomposition_state = "partial"
            task.unexpanded_scope = "Phần còn lại chưa chia"
            db.add_all([first, second])
            await db.flush()
            result = await get_task_planning_states(db, user_id, [task_id])
            assert result[task_id].decomposition_state == "partial"
            assert result[task_id].scheduling_state == "not_scheduled"
            task.decomposition_state = "complete"
            task.unexpanded_scope = None
            await db.flush()
            result = await get_task_planning_states(db, user_id, [task_id])
            assert result[task_id].decomposition_state == "complete"
            assert result[task_id].scheduling_state == "complete"
            assert await get_task_planning_states(db, uuid4(), [task_id]) == {}
            assert "decomposition_state" in Task.__table__.c
            assert "planning_parts" not in Base.metadata.tables
    finally:
        await engine.dispose()


def test_sqlite_migration_preserves_legacy_and_requires_reviewed_downgrade(tmp_path):
    url = "sqlite+aiosqlite:///" + (tmp_path / "migration.db").as_posix()
    env = {**os.environ, "DATABASE_URL": url}
    cwd = Path(__file__).resolve().parents[1]

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    alembic("upgrade", "head")
    engine = create_engine(url.replace("+aiosqlite", ""))
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version",
            "planning_parts",
        }
        assert {c["name"] for c in inspect(connection).get_columns("tasks")} >= {
            "availability",
            "decomposition_state",
            "unexpanded_scope",
            "source_summary_revision",
            "source_planning_input_revision",
        }
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with DBSession(engine) as db:
        _, _, task, step, session = make_plan(db)
        assert step.task_id == task.id
        assert session.step_id == step.id
    engine.dispose()
    # C01 chủ động giữ cột/bảng legacy và từ chối downgrade mất nguồn gốc.
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert downgrade.returncode != 0
    assert "C01 downgrade requires an explicit snapshot/data recovery plan" in downgrade.stderr
    alembic("upgrade", "head")


def test_migrated_thought_link_enforces_owner_and_concurrent_uniqueness(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = create_engine(url.replace("+aiosqlite", ""))

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        with DBSession(engine) as db:
            user, _, _, _, session = make_plan(db)
            run = run_for(
                session,
                user.id,
                ended_at=session.scheduled_end,
                run_state="awaiting_checkin",
                active_phase="ended",
                segment_index=None,
            )
            other = User(email="other-owner@test.example", display_name="Other")
            db.add_all([run, other])
            db.flush()
            thought = Thought(
                user_id=user.id,
                source_run_id=run.id,
                client_thought_id="thought-race",
                input_type="audio",
                capture_state="saved",
                attempt_count=1,
            )
            db.add(thought)
            db.commit()
            user_id, other_id, thought_id = user.id, other.id, thought.id
            db.add(
                PlanningDraft(
                    user_id=other_id,
                    client_draft_id="wrong-owner",
                    source_thought_id=thought_id,
                    input_source="thought_conversion",
                )
            )
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                db.commit()
            db.rollback()

        ready = Barrier(2, timeout=5)

        def insert_draft(client_id):
            with DBSession(engine) as db:
                draft = PlanningDraft(
                    user_id=user_id,
                    client_draft_id=client_id,
                    source_thought_id=thought_id,
                    input_source="thought_conversion",
                )
                db.add(draft)
                ready.wait()
                try:
                    db.commit()
                except IntegrityError as error:
                    db.rollback()
                    assert "planning_drafts.source_thought_id" in str(error.orig)
                    return None
                return draft.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(insert_draft, ["attempt-a", "attempt-b"]))
        winner_ids = [draft_id for draft_id in results if draft_id is not None]
        with DBSession(engine) as db:
            assert len(winner_ids) == 1
            assert db.scalars(select(PlanningDraft.id)).all() == winner_ids
    finally:
        engine.dispose()


def test_reminder_offsets_storage_preserves_existing_preferences(engine):
    with DBSession(engine) as db:
        existing, pref, *_ = make_plan(db)
        pref.reminder_offsets = [300]
        db.commit()
        new = User(email="new-reminders@test.example", display_name="New")
        db.add(new)
        db.flush()
        db.add(UserPreference(user_id=new.id, reminder_offsets=[900, 600, 300]))
        db.commit()
        existing_id, new_id = existing.id, new.id

    with DBSession(engine) as db:
        offsets = {
            owner: values
            for owner, values in db.execute(
                select(UserPreference.user_id, UserPreference.reminder_offsets)
            ).all()
        }
        assert offsets == {existing_id: [300], new_id: [900, 600, 300]}


@pytest.mark.asyncio
async def test_run_end_event_checkin_and_receipt_survive_reopen(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "head")

    class EndResult(BaseModel):
        run_id: str
        checkin_id: str

    def seed_run(db):
        user, _, _, _, session = make_plan(db)
        run = run_for(session, user.id)
        db.add(run)
        db.flush()
        db.add(
            RunEvent(
                user_id=user.id,
                run_id=run.id,
                client_event_id="start-event",
                event_type="start",
                occurred_at=run.started_at,
                payload={},
            )
        )
        db.commit()
        return user.id, run.id, session.scheduled_end

    engine = make_engine(url)
    try:
        sessions = session_factory(engine)
        async with sessions() as db:
            user_id, run_id, ended_at = await db.run_sync(seed_run)

        owner = str(user_id)
        key = "end-command"
        digest = command_service.command_hash("end_run", str(run_id), {"event_id": "end-event"})

        async def stage_end(db, event_id):
            run = await db.get(SessionRun, run_id)
            assert run is not None
            run.ended_at = ended_at
            run.run_state = "awaiting_checkin"
            run.active_phase = "ended"
            run.segment_index = None
            run.work_elapsed_seconds = 3600
            run.actual_work_seconds = 3600
            run.actual_source = "timer_proxy"
            run.checkin_timeout_at = ended_at + timedelta(seconds=300)
            run.dnd_release_at = run.checkin_timeout_at
            checkin = CheckIn(user_id=user_id, run_id=run_id, kind="final", asked_at=ended_at)
            db.add(checkin)
            await db.flush()
            result = EndResult(run_id=str(run_id), checkin_id=str(checkin.id))
            command_service.record(db, owner, key, digest, "end_run", result)
            await db.flush()
            db.add(
                RunEvent(
                    user_id=user_id,
                    run_id=run_id,
                    client_event_id=event_id,
                    event_type="end",
                    occurred_at=ended_at,
                    payload={},
                )
            )
            return result

        async with sessions() as db:
            await stage_end(db, "start-event")
            with pytest.raises(IntegrityError, match="run_events.client_event_id"):
                await db.commit()
            await db.rollback()

        async with sessions() as db:
            run = await db.get(SessionRun, run_id)
            assert run is not None and run.run_state == "running" and run.ended_at is None
            assert await db.scalar(select(CheckIn.id)) is None
            assert await command_service.replay(db, owner, key, digest) is None
            result = await stage_end(db, "end-event")
            await db.commit()
    finally:
        await engine.dispose()

    reopened = make_engine(url)
    try:
        async with session_factory(reopened)() as db:
            assert await command_service.replay(db, owner, key, digest) == result.model_dump()
            receipt = await get_command_result(user_id=owner, command_id=key, db=db)
            assert receipt.result_reference == result.model_dump()
            run = await db.get(SessionRun, run_id)
            assert run is not None and run.ended_at == ended_at
            assert run.actual_work_seconds == 3600 and run.outcome == "unknown"
            assert run.checkin_timeout_at == ended_at + timedelta(seconds=300)
            assert [str(value) for value in await db.scalars(select(CheckIn.id))] == [
                result.checkin_id
            ]
            assert set(await db.scalars(select(RunEvent.client_event_id))) == {
                "start-event",
                "end-event",
            }
    finally:
        await reopened.dispose()


def test_postgresql_offline_migration_contains_deferred_fks():
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://unused:unused@localhost/test"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ALTER TABLE planning_drafts ADD CONSTRAINT fk_draft_source_thought" in result.stdout
    assert "CREATE UNIQUE INDEX uq_user_live_run" in result.stdout
    assert "DO $c01$" in result.stdout
    assert "client_step_keys" in result.stdout
    assert "USING to_json(resource_ref)" in result.stdout


def test_focus_defaults_upgrade_preserves_existing_preferences(sqlite_migration):
    url, migrate = sqlite_migration
    migrate("upgrade", "20261106_01")
    engine = create_engine(url.replace("+aiosqlite", ""))
    with DBSession(engine) as db:
        user = User(email="legacy-defaults@example.test", display_name="Legacy")
        db.add(user)
        db.flush()
        db.add(UserPreference(user_id=user.id, reminder_offsets=[300], micro_task_seconds=45))
        db.commit()
        owner = user.id
        db.execute(update(UserPreference).values(micro_task_seconds=None))
        db.commit()
    migrate("upgrade", "head")
    with DBSession(engine) as db:
        pref = db.scalar(select(UserPreference).where(UserPreference.user_id == owner))
        assert pref is not None
        assert pref.micro_task_seconds == 120
        assert pref.reminder_offsets == [300]
    engine.dispose()


def test_step_cannot_reference_task_of_another_user(engine):
    with DBSession(engine) as db:
        user, _, task, step, _ = make_plan(db)
        other_user = User(email="other-owner@example.test", display_name="Other")
        db.add(other_user)
        db.flush()
        other = Task(user_id=other_user.id, title="Other task", time_constraint={})
        db.add(other)
        db.flush()
        step.task_id = other.id
        with pytest.raises(IntegrityError):
            db.commit()


def test_calendar_outbox_rollback(engine):
    from app.models import CalendarConnection, CalendarEventLink, CalendarSyncJob

    with DBSession(engine) as db:
        user, _, _, _, session = make_plan(db)
        connection = CalendarConnection(
            user_id=user.id,
            account_ref="test@example.com",
            read_calendar_ids=["primary"],
            write_calendar_id="primary",
            granted_scopes=[],
            connection_state="connected",
            credential_ref="vault:test",
        )
        db.add(connection)
        db.flush()
        link = CalendarEventLink(
            user_id=user.id,
            session_id=session.id,
            connection_id=connection.id,
            google_calendar_id="primary",
            desired_schedule_revision=1,
        )
        db.add(link)
        db.flush()
        link_id, connection_id, user_id = link.id, connection.id, user.id
        job = CalendarSyncJob(
            user_id=user_id,
            connection_id=connection_id,
            event_link_id=link_id,
            target_kind="session",
            target_ref=str(link_id),
            operation="create",
            target_schedule_revision=1,
            payload_snapshot={"summary": "Focus"},
            idempotency_key="job-1",
        )
        db.add(job)
        db.flush()
        db.rollback()
        assert db.scalar(select(CalendarSyncJob.id)) is None
        assert db.scalar(select(CalendarEventLink.id)) is None
        # Cả lịch liên kết lẫn job đều phải cùng transaction do service điều phối.


def test_json_suggestion_supports_array_and_context_unknown(engine):
    from app.models import AdaptationSuggestion

    with DBSession(engine) as db:
        user, *_ = make_plan(db)
        suggestion = AdaptationSuggestion(
            user_id=user.id,
            kind="reminder_offset",
            sample_count=0,
            current_value=[900],
            suggested_value=[300],
            reason="Nhắc trước 5 phút",
            policy_revision="demo-policy",
            sample_refs=[],
        )
        db.add(suggestion)
        db.commit()
        db.expire_all()
        assert suggestion.current_value == [900] and suggestion.suggested_value == [300]
