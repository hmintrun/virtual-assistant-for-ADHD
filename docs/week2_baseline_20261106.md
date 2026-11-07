# Nền chung tuần 2 — 06/11/2026

Đây là nền phát triển Backend trên SQLite để nhóm tiếp tục UF3–UF4. Nền gồm C01, A20 hiện hành, lưu trữ tuần 2 của Tín, bộ QA đã chuyển C01 và tối ưu `list_tasks`. Service/router Run, Thought và CheckIn tối thiểu vẫn cần triển khai; chưa nghiệm thu APK hoặc MVP.

**Toàn suite chưa xanh:** còn một lỗi xuất SQL PostgreSQL offline đã có ở C01; xem phần kết quả. Không dùng báo cáo này để kết luận PostgreSQL online, Auth production hoặc provider thật đã đạt.

## 1. Nguồn được giữ

| Nguồn | Commit | Nội dung |
| --- | --- | --- |
| Backend | `e1fb47e3216f73ac130cb6cf314f0caedb418f89` | C01 Task → Steps → Sessions, nguồn giờ riêng và A20 đã kiểm đầu vào |
| QA | `e22f1f07e60fa84a3ea05d8868b4310bfc6e9e6a` | Bộ hồi quy UF1–UF2 và receipt bền |
| Tín | `95706a26199099b63d036a992035f37840586803` | 5 commit lưu trữ tuần 2 |
| Bản tương thích | `1dfb1a5c593e0eb938a56af8808ac01413dc9f8b` | Giữ code Tín, giải quyết C01/test và hợp nhất migration |
| Tối ưu trên Backend | `09f19b99d4095bb290e6c810ab5dd7d322043d3d` | Chỉ thay `list_tasks`, giữ kiểm owner/nguồn |
| Tối ưu trên nền chung | `4794f7abd4cb645c57a0666aa0a4e06e342f7d60` | Cherry-pick đúng thay đổi trên, không merge toàn nhánh fix Thịnh |
| Mobile | `c17ab7740d8ae246ccdf1e64d1db9294f2e3c4fa` | Giữ toàn bộ thư mục Mobile như dev `935e3cc` |
| **Bản code/test được kiểm** | **`58ddf8a8dac513a3a67d39fc272c82f3d2d6e1f6`** | Đưa 9 ca list_tasks vào bộ test chung |

Commit bàn giao sau đó chỉ bổ sung README/báo cáo, không đổi `backend/app`, migration hoặc test so với bản đã kiểm. Xem SHA cuối tại nhánh `dev` sau cập nhật.

Lịch sử 5 commit gốc của Tín được giữ. Model DeviceState, các repository, Voice wiring và migration DeviceState gốc được giữ nội dung; receipt Run/CheckIn và kiểm nguồn Thought được giữ khi ghép Backend. AI public UF2 theo C01: khi không có kết quả provider thì giữ Draft và trả lỗi, không tự thay phạm vi bằng ba bước mẫu.

## 2. Cài và chạy Backend

Yêu cầu Python 3.12 và uv. Từ repo gốc, vào `backend/`:

```powershell
uv sync --frozen --group dev
Copy-Item .env.example .env
```

Chỉ copy `.env` khi file chưa tồn tại. Không ghi đè cấu hình cá nhân. Sửa `.env` cho môi trường nội bộ:

```dotenv
ENVIRONMENT=development
DATABASE_URL=sqlite+aiosqlite:///./week2_demo.db
GEMINI_API_KEY=
GROQ_API_KEY=
GOOGLE_CREDENTIALS_PATH=
```

Các key trống phù hợp kiểm khởi động/API đọc. Để thử AI/STT thật, người phụ trách tự cấu hình credential ngoài Git và ghi kết quả riêng. Khi provider không khả dụng, lỗi AI không được coi là luồng phân rã đã đạt.

Với **DB demo mới, riêng biệt**:

```powershell
uv run --frozen alembic heads
uv run --frozen alembic upgrade head
uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Head cần thấy: `20261106_01`. Mở `/docs` và `/openapi.json` trên server đó. Trong development, API dùng user demo `user_a`. Gọi GET `/api/v1/drafts` để bootstrap dữ liệu dev; GET `/api/v1/tasks` có thể trả trang rỗng vì chưa confirm kế hoạch.

Đường confirm UF1–UF2 cần preferences, giờ riêng và revision đúng contract. Dùng [schema](backend/schema.md) và [hướng dẫn Mobile](backend/MOBILE_UF1_UF2_API_HANDOFF_GUIDE.md); không dùng JSON theo mô hình Part cũ.

## 3. Quy tắc DB khi ghép

- Migration `20261103_01` và `20261105_01` vẫn giữ nguyên lịch sử. `20261106_01` hợp nhất hai parent, upgrade không thêm/drop dữ liệu.
- Đã kiểm nâng từ DB C01 có kế hoạch và từ DB DeviceState có dữ liệu; lựa chọn nhắc cũ `[300]` được giữ.
- Bảng/cột Part archive còn tồn tại có chủ đích. Không drop archive để làm `alembic check` hết báo khác biệt; đối chiếu metadata trong test có loại trừ đúng phần archive được giữ.
- Downgrade bị chặn ngay tại merge head để không xóa DeviceState trước khi gặp guard C01. Cần kế hoạch snapshot/phục hồi rõ ràng nếu muốn quay lại schema cũ.
- Lượt tích hợp này chỉ dùng DB thử mới và dữ liệu giả; không áp migration lên DB app của thành viên.

## 4. Kết quả kiểm

Kết quả chuẩn của `pytest tests` trên bản đã kiểm được ghi trong [JSON từng ca và hash nguồn](backend/qa_results/week2_baseline_20261106.json).

| Nhóm | Kết quả |
| --- | --- |
| Full suite | 171 passed, 1 failed, 6 skipped, 4 xfailed, 2 subtests passed |
| list_tasks | 9 ca đã nằm tại `backend/tests/test_list_tasks_batch.py`; trang 1/10/20 Task cùng 8 SELECT, thay cho 9/81/161 |
| Migration | Một head; upgrade mới và nâng từ hai nhánh có dữ liệu đạt; downgrade từ chối trước khi làm mất dữ liệu |
| Uvicorn thật | Khởi động hai lần với cùng DB SQLite thử; `/docs`, `/openapi.json`, `/api/v1/capabilities`, `/api/v1/drafts`, `/api/v1/tasks` đều 200; hai Draft demo giữ nguyên sau restart |
| Static | Ruff lint/format trên 8 file ghép trọng tâm và ty `plan_service.py` đạt |
| Mobile | Thư mục Mobile khớp dev trước ghép; không có lượt Flutter/Android/Doze trong nhiệm vụ này |

FAIL còn lại: `tests/test_database.py::test_postgresql_offline_migration_contains_deferred_fks`. C01 dùng reflection trên `MockConnection` khi chạy `alembic upgrade head --sql`, gây `NoInspectionAvailable`. Đây là lỗi đã có trước nền tuần 2; chưa suy ra PostgreSQL online chắc chắn lỗi. Giữ FAIL để theo dõi, không đổi thành PASS/SKIP.

XFAIL còn lại gồm các chức năng chưa triển khai/giới hạn: xóa Draft, API start, API Thought và cấu hình nhiều mốc nhắc. SKIP/XFAIL không được cộng vào PASS.

Lệnh tái lập, từ `backend/`, với DB test và provider giả do fixture chuẩn bị:

```powershell
$env:ENVIRONMENT = "testing"
uv run --frozen pytest tests -q -p no:cacheprovider
uv run --frozen pytest tests/test_list_tasks_batch.py -q -s -p no:cacheprovider
```

`testing` là giá trị cấu hình hợp lệ cho pytest; smoke API dùng `development`. Chưa nghiệm thu API với Auth production. Raw log/JUnit nằm trong `_temp/week2_baseline_20261106` của phiên tích hợp và không commit DB/log tạm.

## 5. Điểm bàn giao để làm tiếp

| Người | Đầu vào từ nền này | Phần tiếp theo |
| --- | --- | --- |
| Hưng | C01/A20, list_tasks theo lô, repository và receipt | Service Run, kết thúc/CheckIn/timeout, device/reminder và Thought |
| Tín | Schema/repository/migration tuần 2 đã ghép | Hỗ trợ transaction, dữ liệu đồng bộ và kiểm DB đích |
| Cương | Cùng schema, DTO hiện có và repository | Thống nhất interface với Hưng, viết/đăng ký router UF3–UF4/CheckIn |
| An/Thịnh/Khải | Nền dev có Backend C01, contract và source lưu trữ thống nhất | Ghép Mobile, timer/outbox/native/recorder; phần thiết bị cần bằng chứng riêng |
| Thành/MT | Commit nền, test chung và báo cáo giới hạn | Review/đối chiếu nghiệp vụ, kiểm tích hợp trên APK và theo dõi lỗi còn lại |

Các nhánh chức năng mới nên dựa trên dev đã nhận nền này. Không gọi nền lưu trữ đã ghép là service/API UF3–UF4 hoặc MVP đã hoàn thành.
