"""Bộ kiểm thử liên hoàn theo luồng người dùng (Offline Service Integration Pipeline Tests).

Nối các tác vụ từ UF1 -> UF2 -> UF6 thành quy trình khép kín:
1. Pipeline 1: Happy Path Full Chain (UF1 -> UF2 -> Schedule Execution)
   Nhập liệu đầy đủ -> Xác nhận tóm tắt -> Bẻ nhỏ việc & Lên lịch -> Kiểm tra khả thi -> Chốt lịch vào Database.
2. Pipeline 2: ADHD Clarification Loop (UF1 Vòng làm rõ tập trung)
   Nhập liệu mơ hồ -> Bắt thiếu trường -> Chặn chuyển bước -> Người dùng vá thông tin -> Xác nhận thành công -> Sang UF2.
3. Pipeline 3: Deficit & Natural Language Adjustment Loop (UF2 Thương lượng lịch)
   Khối lượng việc vượt quá giờ rảnh -> Báo Infeasible & Deficit -> Chặn chốt lịch -> Người dùng điều chỉnh -> Lịch trở nên Feasible -> Chốt lịch thành công.
4. Pipeline 4: Disruption & Adaptive Rescheduling Flow (UF6 Phục hồi gián đoạn)
   Bỏ lỡ phiên -> Tịnh tiến phiên trôi qua sang tương lai -> Bảo vệ các Task cố định khác -> Sinh lời giải thích ấm áp không phán xét.
5. Pipeline 5: A20 suggestion helper (chưa áp Settings hay cập nhật lịch)
   Thu thập mẫu bước sạch -> Tính trung vị -> Trả gợi ý pending để người dùng xem.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.errors import AppError
from app.models import (
    Base,
)
from app.models import (
    PlanCandidate as CandidateModel,
)
from app.models import (
    Session as SessionModel,
)
from app.models import (
    Step as StepModel,
)
from app.models import (
    Task as TaskModel,
)
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.common import SessionSegment, TimeConstraint, TimeInterval
from app.schemas.draft import (
    ConfirmSummaryRequest,
    CreateDraftRequest,
    DraftChanges,
    SummaryChanges,
    SummaryInput,
    UpdateDraftRequest,
)
from app.schemas.planning import (
    ConfirmCandidateRequest,
    GenerateCandidateRequest,
)
from app.schemas.task import Session, StepPromptItem, TaskDecompositionResult
from app.services import draft_service, plan_service
from app.services.ai_service import AIService, ai_service
from app.services.planning.adaptation import evaluate_buffer_adaptation
from app.services.planning.rescheduling import shift_elapsed_sessions

VN_TZ = timezone(timedelta(hours=7))
DAYTIME_ANCHOR = datetime(2026, 11, 2, 9, 0, tzinfo=VN_TZ)  # 09:00 sáng Thứ Hai


class TestDeepChainedPipeline(unittest.TestCase):
    """Kiểm thử chuỗi tác vụ liên hoàn tích hợp toàn bộ hệ thống."""

    def setUp(self) -> None:
        self.ai_patcher = patch.object(
            ai_service,
            "_execute_hedged_structured",
            side_effect=RuntimeError("AI hedged execution disabled for offline testing"),
        )
        self.ai_patcher.start()

        async def deterministic_decomposition(*, task_title, **kwargs):
            # Mock kết quả AI hợp lệ, không giả định public API tự fallback khi mất provider.
            return TaskDecompositionResult(
                task_title=task_title,
                steps=[
                    StepPromptItem(
                        order_index=i,
                        title=f"Bước {i}",
                        expected_output=f"Đầu ra {i}",
                        estimated_minutes=25,
                        micro_task_prompt="Mở tài liệu và ghi một ý chính",
                    )
                    for i in range(1, 4)
                ],
                reasoning="Phương án cố định cho kiểm thử scheduler và lưu DB.",
                generation_tier="tier_1_gemini",
            )

        self.decomposition_patcher = patch.object(
            ai_service, "decompose_task", side_effect=deterministic_decomposition
        )
        self.decomposition_patcher.start()

    def tearDown(self) -> None:
        self.decomposition_patcher.stop()
        self.ai_patcher.stop()

    async def _async_setup_db(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await draft_service.ensure_dev_seed_in_db(db)
        return engine, factory

    async def _async_seed_availability(self, factory) -> DraftAvailability:
        # Giờ riêng của yêu cầu UF1; không cần tạo bộ Availability toàn cục.
        return DraftAvailability(
            timezone="UTC",
            weekly_rules=[
                WeeklyRule(weekday=w, start_local="00:00", end_local="23:59") for w in range(1, 8)
            ],
        )

    # =========================================================================
    # PIPELINE 1: HAPPY PATH FULL CHAIN (UF1 -> UF2 -> PERSISTENCE)
    # =========================================================================

    def test_pipeline_1_happy_path_end_to_end(self):
        """Pipeline 1: Luồng hoàn hảo đầy đủ từ khâu nhập văn bản tới ghi lịch DB."""

        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                availability = await self._async_seed_availability(factory)

                # BƯỚC 1: Tạo Draft với đầy đủ thông tin (UF1)
                t_start = datetime.now(UTC).replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                t_deadline = t_start + timedelta(days=7)

                payload_create = CreateDraftRequest(
                    client_draft_id="pipe_1_draft",
                    input_source="text",
                    raw_input="Làm báo cáo môn Hệ thống Thông tin 10 trang, nộp sau 7 ngày",
                    summary=SummaryInput(
                        title="Báo cáo HTTT",
                        description="Nghiên cứu tài liệu và hoàn thành báo cáo",
                        scope="10 trang kèm sơ đồ kiến trúc",
                        time_constraint=TimeConstraint(
                            kind="deadline",
                            start_at=t_start,
                            deadline_at=t_deadline,
                            input_timezone="Asia/Ho_Chi_Minh",
                        ),
                        availability=availability,
                    ),
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a", idempotency_key="pipe_1_k1", payload=payload_create, db=db
                    )
                    # Draft đầy đủ chuyển ngay sang giai đoạn summary_review
                    self.assertEqual(draft.stage, "summary_review")

                # BƯỚC 2: Xác nhận Summary (UF1 Chốt mục tiêu)
                async with factory() as db:
                    conf_draft = await draft_service.confirm_draft_summary(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_1_k2",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=draft.summary_revision
                        ),
                        db=db,
                    )
                    self.assertEqual(conf_draft.stage, "summary_confirmed")

                # BƯỚC 3: Sinh Candidate Plan (UF2 Bẻ nhỏ việc & Xếp lịch)
                async with factory() as db:
                    cand = await plan_service.create_candidate(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_1_k3",
                        payload=GenerateCandidateRequest(
                            source_summary_revision=conf_draft.summary_revision,
                            source_planning_input_revision=conf_draft.planning_input_revision,
                        ),
                        db=db,
                    )
                    self.assertEqual(cand.feasibility.status, "feasible")
                    self.assertTrue(cand.feasibility.fully_scheduled)
                    self.assertGreaterEqual(len(cand.steps), 2)
                    self.assertGreaterEqual(len(cand.sessions), 2)

                # BƯỚC 4: Chốt Candidate Plan vào Database (UF2 Chốt kế hoạch)
                async with factory() as db:
                    confirmed_res = await plan_service.confirm_candidate(
                        user_id="user_a",
                        candidate_id=cand.candidate_id,
                        idempotency_key="pipe_1_k4",
                        payload=ConfirmCandidateRequest(
                            expected_review_revision=cand.review_revision,
                            expected_summary_revision=cand.source_summary_revision,
                            expected_planning_input_revision=cand.source_planning_input_revision,
                            expected_source_revisions=cand.source_revisions,
                        ),
                        db=db,
                    )
                    self.assertEqual(confirmed_res.app_save_state, "saved")

                # BƯỚC 5: Kiểm tra tính toàn vẹn trong Database
                async with factory() as db:
                    tasks = (await db.scalars(select(TaskModel))).all()
                    self.assertEqual(len(tasks), 1)
                    self.assertEqual(tasks[0].title, "Báo cáo HTTT")
                    self.assertEqual(tasks[0].status, "pending")

                    steps = (
                        await db.scalars(select(StepModel).where(StepModel.task_id == tasks[0].id))
                    ).all()
                    self.assertGreaterEqual(len(steps), 2)

                    sessions = (
                        await db.scalars(
                            select(SessionModel).where(
                                SessionModel.step_id.in_([s.id for s in steps])
                            )
                        )
                    ).all()
                    self.assertGreaterEqual(len(sessions), 2)
                    for sess in sessions:
                        self.assertEqual(sess.status, "scheduled")
                        self.assertIsNotNone(sess.scheduled_start)
                        self.assertIsNotNone(sess.scheduled_end)
                        self.assertGreater(sess.scheduled_end, sess.scheduled_start)

            finally:
                await engine.dispose()

        asyncio.run(_run())

    # =========================================================================
    # PIPELINE 2: CLARIFICATION & CONVERGENCE LOOP (UF1 LÀM RÕ)
    # =========================================================================

    def test_pipeline_2_clarification_loop_convergence(self):
        """Pipeline 2: Nhập mơ hồ -> Bị chặn chuyển bước -> Cung cấp thông tin -> Hoàn tất."""

        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                availability = await self._async_seed_availability(factory)

                # BƯỚC 1: Người dùng nhập rất mơ hồ ("Làm bài tập") thiếu scope, deadline
                payload_vague = CreateDraftRequest(
                    client_draft_id="pipe_2_draft",
                    input_source="text",
                    raw_input="Làm bài tập",
                    summary=SummaryInput(
                        title="Bài tập",
                        description="Làm bài tập",
                        # Không có scope, không có time_constraint!
                    ),
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a", idempotency_key="pipe_2_k1", payload=payload_vague, db=db
                    )
                    # Giai đoạn vẫn là collecting do chưa đủ điều kiện
                    self.assertEqual(draft.stage, "collecting")

                # BƯỚC 2: Cố tình confirm summary khi chưa đủ thông tin -> BỊ CHẶN (422)
                async with factory() as db:
                    with self.assertRaises(AppError) as ctx:
                        await draft_service.confirm_draft_summary(
                            user_id="user_a",
                            draft_id=draft.draft_id,
                            idempotency_key="pipe_2_fail_conf",
                            payload=ConfirmSummaryRequest(
                                expected_summary_revision=draft.summary_revision
                            ),
                            db=db,
                        )
                    self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

                # BƯỚC 3: Người dùng bổ sung thông tin qua màn hình làm rõ 1 vòng (Single-round Clarification)
                t_start = datetime.now(UTC).replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                t_deadline = t_start + timedelta(days=3)

                patch_payload = UpdateDraftRequest(
                    expected_summary_revision=draft.summary_revision,
                    expected_planning_input_revision=draft.planning_input_revision,
                    changes=DraftChanges(
                        summary=SummaryChanges(
                            scope="Bài tập gồm 5 câu đại số tuyến tính",
                            time_constraint=TimeConstraint(
                                kind="deadline",
                                start_at=t_start,
                                deadline_at=t_deadline,
                                input_timezone="Asia/Ho_Chi_Minh",
                            ),
                            availability=availability,
                        )
                    ),
                )
                async with factory() as db:
                    patched_draft = await draft_service.patch_draft(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_2_patch",
                        payload=patch_payload,
                        db=db,
                    )
                    # Sau khi bổ sung, chuyển sang summary_review
                    self.assertEqual(patched_draft.stage, "summary_review")

                # BƯỚC 4: Sau khi bổ sung, confirm summary thành công
                async with factory() as db:
                    conf_draft = await draft_service.confirm_draft_summary(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_2_success_conf",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=patched_draft.summary_revision
                        ),
                        db=db,
                    )
                    self.assertEqual(conf_draft.stage, "summary_confirmed")

            finally:
                await engine.dispose()

        asyncio.run(_run())

    # =========================================================================
    # PIPELINE 3: INFEASIBLE DEFICIT & INTERACTIVE ADJUSTMENT (UF2)
    # =========================================================================

    def test_pipeline_3_infeasible_deficit_and_adjustment(self):
        """Pipeline 3: Lịch bị thiếu thời gian (Deficit) -> Chặn chốt -> Chỉnh sửa -> Khả thi."""

        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                availability = await self._async_seed_availability(factory)

                # Tạo Draft có deadline rất ngắn: chỉ 40 phút nữa (t_start = +1h, deadline = +1h40m)
                t_start = datetime.now(UTC).replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                t_deadline = t_start + timedelta(minutes=40)

                payload = CreateDraftRequest(
                    client_draft_id="pipe_3_draft",
                    input_source="text",
                    raw_input="Ôn thi học kỳ",
                    summary=SummaryInput(
                        title="Ôn thi học kỳ",
                        description="Ôn thi",
                        scope="Toàn bộ chương trình",
                        time_constraint=TimeConstraint(
                            kind="deadline",
                            start_at=t_start,
                            deadline_at=t_deadline,
                            input_timezone="Asia/Ho_Chi_Minh",
                        ),
                        availability=availability,
                    ),
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a", idempotency_key="pipe_3_k1", payload=payload, db=db
                    )
                    await draft_service.confirm_draft_summary(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_3_k2",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=draft.summary_revision
                        ),
                        db=db,
                    )

                # BƯỚC 1: Sinh Candidate -> 3 bước mặc định (3 * 25p = 75p work + 2*15p rest = 105p > 40p) -> Infeasible!
                async with factory() as db:
                    cand = await plan_service.create_candidate(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_3_k3",
                        payload=GenerateCandidateRequest(
                            source_summary_revision=draft.summary_revision,
                            source_planning_input_revision=draft.planning_input_revision,
                        ),
                        db=db,
                    )
                    self.assertEqual(cand.feasibility.status, "infeasible")
                    self.assertFalse(cand.feasibility.fully_scheduled)
                    assert cand.feasibility.deficit_seconds is not None
                    self.assertGreater(cand.feasibility.deficit_seconds, 0)

                # BƯỚC 2: Cố tình chốt lịch Infeasible -> BỊ CHẶN với mã lỗi PLAN_NOT_CONFIRMABLE
                async with factory() as db:
                    with self.assertRaises(AppError) as ctx:
                        await plan_service.confirm_candidate(
                            user_id="user_a",
                            candidate_id=cand.candidate_id,
                            idempotency_key="pipe_3_k4_blocked",
                            payload=ConfirmCandidateRequest(
                                expected_review_revision=cand.review_revision,
                                expected_summary_revision=cand.source_summary_revision,
                                expected_planning_input_revision=cand.source_planning_input_revision,
                                expected_source_revisions=cand.source_revisions,
                            ),
                            db=db,
                        )
                    self.assertEqual(ctx.exception.code, "PLAN_NOT_CONFIRMABLE")

                # BƯỚC 3: Người dùng điều chỉnh: nới lỏng deadline thêm 5 tiếng nữa
                new_deadline = t_start + timedelta(hours=5)
                patch_payload = UpdateDraftRequest(
                    expected_summary_revision=draft.summary_revision,
                    expected_planning_input_revision=draft.planning_input_revision,
                    changes=DraftChanges(
                        summary=SummaryChanges(
                            time_constraint=TimeConstraint(
                                kind="deadline",
                                start_at=t_start,
                                deadline_at=new_deadline,
                                input_timezone="Asia/Ho_Chi_Minh",
                            )
                        )
                    ),
                )
                async with factory() as db:
                    patched_draft = await draft_service.patch_draft(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_3_k5_patch",
                        payload=patch_payload,
                        db=db,
                    )
                    conf_draft = await draft_service.confirm_draft_summary(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_3_k5_reconf",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=patched_draft.summary_revision
                        ),
                        db=db,
                    )

                # BƯỚC 4: Tạo lại candidate với deadline mới -> FEASIBLE và chốt lịch thành công!
                async with factory() as db:
                    cand2 = await plan_service.create_candidate(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="pipe_3_k6_cand2",
                        payload=GenerateCandidateRequest(
                            source_summary_revision=conf_draft.summary_revision,
                            source_planning_input_revision=conf_draft.planning_input_revision,
                        ),
                        db=db,
                    )
                    self.assertEqual(cand2.feasibility.status, "feasible")
                    self.assertTrue(cand2.feasibility.fully_scheduled)
                    self.assertEqual(cand2.feasibility.deficit_seconds, 0)

                    res = await plan_service.confirm_candidate(
                        user_id="user_a",
                        candidate_id=cand2.candidate_id,
                        idempotency_key="pipe_3_k7_ok",
                        payload=ConfirmCandidateRequest(
                            expected_review_revision=cand2.review_revision,
                            expected_summary_revision=cand2.source_summary_revision,
                            expected_planning_input_revision=cand2.source_planning_input_revision,
                            expected_source_revisions=cand2.source_revisions,
                        ),
                        db=db,
                    )
                    self.assertEqual(res.app_save_state, "saved")

            finally:
                await engine.dispose()

        asyncio.run(_run())

    # =========================================================================
    # PIPELINE 4: DISRUPTION RECOVERY & ADAPTIVE RESCHEDULING (UF6)
    # =========================================================================

    def test_pipeline_4_disruption_recovery_and_rescheduling(self):
        """Pipeline 4: Gián đoạn/Bỏ lỡ phiên -> Tịnh tiến an toàn sang tương lai kèm lời an ủi."""
        now = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)

        s1 = Session(
            session_id="sess_1_missed",
            step_id="step_a",
            scheduled_start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
            scheduled_end=datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
            duration_seconds=3600,
            work_seconds=3600,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            micro_task_text="Khởi động 2 phút",
            schedule_revision=1,
            status="incomplete",
            start_state="waiting",
            start_decision_due_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
        )
        s2_fixed = Session(
            session_id="sess_2_fixed",
            step_id="step_b",
            scheduled_start=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
            scheduled_end=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
            duration_seconds=3600,
            work_seconds=3600,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            micro_task_text="Khởi động 2 phút",
            schedule_revision=1,
            status="scheduled",
            start_state="waiting",
            start_decision_due_at=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
        )

        future_free = [
            TimeInterval(
                start_at=datetime(2026, 11, 2, 11, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 2, 18, 0, tzinfo=UTC),
            )
        ]

        # 1. Tái lập lịch thích ứng (UF6)
        moved, affected, all_shifted, deficit = shift_elapsed_sessions(
            sessions=[s1, s2_fixed],
            allowed_task_ids=["task_current"],
            effective_intervals=future_free,
            real_time_anchor=now,
            step_to_task_map={"step_a": "task_current", "step_b": "task_other_fixed"},
        )

        self.assertTrue(all_shifted)
        self.assertEqual(affected, ["task_current"])
        self.assertEqual(len(moved), 1)
        self.assertGreaterEqual(moved[0]["new_start"], datetime(2026, 11, 2, 11, 0, tzinfo=UTC))

        # 2. Sinh lời an ủi/giải thích UF6 (Guilt-free AI note)
        svc = AIService(
            app_settings=Settings(GEMINI_API_KEY="", GROQ_API_KEY="", GOOGLE_CREDENTIALS_PATH="")
        )
        empathy = asyncio.run(
            svc.generate_reschedule_empathy(
                trigger_reason="missed_session",
                task_title="Báo cáo tiến độ",
            )
        )
        self.assertEqual(empathy.generation_tier, "tier_3_rule_based")
        self.assertIsNotNone(empathy.empathetic_note)
        self.assertNotIn("lỗi", empathy.empathetic_note.lower())

    # =========================================================================
    # PIPELINE 5: ADAPTIVE LEARNING FEEDBACK LOOP (A20 BUFFER MULTIPLIER)
    # =========================================================================

    def test_pipeline_5_adaptive_buffer_learning_loop(self):
        """Pipeline 5: Vòng lặp thích ứng A20 — Học hệ số đệm từ lịch sử thực tế."""
        step_history = [
            {
                "step_id": "st1",
                "status": "completed",
                "estimated_base_seconds": 3000,
                "actual_work_seconds": 4200,
                "scope_modified": False,
                "actual_source": "self_report",
            },
            {
                "step_id": "st2",
                "status": "completed",
                "estimated_base_seconds": 2000,
                "actual_work_seconds": 2900,
                "scope_modified": False,
                "actual_source": "self_report",
            },
            {
                "step_id": "st3",
                "status": "completed",
                "estimated_base_seconds": 3000,
                "actual_work_seconds": 4200,
                "scope_modified": False,
                "actual_source": "self_report",
            },
            {
                "step_id": "st4",
                "status": "completed",
                "estimated_base_seconds": 2500,
                "actual_work_seconds": 3500,
                "scope_modified": False,
                "actual_source": "self_report",
            },
            {
                "step_id": "st5",
                "status": "completed",
                "estimated_base_seconds": 4000,
                "actual_work_seconds": 5400,
                "scope_modified": False,
                "actual_source": "self_report",
            },
        ]

        suggestion = evaluate_buffer_adaptation(
            step_samples=step_history,
            current_buffer_multiplier=1.05,
            min_sample_count=5,
            delta_threshold=0.20,
        )

        assert suggestion is not None
        self.assertEqual(suggestion["kind"], "buffer_multiplier")
        self.assertEqual(suggestion["suggested_value"], 1.40)
        self.assertEqual(suggestion["sample_count"], 5)
        self.assertIn("1.40", suggestion["reason"])

    def test_pipeline_6_ai_unavailable_keeps_confirmed_draft(self):
        """Public UF2 lỗi provider -> phương án Python; chỉ ghi Task khi user xác nhận."""

        async def run():
            engine, factory = await self._async_setup_db()
            try:
                start = datetime.now(UTC).replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                availability = await self._async_seed_availability(factory)
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a",
                        idempotency_key="ai_failure_create_draft",
                        payload=CreateDraftRequest(
                            client_draft_id="ai_failure_draft",
                            input_source="text",
                            raw_input="Viết báo cáo",
                            summary=SummaryInput(
                                title="Báo cáo",
                                description="Viết báo cáo",
                                scope="1 trang",
                                availability=availability,
                                time_constraint=TimeConstraint(
                                    kind="deadline",
                                    start_at=start,
                                    deadline_at=start + timedelta(days=2),
                                    input_timezone="UTC",
                                ),
                            ),
                        ),
                        db=db,
                    )
                async with factory() as db:
                    await draft_service.confirm_draft_summary(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="ai_failure_confirm_draft",
                        payload=ConfirmSummaryRequest(
                            expected_summary_revision=draft.summary_revision
                        ),
                        db=db,
                    )
                # Gọi implementation public thật, chỉ mock provider bên dưới đã bị ngắt ở setUp.
                with patch.object(
                    ai_service,
                    "decompose_task",
                    AIService.decompose_task.__get__(ai_service, AIService),
                ):
                    async with factory() as db:
                        generated = await plan_service.create_candidate(
                            user_id="user_a",
                            draft_id=draft.draft_id,
                            idempotency_key="ai_failure_candidate",
                            payload=GenerateCandidateRequest(
                                source_summary_revision=draft.summary_revision,
                                source_planning_input_revision=draft.planning_input_revision,
                            ),
                            db=db,
                        )
                        self.assertEqual(len(generated.steps), 3)
                        self.assertTrue(generated.sessions)
                async with factory() as db:
                    kept = await draft_service.get_draft_by_id(
                        user_id="user_a", draft_id=draft.draft_id, db=db
                    )
                    self.assertEqual(kept.stage, "candidate_review")
                    self.assertEqual(kept.summary_revision, draft.summary_revision)
                    self.assertEqual(kept.summary.scope, "1 trang")
                    self.assertIsNone(kept.confirmed_task_id)
                    self.assertIsNotNone(await db.scalar(select(CandidateModel.id)))
                    self.assertIsNone(await db.scalar(select(TaskModel.id)))
            finally:
                await engine.dispose()

        asyncio.run(run())
