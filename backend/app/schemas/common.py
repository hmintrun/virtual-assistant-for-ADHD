"""Shared API values for planning; timestamps always include an offset."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeInterval(ApiModel):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            raise ValueError("Mốc thời gian phải có múi giờ")
        if self.start_at >= self.end_at:
            raise ValueError("Thời điểm bắt đầu phải trước kết thúc")
        return self


class AvailabilityRef(ApiModel):
    availability_id: str = Field(min_length=1)
    revision: int = Field(ge=0)


class CalendarContextRef(ApiModel):
    context_id: str = Field(min_length=1)
    revision: int = Field(ge=0)


class CandidateRef(ApiModel):
    candidate_id: str = Field(min_length=1)
    review_revision: int = Field(ge=0)


class SourceRevisions(ApiModel):
    availability: int = Field(ge=0)
    preferences: int = Field(ge=0)
    schedule: int = Field(ge=0)
    calendar_context: CalendarContextRef


class FieldSource(ApiModel):
    source: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class ClarificationIssue(ApiModel):
    code: str
    field_path: str
    message: str
    question: str
    blocking: bool


class BlockingReason(ApiModel):
    code: str
    message: str


class SessionSegment(ApiModel):
    order_index: int = Field(ge=0)
    kind: Literal["work", "rest"]
    duration_seconds: int = Field(gt=0)


class TimeConstraint(ApiModel):
    kind: Literal["deadline", "window"]
    start_at: datetime
    finish_by_at: datetime | None
    deadline_at: datetime | None
    input_timezone: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_branch(self):
        end = self.deadline_at if self.kind == "deadline" else self.finish_by_at
        other = self.finish_by_at if self.kind == "deadline" else self.deadline_at
        if end is None or other is not None:
            raise ValueError("Chỉ điền mốc kết thúc của nhánh đã chọn")
        if self.start_at.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("Mốc thời gian phải có múi giờ")
        if self.start_at >= end:
            raise ValueError("Thời điểm bắt đầu phải trước hạn cuối")
        return self
