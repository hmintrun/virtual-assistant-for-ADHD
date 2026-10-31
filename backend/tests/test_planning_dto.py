"""Contract checks for the week-one planning DTOs."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.availability import Availability
from app.schemas.common import TimeConstraint
from app.schemas.planning import PlanCandidate


def candidate_payload():
    start = "2026-10-30T09:00:00+07:00"
    end = "2026-10-30T09:25:00+07:00"
    return {
        "candidate_id": "candidate_1",
        "draft_id": "draft_1",
        "source_summary_revision": 1,
        "source_planning_input_revision": 1,
        "review_revision": 0,
        "planning_parts": [
            {
                "client_part_key": "part_1",
                "scope_description": "Đọc tài liệu",
                "order_index": 0,
                "decomposition_state": "complete",
                "scheduling_state": "complete",
                "unexpanded_scope": None,
                "rough_estimate_seconds": 1500,
                "estimate_source": "user_confirmed",
            }
        ],
        "steps": [
            {
                "client_step_key": "step_1",
                "client_part_key": "part_1",
                "title": "Đọc tài liệu",
                "expected_output": "Ghi chú",
                "order_index": 0,
                "estimated_base_seconds": 1500,
                "remaining_base_seconds": 1500,
                "buffer_multiplier": 1.2,
                "estimate_source": "user_confirmed",
                "status": "pending",
            }
        ],
        "sessions": [
            {
                "client_session_key": "session_1",
                "client_step_key": "step_1",
                "scheduled_start": start,
                "scheduled_end": end,
                "micro_task_text": "Mở tài liệu",
                "micro_task_seconds": 120,
                "work_seconds": 1500,
                "rest_seconds": 0,
                "segments": [{"order_index": 0, "kind": "work", "duration_seconds": 1500}],
                "duration_seconds": 1500,
                "start_state": "waiting",
                "start_decision_due_at": "2026-10-30T09:15:00+07:00",
            }
        ],
        "planning_horizon": {"start_at": start, "end_at": "2026-11-13T09:00:00+07:00"},
        "source_revisions": {
            "availability": 0,
            "preferences": 0,
            "schedule": 0,
            "calendar_context": {"context_id": "context_1", "revision": 0},
        },
        "min_inter_session_gap_seconds": 900,
        "feasibility": {
            "status": "feasible",
            "required_seconds": 1500,
            "available_seconds": 1800,
            "unscheduled_seconds": 0,
            "verification_scope": {
                "scope_type": "candidate_parts",
                "client_part_keys": ["part_1"],
                "planning_horizon": {"start_at": start, "end_at": "2026-11-13T09:00:00+07:00"},
            },
            "fully_scheduled": True,
            "blocking_reasons": [],
            "deficit_seconds": 0,
            "unestimated_part_ids": [],
            "unplanned_part_ids": [],
            "extension_candidate_at": None,
        },
        "stale": False,
        "user_step_proposal": None,
    }


def test_candidate_keeps_client_keys_and_micro_outside_duration():
    candidate = PlanCandidate.model_validate(candidate_payload())
    assert candidate.sessions[0].duration_seconds == 1500
    assert candidate.sessions[0].micro_task_seconds == 120
    assert "session_id" not in candidate.sessions[0].model_dump()


def test_candidate_rejects_missing_parent_and_wrong_duration():
    bad = deepcopy(candidate_payload())
    bad["sessions"][0]["client_step_key"] = "absent"
    with pytest.raises(ValidationError):
        PlanCandidate.model_validate(bad)
    bad = deepcopy(candidate_payload())
    bad["sessions"][0]["duration_seconds"] = 1620
    with pytest.raises(ValidationError):
        PlanCandidate.model_validate(bad)


def test_time_constraint_requires_one_aware_end():
    base = {
        "kind": "deadline",
        "start_at": "2026-10-30T09:00:00+07:00",
        "deadline_at": "2026-10-31T09:00:00+07:00",
        "finish_by_at": None,
        "input_timezone": "Asia/Ho_Chi_Minh",
    }
    assert TimeConstraint.model_validate(base).kind == "deadline"
    with pytest.raises(ValidationError):
        TimeConstraint.model_validate({**base, "finish_by_at": base["deadline_at"]})
    with pytest.raises(ValidationError):
        TimeConstraint.model_validate({**base, "start_at": "2026-10-30T09:00:00"})


def test_availability_rejects_overnight_rule():
    payload = {
        "availability_id": "availability_1",
        "revision": 0,
        "timezone": "Asia/Ho_Chi_Minh",
        "weekly_rules": [{"weekday": 1, "start_local": "09:00", "end_local": "12:00"}],
        "date_overrides": [],
        "task_limits": [],
        "source": "user_confirmed",
        "effective_intervals": None,
        "calendar_context": None,
        "confirmed_at": None,
    }
    assert Availability.model_validate(payload).weekly_rules[0].weekday == 1
    bad = deepcopy(payload)
    bad["weekly_rules"] = [{"weekday": 1, "start_local": "09:00", "end_local": "08:00"}]
    with pytest.raises(ValidationError):
        Availability.model_validate(bad)
