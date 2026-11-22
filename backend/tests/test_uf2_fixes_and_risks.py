"""Regression & risk verification tests for UF2 repair items 2.2, 2.5, and 2.6."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_db
from app.core.config import Settings
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import (
    Base,
    CalendarConnection,
    CalendarEventLink,
    Reminder,
)
from app.models.planning import PlanCandidate as CandidateRow
from app.models.session import Session as SessionRow
from app.models.step import Step as StepRow
from app.models.user import UserPreference as PreferenceRow
from app.schemas.availability import DateOverride, DraftAvailability, LocalInterval
from app.schemas.calendar import CalendarSource
from app.schemas.common import SessionSegment, TimeConstraint, TimeInterval
from app.schemas.planning import CandidateStep, PlanCandidate
from app.schemas.settings import UserPreference
from app.schemas.task import (
    LLMPlanAdjustmentPayload,
    LLMTaskDecompositionPayload,
    Session,
    StepPromptItem,
    TaskDecompositionResult,
)
from app.services import draft_service, plan_service, reschedule_service, run_service
from app.services.ai_service import AIService, ai_service
from app.services.draft_service import resolve_user_uuid
from app.services.planning.availability import compute_effective_free_intervals
from app.services.planning.calendar_ingestion import ingest_calendar_sources
from app.services.planning.durations import fit_session_to_slot
from app.services.planning.scheduling import (
    parse_extra_requirements,
    schedule_candidate_sessions,
    subtract_fixed_session_gaps,
)


@pytest.fixture
async def api(monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    async def unavailable(**kwargs):
        raise ConnectionError("Providers unavailable in regression test")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", unavailable)
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def _base_summary(
    *,
    start_at: datetime | None = None,
    deadline_at: datetime | None = None,
    weekly_rules: list[dict] | None = None,
    date_overrides: list[dict] | None = None,
    extra_requirements: list[str] | None = None,
    timezone_name: str = "Asia/Ho_Chi_Minh",
) -> dict:
    now = datetime.now(UTC)
    s_at = start_at or (now - timedelta(hours=1))
    d_at = deadline_at or (now + timedelta(days=5))
    avail: dict = {
        "timezone": timezone_name,
        "weekly_rules": weekly_rules
        if weekly_rules is not None
        else [{"weekday": i, "start_local": "00:00", "end_local": "23:59"} for i in range(1, 8)],
    }
    if date_overrides is not None:
        avail["date_overrides"] = date_overrides
    data: dict = {
        "title": "Kế hoạch UF2 kiểm chứng",
        "description": "Thực hiện dự án theo từng bước rõ ràng",
        "scope": "Hoàn thành toàn bộ phạm vi",
        "time_constraint": {
            "kind": "deadline",
            "start_at": s_at.isoformat(),
            "deadline_at": d_at.isoformat(),
            "input_timezone": timezone_name,
        },
        "availability": avail,
    }
    if extra_requirements is not None:
        data["extra_requirements"] = extra_requirements
    return data


async def _post(client: AsyncClient, path: str, key: str, payload: dict):
    return await client.post("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _patch(client: AsyncClient, path: str, key: str, payload: dict):
    return await client.patch("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _confirmed_draft(
    client: AsyncClient,
    key: str = "uf2-draft",
    *,
    task_summary: dict | None = None,
    user_step_proposal: str | None = None,
) -> dict:
    body: dict = {
        "client_draft_id": key,
        "input_source": "text",
        "summary": task_summary or _base_summary(),
    }
    if user_step_proposal is not None:
        body["user_step_proposal"] = user_step_proposal
    resp = await _post(client, "/drafts", key, body)
    assert resp.status_code == 201, resp.text
    draft = resp.json()["data"]
    conf = await _post(
        client,
        f"/drafts/{draft['draft_id']}/confirm-summary",
        f"{key}-conf",
        {"expected_summary_revision": draft["summary_revision"]},
    )
    assert conf.status_code == 200, conf.text
    return conf.json()["data"]


async def _create_candidate_api(
    client: AsyncClient,
    draft: dict,
    key: str = "uf2-cand",
    *,
    manual_steps: list[dict] | None = None,
    selected_step_keys: list[str] | None = None,
):
    body: dict = {
        "source_summary_revision": draft["summary_revision"],
        "source_planning_input_revision": draft["planning_input_revision"],
    }
    if manual_steps is not None:
        body["manual_steps"] = manual_steps
    if selected_step_keys is not None:
        body["selected_step_keys"] = selected_step_keys
    return await _post(client, f"/drafts/{draft['draft_id']}/candidates", key, body)


async def _confirm_candidate_api(client: AsyncClient, plan: dict, key: str = "uf2-confirm"):
    return await _post(
        client,
        f"/candidates/{plan['candidate_id']}/confirm",
        key,
        {
            "expected_review_revision": plan["review_revision"],
            "expected_summary_revision": plan["source_summary_revision"],
            "expected_planning_input_revision": plan["source_planning_input_revision"],
            "expected_source_revisions": plan["source_revisions"],
        },
    )


# ---------------------------------------------------------------------------
# 2.2 RG03: rest_interval_seconds == 0 & negative controls
# ---------------------------------------------------------------------------


async def test_rg03_rest_zero_consecutive_work_segments_confirm_201_and_negative_controls(
    api,
):
    client, factory = api
    pref_get = await client.get("/api/v1/preferences")
    assert pref_get.status_code == 200, pref_get.text
    pref_rev = pref_get.json()["data"]["revision"]
    pref_resp = await _post(
        client,
        "/settings/change-requests",
        "pref-rest0",
        {
            "expected_preferences_revision": pref_rev,
            "changes": {
                "preferences": {
                    "work_interval_seconds": 1500,
                    "rest_interval_seconds": 0,
                    "buffer_multiplier": 1.2,
                }
            },
        },
    )
    assert pref_resp.status_code == 200, pref_resp.text

    draft = await _confirmed_draft(client, "rg03-draft")
    cand_resp = await _create_candidate_api(
        client,
        draft,
        "rg03-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Hoàn thành module chính",
                "expected_output": "Module chạy qua test",
                "estimated_base_seconds": 3600,
            }
        ],
    )
    assert cand_resp.status_code == 201, cand_resp.text
    plan = cand_resp.json()["data"]
    assert plan["feasibility"]["status"] == "feasible"
    assert len(plan["sessions"]) == 1
    segs = plan["sessions"][0]["segments"]
    assert [(s["kind"], s["duration_seconds"]) for s in segs] == [
        ("work", 1500),
        ("work", 1500),
        ("work", 1320),
    ]
    assert plan["sessions"][0]["work_seconds"] == 4320
    assert plan["sessions"][0]["rest_seconds"] == 0

    confirm_resp = await _confirm_candidate_api(client, plan, "rg03-confirm")
    assert confirm_resp.status_code == 201, confirm_resp.text

    # Negative controls:
    # (1) rest=0 with a "rest" segment -> INVALID_SEGMENTS & 409 on confirm
    # (2) rest=0 with work > work_interval_seconds -> INVALID_SEGMENTS & 409 on confirm
    # (3) rest=300 with [work, work] -> INVALID_SEGMENTS & 409 on confirm
    draft2 = await _confirmed_draft(client, "rg03-neg-draft")
    cand2_resp = await _create_candidate_api(
        client,
        draft2,
        "rg03-neg-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Bước kiểm soát âm",
                "expected_output": "Bị từ chối 409",
                "estimated_base_seconds": 3600,
            }
        ],
    )
    assert cand2_resp.status_code == 201
    plan2 = cand2_resp.json()["data"]

    async def _assert_invalid_segments(new_session: dict, key: str) -> None:
        async with factory() as db:
            row = await db.get(CandidateRow, UUID(plan2["candidate_id"]))
            assert row is not None
            row.sessions = [new_session]
            await db.commit()
            _, d_schema, c_schema = await plan_service._read_candidate(
                db, "user_a", plan2["candidate_id"]
            )
            src = await plan_service._sources(
                db, "user_a", d_schema, c_schema.planning_horizon, read_only=True
            )
            issues = plan_service._concrete_issues(c_schema, src)
            assert any(item.code == "INVALID_SEGMENTS" for item in issues)
        resp = await _confirm_candidate_api(client, plan2, key)
        assert resp.status_code == 409

    base_sess = plan2["sessions"][0]
    await _assert_invalid_segments(
        {
            **base_sess,
            "work_seconds": 4020,
            "rest_seconds": 300,
            "duration_seconds": 4320,
            "segments": [
                {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                {"order_index": 2, "kind": "work", "duration_seconds": 1500},
                {"order_index": 3, "kind": "work", "duration_seconds": 1020},
            ],
        },
        "rg03-neg-1",
    )
    await _assert_invalid_segments(
        {
            **base_sess,
            "work_seconds": 4320,
            "rest_seconds": 0,
            "duration_seconds": 4320,
            "segments": [
                {"order_index": 0, "kind": "work", "duration_seconds": 1800},
                {"order_index": 1, "kind": "work", "duration_seconds": 1500},
                {"order_index": 2, "kind": "work", "duration_seconds": 1020},
            ],
        },
        "rg03-neg-2",
    )

    async with factory() as db:
        pref = await db.scalar(select(PreferenceRow))
        assert pref is not None
        pref.rest_interval_seconds = 300
        await db.commit()

    await _assert_invalid_segments(
        {
            **base_sess,
            "work_seconds": 4320,
            "rest_seconds": 0,
            "duration_seconds": 4320,
            "segments": [
                {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                {"order_index": 1, "kind": "work", "duration_seconds": 1500},
                {"order_index": 2, "kind": "work", "duration_seconds": 1320},
            ],
        },
        "rg03-neg-3",
    )


# ---------------------------------------------------------------------------
# 2.5 Bug 2.3: CandidateStep(remaining_base_seconds=0)
# ---------------------------------------------------------------------------


async def test_bug_2_3_remaining_base_seconds_zero_does_not_raise_value_error(api):
    client, factory = api
    draft = await _confirmed_draft(client, "bug23-draft")
    cand_resp = await _create_candidate_api(
        client,
        draft,
        "bug23-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Bước 1",
                "expected_output": "Xong 1",
                "estimated_base_seconds": 1500,
            }
        ],
    )
    assert cand_resp.status_code == 201
    cand = PlanCandidate.model_validate(cand_resp.json()["data"])
    zero_step = cand.steps[0].model_copy(
        update={"client_step_key": "step-0", "order_index": 0, "remaining_base_seconds": 0}
    )
    step1 = cand.steps[0].model_copy(update={"order_index": 1})
    cand_with_zero = cand.model_copy(
        update={
            "steps": [zero_step, step1],
            "feasibility": cand.feasibility.model_copy(
                update={
                    "verification_scope": cand.feasibility.verification_scope.model_copy(
                        update={"client_step_keys": ["step-0", "step-1"]}
                    )
                }
            ),
        }
    )
    async with factory() as db:
        _, parsed_draft, _ = await plan_service._read_candidate(db, "user_a", cand.candidate_id)
        source = await plan_service._sources(
            db, "user_a", parsed_draft, cand.planning_horizon, read_only=True
        )
    # Must NOT raise ValueError("estimated_base_seconds phải là số nguyên dương (> 0).")
    issues = plan_service._concrete_issues(cand_with_zero, source)
    assert issues == []
    reviewed = plan_service._review_feasibility(cand_with_zero, source)
    assert reviewed.feasibility.status == "feasible"
    assert reviewed.feasibility.fully_scheduled is True


# ---------------------------------------------------------------------------
# 2.5 Bug 2.1 + RISK-UF2-05: skipped/cancelled vs completed/incomplete + Google Calendar
# ---------------------------------------------------------------------------


async def test_bug_2_1_skipped_and_cancelled_do_not_block_while_completed_and_incomplete_keep_900s_gap(
    api,
):
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    day_start = now + timedelta(days=2, hours=1)
    # Free window: 4 hours on day_start (in UTC so exact slot comparison is deterministic)
    if day_start.hour >= 20:
        day_start = (now + timedelta(days=3)).replace(hour=8, minute=0, second=0, microsecond=0)
    day_str = day_start.date().isoformat()
    summary = _base_summary(
        start_at=day_start - timedelta(hours=1),
        deadline_at=day_start + timedelta(hours=10),
        weekly_rules=[],
        date_overrides=[
            {
                "date": day_str,
                "intervals": [
                    {
                        "start_local": f"{day_start.hour:02d}:00",
                        "end_local": f"{(day_start + timedelta(hours=4)).hour:02d}:00",
                    }
                ],
            }
        ],
        timezone_name="UTC",
    )

    seed_draft = await _confirmed_draft(
        client,
        "bug21-seed-draft",
        task_summary=_base_summary(
            start_at=day_start - timedelta(hours=1),
            deadline_at=day_start + timedelta(days=2),
            timezone_name="UTC",
        ),
    )
    seed_cand = (
        await _create_candidate_api(
            client,
            seed_draft,
            "bug21-seed-cand",
            manual_steps=[
                {
                    "order_index": i,
                    "title": f"Seed step {i}",
                    "expected_output": "OK",
                    "estimated_base_seconds": 1500,
                }
                for i in range(4)
            ],
        )
    ).json()["data"]
    seed_conf = await _confirm_candidate_api(client, seed_cand, "bug21-seed-conf")
    assert seed_conf.status_code == 201
    sess_ids = list(seed_conf.json()["data"]["key_mapping"]["sessions"].values())

    offsets_min = [0, 30, 90, 150]
    statuses = ["skipped", "cancelled", "completed", "incomplete"]
    async with factory() as db:
        for sid, st, off in zip(sess_ids, statuses, offsets_min, strict=True):
            srow = await db.get(SessionRow, UUID(sid))
            assert srow is not None
            srow.scheduled_start = day_start + timedelta(minutes=off)
            srow.scheduled_end = day_start + timedelta(minutes=off + 30)
            srow.work_seconds = 1800
            srow.rest_seconds = 0
            srow.duration_seconds = 1800
            srow.segments = [{"order_index": 0, "kind": "work", "duration_seconds": 1800}]
            srow.status = st
        await db.commit()

    fixed_list = [
        Session(
            session_id=str(uuid4()),
            step_id=str(uuid4()),
            scheduled_start=day_start + timedelta(minutes=off),
            scheduled_end=day_start + timedelta(minutes=off + 30),
            micro_task_text="x",
            work_seconds=1800,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=1800)],
            duration_seconds=1800,
            schedule_revision=1,
            status=st,
            start_state="waiting",
            start_decision_due_at=day_start + timedelta(minutes=off + 15),
        )
        for st, off in zip(statuses, offsets_min, strict=True)
    ]

    free_after_gaps = subtract_fixed_session_gaps(
        [TimeInterval(start_at=day_start, end_at=day_start + timedelta(hours=4))],
        fixed_list,
        day_start,
        day_start + timedelta(hours=4),
        min_gap_seconds=900,
    )
    # Skipped (0..30) & Cancelled (30..60) ignored; Completed (90..120 -> 75..135) & Incomplete (150..180 -> 135..195) kept!
    assert free_after_gaps == [
        (day_start, day_start + timedelta(minutes=75)),
        (day_start + timedelta(minutes=195), day_start + timedelta(hours=4)),
    ]

    # Verify via HTTP create_candidate + _sources DB query
    new_draft = await _confirmed_draft(client, "bug21-new-draft", task_summary=summary)
    new_cand_resp = await _create_candidate_api(
        client,
        new_draft,
        "bug21-new-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Dùng khung skipped cũ",
                "expected_output": "Xếp vào phút 0",
                "estimated_base_seconds": 1500,
            }
        ],
    )
    assert new_cand_resp.status_code == 201, new_cand_resp.text
    new_plan = new_cand_resp.json()["data"]
    assert new_plan["feasibility"]["status"] == "feasible"
    first_start = datetime.fromisoformat(
        new_plan["sessions"][0]["scheduled_start"].replace("Z", "+00:00")
    )
    assert first_start == day_start


async def test_bug_2_1_risk_uf2_05_calendar_ingestion_skipped_untouched_vs_user_moved(
    api, monkeypatch
):
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    base_start = now + timedelta(days=2)

    draft = await _confirmed_draft(
        client,
        "cal-ing-draft",
        task_summary=_base_summary(
            start_at=base_start - timedelta(hours=1),
            deadline_at=base_start + timedelta(days=2),
            timezone_name="UTC",
        ),
    )
    cand = (
        await _create_candidate_api(
            client,
            draft,
            "cal-ing-cand",
            manual_steps=[
                {
                    "order_index": i,
                    "title": f"Step {i}",
                    "expected_output": "Done",
                    "estimated_base_seconds": 1500,
                }
                for i in range(3)
            ],
        )
    ).json()["data"]
    conf = await _confirm_candidate_api(client, cand, "cal-ing-conf")
    assert conf.status_code == 201
    sess_ids = list(conf.json()["data"]["key_mapping"]["sessions"].values())
    s_prop_id, s_link_id, s_moved_id = [UUID(x) for x in sess_ids]

    conn_id = uuid4()
    cal_id = "qa-secondary@group.calendar.google.com"
    owner = resolve_user_uuid("user_a")

    async with factory() as db:
        db.add(
            CalendarConnection(
                id=conn_id,
                user_id=owner,
                provider="google",
                account_ref="qa@example.com",
                credential_ref="encrypted",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                read_calendar_ids=[cal_id],
                write_calendar_id=cal_id,
            )
        )
        await db.flush()
        for idx, sid in enumerate([s_prop_id, s_link_id, s_moved_id]):
            srow = await db.get(SessionRow, sid)
            assert srow is not None
            srow.status = "skipped"
            srow.scheduled_start = base_start + timedelta(hours=idx * 2)
            srow.scheduled_end = base_start + timedelta(hours=idx * 2, minutes=30)
            if sid == s_link_id:
                db.add(
                    CalendarEventLink(
                        user_id=owner,
                        session_id=sid,
                        connection_id=conn_id,
                        google_calendar_id=cal_id,
                        google_event_id="linked_skipped_evt",
                        desired_schedule_revision=1,
                        sync_state="synced",
                        is_current=True,
                    )
                )
        await db.commit()

    from app.services.calendar_service import CalendarService

    async def _fake_token(self, conn):
        return "fake-access-token"

    fake_events = [
        # 1. Skipped session matched via proactive_session_id, untouched (delta = 10s < 60s) -> IGNORED
        {
            "id": "gcal_prop_untouched",
            "status": "confirmed",
            "summary": "Skipped via prop",
            "start": {"dateTime": (base_start + timedelta(seconds=10)).isoformat()},
            "end": {"dateTime": (base_start + timedelta(minutes=30, seconds=10)).isoformat()},
            "extendedProperties": {"private": {"proactive_session_id": str(s_prop_id)}},
        },
        # 2. Skipped session matched via CalendarEventLink (event_to_session), untouched -> IGNORED
        {
            "id": "linked_skipped_evt",
            "status": "confirmed",
            "summary": "Skipped via event_to_session",
            "start": {"dateTime": (base_start + timedelta(hours=2)).isoformat()},
            "end": {"dateTime": (base_start + timedelta(hours=2, minutes=30)).isoformat()},
        },
        # 3. Skipped session moved by user > 60s (+15 minutes) -> KEPT as busy_interval!
        {
            "id": "gcal_moved_skipped",
            "status": "confirmed",
            "summary": "User moved skipped event",
            "start": {"dateTime": (base_start + timedelta(hours=4, minutes=15)).isoformat()},
            "end": {"dateTime": (base_start + timedelta(hours=4, minutes=45)).isoformat()},
            "extendedProperties": {"private": {"proactive_session_id": str(s_moved_id)}},
        },
    ]

    class _FakeClient:
        async def list_events(self, **kwargs):
            return fake_events

    monkeypatch.setattr(CalendarService, "get_valid_access_token", _fake_token)
    monkeypatch.setattr("app.services.calendar_service.get_calendar_client", lambda: _FakeClient())

    async with factory() as db:
        result = await ingest_calendar_sources(
            db=db,
            user_id=owner,
            selected_sources=[CalendarSource(connection_id=str(conn_id), google_calendar_id=cal_id)],
            excluded_sources=[],
            query_start=base_start - timedelta(hours=1),
            query_end=base_start + timedelta(hours=8),
        )

    assert len(result.busy_intervals) == 1
    assert result.busy_intervals[0].start_at == base_start + timedelta(hours=4, minutes=15)
    assert result.busy_intervals[0].end_at == base_start + timedelta(hours=4, minutes=45)
    assert [e.google_event_id for e in result.event_snapshots] == ["gcal_moved_skipped"]


# ---------------------------------------------------------------------------
# 2.5 Bug 4.1: infeasible candidate patched cleanly -> feasible
# ---------------------------------------------------------------------------


async def test_bug_4_1_infeasible_candidate_patched_valid_transitions_to_feasible(api):
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = now + timedelta(days=1)
    deadline_at = start_at + timedelta(days=2)
    summary = _base_summary(start_at=start_at, deadline_at=deadline_at, timezone_name="UTC")
    draft = await _confirmed_draft(client, "bug41-draft", task_summary=summary)
    cand_resp = await _create_candidate_api(
        client,
        draft,
        "bug41-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Bước sửa được",
                "expected_output": "Xong bước",
                "estimated_base_seconds": 1500,
            }
        ],
    )
    assert cand_resp.status_code == 201
    plan = cand_resp.json()["data"]
    sess_key = plan["sessions"][0]["client_session_key"]
    dur = plan["sessions"][0]["duration_seconds"]
    valid_start = datetime.fromisoformat(plan["sessions"][0]["scheduled_start"].replace("Z", "+00:00"))

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(plan["candidate_id"]))
        assert row is not None
        row.feasibility = {
            **row.feasibility,
            "status": "infeasible",
            "fully_scheduled": False,
            "deficit_seconds": 1800,
            "unscheduled_seconds": 1800,
            "unscheduled_step_keys": ["step-1"],
            "blocking_reasons": [
                {"code": "SESSION_COLLISION", "message": "Old collision"},
                {"code": "OUTSIDE_AVAILABILITY", "message": "Old outside"},
                {"code": "TOTAL_DEFICIT", "message": "Old deficit"},
            ],
        }
        row.sessions = [
            {
                **row.sessions[0],
                "scheduled_start": (start_at - timedelta(hours=3)).isoformat(),
                "scheduled_end": (start_at - timedelta(hours=3) + timedelta(seconds=dur)).isoformat(),
            }
        ]
        await db.commit()

    patch_resp = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "bug41-fix-patch",
        {
            "expected_review_revision": plan["review_revision"],
            "edits": {
                "sessions": [
                    {
                        "client_session_key": sess_key,
                        "scheduled_start": valid_start.isoformat(),
                    }
                ]
            },
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    fixed = patch_resp.json()["data"]
    assert fixed["feasibility"]["status"] == "feasible"
    assert fixed["feasibility"]["fully_scheduled"] is True
    assert fixed["feasibility"]["deficit_seconds"] == 0
    assert fixed["feasibility"]["unscheduled_seconds"] == 0
    assert fixed["feasibility"]["unscheduled_step_keys"] == []
    assert fixed["feasibility"]["blocking_reasons"] == []

    conf_resp = await _confirm_candidate_api(client, fixed, "bug41-confirm")
    assert conf_resp.status_code == 201, conf_resp.text


# ---------------------------------------------------------------------------
# 2.5 Bug 4.2: PATCH candidate timing + sessions -> 422; title/output + sessions -> 200
# ---------------------------------------------------------------------------


async def test_bug_4_2_patch_candidate_rejects_timing_and_sessions_together_but_allows_title_and_sessions(
    api,
):
    client, _ = api
    draft = await _confirmed_draft(client, "bug42-draft")
    plan = (
        await _create_candidate_api(
            client,
            draft,
            "bug42-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước A",
                    "expected_output": "Output A",
                    "estimated_base_seconds": 1500,
                }
            ],
        )
    ).json()["data"]
    step_key = plan["steps"][0]["client_step_key"]
    sess = plan["sessions"][0]
    new_start = (
        datetime.fromisoformat(sess["scheduled_start"].replace("Z", "+00:00")) + timedelta(minutes=30)
    ).isoformat()

    resp_est = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "bug42-err-est",
        {
            "expected_review_revision": plan["review_revision"],
            "edits": {
                "steps": [{"client_step_key": step_key, "estimated_base_seconds": 1800}],
                "sessions": [
                    {"client_session_key": sess["client_session_key"], "scheduled_start": new_start}
                ],
            },
        },
    )
    assert resp_est.status_code == 422, resp_est.text
    assert resp_est.json()["error"]["code"] == "VALIDATION_ERROR"

    resp_ord = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "bug42-err-ord",
        {
            "expected_review_revision": plan["review_revision"],
            "edits": {
                "steps": [{"client_step_key": step_key, "order_index": 0}],
                "sessions": [
                    {"client_session_key": sess["client_session_key"], "scheduled_start": new_start}
                ],
            },
        },
    )
    assert resp_ord.status_code == 422, resp_ord.text
    assert resp_ord.json()["error"]["code"] == "VALIDATION_ERROR"

    resp_ok = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "bug42-ok",
        {
            "expected_review_revision": plan["review_revision"],
            "edits": {
                "steps": [
                    {
                        "client_step_key": step_key,
                        "title": "Bước A (đã sửa tên)",
                        "expected_output": "Output A mới",
                    }
                ],
                "sessions": [
                    {"client_session_key": sess["client_session_key"], "scheduled_start": new_start}
                ],
            },
        },
    )
    assert resp_ok.status_code == 200, resp_ok.text
    ok_data = resp_ok.json()["data"]
    assert ok_data["steps"][0]["title"] == "Bước A (đã sửa tên)"
    assert ok_data["steps"][0]["expected_output"] == "Output A mới"


# ---------------------------------------------------------------------------
# 2.5 Bug 5.1: start near +5m skips past reminders; start >= +20m keeps all
# ---------------------------------------------------------------------------


async def test_bug_5_1_confirm_candidate_skips_past_reminders_and_keeps_all_future_reminders(api):
    client, factory = api
    draft1 = await _confirmed_draft(client, "bug51-near-draft")
    plan1 = (await _create_candidate_api(client, draft1, "bug51-near-cand")).json()["data"]
    conf1 = await _confirm_candidate_api(client, plan1, "bug51-near-conf")
    assert conf1.status_code == 201, conf1.text
    approved_at_1 = datetime.fromisoformat(
        conf1.json()["data"]["schedule_version"]["approved_at"].replace("Z", "+00:00")
    )
    async with factory() as db:
        rems1 = (await db.scalars(select(Reminder))).all()
        expected1 = sum(
            1
            for sess in plan1["sessions"]
            for offset in (900, 600, 300)
            if datetime.fromisoformat(sess["scheduled_start"].replace("Z", "+00:00"))
            - timedelta(seconds=offset)
            >= approved_at_1
        )
        assert len(rems1) == expected1
        assert len(rems1) < len(plan1["sessions"]) * 3
        for rem in rems1:
            fire_at = rem.fire_at if rem.fire_at.tzinfo else rem.fire_at.replace(tzinfo=UTC)
            assert fire_at >= approved_at_1

    now = datetime.now(UTC)
    draft2 = await _confirmed_draft(
        client,
        "bug51-fut-draft",
        task_summary=_base_summary(
            start_at=now + timedelta(minutes=20), deadline_at=now + timedelta(days=3)
        ),
    )
    plan2 = (await _create_candidate_api(client, draft2, "bug51-fut-cand")).json()["data"]
    conf2 = await _confirm_candidate_api(client, plan2, "bug51-fut-conf")
    assert conf2.status_code == 201, conf2.text
    task2_step_ids = [UUID(v) for v in conf2.json()["data"]["key_mapping"]["steps"].values()]
    async with factory() as db:
        rems2 = (
            await db.scalars(
                select(Reminder)
                .join(SessionRow, SessionRow.id == Reminder.session_id)
                .where(SessionRow.step_id.in_(task2_step_ids))
            )
        ).all()
        assert len(rems2) == len(plan2["sessions"]) * 3


# ---------------------------------------------------------------------------
# 2.6 RISK-UF1-04: 20:00–23:59 -> 14400s (4h), 4x60p and 8x30p
# ---------------------------------------------------------------------------


def test_risk_uf1_04_20_00_to_23_59_yields_14400s_and_schedules_4x60_and_8x30():
    target_date = datetime(2026, 11, 20, 0, 0, tzinfo=UTC).date()
    avail = DraftAvailability(
        timezone="Asia/Ho_Chi_Minh",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=target_date,
                intervals=[LocalInterval(start_local="20:00", end_local="23:59")],
            )
        ],
    )
    w_start = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    w_end = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    free = compute_effective_free_intervals(avail, w_start, w_end)
    assert len(free) == 1
    assert int((free[0].end_at - free[0].start_at).total_seconds()) == 14400

    tc = TimeConstraint(
        kind="window",
        start_at=w_start,
        finish_by_at=w_end,
        input_timezone="Asia/Ho_Chi_Minh",
    )
    step_4h = CandidateStep(
        client_step_key="step-1",
        title="Full 4h block",
        expected_output="Done 4h",
        order_index=0,
        estimated_base_seconds=14400,
        remaining_base_seconds=14400,
        buffer_multiplier=1.0,
        estimate_source="user_confirmed",
        status="pending",
    )
    pref_60 = UserPreference(
        revision=1,
        effective_revision=1,
        work_interval_seconds=3600,
        rest_interval_seconds=0,
        micro_task_seconds=120,
        reminder_offsets=[900, 600, 300],
        buffer_multiplier=1.0,
        reminder_style="gentle",
    )
    sess_60, rep_60 = schedule_candidate_sessions([step_4h], free, [], tc, pref_60)
    assert rep_60.status == "feasible"
    assert rep_60.deficit_seconds == 0
    assert len(sess_60) == 1
    assert [(s.kind, s.duration_seconds) for s in sess_60[0].segments] == [("work", 3600)] * 4

    pref_30 = pref_60.model_copy(update={"work_interval_seconds": 1800})
    sess_30, rep_30 = schedule_candidate_sessions([step_4h], free, [], tc, pref_30)
    assert rep_30.status == "feasible"
    assert rep_30.deficit_seconds == 0
    assert len(sess_30) == 1
    assert [(s.kind, s.duration_seconds) for s in sess_30[0].segments] == [("work", 1800)] * 8


# ---------------------------------------------------------------------------
# 2.6 RISK-UF2-01: slot sliver (< 300s) & tail sliver (< 120s)
# ---------------------------------------------------------------------------


def test_risk_uf2_01_skips_slot_slivers_under_5m_and_trims_tail_slivers_under_2m():
    segs, work, rest, dur = fit_session_to_slot(
        remaining_work_seconds=2000,
        slot_seconds=1850,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
    )
    assert work == 1500
    assert rest == 0
    assert dur == 1500
    assert [(s.kind, s.duration_seconds) for s in segs] == [("work", 1500)]

    base = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    avail = DraftAvailability(
        timezone="UTC",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=base.date(),
                intervals=[
                    LocalInterval(start_local="08:00", end_local="08:04"),
                    LocalInterval(start_local="09:00", end_local="10:00"),
                ],
            )
        ],
    )
    free = compute_effective_free_intervals(avail, base, base + timedelta(hours=3))
    tc = TimeConstraint(
        kind="window", start_at=base, finish_by_at=base + timedelta(hours=3), input_timezone="UTC"
    )
    pref = UserPreference(
        revision=1,
        effective_revision=1,
        work_interval_seconds=1500,
        rest_interval_seconds=300,
        micro_task_seconds=120,
        reminder_offsets=[900, 600, 300],
        buffer_multiplier=1.0,
        reminder_style="gentle",
    )
    step = CandidateStep(
        client_step_key="step-1",
        title="No tiny sliver",
        expected_output="Done",
        order_index=0,
        estimated_base_seconds=1500,
        remaining_base_seconds=1500,
        buffer_multiplier=1.0,
        estimate_source="user_confirmed",
        status="pending",
    )
    sessions, report = schedule_candidate_sessions([step], free, [], tc, pref)
    assert report.status == "feasible"
    assert len(sessions) == 1
    assert sessions[0].scheduled_start == datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    assert sessions[0].work_seconds == 1500


# ---------------------------------------------------------------------------
# 2.6 RISK-UF2-02: offline parser extra_requirements & 2-pass step order
# ---------------------------------------------------------------------------


async def test_risk_uf2_02_offline_extra_requirements_parser_and_2pass_step_order(api):
    client, _ = api
    parsed = parse_extra_requirements(
        [
            "Mỗi ngày học tối đa 60 phút",
            "Chỉ xếp khi có ít nhất 45 phút trống liền",
            "Ưu tiên sau 20h",
        ]
    )
    assert parsed.max_daily_work_seconds == 3600
    assert parsed.min_contiguous_slot_seconds == 2700
    assert parsed.preferred_time_windows == ((20 * 60, 24 * 60),)

    # Preferred window has only 2h total across 2 days, while steps need 3h -> Pass 2 falls back
    # to full free slots and strictly preserves STEP_ORDER.
    d1 = datetime(2026, 11, 21, 0, 0, tzinfo=UTC).date()
    d2 = datetime(2026, 11, 22, 0, 0, tzinfo=UTC).date()
    avail = DraftAvailability(
        timezone="Asia/Ho_Chi_Minh",
        weekly_rules=[],
        date_overrides=[
            DateOverride(
                date=d,
                intervals=[
                    LocalInterval(start_local="09:00", end_local="11:00"),
                    LocalInterval(start_local="20:00", end_local="21:00"),
                ],
            )
            for d in (d1, d2)
        ],
    )
    w_start = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    w_end = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    free = compute_effective_free_intervals(avail, w_start, w_end)
    tc = TimeConstraint(
        kind="window", start_at=w_start, finish_by_at=w_end, input_timezone="Asia/Ho_Chi_Minh"
    )
    pref = UserPreference(
        revision=1,
        effective_revision=1,
        work_interval_seconds=1800,
        rest_interval_seconds=0,
        micro_task_seconds=120,
        reminder_offsets=[900, 600, 300],
        buffer_multiplier=1.0,
        reminder_style="gentle",
    )
    steps = [
        CandidateStep(
            client_step_key=f"step-{i + 1}",
            title=f"Step {i + 1}",
            expected_output="Done",
            order_index=i,
            estimated_base_seconds=5400,
            remaining_base_seconds=5400,
            buffer_multiplier=1.0,
            estimate_source="user_confirmed",
            status="pending",
        )
        for i in range(2)
    ]
    sessions, report = schedule_candidate_sessions(
        steps,
        free,
        [],
        tc,
        pref,
        extra_requirements=[
            "Mỗi ngày làm tối đa 90 phút",
            "Chỉ xếp khi có ít nhất 45 phút trống liền",
            "Ưu tiên sau 20h",
        ],
    )
    assert report.status == "feasible", report
    s1_end = max(s.scheduled_end for s in sessions if s.client_step_key == "step-1")
    s2_start = min(s.scheduled_start for s in sessions if s.client_step_key == "step-2")
    assert s1_end <= s2_start
    work_by_day: dict = {}
    for s in sessions:
        day = s.scheduled_start.astimezone().date()
        work_by_day[day] = work_by_day.get(day, 0) + s.work_seconds
    assert all(secs <= 5400 for secs in work_by_day.values())

    # Also verify offline via HTTP create_candidate when AI is unavailable
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    http_start = now + timedelta(days=1)
    http_summary = _base_summary(
        start_at=http_start,
        deadline_at=http_start + timedelta(days=6),
        weekly_rules=[
            {"weekday": i, "start_local": "09:00", "end_local": "12:00"} for i in range(1, 8)
        ],
        extra_requirements=["Mỗi ngày học tối đa 60 phút"],
        timezone_name="Asia/Ho_Chi_Minh",
    )
    draft = await _confirmed_draft(client, "uf202-http-draft", task_summary=http_summary)
    cand_resp = await _create_candidate_api(client, draft, "uf202-http-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    plan = cand_resp.json()["data"]
    assert plan["feasibility"]["status"] == "feasible"
    by_day: dict = {}
    for sess in plan["sessions"]:
        st = datetime.fromisoformat(sess["scheduled_start"].replace("Z", "+00:00")).date()
        by_day[st] = by_day.get(st, 0) + sess["work_seconds"]
    assert all(total_w <= 3600 for total_w in by_day.values())


# ---------------------------------------------------------------------------
# 2.6 RISK-UF2-03: Schema (>7..20 steps, <=480m), 3-tier profile, minimal thinking, Groq timeout
# ---------------------------------------------------------------------------


async def test_risk_uf2_03_schema_limits_dynamic_profile_thinking_groq_timeout(monkeypatch):
    steps_10 = [
        StepPromptItem(
            order_index=i + 1,
            title=f"Cột mốc {i + 1}",
            expected_output=f"Đầu ra {i + 1}",
            estimated_minutes=480 if i == 0 else 60,
            micro_task_prompt=f"Mở tài liệu cho cột mốc {i + 1}",
            cognitive_load="medium",
        )
        for i in range(10)
    ]
    decomp = LLMTaskDecompositionPayload(
        task_title="Dự án dài hạn",
        steps=steps_10,
        reasoning="Chia theo 10 cột mốc rõ ràng.",
    )
    assert len(decomp.steps) == 10
    adj = LLMPlanAdjustmentPayload(
        updated_steps=steps_10,
        assistant_note="Đã cập nhật 10 bước.",
    )
    assert len(adj.updated_steps) == 10

    with pytest.raises(ValidationError):
        StepPromptItem(
            order_index=1,
            title="Quá dài",
            expected_output="Fail",
            estimated_minutes=481,
            micro_task_prompt="Mở file",
        )
    with pytest.raises(ValidationError):
        LLMTaskDecompositionPayload(
            task_title="Quá nhiều bước",
            steps=[steps_10[0].model_copy(update={"order_index": i + 1}) for i in range(21)],
            reasoning="Quá giới hạn 20",
        )

    svc_default = AIService(app_settings=Settings(TESTING=True))
    prof_s = svc_default._decomposition_profile(total_project_days=14, total_free_hours=10.0)
    prof_m = svc_default._decomposition_profile(total_project_days=30, total_free_hours=30.0)
    prof_l = svc_default._decomposition_profile(total_project_days=60, total_free_hours=80.0)
    assert prof_s.hard_timeout == 9.0
    assert prof_m.hard_timeout == 14.0
    assert prof_l.hard_timeout == 20.0
    assert prof_l.warm_backup_at == 3.0
    assert prof_l.primary_priority_until == 7.0
    assert prof_l.groq_hedge_at == 6.5

    svc_fast = AIService(app_settings=Settings(TESTING=True, AI_UF2_TIMEOUT_SECONDS=0.5))
    prof_fast = svc_fast._decomposition_profile(total_project_days=60, total_free_hours=80.0)
    assert prof_fast.hard_timeout == 0.5

    captured_thinking: list[bool] = []

    async def _mock_hedged(**kwargs):
        captured_thinking.append(bool(kwargs.get("force_minimal_thinking")))
        return LLMTaskDecompositionPayload(
            task_title="Dự án 60 ngày",
            steps=steps_10[:6],
            reasoning="6 cột mốc.",
        ), "tier_1_gemini"

    monkeypatch.setattr(svc_default, "_execute_hedged_structured", _mock_hedged)
    await svc_default.decompose_task(
        task_title="Dự án 60 ngày",
        total_project_days=60,
        total_free_hours=50.0,
    )
    await svc_default.decompose_task(
        task_title="Dự án 7 ngày",
        total_project_days=7,
        total_free_hours=10.0,
    )
    assert captured_thinking == [True, False]

    seen_timeouts: list[float] = []

    class _FakeCompletions:
        async def create(self, **kwargs):
            seen_timeouts.append(kwargs["timeout"])
            content = LLMTaskDecompositionPayload(
                task_title="Groq timeout check",
                steps=steps_10[:2],
                reasoning="OK",
            ).model_dump_json()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    svc_groq = AIService(app_settings=Settings(TESTING=True, GROQ_API_KEY="fake-key-for-test"))
    svc_groq._groq_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )  # type: ignore[assignment]
    loop = asyncio.get_running_loop()
    res = await svc_groq._call_groq_structured(
        system_prompt="sys",
        user_prompt="usr",
        schema_cls=LLMTaskDecompositionPayload,
        deadline=loop.time() + 12.5,
    )
    assert len(res.steps) == 2
    assert len(seen_timeouts) == 1
    assert 10.0 <= seen_timeouts[0] <= 12.6


# ---------------------------------------------------------------------------
# 2.6 RISK-UF2-03: 60-day plan -> 14d slice + pending StepRow -> UF6 expand_next_horizon
# ---------------------------------------------------------------------------


async def test_risk_uf2_03_60_day_horizon_slice_pending_steps_and_uf6_expand_next_horizon(
    api, monkeypatch
):
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = now + timedelta(hours=1)
    deadline_at = start_at + timedelta(days=60)

    summary_60d = _base_summary(
        start_at=start_at,
        deadline_at=deadline_at,
        weekly_rules=[
            {"weekday": i, "start_local": "19:00", "end_local": "21:00"} for i in range(1, 8)
        ],
        timezone_name="Asia/Ho_Chi_Minh",
    )
    draft = await _confirmed_draft(client, "uf2-60d-draft", task_summary=summary_60d)

    observed_args: list[dict] = []

    async def _fake_decompose(**kwargs):
        observed_args.append(kwargs)
        return TaskDecompositionResult(
            task_title="Khóa học 60 ngày",
            steps=[
                StepPromptItem(
                    order_index=i + 1,
                    title=f"Cột mốc tuần {i + 1}",
                    expected_output=f"Xong cột mốc {i + 1}",
                    estimated_minutes=180,
                    micro_task_prompt=f"Mở bài {i + 1}",
                    cognitive_load="medium",
                )
                for i in range(10)
            ],
            reasoning="Chia 10 cột mốc đều đặn trong 60 ngày.",
            generation_tier="tier_1_gemini",
        )

    monkeypatch.setattr(ai_service, "decompose_task", _fake_decompose)

    cand_resp = await _create_candidate_api(client, draft, "uf2-60d-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    plan = cand_resp.json()["data"]

    assert len(observed_args) == 1
    assert observed_args[0]["total_project_days"] >= 59
    assert observed_args[0]["total_free_hours"] >= 100.0
    assert len(plan["steps"]) == 10
    assert plan["feasibility"]["status"] == "feasible"
    assert plan["feasibility"]["fully_scheduled"] is True
    visible_keys = plan["feasibility"]["verification_scope"]["client_step_keys"]
    assert 1 <= len(visible_keys) < 10
    assert {s["client_step_key"] for s in plan["sessions"]}.issubset(set(visible_keys))
    assert plan["unexpanded_scope"] is None

    conf_resp = await _confirm_candidate_api(client, plan, "uf2-60d-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task_data = conf_resp.json()["data"]["task"]
    assert task_data["decomposition_state"] == "complete"
    assert task_data["scheduling_state"] == "partial"
    assert len(task_data["steps"]) == 10

    async with factory() as db:
        step_rows = (
            await db.scalars(
                select(StepRow)
                .where(StepRow.task_id == UUID(task_data["task_id"]))
                .order_by(StepRow.order_index)
            )
        ).all()
        assert len(step_rows) == 10
        assert all(st.status == "pending" for st in step_rows)
        scheduled_step_ids = {UUID(s["step_id"]) for s in task_data["sessions"]}
        unscheduled_steps = [st for st in step_rows if st.id not in scheduled_step_ids]
        assert len(unscheduled_steps) == 10 - len(visible_keys)

    sched_ver = conf_resp.json()["data"]["schedule_version"]["version_number"]
    prop_resp = await _post(
        client,
        "/proposals",
        "uf6-expand-1",
        {
            "trigger_reason": "expand_next_horizon",
            "target_task_id": task_data["task_id"],
            "allowed_task_ids": [task_data["task_id"]],
            "expected_base_schedule_revision": sched_ver,
            "expected_source_revisions": {
                "task": task_data["revision"],
                "schedule": sched_ver,
                "availability": 0,
                "settings": 1,
                "calendar": 1,
            },
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text
    prop = prop_resp.json()["data"]
    assert prop["feasibility"]["status"] == "feasible"
    assert prop["feasibility"]["confirmable"] is True
    assert prop["proposed_changes"]["steps_to_add"] == []
    assert prop["feasibility"]["next_horizon_preview"]["expanded_steps_count"] >= 1
    expanded_step_ids = {item["step_id"] for item in prop["proposed_changes"]["sessions_to_add"]}
    assert expanded_step_ids.issubset({str(st.id) for st in unscheduled_steps})

    app_resp = await _post(
        client,
        f"/proposals/{prop['proposal_id']}/approve",
        "uf6-expand-approve-1",
        {
            "expected_proposal_revision": prop["proposal_revision"],
            "expected_base_schedule_revision": prop["base_schedule_revision"],
            "expected_source_revisions": prop["source_revisions"],
            "start_continuation": False,
        },
    )
    assert app_resp.status_code == 200, app_resp.text
    task_after = (await client.get(f"/api/v1/tasks/{task_data['task_id']}")).json()["data"]
    assert len(task_after["sessions"]) > len(task_data["sessions"])
    assert task_after["scheduling_state"] == "complete"


async def test_risk_uf2_03_pinned_clock_session_within_15m_lead_preserved_on_expand_next_horizon(
    api, monkeypatch
):
    """Pin đồng hồ tại 12:59:00Z (19:59+07): Session 1 đặt ở 13:04Z (< calc_now + 15m) không bị ép dời khi expand_next_horizon."""
    client, _ = api
    pinned_now = datetime(2026, 11, 22, 12, 59, 0, tzinfo=UTC)

    class _PinnedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return pinned_now.replace(tzinfo=None)
            return pinned_now.astimezone(tz)

    for svc in (draft_service, plan_service, reschedule_service, run_service):
        monkeypatch.setattr(svc, "datetime", _PinnedDateTime)
    monkeypatch.setattr(
        reschedule_service,
        "_current_utc_now",
        lambda now=None: (now or pinned_now).astimezone(UTC),
    )
    monkeypatch.setattr(
        run_service,
        "_current_utc_now",
        lambda now=None: (now or pinned_now).astimezone(UTC),
    )

    start_at = pinned_now
    deadline_at = start_at + timedelta(days=60)
    summary_60d = _base_summary(
        start_at=start_at,
        deadline_at=deadline_at,
        weekly_rules=[
            {"weekday": i, "start_local": "19:00", "end_local": "21:00"} for i in range(1, 8)
        ],
        timezone_name="Asia/Ho_Chi_Minh",
    )
    draft = await _confirmed_draft(client, "uf2-60d-pinned-draft", task_summary=summary_60d)

    async def _fake_decompose(**kwargs):
        return TaskDecompositionResult(
            task_title="Khóa học 60 ngày pin clock",
            steps=[
                StepPromptItem(
                    order_index=i + 1,
                    title=f"Cột mốc tuần {i + 1}",
                    expected_output=f"Xong cột mốc {i + 1}",
                    estimated_minutes=180,
                    micro_task_prompt=f"Mở bài {i + 1}",
                    cognitive_load="medium",
                )
                for i in range(10)
            ],
            reasoning="Chia 10 cột mốc đều đặn trong 60 ngày.",
            generation_tier="tier_1_gemini",
        )

    monkeypatch.setattr(ai_service, "decompose_task", _fake_decompose)

    cand_resp = await _create_candidate_api(client, draft, "uf2-60d-pinned-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    plan = cand_resp.json()["data"]
    assert plan["feasibility"]["status"] == "feasible"
    assert plan["feasibility"]["fully_scheduled"] is True

    first_sess_start = datetime.fromisoformat(
        plan["sessions"][0]["scheduled_start"].replace("Z", "+00:00")
    )
    assert first_sess_start == datetime(2026, 11, 22, 13, 4, 0, tzinfo=UTC)
    assert pinned_now <= first_sess_start < pinned_now + timedelta(minutes=15)

    conf_resp = await _confirm_candidate_api(client, plan, "uf2-60d-pinned-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task_data = conf_resp.json()["data"]["task"]
    assert task_data["scheduling_state"] == "partial"

    sched_ver = conf_resp.json()["data"]["schedule_version"]["version_number"]
    prop_resp = await _post(
        client,
        "/proposals",
        "uf6-expand-pinned-1",
        {
            "trigger_reason": "expand_next_horizon",
            "target_task_id": task_data["task_id"],
            "allowed_task_ids": [task_data["task_id"]],
            "expected_base_schedule_revision": sched_ver,
            "expected_source_revisions": {
                "task": task_data["revision"],
                "schedule": sched_ver,
                "availability": 0,
                "settings": 1,
                "calendar": 1,
            },
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text
    prop = prop_resp.json()["data"]
    assert prop["feasibility"]["status"] == "feasible", prop["feasibility"]
    assert prop["feasibility"]["confirmable"] is True
    assert prop["proposed_changes"]["sessions_to_move"] == []
    assert prop["proposed_changes"]["sessions_to_add"]


# ---------------------------------------------------------------------------
# 2.6 RISK-UF2-04: Fallback decompose user_step_proposal & NL adjust
# ---------------------------------------------------------------------------


async def test_risk_uf2_04_offline_fallbacks_for_user_step_proposal_and_nl_adjust(api):
    client, _ = api
    res_inline = ai_service._fallback_decompose_task(
        "Làm đồ án",
        user_step_proposal="1. Đọc tài liệu 30p; 2. Viết code backend 45 phút; 3. Chạy test 20p",
    )
    assert res_inline.generation_tier == "tier_3_rule_based"
    assert len(res_inline.steps) == 3
    assert [s.estimated_minutes for s in res_inline.steps] == [30, 45, 20]

    draft = await _confirmed_draft(client, "uf204-draft")
    plan = (await _create_candidate_api(client, draft, "uf204-cand")).json()["data"]

    draft_upd = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "uf204-draft-upd",
        {
            "expected_review_revision": plan["review_revision"],
            "user_instruction": "Đổi hạn chót sang thứ 7 và mở thêm giờ rảnh tối",
        },
    )
    assert draft_upd.status_code == 422, draft_upd.text
    assert draft_upd.json()["error"]["code"] == "DRAFT_UPDATE_REQUIRED"

    dur_upd = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "uf204-dur-upd",
        {
            "expected_review_revision": plan["review_revision"],
            "user_instruction": "Chỉnh bước 2 thành 40 phút",
        },
    )
    assert dur_upd.status_code == 503, dur_upd.text
    assert dur_upd.json()["error"]["code"] == "AI_UNAVAILABLE"

    # Option 2A: phương án hiện tại được giữ nguyên hoàn toàn khi AI lỗi
    preserved = (await client.get(f"/api/v1/candidates/{plan['candidate_id']}")).json()["data"]
    assert preserved["review_revision"] == plan["review_revision"]
    assert preserved["steps"] == plan["steps"]

    complex_upd = await _patch(
        client,
        f"/candidates/{plan['candidate_id']}",
        "uf204-complex",
        {
            "expected_review_revision": plan["review_revision"],
            "user_instruction": "Thêm một bước mới và đảo thứ tự bước 1 với bước 2",
        },
    )
    assert complex_upd.status_code == 503, complex_upd.text
    assert complex_upd.json()["error"]["code"] == "AI_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 2.6 RISK-UF2-06: available==0 skips AI; GET candidate uses read_only=True (no FOR UPDATE)
# ---------------------------------------------------------------------------


async def test_risk_uf2_06_zero_free_seconds_skips_ai_and_get_candidate_no_for_update(
    api, monkeypatch
):
    client, factory = api
    now = datetime.now(UTC)
    target_start = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    zero_summary = _base_summary(
        start_at=target_start,
        deadline_at=target_start + timedelta(hours=2),
        weekly_rules=[
            {"weekday": target_start.isoweekday(), "start_local": "09:00", "end_local": "10:00"}
        ],
        timezone_name="UTC",
    )
    draft = await _confirmed_draft(client, "uf206-draft", task_summary=zero_summary)
    ctx_resp = await _post(
        client,
        "/planning-contexts",
        "uf206-busy-ctx",
        {
            "source_draft_id": draft["draft_id"],
            "expected_summary_revision": draft["summary_revision"],
            "from": (target_start - timedelta(hours=1)).isoformat(),
            "to": (target_start + timedelta(days=1)).isoformat(),
            "manual_busy_intervals": [
                {
                    "client_interval_id": "block-all",
                    "start_at": target_start.isoformat(),
                    "end_at": (target_start + timedelta(hours=2)).isoformat(),
                }
            ],
        },
    )
    assert ctx_resp.status_code == 201, ctx_resp.text

    ai_mock = AsyncMock(
        side_effect=AssertionError("AI must NOT be called when total_free_seconds == 0")
    )
    monkeypatch.setattr(ai_service, "decompose_task", ai_mock)

    cand_resp = await _create_candidate_api(client, draft, "uf206-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    assert ai_mock.call_count == 0
    plan = cand_resp.json()["data"]
    assert plan["feasibility"]["status"] == "infeasible"
    assert plan["feasibility"]["deficit_seconds"] > 0

    async with factory() as db:
        crow = await db.get(CandidateRow, UUID(plan["candidate_id"]))
        assert crow is not None
        assert crow.generation_tier == "tier_3_rule_based"

    read_only_flags: list[bool] = []
    for_update_seen: list[bool] = []
    orig_sources = plan_service._sources

    async def _spy_sources(*args, **kwargs):
        read_only_flags.append(bool(kwargs.get("read_only", False)))
        return await orig_sources(*args, **kwargs)

    def _on_orm_execute(execute_state):
        stmt = execute_state.statement
        for_update_seen.append(getattr(stmt, "_for_update_arg", None) is not None)

    monkeypatch.setattr(plan_service, "_sources", _spy_sources)
    event.listen(OrmSession, "do_orm_execute", _on_orm_execute)
    try:
        get_resp = await client.get(f"/api/v1/candidates/{plan['candidate_id']}")
    finally:
        event.remove(OrmSession, "do_orm_execute", _on_orm_execute)

    assert get_resp.status_code == 200, get_resp.text
    assert read_only_flags == [True]
    assert for_update_seen
    assert not any(for_update_seen)



# ---------------------------------------------------------------------------
# Post-audit UF2/UF6 patches verification (4 targeted regression tests)
# ---------------------------------------------------------------------------


async def test_expand_next_horizon_pass1_tomorrow_start_and_pass2_beyond_14_days_preserves_rg02(
    api, monkeypatch
):
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    # RG02 sentinel task scheduled around Day 19 that must NEVER be disturbed
    sentinel_start = now + timedelta(days=18, hours=10)
    sentinel_summary = _base_summary(
        start_at=sentinel_start,
        deadline_at=sentinel_start + timedelta(days=3),
        weekly_rules=[
            {"weekday": wd, "start_local": "18:00", "end_local": "20:00"} for wd in range(1, 8)
        ],
        timezone_name="UTC",
    )
    sentinel_draft = await _confirmed_draft(
        client, "uf2-expand-sentinel-draft", task_summary=sentinel_summary
    )
    sentinel_cand = (
        await _create_candidate_api(
            client,
            sentinel_draft,
            "uf2-expand-sentinel-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Sentinel task cố định",
                    "expected_output": "Không bị dời",
                    "estimated_base_seconds": 1500,
                }
            ],
        )
    ).json()["data"]
    sentinel_conf = await _confirm_candidate_api(client, sentinel_cand, "uf2-expand-sentinel-ok")
    assert sentinel_conf.status_code == 201, sentinel_conf.text
    sentinel_sess_before = sentinel_conf.json()["data"]["task"]["sessions"][0]["scheduled_start"]

    # 60d task starting tomorrow (`now + 1d`) so the 14th daily session in `[now+1d, now+15d]`
    # lands strictly AFTER `calc_now + 14d` on Pass 1, and steps 4..6 require Pass 2+.
    slot_hour = 22 if now.hour < 21 else 10
    t_start = now + timedelta(days=1)
    t_deadline = t_start + timedelta(days=60)
    summary_60d = _base_summary(
        start_at=t_start,
        deadline_at=t_deadline,
        weekly_rules=[
            {
                "weekday": wd,
                "start_local": f"{slot_hour:02d}:00",
                "end_local": f"{slot_hour + 1:02d}:00",
            }
            for wd in range(1, 8)
        ],
        timezone_name="UTC",
    )
    draft = await _confirmed_draft(client, "uf2-expand-60d-draft", task_summary=summary_60d)

    step_mins = [250, 250, 75, 250, 250, 250]
    llm_steps = [
        StepPromptItem(
            order_index=i + 1,
            title=f"Giai đoạn 60 ngày số {i + 1}",
            expected_output="Hoàn tất mốc",
            estimated_minutes=mins,
            micro_task_prompt=f"Mở tài liệu giai đoạn {i + 1}",
        )
        for i, mins in enumerate(step_mins)
    ]
    monkeypatch.setattr(
        ai_service,
        "decompose_task",
        AsyncMock(
            return_value=TaskDecompositionResult(
                task_title=summary_60d["title"],
                steps=llm_steps,
                reasoning="Chia lộ trình 60 ngày thành 6 giai đoạn.",
                generation_tier="tier_1_gemini",
                model_name="fake-gemini",
            )
        ),
    )

    cand_resp = await _create_candidate_api(client, draft, "uf2-expand-60d-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    plan = cand_resp.json()["data"]
    assert plan["feasibility"]["status"] == "feasible"
    # Ensure initial horizon actually crossed calc_now + 14d (Pass 1 bug trigger condition)
    last_initial_end = max(
        datetime.fromisoformat(s["scheduled_end"].replace("Z", "+00:00"))
        for s in plan["sessions"]
    )
    assert last_initial_end > now + timedelta(days=14)

    conf_resp = await _confirm_candidate_api(client, plan, "uf2-expand-60d-confirm")
    assert conf_resp.status_code == 201, conf_resp.text
    task_data = conf_resp.json()["data"]["task"]
    assert task_data["scheduling_state"] == "partial"

    cur_task_rev = task_data["revision"]
    cur_sched_ver = conf_resp.json()["data"]["schedule_version"]["version_number"]
    cal_rev = plan["source_revisions"]["calendar_context"]["revision"]
    passes_completed = 0
    pending_steps = [
        st
        for st in task_data["steps"]
        if st["step_id"] not in {s["step_id"] for s in task_data["sessions"]}
    ]
    assert pending_steps

    while pending_steps and passes_completed < 4:
        passes_completed += 1
        prop_resp = await _post(
            client,
            "/proposals",
            f"uf2-expand-pass-{passes_completed}",
            {
                "trigger_reason": "expand_next_horizon",
                "target_task_id": task_data["task_id"],
                "allowed_task_ids": [task_data["task_id"]],
                "expected_base_schedule_revision": cur_sched_ver,
                "expected_source_revisions": {
                    "task": cur_task_rev,
                    "schedule": cur_sched_ver,
                    "availability": 0,
                    "settings": 1,
                    "calendar": cal_rev,
                },
            },
        )
        assert prop_resp.status_code == 201, prop_resp.text
        prop = prop_resp.json()["data"]
        assert prop["feasibility"]["status"] == "feasible", prop["feasibility"]
        assert prop["feasibility"]["confirmable"] is True
        assert prop["feasibility"]["deficit_seconds"] == 0
        assert prop["proposed_changes"]["sessions_to_add"]
        assert all(
            mv.get("task_id") == task_data["task_id"]
            for mv in prop["proposed_changes"]["sessions_to_move"]
        )

        app_resp = await _post(
            client,
            f"/proposals/{prop['proposal_id']}/approve",
            f"uf2-expand-approve-{passes_completed}",
            {
                "expected_proposal_revision": prop["proposal_revision"],
                "expected_base_schedule_revision": prop["base_schedule_revision"],
                "expected_source_revisions": prop["source_revisions"],
                "start_continuation": False,
            },
        )
        assert app_resp.status_code == 200, app_resp.text
        task_after = (await client.get(f"/api/v1/tasks/{task_data['task_id']}")).json()["data"]
        cur_task_rev = task_after["revision"]
        cur_sched_ver = app_resp.json()["data"]["updated_schedule_revision"]
        sched_step_ids = {s["step_id"] for s in task_after["sessions"]}
        pending_steps = [st for st in task_after["steps"] if st["step_id"] not in sched_step_ids]

    assert passes_completed >= 2
    assert not pending_steps
    sentinel_after = (
        await client.get(f"/api/v1/tasks/{sentinel_conf.json()['data']['task']['task_id']}")
    ).json()["data"]
    assert sentinel_after["sessions"][0]["scheduled_start"] == sentinel_sess_before


async def test_review_feasibility_partial_scope_restores_search_inconclusive_after_local_fix(api):
    client, factory = api
    draft = await _confirmed_draft(client, "partial-inconclusive-draft")
    cand_json = (
        await _create_candidate_api(
            client,
            draft,
            "partial-inconclusive-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Visible step",
                    "expected_output": "OK",
                    "estimated_base_seconds": 1500,
                }
            ],
        )
    ).json()["data"]
    cand = PlanCandidate.model_validate(cand_json)
    hidden_step = cand.steps[0].model_copy(
        update={
            "client_step_key": "step-beyond-horizon",
            "order_index": 1,
            "title": "Hidden future step",
            "expected_output": "Pending horizon",
            "estimated_base_seconds": 3600,
            "remaining_base_seconds": 3600,
        }
    )
    stale_infeasible_partial = cand.model_copy(
        update={
            "steps": [*cand.steps, hidden_step],
            "feasibility": cand.feasibility.model_copy(
                update={
                    # Simulates stale infeasible left over from an earlier local slot error
                    "status": "infeasible",
                    "fully_scheduled": False,
                    "deficit_seconds": 0,
                    "unscheduled_seconds": 4320,
                    "unscheduled_step_keys": ["step-beyond-horizon"],
                    "verification_scope": cand.feasibility.verification_scope.model_copy(
                        update={"client_step_keys": [cand.steps[0].client_step_key]}
                    ),
                }
            ),
        }
    )

    async with factory() as db:
        _, parsed_draft, _ = await plan_service._read_candidate(db, "user_a", cand.candidate_id)
        source = await plan_service._sources(
            db, "user_a", parsed_draft, cand.planning_horizon, read_only=True
        )

    assert plan_service._concrete_issues(stale_infeasible_partial, source) == []
    reviewed = plan_service._review_feasibility(stale_infeasible_partial, source)
    assert reviewed.feasibility.status == "search_inconclusive"
    assert reviewed.feasibility.fully_scheduled is False

    # Control: when global deficit > 0, partial scope still stays "infeasible"
    with_global_deficit = stale_infeasible_partial.model_copy(
        update={
            "feasibility": stale_infeasible_partial.feasibility.model_copy(
                update={"deficit_seconds": 1800}
            )
        }
    )
    reviewed_def = plan_service._review_feasibility(with_global_deficit, source)
    assert reviewed_def.feasibility.status == "infeasible"


def test_parse_extra_requirements_tranh_lam_and_tranh_xep_negated_periods():
    c1 = parse_extra_requirements(["tránh làm buổi sáng"])
    assert c1.preferred_time_windows == ()

    c2 = parse_extra_requirements(["tránh xếp buổi tối"])
    assert c2.preferred_time_windows == ()

    c3 = parse_extra_requirements(
        ["tránh làm buổi sáng, tránh xếp buổi trưa, ưu tiên buổi tối"]
    )
    assert c3.preferred_time_windows == ((18 * 60, 24 * 60),)


async def test_calendar_ingestion_invalid_uuid_proactive_session_id_falls_back_to_event_to_session(
    api, monkeypatch
):
    client, factory = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    base_start = now + timedelta(days=2)

    draft = await _confirmed_draft(
        client,
        "cal-invalid-uuid-draft",
        task_summary=_base_summary(
            start_at=base_start - timedelta(hours=1),
            deadline_at=base_start + timedelta(days=2),
            timezone_name="UTC",
        ),
    )
    cand = (
        await _create_candidate_api(
            client,
            draft,
            "cal-invalid-uuid-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Linked skipped step",
                    "expected_output": "Done",
                    "estimated_base_seconds": 1500,
                }
            ],
        )
    ).json()["data"]
    conf = await _confirm_candidate_api(client, cand, "cal-invalid-uuid-conf")
    assert conf.status_code == 201
    sid = UUID(next(iter(conf.json()["data"]["key_mapping"]["sessions"].values())))

    conn_id = uuid4()
    cal_id = "qa-secondary@group.calendar.google.com"
    owner = resolve_user_uuid("user_a")
    async with factory() as db:
        db.add(
            CalendarConnection(
                id=conn_id,
                user_id=owner,
                provider="google",
                account_ref="qa@example.com",
                credential_ref="encrypted",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                read_calendar_ids=[cal_id],
                write_calendar_id=cal_id,
            )
        )
        await db.flush()
        srow = await db.get(SessionRow, sid)
        assert srow is not None
        srow.status = "skipped"
        srow.scheduled_start = base_start
        srow.scheduled_end = base_start + timedelta(minutes=30)
        db.add(
            CalendarEventLink(
                user_id=owner,
                session_id=sid,
                connection_id=conn_id,
                google_calendar_id=cal_id,
                google_event_id="evt_linked_bad_prop",
                desired_schedule_revision=1,
                sync_state="synced",
                is_current=True,
            )
        )
        await db.commit()

    from app.services.calendar_service import CalendarService

    async def _fake_tok(self, conn):
        return "fake-token"

    class _FakeCli:
        async def list_events(self, **kwargs):
            return [
                {
                    "id": "evt_linked_bad_prop",
                    "status": "confirmed",
                    "summary": "Skipped event with corrupted UUID prop",
                    "start": {"dateTime": (base_start + timedelta(seconds=10)).isoformat()},
                    "end": {"dateTime": (base_start + timedelta(minutes=30, seconds=10)).isoformat()},
                    "extendedProperties": {
                        "private": {"proactive_session_id": "not-a-valid-uuid"}
                    },
                }
            ]

    monkeypatch.setattr(CalendarService, "get_valid_access_token", _fake_tok)
    monkeypatch.setattr("app.services.calendar_service.get_calendar_client", lambda: _FakeCli())

    async with factory() as db:
        ing = await ingest_calendar_sources(
            db=db,
            user_id=owner,
            selected_sources=[CalendarSource(connection_id=str(conn_id), google_calendar_id=cal_id)],
            excluded_sources=[],
            query_start=base_start - timedelta(hours=1),
            query_end=base_start + timedelta(hours=6),
        )

    assert ing.busy_intervals == []
    assert ing.event_snapshots == []


async def test_finding5b1_horizon_slice_available_seconds_matches_14d_verification_scope(
    api, monkeypatch
):
    """PR #22 Finding #5B-1: allow_horizon_slice=True không trộn required_seconds 14d với available_seconds 30d/60d."""
    client, _ = api
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = now + timedelta(hours=2)
    deadline_at = start_at + timedelta(days=30)

    summary_30d = _base_summary(
        start_at=start_at,
        deadline_at=deadline_at,
        weekly_rules=[
            {"weekday": i, "start_local": "19:00", "end_local": "21:00"} for i in range(1, 8)
        ],
        timezone_name="UTC",
    )
    draft = await _confirmed_draft(client, "f5b1-30d-draft", task_summary=summary_30d)

    # 8 bước x 240p (~288p buffered mỗi bước) > 14d * 120p = 1680p (cần ~30d mới đủ, 14d chỉ xếp được một phần)
    monkeypatch.setattr(
        ai_service,
        "decompose_task",
        AsyncMock(
            return_value=TaskDecompositionResult(
                task_title="Khóa 30 ngày",
                steps=[
                    StepPromptItem(
                        order_index=i + 1,
                        title=f"Chặng {i + 1}",
                        expected_output=f"Xong chặng {i + 1}",
                        estimated_minutes=240,
                        micro_task_prompt=f"Mở chặng {i + 1}",
                    )
                    for i in range(8)
                ],
                reasoning="8 chặng đều.",
                generation_tier="tier_1_gemini",
            )
        ),
    )

    cand_resp = await _create_candidate_api(client, draft, "f5b1-30d-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    plan = cand_resp.json()["data"]
    feas = plan["feasibility"]
    assert feas["status"] == "feasible"
    assert feas["fully_scheduled"] is True
    assert feas["unscheduled_step_keys"] == []

    # 14 ngày * 2h/ngày = 28h = 100,800s (cộng/trừ nhỏ theo giờ hiện tại), tuyệt đối KHÔNG phải 30 ngày (~216,000s)!
    assert feas["available_seconds"] <= 14 * 2 * 3600 + 7200
    assert feas["required_seconds"] <= feas["available_seconds"]
