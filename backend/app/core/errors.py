# Định nghĩa lỗi nghiệp vụ (AppError) và bộ bắt lỗi tập trung theo đúng Error Envelope của Sheet
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorDetail, ErrorEnvelope, FieldError, build_response_meta

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Lỗi nghiệp vụ chuẩn để bất kỳ tầng Service/Dependency nào ném ra đều trả đúng JSON Sheet."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        next_action: str,
        retryable: bool = False,
        field_errors: list[FieldError] | None = None,
        current_revisions: dict[str, int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.headers = headers or {}
        self.status_code = status_code
        self.code = code
        self.message = message
        self.next_action = next_action
        self.retryable = retryable
        self.field_errors = field_errors or []
        self.current_revisions = current_revisions or {}


def register_exception_handlers(app: FastAPI) -> None:
    """Gắn các bộ bắt lỗi vào FastAPI app để luôn trả về cấu trúc {error, meta}."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                field_errors=exc.field_errors,
                retryable=exc.retryable,
                current_revisions=exc.current_revisions,
                next_action=exc.next_action,
            ),
            meta=build_response_meta(getattr(request.state, "request_id", None)),
        )
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=envelope.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors = [
            FieldError(
                field_path=".".join(str(loc) for loc in err.get("loc", []) if loc != "body"),
                message=err.get("msg", "Dữ liệu không hợp lệ."),
                code=err.get("type"),
            )
            for err in exc.errors()
        ]
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="Yêu cầu hoặc thông tin bổ sung chưa đủ dữ liệu để xử lý.",
                field_errors=field_errors,
                retryable=False,
                current_revisions={},
                next_action="fix_fields",
            ),
            meta=build_response_meta(getattr(request.state, "request_id", None)),
        )
        return JSONResponse(
            status_code=422,
            content=envelope.model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled exception [request_id=%s]", request_id, exc_info=exc)
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="Đã xảy ra lỗi hệ thống ngoài dự kiến.",
                field_errors=[],
                retryable=True,
                current_revisions={},
                next_action="retry_later",
            ),
            meta=build_response_meta(request_id),
        )
        headers = (
            {"X-Request-ID": request_id}
            if isinstance(request_id, str) and request_id
            else None
        )
        return JSONResponse(
            status_code=500,
            headers=headers,
            content=envelope.model_dump(mode="json"),
        )
