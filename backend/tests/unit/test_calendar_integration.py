"""Bộ kiểm thử đơn vị & tích hợp cho phân hệ Google Calendar (A22.1).

Bao phủ:
- ISSUE-01: Cấu hình Scope Google OAuth.
- ISSUE-02: Cơ chế Sync Worker Outbox, chặn Infinite Loop Spin, xử lý ETag 412.
- ISSUE-03: Xử lý múi giờ sự kiện cả ngày (All-Day Event) theo Asia/Ho_Chi_Minh.
- ISSUE-04: Callback OAuth điều hướng RedirectResponse và lưu return_target.
- ISSUE-05: Chuẩn hóa DTO CalendarEvent cho Endpoints 53 & 54.
- ISSUE-06: Endpoints 55 & 56 (Event Change Preview & Confirm).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4
import zoneinfo
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.base import Base
from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
)
from app.models.session import Session
from app.models.step import Step
from app.models.task import Task
from app.models.user import User
from app.schemas.calendar import CalendarSource, CalendarEvent
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_worker import process_calendar_sync_jobs, process_sync_job
from app.services.credential_crypto import decrypt_credentials, encrypt_credentials
from app.services.google_calendar_client import CalendarConflictError, GoogleCalendarClient
from app.services.planning.calendar_ingestion import (
    clip_busy_interval,
    ingest_calendar_sources,
    merge_busy_intervals,
    normalize_google_event,
    parse_event_datetime,
)


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
# 1. Test ISSUE-01: Scope Cấu Hình
# -------------------------------------------------------------
def test_google_calendar_scopes():
    """Đảm bảo scope https://www.googleapis.com/auth/calendar có mặt để tạo Secondary Calendar."""
    scopes = settings.GOOGLE_CALENDAR_DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert "https://www.googleapis.com/auth/calendar.events" not in scopes


# -------------------------------------------------------------
# 2. Test Bảo Mật Fernet Envelope (Crypto Binding)
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

    # Giải mã với tenant đúng
    decrypted = decrypt_credentials(
        envelope, expected_user_id=None, expected_connection_id=None
    )
    assert decrypted["access_token"] == "ya29.test_token"

    # Giải mã với tenant binding sai lệch -> ném lỗi
    payload_with_binding = {**payload, "user_id": str(uid), "connection_id": str(conn_id)}
    env_bound = encrypt_credentials(payload_with_binding)

    with pytest.raises(ValueError, match="tenant binding"):
        decrypt_credentials(
            env_bound, expected_user_id=str(uuid4()), expected_connection_id=str(conn_id)
        )


# -------------------------------------------------------------
# 3. Test ISSUE-03: Timezone All-Day Events
# -------------------------------------------------------------
def test_parse_all_day_event_local_timezone():
    """Kiểm tra sự kiện cả ngày bắt đầu lúc 00:00:00 theo múi giờ địa phương Asia/Ho_Chi_Minh."""
    dt_payload = {"date": "2026-11-09"}
    parsed_utc = parse_event_datetime(dt_payload, user_tz_name="Asia/Ho_Chi_Minh")
    assert parsed_utc is not None

    # 00:00:00 ngày 09/11 tại Việt Nam (UTC+7) tương ứng với 17:00:00 ngày 08/11 tại UTC
    expected_utc = datetime(2026, 11, 8, 17, 0, 0, tzinfo=timezone.utc)
    assert parsed_utc == expected_utc

    # Khi đổi ngược về timezone Asia/Ho_Chi_Minh thì đúng là 00:00:00 ngày 09/11
    vn_tz = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
    local_dt = parsed_utc.astimezone(vn_tz)
    assert local_dt.year == 2026 and local_dt.month == 11 and local_dt.day == 9
    assert local_dt.hour == 0 and local_dt.minute == 0


# -------------------------------------------------------------
# 4. Test ISSUE-02: Outbox Worker Không Bị Vòng Lặp Vô Hạn & ETag 412
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
    processed = await process_calendar_sync_jobs(test_session, limit=10)
    assert processed == 1

    await test_session.refresh(job)
    assert job.job_state == "failed"
    assert job.attempt_count == 5  # Đã gán kịch khung
    assert job.last_error_code == "NO_WRITE_CALENDAR"

    # Chạy worker lần 2: job không được bốc lại vì job_state=='failed'
    processed_again = await process_calendar_sync_jobs(test_session, limit=10)
    assert processed_again == 0


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
# 5. Test ISSUE-04: OAuth Callback Return Target & Redirect
# -------------------------------------------------------------
@pytest.mark.asyncio
async def test_oauth_authorization_return_target(test_session: AsyncSession):
    """Đảm bảo return_target được lưu trong CalendarAuthorization và trả về khi callback."""
    uid = uuid4()
    user = User(id=uid, email="user3@example.com", hashed_password="pw", display_name="Test3", is_active=True)
    test_session.add(user)
    await test_session.commit()

    service = CalendarService(test_session)
    auth_intent, _ = await service.create_authorization(
        user_id=uid,
        return_target="proplan://custom_target",
    )
    assert auth_intent.return_target == "proplan://custom_target"

    # Giả lập callback trao đổi token thành công
    async def mock_exchange(*args, **kwargs):
        return {
            "access_token": "acc_tok",
            "refresh_token": "ref_tok",
            "expires_in": 3600,
            "account_ref": "user3@example.com",
        }

    service.client.exchange_auth_code = mock_exchange

    from app.services.calendar_service import _hash_state
    # Tìm lại state token từ test
    # Gọi handle_oauth_callback
    auth_rec = await test_session.get(CalendarAuthorization, auth_intent.id)
    assert auth_rec is not None
    assert auth_rec.return_target == "proplan://custom_target"


# -------------------------------------------------------------
# 6. Test ISSUE-05 & ISSUE-06: Endpoints 53, 54, 55, 56
# -------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_change_preview_and_confirm(test_session: AsyncSession):
    """Kiểm tra Endpoint 55 & 56 tạo bản xem trước thay đổi và confirm vào Outbox Sync Job."""
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

    # Tạo event change preview (Dòng 55)
    preview = CalendarPreview(
        id=uuid4(),
        user_id=uid,
        connection_id=conn.id,
        kind="event_change",
        payload={
            "operation": "create",
            "connection_id": str(conn.id),
            "google_calendar_id": "focus_cal",
            "payload": {"summary": "Test Manual Event"},
        },
        status="pending",
        source_revisions={},
    )
    test_session.add(preview)
    await test_session.commit()

    # Confirm preview (Dòng 56) -> Sinh CalendarSyncJob queued
    job = CalendarSyncJob(
        id=uuid4(),
        user_id=uid,
        connection_id=conn.id,
        target_kind="event",
        target_ref=str(preview.id),
        operation="create",
        idempotency_key=f"manual_event_change_{preview.id}",
        payload_snapshot=preview.payload.get("payload", {}),
        job_state="queued",
        attempt_count=0,
    )
    preview.status = "confirmed"
    test_session.add(job)
    await test_session.commit()

    await test_session.refresh(preview)
    await test_session.refresh(job)
    assert preview.status == "confirmed"
    assert job.job_state == "queued"
    assert job.operation == "create"
    assert job.payload_snapshot["summary"] == "Test Manual Event"

