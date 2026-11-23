# Router cho nhóm API đọc và hủy công việc chính thức đã xác nhận UF2 (GET/DELETE /api/v1/tasks)
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query

from app.api.deps import CurrentUserDep, DbSessionDep
from app.schemas.common import ErrorEnvelope, SuccessEnvelope, build_response_meta
from app.schemas.task import Task, TaskDeleteResult, TaskListData
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
    """Dòng 14: Xem công việc chính thức cùng Steps và Sessions, không có tầng Part."""
    task = await plan_service.get_task_by_id(
        user_id=current_user.user_id,
        db=db,
        task_id=task_id,
    )
    return SuccessEnvelope(
        data=task,
        meta=build_response_meta(),
    )


@router.delete(
    "/{task_id}",
    status_code=200,
    response_model=SuccessEnvelope[TaskDeleteResult],
    responses={
        401: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
async def delete_task(
    task_id: Annotated[
        str,
        Path(min_length=1, description="Mã công việc chính thức cần hủy/xóa (ví dụ: task_1)"),
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            description="Mã chống gửi lặp tùy chọn cho lệnh hủy/xóa công việc",
        ),
    ] = None,
    expected_task_revision: Annotated[
        int | None,
        Query(ge=1, description="Phiên bản công việc kỳ vọng để chống xung đột ghi đè"),
    ] = None,
) -> SuccessEnvelope[TaskDeleteResult]:
    """Hủy mềm công việc chính thức, đóng các bước/phiên/nhắc nhở còn mở và đồng bộ xóa lịch Google Calendar."""
    result = await plan_service.delete_task(
        user_id=current_user.user_id,
        task_id=task_id,
        db=db,
        idempotency_key=idempotency_key,
        expected_task_revision=expected_task_revision,
    )
    return SuccessEnvelope(
        data=result,
        meta=build_response_meta(),
    )
