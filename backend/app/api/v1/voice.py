# Router cho API chuyển giọng nói thành chữ (Dòng 8 trên API Sheet: POST /api/v1/voice/transcriptions)
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, UploadFile

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.config import settings
from app.core.errors import AppError
from app.repositories import run_repository, thought_repository
from app.schemas.common import FieldError, SuccessEnvelope, build_response_meta
from app.schemas.voice import TranscriptionData
from app.services import command_service
from app.services.draft_service import resolve_user_uuid
from app.services.voice_service import SttError, SttErrorCode, get_voice_service

router = APIRouter(prefix="/voice", tags=["voice"])

# Bảng đổi mã lỗi STT -> (HTTP status, code, message, next_action) theo Error Envelope của Sheet.
# next_action gợi ý cho Mobile: ghi âm lại / gõ tay / chuyển sang STT on-device (speech_to_text).
_STT_ERROR_MAP: dict[SttErrorCode, tuple[int, str, str, str]] = {
    SttErrorCode.NO_SPEECH: (
        422,
        "NO_SPEECH_DETECTED",
        "Chưa nghe rõ lời nói trong bản ghi. Bạn thử nói lại gần micro hơn hoặc gõ chữ nhé.",
        "record_again_or_type",
    ),
    SttErrorCode.UNSUPPORTED_AUDIO: (
        422,
        "UNSUPPORTED_AUDIO",
        "File âm thanh không đọc được. Bạn thử ghi âm lại hoặc gõ chữ nhé.",
        "record_again_or_type",
    ),
    SttErrorCode.AUDIO_TOO_LARGE: (
        413,
        "AUDIO_TOO_LARGE",
        "Bản ghi quá dài. Bạn thử nói ngắn gọn hơn hoặc gõ chữ nhé.",
        "record_again_or_type",
    ),
    SttErrorCode.PROVIDER_UNAVAILABLE: (
        503,
        "STT_UNAVAILABLE",
        "Dịch vụ nhận giọng nói tạm thời chưa sẵn sàng. Bạn có thể nói lại hoặc gõ chữ.",
        "use_on_device_stt_or_type",
    ),
    SttErrorCode.RATE_LIMITED: (
        503,
        "STT_RATE_LIMITED",
        "Dịch vụ nhận giọng nói đang bận. Bạn có thể nói lại hoặc gõ chữ.",
        "use_on_device_stt_or_type",
    ),
    SttErrorCode.TIMEOUT: (
        504,
        "STT_TIMEOUT",
        "Nhận giọng nói mất nhiều thời gian hơn dự kiến. Bạn có thể nói lại hoặc gõ chữ.",
        "use_on_device_stt_or_type",
    ),
    SttErrorCode.PROVIDER_ERROR: (
        503,
        "STT_PROVIDER_ERROR",
        "Dịch vụ nhận giọng nói gặp sự cố. Bạn có thể nói lại hoặc gõ chữ.",
        "use_on_device_stt_or_type",
    ),
}


def _stt_error_to_app_error(exc: SttError) -> AppError:
    """Đổi lỗi STT sang AppError (không đưa nội dung audio/transcript vào thông báo)."""
    status_code, code, message, next_action = _STT_ERROR_MAP[exc.code]
    return AppError(
        status_code=status_code,
        code=code,
        message=message,
        next_action=next_action,
        retryable=exc.retryable,
    )


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
    db: DbSessionDep,
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

    async def check_thought():
        if purpose != "thought_to_draft":
            return
        try:
            thought_uuid = UUID(thought_id)
        except (TypeError, ValueError):
            raise AppError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                message="Không tìm thấy Thought.",
                next_action="reload",
            ) from None
        owner = resolve_user_uuid(current_user.user_id)
        thought = await thought_repository.get_thought(db, owner, thought_uuid)
        if thought is None:
            raise AppError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                message="Không tìm thấy Thought.",
                next_action="reload",
            )
        if thought.source_run_id is not None:
            run = await run_repository.get_run(db, owner, thought.source_run_id)
            if run is None:
                raise AppError(
                    status_code=404,
                    code="RESOURCE_NOT_FOUND",
                    message="Không tìm thấy phiên nguồn.",
                    next_action="reload",
                )
            if run.ended_at is None:
                raise AppError(
                    status_code=409,
                    code="SESSION_STILL_RUNNING",
                    message="Chỉ chuyển chữ sau khi phiên dừng.",
                    next_action="wait_for_session_end",
                )

    await check_thought()
    # Đóng transaction đọc trước khi chờ provider.
    if db.in_transaction():
        await db.rollback()
    audio_bytes = await file.read(settings.VOICE_MAX_AUDIO_BYTES + 1)
    if len(audio_bytes) > settings.VOICE_MAX_AUDIO_BYTES:
        raise _stt_error_to_app_error(SttError(SttErrorCode.AUDIO_TOO_LARGE))
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
            "content_type": file.content_type,
            "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
        },
        sort_keys=True,
    )
    digest = command_service.command_hash(
        "voice_transcription", thought_id or "", json.loads(fingerprint)
    )
    saved = await command_service.replay(db, current_user.user_id, idempotency_key, digest)
    if saved is not None:
        return SuccessEnvelope(
            data=TranscriptionData.model_validate(saved), meta=build_response_meta()
        )
    await db.rollback()
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

    # V01/A21 (Role Voice): chuyển audio thành chữ qua voice_service; lỗi đổi sang Error Envelope.
    try:
        stt_result = await get_voice_service().transcribe(
            audio_bytes,
            content_type=file.content_type,
            filename=file.filename,
        )
    except SttError as exc:
        raise _stt_error_to_app_error(exc) from exc

    await check_thought()
    data = TranscriptionData(
        text=stt_result.text,
        input_source="thought" if purpose == "thought_to_draft" else "voice",
        thought_id=thought_id if purpose == "thought_to_draft" else None,
    )
    await command_service.lock_owner(db, current_user.user_id)
    saved = await command_service.replay(db, current_user.user_id, idempotency_key, digest)
    if saved is not None:
        await db.rollback()
        return SuccessEnvelope(
            data=TranscriptionData.model_validate(saved), meta=build_response_meta()
        )
    command_service.record(
        db, current_user.user_id, idempotency_key, digest, "voice_transcription", data
    )
    await db.commit()
    _VOICE_IDEMPOTENCY_STORE[(current_user.user_id, idempotency_key)] = (
        fingerprint,
        data,
    )
    return SuccessEnvelope(
        data=data,
        meta=build_response_meta(),
    )
