# Entrypoint FastAPI app và cấu hình middleware, router cho ProPlan VA
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
from app.services.tts_prewarm import prewarm_uf5_checkin_audio
from app.services.voice_service import get_tts_service

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
    testing = getattr(settings, "TESTING", False)
    if settings.ENABLE_CALENDAR_SYNC_WORKER and not testing:
        worker_task = asyncio.create_task(_calendar_sync_background_loop())
    # Chạy nền, không chặn server mở cổng: render sẵn audio câu hỏi Check-in UF5.
    prewarm_task = None
    if settings.VOICE_TTS_PREWARM and settings.VOICE_TTS_PROVIDER == "edge_tts" and not testing:
        prewarm_task = asyncio.create_task(prewarm_uf5_checkin_audio(get_tts_service()))
    try:
        yield
    finally:
        for task in (worker_task, prewarm_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


install_access_log_redaction_filter()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.browser_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health", include_in_schema=False)
async def root_health_check(db: DbSessionDep) -> dict[str, str]:
    return await health_check(db=db)


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def root_metrics(request: Request, db: DbSessionDep) -> PlainTextResponse:
    return await metrics_endpoint(request=request, db=db)
