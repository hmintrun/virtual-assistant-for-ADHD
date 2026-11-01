# Schema/DTO cho kết quả chuyển giọng nói thành chữ (Dòng 8 trên API Sheet: POST /api/v1/voice/transcriptions)
from typing import Literal

from pydantic import Field

from app.schemas.common import StrictSchema


class TranscriptionData(StrictSchema):
    """Dòng 8 trên API Sheet: Dữ liệu trả về sau khi nhận diện giọng nói chủ động gửi lên."""

    text: str = Field(min_length=1)
    input_source: Literal["voice", "thought"]
    thought_id: str | None = None

