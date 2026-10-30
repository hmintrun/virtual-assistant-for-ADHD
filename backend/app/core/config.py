"""Cấu hình hệ thống và đọc biến môi trường qua Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE_PATH = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    """Cấu hình trung tâm cho Backend ProPlan VA (Pydantic v2 Settings)."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Cấu hình chung của ứng dụng
    ENVIRONMENT: Literal["development", "staging", "production", "testing"] = "development"
    PROJECT_NAME: str = "ProPlan VA"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = Field(
        default="change-this-to-a-super-secret-jwt-key",
        repr=False,
        description="Khóa bí mật ký JWT; ẩn khỏi repr/log để bảo mật.",
    )

    # Cơ sở dữ liệu (SQLite cho Dev, PostgreSQL cho Production)
    DATABASE_URL: str = "sqlite+aiosqlite:///./proplan.db"

    # Khóa API cho AI & Voice (ẩn khỏi repr/log theo quy tắc bảo mật O00-O26)
    GEMINI_API_KEY: str = Field(default="", repr=False)
    GROQ_API_KEY: str = Field(default="", repr=False)

    # Định danh Model AI & Voice (Dùng gemini-3.8-flash hiện hành thay cho gemini-2.5-* đã deprecated)
    GEMINI_FLASH_MODEL: str = "gemini-3.8-flash"
    GROQ_FALLBACK_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_STT_MODEL: str = "whisper-large-v3-turbo"

    # Ràng buộc độ trễ NFR 5.2 (tối đa 2.5 giây cho mỗi lượt gọi LLM)
    AI_TIMEOUT_SECONDS: float = Field(default=2.5, gt=0.0, le=10.0)

    # Cấu hình mặc định của Trợ lý ở Giai đoạn Lập kế hoạch Tuần 1 (O01 UserPreference)
    DEFAULT_WORK_INTERVAL_SECONDS: int = 1500  # 25 phút tập trung
    DEFAULT_REST_INTERVAL_SECONDS: int = 300  # 5 phút nghỉ ngắn
    DEFAULT_MICRO_TASK_SECONDS: int = 120  # 2 phút khởi động phá vỡ tê liệt ADHD
    DEFAULT_MIN_INTER_SESSION_GAP_SECONDS: int = 900  # Khoảng cách tối thiểu 15 phút giữa 2 phiên
    DEFAULT_BUFFER_MULTIPLIER: float = 1.2
    DEFAULT_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    @property
    def has_gemini_key(self) -> bool:
        """Kiểm tra đã cấu hình GEMINI_API_KEY hợp lệ (khác rỗng và khác giá trị mẫu)."""
        cleaned = self.GEMINI_API_KEY.strip()
        return bool(cleaned and cleaned != "your-gemini-api-key-here")

    @property
    def has_groq_key(self) -> bool:
        """Kiểm tra đã cấu hình GROQ_API_KEY hợp lệ (khác rỗng và khác giá trị mẫu)."""
        cleaned = self.GROQ_API_KEY.strip()
        return bool(cleaned and cleaned != "your-groq-api-key-here")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Trả về singleton Settings được cache trong bộ nhớ."""
    return Settings()


settings = get_settings()
