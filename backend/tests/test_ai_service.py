"""Kiểm thử cấu hình AI, Pydantic v2 Schemas, Chốt chặn Hallucination và Quality-First Hedged Fallback."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from typing import Any
from unittest.mock import patch

from app.core.config import Settings
from app.schemas.schedule import (
    FeasibilityReportSchema,
    ProposedSessionSchema,
    RescheduleEmpatheticResult,
    SessionSegmentSchema,
)
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    LLMPlanAdjustmentPayload,
    LLMTaskDecompositionPayload,
    LLMTaskExtractionPayload,
    StepPromptItem,
    TaskDecompositionResult,
    TaskExtractionResult,
)
from app.services.ai_service import AIService, ai_service


class TestAIService(unittest.TestCase):
    """Kiểm thử các thành phần AI Service, Hedged Warm Backup và cơ chế Fallback Tầng 3."""

    def test_schema_serialization(self) -> None:
        """Kiểm tra Pydantic v2 serialization cho UF1, UF2, UF6."""
        uf1_obj = TaskExtractionResult(
            content="Làm báo cáo môn Đồ án",
            scope="Hoàn thiện 5 trang tài liệu",
            free_time_slots=["Tối thứ 3 từ 19h-21h"],
            deadline=datetime(2026, 11, 1, 23, 59),
            is_complete=True,
            has_conflict=False,
            summary="Báo cáo môn Đồ án cần hoàn thành trước Chủ nhật.",
            clarification_questions=[],
        )
        self.assertTrue(uf1_obj.is_complete)
        self.assertEqual(len(uf1_obj.clarification_questions), 0)

        uf2_obj = TaskDecompositionResult(
            task_title="Viết luận văn",
            steps=[
                StepPromptItem(
                    order_index=1,
                    title="Viết phần Giới thiệu",
                    estimated_minutes=25,
                    micro_task_prompt="Mở file Word và gõ tiêu đề chương 1.",
                )
            ],
            reasoning="Chia thành các phần nhỏ để không bị ngợp.",
        )
        self.assertEqual(len(uf2_obj.steps), 1)
        self.assertEqual(uf2_obj.steps[0].estimated_base_seconds, 1500)
        self.assertIn("micro_task_prompt", uf2_obj.steps[0].model_dump())

        uf6_obj = RescheduleEmpatheticResult(
            empathetic_note="Có việc bận là điều bình thường, đừng lo lắng.",
            encouragement_tip="Nghỉ ngơi và uống một ngụm nước nhé.",
        )
        self.assertIsNotNone(uf6_obj.empathetic_note)

    def test_uf1_anti_hallucination_guardrail(self) -> None:
        """Bẫy lỗi LLM ảo giác trả is_complete=True khi thiếu deadline hoặc scope."""
        hallucinated = TaskExtractionResult(
            content="Ôn thi cuối kỳ môn Mạng máy tính",
            scope=None,
            free_time_slots=["Tối thứ 5 từ 19h-21h"],
            deadline=None,
            is_complete=True,  # LLM trả sai quy tắc
            has_conflict=False,
            summary="Ôn thi Mạng máy tính vào tối thứ 5.",
            clarification_questions=[],
        )
        self.assertFalse(hallucinated.is_complete)
        self.assertGreaterEqual(len(hallucinated.clarification_questions), 2)

    def test_feasibility_deficit_formula(self) -> None:
        """Kiểm chứng công thức bất biến Delta_T = max(0, T_req - T_free) cho UF2."""
        report = FeasibilityReportSchema(
            status="valid",  # Cố tình truyền valid khi thiếu giờ để kiểm tra validator tự chỉnh
            required_seconds=5400,  # 90 phút
            available_seconds=3600,  # 60 phút
        )
        self.assertEqual(report.status, "deficit")
        self.assertFalse(report.fully_scheduled)
        self.assertEqual(report.deficit_seconds, 1800)
        self.assertEqual(report.deficit_minutes, 30)

    def test_settings_secret_redaction(self) -> None:
        """Đảm bảo API Keys, đường dẫn credentials và SECRET_KEY không bao giờ lộ qua repr()."""
        custom_settings = Settings(
            SECRET_KEY="top-secret-jwt-value",
            GEMINI_API_KEY="AIzaSyDummyGeminiSecret",
            GROQ_API_KEY="gsk_DummyGroqSecret",
            GOOGLE_CREDENTIALS_PATH="secret_sa_key.json",
        )
        rendered = repr(custom_settings)
        self.assertNotIn("top-secret-jwt-value", rendered)
        self.assertNotIn("AIzaSyDummyGeminiSecret", rendered)
        self.assertNotIn("gsk_DummyGroqSecret", rendered)
        self.assertNotIn("secret_sa_key.json", rendered)
        self.assertTrue(custom_settings.has_gemini_key)
        self.assertTrue(custom_settings.has_groq_key)

    def test_tier3_fallback_uf1(self) -> None:
        """Kiểm tra Fallback Tầng 3 cho UF1: Gom câu hỏi làm rõ nếu chưa đủ thông tin."""
        user_input = "Tôi muốn chuẩn bị thuyết trình"
        result = ai_service._fallback_extract_task(user_input)

        self.assertIsInstance(result, TaskExtractionResult)
        self.assertFalse(result.is_complete)
        self.assertGreater(len(result.clarification_questions), 0)
        assert result.content is not None
        self.assertIn("thuyết trình", result.content)

    def test_tier3_fallback_uf2(self) -> None:
        """Kiểm tra Fallback Tầng 3 cho UF2: Bắt buộc có Micro-task 2 phút cho mỗi bước."""
        title = "Dọn dẹp phòng làm việc"
        result = ai_service._fallback_decompose_task(title, user_focus_duration=25)

        self.assertIsInstance(result, TaskDecompositionResult)
        self.assertGreaterEqual(len(result.steps), 2)
        for step in result.steps:
            self.assertTrue(bool(step.micro_task_prompt.strip()), "Bước phải có micro-task 2 phút")
            self.assertEqual(step.estimated_minutes, 25)

    def test_uf2_provider_failure_returns_tier3_proposal(self) -> None:
        """Luồng UF2 phải trả đề xuất tầng 3 khi provider lỗi hoặc hết thời gian."""
        service = AIService(
            app_settings=Settings(
                _env_file=None,
                GEMINI_API_KEY="",
                GROQ_API_KEY="",
                GOOGLE_CREDENTIALS_PATH="",
            )
        )
        for error in (TimeoutError("Provider timeout"), ConnectionError("Provider unavailable")):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(service, "_execute_hedged_structured", side_effect=error),
            ):
                result = asyncio.run(
                    service.decompose_task(
                        task_title="  Viết báo cáo môn học  ",
                        scope="Bản nháp gồm 5 trang",
                        user_focus_duration=20,
                    )
                )

                self.assertEqual(result.generation_tier, "tier_3_rule_based")
                self.assertEqual(result.task_title, "Viết báo cáo môn học")
                self.assertEqual([step.order_index for step in result.steps], [1, 2, 3])
                self.assertIn("Bản nháp gồm 5 trang", result.steps[1].title)
                for step in result.steps:
                    self.assertEqual(step.estimated_base_seconds, 1200)
                    self.assertTrue(step.micro_task_prompt.strip())
                    self.assertTrue(step.expected_output)

    def test_tier3_fallback_uf6(self) -> None:
        """Kiểm tra Fallback Tầng 3 cho UF6: Lời khích lệ nhẹ nhàng (Guilt-free)."""
        reasons = ["busy", "incomplete", "skipped", "setting_changed"]
        for reason in reasons:
            result = ai_service._fallback_reschedule_empathy(reason)
            self.assertIsInstance(result, RescheduleEmpatheticResult)
            self.assertTrue(len(result.empathetic_note) > 10)
            self.assertIsNotNone(result.encouragement_tip)

    def test_timeout_graceful_degradation(self) -> None:
        """Giả lập cả Primary và Backup đều treo quá AI_TIMEOUT_SECONDS -> rơi êm ái về Tier 3."""
        fast_timeout_settings = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
            AI_TIMEOUT_SECONDS=0.06,
            AI_PRIMARY_PRIORITY_SECONDS=0.04,
            AI_WARM_BACKUP_DELAY_SECONDS=0.02,
        )
        service = AIService(app_settings=fast_timeout_settings)

        class _SlowAsyncModels:
            async def generate_content(self, **_: object) -> object:
                await asyncio.sleep(0.5)
                raise AssertionError("Phải bị hủy ngầm bởi bộ điều phối trước khi tới dòng này")

        class _SlowAio:
            models = _SlowAsyncModels()

        class _SlowClient:
            aio = _SlowAio()

        with patch.object(service, "_get_gemini_client", return_value=_SlowClient()):
            result = asyncio.run(service.extract_task_intent("Làm bài tập Giải tích chương 2"))

        self.assertEqual(result.generation_tier, "tier_3_rule_based")
        self.assertFalse(result.is_complete)
        self.assertEqual(result.content, "Làm bài tập Giải tích chương 2")

    def test_primary_preferred_over_faster_warm_backup(self) -> None:
        """
        Đảm bảo nếu flash-lite (Warm Backup) trả xong trước trong RAM nhưng 3.8-flash (Primary)
        vẫn kịp hoàn thành trong cửa sổ ưu tiên -> bắt buộc chọn output chất lượng cao của 3.8-flash.
        """
        cfg = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
            AI_WARM_BACKUP_DELAY_SECONDS=0.02,
            AI_PRIMARY_PRIORITY_SECONDS=0.18,
            AI_TIMEOUT_SECONDS=0.25,
        )
        service = AIService(app_settings=cfg)

        async def _fake_gemini(
            *,
            model_name: str | None = None,
            **_: Any,
        ) -> LLMTaskExtractionPayload:
            if model_name == "gemini-3.8-flash":
                await asyncio.sleep(0.09)  # Chậm hơn backup nhưng vẫn < 0.18s priority
                return LLMTaskExtractionPayload(
                    title="Bản chuẩn từ 3.8 Flash",
                    content="Làm slide KTPM",
                    scope="12 slide PowerPoint",
                    free_time_slots=["Chiều Chủ nhật 14h-17h"],
                    deadline=datetime(2026, 11, 4, 9, 0),
                    is_complete=True,
                    has_conflict=False,
                    summary="Output chất lượng cao từ Gemini 3.8 Flash.",
                    clarification_questions=[],
                )
            await asyncio.sleep(0.02)  # Xong ở 0.02 + 0.02 = 0.04s (sớm hơn 0.09s)
            return LLMTaskExtractionPayload(
                title="Bản dự phòng từ 3.5 Flash-Lite",
                content="Làm slide KTPM",
                scope="12 slide",
                free_time_slots=["Chiều Chủ nhật 14h-17h"],
                deadline=datetime(2026, 11, 4, 9, 0),
                is_complete=True,
                has_conflict=False,
                summary="Output dự phòng từ Flash-Lite.",
                clarification_questions=[],
            )

        with (
            patch.object(service, "_get_gemini_client", return_value=object()),
            patch.object(service, "_call_gemini_structured", side_effect=_fake_gemini),
        ):
            res = asyncio.run(service.extract_task_intent("Làm 12 slide KTPM"))

        self.assertEqual(res.generation_tier, "tier_1_gemini")
        self.assertEqual(res.title, "Bản chuẩn từ 3.8 Flash")

    def test_warm_backup_returned_for_giant_task_past_priority_window(self) -> None:
        """
        Giả lập Task Khổng Lồ ở UF2: 3.8-flash chạy lố cửa sổ ưu tiên, hệ thống bung ngay
        bản chia bước của 3.5-flash-lite đã nằm sẵn trong RAM (không bị rơi xuống Tier 3).
        """
        cfg = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
            AI_UF2_WARM_BACKUP_DELAY_SECONDS=0.02,
            AI_UF2_PRIMARY_PRIORITY_SECONDS=0.10,
            AI_UF2_TIMEOUT_SECONDS=0.20,
        )
        service = AIService(app_settings=cfg)

        async def _fake_uf2_gemini(
            *,
            model_name: str | None = None,
            **_: Any,
        ) -> LLMTaskDecompositionPayload:
            if model_name == "gemini-3.8-flash":
                await asyncio.sleep(0.50)  # Mô phỏng Khóa luận 80 trang bị kéo dài
            await asyncio.sleep(0.03)  # Xong ở ~0.05s và đợi trong RAM tới 0.10s
            return LLMTaskDecompositionPayload(
                task_title="Khóa luận tốt nghiệp",
                steps=[
                    StepPromptItem(
                        order_index=1,
                        title="Thiết kế cấu trúc đề cương LaTeX",
                        estimated_minutes=30,
                        micro_task_prompt="Tạo file main.tex trong thư mục khóa luận.",
                        cognitive_load="light",
                    ),
                    StepPromptItem(
                        order_index=2,
                        title="Xử lý tập dữ liệu mẫu",
                        estimated_minutes=45,
                        micro_task_prompt="Mở notebook và đọc 10 dòng đầu tiên của dữ liệu.",
                        cognitive_load="medium",
                    ),
                ],
                reasoning="Chia nhỏ khóa luận từ Warm Backup 3.5 Flash-Lite.",
            )

        with (
            patch.object(service, "_get_gemini_client", return_value=object()),
            patch.object(service, "_call_gemini_structured", side_effect=_fake_uf2_gemini),
        ):
            res = asyncio.run(service.decompose_task(task_title="Khóa luận tốt nghiệp"))

        self.assertEqual(res.generation_tier, "tier_1_gemini")
        self.assertEqual(len(res.steps), 2)
        self.assertIn("Warm Backup", res.reasoning)

    def test_fast_fail_cascades_immediately_to_groq(self) -> None:
        """
        Khi cả 2 model Google báo lỗi nhanh (ví dụ HTTP 402/403/503), hệ thống nhảy thẳng sang Groq
        ngay tức khắc mà KHÔNG cần chờ tới mốc AI_GROQ_HEDGE_DELAY_SECONDS (3.5s).
        """
        cfg = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="gsk_TestKey",
            GOOGLE_CREDENTIALS_PATH="",
            AI_WARM_BACKUP_DELAY_SECONDS=2.6,
            AI_GROQ_HEDGE_DELAY_SECONDS=3.5,
            AI_PRIMARY_PRIORITY_SECONDS=4.0,
            AI_TIMEOUT_SECONDS=4.5,
        )
        service = AIService(app_settings=cfg)

        async def _failing_gemini(**_: Any) -> LLMTaskExtractionPayload:
            raise PermissionError("Google API unavailable")

        async def _working_groq(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Cứu hộ tức thì từ Groq Qwen",
                content="Viết tiểu luận Triết",
                scope=None,
                free_time_slots=[],
                deadline=None,
                is_complete=False,
                has_conflict=False,
                summary="Groq tiếp nhận xử lý ngay lập tức.",
                clarification_questions=["Bạn cần nộp bài ngày nào và viết mấy trang?"],
            )

        t_start = datetime.now()
        with (
            patch.object(service, "_get_gemini_client", return_value=object()),
            patch.object(service, "_get_groq_client", return_value=object()),
            patch.object(service, "_call_gemini_structured", side_effect=_failing_gemini),
            patch.object(service, "_call_groq_structured", side_effect=_working_groq),
        ):
            res = asyncio.run(service.extract_task_intent("Viết tiểu luận Triết"))
        elapsed = (datetime.now() - t_start).total_seconds()

        self.assertEqual(res.generation_tier, "tier_2_groq")
        self.assertEqual(res.title, "Cứu hộ tức thì từ Groq Qwen")
        self.assertLess(elapsed, 0.5, "Phải nhảy sang Groq ngay tức thì thay vì chờ 3.5s")

    def test_groq_secondary_fallback_when_primary_groq_rate_limited(self) -> None:
        """Khi qwen/qwen3.8-27b chạm trần 429 TPM, tự động nhảy sang openai/gpt-oss-20b."""
        cfg = Settings(
            GEMINI_API_KEY="",
            GROQ_API_KEY="gsk_TestKey",
            GOOGLE_CREDENTIALS_PATH="",
            GROQ_FALLBACK_MODEL="qwen/qwen3.8-27b",
            GROQ_SECONDARY_FALLBACK_MODEL="openai/gpt-oss-20b",
        )
        service = AIService(app_settings=cfg)
        called_models: list[str] = []

        class _Msg:
            content = '{"empathetic_note": "Bạn cứ thong thả nhé, lịch mới đã sẵn sàng.", "encouragement_tip": "Uống ngụm nước ấm nhé."}'

        class _Choice:
            message = _Msg()

        class _Comp:
            choices = [_Choice()]

        class _FakeCompletions:
            async def create(self, *, model: str, **_: Any) -> _Comp:
                called_models.append(model)
                if model == "qwen/qwen3.8-27b":
                    raise RuntimeError("429 Rate limit reached for qwen")
                return _Comp()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeGroqClient:
            chat = _FakeChat()

        with patch.object(service, "_get_groq_client", return_value=_FakeGroqClient()):
            res = asyncio.run(service.generate_reschedule_empathy("busy"))

        self.assertEqual(called_models, ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"])
        self.assertEqual(res.generation_tier, "tier_2_groq")
        self.assertIn("thong thả", res.empathetic_note)

    def test_proposed_session_excludes_micro_task_from_duration(self) -> None:
        """O09 Guardrail: Micro-task 2 phút hiển thị trước phiên, KHÔNG cộng vào duration_seconds."""
        session = ProposedSessionSchema(
            client_session_key="sess-1",
            client_step_key="step-1",
            scheduled_start=datetime(2026, 11, 2, 12, 0),
            scheduled_end=datetime(2026, 11, 2, 12, 30),
            micro_task_seconds=120,
            work_seconds=1500,
            rest_seconds=300,
            duration_seconds=1800,
            micro_task_text="Mở file báo cáo và gõ tiêu đề chương 1.",
            segments=[
                SessionSegmentSchema(order_index=5, kind="work", duration_seconds=1500),
                SessionSegmentSchema(order_index=9, kind="rest", duration_seconds=300),
            ],
        )
        self.assertEqual(session.duration_seconds, 1800)
        self.assertEqual([s.order_index for s in session.segments], [1, 2])

        with self.assertRaises(ValueError):
            ProposedSessionSchema(
                client_session_key="sess-bad",
                client_step_key="step-1",
                scheduled_start=datetime(2026, 11, 2, 12, 0),
                scheduled_end=datetime(2026, 11, 2, 12, 32),
                micro_task_seconds=120,
                work_seconds=1500,
                rest_seconds=300,
                duration_seconds=1920,  # Sai: cộng nhầm 120s micro-task vào phiên chính
            )

    def test_uf1_ambiguous_free_window_blocks_completion(self) -> None:
        """Zero Hallucination: Khung giờ rảnh mơ hồ ('Tối thứ 6' chưa rõ giờ) phải chặn is_complete."""
        result = TaskExtractionResult(
            title="Làm báo cáo KTPM",
            content="Viết báo cáo môn Kỹ thuật phần mềm",
            scope="10 trang Word",
            free_time_slots=["Tối thứ 6"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=5,
                    start_local=None,
                    end_local=None,
                    raw_phrase="Tối thứ 6",
                )
            ],
            deadline=datetime(2026, 11, 8, 17, 0),
            is_complete=True,  # LLM cố tình trả True khi chưa rõ giờ
            has_conflict=False,
            summary="Báo cáo KTPM 10 trang vào tối thứ 6.",
            clarification_questions=[],
        )
        self.assertTrue(result.structured_free_windows[0].is_ambiguous)
        self.assertFalse(result.is_complete)
        self.assertTrue(any("Tối thứ 6" in q for q in result.clarification_questions))

    def test_uf1_python_catches_past_deadline_and_merges_previous_draft(self) -> None:
        """Kiểm tra hợp nhất previous_draft ở UF1 và lớp Python tự bắt lỗi deadline trong quá khứ."""
        cfg = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
        )
        service = AIService(app_settings=cfg)

        prev_draft = LLMTaskExtractionPayload(
            title="Báo cáo Phân tích dữ liệu",
            content="Làm báo cáo môn Phân tích dữ liệu",
            scope="8 trang PDF",
            free_time_slots=[],
            deadline=None,
            summary="Đang thiếu giờ rảnh và hạn chót.",
        )

        captured_prompts: list[str] = []

        async def _fake_gemini_past_deadline(
            *,
            user_prompt: str,
            **_: Any,
        ) -> LLMTaskExtractionPayload:
            captured_prompts.append(user_prompt)
            return LLMTaskExtractionPayload(
                title="Báo cáo Phân tích dữ liệu",
                content="Làm báo cáo môn Phân tích dữ liệu",
                scope="8 trang PDF",
                free_time_slots=["Thứ Bảy 09:00-11:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=6,
                        start_local="09:00",
                        end_local="11:00",
                        raw_phrase="Thứ Bảy 09:00-11:00",
                    )
                ],
                deadline=datetime(2026, 10, 30, 17, 0),  # Đã qua so với 2026-10-31!
                is_complete=True,  # LLM quên đánh dấu conflict
                has_conflict=False,
                summary="Đã gộp thông tin từ bản nháp.",
                clarification_questions=[],
            )

        ref_now = datetime(2026, 10, 31, 21, 0)
        with (
            patch.object(service, "_get_gemini_client", return_value=object()),
            patch.object(
                service,
                "_call_gemini_structured",
                side_effect=_fake_gemini_past_deadline,
            ),
        ):
            res = asyncio.run(
                service.extract_task_intent(
                    "Mình rảnh sáng thứ 7 từ 9h đến 11h, nộp hôm qua 30/10 lúc 17h",
                    previous_draft=prev_draft,
                    reference_now=ref_now,
                )
            )

        self.assertIn("8 trang PDF", captured_prompts[0])
        self.assertTrue(res.has_conflict)
        self.assertFalse(res.is_complete)
        self.assertTrue(any("đã qua" in q for q in res.clarification_questions))

        # Kiểm tra Tier 3 Fallback cũng bảo toàn nguyên vẹn previous_draft
        tier3 = service._fallback_extract_task("Mình rảnh tối mai", previous_draft=prev_draft)
        self.assertEqual(tier3.content, "Làm báo cáo môn Phân tích dữ liệu")
        self.assertEqual(tier3.scope, "8 trang PDF")
        self.assertEqual(len(tier3.clarification_questions), 2)  # Chỉ còn hỏi giờ rảnh & deadline

    def test_uf2_step_order_normalization_and_nl_plan_adjustment(self) -> None:
        """Kiểm tra chuẩn hóa order_index 1..N và hàm điều chỉnh kế hoạch tự nhiên ở UF2 Bước 4."""
        decomp = TaskDecompositionResult(
            task_title="Ôn tập Xác suất thống kê",
            steps=[
                StepPromptItem(
                    order_index=5,
                    title="Giải đề mẫu",
                    estimated_minutes=30,
                    micro_task_prompt="In đề thi mẫu ra bàn học.",
                ),
                StepPromptItem(
                    order_index=2,
                    title="Tóm tắt công thức chương 1-3",
                    estimated_minutes=25,
                    micro_task_prompt="Mở vở ghi và lấy tờ giấy A4 trắng.",
                ),
            ],
            reasoning="Chuẩn hóa thứ tự tuần tự.",
        )
        self.assertEqual([s.order_index for s in decomp.steps], [1, 2])
        self.assertEqual(decomp.steps[0].title, "Tóm tắt công thức chương 1-3")

        cfg = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
        )
        service = AIService(app_settings=cfg)

        async def _fake_adjust(**_: Any) -> LLMPlanAdjustmentPayload:
            return LLMPlanAdjustmentPayload(
                updated_steps=[
                    StepPromptItem(
                        order_index=10,
                        title="Tóm tắt công thức và giải 5 bài tập cơ bản",
                        estimated_minutes=40,
                        micro_task_prompt="Mở vở ghi chương 1 và đặt bút lên trang giấy nháp.",
                        cognitive_load="medium",
                    )
                ],
                added_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=7,
                        start_local="19:00",
                        end_local="21:00",
                        raw_phrase="Tối Chủ Nhật 19:00-21:00",
                    )
                ],
                removed_free_phrases=["Sáng thứ Bảy"],
                updated_deadline=None,
                requires_clarification=False,
                assistant_note="Mình đã gộp hai bước lại và mở thêm khung tối Chủ Nhật cho bạn.",
            )

        with (
            patch.object(service, "_get_gemini_client", return_value=object()),
            patch.object(service, "_call_gemini_structured", side_effect=_fake_adjust),
        ):
            adj = asyncio.run(
                service.adjust_plan_by_nl(
                    "Gộp 2 bước lại thành 40 phút, bỏ sáng thứ 7 và thêm tối Chủ Nhật 19h-21h",
                    current_steps=decomp.steps,
                )
            )

        self.assertEqual(adj.generation_tier, "tier_1_gemini")
        self.assertFalse(adj.requires_clarification)
        self.assertEqual(adj.updated_steps[0].order_index, 1)
        self.assertEqual(adj.added_free_windows[0].start_local, "19:00")
        self.assertEqual(adj.removed_free_phrases, ["Sáng thứ Bảy"])

        # Kiểm tra Tier 3 Fallback của UF2 Bước 4 không làm mất danh sách bước hiện có
        fallback_adj = service._fallback_adjust_plan(decomp.steps)
        self.assertEqual(fallback_adj.generation_tier, "tier_3_rule_based")
        self.assertEqual(len(fallback_adj.updated_steps), 2)
        self.assertTrue(fallback_adj.requires_clarification)


if __name__ == "__main__":
    unittest.main()
