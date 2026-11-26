# Bộ test luồng Mobile

## Chuẩn bị

- Cài bản Mobile mới nhất trên Android Studio Emulator hoặc điện thoại Android.
- Khởi động Backend theo hướng dẫn trong `docs/backend/` và cấu hình Mobile trỏ đúng địa chỉ API.
- Dùng tài khoản và dữ liệu kiểm thử riêng; kiểm tra môi trường API/test database trước khi tạo hoặc xóa dữ liệu.
- Có thể chạy smoke test giao diện trên Chrome. DND, thông báo Android, thu âm và chạy nền cần kiểm tra trên Android.
- Ghi kết quả từng ca là **Pass / Fail / Blocked**, kèm thiết bị, phiên bản app, thời điểm và ảnh/log nếu lỗi. Các ca dưới đây là checklist để chạy, chưa phải kết quả đã xác nhận.

## Ca kiểm thử

| ID | Luồng | Thao tác | Kết quả mong đợi |
|---|---|---|---|
| APP-01 | Mở app / onboarding | Mở app lần đầu, đi qua giới thiệu và chọn giao diện; đóng/mở lại app | Tiến trình onboarding đúng; lựa chọn giao diện được giữ; không kẹt khi quay lại |
| APP-02 | Tài khoản | Mở tab Tài khoản; thử đăng nhập/đăng xuất nếu môi trường đã cấu hình | Nút điều hướng hoạt động; trạng thái chưa đăng nhập/lỗi mạng có đường tiếp tục; không hiện dữ liệu giả như dữ liệu thật |
| APP-03 | Trợ giúp / điều hướng | Mở tab Trợ giúp, quay lại, chuyển qua các tab chính | Các nút điều hướng mở đúng màn; nút quay lại không làm mất dữ liệu đang nhập |
| UF1-01 | Tạo việc | Tạo công việc từ mô tả, đi tới tóm tắt | Tiêu đề, phạm vi, hạn và giờ rảnh hiển thị đúng dữ liệu đã nhập |
| UF1-02 | Thiếu trường bắt buộc | Nhập mô tả công việc nhưng bỏ trống hạn chót hoặc giờ rảnh, yêu cầu phân tích | Màn làm rõ chỉ hiện các câu hỏi có `blocking=true`; không hiện câu hỏi tùy chọn hay thông báo AI dài; chưa thể tạo kế hoạch |
| UF1-03 | Bổ sung thông tin còn thiếu | Trả lời câu hỏi bắt buộc, gửi lại; thử mô tả có hạn chót/giờ rảnh trong văn bản | API cập nhật bản nháp; trường đã nhận diện được điền vào tóm tắt; chỉ yêu cầu chọn tay trường vẫn thiếu |
| UF1-04 | Sửa và xác nhận tóm tắt | Sửa tiêu đề/phạm vi/hạn chót/giờ rảnh rồi xác nhận | Giá trị đã sửa hiển thị nhất quán; chỉ tạo phương án khi API xác nhận đủ điều kiện |
| UF1-05 | Lưu nháp và tiếp tục | Lưu nháp, quay lại danh sách nháp, mở lại | Bản nháp được giữ và có thể tiếp tục; lỗi đồng bộ có thông báo dễ hiểu và có thao tác thử lại |
| UF2-01 | Tạo phương án và xem bước | Từ tóm tắt hợp lệ yêu cầu tạo phương án, xem các bước và lịch phiên | Phương án hiển thị dữ liệu API, hạn chót và các phiên; lỗi feasibility/stale được giải thích và có hướng quay lại sửa |
| UF2-02 | Chỉnh bước / xác nhận | Chỉnh bước hoặc chọn phần chưa chia, sau đó xác nhận | Thay đổi được gửi theo contract; không báo thành công nếu API từ chối; có thể quay lại sửa |
| UF2-03 | Xem lịch | Mở lịch, chuyển tháng, chọn ngày có phiên và ngày trống | Lịch tải được; phiên đúng ngày/giờ; trạng thái lỗi có nút thử lại, màn không tràn nội dung |
| UF3-01 | Nhắc trước phiên và khởi động | Mở phiên sắp tới, xem nhắc trước phiên và bắt đầu việc nhỏ 2 phút | Nội dung và hạn giờ đúng; có thể bỏ qua/bắt đầu tiếp theo theo thiết kế; không tạo phiên trùng |
| UF3-02 | Bắt đầu tập trung | Bắt đầu phiên, xác nhận quyền thông báo/DND nếu Android yêu cầu | Phiên chuyển sang đang tập trung; bộ đếm/thanh tiến độ cập nhật; app không tuyên bố DND đã bật nếu quyền bị từ chối |
| UF3-03 | Tạm dừng, nghỉ và tiếp tục | Trong phiên tạm dừng hoặc kết thúc khối để nghỉ, sau đó tiếp tục | Trạng thái và thời lượng còn lại hợp lý; quay lại app khôi phục đúng phiên đang chạy |
| UF3-04 | Mất mạng và nối lại | Tắt mạng khi phiên chạy, tiếp tục thao tác; bật mạng trở lại | Phiên vẫn chạy cục bộ; trạng thái ngoại tuyến dễ nhận biết; dữ liệu chờ đồng bộ được gửi lại khi có mạng, không nhân đôi |
| UF4-01 | Cất suy nghĩ thành công | Nhấn giữ mic, nói ngắn, thả để lưu; nghe lại bản ghi | Có trạng thái đang ghi và kết quả lưu; phiên tập trung vẫn tiếp diễn; phát lại đúng file |
| UF4-02 | Ghi âm lỗi và thử lại | Từ chối quyền mic hoặc gây lỗi lưu, thử lại tối đa 3 lần | Lỗi không làm dừng phiên; số lần thử hiển thị đúng; không báo lưu thành công khi chưa lưu |
| UF4-03 | Ghi chú dự phòng và quản lý bản ghi | Sau 3 lần lỗi, nhập ghi chú bằng bàn phím; mở danh sách suy nghĩ, xóa và hoàn tác nếu có | Ghi chú tay được lưu; xóa có snackbar hoàn tác; nội dung đã xóa chỉ khôi phục khi chọn hoàn tác trong thời hạn |
| UF5-01 | Check-in sau phiên | Hoàn tất phiên; ghi nhận phần đã làm, thời lượng thực tế và phần còn lại | Check-in lưu được; kết quả phiên và phần việc tiếp theo khớp dữ liệu; có thể bỏ qua trường tùy chọn |
| APP-04 | Lịch và bản nháp | Mở lịch, đổi tháng/ngày; mở danh sách nháp và một bản nháp | Lịch và nháp lấy từ nguồn dữ liệu hiện có; trạng thái trống/lỗi có hướng xử lý; không lộ lỗi kỹ thuật dài |

## Kiểm tra hồi quy giao diện

- Chạy `flutter test` trong thư mục `mobile/`; kiểm tra thêm `flutter analyze`.
- Chạy UF1-02 trên cả sáng và tối, kích thước màn hình nhỏ và lớn: chỉ câu hỏi trường bắt buộc còn thiếu xuất hiện; không có câu hỏi tùy chọn, đoạn giải thích dài hoặc exception thô.
- Kiểm tra chức năng vẫn còn đường vào sau khi rút gọn chữ: lưu nháp, đổi hạn, sửa giờ rảnh, điều khiển ghi âm/ghi chú và các nút thử lại.
- Trên Android thật: kiểm tra vùng an toàn cạnh thanh điều hướng, quyền mic, thông báo, DND, khóa/mở app và mất mạng.
- Trên Chrome: kiểm tra bố cục và API smoke test khi backend cho phép CORS; không dùng kết quả Chrome để kết luận các tính năng Android native hoạt động.
