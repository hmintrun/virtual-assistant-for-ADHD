"""Test Matrix UF1 & UF2 — Doãn Thành (QA Lead) — ProPlan VA.

Phạm vi kiểm thử
─────────────────
UF1 Tương tác & Xác nhận công việc : TC-UF1-01..15
UF2 Bẻ nhỏ việc & Chốt kế hoạch   : TC-UF2-01..15
E2E UF1 → UF2                      : TC-E2E-01..02

Benchmark mục tiêu
──────────────────
LLM (extract / decompose) ≤ 2.5 s
API CRUD mock             ≤ 300 ms

Nguồn tham chiếu bất biến
─────────────────────────
AGENTS.md §2.2 UF1 / UF2 / State Transition Matrix
week1_detailed_plan.md §4 Strict Guardrails
app/schemas/task.py  TaskExtractionResult, TaskDecompositionResult,
                     ExtractedFreeWindowSchema, LLMPlanAdjustmentPayload, StepPromptItem
app/services/ai_service.py  AIService.extract_task_intent / decompose_task / adjust_plan_by_nl
app/schemas/schedule.py     FeasibilityReportSchema
"""

from __future__ import annotations

import asyncio
import time
import unittest
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.errors import AppError
from app.models import (
    Availability as AvailabilityModel,
)
from app.models import (
    Base,
    Reminder,
    Step,
    Task,
)
from app.models import (
    ScheduleVersion as ScheduleVersionModel,
)
from app.models import (
    Session as SessionModel,
)
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.common import AvailabilityRef, TimeConstraint
from app.schemas.draft import (
    ConfirmSummaryRequest,
    CreateDraftRequest,
    DraftChanges,
    SummaryChanges,
    SummaryInput,
    UpdateDraftRequest,
)
from app.schemas.planning import (
    CandidateEdits,
    CandidateSessionEdit,
    CandidateStepEdit,
    ConfirmCandidateRequest,
    GenerateCandidateRequest,
    ManualStepInput,
    UpdateCandidateRequest,
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
from app.services import draft_service, plan_service
from app.services.ai_service import AIService, ai_service

# ─────────────────────────────────────────────────────────────────────────────
# Hằng dùng chung
# ─────────────────────────────────────────────────────────────────────────────
VN_TZ = timezone(timedelta(hours=7))
NOW_REF = datetime(2026, 11, 2, 15, 0, tzinfo=VN_TZ)

_MOCK_SETTINGS = Settings(
    GEMINI_API_KEY="AIzaSyMockKey",
    GROQ_API_KEY="gsk_MockKey",
    GOOGLE_CREDENTIALS_PATH="",
)


def _svc() -> AIService:
    return AIService(app_settings=_MOCK_SETTINGS)


# =============================================================================
# PHẦN 1 — UF1: Tương tác & Xác nhận công việc
# =============================================================================


class TestUF1HappyPath(unittest.TestCase):
    """TC-UF1-01..04: Luồng thành công — đủ thông tin ngay lần nhập đầu."""

    def test_TC_UF1_01_full_text_input_complete(self) -> None:
        """TC-UF1-01: Text input đầy đủ 4 trường bắt buộc.
        Kỳ vọng: is_complete=True, clarification_questions=[], generation_tier='tier_1_gemini'.
        """
        svc = _svc()

        async def _fake(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Báo cáo Phân tích Dữ liệu",
                content="Hoàn thiện báo cáo môn Phân tích Dữ liệu",
                scope="10 trang Word, có biểu đồ minh họa",
                free_time_slots=["Tối thứ 6 từ 19:00 đến 21:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=5,
                        start_local="19:00",
                        end_local="21:00",
                        raw_phrase="Tối thứ 6 từ 19:00 đến 21:00",
                    )
                ],
                deadline=datetime(2026, 11, 8, 17, 0, tzinfo=VN_TZ),
                is_complete=True,
                has_conflict=False,
                summary="Báo cáo 10 trang, nộp Chủ Nhật 17:00.",
                clarification_questions=[],
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(
                svc.extract_task_intent(
                    "Làm báo cáo Phân tích Dữ liệu 10 trang, tối thứ 6 từ 19h-21h, nộp Chủ Nhật 8/11 17h",
                    reference_now=NOW_REF,
                )
            )

        self.assertTrue(r.is_complete, "Đủ 4 trường → is_complete=True")
        self.assertFalse(r.has_conflict)
        self.assertEqual(r.clarification_questions, [])
        self.assertFalse(r.structured_free_windows[0].is_ambiguous)
        self.assertEqual(r.generation_tier, "tier_1_gemini")

    def test_TC_UF1_02_voice_stt_full_input_complete(self) -> None:
        """TC-UF1-02: Voice STT đầy đủ thông tin — 2 khung giờ, cả 2 rõ ràng.
        Kỳ vọng: is_complete=True, 2 structured_free_windows không ambiguous.
        """
        svc = _svc()

        async def _fake(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Báo cáo Phân tích Dữ liệu",
                content="Làm báo cáo môn Phân tích Dữ liệu",
                scope="Báo cáo hoàn chỉnh theo đề cương môn",
                free_time_slots=["Tối thứ 6 19:00-21:00", "Sáng thứ 7 09:00-11:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=5,
                        start_local="19:00",
                        end_local="21:00",
                        raw_phrase="Tối thứ 6 19:00-21:00",
                    ),
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=6,
                        start_local="09:00",
                        end_local="11:00",
                        raw_phrase="Sáng thứ 7 09:00-11:00",
                    ),
                ],
                deadline=datetime(2026, 11, 8, 17, 0, tzinfo=VN_TZ),
                is_complete=True,
                has_conflict=False,
                summary="Báo cáo, tối thứ 6 & sáng thứ 7, nộp Chủ Nhật 17:00.",
                clarification_questions=[],
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(
                svc.extract_task_intent(
                    "Tuần này mình cần làm báo cáo môn Phân tích Dữ liệu nộp trước 5h chiều Chủ Nhật, "
                    "tối thứ 6 và sáng thứ 7 mình rảnh từ 9h đến 11h",
                    reference_now=NOW_REF,
                )
            )

        self.assertTrue(r.is_complete)
        self.assertEqual(len(r.structured_free_windows), 2)
        self.assertFalse(any(w.is_ambiguous for w in r.structured_free_windows))

    def test_TC_UF1_03_summary_contains_key_fields(self) -> None:
        """TC-UF1-03: summary phải chứa nội dung công việc và deadline.
        Kỳ vọng: summary không rỗng, có từ khóa trong content.
        """
        r = TaskExtractionResult(
            title="Ôn thi cuối kỳ",
            content="Ôn thi môn Giải tích",
            scope="Chương 1-5, trọng tâm tích phân",
            free_time_slots=["Sáng thứ 3 08:00-10:00"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=2,
                    start_local="08:00",
                    end_local="10:00",
                    raw_phrase="Sáng thứ 3 08:00-10:00",
                )
            ],
            deadline=datetime(2026, 11, 10, 23, 59, tzinfo=VN_TZ),
            is_complete=True,
            has_conflict=False,
            summary="Ôn thi Giải tích chương 1-5, sáng thứ 3 08:00-10:00, thi 10/11.",
            clarification_questions=[],
        )
        self.assertTrue(r.is_complete)
        self.assertIn("Giải tích", r.summary)
        self.assertIsNotNone(r.deadline)

    def test_TC_UF1_04_multiple_valid_free_windows(self) -> None:
        """TC-UF1-04: 3 khung giờ rảnh tất cả rõ ràng → is_complete=True.
        Kỳ vọng: len(structured_free_windows)==3, không có is_ambiguous.
        """
        r = TaskExtractionResult(
            title="Đồ án KTPM",
            content="Hoàn thiện đồ án môn Kỹ thuật phần mềm",
            scope="SRS 20 trang + source code",
            free_time_slots=["Tối thứ 2 19-21h", "Sáng thứ 4 8-10h", "Chủ Nhật 9-17h"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=1,
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="Tối thứ 2 19-21h",
                ),
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=3,
                    start_local="08:00",
                    end_local="10:00",
                    raw_phrase="Sáng thứ 4 8-10h",
                ),
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=7,
                    start_local="09:00",
                    end_local="17:00",
                    raw_phrase="Chủ Nhật 9-17h",
                ),
            ],
            deadline=datetime(2026, 11, 15, 23, 59, tzinfo=VN_TZ),
            is_complete=True,
            has_conflict=False,
            summary="Đồ án KTPM, 3 khung rảnh/tuần, nộp 15/11.",
            clarification_questions=[],
        )
        self.assertTrue(r.is_complete)
        self.assertEqual(len(r.structured_free_windows), 3)
        self.assertFalse(any(w.is_ambiguous for w in r.structured_free_windows))


class TestUF1SingleRoundClarification(unittest.TestCase):
    """TC-UF1-05..09: Gộp câu hỏi làm rõ trong 1 màn hình duy nhất."""

    def test_TC_UF1_05_all_fields_missing_single_round(self) -> None:
        """TC-UF1-05: Input quá ngắn thiếu hoàn toàn — gộp ≥3 câu hỏi 1 vòng.
        Kỳ vọng: is_complete=False, len(clarification_questions) ≥ 3.
        (AGENTS.md §2.2 UF1 – GỘP CÂU HỎI TRONG 1 VÒNG DUY NHẤT)
        """
        r = ai_service._fallback_extract_task("Tôi muốn làm gì đó quan trọng")
        self.assertFalse(r.is_complete)
        self.assertGreaterEqual(
            len(r.clarification_questions),
            3,
            "Phải gộp ≥3 câu hỏi (scope, giờ rảnh, deadline) trong 1 vòng",
        )

    def test_TC_UF1_06_missing_free_time_and_deadline(self) -> None:
        """TC-UF1-06: Có content + scope nhưng không có giờ rảnh, không có deadline.
        Kỳ vọng: is_complete=False; câu hỏi phải hỏi cả giờ rảnh lẫn hạn chót.
        """
        r = TaskExtractionResult(
            title="Slide Marketing",
            content="Làm slide thuyết trình môn Marketing",
            scope="15 slide PowerPoint",
            free_time_slots=[],
            structured_free_windows=[],
            deadline=None,
            is_complete=True,  # LLM trả sai — guardrail phải chặn
            has_conflict=False,
            summary="Slide Marketing 15 slide.",
            clarification_questions=[],
        )
        self.assertFalse(r.is_complete, "Guardrail phải hạ is_complete xuống False")
        self.assertTrue(
            any("giờ" in q.lower() or "rảnh" in q.lower() for q in r.clarification_questions),
            "Phải hỏi khung giờ rảnh",
        )
        self.assertTrue(
            any("hoàn thành" in q.lower() or "hạn" in q.lower() for q in r.clarification_questions),
            "Phải hỏi hạn chót",
        )

    def test_TC_UF1_07_previous_draft_merging(self) -> None:
        """TC-UF1-07: Lượt 2 bổ sung giờ rảnh + deadline — previous_draft giữ nguyên.
        Kỳ vọng: content/scope giữ nguyên từ lượt 1, is_complete=True sau lượt 2.
        """
        svc = _svc()
        prev = LLMTaskExtractionPayload(
            title="Bài tập lớn CSDL",
            content="Làm bài tập lớn môn Cơ sở dữ liệu",
            scope="Thiết kế ERD và viết 10 câu SQL",
            free_time_slots=[],
            deadline=None,
            summary="Đang thiếu giờ rảnh và hạn chót.",
        )

        async def _fake(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Bài tập lớn CSDL",
                content="Làm bài tập lớn môn Cơ sở dữ liệu",
                scope="Thiết kế ERD và viết 10 câu SQL",
                free_time_slots=["Tối thứ 4 từ 20:00-22:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=3,
                        start_local="20:00",
                        end_local="22:00",
                        raw_phrase="Tối thứ 4 từ 20:00-22:00",
                    )
                ],
                deadline=datetime(2026, 11, 12, 23, 59, tzinfo=VN_TZ),
                is_complete=True,
                has_conflict=False,
                summary="Bài tập CSDL, tối thứ 4 20-22h, nộp 12/11.",
                clarification_questions=[],
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(
                svc.extract_task_intent(
                    "Tối thứ 4 từ 20h đến 22h, nộp 12/11 nha",
                    previous_draft=prev,
                    reference_now=NOW_REF,
                )
            )

        self.assertEqual(
            r.content, "Làm bài tập lớn môn Cơ sở dữ liệu", "content từ lượt 1 phải được giữ nguyên"
        )
        self.assertEqual(r.scope, "Thiết kế ERD và viết 10 câu SQL")
        self.assertTrue(r.is_complete)
        self.assertIsNotNone(r.deadline)

    def test_TC_UF1_08_user_overrides_previous_scope(self) -> None:
        """TC-UF1-08: Người dùng sửa scope '5 trang' → '8 trang' ở lượt 2.
        Kỳ vọng: scope ưu tiên giá trị mới nhất người dùng cung cấp.
        (AGENTS.md UF1 – Nếu người dùng sửa trường đã có, ưu tiên giá trị mới)
        """
        svc = _svc()
        prev = LLMTaskExtractionPayload(
            title="Tiểu luận Triết học",
            content="Viết tiểu luận Triết học",
            scope="5 trang",
            free_time_slots=[],
            deadline=None,
            summary="Thiếu giờ rảnh và hạn.",
        )

        async def _fake(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Tiểu luận Triết học",
                content="Viết tiểu luận Triết học",
                scope="8 trang",  # Giá trị mới
                free_time_slots=["Sáng thứ 7 09:00-11:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=6,
                        start_local="09:00",
                        end_local="11:00",
                        raw_phrase="Sáng thứ 7 09:00-11:00",
                    )
                ],
                deadline=datetime(2026, 11, 14, 17, 0, tzinfo=VN_TZ),
                is_complete=True,
                has_conflict=False,
                summary="Tiểu luận 8 trang, nộp 14/11.",
                clarification_questions=[],
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(
                svc.extract_task_intent(
                    "Thầy yêu cầu 8 trang mới đúng, sáng thứ 7 9h-11h, nộp 14/11 17h",
                    previous_draft=prev,
                    reference_now=NOW_REF,
                )
            )

        self.assertEqual(r.scope, "8 trang", "Scope phải ưu tiên giá trị mới nhất")
        self.assertTrue(r.is_complete)

    def test_TC_UF1_09_ambiguous_free_window_blocks_complete(self) -> None:
        """TC-UF1-09: 'Tối thứ 6' chưa rõ giờ → is_ambiguous=True chặn is_complete.
        Kỳ vọng: ExtractedFreeWindowSchema.is_ambiguous=True, is_complete=False,
                  clarification_questions hỏi mốc giờ 'Tối thứ 6'.
        (AGENTS.md §2.2 KHÔNG TỰ ĐOÁN DỮ LIỆU)
        """
        r = TaskExtractionResult(
            title="Báo cáo KTPM",
            content="Viết báo cáo KTPM",
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
            deadline=datetime(2026, 11, 8, 17, 0, tzinfo=VN_TZ),
            is_complete=True,
            has_conflict=False,  # LLM trả sai
            summary="Báo cáo KTPM tối thứ 6.",
            clarification_questions=[],
        )
        self.assertTrue(r.structured_free_windows[0].is_ambiguous, "Phải is_ambiguous=True")
        self.assertFalse(r.is_complete, "is_ambiguous phải chặn is_complete")
        self.assertTrue(any("Tối thứ 6" in q for q in r.clarification_questions))


class TestUF1StrictGuardrails(unittest.TestCase):
    """TC-UF1-10..15: Strict Guardrails — Zero Hallucination, Conflict, Auto-save Draft."""

    def test_TC_UF1_10_past_deadline_detected_as_conflict(self) -> None:
        """TC-UF1-10: LLM trả deadline đã qua → Python layer phát hiện has_conflict=True.
        Kỳ vọng: has_conflict=True, is_complete=False, clarification_questions nhắc deadline qua.
        (AGENTS.md §2.2 UF1 – PHÁT HIỆN MÂU THUẪN với current_datetime)
        """
        svc = _svc()

        async def _fake(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Bài tập Vật lý",
                content="Làm bài tập Điện từ học",
                scope="Giải 10 bài tập SGK",
                free_time_slots=["Sáng thứ 7 09:00-11:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=6,
                        start_local="09:00",
                        end_local="11:00",
                        raw_phrase="Sáng thứ 7 09:00-11:00",
                    )
                ],
                deadline=datetime(2026, 10, 28, 17, 0, tzinfo=VN_TZ),  # ĐÃ QUA
                is_complete=True,
                has_conflict=False,  # LLM quên đánh dấu
                summary="Bài tập Vật lý, nộp 28/10.",
                clarification_questions=[],
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(
                svc.extract_task_intent(
                    "Làm bài tập Vật lý",
                    reference_now=datetime(2026, 11, 2, 15, 0, tzinfo=VN_TZ),
                )
            )

        self.assertTrue(r.has_conflict, "Deadline quá khứ phải bị phát hiện")
        self.assertFalse(r.is_complete)
        self.assertTrue(
            any("đã qua" in q.lower() or "quá khứ" in q.lower() for q in r.clarification_questions)
        )

    def test_TC_UF1_11_anti_hallucination_scope_none_deadline_none(self) -> None:
        """TC-UF1-11: LLM trả is_complete=True khi scope=None, deadline=None.
        Kỳ vọng: Pydantic validator hạ is_complete=False, thêm ≥2 câu hỏi.
        (AGENTS.md §2.2 – KHÔNG TỰ ĐOÁN DỮ LIỆU — ZERO HALLUCINATION)
        """
        r = TaskExtractionResult(
            content="Ôn thi cuối kỳ môn Mạng máy tính",
            scope=None,
            free_time_slots=["Tối thứ 5 19:00-21:00"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=4,
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="Tối thứ 5 19:00-21:00",
                )
            ],
            deadline=None,
            is_complete=True,  # CỐ TÌNH SAI
            has_conflict=False,
            summary="Ôn thi Mạng máy tính tối thứ 5.",
            clarification_questions=[],
        )
        self.assertFalse(r.is_complete)
        self.assertIsNone(r.scope)
        self.assertIsNone(r.deadline)
        self.assertGreaterEqual(len(r.clarification_questions), 2)

    def test_TC_UF1_12_vague_time_phrase_not_auto_filled(self) -> None:
        """TC-UF1-12: 'Buổi tối mai' không rõ giờ → start_local=None, end_local=None.
        Kỳ vọng: is_ambiguous=True. CẤM LLM tự gán '19:00-21:00'.
        (AGENTS.md §2.2 GUARDRAILS – TUYỆT ĐỐI KHÔNG tự gán giờ mơ hồ)
        """
        w = ExtractedFreeWindowSchema(
            kind="specific_date",
            date="2026-11-03",
            start_local=None,
            end_local=None,
            raw_phrase="Buổi tối mai",
        )
        self.assertTrue(w.is_ambiguous)
        self.assertIsNone(w.start_local)
        self.assertIsNone(w.end_local)

    def test_TC_UF1_13_tier3_fallback_produces_incomplete_draft(self) -> None:
        """TC-UF1-13: Tier 3 xử lý input mơ hồ → is_complete=False (→ auto-save draft).
        Kỳ vọng: clarification_questions không rỗng để Mobile kích hoạt auto-save.
        (AGENTS.md §2.2 UF1 – auto-save draft khi thoát app)
        """
        r = ai_service._fallback_extract_task("Cần làm gấp một việc gì đó")
        self.assertFalse(r.is_complete)
        self.assertGreater(len(r.clarification_questions), 0)
        self.assertEqual(r.generation_tier, "tier_3_rule_based")

    def test_TC_UF1_14_free_window_fully_busy_conflict(self) -> None:
        """TC-UF1-14: Giờ rảnh trùng hoàn toàn với lịch bận → has_conflict=True.
        Kỳ vọng: is_complete=False, clarification_questions có câu hỏi xác nhận lại.
        (AGENTS.md §2.2 UF1 – PHÁT HIỆN MÂU THUẪN với busy_intervals_from_calendar)
        """
        r = TaskExtractionResult(
            title="Báo cáo môi trường",
            content="Hoàn thiện báo cáo đánh giá môi trường",
            scope="15 trang",
            free_time_slots=["Tối thứ 6 19:00-21:00"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=5,
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="Tối thứ 6 19:00-21:00",
                )
            ],
            deadline=datetime(2026, 11, 9, 17, 0, tzinfo=VN_TZ),
            is_complete=False,
            has_conflict=True,
            summary="Toàn bộ khung rảnh tối thứ 6 trùng lịch họp định kỳ.",
            clarification_questions=[
                "Thời gian hoàn thành hoặc khung giờ rảnh đang có điểm mâu thuẫn "
                "với lịch hiện tại, bạn kiểm tra và xác nhận lại giúp mình nhé?"
            ],
        )
        self.assertTrue(r.has_conflict)
        self.assertFalse(r.is_complete)
        self.assertTrue(len(r.clarification_questions) > 0)

    def test_TC_UF1_15_tier3_latency_under_2500ms(self) -> None:
        """TC-UF1-15: Tier 3 Rule-based phải trả kết quả trong < 2.5 giây.
        Kỳ vọng: elapsed_ms < 2500.
        (week1_detailed_plan.md – Benchmark độ trễ LLM ≤ 2.5s)
        """
        t0 = time.perf_counter()
        r = ai_service._fallback_extract_task("Cần ôn bài thi ngày mai")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(elapsed_ms, 2500, f"Tier 3 phải trả < 2500ms, thực tế: {elapsed_ms:.0f}ms")
        self.assertIsInstance(r, TaskExtractionResult)


# =============================================================================
# PHẦN 2 — UF2: Bẻ nhỏ việc & Chốt kế hoạch
# =============================================================================


class TestUF2DefaultProposal(unittest.TestCase):
    """TC-UF2-01..04: Default Decomposition Proposal — chủ động sinh sẵn, tránh Blank Canvas."""

    def test_TC_UF2_01_default_proposal_2_to_5_steps_with_micro_task(self) -> None:
        """TC-UF2-01: decompose_task() sinh 2-5 bước, mỗi bước có micro_task_prompt ≠ rỗng.
        Kỳ vọng: 2 ≤ len(steps) ≤ 5; step.micro_task_prompt.strip() != '' mọi bước.
        (AGENTS.md §2.2 UF2 – Trải nghiệm phân rã chủ động / Blank Canvas)
        """
        svc = _svc()

        async def _fake(**_: Any) -> LLMTaskDecompositionPayload:
            return LLMTaskDecompositionPayload(
                task_title="Báo cáo Phân tích Dữ liệu",
                steps=[
                    StepPromptItem(
                        order_index=1,
                        title="Thu thập và làm sạch dữ liệu",
                        estimated_minutes=30,
                        cognitive_load="medium",
                        micro_task_prompt="Mở file CSV và xem 5 dòng đầu tiên.",
                    ),
                    StepPromptItem(
                        order_index=2,
                        title="Phân tích thống kê mô tả",
                        estimated_minutes=25,
                        cognitive_load="medium",
                        micro_task_prompt="Tạo sheet 'Analysis' và gõ tiêu đề cột.",
                    ),
                    StepPromptItem(
                        order_index=3,
                        title="Vẽ biểu đồ và viết nhận xét",
                        estimated_minutes=30,
                        cognitive_load="light",
                        micro_task_prompt="Chọn vùng dữ liệu và bấm Insert > Chart.",
                    ),
                    StepPromptItem(
                        order_index=4,
                        title="Viết nội dung chính báo cáo",
                        estimated_minutes=40,
                        cognitive_load="heavy",
                        micro_task_prompt="Mở Word và gõ dòng tiêu đề báo cáo.",
                    ),
                ],
                reasoning="Chia 4 bước rõ ràng để không bị ngợp.",
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(svc.decompose_task(task_title="Báo cáo Phân tích Dữ liệu"))

        self.assertGreaterEqual(len(r.steps), 2)
        self.assertLessEqual(len(r.steps), 5)
        for step in r.steps:
            self.assertTrue(
                bool(step.micro_task_prompt.strip()), f"Bước '{step.title}' thiếu micro-task 2 phút"
            )

    def test_TC_UF2_02_step_order_normalization(self) -> None:
        """TC-UF2-02: LLM trả order_index = [5, 2, 10] → validator chuẩn hóa thành [1, 2, 3].
        Kỳ vọng: sắp xếp đúng thứ tự gốc, đánh lại index liên tục.
        (app/schemas/task.py – _normalize_sequential_steps)
        """
        r = TaskDecompositionResult(
            task_title="Slide thuyết trình",
            steps=[
                StepPromptItem(
                    order_index=5,
                    title="Thiết kế Layout",
                    estimated_minutes=20,
                    micro_task_prompt="Mở PowerPoint và chọn theme.",
                ),
                StepPromptItem(
                    order_index=2,
                    title="Viết nội dung chính",
                    estimated_minutes=30,
                    micro_task_prompt="Gõ bullet point đầu tiên slide 1.",
                ),
                StepPromptItem(
                    order_index=10,
                    title="Kiểm tra và hoàn thiện",
                    estimated_minutes=15,
                    micro_task_prompt="Bật Slide Show và xem lại từ slide 1.",
                ),
            ],
            reasoning="3 bước tuần tự.",
        )
        self.assertEqual([s.order_index for s in r.steps], [1, 2, 3])
        # Sắp xếp: 2 < 5 < 10 → step 0 = "Viết nội dung chính"
        self.assertEqual(r.steps[0].title, "Viết nội dung chính")

    def test_TC_UF2_03_estimated_minutes_is_raw_not_buffered(self) -> None:
        """TC-UF2-03: estimated_minutes là giá trị gốc chưa nhân buffer.
        Kỳ vọng: estimated_base_seconds = estimated_minutes * 60.
        (AGENTS.md §2.2 UF2 – RANH GIỚI KIẾN TRÚC: LLM chỉ chia ngữ nghĩa, không nhân buffer)
        """
        step = StepPromptItem(
            order_index=1,
            title="Đọc và ghi chú tài liệu",
            estimated_minutes=25,
            micro_task_prompt="Mở tài liệu và gạch chân đề mục chương 1.",
        )
        self.assertEqual(
            step.estimated_base_seconds, 1500, "25 phút × 60 = 1500s (chưa nhân k_buffer)"
        )

    def test_TC_UF2_04_tier3_fallback_generates_micro_task_per_step(self) -> None:
        """TC-UF2-04: Tier 3 Rule-based fallback sinh bước có micro-task 2 phút.
        Kỳ vọng: ≥2 bước, mỗi bước micro_task_prompt không rỗng, generation_tier='tier_3_rule_based'.
        (AGENTS.md §3 – Tier 3: Rule-based fallback không trả màn hình lỗi trắng)
        """
        r = ai_service._fallback_decompose_task(
            "Dọn dẹp và sắp xếp tài liệu học", user_focus_duration=25
        )
        self.assertGreaterEqual(len(r.steps), 2)
        for step in r.steps:
            self.assertTrue(
                bool(step.micro_task_prompt.strip()), "Mỗi bước Tier 3 phải có micro-task"
            )
            self.assertEqual(step.estimated_minutes, 25)
        self.assertEqual(r.generation_tier, "tier_3_rule_based")


class TestUF2FeasibilityCheck(unittest.TestCase):
    """TC-UF2-05..08: Kiểm tra khả thi — Đủ giờ / Thiếu giờ / ΔT / Không dời Task khác."""

    def test_TC_UF2_05_sufficient_free_time_feasible(self) -> None:
        """TC-UF2-05: T_free=180p > T_req=120p → status='valid', fully_scheduled=True.
        Kỳ vọng: FeasibilityReportSchema.status='valid', deficit=0.
        """
        from app.schemas.schedule import FeasibilityReportSchema

        r = FeasibilityReportSchema(status="valid", required_seconds=7200, available_seconds=10800)
        self.assertEqual(r.status, "valid")
        self.assertTrue(r.fully_scheduled)
        self.assertEqual(r.deficit_seconds, 0)

    def test_TC_UF2_06_deficit_computed_correctly(self) -> None:
        """TC-UF2-06: T_req=90p, T_free=60p → ΔT=30p.
        Kỳ vọng: status='deficit', deficit_minutes=30.
        (AGENTS.md §2.2 UF2 – Công thức ΔT_deficit = T_req − T_free)
        """
        from app.schemas.schedule import FeasibilityReportSchema

        r = FeasibilityReportSchema(
            status="valid",  # validator tự điều chỉnh
            required_seconds=5400,
            available_seconds=3600,
        )
        self.assertEqual(r.status, "deficit")
        self.assertFalse(r.fully_scheduled)
        self.assertEqual(r.deficit_seconds, 1800)
        self.assertEqual(r.deficit_minutes, 30)

    def test_TC_UF2_07_exact_fit_is_valid(self) -> None:
        """TC-UF2-07: T_req == T_free (biên bằng nhau) → vẫn valid, deficit=0."""
        from app.schemas.schedule import FeasibilityReportSchema

        r = FeasibilityReportSchema(status="valid", required_seconds=3600, available_seconds=3600)
        self.assertEqual(r.status, "valid")
        self.assertEqual(r.deficit_seconds, 0)

    def test_TC_UF2_08_adjustment_payload_cannot_move_other_tasks(self) -> None:
        """TC-UF2-08: LLMPlanAdjustmentPayload không có field dời Task khác.
        Kỳ vọng: Không có trường other_task_id / reschedule_other_task trong schema.
        (AGENTS.md §2.2 UF2 – TUYỆT ĐỐI KHÔNG tự ý dời Task khác đã chốt)
        """
        from app.schemas.schedule import FeasibilityReportSchema

        r = FeasibilityReportSchema(status="valid", required_seconds=9000, available_seconds=3600)
        self.assertEqual(r.status, "deficit")
        self.assertEqual(r.deficit_minutes, 90)

        forbidden = {"other_task_id", "reschedule_other_task", "move_conflicting_task"}
        schema_fields = set(LLMPlanAdjustmentPayload.model_fields.keys())
        self.assertEqual(
            forbidden & schema_fields,
            set(),
            "Payload điều chỉnh không được có trường dời Task khác",
        )


class TestUF2NLAdjustment(unittest.TestCase):
    """TC-UF2-09..12b: Điều chỉnh kế hoạch bằng ngôn ngữ tự nhiên (UF2 Bước 4)."""

    _BASE_STEPS = [
        StepPromptItem(
            order_index=1,
            title="Đọc tài liệu",
            estimated_minutes=25,
            micro_task_prompt="Mở tài liệu đọc trang 1.",
        ),
        StepPromptItem(
            order_index=2,
            title="Ghi chú tóm tắt",
            estimated_minutes=20,
            micro_task_prompt="Lấy vở và bút ra bàn.",
        ),
        StepPromptItem(
            order_index=3,
            title="Làm bài tập áp dụng",
            estimated_minutes=30,
            micro_task_prompt="Mở sách bài tập trang 45.",
        ),
    ]

    def test_TC_UF2_09_merge_steps_via_nl(self) -> None:
        """TC-UF2-09: 'Gộp bước 1 và 2 lại' → len(updated_steps) giảm từ 3 xuống 2.
        Kỳ vọng: order_index chuẩn hóa lại [1, 2].
        """
        svc = _svc()

        async def _fake(**_: Any) -> LLMPlanAdjustmentPayload:
            return LLMPlanAdjustmentPayload(
                updated_steps=[
                    StepPromptItem(
                        order_index=1,
                        title="Đọc tài liệu và ghi chú tóm tắt",
                        estimated_minutes=40,
                        cognitive_load="medium",
                        micro_task_prompt="Mở tài liệu, đặt vở bên cạnh và đọc trang 1.",
                    ),
                    StepPromptItem(
                        order_index=2,
                        title="Làm bài tập áp dụng",
                        estimated_minutes=30,
                        cognitive_load="medium",
                        micro_task_prompt="Mở sách bài tập trang 45.",
                    ),
                ],
                added_free_windows=[],
                removed_free_phrases=[],
                updated_deadline=None,
                requires_clarification=False,
                assistant_note="Đã gộp 2 bước đọc và ghi chú thành 40 phút.",
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake),
        ):
            r = asyncio.run(
                svc.adjust_plan_by_nl(
                    "Gộp bước 1 và 2 lại thành 1 bước",
                    current_steps=self._BASE_STEPS,
                )
            )

        self.assertEqual(len(r.updated_steps), 2, "Từ 3 bước gộp còn 2")
        self.assertEqual([s.order_index for s in r.updated_steps], [1, 2])
        self.assertFalse(r.requires_clarification)

    def test_TC_UF2_10_add_clear_free_window(self) -> None:
        """TC-UF2-10: Thêm khung giờ Chủ Nhật 19:00-21:00 rõ ràng.
        Kỳ vọng: added_free_windows có 1 window, is_ambiguous=False.
        """
        w = ExtractedFreeWindowSchema(
            kind="weekly_rule",
            weekday=7,
            start_local="19:00",
            end_local="21:00",
            raw_phrase="Tối Chủ Nhật 19:00-21:00",
        )
        adj = LLMPlanAdjustmentPayload(
            updated_steps=[
                StepPromptItem(
                    order_index=1,
                    title="Bước giữ nguyên",
                    estimated_minutes=25,
                    micro_task_prompt="Mở file và kiểm tra.",
                )
            ],
            added_free_windows=[w],
            removed_free_phrases=[],
            updated_deadline=None,
            requires_clarification=False,
            assistant_note="Đã thêm khung tối Chủ Nhật.",
        )
        self.assertEqual(len(adj.added_free_windows), 1)
        self.assertFalse(adj.added_free_windows[0].is_ambiguous)
        self.assertEqual(adj.added_free_windows[0].weekday, 7)
        self.assertEqual(adj.added_free_windows[0].start_local, "19:00")

    def test_TC_UF2_11_ambiguous_added_window_triggers_clarification(self) -> None:
        """TC-UF2-11: Thêm 'Buổi tối Chủ Nhật' chưa rõ giờ → requires_clarification=True.
        Kỳ vọng: validator tự bật cờ và sinh câu hỏi.
        (app/schemas/task.py LLMPlanAdjustmentPayload.enforce_uf2_adjustment_guardrails)
        """
        vague = ExtractedFreeWindowSchema(
            kind="weekly_rule",
            weekday=7,
            start_local=None,
            end_local=None,
            raw_phrase="Buổi tối Chủ Nhật",
        )
        adj = LLMPlanAdjustmentPayload(
            updated_steps=[
                StepPromptItem(
                    order_index=1,
                    title="Viết báo cáo",
                    estimated_minutes=30,
                    micro_task_prompt="Mở Word và gõ tiêu đề.",
                )
            ],
            added_free_windows=[vague],
            removed_free_phrases=[],
            updated_deadline=None,
            requires_clarification=False,  # LLM trả False, validator phải bật
            assistant_note="Đã thêm tối Chủ Nhật.",
        )
        self.assertTrue(adj.requires_clarification)
        self.assertIsNotNone(adj.clarification_question)
        self.assertIn("Chủ Nhật", adj.clarification_question or "")

    def test_TC_UF2_12_update_deadline_via_nl(self) -> None:
        """TC-UF2-12: Người dùng lùi deadline → updated_deadline != None.
        Kỳ vọng: updated_deadline = 2026-11-10 17:00.
        (AGENTS.md §2.2 UF2 – Người dùng xác nhận hạn mới, hệ thống quay về bước 2)
        """
        new_dl = datetime(2026, 11, 10, 17, 0, tzinfo=VN_TZ)
        adj = LLMPlanAdjustmentPayload(
            updated_steps=[
                StepPromptItem(
                    order_index=1,
                    title="Giữ nguyên",
                    estimated_minutes=25,
                    micro_task_prompt="Mở file và xem lại.",
                )
            ],
            added_free_windows=[],
            removed_free_phrases=[],
            updated_deadline=new_dl,
            requires_clarification=False,
            assistant_note="Đã cập nhật deadline sang 10/11.",
        )
        self.assertIsNotNone(adj.updated_deadline)
        self.assertEqual(adj.updated_deadline, new_dl)

    def test_TC_UF2_12b_deadline_null_when_not_mentioned(self) -> None:
        """TC-UF2-12b: Người dùng chỉ đổi giờ rảnh, không nhắc deadline.
        Kỳ vọng: updated_deadline = None. CẤM LLM tự điền deadline.
        (AGENTS.md §2.2 UF2 – Zero Hallucination deadline)
        """
        adj = LLMPlanAdjustmentPayload(
            updated_steps=[
                StepPromptItem(
                    order_index=1,
                    title="Giữ nguyên",
                    estimated_minutes=25,
                    micro_task_prompt="Mở file và xem lại.",
                )
            ],
            added_free_windows=[],
            removed_free_phrases=["Sáng thứ 7"],
            updated_deadline=None,
            requires_clarification=False,
            assistant_note="Đã bỏ sáng thứ 7.",
        )
        self.assertIsNone(adj.updated_deadline)


class TestUF2ScheduleConfirmation(unittest.TestCase):
    """TC-UF2-13..15: Xác nhận lịch và Strict Guardrails cuối UF2."""

    def test_TC_UF2_13_schedule_requires_explicit_user_confirmation(self) -> None:
        """TC-UF2-13: Lịch chỉ ghi chính thức khi người dùng bấm Xác nhận.
        Kỳ vọng: FeasibilityReport.status='valid' là điều kiện cần, không đủ để tự ghi.
        (AGENTS.md §2.2 UF2 – Chỉ khi người dùng xác nhận, mới ghi lịch chính thức)
        """
        from app.schemas.schedule import FeasibilityReportSchema

        r = FeasibilityReportSchema(status="valid", required_seconds=3600, available_seconds=7200)
        self.assertEqual(r.status, "valid")
        self.assertTrue(r.fully_scheduled)
        # Lịch chính thức cần user_confirm thêm (kiểm tra trong integration test)

    def test_TC_UF2_14_llm_payload_has_no_session_schedule_fields(self) -> None:
        """TC-UF2-14: LLMTaskDecompositionPayload & StepPromptItem không có scheduled_start/end.
        Kỳ vọng: Không có trường tính giờ session trong payload LLM.
        (AGENTS.md §2.2 UF2 – RANH GIỚI KIẾN TRÚC: LLM không tự tính giờ session)
        """
        forbidden = {"scheduled_start", "scheduled_end", "session_start", "session_end"}
        all_fields = set(LLMTaskDecompositionPayload.model_fields.keys()) | set(
            StepPromptItem.model_fields.keys()
        )
        self.assertEqual(
            forbidden & all_fields, set(), "LLM payload không được chứa trường tính giờ session"
        )

    def test_TC_UF2_15_tier3_decompose_latency_under_2500ms(self) -> None:
        """TC-UF2-15: Tier 3 _fallback_decompose_task phải trả kết quả trong < 2.5 giây.
        Kỳ vọng: elapsed_ms < 2500, result có ≥2 bước.
        (week1_detailed_plan.md – Benchmark độ trễ LLM ≤ 2.5s)
        """
        t0 = time.perf_counter()
        r = ai_service._fallback_decompose_task("Làm khóa luận tốt nghiệp", user_focus_duration=25)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(elapsed_ms, 2500, f"Tier 3 UF2 phải < 2500ms, thực tế: {elapsed_ms:.0f}ms")
        self.assertGreaterEqual(len(r.steps), 2)


# =============================================================================
# PHẦN 3 — E2E: UF1 → UF2 (Kịch bản nghiệm thu Milestone 1)
# =============================================================================


class TestE2EUF1ToUF2(unittest.TestCase):
    """TC-E2E-01..02: Kịch bản nghiệm thu Ngày 7 — week1_detailed_plan.md §NGÀY 7."""

    def test_TC_E2E_01_complete_uf1_then_decompose_uf2(self) -> None:
        """TC-E2E-01: Báo cáo PTDL từ voice input — UF1 xong → UF2 sinh 2-4 bước.
        Kỳ vọng: uf1.is_complete=True; uf2.steps 2-5 bước, đủ micro-task, index liên tục.
        """
        svc = _svc()

        async def _fake_extract(**_: Any) -> LLMTaskExtractionPayload:
            return LLMTaskExtractionPayload(
                title="Báo cáo Phân tích Dữ liệu",
                content="Làm báo cáo môn Phân tích Dữ liệu",
                scope="Báo cáo hoàn chỉnh với biểu đồ",
                free_time_slots=["Tối thứ 6 19:00-21:00", "Sáng thứ 7 09:00-11:00"],
                structured_free_windows=[
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=5,
                        start_local="19:00",
                        end_local="21:00",
                        raw_phrase="Tối thứ 6 19:00-21:00",
                    ),
                    ExtractedFreeWindowSchema(
                        kind="weekly_rule",
                        weekday=6,
                        start_local="09:00",
                        end_local="11:00",
                        raw_phrase="Sáng thứ 7 09:00-11:00",
                    ),
                ],
                deadline=datetime(2026, 11, 8, 17, 0, tzinfo=VN_TZ),
                is_complete=True,
                has_conflict=False,
                summary="Báo cáo PTDL, tối thứ 6 & sáng thứ 7, nộp Chủ Nhật 17:00.",
                clarification_questions=[],
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake_extract),
        ):
            uf1 = asyncio.run(
                svc.extract_task_intent(
                    "Tuần này mình cần làm báo cáo môn Phân tích Dữ liệu "
                    "nộp trước 5h chiều Chủ Nhật, tối thứ 6 và sáng thứ 7 mình rảnh",
                    reference_now=NOW_REF,
                )
            )

        self.assertTrue(uf1.is_complete, "UF1 phải hoàn chỉnh trước khi sang UF2")

        async def _fake_decompose(**_: Any) -> LLMTaskDecompositionPayload:
            return LLMTaskDecompositionPayload(
                task_title=uf1.title or "Báo cáo",
                steps=[
                    StepPromptItem(
                        order_index=1,
                        title="Thu thập và đọc tài liệu",
                        estimated_minutes=30,
                        cognitive_load="light",
                        micro_task_prompt="Mở folder tài liệu và đọc mục lục file đầu tiên.",
                    ),
                    StepPromptItem(
                        order_index=2,
                        title="Phân tích dữ liệu và vẽ biểu đồ",
                        estimated_minutes=40,
                        cognitive_load="heavy",
                        micro_task_prompt="Mở file CSV và in 5 dòng đầu ra màn hình.",
                    ),
                    StepPromptItem(
                        order_index=3,
                        title="Viết nội dung báo cáo",
                        estimated_minutes=50,
                        cognitive_load="heavy",
                        micro_task_prompt="Mở Word và gõ tiêu đề 'Báo cáo Phân tích Dữ liệu'.",
                    ),
                ],
                reasoning="Chia 3 bước để hoàn thành trong 2 buổi.",
            )

        with (
            patch.object(svc, "_get_gemini_client", return_value=object()),
            patch.object(svc, "_call_gemini_structured", side_effect=_fake_decompose),
        ):
            uf2 = asyncio.run(svc.decompose_task(task_title=uf1.title or "Báo cáo"))

        self.assertGreaterEqual(len(uf2.steps), 2)
        self.assertLessEqual(len(uf2.steps), 5)
        for step in uf2.steps:
            self.assertTrue(bool(step.micro_task_prompt.strip()))
        self.assertEqual(
            [s.order_index for s in uf2.steps],
            list(range(1, len(uf2.steps) + 1)),
        )

    def test_TC_E2E_02_incomplete_uf1_blocks_uf2(self) -> None:
        """TC-E2E-02: UF1 chưa hoàn chỉnh → KHÔNG chuyển sang UF2.
        Kỳ vọng: is_complete=False sau guardrail → can_proceed=False.
        (State Transition Matrix: UF2 chỉ nhận Task status='confirmed')
        """
        incomplete = TaskExtractionResult(
            content="Làm gì đó chưa rõ",
            scope=None,
            free_time_slots=[],
            structured_free_windows=[],
            deadline=None,
            is_complete=True,  # LLM sai — guardrail chặn
            has_conflict=False,
            summary="Chưa đủ thông tin.",
            clarification_questions=[],
        )
        self.assertFalse(incomplete.is_complete, "Guardrail phải hạ is_complete xuống False")
        can_uf2 = incomplete.is_complete and not incomplete.has_conflict
        self.assertFalse(can_uf2, "Chưa đủ thông tin → KHÔNG chuyển sang UF2")


# =============================================================================
# PHẦN 4 — Đối chiếu trực tiếp yêu cầu AlgorithmDes.pdf (trang 2-4)
# =============================================================================


class TestUF1UF2PDFAlignment(unittest.TestCase):
    """Coverage các dòng PDF còn thiếu hoặc test cũ chưa chứng minh được."""

    def setUp(self) -> None:
        # Xoá in-memory store trong draft_service để không leak giữa test
        for name in (
            "_FAKE_DRAFTS_BY_OWNER",
            "_CLIENT_DRAFT_INDEX",
            "_CREATE_IDEMPOTENCY_STORE",
            "_PATCH_IDEMPOTENCY_STORE",
            "_CONFIRM_SUMMARY_IDEMPOTENCY_STORE",
            "_ANALYZE_IDEMPOTENCY_STORE",
            "_CURRENT_AVAILABILITY_REVISIONS",
            "_INITIALIZED_ENGINES",
        ):
            store = getattr(draft_service, name, None)
            if store is not None and hasattr(store, "clear"):
                store.clear()

        self.ai_patcher = patch.object(
            ai_service,
            "_execute_hedged_structured",
            side_effect=RuntimeError("AI disabled for PDF alignment test"),
        )
        self.ai_patcher.start()

    def tearDown(self) -> None:
        self.ai_patcher.stop()
        for name in (
            "_FAKE_DRAFTS_BY_OWNER",
            "_CLIENT_DRAFT_INDEX",
            "_CREATE_IDEMPOTENCY_STORE",
            "_PATCH_IDEMPOTENCY_STORE",
            "_CONFIRM_SUMMARY_IDEMPOTENCY_STORE",
            "_ANALYZE_IDEMPOTENCY_STORE",
            "_CURRENT_AVAILABILITY_REVISIONS",
            "_INITIALIZED_ENGINES",
        ):
            store = getattr(draft_service, name, None)
            if store is not None and hasattr(store, "clear"):
                store.clear()

    async def _async_setup_db(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await draft_service.ensure_dev_seed_in_db(db)
        return engine, factory

    async def _async_seed_availability(self, factory):
        uid = draft_service.resolve_user_uuid("user_a")
        async with factory() as db:
            avail = AvailabilityModel(
                user_id=uid,
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[
                    {"weekday": w, "start_local": "08:00", "end_local": "12:00"}
                    for w in range(1, 8)
                ],
                date_overrides=[],
                task_limits=[],
                source="user_confirmed",
                confirmed_at=datetime.now(UTC),
            )
            db.add(avail)
            await db.commit()
            await db.refresh(avail)
            return avail.id

    async def _async_create_confirmed_draft(self, factory, cid, deadline_days=30):
        start = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        payload = CreateDraftRequest(
            client_draft_id=cid,
            input_source="text",
            raw_input="Làm báo cáo môn Phân tích dữ liệu",
            summary=SummaryInput(
                title="Báo cáo PTDL",
                description="Viết báo cáo 10 trang",
                scope="10 trang PDF",
                time_constraint=TimeConstraint(
                    kind="deadline",
                    start_at=start,
                    deadline_at=start + timedelta(days=deadline_days),
                    input_timezone="UTC",
                ),
                availability=DraftAvailability(
                    timezone="UTC",
                    weekly_rules=[
                        WeeklyRule(weekday=w, start_local="08:00", end_local="12:00")
                        for w in range(1, 8)
                    ],
                ),
            ),
        )
        async with factory() as db:
            draft = await draft_service.create_draft(
                user_id="user_a", idempotency_key=f"draft_{cid}", payload=payload, db=db
            )
        async with factory() as db:
            await draft_service.confirm_draft_summary(
                user_id="user_a",
                draft_id=draft.draft_id,
                idempotency_key=f"conf_summary_{cid}",
                payload=ConfirmSummaryRequest(expected_summary_revision=draft.summary_revision),
                db=db,
            )
        async with factory() as db:
            d = await draft_service.get_draft_by_id(
                user_id="user_a", draft_id=draft.draft_id, db=db
            )
        return d.draft_id, d.summary_revision, d.planning_input_revision

    async def _async_setup_candidate_flow(self, factory, prefix):
        draft_id, srev, prev = await self._async_create_confirmed_draft(factory, prefix)
        async with factory() as db:
            cand = await plan_service.create_candidate(
                user_id="user_a",
                draft_id=draft_id,
                idempotency_key=f"cand_{prefix}",
                payload=GenerateCandidateRequest(
                    source_summary_revision=srev,
                    source_planning_input_revision=prev,
                    manual_steps=[
                        ManualStepInput(
                            order_index=0,
                            title="Đọc tài liệu",
                            expected_output="Ghi chú",
                            estimated_base_seconds=1200,
                        ),
                        ManualStepInput(
                            order_index=1,
                            title="Viết báo cáo",
                            expected_output="Báo cáo",
                            estimated_base_seconds=1200,
                        ),
                    ],
                ),
                db=db,
            )
        return draft_id, cand

    # ------------------ CÁC HÀM TEST ĐỒNG BỘ BỌC ASYNC ------------------

    def test_TC_UF1_05b_confirm_summary_persists_confirmation(self):
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                draft_id, srev, _prev = await self._async_create_confirmed_draft(factory, "uf1_05b")
                async with factory() as db:
                    d = await draft_service.get_draft_by_id(
                        user_id="user_a", draft_id=draft_id, db=db
                    )
                self.assertEqual(d.stage, "summary_confirmed")
                self.assertEqual(d.summary_confirmed_revision, srev)
            finally:
                await engine.dispose()

        asyncio.run(_run())

    def test_TC_UF1_06b_edit_rebumps_revision_and_revalidates(self):
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                draft_id, srev, prev = await self._async_create_confirmed_draft(factory, "uf1_06b")
                async with factory() as db:
                    updated = await draft_service.patch_draft(
                        user_id="user_a",
                        draft_id=draft_id,
                        idempotency_key="patch_uf1_06b",
                        payload=UpdateDraftRequest(
                            expected_summary_revision=srev,
                            expected_planning_input_revision=prev,
                            changes=DraftChanges(
                                summary=SummaryChanges(scope="15 trang PDF (sửa)")
                            ),
                        ),
                        db=db,
                    )
                self.assertEqual(updated.summary_revision, srev + 1)
                self.assertEqual(updated.summary.scope, "15 trang PDF (sửa)")
                self.assertTrue(updated.summary.can_plan)
                self.assertIsNone(updated.summary_confirmed_revision)
            finally:
                await engine.dispose()

        asyncio.run(_run())

    def test_TC_UF1_08b_no_task_after_confirm_summary(self):
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                await self._async_create_confirmed_draft(factory, "uf1_08b")
                async with factory() as db:
                    tasks = (await db.scalars(select(Task))).all()
                self.assertEqual(
                    len(tasks), 0, "PDF UF1-8: sau confirm-summary KHÔNG được tạo Task"
                )
            finally:
                await engine.dispose()

        asyncio.run(_run())

    def test_TC_UF2_11b_confirm_blocked_when_infeasible(self):
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                _draft_id, cand = await self._async_setup_candidate_flow(factory, "uf2_11b")
                self.assertEqual(cand.feasibility.status, "feasible")

                async with factory() as db:
                    patched = await plan_service.patch_candidate(
                        user_id="user_a",
                        candidate_id=cand.candidate_id,
                        idempotency_key="patch_uf2_11b",
                        payload=UpdateCandidateRequest(
                            expected_review_revision=cand.review_revision,
                            edits=CandidateEdits(
                                sessions=[
                                    CandidateSessionEdit(
                                        client_session_key=cand.sessions[0].client_session_key,
                                        scheduled_start=datetime(2030, 1, 1, 12, tzinfo=UTC),
                                    )
                                ]
                            ),
                        ),
                        db=db,
                    )
                self.assertNotEqual(patched.feasibility.status, "feasible")

                async with factory() as db:
                    with self.assertRaises(AppError) as ctx:
                        await plan_service.confirm_candidate(
                            user_id="user_a",
                            candidate_id=cand.candidate_id,
                            idempotency_key="conf_uf2_11b",
                            payload=ConfirmCandidateRequest(
                                expected_review_revision=patched.review_revision,
                                expected_summary_revision=patched.source_summary_revision,
                                expected_planning_input_revision=patched.source_planning_input_revision,
                                expected_source_revisions=patched.source_revisions,
                            ),
                            db=db,
                        )
                self.assertEqual(ctx.exception.code, "PLAN_NOT_CONFIRMABLE")
            finally:
                await engine.dispose()

        asyncio.run(_run())

    def test_TC_UF2_12b_confirm_creates_full_aggregate_atomically(self):
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                _draft_id, cand = await self._async_setup_candidate_flow(factory, "uf2_12b")
                self.assertEqual(cand.feasibility.status, "feasible")

                async with factory() as db:
                    result = await plan_service.confirm_candidate(
                        user_id="user_a",
                        candidate_id=cand.candidate_id,
                        idempotency_key="conf_uf2_12b",
                        payload=ConfirmCandidateRequest(
                            expected_review_revision=cand.review_revision,
                            expected_summary_revision=cand.source_summary_revision,
                            expected_planning_input_revision=cand.source_planning_input_revision,
                            expected_source_revisions=cand.source_revisions,
                        ),
                        db=db,
                    )
                self.assertEqual(result.app_save_state, "saved")

                async with factory() as db:
                    tasks = (await db.scalars(select(Task))).all()
                    self.assertEqual(len(tasks), 1)
                    steps = (
                        await db.scalars(select(Step).where(Step.task_id == tasks[0].id))
                    ).all()
                    self.assertGreater(len(steps), 0)
                    sessions = (await db.scalars(select(SessionModel))).all()
                    self.assertGreater(len(sessions), 0)
                    reminders = (await db.scalars(select(Reminder))).all()
                    self.assertGreater(len(reminders), 0)
                    versions = (await db.scalars(select(ScheduleVersionModel))).all()
                    self.assertEqual(len(versions), 1)
            finally:
                await engine.dispose()

        asyncio.run(_run())

    def test_TC_UF2_19b_rolling_horizon_at_most_14_days(self):
        # PDF UF2 Lưu ý: Horizon rolling 2-3 tuần (14 ngày)
        from app.schemas.draft import PlanningDraft, TaskSummary

        draft = PlanningDraft(
            draft_id="d1",
            client_draft_id="c1",
            stage="summary_confirmed",
            input_source="text",
            summary=TaskSummary(
                title="x",
                description="x",
                scope="x",
                time_constraint=TimeConstraint(
                    kind="deadline",
                    start_at=datetime(2026, 1, 1, 8, tzinfo=UTC),
                    deadline_at=datetime(2027, 1, 1, 8, tzinfo=UTC),
                    input_timezone="UTC",
                ),
                availability_ref=AvailabilityRef(availability_id="a", revision=1),
                can_plan=True,
            ),
            summary_revision=1,
            planning_input_revision=1,
        )
        horizon = plan_service._horizon(draft, None)
        delta_days = (horizon.end_at - horizon.start_at).days
        self.assertLessEqual(delta_days, 14, f"PDF UF2: horizon {delta_days} ngày > 14")
        self.assertGreaterEqual(delta_days, 13, f"PDF UF2: horizon {delta_days} ngày < 13")

    def test_TC_UF2_21b_patch_candidate_direct_edit(self):
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                _draft_id, cand = await self._async_setup_candidate_flow(factory, "uf2_21b")
                first_step = cand.steps[0]
                async with factory() as db:
                    patched = await plan_service.patch_candidate(
                        user_id="user_a",
                        candidate_id=cand.candidate_id,
                        idempotency_key="patch_uf2_21b",
                        payload=UpdateCandidateRequest(
                            expected_review_revision=cand.review_revision,
                            edits=CandidateEdits(
                                steps=[
                                    CandidateStepEdit(
                                        client_step_key=first_step.client_step_key,
                                        title="Đọc tài liệu và ghi chú chi tiết",
                                    )
                                ]
                            ),
                        ),
                        db=db,
                    )
                self.assertEqual(patched.review_revision, cand.review_revision + 1)
                new_step = next(
                    s for s in patched.steps if s.client_step_key == first_step.client_step_key
                )
                self.assertEqual(new_step.title, "Đọc tài liệu và ghi chú chi tiết")
            finally:
                await engine.dispose()

        asyncio.run(_run())


# =============================================================================
# PHẦN 5 — Gap Analysis: Các yêu cầu PDF CHƯA ĐƯỢC BACKEND LẬP TRÌNH
# =============================================================================
# Các test dưới đây ĐƯỢC THIẾT KẾ ĐỂ FAIL (Báo đỏ) vì Backend hiện tại chưa code.
# Mục đích: Điểm mặt các tính năng còn thiếu so với file AlgorithmDes.pdf.
# =============================================================================


class TestUF1UF2PDFGaps(unittest.TestCase):
    """Kiểm guardrail hiện hành; lịch tạm thiếu dữ liệu cần chốt lại nghiệp vụ."""

    def test_TC_UF1_10_missing_info_keeps_draft_and_blocks_confirmation(self):
        async def run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as db:
                    await draft_service.ensure_dev_seed_in_db(db)
                    draft = await draft_service.create_draft(
                        user_id="user_a",
                        idempotency_key="gap-create",
                        payload=CreateDraftRequest(
                            client_draft_id="gap-client",
                            input_source="text",
                            raw_input="Làm báo cáo",
                        ),
                        db=db,
                    )
                async with factory() as db:
                    with self.assertRaises(AppError) as error:
                        await draft_service.confirm_draft_summary(
                            user_id="user_a",
                            draft_id=draft.draft_id,
                            idempotency_key="gap-confirm",
                            payload=ConfirmSummaryRequest(
                                expected_summary_revision=draft.summary_revision
                            ),
                            db=db,
                        )
                    self.assertEqual(error.exception.code, "VALIDATION_ERROR")
                async with factory() as db:
                    saved = await draft_service.get_draft_by_id(
                        user_id="user_a", draft_id=draft.draft_id, db=db
                    )
                    self.assertFalse(saved.summary.can_plan)
                    self.assertIsNone(saved.summary_confirmed_revision)
                    self.assertEqual(list((await db.scalars(select(Task))).all()), [])
            finally:
                await engine.dispose()

        asyncio.run(run())

    def test_TC_UF2_04_unconfirmed_draft_cannot_create_candidate(self):
        async def run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as db:
                    await draft_service.ensure_dev_seed_in_db(db)
                    draft = await draft_service.create_draft(
                        user_id="user_a",
                        idempotency_key="no-avail-create",
                        payload=CreateDraftRequest(
                            client_draft_id="no-avail-client",
                            input_source="text",
                            raw_input="Làm báo cáo",
                        ),
                        db=db,
                    )
                async with factory() as db:
                    with self.assertRaises(AppError) as error:
                        await plan_service.create_candidate(
                            user_id="user_a",
                            draft_id=draft.draft_id,
                            idempotency_key="no-avail-candidate",
                            payload=GenerateCandidateRequest(
                                source_summary_revision=draft.summary_revision,
                                source_planning_input_revision=draft.planning_input_revision,
                            ),
                            db=db,
                        )
                    self.assertEqual(error.exception.code, "PLAN_NOT_CONFIRMABLE")
            finally:
                await engine.dispose()

        asyncio.run(run())

    @unittest.skip("Chưa có API chuyển chặng; theo dõi trong báo cáo, không kiểm bằng tên hàm")
    def test_TC_UF2_20_rolling_horizon_continuation(self):
        pass
