# Đặc tả đối tượng và schema Backend ProActive

> **Cập nhật C01 ngày 03/11/2026:** Code UF1–UF2 dùng giờ rảnh riêng từ Draft, khóa nguồn qua CalendarContext và lưu snapshot khi confirm Task. Cấu trúc hiện hành là Task → Steps → Sessions; không có PlanningPart trong API mới. Google Calendar thật được tích hợp sau. Unit đạt 10/10, integration đạt 10/10 trên SQLite tạm/AI giả, gồm hai ca migration DB mới/dữ liệu cũ. Chưa áp migration DB app hoặc system test trên điện thoại; chưa nghiệm thu nguồn thật.


Cập nhật nguồn gốc: **30/10/2026**; đồng bộ local và quyết định tuần 2: **03/11/2026**. Phạm vi: O01–O26 của cả 7 UF, cấu trúc lồng và các DTO hỗ trợ trong hợp đồng API. Tài liệu mô tả dữ liệu để triển khai và review; không xác nhận ORM/API đã được code.

## 1. Nguồn chuẩn và cách đọc

- [Backend Docs](https://docs.google.com/document/d/1X1zIWny1nOWCfkS4zLEdn71uZLhYLu5OkHlUPKcdoxE/edit): 01 nghiệp vụ; 02 object; 03 cách dùng API; 04 trách nhiệm; 05 kiểm chứng.
- [Sheet API — tab api](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644): request, success/fail JSON và **ghi chú trong ô** của 62 endpoint, dòng 2–63.
- Tên trường và biến thể JSON trao đổi theo Sheet; nghiệp vụ theo thẻ 01. Chỗ hai nguồn chưa khớp được ghi tại mục 9; không tự coi một ví dụ là toàn bộ schema hoặc enum.
- O00 User và Auth login/me tại mục8/AUTH_GUIDE là contract bổ sung11/11/2026; mức kiểm chứng runtime xem handoff.
- schema.md đồng bộ các nguồn trên và các quyết định local được người dùng chốt trong Project/kế hoạch tuần 2. Mục 12 ghi rõ phần thay đổi local còn chờ cập nhật Docs/Sheet và triển khai DTO/code; không coi tài liệu online đã được cập nhật. Các tài liệu trong _temp không phải phụ thuộc để dùng file này.

### Quy ước kiểu dữ liệu

- ID API là **string**; không ép UUIDv4 chỉ vì DB có thể dùng UUID. Không thêm cả id và task_id/session_id vào response.
- Thời lượng: integer giây. Revision: integer không âm; phải theo tài nguyên. Ví dụ CheckIn mới có response_revision=0; không ép mọi revision >=1.
- Timestamp tuyệt đối: ISO 8601 có offset. Backend chuẩn hóa UTC khi lưu, giữ timezone IANA để hiểu giờ địa phương/lịch lặp. WeeklyRule dùng HH:mm, không phải timestamp UTC.
- X|null cho phép giá trị chưa có/không áp dụng; không tự có nghĩa được bỏ trường. PATCH omitted giữ nguyên; null chỉ xóa/để trống trường được phép. [] là danh sách đã biết rỗng, không thay cho unknown.
- Bảng object mô tả dữ liệu khái niệm và DTO. **Không phải mọi trường đều là cột SQL hoặc đều được trả ra API.** Trường ghi nội bộ/local không đưa vào response thông thường.
- Trường can_plan, feasibility, duration, trạng thái dẫn xuất, revisions và user_id do server kiểm/tính; không cho client tự quyết định.
- Request/response cụ thể có thể là ref, kết quả rút gọn hoặc wrapper; đọc đúng endpoint. Mảng nhiều ví dụ trong Sheet là các tình huống độc lập, không phải HTTP body dạng mảng.
- DTO input/output dùng Pydantic; model lưu trữ dùng SQLAlchemy 2.0. DTO không import ORM/router. Database chọn cột/bảng/JSON và migration, không sao chép một bảng cho mỗi object.

## 2. Quan hệ và vị trí lưu dữ liệu

```text
User
 ├─ Preferences / Availability
 ├─ PlanningDraft ──> PlanCandidate ──(xác nhận UF2)──> Task
 │                                                    └─ Step
 │                                                        └─ Session
 │                                                            ├─ SessionRun ──> CheckIn
 │                                                            ├─ Reminder
 │                                                            └─ CalendarEventLink ──> CalendarSyncJob
 ├─ CalendarConnection ──> CalendarContext / CalendarEvent
 ├─ RescheduleProposal ──(duyệt UF6)──> ScheduleVersion
 ├─ Thought ──(chuyển thành nháp)──> PlanningDraft
 └─ CommandReceipt / AuditEvent / AdaptationSuggestion
```

- UF1 xác nhận Summary, chưa có Task. UF2 confirm mới lưu Task/Steps/Sessions/ScheduleVersion; Thought chỉ ghi converted_task_id sau thành công này.
- Candidate dùng client_step_key/client_session_key. Khi confirm trả key_mapping.steps/sessions sang ID thật; Step thuộc trực tiếp Task.
- Candidate/Task giữ `unexpanded_scope` và `decomposition_state=partial` khi còn phạm vi chưa chia. Steps đã biết nhưng chưa xếp nằm trong report; không tạo Step/Session giả hoặc gán 0 cho phần chưa ước lượng.
- Session là lịch dự kiến; SessionRun là lần thực hiện được chấp nhận start. not_started không có Run giả.
- CalendarEventLink dùng bộ connection_id/google_calendar_id/google_event_id; Session giữ ID nội bộ. Google event status không thay outcome của Task/Step/Run.

### Hướng lưu trữ để API phục hồi được

| Nhóm | Hướng triển khai |
|---|---|
| Draft, Candidate, Proposal | Lưu bền vững ID/revisions và snapshot cần phục hồi; có thể dùng JSON kèm cột query/CAS. Không chỉ để biến RAM giữa nhiều request/worker |
| Availability, Preferences | Có ID/revision và bản user xác nhận/effective; effective_intervals là dữ liệu tính theo phạm vi |
| CalendarContext | Giữ snapshot theo context_id/revision đủ cho GET/review/confirm và đối soát nguồn; payload của ref cũ không bị ghi đè âm thầm |
| Task/Step/Session/Run/CheckIn | Dữ liệu chính thức và kết quả thực hiện lưu qua transaction/migration |
| ScheduleVersion, Receipt, Job | Lưu cùng thay đổi lịch cần áp dụng; Google thực hiện sau commit |
| Thought audio, DND restore snapshot | Audio/snapshot hệ thống chủ yếu ở thiết bị; Backend giữ metadata được hợp đồng cho phép |
| DTO/Value object | Summary, TimeConstraint, Feasibility và Event không bắt buộc có bảng riêng; nhúng snapshot theo nhu cầu |

Đây là hướng thiết kế lưu trữ, chưa phải DDL đã khóa. Chọn FK một chiều ở phần sở hữu; các liên kết ngược có thể truy vấn/dẫn xuất để tránh vòng phụ thuộc migration. Mảng ID có thể lưu JSON hoặc bảng liên kết theo nhu cầu; không ép kiểu ARRAY đặc thù PostgreSQL cho code cần chạy SQLite.

## 3. Danh mục O01–O26

### O01 UserPreference

Cấu hình yêu cầu và có hiệu lực của một user.

API liên quan: [dòng 39](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A39:J39), [dòng 41](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A41:J41), [dòng 42](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A42:J42), [dòng 44](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A44:J44).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `work_interval_seconds` | integer | Độ dài tối đa một quãng làm chính theo cấu hình có hiệu lực; không phải toàn Session. |
| `rest_interval_seconds` | integer | Độ dài nghỉ bên trong khi có; khác khoảng cách 900 giây giữa hai Session. Có thể không tạo quãng nghỉ bên trong. |
| `micro_task_seconds` | integer \| null | Ước lượng độ nhỏ của gợi ý chuẩn bị nếu có; có thể null khi chưa chốt qua nghiên cứu. Không có timer riêng và không cộng vào thời lượng Run/Session chính. |
| `reminder_offsets` | array<integer> | Đích tuần 2 đã chốt: 1–3 integer giây không âm, không trùng, chuẩn hóa giảm dần; default mới `[900, 600, 300]`, giữ cấu hình user đã lưu. Code nền vẫn một offset `[900]`, chưa gửi mảng ba phần tử vào API cho tới khi DTO/validation được mở rộng. Micro-task hiển thị cùng lời nhắc, không tự start Run. |
| `requested_revision` | integer \| null | Phiên bản cài đặt mới được yêu cầu, có thể đang chờ đổi lịch. |
| `effective_revision` | integer | Phiên bản đã có hiệu lực; dùng tạo lịch hoặc snapshot thực thi. |
| `buffer_multiplier` | number | Hệ số dự phòng đang có hiệu lực; chỉ áp một lần lên làm chính. Giá trị cụ thể cần nghiên cứu/pilot. |
| `reminder_style` | string | Giọng, cách diễn đạt và hình thức nhắc người dùng đã chọn; không tự thay thời lượng phiên. |
| `requested_changes` | PreferenceChanges \| null | Các giá trị mới được yêu cầu; chưa dùng thay bản hiệu lực khi còn chờ phương án lịch. |
| `pending_proposal_id` | string \| null | Phương án UF6 liên quan nếu thay cài đặt làm ảnh hưởng lịch; null khi không cần đổi lịch. |
| `revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O02 Availability

O02 mô tả tài nguyên giờ rảnh chung/nguồn cũ. Giờ riêng của yêu cầu mới nằm ở `O04.availability` (DraftAvailability), không tự ghi vào O02. `confirmed_at` của O02 không phải điều kiện bắt buộc cho UF1 mới; nguồn mới được xác nhận bằng `summary_confirmed_revision` của Draft.

Giờ rảnh do user xác nhận, có timezone và nguồn.

API liên quan: [dòng 40](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A40:J40), [dòng 41](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A41:J41), [dòng 61](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A61:J61).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `weekly_rules` | array<WeeklyRule> | Các khoảng rảnh lặp theo ngày trong tuần. |
| `date_overrides` | array<DateOverride> | Ngoại lệ cho ngày cụ thể, như một ngày bận đột xuất. |
| `task_limits` | array<TaskAvailabilityLimit> | Giới hạn giờ thực hiện riêng của Task nếu người dùng xác nhận; không tự ghi đè giờ rảnh chung. |
| `source` | string | Cho biết giờ rảnh được nhập hoặc xác nhận bởi người dùng. |
| `effective_intervals` | array<TimeInterval> \| null | Dữ liệu dẫn xuất theo cặp from/to; không truy vấn thì null, đã tính mà không có khoảng rảnh mới là []. Không ghi ngược vào cấu hình giờ rảnh. |
| `calendar_context` | CalendarContextRef \| null | Tham chiếu O21 và phiên bản dữ liệu Calendar dùng tính lịch; không hiểu lỗi đọc lịch là không có lịch bận. |
| `timezone` | string | Múi giờ áp dụng cho quy tắc theo tuần và ngày ngoại lệ; mốc tuyệt đối vẫn được chuẩn hóa UTC. |
| `confirmed_at` | date-time \| null | Thời điểm người dùng xác nhận bộ giờ rảnh; không suy là đã xác nhận chỉ vì Calendar trả về khoảng trống. |
| `availability_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O03 TimeConstraint

Ràng buộc thời gian được dùng trong Summary và Task.

API liên quan: [dòng 2](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A2:J2), [dòng 6](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A6:J6), [dòng 9](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A9:J9), [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 14](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A14:J14).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `kind` | string enum: deadline \| window | Phân biệt hạn chót và khoảng thực hiện; dùng window trong JSON, không dùng timeframe. |
| `start_at` | date-time | Mốc sớm nhất được bắt đầu; cần làm rõ trước UF2. |
| `finish_by_at` | date-time \| null | Bắt buộc khác null khi kind=window; null khi kind=deadline. |
| `deadline_at` | date-time \| null | Bắt buộc khác null khi kind=deadline; null khi kind=window. Mốc cuối theo nhánh là deadline_at hoặc finish_by_at; không yêu cầu hai trường cùng có giá trị. |
| `input_timezone` | string | Múi giờ để hiểu đúng các ngày và giờ người dùng nhập. |

**Hai nhánh JSON đã được duyệt ngày 02/11/2026:** deadline → start_at + deadline_at, finish_by_at=null; window → start_at + finish_by_at, deadline_at=null. start_at phải trước mốc cuối. Draft có thể chưa đủ để làm rõ; thông tin AI chưa đủ được giữ trong snapshot nội bộ, time_constraint công khai vẫn là null cho đến khi hợp lệ. Mốc cuối đã qua chặn can_plan/confirm-summary; start quá khứ với mốc cuối tương lai vẫn được xem xét lập lịch từ hiện tại. Thẻ 02 online còn chờ cập nhật mô tả.

### O04 TaskSummary

Bảng tóm tắt cho user kiểm tra ở UF1.

API liên quan: [dòng 2](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A2:J2), [dòng 4](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A4:J4), [dòng 5](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A5:J5), [dòng 6](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A6:J6), [dòng 7](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A7:J7).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `title` | string \| null | Tên ngắn gọn để nhận diện công việc. |
| `description` | string \| null | Nội dung cụ thể người dùng muốn thực hiện. |
| `goal` | string \| null | Mục tiêu muốn đạt, nếu cần lưu riêng với nội dung và phạm vi. |
| `scope` | string \| null | Đầu ra và giới hạn công việc để chia bước đúng ý người dùng. |
| `extra_requirements` | array<string> | Các yêu cầu bổ sung cần giữ khi chia việc và xếp lịch. |
| `time_constraint` | O03 \| null | Khoảng bắt đầu và kết thúc đã làm rõ theo O03. |
| `availability` | DraftAvailability \| null | Giờ rảnh user nhập cùng yêu cầu voice/text hiện tại; được review và xác nhận cùng summary UF1. Không ghi đè giờ rảnh chung hoặc plan khác. |
| `availability_ref` | AvailabilityRef \| null | Tương thích nháp cũ đã chủ động chọn nguồn giờ rảnh chung. Nháp dùng availability trực tiếp không bắt buộc ref; sửa/xóa giờ riêng không tự quay lại nguồn chung. |
| `importance` | integer \| null | Số nguyên dương tùy chọn; lớn hơn nghĩa là quan trọng hơn, null khi user chưa chọn. Deadline/phụ thuộc vẫn ưu tiên trước; không cho phép collision. |
| `clarification_issues` | array<ClarificationIssue> | Những chỗ thiếu, mơ hồ hoặc mâu thuẫn cần hỏi lại. |
| `can_plan` | boolean | Đủ dữ liệu đầu vào UF1, không còn vấn đề chặn; chưa chứng minh lịch khả thi và không thay trạng thái sẵn sàng của tích hợp UF2. |
| `field_sources` | map<string, FieldSource> | Nguồn của từng giá trị: người dùng nhập, sửa, xác nhận hoặc AI đề xuất; giữ được yêu cầu gốc khi sửa tóm tắt. |

**DraftAvailability:** `{timezone:string, weekly_rules:WeeklyRule[], date_overrides:DateOverride[]}`. Dùng lại kiểu ngày/thứ và giờ địa phương của O02; `weekday` 1=Thứ Hai..7=Chủ Nhật. Ngày cụ thể thay thế quy tắc lặp của chính ngày đó; `intervals:[]` nghĩa là ngày đó không rảnh. Trường ngày/thứ không rõ từ AI giữ trong ngữ cảnh nội bộ và câu hỏi, không tự đoán. `PATCH summary.availability` thay toàn bộ trường; omitted giữ nguyên, null xóa. Client không gửi confirmed_at hoặc cờ khả thi trong trường này.

**Ranh giới UF1–UF2:** `can_plan` chỉ kiểm đủ đầu vào và giờ khai báo còn sử dụng được, chưa chứng minh khả thi. Confirm UF1 xác nhận giờ cùng summary, chưa tạo Task. UF2 đọc đúng Draft/revision, trừ bận nội bộ/manual trong Context rồi tạo Candidate; confirm UF2 mới lưu Task/Steps/Sessions. Không buộc nhập giờ cố định trong Settings. Chưa tích hợp Google thật nên không bảo đảm đã kiểm lịch bận bên ngoài.

### O05 PlanningDraft

Bản nháp trước khi có Task chính thức.

API liên quan: [dòng 2](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A2:J2), [dòng 3](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A3:J3), [dòng 4](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A4:J4), [dòng 5](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A5:J5), [dòng 6](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A6:J6), [dòng 7](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A7:J7), [dòng 27](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A27:J27).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `client_draft_id` | string | Mã nháp ổn định để lưu máy và đồng bộ không bị nhân đôi. |
| `stage` | string enum | Giai đoạn của nháp: đang thu thập, xem tóm tắt, có phương án hoặc đã xác nhận. |
| `summary` | O04 | Bản tóm tắt hiện tại, kể cả khi còn thiếu dữ liệu. |
| `summary_revision` | integer | Phiên bản tóm tắt; tránh dùng kết quả AI từ bản đã cũ. |
| `planning_input_revision` | integer | Phiên bản đầu vào lập kế hoạch; tăng một lần khi raw_input, summary hoặc user_step_proposal đổi. Chỉ sửa ý tưởng không hủy xác nhận UF1 nhưng làm Candidate cũ stale. |
| `summary_confirmed_revision` | integer \| null | Revision tóm tắt đã được user xác nhận; null khi chưa xác nhận hoặc tóm tắt đã thay đổi. |
| `candidate_ref` | CandidateRef \| null | Tham chiếu phương án đang xem bằng candidate_id/review_revision; không nhúng lịch chính thức vào Draft. |
| `source_thought_id` | string \| null | Bản ghi suy nghĩ đã khởi tạo nháp này, nếu có. |
| `confirmed_task_id` | string \| null | Liên kết Task thật; chỉ có sau khi UF2 lưu thành công. |
| `raw_input` | string \| null | Nội dung chữ ban đầu hoặc bản STT đã nhận; phục hồi và sửa lỗi nhận dạng. Không lưu audio thô vào log. |
| `input_source` | string | Nhập chữ, nói trực tiếp hoặc tạo từ Thought; phân biệt cách tiếp nhận và quyền cần dùng. |
| `user_step_proposal` | string \| null | Ý tưởng chia bước do người dùng cung cấp ở bước 0 UF2; chuỗi gốc hoặc null nếu chọn Để trợ lý đề xuất. Giữ khi lưu nháp và sửa phương án. |
| `draft_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |

### O06 PlanCandidate

Phương án chia việc và lịch đang được review ở UF2.

API liên quan: [dòng 9](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A9:J9), [dòng 10](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A10:J10), [dòng 11](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A11:J11), [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `candidate_id` | string | Mã nhận diện phương án đang hiển thị. |
| `draft_id` | string | ID nháp nguồn của Candidate. |
| `source_summary_revision` | integer | Phiên bản tóm tắt đã dùng để tạo phương án. |
| `source_planning_input_revision` | integer | Bản đầu vào Draft dùng để tạo Candidate; xác nhận phải trùng planning_input_revision hiện tại, cùng các revision tóm tắt, phương án và nguồn lịch. |
| `steps` | array<CandidateStep> | Các bước đã chia, có client_step_key và order_index duy nhất trong Candidate; chưa có ID chính thức. |
| `sessions` | array<CandidateSession> | Các phiên đã đề xuất; mỗi phiên có client_session_key và tham chiếu bước dự kiến. Chưa có timer, alarm hoặc sự kiện Calendar vận hành. |
| `feasibility` | O10 | Kết quả kiểm tra phương án có đáp ứng các ràng buộc. |
| `min_inter_session_gap_seconds` | integer | Khoảng cách tối thiểu giữa phiên: 900 giây, kể cả khác Task. |
| `review_revision` | integer | Phiên bản phương án người dùng xem; xác nhận phải đúng bản này. |
| `decomposition_state` | partial \| complete | partial khi còn phạm vi chưa chia; không dùng kết quả giai đoạn đầu để tuyên bố toàn Task đã hoàn tất phân rã. |
| `unexpanded_scope` | string \| null | Mô tả phạm vi còn tổng quan/chưa chia; bắt buộc có nội dung khi partial. Không phải một thực thể con. |
| `planning_horizon` | TimeInterval | Khoảng bắt đầu–kết thúc của giai đoạn chi tiết, mặc định 14 ngày sắp tới và không vượt window được xác nhận. |
| `source_revisions` | SourceRevisions | Các revision của giờ rảnh, cấu hình, lịch nền và CalendarContext dùng tính; xác nhận phải kiểm lại các nguồn này. |
| `user_step_proposal` | string \| null | Ý tưởng chia bước nguồn đã dùng để tạo phương án, hoặc null khi người dùng giao trợ lý đề xuất. |
| `stale` | boolean | Server tính từ toàn bộ revisions/nguồn hiện hành; không tin cờ false lưu từ lần tính cũ. |

CandidateStep/CandidateSession tương ứng O08/O09, dùng client key trước confirm và chưa có lịch vận hành. feasible chỉ áp dụng các Steps trong verification_scope và horizon; không tự bao gồm unexpanded_scope. Candidate cũ có schema PlanningPart chỉ được chuyển đổi để đọc, bị đánh stale và phải tạo lại trước confirm.

### O07 Task

Công việc chính thức sau xác nhận UF2.

API liên quan: [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 13](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A13:J13), [dòng 14](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A14:J14).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `title` | string | Tên công việc đã được xác nhận. |
| `description` | string | Nội dung công việc cần thực hiện. |
| `scope` | string | Đầu ra và giới hạn đã thống nhất; giúp đánh giá hoàn thành. |
| `extra_requirements` | array<string> | Giữ các yêu cầu riêng từ bảng tóm tắt UF1. |
| `time_constraint` | O03 | Giới hạn bắt đầu và hoàn tất của công việc. |
| `importance` | integer \| null | Số nguyên dương tùy chọn; lớn hơn nghĩa là quan trọng hơn, null khi user chưa chọn. Deadline/phụ thuộc vẫn ưu tiên trước; không cho phép collision. |
| `status` | string enum | Trạng thái thực hiện của Task; không đồng nghĩa trạng thái đã chia hay đã xếp và không tự complete do timer hết. |
| `source_draft_id` | string | Liên kết nháp nguồn và ngăn một nháp tạo nhiều Task. |
| `decomposition_state` | string enum | not_started, partial, complete; còn unexpanded_scope thì chưa complete. Candidate được tạo có partial hoặc complete. |
| `scheduling_state` | string enum | Tổng hợp: not_scheduled, partial, complete; chỉ complete khi toàn bộ phạm vi đã được chia và xếp đủ. |
| `availability_ref` | AvailabilityRef \| null | Chỉ giữ liên kết nguồn cũ nếu Task thực sự dùng nguồn đó; luồng giờ riêng không tạo ref giả. |
| `availability` | DraftAvailability \| null | Snapshot giờ riêng của Task tại confirm; không tự đổi khi sửa một Draft khác. Task cũ thiếu dữ liệu giữ null. |
| `revision` | integer >=1 | Phiên bản Task, dùng khi yêu cầu sửa giờ của Task đã lưu. |
| `source_summary_revision` | integer \| null | Phiên bản summary đã xác nhận để tạo Task; null nếu dữ liệu cũ không có bằng chứng. |
| `source_planning_input_revision` | integer \| null | Phiên bản đầu vào đã dùng khi confirm. |
| `unexpanded_scope` | string \| null | Phạm vi còn chưa chia, giữ nguyên ở Task để lập tiếp về sau. |
| `task_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `steps` | array<Step> | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |
| `sessions` | array<Session> | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |
| `source_summary` | O04 | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

source_summary giữ đầy đủ O04, kể cả goal và nguồn giá trị. Không tự thêm goal vào top-level Task khi JSON chưa có trường này.

### O08 Step

Bước có đầu ra cần đạt, thuộc trực tiếp một Task.

API liên quan: [dòng 9](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A9:J9), [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 14](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A14:J14).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `task_id` | string | Công việc sở hữu bước này. |
| `title` | string | Hành động cần làm ở bước này. |
| `expected_output` | string | Đầu ra dùng hỏi người dùng bước đã xong hay chưa. |
| `order_index` | integer | Thứ tự duy nhất của Step trong Task; giữ thứ tự khi chia tiếp, không cần cấp Part. |
| `estimated_base_seconds` | integer | Ước lượng làm chính gốc, chưa buffer, micro-task hoặc nghỉ; không bị ghi đè bằng actual hay thời lượng đã nhân k. |
| `remaining_base_seconds` | integer | Ước lượng phần còn lại để lập lịch tiếp; không tự lấy timer trừ estimate. |
| `buffer_multiplier` | number | Hệ số dự phòng đã dùng; chỉ nhân một lần vào làm chính. |
| `status` | string enum | Bước đang chờ, đang làm, hoàn thành hay bị bỏ qua. |
| `estimate_source` | string enum | Nguồn ước lượng gốc và revision liên quan; phân biệt AI đề xuất với người dùng đã chỉnh/xác nhận. |
| `step_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |

### O09 Session

Phiên dự kiến để thực hiện một Step; micro-task nằm trước phiên chính.

API liên quan: [dòng 9](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A9:J9), [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 14](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A14:J14), [dòng 15](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A15:J15), [dòng 16](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A16:J16).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `step_id` | string | Bước được thực hiện trong phiên; từ đó xác định Task. |
| `scheduled_start` | date-time | Giờ bắt đầu theo lịch đã xếp. |
| `scheduled_end` | date-time | Giờ kết thúc; bằng giờ bắt đầu cộng tổng thời lượng phiên. |
| `micro_task_seconds` | integer \| null | Độ dài gợi ý nếu có số ước lượng; hiện có thể null. Không có timer micro-task và không cộng vào Session chính. |
| `work_seconds` | integer | Tổng thời gian làm chính của mọi quãng, đã gồm buffer phân bổ. |
| `rest_seconds` | integer | Tổng nghỉ bên trong phiên; không gồm 15 phút giữa hai Session. |
| `segments` | array<SessionSegment> | Các quãng làm chính/nghỉ bên trong phiên. Không có segment micro-task. |
| `segments[].order_index` | integer | Vị trí của từng quãng trong phiên. |
| `segments[].kind` | string enum | Quãng làm việc (work) hoặc nghỉ (rest). |
| `segments[].duration_seconds` | integer | Thời lượng quãng; cộng các quãng để tính tổng làm và nghỉ. |
| `duration_seconds` | integer | Tổng work_seconds + rest_seconds của phiên chính; không cộng micro-task trước phiên hoặc 900 giây giữa hai Session. |
| `micro_task_text` | string | Một gợi ý do AI tạo dựa trên mục tiêu, Step và phần việc của Session sắp tới. Hiển thị trước phiên; không bắt buộc đánh dấu hoàn thành. |
| `schedule_revision` | integer | Phiên bản lịch đã xác nhận chứa Session; dùng loại yêu cầu start và lời nhắc đã cũ. |
| `status` | string enum | Trạng thái lịch của Session, như đã xếp hoặc đã hủy; kết quả thực hiện nằm ở Run/CheckIn và không thay kết quả Task. |
| `calendar_event_link_id` | string \| null | Tham chiếu O24 khi đã có yêu cầu xuất Calendar. session_id vẫn là ID nội bộ; không thay bằng google_event_id. |
| `start_state` | string enum | waiting trước ngưỡng, not_started khi tới scheduled_start + 900 giây mà chưa có Run, started khi đã chấp nhận Bắt đầu. Không đồng nghĩa Step/Task chưa hoàn thành. |
| `start_decision_due_at` | date-time | Mốc scheduled_start + 900 giây. Tới mốc mà chưa có Run thì UF5 cho hai nút Bắt đầu và Hoãn lại; không tạo actual hoặc ended_at giả. |
| `session_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |

### O10 FeasibilityReport

Kết quả kiểm khả thi theo đúng phạm vi được xét.

API liên quan: [dòng 9](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A9:J9), [dòng 11](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A11:J11), [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 33](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A33:J33), [dòng 35](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A35:J35).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `status` | string enum | Kết quả theo phạm vi: có lịch hợp lệ, đã chứng minh vi phạm, chưa tìm được phương án hoặc chưa đủ dữ liệu; không gọi mọi thất bại tìm kiếm là vô nghiệm. |
| `required_seconds` | integer \| null | Tổng thời lượng các phiên chính trong phạm vi được kiểm: làm chính và nghỉ bên trong, không cộng micro-task. |
| `available_seconds` | integer \| null | Tổng giờ rảnh còn lại sau khi loại lịch bận, không cộng trùng. |
| `unscheduled_seconds` | integer \| null | Thời lượng đã xác định nhưng chưa xếp trong phạm vi report; không đồng nghĩa thiếu từng ấy tổng giờ. Phần chưa ước lượng được liệt kê riêng. |
| `verification_scope` | VerificationScope | Phạm vi được kiểm gồm Task/phần được chọn và horizon; không dùng kết quả phần đầu để tuyên bố toàn Task kịp hạn. |
| `fully_scheduled` | boolean | Chỉ true khi toàn bộ phạm vi ghi trong report có Session cụ thể và qua các kiểm tra; không tự đồng nghĩa toàn Task đã có lịch. |
| `blocking_reasons` | array<BlockingReason> | Lý do vướng: ô quá ngắn, sai thứ tự, thiếu khoảng nghỉ giữa phiên… |
| `deficit_seconds` | integer \| null | Cận dưới thiếu thời lượng khi so required_seconds với available_seconds cho phần đã ước lượng. Bằng 0 vẫn có thể không xếp được; không cộng 900 giây vào từng phiên. |
| `unscheduled_step_keys` | array<string> | client_step_key của Steps đã biết nhưng chưa được xếp đủ trong Candidate. |
| `unexpanded_scope` | string \| null | Phạm vi chưa chia/chưa ước lượng; không gộp thành 0 giây hoặc vào required_seconds. |
| `extension_candidate_at` | date-time \| null | Mốc kết thúc được gợi ý sau khi thử xếp thêm; không gọi là hạn tối thiểu tối ưu nếu chưa chứng minh. |

### O11 ScheduleVersion

Mốc phiên bản lịch đã xác nhận; không phải bản sao ghi đè các Run đã chạy.

API liên quan: [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 15](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A15:J15), [dòng 37](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A37:J37).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `version_number` | integer | Số phiên bản lịch; tăng khi một thay đổi được duyệt. |
| `base_version` | integer \| null | Bản lịch nền dùng tạo thay đổi; giúp phát hiện dữ liệu cũ. |
| `approved_at` | date-time | Thời điểm người dùng xác nhận bản lịch này. |
| `affected_task_ids` | array<string> | Những công việc bị thay đổi trong lần cập nhật. |
| `min_inter_session_gap_seconds` | integer | Lưu quy tắc cách phiên 900 giây của bản lịch. |
| `alarm_sync_state` | string enum | Kết quả thiết bị xác nhận đã cập nhật lời nhắc; không suy từ việc lưu DB hoặc đồng bộ Calendar thành công. |
| `calendar_sync_summary` | string enum | Trạng thái tổng hợp từ O24/O25 cho phiên bản lịch; lưu app thành công và ghi Google thành công là hai kết quả riêng. |

### O12 SessionRun

Một lần thực hiện thật của Session sau khi start được chấp nhận.

API liên quan: [dòng 16](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A16:J16), [dòng 17](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A17:J17), [dòng 18](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A18:J18), [dòng 19](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A19:J19), [dòng 20](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A20:J20), [dòng 32](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A32:J32).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `session_id` | string | Phiên theo lịch mà người dùng đang thực hiện. |
| `device_id` | string | Thiết bị đã được chấp nhận thực hiện Run; có trong response start và end hiện hành. |
| `started_at` | date-time | Thời điểm thực sự bắt đầu sau khi thao tác được chấp nhận. |
| `ended_at` | date-time \| null | Mốc kết thúc thực tế của Run, ghi ngay khi hết toàn phiên hoặc user dừng; không phải giờ trả lời check-in hay server nhận đồng bộ. |
| `run_state` | string enum | Lần thực hiện đang chạy, chờ phản hồi hay đã đóng; nghỉ nội bộ vẫn đang chạy. |
| `active_phase` | string enum | Quãng đang chạy: work hoặc rest; kết thúc là ended. Run không có phase micro vì micro-task ở trước phiên chính. |
| `segment_index` | integer \| null | Quãng đang chạy; giúp phục hồi đúng vị trí khi mở lại app. |
| `dnd_release_at` | date-time \| null | Mốc khôi phục Tập trung/DND: ended_at + 300 giây khi chưa xác nhận; nếu xác nhận cuối phiên sớm hơn thì dùng thời điểm đó. Chỉ khi Run kết thúc mới có mốc, không kéo dài actual. |
| `work_elapsed_seconds` | integer \| null | Số giây timer ghi ở quãng làm chính; giữ dữ liệu đã ghi dù user không trả lời check-in, không coi đây là bằng chứng hoàn thành. |
| `rest_elapsed_seconds` | integer \| null | Thời gian nghỉ bên trong đã ghi nhận; không đưa vào học buffer. |
| `actual_work_seconds` | integer \| null | Giá trị làm chính dùng đối chiếu, có nguồn rõ; thiếu dữ liệu là null. Không tính micro-task, nghỉ hoặc chờ check-in. |
| `actual_source` | string enum \| null | timer_proxy hoặc self_report; null nếu chưa có nguồn hợp lệ. Sửa tự báo cáo phải giữ lịch sử nguồn trước. |
| `outcome` | string enum | Kết quả riêng của phiên; không thay xác nhận Step hoặc Task đã xong. |
| `checkin_timeout_at` | date-time \| null | ended_at + 300 giây, chỉ có khi Run đã kết thúc. Tới hạn chưa trả lời thì no_respond. Hạn phản hồi không kéo dài timer hoặc actual. |
| `settings_snapshot` | SettingsSnapshot | Cấu trúc Session và cấu hình effective tại lúc start; đổi cài đặt sau đó không sửa Run hiện tại. |
| `run_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `run_revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |
| `check_in` | O13 \| null | Dòng 20 nhúng CheckIn cuối phiên sau khi Run dừng; không phải trường bắt buộc trên mọi response Run. |

check_in chỉ là dữ liệu nhúng trong kết quả end ở dòng 20; GET/start Run không bắt buộc nhúng nó. Không có micro_elapsed_seconds hoặc active_phase=micro trong hợp đồng hiện hành.

### O13 CheckIn

Phản hồi giữa phiên hoặc cuối Run; không dùng cho Session chưa bắt đầu.

API liên quan: [dòng 20](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A20:J20), [dòng 29](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A29:J29), [dòng 30](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A30:J30), [dòng 31](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A31:J31).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `run_id` | string | Lần thực hiện mà phản hồi này nói tới. |
| `kind` | string enum | Phân biệt hỏi giữa phiên và hỏi khi kết thúc toàn phiên. |
| `response_state` | string enum | open, answered hoặc no_respond cho check-in cuối phiên. Trả lời sau hạn cập nhật cùng CheckIn thành answered. not_started thuộc trạng thái bắt đầu của Session, không gán cho CheckIn có Run giả. |
| `session_outcome` | string enum | Kết quả phiên người dùng xác nhận; không tự suy từ timer. |
| `step_completed` | boolean \| null | Người dùng xác nhận đầu ra Step đã đạt hay chưa. |
| `task_completed` | boolean \| null | Xác nhận toàn bộ phạm vi Task, kể cả phần chưa chia; không lấy việc xong Step cuối của phần đang lập lịch làm hoàn tất Task. |
| `skip_scope` | SkipScope \| null | skipped cần skip_session, postpone_session hoặc skip_step. Không skipped thì null. Bỏ Session/Step không tự hoàn thành Task; thay đổi lịch đi qua UF6. |
| `remaining_note` | string \| null | Mô tả phần chưa xong để hỗ trợ ước lượng và xếp lại. |
| `asked_at` | date-time | Thời điểm mở câu hỏi; phân biệt câu hỏi giữa phiên và check-in cuối phiên. |
| `responded_at` | date-time \| null | Thời điểm người dùng thực sự trả lời; không dùng mốc này để kéo dài ended_at hoặc actual. |
| `response_revision` | integer | Phiên bản phản hồi để sửa câu trả lời trên cùng CheckIn và tránh ghi đè/đếm kết quả lặp. |
| `checkin_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |

### O14 Thought

Suy nghĩ được cất và liên kết tới Draft/Task khi chuyển đổi.

API liên quan: [dòng 24](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A24:J24), [dòng 25](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A25:J25), [dòng 26](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A26:J26), [dòng 27](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A27:J27), [dòng 28](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A28:J28).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `client_thought_id` | string | Mã ổn định giúp lưu hoặc đồng bộ không nhân đôi suy nghĩ. |
| `source_run_id` | string \| null | Lần thực hiện đang diễn ra khi suy nghĩ được ghi lại. |
| `input_type` | string enum; ví dụ API: audio | Giữ đúng nhãn audio trong ví dụ Sheet; không dùng raw_audio từ bản cũ. Nhánh text fallback có trong nghiệp vụ, tên enum wire đầy đủ cần thống nhất. |
| `local_audio_path` | string \| null (local) | Đường dẫn file trên thiết bị; không phải URL để Backend tự tải, không có trong DTO Thought của Sheet hiện tại. |
| `text_content` | string \| null | Nội dung chữ khi dùng phương án nhập dự phòng. |
| `attempt_count` | integer | Số lượt thu/lưu chủ động; tối đa ba lượt cho một lần cất. |
| `linked_draft_id` | string \| null | Nháp tạo việc từ suy nghĩ; chưa chứng minh đã tạo Task. |
| `converted_task_id` | string \| null | Task thật sau UF2 xác nhận và lưu thành công. |
| `capture_state` | string enum | Đang thu, đã lưu hoặc lỗi theo bằng chứng file và metadata; không báo Đã lưu khi chỉ mới thu xong. |
| `retention_expires_at` | date-time \| null | Mốc giữ/xóa khi đã có chính sách; null khi chưa áp dụng. Không mặc định một số ngày khi nhóm chưa chốt. |
| `thought_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `thought_revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O15 RescheduleProposal

Phương án thay đổi lịch chờ user duyệt.

API liên quan: [dòng 33](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A33:J33), [dòng 34](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A34:J34), [dòng 35](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A35:J35), [dòng 36](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A36:J36), [dòng 37](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A37:J37), [dòng 38](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A38:J38).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `trigger_reason` | string enum | Nguyên nhân đổi lịch: `checkin_incomplete`, `checkin_busy`, `checkin_skipped`, `missed_start`, `user_reschedule`, `early_completion_pull_forward`, `settings_change`, `expand_next_horizon`. |
| `target_task_id` | string \| null | Task chính khởi tạo yêu cầu đổi lịch (null khi đổi cài đặt chung); khác với các Task được phép dời và các Task thực tế bị ảnh hưởng. |
| `affected_task_ids` | array<string> | Các Task thực sự bị tác động; phải là tập con của allowed_task_ids, không dùng làm phạm vi cấp quyền. |
| `base_schedule_revision` | integer | Bản lịch nền; dùng ngăn áp một phương án đã cũ. |
| `proposed_changes` | ProposedChanges | Các phiên dời/thêm/hủy, Steps mới thêm và `next_horizon_from`/`next_horizon_to`. Mở rộng dùng cùng Task, không ghi đè phần đã thực hiện. |
| `feasibility` | ProposalFeasibilityData | Kết quả kiểm cả collision, thời hạn và khoảng cách (trạng thái `feasible` \| `deficit` \| `insufficient_data` \| ...). |
| `explanation` | string | Lý do ngắn gọn để người dùng hiểu phương án. |
| `status` | string enum | Wire enum theo API Sheet (SY03): `pending` \| `approved` \| `rejected` \| `superseded`. |
| `proposal_revision` | integer | Phiên bản phương án (`>= 1`); duyệt phải đúng bản đã xem. |
| `allowed_task_ids` | array<string> | Phạm vi người dùng cho phép dời; affected_task_ids là tập thực sự bị tác động và không được vượt phạm vi này. |
| `source_revisions` | ProposalSourceRevisions | Revision 5 nguồn (`task`, `schedule`, `availability`, `settings`, `calendar`) bên cạnh `base_schedule_revision`; nguồn đổi thì phải kiểm và trình lại. |
| `supersedes_proposal_id` | string \| null | Bản cũ bị thay thế bởi proposal này; chỉ một bản hiện hành còn được phép duyệt cho mỗi user. |
| `calculated_at` | date-time \| null | Mốc tính toán phương án; dùng kiểm `PREVIEW_STALE` khi mở lại app hoặc quá `start_window`. |
| `continuation_request` | ContinuationRequest \| null | Tham số xin làm tiếp ngay (SY02 / dòng 33) nếu proposal thuộc luồng Continuation. |
| `gap_exceptions` | array<GapException> | Ngoại lệ khoảng nghỉ trong Continuation (`immediate_continuation` \| `user_borrowed_break`, `borrowed_seconds <= 120`). |
| `start_window` | StartWindow \| null | Cửa sổ bắt đầu hợp lệ (`earliest_start`, `latest_start`, `not_before`, `not_after`) cho Continuation (R05). |
| `approval_envelope` | ApprovalEnvelope \| null | Ràng buộc `actual_start_plus_fixed_work_plus_min_break_le_latest_start` kiểm tại thời điểm bấm Approve (R05). |
| `pending_notification_state` | string enum | Trạng thái gửi một thông báo cho proposal đang chờ khi user rời app; không suy đã đọc từ việc gửi thành công, không nhắc lặp mặc định. |
| `pending_notification_sent_at` | date-time \| null | Mốc đã gửi thông báo nếu quan sát được; null khi chưa gửi. Thông báo mở bản hiện hành để kiểm lại thời gian và revision, không tự duyệt. |
| `proposal_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `stale` | boolean | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O16 Reminder

Lời nhắc gắn với Session, lịch và thiết bị.

API liên quan: [dòng 22](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A22:J22), [dòng 23](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A23:J23).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `session_id` | string | Phiên mà lời nhắc thuộc về; không nhắc từ candidate chưa duyệt. |
| `schedule_revision` | integer | Bản lịch tạo lời nhắc; dùng bỏ lời nhắc cũ sau đổi lịch. |
| `kind` | string enum | Nhắc trước phiên hoặc check-in. Micro-task đi cùng lời nhắc trước phiên, không có timer hoặc alarm tự start riêng. |
| `fire_at` | date-time | Thời điểm dự kiến phát lời nhắc. |
| `dedupe_key` | string | Khóa chống tạo hoặc phát trùng cùng một lời nhắc. |
| `delivery_state` | string enum | Lời nhắc đã lên lịch, đã phát, bị hủy hoặc gặp lỗi. |
| `user_action` | string enum \| null | Thao tác thực nhận được, như mở hoặc bỏ qua; không suy người dùng đã đọc. |
| `device_id` | string | Thiết bị thực thi lời nhắc; giúp phân biệt lưu lịch với đã cài thông báo trên thiết bị cụ thể. |
| `reminder_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |

### O17 DeviceState

Thông tin quyền và trạng thái mà thiết bị báo về.

API liên quan: [dòng 21](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A21:J21).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `device_id` | string | Nhận diện thiết bị hoặc bản cài ứng dụng. |
| `notification_permission` | string enum | Thiết bị có quyền hiển thị thông báo hay không. |
| `microphone_permission` | string enum | Có được dùng micro khi người dùng chủ động ghi âm. |
| `dnd_permission` | string enum | Quyền hoặc khả năng điều khiển Không làm phiền. |
| `alarm_capability` | string enum | Khả năng và quyền cài báo thức; khác quyền thông báo. |
| `active_run_id` | string \| null | Lần thực hiện đang hoạt động trên thiết bị. |
| `last_schedule_revision` | integer \| null | Phiên bản lịch mới nhất thiết bị đã nhận. |
| `dnd_owner_run_id` | string \| null | Run mà phần DND do app áp dụng thuộc về; timeout cũ không được tắt DND của Run mới hoặc DND do user bật. |
| `dnd_restore_snapshot` | DndRestoreSnapshot \| null (local) | Giữ tại thiết bị theo nền tảng; DTO báo trạng thái không gửi toàn bộ snapshot hệ thống. |
| `dnd_apply_state` | string enum | Thiết bị báo áp dụng thành công, thất bại hoặc không hỗ trợ; backend không tự suy là đã bật. |
| `dnd_restore_state` | string enum | Kết quả khôi phục khi user xác nhận cuối phiên hoặc tới ended_at + 300 giây; thiết bị báo theo khả năng quan sát được. |
| `reported_at` | date-time | Mốc thiết bị báo trạng thái; trạng thái cũ không là bằng chứng DND hiện tại đã được dọn. |
| `app_visibility` | string enum | Trạng thái foreground, background hoặc unknown do thiết bị báo; hỗ trợ thông báo proposal chờ khi user rời app, không suy từ im lặng là đã bỏ ứng dụng. |
| `device_report_revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O18 CommandReceipt

Dấu nhận một lệnh đã xử lý để chống thực hiện trùng.

API liên quan: [dòng 63](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A63:J63).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `command_id` | string | Mã một ý định thao tác; giữ nguyên khi gửi lại do mất phản hồi. |
| `command_type` | string | Loại thao tác như xác nhận kế hoạch, bắt đầu hoặc duyệt đổi lịch. |
| `payload_hash` | string (nội bộ) | Chống dùng lại cùng khóa với nội dung khác; không trả ra DTO GET command. |
| `expected_revision` | integer \| null | Phiên bản người gửi đang dựa vào để phát hiện xung đột. |
| `result_reference` | ResourceRef \| null | Kết quả đã tạo; gửi lại cùng lệnh nhận lại kết quả này. |
| `applied_at` | date-time \| null | Thời điểm thao tác được ghi thành công. |
| `queue_state` | string enum (local) | Trạng thái hàng chờ Mobile; không phải state của receipt server. |
| `state` | string | Trạng thái xử lý lệnh trên server; ví dụ applied. Không đồng nhất với queue_state ở máy. |

GET /commands trả command_id/command_type/state/result_reference/applied_at. Các trường payload_hash, expected_revision và queue_state trong mô hình khái niệm không được suy thành trường response.

### O19 AdaptationSuggestion

Gợi ý cá nhân hóa chờ user lựa chọn.

API liên quan: [dòng 43](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A43:J43), [dòng 44](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A44:J44).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `kind` | string enum | Gợi ý đổi buffer hoặc giảm tần suất lời nhắc. |
| `sample_count` | integer | Số mẫu hợp lệ đã dùng; một Step không thành nhiều mẫu theo số phiên. |
| `current_value` | number \| array<integer> | Theo kind: buffer_multiplier dùng number dương trong miền policy; reminder_offset đích đồng bộ theo cấu hình nhắc tuần 2: mảng 1–3 integer >=0, không trùng; code/contract gợi ý UF7 hiện chưa được xác nhận hỗ trợ nhiều mốc, phải review khi triển khai UF7. Không nhận object cấu hình tùy ý chưa có schema. |
| `suggested_value` | number \| array<integer> | Giá trị gợi ý cùng biến thể với current_value; edited_value/accepted_value của API cũng theo kind này. |
| `reason` | string | Giải thích vì sao có gợi ý; không suy diễn chẩn đoán. |
| `policy_revision` | string | Quy tắc hoặc thuật toán đã tạo gợi ý. |
| `status` | string enum | Gợi ý đang chờ, được chấp nhận, bị từ chối hoặc thay thế. |
| `sample_refs` | array<ResourceRef> | Tham chiếu các Step hoàn tất cùng phạm vi và actual hợp lệ; một Step nhiều Session vẫn chỉ một mẫu. |
| `model_revision` | string \| null | Phiên bản model nếu gợi ý có dùng AI; null khi chỉ dùng quy tắc hoặc thống kê. |
| `supersedes_suggestion_id` | string \| null | Thẻ cũ bị thay bằng gợi ý hiện hành của cùng vấn đề; không tạo nhiều thẻ vì một chuỗi sửa cài đặt. |
| `suggestion_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `suggestion_revision` | integer | Revision của thẻ gợi ý; gửi đúng khi phản hồi/chỉnh/sửa. |

### O20 AuditEvent

Sự kiện phục vụ theo dõi thay đổi và nguồn dữ liệu.

API liên quan: [dòng 19](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A19:J19), [dòng 32](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A32:J32).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `event_id` | string | Mã sự kiện; giúp đồng bộ mà không đếm lặp. |
| `event_type` | string | Loại sự kiện như bắt đầu, kết thúc hoặc đổi lịch. |
| `resource_ref` | ResourceRef | Đối tượng liên quan để truy vết sự kiện. |
| `occurred_at` | date-time | Thời điểm sự kiện thực sự xảy ra, kể cả khi offline. |
| `received_at` | date-time | Thời điểm hệ thống nhận được; có thể muộn hơn lúc xảy ra. |
| `command_id` | string \| null | Liên kết lệnh nguồn, giúp đối soát retry và tác động. |
| `duration_source` | string enum \| null | Nguồn thời gian được ghi: đồng hồ hay người dùng tự báo cáo. |
| `resource_revision` | integer \| null | Revision dữ liệu tại thời điểm xảy ra để đối soát thay đổi, trả lời muộn và đồng bộ offline. |

### O21 CalendarContext

Bản dữ liệu lịch bận dùng làm căn cứ xếp lịch.

API liên quan: [dòng 57](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A57:J57), [dòng 61](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A61:J61), [dòng 62](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A62:J62).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `selected_calendar_ids` | array<string> | Những lịch người dùng đã chọn và cho phép đọc. |
| `selected_calendar_sources` | array<CalendarSource> | Danh sách nguồn chuẩn theo cặp connection_id + google_calendar_id. selected_calendar_ids chỉ giữ để hiển thị tương thích, không đủ để đối soát nhiều tài khoản. |
| `query_start_end` | TimeInterval | Khoảng thời gian đã truy vấn; biết dữ liệu bao phủ đến đâu. |
| `busy_intervals` | array<BusyInterval> | Các khoảng bận có nguồn. Khi nhận diện sự kiện của chính Session phải dựa O24/O26; không trừ một khoảng gộp rồi vô tình bỏ luôn sự kiện bên ngoài trùng giờ. |
| `fetched_at` | date-time | Thời điểm lấy dữ liệu; dùng đánh giá độ mới trước xác nhận. |
| `sync_state` | string enum | Cho biết dữ liệu hợp lệ, lỗi một phần hoặc không truy cập được. |
| `calendar_errors` | array<CalendarError> | Lỗi theo cặp connection_id/google_calendar_id; không đổi lỗi thành lịch bận trống. |
| `event_snapshots` | array<O26> | Các sự kiện O26 đã đọc để nhận diện bản của app và thay đổi bên ngoài; không mặc định lưu toàn bộ nội dung Calendar dài hạn. |
| `coverage_state` | string enum | Dữ liệu đã bao phủ đủ nguồn/thời gian được chọn, chỉ một phần hoặc không truy cập được; thiếu nguồn không được coi là lịch trống. |
| `availability_ref` | AvailabilityRef \| null | Chỉ dùng khi tạo Context theo nguồn cũ; không đồng thời với nguồn Draft. |
| `source_draft_id` | string \| null | Draft sở hữu giờ riêng dùng lập Context; phải đúng owner và đúng Draft của Candidate. |
| `source_summary_revision` | integer \| null | Phiên bản summary tại lúc chụp; sửa giờ/summary làm nguồn cũ không còn hợp lệ để confirm. |
| `availability_snapshot` | DraftAvailability \| null | Bản giờ riêng đã chụp; không ghi đè snapshot cũ âm thầm. |
| `manual_busy_intervals` | array<ManualBusyInterval> | Khoảng bận user nhập; giữ ID và nguồn để gửi lại không nhân đôi. |
| `excluded_sources` | array<ExcludedCalendarSource> | Nguồn user xác nhận loại khỏi lần lập phương án; giữ lý do/thời điểm, không coi nguồn lỗi đã được kiểm đủ. |
| `context_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O22 — Không còn dùng trong hợp đồng mới

PlanningPart được loại khỏi mô hình nghiệp vụ và DTO. Giữ mã O22 để các mã O23–O26 không bị đổi. Phạm vi chưa chia nằm ở `Candidate/Task.unexpanded_scope`; thứ tự Steps thuộc trực tiếp Task.

Migration mở rộng vẫn giữ bảng/cột Part cũ để đối chiếu dữ liệu và receipt; runtime mới không đọc/ghi Part như thực thể nghiệp vụ. Chưa drop vật lý hoặc xóa dữ liệu cũ.

### O23 CalendarConnection

Kết nối Google và danh sách lịch được chọn.

API liên quan: [dòng 45](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A45:J45), [dòng 46](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A46:J46), [dòng 47](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A47:J47), [dòng 48](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A48:J48), [dòng 49](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A49:J49), [dòng 50](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A50:J50), [dòng 51](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A51:J51), [dòng 52](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A52:J52).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `provider` | string | Nguồn lịch tích hợp; hiện là Google Calendar. |
| `account_ref` | string | Tài khoản đã kết nối để hiển thị và phân biệt các kết nối; không phải access token. |
| `read_calendar_ids` | array<string> | Các lịch user chọn đọc giờ bận; không mặc định đọc mọi lịch. |
| `write_calendar_id` | string \| null | Một lịch đích có quyền ghi để xuất Session; có thể là lịch riêng ProActive. |
| `granted_scopes` | array<string> | Các quyền đã được cấp; có quyền đọc chưa có nghĩa được tạo/sửa/xóa. |
| `connection_state` | string enum | Đang kết nối, chỉ đọc, cần cấp lại quyền hoặc đã ngắt; cho UI phản ánh đúng khả năng thực tế. |
| `credential_ref` | string (nội bộ) | Tham chiếu credential phía Backend; không đưa vào response thông thường hoặc log. |
| `last_successful_sync_at` | date-time \| null | Lần đồng bộ thành công gần nhất; không nhầm với lần thử gần nhất. |
| `connection_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O24 CalendarEventLink

Ánh xạ một Session với sự kiện Google.

API liên quan: [dòng 12](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A12:J12), [dòng 14](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A14:J14), [dòng 50](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A50:J50), [dòng 55](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A55:J55), [dòng 58](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A58:J58).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `session_id` | string | ID Session nội bộ; giữ nguyên khi dời cùng một phiên. |
| `connection_id` | string | Kết nối O23 đã dùng để đồng bộ sự kiện. |
| `google_calendar_id` | string | Lịch Google chứa sự kiện; event ID phải được hiểu cùng lịch và kết nối. |
| `google_event_id` | string \| null | ID sự kiện Google; có thể chưa có khi chưa tạo thành công. Không dùng thay Session ID. |
| `google_etag` | string \| null | Phiên bản sự kiện đã biết để phát hiện cập nhật/xóa trên bản đã cũ. |
| `desired_schedule_revision` | integer | Phiên bản lịch app muốn xuất; giúp yêu cầu cũ không ghi đè thay đổi mới. |
| `synced_schedule_revision` | integer \| null | Phiên bản lịch đã được Google xác nhận ghi thành công; có thể còn chậm hơn bản mong muốn. |
| `sync_state` | string enum | Chờ, đã đồng bộ, lỗi, xung đột hoặc cần kết nối lại; không xóa Task khi đồng bộ lỗi. |
| `last_synced_at` | date-time \| null | Mốc ghi thành công gần nhất của đúng liên kết. |

### O25 CalendarSyncJob

Lệnh ghi Google có hàng chờ và retry.

API liên quan: [dòng 56](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A56:J56), [dòng 58](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A58:J58), [dòng 59](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A59:J59).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `connection_id` | string | Kết nối cần dùng; ngắt kết nối dừng job mới/đang chờ theo chính sách hiện hành. |
| `target_kind` | string enum | Phân biệt sự kiện gắn Session với sự kiện Calendar do user thao tác trực tiếp. |
| `target_ref` | string | String ID nội bộ: target_kind=session trỏ CalendarEventLink; external_event trỏ thao tác/preview đã lưu. Worker dùng payload_snapshot cùng IDs/etag đã chốt; không dùng Google event ID chưa tồn tại khi create. |
| `operation` | string enum | create, update hoặc delete; thao tác đọc phục vụ context không phải job ghi. |
| `target_schedule_revision` | integer \| null | Bản lịch đích khi job gắn Session; null cho thao tác sự kiện ngoài Task. |
| `expected_google_etag` | string \| null | Bản sự kiện nguồn cho sửa/xóa; khác phiên bản phải đối soát, không ghi đè mù. |
| `payload_snapshot` | object \| null (nội bộ) | Payload của thao tác đã xác nhận; worker không tự đổi nội dung lệnh cũ. Không trả trong DTO job hiện hành. |
| `idempotency_key` | string (nội bộ) | Khóa ổn định cho job retry; không phải trường trong DTO job hiện hành. |
| `job_state` | string enum | Chờ, đang chạy, thành công, cần thử lại, xung đột, cần kết nối lại hoặc bị hủy. |
| `attempt_count` | integer | Số lần đã thử; khác ba lượt ghi âm của UF4. |
| `next_retry_at` | date-time \| null | Mốc được phép thử lại theo lỗi và chính sách vận hành, chưa gắn một chu kỳ cố định. |
| `last_error_code` | string \| null | Mã lỗi phục vụ trạng thái UI và đối soát; không chứa token hoặc toàn bộ nội dung riêng tư. |
| `job_id` | string | Trường bổ sung từ response Sheet; mã tài nguyên hoặc liên kết server. |
| `job_revision` | integer | Trường bổ sung từ response Sheet; xem điều kiện trả của endpoint tương ứng. |

### O26 CalendarEvent

Biểu diễn sự kiện Google được đọc hoặc thao tác.

API liên quan: [dòng 53](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A53:J53), [dòng 54](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A54:J54), [dòng 55](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A55:J55).

| Trường dữ liệu | Kiểu JSON | Tác dụng và ràng buộc |
| --- | --- | --- |
| `connection_id` | string | Tài khoản/kết nối dùng đọc hoặc ghi sự kiện. |
| `google_calendar_id` | string | Lịch chứa sự kiện, đi cùng google_event_id khi gọi CRUD. |
| `google_event_id` | string | Mã sự kiện do Google trả hoặc ID tạo theo quy tắc provider; không nhầm với iCalUID. |
| `etag` | string | Phiên bản sự kiện đang xem; dùng kiểm xung đột khi sửa/xóa. |
| `title` | string | Tiêu đề sự kiện để hiển thị và chỉnh theo quyền. |
| `description` | string \| null | Nội dung bổ sung khi người dùng muốn lưu; không bắt buộc đưa mọi yêu cầu riêng của Task lên Google. |
| `location` | string \| null | Địa điểm nếu có; không ảnh hưởng cách xác nhận Task. |
| `all_day` | boolean | Phân biệt cả ngày với sự kiện có giờ; giúp hiểu đúng start/end. |
| `start` | CalendarDateTime | Mốc bắt đầu có giờ và múi giờ, hoặc ngày bắt đầu nếu all_day. |
| `end` | CalendarDateTime | Mốc kết thúc không bao gồm chính đầu mút; với all_day là ngày kế sau ngày cuối, không tự đặt 23:59. |
| `transparency` | string enum | Sự kiện có chiếm giờ bận hay chỉ để tham khảo; không mặc định mọi sự kiện nhìn thấy đều chặn lịch. |
| `status` | string enum | Trạng thái provider như confirmed, tentative hoặc cancelled; không có nghĩa Step/Task completed. |
| `session_link_id` | string \| null | O24 nếu đây là sự kiện của một Session; null cho sự kiện ngoài app. |
| `reminders` | CalendarReminders | Lời nhắc Calendar; sự kiện Session do app tạo mặc định không bật thêm nhắc trùng. Không tự sửa nhắc của sự kiện ngoài. |
| `recurrence_ref` | RecurrenceRef \| null | Nhận diện sự kiện lặp/lần xuất hiện nếu có. Trước sửa/xóa phải làm rõ phạm vi; không ngầm áp cho toàn chuỗi. |

## 4. Trường chung và cấu trúc lồng

### Trường dùng chung và tên ID trong API

id là tên khái niệm trong mô hình. API dùng tên theo tài nguyên như draft_id, task_id, step_id, session_id, run_id, checkin_id, proposal_id hoặc context_id. Không thêm cả id và mã riêng vào response chỉ vì bảng này liệt kê id chung.

| Trường | Kiểu JSON | Tác dụng |
| --- | --- | --- |
| id | string | Mã định danh của bản ghi trên server. |
| user_id | string | Người dùng sở hữu dữ liệu; lấy từ ngữ cảnh đăng nhập. |
| created_at | date-time | Thời điểm bản ghi được tạo. |
| updated_at | date-time | Thời điểm bản ghi được cập nhật gần nhất. |
| revision | integer | Số phiên bản khi cần kiểm soát cập nhật đồng thời. |

user_id là khóa sở hữu khi lưu; backend suy từ xác thực, không nhận user_id tùy ý từ client. Không mặc định trả credential_ref, payload_hash, đường dẫn local hoặc mọi trường nội bộ trong API.

revision là tên chung. Run dùng run_revision; proposal dùng proposal_revision; candidate dùng review_revision; CheckIn có response_revision. Các giá trị này theo tài nguyên và không được dùng thay nhau.

### Cấu trúc lồng cần đọc cùng

Kiểu dùng chung được định nghĩa ở bảng dưới và mục bổ sung cuối thẻ. Các biến thể request/response cụ thể nằm trong Sheet API; tham số nghiên cứu và chính sách còn cần cấu hình được nêu trong capabilities.

| Tên kiểu | Các trường và kiểu chính |
| --- | --- |
| AvailabilityRef | availability_id: string; revision: integer. |
| CalendarContextRef | context_id: string; revision: integer. |
| CandidateRef | candidate_id:string; review_revision:integer; cả hai bắt buộc khi candidate_ref khác null. |
| TimeInterval | start_at: date-time; end_at: date-time. start_at phải nhỏ hơn end_at; các tên scheduled_start/end ở Session vẫn giữ nguyên. |
| SessionSegment | order_index: integer; kind: work\|rest; duration_seconds: integer. Tổng các quãng phải khớp work_seconds, rest_seconds và duration_seconds. |
| SourceRevisions | availability:integer\|null; preferences:integer; schedule:integer; calendar_context:CalendarContextRef. Nguồn Draft dùng availability=null; khóa Draft/summary nằm trong Candidate và Context, không dùng summary revision giả làm availability revision. |
| VerificationScope | candidate_steps → {scope_type,client_step_keys:array<string>,planning_horizon:TimeInterval}; persisted_schedule → {scope_type,task_ids:array<string>,session_ids:array<string>,step_ids:array<string>,planning_horizon:TimeInterval}. Chỉ kết luận phạm vi được kiểm, không tự bao gồm unexpanded_scope. |
| FieldSource | source: string enum; source_ref: string. Map field_sources dùng tên trường làm khóa để giữ nguồn do user nhập, AI gợi ý hoặc user xác nhận. |
| ClarificationIssue | code, field_path, message, question: string; blocking: boolean. |
| BlockingReason | code, message: string; có thể cần field/session/part ref khi đặc tả lỗi chi tiết. |
| SettingsSnapshot | effective_revision: integer; session: bản chụp O09 tại lúc start. Không cập nhật snapshot khi user đổi cài đặt sau đó. |
| CalendarDateTime | Event có giờ: date_time: date-time; time_zone: string. Event cả ngày: date: string YYYY-MM-DD. Không trộn hai nhánh; end của event cả ngày là ngày kết thúc không bao gồm. |
| ResourceRef | Các mã tài nguyên cần thiết, ví dụ {task_id: string}. Schema phụ thuộc loại tài nguyên; phải định nghĩa cụ thể cho từng endpoint. |
| CandidateStep / CandidateSession | client_step_key và client_session_key trước khi lưu; Session tham chiếu client_step_key. Sau confirm nhận key_mapping.steps/sessions. |
| `ProposedChanges` | Hợp đồng UF6 triển khai (`app/schemas/reschedule.py`): `{sessions_to_move, sessions_to_add, sessions_to_cancel, steps_to_add, next_horizon_from, next_horizon_to}`. Dùng `temp_session_id`/`before`/`after` cho phiên thêm/dời và giữ nguyên Run đang chạy/đã hoàn tất. |
| BusyInterval | {start_at:date-time,end_at:date-time,source:{kind:calendar_event\|confirmed_session\|manual,connection_id:string\|null,google_calendar_id:string\|null,google_event_id:string\|null,session_id:string\|null,client_interval_id:string\|null}}. calendar_event cần bộ 3 mã Google; confirmed_session cần session_id; manual cần client_interval_id. Chỉ loại own-event khi trùng mapping cụ thể, không loại mọi event của cùng calendar. |

### Kiểu dữ liệu bổ sung dùng trong API

CalendarSource — {connection_id:string,google_calendar_id:string}.

WeeklyRule — {weekday:integer 1..7 (Thứ Hai=1),start_local:string HH:mm,end_local:string HH:mm}; start_local<end_local, không qua đêm trong một dòng; khoảng qua đêm tách làm hai ngày. timezone là IANA của bộ giờ rảnh.

DateOverride — {date:string YYYY-MM-DD,intervals:array<{start_local:string HH:mm,end_local:string HH:mm}>}; thay toàn bộ lịch rảnh của ngày đó, [] nghĩa là ngày không rảnh; không trùng date.

TaskAvailabilityLimit — Kiểu tương thích nguồn Availability cũ: {task_id:string,intervals:array<TimeInterval>}. Không dùng thay DraftAvailability của yêu cầu mới.

SettingsChange — {preferences?:{work_interval_seconds?:integer>0,rest_interval_seconds?:integer>=0,reminder_offsets?:array<integer>=0 gồm 1–3 phần tử không trùng, giảm dần (đích tuần 2; code nền còn khóa 1),buffer_multiplier?:number>0 theo policy,reminder_style?:string},availability?:{timezone?:string,weekly_rules?:array<WeeklyRule>,date_overrides?:array<DateOverride>,task_limits?:array<TaskAvailabilityLimit>}}. Ít nhất một trường hợp lệ; không sửa revision/trường do server quản lý. Miền buffer và số giây min/max khác lấy capabilities, chưa tự gán số minh họa.

PreferenceChanges — Các trường cấu hình được phép sửa trong SettingsChange.preferences. Trên O01 requested_changes là PreferenceChanges|null; trên kết quả change-request requested_changes là SettingsChange có namespace preferences/availability.

StructuredChanges — Theo trigger: not_started → {session_ids:array<string> không rỗng}; step_completed_early → {completed_step_id:string,session_ids?:array<string>}; continue_next_session → {next_session_id:string}; remaining_work → {step_id:string,remaining_note:string}; extend_horizon (thiết kế UF6, chưa triển khai) → {selected_step_ids:array<string> không rỗng,planning_horizon:TimeInterval}; settings_change → {change_request_id:string}; calendar_change → {preview_id:string}. reschedule có thể dùng request_text và các constraint đã làm rõ. ID phải thuộc user/Task và phạm vi được cho phép; không tự thêm Task vào allowed_task_ids.

SuggestionValue — kind=buffer_multiplier: number>0 trong miền policy; kind=reminder_offset: array<integer> >=0, đích đồng bộ cấu hình tuần 2 là 1–3 phần tử không trùng, giảm dần; cần cập nhật contract/DTO/tests của gợi ý UF7 trước khi dùng nhiều mốc. current_value/suggested_value/edited_value/accepted_value phải cùng biến thể. Không suy chính sách học buffer từ một thẻ chỉnh nhắc.

ManualBusyInterval — {client_interval_id:string,start_at:date-time,end_at:date-time}; ID ổn định trong một ý định gửi lại, thời gian hợp lệ. Không dùng để giả rằng nguồn Google lỗi đã được kiểm tra.

ExcludedCalendarSource — {connection_id:string,google_calendar_id:string,reason:string,user_confirmed_at:date-time}. User xác nhận bỏ nguồn trong lần lập phương án; giữ calendar_errors, coverage_state=manual_fallback và phạm vi nguồn đã dùng, không ghi complete thay cho dữ liệu chưa kiểm.

CalendarError — {connection_id:string,google_calendar_id:string,error_code:string,retryable:boolean,requires_reconnect:boolean,observed_at:date-time}. Ghi nguồn lỗi cụ thể; không gắn calendar_errors=[] nếu đã biết nguồn đọc thất bại.

CalendarReminders — {use_default:boolean,overrides:array<{method:popup|email,minutes:integer 0..40320}>}. use_default=true thì không gửi overrides khi gọi Google; object app có thể giữ [] để hiển thị. Event Session do app quản lý dùng use_default=false,overrides=[] để tránh nhân lời nhắc trên Google và thiết bị.

RecurrenceRef — null cho sự kiện không lặp; nếu có: {recurring_event_id:string|null,original_start:CalendarDateTime|null,recurrence_rules:array<string>}. Instance cần recurring_event_id và original_start; series master có recurrence_rules. Đây là metadata đọc, không dùng iCalUID thay google_event_id.

DndRestoreSnapshot — Dữ liệu local theo thiết bị: {owner_run_id:string,device_id:string,captured_at:date-time,platform:string,previous_interruption_filter:string|null,applied_interruption_filter:string|null,app_applied:boolean,restore_state:not_needed|pending|restored|failed,restored_at:date-time|null}. Không gửi snapshot hệ thống qua DTO thông thường; O17 chỉ báo metadata owner/trạng thái. Khôi phục đúng phần app áp của Run, không sửa DND người dùng tự bật.

Importance — importance: integer dương|null. Giá trị lớn hơn có mức quan trọng cao hơn; null nghĩa user chưa chọn. Deadline/window và phụ thuộc vẫn là điều kiện ưu tiên trước; không dùng importance để cho phép collision.

## 5. DTO hỗ trợ và envelope API

Các cấu trúc dưới đây phục vụ endpoint cụ thể, không tự đánh thêm mã O27… hoặc tạo bảng DB chỉ vì có DTO. Trường được liệt kê từ các biến thể response hiện hành; có giá trị null trong mẫu không đủ để suy ra kiểu đầy đủ, nên dùng mô tả/ghi chú nguồn khi khóa model.

### Tạo và chỉnh Candidate UF2 — bổ sung 03/11/2026

`POST /api/v1/drafts/{draft_id}/candidates` giữ source_summary_revision, source_planning_input_revision, planning_horizon và calendar_context_ref. Có ba cách lấy Steps: để AI chia (mặc định); chọn selected_step_keys của Candidate nguồn hợp lệ; hoặc gửi manual_steps. Không gửi đồng thời manual_steps và selected_step_keys.

| Trường bổ sung | Kiểu | Ý nghĩa |
|---|---|---|
| manual_steps | array<ManualStepInput> hoặc null | User tự cung cấp Steps khi AI chưa sẵn sàng. Không gọi AI phân rã ở nhánh này |
| manual_steps[].order_index | integer >= 0 | Dãy liên tục 0..N−1, không trùng |
| manual_steps[].title | string 1–255 ký tự | Tên bước; theo giới hạn cột Step hiện có |
| manual_steps[].expected_output | string không rỗng | Đầu ra cần đạt |
| manual_steps[].estimated_base_seconds | integer > 0 | Lượng làm chính gốc, chưa nhân buffer |
| unexpanded_scope | string hoặc null | Chỉ gửi cùng manual_steps; mô tả phạm vi chưa chia. Không tạo Step giả cho phần này |

Server tạo client keys và ghi estimate_source=user_confirmed cho nguồn thủ công. Cùng Context/preferences và Algorithm được dùng như nhánh AI; vẫn cần user review và confirm riêng. A04 cần làm rõ trả 422 DECOMPOSITION_REQUIRES_CLARIFICATION, field user_step_proposal; Draft được giữ, chưa có Candidate mới.

`PATCH /api/v1/candidates/{candidate_id}` giữ expected_review_revision, nhận **đúng một** trong edits (sửa có cấu trúc hiện có) hoặc user_instruction (string 1–2000 ký tự, không chỉ khoảng trắng). Success vẫn là O06 PlanCandidate; không thay response bằng text của AI.

- NL chỉ sửa Step đã có: title, expected_output, estimated_base_seconds, order_index. Dùng đúng client_step_key qua reorder; không thêm/xóa/gộp hoặc sửa Session bằng NL trong bản này. Sửa Session có cấu trúc tiếp tục dùng edits.sessions.
- Kết quả AI nội bộ có keyed step_edits và các cờ cần hỏi lại/đổi Draft/thao tác chưa hỗ trợ. Đây là DTO nội bộ, không tạo object DB hoặc endpoint mới.
- Đổi availability/deadline/window trả 422 DRAFT_UPDATE_REQUIRED, next_action=edit_draft_and_reconfirm. User sửa Draft, xác nhận UF1 và tạo lại nguồn/phương án phù hợp; không áp một phần Step của yêu cầu hỗn hợp.
- 422 ADJUSTMENT_REQUIRES_CLARIFICATION / UNSUPPORTED_CANDIDATE_CHANGE / NO_CANDIDATE_CHANGES hoặc 503 AI_UNAVAILABLE giữ nguyên phương án/revision. Lỗi có câu hỏi/field_errors; không lấy assistant_note làm bằng chứng đã thay đổi lịch.
- Kết quả AI cũ gặp nguồn/review revision đã đổi bị chặn 409. Replay lệnh thành công vẫn đọc receipt trước kiểm bản đã thay đổi; cùng key khác payload bị từ chối.

### ConfirmSummaryResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `draft_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `stage` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `summary_confirmed_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `summary_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `planning_input_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### ConfirmCandidateResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `app_save_state` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `task` | O07 | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `key_mapping` | {steps:map<string,string>,sessions:map<string,string>} | Đổi client key sang ID thật; không có Part hoặc Google event ID. |
| `schedule_version` | O11 | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `alarm_sync_state` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `calendar_sync_summary` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### ScheduleResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `schedule_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `items` | array<DTO theo endpoint> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `next_cursor` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `alarm_sync_state` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `calendar_sync_summary` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### RunEventBatchResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `run_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `run_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `accepted_event_ids` | array<string> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `duplicate_event_ids` | array<kiểu phần tử cần đọc ghi chú> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### SettingsChangeResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `change_request_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `request_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `application_state` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `requested_revision` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `effective_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `requested_availability_revision` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `effective_availability_revision` | integer \| null | null cho nguồn giờ riêng; dùng revision Draft/Task để kiểm thay đổi. |
| `requested_changes` | SettingsChange | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `pending_proposal_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `target_draft_id` | string \| null | Draft được chỉnh giờ. |
| `target_task_id` | string \| null | Task được yêu cầu chỉnh giờ; thay đổi ảnh hưởng lịch đã chốt tạo pending proposal qua UF6. |
| `summary_revision` | integer \| null | Revision sau sửa Draft; đọc lại Draft trước confirm. |
| `planning_input_revision` | integer \| null | Revision đầu vào sau sửa Draft. |

### CancelSettingsChangeResult (Dòng 42)

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `change_request_id` | string | Mã yêu cầu thay đổi cài đặt bị hủy. |
| `application_state` | `"cancelled"` | Trạng thái sau khi hủy pending change request; không đảo cài đặt đã effective. |
| `effective_revision` | integer | Revision cài đặt đang có hiệu lực giữ nguyên hiện tại. |

### SuggestionResponse

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `suggestion_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `suggestion_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `kind` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `action` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `status` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `accepted_value` | SuggestionValue \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `application_state` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `effective_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `requested_revision` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `change_request_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `pending_proposal_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### CalendarAuthorizationResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `authorization_url` | string (URL) | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `authorization_request_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### CalendarConfigurationPreview

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `preview_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `preview_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `read_calendar_ids` | array<string> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `write_calendar_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `affected_session_ids` | array<string> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `migration_actions` | array<MigrationAction> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `source_revisions` | SourceRevisions | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `connection_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `base_connection_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `existing_event_action` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### CalendarConfigurationApplyResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `preview_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `configuration_state` | string enum | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `connection` | O23 | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `sync_jobs` | array<O25> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### CalendarEventChangePreview

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `preview_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `preview_revision` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `operation` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `before` | O26 \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `after` | O26 \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `affected_session_ids` | array<string> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `required_proposal_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `source_revisions` | SourceRevisions | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `connection_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `google_calendar_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `google_event_id` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `expected_google_etag` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `recurrence_scope` | single \| series \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### Capabilities

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `contract_version` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `policy_revision` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `offline_start_supported` | boolean | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `configured_parameters` | ConfiguredParameters | Các tham số nghiên cứu chưa cấu hình là null; xem policy_state. |
| `policy_state` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### ConfiguredParameters

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `buffer_multiplier_range` | null hiện tại; hình dạng miền hợp lệ cần khóa | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `minimum_sample_count` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `audio_max_seconds` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `audio_max_bytes` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `preview_ttl_seconds` | integer \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `default_reminder_offset_seconds` | integer | Wire hiện hành vẫn là số đơn 900. Đích nhắc tuần 2 dùng default `[900, 600, 300]`; cách công bố nhiều mốc trong capabilities cần Cương/Hưng chốt và cập nhật DTO/Mobile trước khi dùng, không đổi trường integer thành array một phía. |
| `start_grace_seconds` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `min_inter_session_gap_seconds` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `checkin_timeout_seconds` | integer | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### ResponseMeta

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `request_id` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `server_time` | date-time | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### ErrorDetail

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `code` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `message` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `field_errors` | array<FieldError> (chưa khóa đủ trường) | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `retryable` | boolean | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `current_revisions` | object revision theo từng loại lỗi | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `next_action` | string | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### ListResult

| Trường | Kiểu JSON | Lưu ý |
| --- | --- | --- |
| `items` | array<DTO theo endpoint> | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |
| `next_cursor` | string \| null | Đọc điều kiện và ghi chú của endpoint; không cho client sửa trường dẫn xuất. |

### Envelope và ngoại lệ

- Thành công thông thường: {data:DTO|null, meta:{request_id:string,server_time:date-time}}. data=null là nhánh hợp lệ của GET active Run hoặc current suggestion khi không có dữ liệu.
- Thất bại thông thường: {error:{code,message,field_errors,retryable,current_revisions,next_action},meta:ResponseMeta}. Không dùng message để suy mã lỗi; giữ retryable và next_action đúng tình huống.
- Calendar OAuth callback dòng 46 là redirect theo hợp đồng; không ép vào success JSON envelope thông thường.
- List dùng items/next_cursor; loại phần tử theo endpoint, không có một List<object> chung bỏ mất schema.
- Multipart STT dùng purpose=uf1_input hoặc thought_to_draft. thought_id chỉ bắt buộc nhánh Thought; audio là file multipart, không gửi chuỗi đường dẫn local để server tự đọc.

### Candidate trước confirm và các phần thay đổi UF6

| Cấu trúc | Nhận diện và quan hệ |
|---|---|

| CandidateStep | client_step_key; title/expected_output/order_index/estimated_base_seconds/remaining_base_seconds/buffer_multiplier/estimate_source/status như O08; không có client_part_key. |
| CandidateSession | client_session_key + client_step_key; scheduled_start/end, micro-task, work/rest/segments/duration và start state như JSON dòng 9; không áp thêm mọi cột của Session đã lưu |
| KeyMapping | steps/sessions là map client key → server ID; không có tầng Part. |
| StepChange / SessionChange | Thiết kế UF6: add dùng client key và after; sửa/dời/hủy dùng ID và before/after. Không thay việc đã hoàn tất hoặc Run đang active. |
| SourceRevisions | availability:integer\|null; preferences:integer; schedule:integer; calendar_context:CalendarContextRef. Nguồn Draft dùng availability=null; khóa Draft/summary nằm trong Candidate và Context, không dùng summary revision giả làm availability revision. |

### Danh sách endpoint để truy vết schema

Bảng này là chỉ mục, không thay request validation. Các trường dưới đây là **trường xuất hiện trong ví dụ** (không tự suy tất cả là required hoặc tất cả đã được liệt kê). Các điều kiện required/tùy chọn phải đọc cả ghi chú cột C/D trong Sheet.

| Dòng Sheet | Method / endpoint | Dữ liệu request trong ví dụ |
| --- | --- | --- |
| 2 | POST `/api/v1/drafts` | body: client_draft_id, input_source, raw_input; summary có thể gửi kèm giờ riêng và form đã nhập. |
| 3 | GET `/api/v1/drafts` | query: limit; body: null |
| 4 | GET `/api/v1/drafts/{draft_id}` | path: draft_id; body: null |
| 5 | POST `/api/v1/drafts/{draft_id}/analyze` | path: draft_id; body: expected_summary_revision, message, expected_planning_input_revision |
| 6 | PATCH `/api/v1/drafts/{draft_id}` | path: draft_id; body: expected_summary_revision, changes, expected_planning_input_revision |
| 7 | POST `/api/v1/drafts/{draft_id}/confirm-summary` | path: draft_id; body: expected_summary_revision |
| 8 | POST `/api/v1/voice/transcriptions` | multipart_fields: purpose, thought_id, device_id, file |
| 9 | POST `/api/v1/drafts/{draft_id}/candidates` | body: source_summary_revision, source_planning_input_revision; selected_step_keys, planning_horizon, calendar_context_ref là tùy chọn. |
| 10 | GET `/api/v1/candidates/{candidate_id}` | path: candidate_id; body: null |
| 11 | PATCH `/api/v1/candidates/{candidate_id}` | path: candidate_id; body: expected_review_revision, edits |
| 12 | POST `/api/v1/candidates/{candidate_id}/confirm` | path: candidate_id; body: expected_review_revision, expected_summary_revision, expected_source_revisions, expected_planning_input_revision |
| 13 | GET `/api/v1/tasks` | query: limit; body: null |
| 14 | GET `/api/v1/tasks/{task_id}` | path: task_id; body: null |
| 15 | GET `/api/v1/schedule` | query: from, to; body: null |
| 16 | POST `/api/v1/sessions/{session_id}/start` | path: session_id; body: expected_schedule_revision, device_id, requested_start_at |
| 17 | GET `/api/v1/runs/active` | body: null |
| 18 | GET `/api/v1/runs/{run_id}` | path: run_id; body: null |
| 19 | POST `/api/v1/runs/{run_id}/events` | path: run_id; body: device_id, expected_run_revision, events |
| 20 | POST `/api/v1/runs/{run_id}/end` | path: run_id; body: device_id, expected_run_revision, event_id, ended_at, end_reason, work_elapsed_seconds, rest_elapsed_seconds |
| 21 | PUT `/api/v1/devices/{device_id}/state` | path: device_id; body: device_report_revision, notification_permission, microphone_permission, dnd_permission, alarm_capability, active_run_id, last_schedule_revision, dnd_owner_run_id, dnd_apply_state, dnd_restore_state, reported_at, app_visibility |
| 22 | GET `/api/v1/reminders` | query: device_id, from, to, schedule_revision; body: null |
| 23 | POST `/api/v1/reminders/{reminder_id}/receipt` | path: reminder_id; body: device_id, schedule_revision, delivery_state, occurred_at, user_action |
| 24 | POST `/api/v1/thoughts` | body: client_thought_id, source_run_id, input_type, capture_state, attempt_count, text_content |
| 25 | GET `/api/v1/thoughts` | query: limit; body: null |
| 26 | GET `/api/v1/thoughts/{thought_id}` | path: thought_id; body: null |
| 27 | POST `/api/v1/thoughts/{thought_id}/draft` | path: thought_id; body: expected_thought_revision, text_content |
| 28 | DELETE `/api/v1/thoughts/{thought_id}` | path: thought_id; body: expected_thought_revision |
| 29 | GET `/api/v1/runs/{run_id}/check-ins` | path: run_id; query: kind; body: null |
| 30 | POST `/api/v1/runs/{run_id}/check-ins` | path: run_id; body: kind, asked_at, expected_run_revision |
| 31 | PUT `/api/v1/check-ins/{checkin_id}/response` | path: checkin_id; body: expected_response_revision, responded_at, session_outcome, step_completed, task_completed, skip_scope, remaining_note |
| 32 | POST `/api/v1/runs/{run_id}/actual-corrections` | path: run_id; body: expected_run_revision, self_report_work_seconds, reason, occurred_at |
| 33 | POST `/api/v1/proposals` | body: trigger_reason, target_task_id, allowed_task_ids, expected_base_schedule_revision, expected_source_revisions, request_text, continuation_request (SY02) |
| 34 | GET `/api/v1/proposals/current` | body: null (trả `ProposalData \| null`) |
| 35 | GET `/api/v1/proposals/{proposal_id}` | path: proposal_id; body: null |
| 36 | POST `/api/v1/proposals/{proposal_id}/rebuild` | path: proposal_id; body: expected_proposal_revision, request_text (`201`) |
| 37 | POST `/api/v1/proposals/{proposal_id}/approve` | path: proposal_id; body: expected_proposal_revision, expected_base_schedule_revision, expected_source_revisions, start_continuation, device_id, event_id, expected_source_run_revision |
| 38 | POST `/api/v1/proposals/{proposal_id}/reject` | path: proposal_id; body: expected_proposal_revision, reason |
| 39 | GET `/api/v1/preferences` | body: null |
| 40 | GET `/api/v1/availability` | body: null; query: from, to |
| 41 | POST `/api/v1/settings/change-requests` | body: changes; sửa preferences cần expected_preferences_revision (lần đầu 0); sửa giờ cần target_draft_id + hai revision Draft hoặc target_task_id + expected_task_revision. Không dùng expected_availability_revision cho giờ riêng. |
| 42 | POST `/api/v1/settings/change-requests/{change_request_id}/cancel` | path: change_request_id; body: expected_request_revision |
| 43 | GET `/api/v1/suggestions/current` | query: kind; body: null |
| 44 | POST `/api/v1/suggestions/{suggestion_id}/response` | path: suggestion_id; body: expected_suggestion_revision, action, annoyance_feedback; body: expected_suggestion_revision, action; body: expected_suggestion_revision, action, edited_value |
| 45 | POST `/api/v1/calendar/authorizations` | body: device_id, return_target |
| 46 | GET `/api/v1/calendar/oauth/callback` | query: code, state; body: null |
| 47 | GET `/api/v1/calendar/connections` | body: null |
| 48 | GET `/api/v1/calendar/connections/{connection_id}` | path: connection_id; body: null |
| 49 | GET `/api/v1/calendar/connections/{connection_id}/calendars` | path: connection_id; body: null |
| 50 | POST `/api/v1/calendar/connections/{connection_id}/configuration-previews` | path: connection_id; body: expected_connection_revision, read_calendar_ids, write_calendar_id, existing_event_action |
| 51 | POST `/api/v1/calendar/configuration-previews/{preview_id}/confirm` | path: preview_id; body: expected_preview_revision, source_revisions |
| 52 | POST `/api/v1/calendar/connections/{connection_id}/disconnect` | path: connection_id; body: expected_connection_revision |
| 53 | GET `/api/v1/calendar/connections/{connection_id}/events` | path: connection_id; query: google_calendar_id, from, to, limit; body: null |
| 54 | GET `/api/v1/calendar/connections/{connection_id}/events/{event_id}` | path: connection_id, event_id; query: google_calendar_id; body: null |
| 55 | POST `/api/v1/calendar/event-change-previews` | body: operation, connection_id, google_calendar_id, payload, recurrence_scope; body: operation, connection_id, google_calendar_id, payload, recurrence_scope, google_event_id, expected_google_etag; body: operation, connection_id, google_calendar_id, recurrence_scope, google_event_id, expected_google_etag |
| 56 | POST `/api/v1/calendar/event-change-previews/{preview_id}/confirm` | path: preview_id; body: expected_preview_revision, source_revisions |
| 57 | POST `/api/v1/calendar/connections/{connection_id}/refresh` | path: connection_id; body: from, to, expected_connection_revision |
| 58 | GET `/api/v1/calendar/sync-jobs/{job_id}` | path: job_id; body: null |
| 59 | POST `/api/v1/calendar/sync-jobs/{job_id}/retry` | path: job_id; body: expected_job_revision |
| 60 | GET `/api/v1/capabilities` | body: null |
| 61 | POST `/api/v1/planning-contexts` | body: source_draft_id, expected_summary_revision, from, to, selected_calendar_sources, manual_busy_intervals, excluded_sources; nguồn cũ dùng availability_ref thay hai trường Draft. |
| 62 | GET `/api/v1/planning-contexts/{context_id}` | path: context_id; body: null |
| 63 | GET `/api/v1/commands/{command_id}` | path: command_id; body: null |
| 64 | GET `/api/v1/runs/{run_id}/upcoming-schedule-context` | path: run_id; query: from, to, cursor, limit; body: null |
| Bổ sung (O00) | POST `/api/v1/auth/google` | body: id_token (public; trả `GoogleLoginData` gồm `access_token`, `refresh_token`, `user`) |
| Bổ sung (O00) | POST `/api/v1/auth/refresh` | body: refresh_token (public; trả `TokenBundle`; `[Target BE-03]` rotate `jti` + chặn reuse) |
| Bổ sung (BE-03 Target) | POST `/api/v1/auth/logout` | body: refresh_token (**Option 1 đã chốt — chưa code**: public theo `refresh_token`, revoke `jti` idempotent) |
| Bổ sung (O00) | GET `/api/v1/auth/me` | body: null (yêu cầu `AppBearer`; trả `CurrentUser`) |
| Bổ sung (UF1) | DELETE `/api/v1/drafts/{draft_id}` | path: draft_id; body: null (xóa Draft chưa confirm; đã có Task/confirmed trả `409 INVALID_STATE_TRANSITION`) |
| Bổ sung (Voice TTS) | POST `/api/v1/voice/tts` | body: text, voice (tổng hợp Edge-TTS trả `TtsSynthesisData`; lỗi trả `422`/`503`/`504` + `next_action="use_on_device_tts"`) |
| Bổ sung (UF5 Voice) | POST `/api/v1/voice/uf5-checkin-prompt` | body: kind, is_final_session_of_step, expected_output, session_outcome, include_audio, voice (luôn `200`, soft-fallback `fallback_to_device_tts`) |
| Bổ sung (System) | GET `/api/v1/health` | body: null (public; alias `GET /health` ẩn schema; trả `{"status":"ok","database":"ok"}` hoặc `503 DATABASE_UNAVAILABLE`) |

## 6. Quy tắc nghiệp vụ tác động đến schema

1. **Ba lần xác nhận:** UF1 xác nhận dữ liệu đầu vào; UF2 confirm lưu kế hoạch đầu tiên; UF6 approve áp dụng thay đổi lịch đã có. Không gộp ba thao tác thành một trạng thái confirmed chung.
2. **Revision Draft:** raw_input/summary/user_step_proposal thực sự đổi → planning_input_revision tăng một lần trong lần ghi. Summary thay đổi tăng summary_revision và mất xác nhận UF1; raw_input thay đổi cũng vô hiệu xác nhận. Chỉ sửa ý tưởng bước 0 giữ summary_revision và summary_confirmed_revision, làm Candidate cũ stale. Metadata/confirm/no-op không tự tăng đầu vào.
3. **Revision Candidate:** tạo bằng source_summary_revision và source_planning_input_revision; ý tưởng đọc từ Draft. PATCH kiểm expected_review_revision. Confirm kiểm expected_review_revision, expected_summary_revision, expected_planning_input_revision, expected_source_revisions trong transaction áp dụng.
4. **Session:** một Session có thể chứa nhiều quãng work/rest; duration_seconds=work_seconds+rest_seconds. Micro-task là một gợi ý chuẩn bị trước Session, không có timer/Run riêng, không nằm trong segments, không cộng vào duration/actual/Google event.
5. **Ba mốc 900 giây khác nhau:** reminder mặc định trước phiên 900 giây (user được đổi); start grace 900 giây → not_started nếu chưa có Run; gap tối thiểu 900 giây giữa mọi hai Session liên tiếp, cùng/khác Task. Một Session đứng riêng không phải cộng gap trước/sau.
6. **Bắt đầu muộn/sớm:** cần kiểm lại lịch, giờ rảnh, thứ tự và gap trước chấp nhận start; không tự tạo Run khi đến giờ hoặc sau micro-task. Sau not_started, user vẫn có thể chọn Bắt đầu hoặc Hoãn; ảnh hưởng lịch phải qua UF6.
7. **Check-in/DND:** ended_at là lúc Run thực sự dừng; checkin_timeout_at=ended_at+300 giây. User xác nhận check-in cuối thì khôi phục phần DND/Tập trung app đã áp; nếu chưa phản hồi thì chậm nhất đến mốc timeout. Giữa phiên không tự tắt. Thiết bị chỉ khôi phục đúng owner Run; không tắt DND vốn do user bật, không đảm bảo đã khôi phục chỉ từ suy đoán Backend.
8. **Kết quả thực hiện:** not_started thuộc Session chưa có Run; no_respond là CheckIn cuối chưa phản hồi; outcome vẫn unknown khi chưa xác nhận. Timer hết không tự complete. Hoàn thành Step sớm được user xác nhận theo đầu ra; phần Session tương lai không còn cần làm được xử lý qua UF6, không tự hoàn tất Task.
9. **Actual:** tách work/rest và timer_proxy/self_report; thiếu nguồn là null. Không phản hồi không được gán planned làm actual. Phản hồi muộn sửa cùng CheckIn, không mở Run/đếm mẫu mới, không dời ended_at.
10. **Feasibility:** chỉ feasible khi có lịch cụ thể trong verification_scope, đúng giờ rảnh/không chồng/thứ tự/thời hạn/gap. deficit_seconds là cận dưới thiếu tổng thời lượng; 0 không bảo đảm xếp được. search_inconclusive không phải đã chứng minh vô nghiệm. Phần chưa ước lượng giữ null và liệt kê riêng.
11. **Horizon:** mặc định chi tiết 14 ngày trong giới hạn được xác nhận. Hoàn tất phạm vi này không nghĩa toàn Task được xếp hoặc hoàn thành. Mở rộng do user đề nghị, giữ cùng Task và Part IDs; có thể phải AI chia phần chưa phân tích trước Algorithm.
12. **Giờ rảnh và Google:** user xác nhận giờ rảnh, Calendar bổ sung bận. Query availability không có from/to → effective_intervals=null. Có query nhưng thực sự không rảnh mới []. Context giữ cặp nguồn, coverage, errors; không đổi Google lỗi thành [] để cho kế hoạch qua kiểm.
13. **Proposal:** allowed_task_ids là phạm vi user cho phép dời; affected_task_ids là tập con thực tế bị ảnh hưởng. Một proposal hiện hành/user; revisions chống duyệt cũ. Đóng app giữ lịch đã chốt và phương án chờ, gửi một thông báo theo quy tắc; mở lại kiểm nguồn/thời gian trước duyệt.
14. **Calendar CRUD:** tạo/sửa/xóa qua preview/confirm; event gắn Session đổi giờ/xóa qua UF6. Phân biệt app_save_state với sync Google; worker dùng etag và revision, không làm sống lại Session đã hủy hoặc ghi đè bản mới. Đổi lịch đích phải xử lý keep/move tường minh.
15. **Cấu hình và học buffer:** requested khác effective, giữ snapshot Run hiện tại. Chỉ học từ Step hoàn tất cùng phạm vi với actual có nguồn; một Step là một mẫu. Miền k, cỡ mẫu, chu kỳ gợi ý và độ nhỏ micro-task cần nghiên cứu/policy, không mặc định 1.2, 2 phút, 7–10 phiên hoặc mỗi tuần.
16. **Retry/quyền:** user_id từ app token; Idempotency-Key giữ nguyên cùng ý định/payload, khác nội dung cùng khóa bị từ chối. Confirm lưu dữ liệu+receipt+outbox nguyên tử, gọi Google sau commit. Không tự thêm Task khác vào phạm vi UF2 hoặc UF6.

## 7. Tách API, DB và dữ liệu local

- Trường dùng chung của DB (id/user_id/created_at/updated_at/revision) được áp theo model thực tế, không tự nhúng tất cả vào response. Không có user_id tự tham chiếu trên bảng User gốc.
- Một ref có ID/revision không bắt buộc FK vật lý tới cột bất biến của mọi snapshot. Database review cách lưu phiên bản và ràng buộc trước migration; không tuyên bố tương thích DB chỉ vì dùng JSON.
- Các dữ liệu audio path/DND snapshot/queue local không trở thành dữ liệu Backend phải nhận/trả. Thiết bị báo trạng thái và revision đúng DTO.
- Lưu lịch trong transaction có kiểm quyền/revision và CAS hoặc khóa phù hợp. Hai confirm đồng thời không được cùng áp lên lịch nền cũ. Worker dùng outbox đã commit; không giữ transaction dài chờ AI/Google.
- Unique theo phạm vi: client_draft_id + user; command_id + user; client key trong nguồn Part/Step/Session; source_draft_id chống tạo nhiều Task đầu tiên.
- Undo Thought đã chốt tuần 2: lưu bền file/metadata local, outbox giữ 5 giây trước khi gửi; Undo cất mới hủy gửi nguyên tử và dọn file. Xóa Thought có sẵn giữ khả năng khôi phục trong 5 giây trước thực hiện xóa; giữ dữ liệu còn phụ thuộc nghiệp vụ. Phải kiểm restart, tranh chấp gửi/hủy và retry. Chưa xác nhận code/API đã hỗ trợ; không áp retention này mặc định cho mọi loại tài nguyên khác.
- Bí mật và transcript/audio không ghi log. credential_ref/payload_hash/payload_snapshot nội bộ không trả ra chỉ vì object có chúng.
- offline_start_supported=false trong capabilities hiện hành; cache/thu âm local không tự đồng nghĩa được start độc lập offline.

## 8. O00 User và Authentication — 11/11/2026

Auth contract triển khai: [AUTH_GUIDE.md](AUTH_GUIDE.md). PR17 bàn giao Backend/Docs; Mobile Google login chờ tích hợp riêng. OpenAPI khai báo AppBearer (HTTP bearer, JWT) cho CurrentUserDep; login và callback Calendar không yêu cầu app Bearer, alias callback vẫn ẩn có chủ ý. Google Sign-In đổi Google ID token tại `POST /api/v1/auth/google`; `GET /api/v1/auth/me` và API nghiệp vụ dùng Bearer JWT app, đọc User DB/active. Google Sign-In tách Calendar consent; callback Calendar dùng state riêng.

| Field | Contract |
|---|---|
| user_id / JWT sub | UUID User nội bộ; API trả string |
| google_sub | unique Google subject đã xác minh, chỉ nội bộ |
| email | Google verified email bắt buộc, <=320; DB unique; không tự link theo email |
| display_name | trimmed Google name, fallback email local part, cắt tối đa100 |
| is_active | DB false →403 ACCOUNT_DISABLED; User đã xóa →401 |
| preferences | tạo đúng một UserPreference theo defaults khi tạo User; không reset khi login lại |

Login200 dùng SuccessEnvelope[GoogleLoginData]: access_token, token_type=bearer, expires_in=3600, refresh_token, refresh_expires_in=2592000, user=CurrentUser. Me200 dùng SuccessEnvelope[CurrentUser]. Request GoogleLoginRequest chỉ id_token, extra=forbid, không blank/padded, dài 1–16384. Login không cần Idempotency-Key hoặc app Bearer. Chi tiết JSON/lỗi/next_action ở mục 14 và Sheet `api!A65:J66`; không renumber routes cũ.

JWT app HS256 allowlist, JWT_SECRET_KEY riêng khỏi SECRET_KEY Calendar; required sub(UUID), exp/iat(integer), iss/aud chính xác, token_type (`access` | `refresh`). Access TTL 3600s, Refresh TTL 2592000s (kèm `jti` UUIDv4), skew mặc định 0 (0–60). Code hiện tại đã có `POST /api/v1/auth/refresh` (stateless); contract `POST /api/v1/auth/logout` (Option 1 — body `refresh_token`) và refresh token rotation (`jti` revocation) là target `BE-03` được chốt ở mục 14. Startup staging/production cấm dev_user; seed/create_all chỉ development/testing và AUTH_MODE=dev_user. Không tự thêm migration nếu chưa qua kế hoạch DB (`tn`).

Auth errors:422 VALIDATION_ERROR/fix_fields;401 INVALID_GOOGLE_ID_TOKEN hoặc UNAUTHENTICATED/authenticate;403 ACCOUNT_DISABLED/contact_support;409 ACCOUNT_LINK_CONFLICT/review_account_link;422 GOOGLE_PROFILE_INCOMPLETE/review_google_profile;503 AUTH_PROVIDER_UNAVAILABLE/retry_login (retryable=true). Các lỗi khác retryable=false. ErrorEnvelope giữ field_errors/current_revisions/meta; mọi Auth401 có WWW-Authenticate: Bearer. Đây là contract delta, không phải tuyên bố Google thật/Tester598cases đã đạt.

Password/streak legacy nằm ngoài phạm vi Auth này. Bằng chứng/lệnh tái lập và phạm vi PostgreSQL/Calendar QA mới ở [PR17_AUTH_VERIFICATION.md](PR17_AUTH_VERIFICATION.md); không cần handoff local để review.

## 9. Giới hạn và trạng thái triển khai C01 — 03/11/2026

- Đã sửa DTO/service/model cho giờ riêng Draft → Context → Candidate → Task và cấu trúc không Part. Đây là trạng thái code, chưa phải nghiệm thu.
- `POST planning-contexts`: đúng một nguồn Draft+summary revision hoặc Availability cũ. Nguồn Draft chụp giờ riêng vào availability_snapshot. Context sai owner/Draft/revision/range hoặc đã hết hiệu lực không dùng được để confirm.
- Tạo Candidate có thể chỉ định calendar_context_ref. Khi không chỉ định, Backend tìm Context hợp lệ đúng nguồn; chỉ tự tạo manual_only nếu không có nguồn Google đọc đã cấu hình. Candidate giữ ref đó để PATCH/confirm không tự đổi sang Context khác.
- Google Calendar thật được tích hợp sau. coverage_state=manual_only chỉ xét giờ khai báo, lịch nội bộ và bận nhập tay; không chứng minh đã kiểm lịch bên ngoài. Confirm trả calendar_sync_summary=not_integrated, không tạo job/event giả.
- Settings sửa giờ của Draft tái dùng PATCH UF1, kiểm hai revision, không mất scope/yêu cầu và hủy xác nhận cũ khi dữ liệu đổi. Sửa Task hoặc cấu hình ảnh hưởng lịch đã lưu trả POLICY_NOT_CONFIGURED cho tới khi UF6 sẵn sàng. reminder_style có thể đổi riêng.
- Cuốn chiếu giữ 14 ngày chi tiết và unexpanded_scope ở Candidate/Task. Không tự mở 21 ngày, I03 hoặc scheduler nhiều Task. Lịch khả thi cho Steps đã biết không chứng minh toàn phần chưa chia kịp deadline.
- Candidate cũ được chuyển dạng khi đọc và đánh stale; tạo lại trước confirm. Receipt cũ được giải mã ở biên mà không áp dụng lại thay đổi nghiệp vụ.
- Migration `20261103_01_c01_sources.py` thêm snapshot/revision/trạng thái, chuyển thứ tự Steps cũ và giữ dữ liệu Part để đối chiếu. Đã kiểm migration trên DB SQLite tạm mới và có dữ liệu cũ; chưa áp vào DB app. Không hạ migration hoặc drop Part tự động.
- AI phân rã được yêu cầu trả Steps và unexpanded_scope; khi provider lỗi giữ Draft/Candidate cũ và trả AI_UNAVAILABLE, không giả lập thành công bằng ba bước mẫu.
- Phần UF3–UF7, Google CRUD và Auth production trong tài liệu vẫn là mục tiêu thiết kế; không suy hoàn thành từ thay DTO C01. Miền k/cỡ mẫu/chất lượng AI/STT và trải nghiệm ADHD vẫn cần kiểm riêng.

Bản đồng bộ local không tự cập nhật Backend Docs/Sheet hoặc triển khai code. Các quyết định tuần 2 được người dùng chốt ở Project/kế hoạch và ghi tại mục 12; phần còn mở phải tiếp tục chốt, cập nhật nguồn liên quan và DTO/tests cùng thay đổi.

- Script review: `_temp/c01_tests/conftest.py`, `test_unit.py`, `test_integration.py`, `system_test.md`. User đã duyệt chạy: unit 10/10 và integration 10/10 đạt ngày 03/11/2026; system chưa chạy, phải thao tác app thật.
- Static lint/type đạt. Bộ 20 ca C01 đã chạy trên SQLite tạm/AI giả; không thay kiểm app, provider thật hoặc PostgreSQL. Mốc 175 ca ngày 02/11 là kết quả trước đợt đổi contract này.
- Test cũ còn dùng PlanningPart cần được chuyển fixture/assert theo contract mới; không sửa/xóa test để che lỗi hoặc coi suite cũ đã chạy đạt.
- Mobile cần bỏ Part keys, đọc giờ riêng và SourceRevisions.availability=null, gửi đúng revisions/Context. Không sửa source Mobile trong đợt Backend này.

- DTO AI nội bộ có goal, time_constraint chưa đầy đủ và issue theo trường; API Mobile vẫn trả PlanningDraft/TaskSummary hiện hành. Snapshot ai_context không trả ra JSON công khai; lưu trong summary._api_meta của Draft và phục hồi qua DB.
- AI null/omitted không xóa dữ liệu user. PATCH giữ quy tắc omitted/null hiện có. AI bổ sung yêu cầu được trim/loại trùng chính xác, giữ yêu cầu cũ; user sửa form có thể thay cả danh sách.
- Gửi lại full form không tự xác nhận các trường không đổi. Muốn giữ giá trị cũ ở field-review, gửi PATCH chỉ trường đó cùng giá trị; đổi giá trị cũng cần gửi đúng trường user sửa. Issue khác vẫn chặn can_plan. AI giải thích bất lợi có căn cứ, không tự khẳng định khả thi hoặc số phút thiếu.
- Ghi Draft và CommandReceipt trong cùng transaction. UF1/Voice có receipt DB; GET commands theo owner chỉ trả ref công khai. Dịch vụ AI/STT có thể gọi lại sau sự cố trước khi lưu; kết quả nghiệp vụ không được áp dụng hai lần.
- Settings bootstrap: expected_*_revision=0 khi tài nguyên chưa tồn tại, lưu lần đầu revision=1. Không đổi nhóm nào thì không tăng revision nhóm đó. Lần đầu preferences cần đủ giá trị user chọn, không tự gán hệ số pilot. Nếu preferences chưa có thì effective_revision=0 trong kết quả bootstrap Availability để thể hiện chưa cấu hình; GET preferences vẫn báo cần cấu hình.
- POST settings/change-requests hiện chỉ áp dụng trước khi có Task. Khi đã có kế hoạch, trả 503 POLICY_NOT_CONFIGURED/wait_for_configuration cho tới khi luồng tác động/UF6 sẵn sàng; chưa giả trả proposal. Chưa triển khai cancel hoặc toàn bộ UF7.
- Luồng cũ: Availability đã xác nhận là khung được phép, CalendarContext liên kết availability_ref. Luồng mới: giờ riêng trong Draft được xác nhận cùng UF1; bộ đọc Context/UF2 còn chờ nối. Calendar giữ vai trò nguồn bận có nguồn gốc/độ mới; Google thật chưa được triển khai.
- UF2 dùng fallback Python tầng 3 khi các provider lỗi hoặc hết thời gian: sinh đề xuất ba bước theo thời lượng tập trung user và phạm vi đầu vào, kèm micro-task. Đề xuất vẫn phải qua scheduler kiểm khả thi và user xác nhận trước khi lưu lịch chính thức. Chưa hoàn tất phân rã nhiều tầng A04 hoặc lập tiếp Task đã lưu I03.


## 11. Điểm giao UF1 mới — 03/11/2026

- Các endpoint tạo, đọc, sửa, phân tích và xác nhận Draft giữ nguyên đường dẫn. Create/PATCH nhận `summary.availability`; response TaskSummary trả cùng trường, bên cạnh các trường hiện có.
- Input voice/text đầu tiên chứa công việc, thời gian và giờ rảnh. Thiếu/mơ hồ thì hỏi bổ sung trong UF1, không tự dùng giờ mặc định hoặc buộc cấu hình Settings trước.
- Giờ theo thứ lặp hoặc ngày cụ thể dùng múi giờ IANA. `date_overrides` thay quy tắc tuần của chính ngày đó; `intervals:[]` là ngày không rảnh. Chỉ có ngày đóng không đủ để xác nhận một kế hoạch cần giờ làm.
- PATCH omitted giữ giờ cũ; gửi availability thay toàn bộ trường; null xóa giờ riêng và không tự quay lại ref chung. Khi áp giờ riêng, ref cũ bị bỏ. Sửa thực sự tăng revision và hủy xác nhận summary cũ.
- AI đề xuất khác dữ liệu user đã nhập/xác nhận thì giữ bản cũ và trả vấn đề review. Trường `availability_update_mode` merge/replace là DTO AI nội bộ, không phải field client gửi trong TaskSummary.
- Khung AI chưa đủ ngày/giờ hoặc không hợp lệ được giữ trong ai_context nội bộ và clarification_issues; không trả nó như một WeeklyRule hợp lệ. ai_context không xuất hiện trong API công khai.
- UF1 kiểm có khoảng khai báo giao với thời gian thực hiện còn lại, chưa trừ Google hay chứng minh đủ thời lượng. Confirm chỉ xác nhận đầu vào, chưa tạo Task/Steps/Sessions.
- Nối UF2 cần dùng đúng Draft và revision, không lẫn Context giữa yêu cầu; phải chốt cách giữ nguồn trong Candidate/Task trước khi bỏ guard. Chưa thay request planning-contexts hoặc SourceRevisions thành một hợp đồng mới chưa được triển khai.
- Kiểm tra gần nhất: 175 ca Backend/review đạt bằng DB tạm và provider giả ngày 02/11/2026; lint/type đạt. Đây không phải nghiệm thu AI/STT/Google thật hoặc trải nghiệm Mobile. Lượt sửa tài liệu ngày 03/11 không chạy lại bộ test.


## 12. Quyết định tuần 2 và trạng thái code nền — 03/11/2026

Quyết định đã được người dùng chốt, lấy [Project mục 8.2](../../Project.md) và [kế hoạch tuần 2](../week2_detailed_plan.md) làm tham chiếu local. Docs/Sheet online chưa được cập nhật trong lượt này. Kết quả kiểm nền ghi ở [biên bản nền tuần 2](../week2_baseline.md).

| Nội dung | Yêu cầu đã chốt | Trạng thái triển khai tại nền tuần 2 |
| --- | --- | --- |
| Nhắc trước phiên | Default mới `[900, 600, 300]`, 1–3 mốc integer không âm, không trùng, giảm dần; giữ lựa chọn user cũ | Code/Settings còn khóa một mốc, default `[900]`; dedupe đã có offset. Capabilities số đơn chưa biểu diễn default nhiều mốc; Cương/Hưng cập nhật đồng bộ DTO, service và Mobile theo checklist |
| Chuông khi thu âm | Âm thanh báo hết giờ ≤1 giây, bảo vệ file; không hoãn 15 giây hoặc dùng rung/UI thay điều kiện âm thanh | Chưa có bằng chứng Android. Không dời ended_at/actual/timeout DND theo lúc finalize; kiểm sớm 04/11 và nghiệm thu thiết bị |
| Undo Thought | Outbox local lưu bền chờ 5 giây; Undo cất mới hủy gửi/dọn file, xóa bản cũ giữ khả năng phục hồi | Local repository/outbox/UI và API UF4 còn phải triển khai; không coi DELETE là đã có endpoint Undo |
| Kết thúc tối thiểu | CheckIn cuối, phản hồi và lazy timeout đọc/start sau hạn; thiết bị tự khôi phục DND đúng owner | Router/service UF3–UF4/CheckIn mới là khung. “Chọn nghỉ” chưa xác nhận kết quả cần ví dụ payload được nhóm chốt, không suy completed |
| Thought → Draft | Metadata Thought có trên server, user yêu cầu sau phiên: POST voice/transcriptions purpose=thought_to_draft → POST thoughts/{id}/draft; retry riêng từng bước | STT có kiểm owner/Run và receipt DB, còn cache RAM trùng; API tạo Draft từ Thought chờ triển khai. FK unique source_thought_id đã có, chỉ kiểm migration/đường dùng và ánh xạ liên kết |
| Vai trò tuần 2 | Cương: router/DTO/HTTP; Hưng: service nghiệp vụ; Tín: schema/migration/repository; An/Thịnh/Khải: UI/local/native | Kế thừa contract chung trên dev; không tạo bộ JSON riêng theo người |

Bản nền phục vụ bắt đầu phát triển, chưa phải nghiệm thu giai đoạn 1 hay MVP: UF1 mới nhận giờ riêng nhưng UF2 vẫn chặn nguồn mới; Mobile chưa xử lý đầy đủ trường availability riêng, chưa build/analyze trên máy MT vì chưa có SDK. Không tự bỏ guard UF2, thay nguồn giờ hoặc đánh dấu các output này đã đạt.

## 13. Đồng bộ hợp đồng UF5–UF7, Dòng 64 & Continuation (SY01–SY03) — 14/11/2026

- **API Dòng 64 (`GET /api/v1/runs/{run_id}/upcoming-schedule-context`):** Triển khai tại `api/v1/focus.py` và `schemas/reschedule.py` (`UpcomingScheduleContextData`), trả `current_step`, `source_run_revision`, `source_revisions` (`ProposalSourceRevisions`), `calendar_status`/`calendar_fetched_at`, `coverage` (`CoverageWindow`), `max_extension_seconds_without_borrow`, `recommended_extension_seconds`, `limit_assumptions`, và `timeline` (`session` | `google_busy`).
- **UF6 Proposals & Continuation (`SY02`, `SY03`):**
  - Wire enum trạng thái trong `ProposalData.status` dùng chuẩn **`pending` \| `approved` \| `rejected` \| `superseded`** (khớp `SY03`).
  - `CreateProposalRequest` nhận `continuation_request` (`ContinuationRequest`) cho trường hợp làm tiếp ngay, áp dụng `GapException` (`borrow_break_seconds <= 120`, đồng bộ `SY02` `120s`/`780s` cho nhánh continuation và giữ chuẩn `>= 900s` cho mọi phiên bình thường).
  - `ApproveProposalRequest` hỗ trợ `start_continuation=true` (yêu cầu kèm `device_id`, `event_id`, `expected_source_run_revision`), trả `ApproveProposalData` gồm `schedule_version`, `sessions`, `continuation_session_id`, `continuation_run`, `calendar_sync_job_enqueued`, và tự enqueue đồng bộ Google Calendar sau commit khi đã liên kết.
- **UF7 Settings & Suggestions (`API41–44`):**
  - Gỡ nhánh `503 POLICY_NOT_CONFIGURED` trong `api/v1/settings.py`: thay đổi `work_interval_seconds`, `rest_interval_seconds`, `buffer_multiplier`, hoặc giờ Task có lịch đã chốt tạo `SettingChangeRequest` `pending` gắn `pending_proposal_id` qua `reschedule_service.create_settings_change_proposal`; giữ nguyên `settings_snapshot` cho Run đang chạy.
  - `POST /api/v1/settings/change-requests/{change_request_id}/cancel` (Dòng 42) trả `CancelSettingsChangeResult`, hủy đồng bộ proposal chờ tương ứng mà không làm mất hiệu lực cấu hình hiện hành.
  - `GET /api/v1/suggestions/current` trả `AdaptationSuggestionData | null`; `POST /api/v1/suggestions/{suggestion_id}/response` trả `SuggestionResponseResult` (không tự áp buffer khi chưa được user `accept`/`edit` và tạo pending change request/proposal nếu ảnh hưởng lịch tương lai).
- **OpenAPI / Swagger UI:** Bổ sung `summary` và `responses` (`ErrorEnvelope` cho `401`/`404`/`409`/`422`/`503`) trên các route UF5 CheckIn (`checkin.py`), UF6 Proposals (`reschedule.py`), Row 64 (`focus.py`) và UF7 Settings/Suggestions (`settings.py`).

## 14. Đồng bộ Contract Bổ sung cho W4-02 (Auth Refresh/Logout Option 1, Voice TTS/UF5 Prompt, Delete Draft & Health) — 18/11/2026

Mục này bổ sung đầy đủ contract các endpoint đã có trong code (`backend/app/api/v1/*.py`) nhưng chưa có trong `schema.md`, đồng thời chốt thiết kế target cho `BE-03` (`POST /api/v1/auth/logout` — **Phương án 1**) để đồng bộ OpenAPI, bộ fixture JSON và contract tests (`W4-02`).

> **Phân biệt trạng thái triển khai:** Các mục gắn `[Đã có trong code]` đang chạy trên router/schema hiện tại; các mục gắn `[Target BE-03 — Option 1 đã chốt]` là đặc tả mục tiêu đã thống nhất, **chưa triển khai trong router Python/ORM** cho tới khi hoàn tất migration `jti` (`tn`) và service `BE-03`.

### 14.1. Nhóm Authentication (`/api/v1/auth/*`)

#### Tổng quan DTO dùng chung (`backend/app/schemas/auth.py` — `[Đã có trong code]`)

| Schema | Trường & ràng buộc | Ghi chú |
| --- | --- | --- |
| `GoogleLoginRequest` | `id_token: string` (`min=1`, `max=16384`, `extra=forbid`, không blank/padded) | Dùng cho `POST /api/v1/auth/google`. |
| `RefreshTokenRequest` | `refresh_token: string` (`min=1`, `max=16384`, `extra=forbid`, không blank/padded) | Dùng cho `POST /api/v1/auth/refresh` (và làm cấu trúc body chuẩn cho `POST /api/v1/auth/logout` theo Option 1). |
| `TokenBundle` | `access_token: string`, `token_type: "bearer"`, `expires_in: integer` (default `3600`, range `60..86400`), `refresh_token: string \| null` (default `null`), `refresh_expires_in: integer \| null` (default `null`, mặc định cấp `2592000` = 30 ngày, range `300..7776000`) | Khi `issue_app_access_token()` cấp token mới, cả `refresh_token` (JWT HS256 có `token_type="refresh"` và `jti: UUIDv4`) và `refresh_expires_in` đều được gán giá trị. |
| `GoogleLoginData` | Kế thừa toàn bộ `TokenBundle` + `user: CurrentUser` | `CurrentUser`: `user_id: string`, `email: string \| null`, `display_name: string \| null`, `is_active: bool` (default `true`). |
| `LogoutData` *(Target BE-03)* | `logged_out: boolean` (`true`), `revoked: boolean` | `revoked=true` ở lần thu hồi đầu tiên; `revoked=false` khi `jti` đã revoked trước đó (idempotent `200`). |

#### a) `POST /api/v1/auth/google` `[Đã có trong code]`

- **Auth:** Public (không yêu cầu `AppBearer`, không cần `Idempotency-Key`).
- **Request JSON:** `{"id_token": "<google_id_token>"}` (`GoogleLoginRequest`).
- **Response `200 OK`:** `SuccessEnvelope[GoogleLoginData]`.
- **Errors (`Auth`):** `401 INVALID_GOOGLE_ID_TOKEN` / `UNAUTHENTICATED` (`next_action="authenticate"`, `retryable=false`), `403 ACCOUNT_DISABLED` (`next_action="contact_support"`, `retryable=false`), `409 ACCOUNT_LINK_CONFLICT` (`next_action="review_account_link"`, `retryable=false`), `422 VALIDATION_ERROR` (`fix_fields`) / `GOOGLE_PROFILE_INCOMPLETE` (`review_google_profile`), `503 AUTH_PROVIDER_UNAVAILABLE` (`next_action="retry_login"`, `retryable=true`).

#### b) `POST /api/v1/auth/refresh` `[Đã có trong code — mở rộng stateful rotation ở BE-03]`

- **Auth:** Public (xác thực trực tiếp bằng `refresh_token` HS256 trong body, không yêu cầu `access_token` còn hạn).
- **Request JSON (`RefreshTokenRequest`):**
  ```json
  {
    "refresh_token": "<app_refresh_jwt>"
  }
  ```
- **Response `200 OK` (`SuccessEnvelope[TokenBundle]`):**
  ```json
  {
    "data": {
      "access_token": "<new_app_access_jwt>",
      "token_type": "bearer",
      "expires_in": 3600,
      "refresh_token": "<new_app_refresh_jwt>",
      "refresh_expires_in": 2592000
    },
    "meta": {
      "request_id": "req_01J...",
      "server_time": "2026-11-18T10:00:00Z"
    }
  }
  ```
- **Hành vi hiện tại trong code (`auth_service.refresh_app_tokens`):** Giải mã & kiểm tra `token_type="refresh"`, bắt buộc có `sub` (UUID) và `jti` (string non-blank), kiểm tra `User.is_active` qua `load_current_user`, sau đó cấp `TokenBundle` mới. Chưa lưu/check bảng `jti` trong DB.
- **Quy tắc Target `BE-03` (Refresh Token Rotation & Reuse Detection):**
  - Mỗi lần `/auth/refresh` thành công phải đánh dấu `jti` cũ đã rotated/revoked và lưu `jti` mới gắn cùng `user_id` (+ `family_id` / `replaced_by_jti`).
  - Nếu client gửi lại `refresh_token` có `jti` đã bị thu hồi hoặc đã xoay (`revoked`/`rotated` reuse), server **bắt buộc thu hồi toàn bộ chuỗi token còn hiệu lực của phiên/family đó** và trả **`401 UNAUTHENTICATED`** (`next_action="authenticate"`, `retryable=false`, header `WWW-Authenticate: Bearer`).
- **Errors:** `401 UNAUTHENTICATED` (`authenticate`, `retryable=false`, có header `WWW-Authenticate: Bearer`), `403 ACCOUNT_DISABLED` (`contact_support`, `retryable=false`), `422 VALIDATION_ERROR` (`fix_fields`, `retryable=false`), `503 AUTH_PROVIDER_UNAVAILABLE` (`retry_login`, `retryable=true`).

#### c) `POST /api/v1/auth/logout` `[Target BE-03 — Phương án 1 đã chốt, chưa code]`

- **Quyết định thiết kế (Option 1):** Endpoint Public xác thực **bằng chính `refresh_token` trong JSON body** (giống `/api/v1/auth/refresh`), **KHÔNG** yêu cầu header `Authorization: Bearer <access_token>` còn hạn, giúp Mobile đăng xuất sạch cả khi `access_token` vừa hết hạn hoặc đang offline-recover. Không yêu cầu header `Idempotency-Key`.
- **Request JSON (`LogoutRequest` / `RefreshTokenRequest`, `extra="forbid"`):**
  ```json
  {
    "refresh_token": "<app_refresh_jwt>"
  }
  ```
- **Response `200 OK` (`SuccessEnvelope[LogoutData]`):**
  - Lần đầu thu hồi `jti` đang còn hiệu lực:
    ```json
    {
      "data": {
        "logged_out": true,
        "revoked": true
      },
      "meta": {
        "request_id": "req_01J...",
        "server_time": "2026-11-18T10:01:00Z"
      }
    }
    ```
  - Lần gọi lại với cùng `refresh_token` (tình huống retry mạng / `jti` đã thu hồi trước đó): vẫn trả **`200 OK`** idempotent với `{"logged_out": true, "revoked": false}`.
  - Sau khi logout, dùng lại `refresh_token` đó ở `/auth/refresh` phải bị từ chối `401 UNAUTHENTICATED`.
- **Errors:**
  - `401 UNAUTHENTICATED` (`next_action="authenticate"`, `retryable=false`, header `WWW-Authenticate: Bearer`): sai chữ ký/hết hạn JWT refresh, thiếu/rỗng `jti`, sai `token_type != "refresh"`, hoặc `user` không tồn tại.
  - `403 ACCOUNT_DISABLED` (`next_action="contact_support"`, `retryable=false`): user sở hữu token đã bị vô hiệu hóa (`is_active=false`).
  - `422 VALIDATION_ERROR` (`next_action="fix_fields"`, `retryable=false`): thiếu `refresh_token`, blank/padded, vượt `16384` ký tự hoặc có field thừa (`extra="forbid"`).
  - `503 AUTH_PROVIDER_UNAVAILABLE` (`next_action="retry_login"`, `retryable=true`): thiếu cấu hình JWT signing (`validate_auth_configuration` / `validate_app_signing_configuration` lỗi).

#### d) `GET /api/v1/auth/me` `[Đã có trong code]`

- **Auth:** Bắt buộc `AppBearer` (trong dev/testing khi `AUTH_MODE=dev_user` và thiếu header `Authorization` sẽ fallback sang `SEED_USER_A`).
- **Response `200 OK` (`SuccessEnvelope[CurrentUser]`):** `{"user_id": "...", "email": "...", "display_name": "...", "is_active": true}`.
- **Errors:** `401 UNAUTHENTICATED` (header `WWW-Authenticate: Bearer`), `403 ACCOUNT_DISABLED`, `503 AUTH_PROVIDER_UNAVAILABLE`.

---

### 14.2. Nhóm Giọng nói STT Error Map & TTS (`/api/v1/voice/*`) `[Đã có trong code]`

#### a) Bổ sung bảng lỗi `POST /api/v1/voice/transcriptions` (STT)

Endpoint Dòng 8 trong Sheet (`multipart/form-data`: `purpose` ∈ `{"uf1_input", "thought_to_draft", "uf5_checkin"}`, `device_id`, `file`, `thought_id` bắt buộc khi `purpose="thought_to_draft"`; header `Idempotency-Key`) trả về `200 SuccessEnvelope[TranscriptionData]` (`text`, `input_source: "voice" | "thought"`, `thought_id: string | null`). Các mã lỗi ánh xạ từ `_STT_ERROR_MAP` (`backend/app/api/v1/voice.py`):

| HTTP | `error.code` | `retryable` | `error.next_action` | Điều kiện |
| --- | --- | --- | --- | --- |
| `422` | `VALIDATION_ERROR` | `false` | `fix_fields` | Thiếu `thought_id` khi `purpose="thought_to_draft"`, hoặc file âm thanh rỗng (`field_path="thought_id"` / `"file"`). |
| `422` | `NO_SPEECH_DETECTED` | `false` | `record_again_or_type` | Audio không có tiếng nói / provider trả chuỗi rỗng. |
| `422` | `UNSUPPORTED_AUDIO` | `false` | `record_again_or_type` | Định dạng audio không hỗ trợ hoặc file hỏng. |
| `413` | `AUDIO_TOO_LARGE` | `false` | `record_again_or_type` | Kích thước file vượt `VOICE_MAX_AUDIO_BYTES` (mặc định `10 MiB`). |
| `404` | `RESOURCE_NOT_FOUND` | `false` | `reload` | Không tìm thấy `thought_id` hoặc phiên nguồn `source_run_id`. |
| `409` | `SESSION_STILL_RUNNING` | `false` | `wait_for_session_end` | Chặn chuyển chữ UF4→UF1 khi Run nguồn (`ended_at=null`) chưa kết thúc. |
| `409` | `IDEMPOTENCY_KEY_REUSED` | `false` | `use_original_payload_or_new_command` | Trùng `Idempotency-Key` với nội dung audio/metadata khác. |
| `503` | `STT_UNAVAILABLE` | `false` | `use_on_device_stt_or_type` | Provider STT chưa cấu hình key hoặc bị từ chối quyền. |
| `503` | `STT_RATE_LIMITED` | `true` | `use_on_device_stt_or_type` | Provider STT bị HTTP 429. |
| `503` | `STT_PROVIDER_ERROR` | `true` | `use_on_device_stt_or_type` | Lỗi mạng / 5xx từ Groq Whisper. |
| `504` | `STT_TIMEOUT` | `true` | `use_on_device_stt_or_type` | Quá thời gian chờ STT. |

#### b) `POST /api/v1/voice/tts` `[Đã có trong code]`

- **Mục đích:** Tổng hợp giọng nói tiếng Việt qua Edge-TTS (server-side) trả về Base64 audio. Khi lỗi, trả mã lỗi chuẩn hướng dẫn Mobile chuyển về `flutter_tts` trên thiết bị (`use_on_device_tts`).
- **Auth:** Bắt buộc `AppBearer`. Không dùng `Idempotency-Key`.
- **Request JSON (`TtsSynthesizeRequest`, `extra="forbid"`):**
  ```json
  {
    "text": "Đã đến giờ bắt đầu phiên tập trung 25 phút rồi nhé.",
    "voice": "vi-VN-HoaiMyNeural"
  }
  ```
  - `text`: `string`, bắt buộc (`min_length=1`, `max_length=2000`; trong service cũng kiểm giới hạn `VOICE_TTS_MAX_CHARS`, mặc định `600`, vượt ngưỡng sẽ raise `TtsErrorCode.TEXT_TOO_LONG`).
  - `voice`: `string | null` (`min_length=1`, `max_length=80`), mặc định `null` (server dùng `VOICE_TTS_DEFAULT_VOICE = "vi-VN-HoaiMyNeural"`).
- **Response `200 OK` (`SuccessEnvelope[TtsSynthesisData]`):**
  ```json
  {
    "data": {
      "audio_base64": "SUQzBAAAAAA...",
      "mime_type": "audio/mpeg",
      "provider": "edge_tts",
      "voice": "vi-VN-HoaiMyNeural",
      "cached": false,
      "duration_ms": 420
    },
    "meta": {
      "request_id": "req_01J...",
      "server_time": "2026-11-18T10:02:00Z"
    }
  }
  ```
- **Bảng lỗi `_TTS_ERROR_MAP` (tất cả lỗi TTS đều có `next_action = "use_on_device_tts"`):**

| HTTP | `error.code` | `retryable` | `error.next_action` | Nguyên nhân |
| --- | --- | --- | --- | --- |
| `401` | `UNAUTHENTICATED` | `false` | `authenticate` | Thiếu hoặc sai `AppBearer` JWT. |
| `422` | `VALIDATION_ERROR` | `false` | `fix_fields` | Sai schema Pydantic (ví dụ `text` rỗng, >2000 ký tự, hoặc field lạ `extra="forbid"`). |
| `422` | `TTS_INVALID_INPUT` | `false` | `use_on_device_tts` | `text` toàn khoảng trắng sau `strip()`. |
| `422` | `TTS_TEXT_TOO_LONG` | `false` | `use_on_device_tts` | `text` vượt giới hạn `VOICE_TTS_MAX_CHARS` (mặc định `600`). |
| `503` | `TTS_EMPTY_AUDIO` | `false` | `use_on_device_tts` | Provider trả buffer âm thanh rỗng. |
| `503` | `TTS_UNAVAILABLE` | `true` | `use_on_device_tts` | Edge-TTS không khả dụng / thiếu dependency/môi trường. |
| `503` | `TTS_PROVIDER_ERROR` | `true` | `use_on_device_tts` | Lỗi kết nối / runtime từ Edge-TTS. |
| `504` | `TTS_TIMEOUT` | `true` | `use_on_device_tts` | Hết thời gian chờ Edge-TTS (`VOICE_TTS_TIMEOUT_SECONDS = 10.0s`). |

#### c) `POST /api/v1/voice/uf5-checkin-prompt` `[Đã có trong code]`

- **Mục đích:** Sinh câu hỏi Voice Check-in 2 chiều không phán xét cho UF5 (tách biệt xác nhận kết quả Phiên vs kết quả Bước), trả kèm **3 chip Bỏ qua + Undo 5 giây** (W3C COGA) và audio TTS tùy chọn với cơ chế **soft-fallback** (không ném `503`/`504` ra HTTP nếu TTS lỗi).
- **Auth:** Bắt buộc `AppBearer`. Không dùng `Idempotency-Key`.
- **Request JSON (`UF5VoiceCheckInPromptRequest`, `extra="forbid"`):**
  ```json
  {
    "kind": "final",
    "is_final_session_of_step": true,
    "expected_output": "Hoàn thành dàn ý chương 1",
    "session_outcome": "completed",
    "include_audio": true,
    "voice": "vi-VN-HoaiMyNeural"
  }
  ```
  - Defaults: `kind="final"` (`"mid" | "final"`), `is_final_session_of_step=false`, `expected_output=null` (`max=500`), `session_outcome=null` (`max=64`), `include_audio=true`, `voice=null` (`1..80`).
- **Response `200 OK` (Thành công cả TTS):** `SuccessEnvelope[UF5VoiceCheckInPromptResponse]`:
  - `prompt: UF5CheckInPromptData`:
    - `kind`: `"mid" | "final"`
    - `prompt_text`: `string` (`asks_step_completion=true` khi `kind=="final"` & `is_final_session_of_step==true` & `session_outcome` ∈ `{null, "completed", "partial"}`)
    - `asks_step_completion`: `boolean`
    - `skip_chips`: luôn đúng 3 phần tử `UF5SkipChipSchema` (`skip_scope`: `"postpone_session"` | `"skip_session"` | `"skip_step"`, `label`, `description`)
    - `undo_notice`: `string`
    - `undo_timeout_seconds`: `integer` (mặc định `5`, `>= 1`)
  - `audio_base64: string | null`
  - `audio_mime_type: string | null`
  - `tts_provider: string | null`
  - `fallback_to_device_tts: boolean` (`false` khi sinh được audio hoặc khi client đặt `include_audio=false`)
- **Response `200 OK` (Soft-fallback khi Edge-TTS lỗi / `TtsError`):** Vẫn trả HTTP **`200 OK`** với `prompt` đầy đủ, đồng thời:
  ```json
  {
    "data": {
      "prompt": {
        "kind": "final",
        "prompt_text": "Bạn đã xong phiên cuối của bước này. Sau khi xác nhận kết quả phiên, bạn kiểm tra riêng giúp mình xem đầu ra của bước ('Hoàn thành dàn ý chương 1') đã hoàn thành chưa nhé?",
        "asks_step_completion": true,
        "skip_chips": [
          {
            "skip_scope": "postpone_session",
            "label": "Hoãn phiên này làm sau",
            "description": "Giữ nguyên bước hiện tại và chuyển phiên này sang khung giờ rảnh khác qua UF6."
          },
          {
            "skip_scope": "skip_session",
            "label": "Bỏ qua phiên này",
            "description": "Bỏ qua riêng phiên hiện tại mà không tự động bỏ các phiên hay bước khác."
          },
          {
            "skip_scope": "skip_step",
            "label": "Bỏ toàn bộ bước này",
            "description": "Bỏ phần còn lại của bước hiện tại và chuyển qua UF6 để cập nhật lịch tương lai."
          }
        ],
        "undo_notice": "Đã ghi nhận thao tác bỏ qua. Bạn có thể bấm Hoàn tác trong 5 giây nếu muốn đổi ý.",
        "undo_timeout_seconds": 5
      },
      "audio_base64": null,
      "audio_mime_type": null,
      "tts_provider": null,
      "fallback_to_device_tts": true
    },
    "meta": {
      "request_id": "req_01J...",
      "server_time": "2026-11-18T10:03:00Z"
    }
  }
  ```
- **Errors:** Chỉ trả `401 UNAUTHENTICATED` hoặc `422 VALIDATION_ERROR` (sai `kind`, vượt độ dài `expected_output`/`session_outcome`/`voice`, hoặc field lạ).

---

### 14.3. Xóa Bản nháp UF1 & Health Check `[Đã có trong code]`

#### a) `DELETE /api/v1/drafts/{draft_id}` `[Đã có trong code]`

- **Auth:** Bắt buộc `AppBearer`. Path param `draft_id: string` (`min_length=1`). Không yêu cầu `expected_summary_revision` hay `Idempotency-Key`.
- **Hành vi:** Chỉ xóa vĩnh viễn bản nháp UF1 thuộc owner chưa chốt thành công việc chính thức (xóa kèm `PlanningContext` và `PlanCandidate` liên kết với Draft đó).
- **Response `200 OK` (`SuccessEnvelope`):**
  ```json
  {
    "data": {
      "draft_id": "draft_1",
      "deleted": true
    },
    "meta": {
      "request_id": "req_01J...",
      "server_time": "2026-11-18T10:04:00Z"
    }
  }
  ```
- **Errors:**
  - `401 UNAUTHENTICATED` (`authenticate`)
  - `404 RESOURCE_NOT_FOUND` (`reload`): không tìm thấy `draft_id` thuộc user hiện tại.
  - `409 INVALID_STATE_TRANSITION` (`reload_and_review`): bản nháp đã ở `stage="confirmed"`, có `confirmed_task_id`, hoặc đã tồn tại `Task` tham chiếu `source_draft_id`.
  - `422 VALIDATION_ERROR` (`fix_fields`)

#### b) `GET /api/v1/health` (và alias `GET /health` ẩn khỏi OpenAPI) `[Đã có trong code]`

- **Auth:** Public (không yêu cầu `AppBearer`).
- **Response `200 OK`:** Trả object JSON phẳng (không bọc `SuccessEnvelope`):
  ```json
  {
    "status": "ok",
    "database": "ok"
  }
  ```
- **Errors:** `503 DATABASE_UNAVAILABLE` (`retryable=true`, `next_action="retry_later"`, trả theo chuẩn `ErrorEnvelope`) khi truy vấn kiểm tra `SELECT 1` xuống DB thất bại.
