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
from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models import Base
from app.models.user import UserPreference as PreferenceRow
from app.schemas.settings import UserPreference
from app.schemas.user import CurrentUser
from app.services import draft_service
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
                    self.assertIsNotNone(pref)
                    self.assertEqual(pref.work_interval_seconds, 900)
                    self.assertEqual(pref.buffer_multiplier, 1.4)
                    self.assertEqual(pref.effective_revision, 2)
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous_overrides)
                await engine.dispose()

        asyncio.run(run())

    @pytest.mark.xfail(
        strict=True,
        raises=ValidationError,
        reason="Giới hạn hiện hành: UserPreference chỉ cho một mốc reminder; hỗ trợ nhiều mốc chưa triển khai.",
    )
    def test_gap_reminder_offsets_multi_memos_blocked(self):
        """Mong muốn hai mốc, đơn vị giây; không bắt ValidationError rồi ép fail."""
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

    @pytest.mark.xfail(
        strict=True,
        raises=MissingEndpoint,
        reason="DELETE /drafts/{id} chưa triển khai (HTTP 405).",
    )
    def test_gap_missing_draft_deletion_api(self):
        with TestClient(app) as client:
            response = client.delete("/api/v1/drafts/draft_sample_123")
        if response.status_code == 405 and not endpoint_registered(
            "DELETE", "/api/v1/drafts/draft_sample_123"
        ):
            raise MissingEndpoint("DELETE Draft trả 405: method chưa có")
        self.assertIn(response.status_code, (200, 204), response.text)

    @pytest.mark.xfail(
        strict=True,
        raises=MissingEndpoint,
        reason="POST /sessions/{id}/start chưa triển khai (HTTP 404). Ngoài phạm vi UF1–UF2.",
    )
    def test_gap_missing_session_execution_apis(self):
        with TestClient(app) as client:
            response = client.post("/api/v1/sessions/sess_123/start")
        if response.status_code == 404 and not endpoint_registered(
            "POST", "/api/v1/sessions/sess_123/start"
        ):
            raise MissingEndpoint("Route start session chưa có")
        self.assertIn(response.status_code, (200, 201), response.text)

    @pytest.mark.xfail(
        strict=True,
        raises=MissingEndpoint,
        reason="POST /thoughts chưa triển khai (HTTP 404). Ngoài phạm vi UF1–UF2.",
    )
    def test_gap_missing_thoughts_inbox_api(self):
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/thoughts", json={"content": "Nhớ gọi điện", "source": "text"}
            )
        if response.status_code == 404 and not endpoint_registered("POST", "/api/v1/thoughts"):
            raise MissingEndpoint("Route Thought capture chưa có")
        self.assertIn(response.status_code, (200, 201), response.text)

    @pytest.mark.skip(
        reason="Ca isinstance(dict) cũ không chứng minh mất idempotency. Được thay bằng regression receipt lưu DB/reopen/retry ở suite tích hợp mới."
    )
    def test_gap_in_memory_idempotency_dict_vulnerability(self):
        """Không báo lỗ hổng persistence chỉ vì vẫn còn cache dict trong module."""
