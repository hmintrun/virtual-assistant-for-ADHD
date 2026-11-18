# Báo cáo Chi tiết Trace Logs: Cơ chế 3-Tier AI Fallback $\rightarrow$ Thuần Python (Thang đo Giây Thực tế)

- **Thời gian xuất báo cáo:** `2026-11-12 01:33:33 UTC`
- **Tổng số kịch bản:** `6`
- **Kết quả:** `100% PASSED`

## 1. Bảng Ma trận Kiểm soát Model & Quyết định Chuyển tầng

| Kịch bản | Model được gọi | Model bỏ qua (Không gọi) | Model chiến thắng | Tổng thời gian | Kết quả |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Fast-Fail Tức Thì (Google Lỗi Nhanh -> Groq Cứu Hộ)** | `gemini-3.8-flash, gemini-3.5-flash-lite, qwen/qwen3.8-27b` | *(Không có)* | **`qwen/qwen3.8-27b`** | **0.02s** | ĐẠT |
| **Cửa sổ Ưu tiên Chất lượng (Quality-First Priority Window)** | `gemini-3.5-flash-lite, gemini-3.8-flash` | qwen/qwen3.8-27b | **`gemini-3.8-flash`** | **3.41s** | ĐẠT |
| **Tier 1 Trả Về Quá Lâu (> 7.5s) -> Bung Bản Nháp RAM Cứu Hộ** | `gemini-3.8-flash, gemini-3.5-flash-lite` | *(Không có)* | **`gemini-3.5-flash-lite`** | **7.51s** | ĐẠT |
| **Toàn Bộ Google Bị Treo -> Groq Cloud LPU Cứu Hộ** | `gemini-3.8-flash, gemini-3.5-flash-lite, qwen/qwen3.8-27b` | *(Không có)* | **`qwen/qwen3.8-27b`** | **4.00s** | ĐẠT |
| **Chống Nghẽn Quota (Groq Qwen 429 TPM -> Groq GPT-OSS)** | `qwen/qwen3.8-27b, openai/gpt-oss-20b` | *(Không có)* | **`openai/gpt-oss-20b`** | **0.00s** | ĐẠT |
| **Sập Toàn Bộ Cloud -> Thuần Python Bọc Lót & Anti-Hallucination** | `UF1 / gemini-3.8-flash, UF2 / gemini-3.8-flash, UF1 / gemini-3.5-flash-lite, UF2 / gemini-3.5-flash-lite, UF1 / qwen/qwen3.8-27b, UF2 / qwen/qwen3.8-27b` | *(Không có)* | **`pure_python_rule_based`** | **9.01s** | ĐẠT |

## 2. Chi tiết Từng Kịch bản & Dòng Thời gian (Timeline)

### 1. Fast-Fail Tức Thì (Google Lỗi Nhanh -> Groq Cứu Hộ)
- **Mô tả:** Cả hai model Gemini lỗi nhanh. Groq được gọi trước mốc hedge 3.5s.
- **Thời gian bắt đầu:** `2026-11-12T01:33:09.278945+00:00`
- **Thời gian quyết định:** `2026-11-12T01:33:09.296364+00:00` (ở mốc `+0.018 giây`)
- **Model chiến thắng:** `qwen/qwen3.8-27b` (`tier_2_groq`)
- **Ghi chú:** Kỳ vọng chuyển sang Groq ngay sau khi cả hai model Gemini báo lỗi.

#### Timeline & Chi tiết Từng Model:

| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `gemini-3.8-flash` | tier_1_gemini | ✅ Có | +0.02s | +0.02s | `FAILED` | PermissionError: Google Gemini API unavailable (HTTP 403 Forbidden) | Không |
| `gemini-3.5-flash-lite` | tier_1_gemini | ✅ Có | +0.02s | +0.02s | `FAILED` | PermissionError: Google Gemini API unavailable (HTTP 403 Forbidden) | Không |
| `qwen/qwen3.8-27b` | tier_2_groq | ✅ Có | +0.02s | +0.02s | `SUCCESS` | Có dữ liệu hợp lệ | ⭐ **CHỌN** |

> **Dữ liệu Final Output:** `{"title": "Tiểu luận Triết học Mác - Lênin", "content": "Viết tiểu luận Triết học về chủ nghĩa duy vật biện chứng", "is_complete": true}`

---

### 2. Cửa sổ Ưu tiên Chất lượng (Quality-First Priority Window)
- **Mô tả:** Flash-Lite trong RAM xong sớm (2.9s) nhưng 3.8 Flash hoàn thành lúc 3.4s (nằm trong cửa sổ 4.0s) -> Hệ thống kiên quyết chọn 3.8 Flash
- **Thời gian bắt đầu:** `2026-11-12T01:33:09.296883+00:00`
- **Thời gian quyết định:** `2026-11-12T01:33:12.708118+00:00` (ở mốc `+3.412 giây`)
- **Model chiến thắng:** `gemini-3.8-flash` (`tier_1_gemini`)
- **Ghi chú:** Gemini 3.8 Flash hoàn tất lúc 3.4s (nằm trong cửa sổ ưu tiên 4.0s); hệ thống kiên nhẫn nhận output chất lượng cao dù 3.5 Lite trong RAM xong sớm hơn

#### Timeline & Chi tiết Từng Model:

| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `gemini-3.5-flash-lite` | tier_1_gemini (warm_backup) | ✅ Có | +2.62s | +2.93s | `SUCCESS` | Có dữ liệu hợp lệ | Không |
| `gemini-3.8-flash` | tier_1_gemini | ✅ Có | +0.00s | +3.41s | `SUCCESS` | Có dữ liệu hợp lệ | ⭐ **CHỌN** |
| `qwen/qwen3.8-27b` | tier_2_groq | ❌ Không | - | - | `SKIPPED` | Groq không được cấu hình trong kịch bản này | Không |

> **Dữ liệu Final Output:** `{"title": "Bản chuẩn chất lượng cao từ Gemini 3.8 Flash", "scope": "15 slide PowerPoint chi tiết kèm sơ đồ kiến trúc"}`

---

### 3. Tier 1 Trả Về Quá Lâu (> 7.5s) -> Bung Bản Nháp RAM Cứu Hộ
- **Mô tả:** Primary treo; Flash-Lite phản hồi trước khi kết thúc cửa sổ ưu tiên 7.5s.
- **Thời gian bắt đầu:** `2026-11-12T01:33:12.708118+00:00`
- **Thời gian quyết định:** `2026-11-12T01:33:20.215843+00:00` (ở mốc `+7.508 giây`)
- **Model chiến thắng:** `gemini-3.5-flash-lite` (`tier_1_gemini`)
- **Ghi chú:** Kỳ vọng chọn Flash-Lite tại mốc 7.5s của Khung B.

#### Timeline & Chi tiết Từng Model:

| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `gemini-3.8-flash` | tier_1_gemini (primary) | ✅ Có | +0.00s | +7.51s | `CANCELLED` | None | Không |
| `gemini-3.5-flash-lite` | tier_1_gemini (warm_backup) | ✅ Có | +3.11s | +3.51s | `SUCCESS` | Có dữ liệu hợp lệ | ⭐ **CHỌN** |

> **Dữ liệu Final Output:** `{"steps_count": 2, "reasoning": "Phương án bẻ nhỏ cứu hộ từ Warm Backup Gemini 3.5 Flash-Lite (cứu nguy khi 3.8 Flash bị treo)."}`

---

### 4. Toàn Bộ Google Bị Treo -> Groq Cloud LPU Cứu Hộ
- **Mô tả:** Cả hai model Gemini treo; nhánh Groq trả kết quả trước khi hết cửa sổ ưu tiên.
- **Thời gian bắt đầu:** `2026-11-12T01:33:20.215843+00:00`
- **Thời gian quyết định:** `2026-11-12T01:33:24.215864+00:00` (ở mốc `+4.000 giây`)
- **Model chiến thắng:** `qwen/qwen3.8-27b` (`tier_2_groq`)
- **Ghi chú:** Kỳ vọng gọi Groq tại 3.5s và chốt kết quả tại 4.0s của Khung A.

#### Timeline & Chi tiết Từng Model:

| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `gemini-3.8-flash` | tier_1_gemini | ✅ Có | +0.00s | +4.00s | `CANCELLED` | None | Không |
| `gemini-3.5-flash-lite` | tier_1_gemini | ✅ Có | +2.61s | +4.00s | `CANCELLED` | None | Không |
| `qwen/qwen3.8-27b` | tier_2_groq | ✅ Có | +3.50s | +3.76s | `SUCCESS` | Có dữ liệu hợp lệ | ⭐ **CHỌN** |

> **Dữ liệu Final Output:** `{"title": "Đề cương luận văn Thạc sĩ", "scope": "8 trang A4"}`

---

### 5. Chống Nghẽn Quota (Groq Qwen 429 TPM -> Groq GPT-OSS)
- **Mô tả:** Khi model chính trên Groq chạm ngưỡng 429 TPM, hệ thống tự động gọi model phụ thay vì báo lỗi
- **Thời gian bắt đầu:** `2026-11-12T01:33:24.215864+00:00`
- **Thời gian quyết định:** `2026-11-12T01:33:24.220301+00:00` (ở mốc `+0.004 giây`)
- **Model chiến thắng:** `openai/gpt-oss-20b` (`tier_2_groq`)
- **Ghi chú:** qwen/qwen3.8-27b chạm trần HTTP 429; hệ thống tự động fallback sang openai/gpt-oss-20b và nhận output thành công

#### Timeline & Chi tiết Từng Model:

| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `qwen/qwen3.8-27b` | tier_2_groq (primary) | ✅ Có | +0.00s | +0.00s | `FAILED` | RuntimeError: 429 Rate limit reached for qwen (TPM threshold hit) | Không |
| `openai/gpt-oss-20b` | tier_2_groq (secondary) | ✅ Có | +0.00s | +0.00s | `SUCCESS` | Có dữ liệu hợp lệ | ⭐ **CHỌN** |

> **Dữ liệu Final Output:** `{"empathetic_note": "Lịch làm việc đã được sắp xếp lại nhẹ nhàng cho bạn, đừng tự trách mình nhé.", "encouragement_tip": "Uống một ngụm nước ấm và vươn vai nhé."}`

---

### 6. Sập Toàn Bộ Cloud -> Thuần Python Bọc Lót & Anti-Hallucination
- **Mô tả:** Khi Cloud sập, Python tự sinh 3 bước chuẩn; khi LLM ảo giác deadline quá khứ, Python Guardrail can thiệp lật cờ is_complete=False
- **Thời gian bắt đầu:** `2026-11-12T01:33:24.220301+00:00`
- **Thời gian quyết định:** `2026-11-12T01:33:33.231712+00:00` (ở mốc `+9.011 giây`)
- **Model chiến thắng:** `pure_python_rule_based` (`tier_3_rule_based`)
- **Ghi chú:** Kỳ vọng trả dự phòng tại trần A/B trong khi các provider vẫn đang dọn dẹp.

#### Timeline & Chi tiết Từng Model:

| Model | Tầng | Đã gọi? | Bắt đầu (s) | Hoàn tất (s) | Trạng thái | Đầu ra / Lỗi | Chọn làm Final? |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `UF1 / gemini-3.8-flash` | tier_1_gemini | ✅ Có | +0.00s | +4.50s | `CANCELLED` | None | Không |
| `UF2 / gemini-3.8-flash` | tier_1_gemini | ✅ Có | +0.00s | +9.01s | `CANCELLED` | None | Không |
| `UF1 / gemini-3.5-flash-lite` | tier_1_gemini | ✅ Có | +2.61s | +4.50s | `CANCELLED` | None | Không |
| `UF2 / gemini-3.5-flash-lite` | tier_1_gemini | ✅ Có | +3.11s | +9.01s | `CANCELLED` | None | Không |
| `UF1 / qwen/qwen3.8-27b` | tier_2_groq | ✅ Có | +3.50s | +4.50s | `CANCELLED` | None | Không |
| `UF2 / qwen/qwen3.8-27b` | tier_2_groq | ✅ Có | +4.40s | +9.01s | `CANCELLED` | None | Không |

> **Dữ liệu Final Output:** `{"uf1_elapsed_s": 4.494441799994092, "uf1_tier": "tier_3_rule_based", "uf2_elapsed_s": 9.009175100014545, "uf2_steps": [{"order_index": 1, "title": "Chuẩn bị tài liệu và lập dàn ý cho: Dọn dẹp phòng làm việc", "expected_output": "Danh sách các ý chính hoặc dụng cụ cần thiết đã sẵn sàng.", "estimated_minutes": 20, "micro_task_prompt": "Đặt một tài liệu hoặc dụng cụ cần dùng cho 'Dọn dẹp phòng làm việc' trước mặt.", "cognitive_load": "light"}, {"order_index": 2, "title": "Thực hiện phần trọng tâm của: Dọn dẹp phòng làm việc", "expected_output": "Hoàn thành bản nháp hoặc phần khối lượng chính của công việc.", "estimated_minutes": 20, "micro_task_prompt": "Mở tài liệu hoặc công cụ làm việc của 'Dọn dẹp phòng làm việc'.", "cognitive_load": "medium"}, {"order_index": 3, "title": "Rà soát và hoàn thiện kết quả: Dọn dẹp phòng làm việc", "expected_output": "Kết quả đã được kiểm tra lại và sẵn sàng bàn giao/lưu trữ.", "estimated_minutes": 20, "micro_task_prompt": "Đánh dấu một chi tiết cần kiểm tra trong kết quả của 'Dọn dẹp phòng làm việc'.", "cognitive_load": "light"}], "guardrail_is_complete": false, "guardrail_has_conflict": true, "guardrail_questions": ["Hạn chót bạn nêu đang nằm ở thời điểm đã qua so với hiện tại, bạn xác nhận lại ngày giờ hoàn thành mới giúp mình nhé?"]}`

---
