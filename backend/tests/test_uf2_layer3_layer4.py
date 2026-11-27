"""Kiểm thử Layer 3 & Layer 4 cho UF2: Horizon 14-21d, Session Enrichment, Structural Ops, NL Adjust, 1-Step Undo, Extension At, Calendar Outbox & AI Expand Horizon."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect, select, text

from app.api.deps import get_db
from app.core.config import settings
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import (
    Availability as AvailabilityRow,
)
from app.models import (
    Base,
    CalendarConnection,
    CalendarSyncJob,
    PlanningDraft,
    Step,
    Task,
)
from app.models import (
    PlanCandidate as CandidateRow,
)
from app.models import (
    Session as SessionRow,
)
from app.schemas.common import SessionSegment, SourceRevisions, TimeInterval
from app.schemas.planning import (
    CandidateSession,
    CandidateStep,
    CandidateVerificationScope,
    FeasibilityReport,
    PlanCandidate,
)
from app.schemas.reschedule import (
    ApproveProposalRequest,
    CreateProposalRequest,
    ProposalSourceRevisions,
)
from app.schemas.task import (
    CandidateAdjustmentResult,
    CandidateStepAdjustment,
    LLMSessionEnrichmentItem,
    NLCandidateStepAdd,
    NLCandidateStepMerge,
    NLCandidateStepSplit,
    SessionEnrichmentResult,
    StepPromptItem,
    TaskDecompositionResult,
)
from app.services import draft_service, plan_service, reschedule_service
from app.services.ai_service import ai_service
from app.services.credential_crypto import encrypt_credentials
from app.services.draft_service import resolve_user_uuid
from app.services.plan_service import build_calendar_event_text


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
        raise ConnectionError("Providers unavailable in Layer 3/4 test")

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
    days: int = 10,
    start_local: str = "09:00",
    end_local: str = "11:00",
    tz: str = "UTC",
    start_offset_hours: int = 2,
) -> dict:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = now + timedelta(hours=start_offset_hours)
    return {
        "title": "Đề án tốt nghiệp",
        "description": "Hoàn thành tài liệu và mã nguồn",
        "scope": "Phạm vi đầy đủ 4 chương",
        "time_constraint": {
            "kind": "deadline",
            "start_at": start_at.isoformat(),
            "deadline_at": (start_at + timedelta(days=days)).isoformat(),
            "input_timezone": tz,
        },
        "availability": {
            "timezone": tz,
            "weekly_rules": [
                {"weekday": wd, "start_local": start_local, "end_local": end_local}
                for wd in range(1, 8)
            ],
        },
    }


async def _post(client: AsyncClient, path: str, key: str, payload: dict):
    return await client.post("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _patch(client: AsyncClient, path: str, key: str, payload: dict):
    return await client.patch("/api/v1" + path, headers={"Idempotency-Key": key}, json=payload)


async def _confirmed_draft(client: AsyncClient, key: str, summary: dict | None = None) -> dict:
    resp = await _post(
        client,
        "/drafts",
        f"{key}-create",
        {
            "client_draft_id": f"{key}-client",
            "input_source": "text",
            "summary": summary or _base_summary(),
        },
    )
    assert resp.status_code == 201, resp.text
    draft = resp.json()["data"]
    conf = await _post(
        client,
        f"/drafts/{draft['draft_id']}/confirm-summary",
        f"{key}-confirm",
        {"expected_summary_revision": draft["summary_revision"]},
    )
    assert conf.status_code == 200, conf.text
    return conf.json()["data"]


async def _create_candidate(
    client: AsyncClient,
    draft: dict,
    key: str,
    *,
    manual_steps: list[dict] | None = None,
    unexpanded_scope: str | None = None,
    planning_horizon: dict | None = None,
):
    body: dict = {
        "source_summary_revision": draft["summary_revision"],
        "source_planning_input_revision": draft["planning_input_revision"],
    }
    if manual_steps is not None:
        body["manual_steps"] = manual_steps
    if unexpanded_scope is not None:
        body["unexpanded_scope"] = unexpanded_scope
    if planning_horizon is not None:
        body["planning_horizon"] = planning_horizon
    return await _post(client, f"/drafts/{draft['draft_id']}/candidates", key, body)


async def _confirm_candidate(client: AsyncClient, plan: dict, key: str):
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


async def _connect_calendar(db, user_id: str) -> CalendarConnection:
    owner = resolve_user_uuid(user_id)
    conn_id = UUID("77777777-7777-4777-8777-777777777777")
    creds = encrypt_credentials(
        {
            "access_token": "acc_layer3_test",
            "refresh_token": "ref_layer3_test",
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
        account_ref="layer3_test@gmail.com",
        connection_state="connected",
        read_calendar_ids=[],
        write_calendar_id="primary",
        granted_scopes=["https://www.googleapis.com/auth/calendar"],
        credential_ref=creds,
    )
    db.add(conn)
    await db.flush()
    return conn


def test_alembic_uf2_upgrade_downgrade_roundtrip(sqlite_migration) -> None:
    """Alembic upgrade head -> downgrade -1 -> upgrade head cho 5 cột UF2 Layer 3/4 trên SQLite."""
    url, alembic = sqlite_migration
    sync_url = url.replace("+aiosqlite", "")

    alembic("upgrade", "head")
    eng = create_engine(sync_url)
    try:
        insp = inspect(eng)
        cand_cols = {c["name"] for c in insp.get_columns("plan_candidates")}
        task_cols = {c["name"] for c in insp.get_columns("tasks")}
        sess_cols = {c["name"] for c in insp.get_columns("sessions")}
        assert "previous_snapshot" in cand_cols
        assert "extension_candidate_at" in task_cols
        assert {"title", "session_goal", "expected_output"} <= sess_cols
    finally:
        eng.dispose()

    alembic("downgrade", "-1")
    eng = create_engine(sync_url)
    try:
        insp = inspect(eng)
        cand_cols = {c["name"] for c in insp.get_columns("plan_candidates")}
        task_cols = {c["name"] for c in insp.get_columns("tasks")}
        sess_cols = {c["name"] for c in insp.get_columns("sessions")}
        assert "previous_snapshot" not in cand_cols
        assert "extension_candidate_at" not in task_cols
        assert not ({"title", "session_goal", "expected_output"} & sess_cols)
    finally:
        eng.dispose()

    alembic("upgrade", "head")
    eng = create_engine(sync_url)
    try:
        insp = inspect(eng)
        cand_cols = {c["name"] for c in insp.get_columns("plan_candidates")}
        task_cols = {c["name"] for c in insp.get_columns("tasks")}
        sess_cols = {c["name"] for c in insp.get_columns("sessions")}
        assert "previous_snapshot" in cand_cols
        assert "extension_candidate_at" in task_cols
        assert {"title", "session_goal", "expected_output"} <= sess_cols
    finally:
        eng.dispose()


async def test_u2_be_15_horizon_14_default_and_21_max_clamp(api) -> None:
    """U2-BE-15: mặc định 14 ngày, hỗ trợ horizon yêu cầu tối đa 21 ngày và kẹp khi vượt 21 ngày."""
    client, _ = api
    draft = await _confirmed_draft(
        client,
        "h21-draft",
        summary=_base_summary(days=40, start_local="09:00", end_local="10:30"),
    )
    steps_payload = [
        {
            "order_index": i,
            "title": f"Chặng {i + 1}",
            "expected_output": f"Đầu ra {i + 1}",
            "estimated_base_seconds": 3600,
        }
        for i in range(6)
    ]

    # 1) Default horizon = DEFAULT_PLANNING_HORIZON_DAYS (14d)
    resp_default = await _create_candidate(
        client, draft, "h21-default", manual_steps=steps_payload
    )
    assert resp_default.status_code == 201, resp_default.text
    cand_def = resp_default.json()["data"]
    def_start = datetime.fromisoformat(cand_def["planning_horizon"]["start_at"].replace("Z", "+00:00"))
    def_end = datetime.fromisoformat(cand_def["planning_horizon"]["end_at"].replace("Z", "+00:00"))
    assert abs((def_end - def_start).total_seconds() - settings.DEFAULT_PLANNING_HORIZON_DAYS * 86400) <= 5

    # 2) Requested 21d -> preserved up to MAX_PLANNING_HORIZON_DAYS (21d)
    req_start = def_start + timedelta(minutes=1)
    req_end_21 = req_start + timedelta(days=21)
    resp_21 = await _create_candidate(
        client,
        draft,
        "h21-req21",
        manual_steps=steps_payload,
        planning_horizon={"start_at": req_start.isoformat(), "end_at": req_end_21.isoformat()},
    )
    assert resp_21.status_code == 201, resp_21.text
    cand_21 = resp_21.json()["data"]
    c21_start = datetime.fromisoformat(cand_21["planning_horizon"]["start_at"].replace("Z", "+00:00"))
    c21_end = datetime.fromisoformat(cand_21["planning_horizon"]["end_at"].replace("Z", "+00:00"))
    assert abs((c21_end - c21_start).total_seconds() - settings.MAX_PLANNING_HORIZON_DAYS * 86400) <= 5

    # 3) Requested 28d -> clamped to 21d
    resp_28 = await _create_candidate(
        client,
        draft,
        "h21-req28",
        manual_steps=steps_payload,
        planning_horizon={
            "start_at": req_start.isoformat(),
            "end_at": (req_start + timedelta(days=28)).isoformat(),
        },
    )
    assert resp_28.status_code == 201, resp_28.text
    cand_28 = resp_28.json()["data"]
    c28_start = datetime.fromisoformat(cand_28["planning_horizon"]["start_at"].replace("Z", "+00:00"))
    c28_end = datetime.fromisoformat(cand_28["planning_horizon"]["end_at"].replace("Z", "+00:00"))
    assert abs((c28_end - c28_start).total_seconds() - settings.MAX_PLANNING_HORIZON_DAYS * 86400) <= 5

    # 4) Requested range completely after deadline -> 422 VALIDATION_ERROR
    resp_bad = await _create_candidate(
        client,
        draft,
        "h21-bad",
        manual_steps=steps_payload,
        planning_horizon={
            "start_at": (req_start + timedelta(days=45)).isoformat(),
            "end_at": (req_start + timedelta(days=50)).isoformat(),
        },
    )
    assert resp_bad.status_code == 422, resp_bad.text
    assert resp_bad.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_u2_se_01_04_lock_free_enrichment_single_vs_split_and_source_changed(
    api, monkeypatch
) -> None:
    """U2-SE-01 & U2-SE-04: single-session không gọi LLM, split-sessions gọi 1 batch không giữ lock DB, DTOs & SOURCE_CHANGED."""
    client, factory = api

    # A) Single-session steps -> 0 calls to enrich_split_sessions
    draft_single = await _confirmed_draft(
        client, "enr-single-draft", summary=_base_summary(days=7, start_local="09:00", end_local="12:00")
    )
    split_spy = AsyncMock()
    monkeypatch.setattr(ai_service, "enrich_split_sessions", split_spy)
    resp_single = await _create_candidate(
        client,
        draft_single,
        "enr-single-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Lập dàn ý",
                "expected_output": "Dàn ý 5 gạch đầu dòng",
                "estimated_base_seconds": 1200,
            }
        ],
    )
    assert resp_single.status_code == 201, resp_single.text
    assert split_spy.call_count == 0
    s_sess = resp_single.json()["data"]["sessions"][0]
    assert s_sess["title"] == "Lập dàn ý"
    assert "Lập dàn ý" in s_sess["session_goal"]
    assert s_sess["expected_output"] == "Dàn ý 5 gạch đầu dòng"

    # B) Multi-session steps + direct DB session call to verify ZERO db.in_transaction() during AI calls
    # Khung 40 phút/ngày (09:00-09:40) ép các bước >=50p phải tách >=2 phiên sang nhiều ngày
    draft_multi = await _confirmed_draft(
        client, "enr-multi-draft", summary=_base_summary(days=20, start_local="09:00", end_local="09:40")
    )
    tx_states_during_ai: list[tuple[str, bool]] = []
    batch_calls: list[list[str]] = []

    async with factory() as db:
        async def _mock_decompose(**kwargs):
            tx_states_during_ai.append(("decompose", db.in_transaction()))
            return TaskDecompositionResult(
                task_title="Báo cáo lớn",
                steps=[
                    StepPromptItem(
                        order_index=1,
                        title="Đọc nhanh đề bài",
                        expected_output="Tóm tắt yêu cầu đề bài",
                        estimated_minutes=20,
                        micro_task_prompt="Mở đề bài PDF.",
                    ),
                    StepPromptItem(
                        order_index=2,
                        title="Phân tích dữ liệu",
                        expected_output="Bảng phân tích 3 chỉ số",
                        estimated_minutes=50,
                        micro_task_prompt="Mở file Excel dữ liệu thô.",
                    ),
                    StepPromptItem(
                        order_index=3,
                        title="Viết báo cáo chi tiết",
                        expected_output="Bản nháp 5 trang",
                        estimated_minutes=50,
                        micro_task_prompt="Mở file Word mẫu báo cáo.",
                    ),
                ],
                reasoning="3 bước rõ ràng.",
                generation_tier="tier_1_gemini",
            )

        async def _mock_enrich(split_sessions, *, task_title=None):
            tx_states_during_ai.append(("enrich", db.in_transaction()))
            batch_calls.append([item.client_session_key for item in split_sessions])
            return SessionEnrichmentResult(
                sessions=[
                    LLMSessionEnrichmentItem(
                        client_session_key=item.client_session_key,
                        title=f"{item.step_title} (Phần {item.session_index}/{item.total_sessions_in_step})",
                        session_goal=f"Mục tiêu phần {item.session_index} của {item.step_title}",
                        expected_output=f"Đầu ra phần {item.session_index}: {item.step_expected_output}",
                        micro_task_text=f"Micro AI phần {item.session_index} ({item.client_step_key})",
                    )
                    for item in split_sessions
                ],
                generation_tier="tier_1_gemini",
            )

        monkeypatch.setattr(ai_service, "decompose_task", _mock_decompose)
        monkeypatch.setattr(ai_service, "enrich_split_sessions", _mock_enrich)

        from app.schemas.planning import GenerateCandidateRequest

        cand = await plan_service.create_candidate(
            user_id="user_a",
            draft_id=draft_multi["draft_id"],
            idempotency_key="enr-multi-cand",
            payload=GenerateCandidateRequest(
                source_summary_revision=draft_multi["summary_revision"],
                source_planning_input_revision=draft_multi["planning_input_revision"],
            ),
            db=db,
        )

    # Zero transaction held during BOTH decompose_task and enrich_split_sessions
    assert tx_states_during_ai == [("decompose", False), ("enrich", False)]
    # Single batch call across all split steps (Step 2 and Step 3 each have >= 2 sessions)
    assert len(batch_calls) == 1
    assert len(batch_calls[0]) >= 4

    step1_sess = [s for s in cand.sessions if s.client_step_key == "step-1"]
    step2_sess = sorted(
        [s for s in cand.sessions if s.client_step_key == "step-2"],
        key=lambda s: s.scheduled_start,
    )
    assert len(step1_sess) == 1
    assert step1_sess[0].title == "Đọc nhanh đề bài"
    assert step1_sess[0].micro_task_text == "Mở đề bài PDF."

    assert len(step2_sess) >= 2
    assert step2_sess[0].title == "Phân tích dữ liệu (Phần 1/2)"
    assert step2_sess[0].session_goal == "Mục tiêu phần 1 của Phân tích dữ liệu"
    assert step2_sess[0].micro_task_text == "Mở file Excel dữ liệu thô."  # Part 1 preserved!
    assert step2_sess[1].title == "Phân tích dữ liệu (Phần 2/2)"
    assert step2_sess[1].micro_task_text == "Micro AI phần 2 (step-2)"  # Part 2+ enriched!

    # Confirm and verify Session enrichment fields propagate to Task, Tasks list, and Schedule DTOs
    conf_resp = await _confirm_candidate(
        client, json.loads(cand.model_dump_json()), "enr-multi-confirm"
    )
    assert conf_resp.status_code == 201, conf_resp.text
    conf_task = conf_resp.json()["data"]["task"]
    assert any(s["session_goal"] == "Mục tiêu phần 1 của Phân tích dữ liệu" for s in conf_task["sessions"])

    get_task = await client.get(f"/api/v1/tasks/{conf_task['task_id']}")
    assert get_task.status_code == 200
    assert any(s["title"] == "Phân tích dữ liệu (Phần 2/2)" for s in get_task.json()["data"]["sessions"])

    list_tasks = await client.get("/api/v1/tasks")
    assert list_tasks.status_code == 200
    found_task = next(t for t in list_tasks.json()["data"]["items"] if t["task_id"] == conf_task["task_id"])
    assert any(s["expected_output"] is not None for s in found_task["sessions"])

    now_utc = datetime.now(UTC)
    sched_resp = await client.get(
        "/api/v1/schedule",
        params={
            "from": now_utc.isoformat(),
            "to": (now_utc + timedelta(days=15)).isoformat(),
        },
    )
    assert sched_resp.status_code == 200
    assert any(
        item["session_goal"] == "Mục tiêu phần 1 của Phân tích dữ liệu"
        for item in sched_resp.json()["data"]["items"]
    )

    # C) SOURCE_CHANGED during lock-free AI step -> 409 SOURCE_CHANGED
    draft_race = await _confirmed_draft(
        client, "enr-race-draft", summary=_base_summary(days=7, start_local="09:00", end_local="12:00")
    )

    async def _race_decompose(**kwargs):
        async with factory() as other_db:
            pref_rows = (
                await other_db.scalars(
                    select(AvailabilityRow)
                )
            ).all()
            # Mutate UserPreference revision via raw update if exists, or create/bump preference revision
            from app.models.user import UserPreference as PrefRow

            pref = await other_db.scalar(select(PrefRow))
            if pref is not None:
                pref.effective_revision += 1
                await other_db.commit()
            elif pref_rows:
                pref_rows[0].revision += 1
                await other_db.commit()
        return TaskDecompositionResult(
            task_title="Race",
            steps=[
                StepPromptItem(
                    order_index=1,
                    title="Bước 1",
                    expected_output="Output 1",
                    estimated_minutes=25,
                    micro_task_prompt="Mở file 1.",
                )
            ],
            reasoning="Test race.",
            generation_tier="tier_1_gemini",
        )

    monkeypatch.setattr(ai_service, "decompose_task", _race_decompose)
    race_resp = await _create_candidate(client, draft_race, "enr-race-cand")
    assert race_resp.status_code == 409, race_resp.text
    assert race_resp.json()["error"]["code"] == "SOURCE_CHANGED"


async def test_u2_be_07_11_structural_step_ops_nl_micro_task_and_lock_free_patch(
    api, monkeypatch
) -> None:
    """U2-BE-07..11: add, delete, merge, split, conflict 422, micro_task_text edit, NL 5-array wiring & lock-free patch."""
    client, factory = api
    draft = await _confirmed_draft(
        client, "ops-draft", summary=_base_summary(days=25, start_local="08:00", end_local="08:40")
    )
    cand = (
        await _create_candidate(
            client,
            draft,
            "ops-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước A",
                    "expected_output": "Out A",
                    "estimated_base_seconds": 1500,
                },
                {
                    "order_index": 1,
                    "title": "Bước B",
                    "expected_output": "Out B",
                    "estimated_base_seconds": 1500,
                },
                {
                    "order_index": 2,
                    "title": "Bước C",
                    "expected_output": "Out C",
                    "estimated_base_seconds": 1500,
                },
            ],
        )
    ).json()["data"]

    # 1) Validation guards (422): conflict key, unknown key, delete all, >20 steps
    err_conflict = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-err-conflict",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "steps": [{"client_step_key": "step-1", "title": "Đổi tên"}],
                "delete_step_keys": ["step-1"],
            },
        },
    )
    assert err_conflict.status_code == 422

    err_unknown = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-err-unknown",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {"delete_step_keys": ["step-999"]},
        },
    )
    assert err_unknown.status_code == 422

    err_zero = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-err-zero",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {"delete_step_keys": ["step-1", "step-2", "step-3"]},
        },
    )
    assert err_zero.status_code == 422

    err_too_many = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-err-many",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "add_steps": [
                    {
                        "title": f"Thêm {i}",
                        "expected_output": f"Out {i}",
                        "estimated_base_seconds": 600,
                    }
                    for i in range(18)
                ]
            },
        },
    )
    assert err_too_many.status_code == 422

    # 2) Non-timing edit: micro_task_text on step + session title/goal/output
    first_sess_key = cand["sessions"][0]["client_session_key"]
    p_micro = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-micro",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "steps": [
                    {"client_step_key": "step-1", "micro_task_text": "Mở mục 1 và gạch 3 ý đầu"}
                ],
                "sessions": [
                    {
                        "client_session_key": first_sess_key,
                        "title": "Phiên mở màn tùy chỉnh",
                        "session_goal": "Chốt khung sườn A",
                        "expected_output": "Dàn ý A hoàn chỉnh",
                    }
                ],
            },
        },
    )
    assert p_micro.status_code == 200, p_micro.text
    cand = p_micro.json()["data"]
    assert cand["can_undo"] is True
    s0 = next(s for s in cand["sessions"] if s["client_session_key"] == first_sess_key)
    assert s0["micro_task_text"] == "Mở mục 1 và gạch 3 ý đầu"
    assert s0["title"] == "Phiên mở màn tùy chỉnh"
    assert s0["session_goal"] == "Chốt khung sườn A"
    assert s0["expected_output"] == "Dàn ý A hoàn chỉnh"

    # 3) add_steps at insert_at_index=1 -> sequential order_index & unused step-4
    p_add = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-add",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "add_steps": [
                    {
                        "title": "Bước Chèn",
                        "expected_output": "Out Chèn",
                        "estimated_base_seconds": 1500,
                        "micro_task_text": "Mở tài liệu Bước Chèn",
                        "insert_at_index": 1,
                    }
                ]
            },
        },
    )
    assert p_add.status_code == 200, p_add.text
    cand = p_add.json()["data"]
    assert [s["title"] for s in cand["steps"]] == ["Bước A", "Bước Chèn", "Bước B", "Bước C"]
    assert [s["order_index"] for s in cand["steps"]] == [0, 1, 2, 3]
    assert cand["steps"][1]["client_step_key"] == "step-4"

    # 4) merge_steps (step-4 + step-2) -> inherits sum & micro_task from step-4
    p_merge = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-merge",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "merge_steps": [
                    {
                        "source_step_keys": ["step-4", "step-2"],
                        "title": "Bước Gộp Chèn-B",
                        "expected_output": "Out Gộp",
                    }
                ]
            },
        },
    )
    assert p_merge.status_code == 200, p_merge.text
    cand = p_merge.json()["data"]
    assert [s["title"] for s in cand["steps"]] == ["Bước A", "Bước Gộp Chèn-B", "Bước C"]
    assert [s["order_index"] for s in cand["steps"]] == [0, 1, 2]
    merged_step = cand["steps"][1]
    assert merged_step["estimated_base_seconds"] == 3000
    merged_sess = sorted(
        [s for s in cand["sessions"] if s["client_step_key"] == merged_step["client_step_key"]],
        key=lambda x: x["scheduled_start"],
    )
    assert len(merged_sess) >= 2
    assert merged_sess[0]["micro_task_text"] == "Mở tài liệu Bước Chèn"

    # 5) split_steps on merged_step -> 2 steps in-place + delete step-3
    p_split_del = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-split-del",
        {
            "expected_review_revision": cand["review_revision"],
            "edits": {
                "delete_step_keys": ["step-3"],
                "split_steps": [
                    {
                        "source_step_key": merged_step["client_step_key"],
                        "new_steps": [
                            {
                                "title": "Tách 1",
                                "expected_output": "Out Tách 1",
                                "estimated_base_seconds": 1200,
                                "micro_task_text": "Mở Tách 1",
                            },
                            {
                                "title": "Tách 2",
                                "expected_output": "Out Tách 2",
                                "estimated_base_seconds": 1200,
                                "micro_task_text": "Mở Tách 2",
                            },
                        ],
                    }
                ],
            },
        },
    )
    assert p_split_del.status_code == 200, p_split_del.text
    cand = p_split_del.json()["data"]
    assert [s["title"] for s in cand["steps"]] == ["Bước A", "Tách 1", "Tách 2"]
    assert [s["order_index"] for s in cand["steps"]] == [0, 1, 2]

    # 6) NL user_instruction wiring for all 5 arrays + lock-free DB verification + NO_CANDIDATE_CHANGES
    from app.schemas.planning import UpdateCandidateRequest

    nl_tx_checks: list[bool] = []
    async with factory() as db:
        step_keys = [s["client_step_key"] for s in cand["steps"]]

        async def _mock_nl_noop(*args, **kwargs):
            nl_tx_checks.append(db.in_transaction())
            return CandidateAdjustmentResult(
                step_edits=[
                    CandidateStepAdjustment(
                        client_step_key=step_keys[0],
                        title=cand["steps"][0]["title"],
                    )
                ],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Không đổi.",
                generation_tier="tier_1_gemini",
            )

        monkeypatch.setattr(ai_service, "adjust_candidate_by_nl", _mock_nl_noop)
        with pytest.raises(Exception) as exc_info:
            await plan_service.patch_candidate(
                user_id="user_a",
                candidate_id=cand["candidate_id"],
                idempotency_key="ops-nl-noop",
                payload=UpdateCandidateRequest(
                    expected_review_revision=cand["review_revision"],
                    user_instruction="Giữ nguyên tên bước 1",
                ),
                db=db,
            )
        assert getattr(exc_info.value, "code", None) == "NO_CANDIDATE_CHANGES"
        assert nl_tx_checks == [False]

        async def _mock_nl_structural(*args, **kwargs):
            nl_tx_checks.append(db.in_transaction())
            return CandidateAdjustmentResult(
                step_edits=[
                    CandidateStepAdjustment(
                        client_step_key=step_keys[0],
                        estimated_minutes=30,
                        micro_task_text="Mở tài liệu NL mới",
                    )
                ],
                add_steps=[
                    NLCandidateStepAdd(
                        title="Bước Thêm NL",
                        expected_output="Output Thêm NL",
                        estimated_minutes=20,
                        micro_task_prompt="Bắt đầu NL thêm",
                        insert_at_index=1,
                    )
                ],
                delete_step_keys=[step_keys[2]],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Đã cập nhật qua NL.",
                generation_tier="tier_1_gemini",
            )

        monkeypatch.setattr(ai_service, "adjust_candidate_by_nl", _mock_nl_structural)
        updated_nl = await plan_service.patch_candidate(
            user_id="user_a",
            candidate_id=cand["candidate_id"],
            idempotency_key="ops-nl-struct",
            payload=UpdateCandidateRequest(
                expected_review_revision=cand["review_revision"],
                user_instruction="Đổi bước 1 thành 30 phút, thêm bước mới và xóa bước cuối",
            ),
            db=db,
        )
        assert [s.title for s in updated_nl.steps] == ["Bước A", "Bước Thêm NL", "Tách 1"]
        assert updated_nl.steps[0].estimated_base_seconds == 1800
        assert updated_nl.can_undo is True
        assert nl_tx_checks == [False, False]

    # Also exercise merge_steps & split_steps via NL via HTTP endpoint
    nl_keys = [s.client_step_key for s in updated_nl.steps]

    async def _mock_nl_merge_split(*args, **kwargs):
        return CandidateAdjustmentResult(
            merge_steps=[
                NLCandidateStepMerge(
                    source_step_keys=[nl_keys[0], nl_keys[1]],
                    title="Gộp NL",
                    expected_output="Output Gộp NL",
                    estimated_minutes=35,
                    micro_task_prompt="Micro gộp NL",
                )
            ],
            split_steps=[
                NLCandidateStepSplit(
                    source_step_key=nl_keys[2],
                    new_steps=[
                        NLCandidateStepAdd(
                            title="Tách NL 1",
                            expected_output="Out NL 1",
                            estimated_minutes=15,
                            micro_task_prompt="Mở NL 1",
                        ),
                        NLCandidateStepAdd(
                            title="Tách NL 2",
                            expected_output="Out NL 2",
                            estimated_minutes=15,
                            micro_task_prompt="Mở NL 2",
                        ),
                    ],
                )
            ],
            requires_clarification=False,
            clarification_question=None,
            requires_draft_update=False,
            unsupported_changes=[],
            assistant_note="Đã gộp và tách qua NL.",
            generation_tier="tier_1_gemini",
        )

    monkeypatch.setattr(ai_service, "adjust_candidate_by_nl", _mock_nl_merge_split)
    resp_nl_ms = await _patch(
        client,
        f"/candidates/{cand['candidate_id']}",
        "ops-nl-ms",
        {
            "expected_review_revision": updated_nl.review_revision,
            "user_instruction": "Gộp 2 bước đầu và tách bước cuối",
        },
    )
    assert resp_nl_ms.status_code == 200, resp_nl_ms.text
    final_cand = resp_nl_ms.json()["data"]
    assert [s["title"] for s in final_cand["steps"]] == ["Gộp NL", "Tách NL 1", "Tách NL 2"]
    assert final_cand["steps"][0]["estimated_base_seconds"] == 2100


async def test_u2_be_12_14_one_step_undo_lifecycle_and_conflicts(api) -> None:
    """U2-BE-12..14: 1-step Undo khôi phục toàn vẹn steps/_micro_task_text/sessions, replay idempotent & 409 conflicts."""
    client, factory = api
    draft = await _confirmed_draft(
        client, "undo-draft", summary=_base_summary(days=10, start_local="08:00", end_local="16:00")
    )
    cand = (
        await _create_candidate(
            client,
            draft,
            "undo-cand",
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
    assert cand["can_undo"] is False

    # Undo before any edit -> 409 UNDO_UNAVAILABLE
    resp_no_undo = await _post(
        client,
        f"/candidates/{cand['candidate_id']}/undo",
        "undo-none",
        {"expected_review_revision": cand["review_revision"]},
    )
    assert resp_no_undo.status_code == 409
    assert resp_no_undo.json()["error"]["code"] == "UNDO_UNAVAILABLE"

    # Patch 1: set micro_task_text & session title
    p1 = (
        await _patch(
            client,
            f"/candidates/{cand['candidate_id']}",
            "undo-p1",
            {
                "expected_review_revision": cand["review_revision"],
                "edits": {
                    "steps": [
                        {"client_step_key": "step-1", "micro_task_text": "Micro gốc cần khôi phục"}
                    ],
                    "sessions": [
                        {
                            "client_session_key": cand["sessions"][0]["client_session_key"],
                            "title": "Tiêu đề trước khi xóa",
                        }
                    ],
                },
            },
        )
    ).json()["data"]
    assert p1["can_undo"] is True

    # Patch 2: delete step-2 (structural + reschedule)
    p2 = (
        await _patch(
            client,
            f"/candidates/{cand['candidate_id']}",
            "undo-p2",
            {
                "expected_review_revision": p1["review_revision"],
                "edits": {"delete_step_keys": ["step-2"]},
            },
        )
    ).json()["data"]
    assert p2["can_undo"] is True
    assert len(p2["steps"]) == 1

    # Stale revision -> 409 REVISION_CONFLICT
    resp_rev_err = await _post(
        client,
        f"/candidates/{cand['candidate_id']}/undo",
        "undo-rev-err",
        {"expected_review_revision": p1["review_revision"]},
    )
    assert resp_rev_err.status_code == 409
    assert resp_rev_err.json()["error"]["code"] == "REVISION_CONFLICT"

    # Valid Undo -> restores Patch 1 state
    resp_undo = await _post(
        client,
        f"/candidates/{cand['candidate_id']}/undo",
        "undo-exec",
        {"expected_review_revision": p2["review_revision"]},
    )
    assert resp_undo.status_code == 200, resp_undo.text
    undone = resp_undo.json()["data"]
    assert undone["can_undo"] is False
    assert undone["review_revision"] == p2["review_revision"] + 1
    assert [s["client_step_key"] for s in undone["steps"]] == ["step-1", "step-2"]
    assert undone["sessions"][0]["micro_task_text"] == "Micro gốc cần khôi phục"
    assert undone["sessions"][0]["title"] == "Tiêu đề trước khi xóa"

    # Verify DB row._micro_task_text was restored and previous_snapshot cleared
    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand["candidate_id"]))
        assert row is not None
        assert row.previous_snapshot is None
        assert row.steps[0]["_micro_task_text"] == "Micro gốc cần khôi phục"

    # Replay same idempotency key -> 200 idempotent replay
    resp_replay = await _post(
        client,
        f"/candidates/{cand['candidate_id']}/undo",
        "undo-exec",
        {"expected_review_revision": p2["review_revision"]},
    )
    assert resp_replay.status_code == 200
    assert resp_replay.json()["data"]["review_revision"] == undone["review_revision"]
    assert resp_replay.json()["data"]["can_undo"] is False

    # Second undo with new key -> 409 UNDO_UNAVAILABLE
    resp_second = await _post(
        client,
        f"/candidates/{cand['candidate_id']}/undo",
        "undo-second",
        {"expected_review_revision": undone["review_revision"]},
    )
    assert resp_second.status_code == 409
    assert resp_second.json()["error"]["code"] == "UNDO_UNAVAILABLE"

    # Confirm restored candidate succeeds
    conf = await _confirm_candidate(client, undone, "undo-confirm")
    assert conf.status_code == 201, conf.text
    assert len(conf.json()["data"]["task"]["steps"]) == 2


async def test_u2_be_16_17_extension_candidate_at_confirm_and_expand_horizon(api) -> None:
    """U2-BE-16 & U2-BE-17: extension_candidate_at lưu khi confirm partial, cập nhật khi expand partial, và xóa khi complete."""
    client, factory = api
    draft = await _confirmed_draft(
        client, "ext-draft", summary=_base_summary(days=60, start_local="08:00", end_local="20:00")
    )
    cand_resp = await _create_candidate(
        client,
        draft,
        "ext-cand",
        manual_steps=[
            {
                "order_index": 0,
                "title": "Phần mở đầu",
                "expected_output": "Xong mở đầu",
                "estimated_base_seconds": 1250,
            }
        ],
        unexpanded_scope="Mục 1; Mục 2; Mục 3; Mục 4; Mục 5; Mục 6; Mục 7",
    )
    assert cand_resp.status_code == 201, cand_resp.text
    cand = cand_resp.json()["data"]
    conf_resp = await _confirm_candidate(client, cand, "ext-confirm")
    assert conf_resp.status_code == 201, conf_resp.text
    task = conf_resp.json()["data"]["task"]
    expected_ext = max(
        datetime.fromisoformat(s["scheduled_end"].replace("Z", "+00:00")) for s in task["sessions"]
    )
    assert task["extension_candidate_at"] is not None
    assert datetime.fromisoformat(task["extension_candidate_at"].replace("Z", "+00:00")) == expected_ext

    get_resp = await client.get(f"/api/v1/tasks/{task['task_id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["extension_candidate_at"] == task["extension_candidate_at"]

    list_resp = await client.get("/api/v1/tasks")
    assert list_resp.status_code == 200
    assert list_resp.json()["data"]["items"][0]["extension_candidate_at"] == task["extension_candidate_at"]

    # Pass 1 expand_next_horizon -> 6 steps added, "Mục 7" left -> still partial with updated extension_candidate_at
    now = datetime.now(UTC)
    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        revs, _ = await reschedule_service._compute_db_source_revisions(
            db, owner, target_task_id=task["task_id"]
        )
        sched_rev = await reschedule_service._get_current_schedule_revision(db, owner)
        prop1 = await reschedule_service.create_proposal(
            db,
            user_id="user_a",
            idempotency_key="ext-prop-1",
            payload=CreateProposalRequest(
                trigger_reason="expand_next_horizon",
                target_task_id=task["task_id"],
                allowed_task_ids=[task["task_id"]],
                expected_base_schedule_revision=sched_rev,
                expected_source_revisions=ProposalSourceRevisions.model_validate(revs.model_dump()),
            ),
            now=now + timedelta(hours=3),
        )

    async with factory() as db:
        await reschedule_service.approve_proposal(
            db,
            user_id="user_a",
            proposal_id=prop1.proposal_id,
            idempotency_key="ext-app-1",
            payload=ApproveProposalRequest(
                expected_proposal_revision=prop1.proposal_revision,
                expected_base_schedule_revision=prop1.base_schedule_revision,
                expected_source_revisions=prop1.source_revisions,
            ),
            now=now + timedelta(hours=3, minutes=1),
        )

    t_after1 = (await client.get(f"/api/v1/tasks/{task['task_id']}")).json()["data"]
    assert t_after1["decomposition_state"] == "partial"
    assert t_after1["extension_candidate_at"] is not None
    after1_max_end = max(
        datetime.fromisoformat(s["scheduled_end"].replace("Z", "+00:00"))
        for s in t_after1["sessions"]
    )
    assert datetime.fromisoformat(t_after1["extension_candidate_at"].replace("Z", "+00:00")) == after1_max_end
    assert after1_max_end > expected_ext

    # Pass 2 expand_next_horizon -> "Mục 7" expanded -> complete, extension_candidate_at cleared to None
    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        revs2, _ = await reschedule_service._compute_db_source_revisions(
            db, owner, target_task_id=task["task_id"]
        )
        sched_rev2 = await reschedule_service._get_current_schedule_revision(db, owner)
        prop2 = await reschedule_service.create_proposal(
            db,
            user_id="user_a",
            idempotency_key="ext-prop-2",
            payload=CreateProposalRequest(
                trigger_reason="expand_next_horizon",
                target_task_id=task["task_id"],
                allowed_task_ids=[task["task_id"]],
                expected_base_schedule_revision=sched_rev2,
                expected_source_revisions=ProposalSourceRevisions.model_validate(revs2.model_dump()),
            ),
            now=now + timedelta(days=1),
        )

    async with factory() as db:
        await reschedule_service.approve_proposal(
            db,
            user_id="user_a",
            proposal_id=prop2.proposal_id,
            idempotency_key="ext-app-2",
            payload=ApproveProposalRequest(
                expected_proposal_revision=prop2.proposal_revision,
                expected_base_schedule_revision=prop2.base_schedule_revision,
                expected_source_revisions=prop2.source_revisions,
            ),
            now=now + timedelta(days=1, minutes=1),
        )

    t_after2 = (await client.get(f"/api/v1/tasks/{task['task_id']}")).json()["data"]
    assert t_after2["decomposition_state"] == "complete"
    assert t_after2["extension_candidate_at"] is None


async def test_u2_be_18_se_04_05_expand_horizon_ai_decompose_enrich_and_calendar_outbox(
    api, monkeypatch
) -> None:
    """U2-BE-18 & U2-SE-04/05: helper build_calendar_event_text, AI decompose+enrich ở expand_next_horizon (lock-free), và outbox summary/description."""
    # 1) Unit checks for build_calendar_event_text
    sum1, desc1 = build_calendar_event_text(
        "Viết chương 1", "Bước 1", "Hoàn thiện mở đầu", "File nháp 2 trang"
    )
    assert sum1 == "[ProPlan] Viết chương 1"
    assert desc1 == "Mục tiêu: Hoàn thiện mở đầu\nĐầu ra dự kiến: File nháp 2 trang"

    sum2, desc2 = build_calendar_event_text(None, "Bước dự phòng", None, None)
    assert sum2 == "[ProPlan] Bước dự phòng"
    assert desc2 is None

    sum3, desc3 = build_calendar_event_text("A" * 250, None, "Goal", None)
    assert len(sum3) == 200
    assert sum3.startswith("[ProPlan] ")
    assert desc3 == "Mục tiêu: Goal"

    sum4, _ = build_calendar_event_text(None, None, None, None)
    assert sum4 == "[ProPlan] Focus Session"

    # 2) Connected CalendarConnection -> confirm_candidate + expand_next_horizon AI + user_reschedule outbox
    client, factory = api
    async with factory() as db:
        await draft_service.ensure_dev_seed_in_db(db)
        await _connect_calendar(db, "user_a")
        await db.commit()

    draft = await _confirmed_draft(
        client, "cal-draft", summary=_base_summary(days=45, start_local="08:00", end_local="08:40")
    )
    cand = (
        await _create_candidate(
            client,
            draft,
            "cal-cand",
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
    conf = (await _confirm_candidate(client, cand, "cal-conf")).json()["data"]
    task_id = conf["task"]["task_id"]

    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        jobs = (
            await db.scalars(
                select(CalendarSyncJob).where(CalendarSyncJob.user_id == owner)
            )
        ).all()
        assert len(jobs) == 1
        snap = jobs[0].payload_snapshot
        assert snap["summary"] == "[ProPlan] Bước Khởi Động"
        assert "Mục tiêu:" in snap["description"]
        assert "Đầu ra dự kiến: Tài liệu sẵn sàng" in snap["description"]

    # 3) expand_next_horizon with AI decompose (7 steps -> 6 selected + 1 leftover) & batch split enrichment, lock-free
    now = datetime.now(UTC)
    tx_checks: list[tuple[str, bool]] = []
    async with factory() as db:
        async def _mock_expand_decompose(**kwargs):
            tx_checks.append(("expand_decompose", db.in_transaction()))
            return TaskDecompositionResult(
                task_title="Đề án",
                steps=[
                    StepPromptItem(
                        order_index=i + 1,
                        title=f"Bước AI {i + 1}",
                        expected_output=f"Đầu ra AI {i + 1}",
                        estimated_minutes=50 if i == 0 else 20,
                        micro_task_prompt=f"Khởi động AI {i + 1}",
                    )
                    for i in range(7)
                ],
                reasoning="Chia 7 bước AI.",
                generation_tier="tier_1_gemini",
            )

        async def _mock_expand_enrich(split_sessions, *, task_title=None):
            tx_checks.append(("expand_enrich", db.in_transaction()))
            return SessionEnrichmentResult(
                sessions=[
                    LLMSessionEnrichmentItem(
                        client_session_key=item.client_session_key,
                        title=f"{item.step_title} ({item.session_index}/{item.total_sessions_in_step})",
                        session_goal=f"Mục tiêu expand phần {item.session_index}",
                        expected_output=f"Đầu ra expand phần {item.session_index}",
                        micro_task_text=f"Tiếp tục expand phần {item.session_index}",
                    )
                    for item in split_sessions
                ],
                generation_tier="tier_1_gemini",
            )

        monkeypatch.setattr(ai_service, "decompose_task", _mock_expand_decompose)
        monkeypatch.setattr(ai_service, "enrich_split_sessions", _mock_expand_enrich)

        owner = resolve_user_uuid("user_a")
        revs, _ = await reschedule_service._compute_db_source_revisions(
            db, owner, target_task_id=task_id
        )
        sched_rev = await reschedule_service._get_current_schedule_revision(db, owner)
        prop_exp = await reschedule_service.create_proposal(
            db,
            user_id="user_a",
            idempotency_key="cal-exp-prop",
            payload=CreateProposalRequest(
                trigger_reason="expand_next_horizon",
                target_task_id=task_id,
                allowed_task_ids=[task_id],
                expected_base_schedule_revision=sched_rev,
                expected_source_revisions=ProposalSourceRevisions.model_validate(revs.model_dump()),
            ),
            now=now + timedelta(hours=1),
        )

    assert tx_checks == [("expand_decompose", False), ("expand_enrich", False)]
    assert len(prop_exp.proposed_changes.steps_to_add) == 6
    assert prop_exp.proposed_changes.steps_to_add[0].expected_output == "Đầu ra AI 1"
    assert prop_exp.feasibility.next_horizon_preview is not None
    assert prop_exp.feasibility.next_horizon_preview["unexpanded_scope"] == "Bước AI 7"

    step1_adds = [
        a
        for a in prop_exp.proposed_changes.sessions_to_add
        if a.step_id == prop_exp.proposed_changes.steps_to_add[0].step_id
    ]
    assert len(step1_adds) >= 2
    assert step1_adds[0].after.title == "Bước AI 1 (1/2)"
    assert step1_adds[0].after.micro_task_text == "Khởi động AI 1"
    assert step1_adds[1].after.micro_task_text == "Tiếp tục expand phần 2"
    assert step1_adds[1].after.micro_task_seconds == 0

    async with factory() as db:
        approved_exp = await reschedule_service.approve_proposal(
            db,
            user_id="user_a",
            proposal_id=prop_exp.proposal_id,
            idempotency_key="cal-exp-app",
            payload=ApproveProposalRequest(
                expected_proposal_revision=prop_exp.proposal_revision,
                expected_base_schedule_revision=prop_exp.base_schedule_revision,
                expected_source_revisions=prop_exp.source_revisions,
            ),
            now=now + timedelta(hours=1, minutes=1),
        )
        exp_dto = next(
            (s for s in approved_exp.sessions if s.title == "Bước AI 1 (1/2)"),
            None,
        )
        assert exp_dto is not None
        assert exp_dto.session_goal == "Mục tiêu expand phần 1"
        assert exp_dto.expected_output == "Đầu ra expand phần 1"

    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        exp_step = await db.scalar(
            select(Step).where(Step.task_id == UUID(task_id), Step.title == "Bước AI 1")
        )
        assert exp_step is not None
        assert exp_step.expected_output == "Đầu ra AI 1"

        exp_task = await db.get(Task, UUID(task_id))
        assert exp_task is not None
        assert exp_task.unexpanded_scope == "Bước AI 7"
        assert exp_task.extension_candidate_at is not None

        exp_sessions = (
            await db.scalars(
                select(SessionRow).where(SessionRow.step_id == exp_step.id).order_by(SessionRow.scheduled_start)
            )
        ).all()
        assert exp_sessions[0].title == "Bước AI 1 (1/2)"
        assert exp_sessions[0].session_goal == "Mục tiêu expand phần 1"
        assert exp_sessions[0].expected_output == "Đầu ra expand phần 1"

        add_jobs = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.target_ref == str(exp_sessions[0].id),
                )
            )
        ).all()
        assert len(add_jobs) == 1
        assert add_jobs[0].payload_snapshot["summary"] == "[ProPlan] Bước AI 1 (1/2)"
        assert "Mục tiêu: Mục tiêu expand phần 1" in add_jobs[0].payload_snapshot["description"]

    # 4) user_reschedule (moved_sessions) -> CalendarSyncJob uses build_calendar_event_text too
    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        revs3, _ = await reschedule_service._compute_db_source_revisions(
            db, owner, target_task_id=task_id
        )
        sched_rev3 = await reschedule_service._get_current_schedule_revision(db, owner)
        prop_move = await reschedule_service.create_proposal(
            db,
            user_id="user_a",
            idempotency_key="cal-move-prop",
            payload=CreateProposalRequest(
                trigger_reason="user_reschedule",
                target_task_id=task_id,
                allowed_task_ids=[task_id],
                expected_base_schedule_revision=sched_rev3,
                expected_source_revisions=ProposalSourceRevisions.model_validate(revs3.model_dump()),
                request_text="Dời sang mai",
            ),
            now=now + timedelta(hours=1, minutes=2),
        )
        assert len(prop_move.proposed_changes.sessions_to_move) >= 1

    async with factory() as db:
        approved_move = await reschedule_service.approve_proposal(
            db,
            user_id="user_a",
            proposal_id=prop_move.proposal_id,
            idempotency_key="cal-move-app",
            payload=ApproveProposalRequest(
                expected_proposal_revision=prop_move.proposal_revision,
                expected_base_schedule_revision=prop_move.base_schedule_revision,
                expected_source_revisions=prop_move.source_revisions,
            ),
            now=now + timedelta(hours=1, minutes=3),
        )
        assert any(
            s.title == "Bước AI 1 (1/2)"
            and s.session_goal == "Mục tiêu expand phần 1"
            and s.expected_output == "Đầu ra expand phần 1"
            for s in approved_move.sessions
        )

    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        move_jobs = (
            await db.scalars(
                select(CalendarSyncJob).where(
                    CalendarSyncJob.user_id == owner,
                    CalendarSyncJob.target_schedule_revision == sched_rev3 + 1,
                )
            )
        ).all()
        assert len(move_jobs) >= 1
        assert all(job.payload_snapshot["summary"].startswith("[ProPlan] ") for job in move_jobs)
        assert any("Mục tiêu:" in job.payload_snapshot.get("description", "") for job in move_jobs)


async def test_alembic_uf2_populated_db_downgrade_roundtrip(sqlite_migration) -> None:
    """SQLite có dữ liệu sẵn + PRAGMA foreign_keys=ON: upgrade head -> downgrade -1 -> upgrade head phải bảo toàn dòng dữ liệu."""
    url, alembic = sqlite_migration
    sync_url = url.replace("+aiosqlite", "")

    alembic("upgrade", "head")
    eng = make_engine(url)
    try:
        async with session_factory(eng)() as db:
            await draft_service.ensure_dev_seed_in_db(db)
            owner = resolve_user_uuid("user_a")
            now_dt = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            horizon_payload = {
                "start_at": now_dt.isoformat(),
                "end_at": (now_dt + timedelta(days=14)).isoformat(),
            }
            draft = PlanningDraft(
                user_id=owner,
                client_draft_id="pop-mig-draft",
                stage="confirmed",
                input_source="text",
                summary={"title": "Populated Migration"},
            )
            db.add(draft)
            await db.flush()

            cand = CandidateRow(
                user_id=owner,
                draft_id=draft.id,
                source_summary_revision=1,
                source_planning_input_revision=1,
                review_revision=2,
                decomposition_state="complete",
                steps=[],
                sessions=[],
                planning_horizon=horizon_payload,
                source_revisions={"preferences": 1, "schedule": 0},
                feasibility={
                    "status": "feasible",
                    "verification_scope": {
                        "scope_type": "candidate_steps",
                        "client_step_keys": ["s1"],
                        "planning_horizon": horizon_payload,
                    },
                    "fully_scheduled": True,
                },
                previous_snapshot={"review_revision": 1},
            )
            task = Task(
                user_id=owner,
                title="Task Tồn Tại",
                time_constraint={
                    "kind": "deadline",
                    "start_at": now_dt.isoformat(),
                    "deadline_at": (now_dt + timedelta(days=10)).isoformat(),
                    "input_timezone": "UTC",
                },
                status="pending",
                decomposition_state="partial",
                unexpanded_scope="Phần 2",
                extension_candidate_at=now_dt + timedelta(days=14),
            )
            db.add_all([cand, task])
            await db.flush()

            step = Step(
                user_id=owner,
                task_id=task.id,
                title="Bước 1",
                order_index=0,
                estimated_base_seconds=1200,
                remaining_base_seconds=1200,
                buffer_multiplier=1.2,
                estimate_source="user_confirmed",
            )
            db.add(step)
            await db.flush()

            sess = SessionRow(
                user_id=owner,
                step_id=step.id,
                scheduled_start=now_dt + timedelta(hours=1),
                scheduled_end=now_dt + timedelta(hours=1, minutes=20),
                title="Phiên Đầy Đủ",
                session_goal="Mục tiêu Đầy Đủ",
                expected_output="Đầu ra Đầy Đủ",
                work_seconds=1200,
                rest_seconds=0,
                segments=[{"order_index": 0, "kind": "work", "duration_seconds": 1200}],
                duration_seconds=1200,
                schedule_revision=1,
            )
            db.add(sess)
            await db.commit()
    finally:
        await eng.dispose()

    alembic("downgrade", "-1")
    sync_eng = create_engine(sync_url)
    try:
        insp = inspect(sync_eng)
        assert "previous_snapshot" not in {c["name"] for c in insp.get_columns("plan_candidates")}
        assert "extension_candidate_at" not in {c["name"] for c in insp.get_columns("tasks")}
        assert not (
            {"title", "session_goal", "expected_output"}
            & {c["name"] for c in insp.get_columns("sessions")}
        )
        with sync_eng.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []
            assert conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM steps")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM plan_candidates")).scalar_one() == 1
            assert conn.execute(text("SELECT title FROM tasks")).scalar_one() == "Task Tồn Tại"
    finally:
        sync_eng.dispose()

    alembic("upgrade", "head")
    eng2 = make_engine(url)
    try:
        async with session_factory(eng2)() as db:
            restored_task = await db.scalar(select(Task))
            restored_sess = await db.scalar(select(SessionRow))
            restored_cand = await db.scalar(select(CandidateRow))
            assert restored_task is not None
            assert restored_task.title == "Task Tồn Tại"
            assert restored_task.extension_candidate_at is None
            assert restored_sess is not None
            assert restored_sess.work_seconds == 1200
            assert restored_sess.title is None
            assert restored_cand is not None
            assert restored_cand.previous_snapshot is None
    finally:
        await eng2.dispose()


async def test_candidate_step_edit_validation_and_enrich_over_50_sessions_fallback(
    api, monkeypatch
) -> None:
    """CandidateStepEdit từ chối chuỗi trống/quá dài bằng 422 (không 500) và > 50 sessions tự fallback."""
    client, _ = api
    draft = await _confirmed_draft(client, "val-draft")
    cand = (
        await _create_candidate(
            client,
            draft,
            "val-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước hợp lệ",
                    "expected_output": "Đầu ra hợp lệ",
                    "estimated_base_seconds": 1200,
                }
            ],
        )
    ).json()["data"]
    step_key = cand["steps"][0]["client_step_key"]

    for bad_idx, step_edit in enumerate(
        [
            {"client_step_key": step_key, "title": "   "},
            {"client_step_key": step_key, "expected_output": " \t\n "},
            {"client_step_key": step_key, "title": "A" * 256},
            {"client_step_key": step_key, "expected_output": "B" * 501},
        ]
    ):
        bad_resp = await _patch(
            client,
            f"/candidates/{cand['candidate_id']}",
            f"val-bad-{bad_idx}",
            {
                "expected_review_revision": cand["review_revision"],
                "edits": {"steps": [step_edit]},
            },
        )
        assert bad_resp.status_code == 422, bad_resp.text

    base_start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
    sessions_52 = [
        CandidateSession(
            client_session_key=f"big-sess-{idx + 1}",
            client_step_key="step-big",
            scheduled_start=base_start + timedelta(hours=idx),
            scheduled_end=base_start + timedelta(hours=idx, minutes=20),
            micro_task_text="Mở IDE làm phần đầu",
            micro_task_seconds=120 if idx == 0 else 0,
            work_seconds=1200,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=1200)],
            duration_seconds=1200,
            start_state="waiting",
            start_decision_due_at=base_start + timedelta(hours=idx, minutes=10),
        )
        for idx in range(52)
    ]
    big_cand = PlanCandidate(
        candidate_id="cand-big-52",
        draft_id="draft-big-52",
        source_summary_revision=1,
        source_planning_input_revision=1,
        review_revision=1,
        steps=[
            CandidateStep(
                client_step_key="step-big",
                title="Xây dựng 52 module",
                expected_output="Hoàn thiện 52 module",
                order_index=0,
                estimated_base_seconds=62400,
                remaining_base_seconds=62400,
                buffer_multiplier=1.0,
                estimate_source="user_confirmed",
                status="pending",
            )
        ],
        sessions=sessions_52,
        planning_horizon=TimeInterval(
            start_at=base_start,
            end_at=base_start + timedelta(days=14),
        ),
        source_revisions=SourceRevisions(availability=None, preferences=1, schedule=0),
        min_inter_session_gap_seconds=900,
        feasibility=FeasibilityReport(
            status="feasible",
            verification_scope=CandidateVerificationScope(
                client_step_keys=["step-big"],
                planning_horizon=TimeInterval(
                    start_at=base_start,
                    end_at=base_start + timedelta(days=14),
                ),
            ),
            fully_scheduled=True,
        ),
        stale=False,
    )

    enrich_mock = AsyncMock(side_effect=AssertionError("Không được gọi LLM khi > 50 phiên"))
    monkeypatch.setattr(ai_service, "enrich_split_sessions", enrich_mock)
    enriched = await plan_service._enrich_candidate_sessions(big_cand, "Đề án 52 phiên")
    assert enrich_mock.await_count == 0
    ordered = sorted(enriched.sessions, key=lambda item: item.scheduled_start)
    assert len(ordered) == 52
    assert ordered[0].title == "Xây dựng 52 module (Phần 1/52)"
    assert ordered[0].micro_task_text == "Mở IDE làm phần đầu"
    assert ordered[1].title == "Xây dựng 52 module (Phần 2/52)"
    assert "làm tiếp phần 2/52" in ordered[1].micro_task_text
    assert ordered[51].title == "Xây dựng 52 module (Phần 52/52)"


async def test_non_timing_step_micro_preserves_continuation_and_subset_scope_structural_ops(
    api,
) -> None:
    """Sửa micro_task_text trên step không đổi timing giữ nguyên phần 2+, và ops bảo toàn subset scope."""
    client, factory = api
    draft_micro = await _confirmed_draft(
        client,
        "micro-draft",
        summary=_base_summary(days=10, start_local="08:00", end_local="08:40"),
    )
    cand_micro = (
        await _create_candidate(
            client,
            draft_micro,
            "micro-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước Dài Đa Phiên",
                    "expected_output": "Hoàn thành 2 phần",
                    "estimated_base_seconds": 3000,
                }
            ],
        )
    ).json()["data"]
    step_key = cand_micro["steps"][0]["client_step_key"]
    sess_before = sorted(cand_micro["sessions"], key=lambda item: item["scheduled_start"])
    assert len(sess_before) >= 2
    part1_micro_before = sess_before[0]["micro_task_text"]
    part2_micro_before = sess_before[1]["micro_task_text"]
    assert part2_micro_before != part1_micro_before

    patch_micro = await _patch(
        client,
        f"/candidates/{cand_micro['candidate_id']}",
        "micro-patch-non-timing",
        {
            "expected_review_revision": cand_micro["review_revision"],
            "edits": {
                "steps": [
                    {
                        "client_step_key": step_key,
                        "micro_task_text": "Mở file note 2 phút mới nhất",
                    }
                ]
            },
        },
    )
    assert patch_micro.status_code == 200, patch_micro.text
    sess_after = sorted(
        patch_micro.json()["data"]["sessions"],
        key=lambda item: item["scheduled_start"],
    )
    assert sess_after[0]["micro_task_text"] == "Mở file note 2 phút mới nhất"
    assert sess_after[1]["micro_task_text"] == part2_micro_before

    # Subset verification_scope qua structural ops không phình ra thành all steps
    draft_scope = await _confirmed_draft(
        client,
        "scope-draft",
        summary=_base_summary(days=10, start_local="09:00", end_local="13:00"),
    )
    cand_scope = (
        await _create_candidate(
            client,
            draft_scope,
            "scope-cand",
            manual_steps=[
                {
                    "order_index": i,
                    "title": f"Bước Scope {i + 1}",
                    "expected_output": f"Đầu ra Scope {i + 1}",
                    "estimated_base_seconds": 1200,
                }
                for i in range(4)
            ],
        )
    ).json()["data"]

    async with factory() as db:
        row = await db.get(CandidateRow, UUID(cand_scope["candidate_id"]))
        assert row is not None
        feas_copy = json.loads(json.dumps(row.feasibility))
        feas_copy["verification_scope"]["client_step_keys"] = [
            cand_scope["steps"][0]["client_step_key"],
            cand_scope["steps"][1]["client_step_key"],
        ]
        row.feasibility = feas_copy
        await db.commit()

    split_add_resp = await _patch(
        client,
        f"/candidates/{cand_scope['candidate_id']}",
        "scope-split-add",
        {
            "expected_review_revision": cand_scope["review_revision"],
            "edits": {
                "split_steps": [
                    {
                        "source_step_key": cand_scope["steps"][1]["client_step_key"],
                        "new_steps": [
                            {
                                "title": "Nhánh 2A",
                                "expected_output": "Đầu ra 2A",
                                "estimated_base_seconds": 1200,
                            },
                            {
                                "title": "Nhánh 2B",
                                "expected_output": "Đầu ra 2B",
                                "estimated_base_seconds": 1200,
                            },
                        ],
                    }
                ],
                "add_steps": [
                    {
                        "title": "Chèn Giữa",
                        "expected_output": "Đầu ra Chèn Giữa",
                        "estimated_base_seconds": 1200,
                        "insert_at_index": 1,
                    }
                ],
            },
        },
    )
    assert split_add_resp.status_code == 200, split_add_resp.text
    scoped_cand = split_add_resp.json()["data"]
    scope_keys_after = set(scoped_cand["feasibility"]["verification_scope"]["client_step_keys"])
    step4_key = next(s["client_step_key"] for s in scoped_cand["steps"] if s["title"] == "Bước Scope 4")
    branch_2a_key = next(s["client_step_key"] for s in scoped_cand["steps"] if s["title"] == "Nhánh 2A")
    branch_2b_key = next(s["client_step_key"] for s in scoped_cand["steps"] if s["title"] == "Nhánh 2B")
    added_mid_key = next(s["client_step_key"] for s in scoped_cand["steps"] if s["title"] == "Chèn Giữa")
    assert step4_key not in scope_keys_after
    assert {branch_2a_key, branch_2b_key, added_mid_key} <= scope_keys_after

    del_resp = await _patch(
        client,
        f"/candidates/{scoped_cand['candidate_id']}",
        "scope-del",
        {
            "expected_review_revision": scoped_cand["review_revision"],
            "edits": {"delete_step_keys": [scoped_cand["steps"][0]["client_step_key"]]},
        },
    )
    assert del_resp.status_code == 200, del_resp.text
    scope_after_del = set(
        del_resp.json()["data"]["feasibility"]["verification_scope"]["client_step_keys"]
    )
    assert step4_key not in scope_after_del
    assert {branch_2a_key, branch_2b_key, added_mid_key} <= scope_after_del


async def test_expand_next_horizon_branch2_past_day14_and_partial_window_overflow(
    api, monkeypatch
) -> None:
    """Branch 2 của expand_next_horizon tịnh tiến qua Ngày 14 và dừng đúng thứ tự khi tràn cửa sổ 14 ngày."""
    client, factory = api
    base = datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=2)
    summary_45d = {
        "title": "Đề án 45 ngày",
        "description": "Kiểm tra Branch 2 Rolling Horizon",
        "scope": "Phạm vi mở rộng",
        "time_constraint": {
            "kind": "deadline",
            "start_at": base.isoformat(),
            "deadline_at": (base + timedelta(days=45)).isoformat(),
            "input_timezone": "UTC",
        },
        "availability": {
            "timezone": "UTC",
            "weekly_rules": [
                {"weekday": wd, "start_local": "08:00", "end_local": "08:25"}
                for wd in range(1, 8)
            ],
        },
    }

    # 1) Task đã có phiên đến ngày 15 (vượt mốc 14 ngày từ base) -> Branch 2 phải bắt đầu sau phiên cuối + 15p
    draft_d14 = await _confirmed_draft(client, "b2-d14-draft", summary=summary_45d)
    cand_d14 = (
        await _create_candidate(
            client,
            draft_d14,
            "b2-d14-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước Nền 15 Ngày",
                    "expected_output": "Đầu ra 15 ngày",
                    "estimated_base_seconds": 18750,
                }
            ],
            unexpanded_scope="Phần AI 2A; Phần AI 2B",
            planning_horizon={
                "start_at": base.isoformat(),
                "end_at": (base + timedelta(days=16)).isoformat(),
            },
        )
    ).json()["data"]
    conf_d14 = (await _confirm_candidate(client, cand_d14, "b2-d14-conf")).json()["data"]
    task1_id = conf_d14["task"]["task_id"]

    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        revs1, _ = await reschedule_service._compute_db_source_revisions(
            db, owner, target_task_id=task1_id
        )
        sched_rev1 = await reschedule_service._get_current_schedule_revision(db, owner)
        prop_d14 = await reschedule_service.create_proposal(
            db,
            user_id="user_a",
            idempotency_key="b2-d14-prop",
            payload=CreateProposalRequest(
                trigger_reason="expand_next_horizon",
                target_task_id=task1_id,
                allowed_task_ids=[task1_id],
                expected_base_schedule_revision=sched_rev1,
                expected_source_revisions=ProposalSourceRevisions.model_validate(revs1.model_dump()),
            ),
            now=base + timedelta(hours=1),
        )
    assert prop_d14.feasibility.status == "feasible"
    assert prop_d14.proposed_changes.next_horizon_from is not None
    assert prop_d14.proposed_changes.next_horizon_to is not None
    assert prop_d14.proposed_changes.next_horizon_from >= base + timedelta(days=14, hours=1, minutes=40)
    assert prop_d14.proposed_changes.next_horizon_to >= base + timedelta(days=28)
    assert len(prop_d14.proposed_changes.sessions_to_add) >= 2
    assert all(
        item.after.start >= prop_d14.proposed_changes.next_horizon_from
        for item in prop_d14.proposed_changes.sessions_to_add
    )

    # 2) Cửa sổ 14 ngày không đủ chứa bước nặng thứ 2 trước deadline -> dừng đúng thứ tự, status=feasible
    summary_ovf = {
        **summary_45d,
        "availability": {
            "timezone": "UTC",
            "weekly_rules": [
                {"weekday": wd, "start_local": "10:00", "end_local": "10:25"}
                for wd in range(1, 8)
            ],
        },
    }
    draft_ovf = await _confirmed_draft(client, "b2-ovf-draft", summary=summary_ovf)
    cand_ovf = (
        await _create_candidate(
            client,
            draft_ovf,
            "b2-ovf-cand",
            manual_steps=[
                {
                    "order_index": 0,
                    "title": "Bước Gốc Nhỏ",
                    "expected_output": "Đầu ra Gốc",
                    "estimated_base_seconds": 1200,
                }
            ],
            unexpanded_scope="Mở đầu; Việc nặng; Việc cuối",
        )
    ).json()["data"]
    conf_ovf = (await _confirm_candidate(client, cand_ovf, "b2-ovf-conf")).json()["data"]
    task2_id = conf_ovf["task"]["task_id"]

    async def _mock_overflow_decompose(**kwargs):
        return TaskDecompositionResult(
            task_title="Đề án 45 ngày",
            steps=[
                StepPromptItem(
                    order_index=1,
                    title="Khởi động ngắn",
                    expected_output="Đầu ra khởi động",
                    estimated_minutes=20,
                    micro_task_prompt="Bắt đầu bước ngắn",
                ),
                StepPromptItem(
                    order_index=2,
                    title="Chặng cực nặng",
                    expected_output="Đầu ra nặng",
                    estimated_minutes=360,
                    micro_task_prompt="Bắt đầu chặng nặng",
                ),
                StepPromptItem(
                    order_index=3,
                    title="Việc nhẹ sau đó",
                    expected_output="Đầu ra nhẹ sau",
                    estimated_minutes=20,
                    micro_task_prompt="Bắt đầu việc nhẹ",
                ),
            ],
            unexpanded_scope="Phần 4 chưa chia",
            reasoning="Chia 3 bước để kiểm tra tràn cửa sổ.",
            generation_tier="tier_1_gemini",
        )

    monkeypatch.setattr(ai_service, "decompose_task", _mock_overflow_decompose)
    async with factory() as db:
        owner = resolve_user_uuid("user_a")
        revs2, _ = await reschedule_service._compute_db_source_revisions(
            db, owner, target_task_id=task2_id
        )
        sched_rev2 = await reschedule_service._get_current_schedule_revision(db, owner)
        prop_ovf = await reschedule_service.create_proposal(
            db,
            user_id="user_a",
            idempotency_key="b2-ovf-prop",
            payload=CreateProposalRequest(
                trigger_reason="expand_next_horizon",
                target_task_id=task2_id,
                allowed_task_ids=[task2_id],
                expected_base_schedule_revision=sched_rev2,
                expected_source_revisions=ProposalSourceRevisions.model_validate(revs2.model_dump()),
            ),
            now=base + timedelta(hours=1),
        )

    assert prop_ovf.feasibility.status == "feasible"
    assert prop_ovf.feasibility.confirmable is True
    assert prop_ovf.feasibility.deficit_seconds == 0
    assert [s.title for s in prop_ovf.proposed_changes.steps_to_add] == ["Khởi động ngắn"]
    assert prop_ovf.feasibility.next_horizon_preview is not None
    assert prop_ovf.feasibility.next_horizon_preview["expanded_steps_count"] == 1
    assert (
        prop_ovf.feasibility.next_horizon_preview["unexpanded_scope"]
        == "Chặng cực nặng; Việc nhẹ sau đó; Phần 4 chưa chia"
    )
