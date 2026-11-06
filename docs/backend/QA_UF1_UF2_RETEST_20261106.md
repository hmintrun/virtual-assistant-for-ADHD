# Báo cáo retest UF1–UF2 sau khi ghép backend vào test

Ngày: **06/11/2026**, múi giờ Asia/Saigon. Kết quả: **134 PASS, 1 FAIL, 6 SKIP, 4 XFAIL / 145 test**, thời gian **17.38 giây**. Toàn suite chưa đạt; không kết luận app sẵn sàng phát hành.

## 1. Phiên bản và phạm vi

- Nền nhánh `test`: `faf30500aea765ae567809b00ba6907120379167` (05/11, cập nhật test lần hai).
- Ghép đầy đủ nhánh `backend`: `e1fb47e3216f73ac130cb6cf314f0caedb418f89` (04/11).
- Không có conflict Git. Source `backend/app` và migration giữ đúng bản backend trên; chỉ port/bổ sung test và báo cáo. Mobile giữ nguyên so với nền `test`.
- Nhánh test cũ có `backend/app` giống nền `d7269b0` ngày 02/11; chưa chứa cập nhật UF1 inline availability, migration ba tầng, receipt DB và A20 mới. Đây là bằng chứng test được viết mới trên nền source cũ, không chứng minh từng lần tester thực thi bằng môi trường nào.
- Chạy Python 3.12.15, pytest 9.2.0, SQLAlchemy 2.0.55, Pydantic 2.14.2, FastAPI 0.162.0, Alembic 1.20.1.
- Dùng môi trường Python đã có của backend; không cài thêm dependency, không đọc/copy `.env` hoặc credential sang worktree. DB chỉ SQLite tạm, AI/Voice giả; chặn TCP ngoài trong pytest, cho loopback phục vụ asyncio Windows. Các subprocess receipt không có API key và Voice được thay provider giả.

## 2. Các test đã cập nhật

Lượt collect sau merge gặp 5 lỗi import: 4 file còn `CandidatePart` và 1 file còn ORM `PlanningPart`. Đã port sang Task → Step → Session, bỏ `planning_parts`/`client_part_key`, sửa verification scope và DTO liên quan. Không khôi phục mô hình cũ trong production để chiều test.

- Thêm nguồn actual hợp lệ vào fixture A20 và giữ clock kiểm validation rõ ràng.
- Dùng giờ rảnh khai báo trong Draft; service integration dùng manual steps hoặc mock kết quả AI cụ thể.
- Bỏ `self.fail()` vô điều kiện và kiểm tên hàm bằng `hasattr`; thay bằng assertion hành vi thật hoặc SKIP có lý do.
- Siết assertion không giao busy interval; kiểm thứ tự và tổng work của 30 bước.
- Kiểm settings qua `POST /settings/change-requests`, đọc lại DB, revision và replay.
- Thêm test receipt qua các process Python mới dùng chung file SQLite. Draft replay cùng ID, chỉ một draft/receipt, đổi payload cùng key trả `IDEMPOTENCY_KEY_REUSED`. Voice replay không gọi lại provider giả (counter = 1).
- Chỉ XFAIL khi đúng lỗi đã biết; endpoint 404/405 được đối chiếu thêm method/path OpenAPI để không nhầm lỗi dữ liệu với thiếu route.
- Một subagent sửa 5 file extended và review độc lập; root thực thi toàn suite và lập báo cáo.

## 3. Kết quả thực thi

| Trạng thái | Số lượng | Ý nghĩa |
|---|---:|---|
| PASS | 134 | Assertion của hành vi được kiểm đã đạt |
| FAIL | 1 | Sinh SQL PostgreSQL offline bị lỗi; xem mục 4 |
| XFAIL | 4 | Tái hiện đúng giới hạn đã biết; không phải chức năng đạt |
| SKIP | 6 | Không thực thi; gồm chính sách chưa chốt, chuyển chặng chưa có contract và ca cũ đã được thay thế |

4 XFAIL: DELETE Draft trả 405; POST Session start trả 404; POST Thoughts trả 404; schema chỉ cho một mốc nhắc. Hai endpoint Session/Thought thuộc các luồng ngoài UF1–UF2.

6 SKIP: giới hạn số Task/ngày chưa chốt; can thiệp khi hoãn nhiều lần chưa có contract; chuyển chặng Rolling Horizon (2 test trùng nhóm); ép nghỉ sau 90 phút chưa chốt; ca `isinstance(dict)` cũ đã thay bằng test receipt thực. SKIP không được cộng vào PASS.

## 4. FAIL còn lại

Test: `tests/test_database.py::test_postgresql_offline_migration_contains_deferred_fks`.

Lệnh được test: `alembic upgrade head --sql` với dialect PostgreSQL, không kết nối server PostgreSQL.

Migration `20261103_01_c01_sources.py:68` gọi `sa.Table(..., autoload_with=connection)`. Offline mode dùng `MockConnection`, không hỗ trợ inspection, dẫn tới `sqlalchemy.exc.NoInspectionAvailable`. Đường sinh SQL offline hiện không chạy hết migration này.

**Ảnh hưởng:** chưa thể dùng lệnh trên để xuất toàn bộ SQL migration PostgreSQL. Cần tách phần DDL và chuyển dữ liệu hoặc thiết kế đường offline phù hợp nếu nhóm cần đầu ra SQL. Chưa chạy PostgreSQL online nên không suy rằng migration trên DB PostgreSQL thực tế chắc chắn thất bại. Giữ test FAIL để theo dõi, không sửa production trong nhiệm vụ ghép và retest này.

SQLite: upgrade DB mới, insert Task/Step/Session bằng ORM và kiểm FK đạt. `planning_parts` vẫn được migration giữ lại có chủ đích; không coi bảng legacy thừa là bug ORM. Downgrade được xác nhận từ chối với thông báo cần kế hoạch phục hồi dữ liệu. Chưa kiểm backfill dữ liệu legacy có sẵn.

## 5. Đánh giá lại feedback

| Nội dung feedback | Kết quả trên backend mới |
|---|---|
| Draft/Voice mất idempotency vì dùng dict | Đã có receipt DB; kiểm replay sau restart tuần tự PASS. Chưa kiểm nhiều worker đồng thời/PostgreSQL. |
| Không có API đổi cấu hình | Không còn đúng: API change-requests cập nhật DB/revision/replay PASS khi chưa có Task bị ảnh hưởng. Thay đổi tác động lịch Task đã có vẫn có nhánh `POLICY_NOT_CONFIGURED`, cần luồng xem tác động. |
| Bắt buộc availability_ref | Mô tả cũ; inline DraftAvailability chạy được. |
| Thiếu deadline vẫn phải tạo lịch tạm | Backend hiện giữ Draft/câu hỏi, chặn xác nhận và tạo Candidate. Guardrail hiện hành PASS; lịch tạm chưa được thực hiện, cần chốt mâu thuẫn tài liệu trước khi gọi là bug. |
| Chốt muộn làm hủy kế hoạch, phải làm lại từ đầu | Tái hiện PLAN_NOT_CONFIRMABLE khi giờ trôi qua, nhưng Candidate và Draft được giữ, chưa tạo Task. Chưa có auto-slide. Không có bằng chứng kế hoạch bị xóa. |
| task_limits nghĩa là max_tasks/ngày | Sai với schema hiện hành: đây là khoảng thời gian giới hạn từng Task. Chưa có contract max_tasks/ngày. |
| shift_elapsed_sessions tăng avoidance_count và phải can thiệp sau 3 lần | Test cũ chưa thiết lập điều kiện đó; helper không nhận avoidance_count. Chưa có contract can thiệp, không suy từ schedule_revision. |
| Rolling Horizon | Kiểm horizon ban đầu tối đa 14 ngày PASS. Tiếp tục chặng sau chưa có API/contract để kiểm hành vi hoàn chỉnh. |
| Qua nửa đêm, vừa khít deadline, slot ngắn, 30 bước | Các test tương ứng PASS; 30 bước có kiểm thứ tự/tổng work/gap. Mốc <100ms là smoke benchmark local, không chứng minh độ phức tạp hay NFR production. |
| Emoji/prompt injection an toàn tuyệt đối | Chỉ chứng minh helper rule-based xử lý input Unicode trong test; chưa chứng minh LLM prompt injection hoặc an toàn hiển thị UI. |
| UF2 luôn fallback Rule-based khi AI lỗi | Public decompose mới trả lỗi khi provider không có kết quả; service trả 503 AI_UNAVAILABLE, giữ Draft và không tạo Candidate/Task. Test hành vi giữ dữ liệu PASS. Helper fallback được kiểm riêng; không suy public UF2 luôn dùng helper này. Cần đối chiếu lại mô tả fallback cũ. |
| Xóa Draft, nhiều mốc nhắc, Thoughts | Vẫn thiếu/giới hạn như 4 XFAIL ghi nhận; không tuyên bố đã sửa. |
| Auth, request ID, CORS, N+1 | Không sửa trong nhiệm vụ này. Đối chiếu tĩnh vẫn thấy dev auth, thiếu middleware request ID, cấu hình CORS cũ và list_tasks gọi lần lượt get_task_by_id. Chưa đo tải/query count hoặc kiểm auth production. |
| Calendar lỗi, nút thêm việc, giao diện/chính tả | Chưa retest Mobile hoặc kết nối Calendar thực; không dùng test backend để đóng lỗi giao diện. |

## 6. Cách chạy và bằng chứng

Từ repo đã cài dependency theo `backend/uv.lock`, đặt API keys/credential path rỗng và dùng SQLite tạm. Lệnh chuẩn:

```powershell
uv run --project backend --frozen python -m pytest -c backend/pyproject.toml backend/tests -q -p no:cacheprovider --tb=short -r sxX
```

Lượt này dùng `uv run --project D:\HCMUS\CT\ProActive\backend --frozen --no-sync` để tái sử dụng môi trường sẵn có, còn cwd và pythonpath pytest trỏ vào worktree test; traceback xác nhận import source worktree mới.

- [Kết quả từng test, phiên bản và SHA256 source test](qa_results/uf1_uf2_20261106.json).
- Raw JUnit/log local: `_temp/qa_retest_20261106/junit.xml`, `pytest.log`, `initial_collection.log` (không commit file tạm).
- Ruff check/format 11 file test đã sửa và diff check trong phạm vi test/báo cáo đạt. Diff check toàn merge còn 8 cảnh báo khoảng trắng có sẵn trong `UF3_UF4_DEEP_DIVE_ANALYSIS.md` lấy nguyên từ backend; không sửa tài liệu ngoài phạm vi để che cảnh báo.

Giới hạn: chưa kiểm Mobile/APK, mic/notification/DND thiết bị, Google Calendar thật, chất lượng/độ trễ provider thật, PostgreSQL online, nhiều worker đồng thời, dữ liệu legacy có sẵn hoặc rollback bằng chèn lỗi giữa transaction confirm. Aggregate được lưu thành công không tự chứng minh riêng rollback khi lỗi.
