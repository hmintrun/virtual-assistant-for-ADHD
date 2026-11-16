from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.core.config import settings
from app.core.errors import AppError
from app.services import google_identity_verifier as verifier


@pytest.fixture
def google_keys(monkeypatch, auth_config):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    monkeypatch.setattr(verifier, "_get_certificates", AsyncMock(return_value={"test-key": public}))
    return private


def token(key, **changes):
    now = int(datetime.now(UTC).timestamp())
    claims = dict(
        iss="https://accounts.google.com",
        aud=settings.GOOGLE_SIGN_IN_AUDIENCES[0],
        sub="google-sub",
        iat=now,
        exp=now + 3600,
        email="a@example.com",
        email_verified=True,
        name="User A",
    )
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


async def test_real_google_crypto_boundary(google_keys):
    identity = await verifier.verify_google_identity(token(google_keys))
    assert identity.sub == "google-sub"
    assert identity.email == "a@example.com"


@pytest.mark.parametrize(
    "changes",
    [dict(iss="other"), dict(aud="other"), dict(exp=0), dict(sub=""), dict(iat=9999999999)],
)
async def test_invalid_google_claims(google_keys, changes):
    with pytest.raises(AppError) as error:
        await verifier.verify_google_identity(token(google_keys, **changes))
    assert error.value.code == "INVALID_GOOGLE_ID_TOKEN"


@pytest.mark.parametrize(
    "changes",
    [dict(email_verified=False), dict(email=None), dict(email="bad"), dict(email="a@b c")],
)
async def test_profile_policy(google_keys, changes):
    with pytest.raises(AppError) as error:
        await verifier.verify_google_identity(token(google_keys, **changes))
    assert error.value.code == "GOOGLE_PROFILE_INCOMPLETE"


async def test_profile_fallback_and_limit(google_keys):
    assert (
        await verifier.verify_google_identity(token(google_keys, name=None))
    ).display_name == "a"
    assert (
        len(
            (await verifier.verify_google_identity(token(google_keys, name="A" * 500))).display_name
        )
        == 100
    )


async def test_wrong_signature(google_keys):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(AppError) as error:
        await verifier.verify_google_identity(token(other))
    assert error.value.status_code == 401


async def test_upstream_unavailable_and_cache(monkeypatch):
    monkeypatch.setattr(verifier, "_certs", {})
    monkeypatch.setattr(verifier, "_certs_expiry", 0)

    async def fail(*args, **kwargs):
        raise httpx.ConnectTimeout("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail)
    with pytest.raises(AppError) as error:
        await verifier._get_certificates()
    assert error.value.code == "AUTH_PROVIDER_UNAVAILABLE"
    assert error.value.retryable
    calls = []

    async def success(*args, **kwargs):
        calls.append(1)
        return httpx.Response(
            200,
            json={"key": "cert"},
            headers={"cache-control": "public, max-age=300"},
            request=httpx.Request("GET", verifier.CERTS_URL),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", success)
    assert await verifier._get_certificates() == {"key": "cert"}
    assert await verifier._get_certificates() == {"key": "cert"}
    assert len(calls) == 1
