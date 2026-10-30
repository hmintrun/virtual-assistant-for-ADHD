"""Kiểm thử cấu hình AI, Pydantic v2 Schemas, Chốt chặn Hallucination và 3-Tier Fallback."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from app.core.config import Settings
from app.schemas.schedule import FeasibilityReportSchema, RescheduleEmpatheticResult
from app.schemas.task import (
    StepPromptItem,
    TaskDecompositionResult,
    TaskExtractionResult,
)
from app.services.ai_service import AIService, ai_service


class TestAIService(unittest.TestCase):
    """Kiểm thử các thành phần AI Service và cơ chế Fallback Tầng 3"""

    def test_schema_serialization(self) -> None:
        """Kiểm tra Pydantic v2 serialization cho UF1, UF2, UF6"""
        # Test UF1 Schema
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

        # Test UF2 Schema (Micro-task 2p)
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

        # Test UF6 Schema
        uf6_obj = RescheduleEmpatheticResult(
            empathetic_note="Có việc bận là điều bình thường, đừng lo lắng.",
            encouragement_tip="Nghỉ ngơi và uống một ngụm nước nhé.",
        )
        self.assertIsNotNone(uf6_obj.empathetic_note)

    def test_uf1_anti_hallucination_guardrail(self) -> None:
        """Bẫy lỗi LLM ảo giác trả is_complete=True khi thiếu deadline hoặc scope"""
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
        """Kiểm chứng công thức bất biến Delta_T = max(0, T_req - T_free) cho UF2"""
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
        """Đảm bảo API Keys và SECRET_KEY không bao giờ lộ qua repr() vào log kỹ thuật"""
        custom_settings = Settings(
            SECRET_KEY="top-secret-jwt-value",
            GEMINI_API_KEY="AIzaSyDummyGeminiSecret",
            GROQ_API_KEY="gsk_DummyGroqSecret",
        )
        rendered = repr(custom_settings)
        self.assertNotIn("top-secret-jwt-value", rendered)
        self.assertNotIn("AIzaSyDummyGeminiSecret", rendered)
        self.assertNotIn("gsk_DummyGroqSecret", rendered)
        self.assertTrue(custom_settings.has_gemini_key)
        self.assertTrue(custom_settings.has_groq_key)

    def test_tier3_fallback_uf1(self) -> None:
        """Kiểm tra Fallback Tầng 3 cho UF1: Gom câu hỏi làm rõ nếu chưa đủ thông tin"""
        user_input = "Tôi muốn chuẩn bị thuyết trình"
        result = ai_service._fallback_extract_task(user_input)

        self.assertIsInstance(result, TaskExtractionResult)
        self.assertFalse(result.is_complete)
        # Bắt buộc phải có câu hỏi làm rõ gộp trong 1 lần duy nhất
        self.assertGreater(len(result.clarification_questions), 0)
        assert result.content is not None
        self.assertIn("thuyết trình", result.content)

    def test_tier3_fallback_uf2(self) -> None:
        """Kiểm tra Fallback Tầng 3 cho UF2: Bắt buộc có Micro-task 2 phút cho mỗi bước"""
        title = "Dọn dẹp phòng làm việc"
        result = ai_service._fallback_decompose_task(title, user_focus_duration=25)

        self.assertIsInstance(result, TaskDecompositionResult)
        self.assertGreaterEqual(len(result.steps), 2)
        for step in result.steps:
            self.assertTrue(bool(step.micro_task_prompt.strip()), "Bước phải có micro-task 2 phút")
            self.assertEqual(step.estimated_minutes, 25)

    def test_tier3_fallback_uf6(self) -> None:
        """Kiểm tra Fallback Tầng 3 cho UF6: Lời khích lệ nhẹ nhàng (Guilt-free)"""
        reasons = ["busy", "incomplete", "skipped", "setting_changed"]
        for reason in reasons:
            result = ai_service._fallback_reschedule_empathy(reason)
            self.assertIsInstance(result, RescheduleEmpatheticResult)
            self.assertTrue(len(result.empathetic_note) > 10)
            self.assertIsNotNone(result.encouragement_tip)

    def test_timeout_graceful_degradation(self) -> None:
        """Giả lập Gemini bị treo quá AI_TIMEOUT_SECONDS -> asyncio.wait_for ngắt và rơi êm ái về Tier 3"""
        fast_timeout_settings = Settings(
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="",
            AI_TIMEOUT_SECONDS=0.05,
        )
        service = AIService(app_settings=fast_timeout_settings)

        class _SlowAsyncInteractions:
            async def create(self, **_: object) -> object:
                await asyncio.sleep(0.3)
                raise AssertionError("Phải bị ngắt bởi asyncio.wait_for trước khi tới dòng này")

        class _SlowAio:
            interactions = _SlowAsyncInteractions()

        class _SlowClient:
            aio = _SlowAio()

        with patch.object(service, "_get_gemini_client", return_value=_SlowClient()):
            result = asyncio.run(service.extract_task_intent("Làm bài tập Giải tích chương 2"))

        self.assertEqual(result.generation_tier, "tier_3_rule_based")
        self.assertFalse(result.is_complete)
        self.assertEqual(result.content, "Làm bài tập Giải tích chương 2")


if __name__ == "__main__":
    unittest.main()
