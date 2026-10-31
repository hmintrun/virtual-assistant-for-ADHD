# Entrypoint FastAPI app và cấu hình middleware, router cho ProPlan VA
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers

app = FastAPI(title=settings.PROJECT_NAME)

register_exception_handlers(app)
app.include_router(api_router)


