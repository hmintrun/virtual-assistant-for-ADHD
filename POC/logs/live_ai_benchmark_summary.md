# Thống kê Kiểm thử API Thật Toàn bộ Luồng AI (POC Live Benchmark)

- **Đợt 1 (All-Lite + Groq Tier 2 + Voice + UF5):** `2026-11-16T11:14:32.042516+07:00` → `2026-11-16T11:33:23.997202+07:00` (`100` lượt LLM + `2` Voice TTS + `5` UF5 rule-based)
- **Đợt 2 (Bổ sung `gemini-3.8-flash` cho UF2/UF6 & Gộp Hybrid):** `2026-11-16T11:43:55.215966+07:00` → `2026-11-16T11:46:27.676141+07:00` (`32` lượt LLM)
- **Tổng số lượt chạy thật:** `132` lượt LLM (`28` testcases trải đều `UF1`, `UF2_Decompose`, `UF2_Adjust`, `UF6`) + `2` lượt `Edge-TTS` + `5` ca `UF5` rule-based.
- **Ngưỡng đối chiếu:** SLA mục tiêu `2.5s` | Trần timeout `UF1 = 4.5s`, `UF2_Decompose = 9.0s`, `UF2_Adjust = 4.5s`, `UF6 = 4.5s`.
- **File log chi tiết đi kèm:** [`live_ai_benchmark_trace.log`](./live_ai_benchmark_trace.log)

## 1. Bảng Tổng hợp Thời gian Theo Từng Tier / Chế độ

| Chế độ / Tier | Tổng ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min (s) | **Tối đa (s)** | > 2.5s | > Trần UF | Lỗi 429 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `production (All 3.5-Flash-Lite)` | 28 | 28 | 28 | 1.792 | 1.794 | 0.785 | **2.916** | 3 | 0 | 0 |
| `production_hybrid (UF1=3.5-Lite; UF2/UF6=3.8-Flash+Backup)` | 28 | 28 | 28 | 2.811 | 2.379 | 1.774 | **4.784** | 13 | 0 | 0 |
| `tier_1_gemini (3.5-Flash-Lite đơn lẻ, trần 35s)` | 28 | 28 | 28 | 1.839 | 1.919 | 0.966 | **3.013** | 2 | 0 | 0 |
| `tier_1_hybrid_primary (UF1=3.5-Lite; UF2/UF6=3.8-Flash, 35s)` | 28 | 27 | 27 | 3.064 | 2.847 | 1.694 | **6.535** | 17 | 1 | 0 |
| `tier_1_flash38_only (3.8-Flash riêng UF2/UF6, trần 35s)` | 16 | 15 | 15 | 3.744 | 3.176 | 2.326 | **6.535** | 15 | 1 | 0 |
| `tier_1_backup_flash_lite_uf26 (Warm Backup UF2/UF6)` | 16 | 16 | 16 | 1.559 | 1.425 | 0.966 | **2.398** | 0 | 0 | 0 |
| `tier_2_groq_qwen (qwen/qwen3.8-27b)` | 28 | 27 | 26 | 1.315 | 1.430 | 0.393 | **2.705** | 2 | 0 | 9 |
| `tier_2_groq_gpt_oss (openai/gpt-oss-20b, UF2/UF6)` | 16 | 16 | 14 | 1.281 | 1.220 | 0.586 | **2.308** | 0 | 0 | 0 |

> **Lưu ý ở `tier_1_flash38_only` / `tier_1_hybrid_primary`:** 1 ca (`uf2_03_large_rolling_horizon`) gặp lỗi Google `504 DEADLINE_EXCEEDED` sau `33.295s` nên không tính vào `Trung bình/Tối đa` các lượt thành công (`6.535s`), được tính vào `> Trần UF = 1` và ghi đầy đủ trong file log.

## 2. Bảng Chi tiết Theo Từng Tier × Từng UF

| Chế độ / Tier | UF | Số ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min–Max (s) | > 2.5s | > Trần UF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `production` | `UF1` | 12 | 12 | 12 | 2.179 | 2.133 | 1.774–2.870 | 2 | 0 |
| `production` | `UF2_Decompose` | 6 | 6 | 6 | 2.066 | 1.966 | 1.530–2.916 | 1 | 0 |
| `production` | `UF2_Adjust` | 5 | 5 | 5 | 1.241 | 1.298 | 1.040–1.350 | 0 | 0 |
| `production` | `UF6` | 5 | 5 | 5 | 1.086 | 1.199 | 0.785–1.332 | 0 | 0 |
| `production_hybrid` | `UF1` | 12 | 12 | 12 | 2.179 | 2.133 | 1.774–2.870 | 2 | 0 |
| `production_hybrid` | `UF2_Decompose` | 6 | 6 | 6 | 4.368 | 4.510 | 3.497–4.784 | 6 | 0 |
| `production_hybrid` | `UF2_Adjust` | 5 | 5 | 5 | 2.837 | 2.998 | 2.123–3.225 | 4 | 0 |
| `production_hybrid` | `UF6` | 5 | 5 | 5 | 2.432 | 2.299 | 2.245–3.005 | 1 | 0 |
| `tier_1_gemini` | `UF1` | 12 | 12 | 12 | 2.213 | 2.106 | 1.694–3.013 | 2 | 0 |
| `tier_1_gemini` | `UF2_Decompose` | 6 | 6 | 6 | 2.081 | 2.188 | 1.375–2.398 | 0 | 0 |
| `tier_1_gemini` | `UF2_Adjust` | 5 | 5 | 5 | 1.314 | 1.371 | 0.966–1.477 | 0 | 0 |
| `tier_1_gemini` | `UF6` | 5 | 5 | 5 | 1.177 | 1.106 | 1.039–1.503 | 0 | 0 |
| `tier_1_hybrid_primary` | `UF1` | 12 | 12 | 12 | 2.213 | 2.106 | 1.694–3.013 | 2 | 0 |
| `tier_1_hybrid_primary` | `UF2_Decompose` | 6 | 5 | 5 | 5.202 | 5.060 | 4.066–6.535 | 6 | 1 |
| `tier_1_hybrid_primary` | `UF2_Adjust` | 5 | 5 | 5 | 3.179 | 3.110 | 2.501–4.208 | 5 | 0 |
| `tier_1_hybrid_primary` | `UF6` | 5 | 5 | 5 | 2.852 | 2.847 | 2.326–3.576 | 4 | 0 |
| `tier_1_flash38_only` | `UF2_Decompose` | 6 | 5 | 5 | 5.202 | 5.060 | 4.066–6.535 | 6 | 1 |
| `tier_1_flash38_only` | `UF2_Adjust` | 5 | 5 | 5 | 3.179 | 3.110 | 2.501–4.208 | 5 | 0 |
| `tier_1_flash38_only` | `UF6` | 5 | 5 | 5 | 2.852 | 2.847 | 2.326–3.576 | 4 | 0 |
| `tier_1_backup_flash_lite_uf26` | `UF2_Decompose` | 6 | 6 | 6 | 2.081 | 2.188 | 1.375–2.398 | 0 | 0 |
| `tier_1_backup_flash_lite_uf26` | `UF2_Adjust` | 5 | 5 | 5 | 1.314 | 1.371 | 0.966–1.477 | 0 | 0 |
| `tier_1_backup_flash_lite_uf26` | `UF6` | 5 | 5 | 5 | 1.177 | 1.106 | 1.039–1.503 | 0 | 0 |
| `tier_2_groq_qwen` | `UF1` | 12 | 12 | 11 | 1.975 | 1.831 | 1.446–2.705 | 2 | 0 |
| `tier_2_groq_qwen` | `UF2_Decompose` | 6 | 5 | 5 | 1.305 | 1.268 | 0.851–1.718 | 0 | 0 |
| `tier_2_groq_qwen` | `UF2_Adjust` | 5 | 5 | 5 | 0.613 | 0.641 | 0.473–0.763 | 0 | 0 |
| `tier_2_groq_qwen` | `UF6` | 5 | 5 | 5 | 0.444 | 0.433 | 0.393–0.519 | 0 | 0 |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | 6 | 6 | 5 | 1.809 | 1.906 | 1.227–2.308 | 0 | 0 |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | 5 | 5 | 4 | 1.031 | 1.166 | 0.625–1.259 | 0 | 0 |
| `tier_2_groq_gpt_oss` | `UF6` | 5 | 5 | 5 | 0.897 | 0.750 | 0.586–1.645 | 0 | 0 |

## 3. Phân tích Winner Thực tế Trong `production_hybrid` (UF1=`3.5-flash-lite`; UF2/UF6=`3.8-flash` + Warm Backup)

| UF | Winner Model | Số ca thắng | Trung bình (s) | Min–Max (s) | Ghi chú |
|---|---|---:|---:|---:|---|
| `UF1` | `gemini-3.5-flash-lite` | 12/12 | 2.179 | 1.774–2.870 | Primary |
| `UF2_Decompose` | `gemini-3.5-flash-lite` | 4/6 | 4.582 | 4.502–4.784 | Warm Backup cứu tại mốc priority |
| `UF2_Decompose` | `gemini-3.8-flash` | 2/6 | 3.940 | 3.497–4.383 | Primary |
| `UF2_Adjust` | `gemini-3.5-flash-lite` | 3/5 | 3.077 | 2.998–3.225 | Warm Backup cứu tại mốc priority |
| `UF2_Adjust` | `gemini-3.8-flash` | 2/5 | 2.478 | 2.123–2.832 | Primary |
| `UF6` | `gemini-3.5-flash-lite` | 1/5 | 3.005 | 3.005–3.005 | Warm Backup cứu tại mốc priority |
| `UF6` | `gemini-3.8-flash` | 4/5 | 2.288 | 2.245–2.355 | Primary |

## 4. Voice Pipeline (`Edge-TTS`) & `UF5` Rule-based

| Thành phần | Số mẫu | Thành công | Trung bình (s) | Min–Max (s) | Kết quả |
|---|---:|---:|---:|---:|---|
| `voice_tts_edge` (`vi-VN-HoaiMyNeural`) | 2 | 0 | 2.766 | 2.762–2.769 | Lỗi `EMPTY_AUDIO: NoAudioReceived` (nên 2 ca STT kế tiếp chưa có audio đầu vào) |
| `uf5_rule_based` (`build_uf5_checkin_prompt`) | 5 | 5 | 0.000 | 0.000–0.000 | 5/5 PASS (đồng bộ cục bộ, không gọi LLM) |

## 5. Đề xuất 3 Bộ Trọng số Timeout Phù hợp Từng UF (Từ Số liệu Thực nghiệm)

### Bộ A — Speed-First (Ưu tiên SLA ≤ 2.5s cho ADHD-I)
- **UF1:** `AI_UF1_GROQ_FAST_FAIL_SECONDS = 1.8s`, `AI_UF1_TIMEOUT_SECONDS = 4.0s`
- **UF2_Decompose:** `warm_backup_at = 1.2s`, `primary_priority_until = 3.6s`, `groq_hedge_at = 3.8s`, `hard_timeout = 7.0s`
- **UF2_Adjust:** `warm_backup_at = 1.2s`, `primary_priority_until = 2.6s`, `groq_hedge_at = 2.9s`, `hard_timeout = 4.0s`
- **UF6:** `warm_backup_at = 1.0s`, `primary_priority_until = 2.4s`, `groq_hedge_at = 2.7s`, `hard_timeout = 3.8s`

### Bộ B — Balanced (Cân bằng Quality–Speed — Khuyên dùng nếu giữ `3.8-flash` ở UF2/UF6)
- **UF1:** `AI_UF1_GROQ_FAST_FAIL_SECONDS = 2.0s`, `AI_UF1_TIMEOUT_SECONDS = 4.5s` *(giúp Qwen còn đủ 2.5s hoàn tất trước trần nếu Gemini lỗi)*
- **UF2_Decompose (Khung B):** `warm_backup_at = 2.2s`, `primary_priority_until = 5.2s`, `groq_hedge_at = 5.5s`, `hard_timeout = 9.0s` *(nới ưu tiên qua median `5.06s` của `3.8-flash`)*
- **UF2_Adjust (Khung A):** `warm_backup_at = 1.6s`, `primary_priority_until = 3.2s`, `groq_hedge_at = 3.5s`, `hard_timeout = 4.5s`
- **UF6 (Khung A):** `warm_backup_at = 1.5s`, `primary_priority_until = 3.0s`, `groq_hedge_at = 3.5s`, `hard_timeout = 4.5s`

### Bộ C — Quality-First (Ưu tiên `gemini-3.8-flash` Tối đa)
- **UF1:** `AI_UF1_GROQ_FAST_FAIL_SECONDS = 2.5s`, `AI_UF1_TIMEOUT_SECONDS = 4.5s`
- **UF2_Decompose:** `warm_backup_at = 3.5s`, `primary_priority_until = 6.8s`, `groq_hedge_at = 6.5s`, `hard_timeout = 9.0s`
- **UF2_Adjust:** `warm_backup_at = 2.2s`, `primary_priority_until = 3.8s` *(hoặc `4.3s` nếu nới trần lên `5.0s`)*, `groq_hedge_at = 3.8s`, `hard_timeout = 4.5s` *(hoặc `5.0s`)*
- **UF6:** `warm_backup_at = 2.0s`, `primary_priority_until = 3.7s`, `groq_hedge_at = 3.8s`, `hard_timeout = 4.5s`

## 6. Kết luận Nút thắt & Lưu ý Kỹ thuật Chính

1. **Không nên giữ `AI_UF1_GROQ_FAST_FAIL_SECONDS = 3.5s` với trần `4.5s`:** Nếu Gemini lỗi sau `2.5s`, Qwen chỉ còn `< 2.0s` trong khi thực tế Qwen UF1 mất `1.446s–2.705s`.
2. **Groq `qwen/qwen3.8-27b` giới hạn OTPM = `1,000` tokens/phút:** Dù đã điều tiết đầu vào, log ghi nhận **9 lần HTTP 429 OTPM** (`uf1_03_large_complex` tốn `1,063` completion tokens trong 1 lượt). Không nên để Qwen chịu lưu lượng chính.
3. **Groq `openai/gpt-oss-20b` đạt `0.586s–2.308s` (0 lỗi 429)** nhưng sai nghiệp vụ ở `2/16` ca phức tạp (trả tiếng Anh + mất `unexpanded_scope` ở `uf2_03`; tự sửa thay vì hỏi lại ở `uf2_adj_05`).
4. **Giá trị của Warm Backup (`3.5-flash-lite`) khi chạy cùng `3.8-flash`:** Đạt `28/28 PASS` ở `production_hybrid`, cứu toàn bộ `8` ca `3.8-flash` chậm hơn cửa sổ ưu tiên (kể cả ca Google trả `504` sau `33.295s`).
