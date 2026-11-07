"""Đối chiếu feedback với contract UF1–UF2 hiện hành.

Các yêu cầu chưa có hợp đồng thực thi được ghi SKIP với lý do rõ; không dùng
xfail bao trùm để biến lỗi fixture/schema thành bằng chứng thiếu tính năng.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.models import Base, Task
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.common import TimeConstraint
from app.schemas.draft import ConfirmSummaryRequest, CreateDraftRequest, SummaryInput
from app.schemas.planning import ConfirmCandidateRequest, GenerateCandidateRequest, ManualStepInput
from app.services import draft_service, plan_service


class TestRequirementGapsAndADHDScenarios(unittest.TestCase):
    async def _async_setup_db(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await draft_service.ensure_dev_seed_in_db(db)
        return engine, factory

    def test_gap_1_draft_candidate_when_missing_deadline(self):
        """Thiếu deadline: giữ Draft và câu hỏi; contract hiện hành chưa cho sang UF2."""

        async def run():
            engine, factory = await self._async_setup_db()
            try:
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a",
                        idempotency_key="missing_time_create",
                        payload=CreateDraftRequest(
                            client_draft_id="missing_time_draft",
                            input_source="text",
                            raw_input="Viết bài luận Triết học 15 trang",
                            summary=SummaryInput(
                                title="Bài luận", description="Viết bài luận", scope="15 trang"
                            ),
                        ),
                        db=db,
                    )
                self.assertFalse(draft.summary.can_plan)
                self.assertTrue(
                    any(
                        i.field_path == "time_constraint" and i.blocking
                        for i in draft.summary.clarification_issues
                    )
                )
                async with factory() as db:
                    with self.assertRaises(AppError) as ctx:
                        await draft_service.confirm_draft_summary(
                            user_id="user_a",
                            draft_id=draft.draft_id,
                            idempotency_key="missing_time_confirm",
                            payload=ConfirmSummaryRequest(
                                expected_summary_revision=draft.summary_revision
                            ),
                            db=db,
                        )
                    self.assertEqual(ctx.exception.status_code, 422)
                    self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
                async with factory() as db:
                    kept = await draft_service.get_draft_by_id(
                        user_id="user_a", draft_id=draft.draft_id, db=db
                    )
                    self.assertEqual(kept.stage, "collecting")
                    self.assertIsNone(kept.confirmed_task_id)
            finally:
                await engine.dispose()

        asyncio.run(run())

    @pytest.mark.skip(
        reason="Chưa chốt contract max_tasks/ngày; task_limits hiện hành là khoảng thời gian của từng Task."
    )
    def test_gap_2_task_limits_per_day_ignored_by_scheduler(self):
        """Đề xuất giới hạn số Task/ngày cần contract riêng trước khi viết test hành vi."""

    def test_gap_3_past_session_stale_candidate_rejection(self):
        """Candidate trễ bị từ chối an toàn, vẫn giữ để xem lại; chưa có auto-slide."""

        async def run():
            engine, factory = await self._async_setup_db()
            try:
                start = datetime.now(UTC).replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                availability = DraftAvailability(
                    timezone="UTC",
                    weekly_rules=[
                        WeeklyRule(weekday=w, start_local="00:00", end_local="23:59")
                        for w in range(1, 8)
                    ],
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a",
                        idempotency_key="stale_draft_create",
                        payload=CreateDraftRequest(
                            client_draft_id="stale_draft",
                            input_source="text",
                            raw_input="Viết báo cáo",
                            summary=SummaryInput(
                                title="Báo cáo",
                                description="Viết báo cáo",
                                scope="1 trang",
                                time_constraint=TimeConstraint(
                                    kind="deadline",
                                    start_at=start,
                                    deadline_at=start + timedelta(days=2),
                                    input_timezone="UTC",
                                ),
                                availability=availability,
                            ),
                        ),
                        db=db,
                    )
                async with factory() as db:
                    await draft_service.confirm_draft_summary(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="stale_draft_confirm",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=draft.summary_revision
                        ),
                        db=db,
                    )
                async with factory() as db:
                    candidate = await plan_service.create_candidate(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="stale_candidate_create",
                        payload=GenerateCandidateRequest(
                            source_summary_revision=draft.summary_revision,
                            source_planning_input_revision=draft.planning_input_revision,
                            manual_steps=[
                                ManualStepInput(
                                    order_index=0,
                                    title="Viết",
                                    expected_output="1 trang",
                                    estimated_base_seconds=1800,
                                )
                            ],
                        ),
                        db=db,
                    )
                self.assertEqual(candidate.feasibility.status, "feasible")
                confirm_at = candidate.sessions[0].scheduled_start + timedelta(minutes=3)

                class ConfirmClock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return confirm_at.astimezone(tz) if tz else confirm_at.replace(tzinfo=None)

                with patch.object(plan_service, "datetime", ConfirmClock):
                    async with factory() as db:
                        with self.assertRaises(AppError) as ctx:
                            await plan_service.confirm_candidate(
                                user_id="user_a",
                                candidate_id=candidate.candidate_id,
                                idempotency_key="stale_candidate_confirm",
                                payload=ConfirmCandidateRequest(
                                    expected_review_revision=candidate.review_revision,
                                    expected_summary_revision=candidate.source_summary_revision,
                                    expected_planning_input_revision=candidate.source_planning_input_revision,
                                    expected_source_revisions=candidate.source_revisions,
                                ),
                                db=db,
                            )
                        self.assertEqual(ctx.exception.code, "CANDIDATE_EXPIRED")
                async with factory() as db:
                    self.assertIsNone(await db.scalar(select(Task.id)))
                    kept = await plan_service.get_candidate_by_id(
                        user_id="user_a", candidate_id=candidate.candidate_id, db=db
                    )
                    self.assertEqual(kept.candidate_id, candidate.candidate_id)
            finally:
                await engine.dispose()

        asyncio.run(run())

    @pytest.mark.skip(
        reason="Chưa có contract đầu vào avoidance_count và đầu ra can thiệp; schedule_revision không phải số lần hoãn."
    )
    def test_gap_4_chronic_task_avoidance_escalation(self):
        """Cần contract/fixture đúng số lần hoãn trước khi kiểm cơ chế can thiệp."""

    @pytest.mark.skip(
        reason="Chưa có API/contract tiếp tục chặng Rolling Horizon; cần test chuyển chặng thực, không kiểm tên hàm."
    )
    def test_gap_5_rolling_horizon_continuation_missing(self):
        """Rolling continuation còn thiếu, không suy từ hasattr service."""

    @pytest.mark.skip(
        reason="Quy tắc ép nghỉ sau 90 phút chưa chốt; contract durations hiện hành cho phép rest_interval_seconds=0."
    )
    def test_gap_6_zero_rest_burnout_prevention(self):
        """Đề xuất ép nghỉ là quyết định nghiệp vụ, chưa phải lỗi hợp đồng hiện hành."""
