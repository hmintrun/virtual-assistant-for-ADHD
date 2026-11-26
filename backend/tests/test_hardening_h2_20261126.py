"""Bộ kiểm thử Hardening Batch H2 (`H2-01` .. `H2-03`): UF3 Devices, D2-Safe & Reconcile."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import Base
from app.models.calendar import CalendarConnection, PlanningContext
from app.models.execution import CheckIn, SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.execution import SessionStartRequest
from app.schemas.settings import SettingsChangeRequest
from app.schemas.user import CurrentUser
from app.services import device_service, run_service, settings_service
from app.services.credential_crypto import encrypt_credentials


async def _make_env(db_url: str = "sqlite+aiosqlite:///:memory:"):
    engine = make_engine(db_url)
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
        email=f"h2_{user_id.hex[:8]}@example.com",
        display_name="H2 Tester",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-{user_id.hex[:8]}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _seed_h2_user(
    db: AsyncSession,
    *,
    now: datetime,
    reminder_offsets: list[int] | None = None,
    with_disconnected_cal: bool = False,
) -> dict[str, UUID]:
    uid = uuid4()
    avail_id = uuid4()
    conn_id = uuid4()
    task_id = uuid4()
    step_id = uuid4()

    db.add(
        User(
            id=uid,
            email=f"h2_{uid.hex[:8]}@example.com",
            hashed_password="pw",
            display_name="H2 Tester",
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
            reminder_offsets=reminder_offsets if reminder_offsets is not None else [900, 600, 300],
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
                {"weekday": w, "start_local": "00:00", "end_local": "23:59"}
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
    if with_disconnected_cal:
        creds = {
            "access_token": "acc_h2",
            "refresh_token": "ref_h2",
            "expires_in": 3600,
            "stored_at": now.isoformat(),
            "user_id": str(uid),
            "connection_id": str(conn_id),
        }
        db.add(
            CalendarConnection(
                id=conn_id,
                user_id=uid,
                revision=1,
                provider="google",
                account_ref=f"h2_{uid.hex[:8]}@example.com",
                read_calendar_ids=["primary"],
                write_calendar_id="proplan_secondary",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="disconnected",
                credential_ref=encrypt_credentials(creds),
            )
        )
    db.add(
        TaskRow(
            id=task_id,
            user_id=uid,
            revision=1,
            title="Công việc H2",
            description="Mô tả H2",
            scope="Phạm vi H2",
            extra_requirements=[],
            time_constraint={
                "mode": "deadline",
                "at": (now + timedelta(days=10)).isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            },
            importance=1,
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
            revision=1,
            order_index=0,
            title="Bước 1 H2",
            expected_output="Xong bước 1",
            estimated_base_seconds=3000,
            remaining_base_seconds=3000,
            buffer_multiplier=1.2,
            estimate_source="ai",
            status="scheduled",
        )
    )
    db.add(
        ScheduleVersionRow(
            user_id=uid,
            version_number=1,
            base_version=None,
            approved_at=now - timedelta(hours=1),
            affected_task_ids=[str(task_id)],
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )
    await db.flush()
    return {
        "user_id": uid,
        "avail_id": avail_id,
        "conn_id": conn_id,
        "task_id": task_id,
        "step_id": step_id,
    }


def _make_session(
    *,
    uid: UUID,
    step_id: UUID,
    start: datetime,
    title: str = "Phiên H2",
) -> SessionRow:
    return SessionRow(
        id=uuid4(),
        user_id=uid,
        step_id=step_id,
        revision=1,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=30),
        title=title,
        session_goal=f"Mục tiêu {title}",
        expected_output=f"Đầu ra {title}",
        micro_task_text="Mở tài liệu",
        micro_task_seconds=120,
        work_seconds=1500,
        rest_seconds=300,
        segments=[
            {"order_index": 0, "kind": "work", "duration_seconds": 1500},
            {"order_index": 1, "kind": "rest", "duration_seconds": 300},
        ],
        duration_seconds=1800,
        schedule_revision=1,
        status="scheduled",
        start_state="waiting",
        start_decision_due_at=start + timedelta(minutes=5),
    )


async def test_h2_01_device_reminders_survive_repeated_offset_changes_without_dedupe_collision(
    monkeypatch: pytest.MonkeyPatch,
):
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        monkeypatch.setattr(device_service, "_now", lambda: now)
        monkeypatch.setattr(settings_service, "_utc_now", lambda: now)
        sess_start = now + timedelta(hours=2)

        async with sf() as db:
            ids = await _seed_h2_user(db, now=now, reminder_offsets=[900, 600, 300])
            uid = ids["user_id"]
            sess = _make_session(uid=uid, step_id=ids["step_id"], start=sess_start)
            db.add(sess)
            await db.commit()

            # 1) Poll ban đầu với [900, 600, 300]
            res1 = await device_service.list_reminders(
                db,
                user_id=str(uid),
                device_id="dev-h2-01",
                from_time=now,
                to_time=now + timedelta(hours=4),
            )
            scheduled1 = [r for r in res1.items if r.delivery_state == "scheduled"]
            assert len(scheduled1) == 3

            # 2) Đổi reminder_offsets -> [600, 300] (effective_revision = 2)
            await settings_service.apply_settings_change(
                user_id=str(uid),
                idempotency_key="h2-01-chg-1",
                payload=SettingsChangeRequest(
                    expected_preferences_revision=1,
                    changes={"preferences": {"reminder_offsets": [600, 300]}},  # type: ignore[arg-type]
                ),
                db=db,
            )
            res2 = await device_service.list_reminders(
                db,
                user_id=str(uid),
                device_id="dev-h2-01",
                from_time=now,
                to_time=now + timedelta(hours=4),
            )
            scheduled2 = [r for r in res2.items if r.delivery_state == "scheduled"]
            assert len(scheduled2) == 2
            assert {round((sess_start - r.fire_at).total_seconds()) for r in scheduled2} == {600, 300}

            # 3) Đổi lại reminder_offsets -> [900, 600, 300] (effective_revision = 3) không đụng UniqueConstraint
            await settings_service.apply_settings_change(
                user_id=str(uid),
                idempotency_key="h2-01-chg-2",
                payload=SettingsChangeRequest(
                    expected_preferences_revision=2,
                    changes={"preferences": {"reminder_offsets": [900, 600, 300]}},  # type: ignore[arg-type]
                ),
                db=db,
            )
            res3 = await device_service.list_reminders(
                db,
                user_id=str(uid),
                device_id="dev-h2-01",
                from_time=now,
                to_time=now + timedelta(hours=4),
            )
            scheduled3 = [r for r in res3.items if r.delivery_state == "scheduled"]
            assert len(scheduled3) == 3
            assert {round((sess_start - r.fire_at).total_seconds()) for r in scheduled3} == {
                900,
                600,
                300,
            }

            all_dev_rems = (
                await db.scalars(
                    select(Reminder).where(
                        Reminder.user_id == uid,
                        Reminder.device_id == "dev-h2-01",
                    )
                )
            ).all()
            cancelled_count = sum(1 for r in all_dev_rems if r.delivery_state == "cancelled")
            assert cancelled_count >= 5
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h2_01_device_reminders_preserve_snoozed_and_do_not_duplicate_delivered_on_style_change(
    monkeypatch: pytest.MonkeyPatch,
):
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        monkeypatch.setattr(device_service, "_now", lambda: now)
        monkeypatch.setattr(settings_service, "_utc_now", lambda: now)
        sess_start = now + timedelta(hours=2)

        async with sf() as db:
            ids = await _seed_h2_user(db, now=now, reminder_offsets=[900, 600])
            uid = ids["user_id"]
            sess = _make_session(uid=uid, step_id=ids["step_id"], start=sess_start)
            db.add(sess)
            await db.commit()

            res1 = await device_service.list_reminders(
                db,
                user_id=str(uid),
                device_id="dev-h2-style",
                from_time=now,
                to_time=now + timedelta(hours=4),
            )
            assert len(res1.items) == 2

            # Đặt 1 reminder thành snoozed (900s), 1 reminder thành delivered (600s)
            rows = (
                await db.scalars(
                    select(Reminder)
                    .where(Reminder.user_id == uid, Reminder.device_id == "dev-h2-style")
                    .order_by(Reminder.fire_at.asc())
                )
            ).all()
            assert len(rows) == 2
            snoozed_id = rows[0].id
            delivered_id = rows[1].id
            rows[0].delivery_state = "snoozed"
            rows[1].delivery_state = "delivered"
            await db.commit()

            # Đổi reminder_style -> tăng pref.effective_revision nhưng KHÔNG đổi reminder_offsets
            await settings_service.apply_settings_change(
                user_id=str(uid),
                idempotency_key="h2-01-style-chg",
                payload=SettingsChangeRequest(
                    expected_preferences_revision=1,
                    changes={"preferences": {"reminder_style": "upbeat"}},  # type: ignore[arg-type]
                ),
                db=db,
            )

            res2 = await device_service.list_reminders(
                db,
                user_id=str(uid),
                device_id="dev-h2-style",
                from_time=now,
                to_time=now + timedelta(hours=4),
            )
            assert len(res2.items) == 2
            by_id = {r.reminder_id: r for r in res2.items}
            assert by_id[str(snoozed_id)].delivery_state == "snoozed"
            assert by_id[str(delivered_id)].delivery_state == "delivered"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h2_02_d2_safe_ignores_disconnected_calendar_event_but_enforces_manual_busy(
    monkeypatch: pytest.MonkeyPatch,
):
    engine, sf = await _make_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        s1_start = now
        s2_start = now + timedelta(hours=3)
        monkeypatch.setattr(run_service, "_current_utc_now", lambda dt=None: (dt or s1_start).astimezone(UTC))

        async with sf() as db:
            ids = await _seed_h2_user(db, now=now, with_disconnected_cal=True)
            uid = ids["user_id"]
            conn_id = ids["conn_id"]

            sess1 = _make_session(uid=uid, step_id=ids["step_id"], start=s1_start, title="Phiên Calendar Ngắt")
            sess2 = _make_session(uid=uid, step_id=ids["step_id"], start=s2_start, title="Phiên Manual Busy")
            db.add_all([sess1, sess2])
            db.add(
                PlanningContext(
                    id=uuid4(),
                    user_id=uid,
                    revision=1,
                    availability_id=ids["avail_id"],
                    availability_revision=1,
                    selected_calendar_ids=["primary"],
                    query_start=now - timedelta(hours=1),
                    query_end=now + timedelta(days=7),
                    busy_intervals=[
                        {
                            "start_at": s1_start.isoformat(),
                            "end_at": (s1_start + timedelta(minutes=20)).isoformat(),
                            "source": {
                                "kind": "calendar_event",
                                "connection_id": str(conn_id),
                                "google_calendar_id": "primary",
                            },
                        }
                    ],
                    fetched_at=now - timedelta(minutes=5),
                    sync_state="valid",
                    calendar_errors=[],
                    event_snapshots=[],
                    coverage_state="complete",
                    manual_busy_intervals=[
                        {
                            "start_at": s2_start.isoformat(),
                            "end_at": (s2_start + timedelta(minutes=20)).isoformat(),
                            "source": {
                                "kind": "manual_busy",
                                "connection_id": str(conn_id),
                            },
                        }
                    ],
                    excluded_sources=[],
                    selected_calendar_sources=[],
                )
            )
            await db.commit()

            # 1) Trùng calendar_event của kết nối đã disconnected -> BỎ QUA -> start 201 OK
            headers1 = _auth_headers(uid, "h2-02-start-1")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r1 = await client.post(
                    f"/api/v1/sessions/{sess1.id}/start",
                    json=SessionStartRequest(
                        expected_schedule_revision=1,
                        device_id="dev-h2-02",
                        requested_start_at=s1_start,
                    ).model_dump(mode="json"),
                    headers=headers1,
                )
            assert r1.status_code == 201, r1.text
            assert r1.json()["data"]["run_state"] == "running"

            # Kết thúc run1 để test sess2 không bị vướng active run
            async with sf() as db_upd:
                run1_id = UUID(r1.json()["data"]["run_id"])
                run1_row = await db_upd.get(SessionRun, run1_id)
                assert run1_row is not None
                run1_row.run_state = "completed"
                sess1_row = await db_upd.get(SessionRow, sess1.id)
                assert sess1_row is not None
                sess1_row.status = "completed"
                await db_upd.commit()

            # 2) Trùng manual_busy_intervals -> vẫn BỊ CHẶN 409 REPLAN_REQUIRED
            monkeypatch.setattr(run_service, "_current_utc_now", lambda dt=None: (dt or s2_start).astimezone(UTC))
            async with sf() as db2:
                with pytest.raises(AppError) as exc_s2:
                    await run_service.start_session_run(
                        db2,
                        user_id=str(uid),
                        session_id=str(sess2.id),
                        idempotency_key="h2-02-start-2",
                        payload=SessionStartRequest(
                            expected_schedule_revision=1,
                            device_id="dev-h2-02",
                            requested_start_at=s2_start,
                        ),
                    )
            assert exc_s2.value.status_code == 409
            assert exc_s2.value.code == "START_REQUIRES_REPLAN"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_h2_03_reconcile_expired_checkins_rolls_back_read_tx_before_lock_owner(
    tmp_path: Path,
):
    db_file = tmp_path / "h2_03_reconcile_concurrent.db"
    engine, sf = await _make_env(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    try:
        now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_h2_user(db, now=now)
            uid = ids["user_id"]

            # Phiên 1 có CheckIn đã hết hạn
            s1 = _make_session(uid=uid, step_id=ids["step_id"], start=now - timedelta(hours=2), title="S1 Expired CI")
            s1.status = "awaiting_checkin"
            s1.start_state = "started"
            db.add(s1)
            await db.flush()
            run1 = SessionRun(
                id=uuid4(),
                user_id=uid,
                session_id=s1.id,
                device_id="dev-h2-03",
                started_at=now - timedelta(hours=2),
                ended_at=now - timedelta(hours=1, minutes=30),
                run_state="awaiting_checkin",
                active_phase="ended",
                segment_index=1,
                work_elapsed_seconds=1500,
                rest_elapsed_seconds=0,
                actual_work_seconds=1500,
                actual_source="timer",
                outcome="unknown",
                checkin_timeout_at=now - timedelta(minutes=10),
                settings_snapshot={"effective_revision": 1},
            )
            db.add(run1)
            await db.flush()
            ci = CheckIn(
                id=uuid4(),
                user_id=uid,
                run_id=run1.id,
                kind="final",
                response_state="open",
                session_outcome="unknown",
                asked_at=now - timedelta(hours=1, minutes=30),
                response_revision=0,
            )
            db.add(ci)

            # Phiên 2 quá grace window chưa start
            s2 = _make_session(uid=uid, step_id=ids["step_id"], start=now - timedelta(minutes=20), title="S2 Overdue")
            db.add(s2)
            await db.commit()
            ci_id, s2_id = ci.id, s2.id

        async def _call_reconcile():
            async with sf() as session:
                return await run_service.reconcile_expired_checkins(
                    session,
                    str(uid),
                    now=now,
                    commit_if_read=True,
                )

        results = await asyncio.gather(
            _call_reconcile(),
            _call_reconcile(),
            _call_reconcile(),
            _call_reconcile(),
        )
        assert any(results)

        async with sf() as db:
            ci_after = await db.get(CheckIn, ci_id)
            s2_after = await db.get(SessionRow, s2_id)
            assert ci_after is not None and ci_after.response_state == "no_respond"
            assert s2_after is not None and s2_after.start_state == "not_started"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
