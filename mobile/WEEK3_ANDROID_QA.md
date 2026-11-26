# ProPlan VA · APK tuần 3

Bản release QA cài thử dùng cấu hình dev_user và kết nối qua USB. Bản APK này dùng chữ ký debug theo Gradle hiện tại. Dùng bản ký đã đăng ký OAuth khi xác minh Google Sign-In.

## Cài bản QA qua USB

1. Khởi động backend đúng nhánh tích hợp, có cổng 8000 và cấu hình dev_user phù hợp. Không đổi cài đặt Google thật của môi trường dùng chung.
2. Bật gỡ lỗi USB và chấp nhận quyền trên Redmi. Kiểm tra thiết bị để bảo đảm lệnh chọn đúng máy.
3. Chạy từ thư mục mobile sau khi lấy dependency bằng flutter pub get.

```powershell
flutter build apk --release --dart-define=AUTH_MODE=dev_user --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1
Get-FileHash -Algorithm SHA256 build/app/outputs/flutter-apk/app-release.apk
adb devices
adb reverse tcp:8000 tcp:8000
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

Nếu chưa có adb trong PATH, dùng adb.exe trong thư mục platform-tools của Android SDK. Nếu máy đã cài app khác chữ ký, dùng thiết bị thử khác hoặc gỡ bản cũ khi đã sao lưu dữ liệu thử nghiệm.

## Cấu hình APK dùng Google thật

Lấy API_BASE_URL HTTPS và Google Server Client ID công khai từ môi trường QA của backend. Đăng ký package com.proplan.proplan_va và dấu vân tay của chứng chỉ thực sự ký APK. Để backend xác thực Google Sign-In và cấp quyền Calendar, chuẩn bị cấu hình OAuth và callback trước khi chạy.

```powershell
flutter build apk --release --dart-define=API_BASE_URL=<api-https>/api/v1 --dart-define=GOOGLE_SERVER_CLIENT_ID=<google-server-client-id>
Get-FileHash -Algorithm SHA256 build/app/outputs/flutter-apk/app-release.apk
```

## Kiểm tra trên APK

- Google Sign-In bằng hai tài khoản QA. Kiểm tra đóng mở app, đăng xuất, hết hạn và tách dữ liệu lịch.
- Liên kết Google Calendar, tạo việc quanh một sự kiện bận, chốt kế hoạch rồi kiểm tra sự kiện trên lịch đích.
- Khởi động phiên, cất ghi âm trong lúc làm và mở lại sau phiên. Kiểm tra chuông, DND và thao tác mất mạng trên điện thoại.
- Ở UF5, thử cả ba lựa chọn bỏ qua. Bấm Hoàn tác trong 5 giây rồi xác nhận cùng phiên vẫn cho check-in. Thử đóng app trong cửa sổ 5 giây và mở lại trước hoặc sau khi hết hạn. Lặp lại với mạng tắt.
- Sau check-in Chưa xong, tạo đề xuất UF6 rồi thoát. Vào Lịch → Đề xuất đổi lịch để mở lại. Duyệt bản còn hiệu lực, thử từ chối và thử mở bản lưu khi mất mạng.
- Ở UF7, kiểm tra cấu hình phiên đang chạy, mở đề xuất chờ duyệt nếu có, rồi kiểm tra chấp nhận/sửa/bỏ qua gợi ý khi tài khoản QA có mẫu đủ điều kiện.
- Dùng flutter run --profile với cùng cấu hình API trên Redmi. Đo nhập việc, mở so sánh lịch và chuyển màn focus bằng DevTools, ghi thiết bị, số khung hình chậm và thao tác đo.
