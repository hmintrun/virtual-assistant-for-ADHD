# Đo UF1 với API thật

Gọi `extract_task_intent` với prompt/schema/Python guardrail hiện tại. Không mock provider.
Đo từ đầu service đến kết quả; không gồm STT, HTTP endpoint, DB hoặc Calendar.
Mỗi model riêng có cùng trần UF1; production giữ nguyên các mốc hedge.
Client tạo trước lượt đo, kết nối đầu tiên vẫn có thể cold. Các lượt chạy tuần tự, không retry ngoài service.
Timeout là chưa có kết quả hợp lệ trước trần, không phải thời gian model hoàn thành.
Cancelled trong production có thể do đã có winner, không đồng nghĩa model timeout.
Thành công chỉ nghĩa là có kết quả đúng schema; không chứng minh can_plan hay đầy đủ nghiệp vụ.
Trung vị/min/max bên dưới tính trên tất cả outcome (kể cả lỗi/timeout), không phải latency riêng của phản hồi thành công.

| Chế độ | Task | n | Thành công | Timeout | Lỗi | Trung vị (s) | Min–max (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| groq | small | 1 | 1 | 0 | 0 | 2.150 | 2.150–2.150 |
| groq | medium | 1 | 1 | 0 | 0 | 2.522 | 2.522–2.522 |
| groq | large | 1 | 1 | 0 | 0 | 3.268 | 3.268–3.268 |
| groq_secondary | small | 1 | 0 | 0 | 1 | 3.098 | 3.098–3.098 |
| groq_secondary | medium | 1 | 1 | 0 | 0 | 2.554 | 2.554–2.554 |
| groq_secondary | large | 1 | 0 | 0 | 1 | 3.243 | 3.243–3.243 |

Mẫu nhỏ, không suy ra p95/p99 hoặc timeout tối ưu từ bài đo này.

## Đầu vào

- **small** (177 ký tự): Sắp xếp 20 tài liệu PDF vào 3 thư mục môn học và đổi tên theo môn-ngày. Bắt đầu ngày 12/11/2026 lúc 19:00, rảnh 19:00-20:00 ngày đó. Hạn chót 12/11/2026 lúc 21:00, giờ Việt Nam.
- **medium** (402 ký tự): Viết báo cáo 8 trang về khảo sát thói quen học tập của 30 sinh viên, gồm mục tiêu, phương pháp, kết quả, 3 biểu đồ và kết luận; nộp một file PDF. Dữ liệu đã có trong bảng tính, không cần khảo sát thêm. Bắt đầu 12/11/2026 lúc 19:00. Rảnh ngày 12, 13 và 14/11/2026, mỗi ngày từ 19:00 đến 21:00. Hạn chót 15/11/2026 lúc 17:00 giờ Việt Nam. Chỉ dùng dữ liệu có sẵn, không đưa thông tin cá nhân vào báo cáo.
- **large** (1116 ký tự): Hoàn thành nguyên mẫu ứng dụng quản lý thư viện cho đồ án nhóm 4 người. Phạm vi gồm đăng nhập, phân quyền thủ thư và độc giả, quản lý 500 đầu sách, tìm kiếm theo tên/tác giả, mượn trả, theo dõi quá hạn và báo cáo thống kê tháng. Đầu ra là mã nguồn backend FastAPI, frontend web, database PostgreSQL, tài liệu API, 30 ca kiểm thử chức năng, báo cáo PDF 25 trang, 15 slide và video demo 5 phút. Không triển khai thanh toán, không gửi email thật và không triển khai lên cloud. Thiết kế giao diện đã có; cần rà soát tính nhất quán giữa API và màn hình, dữ liệu demo không chứa thông tin người thật. Tôi phụ trách tích hợp và báo cáo; các thành viên khác đã cung cấp module đăng nhập và danh mục sách. Bắt đầu 12/11/2026 lúc 18:00, hạn chót 30/11/2026 lúc 17:00 giờ Việt Nam. Tôi rảnh thứ Hai, thứ Tư và thứ Sáu từ 18:00 đến 21:00, thứ Bảy từ 09:00 đến 12:00 và từ 14:00 đến 17:00 trong khoảng ngày 12 đến 29/11/2026. Giữ nguyên các lịch học đã có, không tự đổi lịch công việc khác. Ưu tiên demo chạy ổn định, sau đó kiểm thử, cuối cùng hoàn thiện tài liệu; chỉ ghi nhận yêu cầu ở bước này, chưa chia bước hoặc chốt lịch.
- **missing_fields** (55 ký tự): Mình muốn viết báo cáo 2 trang gồm kết quả và kết luận.
