"""Bộ kiểm thử toàn diện cho BE-UF6-01 .. BE-UF6-04 (Cycle 2 UF6 Upgrade)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, make_engine, session_factory
from app.core.errors import AppError
from app.main import app
from app.models import Base
from app.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.execution import CheckIn, SessionRun
from app.models.planning import PlanningDraft
from app.models.planning import ScheduleVersion as ScheduleVersionRow
from app.models.session import Session as SessionRow
from app.models.settings import AdaptationSuggestion
from app.models.settings import Availability as AvailabilityRow
from app.models.step import Step as StepRow
from app.models.system import AuditEvent
from app.models.task import Task as TaskRow
from app.models.user import User
from app.models.user import UserPreference as UserPreferenceRow
from app.schemas.availability import DraftAvailability, WeeklyRule
from app.schemas.draft import ConfirmSummaryRequest, CreateDraftRequest, SummaryInput
from app.schemas.execution import ActualCorrectionRequest, CheckInResponseRequest
from app.schemas.planning import GenerateCandidateRequest, ManualStepInput
from app.schemas.reschedule import ApproveProposalRequest, CreateProposalRequest
from app.schemas.schedule import RescheduleEmpatheticResult
from app.schemas.settings import SettingsChangeRequest, SuggestionResponseRequest
from app.schemas.task import TimeConstraint
from app.schemas.user import CurrentUser
from app.services import (
    auth_service,
    checkin_service,
    command_service,
    draft_service,
    plan_service,
    reschedule_service,
    run_service,
    settings_service,
    streak_service,
)
from app.services.credential_crypto import encrypt_credentials
from app.services.google_calendar_client import (
    FakeCalendarClient,
    get_calendar_client,
    reset_fake_calendar_client,
)

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _auth_headers(user_id: UUID, idempotency_key: str | None = None) -> dict[str, str]:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=str(user_id),
        email=f"uf6_{user_id.hex[:8]}@example.com",
        display_name="UF6 Tester",
        is_active=True,
    )
    headers = {"Authorization": f"Bearer test-{user_id.hex[:8]}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _make_test_env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    sf = session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return engine, sf


async def _seed_user_task_session(
    db: AsyncSession,
    *,
    now: datetime,
    timezone_name: str = "Asia/Ho_Chi_Minh",
    with_future_session: bool = True,
    streak_days: int = 0,
    streak_frozen: bool = False,
    freeze_tokens: int = 2,
    last_streak_date=None,
) -> dict[str, UUID]:
    uid = uuid4()
    draft_id = uuid4()
    task_id = uuid4()
    step_id = uuid4()
    session_id = uuid4()

    db.add(
        User(
            id=uid,
            email=f"u_{uid.hex[:8]}@example.com",
            hashed_password="pw",
            display_name="UF6 Tester",
            current_streak_days=streak_days,
            streak_frozen=streak_frozen,
            streak_freeze_tokens=freeze_tokens,
            last_streak_date=last_streak_date,
        )
    )
    await db.flush()
    db.add(
        UserPreferenceRow(
            user_id=uid,
            revision=1,
            effective_revision=1,
            work_interval_seconds=1500,
            rest_interval_seconds=300,
            micro_task_seconds=120,
            reminder_offsets=[900, 600, 300],
            buffer_multiplier=1.2,
            reminder_style="gentle",
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        AvailabilityRow(
            user_id=uid,
            revision=1,
            timezone=timezone_name,
            weekly_rules=[
                {"weekday": w, "start_local": "08:00", "end_local": "23:00"}
                for w in range(1, 8)
            ],
            date_overrides=[],
            task_limits=[],
            source="manual_input",
            confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    summary_payload = {
        "title": "Test Task UF6",
        "description": "Mô tả chi tiết",
        "goal": "Mục tiêu",
        "scope": "Phạm vi",
        "extra_requirements": [],
        "time_constraint": {
            "kind": "deadline",
            "start_at": (now - timedelta(days=1)).isoformat(),
            "deadline_at": (now + timedelta(days=14)).isoformat(),
            "input_timezone": timezone_name,
        },
        "availability": {
            "timezone": timezone_name,
            "weekly_rules": [
                {"weekday": w, "start_local": "08:00", "end_local": "23:00"}
                for w in range(1, 8)
            ],
            "date_overrides": [],
        },
        "clarification_issues": [],
        "can_plan": True,
        "field_sources": {},
    }
    db.add(
        PlanningDraft(
            id=draft_id,
            user_id=uid,
            client_draft_id=f"cd_{draft_id.hex[:8]}",
            stage="confirmed",
            raw_input="Test Task UF6",
            input_source="text",
            summary=summary_payload,
            summary_revision=1,
            planning_input_revision=1,
        )
    )
    db.add(
        TaskRow(
            id=task_id,
            user_id=uid,
            title="Test Task UF6",
            description="Mô tả chi tiết",
            scope="Phạm vi",
            extra_requirements=[],
            time_constraint=summary_payload["time_constraint"],
            status="scheduled",
            source_draft_id=draft_id,
            source_summary=summary_payload,
            source_summary_revision=1,
            source_planning_input_revision=1,
            availability=summary_payload["availability"],
            revision=1,
        )
    )
    db.add(
        StepRow(
            id=step_id,
            user_id=uid,
            task_id=task_id,
            title="Bước 1",
            expected_output="Xong bước 1",
            order_index=0,
            estimated_base_seconds=1500,
            remaining_base_seconds=1500,
            buffer_multiplier=1.2,
            estimate_source="user",
            status="scheduled",
        )
    )
    db.add(
        ScheduleVersionRow(
            user_id=uid,
            version_number=1,
            base_version=0,
            approved_at=now,
            affected_task_ids=[str(task_id)],
            min_inter_session_gap_seconds=900,
        )
    )
    if with_future_session:
        start = now + timedelta(days=1, hours=2)
        end = start + timedelta(minutes=30)
        db.add(
            SessionRow(
                id=session_id,
                user_id=uid,
                step_id=step_id,
                scheduled_start=start,
                scheduled_end=end,
                micro_task_text="Mở tài liệu",
                work_seconds=1500,
                rest_seconds=300,
                segments=[
                    {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                ],
                duration_seconds=1800,
                schedule_revision=1,
                status="scheduled",
                start_state="waiting",
                start_decision_due_at=start + timedelta(minutes=5),
            )
        )
    await db.commit()
    return {
        "user_id": uid,
        "draft_id": draft_id,
        "task_id": task_id,
        "step_id": step_id,
        "session_id": session_id,
    }


async def _seed_completed_run_and_checkin(
    db: AsyncSession,
    *,
    user_id: UUID,
    step_id: UUID,
    ended_at: datetime,
    actual_seconds: int = 1500,
    outcome: str = "completed",
    answer_checkin: bool = True,
    idempotency_prefix: str = "run",
) -> tuple[UUID, UUID, UUID]:
    sched_start = ended_at - timedelta(minutes=30)
    sess = SessionRow(
        id=uuid4(),
        user_id=user_id,
        step_id=step_id,
        scheduled_start=sched_start,
        scheduled_end=ended_at,
        micro_task_text="Bắt đầu 2 phút",
        work_seconds=1500,
        rest_seconds=300,
        segments=[
            {"order_index": 0, "kind": "work", "duration_seconds": 1500},
            {"order_index": 1, "kind": "rest", "duration_seconds": 300},
        ],
        duration_seconds=1800,
        schedule_revision=1,
        status="ended",
        start_state="started",
        start_decision_due_at=sched_start + timedelta(minutes=5),
    )
    run = SessionRun(
        id=uuid4(),
        user_id=user_id,
        session_id=sess.id,
        device_id="dev-1",
        started_at=sched_start,
        ended_at=ended_at,
        run_state="awaiting_checkin",
        active_phase="ended",
        segment_index=1,
        work_elapsed_seconds=actual_seconds,
        rest_elapsed_seconds=0,
        actual_work_seconds=actual_seconds,
        actual_source="timer",
        outcome="unknown",
        checkin_timeout_at=ended_at + timedelta(seconds=300),
        settings_snapshot={
            "effective_revision": 1,
            "session": {
                "session_id": str(sess.id),
                "step_id": str(step_id),
                "scheduled_start": sched_start.isoformat(),
                "scheduled_end": ended_at.isoformat(),
                "work_seconds": 1500,
                "rest_seconds": 300,
                "segments": [
                    {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                    {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                ],
                "duration_seconds": 1800,
                "schedule_revision": 1,
                "start_decision_due_at": (sched_start + timedelta(minutes=5)).isoformat(),
            },
        },
    )
    checkin = CheckIn(
        id=uuid4(),
        user_id=user_id,
        run_id=run.id,
        kind="final",
        response_state="open",
        session_outcome="unknown",
        asked_at=ended_at,
        response_revision=0,
    )
    db.add(sess)
    await db.flush()
    db.add(run)
    await db.flush()
    db.add(checkin)
    await db.commit()

    if answer_checkin:
        await checkin_service.respond_to_checkin(
            db,
            user_id=str(user_id),
            checkin_id=str(checkin.id),
            idempotency_key=f"{idempotency_prefix}_ci_{checkin.id}",
            payload=CheckInResponseRequest(
                expected_response_revision=0,
                session_outcome=outcome,  # type: ignore[arg-type]
                responded_at=ended_at + timedelta(seconds=10),
            ),
            now=ended_at + timedelta(seconds=10),
        )
    return sess.id, run.id, checkin.id


# =====================================================================
# BE-UF6-01: Lock-Free AI Empathy trong apply_settings_change & respond_to_suggestion
# =====================================================================


async def test_uf6_01_apply_settings_change_releases_db_tx_during_ai_and_replay_zero_ai():
    engine, sf = await _make_test_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_task_session(db, now=now, with_future_session=True)
            uid = ids["user_id"]

            # Seed thêm 1 Draft chưa confirm để test nhánh target_draft_id
            open_draft = await draft_service.create_draft(
                user_id=str(uid),
                idempotency_key="open-draft-uf601",
                payload=CreateDraftRequest(
                    client_draft_id="cd-open-uf601",
                    input_source="text",
                    raw_input="Draft chưa chốt",
                    summary=SummaryInput(
                        title="Draft chưa chốt",
                        description="Mô tả",
                        scope="1 phần",
                        time_constraint=TimeConstraint(
                            kind="deadline",
                            start_at=now,
                            deadline_at=now + timedelta(days=7),
                            input_timezone="Asia/Ho_Chi_Minh",
                        ),
                        availability=DraftAvailability(
                            timezone="Asia/Ho_Chi_Minh",
                            weekly_rules=[
                                WeeklyRule(weekday=1, start_local="09:00", end_local="11:00")
                            ],
                        ),
                    ),
                ),
                db=db,
            )

        entered = asyncio.Event()
        release = asyncio.Event()
        ai_calls = 0
        tx_state_during_ai: list[bool] = []

        async with sf() as db:
            async def _fake_empathy(**_kwargs):
                nonlocal ai_calls
                ai_calls += 1
                tx_state_during_ai.append(db.in_transaction())
                entered.set()
                await release.wait()
                return RescheduleEmpatheticResult(
                    empathetic_note="Mình đã điều chỉnh lại nhịp theo cài đặt mới.",
                    encouragement_tip="Mỗi bước nhỏ đều đáng ghi nhận.",
                    generation_tier="tier_1_gemini",
                )

            with patch.object(
                reschedule_service.ai_service,
                "generate_reschedule_empathy",
                side_effect=_fake_empathy,
            ):
                task = asyncio.create_task(
                    settings_service.apply_settings_change(
                        user_id=str(uid),
                        idempotency_key="uf6-01-pref-1",
                        payload=SettingsChangeRequest(
                            expected_preferences_revision=1,
                            changes={"preferences": {"buffer_multiplier": 1.5}},  # type: ignore[arg-type]
                        ),
                        db=db,
                    )
                )
                await entered.wait()
                assert tx_state_during_ai == [False]

                # Session song song vẫn ghi được lên users (không bị lock_owner chặn)
                async with sf() as other_db:
                    await other_db.execute(
                        update(User)
                        .where(User.id == uid)
                        .values(display_name="Parallel Write OK")
                    )
                    await other_db.commit()

                release.set()
                res1 = await task
                assert res1.application_state == "pending_review"
                assert res1.pending_proposal_id is not None
                assert ai_calls == 1

                # Replay cùng Idempotency-Key: gọi AI đúng 0 lần thêm
                res2 = await settings_service.apply_settings_change(
                    user_id=str(uid),
                    idempotency_key="uf6-01-pref-1",
                    payload=SettingsChangeRequest(
                        expected_preferences_revision=1,
                        changes={"preferences": {"buffer_multiplier": 1.5}},  # type: ignore[arg-type]
                    ),
                    db=db,
                )
                assert res2.change_request_id == res1.change_request_id
                assert ai_calls == 1

                # Nhánh target_draft_id: patch_draft chạy trong Phase B sau rollback() ở Phase A
                draft_res = await settings_service.apply_settings_change(
                    user_id=str(uid),
                    idempotency_key="uf6-01-draft-avail",
                    payload=SettingsChangeRequest(
                        target_draft_id=open_draft.draft_id,
                        expected_summary_revision=open_draft.summary_revision,
                        expected_planning_input_revision=open_draft.planning_input_revision,
                        changes={
                            "availability": {
                                "timezone": "Asia/Ho_Chi_Minh",
                                "weekly_rules": [
                                    {"weekday": 2, "start_local": "14:00", "end_local": "17:00"}
                                ],
                            }
                        },  # type: ignore[arg-type]
                    ),
                    db=db,
                )
                assert draft_res.application_state == "applied_to_draft"
                assert draft_res.summary_revision == open_draft.summary_revision + 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_uf6_01_apply_settings_change_mid_ai_mutation_and_false_to_true_race_409():
    engine, sf = await _make_test_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        # (a) Đột biến PreferenceRow giữa lúc pause AI -> Phase B trả 409 REVISION_CONFLICT
        async with sf() as db:
            ids = await _seed_user_task_session(db, now=now, with_future_session=True)
            uid = ids["user_id"]

        entered = asyncio.Event()
        release = asyncio.Event()

        async def _slow_empathy(**_kwargs):
            entered.set()
            await release.wait()
            return RescheduleEmpatheticResult(
                empathetic_note="Ghi chú điều chỉnh lịch.",
                encouragement_tip="Tip",
                generation_tier="tier_1_gemini",
            )

        async with sf() as db:
            with patch.object(
                reschedule_service.ai_service,
                "generate_reschedule_empathy",
                side_effect=_slow_empathy,
            ):
                task = asyncio.create_task(
                    settings_service.apply_settings_change(
                        user_id=str(uid),
                        idempotency_key="uf6-01-race-pref",
                        payload=SettingsChangeRequest(
                            expected_preferences_revision=1,
                            changes={"preferences": {"buffer_multiplier": 1.6}},  # type: ignore[arg-type]
                        ),
                        db=db,
                    )
                )
                await entered.wait()
                async with sf() as other_db:
                    pref = await other_db.scalar(
                        select(UserPreferenceRow).where(UserPreferenceRow.user_id == uid)
                    )
                    assert pref is not None
                    pref.effective_revision = 2
                    await other_db.commit()
                release.set()
                with pytest.raises(AppError) as exc_a:
                    await task
                assert exc_a.value.status_code == 409
                assert exc_a.value.code == "REVISION_CONFLICT"

        # (b) Race False -> True: Phase A chưa có phiên tương lai (precomputed_empathy=None),
        # session khác chèn Session scheduled ngay trước Phase B -> Phase B ném 409 REVISION_CONFLICT (retryable=True).
        async with sf() as db:
            ids2 = await _seed_user_task_session(db, now=now, with_future_session=False)
            uid2 = ids2["user_id"]
            step_id2 = ids2["step_id"]

        orig_lock_owner = command_service.lock_owner
        inserted_race = False

        async def _lock_owner_with_race(db_arg, user_id_arg):
            nonlocal inserted_race
            if not inserted_race:
                inserted_race = True
                async with sf() as other_db:
                    start = now + timedelta(days=2)
                    other_db.add(
                        SessionRow(
                            id=uuid4(),
                            user_id=uid2,
                            step_id=step_id2,
                            scheduled_start=start,
                            scheduled_end=start + timedelta(minutes=30),
                            micro_task_text="Race",
                            work_seconds=1500,
                            rest_seconds=300,
                            segments=[
                                {"order_index": 0, "kind": "work", "duration_seconds": 1500},
                                {"order_index": 1, "kind": "rest", "duration_seconds": 300},
                            ],
                            duration_seconds=1800,
                            schedule_revision=2,
                            status="scheduled",
                            start_state="waiting",
                            start_decision_due_at=start + timedelta(minutes=5),
                        )
                    )
                    await other_db.commit()
            return await orig_lock_owner(db_arg, user_id_arg)

        async with sf() as db:
            with patch.object(command_service, "lock_owner", side_effect=_lock_owner_with_race):
                with pytest.raises(AppError) as exc_b:
                    await settings_service.apply_settings_change(
                        user_id=str(uid2),
                        idempotency_key="uf6-01-race-f2t",
                        payload=SettingsChangeRequest(
                            expected_preferences_revision=1,
                            changes={"preferences": {"buffer_multiplier": 1.6}},  # type: ignore[arg-type]
                        ),
                        db=db,
                    )
                assert exc_b.value.status_code == 409
                assert exc_b.value.code == "REVISION_CONFLICT"
                assert exc_b.value.retryable is True
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_uf6_01_respond_to_suggestion_releases_db_tx_during_ai_and_handles_conflict():
    engine, sf = await _make_test_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_task_session(db, now=now, with_future_session=True)
            uid = ids["user_id"]
            sugg1 = AdaptationSuggestion(
                id=uuid4(),
                user_id=uid,
                kind="buffer_multiplier",
                sample_count=8,
                current_value=1.2,
                suggested_value=1.5,
                reason="Xu hướng mất thêm thời gian.",
                policy_revision="v1",
                sample_refs=["run-1"],
                status="pending",
            )
            sugg2 = AdaptationSuggestion(
                id=uuid4(),
                user_id=uid,
                kind="buffer_multiplier",
                sample_count=8,
                current_value=1.2,
                suggested_value=1.6,
                reason="Xu hướng mất thêm thời gian.",
                policy_revision="v1",
                sample_refs=["run-2"],
                status="pending",
            )
            db.add_all([sugg1, sugg2])
            await db.commit()
            sugg1_id, sugg2_id = sugg1.id, sugg2.id

        entered = asyncio.Event()
        release = asyncio.Event()
        tx_states: list[bool] = []

        async with sf() as db:
            async def _fake_empathy(**_kwargs):
                tx_states.append(db.in_transaction())
                entered.set()
                await release.wait()
                return RescheduleEmpatheticResult(
                    empathetic_note="Gợi ý hợp lý.",
                    encouragement_tip="Cố gắng nhé.",
                    generation_tier="tier_1_gemini",
                )

            with patch.object(
                reschedule_service.ai_service,
                "generate_reschedule_empathy",
                side_effect=_fake_empathy,
            ):
                t1 = asyncio.create_task(
                    settings_service.respond_to_suggestion(
                        user_id=str(uid),
                        idempotency_key="uf6-01-sugg-1",
                        suggestion_id=str(sugg1_id),
                        payload=SuggestionResponseRequest(
                            expected_suggestion_revision=1,
                            expected_preference_revision=1,
                            action="accept",
                        ),
                        db=db,
                    )
                )
                await entered.wait()
                assert tx_states == [False]
                # Phase A KHÔNG mutate SuggestionRow
                async with sf() as check_db:
                    row_mid = await check_db.get(AdaptationSuggestion, sugg1_id)
                    assert row_mid is not None
                    assert row_mid.status == "pending"

                release.set()
                res = await t1
                assert res.status == "accepted"
                assert res.application_state == "pending_review"
                assert res.pending_proposal_id is not None

        # Đột biến SuggestionRow giữa AI -> 409 SUGGESTION_CHANGED
        entered2 = asyncio.Event()
        release2 = asyncio.Event()

        async def _slow_empathy_2(**_kwargs):
            entered2.set()
            await release2.wait()
            return RescheduleEmpatheticResult(
                empathetic_note="Ghi chú điều chỉnh lịch.",
                encouragement_tip="Tip",
                generation_tier="tier_1_gemini",
            )

        async with sf() as db:
            with patch.object(
                reschedule_service.ai_service,
                "generate_reschedule_empathy",
                side_effect=_slow_empathy_2,
            ):
                t2 = asyncio.create_task(
                    settings_service.respond_to_suggestion(
                        user_id=str(uid),
                        idempotency_key="uf6-01-sugg-2",
                        suggestion_id=str(sugg2_id),
                        payload=SuggestionResponseRequest(
                            expected_suggestion_revision=1,
                            expected_preference_revision=1,
                            action="accept",
                        ),
                        db=db,
                    )
                )
                await entered2.wait()
                async with sf() as other_db:
                    s2 = await other_db.get(AdaptationSuggestion, sugg2_id)
                    assert s2 is not None
                    s2.status = "superseded"
                    await other_db.commit()
                release2.set()
                with pytest.raises(AppError) as exc:
                    await t2
                assert exc.value.status_code == 409
                assert exc.value.code == "SUGGESTION_CHANGED"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =====================================================================
# BE-UF6-02: Disconnect queue purge, preview confirm event_link_id, _load_google_busy_items
# =====================================================================


async def test_uf6_02_calendar_disconnect_cancels_queued_retry_conflict_jobs_and_preserves_tasks():
    engine, sf = await _make_test_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_task_session(db, now=now, with_future_session=True)
            uid = ids["user_id"]
            conn = CalendarConnection(
                id=uuid4(),
                user_id=uid,
                provider="google",
                account_ref="e2e-cal-06@example.com",
                read_calendar_ids=["primary"],
                write_calendar_id="proplan_cal",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref=encrypt_credentials({"access_token": "tok", "refresh_token": "ref"}),
            )
            db.add(conn)
            await db.flush()
            jobs = [
                CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn.id,
                    target_kind="session",
                    target_ref=str(ids["session_id"]),
                    operation="create",
                    target_schedule_revision=1,
                    payload_snapshot={},
                    idempotency_key=f"job-{state}",
                    job_state=state,
                )
                for state in ("queued", "retry", "conflict")
            ]
            db.add_all(jobs)
            await db.commit()
            conn_id = conn.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = _auth_headers(uid)
            disc_resp = await client.post(
                f"/api/v1/calendar/connections/{conn_id}/disconnect",
                headers=headers,
            )
            assert disc_resp.status_code == 200, disc_resp.text
            assert disc_resp.json()["data"]["connection_state"] == "disconnected"

            # Verify GET /api/v1/schedule reports not_integrated
            sched_resp = await client.get(
                "/api/v1/schedule",
                params={
                    "from": (now - timedelta(days=1)).isoformat(),
                    "to": (now + timedelta(days=10)).isoformat(),
                },
                headers=headers,
            )
            assert sched_resp.status_code == 200, sched_resp.text
            assert sched_resp.json()["data"]["calendar_sync_summary"] == "not_integrated"

        async with sf() as db:
            refreshed_jobs = (
                await db.scalars(
                    select(CalendarSyncJob).where(CalendarSyncJob.connection_id == conn_id)
                )
            ).all()
            assert len(refreshed_jobs) == 3
            assert all(j.job_state == "cancelled" for j in refreshed_jobs)
            assert all(j.last_error_code == "DISCONNECTED" for j in refreshed_jobs)
            remaining_queued = [j for j in refreshed_jobs if j.job_state == "queued"]
            assert len(remaining_queued) == 0
            # Task / Step / Session được bảo toàn nguyên vẹn
            task_row = await db.get(TaskRow, ids["task_id"])
            sess_row = await db.get(SessionRow, ids["session_id"])
            assert task_row is not None and task_row.status == "scheduled"
            assert sess_row is not None and sess_row.status == "scheduled"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_uf6_02_event_change_preview_confirm_binds_event_link_and_blocks_disconnected_and_cancelled_retry():
    engine, sf = await _make_test_env()
    reset_fake_calendar_client()
    fake_client = get_calendar_client()
    assert isinstance(fake_client, FakeCalendarClient)
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_task_session(db, now=now, with_future_session=True)
            uid = ids["user_id"]
            conn_id = uuid4()
            conn = CalendarConnection(
                id=conn_id,
                user_id=uid,
                provider="google",
                account_ref="cal-bind@example.com",
                read_calendar_ids=["primary"],
                write_calendar_id="proplan_cal",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref=encrypt_credentials(
                    {
                        "user_id": str(uid),
                        "connection_id": str(conn_id),
                        "access_token": "fake-access-token",
                        "refresh_token": "fake-refresh-token",
                        "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                    }
                ),
            )
            db.add(conn)
            await db.flush()
            # Seed fake Google event
            fake_client.mock_events["proplan_cal"] = [
                {
                    "id": "gcal_ev_1",
                    "summary": "Session Focus",
                    "etag": '"etag-v1"',
                    "start": {"dateTime": (now + timedelta(days=1)).isoformat()},
                    "end": {"dateTime": (now + timedelta(days=1, minutes=30)).isoformat()},
                }
            ]
            link = CalendarEventLink(
                id=uuid4(),
                user_id=uid,
                session_id=ids["session_id"],
                connection_id=conn.id,
                google_calendar_id="proplan_cal",
                google_event_id="gcal_ev_1",
                google_etag='"etag-v1"',
                desired_schedule_revision=1,
                synced_schedule_revision=1,
                sync_state="synced",
                is_current=True,
            )
            p1 = CalendarPreview(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                kind="event_change",
                base_connection_revision=conn.revision,
                source_revisions={},
                payload={
                    "operation": "update",
                    "connection_id": str(conn.id),
                    "google_calendar_id": "proplan_cal",
                    "google_event_id": "gcal_ev_1",
                    "expected_google_etag": '"etag-v1"',
                    "payload": {
                        "summary": "Updated Title",
                        "start": {"dateTime": (now + timedelta(days=1, hours=1)).isoformat()},
                        "end": {"dateTime": (now + timedelta(days=1, hours=1, minutes=30)).isoformat()},
                    },
                },
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            p2 = CalendarPreview(
                id=uuid4(),
                user_id=uid,
                connection_id=conn.id,
                kind="event_change",
                base_connection_revision=conn.revision,
                source_revisions={},
                payload={
                    "operation": "update",
                    "connection_id": str(conn.id),
                    "google_calendar_id": "proplan_cal",
                    "google_event_id": "gcal_ev_1",
                    "expected_google_etag": '"etag-v1"',
                    "payload": {"summary": "Will fail after disconnect"},
                },
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            db.add_all([link, p1, p2])
            await db.commit()
            conn_id, link_id, p1_id, p2_id = conn.id, link.id, p1.id, p2.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = _auth_headers(uid)
            c1 = await client.post(
                f"/api/v1/calendar/event-change-previews/{p1_id}/confirm",
                json={"expected_preview_revision": 1, "source_revisions": {}},
                headers=headers,
            )
            assert c1.status_code == 200, c1.text
            job_id = UUID(c1.json()["data"]["job_id"])

            async with sf() as db:
                job = await db.get(CalendarSyncJob, job_id)
                assert job is not None
                assert job.event_link_id == link_id

            # Refresh connection -> worker cập nhật CalendarEventLink.google_etag
            ref_resp = await client.post(
                f"/api/v1/calendar/connections/{conn_id}/refresh",
                json={},
                headers=headers,
            )
            assert ref_resp.status_code == 200, ref_resp.text
            async with sf() as db:
                updated_link = await db.get(CalendarEventLink, link_id)
                assert updated_link is not None
                assert updated_link.google_etag != '"etag-v1"'

            # Tạo thêm 1 job lỗi để test disconnect cancel + retry block
            async with sf() as db:
                cancelled_job = CalendarSyncJob(
                    id=uuid4(),
                    user_id=uid,
                    connection_id=conn_id,
                    target_kind="event",
                    target_ref="gcal_ev_1",
                    operation="update",
                    idempotency_key="retry-cancel-test",
                    payload_snapshot={},
                    job_state="retry",
                )
                db.add(cancelled_job)
                await db.commit()
                cancelled_job_id = cancelled_job.id

            # Disconnect
            disc = await client.post(
                f"/api/v1/calendar/connections/{conn_id}/disconnect",
                headers=headers,
            )
            assert disc.status_code == 200

            # Confirm preview p2 tạo trước khi disconnect -> 409 CALENDAR_DISCONNECTED (trước REVISION_CONFLICT)
            c2 = await client.post(
                f"/api/v1/calendar/event-change-previews/{p2_id}/confirm",
                json={"expected_preview_revision": 1, "source_revisions": {}},
                headers=headers,
            )
            assert c2.status_code == 409
            assert c2.json()["error"]["code"] == "CALENDAR_DISCONNECTED"

            # Retry job đã bị cancelled -> 409 JOB_CANCELLED (kể cả sau khi reconnect)
            async with sf() as db:
                conn_row = await db.get(CalendarConnection, conn_id)
                assert conn_row is not None
                conn_row.connection_state = "connected"
                await db.commit()

            retry_resp = await client.post(
                f"/api/v1/calendar/sync-jobs/{cancelled_job_id}/retry",
                json={},
                headers=headers,
            )
            assert retry_resp.status_code == 409
            assert retry_resp.json()["error"]["code"] == "JOB_CANCELLED"
    finally:
        app.dependency_overrides.clear()
        reset_fake_calendar_client()
        await engine.dispose()


async def test_uf6_02_load_google_busy_items_filters_valid_preserves_degraded_status_and_ignores_all_disconnected():
    engine, sf = await _make_test_env()
    try:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        async with sf() as db:
            ids = await _seed_user_task_session(db, now=now, with_future_session=False)
            uid = ids["user_id"]
            avail = await db.scalar(
                select(AvailabilityRow).where(AvailabilityRow.user_id == uid)
            )
            assert avail is not None
            conn = CalendarConnection(
                id=uuid4(),
                user_id=uid,
                provider="google",
                account_ref="busy@example.com",
                read_calendar_ids=["primary"],
                write_calendar_id="primary",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref="ref",
            )
            db.add(conn)
            valid_ctx = PlanningContext(
                id=uuid4(),
                user_id=uid,
                availability_id=avail.id,
                availability_revision=avail.revision,
                query_start=now,
                query_end=now + timedelta(days=7),
                fetched_at=now - timedelta(minutes=10),
                sync_state="valid",
                coverage_state="complete",
                selected_calendar_ids=["primary"],
                selected_calendar_sources=[],
                busy_intervals=[
                    {
                        "id": "busy-1",
                        "title": "Họp cố định",
                        "start": (now + timedelta(hours=2)).isoformat(),
                        "end": (now + timedelta(hours=3)).isoformat(),
                    }
                ],
                calendar_errors=[],
                event_snapshots=[],
                manual_busy_intervals=[],
                excluded_sources=[],
                created_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
            )
            error_ctx = PlanningContext(
                id=uuid4(),
                user_id=uid,
                availability_id=avail.id,
                availability_revision=avail.revision,
                query_start=now,
                query_end=now + timedelta(days=7),
                fetched_at=now,
                sync_state="error",
                coverage_state="degraded",
                selected_calendar_ids=["primary"],
                selected_calendar_sources=[],
                busy_intervals=[],
                calendar_errors=[{"code": "UPSTREAM_503"}],
                event_snapshots=[],
                manual_busy_intervals=[],
                excluded_sources=[],
                created_at=now,
                updated_at=now,
            )
            db.add_all([valid_ctx, error_ctx])
            await db.commit()

            items, status_str, fetched_at = await reschedule_service._load_google_busy_items(
                db, uid, now, now + timedelta(days=7)
            )
            assert status_str == "unavailable"
            assert len(items) == 1
            assert items[0].event_id == "busy-1"
            assert fetched_at is not None

            # Kiểm tra thêm sync_state="synced" (cùng "valid", "ok") vẫn được chấp nhận khi latest là lỗi
            valid_ctx.sync_state = "synced"
            await db.commit()
            items_synced, status_synced, _ = await reschedule_service._load_google_busy_items(
                db, uid, now, now + timedelta(days=7)
            )
            assert status_synced == "unavailable"
            assert len(items_synced) == 1
            assert items_synced[0].event_id == "busy-1"

            # Khi tất cả CalendarConnection của user đều disconnected -> trả rỗng
            conn.connection_state = "disconnected"
            await db.commit()
            items2, status2, fetched2 = await reschedule_service._load_google_busy_items(
                db, uid, now, now + timedelta(days=7)
            )
            assert items2 == []
            assert status2 == "ok"
            assert fetched2 is None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =====================================================================
# BE-UF6-03: Task.source_revisions cho expandNextHorizon
# =====================================================================


async def test_uf6_03_task_detail_and_list_return_source_revisions_for_expand_next_horizon():
    engine, sf = await _make_test_env()
    try:
        now = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        async with sf() as db:
            uid = uuid4()
            db.add(
                User(
                    id=uid,
                    email=f"u_{uid.hex[:8]}@example.com",
                    hashed_password="pw",
                    display_name="UF6-03 User",
                )
            )
            await db.flush()
            conn = CalendarConnection(
                id=uuid4(),
                user_id=uid,
                provider="google",
                account_ref="horizon@example.com",
                read_calendar_ids=[],
                write_calendar_id="primary",
                granted_scopes=["https://www.googleapis.com/auth/calendar"],
                connection_state="connected",
                credential_ref="ref",
                revision=3,
            )
            db.add(conn)
            await db.commit()

            availability = DraftAvailability(
                timezone="UTC",
                weekly_rules=[
                    WeeklyRule(weekday=w, start_local="08:00", end_local="21:00")
                    for w in range(1, 8)
                ],
            )
            draft = await draft_service.create_draft(
                user_id=str(uid),
                idempotency_key="uf603-draft",
                payload=CreateDraftRequest(
                    client_draft_id="uf603-cd",
                    input_source="text",
                    raw_input="Đề tài dài hạn",
                    summary=SummaryInput(
                        title="Đề tài dài hạn",
                        description="Mô tả",
                        scope="3 giai đoạn",
                        time_constraint=TimeConstraint(
                            kind="deadline",
                            start_at=now,
                            deadline_at=now + timedelta(days=28),
                            input_timezone="UTC",
                        ),
                        availability=availability,
                    ),
                ),
                db=db,
            )
            await draft_service.confirm_draft_summary(
                user_id=str(uid),
                draft_id=draft.draft_id,
                idempotency_key="uf603-confirm-sum",
                payload=ConfirmSummaryRequest(expected_summary_revision=draft.summary_revision),
                db=db,
            )
            cand = await plan_service.create_candidate(
                user_id=str(uid),
                draft_id=draft.draft_id,
                idempotency_key="uf603-cand",
                payload=GenerateCandidateRequest(
                    source_summary_revision=draft.summary_revision,
                    source_planning_input_revision=draft.planning_input_revision,
                    manual_steps=[
                        ManualStepInput(
                            order_index=0,
                            title="Giai đoạn 1",
                            expected_output="Xong GĐ1",
                            estimated_base_seconds=1250,
                        )
                    ],
                    unexpanded_scope="Giai đoạn 2: Thử nghiệm\nGiai đoạn 3: Hoàn tất",
                ),
                db=db,
            )
            confirmed = await plan_service.confirm_candidate(
                user_id=str(uid),
                candidate_id=cand.candidate_id,
                idempotency_key="uf603-confirm-cand",
                payload=ConfirmCandidateRequestFromTest(cand),
                db=db,
            )
            task_id = confirmed.task.task_id
            assert confirmed.task.source_revisions is not None
            assert confirmed.task.source_revisions.calendar >= 1
            # Giả lập CalendarContext tăng revision lên 3 sau khi đồng bộ lịch
            await db.execute(
                update(PlanningContext)
                .where(PlanningContext.user_id == uid)
                .values(revision=3)
            )
            await db.commit()

        async def _no_ai_decompose(**_kwargs):
            return None

        async def _fallback_enrich(split_sessions, task_title=None):
            del task_title
            return reschedule_service.ai_service._fallback_enrich_split_sessions(split_sessions)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = _auth_headers(uid)
            detail = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
            assert detail.status_code == 200, detail.text
            detail_revs = detail.json()["data"]["source_revisions"]
            assert detail_revs["calendar"] == 3
            assert detail_revs["task"] >= 1

            listed = await client.get("/api/v1/tasks", headers=headers)
            assert listed.status_code == 200, listed.text
            assert listed.json()["data"]["items"][0]["source_revisions"] == detail_revs

            with (
                patch.object(
                    reschedule_service.ai_service,
                    "decompose_task",
                    side_effect=_no_ai_decompose,
                ),
                patch.object(
                    reschedule_service.ai_service,
                    "enrich_split_sessions",
                    side_effect=_fallback_enrich,
                ),
            ):
                prop_resp = await client.post(
                    "/api/v1/proposals",
                    json=CreateProposalRequest(
                        trigger_reason="expand_next_horizon",
                        target_task_id=task_id,
                        allowed_task_ids=[task_id],
                        expected_base_schedule_revision=detail_revs["schedule"],
                        expected_source_revisions=detail_revs,
                    ).model_dump(mode="json"),
                    headers={**headers, "Idempotency-Key": "uf603-expand-prop"},
                )
            assert prop_resp.status_code == 201, prop_resp.text
            prop_data = prop_resp.json()["data"]

            app_resp = await client.post(
                f"/api/v1/proposals/{prop_data['proposal_id']}/approve",
                json=ApproveProposalRequest(
                    expected_proposal_revision=prop_data["proposal_revision"],
                    expected_base_schedule_revision=prop_data["base_schedule_revision"],
                    expected_source_revisions=prop_data["source_revisions"],
                ).model_dump(mode="json"),
                headers={**headers, "Idempotency-Key": "uf603-expand-approve"},
            )
            assert app_resp.status_code == 200, app_resp.text

            detail_after = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
            assert detail_after.json()["data"]["decomposition_state"] == "complete"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def ConfirmCandidateRequestFromTest(cand):
    from app.schemas.planning import ConfirmCandidateRequest

    return ConfirmCandidateRequest(
        expected_review_revision=cand.review_revision,
        expected_summary_revision=cand.source_summary_revision,
        expected_planning_input_revision=cand.source_planning_input_revision,
        expected_source_revisions=cand.source_revisions,
    )


# =====================================================================
# BE-UF6-04: Focus Streak, Freeze, 24h Repair & Undo / Correction
# =====================================================================


async def test_uf6_04_streak_3_consecutive_days_with_local_timezone_and_auth_me():
    engine, sf = await _make_test_env()
    try:
        # 23:30 +07:00 = 16:30 UTC cùng ngày ở VN, sang UTC ngày kế nếu ở phía đông nhưng ở VN vẫn là ngày 1, 2, 3
        day1_vn = datetime(2026, 11, 18, 23, 30, tzinfo=TZ_VN)
        day2_vn = datetime(2026, 11, 19, 23, 30, tzinfo=TZ_VN)
        day3_vn = datetime(2026, 11, 20, 23, 30, tzinfo=TZ_VN)

        async with sf() as db:
            ids = await _seed_user_task_session(
                db,
                now=day1_vn.astimezone(UTC),
                timezone_name="Asia/Ho_Chi_Minh",
                with_future_session=False,
            )
            uid, step_id = ids["user_id"], ids["step_id"]

            for idx, dt in enumerate((day1_vn, day2_vn, day3_vn), start=1):
                await _seed_completed_run_and_checkin(
                    db,
                    user_id=uid,
                    step_id=step_id,
                    ended_at=dt.astimezone(UTC),
                    actual_seconds=1500,
                    outcome="completed",
                    idempotency_prefix=f"d{idx}",
                )

            user_row = await db.get(User, uid)
            assert user_row is not None
            assert user_row.current_streak_days == 3
            assert user_row.last_streak_date == day3_vn.date()

            me_user = await auth_service.load_current_user(db, uid)
            assert me_user.current_streak_days == 3
            assert me_user.last_streak_date == day3_vn.date()

            summary = await streak_service.get_streak_summary(
                db,
                user_id=str(uid),
                commit_if_changed=False,
                now=day3_vn.astimezone(UTC),
            )
            assert summary.current_streak_days == 3
            assert summary.unlocked is True
            assert summary.longest_streak_days == 3
            assert summary.last_streak_date == day3_vn.date()
            assert summary.today_completed is True
            assert len(summary.day_records) == 3

        # Kiểm tra HTTP GET /api/v1/auth/me trả đủ 4 trường streak
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def _real_me_dep():
                async with sf() as s:
                    return await auth_service.load_current_user(s, uid)

            app.dependency_overrides[get_current_user] = _real_me_dep
            me_resp = await client.get("/api/v1/auth/me")
            assert me_resp.status_code == 200
            me_data = me_resp.json()["data"]
            assert me_data["current_streak_days"] == 3
            assert me_data["streak_freeze_tokens"] == 2
            assert me_data["streak_frozen"] is False
            assert me_data["last_streak_date"] == day3_vn.date().isoformat()
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_uf6_04_timer_end_alone_and_under_120s_do_not_increment_streak_and_backfill_safe():
    engine, sf = await _make_test_env()
    try:
        day1 = datetime(2026, 11, 20, 10, 0, tzinfo=TZ_VN)
        past_day = datetime(2026, 11, 15, 10, 0, tzinfo=TZ_VN)
        async with sf() as db:
            ids = await _seed_user_task_session(
                db,
                now=day1.astimezone(UTC),
                timezone_name="Asia/Ho_Chi_Minh",
                with_future_session=False,
            )
            uid, step_id = ids["user_id"], ids["step_id"]

            # 1) Hết giờ / chưa check-in -> streak = 0
            _, _, _ = await _seed_completed_run_and_checkin(
                db,
                user_id=uid,
                step_id=step_id,
                ended_at=day1.astimezone(UTC),
                actual_seconds=1500,
                answer_checkin=False,
                idempotency_prefix="no_ci",
            )
            u1 = await db.get(User, uid)
            assert u1 is not None and u1.current_streak_days == 0

            # 2) Check-in completed nhưng actual = 90s (< 120s) -> streak không tăng
            _, run2_id, _ = await _seed_completed_run_and_checkin(
                db,
                user_id=uid,
                step_id=step_id,
                ended_at=(day1 + timedelta(hours=2)).astimezone(UTC),
                actual_seconds=90,
                outcome="completed",
                answer_checkin=True,
                idempotency_prefix="short",
            )
            u2 = await db.get(User, uid)
            assert u2 is not None and u2.current_streak_days == 0

            # 3) Hiệu chỉnh actual-corrections lên 300s -> streak tự cập nhật lên 1
            run2 = await db.get(SessionRun, run2_id)
            assert run2 is not None
            await run_service.record_actual_correction(
                db,
                user_id=str(uid),
                run_id=str(run2_id),
                idempotency_key="corr-300",
                payload=ActualCorrectionRequest(
                    expected_run_revision=run2.revision,
                    self_report_work_seconds=300,
                    reason="Quên bấm resume timer",
                    occurred_at=(day1 + timedelta(hours=2, minutes=1)).astimezone(UTC),
                ),
                now=(day1 + timedelta(hours=2, minutes=2)).astimezone(UTC),
            )
            u3 = await db.get(User, uid)
            assert u3 is not None
            assert u3.current_streak_days == 1
            assert u3.last_streak_date == day1.date()

            # 4) Backfilled check-in ở quá khứ (< last_streak_date): chỉ ghi lịch sử, không phá current_streak_days
            await _seed_completed_run_and_checkin(
                db,
                user_id=uid,
                step_id=step_id,
                ended_at=past_day.astimezone(UTC),
                actual_seconds=600,
                outcome="completed",
                answer_checkin=True,
                idempotency_prefix="backfill",
            )
            u4 = await db.get(User, uid)
            assert u4 is not None
            assert u4.current_streak_days == 1
            assert u4.last_streak_date == day1.date()
            backfill_ev = await db.scalar(
                select(AuditEvent).where(
                    AuditEvent.user_id == uid,
                    AuditEvent.event_id == f"streak:done:{past_day.date().isoformat()}",
                )
            )
            assert backfill_ev is not None
            assert backfill_ev.resource_ref.get("backfilled") is True
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_uf6_04_same_day_multiple_sessions_idempotent_and_undo_preserves_manual_repair():
    engine, sf = await _make_test_env()
    try:
        day1 = datetime(2026, 11, 17, 10, 0, tzinfo=TZ_VN)
        day2 = datetime(2026, 11, 18, 10, 0, tzinfo=TZ_VN)
        day3 = datetime(2026, 11, 19, 10, 0, tzinfo=TZ_VN)
        day5 = datetime(2026, 11, 21, 10, 0, tzinfo=TZ_VN)

        async with sf() as db:
            ids = await _seed_user_task_session(
                db,
                now=day1.astimezone(UTC),
                timezone_name="Asia/Ho_Chi_Minh",
                with_future_session=False,
            )
            uid, step_id = ids["user_id"], ids["step_id"]

            # 3 ngày liên tiếp -> streak = 3
            for idx, dt in enumerate((day1, day2, day3), start=1):
                await _seed_completed_run_and_checkin(
                    db,
                    user_id=uid,
                    step_id=step_id,
                    ended_at=dt.astimezone(UTC),
                    actual_seconds=1500,
                    outcome="completed",
                    idempotency_prefix=f"s{idx}",
                )

            # Phiên thứ 2 trong cùng ngày day3 -> idempotent, vẫn là 3
            _, _, ci_day3_second = await _seed_completed_run_and_checkin(
                db,
                user_id=uid,
                step_id=step_id,
                ended_at=(day3 + timedelta(hours=3)).astimezone(UTC),
                actual_seconds=1500,
                outcome="completed",
                idempotency_prefix="s3_extra",
            )
            u = await db.get(User, uid)
            assert u is not None and u.current_streak_days == 3

            # Undo 1 phiên trên day3 khi vẫn còn phiên đầu trên day3 -> giữ nguyên streak = 3
            await checkin_service.respond_to_checkin(
                db,
                user_id=str(uid),
                checkin_id=str(ci_day3_second),
                idempotency_key="undo-second-day3",
                payload=CheckInResponseRequest(
                    expected_response_revision=1,
                    session_outcome="skipped",
                    skip_scope="skip_session",
                    responded_at=(day3 + timedelta(hours=3, minutes=2)).astimezone(UTC),
                ),
                now=(day3 + timedelta(hours=3, minutes=2)).astimezone(UTC),
            )
            u = await db.get(User, uid)
            assert u is not None and u.current_streak_days == 3

            # Hết token -> lỡ day4 -> dùng manual_repair trên day5 -> làm day5 -> Undo day5 không xóa manual_repair day4
            u.streak_freeze_tokens = 0
            await db.commit()

            repaired = await streak_service.repair_streak(
                db,
                user_id=str(uid),
                idempotency_key="manual-rep-day4",
                now=day5.astimezone(UTC),
            )
            assert repaired.repairs_used_this_month == 1

            _, _, ci_day5 = await _seed_completed_run_and_checkin(
                db,
                user_id=uid,
                step_id=step_id,
                ended_at=day5.astimezone(UTC),
                actual_seconds=1500,
                outcome="completed",
                idempotency_prefix="s5",
            )
            u = await db.get(User, uid)
            assert u is not None and u.current_streak_days == 4

            # Undo check-in duy nhất của day5 -> hoàn nguyên về 3, bảo toàn manual_repair day4!
            await checkin_service.respond_to_checkin(
                db,
                user_id=str(uid),
                checkin_id=str(ci_day5),
                idempotency_key="undo-day5",
                payload=CheckInResponseRequest(
                    expected_response_revision=1,
                    session_outcome="skipped",
                    skip_scope="skip_session",
                    responded_at=(day5 + timedelta(minutes=2)).astimezone(UTC),
                ),
                now=(day5 + timedelta(minutes=2)).astimezone(UTC),
            )
            u = await db.get(User, uid)
            assert u is not None
            assert u.current_streak_days == 3
            assert u.last_streak_date == (day5.date() - timedelta(days=1))

            rep_ev = await db.scalar(
                select(AuditEvent).where(
                    AuditEvent.user_id == uid,
                    AuditEvent.event_id == f"streak:repair:{(day5.date() - timedelta(days=1)).isoformat()}",
                )
            )
            assert rep_ev is not None
            assert rep_ev.resource_ref.get("active") is True
            assert rep_ev.resource_ref.get("source") == "manual_repair"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_uf6_04_auto_freeze_24h_repair_idempotent_concurrent_and_soft_reset_rules(
    tmp_path,
):
    engine = make_engine("sqlite+aiosqlite:///" + (tmp_path / "streak_concurrent.db").as_posix())
    sf = session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        day1 = datetime(2026, 11, 10, 10, 0, tzinfo=TZ_VN)
        day3 = datetime(2026, 11, 12, 10, 0, tzinfo=TZ_VN)

        # 1) Warm-up (< 3 ngày) lỡ 1 ngày -> reset về 0 không trừ khiên
        async with sf() as db:
            ids_w = await _seed_user_task_session(
                db,
                now=day1.astimezone(UTC),
                with_future_session=False,
                streak_days=2,
                freeze_tokens=2,
                last_streak_date=day1.date(),
            )
            uid_w = ids_w["user_id"]
            res_w = await streak_service.get_streak_summary(
                db, user_id=str(uid_w), commit_if_changed=True, now=day3.astimezone(UTC)
            )
            assert res_w.current_streak_days == 0
            assert res_w.streak_freeze_tokens == 2

        # 2) Unlocked (>= 3 ngày) lỡ 1 ngày (gap == 2) khi còn token -> Auto-Freeze trừ đúng 1 token kể cả gọi song song
        async with sf() as db:
            ids_f = await _seed_user_task_session(
                db,
                now=day1.astimezone(UTC),
                with_future_session=False,
                streak_days=3,
                freeze_tokens=2,
                last_streak_date=day1.date(),
            )
            uid_f, step_f = ids_f["user_id"], ids_f["step_id"]

        async with sf() as db_a, sf() as db_b:
            r_a, r_b = await asyncio.gather(
                streak_service.get_streak_summary(
                    db_a, user_id=str(uid_f), commit_if_changed=True, now=day3.astimezone(UTC)
                ),
                streak_service.get_streak_summary(
                    db_b, user_id=str(uid_f), commit_if_changed=True, now=day3.astimezone(UTC)
                ),
            )
            assert r_a.streak_freeze_tokens == 1
            assert r_b.streak_freeze_tokens == 1
            assert r_a.streak_frozen is True
            assert r_a.last_streak_date == day1.date() + timedelta(days=1)

        # Làm tiếp day3 -> streak tăng lên 4 & streak_frozen=False
        async with sf() as db:
            await _seed_completed_run_and_checkin(
                db,
                user_id=uid_f,
                step_id=step_f,
                ended_at=day3.astimezone(UTC),
                actual_seconds=1500,
                outcome="completed",
                idempotency_prefix="after_freeze",
            )
            u_f = await db.get(User, uid_f)
            assert u_f is not None
            assert u_f.current_streak_days == 4
            assert u_f.streak_frozen is False

        # 3) Hết token (0) lỡ 1 ngày (gap == 2) -> repair_pending=True, repair idempotent & soft reset gap >= 3
        async with sf() as db:
            ids_r = await _seed_user_task_session(
                db,
                now=day1.astimezone(UTC),
                with_future_session=False,
                streak_days=4,
                freeze_tokens=0,
                last_streak_date=day1.date(),
            )
            uid_r = ids_r["user_id"]
            # Seed 4 AuditEvent lịch sử tương ứng chuỗi 4 ngày trước day1
            for offset in range(4):
                d_hist = day1.date() - timedelta(days=3 - offset)
                db.add(
                    AuditEvent(
                        user_id=uid_r,
                        event_id=f"streak:done:{d_hist.isoformat()}",
                        event_type=streak_service.STREAK_EVENT_DONE,
                        resource_ref={"date": d_hist.isoformat(), "active": True},
                        occurred_at=datetime.combine(d_hist, day1.timetz()),
                        received_at=datetime.combine(d_hist, day1.timetz()),
                    )
                )
            await db.commit()

            summary_r = await streak_service.get_streak_summary(
                db, user_id=str(uid_r), commit_if_changed=True, now=day3.astimezone(UTC)
            )
            assert summary_r.repair_pending is True
            assert summary_r.current_streak_days == 4
            assert any(
                rec.date == day1.date() + timedelta(days=1) and rec.kind == "repair"
                for rec in summary_r.day_records
            )

            rep1 = await streak_service.repair_streak(
                db,
                user_id=str(uid_r),
                idempotency_key="rep-idemp-1",
                now=day3.astimezone(UTC),
            )
            rep2 = await streak_service.repair_streak(
                db,
                user_id=str(uid_r),
                idempotency_key="rep-idemp-1",
                now=day3.astimezone(UTC),
            )
            assert rep1.repairs_used_this_month == 1
            assert rep2.repairs_used_this_month == 1
            assert rep1.repairs_left_this_month == 1

            # Lỡ quá hạn (gap >= 3) -> Soft Reset nhưng giữ longest_streak_days >= 3
            day6 = day1 + timedelta(days=5)
            soft_reset = await streak_service.get_streak_summary(
                db, user_id=str(uid_r), commit_if_changed=True, now=day6.astimezone(UTC)
            )
            assert soft_reset.current_streak_days == 0
            assert soft_reset.longest_streak_days >= 4
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
