"""Bộ kiểm thử đơn vị cho Module mã hóa bảo mật credential Google Calendar.

Quy chuẩn kiểm thử & Đối chiếu (QA Tracking):
- Commit SHA tham chiếu: 48f0c68eb9bae2d0442465de2a2a712670198d60 (short: 48f0c68)
- Mã ca kiểm thử bao phủ:
  * TC_CRYPTO_01: Mã hóa payload JSON thành envelope chuẩn fernet:v1:<ciphertext>.
  * TC_CRYPTO_02: Giải mã envelope thành công khi đúng khóa và đúng tenant binding.
  * TC_CRYPTO_03: Chặn tấn công tráo token giữa các user (Tenant Binding Guard).
  * TC_CRYPTO_04: Chặn tấn công tráo connection (Connection Binding Guard).
  * TC_CRYPTO_05: Xử lý ngoại lệ khi envelope sai format, token giả mạo hoặc sai khóa giải mã.
  * TC_CRYPTO_06: Cơ chế tự sinh khóa mã hóa fallback an toàn bằng SHA-256 từ SECRET_KEY.
"""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.credential_crypto import (
    ENVELOPE_PREFIX,
    _get_fernet_key,
    decrypt_credentials,
    encrypt_credentials,
)

TARGET_COMMIT_SHA = "48f0c68eb9bae2d0442465de2a2a712670198d60"


def test_TC_CRYPTO_01_encrypt_credentials_format() -> None:
    """TC_CRYPTO_01: Mã hóa payload JSON thành chuỗi envelope chuẩn fernet:v1:<ciphertext>."""
    payload = {
        "access_token": "ya29.sample_oauth_access_token_12345",
        "refresh_token": "1//sample_refresh_token_67890",
        "user_id": str(uuid4()),
        "connection_id": str(uuid4()),
        "account_ref": "thanh26022007@gmail.com",
    }

    envelope = encrypt_credentials(payload)

    # 1. Kiểm tra tiền tố envelope bắt buộc
    assert envelope.startswith(ENVELOPE_PREFIX)
    # 2. Kiểm tra ciphertext không được rỗng
    ciphertext = envelope[len(ENVELOPE_PREFIX) :]
    assert len(ciphertext) > 0
    # 3. Đảm bảo token plaintext hoàn toàn không xuất hiện trong chuỗi mã hóa
    assert "ya29.sample_oauth_access_token_12345" not in envelope
    assert "1//sample_refresh_token_67890" not in envelope


def test_TC_CRYPTO_02_decrypt_credentials_success() -> None:
    """TC_CRYPTO_02: Giải mã envelope thành công khi đúng khóa và đúng tenant binding."""
    user_id = str(uuid4())
    connection_id = str(uuid4())
    original_payload = {
        "access_token": "ya29.live_token_abc_xyz",
        "refresh_token": "1//refresh_token_valid",
        "user_id": user_id,
        "connection_id": connection_id,
        "account_ref": "qa_lead@proplan.app",
        "scopes": ["openid", "email", "https://www.googleapis.com/auth/calendar"],
    }

    envelope = encrypt_credentials(original_payload)

    # Giải mã với đúng user_id và connection_id
    decrypted = decrypt_credentials(
        envelope,
        expected_user_id=user_id,
        expected_connection_id=connection_id,
    )

    assert decrypted == original_payload
    assert decrypted["access_token"] == "ya29.live_token_abc_xyz"
    assert decrypted["user_id"] == user_id
    assert decrypted["connection_id"] == connection_id


def test_TC_CRYPTO_03_tenant_binding_user_id_mismatch() -> None:
    """TC_CRYPTO_03: Chặn tấn công tráo token giữa các user (Tenant Binding Guard)."""
    legit_user_id = str(uuid4())
    attacker_user_id = str(uuid4())
    connection_id = str(uuid4())

    payload = {
        "access_token": "ya29.token_belonging_to_legit_user",
        "user_id": legit_user_id,
        "connection_id": connection_id,
    }

    envelope = encrypt_credentials(payload)

    # Kẻ tấn công hoặc request từ tenant khác cố tình dùng ciphertext này
    with pytest.raises(ValueError, match="Vi phạm bảo mật tenant binding"):
        decrypt_credentials(
            envelope,
            expected_user_id=attacker_user_id,
            expected_connection_id=connection_id,
        )


def test_TC_CRYPTO_04_connection_binding_mismatch() -> None:
    """TC_CRYPTO_04: Chặn tấn công tráo connection (Connection Binding Guard)."""
    user_id = str(uuid4())
    conn_a = str(uuid4())
    conn_b = str(uuid4())

    payload = {
        "access_token": "ya29.token_of_conn_a",
        "user_id": user_id,
        "connection_id": conn_a,
    }

    envelope = encrypt_credentials(payload)

    # Thử giải mã với connection_id khác
    with pytest.raises(ValueError, match="Vi phạm bảo mật connection binding"):
        decrypt_credentials(
            envelope,
            expected_user_id=user_id,
            expected_connection_id=conn_b,
        )


def test_TC_CRYPTO_05_tampered_or_invalid_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC_CRYPTO_05: Xử lý ngoại lệ khi envelope sai format, token giả mạo hoặc sai khóa giải mã."""
    # 1. Sai tiền tố envelope
    with pytest.raises(ValueError, match="Định dạng envelope credential không hợp lệ"):
        decrypt_credentials("invalid_prefix_data_here")

    # 2. Ciphertext bị giả mạo / cắt vụn
    with pytest.raises(ValueError, match="Không thể giải mã credential"):
        decrypt_credentials(f"{ENVELOPE_PREFIX}CorruptedCiphertext12345==")

    # 3. Mã hóa bằng Key A, thử giải mã bằng Key B
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    monkeypatch.setattr(settings, "CALENDAR_CREDENTIAL_SECRET_KEY", key_a)
    envelope = encrypt_credentials({"secret_token": "top_secret_data"})

    monkeypatch.setattr(settings, "CALENDAR_CREDENTIAL_SECRET_KEY", key_b)
    with pytest.raises(ValueError, match="Không thể giải mã credential"):
        decrypt_credentials(envelope)


def test_TC_CRYPTO_06_fallback_key_derivation_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC_CRYPTO_06: Cơ chế tự sinh khóa mã hóa fallback an toàn bằng SHA-256 từ SECRET_KEY."""
    # Giả lập khi cấu hình CALENDAR_CREDENTIAL_SECRET_KEY rỗng
    monkeypatch.setattr(settings, "CALENDAR_CREDENTIAL_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "proplan-fallback-test-secret-key-2026")

    key1 = _get_fernet_key()
    assert isinstance(key1, bytes)
    # Khóa Fernet base64 phải có độ dài 44 bytes và giải mã ra đúng 32 bytes
    decoded = base64.urlsafe_b64decode(key1)
    assert len(decoded) == 32

    # Tính ổn định xác định (deterministic): cùng SECRET_KEY sinh cùng khóa Fernet
    key2 = _get_fernet_key()
    assert key1 == key2

    # Thao tác encrypt / decrypt vẫn hoạt động hoàn hảo với fallback key
    payload = {"data": "fallback_test"}
    env = encrypt_credentials(payload)
    assert decrypt_credentials(env) == payload
