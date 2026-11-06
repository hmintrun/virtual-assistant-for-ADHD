"""Run real HTTP/SQLite UF1–UF2 with providers disabled, then restart Uvicorn."""

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


def test_live_planning_confirmation_survives_restart(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite+aiosqlite:///" + (tmp_path / "planning.db").as_posix(),
        "ENVIRONMENT": "development",
        "GEMINI_API_KEY": "",
        "GROQ_API_KEY": "",
        "GOOGLE_CREDENTIALS_PATH": "",
        "PYTHONIOENCODING": "utf-8",
    }
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert migration.returncode == 0, migration.stderr
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]

    @contextmanager
    def server():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "error",
            ],
            cwd=backend,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=10) as client:
                until = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "Uvicorn exited before readiness"
                    try:
                        if client.get("/capabilities").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < until, "Uvicorn readiness timed out"
                    time.sleep(0.1)
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def post(client, path, key, body, expected):
        result = client.post(path, headers={"Idempotency-Key": key}, json=body)
        assert result.status_code == expected, result.text
        return result.json()["data"]

    start = datetime.now(UTC) + timedelta(minutes=30)
    with server() as client:
        draft = post(
            client,
            "/drafts",
            "live-draft",
            {
                "client_draft_id": "live-restart",
                "input_source": "text",
                "summary": {
                    "title": "Báo cáo",
                    "description": "Viết báo cáo",
                    "scope": "5 trang",
                    "time_constraint": {
                        "kind": "deadline",
                        "start_at": start.isoformat(),
                        "deadline_at": (start + timedelta(days=3)).isoformat(),
                        "input_timezone": "UTC",
                    },
                    "availability": {
                        "timezone": "UTC",
                        "weekly_rules": [
                            {"weekday": i, "start_local": "00:00", "end_local": "23:59"}
                            for i in range(1, 8)
                        ],
                    },
                },
            },
            201,
        )
        draft_id = draft["draft_id"]
        post(
            client,
            f"/drafts/{draft_id}/confirm-summary",
            "live-summary",
            {"expected_summary_revision": 1},
            200,
        )
        plan = post(
            client,
            f"/drafts/{draft_id}/candidates",
            "live-plan",
            {
                "source_summary_revision": 1,
                "source_planning_input_revision": 1,
                "selected_part_keys": None,
                "planning_horizon": None,
            },
            201,
        )
        assert plan["feasibility"]["status"] == "feasible"
        assert all(s["micro_task_seconds"] == 120 for s in plan["sessions"])
        assert client.get("/tasks").json()["data"]["items"] == []
        command = {
            "expected_review_revision": 1,
            "expected_summary_revision": 1,
            "expected_planning_input_revision": 1,
            "expected_source_revisions": plan["source_revisions"],
        }
        confirmed = post(
            client, f"/candidates/{plan['candidate_id']}/confirm", "live-confirm", command, 201
        )
        task_id = confirmed["task"]["task_id"]

    with server() as client:
        replayed = post(
            client, f"/candidates/{plan['candidate_id']}/confirm", "live-confirm", command, 201
        )
        assert replayed == confirmed
        tasks = client.get("/tasks").json()["data"]["items"]
        assert len(tasks) == 1 and tasks[0]["task_id"] == task_id
        reopened = client.get(f"/drafts/{draft_id}").json()["data"]
        assert reopened["stage"] == "confirmed"
        assert reopened["confirmed_task_id"] == task_id
        assert all(s["status"] == "scheduled" for s in tasks[0]["sessions"])
