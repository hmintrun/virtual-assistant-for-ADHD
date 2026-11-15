"""Đo cùng bộ UF1, chờ model trả xong; chỉ script này bỏ deadline theo yêu cầu user.

Không sửa timing production. SDK không giới hạn timeout; server vẫn có thể từ chối.
Chạy: .venv/Scripts/python.exe scripts/benchmark_uf1_unbounded.py
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import io
import json
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from benchmark_uf1_live import CASES, REFERENCE_NOW, MeasuredService, evaluate_time_fields
from google.genai import types

from app.core.config import settings

OUTPUT = Path(__file__).resolve().parents[2] / "docs/backend/qa_results/uf1_unbounded_20261111.json"
MODES = ("primary", "backup", "groq", "groq_secondary")


class UnboundedService(MeasuredService):
    def __init__(self, mode):
        super().__init__(mode)
        if self._gemini_client:
            original = self._gemini_client.aio.models.generate_content

            async def generate(**kwargs):
                # SDK cài trên máy: timeout=0 -> HTTP request timeout=None,
                # không gửi X-Server-Timeout; không thay config suy luận/schema.
                kwargs["config"].http_options = types.HttpOptions(timeout=0)
                return await self.capture_api(original(**kwargs), "gemini")

            cast(Any, self._gemini_client.aio.models).generate_content = generate
        if self._groq_client:
            original_groq = self._groq_client.chat.completions.create

            async def complete(**kwargs):
                kwargs["timeout"] = None
                return await self.capture_api(original_groq(**kwargs), "groq")

            cast(Any, self._groq_client.chat.completions).create = complete

    async def capture_api(self, call, provider):
        started = time.perf_counter()
        try:
            response = await call
        except Exception:
            self.api["response_s"] = round(time.perf_counter() - started, 4)
            self.api["status"] = "error"
            raise
        self.api.update(response_s=round(time.perf_counter() - started, 4), status="response")
        if provider == "gemini":
            self.api["raw_text"] = response.text
            usage = response.usage_metadata
            self.api["served_model"] = response.model_version
        else:
            self.api["raw_text"] = response.choices[0].message.content
            usage = response.usage
            self.api["served_model"] = response.model
        self.api["usage"] = usage.model_dump(mode="json") if usage else None
        return response

    async def _execute_hedged_structured(self, **kwargs):
        # Gọi đúng adapter của từng model, không hedge, không wait_for/deadline.
        from benchmark_uf1_live import error_info

        arguments = {key: kwargs[key] for key in ("system_prompt", "user_prompt", "schema_cls")}
        try:
            if self.mode in ("primary", "backup"):
                result = await self._call_gemini_structured(
                    **arguments,
                    model_name=self._settings.GEMINI_FLASH_MODEL,
                    thinking_level=types.ThinkingLevel.LOW,
                )
                tier = "tier_1_gemini"
            else:
                result = await self._call_groq_structured(**arguments)
                tier = "tier_2_groq"
            self.winner = self.result_models[id(result)]
            return result, tier
        except Exception as exc:
            self.coordinator_error = error_info(exc)
            raise

    async def run_case(self, case, repetition):
        self.api = {}
        row = await super().run_case(case, repetition)
        row["api"] = self.api
        if row["status"] != "success":
            row["status"] = "schema_error" if self.api.get("status") == "response" else "api_error"
        evaluate_time_fields(row)
        return row


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# UF1: thời gian từng model trả về khi bỏ timeout",
        "",
        "Đơn vị: giây. Đo toàn bộ phản hồi API, không phải time-to-first-token. "
        "Tắt deadline điều phối và timeout SDK trong script benchmark; không đổi app production.",
        "Giữ nguyên testcase, context 09:00 ngày 11/11/2026, prompt, schema, thinking và temperature của lượt trước. "
        "Không retry, không đổi model khi lỗi. Server vẫn có thể trả lỗi/giới hạn riêng.",
        "Chạy tuần tự, client tái sử dụng. Hai lượt cùng model Groq cách nhau tối thiểu "
        f"{report['groq_cooldown_s']} giây tính từ khi lượt trước kết thúc; thời gian nghỉ không cộng vào latency.",
        "Chưa đo HTTP endpoint, STT, DB, Calendar hay mobile. JSON lưu raw response, usage và checks; "
        "đúng schema không chứng minh can_plan.",
        "",
        "## Từng lượt",
        "",
        "| Model | Testcase | Lượt | API trả về (s) | Tổng service (s) | Kết quả |",
        "|---|---|---:|---:|---:|---|",
    ]
    for mode in MODES:
        for case in CASES:
            for row in report["runs"]:
                if (row["mode"], row["case"]) != (mode, case):
                    continue
                error = row["coordinator_error"] or {}
                detail = row["status"]
                if error:
                    detail += f" ({error.get('http_status') or error['error_type']})"
                lines.append(
                    f"| {report['models'][mode]} | {case} | {row['repetition']} | "
                    f"{row['api']['response_s']:.3f} | {row['elapsed_s']:.3f} | {detail} |"
                )
    lines += [
        "",
        "## Tổng hợp phản hồi hợp lệ",
        "",
        "Trung vị/min/max chỉ tính phản hồi đúng schema; giữ lỗi riêng trong bảng từng lượt.",
        "",
        "| Model | Testcase | Đúng schema / tổng | Trung vị (s) | Min–max (s) |",
        "|---|---|---:|---:|---:|",
    ]
    for mode in MODES:
        for case in CASES:
            rows = [r for r in report["runs"] if (r["mode"], r["case"]) == (mode, case)]
            if not rows:
                continue
            times = [r["api"]["response_s"] for r in rows if r["status"] == "success"]
            summary = (
                f"{statistics.median(times):.3f} | {min(times):.3f}–{max(times):.3f}"
                if times
                else "— | —"
            )
            lines.append(
                f"| {report['models'][mode]} | {case} | {len(times)}/{len(rows)} | {summary} |"
            )
    lines += ["", "## Testcase nguyên văn", ""]
    lines.extend(f"- **{case}**: {value}" for case, value in CASES.items())
    lines += [
        "",
        "Chạy lại từ `backend`:",
        "",
        "```powershell",
        ".venv/Scripts/python.exe scripts/benchmark_uf1_unbounded.py",
        "```",
        "",
        "Mẫu nhỏ; không suy ra p95/p99 hay timeout tối ưu. Mỗi lần chạy có thể có biến động mạng/provider.",
    ]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--groq-cooldown", type=float, default=65)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.repetitions < 1 or args.groq_cooldown < 0:
        parser.error("Số lần lặp phải dương, cooldown không âm.")
    if not settings.has_gemini_auth or not settings.has_groq_key:
        parser.error("Cần có credential cho cả Gemini và Groq.")
    logging.disable(logging.CRITICAL)
    report = {
        "started_at": datetime.now().astimezone().isoformat(),
        "reference_now": REFERENCE_NOW.isoformat(),
        "cases": CASES,
        "application_timeout_s": None,
        "sdk_timeout_s": None,
        "groq_cooldown_s": args.groq_cooldown,
        "models": dict(
            zip(
                MODES,
                (
                    settings.GEMINI_FLASH_MODEL,
                    settings.GEMINI_BACKUP_MODEL,
                    settings.GROQ_FALLBACK_MODEL,
                    settings.GROQ_SECONDARY_FALLBACK_MODEL,
                ),
                strict=True,
            )
        ),
        "sdk_versions": {
            p: importlib.metadata.version(p) for p in ("google-genai", "groq", "pydantic")
        },
        "runs": [],
    }
    services = {mode: UnboundedService(mode) for mode in MODES}
    ready_at = dict.fromkeys(MODES, 0.0)
    pending = [
        (mode, case, repetition)
        for repetition in range(1, args.repetitions + 1)
        for case in CASES
        if case != "missing_fields" or repetition == 1
        for mode in MODES
    ]
    write_report(args.output, report)
    try:
        while pending:
            ready = next((job for job in pending if ready_at[job[0]] <= time.perf_counter()), None)
            if ready is None:
                delay = min(30, min(ready_at[job[0]] for job in pending) - time.perf_counter())
                print(f"Waiting quota {delay:.1f}s; {len(pending)} calls left", flush=True)
                await asyncio.sleep(max(0, delay))
                continue
            pending.remove(ready)
            mode, case, repetition = ready
            print(f"START {mode} {case} #{repetition}", flush=True)
            row = await services[mode].run_case(case, repetition)
            report["runs"].append(row)
            ready_at[mode] = time.perf_counter() + (
                args.groq_cooldown if mode.startswith("groq") else 0.5
            )
            write_report(args.output, report)
            print(
                f"DONE {mode} {case} #{repetition}: {row['api']['response_s']:.3f}s {row['status']}",
                flush=True,
            )
    finally:
        await asyncio.gather(*(s.close() for s in services.values()))
        report["finished_at"] = datetime.now().astimezone().isoformat()
        report["complete"] = not pending and len(report["runs"]) == 4 * (args.repetitions * 3 + 1)
        write_report(args.output, report)


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
