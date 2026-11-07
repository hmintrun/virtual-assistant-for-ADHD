# Hướng dẫn chạy thử Mobile và xuất APK

> **Đối tượng:** thành viên cần chạy UI Mobile hoặc tích hợp Backend để kiểm tra APK.
> **Thư mục app:** `mobile/` trong repo.
> **Trạng thái:** đây là hướng dẫn thao tác. APK phải được build và chạy thử trên commit định bàn giao; tài liệu này không xác nhận build hiện tại đã thành công.

## 1. Chuẩn bị

- Cài Flutter, Android Studio và Android SDK; `flutter doctor` nên không báo thiếu Android toolchain.
- Mở terminal tại thư mục gốc repo, kiểm tra thiết bị:

```powershell
flutter doctor
flutter devices
```

- Cài dependency Mobile một lần:

```powershell
cd mobile
flutter pub get
```

Nếu PowerShell đang ở `mobile/`, không chạy `cd mobile` lần nữa.

## 2. Khởi động Backend khi cần dữ liệu/API thật

Mở **terminal riêng** tại thư mục gốc repo:

```powershell
cd backend
uv sync --frozen --group dev
uv run --frozen python scripts/check_environment.py --prepare-env
```

Kiểm tra `backend/.env` đang trỏ tới database mà nhóm muốn dùng. Nếu cần tạo/cập nhật schema, làm theo hướng dẫn Backend và migration hiện hành trong `backend/README.md` và `docs/backend/backend_testing_setup.md`; không dùng database cá nhân chứa dữ liệu quan trọng cho demo.

Khởi động server để emulator/điện thoại truy cập được từ ngoài máy tính:

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Giữ terminal này mở. Trên máy tính, có thể mở `http://127.0.0.1:8000/docs` để xem API. Nếu Windows Firewall hỏi quyền cho Python, cho phép trên mạng riêng đang dùng.

## 3. Chạy demo trên Chrome

Từ terminal thứ hai, vào `mobile/` rồi chạy:

```powershell
cd mobile
flutter run -d chrome --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1
```

Chrome trên cùng máy truy cập Backend qua `127.0.0.1`. Backend hiện có CORS middleware; nếu trình duyệt vẫn báo CORS hoặc `OPTIONS 405`, kiểm tra Backend đang chạy đúng checkout mới nhất và xem method/route trong `/docs`.

Chrome phù hợp để duyệt UI và các luồng không phụ thuộc Android. Không dùng Chrome để nghiệm thu chuông Android, DND, exact alarm hoặc hành vi ghi âm khi máy khóa.

## 4. Chạy trên Android Studio Emulator

1. Mở Android Studio, mở thư mục `mobile/` của repo.
2. Mở **Device Manager**, tạo hoặc khởi chạy một máy ảo Android.
3. Trong terminal tại `mobile/`, xem ID máy ảo:

```powershell
flutter devices
```

4. Chạy app bằng ID vừa thấy, ví dụ:

```powershell
flutter run -d emulator-5554
```

`ApiClient` tự dùng `http://10.0.2.2:8000/api/v1` trên Android Emulator để gọi Backend đang chạy trên máy tính. Nếu cần đổi địa chỉ, truyền `--dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1`.

## 5. Chạy trên điện thoại Android thật

1. Bật **Tùy chọn nhà phát triển** và **Gỡ lỗi USB** trên điện thoại; nối USB và chấp nhận hộp thoại cho phép gỡ lỗi.
2. Trên máy tính, kiểm tra:

```powershell
flutter devices
```

Nếu chưa thấy điện thoại, thử `adb devices`; kiểm tra lại cáp có truyền dữ liệu, chế độ USB và quyền gỡ lỗi.

3. Điện thoại và máy tính phải cùng mạng Wi-Fi. Trên Windows chạy `ipconfig`, lấy IPv4 của adapter Wi-Fi, ví dụ `192.168.1.25`.
4. Từ `mobile/`, chạy app với IP máy tính thật:

```powershell
flutter run -d DEVICE_ID --dart-define=API_BASE_URL=http://192.168.1.25:8000/api/v1
```

Thay `DEVICE_ID` bằng ID trong `flutter devices` và thay IP mẫu bằng IPv4 của máy bạn. Không dùng `localhost` hay `127.0.0.1` trong URL trên điện thoại: chúng trỏ về chính điện thoại. Backend phải bind `0.0.0.0` như mục 2.

Khi nghiệm thu focus, cấp quyền cần thiết cho thông báo, micro, báo thức và Không làm phiền theo hướng dẫn của app/Android. Trên Xiaomi/HyperOS, kiểm tra thêm Tự khởi chạy và tối ưu pin; kết quả có thể khác giữa các hãng máy.

## 6. Xuất APK

Đứng trong thư mục `mobile/`. Nên build **debug APK trước** để thử nội bộ:

```powershell
flutter build apk --debug
```

Nếu APK cần gọi Backend trên điện thoại thật, truyền IP LAN của máy đang chạy Backend:

```powershell
flutter build apk --debug --dart-define=API_BASE_URL=http://192.168.1.25:8000/api/v1
```

Thay IP mẫu trước khi chạy. Máy nhận APK phải truy cập được IP/port Backend đó; URL LAN của máy người build thường không dùng được khi gửi APK cho người khác.

APK nằm tại:

```text
mobile/build/app/outputs/flutter-apk/app-debug.apk
```

Build bản release nội bộ bằng:

```powershell
flutter build apk --release --dart-define=API_BASE_URL=https://YOUR_BACKEND_HOST/api/v1
```

File đầu ra là `mobile/build/app/outputs/flutter-apk/app-release.apk`. **Lưu ý:** cấu hình Gradle hiện dùng debug signing cho release để thử nghiệm. Đây chưa phải APK phát hành; trước khi phân phối cần cấu hình khóa ký release và URL Backend HTTPS ổn định. HTTP LAN chỉ phù hợp kiểm tra nội bộ bằng debug APK; không bật cleartext rộng cho bản phát hành.

## 7. Checklist trước khi gửi APK cho VQH

- Ghi lại commit SHA của Mobile và Backend được dùng để build.
- Ghi rõ build `debug` hay `release`, thiết bị/Android version và `API_BASE_URL`.
- Xác nhận Backend chạy, database/migration đúng môi trường và thiết bị kết nối được tới host:port.
- Cài APK sạch trên thiết bị; kiểm tra mở app, đăng nhập/demo, luồng cần trình bày, kết nối API và trạng thái lỗi/offline.
- Với UF3–UF4: thử timer, khôi phục phiên, ghi âm/nhập chữ dự phòng; thử chuông, quyền DND và hành vi nền trên điện thoại thật.
- Không coi `flutter analyze` là bằng chứng APK đã build hoặc native behavior đã được nghiệm thu.

### Địa chỉ API mặc định trong app

`ApiClient` đọc `API_BASE_URL` lúc build/run; nếu không truyền thì mặc định:

| Môi trường | URL mặc định |
|---|---|
| Chrome/Web | `http://127.0.0.1:8000/api/v1` |
| Android Emulator | `http://10.0.2.2:8000/api/v1` |
| Windows/desktop | `http://127.0.0.1:8000/api/v1` |
| Điện thoại thật | Truyền IP LAN máy Backend bằng `--dart-define` |
