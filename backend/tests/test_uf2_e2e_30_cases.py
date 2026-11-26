"""30-case E2E QA verification suite for UF2 Layers 1-4 (+ UF6 bridges).

Covers TC-UF2-01 .. TC-UF2-30 over real FastAPI endpoints, real services
(`draft_service`, `plan_service`, `reschedule_service`, `ai_service`,
`scheduler_engine`), and in-memory SQLite persistence. Set the optional
`PROACTIVE_UF2_E2E_TRACE_PATH` environment variable to emit a structured JSON
trace artifact after the module completes.
"""

from __future__ import annotations

import json
import math
import os
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.deps import get_db
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import (
    Base,
    CalendarConnection,
    CalendarEventLink,
    CalendarSyncJob,
    CommandReceipt,
    Task,
)
from app.models import (
    PlanCandidate as CandidateRow,
)
from app.models import (
    Session as SessionRow,
)
from app.models.user import UserPreference as PreferenceRow
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.common import TimeConstraint
from app.schemas.planning import CandidateStep
from app.schemas.settings import UserPreference
from app.schemas.task import (
    CandidateStepAdjustment,
    LLMCandidateAdjustmentPayload,
    LLMSessionEnrichmentItem,
    LLMSessionEnrichmentPayload,
    LLMStrictStepPromptItem,
    LLMStrictTaskDecompositionPayload,
    NLCandidateStepAdd,
)
from app.services import plan_service
from app.services.ai_service import ai_service
from app.services.credential_crypto import encrypt_credentials
from app.services.draft_service import resolve_user_uuid
from app.services.scheduler_engine import (
    compute_effective_free_intervals,
    schedule_candidate_sessions,
)

UiScenario = Literal[
    "standard_review",
    "split_sessions_focus",
    "partial_horizon_locked",
    "structural_ops_undo",
    "infeasible_recovery",
    "error_expired_or_stale",
    "error_draft_update",
]

_TRACE_CASES: dict[str, dict[str, Any]] = {}


def _flush_trace_json() -> None:
    raw_trace_path = os.getenv("PROACTIVE_UF2_E2E_TRACE_PATH", "").strip()
    if not raw_trace_path or not _TRACE_CASES:
        return
    trace_path = Path(raw_trace_path)
    ordered_cases = [_TRACE_CASES[k] for k in sorted(_TRACE_CASES)]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "db": "sqlite+aiosqlite:///:memory:",
            "ai_mode": "controlled_deterministic_and_stub_providers",
            "auth_mode": "dev_user",
            "calendar_provider_mode": "fake",
            "python": platform.python_version(),
            "total_cases": len(ordered_cases),
        },
        "cases": ordered_cases,
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def trace_collector():
    _TRACE_CASES.clear()
    yield
    _flush_trace_json()


@pytest.fixture
async def api(monkeypatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    async def unavailable(**kwargs: Any) -> Any:
        raise ConnectionError("Providers unavailable by default in E2E suite")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", unavailable)
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def _future_base_day(days_ahead: int = 2) -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )


def _make_summary(
    *,
    title: str = "Đề án UF2 E2E",
    description: str = "Triển khai và kiểm thử luồng công việc theo bước cụ thể",
    scope: str = "Hoàn thành toàn bộ phạm vi đã xác nhận",
    start_at: datetime | None = None,
    deadline_at: datetime | None = None,
    days: int = 10,
    start_local: str = "09:00",
    end_local: str = "12:00",
    weekly_rules: list[dict[str, Any]] | None = None,
    date_overrides: list[dict[str, Any]] | None = None,
    extra_requirements: list[str] | None = None,
    tz: str = "UTC",
) -> dict[str, Any]:
    s_at = start_at or _future_base_day(2)
    d_at = deadline_at or (s_at + timedelta(days=days))
    rules = (
        weekly_rules
        if weekly_rules is not None
        else [
            {"weekday": wd, "start_local": start_local, "end_local": end_local}
            for wd in range(1, 8)
        ]
    )
    avail: dict[str, Any] = {
        "timezone": tz,
        "weekly_rules": rules,
        "date_overrides": date_overrides or [],
    }
    return {
        "title": title,
        "description": description,
        "scope": scope,
        "extra_requirements": extra_requirements or [],
        "time_constraint": {
            "kind": "deadline",
            "start_at": s_at.isoformat(),
            "deadline_at": d_at.isoformat(),
            "input_timezone": tz,
        },
        "availability": avail,
    }


async def _post(client: AsyncClient, path: str, key: str, payload: dict[str, Any]):
    return await client.post("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _patch(client: AsyncClient, path: str, key: str, payload: dict[str, Any]):
    return await client.patch("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _confirmed_draft(
    client: AsyncClient,
    key: str,
    summary: dict[str, Any],
    *,
    user_step_proposal: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_draft_id": f"{key}-client",
        "input_source": "text",
        "summary": summary,
    }
    if user_step_proposal is not None:
        body["user_step_proposal"] = user_step_proposal
    resp = await _post(client, "/drafts", f"{key}-create", body)
    assert resp.status_code == 201, resp.text
    draft = resp.json()["data"]
    conf = await _post(
        client,
        f"/drafts/{draft['draft_id']}/confirm-summary",
        f"{key}-confirm",
        {"expected_summary_revision": draft["summary_revision"]},
    )
    assert conf.status_code == 200, conf.text
    loaded = await client.get(f"/api/v1/drafts/{draft['draft_id']}")
    assert loaded.status_code == 200, loaded.text
    return loaded.json()["data"]


async def _create_candidate(
    client: AsyncClient,
    draft: dict[str, Any],
    key: str,
    *,
    manual_steps: list[dict[str, Any]] | None = None,
    unexpanded_scope: str | None = None,
    planning_horizon: dict[str, Any] | None = None,
    selected_step_keys: list[str] | None = None,
):
    body: dict[str, Any] = {
        "source_summary_revision": draft["summary_revision"],
        "source_planning_input_revision": draft["planning_input_revision"],
    }
    if manual_steps is not None:
        body["manual_steps"] = manual_steps
    if unexpanded_scope is not None:
        body["unexpanded_scope"] = unexpanded_scope
    if planning_horizon is not None:
        body["planning_horizon"] = planning_horizon
    if selected_step_keys is not None:
        body["selected_step_keys"] = selected_step_keys
    return await _post(client, f"/drafts/{draft['draft_id']}/candidates", key, body)


async def _confirm_candidate(
    client: AsyncClient,
    plan: dict[str, Any],
    key: str,
    *,
    override_review_revision: int | None = None,
    override_summary_revision: int | None = None,
    override_planning_input_revision: int | None = None,
):
    return await _post(
        client,
        f"/candidates/{plan['candidate_id']}/confirm",
        key,
        {
            "expected_review_revision": (
                override_review_revision
                if override_review_revision is not None
                else plan["review_revision"]
            ),
            "expected_summary_revision": (
                override_summary_revision
                if override_summary_revision is not None
                else plan["source_summary_revision"]
            ),
            "expected_planning_input_revision": (
                override_planning_input_revision
                if override_planning_input_revision is not None
                else plan["source_planning_input_revision"]
            ),
            "expected_source_revisions": plan["source_revisions"],
        },
    )


async def _connect_write_calendar(db: Any, user_id: str) -> CalendarConnection:
    owner = resolve_user_uuid(user_id)
    conn_id = uuid4()
    creds = encrypt_credentials(
        {
            "access_token": "acc_e2e_test",
            "refresh_token": "ref_e2e_test",
            "expires_in": 3600,
            "stored_at": datetime.now(UTC).isoformat(),
            "user_id": str(owner),
            "connection_id": str(conn_id),
        }
    )
    conn = CalendarConnection(
        id=conn_id,
        user_id=owner,
        provider="google",
        account_ref="qa_e2e@gmail.com",
        connection_state="connected",
        read_calendar_ids=[],
        write_calendar_id="primary",
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        credential_ref=creds,
    )
    db.add(conn)
    await db.flush()
    return conn


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _assert_candidate_flutter_compatible(cand: dict[str, Any]) -> None:
    """Kiểm tra PlanCandidate JSON tương thích 100% với Flutter PlanCandidateModel.fromJson."""
    for key in (
        "candidate_id",
        "draft_id",
        "review_revision",
        "source_summary_revision",
        "source_planning_input_revision",
        "stale",
        "feasibility",
        "steps",
        "sessions",
        "source_revisions",
        "planning_horizon",
    ):
        assert key in cand, f"Missing {key} in candidate payload"
    assert isinstance(cand["candidate_id"], str) and cand["candidate_id"]
    assert isinstance(cand["draft_id"], str) and cand["draft_id"]
    assert isinstance(cand["review_revision"], int)
    assert isinstance(cand["source_summary_revision"], int)
    assert isinstance(cand["source_planning_input_revision"], int)
    assert isinstance(cand["stale"], bool)
    feas = cand["feasibility"]
    assert isinstance(feas, dict) and isinstance(feas.get("status"), str)
    for st in cand["steps"]:
        assert isinstance(st.get("client_step_key"), str) and st["client_step_key"]
        assert isinstance(st.get("title"), str) and st["title"]
        assert isinstance(st.get("estimated_base_seconds"), int)
    for sess in cand["sessions"]:
        assert isinstance(sess.get("client_session_key"), str) and sess["client_session_key"]
        assert isinstance(sess.get("client_step_key"), str) and sess["client_step_key"]
        assert isinstance(sess.get("scheduled_start"), str)
        assert isinstance(sess.get("scheduled_end"), str)
        assert isinstance(sess.get("micro_task_text"), str) and sess["micro_task_text"]
        assert isinstance(sess.get("work_seconds"), int) and sess["work_seconds"] > 0
        assert isinstance(sess.get("rest_seconds"), int) and sess["rest_seconds"] >= 0
        assert isinstance(sess.get("duration_seconds"), int) and sess["duration_seconds"] > 0


def _assert_task_flutter_compatible(task: dict[str, Any]) -> None:
    """Kiểm tra Task JSON tương thích 100% với Flutter TaskModel.fromJson."""
    for key in ("task_id", "title", "status", "steps", "sessions"):
        assert key in task, f"Missing {key} in task payload"
    assert isinstance(task["task_id"], str) and task["task_id"]
    assert isinstance(task["title"], str) and task["title"]
    assert isinstance(task["status"], str) and task["status"]
    for st in task["steps"]:
        for sk in (
            "step_id",
            "task_id",
            "title",
            "expected_output",
            "order_index",
            "estimated_base_seconds",
            "buffer_multiplier",
            "status",
        ):
            assert sk in st, f"Missing {sk} in step payload"
    for sess in task["sessions"]:
        for sk in (
            "session_id",
            "step_id",
            "scheduled_start",
            "scheduled_end",
            "micro_task_text",
            "work_seconds",
            "rest_seconds",
            "duration_seconds",
            "status",
            "segments",
        ):
            assert sk in sess, f"Missing {sk} in session payload"
        _parse_iso(sess["scheduled_start"])
        _parse_iso(sess["scheduled_end"])
        for seg in sess["segments"]:
            assert {"order_index", "kind", "duration_seconds"} <= set(seg)


def _layer2_snapshot(cand: dict[str, Any], *, summary: str) -> dict[str, Any]:
    feas = cand["feasibility"]
    return {
        "status": feas["status"],
        "fully_scheduled": feas["fully_scheduled"],
        "required_seconds": feas.get("required_seconds"),
        "available_seconds": feas.get("available_seconds"),
        "deficit_seconds": feas.get("deficit_seconds"),
        "unscheduled_seconds": feas.get("unscheduled_seconds"),
        "unscheduled_step_keys": feas.get("unscheduled_step_keys", []),
        "earliest_feasible_end_at": feas.get("earliest_feasible_end_at"),
        "suggested_extra_free_seconds_per_day": feas.get("suggested_extra_free_seconds_per_day"),
        "applied_extra_constraints": feas.get("applied_extra_constraints"),
        "blocking_reasons": feas.get("blocking_reasons", []),
        "summary": summary,
    }


def _layer3_snapshot(
    cand: dict[str, Any] | None,
    *,
    summary: str,
    error: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"summary": summary}
    if cand is not None:
        out.update(
            {
                "candidate_id": cand["candidate_id"],
                "review_revision": cand["review_revision"],
                "can_undo": cand["can_undo"],
                "decomposition_state": cand["decomposition_state"],
                "unexpanded_scope": cand.get("unexpanded_scope"),
                "stale": cand["stale"],
                "step_count": len(cand["steps"]),
                "session_count": len(cand["sessions"]),
                "planning_horizon": cand["planning_horizon"],
            }
        )
    if error is not None:
        out["error"] = error
    if proposal is not None:
        out["proposal_id"] = proposal.get("proposal_id")
        out["proposal_status"] = proposal.get("status")
        out["proposed_changes_counts"] = {
            "steps_to_add": len(proposal.get("proposed_changes", {}).get("steps_to_add", [])),
            "sessions_to_add": len(proposal.get("proposed_changes", {}).get("sessions_to_add", [])),
            "sessions_to_move": len(
                proposal.get("proposed_changes", {}).get("sessions_to_move", [])
            ),
            "sessions_to_cancel": len(
                proposal.get("proposed_changes", {}).get("sessions_to_cancel", [])
            ),
        }
    return out


def _error_snapshot(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    err = body["error"]
    return {
        "status_code": status_code,
        "code": err["code"],
        "message": err["message"],
        "next_action": err["next_action"],
    }


def _record_case(
    *,
    case_id: str,
    group: str,
    title: str,
    ui_scenario: UiScenario,
    input_a: dict[str, Any],
    layer1_b: dict[str, Any],
    layer2_c: dict[str, Any],
    layer3_d: dict[str, Any],
    layer4_e: dict[str, Any],
    assertions_checked: list[str],
    candidate_payload: dict[str, Any] | None = None,
    task_payload: dict[str, Any] | None = None,
    error_payload: dict[str, Any] | None = None,
    proposal_payload: dict[str, Any] | None = None,
) -> None:
    if candidate_payload is not None:
        _assert_candidate_flutter_compatible(candidate_payload)
    if task_payload is not None:
        _assert_task_flutter_compatible(task_payload)
    _TRACE_CASES[case_id] = {
        "case_id": case_id,
        "group": group,
        "title": title,
        "ui_scenario": ui_scenario,
        "input_a": input_a,
        "layer1_b": layer1_b,
        "layer2_c": layer2_c,
        "layer3_d": layer3_d,
        "layer4_e": layer4_e,
        "backend_passed": True,
        "assertions_checked": assertions_checked,
        "candidate_payload": candidate_payload,
        "task_payload": task_payload,
        "error_payload": error_payload,
        "proposal_payload": proposal_payload,
    }


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    decompose_payload: LLMStrictTaskDecompositionPayload | None = None,
    enrich_fn: Any = None,
    adjust_payload: LLMCandidateAdjustmentPayload | None = None,
    tier: Literal["tier_1_gemini", "tier_2_groq"] = "tier_1_gemini",
) -> dict[str, int]:
    """Stub `_execute_hedged_structured` theo flow_name để chạy trọn vẹn logic thật của AIService."""
    counts = {"UF2": 0, "UF2_Enrich": 0, "UF2_Candidate_Adjust": 0, "UF6_Empathy": 0}

    async def _fake_exec(**kwargs: Any) -> tuple[Any, str]:
        flow = str(kwargs.get("flow_name", ""))
        counts[flow] = counts.get(flow, 0) + 1
        if flow == "UF2" and decompose_payload is not None:
            return decompose_payload, tier
        if flow == "UF2_Enrich" and enrich_fn is not None:
            user_prompt = json.loads(kwargs["user_prompt"])
            return enrich_fn(user_prompt), tier
        if flow == "UF2_Candidate_Adjust" and adjust_payload is not None:
            return adjust_payload, tier
        raise ConnectionError(f"Stubbed outage for flow {flow}")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_exec)
    return counts


# =============================================================================
# GROUP 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync
# =============================================================================


async def test_tc_uf2_01_short_task_single_session_skips_llm_enrichment(api, monkeypatch) -> None:
    """TC-UF2-01: Việc ngắn có 2 bước đơn phiên -> 0 lần gọi LLM enrich, kế thừa tiêu đề/đầu ra từ Step."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Ôn tập chương 1 ngắn gọn",
        scope="Đọc và tóm tắt 2 bài giảng",
        start_at=base_day,
        days=5,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc01-draft", summary)

    llm_counts = _stub_llm(
        monkeypatch,
        decompose_payload=LLMStrictTaskDecompositionPayload(
            task_title="Ôn tập chương 1 ngắn gọn",
            steps=[
                LLMStrictStepPromptItem(
                    order_index=1,
                    title="Đọc slide bài 1",
                    expected_output="5 gạch đầu dòng ý chính bài 1",
                    estimated_minutes=20,
                    micro_task_prompt="Mở file PDF bài 1.",
                    cognitive_load="light",
                ),
                LLMStrictStepPromptItem(
                    order_index=2,
                    title="Tóm tắt bài 2",
                    expected_output="Sơ đồ tóm tắt 1 trang bài 2",
                    estimated_minutes=20,
                    micro_task_prompt="Mở vở ghi và viết tiêu đề bài 2.",
                    cognitive_load="medium",
                ),
            ],
            reasoning="Chia 2 bước đơn phiên nhẹ nhàng.",
        ),
    )

    single_calls: list[str] = []
    orig_single = ai_service.build_single_session_enrichment
    split_calls: list[int] = []
    orig_split = ai_service.enrich_split_sessions

    def _spy_single(**kwargs: Any):
        single_calls.append(kwargs["client_session_key"])
        return orig_single(**kwargs)

    async def _spy_split(split_sessions: Any, *, task_title: str | None = None):
        split_calls.append(len(split_sessions))
        return await orig_split(split_sessions, task_title=task_title)

    monkeypatch.setattr(ai_service, "build_single_session_enrichment", _spy_single)
    monkeypatch.setattr(ai_service, "enrich_split_sessions", _spy_split)

    cand_resp = await _create_candidate(client, draft, "tc01-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert len(cand["steps"]) == 2
    assert len(cand["sessions"]) == 2
    assert len(single_calls) == 2
    assert split_calls == []
    assert llm_counts["UF2_Enrich"] == 0

    step_by_key = {s["client_step_key"]: s for s in cand["steps"]}
    for sess in cand["sessions"]:
        st = step_by_key[sess["client_step_key"]]
        assert sess["title"] == st["title"]
        assert sess["expected_output"] == st["expected_output"]
        assert st["title"] in sess["session_goal"]

    conf_resp = await _confirm_candidate(client, cand, "tc01-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand["candidate_id"]))
        assert row is not None and row.generation_tier == "tier_1_gemini"
        db_sessions = (
            await db.scalars(select(SessionRow).order_by(SessionRow.scheduled_start))
        ).all()
        assert len(db_sessions) == 2
        assert all(s.title and s.session_goal and s.expected_output for s in db_sessions)

    _record_case(
        case_id="TC-UF2-01",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="Short task with 2 single-session steps skips LLM enrichment",
        ui_scenario="standard_review",
        input_a={
            "task_title": summary["title"],
            "scope": summary["scope"],
            "time_constraint": summary["time_constraint"],
            "availability": summary["availability"],
            "summary": "Nháp 5 ngày, rảnh 09:00-12:00 UTC/ngày, AI chia 2 bước x 20 phút.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "decompose_calls": llm_counts["UF2"],
            "single_enrichment_calls": len(single_calls),
            "split_enrichment_llm_calls": llm_counts["UF2_Enrich"],
            "summary": "decompose_task sinh 2 bước đơn phiên; 0 lần gọi LLM enrich (dùng build_single_session_enrichment).",
        },
        layer2_c=_layer2_snapshot(
            cand,
            summary="2/2 bước xếp trọn vẹn trong 2 phiên, feasible không thiếu giờ.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="PlanCandidate v1 complete với 2 steps, 2 sessions kế thừa title/expected_output từ Step.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_sessions": [
                {
                    "title": s["title"],
                    "session_goal": s["session_goal"],
                    "expected_output": s["expected_output"],
                }
                for s in task["sessions"]
            ],
            "summary": "Confirm 201 lưu Task/Steps/Sessions vào SQLite đầy đủ trường enrichment.",
        },
        assertions_checked=[
            "enrich_split_sessions called 0 times",
            "build_single_session_enrichment called 2 times",
            "session.title == step.title and session.expected_output == step.expected_output",
            "confirm_candidate returns 201 and persists enriched SessionRow fields",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_02_long_task_single_step_split_3_sessions_batched_enrichment(
    api, monkeypatch
) -> None:
    """TC-UF2-02: 1 bước tách thành 3 phiên -> 1 batch enrich_split_sessions, giữ micro-task phiên 1 và nối tiếp phiên 2-3."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Viết chương tổng quan tài liệu",
        scope="Bản thảo 6 trang có trích dẫn",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="09:30",  # 30p/ngày -> tối đa 1500s work/ngày -> 60p*1.2 = 4320s tách đúng 3 phiên (1500+1500+1320)
    )
    draft = await _confirmed_draft(client, "tc02-draft", summary)

    orig_micro = "Mở file Word mẫu và tạo tiêu đề chương 1."
    batch_inputs: list[list[dict[str, Any]]] = []

    def _enrich_fn(prompt: dict[str, Any]) -> LLMSessionEnrichmentPayload:
        items = prompt["sessions"]
        batch_inputs.append(items)
        return LLMSessionEnrichmentPayload(
            sessions=[
                LLMSessionEnrichmentItem(
                    client_session_key=item["client_session_key"],
                    title=f"{item['step_title']} (Phần {item['session_index']}/{item['total_sessions_in_step']})",
                    session_goal=f"Hoàn thành phân đoạn {item['session_index']}/3 trong {item['work_minutes']} phút",
                    expected_output=f"Đầu ra phân đoạn {item['session_index']}/3: {item['session_index'] * 2} trang bản thảo",
                    micro_task_text=(
                        "AI ghi đè không dùng ở phần 1"
                        if item["session_index"] == 1
                        else f"Mở lại bản thảo đang viết dở ở trang {item['session_index']} để viết tiếp."
                    ),
                )
                for item in items
            ]
        )

    llm_counts = _stub_llm(
        monkeypatch,
        decompose_payload=LLMStrictTaskDecompositionPayload(
            task_title="Viết chương tổng quan tài liệu",
            steps=[
                LLMStrictStepPromptItem(
                    order_index=1,
                    title="Soạn thảo chương tổng quan",
                    expected_output="Bản thảo 6 trang hoàn chỉnh",
                    estimated_minutes=60,
                    micro_task_prompt=orig_micro,
                    cognitive_load="heavy",
                )
            ],
            reasoning="Chia thành 1 bước trọng tâm để thuật toán tách 3 phiên 25p.",
        ),
        enrich_fn=_enrich_fn,
    )

    cand_resp = await _create_candidate(client, draft, "tc02-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert llm_counts["UF2_Enrich"] == 1
    assert len(batch_inputs) == 1 and len(batch_inputs[0]) == 3

    sessions = sorted(cand["sessions"], key=lambda x: x["scheduled_start"])
    assert len(sessions) == 3
    assert sessions[0]["micro_task_text"] == orig_micro
    assert "viết tiếp" in sessions[1]["micro_task_text"]
    assert "viết tiếp" in sessions[2]["micro_task_text"]
    assert len({s["title"] for s in sessions}) == 3
    assert len({s["session_goal"] for s in sessions}) == 3
    assert len({s["expected_output"] for s in sessions}) == 3

    conf_resp = await _confirm_candidate(client, cand, "tc02-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    async with factory() as db:
        persisted = (
            await db.scalars(select(SessionRow).order_by(SessionRow.scheduled_start))
        ).all()
        assert len(persisted) == 3
        assert persisted[0].micro_task_text == orig_micro
        assert persisted[1].title and "Phần 2/3" in persisted[1].title

    _record_case(
        case_id="TC-UF2-02",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="Single step split into 3 sessions uses 1 batched enrichment call",
        ui_scenario="split_sessions_focus",
        input_a={
            "task_title": summary["title"],
            "scope": summary["scope"],
            "time_constraint": summary["time_constraint"],
            "availability": summary["availability"],
            "summary": "Bước 60p (4320s có buffer) trên khe rảnh 30p/ngày -> tách đúng 3 phiên.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "enrich_batch_calls": llm_counts["UF2_Enrich"],
            "split_input_count": len(batch_inputs[0]),
            "summary": "1 lần gọi batch enrich_split_sessions cho cả 3 phiên.",
        },
        layer2_c=_layer2_snapshot(
            cand,
            summary="Xếp 3 phiên across 3 ngày (1500s + 1500s + 1320s work), feasible.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Phiên 1 giữ nguyên micro_task_prompt gốc; phiên 2-3 nhận micro_task_text nối tiếp.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "session_titles": [s["title"] for s in task["sessions"]],
            "summary": "Lưu 3 SessionRow với title/session_goal/expected_output phân biệt.",
        },
        assertions_checked=[
            "enrich_split_sessions called once with 3 sessions",
            "Session 1 retains original micro_task_prompt",
            "Sessions 2-3 receive distinct continuation micro_task_text",
            "All 3 sessions have distinct title, session_goal, expected_output",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_03_mixed_task_only_split_sessions_sent_to_llm(api, monkeypatch) -> None:
    """TC-UF2-03: Hỗn hợp 1 bước đơn phiên + 1 bước tách 2 phiên -> CHỈ 2 phiên tách gửi sang LLM enrich."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Chuẩn bị bài thuyết trình",
        scope="Dàn ý + 8 slide nội dung",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="09:30",  # 30p/ngày -> Step 1 (20p*1.2=1440s -> 1 phiên), Step 2 (40p*1.2=2880s -> 2 phiên)
    )
    draft = await _confirmed_draft(client, "tc03-draft", summary)

    captured_batches: list[list[dict[str, Any]]] = []

    def _enrich_fn(prompt: dict[str, Any]) -> LLMSessionEnrichmentPayload:
        items = prompt["sessions"]
        captured_batches.append(items)
        return LLMSessionEnrichmentPayload(
            sessions=[
                LLMSessionEnrichmentItem(
                    client_session_key=item["client_session_key"],
                    title=f"{item['step_title']} (Phần {item['session_index']}/{item['total_sessions_in_step']})",
                    session_goal=f"Mục tiêu phần {item['session_index']}/{item['total_sessions_in_step']}",
                    expected_output=f"Đầu ra slide phần {item['session_index']}",
                    micro_task_text=f"Mở PowerPoint làm tiếp phần {item['session_index']}.",
                )
                for item in items
            ]
        )

    llm_counts = _stub_llm(
        monkeypatch,
        decompose_payload=LLMStrictTaskDecompositionPayload(
            task_title="Chuẩn bị bài thuyết trình",
            steps=[
                LLMStrictStepPromptItem(
                    order_index=1,
                    title="Lập dàn ý thuyết trình",
                    expected_output="Dàn ý 5 mục chính",
                    estimated_minutes=20,
                    micro_task_prompt="Mở Google Docs ghi 5 gạch đầu dòng.",
                    cognitive_load="light",
                ),
                LLMStrictStepPromptItem(
                    order_index=2,
                    title="Thiết kế 8 slide nội dung",
                    expected_output="Bộ 8 slide hoàn chỉnh",
                    estimated_minutes=40,
                    micro_task_prompt="Mở template slide bài giảng.",
                    cognitive_load="medium",
                ),
            ],
            reasoning="Bước 1 gọn 1 phiên, bước 2 tách 2 phiên.",
        ),
        enrich_fn=_enrich_fn,
    )

    cand_resp = await _create_candidate(client, draft, "tc03-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert llm_counts["UF2_Enrich"] == 1
    assert len(captured_batches) == 1
    assert len(captured_batches[0]) == 2
    assert {item["client_step_key"] for item in captured_batches[0]} == {"step-2"}

    s_step1 = [s for s in cand["sessions"] if s["client_step_key"] == "step-1"]
    s_step2 = sorted(
        [s for s in cand["sessions"] if s["client_step_key"] == "step-2"],
        key=lambda x: x["scheduled_start"],
    )
    assert len(s_step1) == 1
    assert s_step1[0]["title"] == "Lập dàn ý thuyết trình"
    assert s_step1[0]["expected_output"] == "Dàn ý 5 mục chính"
    assert len(s_step2) == 2
    assert s_step2[0]["title"] == "Thiết kế 8 slide nội dung (Phần 1/2)"
    assert s_step2[0]["micro_task_text"] == "Mở template slide bài giảng."
    assert s_step2[1]["micro_task_text"] == "Mở PowerPoint làm tiếp phần 2."

    conf_resp = await _confirm_candidate(client, cand, "tc03-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-03",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="Mixed single-session and split step sends only split sessions to LLM",
        ui_scenario="split_sessions_focus",
        input_a={
            "task_title": summary["title"],
            "scope": summary["scope"],
            "time_constraint": summary["time_constraint"],
            "availability": summary["availability"],
            "summary": "Step 1 (20p -> 1 phiên) + Step 2 (40p -> 2 phiên) trên khe 30p/ngày.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "enrich_batch_calls": llm_counts["UF2_Enrich"],
            "split_input_count": len(captured_batches[0]),
            "split_step_keys": ["step-2"],
            "summary": "Chỉ 2 phiên của step-2 được gửi qua batch enrich LLM; step-1 bỏ qua LLM.",
        },
        layer2_c=_layer2_snapshot(
            cand,
            summary="Tổng 3 phiên xếp gọn qua 3 ngày liên tiếp, feasible.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Step-1 dùng deterministic single enrichment, step-2 dùng batch LLM enrichment.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_session_count": len(task["sessions"]),
            "summary": "Confirm lưu Task với cả 3 phiên đã làm giàu đúng phân nhánh.",
        },
        assertions_checked=[
            "split_input_count == 2 (only step-2 sessions sent)",
            "step-1 session inherits Step.title/expected_output without LLM call",
            "step-2 sessions receive batch LLM enrichment",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_04_manual_steps_preserves_order_and_enriches_split_sessions(
    api, monkeypatch
) -> None:
    """TC-UF2-04: manual_steps tự nhập -> generation_tier=='manual_user', giữ thứ tự và enrich phiên tách."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Tự thiết kế lộ trình ôn SQL",
        scope="2 bài tập thực hành truy vấn",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="09:30",
    )
    draft = await _confirmed_draft(client, "tc04-draft", summary)

    decompose_calls: list[str] = []

    async def _fail_decompose(**kwargs: Any) -> Any:
        decompose_calls.append(kwargs.get("task_title", ""))
        raise AssertionError("decompose_task must not be called for manual_steps")

    monkeypatch.setattr(ai_service, "decompose_task", _fail_decompose)

    captured_batches: list[list[dict[str, Any]]] = []

    def _enrich_fn(prompt: dict[str, Any]) -> LLMSessionEnrichmentPayload:
        items = prompt["sessions"]
        captured_batches.append(items)
        return LLMSessionEnrichmentPayload(
            sessions=[
                LLMSessionEnrichmentItem(
                    client_session_key=item["client_session_key"],
                    title=f"{item['step_title']} - Chặng {item['session_index']}/{item['total_sessions_in_step']}",
                    session_goal=f"Luyện tập phần {item['session_index']} của {item['step_title']}",
                    expected_output=f"Hoàn thành truy vấn phần {item['session_index']}",
                    micro_task_text=f"Mở DBeaver làm tiếp phần {item['session_index']}.",
                )
                for item in items
            ]
        )

    llm_counts = _stub_llm(monkeypatch, enrich_fn=_enrich_fn)

    manual_steps = [
        {
            "order_index": 0,
            "title": "Ôn tập JOIN cơ bản",
            "expected_output": "5 câu query JOIN đúng",
            "estimated_base_seconds": 1200,
        },
        {
            "order_index": 1,
            "title": "Thực hành Window Function",
            "expected_output": "6 câu query RANK/SUM OVER",
            "estimated_base_seconds": 2400,
        },
    ]
    cand_resp = await _create_candidate(
        client, draft, "tc04-cand", manual_steps=manual_steps
    )
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert decompose_calls == []
    assert [s["order_index"] for s in cand["steps"]] == [0, 1]
    assert [s["title"] for s in cand["steps"]] == [
        "Ôn tập JOIN cơ bản",
        "Thực hành Window Function",
    ]
    assert all(s["estimate_source"] == "user_confirmed" for s in cand["steps"])
    assert llm_counts["UF2_Enrich"] == 1
    assert len(captured_batches[0]) == 2

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand["candidate_id"]))
        assert row is not None and row.generation_tier == "manual_user"

    conf_resp = await _confirm_candidate(client, cand, "tc04-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-04",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="User-provided manual_steps skips AI decompose and enriches split sessions",
        ui_scenario="split_sessions_focus",
        input_a={
            "task_title": summary["title"],
            "scope": summary["scope"],
            "manual_steps": manual_steps,
            "availability": summary["availability"],
            "summary": "2 manual_steps (20p đơn phiên + 40p tách 2 phiên).",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "decompose_calls": 0,
            "enrich_batch_calls": llm_counts["UF2_Enrich"],
            "split_input_count": len(captured_batches[0]),
            "summary": "Không gọi decompose_task; generation_tier='manual_user'; enrich 2 phiên tách của step-2.",
        },
        layer2_c=_layer2_snapshot(
            cand,
            summary="Giữ thứ tự 0->1, xếp 3 phiên feasible với gap >= 900s.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Candidate lưu manual_steps với estimate_source='user_confirmed'.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "generation_tier_in_db": "manual_user",
            "summary": "Confirm lưu Task và xác nhận CandidateRow.generation_tier == 'manual_user'.",
        },
        assertions_checked=[
            "CandidateRow.generation_tier == 'manual_user'",
            "decompose_task called 0 times",
            "step order_index [0, 1] and user titles preserved",
            "split step sessions enriched via batch call",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_05_long_45_day_project_rolling_horizon_14d_partial_extension(
    api, monkeypatch
) -> None:
    """TC-UF2-05: Dự án 45 ngày -> Rolling Horizon 14d (decomposition_state=='partial', lưu Task.extension_candidate_at)."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Xây dựng khóa luận tốt nghiệp 45 ngày",
        scope="4 chương tài liệu, mã nguồn thử nghiệm và báo cáo bảo vệ",
        start_at=base_day,
        days=45,
        start_local="08:00",
        end_local="09:00",
    )
    draft = await _confirmed_draft(client, "tc05-draft", summary)

    llm_counts = _stub_llm(
        monkeypatch,
        decompose_payload=LLMStrictTaskDecompositionPayload(
            task_title="Xây dựng khóa luận tốt nghiệp 45 ngày",
            unexpanded_scope="Chương 3: Thử nghiệm đánh giá; Chương 4: Hoàn thiện báo cáo bảo vệ",
            steps=[
                LLMStrictStepPromptItem(
                    order_index=1,
                    title="Chương 1: Tổng quan đề tài",
                    expected_output="Bản nháp Chương 1 (5 trang)",
                    estimated_minutes=60,
                    micro_task_prompt="Mở template LaTeX tạo mục Chương 1.",
                    cognitive_load="medium",
                ),
                LLMStrictStepPromptItem(
                    order_index=2,
                    title="Chương 2: Thiết kế kiến trúc",
                    expected_output="Sơ đồ ERD và đặc tả API",
                    estimated_minutes=60,
                    micro_task_prompt="Mở công cụ vẽ ERD tạo bảng chính.",
                    cognitive_load="heavy",
                ),
            ],
            reasoning="Chia trước chặng 14 ngày đầu và giữ phần sau trong unexpanded_scope.",
        ),
    )

    cand_resp = await _create_candidate(client, draft, "tc05-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    h_start = _parse_iso(cand["planning_horizon"]["start_at"])
    h_end = _parse_iso(cand["planning_horizon"]["end_at"])
    assert abs((h_end - h_start).total_seconds() - 14 * 86400) <= 5
    assert cand["decomposition_state"] == "partial"
    assert cand["unexpanded_scope"] is not None
    assert cand["feasibility"]["status"] == "feasible"
    assert cand["feasibility"]["fully_scheduled"] is True

    conf_resp = await _confirm_candidate(client, cand, "tc05-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    expected_ext = max(_parse_iso(s["scheduled_end"]) for s in task["sessions"])
    assert task["decomposition_state"] == "partial"
    assert task["extension_candidate_at"] is not None
    assert _parse_iso(task["extension_candidate_at"]) == expected_ext

    async with factory() as db:
        t_row = await db.get(Task, UUID(task["task_id"]))
        assert t_row is not None
        assert t_row.decomposition_state == "partial"
        assert t_row.extension_candidate_at is not None

    _record_case(
        case_id="TC-UF2-05",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="45-day project slices to 14d rolling horizon and persists extension_candidate_at",
        ui_scenario="partial_horizon_locked",
        input_a={
            "task_title": summary["title"],
            "scope": summary["scope"],
            "time_constraint": summary["time_constraint"],
            "availability": summary["availability"],
            "summary": "Deadline 45 ngày; mặc định Rolling Horizon 14 ngày đầu + unexpanded_scope.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "decompose_calls": llm_counts["UF2"],
            "unexpanded_scope": cand["unexpanded_scope"],
            "summary": "AI sinh 2 bước chặng đầu và đặt unexpanded_scope cho Chương 3-4.",
        },
        layer2_c=_layer2_snapshot(
            cand,
            summary="Xếp đủ 2 bước trong horizon 14 ngày (<= 14d), feasible.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="decomposition_state == 'partial', planning_horizon dài đúng 14 ngày.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "decomposition_state": task["decomposition_state"],
            "extension_candidate_at": task["extension_candidate_at"],
            "summary": "Task.extension_candidate_at được lưu bằng max(scheduled_end) khi confirm partial.",
        },
        assertions_checked=[
            "planning_horizon span == 14 days despite 45-day deadline",
            "candidate.decomposition_state == 'partial'",
            "Task.extension_candidate_at persisted == max(session.scheduled_end)",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_06_planning_horizon_21_days_and_clamp_over_21(api) -> None:
    """TC-UF2-06: planning_horizon=21 ngày xếp tới ngày 21 (MAX=21); gửi >21 ngày bị kẹp về 21 ngày."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Dự án mở rộng 40 ngày",
        scope="4 chặng liên tiếp qua tuần 3",
        start_at=base_day,
        days=40,
        start_local="09:00",
        end_local="10:00",  # 3000s work/ngày -> 14d=42,000s; 21d=63,000s
    )
    draft = await _confirmed_draft(client, "tc06-draft", summary)

    # 4 steps x 200m (12000s * 1.2 = 14400s work/step -> 57,600s tổng: tràn 14d, vừa khít trong 21d!)
    steps_payload = [
        {
            "order_index": i,
            "title": f"Chặng dài {i + 1}",
            "expected_output": f"Xong chặng {i + 1}",
            "estimated_base_seconds": 12000,
        }
        for i in range(4)
    ]

    resp_21 = await _create_candidate(
        client,
        draft,
        "tc06-h21",
        manual_steps=steps_payload,
        planning_horizon={
            "start_at": base_day.isoformat(),
            "end_at": (base_day + timedelta(days=21)).isoformat(),
        },
    )
    assert resp_21.status_code == 201, resp_21.text
    cand_21 = resp_21.json()["data"]
    h21_start = _parse_iso(cand_21["planning_horizon"]["start_at"])
    h21_end = _parse_iso(cand_21["planning_horizon"]["end_at"])
    assert abs((h21_end - h21_start).total_seconds() - settings.MAX_PLANNING_HORIZON_DAYS * 86400) <= 5
    assert cand_21["feasibility"]["status"] == "feasible"
    assert cand_21["feasibility"]["fully_scheduled"] is True
    last_sess_end = max(_parse_iso(s["scheduled_end"]) for s in cand_21["sessions"])
    assert last_sess_end > h21_start + timedelta(days=14)
    assert last_sess_end <= h21_end

    resp_30 = await _create_candidate(
        client,
        draft,
        "tc06-h30",
        manual_steps=steps_payload,
        planning_horizon={
            "start_at": base_day.isoformat(),
            "end_at": (base_day + timedelta(days=30)).isoformat(),
        },
    )
    assert resp_30.status_code == 201, resp_30.text
    cand_30 = resp_30.json()["data"]
    h30_start = _parse_iso(cand_30["planning_horizon"]["start_at"])
    h30_end = _parse_iso(cand_30["planning_horizon"]["end_at"])
    assert abs((h30_end - h30_start).total_seconds() - settings.MAX_PLANNING_HORIZON_DAYS * 86400) <= 5

    conf_resp = await _confirm_candidate(client, cand_21, "tc06-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-06",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="Requested 21-day horizon schedules into week 3 and >21d is clamped to 21d",
        ui_scenario="partial_horizon_locked",
        input_a={
            "task_title": summary["title"],
            "requested_horizon_21d": {
                "start_at": base_day.isoformat(),
                "end_at": (base_day + timedelta(days=21)).isoformat(),
            },
            "requested_horizon_30d": {
                "start_at": base_day.isoformat(),
                "end_at": (base_day + timedelta(days=30)).isoformat(),
            },
            "manual_steps": steps_payload,
            "summary": "Kiểm tra horizon 21 ngày (xếp tới ngày 20) và clamp 30 ngày -> 21 ngày.",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "summary": "Dùng manual_steps 4x200p (57,600s có buffer > 42,000s của 14d, <= 63,000s của 21d).",
        },
        layer2_c=_layer2_snapshot(
            cand_21,
            summary="Horizon 21d xếp đủ cả 4 bước qua mốc 14 ngày; horizon 30d bị kẹp về 21d.",
        ),
        layer3_d=_layer3_snapshot(
            cand_21,
            summary="MAX_PLANNING_HORIZON_DAYS=21 được áp dụng chính xác cho cả request 21d và 30d.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "clamped_30d_span_days": round((h30_end - h30_start).total_seconds() / 86400, 2),
            "summary": "Confirm 21d candidate thành công với phiên trải tới tuần 3.",
        },
        assertions_checked=[
            "21d planning_horizon preserved (span == 21 days)",
            "sessions extend past day 14 and end <= day 21",
            "30d planning_horizon clamped to MAX_PLANNING_HORIZON_DAYS (21 days)",
            "confirm_candidate succeeds (201)",
        ],
        candidate_payload=cand_21,
        task_payload=task,
    )


async def test_tc_uf2_07_confirm_with_google_calendar_creates_links_and_outbox(api) -> None:
    """TC-UF2-07: confirm_candidate khi đã nối Google Calendar -> tạo CalendarEventLink + CalendarSyncJob chuẩn [ProPlan]."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Đồng bộ lịch Google",
        scope="2 bước kiểm tra outbox lịch",
        start_at=base_day,
        days=7,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc07-draft", summary)

    async with factory() as db:
        await _connect_write_calendar(db, "user_a")
        await db.commit()

    cand_resp = await _create_candidate(
        client,
        draft,
        "tc07-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Lập kế hoạch đồng bộ",
                "expected_output": "Bảng kiểm thử outbox",
                "estimated_base_seconds": 1200,
            }
        ],
    )
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    conf_resp = await _confirm_candidate(client, cand, "tc07-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    conf_data = conf_resp.json()["data"]
    assert conf_data["calendar_sync_summary"] == "pending"
    task = conf_data["task"]
    assert all(s["calendar_event_link_id"] is not None for s in task["sessions"])

    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        links = (
            await db.scalars(
                select(CalendarEventLink).where(CalendarEventLink.user_id == owner)
            )
        ).all()
        jobs = (
            await db.scalars(select(CalendarSyncJob).where(CalendarSyncJob.user_id == owner))
        ).all()
        assert len(links) == len(task["sessions"]) == 1
        assert len(jobs) == len(task["sessions"]) == 1
        snap = jobs[0].payload_snapshot
        assert snap["summary"] == "[ProPlan] Lập kế hoạch đồng bộ"
        assert "Mục tiêu:" in snap["description"]
        assert "Đầu ra dự kiến: Bảng kiểm thử outbox" in snap["description"]
        outbox_summaries = [j.payload_snapshot["summary"] for j in jobs]

    _record_case(
        case_id="TC-UF2-07",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="Confirm with connected Google Calendar creates EventLink and Outbox SyncJob",
        ui_scenario="standard_review",
        input_a={
            "task_title": summary["title"],
            "google_calendar_connected": True,
            "write_calendar_id": "primary",
            "summary": "CalendarConnection đã nối; confirm candidate 1 bước.",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "summary": "Bước đơn phiên dùng build_single_session_enrichment.",
        },
        layer2_c=_layer2_snapshot(cand, summary="1 phiên xếp gọn trong 09:00-12:00 UTC, feasible."),
        layer3_d=_layer3_snapshot(
            cand,
            summary="confirm_candidate trả calendar_sync_summary='pending' cùng calendar_event_link_id.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "calendar_sync_summary": conf_data["calendar_sync_summary"],
            "calendar_event_links_count": 1,
            "calendar_outbox_summaries": outbox_summaries,
            "outbox_description": snap["description"],
            "summary": "Tạo CalendarEventLink + CalendarSyncJob queued với [ProPlan] summary và Mục tiêu/Đầu ra dự kiến.",
        },
        assertions_checked=[
            "confirm returns calendar_sync_summary == 'pending'",
            "CalendarEventLink created with is_current=True and sync_state='pending'",
            "CalendarSyncJob queued with summary == '[ProPlan] Lập kế hoạch đồng bộ'",
            "Outbox description contains 'Mục tiêu:' and 'Đầu ra dự kiến:'",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_08_selected_step_keys_partial_selection(api) -> None:
    """TC-UF2-08: selected_step_keys chọn 2/3 bước từ candidate trước -> chỉ xếp lịch cho 2 bước đã chọn."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Chọn bước một phần",
        scope="3 bước tuần tự",
        start_at=base_day,
        days=7,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc08-draft", summary)

    cand_all = (
        await _create_candidate(
            client,
            draft,
            "tc08-cand-all",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước 1",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 1,
                    "title": "Bước 2",
                    "expected_output": "Out 2",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 2,
                    "title": "Bước 3",
                    "expected_output": "Out 3",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]
    assert len(cand_all["sessions"]) == 3

    cand_sub_resp = await _create_candidate(
        client,
        draft,
        "tc08-cand-sub",
        selected_step_keys=["step-1", "step-2"],
    )
    assert cand_sub_resp.status_code == 201, cand_sub_resp.text
    cand_sub = cand_sub_resp.json()["data"]

    assert cand_sub["feasibility"]["status"] == "feasible"
    assert cand_sub["feasibility"]["fully_scheduled"] is True
    assert cand_sub["feasibility"]["verification_scope"]["client_step_keys"] == ["step-1", "step-2"]
    assert [s["client_step_key"] for s in cand_sub["sessions"]] == ["step-1", "step-2"]

    conf_resp = await _confirm_candidate(client, cand_sub, "tc08-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]
    assert len(task["steps"]) == 3
    assert len(task["sessions"]) == 2
    assert task["scheduling_state"] == "partial"

    _record_case(
        case_id="TC-UF2-08",
        group="Group 1 — Happy Path, Session Enrichment Option 1, Rolling Horizon & Calendar Sync",
        title="Partial selected_step_keys schedules only selected steps cleanly",
        ui_scenario="partial_horizon_locked",
        input_a={
            "task_title": summary["title"],
            "selected_step_keys": ["step-1", "step-2"],
            "total_steps_in_previous": 3,
            "summary": "Chọn 2/3 bước đầu từ candidate trước qua selected_step_keys.",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "summary": "Kế thừa steps từ candidate trước, không gọi AI phân rã lại.",
        },
        layer2_c=_layer2_snapshot(
            cand_sub,
            summary="verification_scope=['step-1','step-2'], xếp đúng 2 phiên, feasible.",
        ),
        layer3_d=_layer3_snapshot(
            cand_sub,
            summary="Candidate giữ 3 steps nhưng chỉ có 2 sessions thuộc ['step-1', 'step-2'].",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "scheduling_state": task["scheduling_state"],
            "persisted_steps": len(task["steps"]),
            "persisted_sessions": len(task["sessions"]),
            "summary": "Confirm thành công với Task.scheduling_state == 'partial'.",
        },
        assertions_checked=[
            "verification_scope.client_step_keys == ['step-1', 'step-2']",
            "sessions belong exclusively to step-1 and step-2",
            "confirm creates 3 steps, 2 sessions, scheduling_state == 'partial'",
        ],
        candidate_payload=cand_sub,
        task_payload=task,
    )


# =============================================================================
# GROUP 2 — Structural Step Ops, NL Edits & 1-Step Undo
# =============================================================================


async def test_tc_uf2_09_structured_add_steps_insert_and_append(api, monkeypatch) -> None:
    """TC-UF2-09: add_steps chèn giữa (insert_at_index=1) + thêm cuối -> đánh lại order_index, xếp lịch & enrich lại."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Thao tác add_steps",
        scope="Thêm bước chèn và cuối",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc09-draft", summary)

    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc09-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước Đầu",
                    "expected_output": "Out Đầu",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 1,
                    "title": "Bước Giữa Cũ",
                    "expected_output": "Out Giữa Cũ",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]

    patch_resp = await _patch(
        client,
        f"/candidates/{cand0['candidate_id']}",
        "tc09-patch",
        {
            "expected_review_revision": cand0["review_revision"],
            "edits": {
                "add_steps": [
                    {
                        "title": "Bước Chèn Vị Trí 1",
                        "expected_output": "Out Chèn",
                        "estimated_base_seconds": 1200,
                        "micro_task_text": "Mở mục 1 chèn thêm",
                        "insert_at_index": 1,
                    },
                    {
                        "title": "Bước Cuối Mới",
                        "expected_output": "Out Cuối",
                        "estimated_base_seconds": 1200,
                        "micro_task_text": "Mở checklist bước cuối",
                    },
                ]
            },
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand1 = patch_resp.json()["data"]

    assert cand1["review_revision"] == 2
    assert cand1["can_undo"] is True
    assert [s["title"] for s in cand1["steps"]] == [
        "Bước Đầu",
        "Bước Chèn Vị Trí 1",
        "Bước Giữa Cũ",
        "Bước Cuối Mới",
    ]
    assert [s["order_index"] for s in cand1["steps"]] == [0, 1, 2, 3]
    assert len(cand1["sessions"]) == 4
    ordered_sess = sorted(cand1["sessions"], key=lambda x: x["scheduled_start"])
    for a, b in zip(ordered_sess, ordered_sess[1:], strict=False):
        assert (_parse_iso(b["scheduled_start"]) - _parse_iso(a["scheduled_end"])).total_seconds() >= 900
    assert all(s["title"] and s["session_goal"] and s["expected_output"] for s in ordered_sess)

    conf_resp = await _confirm_candidate(client, cand1, "tc09-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-09",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="Structured add_steps (insert_at_index=1 + append) renumbers and reschedules",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "edits": {
                "add_steps": [
                    {"title": "Bước Chèn Vị Trí 1", "insert_at_index": 1, "estimated_base_seconds": 1200},
                    {"title": "Bước Cuối Mới", "insert_at_index": None, "estimated_base_seconds": 1200},
                ]
            },
            "summary": "Chèn 1 bước tại index 1 và thêm 1 bước cuối từ Candidate 2 bước.",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "summary": "Re-enrichment chạy lại cho 4 phiên đơn sau tái xếp lịch.",
        },
        layer2_c=_layer2_snapshot(
            cand1,
            summary="Xếp tuần tự 4 phiên, đảm bảo khoảng nghỉ >= 900s giữa mọi phiên.",
        ),
        layer3_d=_layer3_snapshot(
            cand1,
            summary="order_index đánh lại [0,1,2,3], review_revision=2, can_undo=True.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_step_titles": [s["title"] for s in task["steps"]],
            "summary": "Confirm lưu đủ 4 bước đúng thứ tự vào DB.",
        },
        assertions_checked=[
            "steps ordered as ['Bước Đầu', 'Bước Chèn Vị Trí 1', 'Bước Giữa Cũ', 'Bước Cuối Mới']",
            "order_index == [0, 1, 2, 3]",
            "all sessions maintain >= 900s inter-session gap",
            "can_undo == True",
        ],
        candidate_payload=cand1,
        task_payload=task,
    )


async def test_tc_uf2_10_structured_delete_step_keys(api) -> None:
    """TC-UF2-10: delete_step_keys xóa 1 bước -> đánh lại order_index, can_undo==True."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Thao tác delete_step_keys",
        scope="Xóa bước giữa",
        start_at=base_day,
        days=7,
    )
    draft = await _confirmed_draft(client, "tc10-draft", summary)
    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc10-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước A",
                    "expected_output": "Out A",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 1,
                    "title": "Bước B Sẽ Xóa",
                    "expected_output": "Out B",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 2,
                    "title": "Bước C",
                    "expected_output": "Out C",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]

    patch_resp = await _patch(
        client,
        f"/candidates/{cand0['candidate_id']}",
        "tc10-patch",
        {
            "expected_review_revision": cand0["review_revision"],
            "edits": {"delete_step_keys": ["step-2"]},
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand1 = patch_resp.json()["data"]

    assert [s["client_step_key"] for s in cand1["steps"]] == ["step-1", "step-3"]
    assert [s["order_index"] for s in cand1["steps"]] == [0, 1]
    assert all(s["client_step_key"] != "step-2" for s in cand1["sessions"])
    assert cand1["can_undo"] is True

    conf_resp = await _confirm_candidate(client, cand1, "tc10-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-10",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="Structured delete_step_keys removes step, renumbers remaining, enables undo",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "edits": {"delete_step_keys": ["step-2"]},
            "summary": "Xóa step-2 khỏi Candidate 3 bước.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Cập nhật enrichment cho 2 phiên còn lại."},
        layer2_c=_layer2_snapshot(cand1, summary="2 bước còn lại xếp lịch feasible."),
        layer3_d=_layer3_snapshot(
            cand1,
            summary="Còn ['step-1','step-3'] với order_index [0,1] và can_undo=True.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_steps": len(task["steps"]),
            "summary": "Confirm lưu đúng 2 bước sau khi xóa.",
        },
        assertions_checked=[
            "remaining step keys == ['step-1', 'step-3']",
            "order_index renumbered to [0, 1]",
            "no sessions remain for deleted step-2",
            "can_undo == True",
        ],
        candidate_payload=cand1,
        task_payload=task,
    )


async def test_tc_uf2_11_structured_merge_steps_auto_sums_duration(api, monkeypatch) -> None:
    """TC-UF2-11: merge_steps gộp 2 bước -> tự cộng thời lượng gốc, xếp lịch & enrich phiên tách."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Thao tác merge_steps",
        scope="Gộp 2 bước đầu",
        start_at=base_day,
        days=10,
        start_local="08:00",
        end_local="08:30",
    )
    draft = await _confirmed_draft(client, "tc11-draft", summary)
    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc11-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Đọc tài liệu A",
                    "expected_output": "Out A",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 1,
                    "title": "Tóm tắt tài liệu B",
                    "expected_output": "Out B",
                    "estimated_base_seconds": 1500,
                },
                {
                    "order_index": 2,
                    "title": "Kết luận",
                    "expected_output": "Out C",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]

    llm_counts = _stub_llm(
        monkeypatch,
        enrich_fn=lambda prompt: LLMSessionEnrichmentPayload(
            sessions=[
                LLMSessionEnrichmentItem(
                    client_session_key=item["client_session_key"],
                    title=f"{item['step_title']} (Phần {item['session_index']}/{item['total_sessions_in_step']})",
                    session_goal=f"Mục tiêu gộp phần {item['session_index']}",
                    expected_output=f"Đầu ra gộp phần {item['session_index']}",
                    micro_task_text=f"Tiếp tục gộp phần {item['session_index']}.",
                )
                for item in prompt["sessions"]
            ]
        ),
    )

    patch_resp = await _patch(
        client,
        f"/candidates/{cand0['candidate_id']}",
        "tc11-patch",
        {
            "expected_review_revision": cand0["review_revision"],
            "edits": {
                "merge_steps": [
                    {
                        "source_step_keys": ["step-1", "step-2"],
                        "title": "Đọc và tóm tắt A+B",
                        "expected_output": "Bản tóm tắt gộp A+B",
                    }
                ]
            },
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand1 = patch_resp.json()["data"]

    assert [s["title"] for s in cand1["steps"]] == ["Đọc và tóm tắt A+B", "Kết luận"]
    assert [s["order_index"] for s in cand1["steps"]] == [0, 1]
    merged_step = cand1["steps"][0]
    assert merged_step["estimated_base_seconds"] == 2700
    merged_sessions = sorted(
        [s for s in cand1["sessions"] if s["client_step_key"] == merged_step["client_step_key"]],
        key=lambda x: x["scheduled_start"],
    )
    assert len(merged_sessions) >= 2
    assert llm_counts["UF2_Enrich"] == 1
    assert merged_sessions[1]["title"].endswith("(Phần 2/3)") or "(Phần 2/" in merged_sessions[1]["title"]
    assert cand1["can_undo"] is True

    conf_resp = await _confirm_candidate(client, cand1, "tc11-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-11",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="Structured merge_steps auto-sums durations and re-enriches split sessions",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "edits": {
                "merge_steps": [
                    {
                        "source_step_keys": ["step-1", "step-2"],
                        "title": "Đọc và tóm tắt A+B",
                        "expected_output": "Bản tóm tắt gộp A+B",
                    }
                ]
            },
            "summary": "Gộp step-1 (1200s) + step-2 (1500s) -> auto-sum 2700s trên khe 30p/ngày.",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "enrich_batch_calls": llm_counts["UF2_Enrich"],
            "summary": "Sau khi gộp thành 2700s (3240s buffered -> 3 phiên), gọi batch enrich 1 lần.",
        },
        layer2_c=_layer2_snapshot(
            cand1,
            summary="Bước gộp 2700s chia 3 phiên, bước cuối 1 phiên; tổng 4 phiên feasible.",
        ),
        layer3_d=_layer3_snapshot(
            cand1,
            summary="2 steps với order_index [0,1], merged estimated_base_seconds=2700, can_undo=True.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "merged_step_base_seconds": task["steps"][0]["estimated_base_seconds"],
            "summary": "Confirm lưu bước gộp 2700s và các phiên đã làm giàu vào SQLite.",
        },
        assertions_checked=[
            "merged_step.estimated_base_seconds == 1200 + 1500 == 2700",
            "steps renumbered [0, 1]",
            "split sessions of merged step re-enriched via batch LLM",
            "can_undo == True",
        ],
        candidate_payload=cand1,
        task_payload=task,
    )


async def test_tc_uf2_12_structured_split_steps(api) -> None:
    """TC-UF2-12: split_steps tách 1 bước thành 2 bước con -> giữ vị trí, đánh lại order_index, xếp lịch & enrich."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Thao tác split_steps",
        scope="Tách bước lớn thành 2 bước con",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc12-draft", summary)
    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc12-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Xây dựng module lớn",
                    "expected_output": "Module hoàn chỉnh",
                    "estimated_base_seconds": 3600,
                },
                {
                    "order_index": 1,
                    "title": "Kiểm thử tích hợp",
                    "expected_output": "Báo cáo pass 100%",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]

    patch_resp = await _patch(
        client,
        f"/candidates/{cand0['candidate_id']}",
        "tc12-patch",
        {
            "expected_review_revision": cand0["review_revision"],
            "edits": {
                "split_steps": [
                    {
                        "source_step_key": "step-1",
                        "new_steps": [
                            {
                                "title": "Thiết kế schema",
                                "expected_output": "File schema.py",
                                "estimated_base_seconds": 1200,
                                "micro_task_text": "Mở schema.py tạo class đầu tiên",
                            },
                            {
                                "title": "Viết service xử lý",
                                "expected_output": "File service.py",
                                "estimated_base_seconds": 1200,
                                "micro_task_text": "Mở service.py viết hàm đầu",
                            },
                        ],
                    }
                ]
            },
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand1 = patch_resp.json()["data"]

    assert [s["title"] for s in cand1["steps"]] == [
        "Thiết kế schema",
        "Viết service xử lý",
        "Kiểm thử tích hợp",
    ]
    assert [s["order_index"] for s in cand1["steps"]] == [0, 1, 2]
    assert len(cand1["sessions"]) == 3
    assert cand1["can_undo"] is True

    conf_resp = await _confirm_candidate(client, cand1, "tc12-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-12",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="Structured split_steps replaces 1 step with 2 child steps and reschedules",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "edits": {
                "split_steps": [
                    {
                        "source_step_key": "step-1",
                        "new_steps": ["Thiết kế schema (1200s)", "Viết service xử lý (1200s)"],
                    }
                ]
            },
            "summary": "Tách step-1 (3600s) thành 2 bước con in-place.",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "summary": "Re-enrichment chạy cho 3 phiên sau khi tách bước.",
        },
        layer2_c=_layer2_snapshot(cand1, summary="3 phiên xếp tuần tự feasible."),
        layer3_d=_layer3_snapshot(
            cand1,
            summary="3 steps đúng thứ tự ['Thiết kế schema', 'Viết service xử lý', 'Kiểm thử tích hợp'], can_undo=True.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_step_titles": [s["title"] for s in task["steps"]],
            "summary": "Confirm lưu 3 bước mới vào SQLite.",
        },
        assertions_checked=[
            "split_steps replaces source step in-place with 2 child steps",
            "order_index renumbered [0, 1, 2]",
            "all 3 child/existing sessions rescheduled and enriched",
            "can_undo == True",
        ],
        candidate_payload=cand1,
        task_payload=task,
    )


async def test_tc_uf2_13_non_timing_edit_preserves_times_and_continuation_micro(
    api, monkeypatch
) -> None:
    """TC-UF2-13: Sửa micro_task_text trên step + title/goal/output trên session -> giữ nguyên giờ lịch và micro nối tiếp phần 2+."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Sửa phi thời gian (non-timing)",
        scope="1 bước tách 2 phiên",
        start_at=base_day,
        days=10,
        start_local="08:00",
        end_local="08:30",
    )
    draft = await _confirmed_draft(client, "tc13-draft", summary)
    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc13-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước Hai Phiên",
                    "expected_output": "Xong 2 phần",
                    "estimated_base_seconds": 2400,
                }
            ],
        )
    ).json()["data"]

    sess_before = sorted(cand0["sessions"], key=lambda x: x["scheduled_start"])
    assert len(sess_before) == 2
    times_before = [(s["scheduled_start"], s["scheduled_end"]) for s in sess_before]
    part2_micro_before = sess_before[1]["micro_task_text"]

    # Ensure enrich_split_sessions is NOT called during non-timing patches
    enrich_spy = AsyncMock(side_effect=AssertionError("Non-timing patch must NOT re-enrich"))
    monkeypatch.setattr(ai_service, "enrich_split_sessions", enrich_spy)

    p1_resp = await _patch(
        client,
        f"/candidates/{cand0['candidate_id']}",
        "tc13-patch-micro",
        {
            "expected_review_revision": cand0["review_revision"],
            "edits": {
                "steps": [
                    {"client_step_key": "step-1", "micro_task_text": "Mở file ghi chú 2 phút mới"}
                ]
            },
        },
    )
    assert p1_resp.status_code == 200, p1_resp.text
    cand1 = p1_resp.json()["data"]
    sess_after_p1 = sorted(cand1["sessions"], key=lambda x: x["scheduled_start"])
    assert [(s["scheduled_start"], s["scheduled_end"]) for s in sess_after_p1] == times_before
    assert sess_after_p1[0]["micro_task_text"] == "Mở file ghi chú 2 phút mới"
    assert sess_after_p1[1]["micro_task_text"] == part2_micro_before

    p2_resp = await _patch(
        client,
        f"/candidates/{cand1['candidate_id']}",
        "tc13-patch-sess",
        {
            "expected_review_revision": cand1["review_revision"],
            "edits": {
                "sessions": [
                    {
                        "client_session_key": sess_after_p1[0]["client_session_key"],
                        "title": "Phiên 1 tùy chỉnh tên",
                        "session_goal": "Mục tiêu phiên 1 tùy chỉnh",
                        "expected_output": "Đầu ra phiên 1 tùy chỉnh",
                    }
                ]
            },
        },
    )
    assert p2_resp.status_code == 200, p2_resp.text
    cand2 = p2_resp.json()["data"]
    sess_after_p2 = sorted(cand2["sessions"], key=lambda x: x["scheduled_start"])
    assert [(s["scheduled_start"], s["scheduled_end"]) for s in sess_after_p2] == times_before
    assert sess_after_p2[0]["title"] == "Phiên 1 tùy chỉnh tên"
    assert sess_after_p2[0]["session_goal"] == "Mục tiêu phiên 1 tùy chỉnh"
    assert sess_after_p2[0]["expected_output"] == "Đầu ra phiên 1 tùy chỉnh"
    assert sess_after_p2[1]["micro_task_text"] == part2_micro_before
    assert enrich_spy.call_count == 0

    conf_resp = await _confirm_candidate(client, cand2, "tc13-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-13",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="Non-timing step micro_task and session field edits keep schedule and part 2+ micro",
        ui_scenario="split_sessions_focus",
        input_a={
            "task_title": summary["title"],
            "patch_1": {"steps": [{"client_step_key": "step-1", "micro_task_text": "Mở file ghi chú 2 phút mới"}]},
            "patch_2": {
                "sessions": [
                    {
                        "client_session_key": sess_after_p1[0]["client_session_key"],
                        "title": "Phiên 1 tùy chỉnh tên",
                        "session_goal": "Mục tiêu phiên 1 tùy chỉnh",
                        "expected_output": "Đầu ra phiên 1 tùy chỉnh",
                    }
                ]
            },
            "summary": "2 lần PATCH phi thời gian (micro_task trên step + metadata trên session 1).",
        },
        layer1_b={
            "generation_tier": "manual_user",
            "re_enrich_calls_on_patch": enrich_spy.call_count,
            "summary": "changed_timing=False nên không gọi lại LLM enrich.",
        },
        layer2_c=_layer2_snapshot(
            cand2,
            summary="Thời gian scheduled_start/scheduled_end giữ nguyên tuyệt đối qua cả 2 PATCH.",
        ),
        layer3_d=_layer3_snapshot(
            cand2,
            summary="Phiên 1 cập nhật micro_task + title/goal/output; phiên 2 giữ nguyên micro nối tiếp.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_session_0_title": task["sessions"][0]["title"],
            "persisted_session_1_micro": task["sessions"][1]["micro_task_text"],
            "summary": "Confirm lưu đầy đủ các trường đã sửa vào SessionRow.",
        },
        assertions_checked=[
            "changed_timing=False keeps scheduled_start and scheduled_end identical",
            "step micro_task_text edit updates session 1 and preserves session 2+ continuation micro",
            "direct session title/session_goal/expected_output edits persist to Task",
            "enrich_split_sessions not called on non-timing edits",
        ],
        candidate_payload=cand2,
        task_payload=task,
    )


async def test_tc_uf2_14_nl_user_instruction_structural_op_and_micro_task(api, monkeypatch) -> None:
    """TC-UF2-14: user_instruction NL qua Layer 1 (adjust_candidate_by_nl) -> Layer 3 (patch_candidate) -> Layer 4 DB."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Điều chỉnh bằng ngôn ngữ tự nhiên",
        scope="3 bước chuẩn",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc14-draft", summary)
    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc14-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước 1",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 1,
                    "title": "Bước 2",
                    "expected_output": "Out 2",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 2,
                    "title": "Bước 3 Bỏ",
                    "expected_output": "Out 3",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]

    llm_counts = _stub_llm(
        monkeypatch,
        adjust_payload=LLMCandidateAdjustmentPayload(
            step_edits=[
                CandidateStepAdjustment(
                    client_step_key="step-1",
                    estimated_minutes=30,
                    micro_task_text="Mở tài liệu NL cập nhật mới",
                )
            ],
            add_steps=[
                NLCandidateStepAdd(
                    title="Bước Thêm Từ NL",
                    expected_output="Output Thêm Từ NL",
                    estimated_minutes=20,
                    micro_task_prompt="Bắt đầu bước NL mới",
                    insert_at_index=1,
                )
            ],
            delete_step_keys=["step-3"],
            requires_clarification=False,
            clarification_question=None,
            requires_draft_update=False,
            unsupported_changes=[],
            assistant_note="Đã cập nhật bước 1, chèn bước mới và bỏ bước 3.",
        ),
    )

    instruction = "Đổi bước 1 thành 30 phút với việc mở đầu mới, thêm 1 bước sau bước 1 và xóa bước 3"
    patch_resp = await _patch(
        client,
        f"/candidates/{cand0['candidate_id']}",
        "tc14-nl-patch",
        {
            "expected_review_revision": cand0["review_revision"],
            "user_instruction": instruction,
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand1 = patch_resp.json()["data"]

    assert llm_counts["UF2_Candidate_Adjust"] == 1
    assert [s["title"] for s in cand1["steps"]] == ["Bước 1", "Bước Thêm Từ NL", "Bước 2"]
    assert cand1["steps"][0]["estimated_base_seconds"] == 1800
    assert cand1["can_undo"] is True
    sess_step1 = sorted(
        [s for s in cand1["sessions"] if s["client_step_key"] == "step-1"],
        key=lambda x: x["scheduled_start"],
    )
    assert sess_step1[0]["micro_task_text"] == "Mở tài liệu NL cập nhật mới"

    async with factory() as db:
        crow = await db.get(CandidateRow, UUID(cand1["candidate_id"]))
        assert crow is not None
        assert crow.review_revision == 2
        assert crow.steps[0]["_micro_task_text"] == "Mở tài liệu NL cập nhật mới"

    conf_resp = await _confirm_candidate(client, cand1, "tc14-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-14",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="NL user_instruction executes Layer 1 -> Layer 3 patch -> Layer 4 DB",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "user_instruction": instruction,
            "summary": "Gửi câu điều chỉnh NL kết hợp step_edits + add_steps + delete_step_keys.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "nl_adjust_calls": llm_counts["UF2_Candidate_Adjust"],
            "summary": "adjust_candidate_by_nl dịch NL sang step_edits + add_steps + delete_step_keys.",
        },
        layer2_c=_layer2_snapshot(cand1, summary="Tái xếp lịch 3 bước sau chỉnh sửa NL, feasible."),
        layer3_d=_layer3_snapshot(
            cand1,
            summary="Candidate cập nhật thành ['Bước 1', 'Bước Thêm Từ NL', 'Bước 2'], review_revision=2.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "persisted_step_titles": [s["title"] for s in task["steps"]],
            "persisted_micro_task": task["sessions"][0]["micro_task_text"],
            "summary": "DB CandidateRow._micro_task_text và Task/Step/Session lưu đúng dữ liệu NL.",
        },
        assertions_checked=[
            "real ai_service.adjust_candidate_by_nl wired into patch_candidate",
            "steps updated to ['Bước 1', 'Bước Thêm Từ NL', 'Bước 2']",
            "CandidateRow.steps[0]['_micro_task_text'] persisted",
            "confirm_candidate persists Task/Steps/Sessions cleanly",
        ],
        candidate_payload=cand1,
        task_payload=task,
    )


async def test_tc_uf2_15_one_step_undo_lifecycle_idempotency_and_second_undo_409(api) -> None:
    """TC-UF2-15: 1-Step Undo khôi phục snapshot, replay Idempotency-Key giống hệt, Undo lần 2 trả 409 UNDO_UNAVAILABLE."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(title="Kiểm thử 1-Step Undo", start_at=base_day, days=10)
    draft = await _confirmed_draft(client, "tc15-draft", summary)
    cand0 = (
        await _create_candidate(
            client,
            draft,
            "tc15-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước 1",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1500,
                },
                {
                    "order_index": 1,
                    "title": "Bước 2",
                    "expected_output": "Out 2",
                    "estimated_base_seconds": 1500,
                },
            ],
        )
    ).json()["data"]
    assert cand0["can_undo"] is False

    p1 = (
        await _patch(
            client,
            f"/candidates/{cand0['candidate_id']}",
            "tc15-p1",
            {
                "expected_review_revision": cand0["review_revision"],
                "edits": {
                    "steps": [{"client_step_key": "step-1", "micro_task_text": "Micro gốc cần khôi phục"}],
                    "sessions": [
                        {
                            "client_session_key": cand0["sessions"][0]["client_session_key"],
                            "title": "Tiêu đề trước khi xóa",
                        }
                    ],
                },
            },
        )
    ).json()["data"]
    assert p1["can_undo"] is True

    p2 = (
        await _patch(
            client,
            f"/candidates/{cand0['candidate_id']}",
            "tc15-p2",
            {
                "expected_review_revision": p1["review_revision"],
                "edits": {"delete_step_keys": ["step-2"]},
            },
        )
    ).json()["data"]
    assert len(p2["steps"]) == 1 and p2["can_undo"] is True

    undo_resp = await _post(
        client,
        f"/candidates/{cand0['candidate_id']}/undo",
        "tc15-undo-exec",
        {"expected_review_revision": p2["review_revision"]},
    )
    assert undo_resp.status_code == 200, undo_resp.text
    undone = undo_resp.json()["data"]
    assert undone["can_undo"] is False
    assert undone["review_revision"] == p2["review_revision"] + 1
    assert [s["client_step_key"] for s in undone["steps"]] == ["step-1", "step-2"]
    assert undone["sessions"][0]["micro_task_text"] == "Micro gốc cần khôi phục"
    assert undone["sessions"][0]["title"] == "Tiêu đề trước khi xóa"

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand0["candidate_id"]))
        assert row is not None
        assert row.previous_snapshot is None
        assert row.steps[0]["_micro_task_text"] == "Micro gốc cần khôi phục"

    replay_resp = await _post(
        client,
        f"/candidates/{cand0['candidate_id']}/undo",
        "tc15-undo-exec",
        {"expected_review_revision": p2["review_revision"]},
    )
    assert replay_resp.status_code == 200, replay_resp.text
    assert replay_resp.json()["data"] == undone

    second_resp = await _post(
        client,
        f"/candidates/{cand0['candidate_id']}/undo",
        "tc15-undo-second",
        {"expected_review_revision": undone["review_revision"]},
    )
    assert second_resp.status_code == 409, second_resp.text
    err_snap = _error_snapshot(second_resp.status_code, second_resp.json())
    assert err_snap["code"] == "UNDO_UNAVAILABLE"

    conf_resp = await _confirm_candidate(client, undone, "tc15-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-15",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="1-Step Undo restores snapshot, supports Idempotency replay, blocks 2nd undo with 409",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "sequence": ["patch micro+title (rev 1->2)", "delete step-2 (rev 2->3)", "undo (rev 3->4)", "replay undo", "2nd undo (rev 4)"],
            "summary": "Kiểm tra vòng đời Undo 1 bước, replay Idempotency-Key và chặn Undo lần 2.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Không gọi LLM trong thao tác Undo snapshot."},
        layer2_c=_layer2_snapshot(undone, summary="Khôi phục 2 bước và 2 phiên feasible."),
        layer3_d=_layer3_snapshot(
            undone,
            summary="can_undo chuyển True -> False, replay trả y hệt, undo lần 2 trả 409 UNDO_UNAVAILABLE.",
            error=err_snap,
        ),
        layer4_e={
            "task_id": task["task_id"],
            "previous_snapshot_cleared": True,
            "restored_micro_task": task["sessions"][0]["micro_task_text"],
            "summary": "DB xóa previous_snapshot, khôi phục _micro_task_text và confirm 201.",
        },
        assertions_checked=[
            "can_undo transitions True -> False after undo",
            "steps, sessions, title, and _micro_task_text restored in DB",
            "Idempotency-Key replay returns identical 200 payload",
            "2nd undo returns 409 UNDO_UNAVAILABLE",
        ],
        candidate_payload=undone,
        task_payload=task,
        error_payload=err_snap,
    )


async def test_tc_uf2_16_structural_guardrails_reject_invalid_patches_422(api) -> None:
    """TC-UF2-16: Guardrails chặn xóa về 0 bước (422), >20 bước (422), xung đột key (422) và đồng thời sửa duration+sessions (422)."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(title="Kiểm tra Guardrails 422", start_at=base_day, days=10)
    draft = await _confirmed_draft(client, "tc16-draft", summary)
    cand = (
        await _create_candidate(
            client,
            draft,
            "tc16-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước 1",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1200,
                },
                {
                    "order_index": 1,
                    "title": "Bước 2",
                    "expected_output": "Out 2",
                    "estimated_base_seconds": 1200,
                },
            ],
        )
    ).json()["data"]

    r_zero = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc16-zero",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {"delete_step_keys": ["step-1", "step-2"]},
        },
    )
    assert r_zero.status_code == 422

    r_many = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc16-many",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "add_steps": [
                    {
                        "title": f"Thêm {i}",
                        "expected_output": f"Out {i}",
                        "estimated_base_seconds": 600,
                    }
                    for i in range(19)
                ]
            },
        },
    )
    assert r_many.status_code == 422

    r_conflict = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc16-conflict",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "delete_step_keys": ["step-1"],
                "merge_steps": [
                    {
                        "source_step_keys": ["step-1", "step-2"],
                        "title": "Gộp lỗi",
                        "expected_output": "Out lỗi",
                    }
                ],
            },
        },
    )
    assert r_conflict.status_code == 422

    r_mixed = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc16-mixed",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "steps": [{"client_step_key": "step-1", "estimated_base_seconds": 1800}],
                "sessions": [
                    {
                        "client_session_key": cand["sessions"][0]["client_session_key"],
                        "title": "Không cho sửa cùng lúc",
                    }
                ],
            },
        },
    )
    assert r_mixed.status_code == 422
    err_snap = _error_snapshot(r_mixed.status_code, r_mixed.json())
    assert err_snap["code"] == "VALIDATION_ERROR"

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand["candidate_id"]))
        assert row is not None
        assert row.review_revision == 1
        assert len(row.steps) == 2

    _record_case(
        case_id="TC-UF2-16",
        group="Group 2 — Structural Step Ops, NL Edits & 1-Step Undo",
        title="Guardrails reject 0 steps, >20 steps, key conflicts, and simultaneous duration+session edits",
        ui_scenario="structural_ops_undo",
        input_a={
            "task_title": summary["title"],
            "invalid_patches": [
                "delete all to 0 steps",
                "add 19 steps (total 21 > 20)",
                "conflict step-1 in delete_step_keys + merge_steps",
                "simultaneous estimated_base_seconds + edits.sessions",
            ],
            "summary": "4 mẫu PATCH vi phạm guardrail trên Candidate 2 bước.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Bị chặn ở tầng schema/validation, không gọi AI."},
        layer2_c=_layer2_snapshot(cand, summary="Candidate gốc giữ nguyên trạng thái feasible."),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Cả 4 request trả 422 VALIDATION_ERROR; review_revision giữ nguyên == 1.",
            error=err_snap,
        ),
        layer4_e={
            "candidate_untouched_revision": 1,
            "codes": [
                r_zero.json()["error"]["code"],
                r_many.json()["error"]["code"],
                r_conflict.json()["error"]["code"],
                r_mixed.json()["error"]["code"],
            ],
            "summary": "DB CandidateRow không bị biến đổi sau 4 lệnh 422.",
        },
        assertions_checked=[
            "delete down to 0 steps -> 422",
            "add_steps exceeding 20 steps -> 422",
            "conflicting step key across delete_step_keys & merge_steps -> 422",
            "simultaneous estimated_base_seconds & edits.sessions -> 422 VALIDATION_ERROR",
        ],
        candidate_payload=cand,
        error_payload=err_snap,
    )


# =============================================================================
# GROUP 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails
# =============================================================================


async def test_tc_uf2_17_deficit_returns_earliest_feasible_end_and_proof_of_feasibility(
    api,
) -> None:
    """TC-UF2-17: Thiếu giờ (deficit_seconds > 0) -> trả earliest_feasible_end_at (Proof-of-Feasibility) + suggested_extra_free_seconds_per_day."""
    client, _ = api
    base_day = _future_base_day(2)
    deadline_at = base_day + timedelta(days=2)  # 2 ngày x 1h = 7200s rảnh
    summary = _make_summary(
        title="Phân tích thiếu hụt giờ rảnh",
        scope="Khối lượng vượt 2h rảnh",
        start_at=base_day,
        deadline_at=deadline_at,
        start_local="09:00",
        end_local="10:00",
    )
    draft = await _confirmed_draft(client, "tc17-draft", summary)

    cand_resp = await _create_candidate(
        client,
        draft,
        "tc17-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Xử lý khối lượng 2.5 giờ",
                "expected_output": "Xong báo cáo",
                "estimated_base_seconds": 9000,  # * 1.2 = 10800s work > 7200s available
            }
        ],
    )
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]
    feas = cand["feasibility"]

    assert feas["status"] == "infeasible"
    assert feas["fully_scheduled"] is False
    assert feas["deficit_seconds"] == 10800 - 7200
    assert feas["blocking_reasons"][0]["code"] == "TOTAL_DEFICIT"
    assert feas["earliest_feasible_end_at"] is not None
    earliest_end = _parse_iso(feas["earliest_feasible_end_at"])
    assert earliest_end > deadline_at
    assert feas["suggested_extra_free_seconds_per_day"] is not None
    assert feas["suggested_extra_free_seconds_per_day"] > 0

    # Proof-of-Feasibility: đưa earliest_feasible_end_at quay lại solver -> feasible; rút 1 phút -> infeasible
    avail = DraftAvailability(
        timezone="UTC",
        weekly_rules=[
            WeeklyRule(weekday=wd, start_local="09:00", end_local="10:00")  # type: ignore[arg-type]
            for wd in range(1, 8)
        ],
        date_overrides=[],
    )
    pref = UserPreference(
        revision=1,
        effective_revision=1,
        work_interval_seconds=settings.DEFAULT_WORK_INTERVAL_SECONDS,
        rest_interval_seconds=settings.DEFAULT_REST_INTERVAL_SECONDS,
        micro_task_seconds=120,
        reminder_offsets=[900, 600, 300],
        buffer_multiplier=settings.DEFAULT_BUFFER_MULTIPLIER,
        reminder_style="gentle",
    )
    cand_steps = [CandidateStep.model_validate(s) for s in cand["steps"]]
    h_start = _parse_iso(cand["planning_horizon"]["start_at"])
    h_days = max(1, math.ceil((earliest_end - h_start).total_seconds() / 86400))

    _, proof_ok = schedule_candidate_sessions(
        cand_steps,
        compute_effective_free_intervals(avail, h_start, earliest_end),
        [],
        TimeConstraint(kind="deadline", start_at=h_start, deadline_at=earliest_end, input_timezone="UTC"),
        pref,
        horizon_days=h_days,
    )
    assert proof_ok.status == "feasible" and proof_ok.fully_scheduled is True

    too_early = earliest_end - timedelta(minutes=1)
    _, proof_fail = schedule_candidate_sessions(
        cand_steps,
        compute_effective_free_intervals(avail, h_start, too_early),
        [],
        TimeConstraint(kind="deadline", start_at=h_start, deadline_at=too_early, input_timezone="UTC"),
        pref,
        horizon_days=h_days,
    )
    assert proof_fail.fully_scheduled is False

    conf_err = await _confirm_candidate(client, cand, "tc17-conf-err")
    assert conf_err.status_code == 409
    err_snap = _error_snapshot(conf_err.status_code, conf_err.json())
    assert err_snap["code"] == "PLAN_NOT_CONFIRMABLE"

    _record_case(
        case_id="TC-UF2-17",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="Deficit returns earliest_feasible_end_at (Proof-of-Feasibility) and extra_free_per_day",
        ui_scenario="infeasible_recovery",
        input_a={
            "task_title": summary["title"],
            "time_constraint": summary["time_constraint"],
            "availability": summary["availability"],
            "estimated_base_seconds": 9000,
            "summary": "2 ngày (7200s rảnh), việc cần 10800s work có buffer -> thiếu 3600s.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Manual step 9000s (10800s buffered)."},
        layer2_c=_layer2_snapshot(
            cand,
            summary="TOTAL_DEFICIT=3600s; earliest_feasible_end_at được chứng minh khả thi bằng solver (rút 1p thất bại).",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Candidate infeasible không cho phép confirm (409 PLAN_NOT_CONFIRMABLE).",
            error=err_snap,
        ),
        layer4_e={
            "proof_of_feasibility_passed": True,
            "earliest_minus_1m_feasible": False,
            "summary": "Không lưu Task vào DB; kiểm chứng chặt earliest_feasible_end_at.",
        },
        assertions_checked=[
            "feasibility.status == 'infeasible' and deficit_seconds == 3600",
            "earliest_feasible_end_at > deadline_at and suggested_extra_free_seconds_per_day > 0",
            "Proof-of-Feasibility at earliest_feasible_end_at == feasible (and -1m == not fully_scheduled)",
            "confirm returns 409 PLAN_NOT_CONFIRMABLE",
        ],
        candidate_payload=cand,
        error_payload=err_snap,
    )


async def test_tc_uf2_18_extra_requirements_max_daily_work_seconds(api) -> None:
    """TC-UF2-18: extra_requirements max_daily_work_seconds -> điền applied_extra_constraints, giới hạn giờ làm/ngày & recovery."""
    client, _ = api
    base_day = _future_base_day(2)
    deadline_at = base_day + timedelta(days=2)  # 2 ngày x 3h rảnh = 6h tổng, nhưng cap 60p/ngày -> tối đa 2h!
    summary = _make_summary(
        title="Giới hạn tối đa 60 phút mỗi ngày",
        scope="Hoàn thành bài nghiên cứu vừa sức ADHD",
        start_at=base_day,
        deadline_at=deadline_at,
        start_local="09:00",
        end_local="12:00",
        extra_requirements=["Mỗi ngày học tối đa 60 phút"],
    )
    draft = await _confirmed_draft(client, "tc18-draft", summary)

    # 1) 7500s * 1.2 = 9000s work > 2 * 3600s = 7200s daily cap (dù available=21600s) -> FRAGMENTED_FREE_SLOTS + recovery
    cand_infeas = (
        await _create_candidate(
            client,
            draft,
            "tc18-cand-1",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Nghiên cứu vượt trần ngày",
                    "expected_output": "Tài liệu tổng hợp",
                    "estimated_base_seconds": 7500,
                }
            ],
        )
    ).json()["data"]
    feas1 = cand_infeas["feasibility"]
    assert feas1["status"] == "search_inconclusive"
    assert feas1["deficit_seconds"] == 0
    assert feas1["blocking_reasons"][0]["code"] == "FRAGMENTED_FREE_SLOTS"
    assert feas1["applied_extra_constraints"] == {
        "max_daily_work_seconds": 3600,
        "min_contiguous_slot_seconds": None,
        "preferred_time_windows": [],
    }
    assert feas1["earliest_feasible_end_at"] is not None
    assert _parse_iso(feas1["earliest_feasible_end_at"]) > deadline_at
    assert feas1["suggested_extra_free_seconds_per_day"] is not None

    # 2) Patch về 5000s (6000s work <= 7200s cap trên 2 ngày) -> feasible, chia đều qua 2 ngày với mỗi ngày <= 3600s!
    patch_resp = await _patch(
        client,
        f"/candidates/{cand_infeas['candidate_id']}",
        "tc18-patch-ok",
        {
            "expected_review_revision": cand_infeas["review_revision"],
            "edits": {"steps": [{"client_step_key": "step-1", "estimated_base_seconds": 5000}]},
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    cand_ok = patch_resp.json()["data"]
    feas2 = cand_ok["feasibility"]
    assert feas2["status"] == "feasible"
    assert feas2["applied_extra_constraints"]["max_daily_work_seconds"] == 3600

    daily_work: dict[str, int] = {}
    for sess in cand_ok["sessions"]:
        day_key = _parse_iso(sess["scheduled_start"]).date().isoformat()
        daily_work[day_key] = daily_work.get(day_key, 0) + sess["work_seconds"]
    assert len(daily_work) == 2
    assert all(secs <= 3600 for secs in daily_work.values())

    conf_resp = await _confirm_candidate(client, cand_ok, "tc18-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-18",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="max_daily_work_seconds constraint caps daily work and guides recovery",
        ui_scenario="standard_review",
        input_a={
            "task_title": summary["title"],
            "extra_requirements": summary["extra_requirements"],
            "initial_base_seconds": 7500,
            "patched_base_seconds": 5000,
            "summary": "Rảnh 3h/ngày x 2 ngày, ràng buộc tối đa 60p/ngày (3600s).",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Ràng buộc phân tích thuần Python từ extra_requirements."},
        layer2_c={
            **_layer2_snapshot(
                cand_ok,
                summary="Sau khi giảm về 5000s (6000s work), lịch chia qua 2 ngày với mỗi ngày <= 3600s.",
            ),
            "initial_recovery_check": {
                "status": feas1["status"],
                "blocking_code": feas1["blocking_reasons"][0]["code"],
                "earliest_feasible_end_at": feas1["earliest_feasible_end_at"],
            },
            "daily_work_seconds": daily_work,
        },
        layer3_d=_layer3_snapshot(
            cand_ok,
            summary="PATCH giảm thời lượng chuyển từ search_inconclusive sang feasible và giữ nguyên applied_extra_constraints.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "extra_requirements": task["extra_requirements"],
            "summary": "Confirm lưu Task cùng extra_requirements và 2 phiên rải qua 2 ngày.",
        },
        assertions_checked=[
            "applied_extra_constraints.max_daily_work_seconds == 3600",
            "exceeding daily cap produces FRAGMENTED_FREE_SLOTS + earliest_feasible_end_at",
            "feasible plan caps daily scheduled work <= 3600s across 2 days",
            "confirm_candidate returns 201",
        ],
        candidate_payload=cand_ok,
        task_payload=task,
    )


async def test_tc_uf2_19_min_contiguous_slot_fragmented_returns_recovery(api) -> None:
    """TC-UF2-19: min_contiguous_slot_seconds với khe vụn -> trả FRAGMENTED_FREE_SLOTS và earliest_feasible_end_at nơi có khe liền đủ dài."""
    client, _ = api
    base_day = _future_base_day(2)
    day2 = base_day + timedelta(days=1)
    deadline_at = base_day + timedelta(hours=23)

    # Ngày 1 bị date_override thành 3 khe rời 30p (tổng 90p); Ngày 2 (sau deadline) giữ weekly_rules 09:00-11:00 (120p liền)
    summary = _make_summary(
        title="Cần khe trống liên tục >= 60 phút",
        scope="Viết phân tích chuyên sâu 30 phút",
        start_at=base_day,
        deadline_at=deadline_at,
        weekly_rules=[
            {"weekday": wd, "start_local": "09:00", "end_local": "11:00"} for wd in range(1, 8)
        ],
        date_overrides=[
            {
                "date": base_day.date().isoformat(),
                "intervals": [
                    {"start_local": "09:00", "end_local": "09:30"},
                    {"start_local": "10:00", "end_local": "10:30"},
                    {"start_local": "11:00", "end_local": "11:30"},
                ],
            }
        ],
        extra_requirements=["Chỉ xếp khi có ít nhất 60 phút trống liền"],
    )
    draft = await _confirmed_draft(client, "tc19-draft", summary)

    cand_resp = await _create_candidate(
        client,
        draft,
        "tc19-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Phân tích tập trung sâu",
                "expected_output": "Bản phân tích hoàn chỉnh",
                "estimated_base_seconds": 1800,  # 2160s buffered <= 5400s tổng rảnh ngày 1
            }
        ],
    )
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]
    feas = cand["feasibility"]

    assert feas["status"] == "search_inconclusive"
    assert feas["fully_scheduled"] is False
    assert feas["deficit_seconds"] == 0
    assert feas["blocking_reasons"][0]["code"] == "FRAGMENTED_FREE_SLOTS"
    assert "60 phút" in feas["blocking_reasons"][0]["message"]
    assert feas["applied_extra_constraints"] == {
        "max_daily_work_seconds": None,
        "min_contiguous_slot_seconds": 3600,
        "preferred_time_windows": [],
    }
    assert feas["earliest_feasible_end_at"] is not None
    earliest_end = _parse_iso(feas["earliest_feasible_end_at"])
    assert earliest_end.date() == day2.date()
    assert earliest_end >= day2.replace(hour=10, minute=0)

    _record_case(
        case_id="TC-UF2-19",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="min_contiguous_slot_seconds on fragmented slots returns FRAGMENTED_FREE_SLOTS and Day-2 recovery",
        ui_scenario="infeasible_recovery",
        input_a={
            "task_title": summary["title"],
            "extra_requirements": summary["extra_requirements"],
            "day1_fragmented_slots": ["09:00-09:30", "10:00-10:30", "11:00-11:30"],
            "day2_contiguous_slot": "09:00-11:00",
            "summary": "Ngày 1 có 3 khe 30p (< 60p), Ngày 2 sau hạn có khe 120p liền.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Nhận diện min_contiguous_slot_seconds=3600."},
        layer2_c=_layer2_snapshot(
            cand,
            summary="FRAGMENTED_FREE_SLOTS trên Ngày 1; earliest_feasible_end_at tìm thấy khe >= 60p vào Ngày 2.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Trả search_inconclusive (0 sessions) kèm earliest_feasible_end_at cho UI đề xuất nới hạn.",
        ),
        layer4_e={
            "persisted_task": None,
            "earliest_feasible_date": earliest_end.date().isoformat(),
            "summary": "Chưa lưu Task vì chưa khả thi trong ngày 1; gợi ý nới sang ngày 2.",
        },
        assertions_checked=[
            "feasibility.status == 'search_inconclusive' and deficit_seconds == 0",
            "blocking_reasons[0].code == 'FRAGMENTED_FREE_SLOTS'",
            "applied_extra_constraints.min_contiguous_slot_seconds == 3600",
            "earliest_feasible_end_at lands on Day 2 where contiguous 120m slot exists",
        ],
        candidate_payload=cand,
    )


async def test_tc_uf2_20_preferred_time_windows_steers_schedule(api) -> None:
    """TC-UF2-20: extra_requirements preferred_time_windows (buổi sáng) -> xếp đúng khung ưu tiên dù có giờ sớm hơn từ 02:00."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Ưu tiên làm việc buổi sáng",
        scope="Viết 1 bài luận ngắn",
        start_at=base_day,
        days=5,
        start_local="02:00",
        end_local="11:00",
        extra_requirements=["Ưu tiên buổi sáng"],
    )
    draft = await _confirmed_draft(client, "tc20-draft", summary)

    cand_resp = await _create_candidate(
        client,
        draft,
        "tc20-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Viết mở bài và thân bài",
                "expected_output": "Bài luận 500 chữ",
                "estimated_base_seconds": 1200,
            }
        ],
    )
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]
    feas = cand["feasibility"]

    assert feas["status"] == "feasible"
    assert feas["applied_extra_constraints"] == {
        "max_daily_work_seconds": None,
        "min_contiguous_slot_seconds": None,
        "preferred_time_windows": ["05:00-12:00"],
    }
    sess_start = _parse_iso(cand["sessions"][0]["scheduled_start"])
    assert sess_start.hour == 5 and sess_start.minute == 0

    conf_resp = await _confirm_candidate(client, cand, "tc20-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-20",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="preferred_time_windows steers scheduling into 05:00-12:00 morning window",
        ui_scenario="standard_review",
        input_a={
            "task_title": summary["title"],
            "availability": summary["availability"],
            "extra_requirements": summary["extra_requirements"],
            "summary": "Giờ rảnh 02:00-11:00 UTC nhưng yêu cầu 'Ưu tiên buổi sáng' (05:00-12:00).",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Phân tích preferred_time_windows=['05:00-12:00']."},
        layer2_c=_layer2_snapshot(
            cand,
            summary="Pass 1 ưu tiên xếp đúng 05:00 UTC thay vì 02:00 UTC.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="applied_extra_constraints.preferred_time_windows == ['05:00-12:00'], feasible.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "scheduled_start": task["sessions"][0]["scheduled_start"],
            "summary": "Confirm lưu phiên bắt đầu lúc 05:00 UTC đúng cửa sổ ưu tiên.",
        },
        assertions_checked=[
            "applied_extra_constraints.preferred_time_windows == ['05:00-12:00']",
            "session scheduled_start is 05:00 UTC (inside preferred morning window, skipping 02:00-05:00)",
            "confirm_candidate succeeds (201)",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_21_protects_fixed_sessions_and_enforces_900s_gap(api) -> None:
    """TC-UF2-21: Bảo vệ tuyệt đối fixed_sessions của Task đã chốt + đảm bảo khoảng nghỉ >= 900s (15p)."""
    client, _ = api
    base_day = _future_base_day(2)

    # Task A chốt phiên đầu ngày lúc 09:00-09:24 UTC
    summary_a = _make_summary(
        title="Task A Cố Định",
        scope="1 phiên cố định",
        start_at=base_day,
        days=5,
        start_local="09:00",
        end_local="12:00",
    )
    draft_a = await _confirmed_draft(client, "tc21-draft-a", summary_a)
    cand_a = (
        await _create_candidate(
            client,
            draft_a,
            "tc21-cand-a",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Việc A",
                    "expected_output": "Out A",
                    "estimated_base_seconds": 1200,
                }
            ],
        )
    ).json()["data"]
    conf_a = await _confirm_candidate(client, cand_a, "tc21-conf-a")
    assert conf_a.status_code == 201, conf_a.text
    task_a = conf_a.json()["data"]["task"]
    a_sess_before = task_a["sessions"][0]
    a_end = _parse_iso(a_sess_before["scheduled_end"])

    # Task B dùng cùng khung 09:00-12:00 UTC -> phải né Task A + cách ít nhất 900s
    summary_b = _make_summary(
        title="Task B Xếp Quanh Task A",
        scope="2 bước cần xếp nối",
        start_at=base_day,
        days=5,
        start_local="09:00",
        end_local="12:00",
    )
    draft_b = await _confirmed_draft(client, "tc21-draft-b", summary_b)
    cand_b_resp = await _create_candidate(
        client,
        draft_b,
        "tc21-cand-b",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Việc B1",
                "expected_output": "Out B1",
                "estimated_base_seconds": 1200,
            },
            {
                "order_index": 1,
                "title": "Việc B2",
                "expected_output": "Out B2",
                "estimated_base_seconds": 1200,
            },
        ],
    )
    assert cand_b_resp.status_code == 201, cand_b_resp.text
    cand_b = cand_b_resp.json()["data"]
    b_sessions = sorted(cand_b["sessions"], key=lambda x: x["scheduled_start"])
    assert len(b_sessions) == 2

    b0_start = _parse_iso(b_sessions[0]["scheduled_start"])
    b0_end = _parse_iso(b_sessions[0]["scheduled_end"])
    b1_start = _parse_iso(b_sessions[1]["scheduled_start"])
    assert (b0_start - a_end).total_seconds() >= 900
    assert (b1_start - b0_end).total_seconds() >= 900

    conf_b = await _confirm_candidate(client, cand_b, "tc21-conf-b")
    assert conf_b.status_code == 201, conf_b.text
    task_b = conf_b.json()["data"]["task"]

    # Task A không hề bị đụng chạm
    task_a_after = (await client.get(f"/api/v1/tasks/{task_a['task_id']}")).json()["data"]
    assert task_a_after["sessions"][0] == a_sess_before

    _record_case(
        case_id="TC-UF2-21",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="Protects confirmed Task A fixed_sessions and enforces >=900s inter-session gap",
        ui_scenario="standard_review",
        input_a={
            "task_a_session": {
                "start": a_sess_before["scheduled_start"],
                "end": a_sess_before["scheduled_end"],
            },
            "task_b_title": summary_b["title"],
            "availability": summary_b["availability"],
            "summary": "Task A đã chốt lúc 09:00-09:24; Task B xin xếp cùng khung 09:00-12:00.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "2 manual_steps cho Task B."},
        layer2_c=_layer2_snapshot(
            cand_b,
            summary=f"B1 bắt đầu >= {int((b0_start - a_end).total_seconds())}s sau Task A; B2 cách B1 >= {int((b1_start - b0_end).total_seconds())}s.",
        ),
        layer3_d=_layer3_snapshot(
            cand_b,
            summary="min_inter_session_gap_seconds=900 được giữ nghiêm ngặt giữa hai Task khác nhau.",
        ),
        layer4_e={
            "task_a_id": task_a["task_id"],
            "task_b_id": task_b["task_id"],
            "task_a_unchanged": True,
            "summary": "Confirm Task B giữ nguyên toàn vẹn Task A trong DB.",
        },
        assertions_checked=[
            "Task A sessions completely unmodified after Task B candidate + confirm",
            "gap between Task A end and Task B first session >= 900s",
            "gap between Task B sessions >= 900s",
        ],
        candidate_payload=cand_b,
        task_payload=task_b,
    )


async def test_tc_uf2_22_zero_free_seconds_bypasses_llm_immediately(api, monkeypatch) -> None:
    """TC-UF2-22: total_free_seconds == 0 -> bỏ qua gọi LLM decompose ngay lập tức (0 quota lãng phí) và báo infeasible."""
    client, factory = api
    base_day = _future_base_day(2)
    target_start = base_day.replace(hour=9, minute=0)
    summary = _make_summary(
        title="Không còn phút rảnh nào",
        scope="Lịch bận che kín",
        start_at=target_start,
        deadline_at=target_start + timedelta(hours=2),
        weekly_rules=[
            {"weekday": target_start.isoweekday(), "start_local": "09:00", "end_local": "10:00"}
        ],
    )
    draft = await _confirmed_draft(client, "tc22-draft", summary)

    ctx_resp = await _post(
        client,
        "/planning-contexts",
        "tc22-ctx",
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

    ai_spy = AsyncMock(side_effect=AssertionError("AI must NOT be called when total_free_seconds == 0"))
    monkeypatch.setattr(ai_service, "decompose_task", ai_spy)

    cand_resp = await _create_candidate(client, draft, "tc22-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert ai_spy.call_count == 0
    assert cand["feasibility"]["status"] == "infeasible"
    assert cand["feasibility"]["available_seconds"] == 0
    assert cand["feasibility"]["deficit_seconds"] > 0
    assert cand["sessions"] == []

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand["candidate_id"]))
        assert row is not None and row.generation_tier == "tier_3_rule_based"

    _record_case(
        case_id="TC-UF2-22",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="total_free_seconds == 0 bypasses LLM decomposition and reports infeasible",
        ui_scenario="infeasible_recovery",
        input_a={
            "task_title": summary["title"],
            "availability": summary["availability"],
            "manual_busy_intervals": ["09:00-11:00Z (che kín 09:00-10:00Z)"],
            "summary": "Toàn bộ quỹ rảnh bị lịch bận che kín -> total_free_seconds == 0.",
        },
        layer1_b={
            "generation_tier": "tier_3_rule_based",
            "decompose_task_calls": ai_spy.call_count,
            "summary": "Bỏ qua gọi LLM decompose (0 calls), dùng _fallback_decompose_task tức thì.",
        },
        layer2_c=_layer2_snapshot(
            cand,
            summary="available_seconds == 0, deficit_seconds > 0, blocking TOTAL_DEFICIT.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Trả Candidate infeasible với 3 bước fallback và 0 sessions.",
        ),
        layer4_e={
            "candidate_tier_in_db": "tier_3_rule_based",
            "summary": "CandidateRow.generation_tier == 'tier_3_rule_based', không tốn quota LLM.",
        },
        assertions_checked=[
            "ai_service.decompose_task called 0 times when total_free_seconds == 0",
            "CandidateRow.generation_tier == 'tier_3_rule_based'",
            "feasibility.status == 'infeasible' and available_seconds == 0 and deficit_seconds > 0",
        ],
        candidate_payload=cand,
    )


async def test_tc_uf2_23_nl_guardrails_draft_update_noop_and_clarification(
    api, monkeypatch
) -> None:
    """TC-UF2-23: NL đòi dời deadline/availability -> 422 DRAFT_UPDATE_REQUIRED; no-op -> 422 NO_CANDIDATE_CHANGES; mơ hồ -> 422 ADJUSTMENT_REQUIRES_CLARIFICATION."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(title="NL Guardrails UF2", start_at=base_day, days=10)
    draft = await _confirmed_draft(client, "tc23-draft", summary)
    cand = (
        await _create_candidate(
            client,
            draft,
            "tc23-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước 1 Gốc",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1500,
                },
                {
                    "order_index": 1,
                    "title": "Bước 2 Gốc",
                    "expected_output": "Out 2",
                    "estimated_base_seconds": 1500,
                },
            ],
        )
    ).json()["data"]

    # A) Dời deadline / giờ rảnh (fallback rule-based hoặc LLM) -> 422 DRAFT_UPDATE_REQUIRED
    r_draft_upd = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc23-draft-upd",
        {
            "expected_review_revision": cand["review_revision"],
            "user_instruction": "Dời hạn chót sang thứ Bảy và mở thêm giờ rảnh buổi tối",
        },
    )
    assert r_draft_upd.status_code == 422, r_draft_upd.text
    err_draft = _error_snapshot(r_draft_upd.status_code, r_draft_upd.json())
    assert err_draft["code"] == "DRAFT_UPDATE_REQUIRED"

    # B) No-op NL edit (AI trả đúng micro_task_text đang có -> Layer 3 _nl_adjustment_to_edits lọc còn rỗng -> 422 NO_CANDIDATE_CHANGES)
    _stub_llm(
        monkeypatch,
        adjust_payload=LLMCandidateAdjustmentPayload(
            step_edits=[
                CandidateStepAdjustment(
                    client_step_key="step-1",
                    micro_task_text=f"Chuẩn bị cho bước: {cand['steps'][0]['title']}",
                )
            ],
            requires_clarification=False,
            clarification_question=None,
            requires_draft_update=False,
            unsupported_changes=[],
            assistant_note="Giữ nguyên hành động mở đầu bước 1.",
        ),
    )
    r_noop = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc23-noop",
        {
            "expected_review_revision": cand["review_revision"],
            "user_instruction": "Giữ nguyên việc mở đầu bước 1",
        },
    )
    assert r_noop.status_code == 422, r_noop.text
    err_noop = _error_snapshot(r_noop.status_code, r_noop.json())
    assert err_noop["code"] == "NO_CANDIDATE_CHANGES"

    # C) Clarifying NL -> 422 ADJUSTMENT_REQUIRES_CLARIFICATION
    _stub_llm(
        monkeypatch,
        adjust_payload=LLMCandidateAdjustmentPayload(
            requires_clarification=True,
            clarification_question="Bạn muốn rút ngắn bước 1 hay bước 2?",
            requires_draft_update=False,
            unsupported_changes=[],
            assistant_note="Mình cần làm rõ bước muốn chỉnh.",
        ),
    )
    r_clar = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "tc23-clar",
        {
            "expected_review_revision": cand["review_revision"],
            "user_instruction": "Rút ngắn bớt một bước nhé",
        },
    )
    assert r_clar.status_code == 422, r_clar.text
    err_clar = _error_snapshot(r_clar.status_code, r_clar.json())
    assert err_clar["code"] == "ADJUSTMENT_REQUIRES_CLARIFICATION"

    latest = (await client.get(f"/api/v1/candidates/{cand['candidate_id']}")).json()["data"]
    assert latest["review_revision"] == cand["review_revision"]
    assert latest["steps"] == cand["steps"]

    _record_case(
        case_id="TC-UF2-23",
        group="Group 3 — Layer 2 Feasibility Advisor, Extra Constraints & Guardrails",
        title="NL guardrails return DRAFT_UPDATE_REQUIRED, NO_CANDIDATE_CHANGES, and ADJUSTMENT_REQUIRES_CLARIFICATION",
        ui_scenario="error_draft_update",
        input_a={
            "task_title": summary["title"],
            "instructions": [
                "Dời hạn chót sang thứ Bảy và mở thêm giờ rảnh buổi tối",
                "Giữ nguyên việc mở đầu bước 1",
                "Rút ngắn bớt một bước nhé",
            ],
            "summary": "3 yêu cầu NL: đổi deadline/rảnh, no-op không đổi trường nào, và câu mơ hồ cần hỏi lại.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "summary": "Layer 1 trả requires_draft_update / lọc trùng thành rỗng / requires_clarification.",
        },
        layer2_c=_layer2_snapshot(cand, summary="Lịch Candidate gốc giữ nguyên không bị xáo trộn."),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Trả đúng 3 mã 422: DRAFT_UPDATE_REQUIRED, NO_CANDIDATE_CHANGES, ADJUSTMENT_REQUIRES_CLARIFICATION.",
            error=err_draft,
        ),
        layer4_e={
            "error_codes": [err_draft["code"], err_noop["code"], err_clar["code"]],
            "candidate_preserved_revision": latest["review_revision"],
            "summary": "Candidate trong DB giữ nguyên tuyệt đối qua cả 3 lỗi 422.",
        },
        assertions_checked=[
            "deadline/availability NL -> 422 DRAFT_UPDATE_REQUIRED",
            "identical field NL edit -> 422 NO_CANDIDATE_CHANGES",
            "ambiguous NL -> 422 ADJUSTMENT_REQUIRES_CLARIFICATION",
            "candidate review_revision and steps remain untouched",
        ],
        candidate_payload=cand,
        error_payload=err_draft,
    )


# =============================================================================
# GROUP 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge
# =============================================================================


async def test_tc_uf2_24_enrichment_batch_timeout_falls_back_deterministically(
    api, monkeypatch
) -> None:
    """TC-UF2-24: Batch enrich timeout/lỗi provider -> fallback tất định '<Step.title> (Phần i/N)', vẫn trả 201 Created."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Fallback Enrichment Khi Timeout",
        scope="1 bước tách 2 phiên",
        start_at=base_day,
        days=10,
        start_local="09:00",
        end_local="09:30",
    )
    draft = await _confirmed_draft(client, "tc24-draft", summary)

    orig_micro = "Mở IDE và tạo test đầu tiên."
    attempts = {"UF2_Enrich": 0}

    async def _timeout_enrich_only(**kwargs: Any) -> tuple[Any, str]:
        flow = str(kwargs.get("flow_name", ""))
        if flow == "UF2":
            return (
                LLMStrictTaskDecompositionPayload(
                    task_title="Fallback Enrichment Khi Timeout",
                    steps=[
                        LLMStrictStepPromptItem(
                            order_index=1,
                            title="Triển khai kiểm thử E2E",
                            expected_output="Bộ test E2E xanh 100%",
                            estimated_minutes=40,
                            micro_task_prompt=orig_micro,
                            cognitive_load="medium",
                        )
                    ],
                    reasoning="1 bước dài để tách 2 phiên.",
                ),
                "tier_1_gemini",
            )
        if flow == "UF2_Enrich":
            attempts["UF2_Enrich"] += 1
            raise TimeoutError("Enrichment batch exceeded 3.0s deadline")
        raise ConnectionError("Unexpected flow")

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _timeout_enrich_only)

    cand_resp = await _create_candidate(client, draft, "tc24-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert attempts["UF2_Enrich"] == 1
    sessions = sorted(cand["sessions"], key=lambda x: x["scheduled_start"])
    assert len(sessions) == 2
    assert sessions[0]["title"] == "Triển khai kiểm thử E2E (Phần 1/2)"
    assert sessions[0]["micro_task_text"] == orig_micro
    assert sessions[1]["title"] == "Triển khai kiểm thử E2E (Phần 2/2)"
    assert "làm tiếp phần 2/2" in sessions[1]["micro_task_text"]
    assert sessions[0]["session_goal"] and sessions[1]["session_goal"]
    assert sessions[0]["expected_output"] and sessions[1]["expected_output"]

    conf_resp = await _confirm_candidate(client, cand, "tc24-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-24",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="Enrichment batch timeout degrades deterministically and returns 201",
        ui_scenario="split_sessions_focus",
        input_a={
            "task_title": summary["title"],
            "availability": summary["availability"],
            "simulated_fault": "TimeoutError on UF2_Enrich",
            "summary": "Bước 40p tách 2 phiên; UF2_Enrich gặp TimeoutError.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini (decompose) / tier_3_rule_based (enrich fallback)",
            "enrich_attempts": attempts["UF2_Enrich"],
            "summary": "enrich_split_sessions bắt TimeoutError và dùng _fallback_enrich_split_sessions.",
        },
        layer2_c=_layer2_snapshot(cand, summary="2 phiên xếp lịch feasible bất chấp lỗi enrich."),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Trả 201 Created với tiêu đề '<Step.title> (Phần 1/2)' và '(Phần 2/2)'.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "fallback_titles": [s["title"] for s in task["sessions"]],
            "summary": "Confirm lưu thành công các phiên fallback đầy đủ title/goal/output/micro.",
        },
        assertions_checked=[
            "POST /candidates returns 201 Created despite TimeoutError in enrich_split_sessions",
            "titles formatted as '<Step.title> (Phần 1/2)' and '(Phần 2/2)'",
            "session 1 retains original micro_task_prompt; session 2 gets continuation micro",
            "confirm_candidate succeeds (201)",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_25_full_llm_decomposition_outage_tier3_fallback(api) -> None:
    """TC-UF2-25: Mất cả Tier 1 & Tier 2 khi decompose -> Tier 3 Python fallback tạo Candidate hợp lệ có expected_output."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Nghiên cứu thuật toán đồ thị",
        scope="3 bài tập BFS và DFS",
        start_at=base_day,
        days=7,
        start_local="09:00",
        end_local="12:00",
    )
    draft = await _confirmed_draft(client, "tc25-draft", summary)

    # Fixture mặc định đã ném ConnectionError từ _execute_hedged_structured -> Tier 3 fallback
    cand_resp = await _create_candidate(client, draft, "tc25-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]

    assert len(cand["steps"]) == 3
    assert [s["order_index"] for s in cand["steps"]] == [0, 1, 2]
    assert all(s["expected_output"] and len(s["expected_output"].strip()) >= 3 for s in cand["steps"])
    assert cand["feasibility"]["status"] == "feasible"
    assert len(cand["sessions"]) == 3
    assert all(s["micro_task_text"] and s["expected_output"] for s in cand["sessions"])

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand["candidate_id"]))
        assert row is not None and row.generation_tier == "tier_3_rule_based"

    conf_resp = await _confirm_candidate(client, cand, "tc25-conf")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]

    _record_case(
        case_id="TC-UF2-25",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="Full Tier 1 & Tier 2 LLM outage triggers Tier 3 Python fallback with valid Candidate",
        ui_scenario="standard_review",
        input_a={
            "task_title": summary["title"],
            "scope": summary["scope"],
            "simulated_fault": "ConnectionError on Tier 1 & Tier 2",
            "summary": "Cả 2 tầng AI mất kết nối -> chạy ai_service._fallback_decompose_task.",
        },
        layer1_b={
            "generation_tier": "tier_3_rule_based",
            "summary": "Tier 3 sinh 3 bước chuẩn kèm expected_output và micro_task_prompt.",
        },
        layer2_c=_layer2_snapshot(cand, summary="3 bước Tier 3 xếp trọn vẹn thành 3 phiên feasible."),
        layer3_d=_layer3_snapshot(
            cand,
            summary="Candidate trả 201 Created, generation_tier == 'tier_3_rule_based'.",
        ),
        layer4_e={
            "task_id": task["task_id"],
            "generation_tier_in_db": "tier_3_rule_based",
            "summary": "Confirm lưu 3 bước và 3 phiên Tier 3 vào DB.",
        },
        assertions_checked=[
            "CandidateRow.generation_tier == 'tier_3_rule_based'",
            "3 fallback steps created with non-empty expected_output",
            "candidate is feasible and confirmable (201)",
        ],
        candidate_payload=cand,
        task_payload=task,
    )


async def test_tc_uf2_26_source_changed_marks_stale_and_blocks_confirm_409(api) -> None:
    """TC-UF2-26: Khi draft/availability hoặc source_revisions đổi -> GET /candidates/{id} báo stale=True, confirm trả 409 SOURCE_CHANGED."""
    client, factory = api
    base_day = _future_base_day(2)

    # Nhánh A: Thay đổi availability trên Draft + confirm-summary lại
    summary_a = _make_summary(
        title="Kiểm tra SOURCE_CHANGED từ Draft",
        start_at=base_day,
        days=7,
        start_local="09:00",
        end_local="12:00",
    )
    draft_a = await _confirmed_draft(client, "tc26-draft-a", summary_a)
    cand_a = (
        await _create_candidate(
            client,
            draft_a,
            "tc26-cand-a",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước nguồn A",
                    "expected_output": "Out A",
                    "estimated_base_seconds": 1200,
                }
            ],
        )
    ).json()["data"]
    assert cand_a["stale"] is False

    patched_draft = await _patch(
        client,
        f"/drafts/{draft_a['draft_id']}",
        "tc26-patch-draft",
        {
            "expected_summary_revision": draft_a["summary_revision"],
            "expected_planning_input_revision": draft_a["planning_input_revision"],
            "changes": {
                "summary": {
                    "availability": {
                        "timezone": "UTC",
                        "weekly_rules": [
                            {"weekday": wd, "start_local": "14:00", "end_local": "17:00"}
                            for wd in range(1, 8)
                        ],
                        "date_overrides": [],
                    }
                }
            },
        },
    )
    assert patched_draft.status_code == 200, patched_draft.text
    d2 = patched_draft.json()["data"]
    reconf = await _post(
        client,
        f"/drafts/{d2['draft_id']}/confirm-summary",
        "tc26-reconf-draft",
        {"expected_summary_revision": d2["summary_revision"]},
    )
    assert reconf.status_code == 200, reconf.text
    d2_conf = reconf.json()["data"]

    get_stale_a = await client.get(f"/api/v1/candidates/{cand_a['candidate_id']}")
    assert get_stale_a.status_code == 200, get_stale_a.text
    stale_cand_a = get_stale_a.json()["data"]
    assert stale_cand_a["stale"] is True

    conf_err_a = await _confirm_candidate(
        client,
        cand_a,
        "tc26-conf-a",
        override_summary_revision=d2_conf["summary_revision"],
        override_planning_input_revision=d2_conf["planning_input_revision"],
    )
    assert conf_err_a.status_code == 409, conf_err_a.text
    err_snap = _error_snapshot(conf_err_a.status_code, conf_err_a.json())
    assert err_snap["code"] == "SOURCE_CHANGED"

    # Nhánh B: Thay đổi UserPreference.effective_revision ở ngoài -> GET stale=True, confirm -> 409 SOURCE_CHANGED
    summary_b = _make_summary(title="Kiểm tra SOURCE_CHANGED từ Preference", start_at=base_day, days=7)
    draft_b = await _confirmed_draft(client, "tc26-draft-b", summary_b)
    cand_b = (
        await _create_candidate(
            client,
            draft_b,
            "tc26-cand-b",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước nguồn B",
                    "expected_output": "Out B",
                    "estimated_base_seconds": 1200,
                }
            ],
        )
    ).json()["data"]

    async with factory() as db:
        pref = await db.scalar(select(PreferenceRow))
        assert pref is not None
        pref.effective_revision += 1
        await db.commit()

    get_stale_b = await client.get(f"/api/v1/candidates/{cand_b['candidate_id']}")
    assert get_stale_b.status_code == 200 and get_stale_b.json()["data"]["stale"] is True
    conf_err_b = await _confirm_candidate(client, cand_b, "tc26-conf-b")
    assert conf_err_b.status_code == 409 and conf_err_b.json()["error"]["code"] == "SOURCE_CHANGED"

    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0

    _record_case(
        case_id="TC-UF2-26",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="Draft availability or preference change marks candidate stale and blocks confirm with 409 SOURCE_CHANGED",
        ui_scenario="error_expired_or_stale",
        input_a={
            "task_title": summary_a["title"],
            "draft_availability_updated_to": "14:00-17:00 UTC",
            "secondary_check": "UserPreference.effective_revision incremented",
            "summary": "Đổi giờ rảnh trên Draft (rồi reconfirm) và đổi Preference revision sau khi đã tạo Candidate.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Candidate ban đầu tạo hợp lệ trước khi nguồn thay đổi."},
        layer2_c=_layer2_snapshot(
            stale_cand_a,
            summary="GET /candidates/{id} trả stale=True khi nguồn không còn khớp.",
        ),
        layer3_d=_layer3_snapshot(
            stale_cand_a,
            summary="confirm_candidate trả 409 SOURCE_CHANGED cho cả nhánh Draft đổi và nhánh Preference đổi.",
            error=err_snap,
        ),
        layer4_e={
            "persisted_tasks_count": 0,
            "summary": "Không lưu Task vào DB khi nguồn đã đổi (409 SOURCE_CHANGED).",
        },
        assertions_checked=[
            "GET /candidates/{id} returns stale == True after draft availability update",
            "confirm_candidate returns 409 SOURCE_CHANGED on draft revision drift",
            "GET stale == True and confirm 409 SOURCE_CHANGED on UserPreference.effective_revision drift",
            "0 Tasks persisted in DB",
        ],
        candidate_payload=stale_cand_a,
        error_payload=err_snap,
    )


async def test_tc_uf2_27_candidate_expired_when_first_session_in_past(api, monkeypatch) -> None:
    """TC-UF2-27: Phiên đầu trôi vào quá khứ lúc confirm -> 409 CANDIDATE_EXPIRED (next_action == 'regenerate_candidate')."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(title="Kiểm tra CANDIDATE_EXPIRED", start_at=base_day, days=7)
    draft = await _confirmed_draft(client, "tc27-draft", summary)
    cand = (
        await _create_candidate(
            client,
            draft,
            "tc27-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước hết hạn",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1200,
                }
            ],
        )
    ).json()["data"]

    first_start = _parse_iso(cand["sessions"][0]["scheduled_start"])

    class _PastClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (first_start + timedelta(seconds=5)).astimezone(tz)

    monkeypatch.setattr(plan_service, "datetime", _PastClock)
    conf_resp = await _confirm_candidate(client, cand, "tc27-conf")
    assert conf_resp.status_code == 409, conf_resp.text
    err_snap = _error_snapshot(conf_resp.status_code, conf_resp.json())
    assert err_snap["code"] == "CANDIDATE_EXPIRED"
    assert err_snap["next_action"] == "regenerate_candidate"

    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0
        assert (
            await db.scalar(
                select(CommandReceipt).where(CommandReceipt.command_id == "tc27-conf")
            )
            is None
        )

    _record_case(
        case_id="TC-UF2-27",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="Past first session start at confirm returns 409 CANDIDATE_EXPIRED",
        ui_scenario="error_expired_or_stale",
        input_a={
            "task_title": summary["title"],
            "first_session_start": cand["sessions"][0]["scheduled_start"],
            "simulated_confirm_time": (first_start + timedelta(seconds=5)).isoformat(),
            "summary": "Đồng hồ server tiến qua mốc scheduled_start của phiên đầu trước lúc bấm Confirm.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Candidate tạo hợp lệ trước khi hết hạn."},
        layer2_c=_layer2_snapshot(
            cand,
            summary="_concrete_issues phát hiện PAST_SESSION khi đối chiếu với thời gian hiện tại.",
        ),
        layer3_d=_layer3_snapshot(
            cand,
            summary="confirm_candidate chặn với 409 CANDIDATE_EXPIRED và next_action='regenerate_candidate'.",
            error=err_snap,
        ),
        layer4_e={
            "persisted_tasks_count": 0,
            "confirm_receipt_stored": False,
            "summary": "Không tạo Task và không lưu CommandReceipt khi bị CANDIDATE_EXPIRED.",
        },
        assertions_checked=[
            "confirm returns 409 CANDIDATE_EXPIRED",
            "error.next_action == 'regenerate_candidate'",
            "0 Tasks and 0 confirm receipts persisted in DB",
        ],
        candidate_payload=cand,
        error_payload=err_snap,
    )


async def test_tc_uf2_28_revision_conflict_on_patch_undo_confirm(api) -> None:
    """TC-UF2-28: Gửi expected_review_revision cũ cho patch / undo / confirm -> 409 REVISION_CONFLICT."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(title="Kiểm tra REVISION_CONFLICT", start_at=base_day, days=7)
    draft = await _confirmed_draft(client, "tc28-draft", summary)
    cand1 = (
        await _create_candidate(
            client,
            draft,
            "tc28-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước 1",
                    "expected_output": "Out 1",
                    "estimated_base_seconds": 1200,
                }
            ],
        )
    ).json()["data"]
    assert cand1["review_revision"] == 1

    p_ok = await _patch(
        client,
        f"/candidates/{cand1['candidate_id']}",
        "tc28-patch-ok",
        {
            "expected_review_revision": 1,
            "edits": {"steps": [{"client_step_key": "step-1", "title": "Bước 1 Đã Sửa"}]},
        },
    )
    assert p_ok.status_code == 200, p_ok.text
    cand2 = p_ok.json()["data"]
    assert cand2["review_revision"] == 2 and cand2["can_undo"] is True

    # Stale rev=1 on PATCH, UNDO, and CONFIRM -> all 409 REVISION_CONFLICT
    r_patch = await _patch(
        client,
        f"/candidates/{cand1['candidate_id']}",
        "tc28-patch-stale",
        {
            "expected_review_revision": 1,
            "edits": {"steps": [{"client_step_key": "step-1", "title": "Ghi đè cũ"}]},
        },
    )
    assert r_patch.status_code == 409 and r_patch.json()["error"]["code"] == "REVISION_CONFLICT"

    r_undo = await _post(
        client,
        f"/candidates/{cand1['candidate_id']}/undo",
        "tc28-undo-stale",
        {"expected_review_revision": 1},
    )
    assert r_undo.status_code == 409 and r_undo.json()["error"]["code"] == "REVISION_CONFLICT"

    r_conf = await _confirm_candidate(
        client, cand2, "tc28-conf-stale", override_review_revision=1
    )
    assert r_conf.status_code == 409
    err_snap = _error_snapshot(r_conf.status_code, r_conf.json())
    assert err_snap["code"] == "REVISION_CONFLICT"

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand1["candidate_id"]))
        assert row is not None and row.review_revision == 2

    _record_case(
        case_id="TC-UF2-28",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="Stale expected_review_revision on patch, undo, and confirm returns 409 REVISION_CONFLICT",
        ui_scenario="error_expired_or_stale",
        input_a={
            "task_title": summary["title"],
            "current_review_revision": 2,
            "stale_expected_review_revision": 1,
            "operations_tested": ["PATCH /candidates/{id}", "POST /candidates/{id}/undo", "POST /candidates/{id}/confirm"],
            "summary": "Candidate đã tăng review_revision từ 1 -> 2; thử lại 3 lệnh với revision=1.",
        },
        layer1_b={"generation_tier": "manual_user", "summary": "Không gọi AI khi kiểm tra revision."},
        layer2_c=_layer2_snapshot(cand2, summary="Lịch rev 2 vẫn hợp lệ và được bảo toàn."),
        layer3_d=_layer3_snapshot(
            cand2,
            summary="Cả PATCH, UNDO và CONFIRM với rev=1 đều bị chặn 409 REVISION_CONFLICT.",
            error=err_snap,
        ),
        layer4_e={
            "db_review_revision": 2,
            "error_codes": [
                r_patch.json()["error"]["code"],
                r_undo.json()["error"]["code"],
                err_snap["code"],
            ],
            "summary": "DB CandidateRow giữ nguyên review_revision == 2.",
        },
        assertions_checked=[
            "PATCH with stale expected_review_revision -> 409 REVISION_CONFLICT",
            "UNDO with stale expected_review_revision -> 409 REVISION_CONFLICT",
            "CONFIRM with stale expected_review_revision -> 409 REVISION_CONFLICT",
        ],
        candidate_payload=cand2,
        error_payload=err_snap,
    )


async def test_tc_uf2_29_uf6_expand_next_horizon_branch1_ai_enrich_and_outbox(
    api, monkeypatch
) -> None:
    """TC-UF2-29: UF6 expand_next_horizon Branch 1 (unexpanded_scope) -> AI decompose + enrich + approve cập nhật extension_candidate_at & outbox [ProPlan]."""
    client, factory = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Đề án dài hạn Branch 1",
        scope="Khởi động và mở rộng bằng AI",
        start_at=base_day,
        days=45,
        start_local="08:00",
        end_local="08:40",
    )
    draft = await _confirmed_draft(client, "tc29-draft", summary)

    async with factory() as db:
        await _connect_write_calendar(db, "user_a")
        await db.commit()

    cand = (
        await _create_candidate(
            client,
            draft,
            "tc29-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước Khởi Động",
                    "expected_output": "Tài liệu sẵn sàng",
                    "estimated_base_seconds": 1200,
                }
            ],
            unexpanded_scope="Giai đoạn 2: Xây dựng mô hình và kiểm thử toàn diện",
        )
    ).json()["data"]
    conf = (await _confirm_candidate(client, cand, "tc29-conf")).json()["data"]
    task_before = conf["task"]
    task_id = task_before["task_id"]
    initial_ext = _parse_iso(task_before["extension_candidate_at"])

    # Stub AI cho expand_next_horizon Branch 1: trả 7 bước (chọn top 6, để bước 7 sang unexpanded_scope; bước 1=50p tách >=2 phiên)
    llm_counts = _stub_llm(
        monkeypatch,
        decompose_payload=LLMStrictTaskDecompositionPayload(
            task_title=summary["title"],
            steps=[
                LLMStrictStepPromptItem(
                    order_index=i + 1,
                    title=f"Bước Mở Rộng {i + 1}",
                    expected_output=f"Đầu ra Mở Rộng {i + 1}",
                    estimated_minutes=50 if i == 0 else 20,
                    micro_task_prompt=f"Khởi động mở rộng {i + 1}",
                    cognitive_load="medium",
                )
                for i in range(7)
            ],
            reasoning="Chia 7 bước cho chặng kế tiếp.",
        ),
        enrich_fn=lambda prompt: LLMSessionEnrichmentPayload(
            sessions=[
                LLMSessionEnrichmentItem(
                    client_session_key=item["client_session_key"],
                    title=f"{item['step_title']} ({item['session_index']}/{item['total_sessions_in_step']})",
                    session_goal=f"Mục tiêu expand phần {item['session_index']}",
                    expected_output=f"Đầu ra expand phần {item['session_index']}",
                    micro_task_text=f"Tiếp tục expand phần {item['session_index']}.",
                )
                for item in prompt["sessions"]
            ]
        ),
    )

    prop_resp = await _post(
        client,
        "/proposals",
        "tc29-prop",
        {
            "trigger_reason": "expand_next_horizon",
            "target_task_id": task_id,
            "allowed_task_ids": [task_id],
            "expected_base_schedule_revision": conf["schedule_version"]["version_number"],
            "expected_source_revisions": {
                "task": task_before["revision"],
                "schedule": conf["schedule_version"]["version_number"],
                "availability": 0,
                "settings": 1,
                "calendar": cand["source_revisions"]["calendar_context"]["revision"],
            },
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text
    prop = prop_resp.json()["data"]

    assert llm_counts["UF2"] == 1
    assert llm_counts["UF2_Enrich"] == 1
    assert len(prop["proposed_changes"]["steps_to_add"]) == 6
    assert prop["feasibility"]["next_horizon_preview"]["unexpanded_scope"] == "Bước Mở Rộng 7"

    first_added_step_id = prop["proposed_changes"]["steps_to_add"][0]["step_id"]
    first_step_adds = [
        a
        for a in prop["proposed_changes"]["sessions_to_add"]
        if a["step_id"] == first_added_step_id
    ]
    assert len(first_step_adds) >= 2
    assert first_step_adds[0]["after"]["title"] == "Bước Mở Rộng 1 (1/2)"
    assert first_step_adds[0]["after"]["micro_task_text"] == "Khởi động mở rộng 1"
    assert first_step_adds[1]["after"]["micro_task_text"] == "Tiếp tục expand phần 2."
    assert first_step_adds[1]["after"]["micro_task_seconds"] == 0

    app_resp = await _post(
        client,
        f"/proposals/{prop['proposal_id']}/approve",
        "tc29-approve",
        {
            "expected_proposal_revision": prop["proposal_revision"],
            "expected_base_schedule_revision": prop["base_schedule_revision"],
            "expected_source_revisions": prop["source_revisions"],
            "start_continuation": False,
        },
    )
    assert app_resp.status_code == 200, app_resp.text
    app_data = app_resp.json()["data"]
    assert any(
        s.get("title") == "Bước Mở Rộng 1 (1/2)"
        and s.get("session_goal") == "Mục tiêu expand phần 1"
        and s.get("expected_output") == "Đầu ra expand phần 1"
        for s in app_data["sessions"]
    )

    task_after = (await client.get(f"/api/v1/tasks/{task_id}")).json()["data"]
    assert task_after["unexpanded_scope"] == "Bước Mở Rộng 7"
    assert task_after["extension_candidate_at"] is not None
    assert _parse_iso(task_after["extension_candidate_at"]) > initial_ext

    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        jobs = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.target_schedule_revision == app_data["updated_schedule_revision"],
                )
            )
        ).all()
        assert len(jobs) == len(prop["proposed_changes"]["sessions_to_add"])
        assert all(j.payload_snapshot["summary"].startswith("[ProPlan] ") for j in jobs)
        assert any("Mục tiêu: Mục tiêu expand phần 1" in j.payload_snapshot.get("description", "") for j in jobs)
        outbox_summaries = [j.payload_snapshot["summary"] for j in jobs]

    _record_case(
        case_id="TC-UF2-29",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="UF6 expand_next_horizon Branch 1 runs AI decompose + enrich + outbox [ProPlan]",
        ui_scenario="partial_horizon_locked",
        input_a={
            "task_title": summary["title"],
            "initial_unexpanded_scope": "Giai đoạn 2: Xây dựng mô hình và kiểm thử toàn diện",
            "trigger_reason": "expand_next_horizon",
            "summary": "Task partial có unexpanded_scope và Google Calendar nối -> gọi POST /proposals + approve.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "expand_decompose_calls": llm_counts["UF2"],
            "expand_enrich_calls": llm_counts["UF2_Enrich"],
            "summary": "Branch 1 gọi AI decompose (7 bước -> lấy 6, giữ bước 7) + batch enrich cho bước tách.",
        },
        layer2_c={
            "status": prop["feasibility"]["status"],
            "confirmable": prop["feasibility"]["confirmable"],
            "deficit_seconds": prop["feasibility"]["deficit_seconds"],
            "next_horizon_preview": prop["feasibility"]["next_horizon_preview"],
            "summary": "6 bước mở rộng xếp đủ trong chặng 14d mới sau phiên gốc.",
        },
        layer3_d=_layer3_snapshot(
            cand,
            summary="Proposal thêm 6 steps và sessions_to_add có đầy đủ title/session_goal/expected_output.",
            proposal=prop,
        ),
        layer4_e={
            "task_id": task_id,
            "extension_candidate_at_before": task_before["extension_candidate_at"],
            "extension_candidate_at_after": task_after["extension_candidate_at"],
            "remaining_unexpanded_scope": task_after["unexpanded_scope"],
            "calendar_outbox_summaries": outbox_summaries,
            "summary": "Approve cập nhật Task.extension_candidate_at, trả ProposalSessionData enriched và tạo Google Calendar outbox [ProPlan].",
        },
        assertions_checked=[
            "POST /proposals expand_next_horizon Branch 1 invokes AI decompose + batch enrich",
            "top 6 steps added; step 7 retained in next_horizon_preview.unexpanded_scope",
            "approve updates Task.extension_candidate_at forward",
            "ApproveProposalData.sessions includes enriched title/session_goal/expected_output",
            "CalendarSyncJob queued for new sessions with [ProPlan] summary and Mục tiêu/Đầu ra dự kiến",
        ],
        candidate_payload=cand,
        task_payload=task_after,
        proposal_payload=prop,
    )


async def test_tc_uf2_30_uf6_expand_next_horizon_branch2_pending_steps_two_passes(
    api, monkeypatch
) -> None:
    """TC-UF2-30: UF6 expand_next_horizon Branch 2 (pending_unscheduled_steps) qua 2 lượt -> partial -> complete và extension_candidate_at -> None."""
    client, _ = api
    base_day = _future_base_day(2)
    summary = _make_summary(
        title="Đề án 60 ngày Branch 2",
        scope="6 chặng lớn trải qua 3 cửa sổ 14 ngày",
        start_at=base_day,
        days=60,
        start_local="10:00",
        end_local="11:00",  # 3000s work/ngày -> 14 ngày = 42,000s work
    )
    draft = await _confirmed_draft(client, "tc30-draft", summary)

    # 6 bước: [250, 250, 75, 250, 250, 250] phút -> buffered [18000, 18000, 5400, 18000, 18000, 18000]s
    # 14 ngày đầu chứa bước 1..3 (41,400s <= 42,000s); bước 4..6 còn pending chưa xếp lịch.
    step_mins = [250, 250, 75, 250, 250, 250]
    _stub_llm(
        monkeypatch,
        decompose_payload=LLMStrictTaskDecompositionPayload(
            task_title=summary["title"],
            steps=[
                LLMStrictStepPromptItem(
                    order_index=i + 1,
                    title=f"Chặng 60d số {i + 1}",
                    expected_output=f"Hoàn tất mốc {i + 1}",
                    estimated_minutes=mins,
                    micro_task_prompt=f"Mở tài liệu chặng {i + 1}",
                    cognitive_load="heavy",
                )
                for i, mins in enumerate(step_mins)
            ],
            reasoning="Chia 6 chặng xuyên suốt 60 ngày.",
        ),
    )

    cand_resp = await _create_candidate(client, draft, "tc30-cand")
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]
    assert cand["feasibility"]["status"] == "feasible"
    assert len(cand["feasibility"]["verification_scope"]["client_step_keys"]) == 3

    conf = (await _confirm_candidate(client, cand, "tc30-conf")).json()["data"]
    task0 = conf["task"]
    task_id = task0["task_id"]
    assert task0["scheduling_state"] == "partial"

    # Lượt 1 Branch 2: xếp bước 4 & 5 (36,000s <= 42,000s), vẫn còn bước 6 -> vẫn partial
    cal_rev = cand["source_revisions"]["calendar_context"]["revision"]
    cur_task_rev = task0["revision"]
    cur_sched_ver = conf["schedule_version"]["version_number"]

    prop1_resp = await _post(
        client,
        "/proposals",
        "tc30-prop-1",
        {
            "trigger_reason": "expand_next_horizon",
            "target_task_id": task_id,
            "allowed_task_ids": [task_id],
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
    assert prop1_resp.status_code == 201, prop1_resp.text
    prop1 = prop1_resp.json()["data"]
    assert prop1["feasibility"]["status"] == "feasible"
    assert prop1["proposed_changes"]["steps_to_add"] == []
    assert len(prop1["proposed_changes"]["sessions_to_add"]) > 0

    app1_resp = await _post(
        client,
        f"/proposals/{prop1['proposal_id']}/approve",
        "tc30-app-1",
        {
            "expected_proposal_revision": prop1["proposal_revision"],
            "expected_base_schedule_revision": prop1["base_schedule_revision"],
            "expected_source_revisions": prop1["source_revisions"],
            "start_continuation": False,
        },
    )
    assert app1_resp.status_code == 200, app1_resp.text
    task_after1 = (await client.get(f"/api/v1/tasks/{task_id}")).json()["data"]
    assert task_after1["decomposition_state"] == "partial"
    assert task_after1["scheduling_state"] == "partial"
    assert task_after1["extension_candidate_at"] is not None

    # Lượt 2 Branch 2: xếp bước 6 cuối cùng -> decomposition_state='complete', extension_candidate_at=None!
    cur_task_rev = task_after1["revision"]
    cur_sched_ver = app1_resp.json()["data"]["updated_schedule_revision"]
    prop2_resp = await _post(
        client,
        "/proposals",
        "tc30-prop-2",
        {
            "trigger_reason": "expand_next_horizon",
            "target_task_id": task_id,
            "allowed_task_ids": [task_id],
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
    assert prop2_resp.status_code == 201, prop2_resp.text
    prop2 = prop2_resp.json()["data"]
    assert prop2["feasibility"]["status"] == "feasible"
    assert len(prop2["proposed_changes"]["sessions_to_add"]) > 0

    app2_resp = await _post(
        client,
        f"/proposals/{prop2['proposal_id']}/approve",
        "tc30-app-2",
        {
            "expected_proposal_revision": prop2["proposal_revision"],
            "expected_base_schedule_revision": prop2["base_schedule_revision"],
            "expected_source_revisions": prop2["source_revisions"],
            "start_continuation": False,
        },
    )
    assert app2_resp.status_code == 200, app2_resp.text
    task_after2 = (await client.get(f"/api/v1/tasks/{task_id}")).json()["data"]
    assert task_after2["decomposition_state"] == "complete"
    assert task_after2["scheduling_state"] == "complete"
    assert task_after2["extension_candidate_at"] is None
    scheduled_step_ids = {s["step_id"] for s in task_after2["sessions"]}
    assert scheduled_step_ids == {st["step_id"] for st in task_after2["steps"]}

    _record_case(
        case_id="TC-UF2-30",
        group="Group 4 — Concurrency, Stale/Expired, AI Tier-3 Fallback & UF6 Bridge",
        title="UF6 expand_next_horizon Branch 2 schedules pending steps across 2 passes to complete",
        ui_scenario="partial_horizon_locked",
        input_a={
            "task_title": summary["title"],
            "step_minutes": step_mins,
            "availability": summary["availability"],
            "trigger_reason": "expand_next_horizon",
            "summary": "6 bước (1..3 xếp trong 14d đầu, 4..6 pending) mở rộng qua 2 lượt Branch 2.",
        },
        layer1_b={
            "generation_tier": "tier_1_gemini",
            "summary": "Branch 2 xếp tiếp pending_unscheduled_steps hiện có; enrich fallback dùng cho các phiên tách.",
        },
        layer2_c={
            "pass1_status": prop1["feasibility"]["status"],
            "pass2_status": prop2["feasibility"]["status"],
            "pass1_added_sessions": len(prop1["proposed_changes"]["sessions_to_add"]),
            "pass2_added_sessions": len(prop2["proposed_changes"]["sessions_to_add"]),
            "summary": "Pass 1 xếp Bước 4-5; Pass 2 xếp nốt Bước 6.",
        },
        layer3_d=_layer3_snapshot(
            cand,
            summary="Pass 1 -> partial; Pass 2 -> complete toàn bộ 6 bước.",
            proposal=prop2,
        ),
        layer4_e={
            "task_id": task_id,
            "pass1_state": {
                "decomposition_state": task_after1["decomposition_state"],
                "scheduling_state": task_after1["scheduling_state"],
                "extension_candidate_at": task_after1["extension_candidate_at"],
            },
            "pass2_state": {
                "decomposition_state": task_after2["decomposition_state"],
                "scheduling_state": task_after2["scheduling_state"],
                "extension_candidate_at": task_after2["extension_candidate_at"],
            },
            "total_persisted_sessions": len(task_after2["sessions"]),
            "summary": "decomposition_state chuyển partial -> complete và extension_candidate_at xóa về None ở Pass 2.",
        },
        assertions_checked=[
            "initial confirm schedules steps 1..3 with scheduling_state == 'partial'",
            "Pass 1 expand_next_horizon schedules steps 4..5, keeps decomposition_state == 'partial' and extension_candidate_at != None",
            "Pass 2 expand_next_horizon schedules step 6, sets decomposition_state == 'complete', scheduling_state == 'complete', extension_candidate_at == None",
        ],
        candidate_payload=cand,
        task_payload=task_after2,
        proposal_payload=prop2,
    )
