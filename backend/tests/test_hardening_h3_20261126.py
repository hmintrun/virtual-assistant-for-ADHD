"""Bộ kiểm thử Hardening Batch H3 (`H3-01` .. `H3-04`): UF1 Drafts, Pagination & Google Auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import Base
from app.models.planning import PlanningDraft as PlanningDraftModel
from app.models.settings import Availability as AvailabilityModel
from app.models.task import Task as TaskModel
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceModel
from app.schemas.draft import (
    ConfirmSummaryRequest,
    DraftAnalyzeRequest,
    DraftChanges,
    SummaryChanges,
    UpdateDraftRequest,
)
from app.schemas.task import TaskExtractionResult
from app.schemas.user import CurrentUser
from app.services import draft_service
from app.services import google_identity_verifier as verifier


async def _make_env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    sf = session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return engine, sf


def _auth_headers(user_id: UUID) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"h3_{user_id.hex[:8]}@example.com",
        display_name="H3 Tester",
        is_active=True,
    )
    return {"Authorization": f"Bearer test-{user_id.hex[:8]}"}


async def _seed_user_and_draft(
    db: AsyncSession,
    *,
    now: datetime,
    stage: str = "summary_review",
    link_confirmed_task: bool = False,
    created_at: datetime | None = None,
) -> dict[str, UUID]:
    uid = uuid4()
    draft_id = uuid4()
    avail_id = uuid4()
    task_id = uuid4()
    ts = created_at or now

    existing_u = await db.get(User, uid)
    if existing_u is None:
        db.add(
            User(
                id=uid,
                email=f"h3_{uid.hex[:8]}@example.com",
                hashed_password="pw",
                display_name="H3 Tester",
            )
        )
        await db.flush()
        db.add(
            UserPreferenceModel(
                user_id=uid,
                revision=1,
                effective_revision=1,
                work_interval_seconds=1500,
                rest_interval_seconds=300,
                micro_task_seconds=120,
                reminder_offsets=[900, 600, 300],
                buffer_multiplier=1.2,
                reminder_style="gentle",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            AvailabilityModel(
                id=avail_id,
                user_id=uid,
                revision=1,
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[
                    {"weekday": w, "start_local": "08:00", "end_local": "22:00"}
                    for w in range(1, 8)
                ],
                date_overrides=[],
                task_limits=[],
                source="manual_input",
                confirmed_at=now - timedelta(days=1),
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()

    summary_payload = {
        "title": "Công việc UF1 H3",
        "description": "Mô tả chi tiết H3",
        "goal": "Hoàn thành H3",
        "scope": "Phạm vi H3",
        "extra_requirements": [],
        "time_constraint": {
            "kind": "deadline",
            "start_at": now.isoformat(),
            "deadline_at": (now + timedelta(days=7)).isoformat(),
            "input_timezone": "Asia/Ho_Chi_Minh",
        },
        "availability": {
            "timezone": "Asia/Ho_Chi_Minh",
            "weekly_rules": [
                {"weekday": w, "start_local": "08:00", "end_local": "22:00"}
                for w in range(1, 8)
            ],
            "date_overrides": [],
        },
        "clarification_issues": [],
        "can_plan": True,
        "field_sources": {},
    }
    db.add(
        PlanningDraftModel(
            id=draft_id,
            user_id=uid,
            client_draft_id=f"cd_{draft_id.hex[:8]}",
            stage=stage,
            raw_input="Lập kế hoạch H3",
            input_source="text",
            summary=summary_payload,
            summary_revision=1,
            planning_input_revision=1,
            created_at=ts,
            updated_at=ts,
        )
    )
    await db.flush()
    if link_confirmed_task:
        db.add(
            TaskModel(
                id=task_id,
                user_id=uid,
                revision=1,
                title="Task đã chốt H3",
                description="Mô tả",
                scope="Phạm vi",
                time_constraint=summary_payload["time_constraint"],
                status="scheduled",
                source_draft_id=draft_id,
                source_summary=summary_payload,
                source_summary_revision=1,
                source_planning_input_revision=1,
                availability=summary_payload["availability"],
            )
        )
    await db.commit()
    return {"user_id": uid, "draft_id": draft_id, "task_id": task_id}


async def test_h3_01_confirmed_draft_blocks_patch_confirm_summary_and_analyze():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids1 = await _seed_user_and_draft(
                db, now=now, stage="confirmed", link_confirmed_task=True
            )
            uid1, draft1 = ids1["user_id"], ids1["draft_id"]

            # 1) patch_draft bị chặn 409 INVALID_STATE_TRANSITION
            with pytest.raises(AppError) as exc_patch:
                await draft_service.patch_draft(
                    user_id=str(uid1),
                    draft_id=str(draft1),
                    idempotency_key="h3-01-patch",
                    payload=UpdateDraftRequest(
                        expected_summary_revision=1,
                        expected_planning_input_revision=1,
                        changes=DraftChanges(summary=SummaryChanges(title="Sửa lén")),
                    ),
                    db=db,
                )
            assert exc_patch.value.status_code == 409
            assert exc_patch.value.code == "INVALID_STATE_TRANSITION"
            assert exc_patch.value.next_action == "review_task"

            # 2) confirm_draft_summary bị chặn
            with pytest.raises(AppError) as exc_conf:
                await draft_service.confirm_draft_summary(
                    user_id=str(uid1),
                    draft_id=str(draft1),
                    idempotency_key="h3-01-confirm",
                    payload=ConfirmSummaryRequest(expected_summary_revision=1),
                    db=db,
                )
            assert exc_conf.value.status_code == 409
            assert exc_conf.value.code == "INVALID_STATE_TRANSITION"
            assert exc_conf.value.next_action == "review_task"

            # 3) analyze_draft bị chặn ở Pha 1
            with pytest.raises(AppError) as exc_ana:
                await draft_service.analyze_draft(
                    user_id=str(uid1),
                    draft_id=str(draft1),
                    idempotency_key="h3-01-analyze",
                    payload=DraftAnalyzeRequest(
                        expected_summary_revision=1,
                        expected_planning_input_revision=1,
                        message="Thêm yêu cầu",
                    ),
                    db=db,
                )
            assert exc_ana.value.status_code == 409
            assert exc_ana.value.code == "INVALID_STATE_TRANSITION"
            assert exc_ana.value.next_action == "review_task"

            row = await db.get(PlanningDraftModel, draft1)
            assert row is not None and row.stage == "confirmed"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h3_01_analyze_draft_race_with_confirm_candidate_blocks_overwrite():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_draft(
                db, now=now, stage="summary_confirmed", link_confirmed_task=False
            )
            uid, draft_id = ids["user_id"], ids["draft_id"]

            # (a) Trong lúc Pha 2 đang gọi AI thì confirm_candidate chốt Draft -> stage="confirmed"
            async def _extract_while_confirmed(*_args, **_kwargs):
                async with sf() as db_other:
                    d_row = await db_other.get(PlanningDraftModel, draft_id)
                    assert d_row is not None
                    d_row.stage = "confirmed"
                    await db_other.commit()
                return TaskExtractionResult(
                    title="Công việc mới",
                    scope="Phạm vi",
                    content="Nội dung",
                    summary="Tóm tắt công việc mới",
                    is_complete=False,
                    generation_tier="tier_1_gemini",
                )

            with patch.object(
                draft_service.ai_service,
                "extract_task_intent",
                side_effect=_extract_while_confirmed,
            ):
                with pytest.raises(AppError) as exc_race:
                    await draft_service.analyze_draft(
                        user_id=str(uid),
                        draft_id=str(draft_id),
                        idempotency_key="h3-01-race",
                        payload=DraftAnalyzeRequest(
                            expected_summary_revision=1,
                            expected_planning_input_revision=1,
                            message="Phân tích bổ sung",
                        ),
                        db=db,
                    )
            assert exc_race.value.status_code == 409
            assert exc_race.value.code == "INVALID_STATE_TRANSITION"
            assert exc_race.value.next_action == "review_task"

            # (b) Phòng thủ chiều sâu _persist_draft_to_db nếu confirm chạy chen giữa Pha 3 và UPDATE CAS
            row = await db.get(PlanningDraftModel, draft_id)
            assert row is not None and row.stage == "confirmed"
            stale_expected = draft_service._row_to_schema(row).model_copy(
                update={"stage": "summary_confirmed", "confirmed_task_id": None}
            )
            new_candidate_draft = stale_expected.model_copy(
                update={"stage": "summary_review", "summary_revision": 2}
            )
            with pytest.raises(AppError) as exc_cas:
                await draft_service._persist_draft_to_db(
                    db, str(uid), new_candidate_draft, expected=stale_expected
                )
            assert exc_cas.value.status_code == 409
            assert exc_cas.value.code == "INVALID_STATE_TRANSITION"
            assert exc_cas.value.next_action == "review_task"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h3_02_delete_draft_locks_owner_and_maps_integrity_error_to_409():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_draft(
                db, now=now, stage="summary_review", link_confirmed_task=False
            )
            uid, draft_id = ids["user_id"], ids["draft_id"]

            lock_calls: list[str] = []
            orig_lock_owner = draft_service.lock_owner

            async def _spy_lock_owner(db_arg, u_id):
                lock_calls.append(u_id)
                return await orig_lock_owner(db_arg, u_id)

            orig_commit = db.commit
            await draft_service.ensure_dev_seed_in_db(db)

            async def _raise_fk_on_delete(_row):
                raise IntegrityError("DELETE FROM planning_drafts", {}, Exception("FK violation"))

            with (
                patch.object(draft_service, "lock_owner", side_effect=_spy_lock_owner),
                patch.object(db, "delete", side_effect=_raise_fk_on_delete),
            ):
                with pytest.raises(AppError) as exc_del:
                    await draft_service.delete_draft(
                        user_id=str(uid),
                        draft_id=str(draft_id),
                        db=db,
                    )
            assert lock_calls == [str(uid)]
            assert exc_del.value.status_code == 409
            assert exc_del.value.code == "INVALID_STATE_TRANSITION"
            assert exc_del.value.next_action == "reload_and_review"

            # Khi không lỗi FK, xóa thành công và commit bằng orig_commit
            assert callable(orig_commit)
            res = await draft_service.delete_draft(
                user_id=str(uid),
                draft_id=str(draft_id),
                db=db,
            )
            assert res["deleted"] is True
            assert await db.get(PlanningDraftModel, draft_id) is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _make_rs256_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return priv, pub


async def test_h3_03_google_verifier_refreshes_certs_on_kid_rotation_with_60s_cooldown(
    monkeypatch: pytest.MonkeyPatch, auth_config
):
    priv_old, pub_old = _make_rs256_pair()
    priv_new, pub_new = _make_rs256_pair()
    priv_rogue, _ = _make_rs256_pair()

    mono_now = 5.0  # < 60s để kiểm tra MED-8 (_last_force_refresh_at khởi tạo None)
    monkeypatch.setattr(verifier.time, "monotonic", lambda: mono_now)
    monkeypatch.setattr(verifier, "_certs", {})
    monkeypatch.setattr(verifier, "_certs_expiry", 0.0)
    monkeypatch.setattr(verifier, "_last_force_refresh_at", None)

    http_calls: list[int] = []
    cert_responses = [
        {"kid-old": pub_old},
        {"kid-old": pub_old, "kid-new": pub_new},
        {"kid-old": pub_old, "kid-new": pub_new, "kid-later": pub_new},
    ]

    async def _fake_get(self, url, *args, **kwargs):
        idx = min(len(http_calls), len(cert_responses) - 1)
        http_calls.append(1)
        return httpx.Response(
            200,
            json=cert_responses[idx],
            headers={"cache-control": "public, max-age=3600"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    def _sign(priv, kid: str) -> str:
        ts = int(datetime.now(UTC).timestamp())
        return jose_jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": verifier.settings.GOOGLE_SIGN_IN_AUDIENCES[0],
                "sub": "sub-h3-03",
                "iat": ts,
                "exp": ts + 3600,
                "email": "h3@example.com",
                "email_verified": True,
                "name": "H3 User",
            },
            priv,
            algorithm="RS256",
            headers={"kid": kid},
        )

    # 1) Token kid-old làm ấm cache (1 HTTP call)
    id_old = await verifier.verify_google_identity(_sign(priv_old, "kid-old"))
    assert id_old.sub == "sub-h3-03"
    assert len(http_calls) == 1

    # 2) Token kid-new dù đang ở mono=5.0 (<60s) và TTL cache còn hạn -> tự force-refresh 1 lần thành công!
    id_new = await verifier.verify_google_identity(_sign(priv_new, "kid-new"))
    assert id_new.sub == "sub-h3-03"
    assert len(http_calls) == 2

    # 3) Token chữ ký sai nhưng dùng kid-new đã biết -> KHÔNG kích hoạt force-refresh (vẫn 2 calls)
    mono_now = 100.0
    with pytest.raises(AppError) as exc_bad_sig:
        await verifier.verify_google_identity(_sign(priv_rogue, "kid-new"))
    assert exc_bad_sig.value.status_code == 401
    assert len(http_calls) == 2

    # 4) Token kid-garbage liên tiếp trong cooldown 60s -> chỉ gọi force-refresh tối đa 1 lần
    mono_now = 120.0
    with pytest.raises(AppError):
        await verifier.verify_google_identity(_sign(priv_rogue, "kid-garbage-1"))
    assert len(http_calls) == 3
    mono_now = 130.0
    with pytest.raises(AppError):
        await verifier.verify_google_identity(_sign(priv_rogue, "kid-garbage-2"))
    assert len(http_calls) == 3  # Bị cooldown 60s chặn không spam Google


async def test_h3_04_pagination_limit_cap_100_on_drafts_tasks_schedule():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_draft(db, now=now)
            uid = ids["user_id"]

        headers = _auth_headers(uid)
        from_iso = now.isoformat()
        to_iso = (now + timedelta(days=7)).isoformat()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in (
                "/api/v1/drafts?limit=101",
                "/api/v1/tasks?limit=101",
                f"/api/v1/schedule?from={from_iso.replace('+', '%2B')}&to={to_iso.replace('+', '%2B')}&limit=101",
            ):
                r_over = await client.get(path, headers=headers)
                assert r_over.status_code == 422, f"{path} -> {r_over.text}"

            for path in (
                "/api/v1/drafts?limit=100",
                "/api/v1/tasks?limit=100",
                f"/api/v1/schedule?from={from_iso.replace('+', '%2B')}&to={to_iso.replace('+', '%2B')}&limit=100",
            ):
                r_ok = await client.get(path, headers=headers)
                assert r_ok.status_code == 200, f"{path} -> {r_ok.text}"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h3_04_list_drafts_tiebreaker_deterministic_on_equal_created_at():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            uid = uuid4()
            db.add(
                User(
                    id=uid,
                    email=f"h3tie_{uid.hex[:8]}@example.com",
                    hashed_password="pw",
                    display_name="H3 Tie Tester",
                )
            )
            await db.flush()
            draft_ids = [uuid4() for _ in range(3)]
            for did in draft_ids:
                db.add(
                    PlanningDraftModel(
                        id=did,
                        user_id=uid,
                        client_draft_id=f"cd_{did.hex[:8]}",
                        stage="collecting",
                        raw_input=f"Draft {did.hex[:4]}",
                        input_source="text",
                        summary={"can_plan": False, "clarification_issues": [], "field_sources": {}},
                        summary_revision=1,
                        planning_input_revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await db.commit()

            seen: list[str] = []
            cursor: str | None = None
            for _ in range(5):
                page = await draft_service.list_drafts(
                    user_id=str(uid),
                    limit=1,
                    cursor=cursor,
                    db=db,
                )
                seen.extend(item.draft_id for item in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break

            assert len(seen) == 3
            assert set(seen) == {str(d) for d in draft_ids}
            expected_order = [
                str(d)
                for d in (
                    await db.scalars(
                        select(PlanningDraftModel.id)
                        .where(PlanningDraftModel.user_id == uid)
                        .order_by(PlanningDraftModel.created_at.asc(), PlanningDraftModel.id.asc())
                    )
                ).all()
            ]
            assert seen == expected_order
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
