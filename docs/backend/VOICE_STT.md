# Voice STT (V01 & A21) — Quyết định & Hướng dẫn tích hợp

> Role Voice · Quang Khải (`kai`) · 01/11/2026
> Endpoint: `POST /api/v1/voice/transcriptions` (Dòng 8 trên API Sheet)

## 1. Quyết định

| Tầng | Công cụ | Dùng cho |
|---|---|---|
| Chính (Backend) | **Gemini 3.5 Transcribe**, chế độ `smart`, `vi-VN` | V01 (UF1 nói để nhập việc) & A21 (chép bản ghi UF4 **sau phiên**) |
| Dự phòng (Mobile) | `speech_to_text` on-device | **Chỉ V01** — nghe mic trực tiếp, không đọc được file |
| Cuối cùng | Ô nhập chữ | Mọi trường hợp còn lại |

⚠️ **Lệch Project.md §4.1 / §8 mục 5** (đang ghi Groq Whisper là STT chính) → cần BA cập nhật. Nhà cung cấp là tham số cấu hình (`VOICE_STT_PROVIDER`) nên đổi lại không cần sửa code.

## 2. Căn cứ (khảo sát 5 clip tiếng Việt, 01/11/2026)

| | Groq `whisper-large-v3-turbo` | Groq `whisper-large-v3` | Gemini Transcribe `smart` |
|---|---|---|---|
| Ngày/giờ/thực thể | Sai ("tuần sau" → "tháng sau", "15/11" → "2015-11") | Đúng ở clip sạch, sai ở clip ồn | **Đúng hết, kể cả clip ồn** |
| Từ hay gặp ("slide", "ôn thi") | Sai ("slime", "ông thi") | Sai | **Đúng** |
| Clip im lặng | **Bịa câu** ("Hãy subscribe…") | Bịa câu (lọc được bằng `no_speech_prob`) | **Trả rỗng** |
| Độ trễ | ~0,5s | ~0,5s | **~8s** (~2,5s upload + chép) |

`speech_to_text` trên Redmi (HyperOS 3, Android 16): **không nhận được chữ nào** — Gboard cũng lỗi y hệt, app Ghi âm vẫn thu rõ → lỗi đường thu `VOICE_RECOGNITION` của máy, **không sửa được từ app**. → On-device chỉ làm dự phòng, UI luôn có lối gõ chữ.

## 3. Backend

- `app/services/voice_service.py` — adapter: `VoiceService.transcribe(audio, content_type, filename) -> SttResult`, ném `SttError(code)`.
  - Providers: `GeminiSttProvider` (thật), `FakeSttProvider` (dev/test, không gọi mạng).
  - Upload Files API → `interactions.create` → **xóa file ngay cả khi lỗi** (quy tắc riêng tư).
  - **Không log audio/transcript** — log chỉ có provider, mã lỗi, thời gian.
- `app/api/v1/voice.py` — gọi service tại chỗ `TODO(Voice - V01)` cũ, đổi `SttError` → Error Envelope.
- Cấu hình (`app/core/config.py`, `.env.example`): `VOICE_STT_PROVIDER` (`gemini`|`fake`), `GEMINI_STT_MODEL`, `GEMINI_STT_MODE` (`smart`|`verbatim`), `VOICE_STT_LANGUAGE`, `VOICE_STT_TIMEOUT_SECONDS` (mặc định 20), `VOICE_MAX_AUDIO_BYTES` (10 MB). Dùng chung `GEMINI_API_KEY` (AI Studio). Chưa hỗ trợ đường Vertex/`credentials.json`.
- Test: `tests/test_voice_service.py` (27 ca).

## 4. Bảng lỗi cho Mobile

| HTTP | `error.code` | `retryable` | `next_action` | Mobile nên |
|---|---|---|---|---|
| 422 | `NO_SPEECH_DETECTED` | false | `record_again_or_type` | Mời nói lại / gõ chữ (không chuyển on-device) |
| 422 | `UNSUPPORTED_AUDIO` | false | `record_again_or_type` | Ghi lại / gõ chữ |
| 413 | `AUDIO_TOO_LARGE` | false | `record_again_or_type` | Nhắc nói ngắn hơn |
| 503 | `STT_UNAVAILABLE` | false | `use_on_device_stt_or_type` | V01: chuyển `speech_to_text`; A21: gõ tay / thử sau |
| 503 | `STT_RATE_LIMITED` | true | `use_on_device_stt_or_type` | như trên |
| 504 | `STT_TIMEOUT` | true | `use_on_device_stt_or_type` | như trên |
| 503 | `STT_PROVIDER_ERROR` | true | `use_on_device_stt_or_type` | như trên |

Mất mạng (không tới được Backend) → xử lý như `use_on_device_stt_or_type`.

## 5. Mobile

- `mobile/lib/services/voice_service.dart` — `OnDeviceVoiceService.listenOnce({onPartial, requireOnDevice})` trả 1 câu, ném `OnDeviceSttException(code)`.
- `pauseFor = 8s`: recognizer Android tự dừng nếu chưa nghe tiếng trong khoảng chờ; 3s gây lỗi liên tục với người hay ngập ngừng.
- Cần thêm vào `AndroidManifest.xml` khi tạo `android/` (xem comment đầu file) — `RecognitionService` phải là một `<intent>` **riêng** trong `<queries>`.

## 6. Việc còn mở (cần nhóm chốt)

1. **BA:** cập nhật Project.md/AGENTS.md (Gemini thay Groq Whisper cho STT).
2. **BA:** chế độ `smart` trả bản **đã biên tập** (bỏ "ờ, à", gộp chỗ tự sửa lời). Doc UF1 yêu cầu "giữ bản gốc để sửa lỗi nhận dạng" → chấp nhận bản smart làm bản gốc, hay cần lưu thêm verbatim?
3. **UX/Mobile:** ~8s chờ ở V01 → cần hiệu ứng chờ dịu (Shimmer/Breathing) + thông điệp rõ.
4. **API Sheet:** đối chiếu các `error.code` / `next_action` ở mục 4 với Sheet.
5. Đo lại độ trễ Gemini với bộ test lớn hơn; thử gửi bytes trực tiếp (bỏ bước upload).
