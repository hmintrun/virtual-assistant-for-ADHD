from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, RecordMixin, UTCDateTime


class PlanningDraft(OwnedMixin, RecordMixin, Base):
    __tablename__ = "planning_drafts"
    __table_args__ = (
        UniqueConstraint("user_id", "client_draft_id"),
        CheckConstraint(
            "summary_revision >= 1 AND planning_input_revision >= 1", name="revisions_positive"
        ),
    )
    client_draft_id: Mapped[str] = mapped_column(String(128))
    stage: Mapped[str] = mapped_column(String(32), default="collecting")
    raw_input: Mapped[str | None] = mapped_column(Text)
    input_source: Mapped[str] = mapped_column(String(32))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_revision: Mapped[int] = mapped_column(default=1)
    summary_confirmed_revision: Mapped[int | None]
    planning_input_revision: Mapped[int] = mapped_column(default=1)
    user_step_proposal: Mapped[str | None] = mapped_column(Text)
    # FK này khép chuỗi lịch sử Thought -> Draft -> Task -> ... -> Run -> Thought.
    # use_alter tách constraint trên PostgreSQL; SQLite tạo FK cùng bảng.
    source_thought_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "thoughts.id", ondelete="RESTRICT", use_alter=True, name="fk_draft_source_thought"
        ),
        unique=True,
    )


class PlanCandidate(OwnedMixin, RecordMixin, Base):
    __tablename__ = "plan_candidates"
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("planning_drafts.id", ondelete="RESTRICT"), index=True
    )
    source_summary_revision: Mapped[int]
    source_planning_input_revision: Mapped[int]
    review_revision: Mapped[int] = mapped_column(default=1)
    planning_parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    sessions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    planning_horizon: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_revisions: Mapped[dict[str, Any]] = mapped_column(JSON)
    feasibility: Mapped[dict[str, Any]] = mapped_column(JSON)
    min_inter_session_gap_seconds: Mapped[int] = mapped_column(default=900)
    generation_tier: Mapped[str | None] = mapped_column(String(32))
    user_step_proposal: Mapped[str | None] = mapped_column(Text)
    # stale được so sánh với revision nguồn khi đọc, không lưu boolean dễ lỗi thời.


class PlanningPart(OwnedMixin, RecordMixin, Base):
    __tablename__ = "planning_parts"
    __table_args__ = (
        UniqueConstraint("task_id", "client_part_key"),
        UniqueConstraint("task_id", "order_index"),
        CheckConstraint("order_index >= 0", name="order_nonnegative"),
        CheckConstraint(
            "rough_estimate_seconds IS NULL OR rough_estimate_seconds > 0", name="estimate_positive"
        ),
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    client_part_key: Mapped[str] = mapped_column(String(128))
    scope_description: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int]
    decomposition_state: Mapped[str] = mapped_column(String(32))
    scheduling_state: Mapped[str] = mapped_column(String(32))
    unexpanded_scope: Mapped[str | None] = mapped_column(Text)
    rough_estimate_seconds: Mapped[int | None]
    estimate_source: Mapped[str | None] = mapped_column(String(32))


class ScheduleVersion(OwnedMixin, RecordMixin, Base):
    __tablename__ = "schedule_versions"
    __table_args__ = (
        UniqueConstraint("user_id", "version_number"),
        CheckConstraint(
            "version_number >= 1 AND min_inter_session_gap_seconds = 900", name="version_gap_valid"
        ),
    )
    version_number: Mapped[int]
    base_version: Mapped[int | None]
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime())
    affected_task_ids: Mapped[list[str]] = mapped_column(JSON)
    min_inter_session_gap_seconds: Mapped[int] = mapped_column(default=900)
    alarm_sync_state: Mapped[str] = mapped_column(String(32), default="pending")
