"""Kiểm hợp đồng và số truy vấn của list_tasks bằng SQLite riêng, không gọi provider."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update

from app.core.database import make_engine, session_factory
from app.core.errors import AppError
from app.models import (
    Base,
    CalendarConnection,
    CalendarEventLink,
    PlanningDraft,
    Session,
    Step,
    Task,
    User,
)
from app.schemas.common import TimeConstraint
from app.services import draft_service, plan_service

ANCHOR = datetime(2099, 1, 1, tzinfo=UTC)


@pytest.fixture
async def data():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    factory = session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    owners, task_ids, draft_ids = [], [], []
    async with factory() as db:
        for owner_index, count in enumerate((20, 1)):
            user = User(email=f"batch-{owner_index}@example.test", display_name="Batch")
            db.add(user)
            await db.flush()
            owners.append(user.id)
            calendar = CalendarConnection(
                user_id=user.id,
                account_ref=f"batch-{owner_index}@example.test",
                read_calendar_ids=["primary"],
                write_calendar_id="primary",
                granted_scopes=[],
                connection_state="connected",
                credential_ref="fake:test",
            )
            db.add(calendar)
            await db.flush()
            for index in range(count):
                public_id = f"public-draft-{owner_index}-{index}"
                dto = draft_service._build_seed_draft(public_id, public_id)
                dto.summary.time_constraint = TimeConstraint(
                    kind="window",
                    start_at=ANCHOR,
                    finish_by_at=ANCHOR + timedelta(days=90),
                    input_timezone="UTC",
                )
                draft = PlanningDraft(
                    user_id=user.id,
                    client_draft_id=public_id,
                    input_source="text",
                    summary=draft_service._serialize_summary_payload(dto),
                )
                db.add(draft)
                await db.flush()
                task = Task(
                    user_id=user.id,
                    title=f"Task {index}",
                    description=dto.summary.description,
                    scope=dto.summary.scope,
                    extra_requirements=dto.summary.extra_requirements,
                    time_constraint=dto.summary.time_constraint.model_dump(mode="json"),
                    source_draft_id=draft.id,
                    source_summary=dto.summary.model_dump(mode="json"),
                    source_summary_revision=5,
                    source_planning_input_revision=5,
                    decomposition_state="partial" if index % 2 else "complete",
                    unexpanded_scope="Phần chưa chia" if index % 2 else None,
                    created_at=ANCHOR + timedelta(minutes=index),
                )
                db.add(task)
                await db.flush()
                if owner_index == 0:
                    task_ids.append(task.id)
                    draft_ids.append(draft.id)
                for order in (1, 0):
                    step = Step(
                        user_id=user.id,
                        task_id=task.id,
                        title=f"Step {order}",
                        expected_output="Có bản nháp",
                        order_index=order,
                        estimated_base_seconds=1500,
                        remaining_base_seconds=1500,
                        buffer_multiplier=1.2,
                        estimate_source="user_confirmed",
                    )
                    db.add(step)
                    await db.flush()
                    start = ANCHOR + timedelta(days=index, hours=order)
                    session = Session(
                        user_id=user.id,
                        step_id=step.id,
                        scheduled_start=start,
                        scheduled_end=start + timedelta(seconds=2100),
                        work_seconds=1800,
                        rest_seconds=300,
                        duration_seconds=2100,
                        segments=[
                            {"order_index": 0, "kind": "work", "duration_seconds": 1800},
                            {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                        ],
                        schedule_revision=1,
                        status="cancelled" if index == 2 else "scheduled",
                    )
                    db.add(session)
                    await db.flush()
                    for current in (False, True):
                        db.add(
                            CalendarEventLink(
                                user_id=user.id,
                                session_id=session.id,
                                connection_id=calendar.id,
                                google_calendar_id="primary",
                                google_event_id=f"{session.id}-{current}",
                                desired_schedule_revision=1,
                                is_current=current,
                            )
                        )
        other_draft = PlanningDraft(
            user_id=owners[1], client_draft_id="unlinked-other-owner", input_source="text"
        )
        db.add(other_draft)
        await db.flush()
        other_draft_id = other_draft.id
        await db.commit()
    yield SimpleNamespace(
        engine=engine,
        factory=factory,
        owner=owners[0],
        other=owners[1],
        task_ids=task_ids,
        draft_ids=draft_ids,
        other_draft_id=other_draft_id,
    )
    await engine.dispose()


async def test_results_match_detail_and_preserve_owner_source_states_and_order(data):
    async with data.factory() as db:
        result = await plan_service.list_tasks(user_id=str(data.owner), db=db, limit=20)
        expected = [
            await plan_service.get_task_by_id(user_id=str(data.owner), task_id=str(key), db=db)
            for key in data.task_ids
        ]
        assert [item.model_dump() for item in result.items] == [
            item.model_dump() for item in expected
        ]
        assert [item.task_id for item in result.items] == list(map(str, data.task_ids))
        assert result.items[0].source_draft_id == "public-draft-0-0"
        assert result.items[0].scheduling_state == "complete"
        assert result.items[1].decomposition_state == "partial"
        assert result.items[2].scheduling_state == "not_scheduled"
        assert [step.order_index for step in result.items[0].steps] == [0, 1]
        current_links = set(
            map(
                str,
                await db.scalars(
                    select(CalendarEventLink.id).where(
                        CalendarEventLink.user_id == data.owner,
                        CalendarEventLink.is_current.is_(True),
                    )
                ),
            )
        )
        assert all(
            session.calendar_event_link_id in current_links
            for task in result.items
            for session in task.sessions
        )


async def test_query_count_stays_constant_when_page_grows(data):
    queries = []

    def count(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(data.engine.sync_engine, "before_cursor_execute", count)
    try:
        measured = []
        for size in (1, 10, 20):
            async with data.factory() as db:
                queries.clear()
                result = await plan_service.list_tasks(user_id=str(data.owner), db=db, limit=size)
                assert len(result.items) == size
                measured.append(len(queries))
        assert measured == [8, 8, 8], measured
        print(f"list_tasks SELECT count for pages 1/10/20: {measured}")
    finally:
        event.remove(data.engine.sync_engine, "before_cursor_execute", count)


async def test_pagination_and_empty_page(data):
    seen, cursor = [], None
    async with data.factory() as db:
        while True:
            result = await plan_service.list_tasks(
                user_id=str(data.owner), db=db, limit=3, cursor=cursor
            )
            seen.extend(item.task_id for item in result.items)
            cursor = result.next_cursor
            if cursor is None:
                break
        assert seen == list(map(str, data.task_ids))
        empty = await plan_service.list_tasks(
            user_id=str(data.owner), db=db, cursor=str(data.task_ids[-1])
        )
        assert empty.items == [] and empty.next_cursor is None
        empty_owner = await plan_service.list_tasks(user_id=str(uuid4()), db=db)
        assert empty_owner.items == [] and empty_owner.next_cursor is None


async def test_cursor_from_other_owner_is_rejected(data):
    async with data.factory() as db:
        other = await plan_service.list_tasks(user_id=str(data.other), db=db)
        with pytest.raises(AppError) as caught:
            await plan_service.list_tasks(
                user_id=str(data.owner), db=db, cursor=other.items[0].task_id
            )
        assert caught.value.status_code == 422


@pytest.mark.parametrize("field", ["source_draft_id", "source_summary"])
async def test_missing_source_keeps_409(data, field):
    async with data.factory() as db:
        await db.execute(update(Task).where(Task.id == data.task_ids[0]).values({field: None}))
        await db.commit()
        with pytest.raises(AppError) as caught:
            await plan_service.list_tasks(user_id=str(data.owner), db=db)
        assert caught.value.status_code == 409 and caught.value.code == "SOURCE_CHANGED"


@pytest.mark.parametrize("source", ["missing", "other_owner"])
async def test_unavailable_linked_draft_keeps_404(data, source):
    # Mô phỏng dữ liệu legacy hỏng; FK của DB ứng dụng mới vẫn được giữ nguyên.
    async with data.engine.connect() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if source == "missing":
            await connection.execute(
                PlanningDraft.__table__.delete().where(PlanningDraft.id == data.draft_ids[0])
            )
        else:
            await connection.execute(
                update(Task)
                .where(Task.id == data.task_ids[0])
                .values(source_draft_id=data.other_draft_id)
            )
        await connection.commit()
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        await connection.commit()
    async with data.factory() as db:
        with pytest.raises(AppError) as caught:
            await plan_service.list_tasks(user_id=str(data.owner), db=db)
        assert caught.value.status_code == 404 and caught.value.code == "RESOURCE_NOT_FOUND"


async def test_task_identity_map_is_refreshed(data):
    async with data.factory() as db:
        cached = await db.get(Task, data.task_ids[0])
        async with data.factory() as writer:
            await writer.execute(
                update(Task).where(Task.id == cached.id).values(title="Tên mới từ session khác")
            )
            await writer.commit()
        result = await plan_service.list_tasks(user_id=str(data.owner), db=db, limit=1)
        assert result.items[0].title == "Tên mới từ session khác"
