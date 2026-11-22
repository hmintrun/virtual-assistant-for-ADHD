"""Bộ kiểm thử đơn vị & tích hợp cho phân hệ Google Calendar (A22.1).

Bao phủ toàn diện 7 ca kiểm thử cốt lõi:
- Test 1: test_google_calendar_scopes: Cấu hình Scope Google OAuth (ISSUE-01 / P0).
- Test 2: test_credential_crypto_envelope_and_binding: Bảo mật Fernet Envelope có Tenant & Connection Binding.
- Test 3: test_parse_all_day_event_local_timezone: Múi giờ sự kiện cả ngày Asia/Ho_Chi_Minh (ISSUE-03 / P0).
- Test 4: test_worker_infinite_spin_prevention: Ngăn chặn Outbox Worker lặp vô hạn khi thiếu write_calendar_id (ISSUE-02 / P0).
- Test 5: test_worker_etag_conflict_handling: Worker xử lý ETag 412 CalendarConflictError và dừng retry mù quáng.
- Test 6: test_oauth_authorization_and_callback_flow: Toàn trình OAuth flow, trích xuất state, exchange token, lưu return_target và tiêu thụ authorization.
- Test 7: test_event_change_preview_expiry_and_idempotent_confirm: Bản xem trước thay đổi sự kiện, kiểm tra hết hạn và xác nhận idempotent Outbox job chống double-click.
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.calendar import confirm_event_change_preview, create_event_change_preview
from app.core.config import settings
from app.core.errors import AppError
from app.models.base import Base
from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
)
from app.models.user import User
from app.schemas.calendar import (
    EventChangeConfirmRequest,
    EventChangePreviewRequest,
)
from app.schemas.user import CurrentUser
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.credential_crypto import decrypt_credentials, encrypt_credentials
from app.services.google_calendar_client import CalendarConflictError
from app.services.planning.calendar_ingestion import parse_event_datetime


@pytest.fixture
async def test_session():
    """Tạo session SQLite in-memory độc lập cho mỗi ca test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# -------------------------------------------------------------
# 1. Test ISSUE-01: Scope Cấu Hình Google OAuth
# -------------------------------------------------------------
def test_google_calendar_scopes():
    """Đảm bảo scope https://www.googleapis.com/auth/calendar có mặt để tạo Secondary Calendar."""
    scopes = settings.GOOGLE_CALENDAR_DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert "https://www.googleapis.com/auth/calendar.events" not in scopes


# -------------------------------------------------------------
# 2. Test Bảo Mật Fernet Envelope (Tenant & Connection Binding)
# -------------------------------------------------------------
def test_credential_crypto_envelope_and_binding():
    """Kiểm tra mã hóa Fernet Envelope và cơ chế ràng buộc định danh chống tráo ciphertext."""
    uid = uuid4()
    conn_id = uuid4()
    payload = {
        "access_token": "ya29.test_token",
        "refresh_token": "1//test_refresh",
        "expires_in": 3600,
    }

    envelope = encrypt_credentials(payload)
    assert envelope.startswith("fernet:v1:")

    # Giải mã với tenant đúng (không ràng buộc)
    decrypted = decrypt_credentials(
        envelope, expected_user_id=None, expected_connection_id=None
    )
    assert decrypted["access_token"] == "ya29.test_token"

    # Giải mã với tenant binding đúng
    payload_with_binding = {**payload, "user_id": str(uid), "connection_id": str(conn_id)}
    env_bound = encrypt_credentials(payload_with_binding)

    decrypted_bound = decrypt_credentials(
        env_bound, expected_user_id=str(uid), expected_connection_id=str(conn_id)
    )
    assert decrypted_bound["access_token"] == "ya29.test_token"
    assert decrypted_bound["user_id"] == str(uid)
    assert decrypted_bound["connection_id"] == str(conn_id)

    # Giải mã với tenant binding sai lệch -> ném lỗi
    with pytest.raises(ValueError, match="tenant binding"):
        decrypt_credentials(
            env_bound, expected_user_id=str(uuid4()), expected_connection_id=str(conn_id)
        )


# -------------------------------------------------------------
# 3. Test ISSUE-03: Timezone All-Day Events Asia/Ho_Chi_Minh
# -------------------------------------------------------------
def test_parse_all_day_event_local_timezone():
    """Kiểm tra sự kiện cả ngày bắt đầu lúc 00:00:00 theo múi giờ địa phương Asia/Ho_Chi_Minh."""
    dt_payload = {"date": "2026-11-09"}
    parsed_utc = parse_event_datetime(dt_payload, user_tz_name="Asia/Ho_Chi_Minh")
    assert parsed_utc is not None

    # 00:00:00 ngày 09/11 tại Việt Nam (UTC+7) tương ứng với 17:00:00 ngày 08/11 tại UTC
    expected_utc = datetime(2026, 11, 8, 17, 0, 0, tzinfo=UTC)
    assert parsed_utc == expected_utc

    # Khi đổi ngược về timezone Asia/Ho_Chi_Minh thì đúng là 00:00:00 ngày 09/11
    vn_tz = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
    local_dt = parsed_utc.astimezone(vn_tz)
    assert local_dt.year == 2026 and local_dt.month == 11 and local_dt.day == 9
    assert local_dt.hour == 0 and local_dt.minute == 0


# -------------------------------------------------------------
# 4. Test ISSUE-02: Outbox Worker Ngăn Chặn Vòng Lặp Vô Hạn
# -------------------------------------------------------------
@pytest.mark.asyncio
async def test_worker_infinite_spin_prevention(test_session: AsyncSession):
    """Khi gặp lỗi cấu hình vĩnh viễn, worker phải set attempt_count=5 và không lặp lại."""
    uid = uuid4()
    user = User(id=uid, email="test@example.com", hashed_password="pw", display_name="Test", is_active=True)
    conn = CalendarConnection(
        id=uuid4(),
        user_id=uid,
        provider="google",
        account_ref="test@example.com",
        read_calendar_ids=[],
        write_calendar_id=None,  # Cố tình thiếu write_calendar_id
        granted_scopes=[],
        connection_state="connected",
        credential_ref="fernet:v1:dummy",
    )
    job = CalendarSyncJob(
        id=uuid4(),
        user_id=uid,
        connection_id=conn.id,
        target_kind="session",
        target_ref="s1",
        operation="create",
        idempotency_key="job_no_write_cal",
        job_state="queued",
        attempt_count=0,
        payload_snapshot={},
    )
    test_session.add_all([user, conn, job])
    await test_session.commit()

    # Chạy worker lần 1
    processed = await process_calendar_sync_jobs(test_session, user_id=uid, limit=10)
    assert processed == 1

    await test_session.refresh(job)
    assert job.job_state == "failed"
    assert job.attempt_count == 5  # Đã gán kịch khung
    assert job.last_error_code == "NO_WRITE_CALENDAR"

    # Chạy worker lần 2: job không được bốc lại vì job_state=='failed'
    processed_again = await process_calendar_sync_jobs(test_session, user_id=uid, limit=10)
    assert processed_again == 0


# -------------------------------------------------------------
# 5. Test ISSUE-02: Outbox Worker Xử Lý Xung Đột ETag 412
# -------------------------------------------------------------
@pytest.mark.asyncio
async def test_worker_etag_conflict_handling(test_session: AsyncSession):
    """Worker phát hiện 412 Conflict và dừng retry mù quáng."""
    uid = uuid4()
    user = User(id=uid, email="user2@example.com", hashed_password="pw", display_name="Test2", is_active=True)
    conn_id = uuid4()
    conn = CalendarConnection(
        id=conn_id,
        user_id=uid,
        provider="google",
        account_ref="user2@example.com",
        read_calendar_ids=[],
        write_calendar_id="focus_cal",
        granted_scopes=[],
        connection_state="connected",
        credential_ref=encrypt_credentials({
            "access_token": "valid_token",
            "user_id": str(uid),
            "connection_id": str(conn_id),
        }),
    )
    link = CalendarEventLink(
        id=uuid4(),
        user_id=uid,
        session_id=uuid4(),
        connection_id=conn.id,
        google_calendar_id="focus_cal",
        google_event_id="ev_conflict",
        google_etag='"old_etag"',
        desired_schedule_revision=2,
        sync_state="pending",
        is_current=True,
    )
    job = CalendarSyncJob(
        id=uuid4(),
        user_id=uid,
        connection_id=conn.id,
        event_link_id=link.id,
        target_kind="session",
        target_ref=str(link.session_id),
        operation="update",
        idempotency_key="job_etag_conflict",
        expected_google_etag='"old_etag"',
        job_state="queued",
        attempt_count=0,
        payload_snapshot={"summary": "New Title"},
    )
    test_session.add_all([user, conn, link, job])
    await test_session.commit()

    cal_service = CalendarService(test_session)

    # Giả lập Google trả về 412 Conflict
    async def mock_update_fail(*args, **kwargs):
        raise CalendarConflictError("ETag mismatch on Google Calendar")

    cal_service.client.update_event = mock_update_fail

    status = await process_sync_job(test_session, job, cal_service)
    assert status == "conflict"
    await test_session.refresh(job)
    await test_session.refresh(link)
    assert job.job_state == "conflict"
    assert link.sync_state == "conflict"
    assert job.last_error_code == "ETAG_CONFLICT_412"


# -------------------------------------------------------------
# 6. Test Toàn Trình OAuth Authorization & Callback Flow
# -------------------------------------------------------------
@pytest.mark.asyncio
async def test_oauth_authorization_and_callback_flow(test_session: AsyncSession):
    """Kiểm tra toàn trình luồng OAuth: tạo authorization, trích xuất state, exchange token và callback."""
    uid = uuid4()
    user = User(
        id=uid,
        email="user3@example.com",
        hashed_password="pw",
        display_name="Test3",
        is_active=True,
    )
    test_session.add(user)
    await test_session.commit()

    service = CalendarService(test_session)

    # 1. Khởi tạo authorization với return_target tuỳ biến cho mobile deep-link
    auth_intent, auth_url = await service.create_authorization(
        user_id=uid,
        return_target="proplan://custom_target",
    )
    assert auth_intent.return_target == "proplan://custom_target"

    # Xác nhận return_target được lưu thực tế vào DB CalendarAuthorization
    auth_rec = await test_session.get(CalendarAuthorization, auth_intent.id)
    assert auth_rec is not None
    assert auth_rec.return_target == "proplan://custom_target"
    assert auth_rec.consumed_at is None

    # 2. Trích xuất state token từ auth_url
    parsed_url = urlparse(auth_url)
    query_params = parse_qs(parsed_url.query)
    assert "state" in query_params
    state = query_params["state"][0]
    assert len(state) > 0

    # 3. Giả lập Google OAuth exchange token thành công
    async def mock_exchange(*args, **kwargs):
        return {
            "access_token": "ya29.mock_access_token",
            "refresh_token": "1//mock_refresh_token",
            "expires_in": 3600,
            "token_type": "Bearer",
            "account_ref": "user3@example.com",
            "scope": "https://www.googleapis.com/auth/calendar",
        }

    service.client.exchange_auth_code = mock_exchange

    # 4. Thực thi callback trao đổi mã code lấy token
    conn, returned_target = await service.handle_oauth_callback(
        user_id=uid,
        code="mock_auth_code_123",
        state=state,
    )

    # 5. Kiểm tra kết quả trả về
    assert returned_target == "proplan://custom_target"
    assert conn.connection_state == "connected"
    assert conn.account_ref == "user3@example.com"
    assert conn.user_id == uid

    # 6. Kiểm tra credential_ref được mã hóa Fernet Envelope có tenant binding
    assert conn.credential_ref.startswith("fernet:v1:")
    decrypted = decrypt_credentials(
        conn.credential_ref,
        expected_user_id=str(uid),
        expected_connection_id=str(conn.id),
    )
    assert decrypted["access_token"] == "ya29.mock_access_token"
    assert decrypted["refresh_token"] == "1//mock_refresh_token"

    # 7. Kiểm tra authorization đã được tiêu thụ (consumed_at được cập nhật)
    await test_session.refresh(auth_rec)
    assert auth_rec.consumed_at is not None


# -------------------------------------------------------------
# 7. Test Event Change Preview: Expiry & Idempotent Confirm
# -------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_change_preview_expiry_and_idempotent_confirm(test_session: AsyncSession):
    """Kiểm tra Endpoint 55 & 56: tạo preview, bắt lỗi quá hạn và xử lý idempotent confirm Outbox job."""
    uid = uuid4()
    conn_id = uuid4()
    user = User(id=uid, email="user4@example.com", hashed_password="pw", display_name="Test4", is_active=True)
    conn = CalendarConnection(
        id=conn_id,
        user_id=uid,
        provider="google",
        account_ref="user4@example.com",
        read_calendar_ids=["cal_primary"],
        write_calendar_id="focus_cal",
        granted_scopes=[],
        connection_state="connected",
        credential_ref=encrypt_credentials({
            "access_token": "valid_token",
            "user_id": str(uid),
            "connection_id": str(conn_id),
        }),
    )
    test_session.add_all([user, conn])
    await test_session.commit()

    current_user = CurrentUser(user_id=str(uid), email="user4@example.com")

    # ---------------------------------------------------------
    # Nhánh A: Thử nghiệm preview đã hết hạn (expires_at < now_utc)
    # ---------------------------------------------------------
    expired_preview = CalendarPreview(
        id=uuid4(),
        user_id=uid,
        connection_id=conn.id,
        kind="event_change",
        payload={
            "operation": "create",
            "connection_id": str(conn.id),
            "google_calendar_id": "focus_cal",
            "payload": {"summary": "Expired Event"},
        },
        status="pending",
        source_revisions={},
        expires_at=datetime.now(UTC) - timedelta(minutes=5),  # Đã quá hạn 5 phút
    )
    test_session.add(expired_preview)
    await test_session.commit()

    with pytest.raises(AppError) as exc_info:
        await confirm_event_change_preview(
            preview_id=expired_preview.id,
            payload=EventChangeConfirmRequest(expected_preview_revision=1),
            current_user=current_user,
            db=test_session,
        )
    assert exc_info.value.status_code == 409
    detail_lower = exc_info.value.message.lower()
    assert (
        "hết hạn" in detail_lower
        or "15" in detail_lower
        or "expired" in detail_lower
    )

    await test_session.refresh(expired_preview)
    assert expired_preview.status == "expired"

    # ---------------------------------------------------------
    # Nhánh B: Thử nghiệm preview hợp lệ và confirm sinh Outbox Sync Job
    # ---------------------------------------------------------
    preview_res = await create_event_change_preview(
        payload=EventChangePreviewRequest(
            connection_id=str(conn.id),
            operation="create",
            google_calendar_id="focus_cal",
            payload={"summary": "Valid Manual Event"},
        ),
        current_user=current_user,
        db=test_session,
    )
    valid_preview_id = UUID(preview_res.data.preview_id)

    # Xác nhận preview lần 1 (Click 1)
    confirm_res_1 = await confirm_event_change_preview(
        preview_id=valid_preview_id,
        payload=EventChangeConfirmRequest(expected_preview_revision=1),
        current_user=current_user,
        db=test_session,
    )
    job_1 = confirm_res_1.data
    assert job_1.job_state == "queued"
    assert job_1.operation == "create"

    # Kiểm tra job trong Database
    db_job_1 = (
        await test_session.scalars(
            select(CalendarSyncJob).where(
                CalendarSyncJob.idempotency_key == f"manual_event_change_{valid_preview_id}"
            )
        )
    ).one()
    assert db_job_1.job_state == "queued"
    assert db_job_1.idempotency_key == f"manual_event_change_{valid_preview_id}"
    assert db_job_1.payload_snapshot["summary"] == "Valid Manual Event"

    valid_preview = await test_session.get(CalendarPreview, valid_preview_id)
    assert valid_preview.status == "confirmed"

    # ---------------------------------------------------------
    # Nhánh C: Thử nghiệm double-click confirm lại lần 2 (Idempotency)
    # ---------------------------------------------------------
    confirm_res_2 = await confirm_event_change_preview(
        preview_id=valid_preview_id,
        payload=EventChangeConfirmRequest(expected_preview_revision=1),
        current_user=current_user,
        db=test_session,
    )
    job_2 = confirm_res_2.data
    assert job_2.job_id == job_1.job_id
    assert job_2.job_state == "queued"

    # Xác nhận không sinh job trùng lặp hay xung đột trong Database
    all_jobs = (
        await test_session.scalars(
            select(CalendarSyncJob).where(
                CalendarSyncJob.idempotency_key == f"manual_event_change_{valid_preview_id}"
            )
        )
    ).all()
    assert len(all_jobs) == 1
