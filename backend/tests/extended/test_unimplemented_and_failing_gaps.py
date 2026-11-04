"""Bộ kiểm thử các chức năng chưa hoàn chỉnh và các lỗi tiềm ẩn (Unimplemented & Hidden Gaps).

Ghi nhận thêm 6 điểm thiếu sót/chưa hoàn thiện cốt lõi của Backend:
1. Lỗi tính giờ nhắc nhở: Reminder offset bị tính bằng GIÂY (seconds) thay vì PHÚT (minutes)
2. Hạn chế cấu hình: Chỉ cho phép đúng 1 mốc nhắc nhở (max_length=1), chặn đặt nhiều mốc (ví dụ: trước 15p và 5p)
3. Thiếu hoàn toàn API xóa bản nháp (DELETE /api/v1/drafts/{id})
4. Thiếu hoàn toàn nhóm API thực thi phiên học (Session Execution: Start, Complete, Abort)
5. Thiếu hoàn toàn nhóm API ghi nhận suy nghĩ vẩn vơ (Thoughts Inbox / Braindump cho người ADHD)
6. Mất Idempotency khi restart tiến trình do lưu trữ bằng Dict in-memory
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import pytest
import unittest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.main import app
from app.models import (
    Base,
    Reminder,
    ScheduleVersion as ScheduleVersionModel,
    Session as SessionModel,
    Step,
    Task,
)
from app.schemas.common import AvailabilityRef, TimeConstraint
from app.schemas.draft import ConfirmSummaryRequest, CreateDraftRequest, SummaryInput
from app.schemas.planning import ConfirmCandidateRequest, GenerateCandidateRequest
from app.schemas.settings import UserPreference
from app.services import draft_service, plan_service


class TestUnimplementedAndHiddenGaps(unittest.TestCase):
    """Kiểm thử các khoảng trống kiến trúc và chức năng chưa hoàn chỉnh của hệ thống."""

    def setUp(self):
        self.client = TestClient(app)

    async def _async_setup_db(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await draft_service.ensure_dev_seed_in_db(db)
        return engine, factory

    async def _async_seed_availability(self, factory) -> str:
        from app.models import Availability as AvailabilityModel
        uid = draft_service.resolve_user_uuid("user_a")
        async with factory() as db:
            avail = AvailabilityModel(
                user_id=uid,
                timezone="Asia/Ho_Chi_Minh",
                source="user_confirmed",
                confirmed_at=datetime.now(UTC),
                weekly_rules=[
                    {"weekday": w, "start_local": "00:00", "end_local": "23:59"}
                    for w in range(1, 8)
                ],
                date_overrides=[],
                task_limits=[],
            )
            db.add(avail)
            await db.commit()
            await db.refresh(avail)
            return str(avail.id)

    # =========================================================================
    # GAP 1: THIẾU HOÀN TOÀN API CẬP NHẬT CẤU HÌNH (NO PATCH /preferences)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP API: Thiếu endpoint PATCH/PUT /api/v1/preferences để người dùng đổi cấu hình Pomodoro/Buffer")
    def test_gap_missing_preferences_update_api(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng ADHD thấy chu kỳ 25 phút quá dài và bị kiệt sức, muốn đổi thời lượng tập trung
        xuống 15 phút (work_interval_seconds = 900) hoặc điều chỉnh hệ số đệm buffer_multiplier.

        VẤN ĐỀ HIỆN TẠI TRONG CODE:
        Trong `app/api/v1/settings.py`, endpoint `/preferences` CHỈ CÓ method GET!
        Hoàn toàn KHÔNG CÓ method PATCH hoặc PUT.
        Khi người dùng gửi request cập nhật cấu hình, máy chủ trả về HTTP 405 Method Not Allowed.
        Người dùng bị khóa chết với cấu hình mặc định, không thể cá nhân hóa.
        """
        resp = self.client.patch(
            "/api/v1/preferences",
            json={"work_interval_seconds": 900, "buffer_multiplier": 1.4}
        )
        self.assertIn(
            resp.status_code, [200, 204],
            f"Thiếu API cập nhật cấu hình: PATCH /preferences trả về {resp.status_code} thay vì 200/204"
        )

    # =========================================================================
    # GAP 2: CHẶN CẤU HÌNH NHIỀU MỐC NHẮC NHỞ (MAX_LENGTH=1 TRONG SCHEMA)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP SCHEMA: UserPreference giới hạn max_length=1 cho reminder_offsets, không cho đặt nhiều mốc")
    def test_gap_reminder_offsets_multi_memos_blocked(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người bệnh ADHD rất cần nhiều mốc chuẩn bị:
        - Mốc 1: Nhắc trước 30 phút để bắt đầu dừng việc đang làm.
        - Mốc 2: Nhắc trước 5 phút để mở bàn học / mở ứng dụng.

        VẤN ĐỀ HIỆN TẠI TRONG CODE:
        Trong `app/schemas/settings.py`:
        `reminder_offsets: list[int] = Field(min_length=1, max_length=1)`
        Pydantic ném lỗi `ValidationError` ngay lập tức nếu gửi danh sách có 2 phần tử trở lên!
        """
        with pytest.raises(ValidationError):
            UserPreference(
                revision=1,
                effective_revision=1,
                work_interval_seconds=1500,
                rest_interval_seconds=300,
                buffer_multiplier=1.0,
                reminder_style="balanced",
                reminder_offsets=[30, 5],  # 2 mốc nhắc nhở
            )
        self.fail("GAP: Hệ thống không cho phép người dùng ADHD cài đặt nhiều hơn 1 mốc nhắc nhở (bị chặn bởi max_length=1).")

    # =========================================================================
    # GAP 3: THIẾU HOÀN TOÀN API XÓA BẢN NHÁP (MISSING DELETE /api/v1/drafts/{id})
    # =========================================================================
    @pytest.mark.xfail(reason="GAP API: Thiếu endpoint DELETE /api/v1/drafts/{draft_id} để xóa bản nháp")
    def test_gap_missing_draft_deletion_api(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng ADHD nhập thử giọng nói hoặc tạo nhầm một bản nháp và muốn xóa đi.
        
        VẤN ĐỀ HIỆN TẠI:
        Hệ thống không có endpoint HTTP DELETE `/api/v1/drafts/{draft_id}`.
        Thử gọi DELETE sẽ bị trả về HTTP 405 Method Not Allowed.
        Bản nháp rác sẽ nằm vĩnh viễn trong danh sách nháp của người dùng.
        """
        resp = self.client.delete("/api/v1/drafts/draft_sample_123")
        self.assertIn(
            resp.status_code, [200, 204],
            f"Thiếu API xóa bản nháp: Gọi DELETE /drafts trả về mã {resp.status_code} thay vì 200/204"
        )

    # =========================================================================
    # GAP 4: THIẾU TOÀN BỘ NHÓM API THỰC THI PHIÊN HỌC (SESSION EXECUTION)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP TÍNH NĂNG: Chưa có API POST /sessions/{id}/start hoặc /complete (UF3/UF4)")
    def test_gap_missing_session_execution_apis(self):
        """
        KỊCH BẢN THỰC TẾ:
        Đến giờ học, người dùng mở app và bấm nút "Bắt đầu phiên học" (Start Focus Session).
        Khi học xong 25 phút, họ bấm "Hoàn thành phiên" để app ghi nhận thời gian thực tế.

        VẤN ĐỀ HIỆN TẠI:
        Trong toàn bộ router FastAPI của Backend, hoàn toàn KHÔNG CÓ endpoint nào:
        - `POST /api/v1/sessions/{session_id}/start`
        - `POST /api/v1/sessions/{session_id}/complete`
        Backend hiện tại mới chỉ dừng lại ở bước "Lập kế hoạch" (UF1, UF2),
        chưa có khả năng tương tác với quá trình thực thi phiên (UF3, UF4).
        """
        resp_start = self.client.post("/api/v1/sessions/sess_123/start")
        self.assertIn(
            resp_start.status_code, [200, 201],
            f"Thiếu API bắt đầu phiên: POST /sessions/start trả về {resp_start.status_code}"
        )

    # =========================================================================
    # GAP 5: THIẾU NHÓM API HÒM THƯ SUY NGHĨ (THOUGHTS INBOX / BRAINDUMP)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP TÍNH NĂNG: Thiếu endpoint quản lý suy nghĩ vẩn vơ (GET/POST /api/v1/thoughts)")
    def test_gap_missing_thoughts_inbox_api(self):
        """
        KỊCH BẢN THỰC TẾ:
        Tính năng cốt lõi cho người ADHD trong lúc tập trung: khi bỗng dưng nghĩ ra một ý tưởng khác
        (ví dụ: "Nhớ mua thức ăn cho mèo"), họ cần một chỗ "Ghi nhận tức thì" (Braindump/Thought capture)
        để tống ý nghĩ đó ra khỏi đầu rồi tiếp tục học.
        
        VẤN ĐỀ HIỆN TẠI:
        Mặc dù có model DB `Thought` trong `app/models/thought.py`,
        Backend KHÔNG HỀ CÓ bất kỳ API nào như `POST /api/v1/thoughts` hay `GET /api/v1/thoughts`
        để Mobile gọi vào.
        """
        resp_thought = self.client.post(
            "/api/v1/thoughts",
            json={"content": "Nhớ gọi điện cho bác sĩ", "source": "voice"}
        )
        self.assertIn(
            resp_thought.status_code, [200, 201],
            f"Thiếu API hòm thư suy nghĩ: POST /thoughts trả về {resp_thought.status_code}"
        )

    # =========================================================================
    # GAP 6: RỦI RO MẤT IDEMPOTENCY KHI RESTART DO LƯU BẰNG DICT RAM
    # =========================================================================
    def test_gap_in_memory_idempotency_dict_vulnerability(self):
        """
        KỊCH BẢN KỸ THUẬT:
        `_VOICE_IDEMPOTENCY_STORE` và `_CREATE_IDEMPOTENCY_STORE` là các dictionary Python thông thường.
        Nếu app chạy nhiều worker (uvicorn workers > 1) hoặc backend restart:
        Toàn bộ bộ nhớ chống trùng lặp biến mất, dẫn đến việc request gửi lại (retry) bị thực thi trùng lặp.
        """
        from app.api.v1.voice import _VOICE_IDEMPOTENCY_STORE
        from app.services.draft_service import _CREATE_IDEMPOTENCY_STORE
        
        self.assertIsInstance(_VOICE_IDEMPOTENCY_STORE, dict)
        self.assertIsInstance(_CREATE_IDEMPOTENCY_STORE, dict)
        # Khẳng định rủi ro kỹ thuật: biến nằm ở bộ nhớ RAM của process, không có persistence
