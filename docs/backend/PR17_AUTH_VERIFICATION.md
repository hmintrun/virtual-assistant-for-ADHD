# PR17 — Auth review fixes và bằng chứng kiểm chứng

Ngày11/11/2026. Source nền: backend@76c5632de3d5282348dde3a017c57fca3148245f; kết quả sau sửa áp dụng cho patch PR17 có AppBearer và tests trong repository. Mobile WIP không thuộc phạm vi patch Backend này.

## Hai yêu cầu review đã xử lý

1. CurrentUser dùng HTTPBearer AppBearer để OpenAPI khai báo đúng security cho API cần đăng nhập, không khai báo Authorization thành header parameter thủ công. Parser/seed guard giữ401 ErrorEnvelope và WWW-Authenticate. Header vắng mới được fallback theo devguard; header sai luôn bị từ chối.
2. AUTH_GUIDE phân biệt Backend delivered và Mobile pending; hướng dẫn kiểm chứng trong repo thay phụ thuộc _temp. Không thêm endpoint hoặc đổi TTL/JWT policy.

## Kết quả kiểm thử sau sửa

| Kiểm chứng | Kết quả |
|---|---|
| Test OpenAPI trên source trước sửa | 2FAIL đúng lỗi thiếu scheme/security;1PASS cho public routes |
| Auth + OpenAPI sau sửa | 82PASS trong11.76s, gồm73Auth cũ và9case mới |
| Toàn Backend sau sửa | 402PASS,6SKIP,1XFAIL,3warnings,4subtestsPASS trong169.50s |
| Ruff/ty ba file Python đổi | PASS |
| Google/DB app trong regression | Không sử dụng; fixtures DB tạm, provider giả, socket guard |

Skip/xfail là gaps hiện hữu, khôngPASS; gồm policy/task limits/avoidance/continuation và API xóa Draft chưa có. Warning gồm Starlette/httpx và Pydantic serializer nền. Các test mới kiểm toàn bộ operation có CurrentUser dependency, không chỉ2route mẫu, và6biến thể header sai ở dev mode. Test callback hiện hữu vẫn kiểm state-only của cả alias.

## Tái lập kiểm thử trên Windows PowerShell

Từ folder backend, dùng Python3.12 và lockfile đã commit:

```powershell
uv sync --frozen
$env:ENVIRONMENT = 'testing'
$env:TESTING = 'true'
$env:ENABLE_CALENDAR_SYNC_WORKER = 'false'
$env:DATABASE_URL = 'sqlite+aiosqlite:///:memory:'
$env:CALENDAR_PROVIDER_MODE = 'fake'
$env:GEMINI_API_KEY = ''
$env:GROQ_API_KEY = ''
uv run --frozen pytest tests/test_auth_openapi.py tests/test_auth_tokens.py tests/test_auth_google_verifier.py tests/test_auth_api.py -q
uv run --frozen pytest -q -ra
uv run --frozen ruff check app/api/deps.py tests/test_auth_openapi.py tests/test_auth_api.py
uv run --frozen ty check app/api/deps.py tests/test_auth_openapi.py tests/test_auth_api.py --output-format concise
```

Focused cuối chạy uv run --frozen --offline; full regression dùng interpreter backend/.venv/Scripts/python.exe và pytest trong môi trường hiện hữu; không cài mới dependency. FastAPI hiện là0.162.0; test route dùng effective_route_contexts cho included routers của phiên bản này. Các command uv ở trên là cách tái lập bằng cùng lockfile. Không trỏ test về DB app, không chạy migration bằng lệnh test này.

Swagger: lấy app access_token từ POST /api/v1/auth/google, mở /docs và chọn Authorize/AppBearer, dán riêng token. API app không nhận Google ID token hoặc Calendar access_token. Schema vẫn mô tả Bearer khi dev_user được bật; fallback dev là ngoại lệ có guard, không phải chính sách public của staging/production.

## PostgreSQL và Calendar thật: cập nhật đánh giá

| Môi trường | DB | Calendar | App auth | Worker |
|---|---|---|---|---|
| .env mặc định tại lần kiểm11/11 | SQLite | fake | dev_user/development | cấu hình bật |
| Process QA loopback riêng | PostgreSQL17.11/proactive_qa | real, chỉ lịch ProActive QA | dev_user/testing | nền tắt, QA trigger có chủ đích |
| Regression patch này | SQLite tạm | fake | fixtures Google/dev theo case | tắt |

Đã kiểm trực tiếp PostgreSQL bằng transaction READ ONLY: current_database=proactive_qa, version17.11, alembic_version=20261108_01. Báo cáo QA độc lập trước đó có79PASS trong scope PostgreSQL (validation/schema và concurrent login); chưa chạy toàn Backend trênPG. Không thay DATABASE_URL/.env/runtime hoặc migrate DB app trong patch này.

Calendar live: OAuth/list/create/update/delete trên secondary ProActive QA đãPASS trong lần QA trước và eventtest đã xóa. Follow-up GET calendar list vẫn200, thấy QA selected/read/write; GET events200 data=[], GET event đã xóa404; SELECT READ ONLY và GET job xác nhận3jobcompleted. Xem [payload, response và toàn luồng thực tế](GOOGLE_CALENDAR_LIVE_DATA_FLOW_20261111.md).

Đây là kết nối Calendar thật, không phải nghiệm thu Google Sign-In app thật: QA dùng dev_user. Mobile tại commit nền còn demo và đang chờ tích hợp riêng. Hai app users/GoogleAndroid-Web thật, fullPGsuite, Calendarrefresh/revoke/412/recurring/all-day, deviceDoze và measurement là các gates chưa đóng. Không công bố598casePASS từ các scope này.

## Tài liệu chung

- [Auth contract](AUTH_GUIDE.md), [schema](schema.md).
- [Google Backend Docs](https://docs.google.com/document/d/1X1zIWny1nOWCfkS4zLEdn71uZLhYLu5OkHlUPKcdoxE/edit): Auth notes ở tabs01/03/04/05 đã cập nhật scope và kết quả.
- [Sheet API](https://docs.google.com/spreadsheets/d/1RTI0Hv0DydDX5S0DMq1clSFmGggCuGkAMORynke6J0w/edit#gid=1522506644&range=A65:J66): cập nhật notes A65:J66; không đổi endpoint/payload/format/table range.

Reviewer không cần file _temp hoặc credential để xem contract/testsource. Không đưa token/secret, credential JSON, DB dump hoặc key vào patch.

## SHA256 source kiểm chứng

Các hash dưới đây là byte source tại thời điểm chạy kiểm chứng, trước khi Git chuẩn hóa line endings. Commit PR sẽ định danh toàn bộ patch.

| File | SHA256 |
|---|---|
| backend/app/api/deps.py | e3273d8ef56f31b98428404c09d1e6c9acb3ed9e4a44de15a586535e2fa9c54a |
| backend/tests/test_auth_openapi.py | 935f29e547f3ebd8fbc84028fe5f05e0c6652327263280b8301b2c221bc26d84 |
| backend/tests/test_auth_api.py | e62b86548a33e70a579da0f2d55ac6c488b16d1f83f427731e977be9cb4240de |
