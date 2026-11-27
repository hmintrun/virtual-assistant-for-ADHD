"""Bộ kiểm thử Hardening Batch H4 (`H4-01` .. `H4-07`): Google Calendar Sync, Worker, Pull-Forward & Test Isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models import Base
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.execution import CheckIn, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.calendar import (
    CalendarConfigurationConfirmRequest,
    CalendarSource,
    CalendarSyncJobRetryRequest,
    EventChangeConfirmRequest,
    EventChangePreviewRequest,
)
from app.schemas.reschedule import CreateProposalRequest, ProposalSourceRevisions
from app.schemas.user import CurrentUser
from app.services import reschedule_service
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import (
    CalendarAuthError,
    CalendarClientError,
    FakeCalendarClient,
    GoogleCalendarClient,
    get_calendar_client,
    reset_fake_calendar_client,
)
from app.services.planning.calendar_ingestion import ingest_calendar_sources

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


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


def _auth_headers(user_id: UUID, idempotency_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"h4_{user_id.hex[:8]}@example.com",
        display_name="H4 Tester",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-{user_id.hex[:8]}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _seed_user_and_connection(
    db: AsyncSession,
    *,
    now: datetime,
    write_calendar_id: str | None = "proplan_secondary",
    expired_token: bool = False,
    corrupt_creds: bool = False,
) -> dict[str, UUID]:
    uid = uuid4()
    conn_id = uuid4()
    avail_id = uuid4()
    db.add(
        User(
            id=uid,
            email=f"h4_{uid.hex[:8]}@example.com",
            hashed_password="pw",
            display_name="H4 Tester",
        )
    )
    await db.flush()
    db.add(
        UserPreferenceRow(
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
        AvailabilityRow(
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
    if corrupt_creds:
        cred_ref = "enc:v1:corrupted-fernet-token"
    else:
        stored_at = (now - timedelta(hours=2)) if expired_token else now
        cred_ref = encrypt_credentials(
            {
                "access_token": "acc_h4_valid",
                "refresh_token": "ref_h4_valid",
                "expires_in": 3600,
                "stored_at": stored_at.isoformat(),
                "user_id": str(uid),
                "connection_id": str(conn_id),
            }
        )
    db.add(
        CalendarConnection(
            id=conn_id,
            user_id=uid,
            revision=1,
            provider="google",
            account_ref=f"h4_{uid.hex[:8]}@example.com",
            read_calendar_ids=["primary"],
            write_calendar_id=write_calendar_id,
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            connection_state="connected",
            credential_ref=cred_ref,
            last_successful_sync_at=now - timedelta(minutes=10),
        )
    )
    await db.commit()
    return {"user_id": uid, "conn_id": conn_id, "avail_id": avail_id}


async def test_h4_01_event_change_preview_preserves_calendar_id_strips_internal_key_and_allows_no_write_cal(
    monkeypatch: pytest.MonkeyPatch,
):
    reset_fake_calendar_client()
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now, write_calendar_id=None)
            uid, conn_id = ids["user_id"], ids["conn_id"]

        headers = _auth_headers(uid)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            prev_resp = await client.post(
                "/api/v1/calendar/event-change-previews",
                json=EventChangePreviewRequest(
                    operation="create",
                    connection_id=str(conn_id),
                    google_calendar_id="work_cal_123",
                    payload={
                        "summary": "Họp nhóm H4",
                        "start": {"dateTime": now.isoformat()},
                        "end": {"dateTime": (now + timedelta(hours=1)).isoformat()},
                    },
                ).model_dump(mode="json"),
                headers=headers,
            )
            assert prev_resp.status_code == 200, prev_resp.text
            preview_id = prev_resp.json()["data"]["preview_id"]

            conf_resp = await client.post(
                f"/api/v1/calendar/event-change-previews/{preview_id}/confirm",
                json=EventChangeConfirmRequest(expected_preview_revision=1).model_dump(mode="json"),
                headers=headers,
            )
            assert conf_resp.status_code == 200, conf_resp.text
            job_id = UUID(conf_resp.json()["data"]["job_id"])

        recorded_calls: list[dict] = []
        fake_client = get_calendar_client()

        async def _spy_create_event(*, access_token: str, calendar_id: str, event_payload: dict):
            recorded_calls.append({"calendar_id": calendar_id, "payload": dict(event_payload)})
            return {"id": "created_ev_1", "etag": '"etag-new"'}

        monkeypatch.setattr(fake_client, "create_event", _spy_create_event)
        async with sf() as db:
            processed = await process_calendar_sync_jobs(db, user_id=str(uid), limit=10)
            assert processed == 1
            job_row = await db.get(CalendarSyncJob, job_id)
            assert job_row is not None and job_row.job_state == "completed"
            assert "_google_calendar_id" in (job_row.payload_snapshot or {})

        assert len(recorded_calls) == 1
        assert recorded_calls[0]["calendar_id"] == "work_cal_123"
        assert "_google_calendar_id" not in recorded_calls[0]["payload"]
        assert "google_calendar_id" not in recorded_calls[0]["payload"]
    finally:
        reset_fake_calendar_client()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h4_01_worker_fails_no_write_calendar_early_before_token_acquisition():
    reset_fake_calendar_client()
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now, write_calendar_id=None)
            uid, conn_id = ids["user_id"], ids["conn_id"]
            job = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                event_link_id=None,
                target_kind="event",
                target_ref="ev-missing-cal",
                operation="create",
                idempotency_key="h4-01-no-cal",
                payload_snapshot={"summary": "Không có target calendar"},
                job_state="queued",
                attempt_count=0,
            )
            db.add(job)
            await db.commit()

            cal_service = CalendarService(db)
            token_spy = AsyncMock()
            with patch.object(cal_service, "get_valid_access_token", token_spy):
                status = await process_sync_job(db, job, cal_service)
            assert status == "failed"
            token_spy.assert_not_called()
            await db.refresh(job)
            assert job.last_error_code == "NO_WRITE_CALENDAR"
            assert job.attempt_count == 5
    finally:
        reset_fake_calendar_client()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h4_02_refresh_access_token_429_5xx_retryable_vs_400_401_invalid_grant_auth_error(
    monkeypatch: pytest.MonkeyPatch,
):
    client = GoogleCalendarClient(
        client_id="cid", client_secret="csec", default_redirect_uri="http://localhost/cb"
    )

    for status_code, body in ((429, {"error": "rate_limit_exceeded"}), (503, {"error": "backendError"})):
        async def _post_transient(
            self, url, data=None, _sc=status_code, _body=body, **_kwargs
        ):
            return httpx.Response(_sc, json=_body, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", _post_transient)
        with pytest.raises(CalendarClientError) as exc_tr:
            await client.refresh_access_token("ref-1")
        assert not isinstance(exc_tr.value, CalendarAuthError)
        assert exc_tr.value.status_code == status_code

    for status_code, body in (
        (400, {"error": "invalid_grant"}),
        (401, {"error": "unauthorized"}),
        (500, {"error": "invalid_client"}),
    ):
        async def _post_auth(
            self, url, data=None, _sc=status_code, _body=body, **_kwargs
        ):
            return httpx.Response(_sc, json=_body, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", _post_auth)
        with pytest.raises(CalendarAuthError):
            await client.refresh_access_token("ref-1")


async def test_h4_03_confirm_configuration_preview_lock_free_cas_replay_and_expired_no_missing_greenlet(
    monkeypatch: pytest.MonkeyPatch,
):
    reset_fake_calendar_client()
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now, write_calendar_id=None)
            uid, conn_id = ids["user_id"], ids["conn_id"]

            # (a) Kiểm tra db.in_transaction() is False lúc gọi HTTP Google + replay không MissingGreenlet
            p1 = CalendarPreview(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                kind="configuration",
                base_connection_revision=1,
                source_revisions={},
                payload={
                    "read_calendar_ids": ["primary"],
                    "write_calendar_id": "provision_new",
                    "existing_event_action": "provision_new",
                },
                status="pending",
                expires_at=now + timedelta(days=3650),
            )
            db.add(p1)
            await db.commit()
            p1_id = p1.id

            tx_states_during_http: list[bool] = []
            service = CalendarService(db)
            orig_list_cal = service.client.list_calendar_list

            async def _spy_list_cal(access_token: str):
                tx_states_during_http.append(db.in_transaction())
                return await orig_list_cal(access_token)

            monkeypatch.setattr(service.client, "list_calendar_list", _spy_list_cal)
            headers = _auth_headers(uid)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch("app.api.v1.calendar.CalendarService", return_value=service):
                    r_ok = await client.post(
                        f"/api/v1/calendar/configuration-previews/{p1_id}/confirm",
                        json=CalendarConfigurationConfirmRequest(expected_preview_revision=1).model_dump(
                            mode="json"
                        ),
                        headers=headers,
                    )
                assert r_ok.status_code == 200, r_ok.text
                assert tx_states_during_http == [False]

                # Replay ở Pha A (preview đã confirmed) không MissingGreenlet
                r_replay = await client.post(
                    f"/api/v1/calendar/configuration-previews/{p1_id}/confirm",
                    json=CalendarConfigurationConfirmRequest(expected_preview_revision=1).model_dump(
                        mode="json"
                    ),
                    headers=headers,
                )
                assert r_replay.status_code == 200, r_replay.text

            # (b) Nhánh hết hạn expires_at < now_utc không MissingGreenlet -> 409 PREVIEW_EXPIRED
            p_exp = CalendarPreview(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                kind="configuration",
                base_connection_revision=1,
                source_revisions={},
                payload={"read_calendar_ids": ["primary"], "write_calendar_id": "primary"},
                status="pending",
                expires_at=now - timedelta(minutes=1),
            )
            db.add(p_exp)
            await db.commit()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r_exp = await client.post(
                    f"/api/v1/calendar/configuration-previews/{p_exp.id}/confirm",
                    json=CalendarConfigurationConfirmRequest(expected_preview_revision=1).model_dump(
                        mode="json"
                    ),
                    headers=headers,
                )
            assert r_exp.status_code == 409
            assert r_exp.json()["error"]["code"] == "PREVIEW_EXPIRED"

            # (c) CAS ở Pha B bắt thay đổi conn.revision trong lúc gọi HTTP Google
            conn_row = await db.get(CalendarConnection, conn_id)
            assert conn_row is not None
            conn_rev_now = conn_row.revision
            p_cas = CalendarPreview(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                kind="configuration",
                base_connection_revision=conn_rev_now,
                source_revisions={},
                payload={
                    "read_calendar_ids": ["primary"],
                    "write_calendar_id": "provision_new",
                    "existing_event_action": "provision_new",
                },
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            db.add(p_cas)
            await db.commit()

            service_cas = CalendarService(db)

            async def _bump_conn_during_http(_access_token: str):
                async with sf() as db_other:
                    c_other = await db_other.get(CalendarConnection, conn_id)
                    assert c_other is not None
                    c_other.revision += 1
                    await db_other.commit()
                return []

            monkeypatch.setattr(service_cas.client, "list_calendar_list", _bump_conn_during_http)
            with pytest.raises(CalendarClientError) as exc_cas:
                await service_cas.confirm_configuration_preview(
                    user_id=str(uid), preview_id=p_cas.id, expected_preview_revision=1
                )
            assert exc_cas.value.status_code == 409
            assert exc_cas.value.error_code == "REVISION_CONFLICT"

            # (d) 401 từ Google trong confirm_configuration_preview -> 401 CALENDAR_AUTH_REQUIRED qua router
            conn_fresh = await db.get(CalendarConnection, conn_id)
            assert conn_fresh is not None
            p_auth = CalendarPreview(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                kind="configuration",
                base_connection_revision=conn_fresh.revision,
                source_revisions={},
                payload={
                    "read_calendar_ids": ["primary"],
                    "write_calendar_id": "provision_new",
                    "existing_event_action": "provision_new",
                },
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            db.add(p_auth)
            await db.commit()
            service_auth = CalendarService(db)

            async def _raise_401(_access_token: str):
                raise CalendarAuthError("Google 401")

            monkeypatch.setattr(service_auth.client, "list_calendar_list", _raise_401)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch("app.api.v1.calendar.CalendarService", return_value=service_auth):
                    r_401 = await client.post(
                        f"/api/v1/calendar/configuration-previews/{p_auth.id}/confirm",
                        json=CalendarConfigurationConfirmRequest(
                            expected_preview_revision=1
                        ).model_dump(mode="json"),
                        headers=headers,
                    )
            assert r_401.status_code == 401
            assert r_401.json()["error"]["code"] == "CALENDAR_AUTH_REQUIRED"
    finally:
        reset_fake_calendar_client()
        app.dependency_overrides.clear()
        await engine.dispose()


def test_h4_04_fake_calendar_client_reset_clears_singleton_and_patched_methods():
    reset_fake_calendar_client()
    try:
        c1 = get_calendar_client()
        assert isinstance(c1, FakeCalendarClient)
        c1.update_event = lambda *args, **kwargs: None  # type: ignore[method-assign]
        reset_fake_calendar_client()
        c2 = get_calendar_client()
        assert c2 is not c1
        assert callable(c2.update_event)
    finally:
        reset_fake_calendar_client()


async def test_h4_05_retry_sync_job_checks_revision_completed_running_active_lease_inside_lock():
    reset_fake_calendar_client()
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now)
            uid, conn_id = ids["user_id"], ids["conn_id"]

            j_comp = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                target_kind="event",
                target_ref="e1",
                operation="create",
                idempotency_key="j-comp",
                payload_snapshot={"summary": "Done"},
                job_state="completed",
                revision=2,
            )
            j_run = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                target_kind="event",
                target_ref="e2",
                operation="create",
                idempotency_key="j-run",
                payload_snapshot={"summary": "Running"},
                job_state="running",
                revision=1,
            )
            j_leased = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                target_kind="event",
                target_ref="e3",
                operation="create",
                idempotency_key="j-leased",
                payload_snapshot={"summary": "Leased"},
                job_state="queued",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                revision=1,
            )
            j_fail = CalendarSyncJob(
                id=uuid4(),
                user_id=uid,
                connection_id=conn_id,
                target_kind="event",
                target_ref="e4",
                operation="create",
                idempotency_key="j-fail",
                payload_snapshot={"summary": "Retryable"},
                job_state="failed",
                attempt_count=2,
            )
            db.add_all([j_comp, j_run, j_leased, j_fail])
            await db.commit()
            # Tăng revision lên >1 qua ORM update
            j_fail.attempt_count = 3
            await db.commit()
            await db.refresh(j_fail)
            fail_rev = j_fail.revision

        headers = _auth_headers(uid)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_rev = await client.post(
                f"/api/v1/calendar/sync-jobs/{j_fail.id}/retry",
                json=CalendarSyncJobRetryRequest(expected_job_revision=fail_rev + 10).model_dump(
                    mode="json"
                ),
                headers=headers,
            )
            assert r_rev.status_code == 409
            assert r_rev.json()["error"]["code"] == "REVISION_CONFLICT"

            r_comp = await client.post(
                f"/api/v1/calendar/sync-jobs/{j_comp.id}/retry",
                json=CalendarSyncJobRetryRequest(expected_job_revision=j_comp.revision).model_dump(
                    mode="json"
                ),
                headers=headers,
            )
            assert r_comp.status_code == 409
            assert r_comp.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

            r_run = await client.post(
                f"/api/v1/calendar/sync-jobs/{j_run.id}/retry",
                json=CalendarSyncJobRetryRequest(expected_job_revision=j_run.revision).model_dump(
                    mode="json"
                ),
                headers=headers,
            )
            assert r_run.status_code == 409
            assert r_run.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

            r_leased = await client.post(
                f"/api/v1/calendar/sync-jobs/{j_leased.id}/retry",
                json=CalendarSyncJobRetryRequest(expected_job_revision=j_leased.revision).model_dump(
                    mode="json"
                ),
                headers=headers,
            )
            assert r_leased.status_code == 409
            assert r_leased.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

            r_ok = await client.post(
                f"/api/v1/calendar/sync-jobs/{j_fail.id}/retry",
                json=CalendarSyncJobRetryRequest(expected_job_revision=fail_rev).model_dump(
                    mode="json"
                ),
                headers=headers,
            )
            assert r_ok.status_code == 200, r_ok.text
            assert r_ok.json()["data"]["job_state"] == "completed"
    finally:
        reset_fake_calendar_client()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h4_06_corrupt_fernet_credential_returns_calendar_auth_error_not_500():
    reset_fake_calendar_client()
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now, corrupt_creds=True)
            uid, conn_id = ids["user_id"], ids["conn_id"]

        headers = _auth_headers(uid)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/calendar/connections/{conn_id}/calendars",
                headers=headers,
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "CALENDAR_AUTH_REQUIRED"

        async with sf() as db:
            conn_after = await db.get(CalendarConnection, conn_id)
            assert conn_after is not None
            assert conn_after.connection_state == "needs_reconnect"
    finally:
        reset_fake_calendar_client()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h4_06_401_on_read_endpoints_reconcile_and_ingestion_persists_needs_reconnect(
    monkeypatch: pytest.MonkeyPatch,
):
    reset_fake_calendar_client()
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now)
            uid, conn_id = ids["user_id"], ids["conn_id"]

            async def _reset_conn():
                c = await db.get(CalendarConnection, conn_id)
                assert c is not None
                c.connection_state = "connected"
                await db.commit()

            fake_client = get_calendar_client()
            monkeypatch.setattr(fake_client, "list_calendar_list", AsyncMock(side_effect=CalendarAuthError("401")))
            monkeypatch.setattr(fake_client, "list_events", AsyncMock(side_effect=CalendarAuthError("401")))
            monkeypatch.setattr(fake_client, "get_event", AsyncMock(side_effect=CalendarAuthError("401")))

            headers = _auth_headers(uid)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # 1) list_calendars -> 401 & needs_reconnect
                r1 = await client.get(f"/api/v1/calendar/connections/{conn_id}/calendars", headers=headers)
                assert r1.status_code == 401
                assert (await db.get(CalendarConnection, conn_id)).connection_state == "needs_reconnect"  # type: ignore[union-attr]

                # 2) get_connection_events -> 401 & needs_reconnect
                await _reset_conn()
                r2 = await client.get(
                    f"/api/v1/calendar/connections/{conn_id}/events?google_calendar_id=primary",
                    headers=headers,
                )
                assert r2.status_code == 401
                assert (await db.get(CalendarConnection, conn_id)).connection_state == "needs_reconnect"  # type: ignore[union-attr]

                # 3) get_connection_event_detail -> 401 & needs_reconnect
                await _reset_conn()
                r3 = await client.get(
                    f"/api/v1/calendar/connections/{conn_id}/events/ev1?google_calendar_id=primary",
                    headers=headers,
                )
                assert r3.status_code == 401
                assert (await db.get(CalendarConnection, conn_id)).connection_state == "needs_reconnect"  # type: ignore[union-attr]

            # 4) reconcile_external_link_changes: link1 đổi nhưng link2 bị 401 -> rollback link1 và persist needs_reconnect
            await _reset_conn()
            s_id_1, s_id_2 = uuid4(), uuid4()
            task_id = uuid4()
            step_id = uuid4()
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=uid,
                    revision=1,
                    title="T",
                    description="D",
                    scope="S",
                    time_constraint={"mode": "deadline", "at": (now + timedelta(days=3)).isoformat()},
                    status="scheduled",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=uid,
                    task_id=task_id,
                    order_index=0,
                    title="St",
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="scheduled",
                )
            )
            await db.flush()
            db.add_all(
                [
                    SessionRow(
                        id=s_id_1,
                        user_id=uid,
                        step_id=step_id,
                        scheduled_start=now + timedelta(hours=2),
                        scheduled_end=now + timedelta(hours=2, minutes=30),
                        duration_seconds=1800,
                        work_seconds=1500,
                        rest_seconds=300,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        schedule_revision=1,
                        status="scheduled",
                    ),
                    SessionRow(
                        id=s_id_2,
                        user_id=uid,
                        step_id=step_id,
                        scheduled_start=now + timedelta(hours=4),
                        scheduled_end=now + timedelta(hours=4, minutes=30),
                        duration_seconds=1800,
                        work_seconds=1500,
                        rest_seconds=300,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        schedule_revision=1,
                        status="scheduled",
                    ),
                ]
            )
            await db.flush()
            l1 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_id_1,
                connection_id=conn_id,
                google_calendar_id="proplan_secondary",
                google_event_id="ev_1",
                desired_schedule_revision=1,
                synced_schedule_revision=1,
                sync_state="synced",
                is_current=True,
            )
            l2 = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=s_id_2,
                connection_id=conn_id,
                google_calendar_id="proplan_secondary",
                google_event_id="ev_2",
                desired_schedule_revision=1,
                synced_schedule_revision=1,
                sync_state="synced",
                is_current=True,
            )
            db.add_all([l1, l2])
            await db.commit()
            l1_id = l1.id

            call_idx = 0

            async def _get_ev_then_401(*_args, **_kwargs):
                nonlocal call_idx
                call_idx += 1
                if call_idx == 1:
                    return None  # sẽ sửa l1 nếu không rollback
                raise CalendarAuthError("401 mid-loop")

            monkeypatch.setattr(fake_client, "get_event", _get_ev_then_401)
            svc = CalendarService(db)
            with pytest.raises(CalendarAuthError):
                await svc.reconcile_external_link_changes(uid, conn_id)
            l1_after = await db.get(CalendarEventLink, l1_id)
            assert l1_after is not None and l1_after.sync_state == "synced"
            assert (await db.get(CalendarConnection, conn_id)).connection_state == "needs_reconnect"  # type: ignore[union-attr]

            # 5) calendar_ingestion với defer_command_commit=True không commit sớm transaction cha
            await _reset_conn()
            monkeypatch.setattr(fake_client, "list_events", AsyncMock(side_effect=CalendarAuthError("401")))
            db.info["defer_command_commit"] = True
            try:
                ing = await ingest_calendar_sources(
                    db=db,
                    user_id=str(uid),
                    selected_sources=[
                        CalendarSource(
                            connection_id=str(conn_id),
                            google_calendar_id="primary",
                        )
                    ],
                    excluded_sources=[],
                    query_start=now,
                    query_end=now + timedelta(days=3),
                )
                assert ing.calendar_errors[0].requires_reconnect is True
                assert (await db.get(CalendarConnection, conn_id)).connection_state == "needs_reconnect"  # type: ignore[union-attr]
                await db.rollback()
                # Vì defer_command_commit=True chỉ flush() chứ không commit(), rollback sẽ hoàn nguyên
                assert (await db.get(CalendarConnection, conn_id)).connection_state == "connected"  # type: ignore[union-attr]
            finally:
                db.info.pop("defer_command_commit", None)
    finally:
        reset_fake_calendar_client()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h4_07_early_completion_pull_forward_moves_future_session_earlier_respecting_preferred_windows(
    monkeypatch: pytest.MonkeyPatch,
):
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)  # 08:00 +07:00
        monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda dt=None: (dt or now).astimezone(UTC))
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now)
            uid = ids["user_id"]
            task_id = uuid4()
            step_id = uuid4()
            s1_id = uuid4()
            s2_id = uuid4()

            # Availability có preferred_time_windows buổi sáng 08:00-12:00 (và 13:00-18:00 bình thường)
            avail_row = await db.get(AvailabilityRow, ids["avail_id"])
            assert avail_row is not None
            avail_row.weekly_rules = [
                {"weekday": w, "start_local": "08:00", "end_local": "20:00"}
                for w in range(1, 8)
            ]
            db.add(
                PlanningContext(
                    id=uuid4(),
                    user_id=uid,
                    revision=1,
                    availability_id=ids["avail_id"],
                    availability_revision=1,
                    selected_calendar_ids=[],
                    query_start=now - timedelta(hours=1),
                    query_end=now + timedelta(days=7),
                    busy_intervals=[],
                    fetched_at=now - timedelta(minutes=5),
                    sync_state="ok",
                    calendar_errors=[],
                    event_snapshots=[],
                    coverage_state="complete",
                    manual_busy_intervals=[],
                    excluded_sources=[],
                    selected_calendar_sources=[],
                )
            )
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=uid,
                    revision=1,
                    title="Task Pull Forward",
                    description="Mô tả",
                    scope="Phạm vi",
                    extra_requirements=["ưu tiên từ 09:00 đến 12:00"],
                    time_constraint={
                        "mode": "deadline",
                        "at": (now + timedelta(days=5)).isoformat(),
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                    status="in_progress",
                    decomposition_state="complete",
                    availability={
                        "timezone": "Asia/Ho_Chi_Minh",
                        "weekly_rules": [
                            {"weekday": w, "start_local": "08:00", "end_local": "20:00"}
                            for w in range(1, 8)
                        ],
                        "preferred_time_windows": [
                            {"weekday": w, "start_local": "09:00", "end_local": "12:00"}
                            for w in range(1, 8)
                        ],
                    },
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=uid,
                    task_id=task_id,
                    revision=1,
                    order_index=0,
                    title="Bước 1",
                    estimated_base_seconds=3600,
                    remaining_base_seconds=1800,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="in_progress",
                )
            )
            await db.flush()
            # Phiên 1 đã completed sớm; Phiên 2 nằm tận 15:00 +07:00 (08:00 UTC) -> trống 09:00-12:00 +07:00 (02:00-05:00 UTC)
            s2_start = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
            db.add_all(
                [
                    SessionRow(
                        id=s1_id,
                        user_id=uid,
                        step_id=step_id,
                        scheduled_start=now - timedelta(hours=1),
                        scheduled_end=now - timedelta(minutes=30),
                        duration_seconds=1800,
                        work_seconds=1500,
                        rest_seconds=300,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        schedule_revision=1,
                        status="completed",
                        start_state="started",
                    ),
                    SessionRow(
                        id=s2_id,
                        user_id=uid,
                        step_id=step_id,
                        scheduled_start=s2_start,
                        scheduled_end=s2_start + timedelta(minutes=30),
                        duration_seconds=1800,
                        work_seconds=1500,
                        rest_seconds=300,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        schedule_revision=1,
                        status="scheduled",
                        start_state="waiting",
                    ),
                    ScheduleVersionRow(
                        user_id=uid,
                        version_number=1,
                        base_version=None,
                        approved_at=now - timedelta(hours=2),
                        affected_task_ids=[str(task_id)],
                        min_inter_session_gap_seconds=900,
                    ),
                ]
            )
            await db.commit()
            await db.refresh(avail_row)

            prop = await reschedule_service.create_proposal(
                db,
                user_id=str(uid),
                idempotency_key="h4-07-pull",
                payload=CreateProposalRequest(
                    trigger_reason="early_completion_pull_forward",
                    target_task_id=str(task_id),
                    allowed_task_ids=[str(task_id)],
                    expected_base_schedule_revision=1,
                    expected_source_revisions=ProposalSourceRevisions(
                        task=1, schedule=1, availability=avail_row.revision, settings=1, calendar=1
                    ),
                ),
                now=now,
            )
            moves = prop.proposed_changes.sessions_to_move
            assert len(moves) == 1
            assert moves[0].session_id == str(s2_id)
            assert moves[0].to_start is not None
            assert moves[0].to_start < s2_start
            # Tôn trọng preferred_time_windows 09:00-12:00 +07:00 = 02:00-05:00 UTC
            assert moves[0].to_start == datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h4_07_delete_task_keeps_global_live_run_guard_and_task_scoped_awaiting_checkin():
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_and_connection(db, now=now)
            uid = ids["user_id"]

            t_a, st_a, sess_a = uuid4(), uuid4(), uuid4()
            t_b, st_b, sess_b = uuid4(), uuid4(), uuid4()
            db.add_all(
                [
                    TaskRow(
                        id=t_a,
                        user_id=uid,
                        revision=1,
                        title="Task A",
                        description="D",
                        scope="S",
                        time_constraint={"mode": "deadline", "at": (now + timedelta(days=5)).isoformat()},
                        status="scheduled",
                        decomposition_state="complete",
                    ),
                    TaskRow(
                        id=t_b,
                        user_id=uid,
                        revision=1,
                        title="Task B",
                        description="D",
                        scope="S",
                        time_constraint={"mode": "deadline", "at": (now + timedelta(days=5)).isoformat()},
                        status="scheduled",
                        decomposition_state="complete",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    StepRow(
                        id=st_a,
                        user_id=uid,
                        task_id=t_a,
                        order_index=0,
                        title="St A",
                        estimated_base_seconds=1500,
                        remaining_base_seconds=1500,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="scheduled",
                    ),
                    StepRow(
                        id=st_b,
                        user_id=uid,
                        task_id=t_b,
                        order_index=0,
                        title="St B",
                        estimated_base_seconds=1500,
                        remaining_base_seconds=1500,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="scheduled",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    SessionRow(
                        id=sess_a,
                        user_id=uid,
                        step_id=st_a,
                        scheduled_start=now + timedelta(hours=2),
                        scheduled_end=now + timedelta(hours=2, minutes=30),
                        duration_seconds=1800,
                        work_seconds=1500,
                        rest_seconds=300,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        schedule_revision=1,
                        status="scheduled",
                    ),
                    SessionRow(
                        id=sess_b,
                        user_id=uid,
                        step_id=st_b,
                        scheduled_start=now + timedelta(hours=4),
                        scheduled_end=now + timedelta(hours=4, minutes=30),
                        duration_seconds=1800,
                        work_seconds=1500,
                        rest_seconds=300,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        schedule_revision=1,
                        status="scheduled",
                    ),
                ]
            )
            await db.flush()
            run_b = SessionRun(
                id=uuid4(),
                user_id=uid,
                session_id=sess_b,
                device_id="dev-h4-07",
                started_at=now,
                run_state="running",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=60,
                rest_elapsed_seconds=0,
                outcome="unknown",
                settings_snapshot={"effective_revision": 1},
            )
            db.add(run_b)
            await db.commit()

            # 1) running trên Task B chặn xóa Task A -> 409 ACTIVE_RUN_EXISTS
            headers = _auth_headers(uid, "h4-07-del-blocked")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r_blocked = await client.delete(f"/api/v1/tasks/{t_a}", headers=headers)
                assert r_blocked.status_code == 409
                assert r_blocked.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"

            # 2) awaiting_checkin trên Task B KHÔNG chặn xóa Task A
            run_b_row = await db.get(SessionRun, run_b.id)
            assert run_b_row is not None
            run_b_row.run_state = "awaiting_checkin"
            run_b_row.checkin_timeout_at = datetime.now(UTC) + timedelta(minutes=5)
            db.add(
                CheckIn(
                    id=uuid4(),
                    user_id=uid,
                    run_id=run_b.id,
                    kind="final",
                    response_state="open",
                    session_outcome="unknown",
                    asked_at=datetime.now(UTC),
                    response_revision=0,
                )
            )
            await db.commit()

            headers2 = _auth_headers(uid, "h4-07-del-allowed")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r_ok = await client.delete(f"/api/v1/tasks/{t_a}", headers=headers2)
                assert r_ok.status_code == 200, r_ok.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
