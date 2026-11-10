# Demo local · Đối chiếu đầu ra UF1–UF2

## Khởi tạo database riêng

Trong PowerShell ở thư mục gốc của repo, chạy các lệnh sau. SQLite demo được Git bỏ qua. Các lệnh đặt cấu hình riêng cho terminal hiện tại.

```powershell
cd backend
$env:DATABASE_URL = 'sqlite+aiosqlite:///./demo_uf1_uf2.db'
$env:ENVIRONMENT = 'development'
$env:ENABLE_CALENDAR_SYNC_WORKER = 'false'
uv sync --frozen --group dev
uv run --frozen alembic upgrade head
uv run --frozen alembic current
uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8011
```

`alembic current` phải báo revision ở `head`. API dev tự tạo dữ liệu người dùng phát triển ở request đầu tiên cần người dùng. Mở terminal khác ở thư mục gốc để chạy UI.

```powershell
cd mobile
flutter run -d chrome --web-port=8081 --dart-define=API_BASE_URL=http://127.0.0.1:8011/api/v1
```

Chrome dùng cổng riêng để tách dữ liệu trình duyệt của demo. Với Android Emulator, chọn ID từ `flutter devices` và đổi URL thành `http://10.0.2.2:8011/api/v1`.

## Dữ liệu nhập thử

Tạo bản nháp mới cho từng ca. Chọn giờ rảnh còn ở tương lai, chẳng hạn ngày mai 08:00–17:00, và hạn chót lúc 18:00 ngày kia theo giờ Việt Nam. Không chốt lịch giữa các ca để tránh làm thay đổi quỹ giờ rảnh.

| Ca | Dữ liệu / thao tác | Kết quả phải có |
|---|---|---|
| OUT-01 · Đủ đầu ra | Nhập “Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang, gồm tóm tắt và một biểu đồ.” Thêm giờ rảnh và hạn. Bấm **Tạo phương án**. | Tóm tắt hiện phạm vi API trả về. API có `can_plan=true` sau khi đủ dữ liệu. Màn Kế hoạch đề xuất hiện đúng danh sách bước và đầu ra của từng bước trong candidate. |
| OUT-02 · Bỏ đầu ra ở mô tả | Nhập “Làm báo cáo khảo sát”, vẫn có giờ rảnh và hạn. Bỏ trống ô Phạm vi. | Nếu API thực sự trả phạm vi trống, kiểm tra `MISSING_SCOPE`, `field_path=scope`, `blocking=true` và `can_plan=false`. UI hiện câu hỏi bổ sung phạm vi và chưa mở Kế hoạch đề xuất. Nếu API đã điền phạm vi từ mô tả, UI phải hiện đúng giá trị đó. Không dùng kết quả này làm bằng chứng cho nhánh phạm vi trống. |
| OUT-03 · Bổ sung đầu ra | Từ OUT-02 đang bị chặn, điền “PDF 3 trang, có một biểu đồ” vào Phạm vi rồi gửi lại. | API lưu phạm vi mới. Khi đã đủ tất cả trường bắt buộc, `can_plan=true`, blocker phạm vi biến mất và UI mở Kế hoạch đề xuất. Các bước lấy từ candidate mới, không lấy từ bản nháp trước. |
| OUT-04 · Xóa đầu ra một bước | Trong Kế hoạch đề xuất, sửa một bước. Thử để trống rồi thử chỉ nhập dấu cách vào ô kết quả, giữ tên và số phút hợp lệ. | UI báo cần tên, kết quả và số phút hợp lệ. Không có PATCH thành công. Mở lại bước thấy đầu ra cũ. GET candidate vẫn trả giá trị cũ và revision không đổi. |
| OUT-05 · Sửa đầu ra hợp lệ | Sửa đầu ra của cùng bước thành “Bản PDF có biểu đồ đã kiểm tra” rồi lưu. | PATCH thành công, giá trị trên thẻ và ô sửa khớp `expected_output` của đúng `client_step_key` trong revision mới. |
| OUT-06 · AI trả bước thiếu đầu ra | Chỉ áp dụng khi console/log backend thực sự ghi nhận lỗi AI thiếu đầu ra và chuyển sang Python dự phòng. | Đối chiếu candidate cuối mà API gửi. Nếu API tạo candidate thành công, các bước dự phòng có đầu ra và UI hiển thị đúng dữ liệu đó. Nếu không tái hiện được log này, đánh dấu **Blocked** cho ca này. |

## Đối chiếu với console

1. Trên Chrome mở DevTools → Network, bật **Preserve log**. Với OUT-02, xem response `POST /drafts/{id}/analyze` để đối chiếu `data.summary.scope`, `can_plan` và `clarification_issues`.
2. Với ca đã tạo phương án, lấy `candidate_id` từ response `POST /drafts/{id}/candidates`. Mã và revision cũng hiện trên màn Kế hoạch đề xuất.
3. Đọc lại đúng candidate từ PowerShell. Sau khi sửa, chạy lại lệnh để lấy revision mới.

```powershell
$candidateId = 'DAN_ID_TU_RESPONSE'
$candidate = (Invoke-RestMethod "http://127.0.0.1:8011/api/v1/candidates/$candidateId").data
$candidate | Select-Object candidate_id, review_revision, decomposition_state
$candidate.steps | Sort-Object order_index |
    Select-Object order_index, client_step_key, title, expected_output |
    ConvertTo-Json -Depth 4
```

Một ca đồng bộ đạt khi UI và console dùng cùng `candidate_id` và `review_revision`, khớp số bước, thứ tự, tiêu đề và đầu ra. Câu AI sinh có thể khác giữa hai lần chạy, vì vậy hãy so dữ liệu của cùng một lần tạo phương án. Ghi **Pass / Fail / Blocked** kèm ID, revision và ảnh UI cho từng ca.
