# Router cho API chuyển giọng nói thành chữ (Dòng 8 trên API Sheet: POST /api/v1/voice/transcriptions)
import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, UploadFile

from app.api.deps import CurrentUserDep
from app.core.errors import AppError
from app.schemas.common import FieldError, SuccessEnvelope, build_response_meta
from app.schemas.voice import TranscriptionData

router = APIRouter(prefix="/voice", tags=["voice"])

# Lưu biên nhận Idempotency-Key tạm thời cho lệnh chuyển âm: (user_id, idempotency_key) -> (fingerprint, TranscriptionData)
_VOICE_IDEMPOTENCY_STORE: dict[tuple[str, str], tuple[str, TranscriptionData]] = {}


@router.post("/transcriptions", response_model=SuccessEnvelope[TranscriptionData])
async def create_transcription(
    purpose: Annotated[
        Literal["uf1_input", "thought_to_draft"],
        Form(description="Mục đích chuyển giọng nói: uf1_input hoặc thought_to_draft"),
    ],
    device_id: Annotated[
        str,
        Form(min_length=1, description="Mã thiết bị gửi file ghi âm (ví dụ: device_1)"),
    ],
    file: Annotated[
        UploadFile,
        File(description="File âm thanh người dùng chủ động gửi lên (.m4a, .wav)"),
    ],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            description="Mã chống gửi lặp cho lệnh chuyển giọng nói (ví dụ: lenh_8_1)",
        ),
    ],
    current_user: CurrentUserDep,
    thought_id: Annotated[
        str | None,
        Form(
            min_length=1,
            description="Mã ghi chú suy nghĩ (bắt buộc khi purpose='thought_to_draft', bỏ trống khi purpose='uf1_input')",
        ),
    ] = None,
) -> SuccessEnvelope[TranscriptionData]:
    """Dòng 8 trên API Sheet: Nhận multipart audio do người dùng chủ động gửi, trả về text và nguồn nhận dạng."""
    if purpose == "thought_to_draft" and not thought_id:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="thought_id là bắt buộc khi purpose='thought_to_draft'.",
            field_errors=[
                FieldError(
                    field_path="thought_id",
                    code="THOUGHT_ID_REQUIRED",
                    message="Bắt buộc cung cấp thought_id khi chuyển Thought sang Draft.",
                )
            ],
            next_action="fix_fields",
        )

    audio_bytes = await file.read()
    if not audio_bytes:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Chưa gửi file âm thanh hợp lệ để chuyển thành chữ.",
            field_errors=[
                FieldError(
                    field_path="file",
                    code="EMPTY_AUDIO_FILE",
                    message="File âm thanh không được rỗng.",
                )
            ],
            next_action="fix_fields",
        )

    fingerprint = json.dumps(
        {
            "purpose": purpose,
            "device_id": device_id,
            "thought_id": thought_id,
            "filename": file.filename or "",
            "size": len(audio_bytes),
        },
        sort_keys=True,
    )
    idem_record = _VOICE_IDEMPOTENCY_STORE.get((current_user.user_id, idempotency_key))
    if idem_record is not None:
        saved_fingerprint, saved_data = idem_record
        if saved_fingerprint != fingerprint:
            raise AppError(
                status_code=409,
                code="IDEMPOTENCY_KEY_REUSED",
                message=(
                    "Mã lệnh đã được dùng với nội dung khác. "
                    "Giữ mã cũ khi retry đúng nội dung; tạo mã mới cho ý định mới."
                ),
                next_action="use_original_payload_or_new_command",
            )
        return SuccessEnvelope(
            data=saved_data,
            meta=build_response_meta(),
        )

    # TODO(Voice - V01): Khi Role Voice bàn giao voice_service.py, gọi hàm STT tại đây.
    # Hiện giữ nguyên voice_service.py của Role Voice để tránh xung đột Git.
    data = TranscriptionData(
        text="Viết bản nháp báo cáo môn học",
        input_source="thought" if purpose == "thought_to_draft" else "voice",
        thought_id=thought_id if purpose == "thought_to_draft" else None,
    )
    _VOICE_IDEMPOTENCY_STORE[(current_user.user_id, idempotency_key)] = (
        fingerprint,
        data,
    )
    return SuccessEnvelope(
        data=data,
        meta=build_response_meta(),
    )

