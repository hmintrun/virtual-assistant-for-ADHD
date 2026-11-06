"""Kiểm đường PATCH UF1 sau khi ghép bản sửa Mobile và Backend."""

from uuid import uuid4

import httpx
import pytest

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.security import SEED_USER_A
from app.main import app
from app.models import Base


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_availability", [False, True])
async def test_patch_nested_summary_preserves_models_and_clears_legacy_source(clear_availability):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    sessions = session_factory(engine)

    async def test_db():
        async with sessions() as db:
            yield db

    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_current_user] = lambda: SEED_USER_A
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://baseline.test"
        ) as client:
            # Mobile khôi phục danh sách nháp khi khởi động, đồng thời bootstrap user dev.
            listed = await client.get("/api/v1/drafts")
            assert listed.status_code == 200, listed.text
            created = await client.post(
                "/api/v1/drafts",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "client_draft_id": str(uuid4()),
                    "input_source": "text",
                    "summary": {
                        "title": "Baseline draft",
                        "description": "Write a document for the course project",
                        "scope": "One document",
                        "availability_ref": {"availability_id": "availability_1", "revision": 2},
                    },
                },
            )
            assert created.status_code == 201, created.text
            draft = created.json()["data"]
            time_constraint = {
                "kind": "deadline",
                "start_at": "2099-11-03T08:00:00+07:00",
                "deadline_at": "2099-11-10T18:00:00+07:00",
                "input_timezone": "Asia/Ho_Chi_Minh",
            }
            availability = (
                None
                if clear_availability
                else {
                    "timezone": "Asia/Ho_Chi_Minh",
                    "weekly_rules": [
                        {"weekday": day, "start_local": "08:00", "end_local": "18:00"}
                        for day in range(1, 8)
                    ],
                    "date_overrides": [],
                }
            )
            headers = {"Idempotency-Key": str(uuid4())}
            payload = {
                "expected_summary_revision": draft["summary_revision"],
                "expected_planning_input_revision": draft["planning_input_revision"],
                "changes": {
                    "summary": {
                        "time_constraint": time_constraint,
                        "availability": availability,
                    }
                },
            }
            url = f"/api/v1/drafts/{draft['draft_id']}"
            patched = await client.patch(url, headers=headers, json=payload)
            assert patched.status_code == 200, patched.text
            result = patched.json()["data"]
            assert result["summary"]["availability_ref"] is None
            assert result["summary"]["time_constraint"]["kind"] == "deadline"
            assert result["summary"]["can_plan"] is not clear_availability, result["summary"][
                "clarification_issues"
            ]
            assert (result["summary"]["availability"] is None) is clear_availability
            assert result["summary_revision"] == draft["summary_revision"] + 1

            # Receipt và đọc qua session mới phải giữ cùng nguồn/revision.
            replay = await client.patch(url, headers=headers, json=payload)
            assert replay.status_code == 200, replay.text
            reloaded = await client.get(url)
            assert reloaded.status_code == 200, reloaded.text
            for response in (replay, reloaded):
                assert response.json()["data"] == result
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        await engine.dispose()
