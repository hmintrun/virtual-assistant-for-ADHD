# Biên bản nền phát triển tuần 2 — 03/11/2026

**Kết luận:** Đã chuẩn bị nền tích hợp local từ `dev` và Backend mới, kiểm Backend đạt. Theo lựa chọn của MT, Mobile chờ người có Flutter/Android SDK kiểm tiếp. Đây là nền bắt đầu phát triển tuần 2, chưa phải nghiệm thu giai đoạn 1 hoặc MVP.

## Nguồn tích hợp và commit đã kiểm

| Nguồn | Commit | Nội dung |
| --- | --- | --- |
| `origin/dev` tại lần fetch | `059cccea2e6fe7be6b553db6c003ad6028e5b5af` | Nhánh chung remote trước lượt tích hợp |
| `dev` local trước tích hợp | `d357faa3fafd6762e0c48c05f9d70d572778fdfa` | Giữ ba commit local, gồm Mobile UF1–UF2 và sửa PATCH Pydantic |
| `origin/backend` tại lần fetch | `571fff725dfb83e673ed03f9088cdfd7a547631f` | Hai commit chưa có trên dev: `7387ed3` giờ rảnh UF1 và `571fff7` ưu tiên scheduler |
| Bản sửa fallback local | `5ee0321` | UF2 trả đề xuất Python tầng 3 khi provider timeout/lỗi kết nối, có test hồi quy |
| Merge Mobile/Backend | `e309296`, `b695dce` | Giữ giao diện Mobile từ dev, Backend mới và scheduler |
| **Commit code đã kiểm** | **`ce2bc1e631964289c735a68b0ce8337e09d71cd6`** | Sau giải quyết conflict, bổ sung test HTTP PATCH UF1 và chuẩn hóa format các file vừa ghép |

Commit tài liệu đồng bộ được ghi sau commit code đã kiểm; lấy `git rev-parse dev` sau khi fast-forward để biết SHA đầy đủ của bản nền gồm tài liệu. Nhánh lưu công việc tích hợp: `chore/week2-baseline-20261103`. Lượt này chỉ tích hợp local, chưa push remote hoặc gửi yêu cầu review cho thành viên.

## Kết quả kiểm chứng

| Kiểm tra | Kết quả và giới hạn |
| --- | --- |
| Toàn bộ test Backend | **58 passed, 2 subtests passed**; gồm SQLite migration upgrade/check/downgrade, model/constraint, DTO, AI/scheduler và test HTTP mới |
| Hồi quy conflict UF1 | Hai ca HTTP: PATCH time_constraint cùng availability mới hoặc null; không mất kiểu Pydantic, bỏ ref nguồn cũ, giữ revision/dữ liệu khi retry và đọc lại qua session DB mới |
| Ruff lint/format | Đạt trên bảy file AI, draft service, scheduler facade/implementation và các test liên quan vừa ghép |
| Type check | Đạt trên bốn file service AI/draft/scheduler vừa ghép |
| Chạy Backend thật | Uvicorn khởi động ở localhost với SQLite in-memory và ENVIRONMENT=development; GET `/docs`, `/openapi.json`, `/api/v1/capabilities`, `/api/v1/drafts` đều HTTP 200. Đã dừng server sau kiểm |
| Dữ liệu và provider | Kiểm với DB tạm/in-memory; không dùng dữ liệu DB cá nhân. AI timeout/lỗi được mô phỏng; không xác nhận Gemini/Groq/STT/Google thật đã đạt |
| Mobile/Android | **Chưa kiểm**: máy MT chưa cài Flutter/Dart SDK. Người dùng chọn chốt Backend và ghi Mobile chờ kiểm; không có kết quả build APK, dart analyze hoặc đo máy Android |
| Cảnh báo môi trường | Pytest hiện cảnh báo fixture loop scope của pytest-asyncio chưa cấu hình; các test vẫn đạt. Lượt chạy đầu bị sandbox chặn thư mục tạm, đã chạy lại có quyền phù hợp và test migration đạt |

Lệnh tái lập từ `backend/` với Python 3.12 và dependencies đã cài:

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/ruff.exe check app/services/draft_service.py app/services/ai_service.py app/services/planning/scheduling.py app/services/scheduler_engine.py tests/test_ai_service.py tests/unit/test_scheduler_engine.py tests/test_week2_baseline.py
.venv/Scripts/ruff.exe format --check app/services/draft_service.py app/services/ai_service.py app/services/planning/scheduling.py app/services/scheduler_engine.py tests/test_ai_service.py tests/unit/test_scheduler_engine.py tests/test_week2_baseline.py
.venv/Scripts/ty.exe check app/services/draft_service.py app/services/ai_service.py app/services/planning/scheduling.py app/services/scheduler_engine.py
```

## Rà soát phần tích hợp

Mục đích rà soát: đưa Mobile đang có trên dev, Backend mới và bản sửa fallback vào cùng một nền local mà giữ cả hai hành vi UF1 đã sửa ở mỗi nhánh.

- **Conflict đã xử lý:** PATCH Summary giữ object Pydantic của dev, đồng thời xóa availability_ref khi user sửa/xóa availability riêng theo Backend mới. Hai điều kiện cùng được giữ và kiểm bằng HTTP/DB.
- **Điểm đã làm tốt:** nguồn giờ riêng được chặn rõ tại UF2 thay vì âm thầm dùng giờ chung; lưu lịch cũ và scheduler không tự dời Task khác; receipt/unique constraint được tái sử dụng thay vì tạo cơ chế mới.
- **Vướng mắc chức năng quan trọng đã biết:** UF2/CalendarContext chưa đọc nguồn giờ riêng theo Draft. Code chủ động trả `409 PLAN_NOT_CONFIRMABLE` / `wait_for_configuration`; Hưng tiếp tục trong giai đoạn 1. Không coi nhánh này đã đi xuyên UF1–UF2.
- **Tích hợp Mobile cần tiếp nối:** adapter/model hiện vẫn theo nguồn availability_ref cũ, chưa xử lý đầy đủ availability riêng. An/Hưng/Cương phải kiểm contract và ghép dữ liệu thật; contract chung không đồng nghĩa mọi thay đổi đã có implementation ở cả hai phía.
- **Cần kiểm khi nghiệm thu API:** bootstrap user dev hiện dựa vào đường đọc danh sách nháp trước khi tạo mới; bộ test HTTP mô phỏng bước này. Không xác nhận luồng POST đầu tiên trên DB hoàn toàn rỗng hoặc Auth production đã hoàn thiện. ENVIRONMENT của smoke là development; mode testing/auth cần kiểm riêng nếu sử dụng.
- **Các phần tuần 2 còn phải viết:** nhiều mốc nhắc, router/service UF3–UF4/CheckIn, outbox/Undo, timer/native/recorder và pipeline Thought→Draft. Các phần này chưa được ghi Pass từ kết quả test nền.

**Đánh giá local:** đủ làm nền Backend để nhóm phát triển. **Chưa đủ điều kiện công bố Mobile chạy được hoặc nghiệm thu MVP.** Đây là rà soát và kiểm tự động trong workspace; chưa có review chuyên môn độc lập của thành viên. Review độc lập trước merge PR/publish theo quy trình nhóm vẫn cần được ghi nhận; không coi biên bản này thay thế reviewer.

## Đồng bộ tài liệu và phần còn chờ ý kiến

| Nội dung | Trạng thái |
| --- | --- |
| Nhắc trước phiên | Đã chốt default mới `[900, 600, 300]`, 1–3 integer không âm, không trùng, giảm dần; giữ cấu hình cũ. Project/schema/plan/checklist cùng mô tả đích, code nền vẫn một offset |
| Chuông khi thu | Đã chốt âm thanh đúng hạn ≤1 giây, không hoãn 15 giây; file hợp lệ, không lùi ended_at/actual/timeout DND. Chưa có bằng chứng Android |
| Undo Thought | Đã chốt file/metadata lưu bền, outbox chờ 5 giây; phân biệt Undo cất mới và xóa bản cũ; kiểm crash/race/retry. Chưa triển khai đầy đủ |
| Phân công | Cương router/DTO/HTTP; Hưng service nghiệp vụ; Tín DB/repository; giữ một người phụ trách chính mỗi đầu việc và đủ tám người trong bốn giai đoạn |
| **Chọn nghỉ khi chưa xác nhận kết quả** | **Còn mở:** ví dụ payload/biểu diễn quyết định nghỉ trong CheckIn cần MT/Hưng/Cương/An chốt ở giai đoạn 1. Không tự thêm enum hoặc gán completed. Thuộc việc tiếp theo, chưa triển khai trong lượt chốt nền/tài liệu |
| Capabilities nhiều mốc nhắc | Cách biểu diễn wire thay trường default số đơn cần Cương/Hưng hoàn thiện đồng bộ với Mobile trước khi dùng; không đổi kiểu một phía |
| Policy khác ngoài phạm vi | Buffer sample/history, handoff DND sang Run tiếp theo, miền cấu hình và Auth còn theo danh sách mở trong Project; không tự chốt thêm |
| Tài liệu online | Docs/Sheet chưa cập nhật trong lượt này; schema mục 12 ghi rõ quyết định local đã chốt và code còn chờ |

Tài liệu dùng tiếp: [Project](../Project.md), [schema](backend/schema.md), [kế hoạch tuần 2](week2_detailed_plan.md), [checklist kỹ thuật](week2_technical_checklist.md).

**Việc bàn giao tiếp theo:** An/Khải kiểm Mobile trên máy có SDK và ghi kết quả; Hưng nối nguồn giờ riêng UF1→UF2; MT cùng Hưng/Cương/An chốt payload Chọn nghỉ trước khi triển khai CheckIn tối thiểu. Ngày 04/11 vẫn phải nghiệm thu theo điều kiện giai đoạn 1, không lấy bản nền này thay bằng chứng sản phẩm.
