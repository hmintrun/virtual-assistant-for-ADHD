# Tầng Service điều phối nghiệp vụ cho bản nháp công việc UF1 (PlanningDraft)
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import dev_seed_enabled
from app.models.base import Base
from app.models.calendar import PlanningContext as PlanningContextModel
from app.models.planning import PlanCandidate as PlanCandidateModel
from app.models.planning import PlanningDraft as PlanningDraftModel
from app.models.settings import Availability as AvailabilityModel
from app.models.task import Task as TaskModel
from app.models.user import User as UserModel
from app.models.user import UserPreference as UserPreferenceModel
from app.repositories import run_repository, thought_repository
from app.schemas.availability import Availability, DraftAvailability
from app.schemas.common import (
    AvailabilityRef,
    CandidateRef,
    ClarificationIssue,
    FieldError,
    FieldSource,
    TimeConstraint,
)
from app.schemas.draft import (
    ConfirmSummaryRequest,
    ConfirmSummaryResult,
    CreateDraftRequest,
    DraftAnalyzeRequest,
    DraftListData,
    PlanningDraft,
    SummaryInput,
    TaskSummary,
    UpdateDraftRequest,
)
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMTaskExtractionPayload,
    TaskExtractionResult,
)
from app.services.ai_service import UF1_FALLBACK_NOTICE, UF1_FALLBACK_QUESTIONS, ai_service
from app.services.command_service import durable_command, lock_owner
from app.services.scheduler_engine import validate_planning_prerequisites


def _build_seed_draft(draft_id: str, client_draft_id: str) -> PlanningDraft:
    """Tạo dữ liệu mẫu chuẩn theo Dòng 4 trên API Sheet (dùng tạm trước khi ghép DB ở Bước 5.1)."""
    return PlanningDraft(
        draft_id=draft_id,
        client_draft_id=client_draft_id,
        stage="summary_review",
        raw_input="Tìm tài liệu và viết bản nháp, chỉ làm vào giờ rảnh đã xác nhận.",
        input_source="text",
        summary=TaskSummary(
            title="Viết báo cáo môn học",
            description="Tìm tài liệu và viết bản nháp báo cáo môn học.",
            goal="Có bản nháp báo cáo để nhóm review.",
            scope="Tìm tài liệu và viết bản nháp; không làm slide.",
            extra_requirements=["Giữ trích dẫn nguồn tài liệu."],
            time_constraint=TimeConstraint(
                kind="window",
                start_at=datetime.fromisoformat("2026-10-30T07:00:00+07:00"),
                finish_by_at=datetime.fromisoformat("2026-11-13T07:00:00+07:00"),
                deadline_at=None,
                input_timezone="Asia/Ho_Chi_Minh",
            ),
            availability_ref=AvailabilityRef(
                availability_id="availability_1",
                revision=2,
            ),
            importance=None,
            clarification_issues=[],
            can_plan=True,
            field_sources={
                "title": FieldSource(source="user_confirmed", source_ref="summary_revision:5"),
                "scope": FieldSource(source="user_confirmed", source_ref="summary_revision:5"),
                "time_constraint": FieldSource(
                    source="user_confirmed", source_ref="summary_revision:5"
                ),
                "availability_ref": FieldSource(
                    source="user_confirmed", source_ref="availability_revision:2"
                ),
                "description": FieldSource(
                    source="user_confirmed", source_ref="summary_revision:5"
                ),
                "goal": FieldSource(source="user_confirmed", source_ref="summary_revision:5"),
                "extra_requirements": FieldSource(
                    source="user_confirmed", source_ref="summary_revision:5"
                ),
            },
        ),
        summary_revision=5,
        summary_confirmed_revision=None,
        candidate_ref=None,
        source_thought_id=None,
        confirmed_task_id=None,
        user_step_proposal=None,
        planning_input_revision=5,
    )


# Phiên bản nguồn giờ rảnh hiện tại theo từng chủ sở hữu (user_id, availability_id) -> revision
_CURRENT_AVAILABILITY_REVISIONS: dict[tuple[str, str], int] = {
    ("user_a", "availability_1"): 2,
    ("user_b", "availability_b_1"): 1,
}

_INITIALIZED_ENGINES: set[int] = set()


def resolve_user_uuid(user_id: str) -> UUID:
    """Ánh xạ nhất quán mã user_id chuỗi (ví dụ 'user_a') sang UUID khóa chính của bảng users."""
    try:
        return UUID(user_id)
    except ValueError:
        return uuid5(NAMESPACE_DNS, f"proplan.user:{user_id}")


def resolve_draft_uuid(draft_id: str) -> UUID:
    """Ánh xạ nhất quán mã draft_id chuỗi (ví dụ 'draft_025') sang UUID khóa chính của bảng planning_drafts."""
    try:
        return UUID(draft_id)
    except ValueError:
        return uuid5(NAMESPACE_DNS, f"proplan.draft:{draft_id}")


def _serialize_summary_payload(draft: PlanningDraft) -> dict[str, Any]:
    """Đóng gói TaskSummary kèm metadata API vào cột JSON summary của bảng planning_drafts."""
    summary_dict = draft.summary.model_dump(mode="json")
    summary_dict["_api_meta"] = {
        "ai_context": draft.ai_context,
        "draft_id": draft.draft_id,
        "candidate_ref": (
            draft.candidate_ref.model_dump(mode="json") if draft.candidate_ref is not None else None
        ),
        "confirmed_task_id": draft.confirmed_task_id,
    }
    return summary_dict


def _fallback_notice() -> ClarificationIssue:
    return ClarificationIssue(
        code="AI_FALLBACK_NOTICE",
        field_path="summary",
        message="AI đang tạm gián đoạn. Bạn vẫn có thể kiểm tra và xác nhận trên biểu mẫu.",
        question=UF1_FALLBACK_NOTICE,
        blocking=False,
    )


def _restore_summary(raw_summary: dict[str, Any]) -> TaskSummary:
    summary = TaskSummary.model_validate(raw_summary)
    issues: list[ClarificationIssue] = []
    restored = False
    for issue in summary.clarification_issues:
        if issue.code == "AI_CLARIFICATION" and issue.field_path.startswith(
            "clarification_questions["
        ):
            if issue.question in UF1_FALLBACK_QUESTIONS.values():
                # Các câu hỏi này đã có bản tương ứng do validator UF1 tạo.
                restored = True
                continue
            if issue.question == UF1_FALLBACK_NOTICE:
                issue = _fallback_notice()
                restored = True
        if issue not in issues:
            issues.append(issue)
    if restored:
        return summary.model_copy(
            update={"clarification_issues": issues, "can_plan": not any(i.blocking for i in issues)}
        )
    return summary


def _row_to_schema(row: PlanningDraftModel, fallback_draft_id: str | None = None) -> PlanningDraft:
    """Chuyển dòng SQLAlchemy PlanningDraftModel thành DTO PlanningDraft chuẩn hợp đồng."""
    raw_summary = dict(row.summary or {})
    api_meta = raw_summary.pop("_api_meta", {})
    if not isinstance(api_meta, dict):
        api_meta = {}
    draft_id_str = str(api_meta.get("draft_id") or fallback_draft_id or row.id)
    cand_ref_raw = api_meta.get("candidate_ref")
    cand_ref = CandidateRef.model_validate(cand_ref_raw) if isinstance(cand_ref_raw, dict) else None
    confirmed_task_id = api_meta.get("confirmed_task_id")
    source_thought_id = (
        str(row.source_thought_id) if row.source_thought_id else api_meta.get("source_thought_id")
    )
    return PlanningDraft(
        ai_context=api_meta.get("ai_context") or {},
        draft_id=draft_id_str,
        client_draft_id=row.client_draft_id,
        stage=row.stage,
        raw_input=row.raw_input,
        input_source=cast(Literal["text", "voice", "thought"], row.input_source),
        summary=_restore_summary(raw_summary),
        summary_revision=row.summary_revision,
        summary_confirmed_revision=row.summary_confirmed_revision,
        candidate_ref=cand_ref,
        source_thought_id=source_thought_id,
        confirmed_task_id=confirmed_task_id,
        user_step_proposal=row.user_step_proposal,
        planning_input_revision=row.planning_input_revision,
    )


async def ensure_dev_seed_in_db(db: AsyncSession) -> None:
    """Bước 5.1 (F07, T23): Khởi tạo bảng và seed idempotent cho User A, User B cùng các Draft mẫu."""
    if not dev_seed_enabled():
        return
    if db.info.get("dev_seed_ready"):
        return
    bind = db.get_bind()
    engine_key = id(bind)
    if engine_key not in _INITIALIZED_ENGINES:
        conn = await db.connection()
        await conn.run_sync(Base.metadata.create_all)
        _INITIALIZED_ENGINES.add(engine_key)

    seed_users = (
        ("user_a", "user_a@proplan.local", "User A (Dev)"),
        ("user_b", "user_b@proplan.local", "User B (Dev)"),
    )
    changed = False
    for raw_uid, email, display_name in seed_users:
        uid = resolve_user_uuid(raw_uid)
        existing_user = await db.get(UserModel, uid)
        if existing_user is None:
            db.add(
                UserModel(
                    id=uid,
                    email=email,
                    display_name=display_name,
                    is_active=True,
                )
            )
            changed = True

        existing_pref = await db.scalar(
            select(UserPreferenceModel).where(UserPreferenceModel.user_id == uid)
        )
        if existing_pref is None:
            db.add(
                UserPreferenceModel(
                    user_id=uid,
                    work_interval_seconds=1500,
                    rest_interval_seconds=300,
                    effective_revision=1,
                    buffer_multiplier=1.2,
                    reminder_style="gentle",
                    micro_task_seconds=120,
                    reminder_offsets=[900, 600, 300],
                )
            )
            changed = True

    if changed:
        await db.flush()

    seed_drafts = (
        ("user_a", "draft_1", "draft_may_1"),
        ("user_a", "draft_025", "draft_may_025"),
        ("user_b", "draft_b_1", "draft_may_b_1"),
    )
    for raw_uid, raw_did, client_did in seed_drafts:
        did = resolve_draft_uuid(raw_did)
        existing_draft = await db.get(PlanningDraftModel, did)
        if existing_draft is None:
            seed_dto = _build_seed_draft(raw_did, client_did)
            db.add(
                PlanningDraftModel(
                    id=did,
                    user_id=resolve_user_uuid(raw_uid),
                    client_draft_id=seed_dto.client_draft_id,
                    stage=seed_dto.stage,
                    raw_input=seed_dto.raw_input,
                    input_source=seed_dto.input_source,
                    summary=_serialize_summary_payload(seed_dto),
                    summary_revision=seed_dto.summary_revision,
                    summary_confirmed_revision=seed_dto.summary_confirmed_revision,
                    planning_input_revision=seed_dto.planning_input_revision,
                    user_step_proposal=seed_dto.user_step_proposal,
                )
            )
            changed = True

    if changed:
        await db.commit()
    db.info["dev_seed_ready"] = True


async def _validate_source_thought(
    db: AsyncSession, user_id: str, draft_id: str, source_thought_id: str | None
) -> UUID | None:
    if source_thought_id is None:
        return None
    try:
        thought_id = UUID(source_thought_id)
    except ValueError:
        thought_id = None
    owner = resolve_user_uuid(user_id)
    thought = (
        await thought_repository.get_thought(db, owner, thought_id)
        if thought_id is not None
        else None
    )
    if thought is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy suy nghĩ nguồn.",
            next_action="reload",
        )
    if thought.source_run_id is not None:
        run = await run_repository.get_run(db, owner, thought.source_run_id)
        if run is None or run.ended_at is None:
            raise AppError(
                status_code=409,
                code="SESSION_STILL_RUNNING",
                message="Chỉ tạo nháp từ suy nghĩ sau khi phiên nguồn kết thúc.",
                next_action="wait_for_session_end",
            )
    try:
        links = await thought_repository.get_links(db, owner, [thought.id])
    except MultipleResultsFound:
        raise AppError(
            status_code=409,
            code="SOURCE_CHANGED",
            message="Dữ liệu cũ có nhiều nháp cùng nguồn suy nghĩ; cần đối soát liên kết.",
            next_action="reload_and_review",
        ) from None
    linked = links.get(thought.id)
    if linked is not None and linked.draft.id != resolve_draft_uuid(draft_id):
        raise AppError(
            status_code=409,
            code="THOUGHT_ALREADY_LINKED",
            message="Suy nghĩ này đã có bản nháp.",
            next_action="open_linked_draft",
        )
    return thought.id


async def _persist_draft_to_db(
    db: AsyncSession | None,
    user_id: str,
    draft: PlanningDraft,
    *,
    expected: PlanningDraft | None = None,
) -> None:
    """Ghi DB trước khi cập nhật cache; kiểm phiên bản ngay trong câu UPDATE."""
    if db is not None:
        await ensure_dev_seed_in_db(db)
        did = resolve_draft_uuid(draft.draft_id)
        uid = resolve_user_uuid(user_id)
        if draft.source_thought_id is not None:
            await lock_owner(db, user_id)
        source_thought_id = await _validate_source_thought(
            db, user_id, draft.draft_id, draft.source_thought_id
        )
        if source_thought_id is not None:
            draft.source_thought_id = str(source_thought_id)
        values = {
            "stage": draft.stage,
            "raw_input": draft.raw_input,
            "input_source": draft.input_source,
            "summary": _serialize_summary_payload(draft),
            "summary_revision": draft.summary_revision,
            "summary_confirmed_revision": draft.summary_confirmed_revision,
            "planning_input_revision": draft.planning_input_revision,
            "user_step_proposal": draft.user_step_proposal,
            "source_thought_id": source_thought_id,
        }
        if expected is not None:
            written_id = await db.scalar(
                update(PlanningDraftModel)
                .where(
                    PlanningDraftModel.id == did,
                    PlanningDraftModel.user_id == uid,
                    PlanningDraftModel.summary_revision == expected.summary_revision,
                    PlanningDraftModel.planning_input_revision == expected.planning_input_revision,
                    PlanningDraftModel.summary_confirmed_revision.is_(None)
                    if expected.summary_confirmed_revision is None
                    else PlanningDraftModel.summary_confirmed_revision
                    == expected.summary_confirmed_revision,
                    PlanningDraftModel.stage == expected.stage,
                )
                .values(**values, revision=PlanningDraftModel.revision + 1)
                .returning(PlanningDraftModel.id)
                .execution_options(synchronize_session=False)
            )
            if written_id is None:
                await db.rollback()
                raise AppError(
                    status_code=409,
                    code="REVISION_CONFLICT",
                    message="Bản nháp đã thay đổi. Hãy tải lại trước khi thực hiện tiếp.",
                    next_action="reload_and_review",
                )
        else:
            row = await db.get(PlanningDraftModel, did)
            if row is None:
                db.add(
                    PlanningDraftModel(
                        id=did, user_id=uid, client_draft_id=draft.client_draft_id, **values
                    )
                )
            else:
                if row.user_id != uid:
                    raise AppError(
                        status_code=404,
                        code="RESOURCE_NOT_FOUND",
                        message="Không tìm thấy bản nháp.",
                        next_action="reload",
                    )
                for name, value in values.items():
                    setattr(row, name, value)
        if not db.info.get("defer_command_commit"):
            await db.commit()
        else:
            await db.flush()


async def get_user_availability(user_id: str, db: AsyncSession) -> Availability | None:
    """Đọc nguồn giờ rảnh thật dùng chung cho GET /availability và phép kiểm UF1."""
    await ensure_dev_seed_in_db(db)
    row = await db.scalar(
        select(AvailabilityModel)
        .where(AvailabilityModel.user_id == resolve_user_uuid(user_id))
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    return Availability(
        availability_id=str(row.id),
        revision=row.revision,
        timezone=row.timezone,
        weekly_rules=row.weekly_rules,
        date_overrides=row.date_overrides,
        task_limits=row.task_limits,
        source=row.source,
        confirmed_at=row.confirmed_at,
        effective_intervals=None,
        calendar_context=None,
    )


def _free_windows(availability: Availability | DraftAvailability) -> list[dict[str, Any]]:
    windows = []
    for rule in availability.weekly_rules:
        windows.append(
            {
                "kind": "weekly_rule",
                "weekday": rule.weekday,
                "start_local": rule.start_local.strftime("%H:%M"),
                "end_local": rule.end_local.strftime("%H:%M"),
                "raw_phrase": f"Thứ ISO {rule.weekday}: {rule.start_local:%H:%M}–{rule.end_local:%H:%M}",
            }
        )
    for override in availability.date_overrides:
        for interval in override.intervals:
            windows.append(
                {
                    "kind": "specific_date",
                    "date": override.date.isoformat(),
                    "start_local": interval.start_local.strftime("%H:%M"),
                    "end_local": interval.end_local.strftime("%H:%M"),
                    "raw_phrase": f"{override.date}: {interval.start_local:%H:%M}–{interval.end_local:%H:%M}",
                }
            )
    return windows


def _merge_free_windows(previous, incoming):
    """Chỉ hoàn thiện cùng ngày/thứ và các mốc đã rõ phải khớp; không đoán ngày."""
    result = list(previous)
    for item in incoming:
        identity = (item.kind, item.weekday, item.date, item.start_local, item.end_local)
        if any(
            (old.kind, old.weekday, old.date, old.start_local, old.end_local) == identity
            and old.is_ambiguous == item.is_ambiguous
            for old in result
        ):
            continue
        result = [
            old
            for old in result
            if not (
                old.is_ambiguous
                and not item.is_ambiguous
                and (old.kind, old.weekday, old.date) == (item.kind, item.weekday, item.date)
                and (old.start_local is None or old.start_local == item.start_local)
                and (old.end_local is None or old.end_local == item.end_local)
            )
        ]
        result.append(item)
    return result


def _availability_proposal(extracted, draft, legacy, timezone_name):
    """Trả bản giờ rảnh hợp lệ + các khung mơ hồ còn cần user làm rõ."""
    context = draft.ai_context
    base = draft.summary.availability or legacy
    previous = [
        ExtractedFreeWindowSchema.model_validate(item)
        for item in context.get("availability_windows", _free_windows(base) if base else [])
    ]
    incoming = extracted.structured_free_windows
    if not incoming and extracted.availability_update_mode != "replace":
        return None, None
    windows = (
        list(incoming)
        if extracted.availability_update_mode == "replace"
        else _merge_free_windows(previous, incoming)
    )
    weekly, dates, unresolved = [], {}, []
    for item in windows:
        if item.is_ambiguous:
            unresolved.append(item.model_dump(mode="json"))
            continue
        interval = {"start_local": item.start_local, "end_local": item.end_local}
        try:
            if item.kind == "weekly_rule":
                checked = DraftAvailability(
                    timezone=timezone_name, weekly_rules=[{**interval, "weekday": item.weekday}]
                )
                weekly.extend(checked.weekly_rules)
            else:
                checked = DraftAvailability(
                    timezone=timezone_name,
                    date_overrides=[{"date": item.date, "intervals": [interval]}],
                )
                dates.setdefault(item.date, []).extend(checked.date_overrides[0].intervals)
        except ValidationError:
            item.is_ambiguous = True
            unresolved.append(item.model_dump(mode="json"))
    # Không để AI làm mất ngoại lệ ngày không rảnh đã có chỉ vì nó không có window.
    if base is not None and extracted.availability_update_mode != "replace":
        for override in base.date_overrides:
            if not override.intervals:
                dates.setdefault(override.date.isoformat(), [])
    proposal = DraftAvailability(
        timezone=timezone_name,
        weekly_rules=weekly,
        date_overrides=[{"date": day, "intervals": values} for day, values in dates.items()],
    )
    # Giữ tương thích nháp cũ: AI chỉ nhắc lại giờ đã chọn không tự nâng sang nguồn mới.
    if draft.summary.availability is None and legacy is not None and not unresolved:
        old = DraftAvailability(
            timezone=legacy.timezone,
            weekly_rules=legacy.weekly_rules,
            date_overrides=legacy.date_overrides,
        )
        if proposal == old:
            return None, None
    return proposal, (windows, unresolved)


async def _summary_availability(
    summary: TaskSummary,
    user_id: str,
    db: AsyncSession | None,
) -> Availability | None:
    """Không tự nhận giờ rảnh do AI suy ra là nguồn đã được user xác nhận."""
    if summary.availability is not None:
        return None
    ref = summary.availability_ref
    if db is None or ref is None:
        return None
    availability = await get_user_availability(user_id, db)
    if availability is None or availability.availability_id != ref.availability_id:
        return None
    return availability


async def _evaluate_summary_for_user(
    summary: TaskSummary,
    user_id: str,
    db: AsyncSession | None,
) -> tuple[list[ClarificationIssue], bool]:
    if db is None:
        return _evaluate_summary_issues(summary)
    availability = await _summary_availability(summary, user_id, db)
    can_plan, issues = validate_planning_prerequisites(
        summary.model_copy(update={"clarification_issues": []}),
        availability,
        reference_now=datetime.now(UTC),
    )
    if (
        availability is not None
        and summary.availability_ref is not None
        and summary.availability_ref.revision != availability.revision
    ):
        issues.append(
            ClarificationIssue(
                code="SOURCE_CHANGED",
                field_path="availability_ref.revision",
                message="Bộ giờ rảnh đã thay đổi so với bản đang chọn.",
                question="Bạn kiểm tra và chọn lại phiên bản giờ rảnh hiện tại nhé?",
                blocking=True,
            )
        )
    return issues, can_plan and not any(issue.blocking for issue in issues)


def _evaluate_summary_issues(summary: TaskSummary) -> tuple[list[ClarificationIssue], bool]:
    """Kiểm tra 4 thông tin bắt buộc của UF1 (Nội dung, Phạm vi, Khung giờ rảnh, Thời hạn) để lập danh sách câu hỏi."""
    if summary.availability is not None:
        can_plan, issues = validate_planning_prerequisites(
            summary.model_copy(update={"clarification_issues": []}),
            None,
            reference_now=datetime.now(UTC),
        )
        return issues, can_plan
    issues: list[ClarificationIssue] = []
    if not summary.description and not summary.title:
        issues.append(
            ClarificationIssue(
                code="DESCRIPTION_REQUIRED",
                field_path="description",
                message="Chưa có nội dung công việc cần thực hiện.",
                question="Bạn muốn thực hiện công việc cụ thể nào?",
                blocking=True,
            )
        )
    if not summary.scope:
        issues.append(
            ClarificationIssue(
                code="SCOPE_REQUIRED",
                field_path="scope",
                message="Chưa rõ đầu ra cần đạt.",
                question="Kết quả cụ thể bạn muốn có là gì?",
                blocking=True,
            )
        )
    if summary.time_constraint is None:
        issues.append(
            ClarificationIssue(
                code="TIME_CONSTRAINT_REQUIRED",
                field_path="time_constraint",
                message="Chưa có mốc bắt đầu và hoàn tất.",
                question="Bạn muốn bắt đầu khi nào và cần xong trước lúc nào?",
                blocking=True,
            )
        )
    if summary.availability_ref is None:
        issues.append(
            ClarificationIssue(
                code="AVAILABILITY_REQUIRED",
                field_path="availability_ref",
                message="Chưa có bộ giờ rảnh do người dùng xác nhận.",
                question="Bạn có thể làm công việc vào những khoảng nào?",
                blocking=True,
            )
        )
    can_plan = not any(issue.blocking for issue in issues)
    return issues, can_plan


def _build_initial_summary(raw_input: str | None, summary_in: SummaryInput | None) -> TaskSummary:
    """Khởi tạo TaskSummary từ dữ liệu người dùng nhập khi gọi POST /drafts."""
    field_sources: dict[str, FieldSource] = {}
    if summary_in is not None:
        for field_name in (
            "title",
            "description",
            "goal",
            "scope",
            "time_constraint",
            "availability",
            "availability_ref",
            "importance",
        ):
            if getattr(summary_in, field_name) is not None:
                field_sources[field_name] = FieldSource(
                    source="user_input", source_ref="draft_create"
                )
        if summary_in.extra_requirements:
            field_sources["extra_requirements"] = FieldSource(
                source="user_input", source_ref="draft_create"
            )
        base_summary = TaskSummary(
            title=summary_in.title,
            description=summary_in.description or raw_input,
            goal=summary_in.goal,
            scope=summary_in.scope,
            extra_requirements=list(summary_in.extra_requirements),
            time_constraint=summary_in.time_constraint,
            availability=summary_in.availability,
            availability_ref=summary_in.availability_ref
            if summary_in.availability is None
            else None,
            importance=summary_in.importance,
            clarification_issues=[],
            can_plan=False,
            field_sources=field_sources,
        )
    else:
        if raw_input is not None:
            field_sources["description"] = FieldSource(source="user_input", source_ref="raw_input")
        base_summary = TaskSummary(
            title=None,
            description=raw_input,
            goal=None,
            scope=None,
            extra_requirements=[],
            time_constraint=None,
            availability_ref=None,
            importance=None,
            clarification_issues=[],
            can_plan=False,
            field_sources=field_sources,
        )

    issues, can_plan = _evaluate_summary_issues(base_summary)
    return base_summary.model_copy(
        update={
            "clarification_issues": issues,
            "can_plan": can_plan,
        }
    )


@durable_command("create_draft", PlanningDraft)
async def create_draft(
    *,
    user_id: str,
    idempotency_key: str,
    payload: CreateDraftRequest,
    db: AsyncSession | None = None,
) -> PlanningDraft:
    """Dòng 2 (POST /api/v1/drafts): Tạo bản nháp mới, chống nhân bản qua Idempotency-Key và client_draft_id."""
    if db is None:
        raise AppError(
            status_code=500,
            code="DATABASE_REQUIRED",
            message="Thao tác yêu cầu kết nối cơ sở dữ liệu.",
            next_action="reload",
        )
    await ensure_dev_seed_in_db(db)

    uid = resolve_user_uuid(user_id)
    stmt = select(PlanningDraftModel).where(
        PlanningDraftModel.user_id == uid,
        PlanningDraftModel.client_draft_id == payload.client_draft_id,
    )
    existing_row = (await db.execute(stmt)).scalar_one_or_none()
    if existing_row is not None:
        return _row_to_schema(existing_row)

    new_draft_id = f"draft_{uuid4().hex}"
    summary = _build_initial_summary(payload.raw_input, payload.summary)
    issues, can_plan = await _evaluate_summary_for_user(summary, user_id, db)
    summary = summary.model_copy(update={"clarification_issues": issues, "can_plan": can_plan})
    stage = "summary_review" if summary.can_plan else "collecting"
    new_draft = PlanningDraft(
        draft_id=new_draft_id,
        client_draft_id=payload.client_draft_id,
        stage=stage,
        raw_input=payload.raw_input,
        input_source=payload.input_source,
        summary=summary,
        summary_revision=1,
        summary_confirmed_revision=None,
        candidate_ref=None,
        source_thought_id=payload.source_thought_id,
        confirmed_task_id=None,
        user_step_proposal=None,
        planning_input_revision=1,
    )
    await _persist_draft_to_db(db, user_id, new_draft)
    return new_draft


async def list_drafts(
    *,
    user_id: str,
    limit: int = 20,
    cursor: str | None = None,
    db: AsyncSession | None = None,
) -> DraftListData:
    """Dòng 3 (GET /api/v1/drafts): Liệt kê các bản nháp thuộc về user_id có phân trang."""
    if db is None:
        raise AppError(
            status_code=500,
            code="DATABASE_REQUIRED",
            message="Thao tác yêu cầu kết nối cơ sở dữ liệu.",
            next_action="reload",
        )
    await ensure_dev_seed_in_db(db)
    uid = resolve_user_uuid(user_id)
    rows = (
        (
            await db.execute(
                select(PlanningDraftModel)
                .where(PlanningDraftModel.user_id == uid)
                .order_by(PlanningDraftModel.created_at)
            )
        )
        .scalars()
        .all()
    )
    user_drafts = [_row_to_schema(row) for row in rows]
    start_index = 0
    if cursor is not None:
        for idx, draft in enumerate(user_drafts):
            if draft.draft_id == cursor:
                start_index = idx + 1
                break
        else:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Tham số lọc không hợp lệ.",
                next_action="fix_request",
            )

    sliced = user_drafts[start_index : start_index + limit]
    next_cursor = (
        sliced[-1].draft_id if (start_index + limit) < len(user_drafts) and sliced else None
    )
    return DraftListData(items=sliced, next_cursor=next_cursor)


async def get_draft_by_id(
    *,
    user_id: str,
    draft_id: str,
    db: AsyncSession | None = None,
) -> PlanningDraft:
    """Tìm bản nháp trong phạm vi của user_id qua cơ sở dữ liệu. Không tồn tại hoặc khác chủ sở hữu đều trả 404."""
    if db is None:
        raise AppError(
            status_code=500,
            code="DATABASE_REQUIRED",
            message="Thao tác yêu cầu kết nối cơ sở dữ liệu.",
            next_action="reload",
        )
    await ensure_dev_seed_in_db(db)
    did = resolve_draft_uuid(draft_id)
    uid = resolve_user_uuid(user_id)
    row = (
        await db.execute(
            select(PlanningDraftModel)
            .where(
                PlanningDraftModel.id == did,
                PlanningDraftModel.user_id == uid,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy tài nguyên trong phạm vi bạn được phép truy cập.",
            next_action="reload",
        )
    return _row_to_schema(row, fallback_draft_id=draft_id)


async def delete_draft(
    *,
    user_id: str,
    draft_id: str,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """Xóa vĩnh viễn bản nháp UF1 chưa chốt thành Task chính thức."""
    if db is None:
        raise AppError(
            status_code=500,
            code="DATABASE_REQUIRED",
            message="Thao tác yêu cầu kết nối cơ sở dữ liệu.",
            next_action="reload",
        )
    await ensure_dev_seed_in_db(db)
    did = resolve_draft_uuid(draft_id)
    uid = resolve_user_uuid(user_id)
    row = (
        await db.execute(
            select(PlanningDraftModel)
            .where(
                PlanningDraftModel.id == did,
                PlanningDraftModel.user_id == uid,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy bản nháp.",
            next_action="reload",
        )

    draft_schema = _row_to_schema(row, fallback_draft_id=draft_id)
    has_task = (
        await db.scalar(
            select(TaskModel.id)
            .where(TaskModel.user_id == uid, TaskModel.source_draft_id == row.id)
            .limit(1)
        )
    ) is not None
    if (
        row.stage == "confirmed"
        or draft_schema.confirmed_task_id is not None
        or has_task
    ):
        raise AppError(
            status_code=409,
            code="INVALID_STATE_TRANSITION",
            message="Bản nháp đã chốt thành công việc chính thức, không thể xóa.",
            next_action="reload_and_review",
        )

    await db.execute(
        delete(PlanningContextModel).where(
            PlanningContextModel.user_id == uid,
            PlanningContextModel.source_draft_id == row.id,
        )
    )
    await db.execute(
        delete(PlanCandidateModel).where(
            PlanCandidateModel.user_id == uid,
            PlanCandidateModel.draft_id == row.id,
        )
    )
    await db.delete(row)
    await db.commit()
    return {"draft_id": draft_schema.draft_id, "deleted": True}


@durable_command("patch_draft", PlanningDraft)
async def patch_draft(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: UpdateDraftRequest,
    db: AsyncSession | None = None,
) -> PlanningDraft:
    """Dòng 6 (PATCH /api/v1/drafts/{draft_id}): Sửa đúng trường gửi lên, kiểm revision và cập nhật trạng thái."""
    draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id, db=db)

    # Kiểm tra xung đột revision (409 REVISION_CONFLICT)
    summary_rev_mismatch = (
        payload.expected_summary_revision is not None
        and payload.expected_summary_revision != draft.summary_revision
    )
    input_rev_mismatch = payload.expected_planning_input_revision != draft.planning_input_revision
    if summary_rev_mismatch or input_rev_mismatch:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={
                "summary_revision": draft.summary_revision,
                "planning_input_revision": draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    changes_fields = payload.changes.model_fields_set
    new_raw_input = draft.raw_input
    raw_input_changed = False
    if "raw_input" in changes_fields and payload.changes.raw_input != draft.raw_input:
        new_raw_input = payload.changes.raw_input
        raw_input_changed = True

    new_user_step_proposal = draft.user_step_proposal
    proposal_changed = False
    if (
        "user_step_proposal" in changes_fields
        and payload.changes.user_step_proposal != draft.user_step_proposal
    ):
        new_user_step_proposal = payload.changes.user_step_proposal
        proposal_changed = True

    new_summary = draft.summary
    summary_changed = False
    if "summary" in changes_fields and payload.changes.summary is not None:
        summary_patch = {
            k: getattr(payload.changes.summary, k) for k in payload.changes.summary.model_fields_set
        }
        if "availability" in summary_patch:
            # User sửa/xóa giờ riêng không có nghĩa đồng ý quay về giờ chung cũ.
            summary_patch["availability_ref"] = None
        current_summary_dict = draft.summary.model_dump()
        updated_fields: list[str] = []
        for field_name, new_val in summary_patch.items():
            if field_name == "extra_requirements" and new_val is None:
                new_val = []
            if current_summary_dict.get(field_name) != new_val:
                updated_fields.append(field_name)
                summary_changed = True

        # User có thể chọn giữ giá trị cũ khi trả lời câu hỏi review của AI.
        reviewed_fields = {
            issue.field_path
            for issue in draft.summary.clarification_issues
            if issue.code.startswith("AI_")
            and issue.field_path.split(".")[0] in summary_patch
            and (
                len(payload.changes.summary.model_fields_set) == 1
                or summary_patch[issue.field_path.split(".")[0]]
                != current_summary_dict.get(issue.field_path.split(".")[0])
            )
        }
        if reviewed_fields:
            summary_changed = True
            updated_fields.extend(
                {field.split(".")[0] for field in reviewed_fields} - set(updated_fields)
            )

        if summary_changed:
            next_summary_rev = draft.summary_revision + 1
            new_field_sources = dict(draft.summary.field_sources)
            for field_name in updated_fields:
                new_field_sources[field_name] = FieldSource(
                    source="user_confirmed",
                    source_ref=f"summary_revision:{next_summary_rev}",
                )
            merged_summary = TaskSummary.model_validate(
                {
                    **draft.summary.model_dump(),
                    **{
                        k: ([] if k == "extra_requirements" and v is None else v)
                        for k, v in summary_patch.items()
                    },
                    "field_sources": new_field_sources,
                }
            )
            issues, can_plan = await _evaluate_summary_for_user(merged_summary, user_id, db)
            # Recompute structural errors, but keep AI issues the user has not reviewed.
            issues.extend(
                issue
                for issue in draft.summary.clarification_issues
                if issue.code.startswith("AI_") and issue.field_path not in reviewed_fields
            )
            can_plan = can_plan and not any(issue.blocking for issue in issues)
            new_summary = merged_summary.model_copy(
                update={
                    "clarification_issues": issues,
                    "can_plan": can_plan,
                }
            )

    # Quy tắc tăng revision đúng một lần cho mỗi lần ghi thực sự thay đổi đầu vào
    any_input_changed = raw_input_changed or summary_changed or proposal_changed
    new_planning_input_revision = (
        draft.planning_input_revision + 1 if any_input_changed else draft.planning_input_revision
    )

    if summary_changed:
        new_summary_revision = draft.summary_revision + 1
    elif raw_input_changed:
        new_summary_revision = draft.summary_revision + 1
    else:
        new_summary_revision = draft.summary_revision

    # Sửa raw_input hoặc summary làm mất xác nhận UF1; chỉ sửa user_step_proposal giữ nguyên xác nhận UF1
    if raw_input_changed or summary_changed:
        new_summary_confirmed_revision = None
        new_stage = "summary_review" if new_summary.can_plan else "collecting"
    else:
        new_summary_confirmed_revision = draft.summary_confirmed_revision
        new_stage = draft.stage

    next_context = dict(draft.ai_context)
    if payload.changes.summary is not None:
        edited = payload.changes.summary.model_fields_set
        if "time_constraint" in edited:
            next_context.pop("time_constraint", None)
        if "availability" in edited:
            next_context.pop("availability_windows", None)
            next_context.pop("availability_proposal", None)
    updated_draft = draft.model_copy(
        update={
            "raw_input": new_raw_input,
            "ai_context": next_context,
            "summary": new_summary,
            "user_step_proposal": new_user_step_proposal,
            "summary_revision": new_summary_revision,
            "summary_confirmed_revision": new_summary_confirmed_revision,
            "planning_input_revision": new_planning_input_revision,
            "stage": new_stage,
        }
    )
    await _persist_draft_to_db(db, user_id, updated_draft, expected=draft)
    return updated_draft


@durable_command("confirm_draft_summary", ConfirmSummaryResult)
async def confirm_draft_summary(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: ConfirmSummaryRequest,
    db: AsyncSession | None = None,
) -> ConfirmSummaryResult:
    """Dòng 7 (POST /api/v1/drafts/{draft_id}/confirm-summary): Xác nhận bảng tóm tắt UF1 (A02.2, T04)."""
    draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id, db=db)

    # 1. Kiểm tra phiên bản bảng tóm tắt người dùng đang xem (409 REVISION_CONFLICT)
    if payload.expected_summary_revision != draft.summary_revision:
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message="Phiên bản nguồn đã đổi. Tải bản hiện tại trước khi sửa.",
            current_revisions={"summary_revision": draft.summary_revision},
            next_action="reload_and_review",
        )

    # 2. Kiểm tra điều kiện đủ 4 thông tin bắt buộc của UF1 (A02.2)
    issues, can_plan = await _evaluate_summary_for_user(draft.summary, user_id, db)
    issues.extend(
        issue for issue in draft.summary.clarification_issues if issue.code.startswith("AI_")
    )
    if any(issue.code == "SOURCE_CHANGED" for issue in issues):
        raise AppError(
            status_code=409,
            code="SOURCE_CHANGED",
            message="Bộ giờ rảnh đã thay đổi. Hãy chọn lại trước khi xác nhận.",
            next_action="rebuild_and_review",
        )
    if not can_plan or any(issue.blocking for issue in issues):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Bảng tóm tắt chưa đủ 4 thông tin bắt buộc để chuyển sang lập kế hoạch UF2.",
            field_errors=[
                FieldError(
                    field_path=issue.field_path,
                    code=issue.code,
                    message=issue.message,
                )
                for issue in issues
                if issue.blocking
            ],
            next_action="fix_fields",
        )

    # 3. Kiểm tra nguồn giờ rảnh (availability_ref) có bị lệch revision hiện tại không (409 SOURCE_CHANGED)
    avail_ref = draft.summary.availability_ref
    if db is None and avail_ref is not None:
        # Nháp legacy có thể giữ dict; chuẩn hóa trước khi đọc ID/revision.
        avail_ref = AvailabilityRef.model_validate(avail_ref)
        current_avail_rev = _CURRENT_AVAILABILITY_REVISIONS.get(
            (
                user_id,
                avail_ref.availability_id
                if hasattr(avail_ref, "availability_id")
                else avail_ref["availability_id"],
            )
        )
        ref_rev = avail_ref.revision if hasattr(avail_ref, "revision") else avail_ref["revision"]
        if current_avail_rev is not None and ref_rev != current_avail_rev:
            raise AppError(
                status_code=409,
                code="SOURCE_CHANGED",
                message="Bảng tóm tắt đã được sửa. Hãy tải bản mới trước khi xác nhận.",
                current_revisions={"availability": current_avail_rev},
                next_action="rebuild_and_review",
            )

    # 4. Đóng dấu xác nhận UF1 (T04: Chỉ cập nhật summary_confirmed_revision và stage, TUYỆT ĐỐI chưa tạo Task)
    confirmed_summary = draft.summary
    if draft.summary.availability is not None:
        confirmed_summary = draft.summary.model_copy(
            update={
                "field_sources": {
                    **draft.summary.field_sources,
                    "availability": FieldSource(
                        source="user_confirmed",
                        source_ref=f"summary_revision:{draft.summary_revision}",
                    ),
                }
            }
        )
    confirmed_draft = draft.model_copy(
        update={
            "summary": confirmed_summary,
            "stage": "summary_confirmed",
            "summary_confirmed_revision": draft.summary_revision,
        }
    )
    await _persist_draft_to_db(db, user_id, confirmed_draft, expected=draft)

    result = ConfirmSummaryResult(
        draft_id=confirmed_draft.draft_id,
        stage=confirmed_draft.stage,
        summary_confirmed_revision=confirmed_draft.summary_revision,
        summary_revision=confirmed_draft.summary_revision,
        planning_input_revision=confirmed_draft.planning_input_revision,
    )
    return result


@durable_command("analyze_draft", PlanningDraft)
async def analyze_draft(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: DraftAnalyzeRequest,
    db: AsyncSession | None = None,
) -> PlanningDraft:
    """Dòng 5 (POST /api/v1/drafts/{draft_id}/analyze): Gọi AI trích xuất & làm rõ 1 vòng (I01, A01/A02.1, T03, T18)."""
    # Pha 1: Đọc snapshot hiện tại và kiểm tra cả 2 revision trước khi gọi AI
    snapshot_draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id, db=db)
    if (
        payload.expected_summary_revision != snapshot_draft.summary_revision
        or payload.expected_planning_input_revision != snapshot_draft.planning_input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={
                "summary_revision": snapshot_draft.summary_revision,
                "planning_input_revision": snapshot_draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    # Kiểm nguồn trước provider và kiểm lại lúc lưu để chặn chuyển suy nghĩ trong phiên.
    if db is not None:
        await _validate_source_thought(
            db, user_id, draft_id, payload.source_thought_id or snapshot_draft.source_thought_id
        )

    # Pha 2: Gọi AI ngoài transaction; giữ snapshot làm ngữ cảnh, không tạo Task.
    availability = await _summary_availability(snapshot_draft.summary, user_id, db)
    summary = snapshot_draft.summary
    tc = summary.time_constraint
    timezone_name = (
        summary.availability.timezone
        if summary.availability is not None
        else tc.input_timezone
        if tc is not None
        else availability.timezone
        if availability is not None
        else "Asia/Ho_Chi_Minh"
    )
    base = summary.availability or availability
    windows = snapshot_draft.ai_context.get(
        "availability_windows", _free_windows(base) if base else []
    )
    # Drafts may retain long titles; validate before building the narrower AI DTO.
    if summary.title is not None and len(summary.title) > 100:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Tiêu đề quá dài để phân tích. Hãy rút gọn tiêu đề, giữ chi tiết trong nội dung.",
            field_errors=[
                FieldError(
                    field_path="summary.title",
                    code="string_too_long",
                    message="Tiêu đề tối đa 100 ký tự; nội dung nhập được giữ nguyên.",
                )
            ],
            next_action="fix_fields",
        )
    previous = LLMTaskExtractionPayload(
        title=summary.title,
        goal=summary.goal,
        time_constraint=ExtractedTimeConstraint.model_validate(
            tc.model_dump(mode="json")
            if tc
            else snapshot_draft.ai_context.get("time_constraint") or {}
        ),
        clarification_issues=[
            i
            for i in summary.clarification_issues
            if i.code.startswith("AI_") and i.code != "AI_FALLBACK_NOTICE"
        ],
        content=summary.description,
        scope=summary.scope,
        deadline=(tc.deadline_at if tc.kind == "deadline" else tc.finish_by_at) if tc else None,
        extra_requirements=list(summary.extra_requirements),
        structured_free_windows=windows,
        summary=summary.description or "Bản nháp công việc",
    )
    if db is not None and db.in_transaction():
        await db.rollback()
    input_text = "\n".join(value for value in (payload.raw_input, payload.message) if value)
    try:
        extracted = TaskExtractionResult.model_validate(
            await ai_service.extract_task_intent(
                input_text,
                timezone=timezone_name,
                previous_draft=previous,
                default_free_windows=windows,
            )
        )
    except (TimeoutError, ValidationError) as exc:
        raise AppError(
            status_code=503,
            code="AI_UNAVAILABLE",
            message="Chưa phân tích được nội dung. Bản nháp được giữ nguyên để bạn sửa hoặc thử lại.",
            retryable=True,
            next_action="retry_or_edit_manually",
        ) from exc

    # Pha 3: Kiểm tra lại revision hiện tại của Draft trước khi merge (T03: Chống kết quả AI cũ đè dữ liệu mới)
    current_draft = await get_draft_by_id(user_id=user_id, draft_id=draft_id, db=db)
    if (
        current_draft.summary_revision != snapshot_draft.summary_revision
        or current_draft.planning_input_revision != snapshot_draft.planning_input_revision
    ):
        raise AppError(
            status_code=409,
            code="REVISION_CONFLICT",
            message=(
                "Dữ liệu đã thay đổi so với phiên bản gửi lên. "
                "Tải bản hiện tại và xem lại trước khi gửi yêu cầu mới."
            ),
            current_revisions={
                "summary_revision": current_draft.summary_revision,
                "planning_input_revision": current_draft.planning_input_revision,
            },
            next_action="reload_and_review",
        )

    if availability is not None:
        fresh_availability = await _summary_availability(current_draft.summary, user_id, db)
        if fresh_availability is None or fresh_availability.revision != availability.revision:
            raise AppError(
                status_code=409,
                code="SOURCE_CHANGED",
                message="Bộ giờ rảnh đã đổi trong lúc AI xử lý; hãy tải lại và phân tích lại.",
                next_action="reload_and_review",
            )

    next_summary_rev = current_draft.summary_revision + 1
    next_input_rev = current_draft.planning_input_revision + 1
    new_field_sources = dict(current_draft.summary.field_sources)

    merged_updates: dict[str, object] = {}
    ai_issues: list[ClarificationIssue] = []
    for field_name, proposed_val in (
        ("title", extracted.title),
        ("description", extracted.content),
        ("goal", extracted.goal),
        ("scope", extracted.scope),
    ):
        current_value = getattr(current_draft.summary, field_name)
        if proposed_val is None or proposed_val == current_value:
            continue
        source = current_draft.summary.field_sources.get(field_name)
        if (
            source is not None
            and source.source in {"user_confirmed", "user_input"}
            and source.source_ref != "raw_input"
        ):
            ai_issues.append(
                ClarificationIssue(
                    code="AI_FIELD_REVIEW_REQUIRED",
                    field_path=field_name,
                    message="Kết quả AI khác trường bạn đã chỉnh/xác nhận; giá trị cũ được giữ nguyên.",
                    question=(
                        f"Bạn muốn giữ giá trị hiện tại hay đổi trường {field_name} thành: {proposed_val}? "
                        + extracted.field_review_notes.get(
                            field_name,
                            "Chưa đủ dữ liệu để kết luận bất lợi; bạn có quyền giữ giá trị cũ.",
                        )
                    ),
                    blocking=True,
                )
            )
            continue
        merged_updates[field_name] = proposed_val
        new_field_sources[field_name] = FieldSource(
            source="ai_suggested",
            source_ref=f"summary_revision:{next_summary_rev}",
        )
    requirements = list(current_draft.summary.extra_requirements)
    seen_requirements = {item.strip() for item in requirements}
    for item in extracted.extra_requirements:
        cleaned = item.strip()
        if cleaned and cleaned not in seen_requirements:
            requirements.append(cleaned)
            seen_requirements.add(cleaned)
    if requirements != current_draft.summary.extra_requirements:
        merged_updates["extra_requirements"] = requirements
        new_field_sources["extra_requirements"] = FieldSource(
            source="ai_suggested",
            source_ref=f"summary_revision:{next_summary_rev}",
        )
    context = dict(current_draft.ai_context)
    proposed_availability, availability_state = _availability_proposal(
        extracted, current_draft, availability, timezone_name
    )
    if availability_state is not None:
        proposal_windows, unresolved = availability_state
        old_availability = current_draft.summary.availability
        source = current_draft.summary.field_sources.get("availability")
        protected = source is not None and source.source in {"user_input", "user_confirmed"}
        if proposed_availability != old_availability and protected:
            context["availability_proposal"] = proposed_availability.model_dump(mode="json")
            proposed_label = "; ".join(
                w["raw_phrase"] for w in _free_windows(proposed_availability)
            )
            proposed_label = proposed_label or "không còn khung giờ rảnh"
            ai_issues.append(
                ClarificationIssue(
                    code="AI_AVAILABILITY_REVIEW_REQUIRED",
                    field_path="availability",
                    message="AI đề xuất giờ rảnh khác giá trị bạn đã nhập; giờ cũ được giữ nguyên.",
                    question=f"Bạn muốn giữ giờ rảnh hiện tại hay sửa thành {proposed_label} "
                    f"({proposed_availability.timezone})? "
                    + extracted.field_review_notes.get("availability", ""),
                    blocking=True,
                )
            )
        else:
            context["availability_windows"] = [
                item.model_dump(mode="json") for item in proposal_windows
            ]
            if unresolved:
                phrases = "; ".join(item["raw_phrase"] for item in unresolved)
                ai_issues.append(
                    ClarificationIssue(
                        code="AI_AVAILABILITY_INCOMPLETE",
                        field_path="availability",
                        message="Một số khung giờ rảnh chưa đủ ngày/giờ hợp lệ.",
                        question=f"Bạn làm rõ ngày và giờ bắt đầu–kết thúc cho: {phrases}",
                        blocking=True,
                    )
                )
            else:
                context.pop("availability_windows", None)
            if proposed_availability != old_availability:
                merged_updates["availability"] = proposed_availability
                merged_updates["availability_ref"] = None
                new_field_sources["availability"] = FieldSource(
                    source="ai_suggested", source_ref=f"summary_revision:{next_summary_rev}"
                )
    else:
        unresolved = []
    complete_time: TimeConstraint | None = None
    proposed_time = extracted.time_constraint
    has_proposed_time = proposed_time is not None and any(
        getattr(proposed_time, field) is not None
        for field in ("start_at", "deadline_at", "finish_by_at")
    )
    if has_proposed_time or extracted.deadline is not None:
        old_time = current_draft.summary.time_constraint
        values = (
            old_time.model_dump(mode="json")
            if old_time
            else dict(context.get("time_constraint") or {})
        )
        incoming = (
            proposed_time.model_dump(mode="json", exclude_none=True)
            if proposed_time is not None
            else {}
        )
        if incoming.get("kind") and incoming["kind"] != values.get("kind"):
            values.pop("deadline_at", None)
            values.pop("finish_by_at", None)
        values.update(incoming)
        if (
            values.get("deadline_at") is None
            and values.get("finish_by_at") is None
            and extracted.deadline is not None
        ):
            values["deadline_at"] = extracted.deadline.isoformat()
            values.setdefault("kind", "deadline")

        raw_tz = str(values.get("input_timezone") or timezone_name or "Asia/Ho_Chi_Minh").strip()
        try:
            tz_info = ZoneInfo(raw_tz)
            resolved_tz = raw_tz
        except ZoneInfoNotFoundError:
            try:
                tz_info = ZoneInfo(timezone_name)
                resolved_tz = timezone_name
            except ZoneInfoNotFoundError:
                tz_info = UTC
                resolved_tz = "UTC"
        values["input_timezone"] = resolved_tz
        if values.get("finish_by_at") is not None and values.get("deadline_at") is None:
            values["kind"] = "window"
        else:
            values["kind"] = str(values.get("kind") or "deadline")
            if values["kind"] == "window" and values.get("finish_by_at") is None:
                values["kind"] = "deadline"

        for dt_key in ("start_at", "deadline_at", "finish_by_at"):
            raw_dt = values.get(dt_key)
            if isinstance(raw_dt, str):
                parsed_dt = datetime.fromisoformat(raw_dt)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=tz_info)
                values[dt_key] = parsed_dt.isoformat()
            elif isinstance(raw_dt, datetime):
                parsed_dt = raw_dt if raw_dt.tzinfo is not None else raw_dt.replace(tzinfo=tz_info)
                values[dt_key] = parsed_dt.isoformat()

        end_key = "deadline_at" if values["kind"] == "deadline" else "finish_by_at"
        end_raw = values.get(end_key)
        if end_raw is not None and values.get("start_at") is None:
            now_in_tz = datetime.now(tz_info)
            end_dt = datetime.fromisoformat(str(end_raw))
            if end_dt > now_in_tz:
                values["start_at"] = now_in_tz.isoformat()

        context["time_constraint"] = values
        try:
            complete_time = TimeConstraint.model_validate(values)
        except ValidationError:
            complete_time = None
            ai_issues.append(
                ClarificationIssue(
                    code="AI_TIME_INCOMPLETE",
                    field_path="time_constraint",
                    message="Thời gian trích xuất chưa đầy đủ hoặc chưa hợp lệ.",
                    question="Bạn xác nhận kiểu thời gian, mốc bắt đầu, mốc hoàn tất và múi giờ nhé?",
                    blocking=True,
                )
            )
        if complete_time is not None and complete_time != old_time:
            source = current_draft.summary.field_sources.get("time_constraint")
            if (
                old_time is not None
                and source
                and source.source in {"user_input", "user_confirmed"}
            ):
                ai_issues.append(
                    ClarificationIssue(
                        code="AI_TIME_REVIEW_REQUIRED",
                        field_path="time_constraint",
                        message="AI đề xuất thời gian khác bản bạn đã nhập; bản cũ được giữ nguyên.",
                        question="Bạn muốn giữ thời gian cũ hay sửa theo đề xuất mới?",
                        blocking=True,
                    )
                )
            else:
                merged_updates["time_constraint"] = complete_time
                new_field_sources["time_constraint"] = FieldSource(
                    source="ai_suggested", source_ref=f"summary_revision:{next_summary_rev}"
                )
    ai_issues.extend(
        item.model_copy(
            update={"code": item.code if item.code.startswith("AI_") else f"AI_{item.code}"}
        )
        for item in extracted.clarification_issues
    )
    ambiguous_phrases = [w.raw_phrase for w in extracted.structured_free_windows if w.is_ambiguous]
    generated_availability_questions = {
        "Bạn thường rảnh vào những khung giờ nào để mình xếp phiên tập trung phù hợp?",
        "Khung giờ "
        + ", ".join(f"'{phrase}'" for phrase in ambiguous_phrases)
        + " bạn có thể làm từ mấy giờ đến mấy giờ cụ thể để mình xếp lịch chuẩn xác?",
    }
    if extracted.generation_tier == "tier_3_rule_based":
        ai_issues.append(_fallback_notice())
    for index, question in enumerate(dict.fromkeys(extracted.clarification_questions)):
        if extracted.generation_tier == "tier_3_rule_based" and (
            question in UF1_FALLBACK_QUESTIONS.values() or question == UF1_FALLBACK_NOTICE
        ):
            # Validator UF1 hỏi đúng trường còn thiếu sau khi đã hợp nhất bản tóm tắt.
            continue
        if any(issue.question == question for issue in ai_issues):
            continue
        if question in generated_availability_questions:
            if any(i.field_path == "availability" for i in ai_issues):
                continue
            ai_issues.append(
                ClarificationIssue(
                    code="AI_AVAILABILITY_INCOMPLETE",
                    field_path="availability",
                    message=question,
                    question=question,
                    blocking=True,
                )
            )
            continue
        ai_issues.append(
            ClarificationIssue(
                code="AI_CLARIFICATION",
                field_path=f"clarification_questions[{index}]",
                message=question,
                question=question,
                blocking=True,
            )
        )
    if extracted.has_conflict and not extracted.clarification_questions:
        ai_issues.append(
            ClarificationIssue(
                code="AI_CONFLICT",
                field_path="summary",
                message="AI phát hiện thông tin còn mâu thuẫn.",
                question="Bạn kiểm tra lại thông tin tóm tắt trước khi xác nhận nhé?",
                blocking=True,
            )
        )
    active_keys = {
        f"{i.code}|{i.field_path}|{i.question}": i
        for i in current_draft.summary.clarification_issues
        if i.code.startswith("AI_") and i.code != "AI_FALLBACK_NOTICE"
    }
    for key, issue in active_keys.items():
        if (
            not issue.code.endswith("_REVIEW_REQUIRED")
            and (
                issue.code in {"AI_TIME_INCOMPLETE", "AI_MISSING_TIME_CONSTRAINT"}
                or issue.field_path == "time_constraint"
            )
            and (complete_time is not None or current_draft.summary.time_constraint is not None)
        ):
            continue
        if (
            not issue.code.endswith("_REVIEW_REQUIRED")
            and (
                issue.code in {"AI_AVAILABILITY_INCOMPLETE", "AI_MISSING_AVAILABILITY"}
                or issue.field_path == "availability"
            )
            and availability_state is not None
            and not unresolved
        ):
            continue
        if (
            not issue.code.endswith("_REVIEW_REQUIRED")
            and issue.field_path in {"scope", "title", "description", "goal"}
            and bool(
                merged_updates.get(issue.field_path)
                or getattr(current_draft.summary, issue.field_path, None)
            )
            and (
                extracted.is_complete
                or (
                    extracted.generation_tier != "tier_3_rule_based"
                    and not any(i.field_path == issue.field_path for i in ai_issues)
                )
            )
        ):
            continue
        # Field-review decisions stay with the user; generic semantic resolutions
        # accept exact/short resolved keys, completion, or removal on a non-fallback AI pass.
        raw_code = issue.code.removeprefix("AI_")
        resolved = not issue.code.endswith("_REVIEW_REQUIRED") and (
            extracted.is_complete
            or (
                bool(input_text)
                and (
                    key in extracted.resolved_issue_keys
                    or issue.code in extracted.resolved_issue_keys
                    or raw_code in extracted.resolved_issue_keys
                    or issue.field_path in extracted.resolved_issue_keys
                    or issue.question in extracted.resolved_issue_keys
                    or (
                        extracted.generation_tier != "tier_3_rule_based"
                        and issue.question not in extracted.clarification_questions
                        and not any(
                            i.code == issue.code and i.field_path == issue.field_path
                            for i in ai_issues
                        )
                    )
                )
            )
        )
        if not resolved and issue not in ai_issues:
            ai_issues.append(issue)
    structured_questions = {i.question for i in ai_issues if i.code != "AI_CLARIFICATION"}
    ai_issues = [
        i
        for i in ai_issues
        if i.code != "AI_CLARIFICATION" or i.question not in structured_questions
    ]
    ai_issues = list({(i.code, i.field_path, i.question): i for i in ai_issues}.values())
    merged_summary = TaskSummary.model_validate(
        {
            **current_draft.summary.model_dump(),
            **merged_updates,
            "field_sources": new_field_sources,
        }
    )
    issues, can_plan = await _evaluate_summary_for_user(merged_summary, user_id, db)
    issues.extend(ai_issues)
    can_plan = can_plan and not any(issue.blocking for issue in ai_issues)
    final_summary = merged_summary.model_copy(
        update={
            "clarification_issues": issues,
            "can_plan": can_plan,
        }
    )
    new_raw_input = payload.raw_input if payload.raw_input is not None else current_draft.raw_input
    if payload.message is not None:
        new_raw_input = "\n".join(value for value in (new_raw_input, payload.message) if value)

    updated_draft = current_draft.model_copy(
        update={
            "raw_input": new_raw_input,
            "ai_context": context,
            "source_thought_id": (payload.source_thought_id or current_draft.source_thought_id),
            "summary": final_summary,
            "summary_revision": next_summary_rev,
            "summary_confirmed_revision": None,
            "planning_input_revision": next_input_rev,
            "stage": "summary_review" if can_plan else "collecting",
        }
    )
    await _persist_draft_to_db(db, user_id, updated_draft, expected=snapshot_draft)
    return updated_draft
