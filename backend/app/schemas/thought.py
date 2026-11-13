"""DTO Thought; đường dẫn audio và Undo outbox được quản lý trên thiết bị."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.schemas.common import ApiModel
from app.schemas.draft import PlanningDraft


class ThoughtBase(ApiModel):
    client_thought_id: str = Field(min_length=1, max_length=128)
    source_run_id: str | None = Field(default=None, min_length=1)
    input_type: Literal["audio", "text"]
    capture_state: str = Field(min_length=1)
    attempt_count: int = Field(ge=1, le=5)
    text_content: str | None = None

    @field_validator("client_thought_id", mode="before")
    @classmethod
    def validate_client_thought_id(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Mã client_thought_id không được để trống hoặc chỉ chứa khoảng trắng.")
        return v.strip()


class ThoughtCreateRequest(ThoughtBase):
    @model_validator(mode="after")
    def validate_create_input_content(self) -> ThoughtCreateRequest:
        if self.input_type == "audio":
            if self.text_content is not None:
                raise ValueError(
                    "Thought dạng âm thanh không được chứa text_content khi tạo trong phiên tập trung (Zero AI)."
                )
            if self.attempt_count > 3:
                raise ValueError(
                    "Thu âm suy nghĩ chỉ cho phép tối đa 3 lần thử. Từ lần thứ 4 bắt buộc chuyển sang nhập văn bản."
                )
        if self.input_type == "text" and (not self.text_content or not self.text_content.strip()):
            raise ValueError("Thought dạng văn bản bắt buộc phải có nội dung text_content hợp lệ.")
        return self


class ThoughtData(ThoughtBase):
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
