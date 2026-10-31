# API endpoints hỗ trợ hệ thống (Nhóm 11): Khả năng hệ thống (Dòng 60), Ngữ cảnh xếp lịch (Dòng 61, 62) và Tra kết quả lệnh (Dòng 63)
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Path, Request

from app.api.deps import CurrentUserDep
from app.core.errors import AppError
from app.schemas.calendar import (
    BusyInterval,
    BusyIntervalSource,
    CalendarContext,
    CalendarSource,
    PlanningContextCreateRequest,
)
from app.schemas.common import (
    AvailabilityRef,
    CapabilitiesData,
    CommandResult,
    ConfiguredParameters,
    ErrorEnvelope,
    SuccessEnvelope,
    TimeInterval,
    build_response_meta,
)
from app.services import plan_service

router = APIRouter(tags=["System - Capabilities, Contexts & Commands"])

_DEFAULT_CAPABILITIES = CapabilitiesData(
    contract_version="v1",
    policy_revision="demo-policy",
    offline_start_supported=False,
    configured_parameters=ConfiguredParameters(
        buffer_multiplier_range=None,
        minimum_sample_count=None,
        audio_max_seconds=None,
        audio_max_bytes=None,
        preview_ttl_seconds=None,
        default_reminder_offset_seconds=900,
        start_grace_seconds=900,
        min_inter_session_gap_seconds=900,
        checkin_timeout_seconds=300,
    ),
    policy_state="requires_configuration_before_release",
)

_FAKE_PLANNING_CONTEXTS_BY_OWNER: dict[tuple[str, str], CalendarContext] = {
    ("user_a", "context_1"): CalendarContext(
        context_id="context_1",
        revision=2,
        availability_ref=AvailabilityRef(
            availability_id="availability_1",
            revision=2,
        ),
        selected_calendar_ids=["proactive@example.com"],
        query_start_end=TimeInterval(
            start_at=datetime.fromisoformat("2026-10-30T07:00:00+07:00"),
            end_at=datetime.fromisoformat("2026-11-13T07:00:00+07:00"),
        ),
        busy_intervals=[],
        fetched_at=datetime.fromisoformat("2026-10-29T15:00:00+07:00"),
        sync_state="valid",
        calendar_errors=[],
        event_snapshots=[],
        coverage_state="complete",
        manual_busy_intervals=[],
        excluded_sources=[],
        selected_calendar_sources=[
            CalendarSource(
                connection_id="calendar_1",
                google_calendar_id="proactive@example.com",
            )
        ],
    ),
    ("user_b", "context_b_1"): CalendarContext(
        context_id="context_b_1",
        revision=1,
        availability_ref=AvailabilityRef(
            availability_id="availability_b_1",
            revision=1,
        ),
        selected_calendar_ids=[],
        query_start_end=TimeInterval(
            start_at=datetime.fromisoformat("2026-10-30T07:00:00+07:00"),
            end_at=datetime.fromisoformat("2026-11-13T07:00:00+07:00"),
        ),
        busy_intervals=[],
        fetched_at=datetime.fromisoformat("2026-10-29T15:00:00+07:00"),
        sync_state="valid",
        calendar_errors=[],
        event_snapshots=[],
        coverage_state="complete",
        manual_busy_intervals=[],
        excluded_sources=[],
        selected_calendar_sources=[],
    ),
}

_CREATE_CONTEXT_IDEMPOTENCY_STORE: dict[tuple[str, str], tuple[str, CalendarContext]] = {}

_FAKE_COMMANDS_BY_OWNER: dict[tuple[str, str], CommandResult] = {
    ("user_a", "lenh_confirm_1"): CommandResult(
        command_id="lenh_confirm_1",
        command_type="confirm_candidate",
        state="applied",
        result_reference={"task_id": "task_1"},
        applied_at=datetime.fromisoformat("2026-10-29T15:00:00+07:00"),
    ),
}


@router.get(
    "/capabilities",
    response_model=SuccessEnvelope[CapabilitiesData],
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Chưa đăng nhập hoặc thiếu Bearer Token",
        },
    },
    summary="Đọc khả năng hệ thống (Dòng 60 trên API Sheet)",
)
async def get_capabilities(
    request: Request,
    current_user: CurrentUserDep,
) -> SuccessEnvelope[CapabilitiesData]:
    """Cho Mobile App biết phiên bản hợp đồng, ngưỡng cấu hình và tính năng backend đang hỗ trợ."""
    _ = current_user
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=_DEFAULT_CAPABILITIES,
        meta=build_response_meta(request_id),
    )


@router.post(
    "/planning-contexts",
    status_code=201,
    response_model=SuccessEnvelope[CalendarContext],
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Chưa đăng nhập hoặc thiếu Bearer Token",
        },
        409: {
            "model": ErrorEnvelope,
            "description": "Trùng Idempotency-Key với nội dung khác",
        },
        422: {
            "model": ErrorEnvelope,
            "description": "Thiếu giờ rảnh đã xác nhận hoặc khoảng thời gian cần xếp lịch",
        },
        503: {
            "model": ErrorEnvelope,
            "description": "Chưa cấu hình chính sách bắt buộc",
        },
    },
    summary="Chuẩn bị dữ liệu để xếp lịch (Dòng 61 trên API Sheet)",
)
async def create_planning_context(
    request: Request,
    current_user: CurrentUserDep,
    payload: PlanningContextCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Khóa chống trùng lặp lệnh tạo bản chụp ngữ cảnh xếp lịch (ví dụ: lenh_61_1)",
        ),
    ],
) -> SuccessEnvelope[CalendarContext]:
    """Gom giờ rảnh đã xác nhận, lịch bận và nguồn Calendar thành một bản chụp có phiên bản (T24)."""
    fingerprint = json.dumps(
        payload.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    idem_record = _CREATE_CONTEXT_IDEMPOTENCY_STORE.get(
        (current_user.user_id, idempotency_key)
    )
    if idem_record is not None:
        saved_fingerprint, saved_context = idem_record
        if saved_fingerprint != fingerprint:
            raise AppError(
                status_code=409,
                code="IDEMPOTENCY_KEY_REUSED",
                message=(
                    "Mã lệnh đã được dùng với nội dung khác. "
                    "Giữ mã cũ khi retry đúng nội dung; tạo mã mới cho ý định mới."
                ),
                next_action="use_original_payload_or_new_command",
            )
        request_id = getattr(request.state, "request_id", None)
        return SuccessEnvelope(
            data=saved_context,
            meta=build_response_meta(request_id),
        )

    # Kiểm tra khoảng thời gian hợp lệ (có múi giờ và from < to)
    if (
        payload.from_at.tzinfo is None
        or payload.to_at.tzinfo is None
        or payload.from_at >= payload.to_at
    ):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Thiếu giờ rảnh đã xác nhận hoặc khoảng thời gian cần xếp lịch.",
            next_action="fix_fields",
        )

    # Kiểm tra các khoảng manual_busy_intervals hợp lệ
    for manual_item in payload.manual_busy_intervals:
        if (
            manual_item.start_at.tzinfo is None
            or manual_item.end_at.tzinfo is None
            or manual_item.start_at >= manual_item.end_at
        ):
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Thiếu giờ rảnh đã xác nhận hoặc khoảng thời gian cần xếp lịch.",
                next_action="fix_fields",
            )

    busy_intervals = [
        BusyInterval(
            start_at=m.start_at,
            end_at=m.end_at,
            source=BusyIntervalSource(
                kind="manual",
                client_interval_id=m.client_interval_id,
            ),
        )
        for m in payload.manual_busy_intervals
    ]
    coverage_state = "manual_fallback" if payload.excluded_sources else "complete"
    selected_ids = [s.google_calendar_id for s in payload.selected_calendar_sources]

    # Nếu khớp với ví dụ chuẩn Dòng 61 của user_a thì giữ context_id = "context_1" (revision 2)
    is_default_demo = (
        current_user.user_id == "user_a"
        and payload.availability_ref.availability_id == "availability_1"
        and not payload.manual_busy_intervals
        and not payload.excluded_sources
    )
    context_id = (
        "context_1"
        if is_default_demo
        else f"context_{len(_FAKE_PLANNING_CONTEXTS_BY_OWNER) + 1}"
    )
    revision = 2 if is_default_demo else 1

    new_context = CalendarContext(
        context_id=context_id,
        revision=revision,
        availability_ref=payload.availability_ref,
        selected_calendar_ids=selected_ids,
        query_start_end=TimeInterval(
            start_at=payload.from_at,
            end_at=payload.to_at,
        ),
        busy_intervals=busy_intervals,
        fetched_at=datetime.fromisoformat("2026-10-29T15:00:00+07:00"),
        sync_state="valid",
        calendar_errors=[],
        event_snapshots=[],
        coverage_state=coverage_state,
        manual_busy_intervals=list(payload.manual_busy_intervals),
        excluded_sources=list(payload.excluded_sources),
        selected_calendar_sources=list(payload.selected_calendar_sources),
    )

    _FAKE_PLANNING_CONTEXTS_BY_OWNER[(current_user.user_id, context_id)] = new_context
    _CREATE_CONTEXT_IDEMPOTENCY_STORE[(current_user.user_id, idempotency_key)] = (
        fingerprint,
        new_context,
    )
    _FAKE_COMMANDS_BY_OWNER[(current_user.user_id, idempotency_key)] = CommandResult(
        command_id=idempotency_key,
        command_type="create_planning_context",
        state="applied",
        result_reference={"context_id": context_id},
        applied_at=new_context.fetched_at,
    )

    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=new_context,
        meta=build_response_meta(request_id),
    )


@router.get(
    "/planning-contexts/{context_id}",
    response_model=SuccessEnvelope[CalendarContext],
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Chưa đăng nhập hoặc thiếu Bearer Token",
        },
        404: {
            "model": ErrorEnvelope,
            "description": "Không tìm thấy bản chụp ngữ cảnh trong phạm vi quyền truy cập",
        },
    },
    summary="Xem dữ liệu nguồn đã dùng (Dòng 62 trên API Sheet)",
)
async def get_planning_context(
    request: Request,
    current_user: CurrentUserDep,
    context_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã bản chụp ngữ cảnh xếp lịch (ví dụ: context_1)",
        ),
    ],
) -> SuccessEnvelope[CalendarContext]:
    """Đọc lại bản lịch bận và nguồn đã dùng để biết kết quả lập lịch dựa trên dữ liệu nào."""
    context = _FAKE_PLANNING_CONTEXTS_BY_OWNER.get((current_user.user_id, context_id))
    if context is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )
    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=context,
        meta=build_response_meta(request_id),
    )


@router.get(
    "/commands/{command_id}",
    response_model=SuccessEnvelope[CommandResult],
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Chưa đăng nhập hoặc thiếu Bearer Token",
        },
        404: {
            "model": ErrorEnvelope,
            "description": "Chưa tìm thấy biên lai lệnh trong phạm vi của người dùng",
        },
    },
    summary="Tra kết quả lệnh đã gửi (Dòng 63 trên API Sheet)",
)
async def get_command_result(
    request: Request,
    current_user: CurrentUserDep,
    command_id: Annotated[
        str,
        Path(
            min_length=1,
            description="Mã lệnh Idempotency-Key đã gửi trước đó (ví dụ: lenh_confirm_1)",
        ),
    ],
) -> SuccessEnvelope[CommandResult]:
    """Kiểm tra lệnh ghi đã được xử lý chưa và tài nguyên nào đã được tạo để tránh gửi trùng khi rớt mạng (T13, T26)."""
    cmd = _FAKE_COMMANDS_BY_OWNER.get((current_user.user_id, command_id))
    if cmd is None:
        # Kiểm tra thêm trong kho Idempotency của lệnh Confirm Candidate (Dòng 12)
        confirm_record = plan_service._CONFIRM_CANDIDATE_IDEMPOTENCY_STORE.get(
            (current_user.user_id, command_id)
        )
        if confirm_record is not None:
            _, confirm_result = confirm_record
            cmd = CommandResult(
                command_id=command_id,
                command_type="confirm_candidate",
                state="applied",
                result_reference={"task_id": confirm_result.task.task_id},
                applied_at=confirm_result.schedule_version.approved_at,
            )

    if cmd is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )

    request_id = getattr(request.state, "request_id", None)
    return SuccessEnvelope(
        data=cmd,
        meta=build_response_meta(request_id),
    )

