# Voice STT — Đo đạc & chọn phương án dưới 3 giây

> Người đo: Lê Quang Khải (kai) · Ngày: 17/11 và 21/11/2026 · Nhánh code: `feat/kai-stt-transcribe-inline`
> (tách từ `feat/upgrade-STT-and-AI-extract-in-UF1` của vqh).
> Script đo và audio nằm ở thư mục `speech-lab/`, **ngoài repo** (theo AGENTS.md §5.4: không commit file ghi âm).

## 1. Kết luận ngắn

| Vai trò | Lựa chọn | Thời gian | Đúng từ khoá (3 clip dài) |
|---|---|---|---|
| **STT chính (UF1, nháp → việc)** | `gemini-3.5-transcribe` gọi `models.generate_content` + **audio inline** + `audio_transcription_config` (verbatim) | **1,7 – 2,9 s** | **29/30** |
| Dự phòng | Groq **`whisper-large-v3`** + prompt của vqh (đổi từ `-turbo`) | ~1 – 1,3 s* | 18/28 |
| UF5 check-in (câu ngắn) | Giữ Groq đi trước (`VOICE_STT_UF5_PREFER_GROQ=true`) | < 1 s | — |

- **Chậm là do cách gọi API, không phải do model.** Cùng model Transcribe:
  Files API + `interactions` + xoá file ≈ **10 s** → `interactions` + inline ≈ **5 s** → `generate_content` + inline ≈ **2–2,9 s**.
- Thời gian **gần như không tăng theo độ dài câu** (câu 12 s hay 43 s đều < 3 s).
- Clip im lặng: Transcribe trả **rỗng** (Groq bịa "Hãy subscribe cho kênh…").
- **Groq turbo + prompt bịa cả câu** với clip 25 s (2/2 lần) và luôn trả `no_speech_prob=0` → đổi sang v3.

\* Ngày 21/11 tối Groq chậm bất thường (10–46 s/request cho mọi model), số ~1 s là từ lần đo trước cùng ngày.

## 2. Bộ dữ liệu

| Clip | Độ dài | Nội dung | Bẫy |
|---|---|---|---|
| c01–c04 | 4,5 – 7,9 s | Câu ngắn: slide, Figma, ôn thi, có ồn | Tên riêng, ngày giờ |
| c05 | 5,7 s | **Im lặng** | Ảo giác |
| c06 | 12,3 s | 1 việc đủ 4 trường UF1 | "Tư duy tính toán", giờ rảnh |
| c07 | 25,3 s | 3 việc, có tự sửa lời | Xác suất thống kê, Figma, ProPlan, Minh Trung, "thứ năm, à không, thứ sáu" |
| c08 | 43,0 s | Nói lan man kiểu xả suy nghĩ | AVL, Moodle, Atomic Habits, khám răng, "chiều mai", xao nhãng |

Bản gốc (ground truth) của từng clip ở `speech-lab/data/refs/*.txt`. "Đúng từ khoá" = đếm số từ khoá quan trọng
(tên riêng, môn học, ngày giờ) xuất hiện đúng trong kết quả — 8 + 8 + 12 = 28 từ khoá cho c06–c08 (+2 cho c02 ở bảng §3.3).

## 3. Kết quả

### 3.1. Cùng model Transcribe — 3 cách gọi (câu dài)

| Cách gọi | c06 (12 s) | c07 (25 s) | c08 (43 s) |
|---|---|---|---|
| Files API upload → `interactions.create` → xoá file (code cũ) | 11,1 s | 10,3 s | 10,4 s |
| `interactions.create` + inline | 5,1 s | 4,5 s | 5,0 s |
| **`models.generate_content` + inline, verbatim** | **1,7 – 2,0 s** | **2,5 – 2,9 s** | **2,6 s** |
| `models.generate_content` + inline, smart | — | 2,4 s | 2,8 s |

Câu ngắn (T1, 17/11, c01–c05): upload ≈ 7,6 s (+1,9 s xoá) → inline `interactions` ≈ 4,8 s. Chữ chép ra **giống hệt 5/5**.

### 3.2. Groq vs Gemini Transcribe trên câu dài

| Phương án | Thời gian | Đúng từ khoá | Lỗi điển hình |
|---|---|---|---|
| Groq `whisper-large-v3-turbo` | 0,4 – 0,9 s | 13/28 | "phít ma", "approblend", "bút đồ" (Moodle), "Automatic Habit", **"chị Mai"** (chiều mai) |
| Groq `whisper-large-v3` | 1,0 – 1,3 s | 17/28 | "sản xuất thống kê", "Apple plan", "khám trang", "sửa bức" |
| Groq v3 + prompt cũ (`_DEFAULT_STT_PROMPT` bản ngắn) | 0,9 – 2,8 s | 13/28 | c07 trả về **"Các bạn có thể nhớ like, share và đăng ký kênh…"** (mất trắng 25 s lời nói, `no_speech_prob`=0,001) |
| **Gemini Transcribe** | xem §3.1 | **28/28** | — |

### 3.3. Các model Gemini khác (generate_content + audio inline)

| Model | c02 / c05 | c06 / c07 / c08 | Đúng từ khoá (c02 + c06–c08) | Ghi chú |
|---|---|---|---|---|
| **gemini-3.5-transcribe** | 2,0 / 1,9 s | 1,7 / 2,9 / 2,6 s | **29/30** | c05 rỗng ✅ |
| gemini-3.1-flash-lite + prompt | 1,9 / 2,6 s | 1,5 / 2,3 / 2,5 s | 27/30 | Ứng viên dự phòng tầng 2 (quota riêng) |
| gemini-3.5-flash-lite + prompt (mặc định cũ của nhánh vqh) | 1,6 / 1,4 s | **8,9 / 10,7 / 10,2 s** | 25/30 | Sai Moodle, Atomic Habits |
| gemini-3.5-flash | 8 – 30 s | 2 lần timeout 504 | — | Loại |
| gemini-3.5-transcribe-live (streaming) | 4/5 lỗi `1008 aborted` | 44 s sau khi nhả nút | — | Loại; còn phải đổi mobile sang stream PCM |

### 3.4. Verbatim hay smart?

Tốc độ như nhau. Chọn **verbatim** làm mặc định vì:

- Smart **tự quyết thay người dùng**: "hạn là thứ năm, à không, thứ sáu" → "hạn là thứ Sáu"; tự chia gạch đầu dòng.
- Theo AGENTS.md §3, hiểu ngữ nghĩa và hỏi lại khi mâu thuẫn là việc của **LLM trích xuất UF1**, nên STT nên giữ nguyên lời.
- Đổi lại bằng `GEMINI_STT_MODE=smart` nếu nhóm muốn.

## 4. Thay đổi code trong nhánh này

| File | Thay đổi |
|---|---|
| `backend/app/services/voice_service.py` | Thêm transport `transcribe_inline` (`_transcribe_via_transcribe_inline`): 1 lần `generate_content`, audio `Part.from_bytes`, `AudioTranscriptionConfig(language_codes, mode)`, không prompt, không upload/xoá. `auto` + model có chữ `transcribe` → `transcribe_inline`. Audio > 15 MB vẫn quay về Files API. Thêm `_transcript_text_of` đọc cả part `text` lẫn `audio_transcription`. |
| `backend/app/core/config.py` | `GEMINI_STT_MODEL=gemini-3.5-transcribe`, `GEMINI_STT_MODE=verbatim`, thêm giá trị `transcribe_inline` cho `GEMINI_STT_TRANSPORT`, **`GROQ_STT_MODEL=whisper-large-v3`**. |
| `backend/.env.example` | Cập nhật ghi chú cấu hình. |
| `backend/tests/test_stt_gemini_transcribe_inline.py` | 6 test mới (mock SDK, offline). |
| `backend/tests/test_stt_gemini_flash_lite_inline.py`, `test_voice_service.py` | Cập nhật giá trị mặc định; test legacy giờ chọn `transport="transcribe_api"` tường minh. |

**Không đổi:** router `/api/v1/voice/transcriptions`, schema response, mobile, luồng fallback/purpose routing, prompt Groq và bộ lọc ảo giác Groq của vqh.

Quay lại cấu hình cũ chỉ cần `.env`:

```env
GEMINI_STT_MODEL=gemini-3.5-flash-lite
GEMINI_STT_MODE=smart
GROQ_STT_MODEL=whisper-large-v3-turbo
```

## 5. Giới hạn & việc còn lại

1. **Quota free tier Transcribe: 25 request/ngày.** Đủ demo, không đủ cho cả nhóm test liên tục → khi 429 sẽ tự rơi xuống Groq.
   Đề xuất tiếp: thêm `gemini-3.1-flash-lite` làm tầng 2 trước Groq (cần mở rộng `FallbackSttProvider` lên 3 tầng).
2. **Mẫu đo nhỏ** (1–2 lần/clip). Google có lúc chậm bất thường (từng thấy 16 s với flash-lite) → nên đo lặp 3–5 lần
   và cân nhắc giảm `VOICE_STT_PRIMARY_TIMEOUT_SECONDS` từ 8 s xuống ~5 s để rơi sang Groq sớm hơn.
3. **Lệch Project.md / AGENTS.md §3.3** (ghi Groq `whisper-large-v3-turbo` là STT ưu tiên). Cần nhóm đồng ý và cập nhật tài liệu.
4. UF5 check-in ưu tiên Groq: v3 chậm hơn turbo một chút với câu ngắn (~0,5 s vs ~0,4 s) — chấp nhận được, nhưng nên đo lại khi Groq ổn định.

## 6. Groq với prompt mới của nhánh vqh

Prompt đo: bản dài trong nhánh vqh (thêm Thứ 2…Chủ nhật, 19h30, rảnh/bận/trừ…). Mỗi clip 2 lần, c05–c08.

| Groq | Đúng từ khoá (c06–c08) | c07 (25 s) | c05 im lặng | `no_speech_prob` c05 |
|---|---|---|---|---|
| turbo, không prompt | 13/28 | đúng nội dung (sai tên riêng) | bịa | **0** |
| **turbo + prompt (mặc định cũ)** | 11/28 | ❌ **"Hãy subscribe cho kênh Ghiền Mì Gõ…" 2/2 lần** | bịa | **0** |
| v3, không prompt | 17/28 (lần đo trước) | đúng nội dung | bịa | 0,953 |
| **v3 + prompt (mặc định mới)** | **18/28** | ✅ đúng | bịa | **0,988** |

Nhận xét:

- Lỗi ảo giác cả câu là do **turbo + prompt**, không phải do prompt nói chung. Với v3, prompt mới còn giúp tăng độ đúng (17 → 18/28).
- Turbo **luôn** trả `no_speech_prob=0` → bộ lọc im lặng `no_speech_prob > 0.85` của vqh vô hiệu. v3 trả 0,95–0,99 trên clip im lặng
  → bộ lọc (kết hợp danh sách cụm "hãy subscribe", "đăng ký kênh") bắt được.
- Nếu turbo + prompt bịa "Hãy subscribe…" cho c07, bộ lọc cụm của vqh sẽ báo `NO_SPEECH` → người dùng nói 25 s mà **mất trắng**. Đổi sang v3 tránh được.
- Kết quả: `speech-lab/results/groq_prompt_v2.json`.

## 7. Cách chạy lại

```bash
cd speech-lab
.venv/bin/python -I run_gemini_inline.py smart   # T1: inline vs upload (câu ngắn)
.venv/bin/python -I run_long.py c06 c07 c08      # T2/T5: Groq turbo/v3/v3+prompt + Gemini inline/upload
.venv/bin/python -I run_flash_audio.py           # T6: các model Flash + Transcribe qua generate_content
.venv/bin/python -I run_live.py verbatim         # T7: streaming transcribe-live
.venv/bin/python -I run_gc_mode.py               # T8: generate_content smart vs verbatim
.venv/bin/python -I run_groq_prompt2.py          # T9: Groq với prompt mới
```

Kết quả JSON: `speech-lab/results/` (`gemini_inline_vs_upload_smart.json`, `long_compare.json`, `flash_audio.json`,
`live_verbatim.json`, `gc_mode.json`, `groq_prompt_v2.json`).
