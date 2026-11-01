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

    # Khóa API & Chứng thực Cloud cho AI/Voice (ẩn khỏi repr/log theo quy tắc bảo mật O00-O26)
    GEMINI_API_KEY: str = Field(default="", repr=False)
    GROQ_API_KEY: str = Field(default="", repr=False)
    GOOGLE_CREDENTIALS_PATH: str = Field(
        default="credentials.json",
        repr=False,
        description=(
            "Đường dẫn tương đối (từ backend/) hoặc tuyệt đối tới file JSON Service Account "
            "khi dùng Vertex AI / Agent Platform Postpay. Tự động ưu tiên nếu file tồn tại."
        ),
    )
    GOOGLE_CLOUD_PROJECT: str = Field(default="", repr=False)
    GOOGLE_CLOUD_LOCATION: str = "global"

    # Định danh Model AI & Voice (Đã kiểm chứng thực nghiệm trên Postpay Tier 1 & Groq 2026)
    GEMINI_FLASH_MODEL: str = "gemini-3.8-flash"
    GEMINI_BACKUP_MODEL: str = "gemini-3.5-flash-lite"
    GROQ_FALLBACK_MODEL: str = "qwen/qwen3.8-27b"
    GROQ_SECONDARY_FALLBACK_MODEL: str = "openai/gpt-oss-20b"
    GROQ_STT_MODEL: str = "whisper-large-v3-turbo"

    # Khung thời gian A — Luồng tương tác trực tiếp (UF1 & UF6):
    # 0.0s chạy 3.8-flash -> 2.6s Warm Backup 3.5-flash-lite -> 3.5s Groq -> ưu tiên 3.8-flash tới 4.0s -> cắt cứng 4.5s
    AI_TIMEOUT_SECONDS: float = Field(default=4.5, gt=0.0, le=20.0)
    AI_PRIMARY_PRIORITY_SECONDS: float = Field(default=4.0, gt=0.0, le=20.0)
    AI_WARM_BACKUP_DELAY_SECONDS: float = Field(default=2.6, ge=0.0, le=20.0)
    AI_GROQ_HEDGE_DELAY_SECONDS: float = Field(default=3.5, ge=0.0, le=20.0)

    # Khung thời gian B — Luồng chia nhỏ công việc (UF2 - có màn hình Breathing Circle):
    # 0.0s chạy 3.8-flash -> 3.1s Warm Backup 3.5-flash-lite -> 4.4s Groq -> ưu tiên 3.8-flash tới 5.4s -> cắt cứng 6.0s
    AI_UF2_TIMEOUT_SECONDS: float = Field(default=6.0, gt=0.0, le=25.0)
    AI_UF2_PRIMARY_PRIORITY_SECONDS: float = Field(default=5.4, gt=0.0, le=25.0)
    AI_UF2_WARM_BACKUP_DELAY_SECONDS: float = Field(default=3.1, ge=0.0, le=25.0)
    AI_UF2_GROQ_HEDGE_DELAY_SECONDS: float = Field(default=4.4, ge=0.0, le=25.0)

    # Cấu hình mặc định của Trợ lý ở Giai đoạn Lập kế hoạch Tuần 1 (O01 UserPreference)
    DEFAULT_WORK_INTERVAL_SECONDS: int = 1500  # 25 phút tập trung
    DEFAULT_REST_INTERVAL_SECONDS: int = 300  # 5 phút nghỉ ngắn
    DEFAULT_MICRO_TASK_SECONDS: int = 120  # 2 phút khởi động phá vỡ tê liệt ADHD
    DEFAULT_MIN_INTER_SESSION_GAP_SECONDS: int = 900  # Khoảng cách tối thiểu 15 phút giữa 2 phiên
    DEFAULT_BUFFER_MULTIPLIER: float = 1.2
    DEFAULT_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    @property
    def resolved_google_credentials_file(self) -> Path | None:
        """Trả về đường dẫn file Service Account JSON nếu được cấu hình và tồn tại trên đĩa."""
        raw_path = self.GOOGLE_CREDENTIALS_PATH.strip()
        if not raw_path:
            return None
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = BACKEND_DIR / candidate
        return candidate if candidate.is_file() else None

    @property
    def has_vertex_credentials(self) -> bool:
        """Kiểm tra có sẵn file Service Account JSON cho Vertex AI / Agent Platform Postpay."""
        return self.resolved_google_credentials_file is not None

    @property
    def has_gemini_key(self) -> bool:
        """Kiểm tra đã cấu hình GEMINI_API_KEY hợp lệ (khác rỗng và khác giá trị mẫu)."""
        cleaned = self.GEMINI_API_KEY.strip()
        return bool(cleaned and cleaned != "your-gemini-api-key-here")

    @property
    def has_gemini_auth(self) -> bool:
        """Kiểm tra có thể xác thực Gemini qua Service Account Postpay hoặc GEMINI_API_KEY."""
        return self.has_vertex_credentials or self.has_gemini_key

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
