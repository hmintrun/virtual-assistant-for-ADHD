"""Pydantic contracts for UF7 adaptation suggestions."""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.common import ApiModel

SuggestionKind = Literal["buffer_multiplier", "reminder_offset"]
SuggestionValue = float | list[int]


class SuggestionResponseRequest(ApiModel):
    expected_suggestion_revision: int = Field(ge=0)
    action: str = Field(min_length=1)
    annoyance_feedback: str | None = Field(default=None, max_length=1000)
    edited_value: SuggestionValue | None = None

    @model_validator(mode="after")
    def validate_edited_value(self) -> "SuggestionResponseRequest":
        if isinstance(self.edited_value, (float, int)) and self.edited_value <= 0:
            raise ValueError("buffer_multiplier phải lớn hơn 0")
        if isinstance(self.edited_value, list):
            if not 1 <= len(self.edited_value) <= 3:
                raise ValueError("reminder_offset cần từ 1 đến 3 mốc")
            if any(value < 0 for value in self.edited_value):
                raise ValueError("reminder_offset không được âm")
            if len(set(self.edited_value)) != len(self.edited_value):
                raise ValueError("reminder_offset không được trùng")
            if self.edited_value != sorted(self.edited_value, reverse=True):
                raise ValueError("reminder_offset phải xếp theo thứ tự giảm dần")
        return self


class AdaptationSuggestionData(ApiModel):
    suggestion_id: str = Field(min_length=1)
    suggestion_revision: int = Field(ge=0)
    kind: SuggestionKind
    current_value: SuggestionValue
    suggested_value: SuggestionValue
    sample_count: int = Field(ge=0)
    sample_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason: str
    policy_revision: str
    model_revision: str | None = None
    status: Literal["pending", "accepted", "rejected", "superseded"]
    supersedes_suggestion_id: str | None = None

    @model_validator(mode="after")
    def keep_value_variant(self) -> "AdaptationSuggestionData":
        if self.kind == "buffer_multiplier":
            if isinstance(self.current_value, list) or isinstance(self.suggested_value, list):
                raise ValueError("buffer_multiplier cần current/suggested_value dạng số")
            if self.current_value <= 0 or self.suggested_value <= 0:
                raise ValueError("buffer_multiplier phải lớn hơn 0")
        else:
            if not isinstance(self.current_value, list) or not isinstance(
                self.suggested_value, list
            ):
                raise ValueError("reminder_offset cần current/suggested_value dạng danh sách")
            for values in (self.current_value, self.suggested_value):
                if not 1 <= len(values) <= 3 or len(set(values)) != len(values):
                    raise ValueError("reminder_offset nhận tối đa 3 giá trị không trùng")
                if values != sorted(values, reverse=True):
                    raise ValueError("reminder_offset phải xếp theo thứ tự giảm dần")
                if any(value < 0 for value in values):
                    raise ValueError("reminder_offset không được âm")
        return self


class SuggestionResponseData(ApiModel):
    suggestion_id: str = Field(min_length=1)
    suggestion_revision: int = Field(ge=0)
    kind: SuggestionKind
    action: str = Field(min_length=1)
    status: str = Field(min_length=1)
    accepted_value: SuggestionValue | None = None
    application_state: str = Field(min_length=1)
    effective_revision: int = Field(ge=0)
    requested_revision: int | None = Field(default=None, ge=0)
    change_request_id: str | None = None
    pending_proposal_id: str | None = None

    @model_validator(mode="after")
    def keep_value_variant(self) -> "SuggestionResponseData":
        if self.kind == "buffer_multiplier" and isinstance(self.accepted_value, list):
            raise ValueError("buffer_multiplier cần accepted_value dạng số")
        if self.kind == "buffer_multiplier" and isinstance(self.accepted_value, (float, int)):
            if self.accepted_value <= 0:
                raise ValueError("buffer_multiplier phải lớn hơn 0")
        if self.kind == "reminder_offset" and self.accepted_value is not None:
            if not isinstance(self.accepted_value, list):
                raise ValueError("reminder_offset cần accepted_value dạng danh sách")
            if not 1 <= len(self.accepted_value) <= 3 or len(set(self.accepted_value)) != len(
                self.accepted_value
            ):
                raise ValueError("reminder_offset nhận tối đa 3 giá trị không trùng")
            if self.accepted_value != sorted(self.accepted_value, reverse=True):
                raise ValueError("reminder_offset phải xếp theo thứ tự giảm dần")
            if any(value < 0 for value in self.accepted_value):
                raise ValueError("reminder_offset không được âm")
        return self
