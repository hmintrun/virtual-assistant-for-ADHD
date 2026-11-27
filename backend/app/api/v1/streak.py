"""Router cho API Focus Streak, Khiên bảo vệ và Nối lại nhịp 24h (BE-UF6-04)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUserDep, DbSessionDep
from app.api.v1.service_dispatch import IdempotencyKey, success
from app.schemas.common import ErrorEnvelope, SuccessEnvelope
from app.schemas.streak import StreakData
from app.services import streak_service

router = APIRouter(prefix="/streak", tags=["Focus Streak - UF6"])


@router.get(
    "",
    response_model=SuccessEnvelope[StreakData],
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập hoặc thiếu Bearer Token"},
        503: {"model": ErrorEnvelope, "description": "Dịch vụ chuỗi tập trung chưa sẵn sàng"},
    },
    summary="Đọc trạng thái Focus Streak, Khiên bảo vệ và lịch sử 35 ngày (UF6)",
)
async def get_streak(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> SuccessEnvelope[StreakData]:
    data = await streak_service.get_streak_summary(
        db,
        user_id=current_user.user_id,
        commit_if_changed=True,
    )
    return success(request, data)


@router.post(
    "/repair",
    response_model=SuccessEnvelope[StreakData],
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập hoặc thiếu Bearer Token"},
        409: {
            "model": ErrorEnvelope,
            "description": "Không có cửa sổ 24h Repair hợp lệ hoặc trùng Idempotency-Key",
        },
        422: {"model": ErrorEnvelope, "description": "Thiếu hoặc sai header Idempotency-Key"},
        503: {"model": ErrorEnvelope, "description": "Dịch vụ chuỗi tập trung chưa sẵn sàng"},
    },
    summary="Kích hoạt Nối lại nhịp 24h (24h Repair) cho ngày vừa lỡ (UF6)",
)
async def repair_streak(
    request: Request,
    current_user: CurrentUserDep,
    db: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> SuccessEnvelope[StreakData]:
    data = await streak_service.repair_streak(
        db,
        user_id=current_user.user_id,
        idempotency_key=idempotency_key,
    )
    return success(request, data)
