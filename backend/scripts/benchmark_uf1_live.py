"""Đo UF1 với API thật; chạy thủ công, không thuộc unit test/CI.

Chạy từ backend: .venv/Scripts/python.exe scripts/benchmark_uf1_live.py
Không ghi DB/Calendar; chỉ dùng tình huống giả lập, prompt/schema/service thật.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import io
import json
import logging
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.ai_service import AIService  # noqa: E402

REFERENCE_NOW = datetime.fromisoformat("2026-11-11T09:00:00+07:00")
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2] / "docs/backend/qa_results/uf1_live_latency_20261111.json"
)
CASES = {
    "small": "Sắp xếp 20 tài liệu PDF vào 3 thư mục môn học và đổi tên theo môn-ngày. "
    "Bắt đầu ngày 12/11/2026 lúc 19:00, rảnh 19:00-20:00 ngày đó. "
    "Hạn chót 12/11/2026 lúc 21:00, giờ Việt Nam.",
    "medium": "Viết báo cáo 8 trang về khảo sát thói quen học tập của 30 sinh viên, gồm "
    "mục tiêu, phương pháp, kết quả, 3 biểu đồ và kết luận; nộp một file PDF. "
    "Dữ liệu đã có trong bảng tính, không cần khảo sát thêm. Bắt đầu 12/11/2026 lúc 19:00. "
    "Rảnh ngày 12, 13 và 14/11/2026, mỗi ngày từ 19:00 đến 21:00. "
    "Hạn chót 15/11/2026 lúc 17:00 giờ Việt Nam. Chỉ dùng dữ liệu có sẵn, "
    "không đưa thông tin cá nhân vào báo cáo.",
    "large": "Hoàn thành nguyên mẫu ứng dụng quản lý thư viện cho đồ án nhóm 4 người. "
    "Phạm vi gồm đăng nhập, phân quyền thủ thư và độc giả, quản lý 500 đầu sách, "
    "tìm kiếm theo tên/tác giả, mượn trả, theo dõi quá hạn và báo cáo thống kê tháng. "
    "Đầu ra là mã nguồn backend FastAPI, frontend web, database PostgreSQL, tài liệu "
    "API, 30 ca kiểm thử chức năng, báo cáo PDF 25 trang, 15 slide và video demo 5 phút. "
    "Không triển khai thanh toán, không gửi email thật và không triển khai lên cloud. "
    "Thiết kế giao diện đã có; cần rà soát tính nhất quán giữa API và màn hình, "
    "dữ liệu demo không chứa thông tin người thật. Tôi phụ trách tích hợp và báo cáo; "
    "các thành viên khác đã cung cấp module đăng nhập và danh mục sách. "
    "Bắt đầu 12/11/2026 lúc 18:00, hạn chót 30/11/2026 lúc 17:00 giờ Việt Nam. "
    "Tôi rảnh thứ Hai, thứ Tư và thứ Sáu từ 18:00 đến 21:00, thứ Bảy từ 09:00 đến 12:00 "
    "và từ 14:00 đến 17:00 trong khoảng ngày 12 đến 29/11/2026. "
    "Giữ nguyên các lịch học đã có, không tự đổi lịch công việc khác. "
    "Ưu tiên demo chạy ổn định, sau đó kiểm thử, cuối cùng hoàn thiện tài liệu; "
    "chỉ ghi nhận yêu cầu ở bước này, chưa chia bước hoặc chốt lịch.",
    "missing_fields": "Mình muốn viết báo cáo 2 trang gồm kết quả và kết luận.",
}
MODES = ("primary", "backup", "groq", "groq_secondary", "production")


def error_info(exc):
    # Không xuất repr client, request headers hay toàn bộ response chứa credential.
    message = str(exc)
    for secret in (settings.GEMINI_API_KEY, settings.GROQ_API_KEY):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"org_[A-Za-z0-9]+", "[REDACTED_ORG]", message)
    return {
        "error_type": type(exc).__name__,
        "http_status": getattr(exc, "status_code", None) or getattr(exc, "code", None),
        "error_message": message[:800],
    }


class MeasuredService(AIService):
    def __init__(self, mode):
        overrides = {}
        if mode in ("primary", "backup"):
            overrides = {
                "GEMINI_FLASH_MODEL": settings.GEMINI_FLASH_MODEL
                if mode == "primary"
                else settings.GEMINI_BACKUP_MODEL,
                "GEMINI_BACKUP_MODEL": "",
                "GROQ_API_KEY": "",
            }
        elif mode in ("groq", "groq_secondary"):
            overrides = {
                "GEMINI_API_KEY": "",
                "GOOGLE_CREDENTIALS_PATH": "",
                "GROQ_FALLBACK_MODEL": settings.GROQ_FALLBACK_MODEL
                if mode == "groq"
                else settings.GROQ_SECONDARY_FALLBACK_MODEL,
                "GROQ_SECONDARY_FALLBACK_MODEL": "",
            }
        super().__init__(settings.model_copy(update=overrides))
        self.mode = mode
        self.events: list[dict[str, Any]] = []
        self.result_models: dict[int, str] = {}
        self.coordinator_error: dict[str, Any] | None = None
        self.winner: str | None = None
        self.started = 0.0
        # Dùng lại kết nối giữa các lượt; tách chi phí khởi tạo client khỏi latency.
        self._get_gemini_client()
        client = self._get_groq_client()
        if client:
            original = client.chat.completions.create

            async def measured_create(**kwargs):
                return await self.measure(kwargs["model"], "http_response", original(**kwargs))

            cast(Any, client.chat.completions).create = measured_create

    async def measure(self, model, success_status, call):
        start = time.perf_counter()
        event = {"model": model, "start_s": round(start - self.started, 4)}
        self.events.append(event)
        try:
            result = await call
            event["status"] = success_status
            self.result_models[id(result)] = model
            return result
        except asyncio.CancelledError:
            event["status"] = "cancelled"
            raise
        except Exception as exc:
            event.update(status="error", **error_info(exc))
            raise
        finally:
            event["elapsed_s"] = round(time.perf_counter() - start, 4)

    async def _call_gemini_structured(self, **kwargs):
        if self.mode == "backup":
            kwargs["thinking_level"] = types.ThinkingLevel.MINIMAL
        return await self.measure(
            kwargs["model_name"], "schema_valid", super()._call_gemini_structured(**kwargs)
        )

    async def _call_groq_structured(self, **kwargs):
        try:
            result = await super()._call_groq_structured(**kwargs)
        except Exception as exc:
            if self.events and self.events[-1].get("status") == "http_response":
                self.events[-1].update(status="schema_error", **error_info(exc))
            raise
        event = next(e for e in reversed(self.events) if e.get("status") == "http_response")
        event["status"] = "schema_valid"
        self.result_models[id(result)] = event["model"]
        return result

    async def _execute_hedged_structured(self, **kwargs):
        try:
            result, tier = await super()._execute_hedged_structured(**kwargs)
            self.winner = self.result_models.get(id(result))
            return result, tier
        except Exception as exc:
            self.coordinator_error = error_info(exc)
            raise

    async def run_case(self, case, repetition):
        self.events = []
        self.result_models = {}
        self.coordinator_error = cast("dict[str, Any] | None", None)
        self.winner = cast("str | None", None)
        self.started = time.perf_counter()
        result = await self.extract_task_intent(CASES[case], reference_now=REFERENCE_NOW)
        elapsed = time.perf_counter() - self.started
        if self._cancelling_provider_tasks:
            await asyncio.gather(*list(self._cancelling_provider_tasks), return_exceptions=True)
        status = (
            "success"
            if self.winner
            else (
                "timeout"
                if self.coordinator_error and self.coordinator_error["error_type"] == "TimeoutError"
                else "error"
            )
        )
        checks = {
            "content_present": bool(result.content),
            "scope_present": bool(result.scope),
            "is_complete": result.is_complete,
            "has_conflict": result.has_conflict,
        }
        if case == "missing_fields":
            checks["missing_fields_not_invented"] = (
                result.deadline is None
                and result.time_constraint is None
                and not result.free_time_slots
                and not result.structured_free_windows
                and not result.is_complete
                and bool(result.clarification_questions)
            )
        return {
            "mode": self.mode,
            "case": case,
            "repetition": repetition,
            "elapsed_s": round(elapsed, 4),
            "status": status,
            "winner": self.winner,
            "generation_tier": result.generation_tier,
            "coordinator_error": self.coordinator_error,
            "attempts": self.events,
            "checks": checks,
            "result": result.model_dump(mode="json"),
        }

    async def close(self):
        if self._gemini_client:
            await self._gemini_client.aio.aclose()
            self._gemini_client.close()
        if self._groq_client:
            await self._groq_client.close()


def evaluate_time_fields(row):
    """Kiểm đúng start/deadline đã nêu; không dùng is_complete làm bằng chứng can_plan."""
    expected = {
        "small": ("2026-11-12T19:00:00+07:00", "2026-11-12T21:00:00+07:00"),
        "medium": ("2026-11-12T19:00:00+07:00", "2026-11-15T17:00:00+07:00"),
        "large": ("2026-11-12T18:00:00+07:00", "2026-11-30T17:00:00+07:00"),
    }
    if row["case"] not in expected or row["status"] != "success":
        return
    constraint = row["result"].get("time_constraint") or {}
    row["checks"].pop("deadline_at_preserved", None)
    actual_values = (
        constraint.get("start_at"),
        constraint.get("deadline_at") or constraint.get("finish_by_at"),
    )
    for field, actual, target in zip(
        ("start_at", "time_constraint_end"), actual_values, expected[row["case"]], strict=True
    ):
        row["checks"][f"{field}_preserved"] = actual is not None and datetime.fromisoformat(
            actual
        ) == datetime.fromisoformat(target)


def write_report(path, report):
    for row in report["runs"]:
        evaluate_time_fields(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Đo UF1 với API thật",
        "",
        "Gọi `extract_task_intent` với prompt/schema/Python guardrail hiện tại. Không mock provider.",
        "Đo từ đầu service đến kết quả; không gồm STT, HTTP endpoint, DB hoặc Calendar.",
        "Mỗi model riêng có cùng trần UF1; production giữ nguyên các mốc hedge.",
        "Client tạo trước lượt đo, kết nối đầu tiên vẫn có thể cold. Các lượt chạy tuần tự, không retry ngoài service.",
        "Timeout là chưa có kết quả hợp lệ trước trần, không phải thời gian model hoàn thành.",
        "Cancelled trong production có thể do đã có winner, không đồng nghĩa model timeout.",
        "Thành công chỉ nghĩa là có kết quả đúng schema; không chứng minh can_plan hay đầy đủ nghiệp vụ.",
        "Trung vị/min/max bên dưới tính trên tất cả outcome (kể cả lỗi/timeout), không phải latency riêng của phản hồi thành công.",
        "",
        "| Chế độ | Task | n | Thành công | Timeout | Lỗi | Trung vị (s) | Min–max (s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        for case in CASES:
            rows = [r for r in report["runs"] if r["mode"] == mode and r["case"] == case]
            if not rows:
                continue
            times = [r["elapsed_s"] for r in rows]
            counts = [sum(r["status"] == s for r in rows) for s in ("success", "timeout", "error")]
            lines.append(
                f"| {mode} | {case} | {len(rows)} | {counts[0]} | {counts[1]} | {counts[2]} | {statistics.median(times):.3f} | {min(times):.3f}–{max(times):.3f} |"
            )
    lines += [
        "",
        "Mẫu nhỏ, không suy ra p95/p99 hoặc timeout tối ưu từ bài đo này.",
        "",
        "## Đầu vào",
        "",
    ]
    lines += [f"- **{key}** ({len(value)} ký tự): {value}" for key, value in CASES.items()]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    parser.add_argument("--pause-seconds", type=float, default=0.5)
    parser.add_argument("--cooldown-seconds", type=float, default=0)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    if args.pause_seconds < 0 or args.cooldown_seconds < 0:
        parser.error("Thời gian nghỉ không được âm")
    if not settings.has_gemini_auth or not settings.has_groq_key:
        parser.error("Cần cấu hình cả Gemini và Groq để đo đủ bốn model.")
    logging.disable(logging.CRITICAL)
    report = {
        "started_at": datetime.now().astimezone().isoformat(),
        "reference_now": REFERENCE_NOW.isoformat(),
        "sdk_versions": {
            package: importlib.metadata.version(package)
            for package in ("google-genai", "groq", "pydantic")
        },
        "cases": CASES,
        "run_options": {
            "repetitions": args.repetitions,
            "modes": args.modes,
            "cases": args.cases,
            "pause_seconds": args.pause_seconds,
            "cooldown_seconds": args.cooldown_seconds,
        },
        "config": {
            k: getattr(settings, k)
            for k in (
                "GEMINI_FLASH_MODEL",
                "GEMINI_BACKUP_MODEL",
                "GROQ_FALLBACK_MODEL",
                "GROQ_SECONDARY_FALLBACK_MODEL",
                "AI_TIMEOUT_SECONDS",
                "AI_PRIMARY_PRIORITY_SECONDS",
                "AI_WARM_BACKUP_DELAY_SECONDS",
                "AI_GROQ_HEDGE_DELAY_SECONDS",
            )
        },
        "runs": [],
    }
    services = {mode: MeasuredService(mode) for mode in args.modes}
    try:
        remaining = args.cooldown_seconds
        while remaining > 0:
            print(f"Cooldown: {remaining:.0f}s", flush=True)
            delay = min(30, remaining)
            await asyncio.sleep(delay)
            remaining -= delay
        for repetition in range(1, args.repetitions + 1):
            for case in args.cases:
                if case == "missing_fields" and repetition > 1:
                    continue
                # Xoay thứ tự nhằm giảm thiên lệch do model luôn được chạy trước.
                offset = (repetition - 1) % len(args.modes)
                modes = args.modes[offset:] + args.modes[:offset]
                for mode in modes:
                    row = await services[mode].run_case(case, repetition)
                    report["runs"].append(row)
                    write_report(args.output, report)
                    print(
                        f"{repetition} {case:14} {mode:14} {row['status']:8} {row['elapsed_s']:.3f}s winner={row['winner']}",
                        flush=True,
                    )
                    remaining = args.pause_seconds
                    while remaining > 0:
                        delay = min(30, remaining)
                        await asyncio.sleep(delay)
                        remaining -= delay
    finally:
        await asyncio.gather(*(service.close() for service in services.values()))
        report["finished_at"] = datetime.now().astimezone().isoformat()
        write_report(args.output, report)


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
