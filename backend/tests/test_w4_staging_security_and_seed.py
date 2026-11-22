"""Tests for W4 staging security hardening (BE-02 CORS, BE-05 OAuth/Secret) and W4-03A QA seed."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import get_db
from app.api.v1.calendar import MOBILE_RETURN_TARGET, _allowed_return_target
from app.core.config import DEFAULT_SECRET_KEY, Settings, settings
from app.core.database import make_engine, session_factory
from app.core.security import issue_app_access_token
from app.main import app
from app.models import (
    Base,
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    CheckIn,
    PlanningDraft,
    Session,
    SessionRun,
    Step,
    Task,
    User,
    UserPreference,
)
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_calendar_sync_jobs
from app.services.credential_crypto import decrypt_credentials, encrypt_credentials
from app.services.google_calendar_client import (
    FakeCalendarClient,
    get_calendar_client,
    reset_fake_calendar_client,
)
from scripts import seed_w4_qa

_STRONG_SECRET = "s" * 40
_VALID_FERNET = Fernet.generate_key().decode("ascii")


def _cors_app(origins: list[str]) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @test_app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return test_app


# ---------------------------------------------------------------------------
# BE-02: CORS Origin Hardening & Settings Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_be02_cors_blocks_evil_origin_and_allows_local_origins():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for evil in (
            "https://evil.example",
            "http://localhost:8080.evil.com",
            "http://localhost:8080@evil.com",
            "http://localhost:8080/",
            "null",
        ):
            opt_bad = await client.options(
                "/api/v1/health",
                headers={
                    "Origin": evil,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert opt_bad.status_code == 400
            assert "access-control-allow-origin" not in opt_bad.headers

            get_bad = await client.get("/api/v1/health", headers={"Origin": evil})
            assert get_bad.status_code == 200
            assert "access-control-allow-origin" not in get_bad.headers

        for allowed in ("http://localhost:8080", "http://127.0.0.1:8080"):
            opt_ok = await client.options(
                "/api/v1/health",
                headers={
                    "Origin": allowed,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert opt_ok.status_code == 200
            assert opt_ok.headers["access-control-allow-origin"] == allowed
            assert opt_ok.headers["access-control-allow-credentials"] == "true"

            get_ok = await client.get("/api/v1/health", headers={"Origin": allowed})
            assert get_ok.status_code == 200
            assert get_ok.headers["access-control-allow-origin"] == allowed
            assert get_ok.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_be02_deployed_cors_defaults_empty_and_respects_explicit_origins():
    staging_cfg = Settings(
        _env_file=None,
        ENVIRONMENT="staging",
        SECRET_KEY=_STRONG_SECRET,
        CALENDAR_CREDENTIAL_SECRET_KEY=_VALID_FERNET,
    )
    prod_cfg = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        SECRET_KEY=_STRONG_SECRET,
        CALENDAR_CREDENTIAL_SECRET_KEY=_VALID_FERNET,
    )
    assert staging_cfg.browser_origins == []
    assert prod_cfg.browser_origins == []

    explicit_cfg = Settings(
        _env_file=None,
        ENVIRONMENT="staging",
        SECRET_KEY=_STRONG_SECRET,
        CALENDAR_CREDENTIAL_SECRET_KEY=_VALID_FERNET,
        CORS_ALLOWED_ORIGINS=["https://app.proplan.example"],
    )
    assert explicit_cfg.browser_origins == ["https://app.proplan.example"]

    cors_app = _cors_app(explicit_cfg.browser_origins)
    async with AsyncClient(transport=ASGITransport(app=cors_app), base_url="http://test") as client:
        ok = await client.options(
            "/ping",
            headers={
                "Origin": "https://app.proplan.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == "https://app.proplan.example"
        assert ok.headers["access-control-allow-credentials"] == "true"

        blocked_get = await client.get("/ping", headers={"Origin": "https://evil.example"})
        assert blocked_get.status_code == 200
        assert "access-control-allow-origin" not in blocked_get.headers

        ok_get = await client.get("/ping", headers={"Origin": "https://app.proplan.example"})
        assert ok_get.status_code == 200
        assert ok_get.headers["access-control-allow-origin"] == "https://app.proplan.example"
        assert ok_get.headers["access-control-allow-credentials"] == "true"

        blocked = await client.options(
            "/ping",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert blocked.status_code == 400
        assert "access-control-allow-origin" not in blocked.headers


@pytest.mark.parametrize(
    "bad_origin",
    [
        "*",
        "https://*.proplan.example",
        "https://app.proplan.example/callback",
        "https://app.proplan.example?x=1",
        "https://app.proplan.example#frag",
        "https://user@app.proplan.example",
        "https://user:pass@app.proplan.example",
        "http://localhost:0",
        "ftp://files.proplan.example",
        "https://app.proplan.example\n",
        "https://app.proplan.example\r",
        "https://app.proplan.example\\evil",
        "https://app.proplan.example%2f",
        "https://app.proplan.example ",
        "https://app proplan.example",
        "http://localhost:8080@evil.com",
        "http://localhost:8080/",
        "https://localhost:8080:abc",
        "https://localhost:65536",
        "HTTP://localhost:8080",
        "http://@evil.com",
        "http://:pass@evil.com",
        "http://localhost:8080\t",
        "https://app%2eproplan.example",
        "http://localhost:8080?",
        "http://localhost:8080#",
        "",
    ],
)
def test_be02_validate_cors_origins_rejects_unsafe_values(bad_origin: str):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, CORS_ALLOWED_ORIGINS=[bad_origin])


# ---------------------------------------------------------------------------
# BE-05: OAuth Redirect Allowlist & Secret Fail-Fast
# ---------------------------------------------------------------------------


def test_be05_allowed_return_target_whitelist():
    assert _allowed_return_target("proplan://calendar/connected") == "proplan://calendar/connected"
    assert (
        _allowed_return_target("proplan://calendar/connected?tab=sync")
        == "proplan://calendar/connected?tab=sync"
    )
    assert (
        _allowed_return_target("http://localhost:8080/#/google-calendar")
        == "http://localhost:8080/#/google-calendar"
    )
    assert (
        _allowed_return_target("http://127.0.0.1:8080/#/google-calendar")
        == "http://127.0.0.1:8080/#/google-calendar"
    )

    for bad_target in (
        None,
        "",
        "https://evil.example",
        "https://evil.example/#/google-calendar",
        "javascript:alert(1)",
        "proplan://calendar/connected\r\nLocation: https://evil.example",
        "proplan://calendar/connected\n",
        "proplan://calendar/connected\\@evil.example",
        "http://localhost:8080\\#/google-calendar",
        "http://localhost:8080.evil.com/#/google-calendar",
        "http://evil.localhost:8080/#/google-calendar",
        "http://localhost:8080@evil.com/#/google-calendar",
        "proplan://calendar/connected@evil.com",
        "proplan://calendar/connected%0d%0aSet-Cookie:x",
        "http://localhost:8080/#/google-calendar/",
        "HTTP://LOCALHOST:8080/#/google-calendar",
        "//evil.com",
        "proplan://calendar/connected/",
        "proplan://user@calendar/connected",
        "http://localhost:8080/#/google-calendar?next=https://evil.com",
        "proplan://calendar/connected\x7f",
        "proplan://calendar/connected\t",
    ):
        assert _allowed_return_target(bad_target) is None, bad_target


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["/api/v1/calendar/oauth/callback", "/api/v1/calendar/oauth2callback"],
)
async def test_be05_oauth_callbacks_sanitize_redirect_on_success_and_error(tmp_path, endpoint: str):
    engine = make_engine("sqlite+aiosqlite:///" + (tmp_path / "be05_cb.db").as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def override_get_db():
        async with factory() as db:
            yield db

    uid = uuid4()
    async with factory() as db:
        db.add(User(id=uid, email="be05@example.com", display_name="BE05", is_active=True))
        await db.commit()

    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def create_state(target: str | None) -> str:
                async with factory() as db:
                    _, auth_url = await CalendarService(db).create_authorization(
                        user_id=uid, device_id="web-dev", return_target=target
                    )
                return parse_qs(urlparse(auth_url).query)["state"][0]

            # 1. Thành công + return_target Web hợp lệ
            st_web_ok = await create_state("http://localhost:8080/#/google-calendar")
            r_web_ok = await client.get(
                endpoint,
                params={"code": "fake-auth-code", "state": st_web_ok},
                follow_redirects=False,
            )
            assert r_web_ok.status_code == 302
            assert r_web_ok.headers["location"] == "http://localhost:8080/#/google-calendar"

            # 2. Thành công + return_target giả mạo -> ép fallback về MOBILE_RETURN_TARGET + connection_id
            st_evil_ok = await create_state("https://evil.example/phish")
            r_evil_ok = await client.get(
                endpoint,
                params={"code": "fake-auth-code", "state": st_evil_ok},
                follow_redirects=False,
            )
            assert r_evil_ok.status_code == 302
            loc_evil_ok = r_evil_ok.headers["location"]
            assert loc_evil_ok.startswith(f"{MOBILE_RETURN_TARGET}?")
            assert "evil.example" not in loc_evil_ok
            assert parse_qs(urlparse(loc_evil_ok).query)["connection_id"][0]

            # 3. Nhánh error=access_denied + return_target hợp lệ -> giữ target hợp lệ + error
            st_web_err = await create_state("http://localhost:8080/#/google-calendar")
            r_web_err = await client.get(
                endpoint,
                params={"error": "access_denied", "state": st_web_err},
                follow_redirects=False,
            )
            assert r_web_err.status_code == 302
            assert (
                r_web_err.headers["location"]
                == "http://localhost:8080/?error=access_denied#/google-calendar"
            )

            # 4. Nhánh error=access_denied + return_target giả mạo -> fallback MOBILE_RETURN_TARGET + error
            st_evil_err = await create_state("https://evil.example/steal")
            r_evil_err = await client.get(
                endpoint,
                params={"error": "access_denied", "state": st_evil_err},
                follow_redirects=False,
            )
            assert r_evil_err.status_code == 302
            loc_evil_err = r_evil_err.headers["location"]
            assert loc_evil_err == f"{MOBILE_RETURN_TARGET}?error=access_denied"
            assert "evil.example" not in loc_evil_err

            # 5. Bypass vectors qua OAuth callback -> luôn fallback an toàn về MOBILE_RETURN_TARGET
            for bypass_target in (
                "http://localhost:8080@evil.com/#/google-calendar",
                "proplan://calendar/connected%0d%0aSet-Cookie:x",
                "http://localhost:8080/#/google-calendar/",
                "HTTP://LOCALHOST:8080/#/google-calendar",
                "//evil.com",
            ):
                st_bypass = await create_state(bypass_target)
                r_bypass = await client.get(
                    endpoint,
                    params={"code": "fake-auth-code", "state": st_bypass},
                    follow_redirects=False,
                )
                assert r_bypass.status_code == 302, bypass_target
                loc_bypass = r_bypass.headers["location"]
                assert loc_bypass.startswith(f"{MOBILE_RETURN_TARGET}?connection_id="), (
                    bypass_target,
                    loc_bypass,
                )

        async with factory() as db:
            auth_rows = (await db.scalars(select(CalendarAuthorization))).all()
            conn_count = await db.scalar(select(func.count()).select_from(CalendarConnection))
            # create_authorization dọn các authorization đã consumed trước đó nên còn 1 bản ghi cuối cùng.
            assert len(auth_rows) == 1
            assert auth_rows[0].consumed_at is not None
            assert conn_count == 1
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        await engine.dispose()


@pytest.mark.parametrize("env_name", ["staging", "production"])
@pytest.mark.parametrize(
    "secret_key,cal_key",
    [
        (DEFAULT_SECRET_KEY, _VALID_FERNET),
        ("short-key", _VALID_FERNET),
        ("   ", _VALID_FERNET),
        (_STRONG_SECRET, ""),
        (_STRONG_SECRET, "not-a-valid-fernet-key"),
        (_STRONG_SECRET, "YWJj"),
    ],
)
def test_be05_deployed_secret_configuration_fails_fast(
    env_name: Literal["staging", "production"],
    secret_key: str,
    cal_key: str,
):
    cfg = Settings(
        _env_file=None,
        ENVIRONMENT=env_name,
        SECRET_KEY=secret_key,
        CALENDAR_CREDENTIAL_SECRET_KEY=cal_key,
    )
    with pytest.raises(ValueError):
        cfg.validate_secret_configuration()
    if secret_key == _STRONG_SECRET:
        with pytest.raises(ValueError):
            cfg.calendar_credential_key()


def test_be05_secret_valid_in_staging_and_fallback_in_dev_test(monkeypatch, caplog):
    deployed_cfg = Settings(
        _env_file=None,
        ENVIRONMENT="staging",
        SECRET_KEY=_STRONG_SECRET,
        CALENDAR_CREDENTIAL_SECRET_KEY=_VALID_FERNET,
    )
    deployed_cfg.validate_secret_configuration()
    assert deployed_cfg.calendar_credential_key() == _VALID_FERNET.encode("ascii")

    # Dev/test với CALENDAR_CREDENTIAL_SECRET_KEY không hợp lệ -> trả None, log warning, fallback SECRET_KEY
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing")
    monkeypatch.setattr(settings, "SECRET_KEY", _STRONG_SECRET)
    monkeypatch.setattr(settings, "CALENDAR_CREDENTIAL_SECRET_KEY", "invalid-fernet-format")
    assert settings.calendar_credential_key() is None

    payload = {
        "access_token": "tok-123",
        "refresh_token": "ref-456",
        "user_id": "user-1",
        "connection_id": "conn-1",
    }
    with caplog.at_level(logging.WARNING):
        encrypted = encrypt_credentials(payload)
        decrypted = decrypt_credentials(
            encrypted, expected_user_id="user-1", expected_connection_id="conn-1"
        )
    assert decrypted == payload
    assert any("CALENDAR_CREDENTIAL_SECRET_KEY không hợp lệ" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# W4-03A: seed_w4_qa.py Seed, Idempotency, Rollback, Isolation & CLI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w4_03a_seed_qa_populates_all_records_is_idempotent_and_isolated(
    sqlite_migration, auth_config
):
    url, migrate = sqlite_migration
    migrate("upgrade", "head")
    engine = make_engine(url)
    factory = session_factory(engine)

    uid_a = uuid4()
    uid_b = uuid4()
    email_a = "qa-a@example.test"
    email_b = "qa-b@example.test"

    try:
        async with factory() as db:
            db.add_all(
                [
                    User(
                        id=uid_a,
                        email=email_a,
                        google_sub="google-sub-qa-a",
                        display_name="QA A",
                        is_active=True,
                    ),
                    User(
                        id=uid_b,
                        email=email_b,
                        google_sub="google-sub-qa-b",
                        display_name="QA B",
                        is_active=True,
                    ),
                ]
            )
            await db.commit()

        dialect = await seed_w4_qa.seed_qa(url, (email_a, email_b))
        assert dialect == "sqlite"

        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(UserPreference)) == 2
            assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 4
            assert await db.scalar(select(func.count()).select_from(Task)) == 2
            assert await db.scalar(select(func.count()).select_from(Step)) == 2
            assert await db.scalar(select(func.count()).select_from(Session)) == 2
            assert await db.scalar(select(func.count()).select_from(SessionRun)) == 2
            assert await db.scalar(select(func.count()).select_from(CheckIn)) == 2

            for owner, label in ((uid_a, "A"), (uid_b, "B")):
                drafts = (
                    await db.scalars(
                        select(PlanningDraft).where(PlanningDraft.user_id == owner)
                    )
                ).all()
                assert {d.stage for d in drafts} == {"collecting", "confirmed"}
                assert all(f"[W4 QA {label}]" in (d.summary or {}).get("title", "") for d in drafts)

                task = await db.scalar(select(Task).where(Task.user_id == owner))
                step = await db.scalar(select(Step).where(Step.user_id == owner))
                sess = await db.scalar(select(Session).where(Session.user_id == owner))
                run = await db.scalar(select(SessionRun).where(SessionRun.user_id == owner))
                checkin = await db.scalar(select(CheckIn).where(CheckIn.user_id == owner))
                assert task is not None and f"[W4 QA {label}]" in task.title
                assert task.status == "completed"
                assert step is not None and step.status == "completed"
                assert sess is not None and sess.status == "completed"
                assert run is not None and run.run_state == "ended" and run.outcome == "completed"
                assert checkin is not None and checkin.response_state == "answered"
                assert checkin.session_outcome == "completed"
                assert checkin.step_completed is True and checkin.task_completed is True

            # Sửa UserPreference của A trước khi chạy lại lần 2 -> phải được giữ nguyên
            pref_a = await db.scalar(select(UserPreference).where(UserPreference.user_id == uid_a))
            assert pref_a is not None
            pref_a.work_interval_seconds = 1800
            await db.commit()

        await seed_w4_qa.seed_qa(url, (email_a, email_b))

        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(UserPreference)) == 2
            assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 4
            assert await db.scalar(select(func.count()).select_from(Task)) == 2
            assert await db.scalar(select(func.count()).select_from(Step)) == 2
            assert await db.scalar(select(func.count()).select_from(Session)) == 2
            assert await db.scalar(select(func.count()).select_from(SessionRun)) == 2
            assert await db.scalar(select(func.count()).select_from(CheckIn)) == 2
            pref_a_after = await db.scalar(
                select(UserPreference).where(UserPreference.user_id == uid_a)
            )
            assert pref_a_after is not None and pref_a_after.work_interval_seconds == 1800

            task_a_id = str(await db.scalar(select(Task.id).where(Task.user_id == uid_a)))
            task_b_id = str(await db.scalar(select(Task.id).where(Task.user_id == uid_b)))

        # Kiểm tra cô lập dữ liệu A/B qua API thực tế
        async def override_get_db():
            async with factory() as db:
                yield db

        old_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = override_get_db
        try:
            headers_a = {"Authorization": f"Bearer {issue_app_access_token(uid_a).access_token}"}
            headers_b = {"Authorization": f"Bearer {issue_app_access_token(uid_b).access_token}"}
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get(f"/api/v1/tasks/{task_a_id}", headers=headers_a)).status_code == 200
                assert (await client.get(f"/api/v1/tasks/{task_b_id}", headers=headers_a)).status_code == 404
                assert (await client.get(f"/api/v1/tasks/{task_b_id}", headers=headers_b)).status_code == 200
                assert (await client.get(f"/api/v1/tasks/{task_a_id}", headers=headers_b)).status_code == 404

                drafts_a = (await client.get("/api/v1/drafts", headers=headers_a)).json()["data"]["items"]
                drafts_b = (await client.get("/api/v1/drafts", headers=headers_b)).json()["data"]["items"]
                assert drafts_a and all(
                    "[W4 QA A]" in (d.get("summary") or {}).get("title", "") for d in drafts_a
                )
                assert drafts_b and all(
                    "[W4 QA B]" in (d.get("summary") or {}).get("title", "") for d in drafts_b
                )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_b_kwargs",
    [
        None,  # thiếu user B
        {"is_active": False, "google_sub": "sub-b"},  # user B inactive
        {"is_active": True, "google_sub": ""},  # google_sub rỗng
        {"is_active": True, "google_sub": None},  # google_sub null
    ],
)
async def test_w4_03a_seed_qa_rolls_back_cleanly_when_second_user_invalid(
    sqlite_migration, user_b_kwargs: dict | None
):
    url, migrate = sqlite_migration
    migrate("upgrade", "head")
    engine = make_engine(url)
    factory = session_factory(engine)

    email_a = "valid-a@example.test"
    email_b = "invalid-b@example.test"

    try:
        async with factory() as db:
            db.add(
                User(
                    id=uuid4(),
                    email=email_a,
                    google_sub="sub-valid-a",
                    display_name="Valid A",
                    is_active=True,
                )
            )
            if user_b_kwargs is not None:
                db.add(
                    User(
                        id=uuid4(),
                        email=email_b,
                        display_name="Invalid B",
                        **user_b_kwargs,
                    )
                )
            await db.commit()

        with pytest.raises(ValueError, match="Chờ đăng nhập Google"):
            await seed_w4_qa.seed_qa(url, (email_a, email_b))

        async with factory() as db:
            for model in (UserPreference, PlanningDraft, Task, Step, Session, SessionRun, CheckIn):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
    finally:
        await engine.dispose()


def test_w4_03a_cli_main_rejects_duplicate_emails_and_missing_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(sys, "argv", ["seed_w4_qa.py", "QA@Example.com", "qa@example.com"])
    with pytest.raises(SystemExit) as dup_exc:
        seed_w4_qa.main()
    assert dup_exc.value.code != 0

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["seed_w4_qa.py", "a@example.com", "b@example.com"])
    with pytest.raises(SystemExit) as env_exc:
        seed_w4_qa.main()
    assert env_exc.value.code != 0


# ---------------------------------------------------------------------------
# BE-05 (#6): Calendar Sync Worker SQLite File Concurrency & Write Lock Tests
# ---------------------------------------------------------------------------


def _build_sqlite_worker_connection(
    *,
    user_id: UUID,
    connection_id: UUID,
    account_ref: str,
    write_calendar_id: str,
    now: datetime,
) -> CalendarConnection:
    cred_ref = encrypt_credentials(
        {
            "access_token": f"fake-access-{account_ref}",
            "refresh_token": f"fake-refresh-{account_ref}",
            "user_id": str(user_id),
            "connection_id": str(connection_id),
            "stored_at": now.isoformat(),
            "expires_in": 3600,
        }
    )
    return CalendarConnection(
        id=connection_id,
        user_id=user_id,
        provider="google",
        account_ref=account_ref,
        read_calendar_ids=["primary"],
        write_calendar_id=write_calendar_id,
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        connection_state="connected",
        credential_ref=cred_ref,
    )


async def _seed_sqlite_worker_task_step(db, *, user_id: UUID, now: datetime) -> Step:
    task = Task(
        id=uuid4(),
        user_id=user_id,
        title="Task đồng bộ lịch SQLite",
        time_constraint={
            "kind": "deadline",
            "at": (now + timedelta(days=2)).isoformat(),
            "input_timezone": "Asia/Ho_Chi_Minh",
        },
        status="scheduled",
    )
    db.add(task)
    await db.flush()
    step = Step(
        id=uuid4(),
        user_id=user_id,
        task_id=task.id,
        title="Step kiểm thử khóa ghi",
        order_index=0,
        estimated_base_seconds=1500,
        remaining_base_seconds=1500,
        buffer_multiplier=1.2,
        estimate_source="manual",
    )
    db.add(step)
    await db.flush()
    return step


async def _add_sqlite_worker_session(
    db, *, user_id: UUID, step_id: UUID, start_at: datetime
) -> Session:
    sess = Session(
        id=uuid4(),
        user_id=user_id,
        step_id=step_id,
        scheduled_start=start_at,
        scheduled_end=start_at + timedelta(minutes=25),
        work_seconds=1500,
        rest_seconds=0,
        duration_seconds=1500,
        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
        schedule_revision=1,
    )
    db.add(sess)
    await db.flush()
    return sess


@pytest.mark.asyncio
async def test_be05_calendar_sync_worker_sqlite_concurrency_no_database_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Nhiều coroutine process_calendar_sync_jobs chạy song song trên SQLite file DB không lỗi locked hay trùng event."""
    reset_fake_calendar_client()
    monkeypatch.setattr(settings, "CALENDAR_PROVIDER_MODE", "fake")
    fake_client = get_calendar_client()
    assert isinstance(fake_client, FakeCalendarClient)

    db_url = "sqlite+aiosqlite:///" + (tmp_path / "worker_sqlite_lock.db").as_posix()
    engine = make_engine(db_url)
    factory = session_factory(engine)
    now = datetime.now(UTC)
    uid = uuid4()
    conn_id = uuid4()
    cal_id = "focus_cal"

    orig_create = fake_client.create_event
    orig_update = fake_client.update_event
    create_calls: list[str] = []
    update_calls: list[str] = []

    async def slow_create(access_token: str, calendar_id: str, event_payload: dict):
        create_calls.append(str(event_payload.get("summary", "")))
        await asyncio.sleep(0.015)
        return await orig_create(access_token, calendar_id, event_payload)

    async def slow_update(
        access_token: str,
        calendar_id: str,
        event_id: str,
        event_payload: dict,
        expected_etag: str | None = None,
    ):
        update_calls.append(str(event_payload.get("summary", "")))
        await asyncio.sleep(0.015)
        return await orig_update(
            access_token, calendar_id, event_id, event_payload, expected_etag=expected_etag
        )

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Tạo 1 event có sẵn trước trên fake client để kiểm thử job update song song
        seeded_event = await orig_create("fake-tok", cal_id, {"summary": "Focus Existing"})

        async with factory() as db:
            db.add(
                User(
                    id=uid,
                    email="worker-lock@example.com",
                    display_name="Worker Lock",
                    is_active=True,
                )
            )
            await db.flush()
            db.add(
                _build_sqlite_worker_connection(
                    user_id=uid,
                    connection_id=conn_id,
                    account_ref="worker-lock@gmail.com",
                    write_calendar_id=cal_id,
                    now=now,
                )
            )
            await db.flush()
            step = await _seed_sqlite_worker_task_step(db, user_id=uid, now=now)

            s_dup = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=1)
            )
            s_c1 = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=2)
            )
            s_c2 = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=3)
            )
            s_upd = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=4)
            )

            link_dup = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_dup.id,
                connection_id=conn_id,
                google_calendar_id=cal_id,
                desired_schedule_revision=2,
                sync_state="pending",
                is_current=True,
            )
            link_c1 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_c1.id,
                connection_id=conn_id,
                google_calendar_id=cal_id,
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            link_c2 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_c2.id,
                connection_id=conn_id,
                google_calendar_id=cal_id,
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            link_upd = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_upd.id,
                connection_id=conn_id,
                google_calendar_id=cal_id,
                google_event_id=seeded_event["id"],
                google_etag=seeded_event["etag"],
                desired_schedule_revision=2,
                synced_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            db.add_all([link_dup, link_c1, link_c2, link_upd])
            await db.flush()

            # Gồm cả 2 job queued cùng 1 event_link_id (create trước, update sau) và các link song song
            jobs = [
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn_id,
                    event_link_id=link_dup.id,
                    target_kind="session",
                    target_ref=str(s_dup.id),
                    operation="create",
                    target_schedule_revision=1,
                    payload_snapshot={"summary": "Dup Create"},
                    idempotency_key="lock-dup-create",
                    job_state="queued",
                    attempt_count=0,
                    created_at=now - timedelta(seconds=30),
                ),
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn_id,
                    event_link_id=link_c1.id,
                    target_kind="session",
                    target_ref=str(s_c1.id),
                    operation="create",
                    target_schedule_revision=1,
                    payload_snapshot={"summary": "Create 1"},
                    idempotency_key="lock-create-1",
                    job_state="queued",
                    attempt_count=0,
                    created_at=now - timedelta(seconds=20),
                ),
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn_id,
                    event_link_id=link_c2.id,
                    target_kind="session",
                    target_ref=str(s_c2.id),
                    operation="create",
                    target_schedule_revision=1,
                    payload_snapshot={"summary": "Create 2"},
                    idempotency_key="lock-create-2",
                    job_state="queued",
                    attempt_count=0,
                    created_at=now - timedelta(seconds=15),
                ),
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn_id,
                    event_link_id=link_upd.id,
                    target_kind="session",
                    target_ref=str(s_upd.id),
                    operation="update",
                    target_schedule_revision=2,
                    expected_google_etag=seeded_event["etag"],
                    payload_snapshot={"summary": "Update Existing"},
                    idempotency_key="lock-update-1",
                    job_state="queued",
                    attempt_count=0,
                    created_at=now - timedelta(seconds=10),
                ),
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn_id,
                    event_link_id=link_dup.id,
                    target_kind="session",
                    target_ref=str(s_dup.id),
                    operation="update",
                    target_schedule_revision=2,
                    payload_snapshot={"summary": "Dup Update"},
                    idempotency_key="lock-dup-update",
                    job_state="queued",
                    attempt_count=0,
                    created_at=now - timedelta(seconds=5),
                ),
            ]
            db.add_all(jobs)
            await db.commit()

        monkeypatch.setattr(fake_client, "create_event", slow_create)
        monkeypatch.setattr(fake_client, "update_event", slow_update)

        async def run_worker(limit: int, skip_busy: bool) -> int:
            async with factory() as db:
                return await process_calendar_sync_jobs(
                    db,
                    user_id=uid,
                    limit=limit,
                    skip_busy_connections=skip_busy,
                )

        round1 = await asyncio.gather(
            run_worker(1, False),
            run_worker(10, False),
            run_worker(10, False),
            run_worker(10, False),
        )
        round2 = await asyncio.gather(
            run_worker(10, False),
            run_worker(10, False),
        )
        assert sum(round1) + sum(round2) == 5

        assert sorted(create_calls) == ["Create 1", "Create 2", "Dup Create"]
        assert sorted(update_calls) == ["Dup Update", "Update Existing"]
        assert len(fake_client.mock_events.get(cal_id, [])) == 4

        async with factory() as db:
            db_jobs = (await db.scalars(select(CalendarSyncJob))).all()
            db_links = (await db.scalars(select(CalendarEventLink))).all()
            assert len(db_jobs) == 5
            for job in db_jobs:
                assert job.job_state == "completed"
                assert job.attempt_count == 0
                assert job.last_error_code is None
                assert job.lease_expires_at is None

            assert len(db_links) == 4
            for link in db_links:
                assert link.sync_state == "synced"
                assert link.synced_schedule_revision == link.desired_schedule_revision
                assert link.google_event_id is not None
                assert link.google_etag is not None
    finally:
        reset_fake_calendar_client()
        await engine.dispose()


@pytest.mark.asyncio
async def test_be05_calendar_sync_worker_sqlite_skip_busy_connections(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Kiểm thử skip_busy_connections=True bỏ qua connection đang giữ lock mà không chặn hay lỗi SQLite."""
    reset_fake_calendar_client()
    monkeypatch.setattr(settings, "CALENDAR_PROVIDER_MODE", "fake")
    fake_client = get_calendar_client()
    assert isinstance(fake_client, FakeCalendarClient)

    db_url = "sqlite+aiosqlite:///" + (tmp_path / "worker_sqlite_skip_busy.db").as_posix()
    engine = make_engine(db_url)
    factory = session_factory(engine)
    now = datetime.now(UTC)
    uid = uuid4()
    conn_busy_id = uuid4()
    conn_free_id = uuid4()
    cal_busy = "cal_busy"
    cal_free = "cal_free"

    orig_create = fake_client.create_event
    busy_entered = asyncio.Event()
    release_busy = asyncio.Event()
    created_summaries: list[str] = []

    async def controlled_create(access_token: str, calendar_id: str, event_payload: dict):
        summary = str(event_payload.get("summary", ""))
        created_summaries.append(summary)
        if summary == "Busy First":
            busy_entered.set()
            await asyncio.wait_for(release_busy.wait(), timeout=5.0)
        else:
            await asyncio.sleep(0.01)
        return await orig_create(access_token, calendar_id, event_payload)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with factory() as db:
            db.add(
                User(
                    id=uid,
                    email="worker-skip@example.com",
                    display_name="Worker Skip",
                    is_active=True,
                )
            )
            await db.flush()
            db.add_all(
                [
                    _build_sqlite_worker_connection(
                        user_id=uid,
                        connection_id=conn_busy_id,
                        account_ref="busy@gmail.com",
                        write_calendar_id=cal_busy,
                        now=now,
                    ),
                    _build_sqlite_worker_connection(
                        user_id=uid,
                        connection_id=conn_free_id,
                        account_ref="free@gmail.com",
                        write_calendar_id=cal_free,
                        now=now,
                    ),
                ]
            )
            await db.flush()
            step = await _seed_sqlite_worker_task_step(db, user_id=uid, now=now)

            s_busy_1 = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=1)
            )
            s_busy_2 = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=2)
            )
            s_free = await _add_sqlite_worker_session(
                db, user_id=uid, step_id=step.id, start_at=now + timedelta(hours=3)
            )

            l_busy_1 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_busy_1.id,
                connection_id=conn_busy_id,
                google_calendar_id=cal_busy,
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            l_busy_2 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_busy_2.id,
                connection_id=conn_busy_id,
                google_calendar_id=cal_busy,
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            l_free = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_free.id,
                connection_id=conn_free_id,
                google_calendar_id=cal_free,
                desired_schedule_revision=1,
                sync_state="pending",
                is_current=True,
            )
            db.add_all([l_busy_1, l_busy_2, l_free])
            await db.flush()

            db.add_all(
                [
                    CalendarSyncJob(
                        id=uuid4(),
                        user_id=uid,
                        connection_id=conn_busy_id,
                        event_link_id=l_busy_1.id,
                        target_kind="session",
                        target_ref=str(s_busy_1.id),
                        operation="create",
                        target_schedule_revision=1,
                        payload_snapshot={"summary": "Busy First"},
                        idempotency_key="skip-busy-1",
                        job_state="queued",
                        attempt_count=0,
                        created_at=now - timedelta(seconds=20),
                    ),
                    CalendarSyncJob(
                        id=uuid4(),
                        user_id=uid,
                        connection_id=conn_busy_id,
                        event_link_id=l_busy_2.id,
                        target_kind="session",
                        target_ref=str(s_busy_2.id),
                        operation="create",
                        target_schedule_revision=1,
                        payload_snapshot={"summary": "Busy Second"},
                        idempotency_key="skip-busy-2",
                        job_state="queued",
                        attempt_count=0,
                        created_at=now - timedelta(seconds=10),
                    ),
                    CalendarSyncJob(
                        id=uuid4(),
                        user_id=uid,
                        connection_id=conn_free_id,
                        event_link_id=l_free.id,
                        target_kind="session",
                        target_ref=str(s_free.id),
                        operation="create",
                        target_schedule_revision=1,
                        payload_snapshot={"summary": "Free First"},
                        idempotency_key="skip-free-1",
                        job_state="queued",
                        attempt_count=0,
                        created_at=now - timedelta(seconds=5),
                    ),
                ]
            )
            await db.commit()

        monkeypatch.setattr(fake_client, "create_event", controlled_create)

        async def run_busy_first() -> int:
            async with factory() as db:
                return await process_calendar_sync_jobs(
                    db,
                    user_id=uid,
                    connection_id=conn_busy_id,
                    limit=1,
                    skip_busy_connections=False,
                )

        async def run_skip_while_busy() -> int:
            await asyncio.wait_for(busy_entered.wait(), timeout=2.0)
            try:
                async with factory() as db:
                    return await process_calendar_sync_jobs(
                        db,
                        user_id=uid,
                        limit=10,
                        skip_busy_connections=True,
                    )
            finally:
                release_busy.set()

        processed_busy, processed_skip = await asyncio.gather(
            run_busy_first(),
            run_skip_while_busy(),
        )
        assert processed_busy == 1
        assert processed_skip == 1

        async with factory() as db:
            remaining = await process_calendar_sync_jobs(
                db, user_id=uid, limit=10, skip_busy_connections=True
            )
            assert remaining == 1

            db_jobs = (await db.scalars(select(CalendarSyncJob))).all()
            db_links = (await db.scalars(select(CalendarEventLink))).all()
            assert all(j.job_state == "completed" and j.attempt_count == 0 for j in db_jobs)
            assert all(link.sync_state == "synced" for link in db_links)

        assert sorted(created_summaries) == ["Busy First", "Busy Second", "Free First"]
        assert len(fake_client.mock_events.get(cal_busy, [])) == 2
        assert len(fake_client.mock_events.get(cal_free, [])) == 1
    finally:
        reset_fake_calendar_client()
        await engine.dispose()
