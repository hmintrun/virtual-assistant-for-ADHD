"""W07: capture without AI, post-run STT and atomic Thought -> Draft lifecycle."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.api.v1 import voice
from app.core.errors import AppError
from app.models import CommandReceipt, Task, Thought, User
from app.models.planning import PlanningDraft
from app.schemas.thought import ThoughtData, ThoughtDeleteRequest, ThoughtToDraftRequest
from app.services import command_service, thought_service
from app.services.voice_service import SttError, SttErrorCode, SttResult
from tests import test_w05_execution_checkin as baseline

w05_env = baseline.w05_env
w05_client = baseline.w05_client
_start_run = baseline._start_run


async def capture(client, *, key=None, **changes):
    return await client.post(
        "/api/v1/thoughts",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={
            "client_thought_id": str(uuid4()),
            "source_run_id": None,
            "input_type": "audio",
            "capture_state": "saved",
            "attempt_count": 1,
            "text_content": None,
            **changes,
        },
    )


async def convert(client, row, *, key=None, revision=None, text="Viết báo cáo chương 2"):
    return await client.post(
        f"/api/v1/thoughts/{row['thought_id']}/draft",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={
            "expected_thought_revision": revision or row["thought_revision"],
            "text_content": text,
        },
    )


async def delete(client, row, *, key=None, revision=None):
    return await client.request(
        "DELETE",
        f"/api/v1/thoughts/{row['thought_id']}",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={"expected_thought_revision": revision or row["thought_revision"]},
    )


async def end_run(client, env, run):
    res = await client.post(
        f"/api/v1/runs/{run['run_id']}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": run["device_id"],
            "expected_run_revision": run["run_revision"],
            "event_id": str(uuid4()),
            "ended_at": (env["scheduled_start"] + timedelta(seconds=30)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 30,
            "rest_elapsed_seconds": 0,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["run_state"] == "awaiting_checkin"
    return res.json()["data"]


async def transcribe(client, thought_id, key):
    return await client.post(
        "/api/v1/voice/transcriptions",
        headers={"Idempotency-Key": key},
        data={
            "purpose": "thought_to_draft",
            "device_id": "android_pixel_8",
            "thought_id": thought_id,
        },
        files={"file": ("thought.wav", b"RIFF-test-audio", "audio/wav")},
    )


async def test_capture_retry_dedupe_preserves_run_and_does_not_call_ai(
    w05_client, w05_env, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Capture must not call STT/LLM")

    monkeypatch.setattr(voice, "get_voice_service", forbidden)
    from app.services import ai_service

    for method in (
        "extract_task_intent",
        "decompose_task",
        "adjust_candidate_by_nl",
        "adjust_plan_by_nl",
        "generate_reschedule_empathy",
    ):
        monkeypatch.setattr(ai_service.AIService, method, forbidden)
    monkeypatch.setattr("app.services.voice_service.get_voice_service", forbidden)
    run = await _start_run(w05_client, w05_env)
    client_id, key = str(uuid4()), str(uuid4())
    first = await capture(
        w05_client,
        source_run_id=run["run_id"],
        client_thought_id=client_id,
        key=key,
        capture_state="failed",
    )
    assert first.status_code == 201, first.text
    row = first.json()["data"]
    saved = await capture(
        w05_client, source_run_id=run["run_id"], client_thought_id=client_id, attempt_count=3
    )
    assert saved.status_code == 201
    assert saved.json()["data"]["thought_id"] == row["thought_id"]
    assert saved.json()["data"]["thought_revision"] == 2
    late = await capture(
        w05_client, source_run_id=run["run_id"], client_thought_id=client_id, capture_state="failed"
    )
    assert late.json()["data"]["capture_state"] == "saved"
    assert late.json()["data"]["attempt_count"] == 3
    await w05_env["engine"].dispose()
    replay = await capture(
        w05_client,
        source_run_id=run["run_id"],
        client_thought_id=client_id,
        key=key,
        capture_state="failed",
    )
    assert replay.json()["data"] == row
    detail = await w05_client.get(f"/api/v1/runs/{run['run_id']}")
    assert detail.json()["data"]["run_revision"] == run["run_revision"]
    assert detail.json()["data"]["run_state"] == "running"
    conflict = await capture(w05_client, client_thought_id=client_id)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CLIENT_THOUGHT_CONFLICT"
    async with w05_env["sessions"]() as db:
        assert await db.scalar(select(func.count()).select_from(Thought)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"input_type": "text", "text_content": "   "},
        {"capture_state": "invented"},
        {"attempt_count": 4},
        {"source_run_id": "invalid"},
        {"source_run_id": str(uuid4())},
    ],
)
async def test_capture_validation(w05_client, changes):
    res = await capture(w05_client, **changes)
    assert res.status_code in {404, 422}


@pytest.mark.parametrize("paused", [False, True])
async def test_post_run_stt_and_conversion_replay_after_restart(
    w05_client, w05_env, monkeypatch, paused
):
    class Provider:
        calls = 0

        async def transcribe(self, audio_bytes, **kwargs):
            self.calls += 1
            return SttResult(text="Viết báo cáo chương 2", provider="test", latency_ms=1)

    provider = Provider()
    monkeypatch.setattr(voice, "get_voice_service", lambda: provider)
    run = await _start_run(w05_client, w05_env)
    if paused:
        from app.models import SessionRun

        async with w05_env["sessions"]() as db:
            source = await db.get(SessionRun, UUID(run["run_id"]))
            source.run_state = "paused"
            await db.commit()
            run["run_revision"] = source.revision
    captured = await capture(w05_client, source_run_id=run["run_id"])
    assert captured.status_code == 201, captured.text
    row = captured.json()["data"]
    blocked = await transcribe(w05_client, row["thought_id"], str(uuid4()))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SESSION_STILL_RUNNING"
    assert (await convert(w05_client, row)).status_code == 409
    assert provider.calls == 0
    await end_run(w05_client, w05_env, run)
    stt_key, draft_key = str(uuid4()), str(uuid4())
    stt = await transcribe(w05_client, row["thought_id"], stt_key)
    assert stt.status_code == 200, stt.text
    assert provider.calls == 1
    created = await convert(w05_client, row, key=draft_key, text=stt.json()["data"]["text"])
    assert created.status_code == 201, created.text
    result = created.json()["data"]
    assert result["converted_task_id"] is None
    assert result["draft"]["input_source"] == "thought"
    assert result["draft"]["source_thought_id"] == row["thought_id"]
    assert result["draft"]["summary"]["can_plan"] is False
    await w05_env["engine"].dispose()
    voice._VOICE_IDEMPOTENCY_STORE.clear()
    replay_stt = await transcribe(w05_client, row["thought_id"], stt_key)
    assert replay_stt.status_code == 200 and replay_stt.json()["data"] == stt.json()["data"]
    assert provider.calls == 1
    replay_draft = await convert(w05_client, row, key=draft_key)
    assert replay_draft.status_code == 201 and replay_draft.json()["data"] == result
    second_key = await convert(w05_client, row)
    assert second_key.status_code == 409
    assert second_key.json()["error"]["code"] == "THOUGHT_ALREADY_LINKED"
    detail = await w05_client.get(f"/api/v1/thoughts/{row['thought_id']}")
    assert detail.json()["data"]["thought_revision"] == 2
    assert detail.json()["data"]["linked_draft_id"] == result["linked_draft_id"]
    assert (await delete(w05_client, row, revision=2)).status_code == 409
    async with w05_env["sessions"]() as db:
        assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 1
        assert await db.scalar(select(func.count()).select_from(Task)) == 1  # only seeded Task
        receipt = await db.scalar(
            select(CommandReceipt).where(CommandReceipt.command_id == draft_key)
        )
        assert receipt.result_reference["thought_id"] == row["thought_id"]
        assert receipt.result_reference["draft_id"] == result["linked_draft_id"]


async def test_convert_concurrency_creates_only_one_draft(w05_client, w05_env):
    row = (await capture(w05_client, input_type="text", text_content="Ghi chú")).json()["data"]
    key = str(uuid4())
    same = await asyncio.gather(*(convert(w05_client, row, key=key) for _ in range(2)))
    assert [r.status_code for r in same] == [201, 201]
    assert same[0].json()["data"] == same[1].json()["data"]
    another = (await capture(w05_client)).json()["data"]
    competing = await asyncio.gather(*(convert(w05_client, another) for _ in range(2)))
    assert sorted(r.status_code for r in competing) == [201, 409]
    async with w05_env["sessions"]() as db:
        assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 2


async def test_convert_receipt_failure_rolls_back_draft_and_thought_revision(
    w05_client, w05_env, monkeypatch
):
    row = (await capture(w05_client)).json()["data"]
    original = command_service.record

    def fail_receipt(*args, **kwargs):
        raise RuntimeError("injected receipt failure")

    monkeypatch.setattr(command_service, "record", fail_receipt)
    async with w05_env["sessions"]() as db:
        with pytest.raises(RuntimeError, match="injected receipt"):
            await thought_service.convert_thought_to_draft(
                db,
                user_id=str(w05_env["user_id"]),
                thought_id=row["thought_id"],
                idempotency_key=str(uuid4()),
                payload=ThoughtToDraftRequest(expected_thought_revision=1, text_content="Ghi chú"),
            )
    monkeypatch.setattr(command_service, "record", original)
    async with w05_env["sessions"]() as db:
        assert await db.scalar(select(func.count()).select_from(PlanningDraft)) == 0
        assert (await db.get(Thought, UUID(row["thought_id"]))).revision == 1
    assert (await convert(w05_client, row)).status_code == 201


async def test_delete_revision_guard_and_durable_replay(w05_client, w05_env):
    row = (await capture(w05_client)).json()["data"]
    stale = await delete(w05_client, row, revision=2)
    assert stale.status_code == 409
    key = str(uuid4())
    removed = await delete(w05_client, row, key=key)
    assert removed.status_code == 200, removed.text
    assert removed.json()["data"]["local_cleanup_required"] is True
    await w05_env["engine"].dispose()
    replay = await delete(w05_client, row, key=key)
    assert replay.status_code == 200 and replay.json()["data"] == removed.json()["data"]
    assert (await w05_client.get(f"/api/v1/thoughts/{row['thought_id']}")).status_code == 404
    assert (await delete(w05_client, row)).status_code == 404


async def test_thought_ownership_guards(w05_client, w05_env):
    row = (await capture(w05_client)).json()["data"]
    async with w05_env["sessions"]() as db:
        user = User(email="foreign-w07@example.test", display_name="Other")
        db.add(user)
        await db.commit()
        foreign_id = str(user.id)
    for action in ("get", "delete", "convert"):
        async with w05_env["sessions"]() as db:
            with pytest.raises(AppError) as err:
                if action == "get":
                    await thought_service.get_thought_detail(
                        db, user_id=foreign_id, thought_id=row["thought_id"]
                    )
                elif action == "delete":
                    await thought_service.delete_thought(
                        db,
                        user_id=foreign_id,
                        thought_id=row["thought_id"],
                        idempotency_key=str(uuid4()),
                        payload=ThoughtDeleteRequest(expected_thought_revision=1),
                    )
                else:
                    await thought_service.convert_thought_to_draft(
                        db,
                        user_id=foreign_id,
                        thought_id=row["thought_id"],
                        idempotency_key=str(uuid4()),
                        payload=ThoughtToDraftRequest(
                            expected_thought_revision=1, text_content="Ghi chú"
                        ),
                    )
            assert err.value.status_code == 404
    async with w05_env["sessions"]() as db:
        assert (await thought_service.list_thoughts(db, user_id=foreign_id)).items == []


async def test_list_batch_query_count_pagination_and_links(w05_client, w05_env):
    ids = []
    for _ in range(23):
        row = (await capture(w05_client)).json()["data"]
        ids.append(row["thought_id"])
    linked = await convert(w05_client, {"thought_id": ids[-1], "thought_revision": 1})
    assert linked.status_code == 201
    reads = []

    def record_select(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement)

    event.listen(w05_env["engine"].sync_engine, "before_cursor_execute", record_select)
    try:
        for limit in (1, 10, 20):
            reads.clear()
            async with w05_env["sessions"]() as db:
                page = await thought_service.list_thoughts(
                    db, user_id=str(w05_env["user_id"]), limit=limit
                )
                assert len(page.items) == limit
            assert len(reads) == 2
    finally:
        event.remove(w05_env["engine"].sync_engine, "before_cursor_execute", record_select)
    seen, cursor, linked_ids = [], None, []
    while True:
        res = await w05_client.get(
            "/api/v1/thoughts", params={"limit": 7, **({"cursor": cursor} if cursor else {})}
        )
        assert res.status_code == 200, res.text
        page = res.json()["data"]
        seen.extend(r["thought_id"] for r in page["items"])
        linked_ids.extend(r["linked_draft_id"] for r in page["items"] if r["linked_draft_id"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 23
    assert set(seen) == set(ids)
    assert linked_ids == [linked.json()["data"]["linked_draft_id"]]
    assert (await w05_client.get("/api/v1/thoughts", params={"cursor": "bad"})).status_code == 422


async def test_stt_failure_leaves_thought_retryable(w05_client, w05_env, monkeypatch):
    class Provider:
        async def transcribe(self, audio_bytes, **kwargs):
            raise SttError(SttErrorCode.PROVIDER_UNAVAILABLE)

    monkeypatch.setattr(voice, "get_voice_service", Provider)
    row = (await capture(w05_client)).json()["data"]
    res = await transcribe(w05_client, row["thought_id"], str(uuid4()))
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "STT_UNAVAILABLE"
    detail = await w05_client.get(f"/api/v1/thoughts/{row['thought_id']}")
    assert detail.json()["data"]["thought_revision"] == 1
    assert detail.json()["data"]["linked_draft_id"] is None
    assert (await convert(w05_client, row, text="Gõ nội dung thay STT")).status_code == 201


async def test_capture_concurrent_same_client_identity_and_text_fallback(w05_client, w05_env):
    client_id = str(uuid4())
    results = await asyncio.gather(
        *(capture(w05_client, client_thought_id=client_id) for _ in range(2))
    )
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json()["data"]["thought_id"] == results[1].json()["data"]["thought_id"]
    fallback = await capture(
        w05_client, input_type="text", text_content="Nhập nhanh sau ba lần lỗi", attempt_count=3
    )
    assert fallback.status_code == 201
    assert (await delete(w05_client, fallback.json()["data"])).json()["data"][
        "local_cleanup_required"
    ] is False


async def test_thought_link_reflects_task_only_after_candidate_confirm(w05_client, w05_env):
    from datetime import UTC, datetime

    # W05 seed sao chép segments dài vào Session 2; chuẩn hóa dữ liệu mồi cho UF2 DTO.
    from app.models import Session

    async with w05_env["sessions"]() as db:
        session = await db.get(Session, w05_env["session2_id"])
        session.segments = [
            {"order_index": 0, "kind": "work", "duration_seconds": 1500},
            {"order_index": 1, "kind": "rest", "duration_seconds": 300},
        ]
        await db.commit()

    row = (await capture(w05_client, input_type="text", text_content="Viết tài liệu")).json()[
        "data"
    ]
    created = await convert(w05_client, row)
    assert created.status_code == 201
    draft = created.json()["data"]["draft"]
    start = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    patched = await w05_client.patch(
        f"/api/v1/drafts/{draft['draft_id']}",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_summary_revision": draft["summary_revision"],
            "expected_planning_input_revision": draft["planning_input_revision"],
            "changes": {
                "summary": {
                    "title": "Viết tài liệu",
                    "description": "Viết tài liệu bàn giao",
                    "scope": "Một tài liệu",
                    "time_constraint": {
                        "kind": "deadline",
                        "start_at": start.isoformat(),
                        "deadline_at": (start + timedelta(days=3)).isoformat(),
                        "input_timezone": "UTC",
                    },
                    "availability": {
                        "timezone": "UTC",
                        "weekly_rules": [
                            {"weekday": d, "start_local": "08:00", "end_local": "18:00"}
                            for d in range(1, 8)
                        ],
                        "date_overrides": [],
                    },
                }
            },
        },
    )
    assert patched.status_code == 200, patched.text
    draft = patched.json()["data"]
    summary = await w05_client.post(
        f"/api/v1/drafts/{draft['draft_id']}/confirm-summary",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_summary_revision": draft["summary_revision"]},
    )
    assert summary.status_code == 200, summary.text
    draft = summary.json()["data"]
    generated = await w05_client.post(
        f"/api/v1/drafts/{draft['draft_id']}/candidates",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "source_summary_revision": draft["summary_revision"],
            "source_planning_input_revision": draft["planning_input_revision"],
            "manual_steps": [
                {
                    "order_index": 0,
                    "title": "Viết bản nháp",
                    "expected_output": "Một bản nháp",
                    "estimated_base_seconds": 1500,
                }
            ],
        },
    )
    assert generated.status_code == 201, generated.text
    candidate = generated.json()["data"]
    before = (await w05_client.get(f"/api/v1/thoughts/{row['thought_id']}")).json()["data"]
    assert before["linked_draft_id"] == draft["draft_id"] and before["converted_task_id"] is None
    confirmed = await w05_client.post(
        f"/api/v1/candidates/{candidate['candidate_id']}/confirm",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_review_revision": candidate["review_revision"],
            "expected_summary_revision": candidate["source_summary_revision"],
            "expected_planning_input_revision": candidate["source_planning_input_revision"],
            "expected_source_revisions": candidate["source_revisions"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    task_id = confirmed.json()["data"]["task"]["task_id"]
    detail = (await w05_client.get(f"/api/v1/thoughts/{row['thought_id']}")).json()["data"]
    assert detail["converted_task_id"] == task_id
    listing = (await w05_client.get("/api/v1/thoughts")).json()["data"]["items"]
    assert listing[0]["converted_task_id"] == task_id
    assert (await delete(w05_client, row, revision=detail["thought_revision"])).status_code == 409


async def test_thought_text_fallback_attempts_4_and_5_succeed_and_persist_in_db(
    w05_client, w05_env
):
    for attempt in (4, 5):
        res = await capture(
            w05_client,
            input_type="text",
            attempt_count=attempt,
            text_content=f"Suy nghĩ nhập tay lần {attempt}",
        )
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        assert data["attempt_count"] == attempt
        assert data["input_type"] == "text"
        async with w05_env["sessions"]() as db:
            saved = await db.scalar(select(Thought).where(Thought.id == UUID(data["thought_id"])))
            assert saved is not None
            assert saved.attempt_count == attempt


@pytest.mark.parametrize(
    "changes",
    [
        {"input_type": "audio", "attempt_count": 4, "text_content": None},
        {"input_type": "audio", "attempt_count": 1, "text_content": "Không được có text"},
        {"input_type": "text", "attempt_count": 1, "text_content": "Hợp lệ", "client_thought_id": "   "},
    ],
)
async def test_thought_validation_rejects_audio_over_3_zero_ai_and_blank_id(w05_client, changes):
    res = await capture(w05_client, **changes)
    assert res.status_code == 422, res.text


async def test_thought_data_preserves_legacy_whitespace_and_opaque_ids(w05_client):
    legacy = ThoughtData(
        thought_id=str(uuid4()),
        thought_revision=1,
        client_thought_id="   ",
        source_run_id=None,
        input_type="audio",
        capture_state="captured_offline",
        attempt_count=1,
        text_content=None,
    )
    assert legacy.client_thought_id == "   "

    opaque = " item "
    res = await capture(
        w05_client,
        client_thought_id=opaque,
        input_type="text",
        attempt_count=1,
        text_content="Ghi chú giữ nguyên ID",
    )
    assert res.status_code == 201, res.text
    assert res.json()["data"]["client_thought_id"] == opaque
