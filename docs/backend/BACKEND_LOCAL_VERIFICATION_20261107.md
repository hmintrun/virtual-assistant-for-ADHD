# Kiểm chứng và sửa Backend local — 07/11/2026

## Nguồn và phạm vi

- Repository: `D:\HCMUS\CT\ProActive`, nhánh `backend`, HEAD `09f19b9`.
- Dùng working tree local theo chỉ định của Quốc Hưng. Không pull, merge hoặc reset theo `origin/backend` đã hợp nhất với dev; không thay đổi HEAD, commit hay push trong lượt này.
- Phạm vi: Tier 3 phân rã UF2, bảo toàn Candidate khi keyed adjustment thất bại, PATCH/dedup UF1, test C01, batch query và migration.
- Giữ các sửa dở Docs/Mobile và file untracked `tests/unit/test_adaptation.py` đã có trước lượt này. Không làm pagination hoặc đánh giá Mobile.

## Lỗi đã tái hiện và cách sửa

| Vị trí | Trước sửa | Sau sửa |
| --- | --- | --- |
| `app/services/ai_service.py:decompose_task` | Có helper Tier 3 nhưng provider timeout/mất kết nối vẫn ném TimeoutError; API sinh Candidate trả 503. AI trả expected output rỗng cũng chưa chuyển fallback. | Gọi lại `_fallback_decompose_task` khi provider lỗi hoặc phân rã không hợp lệ; dùng thời lượng user, có expected output và micro-task. Scheduler/review/confirm vẫn bắt buộc. |
| `ai_service.py:_fallback_decompose_task` | Scope dài có thể khiến tên Step vượt giới hạn 255 ký tự, làm chính fallback bị lỗi validation. | Giới hạn phần title/scope nhúng trong nhãn dự phòng. Không sửa Summary/phạm vi gốc. |
| `draft_service.py:confirm_draft_summary` | Nhánh revision không DB đọc ref bằng attribute, gây AttributeError khi ref legacy là dict. | Chuẩn hóa biến ref bằng AvailabilityRef.model_validate trước khi đọc ID/revision; model/dict đều được kiểm đúng revision. |
| `migrations/versions/20261103_01_c01_sources.py` | PostgreSQL `alembic upgrade head --sql` cố reflection trên MockConnection nên lỗi. | Dùng SQL backfill chung cho PostgreSQL online/offline; SQLite giữ batch/reflection. Không bỏ chuyển dữ liệu và không thêm migration head. |
| Tests C01 | Test database/scheduler không collect vì còn import PlanningPart/CandidatePart; fixture DTO còn contract Part. | Port sang Task → Step → Session; giữ kiểm thứ tự, gap, feasibility, owner và thời lượng. Test migration giữ archive Part, chặn downgrade và kiểm head local. |

`adjust_candidate_by_nl` và xử lý `AI_UNAVAILABLE` của `plan_service` được giữ nguyên. Không thêm fallback phân rã cho chỉnh Candidate, không tạo Candidate thay thế khi chỉnh thất bại. Regression kiểm Steps/Sessions/review revision và Draft không đổi sau lỗi, retry không sinh Candidate mới; chỉnh thành công đúng key thì tính lại lịch, replay không gọi AI lần nữa.

## Kết quả kiểm chứng

| Nhóm | Kết quả |
| --- | --- |
| Baseline local nguyên trạng | 2 lỗi collection: import PlanningPart và CandidatePart. Phần có thể collect: 65 passed, 1 failed do fixture DTO Part cũ. |
| Toàn bộ `backend/tests` sau sửa | **118 passed, 2 subtests passed**, 12.91 giây; không fail/error/skip/xfail. Bao gồm test adaptation untracked đã có trước lượt này. |
| Bộ `_temp/c01_tests` local có sẵn | **37 passed**, 12.19 giây; có nâng SQLite mới/dữ liệu trước C01, rollback, nguồn/revision và keyed adjustment. |
| Ruff và ty | Đạt trên các file production/test được sửa hoặc thêm trong lượt này. |
| Diff check | Đạt cho Project và backend. Cảnh báo LF/CRLF của Git là cấu hình line-ending, không phải whitespace error. |
| Batch query | Regression trang 1/10/20 Task giữ 8 SELECT. Đây là 5 nhóm batch, chưa tối ưu pagination tại DB. |

Các test dùng SQLite tạm/in-memory, provider giả và environment tắt keys; conftest bổ sung guard chặn outbound socket trừ loopback. Không chạy migration vào `backend/proplan.db`, không đọc/sửa `.env` hoặc credentials.

Giữ/bổ sung suite `tests/test_ai_service.py`, `tests/test_week2_baseline.py`, `tests/test_list_tasks_batch.py` và `tests/test_planning_api.py`. Các test adapter hiện có dùng expected output hợp lệ để kiểm warm backup, không bỏ assertion về tier để làm suite xanh.

Bằng chứng local (ignored, không phụ thuộc để chạy suite):

- `_temp/backend_verification_20261107/baseline.xml`
- `_temp/backend_verification_20261107/collectable.xml`
- `_temp/backend_verification_20261107/red.xml`, `api_red.xml`
- `_temp/backend_verification_20261107/verified.xml`
- `_temp/backend_verification_20261107/c01.xml`

## Cách chạy lại

Từ `backend/`, dùng `.venv/Scripts/python.exe` Python 3.12 và đặt `DATABASE_URL=sqlite+aiosqlite:///:memory:`, các biến `GEMINI_API_KEY`, `GROQ_API_KEY`, `GOOGLE_CREDENTIALS_PATH` thành chuỗi rỗng trước khi gọi pytest.

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Bộ C01 có sẵn chạy từ root:

```powershell
backend/.venv/Scripts/python.exe -m pytest -c backend/pyproject.toml _temp/c01_tests -q
```

Không tự `alembic upgrade head` lên DB app để thay cho test. Chuỗi local tại mốc này có đúng head `20261103_01`; DeviceState/merge revisions của remote không thuộc nền local này.

## Giới hạn

- Chưa kiểm provider Gemini/Groq, Google Calendar hoặc PostgreSQL server/asyncpg thật. PostgreSQL được kiểm xuất SQL; không coi đó là bằng chứng thực thi backfill trên server.
- Còn cảnh báo cấu hình loop scope từ pytest-asyncio hiện có; không đổi dependency/config ngoài phạm vi để che cảnh báo.
- A20 vẫn giữ mặc định thử nghiệm; không áp tự động settings. Rolling horizon cho Candidate đầu không đồng nghĩa API lập tiếp Task đã lưu đã hoàn tất.
- Bản Project.md từng sửa ở checkout tạm dưới `cp/_temp` không phải nguồn của lần kiểm chứng này. Project.md trong repo local được cập nhật riêng theo đúng code local và quyết định đã chốt.


## Kiểm chứng nhánh PR vào dev

Commit sửa local: `dc388e7`. Nhánh PR `codex/backend-local-verification-20261107` ghép thêm `origin/dev@935e3cc` trong worktree riêng; không merge remote backend và không thay các sửa dở của working tree local.

Đã giải quyết sáu conflict ở Project, AI service, scheduler, AI/scheduler tests và schema. Toàn bộ `backend/` sau giải quyết khớp commit local đã kiểm chứng; `mobile/` khớp hoàn toàn dev. Tài liệu giữ các nội dung nền dev đồng thời chuyển phần Part và nguồn giờ rảnh sang C01.

Suite chạy từ checkout PR độc lập đạt **81 passed, 2 subtests passed**; không cần file untracked `test_adaptation.py` hoặc `_temp/c01_tests`. Ruff và ty toàn backend đều đạt; diffcheck các file sửa của lượt này đạt. Diff toàn PR còn 8 cảnh báo whitespace kế thừa từ tài liệu `UF3_UF4_DEEP_DIVE_ANALYSIS.md` đã có trong lịch sử local; không sửa nội dung ngoài phạm vi để che cảnh báo. Số 118 test ở mục trước là kết quả working tree local có thêm suite adaptation untracked của tác giả, không dùng làm số test của artifact PR.

Vẫn giữ giới hạn PostgreSQL SQL export, provider giả/SQLite và cảnh báo loop-scope pytest-asyncio; chưa nghiệm thu PostgreSQL server, AI/Calendar hoặc Android thật.
