"""Public/protected API security must be discoverable by OpenAPI clients."""

from fastapi.routing import APIRoute

from app.api.deps import get_current_user
from app.main import app


def api_routes():
    # FastAPI 0.162 giữ included router dưới dạng effective route contexts.
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
        elif callable(contexts := getattr(route, "effective_route_contexts", None)):
            yield from contexts()


def has_current_user(dependency):
    return dependency.call is get_current_user or any(
        has_current_user(child) for child in dependency.dependencies
    )


def test_openapi_declares_app_bearer():
    scheme = app.openapi()["components"].get("securitySchemes", {}).get("AppBearer")
    assert scheme == {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


def test_all_current_user_operations_declare_bearer_without_manual_header():
    schema = app.openapi()
    protected = set()
    for route in api_routes():
        if not route.include_in_schema:
            continue
        if not has_current_user(route.dependant):
            continue
        for method in route.methods:
            operation = schema["paths"][route.path][method.lower()]
            assert {"AppBearer": []} in operation.get("security", []), route.path
            assert not any(
                p.get("in") == "header" and p["name"].lower() == "authorization"
                for p in operation.get("parameters", [])
            ), route.path
            protected.add((route.path, method))
    assert ("/api/v1/auth/me", "GET") in protected
    assert ("/api/v1/tasks", "GET") in protected


def test_login_and_calendar_callbacks_stay_public():
    schema = app.openapi()
    for path, method in (
        ("/api/v1/auth/google", "post"),
        ("/api/v1/auth/refresh", "post"),
        ("/api/v1/calendar/oauth/callback", "get"),
    ):
        assert not schema["paths"][path][method].get("security")
    legacy = next(
        route for route in api_routes()
        if route.path == "/api/v1/calendar/oauth2callback"
    )
    assert not legacy.include_in_schema
    assert not has_current_user(legacy.dependant)
    assert legacy.path not in schema["paths"]


def test_uf5_to_uf7_openapi_declares_error_envelopes_and_summaries():
    schema = app.openapi()
    expected = (
        ("/api/v1/runs/{run_id}/check-ins", "post", ("401", "404", "409", "422", "503")),
        (
            "/api/v1/runs/{run_id}/upcoming-schedule-context",
            "get",
            ("401", "404", "409", "422", "503"),
        ),
        ("/api/v1/proposals", "post", ("401", "404", "409", "422", "503")),
        ("/api/v1/proposals/current", "get", ("401", "404", "503")),
        ("/api/v1/proposals/{proposal_id}", "get", ("401", "404", "503")),
        ("/api/v1/proposals/{proposal_id}/rebuild", "post", ("401", "404", "409", "422", "503")),
        ("/api/v1/proposals/{proposal_id}/approve", "post", ("401", "404", "409", "422", "503")),
        ("/api/v1/proposals/{proposal_id}/reject", "post", ("401", "404", "409", "422", "503")),
        ("/api/v1/settings/change-requests/{change_request_id}/cancel", "post", ("401", "404", "409", "422")),
        ("/api/v1/suggestions/current", "get", ("401", "422")),
        ("/api/v1/suggestions/{suggestion_id}/response", "post", ("401", "404", "409", "422")),
    )
    for path, method, codes in expected:
        operation = schema["paths"][path][method]
        assert operation.get("summary"), f"Missing summary for {method.upper()} {path}"
        for code in codes:
            resp = operation["responses"][code]
            ref = resp["content"]["application/json"]["schema"]["$ref"]
            assert ref == "#/components/schemas/ErrorEnvelope", (path, method, code, ref)
