"""Reads for UF3 services; mutations use the models' optimistic revisions."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import CheckIn, RunEvent, SessionRun


async def get_run(db: AsyncSession, user_id: UUID, run_id: UUID) -> SessionRun | None:
    return await db.scalar(
        select(SessionRun)
        .where(SessionRun.user_id == user_id, SessionRun.id == run_id)
        .execution_options(populate_existing=True)
    )


async def get_live_run(db: AsyncSession, user_id: UUID) -> SessionRun | None:
    result = await db.execute(
        select(SessionRun)
        .where(
            SessionRun.user_id == user_id,
            SessionRun.run_state.in_(("running", "paused", "awaiting_checkin")),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_final_checkin(db: AsyncSession, user_id: UUID, run_id: UUID) -> CheckIn | None:
    result = await db.execute(
        select(CheckIn)
        .where(CheckIn.user_id == user_id, CheckIn.run_id == run_id, CheckIn.kind == "final")
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_events_by_client_ids(
    db: AsyncSession, user_id: UUID, client_event_ids: Sequence[str]
) -> dict[str, RunEvent]:
    """Tra theo user để service phát hiện cả ID bị dùng lại cho Run khác."""
    rows = await db.scalars(
        select(RunEvent)
        .where(RunEvent.user_id == user_id, RunEvent.client_event_id.in_(client_event_ids))
        .execution_options(populate_existing=True)
    )
    return {row.client_event_id: row for row in rows}
