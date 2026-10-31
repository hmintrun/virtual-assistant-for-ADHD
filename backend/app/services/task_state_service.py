"""Read-only task planning states, batched without per-task queries."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlanningPart, Task


@dataclass(frozen=True)
class TaskPlanningState:
    decomposition_state: str
    scheduling_state: str


async def get_task_planning_states(
    db: AsyncSession, user_id: UUID, task_ids: list[UUID]
) -> dict[UUID, TaskPlanningState]:
    if not task_ids:
        return {}
    stmt = (
        select(
            Task.id,
            func.count(PlanningPart.id),
            func.sum(case((PlanningPart.decomposition_state == "complete", 1), else_=0)),
            func.sum(case((PlanningPart.decomposition_state == "not_started", 1), else_=0)),
            func.sum(case((PlanningPart.scheduling_state == "complete", 1), else_=0)),
            func.sum(case((PlanningPart.scheduling_state == "not_scheduled", 1), else_=0)),
        )
        .outerjoin(
            PlanningPart, (PlanningPart.task_id == Task.id) & (PlanningPart.user_id == Task.user_id)
        )
        .where(Task.user_id == user_id, Task.id.in_(task_ids))
        .group_by(Task.id)
    )
    result = {}
    for task_id, total, decomposed, untouched, scheduled, unscheduled in (
        await db.execute(stmt)
    ).all():
        decomposition = (
            "not_started"
            if total == 0 or untouched == total
            else "complete"
            if decomposed == total
            else "partial"
        )
        scheduling = (
            "not_scheduled"
            if total == 0 or unscheduled == total
            else "complete"
            if scheduled == total and decomposition == "complete"
            else "partial"
        )
        result[task_id] = TaskPlanningState(decomposition, scheduling)
    return result
