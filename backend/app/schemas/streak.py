"""Pydantic v2 DTOs cho Focus Streak, Auto-Freeze và 24h Repair (BE-UF6-04)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel


class StreakDayRecordData(ApiModel):
    """Một ngày hoạt động trong cửa sổ lịch sử 35 ngày gần nhất của Focus Streak."""

    date: date
    kind: Literal["done", "freeze", "repair"]
    minutes: int = Field(ge=0)


class StreakData(ApiModel):
    """Trạng thái Focus Streak đầy đủ trả về từ GET /api/v1/streak và POST /api/v1/streak/repair."""

    current_streak_days: int = Field(ge=0)
    effective_display_streak: int = Field(ge=0)
    unlocked: bool
    streak_frozen: bool
    streak_freeze_tokens: int = Field(ge=0)
    max_freeze_tokens: int = Field(default=2, ge=1)
    last_streak_date: date | None = None
    repair_pending: bool = False
    repair_window_ends_at: datetime | None = None
    repairs_used_this_month: int = Field(default=0, ge=0)
    repairs_left_this_month: int = Field(default=2, ge=0)
    max_repairs_per_month: int = Field(default=2, ge=1)
    longest_streak_days: int = Field(default=0, ge=0)
    today_completed: bool = False
    today_focus_minutes: int = Field(default=0, ge=0)
    timezone: str = Field(min_length=1)
    day_records: list[StreakDayRecordData] = Field(default_factory=list)
