# Router cho nhóm API bản nháp công việc UF1 (/api/v1/drafts)
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query

from app.api.deps import CurrentUserDep, DbSessionDep
from app.schemas.common import SuccessEnvelope, build_response_meta
from app.schemas.draft import (
    ConfirmSummaryRequest,
    ConfirmSummaryResult,
    CreateDraftRequest,
    DraftAnalyzeRequest,
    DraftListData,
    PlanningDraft,
    UpdateDraftRequest,
)
from app.services import draft_service

router = APIRouter(prefix="/drafts", tags=["drafts"])


@router.post("", status_code=201, response_model=SuccessEnvelope[PlanningDraft])
async def create_draft(
    payload: CreateDraftRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh tạo bản nháp (ví dụ: lenh_2_1)",
        ),
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[PlanningDraft]:
    """Dòng 2 trên API Sheet: Tạo bản nháp yêu cầu mới (UF1)."""
    draft = await draft_service.create_draft(
        user_id=current_user.user_id,
        idempotency_key=idempotency_key,
        payload=payload,
        db=db,
    )
    return SuccessEnvelope(
        data=draft,
        meta=build_response_meta(),
    )


@router.get("", response_model=SuccessEnvelope[DraftListData])
async def list_drafts(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    limit: Annotated[
        int,
        Query(gt=0, description="Số bản nháp tối đa trả về trong một trang (ví dụ: 20)"),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, description="Con trỏ phân trang từ next_cursor của trang trước"),
    ] = None,
) -> SuccessEnvelope[DraftListData]:
    """Dòng 3 trên API Sheet: Liệt kê các bản nháp của người dùng hiện tại."""
    data = await draft_service.list_drafts(
        user_id=current_user.user_id,
        limit=limit,
        cursor=cursor,
        db=db,
    )
    return SuccessEnvelope(
        data=data,
        meta=build_response_meta(),
    )


@router.get("/{draft_id}", response_model=SuccessEnvelope[PlanningDraft])
async def get_draft(
    draft_id: Annotated[
        str, Path(min_length=1, description="Mã bản nháp cần mở lại (ví dụ: draft_1)")
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[PlanningDraft]:
    """Dòng 4 trên API Sheet: Mở lại một bản nháp theo draft_id."""
    draft = await draft_service.get_draft_by_id(
        user_id=current_user.user_id,
        draft_id=draft_id,
        db=db,
    )
    return SuccessEnvelope(
        data=draft,
        meta=build_response_meta(),
    )


@router.patch("/{draft_id}", response_model=SuccessEnvelope[PlanningDraft])
async def patch_draft(
    draft_id: Annotated[
        str, Path(min_length=1, description="Mã bản nháp cần chỉnh sửa (ví dụ: draft_1)")
    ],
    payload: UpdateDraftRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh sửa bản nháp (ví dụ: lenh_6_1)",
        ),
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[PlanningDraft]:
    """Dòng 6 trên API Sheet: Sửa một phần bản nháp (UF1 hoặc Bước 0 của UF2)."""
    draft = await draft_service.patch_draft(
        user_id=current_user.user_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        payload=payload,
        db=db,
    )
    return SuccessEnvelope(
        data=draft,
        meta=build_response_meta(),
    )


@router.post(
    "/{draft_id}/confirm-summary",
    response_model=SuccessEnvelope[ConfirmSummaryResult],
)
async def confirm_summary(
    draft_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã bản nháp cần xác nhận bảng tóm tắt UF1 (ví dụ: draft_1)",
        ),
    ],
    payload: ConfirmSummaryRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh xác nhận UF1 (ví dụ: lenh_7_1)",
        ),
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[ConfirmSummaryResult]:
    """Dòng 7 trên API Sheet: Xác nhận thông tin UF1 (chỉ đóng dấu summary_confirmed_revision, chưa tạo Task)."""
    result = await draft_service.confirm_draft_summary(
        user_id=current_user.user_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        payload=payload,
        db=db,
    )
    return SuccessEnvelope(
        data=result,
        meta=build_response_meta(),
    )


@router.post(
    "/{draft_id}/analyze",
    response_model=SuccessEnvelope[PlanningDraft],
)
async def analyze_draft(
    draft_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã bản nháp cần AI phân tích và làm rõ 1 vòng (ví dụ: draft_1)",
        ),
    ],
    payload: DraftAnalyzeRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh phân tích AI UF1 (ví dụ: lenh_5_1)",
        ),
    ],
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[PlanningDraft]:
    """Dòng 5 trên API Sheet: Hiểu nội dung người dùng mới nhập hoặc bổ sung và merge vào bản nháp (UF1)."""
    draft = await draft_service.analyze_draft(
        user_id=current_user.user_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        payload=payload,
        db=db,
    )
    return SuccessEnvelope(
        data=draft,
        meta=build_response_meta(),
    )
