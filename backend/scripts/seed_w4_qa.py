"""Seed the two verified Google accounts in a migrated W4 QA database."""

import argparse
import asyncio
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import make_engine, session_factory  # noqa: E402
from app.models import CheckIn, PlanningDraft, Session, SessionRun, Step, Task, User, UserPreference  # noqa: E402
from app.schemas.availability import DateOverride, DraftAvailability, LocalInterval  # noqa: E402
from app.schemas.common import TimeConstraint  # noqa: E402
from app.schemas.draft import TaskSummary  # noqa: E402


async def seed_account(db: AsyncSession, user: User, label: str) -> None:
    owner = user.id
    if await db.scalar(select(UserPreference.id).where(UserPreference.user_id == owner)) is None:
        db.add(UserPreference(user_id=owner))

    draft_id = uuid5(owner, "w4-03a:draft")
    if await db.get(PlanningDraft, draft_id) is None:
        title = f"[W4 QA {label}] Chuẩn bị buổi họp nhóm"
        db.add(
            PlanningDraft(
                id=draft_id,
                user_id=owner,
                client_draft_id="w4-03a-draft",
                stage="collecting",
                input_source="text",
                raw_input=title,
                summary=TaskSummary(title=title, can_plan=False).model_dump(mode="json"),
            )
        )

    task_id = uuid5(owner, "w4-03a:history-task")
    if await db.get(Task, task_id) is not None:
        return

    # Dữ liệu tổng hợp mô phỏng một phiên đã được xác nhận ở UF5.
    timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = datetime.now(timezone).date() - timedelta(days=1)
    started = datetime.combine(yesterday, time(9), timezone)
    work_seconds = 1500
    ended = started + timedelta(seconds=work_seconds)
    responded = ended + timedelta(seconds=15)
    source_id = uuid5(owner, "w4-03a:history-draft")
    step_id = uuid5(owner, "w4-03a:history-step")
    session_id = uuid5(owner, "w4-03a:history-session")
    run_id = uuid5(owner, "w4-03a:history-run")
    constraint = TimeConstraint(
        kind="deadline",
        start_at=started,
        deadline_at=started + timedelta(hours=2),
        input_timezone=timezone.key,
    )
    summary = TaskSummary(
        title=f"[W4 QA {label}] Ôn lại ghi chú buổi họp",
        description="Công việc mẫu đã xác nhận hoàn thành qua Check-in.",
        scope="Một trang ghi chú",
        time_constraint=constraint,
        availability=DraftAvailability(
            timezone=timezone.key,
            date_overrides=[
                DateOverride(
                    date=yesterday,
                    intervals=[LocalInterval(start_local=time(9), end_local=time(11))],
                )
            ],
        ),
        can_plan=True,
    )
    snapshot = summary.model_dump(mode="json")
    db.add(
        PlanningDraft(
            id=source_id,
            user_id=owner,
            client_draft_id="w4-03a-history",
            stage="confirmed",
            input_source="text",
            raw_input=summary.title,
            summary={**snapshot, "_api_meta": {"confirmed_task_id": str(task_id)}},
            summary_confirmed_revision=1,
        )
    )
    await db.flush()
    db.add(
        Task(
            id=task_id,
            user_id=owner,
            source_draft_id=source_id,
            title=snapshot["title"],
            description=summary.description,
            scope=summary.scope,
            time_constraint=constraint.model_dump(mode="json"),
            availability=snapshot["availability"],
            source_summary=snapshot,
            source_summary_revision=1,
            source_planning_input_revision=1,
            status="completed",
        )
    )
    await db.flush()
    db.add(
        Step(
            id=step_id,
            user_id=owner,
            task_id=task_id,
            title="Tóm tắt ba ý chính",
            expected_output="Ba gạch đầu dòng",
            order_index=0,
            estimated_base_seconds=1250,
            remaining_base_seconds=0,
            buffer_multiplier=1.2,
            estimate_source="manual",
            status="completed",
            created_at=started,
            updated_at=responded,
        )
    )
    await db.flush()
    db.add(
        Session(
            id=session_id,
            user_id=owner,
            step_id=step_id,
            scheduled_start=started,
            scheduled_end=ended,
            start_decision_due_at=started,
            micro_task_text="Mở ghi chú buổi họp",
            micro_task_seconds=120,
            work_seconds=work_seconds,
            rest_seconds=0,
            duration_seconds=work_seconds,
            segments=[{"order_index": 0, "kind": "work", "duration_seconds": work_seconds}],
            schedule_revision=1,
            start_state="started",
            status="completed",
        )
    )
    await db.flush()
    db.add(
        SessionRun(
            id=run_id,
            user_id=owner,
            session_id=session_id,
            device_id="w4-03a-qa",
            started_at=started,
            ended_at=ended,
            run_state="ended",
            active_phase="ended",
            work_elapsed_seconds=work_seconds,
            actual_work_seconds=work_seconds,
            actual_source="timer_proxy",
            outcome="completed",
            settings_snapshot={"buffer_multiplier": 1.2},
        )
    )
    await db.flush()
    db.add(
        CheckIn(
            user_id=owner,
            run_id=run_id,
            kind="final",
            response_state="answered",
            session_outcome="completed",
            step_completed=True,
            task_completed=True,
            asked_at=ended,
            responded_at=responded,
            response_revision=1,
        )
    )


async def seed_qa(database_url: str, emails: tuple[str, str]) -> str:
    engine = make_engine(database_url)
    try:
        async with session_factory(engine).begin() as db:
            users = {
                user.email: user
                for user in await db.scalars(
                    select(User)
                    .where(User.email.in_(emails), User.is_active.is_(True), User.google_sub != "")
                    .order_by(User.id)
                    .with_for_update()
                )
            }
            missing = [
                label for label, email in zip("AB", emails, strict=True) if email not in users
            ]
            if missing:
                raise ValueError(
                    f"Chờ đăng nhập Google trên DB QA cho tài khoản {', '.join(missing)}."
                )
            for label, email in zip("AB", emails, strict=True):
                await seed_account(db, users[email], label)
        return engine.dialect.name
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("emails", nargs=2, metavar="EMAIL", help="Email Google QA A và B")
    args = parser.parse_args()
    email_a, email_b = (value.strip() for value in args.emails)
    if email_a.casefold() == email_b.casefold():
        parser.error("Cần email của hai tài khoản riêng biệt.")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("Đặt DATABASE_URL trỏ đến DB QA đã chạy alembic upgrade head.")
    try:
        dialect = asyncio.run(seed_qa(database_url, (email_a, email_b)))
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"W4-03A: Đã kiểm tra seed cho hai tài khoản trên {dialect}.")


if __name__ == "__main__":
    main()
