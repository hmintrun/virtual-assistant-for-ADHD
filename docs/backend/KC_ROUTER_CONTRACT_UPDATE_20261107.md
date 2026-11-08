# Bàn giao router KC theo backend hiện hành — 07/11/2026

## Nền và phạm vi

Bản sửa bắt đầu từ `feat/kc-w2-uf3-uf4-router` (`1a98a1f`) và hợp nhất backend `c03fdeb`. Bốn conflict Settings/User/Draft được giải quyết bằng implementation backend hiện hành, giữ C01, storage, Tier3, giờ riêng Draft và các sửa UF1/UF2 mới. Models, repositories, migrations, business services và Mobile không đổi so với backend này.

User xác nhận sẽ bổ sung service; lượt sửa chỉ chuẩn hóa router/DTO và điểm nối. Không tạo execution_service hoặc business service giả. DTO dùng schema.md và giữ tương thích tên lớp/interface W04 local. Run không có active_phase=micro_task; no_respond thuộc response_state CheckIn.

## Điểm gọi service

Tất cả command nhận Idempotency-Key bắt buộc, không rỗng, truyền nguyên vẹn cùng user_id/db/path ID/payload. GET không tự commit/query DB trong router.

| HTTP | Module / hàm | DTO kết quả trong data |
| --- | --- | --- |
| POST /sessions/{session_id}/start | run_service.start_session_run | SessionRunData;201 |
| GET /runs/active | run_service.get_active_run | SessionRunData hoặc null;200 |
| GET /runs/{run_id} | run_service.get_run_detail | SessionRunData;200 |
| POST /runs/{run_id}/events | run_service.record_run_events | RunEventBatchResult;200 |
| POST /runs/{run_id}/end | run_service.end_session_run | SessionRunData có check_in;200 |
| PUT /devices/{device_id}/state | device_service.save_device_state_report | DeviceStateData;200 |
| GET /reminders | device_service.list_reminders | ReminderListResult;200 |
| POST /reminders/{reminder_id}/receipt | device_service.record_reminder_receipt | ReminderData;200 |
| GET /runs/{run_id}/check-ins | checkin_service.list_run_checkins | CheckInListResult;200 |
| PUT /check-ins/{checkin_id}/response | checkin_service.respond_to_checkin | CheckInData;200 |
| POST /thoughts | thought_service.capture_thought | ThoughtData;201 |
| GET /thoughts | thought_service.list_thoughts | ThoughtListResult;200 |
| GET /thoughts/{thought_id} | thought_service.get_thought_detail | ThoughtData;200 |
| POST /thoughts/{thought_id}/draft | thought_service.convert_thought_to_draft | ThoughtToDraftResult;201 |
| DELETE /thoughts/{thought_id} | thought_service.delete_thought | ThoughtDeleteResult;200 |

Mọi response thành công là SuccessEnvelope {data,meta}, giữ request_id nếu có. Error của service đi qua handler AppError hiện hành. Events trả accepted_event_ids/duplicate_event_ids/run_revision. End không bọc thêm run/final_checkin_id; Run có run_revision/dnd_release_at. DELETE Thought nhận body expected_thought_revision và trả deleted/local_cleanup_required.

DTO ThoughtToDraftResult gồm thought_id, linked_draft_id, converted_task_id và draft: PlanningDraft. Service trả kết quả này, không chỉ trả ID hoặc PlanningDraft trần. W03 đang mô tả luồng tạo201; nhánh200 mở lại Draft đã có cần nối cùng service/receipt khi triển khai, không suy đã nghiệm thu nhánh đó từ mock201 trong đợt kiểm router.

GET /reminders nhận device_id, from/to có offset và from<to, schedule_revision tùy chọn; truyền xuống service dưới tên from_time/to_time. GET /thoughts chuyển limit/cursor; các result list có items/next_cursor. CheckIn GET chuyển kind.

Không thêm endpoint undo-delete. Undo5giây/file/outbox vẫn theo thiết kế thiết bị đã chốt. Các endpoint mid CheckIn create/ActualCorrection chưa mở thêm trong lát router tối thiểu này.

## Service chưa bàn giao

service_dispatch.service_handler resolve đúng module/operation tại lúc gọi endpoint. Module hoặc operation chưa có trả503 SERVICE_NOT_READY bằng ErrorEnvelope; app và các API hiện có vẫn import/chạy được. Khi Hưng đưa service đúng interface vào, router gọi trực tiếp mà không cần thay tên hàm.

Không bắt lỗi ImportError/ModuleNotFoundError của dependency bên trong service rồi biến thành SERVICE_NOT_READY. Không giả lập response thành công, Run hoặc trạng thái thiết bị. Device nhận trạng thái dạng string thực tế, không default permission=true/DND=applied/restored. Các enum chưa liệt kê đầy đủ trong nguồn không được tự bịa thêm policy ở router; service chịu trách nhiệm kiểm nghiệp vụ.

## Kiểm chứng

- Targeted contract suite49ca: dùng payload/response từ Sheet API và mock service theo đúng interface, đạt49/49.
- Full backend suite:264passed,6skipped,1xfailed,2subtests passed; một warning TestClient kế thừa. Hai probe cũ gửi request sai schema đã được đổi thành kiểm validation422, không coi router tồn tại là bằng chứng service hoàn thành.
- Kiểm tương thích W04 trong snapshot riêng: copy đúng service/command/model/index/repository và13testW04 gốc của user, kết hợp49contract tests:62/62passed. Giữ default run_revision=1 của DTO W04 để không phá construction hiện có; service thật vẫn cung cấp revision DB khi trả Run.
- Ruff và ty toàn backend đạt; diff so với backend không có whitespace lỗi mới.
- Snapshot kiểm W04 nằm _temp/w04_integration, service đó không được đưa vào commit KC. Không sửa W04 trong repo chính D.
- Chưa nghiệm thu business service W05–W07, PostgreSQL online, Google/provider hoặc Android. Mock contract tests không thay cho E2E service thật.

## Bước tiếp theo

Hưng ghép service theo bảng trên, tái dùng DTO của branch này; Tín giữ migration live-run của W04 riêng. Khi ghép W04 local cần giữ test nghiệp vụ và đối soát file schemas/execution.py/focus.py/router.py thay vì chọn nguyên một phía. Chạy fullsuite sau tích hợp; chưa push hay merge PR trong lượt sửa router này.
