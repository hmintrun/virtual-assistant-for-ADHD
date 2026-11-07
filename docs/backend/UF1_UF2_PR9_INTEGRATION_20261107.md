# Tích hợp sửa UF1–UF2 với PR #9 — 07/11/2026

Nguồn: [PR #9](https://github.com/hmintrun/virtual-assistant-for-ADHD/pull/9), commit `9528f049474467e4d9a4b0e69cb6026fba341dc0`. Nhánh tích hợp: `codex/integrate-uf12-pr9`. Phạm vi: backend; giữ nguyên Mobile của PR và để Google Calendar tích hợp sau theo yêu cầu.

## Các lỗi đã xử lý

| Trường hợp | Hành vi sau sửa | Bằng chứng |
| --- | --- | --- |
| POST đầu tiên trên DB mới chưa có user demo | Khởi tạo dữ liệu development trong dependency trước service; không còn 404 do thiếu user | Regression API và Uvicorn/SQLite thật |
| AI timeout, mất kết nối hoặc thiếu expected output | Trả đề xuất Python dự phòng theo thời lượng user, giữ nguyên Summary/scope; vẫn phải review/xác nhận | Unit, API và pipeline fallback |
| Ngữ cảnh chỉ phủ một phần chân trời nhưng đã có lịch bận | Trả `409 CALENDAR_CONTEXT_UNAVAILABLE`, không tự tạo lịch trống | API: thiếu coverage, context mới và stale candidate |
| Thay context bằng khoảng hẹp làm mất lịch bận ngoài khoảng | Từ chối `422`; giữ context cũ | API: cập nhật context bị chặn |
| Lịch bắt đầu ngay khi sinh / user duyệt chậm | Chừa mặc định 300 giây; chốt lịch đã qua trả `409 CANDIDATE_EXPIRED`, không ghi Task/Session | API và pipeline với clock cố định |
| Mobile cũ gửi `selected_part_keys: null` | Chấp nhận khóa legacy null; yêu cầu Part không rỗng vẫn bị 422, không đoán ánh xạ Part → Step | Hai ca API riêng |
| Mặc định micro-task/nhắc không khớp nghiệp vụ | Micro-task 120 giây; user mới có mốc `[900,600,300]`; hỗ trợ 1–3 mốc không trùng, không âm | Schema, API và migration |
| Migration PostgreSQL dùng reflection khi `--sql` | Backfill SQL dùng chung online/offline cho C01; sửa cast audit JSON và importance | SQLite tests, SQL export và thực thi PostgreSQL WASM |

Các ràng buộc giữ nguyên: xác nhận Summary chưa tạo Task; chỉ confirm Candidate mới ghi Task → Steps → Sessions và ScheduleVersion trong transaction; không dời Task khác; tính buffer, nghỉ, gap 15 phút và thiếu hụt bằng Python; rolling horizon ban đầu 14 ngày; đóng/mở app không tự chốt; receipt giữ idempotency qua restart. Tối ưu `list_tasks` của PR (8 SELECT bất kể số Task) và tầng storage DeviceState được giữ.

## Quyết định ghép schema và nâng cấp

Giữ thiết kế C01 của PR: giờ riêng nằm trong Summary/PlanningContext snapshot, không ghép lại tầng PlanningPart hoặc cột `Availability.scope_key` của bản sửa cục bộ trước đó. Thay đổi được chuyển theo nghiệp vụ tương đương.

Giữ merge revision `20261106_01` của PR, thêm revision `20261107_01` sau đó để backfill micro-task còn null. Không đổi mốc nhắc do user đã chọn, không sửa lịch/phiên cũ. PostgreSQL backfill chuyển dữ liệu Parts cũ sang thứ tự Steps, giữ phần chưa phân rã, micro-task và phạm vi đã chọn; candidate schema cũ vẫn stale, phải tạo lại để duyệt.

Nếu DB cục bộ đã chạy file `20261106_01_scoped_availability.py` của bản sửa trước, revision đó trùng tên nhưng khác schema với merge head của PR. Migration mới kiểm schema và dừng với thông báo rõ khi gặp nhánh cũ; không tự stamp hay sửa mất dữ liệu. DB này cần đối chiếu/backup và chuyển đổi riêng trước khi dùng nhánh tích hợp. DB phát triển mới dùng `alembic upgrade head`.

## Kiểm chứng

Kết quả cuối: **195 passed, 6 skipped, 3 xfailed, 2 subtests passed**, không có FAIL/ERROR (102,54 giây). Ruff và ty đều PASS. Có một cảnh báo deprecation từ FastAPI/Starlette TestClient hiện hành. PostgreSQL WASM PASS cho cả schema mới và nâng cấp dữ liệu cũ; Alembic có đúng một head `20261107_01`. Mã Mobile không có diff so với PR gốc.

Chạy Ruff, ty và toàn bộ pytest từ `backend/`; test provider dùng fake hoặc bị tắt. Test Uvicorn dùng cổng loopback và DB tạm đã migrate, thực hiện đủ Draft → confirm Summary → generate → confirm Candidate → restart → replay confirm → đọc Task; chỉ có một Task và Session vẫn `scheduled`.

Script `backend/scripts/check_postgres_migrations.mjs` thực thi SQL PostgreSQL WASM cho schema mới, nâng dữ liệu cũ trước C01 và các cast alignment. Cách chạy trong README backend. Không coi kết quả WASM là kiểm thử triển khai PostgreSQL/asyncpg thực tế.

Các ca còn SKIP/XFAIL kế thừa được công khai: API chuyển chặng Rolling Horizon chưa có (hai ca trùng yêu cầu), chính sách số Task/ngày, tránh né kéo dài và ép nghỉ 90 phút chưa chốt; một ca kiểm cache cũ đã được thay bằng bằng chứng receipt/restart. Ba XFAIL là xóa Draft, start Session và capture Thought chưa có endpoint. Vì vậy kết quả suite không đồng nghĩa toàn bộ UF1–UF7 hay mọi phần mở rộng UF2 đã hoàn thành.

Chưa kiểm provider Gemini/Groq thật, Android, Flutter, Google Calendar hoặc PostgreSQL server. Mobile vẫn cần chuyển hợp đồng review từ Parts sang Steps theo C01; backend chỉ tương thích khóa null cũ, không thực hiện thay đổi Mobile trong đợt này.
