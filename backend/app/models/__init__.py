"""All persistent models; import this module before using Base.metadata."""

from app.models.base import Base
from app.models.calendar import (
    CalendarAuthorization,
    CalendarConnection,
    CalendarEventLink,
    CalendarPreview,
    CalendarSyncJob,
    PlanningContext,
)
from app.models.device import DeviceState
from app.models.execution import ActualCorrection, CheckIn, RunEvent, SessionRun
from app.models.planning import PlanCandidate, PlanningDraft, ScheduleVersion
from app.models.schedule_proposal import RescheduleProposal
from app.models.session import Session
from app.models.settings import AdaptationSuggestion, Availability, SettingChangeRequest
from app.models.step import Step
from app.models.system import AuditEvent, CommandReceipt, Reminder
from app.models.task import Task
from app.models.thought import Thought
from app.models.user import User, UserPreference

__all__ = [
    "Base",
    "User",
    "UserPreference",
    "Task",
    "Step",
    "Session",
    "PlanningDraft",
    "PlanCandidate",
    "ScheduleVersion",
    "SessionRun",
    "DeviceState",
    "CheckIn",
    "RunEvent",
    "ActualCorrection",
    "Thought",
    "RescheduleProposal",
    "Availability",
    "SettingChangeRequest",
    "AdaptationSuggestion",
    "CommandReceipt",
    "AuditEvent",
    "Reminder",
    "CalendarConnection",
    "CalendarEventLink",
    "CalendarSyncJob",
    "PlanningContext",
    "CalendarPreview",
    "CalendarAuthorization",
]

from app.models.integrity import configure_integrity

configure_integrity()
