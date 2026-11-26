"""Bộ kiểm thử toàn diện cho BE-UF7-01 .. BE-UF7-04 (Cycle 2 UF7 Upgrade)."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.main import app
from app.models import Base
from app.models.calendar import PlanningContext
from app.models.execution import SessionRun
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion
from app.models.settings import Availability as AvailabilityRow
from app.models.settings import SettingChangeRequest as SettingChangeRow
from app.models.step import Step as StepRow
from app.models.system import Reminder
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.user import CurrentUser
from app.services import reschedule_service, settings_service, task_state_service

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _auth_headers(user_id: UUID, idempotency_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"uf7_upg_{user_id.hex[:8]}@example.com",
        display_name="UF7 Upgrade Tester",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-{user_id.hex[:8]}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


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


async def _seed_user(
    db: AsyncSession,
    *,
    now: datetime,
    buffer_multiplier: float = 1.2,
    effective_revision: int = 1,
    cooldown_until: datetime | None = None,
) -> UUID:
    uid = uuid4()
    db.add(
        User(
            id=uid,
            email=f"u_{uid.hex[:8]}@example.com",
            hashed_password="pw",
            display_name="UF7 Upgrade Tester",
        )
    )
    await db.flush()
    db.add(
        UserPreferenceRow(
            user_id=uid,
            revision=1,
            effective_revision=effective_revision,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=buffer_multiplier,
            reminder_style="gentle",
            suggestion_cooldown_until=cooldown_until,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        AvailabilityRow(
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
            confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return uid


async def _seed_task_with_future_session(
    db: AsyncSession,
    uid: UUID,
    *,
    now: datetime,
    with_scheduled_session: bool = True,
    with_active_run: bool = False,
    buffer_multiplier: float = 1.2,
    step_status: str = "pending",
) -> dict[str, UUID]:
    task_id = uuid4()
    step_id = uuid4()
    sess_id = uuid4()
    run_id = uuid4()

    avail_row = await db.scalar(
        select(AvailabilityRow).where(AvailabilityRow.user_id == uid).limit(1)
    )
    avail_id = avail_row.id if avail_row is not None else uuid4()

    existing_ctx = await db.scalar(
        select(PlanningContext).where(PlanningContext.user_id == uid).limit(1)
    )
    if existing_ctx is None:
        db.add(
            PlanningContext(
                id=uuid4(),
                user_id=uid,
                revision=1,
                availability_id=avail_id,
                availability_revision=1,
                selected_calendar_ids=["primary"],
                query_start=now - timedelta(hours=2),
                query_end=now + timedelta(days=14),
                busy_intervals=[],
                fetched_at=now - timedelta(minutes=15),
                sync_state="ok",
                calendar_errors=[],
                event_snapshots=[],
                coverage_state="complete",
                manual_busy_intervals=[],
                excluded_sources=[],
                selected_calendar_sources=[],
            )
        )
    existing_ver = await db.scalar(
        select(ScheduleVersionRow).where(ScheduleVersionRow.user_id == uid).limit(1)
    )
    if existing_ver is None:
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

    db.add(
        TaskRow(
            id=task_id,
            user_id=uid,
            revision=1,
            title="Công việc kiểm thử UF7",
            description="Chi tiết UF7",
            scope="Hoàn thành tài liệu",
            time_constraint={
                "mode": "deadline",
                "at": (now + timedelta(days=5)).isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
            },
            importance=1,
            status="in_progress",
            decomposition_state="complete",
            availability={
                "timezone": "Asia/Ho_Chi_Minh",
                "weekly_rules": [
                    {"weekday": w, "start_local": "08:00", "end_local": "22:00"}
                    for w in range(1, 8)
                ],
                "date_overrides": [],
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
            title="Bước 1 kiểm thử",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=buffer_multiplier,
            estimate_source="ai",
            status=step_status,
        )
    )
    await db.flush()

    if with_scheduled_session:
        t_start = now + timedelta(hours=3)
        t_end = t_start + timedelta(seconds=1800)
        db.add(
            SessionRow(
                id=sess_id,
                user_id=uid,
                step_id=step_id,
                revision=1,
                scheduled_start=t_start,
                scheduled_end=t_end,
                micro_task_text="Mở file và đọc mục lục",
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
                start_decision_due_at=t_start + timedelta(minutes=5),
            )
        )
        db.add(
            Reminder(
                user_id=uid,
                session_id=sess_id,
                schedule_revision=1,
                kind="pre_session",
                fire_at=t_start - timedelta(seconds=900),
                dedupe_key=f"seed:{sess_id}:900",
                delivery_state="scheduled",
            )
        )

    if with_active_run:
        run_sess_id = uuid4()
        r_start = now - timedelta(minutes=10)
        r_end = now + timedelta(minutes=20)
        db.add(
            SessionRow(
                id=run_sess_id,
                user_id=uid,
                step_id=step_id,
                revision=1,
                scheduled_start=r_start,
                scheduled_end=r_end,
                micro_task_text="Bắt đầu chạy",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=300,
                segments=[
                    {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                ],
                duration_seconds=1800,
                schedule_revision=1,
                status="in_progress",
                start_state="started",
                start_decision_due_at=r_start + timedelta(minutes=5),
            )
        )
        await db.flush()
        db.add(
            SessionRun(
                id=run_id,
                user_id=uid,
                session_id=run_sess_id,
                device_id="device-uf7-test",
                started_at=r_start,
                run_state="running",
                active_phase="work",
                segment_index=0,
                work_elapsed_seconds=120,
                rest_elapsed_seconds=0,
                outcome="unknown",
                settings_snapshot={
                    "effective_revision": 1,
                    "reminder_style": "gentle",
                    "reminder_offsets": [900, 600, 300],
                    "work_interval_seconds": 1500,
                    "buffer_multiplier": buffer_multiplier,
                },
            )
        )

    await db.commit()
    return {"task_id": task_id, "step_id": step_id, "session_id": sess_id, "run_id": run_id}


async def _seed_suggestion(
    db: AsyncSession,
    uid: UUID,
    *,
    now: datetime,
    kind: str = "buffer_multiplier",
    current_value: float | list[int] = 1.2,
    suggested_value: float | list[int] = 1.45,
    status: str = "pending",
    created_at: datetime | None = None,
) -> UUID:
    sid = uuid4()
    ts = created_at or now
    db.add(
        AdaptationSuggestion(
            id=sid,
            user_id=uid,
            kind=kind,
            sample_count=8,
            current_value=current_value,
            suggested_value=suggested_value,
            reason="Thời gian thực tế trung bình cao hơn dự kiến.",
            policy_revision="v2_step_median_checked_inputs",
            sample_refs=[f"step-{i}" for i in range(8)],
            model_revision=None,
            status=status,
            supersedes_suggestion_id=None,
            created_at=ts,
            updated_at=ts,
        )
    )
    await db.commit()
    return sid


@pytest.mark.asyncio
async def test_be_uf7_01_preferences_exposes_pending_change_request_fields(monkeypatch):
    """BE-UF7-01: GET /preferences trả đúng pending_change_request_id & revision trong mọi nhánh pending và trả None sau hủy/reject/approve/UF6 thuần."""
    engine, sf = await _make_env()
    fixed_now = datetime(2026, 11, 2, 9, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        async with sf() as db:
            uid = await _seed_user(db, now=fixed_now)
            seeded = await _seed_task_with_future_session(db, uid, now=fixed_now)
            sugg_id = await _seed_suggestion(db, uid, now=fixed_now)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Chưa có request -> None/None
            r0 = await client.get("/api/v1/preferences", headers=_auth_headers(uid))
            assert r0.status_code == 200
            p0 = r0.json()["data"]
            assert p0["pending_change_request_id"] is None
            assert p0["pending_change_request_revision"] is None

            # 2) Global preference pending_review -> hiện cr1, rev 1; cancel -> None/None
            r_glob = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid, "uf7-01-glob"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"buffer_multiplier": 1.4}},
                },
            )
            assert r_glob.status_code == 200
            cr1 = r_glob.json()["data"]["change_request_id"]
            p1 = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p1["pending_change_request_id"] == cr1
            assert p1["pending_change_request_revision"] == 1

            r_cancel = await client.post(
                f"/api/v1/settings/change-requests/{cr1}/cancel",
                headers=_auth_headers(uid, "uf7-01-cancel"),
                json={"expected_request_revision": p1["pending_change_request_revision"]},
            )
            assert r_cancel.status_code == 200
            p1_after = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p1_after["pending_change_request_id"] is None
            assert p1_after["pending_change_request_revision"] is None

            # 3) target_task_id availability pending_review -> hiện cr2, rev 1; reject UF6 -> None/None
            r_avail = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid, "uf7-01-avail"),
                json={
                    "target_task_id": str(seeded["task_id"]),
                    "expected_task_revision": 1,
                    "changes": {
                        "availability": {
                            "timezone": "Asia/Ho_Chi_Minh",
                            "weekly_rules": [
                                {"weekday": w, "start_local": "10:00", "end_local": "18:00"}
                                for w in range(1, 8)
                            ],
                            "date_overrides": [],
                        }
                    },
                },
            )
            assert r_avail.status_code == 200
            cr2 = r_avail.json()["data"]["change_request_id"]
            prop2_id = r_avail.json()["data"]["pending_proposal_id"]
            p2 = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p2["pending_change_request_id"] == cr2
            assert p2["pending_change_request_revision"] == 1

            prop2 = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid))).json()["data"]
            r_rej = await client.post(
                f"/api/v1/proposals/{prop2_id}/reject",
                headers=_auth_headers(uid, "uf7-01-rej"),
                json={"expected_proposal_revision": prop2["proposal_revision"]},
            )
            assert r_rej.status_code == 200
            p2_after = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p2_after["pending_change_request_id"] is None
            assert p2_after["pending_change_request_revision"] is None

            # 4) respond_to_suggestion pending_review -> hiện cr3, rev 1; approve -> None/None
            r_sugg = await client.post(
                f"/api/v1/suggestions/{sugg_id}/response",
                headers=_auth_headers(uid, "uf7-01-sugg"),
                json={
                    "expected_suggestion_revision": 1,
                    "expected_preference_revision": p2_after["effective_revision"],
                    "action": "accept",
                },
            )
            assert r_sugg.status_code == 200
            cr3 = r_sugg.json()["data"]["change_request_id"]
            p3 = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p3["pending_change_request_id"] == cr3
            assert p3["pending_change_request_revision"] == 1

            prop3 = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid))).json()["data"]
            r_app = await client.post(
                f"/api/v1/proposals/{prop3['proposal_id']}/approve",
                headers=_auth_headers(uid, "uf7-01-app"),
                json={
                    "expected_proposal_revision": prop3["proposal_revision"],
                    "expected_base_schedule_revision": prop3["base_schedule_revision"],
                    "expected_source_revisions": prop3["source_revisions"],
                },
            )
            assert r_app.status_code == 200
            p3_after = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p3_after["pending_change_request_id"] is None
            assert p3_after["pending_change_request_revision"] is None

            # 5) Proposal thuần từ UF6 (POST /api/v1/proposals) KHÔNG làm rò pending_change_request_*
            r_uf6 = await client.post(
                "/api/v1/proposals",
                headers=_auth_headers(uid, "uf7-01-uf6"),
                json={
                    "trigger_reason": "user_reschedule",
                    "target_task_id": str(seeded["task_id"]),
                    "allowed_task_ids": [str(seeded["task_id"])],
                    "expected_base_schedule_revision": prop3["base_schedule_revision"] + 1,
                    "expected_source_revisions": {
                        "task": 1,
                        "schedule": prop3["base_schedule_revision"] + 1,
                        "availability": 1,
                        "settings": p3_after["effective_revision"],
                        "calendar": 1,
                    },
                    "request_text": "Dời lịch sang ngày mai",
                },
            )
            assert r_uf6.status_code == 201
            p_uf6 = (await client.get("/api/v1/preferences", headers=_auth_headers(uid))).json()["data"]
            assert p_uf6["pending_change_request_id"] is None
            assert p_uf6["pending_change_request_revision"] is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_be_uf7_02_deferred_suggestion_accept_rebuild_approve_and_cancel_paths(monkeypatch):
    """BE-UF7-02: Trì hoãn AdaptationSuggestion.status='accepted' tới approve_proposal, giữ qua rebuild_proposal, phục hồi khi cancel/reject/supersede và xử lý nhiều pending rows."""
    engine, sf = await _make_env()
    fixed_now = datetime(2026, 11, 2, 9, 0, 0, tzinfo=TZ_VN)
    cooldown_ts = fixed_now - timedelta(days=1)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        # Nhánh A: accept/edit pending_review -> rebuild_proposal -> approve_proposal (supersede gợi ý cũ khác)
        async with sf() as db:
            uid_a = await _seed_user(db, now=fixed_now, cooldown_until=cooldown_ts)
            await _seed_task_with_future_session(db, uid_a, now=fixed_now)
            old_sugg_id = await _seed_suggestion(
                db,
                uid_a,
                now=fixed_now,
                suggested_value=1.35,
                created_at=fixed_now - timedelta(minutes=10),
            )
            sugg_a_id = await _seed_suggestion(
                db,
                uid_a,
                now=fixed_now,
                suggested_value=1.5,
                created_at=fixed_now,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_resp = await client.post(
                f"/api/v1/suggestions/{sugg_a_id}/response",
                headers=_auth_headers(uid_a, "uf7-02-a-accept"),
                json={
                    "expected_suggestion_revision": 1,
                    "expected_preference_revision": 1,
                    "action": "edit",
                    "edited_value": 1.45,
                },
            )
            assert r_resp.status_code == 200
            res_a = r_resp.json()["data"]
            assert res_a["status"] == "accepted"
            assert res_a["application_state"] == "pending_review"
            assert res_a["pending_proposal_id"] is not None
            assert res_a["change_request_id"] is not None

            # Trong DB, AdaptationSuggestion vẫn là pending; proposed_changes._meta.suggestion_id được gắn
            async with sf() as db:
                sugg_row = await db.get(AdaptationSuggestion, sugg_a_id)
                assert sugg_row is not None
                assert sugg_row.status == "pending"
                assert sugg_row.revision == 1
                prop_row = await db.get(RescheduleProposal, UUID(res_a["pending_proposal_id"]))
                assert prop_row is not None
                assert prop_row.proposed_changes["_meta"]["suggestion_id"] == str(sugg_a_id)

            # Gửi lại response trong lúc pending_review -> 409 SUGGESTION_CHANGED
            r_dup = await client.post(
                f"/api/v1/suggestions/{sugg_a_id}/response",
                headers=_auth_headers(uid_a, "uf7-02-a-dup"),
                json={"expected_suggestion_revision": 1, "action": "accept"},
            )
            assert r_dup.status_code == 409
            assert r_dup.json()["error"]["code"] == "SUGGESTION_CHANGED"

            # Nhánh nhiều pending rows: vì old_sugg_id đang pending và không in-flight, GET /suggestions/current trả old_sugg_id (không bị .limit(1) che khuất)
            r_cur_other = await client.get("/api/v1/suggestions/current", headers=_auth_headers(uid_a))
            assert r_cur_other.status_code == 200
            assert r_cur_other.json()["data"]["suggestion_id"] == str(old_sugg_id)

            # Rebuild proposal -> _meta.suggestion_id vẫn được bảo toàn
            prop_a = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid_a))).json()["data"]
            r_reb = await client.post(
                f"/api/v1/proposals/{prop_a['proposal_id']}/rebuild",
                headers=_auth_headers(uid_a, "uf7-02-a-rebuild"),
                json={
                    "expected_proposal_revision": prop_a["proposal_revision"],
                    "request_text": "Tính lại sau khi mở app",
                },
            )
            assert r_reb.status_code == 201
            prop_rebuilt = r_reb.json()["data"]
            async with sf() as db:
                prop_row_rebuilt = await db.get(RescheduleProposal, UUID(prop_rebuilt["proposal_id"]))
                assert prop_row_rebuilt is not None
                assert prop_row_rebuilt.proposed_changes["_meta"]["suggestion_id"] == str(sugg_a_id)

            # Approve proposal -> sugg_a_id chuyển accepted (rev 2), old_sugg_id bị superseded, pref cập nhật
            r_app = await client.post(
                f"/api/v1/proposals/{prop_rebuilt['proposal_id']}/approve",
                headers=_auth_headers(uid_a, "uf7-02-a-approve"),
                json={
                    "expected_proposal_revision": prop_rebuilt["proposal_revision"],
                    "expected_base_schedule_revision": prop_rebuilt["base_schedule_revision"],
                    "expected_source_revisions": prop_rebuilt["source_revisions"],
                },
            )
            assert r_app.status_code == 200

            async with sf() as db:
                sugg_after = await db.get(AdaptationSuggestion, sugg_a_id)
                old_after = await db.get(AdaptationSuggestion, old_sugg_id)
                cr_after = await db.get(SettingChangeRow, UUID(res_a["change_request_id"]))
                pref_after = await db.scalar(
                    select(UserPreferenceRow).where(UserPreferenceRow.user_id == uid_a)
                )
                assert sugg_after is not None and sugg_after.status == "accepted" and sugg_after.revision == 2
                assert old_after is not None and old_after.status == "superseded" and old_after.revision == 2
                assert cr_after is not None and cr_after.application_state == "applied"
                assert pref_after is not None and pref_after.buffer_multiplier == 1.45

        # Nhánh B: chỉ có 1 pending row in-flight -> GET /suggestions/current trả None (0 duplicate), sau cancel/reject/supersede trả lại đúng row gốc & giữ cooldown
        async with sf() as db:
            uid_b = await _seed_user(db, now=fixed_now, cooldown_until=cooldown_ts)
            await _seed_task_with_future_session(db, uid_b, now=fixed_now)
            sugg_b_id = await _seed_suggestion(db, uid_b, now=fixed_now, suggested_value=1.55)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1) Accept -> pending_review -> GET /suggestions/current == None -> Cancel -> phục hồi đúng sugg_b_id
            r_b1 = await client.post(
                f"/api/v1/suggestions/{sugg_b_id}/response",
                headers=_auth_headers(uid_b, "uf7-02-b-1"),
                json={"expected_suggestion_revision": 1, "action": "accept"},
            )
            assert r_b1.status_code == 200
            cr_b1 = r_b1.json()["data"]["change_request_id"]

            r_cur_none = await client.get("/api/v1/suggestions/current", headers=_auth_headers(uid_b))
            assert r_cur_none.status_code == 200
            assert r_cur_none.json()["data"] is None

            r_b_cancel = await client.post(
                f"/api/v1/settings/change-requests/{cr_b1}/cancel",
                headers=_auth_headers(uid_b, "uf7-02-b-cancel"),
                json={"expected_request_revision": 1},
            )
            assert r_b_cancel.status_code == 200

            r_cur_restored = await client.get("/api/v1/suggestions/current", headers=_auth_headers(uid_b))
            assert r_cur_restored.status_code == 200
            assert r_cur_restored.json()["data"]["suggestion_id"] == str(sugg_b_id)

            # 2) Accept lần 2 -> Reject proposal trên UF6 -> phục hồi đúng sugg_b_id & bảo toàn cooldown
            r_b2 = await client.post(
                f"/api/v1/suggestions/{sugg_b_id}/response",
                headers=_auth_headers(uid_b, "uf7-02-b-2"),
                json={"expected_suggestion_revision": 1, "action": "accept"},
            )
            assert r_b2.status_code == 200
            prop_b2 = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid_b))).json()["data"]
            r_b_rej = await client.post(
                f"/api/v1/proposals/{prop_b2['proposal_id']}/reject",
                headers=_auth_headers(uid_b, "uf7-02-b-rej"),
                json={"expected_proposal_revision": prop_b2["proposal_revision"]},
            )
            assert r_b_rej.status_code == 200
            r_cur_after_rej = await client.get("/api/v1/suggestions/current", headers=_auth_headers(uid_b))
            assert r_cur_after_rej.json()["data"]["suggestion_id"] == str(sugg_b_id)

            # 3) Accept lần 3 -> Supersede bởi settings change mới -> suggestion vẫn pending & hiện lại sạch
            r_b3 = await client.post(
                f"/api/v1/suggestions/{sugg_b_id}/response",
                headers=_auth_headers(uid_b, "uf7-02-b-3"),
                json={"expected_suggestion_revision": 1, "action": "accept"},
            )
            assert r_b3.status_code == 200
            cr_b3 = r_b3.json()["data"]["change_request_id"]

            r_sup = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_b, "uf7-02-b-sup"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"work_interval_seconds": 1800}},
                },
            )
            assert r_sup.status_code == 200

            async with sf() as db:
                cnt_sugg = await db.scalar(
                    select(func.count())
                    .select_from(AdaptationSuggestion)
                    .where(AdaptationSuggestion.user_id == uid_b)
                )
                assert cnt_sugg == 1
                cr_b3_row = await db.get(SettingChangeRow, UUID(cr_b3))
                assert cr_b3_row is not None and cr_b3_row.application_state == "cancelled"
                pref_b_row = await db.scalar(
                    select(UserPreferenceRow).where(UserPreferenceRow.user_id == uid_b)
                )
                assert pref_b_row is not None
                assert settings_service._as_utc(pref_b_row.suggestion_cooldown_until) == cooldown_ts

            r_cur_after_sup = await client.get("/api/v1/suggestions/current", headers=_auth_headers(uid_b))
            assert r_cur_after_sup.json()["data"]["suggestion_id"] == str(sugg_b_id)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_be_uf7_03_sync_active_steps_buffer_multiplier_all_three_paths(monkeypatch):
    """BE-UF7-03: Đồng bộ StepRow.buffer_multiplier qua approve_proposal (kể cả về 1.0) và 2 nhánh immediate, giữ nguyên step terminal/archived/active."""
    engine, sf = await _make_env()
    fixed_now = datetime(2026, 11, 2, 9, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        # 1) Qua approve_proposal (1.2 -> 1.45 -> 1.0)
        async with sf() as db:
            uid_1 = await _seed_user(db, now=fixed_now, buffer_multiplier=1.2)
            seeded = await _seed_task_with_future_session(
                db, uid_1, now=fixed_now, buffer_multiplier=1.2, step_status="scheduled"
            )
            active_task_id = seeded["task_id"]
            st_sched_id = seeded["step_id"]

            st_pending_id = uuid4()
            st_completed_id = uuid4()
            st_skipped_id = uuid4()
            st_archived_id = uuid4()
            st_inprog_sess_id = uuid4()
            st_await_run_id = uuid4()
            db.add_all(
                [
                    StepRow(
                        id=st_pending_id,
                        user_id=uid_1,
                        task_id=active_task_id,
                        revision=1,
                        title="Bước pending horizon sau",
                        order_index=1,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="pending",
                    ),
                    StepRow(
                        id=st_completed_id,
                        user_id=uid_1,
                        task_id=active_task_id,
                        revision=1,
                        title="Bước completed",
                        order_index=2,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=0,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="completed",
                    ),
                    StepRow(
                        id=st_skipped_id,
                        user_id=uid_1,
                        task_id=active_task_id,
                        revision=1,
                        title="Bước skipped",
                        order_index=3,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="skipped",
                    ),
                    StepRow(
                        id=st_archived_id,
                        user_id=uid_1,
                        task_id=active_task_id,
                        revision=1,
                        title="Bước archived",
                        order_index=4,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="archived",
                    ),
                    StepRow(
                        id=st_inprog_sess_id,
                        user_id=uid_1,
                        task_id=active_task_id,
                        revision=1,
                        title="Bước có session in_progress",
                        order_index=5,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="in_progress",
                    ),
                    StepRow(
                        id=st_await_run_id,
                        user_id=uid_1,
                        task_id=active_task_id,
                        revision=1,
                        title="Bước có run awaiting_checkin",
                        order_index=6,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="in_progress",
                    ),
                ]
            )
            await db.flush()

            # Session cho step pending để test planning state hoặc tách thành task riêng cho sạch
            sess_inprog = SessionRow(
                id=uuid4(),
                user_id=uid_1,
                step_id=st_inprog_sess_id,
                revision=1,
                scheduled_start=fixed_now - timedelta(minutes=15),
                scheduled_end=fixed_now + timedelta(minutes=15),
                micro_task_text="Đang làm",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=1,
                status="in_progress",
                start_state="started",
                start_decision_due_at=fixed_now - timedelta(minutes=10),
            )
            sess_await_id = uuid4()
            sess_await = SessionRow(
                id=sess_await_id,
                user_id=uid_1,
                step_id=st_await_run_id,
                revision=1,
                scheduled_start=fixed_now - timedelta(minutes=40),
                scheduled_end=fixed_now - timedelta(minutes=10),
                micro_task_text="Chờ check-in",
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                duration_seconds=1500,
                schedule_revision=1,
                status="completed",
                start_state="started",
                start_decision_due_at=fixed_now - timedelta(minutes=35),
            )
            db.add_all([sess_inprog, sess_await])
            await db.flush()
            db.add(
                SessionRun(
                    id=uuid4(),
                    user_id=uid_1,
                    session_id=sess_await_id,
                    device_id="dev-await",
                    started_at=fixed_now - timedelta(minutes=40),
                    run_state="awaiting_checkin",
                    active_phase="checkin",
                    segment_index=0,
                    work_elapsed_seconds=1500,
                    rest_elapsed_seconds=0,
                    outcome="unknown",
                    settings_snapshot={"effective_revision": 1},
                )
            )

            # Task completed & Task archived không được đụng
            done_task_id = uuid4()
            arch_task_id = uuid4()
            st_done_task_id = uuid4()
            st_arch_task_id = uuid4()
            db.add_all(
                [
                    TaskRow(
                        id=done_task_id,
                        user_id=uid_1,
                        revision=1,
                        title="Task đã xong",
                        time_constraint={"mode": "deadline", "at": (fixed_now + timedelta(days=2)).isoformat(), "timezone": "Asia/Ho_Chi_Minh"},
                        status="completed",
                        decomposition_state="complete",
                    ),
                    TaskRow(
                        id=arch_task_id,
                        user_id=uid_1,
                        revision=1,
                        title="Task lưu trữ",
                        time_constraint={"mode": "deadline", "at": (fixed_now + timedelta(days=2)).isoformat(), "timezone": "Asia/Ho_Chi_Minh"},
                        status="archived",
                        decomposition_state="complete",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    StepRow(
                        id=st_done_task_id,
                        user_id=uid_1,
                        task_id=done_task_id,
                        revision=1,
                        title="Step task completed",
                        order_index=0,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="pending",
                    ),
                    StepRow(
                        id=st_arch_task_id,
                        user_id=uid_1,
                        task_id=arch_task_id,
                        revision=1,
                        title="Step task archived",
                        order_index=0,
                        estimated_base_seconds=1200,
                        remaining_base_seconds=1200,
                        buffer_multiplier=1.2,
                        estimate_source="ai",
                        status="pending",
                    ),
                ]
            )

            # Thêm 1 task thuần có 1 step scheduled để kiểm tra get_task_planning_states == "complete"
            clean_task_id = uuid4()
            clean_step_id = uuid4()
            db.add(
                TaskRow(
                    id=clean_task_id,
                    user_id=uid_1,
                    revision=1,
                    title="Task sạch để kiểm tra planning state",
                    time_constraint={"mode": "deadline", "at": (fixed_now + timedelta(days=5)).isoformat(), "timezone": "Asia/Ho_Chi_Minh"},
                    status="scheduled",
                    decomposition_state="complete",
                )
            )
            await db.flush()
            db.add(
                StepRow(
                    id=clean_step_id,
                    user_id=uid_1,
                    task_id=clean_task_id,
                    revision=1,
                    title="Clean step",
                    order_index=0,
                    estimated_base_seconds=1500,
                    remaining_base_seconds=1500,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="scheduled",
                )
            )
            await db.flush()
            c_start = fixed_now + timedelta(hours=6)
            db.add(
                SessionRow(
                    id=uuid4(),
                    user_id=uid_1,
                    step_id=clean_step_id,
                    revision=1,
                    scheduled_start=c_start,
                    scheduled_end=c_start + timedelta(seconds=1800),
                    micro_task_text="Bắt đầu",
                    micro_task_seconds=120,
                    work_seconds=1800,
                    rest_seconds=0,
                    segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1800}],
                    duration_seconds=1800,
                    schedule_revision=1,
                    status="scheduled",
                    start_state="waiting",
                    start_decision_due_at=c_start + timedelta(minutes=5),
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Đổi 1.2 -> 1.45
            r_chg1 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_1, "uf7-03-chg1"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"buffer_multiplier": 1.45}},
                },
            )
            assert r_chg1.status_code == 200
            prop1 = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid_1))).json()["data"]
            r_app1 = await client.post(
                f"/api/v1/proposals/{prop1['proposal_id']}/approve",
                headers=_auth_headers(uid_1, "uf7-03-app1"),
                json={
                    "expected_proposal_revision": prop1["proposal_revision"],
                    "expected_base_schedule_revision": prop1["base_schedule_revision"],
                    "expected_source_revisions": prop1["source_revisions"],
                },
            )
            assert r_app1.status_code == 200

            async with sf() as db:
                for sid in (st_sched_id, st_pending_id, clean_step_id):
                    row = await db.get(StepRow, sid)
                    assert row is not None
                    assert row.buffer_multiplier == 1.45
                    assert row.revision == 2
                for sid in (
                    st_completed_id,
                    st_skipped_id,
                    st_archived_id,
                    st_inprog_sess_id,
                    st_await_run_id,
                    st_done_task_id,
                    st_arch_task_id,
                ):
                    row = await db.get(StepRow, sid)
                    assert row is not None
                    assert row.buffer_multiplier == 1.2
                    assert row.revision == 1
                p_states = await task_state_service.get_task_planning_states(db, uid_1, [clean_task_id])
                assert p_states[clean_task_id].scheduling_state == "complete"

            # Đổi tiếp 1.45 -> 1.0 (kiểm tra biên thấp 1.0 không bị falsy)
            r_chg2 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_1, "uf7-03-chg2"),
                json={
                    "expected_preferences_revision": 2,
                    "changes": {"preferences": {"buffer_multiplier": 1.0}},
                },
            )
            assert r_chg2.status_code == 200
            prop2 = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid_1))).json()["data"]
            r_app2 = await client.post(
                f"/api/v1/proposals/{prop2['proposal_id']}/approve",
                headers=_auth_headers(uid_1, "uf7-03-app2"),
                json={
                    "expected_proposal_revision": prop2["proposal_revision"],
                    "expected_base_schedule_revision": prop2["base_schedule_revision"],
                    "expected_source_revisions": prop2["source_revisions"],
                },
            )
            assert r_app2.status_code == 200
            async with sf() as db:
                for sid in (st_sched_id, st_pending_id, clean_step_id):
                    row = await db.get(StepRow, sid)
                    assert row is not None and row.buffer_multiplier == 1.0 and row.revision == 3

        # 2) Nhánh immediate qua POST /settings/change-requests (chỉ có step pending, không có phiên scheduled)
        async with sf() as db:
            uid_2 = await _seed_user(db, now=fixed_now, buffer_multiplier=1.2)
            s2 = await _seed_task_with_future_session(
                db, uid_2, now=fixed_now, with_scheduled_session=False, buffer_multiplier=1.2
            )
            sugg_2 = await _seed_suggestion(db, uid_2, now=fixed_now, suggested_value=1.5)
            st2_arch_id = uuid4()
            db.add(
                StepRow(
                    id=st2_arch_id,
                    user_id=uid_2,
                    task_id=s2["task_id"],
                    revision=1,
                    title="Step archived",
                    order_index=1,
                    estimated_base_seconds=900,
                    remaining_base_seconds=900,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="archived",
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_imm_cr = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_2, "uf7-03-imm-cr"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {"preferences": {"buffer_multiplier": 1.35}},
                },
            )
            assert r_imm_cr.status_code == 200
            assert r_imm_cr.json()["data"]["application_state"] == "effective_from_next_session"
            async with sf() as db:
                st2_row = await db.get(StepRow, s2["step_id"])
                st2_arch_row = await db.get(StepRow, st2_arch_id)
                sugg2_row = await db.get(AdaptationSuggestion, sugg_2)
                assert st2_row is not None and st2_row.buffer_multiplier == 1.35 and st2_row.revision == 2
                assert st2_arch_row is not None and st2_arch_row.buffer_multiplier == 1.2
                assert sugg2_row is not None and sugg2_row.status == "superseded"

        # 3) Nhánh immediate qua POST /suggestions/{id}/response (không có phiên scheduled)
        async with sf() as db:
            uid_3 = await _seed_user(db, now=fixed_now, buffer_multiplier=1.2)
            s3 = await _seed_task_with_future_session(
                db, uid_3, now=fixed_now, with_scheduled_session=False, buffer_multiplier=1.2
            )
            sugg_3 = await _seed_suggestion(db, uid_3, now=fixed_now, suggested_value=1.55)
            st3_comp_id = uuid4()
            db.add(
                StepRow(
                    id=st3_comp_id,
                    user_id=uid_3,
                    task_id=s3["task_id"],
                    revision=1,
                    title="Step completed",
                    order_index=1,
                    estimated_base_seconds=900,
                    remaining_base_seconds=0,
                    buffer_multiplier=1.2,
                    estimate_source="ai",
                    status="completed",
                )
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_imm_sugg = await client.post(
                f"/api/v1/suggestions/{sugg_3}/response",
                headers=_auth_headers(uid_3, "uf7-03-imm-sugg"),
                json={
                    "expected_suggestion_revision": 1,
                    "expected_preference_revision": 1,
                    "action": "accept",
                },
            )
            assert r_imm_sugg.status_code == 200
            assert r_imm_sugg.json()["data"]["application_state"] == "effective_from_next_session"
            async with sf() as db:
                st3_row = await db.get(StepRow, s3["step_id"])
                st3_comp = await db.get(StepRow, st3_comp_id)
                assert st3_row is not None and st3_row.buffer_multiplier == 1.55 and st3_row.revision == 2
                assert st3_comp is not None and st3_comp.buffer_multiplier == 1.2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_be_uf7_04_reminder_style_validation_active_run_isolation_and_cancel_resubmit(
    monkeypatch,
):
    """BE-UF7-04: Chuẩn hóa reminder_style, cách ly phiên đang chạy, Cancel->Resubmit idempotency và negative guards."""
    engine, sf = await _make_env()
    fixed_now = datetime(2026, 11, 2, 9, 0, 0, tzinfo=TZ_VN)
    monkeypatch.setattr(reschedule_service, "_current_utc_now", lambda now=None: now or fixed_now)
    monkeypatch.setattr(settings_service, "_utc_now", lambda now=None: now or fixed_now)

    try:
        # 1) Validation reminder_style (canonical + legacy + strip vs invalid 422)
        async with sf() as db:
            uid_val = await _seed_user(db, now=fixed_now)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            rev = 1
            for idx, valid_style in enumerate(
                ("  upbeat  ", "encouraging", "gentle", "firm", "minimal", "gentle_sound", "balanced", "default")
            ):
                r_ok = await client.post(
                    "/api/v1/settings/change-requests",
                    headers=_auth_headers(uid_val, f"uf7-04-style-ok-{idx}"),
                    json={
                        "expected_preferences_revision": rev,
                        "changes": {"preferences": {"reminder_style": valid_style}},
                    },
                )
                assert r_ok.status_code == 200
                rev = r_ok.json()["data"]["effective_revision"]
                p_cur = (await client.get("/api/v1/preferences", headers=_auth_headers(uid_val))).json()["data"]
                assert p_cur["reminder_style"] == valid_style.strip()

            for idx, bad_style in enumerate(("", "   ", True, "x" * 33, "aggressive_loud")):
                r_bad = await client.post(
                    "/api/v1/settings/change-requests",
                    headers=_auth_headers(uid_val, f"uf7-04-style-bad-{idx}"),
                    json={
                        "expected_preferences_revision": rev,
                        "changes": {"preferences": {"reminder_style": bad_style}},
                    },
                )
                assert r_bad.status_code == 422
                assert r_bad.json()["error"]["code"] == "VALIDATION_ERROR"

        # 2) Active-Run Isolation + Cancel -> Resubmit + Negative Guards
        async with sf() as db:
            uid_iso = await _seed_user(db, now=fixed_now)
            seeded = await _seed_task_with_future_session(
                db, uid_iso, now=fixed_now, with_scheduled_session=True, with_active_run=True
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 2a) Đổi reminder_style/offsets khi run đang chạy -> effective_from_next_session, giữ nguyên snapshot & dựng lại Reminder tương lai
            r_nonsched = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_iso, "uf7-04-nonsched"),
                json={
                    "expected_preferences_revision": 1,
                    "changes": {
                        "preferences": {
                            "reminder_style": "encouraging",
                            "reminder_offsets": [600, 300],
                        }
                    },
                },
            )
            assert r_nonsched.status_code == 200
            cr_applied_id = r_nonsched.json()["data"]["change_request_id"]
            assert r_nonsched.json()["data"]["application_state"] == "effective_from_next_session"

            async with sf() as db:
                run_row = await db.get(SessionRun, seeded["run_id"])
                assert run_row is not None
                assert run_row.settings_snapshot["reminder_style"] == "gentle"
                assert run_row.settings_snapshot["reminder_offsets"] == [900, 600, 300]
                rems = (
                    await db.scalars(
                        select(Reminder).where(
                            Reminder.session_id == seeded["session_id"],
                            Reminder.delivery_state == "scheduled",
                        )
                    )
                ).all()
                assert len(rems) == 2

            # Negative: cancel request đã applied -> 409 SETTINGS_CHANGE_NOT_CANCELLABLE
            r_cancel_applied = await client.post(
                f"/api/v1/settings/change-requests/{cr_applied_id}/cancel",
                headers=_auth_headers(uid_iso, "uf7-04-cancel-applied"),
                json={"expected_request_revision": 1},
            )
            assert r_cancel_applied.status_code == 409
            assert r_cancel_applied.json()["error"]["code"] == "SETTINGS_CHANGE_NOT_CANCELLABLE"

            # 2b) Cancel -> Resubmit Idempotency-Key + Negative guards
            payload_cr1 = {
                "expected_preferences_revision": 2,
                "changes": {"preferences": {"buffer_multiplier": 1.4}},
            }
            r_cr1 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_iso, "key-resubmit-1"),
                json=payload_cr1,
            )
            assert r_cr1.status_code == 200
            cr1_id = r_cr1.json()["data"]["change_request_id"]

            p_before_cancel = (
                await client.get("/api/v1/preferences", headers=_auth_headers(uid_iso))
            ).json()["data"]
            assert p_before_cancel["pending_change_request_id"] == cr1_id
            assert p_before_cancel["pending_change_request_revision"] == 1

            # Negative: sai expected_request_revision -> 409 REVISION_CONFLICT
            r_bad_rev = await client.post(
                f"/api/v1/settings/change-requests/{cr1_id}/cancel",
                headers=_auth_headers(uid_iso, "uf7-04-bad-rev"),
                json={"expected_request_revision": 99},
            )
            assert r_bad_rev.status_code == 409
            assert r_bad_rev.json()["error"]["code"] == "REVISION_CONFLICT"

            # Cancel đúng revision -> preferences về None/None
            r_cancel_1 = await client.post(
                f"/api/v1/settings/change-requests/{cr1_id}/cancel",
                headers=_auth_headers(uid_iso, "uf7-04-cancel-1"),
                json={"expected_request_revision": 1},
            )
            assert r_cancel_1.status_code == 200

            # Cancel lại request đã cancelled -> 409 SETTINGS_CHANGE_NOT_CANCELLABLE
            r_cancel_again = await client.post(
                f"/api/v1/settings/change-requests/{cr1_id}/cancel",
                headers=_auth_headers(uid_iso, "uf7-04-cancel-2"),
                json={"expected_request_revision": 2},
            )
            assert r_cancel_again.status_code == 409
            assert r_cancel_again.json()["error"]["code"] == "SETTINGS_CHANGE_NOT_CANCELLABLE"

            # Replay cùng key-resubmit-1 + cùng payload -> replay receipt cr1 (DB vẫn cancelled, GET /preferences vẫn None)
            r_replay = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_iso, "key-resubmit-1"),
                json=payload_cr1,
            )
            assert r_replay.status_code == 200
            assert r_replay.json()["data"]["change_request_id"] == cr1_id
            p_after_replay = (
                await client.get("/api/v1/preferences", headers=_auth_headers(uid_iso))
            ).json()["data"]
            assert p_after_replay["pending_change_request_id"] is None

            # Cùng key-resubmit-1 + payload khác -> 409 IDEMPOTENCY_KEY_REUSED
            r_reused = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_iso, "key-resubmit-1"),
                json={
                    "expected_preferences_revision": 2,
                    "changes": {"preferences": {"buffer_multiplier": 1.6}},
                },
            )
            assert r_reused.status_code == 409
            assert r_reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

            # Key mới key-resubmit-2 -> tạo cr2 mới (work_interval_seconds + buffer_multiplier)
            r_cr2 = await client.post(
                "/api/v1/settings/change-requests",
                headers=_auth_headers(uid_iso, "key-resubmit-2"),
                json={
                    "expected_preferences_revision": 2,
                    "changes": {
                        "preferences": {
                            "work_interval_seconds": 1800,
                            "buffer_multiplier": 1.5,
                        }
                    },
                },
            )
            assert r_cr2.status_code == 200
            cr2_id = r_cr2.json()["data"]["change_request_id"]
            assert cr2_id != cr1_id
            p_cr2 = (await client.get("/api/v1/preferences", headers=_auth_headers(uid_iso))).json()["data"]
            assert p_cr2["pending_change_request_id"] == cr2_id
            assert p_cr2["buffer_multiplier"] == 1.2
            assert p_cr2["work_interval_seconds"] == 1500

            # 2c) approve_proposal khi SessionRun đang running -> 409 ACTIVE_RUN_EXISTS
            prop_iso = (await client.get("/api/v1/proposals/current", headers=_auth_headers(uid_iso))).json()["data"]
            r_app_blocked = await client.post(
                f"/api/v1/proposals/{prop_iso['proposal_id']}/approve",
                headers=_auth_headers(uid_iso, "uf7-04-app-blocked"),
                json={
                    "expected_proposal_revision": prop_iso["proposal_revision"],
                    "expected_base_schedule_revision": prop_iso["base_schedule_revision"],
                    "expected_source_revisions": prop_iso["source_revisions"],
                },
            )
            assert r_app_blocked.status_code == 409
            assert r_app_blocked.json()["error"]["code"] == "ACTIVE_RUN_EXISTS"

            # Kết thúc run -> approve_proposal thành công 200 và đồng bộ StepRow.buffer_multiplier
            async with sf() as db:
                run_row = await db.get(SessionRun, seeded["run_id"])
                assert run_row is not None
                run_row.run_state = "closed"
                run_row.ended_at = fixed_now
                run_row.outcome = "completed"
                run_row.revision += 1
                run_sess = await db.get(SessionRow, run_row.session_id)
                if run_sess is not None:
                    run_sess.status = "completed"
                    run_sess.revision += 1
                await db.commit()

            r_app_ok = await client.post(
                f"/api/v1/proposals/{prop_iso['proposal_id']}/approve",
                headers=_auth_headers(uid_iso, "uf7-04-app-ok"),
                json={
                    "expected_proposal_revision": prop_iso["proposal_revision"],
                    "expected_base_schedule_revision": prop_iso["base_schedule_revision"],
                    "expected_source_revisions": prop_iso["source_revisions"],
                },
            )
            assert r_app_ok.status_code == 200
            async with sf() as db:
                st_after = await db.get(StepRow, seeded["step_id"])
                run_after = await db.get(SessionRun, seeded["run_id"])
                assert st_after is not None and st_after.buffer_multiplier == 1.5
                assert run_after is not None and run_after.settings_snapshot["buffer_multiplier"] == 1.2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
