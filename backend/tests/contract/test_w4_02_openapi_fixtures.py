"""Contract checks for W4-02 OpenAPI snapshots, fixtures and API route DTOs."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas import calendar, common, execution
from app.schemas.auth import LogoutData, RefreshTokenRequest, TokenBundle
from app.schemas.reschedule import ProposalData
from app.schemas.schedule import UF5CheckInPromptData
from app.schemas.settings import SettingsChangeRequest, UserPreference
from app.schemas.thought import ThoughtCreateRequest, ThoughtData
from app.schemas.voice import UF5VoiceCheckInPromptResponse

DOCS = Path(__file__).resolve().parents[3] / "docs/backend"
FILES = sorted((DOCS / "fixtures/w4-02").glob("*.json"))
CASES = {case["name"]: case for file in FILES for case in json.loads(file.read_text(encoding="utf-8"))["cases"]}


def test_runtime_openapi_snapshot_matches_application():
    snapshot = json.loads((DOCS / "openapi.json").read_text(encoding="utf-8"))
    assert snapshot == app.openapi()
    assert "/api/v1/auth/refresh" in snapshot["paths"]
    assert "/api/v1/auth/logout" in snapshot["paths"]
    assert not snapshot["paths"]["/api/v1/auth/logout"]["post"].get("security")
    assert "/api/v1/voice/transcriptions" in snapshot["paths"]
    assert "/api/v1/voice/tts" in snapshot["paths"]
    assert "/api/v1/voice/uf5-checkin-prompt" in snapshot["paths"]
    start_parameters = snapshot["paths"]["/api/v1/sessions/{session_id}/start"]["post"]["parameters"]
    assert any(parameter["name"] == "Idempotency-Key" and parameter["in"] == "header" and parameter["required"]
               for parameter in start_parameters)
    for path in (
        "/api/v1/calendar/connections/{connection_id}/configuration-previews",
        "/api/v1/calendar/event-change-previews",
    ):
        schema_ref = snapshot["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        envelope = snapshot["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]
        data_ref = envelope["properties"]["data"]["$ref"].rsplit("/", 1)[-1]
        data_schema = snapshot["components"]["schemas"][data_ref]
        assert "preview_revision" in data_schema.get("properties", {})
        assert "base_connection_revision" in data_schema.get("properties", {})
        assert "expected_preview_revision" not in data_schema.get("properties", {})
    for confirm_path in (
        "/api/v1/calendar/configuration-previews/{preview_id}/confirm",
        "/api/v1/calendar/event-change-previews/{preview_id}/confirm",
    ):
        req_ref = snapshot["paths"][confirm_path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        req_schema = snapshot["components"]["schemas"][req_ref.rsplit("/", 1)[-1]]
        assert "expected_preview_revision" in req_schema.get("properties", {})


def test_auth_target_openapi_is_separate_and_marked_unimplemented():
    spec = json.loads((DOCS / "openapi-auth-target.json").read_text(encoding="utf-8"))
    logout = spec["paths"]["/api/v1/auth/logout"]["post"]
    assert logout["security"] == []
    assert logout["x-implementation-status"] == "target_be03"
    assert logout["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/RefreshTokenRequest")
    assert set(logout["responses"]) == {"200", "401", "403", "422", "503"}


def test_five_fixture_sets_validate_envelopes_and_dtos():
    assert len(FILES) == 5
    assert {200, 201, 401, 409, 412, 422, 503, 504} <= {case["response"]["status"] for case in CASES.values()}
    for case in CASES.values():
        body = case["response"]["body"]
        if case["scope"] == "google_upstream":
            assert body["error"]["code"] == 412
        elif "error" in body:
            common.ErrorEnvelope.model_validate(body)
        elif "meta" in body:
            common.ResponseMeta.model_validate(body["meta"])

    TokenBundle.model_validate(CASES["refresh_success"]["response"]["body"]["data"])
    RefreshTokenRequest.model_validate(CASES["logout_first"]["request"])
    LogoutData.model_validate(CASES["logout_first"]["response"]["body"]["data"])
    LogoutData.model_validate(CASES["logout_retry"]["response"]["body"]["data"])
    assert CASES["logout_first"]["scope"] == "runtime"
    assert CASES["logout_retry"]["scope"] == "runtime"
    assert CASES["refresh_reuse"]["scope"] == "runtime"
    UserPreference.model_validate(CASES["preferences_after_reject"]["response"]["body"]["data"])
    calendar.CalendarConfigurationPreviewResponse.model_validate(CASES["configuration_preview"]["response"]["body"]["data"])
    calendar.CalendarConfigurationPreviewResponse.model_validate(CASES["configuration_preview_dynamic_revision"]["response"]["body"]["data"])
    calendar.EventChangePreviewResponse.model_validate(CASES["event_preview"]["response"]["body"]["data"])
    calendar.EventChangePreviewResponse.model_validate(CASES["event_preview_dynamic_revision"]["response"]["body"]["data"])
    calendar.EventChangeConfirmRequest.model_validate(CASES["preview_conflict"]["request"])
    execution.SessionRunData.model_validate(CASES["start_idempotent"]["response"]["body"]["data"])
    ThoughtCreateRequest.model_validate(CASES["thought_capture"]["request"])
    ThoughtData.model_validate(CASES["thought_capture"]["response"]["body"]["data"])
    ProposalData.model_validate(CASES["proposal_deficit"]["response"]["body"]["data"])
    prompt = CASES["uf5_tts_fallback"]["response"]["body"]["data"]
    UF5VoiceCheckInPromptResponse.model_validate(prompt)
    UF5CheckInPromptData.model_validate(prompt["prompt"])
    assert CASES["stt_timeout"]["client_expectations"]["receive_timeout_seconds_min"] >= 45


def test_runtime_preview_revision_and_buffer_upper_bound_contracts():
    assert CASES["configuration_preview_dynamic_revision"]["scope"] == "runtime"
    assert CASES["configuration_preview_dynamic_revision"]["response"]["body"]["data"]["preview_revision"] == 7
    assert CASES["event_preview_dynamic_revision"]["scope"] == "runtime"
    assert CASES["event_preview_dynamic_revision"]["response"]["body"]["data"]["preview_revision"] == 7
    assert CASES["preview_conflict"]["scope"] == "runtime"
    assert CASES["preview_conflict"]["request"]["expected_preview_revision"] == 7
    assert CASES["preview_conflict"]["response"]["body"]["error"]["current_revisions"]["preview_revision"] == 2
    assert CASES["settings_buffer_high"]["scope"] == "runtime"
    assert CASES["settings_buffer_high"]["request"]["changes"]["preferences"]["buffer_multiplier"] == 2.01
    with pytest.raises(ValidationError):
        SettingsChangeRequest.model_validate(CASES["settings_buffer_high"]["request"])


def test_start_idempotency_and_receipt_fixtures_are_linked():
    case = CASES["start_idempotent"]
    retry = CASES["start_idempotent_replay"]
    execution.SessionStartRequest.model_validate(case["request"])
    assert case["request_headers"]["Idempotency-Key"]
    assert case["request"] == retry["request"]
    assert case["request_headers"] == retry["request_headers"]
    assert case["response"]["body"]["data"]["run_id"] == retry["response"]["body"]["data"]["run_id"]


def test_voice_error_mappings_and_fallback_fixture():
    from app.api.v1 import voice as voice_api
    from app.services.voice_service import SttError, SttErrorCode, TtsError, TtsErrorCode

    tts_case = CASES["tts_empty_audio"]
    tts_error = voice_api._tts_error_to_app_error(TtsError(TtsErrorCode.EMPTY_AUDIO))
    assert tts_error.status_code == tts_case["response"]["status"]
    assert tts_error.code == tts_case["response"]["body"]["error"]["code"]
    prompt = CASES["uf5_tts_fallback"]["response"]["body"]["data"]
    assert prompt["fallback_to_device_tts"] is True
    assert len(prompt["prompt"]["skip_chips"]) == 3
    timeout_error = voice_api._stt_error_to_app_error(SttError(SttErrorCode.TIMEOUT))
    stt_case = CASES["stt_timeout"]["response"]
    assert timeout_error.status_code == stt_case["status"]
    assert timeout_error.code == stt_case["body"]["error"]["code"]
