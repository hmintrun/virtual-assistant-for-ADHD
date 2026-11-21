# Entrypoint FastAPI app và cấu hình middleware, router cho ProPlan VA
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.deps import DbSessionDep
from app.api.v1.router import api_router
from app.api.v1.system import health_check, metrics_endpoint
from app.core.config import settings
from app.core.database import get_engine, session_factory
from app.core.errors import register_exception_handlers
from app.core.middleware import (
    BodySizeLimitMiddleware,
    ObservabilityMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    install_access_log_redaction_filter,
)
from app.core.security import validate_auth_configuration
from app.services.calendar_sync_worker import process_calendar_sync_jobs

logger = logging.getLogger(__name__)


async def _calendar_sync_background_loop():
    """Background task định kỳ quét và xử lý các Outbox sync jobs."""
    session_maker = session_factory(get_engine())
    while True:
        try:
            async with session_maker() as session:
                await process_calendar_sync_jobs(
                    session, limit=10, skip_busy_connections=True
                )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("[CalendarSyncWorker] Loop exception: %s", exc)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_configuration()
    worker_task = None
    if settings.ENABLE_CALENDAR_SYNC_WORKER and not getattr(settings, "TESTING", False):
        worker_task = asyncio.create_task(_calendar_sync_background_loop())
    try:
        yield
    finally:
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


install_access_log_redaction_filter()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health", include_in_schema=False)
async def root_health_check(db: DbSessionDep) -> dict[str, str]:
    return await health_check(db=db)


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def root_metrics(db: DbSessionDep) -> PlainTextResponse:
    return await metrics_endpoint(db=db)
