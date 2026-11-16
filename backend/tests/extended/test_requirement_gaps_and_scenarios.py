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

    def test_gap_5_rolling_horizon_continuation_missing(self):
        """I03 & I07: Mở rộng chặng Rolling Horizon qua UF6 expand_next_horizon và cập nhật pending_notification_state."""
        from app.schemas.reschedule import (
            ApproveProposalRequest,
            CreateProposalRequest,
            ProposalSourceRevisions,
        )
        from app.services import reschedule_service

        async def run():
            engine, factory = await self._async_setup_db()
            try:
                now = datetime.now(UTC).replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                availability = DraftAvailability(
                    timezone="UTC",
                    weekly_rules=[
                        WeeklyRule(weekday=w, start_local="08:00", end_local="21:00")
                        for w in range(1, 8)
                    ],
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a",
                        idempotency_key="gap5_draft_create",
                        payload=CreateDraftRequest(
                            client_draft_id="gap5_draft",
                            input_source="text",
                            raw_input="Dự án nghiên cứu Rolling Horizon",
                            summary=SummaryInput(
                                title="Dự án nghiên cứu",
                                description="Thực hiện nghiên cứu 3 giai đoạn",
                                scope="3 giai đoạn",
                                time_constraint=TimeConstraint(
                                    kind="deadline",
                                    start_at=now,
                                    deadline_at=now + timedelta(days=28),
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
                        idempotency_key="gap5_draft_confirm",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=draft.summary_revision
                        ),
                        db=db,
                    )
                async with factory() as db:
                    candidate = await plan_service.create_candidate(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="gap5_cand_create",
                        payload=GenerateCandidateRequest(
                            source_summary_revision=draft.summary_revision,
                            source_planning_input_revision=draft.planning_input_revision,
                            manual_steps=[
                                ManualStepInput(
                                    order_index=0,
                                    title="Giai đoạn 1: Khảo sát",
                                    expected_output="Báo cáo khảo sát",
                                    estimated_base_seconds=1250,
                                )
                            ],
                            unexpanded_scope="Giai đoạn 2: Thử nghiệm mô hình\nGiai đoạn 3: Viết tổng kết",
                        ),
                        db=db,
                    )
                async with factory() as db:
                    confirmed = await plan_service.confirm_candidate(
                        user_id="user_a",
                        candidate_id=candidate.candidate_id,
                        idempotency_key="gap5_cand_confirm",
                        payload=ConfirmCandidateRequest(
                            expected_review_revision=candidate.review_revision,
                            expected_summary_revision=candidate.source_summary_revision,
                            expected_planning_input_revision=candidate.source_planning_input_revision,
                            expected_source_revisions=candidate.source_revisions,
                        ),
                        db=db,
                    )
                task_id = confirmed.task.task_id
                self.assertEqual(confirmed.task.decomposition_state, "partial")

                async with factory() as db:
                    owner = draft_service.resolve_user_uuid("user_a")
                    revs, _ = await reschedule_service._compute_db_source_revisions(
                        db, owner, target_task_id=task_id
                    )
                    sched_rev = await reschedule_service._get_current_schedule_revision(db, owner)
                    proposal = await reschedule_service.create_proposal(
                        db,
                        user_id="user_a",
                        idempotency_key="gap5_prop_create",
                        payload=CreateProposalRequest(
                            trigger_reason="expand_next_horizon",
                            target_task_id=task_id,
                            allowed_task_ids=[task_id],
                            expected_base_schedule_revision=sched_rev,
                            expected_source_revisions=ProposalSourceRevisions.model_validate(
                                revs.model_dump()
                            ),
                        ),
                        now=now + timedelta(days=1),
                    )
                # I07: Khi mới tạo, pending_notification_state == "pending" và chưa có sent_at
                self.assertEqual(proposal.pending_notification_state, "pending")
                self.assertIsNone(proposal.pending_notification_sent_at)
                self.assertEqual(len(proposal.proposed_changes.steps_to_add), 2)
                self.assertGreaterEqual(len(proposal.proposed_changes.sessions_to_add), 2)

                # Khi đọc đề xuất hiện tại (GET /proposals/current), trạng thái chuyển sang "sent"
                read_time = now + timedelta(days=1, seconds=30)
                async with factory() as db:
                    current_prop = await reschedule_service.get_current_proposal(
                        db, user_id="user_a", now=read_time
                    )
                assert current_prop is not None
                self.assertEqual(current_prop.pending_notification_state, "sent")
                self.assertEqual(current_prop.pending_notification_sent_at, read_time)

                # Duyệt đề xuất mở rộng chặng tiếp theo
                async with factory() as db:
                    approved = await reschedule_service.approve_proposal(
                        db,
                        user_id="user_a",
                        proposal_id=proposal.proposal_id,
                        idempotency_key="gap5_prop_approve",
                        payload=ApproveProposalRequest(
                            expected_proposal_revision=proposal.proposal_revision,
                            expected_base_schedule_revision=proposal.base_schedule_revision,
                            expected_source_revisions=proposal.source_revisions,
                        ),
                        now=now + timedelta(days=1, minutes=2),
                    )
                self.assertEqual(approved.status, "approved")

                # Kiểm tra Task đã chuyển sang decomposition_state="complete" và xóa unexpanded_scope
                async with factory() as db:
                    updated_task = await plan_service.get_task_by_id(
                        user_id="user_a", task_id=task_id, db=db
                    )
                self.assertEqual(updated_task.decomposition_state, "complete")
                self.assertIsNone(updated_task.unexpanded_scope)
                self.assertEqual(len(updated_task.steps), 3)

                # Gọi lại expand_next_horizon khi đã hết unexpanded_scope phải trả 409 INVALID_STATE_TRANSITION
                async with factory() as db:
                    owner = draft_service.resolve_user_uuid("user_a")
                    revs2, _ = await reschedule_service._compute_db_source_revisions(
                        db, owner, target_task_id=task_id
                    )
                    sched_rev2 = await reschedule_service._get_current_schedule_revision(db, owner)
                    with self.assertRaises(AppError) as ctx:
                        await reschedule_service.create_proposal(
                            db,
                            user_id="user_a",
                            idempotency_key="gap5_prop_repeat",
                            payload=CreateProposalRequest(
                                trigger_reason="expand_next_horizon",
                                target_task_id=task_id,
                                allowed_task_ids=[task_id],
                                expected_base_schedule_revision=sched_rev2,
                                expected_source_revisions=ProposalSourceRevisions.model_validate(
                                    revs2.model_dump()
                                ),
                            ),
                            now=now + timedelta(days=1, minutes=5),
                        )
                    self.assertEqual(ctx.exception.status_code, 409)
                    self.assertEqual(ctx.exception.code, "INVALID_STATE_TRANSITION")
            finally:
                await engine.dispose()

        asyncio.run(run())

    @pytest.mark.skip(
        reason="Quy tắc ép nghỉ sau 90 phút chưa chốt; contract durations hiện hành cho phép rest_interval_seconds=0."
    )
    def test_gap_6_zero_rest_burnout_prevention(self):
        """Đề xuất ép nghỉ là quyết định nghiệp vụ, chưa phải lỗi hợp đồng hiện hành."""
