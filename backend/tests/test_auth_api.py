import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.deps import get_db
from app.api.v1.auth import get_google_verifier
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import Base, User, UserPreference
from app.schemas.auth import VerifiedGoogleIdentity
from app.services.auth_service import login_with_google
from app.services.draft_service import ensure_dev_seed_in_db


@pytest.fixture
async def auth_api(tmp_path, monkeypatch, auth_config):
    engine = make_engine("sqlite+aiosqlite:///" + (tmp_path / "auth.db").as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    identities = {
        "a": VerifiedGoogleIdentity(sub="sub-a", email="a@example.com", display_name="A"),
        "b": VerifiedGoogleIdentity(sub="sub-b", email="b@example.com", display_name="B"),
    }

    async def verify(token):
        if token not in identities:
            raise AppError(
                status_code=401,
                code="INVALID_GOOGLE_ID_TOKEN",
                message="Invalid",
                next_action="authenticate",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return identities[token]

    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_google_verifier] = lambda: verify
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory, identities, verify
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        await engine.dispose()


async def login(client, name="a"):
    result = await client.post("/api/v1/auth/google", json={"id_token": name})
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["expires_in"] == 3600
    assert data["token_type"] == "bearer"
    return data, {"Authorization": "Bearer " + data["access_token"]}


async def test_login_me_preferences_repeat_email_change(auth_api):
    client, factory, identities, _ = auth_api
    data, headers = await login(client)
    uid = UUID(data["user"]["user_id"])
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["data"] == data["user"]
    assert (await client.get("/api/v1/preferences", headers=headers)).status_code == 200
    async with factory() as db:
        pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid))
        pref.work_interval_seconds = 1200
        await db.commit()
    identities["a"] = identities["a"].model_copy(update={"email": "changed@example.com"})
    again, _ = await login(client)
    assert again["user"]["user_id"] == str(uid)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(UserPreference)) == 1
        pref = await db.scalar(select(UserPreference))
        assert pref.work_interval_seconds == 1200


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer", "Bearer a b", "Bearer wrong", ""])
async def test_headers_no_seed(auth_api, header):
    client, factory, _, _ = auth_api
    response = await client.get(
        "/api/v1/auth/me", headers={} if header is None else {"Authorization": header}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    async with factory() as db:
        db.info["dev_seed_ready"] = True
        await ensure_dev_seed_in_db(db)
        assert await db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize(
    "payload", [{}, {"id_token": " "}, {"id_token": 5}, {"id_token": "a", "sub": "b"}]
)
async def test_request_schema(auth_api, payload):
    assert (await auth_api[0].post("/api/v1/auth/google", json=payload)).status_code == 422


async def test_invalid_google_creates_nothing(auth_api):
    client, factory, _, _ = auth_api
    response = await client.post("/api/v1/auth/google", json={"id_token": "wrong"})
    assert response.status_code == 401
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_collision_disabled_deleted(auth_api):
    client, factory, identities, _ = auth_api
    data, headers = await login(client)
    identities["b"] = identities["b"].model_copy(update={"email": "a@example.com"})
    response = await client.post("/api/v1/auth/google", json={"id_token": "b"})
    assert response.status_code == 409
    uid = UUID(data["user"]["user_id"])
    async with factory() as db:
        user = await db.get(User, uid)
        user.is_active = False
        await db.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 403
    assert (await client.post("/api/v1/auth/google", json={"id_token": "a"})).status_code == 403
    async with factory() as db:
        pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid))
        await db.delete(pref)
        await db.delete(await db.get(User, uid))
        await db.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_concurrent_same_subject(auth_api):
    _, factory, _, verify = auth_api

    async def exchange():
        async with factory() as db:
            return await login_with_google(db, "a", verify)

    results = await asyncio.gather(exchange(), exchange())
    assert results[0].user.user_id == results[1].user.user_id
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(UserPreference)) == 1


async def test_commit_failure_does_not_issue(auth_api, monkeypatch):
    _, factory, _, verify = auth_api
    from app.services import auth_service

    issue = AsyncMock()
    monkeypatch.setattr(auth_service, "issue_app_access_token", issue)
    async with factory() as db:

        async def fail(*args, **kwargs):
            raise RuntimeError("commit failed")

        monkeypatch.setattr(
            db.sync_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed"))
        )
        # SessionTransaction commits the transaction directly, so fail its before_commit event.
        from sqlalchemy import event

        def before_commit(session):
            raise RuntimeError("commit failed")

        event.listen(db.sync_session, "before_commit", before_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            await login_with_google(db, "a", verify)
    issue.assert_not_called()
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_startup_guard_before_worker(monkeypatch):
    from app.main import lifespan

    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        asyncio, "create_task", lambda *args: pytest.fail("worker started before guard")
    )
    with pytest.raises(ValueError):
        async with lifespan(app):
            pytest.fail("startup accepted bypass")


async def test_seed_guard_before_db_access(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "google")

    class NoDatabaseAccess:
        @property
        def info(self):
            pytest.fail("seed touched DB/cache in google mode")

    from typing import cast

    from sqlalchemy.ext.asyncio import AsyncSession

    await ensure_dev_seed_in_db(cast(AsyncSession, NoDatabaseAccess()))


async def test_owner_draft_and_receipt(auth_api):
    client, _, _, _ = auth_api
    a, ha = await login(client, "a")
    b, hb = await login(client, "b")
    assert a["user"]["user_id"] != b["user"]["user_id"]
    payload = {"client_draft_id": "owner-test", "input_source": "text", "raw_input": "Viết báo cáo"}
    created = await client.post(
        "/api/v1/drafts", headers={**ha, "Idempotency-Key": "owner-command"}, json=payload
    )
    assert created.status_code == 201, created.text
    did = created.json()["data"]["draft_id"]
    assert (await client.get("/api/v1/drafts/" + did, headers=ha)).status_code == 200
    assert (await client.get("/api/v1/drafts/" + did, headers=hb)).status_code == 404
    assert (await client.get("/api/v1/commands/owner-command", headers=ha)).status_code == 200
    assert (await client.get("/api/v1/commands/owner-command", headers=hb)).status_code == 404
    assert (await client.get("/api/v1/drafts", headers=hb)).json()["data"]["items"] == []


async def test_dev_missing_header_allowed_but_bad_bearer_rejected(auth_api, monkeypatch):
    client, _, _, _ = auth_api
    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["data"]["user_id"] == "user_a"
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer bad"})
    ).status_code == 401


async def test_calendar_callback_uses_state_without_bearer(auth_api, monkeypatch):
    from app.api.v1.calendar import CalendarAuthError, CalendarService

    calls = []

    async def reject_state(self, **kwargs):
        calls.append(kwargs)
        raise CalendarAuthError("Invalid state")

    monkeypatch.setattr(CalendarService, "handle_oauth_callback", reject_state)
    for path in ("/api/v1/calendar/oauth/callback", "/api/v1/calendar/oauth2callback"):
        response = await auth_api[0].get(path, params={"code": "code", "state": "invalid"})
        assert response.status_code == 401
        err = response.json()["error"]
        assert err["code"] == "CALENDAR_AUTH_REQUIRED"
        assert err["message"] == "Invalid state"
    assert len(calls) == 2
    assert all(call["user_id"] is None for call in calls)


async def test_real_resource_owner_task_run_thought_connection(auth_api):
    from app.core.security import issue_app_access_token
    from app.models import CalendarConnection
    from tests.test_w04_focus_start import _seed_w04_plan

    client, factory, _, _ = auth_api
    _, other = await login(client, "b")
    async with factory() as db:
        info = await db.run_sync(_seed_w04_plan)
        connection = CalendarConnection(
            user_id=info["user_id"],
            provider="google",
            account_ref="owner-calendar",
            connection_state="connected",
            read_calendar_ids=[],
            granted_scopes=[],
            credential_ref="offline-test",
        )
        db.add(connection)
        await db.commit()
        connection_id = str(connection.id)
    own = {"Authorization": "Bearer " + issue_app_access_token(info["user_id"]).access_token}
    from app.models import Step, Task
    from app.services.draft_service import resolve_draft_uuid
    from tests.test_planning_api import summary

    draft_response = await client.post(
        "/api/v1/drafts",
        headers={**own, "Idempotency-Key": "task-source"},
        json={"client_draft_id": "auth-task-source", "input_source": "text", "summary": summary()},
    )
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()["data"]
    async with factory() as db:
        task = await db.get(Task, info["task_id"])
        step = await db.get(Step, info["step_id"])
        step.expected_output = "Một trang tài liệu"
        task.source_draft_id = resolve_draft_uuid(draft["draft_id"])
        task.description = draft["summary"]["description"]
        task.scope = draft["summary"]["scope"]
        task.source_summary = draft["summary"]
        task.source_summary_revision = draft["summary_revision"]
        task.source_planning_input_revision = draft["planning_input_revision"]
        task.time_constraint = dict(
            draft["summary"]["time_constraint"], start_at=info["scheduled_start"].isoformat()
        )
        await db.commit()
    task_path = "/api/v1/tasks/" + str(info["task_id"])
    owned_task = await client.get(task_path, headers=own)
    assert owned_task.status_code == 200, owned_task.text
    assert (await client.get(task_path, headers=other)).status_code == 404
    started = await client.post(
        "/api/v1/sessions/" + str(info["session_id"]) + "/start",
        headers={**own, "Idempotency-Key": "owned-start"},
        json={
            "expected_schedule_revision": 1,
            "device_id": "auth-test-device",
            "requested_start_at": info["scheduled_start"].isoformat(),
        },
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["data"]["run_id"]
    assert (await client.get("/api/v1/runs/" + run_id, headers=own)).status_code == 200
    assert (await client.get("/api/v1/runs/" + run_id, headers=other)).status_code == 404
    thought = await client.post(
        "/api/v1/thoughts",
        headers={**own, "Idempotency-Key": "owned-thought"},
        json={
            "client_thought_id": "auth-thought",
            "source_run_id": run_id,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
            "text_content": None,
        },
    )
    assert thought.status_code == 201, thought.text
    tid = thought.json()["data"]["thought_id"]
    assert (await client.get("/api/v1/thoughts/" + tid, headers=own)).status_code == 200
    assert (await client.get("/api/v1/thoughts/" + tid, headers=other)).status_code == 404
    path = "/api/v1/calendar/connections/" + connection_id
    assert (await client.get(path, headers=own)).status_code == 200
    assert (await client.get(path, headers=other)).status_code == 404


async def test_missing_signing_key_fails_before_verification_or_writes(auth_api, monkeypatch):
    client, factory, _, _ = auth_api
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "")
    response = await client.post("/api/v1/auth/google", json={"id_token": "a"})
    assert response.status_code == 503
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_login_does_not_commit_parent_transaction(auth_api):
    _, factory, _, verify = auth_api
    async with factory() as db:
        async with db.begin():
            with pytest.raises(RuntimeError, match="fresh session"):
                await login_with_google(db, "a", verify)
            assert db.in_transaction()


async def test_same_sub_winner_visible_between_subject_and_email_reads(auth_api, monkeypatch):
    client, factory, _, verify = auth_api
    existing, _ = await login(client)
    async with factory() as db:
        scalar = db.scalar
        calls = 0

        async def read_gap(statement, *args, **kwargs):
            nonlocal calls
            calls += 1
            # First lookup models a SELECT that ran before the winner committed;
            # the next email query runs after its commit and must resolve it.
            if calls == 1:
                return None
            return await scalar(statement, *args, **kwargs)

        monkeypatch.setattr(db, "scalar", read_gap)
        result = await login_with_google(db, "a", verify)
        assert result.user.user_id == existing["user"]["user_id"]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(UserPreference)) == 1


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer", "Bearer ", "Bearer a b", "Bearer wrong"])
async def test_dev_malformed_headers_never_seed(auth_api, monkeypatch, header):
    client, factory, _, _ = auth_api
    monkeypatch.setattr(settings, "AUTH_MODE", "dev_user")
    response = await client.get("/api/v1/auth/me", headers={"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0
