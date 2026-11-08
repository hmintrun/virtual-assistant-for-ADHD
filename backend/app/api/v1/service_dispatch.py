"""Điểm nối router với các service được bàn giao riêng theo W03."""

from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import Annotated, Any, cast

from fastapi import Header, Request

from app.core.errors import AppError
from app.schemas.common import SuccessEnvelope, build_response_meta

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1)]


def service_handler(module_name: str, operation: str) -> Callable[..., Awaitable[Any]]:
    """Không giả lập service; chỉ trì hoãn module/operation chưa được bàn giao."""
    module_path = f"app.services.{module_name}"
    try:
        module = import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name != module_path:
            raise
    else:
        handler = getattr(module, operation, None)
        if callable(handler):
            return cast(Callable[..., Awaitable[Any]], handler)
    raise AppError(
        status_code=503,
        code="SERVICE_NOT_READY",
        message="Chức năng này chưa sẵn sàng. Vui lòng thử lại sau.",
        retryable=True,
        next_action="retry_later",
    )


def success[DataT](request: Request, data: DataT) -> SuccessEnvelope[DataT]:
    return SuccessEnvelope(
        data=data, meta=build_response_meta(getattr(request.state, "request_id", None))
    )
