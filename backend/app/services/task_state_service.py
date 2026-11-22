"""Read-only task planning states, batched without per-task queries."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session, Step, Task
from app.services.planning.durations import calculate_buffered_work


@dataclass(frozen=True)
class TaskPlanningState:
    decomposition_state: str
    scheduling_state: str


async def get_task_planning_states(
    db: AsyncSession, user_id: UUID, task_ids: list[UUID]
) -> dict[UUID, TaskPlanningState]:
    if not task_ids:
        return {}
    tasks = (
        await db.scalars(select(Task).where(Task.user_id == user_id, Task.id.in_(task_ids)))
    ).all()
    steps = (
        await db.scalars(select(Step).where(Step.user_id == user_id, Step.task_id.in_(task_ids)))
    ).all()
    work = {
        key: seconds
        for key, seconds in (
            await db.execute(
                select(Session.step_id, func.sum(Session.work_seconds))
                .join(Step, Step.id == Session.step_id)
                .where(
                    Session.user_id == user_id,
                    Step.user_id == user_id,
                    Step.task_id.in_(task_ids),
                    Session.status.notin_(("cancelled", "skipped")),
                )
                .group_by(Session.step_id)
            )
        ).all()
    }
    result = {}
    for task in tasks:
        children = [step for step in steps if step.task_id == task.id]
        fully_scheduled = (
            bool(children)
            and task.decomposition_state == "complete"
            and not task.unexpanded_scope
            and all(
                (work.get(step.id, 0) or 0)
                >= calculate_buffered_work(step.remaining_base_seconds, step.buffer_multiplier)
                if step.remaining_base_seconds
                else True
                for step in children
            )
        )
        any_scheduled = any(work.get(step.id, 0) for step in children)
        result[task.id] = TaskPlanningState(
            task.decomposition_state,
            "complete" if fully_scheduled else "partial" if any_scheduled else "not_scheduled",
        )
    return result
