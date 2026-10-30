"""Verify the development environment without starting ProActive business code."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import secrets
import sys
from importlib.metadata import version
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent


def prepare_env() -> None:
    """Create a local configuration once; never overwrite an existing .env."""
    target = BACKEND_DIR / ".env"
    if target.exists():
        print("OK .env already exists; kept unchanged")
        return
    sample = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8-sig")
    lines = []
    for line in sample.splitlines():
        if line.startswith("SECRET_KEY="):
            line = f"SECRET_KEY={secrets.token_urlsafe(48)}"
        elif line.startswith(("GEMINI_API_KEY=", "GROQ_API_KEY=")):
            line = line.split("=", 1)[0] + "="
        lines.append(line)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except FileExistsError:
        print("OK .env was created by another process; kept unchanged")
    else:
        print("OK created local .env; generated SECRET_KEY without displaying it")


class ProbeInput(BaseModel):
    value: int


async def check_http_stack() -> None:
    """Use a disposable FastAPI app, not app.main or any real endpoint."""
    probe = FastAPI()

    @probe.post("/environment-probe")
    def echo(body: ProbeInput) -> dict[str, int]:
        return {"value": body.value}

    transport = httpx.ASGITransport(app=probe)
    async with httpx.AsyncClient(transport=transport, base_url="http://environment.test") as client:
        response = await client.post("/environment-probe", json={"value": "12"})
        if response.status_code != 200 or response.json() != {"value": 12}:
            raise RuntimeError("FastAPI/Pydantic/httpx probe failed")
        invalid = await client.post("/environment-probe", json={"value": "invalid"})
        if invalid.status_code != 422:
            raise RuntimeError("Request validation did not return HTTP 422")
    print("OK FastAPI + Pydantic v2 + HTTP test client")


async def check_sqlite() -> None:
    """Probe the async driver in memory; do not touch the project's DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.connect() as connection:
            result = await connection.scalar(text("SELECT 1"))
            if result != 1:
                raise RuntimeError("SQLite async query failed")
    finally:
        await engine.dispose()
    print("OK SQLAlchemy 2.0 + aiosqlite (in-memory database)")


async def check_async_stack() -> None:
    await check_http_stack()
    await check_sqlite()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-env", action="store_true", help="Create .env only if missing")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        print("ERROR Python 3.12 is required. Run this command through uv.", file=sys.stderr)
        return 1
    if sys.prefix == sys.base_prefix:
        print("ERROR Run through uv inside the backend virtual environment.", file=sys.stderr)
        return 1
    print(f"OK Python {sys.version.split()[0]} in backend virtual environment")
    if args.prepare_env:
        prepare_env()
    for module in (
        "uvicorn",
        "pydantic_settings",
        "asyncpg",
        "alembic",
        "google.genai",
        "groq",
        "edge_tts",
    ):
        importlib.import_module(module)
    asyncio.run(check_async_stack())
    for package in ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "pytest", "ruff", "ty"):
        print(f"  {package}=={version(package)}")
    print("READY: development environment. ProActive APIs and external services are not tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
