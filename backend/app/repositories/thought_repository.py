"""Thought storage and forward links. Services decide policy and commit/rollback."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.models.planning import PlanningDraft
from app.models.task import Task
from app.models.thought import Thought


@dataclass(frozen=True)
class ThoughtLinks:
    draft: PlanningDraft
    task_id: UUID | None


def _legacy_source_key():
    # UUID cũ có thể viết hoa, dùng hex, ngoặc nhọn hoặc URN. So sánh cùng dạng hex.
    source = func.lower(PlanningDraft.summary["_api_meta"]["source_thought_id"].as_string())
    for token in ("urn:uuid:", "{", "}", "-"):
        source = func.replace(source, token, "")
    return source


async def get_thought(db: AsyncSession, user_id: UUID, thought_id: UUID) -> Thought | None:
    return await db.scalar(
        select(Thought)
        .where(Thought.user_id == user_id, Thought.id == thought_id)
        .execution_options(populate_existing=True)
    )


async def get_links(
    db: AsyncSession, user_id: UUID, thought_ids: Sequence[UUID]
) -> dict[UUID, ThoughtLinks]:
    """Một truy vấn cho cả trang; hỗ trợ nháp cũ chỉ lưu nguồn trong JSON."""
    legacy_source = _legacy_source_key()
    rows = await db.execute(
        select(PlanningDraft, Task.id, legacy_source)
        .outerjoin(Task, (Task.source_draft_id == PlanningDraft.id) & (Task.user_id == user_id))
        .where(
            PlanningDraft.user_id == user_id,
            or_(
                PlanningDraft.source_thought_id.in_(thought_ids),
                PlanningDraft.source_thought_id.is_(None)
                & legacy_source.in_([value.hex for value in thought_ids]),
            ),
        )
        .execution_options(populate_existing=True)
    )
    links = {}
    for draft, task_id, legacy_key in rows:
        source_id = draft.source_thought_id
        if source_id is None:
            source_id = UUID(hex=legacy_key)
        if source_id in links:
            raise MultipleResultsFound("Multiple legacy drafts reference the same Thought")
        links[source_id] = ThoughtLinks(draft=draft, task_id=task_id)
    return links


async def attach_draft(
    db: AsyncSession,
    user_id: UUID,
    thought_id: UUID,
    expected_revision: int,
    draft: PlanningDraft,
) -> None:
    """Gắn Draft trong transaction của service, sau kiểm quyền/trạng thái Run và receipt."""
    if draft.user_id != user_id or draft.source_thought_id not in (None, thought_id):
        raise ValueError("Draft owner or source does not match the Thought")
    with db.no_autoflush:
        linked = (await get_links(db, user_id, [thought_id])).get(thought_id)
    if linked is not None and linked.draft.id != draft.id:
        raise ValueError("Thought already has a linked Draft")
    written_id = await db.scalar(
        update(Thought)
        .where(
            Thought.id == thought_id,
            Thought.user_id == user_id,
            Thought.revision == expected_revision,
        )
        .values(revision=Thought.revision + 1)
        .returning(Thought.id)
        .execution_options(synchronize_session="fetch")
    )
    if written_id is None:
        raise StaleDataError("Thought is missing or its revision has changed")
    draft.source_thought_id = thought_id
    db.add(draft)
    await db.flush()


async def delete_thought(
    db: AsyncSession, user_id: UUID, thought_id: UUID, expected_revision: int
) -> bool:
    """Service chỉ gọi sau hạn Undo; giữ nguồn của cả Draft cũ còn dùng JSON."""
    linked_draft = select(PlanningDraft.id).where(
        PlanningDraft.user_id == user_id,
        or_(
            PlanningDraft.source_thought_id == thought_id,
            PlanningDraft.source_thought_id.is_(None) & (_legacy_source_key() == thought_id.hex),
        ),
    )
    deleted_id = await db.scalar(
        delete(Thought)
        .where(
            Thought.user_id == user_id,
            Thought.id == thought_id,
            Thought.revision == expected_revision,
            ~linked_draft.exists(),
        )
        .returning(Thought.id)
        .execution_options(synchronize_session="fetch")
    )
    return deleted_id is not None
