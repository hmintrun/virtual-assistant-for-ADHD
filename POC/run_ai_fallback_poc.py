#!/usr/bin/env python3
"""
================================================================================
PROPLAN VA — PROOF OF CONCEPT (POC) DEMO & TEST SUITE
Cơ chế Phục hồi Đa tầng (3-Tier AI Fallback) kết hợp Thuần Python
================================================================================
Mục đích:
  Kiểm chứng và trình diễn trực quan cơ chế phục hồi chất lượng cao (Quality-First):
  Gemini 3.8 Flash -> Gemini 3.5 Flash-Lite (RAM) -> Groq Qwen/GPT-OSS -> Thuần Python.
  Provider được giả lập. Bộ điều phối chạy với cấu hình Khung A/B theo Project.md.
  
Cách chạy độc lập (CLI Demo):
  backend\\.venv\\Scripts\\python.exe POC/run_ai_fallback_poc.py

Cách chạy qua Pytest:
  backend\\.venv\\Scripts\\python.exe -m pytest POC/run_ai_fallback_poc.py -v
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Bắt buộc mã hóa UTF-8 cho Windows Terminal / Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

POC_DIR = ROOT_DIR / "POC"
LOGS_DIR = POC_DIR / "logs"

from app.core.config import Settings
from app.schemas.task import (
    LLMTaskDecompositionPayload,
    LLMTaskExtractionPayload,
    StepPromptItem,
    TaskDecompositionResult,
    TaskExtractionResult,
)
from app.services.ai_service import AIService

# ==============================================================================
# ĐỊNH DẠNG MÀU ANSI & GIAO DIỆN TERMINAL
# ==============================================================================
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"
    BG_RED = "\033[41m"

def print_header(title: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.WHITE}>> {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")

def print_step(msg: str) -> None:
    print(f"  {Colors.BOLD}{Colors.BLUE}[TIẾN TRÌNH]{Colors.RESET} {msg}")

def print_sim(msg: str) -> None:
    print(f"  {Colors.BOLD}{Colors.YELLOW}[THỜI GIAN THỰC]{Colors.RESET} {msg}")

def print_error(msg: str) -> None:
    print(f"  {Colors.BOLD}{Colors.RED}[LỖI / TREO]{Colors.RESET} {msg}")

def print_fallback(msg: str) -> None:
    print(f"  {Colors.BOLD}{Colors.MAGENTA}[CHUYỂN TẦNG]{Colors.RESET} {msg}")

def print_success(msg: str) -> None:
    print(f"  {Colors.BOLD}{Colors.GREEN}[THÀNH CÔNG]{Colors.RESET} {msg}")

def print_info(key: str, val: Any) -> None:
    print(f"      {Colors.DIM}• {key}:{Colors.RESET} {Colors.BOLD}{val}{Colors.RESET}")


# ==============================================================================
# HỆ THỐNG GHI LOG VÀ THEO DÕI TIMELINE (EXECUTION TRACE RECORDER)
# ==============================================================================
@dataclass
class ModelTraceEntry:
    model_name: str
    tier: str
    called: bool
    not_called_reason: str | None = None
    call_started_at: str | None = None
    call_completed_at: str | None = None
    relative_start_s: float | None = None
    relative_end_s: float | None = None
    latency_s: float | None = None
    status: str = "SKIPPED"
    error_message: str | None = None
    output_summary: dict[str, Any] | str | None = None
    chosen_as_final: bool = False

@dataclass
class ScenarioTraceRecord:
    scenario_id: str
    scenario_name: str
    description: str
    start_time: str
    end_time: str
    total_duration_s: float
    models_trace: list[ModelTraceEntry] = field(default_factory=list)
    decision_timestamp: str = ""
    decision_relative_s: float = 0.0
    winner_model: str = ""
    winner_tier: str = ""
    decision_rationale: str = ""
    final_output: dict[str, Any] = field(default_factory=dict)
    passed: bool = False


class GlobalTraceRecorder:
    def __init__(self) -> None:
        self.records: list[ScenarioTraceRecord] = []

    def add_record(self, record: ScenarioTraceRecord) -> None:
        self.records.append(record)

    def export_json(self, path: Path) -> None:
        payload = {
            "project": "ProPlan VA",
            "test_suite": "AI Fallback POC (mock providers, standard A/B timing)",
            "exported_at": datetime.now(UTC).isoformat(),
            "total_scenarios": len(self.records),
            "all_passed": all(r.passed for r in self.records),
            "scenarios": [asdict(r) for r in self.records],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def export_text_log(self, path: Path) -> None:
        lines: list[str] = [
            "=" * 95,
            "PROPLAN VA — AI MULTI-TIER FALLBACK DETAILED EXECUTION TRACE LOG (PRODUCTION SCALE)",
            f"Thời điểm xuất log: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Tổng số kịch bản: {len(self.records)} | Kết quả: {'100% ĐẠT' if all(r.passed for r in self.records) else 'CÓ LỖI'}",
            "=" * 95,
            "",
        ]

        for idx, rec in enumerate(self.records, 1):
            lines.extend([
                "-" * 95,
                f"KỊCH BẢN #{idx}: {rec.scenario_name} (ID: {rec.scenario_id})",
                f"Mô tả: {rec.description}",
                f"Bắt đầu: {rec.start_time} | Kết thúc: {rec.end_time} | Tổng thời gian chạy: {rec.total_duration_s:.3f} giây",
                "-" * 95,
                "DANH SÁCH CÁC MODEL VÀ TRẠNG THÁI GỌI:",
            ])

            for m in rec.models_trace:
                called_str = "[ĐÃ GỌI]" if m.called else "[KHÔNG GỌI]"
                lines.append(f"  * Model: {m.model_name:<30} | Tầng: {m.tier:<24} | {called_str}")
                if not m.called:
                    lines.append(f"      - Lý do bỏ qua : {m.not_called_reason}")
                else:
                    lines.append(f"      - Bắt đầu gọi  : {m.call_started_at} (t = +{m.relative_start_s:.3f}s)")
                    if m.call_completed_at:
                        lines.append(f"      - Trả về lúc   : {m.call_completed_at} (t = +{m.relative_end_s:.3f}s)")
                        lines.append(f"      - Thời gian đợi: {m.latency_s:.3f} giây")
                    lines.append(f"      - Trạng thái   : {m.status}")
                    if m.error_message:
                        lines.append(f"      - Chi tiết lỗi : {m.error_message}")
                    if m.output_summary:
                        out_str = json.dumps(m.output_summary, ensure_ascii=False) if isinstance(m.output_summary, dict) else str(m.output_summary)
                        lines.append(f"      - Dữ liệu ra   : {out_str}")
                chosen_str = ">>> ĐƯỢC CHỌN LÀM OUTPUT CUỐI CÙNG <<<" if m.chosen_as_final else "Không chọn làm output cuối"
                lines.append(f"      - Quyết định   : {chosen_str}")
                lines.append("")

            lines.extend([
                "MỐC QUYẾT ĐỊNH ĐẦU RA CUỐI CÙNG (DECISION MILESTONE):",
                f"  • Thời điểm chốt  : {rec.decision_timestamp} (ở mốc t = +{rec.decision_relative_s:.3f} giây)",
                f"  • Model chiến thắng: {rec.winner_model} ({rec.winner_tier})",
                f"  • Căn cứ lựa chọn : {rec.decision_rationale}",
                f"  • Dữ liệu cuối    : {json.dumps(rec.final_output, ensure_ascii=False)}",
                f"  • Đánh giá nghiệm thu: {'ĐẠT (PASSED)' if rec.passed else 'THẤT BẠI (FAILED)'}",
                "",
            ])

        lines.extend([
            "=" * 95,
            "KẾT THÚC BẢNG TRACE LOG",
            "=" * 95,
        ])
        path.write_text("\n".join(lines), encoding="utf-8")

    def export_markdown_summary(self, path: Path) -> None:
        md_lines: list[str] = [
            "# Báo cáo Chi tiết Trace Logs: Cơ chế 3-Tier AI Fallback $\\rightarrow$ Thuần Python (Thang đo Giây Thực tế)",
            "",
            f"- **Thời gian xuất báo cáo:** `{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            f"- **Tổng số kịch bản:** `{len(self.records)}`",
            f"- **Kết quả:** `{'100% PASSED' if all(r.passed for r in self.records) else 'FAILED'}`",
            "",
            "## 1. Bảng Ma trận Kiểm soát Model & Quyết định Chuyển tầng",
            "",
            "| Kịch bản | Model được gọi | Model bỏ qua (Không gọi) | Model chiến thắng | Tổng thời gian | Kết quả |",
            "| :--- | :--- | :--- | :--- | :---: | :--- |",
        ]

        for rec in self.records:
            called_models = ", ".join(m.model_name for m in rec.models_trace if m.called)
            skipped_models = ", ".join(m.model_name for m in rec.models_trace if not m.called) or "*(Không có)*"
            md_lines.append(
                f"| **{rec.scenario_name}** | `{called_models}` | {skipped_models} | **`{rec.winner_model}`** | **{rec.total_duration_s:.2f}s** | {'ĐẠT' if rec.passed else 'THẤT BẠI'} |"
            )

        md_lines.extend([
            "",
            "## 2. Chi tiết Từng Kịch bản & Dòng Thời gian (Timeline)",
            "",
        ])

        for idx, rec in enumerate(self.records, 1):
            md_lines.extend([
                f"### {idx}. {rec.scenario_name}",
                f"- **Mô tả:** {rec.description}",
                f"- **Thời gian bắt đầu:** `{rec.start_time}`",
                f"- **Thời gian quyết định:** `{rec.decision_timestamp}` (ở mốc `+{rec.decision_relative_s:.3f} giây`)",
                f"- **Model chiến thắng:** `{rec.winner_model}` (`{rec.winner_tier}`)",
                f"- **Ghi chú:** {rec.decision_rationale}",
                "",
                "#### Timeline & Chi tiết Từng Model:",
                "",
                "| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |",
                "| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |",
            ])

            for m in rec.models_trace:
                called_icon = "✅ Có" if m.called else "❌ Không"
                start_str = f"+{m.relative_start_s:.2f}s" if m.relative_start_s is not None else "-"
                end_str = f"+{m.relative_end_s:.2f}s" if m.relative_end_s is not None else "-"
                info = m.error_message if m.error_message else ("Có dữ liệu hợp lệ" if m.output_summary else m.not_called_reason)
                chosen_icon = "⭐ **CHỌN**" if m.chosen_as_final else "Không"
                md_lines.append(
                    f"| `{m.model_name}` | {m.tier} | {called_icon} | {start_str} | {end_str} | `{m.status}` | {info} | {chosen_icon} |"
                )

            md_lines.extend([
                "",
                f"> **Dữ liệu Final Output:** `{json.dumps(rec.final_output, ensure_ascii=False)}`",
                "",
                "---",
                "",
            ])

        path.write_text("\n".join(md_lines), encoding="utf-8")


global_tracer = GlobalTraceRecorder()
# Dung sai chỉ dành cho phép đo wall clock trong test trên hệ điều hành.
TIMING_TOLERANCE_S = 0.2


def at_milestone(actual: float, expected: float) -> bool:
    return expected - 0.01 <= actual < expected + TIMING_TOLERANCE_S


async def stall_provider(
    traces: list[ModelTraceEntry],
    start: float,
    model: str,
    tier: str,
    *,
    cleanup_delay: float = 0.0,
) -> None:
    call_start = time.perf_counter()
    trace = ModelTraceEntry(
        model_name=model,
        tier=tier,
        called=True,
        call_started_at=datetime.now(UTC).isoformat(),
        relative_start_s=call_start - start,
        status="PENDING",
    )
    traces.append(trace)
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cancelled_at = time.perf_counter()
        trace.call_completed_at = datetime.now(UTC).isoformat()
        trace.relative_end_s = cancelled_at - start
        trace.latency_s = cancelled_at - call_start
        trace.status = "CANCELLED"
        if cleanup_delay:
            await asyncio.sleep(cleanup_delay)
        raise


def mark_winner(traces: list[ModelTraceEntry], model: str) -> str:
    for trace in traces:
        trace.chosen_as_final = trace.model_name == model and trace.status == "SUCCESS"
    return model if any(trace.chosen_as_final for trace in traces) else ""


# ==============================================================================
# KỊCH BẢN 1: FAST-FAIL TỨC THÌ (GOOGLE LỖI PHÂN QUYỀN / QUOTA -> GROQ NHẢY TỨC THÌ)
# ==============================================================================
async def scenario_1_fast_fail_gemini_to_groq() -> bool:
    print_header("KỊCH BẢN 1: Fast-Fail Tức Thì (Google Lỗi Nhanh -> Groq Cứu Hộ Trước Hedge)")
    print_step("Mô phỏng tình huống: Gemini API gặp lỗi phân quyền hoặc hết tiền (HTTP 402/403/503).")
    
    t_start_iso = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    models_trace: list[ModelTraceEntry] = []

    cfg = Settings(
        _env_file=None,
        GEMINI_API_KEY="AIzaSy_Simulated_Gemini_Key",
        GROQ_API_KEY="gsk_Simulated_Groq_Key",
        GOOGLE_CREDENTIALS_PATH="",
        AI_WARM_BACKUP_DELAY_SECONDS=2.6,
        AI_GROQ_HEDGE_DELAY_SECONDS=3.5,
        AI_PRIMARY_PRIORITY_SECONDS=4.0,
        AI_TIMEOUT_SECONDS=4.5,
    )
    service = AIService(app_settings=cfg)

    async def _failing_gemini(*, model_name: str, **_: Any) -> LLMTaskExtractionPayload:
        m_start = time.perf_counter()
        m_start_iso = datetime.now(UTC).isoformat()
        print_error(f"Google {model_name} ném PermissionError (HTTP 403 Forbidden).")
        m_end = time.perf_counter()
        models_trace.append(
            ModelTraceEntry(
                model_name=model_name,
                tier="tier_1_gemini",
                called=True,
                call_started_at=m_start_iso,
                call_completed_at=datetime.now(UTC).isoformat(),
                relative_start_s=m_start - t0,
                relative_end_s=m_end - t0,
                latency_s=m_end - m_start,
                status="FAILED",
                error_message="PermissionError: Google Gemini API unavailable (HTTP 403 Forbidden)",
            )
        )
        raise PermissionError("Google Gemini API unavailable")

    async def _working_groq(**_: Any) -> LLMTaskExtractionPayload:
        m_start = time.perf_counter()
        m_start_iso = datetime.now(UTC).isoformat()
        print_fallback("Tầng 2 (Groq Cloud LPU) lập tức tiếp quản với model 'qwen/qwen3.8-27b'.")
        payload = LLMTaskExtractionPayload(
            title="Tiểu luận Triết học Mác - Lênin",
            content="Viết tiểu luận Triết học về chủ nghĩa duy vật biện chứng",
            scope="10 trang",
            free_time_slots=["Tối thứ 4 từ 19h-21h"],
            deadline=datetime.now() + timedelta(days=3),
            is_complete=True,
            has_conflict=False,
            summary="Groq Qwen đã trích xuất thành công trong lúc Google gián đoạn.",
            clarification_questions=[],
        )
        m_end = time.perf_counter()
        models_trace.append(
            ModelTraceEntry(
                model_name="qwen/qwen3.8-27b",
                tier="tier_2_groq",
                called=True,
                call_started_at=m_start_iso,
                call_completed_at=datetime.now(UTC).isoformat(),
                relative_start_s=m_start - t0,
                relative_end_s=m_end - t0,
                latency_s=m_end - m_start,
                status="SUCCESS",
                output_summary={
                    "title": payload.title,
                    "scope": payload.scope,
                    "is_complete": payload.is_complete,
                },
            )
        )
        return payload

    with (
        patch.object(service, "_get_gemini_client", return_value=object()),
        patch.object(service, "_get_groq_client", return_value=object()),
        patch.object(service, "_call_gemini_structured", side_effect=_failing_gemini),
        patch.object(service, "_call_groq_structured", side_effect=_working_groq),
    ):
        result = await service.extract_task_intent("Viết tiểu luận Triết 10 trang")
    
    t_end = time.perf_counter()
    elapsed_s = t_end - t0
    t_end_iso = datetime.now(UTC).isoformat()

    print_info("Tầng AI phục vụ", result.generation_tier)
    print_info("Tiêu đề trích xuất", result.title)
    print_info("Thời gian chuyển giao", f"{elapsed_s:.3f} giây")

    winner = mark_winner(models_trace, "qwen/qwen3.8-27b" if result.generation_tier == "tier_2_groq" else "")
    passed = (
        bool(winner)
        and [trace.model_name for trace in models_trace]
        == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "qwen/qwen3.8-27b"]
        and elapsed_s < cfg.AI_GROQ_HEDGE_DELAY_SECONDS
    )

    rec = ScenarioTraceRecord(
        scenario_id="scenario_1_fast_fail",
        scenario_name="Fast-Fail Tức Thì (Google Lỗi Nhanh -> Groq Cứu Hộ)",
        description="Cả hai model Gemini lỗi nhanh. Groq được gọi trước mốc hedge 3.5s.",
        start_time=t_start_iso,
        end_time=t_end_iso,
        total_duration_s=elapsed_s,
        models_trace=models_trace,
        decision_timestamp=t_end_iso,
        decision_relative_s=elapsed_s,
        winner_model=winner,
        winner_tier=result.generation_tier,
        decision_rationale="Kỳ vọng chuyển sang Groq ngay sau khi cả hai model Gemini báo lỗi.",
        final_output={"title": result.title, "content": result.content, "is_complete": result.is_complete},
        passed=passed,
    )
    global_tracer.add_record(rec)

    return passed


# ==============================================================================
# KỊCH BẢN 2: CỬA SỔ ƯU TIÊN CHẤT LƯỢNG (CHỜ 3.8 FLASH TRONG CỬA SỔ 4.0s DÙ LITE XONG TRƯỚC)
# ==============================================================================
async def scenario_2_quality_first_priority_window() -> bool:
    print_header("KỊCH BẢN 2: Cửa sổ Ưu tiên Chất lượng (Quality-First Priority Window)")
    print_step("Cấu hình thời gian thực tế: Primary Priority Window = 4.0 giây.")
    print_step("Mô phỏng: Warm Backup (Flash-Lite) chạy nhanh xong trước lúc t = 2.9s và nạp vào RAM,")
    print_step("nhưng hệ thống vẫn kiên nhẫn đợi Primary (3.8 Flash) hoàn thành lúc t = 3.4s để lấy chất lượng cao nhất.")

    t_start_iso = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    models_trace: list[ModelTraceEntry] = []

    cfg = Settings(
        _env_file=None,
        GEMINI_API_KEY="AIzaSy_Simulated_Gemini_Key",
        GROQ_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
    )
    service = AIService(app_settings=cfg)

    async def _fake_gemini_call(*, model_name: str | None = None, **_: Any) -> LLMTaskExtractionPayload:
        m_start = time.perf_counter()
        m_start_iso = datetime.now(UTC).isoformat()

        if model_name == "gemini-3.8-flash":
            print_sim("[0.00s] Đang gọi Gemini 3.8 Flash (Primary, tư duy sâu)...")
            await asyncio.sleep(3.4)  # Mất 3.4 giây (vẫn nằm trong cửa sổ ưu tiên 4.0s)
            m_end = time.perf_counter()
            print_step(f"[+3.40s] Gemini 3.8 Flash hoàn tất xử lý (chất lượng cao) trong cửa sổ ưu tiên!")
            payload = LLMTaskExtractionPayload(
                title="Bản chuẩn chất lượng cao từ Gemini 3.8 Flash",
                content="Làm slide đồ án tốt nghiệp",
                scope="15 slide PowerPoint chi tiết kèm sơ đồ kiến trúc",
                free_time_slots=["Chiều thứ 7 14h-17h"],
                deadline=datetime.now() + timedelta(days=2),
                is_complete=True,
                has_conflict=False,
                summary="Phân tích ngữ nghĩa đầy đủ và ấm áp từ Gemini 3.8 Flash.",
                clarification_questions=[],
            )
            models_trace.append(
                ModelTraceEntry(
                    model_name="gemini-3.8-flash",
                    tier="tier_1_gemini",
                    called=True,
                    call_started_at=m_start_iso,
                    call_completed_at=datetime.now(UTC).isoformat(),
                    relative_start_s=m_start - t0,
                    relative_end_s=m_end - t0,
                    latency_s=m_end - m_start,
                    status="SUCCESS",
                    output_summary={"title": payload.title, "scope": payload.scope},
                )
            )
            return payload

        # Mô phỏng Gemini 3.5 Flash-Lite (Warm Backup kích hoạt lúc 2.6s)
        await asyncio.sleep(0.3)  # Chạy 0.3s -> Xong lúc 2.6s + 0.3s = 2.9s
        m_end = time.perf_counter()
        print_fallback(f"[+{m_end - t0:.2f}s] Flash-Lite đã trả dữ liệu dự phòng.")
        payload_backup = LLMTaskExtractionPayload(
            title="Bản dự phòng nông từ Flash-Lite",
            content="Làm slide đồ án",
            scope="15 slide",
            free_time_slots=["Chiều thứ 7 14h-17h"],
            deadline=datetime.now() + timedelta(days=2),
            is_complete=True,
            has_conflict=False,
            summary="Bản tóm tắt cơ bản từ Flash-Lite.",
            clarification_questions=[],
        )
        models_trace.append(
            ModelTraceEntry(
                model_name="gemini-3.5-flash-lite",
                tier="tier_1_gemini (warm_backup)",
                called=True,
                call_started_at=m_start_iso,
                call_completed_at=datetime.now(UTC).isoformat(),
                relative_start_s=m_start - t0,
                relative_end_s=m_end - t0,
                latency_s=m_end - m_start,
                status="SUCCESS",
                output_summary={"title": payload_backup.title, "scope": payload_backup.scope},
            )
        )
        return payload_backup

    with (
        patch.object(service, "_get_gemini_client", return_value=object()),
        patch.object(service, "_call_gemini_structured", side_effect=_fake_gemini_call),
    ):
        result = await service.extract_task_intent("Làm 15 slide đồ án tốt nghiệp")

    t_end = time.perf_counter()
    elapsed_s = t_end - t0
    t_end_iso = datetime.now(UTC).isoformat()

    models_trace.append(
        ModelTraceEntry(
            model_name="qwen/qwen3.8-27b",
            tier="tier_2_groq",
            called=False,
            not_called_reason="Groq không được cấu hình trong kịch bản này",
            status="SKIPPED",
        )
    )

    print_info("Tầng AI chiến thắng", result.generation_tier)
    print_info("Tiêu đề được chọn", result.title)
    print_info("Tổng thời gian thực tế", f"{elapsed_s:.3f} giây (Chờ 3.8 Flash thành công)")

    primary = next((trace for trace in models_trace if trace.model_name == cfg.GEMINI_FLASH_MODEL), None)
    backup = next((trace for trace in models_trace if trace.model_name == cfg.GEMINI_BACKUP_MODEL), None)
    winner = mark_winner(
        models_trace,
        next(
            (trace.model_name for trace in models_trace
             if isinstance(trace.output_summary, dict) and trace.output_summary.get("title") == result.title),
            "",
        ),
    )
    passed = (
        result.generation_tier == "tier_1_gemini"
        and winner == cfg.GEMINI_FLASH_MODEL
        and primary is not None
        and backup is not None
        and backup.relative_start_s is not None
        and at_milestone(backup.relative_start_s, cfg.AI_WARM_BACKUP_DELAY_SECONDS)
        and backup.relative_end_s is not None
        and primary.relative_end_s is not None
        and backup.relative_end_s < primary.relative_end_s
        and elapsed_s < cfg.AI_PRIMARY_PRIORITY_SECONDS
    )

    rec = ScenarioTraceRecord(
        scenario_id="scenario_2_quality_first",
        scenario_name="Cửa sổ Ưu tiên Chất lượng (Quality-First Priority Window)",
        description="Flash-Lite trong RAM xong sớm (2.9s) nhưng 3.8 Flash hoàn thành lúc 3.4s (nằm trong cửa sổ 4.0s) -> Hệ thống kiên quyết chọn 3.8 Flash",
        start_time=t_start_iso,
        end_time=t_end_iso,
        total_duration_s=elapsed_s,
        models_trace=models_trace,
        decision_timestamp=t_end_iso,
        decision_relative_s=elapsed_s,
        winner_model=winner,
        winner_tier=result.generation_tier,
        decision_rationale="Gemini 3.8 Flash hoàn tất lúc 3.4s (nằm trong cửa sổ ưu tiên 4.0s); hệ thống kiên nhẫn nhận output chất lượng cao dù 3.5 Lite trong RAM xong sớm hơn",
        final_output={"title": result.title, "scope": result.scope},
        passed=passed,
    )
    global_tracer.add_record(rec)

    return passed


# ==============================================================================
# KỊCH BẢN 3: TIER 1 TRẢ VỀ QUÁ LÂU (> 7.5 GIÂY) -> THU HOẠCH BẢN NHÁP RAM TẠI MỐC 7.5s
# ==============================================================================
async def scenario_3_giant_task_warm_backup_ram_harvest() -> bool:
    print_header("KỊCH BẢN 3: TIER 1 BỊ TREO/TRỄ QUÁ LÂU (> 7.5s) -> BUNG BẢN NHÁP RAM CỨU HỘ")
    print_step("Mô phỏng bài toán lớn (Khóa luận tốt nghiệp):")
    print_step("• Gemini 3.8 Flash treo cho đến khi bị hủy.")
    print_step("• Cửa sổ ưu tiên giới hạn tại t = 7.5 giây. Đúng mốc 7.5s, hệ thống KHÔNG đợi 3.8 Flash nữa,")
    print_step("  mà lập tức bung bản chia bước của Gemini 3.5 Flash-Lite đã tính sẵn trong RAM từ mốc 3.5s!")

    t_start_iso = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    models_trace: list[ModelTraceEntry] = []

    cfg = Settings(
        _env_file=None,
        GEMINI_API_KEY="AIzaSy_Simulated_Gemini_Key",
        GROQ_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
    )
    service = AIService(app_settings=cfg)

    async def _fake_decomp_call(*, model_name: str | None = None, **_: Any) -> LLMTaskDecompositionPayload:
        m_start = time.perf_counter()
        m_start_iso = datetime.now(UTC).isoformat()

        if model_name == "gemini-3.8-flash":
            await stall_provider(models_trace, t0, model_name, "tier_1_gemini (primary)")
            raise AssertionError("Primary phải bị hủy khi chốt kết quả dự phòng")

        # Warm Backup bắt đầu ở mốc 3.1s
        await asyncio.sleep(0.4)  # Mất 0.4s -> Xong ở 3.1s + 0.4s = 3.5s
        m_end = time.perf_counter()
        print_fallback(f"[+{m_end - t0:.2f}s] Flash-Lite đã trả phương án chia bước.")
        payload_lite = LLMTaskDecompositionPayload(
            task_title="Khóa luận tốt nghiệp Đại học",
            steps=[
                StepPromptItem(
                    order_index=1,
                    title="Thiết lập khung cấu trúc luận văn LaTeX",
                    expected_output="File main.tex và các thư mục chương đã sẵn sàng.",
                    estimated_minutes=30,
                    micro_task_prompt="Tạo file main.tex trong thư mục luận văn.",
                    cognitive_load="light",
                ),
                StepPromptItem(
                    order_index=2,
                    title="Viết phần Giới thiệu và Đặt bài toán",
                    expected_output="Bản thảo 3 trang chương 1 hoàn thành.",
                    estimated_minutes=45,
                    micro_task_prompt="Gõ tiêu đề Chương 1 và mục 1.1.",
                    cognitive_load="medium",
                ),
            ],
            reasoning="Phương án bẻ nhỏ cứu hộ từ Warm Backup Gemini 3.5 Flash-Lite (cứu nguy khi 3.8 Flash bị treo).",
        )
        models_trace.append(
            ModelTraceEntry(
                model_name="gemini-3.5-flash-lite",
                tier="tier_1_gemini (warm_backup)",
                called=True,
                call_started_at=m_start_iso,
                call_completed_at=datetime.now(UTC).isoformat(),
                relative_start_s=m_start - t0,
                relative_end_s=m_end - t0,
                latency_s=m_end - m_start,
                status="SUCCESS",
                output_summary={"steps_count": len(payload_lite.steps), "reasoning": payload_lite.reasoning},
            )
        )
        return payload_lite

    with (
        patch.object(service, "_get_gemini_client", return_value=object()),
        patch.object(service, "_call_gemini_structured", side_effect=_fake_decomp_call),
    ):
        result = await service.decompose_task(task_title="Khóa luận tốt nghiệp Đại học")

    t_end = time.perf_counter()
    elapsed_s = t_end - t0
    t_end_iso = datetime.now(UTC).isoformat()
    await asyncio.sleep(0)  # Cho coroutine đã bị hủy cơ hội ghi sự kiện.

    print_info("Tầng AI", result.generation_tier)
    print_info("Lý do suy luận", result.reasoning)
    print_info("Số bước được chia", len(result.steps))
    print_info("Thời gian phản hồi thực tế", f"{elapsed_s:.3f} giây")

    backup = next((trace for trace in models_trace if trace.model_name == cfg.GEMINI_BACKUP_MODEL), None)
    winner = mark_winner(
        models_trace,
        cfg.GEMINI_BACKUP_MODEL if "Warm Backup" in (result.reasoning or "") else "",
    )
    passed = (
        result.generation_tier == "tier_1_gemini"
        and winner == cfg.GEMINI_BACKUP_MODEL
        and len(result.steps) == 2
        and backup is not None
        and backup.relative_start_s is not None
        and at_milestone(backup.relative_start_s, cfg.AI_UF2_WARM_BACKUP_DELAY_SECONDS)
        and any(trace.status == "CANCELLED" for trace in models_trace)
        and at_milestone(elapsed_s, cfg.AI_UF2_PRIMARY_PRIORITY_SECONDS)
    )

    rec = ScenarioTraceRecord(
        scenario_id="scenario_3_tier1_lag_ram_harvest",
        scenario_name="Tier 1 Trả Về Quá Lâu (> 7.5s) -> Bung Bản Nháp RAM Cứu Hộ",
        description="Primary treo; Flash-Lite phản hồi trước khi kết thúc cửa sổ ưu tiên 7.5s.",
        start_time=t_start_iso,
        end_time=t_end_iso,
        total_duration_s=elapsed_s,
        models_trace=models_trace,
        decision_timestamp=t_end_iso,
        decision_relative_s=elapsed_s,
        winner_model=winner,
        winner_tier=result.generation_tier,
        decision_rationale="Kỳ vọng chọn Flash-Lite tại mốc 7.5s của Khung B.",
        final_output={"steps_count": len(result.steps), "reasoning": result.reasoning},
        passed=passed,
    )
    global_tracer.add_record(rec)

    return passed


# ==============================================================================
# KỊCH BẢN 4: TOÀN BỘ CỤM GOOGLE BỊ TREO QUÁ HARD TIMEOUT (4.5s) -> GROQ LPU CỨU HỘ
# ==============================================================================
async def scenario_4_total_gemini_timeout_to_groq() -> bool:
    print_header("KỊCH BẢN 4: Cả Hai Model Google Bị Treo -> Groq Cứu Hộ")
    print_step("Mô phỏng sự cố mạng quốc tế: Cả Gemini 3.8 Flash lẫn 3.5 Flash-Lite đều bị treo không phản hồi.")
    print_step("Hệ thống có mốc Groq Hedge lúc t = 3.5s và kết thúc cửa sổ ưu tiên tại 4.0s để Groq Cloud LPU tiếp quản thành công.")

    t_start_iso = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    models_trace: list[ModelTraceEntry] = []

    cfg = Settings(
        _env_file=None,
        GEMINI_API_KEY="AIzaSy_Simulated_Gemini_Key",
        GROQ_API_KEY="gsk_Simulated_Groq_Key",
        GOOGLE_CREDENTIALS_PATH="",
    )
    service = AIService(app_settings=cfg)

    async def _hanging_gemini(*, model_name: str, **_: Any) -> None:
        await stall_provider(models_trace, t0, model_name, "tier_1_gemini")

    async def _working_groq(**_: Any) -> LLMTaskExtractionPayload:
        m_start = time.perf_counter()
        m_start_iso = datetime.now(UTC).isoformat()
        print_fallback(f"[+{m_start - t0:.2f}s] Bắt đầu gọi Groq giả lập.")
        await asyncio.sleep(0.25)
        m_end = time.perf_counter()
        print_fallback(f"[+{m_end - t0:.2f}s] Groq giả lập đã trả dữ liệu.")
        payload = LLMTaskExtractionPayload(
            title="Đề cương luận văn Thạc sĩ",
            content="Chuẩn bị đề cương nghiên cứu về AI Alignment",
            scope="8 trang A4",
            free_time_slots=["Tối thứ 5 19h-21h"],
            deadline=datetime.now() + timedelta(days=5),
            is_complete=True,
            has_conflict=False,
            summary="Groq Cloud LPU đã cứu hộ kịp thời khi toàn bộ cụm Google bị nghẽn mạng.",
            clarification_questions=[],
        )
        models_trace.append(
            ModelTraceEntry(
                model_name="qwen/qwen3.8-27b",
                tier="tier_2_groq",
                called=True,
                call_started_at=m_start_iso,
                call_completed_at=datetime.now(UTC).isoformat(),
                relative_start_s=m_start - t0,
                relative_end_s=m_end - t0,
                latency_s=m_end - m_start,
                status="SUCCESS",
                output_summary={"title": payload.title, "scope": payload.scope},
            )
        )
        return payload

    with (
        patch.object(service, "_get_gemini_client", return_value=object()),
        patch.object(service, "_get_groq_client", return_value=object()),
        patch.object(service, "_call_gemini_structured", side_effect=_hanging_gemini),
        patch.object(service, "_call_groq_structured", side_effect=_working_groq),
    ):
        result = await service.extract_task_intent("Làm đề cương luận văn Thạc sĩ 8 trang")

    t_end = time.perf_counter()
    elapsed_s = t_end - t0
    t_end_iso = datetime.now(UTC).isoformat()
    await asyncio.sleep(0)  # Ghi nhận cả hai tác vụ Gemini đã bị hủy.

    print_info("Tầng AI", result.generation_tier)
    print_info("Tiêu đề", result.title)
    print_info("Thời gian toàn trình", f"{elapsed_s:.3f} giây")

    groq = next((trace for trace in models_trace if trace.model_name == cfg.GROQ_FALLBACK_MODEL), None)
    winner = mark_winner(
        models_trace, cfg.GROQ_FALLBACK_MODEL if result.generation_tier == "tier_2_groq" else ""
    )
    passed = (
        winner == cfg.GROQ_FALLBACK_MODEL
        and "Groq" in result.summary
        and groq is not None
        and groq.relative_start_s is not None
        and at_milestone(groq.relative_start_s, cfg.AI_GROQ_HEDGE_DELAY_SECONDS)
        and {trace.model_name for trace in models_trace if trace.status == "CANCELLED"}
        == {cfg.GEMINI_FLASH_MODEL, cfg.GEMINI_BACKUP_MODEL}
        and at_milestone(elapsed_s, cfg.AI_PRIMARY_PRIORITY_SECONDS)
    )

    rec = ScenarioTraceRecord(
        scenario_id="scenario_4_total_gemini_hang_groq_rescue",
        scenario_name="Toàn Bộ Google Bị Treo -> Groq Cloud LPU Cứu Hộ",
        description="Cả hai model Gemini treo; nhánh Groq trả kết quả trước khi hết cửa sổ ưu tiên.",
        start_time=t_start_iso,
        end_time=t_end_iso,
        total_duration_s=elapsed_s,
        models_trace=models_trace,
        decision_timestamp=t_end_iso,
        decision_relative_s=elapsed_s,
        winner_model=winner,
        winner_tier=result.generation_tier,
        decision_rationale="Kỳ vọng gọi Groq tại 3.5s và chốt kết quả tại 4.0s của Khung A.",
        final_output={"title": result.title, "scope": result.scope},
        passed=passed,
    )
    global_tracer.add_record(rec)

    return passed


# ==============================================================================
# KỊCH BẢN 5: GROQ PRIMARY 429 TPM RATE-LIMIT -> GROQ SECONDARY TỰ ĐỘNG
# ==============================================================================
async def scenario_5_groq_primary_429_to_secondary() -> bool:
    print_header("KỊCH BẢN 5: Chống Nghẽn Quota (Groq Qwen 429 TPM -> Groq GPT-OSS)")
    print_step("Mô phỏng tình huống: Model chính trên Groq (Qwen 3.8) chạm trần HTTP 429 Rate Limit.")
    print_step("Hệ thống tự động điều phối chuyển sang model phụ 'openai/gpt-oss-20b'.")

    t_start_iso = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    models_trace: list[ModelTraceEntry] = []

    cfg = Settings(
        _env_file=None,
        GEMINI_API_KEY="",
        GROQ_API_KEY="gsk_Simulated_Groq_Key",
        GOOGLE_CREDENTIALS_PATH="",
        GROQ_FALLBACK_MODEL="qwen/qwen3.8-27b",
        GROQ_SECONDARY_FALLBACK_MODEL="openai/gpt-oss-20b",
    )
    service = AIService(app_settings=cfg)
    models_invoked: list[str] = []

    class _MockMessage:
        content = (
            '{"empathetic_note": "Lịch làm việc đã được sắp xếp lại nhẹ nhàng cho bạn, '
            'đừng tự trách mình nhé.", "encouragement_tip": "Uống một ngụm nước ấm và vươn vai nhé."}'
        )

    class _MockChoice:
        message = _MockMessage()

    class _MockCompletion:
        choices = [_MockChoice()]

    class _MockCompletions:
        async def create(self, *, model: str, **_: Any) -> _MockCompletion:
            m_start = time.perf_counter()
            m_start_iso = datetime.now(UTC).isoformat()
            models_invoked.append(model)

            if model == "qwen/qwen3.8-27b":
                print_error("Groq Model 'qwen/qwen3.8-27b' ném 429: 'Tokens-per-minute limit reached'.")
                m_end = time.perf_counter()
                models_trace.append(
                    ModelTraceEntry(
                        model_name="qwen/qwen3.8-27b",
                        tier="tier_2_groq (primary)",
                        called=True,
                        call_started_at=m_start_iso,
                        call_completed_at=datetime.now(UTC).isoformat(),
                        relative_start_s=m_start - t0,
                        relative_end_s=m_end - t0,
                        latency_s=m_end - m_start,
                        status="FAILED",
                        error_message="RuntimeError: 429 Rate limit reached for qwen (TPM threshold hit)",
                    )
                )
                raise RuntimeError("429 Rate limit reached for qwen")

            print_fallback(f"Tự động chuyển tiếp thành công sang Groq model phụ: '{model}'.")
            m_end = time.perf_counter()
            models_trace.append(
                ModelTraceEntry(
                    model_name="openai/gpt-oss-20b",
                    tier="tier_2_groq (secondary)",
                    called=True,
                    call_started_at=m_start_iso,
                    call_completed_at=datetime.now(UTC).isoformat(),
                    relative_start_s=m_start - t0,
                    relative_end_s=m_end - t0,
                    latency_s=m_end - m_start,
                    status="SUCCESS",
                    output_summary={"empathetic_note": "Lịch làm việc đã được sắp xếp lại nhẹ nhàng..."},
                )
            )
            return _MockCompletion()

    class _MockChat:
        completions = _MockCompletions()

    class _MockGroqClient:
        chat = _MockChat()

    with (
        patch.object(service, "_get_gemini_client", return_value=None),
        patch.object(service, "_get_groq_client", return_value=_MockGroqClient()),
    ):
        res = await service.generate_reschedule_empathy("busy")

    t_end = time.perf_counter()
    elapsed_s = t_end - t0
    t_end_iso = datetime.now(UTC).isoformat()

    print_info("Các model Groq đã gọi", models_invoked)
    print_info("Tầng AI", res.generation_tier)
    print_info("Lời động viên chuẩn Guilt-free", res.empathetic_note)

    passed = (models_invoked == ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]) and (res.generation_tier == "tier_2_groq") and ("nhẹ nhàng" in res.empathetic_note)

    winner = mark_winner(
        models_trace, models_invoked[-1] if res.generation_tier == "tier_2_groq" and models_invoked else ""
    )
    rec = ScenarioTraceRecord(
        scenario_id="scenario_5_groq_429",
        scenario_name="Chống Nghẽn Quota (Groq Qwen 429 TPM -> Groq GPT-OSS)",
        description="Khi model chính trên Groq chạm ngưỡng 429 TPM, hệ thống tự động gọi model phụ thay vì báo lỗi",
        start_time=t_start_iso,
        end_time=t_end_iso,
        total_duration_s=elapsed_s,
        models_trace=models_trace,
        decision_timestamp=t_end_iso,
        decision_relative_s=elapsed_s,
        winner_model=winner,
        winner_tier=res.generation_tier,
        decision_rationale="qwen/qwen3.8-27b chạm trần HTTP 429; hệ thống tự động fallback sang openai/gpt-oss-20b và nhận output thành công",
        final_output={"empathetic_note": res.empathetic_note, "encouragement_tip": res.encouragement_tip},
        passed=passed,
    )
    global_tracer.add_record(rec)

    return passed


# ==============================================================================
# KỊCH BẢN 6: SẬP TOÀN BỘ CLOUD AI -> THUẦN PYTHON BỌC LÓT & PYTHON GUARDRAIL
# ==============================================================================
async def scenario_6_pure_python_fallback_and_guardrail() -> bool:
    print_header("KỊCH BẢN 6: Sập Toàn Bộ Cloud AI -> Thuần Python Bọc Lót & Anti-Hallucination")
    print_step("Provider giả lập treo ở cả Khung A và B, rồi mất 0.5s để dọn dẹp sau khi bị hủy.")
    print_step("Kiểm tra riêng việc UF1 phát hiện deadline quá khứ trong kết quả của AI.")

    t_start_iso = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    models_trace: list[ModelTraceEntry] = []

    cfg = Settings(
        _env_file=None, GEMINI_API_KEY="", GROQ_API_KEY="", GOOGLE_CREDENTIALS_PATH=""
    )
    service_offline = AIService(cfg)

    async def _stall_gemini(*, model_name: str, user_prompt: str, **_: Any) -> None:
        flow = "UF1" if "user_raw_input" in json.loads(user_prompt) else "UF2"
        await stall_provider(models_trace, t0, f"{flow} / {model_name}", "tier_1_gemini", cleanup_delay=0.5)

    async def _stall_groq(*, user_prompt: str, **_: Any) -> None:
        flow = "UF1" if "user_raw_input" in json.loads(user_prompt) else "UF2"
        await stall_provider(models_trace, t0, f"{flow} / {cfg.GROQ_FALLBACK_MODEL}", "tier_2_groq", cleanup_delay=0.5)

    async def _run_uf1() -> tuple[TaskExtractionResult, float]:
        started = time.perf_counter()
        result = await service_offline.extract_task_intent("Làm báo cáo 5 trang")
        return result, time.perf_counter() - started

    async def _run_uf2() -> tuple[TaskDecompositionResult, float]:
        started = time.perf_counter()
        result = await service_offline.decompose_task(
            task_title="Dọn dẹp phòng làm việc", user_focus_duration=20
        )
        return result, time.perf_counter() - started

    with (
        patch.object(service_offline, "_get_gemini_client", return_value=object()),
        patch.object(service_offline, "_get_groq_client", return_value=object()),
        patch.object(service_offline, "_call_gemini_structured", side_effect=_stall_gemini),
        patch.object(service_offline, "_call_groq_structured", side_effect=_stall_groq),
    ):
        (uf1_res, uf1_elapsed), (uf2_res, uf2_elapsed) = await asyncio.gather(_run_uf1(), _run_uf2())

    # 2. Kiểm thử Python Guardrail đè ảo giác deadline quá khứ
    service_guardrail = AIService(cfg)
    ref_now = datetime(2026, 11, 9, 14, 0, 0)
    past_deadline = datetime(2026, 11, 8, 23, 59, 0)
    hallucinated_payload = LLMTaskExtractionPayload(
        title="Báo cáo tiến độ",
        content="Báo cáo đồ án",
        scope="5 trang",
        free_time_slots=["Hôm qua"],
        deadline=past_deadline,
        is_complete=True,  # LLM BỊ ẢO GIÁC!
        has_conflict=False,
        summary="Đã xong.",
        clarification_questions=[],
    )

    with (
        patch.object(service_guardrail, "_get_gemini_client", return_value=object()),
        patch.object(service_guardrail, "_call_gemini_structured", return_value=hallucinated_payload),
    ):
        guard_res = await service_guardrail.extract_task_intent("Làm báo cáo 5 trang", reference_now=ref_now)

    t_end = time.perf_counter()
    elapsed_s = t_end - t0
    t_end_iso = datetime.now(UTC).isoformat()

    print_info("UF1 trả kết quả sau", f"{uf1_elapsed:.3f}s")
    print_info("UF2 trả kết quả sau", f"{uf2_elapsed:.3f}s")
    print_info("Tầng sinh UF2", uf2_res.generation_tier)
    print_info("Số bước Python tự sinh", len(uf2_res.steps))
    print_info("Kết quả chống ảo giác", f"is_complete={guard_res.is_complete} (Đã bị Python lật cờ)")

    passed = (
        uf1_res.generation_tier == uf2_res.generation_tier == "tier_3_rule_based"
        and at_milestone(uf1_elapsed, cfg.AI_TIMEOUT_SECONDS)
        and at_milestone(uf2_elapsed, cfg.AI_UF2_TIMEOUT_SECONDS)
        and len(models_trace) == 6
        and all(trace.status == "CANCELLED" for trace in models_trace)
        and len(uf2_res.steps) == 3
        and all(
            step.estimated_minutes == 20 and step.expected_output and step.micro_task_prompt
            for step in uf2_res.steps
        )
        and (not guard_res.is_complete)
        and guard_res.has_conflict
        and len(guard_res.clarification_questions) == 1
    )

    rec = ScenarioTraceRecord(
        scenario_id="scenario_6_python_tier3_and_guardrail",
        scenario_name="Sập Toàn Bộ Cloud -> Thuần Python Bọc Lót & Anti-Hallucination",
        description="Khi Cloud sập, Python tự sinh 3 bước chuẩn; khi LLM ảo giác deadline quá khứ, Python Guardrail can thiệp lật cờ is_complete=False",
        start_time=t_start_iso,
        end_time=t_end_iso,
        total_duration_s=elapsed_s,
        models_trace=models_trace,
        decision_timestamp=t_end_iso,
        decision_relative_s=elapsed_s,
        winner_model="pure_python_rule_based" if uf2_res.generation_tier == "tier_3_rule_based" else "",
        winner_tier=uf2_res.generation_tier,
        decision_rationale="Kỳ vọng trả dự phòng tại trần A/B trong khi các provider vẫn đang dọn dẹp.",
        final_output={
            "uf1_elapsed_s": uf1_elapsed,
            "uf1_tier": uf1_res.generation_tier,
            "uf2_elapsed_s": uf2_elapsed,
            "uf2_steps": [s.model_dump(mode="json") for s in uf2_res.steps],
            "guardrail_is_complete": guard_res.is_complete,
            "guardrail_has_conflict": guard_res.has_conflict,
            "guardrail_questions": guard_res.clarification_questions,
        },
        passed=passed,
    )
    global_tracer.add_record(rec)

    return passed


# ==============================================================================
# BỘ CHẠY CHÍNH VÀ XUẤT FILE LOG VÀO POC/logs/
# ==============================================================================
async def async_main() -> int:
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.WHITE} PROPLAN VA — KIỂM THỬ THỰC NGHIỆM AI FALLBACK 3 TẦNG (THANG ĐO GIÂY THỰC TẾ) {Colors.RESET}\n")
    print(f"{Colors.DIM}Thời gian chạy: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Provider giả lập, cấu hình chuẩn Khung A/B")
    print(f"Thư mục lưu trữ logs: {LOGS_DIR}{Colors.RESET}\n")

    scenarios = [
        ("Kịch bản 1: Fast-fail Gemini sang Groq trước hedge", scenario_1_fast_fail_gemini_to_groq),
        ("Kịch bản 2: Ưu tiên Gemini Primary trong 4.0s", scenario_2_quality_first_priority_window),
        ("Kịch bản 3: Trả Flash-Lite tại 7.5s", scenario_3_giant_task_warm_backup_ram_harvest),
        ("Kịch bản 4: Khởi chạy Groq tại 3.5s, trả kết quả tại 4.0s", scenario_4_total_gemini_timeout_to_groq),
        ("Kịch bản 5: Groq 429 Rate-Limit sang Groq Secondary", scenario_5_groq_primary_429_to_secondary),
        ("Kịch bản 6: Sập Cloud sang Thuần Python & Chống ảo giác", scenario_6_pure_python_fallback_and_guardrail),
    ]

    global_tracer.records.clear()
    t_start = time.perf_counter()

    for name, func in scenarios:
        scenario_started = datetime.now(UTC)
        scenario_start = time.perf_counter()
        try:
            if await func():
                print_success(f"{name} — ĐẠT")
            else:
                print_error(f"{name} — THẤT BẠI")
        except Exception as exc:
            global_tracer.add_record(
                ScenarioTraceRecord(
                    scenario_id=func.__name__,
                    scenario_name=name,
                    description="Kịch bản kết thúc với lỗi",
                    start_time=scenario_started.isoformat(),
                    end_time=datetime.now(UTC).isoformat(),
                    total_duration_s=time.perf_counter() - scenario_start,
                    decision_rationale=f"{type(exc).__name__}: {exc}",
                )
            )
            print_error(f"Kịch bản '{name}' THẤT BẠI: {exc}")
            import traceback
            traceback.print_exc()

    total_time = time.perf_counter() - t_start
    passed_count = sum(record.passed for record in global_tracer.records)

    # Xuất các file log
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = LOGS_DIR / "ai_fallback_trace.json"
    text_log_path = LOGS_DIR / "ai_fallback_trace.log"
    md_summary_path = LOGS_DIR / "ai_fallback_summary.md"

    global_tracer.export_json(json_path)
    global_tracer.export_text_log(text_log_path)
    global_tracer.export_markdown_summary(md_summary_path)

    # Bảng tổng kết ra màn hình
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.WHITE}TỔNG KẾT KỊCH BẢN MÔ PHỎNG{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")
    print(f"  • Tổng số kịch bản kiểm thử: {len(scenarios)}")
    print(f"  • Số kịch bản ĐẠT: {Colors.BOLD}{Colors.GREEN}{passed_count}/{len(scenarios)}{Colors.RESET}")
    print(f"  • Tổng thời gian thực thi:   {Colors.BOLD}{total_time:.3f} giây{Colors.RESET}")
    print(f"\n{Colors.BOLD}{Colors.YELLOW}[ĐÃ CẬP NHẬT TRACE LOGS VÀO THƯ MỤC POC/logs/]:{Colors.RESET}")
    print(f"  1. File Log chi tiết timeline: {Colors.CYAN}{text_log_path}{Colors.RESET}")
    print(f"  2. File JSON cấu trúc đầy đủ : {Colors.CYAN}{json_path}{Colors.RESET}")
    print(f"  3. Báo cáo Markdown tổng hợp : {Colors.CYAN}{md_summary_path}{Colors.RESET}")

    if passed_count == len(scenarios):
        print(f"\n{Colors.BOLD}{Colors.BG_GREEN}{Colors.WHITE} CẢ 6 KỊCH BẢN MÔ PHỎNG ĐỀU ĐẠT {Colors.RESET}\n")
        return 0
    else:
        print(f"\n{Colors.BOLD}{Colors.BG_RED}{Colors.WHITE} [CẢNH BÁO] Có kịch bản không đạt yêu cầu kỹ thuật! {Colors.RESET}\n")
        return 1


def main() -> None:
    code = asyncio.run(async_main())
    sys.exit(code)


# Hỗ trợ Pytest
def test_poc_scenario_1() -> None:
    assert asyncio.run(scenario_1_fast_fail_gemini_to_groq())

def test_poc_scenario_2() -> None:
    assert asyncio.run(scenario_2_quality_first_priority_window())

def test_poc_scenario_3() -> None:
    assert asyncio.run(scenario_3_giant_task_warm_backup_ram_harvest())

def test_poc_scenario_4() -> None:
    assert asyncio.run(scenario_4_total_gemini_timeout_to_groq())

def test_poc_scenario_5() -> None:
    assert asyncio.run(scenario_5_groq_primary_429_to_secondary())

def test_poc_scenario_6() -> None:
    assert asyncio.run(scenario_6_pure_python_fallback_and_guardrail())


if __name__ == "__main__":
    main()
