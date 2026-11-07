"""W2 persistence evidence on migrated SQLite; service/API acceptance is separate."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import BaseModel
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.api.deps import get_current_user
from app.api.v1 import voice
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import (
    Base,
    CheckIn,
    CommandReceipt,
    DeviceState,
    PlanningDraft,
    Reminder,
    RunEvent,
    SessionRun,
    Task,
    Thought,
    User,
    UserPreference,
)
from app.repositories import device_repository, run_repository, thought_repository
from app.services import command_service, draft_service
from tests.test_models import make_plan


@pytest.fixture
async def storage(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "head")
    engine = make_engine(url)
    sessions = session_factory(engine)

    def seed(db):
        user, _, _, _, session = make_plan(db)
        run = SessionRun(
            user_id=user.id,
            session_id=session.id,
            device_id="device-1",
            started_at=session.scheduled_start,
            ended_at=session.scheduled_end,
            run_state="awaiting_checkin",
            active_phase="ended",
            segment_index=None,
            actual_work_seconds=3600,
            actual_source="timer_proxy",
            settings_snapshot={},
        )
        other = User(email="other-storage@test.example", display_name="Other")
        db.add_all([run, other])
        db.flush()
        thought = Thought(
            user_id=user.id,
            client_thought_id="thought-1",
            source_run_id=run.id,
            input_type="audio",
            capture_state="saved",
            attempt_count=1,
        )
        checkin = CheckIn(user_id=user.id, run_id=run.id, kind="final", asked_at=run.ended_at)
        db.add_all([thought, checkin])
        db.commit()
        return SimpleNamespace(
            owner=user.id,
            other=other.id,
            run_id=run.id,
            thought_id=thought.id,
            session_id=session.id,
            started_at=run.started_at,
            ended_at=run.ended_at,
        )

    try:
        async with sessions() as db:
            data = await db.run_sync(seed)
        data.sessions, data.url, data.engine = sessions, url, engine
        yield data
    finally:
        await engine.dispose()


class DraftResult(BaseModel):
    draft_id: str
    thought_id: str


def new_draft(owner, client_id="draft-1"):
    return PlanningDraft(user_id=owner, client_draft_id=client_id, input_source="thought")


@pytest.mark.asyncio
async def test_conversion_rollback_receipt_reopen_and_forward_links(storage):
    owner = str(storage.owner)
    key = "convert-thought"
    digest = command_service.command_hash("thought_to_draft", str(storage.thought_id), {})
    async with storage.sessions() as db:
        draft = new_draft(storage.owner)
        await thought_repository.attach_draft(db, storage.owner, storage.thought_id, 1, draft)
        result = DraftResult(draft_id=str(draft.id), thought_id=str(storage.thought_id))
        command_service.record(db, owner, key, digest, "thought_to_draft", result)
        await db.flush()
        await db.rollback()
    async with storage.sessions() as db:
        thought = await thought_repository.get_thought(db, storage.owner, storage.thought_id)
        assert thought is not None
        assert thought.revision == 1
        assert await thought_repository.get_links(db, storage.owner, [storage.thought_id]) == {}
        assert await command_service.replay(db, owner, key, digest) is None
        draft = new_draft(storage.owner)
        await thought_repository.attach_draft(db, storage.owner, storage.thought_id, 1, draft)
        result = DraftResult(draft_id=str(draft.id), thought_id=str(storage.thought_id))
        command_service.record(db, owner, key, digest, "thought_to_draft", result)
        await db.commit()

    await storage.engine.dispose()
    reopened = make_engine(storage.url)
    try:
        async with session_factory(reopened)() as db:
            assert await command_service.replay(db, owner, key, digest) == result.model_dump()
            links = await thought_repository.get_links(db, storage.owner, [storage.thought_id])
            assert str(links[storage.thought_id].draft.id) == result.draft_id
            assert links[storage.thought_id].task_id is None
            # Mô phỏng phần ghi Task của UF2; chưa nghiệm thu thuật toán chốt lịch.
            task = Task(
                user_id=storage.owner,
                source_draft_id=links[storage.thought_id].draft.id,
                title="Công việc đã chốt",
                time_constraint={},
            )
            db.add(task)
            await db.commit()
            links = await thought_repository.get_links(db, storage.owner, [storage.thought_id])
            assert links[storage.thought_id].task_id == task.id
            assert await thought_repository.get_links(db, storage.other, [storage.thought_id]) == {}
            assert not await thought_repository.delete_thought(
                db, storage.owner, storage.thought_id, 2
            )
            await db.rollback()
    finally:
        await reopened.dispose()


@pytest.mark.asyncio
async def test_concurrent_conversion_replays_one_receipt(storage):
    owner, key = str(storage.owner), "same-conversion"
    digest = command_service.command_hash("thought_to_draft", str(storage.thought_id), {})

    async def convert():
        async with storage.sessions() as db:
            await command_service.lock_owner(db, owner)
            saved = await command_service.replay(db, owner, key, digest)
            if saved is not None:
                await db.rollback()
                return saved
            draft = new_draft(storage.owner)
            await thought_repository.attach_draft(db, storage.owner, storage.thought_id, 1, draft)
            result = DraftResult(draft_id=str(draft.id), thought_id=str(storage.thought_id))
            command_service.record(db, owner, key, digest, "thought_to_draft", result)
            await db.commit()
            return result.model_dump()

    results = await asyncio.gather(convert(), convert())
    assert results[0] == results[1]
    async with storage.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 1
        assert await db.scalar(select(func.count()).select_from(CommandReceipt)) == 1
        assert await command_service.replay(db, str(storage.other), key, digest) is None
        with pytest.raises(AppError) as error:
            await command_service.replay(db, owner, key, "b" * 64)
        assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_thought_delete_rollback_stale_revision_and_owner(storage):
    async with storage.sessions() as db:
        assert not await thought_repository.delete_thought(db, storage.other, storage.thought_id, 1)
        assert not await thought_repository.delete_thought(db, storage.owner, storage.thought_id, 0)
        assert await thought_repository.delete_thought(db, storage.owner, storage.thought_id, 1)
        await db.rollback()
    async with storage.sessions() as db:
        assert await thought_repository.get_thought(db, storage.owner, storage.thought_id)
        with pytest.raises(StaleDataError):
            await thought_repository.attach_draft(
                db, storage.owner, storage.thought_id, 0, new_draft(storage.owner)
            )
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 0
        assert await thought_repository.delete_thought(db, storage.owner, storage.thought_id, 1)
        await db.commit()
    async with storage.sessions() as db:
        assert await thought_repository.get_thought(db, storage.owner, storage.thought_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source_format", ["canonical", "hex", "uppercase", "braces", "urn"])
async def test_legacy_links_are_batched_and_prevent_second_draft(storage, source_format):
    legacy_id = {
        "canonical": str(storage.thought_id),
        "hex": storage.thought_id.hex,
        "uppercase": str(storage.thought_id).upper(),
        "braces": "{" + str(storage.thought_id) + "}",
        "urn": storage.thought_id.urn,
    }[source_format]
    async with storage.sessions() as db:
        legacy = new_draft(storage.owner)
        legacy.summary = {"_api_meta": {"source_thought_id": legacy_id}}
        db.add(legacy)
        await db.commit()
        statements = []

        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(storage.engine.sync_engine, "before_cursor_execute", capture)
        try:
            links = await thought_repository.get_links(
                db, storage.owner, [storage.thought_id, uuid4()]
            )
        finally:
            event.remove(storage.engine.sync_engine, "before_cursor_execute", capture)
        assert len(statements) == 1
        assert links[storage.thought_id].draft.id == legacy.id
        assert not await thought_repository.delete_thought(db, storage.owner, storage.thought_id, 1)
        with pytest.raises(ValueError, match="already has"):
            await thought_repository.attach_draft(
                db, storage.owner, storage.thought_id, 1, new_draft(storage.owner, "second")
            )
        await db.rollback()


def device_report(storage, revision, **changes):
    values = dict(
        user_id=storage.owner,
        device_id="device-1",
        device_report_revision=revision,
        notification_permission="granted",
        microphone_permission="granted",
        dnd_permission="granted",
        alarm_capability="exact",
        active_run_id=storage.run_id,
        dnd_owner_run_id=storage.run_id,
        last_schedule_revision=1,
        dnd_apply_state="applied",
        dnd_restore_state="pending",
        reported_at=storage.ended_at,
        app_visibility="foreground",
    )
    values.update(changes)
    return DeviceState(**values)


@pytest.mark.asyncio
async def test_device_reports_keep_latest_owner_and_rollback(storage):
    async with storage.sessions() as db:
        assert await device_repository.save_report(db, device_report(storage, 2))
        await db.commit()
    async with storage.sessions() as db:
        assert not await device_repository.save_report(
            db, device_report(storage, 1, dnd_restore_state="restored", active_run_id=None)
        )
        assert not await device_repository.save_report(db, device_report(storage, 2))
        assert await device_repository.save_report(
            db, device_report(storage, 3, active_run_id=None)
        )
        await db.rollback()
    async with storage.sessions() as db:
        saved = await device_repository.get_device_state(db, storage.owner, "device-1")
        assert saved is not None
        assert saved.device_report_revision == 2 and saved.active_run_id == storage.run_id
        assert saved.dnd_restore_state == "pending"
        assert await device_repository.get_device_state(db, storage.other, "device-1") is None
        with pytest.raises(IntegrityError):
            await device_repository.save_report(
                db, device_report(storage, 1, user_id=storage.other)
            )
        await db.rollback()


@pytest.mark.asyncio
async def test_concurrent_device_reports_keep_highest_revision(storage):
    async def send(revision):
        async with storage.sessions() as db:
            await command_service.lock_owner(db, str(storage.owner))
            await device_repository.save_report(db, device_report(storage, revision))
            await db.commit()

    await asyncio.gather(send(2), send(3))
    async with storage.sessions() as db:
        saved = await device_repository.get_device_state(db, storage.owner, "device-1")
        assert saved is not None
        assert saved.device_report_revision == 3
        assert await db.scalar(select(func.count()).select_from(DeviceState)) == 1


@pytest.mark.asyncio
async def test_run_checkin_revision_conflict_and_event_scope(storage):
    async with storage.sessions() as first, storage.sessions() as second:
        checkin = await run_repository.get_final_checkin(first, storage.owner, storage.run_id)
        assert checkin is not None
        stale = await run_repository.get_final_checkin(second, storage.owner, storage.run_id)
        assert stale is not None
        checkin.response_state = "no_respond"
        await first.commit()
        stale.response_state = "answered"
        with pytest.raises(StaleDataError):
            await second.commit()
        await second.rollback()
        fresh = await run_repository.get_final_checkin(second, storage.owner, storage.run_id)
        assert fresh is not None
        assert fresh.id == checkin.id and fresh.response_state == "no_respond"
        fresh.response_state, fresh.response_revision = "answered", 1
        fresh.session_outcome = "partial"
        await second.commit()
    async with storage.sessions() as db:
        run = await run_repository.get_live_run(db, storage.owner)
        assert run is not None
        assert run.id == storage.run_id and run.outcome == "unknown"
        assert run.ended_at == storage.ended_at and run.actual_work_seconds == 3600
        assert await run_repository.get_run(db, storage.other, storage.run_id) is None
        assert await run_repository.get_final_checkin(db, storage.other, storage.run_id) is None
        db.add(
            RunEvent(
                user_id=storage.owner,
                run_id=storage.run_id,
                client_event_id="event-1",
                event_type="end",
                occurred_at=storage.ended_at,
                payload={},
            )
        )
        await db.commit()
        events = await run_repository.get_events_by_client_ids(
            db, storage.owner, ["event-1", "missing"]
        )
        assert list(events) == ["event-1"]
        assert await run_repository.get_events_by_client_ids(db, storage.other, ["event-1"]) == {}


@pytest.mark.asyncio
async def test_multiple_reminders_and_revision_cancellation_rollback(storage):
    async with storage.sessions() as db:
        for offset in [900, 600, 300]:
            db.add(
                Reminder(
                    user_id=storage.owner,
                    session_id=storage.session_id,
                    schedule_revision=1,
                    kind="pre_session",
                    fire_at=storage.started_at - timedelta(seconds=offset),
                    dedupe_key=f"{storage.session_id}:1:{offset}",
                )
            )
        await db.commit()
        reminders = list(await db.scalars(select(Reminder)))
        for reminder in reminders:
            reminder.delivery_state = "cancelled"
        await db.flush()
        await db.rollback()
    async with storage.sessions() as db:
        reminders = list(await db.scalars(select(Reminder)))
        assert len(reminders) == 3
        assert all(row.delivery_state == "scheduled" for row in reminders)
        duplicate = Reminder(
            user_id=storage.owner,
            session_id=storage.session_id,
            schedule_revision=1,
            kind="pre_session",
            fire_at=storage.started_at,
            dedupe_key=reminders[0].dedupe_key,
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.fixture
async def http_client(storage):
    async def test_db():
        async with storage.sessions() as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=str(storage.owner))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://storage.test"
        ) as client:
            bootstrap = await client.get("/api/v1/drafts")
            assert bootstrap.status_code == 200
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_http_create_draft_persists_fk_and_receipt_and_promotes_legacy(storage, http_client):
    headers = {"Idempotency-Key": str(uuid4())}
    payload = {
        "client_draft_id": "http-thought",
        "input_source": "thought",
        "raw_input": "Viết báo cáo",
        "source_thought_id": str(storage.thought_id),
    }
    created = await http_client.post("/api/v1/drafts", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    replay = await http_client.post("/api/v1/drafts", headers=headers, json=payload)
    assert replay.json()["data"] == data
    duplicate = await http_client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": str(uuid4())},
        json={**payload, "client_draft_id": "different-client-id"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "THOUGHT_ALREADY_LINKED"
    did = draft_service.resolve_draft_uuid(data["draft_id"])
    async with storage.sessions() as db:
        row = await db.get(PlanningDraft, did)
        assert row.source_thought_id == storage.thought_id
        assert "source_thought_id" not in row.summary["_api_meta"]
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        # Tái tạo dạng dữ liệu UF1 cũ, để kiểm sửa nháp sẽ khôi phục FK.
        row.source_thought_id = None
        row.summary = {
            **row.summary,
            "_api_meta": {**row.summary["_api_meta"], "source_thought_id": str(storage.thought_id)},
        }
        await db.commit()
    reloaded = await http_client.get(f"/api/v1/drafts/{data['draft_id']}")
    assert reloaded.json()["data"]["source_thought_id"] == str(storage.thought_id)
    updated = await http_client.patch(
        f"/api/v1/drafts/{data['draft_id']}",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_summary_revision": data["summary_revision"],
            "expected_planning_input_revision": data["planning_input_revision"],
            "changes": {"raw_input": "Viết báo cáo cuối kỳ"},
        },
    )
    assert updated.status_code == 200, updated.text
    async with storage.sessions() as db:
        row = await db.get(PlanningDraft, did)
        assert row.source_thought_id == storage.thought_id
        assert "source_thought_id" not in row.summary["_api_meta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["wrong_owner", "missing", "running"])
async def test_http_rejects_invalid_thought_source_without_writing(storage, http_client, source):
    thought_id = storage.thought_id
    async with storage.sessions() as db:
        if source == "wrong_owner":
            foreign = Thought(
                user_id=storage.other,
                client_thought_id="other-thought",
                input_type="text",
                capture_state="saved",
                attempt_count=1,
            )
            db.add(foreign)
            await db.commit()
            thought_id = foreign.id
        elif source == "missing":
            thought_id = uuid4()
        else:
            run = await db.get(SessionRun, storage.run_id)
            run.ended_at = None
            run.run_state = "running"
            await db.commit()
        before = await db.scalar(select(func.count()).select_from(PlanningDraft))
    key = str(uuid4())
    response = await http_client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": key},
        json={
            "client_draft_id": str(uuid4()),
            "input_source": "thought",
            "raw_input": "Viết báo cáo",
            "source_thought_id": str(thought_id),
        },
    )
    assert response.status_code == (409 if source == "running" else 404), response.text
    async with storage.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == before
        assert (
            await db.scalar(select(CommandReceipt).where(CommandReceipt.command_id == key)) is None
        )

    transcription = await http_client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": str(uuid4())},
        data={
            "purpose": "thought_to_draft",
            "thought_id": str(thought_id),
            "device_id": "device-1",
        },
        files={"file": ("thought.wav", b"test-audio", "audio/wav")},
    )
    assert transcription.status_code == response.status_code, transcription.text


@pytest.mark.asyncio
async def test_http_concurrent_draft_creation_keeps_one_source_link(storage, http_client):
    async def create(client_id):
        return await http_client.post(
            "/api/v1/drafts",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "client_draft_id": client_id,
                "input_source": "thought",
                "raw_input": "Viết báo cáo",
                "source_thought_id": str(storage.thought_id),
            },
        )

    responses = await asyncio.gather(create("first"), create("second"))
    assert sorted(response.status_code for response in responses) == [201, 409]
    async with storage.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(PlanningDraft)
                .where(PlanningDraft.source_thought_id == storage.thought_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_voice_retry_uses_durable_receipt_after_memory_cache_is_lost(
    storage, http_client, monkeypatch
):
    calls = []

    async def transcribe(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(text="Viết báo cáo")

    monkeypatch.setattr(voice, "get_voice_service", lambda: SimpleNamespace(transcribe=transcribe))
    headers = {"Idempotency-Key": str(uuid4())}
    data = {
        "purpose": "thought_to_draft",
        "thought_id": str(storage.thought_id),
        "device_id": "device-1",
    }
    files = {"file": ("thought.wav", b"test-audio", "audio/wav")}
    first = await http_client.post(
        "/api/v1/voice/transcriptions", headers=headers, data=data, files=files
    )
    assert first.status_code == 200, first.text
    voice._VOICE_IDEMPOTENCY_STORE.pop((str(storage.owner), headers["Idempotency-Key"]), None)
    await storage.engine.dispose()
    replay = await http_client.post(
        "/api/v1/voice/transcriptions", headers=headers, data=data, files=files
    )
    assert replay.status_code == 200, replay.text
    assert first.json()["data"] == replay.json()["data"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_analysis_blocks_live_source_before_provider(storage, http_client, monkeypatch):
    async def unexpected_provider(*args, **kwargs):
        pytest.fail("LLM called while the source Run is active")

    monkeypatch.setattr(draft_service.ai_service, "extract_task_intent", unexpected_provider)
    async with storage.sessions() as db:
        run = await db.get(SessionRun, storage.run_id)
        run.ended_at = None
        run.run_state = "running"
        await db.commit()
    created = await http_client.post(
        "/api/v1/drafts",
        headers={"Idempotency-Key": str(uuid4())},
        json={"client_draft_id": "analysis-draft", "input_source": "text", "raw_input": "Báo cáo"},
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    response = await http_client.post(
        f"/api/v1/drafts/{data['draft_id']}/analyze",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_summary_revision": data["summary_revision"],
            "expected_planning_input_revision": data["planning_input_revision"],
            "source_thought_id": str(storage.thought_id),
            "message": "Tạo việc từ suy nghĩ",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "SESSION_STILL_RUNNING"


@pytest.mark.asyncio
async def test_device_migration_preserves_populated_database(sqlite_migration):
    url, alembic = sqlite_migration
    # Fixture ORM hiện dùng C01; kiểm thêm DeviceState trên DB kế hoạch đã có dữ liệu.
    alembic("upgrade", "20261103_01")
    engine = make_engine(url)
    try:
        async with session_factory(engine)() as db:
            user, pref, task, _, session = await db.run_sync(make_plan)
            pref.reminder_offsets = [300]
            await db.commit()
            ids = user.id, task.id, session.id
    finally:
        await engine.dispose()
    alembic("upgrade", "head")
    with pytest.raises(
        AssertionError, match="C01 downgrade requires an explicit snapshot/data recovery plan"
    ):
        alembic("downgrade", "base")
    reopened = make_engine(url)
    try:
        await assert_migrated_schema(reopened)
        async with session_factory(reopened)() as db:
            assert await db.get(User, ids[0]) is not None
            assert await db.get(Task, ids[1]) is not None
            pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == ids[0]))
            assert pref is not None
            assert pref.reminder_offsets == [300]
            assert await db.scalar(select(func.count()).select_from(DeviceState)) == 0
    finally:
        await reopened.dispose()


@pytest.mark.asyncio
async def test_merge_preserves_existing_device_branch(sqlite_migration):
    url, alembic = sqlite_migration
    alembic("upgrade", "20261105_01")
    engine = make_engine(url)
    try:
        async with session_factory(engine)() as db:
            user = User(email="existing-device@example.test", display_name="TN")
            db.add(user)
            await db.flush()
            db.add(UserPreference(user_id=user.id, reminder_offsets=[300]))
            report = device_report(
                SimpleNamespace(owner=user.id, run_id=None, ended_at=datetime.now(UTC)),
                3,
                dnd_apply_state="not_applied",
                dnd_restore_state="not_required",
            )
            await device_repository.save_report(db, report)
            await db.commit()
            owner = user.id
    finally:
        await engine.dispose()

    alembic("upgrade", "head")
    with pytest.raises(
        AssertionError, match="C01 downgrade requires an explicit snapshot/data recovery plan"
    ):
        alembic("downgrade", "base")
    reopened = make_engine(url)
    try:
        await assert_migrated_schema(reopened)
        async with session_factory(reopened)() as db:
            saved = await device_repository.get_device_state(db, owner, "device-1")
            assert saved is not None
            assert saved.device_report_revision == 3
            pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == owner))
            assert pref is not None
            assert pref.reminder_offsets == [300]
    finally:
        await reopened.dispose()


async def assert_migrated_schema(engine):
    """C01 giữ archive Part; mọi khác biệt ngoài archive vẫn phải làm test thất bại."""
    legacy_columns = {"steps": {"planning_part_id"}, "plan_candidates": {"planning_parts"}}

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return name != "planning_parts"
        if type_ == "column":
            return name not in legacy_columns.get(obj.table.name, set())
        if type_ in ("index", "unique_constraint", "foreign_key_constraint"):
            return not any(
                column.name in legacy_columns.get(column.table.name, set())
                for column in obj.columns
            )
        return True

    def compare(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_object": include_object}
        )
        assert compare_metadata(context, Base.metadata) == []

    async with engine.connect() as connection:
        await connection.run_sync(compare)
