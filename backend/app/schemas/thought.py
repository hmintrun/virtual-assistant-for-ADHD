"""DTO Thought; đường dẫn audio và Undo outbox được quản lý trên thiết bị."""

from typing import Literal

from pydantic import AwareDatetime, Field

from app.schemas.common import ApiModel
from app.schemas.draft import PlanningDraft


class ThoughtCreateRequest(ApiModel):
    client_thought_id: str = Field(min_length=1, max_length=128)
    source_run_id: str | None = Field(default=None, min_length=1)
    input_type: Literal["audio", "text"]
    capture_state: str = Field(min_length=1)
    attempt_count: int = Field(ge=1, le=3)
    text_content: str | None = None


class ThoughtData(ThoughtCreateRequest):
    thought_id: str = Field(min_length=1)
    thought_revision: int = Field(ge=1)
    linked_draft_id: str | None = None
    converted_task_id: str | None = None
    retention_expires_at: AwareDatetime | None = None


class ThoughtListResult(ApiModel):
    items: list[ThoughtData]
    next_cursor: str | None = None


class ThoughtToDraftRequest(ApiModel):
    expected_thought_revision: int = Field(ge=1)
    text_content: str = Field(min_length=1)


class ThoughtToDraftResult(ApiModel):
    thought_id: str = Field(min_length=1)
    linked_draft_id: str = Field(min_length=1)
    converted_task_id: str | None = None
    draft: PlanningDraft


class ThoughtDeleteRequest(ApiModel):
    expected_thought_revision: int = Field(ge=1)


class ThoughtDeleteResult(ApiModel):
    thought_id: str = Field(min_length=1)
    deleted: bool
    local_cleanup_required: bool
