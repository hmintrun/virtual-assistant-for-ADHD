from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from jose import jwt

from app.core.config import Settings, settings
from app.core.errors import AppError
from app.core.security import (
    decode_app_access_token,
    issue_app_access_token,
    validate_auth_configuration,
)


def valid_claims():
    now = int(datetime.now(UTC).timestamp())
    return dict(
        sub=str(uuid4()),
        exp=now + 3600,
        iat=now,
        iss=settings.JWT_ISSUER,
        aud=settings.JWT_AUDIENCE,
        token_type="access",
    )


def test_round_trip(auth_config):
    uid = uuid4()
    bundle = issue_app_access_token(uid)
    assert bundle.expires_in == 3600
    assert decode_app_access_token(bundle.access_token).user_id == uid
    assert "access_token" not in repr(bundle)


@pytest.mark.parametrize("field", ["sub", "exp", "iat", "iss", "aud", "token_type"])
def test_required_claims(auth_config, field):
    claims = valid_claims()
    del claims[field]
    with pytest.raises(AppError) as error:
        decode_app_access_token(jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm="HS256"))
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("sub", "user_a"),
        ("sub", None),
        ("sub", 123),
        ("exp", 0),
        ("exp", True),
        ("exp", "9999999999"),
        ("iat", 9999999999),
        ("iat", False),
        ("iat", "123"),
        ("iss", "accounts.google.com"),
        ("aud", "other"),
        ("aud", ["proplan-va-api"]),
        ("token_type", "refresh"),
    ],
)
def test_invalid_claims(auth_config, field, value):
    claims = valid_claims()
    claims[field] = value
    with pytest.raises(AppError):
        decode_app_access_token(jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm="HS256"))


@pytest.mark.parametrize(
    "algorithm,key", [("HS384", "0123456789abcdef" * 4), ("HS256", "wrong-key")]
)
def test_signature_and_algorithm(auth_config, algorithm, key):
    with pytest.raises(AppError):
        decode_app_access_token(jwt.encode(valid_claims(), key, algorithm=algorithm))


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "eyJhbGciOiJub25lIn0.e30."])
def test_malformed(auth_config, token):
    with pytest.raises(AppError):
        decode_app_access_token(token)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_dev_mode_fails_closed(environment):
    with pytest.raises(ValueError):
        validate_auth_configuration(
            Settings(_env_file=None, AUTH_MODE="dev_user", ENVIRONMENT=environment)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"JWT_SECRET_KEY": ""},
        {"JWT_SECRET_KEY": "short"},
        {"JWT_SECRET_KEY": "change-me" * 8},
        {"GOOGLE_SIGN_IN_AUDIENCES": []},
        {"GOOGLE_SIGN_IN_AUDIENCES": ["example.apps.googleusercontent.com"]},
        {"JWT_ISSUER": " "},
        {"JWT_AUDIENCE": ""},
        {"SECRET_KEY": "0123456789abcdef" * 4},
    ],
)
def test_google_configuration(changes):
    config: dict[str, Any] = dict(
        AUTH_MODE="google",
        JWT_SECRET_KEY="0123456789abcdef" * 4,
        GOOGLE_SIGN_IN_AUDIENCES=["test-client.apps.googleusercontent.com"],
    )
    config.update(changes)
    with pytest.raises(ValueError):
        validate_auth_configuration(Settings(_env_file=None, **config))
