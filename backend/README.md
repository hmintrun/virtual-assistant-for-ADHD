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

Service/router UF3–UF4 và CheckIn tối thiểu vẫn cần triển khai. Auth hiện dùng tài khoản demo trong development; kiểm khởi động không thay bằng chứng Android, provider thật hoặc triển khai production.
