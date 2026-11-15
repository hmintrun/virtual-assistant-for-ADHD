# Bàn giao mobile Week 3

Nhánh `feat/tan-w3-mobile`. Bản debug dùng `AUTH_MODE=dev_user` để kiểm thử backend local. Kiểm thử đăng nhập và Calendar thật cần build riêng với cấu hình Google của team.

## Chạy local

Khởi động FastAPI theo [hướng dẫn demo](FRONTEND_DEMO_APK_GUIDE.md). Dùng SQLite demo riêng và tắt worker đồng bộ Calendar nếu chỉ kiểm dữ liệu local. Từ thư mục `mobile`:

| Môi trường | Lệnh |
| --- | --- |
| Chrome | `flutter run -d chrome --dart-define=AUTH_MODE=dev_user --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1` |
| Android Emulator | `flutter run -d DEVICE_ID --dart-define=AUTH_MODE=dev_user --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1` |
| Android thật | `flutter run -d DEVICE_ID --dart-define=AUTH_MODE=dev_user --dart-define=API_BASE_URL=http://IP_MAY_TINH:8000/api/v1` |

Dùng `flutter devices` để lấy ID. Điện thoại thật và máy tính cần thấy nhau qua LAN, và FastAPI cần lắng nghe trên `0.0.0.0`.

## Ca nghiệm thu trên APK ứng viên

| Luồng | Thao tác | Kết quả cần thấy |
| --- | --- | --- |
| UF1 | Tạo nháp “Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang có một biểu đồ. Hạn 18:00 ngày kia. Tôi rảnh ngày mai 08:00–17:00”, rồi mở việc mới thiếu phạm vi. Đối chiếu JSON create/analyze với UI. | Nháp đầu hiển thị đúng dữ liệu backend đã trích. Nháp thứ hai không mang giờ, hạn hoặc phạm vi từ nháp đầu. Tạo phương án chỉ bật khi `can_plan=true`. |
| UF2 | Kiểm một ca đủ giờ và một ca thiếu giờ. Chốt ca đủ giờ. | Thiếu giờ thấy số phút thiếu. Phiên đã chốt xuất hiện trên Trang chủ/Lịch và các công việc khác giữ nguyên. |
| UF3 | Bắt đầu đúng phiên, thử phiên chưa đến lượt, tắt màn hình, mở lại, tắt mạng giữa phiên. | Chặn sai thứ tự có đường về lịch. Timer tiếp tục qua đổi trạng thái. Ghi thời điểm chuông thực phát và trạng thái DND. |
| UF4 | Giữ mic lúc đang chạy, thả để lưu, thử bị từ chối quyền hoặc lỗi ghi, kết nối lại sau khi lưu ngoại tuyến. | Phiên tiếp tục chạy. Lỗi lặp đủ ba lần mở đường nhập chữ. Raw audio không bị gửi đi nhận diện khi đang focus. |
| UF5 | Kiểm Hoàn thành, Chưa xong, Việc đột xuất, ba phạm vi Bỏ qua, và không phản hồi. Thử Hoàn tác trước 5 giây và thử gửi khi mất mạng, đóng/mở lại app. | Hết giờ không tự hoàn thành phiên/bước. Undo giữ kết quả cũ. Khi nối mạng lại chỉ ghi một kết quả, đúng phạm vi đã chọn. |
| UF6 | Mở so sánh rồi đóng app, quay lại sau khi khung giờ cũ trôi qua. Thử duyệt, từ chối và mở bản cache ngoại tuyến. | Lịch chính thức giữ nguyên cho tới khi duyệt hợp lệ. Bản quá hạn cần kiểm/tính lại, bản cache không có quyền duyệt. |
| UF7 | Đổi hệ số đệm, nhịp tập trung/nghỉ và mốc nhắc khi đang có phiên, xem lại đề xuất chờ duyệt. | Phiên đang chạy giữ cấu hình cũ. Thay đổi ảnh hưởng lịch tương lai hiển thị ở UF6. |
| Google | Cài mới, đăng nhập hai tài khoản lần lượt, thử hết phiên, từ chối/thu hồi Calendar, chọn lịch có một sự kiện bận, thử ghi lịch sau khi chốt. | Dữ liệu tách theo tài khoản. Từ chối Calendar vẫn nhập giờ thủ công. Phiên tránh khoảng bận và đồng bộ không trùng. |

Ghi trạng thái Pass/Fail/Blocked, SHA nguồn, SHA-256 APK, thiết bị/Android, API và ảnh hoặc log đã che dữ liệu nhạy cảm.

## Phối hợp trước khi chốt release

- Thành và Khải chạy ca máy thật, đo chuông/Doze, DND, mic, mất mạng và callback Calendar.
- Hưng và Khải xác nhận HTTPS backend, chứng chỉ ký Android đã đăng ký cho `com.proplan.proplan_va`, Google Client ID và audience, callback Calendar và hai tài khoản QA. Dùng cấu hình đó cho APK release.
- Trung phối hợp xác nhận phần đọc câu hỏi Check-in và hành vi DND khi làm tiếp ngay. Đường nhập chữ vẫn dùng được trong bản hiện tại.
- Tan chạy lại UI trên APK cuối, ghi SHA-256 và chốt bằng chứng W3-20. Thành viên khác cài thử theo hướng dẫn ở trên cho W3-21.
