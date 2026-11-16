# Thống kê Gộp: Chỉ bỏ 3.8-Flash ở UF1, các UF khác giữ `gemini-3.8-flash`

- **Thời điểm chạy bổ sung 3.8-Flash:** `2026-11-16T11:43:55.215966+07:00` $\rightarrow$ `2026-11-16T11:46:27.676141+07:00`
- **Nguồn gộp UF1 & Tier 2/Backup:** `docs/backend/qa_results/all_ufs_live_20261116.json`
- **Chính sách kiểm chứng:** UF1 = `gemini-3.5-flash-lite` (`MINIMAL`) + Qwen; UF2/UF6 = `gemini-3.8-flash` (`LOW`) + Warm Backup `gemini-3.5-flash-lite` + Groq (`qwen/qwen3.8-27b` / `openai/gpt-oss-20b`).
- **Mốc Khung A (UF2-Adjust / UF6):** Warm Backup `1.5s` | Priority `3.0s` | Groq Hedge `3.5s` | Hard `4.5s`.
- **Mốc Khung B (UF2-Decompose):** Warm Backup `2.0s` | Priority `4.5s` | Groq Hedge `5.0s` | Hard `9.0s`.

## 1. Bảng Tổng hợp Theo Từng Tier / Chế độ (Sau khi Gộp)

| Chế độ / Tier | Mô tả | Tổng ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min (s) | **Tối đa (s)** | > 2.5s | > Trần UF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `production_hybrid` | Production Hybrid (UF1=3.5-flash-lite; UF2/UF6=3.8-flash + Warm Backup 3.5-flash-lite + Groq) | 28 | 28 | 28 | 2.811 | 2.379 | 1.774 | 4.784 | 13 | 0 |
| `tier_1_hybrid_primary` | Tier 1 Primary Hybrid (UF1=3.5-flash-lite; UF2/UF6=3.8-flash riêng lẻ, trần 35s) | 28 | 27 | 27 | 3.064 | 2.847 | 1.694 | 6.535 | 17 | 1 |
| `tier_1_flash38_only` | Tier 1 Riêng `gemini-3.8-flash` (chỉ UF2/UF6, trần 35s) | 16 | 15 | 15 | 3.744 | 3.176 | 2.326 | 6.535 | 15 | 1 |
| `tier_1_backup_flash_lite_uf26` | Tier 1 Warm Backup `gemini-3.5-flash-lite` (UF2/UF6, từ lượt đo gốc) | 16 | 16 | 16 | 1.559 | 1.425 | 0.966 | 2.398 | 0 | 0 |
| `tier_2_groq_qwen` | Tier 2 Groq `qwen/qwen3.8-27b` (28 ca, từ lượt đo gốc) | 28 | 27 | 26 | 1.315 | 1.430 | 0.393 | 2.705 | 2 | 0 |
| `tier_2_groq_gpt_oss` | Tier 2 Groq `openai/gpt-oss-20b` (16 ca UF2/UF6, từ lượt đo gốc) | 16 | 16 | 14 | 1.281 | 1.220 | 0.586 | 2.308 | 0 | 0 |

## 2. Bảng Chi tiết Theo Từng Tier × Từng UF

| Chế độ / Tier | UF | Số ca | Thành công | Đạt check | Trung bình (s) | Trung vị (s) | Min–Max (s) | > 2.5s | > Trần UF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `production_hybrid` | `UF1` | 12 | 12 | 12 | 2.179 | 2.133 | 1.774–2.870 | 2 | 0 |
| `production_hybrid` | `UF2_Decompose` | 6 | 6 | 6 | 4.368 | 4.510 | 3.497–4.784 | 6 | 0 |
| `production_hybrid` | `UF2_Adjust` | 5 | 5 | 5 | 2.837 | 2.998 | 2.123–3.225 | 4 | 0 |
| `production_hybrid` | `UF6` | 5 | 5 | 5 | 2.432 | 2.299 | 2.245–3.005 | 1 | 0 |
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

## 3. Phân tích Winner Thực tế Trong `production_hybrid` (AI nào thắng?)

| UF | Winner Model | Số ca thắng | Trung bình (s) | Min–Max (s) |
|---|---|---:|---:|---:|
| `UF1` | `gemini-3.5-flash-lite` | 12 | 2.179 | 1.774–2.870 |
| `UF2_Decompose` | `gemini-3.5-flash-lite` | 4 | 4.582 | 4.502–4.784 |
| `UF2_Decompose` | `gemini-3.8-flash` | 2 | 3.940 | 3.497–4.383 |
| `UF2_Adjust` | `gemini-3.5-flash-lite` | 3 | 3.077 | 2.998–3.225 |
| `UF2_Adjust` | `gemini-3.8-flash` | 2 | 2.478 | 2.123–2.832 |
| `UF6` | `gemini-3.5-flash-lite` | 1 | 3.005 | 3.005–3.005 |
| `UF6` | `gemini-3.8-flash` | 4 | 2.288 | 2.245–2.355 |

## 4. Chi tiết Từng Lượt Chạy Mới (`gemini-3.8-flash` trên UF2 & UF6)

| Mode | UF | Testcase | Status | Winner Model | Elapsed (s) | Checks | Timeline Attempts |
|---|---|---|---|---|---:|---|---|
| `production_flash38` | `UF2_Decompose` | `uf2_01_small_task` | `success` | `gemini-3.8-flash` | 4.383 | PASS | `gemini-3.8-flash=schema_valid@0.0006s(4.3826s); gemini-3.5-flash-lite=schema_valid@2.0167s(1.9187s)` |
| `tier_1_flash38_only` | `UF2_Decompose` | `uf2_01_small_task` | `success` | `gemini-3.8-flash` | 5.060 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(5.0601s)` |
| `production_flash38` | `UF2_Decompose` | `uf2_02_medium_report` | `success` | `gemini-3.5-flash-lite` | 4.523 | PASS | `gemini-3.8-flash=cancelled@0.0002s(4.5233s); gemini-3.5-flash-lite=schema_valid@2.0105s(2.5126s)` |
| `tier_1_flash38_only` | `UF2_Decompose` | `uf2_02_medium_report` | `success` | `gemini-3.8-flash` | 6.535 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(6.5344s)` |
| `production_flash38` | `UF2_Decompose` | `uf2_03_large_rolling_horizon` | `success` | `gemini-3.5-flash-lite` | 4.784 | PASS | `gemini-3.8-flash=cancelled@0.0001s(4.784s); gemini-3.5-flash-lite=schema_valid@2.0115s(2.7722s)` |
| `tier_1_flash38_only` | `UF2_Decompose` | `uf2_03_large_rolling_horizon` | `error` | `tier_3_rule_based` | 33.295 | FAIL: not_tier3_fallback, has_unexpanded_scope | `gemini-3.8-flash=error@0.0002s(33.295s)` |
| `production_flash38` | `UF2_Decompose` | `uf2_04_user_proposal_clear` | `success` | `gemini-3.5-flash-lite` | 4.502 | PASS | `gemini-3.8-flash=cancelled@0.0001s(4.5019s); gemini-3.5-flash-lite=schema_valid@2.0056s(2.1362s)` |
| `tier_1_flash38_only` | `UF2_Decompose` | `uf2_04_user_proposal_clear` | `success` | `gemini-3.8-flash` | 4.870 | PASS | `gemini-3.8-flash=schema_valid@0.0001s(4.8693s)` |
| `production_flash38` | `UF2_Decompose` | `uf2_05_user_proposal_unclear` | `success` | `gemini-3.8-flash` | 3.497 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(3.4968s); gemini-3.5-flash-lite=schema_valid@2.0079s(1.4603s)` |
| `tier_1_flash38_only` | `UF2_Decompose` | `uf2_05_user_proposal_unclear` | `success` | `gemini-3.8-flash` | 4.066 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(4.0654s)` |
| `production_flash38` | `UF2_Decompose` | `uf2_06_long_focus_interval` | `success` | `gemini-3.5-flash-lite` | 4.519 | PASS | `gemini-3.8-flash=cancelled@0.0001s(4.5191s); gemini-3.5-flash-lite=schema_valid@2.0056s(2.0329s)` |
| `tier_1_flash38_only` | `UF2_Decompose` | `uf2_06_long_focus_interval` | `success` | `gemini-3.8-flash` | 5.478 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(5.4774s)` |
| `production_flash38` | `UF2_Adjust` | `uf2_adj_01_rename_and_duration` | `success` | `gemini-3.5-flash-lite` | 3.225 | PASS | `gemini-3.8-flash=cancelled@0.0015s(3.224s); gemini-3.5-flash-lite=schema_valid@1.5098s(1.7152s)` |
| `tier_1_flash38_only` | `UF2_Adjust` | `uf2_adj_01_rename_and_duration` | `success` | `gemini-3.8-flash` | 3.176 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(3.1762s)` |
| `production_flash38` | `UF2_Adjust` | `uf2_adj_02_reorder_steps` | `success` | `gemini-3.8-flash` | 2.832 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.8319s); gemini-3.5-flash-lite=cancelled@1.5134s(1.3193s)` |
| `tier_1_flash38_only` | `UF2_Adjust` | `uf2_adj_02_reorder_steps` | `success` | `gemini-3.8-flash` | 3.110 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(3.1101s)` |
| `production_flash38` | `UF2_Adjust` | `uf2_adj_03_draft_update_required` | `success` | `gemini-3.8-flash` | 2.123 | PASS | `gemini-3.8-flash=schema_valid@0.0003s(2.1228s); gemini-3.5-flash-lite=cancelled@1.5104s(0.613s)` |
| `tier_1_flash38_only` | `UF2_Adjust` | `uf2_adj_03_draft_update_required` | `success` | `gemini-3.8-flash` | 2.898 | PASS | `gemini-3.8-flash=schema_valid@0.0003s(2.8973s)` |
| `production_flash38` | `UF2_Adjust` | `uf2_adj_04_unsupported_add_delete` | `success` | `gemini-3.5-flash-lite` | 2.998 | PASS | `gemini-3.8-flash=cancelled@0.0003s(2.9986s); gemini-3.5-flash-lite=schema_valid@1.5035s(1.3225s)` |
| `tier_1_flash38_only` | `UF2_Adjust` | `uf2_adj_04_unsupported_add_delete` | `success` | `gemini-3.8-flash` | 2.501 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.5009s)` |
| `production_flash38` | `UF2_Adjust` | `uf2_adj_05_ambiguous` | `success` | `gemini-3.5-flash-lite` | 3.007 | PASS | `gemini-3.8-flash=cancelled@0.0004s(3.0072s); gemini-3.5-flash-lite=schema_valid@1.5156s(1.366s)` |
| `tier_1_flash38_only` | `UF2_Adjust` | `uf2_adj_05_ambiguous` | `success` | `gemini-3.8-flash` | 4.208 | PASS | `gemini-3.8-flash=schema_valid@0.0005s(4.2076s)` |
| `production_flash38` | `UF6` | `uf6_01_busy_proposal` | `success` | `gemini-3.8-flash` | 2.355 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.3549s); gemini-3.5-flash-lite=cancelled@1.508s(0.8475s)` |
| `tier_1_flash38_only` | `UF6` | `uf6_01_busy_proposal` | `success` | `gemini-3.8-flash` | 2.881 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.8805s)` |
| `production_flash38` | `UF6` | `uf6_02_incomplete_deficit` | `success` | `gemini-3.5-flash-lite` | 3.005 | PASS | `gemini-3.8-flash=cancelled@0.0001s(3.0055s); gemini-3.5-flash-lite=schema_valid@1.5023s(1.1211s)` |
| `tier_1_flash38_only` | `UF6` | `uf6_02_incomplete_deficit` | `success` | `gemini-3.8-flash` | 3.576 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(3.5762s)` |
| `production_flash38` | `UF6` | `uf6_03_skipped_no_proposal` | `success` | `gemini-3.8-flash` | 2.299 | PASS | `gemini-3.8-flash=schema_valid@0.0001s(2.2991s); gemini-3.5-flash-lite=cancelled@1.5131s(0.7868s)` |
| `tier_1_flash38_only` | `UF6` | `uf6_03_skipped_no_proposal` | `success` | `gemini-3.8-flash` | 2.326 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.3258s)` |
| `production_flash38` | `UF6` | `uf6_04_missed_session` | `success` | `gemini-3.8-flash` | 2.254 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.2542s); gemini-3.5-flash-lite=cancelled@1.5161s(0.7387s)` |
| `tier_1_flash38_only` | `UF6` | `uf6_04_missed_session` | `success` | `gemini-3.8-flash` | 2.847 | PASS | `gemini-3.8-flash=schema_valid@0.0002s(2.8471s)` |
| `production_flash38` | `UF6` | `uf6_05_prompt_injection` | `success` | `gemini-3.8-flash` | 2.245 | PASS | `gemini-3.8-flash=schema_valid@0.0001s(2.245s); gemini-3.5-flash-lite=cancelled@1.4991s(0.7463s)` |
| `tier_1_flash38_only` | `UF6` | `uf6_05_prompt_injection` | `success` | `gemini-3.8-flash` | 2.632 | PASS | `gemini-3.8-flash=schema_valid@0.0001s(2.6315s)` |
