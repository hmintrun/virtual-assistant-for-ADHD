# Bộ gom nhóm tất cả API router phiên bản v1 với tiền tố /api/v1
from fastapi import APIRouter

from app.api.v1 import (
    calendars,
    drafts,
    planning,
    schedule,
    settings,
    system,
    tasks,
    voice,
)
from app.core.config import settings as app_settings

api_router = APIRouter(prefix=app_settings.API_V1_STR)
api_router.include_router(calendars.router)
api_router.include_router(drafts.router)
api_router.include_router(voice.router)
api_router.include_router(planning.router)
api_router.include_router(tasks.router)
api_router.include_router(schedule.router)
api_router.include_router(settings.router)
api_router.include_router(system.router)
