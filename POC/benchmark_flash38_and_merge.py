"""Chạy bổ sung gemini-3.8-flash cho UF2/UF6 và gộp thống kê với UF1 (3.5-flash-lite).

Không sửa code production hay config.py; override trong đối tượng Settings của phiên đo:
- UF1: giữ kết quả cấu hình hiện hành (gemini-3.5-flash-lite + qwen/qwen3.8-27b).
- UF2_Decompose, UF2_Adjust, UF6: chạy mới `tier_1_gemini_flash_3_8` (unbounded 35s)
  và `production_with_flash_3_8` (Primary 3.8-flash + Warm Backup 3.5-flash-lite + Groq).
Sau đó gộp cùng dữ liệu đã đo trong `all_ufs_live_20261116.json` và sinh bảng + log chi tiết.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
SCRIPTS_DIR = Path(__file__).resolve().parent
for p in (str(BACKEND_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.config import settings
from app.services.ai_service import AIService, ExecutionTimingProfile
from benchmark_all_ufs_live import (
    DEFAULT_SLA_SECONDS,
    REFERENCE_NOW,
    UNBOUNDED_CEILING_SECONDS,
    MeasuredAIService,
    _build_test_cases,
    _evaluate_case,
    _fmt_num,
    _normalize_cases,
    _summarize_group,
    error_info,
)
from google.genai import types
from pydantic import BaseModel

BASE_JSON = ROOT_DIR / "POC/logs/all_ufs_live_20261116.json"
DEFAULT_MERGED_JSON = ROOT_DIR / "POC/logs/all_ufs_live_flash38_merged_20261116.json"
FLASH38_MODEL = "gemini-3.8-flash"


class Flash38MeasuredService(MeasuredAIService):
    """Mở rộng MeasuredAIService để chạy `gemini-3.8-flash` ở UF2/UF6."""

    def __init__(self, mode_label: str, *, use_production_profile: bool) -> None:
        if use_production_profile:
            overrides: dict[str, Any] = {
                "GEMINI_FLASH_MODEL": FLASH38_MODEL,
                "GEMINI_BACKUP_MODEL": settings.GEMINI_BACKUP_MODEL,
                "GEMINI_UF1_MODEL": settings.GEMINI_UF1_MODEL,
            }
        else:
            overrides = {
                "GEMINI_FLASH_MODEL": FLASH38_MODEL,
                "GEMINI_BACKUP_MODEL": "",
                "GROQ_API_KEY": "",
            }

        # Khởi tạo AIService với overrides riêng
        AIService.__init__(self, settings.model_copy(update=overrides))
        self.mode = "production" if use_production_profile else "tier_1_gemini"
        self.custom_mode = mode_label
        self.use_production_profile = use_production_profile
        self.pacer = None
        self.unbounded_timeout_s = UNBOUNDED_CEILING_SECONDS
        self.events: list[dict[str, Any]] = []
        self.result_models: dict[int, str] = {}
        self.coordinator_error: dict[str, Any] | None = None
        self.winner_model: str | None = None
        self.started: float = 0.0
        self.rate_limit_events: list[dict[str, Any]] = []
        self.last_pacing_wait_s: float = 0.0

        gemini_client = self._get_gemini_client()
        if gemini_client and not use_production_profile:
            orig_gen = gemini_client.aio.models.generate_content

            async def _unbounded_gemini_gen(**kwargs: Any) -> Any:
                if "config" in kwargs and kwargs["config"] is not None:
                    kwargs["config"].http_options = types.HttpOptions(
                        timeout=int(self.unbounded_timeout_s * 1000)
                    )
                return await orig_gen(**kwargs)

            cast(Any, gemini_client.aio.models).generate_content = _unbounded_gemini_gen

        self._get_groq_client()

    def _extraction_profile(self) -> ExecutionTimingProfile:
        if self.use_production_profile:
            return AIService._extraction_profile(self)
        t = self.unbounded_timeout_s
        return ExecutionTimingProfile(t, t, t, groq_hedge_at=None)

    def _interactive_profile(self) -> ExecutionTimingProfile:
        if self.use_production_profile:
            return AIService._interactive_profile(self)
        t = self.unbounded_timeout_s
        return ExecutionTimingProfile(t, t, t, groq_hedge_at=None)

    def _decomposition_profile(self) -> ExecutionTimingProfile:
        if self.use_production_profile:
            return AIService._decomposition_profile(self)
        t = self.unbounded_timeout_s
        return ExecutionTimingProfile(t, t, t, groq_hedge_at=None)

    async def run_custom_case(self, case: dict[str, Any]) -> dict[str, Any]:
        from benchmark_all_ufs_live import UF_HARD_TIMEOUTS

        self.events = []
        self.result_models = {}
        self.coordinator_error = None
        self.winner_model = None
        self.rate_limit_events = []
        self.last_pacing_wait_s = 0.0
        self.started = time.perf_counter()

        result: Any = None
        raw_exc: BaseException | None = None
        try:
            result = await self._invoke_uf_method(case)
        except Exception as exc:  # noqa: BLE001
            raw_exc = exc

        elapsed = time.perf_counter() - self.started
        if self._cancelling_provider_tasks:
            await asyncio.gather(*list(self._cancelling_provider_tasks), return_exceptions=True)

        uf = case["uf"]
        uf_hard_timeout = UF_HARD_TIMEOUTS[uf]
        is_fallback_tier3 = (
            result is not None and getattr(result, "generation_tier", None) == "tier_3_rule_based"
        )
        if self.winner_model and not is_fallback_tier3:
            status = "success"
        elif self.coordinator_error and self.coordinator_error.get("error_type") == "TimeoutError":
            status = "timeout"
        elif is_fallback_tier3 and self.coordinator_error is None:
            status = "fallback_tier3"
        else:
            status = "error"

        checks = _evaluate_case(case, result, raw_exc)
        all_checks_passed = bool(checks) and all(checks.values())
        winning_event = next(
            (
                ev
                for ev in reversed(self.events)
                if ev.get("status") == "schema_valid"
                and (self.winner_model is None or ev.get("model") == self.winner_model)
            ),
            None,
        )
        api_elapsed_s = winning_event["elapsed_s"] if winning_event else None

        return {
            "timestamp": datetime.now().astimezone().isoformat(),
            "mode": self.custom_mode,
            "uf": uf,
            "case_id": case["case_id"],
            "description": case["description"],
            "elapsed_s": round(elapsed, 4),
            "api_response_s": api_elapsed_s,
            "pacing_wait_s": 0.0,
            "target_sla_s": DEFAULT_SLA_SECONDS,
            "uf_hard_timeout_s": uf_hard_timeout,
            "exceeded_sla": elapsed > DEFAULT_SLA_SECONDS,
            "exceeded_uf_hard_timeout": elapsed > uf_hard_timeout,
            "status": status,
            "winner_model": self.winner_model,
            "generation_tier": (
                getattr(result, "generation_tier", None)
                if result is not None
                else ("error" if raw_exc is not None else None)
            ),
            "checks": checks,
            "all_checks_passed": all_checks_passed,
            "usage": winning_event.get("usage") if winning_event else None,
            "coordinator_error": self.coordinator_error
            or (error_info(raw_exc) if raw_exc else None),
            "prior_429_events": [],
            "attempts": self.events,
            "result": (result.model_dump(mode="json") if isinstance(result, BaseModel) else None),
        }


def write_merged_outputs(
    out_json: Path,
    base_data: dict[str, Any],
    flash38_runs: list[dict[str, Any]],
    started_at: str,
) -> tuple[Path, Path]:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    md_path = out_json.with_suffix(".md")
    log_path = out_json.with_name(f"{out_json.stem}_exceeded.log")

    base_llm: list[dict[str, Any]] = base_data["llm_runs"]

    # Gộp Production hybrid: UF1 từ lượt đo gốc (3.5-flash-lite) + UF2/UF6 từ production_with_flash_3_8
    uf1_prod = [r for r in base_llm if r["mode"] == "production" and r["uf"] == "UF1"]
    uf26_prod_flash38 = [
        r for r in flash38_runs if r["mode"] == "production_flash38" and r["uf"] != "UF1"
    ]
    hybrid_prod = [
        {**r, "mode": "production_hybrid"} for r in [*uf1_prod, *uf26_prod_flash38]
    ]

    # Tier 1 Primary hybrid: UF1 (3.5-flash-lite) + UF2/UF6 (3.8-flash unbounded)
    uf1_t1 = [r for r in base_llm if r["mode"] == "tier_1_gemini" and r["uf"] == "UF1"]
    uf26_t1_flash38 = [
        r for r in flash38_runs if r["mode"] == "tier_1_flash38_only" and r["uf"] != "UF1"
    ]
    hybrid_t1 = [
        {**r, "mode": "tier_1_hybrid_primary"} for r in [*uf1_t1, *uf26_t1_flash38]
    ]

    # Tier 1 Warm Backup (3.5-flash-lite trên UF2/UF6 từ lượt đo gốc)
    uf26_backup_lite = [
        {**r, "mode": "tier_1_backup_flash_lite_uf26"}
        for r in base_llm
        if r["mode"] == "tier_1_gemini" and r["uf"] != "UF1"
    ]

    tier2_qwen = [r for r in base_llm if r["mode"] == "tier_2_groq_qwen"]
    tier2_gpt_oss = [r for r in base_llm if r["mode"] == "tier_2_groq_gpt_oss"]

    combined_views = [
        *hybrid_prod,
        *hybrid_t1,
        *[r for r in flash38_runs if r["mode"] == "tier_1_flash38_only"],
        *uf26_backup_lite,
        *tier2_qwen,
        *tier2_gpt_oss,
    ]

    modes_order = [
        "production_hybrid",
        "tier_1_hybrid_primary",
        "tier_1_flash38_only",
        "tier_1_backup_flash_lite_uf26",
        "tier_2_groq_qwen",
        "tier_2_groq_gpt_oss",
    ]
    mode_descriptions = {
        "production_hybrid": "Production Hybrid (UF1=3.5-flash-lite; UF2/UF6=3.8-flash + Warm Backup 3.5-flash-lite + Groq)",
        "tier_1_hybrid_primary": "Tier 1 Primary Hybrid (UF1=3.5-flash-lite; UF2/UF6=3.8-flash riêng lẻ, trần 35s)",
        "tier_1_flash38_only": "Tier 1 Riêng `gemini-3.8-flash` (chỉ UF2/UF6, trần 35s)",
        "tier_1_backup_flash_lite_uf26": "Tier 1 Warm Backup `gemini-3.5-flash-lite` (UF2/UF6, từ lượt đo gốc)",
        "tier_2_groq_qwen": "Tier 2 Groq `qwen/qwen3.8-27b` (28 ca, từ lượt đo gốc)",
        "tier_2_groq_gpt_oss": "Tier 2 Groq `openai/gpt-oss-20b` (16 ca UF2/UF6, từ lượt đo gốc)",
    }
    ufs_order = ["UF1", "UF2_Decompose", "UF2_Adjust", "UF6"]

    summary_by_mode = {
        m: _summarize_group([r for r in combined_views if r["mode"] == m]) for m in modes_order
    }
    summary_by_mode_uf = {
        f"{m}::{uf}": _summarize_group(
            [r for r in combined_views if r["mode"] == m and r["uf"] == uf]
        )
        for m in modes_order
        for uf in ufs_order
    }

    # Winner model trong production_hybrid
    winner_breakdown: dict[str, dict[str, Any]] = {}
    for uf in ufs_order:
        uf_rows = [r for r in hybrid_prod if r["uf"] == uf]
        models_seen = sorted(
            {str(r.get("winner_model") or r.get("generation_tier") or "none") for r in uf_rows}
        )
        for wm in models_seen:
            winner_breakdown[f"{uf}::{wm}"] = _summarize_group(
                [
                    r
                    for r in uf_rows
                    if str(r.get("winner_model") or r.get("generation_tier") or "none") == wm
                ]
            )

    merged_report = {
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "base_report_source": str(BASE_JSON),
        "reference_now": REFERENCE_NOW.isoformat(),
        "policy": "UF1 dùng gemini-3.5-flash-lite (MINIMAL) + Qwen; UF2 & UF6 dùng gemini-3.8-flash (LOW) + Warm Backup 3.5-flash-lite + Groq (Qwen/GPT-OSS).",
        "timing_config_used_in_production_flash38": {
            "UF1": {
                "model": settings.GEMINI_UF1_MODEL,
                "fast_fail_groq_s": settings.AI_UF1_GROQ_FAST_FAIL_SECONDS,
                "hard_timeout_s": settings.AI_UF1_TIMEOUT_SECONDS,
            },
            "UF2_Decompose_FrameB": {
                "primary": FLASH38_MODEL,
                "backup": settings.GEMINI_BACKUP_MODEL,
                "warm_backup_at_s": settings.AI_UF2_WARM_BACKUP_DELAY_SECONDS,
                "primary_priority_until_s": settings.AI_UF2_PRIMARY_PRIORITY_SECONDS,
                "groq_hedge_at_s": settings.AI_UF2_GROQ_HEDGE_DELAY_SECONDS,
                "hard_timeout_s": settings.AI_UF2_TIMEOUT_SECONDS,
            },
            "UF2_Adjust_and_UF6_FrameA": {
                "primary": FLASH38_MODEL,
                "backup": settings.GEMINI_BACKUP_MODEL,
                "warm_backup_at_s": settings.AI_WARM_BACKUP_DELAY_SECONDS,
                "primary_priority_until_s": settings.AI_PRIMARY_PRIORITY_SECONDS,
                "groq_hedge_at_s": settings.AI_GROQ_HEDGE_DELAY_SECONDS,
                "hard_timeout_s": settings.AI_TIMEOUT_SECONDS,
            },
        },
        "summary": {
            "by_mode": summary_by_mode,
            "by_mode_uf": summary_by_mode_uf,
            "production_hybrid_winners_by_uf": winner_breakdown,
        },
        "flash38_new_runs": flash38_runs,
        "combined_views": combined_views,
    }
    out_json.write_text(json.dumps(merged_report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Thống kê Gộp: Chỉ bỏ 3.8-Flash ở UF1, các UF khác giữ `gemini-3.8-flash`",
        "",
        f"- **Thời điểm chạy bổ sung 3.8-Flash:** `{started_at}` $\\rightarrow$ `{merged_report['finished_at']}`",
        "- **Nguồn gộp UF1 & Tier 2/Backup:** `POC/logs/all_ufs_live_20261116.json`",
        f"- **Chính sách kiểm chứng:** UF1 = `{settings.GEMINI_UF1_MODEL}` (`MINIMAL`) + Qwen; UF2/UF6 = `{FLASH38_MODEL}` (`LOW`) + Warm Backup `{settings.GEMINI_BACKUP_MODEL}` + Groq (`{settings.GROQ_FALLBACK_MODEL}` / `{settings.GROQ_SECONDARY_FALLBACK_MODEL}`).",
        f"- **Mốc Khung A (UF2-Adjust / UF6):** Warm Backup `{settings.AI_WARM_BACKUP_DELAY_SECONDS}s` | Priority `{settings.AI_PRIMARY_PRIORITY_SECONDS}s` | Groq Hedge `{settings.AI_GROQ_HEDGE_DELAY_SECONDS}s` | Hard `{settings.AI_TIMEOUT_SECONDS}s`.",
        f"- **Mốc Khung B (UF2-Decompose):** Warm Backup `{settings.AI_UF2_WARM_BACKUP_DELAY_SECONDS}s` | Priority `{settings.AI_UF2_PRIMARY_PRIORITY_SECONDS}s` | Groq Hedge `{settings.AI_UF2_GROQ_HEDGE_DELAY_SECONDS}s` | Hard `{settings.AI_UF2_TIMEOUT_SECONDS}s`.",
        "",
        "## 1. Bảng Tổng hợp Theo Từng Tier / Chế độ (Sau khi Gộp)",
        "",
        "| Chế độ / Tier | Mô tả | Tổng ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min (s) | **Tối đa (s)** | > 2.5s | > Trần UF |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in modes_order:
        s = summary_by_mode[m]
        if not s.get("n_total"):
            continue
        md_lines.append(
            f"| `{m}` | {mode_descriptions[m]} | {s['n_total']} | {s['n_success']} | {s['n_business_pass']} | "
            f"{_fmt_num(s['avg_success_s'])} | {_fmt_num(s['median_success_s'])} | "
            f"{_fmt_num(s['min_success_s'])} | {_fmt_num(s['max_success_s'])} | "
            f"{s['over_sla_2_5s']} | {s['over_uf_hard_timeout']} |"
        )

    md_lines += [
        "",
        "## 2. Bảng Chi tiết Theo Từng Tier × Từng UF",
        "",
        "| Chế độ / Tier | UF | Số ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min–Max (s) | > 2.5s | > Trần UF |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in modes_order:
        for uf in ufs_order:
            s = summary_by_mode_uf[f"{m}::{uf}"]
            if not s.get("n_total"):
                continue
            minmax = (
                f"{_fmt_num(s['min_success_s'])}–{_fmt_num(s['max_success_s'])}"
                if s["min_success_s"] is not None
                else "—"
            )
            md_lines.append(
                f"| `{m}` | `{uf}` | {s['n_total']} | {s['n_success']} | {s['n_business_pass']} | "
                f"{_fmt_num(s['avg_success_s'])} | {_fmt_num(s['median_success_s'])} | {minmax} | "
                f"{s['over_sla_2_5s']} | {s['over_uf_hard_timeout']} |"
            )

    md_lines += [
        "",
        "## 3. Phân tích Winner Thực tế Trong `production_hybrid` (AI nào thắng?)",
        "",
        "| UF | Winner Model | Số ca thắng | Trung bình (s) | Min–Max (s) |",
        "|---|---|---:|---:|---:|",
    ]
    for key, s in winner_breakdown.items():
        uf, wm = key.split("::", 1)
        minmax = f"{_fmt_num(s['min_all_s'])}–{_fmt_num(s['max_all_s'])}"
        md_lines.append(
            f"| `{uf}` | `{wm}` | {s['n_total']} | {_fmt_num(s['avg_all_s'])} | {minmax} |"
        )

    md_lines += [
        "",
        "## 4. Chi tiết Từng Lượt Chạy Mới (`gemini-3.8-flash` trên UF2 & UF6)",
        "",
        "| Mode | UF | Testcase | Status | Winner Model | Elapsed (s) | Checks | Timeline Attempts |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    for r in flash38_runs:
        failed_checks = [k for k, v in r.get("checks", {}).items() if not v]
        chk_text = "PASS" if r.get("all_checks_passed") else f"FAIL: {', '.join(failed_checks)}"
        timeline = "; ".join(
            f"{a['model']}={a['status']}@{a['start_s']}s({a.get('elapsed_s', '?')}s)"
            for a in r.get("attempts", [])
        )
        md_lines.append(
            f"| `{r['mode']}` | `{r['uf']}` | `{r['case_id']}` | `{r['status']}` | "
            f"`{r.get('winner_model') or r.get('generation_tier')}` | {r['elapsed_s']:.3f} | "
            f"{chk_text} | `{timeline}` |"
        )

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # Ghi log chi tiết các ca vượt ngưỡng / lỗi / Warm Backup cướp cờ / > 2.5s / > Trần UF
    flagged = [
        r
        for r in [*hybrid_prod, *[x for x in flash38_runs if x["mode"] == "tier_1_flash38_only"]]
        if (
            r.get("exceeded_sla")
            or r.get("exceeded_uf_hard_timeout")
            or r.get("status") != "success"
            or (r.get("checks") is not None and not r.get("all_checks_passed"))
            or (r["mode"] == "production_hybrid" and r["uf"] != "UF1" and r.get("winner_model") != FLASH38_MODEL)
        )
    ]

    log_lines = [
        f"=== MERGED HYBRID (UF1=Flash-Lite; UF2/UF6=Flash-3.8) EXCEEDED & HANDOFF LOG ({merged_report['finished_at']}) ===",
        f"Total flagged entries: {len(flagged)}",
        "Bao gồm: vượt SLA 2.5s, vượt trần timeout UF (4.5s / 9.0s), lỗi/fail check, hoặc 3.8-Flash chậm hơn priority window nên Warm Backup/Groq thắng.",
        "",
    ]
    for idx, r in enumerate(flagged, start=1):
        reasons = []
        if r.get("exceeded_uf_hard_timeout"):
            reasons.append(f"EXCEEDED_UF_HARD_TIMEOUT(>{r['uf_hard_timeout_s']}s)")
        if r.get("exceeded_sla"):
            reasons.append(f"EXCEEDED_TARGET_SLA(>{DEFAULT_SLA_SECONDS}s)")
        if r.get("status") != "success":
            reasons.append(f"STATUS_{str(r['status']).upper()}")
        if not r.get("all_checks_passed"):
            failed = [k for k, v in r.get("checks", {}).items() if not v]
            reasons.append(f"FAILED_CHECKS({', '.join(failed)})")
        if r["mode"] == "production_hybrid" and r["uf"] != "UF1" and r.get("winner_model") != FLASH38_MODEL:
            reasons.append(f"FALLBACK_WINNER({r.get('winner_model')})")

        log_lines.append(f"--- [{idx}] {r['mode']} | {r['uf']} | {r['case_id']} ---")
        log_lines.append(f"Timestamp         : {r.get('timestamp')}")
        log_lines.append(f"Reasons           : {' | '.join(reasons)}")
        log_lines.append(
            f"Elapsed / API (s) : elapsed={r.get('elapsed_s')}s | api_response={r.get('api_response_s')}s"
        )
        log_lines.append(
            f"Thresholds (s)    : target_sla={DEFAULT_SLA_SECONDS}s | uf_hard_timeout={r.get('uf_hard_timeout_s')}s"
        )
        log_lines.append(
            f"Winner / GenTier  : model={r.get('winner_model')} | generation_tier={r.get('generation_tier')}"
        )
        log_lines.append(f"Checks Detail     : {json.dumps(r.get('checks'), ensure_ascii=False)}")
        if r.get("coordinator_error"):
            log_lines.append(
                f"Error Detail      : {json.dumps(r['coordinator_error'], ensure_ascii=False)}"
            )
        log_lines.append(f"Attempts Timeline : {json.dumps(r.get('attempts'), ensure_ascii=False)}")
        if r.get("result") is not None:
            log_lines.append(f"Parsed Result     : {json.dumps(r['result'], ensure_ascii=False)}")
        log_lines.append("")

    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    return md_path, log_path


async def main_async(base_data: dict[str, Any], out_json: Path) -> None:
    if not settings.has_gemini_auth or not settings.has_groq_key:
        raise RuntimeError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY.")

    logging.disable(logging.CRITICAL)
    started_at = datetime.now().astimezone().isoformat()
    all_cases = _normalize_cases(_build_test_cases())
    uf26_cases = [c for c in all_cases if c["uf"] != "UF1"]

    prod_38_svc = Flash38MeasuredService("production_flash38", use_production_profile=True)
    only_38_svc = Flash38MeasuredService("tier_1_flash38_only", use_production_profile=False)
    flash38_runs: list[dict[str, Any]] = []

    try:
        print(f"=== Chạy bổ sung gemini-3.8-flash trên {len(uf26_cases)} cases UF2 & UF6 ===", flush=True)
        for idx, case in enumerate(uf26_cases, start=1):
            for svc in (prod_38_svc, only_38_svc):
                row = await svc.run_custom_case(case)
                flash38_runs.append(row)
                print(
                    f"  [{idx:02d}/{len(uf26_cases)}] {svc.custom_mode:22} {case['uf']:13} {case['case_id']:30} "
                    f"{row['status']:10} {row['elapsed_s']:.3f}s winner={row['winner_model']} pass={row['all_checks_passed']}",
                    flush=True,
                )
                write_merged_outputs(out_json, base_data, flash38_runs, started_at)
                await asyncio.sleep(0.3)
    finally:
        await asyncio.gather(prod_38_svc.close(), only_38_svc.close())
        md_path, log_path = write_merged_outputs(out_json, base_data, flash38_runs, started_at)
        print(f"Hoàn tất gộp! JSON={out_json} | MD={md_path} | LOG={log_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-json", type=Path, default=BASE_JSON)
    parser.add_argument("--output", type=Path, default=DEFAULT_MERGED_JSON)
    args = parser.parse_args()
    base_data = json.loads(args.base_json.read_text(encoding="utf-8"))
    asyncio.run(main_async(base_data, args.output))


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
