# W4-13 · Báo cáo Kiểm định Backend CI (Static, Contract, SQLite & PostgreSQL 16)

- **Worktree kiểm định:** `D:\HCMUS\CT\ProActive\_temp\wt_merge_stt_uf5_uf7`
- **Nhánh & Commit SHA đã kiểm:** Nhánh `backend`, nền commit `c2c232e` (`feat(w4-12b): add A20 QA seed script, 7d/14d cooldown tests, and handoff guide`) + WIP Phase 1 + bản vá Step 1 cho `[BUG-01]` & `[BUG-02]` (`M Project.md`, `M backend/app/services/reschedule_service.py`, `M backend/tests/test_uf2_fixes_and_risks.py`, `M backend/tests/test_uf6_bugs_and_risks_regression.py`, `M backend/tests/test_w4_staging_security_and_seed.py`, `?? backend/tests/test_uf5_uf6_edge_cases.py`, `?? backend/tests/test_uf5_uf6_matrix.py`, `?? docs/backend/W4_06_STAGING_SECURITY_CHECKLIST.md`, `?? docs/backend/W4_12A_AI_GROUNDED_AUDIT.md`, `?? docs/backend/W4_13_BACKEND_CI_REPORT.md`).
  - *Ghi chú đối soát:* Sẽ đối soát bổ sung `dev` SHA sau khi `mt` hoàn tất merge nhánh vào `dev`.
- **Alembic Head:** `20261118_01` (`20261118_01_refresh_tokens.py`, `down_revision = "20261111_01"`).
- **Tổng kết trạng thái CI (Bước 2 — Tái kiểm chứng sau bản vá Step 1):** **PASS (`0 failed`)** trên toàn bộ cổng Static/Type/Contract (`app`, `tests`, W4 scripts), Alembic SQLite & PostgreSQL 16, Full Offline SQLite `pytest` (**`991 passed, 4 skipped`**) và **5/5 suite PostgreSQL 16 thật** (**`81 passed, 0 failed`**).
  - *Ghi nhận minh bạch:* Quá trình CI W4-13 ban đầu đã phát hiện 2 lỗi `[BUG-01]` và `[BUG-02]` (chi tiết mục 5); cả hai đã được vá ở Step 1 và tái kiểm chứng vượt qua toàn bộ test gates ở Step 2. Lệnh `ruff check --no-cache scripts` trên toàn thư mục `scripts/` giữ nguyên 12 lỗi pre-existing tại 2 script cũ không thuộc phạm vi sửa Tuần 4 (`probe_voice_stt_ai_extraction.py`, `seed_staging.py`).

---

## 1. Kết quả Static, Type & Contract Checks (`backend/`, sau bản vá Step 1)

| Hạng mục | Lệnh chạy thực tế | Kết quả | Ghi chú chi tiết |
| :--- | :--- | :--- | :--- |
| Ruff (`app`, `tests`) | `.\.venv\Scripts\python.exe -m ruff check --no-cache app tests` | **PASS** (`RC=0`) | `All checks passed!` |
| Ruff (`scripts/` toàn bộ) | `.\.venv\Scripts\python.exe -m ruff check --no-cache scripts` | **FAIL** (`RC=1`, **12 errors**) | Lỗi pre-existing ở 2 script cũ: 11 lỗi tại `scripts/probe_voice_stt_ai_extraction.py` (`1x F401`, `8x E402`, `1x ASYNC240`, `1x F841`) và 1 lỗi tại `scripts/seed_staging.py` (`1x UP017`). |
| Ruff (`scripts/` mới W4) | `.\.venv\Scripts\ruff.exe check --no-cache scripts/export_w4_contracts.py scripts/seed_w4_qa.py scripts/seed_w4_a20_cases.py` | **PASS** (`RC=0`) | `All checks passed!` *(kiểm chứng trước Step 1; 3 script này không thay đổi ở Step 1)* |
| Type Check (`ty app`) | `.\.venv\Scripts\python.exe -m ty check app` | **PASS** (`RC=0`) | `All checks passed!` |
| Type Check (`ty app + test W4`) | `.\.venv\Scripts\python.exe -m ty check app tests/test_w4_staging_security_and_seed.py` | **PASS** (`RC=0`) | `All checks passed!` |
| Contract Check (`W4-02`) | `.\.venv\Scripts\python.exe -m scripts.export_w4_contracts --check` | **PASS** (`RC=0`) | `W4-02: artifacts up to date.` |

---

## 2. Kiểm chứng Nâng cấp / Hạ cấp Alembic (`20261111_01` $\leftrightarrow$ `20261118_01`)

### 2.1. SQLite File DB (DB tạm trong `%TEMP%` qua `tempfile.TemporaryDirectory()`)

1. `alembic upgrade 20261111_01` $\rightarrow$ `alembic current` = `20261111_01` (`rc=0`).
2. Chèn 1 bản ghi mẫu `User` + `UserPreference`.
3. `alembic upgrade head` $\rightarrow$ `alembic current` = `20261118_01 (head)` (`rc=0`):
   - `has_refresh_tokens=True`, `cols=12`, `idx=['ix_refresh_tokens_family_id', 'ix_refresh_tokens_user_id']`, `user_row=1`, `pref_row=1`.
4. `alembic downgrade 20261111_01` $\rightarrow$ `alembic current` = `20261111_01` (`rc=0`):
   - `has_refresh_tokens=False` (bảng đã drop sạch), `user_row=1`, `pref_row=1` (bản ghi cũ còn nguyên vẹn).
5. `alembic upgrade head` $\rightarrow$ `alembic current` = `20261118_01 (head)` (`rc=0`):
   - `has_refresh_tokens=True`, `cols=12`, `idx=['ix_refresh_tokens_family_id', 'ix_refresh_tokens_user_id']`, `user_row=1`, `pref_row=1`.
6. Thư mục tạm `%TEMP%` được `TemporaryDirectory` tự động giải phóng; không phát sinh file `.db` trong repository.

### 2.2. PostgreSQL 16 Thật (`proplan-qa-phase0-postgres-staging-1`, `127.0.0.1:5433`)

- **Kiểm chứng đường nâng cấp/hạ cấp kèm dữ liệu & CLI seed (`proactive_w4_13_alembic_pg16_20261122`):**
  1. `alembic upgrade 20261111_01` $\rightarrow$ chèn 2 Google QA `User` (`qa-a@example.test`, `qa-b@example.test`, `is_active=True`, `google_sub` hợp lệ) + 2 `UserPreference`.
  2. `alembic upgrade head` $\rightarrow$ `20261118_01 (head)` (`rc=0`):
     - Giữ nguyên `users=2`, `prefs=2`; `refresh_tokens` có `has_rt=True`, `cols=12`, UQ `['uq_refresh_tokens_id_user', 'uq_refresh_tokens_jti']`, CK `['ck_refresh_tokens_expiry_after_issue', 'ck_refresh_tokens_jti_nonempty', 'ck_refresh_tokens_revision_positive']`, FK `['fk_refresh_tokens_user_id_users']`, Indexes `['ix_refresh_tokens_family_id', 'ix_refresh_tokens_user_id', 'uq_refresh_tokens_id_user', 'uq_refresh_tokens_jti']`.
  3. CLI Seed trên PG16 (`20261118_01`):
     - `scripts/seed_w4_qa.py qa-a@example.test qa-b@example.test` $\rightarrow$ `rc=0`: `W4-03A: Đã kiểm tra seed cho hai tài khoản trên postgresql.`
     - `scripts/seed_w4_a20_cases.py qa-a@example.test --scenario trigger_window_10` $\rightarrow$ `rc=0`: `W4-12B: Đã seed kịch bản 'trigger_window_10' trên postgresql (+12 bước sạch, +0 mẫu bẩn).`
     - `scripts/seed_w4_a20_cases.py qa-a@example.test --reset` $\rightarrow$ `rc=0`: `W4-12B: Đã reset dữ liệu A20 trên postgresql.`
  4. `alembic downgrade 20261111_01` (`has_rt=False`, `users=2`, `prefs=2`) $\rightarrow$ `alembic upgrade head` (`20261118_01`, `has_rt=True`, `cols=12`, `users=2`, `prefs=2`).
- **Tái kiểm chứng Step 2 trên `proactive_w4_13_reverify_pg16_20261122`:**
  - `alembic upgrade head` $\rightarrow$ `alembic downgrade 20261111_01` $\rightarrow$ `alembic upgrade head` $\rightarrow$ `alembic current` = `20261118_01 (head)` (**PASS**).

---

## 3. Bảng Kết quả Pytest — SQLite Offline (Sau bản vá Step 1, không set `PROACTIVE_TEST_PG_URL` / `TEST_POSTGRES_URL`)

| Phạm vi / Lệnh chạy | Passed | Failed | Skipped | XFail | Warnings / Subtests | Thời gian | Trạng thái |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Full Offline Suite:** `.\.venv\Scripts\python.exe -m pytest -q` | **991** | **0** | **4** | 0 | 7 warnings, 23 subtests passed | 466.11s (`0:07:46`) | **PASS** |
| **Targeted UF2 + UF5/UF6 + W4 Security/Seed/Lock:** `.\.venv\Scripts\python.exe -m pytest tests/test_uf2_fixes_and_risks.py tests/test_uf5_uf6_matrix.py tests/test_uf5_uf6_edge_cases.py tests/test_w4_staging_security_and_seed.py -q` | **108** | **0** | 0 | 0 | 0 | 95.60s (`0:01:35`) | **PASS** |
| **W4-12A Audit Suite:** `.\.venv\Scripts\python.exe -m pytest tests/test_uf1_spoken_contexts.py tests/test_uf6_bugs_and_risks_regression.py tests/test_ai_service.py -q` | **109** | **0** | 0 | 0 | 1 warning, 23 subtests passed | 20.16s | **PASS** |

- **Chi tiết 4 test `@pytest.mark.skip` sẵn có trong `tests/extended/`:**
  1. `tests/extended/test_requirement_gaps_and_scenarios.py::TestRequirementGapsAndADHDScenarios::test_gap_2_task_limits_per_day_ignored_by_scheduler` (`#L88-L91` — chưa chốt contract `max_tasks`/ngày).
  2. `tests/extended/test_requirement_gaps_and_scenarios.py::TestRequirementGapsAndADHDScenarios::test_gap_4_chronic_task_avoidance_escalation` (`#L197-L200` — chưa có contract đầu vào `avoidance_count`).
  3. `tests/extended/test_requirement_gaps_and_scenarios.py::TestRequirementGapsAndADHDScenarios::test_gap_6_zero_rest_burnout_prevention` (`#L386-L389` — quy tắc ép nghỉ sau 90p chưa chốt).
  4. `tests/extended/test_unimplemented_and_failing_gaps.py::TestUnimplementedAndHiddenGaps::test_gap_in_memory_idempotency_dict_vulnerability` (`#L227-L230` — đã thay bằng regression receipt lưu DB).
- **Bằng chứng khóa ghi SQLite `calendar_sync_worker.py` (`BE-05` #6) & W4 Security/Seed:** Tập hợp `tests/test_w4_staging_security_and_seed.py` (53 ca, gồm `BE-02`, `BE-05`, `W4-03A`, `test_be05_calendar_sync_worker_sqlite_concurrency_no_database_lock`, `test_be05_calendar_sync_worker_sqlite_skip_busy_connections`) nằm trong tổ hợp 108/108 và 991/991 đều **PASSED**.

---

## 4. Bảng Kết quả Pytest — PostgreSQL 16 Thật (`proactive_w4_13_reverify_pg16_20261122`, Sau bản vá Step 1)

> Trước mỗi suite, DB `proactive_w4_13_reverify_pg16_20261122` được `DROP` & `CREATE DATABASE` sạch và chạy `alembic upgrade head` (`20261118_01`), sau đó đặt `$env:PROACTIVE_TEST_PG_URL` trỏ về PG16 `127.0.0.1:5433`.
> *Lưu ý tiền kiểm:* `test_uf3_uf4_deep_fixes_regression.py` không tự chạy `Base.metadata.create_all` khi bật `PROACTIVE_TEST_PG_URL`; 4 suite còn lại có gọi `Base.metadata.create_all` (và `test_uf6_bugs_and_risks_regression.py` đã bổ sung `_truncate_pg_tables()` cô lập từng test trong file).

| # | Bộ test PG16 (lệnh `-v`) | Passed | Failed | Skipped | XFail | Warnings | Thời gian | Trạng thái |
| :-: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | `pytest tests/test_uf6_bugs_and_risks_regression.py -v` | **23** | **0** | 0 | 0 | 0 | 23.10s | **PASS** |
| 2 | `pytest tests/test_be03_auth_rotation_logout.py -v` | **10** | **0** | 0 | 0 | 0 | 3.85s | **PASS** |
| 3 | `pytest tests/test_w4_12b_a20_qa_handoff.py -v` | **12** | **0** | 0 | 0 | 0 | 12.10s | **PASS** |
| 4 | `pytest tests/test_uf3_uf4_deep_fixes_regression.py -v` | **7** | **0** | 0 | 0 | 0 | 4.01s | **PASS** |
| 5 | `pytest tests/test_uf5_uf7_acceptance_checklist_20261119.py -v` | **29** | **0** | 0 | 0 | 4 | 8.30s | **PASS** |
| **Tổng** | **5/5 bộ kiểm thử PG16** | **81** | **0** | **0** | **0** | **4** | — | **PASS** |

---

## 5. Nhật ký `[BUG]` Phát hiện trong CI W4-13 & Tái kiểm chứng Sau Vá

### `[BUG-01]` (Đã vá ở Step 1 & Re-verify PASS): `test_risk_uf2_03_60_day_horizon_slice_pending_steps_and_uf6_expand_next_horizon`

- **Phát hiện ban đầu (trước Step 1):** Khi chạy vào khung giờ 19:00–21:00 VN (`12:45–12:59Z`), full SQLite suite FAIL `1/990` tại `tests/test_uf2_fixes_and_risks.py#L1337` (`AssertionError: assert 'infeasible' == 'feasible'`, `deficit_seconds=2764`/`2781`) vì `plan_service._horizon()` (`#L822-L839`) dùng lead `+5 phút` (`PLANNING_REVIEW_LEAD_SECONDS=300`) nhưng `reschedule_service._build_normal_proposal_plan()` ở nhánh `expand_next_horizon` kiểm `s_start < task_cursor` (`calc_now + 15m`), đẩy dây chuyền 17/18 phiên tiến +1 ngày vượt `horizon_end`.
- **Bản vá Step 1 & Kết quả Re-verify Step 2:**
  - `backend/app/services/reschedule_service.py#L2985-L2990`: bổ sung `min_valid_start` cho nhánh `expand_next_horizon` (khi chưa có phiên trước bị dịch trong task, dùng ngưỡng `calc_now` thay vì `calc_now + 15m`).
  - `backend/tests/test_uf2_fixes_and_risks.py#L1361-L1462`: bổ sung ca pin đồng hồ `test_risk_uf2_03_pinned_clock_session_within_15m_lead_preserved_on_expand_next_horizon` (`12:59:00Z` / `19:59+07`).
  - **Tái kiểm chứng Step 2:** **PASS (`0 failed`)** trong cả full SQLite suite (`991 passed, 4 skipped`) lẫn tổ hợp targeted SQLite (`108 passed`).

### `[BUG-02]` (Đã vá ở Step 1 & Re-verify PASS): `test_sync_worker_concurrent_refresh_and_background_loop_no_false_412_or_diverged` trên PG16

- **Phát hiện ban đầu (trước Step 1):** Khi chạy cả file `tests/test_uf6_bugs_and_risks_regression.py` trên một DB PG16 dùng chung, ca #22 FAIL (`AssertionError: assert ((7 + 0) + 0) == 1`) do fixture `_regression_db_env()` không dọn các `CalendarSyncJob(job_state="queued")` còn sót từ các ca trước trong cùng file.
- **Bản vá Step 1 & Kết quả Re-verify Step 2:**
  - `backend/tests/test_uf6_bugs_and_risks_regression.py#L72-L116`: bổ sung `_truncate_pg_tables(engine)` (`TRUNCATE TABLE ... RESTART IDENTITY CASCADE`) trước và sau mỗi test khi chạy với `PROACTIVE_TEST_PG_URL`.
  - **Tái kiểm chứng Step 2:** `pytest tests/test_uf6_bugs_and_risks_regression.py -v` trên PG16 đạt **`23 passed in 23.10s` (`0 failed`)**.

---

## 6. Xác nhận Dọn dẹp Sau Kiểm định PG16

- Đã ngắt kết nối và `DROP DATABASE IF EXISTS` toàn bộ các DB tạm của phiên W4-13 (`proactive_w4_13_alembic_pg16_20261122`, `proactive_w4_13_ci_pg16_20261122`, `proactive_w4_13_reverify_pg16_20261122`).
- Truy vấn `SELECT datname FROM pg_database WHERE datname LIKE 'proactive_w4_13_%';` trả về **`(0 rows)`** (giữ nguyên 16 DB lịch sử từ các phiên 2026-11-17..2026-11-21 không liên quan).
- Đã dừng container PostgreSQL staging (`docker stop proplan-qa-phase0-postgres-staging-1`):
  - `docker ps -a --filter "name=proplan-qa-phase0-postgres-staging-1" --format "{{.Names}} | {{.Status}}"`:
    `proplan-qa-phase0-postgres-staging-1 | Exited (0) Less than a second ago`.
- Kiểm tra `git status -s` trong worktree: không còn file `.db` tạm trong repository và không chứa bất kỳ thông tin mật khẩu, token hay biến môi trường nhạy cảm nào.
