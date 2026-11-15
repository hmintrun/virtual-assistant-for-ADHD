"""UF1–UF2 regression coverage using an isolated DB and no external providers."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.deps import get_db
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import Base, CommandReceipt, PlanningDraft, Reminder, Task
from app.models.settings import Availability as AvailabilityRow
from app.schemas.task import (
    ExtractedTimeConstraint,
    LLMTaskDecompositionPayload,
    LLMTaskExtractionPayload,
    StepPromptItem,
)
from app.services import plan_service
from app.services.ai_service import UF1_FALLBACK_NOTICE, UF1_FALLBACK_QUESTIONS, ai_service
from app.services.draft_service import resolve_draft_uuid, resolve_user_uuid


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


def summary():
    now = datetime.now(UTC)
    return {
        "title": "Viết báo cáo",
        "description": "Viết báo cáo môn học",
        "scope": "Hoàn thành 5 trang",
        "time_constraint": {
            "kind": "deadline",
            "start_at": (now - timedelta(hours=1)).isoformat(),
            "deadline_at": (now + timedelta(days=5)).isoformat(),
            "input_timezone": "Asia/Ho_Chi_Minh",
        },
        "availability": {
            "timezone": "Asia/Ho_Chi_Minh",
            "weekly_rules": [
                {"weekday": i, "start_local": "00:00", "end_local": "23:59"} for i in range(1, 8)
            ],
        },
    }


async def post(client, path, key, payload):
    return await client.post("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def confirmed_draft(client, key="draft", task_summary=None):
    response = await post(
        client,
        "/drafts",
        key,
        {"client_draft_id": key, "input_source": "text", "summary": task_summary or summary()},
    )
    assert response.status_code == 201, response.text
    draft = response.json()["data"]
    response = await post(
        client,
        f"/drafts/{draft['draft_id']}/confirm-summary",
        key + "-summary",
        {"expected_summary_revision": 1},
    )
    assert response.status_code == 200, response.text
    return draft["draft_id"]


async def candidate(client, draft_id, key="candidate"):
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        key,
        {"source_summary_revision": 1, "source_planning_input_revision": 1},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def confirm(client, plan, key="confirm"):
    return await post(
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


async def test_first_request_can_create_draft(api):
    client, _ = api
    response = await post(
        client,
        "/drafts",
        "first",
        {"client_draft_id": "first", "input_source": "text", "raw_input": "Viết báo cáo"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["stage"] == "collecting"


@pytest.mark.parametrize("has_availability", [False, True])
async def test_uf1_can_confirm_after_fallback_analysis(api, has_availability):
    client, _ = api
    details = summary()
    availability = details.pop("availability")
    if has_availability:
        details["availability"] = availability
    created = await post(
        client,
        "/drafts",
        "create",
        {
            "client_draft_id": "fallback",
            "input_source": "text",
            "raw_input": "Viết báo cáo môn học",
            "summary": details,
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    route = f"/drafts/{draft['draft_id']}"
    analyzed = await post(
        client,
        route + "/analyze",
        "analyze",
        {
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": draft["raw_input"],
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    draft = analyzed.json()["data"]
    issues = draft["summary"]["clarification_issues"]
    assert [i["code"] for i in issues if not i["blocking"]] == ["AI_FALLBACK_NOTICE"]
    assert draft["summary"]["can_plan"] is has_availability
    if not has_availability:
        blocking = [i for i in issues if i["blocking"]]
        assert len(blocking) == 1
        assert blocking[0]["field_path"].split(".")[0] in {"availability", "availability_ref"}
        patched = await client.patch(
            "/api/v1" + route,
            headers={"Idempotency-Key": "choose-availability"},
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "changes": {
                    "summary": {
                        "title": details["title"],
                        "scope": details["scope"],
                        "availability": availability,
                    }
                },
            },
        )
        assert patched.status_code == 200, patched.text
        draft = patched.json()["data"]
        assert draft["summary"]["can_plan"] is True
        assert not any(i["blocking"] for i in draft["summary"]["clarification_issues"])

    result = await post(
        client,
        route + "/confirm-summary",
        "confirm",
        {
            "expected_summary_revision": draft["summary_revision"],
        },
    )
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("unrelated_blocker", [False, True])
async def test_uf1_restores_legacy_fallback_issues(api, unrelated_blocker):
    client, factory = api
    created = await post(
        client,
        "/drafts",
        "legacy-create",
        {"client_draft_id": "legacy", "input_source": "text", "summary": summary()},
    )
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    questions = [UF1_FALLBACK_QUESTIONS["availability"], UF1_FALLBACK_NOTICE]
    if unrelated_blocker:
        questions.append("Báo cáo cần dùng bộ dữ liệu nào?")
    async with factory() as db:
        row = await db.get(PlanningDraft, resolve_draft_uuid(draft["draft_id"]))
        row.stage = "collecting"
        row.summary = {
            **row.summary,
            "can_plan": False,
            "clarification_issues": [
                {
                    "code": "AI_CLARIFICATION",
                    "field_path": f"clarification_questions[{index}]",
                    "message": question,
                    "question": question,
                    "blocking": True,
                }
                for index, question in enumerate(questions)
            ],
        }
        await db.commit()

    route = f"/drafts/{draft['draft_id']}"
    restored = (await client.get("/api/v1" + route)).json()["data"]
    assert restored["summary"]["can_plan"] is not unrelated_blocker
    assert [
        i["question"] for i in restored["summary"]["clarification_issues"] if i["blocking"]
    ] == (questions[-1:] if unrelated_blocker else [])
    confirmed = await post(
        client,
        route + "/confirm-summary",
        "legacy-confirm",
        {
            "expected_summary_revision": restored["summary_revision"],
        },
    )
    assert confirmed.status_code == (422 if unrelated_blocker else 200), confirmed.text


async def test_draft_availability_offline_plan_confirm_and_replay(api):
    client, factory = api
    draft_id = await confirmed_draft(client)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0
    plan = await candidate(client, draft_id)
    assert plan["feasibility"]["status"] == "feasible"
    assert all(s["micro_task_seconds"] == 120 for s in plan["sessions"])
    assert all(
        s["duration_seconds"] == s["work_seconds"] + s["rest_seconds"] for s in plan["sessions"]
    )
    first = datetime.fromisoformat(plan["sessions"][0]["scheduled_start"].replace("Z", "+00:00"))
    assert first > datetime.now(UTC) + timedelta(minutes=4)
    result = await confirm(client, plan)
    assert result.status_code == 201, result.text
    assert result.json()["data"]["calendar_sync_summary"] == "not_integrated"
    assert (await confirm(client, plan)).json()["data"] == result.json()["data"]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        reminders = (await db.scalars(select(Reminder))).all()
        assert len(reminders) == len(plan["sessions"]) * 3
        assert len({item.dedupe_key for item in reminders}) == len(reminders)
    # Giờ riêng không tự biến thành cài đặt chung.
    assert (await client.get("/api/v1/availability")).status_code == 404


async def legacy_availability(client, factory):
    await client.get("/api/v1/preferences")
    async with factory() as db:
        db.add(
            AvailabilityRow(
                user_id=resolve_user_uuid("user_a"),
                source="user_confirmed",
                confirmed_at=datetime.now(UTC),
                task_limits=[],
                **summary()["availability"],
            )
        )
        await db.commit()
    return (await client.get("/api/v1/availability")).json()["data"]


async def test_draft_availability_does_not_replace_global_or_other_drafts(api):
    client, factory = api
    before = await legacy_availability(client, factory)
    first = await confirmed_draft(client, "one")
    second = await confirmed_draft(client, "two")
    one = await candidate(client, first, "one-plan")
    two = await candidate(client, second, "two-plan")
    assert (
        one["source_revisions"]["calendar_context"] != two["source_revisions"]["calendar_context"]
    )
    assert (await client.get("/api/v1/availability")).json()["data"] == before


async def test_new_task_keeps_official_sessions_and_gap(api):
    client, _ = api
    one = await candidate(client, await confirmed_draft(client, "one"), "one-plan")
    result = await confirm(client, one, "one-save")
    assert result.status_code == 201, result.text
    task_id = result.json()["data"]["task"]["task_id"]
    old = (await client.get(f"/api/v1/tasks/{task_id}")).json()["data"]
    two = await candidate(client, await confirmed_draft(client, "two"), "two-plan")
    assert (await confirm(client, two, "two-save")).status_code == 201
    assert (await client.get(f"/api/v1/tasks/{task_id}")).json()["data"] == old
    for a in one["sessions"]:
        for b in two["sessions"]:
            a_start, a_end = [
                datetime.fromisoformat(a[k].replace("Z", "+00:00"))
                for k in ("scheduled_start", "scheduled_end")
            ]
            b_start, b_end = [
                datetime.fromisoformat(b[k].replace("Z", "+00:00"))
                for k in ("scheduled_start", "scheduled_end")
            ]
            assert (
                a_end + timedelta(minutes=15) <= b_start or b_end + timedelta(minutes=15) <= a_start
            )


async def test_expired_candidate_requires_review_without_saving(api, monkeypatch):
    client, factory = api
    plan = await candidate(client, await confirmed_draft(client))
    first = datetime.fromisoformat(plan["sessions"][0]["scheduled_start"].replace("Z", "+00:00"))

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return (first + timedelta(seconds=1)).astimezone(tz)

    monkeypatch.setattr(plan_service, "datetime", Later)
    response = await confirm(client, plan)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANDIDATE_EXPIRED"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0
        assert (
            await db.scalar(select(CommandReceipt).where(CommandReceipt.command_id == "confirm"))
            is None
        )


async def test_manual_busy_context_for_draft_availability(api):
    client, _ = api
    draft_id = await confirmed_draft(client)
    draft = (await client.get(f"/api/v1/drafts/{draft_id}")).json()["data"]
    now = datetime.now(UTC)
    busy_end = now + timedelta(hours=3)
    response = await post(
        client,
        "/planning-contexts",
        "busy",
        {
            "source_draft_id": draft_id,
            "expected_summary_revision": draft["summary_revision"],
            "from": now.isoformat(),
            "to": (now + timedelta(days=6)).isoformat(),
            "manual_busy_intervals": [
                {
                    "client_interval_id": "meeting",
                    "start_at": now.isoformat(),
                    "end_at": busy_end.isoformat(),
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    plan = await candidate(client, draft_id)
    assert all(
        datetime.fromisoformat(s["scheduled_start"].replace("Z", "+00:00")) >= busy_end
        for s in plan["sessions"]
    )


async def test_reminder_settings_accept_three_distinct_offsets(api):
    client, _ = api
    await client.get("/api/v1/preferences")
    payload = {
        "expected_preferences_revision": 1,
        "changes": {"preferences": {"reminder_offsets": [900, 600, 300]}},
    }
    response = await post(client, "/settings/change-requests", "reminders", payload)
    assert response.status_code == 200, response.text
    assert (await client.get("/api/v1/preferences")).json()["data"]["reminder_offsets"] == [
        900,
        600,
        300,
    ]
    payload["changes"]["preferences"]["reminder_offsets"] = [900, 900]
    assert (
        await post(client, "/settings/change-requests", "duplicate-reminders", payload)
    ).status_code == 422


async def test_incomplete_summary_cannot_generate_plan(api):
    client, factory = api
    response = await post(
        client,
        "/drafts",
        "incomplete",
        {"client_draft_id": "incomplete", "input_source": "text", "raw_input": "Viết báo cáo"},
    )
    draft_id = response.json()["data"]["draft_id"]
    response = await post(
        client,
        f"/drafts/{draft_id}/confirm-summary",
        "incomplete-summary",
        {"expected_summary_revision": 1},
    )
    assert response.status_code == 422
    assert {e["field_path"] for e in response.json()["error"]["field_errors"]} >= {
        "scope",
        "time_constraint",
        "availability",
    }
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        "incomplete-plan",
        {"source_summary_revision": 1, "source_planning_input_revision": 1},
    )
    assert response.status_code == 409
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0


async def test_analyze_raw_input_then_confirm_and_plan(api, monkeypatch):
    client, _ = api
    data = summary()
    response = await post(
        client,
        "/drafts",
        "raw",
        {"client_draft_id": "raw", "input_source": "text", "raw_input": "Viết báo cáo 5 trang"},
    )
    draft_id = response.json()["data"]["draft_id"]

    async def extracted(**kwargs):
        return LLMTaskExtractionPayload(
            title=data["title"],
            content=data["description"],
            scope=data["scope"],
            time_constraint=data["time_constraint"],
            summary="Báo cáo 5 trang",
            structured_free_windows=[
                {
                    "kind": "weekly_rule",
                    "weekday": i,
                    "start_local": "00:00",
                    "end_local": "23:59",
                    "raw_phrase": f"Thứ {i} từ 00:00 đến 23:59",
                    "is_ambiguous": False,
                }
                for i in range(1, 8)
            ],
            is_complete=True,
        ), "tier_1_gemini"

    with monkeypatch.context() as context:
        context.setattr(ai_service, "_execute_hedged_structured", extracted)
        response = await post(
            client,
            f"/drafts/{draft_id}/analyze",
            "analyze",
            {
                "expected_summary_revision": 1,
                "expected_planning_input_revision": 1,
                "message": "Đủ thông tin về phạm vi, thời gian và giờ rảnh",
            },
        )
    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["summary"]["can_plan"] is True
    assert draft["summary_confirmed_revision"] is None
    response = await post(
        client,
        f"/drafts/{draft_id}/confirm-summary",
        "analyzed-confirm",
        {
            "expected_summary_revision": draft["summary_revision"],
        },
    )
    assert response.status_code == 200, response.text
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        "analyzed-plan",
        {
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
        },
    )
    assert response.status_code == 201, response.text
    assert (await confirm(client, response.json()["data"])).status_code == 201


async def test_edit_candidate_recalculates_duration_and_rejects_old_revision(api):
    client, _ = api
    plan = await candidate(client, await confirmed_draft(client))
    step_key = plan["steps"][0]["client_step_key"]
    response = await client.patch(
        f"/api/v1/candidates/{plan['candidate_id']}",
        headers={"Idempotency-Key": "edit"},
        json={
            "expected_review_revision": 1,
            "edits": {
                "steps": [
                    {
                        "client_step_key": step_key,
                        "estimated_base_seconds": 600,
                    }
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["review_revision"] == 2
    assert (
        sum(s["work_seconds"] for s in updated["sessions"] if s["client_step_key"] == step_key)
        == 720
    )
    assert (await confirm(client, plan, "old-revision")).status_code == 409
    assert (await confirm(client, updated)).status_code == 201


async def test_global_availability_remains_usable(api):
    client, factory = api
    availability = await legacy_availability(client, factory)
    data = summary()
    data.pop("availability")
    data["availability_ref"] = {
        "availability_id": availability["availability_id"],
        "revision": availability["revision"],
    }
    plan = await candidate(client, await confirmed_draft(client, task_summary=data))
    assert (await confirm(client, plan)).status_code == 201


async def test_invalid_ai_decomposition_uses_python_fallback(api, monkeypatch):
    client, _ = api

    async def missing_output(**kwargs):
        return LLMTaskDecompositionPayload(
            task_title="Viết báo cáo",
            reasoning="Phương án chưa đủ thông tin",
            steps=[
                StepPromptItem(
                    order_index=1,
                    title="Viết nháp",
                    estimated_minutes=25,
                    micro_task_prompt="Mở tài liệu mới.",
                )
            ],
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", missing_output)
    plan = await candidate(client, await confirmed_draft(client))
    assert len(plan["steps"]) == 3
    assert all(step["expected_output"] for step in plan["steps"])
    assert (await confirm(client, plan)).status_code == 201


async def test_insufficient_time_reports_deficit_without_saving(api):
    client, factory = api
    data = summary()
    for rule in data["availability"]["weekly_rules"]:
        rule.update(start_local="09:00", end_local="09:01")
    plan = await candidate(client, await confirmed_draft(client, task_summary=data))
    assert plan["feasibility"]["status"] == "infeasible"
    assert plan["feasibility"]["deficit_seconds"] > 0
    assert (await confirm(client, plan)).status_code == 409
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0


async def test_edit_draft_invalidates_candidate_and_creates_new_snapshot(api):
    client, factory = api
    draft_id = await confirmed_draft(client)
    old = (await client.get(f"/api/v1/drafts/{draft_id}")).json()["data"]
    old_plan = await candidate(client, draft_id)
    availability = summary()["availability"]
    for rule in availability["weekly_rules"]:
        rule.update(start_local="18:00", end_local="23:59")
    response = await client.patch(
        f"/api/v1/drafts/{draft_id}",
        headers={"Idempotency-Key": "draft-edit"},
        json={
            "expected_summary_revision": 1,
            "expected_planning_input_revision": 1,
            "changes": {"summary": {"availability": availability}},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["summary_confirmed_revision"] is None
    assert (await confirm(client, old_plan, "outdated")).status_code == 409
    response = await post(
        client,
        f"/drafts/{draft_id}/confirm-summary",
        "new-summary",
        {
            "expected_summary_revision": 2,
        },
    )
    assert response.status_code == 200, response.text
    fresh = (await client.get(f"/api/v1/drafts/{draft_id}")).json()["data"]
    assert fresh["summary"]["availability"] != old["summary"]["availability"]
    assert fresh["summary_revision"] == old["summary_revision"] + 1
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        "new-plan",
        {
            "source_summary_revision": 2,
            "source_planning_input_revision": 2,
        },
    )
    assert response.status_code == 201, response.text
    assert (await confirm(client, response.json()["data"], "new-save")).status_code == 201
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 1


async def test_legacy_confirmed_draft_gets_availability_snapshot(api):
    client, _ = api
    draft_id = await confirmed_draft(client)
    before = (await client.get(f"/api/v1/drafts/{draft_id}")).json()["data"]
    plan = await candidate(client, draft_id)
    context_id = plan["source_revisions"]["calendar_context"]["context_id"]
    context = (await client.get(f"/api/v1/planning-contexts/{context_id}")).json()["data"]
    assert context["availability_snapshot"] == before["summary"]["availability"]
    assert context["source_summary_revision"] == before["summary_revision"]
    assert (await confirm(client, plan)).status_code == 201


async def busy_context(client, draft_id, key, start, end, busy_start, busy_end):
    return await post(
        client,
        "/planning-contexts",
        key,
        {
            "source_draft_id": draft_id,
            "expected_summary_revision": 1,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "manual_busy_intervals": [
                {
                    "client_interval_id": "meeting",
                    "start_at": busy_start.isoformat(),
                    "end_at": busy_end.isoformat(),
                }
            ],
        },
    )


async def test_partial_busy_context_cannot_be_replaced_by_empty_schedule(api):
    client, factory = api
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    data = summary()
    data["time_constraint"].update(
        start_at=start.isoformat(),
        deadline_at=(start + timedelta(days=3)).isoformat(),
        input_timezone="UTC",
    )
    data["availability"]["timezone"] = "UTC"
    draft_id = await confirmed_draft(client, task_summary=data)
    response = await busy_context(
        client,
        draft_id,
        "partial-busy",
        start,
        start + timedelta(days=1),
        start,
        start + timedelta(hours=2),
    )
    assert response.status_code == 201, response.text
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        "must-not-ignore-busy",
        {
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "manual_steps": [
                {
                    "order_index": 0,
                    "title": "Viết nháp",
                    "expected_output": "5 trang",
                    "estimated_base_seconds": 1500,
                }
            ],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CALENDAR_CONTEXT_UNAVAILABLE"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0
    response = await busy_context(
        client,
        draft_id,
        "full-busy",
        start,
        start + timedelta(days=4),
        start,
        start + timedelta(hours=2),
    )
    assert response.status_code == 201, response.text
    plan = await candidate(client, draft_id)
    assert all(
        datetime.fromisoformat(s["scheduled_start"].replace("Z", "+00:00"))
        >= start + timedelta(hours=2)
        for s in plan["sessions"]
    )
    assert (await confirm(client, plan)).status_code == 201


async def test_narrower_context_cannot_discard_busy_intervals(api):
    client, _ = api
    draft_id = await confirmed_draft(client)
    start = datetime.now(UTC)
    end = start + timedelta(days=6)
    response = await busy_context(
        client, draft_id, "wide", start, end, start, start + timedelta(hours=1)
    )
    assert response.status_code == 201
    context_id = response.json()["data"]["context_id"]
    response = await busy_context(
        client,
        draft_id,
        "narrow",
        start + timedelta(days=1),
        start + timedelta(days=2),
        start + timedelta(days=1),
        start + timedelta(days=1, hours=1),
    )
    assert response.status_code == 422, response.text
    old = (await client.get(f"/api/v1/planning-contexts/{context_id}")).json()["data"]
    assert old["sync_state"] == "valid"
    assert (await confirm(client, await candidate(client, draft_id))).status_code == 201


async def test_updated_busy_context_invalidates_old_candidate(api):
    client, _ = api
    draft_id = await confirmed_draft(client)
    old_plan = await candidate(client, draft_id)
    horizon = old_plan["planning_horizon"]
    start = datetime.fromisoformat(horizon["start_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(horizon["end_at"].replace("Z", "+00:00"))
    response = await busy_context(
        client,
        draft_id,
        "new-busy",
        start,
        end + timedelta(days=1),
        start,
        start + timedelta(hours=1),
    )
    assert response.status_code == 201, response.text
    loaded = (await client.get(f"/api/v1/candidates/{old_plan['candidate_id']}")).json()["data"]
    assert loaded["stale"] is True
    assert (await confirm(client, old_plan)).status_code == 409


async def test_old_mobile_null_part_selection_generates_plan(api):
    client, _ = api
    draft_id = await confirmed_draft(client)
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        "legacy-mobile",
        {
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "selected_part_keys": None,
            "planning_horizon": None,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["feasibility"]["status"] == "feasible"


async def test_nonempty_legacy_selection_is_not_guessed(api):
    client, _ = api
    draft_id = await confirmed_draft(client)
    response = await post(
        client,
        f"/drafts/{draft_id}/candidates",
        "legacy-part",
        {
            "source_summary_revision": 1,
            "source_planning_input_revision": 1,
            "selected_part_keys": ["part-1"],
        },
    )
    assert response.status_code == 422


async def test_uf1_deadline_without_start_at_and_resolved_ai_clarification_can_confirm(
    api, monkeypatch
):
    client, _ = api
    future_deadline = (datetime.now(UTC) + timedelta(days=5)).replace(microsecond=0)
    created = await post(
        client,
        "/drafts",
        "uf1-start-at-create",
        {
            "client_draft_id": "uf1-start-at-create",
            "input_source": "text",
            "raw_input": "Viết báo cáo 5 trang trước thứ 6 tuần này",
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    draft_id = draft["draft_id"]

    turn = 0

    async def extracted(**kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo môn học",
                scope="Hoàn thành 5 trang",
                time_constraint=ExtractedTimeConstraint(
                    kind="deadline",
                    start_at=None,
                    deadline_at=future_deadline,
                    input_timezone=None,
                ),
                free_time_slots=[],
                structured_free_windows=[],
                is_complete=False,
                summary="Đã có nội dung, phạm vi và hạn chót; thiếu giờ rảnh.",
                clarification_questions=["Bạn cần nộp định dạng PDF hay Word?"],
            ), "tier_1_gemini"
        return LLMTaskExtractionPayload(
            title="Viết báo cáo",
            content="Viết báo cáo môn học",
            scope="Hoàn thành 5 trang",
            time_constraint=ExtractedTimeConstraint(
                kind="deadline",
                start_at=None,
                deadline_at=future_deadline,
                input_timezone=None,
            ),
            free_time_slots=["Thứ 3 từ 19:00 đến 21:00"],
            structured_free_windows=[
                {
                    "kind": "weekly_rule",
                    "weekday": 2,
                    "start_local": "19:00",
                    "end_local": "21:00",
                    "raw_phrase": "Thứ 3 từ 19:00 đến 21:00",
                    "is_ambiguous": False,
                }
            ],
            is_complete=True,
            resolved_issue_keys=[],
            summary="Đã đủ 4 thông tin bắt buộc.",
            clarification_questions=[],
        ), "tier_1_gemini"

    with monkeypatch.context() as context:
        context.setattr(ai_service, "_execute_hedged_structured", extracted)
        turn1 = await post(
            client,
            f"/drafts/{draft_id}/analyze",
            "uf1-turn1",
            {
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "raw_input": "Viết báo cáo 5 trang trước thứ 6 tuần này",
            },
        )
        assert turn1.status_code == 200, turn1.text
        draft1 = turn1.json()["data"]
        assert draft1["summary"]["time_constraint"] is not None
        assert draft1["summary"]["time_constraint"]["start_at"] is not None
        assert not any(
            i["code"] == "AI_TIME_INCOMPLETE" for i in draft1["summary"]["clarification_issues"]
        )
        assert any(
            i["code"] == "AI_CLARIFICATION" and i["question"] == "Bạn cần nộp định dạng PDF hay Word?"
            for i in draft1["summary"]["clarification_issues"]
        )
        assert draft1["summary"]["can_plan"] is False

        turn2 = await post(
            client,
            f"/drafts/{draft_id}/analyze",
            "uf1-turn2",
            {
                "expected_summary_revision": draft1["summary_revision"],
                "expected_planning_input_revision": draft1["planning_input_revision"],
                "message": "Nộp PDF, mình rảnh tối thứ 3 từ 19h đến 21h",
            },
        )
        assert turn2.status_code == 200, turn2.text
        draft2 = turn2.json()["data"]
        assert draft2["summary"]["can_plan"] is True
        assert not any(i["blocking"] for i in draft2["summary"]["clarification_issues"])

    confirmed = await post(
        client,
        f"/drafts/{draft_id}/confirm-summary",
        "uf1-confirm",
        {"expected_summary_revision": draft2["summary_revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
