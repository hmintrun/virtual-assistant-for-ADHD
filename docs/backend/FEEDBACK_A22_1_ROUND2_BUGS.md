# PHIẾU PHẢN HỒI KỸ THUẬT (ROUND 2): CÁC LỖI CẦN XỬ LÝ TRONG TASK A22.1

**Gửi:** Nguyễn Quốc Thịnh (`qt`)  
**Từ:** Võ Quốc Hưng (`vqh`) — Backend Lead  
**Đối tượng:** Rà soát commit mới [`0acdafd`](file:///C:/Users/admin/.gemini/antigravity/worktrees/ProActive/backend_task_audit_docs) (`fix(calendar): address lead code review feedback (P0-P2)`) trên nhánh `origin/feat/calendar-integration-a22.1`  
**Ngày thực hiện:** 08/11/2026  

---

Chào Thịnh,

Trước hết, tui đánh giá rất cao tốc độ phản hồi và tinh thần làm việc của ông! Ông đã tiếp thu và xử lý dứt điểm cả 8 đầu việc P0–P2 từ phiếu review trước chỉ trong đúng một commit `0acdafd`. Phần xử lý múi giờ địa phương `Asia/Ho_Chi_Minh` cho All-Day Events, cơ chế Fernet Envelope gắn Tenant Binding và chặn đứng vòng lặp vô hạn ở Outbox Worker ông viết rất chuẩn chỉnh.

Tuy nhiên, khi tui cùng đội subagent rà soát chuyên sâu từng luồng chạy thực tế, vẫn còn một số lỗi logic và rủi ro kỹ thuật cần giải quyết dứt điểm trước khi merge vào nhánh chính `origin/backend`.

> [!IMPORTANT]
> **PHÂN CÔNG XỬ LÝ:**
> * **Lỗi Database Migration (`return_target`):** **PHẦN NÀY TUI SẼ TRỰC TIẾP GIẢI QUYẾT** khi hòa nhánh vào `origin/backend`. Ông không cần phải tạo migration hay chỉnh sửa phần này.
> * **Tất cả các lỗi logic còn lại bên dưới:** **ÔNG TỰ XỬ LÝ GIÚP TUI NHÉ.** Dưới đây là giải thích chi tiết nguyên nhân gốc rễ và gợi ý cách fix cụ thể cho từng lỗi.

---

## 1. THÔNG BÁO: LỖI DATABASE MIGRATION CỦA `return_target` (Tui Sẽ Xử Lý)

* **Vị trí code:** `backend/app/models/calendar.py:150`
* **Vấn đề:** 
  Ông đã thêm cột `return_target: Mapped[str | None] = mapped_column(Text)` vào model `CalendarAuthorization`. Trong unit test của ông, test vẫn PASS vì fixture `test_session` dùng SQLite in-memory và gọi `Base.metadata.create_all`, SQLAlchemy sẽ tự động sinh bảng tạm thời trong RAM có sẵn cột mới này.
  Tuy nhiên, trong `backend/migrations/versions/` chưa có file migration Alembic nào thêm cột `return_target`. Khi chạy trên database thật (SQLite file `proplan.db` hay PostgreSQL Staging), lệnh `INSERT` khi gọi API `/authorizations` sẽ văng lỗi 500 ngay lập tức:
  `OperationalError: no such column: return_target`.
* **Xử lý:** **Tui sẽ viết file migration `20261108_02_add_calendar_auth_return_target.py` và gắn vào revision HEAD của nhánh `backend` khi merge. Phần này ông không cần bận tâm.**

---

## 2. CÁC LỖI ÔNG CẦN TỰ GIẢI QUYẾT (GIẢI THÍCH CHI TIẾT)

### 🔴 LỖI 1 (P1 - CỐT LÕI): Outbox Worker Vỡ Logic Khi Xử Lý Manual Event Changes (Endpoint 56)

* **Vị trí code:** `backend/app/services/calendar_sync_worker.py`, dòng 98–125 (trong hàm `process_sync_job`).
* **Hiện tượng & Nguyên nhân gốc rễ:**
  Ở Endpoint 56 (`confirm_event_change_preview`), job sinh ra trong Outbox là `CalendarSyncJob(target_kind="event", ...)` đại diện cho thay đổi sự kiện thủ công trên Google Calendar. Vì đây là sự kiện Google tùy biến chứ không phải `Session` học tập trong hệ thống, nên job này **hoàn toàn không có liên kết `CalendarEventLink`** (`job.event_link_id = None`, dẫn tới biến `link = None`).

  Khi worker chạy `process_sync_job`:
  1. **Khi `job.operation == "update"` (dòng 88–94):**
     ```python
     elif job.operation == "update":
         if not link or not link.google_event_id:
             job.job_state = "failed"
             job.last_error_code = "LINK_OR_EVENT_NOT_FOUND"
             await db.commit()
             return "failed"
     ```
     $\rightarrow$ Vì `link is None`, worker lập tức đánh rớt job với lỗi `LINK_OR_EVENT_NOT_FOUND`. Người dùng không thể nào cập nhật được sự kiện trên Google Calendar qua Endpoint 56!
  
  2. **Khi `job.operation == "delete"` (dòng 112–126):**
     ```python
     elif job.operation == "delete":
         if link and link.google_event_id:
             await client.delete_event(...)
             link.is_current = False
             ...
         job.job_state = "completed"
         await db.commit()
         return "completed"
     ```
     $\rightarrow$ Vì `link is None`, khối `if` bị bỏ qua, **lệnh xóa trên Google API không hề được gọi**, nhưng worker vẫn thản nhiên cập nhật `job.job_state = "completed"`! Đây là lỗi **Silent No-op (báo thành công nhưng thực tế không làm gì)** cực kỳ nguy hiểm.

* **Hướng dẫn sửa:**
  Trong `process_sync_job`, ông cần hỗ trợ cả 2 trường hợp: job xuất phát từ `session` (có `link`) và job xuất phát từ `event` đổi thủ công (không có `link`):
  ```python
  # Xác định google_event_id mục tiêu
  target_google_event_id = link.google_event_id if link else (job.target_ref if job.target_kind == "event" else None)

  if job.operation == "update":
      if not target_google_event_id:
          job.job_state = "failed"
          job.last_error_code = "EVENT_ID_NOT_FOUND"
          await db.commit()
          return "failed"

      res = await client.update_event(
          access_token=access_token,
          calendar_id=conn.write_calendar_id,
          event_id=target_google_event_id,
          event_payload=job.payload_snapshot,
          expected_etag=job.expected_google_etag,
      )
      if link:
          link.google_etag = res.get("etag")
          link.synced_schedule_revision = job.target_schedule_revision
          link.sync_state = "synced"
          link.last_synced_at = now_utc

      job.job_state = "completed"
      conn.last_successful_sync_at = now_utc
      await db.commit()
      return "completed"

  elif job.operation == "delete":
      if not target_google_event_id:
          job.job_state = "failed"
          job.last_error_code = "EVENT_ID_NOT_FOUND"
          await db.commit()
          return "failed"

      await client.delete_event(
          access_token=access_token,
          calendar_id=conn.write_calendar_id,
          event_id=target_google_event_id,
          expected_etag=job.expected_google_etag,
      )
      if link:
          link.is_current = False
          link.sync_state = "deleted"
          link.last_synced_at = now_utc

      job.job_state = "completed"
      await db.commit()
      return "completed"
  ```

---

### 🟡 LỖI 2 (P2): Vòng lặp Lifespan Worker trong `main.py` Kích Hoạt Ngầm Trong Bộ Test & Bypass DB Fixture

* **Vị trí code:** `backend/app/main.py:18–45`.
* **Hiện tượng & Nguyên nhân gốc rễ:**
  Hàm `_calendar_sync_background_loop()` được gắn vào `lifespan(app: FastAPI)` của ứng dụng:
  ```python
  async def _calendar_sync_background_loop():
      session_maker = session_factory(get_engine())  # <--- GỌI TRỰC TIẾP DB VẬT LÝ
      while True:
          ...
  ```
  Khi các bài test của hệ thống sử dụng `with TestClient(app) as client:` (ví dụ trong `tests/test_kc_router_contracts.py` với 13 ca test), Starlette tự động kích hoạt `lifespan startup`.
  Hệ quả:
  1. Vòng lặp worker chạy ngầm ngoài ý muốn, bật và tắt liên tục 13 lần trong quá trình test.
  2. Worker gọi `get_engine()`, tức là kết nối thẳng vào file `proplan.db` mặc định ngoài ổ cứng, hoàn toàn bỏ qua DB in-memory của test fixture.
  3. Khi chạy test đồng thời hoặc chạy trên SQLite file, điều này gây nguy cơ khóa cơ sở dữ liệu (`sqlite3.OperationalError: database is locked`) và xả log lỗi vào console pytest.
* **Hướng dẫn sửa:**
  1. Trong `backend/app/core/config.py`, bổ sung 1 cờ cài đặt:
     ```python
     ENABLE_CALENDAR_SYNC_WORKER: bool = True
     ```
  2. Trong `backend/app/main.py`, chỉ khởi chạy worker task khi cờ này bật VÀ không ở trạng thái test:
     ```python
     @asynccontextmanager
     async def lifespan(app: FastAPI):
         worker_task = None
         if settings.ENABLE_CALENDAR_SYNC_WORKER and not getattr(settings, "TESTING", False):
             worker_task = asyncio.create_task(_calendar_sync_background_loop())
         try:
             yield
         finally:
             if worker_task:
                 worker_task.cancel()
                 try:
                     await worker_task
                 except asyncio.CancelledError:
                     pass
     ```
  3. Trong `backend/tests/conftest.py`, đặt `settings.ENABLE_CALENDAR_SYNC_WORKER = False` để triệt tiêu hoàn toàn task chạy ngầm khi chạy `pytest`.

---

### 🟡 LỖI 3 (P2): Ca Test 6 và Test 7 trong `test_calendar_integration.py` Bị Dở Dang & Test Ảo

* **Vị trí code:** `backend/tests/unit/test_calendar_integration.py`, dòng 241–342.
* **Chi tiết từng test:**
  1. **Test 6 (`test_oauth_authorization_return_target` - dòng 241–272):**
     * Ông đã mock hàm trao đổi token `mock_exchange`, import `_hash_state`, và để lại 2 dòng comment:
       ```python
       # Tìm lại state token từ test
       # Gọi handle_oauth_callback
       ```
     * Nhưng **ông quên gọi `service.handle_oauth_callback`**! Phép assert ở cuối file thực chất chỉ là đọc lại đúng bản ghi `CalendarAuthorization` mà dòng 249 vừa insert vào DB. Luồng callback và logic trả về `return_target` thực tế chưa được kiểm thử.
     * *Cách sửa:* Hãy lấy `state` từ `auth_intent`, gọi `conn, ret = await service.handle_oauth_callback(code="acc_code", state=...)` và assert `ret == "proplan://custom_target"`.

  2. **Test 7 (`test_event_change_preview_and_confirm` - dòng 278–342):**
     * Test này đang mắc lỗi "Tautology Test" (test tự khẳng định): Ông tự tay viết `test_session.add(preview)`, rồi tự tay `test_session.add(job)` và đổi `preview.status = "confirmed"`, sau đó assert xem SQLite có lưu các giá trị vừa gán không.
     * Test này không hề gọi qua Endpoint 55 (`create_event_change_preview`), Endpoint 56 (`confirm_event_change_preview`), hay hàm service nào cả. Do đó, logic nghiệp vụ của 2 endpoint này thực chất đang có 0% code coverage.
     * *Cách sửa:* Hãy import và gọi trực tiếp hàm xử lý `create_event_change_preview` và `confirm_event_change_preview` (hoặc gọi qua TestClient) để kiểm tra validation và logic sinh job Outbox thực tế.

---

### ⚪ LỖI 4 (P3 - TỐI ƯU NHỎ): Thiếu Kiểm Tra Expiration Cho Bản Xem Trước (Preview)

* **Vị trí code:** `backend/app/api/v1/calendar.py`, dòng 454–465.
* **Vấn đề:**
  Khi tạo preview ở Endpoint 55, ông có set hạn 15 phút: `expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)`.
  Tuy nhiên, khi người dùng confirm ở Endpoint 56, code hiện tại chỉ kiểm tra:
  ```python
  if preview.status != "pending":
      raise HTTPException(status_code=409, detail="Bản xem trước đã được xử lý hoặc hết hạn.")
  ```
  Code quên kiểm tra xem preview đã quá hạn 15 phút hay chưa!
* **Cách sửa:**
  Bổ sung thêm điều kiện kiểm tra thời gian hết hạn:
  ```python
  now_utc = datetime.now(timezone.utc)
  if preview.expires_at and preview.expires_at < now_utc:
      preview.status = "expired"
      await db.commit()
      raise HTTPException(status_code=409, detail="Bản xem trước thay đổi đã hết hạn (15 phút). Vui lòng tạo bản xem trước mới.")
  ```

---

## 3. TỔNG KẾT & BƯỚC TIẾP THEO CHO ÔNG

Ông chỉ cần tập trung xử lý **3 lỗi chính (Lỗi 1, 2, 3)** và chỉnh nhẹ **Lỗi 4**:
1. Sửa `calendar_sync_worker.py` để hỗ trợ `target_kind == "event"` khi update/delete.
2. Thêm cờ `ENABLE_CALENDAR_SYNC_WORKER` vào `config.py` và `main.py` để không chạy ngầm trong test suite.
3. Hoàn thiện lời gọi hàm trong Test 6 và Test 7 của `test_calendar_integration.py`.
4. Bổ sung check `expires_at` ở Endpoint 56.

Sau khi ông sửa xong và push commit mới lên nhánh `feat/calendar-integration-a22.1`:
* Tui sẽ chủ động tạo file migration Alembic cho cột `return_target`.
* Tui sẽ thực hiện merge nhánh của ông vào `origin/backend` và giải quyết xung đột import ở `router.py`.

Có chỗ nào chưa rõ hoặc cần trao đổi thêm về mặt giải thuật thì cứ ping tui liền nhé! Anh em mình hoàn thành dứt điểm để tuần tới bước sang Sprint 3 cho thật mượt mà.

Chúc ông xử lý nhanh gọn! 🚀
