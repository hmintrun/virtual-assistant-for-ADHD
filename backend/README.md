# ProActive Backend

Môi trường phát triển: **Python 3.12.15, uv, FastAPI, SQLAlchemy 2.0**.

Trong thư mục `backend/`, chạy:

```powershell
uv sync --frozen --group dev
uv run --frozen python scripts/check_environment.py --prepare-env
```

Lệnh thứ hai tạo `.env` nếu chưa có và kiểm tra môi trường. Không cần API key để chạy bước này.

Hướng dẫn từng bước cho thành viên mới và cách test sau khi code xong: [Thiết lập môi trường Backend](../docs/backend_testing_setup.md).

Cách chia thư mục và nơi đặt code: [Cấu trúc Backend](../docs/backend_structure.md).

Thư viện được quản lý trong `pyproject.toml` và `uv.lock`. `requirements.txt` được xuất từ lockfile; thay đổi dependency bằng `uv add`/`uv remove`, sau đó xuất lại bằng:

```powershell
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
```

Các file `app/` hiện mới là khung comment. Kiểm tra môi trường thành công không có nghĩa các API ProActive, Auth, migration hoặc worker đã được triển khai.
