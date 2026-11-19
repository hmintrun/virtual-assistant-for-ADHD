# Router cho API giọng nói (STT POST /voice/transcriptions, TTS POST /voice/tts, Prompt UF5 POST /voice/uf5-checkin-prompt)
import asyncio
import base64
from collections import OrderedDict
import hashlib
import json
import time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, UploadFile

from app.api.deps import CurrentUserDep, DbSessionDep
from app.core.config import settings
from app.core.errors import AppError
from app.repositories import run_repository, thought_repository
from app.schemas.common import ErrorEnvelope, FieldError, SuccessEnvelope, build_response_meta
from app.schemas.voice import (
    TranscriptionData,
    TtsSynthesisData,
    TtsSynthesizeRequest,
    UF5VoiceCheckInPromptRequest,
    UF5VoiceCheckInPromptResponse,
)
from app.services import command_service
from app.services.ai_service import ai_service
from app.services.draft_service import resolve_user_uuid
from app.services.thought_service import ensure_thought_eligible_for_draft_conversion
from app.services.voice_service import (
    SttError,
    SttErrorCode,
    TtsError,
    TtsErrorCode,
    get_tts_service,
    get_voice_service,
)

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


_TTS_ERROR_MAP: dict[TtsErrorCode, tuple[int, str, str, str]] = {
    TtsErrorCode.INVALID_INPUT: (
        422,
        "TTS_INVALID_INPUT",
        "Văn bản đọc không được để trống.",
        "use_on_device_tts",
    ),
    TtsErrorCode.TEXT_TOO_LONG: (
        422,
        "TTS_TEXT_TOO_LONG",
        "Đoạn văn bản quá dài để tổng hợp giọng nói một lượt.",
        "use_on_device_tts",
    ),
    TtsErrorCode.EMPTY_AUDIO: (
        503,
        "TTS_EMPTY_AUDIO",
        "Không tạo được âm thanh lúc này. Ứng dụng sẽ dùng giọng đọc trên máy.",
        "use_on_device_tts",
    ),
    TtsErrorCode.PROVIDER_UNAVAILABLE: (
        503,
        "TTS_UNAVAILABLE",
        "Dịch vụ đọc giọng nói tạm chưa sẵn sàng. Ứng dụng sẽ dùng giọng đọc trên máy.",
        "use_on_device_tts",
    ),
    TtsErrorCode.TIMEOUT: (
        504,
        "TTS_TIMEOUT",
        "Tổng hợp giọng nói mất nhiều thời gian hơn dự kiến.",
        "use_on_device_tts",
    ),
    TtsErrorCode.PROVIDER_ERROR: (
        503,
        "TTS_PROVIDER_ERROR",
        "Dịch vụ đọc giọng nói gặp sự cố. Ứng dụng sẽ dùng giọng đọc trên máy.",
        "use_on_device_tts",
    ),
}


def _tts_error_to_app_error(exc: TtsError) -> AppError:
    """Đổi lỗi TTS sang AppError (chỉ dẫn client chuyển sang flutter_tts trên thiết bị)."""
    status_code, code, message, next_action = _TTS_ERROR_MAP[exc.code]
    return AppError(
        status_code=status_code,
        code=code,
        message=message,
        next_action=next_action,
        retryable=exc.retryable,
    )


class VoiceIdempotencyCache:
    """Bộ đệm bộ nhớ lưu biên nhận Voice Idempotency có TTL và giới hạn kích thước (LRU)."""

    def __init__(self, max_entries: int = 512, ttl_seconds: float = 900.0) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._store: OrderedDict[tuple[str, str], tuple[float, str, TranscriptionData]] = (
            OrderedDict()
        )

    def _cleanup_expired(self, now_mono: float) -> None:
        expired_keys = [k for k, (exp, _, _) in self._store.items() if exp <= now_mono]
        for k in expired_keys:
            self._store.pop(k, None)

    def get(self, key: tuple[str, str], default=None) -> tuple[str, TranscriptionData] | None:
        now_mono = time.monotonic()
        self._cleanup_expired(now_mono)
        if key not in self._store:
            return default
        exp, fp, data = self._store[key]
        if exp <= now_mono:
            self._store.pop(key, None)
            return default
        self._store.move_to_end(key)
        return (fp, data)

    def __getitem__(self, key: tuple[str, str]) -> tuple[str, TranscriptionData]:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: tuple[str, str], value: tuple[str, TranscriptionData]) -> None:
        now_mono = time.monotonic()
        self._cleanup_expired(now_mono)
        fp, data = value
        self._store[key] = (now_mono + self._ttl_seconds, fp, data)
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        self._store.clear()

    def pop(self, key: tuple[str, str], default=None):
        now_mono = time.monotonic()
        self._cleanup_expired(now_mono)
        item = self._store.pop(key, None)
        if item is None:
            return default
        return (item[1], item[2])


# Lưu biên nhận Idempotency-Key tạm thời cho lệnh chuyển âm: (user_id, idempotency_key) -> (fingerprint, TranscriptionData)
_VOICE_IDEMPOTENCY_STORE = VoiceIdempotencyCache(max_entries=512, ttl_seconds=900.0)
_INFLIGHT_VOICE_LOCKS: dict[tuple[str, str], asyncio.Event] = {}


@router.post("/transcriptions", response_model=SuccessEnvelope[TranscriptionData])
async def create_transcription(
    purpose: Annotated[
        Literal["uf1_input", "thought_to_draft", "uf5_checkin"],
        Form(description="Mục đích chuyển giọng nói: uf1_input, thought_to_draft hoặc uf5_checkin"),
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

    # 1. REPLAY FIRST: kiểm tra DB và cache bộ nhớ TRƯỚC khi gọi guard nghiệp vụ
    saved = await command_service.replay(db, current_user.user_id, idempotency_key, digest)
    if saved is not None:
        return SuccessEnvelope(
            data=TranscriptionData.model_validate(saved), meta=build_response_meta()
        )
    if db.in_transaction():
        await db.rollback()

    flight_key = (current_user.user_id, idempotency_key)
    idem_record = _VOICE_IDEMPOTENCY_STORE.get(flight_key)
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

    # Single-flight: nếu đang có request khác cùng key đang gọi provider, chờ nó hoàn tất
    while flight_key in _INFLIGHT_VOICE_LOCKS:
        event = _INFLIGHT_VOICE_LOCKS[flight_key]
        await event.wait()
        saved = await command_service.replay(db, current_user.user_id, idempotency_key, digest)
        if saved is not None:
            return SuccessEnvelope(
                data=TranscriptionData.model_validate(saved), meta=build_response_meta()
            )
        if db.in_transaction():
            await db.rollback()
        idem_record = _VOICE_IDEMPOTENCY_STORE.get(flight_key)
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
        break

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
        await ensure_thought_eligible_for_draft_conversion(db, owner, thought_uuid)

    lock_event = asyncio.Event()
    _INFLIGHT_VOICE_LOCKS[flight_key] = lock_event
    try:
        await check_thought()
        # Đóng transaction đọc trước khi chờ provider.
        if db.in_transaction():
            await db.rollback()

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
        _VOICE_IDEMPOTENCY_STORE[flight_key] = (
            fingerprint,
            data,
        )
        return SuccessEnvelope(
            data=data,
            meta=build_response_meta(),
        )
    finally:
        lock_event.set()
        _INFLIGHT_VOICE_LOCKS.pop(flight_key, None)


@router.post(
    "/tts",
    response_model=SuccessEnvelope[TtsSynthesisData],
    summary="Tổng hợp giọng nói tiếng Việt qua Edge-TTS",
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập hoặc thiếu Bearer Token"},
        422: {"model": ErrorEnvelope, "description": "Văn bản TTS rỗng hoặc vượt độ dài cho phép"},
        503: {"model": ErrorEnvelope, "description": "Edge-TTS tạm lỗi, chuyển về flutter_tts on-device"},
        504: {"model": ErrorEnvelope, "description": "Edge-TTS quá thời gian chờ"},
    },
)
async def synthesize_speech(
    payload: TtsSynthesizeRequest,
    current_user: CurrentUserDep,
) -> SuccessEnvelope[TtsSynthesisData]:
    """Tổng hợp giọng nói tiếng Việt từ văn bản và trả về âm thanh mã hóa Base64."""
    _ = current_user
    try:
        result = await get_tts_service().synthesize(payload.text, voice=payload.voice)
    except TtsError as exc:
        raise _tts_error_to_app_error(exc) from exc

    return SuccessEnvelope(
        data=TtsSynthesisData(
            audio_base64=base64.b64encode(result.audio_bytes).decode("ascii"),
            mime_type=result.mime_type,
            provider=result.provider,
            voice=result.voice,
            cached=result.cached,
            duration_ms=result.latency_ms,
        ),
        meta=build_response_meta(),
    )


@router.post(
    "/uf5-checkin-prompt",
    response_model=SuccessEnvelope[UF5VoiceCheckInPromptResponse],
    summary="Sinh lời nhắc Voice Check-in 2 chiều + 3 chip Bỏ qua + audio TTS cho UF5",
    responses={
        401: {"model": ErrorEnvelope, "description": "Chưa đăng nhập hoặc thiếu Bearer Token"},
        422: {"model": ErrorEnvelope, "description": "Tham số ngữ cảnh check-in không hợp lệ"},
    },
)
async def create_uf5_checkin_prompt(
    payload: UF5VoiceCheckInPromptRequest,
    current_user: CurrentUserDep,
) -> SuccessEnvelope[UF5VoiceCheckInPromptResponse]:
    """Tạo câu hỏi Check-in 2 chiều không phán xét (UF5) kèm audio TTS hoặc cờ fallback on-device."""
    _ = current_user
    prompt = ai_service.build_uf5_checkin_prompt(
        kind=payload.kind,
        is_final_session_of_step=payload.is_final_session_of_step,
        expected_output=payload.expected_output,
        session_outcome=payload.session_outcome,
    )
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    tts_provider: str | None = None
    fallback_to_device_tts = False

    if payload.include_audio:
        try:
            tts_result = await get_tts_service().synthesize(
                prompt.prompt_text,
                voice=payload.voice,
            )
            audio_base64 = base64.b64encode(tts_result.audio_bytes).decode("ascii")
            audio_mime_type = tts_result.mime_type
            tts_provider = tts_result.provider
        except TtsError:
            fallback_to_device_tts = True

    return SuccessEnvelope(
        data=UF5VoiceCheckInPromptResponse(
            prompt=prompt,
            audio_base64=audio_base64,
            audio_mime_type=audio_mime_type,
            tts_provider=tts_provider,
            fallback_to_device_tts=fallback_to_device_tts,
        ),
        meta=build_response_meta(),
    )
