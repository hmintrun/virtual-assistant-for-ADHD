# Schema/DTO cho phân hệ Giọng nói (STT /api/v1/voice/transcriptions, TTS /tts, Check-in Prompt /uf5-checkin-prompt)
from typing import Literal

from pydantic import Field

from app.schemas.common import StrictSchema
from app.schemas.schedule import UF5CheckInPromptData


class TranscriptionData(StrictSchema):
    """Dòng 8 trên API Sheet: Dữ liệu trả về sau khi nhận diện giọng nói chủ động gửi lên."""

    text: str = Field(min_length=1)
    input_source: Literal["voice", "thought"]
    thought_id: str | None = None


class TtsSynthesizeRequest(StrictSchema):
    """Yêu cầu tổng hợp giọng nói tiếng Việt qua Edge-TTS."""

    text: str = Field(min_length=1, max_length=2000)
    voice: str | None = Field(default=None, min_length=1, max_length=80)


class TtsSynthesisData(StrictSchema):
    """Dữ liệu âm thanh trả về từ Edge-TTS hoặc FakeTtsProvider."""

    audio_base64: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    cached: bool = False
    duration_ms: int = Field(ge=0)


class UF5VoiceCheckInPromptRequest(StrictSchema):
    """Yêu cầu lấy lời nhắc Voice Check-in 2 chiều cho cuối phiên (UF5)."""

    kind: Literal["mid", "final"] = "final"
    is_final_session_of_step: bool = False
    expected_output: str | None = Field(default=None, max_length=500)
    session_outcome: str | None = Field(default=None, max_length=64)
    include_audio: bool = True
    voice: str | None = Field(default=None, min_length=1, max_length=80)


class UF5VoiceCheckInPromptResponse(StrictSchema):
    """Dữ liệu câu hỏi Check-in 2 chiều + 3 chip Bỏ qua + audio TTS (fallback on-device nếu cần)."""

    prompt: UF5CheckInPromptData
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    tts_provider: str | None = None
    fallback_to_device_tts: bool = False

