#!/usr/bin/env python3
"""
================================================================================
PROPLAN VA — KIỂM THỬ GỌI AI THẬT TRỰC TIẾP LÊN GOOGLE AI STUDIO & GROQ
(Live Real API Call & Real Fallback Verification)
================================================================================
Mục đích:
  - Bắn request HTTP thật 100% đến Google AI Studio qua GEMINI_API_KEY.
  - Bạn có thể mở dashboard của Google AI Studio (https://aistudio.google.com/)
    để thấy request xuất hiện trực tiếp trong phần Analytics / API Metrics!
  - Kiểm thử Fallback THẬT: Truyền key Google sai để xem hệ thống nhận lỗi thật
    từ Google server, rồi tự động nhảy sang Groq Cloud thật và thành công!
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Bắt buộc mã hóa UTF-8 cho Windows Terminal
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

from app.core.config import Settings, settings
from app.services.ai_service import AIService


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


def print_header(title: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.WHITE}>> {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")


async def live_test_1_direct_gemini_call(live_logs: list[dict[str, Any]]) -> bool:
    print_header("LIVE TEST 1: PHÂN RÃ UF2 QUA TẦNG GEMINI")
    print(f"  {Colors.BOLD}{Colors.BLUE}[GỌI API]{Colors.RESET} Đang chạy bộ điều phối AI với cấu hình hiện hành...")
    print(f"  {Colors.DIM}• Model mục tiêu: {settings.GEMINI_FLASH_MODEL}")
    print(f"  • Prompt: Bẻ nhỏ công việc 'Làm slide thuyết trình đồ án tốt nghiệp'{Colors.RESET}")

    service = AIService()
    t0 = time.perf_counter()
    start_time_iso = datetime.now().isoformat()

    try:
        result = await service.decompose_task(
            task_title="Làm slide thuyết trình đồ án tốt nghiệp",
            scope="12 slide tổng quan kiến trúc và kết quả thực nghiệm",
            user_focus_duration=25,
        )
        elapsed_sec = time.perf_counter() - t0
        end_time_iso = datetime.now().isoformat()

        passed = result.generation_tier == "tier_1_gemini"
        print(f"      • Tầng phục vụ      : {Colors.BOLD}{result.generation_tier}{Colors.RESET}")
        print(f"      • Thời gian xử lý   : {Colors.BOLD}{elapsed_sec:.2f} giây{Colors.RESET}")
        print(f"      • Lý do phân rã     : {result.reasoning}")
        print(f"      • Số bước AI đã chia: {len(result.steps)} bước")
        for step in result.steps:
            print(f"        {Colors.CYAN}Bước {step.order_index}:{Colors.RESET} {step.title} ({step.estimated_minutes}p)")
            print(f"          -> Micro-task: {step.micro_task_prompt}")

        live_logs.append({
            "test_name": "LIVE TEST 1: UF2 Gemini Tier",
            "model_requested": settings.GEMINI_FLASH_MODEL,
            "expected_tier": "tier_1_gemini",
            "start_time": start_time_iso,
            "end_time": end_time_iso,
            "elapsed_seconds": round(elapsed_sec, 2),
            "status": "SUCCESS" if passed else "FAILED",
            "generation_tier": result.generation_tier,
            "output_summary": {
                "steps_count": len(result.steps),
                "reasoning": result.reasoning,
                "first_step": result.steps[0].title if result.steps else None,
            },
        })
        return passed
    except Exception as exc:
        live_logs.append({
            "test_name": "LIVE TEST 1: UF2 Gemini Tier",
            "status": "FAILED",
            "error_type": type(exc).__name__,
        })
        return False


async def live_test_2_real_fallback_gemini_to_groq(live_logs: list[dict[str, Any]]) -> bool:
    print_header("LIVE TEST 2: FALLBACK THẬT (GOOGLE LỖI KEY -> NHẢY SANG GROQ THẬT)")
    print(f"  {Colors.BOLD}{Colors.YELLOW}[THỬ NGHIỆM]{Colors.RESET} Cố tình cấp Key Gemini SAI để Google Server từ chối thật (HTTP 400/403).")
    print(f"  {Colors.DIM}• Sau khi Google từ chối, hệ thống PHẢI tự động bắn sang Groq Cloud thật bằng GROQ_API_KEY chuẩn.{Colors.RESET}")

    # Tạo settings với Gemini key rởm nhưng Groq key thật
    bad_gemini_settings = settings.model_copy(update={
        "GEMINI_API_KEY": "AIzaSy_Invalid_Key_For_Testing_Fallback_403",
        "GOOGLE_CREDENTIALS_PATH": "",
    })
    fallback_service = AIService(app_settings=bad_gemini_settings)

    t0 = time.perf_counter()
    start_time_iso = datetime.now().isoformat()

    try:
        result = await fallback_service.extract_task_intent(
            "Viết tiểu luận môn An toàn thông tin 8 trang nộp vào tối Chủ Nhật tuần này"
        )
        elapsed_sec = time.perf_counter() - t0
        end_time_iso = datetime.now().isoformat()

        passed = result.generation_tier == "tier_2_groq"
        print(f"      • Tầng phục vụ      : {Colors.BOLD}{result.generation_tier}{Colors.RESET}")
        print(f"      • Thời gian chuyển  : {Colors.BOLD}{elapsed_sec:.2f} giây{Colors.RESET}")
        print(f"      • Tiêu đề trích xuất: {result.title}")
        print(f"      • Phạm vi (Scope)   : {result.scope}")
        print(f"      • Hạn chót          : {result.deadline}")
        print(f"      • Tóm tắt           : {result.summary}")

        live_logs.append({
            "test_name": "LIVE TEST 2: Real Fallback (Google Error -> Real Groq Call)",
            "primary_model_configured": settings.GEMINI_FLASH_MODEL,
            "groq_models_configured": [settings.GROQ_FALLBACK_MODEL, settings.GROQ_SECONDARY_FALLBACK_MODEL],
            "expected_tier": "tier_2_groq",
            "start_time": start_time_iso,
            "end_time": end_time_iso,
            "elapsed_seconds": round(elapsed_sec, 2),
            "status": "SUCCESS" if passed else "FAILED",
            "generation_tier": result.generation_tier,
            "output_summary": {
                "title": result.title,
                "scope": result.scope,
                "deadline": str(result.deadline),
                "summary": result.summary,
            },
        })
        return passed
    except Exception as exc:
        live_logs.append({
            "test_name": "LIVE TEST 2: Real Fallback (Google Error -> Real Groq Call)",
            "status": "FAILED",
            "error_type": type(exc).__name__,
        })
        return False


async def live_test_3_real_fallback_to_pure_python(live_logs: list[dict[str, Any]]) -> bool:
    print_header("LIVE TEST 3: CẢ 2 HÃNG AI KHÔNG KHẢ DỤNG -> THUẦN PYTHON BỌC LÓT")
    print(f"  {Colors.BOLD}{Colors.YELLOW}[THỬ NGHIỆM]{Colors.RESET} Chạy với cấu hình thiếu key của cả Google lẫn Groq.")
    print(f"  {Colors.DIM}• Thuật toán Python phải tự động tiếp quản: không crash, không màn hình trắng.{Colors.RESET}")

    no_ai_settings = Settings(
        _env_file=None,
        GEMINI_API_KEY="",
        GROQ_API_KEY="",
        GOOGLE_CREDENTIALS_PATH="",
    )
    python_service = AIService(app_settings=no_ai_settings)

    t0 = time.perf_counter()
    start_time_iso = datetime.now().isoformat()

    result = await python_service.decompose_task(
        task_title="Ôn tập cấu trúc dữ liệu và giải thuật",
        user_focus_duration=25,
    )
    elapsed_sec = time.perf_counter() - t0
    end_time_iso = datetime.now().isoformat()

    passed = result.generation_tier == "tier_3_rule_based" and bool(result.steps)
    first_step = result.steps[0] if result.steps else None
    print(f"      • Tầng phục vụ      : {Colors.BOLD}{result.generation_tier}{Colors.RESET}")
    print(f"      • Thời gian xử lý   : {Colors.BOLD}{elapsed_sec * 1000:.2f} ms{Colors.RESET}")
    print(f"      • Số bước tự sinh   : {len(result.steps)}")
    for s in result.steps:
        print(f"        {Colors.CYAN}Bước {s.order_index}:{Colors.RESET} {s.title}")
        print(f"          -> Micro-task: {s.micro_task_prompt}")

    live_logs.append({
        "test_name": "LIVE TEST 3: Pure Python Rule-based Fallback",
        "primary_model_attempted": "none",
        "provider": "Pure Python Local Engine",
        "start_time": start_time_iso,
        "end_time": end_time_iso,
        "elapsed_seconds": round(elapsed_sec, 4),
        "status": "SUCCESS" if passed else "FAILED",
        "expected_tier": "tier_3_rule_based",
        "generation_tier": result.generation_tier,
        "output_summary": {
            "steps_count": len(result.steps),
            "first_step": first_step.title if first_step else None,
            "micro_task": first_step.micro_task_prompt if first_step else None,
        },
    })
    return passed


async def main() -> int:
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.WHITE} PROPLAN VA — KIỂM THỬ THỰC TẾ API THẬT TRÊN GOOGLE AI STUDIO & GROQ CLOUD {Colors.RESET}\n")
    print(f"  • Môi trường: {settings.ENVIRONMENT}")
    print(f"  • Có Key Gemini thật: {settings.has_gemini_key}")
    print(f"  • Có Key Groq thật  : {settings.has_groq_key}")

    live_logs: list[dict[str, Any]] = []

    # 1. Gọi Gemini thật
    ok1 = await live_test_1_direct_gemini_call(live_logs)

    # 2. Gọi Fallback thật: Google lỗi -> Groq thật
    ok2 = await live_test_2_real_fallback_gemini_to_groq(live_logs)

    # 3. Gọi Fallback thật: Mất cả 2 AI -> Thuần Python thật
    try:
        ok3 = await live_test_3_real_fallback_to_pure_python(live_logs)
    except Exception as exc:
        live_logs.append({
            "test_name": "LIVE TEST 3: Pure Python Rule-based Fallback",
            "status": "FAILED",
            "error_type": type(exc).__name__,
        })
        ok3 = False

    # Xuất log ra file POC/logs/live_ai_trace.json
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = LOGS_DIR / "live_ai_trace.json"
    output_path.write_text(json.dumps(live_logs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.WHITE}TỔNG KẾT KIỂM THỬ API THẬT:{Colors.RESET}")
    print(f"  • Test 1 (tầng Gemini)        : {'✅ ĐẠT' if ok1 else '❌ THẤT BẠI'}")
    print(f"  • Test 2 (Fallback Groq thật)   : {'✅ ĐẠT' if ok2 else '❌ THẤT BẠI'}")
    print(f"  • Test 3 (Thuần Python thật)    : {'✅ ĐẠT' if ok3 else '❌ THẤT BẠI'}")
    print(f"  • File lưu log: {Colors.CYAN}{output_path}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 85}{Colors.RESET}\n")
    return 0 if all((ok1, ok2, ok3)) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
