# PHIẾU PHẢN HỒI KỸ THUẬT & YÊU CẦU ĐIỀU CHỈNH (CODE REVIEW FEEDBACK)
## TASK A22.1 — KẾT NỐI VÀ ĐỌC GOOGLE CALENDAR & HẠ TẦNG ĐỒNG BỘ 2 CHIỀU

**Người gửi:** Võ Quốc Hưng (`vqh`) — Backend Lead & Algorithm Designer  
**Người nhận:** Nguyễn Quốc Thịnh (`qt`) — Backend & Time Synchronization Engineer  
**Branch rà soát:** `origin/feat/calendar-integration-a22.1` (Commit `e2912e3`)  
**Ngày review:** 08/11/2026  
**Trạng thái review:** **Yêu cầu cập nhật (Changes Requested)** trước khi merge vào nhánh chính `backend`.

---

## 1. ĐÁNH GIÁ CHUNG TỪ HƯNG

Chào Thịnh, tui cùng đội ngũ đã rà soát toàn bộ 5 commits trong branch `feat/calendar-integration-a22.1` của ông.

**Lời khen ngợi:** Ông làm việc quá đỉnh luôn! Không chỉ dừng lại ở phạm vi Task A22.1 (kết nối và đọc lịch), ông đã chủ động đặt nền móng rất vững chắc cho toàn bộ lộ trình đồng bộ 2 chiều (Mốc C01 – C04). Đặc biệt:
- Cơ chế mã hóa **Fernet Envelope có Tenant & Connection Binding** trong `credential_crypto.py` là một điểm sáng lớn về mặt an toàn thông tin (chống IDOR / ciphertext swap).
- Bẫy giữ lại `refresh_token` khi người dùng reconnect trong `calendar_service.py` được xử lý rất tinh tế.
- Khử trùng sự kiện của ứng dụng (**Own-Event Protection**) trong `calendar_ingestion.py` và cơ chế chặn retry khi gặp **ETag 412** trong `calendar_sync_worker.py` thể hiện tư duy thiết kế hệ thống phân tán rất tốt.
- Điều kiện khả thi bắt đầu phiên **A12.1** trong `validation.py` viết dạng Pure Function rất sạch sẽ.

Tuy nhiên, để mã nguồn sẵn sàng chạy thực tế với Google Cloud Console và tương thích 100% với Mobile cũng như nhánh chính `backend`, có **3 lỗi cần sửa gấp (P0)** và **3 điểm hoàn thiện (P1)** bên dưới. Ông xem qua rồi chỉnh lại giúp tui nhé!

---

## 2. BẢNG PHÂN LOẠI CÁC VẤN ĐỀ CẦN ĐIỀU CHỈNH

| Mức Độ | Mã Vấn Đề | Vị Trí File | Tóm Tắt Lỗi & Rủi Ro | Hành Động Cần Làm |
|:---:|:---|:---|:---|:---|
| **P0 (Blocker)** | **ISSUE-01** | `app/core/config.py` | Thiếu scope tạo lịch phụ `https://www.googleapis.com/auth/calendar` $\rightarrow$ Chạy thật bị Google chặn **403 Forbidden**. | Thêm scope `calendar` và gỡ bỏ scope thừa `calendar.events`. |
| **P0 (Blocker)** | **ISSUE-02** | `app/services/calendar_sync_worker.py` | Vòng lặp vô hạn (Infinite Loop Spin) trong worker khi gặp lỗi cấu hình vĩnh viễn (`attempt_count` không tăng, `next_retry_at` là None). | Chỉ query `job_state == "queued"`; gán `attempt_count = 5` khi lỗi không thể retry. |
| **P0 (Blocker)** | **ISSUE-03** | `app/services/planning/calendar_ingestion.py` | Lệch 7 tiếng sự kiện cả ngày (`all-day event`) do ép timezone UTC thay vì timezone người dùng. | Nhận timezone người dùng (`Asia/Ho_Chi_Minh`) để parse mốc `00:00:00+07:00`. |
| **P1 (Contract)** | **ISSUE-04** | `app/api/v1/calendar.py` & `models/calendar.py` | Endpoint dòng 46 (`/oauth/callback`) trả về JSON thay vì Redirect $\rightarrow$ Mobile bị kẹt màn hình Webview; bỏ rơi `return_target`. | Lưu `return_target` vào DB và trả về `RedirectResponse` về app sau khi callback xong. |
| **P1 (Contract)** | **ISSUE-05** | `app/api/v1/calendar.py` | Endpoints dòng 53 & 54 (`/events` & `/{event_id}`) trả raw `dict` thay vì bọc DTO O26 `CalendarEvent`. | Chuẩn hóa kiểu trả về sang `SuccessEnvelope[list[CalendarEvent]]`. |
| **P1 (Contract)** | **ISSUE-06** | `app/api/v1/calendar.py` | Thiếu 2 endpoints dòng 55 & 56 trong `schema.md` (`/event-change-previews` và `/confirm`). | Bổ sung 2 endpoints hoặc đánh dấu deprecated nếu đã gộp vào flow khác. |
| **P2 (Infra)** | **ISSUE-07** | `app/main.py` | Thiếu Background Task (Lifespan / Fast-trigger) tự động tiêu thụ Outbox Jobs sau khi chốt kế hoạch UF2. | Thêm trigger worker hoặc background task định kỳ. |
| **P2 (QA)** | **ISSUE-08** | `backend/tests/` | Chưa có bộ kiểm thử tự động cho các file Calendar mới tạo. | Thêm file test cho Crypto, Ingestion và Worker. |

---

## 3. HƯỚNG DẪN CHI TIẾT CÁCH SỬA KÈM CODE MẪU

### Vấn đề 1 (P0): Sửa Scope Google OAuth trong `config.py`
- **Hiện tượng:** Trong `provision_proactive_calendar` (`google_calendar_client.py:188`), ông gọi API `POST https://www.googleapis.com/calendar/v3/calendars` để tạo lịch riêng "ProPlan VA Focus". Endpoint này **bắt buộc scope `https://www.googleapis.com/auth/calendar`**.
  Nếu chỉ có `calendar.events`, Google sẽ trả lỗi `HTTP 403 Forbidden (Insufficient Permission)`.
- **Cách sửa tại [`backend/app/core/config.py`](file:///C:/Users/admin/.gemini/antigravity/worktrees/ProActive/backend_task_audit_docs/backend/app/core/config.py#L70-L75):**

```python
# Trước:
GOOGLE_CALENDAR_DEFAULT_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/calendar.events",
]

# Sửa thành:
GOOGLE_CALENDAR_DEFAULT_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar",  # Bắt buộc để tạo và quản lý Secondary Calendar ProPlan
]
```

---

### Vấn đề 2 (P0): Chặn Vòng lặp Vô hạn trong Sync Worker
- **Hiện tượng:** Trong `process_calendar_sync_jobs`, câu query SELECT lấy cả `job_state.in_(["queued", "failed"])` với điều kiện `attempt_count < 5`. Khi gặp lỗi cấu hình vĩnh viễn (như chưa chọn `write_calendar_id` hoặc connection ngắt), code gán `job.job_state = "failed"` nhưng không tăng `attempt_count` (vẫn là 0) và không set `next_retry_at`. Ở ngay tick tiếp theo, worker lại bốc trúng chính job này $\rightarrow$ spam log và nghẽn database liên tục.
- **Cách sửa tại [`backend/app/services/calendar_sync_worker.py`](file:///C:/Users/admin/.gemini/antigravity/worktrees/ProActive/backend_task_audit_docs/backend/app/services/calendar_sync_worker.py):**

```python
# 1. Trong process_sync_job: Khi gặp lỗi cấu hình không thể retry, tăng kịch khung attempt_count = 5
if not conn or conn.connection_state != "connected":
    job.job_state = "failed"
    job.last_error_code = "CONNECTION_NOT_ACTIVE"
    job.attempt_count = 5  # Đánh dấu không retry nữa
    await db.commit()
    return "failed"

if not conn.write_calendar_id:
    job.job_state = "failed"
    job.last_error_code = "NO_WRITE_CALENDAR"
    job.attempt_count = 5  # Đánh dấu không retry nữa
    await db.commit()
    return "failed"

# 2. Trong process_calendar_sync_jobs: CHỈ query các job đang ở trạng thái 'queued'
# Trạng thái 'failed' chỉ được chạy lại khi người dùng chủ động bấm Retry (Endpoint 59)
stmt = (
    select(CalendarSyncJob)
    .where(
        CalendarSyncJob.job_state == "queued",
        CalendarSyncJob.attempt_count < 5,
        or_(
            CalendarSyncJob.next_retry_at.is_(None),
            CalendarSyncJob.next_retry_at <= now_utc,
        ),
        or_(
            CalendarSyncJob.lease_expires_at.is_(None),
            CalendarSyncJob.lease_expires_at <= now_utc,
        ),
    )
    .order_by(CalendarSyncJob.created_at.asc())
    .limit(limit)
)
```

---

### Vấn đề 3 (P0): Sửa Lệch Timezone 7 tiếng cho Sự Kiện Cả Ngày (All-Day Event)
- **Hiện tượng:** Với sự kiện cả ngày (`all-day event`), Google trả về `date = '2026-11-09'`. Hàm `parse_event_datetime` đang gán mặc định `default_tz = UTC`. Tại Việt Nam (UTC+7), sự kiện ngày 09/11 sẽ bị chuyển thành `07:00 09/11 đến 07:00 10/11`, làm lệch mất 7 tiếng so với thực tế của người dùng.
- **Cách sửa tại [`backend/app/services/planning/calendar_ingestion.py`](file:///C:/Users/admin/.gemini/antigravity/worktrees/ProActive/backend_task_audit_docs/backend/app/services/planning/calendar_ingestion.py#L41-L76):**

```python
import zoneinfo

def parse_event_datetime(
    dt_payload: dict[str, Any] | None, 
    user_tz_name: str = "Asia/Ho_Chi_Minh"
) -> datetime | None:
    if not dt_payload:
        return None

    dt_str = dt_payload.get("dateTime")
    if dt_str:
        try:
            parsed = datetime.fromisoformat(dt_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=zoneinfo.ZoneInfo(user_tz_name))
            return parsed.astimezone(UTC)
        except Exception:
            return None

    date_str = dt_payload.get("date")
    if date_str:
        try:
            d = date.fromisoformat(date_str)
            user_tz = zoneinfo.ZoneInfo(user_tz_name)
            # Sự kiện cả ngày bắt đầu lúc 00:00:00 theo múi giờ địa phương của người dùng
            local_dt = datetime.combine(d, time.min, tzinfo=user_tz)
            return local_dt.astimezone(UTC)
        except Exception:
            return None

    return None
```
*(Lưu ý: Truyền `user_tz_name` từ `normalize_google_event` xuống hàm này nhé).*

---

### Vấn đề 4 (P1): OAuth Callback Trả về Redirect Thay vì JSON Thô
- **Hiện tượng:** Endpoint `GET /api/v1/calendar/oauth/callback` đang trả về `SuccessEnvelope[CalendarConnectionRead]`. Trên Mobile, khi mở Google Consent Screen qua Custom Tab hoặc Webview, sau khi hoàn tất người dùng sẽ bị kẹt lại nhìn thấy chuỗi JSON thô, không tự động điều hướng về màn hình ứng dụng Flutter. Ngoài ra, trường `return_target` trong request khởi tạo bị bỏ rơi, không được lưu lại.
- **Cách sửa tại [`backend/app/api/v1/calendar.py`](file:///C:/Users/admin/.gemini/antigravity/worktrees/ProActive/backend_task_audit_docs/backend/app/api/v1/calendar.py#L83-L118):**
  1. Trong `CalendarAuthorization`, lưu lại `return_target: str | None`.
  2. Tại `oauth_callback`, thay vì trả JSON, trả về `RedirectResponse`:
     ```python
     from fastapi.responses import RedirectResponse

     @router.get("/oauth/callback", summary="Tiếp nhận Authorization Code từ Google và redirect về app")
     async def oauth_callback(
         code: Annotated[str, Query(min_length=1)],
         state: Annotated[str, Query(min_length=1)],
         db: DbSessionDep,
     ) -> RedirectResponse:
         service = CalendarService(db)
         conn, return_target = await service.handle_oauth_callback(code=code, state=state)
         
         # Điều hướng về Deep Link của Mobile App hoặc URL chỉ định
         target_url = return_target or f"proplan://calendar/connected?connection_id={conn.id}"
         return RedirectResponse(url=target_url, status_code=302)
     ```

---

### Vấn đề 5 (P1): Chuẩn Hóa DTO Cho Endpoints 53 & 54
- **Hiện tượng:** Endpoint `GET /connections/{id}/events` và `GET /connections/{id}/events/{event_id}` đang trả về raw `dict[str, Any]` thay vì đối tượng DTO `CalendarEvent` (O26).
- **Cách sửa:** Bọc dữ liệu trả về vào DTO `CalendarEvent` để Mobile parse dữ liệu an toàn và có gợi ý kiểu (Type-safe).

---

## 4. LƯU Ý KHI MERGE VỚI NHÁNH `origin/backend` (GIT CONFLICT)

Nhánh của ông rẽ từ commit `09f19b9`. Trong khi đó, nhánh `origin/backend` đã phát triển thêm các tính năng của Tuần 2 (Focus Runs, Thoughts Raw Audio, Check-in Routers).

Tui đã chạy kiểm tra hợp nhất 3 chiều trên bộ nhớ (`git merge-tree`):
- `backend/app/services/plan_service.py` $\rightarrow$ **Ghép tự động sạch sẽ 100%**, không mất code của ai.
- `backend/app/services/scheduler_engine.py` $\rightarrow$ **Ghép tự động sạch sẽ 100%**.
- `backend/pyproject.toml` và `.env.example` $\rightarrow$ **Ghép tự động sạch sẽ 100%**.
- **Chỉ có 1 xung đột nhẹ duy nhất:** Tại [`backend/app/api/v1/router.py`](file:///C:/Users/admin/.gemini/antigravity/worktrees/ProActive/backend_task_audit_docs/backend/app/api/v1/router.py).
  * Lý do: `origin/backend` thêm `checkin`, `focus`, `thoughts`; còn ông thêm `calendar`.
  * **Cách giải quyết:** Khi merge, ông chỉ cần import đầy đủ cả 4 router:
    ```python
    from app.api.v1 import (
        calendar,
        checkin,
        drafts,
        focus,
        planning,
        schedule,
        settings,
        system,
        tasks,
        thoughts,
        voice,
    )

    api_router.include_router(calendar.router)
    api_router.include_router(drafts.router)
    api_router.include_router(voice.router)
    api_router.include_router(planning.router)
    api_router.include_router(tasks.router)
    api_router.include_router(schedule.router)
    api_router.include_router(settings.router)
    api_router.include_router(system.router)
    api_router.include_router(focus.router)
    api_router.include_router(thoughts.router)
    api_router.include_router(checkin.router)
    ```

---

## 5. CÁC BƯỚC THỰC HIỆN TIẾP THEO

1. Ông cập nhật các mục **P0** (Issue 01, 02, 03) và **P1** (Issue 04, 05) trực tiếp trên branch `feat/calendar-integration-a22.1`.
2. Chạy lại test cục bộ để xác nhận:
   ```bash
   pytest backend/tests/test_planning_api.py backend/tests/unit/test_scheduler_engine.py
   ```
3. Push các commit sửa lỗi lên `origin/feat/calendar-integration-a22.1` rồi hú tui. Tui sẽ duyệt PR và merge vào `origin/backend` liền luôn!

Cảm ơn ông nhiều nghen, code bài bản và khối lượng làm ra chất lượng lắm!
