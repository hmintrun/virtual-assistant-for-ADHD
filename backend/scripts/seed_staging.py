"""Idempotent shared staging seed script for W3-04 (PostgreSQL 16 staging & SQLite dev).

Usage (from ``backend/``):
    uv run --frozen python scripts/seed_staging.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import DatabaseSettings, make_engine, session_factory  # noqa: E402
from app.models.settings import Availability  # noqa: E402
from app.services.draft_service import ensure_dev_seed_in_db, resolve_user_uuid  # noqa: E402

DEFAULT_WEEKLY_RULES = [
    {"day_of_week": day, "start_minute": 8 * 60, "end_minute": 12 * 60}
    for day in range(5)
] + [
    {"day_of_week": day, "start_minute": 14 * 60, "end_minute": 18 * 60}
    for day in range(5)
]


async def seed_staging() -> None:
    """Seed shared dev/staging users, preferences, drafts, and default availability."""
    settings = DatabaseSettings()
    engine = make_engine(settings.database_url)
    try:
        async with session_factory(engine)() as db:
            await ensure_dev_seed_in_db(db)
            changed = False
            for raw_uid in ("user_a", "user_b"):
                uid = resolve_user_uuid(raw_uid)
                existing = await db.scalar(
                    select(Availability).where(Availability.user_id == uid)
                )
                if existing is None:
                    db.add(
                        Availability(
                            user_id=uid,
                            timezone="Asia/Ho_Chi_Minh",
                            weekly_rules=DEFAULT_WEEKLY_RULES,
                            date_overrides=[],
                            task_limits=[],
                            source="staging_seed",
                            confirmed_at=datetime.now(timezone.utc),
                        )
                    )
                    changed = True
            if changed:
                await db.commit()
    finally:
        await engine.dispose()
    print(f"Seeded shared staging baseline (dialect={engine.dialect.name}).")


if __name__ == "__main__":
    asyncio.run(seed_staging())
