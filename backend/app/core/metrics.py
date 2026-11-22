"""Collector số liệu giám sát chuẩn Prometheus Text Exposition 0.0.4 (chỉ dùng stdlib)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import importlib
import inspect
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine
from app.core.middleware import LATENCY_BUCKETS, get_http_observations_snapshot
from app.models.calendar import CalendarSyncJob
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_PROCESS_START_MONOTONIC = time.monotonic()

_STANDARD_OUTBOX_STATES: tuple[str, ...] = (
    "queued",
    "processing",
    "completed",
    "failed",
    "conflict",
)

_CIRCUIT_STATE_CODE: dict[str, int] = {
    "closed": 0,
    "half_open": 1,
    "open": 2,
    "unconfigured": -1,
}


class _WinProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_rss_bytes() -> int:
    """Đọc RSS (WorkingSetSize trên Windows, statm/rusage trên POSIX) bằng stdlib."""
    if sys.platform == "win32":
        try:
            windll = getattr(ctypes, "WinDLL", None)
            if windll is None:
                return 0
            kernel32 = windll("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.argtypes = []
            kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
            get_mem_info = getattr(kernel32, "K32GetProcessMemoryInfo", None)
            if get_mem_info is None:
                psapi = windll("psapi", use_last_error=True)
                get_mem_info = psapi.GetProcessMemoryInfo
            get_mem_info.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.POINTER(_WinProcessMemoryCounters),
                ctypes.wintypes.DWORD,
            ]
            get_mem_info.restype = ctypes.wintypes.BOOL
            counters = _WinProcessMemoryCounters()
            counters.cb = ctypes.sizeof(_WinProcessMemoryCounters)
            if get_mem_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
        except Exception:
            return 0
        return 0

    try:
        statm_path = Path("/proc/self/statm")
        if statm_path.is_file():
            parts = statm_path.read_text(encoding="ascii").split()
            sysconf = getattr(os, "sysconf", None)
            if len(parts) >= 2 and callable(sysconf):
                page_size = int(sysconf("SC_PAGE_SIZE"))
                return max(0, int(parts[1]) * page_size)
    except Exception:
        pass

    try:
        resource_mod = importlib.import_module("resource")
        usage = resource_mod.getrusage(resource_mod.RUSAGE_SELF)
        max_rss = int(usage.ru_maxrss)
        if sys.platform == "darwin":
            return max(0, max_rss)
        return max(0, max_rss * 1024)
    except Exception:
        return 0


def _escape_label(val: object) -> str:
    """Escape giá trị label theo đặc tả Prometheus text 0.0.4."""
    return (
        str(val)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _format_labels(pairs: tuple[tuple[str, object], ...]) -> str:
    if not pairs:
        return ""
    encoded = ",".join(f'{key}="{_escape_label(value)}"' for key, value in pairs)
    return f"{{{encoded}}}"


def _append_family_header(lines: list[str], name: str, metric_type: str, help_text: str) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")


def _read_pool_metric(pool: object, method_name: str) -> int:
    if pool is None:
        return 0
    fn = getattr(pool, method_name, None)
    if callable(fn):
        try:
            val = fn()
            if inspect.isawaitable(val):
                close = getattr(val, "close", None)
                if callable(close):
                    close()
                return 0
            return int(val)
        except Exception:
            return 0
    return 0


async def _collect_db_metrics(
    db: AsyncSession | None,
) -> tuple[int, dict[str, int], dict[str, int], int]:
    """Thu thập DB readiness, outbox queue và trạng thái kết nối PostgreSQL nếu có."""
    outbox_counts: dict[str, int] = dict.fromkeys(_STANDARD_OUTBOX_STATES, 0)
    pg_connections: dict[str, int] = {}
    pg_lock_waiting = 0

    if db is None:
        return 0, outbox_counts, pg_connections, pg_lock_waiting

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.debug("Database readiness probe failed during /metrics scrape", exc_info=True)
        return 0, outbox_counts, pg_connections, pg_lock_waiting

    db_up = 1
    try:
        outbox_stmt = select(CalendarSyncJob.job_state, func.count()).group_by(
            CalendarSyncJob.job_state
        )
        for job_state, cnt in (await db.execute(outbox_stmt)).all():
            outbox_counts[str(job_state)] = int(cnt)
    except Exception:
        logger.debug("CalendarSyncJob count failed during /metrics scrape", exc_info=True)
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            try:
                await rollback()
            except Exception:
                pass

    try:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            conn_rows = (
                await db.execute(
                    text(
                        "SELECT COALESCE(state, 'unknown') AS state, COUNT(*) "
                        "FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "GROUP BY COALESCE(state, 'unknown')"
                    )
                )
            ).all()
            for state_name, cnt in conn_rows:
                pg_connections[str(state_name)] = int(cnt)

            lock_val = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) "
                        "FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one_or_none()
            pg_lock_waiting = int(lock_val or 0)
    except Exception:
        logger.debug("pg_stat_activity query failed during /metrics scrape", exc_info=True)
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            try:
                await rollback()
            except Exception:
                pass

    return db_up, outbox_counts, pg_connections, pg_lock_waiting


async def collect_prometheus_metrics(db: AsyncSession | None = None) -> str:
    """Kết xuất toàn bộ số liệu giám sát theo định dạng Prometheus text/plain 0.0.4."""
    t_scrape = time.perf_counter()
    lines: list[str] = []

    # 1. HTTP Request Totals & Latency Histograms
    snapshot = get_http_observations_snapshot()
    sorted_entries = sorted(snapshot.items(), key=lambda item: item[0])

    _append_family_header(
        lines,
        "proactive_http_requests_total",
        "counter",
        "Total HTTP requests observed by method, route template, and status code.",
    )
    for (method, route_template, status_code), metrics in sorted_entries:
        labels = _format_labels(
            (
                ("method", method),
                ("route_template", route_template),
                ("status_code", status_code),
            )
        )
        lines.append(f"proactive_http_requests_total{labels} {metrics['count']}")

    _append_family_header(
        lines,
        "proactive_http_request_duration_seconds",
        "histogram",
        "HTTP request duration histogram in seconds.",
    )
    for (method, route_template, status_code), metrics in sorted_entries:
        base_pairs: tuple[tuple[str, object], ...] = (
            ("method", method),
            ("route_template", route_template),
            ("status_code", status_code),
        )
        buckets = metrics["buckets"]
        for threshold in LATENCY_BUCKETS:
            b_labels = _format_labels((*base_pairs, ("le", threshold)))
            lines.append(
                f"proactive_http_request_duration_seconds_bucket{b_labels} {buckets.get(threshold, 0)}"
            )
        inf_labels = _format_labels((*base_pairs, ("le", "+Inf")))
        base_labels = _format_labels(base_pairs)
        lines.append(f"proactive_http_request_duration_seconds_bucket{inf_labels} {metrics['count']}")
        lines.append(
            f"proactive_http_request_duration_seconds_sum{base_labels} "
            f"{metrics['duration_sum_seconds']:.6f}"
        )
        lines.append(f"proactive_http_request_duration_seconds_count{base_labels} {metrics['count']}")

    # 2. Process Gauges / Counters
    uptime_seconds = max(0.0, time.monotonic() - _PROCESS_START_MONOTONIC)
    cpu_seconds = max(0.0, time.process_time())
    rss_bytes = get_process_rss_bytes()

    _append_family_header(
        lines,
        "proactive_process_uptime_seconds",
        "gauge",
        "Process uptime in seconds.",
    )
    lines.append(f"proactive_process_uptime_seconds {uptime_seconds:.6f}")

    _append_family_header(
        lines,
        "proactive_process_cpu_seconds_total",
        "counter",
        "Total user and system CPU time spent in seconds.",
    )
    lines.append(f"proactive_process_cpu_seconds_total {cpu_seconds:.6f}")

    _append_family_header(
        lines,
        "proactive_process_resident_memory_bytes",
        "gauge",
        "Resident memory size (RSS / WorkingSetSize) in bytes.",
    )
    lines.append(f"proactive_process_resident_memory_bytes {rss_bytes}")

    # 3. Database Connection Pool Gauges
    pool: object | None = None
    if db is not None:
        try:
            bind = db.get_bind()
            pool = getattr(bind, "pool", None) or getattr(
                getattr(bind, "sync_engine", None), "pool", None
            )
        except Exception:
            pool = None
    if pool is None:
        try:
            pool = get_engine().sync_engine.pool
        except Exception:
            pool = None

    pool_metrics: tuple[tuple[str, str, str], ...] = (
        ("proactive_db_pool_size", "size", "Configured SQLAlchemy connection pool size."),
        ("proactive_db_pool_checkedin", "checkedin", "Idle connections currently in the pool."),
        ("proactive_db_pool_checkedout", "checkedout", "Connections currently checked out."),
        ("proactive_db_pool_overflow", "overflow", "Overflow connections currently open."),
    )
    for metric_name, method_name, help_text in pool_metrics:
        _append_family_header(lines, metric_name, "gauge", help_text)
        lines.append(f"{metric_name} {_read_pool_metric(pool, method_name)}")

    # 4. Database Readiness, Calendar Outbox Queue & PostgreSQL Activity
    db_up, outbox_counts, pg_connections, pg_lock_waiting = await _collect_db_metrics(db)

    _append_family_header(
        lines,
        "proactive_db_up",
        "gauge",
        "Database readiness indicator (1 if SELECT 1 succeeded, else 0).",
    )
    lines.append(f"proactive_db_up {db_up}")

    _append_family_header(
        lines,
        "proactive_calendar_outbox_jobs",
        "gauge",
        "Calendar sync outbox job count grouped by job_state.",
    )
    ordered_states = list(_STANDARD_OUTBOX_STATES) + sorted(
        s for s in outbox_counts if s not in _STANDARD_OUTBOX_STATES
    )
    for job_state in ordered_states:
        labels = _format_labels((("job_state", job_state),))
        lines.append(f"proactive_calendar_outbox_jobs{labels} {outbox_counts[job_state]}")

    _append_family_header(
        lines,
        "proactive_db_pg_connections",
        "gauge",
        "PostgreSQL connection count in current database grouped by state.",
    )
    for pg_state in sorted(pg_connections):
        labels = _format_labels((("state", pg_state),))
        lines.append(f"proactive_db_pg_connections{labels} {pg_connections[pg_state]}")

    _append_family_header(
        lines,
        "proactive_db_pg_lock_waiting_connections",
        "gauge",
        "PostgreSQL connections currently waiting on locks in current database.",
    )
    lines.append(f"proactive_db_pg_lock_waiting_connections {pg_lock_waiting}")

    # 5. AI Provider Circuit Breaker States (in-memory read, no network)
    provider_entries: list[dict[str, Any]] = []
    try:
        report = await ai_service.check_providers_status(probe_live=False)
        raw_providers = report.get("providers", {})
        if isinstance(raw_providers, dict):
            provider_entries = sorted(
                (v for v in raw_providers.values() if isinstance(v, dict)),
                key=lambda item: (str(item.get("provider", "")), str(item.get("model", ""))),
            )
    except Exception:
        logger.debug("AI provider status snapshot failed during /metrics scrape", exc_info=True)

    _append_family_header(
        lines,
        "proactive_ai_circuit_state",
        "gauge",
        "AI provider circuit breaker state (closed=0, half_open=1, open=2, unconfigured=-1).",
    )
    for entry in provider_entries:
        labels = _format_labels(
            (
                ("provider", entry.get("provider", "")),
                ("model", entry.get("model", "")),
            )
        )
        state_num = _CIRCUIT_STATE_CODE.get(str(entry.get("state", "unconfigured")), -1)
        lines.append(f"proactive_ai_circuit_state{labels} {state_num}")

    _append_family_header(
        lines,
        "proactive_ai_consecutive_failures",
        "gauge",
        "Consecutive failure count recorded per AI provider and model.",
    )
    for entry in provider_entries:
        labels = _format_labels(
            (
                ("provider", entry.get("provider", "")),
                ("model", entry.get("model", "")),
            )
        )
        failures = int(entry.get("consecutive_failures") or 0)
        lines.append(f"proactive_ai_consecutive_failures{labels} {failures}")

    # 6. Scrape Duration Overhead
    scrape_duration = max(0.0, time.perf_counter() - t_scrape)
    _append_family_header(
        lines,
        "proactive_metrics_scrape_duration_seconds",
        "gauge",
        "Duration of this /metrics scrape in seconds.",
    )
    lines.append(f"proactive_metrics_scrape_duration_seconds {scrape_duration:.6f}")

    return "\n".join(lines) + "\n"
