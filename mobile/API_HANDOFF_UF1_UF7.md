# Bàn giao mobile: UF1–UF7

Đối chiếu với bản API mapping ngày 14/11 và mã backend trong checkout. Các luồng bên dưới cần chạy với cùng tài khoản để đối chiếu dữ liệu. Android thật dùng API_BASE_URL trỏ tới máy chạy backend hoặc staging.

| Luồng | Mobile và API | Kiểm tra trước khi bàn giao |
|---|---|---|
| Home / Google | Đăng nhập Google, tải lịch chính thức từ /schedule. | Kiểm tra OAuth với đúng package và SHA bản ký, sau đó vào lại app và mở Lịch. |
| UF1 | Android gửi m4a tới /voice/transcriptions (uf1_input); nếu lỗi, chạm mic để thử nhận diện trên máy hoặc gõ chữ. Chrome dùng nhận diện trình duyệt. Tóm tắt dùng /drafts, /analyze, PATCH và /confirm-summary. | Nói hoặc gõ: “Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang. Hạn 18:00 ngày kia. Tôi rảnh ngày mai 08:00–17:00.” Đối chiếu tên, phạm vi, giờ rảnh, hạn chót với response. Nếu backend báo can_plan=false, chưa được sang UF2. |
| UF2 | Tạo, sửa, chốt candidate; cập nhật trạng thái Google Calendar nếu có kết nối ghi. | Chốt một phương án khả thi, so sánh giờ từng phiên với lịch chính thức, rồi đối chiếu sự kiện Google nếu tài khoản đã liên kết. Kiểm tra phương án thiếu giờ không thể chốt. |
| UF3–UF4 | Start Run, lưu Thought metadata kèm ID Run nếu có. Sau phiên người dùng chủ động bấm chuyển ghi âm (thought_to_draft), sửa transcript rồi tạo draft. | Thu trong phiên và xác nhận không có yêu cầu STT lúc thu. Sau phiên bấm chuyển ghi âm, kiểm tra nội dung đi vào bản nháp. Nếu STT lỗi, thử nhập chữ. |
| UF5 | Kết thúc Run, đọc Check-in, gửi phản hồi. | Hết đồng hồ không được tự hoàn tất bước. Kiểm tra phản hồi trước khi chuyển màn. |
| UF6 | Màn chọn lý do và xem lịch hiện tại đã có; lịch chính thức giữ nguyên khi chưa có đề xuất. | Kiểm tra cả hai theme; thoát khỏi màn so sánh không thay đổi lịch. |
| UF7 | Đọc preferences và gửi settings/change-requests. Chưa hiện thẻ gợi ý khi chưa có nguồn dữ liệu. | Đổi nhịp làm/nghỉ, tải lại cài đặt và đối chiếu giá trị máy chủ trả về. |

Các điểm cần backend xác nhận trước khi nối tiếp:

- Lịch bận Google: endpoint planning-contexts có, nhưng checkout này chưa hỗ trợ nguồn Google được chọn khi lập phương án. Giữ cách lập lịch hiện tại để tránh bỏ sót lịch bận.
- Hoàn tác chốt lịch: cần API hoàn tác và cách thu hồi sự kiện đã đẩy sang Google.
- UF6: cần API upcoming-schedule-context, proposals và approve từ nhánh có contract hoàn chỉnh.
- UF7: cần API suggestions/current và suggestions/{id}/response.
- Kiểm tra quyền hệ thống, mic, DND và alarm trên điện thoại thật; Flutter test không thay thế phần này.
