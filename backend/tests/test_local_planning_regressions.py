"""Fixtures riêng cho C01: SQLite tạm, hai user, AI giả; không dùng DB app."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.api.v1.router import api_router
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import register_exception_handlers
from app.models import Base, Session, Task
from app.models.planning import PlanCandidate as CandidateRow
from app.models.user import User, UserPreference
from app.schemas.task import LLMCandidateAdjustmentPayload, LLMTaskExtractionPayload
from app.schemas.user import CurrentUser
from app.services import plan_service
from app.services.ai_service import ai_service


@pytest.fixture
def payload():
    start = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    return {
        "client_draft_id": uuid4().hex,
        "input_source": "text",
        "raw_input": "Viết báo cáo hai trang, rảnh 9–18h trong ngày được chọn.",
        "summary": {
            "title": "Viết báo cáo",
            "description": "Tổng hợp kết quả dự án",
            "scope": "Báo cáo hai trang, không làm slide",
            "extra_requirements": ["Giữ trích dẫn"],
            "time_constraint": {
                "kind": "deadline",
                "start_at": start.isoformat(),
                "deadline_at": (start + timedelta(hours=9)).isoformat(),
                "input_timezone": "UTC",
            },
            "availability": {
                "timezone": "UTC",
                "weekly_rules": [],
                "date_overrides": [
                    {
                        "date": start.date().isoformat(),
                        "intervals": [{"start_local": "09:00", "end_local": "18:00"}],
                    }
                ],
            },
        },
    }


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'c01.sqlite3').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    owner, other = uuid4(), uuid4()
    async with factory() as db:
        db.add_all(
            [
                User(id=owner, email="owner@example.test", display_name="Owner"),
                User(id=other, email="other@example.test", display_name="Other"),
            ]
        )
        await db.flush()
        db.add(
            UserPreference(
                user_id=owner,
                work_interval_seconds=1500,
                rest_interval_seconds=300,
                buffer_multiplier=1.2,
                effective_revision=1,
                micro_task_seconds=120,
                reminder_offsets=[900],
                reminder_style="gentle",
            )
        )
        await db.commit()
    calls = []

    async def unavailable(**kwargs):
        calls.append(kwargs)
        raise ConnectionError("Providers disabled in local regression")

    monkeypatch.setattr(plan_service.ai_service, "_execute_hedged_structured", unavailable)
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(api_router)

    async def db_override():
        async with factory() as db:
            yield db

    async def user_override(request: Request):
        return CurrentUser(
            user_id=str(other if request.headers.get("X-Test-User") == "other" else owner)
        )

    application.dependency_overrides[get_db] = db_override
    application.dependency_overrides[get_current_user] = user_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://c01.test"
        ) as client:
            yield {"client": client, "db": factory, "owner": owner, "other": other, "calls": calls}
    finally:
        await engine.dispose()


def headers(key=None):
    return {"Idempotency-Key": key or uuid4().hex}


async def ready(env, payload):
    response = await env["client"].post("/api/v1/drafts", json=payload, headers=headers())
    assert response.status_code == 201, response.text
    draft = response.json()["data"]
    response = await env["client"].post(
        f"/api/v1/drafts/{draft['draft_id']}/confirm-summary",
        json={"expected_summary_revision": draft["summary_revision"]},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    return draft


async def generate(env, draft):
    response = await env["client"].post(
        f"/api/v1/drafts/{draft['draft_id']}/candidates",
        json={
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
        },
        headers=headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_provider_failure_generates_c01_candidate_without_saving_official_plan(env, payload):
    draft = await ready(env, payload)
    plan = await generate(env, draft)
    assert len(plan["steps"]) == 3
    assert [step["order_index"] for step in plan["steps"]] == [0, 1, 2]
    assert all(step["estimated_base_seconds"] == 1500 for step in plan["steps"])
    assert plan["feasibility"]["status"] == "feasible"
    assert "planning_parts" not in plan
    async with env["db"]() as db:
        row = await db.get(CandidateRow, UUID(plan["candidate_id"]))
        assert row.generation_tier == "tier_3_rule_based"
        assert await db.scalar(select(func.count()).select_from(Task)) == 0
        assert await db.scalar(select(func.count()).select_from(Session)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "connection", "unknown_key", "duplicate_key"])
async def test_failed_nl_edit_keeps_candidate_and_draft_unchanged(
    env, payload, monkeypatch, failure
):
    draft = await ready(env, payload)
    plan = await generate(env, draft)
    url = f"/api/v1/candidates/{plan['candidate_id']}"
    draft_url = f"/api/v1/drafts/{draft['draft_id']}"
    before = (await env["client"].get(draft_url)).json()["data"]
    key = plan["steps"][0]["client_step_key"]

    async def provider(**kwargs):
        if failure == "timeout":
            raise TimeoutError("Provider timeout")
        if failure == "connection":
            raise ConnectionError("Provider unavailable")
        keys = ["unknown"] if failure == "unknown_key" else [key, key]
        return LLMCandidateAdjustmentPayload(
            step_edits=[{"client_step_key": item, "title": "Tên mới"} for item in keys],
            requires_clarification=False,
            clarification_question=None,
            requires_draft_update=False,
            unsupported_changes=[],
            assistant_note="Đổi tên",
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    request_headers = headers()
    body = {
        "expected_review_revision": plan["review_revision"],
        "user_instruction": "Đổi tên bước đầu",
    }
    for _ in range(2):
        response = await env["client"].patch(url, headers=request_headers, json=body)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_UNAVAILABLE"
        assert (await env["client"].get(url)).json()["data"] == plan
        assert (await env["client"].get(draft_url)).json()["data"] == before
    async with env["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(CandidateRow)) == 1
        assert await db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.asyncio
async def test_nl_keyed_edit_reschedules_and_replays_without_calling_ai(env, payload, monkeypatch):
    draft = await ready(env, payload)
    plan = await generate(env, draft)
    key = plan["steps"][1]["client_step_key"]
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs)
        return LLMCandidateAdjustmentPayload(
            step_edits=[{"client_step_key": key, "estimated_base_seconds": 600}],
            requires_clarification=False,
            clarification_question=None,
            requires_draft_update=False,
            unsupported_changes=[],
            assistant_note="Đổi thời lượng",
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    url = f"/api/v1/candidates/{plan['candidate_id']}"
    body = {
        "expected_review_revision": plan["review_revision"],
        "user_instruction": "Bước hai làm 10 phút",
    }
    request_headers = headers()
    response = await env["client"].patch(url, headers=request_headers, json=body)
    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["candidate_id"] == plan["candidate_id"]
    assert updated["review_revision"] == plan["review_revision"] + 1
    assert [step["client_step_key"] for step in updated["steps"]] == [
        step["client_step_key"] for step in plan["steps"]
    ]
    assert updated["steps"][0] == plan["steps"][0]
    assert updated["steps"][2] == plan["steps"][2]
    assert updated["steps"][1]["estimated_base_seconds"] == 600
    assert (
        sum(item["work_seconds"] for item in updated["sessions"] if item["client_step_key"] == key)
        == 720
    )
    assert updated["feasibility"]["fully_scheduled"] is True
    replay = await env["client"].patch(url, headers=request_headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == updated
    assert len(calls) == 1
    stale = await env["client"].patch(url, headers=headers(), json=body)
    assert stale.status_code == 409
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_structured_question_replaces_duplicate_generic_question(env, payload, monkeypatch):
    response = await env["client"].post("/api/v1/drafts", json=payload, headers=headers())
    draft = response.json()["data"]
    question = "Bạn muốn đầu ra ở định dạng nào?"
    other_question = "Bạn cần ghi chú nguồn theo chuẩn nào?"
    count = 0

    async def provider(**kwargs):
        nonlocal count
        count += 1
        issues = (
            []
            if count == 1
            else [
                {
                    "code": "SCOPE_DETAIL",
                    "field_path": "scope",
                    "message": question,
                    "question": question,
                    "blocking": True,
                }
            ]
        )
        return LLMTaskExtractionPayload(
            content=payload["summary"]["description"],
            scope=payload["summary"]["scope"],
            summary="Thông tin cần làm rõ",
            clarification_questions=[question, question, other_question],
            clarification_issues=issues,
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    for _ in range(2):
        response = await env["client"].post(
            f"/api/v1/drafts/{draft['draft_id']}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "message": "Xem lại yêu cầu",
            },
        )
        assert response.status_code == 200, response.text
        draft = response.json()["data"]
        questions = [issue["question"] for issue in draft["summary"]["clarification_issues"]]
        assert questions.count(question) == 1
        assert questions.count(other_question) == 1
        assert draft["summary"]["scope"] == payload["summary"]["scope"]
        assert draft["summary"]["can_plan"] is False
    matching = [
        issue for issue in draft["summary"]["clarification_issues"] if issue["question"] == question
    ]
    assert matching[0]["code"] == "AI_SCOPE_DETAIL"


@pytest.mark.asyncio
async def test_patch_nested_summary_omitted_and_null_keep_models_valid(env, payload):
    response = await env["client"].post("/api/v1/drafts", json=payload, headers=headers())
    draft = response.json()["data"]
    url = f"/api/v1/drafts/{draft['draft_id']}"
    for changes in ({"scope": "Báo cáo ba trang"}, {"availability": None}):
        body = {
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "changes": {"summary": changes},
        }
        response = await env["client"].patch(url, headers=headers(), json=body)
        assert response.status_code == 200, response.text
        updated = response.json()["data"]
        assert updated["summary"]["time_constraint"] == draft["summary"]["time_constraint"]
        if "availability" not in changes:
            assert updated["summary"]["availability"] == draft["summary"]["availability"]
        else:
            assert updated["summary"]["availability"] is None
            assert updated["summary"]["availability_ref"] is None
            assert updated["summary"]["can_plan"] is False
        assert (await env["client"].get(url)).json()["data"] == updated
        draft = updated


@pytest.mark.asyncio
@pytest.mark.parametrize("dictionary_ref", [False, True])
@pytest.mark.parametrize("current_revision", [2, 3])
async def test_legacy_availability_revision_supports_model_and_dictionary(
    payload, monkeypatch, dictionary_ref, current_revision
):
    from sqlalchemy import update

    from app.core.database import make_engine, session_factory
    from app.core.errors import AppError
    from app.models import Base, User, UserPreference
    from app.models.planning import PlanningDraft as PlanningDraftModel
    from app.models.settings import Availability as AvailabilityModel
    from app.schemas.common import AvailabilityRef
    from app.schemas.draft import ConfirmSummaryRequest, TaskSummary
    from app.services import draft_service

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    try:
        async with factory() as db:
            owner, draft_id = "legacy-owner", "legacy-draft"
            owner_uid = draft_service.resolve_user_uuid(owner)
            draft_uid = draft_service.resolve_draft_uuid(draft_id)
            avail_uid = uuid4()

            user = User(id=owner_uid, email="legacy@test.example", display_name="Legacy Owner")
            db.add(user)
            await db.flush()

            pref = UserPreference(
                user_id=owner_uid,
                work_interval_seconds=1500,
                rest_interval_seconds=300,
                effective_revision=1,
                buffer_multiplier=1.2,
                reminder_style="gentle",
                micro_task_seconds=120,
                reminder_offsets=[900],
            )
            avail = AvailabilityModel(
                id=avail_uid,
                user_id=owner_uid,
                timezone="UTC",
                weekly_rules=[],
                date_overrides=payload["summary"]["availability"]["date_overrides"],
                task_limits=[],
                source="user_confirmed",
                confirmed_at=datetime.now(UTC),
            )
            db.add(avail)
            await db.flush()
            await db.execute(
                update(AvailabilityModel)
                .where(AvailabilityModel.id == avail_uid)
                .values(revision=current_revision)
            )
            await db.commit()

            ref = AvailabilityRef(availability_id=str(avail_uid), revision=2)
            summary = TaskSummary.model_validate(
                {
                    **payload["summary"],
                    "availability": None,
                    "availability_ref": ref,
                    "clarification_issues": [],
                    "can_plan": True,
                    "field_sources": {},
                }
            )
            if dictionary_ref:
                summary = summary.model_copy(update={"availability_ref": ref.model_dump()})
            draft = draft_service._build_seed_draft(draft_id, "legacy-client").model_copy(
                update={
                    "summary": summary,
                    "summary_revision": 1,
                    "planning_input_revision": 1,
                    "summary_confirmed_revision": None,
                }
            )
            draft_row = PlanningDraftModel(
                id=draft_uid,
                user_id=owner_uid,
                client_draft_id=draft.client_draft_id,
                stage=draft.stage,
                raw_input=draft.raw_input,
                input_source=draft.input_source,
                summary=draft_service._serialize_summary_payload(draft),
                summary_revision=draft.summary_revision,
                summary_confirmed_revision=draft.summary_confirmed_revision,
                planning_input_revision=draft.planning_input_revision,
                user_step_proposal=draft.user_step_proposal,
            )
            db.add_all([pref, draft_row])
            await db.commit()

            async def confirm():
                return await draft_service.confirm_draft_summary(
                    user_id=owner,
                    draft_id=draft_id,
                    idempotency_key="legacy-confirm",
                    payload=ConfirmSummaryRequest(expected_summary_revision=1),
                    db=db,
                )

            if current_revision == 3:
                with pytest.raises(AppError) as error:
                    await confirm()
                assert error.value.code == "SOURCE_CHANGED"
                reloaded = await draft_service.get_draft_by_id(
                    user_id=owner, draft_id=draft_id, db=db
                )
                assert reloaded.summary_confirmed_revision is None
            else:
                confirmed = await confirm()
                assert confirmed.summary_confirmed_revision == 1
                reloaded = await draft_service.get_draft_by_id(
                    user_id=owner, draft_id=draft_id, db=db
                )
                assert reloaded.summary_confirmed_revision == 1
    finally:
        await engine.dispose()
