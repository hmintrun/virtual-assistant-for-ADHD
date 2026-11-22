# Tầng Service điều phối nghiệp vụ cho bản nháp công việc UF1 (PlanningDraft)
import asyncio
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, cast
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import delete, select, update
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
from app.schemas.availability import (
    Availability,
    DateOverride,
    DraftAvailability,
    LocalInterval,
    WeeklyRule,
)
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
from app.utils.vietnamese_itn import normalize_vietnamese_transcript


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
    except (ValueError, TypeError):
        thought_id = None
    if thought_id is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy suy nghĩ nguồn.",
            next_action="reload",
        )
    owner = resolve_user_uuid(user_id)
    from app.services import thought_service

    thought = await thought_service.ensure_thought_eligible_for_draft_conversion(
        db, owner, thought_id, current_draft_id=resolve_draft_uuid(draft_id)
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


_SAME_TIME_PHRASES: tuple[str, ...] = (
    "cũng giờ đó",
    "giờ đó",
    "như trên",
    "khung giờ đó",
    "cùng giờ đó",
    "y như vậy",
)
_UNANCHORED_REPEAT_PHRASES: tuple[str, ...] = (
    "hằng ngày",
    "hàng ngày",
    "mỗi ngày",
    "ngày nào cũng",
    "cả tuần",
    "tất cả các ngày",
    "mọi ngày",
    "tối nào cũng",
    "sáng nào cũng",
    "chiều nào cũng",
    "sau cuộc họp",
    "sau buổi họp",
    "sau sự kiện",
    "sau giờ họp",
)


def _merge_intervals_1d(intervals: list[LocalInterval]) -> list[LocalInterval]:
    """Hợp (Union) các khoảng LocalInterval 1D trong cùng một ngày; giữ tách biệt các khung rời nhau (#1, #5)."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: (iv.start_local, iv.end_local))
    merged: list[LocalInterval] = [ordered[0]]
    for current in ordered[1:]:
        last = merged[-1]
        if current.start_local <= last.end_local:
            if current.end_local > last.end_local:
                merged[-1] = LocalInterval(
                    start_local=last.start_local,
                    end_local=current.end_local,
                )
        else:
            merged.append(current)
    return merged


def _subtract_intervals_1d(
    base_intervals: list[LocalInterval],
    busy_intervals: list[LocalInterval],
) -> list[LocalInterval]:
    """Hiệu (Difference) cắt các đoạn giờ bận khỏi danh sách khung giờ rảnh 1D trong ngày (#2)."""
    result = _merge_intervals_1d(base_intervals)
    for busy in _merge_intervals_1d(busy_intervals):
        next_result: list[LocalInterval] = []
        for iv in result:
            if busy.end_local <= iv.start_local or busy.start_local >= iv.end_local:
                next_result.append(iv)
            else:
                if iv.start_local < busy.start_local:
                    next_result.append(
                        LocalInterval(start_local=iv.start_local, end_local=busy.start_local)
                    )
                if busy.end_local < iv.end_local:
                    next_result.append(
                        LocalInterval(start_local=busy.end_local, end_local=iv.end_local)
                    )
        result = next_result
    return result


def _item_weekdays(item: ExtractedFreeWindowSchema) -> list[int]:
    result: list[int] = []
    if item.weekdays:
        for w in item.weekdays:
            if 1 <= w <= 7 and w not in result:
                result.append(w)
    if item.weekday is not None and 1 <= item.weekday <= 7 and item.weekday not in result:
        result.append(item.weekday)
    return sorted(result)


def _item_dates(item: ExtractedFreeWindowSchema) -> list[date]:
    if item.date is not None:
        return [date.fromisoformat(item.date)]
    if item.date_from is not None and item.date_to is not None:
        d_start = date.fromisoformat(item.date_from)
        d_end = date.fromisoformat(item.date_to)
        if d_start <= d_end:
            num_days = min(180, (d_end - d_start).days + 1)
            return [d_start + timedelta(days=i) for i in range(num_days)]
    return []


_UF1_QTY = r"(?:\d+|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)"
_UF1_SCOPE_UNIT = (
    r"(?:trang|slide|chương|bài|mục|đề|endpoint|biểu\s+đồ|module|service|báo\s+cáo|video|file|phần)"
)

_FIELD_EXPLICIT_EDIT_PATTERNS: dict[str, re.Pattern[str]] = {
    "title": re.compile(
        r"(?:đổi|sửa|chỉnh|cập\s+nhật|đặt\s+lại|thay(?:\s+đổi)?)\s+(?:lại\s+)?(?:tiêu\s+đề|tên|title)"
        r"|tiêu\s+đề\s+mới",
        re.IGNORECASE,
    ),
    "description": re.compile(
        r"(?:đổi|sửa|chỉnh|cập\s+nhật|thay(?:\s+đổi)?)\s+(?:lại\s+)?(?:nội\s+dung|mô\s+tả|description|công\s+việc)",
        re.IGNORECASE,
    ),
    "goal": re.compile(
        r"(?:đổi|sửa|chỉnh|cập\s+nhật|thay(?:\s+đổi)?)\s+(?:lại\s+)?(?:mục\s+tiêu|goal)",
        re.IGNORECASE,
    ),
    "scope": re.compile(
        r"(?:đổi|sửa|chỉnh|cập\s+nhật|tăng|giảm|thêm|bớt|thay(?:\s+đổi)?)\s+"
        r"(?:lại\s+|sang\s+|thành\s+|lên\s+|xuống\s+)?"
        r"(?:phạm\s+vi|scope|đầu\s+ra|(?:số\s+)?(?:trang|slide|chương|bài|đề)"
        r"|" + _UF1_QTY + r"\s+" + _UF1_SCOPE_UNIT + r"\b)"
        r"|phạm\s+vi\s+(?:mới|là|thành)"
        r"|(?:làm|viết|soạn)\s+" + _UF1_QTY + r"\s+" + _UF1_SCOPE_UNIT
        + r"\b[^.!?\n]{0,30}\bthay\s+vì\b"
        r"|(?:thầy|cô|sếp|nhóm)\s+yêu\s+cầu.*\b(?:trang|slide|chương|bài|đề|module|service)\b",
        re.IGNORECASE,
    ),
    "time_constraint": re.compile(
        r"(?:đổi|sửa|chỉnh|cập\s+nhật|lùi|dời|nới|gia\s+hạn|thay(?:\s+đổi)?)\s+(?:lại\s+)?(?:hạn(?:\s+chót)?|deadline|thời\s+hạn|ngày\s+nộp|giờ\s+nộp|mốc|thời\s+gian)"
        r"|(?:lùi|dời|nới|đổi)\s+(?:lại\s+)?(?:sang|thành|tới|đến)\s+(?:\d{1,2}\s*(?:h|:|g|giờ)|ngày\s+mai|mai|mốt|thứ\s*(?:hai|ba|tư|năm|sáu|bảy|[2-7])|chủ\s*nhật|tuần\s+sau|cuối\s+tuần|(?:ngày\s+)?\d{1,2}/\d{1,2})"
        r"|(?:hạn\s+chót|deadline|thời\s+hạn)\s+(?:mới|đổi|sang|thành|lại)",
        re.IGNORECASE,
    ),
    "availability": re.compile(
        r"(?:đổi|sửa|chỉnh|cập\s+nhật|thêm|bớt|xóa|bỏ|thay(?:\s+đổi)?|mở\s+thêm)\s+(?:lại\s+)?(?:giờ\s+rảnh|khung\s+giờ|lịch\s+rảnh|lịch\s+học|lịch\s+làm|ngoại\s+lệ)"
        r"|\b(?:rảnh|bận)\b.*(?:\d{1,2}\s*(?:h|:|g|giờ)|cả\s+ngày|cuối\s+tuần|hằng\s+ngày|mỗi\s+ngày|thứ\s*(?:hai|ba|tư|năm|sáu|bảy|[2-7])|chủ\s*nhật)"
        r"|(?:dùng|theo|lấy|áp\s+dụng)\s+(?:lại\s+)?(?:lịch|giờ(?:\s+rảnh)?)\s+mặc\s+định",
        re.IGNORECASE,
    ),
}

_DEFAULT_AVAIL_REQUEST_RE = re.compile(
    r"(?:dùng|sử\s+dụng|lấy|áp\s+dụng|theo|giống)\s+(?:lại\s+)?(?:khung\s+)?(?:lịch|giờ(?:\s+rảnh)?)\s+mặc\s+định"
    r"|(?:lịch|giờ(?:\s+rảnh)?)\s+mặc\s+định\s+(?:của\s+mình|đã\s+lưu|nhé|nha|đi)",
    re.IGNORECASE,
)
_DEFAULT_AVAIL_EXCLUDE_RE = re.compile(
    r"giữ\s+nguyên|chỉ\s+dùng\s+cho|không\s+(?:dùng|lấy|đụng|đổi).*mặc\s+định",
    re.IGNORECASE,
)

_UF1_NEGATED_EDIT_RE = re.compile(r"\b(?:không|đừng|chưa)(?:\s+(?:cần|muốn))?\s*$", re.IGNORECASE)
_UF1_KEEP_FIELD_RE = re.compile(r"\bgiữ\s+nguyên\s*$", re.IGNORECASE)
_UF1_KEEP_CURRENT_RE = re.compile(
    r"\bgiữ\s+(?:nguyên|như\s+cũ"
    r"|lại(?:\s+(?:cái|giá\s+trị|mức|bản))?(?:\s+(?:cũ|hiện\s+tại)|(?=\s*[.!?,;\n]|$))"
    r"|giá\s+trị\s+(?:cũ|hiện\s+tại)"
    r"|(?:lại\s+)?(?:tiêu\s+đề|nội\s+dung|mô\s+tả|mục\s+tiêu|phạm\s+vi|đầu\s+ra|thời\s+gian|hạn(?:\s+chót)?|deadline|giờ\s+rảnh|lịch\s+rảnh|khung\s+giờ)\s+(?:cũ|hiện\s+tại))\b"
    r"|\b(?:không|khỏi|thôi)\s+(?:(?:cần|muốn)\s+)?(?:đổi|sửa|chỉnh|cập\s+nhật|thay(?:\s+đổi)?)(?:\s+(?:nữa|lại|thêm))?\b",
    re.IGNORECASE,
)
_UF1_KEEP_ALL_RE = re.compile(r"\b(?:tất\s+cả|toàn\s+bộ|cả\s+hai|hết)\b", re.IGNORECASE)
_UF1_FIELD_MENTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "title": re.compile(r"\b(?:tiêu\s+đề|tên|title)\b", re.IGNORECASE),
    "description": re.compile(r"\b(?:nội\s+dung|mô\s+tả|description|công\s+việc)\b", re.IGNORECASE),
    "goal": re.compile(r"\b(?:mục\s+tiêu|goal)\b", re.IGNORECASE),
    "scope": re.compile(r"\b(?:phạm\s+vi|scope|đầu\s+ra)\b", re.IGNORECASE),
    "time_constraint": re.compile(
        r"\b(?:thời\s+gian|hạn(?:\s+chót)?|deadline|thời\s+hạn|ngày\s+nộp|giờ\s+nộp)\b",
        re.IGNORECASE,
    ),
    "availability": re.compile(
        r"\b(?:giờ\s+rảnh|khung\s+giờ|lịch\s+rảnh|lịch\s+học|lịch\s+làm|ngoại\s+lệ|availability)\b",
        re.IGNORECASE,
    ),
}
_UF1_SCOPE_CHOICE_RE = re.compile(
    r"\b(?:chưa\s+(?:chốt|quyết\s+định|chọn)(?:\s+được)?\s+(?:phạm\s+vi|đầu\s+ra)"
    r"|(?:hai|ba|2|3|các)\s+phương\s+án\s+(?:đều\s+)?(?:đang\s+)?(?:để\s+ngỏ|chưa\s+chốt))\b",
    re.IGNORECASE,
)
_UF1_SCOPE_ALTERNATIVES_RE = re.compile(r"\b(?:hoặc|hay|phương\s+án)\b", re.IGNORECASE)
_UF1_SCOPE_UNDECIDED_RE = re.compile(
    r"\b(?:chưa\s+(?:chốt|quyết\s+định|chọn|biết)(?:\s+được)?|phân\s+vân)\b",
    re.IGNORECASE,
)
_UF1_SCOPE_ALT_DELIVERABLE_RE = re.compile(
    r"\b" + _UF1_QTY + r"\s+" + _UF1_SCOPE_UNIT
    + r"\b[^.!?\n]{0,25}\b(?:hoặc|hay|với|và)\b[^.!?\n]{0,25}\b"
    + _UF1_QTY + r"\s+" + _UF1_SCOPE_UNIT + r"\b"
    + r"|\b(?:phương\s+án|đầu\s+ra|phạm\s+vi)\b[^.!?\n]{0,40}\b(?:hoặc|hay)\b",
    re.IGNORECASE,
)
_UF1_SCOPE_UNIT_RE = re.compile(r"\b" + _UF1_SCOPE_UNIT + r"\b", re.IGNORECASE)
_UF1_SCOPE_SELECTION_RE = re.compile(
    r"(?:^|[.!?;\n,])\s*(?:(?:tôi|mình|em|anh|chị|nhóm|chúng\s+tôi|thôi|vậy|thế|ok|oke)\s+){0,2}"
    r"(?:chốt|chọn|quyết\s+định|lấy)\b"
    r"|(?:^|[.!?;\n,])\s*(?:(?:thôi|vậy|mình|tôi|em|anh|chị)\s+)?(?:làm|viết|soạn|nộp)\s+"
    r"(?:(?:báo\s+cáo|bài|bản|tài\s+liệu|bộ|đề|phần)\s+)?"
    + _UF1_QTY + r"\s+" + _UF1_SCOPE_UNIT + r"\b"
    r"|^\s*(?:(?:báo\s+cáo|bài|bản|tài\s+liệu|bộ)\s+)?"
    + _UF1_QTY + r"\s+" + _UF1_SCOPE_UNIT
    + r"(?:\s+(?:word|pdf|powerpoint|ppt|pptx|excel|docs?|thuyết\s+trình))?"
    + r"\s*(?:(?:thôi|nhé|nha|đi|nhỉ|là\s+được|như\s+vậy)\s*)?[.!]?$"
    r"|\bphương\s+án\s+(?:\d+|một|hai|ba|đầu(?:\s+tiên)?|thứ\s+(?:nhất|hai|ba)|sau|cuối)\b",
    re.IGNORECASE,
)
_UF1_REVIEW_FIELD_LABELS = {
    "title": "tiêu đề",
    "description": "nội dung",
    "goal": "mục tiêu",
    "scope": "phạm vi",
    "time_constraint": "thời gian",
    "availability": "giờ rảnh",
}


def _user_explicitly_edits_field(text: str | None, field_name: str) -> bool:
    """Kiểm tra người dùng có chủ động yêu cầu sửa trường đang bị khóa hay không (RISK-UF1-03)."""
    if not text or not text.strip():
        return False
    pattern = _FIELD_EXPLICIT_EDIT_PATTERNS.get(field_name)
    if pattern is None:
        return False
    return bool(pattern.search(text))


def _uf1_requested_edits(text: str, summary: TaskSummary) -> tuple[str, ...]:
    fields: list[str] = []
    for field, pattern in _FIELD_EXPLICIT_EDIT_PATTERNS.items():
        if getattr(summary, field) is None and not (
            field == "availability" and summary.availability_ref is not None
        ):
            continue
        if any(
            not _UF1_NEGATED_EDIT_RE.search(text[max(0, hit.start() - 30):hit.start()])
            and not _UF1_KEEP_FIELD_RE.search(text[max(0, hit.start() - 30):hit.start()])
            for hit in pattern.finditer(text)
        ):
            fields.append(field)
    return tuple(fields)


def _uf1_kept_pending_fields(
    text: str,
    pending_fields: tuple[str, ...],
    requested_edits: tuple[str, ...],
) -> set[str]:
    if not text.strip() or not pending_fields or not _UF1_KEEP_CURRENT_RE.search(text):
        return set()
    candidates = [field for field in pending_fields if field not in requested_edits]
    if not candidates:
        return set()
    mentioned = {
        field
        for field in candidates
        if (pattern := _UF1_FIELD_MENTION_PATTERNS.get(field)) is not None and pattern.search(text)
    }
    if mentioned:
        return mentioned
    if len(candidates) == 1 or _UF1_KEEP_ALL_RE.search(text):
        return set(candidates)
    return set()


def _is_scope_choice_requirement(text: str) -> bool:
    return bool(
        _UF1_SCOPE_CHOICE_RE.search(text)
        or _UF1_SCOPE_ALT_DELIVERABLE_RE.search(text)
        or (_UF1_SCOPE_ALTERNATIVES_RE.search(text) and _UF1_SCOPE_UNIT_RE.search(text))
    )


def _user_requests_default_availability(text: str | None) -> bool:
    """Kiểm tra người dùng có chủ động yêu cầu dùng lịch/giờ rảnh mặc định hay không (TC-LIVE-14A / IT-2)."""
    if not text or not text.strip():
        return False
    if _DEFAULT_AVAIL_EXCLUDE_RE.search(text):
        return False
    return bool(_DEFAULT_AVAIL_REQUEST_RE.search(text))


def _effective_time_dates(
    effective_time: TimeConstraint | None,
    timezone_name: str,
) -> list[date]:
    if effective_time is None:
        return []
    end_dt = (
        effective_time.deadline_at
        if effective_time.kind == "deadline"
        else effective_time.finish_by_at
    ) or effective_time.deadline_at or effective_time.finish_by_at
    start_dt = effective_time.start_at
    if start_dt is None or end_dt is None:
        return []
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    s_local = (start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=tz)).astimezone(tz).date()
    e_local = (end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=tz)).astimezone(tz).date()
    if s_local > e_local:
        return []
    num_days = min(60, (e_local - s_local).days + 1)
    return [s_local + timedelta(days=i) for i in range(num_days)]


def _convert_dated_interval_timezone(
    d: date,
    start_str: str,
    end_str: str,
    src_tz_name: str,
    dst_tz_name: str,
) -> list[tuple[date, LocalInterval]]:
    """Quy đổi khoảng giờ địa phương của ngày d từ src_tz sang dst_tz, tự tách nếu vắt qua 00:00 (#13)."""
    src_tz = ZoneInfo(src_tz_name)
    dst_tz = ZoneInfo(dst_tz_name)
    s_time = time.fromisoformat(start_str)
    e_time = time.fromisoformat(end_str)
    start_src = datetime.combine(d, s_time, tzinfo=src_tz)
    end_date = d if s_time < e_time else (d + timedelta(days=1))
    end_src = datetime.combine(end_date, e_time, tzinfo=src_tz)
    start_dst = start_src.astimezone(dst_tz)
    end_dst = end_src.astimezone(dst_tz)
    if end_dst <= start_dst:
        return []
    if start_dst.date() == end_dst.date():
        s_t = start_dst.time().replace(second=0, microsecond=0)
        e_t = end_dst.time().replace(second=0, microsecond=0)
        if s_t < e_t:
            return [(start_dst.date(), LocalInterval(start_local=s_t, end_local=e_t))]
        return []
    out: list[tuple[date, LocalInterval]] = []
    s_t = start_dst.time().replace(second=0, microsecond=0)
    if s_t < time(23, 59):
        out.append((start_dst.date(), LocalInterval(start_local=s_t, end_local=time(23, 59))))
    cur_day = start_dst.date() + timedelta(days=1)
    while cur_day < end_dst.date():
        out.append((cur_day, LocalInterval(start_local=time(0, 0), end_local=time(23, 59))))
        cur_day += timedelta(days=1)
    e_t = end_dst.time().replace(second=0, microsecond=0)
    if e_t > time(0, 0):
        out.append((end_dst.date(), LocalInterval(start_local=time(0, 0), end_local=e_t)))
    return out


def _resolve_same_time_inheritance(
    windows: list[ExtractedFreeWindowSchema],
    *,
    seed_start: str | None = None,
    seed_end: str | None = None,
) -> list[ExtractedFreeWindowSchema]:
    """Kế thừa (start_local, end_local) từ khung giờ dương đứng trước khi dùng 'cũng giờ đó' (#6)."""
    resolved: list[ExtractedFreeWindowSchema] = []
    last_start = seed_start
    last_end = seed_end
    for item in windows:
        phrase_low = item.raw_phrase.lower()
        if (
            item.start_local is None
            and item.end_local is None
            and not item.is_excluded
            and item.op in ("add", "replace_day")
            and last_start is not None
            and last_end is not None
            and any(tok in phrase_low for tok in _SAME_TIME_PHRASES)
        ):
            payload = item.model_dump()
            payload["start_local"] = last_start
            payload["end_local"] = last_end
            payload["is_ambiguous"] = False
            payload["clarification_hint"] = None
            item = ExtractedFreeWindowSchema.model_validate(payload)
        if (
            item.start_local is not None
            and item.end_local is not None
            and not item.is_excluded
            and item.op in ("add", "replace_day")
        ):
            last_start = item.start_local
            last_end = item.end_local
        resolved.append(item)
    return resolved


_VN_DURATION_WORD_TO_INT: dict[str, int] = {
    "một": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "tư": 4,
    "năm": 5,
    "sáu": 6,
    "bảy": 7,
    "tám": 8,
    "chín": 9,
    "mười": 10,
    "mười một": 11,
    "mười hai": 12,
}

_VN_NUM_TOKEN_PATTERN = r"\d+|mười\s+một|mười\s+hai|một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười"


def _parse_vn_duration_number(token: str) -> int | None:
    cleaned = re.sub(r"\s+", " ", token.strip().lower())
    if cleaned.isdigit():
        return int(cleaned)
    return _VN_DURATION_WORD_TO_INT.get(cleaned)


def _extract_explicit_duration_minutes(text: str | None) -> int | None:
    """Trích xuất thời lượng tường minh bằng tiếng Việt (1..960 phút) cho câu trả lời Lượt 2 (#8, T4.3)."""
    if not text or not text.strip():
        return None
    text_low = text.strip().lower()
    minutes: int | None = None

    if re.search(r"\bnửa\s+(?:tiếng|giờ)\b", text_low):
        minutes = 30
    elif m_dec := re.search(
        r"(?<!\d)(\d+)[.,](\d+)\s*(?:tiếng(?:\s+đồng\s+hồ)?|giờ\s+đồng\s+hồ)\b",
        text_low,
    ):
        minutes = int(round(float(f"{m_dec.group(1)}.{m_dec.group(2)}") * 60))
    elif m_half_hr := re.search(
        rf"\b(?:(?P<num>{_VN_NUM_TOKEN_PATTERN})\s+)?tiếng\s+rưỡi\b",
        text_low,
    ):
        raw_num = m_half_hr.group("num")
        n_val = _parse_vn_duration_number(raw_num) if raw_num else 1
        if n_val is not None:
            minutes = n_val * 60 + 30
    elif m_gio_ruoi := re.search(
        r"(?:\b(?:làm\s+được|rảnh\s+được|kéo\s+dài|trong\s+vòng)\s+"
        rf"(?P<num>{_VN_NUM_TOKEN_PATTERN})\s*giờ\s+rưỡi\b"
        rf"|\b(?P<num_dh>{_VN_NUM_TOKEN_PATTERN})\s*giờ\s+rưỡi\s+đồng\s+hồ\b)"
        r"(?!\s*(?:sáng|trưa|chiều|tối|đêm)\b)",
        text_low,
    ):
        raw_num = m_gio_ruoi.group("num") or m_gio_ruoi.group("num_dh")
        n_val = _parse_vn_duration_number(raw_num) if raw_num else None
        if n_val is not None:
            minutes = n_val * 60 + 30
    elif m_tieng := re.search(
        rf"\b(?P<num>{_VN_NUM_TOKEN_PATTERN})\s*tiếng(?:\s+đồng\s+hồ)?"
        r"(?:\s*(?P<mins>\d+)\s*(?:phút|p)\b)?(?!\s*rưỡi\b)",
        text_low,
    ):
        n_val = _parse_vn_duration_number(m_tieng.group("num"))
        if n_val is not None:
            extra_m = int(m_tieng.group("mins")) if m_tieng.group("mins") else 0
            minutes = n_val * 60 + extra_m
    elif m_gio_dh := re.search(
        rf"\b(?P<num>{_VN_NUM_TOKEN_PATTERN})\s*giờ\s+đồng\s+hồ"
        r"(?:\s*(?P<mins>\d+)\s*(?:phút|p)\b)?",
        text_low,
    ):
        n_val = _parse_vn_duration_number(m_gio_dh.group("num"))
        if n_val is not None:
            extra_m = int(m_gio_dh.group("mins")) if m_gio_dh.group("mins") else 0
            minutes = n_val * 60 + extra_m
    elif m_gio_ctx := re.search(
        r"\b(?:làm\s+được|rảnh\s+được|kéo\s+dài|trong\s+vòng)\s+"
        rf"(?P<num>{_VN_NUM_TOKEN_PATTERN})\s*giờ\b"
        r"(?:\s*(?P<mins>\d+)\s*(?:phút|p)\b)?"
        r"(?!\s*(?:\d|h|p|phút|rưỡi|sáng|trưa|chiều|tối|đêm|kém|hơn)\b)",
        text_low,
    ):
        n_val = _parse_vn_duration_number(m_gio_ctx.group("num"))
        if n_val is not None:
            extra_m = int(m_gio_ctx.group("mins")) if m_gio_ctx.group("mins") else 0
            minutes = n_val * 60 + extra_m
    else:
        clock_prefix_re = re.compile(
            rf"(?:{_VN_NUM_TOKEN_PATTERN})\s*(?:giờ|h|g|:)\s*$"
        )
        for m_min in re.finditer(r"(?<!\d)(\d+)\s*(?:phút|p)\b", text_low):
            if clock_prefix_re.search(text_low[: m_min.start()]):
                continue
            minutes = int(m_min.group(1))
            break

    if minutes is not None and 1 <= minutes <= 960:
        return minutes
    return None


def _merge_free_windows(previous, incoming):
    """Hợp nhất danh sách khung giờ rảnh qua các lượt trả lời UF1; hỗ trợ #1..#9."""
    result: list[ExtractedFreeWindowSchema] = list(previous)
    seed_start: str | None = None
    seed_end: str | None = None
    for old in result:
        if (
            old.start_local is not None
            and old.end_local is not None
            and not old.is_excluded
            and old.op in ("add", "replace_day")
        ):
            seed_start = old.start_local
            seed_end = old.end_local

    processed_incoming = _resolve_same_time_inheritance(
        list(incoming),
        seed_start=seed_start,
        seed_end=seed_end,
    )

    # Ghép câu trả lời Lượt 2 bổ sung giờ kết thúc hoặc thời lượng cho câu hở đầu ra (#8 "sau 19h30")
    completed_incoming: list[ExtractedFreeWindowSchema] = []
    for item in processed_incoming:
        inferred_duration = (
            item.duration_minutes
            if item.duration_minutes is not None
            else _extract_explicit_duration_minutes(item.raw_phrase)
        )
        if (
            item.start_local is None
            and not item.is_excluded
            and item.op in ("add", "replace_day")
            and (item.end_local is not None or inferred_duration is not None)
        ):
            matched_old = next(
                (
                    old
                    for old in result
                    if old.is_ambiguous
                    and old.start_local is not None
                    and old.end_local is None
                    and (
                        (item.date is not None and item.date == old.date)
                        or (item.weekday is not None and item.weekday == old.weekday)
                        or (item.weekdays and item.weekdays == old.weekdays)
                        or (
                            item.date_from is not None
                            and item.date_from == old.date_from
                            and item.date_to == old.date_to
                        )
                        or (
                            item.date is None
                            and item.weekday is None
                            and not item.weekdays
                            and item.date_from is None
                        )
                    )
                ),
                None,
            )
            if matched_old is not None:
                item_is_unanchored = (
                    item.date is None
                    and item.weekday is None
                    and not item.weekdays
                    and item.date_from is None
                )
                payload = item.model_dump()
                payload["kind"] = (
                    matched_old.kind
                    if item_is_unanchored or item.kind == "weekly_rule"
                    else (item.kind or matched_old.kind)
                )
                payload["date"] = item.date or matched_old.date
                payload["weekday"] = item.weekday or matched_old.weekday
                payload["weekdays"] = item.weekdays or matched_old.weekdays
                payload["date_from"] = item.date_from or matched_old.date_from
                payload["date_to"] = item.date_to or matched_old.date_to
                payload["start_local"] = matched_old.start_local
                if item.end_local is None:
                    payload["duration_minutes"] = inferred_duration
                payload["is_ambiguous"] = False
                payload["clarification_hint"] = None
                item = ExtractedFreeWindowSchema.model_validate(payload)
                result = [old for old in result if old is not matched_old]
        completed_incoming.append(item)

    resolved_incoming = [
        item
        for item in completed_incoming
        if not item.is_ambiguous and not item.is_excluded and item.op != "reset_override"
    ]
    if resolved_incoming:
        has_concrete_anchor = any(
            ri.kind in ("specific_date", "date_range", "daily", "weekly_rule")
            for ri in resolved_incoming
        )
        cleaned_result: list[ExtractedFreeWindowSchema] = []
        for old in result:
            is_unanchored_ambiguous = (
                old.is_ambiguous
                and old.weekday is None
                and not old.weekdays
                and old.date is None
                and old.date_from is None
            )
            if is_unanchored_ambiguous and has_concrete_anchor:
                same_hours = (
                    old.start_local is not None
                    and old.end_local is not None
                    and any(
                        (ri.start_local, ri.end_local) == (old.start_local, old.end_local)
                        for ri in resolved_incoming
                    )
                )
                is_vague_repeat = old.kind in ("weekly_rule", "daily") or any(
                    tok in old.raw_phrase.lower() for tok in _UNANCHORED_REPEAT_PHRASES
                )
                if same_hours or is_vague_repeat:
                    continue
            cleaned_result.append(old)
        result = cleaned_result

    for item in completed_incoming:
        identity = (
            item.kind,
            item.weekday,
            tuple(item.weekdays) if item.weekdays else None,
            item.date,
            item.date_from,
            item.date_to,
            item.start_local,
            item.end_local,
            item.op,
            item.is_excluded,
            item.window_timezone,
        )
        if any(
            (
                old.kind,
                old.weekday,
                tuple(old.weekdays) if old.weekdays else None,
                old.date,
                old.date_from,
                old.date_to,
                old.start_local,
                old.end_local,
                old.op,
                old.is_excluded,
                old.window_timezone,
            )
            == identity
            and old.is_ambiguous == item.is_ambiguous
            for old in result
        ):
            continue

        if item.op == "reset_override":
            reset_dates = {d.isoformat() for d in _item_dates(item)}
            if reset_dates:
                result = [
                    old
                    for old in result
                    if not (old.kind == "specific_date" and old.date in reset_dates)
                ]
            result.append(item)
            continue

        if not item.is_ambiguous:
            next_filtered: list[ExtractedFreeWindowSchema] = []
            for old in result:
                if old.is_ambiguous:
                    # 1. Cùng ngày/thứ hoàn thiện giờ (bao gồm câu hở đầu ra #8)
                    same_target = (old.kind, old.weekday, old.date) == (
                        item.kind,
                        item.weekday,
                        item.date,
                    ) or (
                        old.date is not None and old.date == item.date
                    ) or (
                        old.weekday is not None and old.weekday == item.weekday
                    )
                    if (
                        same_target
                        and (old.start_local is None or old.start_local == item.start_local)
                        and (old.end_local is None or old.end_local == item.end_local)
                    ):
                        continue
                    # 2. Xác nhận lại mâu thuẫn Thứ và Ngày (#7)
                    if old.date is not None and (
                        (
                            item.date is not None
                            and (
                                item.date == old.date
                                or (
                                    old.clarification_hint is not None
                                    and item.date in old.clarification_hint
                                )
                                or (
                                    old.weekday is not None
                                    and date.fromisoformat(item.date).isoweekday() == old.weekday
                                )
                            )
                        )
                        or (item.weekday is not None and item.weekday == old.weekday)
                    ):
                        continue
                elif (
                    item.op == "replace_day"
                    and not old.is_excluded
                    and old.op in ("add", "replace_day")
                ):
                    if (item.date is not None and old.date == item.date) or (
                        item.weekday is not None
                        and old.weekday == item.weekday
                        and old.kind == item.kind
                    ):
                        continue
                next_filtered.append(old)
            result = next_filtered

        result.append(item)
    return result


def _availability_proposal(
    extracted,
    draft,
    legacy,
    timezone_name,
    effective_time: TimeConstraint | None = None,
):
    """Trả bản giờ rảnh hợp lệ (thực thi 4 lớp bất biến) + các khung mơ hồ còn cần user làm rõ."""
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
        _resolve_same_time_inheritance(list(incoming))
        if extracted.availability_update_mode == "replace"
        else _merge_free_windows(previous, incoming)
    )

    unresolved: list[dict[str, Any]] = []
    valid_windows: list[ExtractedFreeWindowSchema] = []
    for item in windows:
        if item.is_ambiguous:
            unresolved.append(item.model_dump(mode="json"))
        else:
            valid_windows.append(item)

    weekly_map: dict[int, list[LocalInterval]] = {}
    dates: dict[str, list[LocalInterval]] = {}
    daily_generated_dates: set[str] = set()

    # Bảo toàn các ngày ngoại lệ đóng (intervals=[]) từ base trước khi chạy Lớp 3 & Lớp 4
    if base is not None and extracted.availability_update_mode != "replace":
        for override in base.date_overrides:
            if not override.intervals:
                dates[override.date.isoformat()] = []

    eff_dates = _effective_time_dates(
        effective_time or draft.summary.time_constraint,
        timezone_name,
    )

    # ── LỚP 1: Khung nền dương (daily & weekly_rule với op in ("add", "replace_day")) ──
    for item in valid_windows:
        if item.is_excluded or item.op not in ("add", "replace_day"):
            continue
        if item.kind not in ("daily", "weekly_rule"):
            continue
        if item.start_local is None or item.end_local is None:
            continue
        try:
            s_time = time.fromisoformat(item.start_local)
            e_time = time.fromisoformat(item.end_local)
            if item.kind == "daily":
                if s_time < e_time:
                    day_ivs = [LocalInterval(start_local=s_time, end_local=e_time)]
                else:
                    parts: list[LocalInterval] = []
                    if s_time < time(23, 59):
                        parts.append(LocalInterval(start_local=s_time, end_local=time(23, 59)))
                    if e_time > time(0, 0):
                        parts.append(LocalInterval(start_local=time(0, 0), end_local=e_time))
                    day_ivs = _merge_intervals_1d(parts)
                for w in range(1, 8):
                    if item.op == "replace_day":
                        weekly_map[w] = list(day_ivs)
                    else:
                        weekly_map[w] = _merge_intervals_1d(weekly_map.get(w, []) + day_ivs)
                for d in eff_dates:
                    d_key = d.isoformat()
                    daily_generated_dates.add(d_key)
                    if item.op == "replace_day":
                        dates[d_key] = list(day_ivs)
                    else:
                        dates[d_key] = _merge_intervals_1d(dates.get(d_key, []) + day_ivs)
            elif item.kind == "weekly_rule":
                target_wds = _item_weekdays(item)
                if s_time < e_time:
                    iv = LocalInterval(start_local=s_time, end_local=e_time)
                    for w in target_wds:
                        if item.op == "replace_day":
                            weekly_map[w] = [iv]
                        else:
                            weekly_map[w] = _merge_intervals_1d(weekly_map.get(w, []) + [iv])
                else:
                    for w in target_wds:
                        if s_time < time(23, 59):
                            iv1 = LocalInterval(start_local=s_time, end_local=time(23, 59))
                            if item.op == "replace_day":
                                weekly_map[w] = [iv1]
                            else:
                                weekly_map[w] = _merge_intervals_1d(weekly_map.get(w, []) + [iv1])
                        next_w = (w % 7) + 1
                        if e_time > time(0, 0):
                            iv2 = LocalInterval(start_local=time(0, 0), end_local=e_time)
                            weekly_map[next_w] = _merge_intervals_1d(
                                weekly_map.get(next_w, []) + [iv2]
                            )
        except (ValidationError, ValueError):
            item.is_ambiguous = True
            unresolved.append(item.model_dump(mode="json"))

    # ── LỚP 2: Loại trừ theo thứ (weekly_rule với is_excluded=True / op="exclude") ──
    for item in valid_windows:
        if item.kind != "weekly_rule" or not (item.is_excluded or item.op == "exclude"):
            continue
        target_wds = _item_weekdays(item)
        if item.start_local is None or item.end_local is None:
            for w in target_wds:
                weekly_map.pop(w, None)
                for d_key in list(daily_generated_dates):
                    if date.fromisoformat(d_key).isoweekday() == w:
                        dates[d_key] = []
        else:
            try:
                busy_iv = LocalInterval(
                    start_local=time.fromisoformat(item.start_local),
                    end_local=time.fromisoformat(item.end_local),
                )
                for w in target_wds:
                    if w in weekly_map:
                        rem = _subtract_intervals_1d(weekly_map[w], [busy_iv])
                        if rem:
                            weekly_map[w] = rem
                        else:
                            weekly_map.pop(w, None)
                    for d_key in list(daily_generated_dates):
                        if date.fromisoformat(d_key).isoweekday() == w and d_key in dates:
                            dates[d_key] = _subtract_intervals_1d(dates[d_key], [busy_iv])
            except (ValidationError, ValueError):
                item.is_ambiguous = True
                unresolved.append(item.model_dump(mode="json"))

    # ── LỚP 3: Đích danh ngày dương (specific_date / date_range với op in ("add", "replace_day")) ──
    for item in valid_windows:
        if item.is_excluded or item.op not in ("add", "replace_day"):
            continue
        if item.kind not in ("specific_date", "date_range"):
            continue
        if item.start_local is None or item.end_local is None:
            continue
        target_dates = _item_dates(item)
        try:
            if item.window_timezone and item.window_timezone != timezone_name:
                replaced_days: set[str] = set()
                for d in target_dates:
                    converted = _convert_dated_interval_timezone(
                        d,
                        item.start_local,
                        item.end_local,
                        item.window_timezone,
                        timezone_name,
                    )
                    for dst_d, iv in converted:
                        d_key = dst_d.isoformat()
                        if item.op == "replace_day" and d_key not in replaced_days:
                            dates[d_key] = [iv]
                            replaced_days.add(d_key)
                        else:
                            base_ivs = (
                                dates[d_key]
                                if d_key in dates
                                else list(weekly_map.get(dst_d.isoweekday(), []))
                            )
                            dates[d_key] = _merge_intervals_1d(base_ivs + [iv])
            else:
                s_time = time.fromisoformat(item.start_local)
                e_time = time.fromisoformat(item.end_local)
                for d in target_dates:
                    d_key = d.isoformat()
                    if s_time < e_time:
                        iv = LocalInterval(start_local=s_time, end_local=e_time)
                        if item.op == "replace_day":
                            dates[d_key] = [iv]
                        else:
                            base_ivs = (
                                dates[d_key]
                                if d_key in dates
                                else list(weekly_map.get(d.isoweekday(), []))
                            )
                            dates[d_key] = _merge_intervals_1d(base_ivs + [iv])
                    else:
                        if s_time < time(23, 59):
                            iv1 = LocalInterval(start_local=s_time, end_local=time(23, 59))
                            if item.op == "replace_day":
                                dates[d_key] = [iv1]
                            else:
                                base_ivs = (
                                    dates[d_key]
                                    if d_key in dates
                                    else list(weekly_map.get(d.isoweekday(), []))
                                )
                                dates[d_key] = _merge_intervals_1d(base_ivs + [iv1])
                        d_next = d + timedelta(days=1)
                        d_next_key = d_next.isoformat()
                        if e_time > time(0, 0):
                            iv2 = LocalInterval(start_local=time(0, 0), end_local=e_time)
                            base_next = (
                                dates[d_next_key]
                                if d_next_key in dates
                                else list(weekly_map.get(d_next.isoweekday(), []))
                            )
                            dates[d_next_key] = _merge_intervals_1d(base_next + [iv2])
        except (ValidationError, ValueError):
            item.is_ambiguous = True
            unresolved.append(item.model_dump(mode="json"))

    # ── LỚP 4: Ngoại lệ ngày cụ thể & Hoàn tác (specific_date / date_range với exclude / reset_override) ──
    for item in valid_windows:
        if item.kind not in ("specific_date", "date_range"):
            continue
        if item.op == "reset_override":
            for d in _item_dates(item):
                dates.pop(d.isoformat(), None)
        elif item.op == "exclude" or item.is_excluded:
            for d in _item_dates(item):
                d_key = d.isoformat()
                if item.start_local is None or item.end_local is None:
                    dates[d_key] = []
                else:
                    try:
                        busy_iv = LocalInterval(
                            start_local=time.fromisoformat(item.start_local),
                            end_local=time.fromisoformat(item.end_local),
                        )
                        current_ivs = (
                            dates[d_key]
                            if d_key in dates
                            else list(weekly_map.get(d.isoweekday(), []))
                        )
                        if current_ivs:
                            dates[d_key] = _subtract_intervals_1d(current_ivs, [busy_iv])
                    except (ValidationError, ValueError):
                        item.is_ambiguous = True
                        unresolved.append(item.model_dump(mode="json"))

    weekly: list[WeeklyRule] = []
    for w in sorted(weekly_map):
        for iv in _merge_intervals_1d(weekly_map[w]):
            weekly.append(
                WeeklyRule(
                    weekday=w,
                    start_local=iv.start_local,
                    end_local=iv.end_local,
                )
            )

    has_any_positive = bool(weekly or any(vals for vals in dates.values()))
    if not has_any_positive and not unresolved and windows:
        # Người dùng chỉ mới báo giờ bận/loại trừ mà chưa có khung giờ rảnh nền (#2)
        for item in windows:
            if item.is_excluded or item.op == "exclude":
                item.is_ambiguous = True
                if not item.clarification_hint:
                    item.clarification_hint = (
                        f"Bạn đã báo khung bận '{item.raw_phrase}', nhưng chưa có khung giờ rảnh nền. "
                        "Bạn thường rảnh vào những khung giờ nào để mình xếp lịch và trừ giờ bận này ra?"
                    )
                unresolved.append(item.model_dump(mode="json"))

    proposal = DraftAvailability(
        timezone=timezone_name,
        weekly_rules=weekly,
        date_overrides=[
            DateOverride(date=date.fromisoformat(day), intervals=_merge_intervals_1d(values))
            for day, values in sorted(dates.items())
        ],
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
        updated_fields: list[str] = []
        for field_name, new_val in summary_patch.items():
            if field_name == "extra_requirements" and new_val is None:
                new_val = []
            current_val = getattr(draft.summary, field_name, None)
            if current_val != new_val:
                updated_fields.append(field_name)
                summary_changed = True

        # User có thể chọn giữ giá trị cũ khi trả lời câu hỏi review của AI.
        review_issue_roots = {
            issue.field_path.split(".")[0]
            for issue in draft.summary.clarification_issues
            if issue.code.startswith("AI_")
            and (
                issue.code.endswith("_REVIEW_REQUIRED")
                or issue.code == "AI_SCOPE_CHOICE_REQUIRED"
            )
        }
        all_sent_fields_under_review = bool(payload.changes.summary.model_fields_set) and (
            payload.changes.summary.model_fields_set <= review_issue_roots
        )
        reviewed_fields: set[str] = set()
        for issue in draft.summary.clarification_issues:
            if not issue.code.startswith("AI_"):
                continue
            root = issue.field_path.split(".")[0]
            if root not in summary_patch:
                continue
            patched_val = summary_patch[root]
            if root == "extra_requirements" and patched_val is None:
                patched_val = []
            if (
                len(payload.changes.summary.model_fields_set) == 1
                or all_sent_fields_under_review
                or patched_val != getattr(draft.summary, root, None)
            ):
                reviewed_fields.add(issue.field_path)
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
            # Recompute structural errors, but keep unreviewed field-specific AI issues.
            # Generic AI_CLARIFICATION/AI_CONFLICT prompts clear once the user completes all 4 UF1 fields.
            retained_field_ai_issues = [
                issue
                for issue in draft.summary.clarification_issues
                if issue.code.startswith("AI_")
                and issue.field_path not in reviewed_fields
                and not (
                    (
                        issue.code == "AI_CLARIFICATION"
                        and issue.field_path.startswith("clarification_questions[")
                    )
                    or (issue.code == "AI_CONFLICT" and issue.field_path == "summary")
                )
            ]
            has_unreviewed_field_blocker = any(
                issue.blocking for issue in retained_field_ai_issues
            )
            issues.extend(retained_field_ai_issues)
            if not (can_plan and not has_unreviewed_field_blocker):
                issues.extend(
                    issue
                    for issue in draft.summary.clarification_issues
                    if (
                        issue.code == "AI_CLARIFICATION"
                        and issue.field_path.startswith("clarification_questions[")
                    )
                    or (issue.code == "AI_CONFLICT" and issue.field_path == "summary")
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


class _AnalyzeFlightLock:
    """Khóa single-flight theo (user_id, draft_id, idempotency_key) chống gọi trùng lặp đồng thời (T4.2)."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.ref_count = 0


_INFLIGHT_ANALYZE_LOCKS: dict[tuple[str, str, str], _AnalyzeFlightLock] = {}


@durable_command("analyze_draft", PlanningDraft)
async def _analyze_draft_impl(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: DraftAnalyzeRequest,
    db: AsyncSession | None = None,
) -> PlanningDraft:
    """Thực thi nội bộ cho POST /api/v1/drafts/{draft_id}/analyze có gắn durable_command (I01, A01/A02.1, T03, T18)."""
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
    user_default_avail = availability or (
        await get_user_availability(user_id, db) if db is not None else None
    )
    default_free_windows = (
        windows
        if windows
        else (_free_windows(user_default_avail) if user_default_avail is not None else [])
    )
    # T4.4 (RISK-01): Cắt mềm tiêu đề > 100 ký tự khi tạo DTO cho AI thay vì ném 422 cứng.
    previous = LLMTaskExtractionPayload(
        title=(summary.title[:100].rstrip() if summary.title else None),
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
    locked_fields = [
        field_name
        for field_name in (
            "title",
            "description",
            "goal",
            "scope",
            "time_constraint",
            "availability",
        )
        if getattr(summary, field_name) is not None
        and not (field_name == "title" and summary.title is not None and len(summary.title) > 100)
        and (src := summary.field_sources.get(field_name)) is not None
        and src.source in {"user_confirmed", "user_input"}
        and src.source_ref != "raw_input"
    ]
    if db is not None and db.in_transaction():
        await db.rollback()
    input_text = "\n".join(value for value in (payload.raw_input, payload.message) if value)
    latest_message = (payload.message or "").strip()
    new_raw_text = (payload.raw_input or "").strip()
    raw_changed = new_raw_text != (snapshot_draft.raw_input or "").strip()
    edit_text = latest_message or (new_raw_text if raw_changed else "")
    never_analyzed = (
        not snapshot_draft.ai_context.get("uf1_analyzed")
        and not any(s.source == "ai_suggested" for s in summary.field_sources.values())
        and not any(i.code.startswith("AI_") for i in summary.clarification_issues)
    )
    latest_text = edit_text or (
        new_raw_text if (snapshot_draft.summary_revision == 1 or never_analyzed) else ""
    )
    requested_edits = _uf1_requested_edits(edit_text, summary)
    pending_fields = tuple(
        dict.fromkeys(
            issue.field_path
            for issue in summary.clarification_issues
            if issue.code == "AI_FALLBACK_REVIEW_REQUIRED"
        )
    )
    kept_pending_fields = _uf1_kept_pending_fields(edit_text, pending_fields, requested_edits)
    active_pending_fields = set(pending_fields) - kept_pending_fields
    reviewed_edits = set(requested_edits)
    if pending_fields and not latest_message and new_raw_text:
        reviewed_edits.update(_uf1_requested_edits(new_raw_text, summary))
    scope_needs_choice = bool(
        (
            _UF1_SCOPE_CHOICE_RE.search(latest_text)
            and _UF1_SCOPE_ALTERNATIVES_RE.search(latest_text)
        )
        or (
            _UF1_SCOPE_UNDECIDED_RE.search(latest_text)
            and _UF1_SCOPE_ALT_DELIVERABLE_RE.search(latest_text)
        )
    )
    scope_choice_pending = any(
        issue.code == "AI_SCOPE_CHOICE_REQUIRED" for issue in summary.clarification_issues
    )
    has_explicit_decision_verb = bool(
        re.search(r"\b(?:chốt|chọn|quyết\s+định)\b", edit_text, re.IGNORECASE)
    )
    user_selected_scope = bool(
        not scope_needs_choice
        and _UF1_SCOPE_SELECTION_RE.search(edit_text)
        and (has_explicit_decision_verb or not _UF1_SCOPE_ALT_DELIVERABLE_RE.search(edit_text))
    )
    needs_scope_decision = scope_needs_choice or (scope_choice_pending and not user_selected_scope)
    normalized_input = normalize_vietnamese_transcript(input_text)
    normalized_followup = (
        normalize_vietnamese_transcript(latest_message) if latest_message else None
    )
    try:
        extracted = TaskExtractionResult.model_validate(
            await ai_service.extract_task_intent(
                normalized_input,
                timezone=timezone_name,
                previous_draft=previous,
                default_free_windows=default_free_windows,
                locked_fields=locked_fields or None,
                followup_message=normalized_followup,
                requested_edit_fields=tuple(sorted(reviewed_edits | active_pending_fields)),
                needs_user_decision=needs_scope_decision,
                retrying_fallback_edit=bool(active_pending_fields),
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
    has_followup_message = bool(payload.message and payload.message.strip())
    used_fallback = extracted.generation_tier == "tier_3_rule_based"
    if used_fallback:
        frozen_fields = set(pending_fields) | set(requested_edits)
    else:
        frozen_fields = set(kept_pending_fields)
    if needs_scope_decision:
        frozen_fields.add("scope")
    for field_name, proposed_val in (
        ("title", extracted.title),
        ("description", extracted.content),
        ("goal", extracted.goal),
        ("scope", extracted.scope),
    ):
        if field_name in frozen_fields:
            continue
        current_value = getattr(current_draft.summary, field_name)
        is_overlong_title = (
            field_name == "title"
            and isinstance(current_value, str)
            and len(current_value) > 100
        )
        if is_overlong_title:
            if proposed_val is not None and proposed_val.strip():
                merged_updates["title"] = proposed_val[:100].rstrip()
                new_field_sources["title"] = FieldSource(
                    source="ai_suggested",
                    source_ref=f"summary_revision:{next_summary_rev}",
                )
            continue
        if proposed_val is None or proposed_val == current_value:
            continue
        source = current_draft.summary.field_sources.get(field_name)
        if (
            source is not None
            and source.source in {"user_confirmed", "user_input"}
            and source.source_ref != "raw_input"
        ):
            if (
                has_followup_message
                and field_name not in active_pending_fields
                and not _user_explicitly_edits_field(payload.message, field_name)
            ):
                continue
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
    if (
        current_draft.summary.title is not None
        and len(current_draft.summary.title) > 100
        and "title" not in merged_updates
    ):
        merged_updates["title"] = current_draft.summary.title[:100].rstrip()
        new_field_sources["title"] = FieldSource(
            source="ai_suggested",
            source_ref=f"summary_revision:{next_summary_rev}",
        )
    requirements = list(current_draft.summary.extra_requirements)
    seen_requirements = {item.strip() for item in requirements}
    if used_fallback and frozen_fields:
        accepted_requirements: tuple[str, ...] | list[str] = ()
    elif needs_scope_decision:
        accepted_requirements = [
            item
            for item in extracted.extra_requirements
            if not _is_scope_choice_requirement(item)
        ]
    else:
        accepted_requirements = extracted.extra_requirements
    for item in accepted_requirements:
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
    time_ai_issues: list[ClarificationIssue] = []
    complete_time: TimeConstraint | None = None
    proposed_time = extracted.time_constraint
    has_proposed_time = proposed_time is not None and any(
        getattr(proposed_time, field) is not None
        for field in ("start_at", "deadline_at", "finish_by_at")
    )
    if "time_constraint" not in frozen_fields and (
        has_proposed_time or extracted.deadline is not None
    ):
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
            time_ai_issues.append(
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
                if not (
                    has_followup_message
                    and "time_constraint" not in active_pending_fields
                    and not _user_explicitly_edits_field(payload.message, "time_constraint")
                ):
                    time_ai_issues.append(
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

    effective_time = complete_time or current_draft.summary.time_constraint
    if (
        not extracted.structured_free_windows
        and user_default_avail is not None
        and _user_requests_default_availability(input_text)
    ):
        default_windows_objs = [
            ExtractedFreeWindowSchema.model_validate(item)
            for item in _free_windows(user_default_avail)
        ]
        if default_windows_objs:
            extracted = extracted.model_copy(
                update={
                    "structured_free_windows": default_windows_objs,
                    "free_time_slots": [w.raw_phrase for w in default_windows_objs],
                }
            )
    dur_text = edit_text or latest_message
    dur = _extract_explicit_duration_minutes(normalize_vietnamese_transcript(dur_text))
    if dur is not None and extracted.availability_update_mode != "replace":
        raw_prev_windows = current_draft.ai_context.get("availability_windows") or []
        open_ended_old = next(
            (
                (w.model_dump(mode="json") if isinstance(w, ExtractedFreeWindowSchema) else w)
                for w in raw_prev_windows
                if isinstance(w, (dict, ExtractedFreeWindowSchema))
                and (
                    (
                        w.get("is_ambiguous")
                        and w.get("start_local")
                        and not w.get("end_local")
                        and not w.get("is_excluded")
                    )
                    if isinstance(w, dict)
                    else (
                        w.is_ambiguous
                        and w.start_local is not None
                        and w.end_local is None
                        and not w.is_excluded
                    )
                )
            ),
            None,
        )
        has_exclude_or_reset = any(
            w.is_excluded or w.op in ("exclude", "reset_override")
            for w in extracted.structured_free_windows
        )
        has_completion_window = any(
            w.end_local is not None
            or w.duration_minutes is not None
            or (
                w.start_local is None
                and not w.is_excluded
                and w.op in ("add", "replace_day")
                and _extract_explicit_duration_minutes(w.raw_phrase) is not None
            )
            for w in extracted.structured_free_windows
        )
        if open_ended_old is not None and not has_exclude_or_reset and not has_completion_window:
            safe_raw_phrase = (
                dur_text[:200].strip()
                or str(open_ended_old.get("raw_phrase") or "bổ sung thời lượng")[:200].strip()
            )
            synth = ExtractedFreeWindowSchema(
                kind=open_ended_old.get("kind", "daily"),
                date=open_ended_old.get("date"),
                weekday=open_ended_old.get("weekday"),
                weekdays=open_ended_old.get("weekdays"),
                date_from=open_ended_old.get("date_from"),
                date_to=open_ended_old.get("date_to"),
                raw_phrase=safe_raw_phrase,
                duration_minutes=dur,
                op="add",
            )
            extracted = extracted.model_copy(
                update={
                    "structured_free_windows": [*extracted.structured_free_windows, synth],
                }
            )
    proposed_availability, availability_state = None, None
    if "availability" not in frozen_fields:
        proposed_availability, availability_state = _availability_proposal(
            extracted,
            current_draft,
            availability,
            timezone_name,
            effective_time=effective_time,
        )
    if availability_state is not None and proposed_availability is not None:
        proposal_windows, unresolved = availability_state
        old_availability = current_draft.summary.availability
        source = current_draft.summary.field_sources.get("availability")
        protected = source is not None and source.source in {"user_input", "user_confirmed"}
        if proposed_availability != old_availability and protected:
            if not (
                has_followup_message
                and "availability" not in active_pending_fields
                and not _user_explicitly_edits_field(payload.message, "availability")
            ):
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
                hints = [
                    str(item["clarification_hint"]).strip()
                    for item in unresolved
                    if item.get("clarification_hint")
                ]
                unhinted = [
                    str(item["raw_phrase"])
                    for item in unresolved
                    if not item.get("clarification_hint")
                ]
                question_parts: list[str] = list(dict.fromkeys(hints))
                if unhinted:
                    phrases = "; ".join(unhinted)
                    question_parts.append(
                        f"Bạn làm rõ ngày và giờ bắt đầu–kết thúc cho: {phrases}"
                    )
                ai_issues.append(
                    ClarificationIssue(
                        code="AI_AVAILABILITY_INCOMPLETE",
                        field_path="availability",
                        message="Một số khung giờ rảnh chưa đủ ngày/giờ hợp lệ.",
                        question=" ".join(question_parts),
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

    ai_issues.extend(time_ai_issues)
    for field in requested_edits if used_fallback else ():
        if field in pending_fields:
            continue
        label = _UF1_REVIEW_FIELD_LABELS[field]
        ai_issues.append(
            ClarificationIssue(
                code="AI_FALLBACK_REVIEW_REQUIRED",
                field_path=field,
                message=f"Yêu cầu đổi {label} chưa được áp dụng. Bản nháp vẫn giữ giá trị hiện tại.",
                question=f"Bạn muốn giữ {label} hiện tại hay cập nhật lại?",
                blocking=True,
            )
        )
    if scope_needs_choice:
        ai_issues.append(
            ClarificationIssue(
                code="AI_SCOPE_CHOICE_REQUIRED",
                field_path="scope",
                message="Phạm vi còn nhiều phương án chưa chốt.",
                question="Bạn muốn chốt phương án đầu ra nào cho việc này?",
                blocking=True,
            )
        )

    eff_title = merged_updates.get("title", current_draft.summary.title)
    eff_desc = merged_updates.get("description", current_draft.summary.description)
    eff_goal = merged_updates.get("goal", current_draft.summary.goal)
    eff_scope = merged_updates.get("scope", current_draft.summary.scope)
    eff_tc = merged_updates.get("time_constraint", current_draft.summary.time_constraint)
    eff_avail = merged_updates.get("availability", current_draft.summary.availability)
    eff_avail_ref = (
        None
        if "availability_ref" in merged_updates and merged_updates["availability_ref"] is None
        else (merged_updates.get("availability_ref") or current_draft.summary.availability_ref)
    )
    has_title = isinstance(eff_title, str) and bool(eff_title.strip())
    has_content = (isinstance(eff_desc, str) and bool(eff_desc.strip())) or (
        isinstance(eff_goal, str) and bool(eff_goal.strip())
    )
    has_scope = isinstance(eff_scope, str) and bool(eff_scope.strip())
    has_tc = eff_tc is not None
    has_avail = eff_avail_ref is not None or (
        isinstance(eff_avail, DraftAvailability)
        and bool(
            eff_avail.weekly_rules or any(ov.intervals for ov in eff_avail.date_overrides)
        )
    )

    availability_resolved_cleanly = (
        availability_state is not None
        and not unresolved
        and proposed_availability is not None
        and bool(
            proposed_availability.weekly_rules
            or any(ov.intervals for ov in proposed_availability.date_overrides)
        )
    )
    for item in extracted.clarification_issues:
        normalized_code = item.code if item.code.startswith("AI_") else f"AI_{item.code}"
        if normalized_code in {"AI_FALLBACK_REVIEW_REQUIRED", "AI_SCOPE_CHOICE_REQUIRED"}:
            continue
        is_avail_issue = item.field_path in {
            "availability",
            "availability_ref",
        } or normalized_code in {
            "AI_AVAILABILITY_INCOMPLETE",
            "AI_MISSING_AVAILABILITY",
            "AI_MISSING_FREE_TIME",
            "AI_AMBIGUOUS_FREE_TIME",
            "AI_AMBIGUOUS_AVAILABILITY",
            "AI_EMPTY_AVAILABILITY",
        }
        if is_avail_issue and (
            availability_resolved_cleanly
            or any(
                i.field_path in {"availability", "availability_ref"}
                or i.code in {"AI_AVAILABILITY_INCOMPLETE", "AI_AVAILABILITY_REVIEW_REQUIRED"}
                for i in ai_issues
            )
        ):
            continue
        if has_scope and normalized_code in {"AI_MISSING_SCOPE", "AI_SCOPE_REQUIRED"}:
            continue
        if (
            has_tc
            and not extracted.has_conflict
            and not time_ai_issues
            and (
                item.field_path == "time_constraint"
                or item.field_path.startswith("time_constraint.")
                or normalized_code
                in {
                    "AI_MISSING_TIME_CONSTRAINT",
                    "AI_TIME_CONSTRAINT_REQUIRED",
                    "AI_MISSING_DEADLINE",
                    "AI_TIME_INCOMPLETE",
                }
            )
        ):
            continue
        if has_title and (
            item.field_path == "title"
            or normalized_code in {"AI_MISSING_TITLE", "AI_TITLE_REQUIRED"}
        ):
            continue
        if has_content and (
            item.field_path in {"description", "content", "goal"}
            or normalized_code in {"AI_MISSING_CONTENT", "AI_DESCRIPTION_REQUIRED"}
        ):
            continue
        if any(i.field_path == item.field_path for i in ai_issues):
            continue
        ai_issues.append(item.model_copy(update={"code": normalized_code}))

    ambiguous_windows = [w for w in extracted.structured_free_windows if w.is_ambiguous]
    ambiguous_phrases = [w.raw_phrase for w in ambiguous_windows]
    unhinted_ambiguous_phrases = [
        w.raw_phrase for w in ambiguous_windows if not w.clarification_hint
    ]
    generated_availability_questions = {
        "Bạn thường rảnh vào những khung giờ nào để mình xếp phiên tập trung phù hợp?",
        "Khung giờ "
        + ", ".join(f"'{phrase}'" for phrase in ambiguous_phrases)
        + " bạn có thể làm từ mấy giờ đến mấy giờ cụ thể để mình xếp lịch chuẩn xác?",
        "Khung giờ "
        + ", ".join(f"'{phrase}'" for phrase in unhinted_ambiguous_phrases)
        + " bạn có thể làm từ mấy giờ đến mấy giờ cụ thể để mình xếp lịch chuẩn xác?",
    } | {w.clarification_hint for w in ambiguous_windows if w.clarification_hint}
    default_guardrail_questions = set(UF1_FALLBACK_QUESTIONS.values()) | {
        "Phạm vi hoặc kết quả đầu ra mong muốn của việc này là gì (ví dụ: số trang, số bài, mức độ hoàn thiện)?",
        "Bạn thường rảnh vào những khung giờ nào để mình xếp phiên tập trung phù hợp?",
        "Khi nào bạn cần hoàn thành xong công việc này (hạn chót)?",
    }
    has_incomplete_avail_issue = any(
        i.code in {"AI_AVAILABILITY_INCOMPLETE", "AI_AVAILABILITY_REVIEW_REQUIRED"}
        for i in ai_issues
    )
    if extracted.generation_tier == "tier_3_rule_based":
        ai_issues.append(_fallback_notice())
    for index, question in enumerate(dict.fromkeys(extracted.clarification_questions)):
        if extracted.generation_tier == "tier_3_rule_based" and (
            question in UF1_FALLBACK_QUESTIONS.values() or question == UF1_FALLBACK_NOTICE
        ):
            # Validator UF1 hỏi đúng trường còn thiếu sau khi đã hợp nhất bản tóm tắt.
            continue
        if question in default_guardrail_questions:
            continue
        if any(issue.question == question for issue in ai_issues):
            continue
        if question in generated_availability_questions:
            if availability_resolved_cleanly or any(
                i.field_path == "availability" for i in ai_issues
            ):
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
        q_low = question.lower()
        if availability_resolved_cleanly and any(
            kw in q_low
            for kw in ("khung giờ", "giờ rảnh", "lịch rảnh", "lịch mặc định", "rảnh vào")
        ):
            continue
        if has_incomplete_avail_issue and (
            any(phrase.lower() in q_low for phrase in ambiguous_phrases if phrase)
            or any(
                kw in q_low
                for kw in ("khung giờ", "giờ rảnh", "rảnh từ", "mấy giờ", "cuộc họp", "sự kiện")
            )
        ):
            continue
        if (not has_scope or any(i.field_path == "scope" for i in ai_issues)) and any(
            kw in q_low
            for kw in (
                "phạm vi",
                "đầu ra",
                "kết quả mong muốn",
                "bao nhiêu trang",
                "bao nhiêu chương",
                "số trang",
                "số chương",
                "số bài",
            )
        ):
            continue
        if (
            not has_tc
            or any(
                i.field_path == "time_constraint" or i.field_path.startswith("time_constraint.")
                for i in ai_issues
            )
        ) and any(
            kw in q_low
            for kw in ("hạn chót", "deadline", "khi nào cần hoàn thành", "hoàn tất trước")
        ):
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
    summary_semantically_ready = (
        has_title
        and has_content
        and has_scope
        and has_tc
        and has_avail
        and not unresolved
    )
    effectively_complete = bool(extracted.is_complete and summary_semantically_ready)

    active_keys = {
        f"{i.code}|{i.field_path}|{i.question}": i
        for i in current_draft.summary.clarification_issues
        if i.code.startswith("AI_") and i.code != "AI_FALLBACK_NOTICE"
    }
    for key, issue in active_keys.items():
        if issue.code == "AI_SCOPE_CHOICE_REQUIRED":
            same_scope_confirmed = (
                extracted.scope is not None
                and current_draft.summary.scope is not None
                and extracted.scope.strip() == current_draft.summary.scope.strip()
            )
            if (
                not scope_needs_choice
                and not used_fallback
                and user_selected_scope
                and (
                    "scope" in merged_updates
                    or same_scope_confirmed
                    or any(
                        current.field_path == "scope" and current.code == "AI_FIELD_REVIEW_REQUIRED"
                        for current in ai_issues
                    )
                )
            ):
                continue
            if issue not in ai_issues:
                ai_issues.append(issue)
            continue
        if issue.code == "AI_FALLBACK_REVIEW_REQUIRED":
            kept_by_user = (
                issue.field_path in kept_pending_fields
                and issue.field_path not in requested_edits
            )
            resolved_by_ai = not used_fallback and (
                issue.field_path in merged_updates
                or any(
                    current.field_path == issue.field_path
                    and current.code.endswith("_REVIEW_REQUIRED")
                    and current.code != "AI_FALLBACK_REVIEW_REQUIRED"
                    for current in ai_issues
                )
            )
            if kept_by_user or resolved_by_ai:
                continue
            if issue not in ai_issues:
                ai_issues.append(issue)
            continue
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
                issue.code
                in {
                    "AI_AVAILABILITY_INCOMPLETE",
                    "AI_MISSING_AVAILABILITY",
                    "AI_MISSING_FREE_TIME",
                }
                or issue.field_path == "availability"
            )
            and availability_state is not None
            and not unresolved
            and has_avail
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
                effectively_complete
                or (
                    extracted.generation_tier != "tier_3_rule_based"
                    and not any(i.field_path == issue.field_path for i in ai_issues)
                )
            )
        ):
            continue
        field_still_missing = (
            (issue.field_path == "title" and not has_title)
            or (issue.field_path in {"description", "goal"} and not has_content)
            or (issue.field_path == "scope" and not has_scope)
            or (
                (
                    issue.field_path == "time_constraint"
                    or issue.code in {"AI_TIME_INCOMPLETE", "AI_MISSING_TIME_CONSTRAINT"}
                )
                and not has_tc
            )
            or (
                (
                    issue.field_path in {"availability", "availability_ref"}
                    or issue.code
                    in {
                        "AI_AVAILABILITY_INCOMPLETE",
                        "AI_MISSING_AVAILABILITY",
                        "AI_MISSING_FREE_TIME",
                    }
                )
                and (not has_avail or bool(unresolved))
            )
        )
        # Field-review decisions stay with the user; generic semantic resolutions
        # accept exact/short resolved keys, completion, or removal on a non-fallback AI pass.
        raw_code = issue.code.removeprefix("AI_")
        resolved = (
            not issue.code.endswith("_REVIEW_REQUIRED")
            and not field_still_missing
            and (
                effectively_complete
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
    ai_field_paths = {i.field_path for i in ai_issues}
    has_ai_avail = bool(ai_field_paths & {"availability", "availability_ref"}) or any(
        i.code in {"AI_AVAILABILITY_INCOMPLETE", "AI_AVAILABILITY_REVIEW_REQUIRED"}
        for i in ai_issues
    )
    has_ai_tc = "time_constraint" in ai_field_paths or any(
        i.field_path.startswith("time_constraint.") for i in ai_issues
    )
    filtered_issues: list[ClarificationIssue] = []
    for iss in issues:
        if has_ai_avail and (
            iss.field_path in {"availability", "availability_ref"}
            or iss.code
            in {
                "EMPTY_AVAILABILITY",
                "MISSING_AVAILABILITY",
                "AVAILABILITY_REQUIRED",
                "AVAILABILITY_OUTSIDE_TIME_CONSTRAINT",
            }
        ):
            continue
        if "scope" in ai_field_paths and iss.code in {"MISSING_SCOPE", "SCOPE_REQUIRED"}:
            continue
        if has_ai_tc and iss.code in {"MISSING_TIME_CONSTRAINT", "TIME_CONSTRAINT_REQUIRED"}:
            continue
        if "title" in ai_field_paths and iss.code == "MISSING_TITLE":
            continue
        if (ai_field_paths & {"description", "content", "goal"}) and iss.code in {
            "MISSING_CONTENT",
            "DESCRIPTION_REQUIRED",
        }:
            continue
        filtered_issues.append(iss)
    issues = filtered_issues
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

    context["uf1_analyzed"] = True
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


async def analyze_draft(
    *,
    user_id: str,
    draft_id: str,
    idempotency_key: str,
    payload: DraftAnalyzeRequest,
    db: AsyncSession | None = None,
) -> PlanningDraft:
    """Dòng 5 (POST /api/v1/drafts/{draft_id}/analyze): Gọi AI trích xuất & làm rõ 1 vòng có khóa single-flight (I01, A01/A02.1, T03, T18, T4.2)."""
    if not idempotency_key:
        return await _analyze_draft_impl(
            user_id=user_id,
            draft_id=draft_id,
            idempotency_key=idempotency_key,
            payload=payload,
            db=db,
        )
    lock_key = (user_id, draft_id, idempotency_key)
    flight = _INFLIGHT_ANALYZE_LOCKS.get(lock_key)
    if flight is None:
        flight = _AnalyzeFlightLock()
        _INFLIGHT_ANALYZE_LOCKS[lock_key] = flight
    flight.ref_count += 1
    try:
        async with flight.lock:
            if db is not None and db.in_transaction():
                await db.rollback()
            return await _analyze_draft_impl(
                user_id=user_id,
                draft_id=draft_id,
                idempotency_key=idempotency_key,
                payload=payload,
                db=db,
            )
    finally:
        flight.ref_count -= 1
        if flight.ref_count <= 0 and _INFLIGHT_ANALYZE_LOCKS.get(lock_key) is flight:
            _INFLIGHT_ANALYZE_LOCKS.pop(lock_key, None)
