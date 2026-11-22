"""Unit & integration tests for BE-03 (refresh token rotation, reuse detection, logout)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db
from app.api.v1.auth import get_google_verifier
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.core.errors import AppError
from app.core.security import decode_app_refresh_token, issue_app_access_token
from app.main import app
from app.models import Base, RefreshToken, User, UserPreference
from app.schemas.auth import VerifiedGoogleIdentity


@pytest.fixture
async def be03_api(tmp_path, auth_config):
    pg_url = os.environ.get("PROACTIVE_TEST_PG_URL", "").strip()
    url = pg_url if pg_url else "sqlite+aiosqlite:///" + (tmp_path / "be03.db").as_posix()
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def override_get_db():
        async with factory() as db:
            yield db

    tag = uuid4().hex[:8]
    identities = {
        "a": VerifiedGoogleIdentity(
            sub=f"be03-sub-a-{tag}",
            email=f"be03-a-{tag}@example.com",
            display_name="User A",
        ),
        "b": VerifiedGoogleIdentity(
            sub=f"be03-sub-b-{tag}",
            email=f"be03-b-{tag}@example.com",
            display_name="User B",
        ),
    }

    async def verify(token: str) -> VerifiedGoogleIdentity:
        if token not in identities:
            raise AppError(
                status_code=401,
                code="INVALID_GOOGLE_ID_TOKEN",
                message="Invalid Google token",
                next_action="authenticate",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return identities[token]

    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_google_verifier] = lambda: verify
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        if pg_url:
            subs = [ident.sub for ident in identities.values()]
            async with factory() as db:
                uids = list(await db.scalars(select(User.id).where(User.google_sub.in_(subs))))
                if uids:
                    await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(uids)))
                    await db.execute(delete(UserPreference).where(UserPreference.user_id.in_(uids)))
                    await db.execute(delete(User).where(User.id.in_(uids)))
                    await db.commit()
        await engine.dispose()


async def _login_user(client: AsyncClient, token_key: str = "a") -> dict:
    resp = await client.post("/api/v1/auth/google", json={"id_token": token_key})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_login_persists_refresh_token_and_rotates_jti_in_same_family(be03_api):
    client, factory = be03_api
    login_data = await _login_user(client, "a")
    uid = UUID(login_data["user"]["user_id"])
    refresh_1 = login_data["refresh_token"]
    assert refresh_1 is not None
    claims_1 = decode_app_refresh_token(refresh_1)

    async with factory() as db:
        row_1 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_1.jti))
        assert row_1 is not None
        assert row_1.user_id == uid
        assert row_1.family_id is not None
        assert row_1.issued_at < row_1.expires_at
        assert row_1.revoked_at is None
        assert row_1.revoked_reason is None
        assert row_1.replaced_by_jti is None
        family_id = row_1.family_id

    # Lần refresh thứ 1 xoay jti và giữ nguyên family_id
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert r1.status_code == 200, r1.text
    assert r1.headers.get("cache-control") == "no-store"
    bundle_2 = r1.json()["data"]
    refresh_2 = bundle_2["refresh_token"]
    assert refresh_2 and refresh_2 != refresh_1
    claims_2 = decode_app_refresh_token(refresh_2)
    assert claims_2.jti != claims_1.jti

    async with factory() as db:
        old_row = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_1.jti))
        new_row = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_2.jti))
        assert old_row is not None and new_row is not None
        assert old_row.revoked_at is not None
        assert old_row.revoked_reason == "rotated"
        assert old_row.replaced_by_jti == claims_2.jti
        assert new_row.user_id == uid
        assert new_row.family_id == family_id
        assert new_row.revoked_at is None
        assert new_row.replaced_by_jti is None

    # Token mới tiếp tục refresh thành công sang jti thứ 3
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_2})
    assert r2.status_code == 200, r2.text
    refresh_3 = r2.json()["data"]["refresh_token"]
    claims_3 = decode_app_refresh_token(refresh_3)
    assert claims_3.jti not in {claims_1.jti, claims_2.jti}

    async with factory() as db:
        row_2 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_2.jti))
        row_3 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_3.jti))
        assert row_2 is not None and row_3 is not None
        assert row_2.revoked_reason == "rotated"
        assert row_2.replaced_by_jti == claims_3.jti
        assert row_3.family_id == family_id
        assert row_3.revoked_at is None


@pytest.mark.asyncio
async def test_refresh_reuse_detection_revokes_entire_family_only(be03_api):
    client, factory = be03_api

    # Hai phiên đăng nhập độc lập của cùng user A -> hai family_id tách biệt
    session_a = await _login_user(client, "a")
    session_b = await _login_user(client, "a")
    refresh_a1 = session_a["refresh_token"]
    refresh_b1 = session_b["refresh_token"]
    claims_a1 = decode_app_refresh_token(refresh_a1)
    claims_b1 = decode_app_refresh_token(refresh_b1)

    rot_a = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a1})
    assert rot_a.status_code == 200
    refresh_a2 = rot_a.json()["data"]["refresh_token"]
    claims_a2 = decode_app_refresh_token(refresh_a2)

    # Reuse lại token cũ refresh_a1 -> 401 UNAUTHENTICATED + WWW-Authenticate: Bearer
    reuse_resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a1})
    assert reuse_resp.status_code == 401
    assert reuse_resp.headers["www-authenticate"] == "Bearer"
    assert reuse_resp.headers.get("cache-control") == "no-store"
    assert reuse_resp.json()["error"]["code"] == "UNAUTHENTICATED"

    # Kiểm tra DB: cả family_id của session A (bao gồm token mới refresh_a2) đã bị thu hồi
    async with factory() as db:
        row_a1 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_a1.jti))
        row_a2 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_a2.jti))
        row_b1 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_b1.jti))
        assert row_a1 is not None and row_a2 is not None and row_b1 is not None
        assert row_a1.family_id == row_a2.family_id
        assert row_a1.family_id != row_b1.family_id
        assert row_a1.revoked_at is not None
        assert row_a1.revoked_reason == "rotated"
        assert row_a2.revoked_at is not None
        assert row_a2.revoked_reason == "reuse_detected"
        assert row_b1.revoked_at is None

    # Token mới refresh_a2 cũng bị chặn 401
    blocked_new = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a2})
    assert blocked_new.status_code == 401
    assert blocked_new.headers["www-authenticate"] == "Bearer"
    assert blocked_new.json()["error"]["code"] == "UNAUTHENTICATED"

    # Family khác (session B) vẫn hoạt động bình thường
    ok_b = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_b1})
    assert ok_b.status_code == 200


@pytest.mark.asyncio
async def test_logout_is_public_idempotent_and_blocks_subsequent_refresh(be03_api):
    client, factory = be03_api
    login_data = await _login_user(client, "a")
    uid = login_data["user"]["user_id"]
    refresh_1 = login_data["refresh_token"]

    rot = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert rot.status_code == 200
    refresh_2 = rot.json()["data"]["refresh_token"]
    claims_2 = decode_app_refresh_token(refresh_2)

    # Tạo một access token đã hết hạn để mô phỏng client gửi logout khi access token vừa quá hạn
    now_ts = int(datetime.now(UTC).timestamp())
    expired_access = jwt.encode(
        {
            "sub": uid,
            "iat": now_ts - 7200,
            "exp": now_ts - 3600,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "token_type": "access",
        },
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )

    # Lần 1: không gửi header Authorization vẫn logout thành công với revoked=true
    logout_1 = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_2})
    assert logout_1.status_code == 200, logout_1.text
    assert logout_1.headers.get("cache-control") == "no-store"
    assert logout_1.json()["data"] == {"logged_out": True, "revoked": True}

    async with factory() as db:
        row_2 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_2.jti))
        assert row_2 is not None
        assert row_2.revoked_at is not None
        assert row_2.revoked_reason == "logout"

    # Lần 2 (retry, kể cả khi đính kèm access token đã hết hạn): vẫn 200 idempotent với revoked=false
    logout_2 = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_2},
        headers={"Authorization": f"Bearer {expired_access}"},
    )
    assert logout_2.status_code == 200, logout_2.text
    assert logout_2.headers.get("cache-control") == "no-store"
    assert logout_2.json()["data"] == {"logged_out": True, "revoked": False}

    # Dùng lại refresh_2 sau logout -> 401 UNAUTHENTICATED
    after_logout = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_2})
    assert after_logout.status_code == 401
    assert after_logout.headers["www-authenticate"] == "Bearer"
    assert after_logout.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_refresh_and_logout_error_matrix(be03_api, monkeypatch):
    client, factory = be03_api
    login_a = await _login_user(client, "a")
    login_b = await _login_user(client, "b")
    uid_a = UUID(login_a["user"]["user_id"])
    uid_b = UUID(login_b["user"]["user_id"])
    refresh_a = login_a["refresh_token"]
    claims_a = decode_app_refresh_token(refresh_a)
    now_ts = int(datetime.now(UTC).timestamp())

    wrong_sig = jwt.encode(
        {
            "sub": str(uid_a),
            "iat": now_ts - 10,
            "exp": now_ts + 3600,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "token_type": "refresh",
            "jti": str(uuid4()),
        },
        "completely-wrong-signing-key-at-least-32-bytes",
        algorithm="HS256",
    )
    expired_refresh = jwt.encode(
        {
            "sub": str(uid_a),
            "iat": now_ts - 7200,
            "exp": now_ts - 60,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "token_type": "refresh",
            "jti": claims_a.jti,
        },
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )
    # Token mang jti của user A nhưng sub là user B -> mismatch user_id -> 401
    mismatched_owner = jwt.encode(
        {
            "sub": str(uid_b),
            "iat": now_ts - 10,
            "exp": now_ts + 3600,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "token_type": "refresh",
            "jti": claims_a.jti,
        },
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )
    unregistered_refresh = issue_app_access_token(uid_a).refresh_token
    ghost_user_refresh = issue_app_access_token(uuid4()).refresh_token
    assert unregistered_refresh is not None and ghost_user_refresh is not None

    for endpoint in ("/api/v1/auth/refresh", "/api/v1/auth/logout"):
        # 401: giả / hết hạn / access token / jti chưa có trong DB / sai owner / user không tồn tại
        for bad_tok in (
            wrong_sig,
            expired_refresh,
            login_a["access_token"],
            unregistered_refresh,
            mismatched_owner,
            ghost_user_refresh,
        ):
            r401 = await client.post(endpoint, json={"refresh_token": bad_tok})
            assert r401.status_code == 401, (endpoint, r401.status_code, r401.text)
            assert r401.headers["www-authenticate"] == "Bearer"
            assert r401.headers.get("cache-control") == "no-store"
            assert r401.json()["error"]["code"] == "UNAUTHENTICATED"

        # 422: thiếu field / rỗng / blank / padded / thừa field (extra="forbid")
        for bad_payload in (
            {},
            {"refresh_token": ""},
            {"refresh_token": "   "},
            {"refresh_token": f" {refresh_a} "},
            {"refresh_token": refresh_a, "unexpected": "field"},
        ):
            r422 = await client.post(endpoint, json=bad_payload)
            assert r422.status_code == 422, (endpoint, bad_payload, r422.status_code)
            assert r422.headers.get("cache-control") == "no-store"
            assert r422.json()["error"]["code"] == "VALIDATION_ERROR"

    # Dòng DB expires_at đã qua dù JWT còn hạn -> /auth/refresh trả 401
    expired_db_bundle = issue_app_access_token(uid_a)
    assert expired_db_bundle.refresh_token is not None
    expired_db_claims = decode_app_refresh_token(expired_db_bundle.refresh_token)
    async with factory() as db:
        db.add(
            RefreshToken(
                user_id=uid_a,
                jti=expired_db_claims.jti,
                family_id=uuid4(),
                issued_at=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
        await db.commit()
    r_db_exp = await client.post("/api/v1/auth/refresh", json={"refresh_token": expired_db_bundle.refresh_token})
    assert r_db_exp.status_code == 401
    assert r_db_exp.json()["error"]["code"] == "UNAUTHENTICATED"

    # 403 ACCOUNT_DISABLED khi user bị vô hiệu hóa
    async with factory() as db:
        user_a = await db.get(User, uid_a)
        assert user_a is not None
        user_a.is_active = False
        await db.commit()

    for endpoint in ("/api/v1/auth/refresh", "/api/v1/auth/logout"):
        r403 = await client.post(endpoint, json={"refresh_token": refresh_a})
        assert r403.status_code == 403, (endpoint, r403.status_code, r403.text)
        assert r403.json()["error"]["code"] == "ACCOUNT_DISABLED"

    # 503 AUTH_PROVIDER_UNAVAILABLE khi thiếu JWT_SECRET_KEY
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "")
    for endpoint in ("/api/v1/auth/refresh", "/api/v1/auth/logout"):
        r503 = await client.post(endpoint, json={"refresh_token": refresh_a})
        assert r503.status_code == 503, (endpoint, r503.status_code, r503.text)
        assert r503.json()["error"]["code"] == "AUTH_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_concurrent_double_refresh_race_revokes_family_on_sqlite_and_pg16(be03_api):
    """Concurrent double-refresh race (BE-03) phải trả [200, 401] và thu hồi token mới bằng reuse_detected trên cả SQLite và PG16."""
    client, factory = be03_api
    login_data = await _login_user(client, "a")
    refresh_1 = login_data["refresh_token"]
    assert refresh_1 is not None
    claims_1 = decode_app_refresh_token(refresh_1)

    r1, r2 = await asyncio.gather(
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1}),
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1}),
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 401], (
        f"[BUG] Expected [200, 401] on concurrent double-refresh, got {statuses}: "
        f"r1={r1.status_code} {r1.text}, r2={r2.status_code} {r2.text}"
    )

    winner, loser = (r1, r2) if r1.status_code == 200 else (r2, r1)
    assert loser.headers.get("www-authenticate") == "Bearer"
    assert loser.headers.get("cache-control") == "no-store"
    assert loser.json()["error"]["code"] == "UNAUTHENTICATED"

    refresh_2 = winner.json()["data"]["refresh_token"]
    assert refresh_2 is not None
    claims_2 = decode_app_refresh_token(refresh_2)

    async with factory() as db:
        row_1 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_1.jti))
        assert row_1 is not None
        family_rows = list(
            await db.scalars(
                select(RefreshToken)
                .where(RefreshToken.family_id == row_1.family_id)
                .order_by(RefreshToken.created_at)
            )
        )
        assert len(family_rows) == 2
        replaced_rows = [row for row in family_rows if row.replaced_by_jti is not None]
        assert len(replaced_rows) == 1
        assert replaced_rows[0].jti == claims_1.jti
        assert replaced_rows[0].replaced_by_jti == claims_2.jti
        assert replaced_rows[0].revoked_reason == "rotated"
        # Vì request thua cuộc đọc sau commit thấy reuse -> token mới cũng bị thu hồi reuse_detected
        assert all(row.revoked_at is not None for row in family_rows)
        row_2 = next(row for row in family_rows if row.jti == claims_2.jti)
        assert row_2.revoked_reason == "reuse_detected"

    follow_up = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_2})
    assert follow_up.status_code == 401
    assert follow_up.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_concurrent_double_logout_returns_single_revoked_true(be03_api):
    """Hai request POST /api/v1/auth/logout đồng thời trên cùng token active phải tuần tự hóa, trả đúng [False, True]."""
    client, factory = be03_api
    login_data = await _login_user(client, "a")
    refresh_1 = login_data["refresh_token"]
    assert refresh_1 is not None
    claims_1 = decode_app_refresh_token(refresh_1)

    r1, r2 = await asyncio.gather(
        client.post("/api/v1/auth/logout", json={"refresh_token": refresh_1}),
        client.post("/api/v1/auth/logout", json={"refresh_token": refresh_1}),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["data"]["logged_out"] is True
    assert r2.json()["data"]["logged_out"] is True
    assert sorted(r.json()["data"]["revoked"] for r in (r1, r2)) == [False, True]

    async with factory() as db:
        row_1 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == claims_1.jti))
        assert row_1 is not None
        assert row_1.revoked_at is not None
        assert row_1.revoked_reason == "logout"

    after_logout = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert after_logout.status_code == 401
    assert after_logout.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_cross_family_isolation_on_logout_and_reuse(be03_api):
    """Cùng 1 user đăng nhập 3 phiên: logout family_1 & reuse family_3 không làm thu hồi family_2."""
    client, factory = be03_api

    s1 = await _login_user(client, "a")
    s2 = await _login_user(client, "a")
    s3 = await _login_user(client, "a")

    r1_a = s1["refresh_token"]
    r2_a = s2["refresh_token"]
    r3_a = s3["refresh_token"]
    assert r1_a and r2_a and r3_a

    # Xoay mỗi family 1 lần
    r1_b = (await client.post("/api/v1/auth/refresh", json={"refresh_token": r1_a})).json()["data"]["refresh_token"]
    r2_b = (await client.post("/api/v1/auth/refresh", json={"refresh_token": r2_a})).json()["data"]["refresh_token"]
    r3_b = (await client.post("/api/v1/auth/refresh", json={"refresh_token": r3_a})).json()["data"]["refresh_token"]

    # (a) Logout family_1 -> chỉ family_1 bị thu hồi; family_2 vẫn refresh thành công 200
    lo1 = await client.post("/api/v1/auth/logout", json={"refresh_token": r1_b})
    assert lo1.status_code == 200
    assert lo1.json()["data"] == {"logged_out": True, "revoked": True}

    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": r1_b})).status_code == 401

    rot2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": r2_b})
    assert rot2.status_code == 200
    r2_c = rot2.json()["data"]["refresh_token"]

    # (b) Reuse detection trên family_3 -> chỉ thu hồi family_3; family_2 vẫn refresh thành công 200
    reuse3 = await client.post("/api/v1/auth/refresh", json={"refresh_token": r3_a})
    assert reuse3.status_code == 401
    assert reuse3.json()["error"]["code"] == "UNAUTHENTICATED"
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": r3_b})).status_code == 401

    rot2_again = await client.post("/api/v1/auth/refresh", json={"refresh_token": r2_c})
    assert rot2_again.status_code == 200
    r2_d = rot2_again.json()["data"]["refresh_token"]

    c1_b = decode_app_refresh_token(r1_b)
    c2_d = decode_app_refresh_token(r2_d)
    c3_b = decode_app_refresh_token(r3_b)
    async with factory() as db:
        row_1b = await db.scalar(select(RefreshToken).where(RefreshToken.jti == c1_b.jti))
        row_2d = await db.scalar(select(RefreshToken).where(RefreshToken.jti == c2_d.jti))
        row_3b = await db.scalar(select(RefreshToken).where(RefreshToken.jti == c3_b.jti))
        assert row_1b is not None and row_1b.revoked_reason == "logout"
        assert row_3b is not None and row_3b.revoked_reason == "reuse_detected"
        assert row_2d is not None and row_2d.revoked_at is None
        assert len({row_1b.family_id, row_2d.family_id, row_3b.family_id}) == 3


@pytest.mark.asyncio
async def test_logout_with_rotated_token_behavior_and_secret_hygiene(be03_api, caplog):
    """Kiểm tra logout với token đã rotated (T1 -> T2), trạng thái T2 và vệ sinh log/error JSON."""
    client, factory = be03_api
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.DEBUG, logger="app")
    caplog.set_level(logging.INFO, logger="sqlalchemy")
    caplog.set_level(logging.DEBUG, logger="uvicorn")

    login_data = await _login_user(client, "a")
    t1 = login_data["refresh_token"]
    assert t1 is not None
    c1 = decode_app_refresh_token(t1)

    rot = await client.post("/api/v1/auth/refresh", json={"refresh_token": t1})
    assert rot.status_code == 200
    t2 = rot.json()["data"]["refresh_token"]
    assert t2 is not None
    c2 = decode_app_refresh_token(t2)

    # Gọi /auth/logout bằng T1 (đã rotated -> T1.revoked_at đã set với revoked_reason="rotated")
    lo_rotated = await client.post("/api/v1/auth/logout", json={"refresh_token": t1})
    assert lo_rotated.status_code == 200
    assert lo_rotated.headers.get("cache-control") == "no-store"
    assert lo_rotated.json()["data"] == {"logged_out": True, "revoked": False}

    # Kiểm tra trạng thái DB: T1 giữ revoked_reason="rotated"; T2 hiện KHÔNG bị thu hồi bởi logout(T1)
    async with factory() as db:
        row_1 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == c1.jti))
        row_2 = await db.scalar(select(RefreshToken).where(RefreshToken.jti == c2.jti))
        assert row_1 is not None and row_1.revoked_reason == "rotated"
        assert row_2 is not None and row_2.revoked_at is None

    # T1 dùng lại ở /auth/refresh vẫn bị 401 (kích hoạt reuse_detected thu hồi T2)
    # Trước tiên xác nhận T2 vẫn refresh được T3 trước khi reuse T1
    rot_t2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": t2})
    assert rot_t2.status_code == 200
    t3 = rot_t2.json()["data"]["refresh_token"]
    assert t3 is not None
    c3 = decode_app_refresh_token(t3)

    # Kiểm tra các error JSON response & log không rò raw refresh_token / jti / JWT_SECRET_KEY
    err_422 = await client.post("/api/v1/auth/logout", json={"refresh_token": f" {t3} "})
    assert err_422.status_code == 422
    err_401_reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": t1})
    assert err_401_reuse.status_code == 401
    err_401_t3 = await client.post("/api/v1/auth/refresh", json={"refresh_token": t3})
    assert err_401_t3.status_code == 401

    sensitive_values = [t1, t2, t3, c1.jti, c2.jti, c3.jti, settings.JWT_SECRET_KEY]
    for resp in (err_422, err_401_reuse, err_401_t3):
        body = resp.text
        for secret in sensitive_values:
            assert secret not in body

    log_blob = "\n".join(record.getMessage() for record in caplog.records)
    for secret in sensitive_values:
        assert secret not in log_blob


@pytest.mark.asyncio
async def test_logout_security_and_cors_headers(be03_api):
    """Kiểm tra đủ Security Headers, Cache-Control: no-store, X-Request-ID và CORS trên /api/v1/auth/logout."""
    client, _ = be03_api
    login_data = await _login_user(client, "a")
    refresh_1 = login_data["refresh_token"]
    assert refresh_1 is not None

    # Preflight CORS
    opt_ok = await client.options(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:8080", "Access-Control-Request-Method": "POST"},
    )
    assert opt_ok.status_code == 200
    assert opt_ok.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert opt_ok.headers["access-control-allow-credentials"] == "true"

    opt_evil = await client.options(
        "/api/v1/auth/logout",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert opt_evil.status_code == 400
    assert "access-control-allow-origin" not in opt_evil.headers

    # POST với Origin hợp lệ (200 và 422)
    ok_resp = await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:8080"},
        json={"refresh_token": refresh_1},
    )
    assert ok_resp.status_code == 200
    for resp in (
        ok_resp,
        await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost:8080"},
            json={"refresh_token": "not-a-valid-jwt"},
        ),
    ):
        assert resp.headers.get("cache-control") == "no-store"
        assert resp.headers.get("x-request-id")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8080"
        assert resp.headers.get("access-control-allow-credentials") == "true"

    # POST với Origin độc hại (200 retry và 401)
    for evil_resp in (
        await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "https://evil.example"},
            json={"refresh_token": refresh_1},
        ),
        await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "https://evil.example"},
            json={"refresh_token": "not-a-valid-jwt"},
        ),
    ):
        assert evil_resp.headers.get("cache-control") == "no-store"
        assert evil_resp.headers.get("x-request-id")
        assert evil_resp.headers.get("x-content-type-options") == "nosniff"
        assert evil_resp.headers.get("x-frame-options") == "DENY"
        assert evil_resp.headers.get("referrer-policy") == "no-referrer"
        assert "access-control-allow-origin" not in evil_resp.headers


@pytest.mark.asyncio
async def test_refresh_token_fk_restrict_blocks_user_deletion(be03_api):
    """FK refresh_tokens.user_id -> users.id (ondelete=RESTRICT) phải chặn xóa User khi còn token."""
    client, factory = be03_api
    login_data = await _login_user(client, "a")
    uid = UUID(login_data["user"]["user_id"])

    async with factory() as db:
        with pytest.raises(IntegrityError):
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()
        await db.rollback()
        still_there = await db.get(User, uid)
        assert still_there is not None
