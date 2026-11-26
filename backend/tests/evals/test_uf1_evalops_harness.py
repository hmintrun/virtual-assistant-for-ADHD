"""Trụ cột 7 — Offline Deterministic 3-Tier EvalOps Harness + Unit Tests Trụ cột 1c (Zero Overfitting).

Chạy 100% offline (< 5s, 0 live API call, không đọc file cache bên ngoài lúc chạy test):
- Suite 1: Dev Regression (31 transcript thật từ T01–T33 trừ T29/T30 im lặng + NO_SPEECH/empty guard),
  kiểm tra ITN + Mini-Duckling + 5 Universal Invariants + Tier 3 fallback + Zone 2, FPCR == 0.0%,
  và latency trung bình của tầng deterministic < 5.0 ms/mẫu.
- Suite 2: Unseen Holdout (30 kịch bản tiếng Việt đa miền hoàn toàn mới), kiểm chứng Temporal Grounding
  & 5 Universal Invariants đạt >= 95%.
- Suite 3: Adversarial & Negative Control (22 bẫy dương tính giả), kiểm chứng 100% pass (0 false positives).
- Suite 4: Unit tests 1-Hop Audio-Native (P1c): feature flag False/True, Files API cleanup (> 18MB),
  chứng minh đủ 5 Invariants (I1–I5) chặn hallucination trên đầu ra 1-Hop, và fallback mượt sang 2-Hop
  không rò rỉ PII.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from google.genai import types

from app.core.config import Settings
from app.schemas.task import (
    AudioNativeExtractionPayload,
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    TaskExtractionResult,
)
from app.services.ai_service import (
    UF1_AUDIO_NATIVE_SYSTEM_PROMPT,
    AIService,
    AudioNativeDisabledError,
    _requests_default_availability,
    ai_service,
)
from app.services.draft_service import _detect_zone2_deadline_confirmation_issue
from app.services.user_vocabulary_service import extract_salient_vocabulary_hints
from app.services.voice_service import (
    _is_no_speech_sentinel,
    _is_wav_pcm16_digital_silence,
)
from app.utils.temporal_parser import TemporalAnchor, extract_temporal_anchors
from app.utils.vietnamese_itn import normalize_vietnamese_transcript

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Thứ Ba 2026-11-24 02:35+07:00 (isoweekday = 2)
# -> hôm nay/tối nay = 2026-11-24 (T3)
# -> ngày mai/tối mai/Thứ 4 = 2026-11-25 (T4)
# -> mốt/Thứ 5 = 2026-11-26 (T5)
# -> Thứ 6 = 2026-11-27 (T6)
# -> Thứ 7 = 2026-11-28 (T7)
# -> Chủ nhật = 2026-11-29 (CN)
FROZEN_NOW = datetime(2026, 11, 24, 2, 35, tzinfo=VN_TZ)


# ==============================================================================
# SUITE 1 — DEV REGRESSION (31 REAL SPOKEN TRANSCRIPTS T01–T33 + NO_SPEECH GUARD)
# ==============================================================================


@dataclass(frozen=True, slots=True)
class DevVoiceTranscriptCase:
    test_id: str
    cache_name: str
    raw_transcript: str


DEV_SILENT_CASES: tuple[tuple[str, str, str], ...] = (
    ("T29", "f_000073", "empty_transcript"),
    ("T30", "f_000092", "no_speech_sentinel"),
)

DEV_REGRESSION_31_CASES: tuple[DevVoiceTranscriptCase, ...] = (
    DevVoiceTranscriptCase(
        "T01",
        "f_00007a",
        "Làm báo cáo thực tập khoảng năm trang, rảnh tối mai từ 7:00 đến 9:00 tối, hạn chót là 9:00 tối thứ bảy.",
    ),
    DevVoiceTranscriptCase(
        "T02",
        "f_000080",
        "Soạn 10 câu hỏi ôn tập môn hệ điều hành làm gọn một lèo trong tối thứ năm từ 19:00 đến 22:00.",
    ),
    DevVoiceTranscriptCase(
        "T03",
        "f_000083",
        "Tối nay từ 7:00 đến 9:30 thì mình viết đề cương khóa luận khoảng bốn trang, hẹn 10:00 tối chủ nhật.",
    ),
    DevVoiceTranscriptCase(
        "T04",
        "f_000086",
        "Giải hai đề thi thử rảnh sáng thứ năm từ 8:00 kém 15 đến 10:30 mỗi phiên thì khoảng 1 tiếng rưỡi hoặc là 45 phút hẹn cuối tuần này.",
    ),
    DevVoiceTranscriptCase(
        "T05",
        "f_00008a",
        "Viết đề cương năm phần, rảnh các ngày trong tuần từ 7:00 đến 9:00 tối từ thứ tư với lại sáng chủ nhật là từ 9:00 đến 11:00, hạn là tối chủ nhật tuần sau.",
    ),
    DevVoiceTranscriptCase(
        "T06",
        "f_00008d",
        "Ôn tập Flash API, SQL, AGMI, Docker compose, Flutter provider để viết B unit test. Rảnh tối nay từ 19:00 đến 21:00 hẹn 28 tháng 11.",
    ),
    DevVoiceTranscriptCase(
        "T07",
        "f_000070",
        "À thì tôi muốn tôi muốn làm bài thuyết trình môn AI khoảng tám slide. Kiểu kiểu như là vào thứ thứ năm từ từ 7:00 tối đến 9:00 tối hẹn tối thứ sáu",
    ),
    DevVoiceTranscriptCase(
        "T08",
        "f_000074",
        "Làm bài tiểu luận năm trang vào 7:00 tối thứ năm, à nhầm, sửa lại là tối thứ sáu thay vì thứ năm và làm tám trang nhé, hạn trưa chủ nhật.",
    ),
    DevVoiceTranscriptCase(
        "T09",
        "f_000075",
        "Có chức năng đăng nhập gồm hai màn hình rảnh chiều mai 2:00 đến 4:00. À quên, đổi thành 7:00 đến 9:00 tối mai đi. Hẹn thứ bảy. Thôi chốt hẹn tối chủ nhật cho chắc.",
    ),
    DevVoiceTranscriptCase(
        "T10",
        "f_000079",
        "Làm tài liệu tổng kết dự án nhưng đang phân vân không biết nên viết bản tóm tắt năm trang hay làm hẳn 10 trang kèm slide. Rảnh tối mai 19:00 đến 21:00 hẹn 29 tháng 11.",
    ),
    DevVoiceTranscriptCase(
        "T11",
        "f_00007c",
        "Thôi chốt làm bản tóm tắt năm trang thôi nhé.",
    ),
    DevVoiceTranscriptCase(
        "T12",
        "f_00007f",
        "Tổng hợp và phân tích toàn bộ dữ liệu khảo sát trải nghiệm người người dùng ứng dụng hỗ trợ sinh viên lập kế hoạch học tập môn công nghệ phần mềm năm nay. Viết khoảng 10 trang báo cáo. Rảnh tối thứ năm 7:00 đến 9:00. Hẹn tối Chủ Nhật.",
    ),
    DevVoiceTranscriptCase(
        "T13",
        "f_000082",
        "Ok. Chết rồi, gấp lắm rồi, phải hoàn thành bốn bài lab mạng máy tính. Mình chỉ rảnh đúng tối nay từ 20:00 đến 23:00 thôi. Hạn chót sáng mai 9:00 nộp.",
    ),
    DevVoiceTranscriptCase(
        "T14",
        "f_000085",
        "Ờm à ờm để xem nào ờ thì",
    ),
    DevVoiceTranscriptCase(
        "T15",
        "f_000077",
        "Ôn tập chương 5 môn Giải tích, hạn tối Chủ nhật tuần này nộp bài.",
    ),
    DevVoiceTranscriptCase(
        "T16",
        "f_000078",
        "Làm 15 bài tập trọng tâm, rảnh tối thứ Sáu từ 7:00 đến 9:00 tối.",
    ),
    DevVoiceTranscriptCase(
        "T17",
        "f_00007b",
        "Đọc và tóm tắt hai chương sách Kỹ năng mềm mình rảnh tối mai và tối thứ sáu từ 8:00 đến 9:30 tối.",
    ),
    DevVoiceTranscriptCase(
        "T18",
        "f_00007e",
        "Nhắc mình viết báo cáo đồ án.",
    ),
    DevVoiceTranscriptCase(
        "T19",
        "f_000081",
        "Làm bài tập cơ sở dữ liệu năm câu SQL hạng thứ bảy nhưng ngày mai tôi kẹt học trên trường cả sáng lẫn chiều rồi.",
    ),
    DevVoiceTranscriptCase(
        "T20",
        "f_000084",
        "Soạn sáu slide giới thiệu nhóm dùng khung giờ rảnh mặc định của tôi nhé, hạn tối thứ sáu hoàn thành.",
    ),
    DevVoiceTranscriptCase(
        "T21",
        "f_000088",
        "Việc bài thu hoạch bốn trang, rảnh 7:00 đến 9:00 tối hằng ngày. Hạn chót 20:00 ngày 20 tháng 11 năm 2026.",
    ),
    DevVoiceTranscriptCase(
        "T22",
        "f_00008b",
        "Làm slide thuyết trình sáu trang rảnh tối thứ tư ngày 27 tháng 11 từ 7:00 đến 9:00 tối hẹn tối chủ nhật.",
    ),
    DevVoiceTranscriptCase(
        "T23",
        "f_00008f",
        "Chỉnh sửa lại CV xin thực tập khoảng hai trang, tôi rảnh lúc nào, tiện buổi tối cũng được, hạn cuối tuần này.",
    ),
    DevVoiceTranscriptCase(
        "T24",
        "f_000091",
        "Hoàn thành ba bài tập Python, hạn chót tối thứ năm ngày 26 tháng 11, còn giờ rảnh thì sang tuần tối chủ nhật ngày 29 tháng 11 mới rảnh.",
    ),
    DevVoiceTranscriptCase(
        "T25",
        "f_000093",
        "Hoàng Thế Anh, nộp trang báo cáo nghiên cứu trước ngày 30 tháng 11. Tôi rảnh sáng thứ tư 8:00 đến 10:00, chiều thứ năm 2:00 đến 4:00, tối thứ bảy 7:00 đến 9:00, trừ thứ sáu và ngoại trừ sáng chủ nhật.",
    ),
    DevVoiceTranscriptCase(
        "T26",
        "f_000089",
        "Tối mai 7:00 đến 9:00 làm xong năm trang tiểu luận triết học để kịp hạn 10:00 tối thứ Sáu nộp cho cô nhé.",
    ),
    DevVoiceTranscriptCase(
        "T27",
        "f_00008c",
        "Làm nhóm bài tập xác suất thống kê rảnh tối thứ năm từ 7:00 đến 9:00, hẹn tối thứ bảy.",
    ),
    DevVoiceTranscriptCase(
        "T28",
        "f_000090",
        "Chuẩn bị câu hỏi phỏng vấn người dùng gồm 10 câu. Rảnh tối mai 7:00 đến 9:00 tối, hẹn thứ sáu tuần này.",
    ),
    DevVoiceTranscriptCase(
        "T31",
        "f_00006e",
        "À thì tôi muốn tôi",
    ),
    DevVoiceTranscriptCase(
        "T32",
        "f_000076",
        "Tuần này mình có một việc hơi nặng, đó là chuẩn bị một buổi bảo vệ đồ án giữa kỳ. Cụ thể là phải hoàn thành, hoàn thiện lại phần kiến thức mà hệ thống với tầm tám trang tài liệu thiết kế và trình bày 12 slide. Khoảng thời gian trình bày của mình thì để coi, vào tối thứ tư ngày mai mình rảnh từ 7:30 đến 9:30. Tối thứ năm từ 8:00 đến 10:00. À à à từ từ. Tối thứ năm mình bận đi học nhóm rồi thì coi như bỏ thứ năm ra. Chuyển sang tối thứ sáu từ 7:00 đến khoảng 9:30 tối. Thì hạn chót phải làm xong toàn bộ để gửi giảng viên là trước 5:00 chiều thứ bảy ngày 28 tháng 11.",
    ),
    DevVoiceTranscriptCase(
        "T33",
        "f_00007d",
        "Soạn 10 câu hỏi ôn tập hệ điều hành làm gọn một lèo trong tối thứ năm từ 19:00 đến 20:00 đến 22:00.",
    ),
)

DEV_CASE_MAP: dict[str, DevVoiceTranscriptCase] = {c.test_id: c for c in DEV_REGRESSION_31_CASES}


def _build_silent_wav_pcm16(num_samples: int = 1600, sample_rate: int = 16000) -> bytes:
    pcm_data = b"\x00\x00" * num_samples
    fmt_chunk = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    data_chunk = struct.pack("<4sI", b"data", len(pcm_data)) + pcm_data
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    return struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE") + fmt_chunk + data_chunk


@pytest.mark.asyncio
async def test_suite1_no_speech_and_empty_guards_t29_t30() -> None:
    """Kiểm chứng T29/T30 (NO_SPEECH / empty guard) ở cả tầng âm thanh lẫn tầng trích xuất UF1."""
    assert len(DEV_SILENT_CASES) == 2
    assert {tid for tid, _, _ in DEV_SILENT_CASES} == {"T29", "T30"}

    # T30 sentinel checks
    for sentinel in ("[NO_SPEECH]", "  [no_speech] ", '"[SILENCE]"', "[inaudible]."):
        assert _is_no_speech_sentinel(sentinel) is True

    # T29 PCM16 digital silence check
    silent_wav = _build_silent_wav_pcm16()
    assert _is_wav_pcm16_digital_silence(silent_wav) is True

    # Empty / whitespace input never hallucinates a task
    for blank in ("", "   ", "\n\t "):
        res = await ai_service.extract_task_intent(
            blank,
            timezone="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        assert res.is_complete is False
        assert res.content is None
        assert res.title is None
        assert res.generation_tier == "tier_3_rule_based"


def test_suite1_all_31_real_transcripts_zero_overfitting_tier3_and_zone2() -> None:
    """Kiểm chứng toàn bộ 31 transcript thật: Zero Overfitting ở ITN, Tier 3 an toàn, Zone 2 bắt đồng âm."""
    assert len(DEV_REGRESSION_31_CASES) == 31
    expected_ids = {f"T{i:02d}" for i in range(1, 34)} - {"T29", "T30"}
    assert set(DEV_CASE_MAP.keys()) == expected_ids

    for case in DEV_REGRESSION_31_CASES:
        norm = normalize_vietnamese_transcript(case.raw_transcript)
        assert isinstance(norm, str) and norm.strip()

        # Zero Overfitting: ITN tuyệt đối không ghi đè "hẹn"/"hạng" -> "hạn" hay sửa từ kỹ thuật
        if "hẹn" in case.raw_transcript.lower():
            assert "hẹn" in norm.lower(), f"{case.test_id} bị ghi đè 'hẹn'"
        if "hạng" in case.raw_transcript.lower():
            assert "hạng" in norm.lower(), f"{case.test_id} bị ghi đè 'hạng'"
        if "Flash API" in case.raw_transcript:
            assert "Flash API" in norm
            assert "FastAPI" not in norm

        # Tier 3 fallback luôn bảo toàn an toàn is_complete=False
        t3 = ai_service._fallback_extract_task(
            norm,
            timezone="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        assert t3.is_complete is False, case.test_id
        assert t3.generation_tier == "tier_3_rule_based"

    # Zone 2 phải phát hiện đồng âm "hẹn"/"hạng" + mốc thời gian ở các mẫu không có từ khóa gặp người
    for z2_tid in ("T03", "T06", "T07", "T09", "T10", "T12", "T19", "T27", "T28"):
        issue = _detect_zone2_deadline_confirmation_issue(
            DEV_CASE_MAP[z2_tid].raw_transcript,
            timezone_name="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        assert issue is not None, f"Zone 2 bỏ sót {z2_tid}"
        assert issue.code == "AI_DEADLINE_CONFIRMATION_NEEDED"
        assert issue.blocking is True


def test_suite1_spotlight_cases_and_zero_fpcr() -> None:
    """Kiểm chứng chi tiết T02, T03, T08, T09, T22, T23, T24, T26, T32, T33 và assert FPCR == 0.0%."""
    false_positive_completions = 0
    negative_gate_trials = 0

    def _record_gate(is_blocked: bool, label: str) -> None:
        nonlocal false_positive_completions, negative_gate_trials
        negative_gate_trials += 1
        if not is_blocked:
            false_positive_completions += 1
            pytest.fail(f"FPCR violation on {label}: leaked as complete/plannable")

    # --- T02: Cửa sổ tối thứ Năm 19:00-22:00 ("làm gọn một lèo trong..." -> suy kind='window' + is_complete=True) ---
    t02_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T02"].raw_transcript)
    t02_anchors = extract_temporal_anchors(t02_norm, reference_now=FROZEN_NOW)
    assert len(t02_anchors) == 1
    assert (t02_anchors[0].iso_weekday, t02_anchors[0].resolved_date) == (4, "2026-11-26")
    assert (t02_anchors[0].start_local, t02_anchors[0].end_local) == ("19:00", "22:00")
    t02_window_inferred = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Soạn câu hỏi ôn tập hệ điều hành",
            content=t02_norm,
            scope="10 câu hỏi ôn tập",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    weekday=4,
                    date="2026-11-26",
                    start_local="19:00",
                    end_local="22:00",
                    raw_phrase="tối Thứ 5 từ 19:00 đến 22:00",
                )
            ],
            deadline=None,
            is_complete=True,
            summary="Soạn 10 câu hỏi.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t02_norm,
        temporal_anchors=t02_anchors,
    )
    assert t02_window_inferred.time_constraint is not None
    assert t02_window_inferred.time_constraint.kind == "window"
    assert t02_window_inferred.is_complete is True
    assert t02_window_inferred.clarification_questions == []

    # Cổng âm thiếu hạn chót thật (không có cụm "làm gọn một lèo trong...") bắt buộc giữ is_complete=False
    t02_no_window_text = "Soạn 10 câu hỏi ôn tập môn hệ điều hành, rảnh tối thứ năm từ 19:00 đến 22:00."
    t02_missing_dl = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Soạn câu hỏi ôn tập hệ điều hành",
            content=t02_no_window_text,
            scope="10 câu hỏi ôn tập",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    weekday=4,
                    date="2026-11-26",
                    start_local="19:00",
                    end_local="22:00",
                    raw_phrase="tối Thứ 5 từ 19:00 đến 22:00",
                )
            ],
            deadline=None,
            is_complete=True,
            summary="Soạn 10 câu hỏi.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t02_no_window_text,
    )
    _record_gate(not t02_missing_dl.is_complete, "T02_missing_deadline")

    # --- T03: "Tối nay từ 7:00 đến 9:30 ... hẹn 10:00 tối chủ nhật" ---
    t03_raw = DEV_CASE_MAP["T03"].raw_transcript
    t03_norm = normalize_vietnamese_transcript(t03_raw)
    assert "19:00 đến 21:30" in t03_norm
    assert "hẹn 22:00 chủ nhật" in t03_norm
    t03_anchors = extract_temporal_anchors(t03_norm, reference_now=FROZEN_NOW)
    assert (t03_anchors[0].resolved_date, t03_anchors[0].start_local, t03_anchors[0].end_local) == (
        "2026-11-24",
        "19:00",
        "21:30",
    )
    t03_z2 = _detect_zone2_deadline_confirmation_issue(
        t03_raw, timezone_name="Asia/Ho_Chi_Minh", reference_now=FROZEN_NOW
    )
    assert t03_z2 is not None and t03_z2.blocking is True
    _record_gate(t03_z2.blocking is True, "T03_zone2_homophone_gate")

    # --- T08: Tự đính chính "vào 7:00 tối thứ năm, à nhầm, sửa lại là tối thứ sáu thay vì thứ năm" ---
    t08_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T08"].raw_transcript)
    t08_anchors = extract_temporal_anchors(t08_norm, reference_now=FROZEN_NOW)
    # Giả lập LLM nhầm sang Thứ 5 (weekday=4) và bịa giờ kết thúc 21:00 cho "tối Thứ 6"
    t08_verified = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Làm bài tiểu luận",
            content=t08_norm,
            scope="8 trang",
            abandoned_phrases=["vào 19:00 Thứ 5"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="weekly_rule",
                    weekday=4,  # cố tình truyền sai Thứ 5 để kiểm chứng I1 sửa sang Thứ 6 (5)
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="sửa lại là tối Thứ 6 thay vì Thứ 5",
                )
            ],
            deadline=datetime(2026, 11, 29, 12, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Làm bài tiểu luận 8 trang.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t08_norm,
        temporal_anchors=t08_anchors,
    )
    assert t08_verified.structured_free_windows[0].weekday == 5
    _record_gate(not t08_verified.is_complete, "T08_ungrounded_friday_hours")

    # --- T09: Đổi giờ chiều mai 14:00-16:00 -> 19:00-21:00 mai + đổi hẹn thứ bảy -> hẹn tối chủ nhật ---
    t09_raw = DEV_CASE_MAP["T09"].raw_transcript
    t09_norm = normalize_vietnamese_transcript(t09_raw)
    assert "14:00 đến 16:00" in t09_norm
    assert "19:00 đến 21:00" in t09_norm
    t09_z2 = _detect_zone2_deadline_confirmation_issue(
        t09_raw, timezone_name="Asia/Ho_Chi_Minh", reference_now=FROZEN_NOW
    )
    assert t09_z2 is not None and t09_z2.blocking is True
    t09_no_dl = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Chức năng đăng nhập",
            content=t09_norm,
            scope="2 màn hình",
            abandoned_phrases=["rảnh chiều mai 14:00 đến 16:00", "Hẹn Thứ 7"],
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date="2026-11-25",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="đổi thành 19:00 đến 21:00 mai đi",
                )
            ],
            deadline=None,
            is_complete=True,
            summary="Code đăng nhập 2 màn hình.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t09_norm,
    )
    _record_gate(not t09_no_dl.is_complete and t09_z2.blocking, "T09_homophone_and_missing_dl")

    # --- T21: Deadline nằm trong quá khứ (20/11/2026 < 24/11/2026) ---
    t21_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T21"].raw_transcript)
    t21_verified = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Viết bài thu hoạch",
            content=t21_norm,
            scope="4 trang",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="daily",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="rảnh 19:00 đến 21:00 hằng ngày",
                )
            ],
            deadline=datetime(2026, 11, 20, 20, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Bài thu hoạch 4 trang.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t21_norm,
    )
    assert t21_verified.has_conflict is True
    _record_gate(not t21_verified.is_complete, "T21_past_deadline")

    # --- T22: Mâu thuẫn Thứ Tư (wd=3) và ngày 27/11/2026 (Thứ Sáu wd=5) ---
    t22_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T22"].raw_transcript)
    t22_anchors = extract_temporal_anchors(t22_norm, reference_now=FROZEN_NOW)
    assert any(a.weekday_date_conflict for a in t22_anchors)
    t22_verified = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Làm slide thuyết trình",
            content=t22_norm,
            scope="6 trang",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    weekday=5,  # kể cả khi LLM tự lấp liếm weekday=5 theo ngày 27/11
                    date="2026-11-27",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="ngày 27 tháng 11 từ 19:00 đến 21:00",
                )
            ],
            deadline=datetime(2026, 11, 29, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Làm 6 slide thuyết trình.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t22_norm,
        temporal_anchors=t22_anchors,
    )
    assert t22_verified.structured_free_windows[0].is_ambiguous is True
    _record_gate(not t22_verified.is_complete, "T22_weekday_date_conflict")

    # --- T23: Khung giờ rảnh mơ hồ ("rảnh lúc nào, tiện buổi tối cũng được") -> chặn bịa giờ ---
    t23_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T23"].raw_transcript)
    t23_anchors = extract_temporal_anchors(t23_norm, reference_now=FROZEN_NOW)
    assert all(not a.has_explicit_clock_time for a in t23_anchors)
    t23_verified = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Chỉnh sửa CV xin thực tập",
            content=t23_norm,
            scope="2 trang",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="daily",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="tiện buổi tối cũng được",
                )
            ],
            deadline=datetime(2026, 11, 29, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Chỉnh sửa CV 2 trang.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t23_norm,
        temporal_anchors=t23_anchors,
    )
    assert t23_verified.structured_free_windows[0].is_ambiguous is True
    _record_gate(not t23_verified.is_complete, "T23_hallucinated_clock_hours")

    # --- T24: Giờ rảnh (29/11) nằm sau hạn chót (26/11) ---
    t24_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T24"].raw_transcript)
    t24_anchors = extract_temporal_anchors(t24_norm, reference_now=FROZEN_NOW)
    t24_verified = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Hoàn thành bài tập Python",
            content=t24_norm,
            scope="3 bài tập Python",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    weekday=7,
                    date="2026-11-29",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="tối chủ nhật ngày 29 tháng 11",
                )
            ],
            deadline=datetime(2026, 11, 26, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            has_conflict=False,
            summary="Làm 3 bài tập Python.",
        ),
        current_dt=FROZEN_NOW,
        user_input=t24_norm,
        temporal_anchors=t24_anchors,
    )
    assert t24_verified.has_conflict is True
    _record_gate(not t24_verified.is_complete, "T24_free_window_after_deadline")

    # --- Các mẫu thiếu trường bắt buộc (T14, T15, T17, T18, T19, T31) -> FPCR = 0 ---
    for incomplete_tid in ("T14", "T15", "T17", "T18", "T19", "T31"):
        inc_norm = normalize_vietnamese_transcript(DEV_CASE_MAP[incomplete_tid].raw_transcript)
        inc_res = ai_service._fallback_extract_task(
            inc_norm,
            timezone="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        _record_gate(not inc_res.is_complete, f"{incomplete_tid}_incomplete_guard")

    # --- T26: "Tối mai 7:00 đến 9:00 ... hạn 10:00 tối thứ Sáu" -> KHÔNG đảo sáng 07:00-09:00 ---
    t26_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T26"].raw_transcript)
    assert "Tối mai 19:00 đến 21:00" in t26_norm
    assert "07:00" not in t26_norm
    t26_anchors = extract_temporal_anchors(t26_norm, reference_now=FROZEN_NOW)
    assert (t26_anchors[0].resolved_date, t26_anchors[0].start_local, t26_anchors[0].end_local) == (
        "2026-11-25",
        "19:00",
        "21:00",
    )
    assert (t26_anchors[1].iso_weekday, t26_anchors[1].resolved_date, t26_anchors[1].start_local) == (
        5,
        "2026-11-27",
        "22:00",
    )

    # --- T32: Độc thoại dài, giữ nguyên trạng từ "từ từ", không đảo sáng, bỏ Thứ 5 ---
    t32_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T32"].raw_transcript)
    assert "từ từ" in t32_norm
    assert "19:30 đến 21:30" in t32_norm
    assert "19:00 đến khoảng 21:30" in t32_norm
    assert "17:00 Thứ 7 ngày 28 tháng 11" in t32_norm
    t32_anchors = extract_temporal_anchors(t32_norm, reference_now=FROZEN_NOW)
    assert any(
        a.resolved_date == "2026-11-25" and (a.start_local, a.end_local) == ("19:30", "21:30")
        for a in t32_anchors
    )
    assert any(
        a.resolved_date == "2026-11-27" and (a.start_local, a.end_local) == ("19:00", "21:30")
        for a in t32_anchors
    )
    assert any(
        a.resolved_date == "2026-11-28"
        and a.iso_weekday == 6
        and a.start_local == "17:00"
        and not a.weekday_date_conflict
        for a in t32_anchors
    )

    # --- T33: Nói vấp chuỗi giờ "từ 19:00 đến 20:00 đến 22:00" -> gộp sạch "19:00 đến 22:00" ---
    t33_norm = normalize_vietnamese_transcript(DEV_CASE_MAP["T33"].raw_transcript)
    assert "từ 19:00 đến 22:00" in t33_norm
    assert "20:00" not in t33_norm
    t33_anchors = extract_temporal_anchors(t33_norm, reference_now=FROZEN_NOW)
    assert len(t33_anchors) == 1
    assert (t33_anchors[0].iso_weekday, t33_anchors[0].resolved_date) == (4, "2026-11-26")
    assert (t33_anchors[0].start_local, t33_anchors[0].end_local) == ("19:00", "22:00")

    fpcr = (false_positive_completions / negative_gate_trials) * 100.0
    assert fpcr == 0.0, f"Expected FPCR == 0.0%, got {fpcr:.2f}%"


def test_suite1_deterministic_pipeline_latency_under_5ms_per_sample() -> None:
    """Đo latency trung bình (ITN + extract_temporal_anchors + _enforce_uf1_temporal_guardrails) < 5.0ms/mẫu."""
    seed_results: list[tuple[str, TaskExtractionResult]] = []
    for case in DEV_REGRESSION_31_CASES:
        seed = ai_service._fallback_extract_task(
            case.raw_transcript,
            timezone="Asia/Ho_Chi_Minh",
            reference_now=FROZEN_NOW,
        )
        seed_results.append((case.raw_transcript, seed))

    # Warm-up 1 vòng
    for raw_txt, seed_res in seed_results:
        norm = normalize_vietnamese_transcript(raw_txt)
        anc = extract_temporal_anchors(norm, reference_now=FROZEN_NOW)
        AIService._enforce_uf1_temporal_guardrails(
            seed_res,
            current_dt=FROZEN_NOW,
            timezone="Asia/Ho_Chi_Minh",
            user_input=norm,
            temporal_anchors=anc,
        )

    rounds = 4
    total_samples = rounds * len(seed_results)
    t0 = time.perf_counter()
    for _ in range(rounds):
        for raw_txt, seed_res in seed_results:
            norm = normalize_vietnamese_transcript(raw_txt)
            anc = extract_temporal_anchors(norm, reference_now=FROZEN_NOW)
            AIService._enforce_uf1_temporal_guardrails(
                seed_res,
                current_dt=FROZEN_NOW,
                timezone="Asia/Ho_Chi_Minh",
                user_input=norm,
                temporal_anchors=anc,
            )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    avg_ms = elapsed_ms / total_samples
    assert avg_ms < 5.0, f"Deterministic UF1 latency exceeded 5.0 ms/sample: {avg_ms:.3f} ms"


# ==============================================================================
# SUITE 2 — UNSEEN HOLDOUT (30 BRAND-NEW MULTI-DOMAIN VIETNAMESE SCENARIOS)
# ==============================================================================


@dataclass(frozen=True, slots=True)
class HoldoutEvalCase:
    case_id: str
    domain: str
    utterance: str
    expected_avail_date: str | None
    expected_avail_weekday: int | None
    expected_start_local: str | None
    expected_end_local: str | None
    expected_deadline_date: str
    expect_conflict: bool = False
    expect_ungrounded_time: bool = False
    expect_default_sched: bool = False
    abandoned_phrases: tuple[str, ...] = ()
    window_kind: str = "specific_date"


UNSEEN_HOLDOUT_30_CASES: tuple[HoldoutEvalCase, ...] = (
    # 1. Kế toán / Thuế
    HoldoutEvalCase(
        "H01",
        "accounting_tax",
        "Đối soát hóa đơn GTGT quý 4 gồm 25 chứng từ, rảnh tối thứ tư từ 8 giờ tối đến 10 giờ tối, hạn ngày 30/11 lúc 18:00.",
        "2026-11-25",
        3,
        "20:00",
        "22:00",
        "2026-11-30",
    ),
    HoldoutEvalCase(
        "H02",
        "accounting_tax",
        "Lập tờ khai quyết toán thuế thu nhập cá nhân gồm 3 phụ lục Excel, rảnh chiều thứ sáu từ 2 giờ rưỡi chiều đến 5 giờ chiều, hạn 29/11 lúc 21:00.",
        "2026-11-27",
        5,
        "14:30",
        "17:00",
        "2026-11-29",
    ),
    HoldoutEvalCase(
        "H03",
        "accounting_tax",
        "Kiểm tra sổ phụ ngân hàng tháng 11 dài 12 trang, rảnh sáng mai từ 8 giờ sáng đến 10 giờ rưỡi sáng, hạn 27/11 lúc 17:00.",
        "2026-11-25",
        None,
        "08:00",
        "10:30",
        "2026-11-27",
    ),
    # 2. Y tế / Sức khỏe (gồm "8 giờ kém 10 sáng")
    HoldoutEvalCase(
        "H04",
        "healthcare",
        "Tập 4 bài vật lý trị liệu khớp gối theo phác đồ bác sĩ, rảnh thứ năm từ 8 giờ kém 10 sáng đến 9 giờ rưỡi sáng, hạn 28/11 lúc 12:00.",
        "2026-11-26",
        4,
        "07:50",
        "09:30",
        "2026-11-28",
    ),
    HoldoutEvalCase(
        "H05",
        "healthcare",
        "Sắp xếp hồ sơ khám sức khỏe định kỳ gồm 6 phiếu xét nghiệm máu, rảnh sáng thứ bảy từ 7 giờ rưỡi sáng đến 9 giờ sáng, hạn 29/11 lúc 18:00.",
        "2026-11-28",
        6,
        "07:30",
        "09:00",
        "2026-11-29",
    ),
    HoldoutEvalCase(
        "H06",
        "healthcare",
        "Ghi chép bảng theo dõi huyết áp 7 ngày cho mẹ, rảnh hằng ngày từ 20:30 đến 21:15, hạn 02/12/2026 lúc 22:00.",
        None,
        None,
        "20:30",
        "21:15",
        "2026-12-02",
        window_kind="daily",
    ),
    # 3. Marketing / TikTok
    HoldoutEvalCase(
        "H07",
        "marketing_tiktok",
        "Viết kịch bản quay 3 video TikTok chiến dịch mùa lễ hội, rảnh tối thứ năm từ 19:30 đến 21:30, hạn 28/11 lúc 22:00.",
        "2026-11-26",
        4,
        "19:30",
        "21:30",
        "2026-11-28",
    ),
    HoldoutEvalCase(
        "H08",
        "marketing_tiktok",
        "Thiết kế 5 banner quảng cáo sàn thương mại điện tử, rảnh chiều mốt từ 14:00 đến 16:30, hạn 30/11 lúc 20:00.",
        "2026-11-26",
        None,
        "14:00",
        "16:30",
        "2026-11-30",
    ),
    HoldoutEvalCase(
        "H09",
        "marketing_tiktok",
        "Lên lịch đăng 14 bài nội dung fanpage tháng 12, rảnh tối nay từ 8 giờ tối đến 10 giờ rưỡi tối, hạn 27/11 lúc 18:00.",
        "2026-11-24",
        None,
        "20:00",
        "22:30",
        "2026-11-27",
    ),
    # 4. Pháp lý / Hợp đồng
    HoldoutEvalCase(
        "H10",
        "legal",
        "Rà soát hợp đồng thuê văn phòng gồm 9 điều khoản bồi thường, rảnh sáng thứ sáu từ 8:30 đến 11:00, hạn trước 17:00 ngày 28/11.",
        "2026-11-27",
        5,
        "08:30",
        "11:00",
        "2026-11-28",
    ),
    HoldoutEvalCase(
        "H11",
        "legal",
        "Soạn thảo thỏa thuận bảo mật thông tin NDA dài 4 trang, rảnh tối thứ tư từ 7:00 đến 9:00, hạn 27/11 lúc 16:00.",
        "2026-11-25",
        3,
        "19:00",
        "21:00",
        "2026-11-27",
    ),
    HoldoutEvalCase(
        "H12",
        "legal",
        "Chuẩn bị bộ hồ sơ công chứng ủy quyền gồm 5 bản sao y, rảnh chiều chủ nhật từ 2:00 đến 4:30, hạn 01/12/2026 lúc 17:00.",
        "2026-11-29",
        7,
        "14:00",
        "16:30",
        "2026-12-01",
    ),
    # 5. Việc nhà / Đời sống
    HoldoutEvalCase(
        "H13",
        "household",
        "Sắp xếp và phân loại lại tủ sách phòng khách gồm 3 kệ gỗ, rảnh sáng chủ nhật từ 8:00 đến 10:00, hạn 29/11 lúc 20:00.",
        "2026-11-29",
        7,
        "08:00",
        "10:00",
        "2026-11-29",
    ),
    HoldoutEvalCase(
        "H14",
        "household",
        "Lên thực đơn nấu ăn tiết kiệm cho gia đình gồm 7 bữa tối, rảnh tối thứ sáu từ 20:00 đến 21:00, hạn 28/11 lúc 09:00.",
        "2026-11-27",
        5,
        "20:00",
        "21:00",
        "2026-11-28",
    ),
    HoldoutEvalCase(
        "H15",
        "household",
        "Vệ sinh lưới lọc bụi cho 2 máy điều hòa trong căn hộ, rảnh chiều thứ bảy từ 15:00 đến 17:00, hạn 29/11 lúc 19:00.",
        "2026-11-28",
        6,
        "15:00",
        "17:00",
        "2026-11-29",
    ),
    # 6. Luyện thi IELTS / Ngoại ngữ
    HoldoutEvalCase(
        "H16",
        "ielts",
        "Viết 2 bài luận IELTS Writing Task 2 chủ đề đô thị hóa, rảnh tối thứ năm từ 19:00 đến 21:00, hạn 28/11 lúc 23:00.",
        "2026-11-26",
        4,
        "19:00",
        "21:00",
        "2026-11-28",
    ),
    HoldoutEvalCase(
        "H17",
        "ielts",
        "Luyện nghe chép chính tả 3 bài IELTS Listening Section 4, rảnh sáng thứ tư từ 6 giờ rưỡi sáng đến 8 giờ sáng, hạn 27/11 lúc 21:00.",
        "2026-11-25",
        3,
        "06:30",
        "08:00",
        "2026-11-27",
    ),
    HoldoutEvalCase(
        "H18",
        "ielts_ambiguous_slot",
        "Thu âm 5 chủ đề IELTS Speaking Part 2, rảnh buổi tối ngày mai lúc nào rảnh thì làm, hạn 28/11 lúc 20:00.",
        "2026-11-25",
        None,
        None,
        None,
        "2026-11-28",
        expect_ungrounded_time=True,
    ),
    # 7. Đại học / Học thuật
    HoldoutEvalCase(
        "H19",
        "university",
        "Đọc và tóm tắt 8 bài báo khoa học cho khóa luận, rảnh tối thứ ba tuần sau từ 19:00 đến 21:30, hạn 04/12/2026 lúc 22:00.",
        "2026-12-01",
        2,
        "19:00",
        "21:30",
        "2026-12-04",
    ),
    HoldoutEvalCase(
        "H20",
        "university",
        "Thiết kế poster hội nghị sinh viên nghiên cứu khoa học gồm 4 phần chính, rảnh chiều thứ năm từ 13:30 đến 15:30, hạn 30/11 lúc 17:00.",
        "2026-11-26",
        4,
        "13:30",
        "15:30",
        "2026-11-30",
    ),
    HoldoutEvalCase(
        "H21",
        "university",
        "Giải 6 bài tập phương trình vi phân riêng, rảnh tối thứ sáu từ 19:00 đến 21:00, hạn 29/11 lúc 22:00.",
        "2026-11-27",
        5,
        "19:00",
        "21:00",
        "2026-11-29",
    ),
    # 8. Lịch mặc định
    HoldoutEvalCase(
        "H22",
        "default_schedule",
        "Biên soạn bộ 4 biểu mẫu đánh giá nhân sự cuối năm, dùng lịch mặc định đã lưu nhé, hạn 28/11 lúc 18:00.",
        None,
        None,
        "19:00",
        "21:00",
        "2026-11-28",
        expect_default_sched=True,
        window_kind="daily",
    ),
    HoldoutEvalCase(
        "H23",
        "default_schedule",
        "Tổng hợp 3 biểu đồ tồn kho tháng 11, áp dụng khung giờ rảnh mặc định, hạn 29/11 lúc 21:00.",
        None,
        None,
        "19:00",
        "21:00",
        "2026-11-29",
        expect_default_sched=True,
        window_kind="daily",
    ),
    # 9. Self-Repair / Tự đính chính
    HoldoutEvalCase(
        "H24",
        "self_repair",
        "Soạn 6 trang cẩm nang đào tạo nhân viên mới, rảnh tối thứ tư từ 19:00 đến 21:00, à nhầm đổi sang tối thứ sáu từ 20:00 đến 22:00, hạn 29/11 lúc 22:00.",
        "2026-11-27",
        5,
        "20:00",
        "22:00",
        "2026-11-29",
        abandoned_phrases=("rảnh tối Thứ 4 từ 19:00 đến 21:00",),
    ),
    HoldoutEvalCase(
        "H25",
        "self_repair",
        "Vẽ 4 màn hình thanh toán ví điện tử, rảnh sáng thứ bảy từ 09:00 đến 11:00, hạn 28/11 lúc 18:00, à quên sửa lại hạn 30/11 lúc 20:00.",
        "2026-11-28",
        6,
        "09:00",
        "11:00",
        "2026-11-30",
        abandoned_phrases=("hạn 28/11 lúc 18:00",),
    ),
    HoldoutEvalCase(
        "H26",
        "self_repair",
        "Viết 5 kịch bản kiểm thử hồi quy vào chiều thứ sáu thay vì thứ tư từ 14:00 đến 16:00, hạn 29/11 lúc 21:00.",
        "2026-11-27",
        5,
        "14:00",
        "16:00",
        "2026-11-29",
    ),
    # 10. Mâu thuẫn Thứ–Ngày mới ("thứ Năm 28/11" khi 28/11/2026 là Thứ Bảy; "Chủ nhật 27/11" khi 27/11 là Thứ Sáu)
    HoldoutEvalCase(
        "H27",
        "weekday_date_conflict",
        "Hoàn thiện báo cáo kiểm toán nội bộ dài 7 trang, rảnh tối thứ Năm ngày 28/11 từ 19:00 đến 21:00, hạn 30/11 lúc 22:00.",
        "2026-11-28",
        4,
        "19:00",
        "21:00",
        "2026-11-30",
        expect_conflict=True,
    ),
    HoldoutEvalCase(
        "H28",
        "weekday_date_conflict",
        "Dựng 3 phối cảnh nội thất phòng ngủ, rảnh sáng Chủ nhật ngày 27/11 từ 08:30 đến 10:30, hạn 30/11 lúc 18:00.",
        "2026-11-27",
        7,
        "08:30",
        "10:30",
        "2026-11-30",
        expect_conflict=True,
    ),
    # 11. Ngữ cảnh vùng miền
    HoldoutEvalCase(
        "H29",
        "regional_dialect",
        "Làm giùm mình 4 trang báo cáo khảo sát nhà vườn miền Tây nghen, bữa mai rảnh tối từ 7 giờ rưỡi tối tới 9 giờ rưỡi tối, hạn ngày 28/11 lúc 20:00.",
        "2026-11-25",
        None,
        "19:30",
        "21:30",
        "2026-11-28",
    ),
    HoldoutEvalCase(
        "H30",
        "regional_dialect",
        "Chép lại 3 bản hợp đồng thu mua nông sản nha, ngày mốt rảnh chiều từ 2 giờ chiều tới 4 giờ chiều, nộp trước ngày 29 tháng 11 lúc 18:00.",
        "2026-11-26",
        None,
        "14:00",
        "16:00",
        "2026-11-29",
    ),
)


def _evaluate_holdout_case(case: HoldoutEvalCase) -> bool:
    norm = normalize_vietnamese_transcript(case.utterance)
    anchors = extract_temporal_anchors(norm, reference_now=FROZEN_NOW, timezone="Asia/Ho_Chi_Minh")

    if case.expect_default_sched:
        if not _requests_default_availability(norm):
            return False
        win = ExtractedFreeWindowSchema(
            kind="daily",
            start_local=case.expected_start_local,
            end_local=case.expected_end_local,
            raw_phrase="Khung giờ rảnh mặc định 19:00-21:00",
        )
    else:
        # Tìm anchor giờ rảnh chính (sau khi trừ mảnh bị tự hủy nếu có)
        effective_anchors = anchors
        if case.abandoned_phrases:
            effective_anchors = [
                a
                for a in anchors
                if not any(ab.lower() in a.raw_span.lower() for ab in case.abandoned_phrases)
            ]
        if not effective_anchors:
            return False
        avail_anchor: TemporalAnchor = effective_anchors[0]
        if avail_anchor.weekday_date_conflict != case.expect_conflict:
            return False
        if case.expected_avail_date is not None and avail_anchor.resolved_date != case.expected_avail_date:
            return False
        if case.expected_avail_weekday is not None and avail_anchor.iso_weekday != case.expected_avail_weekday:
            return False
        if not case.expect_ungrounded_time:
            if avail_anchor.start_local != case.expected_start_local:
                return False
            if avail_anchor.end_local != case.expected_end_local:
                return False
        else:
            if avail_anchor.has_explicit_clock_time:
                return False

        win = ExtractedFreeWindowSchema(
            kind="daily" if case.window_kind == "daily" else "specific_date",
            date=case.expected_avail_date if case.window_kind != "daily" else None,
            weekday=case.expected_avail_weekday,
            start_local=case.expected_start_local or "19:00",
            end_local=case.expected_end_local or "21:00",
            raw_phrase=avail_anchor.raw_span[:200],
        )

    # Cố tình đặt ngày deadline lệch 1 ngày cho H25 để kiểm chứng I1 tự sửa ngày theo anchor mới,
    # đặt kind='daily' cho H09 ("tối nay") để kiểm chứng I3b, đặt start_local='07:00' cho H11 để kiểm chứng I3a,
    # đặt rò rỉ giờ vào scope cho H20 để kiểm chứng I5, và tiêu đề bịa cho H21 để kiểm chứng I4.
    dl_seed_date = "2026-11-28" if case.case_id == "H25" else case.expected_deadline_date
    dl_dt = datetime.fromisoformat(f"{dl_seed_date}T20:00:00+07:00")
    if case.case_id == "H09":
        win = ExtractedFreeWindowSchema(
            kind="daily",
            start_local="20:00",
            end_local="22:30",
            raw_phrase="tối nay từ 20:00 đến 22:30",
        )
    elif case.case_id == "H11":
        win = ExtractedFreeWindowSchema(
            kind="specific_date",
            weekday=3,
            date="2026-11-25",
            start_local="07:00",
            end_local="09:00",
            raw_phrase="tối Thứ 4 từ 7:00 đến 9:00",
        )

    scope_val = (
        "4 phần chính, rảnh từ 13:30 đến 15:30"
        if case.case_id == "H20"
        else "Đầu ra chuẩn theo yêu cầu"
    )
    title_val = (
        "Kế hoạch du lịch biển Phú Quốc"
        if case.case_id == "H21"
        else norm[:60]
    )

    candidate = TaskExtractionResult(
        title=title_val,
        content=norm,
        scope=scope_val,
        abandoned_phrases=list(case.abandoned_phrases),
        structured_free_windows=[win],
        free_time_slots=[win.raw_phrase],
        deadline=dl_dt,
        time_constraint=ExtractedTimeConstraint(
            kind="deadline",
            start_at=FROZEN_NOW,
            deadline_at=dl_dt,
            input_timezone="Asia/Ho_Chi_Minh",
        ),
        is_complete=True,
        summary=norm[:120],
    )
    verified = AIService._enforce_uf1_temporal_guardrails(
        candidate,
        current_dt=FROZEN_NOW,
        timezone="Asia/Ho_Chi_Minh",
        user_input=norm,
        temporal_anchors=anchors,
        is_initial_turn=True,
    )

    if case.expect_conflict or case.expect_ungrounded_time or case.case_id == "H21":
        if verified.is_complete:
            return False
        if case.case_id == "H21" and not any(
            i.code == "AI_TITLE_CONFIRMATION_REQUIRED" and i.blocking
            for i in verified.clarification_issues
        ):
            return False
        return True

    if not verified.is_complete:
        return False
    if verified.deadline is None or verified.deadline.astimezone(VN_TZ).date().isoformat() != case.expected_deadline_date:
        return False
    v_win = verified.structured_free_windows[0]
    if case.case_id == "H09" and (v_win.kind != "specific_date" or v_win.date != "2026-11-24"):
        return False
    if case.case_id == "H11" and (v_win.start_local, v_win.end_local) != ("19:00", "21:00"):
        return False
    if case.case_id == "H20" and verified.scope != "4 phần chính":
        return False
    return True


def test_suite2_unseen_holdout_30_multidomain_scenarios_over_95_percent() -> None:
    """Kiểm chứng 30 kịch bản tiếng Việt đa miền hoàn toàn mới đạt >= 95% trên Temporal Grounding & I1–I5."""
    assert len(UNSEEN_HOLDOUT_30_CASES) == 30
    dev_texts = {c.raw_transcript for c in DEV_REGRESSION_31_CASES}
    assert all(hc.utterance not in dev_texts for hc in UNSEEN_HOLDOUT_30_CASES)

    passed_ids: list[str] = []
    failed_ids: list[str] = []
    for hc in UNSEEN_HOLDOUT_30_CASES:
        if _evaluate_holdout_case(hc):
            passed_ids.append(hc.case_id)
        else:
            failed_ids.append(hc.case_id)

    pass_rate = len(passed_ids) / len(UNSEEN_HOLDOUT_30_CASES)
    assert pass_rate >= 0.95, (
        f"Unseen Holdout accuracy {pass_rate:.1%} < 95.0% (failed: {failed_ids})"
    )


# ==============================================================================
# SUITE 3 — ADVERSARIAL & NEGATIVE CONTROL (>= 20 FALSE-POSITIVE TRAPS, 100% PASS)
# ==============================================================================


def test_suite3_adversarial_and_negative_control_22_traps_zero_false_positives() -> None:
    """Kiểm chứng đủ 22 bẫy dương tính giả đạt 100% pass (0 false positives)."""
    outcomes: list[tuple[str, bool]] = []

    def _check(trap_name: str, condition: bool) -> None:
        outcomes.append((trap_name, bool(condition)))

    # 1. "Làm báo cáo sáng Thứ 4 từ 7:00 đến 9:00, tối Thứ 4 bận họp" -> giữ 07:00-09:00 (không thành 19:00-21:00)
    s1 = normalize_vietnamese_transcript(
        "Làm báo cáo sáng Thứ 4 từ 7:00 đến 9:00, tối Thứ 4 bận họp"
    )
    a1 = extract_temporal_anchors(s1, reference_now=FROZEN_NOW)
    _check(
        "trap01_morning_range_not_polluted_by_evening_busy_clause",
        "07:00 đến 09:00" in s1
        and "19:00" not in s1
        and len(a1) == 2
        and (a1[0].start_local, a1[0].end_local) == ("07:00", "09:00"),
    )

    # 2. "hẹn tối thứ Sáu gặp khách hàng rồi nộp trưa thứ Bảy" -> KHÔNG kích hoạt Zone 2
    s2 = "Hoàn thành báo cáo 5 trang, rảnh 19:00-21:00 hằng ngày, hẹn tối thứ Sáu gặp khách hàng rồi nộp trưa thứ Bảy"
    z2_2 = _detect_zone2_deadline_confirmation_issue(
        s2, timezone_name="Asia/Ho_Chi_Minh", reference_now=FROZEN_NOW
    )
    _check("trap02_meeting_customer_no_zone2", z2_2 is None)

    # 3. "Tạo việc chỉnh sửa lịch làm việc cho nhóm gồm 2 bảng..." -> I4 pass 100%
    s3 = "Tạo việc chỉnh sửa lịch làm việc cho nhóm gồm 2 bảng, rảnh tối mai 19:00-21:00, hạn 28/11 lúc 22:00"
    v3 = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Chỉnh sửa lịch làm việc cho nhóm",
            content=s3,
            scope="2 bảng lịch làm việc",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date="2026-11-25",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="rảnh tối mai 19:00-21:00",
                )
            ],
            deadline=datetime(2026, 11, 28, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Chỉnh sửa lịch làm việc gồm 2 bảng.",
        ),
        current_dt=FROZEN_NOW,
        user_input=s3,
        is_initial_turn=True,
    )
    _check(
        "trap03_i4_passes_legitimate_task_about_editing_schedule",
        v3.is_complete is True
        and not any(i.code == "AI_TITLE_CONFIRMATION_REQUIRED" for i in v3.clarification_issues),
    )

    # 4. "chương 5 phần thứ 3" -> không sinh weekday=2 hoặc weekday=3
    s4 = normalize_vietnamese_transcript("Đọc chương 5 phần thứ 3 khoảng 10 trang")
    a4 = extract_temporal_anchors(s4, reference_now=FROZEN_NOW)
    _check(
        "trap04_ordinal_chuong_5_phan_thu_3",
        "phần thứ 3" in s4 and all(a.iso_weekday is None for a in a4),
    )

    # 5. "Viết bài từ từ thôi khoảng 3 trang, ngày ngày ôn 19:00-20:00, hạn 30/11" -> giữ "từ từ", "ngày ngày"
    s5 = normalize_vietnamese_transcript(
        "Viết bài từ từ thôi khoảng 3 trang, ngày ngày ôn 19:00-20:00, hạn 30/11"
    )
    _check(
        "trap05_preserve_adverb_tu_tu_and_ngay_ngay",
        "từ từ thôi" in s5 and "ngày ngày ôn" in s5,
    )

    # 6. "tối Thứ 4 ngày 27/11" -> giữ conflict
    s6 = normalize_vietnamese_transcript(
        "Làm báo cáo 4 trang, rảnh tối Thứ 4 ngày 27/11 từ 19:00 đến 21:00, hạn 30/11"
    )
    a6 = extract_temporal_anchors(s6, reference_now=FROZEN_NOW)
    _check("trap06_keep_thu_4_27_11_conflict", any(a.weekday_date_conflict for a in a6))

    # 7, 8, 9. "5G", "4G", "20g" -> không biến thành giờ đồng hồ
    for tag, sample in (
        ("trap07_5G_network", "Kiểm thử tốc độ mạng 5G"),
        ("trap08_4G_network", "Đo vùng phủ sóng 4G"),
        ("trap09_20g_grams", "Pha 20g bột ca cao"),
    ):
        norm_g = normalize_vietnamese_transcript(sample)
        anc_g = extract_temporal_anchors(norm_g, reference_now=FROZEN_NOW)
        _check(tag, norm_g == sample and anc_g == [])

    # 10, 11. "3/4", "24/7" -> không biến thành ngày tháng
    a10 = extract_temporal_anchors("Hoàn thành 3/4 bài tập môn Xác suất", reference_now=FROZEN_NOW)
    a11 = extract_temporal_anchors("Vận hành dịch vụ trực tuyến 24/7", reference_now=FROZEN_NOW)
    _check("trap10_fraction_3_over_4", all(a.explicit_date is None for a in a10))
    _check("trap11_24_7_uptime", all(a.explicit_date is None for a in a11))

    # 12. "11:00 đến tối 20:00" -> I3a giữ nguyên start_local="11:00"
    s12 = "Làm đồ án 5 trang, rảnh Thứ 7 từ 11:00 đến tối 20:00, hạn 30/11 lúc 22:00"
    v12 = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Làm đồ án",
            content=s12,
            scope="5 trang",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    weekday=6,
                    date="2026-11-28",
                    start_local="11:00",
                    end_local="20:00",
                    raw_phrase="từ 11:00 đến tối 20:00",
                )
            ],
            deadline=datetime(2026, 11, 30, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Làm đồ án 5 trang.",
        ),
        current_dt=FROZEN_NOW,
        user_input=s12,
    )
    _check(
        "trap12_i3a_preserves_11_00_to_toi_20_00",
        (
            v12.structured_free_windows[0].start_local,
            v12.structured_free_windows[0].end_local,
        )
        == ("11:00", "20:00"),
    )

    # 13. "Cắt đoạn 01:30-03:45 trong video" -> I5 giữ nguyên timestamp trong scope
    s13 = "Biên tập video quảng cáo, cắt đoạn 01:30-03:45 trong video, rảnh tối mai 19:00-21:00, hạn 28/11 lúc 22:00"
    v13 = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Biên tập video quảng cáo",
            content=s13,
            scope="Cắt đoạn 01:30-03:45 trong video",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    date="2026-11-25",
                    start_local="19:00",
                    end_local="21:00",
                    raw_phrase="rảnh tối mai 19:00-21:00",
                )
            ],
            deadline=datetime(2026, 11, 28, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Biên tập video.",
        ),
        current_dt=FROZEN_NOW,
        user_input=s13,
    )
    _check(
        "trap13_i5_preserves_video_timestamp_in_scope",
        v13.scope == "Cắt đoạn 01:30-03:45 trong video" and v13.is_complete is True,
    )

    # 14, 15, 16. Zone 2 negative ("hẹn khám nha sĩ", "đạt hạng 2") vs positive ("nhờ bạn nhắc hẹn tối thứ Bảy")
    z2_dentist = _detect_zone2_deadline_confirmation_issue(
        "Ôn bài 4 chương, rảnh 19:00-21:00 hằng ngày, chiều mai hẹn khám nha sĩ",
        timezone_name="Asia/Ho_Chi_Minh",
        reference_now=FROZEN_NOW,
    )
    z2_rank = _detect_zone2_deadline_confirmation_issue(
        "Giải 5 đề luyện thi, rảnh 19:00-21:00 hằng ngày, phấn đấu đạt hạng 2 tuần này",
        timezone_name="Asia/Ho_Chi_Minh",
        reference_now=FROZEN_NOW,
    )
    z2_remind = _detect_zone2_deadline_confirmation_issue(
        "Viết báo cáo 4 trang, rảnh 19:00-21:00 hằng ngày, nhờ bạn nhắc hẹn tối thứ Bảy",
        timezone_name="Asia/Ho_Chi_Minh",
        reference_now=FROZEN_NOW,
    )
    _check("trap14_zone2_negative_hen_kham_nha_si", z2_dentist is None)
    _check("trap15_zone2_negative_dat_hang_2", z2_rank is None)
    _check(
        "trap16_zone2_positive_nho_ban_nhac_hen_toi_thu_bay",
        z2_remind is not None
        and z2_remind.code == "AI_DEADLINE_CONFIRMATION_NEEDED"
        and z2_remind.blocking is True,
    )

    # 17. "tiết 2 và sáng mai" -> không tách sai tại "tiết 2"
    a17 = extract_temporal_anchors("tiết 2 và sáng mai", reference_now=FROZEN_NOW)
    _check(
        "trap17_tiet_2_va_sang_mai_no_false_conj_split",
        len(a17) == 1
        and a17[0].raw_span == "tiết 2 và sáng mai"
        and a17[0].resolved_date == "2026-11-25"
        and not a17[0].has_explicit_clock_time,
    )

    # 18. "12:30 trưa" -> giữ đúng 12:30 (không cộng +12h thành 24:30 hay đổi 00:30)
    s18 = normalize_vietnamese_transcript("Nộp bài lúc 12:30 trưa ngày mai")
    a18 = extract_temporal_anchors(s18, reference_now=FROZEN_NOW)
    _check(
        "trap18_noon_12_30_trua",
        "12:30" in s18 and len(a18) == 1 and a18[0].start_local == "12:30",
    )

    # 19. "24:00 kém 15" -> chuẩn hóa thành 23:45
    s19 = normalize_vietnamese_transcript("Nộp bài trước 24:00 kém 15 tối nay")
    a19 = extract_temporal_anchors(s19, reference_now=FROZEN_NOW)
    _check(
        "trap19_midnight_24_00_kem_15",
        "23:45" in s19
        and len(a19) == 1
        and a19[0].start_local == "23:45"
        and a19[0].resolved_date == "2026-11-24",
    )

    # 20. "gửi qua 0912345678 hoặc a@b.com" -> vocab zero PII
    hints20 = extract_salient_vocabulary_hints(
        ["Báo cáo FastAPI gửi qua 0912345678 hoặc a@b.com"]
    )
    joined20 = " | ".join(hints20)
    _check(
        "trap20_vocab_zero_pii_phone_and_email_scrubbed",
        "FastAPI" in hints20
        and "0912345678" not in joined20
        and "a@b.com" not in joined20
        and "@" not in joined20,
    )

    # 21. "09:00 đến tối 21:00" -> I3a không biến thành "21:00-21:00"
    s21 = "Làm bài tập lớn 6 trang, rảnh Chủ nhật từ 09:00 đến tối 21:00, hạn 30/11 lúc 22:00"
    v21 = AIService._enforce_uf1_temporal_guardrails(
        TaskExtractionResult(
            title="Làm bài tập lớn",
            content=s21,
            scope="6 trang",
            structured_free_windows=[
                ExtractedFreeWindowSchema(
                    kind="specific_date",
                    weekday=7,
                    date="2026-11-29",
                    start_local="09:00",
                    end_local="21:00",
                    raw_phrase="từ 09:00 đến tối 21:00",
                )
            ],
            deadline=datetime(2026, 11, 30, 22, 0, tzinfo=VN_TZ),
            is_complete=True,
            summary="Làm bài tập lớn 6 trang.",
        ),
        current_dt=FROZEN_NOW,
        user_input=s21,
    )
    _check(
        "trap21_i3a_preserves_09_00_to_toi_21_00",
        (
            v21.structured_free_windows[0].start_local,
            v21.structured_free_windows[0].end_local,
        )
        == ("09:00", "21:00"),
    )

    # 22. "ôn lại chương 2 mục 4" -> không sinh thứ hay giờ giả
    s22 = normalize_vietnamese_transcript("Ôn lại chương 2 mục 4 khoảng 8 trang")
    a22 = extract_temporal_anchors(s22, reference_now=FROZEN_NOW)
    _check("trap22_chuong_2_muc_4_no_temporal_false_positive", s22 == "Ôn lại chương 2 mục 4 khoảng 8 trang" and a22 == [])

    assert len(outcomes) == 22
    failed_traps = [name for name, ok in outcomes if not ok]
    assert not failed_traps, f"Adversarial traps failed: {failed_traps}"


# ==============================================================================
# SUITE 4 — UNIT TESTS 1-HOP AUDIO-NATIVE (TRỤ CỘT 1c)
# ==============================================================================


@pytest.mark.asyncio
async def test_suite4_audio_native_schema_and_flag_false_fallback_to_2hop() -> None:
    """Kiểm chứng AudioNativeExtractionPayload schema strip titles + cờ False chặn 1-Hop & fallback 2-Hop."""
    payload = AudioNativeExtractionPayload.model_validate(
        {"summary": "Tóm tắt", "verbatim_transcript": "  Làm   báo cáo \n  5 trang  "}
    )
    assert payload.verbatim_transcript == "Làm báo cáo 5 trang"
    none_payload = AudioNativeExtractionPayload.model_validate(
        {"summary": "Tóm tắt", "verbatim_transcript": None}
    )
    assert none_payload.verbatim_transcript == ""

    schema = AudioNativeExtractionPayload.model_json_schema()
    assert "title" not in schema
    assert "default" not in schema
    assert "verbatim_transcript" in schema["properties"]
    for prop_schema in schema["properties"].values():
        assert "title" not in prop_schema
        assert "default" not in prop_schema
    assert "verbatim_transcript" in UF1_AUDIO_NATIVE_SYSTEM_PROMPT

    disabled_svc = AIService(
        app_settings=Settings(
            _env_file=None,
            UF1_AUDIO_NATIVE_ENABLED=False,
            GEMINI_API_KEY="AIzaSyFakeUnitTestKey",
        )
    )
    with pytest.raises(AudioNativeDisabledError):
        await disabled_svc.extract_task_intent_from_audio(
            b"M4A-audio-bytes",
            "audio/m4a",
            reference_now=FROZEN_NOW,
        )

    transcribe_mock = AsyncMock(
        return_value="Ờm làm báo cáo 5 trang, rảnh tối mai từ 7 giờ đến 9 giờ tối, hạn 28/11/2026 22:00"
    )
    expected_2hop = TaskExtractionResult(
        title="Làm báo cáo",
        content="Làm báo cáo 5 trang",
        scope="5 trang",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-25",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="rảnh tối mai từ 19:00 đến 21:00",
            )
        ],
        deadline=datetime(2026, 11, 28, 22, 0, tzinfo=VN_TZ),
        is_complete=True,
        summary="Làm báo cáo 5 trang.",
        generation_tier="tier_1_gemini",
    )
    with patch.object(
        disabled_svc, "extract_task_intent", new=AsyncMock(return_value=expected_2hop)
    ) as mock_extract_2hop:
        norm_txt, res = await disabled_svc.extract_task_intent_with_audio_fallback(
            b"M4A-audio-bytes",
            "audio/m4a",
            transcribe_fn=transcribe_mock,
            reference_now=FROZEN_NOW,
        )

    transcribe_mock.assert_awaited_once_with(b"M4A-audio-bytes", "audio/m4a")
    mock_extract_2hop.assert_awaited_once()
    assert mock_extract_2hop.await_args is not None
    sent_text = mock_extract_2hop.await_args.args[0]
    assert "19:00 đến 21:00" in sent_text
    assert "Ờm" not in sent_text
    assert norm_txt == sent_text
    assert res.is_complete is True


@pytest.mark.asyncio
async def test_suite4_audio_native_flag_true_enforces_all_5_invariants_and_files_cleanup() -> None:
    """Chứng minh khi UF1_AUDIO_NATIVE_ENABLED=True, đầu ra 1-Hop đi qua ITN + đủ 5 Invariants (I1–I5)."""
    svc = AIService(
        app_settings=Settings(
            _env_file=None,
            UF1_AUDIO_NATIVE_ENABLED=True,
            GEMINI_API_KEY="AIzaSyFakeUnitTestKey",
            GEMINI_UF1_MODEL="gemini-3.5-flash-lite",
        )
    )

    # Kiểm tra validate đầu vào rỗng & MIME không hợp lệ
    with pytest.raises(ValueError, match="empty"):
        await svc.extract_task_intent_from_audio(b"", "audio/m4a", reference_now=FROZEN_NOW)
    with pytest.raises(ValueError, match="mime_type"):
        await svc.extract_task_intent_from_audio(b"abc", "application/pdf", reference_now=FROZEN_NOW)

    def _mock_client_returning(payload_dict: dict[str, Any]) -> tuple[Any, AsyncMock, AsyncMock, AsyncMock]:
        gen_mock = AsyncMock(return_value=SimpleNamespace(text=json.dumps(payload_dict, ensure_ascii=False)))
        upload_mock = AsyncMock(
            return_value=SimpleNamespace(uri="https://generativelanguage.googleapis.com/v1beta/files/f123", mime_type="audio/m4a", name="files/f123")
        )
        delete_mock = AsyncMock(return_value=None)
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=gen_mock),
                files=SimpleNamespace(upload=upload_mock, delete=delete_mock),
            )
        )
        return fake_client, gen_mock, upload_mock, delete_mock

    # --- Case A: Happy path + I3a (7h->19h) + I3b (daily 'tối nay' -> specific_date) + I5 (strip giờ rò rỉ khỏi scope) ---
    happy_json = {
        "verbatim_transcript": "Ờm soạn 5 slide thuyết trình Sinh học, tối nay từ bảy giờ đến chín giờ tối, hạn 28/11 lúc 22:00",
        "title": "Thuyết trình Sinh học",
        "content": "Soạn 5 slide thuyết trình Sinh học",
        "scope": "5 slide thuyết trình, rảnh từ 19:00 đến 21:00",
        "structured_free_windows": [
            {
                "kind": "daily",
                "start_local": "07:00",
                "end_local": "09:00",
                "raw_phrase": "tối nay từ 7:00 đến 9:00 tối",
            }
        ],
        "free_time_slots": ["tối nay từ 7:00 đến 9:00 tối"],
        "deadline": "2026-11-28T22:00:00+07:00",
        "is_complete": True,
        "summary": "Soạn 5 slide Sinh học.",
    }
    client_a, gen_a, _, _ = _mock_client_returning(happy_json)
    with patch.object(svc, "_get_gemini_client", return_value=client_a):
        norm_a, res_a = await svc.extract_task_intent_from_audio(
            b"M4A-inline",
            "audio/x-m4a",
            contextual_hints=["Sinh học", "Karatsuba"],
            reference_now=FROZEN_NOW,
            is_initial_turn=True,
        )

    assert "19:00 đến 21:00" in norm_a
    assert res_a.is_complete is True
    assert res_a.generation_tier == "tier_1_gemini"
    # I3a + I3b đã sửa kind='daily', 07:00-09:00 -> specific_date 2026-11-24, 19:00-21:00
    assert res_a.structured_free_windows[0].kind == "specific_date"
    assert res_a.structured_free_windows[0].date == "2026-11-24"
    assert (
        res_a.structured_free_windows[0].start_local,
        res_a.structured_free_windows[0].end_local,
    ) == ("19:00", "21:00")
    # I5 đã gọt sạch phần giờ rảnh rò rỉ trong scope
    assert res_a.scope == "5 slide thuyết trình"
    assert gen_a.await_args is not None
    assert (
        gen_a.await_args.kwargs["config"].thinking_config.thinking_level
        == types.ThinkingLevel.MINIMAL
    )
    sent_ctx = json.loads(gen_a.await_args.kwargs["contents"][1])
    assert sent_ctx["contextual_hints"] == ["Sinh học", "Karatsuba"]

    # --- Case B: I1 (mâu thuẫn Thứ Tư 27/11) chặn is_complete=True trên đầu ra 1-Hop ---
    i1_json = {
        "verbatim_transcript": "Làm báo cáo 4 trang, rảnh tối thứ tư ngày 27/11 từ 19:00 đến 21:00, hạn 30/11 lúc 22:00",
        "title": "Làm báo cáo",
        "content": "Làm báo cáo 4 trang",
        "scope": "4 trang",
        "structured_free_windows": [
            {
                "kind": "specific_date",
                "weekday": 5,
                "date": "2026-11-27",
                "start_local": "19:00",
                "end_local": "21:00",
                "raw_phrase": "ngày 27/11 19:00-21:00",
            }
        ],
        "deadline": "2026-11-30T22:00:00+07:00",
        "is_complete": True,
        "summary": "Báo cáo 4 trang.",
    }
    client_i1, _, _, _ = _mock_client_returning(i1_json)
    with patch.object(svc, "_get_gemini_client", return_value=client_i1):
        _, res_i1 = await svc.extract_task_intent_from_audio(
            b"M4A-i1", "audio/m4a", reference_now=FROZEN_NOW
        )
    assert res_i1.is_complete is False
    assert res_i1.structured_free_windows[0].is_ambiguous is True

    # --- Case C: I2 (bịa giờ 19:00-21:00 khi audio không nêu giờ) & I4 (bịa tiêu đề lạ ở Turn 1) ---
    i2_i4_json = {
        "verbatim_transcript": "Soạn 5 slide thuyết trình Sinh học, rảnh buổi tối lúc nào tiện cũng được, hạn 29/11 lúc 22:00",
        "title": "Đầu tư bất động sản nghỉ dưỡng",
        "content": "Soạn 5 slide thuyết trình Sinh học",
        "scope": "5 slide",
        "structured_free_windows": [
            {
                "kind": "daily",
                "start_local": "19:00",
                "end_local": "21:00",
                "raw_phrase": "buổi tối lúc nào tiện cũng được",
            }
        ],
        "deadline": "2026-11-29T22:00:00+07:00",
        "is_complete": True,
        "summary": "Soạn 5 slide.",
    }
    client_i24, _, _, _ = _mock_client_returning(i2_i4_json)
    with patch.object(svc, "_get_gemini_client", return_value=client_i24):
        _, res_i24 = await svc.extract_task_intent_from_audio(
            b"M4A-i24",
            "audio/m4a",
            reference_now=FROZEN_NOW,
            is_initial_turn=True,
        )
    assert res_i24.is_complete is False
    assert res_i24.structured_free_windows[0].is_ambiguous is True
    assert any(
        i.code == "AI_TITLE_CONFIRMATION_REQUIRED" and i.blocking
        for i in res_i24.clarification_issues
    )

    # --- Case D: Audio > 18MB dùng Files API upload & bắt buộc xóa trong finally + empty_transcript không ngắt cầu dao ---
    silent_json = {
        "verbatim_transcript": "[NO_SPEECH]",
        "title": None,
        "content": None,
        "is_complete": False,
        "summary": "Không nghe rõ lời nói.",
    }
    client_big, _, upload_big, delete_big = _mock_client_returning(silent_json)
    oversized_audio = b"\x00" * (18 * 1024 * 1024 + 64)
    svc_big = AIService(
        app_settings=Settings(
            _env_file=None,
            UF1_AUDIO_NATIVE_ENABLED=True,
            GEMINI_API_KEY="AIzaSyFakeUnitTestKey",
            GEMINI_UF1_MODEL="gemini-3.5-flash-lite",
            VOICE_MAX_AUDIO_BYTES=20 * 1024 * 1024,
        )
    )
    with patch.object(svc_big, "_get_gemini_client", return_value=client_big):
        with pytest.raises(ValueError, match="empty_transcript"):
            await svc_big.extract_task_intent_from_audio(
                oversized_audio, "audio/m4a", reference_now=FROZEN_NOW
            )
    upload_big.assert_awaited_once()
    delete_big.assert_awaited_once_with(name="files/f123")
    assert svc_big.health.can_attempt("gemini", "gemini-3.5-flash-lite") is True


@pytest.mark.asyncio
async def test_suite4_audio_native_timeout_or_error_cleanly_falls_back_to_2hop_zero_pii(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Khi 1-Hop lỗi hoặc timeout, tự động fallback sạch sang 2-Hop và không ghi PII ra log."""
    svc = AIService(
        app_settings=Settings(
            _env_file=None,
            UF1_AUDIO_NATIVE_ENABLED=True,
            GEMINI_API_KEY="AIzaSySecretKeyNeverLog",
            AI_UF1_TIMEOUT_SECONDS=0.05,
        )
    )
    secret_hint = "BiMatNoiBo_KhongDuocLog"
    secret_transcript = "Làm báo cáo mật cho khách hàng 0912345678, rảnh tối mai 19:00-21:00, hạn 28/11 lúc 22:00"

    async def _stalled_generate_content(**_kwargs: Any) -> Any:
        await asyncio.sleep(0.5)
        raise AssertionError("Must time out via asyncio.wait_for")

    slow_client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=_stalled_generate_content))
    )
    transcribe_mock = AsyncMock(return_value=secret_transcript)
    fallback_result = TaskExtractionResult(
        title="Làm báo cáo",
        content="Làm báo cáo",
        scope="1 bản báo cáo",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-25",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="rảnh tối mai 19:00-21:00",
            )
        ],
        deadline=datetime(2026, 11, 28, 22, 0, tzinfo=VN_TZ),
        is_complete=True,
        summary="Làm báo cáo.",
        generation_tier="tier_2_groq",
    )

    caplog.set_level(logging.INFO)
    with (
        patch.object(svc, "_get_gemini_client", return_value=slow_client),
        patch.object(
            svc, "extract_task_intent", new=AsyncMock(return_value=fallback_result)
        ) as mock_2hop,
    ):
        norm_out, final_res = await svc.extract_task_intent_with_audio_fallback(
            cast(bytes, b"RAW_SECRET_AUDIO_BYTES"),
            "audio/m4a",
            transcribe_fn=transcribe_mock,
            contextual_hints=[secret_hint],
            reference_now=FROZEN_NOW,
        )

    transcribe_mock.assert_awaited_once()
    mock_2hop.assert_awaited_once()
    assert final_res.generation_tier == "tier_2_groq"
    assert "19:00-21:00" in norm_out

    logged_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "UF1 1-Hop audio-native failed" in logged_text
    assert secret_hint not in logged_text
    assert "0912345678" not in logged_text
    assert "AIzaSySecretKeyNeverLog" not in logged_text
    assert "RAW_SECRET_AUDIO_BYTES" not in logged_text
