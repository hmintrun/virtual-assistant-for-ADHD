# Router cho nhóm API đọc công việc chính thức đã xác nhận UF2 (Dòng 13 & 14: GET /api/v1/tasks, GET /api/v1/tasks/{task_id})
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import CurrentUserDep, DbSessionDep
from app.schemas.common import SuccessEnvelope, build_response_meta
from app.schemas.task import Task, TaskListData
from app.services import plan_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=SuccessEnvelope[TaskListData])
async def list_tasks(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    limit: Annotated[
        int,
        Query(gt=0, description="Số công việc tối đa trả về trong một trang (ví dụ: 20)"),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, description="Con trỏ phân trang từ next_cursor của trang trước"),
    ] = None,
) -> SuccessEnvelope[TaskListData]:
    """Dòng 13 trên API Sheet: Liệt kê các công việc chính thức của người dùng hiện tại."""
    data = await plan_service.list_tasks(
        user_id=current_user.user_id,
        db=db,
        limit=limit,
        cursor=cursor,
    )
    return SuccessEnvelope(
        data=data,
        meta=build_response_meta(),
    )


@router.get("/{task_id}", response_model=SuccessEnvelope[Task])
async def get_task(
    task_id: Annotated[
        str,
        Path(min_length=1, description="Mã công việc chính thức cần xem (ví dụ: task_1)"),
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[Task]:
    """Dòng 14 trên API Sheet: Xem chi tiết công việc chính thức kèm PlanningParts, Steps và Sessions."""
    task = await plan_service.get_task_by_id(
        user_id=current_user.user_id,
        db=db,
        task_id=task_id,
    )
    return SuccessEnvelope(
        data=task,
        meta=build_response_meta(),
    )
