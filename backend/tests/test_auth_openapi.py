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
