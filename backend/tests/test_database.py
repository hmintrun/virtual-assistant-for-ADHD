"""Integration coverage for ownership, workflow storage and migration parity."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.database import make_engine
from app.models import (
    Base,
    CheckIn,
    CommandReceipt,
    PlanningDraft,
    RescheduleProposal,
    SessionRun,
    Step,
    Task,
    Thought,
    User,
)
from app.services.task_state_service import get_task_planning_states
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as DBSession
from test_models import make_plan


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


def test_duplicate_active_run_and_final_checkin_rejected(engine):
    with DBSession(engine) as db:
        user, _, _, _, session = make_plan(db)
        run = run_for(session, user.id)
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
