# UF1: thời gian từng model trả về khi bỏ timeout

Đơn vị: giây. Đo toàn bộ phản hồi API, không phải time-to-first-token. Tắt deadline điều phối và timeout SDK trong script benchmark; không đổi app production.
Giữ nguyên testcase, context 09:00 ngày 11/11/2026, prompt, schema, thinking và temperature của lượt trước. Không retry, không đổi model khi lỗi. Server vẫn có thể trả lỗi/giới hạn riêng.
Chạy tuần tự, client tái sử dụng. Hai lượt cùng model Groq cách nhau tối thiểu 65 giây tính từ khi lượt trước kết thúc; thời gian nghỉ không cộng vào latency.
Chưa đo HTTP endpoint, STT, DB, Calendar hay mobile. JSON lưu raw response, usage và checks; đúng schema không chứng minh can_plan.

## Từng lượt

| Model | Testcase | Lượt | API trả về (s) | Tổng service (s) | Kết quả |
|---|---|---:|---:|---:|---|
| gemini-3.8-flash | small | 1 | 5.803 | 5.822 | success |
| gemini-3.8-flash | small | 2 | 4.834 | 4.838 | success |
| gemini-3.8-flash | small | 3 | 5.997 | 5.999 | success |
| gemini-3.8-flash | medium | 1 | 8.415 | 8.448 | success |
| gemini-3.8-flash | medium | 2 | 7.658 | 7.662 | success |
| gemini-3.8-flash | medium | 3 | 12.961 | 12.965 | success |
| gemini-3.8-flash | large | 1 | 7.205 | 7.209 | success |
| gemini-3.8-flash | large | 2 | 10.904 | 10.907 | success |
| gemini-3.8-flash | large | 3 | 10.452 | 10.455 | success |
| gemini-3.8-flash | missing_fields | 1 | 3.857 | 3.861 | success |
| gemini-3.5-flash-lite | small | 1 | 2.105 | 2.108 | success |
| gemini-3.5-flash-lite | small | 2 | 1.957 | 1.960 | success |
| gemini-3.5-flash-lite | small | 3 | 2.397 | 2.401 | success |
| gemini-3.5-flash-lite | medium | 1 | 2.842 | 2.846 | success |
| gemini-3.5-flash-lite | medium | 2 | 2.969 | 2.972 | success |
| gemini-3.5-flash-lite | medium | 3 | 2.732 | 2.735 | success |
| gemini-3.5-flash-lite | large | 1 | 3.073 | 3.076 | success |
| gemini-3.5-flash-lite | large | 2 | 3.091 | 3.096 | success |
| gemini-3.5-flash-lite | large | 3 | 2.985 | 2.988 | success |
| gemini-3.5-flash-lite | missing_fields | 1 | 1.693 | 1.697 | success |
| qwen/qwen3.8-27b | small | 1 | 2.087 | 2.091 | success |
| qwen/qwen3.8-27b | small | 2 | 2.211 | 2.217 | success |
| qwen/qwen3.8-27b | small | 3 | 1.619 | 1.622 | success |
| qwen/qwen3.8-27b | medium | 1 | 2.592 | 2.595 | success |
| qwen/qwen3.8-27b | medium | 2 | 3.100 | 3.104 | success |
| qwen/qwen3.8-27b | medium | 3 | 2.562 | 2.567 | success |
| qwen/qwen3.8-27b | large | 1 | 3.296 | 3.300 | success |
| qwen/qwen3.8-27b | large | 2 | 3.366 | 3.369 | success |
| qwen/qwen3.8-27b | large | 3 | 3.266 | 3.270 | success |
| qwen/qwen3.8-27b | missing_fields | 1 | 1.549 | 1.553 | success |
| openai/gpt-oss-20b | small | 1 | 3.866 | 3.870 | success |
| openai/gpt-oss-20b | small | 2 | 2.023 | 2.026 | success |
| openai/gpt-oss-20b | small | 3 | 1.842 | 1.845 | success |
| openai/gpt-oss-20b | medium | 1 | 2.232 | 2.235 | success |
| openai/gpt-oss-20b | medium | 2 | 3.224 | 3.227 | schema_error (ValidationError) |
| openai/gpt-oss-20b | medium | 3 | 2.323 | 2.326 | success |
| openai/gpt-oss-20b | large | 1 | 3.577 | 3.579 | api_error (400) |
| openai/gpt-oss-20b | large | 2 | 3.194 | 3.197 | api_error (400) |
| openai/gpt-oss-20b | large | 3 | 2.423 | 2.425 | schema_error (ValidationError) |
| openai/gpt-oss-20b | missing_fields | 1 | 1.763 | 1.767 | success |

## Tổng hợp phản hồi hợp lệ

Trung vị/min/max chỉ tính phản hồi đúng schema; giữ lỗi riêng trong bảng từng lượt.

| Model | Testcase | Đúng schema / tổng | Trung vị (s) | Min–max (s) |
|---|---|---:|---:|---:|
| gemini-3.8-flash | small | 3/3 | 5.803 | 4.834–5.997 |
| gemini-3.8-flash | medium | 3/3 | 8.415 | 7.658–12.961 |
| gemini-3.8-flash | large | 3/3 | 10.452 | 7.205–10.904 |
| gemini-3.8-flash | missing_fields | 1/1 | 3.857 | 3.857–3.857 |
| gemini-3.5-flash-lite | small | 3/3 | 2.105 | 1.957–2.397 |
| gemini-3.5-flash-lite | medium | 3/3 | 2.842 | 2.732–2.969 |
| gemini-3.5-flash-lite | large | 3/3 | 3.073 | 2.985–3.091 |
| gemini-3.5-flash-lite | missing_fields | 1/1 | 1.693 | 1.693–1.693 |
| qwen/qwen3.8-27b | small | 3/3 | 2.087 | 1.619–2.211 |
| qwen/qwen3.8-27b | medium | 3/3 | 2.592 | 2.562–3.100 |
| qwen/qwen3.8-27b | large | 3/3 | 3.296 | 3.266–3.366 |
| qwen/qwen3.8-27b | missing_fields | 1/1 | 1.549 | 1.549–1.549 |
| openai/gpt-oss-20b | small | 3/3 | 2.023 | 1.842–3.866 |
| openai/gpt-oss-20b | medium | 2/3 | 2.278 | 2.232–2.323 |
| openai/gpt-oss-20b | large | 0/3 | — | — |
| openai/gpt-oss-20b | missing_fields | 1/1 | 1.763 | 1.763–1.763 |

## Testcase nguyên văn

- **small**: Sắp xếp 20 tài liệu PDF vào 3 thư mục môn học và đổi tên theo môn-ngày. Bắt đầu ngày 12/11/2026 lúc 19:00, rảnh 19:00-20:00 ngày đó. Hạn chót 12/11/2026 lúc 21:00, giờ Việt Nam.
- **medium**: Viết báo cáo 8 trang về khảo sát thói quen học tập của 30 sinh viên, gồm mục tiêu, phương pháp, kết quả, 3 biểu đồ và kết luận; nộp một file PDF. Dữ liệu đã có trong bảng tính, không cần khảo sát thêm. Bắt đầu 12/11/2026 lúc 19:00. Rảnh ngày 12, 13 và 14/11/2026, mỗi ngày từ 19:00 đến 21:00. Hạn chót 15/11/2026 lúc 17:00 giờ Việt Nam. Chỉ dùng dữ liệu có sẵn, không đưa thông tin cá nhân vào báo cáo.
- **large**: Hoàn thành nguyên mẫu ứng dụng quản lý thư viện cho đồ án nhóm 4 người. Phạm vi gồm đăng nhập, phân quyền thủ thư và độc giả, quản lý 500 đầu sách, tìm kiếm theo tên/tác giả, mượn trả, theo dõi quá hạn và báo cáo thống kê tháng. Đầu ra là mã nguồn backend FastAPI, frontend web, database PostgreSQL, tài liệu API, 30 ca kiểm thử chức năng, báo cáo PDF 25 trang, 15 slide và video demo 5 phút. Không triển khai thanh toán, không gửi email thật và không triển khai lên cloud. Thiết kế giao diện đã có; cần rà soát tính nhất quán giữa API và màn hình, dữ liệu demo không chứa thông tin người thật. Tôi phụ trách tích hợp và báo cáo; các thành viên khác đã cung cấp module đăng nhập và danh mục sách. Bắt đầu 12/11/2026 lúc 18:00, hạn chót 30/11/2026 lúc 17:00 giờ Việt Nam. Tôi rảnh thứ Hai, thứ Tư và thứ Sáu từ 18:00 đến 21:00, thứ Bảy từ 09:00 đến 12:00 và từ 14:00 đến 17:00 trong khoảng ngày 12 đến 29/11/2026. Giữ nguyên các lịch học đã có, không tự đổi lịch công việc khác. Ưu tiên demo chạy ổn định, sau đó kiểm thử, cuối cùng hoàn thiện tài liệu; chỉ ghi nhận yêu cầu ở bước này, chưa chia bước hoặc chốt lịch.
- **missing_fields**: Mình muốn viết báo cáo 2 trang gồm kết quả và kết luận.

Chạy lại từ `backend`:

```powershell
.venv/Scripts/python.exe scripts/benchmark_uf1_unbounded.py
```

Mẫu nhỏ; không suy ra p95/p99 hay timeout tối ưu. Mỗi lần chạy có thể có biến động mạng/provider.
