"""Tests for W4 staging security hardening (BE-02 CORS, BE-05 OAuth/Secret) and W4-03A QA seed."""

from __future__ import annotations

import logging
import sys
from typing import Literal
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

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
from app.services.credential_crypto import decrypt_credentials, encrypt_credentials
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
