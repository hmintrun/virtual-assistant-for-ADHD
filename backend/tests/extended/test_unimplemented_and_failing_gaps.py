"""Feedback gaps đã đối chiếu với contract backend ngày 04/11.

Settings được kiểm qua API change-requests có DB tạm. Các endpoint chưa có
chỉ xfail đúng lỗi thiếu endpoint; lỗi auth/validation/server vẫn phải báo FAIL.
"""

from __future__ import annotations

import asyncio
import re
import unittest

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models import Base
from app.models.planning import PlanningDraft as PlanningDraftRow
from app.models.user import UserPreference as PreferenceRow
from app.schemas.settings import UserPreference
from app.schemas.user import CurrentUser
from app.services import draft_service


class MissingEndpoint(AssertionError):
    """Chỉ dùng cho mã HTTP xác định router hoặc method chưa được triển khai."""


def endpoint_registered(method, path):
    """Phân biệt route thiếu với HTTP 404 do ID không tồn tại."""
    return any(
        method.lower() in operations and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
        for template, operations in app.openapi()["paths"].items()
    )


class TestUnimplementedAndHiddenGaps(unittest.TestCase):
    def test_gap_missing_preferences_update_api(self):
        """Đổi cấu hình bằng API hiện hành, đọc lại DB và kiểm revision/idempotency."""

        async def run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            factory = async_sessionmaker(engine, expire_on_commit=False)
            previous_overrides = app.dependency_overrides.copy()
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                async with factory() as db:
                    await draft_service.ensure_dev_seed_in_db(db)

                async def test_db():
                    async with factory() as db:
                        yield db

                async def test_user():
                    return CurrentUser(user_id="user_a", is_active=True)

                app.dependency_overrides[get_db] = test_db
                app.dependency_overrides[get_current_user] = test_user
                body = {
                    "expected_preferences_revision": 1,
                    "changes": {
                        "preferences": {"work_interval_seconds": 900, "buffer_multiplier": 1.4}
                    },
                }
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    first = await client.post(
                        "/api/v1/settings/change-requests",
                        json=body,
                        headers={"Idempotency-Key": "settings_update_current_contract"},
                    )
                    self.assertEqual(first.status_code, 200, first.text)
                    replay = await client.post(
                        "/api/v1/settings/change-requests",
                        json=body,
                        headers={"Idempotency-Key": "settings_update_current_contract"},
                    )
                    self.assertEqual(replay.status_code, 200, replay.text)
                    self.assertEqual(first.json()["data"], replay.json()["data"])
                async with factory() as db:
                    pref = await db.scalar(
                        select(PreferenceRow).where(
                            PreferenceRow.user_id == draft_service.resolve_user_uuid("user_a")
                        )
                    )
                    assert pref is not None
                    self.assertEqual(pref.work_interval_seconds, 900)
                    self.assertEqual(pref.buffer_multiplier, 1.4)
                    self.assertEqual(pref.effective_revision, 2)
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous_overrides)
                await engine.dispose()

        asyncio.run(run())

    def test_gap_reminder_offsets_multi_memos_blocked(self):
        """Cấu hình nhiều mốc nhắc đã được triển khai; phải chạy như ca PASS."""
        pref = UserPreference(
            revision=1,
            effective_revision=1,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            buffer_multiplier=1.0,
            reminder_style="gentle",
            reminder_offsets=[1800, 300],
        )
        self.assertEqual(pref.reminder_offsets, [1800, 300])

    def test_gap_missing_draft_deletion_api(self):
        """Feedback: 'Không có API DELETE /api/v1/drafts/{id} để xoá vĩnh viễn các bản nháp rác.'"""

        async def run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            factory = async_sessionmaker(engine, expire_on_commit=False)
            previous_overrides = app.dependency_overrides.copy()
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                async with factory() as db:
                    await draft_service.ensure_dev_seed_in_db(db)

                async def test_db():
                    async with factory() as db:
                        yield db

                async def test_user():
                    return CurrentUser(user_id="user_a", is_active=True)

                app.dependency_overrides[get_db] = test_db
                app.dependency_overrides[get_current_user] = test_user

                self.assertTrue(endpoint_registered("DELETE", "/api/v1/drafts/draft_1"))
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    created = await client.post(
                        "/api/v1/drafts",
                        json={
                            "client_draft_id": "client_delete_draft_1",
                            "raw_input": "Bản nháp rác cần xóa",
                            "input_source": "text",
                        },
                        headers={"Idempotency-Key": "create_for_delete_1"},
                    )
                    self.assertEqual(created.status_code, 201, created.text)
                    draft_id = created.json()["data"]["draft_id"]

                    deleted = await client.delete(f"/api/v1/drafts/{draft_id}")
                    self.assertEqual(deleted.status_code, 200, deleted.text)
                    self.assertEqual(
                        deleted.json()["data"],
                        {"draft_id": draft_id, "deleted": True},
                    )

                    after_get = await client.get(f"/api/v1/drafts/{draft_id}")
                    self.assertEqual(after_get.status_code, 404, after_get.text)

                    missing_del = await client.delete(f"/api/v1/drafts/{draft_id}")
                    self.assertEqual(missing_del.status_code, 404, missing_del.text)

                    # Bản nháp đã chốt (stage = 'confirmed') không được xóa (409)
                    created_confirmed = await client.post(
                        "/api/v1/drafts",
                        json={
                            "client_draft_id": "client_delete_confirmed_1",
                            "raw_input": "Bản nháp đã chốt",
                            "input_source": "text",
                        },
                        headers={"Idempotency-Key": "create_for_delete_confirmed_1"},
                    )
                    self.assertEqual(created_confirmed.status_code, 201, created_confirmed.text)
                    conf_draft_id = created_confirmed.json()["data"]["draft_id"]
                    async with factory() as db:
                        row = await db.get(
                            PlanningDraftRow,
                            draft_service.resolve_draft_uuid(conf_draft_id),
                        )
                        assert row is not None
                        row.stage = "confirmed"
                        await db.commit()

                    conflict_del = await client.delete(f"/api/v1/drafts/{conf_draft_id}")
                    self.assertEqual(conflict_del.status_code, 409, conflict_del.text)
                    self.assertEqual(
                        conflict_del.json()["error"]["code"],
                        "INVALID_STATE_TRANSITION",
                    )
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous_overrides)
                await engine.dispose()

        asyncio.run(run())

    def test_session_execution_router_rejects_incomplete_request(self):
        """Router đã có: thiếu body/key phải trả validation, không coi là Run thành công."""
        with TestClient(app) as client:
            response = client.post("/api/v1/sessions/sess_123/start")
        self.assertTrue(endpoint_registered("POST", "/api/v1/sessions/sess_123/start"))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        fields = {item["field_path"] for item in response.json()["error"]["field_errors"]}
        self.assertIn("header.Idempotency-Key", fields)

    def test_thought_capture_router_rejects_legacy_payload(self):
        """Payload cũ không phải metadata Thought theo schema đã chốt."""
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/thoughts", json={"content": "Nhớ gọi điện", "source": "text"}
            )
        self.assertTrue(endpoint_registered("POST", "/api/v1/thoughts"))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        fields = {item["field_path"] for item in response.json()["error"]["field_errors"]}
        self.assertTrue(
            {"client_thought_id", "input_type", "capture_state", "attempt_count"} <= fields
        )

    @pytest.mark.skip(
        reason="Ca isinstance(dict) cũ không chứng minh mất idempotency. Được thay bằng regression receipt lưu DB/reopen/retry ở suite tích hợp mới."
    )
    def test_gap_in_memory_idempotency_dict_vulnerability(self):
        """Không báo lỗ hổng persistence chỉ vì vẫn còn cache dict trong module."""
