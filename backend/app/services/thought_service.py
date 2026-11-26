"""W07: cất metadata không gọi AI; tạo Draft sau phiên và giữ liên kết nguồn."""

from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.planning import PlanningDraft as DraftRow
from app.models.thought import Thought
from app.repositories import run_repository, thought_repository
from app.schemas.draft import PlanningDraft
from app.schemas.thought import (
    ThoughtCreateRequest,
    ThoughtData,
    ThoughtDeleteRequest,
    ThoughtDeleteResult,
    ThoughtListResult,
    ThoughtToDraftRequest,
    ThoughtToDraftResult,
)
from app.services import command_service, draft_service


def _error(code: str, message: str, *, status: int = 422, revision: int | None = None) -> AppError:
    return AppError(
        status_code=status,
        code=code,
        message=message,
        current_revisions={"thought_revision": revision} if revision is not None else {},
        next_action=(
            "fix_fields"
            if status == 422
            else {
                "SESSION_STILL_RUNNING": "wait_for_session_end",
                "THOUGHT_ALREADY_LINKED": "open_linked_draft",
            }.get(code, "reload_thought")
        ),
    )


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy suy nghĩ.", status=404) from None


async def _get(db: AsyncSession, user_id: str, thought_id: str) -> Thought:
    row = await thought_repository.get_thought(
        db, command_service.owner_uuid(user_id), _uuid(thought_id)
    )
    if row is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy suy nghĩ.", status=404)
    return row


def _data(row: Thought, linked: thought_repository.ThoughtLinks | None = None) -> ThoughtData:
    return ThoughtData(
        thought_id=str(row.id),
        thought_revision=row.revision,
        client_thought_id=row.client_thought_id,
        source_run_id=str(row.source_run_id) if row.source_run_id else None,
        input_type=cast(Literal["audio", "text"], row.input_type),
        capture_state=row.capture_state,
        attempt_count=row.attempt_count,
        text_content=row.text_content,
        retention_expires_at=row.retention_expires_at,
        created_at=row.created_at,
        linked_draft_id=str(linked.draft.id) if linked else None,
        converted_task_id=str(linked.task_id) if linked and linked.task_id else None,
    )


@command_service.durable_command("capture_thought", ThoughtData, resource_arg="")
async def capture_thought(
    db: AsyncSession, *, user_id: str, idempotency_key: str, payload: ThoughtCreateRequest
) -> ThoughtData:
    owner = command_service.owner_uuid(user_id)
    if payload.input_type == "text" and not (payload.text_content or "").strip():
        raise _error("VALIDATION_ERROR", "Suy nghĩ dạng chữ cần có nội dung.")
    if payload.capture_state not in {"recording", "saved", "failed"}:
        raise _error("VALIDATION_ERROR", "Trạng thái lưu suy nghĩ không hợp lệ.")
    source = _uuid(payload.source_run_id) if payload.source_run_id else None
    if source is not None and await run_repository.get_run(db, owner, source) is None:
        raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy phiên nguồn.", status=404)
    row = await db.scalar(
        select(Thought).where(
            Thought.user_id == owner, Thought.client_thought_id == payload.client_thought_id
        )
    )
    if row is None:
        row = Thought(
            user_id=owner,
            client_thought_id=payload.client_thought_id,
            source_run_id=source,
            input_type=payload.input_type,
            text_content=payload.text_content,
            attempt_count=payload.attempt_count,
            capture_state=payload.capture_state,
        )
        db.add(row)
    else:
        if (
            row.source_run_id != source
            or row.input_type != payload.input_type
            or (
                row.text_content is not None
                and payload.text_content is not None
                and row.text_content != payload.text_content
            )
        ):
            raise _error(
                "CLIENT_THOUGHT_CONFLICT", "Mã suy nghĩ đã được dùng cho nội dung khác.", status=409
            )
        if payload.attempt_count >= row.attempt_count:
            row.attempt_count = payload.attempt_count
            if row.capture_state != "saved":
                row.capture_state = payload.capture_state
            if row.text_content is None:
                row.text_content = payload.text_content
    await db.flush()
    links = await thought_repository.get_links(db, owner, [row.id])
    return _data(row, links.get(row.id))


async def list_thoughts(
    db: AsyncSession, *, user_id: str, limit: int = 20, cursor: str | None = None
) -> ThoughtListResult:
    if not 1 <= limit <= 100:
        raise _error("VALIDATION_ERROR", "Kích thước trang phải từ 1 đến 100.")
    owner = command_service.owner_uuid(user_id)
    stmt = select(Thought).where(Thought.user_id == owner, Thought.capture_state != "failed")
    if cursor is not None:
        try:
            anchor = await thought_repository.get_thought(db, owner, UUID(cursor))
        except (ValueError, TypeError):
            anchor = None
        if anchor is None or anchor.capture_state == "failed":
            raise _error("VALIDATION_ERROR", "Con trỏ trang không hợp lệ.")
        stmt = stmt.where(
            or_(
                Thought.created_at < anchor.created_at,
                and_(Thought.created_at == anchor.created_at, Thought.id < anchor.id),
            )
        )
    rows = list(
        await db.scalars(
            stmt.order_by(Thought.created_at.desc(), Thought.id.desc()).limit(limit + 1)
        )
    )
    page = rows[:limit]
    links = await thought_repository.get_links(db, owner, [r.id for r in page]) if page else {}
    return ThoughtListResult(
        items=[_data(row, links.get(row.id)) for row in page],
        next_cursor=str(page[-1].id) if len(rows) > limit else None,
    )


async def get_thought_detail(db: AsyncSession, *, user_id: str, thought_id: str) -> ThoughtData:
    row = await _get(db, user_id, thought_id)
    links = await thought_repository.get_links(db, command_service.owner_uuid(user_id), [row.id])
    return _data(row, links.get(row.id))


async def ensure_thought_eligible_for_draft_conversion(
    db: AsyncSession,
    owner: UUID,
    thought_id: UUID,
    current_draft_id: UUID | None = None,
) -> Thought:
    """Xác thực Thought đủ điều kiện chuyển thành chữ/nháp (UF4 Zero-AI Guard)."""
    thought = await thought_repository.get_thought(db, owner, thought_id)
    if thought is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy Thought.",
            next_action="reload",
        )

    # 1. Trạng thái thu âm phải hoàn tất thành công
    if thought.capture_state != "saved":
        raise AppError(
            status_code=409,
            code="THOUGHT_CAPTURE_NOT_SAVED",
            message="Chỉ chuyển chữ hoặc tạo nháp khi ghi chú đã lưu hoàn tất (capture_state='saved').",
            next_action="wait_for_capture_saved",
        )

    # 2. Chưa từng được liên kết với Draft khác
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
    if linked is not None and (current_draft_id is None or linked.draft.id != current_draft_id):
        raise AppError(
            status_code=409,
            code="THOUGHT_ALREADY_LINKED",
            message="Suy nghĩ đã có bản nháp liên kết.",
            next_action="open_linked_draft",
        )

    # 3. Zero AI Guard: Không có bất kỳ phiên nào đang chạy (kể cả khi source_run_id=None)
    live_run = await run_repository.get_live_run(db, owner)
    if live_run is not None:
        raise AppError(
            status_code=409,
            code="SESSION_STILL_RUNNING",
            message="Chỉ chuyển chữ hoặc tạo nháp sau khi phiên tập trung kết thúc.",
            next_action="wait_for_session_end",
        )

    # 4. Nếu có phiên nguồn, phiên nguồn phải đã kết thúc hợp lệ
    if thought.source_run_id is not None:
        source_run = await run_repository.get_run(db, owner, thought.source_run_id)
        if source_run is None:
            raise AppError(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                message="Không tìm thấy phiên nguồn.",
                next_action="reload",
            )
        if source_run.ended_at is None or source_run.run_state in ("running", "paused"):
            raise AppError(
                status_code=409,
                code="SESSION_STILL_RUNNING",
                message="Chỉ tạo nháp sau khi phiên đã dừng.",
                next_action="wait_for_session_end",
            )

    return thought


@command_service.durable_command(
    "convert_thought_to_draft", ThoughtToDraftResult, resource_arg="thought_id"
)
async def convert_thought_to_draft(
    db: AsyncSession,
    *,
    user_id: str,
    thought_id: str,
    idempotency_key: str,
    payload: ThoughtToDraftRequest,
) -> ThoughtToDraftResult:
    owner = command_service.owner_uuid(user_id)
    row = await ensure_thought_eligible_for_draft_conversion(db, owner, _uuid(thought_id))
    if payload.expected_thought_revision != row.revision:
        raise _error(
            "REVISION_CONFLICT", "Suy nghĩ đã thay đổi.", status=409, revision=row.revision
        )
    text = payload.text_content.strip()
    if not text:
        raise _error("VALIDATION_ERROR", "Nội dung tạo nháp không được để trống.")
    summary = draft_service._build_initial_summary(text, None)
    issues, can_plan = await draft_service._evaluate_summary_for_user(summary, user_id, db)
    summary = summary.model_copy(update={"clarification_issues": issues, "can_plan": can_plan})
    draft_id = uuid4()
    dto = PlanningDraft(
        draft_id=str(draft_id),
        client_draft_id=f"thought-draft-{row.id}",
        input_source="thought",
        raw_input=text,
        stage="summary_review" if can_plan else "collecting",
        summary=summary,
        summary_revision=1,
        planning_input_revision=1,
        source_thought_id=str(row.id),
    )
    draft = DraftRow(
        id=draft_id,
        user_id=owner,
        client_draft_id=dto.client_draft_id,
        stage=dto.stage,
        input_source="thought",
        raw_input=text,
        summary=draft_service._serialize_summary_payload(dto),
        summary_revision=1,
        planning_input_revision=1,
        source_thought_id=row.id,
    )
    await thought_repository.attach_draft(
        db, owner, row.id, payload.expected_thought_revision, draft
    )
    if row.text_content is None:
        # attach_draft đã tăng revision bằng CAS; lưu transcript trong cùng lần thay đổi đó.
        await db.execute(
            update(Thought)
            .where(Thought.id == row.id, Thought.user_id == owner)
            .values(text_content=text)
            .execution_options(synchronize_session="fetch")
        )
    await db.flush()
    return ThoughtToDraftResult(
        thought_id=str(row.id),
        linked_draft_id=str(draft.id),
        converted_task_id=None,
        draft=draft_service._row_to_schema(draft),
    )


@command_service.durable_command("delete_thought", ThoughtDeleteResult, resource_arg="thought_id")
async def delete_thought(
    db: AsyncSession,
    *,
    user_id: str,
    thought_id: str,
    idempotency_key: str,
    payload: ThoughtDeleteRequest,
) -> ThoughtDeleteResult:
    row = await _get(db, user_id, thought_id)
    owner = command_service.owner_uuid(user_id)
    links = await thought_repository.get_links(db, owner, [row.id])
    if row.id in links:
        raise _error(
            "THOUGHT_ALREADY_LINKED",
            "Không thể xóa nguồn của nháp hoặc công việc đã liên kết.",
            status=409,
            revision=row.revision,
        )
    if row.revision != payload.expected_thought_revision:
        raise _error(
            "REVISION_CONFLICT", "Suy nghĩ đã thay đổi.", status=409, revision=row.revision
        )
    cleanup = row.input_type == "audio"
    if not await thought_repository.delete_thought(
        db, owner, row.id, payload.expected_thought_revision
    ):
        latest = await db.scalar(
            select(Thought)
            .where(Thought.user_id == owner, Thought.id == row.id)
            .execution_options(populate_existing=True)
        )
        if latest is None:
            raise _error("RESOURCE_NOT_FOUND", "Không tìm thấy suy nghĩ.", status=404)
        if latest.id in await thought_repository.get_links(db, owner, [latest.id]):
            raise _error(
                "THOUGHT_ALREADY_LINKED",
                "Không thể xóa nguồn của nháp hoặc công việc đã liên kết.",
                status=409,
                revision=latest.revision,
            )
        raise _error(
            "REVISION_CONFLICT",
            "Suy nghĩ đã thay đổi; vui lòng tải lại.",
            status=409,
            revision=latest.revision,
        )
    return ThoughtDeleteResult(thought_id=thought_id, deleted=True, local_cleanup_required=cleanup)
