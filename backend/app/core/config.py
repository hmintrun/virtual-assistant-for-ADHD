"""Cấu hình hệ thống và đọc biến môi trường qua Pydantic Settings."""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE_PATH = BACKEND_DIR / ".env"
DEFAULT_SECRET_KEY = "change-this-to-a-super-secret-jwt-key"


class Settings(BaseSettings):
    """Cấu hình trung tâm cho Backend ProPlan VA (Pydantic v2 Settings)."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Cấu hình chung của ứng dụng
    ENVIRONMENT: Literal["development", "staging", "production", "testing"] = "development"
    PROJECT_NAME: str = "ProPlan VA"
    API_V1_STR: str = "/api/v1"
    CORS_ALLOWED_ORIGINS: list[str] | None = None
    SECRET_KEY: str = Field(
        default=DEFAULT_SECRET_KEY,
        repr=False,
        description="Khóa tương thích dùng để dẫn xuất khóa Calendar cục bộ.",
    )

    # App authentication is separate from Calendar OAuth/credential encryption.
    AUTH_MODE: Literal["dev_user", "google"] = "dev_user"
    JWT_SECRET_KEY: str = Field(default="", repr=False)
    JWT_ISSUER: str = "proplan-va"
    JWT_AUDIENCE: str = "proplan-va-api"
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=3600, ge=60, le=86400)
    REFRESH_TOKEN_TTL_SECONDS: int = Field(default=30 * 86400, ge=300, le=90 * 86400)
    AUTH_CLOCK_SKEW_SECONDS: int = Field(default=30, ge=0, le=300)
    GOOGLE_SIGN_IN_AUDIENCES: list[str] = []
    GOOGLE_AUTH_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0, le=30)

    # Cơ sở dữ liệu (SQLite cho Dev, PostgreSQL cho Production)
    DATABASE_URL: str = "sqlite+aiosqlite:///./proplan.db"

    # Khóa API & Chứng thực Cloud cho AI/Voice (ẩn khỏi repr/log theo quy tắc bảo mật O00-O26)
    GEMINI_API_KEY: str = Field(default="", repr=False)
    GROQ_API_KEY: str = Field(default="", repr=False)
    GOOGLE_CREDENTIALS_PATH: str = Field(
        default="",
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
    GEMINI_UF1_MODEL: str = "gemini-3.5-flash-lite"
    GROQ_FALLBACK_MODEL: str = "qwen/qwen3.8-27b"
    GROQ_SECONDARY_FALLBACK_MODEL: str = "openai/gpt-oss-20b"
    GROQ_STT_MODEL: str = "whisper-large-v3-turbo"

    # Tích hợp Google Calendar (A22.1 — O23 CalendarConnection & Event Ingestion)
    # Mode "fake": chạy mock adapter hoàn toàn offline, an toàn cho dev/CI/unit test.
    # Mode "real": gọi live Google Calendar API v3 qua httpx bằng token/credentials.
    CALENDAR_PROVIDER_MODE: Literal["real", "fake"] = "fake"
    GOOGLE_OAUTH_CLIENT_ID: str = Field(
        default="",
        repr=False,
        description="OAuth 2.0 Client ID đăng ký trên Google Cloud Console.",
    )
    GOOGLE_OAUTH_CLIENT_SECRET: str = Field(
        default="",
        repr=False,
        description="OAuth 2.0 Client Secret; ẩn hoàn toàn khỏi log/repr để bảo mật.",
    )
    GOOGLE_OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/v1/calendar/oauth/callback"
    CALENDAR_CREDENTIAL_SECRET_KEY: str = Field(
        default="",
        repr=False,
        description="Fernet 32-byte base64 encryption key dùng cho credential_ref.",
    )
    GOOGLE_CALENDAR_DEFAULT_SCOPES: list[str] = [
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/calendar",
    ]
    ENABLE_CALENDAR_SYNC_WORKER: bool = True
    TESTING: bool = False

    # Voice STT (V01 & A21) — hỗ trợ Gemini Transcribe và Groq Whisper (whisper-large-v3-turbo).
    # Dự phòng khi Backend lỗi/mất mạng nằm ở Mobile (speech_to_text on-device).
    VOICE_STT_PROVIDER: Literal["gemini", "groq", "auto", "fake"] = "groq"
    GEMINI_STT_MODEL: str = "gemini-3.5-transcribe"
    GEMINI_STT_MODE: Literal["smart", "verbatim"] = "smart"
    VOICE_STT_LANGUAGE: str = "vi-VN"
    # Đo thực tế ~8-9s/clip ngắn (gồm ~2,5s upload) nên timeout rộng hơn luồng LLM.
    VOICE_STT_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0.0, le=120.0)
    VOICE_MAX_AUDIO_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)

    # Voice TTS (W3-09) — ưu tiên Edge-TTS trên Backend, fallback sang flutter_tts on-device.
    VOICE_TTS_PROVIDER: Literal["edge_tts", "fake"] = "edge_tts"
    VOICE_TTS_DEFAULT_VOICE: str = "vi-VN-HoaiMyNeural"
    VOICE_TTS_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.0, le=60.0)
    VOICE_TTS_MAX_CHARS: int = Field(default=600, gt=0, le=2000)
    VOICE_TTS_CACHE_SIZE: int = Field(default=64, ge=0, le=512)

    # UF1: Flash-Lite MINIMAL từ đầu; chuyển Qwen khi lỗi trong ngưỡng còn đủ thời gian dưới trần.
    AI_UF1_TIMEOUT_SECONDS: float = Field(default=6.0, gt=0.0, le=15.0)
    AI_UF1_GROQ_FAST_FAIL_SECONDS: float = Field(default=3.5, ge=0.0, le=15.0)

    # Khung thời gian A — Chỉnh kế hoạch bằng ngôn ngữ tự nhiên và UF6:
    # 0.0s Primary -> 1.5s Warm Backup -> 2.2s Groq -> ưu tiên Primary tới 3.0s -> cắt 4.5s.
    AI_TIMEOUT_SECONDS: float = Field(default=4.5, gt=0.0, le=20.0)
    AI_PRIMARY_PRIORITY_SECONDS: float = Field(default=3.0, gt=0.0, le=20.0)
    AI_WARM_BACKUP_DELAY_SECONDS: float = Field(default=1.5, ge=0.0, le=20.0)
    AI_GROQ_HEDGE_DELAY_SECONDS: float = Field(default=2.2, ge=0.0, le=20.0)

    # Khung thời gian B — Luồng chia nhỏ công việc (UF2 - có màn hình Breathing Circle):
    AI_UF2_TIMEOUT_SECONDS: float = Field(default=9.0, gt=0.0, le=25.0)
    AI_UF2_PRIMARY_PRIORITY_SECONDS: float = Field(default=4.5, gt=0.0, le=25.0)
    AI_UF2_WARM_BACKUP_DELAY_SECONDS: float = Field(default=2.0, ge=0.0, le=25.0)
    AI_UF2_GROQ_HEDGE_DELAY_SECONDS: float = Field(default=5.0, ge=0.0, le=25.0)

    # Cấu hình mặc định của Trợ lý ở Giai đoạn Lập kế hoạch Tuần 1 (O01 UserPreference)
    DEFAULT_WORK_INTERVAL_SECONDS: int = 1500  # 25 phút tập trung
    DEFAULT_REST_INTERVAL_SECONDS: int = 300  # 5 phút nghỉ ngắn
    DEFAULT_MICRO_TASK_SECONDS: int = 120  # 2 phút khởi động phá vỡ tê liệt ADHD
    PLANNING_REVIEW_LEAD_SECONDS: int = Field(default=300, ge=120)
    DEFAULT_MIN_INTER_SESSION_GAP_SECONDS: int = 900  # Khoảng cách tối thiểu 15 phút giữa 2 phiên
    DEFAULT_BUFFER_MULTIPLIER: float = 1.2
    DEFAULT_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    @field_validator("CORS_ALLOWED_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, origins: list[str] | None) -> list[str] | None:
        for origin in origins or []:
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError:
                raise ValueError("CORS_ALLOWED_ORIGINS cần các HTTP(S) origin hợp lệ") from None
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or origin != f"{parsed.scheme}://{parsed.netloc}"
                or port == 0
                or not origin.isprintable()
                or any(char.isspace() or char in "*\\%" for char in origin)
            ):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS chỉ nhận origin HTTP(S) đầy đủ, không wildcard"
                )
        return origins

    @property
    def is_deployed(self) -> bool:
        return self.ENVIRONMENT in {"staging", "production"}

    @property
    def browser_origins(self) -> list[str]:
        if self.CORS_ALLOWED_ORIGINS is not None:
            return self.CORS_ALLOWED_ORIGINS
        if self.is_deployed:
            return []
        return ["http://localhost:8080", "http://127.0.0.1:8080"]

    def validate_secret_configuration(self) -> None:
        if not self.is_deployed:
            return
        key = self.SECRET_KEY.strip()
        if len(key.encode()) < 32 or key == DEFAULT_SECRET_KEY:
            raise ValueError("Staging/production yêu cầu SECRET_KEY riêng, tối thiểu 32 byte")
        self.calendar_credential_key()

    def calendar_credential_key(self) -> bytes | None:
        """Trả khóa Fernet tường minh, hoặc cho phép khóa dẫn xuất ở dev/test."""
        value = self.CALENDAR_CREDENTIAL_SECRET_KEY.strip()
        if value:
            try:
                key = value.encode("ascii")
                raw = base64.b64decode(key, altchars=b"-_", validate=True)
                if len(raw) == 32 and base64.urlsafe_b64encode(raw) == key:
                    return key
            except ValueError:
                pass
        if self.is_deployed:
            raise ValueError(
                "Staging/production yêu cầu CALENDAR_CREDENTIAL_SECRET_KEY hợp lệ (Fernet)"
            )
        return None

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

    @property
    def has_google_oauth_credentials(self) -> bool:
        """Kiểm tra đã cấu hình đủ Client ID và Client Secret cho Google OAuth 2.0."""
        client_id = self.GOOGLE_OAUTH_CLIENT_ID.strip()
        client_secret = self.GOOGLE_OAUTH_CLIENT_SECRET.strip()
        return bool(
            client_id
            and client_id != "your-google-oauth-client-id-here.apps.googleusercontent.com"
            and client_secret
            and client_secret != "your-google-oauth-client-secret-here"
        )

    @property
    def is_calendar_real_mode(self) -> bool:
        """Kiểm tra hệ thống có đang bật mode gọi Google Calendar thật hay không."""
        return self.CALENDAR_PROVIDER_MODE == "real" and self.has_google_oauth_credentials


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Trả về singleton Settings được cache trong bộ nhớ."""
    config = Settings()
    config.validate_secret_configuration()
    return config


settings = get_settings()
