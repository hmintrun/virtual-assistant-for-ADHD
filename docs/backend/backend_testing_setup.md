# Cài và kiểm tra môi trường Backend

Hướng dẫn cho thành viên dùng Windows và PowerShell. Mỗi máy tạo môi trường riêng từ cùng `uv.lock`, nên cả nhóm dùng cùng phiên bản thư viện.

Môi trường đã được thiết lập và kiểm tra trên máy Quốc Hưng ngày 30/10/2026: **Python 3.12.15**, FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2.0, SQLite async và các công cụ dev.

## 1. Thành viên mới cần làm gì?

### Bước 1 — Lấy bản code mới nhất

Lấy/pull repo ProActive có đủ các file:

```text
backend/
  pyproject.toml
  uv.lock
  .python-version
  .env.example
  scripts/check_environment.py
```

Mở **thư mục gốc repo ProActive** bằng VS Code. Chọn **Terminal → New Terminal**, dùng PowerShell và gõ:

```powershell
cd backend
```

Terminal cần đang ở thư mục có `pyproject.toml`. Trên máy Quốc Hưng, thư mục này là `D:\HCMUS\CT\ProActive\backend`. Máy khác dùng đường dẫn clone của mình.

### Bước 2 — Kiểm tra hoặc cài uv

```powershell
uv --version
```

Nếu hiện số phiên bản thì chuyển sang bước 3. Nếu báo không nhận lệnh `uv`, cài bằng:

```powershell
winget install --id astral-sh.uv --exact --source winget
```

Sau khi cài, đóng terminal cũ và mở terminal mới. Vào lại `backend/`, rồi chạy `uv --version` để kiểm tra.

Nếu máy không có `winget`, làm theo [hướng dẫn cài uv chính thức](https://docs.astral.sh/uv/getting-started/installation/).

### Bước 3 — Cài môi trường dự án

Trong `backend/`, chạy:

```powershell
uv sync --frozen --group dev
```

Lệnh này đọc `.python-version` và `uv.lock`, tạo `backend/.venv` và cài thư viện. Lần đầu có thể cần tải Python 3.12.15 và các gói nên cần mạng. Python mặc định của máy có thể khác phiên bản; các lệnh dự án dùng `uv run` để chọn đúng môi trường.

Mong đợi: lệnh kết thúc thành công, không có dòng `error`. Không cần mở/kích hoạt `.venv` bằng tay.

### Bước 4 — Tạo cấu hình local và kiểm tra

```powershell
uv run --frozen python scripts/check_environment.py --prepare-env
```

Lệnh này:

- Tạo `.env` từ `.env.example` nếu máy chưa có file; giữ nguyên `.env` đã tồn tại.
- Tạo `SECRET_KEY` riêng cho máy, không in key ra terminal.
- Để key AI/Voice trống khi tạo mới; bước kiểm tra không gọi dịch vụ thật.
- Kiểm Python, thư viện, một request FastAPI/Pydantic giả lập trong bộ nhớ và truy vấn SQLite async trong bộ nhớ.

Mong đợi có các dòng:

```text
OK Python 3.12.15 in backend virtual environment
OK FastAPI + Pydantic v2 + HTTP test client
OK SQLAlchemy 2.0 + aiosqlite (in-memory database)
...
READY: development environment. ProActive APIs and external services are not tested.
```

Thấy `READY` là môi trường đã sẵn sàng để code. Đây chưa phải kết quả kiểm thử API nghiệp vụ của ProActive.

## 2. Khi mở lại dự án vào ngày khác

Mở terminal ở `backend/`. Kiểm tra nhanh bằng:

```powershell
uv run --frozen python scripts/check_environment.py
```

Sau khi pull code có thay đổi `pyproject.toml` hoặc `uv.lock`, đồng bộ lại:

```powershell
uv sync --frozen --group dev
```

`--prepare-env` có thể chạy lại an toàn: file `.env` đang có được giữ nguyên.

## 3. Những file nào dùng chung cho nhóm?

| File/thư mục | Cách sử dụng |
|---|---|
| `pyproject.toml` | Khai báo thư viện và cấu hình công cụ, đưa lên Git |
| `uv.lock` | Khóa phiên bản thư viện dùng chung, đưa lên Git |
| `.python-version` | Chọn Python 3.12.15, đưa lên Git |
| `.env.example` | Tên biến và giá trị mẫu, đưa lên Git |
| `scripts/check_environment.py` | Lệnh chuẩn bị/kiểm tra môi trường, đưa lên Git |
| `requirements.txt` | File tương thích được xuất từ lockfile, không sửa độc lập |
| `.venv/` | Mỗi máy tự tạo bằng `uv sync`, được Git bỏ qua |
| `.env` | Key và cấu hình riêng từng máy, được Git bỏ qua |

Khi cần thêm thư viện, người phụ trách dùng `uv add` hoặc `uv remove`, kiểm lại môi trường và gửi thay đổi `pyproject.toml` cùng `uv.lock`. Cập nhật `requirements.txt` bằng lệnh xuất trong README Backend. Các thành viên còn lại pull rồi chạy `uv sync --frozen --group dev`.

## 4. Chạy kiểm tra code trong lúc phát triển

Các công cụ đã được cài. Từ `backend/`:

```powershell
uv run --frozen ruff check app scripts
uv run --frozen ruff format --check app scripts
uv run --frozen ty check app scripts
```

Khi nhóm đã viết test trong `tests/`, chạy:

```powershell
uv run --frozen pytest -q
```

Ruff kiểm lint/định dạng; ty kiểm kiểu dữ liệu; pytest chạy các ca test đã viết. Chưa có test hoặc test bị skip không được ghi là đã kiểm thử nghiệp vụ đạt.

## 5. Sau khi code xong: chạy Backend để thử API

### Điều kiện

Trước bước này cần có `app.main:app`, router, Settings đọc `.env` và các chức năng muốn thử đã được triển khai. Nếu phần đó dùng DB thì phải có model, cấu hình Alembic và migration. API cần user phải có luồng Auth/token kiểm thử thật của build.

Các file `app/` ở thời điểm chuẩn bị môi trường hiện mới là comment; chưa thể dùng lệnh bên dưới để chạy ứng dụng ProActive.

### Database local

Điền `DATABASE_URL` trong `.env` theo môi trường. SQLite local đã có driver:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./proplan.db
```

Để thử dữ liệu thủ công riêng, có thể dùng file khác như `proactive_manual_test.db`. Giữ terminal ở `backend/` để đường dẫn tương đối nhất quán.

Chỉ khi Alembic và migration đã có, chạy:

```powershell
uv run --frozen alembic upgrade head
uv run --frozen alembic current
```

Sau đó khởi động server:

```powershell
uv run --frozen uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Giữ terminal mở và truy cập `http://127.0.0.1:8000/docs` để thử API. `Ctrl+C` dừng server.

Khi thử, lấy ID/revision từ phản hồi thực tế, dùng token theo Auth đã triển khai và gửi đúng header `Idempotency-Key` ở lệnh ghi. Cột Token trống trong Sheet không có nghĩa API bỏ xác thực.

### Worker, PostgreSQL và dịch vụ ngoài

- Chỉ chạy `uv run --frozen python -m app.jobs.worker` khi build có điểm vào worker này. HTTP 202 chưa chứng minh job Google đã hoàn thành.
- Driver PostgreSQL `asyncpg` đã được cài; server và database PostgreSQL chưa được tạo trong bước setup này. Khi kiểm tích hợp, dùng DB test riêng và kiểm transaction/concurrency trên DB đích.
- Chỉ cấu hình key AI/Voice hoặc Google OAuth khi thử dịch vụ thật. Kiểm tra môi trường không dùng các key này.
- Khi ghép Mobile, đặt base URL truy cập được tới máy Backend; `localhost` trên điện thoại không trỏ tới máy tính. Timer/DND cần được kiểm trên thiết bị.

## 6. Gặp lỗi thì kiểm tra gì?

| Lỗi | Cách xử lý |
|---|---|
| Không nhận lệnh `uv` | Cài uv, mở lại terminal rồi thử `uv --version` |
| Không tìm thấy pyproject | Vào đúng thư mục `backend/` |
| Không tìm thấy check_environment.py | Pull đủ bản code có thư mục `scripts/` |
| Lockfile không khớp | Pull cùng phiên bản pyproject/lockfile; báo người phụ trách, không tự tạo lock mới |
| Tải Python/gói bị lỗi mạng | Kiểm tra mạng, gửi nguyên lỗi cho Backend Lead; không tự đổi Python/phiên bản gói |
| `Failed to hardlink ... falling back to full copy` | Đây là cảnh báo thường gặp khi cache ở ổ C và repo ở ổ D; nếu cài kết thúc thành công thì tiếp tục bước kiểm tra |
| Không có `app.main:app` | Entry point chưa được code; môi trường đã cài không thay phần việc này |
| Alembic không có config/migration | Chưa triển khai migration; không sửa DB thủ công để bỏ qua |
| Lệnh kiểm tra không ra READY | Gửi toàn bộ lỗi terminal, lệnh đã chạy và nhánh/commit cho Backend Lead; không gửi `.env` hoặc key |

## 7. Phạm vi đã kiểm tra

Ngày 30/10/2026 đã kiểm: Python trong `.venv`, nhập các thư viện chính, request FastAPI/Pydantic qua HTTPX ASGI không gọi mạng và SQLite async trong bộ nhớ. Lệnh kiểm tra không đọc/ghi database nghiệp vụ.

Auth, API nghiệp vụ, migration, worker, Google Calendar và chất lượng AI/Voice cần kiểm riêng khi có code. Kết quả lint/type check trên file khung không chứng minh các phần này đã hoàn thành.

## Tham khảo

- [uv: làm việc với project và lockfile](https://docs.astral.sh/uv/guides/projects/)
- [uv: quản lý Python](https://docs.astral.sh/uv/guides/install-python/)

## C01 — kiểm chứng sau khi bỏ PlanningPart

Code mới cần migration `20261103_01_c01_sources.py`; migration đã kiểm trên SQLite tạm, chưa áp DB app. Sao lưu và review script kiểm migration trên DB bản sao trước khi nâng DB app. Không dùng `create_all` để kết luận upgrade dữ liệu cũ đúng.

Script ở `_temp/c01_tests`: conftest.py, test_unit.py, test_integration.py và system_test.md. User đã duyệt chạy ngày 03/11/2026: unit 10/10, integration 10/10 đạt, gồm hai ca migration trên SQLite tạm. System chưa chạy. Các lệnh dưới dùng từ thư mục gốc repo; chọn thư mục tạm mới cho lần chạy tiếp:

```powershell
$env:PYTHONPATH = "$PWD\backend"
uv run --project backend --frozen --no-sync pytest -c backend/pyproject.toml _temp/c01_tests/test_unit.py -q -p no:cacheprovider --basetemp=_temp/c01_unit_run
uv run --project backend --frozen --no-sync pytest -c backend/pyproject.toml _temp/c01_tests/test_integration.py -q -p no:cacheprovider --basetemp=_temp/c01_integration_run
```

Unit kiểm quy tắc thuần. Integration dùng DB tạm và AI giả, không trỏ DB app hoặc Google. System theo system_test.md phải thao tác app/thiết bị; gọi ASGI không thay system. Suite cũ còn fixture PlanningPart cần cập nhật theo contract, chưa được coi là đã chạy đạt. Không thêm test vào thư mục chính trong đợt này.
