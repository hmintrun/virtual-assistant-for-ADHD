# Kết quả kiểm thử UF1 với API thật — 11/11/2026

## Kết luận từ lượt đo chính

**Cấu hình hiện tại không trả được kết quả AI hợp lệ trong 9/9 lượt UF1 có đủ thông tin, trên ba cỡ task.** Cả 9 lượt đều chạm trần khoảng 4,5 giây và chuyển sang Python Tier 3. Đây là kết quả mẫu nhỏ trong môi trường local tại thời điểm đo, không phải tỷ lệ lỗi dài hạn.

Gemini chính chưa trả kết quả hợp lệ trước trần. Flash-Lite hoạt động khi chạy riêng, nhưng thời gian quan sát thường vượt ngân sách 1,9 giây còn lại khi khởi chạy ở mốc 2,6 giây. Groq vừa chịu cửa sổ khoảng 1 giây, vừa gặp quota 429 và có lỗi schema. Không nên xem thời gian trả lỗi nhanh là tốc độ suy luận của model.

## Phạm vi và cách đo

- Chạy `AIService.extract_task_intent` thật với System Prompt, JSON schema, SDK, bộ điều phối và Python temporal guardrail hiện có; không mock provider.
- Commit nền `1ab4a25`; SDK `google-genai=2.34.0`, `groq=1.7.0`, `pydantic=2.14.2`.
- Lượt chính: 17:11:14–17:13:59 ngày 11/11/2026, múi giờ Việt Nam. 50 lần gọi service, 78 lần gọi model: 3 cỡ task × 5 chế độ × 3 lần, cộng 5 lượt thiếu thông tin.
- Bốn chế độ đo từng model giữ nguyên trần UF1 4,5 giây, chỉ bật model mục tiêu. Chế độ thứ năm giữ nguyên toàn bộ chuỗi điều phối đang cấu hình. Thay đổi chỉ trong đối tượng Settings của script, không sửa `.env` hay mã nghiệp vụ.
- Gemini chính dùng thinking LOW; Flash-Lite dùng MINIMAL như nhánh backup thực tế. Groq giữ nguyên temperature 0,2 và JSON-object mode.
- Mỗi service dùng lại client; không tính thời gian tạo client nhưng lượt mạng đầu tiên vẫn có thể cold. Chạy tuần tự, xoay thứ tự model giữa các vòng, nghỉ 0,5 giây sau mỗi lượt.
- Đo wall-clock từ lúc vào service đến lúc nhận kết quả; ghi riêng thời điểm bắt đầu và thời lượng từng nhánh. Không gồm STT/TTS, HTTP endpoint, DB, Calendar hay giao diện mobile. Không ghi Task hoặc lịch thật.
- Context dùng mốc cố định `2026-11-11T09:00:00+07:00` để tái lập; các task đều bắt đầu từ ngày 12/11. Nội dung và dữ liệu kiểm thử đều giả lập.
- `success` nghĩa là có kết quả đúng schema sau guardrail, không có nghĩa đã vượt kiểm tra `can_plan`. `timeout` nghĩa là bị cắt trước khi có kết quả hợp lệ; không đo được thời gian hoàn thành nếu cho model chạy lâu hơn. `cancelled` của nhánh thua trong production không tự động đồng nghĩa timeout của model đó.

## Ba cỡ task

| Cỡ | Nội dung | Độ dài đầu vào |
|---|---|---:|
| Nhỏ | Sắp xếp 20 PDF vào 3 thư mục, một khung giờ rảnh và deadline cụ thể | 177 ký tự |
| Vừa | Báo cáo khảo sát 8 trang, 3 biểu đồ, ba ngày rảnh | 402 ký tự |
| Lớn | Đồ án thư viện: backend, frontend, DB, 30 ca test, báo cáo, slide, video; nhiều khung giờ lặp | 1.116 ký tự |

Cỡ ở đây biểu thị độ phức tạp công việc và độ dài yêu cầu, không phải số token đầu ra cố định. UF1 chỉ trích xuất yêu cầu, chưa phân rã hay xếp lịch.

## Kết quả lượt chính: mỗi ô gồm 3 lần thử

Các khoảng giây bên dưới chỉ lấy **phản hồi đúng schema**; lỗi và timeout được ghi riêng.

| Model / chế độ | Nhỏ | Vừa | Lớn |
|---|---|---|---|
| `gemini-3.8-flash` | Timeout 3/3, ~4,51s | Timeout 3/3, ~4,51s | Timeout 3/3, ~4,51s |
| `gemini-3.5-flash-lite` | 1,798–2,030s; đạt 3/3 | 2,552–2,870s; đạt 3/3 | 2,920–3,565s; đạt 2/3, timeout 1/3 |
| `qwen/qwen3.8-27b` | HTTP 429: 3/3 | 2,312–2,622s; đạt 3/3 | HTTP 429: 3/3 |
| `openai/gpt-oss-20b` | 1,922s; đạt 1/3, 429: 2/3 | 2,508s; đạt 1/3, 429: 1/3, sai schema: 1/3 | HTTP 429: 3/3 |
| Chuỗi production | Timeout 3/3 | Timeout 3/3 | Timeout 3/3 |

429 có cả giới hạn input/output token mỗi phút của Qwen và TPM của GPT-OSS. Bài chạy liên tiếp dùng chung quota với các nhánh production nên không thể dùng các ô 429 để kết luận model xử lý task đó nhanh hay chậm. Một lỗi Qwen ghi rõ expected output 1.586 vượt limit 1.000; các lỗi khác ghi quota đã sử dụng và thời gian cần chờ.

## Đối chứng Groq với khoảng nghỉ để giảm ảnh hưởng quota

Sau lượt chính, chờ 70 giây rồi chạy thêm 6 lần gọi service. Mỗi lần gọi cách nhau ít nhất 35 giây; hai lần gọi cùng model cách nhau hơn 70 giây. Giữ nguyên prompt/schema và trần 4,5 giây. Mỗi ô chỉ có **1 mẫu**, không gộp với vòng chính để che mất lỗi quota.

| Model | Nhỏ | Vừa | Lớn |
|---|---|---|---|
| `qwen/qwen3.8-27b` | 2,150s, đúng schema | 2,522s, đúng schema | 3,268s, đúng schema |
| `openai/gpt-oss-20b` | 3,098s, thiếu `summary`, Pydantic loại | 2,554s, đúng schema | 3,243s, HTTP 400 `json_validate_failed` |

Lượt giãn cách không gặp 429. Qwen giữ được mốc bắt đầu và mốc kết thúc, biểu diễn bằng `kind=window` / `finish_by_at`; kiểm tra thời gian chấp nhận cả hai nhánh deadline/window hợp lệ. GPT-OSS vẫn có lỗi JSON/schema khi đã giảm áp lực quota. Tổng cộng hai đợt: **56 lượt UF1, 84 lần gọi model thật**.

## Phát hiện nghiệp vụ và đề xuất

1. **Cao — Cửa sổ fallback không đủ trong mẫu đo.** Theo `Project.md` mục NFR, UF1 giữ trần 4,5s; backup bắt đầu 2,6s và Groq bắt đầu 3,5s, lần lượt chỉ còn 1,9s và 1,0s. Cần đưa số liệu này vào thảo luận cập nhật chính sách hedge, ưu tiên thử khởi chạy backup sớm hơn trong một benchmark được thống nhất. Chưa đổi timing, model hay đặc tả trong task này.
2. **Cao — Quota làm hỏng kết quả so sánh tốc độ.** Dùng lượt giãn cách riêng bên dưới để kiểm chứng; không bỏ các lượt 429 khỏi bằng chứng gốc. Cần đánh giá ngân sách token và quota thật trước khi dựa vào Groq làm fallback.
3. **Vừa — Đúng JSON chưa đủ đúng UF1.** Trong lượt chính, GPT-OSS bỏ `time_constraint` ở task nhỏ và bỏ `start_at` ở task vừa dù đầu vào nêu rõ; Flash-Lite bỏ `start_at` trong một lượt task nhỏ. Những phản hồi này vẫn có `is_complete=true`. Đây là lỗi ở đầu ra extraction được quan sát, chưa chứng minh endpoint cuối cho phép xác nhận sai vì benchmark không chạy validation `can_plan` của toàn API.
4. **Vừa — GPT-OSS có kết quả thiếu `summary`.** Pydantic từ chối ở lượt task vừa thứ ba và service trả Tier 3. Không tính lượt này là phản hồi thành công dù API đã trả dữ liệu.
5. **Ca thiếu thông tin:** yêu cầu “báo cáo 2 trang gồm kết quả và kết luận” không có giờ rảnh/deadline. Flash-Lite, Qwen và GPT-OSS đều có một kết quả hợp lệ không tự bịa các trường thiếu; chuỗi production trả Flash-Lite ở 4,419s. Gemini chính bị cắt ở 4,510s. Chỉ là một mẫu mỗi chế độ, chưa bao phủ đầy đủ hallucination/clarification.

Không suy ra p95/p99, timeout tối ưu, chi phí tiền hoặc chất lượng tổng quát từ số mẫu này. Các timestamp vài mili giây lệch quanh 4,5s phản ánh phép đo/service scheduling; không phải cấu hình timeout khác nhau theo task.

## Chạy lại và bằng chứng

Từ thư mục `backend`:

```powershell
.venv/Scripts/python.exe scripts/benchmark_uf1_live.py
.venv/Scripts/python.exe scripts/benchmark_uf1_live.py --repetitions 1 --modes groq groq_secondary --cases small medium large --pause-seconds 35 --cooldown-seconds 70 --output ../docs/backend/qa_results/uf1_live_latency_20261111_paced.json
```

- Script: `backend/scripts/benchmark_uf1_live.py`.
- Toàn bộ đầu vào, output, kết quả kiểm tra và timeline: `uf1_live_latency_20261111.json`.
- Bảng thống kê đầy đủ: `uf1_live_latency_20261111.md`. Trung vị trong bảng đó tính trên mọi outcome, gồm cả lỗi/timeout.
- Lượt giãn cách: `uf1_live_latency_20261111_paced.json` và `.md`.
- Script đã kiểm tra bằng Ruff lint và format. Đây là live benchmark chạy thủ công, không được đưa vào unit test/CI mặc định.
