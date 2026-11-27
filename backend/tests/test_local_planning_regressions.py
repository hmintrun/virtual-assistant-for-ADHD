"""Fixtures riêng cho C01: SQLite tạm, hai user, AI giả; không dùng DB app."""

import asyncio
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
from app.schemas.draft import DraftAnalyzeRequest
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMCandidateAdjustmentPayload,
    LLMTaskExtractionPayload,
)
from app.schemas.user import CurrentUser
from app.services import draft_service, plan_service
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
@pytest.mark.parametrize(
    ("code", "message", "question", "expected_status"),
    [
        pytest.param(
            "SCOPE_CONFLICT",
            "Nhóm yêu cầu ít nhất 4 trang, nhưng cổng nộp giới hạn tối đa 2 trang.",
            "Bạn muốn chốt giới hạn tối đa 2 trang hay yêu cầu tối thiểu 4 trang?",
            422,
            id="keep-conflict",
        ),
        pytest.param(
            "MISSING_SCOPE", "Xem lại yêu cầu.", "Bạn cần viết bao nhiêu trang?", 200,
            id="drop-missing-scope",
        ),
        pytest.param(
            "SCOPE_REQUIRED", "Xem lại yêu cầu.", "Bạn cần viết bao nhiêu trang?", 200,
            id="drop-scope-required",
        ),
    ],
)
async def test_scope_issue_with_existing_scope(
    env, payload, monkeypatch, code, message, question, expected_status
):
    client = env["client"]
    response = await client.post("/api/v1/drafts", json=payload, headers=headers())
    assert response.status_code == 201, response.text
    draft = response.json()["data"]
    original = payload["summary"]

    async def provider(**kwargs):
        return LLMTaskExtractionPayload(
            title=original["title"],
            content=original["description"],
            scope=original["scope"],
            deadline=original["time_constraint"]["deadline_at"],
            free_time_slots=["09:00–18:00"],
            summary="Kiểm tra phạm vi của bản nháp.",
            clarification_issues=[
                {
                    "code": code,
                    "field_path": "scope",
                    "message": question,
                    "question": question,
                    "blocking": True,
                }
            ],
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    url = f"/api/v1/drafts/{draft['draft_id']}"
    response = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": message,
        },
    )
    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["summary"]["scope"] == original["scope"]
    assert draft["summary"]["can_plan"] is (expected_status == 200)
    issues = draft["summary"]["clarification_issues"]
    if expected_status == 422:
        assert [(i["code"], i["field_path"], i["question"], i["blocking"]) for i in issues] == [
            ("AI_SCOPE_CONFLICT", "scope", question, True)
        ]
    else:
        assert issues == []

    response = await client.post(
        f"{url}/confirm-summary",
        headers=headers(),
        json={"expected_summary_revision": draft["summary_revision"]},
    )
    assert response.status_code == expected_status, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_old", [False, True], ids=["replace-deadline", "confirm-old-deadline"])
async def test_fallback_requires_review_for_unapplied_edit(env, payload, monkeypatch, keep_old):
    client = env["client"]
    created = await client.post("/api/v1/drafts", json=payload, headers=headers())
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    url = f"/api/v1/drafts/{draft['draft_id']}"
    old = draft["summary"]["time_constraint"]
    target = datetime.fromisoformat(old["deadline_at"]) + timedelta(hours=2)
    message = f"Đổi hạn chót sang {target:%H:%M} ngày {target:%d/%m/%Y}."

    async def timeout(**kwargs):
        assert kwargs["profile"].hard_timeout == 10
        raise TimeoutError("controlled timeout")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", timeout)
    analyze_body = {
        "expected_summary_revision": draft["summary_revision"],
        "expected_planning_input_revision": draft["planning_input_revision"],
        "raw_input": payload["raw_input"],
        "message": message,
    }
    analyze_headers = headers()
    analyzed = await client.post(f"{url}/analyze", headers=analyze_headers, json=analyze_body)
    assert analyzed.status_code == 200, analyzed.text
    draft = analyzed.json()["data"]
    assert draft["summary"]["time_constraint"] == old
    assert draft["summary"]["extra_requirements"] == payload["summary"]["extra_requirements"]
    assert message in draft["raw_input"]
    assert draft["summary"]["can_plan"] is False
    assert [
        (i["code"], i["field_path"]) for i in draft["summary"]["clarification_issues"] if i["blocking"]
    ] == [("AI_FALLBACK_REVIEW_REQUIRED", "time_constraint")]
    replay = await client.post(f"{url}/analyze", headers=analyze_headers, json=analyze_body)
    assert replay.status_code == 200 and replay.json()["data"] == draft
    confirm = await client.post(
        f"{url}/confirm-summary", headers=headers(),
        json={"expected_summary_revision": draft["summary_revision"]},
    )
    assert confirm.status_code == 422, confirm.text

    selected = old if keep_old else (old | {"deadline_at": target.isoformat()})
    reviewed = await client.patch(
        url, headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "changes": {"summary": {"time_constraint": selected}},
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    draft = reviewed.json()["data"]
    assert draft["summary"]["can_plan"] is True
    assert datetime.fromisoformat(draft["summary"]["time_constraint"]["deadline_at"]) == (
        datetime.fromisoformat(old["deadline_at"]) if keep_old else target
    )
    confirm = await client.post(
        f"{url}/confirm-summary", headers=headers(),
        json={"expected_summary_revision": draft["summary_revision"]},
    )
    assert confirm.status_code == 200, confirm.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection_field", "selection"),
    [
        ("message", "Chốt làm báo cáo hai trang, không làm slide."),
        ("raw_input", "Chốt làm báo cáo hai trang, không làm slide."),
        ("message", "2 trang thôi."),
    ],
)
async def test_uf1_explicitly_unchosen_scope_waits_for_user_selection(
    env, payload, monkeypatch, selection_field, selection
):
    client = env["client"]
    scope = payload["summary"].pop("scope")
    created = await client.post("/api/v1/drafts", json=payload, headers=headers())
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    url = f"/api/v1/drafts/{draft['draft_id']}"
    returned_scopes = iter(["2 trang hoặc 6 slide", "2 trang", scope, scope])

    async def provider(**kwargs):
        assert kwargs["profile"].hard_timeout == 6
        return LLMTaskExtractionPayload(
            scope=next(returned_scopes), summary="Đầu ra cho buổi demo"
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    for message, ready in (
        ("Có thể làm 2 trang hoặc 6 slide, tôi chưa chốt đầu ra.", False),
        ("Thêm ghi chú giữ trích dẫn.", False),
        (selection, True),
        (None, True),
    ):
        field = "raw_input" if message is None else selection_field if ready else "message"
        response = await client.post(
            f"{url}/analyze", headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                field: draft["raw_input"] if message is None else message,
            },
        )
        assert response.status_code == 200, response.text
        draft = response.json()["data"]
        assert draft["summary"]["can_plan"] is ready
        assert draft["summary"]["scope"] == (scope if ready else None)
        issues = draft["summary"]["clarification_issues"]
        assert any(i["code"] == "AI_SCOPE_CHOICE_REQUIRED" and i["blocking"] for i in issues) is not ready


@pytest.mark.asyncio
async def test_fallback_review_chat_keep_old_and_short_update_and_unrelated_followup(
    env, payload, monkeypatch
):
    client = env["client"]
    start = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    old_deadline = start + timedelta(hours=9)
    new_deadline = old_deadline + timedelta(hours=3)

    # 1. AI-suggested deadline -> colloquial timeout ("Dời sang...") -> unrelated stays blocked
    #    -> short update ("22:00 ngày ...") resolves cleanly without repeating "đổi hạn".
    raw_draft_res = await client.post(
        "/api/v1/drafts",
        headers=headers(),
        json={
            "client_draft_id": uuid4().hex,
            "input_source": "text",
            "raw_input": "Viết báo cáo 2 trang, rảnh 09:00-18:00",
        },
    )
    assert raw_draft_res.status_code == 201, raw_draft_res.text
    d_ai = raw_draft_res.json()["data"]
    url_ai = f"/api/v1/drafts/{d_ai['draft_id']}"

    turn_mode = "ai_initial"
    seen_edit_fields: list[tuple[str, ...]] = []
    real_extract = ai_service.extract_task_intent

    async def spy_extract(*args, **kwargs):
        seen_edit_fields.append(tuple(kwargs.get("requested_edit_fields") or ()))
        return await real_extract(*args, **kwargs)

    async def provider_ai(**kwargs):
        if turn_mode == "timeout":
            assert kwargs["profile"].hard_timeout == 6
            raise TimeoutError("simulated timeout")
        if turn_mode == "ai_initial":
            return LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo 2 trang",
                scope="Báo cáo hai trang",
                deadline=old_deadline,
                free_time_slots=["09:00-18:00"],
                structured_free_windows=[
                    {
                        "kind": "specific_date",
                        "date": start.date().isoformat(),
                        "start_local": "09:00",
                        "end_local": "18:00",
                        "raw_phrase": f"ngày {start.date().isoformat()} 09:00-18:00",
                    }
                ],
                is_complete=True,
                summary="Đủ thông tin ban đầu.",
            ), "tier_1_gemini"
        if turn_mode == "unrelated":
            assert kwargs["profile"].hard_timeout == 8
            return LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo 2 trang",
                scope="Báo cáo hai trang",
                deadline=old_deadline,
                extra_requirements=["Giữ trích dẫn"],
                is_complete=True,
                summary="Thêm ghi chú.",
            ), "tier_1_gemini"
        # short update turn
        assert "time_constraint" in seen_edit_fields[-1]
        return LLMTaskExtractionPayload(
            title="Viết báo cáo",
            content="Viết báo cáo 2 trang",
            scope="Báo cáo hai trang",
            deadline=new_deadline,
            is_complete=True,
            summary="Đã cập nhật hạn mới.",
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "extract_task_intent", spy_extract)
    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_ai)
    r0 = await client.post(
        f"{url_ai}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": d_ai["summary_revision"],
            "expected_planning_input_revision": d_ai["planning_input_revision"],
            "raw_input": d_ai["raw_input"],
        },
    )
    assert r0.status_code == 200, r0.text
    d_ai = r0.json()["data"]
    assert d_ai["summary"]["can_plan"] is True

    turn_mode = "timeout"
    r1 = await client.post(
        f"{url_ai}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": d_ai["summary_revision"],
            "expected_planning_input_revision": d_ai["planning_input_revision"],
            "message": f"Dời sang {new_deadline:%H:%M} ngày {new_deadline:%d/%m/%Y}",
        },
    )
    assert r1.status_code == 200, r1.text
    d_ai = r1.json()["data"]
    assert d_ai["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_FALLBACK_REVIEW_REQUIRED" and i["field_path"] == "time_constraint"
        for i in d_ai["summary"]["clarification_issues"]
    )
    c_fail = await client.post(
        f"{url_ai}/confirm-summary",
        headers=headers(),
        json={"expected_summary_revision": d_ai["summary_revision"]},
    )
    assert c_fail.status_code == 422

    # Unrelated follow-up without changing deadline must keep AI_FALLBACK_REVIEW_REQUIRED.
    turn_mode = "unrelated"
    r2 = await client.post(
        f"{url_ai}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": d_ai["summary_revision"],
            "expected_planning_input_revision": d_ai["planning_input_revision"],
            "message": "Thêm ghi chú giữ trích dẫn",
        },
    )
    assert r2.status_code == 200, r2.text
    d_ai = r2.json()["data"]
    assert d_ai["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_FALLBACK_REVIEW_REQUIRED" and i["field_path"] == "time_constraint"
        for i in d_ai["summary"]["clarification_issues"]
    )

    # Short chat update without verb resolves pending fallback edit on AI-suggested field.
    turn_mode = "short_update"
    r3 = await client.post(
        f"{url_ai}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": d_ai["summary_revision"],
            "expected_planning_input_revision": d_ai["planning_input_revision"],
            "message": f"{new_deadline:%H:%M} ngày {new_deadline:%d/%m/%Y}",
        },
    )
    assert r3.status_code == 200, r3.text
    d_ai = r3.json()["data"]
    assert d_ai["summary"]["can_plan"] is True
    assert not any(i["blocking"] for i in d_ai["summary"]["clarification_issues"])
    assert datetime.fromisoformat(d_ai["summary"]["time_constraint"]["deadline_at"]) == new_deadline
    c_ok = await client.post(
        f"{url_ai}/confirm-summary",
        headers=headers(),
        json={"expected_summary_revision": d_ai["summary_revision"]},
    )
    assert c_ok.status_code == 200, c_ok.text

    # 2. User-locked field -> timeout -> "Giữ nguyên thời gian hiện tại" clears block,
    #    and short update on another locked draft transitions cleanly to AI_TIME_REVIEW_REQUIRED.
    for action in ("keep_old", "short_update_locked"):
        locked_create = await client.post("/api/v1/drafts", json=payload | {"client_draft_id": uuid4().hex}, headers=headers())
        assert locked_create.status_code == 201, locked_create.text
        d_locked = locked_create.json()["data"]
        url_locked = f"/api/v1/drafts/{d_locked['draft_id']}"
        locked_old = datetime.fromisoformat(d_locked["summary"]["time_constraint"]["deadline_at"])
        locked_new = locked_old + timedelta(hours=2)
        locked_state = {"mode": "timeout"}

        async def provider_locked(
            *, _state=locked_state, _old=locked_old, _new=locked_new, **kwargs
        ):
            if _state["mode"] == "timeout":
                assert kwargs["profile"].hard_timeout == 10
                raise TimeoutError("simulated timeout")
            if _state["mode"] == "propose":
                assert kwargs["profile"].hard_timeout == 10
            return LLMTaskExtractionPayload(
                title=payload["summary"]["title"],
                content=payload["summary"]["description"],
                scope=payload["summary"]["scope"],
                deadline=_old if _state["mode"] == "keep" else _new,
                is_complete=True,
                summary="Xử lý phản hồi.",
            ), "tier_1_gemini"

        monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_locked)
        res_to = await client.post(
            f"{url_locked}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": d_locked["summary_revision"],
                "expected_planning_input_revision": d_locked["planning_input_revision"],
                "message": f"Dời sang {locked_new:%H:%M} ngày {locked_new:%d/%m/%Y}",
            },
        )
        assert res_to.status_code == 200, res_to.text
        d_locked = res_to.json()["data"]
        assert d_locked["summary"]["can_plan"] is False
        assert any(
            i["code"] == "AI_FALLBACK_REVIEW_REQUIRED" and i["field_path"] == "time_constraint"
            for i in d_locked["summary"]["clarification_issues"]
        )

        if action == "keep_old":
            locked_state["mode"] = "keep"
            res_keep = await client.post(
                f"{url_locked}/analyze",
                headers=headers(),
                json={
                    "expected_summary_revision": d_locked["summary_revision"],
                    "expected_planning_input_revision": d_locked["planning_input_revision"],
                    "message": "Giữ nguyên thời gian hiện tại",
                },
            )
            assert res_keep.status_code == 200, res_keep.text
            d_locked = res_keep.json()["data"]
            assert d_locked["summary"]["can_plan"] is True
            assert datetime.fromisoformat(d_locked["summary"]["time_constraint"]["deadline_at"]) == locked_old
            res_cf = await client.post(
                f"{url_locked}/confirm-summary",
                headers=headers(),
                json={"expected_summary_revision": d_locked["summary_revision"]},
            )
            assert res_cf.status_code == 200, res_cf.text
        else:
            locked_state["mode"] = "propose"
            res_prop = await client.post(
                f"{url_locked}/analyze",
                headers=headers(),
                json={
                    "expected_summary_revision": d_locked["summary_revision"],
                    "expected_planning_input_revision": d_locked["planning_input_revision"],
                    "message": f"{locked_new:%H:%M} ngày {locked_new:%d/%m/%Y}",
                },
            )
            assert res_prop.status_code == 200, res_prop.text
            d_locked = res_prop.json()["data"]
            codes = {i["code"] for i in d_locked["summary"]["clarification_issues"]}
            assert "AI_TIME_REVIEW_REQUIRED" in codes
            assert "AI_FALLBACK_REVIEW_REQUIRED" not in codes


@pytest.mark.asyncio
async def test_patch_multi_field_keeps_old_values_when_all_fields_under_review(
    env, payload, monkeypatch
):
    client = env["client"]
    created = await client.post("/api/v1/drafts", json=payload, headers=headers())
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    url = f"/api/v1/drafts/{draft['draft_id']}"

    async def timeout(**kwargs):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", timeout)
    analyzed = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": "Đổi tiêu đề mới và đổi sang 10 trang",
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    draft = analyzed.json()["data"]
    assert {
        (i["code"], i["field_path"])
        for i in draft["summary"]["clarification_issues"]
        if i["blocking"]
    } == {
        ("AI_FALLBACK_REVIEW_REQUIRED", "title"),
        ("AI_FALLBACK_REVIEW_REQUIRED", "scope"),
    }

    # Sending unchanged scope alongside changed availability must NOT dismiss unrelated scope review.
    partial_patch = await client.patch(
        url,
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "changes": {
                "summary": {
                    "scope": draft["summary"]["scope"],
                    "availability": {
                        "timezone": "UTC",
                        "weekly_rules": [],
                        "date_overrides": [
                            {
                                "date": payload["summary"]["availability"]["date_overrides"][0]["date"],
                                "intervals": [{"start_local": "10:00", "end_local": "17:00"}],
                            }
                        ],
                    },
                }
            },
        },
    )
    assert partial_patch.status_code == 200, partial_patch.text
    draft = partial_patch.json()["data"]
    assert draft["summary"]["can_plan"] is False
    assert {
        (i["code"], i["field_path"])
        for i in draft["summary"]["clarification_issues"]
        if i["blocking"]
    } == {
        ("AI_FALLBACK_REVIEW_REQUIRED", "title"),
        ("AI_FALLBACK_REVIEW_REQUIRED", "scope"),
    }

    # Sending both pending fields unchanged in one PATCH confirms keeping old values for both.
    reviewed = await client.patch(
        url,
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "changes": {
                "summary": {
                    "title": draft["summary"]["title"],
                    "scope": draft["summary"]["scope"],
                }
            },
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    draft = reviewed.json()["data"]
    assert draft["summary"]["can_plan"] is True
    assert not any(i["blocking"] for i in draft["summary"]["clarification_issues"])
    confirmed = await client.post(
        f"{url}/confirm-summary",
        headers=headers(),
        json={"expected_summary_revision": draft["summary_revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        "Mình chọn 2 trang",
        "2 trang Word",
        "Làm báo cáo 2 trang",
        "Phương án 1",
    ],
)
async def test_uf1_scope_choice_natural_vietnamese_and_preserves_extra_requirements(
    env, payload, monkeypatch, selection
):
    client = env["client"]
    payload["summary"].pop("scope")
    created = await client.post(
        "/api/v1/drafts",
        json=payload | {"raw_input": "Làm 2 trang hoặc 6 slide, tôi chưa chốt, tối đa 2 giờ mỗi ngày."},
        headers=headers(),
    )
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    url = f"/api/v1/drafts/{draft['draft_id']}"

    # Patch an unrelated field before first analyze (summary_revision == 2, never_analyzed == True).
    pre_patch = await client.patch(
        url,
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "changes": {"summary": {"title": "Báo cáo tổng kết"}},
        },
    )
    assert pre_patch.status_code == 200, pre_patch.text
    draft = pre_patch.json()["data"]
    assert draft["summary_revision"] == 2

    responses = iter(
        [
            LLMTaskExtractionPayload(
                scope="2 trang hoặc 6 slide",
                extra_requirements=["2 trang hoặc 6 slide", "tối đa 2 giờ mỗi ngày"],
                summary="Chưa chốt phạm vi.",
            ),
            LLMTaskExtractionPayload(
                scope="2 trang",
                extra_requirements=["giữ trích dẫn"],
                summary="Bổ sung ràng buộc.",
            ),
            LLMTaskExtractionPayload(
                scope="Báo cáo hai trang Word",
                summary="Đã chốt phạm vi.",
            ),
        ]
    )

    async def provider(**kwargs):
        return next(responses), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)

    # Turn 1 (first analyze with unchanged raw_input while summary_revision == 2)
    r1 = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "raw_input": draft["raw_input"],
        },
    )
    assert r1.status_code == 200, r1.text
    draft = r1.json()["data"]
    assert draft["summary"]["can_plan"] is False
    assert draft["summary"]["scope"] is None
    assert "tối đa 2 giờ mỗi ngày" in draft["summary"]["extra_requirements"]
    assert "2 trang hoặc 6 slide" not in draft["summary"]["extra_requirements"]
    assert any(i["code"] == "AI_SCOPE_CHOICE_REQUIRED" for i in draft["summary"]["clarification_issues"])

    # Turn 2: unrelated extra requirement still preserved while scope remains undecided
    r2 = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": "Nhớ giữ trích dẫn.",
        },
    )
    assert r2.status_code == 200, r2.text
    draft = r2.json()["data"]
    assert draft["summary"]["can_plan"] is False
    assert "giữ trích dẫn" in draft["summary"]["extra_requirements"]

    # Turn 3: natural Vietnamese scope selection clears blocker
    r3 = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": selection,
        },
    )
    assert r3.status_code == 200, r3.text
    draft = r3.json()["data"]
    assert draft["summary"]["can_plan"] is True
    assert draft["summary"]["scope"] == "Báo cáo hai trang Word"
    assert not any(
        i["code"] == "AI_SCOPE_CHOICE_REQUIRED" for i in draft["summary"]["clarification_issues"]
    )


@pytest.mark.asyncio
async def test_uf1_scope_choice_same_value_selection_and_non_scope_availability_no_false_positive(
    env, payload, monkeypatch
):
    client = env["client"]
    # 1. Existing AI-suggested scope ("2 trang") -> undecided -> "Chốt làm 2 trang" (same value) clears.
    created = await client.post(
        "/api/v1/drafts",
        json={
            "client_draft_id": uuid4().hex,
            "input_source": "text",
            "raw_input": "Viết báo cáo 2 trang, rảnh 09:00-18:00",
        },
        headers=headers(),
    )
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    url = f"/api/v1/drafts/{draft['draft_id']}"
    start = datetime.now(UTC) + timedelta(days=2)

    turns = iter(
        [
            LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo 2 trang",
                scope="2 trang",
                deadline=start + timedelta(hours=9),
                structured_free_windows=[
                    {
                        "kind": "daily",
                        "start_local": "09:00",
                        "end_local": "18:00",
                        "raw_phrase": "hằng ngày 09:00-18:00",
                    }
                ],
                is_complete=True,
                summary="Có scope 2 trang.",
            ),
            LLMTaskExtractionPayload(scope="2 trang hoặc 6 slide", summary="Chưa chốt."),
            LLMTaskExtractionPayload(scope="2 trang", summary="Chốt lại 2 trang."),
        ]
    )

    async def provider(**kwargs):
        return next(turns), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    for msg in (None, "Phân vân giữa 2 trang với 6 slide, tôi chưa chốt.", "Chốt làm 2 trang"):
        res = await client.post(
            f"{url}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                **({"raw_input": draft["raw_input"]} if msg is None else {"message": msg}),
            },
        )
        assert res.status_code == 200, res.text
        draft = res.json()["data"]

    assert draft["summary"]["scope"] == "2 trang"
    assert draft["summary"]["can_plan"] is True
    assert not any(
        i["code"] == "AI_SCOPE_CHOICE_REQUIRED" for i in draft["summary"]["clarification_issues"]
    )

    # 2. Negative check: undecided availability ("chưa biết rảnh thứ 5 hay thứ 6") with clear scope
    # must NOT falsely raise AI_SCOPE_CHOICE_REQUIRED.
    neg_created = await client.post(
        "/api/v1/drafts",
        json=payload | {"client_draft_id": uuid4().hex, "raw_input": "Tôi chưa biết rảnh thứ 5 hay thứ 6, làm 2 trang báo cáo"},
        headers=headers(),
    )
    assert neg_created.status_code == 201, neg_created.text
    neg_draft = neg_created.json()["data"]

    async def neg_provider(**kwargs):
        return LLMTaskExtractionPayload(
            title=payload["summary"]["title"],
            content=payload["summary"]["description"],
            scope=payload["summary"]["scope"],
            summary="Rõ phạm vi, chưa rõ giờ.",
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", neg_provider)
    neg_res = await client.post(
        f"/api/v1/drafts/{neg_draft['draft_id']}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": neg_draft["summary_revision"],
            "expected_planning_input_revision": neg_draft["planning_input_revision"],
            "raw_input": neg_draft["raw_input"],
        },
    )
    assert neg_res.status_code == 200, neg_res.text
    assert not any(
        i["code"] == "AI_SCOPE_CHOICE_REQUIRED"
        for i in neg_res.json()["data"]["summary"]["clarification_issues"]
    )


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


@pytest.mark.asyncio
async def test_p1_round2_duration_minutes_completes_open_ended_window(env, monkeypatch):
    client = env["client"]
    now_utc = datetime.now(UTC)
    tomorrow = (now_utc + timedelta(days=1)).date()
    day_after_tomorrow = tomorrow + timedelta(days=1)
    deadline = datetime(
        (tomorrow + timedelta(days=2)).year,
        (tomorrow + timedelta(days=2)).month,
        (tomorrow + timedelta(days=2)).day,
        23,
        59,
        tzinfo=UTC,
    )
    tc = ExtractedTimeConstraint(
        kind="deadline",
        start_at=now_utc,
        deadline_at=deadline,
        input_timezone="UTC",
    )

    def _override_map(availability: dict | None) -> dict[str, list[tuple[str, str]]]:
        assert availability is not None
        return {
            item["date"]: [(iv["start_local"][:5], iv["end_local"][:5]) for iv in item["intervals"]]
            for item in availability["date_overrides"]
        }

    # Case 1: AI returns duration_minutes=90 with start_local=None, end_local=None on Turn 2.
    # Case 2: AI returns structured_free_windows=[] on Turn 2 ("làm được tiếng rưỡi") -> Turn-2 synthesis.
    # Case 3: Overnight wrap ("tối mai ... rảnh sau 23h" + "làm được 2 tiếng") -> 23:00-23:59 & 00:00-01:00.
    scenarios = [
        (
            "ai-duration-field",
            "tối mai viết báo cáo rảnh sau 19h30",
            "19:30",
            "làm được 90 phút",
            [
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date=tomorrow.isoformat(),
                    start_local=None,
                    end_local=None,
                    duration_minutes=90,
                    raw_phrase="làm được 90 phút",
                )
            ],
            {tomorrow.isoformat(): [("19:30", "21:00")]},
        ),
        (
            "turn2-synthesis-empty-windows",
            "tối mai viết báo cáo rảnh sau 19h30",
            "19:30",
            "làm được tiếng rưỡi",
            [],
            {tomorrow.isoformat(): [("19:30", "21:00")]},
        ),
        (
            "overnight-wrap-synthesis",
            "tối mai viết báo cáo rảnh sau 23h",
            "23:00",
            "làm được 2 tiếng",
            [],
            {
                tomorrow.isoformat(): [("23:00", "23:59")],
                day_after_tomorrow.isoformat(): [("00:00", "01:00")],
            },
        ),
    ]

    for case_id, raw_text, start_hhmm, turn2_msg, turn2_windows, expected_map in scenarios:
        created = await client.post(
            "/api/v1/drafts",
            headers=headers(),
            json={
                "client_draft_id": f"{case_id}-{uuid4().hex}",
                "input_source": "text",
                "raw_input": raw_text,
            },
        )
        assert created.status_code == 201, created.text
        draft = created.json()["data"]
        url = f"/api/v1/drafts/{draft['draft_id']}"

        turns = iter(
            [
                LLMTaskExtractionPayload(
                    title="Viết báo cáo",
                    content="Viết báo cáo tiến độ",
                    scope="Báo cáo hai trang",
                    time_constraint=tc,
                    deadline=deadline,
                    structured_free_windows=[
                        ExtractedFreeWindowSchema(
                            kind="specific_date",
                            date=tomorrow.isoformat(),
                            start_local=start_hhmm,
                            end_local=None,
                            raw_phrase=raw_text,
                        )
                    ],
                    is_complete=False,
                    summary=f"Chờ giờ kết thúc từ {start_hhmm}.",
                ),
                LLMTaskExtractionPayload(
                    title="Viết báo cáo",
                    content="Viết báo cáo tiến độ",
                    scope="Báo cáo hai trang",
                    time_constraint=tc,
                    deadline=deadline,
                    structured_free_windows=turn2_windows,
                    is_complete=True,
                    summary="Đã bổ sung thời lượng.",
                ),
            ]
        )

        async def provider(_turns=turns, **kwargs):
            return next(_turns), "tier_1_gemini"

        monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)

        r1 = await client.post(
            f"{url}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "raw_input": raw_text,
            },
        )
        assert r1.status_code == 200, r1.text
        draft = r1.json()["data"]
        assert draft["summary"]["can_plan"] is False
        assert any(
            i["code"] == "AI_AVAILABILITY_INCOMPLETE" and start_hhmm in i["question"]
            for i in draft["summary"]["clarification_issues"]
        )

        r2 = await client.post(
            f"{url}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "message": turn2_msg,
            },
        )
        assert r2.status_code == 200, r2.text
        draft = r2.json()["data"]
        assert draft["summary"]["can_plan"] is True
        assert not any(i["blocking"] for i in draft["summary"]["clarification_issues"])
        assert _override_map(draft["summary"]["availability"]) == expected_map


@pytest.mark.asyncio
async def test_p1_round2_duration_edge_cases_and_clock_time_guards(env, monkeypatch):
    # 1. Clock-time expressions must NOT be misparsed as durations; explicit duration phrases still extract.
    for clock_phrase in (
        "10 giờ rưỡi",
        "tầm 9 giờ rưỡi nghỉ",
        "rảnh 8 giờ",
        "làm 9 giờ",
        "19 giờ",
        "19h30",
        "7 giờ tối",
    ):
        assert draft_service._extract_explicit_duration_minutes(clock_phrase) is None, clock_phrase

    assert draft_service._extract_explicit_duration_minutes("tiếng rưỡi") == 90
    assert draft_service._extract_explicit_duration_minutes("làm được 1 giờ rưỡi") == 90
    assert draft_service._extract_explicit_duration_minutes("làm được 2 giờ") == 120
    assert draft_service._extract_explicit_duration_minutes("2 giờ đồng hồ") == 120

    client = env["client"]
    now_utc = datetime.now(UTC)
    tomorrow = (now_utc + timedelta(days=1)).date()
    deadline = datetime(
        (tomorrow + timedelta(days=2)).year,
        (tomorrow + timedelta(days=2)).month,
        (tomorrow + timedelta(days=2)).day,
        23,
        59,
        tzinfo=UTC,
    )
    tc = ExtractedTimeConstraint(
        kind="deadline",
        start_at=now_utc,
        deadline_at=deadline,
        input_timezone="UTC",
    )

    async def _start_open_ended_draft(raw_text: str):
        created = await client.post(
            "/api/v1/drafts",
            headers=headers(),
            json={
                "client_draft_id": uuid4().hex,
                "input_source": "text",
                "raw_input": raw_text,
            },
        )
        assert created.status_code == 201, created.text
        draft = created.json()["data"]
        url = f"/api/v1/drafts/{draft['draft_id']}"
        t1 = LLMTaskExtractionPayload(
            title="Viết báo cáo",
            content="Viết báo cáo tiến độ",
            scope="Báo cáo hai trang",
            time_constraint=tc,
            deadline=deadline,
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date=tomorrow.isoformat(),
                    start_local="19:30",
                    end_local=None,
                    raw_phrase=raw_text,
                )
            ],
            is_complete=False,
            summary="Chờ giờ kết thúc từ 19:30.",
        )

        async def provider_t1(**kwargs):
            return t1, "tier_1_gemini"

        monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_t1)
        r1 = await client.post(
            f"{url}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "raw_input": raw_text,
            },
        )
        assert r1.status_code == 200, r1.text
        data = r1.json()["data"]
        assert data["summary"]["can_plan"] is False
        return url, data

    # 2. exclude / reset_override / replace must NOT trigger Turn-2 duration synthesis.
    for mode_label, turn2_msg, update_mode, turn2_windows in (
        (
            "exclude",
            "bỏ lịch mai dù còn 90 phút",
            "merge",
            [
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date=tomorrow.isoformat(),
                    op="exclude",
                    is_excluded=True,
                    raw_phrase="bỏ lịch mai",
                )
            ],
        ),
        (
            "reset_override",
            "xóa ngoại lệ mai 90 phút",
            "merge",
            [
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date=tomorrow.isoformat(),
                    op="reset_override",
                    raw_phrase="xóa ngoại lệ mai",
                )
            ],
        ),
        (
            "replace",
            "đổi lịch khác 90 phút",
            "replace",
            [],
        ),
    ):
        url, draft = await _start_open_ended_draft("tối mai rảnh sau 19h30")

        async def provider_non_synth(
            _label=mode_label, _mode=update_mode, _wins=turn2_windows, **kwargs
        ):
            return LLMTaskExtractionPayload(
                title="Viết báo cáo",
                content="Viết báo cáo tiến độ",
                scope="Báo cáo hai trang",
                time_constraint=tc,
                deadline=deadline,
                availability_update_mode=_mode,
                structured_free_windows=_wins,
                is_complete=False,
                summary=f"Cập nhật {_label}.",
            ), "tier_1_gemini"

        monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_non_synth)
        r2 = await client.post(
            f"{url}/analyze",
            headers=headers(),
            json={
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "message": turn2_msg,
            },
        )
        assert r2.status_code == 200, r2.text
        after = r2.json()["data"]
        assert after["summary"]["can_plan"] is False, mode_label
        overrides = (after["summary"]["availability"] or {}).get("date_overrides") or []
        assert all(
            (iv["start_local"][:5], iv["end_local"][:5]) != ("19:30", "21:00")
            for item in overrides
            for iv in item["intervals"]
        ), mode_label

    # 3. Clock-time Turn-2 reply ("tầm 9 giờ rưỡi nghỉ") with empty windows must NOT synthesize duration.
    url, draft = await _start_open_ended_draft("tối mai rảnh sau 19h30")

    async def provider_clock(**kwargs):
        return LLMTaskExtractionPayload(
            title="Viết báo cáo",
            content="Viết báo cáo tiến độ",
            scope="Báo cáo hai trang",
            time_constraint=tc,
            deadline=deadline,
            structured_free_windows=[],
            is_complete=False,
            summary="Chờ xác nhận.",
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_clock)
    r_clock = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": "tầm 9 giờ rưỡi nghỉ",
        },
    )
    assert r_clock.status_code == 200, r_clock.text
    assert r_clock.json()["data"]["summary"]["can_plan"] is False

    # 4. >200-char Turn-2 message with empty windows synthesizes safely without ValidationError.
    long_turn2_msg = "làm được 90 phút " + ("chi tiết rà soát tài liệu và kiểm thử " * 6)
    assert len(long_turn2_msg) > 200
    url, draft = await _start_open_ended_draft("tối mai rảnh sau 19h30")
    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_clock)
    r_long = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": long_turn2_msg,
        },
    )
    assert r_long.status_code == 200, r_long.text
    long_data = r_long.json()["data"]
    assert long_data["summary"]["can_plan"] is True
    assert long_data["summary"]["availability"]["weekly_rules"] == []
    assert {
        item["date"]: [(iv["start_local"][:5], iv["end_local"][:5]) for iv in item["intervals"]]
        for item in long_data["summary"]["availability"]["date_overrides"]
    } == {tomorrow.isoformat(): [("19:30", "21:00")]}

    # 5. Unanchored kind="daily" Turn-2 window preserves Turn-1 specific_date instead of leaking weekly_rules.
    url, draft = await _start_open_ended_draft("tối mai rảnh sau 19h30")

    async def provider_unanchored_daily(**kwargs):
        return LLMTaskExtractionPayload(
            title="Viết báo cáo",
            content="Viết báo cáo tiến độ",
            scope="Báo cáo hai trang",
            time_constraint=tc,
            deadline=deadline,
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="daily",
                    date=None,
                    weekday=None,
                    weekdays=[],
                    start_local=None,
                    end_local=None,
                    duration_minutes=90,
                    raw_phrase="làm được 90 phút",
                )
            ],
            is_complete=True,
            summary="Đã bổ sung 90 phút.",
        ), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider_unanchored_daily)
    r_daily = await client.post(
        f"{url}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": "làm được 90 phút",
        },
    )
    assert r_daily.status_code == 200, r_daily.text
    daily_data = r_daily.json()["data"]
    assert daily_data["summary"]["can_plan"] is True
    assert daily_data["summary"]["availability"]["weekly_rules"] == []
    assert {
        item["date"]: [(iv["start_local"][:5], iv["end_local"][:5]) for iv in item["intervals"]]
        for item in daily_data["summary"]["availability"]["date_overrides"]
    } == {tomorrow.isoformat(): [("19:30", "21:00")]}


@pytest.mark.asyncio
async def test_p1_long_existing_title_over_100_chars_reanalyzes_without_422_or_500(
    env, payload, monkeypatch
):
    client = env["client"]
    long_title = (
        "Báo cáo tổng kết kết quả nghiên cứu và đánh giá chi tiết hiệu năng hệ thống lập kế hoạch "
        "thông minh cho người dùng ADHD giai đoạn 2026"
    )
    assert len(long_title) > 100

    long_payload = {
        **payload,
        "client_draft_id": uuid4().hex,
        "summary": {
            **payload["summary"],
            "title": long_title,
        },
    }
    created = await client.post("/api/v1/drafts", json=long_payload, headers=headers())
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    assert draft["summary"]["title"] == long_title
    assert draft["summary"]["field_sources"]["title"]["source"] == "user_input"

    captured_previous_titles: list[str | None] = []

    async def provider(**kwargs):
        return LLMTaskExtractionPayload(
            title="Báo cáo tổng kết hiệu năng hệ thống 2026",
            content=payload["summary"]["description"],
            scope=payload["summary"]["scope"],
            summary="Đã rút gọn tiêu đề hợp lệ.",
        ), "tier_1_gemini"

    real_extract = ai_service.extract_task_intent

    async def spy_extract(*args, **kwargs):
        prev = kwargs.get("previous_draft")
        captured_previous_titles.append(prev.title if prev is not None else None)
        return await real_extract(*args, **kwargs)

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    monkeypatch.setattr(ai_service, "extract_task_intent", spy_extract)

    analyzed = await client.post(
        f"/api/v1/drafts/{draft['draft_id']}/analyze",
        headers=headers(),
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "message": "Giữ nguyên phạm vi và rút gọn tiêu đề giúp mình",
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    updated = analyzed.json()["data"]
    assert len(captured_previous_titles) == 1
    assert captured_previous_titles[0] is not None
    assert len(captured_previous_titles[0]) <= 100
    assert updated["summary"]["title"] is not None
    assert len(updated["summary"]["title"]) <= 100
    assert updated["summary"]["title"] == "Báo cáo tổng kết hiệu năng hệ thống 2026"
    assert not any(
        i["code"] == "AI_FIELD_REVIEW_REQUIRED" and i["field_path"] == "title"
        for i in updated["summary"]["clarification_issues"]
    )
    assert updated["summary"]["can_plan"] is True


@pytest.mark.asyncio
async def test_p1_concurrent_analyze_same_idempotency_key_invokes_llm_once(
    env, payload, monkeypatch
):
    client = env["client"]
    created = await client.post("/api/v1/drafts", json=payload, headers=headers())
    assert created.status_code == 201, created.text
    draft = created.json()["data"]

    extract_calls = 0
    real_extract = ai_service.extract_task_intent

    async def provider(**kwargs):
        return LLMTaskExtractionPayload(
            title=payload["summary"]["title"],
            content=payload["summary"]["description"],
            scope=payload["summary"]["scope"],
            extra_requirements=["Kiểm tra song song"],
            summary="Phân tích hoàn tất.",
        ), "tier_1_gemini"

    async def spy_extract(*args, **kwargs):
        nonlocal extract_calls
        extract_calls += 1
        await asyncio.sleep(0.05)
        return await real_extract(*args, **kwargs)

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", provider)
    monkeypatch.setattr(ai_service, "extract_task_intent", spy_extract)

    user_id = str(env["owner"])
    draft_id = draft["draft_id"]
    idem_key = f"p1-concurrent-{uuid4().hex}"
    req = DraftAnalyzeRequest(
        expected_summary_revision=draft["summary_revision"],
        expected_planning_input_revision=draft["planning_input_revision"],
        message="Thêm yêu cầu kiểm tra song song",
    )

    async def invoke_once():
        async with env["db"]() as db:
            return await draft_service.analyze_draft(
                user_id=user_id,
                draft_id=draft_id,
                idempotency_key=idem_key,
                payload=req,
                db=db,
            )

    res1, res2 = await asyncio.gather(invoke_once(), invoke_once())
    assert extract_calls == 1
    assert res1.model_dump(mode="json") == res2.model_dump(mode="json")
    assert res1.summary_revision == draft["summary_revision"] + 1
    assert "Kiểm tra song song" in res1.summary.extra_requirements
    assert (user_id, draft_id, idem_key) not in draft_service._INFLIGHT_ANALYZE_LOCKS
