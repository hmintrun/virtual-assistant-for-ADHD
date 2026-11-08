"""HTTP contract regressions for KC routers; business services remain separate."""

import copy
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import service_dispatch
from app.core.database import get_db
from app.core.errors import AppError
from app.main import app

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures/uf3_uf4_contract.json").read_text(encoding="utf-8")
)
USER = "contract-user"
DB = object()
# Interfaces from W03, independent of the router implementation.
OPERATIONS = {
    "start": ("run_service", "start_session_run"),
    "events": ("run_service", "record_run_events"),
    "end": ("run_service", "end_session_run"),
    "device": ("device_service", "save_device_state_report"),
    "reminders": ("device_service", "list_reminders"),
    "reminder_receipt": ("device_service", "record_reminder_receipt"),
    "capture": ("thought_service", "capture_thought"),
    "thought_list": ("thought_service", "list_thoughts"),
    "thought": ("thought_service", "get_thought_detail"),
    "to_draft": ("thought_service", "convert_thought_to_draft"),
    "delete": ("thought_service", "delete_thought"),
    "checkin_list": ("checkin_service", "list_run_checkins"),
    "checkin_response": ("checkin_service", "respond_to_checkin"),
}
COMMANDS = [name for name in OPERATIONS if CONTRACT[name]["method"] != "GET"]


def assert_contract_fields(actual, expected):
    """Published fields must survive serialization; existing optional defaults may be added."""
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert key in actual
            assert_contract_fields(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            assert_contract_fields(actual_item, expected_item)
    else:
        assert actual == expected


def example(name):
    item = copy.deepcopy(CONTRACT[name])
    if isinstance(item["response"], list):
        item["response"] = item["response"][0]
    item["url"] = item["path"].format(**item["request"].get("path", {}))
    return item


@pytest.fixture
def client():
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(user_id=USER)
    app.dependency_overrides[get_db] = lambda: DB
    with TestClient(app) as http:
        yield http
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.parametrize("name", OPERATIONS)
def test_published_payload_dispatch_and_response_contract(client, monkeypatch, name):
    item = example(name)
    module_name, operation_name = OPERATIONS[name]
    calls = []

    async def operation(**kwargs):
        calls.append(kwargs)
        assert kwargs["db"] is DB
        assert kwargs["user_id"] == USER
        for key, value in item["request"].get("path", {}).items():
            assert kwargs[key] == value
        if item["method"] != "GET":
            assert kwargs["idempotency_key"] == "command-" + name
            body = kwargs["payload"].model_dump(mode="json")
            for key, value in item["request"]["body"].items():
                assert body[key] == value
        if name == "reminders":
            assert kwargs["device_id"] == item["request"]["query"]["device_id"]
            assert kwargs["from_time"] == datetime.fromisoformat(item["request"]["query"]["from"])
            assert kwargs["to_time"] == datetime.fromisoformat(item["request"]["query"]["to"])
            assert kwargs["schedule_revision"] == item["request"]["query"]["schedule_revision"]
        if name == "thought_list":
            assert kwargs["limit"] == 20 and kwargs["cursor"] is None
        if name == "checkin_list":
            assert kwargs["kind"] == "final"
        return item["response"]["body"]["data"]

    module = ModuleType("app.services." + module_name)
    setattr(module, operation_name, operation)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    response = client.request(
        item["method"],
        item["url"],
        json=item["request"].get("body"),
        params=item["request"].get("query"),
        headers={"Idempotency-Key": "command-" + name},
    )
    assert response.status_code == item["response"]["http_status"], response.text
    assert len(calls) == 1
    result = response.json()
    assert set(result) == {"data", "meta"}
    assert result["meta"]["request_id"]
    assert datetime.fromisoformat(result["meta"]["server_time"]).utcoffset() is not None
    assert_contract_fields(result["data"], item["response"]["body"]["data"])


@pytest.mark.parametrize("name", COMMANDS)
@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": ""}])
def test_commands_require_nonempty_idempotency_header(client, name, headers):
    item = example(name)
    response = client.request(
        item["method"], item["url"], json=item["request"]["body"], headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_openapi_commands_and_delete_revision():
    spec = app.openapi()
    for name in COMMANDS:
        item = example(name)
        operation = spec["paths"][item["path"]][item["method"].lower()]
        assert any(
            p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"]
        )
    delete = spec["paths"]["/api/v1/thoughts/{thought_id}"]["delete"]
    assert delete["requestBody"]["required"] is True
    assert "/api/v1/thoughts/{thought_id}/undo-delete" not in spec["paths"]


def test_delete_requires_expected_revision(client):
    response = client.request(
        "DELETE", "/api/v1/thoughts/thought_1", json={}, headers={"Idempotency-Key": "delete"}
    )
    assert response.status_code == 422


def test_device_does_not_assume_permission_or_dnd_success(client):
    item = example("device")
    for body in [
        {"device_report_revision": 1, "reported_at": "2026-11-07T09:00:00+07:00"},
        {**item["request"]["body"], "notification_permission": True},
    ]:
        response = client.put(item["url"], json=body, headers={"Idempotency-Key": "report"})
        assert response.status_code == 422


@pytest.mark.parametrize(
    "name,field",
    [
        ("start", "requested_start_at"),
        ("end", "ended_at"),
        ("device", "reported_at"),
        ("reminder_receipt", "occurred_at"),
        ("checkin_response", "responded_at"),
    ],
)
def test_naive_timestamps_are_rejected(client, name, field):
    item = example(name)
    body = item["request"]["body"]
    body[field] = "2026-11-07T09:00:00"
    response = client.request(
        item["method"], item["url"], json=body, headers={"Idempotency-Key": "timestamp"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        {"device_id": "device_1"},
        {
            "device_id": "device_1",
            "from": "2026-11-07T10:00:00+07:00",
            "to": "2026-11-07T09:00:00+07:00",
        },
        {"device_id": "device_1", "from": "2026-11-07T09:00:00", "to": "2026-11-07T10:00:00"},
    ],
)
def test_reminder_window_is_validated(client, query):
    response = client.get("/api/v1/reminders", params=query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_pagination_cursor_is_forwarded(client, monkeypatch):
    async def list_thoughts(*, db, user_id, limit, cursor):
        assert db is DB and user_id == USER and limit == 7 and cursor == "cursor-one"
        return {"items": [], "next_cursor": "cursor-two"}

    module = ModuleType("app.services.thought_service")
    module.__dict__["list_thoughts"] = list_thoughts
    monkeypatch.setitem(sys.modules, module.__name__, module)
    response = client.get("/api/v1/thoughts", params={"limit": 7, "cursor": "cursor-one"})
    assert response.json()["data"] == {"items": [], "next_cursor": "cursor-two"}


def test_empty_active_run_is_enveloped_null(client, monkeypatch):
    async def get_active_run(*, db, user_id):
        assert db is DB and user_id == USER
        return None

    module = ModuleType("app.services.run_service")
    module.__dict__["get_active_run"] = get_active_run
    monkeypatch.setitem(sys.modules, module.__name__, module)
    response = client.get("/api/v1/runs/active")
    assert response.status_code == 200 and response.json()["data"] is None


def test_business_error_is_preserved(client, monkeypatch):
    async def get_run_detail(*, db, user_id, run_id):
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy phiên.",
            next_action="reload",
        )

    module = ModuleType("app.services.run_service")
    module.__dict__["get_run_detail"] = get_run_detail
    monkeypatch.setitem(sys.modules, module.__name__, module)
    response = client.get("/api/v1/runs/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_unavailable_service_returns_explicit_503(client, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name, name=name)

    monkeypatch.setattr(service_dispatch, "import_module", missing)
    response = client.get("/api/v1/runs/active")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_NOT_READY"


def test_missing_operation_returns_explicit_503(monkeypatch):
    monkeypatch.setattr(service_dispatch, "import_module", lambda _: ModuleType("empty"))
    with pytest.raises(AppError) as exc:
        service_dispatch.service_handler("run_service", "get_active_run")
    assert exc.value.code == "SERVICE_NOT_READY"


def test_service_internal_import_error_is_not_hidden(monkeypatch):
    def broken(_):
        raise ModuleNotFoundError("broken dependency", name="broken_dependency")

    monkeypatch.setattr(service_dispatch, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="broken dependency"):
        service_dispatch.service_handler("run_service", "get_active_run")


def test_application_imports_without_service_stubs():
    result = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(app.title)"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
