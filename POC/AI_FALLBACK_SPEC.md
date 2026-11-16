# Đặc tả dự phòng AI đa tầng cho UF1 và UF2

## 1. Phạm vi

`AIService` ưu tiên Gemini Primary và chạy song song nhánh dự phòng khi chờ lâu. Nếu các provider không trả được kết quả dùng được, backend xử lý theo nghiệp vụ của từng luồng.

| Nghiệp vụ | Phương thức | Khung thời gian |
| --- | --- | --- |
| UF1 — Trích xuất thông tin và hỏi làm rõ | `extract_task_intent` | A |
| UF2 — Sinh đề xuất chia bước cho Candidate | `decompose_task` | B |
| UF2 — Chỉnh Candidate bằng ngôn ngữ tự nhiên | `adjust_candidate_by_nl` | A |

## 2. Mốc gọi và timeout

Các giá trị dưới đây là mặc định trong [cấu hình backend](../../backend/app/core/config.py). Bộ điều phối tính thời gian từ lúc bắt đầu xử lý yêu cầu AI. Mục tiêu phản hồi thông thường là **≤ 2,5 giây**. Timeout giới hạn thời gian chờ provider trong bộ điều phối. Độ trễ API đầu cuối còn gồm xử lý Python, truy vấn dữ liệu và truyền mạng.

| Mốc | Khung A | Khung B | Hành vi |
| --- | ---: | ---: | --- |
| Gọi Gemini Primary (`gemini-3.8-flash`) | 0 giây | 0 giây | Chạy ngay khi Gemini khả dụng |
| Gọi Gemini Warm Backup (`gemini-3.5-flash-lite`) | 2,6 giây | 3,1 giây | Chạy song song nếu Primary còn chờ |
| Gọi Groq (`qwen/qwen3.8-27b`) | 3,5 giây | 4,4 giây | Chạy nếu Groq khả dụng và chưa có kết quả dự phòng |
| Hết cửa sổ ưu tiên Primary | 4,0 giây | 7,5 giây | Trả kết quả dự phòng đã có. Nếu chưa có, tiếp tục chờ trong ngân sách còn lại |
| **Timeout chung** | **4,5 giây** | **9,0 giây** | Trả kết quả dự phòng đã nhận hoặc xử lý lỗi theo luồng |

### Quy tắc chuyển tầng

1. Primary trả payload hợp lệ trước khi bộ điều phối chốt thì ưu tiên kết quả Primary. Kết quả Flash-Lite sẵn sàng sớm được giữ cho yêu cầu hiện tại. Nếu Primary lỗi, bộ điều phối có thể trả ngay kết quả dự phòng đã có hoặc gọi Flash-Lite ngay khi nhánh này khả dụng.
2. Nếu cả hai model Gemini lỗi hoặc Primary lỗi khi thiếu model dự phòng, gọi Groq ngay nếu đã cấu hình. Nếu thiếu cấu hình Gemini ngay từ đầu, bắt đầu với Groq. Nếu cả hai provider đều không khả dụng, áp dụng xử lý lỗi của luồng ngay.
3. Nếu model Groq đầu tiên thất bại, thử model phụ `openai/gpt-oss-20b` nếu đã cấu hình. Các lỗi kích hoạt gồm `429`, lỗi gọi API và lỗi đọc hoặc kiểm tra payload. Hai model Groq chia sẻ phần ngân sách còn lại của cùng yêu cầu.
4. Khi đã chốt kết quả hoặc chạm timeout, bộ điều phối yêu cầu hủy các tác vụ còn chạy và trả quyền xử lý cho luồng ngay. Việc dọn kết nối của SDK tiếp tục riêng. Nếu mọi nhánh khả dụng đã lỗi, có thể xử lý lỗi sớm trước timeout.

## 3. Đầu ra và ràng buộc nghiệp vụ

| Luồng | Xử lý khi AI thất bại | Điều kiện nghiệp vụ |
| --- | --- | --- |
| UF1 | Python giữ nội dung và dữ liệu nháp đã có, gom các câu hỏi về trường còn thiếu hoặc cần xác nhận. Kết quả có `generation_tier=tier_3_rule_based` | Kiểm tra nội dung, phạm vi, khung giờ rảnh và deadline. Với payload LLM có deadline đã qua, Python đặt `has_conflict=true`, `is_complete=false` và thêm câu hỏi làm rõ nếu cần. Chỉ qua UF2 khi đủ dữ liệu và người dùng xác nhận |
| UF2 — Sinh Candidate | Python sinh ba bước theo nội dung, phạm vi và thời lượng tập trung đã cấu hình. Mỗi bước có đầu ra dự kiến và một micro-task khởi động 2 phút. Kết quả có `generation_tier=tier_3_rule_based` | Scheduler Python chia phiên, kiểm khả thi và giữ lịch các Task khác. Candidate phải được người dùng xem và xác nhận trước khi tạo lịch chính thức hoặc đồng bộ Google Calendar |
| UF2 — Chỉnh Candidate | API trả `503 AI_UNAVAILABLE`. Người dùng có thể thử lại hoặc chỉnh trực tiếp | Giữ Candidate hiện tại, Steps, Sessions, review revision và lịch chính thức. Chỉ nhánh sinh Candidate được dùng Python để phân rã dự phòng |

Kết quả từ Gemini Primary và Flash-Lite đều mang `tier_1_gemini`. Hai model Groq đều mang `tier_2_groq`. Dùng `generation_tier` để nhận diện tầng phục vụ. Khi cần xác định model thắng, cần trace theo từng lời gọi.

Trong lúc chờ phân rã UF2, UI hiển thị vòng thở hoặc shimmer kèm dòng “AI đang bẻ nhỏ công việc giúp bạn...” theo [đặc tả UX](../../Project.md#52-hiệu-năng--độ-trễ-performance--latency).

## 4. Tiêu chí kiểm thử

| Tình huống | Kết quả cần quan sát |
| --- | --- |
| Cả hai Gemini lỗi nhanh | Groq bắt đầu trước mốc hedge và trả `tier_2_groq` |
| Flash-Lite trả sớm, Primary vẫn trả trong cửa sổ ưu tiên | Chọn kết quả Primary |
| Primary treo trong UF2, Flash-Lite đã trả | Trả kết quả Flash-Lite quanh mốc 7,5 giây và yêu cầu hủy Primary |
| Hai Gemini treo trong UF1, Groq trả trước mốc 4,0 giây | Groq bắt đầu quanh mốc 3,5 giây và được chọn khi hết cửa sổ ưu tiên |
| Groq model chính trả `429` | Gọi model phụ trong ngân sách còn lại |
| Tất cả provider treo và dọn dẹp chậm sau khi hủy | UF1 và UF2 trả dự phòng quanh mốc 4,5 và 9,0 giây. Kiểm riêng việc UF1 chặn deadline quá khứ và UF2 giữ thời lượng cấu hình |
| AI lỗi khi chỉnh Candidate | Trả `503 AI_UNAVAILABLE` và giữ dữ liệu của phương án hiện tại |

Chạy các bài kiểm thử hồi quy từ thư mục `backend/`.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ai_service.py tests/test_local_planning_regressions.py
```

Các ca giả lập kiểm thứ tự gọi, chọn kết quả và trả quyền xử lý khi hết giờ. Đo độ trễ đầu cuối và khả năng truy cập từng model cần chạy riêng với provider thật.

Nguồn chuẩn nghiệp vụ và NFR là [Project.md](../../Project.md). Logic điều phối và kiểm tra đầu ra nằm trong [AIService](../../backend/app/services/ai_service.py).
