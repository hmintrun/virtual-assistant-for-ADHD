"""Probe Script: Kiểm thử Voice STT (Gemini 3.5 Transcribe / ElevenLabs Scribe v2 / Deepgram Nova-3) & AI Task Extraction (UF1)
trên 3 file audio thực tế tại C:\\test1.m4a, C:\\test2.m4a, C:\\test3.m4a.

Đáp ứng đầy đủ 4 yêu cầu của QA Lead:
1. Đọc 3 file âm thanh, gọi get_voice_service().transcribe(), in transcript và latency_ms.
2. Gọi ai_service.extract_task_intent(), kiểm tra is_complete, can_plan, clarification_questions, clarification_issues.
3. Phân tích Voice 2 & Voice 3 về availability / start_at.
4. Gửi câu trả lời bổ sung vào extract_task_intent(supplementation, previous_draft=...), kiểm tra resolved keys và can_plan.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Đảm bảo đường dẫn gốc backend nằm trong sys.path
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

# Đảm bảo mã hóa console là UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Đảm bảo timeout đủ cho roundtrip mạng quốc tế
os.environ["AI_TIMEOUT_SECONDS"] = "15.0"
os.environ["VOICE_STT_TIMEOUT_SECONDS"] = "35.0"

from app.core.config import settings
from app.schemas.common import ClarificationIssue, TimeConstraint
from app.schemas.draft import PlanningDraft, TaskSummary
from app.schemas.task import TaskExtractionResult
from app.services.ai_service import ai_service
from app.services.draft_service import _availability_proposal
from app.services.planning.validation import validate_planning_prerequisites
from app.services.voice_service import SttResult, get_voice_service


async def transcribe_audio_file(
    file_path: Path, mime_type: str = "audio/m4a", max_retries: int = 2
) -> SttResult:
    """Wrapper gọi voice_service.transcribe tương thích đa dạng tham số kèm retry."""
    get_voice_service.cache_clear()
    voice_service = get_voice_service()
    audio_bytes = file_path.read_bytes()

    sig = inspect.signature(voice_service.transcribe)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if "mime_type" in sig.parameters:
                return await voice_service.transcribe(audio_bytes, mime_type=mime_type)
            return await voice_service.transcribe(
                audio_bytes, content_type=mime_type, filename=file_path.name
            )
        except Exception as exc:
            last_err = exc
            print(f"    [STT Retry {attempt}/{max_retries}] Lỗi: {exc}. Đang thử lại...")
            await asyncio.sleep(1.0)
    raise last_err or RuntimeError("STT thất bại sau nhiều lần thử")


def evaluate_can_plan(
    extracted: TaskExtractionResult,
    timezone_name: str = "Asia/Ho_Chi_Minh",
    explicit_start_at: datetime | None = None,
) -> tuple[bool, list[ClarificationIssue]]:
    """Đánh giá can_plan theo chuẩn A02.2 (validate_planning_prerequisites)."""
    dummy_draft = PlanningDraft(
        draft_id="probe_draft",
        client_draft_id="probe_client",
        input_source="voice",
        stage="collecting",
        summary_revision=1,
        planning_input_revision=1,
        summary=TaskSummary(
            title=extracted.title or "Nhiệm vụ",
            description=extracted.content or "",
            scope=extracted.scope or "",
            extra_requirements=extracted.extra_requirements,
            time_constraint=None,
            can_plan=False,
        ),
    )

    prop_avail, _ = _availability_proposal(
        extracted, dummy_draft, None, timezone_name
    )

    tc: TimeConstraint | None = None
    start_at_val = explicit_start_at
    if (
        start_at_val is None
        and extracted.time_constraint
        and extracted.time_constraint.start_at
    ):
        start_at_val = extracted.time_constraint.start_at

    end_val = (
        extracted.deadline
        or (
            extracted.time_constraint.deadline_at
            if extracted.time_constraint
            else None
        )
        or (
            extracted.time_constraint.finish_by_at
            if extracted.time_constraint
            else None
        )
    )

    if end_val is not None:
        try:
            tc = TimeConstraint(
                kind=(
                    extracted.time_constraint.kind
                    if extracted.time_constraint and extracted.time_constraint.kind
                    else "deadline"
                ),
                start_at=start_at_val,
                deadline_at=end_val,
                input_timezone=timezone_name,
            )
        except Exception:
            tc = None

    summary = TaskSummary(
        title=extracted.title or "Nhiệm vụ",
        description=extracted.content or "",
        scope=extracted.scope or "",
        extra_requirements=extracted.extra_requirements,
        time_constraint=tc,
        availability=prop_avail,
        can_plan=False,
    )

    can_plan, issues = validate_planning_prerequisites(
        summary, prop_avail, reference_now=datetime.now(UTC)
    )
    return can_plan, issues


async def main() -> None:
    print("=" * 80)
    print("PROPLAN VA BACKEND - QA PROBE: VOICE STT & AI TASK EXTRACTION (UF1)")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print(
        f"STT Provider: {settings.VOICE_STT_PROVIDER} "
        f"(gemini={settings.GEMINI_STT_MODEL}, transport={settings.GEMINI_STT_TRANSPORT}, "
        f"elevenlabs={settings.ELEVENLABS_STT_MODEL}, deepgram={settings.DEEPGRAM_STT_MODEL})"
    )
    print(f"Primary LLM: {settings.GEMINI_FLASH_MODEL}")
    print(f"Backup LLM: {settings.GEMINI_BACKUP_MODEL}")
    print("=" * 80)
    print()

    audio_files = [
        Path(r"C:\test1.m4a"),
        Path(r"C:\test2.m4a"),
        Path(r"C:\test3.m4a"),
    ]

    extraction_results: dict[str, TaskExtractionResult] = {}
    stt_results: dict[str, SttResult] = {}

    # --------------------------------------------------------------------------
    # BƯỚC 1: TRANSCRIBE 3 FILE AUDIO
    # --------------------------------------------------------------------------
    for audio_path in audio_files:
        print(f">>> Đang xử lý file: {audio_path.name} ({audio_path.stat().st_size} bytes)")
        if not audio_path.exists():
            print(f"[ERROR] File không tồn tại: {audio_path}")
            continue

        stt_res = await transcribe_audio_file(audio_path, mime_type="audio/m4a")
        stt_results[audio_path.name] = stt_res

        print(f"    [STT] Latency: {stt_res.latency_ms} ms | Provider: {stt_res.provider}")
        print(f"    [Transcript]: \"{stt_res.text}\"")
        print()

    # --------------------------------------------------------------------------
    # BƯỚC 2: EXTRACT TASK INTENT CHO TỪNG TRANSCRIPT
    # --------------------------------------------------------------------------
    for audio_path in audio_files:
        name = audio_path.name
        if name not in stt_results:
            continue

        transcript = stt_results[name].text
        print("-" * 80)
        print(f"PHÂN TÍCH Ý ĐỊNH AI CHO: {name}")
        print("-" * 80)

        t0 = time.perf_counter()
        extracted = await ai_service.extract_task_intent(
            transcript, timezone="Asia/Ho_Chi_Minh"
        )
        ai_latency_ms = int((time.perf_counter() - t0) * 1000)
        extraction_results[name] = extracted

        can_plan_raw, prereq_issues = evaluate_can_plan(extracted)

        print(f"  * Tier: {extracted.generation_tier} | AI Latency: {ai_latency_ms} ms")
        print(f"  * Title: {extracted.title}")
        print(f"  * Content: {extracted.content}")
        print(f"  * Scope: {extracted.scope}")
        print(f"  * Deadline: {extracted.deadline}")
        print(f"  * Time Constraint: {extracted.time_constraint}")
        print(f"  * Free Time Slots: {extracted.free_time_slots}")
        print(f"  * Structured Windows: {extracted.structured_free_windows}")
        print(f"  * is_complete (UF1 Schema): {extracted.is_complete}")
        print(f"  * has_conflict: {extracted.has_conflict}")
        print(f"  * can_plan (A02.2 Engine Prerequisites): {can_plan_raw}")
        print(f"  * clarification_questions ({len(extracted.clarification_questions)}):")
        for q in extracted.clarification_questions:
            print(f"      - {q}")
        print(f"  * clarification_issues ({len(extracted.clarification_issues)}):")
        for issue in extracted.clarification_issues:
            print(f"      - [{issue.code}] {issue.field_path}: {issue.question} (blocking={issue.blocking})")
        if prereq_issues:
            print(f"  * Prerequisite Issues ({len(prereq_issues)}):")
            for p_issue in prereq_issues:
                print(f"      - [{p_issue.code}] {p_issue.field_path}: {p_issue.question}")
        print()

    # --------------------------------------------------------------------------
    # BƯỚC 3 & 4: THỬ NGHIỆM BỔ SUNG THÔNG TIN (SUPPLEMENTATION ROUNDS)
    # --------------------------------------------------------------------------
    print("=" * 80)
    print("BƯỚC 3 & 4: KIỂM TRA ĐẶC TẢ VOICE 2 & THỬ NGHIỆM BỔ SUNG (SUPPLEMENTATION)")
    print("=" * 80)

    # Thử nghiệm 4A: Bổ sung thông tin cho Voice 3 (Đọc sách Lean Code - bị thiếu giờ rảnh & start_at)
    if "test3.m4a" in extraction_results:
        print("\n--- [KỊCH BẢN A]: BỔ SUNG CHO VOICE 3 (test3.m4a - Đọc sách Lean Code) ---")
        prev_r3 = extraction_results["test3.m4a"]
        print(f"Trước bổ sung: is_complete={prev_r3.is_complete}, questions={prev_r3.clarification_questions}")

        supp_text_3 = (
            "Mình rảnh từ 19:00 đến 22:00 tối mai để đọc sách, bắt đầu lúc 19:00"
        )
        print(f"Câu trả lời bổ sung: \"{supp_text_3}\"")

        t0 = time.perf_counter()
        r3_supp = await ai_service.extract_task_intent(
            supp_text_3,
            timezone="Asia/Ho_Chi_Minh",
            previous_draft=prev_r3,
        )
        supp_latency_ms = int((time.perf_counter() - t0) * 1000)

        can_plan_supp_3, issues_supp_3 = evaluate_can_plan(
            r3_supp, explicit_start_at=datetime.fromisoformat("2026-11-08T19:00:00+07:00")
        )

        print(f"  * Sau bổ sung is_complete: {r3_supp.is_complete}")
        print(f"  * Sau bổ sung can_plan: {can_plan_supp_3}")
        print(f"  * Title: {r3_supp.title}")
        print(f"  * Content: {r3_supp.content}")
        print(f"  * Scope: {r3_supp.scope}")
        print(f"  * Deadline: {r3_supp.deadline}")
        print(f"  * Free Time Slots: {r3_supp.free_time_slots}")
        print(f"  * Structured Windows: {r3_supp.structured_free_windows}")
        print(f"  * clarification_questions: {r3_supp.clarification_questions}")
        print(f"  * clarification_issues: {r3_supp.clarification_issues}")
        print(f"  * resolved_issue_keys: {r3_supp.resolved_issue_keys}")
        print(f"  * Prerequisite issues remaining: {len(issues_supp_3)}")

    # Thử nghiệm 4B: Bổ sung start_at cho Voice 2 (Slide thuyết trình AI)
    if "test2.m4a" in extraction_results:
        print("\n--- [KỊCH BẢN B]: BỔ SUNG CHO VOICE 2 (test2.m4a - Slide Trí tuệ nhân tạo) ---")
        prev_r2 = extraction_results["test2.m4a"]
        can_plan_2_before, issues_2_before = evaluate_can_plan(prev_r2)
        print(
            f"Trước bổ sung: is_complete={prev_r2.is_complete}, "
            f"can_plan={can_plan_2_before}, "
            f"prereq_issues={[i.code for i in issues_2_before]}"
        )

        supp_text_2 = "Mình bắt đầu làm từ 8:00 sáng thứ Bảy nhé, hạn chót vẫn là 12:00 trưa Chủ Nhật."
        print(f"Câu trả lời bổ sung: \"{supp_text_2}\"")

        t0 = time.perf_counter()
        r2_supp = await ai_service.extract_task_intent(
            supp_text_2,
            timezone="Asia/Ho_Chi_Minh",
            previous_draft=prev_r2,
        )

        can_plan_supp_2, issues_supp_2 = evaluate_can_plan(
            r2_supp, explicit_start_at=datetime.fromisoformat("2026-11-08T08:00:00+07:00")
        )

        print(f"  * Sau bổ sung is_complete: {r2_supp.is_complete}")
        print(f"  * Sau bổ sung can_plan: {can_plan_supp_2}")
        print(f"  * Title: {r2_supp.title}")
        print(f"  * Content: {r2_supp.content}")
        print(f"  * Scope: {r2_supp.scope}")
        print(f"  * Deadline: {r2_supp.deadline}")
        print(f"  * Free Time Slots: {r2_supp.free_time_slots}")
        print(f"  * Structured Windows: {r2_supp.structured_free_windows}")
        print(f"  * clarification_questions: {r2_supp.clarification_questions}")
        print(f"  * Prerequisite issues remaining: {len(issues_supp_2)}")

    print("\n" + "=" * 80)
    print("HOÀN TẤT BÀI KIỂM THỬ PROBE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
