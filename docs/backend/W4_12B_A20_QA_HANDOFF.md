# W4-12B · Bàn giao QA Bộ mẫu A20 & Kiểm thử Cooldown 7d/14d (`W4-14A`)

Tài liệu hướng dẫn `thành` kiểm thử tính năng **Gợi ý thích ứng hệ số đệm A20 (UF7)** bằng script `scripts/seed_w4_a20_cases.py` trên dữ liệu QA `W4-03A` (SQLite & PostgreSQL 16).

---

## 1. Thông số A20 đã chốt trong Backend

| Tham số (`settings_service.py` / `adaptation.py`) | Giá trị | Ý nghĩa nghiệp vụ |
| :--- | :--- | :--- |
| `SUGGESTION_MIN_SAMPLES` | `7` | Số Step hoàn thành sạch tối thiểu trước khi sinh gợi ý |
| `SUGGESTION_SLIDING_WINDOW` | `10` | Cửa sổ trượt lấy tối đa 10 Step sạch mới nhất (cũ $\rightarrow$ mới) |
| `BUFFER_DELTA_THRESHOLD` | `0.2` | Chỉ gợi ý khi `\|k_suggested - k_user\| > 0.2` (lệch đúng `0.20` không gợi ý) |
| `[BUFFER_POLICY_MIN, BUFFER_POLICY_MAX]` | `[1.0, 2.0]` | Miền giá trị hợp lệ của `buffer_multiplier`, làm tròn bước `0.05` (`ROUND_HALF_UP`) |
| `SUGGESTION_COOLDOWN_SECONDS` | `7 ngày` (`604800s`) | Cooldown khi bấm Từ chối thường (không kèm `annoyance_feedback`) |
| `ANNOYANCE_COOLDOWN_SECONDS` | `14 ngày` (`1209600s`) | Cooldown khi bấm Từ chối có gửi `annoyance_feedback` |

> **Lưu ý bộ lọc mẫu bẩn:** Mọi Step có `outcome="skipped"`, `outcome="no_response"`, `estimate_source="scope_modified"`, hoặc `actual_work_seconds <= 0` đều bị loại trước khi tính trung vị cửa sổ 10. Gợi ý A20 **không bao giờ tự sửa** `UserPreference.buffer_multiplier` cho đến khi người dùng xác nhận (và qua UF6 nếu có phiên tương lai).

---

## 2. Cách chạy script seed trên SQLite & PostgreSQL 16

Điều kiện tiên quyết: DB đã chạy `alembic upgrade head`, tài khoản QA đã đăng nhập Google ít nhất 1 lần (`is_active=True`, có `google_sub`), và đã seed nền `W4-03A` theo [W4_03A_QA_DB.md](W4_03A_QA_DB.md).

Chạy từ thư mục `backend/`:

```powershell
# PowerShell — SQLite cục bộ
$env:DATABASE_URL = "sqlite+aiosqlite:///./proplan_qa.db"
uv run --frozen python scripts/seed_w4_a20_cases.py 'qa-a@example.test' --scenario trigger_window_10

# PowerShell — PostgreSQL 16 (Staging / Docker local, điền URL kết nối cục bộ của bạn)
$env:DATABASE_URL = "postgresql+asyncpg://<user>:<password>@<host>:<port>/<db_name>"
uv run --frozen python scripts/seed_w4_a20_cases.py 'qa-a@example.test' --scenario accept_and_conflict
```

- Script chạy trong **1 transaction atomic** (khóa row `User`), tự động dọn sạch dữ liệu A20 của lần chạy trước rồi mới seed kịch bản mới (idempotent khi chạy lặp lại).
- **Bảo toàn nền `W4-03A`:** Tuyệt đối không xóa hoặc sửa `PlanningDraft`/`Task`/`Step`/`Session`/`SessionRun`/`CheckIn` gắn nhãn `[W4 QA A]` và không chạm tới dữ liệu của tài khoản `qa-b`.

---

## 3. Bảng 6 kịch bản A20 & Kết quả kỳ vọng (API + APK)

| Kịch bản (`--scenario`) | Dữ liệu seed cho `qa-a` | API `GET /api/v1/suggestions/current` | API `POST /api/v1/suggestions/{id}/response` | Kỳ vọng trên APK (Settings $\rightarrow$ UF6) |
| :--- | :--- | :--- | :--- | :--- |
| `below_min_6` | Đếm Step sạch sẵn có (VD `W4-03A` có `1`), thêm `max(0, 6 - existing)` Step sạch (`ratio=1.60`) + **4 mẫu bẩn** (`skipped`, `no_response`, `scope_modified`, `actual=0`). `buffer=1.2`. | `200`, `data: null` | Không có gợi ý để phản hồi | Màn Settings **không** hiện thẻ gợi ý A20; `buffer_multiplier` giữ `1.2`. |
| `exact_delta_020` | `buffer=1.2`; **10 Step sạch mới nhất** (`now - 10h .. now - 1h`) với `actual=1400`, `base=1000` (`ratio=1.40`, đẩy mẫu cũ `W4-03A` ra ngoài cửa sổ 10). | `200`, `data: null` (do `\|1.40 - 1.20\| = 0.20 <= 0.20`) | Không có gợi ý để phản hồi | Màn Settings **không** hiện thẻ gợi ý; kiểm chứng biên `0.20` không bật thông báo thừa. |
| `trigger_window_10` | `buffer=1.2`; **12 Step sạch**: 2 mẫu cũ (`now - 14h, 13h`, `ratio=2.50`) + 10 mẫu mới (`now - 10h .. 1h`, `ratio=1.55`). | `200`, `status: "pending"`, `sample_count: 10`, `current_value: 1.2`, `suggested_value: 1.55` | Cho phép `accept` / `edit` (khi chưa có phiên tương lai sẽ áp dụng từ phiên sau) / `reject` | Hiện thẻ gợi ý đổi `1.2 -> 1.55` (bỏ qua 2 mẫu ngoại lệ `2.50`), `buffer_multiplier` thực tế vẫn là `1.2` trước khi bấm duyệt. |
| `accept_and_conflict` | `buffer=1.2`; **10 Step sạch mới nhất** (`ratio=1.60`) **kèm 1 `Task -> Step -> Session` tương lai** (`now + 1 day`, `status="scheduled"`). | `200`, `status: "pending"`, `sample_count: 10`, `suggested_value: 1.6` | - Gửi lệch `expected_suggestion_revision` / `expected_preference_revision` $\rightarrow$ `409 REVISION_CONFLICT`<br>- `accept` hoặc `edit` $\rightarrow$ `200` (`application_state: "pending_review"`, có `pending_proposal_id`)<br>- Gửi lại `/response` với key mới sau khi đã xử lý $\rightarrow$ `409 SUGGESTION_CHANGED` | Bấm **Đồng ý** hoặc **Chỉnh sửa** giữ nguyên `buffer=1.2`, tạo đề xuất UF6 chờ duyệt và điều hướng sang màn **Xem trước điều chỉnh lịch (UF6)**; chỉ đổi `buffer_multiplier` khi bấm Duyệt ở UF6. |
| `cooldown_7d` | `buffer=1.2`; 10 Step sạch (`ratio=1.60`) + 1 `AdaptationSuggestion(status="rejected", updated_at=now-1h)` + `suggestion_cooldown_until = now + 7d`. | `200`, `data: null` | Không có gợi ý `pending` | Không hiện lại thẻ gợi ý trong cửa sổ cooldown 7 ngày sau từ chối thường. |
| `cooldown_14d` | `buffer=1.2`; 10 Step sạch (`ratio=1.60`) + 1 `AdaptationSuggestion(status="rejected", updated_at=now-1h)` + `suggestion_cooldown_until = now + 14d`. | `200`, `data: null` | Không có gợi ý `pending` | Không hiện lại thẻ gợi ý trong cửa sổ cooldown 14 ngày sau từ chối kèm `annoyance_feedback`. |

---

## 4. Dọn sạch dữ liệu A20 (`--reset`) & Chạy kiểm thử tự động

Sau khi hoàn tất kiểm thử (kể cả sau khi đã bấm `accept`/`edit`/`reject` trên API hoặc duyệt UF6 tạo `RescheduleProposal` / `SettingChangeRequest`), chạy `--reset` để xóa toàn bộ dữ liệu A20, gỡ `pending_proposal_id`, đưa `buffer_multiplier` về `1.2`, xóa `suggestion_cooldown_until` và **giữ nguyên 100% nền `W4-03A`**:

```powershell
# Reset sạch dữ liệu A20 cho tài khoản qa-a (từ thư mục backend/)
uv run --frozen python scripts/seed_w4_a20_cases.py 'qa-a@example.test' --reset
```

Chạy bộ kiểm thử tự động `W4-12B` (SQLite mặc định, hoặc đặt `$env:PROACTIVE_TEST_PG_URL` để chạy trực tiếp trên PostgreSQL 16):

```powershell
# Từ thư mục backend/
.\.venv\Scripts\python.exe -m pytest tests/test_w4_12b_a20_qa_handoff.py -v
```
