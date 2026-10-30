# Cấu trúc Backend ProActive

Cấu trúc mục tiêu ngày 30/10/2026, dựa trên khung repo hiện có và hợp đồng API đã rà soát. Tài liệu dùng để chia việc và xác định nơi đặt code; cây thư mục không có nghĩa các chức năng đã được triển khai.

Cách cài môi trường và chạy kiểm thử **sau khi code xong** xem [Hướng dẫn chạy môi trường kiểm thử](backend_testing_setup.md).

## 1. Hiện trạng và hướng tổ chức

`app/` hiện có 31 file Python: các module chủ yếu mới chứa comment/docstring; chưa có `FastAPI app`, router, model SQLAlchemy hay hàm nghiệp vụ để chạy. Môi trường Python 3.12.15 đã được thiết lập bằng `pyproject.toml` và `uv.lock`; `scripts/check_environment.py` đã kiểm FastAPI/Pydantic và SQLite async. Các kiểm tra này xác nhận môi trường, không chứng minh chức năng nghiệp vụ đã hoàn thành.

Giữ monorepo `backend/`, `mobile/`, `docs/`. Backend là một ứng dụng chia module, một database và một worker xử lý công việc nền khi cần; chưa cần tách microservice hoặc đưa Redis/Celery vào khung ban đầu. Điểm bàn giao với Mobile tiếp tục theo API hiện hành.

Luồng xử lý chung:

```text
Mobile → API router → service điều phối → SQLAlchemy/database
                           │
                           ├─ AI/Voice: hiểu và sinh nội dung
                           ├─ Algorithm: kiểm dữ liệu, tính và xếp lịch
                           └─ DB outbox → worker → Google Calendar
```

Router không tự tính lại lịch; Algorithm không tự commit DB; AI không xác nhận thay user. Thao tác Google thực hiện sau transaction lưu kế hoạch và outbox thành công. Khi gọi AI/Google hoặc chạy tìm kiếm lịch lâu, không giữ một transaction DB mở suốt thời gian chờ; tới lúc ghi phải kiểm lại revision/ràng buộc.

## 2. Cấu trúc Backend mục tiêu

```text
backend/
├── pyproject.toml               Thư viện, công cụ dev và cấu hình Python
├── uv.lock                      Phiên bản dependency dùng chung
├── requirements.txt             Sinh từ lockfile, không sửa độc lập
├── .python-version              Python 3.12.15
├── .env.example                 Tên biến và giá trị mẫu
├── README.md                    Cài đặt, chạy và kiểm tra
├── alembic.ini
├── migrations/                  Lịch sử thay đổi database
├── app/
│   ├── main.py                  Tạo app, lifecycle, middleware, gắn router
│   ├── core/
│   │   ├── config.py            Đọc và kiểm biến môi trường
│   │   ├── database.py          Engine, AsyncSession và transaction
│   │   ├── security.py          Xác thực và current user
│   │   └── errors.py            Ánh xạ lỗi thành envelope API
│   ├── api/
│   │   ├── deps.py              Dependency DB, user và request context
│   │   └── v1/
│   │       ├── router.py        Gộp router với prefix /api/v1
│   │       ├── drafts.py        Draft và xác nhận tóm tắt UF1
│   │       ├── voice.py         Chuyển âm do user chủ động gửi
│   │       ├── planning.py      Candidate UF2 và xác nhận kế hoạch đầu
│   │       ├── tasks.py         Đọc Task chính thức
│   │       ├── schedule.py      Đọc lịch đã xác nhận
│   │       ├── runs.py          Start, events, end và phục hồi Run
│   │       ├── devices.py       Quyền/trạng thái thiết bị
│   │       ├── reminders.py     Lời nhắc và receipt
│   │       ├── thoughts.py      Hộp suy nghĩ và liên kết Draft
│   │       ├── checkins.py      Kết quả và actual corrections
│   │       ├── proposals.py     Phương án UF6 và approve/reject
│   │       ├── settings.py      Preferences, Availability và Suggestions
│   │       ├── calendar.py      Kết nối, preview, CRUD và trạng thái job
│   │       └── system.py        Capabilities, PlanningContext, CommandReceipt
│   ├── schemas/                Pydantic v2: request/response và DTO bàn giao
│   │   ├── common.py           ID, time interval, lỗi, meta và pagination
│   │   ├── draft.py
│   │   ├── planning.py
│   │   ├── task.py
│   │   ├── run.py
│   │   ├── checkin.py
│   │   ├── thought.py
│   │   ├── settings.py
│   │   └── calendar.py
│   ├── models/                 SQLAlchemy 2.0: cấu trúc lưu trữ
│   │   ├── base.py
│   │   ├── user.py
│   │   ├── planning.py         Draft, Candidate, phần việc và snapshot cần lưu
│   │   ├── task.py             Task; giữ file từ khung cũ
│   │   ├── planning_part.py    Phần đã chia/chưa chia của Task
│   │   ├── step.py             Giữ file Step từ khung cũ
│   │   ├── session.py          Giữ file Session từ khung cũ
│   │   ├── execution.py        Run, CheckIn và actual history
│   │   ├── thought.py
│   │   ├── proposal.py
│   │   ├── settings.py          Preferences, Availability, yêu cầu/gợi ý cấu hình
│   │   ├── device.py            Trạng thái thiết bị và Reminder
│   │   ├── command.py           CommandReceipt và dữ liệu chống ghi lặp
│   │   ├── audit.py             Lịch sử thao tác/hiệu chỉnh cần truy vết
│   │   └── calendar.py         Connection, Context, Link, Preview và SyncJob
│   ├── services/
│   │   ├── draft_service.py    Lưu nháp, merge và revision nguồn
│   │   ├── plan_service.py     Điều phối tạo/sửa/confirm Candidate
│   │   ├── run_service.py      Start/event/end, chống hai Run đồng thời
│   │   ├── checkin_service.py  Timeout, phản hồi muộn và actual
│   │   ├── thought_service.py  Metadata Thought, liên kết Draft và xóa
│   │   ├── device_service.py   Báo trạng thái thiết bị và receipt lời nhắc
│   │   ├── proposal_service.py Vòng đời Proposal, approve và thay thế
│   │   ├── settings_service.py Requested/effective và tác động lịch
│   │   ├── calendar_service.py Preview, cấu hình, mapping và queue job
│   │   ├── command_service.py  Idempotency và tra receipt
│   │   ├── ai_service.py       Điểm gọi phần AI, giữ tên từ khung cũ
│   │   ├── voice_service.py    Điểm gọi STT/TTS phía server khi được chọn
│   │   ├── scheduler_engine.py Điểm gọi Algorithm, giữ tên từ khung cũ
│   │   └── planning/           Tách lõi Algorithm khi cần
│   │       ├── validation.py   Kiểm đầu vào và điều kiện bắt đầu
│   │       ├── availability.py Tính khoảng thực sự được phép xếp
│   │       ├── durations.py    Buffer một lần, chia work/rest
│   │       ├── scheduling.py   Tìm lịch, gap và kiểm khả thi
│   │       ├── rescheduling.py Tính thay đổi trong allowed_task_ids
│   │       └── adaptation.py   Mẫu actual/estimate và gợi ý cấu hình
│   ├── integrations/
│   │   └── google_calendar.py  HTTP/OAuth adapter; không tự duyệt lịch
│   └── jobs/
│       ├── worker.py           Điểm chạy worker riêng
│       ├── calendar_sync.py    Claim/retry/đối soát SyncJob
│       └── checkin_timeout.py  Chuyển open → no_respond ở Backend
├── scripts/
│   └── check_environment.py      Chuẩn bị .env và kiểm môi trường
└── tests/
    ├── unit/                   Hàm tính và quy tắc trạng thái
    ├── api/                    Request, response, lỗi và quyền
    ├── integration/            DB transaction, revision và outbox
    └── fixtures/               Dữ liệu mẫu và fake adapter
```

Đây là danh sách vị trí dự kiến, không yêu cầu tạo tất cả file ngay. Tạo theo luồng đang làm; chưa có trách nhiệm cụ thể thì chưa thêm lớp. O01–O26 là đối tượng dữ liệu, không bắt buộc có 26 bảng. Database quyết định cách lưu, còn schema API giữ theo Sheet. Chưa cần một lớp repository chung cho mọi bảng; chỉ tách truy vấn thành module riêng khi có nhu cầu tái sử dụng hoặc độ phức tạp rõ.

Các package cần `__init__.py` phù hợp. Các file schema không import ORM hoặc router. `scheduler_engine.py` nhận/trả DTO đã thống nhất; không import FastAPI, gọi provider hoặc ghi DB. `ai_service.py` và `voice_service.py` không chứa transaction lưu Task/lịch.

## 3. Thứ tự triển khai và điều kiện bàn giao

1. **P01 chốt điểm giao:** đồng bộ schema API, revision và nguồn xác thực; phân người sở hữu file. Hợp đồng được review không có nghĩa endpoint đã có code.
2. **P02/P03 dựng nền:** model/migration và app/settings/DB/router chạy được trên môi trường cài lại từ lockfile. Database và Backend là hai đầu việc có giao diện rõ.
3. **Làm một luồng UF1–UF2:** Draft → confirm summary → bước 0 → Candidate → confirm. AI và Algorithm dùng DTO mẫu để làm song song; Backend ghép và kiểm revision tại commit.
4. **Có UF6 tối thiểu trước luồng phụ thuộc:** I03 lập phần tiếp và Calendar có tác động lịch cần create/view/approve Proposal; không đợi toàn bộ tuần 6 mới chốt interface của UF6.
5. **Ghép UF3–UF5 và UF4:** start hợp lệ mới tạo Run; timer/actual dừng đúng ended_at; not_started không có Run giả; check-in và DND theo mốc đã thống nhất.
6. **Ghép Calendar/cá nhân hóa:** outbox sau commit; job retry/etag và preview; requested/effective; dùng fake adapter trước, kiểm Google thật riêng.
7. **Kiểm tích hợp:** chạy K01–K20/D01–D03 trong 05 và bổ sung các ca revision mới, cấu hình/JSON biến thể. Có log/bằng chứng và commit cụ thể mới ghi Đạt.

Điểm mới trong API: Draft có `planning_input_revision`; Candidate giữ `source_planning_input_revision`. Chỉ sửa ý tưởng chia bước làm Candidate cũ stale, vẫn giữ xác nhận tóm tắt UF1. Khi confirm Candidate phải kiểm cả revision phương án, tóm tắt, đầu vào lập kế hoạch và các nguồn lịch.

## 4. Quy tắc triển khai để tránh chồng trách nhiệm

- `main.py` chỉ khởi tạo app/lifecycle và gắn router; không chứa nghiệp vụ UF.
- Router nhận request, lấy user/DB dependency, gọi service và trả schema theo hợp đồng. Không tự viết lại phép kiểm lịch.
- `schemas/` định nghĩa dữ liệu trao đổi; `models/` định nghĩa cách lưu. Schema không import router hoặc ORM; không trả nguyên model DB làm hợp đồng mặc định.
- Service điều phối kiểm quyền, revision, transaction và trạng thái. Lõi Algorithm chịu trách nhiệm phép tính; adapter AI/Voice chịu trách nhiệm nội dung/ngôn ngữ.
- `integrations/google_calendar.py` giao tiếp với Google; quyết định áp lịch và lưu job nằm ở service/transaction nội bộ.
- `jobs/` xử lý công việc đã được lưu; worker cũ không được ghi đè một schedule revision mới hoặc tạo lại Session đã hủy.
- Mỗi task có một người sở hữu phần code cụ thể. Thay schema dùng chung, migration hoặc điểm import phải phối hợp trước; không tạo hai module làm cùng trách nhiệm.
- Nguồn xác thực phải được chốt: API đang giả định có user đã đăng nhập. Calendar OAuth cấp quyền lịch, không thay phần đăng nhập app.
- Giữ phần chưa chia/chưa xếp trong `PlanningPart`; tách Session dự kiến với Run thực tế. Chưa confirm không tạo lịch vận hành, chưa approve Proposal không đổi lịch đã xác nhận.

Tên module có thể được điều chỉnh khi triển khai nhưng ranh giới trách nhiệm và hợp đồng API phải giữ thống nhất. Chỉ tạo file khi có nhiệm vụ cần thực hiện; không dựng toàn bộ cây thành các file rỗng rồi đánh dấu hoàn thành.

## Tài liệu kỹ thuật

- [FastAPI: Bigger Applications / APIRouter](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [SQLAlchemy 2.0: AsyncIO](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
