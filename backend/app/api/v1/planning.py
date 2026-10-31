# Router cho nhóm API lập kế hoạch UF2 (Dòng 9, 10, 11, 12 trên API Sheet: Candidates & Confirm)
from typing import Annotated

from fastapi import APIRouter, Header, Path

from app.api.deps import CurrentUserDep
from app.schemas.common import SuccessEnvelope, build_response_meta
from app.schemas.planning import (
    ConfirmCandidateRequest,
    ConfirmCandidateResult,
    GenerateCandidateRequest,
    PlanCandidate,
    UpdateCandidateRequest,
)
from app.services import plan_service

router = APIRouter(tags=["planning"])


@router.post(
    "/drafts/{draft_id}/candidates",
    status_code=201,
    response_model=SuccessEnvelope[PlanCandidate],
)
async def create_candidate(
    draft_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã bản nháp đã xác nhận UF1 để tạo phương án lịch UF2 (ví dụ: draft_025)",
        ),
    ],
    payload: GenerateCandidateRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh tạo Candidate (ví dụ: lenh_9_1)",
        ),
    ],
    current_user: CurrentUserDep,
) -> SuccessEnvelope[PlanCandidate]:
    """Dòng 9 trên API Sheet: Tạo phương án chia việc và xếp lịch từ bản nháp đã xác nhận (UF2)."""
    candidate = await plan_service.create_candidate(
        user_id=current_user.user_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return SuccessEnvelope(
        data=candidate,
        meta=build_response_meta(),
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=SuccessEnvelope[PlanCandidate],
)
async def get_candidate(
    candidate_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã phương án lịch cần mở lại (ví dụ: candidate_1)",
        ),
    ],
    current_user: CurrentUserDep,
) -> SuccessEnvelope[PlanCandidate]:
    """Dòng 10 trên API Sheet: Xem phương án chia bước và xếp lịch chưa duyệt (UF2)."""
    candidate = await plan_service.get_candidate_by_id(
        user_id=current_user.user_id,
        candidate_id=candidate_id,
    )
    return SuccessEnvelope(
        data=candidate,
        meta=build_response_meta(),
    )


@router.patch(
    "/candidates/{candidate_id}",
    response_model=SuccessEnvelope[PlanCandidate],
)
async def patch_candidate(
    candidate_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã phương án lịch cần chỉnh sửa (ví dụ: candidate_1)",
        ),
    ],
    payload: UpdateCandidateRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh sửa Candidate (ví dụ: lenh_11_1)",
        ),
    ],
    current_user: CurrentUserDep,
) -> SuccessEnvelope[PlanCandidate]:
    """Dòng 11 trên API Sheet: Sửa phương án nháp theo client_*_key và tính/kiểm lại (UF2)."""
    candidate = await plan_service.patch_candidate(
        user_id=current_user.user_id,
        candidate_id=candidate_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return SuccessEnvelope(
        data=candidate,
        meta=build_response_meta(),
    )


@router.post(
    "/candidates/{candidate_id}/confirm",
    status_code=201,
    response_model=SuccessEnvelope[ConfirmCandidateResult],
)
async def confirm_candidate(
    candidate_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã phương án lịch cần duyệt và lưu chính thức (ví dụ: candidate_1)",
        ),
    ],
    payload: ConfirmCandidateRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh duyệt kế hoạch UF2 (ví dụ: lenh_confirm_1)",
        ),
    ],
    current_user: CurrentUserDep,
) -> SuccessEnvelope[ConfirmCandidateResult]:
    """Dòng 12 trên API Sheet: Duyệt và lưu kế hoạch chính thức nguyên tử (UF2)."""
    result = await plan_service.confirm_candidate(
        user_id=current_user.user_id,
        candidate_id=candidate_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    return SuccessEnvelope(
        data=result,
        meta=build_response_meta(),
    )
