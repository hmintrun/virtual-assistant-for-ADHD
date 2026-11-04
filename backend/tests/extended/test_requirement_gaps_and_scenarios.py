"""Bộ kiểm thử các khoảng trống yêu cầu (Requirement Gaps & ADHD Edge Scenarios).

Tệp này tập trung vào các trường hợp nghiệp vụ mà hệ thống HIỆN TẠI CHƯA ĐẠT
hoặc CÓ SỰ KHÁC BIỆT so với tài liệu thiết kế nghiệp vụ (AlgorithmDes.pdf & PRD ADHD).

Mỗi test case mô tả:
1. Kịch bản thực tế của người dùng ADHD.
2. Hành vi hiện tại của Backend (Hiện tại gây lỗi gì hoặc bỏ sót gì).
3. Hành vi kỳ vọng chuẩn (Expected behavior).
4. Hệ quả tâm lý/trải nghiệm nếu không được xử lý.

Các test này được đánh dấu @pytest.mark.xfail(strict=False) để ghi nhận chính thức
danh sách Gap cần cải tiến mà không làm gãy quy trình CI/CD.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
import pytest
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.core.errors import AppError
from app.models import Availability as AvailabilityModel, Base
from app.schemas.common import AvailabilityRef, SessionSegment, TimeConstraint, TimeInterval
from app.schemas.draft import (
    ConfirmSummaryRequest,
    CreateDraftRequest,
    SummaryInput,
)
from app.schemas.planning import (
    CandidatePart,
    CandidateStep,
    ConfirmCandidateRequest,
    GenerateCandidateRequest,
)
from app.schemas.settings import UserPreference
from app.schemas.task import Session
from app.services import draft_service, plan_service
from app.services.planning.durations import build_session_segments
from app.services.planning.rescheduling import shift_elapsed_sessions
from app.services.planning.scheduling import schedule_candidate_sessions


VN_TZ = timezone(timedelta(hours=7))


class TestRequirementGapsAndADHDScenarios(unittest.TestCase):
    """Kiểm thử các bài toán đặc thù ADHD chưa được đáp ứng trọn vẹn trong mã nguồn hiện tại."""

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
        uid = draft_service.resolve_user_uuid("user_a")
        async with factory() as db:
            avail = AvailabilityModel(
                user_id=uid,
                timezone="Asia/Ho_Chi_Minh",
                source="user_confirmed",
                confirmed_at=datetime.now(UTC),
                weekly_rules=[
                    {"weekday": w, "start_local": "08:00", "end_local": "18:00"}
                    for w in range(1, 8)
                ],
                date_overrides=[],
                task_limits=[
                    {"weekday": 1, "max_tasks": 1}  # Thứ 2 chỉ tối đa 1 task!
                ],
            )
            db.add(avail)
            await db.commit()
            await db.refresh(avail)
            return str(avail.id)

    # =========================================================================
    # KỊCH BẢN 1: TẠO LỊCH NHÁP KHI THIẾU THÔNG TIN (TIME BLINDNESS DRAFT)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP: Backend chặn tạo lịch nháp khi thiếu thông tin (trái với ghi chú của Khái trong PDF)")
    def test_gap_1_draft_candidate_when_missing_deadline(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng ADHD bị mù thời gian (Time Blindness), chỉ nói: "Muốn viết bài luận môn Triết học 15 trang".
        Họ chưa biết deadline là khi nào và muốn hệ thống đề xuất một "lịch nháp giả định" để họ nhìn vào đó
        và ước lượng xem sẽ mất bao nhiêu buổi học trong tuần tới.

        HÀNH VI HIỆN TẠI (CHƯA ĐÚNG YÊU CẦU):
        Hệ thống yêu cầu phải có `time_constraint` với `deadline_at` và `availability_ref`.
        Khi người dùng cố gắng chuyển bước hoặc xác nhận, hệ thống ném HTTP 422 VALIDATION_ERROR
        và chặn đứng không cho tạo Candidate Plan.

        KỲ VỌNG THIẾT KẾ (PDF UF1/UF2):
        Hệ thống vẫn cho phép tạo Candidate dạng nháp (Draft Candidate) vào các khung rảnh gần nhất,
        kèm trạng thái `provisional = True` và đặt câu hỏi gợi ý: "Bạn dự định nộp bài trước ngày nào?".
        """
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                # Tạo draft không có deadline
                payload = CreateDraftRequest(
                    client_draft_id="gap_1_draft",
                    input_source="text",
                    raw_input="Viết bài luận môn Triết học 15 trang",
                    summary=SummaryInput(
                        title="Bài luận Triết học",
                        description="Viết 15 trang",
                        scope="15 trang",
                        time_constraint=None,  # Chưa có deadline!
                    ),
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(
                        user_id="user_a", idempotency_key="gap_1_k1", payload=payload, db=db
                    )
                    # Kỳ vọng: Cho phép sinh lịch nháp tạm thời
                    # Thực tế: Gọi create_candidate sẽ ném lỗi SOURCE_CHANGED hoặc PRECONDITION_FAILED
                    cand = await plan_service.create_candidate(
                        user_id="user_a",
                        draft_id=draft.draft_id,
                        idempotency_key="gap_1_k2",
                        payload=GenerateCandidateRequest(
                            source_summary_revision=draft.summary_revision,
                            source_planning_input_revision=draft.planning_input_revision,
                        ),
                        db=db,
                    )
                    # Nếu đáp ứng yêu cầu PDF:
                    self.assertTrue(cand.is_provisional, "Kế hoạch phải được đánh dấu là lịch nháp tạm thời (provisional).")
            finally:
                await engine.dispose()

        asyncio.run(_run())

    # =========================================================================
    # KỊCH BẢN 2: BỎ QUA GIỚI HẠN SỐ LƯỢNG TASK TRONG NGÀY (TASK_LIMITS IGNORING)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP: Thuật toán xếp lịch schedule_candidate_sessions chưa áp dụng task_limits")
    def test_gap_2_task_limits_per_day_ignored_by_scheduler(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng ADHD cấu hình trong Availability: "Thứ 2 chỉ làm tối đa 1 task" (task_limits: max_tasks=1)
        để tránh tình trạng bị ngợp (Sensory/Cognitive Overload).
        Đã có Task 1 được chốt vào Thứ 2. Giờ đây người dùng lên lịch cho Task 2.

        HÀNH VI HIỆN TẠI (CHƯA ĐÚNG YÊU CẦU):
        Hàm `schedule_candidate_sessions` chỉ nhận `effective_intervals` và `fixed_sessions`,
        hoàn toàn KHÔNG NHẬN và KHÔNG KIỂM TRA danh sách `task_limits`.
        Vì ngày Thứ 2 vẫn còn khoảng trống rảnh rỗi giữa các giờ, hệ thống vẫn vô tư nhét tiếp Task 2 vào Thứ 2!

        KỲ VỌNG THIẾT KẾ:
        Thuật toán phải kiểm tra số lượng task trong ngày. Khi thấy Thứ 2 đã có 1 task,
        nó phải bỏ qua các khe rảnh của Thứ 2 và đẩy Task 2 sang ngày Thứ 3.
        """
        t_monday_start = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # Thứ 2
        free_slot_monday = TimeInterval(start_at=t_monday_start, end_at=t_monday_start + timedelta(hours=8))
        
        tc = TimeConstraint(
            kind="deadline",
            start_at=t_monday_start,
            deadline_at=t_monday_start + timedelta(days=5),
            input_timezone="UTC",
        )
        pref = UserPreference(
            revision=1,
            effective_revision=1,
            work_interval_seconds=1800,
            rest_interval_seconds=300,
            buffer_multiplier=1.0,
            reminder_offsets=[15],
            reminder_style="balanced",
        )
        parts = [CandidatePart(client_part_key="p1", scope_description="P1", order_index=0, decomposition_state="complete", scheduling_state="not_scheduled")]
        step = CandidateStep(
            client_step_key="s1", client_part_key="p1", title="Task mới", expected_output="Done",
            order_index=0, estimated_base_seconds=1800, remaining_base_seconds=1800,
            buffer_multiplier=1.0, estimate_source="user_confirmed", status="pending"
        )

        # Đã có 1 session của task khác vào ngày Thứ 2
        existing_task1_session = Session(
            session_id="sess_task1",
            step_id="step_task1",
            scheduled_start=t_monday_start,
            scheduled_end=t_monday_start + timedelta(hours=1),
            duration_seconds=3600,
            work_seconds=3600,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            micro_task_text="Bắt đầu",
            schedule_revision=1,
            status="scheduled",
            start_state="waiting",
            start_decision_due_at=t_monday_start,
        )

        sessions, report = schedule_candidate_sessions(
            steps=[step],
            effective_intervals=[free_slot_monday],
            fixed_sessions=[existing_task1_session],
            time_constraint=tc,
            pref=pref,
            planning_parts=parts,
        )

        # Hiện tại: sessions[0] vẫn bị xếp vào Thứ 2 (t_monday_start + 1h + gap)
        # Kỳ vọng nếu tuân thủ task_limits=1: Không được xếp thêm task vào ngày Thứ 2 này!
        session_day = sessions[0].scheduled_start.date()
        monday_date = t_monday_start.date()
        
        self.assertNotEqual(
            session_day, monday_date,
            "LỖI NGHIỆP VỤ: Thứ 2 đã có 1 task và cấu hình max_tasks=1, nhưng scheduler vẫn xếp tiếp task thứ 2 vào Thứ 2!"
        )

    # =========================================================================
    # KỊCH BẢN 3: SỰ CHẦN CHỪ LÀM TRÔI PHIÊN VÀO QUÁ KHỨ (STALE CANDIDATE PAST_SESSION)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP: Backend ném lỗi PLAN_NOT_CONFIRMABLE khi người dùng chần chừ thay vì tự động trượt lịch")
    def test_gap_3_past_session_stale_candidate_rejection(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng ADHD bị tê liệt quyết định (Decision Paralysis / Hesitation).
        Candidate được tạo lúc 08:50, đề xuất phiên đầu tiên lúc 09:00.
        Người dùng ngồi xem, phân vân, mất tập trung đọc tin nhắn đến 09:05 mới bấm "Chốt kế hoạch".

        HÀNH VI HIỆN TẠI (CHƯA ĐÚNG YÊU CẦU):
        Hàm `confirm_candidate` kiểm tra thấy phiên đầu tiên (09:00) <= thời điểm hiện tại (09:05),
        lập tức gắn cờ lỗi `PAST_SESSION` và NÉM NGOẠI LỆ `PLAN_NOT_CONFIRMABLE`.
        Người dùng bị chặn đứng, toàn bộ phương án bị hủy và bắt phải tạo candidate lại từ đầu,
        gây nản lòng và bỏ cuộc (Drop-off).

        KỲ VỌNG THIẾT KẾ:
        Thay vì quăng lỗi thô bạo vào mặt người dùng, hệ thống nên có cơ chế tự động trượt (Auto-slide)
        phiên bị trễ về mốc `now + gap` hoặc gợi ý 1 nút bấm: "Cập nhật giờ theo hiện tại" để chốt ngay.
        """
        async def _run():
            engine, factory = await self._async_setup_db()
            try:
                avail_id = await self._async_seed_availability(factory)
                
                # Tạo draft có start_at cách đây 1 phút (mô phỏng chần chừ)
                t_start = datetime.now(UTC) - timedelta(minutes=5)
                t_deadline = t_start + timedelta(days=2)

                payload = CreateDraftRequest(
                    client_draft_id="gap_3_draft",
                    input_source="text",
                    raw_input="Làm báo cáo",
                    summary=SummaryInput(
                        title="Báo cáo", description="Viết báo cáo", scope="5 trang",
                        time_constraint=TimeConstraint(
                            kind="deadline", start_at=t_start, deadline_at=t_deadline, input_timezone="UTC"
                        ),
                        availability_ref=AvailabilityRef(availability_id=avail_id, revision=1),
                    )
                )
                async with factory() as db:
                    draft = await draft_service.create_draft(user_id="user_a", idempotency_key="gap_3_k1", payload=payload, db=db)
                    await draft_service.confirm_draft_summary(user_id="user_a", draft_id=draft.draft_id, idempotency_key="gap_3_k2", payload=ConfirmSummaryRequest(expected_summary_revision=draft.summary_revision), db=db)
                    
                    cand = await plan_service.create_candidate(
                        user_id="user_a", draft_id=draft.draft_id, idempotency_key="gap_3_k3",
                        payload=GenerateCandidateRequest(source_summary_revision=draft.summary_revision, source_planning_input_revision=draft.planning_input_revision),
                        db=db,
                    )
                    
                    # Giả sử candidate có phiên bắt đầu trong quá khứ do chần chừ
                    # Khi confirm, ta kỳ vọng hệ thống hỗ trợ auto-slide thay vì ném lỗi AppError
                    res = await plan_service.confirm_candidate(
                        user_id="user_a", candidate_id=cand.candidate_id, idempotency_key="gap_3_k4",
                        payload=ConfirmCandidateRequest(
                            expected_review_revision=cand.review_revision,
                            expected_summary_revision=cand.source_summary_revision,
                            expected_planning_input_revision=cand.source_planning_input_revision,
                            expected_source_revisions=cand.source_revisions,
                        ),
                        db=db,
                    )
                    self.assertEqual(res.app_save_state, "saved")
            finally:
                await engine.dispose()

        asyncio.run(_run())

    # =========================================================================
    # KỊCH BẢN 4: TRÁNH NÉ MÃN TÍNH (CHRONIC TASK AVOIDANCE ESCALATION)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP: Chưa có cơ chế can thiệp tâm lý khi avoidance_count >= 3")
    def test_gap_4_chronic_task_avoidance_escalation(self):
        r"""
        KỊCH BẢN THỰC TẾ:
        Một người dùng ADHD đã hoãn (reschedule) một task quá 3 lần liên tiếp.
        Cột `avoidance_count` đã tăng lên $\ge 3$. Đây là biểu hiện kinh điển của chứng "Sợ việc / Tê liệt hành động"
        (Task Paralysis) do bước công việc còn quá mơ hồ hoặc quá lớn.

        HÀNH VI HIỆN TẠI (CHƯA ĐÚNG YÊU CẦU):
        Hàm `shift_elapsed_sessions` chỉ đơn thuần tịnh tiến giờ bắt đầu sang tương lai và tăng biến đếm.
        Nó tiếp tục lặp lại vô tận mà không hề có cảnh báo hay gợi ý thay đổi chiến lược.

        KỲ VỌNG THIẾT KẾ:
        Khi `avoidance_count >= 3`, hệ thống phải:
        1. Bật cờ cảnh báo `requires_task_breakdown_intervention = True`.
        2. Tự động kích hoạt luồng đề xuất phân rã lại (Re-decomposition) bước đó thành các bước nhỏ hơn một nửa.
        3. Rút ngắn Micro-task xuống còn 1 phút (ví dụ: "Chỉ cần mở file Word lên và viết 1 dòng").
        """
        now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        s1 = Session(
            session_id="sess_avoided",
            step_id="step_hard",
            scheduled_start=datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
            scheduled_end=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
            duration_seconds=3600,
            work_seconds=3600,
            rest_seconds=0,
            segments=[SessionSegment(order_index=0, kind="work", duration_seconds=3600)],
            micro_task_text="Khởi động",
            schedule_revision=3,
            status="incomplete",
            start_state="waiting",
            start_decision_due_at=datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
        )

        future_free = [TimeInterval(start_at=datetime(2026, 11, 2, 11, 0, tzinfo=UTC), end_at=datetime(2026, 11, 2, 18, 0, tzinfo=UTC))]

        moved, affected, all_shifted, deficit = shift_elapsed_sessions(
            sessions=[s1],
            allowed_task_ids=["task_hard"],
            effective_intervals=future_free,
            real_time_anchor=now,
            step_to_task_map={"step_hard": "task_hard"},
        )

        # Giả lập kiểm tra can thiệp tâm lý khi hoãn quá nhiều lần:
        # Kỳ vọng: Trả về tín hiệu can thiệp thay vì chỉ dời lịch âm thầm
        has_intervention = any("breakdown_recommendation" in item for item in moved)
        self.assertTrue(
            has_intervention,
            "LỖI THIẾT KẾ ADHD: Task đã bị tránh né 3 lần nhưng hệ thống không kích hoạt gợi ý chia nhỏ lại bước!"
        )

    # =========================================================================
    # KỊCH BẢN 5: CHƯA HỖ TRỢ CHẶNG TIẾP THEO (ROLLING HORIZON CONTINUATION)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP: Backend chưa có API/Service nào xử lý Rolling Horizon chặng tiếp theo")
    def test_gap_5_rolling_horizon_continuation_missing(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng có một dự án lớn (Khóa luận tốt nghiệp kéo dài 2 tháng).
        Hệ thống chỉ xếp chi tiết Chặng 1 trong 14 ngày đầu tiên.
        Khi người dùng đã hoàn thành các buổi của Chặng 1, họ muốn app tiếp tục nạp Chặng 2 vào lịch rảnh.

        HÀNH VI HIỆN TẠI (CHƯA ĐÚNG YÊU CẦU):
        Trong `plan_service`, chỉ có `create_candidate` và `confirm_candidate`.
        Hoàn toàn không có hàm nào để "tiếp tục chặng sau" (chưa có `continue_rolling_horizon` hay `propose_next_chunk`).

        KỲ VỌNG THIẾT KẾ (PDF UF2 - Trang 4):
        "Khi chuẩn bị sang chặng tiếp theo, hệ thống dùng phần đã làm, phần còn lại,
        thông tin bổ sung và thời gian rảnh mới để đề xuất lịch chi tiết."
        """
        has_rolling_service = (
            hasattr(plan_service, "continue_rolling_horizon") or
            hasattr(plan_service, "propose_next_chunk") or
            hasattr(plan_service, "schedule_next_horizon")
        )
        self.assertTrue(
            has_rolling_service,
            "LỖI TÍNH NĂNG: Thiếu service hỗ trợ Rolling Horizon cho các công việc dài hạn."
        )

    # =========================================================================
    # KỊCH BẢN 6: CẤU HÌNH NGHỈ BẰNG 0 DẪN TỚI KIỆT SỨC (BURNOUT RISK)
    # =========================================================================
    @pytest.mark.xfail(reason="GAP: Hệ thống cho phép rest_interval_seconds=0 gây kiệt sức liên tục cho người ADHD")
    def test_gap_6_zero_rest_burnout_prevention(self):
        """
        KỊCH BẢN THỰC TẾ:
        Người dùng cấu hình `rest_interval_seconds = 0` vì muốn "làm một lèo cho xong".
        Công việc dài 4 tiếng (14,400 giây).

        HÀNH VI HIỆN TẠI (CHƯA ĐÚNG YÊU CẦU):
        Hàm `build_session_segments` vui vẻ tạo ra một khối công việc liên tục 4 tiếng không có một giây giải lao nào.
        Điều này đi ngược lại hoàn toàn hướng dẫn Cognitive and Learning Disabilities (COGA) của W3C
        và nguyên lý cốt lõi của ứng dụng hỗ trợ ADHD (người ADHD khi hyperfocus mà không nghỉ sẽ kiệt sức nặng nề sau đó).

        KỲ VỌNG THIẾT KẾ:
        Hệ thống phải có chốt chặn an toàn (Guardrail): Nếu phiên làm việc dự kiến vượt quá 90 phút (5400s),
        hệ thống bắt buộc phải chèn khoảng nghỉ tối thiểu 5-10 phút hoặc cảnh báo nguy cơ kiệt sức.
        """
        segments, work, rest, duration = build_session_segments(
            buffered_work_seconds=14400,  # 4 tiếng
            work_interval_seconds=1500,   # 25 phút
            rest_interval_seconds=0,      # 0 phút nghỉ
        )

        # Hiện tại: rest == 0
        # Kỳ vọng bảo vệ sức khỏe tâm thần: Bắt buộc phải có thời gian nghỉ khi tổng thời gian làm việc > 2 tiếng
        self.assertGreater(
            rest, 0,
            "LỖI NGUYÊN LÝ ADHD: Công việc dài 4 tiếng nhưng hệ thống cho phép 0 giây nghỉ ngơi, gây nguy cơ kiệt sức (Burnout)!"
        )
