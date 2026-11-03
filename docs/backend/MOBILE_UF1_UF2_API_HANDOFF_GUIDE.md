# Tài Liệu Lý Thuyết FastAPI & Hướng Dẫn Từng API UF1–UF2 Cho Mobile

> **Cập nhật UF1 — 02/11/2026:** Giờ rảnh có thể gửi/nhận trong `summary.availability = {timezone,weekly_rules,date_overrides}` ngay từ yêu cầu voice/text. Tạo Draft → analyze → sửa bằng PATCH → confirm-summary cùng giờ rảnh. Không cần tạo Settings trước; không tự ghi đè giờ chung. Giờ mơ hồ được hỏi lại. `availability_ref` chỉ phục vụ nháp cũ, bị bỏ khi áp giờ riêng. UF2 chưa nối nguồn mới và trả `409 PLAN_NOT_CONFIRMABLE`, `next_action=wait_for_configuration`; chưa được coi là lỗi thiếu giờ để tự chọn lại nguồn chung. Website/Mobile chưa được sửa trong chặng này; Google thật chưa được ghép. Chi tiết kiểu dữ liệu theo `schema.md` O04.

**Người lập & bàn giao:** Quốc Hưng (Backend Lead)  
**Phạm vi:** Lý thuyết kiến trúc FastAPI (từ kế hoạch triển khai) + Hướng dẫn chi tiết từng API trong **20 Endpoints UF1–UF2** cho Mobile (Flutter).

---

# PHẦN A: LÝ THUYẾT FASTAPI & CƠ CHẾ HOẠT ĐỘNG CỦA BACKEND

*(Phần này giúp các thành viên nắm rõ chuyện gì xảy ra bên trong Server FastAPI mỗi khi Mobile gửi 1 HTTP Request lên)*

## 1. Bản Đồ Tư Duy 5 Mắt Xích Xử Lý Request Trong FastAPI

Khi ứng dụng Mobile (Flutter) gửi một yêu cầu HTTP lên Backend, dữ liệu luôn đi qua **5 mắt xích** theo thứ tự sau:

```text
[Mobile App (Flutter / Dio)]
     │  1. Gửi HTTP Request (Method + URL + Header + JSON Body / File Audio)
     ▼
[Mắt xích 1: Router] (backend/app/api/v1/*.py)
     │  - Khai báo đường dẫn (@router.get, @router.post, @router.patch).
     │  - Chỉ làm nhiệm vụ tiếp nhận request và trả response, KHÔNG chứa thuật toán hay SQL.
     ▼
[Mắt xích 2: Schema / DTO] (backend/app/schemas/*.py)
     │  - Dùng Pydantic v2 (ApiModel) để kiểm tra kiểu dữ liệu đầu vào (Input) và lọc đầu ra (Output).
     │  - Tự động chặn ngay tại cửa bằng lỗi 422 nếu client gửi sai kiểu hoặc gửi trường cấm (extra="forbid").
     ▼
[Mắt xích 3: Dependency Injection] (backend/app/api/deps.py)
     │  - Dùng Depends(get_current_user) để xác định User đang gọi API (tự gắn user_a ở môi trường dev).
     │  - Dùng Depends(get_db) để mở phiên kết nối cơ sở dữ liệu bất đồng bộ (AsyncSession) và tự đóng khi xong.
     ▼
[Mắt xích 4: Service Điều Phối] (backend/app/services/*_service.py)
     │  - Trái tim nghiệp vụ của Backend:
     │    + Kiểm tra quyền sở hữu tài nguyên (Ownership: User A không đọc/sửa được của User B).
     │    + Kiểm tra chống gửi trùng (Idempotency-Key).
     │    + Kiểm tra xung đột phiên bản (expected_*_revision).
     │    + Gọi sang 4 bộ phận chuyên môn bên dưới:
     ├──► [Database] (app/models/*.py)              -> Lưu và đọc dữ liệu bền vững qua SQLAlchemy 2.0.
     ├──► [AI Adapter] (services/ai_service.py)      -> Trích xuất tóm tắt UF1, chia bước & gợi ý micro-task UF2.
     ├──► [Algorithm] (services/scheduler_engine.py) -> Nhân hệ số đệm buffer, xếp lịch, tính thiếu hụt giờ rảnh.
     └──► [Voice Adapter] (services/voice_service.py)-> Chuyển file ghi âm ở UF1 thành văn bản (STT).
```

---

## 2. Các Khái Niệm FastAPI Cốt Lõi Áp Dụng Trong Dự Án

### 2.1. Khởi tạo `FastAPI()`, `APIRouter` và Trang `/docs` (Swagger UI)
* **`APIRouter(prefix="/api/v1")`:** Toàn bộ 20 API được chia vào 7 file router con (`drafts.py`, `voice.py`, `planning.py`, `tasks.py`, `schedule.py`, `settings.py`, `system.py`) và gom chung dưới tiền tố `/api/v1`.
* **Swagger UI (`http://127.0.0.1:8000/docs`):** FastAPI tự động đọc các class Schema để vẽ ra trang tài liệu tương tác trực quan. Mobile có thể bấm nút **Try it out $\rightarrow$ Execute** ngay trên trình duyệt để xem JSON thật trước khi viết code Dart.
* **Lệnh bật Server & Địa chỉ kết nối cho Mobile:**
  ```powershell
  cd backend
  uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
  ```
  * Trên **Android Emulator**: gọi vào `http://10.0.2.2:8000/api/v1`
  * Trên **Windows / Web / iOS Simulator**: gọi vào `http://127.0.0.1:8000/api/v1`

### 2.2. Pydantic v2 `ApiModel`, Vỏ bọc JSON (`Envelope`) & Bộ bắt lỗi tập trung (`ExceptionHandler`)
* **Khuôn mẫu `ApiModel` (`extra="forbid"`):** Mọi dữ liệu gửi lên hoặc trả về đều đi qua class kế thừa từ `ApiModel`. Nếu Mobile gửi thừa một trường lạ không có trong hợp đồng (hoặc cố gửi các trường chỉ-server-được-quyết như `can_plan`, `feasible`, `stale`), FastAPI sẽ tự động từ chối bằng mã `422`.
* **Vỏ bọc phản hồi (`SuccessEnvelope` & `ErrorEnvelope`):**
  * Khi **Thành công (`200 OK` / `201 Created`)**: Dữ liệu chính luôn nằm trong khóa `"data"`, đi kèm `"meta"` (`request_id`, `server_time`).
  * Khi **Thất bại (`401` / `404` / `409` / `422` / `503`)**: Bộ bắt lỗi tập trung (`core/errors.py`) tự động gói mọi lỗi vào khóa `"error"` gồm:
    * `code`: Mã lỗi cố định (`RESOURCE_NOT_FOUND`, `REVISION_CONFLICT`, `SOURCE_CHANGED`, `IDEMPOTENCY_KEY_REUSED`, `PLAN_NOT_CONFIRMABLE`, `VALIDATION_ERROR`).
    * `message`: Câu thông báo tiếng Việt chuẩn ADHD.
    * `field_errors`: Danh sách các trường bị lỗi cụ thể (`field_path`, `code`, `message`).
    * `current_revisions`: Số phiên bản hiện tại trên Server (khi bị lệch revision).
    * `next_action`: Hướng dẫn hành động tiếp theo cho Mobile (`fix_fields`, `fix_request`, `reload`, `reload_and_review`, `rebuild_and_review`).

### 2.3. Năm Cách FastAPI Nhận Dữ Liệu Từ Mobile
1. **Path Parameter (`/drafts/{draft_id}`):** Biến nằm ngay trên đường dẫn URL (luôn là chuỗi `str`, ví dụ `"draft_025"`).
2. **Query Parameter (`?from=...&to=...&limit=20&cursor=...`):** Tham số lọc hoặc phân trang nằm sau dấu `?` trên URL.
3. **Request Header (`Idempotency-Key`):** Gửi trong HTTP Header của mọi lệnh ghi (`POST`/`PATCH`). Giúp chống nhân bản dữ liệu khi mạng chập chờn Mobile phải gửi lại (retry).
4. **JSON Request Body:** Dùng cho các lệnh `POST` và `PATCH` gửi dữ liệu có cấu trúc.
5. **Multipart/Form-Data (`UploadFile` + `File` + `Form`):** Dùng riêng cho API gửi file âm thanh `POST /api/v1/voice/transcriptions`.

### 2.4. Cơ chế Cập nhật một phần (`PATCH` với `exclude_unset=True`) & Kiểm soát `Revision`
* **Phân biệt Không gửi (`omitted`) và Gửi `null`:** Trong các API `PATCH`, FastAPI dùng `model_dump(exclude_unset=True)` để biết chính xác Mobile muốn sửa trường nào. Trường nào Mobile **không gửi lên** thì Server **giữ nguyên giá trị cũ** (không bị xóa mất).
* **Chống ghi đè bằng `expected_*_revision`:**
  * Bản nháp (`PlanningDraft`) có `summary_revision` (tăng khi sửa bảng tóm tắt) và `planning_input_revision` (tăng khi sửa đầu vào lập kế hoạch).
  * Phương án lịch (`PlanCandidate`) có `review_revision` (tăng khi sửa bước/phiên) và `source_revisions` (chụp lại phiên bản của Giờ rảnh, Cài đặt, Lịch chính thức, Ngữ cảnh Calendar).
  * Khi gửi lệnh sửa/xác nhận, nếu số `expected_*_revision` Mobile gửi lên khác với số hiện tại trên Server, Server trả về `409 REVISION_CONFLICT` hoặc `409 SOURCE_CHANGED` để bảo vệ dữ liệu.

### 2.5. Cơ chế Khóa tạm (`client_*_key`) $\rightarrow$ Khóa thật (`key_mapping`) ở UF2
* Khi đang xem và chỉnh sửa phương án lịch nháp (`PlanCandidate`), dữ liệu **chưa phải là Task chính thức** nên các phần việc, bước và phiên sử dụng mã tạm: `client_part_key`, `client_step_key`, `client_session_key`.
* Chỉ khi người dùng bấm **Chốt kế hoạch (`POST /candidates/{id}/confirm`)**, Backend mới lưu nguyên tử (`Transaction`) vào bảng `tasks`, `planning_parts`, `steps`, `sessions` và trả về bảng ánh xạ `key_mapping` để đổi toàn bộ mã tạm sang ID chính thức (`task_1`, `part_1`, `step_1`, `session_1`).

---

# PHẦN B: HƯỚNG DẪN TỪNG API (20 ENDPOINTS) ĐỂ MOBILE TÍCH HỢP

Dưới đây là giải thích chi tiết từng API trong 20 Endpoints (chia theo đúng trình tự luồng màn hình trên App).

---

## Cụm 1: 6 API UF1 — Quản Lý Bản Nháp & Xác Nhận Công Việc 1 Vòng (`/api/v1/drafts`)

### API 1 (Dòng 2): `POST /api/v1/drafts` — Tạo bản nháp công việc mới
* **Khi nào Mobile gọi?** Khi người dùng vừa gõ xong yêu cầu công việc (hoặc vừa nhận `transcript` từ API giọng nói) hoặc khi App tự động lưu nháp (auto-save).
* **Đầu vào:**
  * **Header:** `Idempotency-Key: <mã-lệnh-duy-nhất>`
  * **JSON Body (`CreateDraftRequest`):**
    * `client_draft_id` *(string, bắt buộc)*: Mã nháp do máy Mobile tự sinh (ví dụ `"draft_may_1"`). Nếu gửi lại cùng `client_draft_id`, Server trả lại đúng bản nháp đó chứ không tạo trùng.
    * `input_source` *(string, bắt buộc)*: `"text"`, `"voice"`, hoặc `"thought"`.
    * `raw_input` *(string | null)*: Câu yêu cầu gốc của người dùng.
    * `summary` *(object | null)*: Các trường tóm tắt nếu người dùng nhập form trực tiếp (`title`, `description`, `goal`, `scope`, `extra_requirements`, `time_constraint`, `availability_ref`, `importance`). *Không gửi `can_plan` hay `clarification_issues`.*
    * `source_thought_id` *(string | null)*: Mã ghi âm UF4 nếu tạo việc từ Hộp nháp.
* **Đầu ra (`201 Created`):** Đối tượng `PlanningDraft` đầy đủ (xem mẫu `_temp/fixtures_uf1_uf2/01_draft_incomplete.json`).
* **Cách Mobile xử lý:** Lưu lại `draft_id`, `summary_revision` (=1), `planning_input_revision` (=1) để dùng cho các bước tiếp theo.

---

### API 2 (Dòng 3): `GET /api/v1/drafts` — Lấy danh sách các bản nháp đang làm dở
* **Khi nào Mobile gọi?** Khi mở màn hình danh sách nháp hoặc kiểm tra xem người dùng có bản nháp nào chưa hoàn tất không.
* **Đầu vào:**
  * **Query Params (tùy chọn):** `limit` *(mặc định 20)*, `cursor` *(lấy từ `next_cursor` của trang trước)*.
* **Đầu ra (`200 OK`):** `{ "items": [ PlanningDraft, ... ], "next_cursor": "..." | null }`.

---

### API 3 (Dòng 4): `GET /api/v1/drafts/{draft_id}` — Mở lại chi tiết 1 bản nháp
* **Khi nào Mobile gọi?** Khi người dùng bấm vào một bản nháp cũ hoặc mở lại App sau khi bị gián đoạn.
* **Đầu vào:** **Path Param:** `draft_id` (ví dụ `/api/v1/drafts/draft_025`).
* **Đầu ra (`200 OK`):** Đối tượng `PlanningDraft` (mẫu tại `_temp/fixtures_uf1_uf2/02_draft_complete.json`). Nếu không tồn tại hoặc thuộc user khác $\rightarrow$ `404 RESOURCE_NOT_FOUND`.

---

### API 4 (Dòng 5): `POST /api/v1/drafts/{draft_id}/analyze` — Gọi AI phân tích & hỏi làm rõ 1 vòng
* **Khi nào Mobile gọi?** Ngay sau khi tạo bản nháp bằng văn bản tự nhiên, hoặc khi người dùng gửi thêm một câu trả lời bổ sung (`message`) để AI điền nốt các trường còn thiếu.
* **Đầu vào:**
  * **Path Param:** `draft_id`
  * **Header:** `Idempotency-Key`
  * **JSON Body (`DraftAnalyzeRequest`):**
    * `expected_summary_revision` *(int, bắt buộc)*: Số `summary_revision` hiện tại của bản nháp.
    * `expected_planning_input_revision` *(int, bắt buộc)*: Số `planning_input_revision` hiện tại.
    * `raw_input` *(string | null)* hoặc `message` *(string | null)*: Ít nhất 1 trong 2 trường phải có mặt.
* **Đầu ra (`200 OK`):** `PlanningDraft` đã được cập nhật bảng `summary`, tăng `summary_revision` và `planning_input_revision`.
* **Cách Mobile xử lý trên UI:**
  * Hiển thị bảng tóm tắt (`title`, `scope`, `time_constraint`, `availability_ref`).
  * Nếu `summary.can_plan == false`: Hiển thị **toàn bộ câu hỏi trong `summary.clarification_issues[]` trong 1 lần duy nhất** để người dùng bổ sung.
  * Nếu `summary.can_plan == true`: Bật sáng nút **"Xác nhận thông tin công việc"**.

---

### API 5 (Dòng 6): `PATCH /api/v1/drafts/{draft_id}` — Sửa trực tiếp bảng tóm tắt hoặc lưu ý tưởng chia bước
* **Khi nào Mobile gọi?**
  1. **Ở UF1:** Khi người dùng sửa tay các ô trên Bảng tóm tắt (`title`, `scope`, chọn `time_constraint` hoặc `availability_ref`).
  2. **Ở Bước 0 của UF2:** Khi người dùng nhập ý tưởng chia bước riêng vào `user_step_proposal` trước khi gọi tạo Candidate.
* **Đầu vào:**
  * **Path Param:** `draft_id`
  * **Header:** `Idempotency-Key`
  * **JSON Body (`UpdateDraftRequest`):**
    * `expected_planning_input_revision` *(int, bắt buộc)*.
    * `expected_summary_revision` *(int | null)*: **Bắt buộc gửi** nếu trong `changes` có sửa `raw_input` hoặc `summary` (chỉ được bỏ trống khi chỉ sửa mỗi `user_step_proposal`).
    * `changes`: `{ "raw_input": ..., "summary": { ... }, "user_step_proposal": ... }` *(chỉ gửi những trường thực sự muốn sửa)*.
* **Đầu ra (`200 OK`):** `PlanningDraft` mới nhất.
  * *Lưu ý:* Nếu sửa `raw_input` hoặc `summary`, bản nháp sẽ xóa dấu xác nhận cũ (`summary_confirmed_revision = null`) để người dùng xác nhận lại. Nếu chỉ sửa `user_step_proposal`, dấu xác nhận UF1 vẫn được giữ nguyên!

---

### API 6 (Dòng 7): `POST /api/v1/drafts/{draft_id}/confirm-summary` — Chốt bảng tóm tắt UF1
* **Khi nào Mobile gọi?** Khi `summary.can_plan == true` và người dùng bấm nút **"Xác nhận thông tin công việc"** để chuyển sang màn hình UF2.
* **Đầu vào:**
  * **Path Param:** `draft_id`
  * **Header:** `Idempotency-Key`
  * **JSON Body (`ConfirmSummaryRequest`):** `{ "expected_summary_revision": 5 }`
* **Đầu ra (`200 OK` — `ConfirmSummaryResult`):**
  * `{ "draft_id": "draft_025", "stage": "summary_confirmed", "summary_confirmed_revision": 5, "summary_revision": 5, "planning_input_revision": 5 }`
* **Cách Mobile xử lý:** Chuyển sang màn hình UF2, hiện hiệu ứng chờ thư giãn (*"AI đang bẻ nhỏ công việc giúp bạn..."*) và gọi tiếp API 8 (Dòng 9). *(Lưu ý: Kết thúc UF1 chưa tạo Task chính thức).*

---

## Cụm 2: 1 API Giọng Nói UF1 (`/api/v1/voice`)

### API 7 (Dòng 8): `POST /api/v1/voice/transcriptions` — Chuyển file ghi âm thành văn bản (STT)
* **Khi nào Mobile gọi?** Khi người dùng bấm mic thu âm yêu cầu công việc ở **UF1**, hoặc khi đã hết phiên tập trung và người dùng mở Hộp nháp bấm *"Tạo việc từ ghi âm này"*.
  * ⛔ **Quy tắc bất biến UF4:** Tuyệt đối **KHÔNG** gọi API này khi người dùng bấm nút *"Cất suy nghĩ"* trong lúc đang chạy đồng hồ tập trung UF3!
* **Đầu vào:**
  * **Header:** `Idempotency-Key`
  * **Body (`multipart/form-data`):**
    * `purpose`: `"uf1_input"` (nếu thu trực tiếp ở UF1) hoặc `"thought_to_draft"` (nếu lấy từ Hộp nháp UF4 — khi đó bắt buộc gửi thêm trường `thought_id`).
    * `device_id`: Chuỗi mã thiết bị (ví dụ `"android_pixel_7"`).
    * `file`: File âm thanh (`.m4a`, `.wav`, `.mp3`).
* **Đầu ra (`200 OK` — `TranscriptionData`):**
  * `{ "transcript": "...", "provider": "groq_whisper", "language": "vi", "duration_seconds": 6, "thought_id": null }`
* **Cách Mobile xử lý:** Điền đoạn `transcript` vào ô nhập việc UF1 và gọi `POST /api/v1/drafts` để tạo nháp.

---

## Cụm 3: 4 API UF2 — Chia Bước, Xếp Lịch Nháp & Chốt Kế Hoạch (`/api/v1`)

### API 8 (Dòng 9): `POST /api/v1/drafts/{draft_id}/candidates` — Sinh phương án chia bước & xếp lịch (UF2)
* **Khi nào Mobile gọi?** Ngay sau khi người dùng xác nhận bảng tóm tắt UF1 (API 6) xong.
* **Đầu vào:**
  * **Path Param:** `draft_id` (bản nháp phải đã được `confirm-summary` ở phiên bản hiện tại).
  * **Header:** `Idempotency-Key`
  * **JSON Body (`GenerateCandidateRequest`):**
    * `source_summary_revision` *(int, bắt buộc)*: Số `summary_revision` của Draft (ví dụ `5`).
    * `source_planning_input_revision` *(int, bắt buộc)*: Số `planning_input_revision` của Draft (ví dụ `5`).
    * `selected_part_keys` *(list[str] | null, tùy chọn)*: Để `null` ở lần tạo đầu tiên.
    * `planning_horizon` *(TimeInterval | null, tùy chọn)*: Khoảng 2–3 tuần cần xếp lịch chi tiết (Rolling Horizon).
* **Đầu ra (`201 Created` — `PlanCandidate`):** Xem mẫu đầy đủ tại `_temp/fixtures_uf1_uf2/04_candidate_feasible.json`. Gồm:
  * `candidate_id`, `review_revision` (=1), `stale` (=false).
  * `planning_parts[]`: Các phần của dự án (phần trong 2–3 tuần đầu có `decomposition_state="complete"`, phần xa hơn giữ ở `"not_started"` để tránh ngợp).
  * `steps[]`: Danh sách bước cụ thể kèm thời lượng gốc `estimated_base_seconds` và hệ số đệm `buffer_multiplier`.
  * `sessions[]`: Các phiên tập trung dự kiến (`scheduled_start`, `scheduled_end`, `micro_task_text` khởi động nhẹ nhàng, `work_seconds`, `rest_seconds`, `segments[]`, `duration_seconds = work_seconds + rest_seconds`).
  * `feasibility`: Kết quả kiểm tra khả thi trước Deadline:
    * Nếu `feasibility.status == "feasible"`: Cho phép bấm nút **"Chốt kế hoạch"**.
    * Nếu `feasibility.status == "infeasible"`: Hiển thị số giây thiếu hụt `feasibility.deficit_seconds` và `blocking_reasons` để người dùng mở thêm giờ rảnh hoặc nới hạn chót.

---

### API 9 (Dòng 10): `GET /api/v1/candidates/{candidate_id}` — Đọc lại phương án lịch nháp
* **Khi nào Mobile gọi?** Khi người dùng mở lại phương án lịch đang xem dở.
* **Đầu vào:** **Path Param:** `candidate_id`
* **Đầu ra (`200 OK`):** Đối tượng `PlanCandidate`.
  * **Kiểm tra quan trọng:** Nếu `stale == true` (nghĩa là bản nháp Draft gốc đã bị sửa sau khi tạo Candidate này), Mobile hiển thị nút nhắc người dùng tạo lại phương án mới.

---

### API 10 (Dòng 11): `PATCH /api/v1/candidates/{candidate_id}` — Tùy chỉnh Bước / Phiên trong phương án nháp
* **Khi nào Mobile gọi?** Khi người dùng bấm nút **"Tùy chỉnh theo ý tôi"** để sửa tên bước, đầu ra kỳ vọng, thời lượng ước tính hoặc đổi giờ phiên.
* **Đầu vào:**
  * **Path Param:** `candidate_id`
  * **Header:** `Idempotency-Key`
  * **JSON Body (`UpdateCandidateRequest`):**
    * `expected_review_revision` *(int, bắt buộc)*: Số `review_revision` đang hiển thị trên màn hình.
    * `edits`: Chứa ít nhất 1 trong 3 danh sách chỉnh sửa theo khóa tạm:
      * `planning_parts`: `[{ "client_part_key": "...", "scope_description": "...", "rough_estimate_seconds": 3000 }]`
      * `steps`: `[{ "client_step_key": "step-research", "title": "...", "expected_output": "...", "estimated_base_seconds": 3000, "order_index": 0 }]`
      * `sessions`: `[{ "client_session_key": "session-research", "scheduled_start": "...", "scheduled_end": "...", "micro_task_text": "..." }]`
* **Đầu ra (`200 OK`):** `PlanCandidate` đã cập nhật và tăng `review_revision` lên +1.

---

### API 11 (Dòng 12): `POST /api/v1/candidates/{candidate_id}/confirm` — Chốt kế hoạch chính thức
* **Khi nào Mobile gọi?** Khi phương án khả thi (`feasibility.status == "feasible"`) và người dùng bấm nút **"Chốt kế hoạch"**.
* **Đầu vào:**
  * **Path Param:** `candidate_id`
  * **Header:** `Idempotency-Key` *(Giữ lại mã này để tra cứu qua API 20 nếu rớt mạng)*.
  * **JSON Body (`ConfirmCandidateRequest`):**
    * `expected_review_revision`: Số `review_revision` của Candidate.
    * `expected_summary_revision`: Số `summary_revision` của Draft nguồn.
    * `expected_planning_input_revision`: Số `planning_input_revision` của Draft nguồn.
    * `expected_source_revisions`: Đối tượng `source_revisions` nằm trong Candidate (`availability`, `preferences`, `schedule`, `calendar_context`).
* **Đầu ra (`201 Created` — `ConfirmCandidateResult`):**
  * `app_save_state`: `"saved"` (Đã lưu thành công vào App DB).
  * `task`: Toàn bộ cây phân cấp 3 tầng chính thức (`Task` $\rightarrow$ `PlanningParts` $\rightarrow$ `Steps` $\rightarrow$ `Sessions`).
  * `key_mapping`: Bảng ánh xạ từ `client_*_key` sang ID thật trên Server (`task_1`, `part_1`, `step_1`, `session_1`).
  * `schedule_version`: Thông tin phiên bản lịch mới (ví dụ tăng từ `7` lên `8`).
  * `alarm_sync_state`: `"pending"` & `calendar_sync_summary`: `"pending"` (Trạng thái đồng bộ báo thức và Google Calendar tách biệt với trạng thái lưu App).

---

## Cụm 4: 3 API Đọc Công Việc & Lịch Chính Thức Sau Khi Chốt (`/api/v1/tasks` & `/api/v1/schedule`)

### API 12 (Dòng 13): `GET /api/v1/tasks` — Đọc danh sách công việc đã chốt
* **Khi nào Mobile gọi?** Khi mở màn hình Danh sách công việc chính thức.
* **Đầu vào:** **Query Params (tùy chọn):** `limit` *(mặc định 20)*, `cursor`.
* **Đầu ra (`200 OK` — `TaskListData`):** `{ "items": [ Task, ... ], "next_cursor": null }`.
  * *Lưu ý:* Nếu công việc lớn mới chỉ chia bước/xếp lịch cho 2 tuần đầu (Rolling Horizon), `Task` sẽ có `decomposition_state: "partial"` và `scheduling_state: "partial"`.

---

### API 13 (Dòng 14): `GET /api/v1/tasks/{task_id}` — Đọc chi tiết 1 công việc chính thức
* **Khi nào Mobile gọi?** Khi người dùng bấm vào xem chi tiết một công việc (`task_1`).
* **Đầu vào:** **Path Param:** `task_id`
* **Đầu ra (`200 OK`):** Đối tượng `Task` kèm đầy đủ `planning_parts[]`, `steps[]`, `sessions[]` và `source_summary`.

---

### API 14 (Dòng 15): `GET /api/v1/schedule` — Đọc lịch các phiên tập trung chính thức theo khoảng ngày
* **Khi nào Mobile gọi?** Khi hiển thị màn hình Lịch trong ngày / Lịch tuần và khi lập lịch báo thức cục bộ (Local Alarm) cho UF3.
* **Đầu vào:**
  * **Query Params (bắt buộc):** `from` và `to` (ISO-8601 có múi giờ `+07:00`, `from < to`).
  * **Query Params (tùy chọn):** `limit` *(mặc định 50)*, `cursor`.
* **Đầu ra (`200 OK` — `ScheduleData`):**
  * `schedule_revision`: Phiên bản lịch chính thức hiện tại của người dùng.
  * `items`: Danh sách các `Session` chính thức rơi vào khoảng `[from, to)` (đã sắp xếp tăng dần theo `scheduled_start`).
  * `alarm_sync_state`, `calendar_sync_summary`.

---

## Cụm 5: 6 API Hỗ Trợ Hệ Thống, Cài Đặt & Nguồn Lịch Bận (`/api/v1`)

### API 15 (Dòng 39): `GET /api/v1/preferences` — Đọc cài đặt trợ lý hiện tại
* **Khi nào Mobile gọi?** Khi mở App hoặc vào màn hình Cài đặt.
* **Đầu ra (`200 OK` — `UserPreference`):** Xem mẫu `_temp/fixtures_uf1_uf2/06_preferences.json`.
  * Cung cấp `work_interval_seconds` (`1500` = 25p), `rest_interval_seconds` (`300` = 5p), `buffer_multiplier` (`1.2`), `reminder_offsets` (`[900]`), `reminder_style` (`"gentle"`), và số `revision` (`4`).

---

### API 16 (Dòng 40): `GET /api/v1/availability` — Đọc bộ khung giờ rảnh đã xác nhận
* **Khi nào Mobile gọi?** Khi người dùng chọn/xem khung giờ rảnh ở UF1 hoặc màn hình Cài đặt.
* **Đầu vào (Query Params tùy chọn nhưng đi theo cặp):**
  * Cách 1 — **Không truyền `from` và `to`:** Trả về cấu hình quy tắc hàng tuần (`weekly_rules`) và `effective_intervals: null`.
  * Cách 2 — **Truyền đủ cả `from` và `to` (có múi giờ, `from < to`):** Server bung quy tắc hàng tuần thành danh sách các khoảng ngày giờ rảnh thực tế trong `effective_intervals[]`.
  * *(Nếu chỉ gửi `from` mà thiếu `to` hoặc ngược lại $\rightarrow$ trả lỗi `422 VALIDATION_ERROR`: `"Cần gửi cặp from/to hợp lệ."`).*
* **Đầu ra (`200 OK` — `Availability`):** Xem mẫu `_temp/fixtures_uf1_uf2/07_availability.json`.

---

### API 17 (Dòng 60): `GET /api/v1/capabilities` — Đọc khả năng & hằng số vận hành của hệ thống
* **Khi nào Mobile gọi?** Ngay khi mở App để đồng bộ các ngưỡng thời gian với Backend mà không cần hardcode trên Mobile.
* **Đầu ra (`200 OK` — `CapabilitiesData`):** Xem mẫu `_temp/fixtures_uf1_uf2/08_capabilities.json`.
  * `contract_version`: `"v1"`
  * `configured_parameters`:
    * `default_reminder_offset_seconds`: `900` (nhắc trước phiên 15 phút).
    * `start_grace_seconds`: `900` (cho phép trễ giờ bắt đầu 15 phút).
    * `min_inter_session_gap_seconds`: `900` (khoảng cách tối thiểu giữa 2 phiên liên tiếp là 15 phút).
    * `checkin_timeout_seconds`: `300` (sau 5 phút không phản hồi Check-in UF5 thì tự động tắt DND).

---

### API 18 (Dòng 61): `POST /api/v1/planning-contexts` — Chụp ngữ cảnh giờ rảnh & lịch bận trước khi xếp lịch
* **Khi nào Mobile gọi?** Trước khi xếp lịch UF2 hoặc khi người dùng chọn lại nguồn lịch Google Calendar / nhập thêm giờ bận thủ công.
* **Đầu vào:**
  * **Header:** `Idempotency-Key`
  * **JSON Body (`PlanningContextCreateRequest`):**
    * `availability_ref`: `{ "availability_id": "availability_1", "revision": 2 }`
    * `from`, `to`: Khoảng thời gian cần lấy lịch bận (có múi giờ, `from < to`).
    * `selected_calendar_sources`: Danh sách cặp `{ "connection_id": "...", "google_calendar_id": "..." }`.
    * `manual_busy_intervals`: Danh sách khoảng bận nhập tay `[{ "client_interval_id": "...", "start_at": "...", "end_at": "..." }]`.
    * `excluded_sources`: Danh sách nguồn Google Calendar bị lỗi mà người dùng chủ động xác nhận bỏ qua.
* **Đầu ra (`201 Created` — `CalendarContext`):** Xem mẫu `_temp/fixtures_uf1_uf2/09_planning_context.json`.
  * Nếu có `excluded_sources`, trường `coverage_state` sẽ là `"manual_fallback"` (chứ không báo `"complete"` giả tạo).

---

### API 19 (Dòng 62): `GET /api/v1/planning-contexts/{context_id}` — Xem lại bản chụp nguồn lịch bận đã dùng
* **Khi nào Mobile gọi?** Khi người dùng đang xem phương án lịch UF2 và muốn kiểm tra phương án đó được tính dựa trên bản chụp lịch bận nào (`source_revisions.calendar_context.context_id`).
* **Đầu vào:** **Path Param:** `context_id` (ví dụ `"context_1"`).
* **Đầu ra (`200 OK`):** Đối tượng `CalendarContext`.

---

### API 20 (Dòng 63): `GET /api/v1/commands/{command_id}` — Tra cứu biên lai kết quả lệnh khi rớt mạng
* **Khi nào Mobile gọi?** Khi Mobile gửi một lệnh quan trọng (như `POST /candidates/{id}/confirm` với `Idempotency-Key: lenh_confirm_1`) nhưng bị mất kết nối mạng trước khi nhận được HTTP Response.
* **Đầu vào:** **Path Param:** `command_id` (chính là chuỗi `Idempotency-Key` đã gửi, ví dụ `/api/v1/commands/lenh_confirm_1`).
* **Đầu ra (`200 OK` — `CommandResult`):** Xem mẫu `_temp/fixtures_uf1_uf2/10_command_result.json`.
  * `{ "command_id": "lenh_confirm_1", "command_type": "confirm_candidate", "state": "applied", "result_reference": { "task_id": "task_1" }, "applied_at": "..." }`
* **Cách Mobile xử lý:**
  * Nếu nhận `200 OK` với `state == "applied"`: Lệnh đã được Server thực thi thành công $\rightarrow$ lấy `result_reference.task_id` để mở thẳng màn hình công việc mới mà không cần gửi lại.
  * Nếu nhận `404 RESOURCE_NOT_FOUND`: Server chưa ghi nhận lệnh đó $\rightarrow$ Mobile gửi lại lệnh `POST` với **giữ nguyên `Idempotency-Key` cũ**.


## Bổ sung hợp đồng và triển khai ngày 02/11/2026

1. Analyze title >100 ký tự trả 422 VALIDATION_ERROR tại summary.title; raw_input và nháp giữ nguyên.
2. PATCH giữ issue chưa xử lý. Mobile gửi đúng trường user sửa; full form không tự xác nhận trường không đổi. Giữ giá trị cũ có thể gửi PATCH chỉ trường review cùng giá trị. User vẫn phải xử lý các lỗi bắt buộc; không mở UF2 nếu còn blocking.
3. AI có thể điền goal và cả hai nhánh thời gian. AI đang có thông tin thời gian chưa đủ thì API time_constraint vẫn null, issue yêu cầu bổ sung; snapshot nội bộ được phục hồi trên server.
4. UF1/Voice đã có receipt DB. Retry cùng key/payload trả kết quả cũ cả sau restart; nội dung/operation/resource khác dùng mã mới. GET commands không trả _response.
5. Schedule trả calendar_event_link_id nhất quán với Task; có link pending không có nghĩa Google đã đồng bộ.

### API bổ sung — Dòng 41: POST /api/v1/settings/change-requests

Dùng khi user chủ động lưu/xác nhận giờ rảnh hoặc cấu hình trước khi có kế hoạch. Nhánh đã có Task cần UF6, hiện trả 503 và giữ nguyên dữ liệu.

Header: Idempotency-Key. Body mẫu tạo giờ rảnh lần đầu:

```json
{
  "expected_availability_revision": 0,
  "changes": {
    "availability": {
      "timezone": "Asia/Ho_Chi_Minh",
      "weekly_rules": [{"weekday": 1, "start_local": "19:00", "end_local": "21:00"}],
      "date_overrides": [],
      "task_limits": []
    }
  }
}
```

0 nghĩa chưa có bộ giờ rảnh; lưu thành công effective_availability_revision=1. Sau đó GET availability lấy availability_id/revision/confirmed_at cho Draft. Nếu đã có bộ khác thì không dùng lại 0: tải và gửi revision hiện hành.

Đổi preferences cần expected_preferences_revision; tạo lần đầu dùng 0 và cung cấp đầy đủ work_interval_seconds, rest_interval_seconds, buffer_multiplier, reminder_offsets (một phần tử), reminder_style theo lựa chọn user. Nhánh chỉ availability không tự tạo preferences mặc định.

200 trả SettingsChangeResult theo schema; 409 khi revision/key không khớp; 422 khi thiếu/sai dữ liệu; 503 khi đã có Task cần workflow tác động chưa sẵn sàng. Đây là phần hỗ trợ bootstrap UF1–UF2, không phải toàn bộ Settings đã triển khai.
