"""Bộ test hồi quy chuyên sâu cho các Bugs & Risks phân hệ UF6 (Phases 1–4).

Hỗ trợ chạy trên cả SQLite (:memory:) và PostgreSQL thật qua biến môi trường
`PROACTIVE_TEST_PG_URL` (hoặc `TEST_POSTGRES_URL`).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import CurrentUser, get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models import Base
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
)
from app.models.execution import SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import Availability as AvailabilityRow
from app.models.settings import SettingChangeRequest
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.services import reschedule_service
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import (
    CalendarAuthError,
    FakeCalendarClient,
    get_calendar_client,
    reset_fake_calendar_client,
)

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _auth_headers(user_id: UUID, idempotency_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"uf6_{user_id.hex[:10]}@example.com",
        display_name="UF6 Tester",
        is_active=True,
    )
    headers: dict[str, str] = {}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _truncate_pg_tables(engine: AsyncEngine) -> None:
    preparer = engine.dialect.identifier_preparer
    table_names = [preparer.format_table(table) for table in reversed(Base.metadata.sorted_tables)]
    if not table_names:
        return
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE")
        )


@asynccontextmanager
async def _regression_db_env() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    pg_url = (
        os.environ.get("PROACTIVE_TEST_PG_URL") or os.environ.get("TEST_POSTGRES_URL") or ""
    ).strip()
    url = pg_url if pg_url else "sqlite+aiosqlite:///:memory:"
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if pg_url:
        await _truncate_pg_tables(engine)

    sf = session_factory(engine)

    async def _override_db():
        async with sf() as session:
            yield session

    previous_db_override = app.dependency_overrides.get(get_db)
    previous_user_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = _override_db
    try:
        yield sf
    finally:
        if previous_db_override is not None:
            app.dependency_overrides[get_db] = previous_db_override
        else:
            app.dependency_overrides.pop(get_db, None)
        if previous_user_override is not None:
            app.dependency_overrides[get_current_user] = previous_user_override
        else:
            app.dependency_overrides.pop(get_current_user, None)
        if pg_url:
            await _truncate_pg_tables(engine)
        await engine.dispose()


async def _seed_continuation_world(
    db: AsyncSession,
    *,
    task_b_start: datetime | None,
    task_a_session2_start: datetime | None = None,
    now_anchor: datetime | None = None,
) -> dict[str, Any]:
    """Tạo user, preferences, availability (08:00–22:00 mọi ngày), Task A + Run 1 kết thúc 14:25,
    tùy chọn Task A Session 2 (movable) và Task B Session cố định (unallowed)."""
    anchor = now_anchor or datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    user_id = uuid4()
    db.add(
        User(
            id=user_id,
            email=f"uf6_{user_id.hex[:10]}@example.com",
            display_name="UF6 Tester",
        )
    )
    await db.flush()

    db.add(
        UserPreferenceRow(
            user_id=user_id,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            reminder_style="gentle_sound",
            buffer_multiplier=1.2,
            effective_revision=1,
        )
    )
    db.add(
        AvailabilityRow(
            user_id=user_id,
            timezone="Asia/Ho_Chi_Minh",
            weekly_rules=[
                {"weekday": wd, "start_local": "08:00", "end_local": "22:00"}
                for wd in range(1, 8)
            ],
            date_overrides=[],
            source="manual",
            confirmed_at=anchor - timedelta(days=1),
            revision=1,
        )
    )

    task_a_id = uuid4()
    step_a_id = uuid4()
    sess_a1_id = uuid4()
    run_a1_id = uuid4()
    db.add(
        TaskRow(
            id=task_a_id,
            user_id=user_id,
            title="Task A",
            description="Làm báo cáo A",
            scope="3 chương",
            time_constraint={
                "kind": "deadline",
                "deadline_at": (anchor + timedelta(days=3)).astimezone(UTC).isoformat(),
            },
            status="scheduled",
            revision=1,
        )
    )
    await db.flush()
    db.add(
        StepRow(
            id=step_a_id,
            user_id=user_id,
            task_id=task_a_id,
            order_index=0,
            title="Bước A1",
            expected_output="Xong A1",
            estimated_base_seconds=3600,
            remaining_base_seconds=1800,
            buffer_multiplier=1.2,
            estimate_source="ai",
            status="in_progress",
        )
    )
    await db.flush()

    a1_start = (anchor - timedelta(minutes=25)).astimezone(UTC)
    a1_end = anchor.astimezone(UTC)
    db.add(
        SessionRow(
            id=sess_a1_id,
            user_id=user_id,
            step_id=step_a_id,
            scheduled_start=a1_start,
            scheduled_end=a1_end,
            micro_task_text="Mở tài liệu A",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=0,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
            duration_seconds=1500,
            schedule_revision=3,
            status="incomplete",
            start_state="started",
            start_decision_due_at=a1_start + timedelta(minutes=15),
        )
    )
    await db.flush()
    db.add(
        SessionRun(
            id=run_a1_id,
            user_id=user_id,
            session_id=sess_a1_id,
            device_id="android_pixel_8",
            started_at=a1_start,
            ended_at=a1_end,
            run_state="awaiting_checkin",
            active_phase="work",
            segment_index=0,
            work_elapsed_seconds=1500,
            rest_elapsed_seconds=0,
            actual_work_seconds=1500,
            actual_source="timer_proxy",
            outcome="incomplete",
            revision=1,
            settings_snapshot={},
        )
    )

    sess_a2_id: UUID | None = None
    if task_a_session2_start is not None:
        sess_a2_id = uuid4()
        a2_start = task_a_session2_start.astimezone(UTC)
        a2_end = a2_start + timedelta(seconds=1800)
        db.add(
            SessionRow(
                id=sess_a2_id,
                user_id=user_id,
                step_id=step_a_id,
                scheduled_start=a2_start,
                scheduled_end=a2_end,
                micro_task_text="Tiếp tục A",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=300,
                segments=[
                    {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                ],
                duration_seconds=1800,
                schedule_revision=3,
                status="scheduled",
                start_state="waiting",
                start_decision_due_at=a2_start + timedelta(minutes=15),
            )
        )

    task_b_id: UUID | None = None
    sess_b_id: UUID | None = None
    if task_b_start is not None:
        task_b_id = uuid4()
        step_b_id = uuid4()
        sess_b_id = uuid4()
        db.add(
            TaskRow(
                id=task_b_id,
                user_id=user_id,
                title="Task B Fixed",
                description="Việc B cố định",
                scope="1 phần",
                time_constraint={
                    "kind": "deadline",
                    "deadline_at": (anchor + timedelta(days=3)).astimezone(UTC).isoformat(),
                },
                status="scheduled",
                revision=1,
            )
        )
        await db.flush()
        db.add(
            StepRow(
                id=step_b_id,
                user_id=user_id,
                task_id=task_b_id,
                order_index=0,
                title="Bước B1",
                expected_output="Xong B1",
                estimated_base_seconds=1500,
                remaining_base_seconds=1500,
                buffer_multiplier=1.2,
                estimate_source="ai",
                status="pending",
            )
        )
        await db.flush()
        b_start = task_b_start.astimezone(UTC)
        b_end = b_start + timedelta(seconds=1500)
        db.add(
            SessionRow(
                id=sess_b_id,
                user_id=user_id,
                step_id=step_b_id,
                scheduled_start=b_start,
                scheduled_end=b_end,
                micro_task_text="Mở tài liệu B",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=3,
                status="scheduled",
                start_state="waiting",
                start_decision_due_at=b_start + timedelta(minutes=15),
            )
        )

    db.add(
        ScheduleVersionRow(
            user_id=user_id,
            version_number=3,
            base_version=2,
            approved_at=anchor - timedelta(hours=1),
            affected_task_ids=[str(task_a_id)] + ([str(task_b_id)] if task_b_id else []),
            min_inter_session_gap_seconds=900,
            alarm_sync_state="synced",
        )
    )
    await db.commit()
    return {
        "user_id": user_id,
        "task_a_id": task_a_id,
        "step_a_id": step_a_id,
        "sess_a1_id": sess_a1_id,
        "sess_a2_id": sess_a2_id,
        "run_a1_id": run_a1_id,
        "task_b_id": task_b_id,
        "sess_b_id": sess_b_id,
    }


@pytest.mark.asyncio
async def test_p1_rg02_continuation_exact_boundary_before_fixed_task_b_without_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-01 / RG02: Task B cố định lúc 14:57, busy_items=[], cont_start=14:27, work=900s:
    - start_window.latest_start phải là đúng 14:27:00 (không trượt thành 14:32:00).
    - approve lúc 14:27:01 (gap 899s) và 14:29:00 (gap 780s) -> 409 PREVIEW_STALE, DB nguyên vẹn.
    - approve lúc 14:27:00 (gap đúng 900s) -> 200 OK.
    """
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    clock = {"now": anchor}
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or clock["now"])

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=datetime(2026, 11, 2, 14, 57, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "rg02-exact-create"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(ids["run_a1_id"]),
                        "expected_run_revision": 1,
                        "additional_work_seconds": 900,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task_a_id"])],
                    },
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            prop = create_resp.json()["data"]
            proposal_id = prop["proposal_id"]

            expected_latest = datetime(2026, 11, 2, 14, 27, 0, tzinfo=TZ_VN).astimezone(UTC)
            actual_latest = datetime.fromisoformat(
                prop["start_window"]["latest_start"].replace("Z", "+00:00")
            )
            assert actual_latest == expected_latest

            # Quá hạn 1 giây (14:27:01 -> gap 899s < 900s)
            clock["now"] = datetime(2026, 11, 2, 14, 27, 1, tzinfo=TZ_VN)
            curr_resp = await client.get(
                "/api/v1/proposals/current",
                headers=_auth_headers(ids["user_id"]),
            )
            assert curr_resp.status_code == 200
            assert curr_resp.json()["data"]["stale"] is True

            for label, late_time in [
                ("1s", datetime(2026, 11, 2, 14, 27, 1, tzinfo=TZ_VN)),
                ("2m", datetime(2026, 11, 2, 14, 29, 0, tzinfo=TZ_VN)),
            ]:
                clock["now"] = late_time
                late_resp = await client.post(
                    f"/api/v1/proposals/{proposal_id}/approve",
                    headers=_auth_headers(ids["user_id"], f"rg02-late-{label}"),
                    json={
                        "expected_proposal_revision": 1,
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": prop["source_revisions"],
                        "start_continuation": True,
                        "device_id": "android_pixel_8",
                        "event_id": f"evt_rg02_late_{label}",
                        "expected_source_run_revision": 1,
                    },
                )
                assert late_resp.status_code == 409, late_resp.text
                assert late_resp.json()["error"]["code"] == "PREVIEW_STALE"

            # DB giữ nguyên, proposal vẫn pending
            async with sf() as db:
                p_row = await db.get(RescheduleProposal, UUID(proposal_id))
                assert p_row is not None and p_row.status == "pending"

            # Duyệt đúng biên 14:27:00 (gap chính xác 900s) -> 200 OK
            clock["now"] = datetime(2026, 11, 2, 14, 27, 0, tzinfo=TZ_VN)
            ok_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "rg02-exact-ok"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                    "start_continuation": True,
                    "device_id": "android_pixel_8",
                    "event_id": "evt_rg02_ok",
                    "expected_source_run_revision": 1,
                },
            )
            assert ok_resp.status_code == 200, ok_resp.text
            assert ok_resp.json()["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_p1_rg02_multi_hop_chain_propagates_fixed_task_b_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-01 / RG02 (Multi-hop): Task A S2 movable (14:40, dur 1800s) + Task B fixed (15:43).
    Cont 14:27 (work 900s) đẩy A S2 -> 14:57..15:27; Task B 15:43 kẹp A S2 latest_start=14:58
    -> lan truyền ngược về cont_latest_start = 14:28:00 (< 14:32:00 mặc định).
    """
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    clock = {"now": anchor}
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or clock["now"])

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_a_session2_start=datetime(2026, 11, 2, 14, 40, 0, tzinfo=TZ_VN),
                task_b_start=datetime(2026, 11, 2, 15, 43, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "rg02-hop-create"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(ids["run_a1_id"]),
                        "expected_run_revision": 1,
                        "additional_work_seconds": 900,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task_a_id"])],
                    },
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            prop = create_resp.json()["data"]
            proposal_id = prop["proposal_id"]

            expected_latest = datetime(2026, 11, 2, 14, 28, 0, tzinfo=TZ_VN).astimezone(UTC)
            actual_latest = datetime.fromisoformat(
                prop["start_window"]["latest_start"].replace("Z", "+00:00")
            )
            assert actual_latest == expected_latest

            clock["now"] = datetime(2026, 11, 2, 14, 28, 1, tzinfo=TZ_VN)
            late_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "rg02-hop-late"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                    "start_continuation": True,
                    "device_id": "android_pixel_8",
                    "event_id": "evt_rg02_hop_late",
                    "expected_source_run_revision": 1,
                },
            )
            assert late_resp.status_code == 409
            assert late_resp.json()["error"]["code"] == "PREVIEW_STALE"

            clock["now"] = datetime(2026, 11, 2, 14, 28, 0, tzinfo=TZ_VN)
            ok_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "rg02-hop-ok"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                    "start_continuation": True,
                    "device_id": "android_pixel_8",
                    "event_id": "evt_rg02_hop_ok",
                    "expected_source_run_revision": 1,
                },
            )
            assert ok_resp.status_code == 200, ok_resp.text


@pytest.mark.asyncio
async def test_p1_rg02_live_revalidation_blocks_newly_inserted_task_c_on_approve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-01 / RG02 (Live DB Re-validation): Sau khi tạo Continuation hợp lệ,
    một phiên cố định Task C mới chen vào 14:55 (gap 780s < 900s) -> GET báo stale=True
    và approve trả 409 PREVIEW_STALE dù start_window tĩnh chưa quá hạn."""
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    clock = {"now": anchor}
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or clock["now"])

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                now_anchor=anchor,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "rg02-live-create"),
                json={
                    "trigger_reason": "checkin_incomplete",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                    "continuation_request": {
                        "mode": "continue_now",
                        "source_run_id": str(ids["run_a1_id"]),
                        "expected_run_revision": 1,
                        "additional_work_seconds": 900,
                        "strategy": "extend_then_shift_later_sessions",
                        "immediate_continuation": True,
                        "borrow_break_seconds": 0,
                        "allowed_task_ids": [str(ids["task_a_id"])],
                    },
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            prop = create_resp.json()["data"]
            proposal_id = prop["proposal_id"]

            # Chèn trực tiếp 1 phiên Task C cố định lúc 14:55 (không tăng ScheduleVersion để cô lập live check)
            async with sf() as db:
                task_c_id = uuid4()
                step_c_id = uuid4()
                c_start = datetime(2026, 11, 2, 14, 55, 0, tzinfo=TZ_VN).astimezone(UTC)
                db.add(
                    TaskRow(
                        id=task_c_id,
                        user_id=ids["user_id"],
                        title="Task C Chen Ngang",
                        description="Việc C",
                        scope="1 phần",
                        time_constraint={
                            "kind": "deadline",
                            "deadline_at": (anchor + timedelta(days=3)).astimezone(UTC).isoformat(),
                        },
                        status="scheduled",
                        revision=1,
                    )
                )
                await db.flush()
                db.add(
                    StepRow(
                        id=step_c_id,
                        user_id=ids["user_id"],
                        task_id=task_c_id,
                        order_index=0,
                        title="Bước C1",
                        expected_output="Xong C1",
                        estimated_base_seconds=1500,
                        remaining_base_seconds=1500,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="pending",
                    )
                )
                await db.flush()
                db.add(
                    SessionRow(
                        id=uuid4(),
                        user_id=ids["user_id"],
                        step_id=step_c_id,
                        scheduled_start=c_start,
                        scheduled_end=c_start + timedelta(seconds=1500),
                        micro_task_text="Mở C",
                        micro_task_seconds=120,
                        work_seconds=1500,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        duration_seconds=1500,
                        schedule_revision=3,
                        status="scheduled",
                        start_state="waiting",
                        start_decision_due_at=c_start + timedelta(minutes=15),
                    )
                )
                await db.commit()

            clock["now"] = datetime(2026, 11, 2, 14, 27, 0, tzinfo=TZ_VN)
            curr_resp = await client.get(
                "/api/v1/proposals/current",
                headers=_auth_headers(ids["user_id"]),
            )
            assert curr_resp.status_code == 200
            assert curr_resp.json()["data"]["stale"] is True

            approve_resp = await client.post(
                f"/api/v1/proposals/{proposal_id}/approve",
                headers=_auth_headers(ids["user_id"], "rg02-live-approve"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                    "start_continuation": True,
                    "device_id": "android_pixel_8",
                    "event_id": "evt_rg02_live_block",
                    "expected_source_run_revision": 1,
                },
            )
            assert approve_resp.status_code == 409, approve_resp.text
            assert approve_resp.json()["error"]["code"] == "PREVIEW_STALE"


@pytest.mark.asyncio
async def test_p1_bug02_supersede_clears_requested_availability_revision_and_repeated_supersede(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-02 / P4-RISK-02: Supersede proposal pending cũ (availability-only / mixed)
    phải dọn sạch cả requested_revision lẫn requested_availability_revision = None,
    kể cả khi supersede 2 lần liên tiếp."""
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Tạo proposal #1 và gắn SettingChangeRequest availability-only (requested_availability_revision=2)
            p1_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "bug02-p1"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert p1_resp.status_code == 201, p1_resp.text
            p1_id = UUID(p1_resp.json()["data"]["proposal_id"])

            req1_id = uuid4()
            async with sf() as db:
                db.add(
                    SettingChangeRequest(
                        id=req1_id,
                        user_id=ids["user_id"],
                        application_state="pending_review",
                        requested_revision=2,
                        effective_revision=1,
                        requested_availability_revision=2,
                        effective_availability_revision=1,
                        requested_changes={
                            "target_task_id": str(ids["task_a_id"]),
                            "availability": {"timezone": "Asia/Ho_Chi_Minh"},
                        },
                        pending_proposal_id=p1_id,
                    )
                )
                await db.commit()

            # 2. Tạo proposal #2 supersede proposal #1
            p2_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "bug02-p2"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert p2_resp.status_code == 201, p2_resp.text
            p2_id = UUID(p2_resp.json()["data"]["proposal_id"])
            assert p2_resp.json()["data"]["supersedes_proposal_id"] == str(p1_id)

            async with sf() as db:
                r1 = await db.get(SettingChangeRequest, req1_id)
                assert r1 is not None
                assert r1.application_state == "cancelled"
                assert r1.requested_revision is None
                assert r1.requested_availability_revision is None

                # 3. Gắn SettingChangeRequest #2 (availability-only, requested_revision=None, requested_availability_revision=3)
                req2_id = uuid4()
                db.add(
                    SettingChangeRequest(
                        id=req2_id,
                        user_id=ids["user_id"],
                        application_state="pending_proposal",
                        requested_revision=None,
                        effective_revision=1,
                        requested_availability_revision=3,
                        effective_availability_revision=1,
                        requested_changes={"availability": {"timezone": "Asia/Ho_Chi_Minh"}},
                        pending_proposal_id=p2_id,
                    )
                )
                await db.commit()

            # 4. Tạo proposal #3 supersede proposal #2
            p3_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "bug02-p3"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert p3_resp.status_code == 201, p3_resp.text
            p3_id = UUID(p3_resp.json()["data"]["proposal_id"])

            async with sf() as db:
                r2 = await db.get(SettingChangeRequest, req2_id)
                assert r2 is not None
                assert r2.application_state == "cancelled"
                assert r2.requested_revision is None
                assert r2.requested_availability_revision is None
                p3_row = await db.get(RescheduleProposal, p3_id)
                assert p3_row is not None and p3_row.status == "pending"
                all_pending = list(
                    (
                        await db.scalars(
                            select(RescheduleProposal).where(
                                RescheduleProposal.user_id == ids["user_id"],
                                RescheduleProposal.status == "pending",
                            )
                        )
                    ).all()
                )
                assert [p.id for p in all_pending] == [p3_id]


@pytest.mark.asyncio
async def test_p2_rg06_relink_after_external_delete_generates_v2_v3_and_syncs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-03 / RG06: Sau khi link cũ bị đánh is_current=False (deleted_externally),
    approve dời lịch ở UF6 không nổ 500 IntegrityError, sinh google_event_id thế hệ mới
    hợp chuẩn base32hex [a-v0-9] (v2, v3) và enqueue job create."""
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            conn_id = uuid4()
            sess_a2_id = ids["sess_a2_id"]
            assert sess_a2_id is not None
            db.add(
                CalendarConnection(
                    id=conn_id,
                    user_id=ids["user_id"],
                    provider="google",
                    account_ref="uf6_relink@gmail.com",
                    connection_state="connected",
                    read_calendar_ids=["primary"],
                    write_calendar_id="focus_cal",
                    granted_scopes=["https://www.googleapis.com/auth/calendar"],
                    credential_ref="enc_cred",
                )
            )
            await db.flush()
            base_event_id = f"session{sess_a2_id.hex}"
            hist_link_id = uuid4()
            db.add(
                CalendarEventLink(
                    id=hist_link_id,
                    user_id=ids["user_id"],
                    session_id=sess_a2_id,
                    connection_id=conn_id,
                    google_calendar_id="focus_cal",
                    google_event_id=base_event_id,
                    google_etag="etag-old",
                    desired_schedule_revision=3,
                    synced_schedule_revision=3,
                    sync_state="deleted_externally",
                    is_current=False,
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Lần relink 1 -> v2
            p1 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "rg06-p1"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert p1.status_code == 201, p1.text
            prop1 = p1.json()["data"]

            a1 = await client.post(
                f"/api/v1/proposals/{prop1['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "rg06-a1"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop1["source_revisions"],
                },
            )
            assert a1.status_code == 200, a1.text
            assert a1.json()["data"]["calendar_sync_job_enqueued"] is True

            async with sf() as db:
                links = list(
                    (
                        await db.scalars(
                            select(CalendarEventLink).where(
                                CalendarEventLink.session_id == sess_a2_id
                            )
                        )
                    ).all()
                )
                assert len(links) == 2
                old_lk = next(lk for lk in links if lk.id == hist_link_id)
                new_lk = next(lk for lk in links if lk.id != hist_link_id)
                assert old_lk.is_current is False
                assert old_lk.sync_state == "deleted_externally"
                assert new_lk.is_current is True
                assert new_lk.google_event_id == f"{base_event_id}v2"
                assert re.fullmatch(r"[a-v0-9]+", new_lk.google_event_id) is not None

                jobs_v4 = list(
                    (
                        await db.scalars(
                            select(CalendarSyncJob).where(
                                CalendarSyncJob.event_link_id == new_lk.id
                            )
                        )
                    ).all()
                )
                assert len(jobs_v4) == 1
                assert jobs_v4[0].operation == "create"
                assert jobs_v4[0].expected_google_etag is None
                assert jobs_v4[0].payload_snapshot["id"] == f"{base_event_id}v2"

                # Đánh v2 bị xóa ngoài tiếp -> thử relink lần 2 lên v3
                new_lk.is_current = False
                new_lk.sync_state = "deleted_externally"
                await db.commit()

            p2 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "rg06-p2"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 4,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 4,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert p2.status_code == 201, p2.text
            prop2 = p2.json()["data"]

            a2 = await client.post(
                f"/api/v1/proposals/{prop2['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "rg06-a2"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 4,
                    "expected_source_revisions": prop2["source_revisions"],
                },
            )
            assert a2.status_code == 200, a2.text

            async with sf() as db:
                current_links = list(
                    (
                        await db.scalars(
                            select(CalendarEventLink).where(
                                CalendarEventLink.session_id == sess_a2_id,
                                CalendarEventLink.is_current.is_(True),
                            )
                        )
                    ).all()
                )
                assert len(current_links) == 1
                assert current_links[0].google_event_id == f"{base_event_id}v3"
                assert re.fullmatch(r"[a-v0-9]+", current_links[0].google_event_id) is not None


@pytest.mark.asyncio
async def test_p2_rg06_moved_session_switches_write_calendar_deletes_old_and_creates_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-03: Khi user đổi write_calendar_id (old_cal -> new_cal), approve dời lịch
    đóng link cũ trên old_cal (enqueue delete) và tạo link mới trên new_cal (enqueue create)."""
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            conn_id = uuid4()
            sess_a2_id = ids["sess_a2_id"]
            assert sess_a2_id is not None
            db.add(
                CalendarConnection(
                    id=conn_id,
                    user_id=ids["user_id"],
                    provider="google",
                    account_ref="uf6_switch@gmail.com",
                    connection_state="connected",
                    read_calendar_ids=["primary"],
                    write_calendar_id="new_cal",
                    granted_scopes=["https://www.googleapis.com/auth/calendar"],
                    credential_ref="enc_cred",
                )
            )
            await db.flush()
            old_link_id = uuid4()
            db.add(
                CalendarEventLink(
                    id=old_link_id,
                    user_id=ids["user_id"],
                    session_id=sess_a2_id,
                    connection_id=conn_id,
                    google_calendar_id="old_cal",
                    google_event_id=f"session{sess_a2_id.hex}",
                    google_etag="etag-old-cal",
                    desired_schedule_revision=3,
                    synced_schedule_revision=3,
                    sync_state="synced",
                    is_current=True,
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            p1 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "rg06-switch-p1"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 1,
                    },
                },
            )
            assert p1.status_code == 201, p1.text
            prop = p1.json()["data"]
            a1 = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "rg06-switch-a1"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert a1.status_code == 200, a1.text

        async with sf() as db:
            old_lk = await db.get(CalendarEventLink, old_link_id)
            assert old_lk is not None and old_lk.is_current is False
            new_lks = list(
                (
                    await db.scalars(
                        select(CalendarEventLink).where(
                            CalendarEventLink.session_id == sess_a2_id,
                            CalendarEventLink.is_current.is_(True),
                        )
                    )
                ).all()
            )
            assert len(new_lks) == 1
            assert new_lks[0].google_calendar_id == "new_cal"
            jobs = list(
                (
                    await db.scalars(
                        select(CalendarSyncJob).where(CalendarSyncJob.user_id == ids["user_id"])
                    )
                ).all()
            )
            ops = {j.operation for j in jobs}
            assert ops == {"delete", "create"}


@pytest.mark.asyncio
async def test_p2_rg05_calendar_previews_revision_null_stale_and_double_confirm_replay() -> None:
    """UF6-BUG-04 / RG05 & Risk P1-D03: Kiểm tra đủ cả event-change-previews và configuration-previews:
    - Gán đúng base_connection_revision = conn.revision (không None, không hardcode 1).
    - expected_preview_revision=99 -> 409; None -> 422; 0 job tạo ra.
    - Connection đổi revision sau khi tạo preview -> 409 REVISION_CONFLICT.
    - Double-confirm với expected_preview_revision=1 -> idempotent 200 ở cả 2 loại preview.
    """
    async with _regression_db_env() as sf:
        user_id = uuid4()
        conn_id = uuid4()
        async with sf() as db:
            db.add(User(id=user_id, email=f"rg05_{user_id.hex[:8]}@example.com", display_name="RG05"))
            await db.flush()
            db.add(
                CalendarConnection(
                    id=conn_id,
                    user_id=user_id,
                    provider="google",
                    account_ref="rg05@gmail.com",
                    connection_state="connected",
                    read_calendar_ids=["primary"],
                    write_calendar_id="primary",
                    granted_scopes=["https://www.googleapis.com/auth/calendar"],
                    credential_ref="enc_cred",
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # --- Phần 1: Event Change Preview ---
            ev_prev = await client.post(
                "/api/v1/calendar/event-change-previews",
                headers=_auth_headers(user_id),
                json={
                    "operation": "create",
                    "connection_id": str(conn_id),
                    "google_calendar_id": "primary",
                    "payload": {"summary": "RG05 Event"},
                },
            )
            assert ev_prev.status_code == 200, ev_prev.text
            ev_data = ev_prev.json()["data"]
            assert ev_data["preview_revision"] == 1
            assert ev_data["base_connection_revision"] == 1
            ev_pid = ev_data["preview_id"]

            # 99 -> 409, None -> 422, 0 job
            r_bad_rev = await client.post(
                f"/api/v1/calendar/event-change-previews/{ev_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 99},
            )
            assert r_bad_rev.status_code == 409
            assert r_bad_rev.json()["error"]["code"] == "REVISION_CONFLICT"

            r_null_rev = await client.post(
                f"/api/v1/calendar/event-change-previews/{ev_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": None},
            )
            assert r_null_rev.status_code == 422
            assert r_null_rev.json()["error"]["code"] == "VALIDATION_ERROR"

            async with sf() as db:
                jobs_before = list(
                    (
                        await db.scalars(
                            select(CalendarSyncJob).where(CalendarSyncJob.user_id == user_id)
                        )
                    ).all()
                )
                assert len(jobs_before) == 0

            # --- Phần 2: Configuration Preview (đổi conn.revision -> 2) ---
            cfg_prev = await client.post(
                f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                headers=_auth_headers(user_id),
                json={"read_calendar_ids": ["primary"], "write_calendar_id": "primary"},
            )
            assert cfg_prev.status_code == 200, cfg_prev.text
            cfg_data = cfg_prev.json()["data"]
            assert cfg_data["preview_revision"] == 1
            assert cfg_data["base_connection_revision"] == 1
            cfg_pid = cfg_data["preview_id"]

            # None -> 422, 99 -> 409
            c_null = await client.post(
                f"/api/v1/calendar/configuration-previews/{cfg_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": None},
            )
            assert c_null.status_code == 422

            c_bad = await client.post(
                f"/api/v1/calendar/configuration-previews/{cfg_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 99},
            )
            assert c_bad.status_code == 409

            # Confirm lần 1 -> 200 (nâng conn.revision lên 2); double-confirm lần 2 -> idempotent 200 (P1-D03)
            c_ok_1 = await client.post(
                f"/api/v1/calendar/configuration-previews/{cfg_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 1},
            )
            assert c_ok_1.status_code == 200, c_ok_1.text
            c_ok_2 = await client.post(
                f"/api/v1/calendar/configuration-previews/{cfg_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 1},
            )
            assert c_ok_2.status_code == 200, c_ok_2.text
            c_replay_bad = await client.post(
                f"/api/v1/calendar/configuration-previews/{cfg_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 99},
            )
            assert c_replay_bad.status_code == 409

            # --- Phần 3: Stale connection trên ev_pid cũ (tạo lúc conn.revision == 1, nay conn.revision == 2) ---
            r_stale_conn = await client.post(
                f"/api/v1/calendar/event-change-previews/{ev_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 1},
            )
            assert r_stale_conn.status_code == 409
            assert r_stale_conn.json()["error"]["code"] == "REVISION_CONFLICT"

            # Tạo event-change preview mới trên conn.revision == 2 -> confirm + double-confirm replay 200
            ev_prev_2 = await client.post(
                "/api/v1/calendar/event-change-previews",
                headers=_auth_headers(user_id),
                json={
                    "operation": "create",
                    "connection_id": str(conn_id),
                    "google_calendar_id": "primary",
                    "payload": {"summary": "RG05 Event Rev2"},
                },
            )
            assert ev_prev_2.status_code == 200
            ev2_data = ev_prev_2.json()["data"]
            assert ev2_data["base_connection_revision"] == 2
            assert ev2_data["preview_revision"] == 1
            ev2_pid = ev2_data["preview_id"]

            r_ok_1 = await client.post(
                f"/api/v1/calendar/event-change-previews/{ev2_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 1},
            )
            assert r_ok_1.status_code == 200, r_ok_1.text
            r_ok_2 = await client.post(
                f"/api/v1/calendar/event-change-previews/{ev2_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 1},
            )
            assert r_ok_2.status_code == 200, r_ok_2.text
            assert r_ok_2.json()["data"]["job_id"] == r_ok_1.json()["data"]["job_id"]
            r_replay_bad = await client.post(
                f"/api/v1/calendar/event-change-previews/{ev2_pid}/confirm",
                headers=_auth_headers(user_id),
                json={"expected_preview_revision": 99},
            )
            assert r_replay_bad.status_code == 409


@pytest.mark.asyncio
async def test_p3_bug05_extra_requirements_daily_cap_min_slot_and_preferred_evening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-05 (A4): UF6 tôn trọng extra_requirements (max_daily_work_seconds cộng cả phiên
    completed cùng ngày, preferred_time_windows buổi tối, và min_contiguous_slot_seconds)."""
    anchor = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        # Ca 1: Daily cap 45p + Ưu tiên buổi tối (sáng đã làm xong 25p -> không dồn thêm 25p vào hôm nay, đẩy sang tối mai >= 18:00)
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            task_a = await db.get(TaskRow, ids["task_a_id"])
            assert task_a is not None
            task_a.extra_requirements = ["Mỗi ngày học tối đa 45 phút", "Ưu tiên buổi tối"]
            # Đưa sess_a1 về sáng nay (09:00-09:25) status=completed để hôm nay đã dùng 25p = 1500s
            sess_a1 = await db.get(SessionRow, ids["sess_a1_id"])
            run_a1 = await db.get(SessionRun, ids["run_a1_id"])
            assert sess_a1 is not None and run_a1 is not None
            s1_s = datetime(2026, 11, 2, 9, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            s1_e = datetime(2026, 11, 2, 9, 25, 0, tzinfo=TZ_VN).astimezone(UTC)
            sess_a1.scheduled_start = s1_s
            sess_a1.scheduled_end = s1_e
            sess_a1.status = "completed"
            run_a1.started_at = s1_s
            run_a1.ended_at = s1_e
            run_a1.actual_work_seconds = 1500
            run_a1.outcome = "completed"
            run_a1.run_state = "closed"
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "bug05-daily-pref"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 2,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]
            assert data["feasibility"]["status"] == "feasible"
            moves = data["proposed_changes"]["sessions_to_move"]
            assert len(moves) == 1
            moved_start_vn = datetime.fromisoformat(
                moves[0]["to_start"].replace("Z", "+00:00")
            ).astimezone(TZ_VN)
            # Hôm nay (02/11) đã dùng 25p -> không được xếp thêm 25p (50p > 45p); phải đẩy sang 03/11 buổi tối (>= 18:00)
            assert moved_start_vn.date() == datetime(2026, 11, 3, tzinfo=TZ_VN).date()
            assert moved_start_vn.hour >= 18

        # Ca 2: min_contiguous_slot_seconds (45p) -> bỏ qua khe 35p (15:00-15:35), chọn khe 60p (17:00-18:00)
        async with sf() as db:
            ids2 = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 14, 10, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            task_a2 = await db.get(TaskRow, ids2["task_a_id"])
            assert task_a2 is not None
            task_a2.extra_requirements = ["Chỉ xếp khi có ít nhất 45 phút trống liền"]
            task_a2.availability = {
                "timezone": "Asia/Ho_Chi_Minh",
                "weekly_rules": [],
                "date_overrides": [
                    {
                        "date": "2026-11-02",
                        "intervals": [
                            {"start_local": "15:00", "end_local": "15:35"},
                            {"start_local": "17:00", "end_local": "18:00"},
                        ],
                    }
                ],
            }
            sess_a2 = await db.get(SessionRow, ids2["sess_a2_id"])
            assert sess_a2 is not None
            sess_a2.duration_seconds = 1500
            sess_a2.work_seconds = 1500
            sess_a2.rest_seconds = 0
            sess_a2.segments = [{"order_index": 0, "kind": "work", "duration_seconds": 1500}]
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp2 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids2["user_id"], "bug05-min-slot"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids2["task_a_id"]),
                    "allowed_task_ids": [str(ids2["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 2,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert resp2.status_code == 201, resp2.text
            moves2 = resp2.json()["data"]["proposed_changes"]["sessions_to_move"]
            assert len(moves2) == 1
            chosen_vn = datetime.fromisoformat(
                moves2[0]["to_start"].replace("Z", "+00:00")
            ).astimezone(TZ_VN)
            assert chosen_vn.hour == 17 and chosen_vn.minute == 0


@pytest.mark.asyncio
async def test_p3_bug06_recent_completed_900s_buffer_and_resegment_ignores_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-06 (A4 / Bug 2.1):
    - Phiên vừa kết thúc (ended_run.ended_at = 14:24, calc_now = 14:20 -> initial_cursor 14:35)
      phải giữ đệm 900s -> phiên dời bắt đầu >= 14:39.
    - Nhánh re-segment UF7 bỏ qua phiên 'skipped' khi tính has_other_step_sessions.
    """
    anchor = datetime(2026, 11, 2, 14, 20, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        # Ca A: đệm 900s sau ended_run.ended_at = 14:24:00
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 14, 15, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            sess_a1 = await db.get(SessionRow, ids["sess_a1_id"])
            run_a1 = await db.get(SessionRun, ids["run_a1_id"])
            assert sess_a1 is not None and run_a1 is not None
            sess_a1.status = "completed"
            run_a1.ended_at = datetime(2026, 11, 2, 14, 24, 0, tzinfo=TZ_VN).astimezone(UTC)
            run_a1.run_state = "closed"
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "bug06-gap"),
                json={
                    "trigger_reason": "missed_start",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert resp.status_code == 201, resp.text
            moves = resp.json()["data"]["proposed_changes"]["sessions_to_move"]
            assert len(moves) == 1
            to_start = datetime.fromisoformat(moves[0]["to_start"].replace("Z", "+00:00"))
            min_expected = datetime(2026, 11, 2, 14, 39, 0, tzinfo=TZ_VN).astimezone(UTC)
            assert to_start >= min_expected

        # Ca B: Re-segment khi Step có 1 phiên 'skipped' và 1 phiên 'scheduled'
        async with sf() as db:
            ids2 = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            sess_a1_b = await db.get(SessionRow, ids2["sess_a1_id"])
            step_a_b = await db.get(StepRow, ids2["step_a_id"])
            assert sess_a1_b is not None and step_a_b is not None
            sess_a1_b.status = "skipped"
            step_a_b.remaining_base_seconds = 1800  # 1800 * 1.5 = 2700s (khác 1500/1.2*1.5 = 1875s)
            await db.commit()

            prop_data = await reschedule_service.create_settings_change_proposal(
                db,
                owner=ids2["user_id"],
                target_task_id=str(ids2["task_a_id"]),
                override_preferences={"buffer_multiplier": 1.5},
                now=anchor,
            )
            await db.commit()
            assert prop_data.feasibility.status == "feasible"
            total_reseg_work = sum(
                ad.after.work_seconds for ad in prop_data.proposed_changes.sessions_to_add
            )
            assert total_reseg_work == 2700


@pytest.mark.asyncio
async def test_p3_bug07_approve_proposal_skips_past_reminders_and_keeps_future_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-07 (Bug 5.1): approve_proposal bỏ qua mốc Reminder quá khứ (fire_at < approve_now)
    và giữ đủ mốc ở tương lai."""
    create_time = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    clock = {"now": create_time}
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or clock["now"])

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 13, 50, 0, tzinfo=TZ_VN),
                now_anchor=create_time,
            )
            # Đưa sess_a1 về quá khứ xa để phiên dời nằm đúng tại initial_cursor = 14:15
            sess_a1 = await db.get(SessionRow, ids["sess_a1_id"])
            run_a1 = await db.get(SessionRun, ids["run_a1_id"])
            assert sess_a1 is not None and run_a1 is not None
            sess_a1.scheduled_start = datetime(2026, 11, 2, 10, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            sess_a1.scheduled_end = datetime(2026, 11, 2, 10, 25, 0, tzinfo=TZ_VN).astimezone(UTC)
            run_a1.started_at = sess_a1.scheduled_start
            run_a1.ended_at = sess_a1.scheduled_end
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            p_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids["user_id"], "bug07-create"),
                json={
                    "trigger_reason": "missed_start",
                    "target_task_id": str(ids["task_a_id"]),
                    "allowed_task_ids": [str(ids["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert p_resp.status_code == 201, p_resp.text
            prop = p_resp.json()["data"]
            # Phiên mới dời về 14:15; user duyệt lúc 14:08 (cách 7 phút -> chỉ mốc 300s = 14:10 ở tương lai)
            clock["now"] = datetime(2026, 11, 2, 14, 8, 0, tzinfo=TZ_VN)
            a_resp = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers=_auth_headers(ids["user_id"], "bug07-approve"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert a_resp.status_code == 200, a_resp.text

        async with sf() as db:
            rems = list(
                (
                    await db.scalars(
                        select(Reminder).where(
                            Reminder.session_id == ids["sess_a2_id"],
                            Reminder.delivery_state == "scheduled",
                        )
                    )
                ).all()
            )
            approve_utc = datetime(2026, 11, 2, 14, 8, 0, tzinfo=TZ_VN).astimezone(UTC)
            assert len(rems) == 1
            assert rems[0].fire_at.astimezone(UTC) >= approve_utc


@pytest.mark.asyncio
async def test_p3_bug08_expand_next_horizon_keeps_partial_until_all_pending_steps_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-08 (RISK-UF2-03): Dự án dài hạn (unexpanded_scope=None, nhiều StepRow pending).
    Sau expand_next_horizon Pass 1 (vẫn còn pending steps) -> decomposition_state vẫn là 'partial';
    sau Pass cuối xếp hết pending steps -> mới chuyển thành 'complete'."""
    anchor = datetime(2026, 11, 2, 10, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        user_id = uuid4()
        task_id = uuid4()
        async with sf() as db:
            db.add(User(id=user_id, email=f"bug08_{user_id.hex[:8]}@example.com", display_name="B08"))
            await db.flush()
            db.add(
                UserPreferenceRow(
                    user_id=user_id,
                    work_interval_seconds=1500,
                    rest_interval_seconds=0,
                    micro_task_seconds=120,
                    reminder_offsets=[900, 600, 300],
                    reminder_style="gentle_sound",
                    buffer_multiplier=1.0,
                    effective_revision=1,
                )
            )
            # Mỗi ngày rảnh đúng 25 phút (19:00-19:25) -> 14 ngày chỉ chứa tối đa 14 * 1500 = 21000s
            db.add(
                AvailabilityRow(
                    user_id=user_id,
                    timezone="Asia/Ho_Chi_Minh",
                    weekly_rules=[
                        {"weekday": wd, "start_local": "19:00", "end_local": "19:25"}
                        for wd in range(1, 8)
                    ],
                    date_overrides=[],
                    source="manual",
                    confirmed_at=anchor - timedelta(days=1),
                    revision=1,
                )
            )
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    title="Dự án 60 ngày",
                    description="Học dài hạn",
                    scope="3 chương lớn",
                    time_constraint={
                        "kind": "deadline",
                        "deadline_at": (anchor + timedelta(days=60)).astimezone(UTC).isoformat(),
                    },
                    status="scheduled",
                    decomposition_state="partial",
                    unexpanded_scope=None,
                    revision=1,
                )
            )
            await db.flush()

            step0_id = uuid4()
            db.add(
                StepRow(
                    id=step0_id,
                    user_id=user_id,
                    task_id=task_id,
                    order_index=0,
                    title="Chương 1",
                    expected_output="Xong C1",
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.0,
                    estimate_source="ai",
                    status="pending",
                )
            )
            # Step 1 (15000s = 10 ngày) và Step 2 (15000s = 10 ngày) -> cửa sổ 14 ngày chỉ xếp được Step 1, còn Step 2 pending!
            for idx, title in [(1, "Chương 2"), (2, "Chương 3")]:
                db.add(
                    StepRow(
                        id=uuid4(),
                        user_id=user_id,
                        task_id=task_id,
                        order_index=idx,
                        title=title,
                        expected_output=f"Xong {title}",
                        estimated_base_seconds=15000,
                        remaining_base_seconds=15000,
                        buffer_multiplier=1.0,
                        estimate_source="ai",
                        status="pending",
                    )
                )
            await db.flush()
            s0_start = datetime(2026, 11, 2, 19, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=user_id,
                    step_id=step0_id,
                    scheduled_start=s0_start,
                    scheduled_end=s0_start + timedelta(seconds=1500),
                    micro_task_text="Mở C1",
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                    start_decision_due_at=s0_start + timedelta(minutes=15),
                )
            )
            db.add(
                ScheduleVersionRow(
                    user_id=user_id,
                    version_number=1,
                    base_version=0,
                    approved_at=anchor - timedelta(hours=1),
                    affected_task_ids=[str(task_id)],
                    min_inter_session_gap_seconds=900,
                    alarm_sync_state="synced",
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Pass 1: mở rộng Chương 2 (vẫn còn Chương 3 pending)
            p1 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(user_id, "bug08-pass1-create"),
                json={
                    "trigger_reason": "expand_next_horizon",
                    "target_task_id": str(task_id),
                    "allowed_task_ids": [str(task_id)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert p1.status_code == 201, p1.text
            prop1 = p1.json()["data"]
            assert prop1["feasibility"]["status"] == "feasible"
            a1 = await client.post(
                f"/api/v1/proposals/{prop1['proposal_id']}/approve",
                headers=_auth_headers(user_id, "bug08-pass1-approve"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": prop1["source_revisions"],
                },
            )
            assert a1.status_code == 200, a1.text

            async with sf() as db:
                t_after_p1 = await db.get(TaskRow, task_id)
                assert t_after_p1 is not None
                assert t_after_p1.decomposition_state == "partial"

            # Pass 2: mở rộng nốt Chương 3 -> không còn pending step -> chuyển 'complete'
            p2 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(user_id, "bug08-pass2-create"),
                json={
                    "trigger_reason": "expand_next_horizon",
                    "target_task_id": str(task_id),
                    "allowed_task_ids": [str(task_id)],
                    "expected_base_schedule_revision": 2,
                    "expected_source_revisions": {
                        "task": 2,
                        "schedule": 2,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert p2.status_code == 201, p2.text
            prop2 = p2.json()["data"]
            a2 = await client.post(
                f"/api/v1/proposals/{prop2['proposal_id']}/approve",
                headers=_auth_headers(user_id, "bug08-pass2-approve"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 2,
                    "expected_source_revisions": prop2["source_revisions"],
                },
            )
            assert a2.status_code == 200, a2.text

            async with sf() as db:
                t_after_p2 = await db.get(TaskRow, task_id)
                assert t_after_p2 is not None
                assert t_after_p2.decomposition_state == "complete"


@pytest.mark.asyncio
async def test_p3_bug09_normal_proposal_uses_task_availability_when_global_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF6-BUG-09: Khi AvailabilityRow toàn cục chưa confirm (hoặc không tồn tại) nhưng Task A
    đã có Task.availability riêng từ UF1/UF2, gọi POST /proposals với target_task_id=None
    phải tạo được proposal feasible thay vì trả oan AVAILABILITY_NOT_CONFIRMED."""
    anchor = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        user_id = uuid4()
        task_id = uuid4()
        step_id = uuid4()
        async with sf() as db:
            db.add(User(id=user_id, email=f"bug09_{user_id.hex[:8]}@example.com", display_name="B09"))
            await db.flush()
            db.add(
                UserPreferenceRow(
                    user_id=user_id,
                    work_interval_seconds=1500,
                    rest_interval_seconds=0,
                    micro_task_seconds=120,
                    reminder_offsets=[900, 600, 300],
                    reminder_style="gentle_sound",
                    buffer_multiplier=1.2,
                    effective_revision=1,
                )
            )
            # Cố tình KHÔNG seed AvailabilityRow toàn cục; chỉ đặt Task.availability riêng
            db.add(
                TaskRow(
                    id=task_id,
                    user_id=user_id,
                    title="Task Có Giờ Rảnh Riêng",
                    description="Tự học",
                    scope="2 bài",
                    time_constraint={
                        "kind": "deadline",
                        "deadline_at": (anchor + timedelta(days=3)).astimezone(UTC).isoformat(),
                    },
                    availability={
                        "timezone": "Asia/Ho_Chi_Minh",
                        "weekly_rules": [
                            {"weekday": wd, "start_local": "08:00", "end_local": "22:00"}
                            for wd in range(1, 8)
                        ],
                        "date_overrides": [],
                    },
                    status="scheduled",
                    revision=1,
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=step_id,
                    user_id=user_id,
                    task_id=task_id,
                    order_index=0,
                    title="Bước 1",
                    expected_output="Xong B1",
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="pending",
                )
            )
            await db.flush()
            s_start = datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=user_id,
                    step_id=step_id,
                    scheduled_start=s_start,
                    scheduled_end=s_start + timedelta(seconds=1500),
                    micro_task_text="Mở bài",
                    micro_task_seconds=120,
                    work_seconds=1500,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                    duration_seconds=1500,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                    start_decision_due_at=s_start + timedelta(minutes=15),
                )
            )
            db.add(
                ScheduleVersionRow(
                    user_id=user_id,
                    version_number=1,
                    base_version=0,
                    approved_at=anchor - timedelta(hours=1),
                    affected_task_ids=[str(task_id)],
                    min_inter_session_gap_seconds=900,
                    alarm_sync_state="synced",
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for idx, (reason, allowed) in enumerate(
                [
                    ("early_completion_pull_forward", []),
                    ("user_reschedule", [str(task_id)]),
                ]
            ):
                resp = await client.post(
                    "/api/v1/proposals",
                    headers=_auth_headers(user_id, f"bug09-case-{idx}"),
                    json={
                        "trigger_reason": reason,
                        "target_task_id": None,
                        "allowed_task_ids": allowed,
                        "expected_base_schedule_revision": 1,
                        "expected_source_revisions": {
                            "task": 1,
                            "schedule": 1,
                            "availability": 0,
                            "settings": 1,
                            "calendar": 0,
                        },
                    },
                )
                assert resp.status_code == 201, resp.text
                feas = resp.json()["data"]["feasibility"]
                assert feas["status"] == "feasible", feas
                assert feas["confirmable"] is True


@pytest.mark.asyncio
async def test_pr1_normal_proposal_approve_preserves_multitask_preview_and_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR1 (Codex P1): Normal proposal multi-task (không phải continuation) phải giữ nguyên
    to_start/to_end đã preview ở UF6 approve: Task A ưu tiên buổi tối (18:00), Task B
    deadline 16:30 được preview lúc 15:45; approve 200 và KHÔNG kéo Task B sang sau 18:00."""
    anchor = datetime(2026, 11, 2, 15, 30, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        user_id = uuid4()
        task_a_id = uuid4()
        task_b_id = uuid4()
        step_a_id = uuid4()
        step_b_id = uuid4()
        sess_a_id = uuid4()
        sess_b_id = uuid4()

        async with sf() as db:
            db.add(User(id=user_id, email=f"pr1_{user_id.hex[:8]}@example.com", display_name="PR1"))
            await db.flush()
            db.add(
                UserPreferenceRow(
                    user_id=user_id,
                    work_interval_seconds=1500,
                    rest_interval_seconds=0,
                    micro_task_seconds=120,
                    reminder_offsets=[900, 600, 300],
                    reminder_style="gentle_sound",
                    buffer_multiplier=1.0,
                    effective_revision=1,
                )
            )
            db.add(
                AvailabilityRow(
                    user_id=user_id,
                    timezone="Asia/Ho_Chi_Minh",
                    weekly_rules=[
                        {"weekday": wd, "start_local": "08:00", "end_local": "22:00"}
                        for wd in range(1, 8)
                    ],
                    date_overrides=[],
                    source="manual",
                    confirmed_at=anchor - timedelta(days=1),
                    revision=1,
                )
            )

            dl_a = (anchor + timedelta(days=3)).astimezone(UTC)
            dl_b = datetime(2026, 11, 2, 16, 30, 0, tzinfo=TZ_VN).astimezone(UTC)

            db.add(
                TaskRow(
                    id=task_a_id,
                    user_id=user_id,
                    title="Task A Evening",
                    description="Ưu tiên tối",
                    scope="1 bài",
                    extra_requirements=["Ưu tiên buổi tối"],
                    time_constraint={"kind": "deadline", "deadline_at": dl_a.isoformat()},
                    status="scheduled",
                    revision=1,
                )
            )
            db.add(
                TaskRow(
                    id=task_b_id,
                    user_id=user_id,
                    title="Task B Urgent",
                    description="Hạn 16:30",
                    scope="1 bài",
                    time_constraint={"kind": "deadline", "deadline_at": dl_b.isoformat()},
                    status="scheduled",
                    revision=1,
                )
            )
            await db.flush()

            db.add(
                StepRow(
                    id=step_a_id,
                    user_id=user_id,
                    task_id=task_a_id,
                    order_index=0,
                    title="Bước A",
                    expected_output="Xong A",
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.0,
                    estimate_source="ai",
                    status="pending",
                )
            )
            db.add(
                StepRow(
                    id=step_b_id,
                    user_id=user_id,
                    task_id=task_b_id,
                    order_index=0,
                    title="Bước B",
                    expected_output="Xong B",
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.0,
                    estimate_source="ai",
                    status="pending",
                )
            )
            await db.flush()

            # Session A đặt trước Session B để sessions_to_move xếp A (18:00) trước B (15:45)
            a_old_s = datetime(2026, 11, 2, 14, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            b_old_s = datetime(2026, 11, 2, 14, 40, 0, tzinfo=TZ_VN).astimezone(UTC)
            for sid, st_id, st_dt in [
                (sess_a_id, step_a_id, a_old_s),
                (sess_b_id, step_b_id, b_old_s),
            ]:
                db.add(
                    SessionRow(
                        id=sid,
                        user_id=user_id,
                        step_id=st_id,
                        scheduled_start=st_dt,
                        scheduled_end=st_dt + timedelta(seconds=1500),
                        micro_task_text="Bắt đầu",
                        micro_task_seconds=120,
                        work_seconds=1500,
                        rest_seconds=0,
                        segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                        duration_seconds=1500,
                        schedule_revision=1,
                        status="scheduled",
                        start_state="waiting",
                        start_decision_due_at=st_dt + timedelta(minutes=15),
                    )
                )
            db.add(
                ScheduleVersionRow(
                    user_id=user_id,
                    version_number=1,
                    base_version=0,
                    approved_at=anchor - timedelta(hours=2),
                    affected_task_ids=[str(task_a_id), str(task_b_id)],
                    min_inter_session_gap_seconds=900,
                    alarm_sync_state="synced",
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(user_id, "pr1-create"),
                json={
                    "trigger_reason": "missed_start",
                    "target_task_id": None,
                    "allowed_task_ids": [str(task_a_id), str(task_b_id)],
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 1,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            prop = create_resp.json()["data"]
            assert prop["feasibility"]["status"] == "feasible"
            moves_by_id = {m["session_id"]: m for m in prop["proposed_changes"]["sessions_to_move"]}
            assert str(sess_a_id) in moves_by_id and str(sess_b_id) in moves_by_id

            prev_a_start = datetime.fromisoformat(
                moves_by_id[str(sess_a_id)]["to_start"].replace("Z", "+00:00")
            ).astimezone(TZ_VN)
            prev_b_start = datetime.fromisoformat(
                moves_by_id[str(sess_b_id)]["to_start"].replace("Z", "+00:00")
            ).astimezone(TZ_VN)
            prev_b_end = datetime.fromisoformat(
                moves_by_id[str(sess_b_id)]["to_end"].replace("Z", "+00:00")
            ).astimezone(TZ_VN)
            assert prev_a_start == datetime(2026, 11, 2, 18, 0, 0, tzinfo=TZ_VN)
            assert prev_b_start == datetime(2026, 11, 2, 15, 45, 0, tzinfo=TZ_VN)
            assert prev_b_end <= datetime(2026, 11, 2, 16, 30, 0, tzinfo=TZ_VN)

            approve_resp = await client.post(
                f"/api/v1/proposals/{prop['proposal_id']}/approve",
                headers=_auth_headers(user_id, "pr1-approve"),
                json={
                    "expected_proposal_revision": 1,
                    "expected_base_schedule_revision": 1,
                    "expected_source_revisions": prop["source_revisions"],
                },
            )
            assert approve_resp.status_code == 200, approve_resp.text

        async with sf() as db:
            row_a = await db.get(SessionRow, sess_a_id)
            row_b = await db.get(SessionRow, sess_b_id)
            assert row_a is not None and row_b is not None
            assert row_a.scheduled_start.astimezone(UTC) == prev_a_start.astimezone(UTC)
            assert row_b.scheduled_start.astimezone(UTC) == prev_b_start.astimezone(UTC)
            assert row_b.scheduled_end.astimezone(UTC) <= datetime(
                2026, 11, 2, 16, 30, 0, tzinfo=TZ_VN
            ).astimezone(UTC)


@pytest.mark.asyncio
async def test_pr2_calendar_sync_worker_deletes_old_calendar_on_write_switch_and_honors_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR2 (Codex P1): Khi đổi write_calendar_id (old_cal -> new_cal) và approve UF6:
    - Với move/default: worker process_calendar_sync_jobs xóa event khỏi old_cal và tạo event trên new_cal.
    - Với existing_event_action='keep': sự kiện trên old_cal được giữ nguyên, event mới tạo trên new_cal.
    """
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    reset_fake_calendar_client()
    try:
        fake_client = get_calendar_client()
        assert isinstance(fake_client, FakeCalendarClient)

        async with _regression_db_env() as sf:
            # --- Case 1: existing_event_action = "move" ---
            fake_client.reset()
            async with sf() as db:
                ids1 = await _seed_continuation_world(
                    db,
                    task_b_start=None,
                    task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                    now_anchor=anchor,
                )
                conn1_id = uuid4()
                sess1_id = ids1["sess_a2_id"]
                assert sess1_id is not None
                old_ev1_id = f"session{sess1_id.hex}"
                creds1 = encrypt_credentials(
                    {
                        "access_token": "acc_pr2_move",
                        "refresh_token": "ref_pr2_move",
                        "expires_in": 3600,
                        "stored_at": datetime.now(UTC).isoformat(),
                        "user_id": str(ids1["user_id"]),
                        "connection_id": str(conn1_id),
                    }
                )
                db.add(
                    CalendarConnection(
                        id=conn1_id,
                        user_id=ids1["user_id"],
                        provider="google",
                        account_ref="pr2_move@gmail.com",
                        connection_state="connected",
                        read_calendar_ids=["primary"],
                        write_calendar_id="old_cal",
                        granted_scopes=["https://www.googleapis.com/auth/calendar"],
                        credential_ref=creds1,
                    )
                )
                await db.flush()
                db.add(
                    CalendarEventLink(
                        id=uuid4(),
                        user_id=ids1["user_id"],
                        session_id=sess1_id,
                        connection_id=conn1_id,
                        google_calendar_id="old_cal",
                        google_event_id=old_ev1_id,
                        google_etag='"etag-old-move"',
                        desired_schedule_revision=3,
                        synced_schedule_revision=3,
                        sync_state="synced",
                        is_current=True,
                    )
                )
                await db.commit()

            fake_client.mock_events["old_cal"] = [
                {
                    "id": old_ev1_id,
                    "summary": "ProPlan VA Focus Session",
                    "etag": '"etag-old-move"',
                    "status": "confirmed",
                }
            ]

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                cfg1 = await client.post(
                    f"/api/v1/calendar/connections/{conn1_id}/configuration-previews",
                    headers=_auth_headers(ids1["user_id"]),
                    json={
                        "read_calendar_ids": ["primary"],
                        "write_calendar_id": "new_cal",
                        "existing_event_action": "move",
                    },
                )
                assert cfg1.status_code == 200, cfg1.text
                cfg1_confirm = await client.post(
                    f"/api/v1/calendar/configuration-previews/{cfg1.json()['data']['preview_id']}/confirm",
                    headers=_auth_headers(ids1["user_id"]),
                    json={"expected_preview_revision": 1},
                )
                assert cfg1_confirm.status_code == 200, cfg1_confirm.text

                p1 = await client.post(
                    "/api/v1/proposals",
                    headers=_auth_headers(ids1["user_id"], "pr2-move-p1"),
                    json={
                        "trigger_reason": "user_reschedule",
                        "target_task_id": str(ids1["task_a_id"]),
                        "allowed_task_ids": [str(ids1["task_a_id"])],
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": {
                            "task": 1,
                            "schedule": 3,
                            "availability": 1,
                            "settings": 1,
                            "calendar": 2,
                        },
                    },
                )
                assert p1.status_code == 201, p1.text
                prop1 = p1.json()["data"]
                a1 = await client.post(
                    f"/api/v1/proposals/{prop1['proposal_id']}/approve",
                    headers=_auth_headers(ids1["user_id"], "pr2-move-a1"),
                    json={
                        "expected_proposal_revision": 1,
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": prop1["source_revisions"],
                    },
                )
                assert a1.status_code == 200, a1.text

            async with sf() as db:
                processed1 = await process_calendar_sync_jobs(db, user_id=ids1["user_id"])
                assert processed1 == 2
                jobs1 = list(
                    (
                        await db.scalars(
                            select(CalendarSyncJob).where(
                                CalendarSyncJob.user_id == ids1["user_id"]
                            )
                        )
                    ).all()
                )
                assert {j.operation for j in jobs1} == {"delete", "create"}
                assert all(j.job_state == "completed" for j in jobs1)

            assert not any(
                e.get("id") == old_ev1_id for e in fake_client.mock_events.get("old_cal", [])
            )
            assert len(fake_client.mock_events.get("new_cal", [])) == 1

            # --- Case 2: existing_event_action = "keep" ---
            fake_client.reset()
            async with sf() as db:
                ids2 = await _seed_continuation_world(
                    db,
                    task_b_start=None,
                    task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                    now_anchor=anchor,
                )
                conn2_id = uuid4()
                sess2_id = ids2["sess_a2_id"]
                assert sess2_id is not None
                old_ev2_id = f"session{sess2_id.hex}"
                old_lk2_id = uuid4()
                creds2 = encrypt_credentials(
                    {
                        "access_token": "acc_pr2_keep",
                        "refresh_token": "ref_pr2_keep",
                        "expires_in": 3600,
                        "stored_at": datetime.now(UTC).isoformat(),
                        "user_id": str(ids2["user_id"]),
                        "connection_id": str(conn2_id),
                    }
                )
                db.add(
                    CalendarConnection(
                        id=conn2_id,
                        user_id=ids2["user_id"],
                        provider="google",
                        account_ref="pr2_keep@gmail.com",
                        connection_state="connected",
                        read_calendar_ids=["primary"],
                        write_calendar_id="old_cal",
                        granted_scopes=["https://www.googleapis.com/auth/calendar"],
                        credential_ref=creds2,
                    )
                )
                await db.flush()
                db.add(
                    CalendarEventLink(
                        id=old_lk2_id,
                        user_id=ids2["user_id"],
                        session_id=sess2_id,
                        connection_id=conn2_id,
                        google_calendar_id="old_cal",
                        google_event_id=old_ev2_id,
                        google_etag='"etag-old-keep"',
                        desired_schedule_revision=3,
                        synced_schedule_revision=3,
                        sync_state="synced",
                        is_current=True,
                    )
                )
                await db.commit()

            fake_client.mock_events["old_cal"] = [
                {
                    "id": old_ev2_id,
                    "summary": "ProPlan VA Focus Session",
                    "etag": '"etag-old-keep"',
                    "status": "confirmed",
                }
            ]

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                cfg2 = await client.post(
                    f"/api/v1/calendar/connections/{conn2_id}/configuration-previews",
                    headers=_auth_headers(ids2["user_id"]),
                    json={
                        "read_calendar_ids": ["primary"],
                        "write_calendar_id": "new_cal",
                        "existing_event_action": "keep",
                    },
                )
                assert cfg2.status_code == 200, cfg2.text
                cfg2_confirm = await client.post(
                    f"/api/v1/calendar/configuration-previews/{cfg2.json()['data']['preview_id']}/confirm",
                    headers=_auth_headers(ids2["user_id"]),
                    json={"expected_preview_revision": 1},
                )
                assert cfg2_confirm.status_code == 200, cfg2_confirm.text

                p2 = await client.post(
                    "/api/v1/proposals",
                    headers=_auth_headers(ids2["user_id"], "pr2-keep-p2"),
                    json={
                        "trigger_reason": "user_reschedule",
                        "target_task_id": str(ids2["task_a_id"]),
                        "allowed_task_ids": [str(ids2["task_a_id"])],
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": {
                            "task": 1,
                            "schedule": 3,
                            "availability": 1,
                            "settings": 1,
                            "calendar": 2,
                        },
                    },
                )
                assert p2.status_code == 201, p2.text
                prop2 = p2.json()["data"]
                a2 = await client.post(
                    f"/api/v1/proposals/{prop2['proposal_id']}/approve",
                    headers=_auth_headers(ids2["user_id"], "pr2-keep-a2"),
                    json={
                        "expected_proposal_revision": 1,
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": prop2["source_revisions"],
                    },
                )
                assert a2.status_code == 200, a2.text

            async with sf() as db:
                processed2 = await process_calendar_sync_jobs(db, user_id=ids2["user_id"])
                assert processed2 == 1
                jobs2 = list(
                    (
                        await db.scalars(
                            select(CalendarSyncJob).where(
                                CalendarSyncJob.user_id == ids2["user_id"]
                            )
                        )
                    ).all()
                )
                assert [j.operation for j in jobs2] == ["create"]
                assert jobs2[0].job_state == "completed"
                old_lk2 = await db.get(CalendarEventLink, old_lk2_id)
                assert old_lk2 is not None
                assert old_lk2.is_current is False
                assert old_lk2.sync_state == "synced"

            assert any(
                e.get("id") == old_ev2_id for e in fake_client.mock_events.get("old_cal", [])
            )
            assert len(fake_client.mock_events.get("new_cal", [])) == 1
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_pr3_min_contiguous_slot_checked_after_cursor_trim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR3 (Codex P2): Slot 09:00-10:00 (60m >= 45m min), cursor 09:40 (còn 20m < 45m).
    Phiên 15m phải bỏ qua 09:40-10:00 và rơi vào khe >= 45m kế tiếp (11:30-12:15) ở cả
    2 nhánh: move-slot (_find_free_slot_with_constraints) và re-segment (_plan_step_sessions_with_constraints).
    """
    anchor = datetime(2026, 11, 2, 9, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    task_avail = {
        "timezone": "Asia/Ho_Chi_Minh",
        "weekly_rules": [],
        "date_overrides": [
            {
                "date": "2026-11-02",
                "intervals": [
                    {"start_local": "09:00", "end_local": "10:00"},
                    {"start_local": "10:30", "end_local": "11:05"},
                    {"start_local": "11:30", "end_local": "12:15"},
                ],
            }
        ],
    }

    async with _regression_db_env() as sf:
        # --- Path 1: move-slot (_find_free_slot_with_constraints) ---
        async with sf() as db:
            ids1 = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 8, 30, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            pref1 = await db.scalar(
                select(UserPreferenceRow).where(UserPreferenceRow.user_id == ids1["user_id"])
            )
            assert pref1 is not None
            pref1.work_interval_seconds = 900
            pref1.rest_interval_seconds = 0
            pref1.buffer_multiplier = 1.0

            t1 = await db.get(TaskRow, ids1["task_a_id"])
            assert t1 is not None
            t1.extra_requirements = ["Chỉ xếp khi có ít nhất 45 phút trống liền"]
            t1.availability = task_avail

            s_a1 = await db.get(SessionRow, ids1["sess_a1_id"])
            r_a1 = await db.get(SessionRun, ids1["run_a1_id"])
            s_a2 = await db.get(SessionRow, ids1["sess_a2_id"])
            assert s_a1 is not None and r_a1 is not None and s_a2 is not None
            s_a1.scheduled_start = datetime(2026, 11, 1, 9, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            s_a1.scheduled_end = datetime(2026, 11, 1, 9, 25, 0, tzinfo=TZ_VN).astimezone(UTC)
            s_a1.status = "completed"
            r_a1.started_at = s_a1.scheduled_start
            r_a1.ended_at = s_a1.scheduled_end
            r_a1.run_state = "closed"

            s_a2.work_seconds = 900
            s_a2.rest_seconds = 0
            s_a2.duration_seconds = 900
            s_a2.segments = [{"order_index": 0, "kind": "work", "duration_seconds": 900}]
            s_a2.scheduled_end = s_a2.scheduled_start + timedelta(seconds=900)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            move_resp = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids1["user_id"], "pr3-move-slot"),
                json={
                    "trigger_reason": "missed_start",
                    "target_task_id": str(ids1["task_a_id"]),
                    "allowed_task_ids": [str(ids1["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 2,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert move_resp.status_code == 201, move_resp.text
            moves = move_resp.json()["data"]["proposed_changes"]["sessions_to_move"]
            assert len(moves) == 1
            moved_start_vn = datetime.fromisoformat(
                moves[0]["to_start"].replace("Z", "+00:00")
            ).astimezone(TZ_VN)
            assert moved_start_vn == datetime(2026, 11, 2, 11, 30, 0, tzinfo=TZ_VN)

        # --- Path 2: re-segment (_plan_step_sessions_with_constraints) ---
        async with sf() as db:
            ids2 = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 8, 30, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )
            pref2 = await db.scalar(
                select(UserPreferenceRow).where(UserPreferenceRow.user_id == ids2["user_id"])
            )
            assert pref2 is not None
            pref2.work_interval_seconds = 900
            pref2.rest_interval_seconds = 300
            pref2.buffer_multiplier = 1.0

            t2 = await db.get(TaskRow, ids2["task_a_id"])
            step2 = await db.get(StepRow, ids2["step_a_id"])
            s2_1 = await db.get(SessionRow, ids2["sess_a1_id"])
            r2_1 = await db.get(SessionRun, ids2["run_a1_id"])
            s2_2 = await db.get(SessionRow, ids2["sess_a2_id"])
            assert t2 is not None and step2 is not None and s2_1 is not None and r2_1 is not None and s2_2 is not None

            t2.extra_requirements = ["Chỉ xếp khi có ít nhất 45 phút trống liền"]
            t2.availability = task_avail
            step2.remaining_base_seconds = 900
            step2.buffer_multiplier = 1.0

            s2_1.scheduled_start = datetime(2026, 11, 1, 9, 0, 0, tzinfo=TZ_VN).astimezone(UTC)
            s2_1.scheduled_end = datetime(2026, 11, 1, 9, 25, 0, tzinfo=TZ_VN).astimezone(UTC)
            s2_1.status = "completed"
            r2_1.started_at = s2_1.scheduled_start
            r2_1.ended_at = s2_1.scheduled_end
            r2_1.run_state = "closed"

            s2_2.work_seconds = 900
            s2_2.rest_seconds = 300
            s2_2.duration_seconds = 1200
            s2_2.segments = [
                {"order_index": 0, "kind": "work", "duration_seconds": 900},
                {"order_index": 1, "kind": "rest", "duration_seconds": 300},
            ]
            s2_2.scheduled_end = s2_2.scheduled_start + timedelta(seconds=1200)
            await db.commit()

            reseg_prop = await reschedule_service.create_settings_change_proposal(
                db,
                owner=ids2["user_id"],
                target_task_id=str(ids2["task_a_id"]),
                override_preferences={"rest_interval_seconds": 0},
                now=anchor,
            )
            await db.commit()
            assert reseg_prop.feasibility.status == "feasible"
            adds = reseg_prop.proposed_changes.sessions_to_add
            assert len(adds) == 1
            assert adds[0].after.duration_seconds == 900
            assert adds[0].after.start.astimezone(TZ_VN) == datetime(
                2026, 11, 2, 11, 30, 0, tzinfo=TZ_VN
            )


@pytest.mark.asyncio
async def test_pr4_rg05_exact_codes_and_concurrent_preview_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR4 (RG05 + Concurrency):
    1. configuration-previews/{id}/confirm trả đúng mã lỗi 'REVISION_CONFLICT' và 'PREVIEW_EXPIRED'.
    2. asyncio.gather đồng thời:
       - 2x confirm cùng 1 config-preview (provision_new) -> [200, 200], provision_proactive_calendar gọi đúng 1 lần.
       - 2x confirm 2 config-preview khác nhau -> {200, 409} với 'REVISION_CONFLICT'.
       - Đồng thời config-preview + event-change-preview -> không 500, event confirm cũ trả 409 'REVISION_CONFLICT'.
    """
    reset_fake_calendar_client()
    try:
        fake_client = get_calendar_client()
        assert isinstance(fake_client, FakeCalendarClient)

        async with _regression_db_env() as sf:
            user_id = uuid4()
            conn_id = uuid4()
            async with sf() as db:
                db.add(
                    User(
                        id=user_id,
                        email=f"pr4_{user_id.hex[:8]}@example.com",
                        display_name="PR4",
                    )
                )
                await db.flush()
                creds = encrypt_credentials(
                    {
                        "access_token": "acc_pr4",
                        "refresh_token": "ref_pr4",
                        "expires_in": 3600,
                        "stored_at": datetime.now(UTC).isoformat(),
                        "user_id": str(user_id),
                        "connection_id": str(conn_id),
                    }
                )
                db.add(
                    CalendarConnection(
                        id=conn_id,
                        user_id=user_id,
                        provider="google",
                        account_ref="pr4@gmail.com",
                        connection_state="connected",
                        read_calendar_ids=["primary"],
                        write_calendar_id="primary",
                        granted_scopes=["https://www.googleapis.com/auth/calendar"],
                        credential_ref=creds,
                    )
                )
                await db.commit()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = _auth_headers(user_id)

                # --- 1. Exact error codes on configuration-previews/{id}/confirm ---
                cfg_rev = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={"read_calendar_ids": ["primary"], "write_calendar_id": "primary"},
                )
                assert cfg_rev.status_code == 200
                cfg_rev_pid = cfg_rev.json()["data"]["preview_id"]

                bad_rev = await client.post(
                    f"/api/v1/calendar/configuration-previews/{cfg_rev_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 99},
                )
                assert bad_rev.status_code == 409
                assert bad_rev.json()["error"]["code"] == "REVISION_CONFLICT"

                cfg_exp = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={"read_calendar_ids": ["primary"], "write_calendar_id": "primary"},
                )
                assert cfg_exp.status_code == 200
                cfg_exp_pid = cfg_exp.json()["data"]["preview_id"]

                async with sf() as db:
                    await db.execute(
                        update(CalendarPreview)
                        .where(CalendarPreview.id == UUID(cfg_exp_pid))
                        .values(expires_at=datetime.now(UTC) - timedelta(minutes=5))
                    )
                    await db.commit()

                exp_resp = await client.post(
                    f"/api/v1/calendar/configuration-previews/{cfg_exp_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert exp_resp.status_code == 409
                assert exp_resp.json()["error"]["code"] == "PREVIEW_EXPIRED"

                # --- 2a. 2x same config-preview confirm w/ provision_new -> [200, 200], provision called once ---
                fake_client.reset()
                orig_provision = fake_client.provision_proactive_calendar
                provision_calls = 0

                async def _spied_provision(
                    access_token: str,
                    summary: str = "ProPlan VA Focus",
                    time_zone: str = "Asia/Ho_Chi_Minh",
                ) -> dict[str, Any]:
                    nonlocal provision_calls
                    provision_calls += 1
                    await asyncio.sleep(0.02)
                    return await orig_provision(
                        access_token=access_token, summary=summary, time_zone=time_zone
                    )

                monkeypatch.setattr(fake_client, "provision_proactive_calendar", _spied_provision)

                cfg_prov = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary"],
                        "write_calendar_id": "provision_new",
                        "existing_event_action": "provision_new",
                    },
                )
                assert cfg_prov.status_code == 200
                cfg_prov_pid = cfg_prov.json()["data"]["preview_id"]

                p_res1, p_res2 = await asyncio.gather(
                    client.post(
                        f"/api/v1/calendar/configuration-previews/{cfg_prov_pid}/confirm",
                        headers=headers,
                        json={"expected_preview_revision": 1},
                    ),
                    client.post(
                        f"/api/v1/calendar/configuration-previews/{cfg_prov_pid}/confirm",
                        headers=headers,
                        json={"expected_preview_revision": 1},
                    ),
                )
                assert [p_res1.status_code, p_res2.status_code] == [200, 200]
                assert provision_calls == 1

                # --- 2b. 2x distinct config-previews -> {200, 409} REVISION_CONFLICT ---
                cfg_d1 = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary"],
                        "write_calendar_id": "work_calendar_123@group.calendar.google.com",
                    },
                )
                cfg_d2 = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary"],
                        "write_calendar_id": "mock_user@gmail.com",
                    },
                )
                assert cfg_d1.status_code == 200 and cfg_d2.status_code == 200
                d1_pid = cfg_d1.json()["data"]["preview_id"]
                d2_pid = cfg_d2.json()["data"]["preview_id"]

                d_res1, d_res2 = await asyncio.gather(
                    client.post(
                        f"/api/v1/calendar/configuration-previews/{d1_pid}/confirm",
                        headers=headers,
                        json={"expected_preview_revision": 1},
                    ),
                    client.post(
                        f"/api/v1/calendar/configuration-previews/{d2_pid}/confirm",
                        headers=headers,
                        json={"expected_preview_revision": 1},
                    ),
                )
                assert {d_res1.status_code, d_res2.status_code} == {200, 409}
                conflict_resp = d_res1 if d_res1.status_code == 409 else d_res2
                assert conflict_resp.json()["error"]["code"] == "REVISION_CONFLICT"

                # --- 2c. Concurrent config-preview + event-change-preview -> no 500, stale event confirm 409 REVISION_CONFLICT ---
                fake_client.mock_calendars = [
                    c
                    for c in fake_client.mock_calendars
                    if c.get("summary") != "ProPlan VA Focus"
                ]
                orig_list = fake_client.list_calendar_list
                cfg_entered_lock = asyncio.Event()

                async def _delayed_list(access_token: str) -> list[dict[str, Any]]:
                    cfg_entered_lock.set()
                    await asyncio.sleep(0.02)
                    return await orig_list(access_token)

                monkeypatch.setattr(fake_client, "list_calendar_list", _delayed_list)

                cfg_race = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary"],
                        "write_calendar_id": "provision_new",
                        "existing_event_action": "provision_new",
                    },
                )
                ev_race = await client.post(
                    "/api/v1/calendar/event-change-previews",
                    headers=headers,
                    json={
                        "operation": "create",
                        "connection_id": str(conn_id),
                        "google_calendar_id": "primary",
                        "payload": {"summary": "PR4 Race Event"},
                    },
                )
                assert cfg_race.status_code == 200 and ev_race.status_code == 200
                cfg_race_pid = cfg_race.json()["data"]["preview_id"]
                ev_race_pid = ev_race.json()["data"]["preview_id"]

                cfg_task = asyncio.create_task(
                    client.post(
                        f"/api/v1/calendar/configuration-previews/{cfg_race_pid}/confirm",
                        headers=headers,
                        json={"expected_preview_revision": 1},
                    )
                )
                await cfg_entered_lock.wait()
                ev_task = asyncio.create_task(
                    client.post(
                        f"/api/v1/calendar/event-change-previews/{ev_race_pid}/confirm",
                        headers=headers,
                        json={"expected_preview_revision": 1},
                    )
                )
                cfg_race_resp, ev_race_resp = await asyncio.gather(cfg_task, ev_task)
                assert 500 not in (cfg_race_resp.status_code, ev_race_resp.status_code)
                assert cfg_race_resp.status_code == 200, cfg_race_resp.text
                assert ev_race_resp.status_code == 409, ev_race_resp.text
                assert ev_race_resp.json()["error"]["code"] == "REVISION_CONFLICT"
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_p4_risk3_create_and_rebuild_release_db_tx_during_ai_and_replay_zero_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4 / Risk 3: create_proposal và rebuild_proposal phải giải phóng transaction/lock DB
    trước khi gọi AI empathy (_generate_uf6_explanation_and_tier), cho phép session khác commit
    song song khi AI đang chạy, và replay theo Idempotency-Key gọi AI đúng 0 lần thêm.
    """
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        async with sf() as db:
            ids = await _seed_continuation_world(
                db,
                task_b_start=None,
                task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                now_anchor=anchor,
            )

        active_sessions: list[AsyncSession] = []
        orig_get_db = app.dependency_overrides[get_db]

        async def _tracking_get_db() -> AsyncGenerator[AsyncSession, None]:
            async for sess in orig_get_db():
                active_sessions.append(sess)
                yield sess

        app.dependency_overrides[get_db] = _tracking_get_db
        try:
            ai_calls = 0
            ai_entered = asyncio.Event()
            ai_release = asyncio.Event()
            in_tx_during_ai: list[bool] = []

            async def _paused_ai(
                trigger_reason: str,
                feasibility: Any,
            ) -> tuple[str, str]:
                nonlocal ai_calls
                ai_calls += 1
                if active_sessions:
                    in_tx_during_ai.append(active_sessions[-1].in_transaction())
                ai_entered.set()
                await ai_release.wait()
                return (f"Empathy #{ai_calls} ({trigger_reason})", "gemini_primary")

            monkeypatch.setattr(
                reschedule_service, "_generate_uf6_explanation_and_tier", _paused_ai
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # --- 1. create_proposal releases DB tx during AI ---
                create_task = asyncio.create_task(
                    client.post(
                        "/api/v1/proposals",
                        headers=_auth_headers(ids["user_id"], "risk3-create-1"),
                        json={
                            "trigger_reason": "user_reschedule",
                            "target_task_id": str(ids["task_a_id"]),
                            "allowed_task_ids": [str(ids["task_a_id"])],
                            "expected_base_schedule_revision": 3,
                            "expected_source_revisions": {
                                "task": 1,
                                "schedule": 3,
                                "availability": 1,
                                "settings": 1,
                                "calendar": 0,
                            },
                        },
                    )
                )
                await ai_entered.wait()
                assert in_tx_during_ai[-1] is False
                assert active_sessions and active_sessions[-1].in_transaction() is False

                # Một session khác commit song song trong khi AI đang pause -> không nghẽn lock
                other_uid_1 = uuid4()
                async with sf() as concurrent_db:
                    concurrent_db.add(
                        User(
                            id=other_uid_1,
                            email=f"conc1_{other_uid_1.hex[:8]}@example.com",
                            display_name="Concurrent 1",
                        )
                    )
                    await concurrent_db.commit()

                ai_release.set()
                create_resp = await create_task
                assert create_resp.status_code == 201, create_resp.text
                prop1 = create_resp.json()["data"]
                assert prop1["explanation"] == "Empathy #1 (user_reschedule)"
                assert prop1["generation_tier"] == "gemini_primary"
                assert ai_calls == 1

                # Replay create_proposal với cùng Idempotency-Key -> 0 lần gọi AI thêm
                replay_create = await client.post(
                    "/api/v1/proposals",
                    headers=_auth_headers(ids["user_id"], "risk3-create-1"),
                    json={
                        "trigger_reason": "user_reschedule",
                        "target_task_id": str(ids["task_a_id"]),
                        "allowed_task_ids": [str(ids["task_a_id"])],
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": {
                            "task": 1,
                            "schedule": 3,
                            "availability": 1,
                            "settings": 1,
                            "calendar": 0,
                        },
                    },
                )
                assert replay_create.status_code == 201, replay_create.text
                assert replay_create.json()["data"]["proposal_id"] == prop1["proposal_id"]
                assert ai_calls == 1

                # --- 2. rebuild_proposal releases DB tx during AI ---
                ai_entered.clear()
                ai_release.clear()
                rebuild_task = asyncio.create_task(
                    client.post(
                        f"/api/v1/proposals/{prop1['proposal_id']}/rebuild",
                        headers=_auth_headers(ids["user_id"], "risk3-rebuild-1"),
                        json={
                            "expected_proposal_revision": 1,
                            "request_text": "dời sang buổi chiều giúp mình",
                        },
                    )
                )
                await ai_entered.wait()
                assert in_tx_during_ai[-1] is False
                assert active_sessions[-1].in_transaction() is False

                other_uid_2 = uuid4()
                async with sf() as concurrent_db:
                    concurrent_db.add(
                        User(
                            id=other_uid_2,
                            email=f"conc2_{other_uid_2.hex[:8]}@example.com",
                            display_name="Concurrent 2",
                        )
                    )
                    await concurrent_db.commit()

                ai_release.set()
                reb_resp = await rebuild_task
                assert reb_resp.status_code == 201, reb_resp.text
                reb_data = reb_resp.json()["data"]
                assert reb_data["proposal_revision"] == 2
                assert reb_data["explanation"] == "Empathy #2 (user_reschedule)"
                assert ai_calls == 2

                # Replay rebuild_proposal với cùng Idempotency-Key -> 0 lần gọi AI thêm
                replay_reb = await client.post(
                    f"/api/v1/proposals/{prop1['proposal_id']}/rebuild",
                    headers=_auth_headers(ids["user_id"], "risk3-rebuild-1"),
                    json={
                        "expected_proposal_revision": 1,
                        "request_text": "dời sang buổi chiều giúp mình",
                    },
                )
                assert replay_reb.status_code == 201, replay_reb.text
                assert replay_reb.json()["data"]["proposal_revision"] == 2
                assert ai_calls == 2
        finally:
            app.dependency_overrides[get_db] = orig_get_db


@pytest.mark.asyncio
async def test_p4_risk3_mid_ai_revision_mutation_returns_409_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4 / Risk 3: Khi DB bị session khác sửa TaskRow.revision hoặc proposal_revision
    ngay trong lúc đang chờ AI empathy ở Phase A, Phase B bắt buộc trả 409 REVISION_CONFLICT
    và không tạo/ghi đè proposal bằng kế hoạch cũ.
    """
    anchor = datetime(2026, 11, 2, 14, 25, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or anchor)

    async with _regression_db_env() as sf:
        ai_entered = asyncio.Event()
        ai_release = asyncio.Event()
        pause_on_ai = {"enabled": True}

        async def _controllable_ai(
            trigger_reason: str,
            feasibility: Any,
        ) -> tuple[str, str]:
            if pause_on_ai["enabled"]:
                ai_entered.set()
                await ai_release.wait()
            return ("Mid-AI stale explanation", "gemini_primary")

        monkeypatch.setattr(
            reschedule_service, "_generate_uf6_explanation_and_tier", _controllable_ai
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # --- Case 1: create_proposal có TaskRow.revision bị đổi giữa pha AI ---
            async with sf() as db:
                ids1 = await _seed_continuation_world(
                    db,
                    task_b_start=None,
                    task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                    now_anchor=anchor,
                )

            ai_entered.clear()
            ai_release.clear()
            pause_on_ai["enabled"] = True
            create_task = asyncio.create_task(
                client.post(
                    "/api/v1/proposals",
                    headers=_auth_headers(ids1["user_id"], "risk3-mid-create"),
                    json={
                        "trigger_reason": "user_reschedule",
                        "target_task_id": str(ids1["task_a_id"]),
                        "allowed_task_ids": [str(ids1["task_a_id"])],
                        "expected_base_schedule_revision": 3,
                        "expected_source_revisions": {
                            "task": 1,
                            "schedule": 3,
                            "availability": 1,
                            "settings": 1,
                            "calendar": 0,
                        },
                    },
                )
            )
            await ai_entered.wait()
            async with sf() as db2:
                t1 = await db2.get(TaskRow, ids1["task_a_id"])
                assert t1 is not None
                t1.title = "Task A Mutated Mid-AI"
                await db2.commit()
                assert t1.revision == 2
            ai_release.set()

            c_resp = await create_task
            assert c_resp.status_code == 409, c_resp.text
            assert c_resp.json()["error"]["code"] == "REVISION_CONFLICT"
            async with sf() as db:
                created_props = list(
                    (
                        await db.scalars(
                            select(RescheduleProposal).where(
                                RescheduleProposal.user_id == ids1["user_id"]
                            )
                        )
                    ).all()
                )
                assert len(created_props) == 0

            # --- Case 2: rebuild_proposal có TaskRow.revision bị đổi giữa pha AI ---
            async with sf() as db:
                ids2 = await _seed_continuation_world(
                    db,
                    task_b_start=None,
                    task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                    now_anchor=anchor,
                )
            pause_on_ai["enabled"] = False
            setup_p = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids2["user_id"], "risk3-mid-reb-setup-1"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids2["task_a_id"]),
                    "allowed_task_ids": [str(ids2["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert setup_p.status_code == 201, setup_p.text
            pid2 = setup_p.json()["data"]["proposal_id"]

            ai_entered.clear()
            ai_release.clear()
            pause_on_ai["enabled"] = True
            reb_src_task = asyncio.create_task(
                client.post(
                    f"/api/v1/proposals/{pid2}/rebuild",
                    headers=_auth_headers(ids2["user_id"], "risk3-mid-reb-src"),
                    json={"expected_proposal_revision": 1, "request_text": "dời lại"},
                )
            )
            await ai_entered.wait()
            async with sf() as db2:
                t2 = await db2.get(TaskRow, ids2["task_a_id"])
                assert t2 is not None
                t2.title = "Task A Mutated During Rebuild"
                await db2.commit()
                assert t2.revision == 2
            ai_release.set()

            reb_src_resp = await reb_src_task
            assert reb_src_resp.status_code == 409, reb_src_resp.text
            assert reb_src_resp.json()["error"]["code"] == "REVISION_CONFLICT"
            async with sf() as db:
                p2_row = await db.get(RescheduleProposal, UUID(pid2))
                assert p2_row is not None
                assert p2_row.proposal_revision == 1
                assert p2_row.source_revisions["task"] == 1

            # --- Case 3: rebuild_proposal có proposal_revision bị đổi giữa pha AI ---
            async with sf() as db:
                ids3 = await _seed_continuation_world(
                    db,
                    task_b_start=None,
                    task_a_session2_start=datetime(2026, 11, 2, 16, 0, 0, tzinfo=TZ_VN),
                    now_anchor=anchor,
                )
            pause_on_ai["enabled"] = False
            setup_p3 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(ids3["user_id"], "risk3-mid-reb-setup-2"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(ids3["task_a_id"]),
                    "allowed_task_ids": [str(ids3["task_a_id"])],
                    "expected_base_schedule_revision": 3,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": 3,
                        "availability": 1,
                        "settings": 1,
                        "calendar": 0,
                    },
                },
            )
            assert setup_p3.status_code == 201, setup_p3.text
            pid3 = setup_p3.json()["data"]["proposal_id"]

            ai_entered.clear()
            ai_release.clear()
            pause_on_ai["enabled"] = True
            reb_prop_task = asyncio.create_task(
                client.post(
                    f"/api/v1/proposals/{pid3}/rebuild",
                    headers=_auth_headers(ids3["user_id"], "risk3-mid-reb-prop"),
                    json={"expected_proposal_revision": 1, "request_text": "dời lại lần 2"},
                )
            )
            await ai_entered.wait()
            async with sf() as db2:
                p3_mut = await db2.get(RescheduleProposal, UUID(pid3))
                assert p3_mut is not None
                p3_mut.proposal_revision = 2
                p3_mut.explanation = "Concurrent external update"
                await db2.commit()
            ai_release.set()

            reb_prop_resp = await reb_prop_task
            assert reb_prop_resp.status_code == 409, reb_prop_resp.text
            assert reb_prop_resp.json()["error"]["code"] == "REVISION_CONFLICT"
            async with sf() as db:
                p3_row = await db.get(RescheduleProposal, UUID(pid3))
                assert p3_row is not None
                assert p3_row.proposal_revision == 2
                assert p3_row.explanation == "Concurrent external update"


async def _seed_calendar_sync_world(
    db: AsyncSession,
    *,
    google_event_id: str | None = None,
    google_etag: str | None = None,
    desired_schedule_revision: int = 1,
    sync_state: str = "pending",
) -> dict[str, Any]:
    """Tạo User + CalendarConnection + Task/Step/Session + CalendarEventLink phục vụ regression test worker."""
    now_utc = datetime.now(UTC).replace(microsecond=0)
    user_id = uuid4()
    conn_id = uuid4()
    task_id = uuid4()
    step_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    db.add(
        User(
            id=user_id,
            email=f"sync_{user_id.hex[:10]}@example.com",
            display_name="Sync Worker Tester",
        )
    )
    await db.flush()

    creds = encrypt_credentials(
        {
            "access_token": f"acc_{conn_id.hex[:8]}",
            "refresh_token": f"ref_{conn_id.hex[:8]}",
            "expires_in": 3600,
            "stored_at": now_utc.isoformat(),
            "user_id": str(user_id),
            "connection_id": str(conn_id),
        }
    )
    db.add(
        CalendarConnection(
            id=conn_id,
            user_id=user_id,
            provider="google",
            account_ref=f"sync_{conn_id.hex[:8]}@gmail.com",
            connection_state="connected",
            read_calendar_ids=["primary"],
            write_calendar_id="primary",
            granted_scopes=["https://www.googleapis.com/auth/calendar"],
            credential_ref=creds,
        )
    )
    db.add(
        TaskRow(
            id=task_id,
            user_id=user_id,
            title="Calendar Sync Regression Task",
            description="Kiểm tra đồng bộ lịch",
            scope="1 phiên",
            time_constraint={
                "kind": "deadline",
                "deadline_at": (now_utc + timedelta(days=3)).isoformat(),
            },
            status="scheduled",
            revision=1,
        )
    )
    await db.flush()
    db.add(
        StepRow(
            id=step_id,
            user_id=user_id,
            task_id=task_id,
            order_index=0,
            title="Bước 1",
            expected_output="Hoàn tất",
            estimated_base_seconds=1800,
            remaining_base_seconds=1800,
            buffer_multiplier=1.2,
            estimate_source="user_confirmed",
            status="pending",
        )
    )
    await db.flush()

    sess_start = now_utc + timedelta(hours=2)
    sess_end = sess_start + timedelta(minutes=30)
    db.add(
        SessionRow(
            id=session_id,
            user_id=user_id,
            step_id=step_id,
            scheduled_start=sess_start,
            scheduled_end=sess_end,
            micro_task_text="Mở tài liệu",
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=300,
            segments=[
                {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                {"order_index": 1, "kind": "rest", "duration_seconds": 300},
            ],
            duration_seconds=1800,
            schedule_revision=desired_schedule_revision,
            status="scheduled",
            start_state="waiting",
            start_decision_due_at=sess_start + timedelta(minutes=15),
        )
    )
    await db.flush()
    db.add(
        CalendarEventLink(
            id=link_id,
            user_id=user_id,
            session_id=session_id,
            connection_id=conn_id,
            google_calendar_id="primary",
            google_event_id=google_event_id,
            google_etag=google_etag,
            desired_schedule_revision=desired_schedule_revision,
            sync_state=sync_state,
            is_current=True,
        )
    )
    await db.commit()
    return {
        "user_id": user_id,
        "conn_id": conn_id,
        "task_id": task_id,
        "step_id": step_id,
        "session_id": session_id,
        "link_id": link_id,
        "sess_start": sess_start,
        "sess_end": sess_end,
    }


@pytest.mark.asyncio
async def test_sync_worker_does_not_bump_connection_revision_or_break_rg05_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Heartbeat last_successful_sync_at qua Core SQL không được tăng CalendarConnection.revision,
    giúp confirm_configuration_preview và confirm_event_change_preview (RG05) không bị 409 giả;
    trong khi CalendarAuthError vẫn tăng revision nguyên tử khi chuyển sang needs_reconnect.
    """
    reset_fake_calendar_client()
    try:
        fake_client = get_calendar_client()
        assert isinstance(fake_client, FakeCalendarClient)

        async with _regression_db_env() as sf:
            async with sf() as db:
                ids = await _seed_calendar_sync_world(db, desired_schedule_revision=1)
                job_id = uuid4()
                db.add(
                    CalendarSyncJob(
                        id=job_id,
                        user_id=ids["user_id"],
                        connection_id=ids["conn_id"],
                        event_link_id=ids["link_id"],
                        target_kind="session",
                        target_ref=str(ids["session_id"]),
                        operation="create",
                        target_schedule_revision=1,
                        idempotency_key=f"sync-rev-create-{job_id.hex[:8]}",
                        job_state="queued",
                        attempt_count=0,
                        payload_snapshot={
                            "summary": "Focus Block",
                            "start": {"dateTime": ids["sess_start"].isoformat()},
                            "end": {"dateTime": ids["sess_end"].isoformat()},
                        },
                    )
                )
                await db.commit()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = _auth_headers(ids["user_id"])

                # Tạo cả configuration preview lẫn event-change preview trước khi worker chạy (base_connection_revision=1)
                cfg_prev = await client.post(
                    f"/api/v1/calendar/connections/{ids['conn_id']}/configuration-previews",
                    headers=headers,
                    json={"read_calendar_ids": ["primary"], "write_calendar_id": "primary"},
                )
                assert cfg_prev.status_code == 200, cfg_prev.text
                cfg_pid = cfg_prev.json()["data"]["preview_id"]

                ev_prev = await client.post(
                    "/api/v1/calendar/event-change-previews",
                    headers=headers,
                    json={
                        "operation": "create",
                        "connection_id": str(ids["conn_id"]),
                        "google_calendar_id": "primary",
                        "payload": {"summary": "RG05 Event After Sync"},
                    },
                )
                assert ev_prev.status_code == 200, ev_prev.text
                ev_pid = ev_prev.json()["data"]["preview_id"]

                # Worker xử lý job create -> cập nhật last_successful_sync_at nhưng KHÔNG tăng CalendarConnection.revision
                async with sf() as db:
                    processed = await process_calendar_sync_jobs(
                        db, user_id=ids["user_id"], limit=10
                    )
                    assert processed == 1

                async with sf() as db:
                    conn_row = await db.get(CalendarConnection, ids["conn_id"])
                    job_row = await db.get(CalendarSyncJob, job_id)
                    link_row = await db.get(CalendarEventLink, ids["link_id"])
                    assert conn_row is not None
                    assert conn_row.revision == 1
                    assert conn_row.last_successful_sync_at is not None
                    assert job_row is not None and job_row.job_state == "completed"
                    assert link_row is not None and link_row.sync_state == "synced"

                # Cả 2 preview RG05 đều confirm 200 OK (không bị 409 REVISION_CONFLICT giả)
                ev_conf = await client.post(
                    f"/api/v1/calendar/event-change-previews/{ev_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert ev_conf.status_code == 200, ev_conf.text

                cfg_conf = await client.post(
                    f"/api/v1/calendar/configuration-previews/{cfg_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert cfg_conf.status_code == 200, cfg_conf.text

            # Xác nhận CalendarAuthError VẪN tăng revision nguyên tử khi chuyển connection_state -> needs_reconnect
            async with sf() as db:
                conn_before_auth = await db.get(CalendarConnection, ids["conn_id"])
                assert conn_before_auth is not None
                rev_before_auth = conn_before_auth.revision
                auth_job = CalendarSyncJob(
                    id=uuid4(),
                    user_id=ids["user_id"],
                    connection_id=ids["conn_id"],
                    target_kind="session",
                    target_ref=str(ids["session_id"]),
                    operation="create",
                    idempotency_key=f"sync-auth-bump-{uuid4().hex[:8]}",
                    job_state="queued",
                    attempt_count=0,
                    payload_snapshot={"summary": "Trigger Auth Error"},
                )
                auth_job_id = auth_job.id
                db.add(auth_job)
                await db.commit()

                cal_service = CalendarService(db)

                async def _raise_auth(*args: Any, **kwargs: Any) -> dict[str, Any]:
                    raise CalendarAuthError("401 revoked")

                monkeypatch.setattr(cal_service.client, "create_event", _raise_auth)
                status_auth = await process_sync_job(db, auth_job, cal_service)
                assert status_auth == "failed"

            async with sf() as db:
                conn_after_auth = await db.get(CalendarConnection, ids["conn_id"])
                job_after_auth = await db.get(CalendarSyncJob, auth_job_id)
                assert conn_after_auth is not None
                assert conn_after_auth.connection_state == "needs_reconnect"
                assert conn_after_auth.revision == rev_before_auth + 1
                assert job_after_auth is not None
                assert job_after_auth.job_state == "failed"
                assert job_after_auth.last_error_code == "AUTH_EXPIRED"
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_sync_worker_chained_update_then_delete_uses_latest_link_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chained jobs trên cùng event_link_id (Job 1 update, Job 2 delete đều enqueue với expected_google_etag='e1'):
    sau khi Job 1 cập nhật Google sang ETag mới, Job 2 phải đọc link.google_etag mới nhất thay vì gửi 'e1' cũ.
    """
    reset_fake_calendar_client()
    try:
        fake_client = get_calendar_client()
        assert isinstance(fake_client, FakeCalendarClient)
        fake_client.mock_events["primary"] = [
            {
                "id": "evt_chain_1",
                "summary": "Initial Focus",
                "etag": '"e1"',
                "status": "confirmed",
            }
        ]

        seen_calls: list[tuple[str, str | None]] = []
        orig_update = fake_client.update_event
        orig_delete = fake_client.delete_event

        async def _spied_update(
            access_token: str,
            calendar_id: str,
            event_id: str,
            event_payload: dict[str, Any],
            expected_etag: str | None = None,
        ) -> dict[str, Any]:
            seen_calls.append(("update", expected_etag))
            return await orig_update(
                access_token,
                calendar_id,
                event_id,
                event_payload,
                expected_etag=expected_etag,
            )

        async def _spied_delete(
            access_token: str,
            calendar_id: str,
            event_id: str,
            expected_etag: str | None = None,
        ) -> bool:
            seen_calls.append(("delete", expected_etag))
            return await orig_delete(
                access_token,
                calendar_id,
                event_id,
                expected_etag=expected_etag,
            )

        monkeypatch.setattr(fake_client, "update_event", _spied_update)
        monkeypatch.setattr(fake_client, "delete_event", _spied_delete)

        async with _regression_db_env() as sf:
            t0 = datetime.now(UTC).replace(microsecond=0)
            async with sf() as db:
                ids = await _seed_calendar_sync_world(
                    db,
                    google_event_id="evt_chain_1",
                    google_etag='"e1"',
                    desired_schedule_revision=3,
                    sync_state="pending",
                )
                j1_id = uuid4()
                j2_id = uuid4()
                db.add_all(
                    [
                        CalendarSyncJob(
                            id=j1_id,
                            user_id=ids["user_id"],
                            connection_id=ids["conn_id"],
                            event_link_id=ids["link_id"],
                            target_kind="session",
                            target_ref=str(ids["session_id"]),
                            operation="update",
                            target_schedule_revision=2,
                            expected_google_etag='"e1"',
                            idempotency_key=f"chain-upd-{j1_id.hex[:8]}",
                            job_state="queued",
                            attempt_count=0,
                            created_at=t0,
                            payload_snapshot={
                                "summary": "Updated Focus",
                                "start": {"dateTime": ids["sess_start"].isoformat()},
                                "end": {"dateTime": ids["sess_end"].isoformat()},
                            },
                        ),
                        CalendarSyncJob(
                            id=j2_id,
                            user_id=ids["user_id"],
                            connection_id=ids["conn_id"],
                            event_link_id=ids["link_id"],
                            target_kind="session",
                            target_ref=str(ids["session_id"]),
                            operation="delete",
                            target_schedule_revision=3,
                            expected_google_etag='"e1"',
                            idempotency_key=f"chain-del-{j2_id.hex[:8]}",
                            job_state="queued",
                            attempt_count=0,
                            created_at=t0 + timedelta(seconds=1),
                            payload_snapshot={},
                        ),
                    ]
                )
                await db.commit()

            async with sf() as db:
                processed = await process_calendar_sync_jobs(
                    db, user_id=ids["user_id"], limit=10
                )
                assert processed == 2

            assert seen_calls == [
                ("update", '"e1"'),
                ("delete", '"mock_etag_updated_0"'),
            ]
            assert fake_client.mock_events["primary"] == []

            async with sf() as db:
                j1_row = await db.get(CalendarSyncJob, j1_id)
                j2_row = await db.get(CalendarSyncJob, j2_id)
                link_row = await db.get(CalendarEventLink, ids["link_id"])
                assert j1_row is not None and j1_row.job_state == "completed"
                assert j1_row.last_error_code is None
                assert j2_row is not None and j2_row.job_state == "completed"
                assert j2_row.last_error_code is None
                assert link_row is not None
                assert link_row.is_current is False
                assert link_row.sync_state == "deleted"
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_sync_worker_survives_concurrent_connection_and_link_revision_bump_after_google_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Khi session khác tăng CalendarConnection.revision và CalendarEventLink.revision trong lúc
    Google update_event đang chạy thành công, worker tuyệt đối không dính StaleDataError hay rollback
    mất ETag mới mà Google vừa trả về; đồng thời chỉ set sync_state='synced' khi desired_schedule_revision <= target.
    """
    reset_fake_calendar_client()
    try:
        async with _regression_db_env() as sf:
            async with sf() as db:
                ids = await _seed_calendar_sync_world(
                    db,
                    google_event_id="evt_race_1",
                    google_etag='"e1"',
                    desired_schedule_revision=2,
                    sync_state="pending",
                )
                j1_id = uuid4()
                db.add(
                    CalendarSyncJob(
                        id=j1_id,
                        user_id=ids["user_id"],
                        connection_id=ids["conn_id"],
                        event_link_id=ids["link_id"],
                        target_kind="session",
                        target_ref=str(ids["session_id"]),
                        operation="update",
                        target_schedule_revision=2,
                        expected_google_etag='"e1"',
                        idempotency_key=f"race-bump-1-{j1_id.hex[:8]}",
                        job_state="queued",
                        attempt_count=0,
                        payload_snapshot={"summary": "Race Update 1"},
                    )
                )
                await db.commit()

            # Case A: trong lúc Google update_event thành công (etag='e2_google'), session khác
            # bump CalendarConnection.revision (1->2) và bump link.desired_schedule_revision (2->3, revision 1->2)
            async with sf() as db:
                cal_service = CalendarService(db)

                async def _update_with_concurrent_bumps_case_a(*args: Any, **kwargs: Any) -> dict[str, Any]:
                    async with sf() as other_db:
                        c_other = await other_db.get(CalendarConnection, ids["conn_id"])
                        l_other = await other_db.get(CalendarEventLink, ids["link_id"])
                        assert c_other is not None and l_other is not None
                        c_other.read_calendar_ids = ["primary", "secondary"]
                        l_other.desired_schedule_revision = 3
                        await other_db.commit()
                        assert c_other.revision == 2
                        assert l_other.revision == 2
                    return {"id": "evt_race_1", "etag": '"e2_google"', "status": "confirmed"}

                monkeypatch.setattr(
                    cal_service.client, "update_event", _update_with_concurrent_bumps_case_a
                )
                job_a = await db.get(CalendarSyncJob, j1_id)
                assert job_a is not None
                result_a = await process_sync_job(db, job_a, cal_service)
                assert result_a == "completed"
                assert job_a.job_state == "completed"

            async with sf() as db:
                j1_after = await db.get(CalendarSyncJob, j1_id)
                l_after = await db.get(CalendarEventLink, ids["link_id"])
                c_after = await db.get(CalendarConnection, ids["conn_id"])
                assert j1_after is not None
                assert j1_after.job_state == "completed"
                assert j1_after.attempt_count == 0
                assert j1_after.last_error_code is None
                assert l_after is not None
                assert l_after.google_etag == '"e2_google"'
                assert l_after.synced_schedule_revision == 2
                # Vì desired_schedule_revision đã tăng lên 3 > target_schedule_revision (2), giữ nguyên 'pending'
                assert l_after.sync_state == "pending"
                assert c_after is not None
                assert c_after.revision == 2
                assert c_after.last_successful_sync_at is not None

            # Case B: job kế tiếp (target_schedule_revision=3 == desired_schedule_revision) có concurrent
            # bump link.revision nhưng không tăng desired_schedule_revision -> chuyển sync_state='synced'
            async with sf() as db:
                j2_id = uuid4()
                db.add(
                    CalendarSyncJob(
                        id=j2_id,
                        user_id=ids["user_id"],
                        connection_id=ids["conn_id"],
                        event_link_id=ids["link_id"],
                        target_kind="session",
                        target_ref=str(ids["session_id"]),
                        operation="update",
                        target_schedule_revision=3,
                        expected_google_etag='"e1"',
                        idempotency_key=f"race-bump-2-{j2_id.hex[:8]}",
                        job_state="queued",
                        attempt_count=0,
                        payload_snapshot={"summary": "Race Update 2"},
                    )
                )
                await db.commit()

            observed_etag_b: list[str | None] = []
            async with sf() as db:
                cal_service = CalendarService(db)

                async def _update_with_concurrent_bumps_case_b(
                    access_token: str,
                    calendar_id: str,
                    event_id: str,
                    event_payload: dict[str, Any],
                    expected_etag: str | None = None,
                ) -> dict[str, Any]:
                    observed_etag_b.append(expected_etag)
                    async with sf() as other_db:
                        l_other = await other_db.get(CalendarEventLink, ids["link_id"])
                        assert l_other is not None
                        l_other.updated_at = datetime.now(UTC) + timedelta(seconds=1)
                        await other_db.commit()
                    return {"id": "evt_race_1", "etag": '"e3_google"', "status": "confirmed"}

                monkeypatch.setattr(
                    cal_service.client, "update_event", _update_with_concurrent_bumps_case_b
                )
                job_b = await db.get(CalendarSyncJob, j2_id)
                assert job_b is not None
                result_b = await process_sync_job(db, job_b, cal_service)
                assert result_b == "completed"

            assert observed_etag_b == ['"e2_google"']
            async with sf() as db:
                l_final = await db.get(CalendarEventLink, ids["link_id"])
                assert l_final is not None
                assert l_final.google_etag == '"e3_google"'
                assert l_final.synced_schedule_revision == 3
                assert l_final.sync_state == "synced"
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_sync_worker_concurrent_refresh_and_background_loop_no_false_412_or_diverged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chạy song song background loop (skip_busy_connections=True) và các request POST /refresh
    cùng lúc trên cùng connection_id: không trùng execute, không 412 giả và links_diverged == '0'.
    """
    reset_fake_calendar_client()
    try:
        fake_client = get_calendar_client()
        assert isinstance(fake_client, FakeCalendarClient)

        async with _regression_db_env() as sf:
            async with sf() as db:
                ids = await _seed_calendar_sync_world(
                    db,
                    google_event_id="evt_conc_1",
                    google_etag='"e1"',
                    desired_schedule_revision=2,
                    sync_state="pending",
                )
                job_id = uuid4()
                db.add(
                    CalendarSyncJob(
                        id=job_id,
                        user_id=ids["user_id"],
                        connection_id=ids["conn_id"],
                        event_link_id=ids["link_id"],
                        target_kind="session",
                        target_ref=str(ids["session_id"]),
                        operation="update",
                        target_schedule_revision=2,
                        expected_google_etag='"e1"',
                        idempotency_key=f"conc-refresh-{job_id.hex[:8]}",
                        job_state="queued",
                        attempt_count=0,
                        payload_snapshot={
                            "summary": "Concurrent Rescheduled Focus",
                            "start": {"dateTime": ids["sess_start"].isoformat()},
                            "end": {"dateTime": ids["sess_end"].isoformat()},
                        },
                    )
                )
                await db.commit()

            fake_client.mock_events["primary"] = [
                {
                    "id": "evt_conc_1",
                    "summary": "Old Focus",
                    "start": {"dateTime": (ids["sess_start"] - timedelta(hours=1)).isoformat()},
                    "end": {"dateTime": (ids["sess_end"] - timedelta(hours=1)).isoformat()},
                    "etag": '"e1"',
                    "status": "confirmed",
                }
            ]

            update_calls = 0
            orig_update = fake_client.update_event

            async def _slow_strict_update(
                access_token: str,
                calendar_id: str,
                event_id: str,
                event_payload: dict[str, Any],
                expected_etag: str | None = None,
            ) -> dict[str, Any]:
                nonlocal update_calls
                update_calls += 1
                await asyncio.sleep(0.05)
                return await orig_update(
                    access_token,
                    calendar_id,
                    event_id,
                    event_payload,
                    expected_etag=expected_etag,
                )

            monkeypatch.setattr(fake_client, "update_event", _slow_strict_update)

            async def _run_background_tick() -> int:
                async with sf() as bg_db:
                    return await process_calendar_sync_jobs(
                        bg_db, limit=10, skip_busy_connections=True
                    )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = _auth_headers(ids["user_id"])
                bg_processed, ref_resp_1, ref_resp_2 = await asyncio.gather(
                    _run_background_tick(),
                    client.post(
                        f"/api/v1/calendar/connections/{ids['conn_id']}/refresh",
                        headers=headers,
                        json={},
                    ),
                    client.post(
                        f"/api/v1/calendar/connections/{ids['conn_id']}/refresh",
                        headers=headers,
                        json={},
                    ),
                )

                assert ref_resp_1.status_code == 200, ref_resp_1.text
                assert ref_resp_2.status_code == 200, ref_resp_2.text
                assert ref_resp_1.json()["data"]["links_diverged"] == "0"
                assert ref_resp_2.json()["data"]["links_diverged"] == "0"
                assert ref_resp_1.json()["data"]["links_deleted_externally"] == "0"
                assert ref_resp_2.json()["data"]["links_deleted_externally"] == "0"
                assert (
                    bg_processed
                    + int(ref_resp_1.json()["data"]["jobs_processed"])
                    + int(ref_resp_2.json()["data"]["jobs_processed"])
                    == 1
                )

            assert update_calls == 1
            async with sf() as db:
                job_row = await db.get(CalendarSyncJob, job_id)
                link_row = await db.get(CalendarEventLink, ids["link_id"])
                assert job_row is not None
                assert job_row.job_state == "completed"
                assert job_row.last_error_code is None
                assert link_row is not None
                assert link_row.google_etag == '"mock_etag_updated_0"'
                assert link_row.sync_state == "synced"
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_reconcile_skips_deleted_externally_when_create_job_still_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reconcile_external_link_changes không được đánh dấu deleted_externally cho link
    đang còn CalendarSyncJob(operation='create', job_state='queued'), nhưng vẫn đánh dấu
    deleted_externally cho link không còn pending job khi Google trả None.
    """
    reset_fake_calendar_client()
    try:
        async with _regression_db_env() as sf:
            async with sf() as db:
                ids = await _seed_calendar_sync_world(
                    db,
                    google_event_id="evt_pending_create_1",
                    google_etag=None,
                    desired_schedule_revision=1,
                    sync_state="pending",
                )
                # Tạo session thứ 2 + link thứ 2 KHÔNG có pending job
                sess2_id = uuid4()
                link2_id = uuid4()
                sess2_start = ids["sess_end"] + timedelta(minutes=30)
                sess2_end = sess2_start + timedelta(minutes=30)
                db.add(
                    SessionRow(
                        id=sess2_id,
                        user_id=ids["user_id"],
                        step_id=ids["step_id"],
                        scheduled_start=sess2_start,
                        scheduled_end=sess2_end,
                        micro_task_text="Mở tài liệu 2",
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
                        start_decision_due_at=sess2_start + timedelta(minutes=15),
                    )
                )
                await db.flush()
                db.add(
                    CalendarEventLink(
                        id=link2_id,
                        user_id=ids["user_id"],
                        session_id=sess2_id,
                        connection_id=ids["conn_id"],
                        google_calendar_id="primary",
                        google_event_id="evt_deleted_ext_2",
                        google_etag='"etag_ext_2"',
                        desired_schedule_revision=1,
                        synced_schedule_revision=1,
                        sync_state="synced",
                        is_current=True,
                    )
                )
                db.add(
                    CalendarSyncJob(
                        id=uuid4(),
                        user_id=ids["user_id"],
                        connection_id=ids["conn_id"],
                        event_link_id=ids["link_id"],
                        target_kind="session",
                        target_ref=str(ids["session_id"]),
                        operation="create",
                        target_schedule_revision=1,
                        idempotency_key=f"pending-create-{uuid4().hex[:8]}",
                        job_state="queued",
                        attempt_count=0,
                        payload_snapshot={"id": "evt_pending_create_1", "summary": "Pending"},
                    )
                )
                await db.commit()

            async with sf() as db:
                cal_service = CalendarService(db)

                async def _always_none(
                    access_token: str, calendar_id: str, event_id: str
                ) -> dict[str, Any] | None:
                    return None

                monkeypatch.setattr(cal_service.client, "get_event", _always_none)
                reconciled = await cal_service.reconcile_external_link_changes(
                    ids["user_id"], ids["conn_id"]
                )
                assert reconciled["links_checked"] == 2
                assert reconciled["links_deleted_externally"] == 1
                assert reconciled["links_diverged"] == 0

            async with sf() as db:
                pending_link = await db.get(CalendarEventLink, ids["link_id"])
                deleted_link = await db.get(CalendarEventLink, link2_id)
                assert pending_link is not None
                assert pending_link.is_current is True
                assert pending_link.sync_state == "pending"
                assert deleted_link is not None
                assert deleted_link.is_current is False
                assert deleted_link.sync_state == "deleted_externally"
    finally:
        reset_fake_calendar_client()


@pytest.mark.asyncio
async def test_dedicated_proplan_va_focus_calendar_excluded_from_read_calendars() -> None:
    """Phase 3: Lịch ghi chuyên dụng ProPlan VA Focus luôn bị loại khỏi read_calendar_ids
    trên provision_new, update_calendar_selection, configuration-previews/confirm và tự chữa lành
    dữ liệu cũ trên GET /calendars mà không làm tăng revision với kết nối đã sạch.
    Đồng thời vẫn cho phép lịch primary làm cả read lẫn write calendar.
    """
    reset_fake_calendar_client()
    try:
        fake_client = get_calendar_client()
        assert isinstance(fake_client, FakeCalendarClient)
        focus_cal_id = "proplan_focus@group.calendar.google.com"
        primary_email = "mock_user@gmail.com"
        work_cal_id = "work_calendar_123@group.calendar.google.com"

        async with _regression_db_env() as sf:
            user_id = uuid4()
            conn_id = uuid4()
            async with sf() as db:
                db.add(
                    User(
                        id=user_id,
                        email=f"cal_excl_{user_id.hex[:8]}@example.com",
                        display_name="Focus Cal Exclusion",
                    )
                )
                await db.flush()
                creds = encrypt_credentials(
                    {
                        "access_token": "acc_focus_excl",
                        "refresh_token": "ref_focus_excl",
                        "expires_in": 3600,
                        "stored_at": datetime.now(UTC).isoformat(),
                        "user_id": str(user_id),
                        "connection_id": str(conn_id),
                    }
                )
                db.add(
                    CalendarConnection(
                        id=conn_id,
                        user_id=user_id,
                        provider="google",
                        account_ref=primary_email,
                        connection_state="connected",
                        read_calendar_ids=["primary"],
                        write_calendar_id="primary",
                        granted_scopes=["https://www.googleapis.com/auth/calendar"],
                        credential_ref=creds,
                    )
                )
                await db.commit()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = _auth_headers(user_id)

                # 1. provision_new qua configuration-previews + confirm ngay cả khi client gửi kèm focus_cal_id trong read_calendar_ids
                prev_prov = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary", primary_email, work_cal_id, focus_cal_id],
                        "write_calendar_id": "provision_new",
                        "existing_event_action": "provision_new",
                    },
                )
                assert prev_prov.status_code == 200, prev_prov.text
                prov_pid = prev_prov.json()["data"]["preview_id"]

                conf_prov = await client.post(
                    f"/api/v1/calendar/configuration-previews/{prov_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert conf_prov.status_code == 200, conf_prov.text
                conf_prov_data = conf_prov.json()["data"]
                assert conf_prov_data["write_calendar_id"] == focus_cal_id
                assert conf_prov_data["read_calendar_ids"] == ["primary", primary_email, work_cal_id]

                # 2. update_calendar_selection: loại focus_cal_id, khử trùng, bỏ chuỗi rỗng
                sel_focus = await client.patch(
                    f"/api/v1/calendar/{conn_id}/selection",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary", focus_cal_id, work_cal_id, "primary", ""],
                        "write_calendar_id": focus_cal_id,
                    },
                )
                assert sel_focus.status_code == 200, sel_focus.text
                assert sel_focus.json()["data"]["read_calendar_ids"] == ["primary", work_cal_id]

                # primary làm write_calendar_id vẫn được phép nằm trong read_calendar_ids
                sel_primary = await client.patch(
                    f"/api/v1/calendar/{conn_id}/selection",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary", primary_email],
                        "write_calendar_id": "primary",
                    },
                )
                assert sel_primary.status_code == 200, sel_primary.text
                assert sel_primary.json()["data"]["read_calendar_ids"] == ["primary", primary_email]

                sel_acct_primary = await client.patch(
                    f"/api/v1/calendar/{conn_id}/selection",
                    headers=headers,
                    json={
                        "read_calendar_ids": [primary_email, work_cal_id],
                        "write_calendar_id": primary_email,
                    },
                )
                assert sel_acct_primary.status_code == 200, sel_acct_primary.text
                assert sel_acct_primary.json()["data"]["read_calendar_ids"] == [primary_email, work_cal_id]

                # 3. configuration-previews + confirm với explicit focus_cal_id -> HTTP response preview, preview.payload & confirm đều loại focus_cal_id
                prev_explicit = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary", focus_cal_id],
                        "write_calendar_id": focus_cal_id,
                    },
                )
                assert prev_explicit.status_code == 200, prev_explicit.text
                assert prev_explicit.json()["data"]["read_calendar_ids"] == ["primary"]
                exp_pid = prev_explicit.json()["data"]["preview_id"]

                async with sf() as db:
                    prev_row = await db.get(CalendarPreview, UUID(exp_pid))
                    assert prev_row is not None
                    assert (prev_row.payload or {}).get("read_calendar_ids") == ["primary"]

                conf_explicit = await client.post(
                    f"/api/v1/calendar/configuration-previews/{exp_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert conf_explicit.status_code == 200, conf_explicit.text
                assert conf_explicit.json()["data"]["write_calendar_id"] == focus_cal_id
                assert conf_explicit.json()["data"]["read_calendar_ids"] == ["primary"]

                # Case A: write_calendar_id="primary" & read_calendar_ids=["primary", focus_cal_id]
                # Cả PATCH /selection lẫn POST /configuration-previews + confirm đều loại focus_cal_id ngay lập tức (không đợi GET /calendars)
                sel_case_a = await client.patch(
                    f"/api/v1/calendar/{conn_id}/selection",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary", focus_cal_id],
                        "write_calendar_id": "primary",
                    },
                )
                assert sel_case_a.status_code == 200, sel_case_a.text
                assert sel_case_a.json()["data"]["read_calendar_ids"] == ["primary"]

                prev_case_a = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary", focus_cal_id],
                        "write_calendar_id": "primary",
                    },
                )
                assert prev_case_a.status_code == 200, prev_case_a.text
                assert prev_case_a.json()["data"]["read_calendar_ids"] == ["primary"]
                case_a_pid = prev_case_a.json()["data"]["preview_id"]

                conf_case_a = await client.post(
                    f"/api/v1/calendar/configuration-previews/{case_a_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert conf_case_a.status_code == 200, conf_case_a.text
                assert conf_case_a.json()["data"]["read_calendar_ids"] == ["primary"]

                # 4. GET /calendars trên kết nối ĐÃ SẠCH không làm tăng conn.revision
                async with sf() as db:
                    conn_clean = await db.get(CalendarConnection, conn_id)
                    assert conn_clean is not None
                    clean_revision = conn_clean.revision

                list_clean = await client.get(
                    f"/api/v1/calendar/connections/{conn_id}/calendars",
                    headers=headers,
                )
                assert list_clean.status_code == 200, list_clean.text
                async with sf() as db:
                    conn_after_clean = await db.get(CalendarConnection, conn_id)
                    assert conn_after_clean is not None
                    assert conn_after_clean.revision == clean_revision

                # 5. Self-healing: dòng DB cũ bị dính focus_cal_id trong read_calendar_ids (kể cả khi write_calendar_id='primary')
                async with sf() as db:
                    conn_dirty = await db.get(CalendarConnection, conn_id)
                    assert conn_dirty is not None
                    conn_dirty.read_calendar_ids = [primary_email, focus_cal_id]
                    conn_dirty.write_calendar_id = "primary"
                    await db.commit()

                list_healed = await client.get(
                    f"/api/v1/calendar/connections/{conn_id}/calendars",
                    headers=headers,
                )
                assert list_healed.status_code == 200, list_healed.text
                items_by_id = {it["id"]: it for it in list_healed.json()["data"]["items"]}
                assert items_by_id[primary_email]["selected_for_read"] is True
                assert items_by_id[focus_cal_id]["selected_for_read"] is False

                async with sf() as db:
                    conn_healed = await db.get(CalendarConnection, conn_id)
                    assert conn_healed is not None
                    assert conn_healed.read_calendar_ids == [primary_email]

                # Case B: account_ref chưa phân giải ("unknown@gmail.com") nhưng mock_calendars có primary id="primary_user@gmail.com"
                # Cả PATCH /selection lẫn POST /configuration-previews + confirm đều giữ nguyên "primary_user@gmail.com" trong read_calendar_ids
                async with sf() as db:
                    conn_unk = await db.get(CalendarConnection, conn_id)
                    assert conn_unk is not None
                    conn_unk.account_ref = "unknown@gmail.com"
                    conn_unk.read_calendar_ids = ["primary"]
                    conn_unk.write_calendar_id = "primary"
                    await db.commit()

                fake_client.mock_calendars = [
                    {
                        "id": "primary_user@gmail.com",
                        "summary": "Primary User",
                        "primary": True,
                        "accessRole": "owner",
                        "backgroundColor": "#9a9cff",
                    },
                    {
                        "id": focus_cal_id,
                        "summary": "ProPlan VA Focus",
                        "primary": False,
                        "accessRole": "owner",
                        "backgroundColor": "#5484ed",
                    },
                ]

                sel_case_b = await client.patch(
                    f"/api/v1/calendar/{conn_id}/selection",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary_user@gmail.com"],
                        "write_calendar_id": "primary_user@gmail.com",
                    },
                )
                assert sel_case_b.status_code == 200, sel_case_b.text
                assert sel_case_b.json()["data"]["read_calendar_ids"] == ["primary_user@gmail.com"]
                assert sel_case_b.json()["data"]["write_calendar_id"] == "primary_user@gmail.com"

                prev_case_b = await client.post(
                    f"/api/v1/calendar/connections/{conn_id}/configuration-previews",
                    headers=headers,
                    json={
                        "read_calendar_ids": ["primary_user@gmail.com"],
                        "write_calendar_id": "primary_user@gmail.com",
                    },
                )
                assert prev_case_b.status_code == 200, prev_case_b.text
                assert prev_case_b.json()["data"]["read_calendar_ids"] == ["primary_user@gmail.com"]
                case_b_pid = prev_case_b.json()["data"]["preview_id"]

                conf_case_b = await client.post(
                    f"/api/v1/calendar/configuration-previews/{case_b_pid}/confirm",
                    headers=headers,
                    json={"expected_preview_revision": 1},
                )
                assert conf_case_b.status_code == 200, conf_case_b.text
                assert conf_case_b.json()["data"]["read_calendar_ids"] == ["primary_user@gmail.com"]
                assert conf_case_b.json()["data"]["write_calendar_id"] == "primary_user@gmail.com"

                # Placeholder account_ref không được xem là primary_ids
                for placeholder in ("google_calendar_user", "unknown@gmail.com", "UNKNOWN@GOOGLE.COM"):
                    assert (
                        CalendarService._sanitize_read_calendar_ids(
                            [placeholder], placeholder, account_ref=placeholder
                        )
                        == []
                    )
    finally:
        reset_fake_calendar_client()

