# ProActive Backend

Môi trường phát triển: **Python 3.12.15, uv, FastAPI, SQLAlchemy 2.0**.

Trong thư mục `backend/`, chạy:

```powershell
uv sync --frozen --group dev
uv run --frozen python scripts/check_environment.py --prepare-env
```

Lệnh thứ hai tạo `.env` nếu chưa có và kiểm tra môi trường. Không cần API key để chạy bước này.

Hướng dẫn từng bước cho thành viên mới và cách test sau khi code xong: [Thiết lập môi trường Backend](../docs/backend/backend_testing_setup.md).

Cách chia thư mục và nơi đặt code: [Cấu trúc Backend](../docs/backend/backend_structure.md).

Thư viện được quản lý trong `pyproject.toml` và `uv.lock`. `requirements.txt` được xuất từ lockfile; thay đổi dependency bằng `uv add`/`uv remove`, sau đó xuất lại bằng:

```powershell
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
```

Backend đã có API/service UF1–UF2, C01 ba tầng Task → Steps → Sessions, receipt DB và tầng lưu trữ tuần 2. Hướng dẫn chạy và kết quả bản ghép hiện tại nằm trong [bàn giao nền tuần 2 ngày 06/11](../docs/week2_baseline_20261106.md).

Bản tích hợp sửa UF1–UF2 trên PR #9 và kết quả kiểm chứng mới nằm trong [báo cáo ngày 07/11](../docs/backend/UF1_UF2_PR9_INTEGRATION_20261107.md). Báo cáo này cập nhật hành vi fallback Python và sửa lỗi xuất SQL PostgreSQL của bản bàn giao 06/11. Google Calendar được để tích hợp sau.

Kiểm tra backend từ thư mục `backend/`:

```powershell
uv run --frozen ruff check .
uv run --frozen ty check
uv run --frozen python -m pytest -q -ra
```

Suite có test khởi chạy Uvicorn trên loopback với SQLite tạm, xác nhận UF1–UF2 rồi khởi động lại để kiểm receipt và dữ liệu. Test không cần khóa provider. Đặt `PLANNING_REVIEW_LEAD_SECONDS` để thay khoảng chờ xem lịch trước phiên đầu (mặc định 300 giây, tối thiểu 120); phương án đã quá giờ phải tạo lại để duyệt.

Kiểm tra bổ sung SQL PostgreSQL bằng PGlite, từ thư mục `backend/` (cần Node/npm):

```powershell
$pgCheckRoot = Join-Path (Get-Location).Path '../_temp/pgcheck'
npm install --prefix $pgCheckRoot @electric-sql/pglite@0.5.15 --no-save --ignore-scripts --no-audit --no-fund
$env:PGLITE_MODULE_ROOT = (Resolve-Path $pgCheckRoot).Path
$env:PYTHON = (uv run --frozen python -c "import sys; print(sys.executable)").Trim()
node scripts/check_postgres_migrations.mjs
```

Script chạy migration SQL trên PostgreSQL WASM cho DB mới và dữ liệu cũ có Parts, candidate, giờ rảnh và cấu hình cá nhân. Bước này chưa kiểm kết nối `asyncpg` với server PostgreSQL triển khai thực tế.

Backend có router/service UF3–UF5 và App Auth Google exchange/JWT; phạm vi kiểm chứng thiết bị/provider và tích hợp Mobile phải đọc theo từng báo cáo, không suy từ việc khởi động server.


## App authentication

See [Auth setup and contract](../docs/backend/AUTH_GUIDE.md) for Google Sign-In/JWT, independent signing key, startup guards, Mobile client IDs, session limitations, offline test commands and acceptance gates. PR17 delivers Backend/Docs; Flutter login remains a demo in the committed baseline and awaits separate integration. See [PR17 verification](../docs/backend/PR17_AUTH_VERIFICATION.md) for the OpenAPI fix and PostgreSQL/Calendar QA scope. Do not rotate Calendar SECRET_KEY when configuring JWT_SECRET_KEY.
