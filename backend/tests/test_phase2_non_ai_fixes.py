"""Integration tests for Phase 2 Non-AI fixes (#3 BUG-UF7-POLICY-503, #5 Refresh Token, A20 Window)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db
from app.api.v1.auth import get_google_verifier
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.core.errors import AppError
from app.core.security import issue_app_access_token
from app.main import app
from app.models import (
    AdaptationSuggestion,
    Base,
    SessionRun,
    Step,
    Task,
    User,
    UserPreference,
)
from app.models import (
    Session as SessionRow,
)
from app.schemas.auth import VerifiedGoogleIdentity
from app.services import plan_service, settings_service


async def _make_env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = session_factory(engine)

    async def override_get_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return engine, sf


def _bearer(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_app_access_token(user_id).access_token}"}


@pytest.mark.asyncio
async def test_get_preferences_auto_creates_default_policy_without_503(auth_config):
    engine, sf = await _make_env()
    user_uuid = uuid4()
    try:
        async with sf() as db:
            db.add(
                User(
                    id=user_uuid,
                    email="nopref@example.com",
                    display_name="No Pref User",
                    is_active=True,
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/preferences",
                headers=_bearer(user_uuid),
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["work_interval_seconds"] == settings.DEFAULT_WORK_INTERVAL_SECONDS
            assert data["rest_interval_seconds"] == settings.DEFAULT_REST_INTERVAL_SECONDS
            assert data["buffer_multiplier"] == settings.DEFAULT_BUFFER_MULTIPLIER
            assert data["effective_revision"] == 1

            resp2 = await client.get(
                "/api/v1/preferences",
                headers=_bearer(user_uuid),
            )
            assert resp2.status_code == 200

        async with sf() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(UserPreference)
                .where(UserPreference.user_id == user_uuid)
            )
            assert count == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_auth_refresh_rotates_tokens_and_checks_user_active(auth_config):
    engine, sf = await _make_env()

    async def verify(id_token: str) -> VerifiedGoogleIdentity:
        if id_token != "valid":
            raise AppError(status_code=401, code="INVALID_GOOGLE_ID_TOKEN", message="Bad")
        return VerifiedGoogleIdentity(
            sub="sub-refresh-test",
            email="refresh@example.com",
            display_name="Refresh User",
        )

    app.dependency_overrides[get_google_verifier] = lambda: verify
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login_resp = await client.post("/api/v1/auth/google", json={"id_token": "valid"})
            assert login_resp.status_code == 200, login_resp.text
            login_data = login_resp.json()["data"]
            assert login_data["refresh_token"]
            assert login_data["refresh_expires_in"] == settings.REFRESH_TOKEN_TTL_SECONDS

            # Refresh token cannot be used as Bearer access token
            bad_me = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {login_data['refresh_token']}"},
            )
            assert bad_me.status_code == 401

            # Access token cannot be used on /auth/refresh
            bad_refresh = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": login_data["access_token"]},
            )
            assert bad_refresh.status_code == 401

            # Valid refresh token issues new token bundle
            refresh_resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": login_data["refresh_token"]},
            )
            assert refresh_resp.status_code == 200, refresh_resp.text
            refreshed = refresh_resp.json()["data"]
            assert refreshed["access_token"]
            assert refreshed["refresh_token"]
            assert refreshed["refresh_token"] != login_data["refresh_token"]

            me_resp = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {refreshed['access_token']}"},
            )
            assert me_resp.status_code == 200

            # Deactivated user is rejected with 403 ACCOUNT_DISABLED
            user_uuid = UUID(login_data["user"]["user_id"])
            async with sf() as db:
                user_row = await db.get(User, user_uuid)
                assert user_row is not None
                user_row.is_active = False
                await db.commit()

            disabled_resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refreshed["refresh_token"]},
            )
            assert disabled_resp.status_code == 403
            assert disabled_resp.json()["error"]["code"] == "ACCOUNT_DISABLED"

            # Direct helper token for non-existent user -> 401
            ghost_bundle = issue_app_access_token(uuid4())
            ghost_resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": ghost_bundle.refresh_token},
            )
            assert ghost_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_suggestion_window_caps_at_10_and_get_is_idempotent(auth_config, monkeypatch):
    engine, sf = await _make_env()
    fixed_now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)
    user_uuid = uuid4()
    try:
        async with sf() as db:
            db.add(User(id=user_uuid, email="a20@example.com", display_name="A20", is_active=True))
            db.add(UserPreference(user_id=user_uuid, buffer_multiplier=1.1, effective_revision=1))
            task_id = uuid4()
            db.add(
                Task(
                    id=task_id,
                    user_id=user_uuid,
                    revision=1,
                    title="A20 Task",
                    description="Collect A20 samples",
                    scope="12 steps",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-20T17:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    status="in_progress",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            # 12 clean completed steps -> sliding window must cap at 10
            for idx in range(12):
                completed_at = fixed_now - timedelta(hours=20 - idx)
                step_id = uuid4()
                sess_id = uuid4()
                db.add(
                    Step(
                        id=step_id,
                        user_id=user_uuid,
                        task_id=task_id,
                        revision=1,
                        order_index=idx + 1,
                        title=f"Step {idx + 1}",
                        estimated_base_seconds=1200,
                        remaining_base_seconds=0,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="completed",
                        created_at=completed_at - timedelta(minutes=40),
                        updated_at=completed_at,
                    )
                )
                await db.flush()
                db.add(
                    SessionRow(
                        id=sess_id,
                        user_id=user_uuid,
                        step_id=step_id,
                        revision=1,
                        scheduled_start=completed_at - timedelta(minutes=40),
                        scheduled_end=completed_at - timedelta(minutes=20),
                        work_seconds=1200,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1200}],
                        duration_seconds=1200,
                        schedule_revision=1,
                        status="completed",
                        start_state="started",
                        created_at=completed_at - timedelta(minutes=40),
                        updated_at=completed_at,
                    )
                )
                await db.flush()
                db.add(
                    SessionRun(
                        id=uuid4(),
                        user_id=user_uuid,
                        session_id=sess_id,
                        device_id="dev-a20",
                        started_at=completed_at - timedelta(minutes=40),
                        ended_at=completed_at,
                        run_state="ended",
                        active_phase="ended",
                        segment_index=0,
                        work_elapsed_seconds=2160,
                        rest_elapsed_seconds=0,
                        actual_work_seconds=2160,
                        actual_source="timer_proxy",
                        outcome="completed",
                        settings_snapshot={"buffer_multiplier": 1.1},
                        created_at=completed_at - timedelta(minutes=40),
                        updated_at=completed_at,
                    )
                )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get(
                "/api/v1/suggestions/current",
                headers=_bearer(user_uuid),
            )
            assert r1.status_code == 200, r1.text
            d1 = r1.json()["data"]
            assert d1 is not None
            assert d1["sample_count"] == 10

            r2 = await client.get(
                "/api/v1/suggestions/current",
                headers=_bearer(user_uuid),
            )
            assert r2.status_code == 200
            assert r2.json()["data"]["suggestion_id"] == d1["suggestion_id"]

        async with sf() as db:
            pending_count = await db.scalar(
                select(func.count())
                .select_from(AdaptationSuggestion)
                .where(AdaptationSuggestion.user_id == user_uuid)
            )
            assert pending_count == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_preferences_propagates_fk_error_and_suggestion_ensures_pref_row():
    engine = make_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = session_factory(engine)
    fixed_now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    try:
        # 1. user_id không tồn tại khi FK bật -> get_preferences phải raise IntegrityError, không trả default giả
        async with sf() as db:
            with pytest.raises(IntegrityError):
                await plan_service.get_preferences(user_id=str(uuid4()), db=db)

        # 2. User hợp lệ chưa có UserPreference nhưng có đủ 7 Step hoàn thành -> get_current_suggestion tạo PreferenceRow trước khi khóa
        uid = uuid4()
        async with sf() as db:
            db.add(User(id=uid, email="nopref_sug@example.com", display_name="NoPref", is_active=True))
            task_id = uuid4()
            db.add(
                Task(
                    id=task_id,
                    user_id=uid,
                    revision=1,
                    title="T",
                    description="D",
                    scope="S",
                    time_constraint={
                        "mode": "deadline",
                        "at": "2026-11-20T17:00:00+07:00",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    importance=3,
                    status="in_progress",
                )
            )
            await db.flush()
            for idx in range(7):
                completed_at = fixed_now - timedelta(hours=10 - idx)
                step_id = uuid4()
                sess_id = uuid4()
                db.add(
                    Step(
                        id=step_id,
                        user_id=uid,
                        task_id=task_id,
                        revision=1,
                        order_index=idx + 1,
                        title=f"Step {idx + 1}",
                        estimated_base_seconds=1200,
                        remaining_base_seconds=0,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="completed",
                        created_at=completed_at - timedelta(minutes=40),
                        updated_at=completed_at,
                    )
                )
                await db.flush()
                db.add(
                    SessionRow(
                        id=sess_id,
                        user_id=uid,
                        step_id=step_id,
                        revision=1,
                        scheduled_start=completed_at - timedelta(minutes=40),
                        scheduled_end=completed_at - timedelta(minutes=20),
                        work_seconds=1200,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1200}],
                        duration_seconds=1200,
                        schedule_revision=1,
                        status="completed",
                        start_state="started",
                        created_at=completed_at - timedelta(minutes=40),
                        updated_at=completed_at,
                    )
                )
                await db.flush()
                db.add(
                    SessionRun(
                        id=uuid4(),
                        user_id=uid,
                        session_id=sess_id,
                        device_id="dev-1",
                        started_at=completed_at - timedelta(minutes=40),
                        ended_at=completed_at,
                        run_state="ended",
                        active_phase="ended",
                        segment_index=0,
                        work_elapsed_seconds=2160,
                        rest_elapsed_seconds=0,
                        actual_work_seconds=2160,
                        actual_source="timer_proxy",
                        outcome="completed",
                        settings_snapshot={"buffer_multiplier": 1.2},
                        created_at=completed_at - timedelta(minutes=40),
                        updated_at=completed_at,
                    )
                )
            await db.commit()

        async with sf() as db:
            sug = await settings_service.get_current_suggestion(
                db,
                user_id=str(uid),
                now=fixed_now,
                commit_if_created=True,
            )
            assert sug is not None
            pref = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid))
            assert pref is not None
    finally:
        await engine.dispose()
