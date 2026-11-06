"""Persistence checks without external services."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateTable

from app.models import Base, Session, Step, Task, User, UserPreference


def make_plan(db):
    user = User(email="tn@example.test", display_name="TN")
    db.add(user)
    db.flush()
    pref = UserPreference(user_id=user.id)
    task = Task(user_id=user.id, title="Báo cáo", time_constraint={"kind": "window"})
    db.add_all([pref, task])
    db.flush()
    step = Step(
        user_id=user.id,
        task_id=task.id,
        title="Tìm tài liệu",
        order_index=0,
        estimated_base_seconds=3000,
        remaining_base_seconds=3000,
        buffer_multiplier=1.2,
        estimate_source="user_confirmed",
    )
    db.add(step)
    db.flush()
    start = datetime(2026, 10, 30, 9, tzinfo=UTC)
    session = Session(
        user_id=user.id,
        step_id=step.id,
        scheduled_start=start,
        scheduled_end=start + timedelta(seconds=4200),
        work_seconds=3600,
        rest_seconds=600,
        duration_seconds=4200,
        segments=[
            {"order_index": 0, "kind": "work", "duration_seconds": 1500},
            {"order_index": 1, "kind": "rest", "duration_seconds": 300},
            {"order_index": 2, "kind": "work", "duration_seconds": 1500},
            {"order_index": 3, "kind": "rest", "duration_seconds": 300},
            {"order_index": 4, "kind": "work", "duration_seconds": 600},
        ],
        schedule_revision=1,
    )
    db.add(session)
    db.commit()
    return user, pref, task, step, session


def test_csv_values_and_roundtrip(engine):
    with DBSession(engine) as db:
        user, pref, task, step, session = make_plan(db)
        db.expire_all()
        assert pref.micro_task_seconds == 120
        assert pref.requested_revision is None
        assert pref.reminder_offsets == [900, 600, 300]
        assert pref.work_interval_seconds == 1500
        assert pref.buffer_multiplier == 1.2
        assert task.status == "pending"
        assert task.importance is None
        assert step.order_index == 0
        assert session.micro_task_seconds is None
        assert session.scheduled_start.tzinfo == UTC
        assert session.segments[0]["kind"] == "work"
        assert session.status == "scheduled"
        assert {
            "users",
            "user_preferences",
            "tasks",
            "steps",
            "sessions",
        } <= Base.metadata.tables.keys()


def test_foreign_key_and_no_cascade_delete(engine):
    with DBSession(engine) as db:
        user, _, task, _, _ = make_plan(db)
        db.delete(task)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(select(Task.id)) == task.id
        db.add(UserPreference(user_id=uuid4()))
        with pytest.raises(IntegrityError):
            db.commit()


def test_one_preference_per_user(engine):
    with DBSession(engine) as db:
        user, *_ = make_plan(db)
        db.add(UserPreference(user_id=user.id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_stale_write_rejected(engine):
    with DBSession(engine) as setup:
        _, _, task, _, _ = make_plan(setup)
        task_id = task.id
    with DBSession(engine) as first, DBSession(engine) as second:
        a, b = first.get(Task, task_id), second.get(Task, task_id)
        assert a is not None and b is not None
        a.title = "Bản mới"
        first.commit()
        b.title = "Bản cũ"
        with pytest.raises(StaleDataError):
            second.commit()


@pytest.mark.parametrize(
    "field,value", [("duration_seconds", 1), ("rest_seconds", -1), ("schedule_revision", 0)]
)
def test_invalid_session_rejected(engine, field, value):
    with DBSession(engine) as db:
        *_, session = make_plan(db)
        setattr(session, field, value)
        with pytest.raises(IntegrityError):
            db.commit()


def test_naive_timestamp_rejected(engine):
    with DBSession(engine) as db:
        *_, session = make_plan(db)
        session.scheduled_start = datetime(2026, 10, 30, 9)
        with pytest.raises(StatementError):
            db.commit()


def test_postgresql_ddl_compiles():
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=postgresql.dialect()))
