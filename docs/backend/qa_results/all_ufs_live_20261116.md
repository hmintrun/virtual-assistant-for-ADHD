# Báo cáo Kiểm thử API Thật Toàn bộ Luồng AI (UF1, UF2, UF6 + Voice + UF5)

- **Bắt đầu:** `2026-11-16T11:14:32.042516+07:00`
- **Kết thúc:** `2026-11-16T11:33:23.997202+07:00`
- **Mốc thời gian giả lập ngữ cảnh:** `2026-11-16T11:00:00+07:00`
- **Ngưỡng SLA mục tiêu chung:** `2.5s` | **Trần timeout production:** UF1 `4.5s`, UF2-Decompose `9.0s`, UF2-Adjust `4.5s`, UF6 `4.5s`
- **Model cấu hình hiện hành:** UF1=`gemini-3.5-flash-lite`, UF2/UF6 Primary=`gemini-3.5-flash-lite`, Groq Primary=`qwen/qwen3.8-27b`, Groq Secondary=`openai/gpt-oss-20b`

> Ghi chú: Các mode đo từng Tier độc lập (`tier_1_gemini`, `tier_2_groq_qwen`, `tier_2_groq_gpt_oss`) nới trần chờ lên 35s để đo **thời gian thực tế model xuất output** (kể cả khi vượt trần production), không cộng thời gian ngủ chờ quota Groq (`pacing_wait_s`) vào `elapsed_s`. Mode `production` chạy giữ nguyên 100% timeout & cấu hình thực tế của `AIService`.

## 1. Bảng Tổng hợp Thời gian Theo Từng Tier / Chế độ

| Chế độ / Tier | Tổng ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min (s) | Tối đa (s) | > 2.5s | > Trần UF | 429 gặp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `production` | 28 | 28 | 28 | 1.792 | 1.794 | 0.785 | 2.916 | 3 | 0 | 0 |
| `tier_1_gemini` | 28 | 28 | 28 | 1.839 | 1.919 | 0.966 | 3.013 | 2 | 0 | 0 |
| `tier_2_groq_qwen` | 28 | 27 | 26 | 1.315 | 1.430 | 0.393 | 2.705 | 2 | 0 | 9 |
| `tier_2_groq_gpt_oss` | 16 | 16 | 14 | 1.281 | 1.220 | 0.586 | 2.308 | 0 | 0 | 0 |

## 2. Bảng Chi tiết Theo Từng Tier × Từng UF

| Chế độ / Tier | UF | Số ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min–Max (s) | > 2.5s | > Trần UF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `production` | `UF1` | 12 | 12 | 12 | 2.179 | 2.133 | 1.774–2.870 | 2 | 0 |
| `production` | `UF2_Decompose` | 6 | 6 | 6 | 2.066 | 1.966 | 1.530–2.916 | 1 | 0 |
| `production` | `UF2_Adjust` | 5 | 5 | 5 | 1.241 | 1.298 | 1.040–1.350 | 0 | 0 |
| `production` | `UF6` | 5 | 5 | 5 | 1.086 | 1.199 | 0.785–1.332 | 0 | 0 |
| `tier_1_gemini` | `UF1` | 12 | 12 | 12 | 2.213 | 2.106 | 1.694–3.013 | 2 | 0 |
| `tier_1_gemini` | `UF2_Decompose` | 6 | 6 | 6 | 2.081 | 2.188 | 1.375–2.398 | 0 | 0 |
| `tier_1_gemini` | `UF2_Adjust` | 5 | 5 | 5 | 1.314 | 1.371 | 0.966–1.477 | 0 | 0 |
| `tier_1_gemini` | `UF6` | 5 | 5 | 5 | 1.177 | 1.106 | 1.039–1.503 | 0 | 0 |
| `tier_2_groq_qwen` | `UF1` | 12 | 12 | 11 | 1.975 | 1.831 | 1.446–2.705 | 2 | 0 |
| `tier_2_groq_qwen` | `UF2_Decompose` | 6 | 5 | 5 | 1.305 | 1.268 | 0.851–1.718 | 0 | 0 |
| `tier_2_groq_qwen` | `UF2_Adjust` | 5 | 5 | 5 | 0.613 | 0.641 | 0.473–0.763 | 0 | 0 |
| `tier_2_groq_qwen` | `UF6` | 5 | 5 | 5 | 0.444 | 0.433 | 0.393–0.519 | 0 | 0 |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | 6 | 6 | 5 | 1.809 | 1.906 | 1.227–2.308 | 0 | 0 |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | 5 | 5 | 4 | 1.031 | 1.166 | 0.625–1.259 | 0 | 0 |
| `tier_2_groq_gpt_oss` | `UF6` | 5 | 5 | 5 | 0.897 | 0.750 | 0.586–1.645 | 0 | 0 |

## 3. Phân bố Winner Tier trong chế độ `production`

| `generation_tier` | Số ca | Trung bình (s) | Min–Max (s) |
|---|---:|---:|---:|
| `tier_1_gemini` | 28 | 1.792 | 0.785–2.916 |

## 4. Voice Pipeline (Edge-TTS, Gemini STT, Groq Whisper STT) & UF5 Rule-based

| Thành phần | Số mẫu | Thành công | Trung bình (s) | Min–Max (s) |
|---|---:|---:|---:|---:|
| `voice_tts_edge` | 2 | 0 | 2.766 | 2.762–2.769 |
| `uf5_rule_based` | 5 | 5 | 0.000 | 0.000–0.000 |

## 5. Chi tiết Từng Lượt Chạy (28 Testcases LLM)

| Mode | UF | Testcase | Status | Model thắng | Elapsed (s) | API (s) | Checks | Ghi chú |
|---|---|---|---|---|---:|---:|---|---|
| `production` | `UF1` | `uf1_01_small_complete` | `success` | `gemini-3.5-flash-lite` | 2.235 | 2.234 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_01_small_complete` | `success` | `gemini-3.5-flash-lite` | 2.206 | 2.205 | PASS | OK |
| `production` | `UF1` | `uf1_02_medium_multi_windows` | `success` | `gemini-3.5-flash-lite` | 2.832 | 2.832 | PASS | OVER_SLA(2.5s) |
| `tier_1_gemini` | `UF1` | `uf1_02_medium_multi_windows` | `success` | `gemini-3.5-flash-lite` | 2.947 | 2.947 | PASS | OVER_SLA(2.5s) |
| `production` | `UF1` | `uf1_03_large_complex` | `success` | `gemini-3.5-flash-lite` | 2.870 | 2.870 | PASS | OVER_SLA(2.5s) |
| `tier_1_gemini` | `UF1` | `uf1_03_large_complex` | `success` | `gemini-3.5-flash-lite` | 3.013 | 3.013 | PASS | OVER_SLA(2.5s) |
| `production` | `UF1` | `uf1_04_missing_all_except_content` | `success` | `gemini-3.5-flash-lite` | 1.889 | 1.888 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_04_missing_all_except_content` | `success` | `gemini-3.5-flash-lite` | 1.854 | 1.854 | PASS | OK |
| `production` | `UF1` | `uf1_05_missing_avail_and_deadline` | `success` | `gemini-3.5-flash-lite` | 1.774 | 1.774 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_05_missing_avail_and_deadline` | `success` | `gemini-3.5-flash-lite` | 1.983 | 1.983 | PASS | OK |
| `production` | `UF1` | `uf1_06_ambiguous_time_phrase` | `success` | `gemini-3.5-flash-lite` | 2.058 | 2.058 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_06_ambiguous_time_phrase` | `success` | `gemini-3.5-flash-lite` | 2.051 | 2.050 | PASS | OK |
| `production` | `UF1` | `uf1_07_past_deadline` | `success` | `gemini-3.5-flash-lite` | 2.361 | 2.360 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_07_past_deadline` | `success` | `gemini-3.5-flash-lite` | 2.071 | 2.071 | PASS | OK |
| `production` | `UF1` | `uf1_08_calendar_busy_conflict` | `success` | `gemini-3.5-flash-lite` | 2.397 | 2.396 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_08_calendar_busy_conflict` | `success` | `gemini-3.5-flash-lite` | 2.497 | 2.496 | PASS | OK |
| `production` | `UF1` | `uf1_09_followup_merge_draft` | `success` | `gemini-3.5-flash-lite` | 2.207 | 2.207 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_09_followup_merge_draft` | `success` | `gemini-3.5-flash-lite` | 2.141 | 2.140 | PASS | OK |
| `production` | `UF1` | `uf1_10_followup_replace_avail` | `success` | `gemini-3.5-flash-lite` | 1.798 | 1.797 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_10_followup_replace_avail` | `success` | `gemini-3.5-flash-lite` | 1.784 | 1.784 | PASS | OK |
| `production` | `UF1` | `uf1_11_default_windows_opt_in` | `success` | `gemini-3.5-flash-lite` | 1.888 | 1.888 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_11_default_windows_opt_in` | `success` | `gemini-3.5-flash-lite` | 2.320 | 2.320 | PASS | OK |
| `production` | `UF1` | `uf1_12_colloquial_voice_slang` | `success` | `gemini-3.5-flash-lite` | 1.833 | 1.833 | PASS | OK |
| `tier_1_gemini` | `UF1` | `uf1_12_colloquial_voice_slang` | `success` | `gemini-3.5-flash-lite` | 1.694 | 1.694 | PASS | OK |
| `production` | `UF2_Decompose` | `uf2_01_small_task` | `success` | `gemini-3.5-flash-lite` | 2.266 | 2.266 | PASS | OK |
| `tier_1_gemini` | `UF2_Decompose` | `uf2_01_small_task` | `success` | `gemini-3.5-flash-lite` | 2.120 | 2.120 | PASS | OK |
| `production` | `UF2_Decompose` | `uf2_02_medium_report` | `success` | `gemini-3.5-flash-lite` | 1.791 | 1.790 | PASS | OK |
| `tier_1_gemini` | `UF2_Decompose` | `uf2_02_medium_report` | `success` | `gemini-3.5-flash-lite` | 2.354 | 2.354 | PASS | OK |
| `production` | `UF2_Decompose` | `uf2_03_large_rolling_horizon` | `success` | `gemini-3.5-flash-lite` | 2.916 | 2.916 | PASS | OVER_SLA(2.5s) |
| `tier_1_gemini` | `UF2_Decompose` | `uf2_03_large_rolling_horizon` | `success` | `gemini-3.5-flash-lite` | 2.398 | 2.397 | PASS | OK |
| `production` | `UF2_Decompose` | `uf2_04_user_proposal_clear` | `success` | `gemini-3.5-flash-lite` | 2.142 | 2.142 | PASS | OK |
| `tier_1_gemini` | `UF2_Decompose` | `uf2_04_user_proposal_clear` | `success` | `gemini-3.5-flash-lite` | 2.255 | 2.255 | PASS | OK |
| `production` | `UF2_Decompose` | `uf2_05_user_proposal_unclear` | `success` | `gemini-3.5-flash-lite` | 1.530 | 1.530 | PASS | OK |
| `tier_1_gemini` | `UF2_Decompose` | `uf2_05_user_proposal_unclear` | `success` | `gemini-3.5-flash-lite` | 1.375 | 1.375 | PASS | OK |
| `production` | `UF2_Decompose` | `uf2_06_long_focus_interval` | `success` | `gemini-3.5-flash-lite` | 1.752 | 1.752 | PASS | OK |
| `tier_1_gemini` | `UF2_Decompose` | `uf2_06_long_focus_interval` | `success` | `gemini-3.5-flash-lite` | 1.986 | 1.985 | PASS | OK |
| `production` | `UF2_Adjust` | `uf2_adj_01_rename_and_duration` | `success` | `gemini-3.5-flash-lite` | 1.350 | 1.348 | PASS | OK |
| `tier_1_gemini` | `UF2_Adjust` | `uf2_adj_01_rename_and_duration` | `success` | `gemini-3.5-flash-lite` | 1.477 | 1.477 | PASS | OK |
| `production` | `UF2_Adjust` | `uf2_adj_02_reorder_steps` | `success` | `gemini-3.5-flash-lite` | 1.204 | 1.203 | PASS | OK |
| `tier_1_gemini` | `UF2_Adjust` | `uf2_adj_02_reorder_steps` | `success` | `gemini-3.5-flash-lite` | 1.371 | 1.371 | PASS | OK |
| `production` | `UF2_Adjust` | `uf2_adj_03_draft_update_required` | `success` | `gemini-3.5-flash-lite` | 1.298 | 1.298 | PASS | OK |
| `tier_1_gemini` | `UF2_Adjust` | `uf2_adj_03_draft_update_required` | `success` | `gemini-3.5-flash-lite` | 1.476 | 1.475 | PASS | OK |
| `production` | `UF2_Adjust` | `uf2_adj_04_unsupported_add_delete` | `success` | `gemini-3.5-flash-lite` | 1.040 | 1.040 | PASS | OK |
| `tier_1_gemini` | `UF2_Adjust` | `uf2_adj_04_unsupported_add_delete` | `success` | `gemini-3.5-flash-lite` | 0.966 | 0.966 | PASS | OK |
| `production` | `UF2_Adjust` | `uf2_adj_05_ambiguous` | `success` | `gemini-3.5-flash-lite` | 1.315 | 1.315 | PASS | OK |
| `tier_1_gemini` | `UF2_Adjust` | `uf2_adj_05_ambiguous` | `success` | `gemini-3.5-flash-lite` | 1.278 | 1.278 | PASS | OK |
| `production` | `UF6` | `uf6_01_busy_proposal` | `success` | `gemini-3.5-flash-lite` | 0.785 | 0.785 | PASS | OK |
| `tier_1_gemini` | `UF6` | `uf6_01_busy_proposal` | `success` | `gemini-3.5-flash-lite` | 1.106 | 1.106 | PASS | OK |
| `production` | `UF6` | `uf6_02_incomplete_deficit` | `success` | `gemini-3.5-flash-lite` | 1.332 | 1.332 | PASS | OK |
| `tier_1_gemini` | `UF6` | `uf6_02_incomplete_deficit` | `success` | `gemini-3.5-flash-lite` | 1.044 | 1.044 | PASS | OK |
| `production` | `UF6` | `uf6_03_skipped_no_proposal` | `success` | `gemini-3.5-flash-lite` | 1.300 | 1.299 | PASS | OK |
| `tier_1_gemini` | `UF6` | `uf6_03_skipped_no_proposal` | `success` | `gemini-3.5-flash-lite` | 1.039 | 1.039 | PASS | OK |
| `production` | `UF6` | `uf6_04_missed_session` | `success` | `gemini-3.5-flash-lite` | 0.815 | 0.815 | PASS | OK |
| `tier_1_gemini` | `UF6` | `uf6_04_missed_session` | `success` | `gemini-3.5-flash-lite` | 1.503 | 1.502 | PASS | OK |
| `production` | `UF6` | `uf6_05_prompt_injection` | `success` | `gemini-3.5-flash-lite` | 1.199 | 1.199 | PASS | OK |
| `tier_1_gemini` | `UF6` | `uf6_05_prompt_injection` | `success` | `gemini-3.5-flash-lite` | 1.195 | 1.195 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_01_small_complete` | `success` | `qwen/qwen3.8-27b` | 1.757 | 1.753 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | `uf2_01_small_task` | `success` | `openai/gpt-oss-20b` | 2.308 | 2.301 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | `uf2_02_medium_report` | `success` | `openai/gpt-oss-20b` | 1.959 | 1.958 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | `uf2_03_large_rolling_horizon` | `success` | `openai/gpt-oss-20b` | 2.142 | 2.140 | FAIL: has_unexpanded_scope | OK |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | `uf2_04_user_proposal_clear` | `success` | `openai/gpt-oss-20b` | 1.362 | 1.360 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_02_medium_multi_windows` | `success` | `qwen/qwen3.8-27b` | 2.626 | 2.623 | PASS | OVER_SLA(2.5s) |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | `uf2_05_user_proposal_unclear` | `success` | `openai/gpt-oss-20b` | 1.227 | 1.225 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Decompose` | `uf2_06_long_focus_interval` | `success` | `openai/gpt-oss-20b` | 1.854 | 1.852 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | `uf2_adj_01_rename_and_duration` | `success` | `openai/gpt-oss-20b` | 1.213 | 1.211 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | `uf2_adj_02_reorder_steps` | `success` | `openai/gpt-oss-20b` | 0.625 | 0.623 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_03_large_complex` | `success` | `qwen/qwen3.8-27b` | 2.705 | 2.702 | PASS | OVER_SLA(2.5s) |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | `uf2_adj_03_draft_update_required` | `success` | `openai/gpt-oss-20b` | 1.166 | 1.164 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | `uf2_adj_04_unsupported_add_delete` | `success` | `openai/gpt-oss-20b` | 1.259 | 1.257 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF2_Adjust` | `uf2_adj_05_ambiguous` | `success` | `openai/gpt-oss-20b` | 0.894 | 0.892 | FAIL: requires_clarification_match, empty_step_edits | OK |
| `tier_2_groq_gpt_oss` | `UF6` | `uf6_01_busy_proposal` | `success` | `openai/gpt-oss-20b` | 0.595 | 0.594 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF6` | `uf6_02_incomplete_deficit` | `success` | `openai/gpt-oss-20b` | 0.908 | 0.907 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF6` | `uf6_03_skipped_no_proposal` | `success` | `openai/gpt-oss-20b` | 0.750 | 0.749 | PASS | OK |
| `tier_2_groq_gpt_oss` | `UF6` | `uf6_04_missed_session` | `success` | `openai/gpt-oss-20b` | 1.645 | 1.645 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_04_missing_all_except_content` | `success` | `qwen/qwen3.8-27b` | 1.782 | 1.777 | PASS | 429x1 |
| `tier_2_groq_gpt_oss` | `UF6` | `uf6_05_prompt_injection` | `success` | `openai/gpt-oss-20b` | 0.586 | 0.585 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_05_missing_avail_and_deadline` | `success` | `qwen/qwen3.8-27b` | 1.446 | 1.442 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_06_ambiguous_time_phrase` | `success` | `qwen/qwen3.8-27b` | 1.647 | 1.644 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_07_past_deadline` | `success` | `qwen/qwen3.8-27b` | 2.332 | 2.329 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_08_calendar_busy_conflict` | `success` | `qwen/qwen3.8-27b` | 2.352 | 2.346 | FAIL: has_conflict_match | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_09_followup_merge_draft` | `success` | `qwen/qwen3.8-27b` | 1.868 | 1.864 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_10_followup_replace_avail` | `success` | `qwen/qwen3.8-27b` | 1.502 | 1.499 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_11_default_windows_opt_in` | `success` | `qwen/qwen3.8-27b` | 1.889 | 1.882 | PASS | OK |
| `tier_2_groq_qwen` | `UF1` | `uf1_12_colloquial_voice_slang` | `success` | `qwen/qwen3.8-27b` | 1.795 | 1.792 | PASS | OK |
| `tier_2_groq_qwen` | `UF2_Decompose` | `uf2_01_small_task` | `success` | `qwen/qwen3.8-27b` | 1.430 | 1.427 | PASS | 429x1 |
| `tier_2_groq_qwen` | `UF2_Decompose` | `uf2_02_medium_report` | `success` | `qwen/qwen3.8-27b` | 1.718 | 1.716 | PASS | OK |
| `tier_2_groq_qwen` | `UF2_Decompose` | `uf2_03_large_rolling_horizon` | `rate_limited_429` | `tier_3_rule_based` | 0.261 | — | FAIL: not_tier3_fallback, has_unexpanded_scope | 429x2 |
| `tier_2_groq_qwen` | `UF2_Decompose` | `uf2_04_user_proposal_clear` | `success` | `qwen/qwen3.8-27b` | 1.258 | 1.256 | PASS | OK |
| `tier_2_groq_qwen` | `UF2_Decompose` | `uf2_05_user_proposal_unclear` | `success` | `qwen/qwen3.8-27b` | 0.851 | 0.850 | PASS | 429x1 |
| `tier_2_groq_qwen` | `UF2_Decompose` | `uf2_06_long_focus_interval` | `success` | `qwen/qwen3.8-27b` | 1.268 | 1.265 | PASS | OK |
| `tier_2_groq_qwen` | `UF2_Adjust` | `uf2_adj_01_rename_and_duration` | `success` | `qwen/qwen3.8-27b` | 0.763 | 0.760 | PASS | OK |
| `tier_2_groq_qwen` | `UF2_Adjust` | `uf2_adj_02_reorder_steps` | `success` | `qwen/qwen3.8-27b` | 0.706 | 0.705 | PASS | OK |
| `tier_2_groq_qwen` | `UF2_Adjust` | `uf2_adj_03_draft_update_required` | `success` | `qwen/qwen3.8-27b` | 0.473 | 0.471 | PASS | 429x1 |
| `tier_2_groq_qwen` | `UF2_Adjust` | `uf2_adj_04_unsupported_add_delete` | `success` | `qwen/qwen3.8-27b` | 0.484 | 0.482 | PASS | 429x1 |
| `tier_2_groq_qwen` | `UF2_Adjust` | `uf2_adj_05_ambiguous` | `success` | `qwen/qwen3.8-27b` | 0.641 | 0.638 | PASS | OK |
| `tier_2_groq_qwen` | `UF6` | `uf6_01_busy_proposal` | `success` | `qwen/qwen3.8-27b` | 0.519 | 0.512 | PASS | OK |
| `tier_2_groq_qwen` | `UF6` | `uf6_02_incomplete_deficit` | `success` | `qwen/qwen3.8-27b` | 0.467 | 0.466 | PASS | OK |
| `tier_2_groq_qwen` | `UF6` | `uf6_03_skipped_no_proposal` | `success` | `qwen/qwen3.8-27b` | 0.393 | 0.391 | PASS | OK |
| `tier_2_groq_qwen` | `UF6` | `uf6_04_missed_session` | `success` | `qwen/qwen3.8-27b` | 0.409 | 0.409 | PASS | 429x1 |
| `tier_2_groq_qwen` | `UF6` | `uf6_05_prompt_injection` | `success` | `qwen/qwen3.8-27b` | 0.433 | 0.432 | PASS | 429x1 |
